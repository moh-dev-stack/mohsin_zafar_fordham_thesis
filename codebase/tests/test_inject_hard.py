"""The hard plausibility injector: defects a crude checker cannot find.

Why this exists
---------------
:data:`dqa.inject.IMPLAUSIBLE_VALUES` substitutes one of four constants, which
makes the detection benchmark degenerate. Measured on the shipped cohorts, a
five-line set-membership test ``value in {-9999, 9999, 1e9, -1}`` scores
F1 = 1.0000 on all twelve seed-by-cohort cells and the bare ``[0, 9000]``
fallback envelope scores 1.0000, while the 441-line per-analyte rule file scores
0.9851. The benchmark ranks ignorance above knowledge.

:func:`dqa.inject.inject_plausibility_hard` derives the replacement from the
analyte's own bound in ``manifests/rules/loinc_ranges.yaml``, so these tests
pin the three properties that restore the ranking, plus the two safety
properties without which a harder benchmark would just be a wrong one:

* set-membership on the four legacy constants catches ZERO of them
  (:func:`test_set_membership_catches_nothing`);
* a per-analyte range check catches all 83 of the 83 hard defects planned on
  the three cohorts at seed 42, rate 0.30
  (:func:`test_per_analyte_ranges_catch_them`), while the crude ``[0, 9000]``
  envelope catches 5 of 83 (:func:`test_the_crude_envelope_mostly_misses`);
* every injected value really is outside the bound, so no defect is fake
  (:func:`test_every_injected_value_is_outside_its_analyte_bound`);
* the injector tracks the rule file rather than any constant of its own
  (:func:`test_values_track_the_rule_file_not_a_constant_list`), which matters
  because the bounds in the manifest are being tightened independently;
* the published legacy path is untouched (the ``test_legacy_*`` group).

Independence of the assertions
------------------------------
``test_every_injected_value_is_outside_its_analyte_bound`` resolves bounds with
a local re-implementation of the manifest's documented keying
(:func:`_bounds_from_yaml`) rather than through ``dqa.ranges``, so a bug shared
between the injector's lookup and the loader's cannot hide. The remaining tests
use ``dqa.ranges.range_verdict``, which is a different module from the one under
test and is itself pinned by ``tests/test_ranges.py``.
"""

from __future__ import annotations

import collections
import copy
import random
from typing import Any

import pytest
import yaml

from dqa.inject import (
    ELIGIBILITY,
    IMPLAUSIBLE_VALUES,
    INJECTORS,
    default_range_rules,
    hard_eligibility,
    hard_injectors,
    inject_planned,
    inject_plausibility,
    inject_plausibility_hard,
    stratified_plan,
)
from dqa.ranges import RangeRule, first_coding_code, range_verdict
from dqa.run import DATASETS, ROOT, read_cohort

SEED = 42
LIMIT = 200
RATE = 0.30

RANGES_PATH = ROOT / "manifests" / "rules" / "loinc_ranges.yaml"

# The crude envelope from the manifest's own fallback rule, written out here so
# the test does not read it from the table it is judging.
CRUDE_MIN, CRUDE_MAX = 0.0, 9000.0

# Measured on the three cohorts at seed 42, rate 0.30, over 83 hard defects:
# set membership 0/83, crude envelope 5/83 (6/83 before the FiO2 bound was
# tightened from 10000 to 100), per-analyte ranges 83/83.
#
# Only the two invariant ends of that are pinned exactly, because the middle
# one is a property of the *manifest*, which is maintained separately: every
# bound tightened there pulls injected values further inside the crude envelope
# and can only lower its catch count. Pinning 5 would turn someone else's
# improvement into this file's failure, so the guard is a ceiling.
MAX_CRUDE_SHARE = 0.15
# A floor on the total, so a collapse in rule coverage (which would silently
# turn hard defects into clean controls) fails loudly instead of leaving a
# benchmark with nothing in it.
MIN_HARD_DEFECTS = 60


@pytest.fixture(scope="module")
def rules() -> dict[str, RangeRule]:
    return default_range_rules()


def _cohorts() -> list[tuple[str, list[dict[str, Any]]]]:
    return [
        (name, read_cohort(directory, LIMIT))
        for name, directory in DATASETS.items()
        if directory.is_dir()
    ]


