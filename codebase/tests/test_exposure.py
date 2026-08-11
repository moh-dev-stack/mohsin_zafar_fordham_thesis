"""Pins the structural facts about the pooled AgentLeak metric.

They are claims the paper makes in prose; these tests make them
reproducible from the shipped code and data.

**Fact 1 - the pooled number is a cohort composition statistic.**
Decomposed by ``resourceType``, exposure is flat. Patient records score
0.00 (the manifests release no Safe Harbor path from them at minimal or
intermediate width), Observation records score 1.00 everywhere, and
Encounter records score a per-dataset constant. For mimic-iv-demo and
eicu-demo not one per-type mean moves across the three manifest widths,
yet those two datasets report the identical pooled 0.6700 while synthea
reports 0.6125 - a difference produced entirely by how many Patients,
Encounters and Observations :func:`dqa.run.read_cohort` admitted, not by
anything the manifests do.

**Fact 2 - the clamp discards multiplicity.**
``LeakRecord.value`` is ``min(1.0, exposed / total)`` at
``metrics.py:119``. Every agent whose manifest covers the resource type
receives its own slice, so a released Safe Harbor path is counted once
per slice in the numerator against once per resource in the denominator.
The ratio saturates on the majority of scoreable records (55.5% of
synthea, 67.0% of mimic and eicu), and above the ceiling the clamp
erases the difference: synthea Observations sit at an uncorrected 1.5000
at intermediate and full width, reported as 1.0000.

**Fact 3 - ``at_ceiling`` counts ties, so it cannot evidence Fact 2.**
``at_ceiling`` counts ``exposed >= total``, and equality is the case
where the clamp discarded nothing at all. The consequence is measurable:
``at_ceiling`` is 111 / 134 / 134 at *every* manifest width, so it cannot
distinguish a width where the clamp is inert from one where it is
destroying measurement. ``at_ceiling_strict``, which counts ``exposed >
total`` only, is 0 at minimal width on all three cohorts and 111 / 40 /
127 at intermediate and full. Only the strict count supports a claim
about clamp loss.

**Fact 4 - the clamp is not what made ``union_mean`` redundant.**
``union_mean`` equals ``clamped_mean`` in all 27 cells, and the natural
explanation - that the union ratio is itself wrapped in ``min(1.0, ...)``
- is wrong. ``union_unclamped_mean`` removes that ceiling and is equal to
``union_mean`` in all 27 cells too, because over all 1,800 scored records
the union numerator never exceeds the denominator. What the union
numerator does carry is ``multiplicity_inflation``, the gap between
counting a released path once per envelope and once overall. Unlike every
other column here it moves with manifest width: 0.0000 at minimal on all
three cohorts, 0.2775 / 0.1000 / 0.3175 at intermediate and full.

Expected values are measured from the shipped code. They are pins, not
targets: if one fails, the run changed, and the failure is the finding.
"""

from __future__ import annotations

import random

import pytest

from dqa.agents import Orchestrator
from dqa.exposure import (
    ExposureStats,
    exposure_by_type,
    exposure_pooled,
    phi_union,
    released_slices,
)
from dqa.inject import inject_planned, stratified_plan
from dqa.manifests import WIDTHS, load_width
from dqa.metrics import leak_mean, leak_record, phi_exposed, phi_total
from dqa.run import (
    DATASETS,
    EXPOSURE_BASES,
    EXPOSURE_BASIS_DEFAULT,
    MANIFESTS_DIR,
    read_cohort,
    run_cell_leaks,
)

COHORT_LIMIT = 200

# The configuration the headline runs use, so the numerator defect is measured
# against the injection the published numbers were computed under.
INJECTION_SEED = 42
INJECTION_RATE = 0.30

# (dataset, width) -> (clean pooled mean, published basis, consistent basis).
# Measured from the shipped code at stratified allocation, rate 0.30, seed 42.
#
#   clean       no injection at all: the decomposition in this module.
#   published   run.py's default: numerator from the slices released for the
#               INJECTED resource, denominator from the CLEAN one.
#   consistent  numerator and denominator both from the injected resource,
#               i.e. run.py under --exposure-basis consistent.
#
# The three columns are three different quantities and none of them is
# redundant. published != consistent is the numerator/denominator defect;
# consistent != clean is injection genuinely removing Safe Harbor content from
# a resource, which is not a defect at all.
EXPECTED_CLEAN_PUBLISHED_CONSISTENT: dict[tuple[str, str], tuple[float, float, float]] = {
    ("synthea", "minimal"): (0.6125, 0.6100, 0.6125),
    ("synthea", "intermediate"): (0.6125, 0.6125, 0.6125),
    ("synthea", "full"): (0.6500, 0.6500, 0.6500),
    ("mimic-iv-demo", "minimal"): (0.6700, 0.6525, 0.6650),
    ("mimic-iv-demo", "intermediate"): (0.6700, 0.6550, 0.6650),
    ("mimic-iv-demo", "full"): (0.6700, 0.6550, 0.6650),
    ("eicu-demo", "minimal"): (0.6700, 0.6600, 0.6700),
    ("eicu-demo", "intermediate"): (0.6700, 0.6700, 0.6700),
    ("eicu-demo", "full"): (0.6700, 0.6700, 0.6700),
}

# The same three columns under the configuration the PUBLISHED artefacts were
# computed with: legacy allocation, rate 0.10, seed 42. Driven end to end
# through ``dqa.run.run_cell_leaks``, so this is the harness's own output and
# not a reconstruction of it.
#
# Read the synthea rows carefully. published == consistent at every width, so
# the numerator/denominator defect contributes EXACTLY 0.0000 on synthea. An
# earlier edition of the README claimed this defect explained the synthea
# 0.6125-vs-0.6100 gap. It does not: that gap is injection mutating the
# resource, which moves the numerator under both bases alike. The defect bites
# only on MIMIC-IV and eICU at minimal width, and is worth -0.0025 when it does.
LEGACY_RATE = 0.10
EXPECTED_LEGACY_BASES: dict[tuple[str, str], tuple[float, float, float]] = {
    ("synthea", "minimal"): (0.6125, 0.6100, 0.6100),
    ("synthea", "intermediate"): (0.6125, 0.6100, 0.6100),
    ("synthea", "full"): (0.6500, 0.6475, 0.6475),
    ("mimic-iv-demo", "minimal"): (0.6700, 0.6675, 0.6700),
    ("mimic-iv-demo", "intermediate"): (0.6700, 0.6700, 0.6700),
    ("mimic-iv-demo", "full"): (0.6700, 0.6700, 0.6700),
    ("eicu-demo", "minimal"): (0.6700, 0.6675, 0.6700),
    ("eicu-demo", "intermediate"): (0.6700, 0.6700, 0.6700),
    ("eicu-demo", "full"): (0.6700, 0.6700, 0.6700),
}

