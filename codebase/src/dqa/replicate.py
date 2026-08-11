"""The four-seed replication summary, as a runnable code path.

    python -m dqa.replicate --out results/replication_summary.json

What it does
------------
Reads the four stratified run files -- seeds 42 to 45 -- and reduces them to the
Confined-full minus Baseline gap per dimension per cohort, with a mean, a standard deviation
and a 95 per cent interval over the four seeds.

Why it exists
-------------
``results/replication_summary.json`` was the only source of every confidence
interval in the paper, and until this module it was produced by nothing in the
bundle: ``results/README.md`` recorded it as "historical. No shipped script
recomputes it". That is the same defect as the controlled comparison being
computed by an ad-hoc script -- a headline number a reader cannot reproduce from
the artefact they were given -- and ``dqa.controlled`` fixed it for one number
while leaving it standing for every interval.

It also puts ``dqa.stats`` to work. That module implements bootstrap confidence
intervals, Holm-Bonferroni FWER control and a non-inferiority test, and before
this it was imported by no module in ``src/``: the statistical machinery the
paper's analysis plan describes existed and ran on nothing.

Two intervals, reported side by side
------------------------------------
``ci95`` is Student's t over the four seed-level gaps (df = 3, t = 3.182). It is
what the published summary reported and it is preserved so the two are
comparable.

``ci95_boot`` is the percentile bootstrap from :func:`dqa.stats.bootstrap_ci`.
With n = 4 it is a weak instrument and it is not offered as a better one; it is
reported because it makes no normality assumption, and where the two disagree
that disagreement is itself worth seeing rather than hiding behind whichever
was chosen first.

Also reported, per dimension and cohort:

``holm_reject``
    Holm-Bonferroni decision at alpha = 0.05 across all cohort-by-dimension
    comparisons at once, from :func:`dqa.stats.holm_bonferroni`. The study makes
    many comparisons and corrected for none of them; this is the correction.
``non_inferiority``
    The pre-declared test from :func:`dqa.stats.non_inferiority_test`, Baseline minus
    Confined against the 0.03 margin the paper names. A null result of "costs nothing"
    needs an equivalence procedure, not the absence of a significant difference.

Offline and deterministic: reads JSON, calls no model, opens no socket, reads no
clock. Every random draw is seeded.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np

from dqa.manifests import DIMENSIONS
from dqa.naming import BASELINE, confined
from dqa.run import ROOT
from dqa.stats import bootstrap_ci, holm_bonferroni, non_inferiority_test

# The four runs behind every interval in the paper. Seed 42 lives in the main
# run file rather than in a rep_seed42.json, which is why there is no such file.
DEFAULT_SOURCES: tuple[tuple[int, str], ...] = (
    (42, "results/results_stratified_model.json"),
    (43, "results/rep_seed43.json"),
    (44, "results/rep_seed44.json"),
    (45, "results/rep_seed45.json"),
)

# The confined arm the gap is measured against. Full width is the like-for-like
# comparator: it is the only width whose field set is not itself an ablation.
CONFINED_SYSTEM = confined("full")
BASELINE_SYSTEM = BASELINE

# Reported per dimension and for the aggregate.
METRICS: tuple[str, ...] = (*(f"f1_{d}" for d in DIMENSIONS), "macro_f1")

# Student's t, two-sided, alpha 0.05, df = n - 1. Tabulated rather than pulled
# from scipy so the bundle keeps its five dependencies. Carried to six figures
# because at df = 3 the three-figure 3.182 shifts the published synthea
# plausibility bound from 4.01 to 4.02, and the point of this module is that the
# artefact and the code agree exactly.
_T_CRITICAL: dict[int, float] = {
    1: 12.70620,
    2: 4.302653,
    3: 3.182446,
    4: 2.776445,
    5: 2.570582,
}

# The margin the paper pre-commits to for the non-inferiority claim.
NI_MARGIN = 0.03


def load_runs(root: Path, sources: tuple[tuple[int, str], ...]) -> dict[int, dict[str, Any]]:
    """Read the run files, keyed by seed. Missing files are an error.

    Silently summarising three runs as four is exactly the failure this module
    exists to remove, so an absent file stops the run rather than shrinking n.
    """
    runs: dict[int, dict[str, Any]] = {}
    for seed, relative in sources:
        path = root / relative
        if not path.is_file():
            raise FileNotFoundError(
                f"missing run file for seed {seed}: {path}. Regenerate it with "
                f"`python -m dqa.run --allocation stratified --rate 0.30 "
                f"--seed {seed} --out {relative}`"
            )
        document = json.loads(path.read_text())
        recorded = document.get("seed")
        if recorded != seed:
            raise ValueError(
                f"{path} records seed {recorded!r} but is being read as the "
                f"seed {seed} replication"
            )
        runs[seed] = document
    return runs


def assert_comparable(runs: dict[int, dict[str, Any]]) -> dict[str, Any]:
    """Every run must share a contract apart from its seed.

    A summary over runs whose allocation, rate, limit, model or evaluation clock
    differ is not a replication, it is an average of different experiments.
    Returns the shared contract so it can travel with the result.
    """
    varying = {"seed", "cohorts"}
    shared: dict[str, Any] | None = None
    for seed, document in sorted(runs.items()):
        contract = {k: v for k, v in document.items() if k not in varying}
        if shared is None:
            shared = contract
            continue
        differing = sorted(
            k for k in set(shared) | set(contract) if shared.get(k) != contract.get(k)
        )
        if differing:
            raise ValueError(
                f"seed {seed} was run under a different contract; {differing} "
                f"differ from the seed {min(runs)} run. These runs are not "
                f"replications of one another"
            )
    return shared or {}


def gap_pp(document: dict[str, Any], dataset: str, metric: str) -> float:
    """Confined-full minus Baseline on one metric, in percentage points."""
    for cohort in document["cohorts"]:
        if cohort["dataset"] != dataset:
            continue
        systems = cohort["systems"]
        return 100.0 * (
            systems[CONFINED_SYSTEM]["scores"][metric] - systems[BASELINE_SYSTEM]["scores"][metric]
        )
    raise KeyError(f"cohort {dataset!r} not present in this run")


def datasets_of(runs: dict[int, dict[str, Any]]) -> list[str]:
    """Cohort names, in run order, required to be identical across seeds."""
    per_seed = {
        seed: [c["dataset"] for c in doc["cohorts"]] for seed, doc in sorted(runs.items())
    }
    first = next(iter(per_seed.values()))
    for seed, names in per_seed.items():
        if names != first:
            raise ValueError(f"seed {seed} covers cohorts {names}, not {first}")
    return first


def t_interval(values: list[float]) -> tuple[float, float, float, float]:
    """Return ``(mean, sd, lo, hi)`` for the Student's t interval at 95 per cent.

    ``sd`` is the sample standard deviation (ddof = 1). With every value equal
    the interval collapses to the point, which is the honest answer for a
    quantity that did not vary: it is a zero-width interval, not an absent one.
    """
    n = len(values)
    if n == 0:
        return 0.0, 0.0, 0.0, 0.0
    mean = float(np.mean(values))
    if n == 1:
        return mean, 0.0, mean, mean
    sd = float(np.std(values, ddof=1))
    critical = _T_CRITICAL.get(n - 1)
    if critical is None:
        raise ValueError(f"no tabulated t critical value for df = {n - 1}")
    half = critical * sd / math.sqrt(n)
    return mean, sd, mean - half, mean + half


def summarise(
    runs: dict[int, dict[str, Any]], margin: float = NI_MARGIN
) -> dict[str, Any]:
    """Reduce the runs to the published summary shape, plus the added tests."""
    seeds = sorted(runs)
    datasets = datasets_of(runs)

    # Collected first so Holm-Bonferroni can correct across every comparison at
    # once rather than within each dimension separately.
    pending: list[tuple[str, str, list[float]]] = []
    for metric in METRICS:
        for dataset in datasets:
            pending.append(
                (metric, dataset, [gap_pp(runs[s], dataset, metric) for s in seeds])
            )

    def _p_value(values: list[float]) -> float:
        """Two-sided seeded bootstrap p-value for "the mean gap is zero".

        A permutation of signs is unavailable at n = 4 in any useful sense, so
        the null is built by centring the observed gaps on zero and resampling.
        Constant-zero input yields p = 1.0 rather than a division by zero.

        A caveat that has to be read off the output rather than inferred: when
        the four gaps are identical and non-zero, every centred draw is exactly
        zero and this returns p = 0.0. That is not evidence of an enormous
        effect, it is n = 4 with no variance to resample. Read ``sd_pp`` beside
        every ``p_value``; where ``sd_pp`` is 0.00 the p-value carries no
        information.
        """
        arr = np.asarray(values, dtype=float)
        observed = float(np.mean(arr))
        if not np.any(arr != 0.0):
            return 1.0
        rng = np.random.default_rng(0)
        centred = arr - observed
        draws = np.array(
            [float(np.mean(rng.choice(centred, size=arr.size, replace=True))) for _ in range(10000)]
        )
        return float(np.mean(np.abs(draws) >= abs(observed)))

    p_values = [_p_value(values) for _, _, values in pending]
    rejects = holm_bonferroni(p_values, alpha=0.05)

    out: dict[str, Any] = {}
    for (metric, dataset, values), p, reject in zip(pending, p_values, rejects, strict=True):
        mean, sd, lo, hi = t_interval(values)
        _, boot_lo, boot_hi = bootstrap_ci(values, lambda a: float(np.mean(a)), seed=0)
        # The non-inferiority test is defined on F1 units, not percentage
        # points, and in the direction Baseline minus Confined. ``values`` holds Confined minus Baseline
        # in pp, so the paired difference the test wants is -v/100 against a
        # zero comparator: passing the gaps directly would test the sign the
        # study is not claiming.
        ni = non_inferiority_test(
            [-v / 100.0 for v in values],
            [0.0] * len(values),
            margin=margin,
            dimension=f"{metric}:{dataset}",
        )
        out.setdefault(metric, {})[dataset] = {
            "mean_pp": round(mean, 2),
            "sd_pp": round(sd, 2),
            "ci95_lo": round(lo, 2),
            "ci95_hi": round(hi, 2),
            "ci95_boot_lo": round(boot_lo, 2),
            "ci95_boot_hi": round(boot_hi, 2),
            "p_value": round(p, 4),
            "holm_reject": reject,
            "non_inferiority": {
                "difference": round(ni.difference, 4),
                "upper_bound": round(ni.upper_bound, 4),
                "margin": ni.margin,
                "passed": ni.passed,
            },
            "runs_pp": [round(v, 2) for v in values],
        }
    return out


def replication_summary(
    root: Path = ROOT, sources: tuple[tuple[int, str], ...] = DEFAULT_SOURCES
) -> dict[str, Any]:
    """The whole document, contract included."""
    runs = load_runs(root, sources)
    contract = assert_comparable(runs)
    seeds = sorted(runs)
    return {
        "description": (
            "Confined-full minus Baseline per dimension and cohort, over four seeds. "
            "ci95 is Student's t (df = n-1); ci95_boot is the percentile "
            "bootstrap. holm_reject corrects across all cohort-by-dimension "
            "comparisons at alpha = 0.05. non_inferiority tests Baseline minus Confined "
            f"against the pre-declared {NI_MARGIN} margin."
        ),
        # Carried in the artefact itself, not only in the surrounding prose,
        # because this file reports a claim the project has WITHDRAWN and a
        # reader who opens the JSON directly would otherwise see a Holm-corrected
        # significant result with nothing to say it was retracted.
        "caveats": {
            "withdrawn_claim": (
                "The f1_plausibility rows below show the confined system BEATING "
                "the full-access baseline. That claim is WITHDRAWN. It is an "
                "artefact of comparing a range-based agent against a baseline "
                "using one crude numeric envelope, so it measures a checker "
                "difference, not a privilege difference. Read only alongside "
                "results/controlled_comparison.json, which holds the checker "
                "constant."
            ),
            "bootstrap_n": (
                f"Every interval here resamples {len(seeds)} points. On rows with "
                "zero variance the percentile interval has width exactly 0 and the "
                "non-inferiority p-value is a step function taking only 0 or 1. "
                "These are not evidential quantities on such rows."
            ),
            "what_the_seeds_resample": (
                "Seeds vary the defect injector only, not patients, sites or time. "
                "No interval here is a population interval."
            ),
            "margin_is_post_hoc": (
                f"The {NI_MARGIN} margin was fixed before the inter-run noise floor "
                "was measured from these same runs, so any comparison of the two is "
                "post hoc."
            ),
        },
        "seeds": seeds,
        "n_runs": len(seeds),
        "confined_system": CONFINED_SYSTEM,
        "baseline_system": BASELINE_SYSTEM,
        "sources": {str(seed): relative for seed, relative in sources},
        # The shared run contract, carried so the summary states the conditions
        # it summarises instead of implying them.
        "contract": contract,
        **summarise(runs),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Four-seed replication summary of the Confined-full minus Baseline gap"
    )
    parser.add_argument(
        "--out", type=Path, default=ROOT / "results" / "replication_summary.json"
    )
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)

    document = replication_summary(args.root)

    for metric in METRICS:
        for dataset, cell in document[metric].items():
            flag = "*" if cell["holm_reject"] else " "
            print(
                f"{metric:<18} {dataset:<14} mean {cell['mean_pp']:>+7.2f} pp  "
                f"t95 [{cell['ci95_lo']:>+7.2f}, {cell['ci95_hi']:>+7.2f}]  "
                f"boot [{cell['ci95_boot_lo']:>+7.2f}, {cell['ci95_boot_hi']:>+7.2f}]  "
                f"p={cell['p_value']:.4f}{flag}",
                file=sys.stderr,
            )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(document, indent=2))
    print(f"wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