def _hard_plausibility_injections(
    cohort: list[dict[str, Any]],
    rules: dict[str, RangeRule],
    seed: int = SEED,
    rate: float = RATE,
):
    """Yield ``(original, injected, label)`` for every planned hard defect."""
    rng = random.Random(seed)
    plan = stratified_plan(cohort, rng, rate, eligibility=hard_eligibility(rules))
    injectors = hard_injectors(rules)
    for resource, dimension in zip(cohort, plan, strict=True):
        if dimension != "plausibility":
            continue
        injected, label = inject_planned(resource, dimension, rng, injectors=injectors)
        yield resource, injected, label


def _planned_plausibility_count(cohort: list[dict[str, Any]], rules) -> int:
    """How many hard plausibility defects the plan asks for, before injecting.

    Independent of the injector: it reads the plan, not the result.
    """
    plan = stratified_plan(
        cohort, random.Random(SEED), RATE, eligibility=hard_eligibility(rules)
    )
    return sum(1 for dimension in plan if dimension == "plausibility")


def _injected_values(rules: dict[str, RangeRule]) -> list[tuple[str, dict[str, Any]]]:
    """Every hard-injected Observation across the three cohorts, at SEED."""
    out: list[tuple[str, dict[str, Any]]] = []
    for name, cohort in _cohorts():
        for _, injected, label in _hard_plausibility_injections(cohort, rules):
            if label.is_defect:
                out.append((name, injected))
    return out


def _value(resource: dict[str, Any]) -> float:
    return float(resource["valueQuantity"]["value"])


# ----------------------------------------------------------------------
# An independent reading of the manifest, used by the ground-truth test.
# ----------------------------------------------------------------------


def _fold(unit: Any) -> str | None:
    if unit is None:
        return None
    text = str(unit).strip()
    return text.casefold() if text else None


def _bounds_from_yaml() -> dict[str, tuple[float, float]]:
    """``key -> (min, max)`` read straight from the YAML.

    A deliberate re-implementation of the keying the manifest header documents:
    every entry is registered under ``"<code>|<folded unit>"`` for its unit and
    each alias, and each code additionally gets a bare ``"<code>"`` key taken
    from its unit-less entry if it has one and otherwise from the first entry
    listed. Nothing here imports ``dqa.ranges``.
    """
    document = yaml.safe_load(RANGES_PATH.read_text())
    table: dict[str, tuple[float, float]] = {}
    bare: dict[str, tuple[float, float]] = {}
    explicit: set[str] = set()
    for entry in document["ranges"]:
        code = str(entry["loinc"]).strip()
        window = (float(entry["physiological_min"]), float(entry["physiological_max"]))
        spellings = [
            u
            for u in [entry.get("unit"), *(entry.get("unit_aliases") or [])]
            if _fold(u) is not None
        ]
        if not spellings:
            bare[code] = window
            explicit.add(code)
            continue
        for spelling in spellings:
            table[f"{code}|{_fold(spelling)}"] = window
        bare.setdefault(code, window)
    for code, window in bare.items():
        if code in explicit or code not in table:
            table[code] = window
    return table


def _resolve(resource: dict[str, Any], table: dict[str, tuple[float, float]]):
    """The bound a correct checker would apply, or ``None``. No dqa.ranges."""
    code = ""
    for coding in (resource.get("code") or {}).get("coding") or []:
        if isinstance(coding, dict) and coding.get("code") is not None:
            code = str(coding["code"])
            break
    if not code:
        return None
    folded = _fold(resource["valueQuantity"].get("unit"))
    if folded is not None and f"{code}|{folded}" in table:
        return table[f"{code}|{folded}"]
    return table.get(code)


# ----------------------------------------------------------------------
# 1. The three detection properties
# ----------------------------------------------------------------------


def test_set_membership_catches_nothing(rules: dict[str, RangeRule]) -> None:
    """The five-line cheat that scores F1 = 1.0000 on the legacy injector."""
    sentinels = set(IMPLAUSIBLE_VALUES)
    injections = _injected_values(rules)
    planned = sum(_planned_plausibility_count(cohort, rules) for _, cohort in _cohorts())
    assert len(injections) == planned, (
        f"{planned} hard defects planned but {len(injections)} realised"
    )
    assert len(injections) >= MIN_HARD_DEFECTS, (
        f"only {len(injections)} hard defects; rule coverage may have collapsed"
    )
    caught = [
        (name, _value(resource))
        for name, resource in injections
        if _value(resource) in sentinels
    ]
    assert caught == [], f"set membership caught {len(caught)}: {caught[:5]}"


