"""Evaluation harness and CLI.

    python -m dqa.run                                     # published legacy path
    python -m dqa.run --allocation stratified --rate 0.30 # current headline path

The run contract
----------------
A cell is comparable with another cell only when every value that
determines it matches. Thirteen top-level keys are written to the output
JSON, twelve of them contract values and the last the results payload:

    model_snapshot   the pinned model, or the snapshot override
    limit            resources read per cohort
    injection_rate   share of the cohort selected for a defect
    seed             seeds the injector, and nothing else
    allocation       legacy | stratified  (see ``run_cell``)
    baseline         envelope | ranges    (see ``--baseline``)
    exposure_basis   published | consistent  (see ``EXPOSURE_BASES``)
    now_anchor       the fixed evaluation clock for the timeliness rules
    resource_types   the admitted FHIR types, ``ADMITTED_RESOURCE_TYPES``
    excluded_file_prefixes  cohort files skipped, ``EXCLUDED_FILE_PREFIXES``
    cache_enabled    whether verdicts may come from the disk cache
    primary_endpoint the single pre-declared endpoint, f1_plausibility
    cohorts          the per-dataset results

Each cohort additionally records the ``manifests_hash`` of every width
evaluated, and the ``realised_defects`` actually injected per dimension,
because a detection score is uninterpretable without the n behind it.
Each Confined width records ``call_stats``: cache hits, cache misses, model
errors and offline skips, so a run degraded by a failing network is
distinguishable from a clean one in the artefact itself.

Two things are deliberately not in the contract because they are not
free parameters: the injector's far-future anchor (``inject.FUTURE_ANCHOR``)
and the decoding temperature, which is pinned at 0.0.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any

from dqa.agents import NOW_ANCHOR, Orchestrator
from dqa.audit import AuditChain
from dqa.baseline import MonolithicBaseline, MonolithicBaselineRanges
from dqa.exposure import ExposureStats, exposure_by_type, exposure_pooled
from dqa.inject import inject_random
from dqa.manifests import WIDTHS, load_width, manifests_hash
from dqa.metrics import LeakRecord, Row, leak_mean, leak_record, score
from dqa.naming import BASELINE, confined
from dqa.ranges import RangeRule, default_rules_path, load_ranges

ROOT = Path(__file__).resolve().parents[2]
MANIFESTS_DIR = ROOT / "manifests"
CACHE_DIR = ROOT / ".llm_cache"

DATASETS: dict[str, Path] = {
    "synthea": ROOT / "data" / "synthea-fhir",
    "mimic-iv-demo": ROOT / "data" / "mimic-iv-demo-fhir",
    "eicu-demo": ROOT / "data" / "eicu-demo-fhir",
}

# Pinned. The pooled exposure metric is a function of cohort composition, so
# admitting a further resource type moves every published number without any
# manifest changing. Widening this is a deliberate act that invalidates
# comparability with the existing results.
ADMITTED_RESOURCE_TYPES = ("Patient", "Observation", "Encounter")

# The Patient quota below reserves this share of the limit for Patient
# resources; the remainder is filled with the other admitted types in file
# order. Named so ``read_cohort`` can derive its split from the constant it
# records rather than from a second hard-coded copy of it.
PATIENT_TYPE = ADMITTED_RESOURCE_TYPES[0]
EVENT_TYPES = ADMITTED_RESOURCE_TYPES[1:]

# Cohort files skipped before anything is parsed. These carry organisational
# reference data (hospitals, practitioners) rather than patient records, so
# admitting them would change the exposure denominator without adding anything
# scoreable. Named and recorded in the run contract rather than hard-coded
# inline, because which files are read determines the cohort and therefore
# every number computed from it.
EXCLUDED_FILE_PREFIXES = ("hospital", "practitioner")

# Resources read per cohort. Named rather than repeated as a literal so the CLI
# default, the cohort manifest and any analysis script cannot drift apart: the
# cohort determines every published number, and the limit determines the cohort.
LIMIT_DEFAULT = 200

DEFAULT_SNAPSHOT = "claude-haiku-4-5-20251001"

# Selects the plausibility rule both arms use. ``envelope`` is the published
# default: Baseline applies its wide numeric envelope and the Confined plausibility agent
# calls the model. ``ranges`` gives BOTH arms the per-analyte rule file, which
# is the controlled comparison, so the two systems differ only in what they may
# see. Anything that changes for one arm must change for the other, or the flag
# reintroduces the confound it exists to remove.
BASELINE_MODES = ("envelope", "ranges")

# Which object the AgentLeak ratio is computed on.
#
# ``published`` is the shipped behaviour and the default, because changing it
# MOVES A PUBLISHED NUMBER. It takes the numerator ``phi_exposed`` from the
# slices released for the INJECTED copy of a resource and the denominator
# ``phi_total`` from the CLEAN copy, so the ratio is not a proportion of any
# single object: injection can delete a Safe Harbor field, which removes it
# from the numerator while the denominator still counts it.
#
# ``consistent`` takes both from the injected copy -- the object the agents
# were actually shown -- so numerator and denominator describe the same
# resource.
#
# What the difference is worth, measured at legacy allocation, rate 0.10,
# seed 42, minimal width (pinned in tests/test_exposure.py):
#
#     cohort          published   consistent   clean decomposition
#     synthea            0.6100      0.6100                 0.6125
#     mimic-iv-demo      0.6675      0.6700                 0.6700
#     eicu-demo          0.6675      0.6700                 0.6700
#
# Note the synthea column: this defect contributes EXACTLY 0.0000 there. An
# earlier edition of the README claimed it explained the synthea 0.6125 vs
# 0.6100 gap. It does not, and that claim has been withdrawn; the cause there
# is the injector mutating the resource, which moves the numerator under both
# bases alike. The defect bites only on MIMIC-IV and eICU at minimal width,
# and is worth -0.0025 when it does.
#
# Both means are recorded in every Confined cell regardless of which basis is
# selected, because computing the second one costs one extra phi_total call
# on an object already in hand and no model call at all.
EXPOSURE_BASES = ("published", "consistent")
EXPOSURE_BASIS_DEFAULT = "published"


def read_cohort(dataset_dir: Path, limit: int) -> list[dict[str, Any]]:
    """Load resources, reserving about a third of the limit for Patient
    resources so the exposure metric sees person-oriented Safe Harbor
    fields. Without the bias, Observations swamp the sample.

    The admitted types come from ``ADMITTED_RESOURCE_TYPES``, the same
    constant written into the run contract, so the recorded value and the
    filter cannot drift apart.
    """
    patients: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    for path in sorted(dataset_dir.glob("*.json")):
        if path.name.startswith(EXCLUDED_FILE_PREFIXES):
            continue
        try:
            payload = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        entries = payload.get("entry", [payload]) if isinstance(payload, dict) else []
        for entry in entries:
            resource = entry.get("resource", entry) if isinstance(entry, dict) else None
            if not isinstance(resource, dict):
                continue
            resource_type = resource.get("resourceType")
            if resource_type == PATIENT_TYPE:
                patients.append(resource)
            elif resource_type in EVENT_TYPES:
                events.append(resource)
    patient_quota = min(len(patients), max(1, limit // 3))
    return patients[:patient_quota] + events[: limit - patient_quota]


def run_cell_leaks(
    system: Any,
    cohort: list[dict[str, Any]],
    injection_rate: float,
    seed: int,
    workers: int = 8,
    allocation: str = "legacy",
) -> tuple[list[Row], dict[str, list[LeakRecord]], dict[str, int]]:
    """Score one system over one cohort, returning exposure on BOTH bases.

    Identical to :func:`run_cell` except that the exposure records are
    returned as ``{basis: records}`` over every entry in
    ``EXPOSURE_BASES`` rather than on one basis only, so a caller can
    report the published number and the consistent one side by side
    without evaluating the cohort twice. The second basis costs one extra
    :func:`dqa.metrics.phi_total` call per resource and no model call.

    Injection happens serially first, so the ground truth depends only
    on the seed. Evaluation then fans out over a thread pool; the LLM
    agents are network-bound, so threads give near-linear speedup.

    ``allocation`` selects the injector. ``legacy`` reproduces the
    published results exactly: it picks a dimension before looking at the
    resource, so a fifth of the budget lands on resources that cannot
    carry that defect and consistency receives nothing at all.
    ``stratified`` allocates against eligibility, so every planned defect
    is realised. The realised count per dimension is returned either way,
    because a detection score is uninterpretable without the n behind it.
    """
    from concurrent.futures import ThreadPoolExecutor

    rng = random.Random(seed)
    prepared: list[tuple[dict[str, Any], dict[str, Any], Any]] = []

    if allocation == "stratified":
        from dqa.inject import inject_planned, stratified_plan

        plan = stratified_plan(cohort, rng, injection_rate)
        for resource, dimension in zip(cohort, plan, strict=True):
            if dimension is None:
                prepared.append((resource, resource, None))
                continue
            modified, label = inject_planned(resource, dimension, rng)
            prepared.append((resource, modified, label.dimension if label.is_defect else None))
    else:
        for resource in cohort:
            if rng.random() >= injection_rate:
                prepared.append((resource, resource, None))
            else:
                modified, label = inject_random(resource, rng)
                prepared.append(
                    (resource, modified, label.dimension if label.is_defect else None)
                )

    realised: dict[str, int] = {}
    for _, _, true_dim in prepared:
        if true_dim is not None:
            realised[true_dim] = realised.get(true_dim, 0) + 1

    def score_one(
        item: tuple[dict[str, Any], dict[str, Any], Any],
    ) -> tuple[Row, LeakRecord, LeakRecord]:
        original, modified, true_dim = item
        verdicts, projected = system.evaluate(modified)
        predicted = frozenset(v.dimension for v in verdicts if v.is_failure)
        return (
            Row(str(original.get("id", "")), true_dim, predicted),
            # published: denominator from the clean resource, numerator from
            # the slices released for the injected one. Kept bit-for-bit.
            leak_record(original, projected),
            # consistent: both from the object the agents were shown.
            leak_record(modified, projected),
        )

    with ThreadPoolExecutor(max_workers=workers) as pool:
        scored = list(pool.map(score_one, prepared))

    leaks: dict[str, list[LeakRecord]] = {
        "published": [rec for _, rec, _ in scored],
        "consistent": [rec for _, _, rec in scored],
    }
    return [row for row, _, _ in scored], leaks, realised


def run_cell(
    system: Any,
    cohort: list[dict[str, Any]],
    injection_rate: float,
    seed: int,
    workers: int = 8,
    allocation: str = "legacy",
    exposure_basis: str = EXPOSURE_BASIS_DEFAULT,
) -> tuple[list[Row], list[LeakRecord], dict[str, int]]:
    """Score one system over one cohort with seeded injection.

    ``exposure_basis`` selects which object the AgentLeak ratio is
    computed on; see ``EXPOSURE_BASES``. It defaults to ``published``,
    which reproduces the shipped numbers exactly, because the alternative
    moves a published number and so must be asked for.

    See :func:`run_cell_leaks` for everything else; this is that function
    with one basis chosen.
    """
    if exposure_basis not in EXPOSURE_BASES:
        raise ValueError(
            f"unknown exposure basis {exposure_basis!r}; expected one of {EXPOSURE_BASES}"
        )
    rows, leaks, realised = run_cell_leaks(
        system, cohort, injection_rate, seed, workers=workers, allocation=allocation
    )
    return rows, leaks[exposure_basis], realised


def load_default_ranges() -> dict[str, RangeRule]:
    """The shipped per-analyte rule file, ``manifests/rules/loinc_ranges.yaml``."""
    return load_ranges(default_rules_path(ROOT))


def _exposure_row(s: ExposureStats) -> dict[str, Any]:
    """One ``ExposureStats`` as the JSON row written into a result cell.

    The first five keys, and their order, are the shipped schema and must
    not move. The last three are additive:

    ``union_unclamped_mean``
        ``union_mean`` with the clamp removed. ``union_mean`` was measured
        equal to ``clamped_mean`` in all 27 shipped cells, and this column
        records that the clamp is not the reason: it is equal to
        ``union_mean`` in all 27 as well, because the union numerator
        never exceeds the denominator on the shipped manifests.
    ``multiplicity_inflation``
        ``unclamped_mean - union_unclamped_mean``: the share of the raw
        exposure ratio that is one released path counted again in another
        agent's envelope. This is the quantity the union numerator was
        introduced to expose, and unlike the union columns it is far from
        zero.
    ``at_ceiling_strict``
        ``at_ceiling`` counts ``exposed >= total``, which includes ties,
        and a tie is a record where the clamp discarded nothing. The
        strict count takes ``exposed > total`` only, so it is the one that
        can be cited as evidence of clamp loss.
    """
    return {
        "n": s.n,
        "clamped_mean": round(s.clamped_mean, 4),
        "unclamped_mean": round(s.unclamped_mean, 4),
        "union_mean": round(s.union_mean, 4),
        "at_ceiling": s.at_ceiling,
        "union_unclamped_mean": round(s.union_unclamped_mean, 4),
        "multiplicity_inflation": round(s.multiplicity_inflation, 4),
        "at_ceiling_strict": s.at_ceiling_strict,
    }


def evaluate_dataset(
    name: str,
    dataset_dir: Path,
    limit: int,
    injection_rate: float,
    seed: int,
    allocation: str = "legacy",
    baseline: str = "envelope",
    exposure_basis: str = EXPOSURE_BASIS_DEFAULT,
) -> dict[str, Any]:
    """Baseline plus Confined at every width, on one cohort.

    ``baseline`` selects the plausibility rule, and selects it for *both*
    arms at once. ``envelope`` is the published configuration: Baseline uses its
    wide numeric envelope and the Confined plausibility agent calls the model.
    ``ranges`` hands the same per-analyte rule file to Baseline and to every Confined
    width, which is the controlled comparison.

    ``exposure_basis`` selects which of the two AgentLeak means becomes
    ``agentleak_mean``; see ``EXPOSURE_BASES``. Both are written to every
    Confined cell either way, so the choice affects which number is headline,
    never which numbers are available.
    """
    if baseline not in BASELINE_MODES:
        raise ValueError(f"unknown baseline mode {baseline!r}; expected one of {BASELINE_MODES}")
    if exposure_basis not in EXPOSURE_BASES:
        raise ValueError(
            f"unknown exposure basis {exposure_basis!r}; expected one of {EXPOSURE_BASES}"
        )

    cohort = read_cohort(dataset_dir, limit)
    out: dict[str, Any] = {
        "dataset": name,
        "n_resources": len(cohort),
        "injection_rate": injection_rate,
        "seed": seed,
        "allocation": allocation,
        "baseline": baseline,
        "exposure_basis": exposure_basis,
        "systems": {},
    }

    rules = load_default_ranges() if baseline == "ranges" else None

    # Baseline: monolithic full-access baseline. Structural exposure is 1.00
    # by construction because there is no projection boundary.
    a1 = MonolithicBaseline() if rules is None else MonolithicBaselineRanges(rules)
    rows, _, realised = run_cell(a1, cohort, injection_rate, seed, allocation=allocation)
    out["realised_defects"] = realised
    out["systems"][BASELINE] = {
        "scores": score(rows),
        "agentleak_structural": 1.0,
        "note": "monolithic full-access baseline; no projection boundary",
    }

    # Confined at each width.
    for width in WIDTHS:
        manifest_set = load_width(MANIFESTS_DIR, width)
        orchestrator = Orchestrator(manifest_set, cache_dir=CACHE_DIR, range_rules=rules)
        rows, leaks_by_basis, _ = run_cell_leaks(
            orchestrator, cohort, injection_rate, seed, allocation=allocation
        )
        # Both bases, always. The selected one is what ``agentleak_mean``
        # reports; the other is reported beside it so the size of the
        # numerator/denominator defect is visible in the artefact itself
        # rather than only in prose about the artefact.
        means = {b: leak_mean(records) for b, records in leaks_by_basis.items()}
        mean, excluded = means[exposure_basis]
        cell: dict[str, Any] = {
            "scores": score(rows),
            "agentleak_mean": round(mean, 4),
            "agentleak_excluded_t0": excluded,
            "manifests_hash": manifests_hash(manifest_set),
            # How the two model-backed dimensions in this cell were obtained.
            # A non-zero model_errors means the scores above are degraded by
            # failed calls that collapsed to "uncertain", which is otherwise
            # indistinguishable from the model declining to flag anything.
            "call_stats": orchestrator.call_stats.as_dict(),
        }
        # Which object the headline ``agentleak_mean`` above was computed on,
        # and what the other basis would have given. Recorded per cell as well
        # as in the run contract so a cell lifted out of the file on its own
        # still says which number it is.
        cell["agentleak_basis"] = exposure_basis
        for basis, (basis_mean, basis_excluded) in means.items():
            cell[f"agentleak_mean_{basis}"] = round(basis_mean, 4)
            cell[f"agentleak_excluded_t0_{basis}"] = basis_excluded
        # Exposure decomposed by resourceType. The pooled mean above is a
        # function of cohort composition rather than of the manifests, so it
        # must never be reported on its own.
        #
        # This decomposition runs on the CLEAN cohort and is a third quantity,
        # distinct from both bases above: no injection at all. It is the
        # column against which the other two should be read.
        by_type = exposure_by_type(cohort, manifest_set)
        cell["exposure_by_type"] = {
            rtype: _exposure_row(s) for rtype, s in by_type.items()
        }
        # The pooled clean decomposition, previously derivable from the table
        # above only by reweighting it by hand.
        cell["exposure_pooled_clean"] = _exposure_row(exposure_pooled(cohort, manifest_set))
        out["systems"][confined(width)] = cell
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DQA evaluation harness")
    parser.add_argument("--dataset", choices=[*DATASETS, "all"], default="all")
    parser.add_argument("--limit", type=int, default=LIMIT_DEFAULT)
    parser.add_argument("--rate", type=float, default=0.10)
    parser.add_argument("--seed", type=int, default=42)
    # Deliberately not "results.json". That name belonged to the superseded
    # 2026-08-02 run, now archived as results_published_v1.json, and a bare
    # invocation must not silently recreate a file readers have learned to
    # treat as the headline artefact. Every Makefile target passes --out.
    parser.add_argument(
        "--out", type=Path, default=ROOT / "results" / "results_latest.json"
    )
    parser.add_argument(
        "--allocation",
        choices=["legacy", "stratified"],
        default="legacy",
        help="legacy reproduces the published results exactly; stratified "
        "allocates the defect budget against eligibility",
    )
    parser.add_argument(
        "--baseline",
        choices=list(BASELINE_MODES),
        default="envelope",
        help="envelope reproduces the published results: Baseline uses its wide "
        "numeric envelope and the Confined plausibility agent calls the model. "
        "ranges gives BOTH arms manifests/rules/loinc_ranges.yaml, which is "
        "the controlled comparison",
    )
    parser.add_argument(
        "--exposure-basis",
        choices=list(EXPOSURE_BASES),
        default=EXPOSURE_BASIS_DEFAULT,
        help="published reproduces the shipped AgentLeak numbers: the "
        "numerator comes from the slices released for the injected resource "
        "and the denominator from the clean one, so the ratio is not a "
        "proportion of a single object. consistent takes both from the "
        "injected resource. Both means are written to every Confined cell either "
        "way; this selects which one is reported as agentleak_mean",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="ignore the verdict cache, forcing the deterministic offline path",
    )
    parser.add_argument(
        "--audit-log",
        type=Path,
        default=None,
        help="write a hash-chained JSONL audit record of the run to this path "
        "and verify the chain before exiting. Off by default, so no shipped "
        "artefact changes",
    )
    args = parser.parse_args(argv)

    import os

    global CACHE_DIR
    if args.no_cache:
        CACHE_DIR = None

    names = list(DATASETS) if args.dataset == "all" else [args.dataset]
    # The run contract. Every value here is required to determine a run, and
    # a cell whose contract differs is not comparable with one whose does not.
    results = {
        "model_snapshot": os.environ.get("ANTHROPIC_MODEL_SNAPSHOT", DEFAULT_SNAPSHOT),
        "limit": args.limit,
        "injection_rate": args.rate,
        "seed": args.seed,
        "allocation": args.allocation,
        "baseline": args.baseline,
        # Which object the exposure ratio was computed on. In the contract
        # because it determines agentleak_mean, and because a cell computed
        # on one basis is not comparable with a cell computed on the other.
        "exposure_basis": args.exposure_basis,
        # Recorded because the timeliness rules are evaluated against this
        # fixed clock rather than against the wall clock. A run whose anchor
        # differs is not comparable on the timeliness dimension.
        "now_anchor": NOW_ANCHOR.isoformat(),
        "resource_types": ADMITTED_RESOURCE_TYPES,
        # Which cohort files were skipped before parsing. The cohort determines
        # every number below it, so the exclusion rule belongs in the contract
        # rather than only in read_cohort's source.
        "excluded_file_prefixes": EXCLUDED_FILE_PREFIXES,
        "cache_enabled": not args.no_cache,
        "primary_endpoint": "f1_plausibility",
        "cohorts": [],
    }
    # Opt-in hash-chained audit log. Off by default, so no shipped artefact and
    # no published number is affected; `--audit-log` is what turns the records
    # dqa.audit already knew how to produce into something written to disk.
    chain = AuditChain(persist_path=args.audit_log) if args.audit_log else None
    if chain is not None:
        chain.append("run_start", {k: v for k, v in results.items() if k != "cohorts"})

    for name in names:
        dataset_dir = DATASETS[name]
        if not dataset_dir.is_dir():
            print(f"skip {name}: {dataset_dir} not found", file=sys.stderr)
            if chain is not None:
                chain.append("cohort_skipped", {"dataset": name, "path": str(dataset_dir)})
            continue
        print(f"evaluating {name} ...", file=sys.stderr)
        cohort_result = evaluate_dataset(
            name,
            dataset_dir,
            args.limit,
            args.rate,
            args.seed,
            args.allocation,
            args.baseline,
            args.exposure_basis,
        )
        results["cohorts"].append(cohort_result)
        if chain is not None:
            # One record per system, so the log is a per-decision trail rather
            # than one opaque blob per cohort.
            for system, cell in cohort_result["systems"].items():
                chain.append(
                    "system_scored",
                    {
                        "dataset": name,
                        "system": system,
                        "scores": cell.get("scores", {}),
                        "realised_defects": cohort_result.get("realised_defects", {}),
                    },
                )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(results, indent=2))
    print(f"wrote {args.out}", file=sys.stderr)

    if chain is not None:
        chain.append("run_complete", {"out": str(args.out), "cohorts": len(results["cohorts"])})
        # Verify before claiming the log is usable. A chain that does not replay
        # is worse than no chain, because it looks like evidence.
        if not chain.verify():
            print(
                f"AUDIT CHAIN FAILED VERIFICATION at {args.audit_log}",
                file=sys.stderr,
            )
            return 1
        print(
            f"wrote {args.audit_log} ({len(chain)} records, chain verified)",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
