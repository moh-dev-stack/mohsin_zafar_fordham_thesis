"""Controlled defect injection, one injector per Kahn dimension.

Injection is the ground truth the evaluation scores against. It is
deterministic given a seed, so a run is reproducible from
(dataset, seed, injection_rate) alone.

Each injector returns the modified resource plus a label. A label whose
``dimension`` is ``None`` means no defect could be injected into that
resource, and the resource is scored as a clean control.
"""

from __future__ import annotations

import copy
import math
import random
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

from dqa.manifests import Dimension
from dqa.ranges import (
    DEFAULT_KEY,
    UNIT_SEPARATOR,
    RangeRule,
    default_rules_path,
    first_coding_code,
    load_ranges,
    normalise_unit,
)

IMPLAUSIBLE_VALUES = (-9999.0, 9999.0, 1e9, -1.0)


@dataclass(frozen=True, slots=True)
class Label:
    """Ground truth for one resource."""

    dimension: Dimension | None
    field_path: str | None
    description: str

    @property
    def is_defect(self) -> bool:
        return self.dimension is not None


def _clean(resource: dict[str, Any], reason: str) -> tuple[dict[str, Any], Label]:
    return copy.deepcopy(resource), Label(None, None, reason)


def inject_completeness(
    resource: dict[str, Any], rng: random.Random
) -> tuple[dict[str, Any], Label]:
    """Drop a required field."""
    candidates = {
        "Observation": ["status", "code", "subject"],
        "Encounter": ["status", "period", "subject"],
    }.get(str(resource.get("resourceType")), [])
    if not candidates:
        return _clean(resource, "no candidate field for completeness")
    out = copy.deepcopy(resource)
    target = rng.choice(candidates)
    out.pop(target, None)
    return out, Label("completeness", target, f"removed required field {target}")


def inject_plausibility(
    resource: dict[str, Any], rng: random.Random
) -> tuple[dict[str, Any], Label]:
    """Replace a numeric value with a clinically impossible one."""
    if resource.get("resourceType") != "Observation":
        return _clean(resource, "not an Observation")
    if not isinstance(resource.get("valueQuantity"), dict):
        return _clean(resource, "no valueQuantity to corrupt")
    out = copy.deepcopy(resource)
    original = out["valueQuantity"].get("value")
    out["valueQuantity"]["value"] = float(rng.choice(IMPLAUSIBLE_VALUES))
    return out, Label(
        "plausibility",
        "$.valueQuantity.value",
        f"replaced {original} with implausible value",
    )


def inject_consistency(
    resource: dict[str, Any], rng: random.Random
) -> tuple[dict[str, Any], Label]:
    """Break the Encounter period invariant by ending before starting."""
    if resource.get("resourceType") != "Encounter":
        return _clean(resource, "not an Encounter")
    period = resource.get("period")
    if not isinstance(period, dict) or "start" not in period:
        return _clean(resource, "no encounter period to corrupt")
    try:
        start = datetime.fromisoformat(str(period["start"]).replace("Z", "+00:00"))  # noqa: FURB162
    except ValueError:
        return _clean(resource, "unparseable encounter start")
    out = copy.deepcopy(resource)
    out["period"]["end"] = (start - timedelta(hours=rng.randint(1, 48))).isoformat()
    return out, Label("consistency", "$.period.end", "set encounter end before start")


# Fixed far-future anchor so injected timestamps are a pure function of the
# seed. Anchoring to the wall clock would break bit-reproducibility between
# runs; a twenty-second-century date fails any future-timestamp rule for
# the useful life of this codebase.
FUTURE_ANCHOR = datetime(2126, 1, 1, tzinfo=UTC)