# (dataset, width) -> {resourceType or "POOLED": (n, clamped_mean)}
EXPECTED: dict[tuple[str, str], dict[str, tuple[int, float]]] = {}

for _width in WIDTHS:
    EXPECTED[("mimic-iv-demo", _width)] = {
        "Patient": (66, 0.0000),
        "Encounter": (27, 1.0000),
        "Observation": (107, 1.0000),
        "POOLED": (200, 0.6700),
    }
    EXPECTED[("eicu-demo", _width)] = {
        "Patient": (66, 0.0000),
        "Encounter": (7, 1.0000),
        "Observation": (127, 1.0000),
        "POOLED": (200, 0.6700),
    }

for _width in ("minimal", "intermediate"):
    EXPECTED[("synthea", _width)] = {
        "Patient": (66, 0.0000),
        "Encounter": (23, 0.5000),
        "Observation": (111, 1.0000),
        "POOLED": (200, 0.6125),
    }
EXPECTED[("synthea", "full")] = {
    "Patient": (66, 0.1138),
    "Encounter": (23, 0.5000),
    "Observation": (111, 1.0000),
    "POOLED": (200, 0.6500),
}

# Scoreable records sitting at or above the ceiling, per dataset, at
# every width: 55.5% of synthea, 67.0% of mimic and eicu.
EXPECTED_AT_CEILING: dict[str, int] = {
    "synthea": 111,
    "mimic-iv-demo": 134,
    "eicu-demo": 134,
}
EXPECTED_CEILING_FRACTION: dict[str, float] = {
    "synthea": 0.5550,
    "mimic-iv-demo": 0.6700,
    "eicu-demo": 0.6700,
}

# (dataset, width) -> {resourceType or "POOLED": at_ceiling_strict}.
# Records whose UNCLAMPED exposure strictly exceeds 1.0, i.e. where the clamp
# actually discarded a measurement rather than merely tying with the ceiling.
#
# Compare against EXPECTED_AT_CEILING above, which is a per-dataset constant
# because it does not move with width. This table does move with width, which
# is the whole reason it exists: at minimal width the clamp discards nothing
# anywhere, and every one of the 111/134/134 records at_ceiling counts is a tie.
EXPECTED_AT_CEILING_STRICT: dict[tuple[str, str], dict[str, int]] = {
    ("synthea", "minimal"): {"Patient": 0, "Encounter": 0, "Observation": 0, "POOLED": 0},
    ("synthea", "intermediate"): {
        "Patient": 0, "Encounter": 0, "Observation": 111, "POOLED": 111,
    },
    ("synthea", "full"): {"Patient": 0, "Encounter": 0, "Observation": 111, "POOLED": 111},
    ("mimic-iv-demo", "minimal"): {
        "Patient": 0, "Encounter": 0, "Observation": 0, "POOLED": 0,
    },
    ("mimic-iv-demo", "intermediate"): {
        "Patient": 0, "Encounter": 0, "Observation": 40, "POOLED": 40,
    },
    ("mimic-iv-demo", "full"): {
        "Patient": 0, "Encounter": 0, "Observation": 40, "POOLED": 40,
    },
    ("eicu-demo", "minimal"): {"Patient": 0, "Encounter": 0, "Observation": 0, "POOLED": 0},
    ("eicu-demo", "intermediate"): {
        "Patient": 0, "Encounter": 0, "Observation": 127, "POOLED": 127,
    },
    ("eicu-demo", "full"): {
        "Patient": 0, "Encounter": 0, "Observation": 127, "POOLED": 127,
    },
}

# (dataset, width) -> {resourceType or "POOLED": union_mean}.
# Written down independently of EXPECTED above rather than derived from it, so
# that if the union column ever stops coinciding with the clamped one the two
# tables disagree and both tests stay meaningful.
EXPECTED_UNION: dict[tuple[str, str], dict[str, float]] = {
    ("synthea", "minimal"): {
        "Patient": 0.0000, "Encounter": 0.5000, "Observation": 1.0000, "POOLED": 0.6125,
    },
    ("synthea", "intermediate"): {
        "Patient": 0.0000, "Encounter": 0.5000, "Observation": 1.0000, "POOLED": 0.6125,
    },
    ("synthea", "full"): {
        "Patient": 0.1138, "Encounter": 0.5000, "Observation": 1.0000, "POOLED": 0.6500,
    },
    ("mimic-iv-demo", "minimal"): {
        "Patient": 0.0000, "Encounter": 1.0000, "Observation": 1.0000, "POOLED": 0.6700,
    },
    ("mimic-iv-demo", "intermediate"): {
        "Patient": 0.0000, "Encounter": 1.0000, "Observation": 1.0000, "POOLED": 0.6700,
    },
    ("mimic-iv-demo", "full"): {
        "Patient": 0.0000, "Encounter": 1.0000, "Observation": 1.0000, "POOLED": 0.6700,
    },
    ("eicu-demo", "minimal"): {
        "Patient": 0.0000, "Encounter": 1.0000, "Observation": 1.0000, "POOLED": 0.6700,
    },
    ("eicu-demo", "intermediate"): {
        "Patient": 0.0000, "Encounter": 1.0000, "Observation": 1.0000, "POOLED": 0.6700,
    },
    ("eicu-demo", "full"): {
        "Patient": 0.0000, "Encounter": 1.0000, "Observation": 1.0000, "POOLED": 0.6700,
    },
}

