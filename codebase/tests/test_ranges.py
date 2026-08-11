"""Tests for the per-analyte physiological range rules.

The point of ``dqa.ranges`` is to replace Baseline's single ``[-1000, 100000]``
envelope with per-analyte survivability bounds, so these tests check the two
things that claim rests on:

* the bounds are TIGHT enough to catch the injected values the old envelope
  missed, ``9999.0`` and ``-1.0`` (``test_all_four_injected_values_fail`` and
  ``test_the_two_values_the_old_envelope_misses``);
* the bounds are LOOSE enough that no genuine value in any of the three
  cohorts is flagged, *except* the handful of real records the manifest
  documents as corrupt (``test_no_real_cohort_value_is_flagged``).

The second is the important one, and the exception in it is the whole point.
An earlier revision of this guard demanded that ZERO real values fail. That
sounds like a test and behaves like a fitting procedure: three corrupt eICU
records (FiO2 of 4000 and 10000, a Fahrenheit temperature labelled degC) could
not be made to pass without widening the bound for their entire analyte group,
so the bounds were widened and the physiology was quietly rewritten to match
the data. The guard constrained the DATA rather than the CODE.

It now constrains the code. A real value that fails is a test failure unless it
appears on ``known_corrupt_values`` in the manifest, and that list is held to
four conditions (``test_known_corrupt_allowlist_is_small_and_honest``,
``test_every_allowlist_entry_is_still_needed``):

* every entry names a cohort, code, unit, value, count and reason;
* every entry occurs in its cohort with exactly the stated count, so the list
  cannot rot or be padded with speculative exemptions;
* every entry is actually flagged by the current bounds, so an exemption cannot
  outlive the bound that needed it;
* the list is small, so "allowlist it" cannot become the cheap way out.

A new real false positive therefore fails the suite loudly, and the only two
ways to clear it are to fix the bound or to add a justified entry naming the
record. Widening a bound to silence a record is no longer one of them.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
import yaml

from dqa.inject import IMPLAUSIBLE_VALUES
from dqa.ranges import (
    DEFAULT_KEY,
    RangeRule,
    load_ranges,
    normalise_unit,
    range_verdict,
    unit_verdict,
)
from dqa.run import ROOT

RANGES_PATH = ROOT / "manifests" / "rules" / "loinc_ranges.yaml"

COHORT_DIRS = {
    "synthea": ROOT / "data" / "synthea-fhir",
    "mimic-iv-demo": ROOT / "data" / "mimic-iv-demo-fhir",
    "eicu-demo": ROOT / "data" / "eicu-demo-fhir",
}

# Potassium in serum or plasma: present in every cohort, unambiguously covered,
# and the analyte the module docstring uses as its worked example.
REPRESENTATIVE_LOINC = "2823-3"
REPRESENTATIVE_UNIT = "mmol/L"


@pytest.fixture(scope="module")
def rules() -> dict[str, RangeRule]:
    return load_ranges(RANGES_PATH)


def _numeric(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _observations(dataset_dir: Path):
    """Yield every Observation resource in a cohort directory."""
    for path in sorted(dataset_dir.glob("*.json")):
        payload = json.loads(path.read_text())
        entries = payload.get("entry", [payload]) if isinstance(payload, dict) else []
        for entry in entries:
            resource = entry.get("resource", entry) if isinstance(entry, dict) else None
            if isinstance(resource, dict) and resource.get("resourceType") == "Observation":
                yield resource


def _first_code(node: Any) -> str:
    for coding in (node or {}).get("coding") or []:
        if isinstance(coding, dict) and coding.get("code") is not None:
            return str(coding["code"])
    return ""


def _first_display(node: Any) -> str:
    for coding in (node or {}).get("coding") or []:
        if isinstance(coding, dict) and coding.get("display"):
            return str(coding["display"])
    return ""


def _numeric_values(resource: dict[str, Any]):
    """Yield (code, display, unit, value) for each numeric quantity in a resource.

    Covers the top-level ``valueQuantity`` (which is what ``inject_plausibility``
    corrupts) and ``component[*].valueQuantity`` (which Baseline also range-checks),
    flagged by ``is_top_level`` so the guard can report the two separately.
    """
    code = _first_code(resource.get("code"))
    display = _first_display(resource.get("code"))
    quantity = resource.get("valueQuantity")
    if isinstance(quantity, dict) and _numeric(quantity.get("value")):
        yield True, code, display, quantity.get("unit"), float(quantity["value"])

    for component in resource.get("component") or []:
        if not isinstance(component, dict):
            continue
        sub = component.get("valueQuantity")
        if isinstance(sub, dict) and _numeric(sub.get("value")):
            yield (
                False,
                _first_code(component.get("code")),
                _first_display(component.get("code")),
                sub.get("unit"),
                float(sub["value"]),
            )


# ----------------------------------------------------------------------
# 1. The four injected values
# ----------------------------------------------------------------------


def test_all_four_injected_values_fail(rules: dict[str, RangeRule]) -> None:
    """Every value inject.py substitutes is implausible for a covered analyte."""
    assert IMPLAUSIBLE_VALUES == (-9999.0, 9999.0, 1e9, -1.0), (
        "the injected value set changed; these bounds were chosen against it"
    )
    for injected in IMPLAUSIBLE_VALUES:
        verdict, reason = range_verdict(
            REPRESENTATIVE_LOINC, injected, REPRESENTATIVE_UNIT, rules
        )
        assert verdict == "fail", (
            f"injected value {injected!r} was not flagged for "
            f"{REPRESENTATIVE_LOINC} ({REPRESENTATIVE_UNIT}): {verdict} -- {reason}"
        )


def test_the_two_values_the_old_envelope_misses(rules: dict[str, RangeRule]) -> None:
    """9999.0 and -1.0 both sit inside Baseline's [-1000, 100000] envelope.

    They are the entire reason Baseline's plausibility recall is pinned at 0.5. This
    asserts them separately from the loop above so a regression names itself.
    """
    from dqa.baseline import PLAUSIBLE_MAX, PLAUSIBLE_MIN

    for missed in (9999.0, -1.0):
        assert PLAUSIBLE_MIN <= missed <= PLAUSIBLE_MAX, (
            f"{missed} is no longer inside Baseline's envelope; this test is stale"
        )
        verdict, reason = range_verdict(
            REPRESENTATIVE_LOINC, missed, REPRESENTATIVE_UNIT, rules
        )
        assert verdict == "fail", (
            f"{missed} must be flagged for {REPRESENTATIVE_LOINC}: {verdict} -- {reason}"
        )


def test_abnormal_but_survivable_values_pass(rules: dict[str, RangeRule]) -> None:
    """The bounds are survivability limits, not normal reference intervals."""
    survivable = [
        (REPRESENTATIVE_LOINC, 6.5, "mmol/L", "hyperkalaemia"),
        (REPRESENTATIVE_LOINC, 2.1, "mmol/L", "hypokalaemia"),
        ("2947-0", 115.0, "mmol/L", "severe hyponatraemia"),
        ("2339-0", 1200.0, "mg/dL", "hyperosmolar hyperglycaemia"),
        ("718-7", 3.2, "g/dL", "profound anaemia"),
        ("8867-4", 210.0, "/min", "supraventricular tachycardia"),
        ("8310-5", 41.5, "Cel", "severe hyperthermia"),
    ]
    for loinc, value, unit, label in survivable:
        verdict, reason = range_verdict(loinc, value, unit, rules)
        assert verdict == "pass", (
            f"{label} ({value} {unit}) is abnormal but survivable and must not "
            f"be flagged for {loinc}: {verdict} -- {reason}"
        )


def test_zero_is_admitted_but_minus_one_is_not(rules: dict[str, RangeRule]) -> None:
    """Bounds are inclusive, so a floor of 0.0 keeps genuine zeroes."""
    assert range_verdict("713-8", 0.0, "%", rules)[0] == "pass"
    assert range_verdict("713-8", -1.0, "%", rules)[0] == "fail"
    assert range_verdict("72514-3", 0.0, "{score}", rules)[0] == "pass"
    assert range_verdict("72514-3", -1.0, "{score}", rules)[0] == "fail"


# ----------------------------------------------------------------------
# 2. The false-positive guard
# ----------------------------------------------------------------------


# The manifest may exempt a real record from the guard only by naming it. Each
# entry must carry all of these; a missing field is a malformed exemption, not a
# tolerated one.
ALLOWLIST_FIELDS = ("cohort", "code", "unit", "value", "count", "why")

# An allowlist is a confession, not a mechanism. Past a handful of entries the
# honest reading is that a bound is wrong, so the cap is deliberately low enough
# that reaching it forces the question. Three entries are in use.
MAX_ALLOWLIST_ENTRIES = 8

# Enough prose to constitute a reason rather than a shrug.
MIN_WHY_CHARS = 60


def _allowlist() -> list[dict[str, Any]]:
    """The manifest's ``known_corrupt_values``, validated for shape.

    Read straight from the YAML rather than through ``load_ranges``, which by
    design ignores this key: the checker must go on flagging these records, and
    the allowlist changes only what the guard concludes from that.
    """
    document = yaml.safe_load(RANGES_PATH.read_text())
    entries = document.get("known_corrupt_values") or []
    assert isinstance(entries, list), "known_corrupt_values must be a list"
    for index, entry in enumerate(entries):
        assert isinstance(entry, dict), f"known_corrupt_values[{index}] is not a mapping"
        missing = [f for f in ALLOWLIST_FIELDS if entry.get(f) is None]
        assert not missing, (
            f"known_corrupt_values[{index}] is missing {missing}; an exemption "
            f"must name the cohort, code, unit, value, count and reason"
        )
    return entries


def _allowlist_key(entry: dict[str, Any]) -> tuple[str, str, str | None, float]:
    return (
        str(entry["cohort"]),
        str(entry["code"]),
        normalise_unit(entry["unit"]),
        float(entry["value"]),
    )


def _observed_key(cohort: str, code: str, unit: Any, value: float):
    return (cohort, code, normalise_unit(unit), float(value))


def test_no_real_cohort_value_is_flagged(rules: dict[str, RangeRule], capsys) -> None:
    """No genuine cohort value may be flagged unless the manifest names it.

    Sweeps every Observation in all three cohorts. A failure here means either a
    bound is wrong or a genuinely corrupt record has appeared that nobody has
    documented. It does NOT mean the bound should be widened, and the assertion
    message says so, because widening is how this file acquired the defect the
    allowlist exists to undo.
    """
    allowed = {_allowlist_key(e) for e in _allowlist()}
    unexplained: list[str] = []
    excused: Counter[tuple[str, str, str | None, float]] = Counter()
    uncheckable: list[str] = []
    top_level = components = 0
    resolved_by_default = 0
    per_cohort: Counter[str] = Counter()

    for cohort, directory in COHORT_DIRS.items():
        assert directory.is_dir(), f"missing cohort directory {directory}"
        for resource in _observations(directory):
            for is_top, code, display, unit, value in _numeric_values(resource):
                if is_top:
                    top_level += 1
                    per_cohort[cohort] += 1
                else:
                    components += 1

                verdict, reason = range_verdict(code, value, unit, rules)
                if verdict == "fail":
                    key = _observed_key(cohort, code, unit, value)
                    if key in allowed:
                        excused[key] += 1
                    else:
                        unexplained.append(
                            f"{cohort} code={code!r} ({display!r}) value={value!r} "
                            f"unit={unit!r} -- {reason}"
                        )
                if verdict == "not_applicable":
                    uncheckable.append(
                        f"{cohort} code={code!r} value={value!r} unit={unit!r} "
                        f"was not checkable at all -- {reason}"
                    )
                # Track how often we lean on the fallback rather than a real rule.
                scoped = code if normalise_unit(unit) is None else (
                    f"{code}|{normalise_unit(unit)}"
                )
                if scoped not in rules and code not in rules:
                    resolved_by_default += 1

    total = top_level + components
    with capsys.disabled():
        print(
            f"\n  false-positive guard: {total} real numeric values checked "
            f"({top_level} Observation.valueQuantity, {components} component "
            f"valueQuantity) across {len(COHORT_DIRS)} cohorts; "
            f"{dict(per_cohort)}; {resolved_by_default} fell back to the "
            f"{DEFAULT_KEY!r} rule; {sum(excused.values())} flagged rows excused "
            f"by {len(excused)} documented known_corrupt_values entries; "
            f"{len(unexplained)} unexplained."
        )

    assert top_level > 50_000, f"guard swept only {top_level} top-level values"
    assert components > 5_000, f"guard swept only {components} component values"
    assert not uncheckable, (
        f"{len(uncheckable)} real value(s) resolved to no rule at all:\n  "
        + "\n  ".join(uncheckable[:25])
    )
    assert not unexplained, (
        f"{len(unexplained)} genuine cohort value(s) were flagged as implausible "
        f"and are not on the manifest's known_corrupt_values list.\n"
        f"Do NOT fix this by widening a bound: that is the defect this guard was "
        f"rewritten to prevent, and it costs the checker every value between the "
        f"old bound and the new one. Either the bound is wrong on clinical "
        f"grounds and should be corrected, or the record is corrupt and should be "
        f"named in known_corrupt_values with its cohort, code, unit, value, count "
        f"and reason.\n  " + "\n  ".join(unexplained[:25])
    )


def test_known_corrupt_allowlist_is_small_and_honest() -> None:
    """The escape hatch must stay narrow, specific and argued.

    Every condition here exists to stop the allowlist becoming the new place to
    hide inconvenient data: a wildcard, an undated pile of entries, or a
    one-word excuse would each restore the failure mode by another route.
    """
    entries = _allowlist()
    assert entries, "the manifest must document its known-corrupt records explicitly"
    assert len(entries) <= MAX_ALLOWLIST_ENTRIES, (
        f"{len(entries)} allowlisted records exceeds the cap of "
        f"{MAX_ALLOWLIST_ENTRIES}; past a handful, the honest conclusion is that "
        f"a bound is wrong, not that the cohort is"
    )

    keys = [_allowlist_key(e) for e in entries]
    duplicates = [k for k, n in Counter(keys).items() if n > 1]
    assert not duplicates, f"duplicate allowlist entries: {duplicates}"

    for entry in entries:
        label = f"{entry['cohort']} {entry['code']} {entry['unit']!r} {entry['value']}"
        assert entry["cohort"] in COHORT_DIRS, f"{label}: unknown cohort"
        # A value must be one specific number: no ranges, no wildcards, no
        # "anything above". An exemption that covers an interval is a bound.
        assert isinstance(entry["value"], (int, float)) and not isinstance(
            entry["value"], bool
        ), f"{label}: value must be a single number, not a range or a pattern"
        assert isinstance(entry["count"], int) and entry["count"] > 0, (
            f"{label}: count must be a positive integer"
        )
        assert len(str(entry["why"]).strip()) >= MIN_WHY_CHARS, (
            f"{label}: 'why' must argue why the record cannot be a measurement, "
            f"not merely assert it"
        )


def test_every_allowlist_entry_is_still_needed(rules: dict[str, RangeRule]) -> None:
    """Each exemption must match reality: right count, and actually flagged.

    This is what keeps the allowlist from rotting. An entry whose record has
    left the cohort, whose count has drifted, or whose bound has since been
    widened to admit it, is dead weight that would silently excuse a future
    false positive at the same coordinates.
    """
    entries = _allowlist()
    observed: Counter[tuple[str, str, str | None, float]] = Counter()
    for cohort, directory in COHORT_DIRS.items():
        for resource in _observations(directory):
            for _is_top, code, _display, unit, value in _numeric_values(resource):
                observed[_observed_key(cohort, code, unit, value)] += 1

    problems: list[str] = []
    for entry in entries:
        key = _allowlist_key(entry)
        label = f"{entry['cohort']} code={entry['code']} unit={entry['unit']!r} value={entry['value']}"
        actual = observed.get(key, 0)
        if actual == 0:
            problems.append(f"{label}: no longer occurs in the cohort; delete the entry")
            continue
        if actual != int(entry["count"]):
            problems.append(
                f"{label}: manifest says count={entry['count']}, cohort has {actual}"
            )
        verdict, reason = range_verdict(
            str(entry["code"]), float(entry["value"]), entry["unit"], rules
        )
        if verdict != "fail":
            problems.append(
                f"{label}: is no longer flagged ({verdict}), so the exemption is "
                f"dead weight and hides a future false positive here -- {reason}"
            )
    assert not problems, "stale known_corrupt_values entries:\n  " + "\n  ".join(problems)


def test_the_documented_artefacts_are_flagged_not_legislated_for(
    rules: dict[str, RangeRule],
) -> None:
    """The two artefacts that forced the old widening now fail the check.

    Named separately from the sweep so a regression identifies itself. If either
    bound drifts back out, this fails with the analyte in the message rather
    than the guard silently passing on a wider rule.
    """
    # A fraction cannot exceed 100%, whatever eICU recorded.
    for corrupt in (4000.0, 10000.0, 9999.0, 101.0):
        verdict, reason = range_verdict("7", corrupt, "%", rules)
        assert verdict == "fail", f"eICU FiO2 {corrupt} must be flagged: {reason}"
    for genuine in (0.5, 21.0, 100.0):
        assert range_verdict("7", genuine, "%", rules)[0] == "pass", genuine

    # 99.8 degC is a Fahrenheit reading wearing a Celsius unit.
    verdict, reason = range_verdict("7", 99.8, "&#176;C", rules)
    assert verdict == "fail", f"eICU 99.8 degC must be flagged: {reason}"
    for genuine in (37.0, 37.2, 41.5):
        assert range_verdict("7", genuine, "&#176;C", rules)[0] == "pass", genuine


def test_no_bound_was_set_by_the_data_it_judges(rules: dict[str, RangeRule]) -> None:
    """A bound must not sit exactly on an observed cohort value.

    The signature of a fitted ceiling is that it coincides exactly with the
    largest thing in the data, which is how ``7|%`` came to read 10000.
    Coincidence is not proof, so this checks only for exact equality between a
    ``physiological_max`` and a real value at that key, which is what fitting
    produces. Floors are not checked: a floor of 0.0 meeting a genuine zero is
    ordinary, not evidence of anything.
    """
    suspects: list[str] = []
    for cohort, directory in COHORT_DIRS.items():
        for resource in _observations(directory):
            for _is_top, code, display, unit, value in _numeric_values(resource):
                folded = normalise_unit(unit)
                scoped = code if folded is None else f"{code}|{folded}"
                rule = rules.get(scoped) or rules.get(code)
                if rule is None:
                    continue
                if value == rule.physiological_max:
                    # A definitional ceiling legitimately coincides with real
                    # data (100% saturation, a pain score of 10, a presence flag
                    # of 1). Fitting shows up on ceilings that are not
                    # definitions, so those are the ones worth suspecting.
                    if rule.source.startswith(("definitional", "instrument definition")):
                        continue
                    suspects.append(
                        f"{cohort} {code} ({display!r}) {unit!r} value={value} "
                        f"equals physiological_max -- source: {rule.source[:80]}"
                    )
    assert not suspects, (
        "these bounds sit exactly on a real cohort value, which is what fitting "
        "a bound to its own data looks like:\n  " + "\n  ".join(sorted(set(suspects))[:25])
    )


def test_every_real_value_resolves_to_a_specific_rule(rules: dict[str, RangeRule]) -> None:
    """The default is a backstop, not the workhorse: coverage should be total."""
    uncovered: Counter[tuple[str, str]] = Counter()
    checked = 0
    for directory in COHORT_DIRS.values():
        for resource in _observations(directory):
            for is_top, code, display, unit, _value in _numeric_values(resource):
                if not is_top:
                    continue
                checked += 1
                folded = normalise_unit(unit)
                scoped = code if folded is None else f"{code}|{folded}"
                if scoped not in rules and code not in rules:
                    uncovered[(code, display)] += 1

    assert checked > 50_000
    assert not uncovered, (
        "these Observation codes have no rule and fall through to the default: "
        + ", ".join(f"{code} ({display}) x{n}" for (code, display), n in uncovered.most_common(20))
    )


# ----------------------------------------------------------------------
# 3. Unit consistency
# ----------------------------------------------------------------------


def test_unit_verdict_flags_a_contradictory_unit(rules: dict[str, RangeRule]) -> None:
    """Potassium reported in mg/dL contradicts the mmol/L the code implies."""
    verdict, reason = unit_verdict(REPRESENTATIVE_LOINC, "mg/dL", rules)
    assert verdict == "fail", reason
    assert "mg/dL" in reason and "mmol/L" in reason


def test_unit_verdict_passes_the_matching_unit(rules: dict[str, RangeRule]) -> None:
    verdict, reason = unit_verdict(REPRESENTATIVE_LOINC, "mmol/L", rules)
    assert verdict == "pass", reason


def test_unit_verdict_accepts_declared_aliases_and_casing(
    rules: dict[str, RangeRule],
) -> None:
    """mEq/L is the same quantity as mmol/L for a monovalent ion."""
    assert unit_verdict(REPRESENTATIVE_LOINC, "mEq/L", rules)[0] == "pass"
    assert unit_verdict(REPRESENTATIVE_LOINC, "  MMOL/L ", rules)[0] == "pass"


def test_unit_verdict_is_not_applicable_without_something_to_compare(
    rules: dict[str, RangeRule],
) -> None:
    assert unit_verdict(REPRESENTATIVE_LOINC, None, rules)[0] == "not_applicable"
    assert unit_verdict(REPRESENTATIVE_LOINC, "  ", rules)[0] == "not_applicable"
    assert unit_verdict("", "mmol/L", rules)[0] == "not_applicable"
    # A code with no rule at all cannot have its unit contradicted.
    assert unit_verdict("99999-9", "mmol/L", rules)[0] == "not_applicable"
    # A code whose rule declares no unit likewise.
    assert unit_verdict("51237", "anything", rules)[0] == "not_applicable"


def test_no_real_cohort_unit_is_flagged_for_a_covered_code(
    rules: dict[str, RangeRule],
) -> None:
    """Units actually used in the cohorts are declared by their rules.

    This is what makes ``unit_verdict`` usable as a live check rather than a
    demonstration: if the cohorts' own spellings failed it, every real record
    would be a false positive.
    """
    offenders: Counter[tuple[str, str]] = Counter()
    for directory in COHORT_DIRS.values():
        for resource in _observations(directory):
            for is_top, code, _display, unit, _value in _numeric_values(resource):
                if not is_top:
                    continue
                if unit_verdict(code, unit, rules)[0] == "fail":
                    offenders[(code, str(unit))] += 1
    assert not offenders, (
        "these real (code, unit) pairs are rejected by unit_verdict: "
        + ", ".join(f"{c} in {u!r} x{n}" for (c, u), n in offenders.most_common(20))
    )


# ----------------------------------------------------------------------
# 4. Fallback and manifest integrity
# ----------------------------------------------------------------------


def test_unknown_code_falls_back_to_default(rules: dict[str, RangeRule]) -> None:
    """An unlisted code uses the default rule instead of raising."""
    assert DEFAULT_KEY in rules, "the manifest must define a default rule"
    unknown = "00000-0"
    assert unknown not in rules

    verdict, reason = range_verdict(unknown, 42.0, "mg/dL", rules)
    assert verdict == "pass", reason
    assert "default" in reason

    for injected in (-9999.0, 9999.0, 1e9, -1.0):
        verdict, reason = range_verdict(unknown, injected, "mg/dL", rules)
        assert verdict == "fail", (
            f"the default rule must still catch {injected!r}: {verdict} -- {reason}"
        )


def test_missing_default_yields_not_applicable_rather_than_raising() -> None:
    without_default = {
        k: v for k, v in load_ranges(RANGES_PATH).items() if k != DEFAULT_KEY
    }
    verdict, reason = range_verdict("00000-0", 42.0, "mg/dL", without_default)
    assert verdict == "not_applicable"
    assert "no default" in reason


def test_non_numeric_values_are_not_applicable(rules: dict[str, RangeRule]) -> None:
    for bad in (None, "5.0", float("nan"), float("inf"), float("-inf"), True):
        verdict, _ = range_verdict(REPRESENTATIVE_LOINC, bad, "mmol/L", rules)
        assert verdict == "not_applicable", f"{bad!r} should not be checkable"


def test_rules_are_well_formed(rules: dict[str, RangeRule]) -> None:
    assert len(rules) > 200
    for key, rule in rules.items():
        assert isinstance(rule, RangeRule)
        assert rule.physiological_min <= rule.physiological_max, key
        assert rule.source and rule.source != "unspecified", (
            f"rule {key} must cite where its bound came from"
        )
        assert rule.display, f"rule {key} must carry a display name"


def test_bounds_are_tighter_than_the_a1_envelope(rules: dict[str, RangeRule]) -> None:
    """Every analyte rule must actually narrow something.

    A rule as wide as ``[-1000, 100000]`` would reproduce the artefact this
    module exists to remove, so nothing may be that wide except the deliberately
    documented exceptions.
    """
    from dqa.baseline import PLAUSIBLE_MAX, PLAUSIBLE_MIN

    too_wide = [
        key
        for key, rule in rules.items()
        if rule.physiological_min <= PLAUSIBLE_MIN and rule.physiological_max >= PLAUSIBLE_MAX
    ]
    # Creatine kinase, hs-troponin, ferritin and AFP genuinely span this range;
    # their floor is 0.0, so none of them qualifies as "as wide as Baseline".
    assert not too_wide, f"rules no tighter than Baseline's envelope: {too_wide}"


def test_checks_are_deterministic(rules: dict[str, RangeRule]) -> None:
    """Pure functions: repeated calls and a reload give identical answers."""
    reloaded = load_ranges(RANGES_PATH)
    cases = [
        (REPRESENTATIVE_LOINC, 4.2, "mmol/L"),
        (REPRESENTATIVE_LOINC, 9999.0, "mmol/L"),
        ("7", 10000.0, "%"),
        ("00000-0", -1.0, None),
    ]
    for loinc, value, unit in cases:
        first = range_verdict(loinc, value, unit, rules)
        assert first == range_verdict(loinc, value, unit, rules)
        assert first == range_verdict(loinc, value, unit, reloaded)
        assert unit_verdict(loinc, unit, rules) == unit_verdict(loinc, unit, reloaded)


def test_malformed_manifest_is_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"

    bad.write_text("version: 1\nranges:\n  - display: no code\n    physiological_min: 0\n"
                   "    physiological_max: 1\n")
    with pytest.raises(ValueError, match="no 'loinc'"):
        load_ranges(bad)

    bad.write_text('version: 1\nranges:\n  - loinc: "1-1"\n    physiological_min: 10\n'
                   "    physiological_max: 1\n")
    with pytest.raises(ValueError, match="above"):
        load_ranges(bad)

    bad.write_text('version: 1\nranges:\n  - loinc: "1-1"\n    unit: "mg/dL"\n'
                   "    physiological_min: 0\n    physiological_max: 1\n"
                   '  - loinc: "1-1"\n    unit: "MG/DL"\n'
                   "    physiological_min: 0\n    physiological_max: 2\n")
    with pytest.raises(ValueError, match="duplicate"):
        load_ranges(bad)

    bad.write_text("version: 1\n")
    with pytest.raises(ValueError, match="'ranges' list"):
        load_ranges(bad)


def test_survey_backs_every_bound() -> None:
    """The survey artefact exists and records the extrema the bounds were set against."""
    survey_path = ROOT / "results" / "loinc_survey.json"
    assert survey_path.is_file(), "results/loinc_survey.json must be committed"
    survey = json.loads(survey_path.read_text())
    assert set(survey["cohorts_surveyed"]) == {
        "synthea-fhir",
        "mimic-iv-demo-fhir",
        "eicu-demo-fhir",
    }
    pooled = survey["all_cohorts_pooled"]["codes"]
    # Every code carrying 20 or more Observations must have a rule.
    rules = load_ranges(RANGES_PATH)
    missing = [
        code
        for code, entry in pooled.items()
        if entry["n_observations"] >= 20
        and entry["n_numeric_values"] > 0
        and code not in rules
        and not any(k.startswith(f"{code}|") for k in rules)
    ]
    assert not missing, f"codes with 20+ Observations and no rule: {missing}"