def inject_timeliness(
    resource: dict[str, Any], rng: random.Random
) -> tuple[dict[str, Any], Label]:
    """Push a timestamp implausibly far into the future.

    On an Encounter this moves ``period.start`` and leaves ``period.end`` where
    it was, so on any Encounter that carries both, the injected record also
    violates the ordering invariant :func:`dqa.baseline.a1_consistency` checks.
    One injection would then be two defects, and the row carries one label, so
    the consistency dimension would take a false positive it did not earn.

    Left as it is, because changing it changes the published ground truth. It is
    inert on these cohorts and measured rather than assumed: at seed 42, rate
    0.30, stratified allocation, three Encounters across all three cohorts are
    injected this way and **none** is flagged for consistency, because none of
    them carries a ``period.end``. ``tests/test_inject_stratified.py`` pins that
    count, so a cohort change that would make the collision live fails the suite
    instead of quietly contaminating the consistency column.
    """
    resource_type = resource.get("resourceType")
    if resource_type == "Observation" and "effectiveDateTime" in resource:
        target = "effectiveDateTime"
    elif resource_type == "Encounter" and isinstance(resource.get("period"), dict):
        target = "period.start"
    else:
        return _clean(resource, "no timestamp to corrupt")

    out = copy.deepcopy(resource)
    stamp = (FUTURE_ANCHOR + timedelta(days=rng.randint(30, 3650))).isoformat()
    if "." in target:
        parent, leaf = target.split(".", 1)
        out[parent][leaf] = stamp
        path = f"$.{parent}.{leaf}"
    else:
        out[target] = stamp
        path = f"$.{target}"
    return out, Label("timeliness", path, "set timestamp far in future")


INJECTORS: dict[Dimension, Callable[..., tuple[dict[str, Any], Label]]] = {
    "completeness": inject_completeness,
    "plausibility": inject_plausibility,
    "consistency": inject_consistency,
    "timeliness": inject_timeliness,
}


def inject_random(
    resource: dict[str, Any], rng: random.Random
) -> tuple[dict[str, Any], Label]:
    """Apply one uniformly chosen injector.

    Legacy allocation, retained unchanged so the published results stay
    reproducible. It picks a dimension before looking at the resource, so
    an injector that cannot apply returns a clean control and the budget
    is spent for nothing. See :func:`stratified_plan` for why that
    matters and what replaces it.
    """
    dimension = rng.choice(list(INJECTORS))
    return INJECTORS[dimension](resource, rng)


# --------------------------------------------------------------- stratified

# Eligibility predicates mirror each injector's own guards. They exist so the
# allocator can see, before spending any budget, which resources a given
# injector could actually corrupt. Keeping them beside the injectors is the
# only way they stay in step; a drift between the two shows up as a planned
# defect that silently becomes a clean control, which is exactly the failure
# the legacy allocator has.


def _eligible_completeness(resource: dict[str, Any]) -> bool:
    candidates = {
        "Observation": ("status", "code", "subject"),
        "Encounter": ("status", "period", "subject"),
    }.get(str(resource.get("resourceType")), ())
    # The injector pops a field; if none is present it would return a clean
    # control while still claiming the slot, which is the waste this allocator
    # exists to remove.
    return any(field in resource for field in candidates)


def _eligible_plausibility(resource: dict[str, Any]) -> bool:
    if resource.get("resourceType") != "Observation":
        return False
    quantity = resource.get("valueQuantity")
    return isinstance(quantity, dict) and "value" in quantity


def _eligible_consistency(resource: dict[str, Any]) -> bool:
    if resource.get("resourceType") != "Encounter":
        return False
    period = resource.get("period")
    if not isinstance(period, dict) or "start" not in period:
        return False
    try:
        datetime.fromisoformat(str(period["start"]).replace("Z", "+00:00"))  # noqa: FURB162
    except ValueError:
        return False
    return True


def _eligible_timeliness(resource: dict[str, Any]) -> bool:
    resource_type = resource.get("resourceType")
    if resource_type == "Observation":
        return "effectiveDateTime" in resource
    if resource_type == "Encounter":
        return isinstance(resource.get("period"), dict)
    return False


ELIGIBILITY: dict[Dimension, Callable[[dict[str, Any]], bool]] = {
    "completeness": _eligible_completeness,
    "plausibility": _eligible_plausibility,
    "consistency": _eligible_consistency,
    "timeliness": _eligible_timeliness,
}

# Default allocation weights. Plausibility carries the primary endpoint and is
# the only dimension whose score moves between systems, so it takes the largest
# share. Consistency is weighted up relative to its eligible population because
# under uniform allocation it receives no defects at all.
DEFAULT_WEIGHTS: dict[Dimension, float] = {
    "completeness": 0.20,
    "plausibility": 0.45,
    "consistency": 0.15,
    "timeliness": 0.20,
}