# (dataset, width) -> {resourceType or "POOLED": multiplicity_inflation}.
# ``unclamped_mean - union_unclamped_mean``: the share of the raw exposure
# ratio that is one released path counted again in another agent's envelope.
# The only column in this module that responds to manifest width on all three
# cohorts, which is what the module docstring says an exposure metric ought to
# do and what the pooled clamped mean conspicuously does not do.
EXPECTED_MULTIPLICITY: dict[tuple[str, str], dict[str, float]] = {
    ("synthea", "minimal"): {
        "Patient": 0.0000, "Encounter": 0.0000, "Observation": 0.0000, "POOLED": 0.0000,
    },
    ("synthea", "intermediate"): {
        "Patient": 0.0000, "Encounter": 0.0000, "Observation": 0.5000, "POOLED": 0.2775,
    },
    ("synthea", "full"): {
        "Patient": 0.0000, "Encounter": 0.0000, "Observation": 0.5000, "POOLED": 0.2775,
    },
    ("mimic-iv-demo", "minimal"): {
        "Patient": 0.0000, "Encounter": 0.0000, "Observation": 0.0000, "POOLED": 0.0000,
    },
    ("mimic-iv-demo", "intermediate"): {
        "Patient": 0.0000, "Encounter": 0.0000, "Observation": 0.1869, "POOLED": 0.1000,
    },
    ("mimic-iv-demo", "full"): {
        "Patient": 0.0000, "Encounter": 0.0000, "Observation": 0.1869, "POOLED": 0.1000,
    },
    ("eicu-demo", "minimal"): {
        "Patient": 0.0000, "Encounter": 0.0000, "Observation": 0.0000, "POOLED": 0.0000,
    },
    ("eicu-demo", "intermediate"): {
        "Patient": 0.0000, "Encounter": 0.0000, "Observation": 0.5000, "POOLED": 0.3175,
    },
    ("eicu-demo", "full"): {
        "Patient": 0.0000, "Encounter": 0.0000, "Observation": 0.5000, "POOLED": 0.3175,
    },
}

# Cells where the unclamped mean must strictly exceed the clamped mean.
# This is the evidence that the clamp is destroying information rather
# than normalising it.
CLAMP_LOSSY_CELLS = [
    ("synthea", "intermediate"),
    ("synthea", "full"),
    ("eicu-demo", "intermediate"),
]

CELLS = [(dataset, width) for dataset in DATASETS for width in WIDTHS]


def _cell_id(cell: tuple[str, str]) -> str:
    return f"{cell[0]}-{cell[1]}"


@pytest.fixture(scope="module")
def cohorts() -> dict[str, list[dict]]:
    """Every available dataset cohort, read exactly as the harness reads it."""
    out: dict[str, list[dict]] = {}
    for name, dataset_dir in DATASETS.items():
        if dataset_dir.is_dir():
            out[name] = read_cohort(dataset_dir, COHORT_LIMIT)
    return out


@pytest.fixture(scope="module")
def manifest_sets() -> dict[str, dict]:
    return {width: load_width(MANIFESTS_DIR, width) for width in WIDTHS}


def _stats(
    cohorts: dict[str, list[dict]], manifest_sets: dict[str, dict], cell: tuple[str, str]
) -> tuple[dict[str, ExposureStats], ExposureStats]:
    dataset, width = cell
    if dataset not in cohorts:
        pytest.skip(f"{dataset} data directory not present")
    cohort = cohorts[dataset]
    manifest_set = manifest_sets[width]
    return exposure_by_type(cohort, manifest_set), exposure_pooled(cohort, manifest_set)


# ----------------------------------------------------- Fact 1: flat by type


@pytest.mark.parametrize("cell", CELLS, ids=_cell_id)
def test_clamped_mean_by_resource_type(cohorts, manifest_sets, cell) -> None:
    """Per-type and pooled clamped means, pinned to 4 decimal places."""
    by_type, pooled = _stats(cohorts, manifest_sets, cell)
    expected = EXPECTED[cell]

    observed = {rt: (s.n, round(s.clamped_mean, 4)) for rt, s in by_type.items()}
    observed["POOLED"] = (pooled.n, round(pooled.clamped_mean, 4))

    for key, (exp_n, exp_mean) in expected.items():
        assert key in observed, f"{_cell_id(cell)}: no {key} group in cohort"
        got_n, got_mean = observed[key]
        assert got_n == exp_n, f"{_cell_id(cell)} {key}: n {got_n} != expected {exp_n}"
        assert got_mean == pytest.approx(exp_mean, abs=5e-5), (
            f"{_cell_id(cell)} {key}: clamped_mean {got_mean:.4f} "
            f"!= expected {exp_mean:.4f}"
        )

    assert set(observed) == set(expected), (
        f"{_cell_id(cell)}: resource types {sorted(observed)} "
        f"!= expected {sorted(expected)}"
    )


@pytest.mark.parametrize("dataset", ["mimic-iv-demo", "eicu-demo"])
def test_per_type_exposure_does_not_move_with_manifest_width(
    cohorts, manifest_sets, dataset
) -> None:
    """The headline finding, stated as an invariant.

    Widening every manifest from minimal to full changes not one
    per-resource-type clamped mean on these two cohorts. A metric that
    measured the manifests could not be constant under that change.
    """
    if dataset not in cohorts:
        pytest.skip(f"{dataset} data directory not present")
    cohort = cohorts[dataset]
    profiles = {
        width: {
            rt: round(s.clamped_mean, 4)
            for rt, s in exposure_by_type(cohort, manifest_sets[width]).items()
        }
        for width in WIDTHS
    }
    baseline = profiles["minimal"]
    for width in WIDTHS:
        assert profiles[width] == baseline, (
            f"{dataset}: per-type profile at {width} {profiles[width]} "
            f"differs from minimal {baseline}"
        )


