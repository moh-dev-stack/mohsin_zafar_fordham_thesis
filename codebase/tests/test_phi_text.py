"""Tests for the linkage-aware text-mode Safe Harbor detector."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from dqa.phi_text import (
    Identifiers,
    PhiHit,
    extract_identifiers,
    phi_text_count,
    scan_text,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SYNTHEA_DIR = REPO_ROOT / "data" / "synthea-fhir"


def _fake_patient(
    given: str = "Abram53",
    family: str = "Strosin214",
    birth_date: str = "1949-06-25",
) -> dict:
    """A minimal Patient carrying exactly the identifiers under test."""
    return {
        "resourceType": "Patient",
        "id": "test-patient-0001",
        "name": [{"use": "official", "family": family, "given": [given]}],
        "birthDate": birth_date,
    }


@pytest.fixture()
def ids() -> Identifiers:
    return extract_identifiers(_fake_patient())


def _categories(hits: list[PhiHit]) -> set[str]:
    return {h.category for h in hits}


# ------------------------------------------------------------------ planted


def test_planted_name_and_birthdate_are_both_found(ids: Identifiers) -> None:
    """The motivating leak: a rationale that quotes the patient back."""
    text = "Value implausible for Abram53 born 1949-06-25"
    hits = scan_text(text, ids)

    assert "name" in _categories(hits)
    assert "birthdate" in _categories(hits)

    name_hit = next(h for h in hits if h.category == "name")
    assert name_hit.matched == "Abram53"
    assert text[name_hit.start : name_hit.start + len(name_hit.matched)] == "Abram53"

    date_hit = next(h for h in hits if h.category == "birthdate")
    assert date_hit.matched == "1949-06-25"

    # The known date is reported once, as "birthdate", not also as "bare_date".
    assert "bare_date" not in _categories(hits)
    assert len(hits) == 2


def test_extract_identifiers_splits_given_and_family(ids: Identifiers) -> None:
    assert ids.names == frozenset({"Abram53", "Strosin214"})
    assert ids.birth_dates == frozenset({"1949-06-25"})
    assert ids.identifiers == frozenset()


# -------------------------------------------------------------------- clean


def test_clean_clinical_sentence_has_no_hits(ids: Identifiers) -> None:
    """Ordinary clinical prose must not trip the detector."""
    assert scan_text("Potassium 4.1 mmol/L is within range", ids) == []
    assert phi_text_count(["Potassium 4.1 mmol/L is within range"], ids) == 0


def test_short_tokens_are_ignored() -> None:
    """Sub-3-character tokens are dropped: they collide with ordinary prose."""
    noisy = Identifiers(
        names=frozenset({"Li"}),
        identifiers=frozenset({"MRN1"}),
        birth_dates=frozenset(),
        addresses=frozenset({"MA"}),
        telecoms=frozenset(),
        references=frozenset(),
    )
    assert scan_text("Lithium level A1c in MAssachusetts", noisy) == []


# ------------------------------------------------------------- date formats


@pytest.mark.parametrize(
    "rendering",
    [
        "1953-04-14",
        "04/14/1953",
        "14/04/1953",
        "April 14, 1953",
        "14 April 1953",
    ],
)
def test_all_renderings_of_a_known_date_are_found(rendering: str) -> None:
    """A date is a date however the model chooses to write it."""
    ids = extract_identifiers(_fake_patient(birth_date="1953-04-14"))
    text = f"Patient was seen on {rendering} for follow-up."
    hits = scan_text(text, ids)

    assert "birthdate" in _categories(hits), f"missed rendering {rendering!r}"
    hit = next(h for h in hits if h.category == "birthdate")
    assert hit.matched == rendering
    assert text[hit.start : hit.start + len(hit.matched)] == rendering
    # No double counting of padded/unpadded overlapping variants.
    assert len(hits) == 1


def test_case_insensitive_match(ids: Identifiers) -> None:
    hits = scan_text("Reviewed with ABRAM53 strosin214 today", ids)
    assert _categories(hits) == {"name"}
    assert {h.matched for h in hits} == {"ABRAM53", "strosin214"}


# --------------------------------------------------------------- bare dates


def test_unknown_date_still_reported_as_bare_date(ids: Identifiers) -> None:
    """Safe Harbor 45 CFR 164.514(b)(2)(i)(C): any date finer than year."""
    hits = scan_text("Follow-up imaging scheduled for 2021-11-03.", ids)
    assert len(hits) == 1
    assert hits[0].category == "bare_date"
    assert hits[0].matched == "2021-11-03"


def test_unknown_slash_date_reported_as_bare_date(ids: Identifiers) -> None:
    hits = scan_text("Admitted 3/17/2018 via the ED.", ids)
    assert [h.category for h in hits] == ["bare_date"]
    assert hits[0].matched == "3/17/2018"


def test_year_alone_is_not_a_date(ids: Identifiers) -> None:
    """Year-only elements are permitted under Safe Harbor."""
    assert scan_text("Diagnosed in 2018; stable since.", ids) == []


def test_phi_text_count_sums_across_texts(ids: Identifiers) -> None:
    texts = [
        "Value implausible for Abram53 born 1949-06-25",  # 2
        "Potassium 4.1 mmol/L is within range",           # 0
        "Follow-up imaging scheduled for 2021-11-03.",    # 1
    ]
    assert phi_text_count(texts, ids) == 3


# ------------------------------------------------------------- real corpus


def _first_bundle() -> dict:
    bundles = sorted(SYNTHEA_DIR.glob("*.json"))
    if not bundles:
        pytest.skip(f"no Synthea bundles under {SYNTHEA_DIR}")
    return json.loads(bundles[0].read_text(encoding="utf-8"))


def _resources(bundle: dict, resource_type: str) -> list[dict]:
    return [
        entry["resource"]
        for entry in bundle.get("entry", [])
        if entry.get("resource", {}).get("resourceType") == resource_type
    ]


def _decode_note(document_reference: dict) -> str:
    data = document_reference["content"][0]["attachment"]["data"]
    return base64.b64decode(data).decode("utf-8")


def _section(note: str, header: str) -> str:
    """The lines belonging to one '# ' section of a Synthea markdown note."""
    kept: list[str] = []
    inside = False
    for line in note.splitlines(keepends=True):
        if line.startswith("# "):
            inside = line.strip() == header
        if inside:
            kept.append(line)
    return "".join(kept)


def test_section_limited_projection_reduces_exposure() -> None:
    """Projecting only the section an agent needs cuts outbound PHI.

    The full Synthea note leaks the patient's given name in the History of
    Present Illness line and the encounter date in the header. The
    Assessment and Plan section, which is all a quality-review agent needs to
    judge a medication entry, carries neither.
    """
    bundle = _first_bundle()
    patients = _resources(bundle, "Patient")
    assert patients, "bundle has no Patient"
    ids = extract_identifiers(patients[0])
    given = sorted(ids.names)

    documents = [
        doc
        for doc in _resources(bundle, "DocumentReference")
        if doc.get("content", [{}])[0].get("attachment", {}).get("data")
    ]
    assert documents, "bundle has no DocumentReference with attachment data"

    note = _decode_note(documents[0])
    assert "# Assessment and Plan" in note, "note layout changed"

    full_hits = scan_text(note, ids)
    assert full_hits, "expected the full note to leak identifiers"

    # The given name really is echoed in the note prose.
    name_hits = [h for h in full_hits if h.category == "name"]
    assert name_hits, f"expected one of {given} in the note"
    assert any(h.matched.lower() in {n.lower() for n in ids.names} for h in name_hits)

    section_hits = scan_text(_section(note, "# Assessment and Plan"), ids)
    assert len(section_hits) < len(full_hits), (
        f"section projection did not reduce exposure: "
        f"full={len(full_hits)} section={len(section_hits)}"
    )


def test_section_projection_reduces_exposure_across_all_notes() -> None:
    """The same result holds over every note in the bundle, not just one."""
    bundle = _first_bundle()
    ids = extract_identifiers(_resources(bundle, "Patient")[0])

    notes = [
        _decode_note(doc)
        for doc in _resources(bundle, "DocumentReference")
        if doc.get("content", [{}])[0].get("attachment", {}).get("data")
    ]
    sections = [_section(n, "# Assessment and Plan") for n in notes]

    full_total = phi_text_count(notes, ids)
    section_total = phi_text_count(sections, ids)

    assert full_total > 0
    assert section_total < full_total