def eligible_indices(
    cohort: list[dict[str, Any]],
    eligibility: Mapping[Dimension, Callable[[dict[str, Any]], bool]] | None = None,
) -> dict[Dimension, list[int]]:
    """Which cohort positions each injector could actually corrupt.

    ``eligibility`` defaults to :data:`ELIGIBILITY`, the published predicates.
    :func:`hard_eligibility` supplies the stricter plausibility predicate the
    near-bound injector needs.
    """
    table = ELIGIBILITY if eligibility is None else eligibility
    return {
        dimension: [i for i, r in enumerate(cohort) if predicate(r)]
        for dimension, predicate in table.items()
    }


def stratified_plan(
    cohort: list[dict[str, Any]],
    rng: random.Random,
    injection_rate: float,
    weights: dict[Dimension, float] | None = None,
    eligibility: Mapping[Dimension, Callable[[dict[str, Any]], bool]] | None = None,
) -> list[Dimension | None]:
    """Allocate the defect budget across dimensions before spending any of it.

    Returns one entry per cohort position: the dimension to inject there, or
    ``None`` for a clean control. Every planned position is eligible for its
    dimension, so a planned defect is always realised and the realised count
    per dimension is knowable in advance rather than discovered afterwards.

    Budget is apportioned by ``weights`` and then capped by eligibility. Any
    share a dimension cannot absorb is redistributed to dimensions that still
    have capacity, so the total realised count matches the requested rate
    whenever the cohort can support it.

    ``eligibility`` defaults to :data:`ELIGIBILITY`, the published predicates,
    so the default plan is unchanged. Pass :func:`hard_eligibility` when the
    plan will be executed with :func:`hard_injectors`, so the "every planned
    defect is realised" property holds for the near-bound injector too.
    """
    predicates = ELIGIBILITY if eligibility is None else eligibility
    # Fill every dimension so a partial dict cannot KeyError in the sorts
    # below, and reject unknown keys rather than silently ignoring them.
    supplied = dict(weights or DEFAULT_WEIGHTS)
    unknown = set(supplied) - set(predicates)
    if unknown:
        raise ValueError(f"unknown dimensions in weights: {sorted(unknown)}")
    if any(w < 0 for w in supplied.values()):
        raise ValueError("weights must be non-negative")
    total_weight = sum(supplied.values())
    if total_weight <= 0:
        raise ValueError("weights must sum to a positive value")
    # Normalise, so a caller-supplied set that does not sum to 1 cannot push
    # the quotas past the budget.
    supplied = {d: w / total_weight for d, w in supplied.items()}
    weights = {d: 0.0 for d in predicates} | supplied
    # A negative rate would make budget negative, and a negative budget makes
    # every guard below misbehave, so clamp before anything else uses it.
    budget = max(0, round(len(cohort) * max(0.0, injection_rate)))
    eligible = eligible_indices(cohort, predicates)

    # Shuffle each pool independently so allocation order does not correlate
    # with cohort order, which is Patients first and then events.
    pools: dict[Dimension, list[int]] = {}
    for dimension, indices in eligible.items():
        shuffled = list(indices)
        rng.shuffle(shuffled)
        pools[dimension] = shuffled

    quota = {d: int(budget * w) for d, w in weights.items()}
    # max(0, ...) matters: if the weights sum above 1 the remainder is negative,
    # and a negative slice bound is not an empty slice, it is all-but-the-last,
    # which would top up quotas that are already over budget.
    remainder = max(0, budget - sum(quota.values()))
    for dimension in sorted(quota, key=lambda d: (-weights[d], d))[:remainder]:
        quota[dimension] += 1

    taken: dict[int, Dimension] = {}

    def _draw(dimension: Dimension, want: int) -> int:
        """Assign up to ``want`` unclaimed eligible positions. Returns shortfall."""
        for index in pools[dimension]:
            if want <= 0:
                break
            if index not in taken:
                taken[index] = dimension
                want -= 1
        return want

    shortfall = 0
    for dimension in sorted(quota, key=lambda d: (len(pools[d]), d)):
        shortfall += _draw(dimension, quota[dimension])

    # Redistribute what the scarce dimensions could not absorb.
    for dimension in sorted(pools, key=lambda d: (-weights[d], d)):
        if shortfall == 0:
            break
        shortfall = _draw(dimension, shortfall)

    return [taken.get(i) for i in range(len(cohort))]