def test_pooled_differs_across_datasets_with_identical_per_type_means(
    cohorts, manifest_sets
) -> None:
    """Composition, not policy, is what separates 0.6125 from 0.6700.

    synthea and eicu-demo both score Patient 0.00 and Observation 1.00 at
    minimal width. Their pooled means still differ, because the cohorts
    hold different numbers of each type.
    """
    if "synthea" not in cohorts or "eicu-demo" not in cohorts:
        pytest.skip("both synthea and eicu-demo required")
    manifest_set = manifest_sets["minimal"]

    synthea = exposure_by_type(cohorts["synthea"], manifest_set)
    eicu = exposure_by_type(cohorts["eicu-demo"], manifest_set)
    for rt in ("Patient", "Observation"):
        assert round(synthea[rt].clamped_mean, 4) == round(eicu[rt].clamped_mean, 4)

    # Same per-type score on those types, different Patient/Observation counts,
    # therefore a different pooled number.
    assert synthea["Observation"].n != eicu["Observation"].n
    pooled_synthea = round(exposure_pooled(cohorts["synthea"], manifest_set).clamped_mean, 4)
    pooled_eicu = round(exposure_pooled(cohorts["eicu-demo"], manifest_set).clamped_mean, 4)
    assert pooled_synthea == 0.6125
    assert pooled_eicu == 0.6700


# ---------------------------------------------------- Fact 2: the clamp bites


@pytest.mark.parametrize("cell", CELLS, ids=_cell_id)
def test_at_ceiling_counts(cohorts, manifest_sets, cell) -> None:
    """Records where exposed >= total. Note that this INCLUDES ties."""
    dataset, _ = cell
    _, pooled = _stats(cohorts, manifest_sets, cell)

    assert pooled.at_ceiling == EXPECTED_AT_CEILING[dataset], (
        f"{_cell_id(cell)}: at_ceiling {pooled.at_ceiling} "
        f"!= expected {EXPECTED_AT_CEILING[dataset]}"
    )
    assert pooled.ceiling_fraction == pytest.approx(
        EXPECTED_CEILING_FRACTION[dataset], abs=5e-5
    )


@pytest.mark.parametrize("cell", CELLS, ids=_cell_id)
def test_at_ceiling_strict_counts_only_where_the_clamp_discarded_something(
    cohorts, manifest_sets, cell
) -> None:
    """``at_ceiling`` counts ties; ``at_ceiling_strict`` is the evidential one.

    Expectations are derived twice over, and neither derivation goes through
    ``_summarise``. The literal table is one; recounting the cohort here from
    ``phi_exposed`` and ``phi_total`` directly is the other. A change that made
    ``at_ceiling_strict`` a synonym for ``at_ceiling`` would fail both.
    """
    dataset, width = cell
    by_type, pooled = _stats(cohorts, manifest_sets, cell)
    expected = EXPECTED_AT_CEILING_STRICT[cell]

    observed = {rt: s.at_ceiling_strict for rt, s in by_type.items()}
    observed["POOLED"] = pooled.at_ceiling_strict
    assert observed == expected, (
        f"{_cell_id(cell)}: at_ceiling_strict {observed} != expected {expected}"
    )

    # Independent recount, done here rather than by ``_summarise``: a record
    # counts only if its UNCLAMPED value exceeds 1.0, which is the definition
    # the column is supposed to implement.
    manifest_set = manifest_sets[width]
    recount = 0
    for resource in cohorts[dataset]:
        total = phi_total(resource)
        if total <= 0:
            continue
        if phi_exposed(released_slices(resource, manifest_set)) / total > 1.0:
            recount += 1
    assert pooled.at_ceiling_strict == recount

    # The strict count is a subset of the tie-inclusive one, and on this data a
    # PROPER subset wherever the clamp is inert: that gap is the reason
    # ``at_ceiling`` on its own cannot evidence clamp loss.
    assert pooled.at_ceiling_strict <= pooled.at_ceiling
    assert pooled.strict_ceiling_fraction == pytest.approx(
        pooled.at_ceiling_strict / pooled.n, abs=5e-5
    )


def test_at_ceiling_cannot_distinguish_widths_but_the_strict_count_can(
    cohorts, manifest_sets
) -> None:
    """Why the strict variant had to be added, stated as a contrast.

    ``at_ceiling`` takes the same value at all three manifest widths on all
    three cohorts, so no reader of that column can tell a width where the clamp
    destroys measurement from one where it destroys nothing. The strict count
    separates them: it is 0 everywhere at minimal width and non-zero at
    intermediate and full on every cohort.
    """
    for dataset in DATASETS:
        if dataset not in cohorts:
            continue
        loose = {w: exposure_pooled(cohorts[dataset], manifest_sets[w]).at_ceiling for w in WIDTHS}
        strict = {
            w: exposure_pooled(cohorts[dataset], manifest_sets[w]).at_ceiling_strict
            for w in WIDTHS
        }
        assert len(set(loose.values())) == 1, (
            f"{dataset}: at_ceiling has started moving with width ({loose}); the "
            f"contrast this test documents no longer holds"
        )
        assert strict["minimal"] == 0, (
            f"{dataset}: expected the clamp to discard nothing at minimal width, "
            f"got at_ceiling_strict={strict['minimal']} against at_ceiling="
            f"{loose['minimal']}"
        )
        assert strict["intermediate"] > 0 and strict["full"] > 0, (
            f"{dataset}: strict ceiling counts {strict} show no clamp loss at any "
            f"width, so Fact 2 has no evidence behind it"
        )


