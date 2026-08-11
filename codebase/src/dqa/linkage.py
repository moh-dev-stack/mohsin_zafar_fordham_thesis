"""The linkage ladder: resolve references once, hand agents a surrogate.

Event resources (Observation, Encounter) carry exactly one class of Safe
Harbor content under every shipped manifest: the linkage references
``$.subject.reference`` and ``$.encounter.reference``. Every checker
needs one of them to know which record it is judging, so narrowing the
manifests cannot lower exposure on those resources at all; they score a
flat 1.0000 at minimal, intermediate and full width alike.

The ladder breaks that floor by moving reference resolution out of the
checkers and into a single trusted tier:

    L0 ``reference``  every agent sees the raw reference (today's system)
    L1 ``surrogate``  one tier resolves the reference and every agent
                      downstream sees an opaque run-scoped token instead
    L2 ``none``       linkage is dropped entirely; agents judge records
                      they cannot join

Two properties of this module are load-bearing and easy to get wrong.

**Non-derivation.** 45 CFR 164.514(c)(1) permits a re-identification
code only if it is *not derived from or related to information about the
individual* and is *not otherwise capable of being translated so as to
identify the individual*. A SHA-256 (or HMAC, or any keyed digest) of the
MRN is the obvious implementation and fails that test: it is derived from
the identifier, and it is translatable by anyone who can enumerate the
identifier space, which for MRNs is small. :class:`SurrogateIssuer`
therefore uses a seeded counter, issued first-come-first-served in
arrival order. The token carries information about *when a reference was
first seen in this run*, and nothing whatsoever about the patient. The
reverse mapping exists only inside the issuer instance and is never
placed in an agent message.

**Key deletion, not value replacement.** :func:`dqa.metrics.phi_exposed`
matches Safe Harbor markers against the *key* of a projected-fields dict
(the substring ``subject.reference``), never against the value. Rewriting
the value while leaving the key in place moves the exposure score by
exactly zero. :func:`rewrite_slice` therefore deletes the linkage key and
installs a differently named surrogate key.

This module defines the ladder. It does not wire it into the
orchestrator; that is a separate integration step.

That integration used to carry a trap: :data:`dqa.agents.REQUIRED_FIELDS`
demanded the raw ``$.subject.reference``, so completeness failed on every
event resource the moment a surrogate replaced it, and the ladder would
have appeared to prove that surrogates destroy detection. Fixed
2026-08-06 by :func:`dqa.agents._requirement_met`, which accepts the
canonical path or the surrogate that replaced it, and pinned by
``tests/test_linkage.py``. L2 ``none`` still fails completeness, which is
correct: there the linkage really is absent.
"""

from __future__ import annotations

import random
from typing import Any, Literal

__all__ = [
    "LINKAGE_LEVELS",
    "LINKAGE_PATHS",
    "SURROGATE_PATHS",
    "Linkage",
    "SurrogateIssuer",
    "rewrite_slice",
    "surrogate_path_for",
]

# The JSONPath keys that carry cross-resource linkage, in the order the
# corresponding surrogate keys appear in SURROGATE_PATHS.
#
# COVERAGE, and why these exact five. The set is held equal to the reference
# paths that ``dqa.metrics.SAFE_HARBOR_JSONPATHS`` counts as exposure, so a path
# cannot be scored as leaked identifier content while being invisible to the
# rewriter that is supposed to surrogate it. Earlier editions carried only
# subject and encounter, which left ``recorder``, ``performer`` and ``device``
# scored but never rewritten: on a cohort whose Observations name a
# practitioner or a device, the ladder would have reported those references as
# exposed at every rung including L1, and the surrogate arm would have looked
# worse than it is. No resource in the three shipped cohorts carries any of the
# three (verified: 0 of the 600 loaded resources), so no published number moves;
# the assertion in ``_assert_linkage_covers_reference_paths`` is what stops the
# two definitions drifting apart again.
LINKAGE_PATHS: tuple[str, ...] = (
    "$.subject.reference",
    "$.encounter.reference",
    "$.recorder.reference",
    "$.performer[*].reference",
    "$.device.reference",
)
SURROGATE_PATHS: tuple[str, ...] = (
    "$.subject.surrogate",
    "$.encounter.surrogate",
    "$.recorder.surrogate",
    "$.performer[*].surrogate",
    "$.device.surrogate",
)

Linkage = Literal["reference", "surrogate", "none"]
LINKAGE_LEVELS: tuple[Linkage, ...] = ("reference", "surrogate", "none")

_PATH_TO_SURROGATE: dict[str, str] = dict(zip(LINKAGE_PATHS, SURROGATE_PATHS, strict=True))

# Token shape: SUR-<12 hex run tag>-<6 digit arrival ordinal>.
_TOKEN_PREFIX = "SUR"
_RUN_TAG_BITS = 48


