"""What everything is called, in one place.

Two naming schemes exist in this project and a reader deserves to be told why
rather than left to infer it.

The paper names things in English
--------------------------------
The paper calls the two systems **Baseline** and **Confined**, and calls the
four departments **Triage**, **Registration**, **Records Linkage** and
**Clinical Checks**. Those are the names to think in.

The result files use the same words
-----------------------------------
``results/`` uses ``baseline`` and ``confined-*``, matching the paper. It used
to use ``A1`` and ``A6``, left over from a six-arm design that was cut to two,
which is why there was never an ``A2`` through ``A5``. Those keys are gone:
the code and every shipped JSON file were migrated together, so
``make reproduce`` still diffs byte for byte.

Departments keep the ordinals ``D0``--``D3``, because they are tier order and
each one carries its English name alongside in :data:`DEPARTMENT_NAMES`.

Use the constants
-----------------
Code should read ``if entry == RECORDS_LINKAGE`` rather than
``if entry == "D2"``, so the intent is legible without a lookup table::

    from dqa.naming import BASELINE, CLINICAL_CHECKS, CONFINED, RECORDS_LINKAGE

    out["systems"][BASELINE] = ...          # the full-access arm
    if entry == RECORDS_LINKAGE: ...        # compares "D2", reads as its job

:data:`SYSTEM_NAMES` and :data:`DEPARTMENT_NAMES` map key to English for
anything that prints. ``tests/test_naming.py`` pins every constant to its wire
value, so the mapping cannot drift away from the shipped artefacts silently.
"""

from __future__ import annotations

__all__ = [
    "BASELINE",
    "CLINICAL_CHECKS",
    "CONFINED_PREFIX",
    "DEPARTMENT_NAMES",
    "RECORDS_LINKAGE",
    "REGISTRATION",
    "SYSTEM_NAMES",
    "TRIAGE",
    "confined",
    "system_name",
]

# ---------------------------------------------------------------- systems

#: The Baseline: one program, full record access, no projection boundary.
BASELINE = "baseline"

#: Prefix for the Confined system's per-width keys. Use :func:`confined`.
CONFINED_PREFIX = "confined"


def confined(width: str) -> str:
    """Result-file key for the Confined system at one manifest width.

    ``confined("minimal")`` is ``"confined-minimal"``. Widths come from
    :data:`dqa.manifests.WIDTHS`.
    """
    return f"{CONFINED_PREFIX}-{width}"


#: Key to the English name the paper uses, for anything that prints a table.
SYSTEM_NAMES: dict[str, str] = {
    BASELINE: "Baseline",
    confined("minimal"): "Confined, minimal",
    confined("intermediate"): "Confined, intermediate",
    confined("full"): "Confined, full",
}


def system_name(key: str) -> str:
    """English name for a system key, or the key itself if unrecognised."""
    return SYSTEM_NAMES.get(key, key)


# ------------------------------------------------------------ departments

#: Triage. Routes on resourceType and status. Holds nothing.
TRIAGE = "D0"

#: Registration. The Patient tier. Identifiers are its legitimate subject.
REGISTRATION = "D1"

#: Records Linkage. Resolves an event's reference once and issues a surrogate.
#: The only tier that both handles event resources and holds identifiers.
RECORDS_LINKAGE = "D2"

#: Clinical Checks. Judges event content against surrogates. Never sees a
#: raw reference, which is the property the whole tiered design exists for.
CLINICAL_CHECKS = "D3"

#: Key to the English name, in tier order.
DEPARTMENT_NAMES: dict[str, str] = {
    TRIAGE: "Triage",
    REGISTRATION: "Registration",
    RECORDS_LINKAGE: "Records Linkage",
    CLINICAL_CHECKS: "Clinical Checks",
}
