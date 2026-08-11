"""Department registry: who is allowed to hold an identifier, and why.

The flat four-agent design gives every checker the same standing, which
is why linkage references leak uniformly across the whole system. A
department structure separates the tiers that legitimately need to
resolve a reference from the tiers that only need to know that two
records belong together.

    D0 Triage           reads resourceType/status, routes, holds nothing
    D1 Registration     the Patient tier; identifiers are its subject
    D2 Records Linkage  resolves event references and issues surrogates
    D3 Clinical Checks  judges event content against surrogates only

D2 is the trusted tier of the linkage ladder in :mod:`dqa.linkage`: it is
the only department that both handles event resources and holds
identifiers. D3 does the clinical work and never sees a reference.

This module is a registry of definitions. It deliberately runs nothing:
routing is a total function over resources, and the pipeline that
consumes it is a separate integration step.
"""

from __future__ import annotations

from dataclasses import dataclass

from dqa.naming import CLINICAL_CHECKS, DEPARTMENT_NAMES, RECORDS_LINKAGE, REGISTRATION, TRIAGE

__all__ = [
    "DEPARTMENTS",
    "DEPARTMENT_IDS",
    "UNROUTED",
    "Department",
    "department",
    "route",
]


@dataclass(frozen=True, slots=True)
class Department:
    """One department in the tiered design.

    ``resource_types`` empty means the department is metadata-only: it
    never receives a projected slice of a clinical resource.
    ``holds_identifiers`` records whether the department is permitted to
    see raw linkage references; ``requires_model`` whether any of its
    checks call the pinned LLM snapshot.
    """

    id: str
    name: str
    resource_types: tuple[str, ...]
    dimensions: tuple[str, ...]
    holds_identifiers: bool
    requires_model: bool


DEPARTMENTS: tuple[Department, ...] = (
    Department(
        id=TRIAGE,
        name=DEPARTMENT_NAMES[TRIAGE],
        resource_types=(),
        dimensions=(),
        holds_identifiers=False,
        requires_model=False,
    ),
    Department(
        id=REGISTRATION,
        name=DEPARTMENT_NAMES[REGISTRATION],
        resource_types=("Patient",),
        dimensions=("completeness", "consistency"),
        holds_identifiers=True,
        requires_model=False,
    ),
    Department(
        id=RECORDS_LINKAGE,
        name=DEPARTMENT_NAMES[RECORDS_LINKAGE],
        resource_types=("Observation", "Encounter"),
        # Deliberately empty, corrected 2026-08-06. D2 previously also claimed
        # "consistency" on both event types, which D3 claims as well, so those
        # two (resource_type, dimension) pairs were assigned to two departments
        # at once and routing was ambiguous. That is not a cosmetic clash: D2
        # holds identifiers and D3 does not, so the same check could have been
        # routed to a tier that may resolve a reference or to one that may not,
        # which is precisely the property this design exists to control.
        #
        # Resolved in D3's favour because it matches this module's own
        # description of the two tiers: D2 "resolves event references and issues
        # surrogates", which is a resolution role, while D3 "judges event
        # content", and period ordering is event content. D2 therefore runs no
        # dimension check at all; it is the trusted resolution tier and nothing
        # else. Enforced by _assert_registry_is_unambiguous below.
        dimensions=(),
        holds_identifiers=True,
        requires_model=False,
    ),
    Department(
        id=CLINICAL_CHECKS,
        name=DEPARTMENT_NAMES[CLINICAL_CHECKS],
        resource_types=("Observation", "Encounter"),
        dimensions=("completeness", "plausibility", "timeliness", "consistency"),
        holds_identifiers=False,
        requires_model=True,
    ),
)

DEPARTMENT_IDS: tuple[str, ...] = tuple(d.id for d in DEPARTMENTS)

_BY_ID: dict[str, Department] = {d.id: d for d in DEPARTMENTS}

#: Returned by :func:`route` for anything the design does not handle.
UNROUTED = TRIAGE

# Entry point per resource type. Event resources enter at D2, which
# resolves their references and issues surrogates, and only then hands
# them on to D3. Routing names the entry department, not every
# department that eventually sees the record.
_ENTRY_BY_RESOURCE_TYPE: dict[str, str] = {
    "Patient": REGISTRATION,
    "Observation": RECORDS_LINKAGE,
    "Encounter": RECORDS_LINKAGE,
}


def route(resource: dict) -> str:
    """Return the entry department id for ``resource``.

    Total: any input, including one with a missing or non-string
    ``resourceType``, yields a department id. Unhandled resource types
    fall to :data:`UNROUTED` (``"D0"``), meaning not applicable rather
    than an error.
    """
    resource_type = resource.get("resourceType") if isinstance(resource, dict) else None
    if not isinstance(resource_type, str):
        return UNROUTED
    return _ENTRY_BY_RESOURCE_TYPE.get(resource_type, UNROUTED)


def department(dept_id: str) -> Department:
    """Look up a department by id. Raises ``KeyError`` if unknown."""
    try:
        return _BY_ID[dept_id]
    except KeyError:
        raise KeyError(
            f"unknown department {dept_id!r}; expected one of {DEPARTMENT_IDS}"
        ) from None


def _assert_registry_is_unambiguous() -> None:
    """No (resource_type, dimension) pair may belong to two departments.

    Runs at import. Routing must be a function: a pair claimed twice has no
    single answer to "which department checks this", and because departments
    differ in ``holds_identifiers`` the ambiguity is a privilege question rather
    than a bookkeeping one.

    Also checks every declared dimension is a real dimension, so a typo cannot
    silently create a department that checks nothing.
    """
    from dqa.manifests import DIMENSIONS

    seen: dict[tuple[str, str], str] = {}
    for dept in DEPARTMENTS:
        for dimension in dept.dimensions:
            if dimension not in DIMENSIONS:
                raise AssertionError(
                    f"department {dept.id} declares unknown dimension "
                    f"{dimension!r}; expected one of {DIMENSIONS}"
                )
            for resource_type in dept.resource_types:
                key = (resource_type, dimension)
                if key in seen:
                    raise AssertionError(
                        f"{key} is claimed by both {seen[key]} and {dept.id}; "
                        f"routing would be ambiguous, and the two departments "
                        f"may differ in whether they hold identifiers"
                    )
                seen[key] = dept.id


_assert_registry_is_unambiguous()
