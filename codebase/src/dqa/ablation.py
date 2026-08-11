"""The privilege ablation, as a runnable code path.

    python -m dqa.ablation --out results/privilege_ablation.json

What it measures
----------------
:mod:`dqa.controlled` reports that field projection costs 0.0000 pp of
plausibility F1 in all twelve seed-by-cohort cells, at all three manifest
widths. That number is true and it is also, as constructed, an identity rather
than a measurement.

The range checker consumes exactly three JSONPaths --
``dqa.ranges.CODE_PATH``, ``VALUE_PATH`` and ``UNIT_PATH``. All three shipped
widths release all three. So both arms of the controlled comparison read
byte-identical inputs on every resource, and the difference between them is
zero for the same reason ``x - x`` is zero. The independent variable never
varies: privilege is nominally swept from minimal to full while the privilege
that *matters to the checker* is held constant at "everything it needs".

This module makes the variable vary. It holds the checker, the cohorts, the
seeds and the injection fixed, and moves exactly one thing: which single field
the plausibility agent's manifest withholds. Each arm is a manifest under
``manifests/ablation/``, identical to ``manifests/minimal/plausibility.yaml``
except that one ``allowed_field`` is deleted. The result is a dose-response
curve over privilege instead of a constant.

The arms
--------
* ``control_minimal`` -- the unmodified ``manifests/minimal/plausibility.yaml``.
  Must reproduce 0.0000 pp in every cell. It is what demonstrates that the
  headline zero is a property of the shipped manifests, not of the method or of
  this harness;
* ``withhold_observation_code`` -- the analyte key. Every Observation falls back
  to the ``default`` envelope in the rule file;
* ``withhold_observation_unit`` -- the unit-scoped rule key can no longer be
  resolved, so multi-unit codes get another analyte's bounds;
* ``withhold_observation_value`` -- the measurement itself. The agent has
  nothing numeric to judge, returns ``uncertain`` everywhere and scores zero.
  The ceiling of the effect;
* ``withhold_ucum_code`` -- ``$.valueQuantity.code``, which every width releases
  and no arm reads. The negative control: it must also cost 0.0000 pp.

Reading the result
------------------
The control and the negative control bracket the claim. Withholding a released
but unconsumed field costs nothing; withholding a consumed one costs between a
few and a hundred percentage points. The shipped zero therefore says that the
three fields this checker needs are disjoint from the fields the manifests
withhold, which is a statement about *this* rule set's appetite, not a general
result that least privilege is free. Any check with a wider appetite -- one
reading ``referenceRange``, ``component[*]``, patient age or a note -- sits
somewhere on the curve below rather than at its zero end.

Two properties of the arms are worth stating because they are what make the
comparison an ablation rather than four unrelated runs:

* the arms differ from the control in exactly one ``allowed_field`` and in
  nothing else, which :func:`load_arms` verifies on load and refuses to run
  otherwise;
* every arm scores the same prepared cohort, injected once per cell, so a gap
  between two arms cannot be a difference in what they were shown.

Comparability with the headline
-------------------------------
Same four seeds, same three cohorts, same rate, same limit, same primary
endpoint, and the scoring helpers are imported from :mod:`dqa.controlled`
rather than reimplemented, so the ``baseline_ranges`` column here is the same number
as the ``baseline_ranges`` column there, cell for cell.

Offline and deterministic: no model, no network, no clock.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from dqa.controlled import (
    INJECTION_MODE_DEFAULT,
    INJECTION_MODES,
    LIMIT,
    RATE,
    SEEDS,
    inject_cohort,
    rules_digest,
    score_full_access,
    score_projected,
)
from dqa.manifests import Manifest, ManifestSet, manifests_hash
from dqa.ranges import CODE_PATH, UNIT_PATH, VALUE_PATH, RangeRule
from dqa.run import (
    ADMITTED_RESOURCE_TYPES,
    DATASETS,
    MANIFESTS_DIR,
    ROOT,
    load_default_ranges,
    read_cohort,
)

# The dimension under ablation. Plausibility for the same reason dqa.controlled
# gives: it is the pre-declared primary endpoint and the only dimension whose
# score moves at all between the two systems.
DIMENSION = "plausibility"

# Where the ablation arms are declared. One YAML file per withheld field, so
# adding an arm is a data change rather than a code change.
ABLATION_DIR = MANIFESTS_DIR / "ablation"

# The control arm: the shipped minimal manifest, unmodified. Loaded from its own
# published location rather than copied into manifests/ablation/, because a copy
# could drift from the file the headline result actually used, and then the zero
# this arm reports would no longer be the headline's zero.
CONTROL_NAME = "control_minimal"
CONTROL_PATH = MANIFESTS_DIR / "minimal" / f"{DIMENSION}.yaml"

# The three JSONPaths dqa.ranges actually reads. The ablation space is exactly
# this set plus, as a negative control, one path the manifests release and the
# checker ignores. Recorded in the output because it is what decides whether an
# arm can possibly have an effect.
CONSUMED_PATHS: tuple[str, ...] = (CODE_PATH, VALUE_PATH, UNIT_PATH)


@dataclass(frozen=True, slots=True)
class Arm:
    """One ablation arm: a manifest, and what it withholds relative to control.

    ``withheld`` is derived by set difference against the control manifest, not
    declared in the file, so the label in the output cannot disagree with the
    manifest that produced the number.
    """

    name: str
    path: Path
    manifest: Manifest
    withheld: tuple[str, ...]

    @property
    def manifest_set(self) -> ManifestSet:
        """The single-dimension set :func:`dqa.controlled.score_projected` wants."""
        return {DIMENSION: self.manifest}

    @property
    def consumed(self) -> bool:
        """Whether this arm withholds anything the range checker reads."""
        return any(path in CONSUMED_PATHS for path in self.withheld)


def load_plausibility_manifest(path: Path) -> Manifest:
    """Load and validate one plausibility manifest.

    The dimension is checked against the one this module scores for the same
    reason :func:`dqa.manifests.load_width` checks it against the filename: a
    manifest for another dimension would be handed to the plausibility checker
    with nothing in the output to show it.
    """
    manifest = Manifest(**yaml.safe_load(Path(path).read_text()))
    if manifest.dimension != DIMENSION:
        raise ValueError(
            f"{path}: declares dimension {manifest.dimension!r}, but the privilege "
            f"ablation scores {DIMENSION!r} only"
        )
    return manifest


def _fields_by_type(manifest: Manifest) -> dict[str, tuple[str, ...]]:
    return {
        policy.fhir_resource_type: tuple(f.jsonpath_expression for f in policy.allowed_fields)
        for policy in manifest.resource_types
    }


def _withheld_against(control: Manifest, candidate: Manifest, path: Path) -> tuple[str, ...]:
    """The fields ``candidate`` removes from ``control``, or raise.

    An ablation arm is only interpretable if it differs from the control in the
    allow-list and in nothing else. A different ``agent_id``, an extra resource
    type, a changed ``note_access`` or an added field would all put a second
    variable into a one-variable experiment, so each is rejected here rather
    than quietly averaged into the result.
    """
    control_meta = control.model_dump(exclude={"resource_types"})
    candidate_meta = candidate.model_dump(exclude={"resource_types"})
    if control_meta != candidate_meta:
        raise ValueError(
            f"{path}: differs from {CONTROL_PATH.name} outside its allow-list "
            f"({control_meta} against {candidate_meta}); an ablation arm must differ "
            f"in withheld fields alone"
        )

    control_fields = _fields_by_type(control)
    candidate_fields = _fields_by_type(candidate)
    if set(control_fields) != set(candidate_fields):
        raise ValueError(
            f"{path}: declares resource types {sorted(candidate_fields)}, control "
            f"declares {sorted(control_fields)}"
        )

    withheld: list[str] = []
    for resource_type, allowed in control_fields.items():
        theirs = candidate_fields[resource_type]
        added = [p for p in theirs if p not in allowed]
        if added:
            raise ValueError(
                f"{path}: {resource_type} grants {added}, which the control does not; "
                f"an ablation arm may only withhold"
            )
        withheld.extend(p for p in allowed if p not in theirs)

    if len(withheld) != 1:
        raise ValueError(
            f"{path}: withholds {len(withheld)} fields ({withheld or 'none'}); "
            f"each arm must withhold exactly one, or the effect cannot be attributed"
        )
    return tuple(withheld)


def load_arms(
    ablation_dir: Path = ABLATION_DIR, control_path: Path = CONTROL_PATH
) -> list[Arm]:
    """The control arm followed by every declared ablation, in filename order.

    Arms are discovered by globbing ``ablation_dir``, so the experiment's shape
    lives in ``manifests/ablation/`` rather than in this file. Ordering is by
    filename so the output is stable across filesystems.
    """
    control = load_plausibility_manifest(control_path)
    arms = [Arm(CONTROL_NAME, control_path, control, ())]

    paths = sorted(Path(ablation_dir).glob("*.yaml"))
    if not paths:
        raise FileNotFoundError(f"no ablation manifests under {ablation_dir}")
    for path in paths:
        manifest = load_plausibility_manifest(path)
        arms.append(Arm(path.stem, path, manifest, _withheld_against(control, manifest, path)))

    names = [arm.name for arm in arms]
    if len(set(names)) != len(names):
        raise ValueError(f"duplicate ablation arm names: {names}")
    return arms


def _relative_to_root(path: Path) -> str:
    """Repository-relative manifest path, so the artefact is machine-independent."""
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def run_cell(
    dataset: str,
    dataset_dir: Path,
    seed: int,
    arms: list[Arm],
    rules: dict[str, RangeRule],
    rate: float = RATE,
    limit: int = LIMIT,
    mode: str = INJECTION_MODE_DEFAULT,
) -> dict[str, Any]:
    """One seed-by-cohort cell: full access against every ablation arm.

    Injection runs once and is shared by all arms, which is what makes the cell
    paired: two arms of the same cell saw the same defects in the same places.
    """
    cohort = read_cohort(dataset_dir, limit)
    prepared = inject_cohort(cohort, seed, rate, mode)

    cell: dict[str, Any] = {
        "seed": seed,
        "dataset": dataset,
        "n_plausibility": sum(1 for _, dim in prepared if dim == DIMENSION),
        "baseline_ranges": round(score_full_access(prepared, rules), 4),
    }
    for arm in arms:
        cell[f"confined_{arm.name}"] = round(score_projected(prepared, arm.manifest_set, rules), 4)
    # Percentage points, computed from the ROUNDED figures so every gap in the
    # table can be verified from the other columns of the same row. Signed, not
    # absolute: withholding the observation code raises Confined above full access on
    # one cohort, and an absolute gap would hide the direction of that.
    for arm in arms:
        cell[f"gap_{arm.name}_pp"] = round((cell[f"confined_{arm.name}"] - cell["baseline_ranges"]) * 100, 4)
    return cell


def aggregate(cells: list[dict[str, Any]], arms: list[Arm]) -> list[dict[str, Any]]:
    """Reduce the cells to one row per arm, ordered worst effect first."""
    rows: list[dict[str, Any]] = []
    for arm in arms:
        gaps = [cell[f"gap_{arm.name}_pp"] for cell in cells]
        scores = [cell[f"confined_{arm.name}"] for cell in cells]
        rows.append(
            {
                "arm": arm.name,
                "withheld": list(arm.withheld),
                "withholds_a_consumed_field": arm.consumed,
                "n_cells": len(cells),
                "mean_A6": round(sum(scores) / len(scores), 4) if scores else 0.0,
                "min_A6": min(scores, default=0.0),
                "max_A6": max(scores, default=0.0),
                "mean_gap_pp": round(sum(gaps) / len(gaps), 4) if gaps else 0.0,
                "min_gap_pp": min(gaps, default=0.0),
                "max_gap_pp": max(gaps, default=0.0),
                # The headline figure per arm, on the same scale and with the
                # same sign convention as controlled.py's max_abs_gap_pp.
                "max_abs_gap_pp": round(max((abs(g) for g in gaps), default=0.0), 4),
            }
        )
    rows.sort(key=lambda row: (-row["max_abs_gap_pp"], row["arm"]))
    return rows


def privilege_ablation(
    seeds: tuple[int, ...] = SEEDS,
    rate: float = RATE,
    limit: int = LIMIT,
    ablation_dir: Path = ABLATION_DIR,
    mode: str = INJECTION_MODE_DEFAULT,
) -> dict[str, Any]:
    """Every arm against every seed and every cohort. The full result document."""
    rules = load_default_ranges()
    arms = load_arms(ablation_dir)

    cells: list[dict[str, Any]] = []
    for seed in seeds:
        for dataset, dataset_dir in DATASETS.items():
            if not dataset_dir.is_dir():
                print(f"skip {dataset}: {dataset_dir} not found", file=sys.stderr)
                continue
            cells.append(
                run_cell(dataset, dataset_dir, seed, arms, rules, rate, limit, mode)
            )

    return {
        "description": (
            "Privilege ablation: identical range-based plausibility checker on both "
            "systems, with one consumed field withheld from the agent's manifest at a "
            "time. Converts the controlled comparison's constant zero into a "
            "dose-response curve over privilege."
        ),
        # The run contract, matching dqa.controlled's key for key where the two
        # experiments share a parameter, so the Baseline column can be checked against
        # the headline artefact rather than merely assumed to match it.
        "seeds": list(seeds),
        "rate": rate,
        "limit": limit,
        "resource_types": list(ADMITTED_RESOURCE_TYPES),
        "allocation": "stratified",
        "injection_mode": mode,
        # What the checker reads. An arm withholding a path outside this tuple
        # cannot move the score, and that is the point of the negative control.
        "consumed_paths": list(CONSUMED_PATHS),
        "control_arm": CONTROL_NAME,
        "arms": [
            {
                "arm": arm.name,
                "manifest": _relative_to_root(arm.path),
                "withheld": list(arm.withheld),
                "withholds_a_consumed_field": arm.consumed,
                # Hash of the one-manifest set actually projected through, so it
                # is deliberately NOT comparable with controlled.py's
                # manifests_hash, which covers all four dimensions of a width.
                "manifests_hash": manifests_hash(arm.manifest_set),
            }
            for arm in arms
        ],
        "ranges_hash": rules_digest(),
        "primary_endpoint": "f1_plausibility",
        "aggregate": aggregate(cells, arms) if cells else [],
        "cells": cells,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Privilege ablation: same checker, one consumed field withheld at a time"
    )
    parser.add_argument(
        "--out", type=Path, default=ROOT / "results" / "privilege_ablation.json"
    )
    parser.add_argument("--rate", type=float, default=RATE)
    parser.add_argument("--limit", type=int, default=LIMIT)
    parser.add_argument("--seeds", type=int, nargs="+", default=list(SEEDS))
    parser.add_argument("--manifests", type=Path, default=ABLATION_DIR)
    parser.add_argument(
        "--injection-mode",
        choices=list(INJECTION_MODES),
        default=INJECTION_MODE_DEFAULT,
        help="legacy reproduces the shipped artefact; hard derives each injected "
        "value from the analyte's own bound, which a sentinel test cannot catch",
    )
    args = parser.parse_args(argv)

    document = privilege_ablation(
        tuple(args.seeds), args.rate, args.limit, args.manifests, args.injection_mode
    )

    width = max((len(row["arm"]) for row in document["aggregate"]), default=4)
    print(
        f"{'arm':<{width}}  {'withheld':<26} {'consumed':>8} "
        f"{'mean Confined':>8} {'mean gap':>9} {'max |gap|':>10}",
        file=sys.stderr,
    )
    for row in document["aggregate"]:
        withheld = ", ".join(row["withheld"]) or "-- (control)"
        print(
            f"{row['arm']:<{width}}  {withheld:<26} "
            f"{'yes' if row['withholds_a_consumed_field'] else 'no':>8} "
            f"{row['mean_A6']:>8.4f} {row['mean_gap_pp']:>+8.2f}pp "
            f"{row['max_abs_gap_pp']:>8.2f}pp",
            file=sys.stderr,
        )
    print(
        f"{len(document['cells'])} cells x {len(document['arms'])} arms; "
        f"control {CONTROL_NAME} max |gap| "
        f"{next(r['max_abs_gap_pp'] for r in document['aggregate'] if r['arm'] == CONTROL_NAME):.4f} pp",
        file=sys.stderr,
    )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(document, indent=2))
    print(f"wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
