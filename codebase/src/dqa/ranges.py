"""Per-analyte physiological range rules, and the pure checks that use them.

Why this module exists
----------------------
Baseline (``dqa.baseline``) judges plausibility with one numeric envelope,
``[PLAUSIBLE_MIN, PLAUSIBLE_MAX] == [-1000, 100000]``, applied to every
analyte regardless of what is being measured. Of the four values
``dqa.inject.IMPLAUSIBLE_VALUES`` substitutes into ``valueQuantity.value``,
that envelope catches ``-9999.0`` and ``1e9`` but lets ``9999.0`` and ``-1.0``
through, because both sit comfortably inside it. Recall is therefore pinned at
exactly 0.5 and F1 at exactly 0.667 on every cohort: an arithmetic consequence
of the envelope's width, not a measurement of anything.

Meanwhile the multi-agent system judges plausibility with no reference ranges
at all, because no Observation in any of the three demo cohorts carries a
``referenceRange`` element. The two arms of the experiment therefore differ in
their *checking logic* as well as in their *privilege*, which confounds the
comparison the study is trying to make.

This module supplies the missing clinical knowledge in a form either arm can be
given: a per-analyte table of survivability bounds, plus two pure predicates
over it. Nothing here calls a model, touches the network, or reads the clock,
so both arms can be handed identical knowledge and the only difference left
between them is how much of the record they are allowed to see.

What a verdict means
--------------------
``range_verdict`` returns ``'fail'`` only for values that cannot be real
measurements. A potassium of 6.5 mmol/L is grossly abnormal but survivable and
returns ``'pass'``; the point of the table is to separate "this patient is
unwell" from "this number did not come from a patient".

The comparison is inclusive at both ends, so a rule with a floor of ``0.0``
admits a genuine zero (0% eosinophils, a pain score of 0) while rejecting
``-1.0``.

Rule keys
---------
Two of the three cohorts do not really use LOINC. MIMIC-IV emits ``d_labitems``
itemids and eICU emits ``labtypeid`` integers, yet both declare
``system=http://loinc.org``, so the code string is used verbatim as the key. The
eICU integers aggregate dozens of unrelated analytes under a single code, so a
rule may additionally be scoped by unit; see ``load_ranges``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

# Key under which the fallback rule is registered.
DEFAULT_KEY = "default"

# Separator between the code and the unit in a unit-scoped rule key. Chosen
# because it cannot occur in a LOINC code, a MIMIC itemid or a UCUM unit.
UNIT_SEPARATOR = "|"

Verdict = str  # 'pass' | 'fail' | 'not_applicable'


@dataclass(frozen=True, slots=True)
class RangeRule:
    """One analyte's survivability bounds.

    ``physiological_min`` and ``physiological_max`` are limits of credibility,
    not normal reference intervals: outside them the number cannot have come
    from a living patient.
    """

    loinc: str
    display: str
    unit: str | None
    physiological_min: float
    physiological_max: float
    source: str


def normalise_unit(unit: str | None) -> str | None:
    """Fold a unit string to its lookup form, or ``None`` if it carries none.

    Cohort units are inconsistently cased and sometimes blank
    (``'Ratio'``/``'ratio'``, ``'ng/dl'``/``'ng/dL'``, ``''``, ``' '``). A blank
    unit is treated as absent rather than as the empty unit, so an Observation
    with no unit falls through to its code-level rule.
    """
    if unit is None:
        return None
    text = str(unit).strip()
    if not text:
        return None
    return text.casefold()


def _scoped_key(loinc: str, unit: str | None) -> str:
    """Build the lookup key for a (code, unit) pair."""
    folded = normalise_unit(unit)
    if folded is None:
        return loinc
    return f"{loinc}{UNIT_SEPARATOR}{folded}"


def _require(condition: bool, message: str) -> None:
    """Reject a malformed manifest.

    A manifest that does not parse is a value problem, not a type problem: the
    file is the input being validated, so ``ValueError`` is what callers catch.
    """
    if not condition:
        raise ValueError(message)


def _as_float(raw: Any, field: str, loinc: str) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"rule {loinc!r}: {field} is not a number: {raw!r}") from exc
    if not math.isfinite(value):
        raise ValueError(f"rule {loinc!r}: {field} is not finite: {raw!r}")
    return value


def load_ranges(path: Path) -> dict[str, RangeRule]:
    """Read a range manifest into a lookup table.

    Each YAML entry is registered under ``"<loinc>|<folded unit>"`` for its
    ``unit`` and for every entry in its optional ``unit_aliases`` list. Aliases
    are alternative spellings of the *same* quantity (``'pH'`` and ``'[pH]'``,
    ``'mmol/L'`` and ``'mEq/L'`` for a monovalent ion); a genuinely different
    quantity always gets its own entry.

    Each code additionally gets a bare ``"<loinc>"`` key, used when the
    Observation carries no unit or an unrecognised one. That bare key comes from
    the entry declaring ``unit: null`` if there is one, otherwise from the first
    entry listed for the code.

    Raises ``ValueError`` on a malformed manifest: a missing bound, a
    non-numeric bound, an inverted interval, or two entries claiming the same
    (code, unit) key.
    """
    document = yaml.safe_load(Path(path).read_text())
    _require(isinstance(document, dict), f"{path}: expected a YAML mapping at the top level")
    entries = document.get("ranges")
    _require(isinstance(entries, list), f"{path}: expected a 'ranges' list")

    rules: dict[str, RangeRule] = {}
    # loinc -> rule to register under the bare code key.
    bare: dict[str, RangeRule] = {}
    # loincs whose bare key was claimed by an explicit unit-less entry.
    bare_is_explicit: set[str] = set()

    for index, entry in enumerate(entries):
        _require(isinstance(entry, dict), f"{path}: ranges[{index}] is not a mapping")
        _require("loinc" in entry, f"{path}: ranges[{index}] has no 'loinc'")
        loinc = str(entry["loinc"]).strip()
        _require(bool(loinc), f"{path}: ranges[{index}] has an empty 'loinc'")
        for bound in ("physiological_min", "physiological_max"):
            _require(bound in entry, f"{path}: rule {loinc!r} has no {bound!r}")

        low = _as_float(entry["physiological_min"], "physiological_min", loinc)
        high = _as_float(entry["physiological_max"], "physiological_max", loinc)
        _require(
            low <= high,
            f"{path}: rule {loinc!r} has physiological_min {low} above "
            f"physiological_max {high}",
        )

        raw_unit = entry.get("unit")
        canonical = str(raw_unit).strip() if normalise_unit(raw_unit) is not None else None
        rule = RangeRule(
            loinc=loinc,
            display=str(entry.get("display", "")),
            unit=canonical,
            physiological_min=low,
            physiological_max=high,
            source=str(entry.get("source", "unspecified")),
        )

        aliases = entry.get("unit_aliases") or []
        _require(isinstance(aliases, list), f"{path}: rule {loinc!r} has a non-list 'unit_aliases'")
        spellings = [u for u in [raw_unit, *aliases] if normalise_unit(u) is not None]

        if not spellings:
            # An explicitly unit-less rule: it owns the bare code key.
            _require(
                loinc not in bare_is_explicit,
                f"{path}: rule {loinc!r} declares two unit-less entries",
            )
            bare[loinc] = rule
            bare_is_explicit.add(loinc)
            continue

        for spelling in spellings:
            key = _scoped_key(loinc, spelling)
            if key in rules:
                raise ValueError(f"{path}: duplicate rule for key {key!r}")
            rules[key] = rule
        bare.setdefault(loinc, rule)

    for loinc, rule in bare.items():
        # A unit-less entry always wins the bare key over the positional default.
        if loinc in bare_is_explicit or loinc not in rules:
            rules[loinc] = rule

    return rules


def _lookup(loinc: str, unit: str | None, rules: dict[str, RangeRule]) -> tuple[RangeRule | None, str]:
    """Resolve the most specific rule for a (code, unit) pair."""
    if loinc:
        scoped = _scoped_key(loinc, unit)
        if scoped != loinc and scoped in rules:
            return rules[scoped], f"rule for {loinc} in {rules[scoped].unit}"
        if loinc in rules:
            return rules[loinc], f"rule for {loinc}"
    fallback = rules.get(DEFAULT_KEY)
    if fallback is not None:
        return fallback, "default rule"
    return None, ""


def range_verdict(
    loinc: str,
    value: float,
    unit: str | None,
    rules: dict[str, RangeRule],
) -> tuple[Verdict, str]:
    """Judge whether a numeric observation value is physiologically possible.

    Returns ``(verdict, reason)``. ``verdict`` is:

    * ``'fail'`` -- the value lies outside the analyte's survivability bounds,
      so it cannot be a real measurement;
    * ``'pass'`` -- the value is within bounds. It may still be grossly
      abnormal; that is a clinical question, not a data-quality one;
    * ``'not_applicable'`` -- the value is not a finite number, or no rule and
      no default applies.

    Pure: same inputs, same output, always.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "not_applicable", f"value is not numeric: {value!r}"
    numeric = float(value)
    if not math.isfinite(numeric):
        return "not_applicable", f"value is not finite: {value!r}"

    code = str(loinc).strip() if loinc is not None else ""
    rule, provenance = _lookup(code, unit, rules)
    if rule is None:
        return (
            "not_applicable",
            f"no range rule for code {code or '<missing>'} and no default rule",
        )

    label = rule.display or rule.loinc
    window = f"[{rule.physiological_min:g}, {rule.physiological_max:g}]"
    if numeric < rule.physiological_min or numeric > rule.physiological_max:
        reason = (
            f"{numeric:g} is outside the physiological range {window} "
            f"for {label} ({provenance}; source: {rule.source})"
        )
        return "fail", reason
    reason = (
        f"{numeric:g} is within the physiological range {window} "
        f"for {label} ({provenance})"
    )
    return "pass", reason


