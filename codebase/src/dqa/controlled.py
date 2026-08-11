"""The controlled comparison, as a runnable code path.

    python -m dqa.controlled --out results/controlled_comparison.json

What it measures
----------------
The study's research question is what *field projection* costs a data-quality
check. The published evaluation could not answer it, because the two arms did
not run the same check: the monolith applied one numeric envelope of
``[-1000, 100000]`` to every analyte, while the confined plausibility agent had
no reference ranges at all and asked a language model. Any gap between them
mixed a privilege effect with a checker effect, and there was no way to
separate the two after the fact.

This module removes the checker from the comparison. Both arms are given the
same rule file, ``manifests/rules/loinc_ranges.yaml``, and the same three
fields it consumes: the observation code, the value, and the unit. The only
remaining difference is how much of the record each arm may read:

* ``baseline_ranges`` -- :func:`dqa.baseline.baseline_plausibility_ranges`, which has access
  to the whole FHIR resource but **consumes only** code, value and unit, and
  deliberately does not read ``component[*].valueQuantity``;
* ``confined_<width>`` -- :func:`dqa.agents.check_plausibility_ranges`, reading only
  what ``manifests/<width>/plausibility.yaml`` projects.

Earlier editions of this docstring described Baseline as "reading the whole FHIR
resource". In the only sense that can affect a verdict it does not, and the
distinction is the whole point of the section below.

Why plausibility only
---------------------
Plausibility is the study's single pre-declared primary endpoint and the only
dimension whose score moves between the two systems: completeness scores 1.000
in every published cell, timeliness is constant within each cohort because it
is driven by cohort date shape, and consistency received no defects at all
under the published allocator. Averaging the real effect with three constants
divides it by four. So this module reports plausibility F1 and nothing else.

Reading the result: the zero is an identity
-------------------------------------------
Every width equals full access exactly, giving ``max_abs_gap_pp = 0.0`` in all
twelve seed-by-cohort cells. **That outcome is forced, not observed.** Audited
2026-08-06:

``dqa.ranges`` consumes exactly three JSONPaths, ``CODE_PATH``, ``VALUE_PATH``
and ``UNIT_PATH``. :func:`dqa.ranges.record_range_inputs` reads those three off
the raw resource and :func:`dqa.ranges.projected_range_inputs` reads the same
three off the projected slice, and **all three width manifests release all
three**. Checked over 600 clean resources, 2,400 injected resources and ten
adversarial synthetics: zero input mismatches and zero verdict mismatches, with
the 277 not-applicable cases coinciding exactly. Both arms are therefore the
same pure function of the same inputs, so the equality holds for any seed, any
cohort and any injection rate, and **the four-seed replication adds no
information**.

This module is still worth running: it is the pinned demonstration of that
property. But the number it produces is a theorem with a unit test, not a
measurement, and it must not be reported as an effect size.

For what projection actually costs when it bites, see :mod:`dqa.ablation`, which
withholds one consumed field at a time and measures 3.85, 28.04 and 100.00 pp,
alongside a negative control that withholds a released-but-unconsumed field and
correctly scores 0.0000.

On task difficulty, use ``--injection-mode hard``. Under the default ``legacy``
mode the injector draws from four constants that collide with no real value, so
a five-line set-membership test also scores 1.0000 and the endpoint cannot rank
detectors. ``hard`` fixes that: 0 of 83 caught by set membership, 5 of 83 by the
crude envelope, 83 of 83 by the per-analyte check. The arm-to-arm gap remains
0.0000 pp under both modes, because the identity above does not depend on how
hard the defects are.

Offline and deterministic: no model, no network, no clock.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from pathlib import Path
from typing import Any

from dqa.agents import check_plausibility_ranges
from dqa.baseline import baseline_plausibility_ranges
from dqa.inject import (
    hard_eligibility,
    hard_injectors,
    inject_planned,
    stratified_plan,
)
from dqa.manifests import (
    WIDTHS,
    ManifestSet,
    Width,
    load_width,
    manifests_hash,
    project,
)
from dqa.metrics import Row, f1
from dqa.ranges import RangeRule, default_rules_path
from dqa.run import (
    ADMITTED_RESOURCE_TYPES,
    DATASETS,
    MANIFESTS_DIR,
    ROOT,
    load_default_ranges,
    read_cohort,
)

# The published configuration of the controlled comparison. Four seeds so the
# result is replicated rather than observed once, and rate 0.30 because at the
# published 0.10 the stratified allocator realises only nine plausibility
# defects per cell, which is too few to measure a difference against.
SEEDS = (42, 43, 44, 45)
RATE = 0.30
LIMIT = 200

# Only the plausibility manifest is loaded per width: the other three agents
# play no part in this comparison and loading them would imply otherwise.
DIMENSION = "plausibility"

# Which plausibility injector the comparison runs against.
#
# ``legacy`` substitutes one of four constants, (-9999, 9999, 1e9, -1). No real
# value in any cohort collides with them, so a five-line set-membership test
# scores F1 = 1.0000 and the endpoint cannot tell a clinical range check from a
# sentinel detector. It is the published configuration and the default, because
# ``results/controlled_comparison.json`` was computed under it.
#
# ``hard`` derives each injected value from the analyte's own bound in
# ``manifests/rules/loinc_ranges.yaml``, placing it just outside by 2 to 15 per
# cent. Measured over the three cohorts: set membership catches 0 of 83, the
# crude [0, 9000] envelope catches 5 of 83, and the per-analyte check catches
# 83 of 83. That is an endpoint that discriminates.
#
# The gap between the arms is 0.0000 pp under BOTH modes, and necessarily so:
# both arms consume the same (code, value, unit) triple, so the comparison is an
# identity regardless of how hard the defects are. Running ``hard`` does not
# rescue the headline; it removes the objection that the headline is measured on
# a task no detector could fail.
INJECTION_MODES = ("legacy", "hard")
INJECTION_MODE_DEFAULT = "legacy"


def inject_cohort(
    cohort: list[dict[str, Any]],
    seed: int,
    rate: float,
    mode: str = INJECTION_MODE_DEFAULT,
) -> list[tuple[dict[str, Any], str | None]]:
    """Stratified injection over one cohort, seeded.

    ``mode`` selects the plausibility injector and defaults to ``legacy``, which
    reproduces the published artefact exactly. ``hard`` draws clinically
    adjacent but impossible values derived per analyte from the shipped rule
    file, which is what makes the endpoint able to discriminate a real range
    check from a sentinel test; see :mod:`dqa.inject`.

    Returns ``(resource, true_dimension)`` pairs, ``true_dimension`` being
    ``None`` for a clean control. The rng is drawn in exactly the order
    :func:`dqa.run.run_cell` draws it -- plan first, then one injector call per
    planned position -- so a cell built here carries the same ground truth as
    the same cell built by the harness.

    Injection happens once and is shared by all four systems. That is not an
    optimisation: it is what makes the comparison paired, so a difference
    between two arms cannot be a difference in what they were shown.
    """
    if mode not in INJECTION_MODES:
        raise ValueError(f"unknown injection mode {mode!r}; expected one of {INJECTION_MODES}")
    injectors = hard_injectors() if mode == "hard" else None
    eligibility = hard_eligibility() if mode == "hard" else None

    rng = random.Random(seed)
    plan = stratified_plan(cohort, rng, rate, eligibility=eligibility)
    prepared: list[tuple[dict[str, Any], str | None]] = []
    for resource, dimension in zip(cohort, plan, strict=True):
        if dimension is None:
            prepared.append((resource, None))
            continue
        modified, label = inject_planned(resource, dimension, rng, injectors=injectors)
        prepared.append((modified, label.dimension if label.is_defect else None))
    return prepared


def _row(resource_id: str, true_dimension: str | None, failed: bool) -> Row:
    return Row(resource_id, true_dimension, frozenset({DIMENSION}) if failed else frozenset())


def score_full_access(
    prepared: list[tuple[dict[str, Any], str | None]], rules: dict[str, RangeRule]
) -> float:
    """Plausibility F1 for the range-based monolith, reading whole records."""
    rows = [
        _row(
            str(resource.get("id", "")),
            true_dimension,
            baseline_plausibility_ranges(resource, rules).is_failure,
        )
        for resource, true_dimension in prepared
    ]
    return f1(rows, DIMENSION)


def score_projected(
    prepared: list[tuple[dict[str, Any], str | None]],
    manifest_set: ManifestSet,
    rules: dict[str, RangeRule],
) -> float:
    """Plausibility F1 for the same check restricted to one manifest's slice.

    A resource the manifest declares no policy for yields no slice at all, and
    is scored as "not flagged" -- the same thing the orchestrator does when
    :func:`dqa.manifests.project` returns ``None``.
    """
    manifest = manifest_set[DIMENSION]
    rows = []
    for resource, true_dimension in prepared:
        sliced = project(resource, manifest)
        failed = (
            check_plausibility_ranges(sliced, rules).is_failure
            if sliced is not None
            else False
        )
        rows.append(_row(str(resource.get("id", "")), true_dimension, failed))
    return f1(rows, DIMENSION)


def run_cell(
    dataset: str,
    dataset_dir: Path,
    seed: int,
    manifest_sets: dict[Width, ManifestSet],
    rules: dict[str, RangeRule],
    rate: float = RATE,
    limit: int = LIMIT,
    mode: str = INJECTION_MODE_DEFAULT,
) -> dict[str, Any]:
    """One seed-by-cohort cell: full access against all three widths."""
    cohort = read_cohort(dataset_dir, limit)
    prepared = inject_cohort(cohort, seed, rate, mode)

    full_access = score_full_access(prepared, rules)
    by_width = {
        width: score_projected(prepared, manifest_sets[width], rules) for width in WIDTHS
    }

    cell: dict[str, Any] = {
        "seed": seed,
        "dataset": dataset,
        "n_plausibility": sum(1 for _, dim in prepared if dim == DIMENSION),
        "baseline_ranges": round(full_access, 4),
    }
    for width in WIDTHS:
        cell[f"confined_{width}"] = round(by_width[width], 4)
    # Reported in percentage points, and computed from the ROUNDED figures so
    # the number in the table is the number a reader can verify from the other
    # columns of the same row.
    cell["max_abs_gap_pp"] = round(
        max(abs(cell[f"confined_{w}"] - cell["baseline_ranges"]) for w in WIDTHS) * 100, 4
    )
    return cell



def rules_digest() -> str:
    """SHA-256 over the shipped range file.

    The comparison is only meaningful against a known rule set, so the digest
    travels with the result rather than being implied by the file on disk.
    """
    path = default_rules_path(ROOT)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def controlled_comparison(
    seeds: tuple[int, ...] = SEEDS,
    rate: float = RATE,
    limit: int = LIMIT,
    mode: str = INJECTION_MODE_DEFAULT,
) -> dict[str, Any]:
    """Every seed against every cohort. Returns the full result document."""
    rules = load_default_ranges()
    manifest_sets = {width: load_width(MANIFESTS_DIR, width) for width in WIDTHS}

    cells: list[dict[str, Any]] = []
    for seed in seeds:
        for dataset, dataset_dir in DATASETS.items():
            if not dataset_dir.is_dir():
                print(f"skip {dataset}: {dataset_dir} not found", file=sys.stderr)
                continue
            cells.append(
                run_cell(
                    dataset, dataset_dir, seed, manifest_sets, rules, rate, limit, mode
                )
            )
    return {
        "description": (
            "Controlled comparison: identical range-based plausibility checker "
            "on both systems, differing only in field visibility."
        ),
        # The run contract for this artefact. Without it a manifest edit or a
        # change to the range file would move the paper's central number with
        # nothing in the output to show that the inputs had changed, which is
        # the failure mode run.py's own contract exists to prevent.
        "seeds": list(seeds),
        "rate": rate,
        "limit": limit,
        "resource_types": list(ADMITTED_RESOURCE_TYPES),
        "allocation": "stratified",
        "injection_mode": mode,
        "widths": list(WIDTHS),
        "manifests_hash": {
            width: manifests_hash(manifest_sets[width]) for width in WIDTHS
        },
        "ranges_hash": rules_digest(),
        "primary_endpoint": "f1_plausibility",
        "cells": cells,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Controlled comparison: same checker, different field visibility"
    )
    parser.add_argument(
        "--out", type=Path, default=ROOT / "results" / "controlled_comparison.json"
    )
    parser.add_argument("--rate", type=float, default=RATE)
    parser.add_argument("--limit", type=int, default=LIMIT)
    parser.add_argument("--seeds", type=int, nargs="+", default=list(SEEDS))
    parser.add_argument(
        "--injection-mode",
        choices=list(INJECTION_MODES),
        default=INJECTION_MODE_DEFAULT,
        help="legacy substitutes four constants and reproduces the published "
        "artefact; hard derives each value from the analyte's own bound, which "
        "a sentinel test cannot catch",
    )
    args = parser.parse_args(argv)

    document = controlled_comparison(
        tuple(args.seeds), args.rate, args.limit, args.injection_mode
    )

    worst = max((c["max_abs_gap_pp"] for c in document["cells"]), default=0.0)
    for cell in document["cells"]:
        widths = " ".join(f"{w}={cell[f'confined_{w}']:.4f}" for w in WIDTHS)
        print(
            f"seed {cell['seed']} {cell['dataset']:<14} "
            f"n={cell['n_plausibility']:>3} baseline={cell['baseline_ranges']:.4f} {widths} "
            f"gap={cell['max_abs_gap_pp']:.4f}pp",
            file=sys.stderr,
        )
    print(
        f"maximum absolute gap over {len(document['cells'])} cells "
        f"and {len(WIDTHS)} widths: {worst:.4f} pp",
        file=sys.stderr,
    )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(document, indent=2))
    print(f"wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
