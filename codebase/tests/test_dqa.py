"""Tests covering the invariants the paper's claims rest on."""

from __future__ import annotations

import random

import pytest

from dqa.baseline import MonolithicBaseline
from dqa.inject import (
    inject_completeness,
    inject_consistency,
    inject_plausibility,
    inject_timeliness,
)
from dqa.manifests import DIMENSIONS, WIDTHS, Manifest, load_width, project
from dqa.metrics import LeakRecord, Row, leak_mean, leak_record, phi_total, score
from dqa.run import MANIFESTS_DIR

OBSERVATION = {
    "resourceType": "Observation",
    "id": "obs-1",
    "status": "final",
    "code": {"coding": [{"code": "8867-4", "system": "http://loinc.org"}]},
    "subject": {"reference": "Patient/p1"},
    "effectiveDateTime": "2024-01-15T08:30:00+00:00",
    "valueQuantity": {"value": 72.0, "unit": "beats/min", "code": "/min"},
}

ENCOUNTER = {
    "resourceType": "Encounter",
    "id": "enc-1",
    "status": "finished",
    "subject": {"reference": "Patient/p1"},
    "period": {"start": "2024-01-15T08:00:00+00:00", "end": "2024-01-15T12:00:00+00:00"},
}

PATIENT = {
    "resourceType": "Patient",
    "id": "p1",
    "gender": "female",
    "birthDate": "1961-04-02",
    "name": [{"family": "Example", "given": ["Pat"]}],
    "address": [{"city": "Boston"}],
}


def all_manifests() -> dict[str, dict[str, Manifest]]:
    return {width: load_width(MANIFESTS_DIR, width) for width in WIDTHS}


# ---------------------------------------------------------------- manifests


def test_every_width_loads_and_validates() -> None:
    sets = all_manifests()
    for width in WIDTHS:
        assert set(sets[width]) == set(DIMENSIONS)


def test_projection_releases_only_allowed_fields() -> None:
    manifests = load_width(MANIFESTS_DIR, "minimal")
    sliced = project(PATIENT, manifests["plausibility"])
    assert sliced is not None
    released = sliced["_projected_fields"]
    # The minimal plausibility manifest must not release birthDate, name,
    # or address. This is the least-privilege invariant the paper claims.
    for path in released:
        assert "birthDate" not in path
        assert ".name" not in path
        assert ".address" not in path


def test_full_width_releases_birthdate_to_plausibility() -> None:
    manifests = load_width(MANIFESTS_DIR, "full")
    sliced = project(PATIENT, manifests["plausibility"])
    assert sliced is not None
    assert any("birthDate" in path for path in sliced["_projected_fields"])


def test_projection_returns_none_for_undeclared_type() -> None:
    manifests = load_width(MANIFESTS_DIR, "minimal")
    assert project({"resourceType": "Medication"}, manifests["completeness"]) is None


# ---------------------------------------------------------------- injection


ALL_INJECTORS = [
    (inject_completeness, OBSERVATION),
    (inject_plausibility, OBSERVATION),
    (inject_consistency, ENCOUNTER),
    (inject_timeliness, OBSERVATION),
]


@pytest.mark.parametrize(("injector", "resource"), ALL_INJECTORS)
def test_every_injector_is_deterministic_given_seed(injector, resource) -> None:
    a = injector(resource, random.Random(42))
    b = injector(resource, random.Random(42))
    assert a[0] == b[0]
    assert a[1] == b[1]


@pytest.mark.parametrize(("injector", "resource"), ALL_INJECTORS)
def test_every_injector_leaves_input_unmutated(injector, resource) -> None:
    import copy

    original = copy.deepcopy(resource)
    injector(resource, random.Random(1))
    assert resource == original


def test_consistency_injection_breaks_period_order() -> None:
    modified, label = inject_consistency(ENCOUNTER, random.Random(7))
    assert label.dimension == "consistency"
    assert modified["period"]["end"] < modified["period"]["start"]


# ---------------------------------------------------------------- the Baseline


def test_baseline_passes_clean_resources() -> None:
    baseline = MonolithicBaseline()
    for resource in (OBSERVATION, ENCOUNTER):
        verdicts, projected = baseline.evaluate(resource)
        failures = [v for v in verdicts if v.is_failure]
        assert not failures, failures
        assert projected == []


def test_baseline_catches_each_injected_dimension() -> None:
    baseline = MonolithicBaseline()
    rng = random.Random(42)

    for injector, resource, dimension in (
        (inject_completeness, OBSERVATION, "completeness"),
        (inject_plausibility, OBSERVATION, "plausibility"),
        (inject_consistency, ENCOUNTER, "consistency"),
        (inject_timeliness, OBSERVATION, "timeliness"),
    ):
        modified, _ = injector(resource, rng)
        verdicts, _ = baseline.evaluate(modified)
        assert any(v.dimension == dimension and v.is_failure for v in verdicts)


# ---------------------------------------------------------------- metrics


def test_f1_scores_perfect_detection() -> None:
    rows = [
        Row("r1", "completeness", frozenset({"completeness"})),
        Row("r2", None, frozenset()),
    ]
    assert score(rows)["f1_completeness"] == 1.0


def test_agentleak_counts_safe_harbor_release() -> None:
    assert phi_total(PATIENT) == 3  # name, address, birthDate
    record = leak_record(PATIENT, [{"$.birthDate": ["1961-04-02"]}])
    assert record.exposed == 1
    assert record.total == 3
    assert record.value == pytest.approx(1 / 3)


def test_leak_mean_excludes_t0_records() -> None:
    records = [
        LeakRecord("a", 1, 2),
        LeakRecord("b", 0, 0),
    ]
    mean, excluded = leak_mean(records)
    assert mean == 0.5
    assert excluded == 1