def unit_verdict(
    loinc: str,
    unit: str | None,
    rules: dict[str, RangeRule],
) -> tuple[Verdict, str]:
    """Judge whether the reported unit contradicts the unit the code implies.

    This is a consistency check rather than a range check: a potassium reported
    in mg/dL is a real defect even when the number itself is unremarkable, and
    no value-only rule can see it.

    **Not wired into either arm, deliberately.** Neither
    :func:`dqa.agents.check_plausibility_ranges` nor
    :func:`dqa.baseline.baseline_plausibility_ranges` calls this; both go straight to
    :func:`inputs_verdict` and therefore to :func:`range_verdict` alone. The
    reason is that ``dqa.inject`` corrupts values and never units, so a
    unit-mismatch check would contribute no true positives to any cell and could
    only add false positives on real records whose units this table happens not
    to list. Adding a check that can only lose points is not a neutral act in a
    study whose whole argument is that both arms must run the *same* check.

    It is kept, and tested against every real (code, unit) pair in all three
    cohorts, because it is the check a deployment would want and because that
    test is what establishes the rule table's unit coverage. If the injector
    ever gains a unit-corruption dimension, wire this into both arms at once.

    Returns ``(verdict, reason)``. ``verdict`` is:

    * ``'fail'`` -- the code has declared units and the observed one is not
      among them;
    * ``'pass'`` -- the observed unit is one the code declares;
    * ``'not_applicable'`` -- there is no code, no rule for the code, the rule
      declares no unit, or the Observation carries no unit to check.

    Pure: same inputs, same output, always.
    """
    code = str(loinc).strip() if loinc is not None else ""
    if not code:
        return "not_applicable", "no code to check"

    prefix = f"{code}{UNIT_SEPARATOR}"
    accepted_folded = {key[len(prefix):] for key in rules if key.startswith(prefix)}
    if not accepted_folded:
        if code in rules:
            return (
                "not_applicable",
                f"the rule for {code} declares no expected unit",
            )
        return "not_applicable", f"no rule for code {code}"

    displayed = sorted(
        {rules[prefix + folded].unit or folded for folded in accepted_folded}
    )
    observed = normalise_unit(unit)
    if observed is None:
        return (
            "not_applicable",
            f"observation carries no unit; {code} expects one of {', '.join(displayed)}",
        )
    if observed in accepted_folded:
        return "pass", f"unit {unit!r} is consistent with {code}"
    return (
        "fail",
        f"unit {unit!r} contradicts {code}, which expects one of {', '.join(displayed)}",
    )