@pytest.mark.parametrize("cell", CLAMP_LOSSY_CELLS, ids=_cell_id)
def test_unclamped_strictly_exceeds_clamped(cohorts, manifest_sets, cell) -> None:
    """The clamp is discarding measured exposure, not normalising it."""
    by_type, pooled = _stats(cohorts, manifest_sets, cell)

    print(f"\n[{_cell_id(cell)}] clamp loss")
    for rt in sorted(by_type):
        s = by_type[rt]
        print(
            f"    {rt:<12} n={s.n:>3} clamped={s.clamped_mean:.4f} "
            f"unclamped={s.unclamped_mean:.4f} union={s.union_mean:.4f} "
            f"at_ceiling={s.at_ceiling}"
        )
    print(
        f"    {'POOLED':<12} n={pooled.n:>3} clamped={pooled.clamped_mean:.4f} "
        f"unclamped={pooled.unclamped_mean:.4f} union={pooled.union_mean:.4f} "
        f"at_ceiling={pooled.at_ceiling} loss={pooled.clamp_loss:.4f}"
    )

    assert pooled.unclamped_mean > pooled.clamped_mean, (
        f"{_cell_id(cell)}: unclamped {pooled.unclamped_mean:.4f} does not exceed "
        f"clamped {pooled.clamped_mean:.4f}"
    )
    assert by_type["Observation"].unclamped_mean > by_type["Observation"].clamped_mean


def test_summarise_keeps_the_union_numerator_distinct_from_the_exposed_one() -> None:
    """REWRITTEN: the old test here asserted a tautology of the implementation.

    The previous version asserted ``stats.union_mean == stats.clamped_mean``
    where BOTH sides came from the same ``_stats(...)`` call. On the shipped
    manifests those two expressions coincide, so the assertion held; but it held
    against nothing external. An implementation in which ``union_mean`` were
    literally assigned ``clamped_mean`` -- that is, one in which the union
    numerator had been dropped altogether -- would have passed it in every cell.
    It pinned the coincidence and could not tell the coincidence from a bug.

    This replaces it in the house style of
    ``test_the_ordering_invariants_are_enforced_on_values_that_could_break_them``:
    hand-built ``(exposed, union, total)`` triples, chosen so the three means are
    three DIFFERENT numbers, against expectations computed by hand here rather
    than read back out of the object under test.
    """
    from dqa.exposure import _summarise

    # Record 1: 6 occurrences released across slices, 2 distinct paths, 2 in the
    #           resource. exposed/total = 3.0, union/total = 1.0.
    # Record 2: 3 released, 1 distinct, 4 present. 0.75 and 0.25.
    # Record 3: below the ceiling with no multiplicity at all. 0.5 and 0.5.
    stats = _summarise([(6, 2, 2), (3, 1, 4), (2, 2, 4)])

    assert stats.n == 3
    # clamped: min(1, 3.0), min(1, 0.75), min(1, 0.5)
    assert stats.clamped_mean == pytest.approx((1.0 + 0.75 + 0.5) / 3)
    # unclamped: no ceiling anywhere
    assert stats.unclamped_mean == pytest.approx((3.0 + 0.75 + 0.5) / 3)
    # union, clamped: min(1, 1.0), min(1, 0.25), min(1, 0.5)
    assert stats.union_mean == pytest.approx((1.0 + 0.25 + 0.5) / 3)
    # union, unclamped: identical here, because no union ratio exceeds 1.0
    assert stats.union_unclamped_mean == pytest.approx((1.0 + 0.25 + 0.5) / 3)

    # The point of the rewrite: the union mean is NOT the clamped mean. Record 2
    # is what separates them, and the old assertion would have failed on it,
    # which is exactly why real-cohort data could never have caught the bug.
    assert stats.union_mean != pytest.approx(stats.clamped_mean)
    assert stats.union_mean < stats.clamped_mean

    # multiplicity_inflation: (3.0 - 1.0) + (0.75 - 0.25) + (0.5 - 0.5), over 3.
    assert stats.multiplicity_inflation == pytest.approx((2.0 + 0.5 + 0.0) / 3)
    assert stats.at_ceiling == 1
    assert stats.at_ceiling_strict == 1


def test_summarise_union_clamp_is_load_bearing_when_the_union_exceeds_the_total() -> None:
    """The union clamp is a no-op on shipped data but not dead code.

    ``phi_union`` keys on released JSONPaths while ``phi_total`` keys on the nine
    entries of ``SAFE_HARBOR_JSONPATHS``, so a manifest releasing two distinct
    Safe-Harbor-marked paths falling under one denominator path would push the
    union numerator above the denominator. No shipped manifest does -- which is
    what ``test_the_union_clamp_is_a_no_op_on_the_shipped_manifests`` measures --
    so this case is reachable only by construction. Built here so the two union
    columns are known to differ when they should.
    """
    from dqa.exposure import _summarise

    stats = _summarise([(4, 3, 2)])
    assert stats.union_mean == pytest.approx(1.0)
    assert stats.union_unclamped_mean == pytest.approx(1.5)
    assert stats.union_clamp_loss == pytest.approx(0.5)


@pytest.mark.parametrize("cell", CELLS, ids=_cell_id)
def test_union_mean_matches_an_independently_computed_expectation(
    cohorts, manifest_sets, cell
) -> None:
    """The union means on real cohorts, against values derived without them.

    Two independent expectations, neither of which is another field of the
    object under test:

    1. a literal table, ``EXPECTED_UNION``, written down from a measured run;
    2. the mean recomputed here, resource by resource, from ``phi_union`` and
       ``phi_total`` directly, with the averaging done in this test rather than
       by ``_summarise``.

    Both would fail if ``union_mean`` were quietly aliased to ``clamped_mean``,
    which the test this replaces would not have.
    """
    dataset, width = cell
    by_type, pooled = _stats(cohorts, manifest_sets, cell)
    manifest_set = manifest_sets[width]
    expected = EXPECTED_UNION[cell]

    observed = {rt: round(s.union_mean, 4) for rt, s in by_type.items()}
    observed["POOLED"] = round(pooled.union_mean, 4)
    for key, exp in expected.items():
        assert observed[key] == pytest.approx(exp, abs=5e-5), (
            f"{_cell_id(cell)} {key}: union_mean {observed[key]:.4f} != {exp:.4f}"
        )

    ratios = [
        min(1.0, phi_union(released_slices(r, manifest_set)) / phi_total(r))
        for r in cohorts[dataset]
        if phi_total(r) > 0
    ]
    assert pooled.n == len(ratios)
    assert pooled.union_mean == pytest.approx(sum(ratios) / len(ratios), abs=1e-12)


