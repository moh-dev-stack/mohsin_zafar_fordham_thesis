"""The replication summary must be computed, not asserted.

``results/replication_summary.json`` supplied every confidence interval in the
paper and, until :mod:`dqa.replicate`, was produced by nothing in the bundle.
These tests pin the reduction, the guards that stop it summarising runs that are
not replications of one another, and the arithmetic of the interval itself.
"""

from __future__ import annotations

import json

import pytest

from dqa.replicate import (
    BASELINE_SYSTEM,
    CONFINED_SYSTEM,
    DEFAULT_SOURCES,
    METRICS,
    assert_comparable,
    datasets_of,
    gap_pp,
    load_runs,
    replication_summary,
    summarise,
    t_interval,
)
from dqa.run import ROOT


def _run(seed: int, gaps: dict[str, dict[str, float]]) -> dict:
    """A minimal run document: one Baseline score and one Confined-full score per cohort."""
    return {
        "model_snapshot": "test-model",
        "limit": 200,
        "injection_rate": 0.3,
        "allocation": "stratified",
        "seed": seed,
        "cohorts": [
            {
                "dataset": dataset,
                "systems": {
                    BASELINE_SYSTEM: {"scores": {m: 0.5 for m in METRICS}},
                    CONFINED_SYSTEM: {"scores": {m: 0.5 + per_metric.get(m, 0.0) for m in METRICS}},
                },
            }
            for dataset, per_metric in gaps.items()
        ],
    }


def _write(tmp_path, runs: dict[int, dict]) -> tuple:
    (tmp_path / "results").mkdir(exist_ok=True)
    sources = []
    for seed, document in runs.items():
        relative = f"results/run_{seed}.json"
        (tmp_path / relative).write_text(json.dumps(document))
        sources.append((seed, relative))
    return tuple(sources)


# ------------------------------------------------------------------ arithmetic


def test_t_interval_reproduces_the_published_arithmetic() -> None:
    """The published intervals are Student's t at df = 3, t = 3.182.

    Checked against the shipped synthea plausibility row: mean 21.07, sd 10.72,
    interval [4.01, 38.13].
    """
    mean, sd, lo, hi = t_interval([16.82, 34.37, 23.92, 9.17])
    assert round(mean, 2) == 21.07
    assert round(sd, 2) == 10.72
    assert round(lo, 2) == 4.01
    assert round(hi, 2) == 38.13


def test_a_gap_that_did_not_vary_gives_a_zero_width_interval() -> None:
    mean, sd, lo, hi = t_interval([-22.58] * 4)
    assert (round(mean, 2), round(sd, 2), round(lo, 2), round(hi, 2)) == (
        -22.58,
        0.0,
        -22.58,
        -22.58,
    )


def test_a_single_run_is_a_point_not_an_interval() -> None:
    assert t_interval([3.0]) == (3.0, 0.0, 3.0, 3.0)


def test_gap_is_confined_minus_baseline_in_percentage_points() -> None:
    document = _run(42, {"synthea": {"macro_f1": 0.05}})
    assert round(gap_pp(document, "synthea", "macro_f1"), 6) == 5.0
    assert round(gap_pp(document, "synthea", "f1_plausibility"), 6) == 0.0


def test_an_absent_cohort_is_an_error_not_a_zero() -> None:
    with pytest.raises(KeyError):
        gap_pp(_run(42, {"synthea": {}}), "eicu-demo", "macro_f1")


# ---------------------------------------------------------------------- guards


def test_a_missing_run_file_stops_the_summary(tmp_path) -> None:
    """Summarising three runs as four is the failure this module removes."""
    sources = _write(tmp_path, {42: _run(42, {"synthea": {}})})
    sources = (*sources, (43, "results/run_43.json"))
    with pytest.raises(FileNotFoundError, match="missing run file for seed 43"):
        load_runs(tmp_path, sources)


def test_a_run_file_recording_the_wrong_seed_is_refused(tmp_path) -> None:
    sources = _write(tmp_path, {42: _run(99, {"synthea": {}})})
    with pytest.raises(ValueError, match="records seed 99"):
        load_runs(tmp_path, sources)


def test_runs_under_different_contracts_are_not_replications() -> None:
    a = _run(42, {"synthea": {}})
    b = _run(43, {"synthea": {}})
    b["injection_rate"] = 0.10
    with pytest.raises(ValueError, match="injection_rate"):
        assert_comparable({42: a, 43: b})


def test_runs_over_different_cohorts_are_refused() -> None:
    a = _run(42, {"synthea": {}, "eicu-demo": {}})
    b = _run(43, {"synthea": {}})
    with pytest.raises(ValueError, match="covers cohorts"):
        datasets_of({42: a, 43: b})


