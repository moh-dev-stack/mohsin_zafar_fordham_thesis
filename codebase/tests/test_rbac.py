"""Tests for dqa.rbac against the repository's manifests/roles.yaml."""

from __future__ import annotations

from pathlib import Path

import pytest

from dqa.rbac import AccessDenied, authorise, load_rbac

ROLES_PATH = Path(__file__).resolve().parents[1] / "manifests" / "roles.yaml"
DIMENSIONS = ["completeness", "plausibility", "consistency", "timeliness"]

PATIENT = {"resourceType": "Patient", "id": "p1"}
OBSERVATION = {"resourceType": "Observation", "id": "o1"}
ENCOUNTER = {"resourceType": "Encounter", "id": "e1"}


@pytest.fixture(scope="module")
def policy():
    return load_rbac(ROLES_PATH)


def test_policy_file_defines_exactly_three_roles(policy) -> None:
    assert set(policy.by_role) == {"dqa_operator", "registration_clerk", "lab_tech"}


def test_lab_tech_denied_on_patient(policy) -> None:
    with pytest.raises(AccessDenied):
        authorise(policy, "lab_tech", PATIENT, DIMENSIONS)


def test_lab_tech_permitted_on_observation(policy) -> None:
    assert authorise(policy, "lab_tech", OBSERVATION, DIMENSIONS) == DIMENSIONS


def test_dqa_operator_permitted_on_all_three_resource_types(policy) -> None:
    for resource in (PATIENT, OBSERVATION, ENCOUNTER):
        assert authorise(policy, "dqa_operator", resource, DIMENSIONS) == DIMENSIONS


def test_registration_clerk_scoped_to_patient(policy) -> None:
    assert authorise(policy, "registration_clerk", PATIENT, DIMENSIONS) == DIMENSIONS
    for resource in (OBSERVATION, ENCOUNTER):
        with pytest.raises(AccessDenied):
            authorise(policy, "registration_clerk", resource, DIMENSIONS)


def test_unknown_role_is_denied(policy) -> None:
    with pytest.raises(AccessDenied):
        authorise(policy, "intern", PATIENT, DIMENSIONS)


def test_requested_dimensions_are_intersected_not_expanded(policy) -> None:
    assert authorise(policy, "lab_tech", OBSERVATION, ["completeness", "bogus"]) == [
        "completeness"
    ]
