"""Linkage-aware Safe Harbor detection over free text.

Why this module exists
----------------------
``dqa.metrics.phi_exposed`` scores a *projection*: it inspects the JSONPath
keys a manifest released and counts Safe Harbor fields among them. That is
correct for structured inter-agent messages, but it scores any free-text
payload as zero unconditionally, because a paragraph of prose has no
JSONPath keys to inspect. An agent that copies "Abram53 born 1949-06-25"
into its natural-language rationale therefore leaks PHI that the structured
metric cannot see.

The design insight
------------------
We do not need general-purpose named-entity recognition. For a given source
FHIR record we already *know* the true identifiers, so we scan outbound text
for those specific strings. This turns an open-vocabulary recognition problem
into a closed-vocabulary string-search problem, which is exact, cheap,
deterministic, and needs no model.

Matching is word-boundary anchored
----------------------------------
A token whose first or last character is alphanumeric must begin or end at a
word boundary to count. Without that rule the search is a bare substring scan,
and a short Synthea-style name such as ``"Ann"`` matches inside ``"Announced"``,
``"Lee"`` inside ``"Sleep"`` and ``"Ray"`` inside ``"X-ray"``. Those are false
hits, and false hits in this direction are worse than useless: they inflate a
number the module's whole claim is that it under-reports. Tokens that begin or
end with punctuation -- a telephone number written ``"(555) 010-9999"``, an
address line -- are anchored only on whichever end is alphanumeric, because a
boundary either side of punctuation is already unambiguous.

WHAT THIS IS NOT
----------------
For the closed-vocabulary categories -- name, identifier, address, telecom,
reference, birthdate -- this is a LOWER BOUND on outbound exposure, not a
general PHI detector and not an NER system. It only finds identifiers
belonging to the record under test. It will systematically miss:

  * identifiers of *other* people mentioned in the text (a relative, a
    referring clinician) that are not in the source record;
  * paraphrased or partially spelled identifiers ("Mr. S.", "Abram");
  * quasi-identifiers and re-identification-by-combination (rare diagnosis
    plus town plus admission month), which Safe Harbor does not enumerate
    but which carry real linkage risk;
  * identifiers reconstructed across several messages, none of which
    contains a full match on its own.

A reported count of zero therefore means "no known identifier of this record
was echoed verbatim", NOT "this text is de-identified". Any claim built on
this metric must be phrased as a floor on leakage, never as a certificate of
safety.

The one category that is NOT a lower bound is ``bare_date``. HIPAA Safe Harbor
45 CFR 164.514(b)(2)(i)(C) treats *all* date elements more precise than year as
identifiers, so any date-shaped substring is reported whether or not it belongs
to the record. That is deliberate and it is over-inclusive: a drug expiry or a
guideline publication date counts. So the total returned by
:func:`phi_text_count` is a floor on *this record's* identifiers plus an
over-count of dates. If a strict floor is wanted, filter ``bare_date`` out.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

__all__ = [
    "Identifiers",
    "PhiHit",
    "extract_identifiers",
    "phi_text_count",
    "scan_text",
]

# Tokens shorter than this are ignored: two-character strings such as a state
# code ("MA") or an initial collide with ordinary prose and would drown the
# signal in false positives.
MIN_TOKEN_LEN = 3

# Reference-bearing elements, mirroring the reference entries of
# dqa.metrics.SAFE_HARBOR_JSONPATHS so the text-mode and structured-mode
# detectors agree on what counts as a linkable pointer.
_REFERENCE_KEYS = ("subject", "encounter", "recorder", "device")
_REFERENCE_LIST_KEYS = ("performer",)

CATEGORIES = (
    "name",
    "identifier",
    "birthdate",
    "address",
    "telecom",
    "reference",
    "bare_date",
)

# Lower number wins when two hits cover the same span. "bare_date" is last so
# a date we can attribute to the record is always reported as "birthdate".
_CATEGORY_PRIORITY = {c: i for i, c in enumerate(CATEGORIES)}

_MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)

_ISO_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")

# Date-shaped substrings: ISO yyyy-mm-dd, plus slash-separated forms in either
# day-first or month-first order, plus yyyy/mm/dd.
_BARE_DATE_RE = re.compile(
    r"\b\d{4}-\d{1,2}-\d{1,2}\b"
    r"|\b\d{4}/\d{1,2}/\d{1,2}\b"
    r"|\b\d{1,2}/\d{1,2}/\d{2,4}\b"
)


# ---------------------------------------------------------------- data model


@dataclass(frozen=True, slots=True)
class Identifiers:
    """The known Safe Harbor identifiers of one source record."""

    names: frozenset[str]
    identifiers: frozenset[str]
    birth_dates: frozenset[str]
    addresses: frozenset[str]
    telecoms: frozenset[str]
    references: frozenset[str]

    def is_empty(self) -> bool:
        return not (
            self.names
            or self.identifiers
            or self.birth_dates
            or self.addresses
            or self.telecoms
            or self.references
        )


@dataclass(frozen=True, slots=True)
class PhiHit:
    """One identifier found in scanned text."""

    category: str
    matched: str
    start: int

    @property
    def end(self) -> int:
        return self.start + len(self.matched)


# ----------------------------------------------------------------- extraction


def _clean(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _add(bucket: set[str], value: Any) -> None:
    text = _clean(value)
    if text:
        bucket.add(text)


def _reference_id(reference: Any) -> str:
    """``"Patient/3d426d86-..."`` -> ``"3d426d86-..."``.

    The bare id is stored rather than the full reference because prose tends
    to quote the id alone; a match on the id also matches inside the full
    reference string, so the shorter form strictly dominates.
    """
    text = _clean(reference)
    return text.rsplit("/", 1)[-1] if text else ""


def extract_identifiers(resource: dict[str, Any]) -> Identifiers:
    """Collect the Safe Harbor identifiers carried by one FHIR resource.

    Works on any resource type. A Patient contributes names, birthDate,
    addresses and telecoms; a clinical resource typically contributes only
    identifiers and references.
    """
    names: set[str] = set()
    identifiers: set[str] = set()
    birth_dates: set[str] = set()
    addresses: set[str] = set()
    telecoms: set[str] = set()
    references: set[str] = set()

    for entry in resource.get("name") or []:
        if not isinstance(entry, dict):
            continue
        _add(names, entry.get("family"))
        for given in entry.get("given") or []:
            _add(names, given)

    for entry in resource.get("identifier") or []:
        if isinstance(entry, dict):
            _add(identifiers, entry.get("value"))

    _add(birth_dates, resource.get("birthDate"))

    for entry in resource.get("address") or []:
        if not isinstance(entry, dict):
            continue
        for line in entry.get("line") or []:
            _add(addresses, line)
        _add(addresses, entry.get("city"))
        _add(addresses, entry.get("postalCode"))

    for entry in resource.get("telecom") or []:
        if isinstance(entry, dict):
            _add(telecoms, entry.get("value"))

    for key in _REFERENCE_KEYS:
        value = resource.get(key)
        if isinstance(value, dict):
            _add(references, _reference_id(value.get("reference")))

    for key in _REFERENCE_LIST_KEYS:
        for entry in resource.get(key) or []:
            if not isinstance(entry, dict):
                continue
            _add(references, _reference_id(entry.get("reference")))
            actor = entry.get("actor")
            if isinstance(actor, dict):
                _add(references, _reference_id(actor.get("reference")))

    return Identifiers(
        names=frozenset(names),
        identifiers=frozenset(identifiers),
        birth_dates=frozenset(birth_dates),
        addresses=frozenset(addresses),
        telecoms=frozenset(telecoms),
        references=frozenset(references),
    )


# --------------------------------------------------------------- date shapes


def date_variants(iso_date: str) -> frozenset[str]:
    """Renderings of an ISO date that a model might emit in prose.

    ``"1953-04-14"`` yields the ISO form plus slash forms in both day-first
    and month-first order (zero-padded and not) and long/abbreviated month
    names in both English orderings. Matching is case-insensitive downstream,
    so only one capitalisation of each month name is generated.
    """
    text = _clean(iso_date)
    match = _ISO_DATE_RE.fullmatch(text)
    if match is None:
        return frozenset({text}) if text else frozenset()

    year, month, day = match.group(1), match.group(2), match.group(3)
    m_i, d_i = int(month), int(day)
    if not (1 <= m_i <= 12):
        return frozenset({text})

    long_name = _MONTHS[m_i - 1]
    short_name = long_name[:3]

    out = {
        text,
        f"{year}/{month}/{day}",
        f"{month}/{day}/{year}",
        f"{m_i}/{d_i}/{year}",
        f"{day}/{month}/{year}",
        f"{d_i}/{m_i}/{year}",
        f"{month}-{day}-{year}",
        f"{day}-{month}-{year}",
    }
    for label in (long_name, short_name):
        for day_form in (str(d_i), day):
            out.add(f"{label} {day_form}, {year}")
            out.add(f"{label} {day_form} {year}")
            out.add(f"{day_form} {label} {year}")
            out.add(f"{day_form} {label}, {year}")
    return frozenset(out)


# ----------------------------------------------------------------- scanning


def _find_all(haystack_lower: str, needle_lower: str) -> Iterable[int]:
    """Offsets of every case-insensitive occurrence of ``needle_lower``."""
    start = haystack_lower.find(needle_lower)
    while start != -1:
        yield start
        start = haystack_lower.find(needle_lower, start + 1)


def _is_word_char(char: str) -> bool:
    """Whether ``char`` can continue an identifier token.

    Underscore is included so ``"_Ann"`` is one token rather than a boundary
    followed by a hit. Hyphen and apostrophe are excluded, so a hyphenated
    surname still matches each half of ``"Smith-Jones"``, which is the
    behaviour a leak detector wants.
    """
    return char.isalnum() or char == "_"


def _at_word_boundary(text: str, start: int, end: int, token: str) -> bool:
    """Whether ``text[start:end]`` stands alone rather than sitting inside a word.

    Anchoring is per end and conditional on the token's own shape: a boundary is
    demanded only where the token itself starts or ends with a word character.
    A token that starts with ``'('`` or ends with ``')'`` is already delimited
    by its own punctuation and gains nothing from the test.
    """
    if _is_word_char(token[0]) and start > 0 and _is_word_char(text[start - 1]):
        return False
    return not (_is_word_char(token[-1]) and end < len(text) and _is_word_char(text[end]))


def _literal_hits(
    text: str, lowered: str, category: str, tokens: Iterable[str]
) -> list[PhiHit]:
    """Exact, case-insensitive, word-boundary-anchored hits for one category.

    ``matched`` preserves the casing as it appears in ``text``, so a hit can
    be quoted back verbatim in an audit trail. See the module docstring for why
    the boundary test is not optional: without it the count is not a floor.
    """
    hits: list[PhiHit] = []
    for raw in tokens:
        token = raw.strip()
        if len(token) < MIN_TOKEN_LEN:
            continue
        width = len(token)
        for start in _find_all(lowered, token.lower()):
            end = start + width
            if not _at_word_boundary(text, start, end, token):
                continue
            hits.append(
                PhiHit(
                    category=category,
                    matched=text[start:end],
                    start=start,
                )
            )
    return hits


def _resolve_overlaps(hits: list[PhiHit]) -> list[PhiHit]:
    """Keep one hit per span of text: leftmost, longest, most specific.

    Two rules collapse here. Hits at the same offset (an identifier value that
    is also a reference id) reduce to the higher-priority category, and a
    ``bare_date`` covering the same characters as an attributable date is
    dropped in favour of ``birthdate``. Partially overlapping matches -- for
    example the padded ``04/14/1953`` and the unpadded ``4/14/1953`` inside it
    -- are the same textual evidence and must not be double counted.
    """
    ordered = sorted(
        hits,
        key=lambda h: (h.start, -len(h.matched), _CATEGORY_PRIORITY.get(h.category, 99)),
    )
    kept: list[PhiHit] = []
    covered_to = -1
    for hit in ordered:
        if hit.start < covered_to:
            continue
        kept.append(hit)
        covered_to = hit.end
    return kept


def scan_text(text: str, ids: Identifiers) -> list[PhiHit]:
    """Find every known identifier of ``ids`` echoed in ``text``.

    Returns hits ordered by offset, one per span. See the module docstring:
    an empty list is a lower bound, not proof of de-identification.
    """
    if not text:
        return []

    lowered = text.lower()
    hits: list[PhiHit] = []

    hits += _literal_hits(text, lowered, "name", ids.names)
    hits += _literal_hits(text, lowered, "identifier", ids.identifiers)
    hits += _literal_hits(text, lowered, "address", ids.addresses)
    hits += _literal_hits(text, lowered, "telecom", ids.telecoms)
    hits += _literal_hits(text, lowered, "reference", ids.references)

    known_dates: set[str] = set()
    for iso in ids.birth_dates:
        known_dates |= date_variants(iso)
    hits += _literal_hits(text, lowered, "birthdate", known_dates)

    # Safe Harbor 45 CFR 164.514(b)(2)(i)(C): every date element finer than
    # year is an identifier, whoever it belongs to.
    for match in _BARE_DATE_RE.finditer(text):
        hits.append(
            PhiHit(category="bare_date", matched=match.group(0), start=match.start())
        )

    return _resolve_overlaps(hits)


def phi_text_count(texts: list[str], ids: Identifiers) -> int:
    """Total identifier hits across every text in ``texts``."""
    return sum(len(scan_text(t, ids)) for t in texts)
