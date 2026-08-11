"""The privilege ablation, pinned.

``dqa.controlled`` reports 0.0000 pp and its tests pin that zero. What neither
could show is that the zero is a *measurement*: the range checker consumes three
JSONPaths, all three shipped widths release all three, so the two arms read
identical inputs and the difference between them is an identity.

``dqa.ablation`` moves the variable that the controlled comparison holds fixed.
These tests pin both ends of it: that withholding a field the checker does not
read still costs exactly nothing (so the headline zero survives), and that
withholding one it does read costs a great deal (so the instrument is capable of
registering a cost at all). Without the second half the first is unfalsifiable.

Everything here is offline, rule-based and deterministic.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from dqa.ablation import (
    ABLATION_DIR,
    CONSUMED_PATHS,
    CONTROL_NAME,
    CONTROL_PATH,
    Arm,
    aggregate,
    load_arms,
    load_plausibility_manifest,
    privilege_ablation,
)
from dqa.controlled import LIMIT, RATE, SEEDS, controlled_comparison
from dqa.manifests import Manifest
from dqa.ranges import CODE_PATH, UNIT_PATH, VALUE_PATH

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def arms() -> list[Arm]:
    return load_arms()


@pytest.fixture(scope="module")
def document() -> dict:
    return privilege_ablation(seeds=SEEDS, rate=RATE, limit=LIMIT)


def _row(document: dict, arm: str) -> dict:
    return next(r for r in document["aggregate"] if r["arm"] == arm)


# --------------------------------------------------------------- the arm files


def test_arms_load_with_the_control_first(arms) -> None:
    assert arms[0].name == CONTROL_NAME
    assert arms[0].path == CONTROL_PATH
    assert arms[0].withheld == ()
    assert len(arms) >= 4, "expected the control plus one arm per consumed field"


def test_every_ablation_arm_withholds_exactly_one_field(arms) -> None:
    """Nothing else may differ, or the effect cannot be attributed to a field."""
    for arm in arms[1:]:
        assert len(arm.withheld) == 1, f"{arm.name} withholds {arm.withheld}"


def test_arm_names_and_manifest_paths_are_unique(arms) -> None:
    assert len({a.name for a in arms}) == len(arms)
    assert len({a.path for a in arms}) == len(arms)


def test_every_consumed_field_has_an_arm(arms) -> None:
    """The ablation must cover the checker's whole appetite.

    If ``dqa.ranges`` ever starts reading a fourth field, this fails until an
    arm for it is declared, which is the point: an ablation that skips a
    consumed field understates the cost of projection.
    """
    withheld = {path for arm in arms for path in arm.withheld}
    assert set(CONSUMED_PATHS) == {CODE_PATH, VALUE_PATH, UNIT_PATH}
    missing = set(CONSUMED_PATHS) - withheld
    assert not missing, f"no ablation arm withholds {sorted(missing)}"


def test_there_is_a_negative_control(arms) -> None:
    """At least one arm must withhold a released-but-unread field."""
    unconsumed = [a for a in arms[1:] if not a.consumed]
    assert unconsumed, (
        "the ablation needs an arm withholding a field the checker ignores; "
        "it is what shows the headline zero comes from released-minus-consumed"
    )


def test_ablation_manifests_validate_against_the_shipped_schema() -> None:
    """``extra='forbid'``: a stray key must abort before any resource is read."""
    for path in sorted(ABLATION_DIR.glob("*.yaml")):
        manifest = load_plausibility_manifest(path)
        assert manifest.dimension == "plausibility"
        assert manifest.policy_for("Observation") is not None


def test_arms_are_the_control_minus_one_line(arms) -> None:
    """Textually as well as structurally: the files are copies, not rewrites."""
    control = yaml.safe_load(CONTROL_PATH.read_text())
    for arm in arms[1:]:
        candidate = yaml.safe_load(arm.path.read_text())
        assert candidate["agent_id"] == control["agent_id"]
        assert candidate["note_access"] == control["note_access"]
        assert candidate["rule_references"] == control["rule_references"]


# ------------------------------------------------------ rejection of bad arms


def _write_variant(tmp_path: Path, mutate) -> Path:
    document = yaml.safe_load(CONTROL_PATH.read_text())
    mutate(document)
    directory = tmp_path / "ablation"
    directory.mkdir(exist_ok=True)
    path = directory / "variant.yaml"
    path.write_text(yaml.safe_dump(document))
    return directory


def test_an_arm_that_withholds_nothing_is_rejected(tmp_path) -> None:
    directory = _write_variant(tmp_path, lambda doc: None)
    with pytest.raises(ValueError, match="withholds 0 fields"):
        load_arms(directory)


def test_an_arm_that_withholds_two_fields_is_rejected(tmp_path) -> None:
    def mutate(doc):
        doc["resource_types"][0]["allowed_fields"] = doc["resource_types"][0][
            "allowed_fields"
        ][:2]

    directory = _write_variant(tmp_path, mutate)
    with pytest.raises(ValueError, match="withholds 2 fields"):
        load_arms(directory)


def test_an_arm_that_grants_an_extra_field_is_rejected(tmp_path) -> None:
    def mutate(doc):
        doc["resource_types"][0]["allowed_fields"].append(
            {"jsonpath_expression": "$.referenceRange[*].low.value"}
        )

    directory = _write_variant(tmp_path, mutate)
    with pytest.raises(ValueError, match="may only withhold"):
        load_arms(directory)


def test_an_arm_that_changes_anything_else_is_rejected(tmp_path) -> None:
    """A second variable is not an ablation."""

    def mutate(doc):
        doc["resource_types"][0]["allowed_fields"].pop()
        doc["note_access"] = {"enabled": True}

    directory = _write_variant(tmp_path, mutate)
    with pytest.raises(ValueError, match="outside its allow-list"):
        load_arms(directory)


def test_a_manifest_for_another_dimension_is_rejected(tmp_path) -> None:
    path = tmp_path / "completeness.yaml"
    document = yaml.safe_load((ROOT / "manifests" / "minimal" / "completeness.yaml").read_text())
    path.write_text(yaml.safe_dump(document))
    with pytest.raises(ValueError, match="scores 'plausibility' only"):
        load_plausibility_manifest(path)


def test_an_empty_ablation_directory_is_rejected(tmp_path) -> None:
    (tmp_path / "empty").mkdir()
    with pytest.raises(FileNotFoundError):
        load_arms(tmp_path / "empty")


# ------------------------------------------------------------------ the result


def test_the_control_arm_costs_exactly_nothing(document) -> None:
    """The headline, reproduced inside the ablation.

    This is what licenses reading the other arms as effects rather than as
    artefacts of this harness.
    """
    row = _row(document, CONTROL_NAME)
    assert row["max_abs_gap_pp"] == pytest.approx(0.0, abs=1e-9)
    for cell in document["cells"]:
        assert cell[f"gap_{CONTROL_NAME}_pp"] == pytest.approx(0.0, abs=1e-9)


def test_withholding_an_unread_field_also_costs_nothing(document, arms) -> None:
    """The negative control. Released-but-unconsumed is free, by construction."""
    for arm in arms[1:]:
        if arm.consumed:
            continue
        assert _row(document, arm.name)["max_abs_gap_pp"] == pytest.approx(0.0, abs=1e-9), (
            f"{arm.name} withholds {arm.withheld}, which the checker does not read, "
            f"yet the score moved"
        )


def test_withholding_a_consumed_field_always_costs_something(document, arms) -> None:
    """The whole point. The independent variable must actually vary."""
    for arm in arms[1:]:
        if not arm.consumed:
            continue
        worst = _row(document, arm.name)["max_abs_gap_pp"]
        assert worst > 0.0, (
            f"{arm.name} withholds {arm.withheld}, which dqa.ranges reads, yet no "
            f"cell moved; the ablation would be as vacuous as the comparison it fixes"
        )


def test_withholding_the_value_destroys_detection(document) -> None:
    """The ceiling of the effect, and the sanity anchor for everything below it.

    With no numeric value in the slice the agent returns 'uncertain' on every
    resource, flags nothing, and scores zero in every cell.
    """
    row = _row(document, "withhold_observation_value")
    assert row["max_A6"] == pytest.approx(0.0, abs=1e-9)
    assert row["max_abs_gap_pp"] == pytest.approx(100.0, abs=1e-9)


def test_the_effect_sizes_are_ordered_and_separated(document) -> None:
    """A dose-response curve, not four points at the same height."""
    by_arm = {row["arm"]: row["max_abs_gap_pp"] for row in document["aggregate"]}
    assert by_arm["withhold_observation_value"] > by_arm["withhold_observation_unit"]
    assert by_arm["withhold_observation_unit"] > by_arm["withhold_observation_code"]
    assert by_arm["withhold_observation_code"] > by_arm[CONTROL_NAME]


def test_aggregate_is_sorted_worst_first(document) -> None:
    worst = [row["max_abs_gap_pp"] for row in document["aggregate"]]
    assert worst == sorted(worst, reverse=True)


def test_the_cells_are_the_full_grid(document) -> None:
    assert len(document["cells"]) == 12, f"expected 12 cells, got {len(document['cells'])}"
    assert {c["seed"] for c in document["cells"]} == set(SEEDS)
    assert len({(c["seed"], c["dataset"]) for c in document["cells"]}) == 12
    for cell in document["cells"]:
        assert cell["n_plausibility"] >= 25, (
            f"{cell['dataset']}: only {cell['n_plausibility']} plausibility defects; "
            f"a gap measured over that few would mean nothing"
        )


def test_gaps_are_recomputable_from_the_reported_columns(document, arms) -> None:
    """Every gap in the table must be derivable from its own row."""
    for cell in document["cells"]:
        for arm in arms:
            expected = round((cell[f"confined_{arm.name}"] - cell["baseline_ranges"]) * 100, 4)
            assert cell[f"gap_{arm.name}_pp"] == pytest.approx(expected, abs=1e-9)


def test_aggregate_agrees_with_the_cells(document, arms) -> None:
    for arm in arms:
        gaps = [c[f"gap_{arm.name}_pp"] for c in document["cells"]]
        row = _row(document, arm.name)
        assert row["n_cells"] == len(gaps)
        assert row["min_gap_pp"] == pytest.approx(min(gaps), abs=1e-9)
        assert row["max_gap_pp"] == pytest.approx(max(gaps), abs=1e-9)
        assert row["max_abs_gap_pp"] == pytest.approx(max(abs(g) for g in gaps), abs=1e-9)


# ----------------------------------------------------------- the run contract


def test_result_document_carries_a_contract(document) -> None:
    for key in (
        "seeds", "rate", "limit", "resource_types", "allocation",
        "consumed_paths", "control_arm", "arms", "ranges_hash", "primary_endpoint",
    ):
        assert key in document, f"contract is missing {key}"
    assert document["seeds"] == list(SEEDS)
    assert document["rate"] == RATE
    assert document["limit"] == LIMIT
    assert document["control_arm"] == CONTROL_NAME
    assert document["consumed_paths"] == list(CONSUMED_PATHS)
    assert len(document["ranges_hash"]) == 64, "ranges_hash should be a SHA-256 hex digest"
    assert document["primary_endpoint"] == "f1_plausibility"
    for entry in document["arms"]:
        assert len(entry["manifests_hash"]) == 64
        assert (ROOT / entry["manifest"]).is_file()


def test_every_arm_has_a_distinct_manifest_hash(document) -> None:
    """Two arms hashing alike would mean two arms projecting alike."""
    hashes = [entry["manifests_hash"] for entry in document["arms"]]
    assert len(set(hashes)) == len(hashes)


def test_shares_the_headline_configuration_and_baseline(document) -> None:
    """The Baseline column must be the controlled comparison's Baseline column, cell for cell.

    That is what makes the two artefacts readable side by side. It holds because
    the scoring helpers are imported from ``dqa.controlled`` rather than
    reimplemented here; this test is what would notice if they stopped being.
    """
    headline = controlled_comparison(seeds=SEEDS, rate=RATE, limit=LIMIT)
    assert document["seeds"] == headline["seeds"]
    assert document["rate"] == headline["rate"]
    assert document["limit"] == headline["limit"]
    assert document["resource_types"] == headline["resource_types"]
    assert document["ranges_hash"] == headline["ranges_hash"]
    assert document["primary_endpoint"] == headline["primary_endpoint"]

    theirs = {(c["seed"], c["dataset"]): c for c in headline["cells"]}
    for cell in document["cells"]:
        other = theirs[(cell["seed"], cell["dataset"])]
        assert cell["baseline_ranges"] == pytest.approx(other["baseline_ranges"], abs=1e-9)
        assert cell["n_plausibility"] == other["n_plausibility"]
        # The control arm IS manifests/minimal/plausibility.yaml, so it must
        # equal the headline's minimal-width column exactly.
        assert cell[f"confined_{CONTROL_NAME}"] == pytest.approx(other["confined_minimal"], abs=1e-9)


def test_the_control_manifest_is_the_shipped_minimal_one() -> None:
    """Not a copy. A copy could drift from the file the headline actually used."""
    assert CONTROL_PATH == ROOT / "manifests" / "minimal" / "plausibility.yaml"
    assert CONTROL_PATH.is_file()
    assert isinstance(load_plausibility_manifest(CONTROL_PATH), Manifest)
    assert CONTROL_PATH.parent != ABLATION_DIR


# ------------------------------------------------------------- the artefact


def test_matches_the_shipped_artefact(document) -> None:
    """Guards against the code and the published JSON drifting apart."""
    shipped = ROOT / "results" / "privilege_ablation.json"
    if not shipped.is_file():
        pytest.skip("shipped artefact not present")
    published = json.loads(shipped.read_text())
    assert published["ranges_hash"] == document["ranges_hash"]
    assert {e["arm"]: e["manifests_hash"] for e in published["arms"]} == {
        e["arm"]: e["manifests_hash"] for e in document["arms"]
    }
    old = {(c["seed"], c["dataset"]): c for c in published["cells"]}
    assert len(old) == len(document["cells"])
    for cell in document["cells"]:
        assert cell == old[(cell["seed"], cell["dataset"])], (
            f"{cell['dataset']} seed {cell['seed']} drifted from the shipped artefact"
        )


def test_the_run_is_deterministic() -> None:
    """No model, no network, no clock: two runs must be identical."""
    first = privilege_ablation(seeds=(42,), rate=RATE, limit=LIMIT)
    second = privilege_ablation(seeds=(42,), rate=RATE, limit=LIMIT)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_aggregate_of_no_cells_is_empty(arms) -> None:
    assert aggregate([], arms) == [
        {
            "arm": arm.name,
            "withheld": list(arm.withheld),
            "withholds_a_consumed_field": arm.consumed,
            "n_cells": 0,
            "mean_A6": 0.0,
            "min_A6": 0.0,
            "max_A6": 0.0,
            "mean_gap_pp": 0.0,
            "min_gap_pp": 0.0,
            "max_gap_pp": 0.0,
            "max_abs_gap_pp": 0.0,
        }
        for arm in sorted(arms, key=lambda a: a.name)
    ]
