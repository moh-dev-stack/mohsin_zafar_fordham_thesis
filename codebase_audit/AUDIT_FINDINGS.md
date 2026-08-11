# Audit Verdict, Fixes Applied, and Corrections to the Audit Itself

> **Naming note (2026-08-07).** The two systems were called `A1` and `A6` when this document was
> written. They are now **Baseline** and **Confined**, and the result-file keys were migrated to
> `baseline` and `confined-<width>`. Labels below are updated; the findings are unchanged.

**Target:** `FINAL_DELIVERABLES/3_updated_architectures/codebase` and its results. Paper and slides remain out of scope (phase 2).
**Method:** `AUDIT_PLAN.md` (two planning iterations) → `AUDIT_ITERATION_1.md` (E1, bottom-up) → `AUDIT_ITERATION_2.md` (E2, adversarial) → this document (fixes and final verdict).
**Model spend:** 426 live Haiku calls (cache-coverage diagnosis) + 80 (determinism probe). The shipped `.llm_cache/` was copied to scratch first and never written to. **Verified 2006 files before and after.**

**Final state:** 346 tests pass (from 236), 1 skipped, `ruff` clean, all four offline artefacts regenerate byte-identically, and every published number is either unchanged or moved for a documented and defensible reason.

---

## 1. Verdict

**Nothing was fabricated.** Real corpora with correct DOIs, a real citation, every artefact traceable to code. No stop condition fired at any point.

**The central claim was arithmetically correct and scientifically vacuous.** That is now fixed, not by changing the number, but by shipping the experiment that gives it meaning.

> **The 0.0000 pp headline is a mathematical identity, not a measurement.** Both arms consume byte-identical `(code, value, unit)` triples: 600 clean resources, 2,400 injected, three widths, ten adversarial synthetics, **zero mismatches**. It could not have returned anything else.

Three things now stand alongside it that did not before:

1. **`make ablation`** withholds one *consumed* field at a time and measures what projection actually costs: **3.85, 28.04 and 100.00 pp**. Plus a negative control that withholds a released-but-unconsumed field and correctly scores **0.0000 pp** — direct proof the headline zero measures released-minus-consumed, not privilege.
2. **`--injection-mode hard`** replaces the four sentinel constants with values derived from each analyte's own bound. Set-membership catches **0 of 83**, the crude envelope 5 of 83, the per-analyte check **83 of 83**. The endpoint finally ranks detectors correctly — and the gap is **still 0.0000 pp in all twelve cells**, which is the strongest available answer to "the equality only holds because the task was trivial."
3. **`make audit`** writes the hash-chained log that two earlier editions promised and never built.

**Severity outcome:** 2 CRITICAL (both addressed), 6 HIGH (5 fixed, 1 documented), 8 MEDIUM (7 fixed), 6 LOW (all fixed).

---

## 2. Corrections to my own audit

Three of my findings were wrong or overstated. Recording them prominently, because an audit that only corrects other people's work is not an audit.

| # | What I claimed | What is actually true |
|---|---|---|
| **Baseline** | `union_mean` collapses **because it is clamped** (inherited from the README and repeated without testing). | **Wrong.** Over all 1,800 records × 3 widths, no record has `union > total`, so the union clamp is a **no-op**. The real cause is narrower: `union` can differ from `exposed` only where a path reaches more than one agent, which on these cohorts happens only where `exposed >= total` already. Dropping the clamp changes nothing. |
| **A2** | The six escaped defects are caused by bounds **widened to satisfy the false-positive guard**. | **Wrong for all four implicated groups.** AST >10,000 IU/L is documented in ischaemic hepatitis; the eICU aggregate code genuinely pools CPK with transaminases; 10 L/day urine is real polyuria. Each bound sits far above its group's observed maximum, so none was forced by the data. The escapes are **false ground truth from the injector**, which writes 9999 into analytes where 9999 is physiologically real. The checker is right to pass them. |
| **A3** | Prior 2: the exposure numerator/denominator defect explains the synthea `0.6125 → 0.6100` gap. | **Wrong.** Its contribution on synthea is exactly **0.0000**; the movement is injection mutating resources. It bites only on MIMIC-IV and eICU at minimal width, worth −0.0025. (This one I caught myself during E1.) |