def test_a_shared_contract_passes_and_is_returned() -> None:
    contract = assert_comparable(
        {42: _run(42, {"synthea": {}}), 43: _run(43, {"synthea": {}})}
    )
    assert contract["allocation"] == "stratified"
    assert "seed" not in contract
    assert "cohorts" not in contract


# --------------------------------------------------------------------- output


def test_summary_reports_both_intervals_and_the_corrections() -> None:
    runs = {
        seed: _run(seed, {"synthea": {"macro_f1": gap / 100.0}})
        for seed, gap in zip((42, 43, 44, 45), (1.0, 2.0, 3.0, 4.0), strict=True)
    }
    out = summarise(runs)
    cell = out["macro_f1"]["synthea"]
    assert cell["mean_pp"] == 2.5
    assert cell["runs_pp"] == [1.0, 2.0, 3.0, 4.0]
    assert cell["ci95_lo"] < cell["mean_pp"] < cell["ci95_hi"]
    assert cell["ci95_boot_lo"] <= cell["mean_pp"] <= cell["ci95_boot_hi"]
    assert isinstance(cell["holm_reject"], bool)
    assert cell["non_inferiority"]["margin"] == 0.03


def test_a_gap_of_exactly_zero_is_reported_as_zero_with_p_one() -> None:
    runs = {seed: _run(seed, {"synthea": {}}) for seed in (42, 43, 44, 45)}
    cell = summarise(runs)["macro_f1"]["synthea"]
    assert cell["mean_pp"] == 0.0
    assert cell["sd_pp"] == 0.0
    assert cell["p_value"] == 1.0
    assert cell["holm_reject"] is False


def test_non_inferiority_is_tested_in_the_direction_the_study_claims() -> None:
    """Confined ahead of Baseline must pass; Confined far behind Baseline must fail.

    The gap is stored as Confined minus Baseline, and the test wants Baseline minus Confined, so a sign
    error here would report a large loss as non-inferior.
    """
    ahead = summarise(
        {s: _run(s, {"synthea": {"macro_f1": 0.05}}) for s in (42, 43, 44, 45)}
    )["macro_f1"]["synthea"]
    assert ahead["non_inferiority"]["passed"] is True
    assert ahead["non_inferiority"]["difference"] < 0

    behind = summarise(
        {s: _run(s, {"synthea": {"macro_f1": -0.20}}) for s in (42, 43, 44, 45)}
    )["macro_f1"]["synthea"]
    assert behind["non_inferiority"]["passed"] is False
    assert behind["non_inferiority"]["difference"] > behind["non_inferiority"]["margin"]


def test_every_metric_and_cohort_appears() -> None:
    runs = {s: _run(s, {"synthea": {}, "eicu-demo": {}}) for s in (42, 43, 44, 45)}
    out = summarise(runs)
    for metric in METRICS:
        assert set(out[metric]) == {"synthea", "eicu-demo"}


# ------------------------------------------------------- against the bundle


def test_the_shipped_summary_regenerates_from_the_shipped_runs() -> None:
    """The whole point: the paper's intervals must come out of the bundle.

    Skipped rather than failed where a run file is absent, so a partial checkout
    does not look like a defect.
    """
    for _, relative in DEFAULT_SOURCES:
        if not (ROOT / relative).is_file():
            pytest.skip(f"{relative} not present in this checkout")

    document = replication_summary()
    assert document["seeds"] == [42, 43, 44, 45]
    assert document["n_runs"] == 4
    assert document["confined_system"] == CONFINED_SYSTEM
    # The contract the four runs share travels with the summary.
    assert document["contract"]["allocation"] == "stratified"
    assert document["contract"]["injection_rate"] == 0.30
    for metric in METRICS:
        for cell in document[metric].values():
            assert len(cell["runs_pp"]) == 4
            assert cell["ci95_lo"] <= cell["mean_pp"] <= cell["ci95_hi"]


def test_the_shipped_summary_file_matches_what_the_module_computes() -> None:
    """The artefact on disk must be the artefact the code produces.

    This is the check that would have caught the stale result files: an artefact
    nothing regenerates drifts from the code silently.
    """
    path = ROOT / "results" / "replication_summary.json"
    for _, relative in DEFAULT_SOURCES:
        if not (ROOT / relative).is_file():
            pytest.skip(f"{relative} not present in this checkout")
    if not path.is_file():
        pytest.skip("no shipped replication summary")
    assert json.loads(path.read_text()) == replication_summary(), (
        "results/replication_summary.json differs from what `python -m "
        "dqa.replicate` computes from the shipped run files. Regenerate it with "
        "`make replicate`."
    )