def test_per_analyte_ranges_catch_them(rules: dict[str, RangeRule]) -> None:
    """The knowledge-bearing checker recovers what the cheats cannot."""
    injections = _injected_values(rules)
    missed = []
    for name, resource in injections:
        verdict, _ = range_verdict(
            first_coding_code(resource.get("code")),
            _value(resource),
            resource["valueQuantity"].get("unit"),
            rules,
        )
        if verdict != "fail":
            missed.append((name, _value(resource), verdict))
    caught = len(injections) - len(missed)
    assert caught == len(injections), (
        f"per-analyte ranges caught {caught}/{len(injections)}; missed {missed[:5]}"
    )
    assert caught >= MIN_HARD_DEFECTS, f"only {caught} defects to catch"


def test_the_crude_envelope_mostly_misses(rules: dict[str, RangeRule]) -> None:
    """A wide envelope keeps only the residue its own width earns it.

    The handful it does catch are not an oversight: they are analytes whose
    *own* physiological maximum already sits past 9000 (triglycerides at 20000,
    AST at 20000, urine volume at 30000, 24h creatinine at 10000, AFP at 1e6,
    eICU enzyme activity at 500000), so any true defect for them must exceed
    9000 too. Measured at 5 of 83; asserted as a ceiling, because tightening a
    bound in the manifest can only move it down.
    """
    injections = _injected_values(rules)
    caught = [
        (name, _value(resource))
        for name, resource in injections
        if not (CRUDE_MIN <= _value(resource) <= CRUDE_MAX)
    ]
    assert len(caught) <= MAX_CRUDE_SHARE * len(injections), (
        "the crude envelope must catch a small minority, caught "
        f"{len(caught)}/{len(injections)}: {caught}"
    )


def test_per_analyte_beats_both_cheats_on_every_cohort(
    rules: dict[str, RangeRule],
) -> None:
    """Cohort by cohort, not just pooled: the ranking must not be an average."""
    for name, cohort in _cohorts():
        membership = envelope = per_analyte = total = 0
        for _, injected, label in _hard_plausibility_injections(cohort, rules):
            if not label.is_defect:
                continue
            total += 1
            value = _value(injected)
            membership += value in set(IMPLAUSIBLE_VALUES)
            envelope += not (CRUDE_MIN <= value <= CRUDE_MAX)
            verdict, _ = range_verdict(
                first_coding_code(injected.get("code")),
                value,
                injected["valueQuantity"].get("unit"),
                rules,
            )
            per_analyte += verdict == "fail"
        assert total > 0, f"{name}: no hard defects planned"
        assert membership == 0, f"{name}: membership caught {membership}/{total}"
        assert per_analyte > envelope, (
            f"{name}: per-analyte {per_analyte} did not beat envelope {envelope}"
        )
        assert per_analyte == total, f"{name}: per-analyte {per_analyte}/{total}"


# ----------------------------------------------------------------------
# 2. The ground truth is real
# ----------------------------------------------------------------------


def test_every_injected_value_is_outside_its_analyte_bound(
    rules: dict[str, RangeRule],
) -> None:
    """No fake defects. Bounds re-read from the YAML, not via dqa.ranges."""
    table = _bounds_from_yaml()
    injections = _injected_values(rules)
    for name, resource in injections:
        window = _resolve(resource, table)
        assert window is not None, f"{name}: injected into a code with no rule"
        low, high = window
        value = _value(resource)
        assert value < low or value > high, (
            f"{name}: {value} is inside [{low}, {high}] and is not a defect"
        )


def test_no_injection_leaves_the_value_unchanged(rules: dict[str, RangeRule]) -> None:
    for name, cohort in _cohorts():
        for original, injected, label in _hard_plausibility_injections(cohort, rules):
            if not label.is_defect:
                continue
            assert _value(injected) != _value(original), f"{name}: value unchanged"
            assert label.field_path == "$.valueQuantity.value"
            assert label.dimension == "plausibility"


def test_injection_touches_only_the_value(rules: dict[str, RangeRule]) -> None:
    """Everything except ``valueQuantity.value`` survives, and the input is not
    mutated in place."""
    for name, cohort in _cohorts():
        for original, injected, label in _hard_plausibility_injections(cohort, rules):
            if not label.is_defect:
                continue
            before = copy.deepcopy(original)
            stripped_in = copy.deepcopy(injected)
            stripped_in["valueQuantity"].pop("value")
            stripped_out = copy.deepcopy(original)
            stripped_out["valueQuantity"].pop("value")
            assert stripped_in == stripped_out, f"{name}: a second field changed"
            assert original == before, f"{name}: the input resource was mutated"