# ------------------------------------------------------- the three-field check

# The default location of the shipped rule file, relative to the repository
# root. Both arms of the controlled comparison load the same file from here.
DEFAULT_RULES_RELPATH = ("manifests", "rules", "loinc_ranges.yaml")

# The only three fields a range-based plausibility check needs. Each of the
# three manifest widths releases all three to the plausibility agent, and none
# of the three is a Safe Harbor identifier: that disjointness is the paper's
# structural explanation for why field projection costs nothing here.
CODE_PATH = "$.code.coding[*].code"
VALUE_PATH = "$.valueQuantity.value"
UNIT_PATH = "$.valueQuantity.unit"


@dataclass(frozen=True, slots=True)
class RangeInputs:
    """The (code, value, unit) triple a range check consumes."""

    code: str
    value: float
    unit: str | None


def default_rules_path(root: Path) -> Path:
    """Where the shipped rule file lives under ``root``."""
    return Path(root).joinpath(*DEFAULT_RULES_RELPATH)


def _numeric(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def first_coding_code(node: Any) -> str:
    """The first ``coding[*].code`` of a CodeableConcept, or ``''``.

    Matches the convention the cohort survey in ``tests/test_ranges.py`` uses,
    so the rule coverage measured there is the coverage the checks get.
    """
    for coding in (node or {}).get("coding") or []:
        if isinstance(coding, dict) and coding.get("code") is not None:
            return str(coding["code"])
    return ""


def record_range_inputs(resource: dict[str, Any]) -> RangeInputs | None:
    """Read the three fields off a whole FHIR Observation.

    This is the full-record reading, used by the range-based monolith. Returns
    ``None`` when the resource is not an Observation or carries no numeric
    ``valueQuantity.value``.

    ``component[*].valueQuantity`` is deliberately not read. The injector only
    corrupts the top-level ``valueQuantity.value``, and reading components would
    make the full-access arm consume a field the minimal manifest does not
    release, which would compare privilege against field coverage instead of
    against privilege alone.
    """
    if resource.get("resourceType") != "Observation":
        return None
    quantity = resource.get("valueQuantity")
    if not isinstance(quantity, dict) or not _numeric(quantity.get("value")):
        return None
    return RangeInputs(
        code=first_coding_code(resource.get("code")),
        value=float(quantity["value"]),
        unit=quantity.get("unit"),
    )


def projected_range_inputs(projected: dict[str, Any]) -> RangeInputs | None:
    """Read the same three fields off a manifest-projected slice.

    ``projected`` is a ``_projected_fields`` dict: JSONPath expression to the
    list of values that expression matched. Returns ``None`` when the manifest
    released no numeric value, which is the only way projection can degrade the
    check.
    """
    values = projected.get(VALUE_PATH) or []
    numeric = [float(v) for v in values if _numeric(v)]
    if not numeric:
        return None
    codes = [str(c) for c in (projected.get(CODE_PATH) or []) if c is not None]
    units = [u for u in (projected.get(UNIT_PATH) or []) if u is not None]
    return RangeInputs(
        code=codes[0] if codes else "",
        value=numeric[0],
        unit=units[0] if units else None,
    )


def inputs_verdict(
    inputs: RangeInputs | None, rules: dict[str, RangeRule]
) -> tuple[Verdict, str]:
    """Apply :func:`range_verdict` to an extracted triple.

    ``None`` inputs mean there was nothing numeric to judge, which is
    ``'not_applicable'`` rather than a pass.
    """
    if inputs is None:
        return "not_applicable", "no numeric value to check"
    return range_verdict(inputs.code, inputs.value, inputs.unit, rules)
