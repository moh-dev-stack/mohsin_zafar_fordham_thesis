"""Tests for the linkage ladder and the department registry.

These pin two things the experiment cannot survive without: that
surrogates are non-derived (a HIPAA Safe Harbor requirement, not a
stylistic preference) and that rewriting a slice actually moves the
exposure metric, which it only does if the linkage *key* disappears.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from dqa.agents import REQUIRED_FIELDS
from dqa.departments import DEPARTMENT_IDS, DEPARTMENTS, Department, department, route
from dqa.linkage import (
    LINKAGE_PATHS,
    SURROGATE_PATHS,
    SurrogateIssuer,
    rewrite_slice,
)
from dqa.manifests import WIDTHS, Manifest, load_width, project
from dqa.metrics import leak_record, phi_exposed
from dqa.run import DATASETS, MANIFESTS_DIR

REFERENCE_A = "Patient/10000032"
REFERENCE_B = "Patient/10001217"


def minimal_manifests() -> dict[str, Manifest]:
    return load_width(MANIFESTS_DIR, "minimal")


def _resources_from(dataset_dir: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for path in sorted(dataset_dir.glob("*.json")):
        if path.name.startswith(("hospital", "practitioner")):
            continue
        try:
            payload = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        entries = payload.get("entry", [payload]) if isinstance(payload, dict) else []
        for entry in entries:
            resource = entry.get("resource", entry) if isinstance(entry, dict) else None
            if isinstance(resource, dict) and resource.get("resourceType"):
                out.append(resource)
    return out


def first_observation() -> dict[str, Any]:
    """A real Observation carrying a subject reference, from MIMIC-IV demo."""
    for resource in _resources_from(DATASETS["mimic-iv-demo"]):
        if resource.get("resourceType") == "Observation" and resource.get("subject", {}).get(
            "reference"
        ):
            return resource
    pytest.skip("no Observation with a subject reference in the MIMIC-IV demo cohort")


# ------------------------------------------------------------------ surrogates


def test_surrogates_are_stable_within_a_run_and_distinct_across_references() -> None:
    issuer = SurrogateIssuer(seed=7)
    first = issuer.surrogate_for(REFERENCE_A)
    assert issuer.surrogate_for(REFERENCE_A) == first
    assert issuer.surrogate_for(REFERENCE_A) == first

    other = issuer.surrogate_for(REFERENCE_B)
    assert other != first
    assert issuer.issued == 2


def test_same_seed_reproduces_mapping_and_different_seeds_do_not() -> None:
    left, right = SurrogateIssuer(seed=42), SurrogateIssuer(seed=42)
    for reference in (REFERENCE_A, REFERENCE_B, "Encounter/22595853"):
        left.surrogate_for(reference)
        right.surrogate_for(reference)
    assert left.mapping == right.mapping

    other = SurrogateIssuer(seed=43)
    for reference in (REFERENCE_A, REFERENCE_B, "Encounter/22595853"):
        other.surrogate_for(reference)
    assert other.mapping != left.mapping
    assert set(other.mapping.values()).isdisjoint(left.mapping.values())


def test_mapping_is_a_copy_so_the_reidentification_key_cannot_be_mutated() -> None:
    issuer = SurrogateIssuer(seed=1)
    token = issuer.surrogate_for(REFERENCE_A)
    snapshot = issuer.mapping
    snapshot[REFERENCE_A] = "tampered"
    assert issuer.mapping[REFERENCE_A] == token


def test_surrogate_is_not_derived_from_the_reference() -> None:
    """45 CFR 164.514(b)(2)(ii): a re-identification code must not be
    derived from information about the individual, and must not be
    translatable back to the individual.

    A digest of the MRN is the obvious implementation and is
    NON-COMPLIANT: it is derived from the identifier and, over the small
    MRN space, trivially invertible by enumeration. Assert explicitly
    against hashlib so that swapping in a hash breaks this test loudly.
    """
    issuer = SurrogateIssuer(seed=11)
    for reference in (REFERENCE_A, REFERENCE_B, "Encounter/22595853"):
        token = issuer.surrogate_for(reference)
        raw = reference.encode("utf-8")

        assert reference not in token
        assert reference.split("/")[-1] not in token

        forbidden = {
            hashlib.sha256(raw).hexdigest(),
            hashlib.md5(raw).hexdigest(),  # comparison target, not in use
            hashlib.sha1(raw).hexdigest(),  # comparison target, not in use
        }
        for digest in forbidden:
            assert digest not in token
            assert digest[:16] not in token
            # ``token not in digest`` was asserted here and could not fail: the
            # token is 30-odd characters of "SUR-<tag>-<ordinal>" and a hex
            # digest contains no hyphen, so the check was free. Dropped.

    # The property that actually distinguishes a counter from a digest: two
    # issuers seeded identically but shown the SAME reference must agree, while
    # a digest-based issuer would also agree here. The discriminating case is
    # below, where arrival order alone changes the token.
    assert SurrogateIssuer(seed=11).surrogate_for(REFERENCE_A) == SurrogateIssuer(
        seed=11
    ).surrogate_for(REFERENCE_A)

    # Arrival order, not content, decides the token: the same reference
    # gets a different token when it is seen second instead of first.
    early = SurrogateIssuer(seed=11).surrogate_for(REFERENCE_A)
    late = SurrogateIssuer(seed=11)
    late.surrogate_for(REFERENCE_B)
    assert late.surrogate_for(REFERENCE_A) != early


# ---------------------------------------------------------------- rewrite_slice


def test_rewrite_surrogate_removes_linkage_keys_and_lowers_exposure() -> None:
    """The key test.

    phi_exposed matches Safe Harbor markers against the KEY of a
    projected-fields dict. Rewriting the value alone leaves exposure
    exactly where it was, so this asserts the key itself is gone and that
    the metric actually moves.
    """
    observation = first_observation()
    manifests = minimal_manifests()
    issuer = SurrogateIssuer(seed=2026)

    before: list[dict[str, Any]] = []
    after: list[dict[str, Any]] = []
    for manifest in manifests.values():
        sliced = project(observation, manifest)
        if sliced is None:
            continue
        fields = sliced["_projected_fields"]
        before.append(fields)
        after.append(rewrite_slice(fields, issuer, "surrogate"))

    assert before, "the minimal manifests released nothing for this Observation"

    completeness = rewrite_slice(
        project(observation, manifests["completeness"])["_projected_fields"],
        issuer,
        "surrogate",
    )

    for key in completeness:
        assert "subject.reference" not in key
        assert "encounter.reference" not in key
    assert "$.subject.surrogate" in completeness
    assert completeness["$.subject.surrogate"] == [
        issuer.surrogate_for(observation["subject"]["reference"])
    ]

    exposed_before = phi_exposed(before)
    exposed_after = phi_exposed(after)
    assert exposed_before > 0
    assert exposed_after < exposed_before

    # And the same movement expressed as the AgentLeak score itself.
    leak_before = leak_record(observation, before).value
    leak_after = leak_record(observation, after).value
    assert leak_before == pytest.approx(1.0)
    assert leak_after < leak_before


def test_rewrite_none_drops_linkage_and_adds_nothing() -> None:
    fields = {
        "$.status": ["final"],
        "$.subject.reference": ["Patient/1"],
        "$.encounter.reference": ["Encounter/9"],
    }
    result = rewrite_slice(fields, SurrogateIssuer(seed=3), "none")

    assert result == {"$.status": ["final"]}
    for path in (*LINKAGE_PATHS, *SURROGATE_PATHS):
        assert path not in result
    assert phi_exposed([result]) == 0


def test_rewrite_reference_is_a_no_op_that_still_returns_a_separate_dict() -> None:
    """REWRITTEN: the last assertion here used to be a tautology.

    The old version asserted ``result == original`` and then
    ``phi_exposed([result]) == phi_exposed([original])``, which follows from the
    first for any deterministic function. What is worth checking instead is the
    property the L0 rung has to have to be safe: it returns a *copy*, so a caller
    that mutates the returned slice cannot reach back into the projection it came
    from. ``dict(slice_fields)`` gives that at the top level, and this pins it.
    """
    fields = {
        "$.status": ["final"],
        "$.subject.reference": ["Patient/1"],
        "$.encounter.reference": ["Encounter/9"],
    }
    original = json.loads(json.dumps(fields))
    issuer = SurrogateIssuer(seed=3)
    result = rewrite_slice(fields, issuer, "reference")

    assert result == original
    assert fields == original  # input untouched
    assert list(result) == list(original)  # key order preserved

    assert result is not fields, "L0 must not hand back the caller's own dict"
    result["$.injected"] = ["x"]
    assert "$.injected" not in fields, "mutating the result reached the input"

    # L0 issues nothing: no surrogate is minted for a rung that rewrites nothing.
    assert issuer.issued == 0


def test_rewrite_surrogate_preserves_non_linkage_fields_and_does_not_mutate() -> None:
    fields = {
        "$.status": ["final"],
        "$.subject.reference": ["Patient/1"],
        "$.reasonReference[*].reference": ["Condition/7"],
    }
    original = json.loads(json.dumps(fields))
    result = rewrite_slice(fields, SurrogateIssuer(seed=5), "surrogate")

    assert fields == original
    assert result["$.status"] == ["final"]
    # Only the declared linkage paths are rewritten; other references are
    # a manifest-width question, not a linkage-ladder one.
    assert result["$.reasonReference[*].reference"] == ["Condition/7"]


def test_unknown_linkage_level_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown linkage level"):
        rewrite_slice({}, SurrogateIssuer(seed=1), "pseudonym")


# ------------------------------------------------------------------ departments


def test_route_is_total_over_every_real_resource_in_every_cohort() -> None:
    seen: set[str] = set()
    routed: set[str] = set()
    for dataset_dir in DATASETS.values():
        resources = _resources_from(dataset_dir)
        assert resources, f"no resources under {dataset_dir}"
        for resource in resources:
            resource_type = resource["resourceType"]
            seen.add(resource_type)
            destination = route(resource)  # must not raise
            assert destination in DEPARTMENT_IDS
            if resource_type in ("Patient", "Observation", "Encounter"):
                assert destination in ("D1", "D2", "D3")
            routed.add(destination)

    assert {"Patient", "Observation", "Encounter"} <= seen
    assert {"D1", "D2"} <= routed


def test_route_is_total_over_malformed_input() -> None:
    assert route({}) == "D0"
    assert route({"resourceType": 17}) == "D0"
    assert route({"resourceType": "Practitioner"}) == "D0"
    assert route({"resourceType": "Patient"}) == "D1"
    assert route({"resourceType": "Observation"}) == "D2"
    assert route({"resourceType": "Encounter"}) == "D2"


def test_department_registry_design_invariants() -> None:
    """The registry's load-bearing property, not a transcription of its contents.

    REWRITTEN: the old version asserted each department's ``resource_types`` and
    ``dimensions`` field by field against the literals in ``departments.py``. That
    is a change-detector, not a test: it re-typed the data under test and would
    have accepted any edit made in both places, while failing on a harmless
    reordering. It also missed the defect the module is known to carry, below.

    What the design actually requires is that exactly one department both handles
    event resources and holds identifiers, because that department is the
    surrogate issuer and the whole ladder rests on there being one of it. That is
    derived here rather than transcribed, so it holds however the table is
    rewritten.
    """
    assert DEPARTMENT_IDS == tuple(sorted(DEPARTMENT_IDS)), "ids must be ordered"
    assert len(set(DEPARTMENT_IDS)) == len(DEPARTMENTS), "ids must be unique"
    for dept in DEPARTMENTS:
        assert isinstance(dept, Department)
        assert department(dept.id) is dept

    events = {"Observation", "Encounter"}
    issuers = [
        d.id for d in DEPARTMENTS if d.holds_identifiers and events & set(d.resource_types)
    ]
    assert issuers == ["D2"], (
        f"exactly one department may resolve event references and hold "
        f"identifiers; found {issuers}"
    )

    # The tier that does the clinical work must not hold identifiers, or the
    # ladder buys nothing.
    clinical = [d for d in DEPARTMENTS if d.requires_model]
    assert clinical, "some department must run the model"
    for dept in clinical:
        assert not dept.holds_identifiers, f"{dept.id} runs the model and holds identifiers"

    # A department that receives no resource type must declare no dimension.
    for dept in DEPARTMENTS:
        if not dept.resource_types:
            assert not dept.dimensions, f"{dept.id} has dimensions but no resource types"

    with pytest.raises(KeyError, match="unknown department"):
        department("D9")


def test_the_registry_disagrees_with_the_manifests_by_exactly_this_much_TRAP() -> None:
    """TRAP, pinned deliberately. Do not "fix" this by editing departments.py.

    ``departments.py`` is a design sketch that runs nothing, and it does not
    describe the manifests it claims to describe. The paper reports this as an
    outstanding defect. Prose cannot stop it drifting, so the disagreement is
    computed from both sources and pinned exactly.

    Two directions, and both matter:

    * pairs the manifests declare that no department covers -- notably
      ``(plausibility, Patient)``, which the plausibility manifest reads at every
      width while the registry routes Patient only to Registration;
    * pairs a department claims that no manifest declares, which would grant a
      tier access it has no policy for.

    When the registry is reconciled, this test is the place to update, and the
    sets should shrink to empty rather than be deleted.
    """
    declared = {
        (dimension, policy.fhir_resource_type)
        for width in WIDTHS
        for dimension, manifest in load_width(MANIFESTS_DIR, width).items()
        for policy in manifest.resource_types
    }
    covered = {
        (dimension, resource_type)
        for dept in DEPARTMENTS
        for dimension in dept.dimensions
        for resource_type in dept.resource_types
    }

    assert declared - covered == {
        ("consistency", "Condition"),
        ("consistency", "MedicationStatement"),
        ("consistency", "Procedure"),
        ("plausibility", "Patient"),
    }, "the set of manifest policies no department covers has changed"

    assert covered - declared == {
        ("completeness", "Patient"),
        ("consistency", "Observation"),
        ("consistency", "Patient"),
        ("plausibility", "Encounter"),
    }, "the set of department claims no manifest supports has changed"

    # The registry also knows about fewer resource types than the manifests do,
    # which is why route() sends Procedure, Condition and MedicationStatement to
    # D0. Harmless today only because run.py admits three types.
    manifest_types = {resource_type for _, resource_type in declared}
    registry_types = {rt for dept in DEPARTMENTS for rt in dept.resource_types}
    assert manifest_types - registry_types == {
        "Condition",
        "MedicationStatement",
        "Procedure",
    }


# ------------------------------------------- the trap, now disarmed and pinned


def test_completeness_survives_surrogate_substitution() -> None:
    """The former TRAP, now fixed, pinned so it cannot regress.

    History. ``REQUIRED_FIELDS`` demands ``$.subject.reference`` on both event
    types, and ``check_completeness`` used to decide pass or fail by looking
    that exact JSONPath key up in the projected-fields dict. Under
    ``linkage='surrogate'`` the key no longer exists: ``rewrite_slice`` deletes
    it and installs ``$.subject.surrogate``, because the exposure metric matches
    Safe Harbor markers against the projected KEY rather than its value, so
    editing the value in place would leave exposure unmoved.

    The effect was that the same real Observation passed completeness
    un-rewritten and failed it after surrogate substitution. Running the L0/L1/L2
    ladder on that footing would have shown completeness cratering from 1.000 on
    every event resource, and the ladder would have appeared to prove that
    surrogates destroy detection.

    The fix is ``dqa.agents._requirement_met``: a requirement is satisfied by the
    canonical path OR by the surrogate that replaced it, since a surrogate
    carries exactly the linkage information a presence check can know about.
    ``REQUIRED_FIELDS`` keeps its published shape.
    """
    # The published constant is unchanged: canonical paths only.
    assert "$.subject.reference" in REQUIRED_FIELDS["Observation"]
    assert "$.subject.reference" in REQUIRED_FIELDS["Encounter"]
    assert "$.subject.surrogate" not in REQUIRED_FIELDS["Observation"]
    assert "$.subject.surrogate" not in REQUIRED_FIELDS["Encounter"]

    from dqa.agents import check_completeness

    observation = first_observation()
    sliced = project(observation, minimal_manifests()["completeness"])
    assert check_completeness(sliced).verdict == "pass"

    rewritten = dict(sliced)
    rewritten["_projected_fields"] = rewrite_slice(
        sliced["_projected_fields"], SurrogateIssuer(seed=1), "surrogate"
    )
    # The reference really is gone, so this is not passing by accident.
    assert "$.subject.reference" not in rewritten["_projected_fields"]
    assert "$.subject.surrogate" in rewritten["_projected_fields"]

    assert check_completeness(rewritten).verdict == "pass"


def test_completeness_still_fails_when_linkage_is_genuinely_absent() -> None:
    """Disarming the trap must not make completeness unfalsifiable.

    At L2 (``linkage='none'``) the linkage key is removed and NO surrogate is
    installed, which is a genuine missing required field and must still fail.
    """
    from dqa.agents import check_completeness

    observation = first_observation()
    sliced = project(observation, minimal_manifests()["completeness"])

    stripped = dict(sliced)
    stripped["_projected_fields"] = rewrite_slice(
        sliced["_projected_fields"], SurrogateIssuer(seed=1), "none"
    )
    assert "$.subject.reference" not in stripped["_projected_fields"]
    assert "$.subject.surrogate" not in stripped["_projected_fields"]

    verdict = check_completeness(stripped)
    assert verdict.verdict == "fail"
    assert "$.subject.reference" in verdict.justification
