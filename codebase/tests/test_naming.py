"""The English names must stay pinned to the keys the result files use.

``dqa.naming`` exists so code can say ``RECORDS_LINKAGE`` instead of ``"D2"``.
That is only safe while the constants still equal the strings every shipped
JSON file was written with. If someone "tidies" ``BASELINE`` to ``"Baseline"``,
the code keeps running, the tests that read fixtures keep passing, and every
published result silently stops loading. These tests are the tripwire.
"""

from __future__ import annotations

import json
from pathlib import Path

from dqa.departments import DEPARTMENT_IDS, DEPARTMENTS
from dqa.manifests import WIDTHS
from dqa.naming import (
    BASELINE,
    CLINICAL_CHECKS,
    DEPARTMENT_NAMES,
    RECORDS_LINKAGE,
    REGISTRATION,
    SYSTEM_NAMES,
    TRIAGE,
    confined,
    system_name,
)

RESULTS = Path(__file__).resolve().parents[1] / "results"


def test_wire_keys_are_exactly_what_the_result_files_use() -> None:
    assert BASELINE == "baseline"
    assert confined("minimal") == "confined-minimal"
    assert confined("intermediate") == "confined-intermediate"
    assert confined("full") == "confined-full"
    assert (TRIAGE, REGISTRATION, RECORDS_LINKAGE, CLINICAL_CHECKS) == ("D0", "D1", "D2", "D3")


def test_every_width_has_a_confined_key_and_an_english_name() -> None:
    for width in WIDTHS:
        assert confined(width) in SYSTEM_NAMES
        assert system_name(confined(width)).startswith("Confined")
    assert system_name(BASELINE) == "Baseline"


def test_department_registry_agrees_with_the_naming_module() -> None:
    """The registry and the naming table must not drift apart."""
    assert DEPARTMENT_IDS == tuple(DEPARTMENT_NAMES)
    for dept in DEPARTMENTS:
        assert dept.name == DEPARTMENT_NAMES[dept.id]


def test_unknown_key_passes_through_rather_than_raising() -> None:
    """A printer must not crash on a key added after this table was written."""
    assert system_name("confined-experimental") == "confined-experimental"


def test_the_old_a1_a6_keys_are_gone_from_every_shipped_result() -> None:
    """The migration must be complete, not partial."""
    for path in sorted(RESULTS.glob("*.json")):
        doc = json.loads(path.read_text())
        stale = sorted(_stale_keys(doc))
        assert not stale, f"{path.name} still carries retired keys: {stale}"


def _stale_keys(node: object) -> set[str]:
    found: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if key.startswith(("A1", "A6")):
                found.add(key)
            found |= _stale_keys(value)
    elif isinstance(node, list):
        for item in node:
            found |= _stale_keys(item)
    return found


def test_shipped_results_still_carry_these_keys() -> None:
    """The real point: the constants must open the real files."""
    path = RESULTS / "results_stratified_model.json"
    if not path.is_file():
        return  # model-backed artefact absent; the offline gates cover the rest
    doc = json.loads(path.read_text())
    for cohort in doc.get("cohorts", []):
        systems = cohort.get("systems", {})
        if not systems:
            continue
        assert BASELINE in systems, f"{BASELINE} missing; naming has drifted from the artefacts"
        for width in WIDTHS:
            assert confined(width) in systems


def test_tiered_results_still_carry_the_department_ids() -> None:
    path = RESULTS / "tiered_departments.json"
    if not path.is_file():
        return
    doc = json.loads(path.read_text())
    ids = {d["id"] for d in doc["departments"]}
    assert ids == set(DEPARTMENT_NAMES)
    routed = set()
    for cell in doc["cells"]:
        routed |= set(cell["routed"])
    assert {REGISTRATION, RECORDS_LINKAGE} <= routed