def inject_planned(
    resource: dict[str, Any],
    dimension: Dimension | None,
    rng: random.Random,
    injectors: Mapping[Dimension, Callable[..., tuple[dict[str, Any], Label]]] | None = None,
) -> tuple[dict[str, Any], Label]:
    """Apply the dimension chosen by :func:`stratified_plan`.

    ``injectors`` defaults to :data:`INJECTORS`, the published behaviour. Pass
    :func:`hard_injectors` to swap the sentinel-based plausibility injector for
    the near-bound one; every other dimension is unaffected.
    """
    if dimension is None:
        return _clean(resource, "clean control")
    table = INJECTORS if injectors is None else injectors
    return table[dimension](resource, rng)


# ------------------------------------------------------- hard plausibility
#
# WHY THIS MODE EXISTS
# --------------------
# :data:`IMPLAUSIBLE_VALUES` substitutes one of four constants, so the
# detection task it poses is degenerate: a five-line set-membership test
# ``value in {-9999, 9999, 1e9, -1}`` scores F1 = 1.0000 on every seed-by-cohort
# cell, and the bare ``[0, 9000]`` fallback envelope scores 1.0000 as well,
# while the per-analyte rule file scores 0.9907. The benchmark rewards
# ignorance and cannot rank detectors.
#
# The hard mode draws the replacement value from the analyte's OWN bound in
# ``manifests/rules/loinc_ranges.yaml``, a small relative margin beyond
# ``physiological_min`` or ``physiological_max``. The result is a number that
# looks like a reading for that analyte (a heart rate of 389/min, a haemoglobin
# of 27.4 g/dL) but cannot have come from a living patient. No constant list is
# hardcoded here: the bounds are read from the rule file at runtime, so
# tightening a bound in the manifest tightens the injector in the same step.
#
# THREE PROPERTIES, BY CONSTRUCTION
#   * a set-membership test on the four legacy constants catches none of these,
#     because a candidate equal to a legacy constant is rejected outright;
#   * a crude wide envelope mostly cannot catch them, because the value sits
#     just outside a per-analyte bound that is itself far inside any crude
#     envelope. The exceptions are real and are not papered over: analytes whose
#     own bound reaches past the crude envelope (eICU FiO2, max 10000) or below
#     zero (base excess, DXA T-score) yield values a ``[0, 9000]`` envelope does
#     catch. That residue is measured in ``tests/test_inject_hard.py`` rather
#     than assumed away;
#   * a correct per-analyte check catches them, because every emitted value is
#     verified strictly outside the bound it was derived from before it is
#     returned.

# Relative margin beyond the bound, as a fraction of the bound's magnitude.
# Wide enough that float rounding cannot pull the value back inside, narrow
# enough that the result stays in the analyte's own order of magnitude: at the
# top of the range a heart rate bound of 350/min yields 357 to 402/min.
HARD_MARGIN_RANGE = (0.02, 0.15)


@lru_cache(maxsize=1)
def _cached_default_rules() -> dict[str, RangeRule]:
    root = Path(__file__).resolve().parents[2]
    return load_ranges(default_rules_path(root))


def default_range_rules() -> dict[str, RangeRule]:
    """The shipped rule table, loaded once per process.

    Returned as a fresh dict so a caller cannot mutate the cache; the
    :class:`~dqa.ranges.RangeRule` values are frozen.
    """
    return dict(_cached_default_rules())


def enumerated_rule(
    code: str, unit: str | None, rules: Mapping[str, RangeRule]
) -> RangeRule | None:
    """The most specific rule for a (code, unit) pair, ignoring the default.

    Mirrors :func:`dqa.ranges._lookup` -- unit-scoped key first, bare code key
    second -- but stops short of the ``default`` fallback, and returns ``None``
    instead. See :func:`inject_plausibility_hard` for why the fallback is
    refused rather than used.
    """
    text = str(code).strip() if code is not None else ""
    if not text or text == DEFAULT_KEY:
        return None
    folded = normalise_unit(unit)
    if folded is not None:
        scoped = f"{text}{UNIT_SEPARATOR}{folded}"
        if scoped in rules:
            return rules[scoped]
    return rules.get(text)