def test_every_planned_hard_defect_is_realised(rules: dict[str, RangeRule]) -> None:
    """The stricter eligibility predicate must match the injector's guards."""
    for name, cohort in _cohorts():
        lost = sum(
            1
            for _, _, label in _hard_plausibility_injections(cohort, rules)
            if not label.is_defect
        )
        assert lost == 0, f"{name}: {lost} planned hard defects became controls"


# ----------------------------------------------------------------------
# 3. Derived from the rule file, not from a constant list
# ----------------------------------------------------------------------


def _observation(code: str, unit: str | None, value: float) -> dict[str, Any]:
    return {
        "resourceType": "Observation",
        "status": "final",
        "code": {"coding": [{"system": "http://loinc.org", "code": code}]},
        "valueQuantity": {"value": value, "unit": unit},
    }


def _rule(code: str, unit: str | None, low: float, high: float) -> RangeRule:
    return RangeRule(
        loinc=code,
        display=f"synthetic {code}",
        unit=unit,
        physiological_min=low,
        physiological_max=high,
        source="test fixture",
    )


def test_values_track_the_rule_file_not_a_constant_list() -> None:
    """Tighten a bound and the injected value follows it down.

    This is the property that keeps the injector correct while the manifest's
    bounds are independently tightened: nothing is hardcoded, so a narrower
    rule yields a narrower defect in the same run.
    """
    wide = {"X": _rule("X", None, 0.0, 1000.0)}
    narrow = {"X": _rule("X", None, 0.0, 10.0)}
    resource = _observation("X", None, 5.0)

    _, injected_wide, _ = _one(resource, wide)
    _, injected_narrow, _ = _one(resource, narrow)

    assert 1000.0 < injected_wide <= 1000.0 * 1.15
    assert 10.0 < injected_narrow <= 10.0 * 1.15
    assert injected_narrow < injected_wide


def _one(resource: dict[str, Any], rules: dict[str, RangeRule], seed: int = SEED):
    """Inject once with a fixed seed; returns (label, value, resource)."""
    out, label = inject_plausibility_hard(resource, random.Random(seed), rules)
    quantity = out.get("valueQuantity")
    value = quantity.get("value") if isinstance(quantity, dict) else None
    numeric = float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else value
    return label, numeric, out


def test_unit_scoped_rules_are_preferred_over_the_bare_code() -> None:
    """A code whose bound depends on the unit must use the unit's bound."""
    rules = {
        "7": _rule("7", "%", 0.0, 100.0),
        "7|%": _rule("7", "%", 0.0, 100.0),
        "7|mls": _rule("7", "mls", 0.0, 5000.0),
    }
    _, percent, _ = _one(_observation("7", "%", 50.0), rules)
    _, millilitres, _ = _one(_observation("7", "mls", 400.0), rules)
    assert 100.0 < percent <= 115.0
    assert 5000.0 < millilitres <= 5750.0


def test_the_default_fallback_is_refused() -> None:
    """An un-enumerated code becomes a clean control, never a defect.

    Injecting relative to the ``[0, 9000]`` fallback would make the defect
    catchable by the crude envelope by construction, which is the degeneracy
    this mode removes, so the injector declines instead.
    """
    rules = {
        "default": _rule("default", None, 0.0, 9000.0),
        "X": _rule("X", None, 0.0, 10.0),
    }
    resource = _observation("no-such-code", None, 5.0)
    label, value, out = _one(resource, rules)
    assert not label.is_defect
    assert label.dimension is None
    assert value == 5.0
    assert out == resource
    assert "default" in label.description or "no enumerated" in label.description


def test_a_zero_floored_analyte_never_yields_a_negative_value() -> None:
    """Below a floor of zero is a sign error, which needs no clinical range.

    Swept over many seeds because the direction is drawn from the rng.
    """
    rules = {"X": _rule("X", None, 0.0, 40.0)}
    resource = _observation("X", None, 12.0)
    for seed in range(200):
        label, value, _ = _one(resource, rules, seed)
        assert label.is_defect
        assert value > 40.0, f"seed {seed}: {value} is not above the max"