@pytest.mark.parametrize("cell", CELLS, ids=_cell_id)
def test_the_union_clamp_is_a_no_op_on_the_shipped_manifests(
    cohorts, manifest_sets, cell
) -> None:
    """The clamp was NOT what made ``union_mean`` redundant.

    ``union_mean`` equalling ``clamped_mean`` in all 27 cells invites the
    explanation that the union is collapsing because it is itself wrapped in
    ``min(1.0, ...)``. That explanation is wrong, and this measures why: no
    record in any cohort at any width has ``phi_union > phi_total``, so the
    ceiling never binds and ``union_unclamped_mean`` is identical to
    ``union_mean``. Removing the clamp would therefore have changed nothing.

    If a manifest is ever added that releases two Safe-Harbor-marked paths under
    one denominator path, this fails, and the clamp becomes load-bearing.
    """
    dataset, width = cell
    _, pooled = _stats(cohorts, manifest_sets, cell)
    manifest_set = manifest_sets[width]

    over = [
        (str(r.get("id", "")), phi_union(released_slices(r, manifest_set)), phi_total(r))
        for r in cohorts[dataset]
        if phi_total(r) > 0
        and phi_union(released_slices(r, manifest_set)) > phi_total(r)
    ]
    assert over == [], (
        f"{_cell_id(cell)}: {len(over)} records now have union > total, e.g. "
        f"{over[:3]}; the union clamp has become load-bearing and "
        f"union_unclamped_mean is no longer the same column"
    )
    assert pooled.union_clamp_loss == pytest.approx(0.0, abs=1e-12)
    assert pooled.union_unclamped_mean == pytest.approx(pooled.union_mean, abs=1e-12)


@pytest.mark.parametrize("cell", CELLS, ids=_cell_id)
def test_multiplicity_inflation_is_what_the_union_numerator_actually_buys(
    cohorts, manifest_sets, cell
) -> None:
    """The column that earns the union numerator its place.

    ``union_mean`` coincides with ``clamped_mean`` and unclamping it changes
    nothing, so on its own the union column is dead weight. The difference
    between the raw ratio and the union ratio is not: it is the share of
    measured exposure that is one released path counted again in another
    agent's envelope, and it is the only column in this module that moves with
    manifest width on all three cohorts.

    Pinned against a literal table and recomputed from ``phi_exposed`` and
    ``phi_union`` directly.
    """
    dataset, width = cell
    by_type, pooled = _stats(cohorts, manifest_sets, cell)
    manifest_set = manifest_sets[width]
    expected = EXPECTED_MULTIPLICITY[cell]

    observed = {rt: round(s.multiplicity_inflation, 4) for rt, s in by_type.items()}
    observed["POOLED"] = round(pooled.multiplicity_inflation, 4)
    for key, exp in expected.items():
        assert observed[key] == pytest.approx(exp, abs=5e-5), (
            f"{_cell_id(cell)} {key}: multiplicity_inflation "
            f"{observed[key]:.4f} != {exp:.4f}"
        )

    gaps = []
    for r in cohorts[dataset]:
        total = phi_total(r)
        if total <= 0:
            continue
        messages = released_slices(r, manifest_set)
        gaps.append((phi_exposed(messages) - phi_union(messages)) / total)
    assert pooled.multiplicity_inflation == pytest.approx(sum(gaps) / len(gaps), abs=1e-12)

    # Zero at minimal width everywhere, positive once the manifests widen. That
    # is the responsiveness to manifest width the pooled clamped mean lacks.
    if width == "minimal":
        assert pooled.multiplicity_inflation == pytest.approx(0.0, abs=1e-12)
    else:
        assert pooled.multiplicity_inflation > 0.0


def test_the_ordering_invariants_are_enforced_on_values_that_could_break_them() -> None:
    """The invariants, checked where they can actually fail.

    ``_summarise`` is given hand-built triples rather than cohort data, including
    one where the numerator is triple the denominator. Real cohorts never
    exercise the clamp this hard, so on real data the ordering assertions are
    vacuous; here they are not.
    """
    from dqa.exposure import _summarise

    # (exposed, union, total): heavy multiplicity, then a below-ceiling record,
    # then a record with no Safe Harbor content at all, which must be dropped.
    stats = _summarise([(6, 2, 2), (1, 1, 4), (0, 0, 0)])
    assert stats.n == 2, "records with total == 0 must not be scored"
    assert stats.clamped_mean == pytest.approx((1.0 + 0.25) / 2)
    assert stats.unclamped_mean == pytest.approx((3.0 + 0.25) / 2)
    assert stats.union_mean == pytest.approx((1.0 + 0.25) / 2)
    assert stats.union_unclamped_mean == pytest.approx((1.0 + 0.25) / 2)
    assert stats.unclamped_mean > stats.clamped_mean
    assert stats.clamp_loss == pytest.approx(1.0)
    assert stats.at_ceiling == 1
    assert stats.ceiling_fraction == pytest.approx(0.5)
    # The first record is 3x over the ceiling, the second is below it, so the
    # strict count and the tie-inclusive count coincide here at 1. They are
    # separated in test_at_ceiling_strict_counts_only_where_the_clamp_discarded_something.
    assert stats.at_ceiling_strict == 1
    assert stats.strict_ceiling_fraction == pytest.approx(0.5)
    # (6-2)/2 + (1-1)/4, over 2.
    assert stats.multiplicity_inflation == pytest.approx((2.0 + 0.0) / 2)


def test_summarise_on_an_all_unscoreable_group() -> None:
    from dqa.exposure import _summarise

    stats = _summarise([(0, 0, 0), (3, 3, 0)])
    assert (stats.n, stats.clamped_mean, stats.at_ceiling) == (0, 0.0, 0)
    assert stats.ceiling_fraction == 0.0
    assert (stats.union_unclamped_mean, stats.at_ceiling_strict) == (0.0, 0)
    assert stats.strict_ceiling_fraction == 0.0
    assert stats.multiplicity_inflation == 0.0


# -------------------------------------------- lock to the shipped metric code


