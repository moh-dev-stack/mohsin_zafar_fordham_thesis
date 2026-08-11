"""Stratified injection: the allocator must not waste budget.

The legacy allocator at :func:`inject_random` chooses a dimension before
looking at the resource, so an injector that cannot apply spends a slot on a
clean control. Measured on the shipped cohorts at seed 42 and rate 0.10 that
costs 9 of every 20 selections, and it starves consistency completely, because
consistency needs an Encounter and Encounters are a small minority of every
cohort.

These tests pin the two properties that matter: every planned defect is
realised, and every dimension receives defects.
"""

from __future__ import annotations

import collections
import random

import pytest

from dqa.inject import (
    DEFAULT_WEIGHTS,
    ELIGIBILITY,
    eligible_indices,
    inject_planned,
    inject_random,
    stratified_plan,
)
from dqa.run import DATASETS, read_cohort

SEED = 42
LIMIT = 200


def _cohorts():
    for name, directory in DATASETS.items():
        if directory.is_dir():
            yield name, read_cohort(directory, LIMIT)


@pytest.mark.parametrize("rate", [0.10, 0.30])
def test_every_planned_defect_is_realised(rate: float) -> None:
    """The property the legacy allocator lacks: no silent loss."""
    for name, cohort in _cohorts():
        rng = random.Random(SEED)
        plan = stratified_plan(cohort, rng, rate)
        lost = 0
        for resource, dimension in zip(cohort, plan, strict=True):
            if dimension is None:
                continue
            _, label = inject_planned(resource, dimension, rng)
            if not label.is_defect or label.dimension != dimension:
                lost += 1
        assert lost == 0, f"{name}: {lost} planned defects were not realised"


def test_legacy_allocator_loses_budget_and_starves_consistency() -> None:
    """Pins the defect being fixed, so the comparison stays evidenced.

    If this test ever fails it means the legacy path changed, which would
    invalidate the published results rather than improve them.
    """
    for name, cohort in _cohorts():
        rng = random.Random(SEED)
        counts: collections.Counter[str] = collections.Counter()
        for resource in cohort:
            if rng.random() >= 0.10:
                continue
            _, label = inject_random(resource, rng)
            counts[label.dimension if label.is_defect else "LOST"] += 1
        assert counts["LOST"] == 9, f"{name}: expected 9 wasted selections"
        assert counts["plausibility"] == 2, f"{name}: expected the n=2 endpoint"
        assert counts["consistency"] == 0, f"{name}: expected consistency starved"


@pytest.mark.parametrize("rate", [0.10, 0.30])
def test_every_dimension_receives_defects(rate: float) -> None:
    for name, cohort in _cohorts():
        rng = random.Random(SEED)
        plan = stratified_plan(cohort, rng, rate)
        counts = collections.Counter(d for d in plan if d is not None)
        for dimension in ELIGIBILITY:
            assert counts[dimension] > 0, f"{name} at rate {rate}: {dimension} starved"


def test_plausibility_is_the_largest_share() -> None:
    """It carries the primary endpoint, so it gets the biggest allocation."""
    assert max(DEFAULT_WEIGHTS, key=lambda d: DEFAULT_WEIGHTS[d]) == "plausibility"
    for name, cohort in _cohorts():
        rng = random.Random(SEED)
        counts = collections.Counter(
            d for d in stratified_plan(cohort, rng, 0.30) if d is not None
        )
        assert counts["plausibility"] >= 25, f"{name}: only {counts['plausibility']}"


def test_stratified_beats_legacy_on_the_primary_endpoint() -> None:
    """The headline improvement, at identical rate and seed."""
    for name, cohort in _cohorts():
        rng = random.Random(SEED)
        legacy = collections.Counter()
        for resource in cohort:
            if rng.random() >= 0.10:
                continue
            _, label = inject_random(resource, rng)
            if label.is_defect:
                legacy[label.dimension] += 1

        rng = random.Random(SEED)
        planned = collections.Counter(
            d for d in stratified_plan(cohort, rng, 0.10) if d is not None
        )
        assert planned["plausibility"] > legacy["plausibility"], name


def test_plan_is_deterministic_given_the_seed() -> None:
    for _, cohort in _cohorts():
        first = stratified_plan(cohort, random.Random(SEED), 0.30)
        second = stratified_plan(cohort, random.Random(SEED), 0.30)
        assert first == second
        other = stratified_plan(cohort, random.Random(SEED + 1), 0.30)
        assert other != first


def test_plan_never_exceeds_eligibility() -> None:
    """A planned position is always eligible for the dimension planned there."""
    for name, cohort in _cohorts():
        plan = stratified_plan(cohort, random.Random(SEED), 0.30)
        eligible = eligible_indices(cohort)
        for index, dimension in enumerate(plan):
            if dimension is None:
                continue
            assert index in eligible[dimension], (
                f"{name}: position {index} planned as {dimension} but is not eligible"
            )


def test_budget_matches_the_requested_rate_where_the_cohort_allows() -> None:
    for name, cohort in _cohorts():
        plan = stratified_plan(cohort, random.Random(SEED), 0.30)
        realised = sum(1 for d in plan if d is not None)
        assert realised == round(len(cohort) * 0.30), name