def test_a_negative_floored_analyte_can_be_pushed_below_it() -> None:
    """Base excess and DXA T-scores legitimately run negative, so both
    directions stay clinically adjacent for them."""
    rules = {"X": _rule("X", None, -30.0, 30.0)}
    resource = _observation("X", None, -2.0)
    directions = collections.Counter()
    for seed in range(200):
        label, value, _ = _one(resource, rules, seed)
        assert label.is_defect
        assert value < -30.0 or value > 30.0
        directions["below" if value < -30.0 else "above"] += 1
    assert directions["below"] > 0 and directions["above"] > 0, directions


def test_a_degenerate_rule_yields_a_clean_control_not_a_fake_defect() -> None:
    """A zero-width rule at zero offers no value that is outside it."""
    rules = {"X": _rule("X", None, 0.0, 0.0)}
    resource = _observation("X", None, 0.0)
    label, value, out = _one(resource, rules)
    assert not label.is_defect
    assert value == 0.0
    assert out == resource


def test_non_observations_and_valueless_observations_are_clean() -> None:
    rules = {"X": _rule("X", None, 0.0, 10.0)}
    for resource in (
        {"resourceType": "Encounter", "status": "finished"},
        {"resourceType": "Observation", "status": "final"},
        {"resourceType": "Observation", "valueString": "positive"},
        _observation("X", None, True),  # a bool is not a measurement
    ):
        label, _, out = _one(resource, rules)
        assert not label.is_defect, resource
        assert out == resource


# ----------------------------------------------------------------------
# 4. Determinism
# ----------------------------------------------------------------------


def test_hard_injection_is_deterministic(rules: dict[str, RangeRule]) -> None:
    for name, cohort in _cohorts():
        first = [
            _value(injected)
            for _, injected, label in _hard_plausibility_injections(cohort, rules)
            if label.is_defect
        ]
        second = [
            _value(injected)
            for _, injected, label in _hard_plausibility_injections(cohort, rules)
            if label.is_defect
        ]
        assert first == second, f"{name}: not reproducible at seed {SEED}"
        other = [
            _value(injected)
            for _, injected, label in _hard_plausibility_injections(
                cohort, rules, seed=SEED + 1
            )
            if label.is_defect
        ]
        assert other != first, f"{name}: seed has no effect"


def test_the_rule_table_is_reloaded_identically() -> None:
    """Same file, same table: the cache cannot hand back a mutated copy."""
    first = default_range_rules()
    first["X"] = _rule("X", None, 0.0, 1.0)
    second = default_range_rules()
    assert "X" not in second
    assert second == default_range_rules()


# ----------------------------------------------------------------------
# 5. The published legacy path is untouched
# ----------------------------------------------------------------------


def test_legacy_plausibility_still_uses_only_the_four_constants() -> None:
    assert IMPLAUSIBLE_VALUES == (-9999.0, 9999.0, 1e9, -1.0)
    seen: set[float] = set()
    for name, cohort in _cohorts():
        rng = random.Random(SEED)
        for resource in cohort:
            modified, label = inject_plausibility(resource, rng)
            if label.is_defect:
                seen.add(modified["valueQuantity"]["value"])
        assert seen <= set(IMPLAUSIBLE_VALUES), f"{name}: {seen}"
    assert seen == set(IMPLAUSIBLE_VALUES)


def test_legacy_injector_table_is_unchanged() -> None:
    assert INJECTORS["plausibility"] is inject_plausibility
    assert set(INJECTORS) == {
        "completeness",
        "plausibility",
        "consistency",
        "timeliness",
    }
    # hard_injectors must not mutate the shared table.
    swapped = hard_injectors()
    assert INJECTORS["plausibility"] is inject_plausibility
    assert swapped["plausibility"] is not inject_plausibility
    for dimension in ("completeness", "consistency", "timeliness"):
        assert swapped[dimension] is INJECTORS[dimension]


def test_default_plan_and_default_injection_are_unaffected() -> None:
    """The new keyword arguments default to the published behaviour."""
    for name, cohort in _cohorts():
        implicit = stratified_plan(cohort, random.Random(SEED), RATE)
        explicit = stratified_plan(
            cohort, random.Random(SEED), RATE, eligibility=ELIGIBILITY
        )
        assert implicit == explicit, name

        rng_a, rng_b = random.Random(SEED), random.Random(SEED)
        for resource, dimension in zip(cohort, implicit, strict=True):
            a = inject_planned(resource, dimension, rng_a)
            b = inject_planned(resource, dimension, rng_b, injectors=INJECTORS)
            assert a == b, name

    hard = hard_eligibility()
    for dimension in ("completeness", "consistency", "timeliness"):
        assert hard[dimension] is ELIGIBILITY[dimension]