Two bounds *were* genuinely fitted, and both are now corrected: eICU `7|%` (10000 → 100, definitional) and `7|degC` (`[0,120]` → `[10,45]`).

---

## 3. The most important thing found

Tightening the rule file made the four eICU cells score **worse** (0.9825 → 0.9655). Investigating why produced the sharpest result of the whole exercise:

> The eICU cohort contains a body temperature of **99.8 °C** — Fahrenheit wearing a Celsius unit. It sits in the evaluation slice, is never injected, and the corrected bound now flags it. The scoring counts that **correct detection of real corruption as a false positive**, because the ground truth assumes every uninjected record is clean.

The benchmark is wrong in both directions: it penalises a checker for finding genuine corruption, and it penalises a checker for *not* flagging injected values that are physiologically real. Relabelling the three allowlisted corrupt records as genuine defects restores 0.9908 and 7-of-12. **That relabelling is not applied**, because it redefines ground truth and is the user's call.

---

## 4. What was fixed

### Headline and experiment design

| Fix | Detail |
|---|---|
| Privilege ablation shipped | New `src/dqa/ablation.py`, `manifests/ablation/` (4 arms), `results/privilege_ablation.json`, `make ablation`, 29 tests. Derives the withheld field by set-difference against the control, so a label cannot disagree with the manifest that produced the number. |
| Negative control | `withhold_ucum_code` withholds a field released by every width and consumed by neither arm. Must read 0.0000. This is the evidence for the identity claim. |
| Hard injector | `dqa.inject.hard_injectors/hard_eligibility`, values derived per analyte from the rule file at 2–15% outside the bound. `--injection-mode hard` on `dqa.controlled`. 20 tests, ground truth verified against bounds re-parsed independently from the YAML. |
| `controlled_comparison_hard.json` | The headline under hard injection. Gap **0.0000 pp in all 12 cells**. |
| Docstrings corrected | `controlled.py` no longer describes Baseline as "reading the whole FHIR resource"; the identity and its evidence are stated in the module docstring. |

### Rule file and its guard

28 bounds tightened, each with an updated `source`. New `known_corrupt_values` allowlist: **3 entries, 4 rows**, all eICU, each naming cohort/code/unit/value/count/why. The guard now **fails** on any flagged real value not on the allowlist, caps the list at 8, requires counts to match exactly, and rejects any non-definitional ceiling sitting exactly on a real value. Verified by mutation: re-widening `7|%` back to 10000 now fails two tests.

### Exposure subsystem

`--exposure-basis {published,consistent}` (default `published`, bit-identical). Both means now always reported. `union_unclamped_mean` added and the clamp proven a no-op; `multiplicity_inflation` added, which is the **only** exposure column that responds to manifest width on all three cohorts (0.0000 at minimal; 0.2775 / 0.1000 / 0.3175 at intermediate and full). `at_ceiling_strict` added: 0 at minimal on all three cohorts, versus `at_ceiling` which is constant at every width and therefore could never have evidenced clamp loss. `test_exposure.py` 64 → 106 tests, tautology replaced.

### Architecture, previously wired to nothing

| Fix | Detail |
|---|---|
| Audit log | `--audit-log PATH` on `dqa.run` + `make audit`. One record per scored system plus run start/complete, chain verified before exit, non-zero on failure. Verified by recomputing every hash independently and by tamper detection. |
| Linkage trap disarmed | `agents._requirement_met` accepts the canonical path **or** its surrogate, so completeness survives L1. L2 `none` still correctly fails. The old "do not fix this" trap test is rewritten to pin the fix. |
| Linkage coverage | `LINKAGE_PATHS` 2 → 5 (`recorder`, `performer`, `device`), with an import-time assertion tying it to the exposure metric so the two cannot drift. Zero effect on shipped data (0 of 600 resources carry them). |
| Department routing bug | `(Observation, consistency)` and `(Encounter, consistency)` were each claimed by **two** departments, one holding identifiers and one not. D2 is now the pure resolution tier. New `test_departments.py`, 13 tests, pinning the remaining manifest divergence explicitly. |