def _assert_linkage_covers_reference_paths() -> None:
    """Every reference path the exposure metric counts must be rewritable.

    Runs at import, so a path added to ``SAFE_HARBOR_JSONPATHS`` without a
    matching entry here is a startup failure rather than a silent under-rewrite
    that would make the surrogate rung look worse than it is.

    Imported lazily inside the function because ``dqa.metrics`` imports from
    ``dqa.manifests``, and keeping this module free of a module-level dependency
    on metrics avoids a cycle as the package grows.
    """
    from dqa.metrics import SAFE_HARBOR_JSONPATHS

    counted = {
        path
        for label, paths in SAFE_HARBOR_JSONPATHS.items()
        for path in paths
        if path.endswith(".reference")
    }
    uncovered = counted - set(LINKAGE_PATHS)
    if uncovered:
        raise AssertionError(
            f"reference paths counted by the exposure metric but not rewritable "
            f"by the linkage ladder: {sorted(uncovered)}; add them to "
            f"LINKAGE_PATHS and SURROGATE_PATHS or they will be scored as "
            f"exposed at every rung"
        )


def surrogate_path_for(linkage_path: str) -> str | None:
    """The surrogate key that replaces ``linkage_path``, or ``None``."""
    return _PATH_TO_SURROGATE.get(linkage_path)


class SurrogateIssuer:
    """Issues run-scoped opaque surrogates for resource references.

    A surrogate is **not** a function of the reference. It is the next
    value of a seeded counter, handed out first-come-first-served, so two
    runs that encounter the same references in a different order produce
    different tokens for the same patient. Within one run the mapping is
    stable, which is all a linkage checker needs.

    The seed fixes the run tag and therefore the whole token sequence, so
    a run is reproducible from ``(seed, arrival order)`` alone. The seed
    is experiment metadata, not a secret: knowing it reveals which tokens
    *will be issued*, never which reference any token stands for. That
    knowledge lives only in :attr:`mapping`, which stays inside this
    object.
    """

    __slots__ = ("_counter", "_mapping", "_run_tag", "_seed")

    def __init__(self, seed: int) -> None:
        self._seed = int(seed)
        # A seeded PRNG supplies the per-run tag only. It never sees a
        # reference, so no token can depend on identifier content.
        rng = random.Random(self._seed)
        self._run_tag = f"{rng.getrandbits(_RUN_TAG_BITS):0{_RUN_TAG_BITS // 4}x}"
        self._counter = 0
        self._mapping: dict[str, str] = {}

    @property
    def seed(self) -> int:
        return self._seed

    @property
    def run_tag(self) -> str:
        """The per-run token namespace. Derived from the seed alone."""
        return self._run_tag

    @property
    def issued(self) -> int:
        """How many distinct references have been surrogated so far."""
        return self._counter

    @property
    def mapping(self) -> dict[str, str]:
        """A copy of the reference -> surrogate table.

        Held for audit inside the trusted tier only. This is the
        re-identification key; releasing it into an agent message, a run
        artefact or a log defeats the entire ladder.
        """
        return dict(self._mapping)

    def surrogate_for(self, reference: str) -> str:
        """Return the stable run-scoped token for ``reference``.

        First call for a given reference consumes the next counter value;
        later calls replay it.
        """
        key = str(reference)
        token = self._mapping.get(key)
        if token is None:
            self._counter += 1
            token = f"{_TOKEN_PREFIX}-{self._run_tag}-{self._counter:06d}"
            self._mapping[key] = token
        return token


def rewrite_slice(
    slice_fields: dict[str, list],
    issuer: SurrogateIssuer,
    linkage: str,
) -> dict[str, list]:
    """Apply one rung of the linkage ladder to a projected-fields dict.

    ``slice_fields`` is the ``_projected_fields`` mapping produced by
    :func:`dqa.manifests.project`: JSONPath expression -> list of values.
    The input is never mutated.

    ``linkage``:

    * ``"reference"`` - L0, return the slice unchanged.
    * ``"surrogate"`` - L1, **delete** each linkage key and install the
      matching surrogate key in its place, carrying issued tokens.
    * ``"none"`` - L2, delete each linkage key and add nothing.

    Key position is preserved so downstream ordering stays stable.
    """
    if linkage not in LINKAGE_LEVELS:
        raise ValueError(
            f"unknown linkage level {linkage!r}; expected one of {LINKAGE_LEVELS}"
        )

    if linkage == "reference":
        return dict(slice_fields)

    rewritten: dict[str, list] = {}
    for path, values in slice_fields.items():
        surrogate_key = _PATH_TO_SURROGATE.get(path)
        if surrogate_key is None:
            rewritten[path] = list(values)
            continue
        # The linkage key is dropped here. phi_exposed scores keys, so
        # dropping the key is the whole mechanism.
        if linkage == "none":
            continue
        items: list[Any] = values if isinstance(values, list) else [values]
        rewritten[surrogate_key] = [issuer.surrogate_for(str(value)) for value in items]

    return rewritten


# Checked at import: the ladder must be able to rewrite every reference path the
# exposure metric counts. See _assert_linkage_covers_reference_paths.
_assert_linkage_covers_reference_paths()
