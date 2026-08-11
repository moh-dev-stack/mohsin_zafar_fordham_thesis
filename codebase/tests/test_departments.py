"""The department registry, and its known divergence from the shipped manifests.

The registry in :mod:`dqa.departments` is a design for a tiered system. The
manifests under ``manifests/<width>/`` are the flat four-agent system that is
actually evaluated. The two do not agree, the registry is wired into no
pipeline, and so it affects no published number.

That divergence is pinned here rather than left implicit, because the moment
somebody wires the registry into a pipeline every mismatch below becomes a
routing bug: a resource whose (type, dimension) pair the registry does not claim
would silently never be checked.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dqa.departments import (
    DEPARTMENT_IDS,
    DEPARTMENTS,
    UNROUTED,
    department,
    route,
)
from dqa.manifests import DIMENSIONS, load_width

MANIFESTS_DIR = Path(__file__).resolve().parents[1] / "manifests"


def registry_pairs() -> set[tuple[str, str]]:
    """Every (resource_type, dimension) the registry claims."""
    return {
        (resource_type, dimension)
        for dept in DEPARTMENTS
        for resource_type in dept.resource_types
        for dimension in dept.dimensions
    }


def manifest_pairs(width: str = "minimal") -> set[tuple[str, str]]:
    """Every (resource_type, dimension) the shipped manifests declare."""
    manifest_set = load_width(MANIFESTS_DIR, width)
    return {
        (policy.fhir_resource_type, dimension)
        for dimension in DIMENSIONS
        for policy in manifest_set[dimension].resource_types
    }


# ------------------------------------------------- the registry is well formed


def test_no_pair_is_claimed_by_two_departments() -> None:
    """Routing must be a function.

    This is the defect fixed on 2026-08-06: D2 and D3 both claimed
    ``consistency`` on Observation and Encounter. D2 holds identifiers and D3
    does not, so the ambiguity decided whether a check might resolve a raw
    reference. Expected counts are written out literally rather than derived
    from the registry, so shrinking a department cannot make this test vacuous.
    """
    pairs: list[tuple[str, str]] = [
        (resource_type, dimension)
        for dept in DEPARTMENTS
        for resource_type in dept.resource_types
        for dimension in dept.dimensions
    ]
    assert len(pairs) == len(set(pairs)), (
        f"duplicate (resource_type, dimension) assignments: "
        f"{sorted({p for p in pairs if pairs.count(p) > 1})}"
    )
    # D1 Patient x {completeness, consistency} = 2; D2 contributes none;
    # D3 {Observation, Encounter} x four dimensions = 8.
    assert len(pairs) == 10


def test_d2_is_the_resolution_tier_and_checks_nothing() -> None:
    """D2 issues surrogates. It must not also be a checking department."""
    d2 = department("D2")
    assert d2.dimensions == ()
    assert d2.holds_identifiers is True
    assert d2.resource_types == ("Observation", "Encounter")


def test_only_identifier_holding_tiers_hold_identifiers() -> None:
    """D3 does the clinical work and must never be permitted a raw reference."""
    assert department("D3").holds_identifiers is False
    assert department("D0").holds_identifiers is False
    assert {d.id for d in DEPARTMENTS if d.holds_identifiers} == {"D1", "D2"}


def test_every_declared_dimension_is_a_real_dimension() -> None:
    for dept in DEPARTMENTS:
        for dimension in dept.dimensions:
            assert dimension in DIMENSIONS, f"{dept.id} declares {dimension!r}"


@pytest.mark.parametrize(
    ("resource", "expected"),
    [
        ({"resourceType": "Patient"}, "D1"),
        ({"resourceType": "Observation"}, "D2"),
        ({"resourceType": "Encounter"}, "D2"),
        ({"resourceType": "Medication"}, UNROUTED),
        ({"resourceType": 7}, UNROUTED),
        ({}, UNROUTED),
        ("not a dict", UNROUTED),
    ],
)
def test_routing_is_total(resource: object, expected: str) -> None:
    assert route(resource) in DEPARTMENT_IDS
    assert route(resource) == expected


# --------------------------------- the divergence from the shipped manifests


def test_registry_and_manifests_disagree_exactly_here_KNOWN() -> None:
    """KNOWN DIVERGENCE, pinned so it cannot change unnoticed.

    Neither set is wrong on its own: the registry describes a tiered design that
    was never built, the manifests describe the flat system that was evaluated.
    What matters is that the difference is explicit, because wiring the registry
    in without reconciling it would silently drop checks.

    Both directions are asserted literally. If either list changes, reconcile
    the registry with the manifests deliberately and update this test.
    """
    registry, manifests = registry_pairs(), manifest_pairs()

    assert sorted(registry - manifests) == [
        # D3 claims all four dimensions on both event types, but the shipped
        # plausibility manifest covers Observation and Patient (not Encounter),
        # and the shipped consistency manifest covers Encounter (not Observation).
        ("Encounter", "plausibility"),
        ("Observation", "consistency"),
        # D1 claims completeness and consistency on Patient; the shipped
        # manifests give Patient only plausibility.
        ("Patient", "completeness"),
        ("Patient", "consistency"),
    ], "registry claims a check the shipped manifests do not declare"

    assert sorted(manifests - registry) == [
        ("Condition", "consistency"),
        ("MedicationStatement", "consistency"),
        ("Patient", "plausibility"),
        ("Procedure", "consistency"),
    ], "shipped manifests declare a check no department would receive"


def test_the_registry_covers_every_admitted_event_check() -> None:
    """The one guarantee that must hold today.

    Whatever else diverges, every (resource_type, dimension) the harness
    actually evaluates on an ADMITTED event resource must be claimed by some
    department, or wiring the registry in would drop a scored check.
    """
    from dqa.run import ADMITTED_RESOURCE_TYPES

    registry = registry_pairs()
    dropped = {
        (resource_type, dimension)
        for resource_type, dimension in manifest_pairs()
        if resource_type in ADMITTED_RESOURCE_TYPES
        and resource_type != "Patient"
        and (resource_type, dimension) not in registry
    }
    assert dropped == set(), f"admitted event checks no department claims: {sorted(dropped)}"
