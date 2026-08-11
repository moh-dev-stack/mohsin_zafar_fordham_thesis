"""The controlled comparison, pinned.

This is the paper's headline result: with the same range-based plausibility
checker on both arms, differing only in field visibility, the confined system
equals the full-access baseline exactly.

The module was shipped without tests, which mattered more than it looks. The
comparison it computes is the one number the paper's central claim rests on,
and until this file existed nothing would have caught a change to it. These
tests are deliberately cheap: the whole path is offline, rule-based and
deterministic, so there is no reason not to pin it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dqa.controlled import (
    controlled_comparison,
    inject_cohort,
    score_full_access,
    score_projected,
)
from dqa.manifests import WIDTHS, load_width
from dqa.ranges import default_rules_path, load_ranges
from dqa.run import DATASETS, read_cohort

ROOT = Path(__file__).resolve().parents[1]
SEEDS = (42, 43, 44, 45)
RATE = 0.30
LIMIT = 200


@pytest.fixture(scope="module")
def rules():
    return load_ranges(default_rules_path(ROOT))


@pytest.fixture(scope="module")
def manifests():
    return {w: load_width(ROOT / "manifests", w) for w in WIDTHS}


def _cohorts():
    for name, directory in DATASETS.items():
        if directory.is_dir():
            yield name, read_cohort(directory, LIMIT)


def test_every_width_equals_full_access(rules, manifests) -> None:
    """The headline. Visibility does not change the score, anywhere."""
    for seed in SEEDS:
        for name, cohort in _cohorts():
            prepared = inject_cohort(cohort, seed, RATE)
            full = score_full_access(prepared, rules)
            for width in WIDTHS:
                projected = score_projected(prepared, manifests[width], rules)
                assert projected == pytest.approx(full, abs=1e-12), (
                    f"{name} seed {seed} width {width}: projected {projected} "
                    f"against full access {full}"
                )


def test_reported_gap_is_zero_across_all_cells(rules, manifests) -> None:
    out = controlled_comparison(seeds=SEEDS, rate=RATE, limit=LIMIT)
    cells = out["cells"] if isinstance(out, dict) else out
    assert len(cells) == 12, f"expected 12 cells, got {len(cells)}"
    worst = max(c["max_abs_gap_pp"] for c in cells)
    assert worst == pytest.approx(0.0, abs=1e-9), f"max absolute gap {worst} pp, expected 0"


def test_matches_the_shipped_artefact() -> None:
    """Guards against the code and the published JSON drifting apart."""
    shipped = ROOT / "results" / "controlled_comparison.json"
    if not shipped.is_file():
        pytest.skip("shipped artefact not present")
    published = json.loads(shipped.read_text())
    recomputed = controlled_comparison(seeds=SEEDS, rate=RATE, limit=LIMIT)
    pub_cells = {(c["seed"], c["dataset"]): c for c in published["cells"]}
    new_cells = recomputed["cells"] if isinstance(recomputed, dict) else recomputed
    assert len(new_cells) == len(pub_cells)
    for cell in new_cells:
        old = pub_cells[(cell["seed"], cell["dataset"])]
        for width in WIDTHS:
            key = f"confined_{width}"
            assert cell[key] == pytest.approx(old[key], abs=1e-9), (
                f"{cell['dataset']} seed {cell['seed']} {width} drifted"
            )
        assert cell["baseline_ranges"] == pytest.approx(old["baseline_ranges"], abs=1e-9)


def test_the_endpoint_carries_enough_defects(rules) -> None:
    """A zero gap over n = 2 would mean nothing. Pin the realised n."""
    out = controlled_comparison(seeds=(42,), rate=RATE, limit=LIMIT)
    cells = out["cells"] if isinstance(out, dict) else out
    for cell in cells:
        assert cell["n_plausibility"] >= 25, (
            f"{cell['dataset']}: only {cell['n_plausibility']} plausibility defects"
        )


def test_range_checker_is_not_trivially_permissive(rules, manifests) -> None:
    """A checker that flags nothing would also show a zero gap.

    So confirm the arms actually detect: the full-access score must be high,
    not merely equal to the projected one.
    """
    for name, cohort in _cohorts():
        prepared = inject_cohort(cohort, 42, RATE)
        full = score_full_access(prepared, rules)
        assert full > 0.9, f"{name}: full-access F1 {full}, checker is not detecting"


def test_result_document_carries_a_contract() -> None:
    """The headline artefact must record the inputs that produced it.

    Without this, editing a manifest or the range file would move the paper's
    central number with nothing in the output to show the inputs had changed.
    That is exactly the failure the run contract in ``dqa.run`` exists to stop,
    and the controlled comparison shipped without one.
    """
    out = controlled_comparison(seeds=(42,), rate=RATE, limit=LIMIT)
    for key in (
        "seeds", "rate", "limit", "resource_types", "allocation",
        "widths", "manifests_hash", "ranges_hash", "primary_endpoint",
    ):
        assert key in out, f"contract is missing {key}"
    assert set(out["manifests_hash"]) == set(WIDTHS)
    assert len(out["ranges_hash"]) == 64, "ranges_hash should be a SHA-256 hex digest"
    assert out["primary_endpoint"] == "f1_plausibility"


def test_a_trivial_lookup_also_scores_perfectly() -> None:
    """Pins the paper's own honesty caveat.

    The paper states that a four-element set-membership test with no clinical
    knowledge scores F1 = 1.0000 on all three cohorts, which is why the zero
    gap is a weak statement about detection ability. That claim is load-bearing
    for the paper's credibility and nothing asserted it until now.
    """
    from dqa.inject import IMPLAUSIBLE_VALUES
    from dqa.metrics import f1

    sentinels = set(IMPLAUSIBLE_VALUES)

    def trivial(resource):
        quantity = resource.get("valueQuantity")
        return isinstance(quantity, dict) and quantity.get("value") in sentinels

    for name, cohort in _cohorts():
        prepared = inject_cohort(cohort, 42, RATE)
        rows = [
            _row_for(resource, true_dimension, trivial(resource))
            for resource, true_dimension in prepared
        ]
        score = f1(rows, "plausibility")
        assert score == pytest.approx(1.0, abs=1e-9), (
            f"{name}: trivial lookup scored {score}, expected 1.0. "
            "If this fails the paper's caveat needs restating."
        )


def _row_for(resource, true_dimension, failed):
    from dqa.metrics import Row

    return Row(
        str(resource.get("id", "")),
        true_dimension,
        frozenset(["plausibility"]) if failed else frozenset(),
    )


# ------------------------------------------- the identity claim, pinned as a test


def test_both_arms_consume_byte_identical_inputs_on_every_real_resource() -> None:
    """The paper's load-bearing claim, recomputed rather than asserted.

    Section 4.3 states that the 0.0000 pp gap is a mathematical identity: both
    arms read the same three JSONPaths, every manifest width releases all three,
    so the two checkers are the same pure function of the same inputs and no
    seed, cohort or rate could have produced a different answer.

    That claim had no test. It does now. If a manifest is ever narrowed so that
    it stops releasing a consumed field, or if either extractor changes what it
    reads, this fails and the headline stops being an identity.

    The expected counts are written out literally so that a bug which silently
    stopped examining resources cannot make the test pass vacuously.
    """
    from dqa.manifests import WIDTHS, load_width, project
    from dqa.ranges import (
        inputs_verdict,
        projected_range_inputs,
        record_range_inputs,
    )
    from dqa.run import DATASETS, MANIFESTS_DIR, load_default_ranges, read_cohort

    rules = load_default_ranges()
    manifest_sets = {w: load_width(MANIFESTS_DIR, w) for w in WIDTHS}

    examined = 0
    input_mismatches = 0
    verdict_mismatches = 0
    not_applicable = {w: 0 for w in WIDTHS}

    for dataset_dir in DATASETS.values():
        if not dataset_dir.is_dir():
            continue
        for resource in read_cohort(dataset_dir, 200):
            examined += 1
            raw = record_range_inputs(resource)
            raw_verdict = inputs_verdict(raw, rules)[0]
            for width in WIDTHS:
                sliced = project(resource, manifest_sets[width]["plausibility"])
                projected = (
                    projected_range_inputs(sliced.get("_projected_fields", {}))
                    if sliced is not None
                    else None
                )
                if raw != projected:
                    input_mismatches += 1
                if raw_verdict != inputs_verdict(projected, rules)[0]:
                    verdict_mismatches += 1
                if projected is None:
                    not_applicable[width] += 1

    assert examined == 600, f"expected 600 resources, examined {examined}"
    assert input_mismatches == 0, (
        f"{input_mismatches} resources where the full-access arm and a projected "
        f"arm consumed different inputs; the controlled comparison is no longer "
        f"an identity and Section 4.3 is wrong"
    )
    assert verdict_mismatches == 0
    # 277 resources yield no numeric value to judge, identically on both sides.
    assert set(not_applicable.values()) == {277}, not_applicable


def test_the_identity_survives_injection_at_every_seed() -> None:
    """The same equality, on the records that are actually scored.

    Injection rewrites values, so the clean-cohort check above is necessary but
    not sufficient. This runs the comparison over every injected cell the
    headline is computed from.
    """
    from dqa.controlled import RATE, SEEDS, inject_cohort
    from dqa.manifests import WIDTHS, load_width, project
    from dqa.ranges import projected_range_inputs, record_range_inputs
    from dqa.run import DATASETS, MANIFESTS_DIR, read_cohort

    manifest_sets = {w: load_width(MANIFESTS_DIR, w) for w in WIDTHS}
    examined = 0
    mismatches = 0

    for seed in SEEDS:
        for dataset_dir in DATASETS.values():
            if not dataset_dir.is_dir():
                continue
            prepared = inject_cohort(read_cohort(dataset_dir, 200), seed, RATE)
            for resource, _true_dimension in prepared:
                examined += 1
                raw = record_range_inputs(resource)
                for width in WIDTHS:
                    sliced = project(resource, manifest_sets[width]["plausibility"])
                    projected = (
                        projected_range_inputs(sliced.get("_projected_fields", {}))
                        if sliced is not None
                        else None
                    )
                    if raw != projected:
                        mismatches += 1

    assert examined == 2400, f"expected 2400 injected resources, examined {examined}"
    assert mismatches == 0, f"{mismatches} input mismatches on injected records"