@pytest.mark.parametrize("cell", CELLS, ids=_cell_id)
def test_pooled_clamped_mean_equals_what_the_orchestrator_releases(
    cohorts, manifest_sets, cell, monkeypatch
) -> None:
    """``exposure_pooled().clamped_mean`` is the metric the harness reports.

    REWRITTEN in the third review pass. The previous version built its
    comparison input with ``exposure.released_slices`` and then compared the
    result against ``exposure.exposure_pooled`` -- both sides of the assertion
    came from the module under test, so it could only fail if that module
    disagreed with itself. Its docstring claimed it established that the
    decomposition was "a decomposition of the reported metric and not of a
    lookalike", which is precisely what it could not establish.

    The pipeline is ``Orchestrator.evaluate``, which projects through each
    manifest and returns the messages the agents actually receive. That is the
    thing ``run.py`` scores. This version drives it, so a change to projection or
    to the orchestrator's message assembly breaks the decomposition test as it
    should.

    The API key is removed so the LLM agents short-circuit to "uncertain"
    without a network call; only the projected messages matter here.
    """
    dataset, width = cell
    if dataset not in cohorts:
        pytest.skip(f"{dataset} data directory not present")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cohort = cohorts[dataset]
    manifest_set = manifest_sets[width]

    orchestrator = Orchestrator(manifest_set, cache_dir=None)
    records = [leak_record(r, orchestrator.evaluate(r)[1]) for r in cohort]
    pipeline_mean, excluded = leak_mean(records)
    pooled = exposure_pooled(cohort, manifest_set)

    assert round(pooled.clamped_mean, 4) == round(pipeline_mean, 4), (
        f"{_cell_id(cell)}: the decomposition reports "
        f"{pooled.clamped_mean:.4f} but the orchestrator pipeline releases "
        f"{pipeline_mean:.4f}"
    )
    assert pooled.n == len(cohort) - excluded


@pytest.mark.parametrize("cell", CELLS, ids=_cell_id)
def test_released_slices_agrees_with_the_orchestrator(
    cohorts, manifest_sets, cell, monkeypatch
) -> None:
    """``released_slices`` must reproduce the orchestrator's messages exactly.

    This is the assumption the rest of this module rests on, and it is the one
    the old circular test quietly assumed instead of checking. Asserted on the
    messages themselves, not on a number derived from them, so a divergence
    cannot cancel out in the mean.
    """
    dataset, width = cell
    if dataset not in cohorts:
        pytest.skip(f"{dataset} data directory not present")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    manifest_set = manifest_sets[width]
    orchestrator = Orchestrator(manifest_set, cache_dir=None)

    for resource in cohorts[dataset]:
        assert released_slices(resource, manifest_set) == orchestrator.evaluate(resource)[1]


@pytest.mark.parametrize("cell", CELLS, ids=_cell_id)
def test_the_injected_numerator_defect_is_exactly_this_large(
    cohorts, manifest_sets, cell, monkeypatch
) -> None:
    """The known, disclosed defect, measured rather than described.

    Under ``--exposure-basis published``, which is the default, ``run.py`` takes
    the exposure numerator from the slices released for the *injected* copy of a
    resource and the denominator from the *clean* copy, so the reported
    AgentLeak score is not a proportion of either object. Under
    ``--exposure-basis consistent`` both come from the injected copy.

    All three quantities are pinned here -- clean, published, consistent -- so
    the defect is separated from a second effect it is easily confused with.
    ``published != consistent`` is the defect. ``consistent != clean`` is not a
    defect at all: it is injection genuinely deleting Safe Harbor content, and
    it moves the numerator whichever basis is chosen.
    """
    dataset, width = cell
    if dataset not in cohorts:
        pytest.skip(f"{dataset} data directory not present")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cohort = cohorts[dataset]
    manifest_set = manifest_sets[width]
    orchestrator = Orchestrator(manifest_set, cache_dir=None)

    clean_mean, _ = leak_mean([leak_record(r, orchestrator.evaluate(r)[1]) for r in cohort])

    rng = random.Random(INJECTION_SEED)
    plan = stratified_plan(cohort, rng, INJECTION_RATE)
    published_records = []
    consistent_records = []
    for resource, dimension in zip(cohort, plan, strict=True):
        modified = resource if dimension is None else inject_planned(resource, dimension, rng)[0]
        projected = orchestrator.evaluate(modified)[1]
        # Numerator from the injected copy, denominator from the clean one.
        # This is run.py's published behaviour, reproduced deliberately.
        published_records.append(leak_record(resource, projected))
        # Both from the injected copy: the object the agents were shown.
        consistent_records.append(leak_record(modified, projected))
    published_mean, _ = leak_mean(published_records)
    consistent_mean, _ = leak_mean(consistent_records)

    expected_clean, expected_published, expected_consistent = (
        EXPECTED_CLEAN_PUBLISHED_CONSISTENT[cell]
    )
    assert round(clean_mean, 4) == expected_clean
    assert round(published_mean, 4) == expected_published
    assert round(consistent_mean, 4) == expected_consistent
    assert published_mean <= clean_mean + 1e-12, (
        "injection can only remove Safe Harbor content from the numerator, "
        "never add it, so the injected score must not exceed the clean one"
    )
    # The defect can only understate exposure: it keeps a denominator that
    # counts fields the numerator can no longer see.
    assert published_mean <= consistent_mean + 1e-12