### Record-keeping

`replication_summary.json` now generates a `caveats` block **inside the artefact** (withdrawn claim, n=4 bootstrap degeneracy, seeds resample the injector only, post-hoc margin) so it survives `make replicate`. All 217 numbers verified unchanged. `metrics.py` attribution corrected from "per El Yagoubi et al." to "adapted from", with the distinction stated. All reproducibility figures corrected across both READMEs (21 → 478, 10 of 60 → 18 of 60, 0.0085 → 0.2105). Finding B removed as stale — `_at_word_boundary` already existed and works.

---

## 5. Results integrity

An integrity gate compared every numeric leaf in every artefact against `git HEAD`.

```
controlled_comparison.json      *** 16 moved (4 eICU cells × Baseline + 3 widths)
controlled_comparison_hard.json NEW
privilege_ablation.json         NEW
loinc_survey.json               ok (7833 identical)
rep_seed43/44/45.json           ok (294 each, identical)
replication_summary.json        ok (217 identical, caveats added)
results_legacy_model.json       ok (237 identical)
results_legacy_offline.json     ok (91 identical)
results_published_v1.json       ok (91 identical)
results_stratified_model.json   ok (294 identical)
results_stratified_offline.json ok (240 identical)
results_stratified_r010.json    ok (240 identical)
```

**Exactly 16 numbers moved, all four eICU cells, all from the de-fitted rule file, all explained in §3.**

Critically: `max_abs_gap_pp` is **0.0000 in all 12 cells before and after**, and `n_plausibility` is unchanged in every cell. The headline claim is untouched. All four offline artefacts regenerate byte-identically on re-run.

---

## 6. What remains

1. **Ground-truth relabelling.** The three `known_corrupt_values` rows are genuine defects but score as false positives. Fixing this restores 0.9908 / 7-of-12 and makes the benchmark honest. Not applied: it redefines ground truth.
2. **Hard mode is not the default.** `results_stratified_model.json` and the four seed replications are still legacy-mode, model-backed runs. Regenerating them under hard mode needs ~1,800 live calls per seed.
3. **Synthea PDW (`32207-3`)** stays at `[0, 1000]`. Real PDW is 8–25 fL; Synthea generates the whole 304-value series at 150–520 fL. A generator-wide scale artefact, not an allowlist case.
4. **The model-backed results remain irreproducible.** 426–478 of 1,800 lookups are uncached. Fixing it means caching `uncertain` verdicts, which invalidates the existing cache.
5. **The headline path still contains no language model.** Deterministic range checker on both arms, one dimension. The agentic architecture is not what the central result exercises.
6. **`departments.py` is still unwired**, and its divergence from the shipped manifests is pinned rather than resolved.

---

## 7. The honest claim

> A range-based plausibility check consumes only an analyte code, a value and a unit, none of which is a Safe Harbor identifier, so a privilege manifest can withhold every direct identifier without affecting it. Demonstrated by construction over 3,000 resources, and bounded by ablation: withholding a consumed field costs 3.85 to 100.00 pp, while withholding a released-but-unconsumed field costs exactly 0.0000. The equality holds under a hard injector that a sentinel detector cannot solve, so it does not depend on task difficulty.

Projection is free precisely when what is released is a superset of what is consumed, and costs 3.85 to 100 pp the moment it is not. That is a real, defensible, and now-measured contribution.

**Phase 2** checks whether `paper.tex` presents 0.0000 as measured, cites four-seed replication as independent evidence, or cites `replication_summary.json` for the withdrawn claim.