def _round_like_a_measurement(value: float) -> float:
    """Round to the precision an instrument would report at that magnitude.

    ``31.499999999999996`` is a giveaway that a machine generated the number;
    ``31.5`` is what a blood gas analyser prints.
    """
    magnitude = abs(value)
    if magnitude >= 100:
        digits = 0
    elif magnitude >= 10:
        digits = 1
    elif magnitude >= 1:
        digits = 2
    else:
        digits = 3
    return round(value, digits)


def _beyond(bound: float, span: float, fraction: float, sign: int) -> float | None:
    """A value ``fraction`` beyond ``bound``, in the direction of ``sign``.

    The margin scales with the bound's own magnitude so it stays in the
    analyte's units. A bound of exactly zero has no magnitude to scale, so the
    width of the plausible interval stands in for it.
    """
    scale = abs(bound) if bound != 0.0 else span
    if not math.isfinite(scale) or scale <= 0.0:
        return None
    candidate = bound + sign * scale * fraction
    if not math.isfinite(candidate):
        return None
    return candidate


def inject_plausibility_hard(
    resource: dict[str, Any],
    rng: random.Random,
    rules: Mapping[str, RangeRule] | None = None,
) -> tuple[dict[str, Any], Label]:
    """Replace a numeric value with a near-bound physiological impossibility.

    The replacement is derived from the analyte's own rule in
    ``manifests/rules/loinc_ranges.yaml``: a margin of
    :data:`HARD_MARGIN_RANGE` beyond ``physiological_min`` or
    ``physiological_max``, rounded to a plausible instrument precision. Nothing
    about the value is a constant, so a set-membership test cannot learn it and
    a bound tightened in the manifest moves the injected value with it.

    Direction is drawn from the directions that remain *clinically adjacent*:

    * above ``physiological_max`` always qualifies;
    * below ``physiological_min`` qualifies only when the result is still a
      value the analyte could in principle report -- either the floor is
      negative (base excess, a DXA T-score) or the floor is positive and the
      result stays above zero. A negative value for an analyte floored at zero
      is a *sign* error, which any non-negativity check catches without knowing
      any clinical range, and is therefore no better than a sentinel.

    THE ``default`` FALLBACK, AND WHY IT IS REFUSED
    An Observation whose code has no enumerated rule resolves, in
    :func:`dqa.ranges.range_verdict`, to the ``default`` rule -- the survey
    backstop ``[0, 9000]``. Injecting relative to that bound would produce a
    value that is a true defect but that the crude wide envelope catches by
    construction, which is the exact degeneracy this mode exists to remove:
    the defect would again be detectable without any per-analyte knowledge. So
    an un-enumerated code yields a **clean control**, with the reason recorded
    in the label, rather than a defect. The cost is a lower realised defect
    count on cohorts with poor rule coverage; the benefit is that every hard
    defect on the board discriminates a real range check from a crude one.

    Returns the resource unchanged with a non-defect :class:`Label` whenever no
    usable bound exists, whenever no direction is clinically adjacent, or
    whenever the derived value cannot be shown to be outside the bound. A fake
    defect is never emitted.
    """
    if resource.get("resourceType") != "Observation":
        return _clean(resource, "not an Observation")
    quantity = resource.get("valueQuantity")
    if not isinstance(quantity, dict) or not isinstance(quantity.get("value"), (int, float)):
        return _clean(resource, "no numeric valueQuantity to corrupt")
    if isinstance(quantity.get("value"), bool):
        return _clean(resource, "no numeric valueQuantity to corrupt")

    table = default_range_rules() if rules is None else rules
    code = first_coding_code(resource.get("code"))
    unit = quantity.get("unit")
    rule = enumerated_rule(code, unit, table)
    if rule is None:
        return _clean(
            resource,
            f"no enumerated range rule for code {code or '<missing>'}; "
            "the default fallback would yield a crudely detectable value",
        )

    low, high = rule.physiological_min, rule.physiological_max
    if not (math.isfinite(low) and math.isfinite(high)):
        return _clean(resource, f"non-finite bounds for {rule.loinc}")
    span = high - low

    # One draw, before the direction is chosen, so the candidate set the
    # direction is chosen from is itself a function of the seed alone.
    fraction = rng.uniform(*HARD_MARGIN_RANGE)
    candidates: dict[str, float] = {}
    above = _beyond(high, span, fraction, +1)
    if above is not None:
        candidates["above"] = above
    below = _beyond(low, span, fraction, -1)
    # Clinically adjacent below the floor only when the sign does not give it
    # away: a negative haemoglobin is junk on inspection, 27.4 g/dL is not.
    if below is not None and (low < 0.0 or (low > 0.0 and below > 0.0)):
        candidates["below"] = below
    if not candidates:
        return _clean(resource, f"no clinically adjacent direction for {rule.loinc}")

    original = float(quantity["value"])
    direction = rng.choice(sorted(candidates))
    raw = candidates[direction]
    rounded = _round_like_a_measurement(raw)
    # Rounding is cosmetic and must never be allowed to walk the value back
    # inside the bound, so it is verified and discarded if it did.
    value = rounded if (rounded < low or rounded > high) else raw

    # Independent verification, on the value actually being written: strictly
    # outside the bound (matching the inclusive comparison in range_verdict),
    # finite, not a legacy sentinel, and an actual change to the record.
    if not math.isfinite(value) or not (value < low or value > high):
        return _clean(resource, f"could not place a value outside {rule.loinc} bounds")
    if value in IMPLAUSIBLE_VALUES:
        return _clean(resource, "derived value collided with a legacy sentinel")
    if value == original:
        return _clean(resource, "derived value equals the original")

    out = copy.deepcopy(resource)
    out["valueQuantity"]["value"] = value
    label_bound = high if direction == "above" else low
    return out, Label(
        "plausibility",
        "$.valueQuantity.value",
        f"replaced {original} with {value:g}, {direction} the physiological "
        f"{'max' if direction == 'above' else 'min'} {label_bound:g} for "
        f"{rule.display or rule.loinc}",
    )


