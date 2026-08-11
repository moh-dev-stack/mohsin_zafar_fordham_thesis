"""The department-tiered system, run end to end.

    python -m dqa.tiered --out results/tiered_departments.json

What this is
------------
:mod:`dqa.departments` describes a tiered design and :mod:`dqa.linkage` describes
a surrogate ladder, but neither was ever wired into a pipeline, so neither had a
measured result. This module runs them.

The flat system in :mod:`dqa.agents` gives every agent the same standing. Each
one receives the raw ``$.subject.reference``, because a checker needs to know
which record it is judging. That is why event resources sit at the exposure
ceiling however narrow the manifest gets: narrowing cannot remove a field the
check needs.

The tiered design breaks that floor by separating who may hold an identifier
from who may not:

    D0 Triage           routes, holds nothing
    D1 Registration     the Patient tier
    D2 Records Linkage  resolves the reference once, issues a surrogate
    D3 Clinical Checks  judges event content against the surrogate only

D2 is the only tier that both handles event resources and sees a raw reference.
D3 does the clinical work and never sees one.

The ladder
----------
Three rungs, from :data:`dqa.linkage.LINKAGE_LEVELS`:

* ``reference`` (L0) is the flat system: every agent sees the raw reference.
* ``surrogate`` (L1) is the tiered system: D2 replaces each reference with a
  run-scoped opaque token before D3 sees it.
* ``none`` (L2) drops linkage entirely, which is the floor: agents judge records
  they cannot join.

Why this could not be run before
--------------------------------
``REQUIRED_FIELDS`` demanded ``$.subject.reference`` literally, so completeness
failed on every event resource the moment a surrogate replaced it, and the
ladder would have appeared to prove that surrogates destroy detection.
:func:`dqa.agents._requirement_met` now accepts the canonical path or the
surrogate that replaced it, so L1 is measurable. L2 still fails completeness,
correctly, because there the linkage really is absent.

Offline and deterministic: no model, no network, no clock.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from dqa.agents import check_completeness, check_plausibility_ranges, check_timeliness
from dqa.controlled import inject_cohort
from dqa.departments import DEPARTMENTS, route
from dqa.linkage import LINKAGE_LEVELS, SurrogateIssuer, rewrite_slice
from dqa.manifests import DIMENSIONS, WIDTHS, load_width, project
from dqa.metrics import Row, f1, phi_exposed, phi_total
from dqa.naming import CLINICAL_CHECKS, RECORDS_LINKAGE
from dqa.ranges import RangeRule
from dqa.run import DATASETS, LIMIT_DEFAULT, MANIFESTS_DIR, load_default_ranges, read_cohort

SEEDS = (42, 43, 44, 45)
RATE = 0.30

# Which tier a dimension's check belongs to under the department design. D2
# resolves references and runs no check of its own; D3 does the clinical work.
# Patient-oriented checks sit in D1, which is allowed to hold identifiers.
TIER_OF_DIMENSION = {
    "completeness": CLINICAL_CHECKS,
    "plausibility": CLINICAL_CHECKS,
    "consistency": CLINICAL_CHECKS,
    "timeliness": CLINICAL_CHECKS,
}


def _rule_verdict(dimension: str, sliced: dict[str, Any], rules: dict[str, RangeRule]):
    """The rule-only checker for one dimension, or None if it needs a model.

    Consistency is model-backed in the flat system and has no rule path, so it
    is excluded here. Running the ladder offline keeps it deterministic, which
    matters more than covering a fourth dimension whose score would be a cache
    artefact.
    """
    if dimension == "completeness":
        return check_completeness(sliced)
    if dimension == "timeliness":
        return check_timeliness(sliced)
    if dimension == "plausibility":
        return check_plausibility_ranges(sliced, rules)
    return None


def run_cell(
    dataset_dir: Path,
    seed: int,
    width: str,
    rung: str,
    rules: dict[str, RangeRule],
    limit: int = LIMIT_DEFAULT,
    rate: float = RATE,
) -> dict[str, Any]:
    """One cohort at one width on one rung of the ladder.

    Routing is by :func:`dqa.departments.route`. A resource entering at D2 has
    its linkage rewritten before any D3 agent sees it, which is the whole point
    of the tier. Exposure is scored on what D3 receives, because that is what
    crosses the boundary the design exists to protect.
    """
    manifest_set = load_width(MANIFESTS_DIR, width)
    cohort = read_cohort(dataset_dir, limit)
    prepared = inject_cohort(cohort, seed, rate)
    issuer = SurrogateIssuer(seed=seed)

    rows: dict[str, list[Row]] = {d: [] for d in DIMENSIONS}
    exposed_total = 0
    present_total = 0
    scored_records = 0
    routed: dict[str, int] = {}

    for resource, true_dimension in prepared:
        entry = route(resource)
        routed[entry] = routed.get(entry, 0) + 1

        released: list[dict[str, Any]] = []
        for dimension in DIMENSIONS:
            sliced = project(resource, manifest_set[dimension])
            if sliced is None:
                continue
            fields = sliced["_projected_fields"]

            # D2 resolves the reference once and hands D3 a surrogate. A
            # resource that enters at D1 is the Patient tier's own subject, so
            # its identifiers are not rewritten.
            if entry == RECORDS_LINKAGE and TIER_OF_DIMENSION.get(dimension) == CLINICAL_CHECKS:
                fields = rewrite_slice(fields, issuer, rung)

            released.append(fields)
            verdict = _rule_verdict(dimension, {**sliced, "_projected_fields": fields}, rules)
            if verdict is not None:
                rows[dimension].append(
                    Row(
                        str(resource.get("id", "")),
                        true_dimension,
                        frozenset({dimension}) if verdict.is_failure else frozenset(),
                    )
                )

        present = phi_total(resource)
        if present:
            exposed_total += min(phi_exposed(released), present)
            present_total += present
            scored_records += 1

    scores = {f"f1_{d}": round(f1(rows[d], d), 4) for d in DIMENSIONS if rows[d]}
    return {
        "seed": seed,
        "width": width,
        "linkage": rung,
        "routed": dict(sorted(routed.items())),
        "exposure": round(exposed_total / present_total, 4) if present_total else 0.0,
        "scored_records": scored_records,
        "surrogates_issued": issuer.issued,
        **scores,
    }


def tiered(seeds: tuple[int, ...] = SEEDS, limit: int = LIMIT_DEFAULT, rate: float = RATE):
    rules = load_default_ranges()
    cells = []
    for rung in LINKAGE_LEVELS:
        for width in WIDTHS:
            for seed in seeds:
                for name, directory in DATASETS.items():
                    if not directory.is_dir():
                        continue
                    cell = run_cell(directory, seed, width, rung, rules, limit, rate)
                    cell["dataset"] = name
                    cells.append(cell)

    summary = []
    for rung in LINKAGE_LEVELS:
        sel = [c for c in cells if c["linkage"] == rung]

        def got(key: str, cells_for_rung: list = sel) -> list:
            return [c[key] for c in cells_for_rung if key in c]
        summary.append(
            {
                "linkage": rung,
                "n_cells": len(sel),
                "mean_exposure": round(sum(c["exposure"] for c in sel) / len(sel), 4),
                "mean_f1_completeness": round(sum(got("f1_completeness")) / len(got("f1_completeness")), 4)
                if got("f1_completeness") else None,
                "mean_f1_plausibility": round(sum(got("f1_plausibility")) / len(got("f1_plausibility")), 4)
                if got("f1_plausibility") else None,
                "mean_f1_timeliness": round(sum(got("f1_timeliness")) / len(got("f1_timeliness")), 4)
                if got("f1_timeliness") else None,
            }
        )

    return {
        "description": (
            "The department-tiered system run end to end across the L0/L1/L2 linkage "
            "ladder. D2 resolves each reference once and issues a run-scoped surrogate; "
            "D3 judges event content against the surrogate only. Rule-only checkers, so "
            "no model, no network, no clock."
        ),
        "seeds": list(seeds),
        "rate": rate,
        "limit": limit,
        "widths": list(WIDTHS),
        "linkage_levels": list(LINKAGE_LEVELS),
        "departments": [
            {"id": d.id, "name": d.name, "holds_identifiers": d.holds_identifiers}
            for d in DEPARTMENTS
        ],
        "summary": summary,
        "cells": cells,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the department-tiered system")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=LIMIT_DEFAULT)
    parser.add_argument("--rate", type=float, default=RATE)
    parser.add_argument("--seeds", type=int, nargs="+", default=list(SEEDS))
    args = parser.parse_args(argv)

    doc = tiered(tuple(args.seeds), args.limit, args.rate)

    def fmt(v: float | None) -> str:
        return f"{v:.4f}" if v is not None else "  --  "

    print(f"{'rung':<12}{'exposure':>10}{'complete':>10}{'plaus':>9}{'timely':>9}", file=sys.stderr)
    for row in doc["summary"]:
        print(
            f"{row['linkage']:<12}{row['mean_exposure']:>10.4f}"
            f"{fmt(row['mean_f1_completeness']):>10}{fmt(row['mean_f1_plausibility']):>9}"
            f"{fmt(row['mean_f1_timeliness']):>9}",
            file=sys.stderr,
        )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, indent=2) + "\n")
    print(f"wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
