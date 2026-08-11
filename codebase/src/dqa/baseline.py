"""Baseline: the monolithic rule-based baseline with full record access.

This is the detection ceiling the least-privilege architecture is
measured against. It is the traditional DQA pattern: one process, one
set of rules, unrestricted access to the whole resource. No projection,
no manifests, no LLM.

Why it exists as its own module: earlier revisions of this project
defined the non-inferiority test against Baseline but never computed Baseline, so
every reported delta was actually Confined-minimal against Confined-full. Computing
Baseline makes the declared test runnable and tells us how much detection the
multi-agent decomposition costs in absolute terms, separately from how
much narrowing the manifest costs.

Baseline sees everything, so its rules are written directly against the FHIR
resource rather than a projected slice.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from dqa.agents import (
    MAX_FUTURE_SKEW_MINUTES,
    MAX_PAST_AGE_DAYS,
    NOW_ANCHOR,
    Verdict,
    _parse_iso,
)
from dqa.ranges import RangeRule, inputs_verdict, record_range_inputs

# Re-exported so ``dqa.baseline.NOW_ANCHOR`` names the same fixed evaluation
# clock the Confined timeliness agent uses. Defined once, in ``dqa.agents``; see the
# comment there for the date and the margin behind it. The two rules must share
# an anchor or the two arms are timed against different presents.
__all__ = [
    "NOW_ANCHOR",
    "PLAUSIBLE_MAX",
    "PLAUSIBLE_MIN",
    "MonolithicBaseline",
    "MonolithicBaselineRanges",
    "a1_completeness",
    "a1_consistency",
    "a1_timeliness",
    "baseline_plausibility",
    "baseline_plausibility_ranges",
]

# Any observation value outside this envelope is not survivable in a
# living patient under any unit convention used by the demo cohorts.
# Deliberately wide: Baseline is a detection ceiling, not a clinical tool.
PLAUSIBLE_MIN = -1000.0
PLAUSIBLE_MAX = 100_000.0

REQUIRED_BY_TYPE: dict[str, list[str]] = {
    "Observation": ["status", "code", "subject"],
    "Encounter": ["status", "period", "subject"],
}


def _has(resource: dict[str, Any], field: str) -> bool:
    value = resource.get(field)
    return value is not None and value != {} and value != []


def a1_completeness(resource: dict[str, Any]) -> Verdict:
    """Required-field presence, read straight off the full resource."""
    resource_type = str(resource.get("resourceType", "Unknown"))
    required = REQUIRED_BY_TYPE.get(resource_type, [])
    if not required:
        return Verdict("completeness", "uncertain", f"no rules for {resource_type}")

    missing = [f for f in required if not _has(resource, f)]
    if missing:
        return Verdict("completeness", "fail", f"missing: {', '.join(missing)}")
    return Verdict("completeness", "pass", "all required fields present")


def baseline_plausibility(resource: dict[str, Any]) -> Verdict:
    """Range check on every numeric value the resource carries."""
    if resource.get("resourceType") != "Observation":
        return Verdict("plausibility", "uncertain", "not an Observation")

    values: list[float] = []
    quantity = resource.get("valueQuantity")
    if isinstance(quantity, dict) and isinstance(quantity.get("value"), (int, float)):
        values.append(float(quantity["value"]))
    for component in resource.get("component", []) or []:
        if not isinstance(component, dict):
            continue
        component_quantity = component.get("valueQuantity")
        if isinstance(component_quantity, dict) and isinstance(
            component_quantity.get("value"), (int, float)
        ):
            values.append(float(component_quantity["value"]))

    if not values:
        return Verdict("plausibility", "uncertain", "no numeric value to check")

    out_of_range = [v for v in values if v < PLAUSIBLE_MIN or v > PLAUSIBLE_MAX]
    if out_of_range:
        return Verdict("plausibility", "fail", f"value out of range: {out_of_range[0]}")
    return Verdict("plausibility", "pass", "values within plausible envelope")


def a1_consistency(resource: dict[str, Any]) -> Verdict:
    """Cross-field invariants, chiefly Encounter period ordering."""
    if resource.get("resourceType") != "Encounter":
        return Verdict("consistency", "uncertain", "not an Encounter")

    period = resource.get("period")
    if not isinstance(period, dict):
        return Verdict("consistency", "uncertain", "no period to check")

    start, end = _parse_iso(period.get("start")), _parse_iso(period.get("end"))
    if start is None or end is None:
        return Verdict("consistency", "uncertain", "period incomplete")
    if end < start:
        return Verdict("consistency", "fail", "encounter end precedes start")
    return Verdict("consistency", "pass", "period ordering consistent")


def a1_timeliness(resource: dict[str, Any], now: datetime | None = None) -> Verdict:
    """Timestamp plausibility across the whole resource.

    ``now`` defaults to :data:`NOW_ANCHOR`, the same fixed evaluation clock
    :func:`dqa.agents.check_timeliness` uses, so this verdict is a function of
    the resource alone and does not drift with the calendar.
    """
    now = NOW_ANCHOR if now is None else now
    stamps: list[tuple[str, Any]] = []

    if "effectiveDateTime" in resource:
        stamps.append(("effectiveDateTime", resource["effectiveDateTime"]))
    period = resource.get("period")
    if isinstance(period, dict):
        for key in ("start", "end"):
            if key in period:
                stamps.append((f"period.{key}", period[key]))

    if not stamps:
        return Verdict("timeliness", "uncertain", "no timestamp to check")

    for name, raw in stamps:
        parsed = _parse_iso(raw)
        if parsed is None:
            return Verdict("timeliness", "fail", f"unparseable timestamp at {name}")
        if parsed > now + timedelta(minutes=MAX_FUTURE_SKEW_MINUTES):
            return Verdict("timeliness", "fail", f"future timestamp at {name}")
        if parsed < now - timedelta(days=MAX_PAST_AGE_DAYS):
            return Verdict("timeliness", "fail", f"implausibly old timestamp at {name}")
    return Verdict("timeliness", "pass", "timestamps within bounds")


def baseline_plausibility_ranges(
    resource: dict[str, Any], rules: dict[str, RangeRule]
) -> Verdict:
    """Per-analyte range check, read straight off the full resource.

    The alternative to :func:`baseline_plausibility`. Same full record access, same
    place in the pipeline; the only difference is that the one-size envelope
    ``[PLAUSIBLE_MIN, PLAUSIBLE_MAX]`` is replaced by the per-analyte
    survivability bounds in ``manifests/rules/loinc_ranges.yaml``.

    That matters because the envelope is not a measurement. It catches two of
    the four values ``dqa.inject.IMPLAUSIBLE_VALUES`` substitutes (-9999 and
    1e9) and misses the other two (9999 and -1), so Baseline's plausibility recall is
    pinned at exactly 0.5 and its F1 at 0.667 on every cohort, by arithmetic
    rather than by evidence. Comparing the confined agents against that is a
    comparison of checkers, not of privilege.

    ``component[*].valueQuantity`` is not read here even though
    :func:`baseline_plausibility` reads it; see
    :func:`dqa.ranges.record_range_inputs` for why the two arms must consume
    the same three fields.
    """
    verdict, reason = inputs_verdict(record_range_inputs(resource), rules)
    if verdict == "fail":
        return Verdict("plausibility", "fail", reason)
    if verdict == "pass":
        return Verdict("plausibility", "pass", reason)
    return Verdict("plausibility", "uncertain", reason)


class MonolithicBaseline:
    """Baseline. One process, full record access, all four checks.

    Plausibility is the wide numeric envelope. This is the baseline the
    published results were computed against and it is unchanged; see
    :class:`MonolithicBaselineRanges` for the variant used by the controlled
    comparison.
    """

    name = "baseline"

    def evaluate(
        self, resource: dict[str, Any]
    ) -> tuple[list[Verdict], list[dict[str, Any]]]:
        """Return (verdicts, projected messages).

        Baseline applies no projection, so there are no inter-agent messages
        to score; its normalised exposure is 1.00 by construction and
        the harness records it structurally.
        """
        verdicts = [
            a1_completeness(resource),
            baseline_plausibility(resource),
            a1_consistency(resource),
            a1_timeliness(resource),
        ]
        return verdicts, []


class MonolithicBaselineRanges:
    """Baseline with the shared per-analyte range rules on plausibility.

    Identical to :class:`MonolithicBaseline` in every other respect: same three
    other checks, same full record access, same absence of a projection
    boundary. Only the plausibility rule differs.

    Pair it with an :class:`dqa.agents.Orchestrator` constructed with the same
    ``rules`` and the two systems hold identical clinical knowledge, leaving
    field visibility as the single variable between them. That is the
    comparison ``dqa.controlled`` runs and the paper's headline rests on.
    """

    name = "baseline-ranges"

    def __init__(self, rules: dict[str, RangeRule]) -> None:
        self.rules = rules

    def evaluate(
        self, resource: dict[str, Any]
    ) -> tuple[list[Verdict], list[dict[str, Any]]]:
        """Return (verdicts, projected messages); see :class:`MonolithicBaseline`."""
        verdicts = [
            a1_completeness(resource),
            baseline_plausibility_ranges(resource, self.rules),
            a1_consistency(resource),
            a1_timeliness(resource),
        ]
        return verdicts, []