def _eligible_plausibility_hard(
    resource: dict[str, Any], rules: Mapping[str, RangeRule]
) -> bool:
    """Whether :func:`inject_plausibility_hard` could actually corrupt this.

    Stricter than :func:`_eligible_plausibility` by exactly the fallback rule:
    the code must resolve to an enumerated rule, since an un-enumerated one is
    deliberately left as a clean control. Mirrors the injector's own guards, for
    the same reason the other predicates do -- a drift shows up as a planned
    defect that silently becomes a control.
    """
    if not _eligible_plausibility(resource):
        return False
    quantity = resource["valueQuantity"]
    value = quantity.get("value")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    rule = enumerated_rule(
        first_coding_code(resource.get("code")), quantity.get("unit"), rules
    )
    if rule is None:
        return False
    low, high = rule.physiological_min, rule.physiological_max
    if not (math.isfinite(low) and math.isfinite(high)):
        return False
    # At least one direction must be constructible. The margin fraction is
    # positive throughout HARD_MARGIN_RANGE, so a direction that exists for one
    # fraction exists for all of them, except for the sign guard below the
    # floor, which the injector re-checks against the drawn fraction.
    span = high - low
    return _beyond(high, span, HARD_MARGIN_RANGE[0], +1) is not None or (
        low < 0.0 and _beyond(low, span, HARD_MARGIN_RANGE[0], -1) is not None
    )


def hard_eligibility(
    rules: Mapping[str, RangeRule] | None = None,
) -> dict[Dimension, Callable[[dict[str, Any]], bool]]:
    """:data:`ELIGIBILITY` with the stricter plausibility predicate bound in.

    Pass to :func:`stratified_plan` alongside :func:`hard_injectors`.
    """
    table = default_range_rules() if rules is None else rules
    return ELIGIBILITY | {
        "plausibility": lambda resource: _eligible_plausibility_hard(resource, table)
    }


def hard_injectors(
    rules: Mapping[str, RangeRule] | None = None,
) -> dict[Dimension, Callable[..., tuple[dict[str, Any], Label]]]:
    """:data:`INJECTORS` with the hard plausibility injector bound in.

    Pass the result to :func:`inject_planned`. The other three dimensions are
    the published injectors, unchanged and shared by identity.
    """
    table = default_range_rules() if rules is None else rules

    def _plausibility(
        resource: dict[str, Any], rng: random.Random
    ) -> tuple[dict[str, Any], Label]:
        return inject_plausibility_hard(resource, rng, table)

    return INJECTORS | {"plausibility": _plausibility}