@pytest.mark.parametrize("cell", CELLS, ids=_cell_id)
def test_the_two_exposure_bases_through_the_harness_at_the_published_config(
    cohorts, manifest_sets, cell, monkeypatch
) -> None:
    """The defect at the configuration the PUBLISHED artefacts were computed at.

    The test above measures a reconstruction at stratified allocation and rate
    0.30. This one drives ``dqa.run.run_cell_leaks`` itself at legacy
    allocation and rate 0.10, which is what produced the shipped numbers, so it
    pins the harness's own output rather than a model of it.

    The synthea rows are the reason this test exists. published == consistent at
    every width there, so the numerator/denominator defect contributes EXACTLY
    0.0000 on synthea. An earlier edition of the README attributed the synthea
    0.6125-vs-0.6100 gap to this defect; that attribution was false and has been
    withdrawn. The real cause on synthea is injection mutating the resource,
    which shows up as ``consistent != clean``. The defect bites only on MIMIC-IV
    and eICU at minimal width, and is worth -0.0025 when it does.
    """
    dataset, width = cell
    if dataset not in cohorts:
        pytest.skip(f"{dataset} data directory not present")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cohort = cohorts[dataset]
    manifest_set = manifest_sets[width]
    orchestrator = Orchestrator(manifest_set, cache_dir=None)

    _, leaks, _ = run_cell_leaks(
        orchestrator, cohort, LEGACY_RATE, INJECTION_SEED, allocation="legacy"
    )
    assert set(leaks) == set(EXPOSURE_BASES)

    published_mean, _ = leak_mean(leaks["published"])
    consistent_mean, _ = leak_mean(leaks["consistent"])
    clean_mean = exposure_pooled(cohort, manifest_set).clamped_mean

    expected_clean, expected_published, expected_consistent = EXPECTED_LEGACY_BASES[cell]
    assert round(clean_mean, 4) == expected_clean
    assert round(published_mean, 4) == expected_published
    assert round(consistent_mean, 4) == expected_consistent

    if dataset == "synthea":
        assert published_mean == pytest.approx(consistent_mean, abs=1e-12), (
            "the numerator/denominator defect must contribute exactly zero on "
            "synthea; if this fails the withdrawn README claim has come back"
        )


def test_run_cell_defaults_to_the_published_basis_and_rejects_unknown_ones(
    cohorts, manifest_sets, monkeypatch
) -> None:
    """The fix is opt-in, and asking for nonsense is an error rather than a mode.

    Changing the basis MOVES A PUBLISHED NUMBER, so the default must be the
    published behaviour and ``run_cell`` with no basis argument must return
    exactly the records ``run_cell_leaks`` files under ``"published"``.
    """
    from dqa.run import run_cell

    if "mimic-iv-demo" not in cohorts:
        pytest.skip("mimic-iv-demo data directory not present")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert EXPOSURE_BASIS_DEFAULT == "published"

    cohort = cohorts["mimic-iv-demo"]
    manifest_set = manifest_sets["minimal"]

    _, leaks, _ = run_cell_leaks(
        Orchestrator(manifest_set, cache_dir=None),
        cohort,
        LEGACY_RATE,
        INJECTION_SEED,
        allocation="legacy",
    )
    _, defaulted, _ = run_cell(
        Orchestrator(manifest_set, cache_dir=None),
        cohort,
        LEGACY_RATE,
        INJECTION_SEED,
        allocation="legacy",
    )
    assert defaulted == leaks["published"]
    # And the mode that is opt-in really does differ on this cohort.
    assert leak_mean(leaks["published"])[0] != leak_mean(leaks["consistent"])[0]

    with pytest.raises(ValueError, match="unknown exposure basis"):
        run_cell(
            Orchestrator(manifest_set, cache_dir=None),
            cohort,
            LEGACY_RATE,
            INJECTION_SEED,
            allocation="legacy",
            exposure_basis="clean",
        )


def test_the_exposure_basis_is_recorded_in_the_run_contract() -> None:
    """A cell computed on one basis is not comparable with one on the other.

    Same guard as ``test_the_excluded_file_prefixes_are_in_the_run_contract``:
    the value that determines ``agentleak_mean`` has to appear in the contract
    dict, not only in the argument parser.
    """
    source = (MANIFESTS_DIR.parent / "src" / "dqa" / "run.py").read_text()
    assert '"exposure_basis": args.exposure_basis' in source
    assert EXPOSURE_BASES == ("published", "consistent")


def test_ceiling_fraction_does_not_divide_by_zero_on_an_empty_group() -> None:
    """REWRITTEN: the old test asserted that ``@dataclass(frozen=True)`` freezes.

    That checks the standard library, not this module, and the two derived
    properties it also checked were computed on hand-picked values chosen to make
    the arithmetic trivial. The property actually worth defending is that
    ``ceiling_fraction`` guards its own denominator, because ``n == 0`` is
    reachable: every resource type with no scoreable record produces it, and
    ``exposure_by_type`` returns those groups rather than dropping them.
    """
    empty = ExposureStats(n=0, clamped_mean=0.0, unclamped_mean=0.0, union_mean=0.0, at_ceiling=0)
    assert empty.ceiling_fraction == 0.0
    assert empty.clamp_loss == 0.0
    assert empty.strict_ceiling_fraction == 0.0

    populated = ExposureStats(
        n=4,
        clamped_mean=0.5,
        unclamped_mean=1.25,
        union_mean=0.5,
        at_ceiling=3,
        union_unclamped_mean=0.5,
        at_ceiling_strict=2,
    )
    assert populated.ceiling_fraction == pytest.approx(0.75)
    assert populated.clamp_loss == pytest.approx(0.75)
    assert populated.strict_ceiling_fraction == pytest.approx(0.5)
    assert populated.multiplicity_inflation == pytest.approx(0.75)
    assert populated.union_clamp_loss == pytest.approx(0.0)


def test_the_new_stats_fields_default_so_five_argument_call_sites_keep_working() -> None:
    """``union_unclamped_mean`` and ``at_ceiling_strict`` were appended.

    They carry defaults deliberately: ``ExposureStats`` is constructed
    positionally, including outside this package, and appending required fields
    would break every existing five-argument call site at import time. This pins
    that the five-argument form still constructs, so the reason the defaults are
    there is recorded as a test rather than only as a comment.
    """
    legacy = ExposureStats(4, 0.5, 1.25, 0.5, 3)
    assert (legacy.union_unclamped_mean, legacy.at_ceiling_strict) == (0.0, 0)


def test_empty_cohort_is_safe() -> None:
    manifest_set = load_width(MANIFESTS_DIR, "minimal")
    assert exposure_by_type([], manifest_set) == {}
    empty = exposure_pooled([], manifest_set)
    assert (empty.n, empty.clamped_mean, empty.at_ceiling) == (0, 0.0, 0)
