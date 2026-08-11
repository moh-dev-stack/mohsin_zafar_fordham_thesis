# Audit Execution E1: does the code do what it says

> **Naming note (2026-08-07).** The two systems were called `A1` and `A6` when this document was
> written. They are now **Baseline** and **Confined**, and the result-file keys were migrated to
> `baseline` and `confined-<width>`. Labels below are updated; the findings are unchanged.

**Scope:** `codebase/` only, per `AUDIT_PLAN.md` §0. Bottom-up. All checks offline. **Zero API spend.**
**Result:** no stop condition triggered. The headline reproduces byte-identically. What it *means* does not survive contact.

---

## 0. Headline of this iteration

> **The 0.0000 pp gap is a mathematical identity, not an empirical finding.**
> Across 600 clean and 2,400 injected resources, at all three manifest widths, the `(code, value, unit)` triple consumed by the full-access arm is bit-identical to the triple consumed by the projected arm. **Zero mismatches.** The experiment could not have returned any other number.

And a second, unpredicted result:

> **The clinical knowledge makes detection worse.** A five-line set-membership check with no clinical content scores F1 = 1.0000 in 12/12 cells. The 441-line, 322-source per-analyte range file scores 0.9907 and is perfect in only 7/12.

---

## 1. Baseline health

| Check | Claimed | Actual | Verdict |
|---|---|---|---|
| Test suite | 195 tests | **236 passed** in 142.94s | MISLABELLED, LOW |
| `ruff` | clean | clean | SUPPORTED |
| Cohort files | 325 | 325 (125 + 100 + 100) | SUPPORTED |
| Cache entries | 2006 | **2005 verdicts + 1 `CACHE_CONTRACT.json`** = 2006 files | MISLABELLED, LOW |
| `CACHE_CONTRACT.json` | present and checked | present, read by `assert_cache_contract` | SUPPORTED |

The bundle understates its own test count by 41. Both mismatches are trivial in effect and both are in numbers the bundle uses to assert its own health, which is the point: these are the easiest possible facts to get right.

---

## 2. P0: the headline

### 2.1 It reproduces exactly

`python -m dqa.controlled` regenerated to a scratch path and compared to the shipped artefact: **IDENTICAL**, whole-document equality including all 12 cells, both hashes and the full contract block. Prior 8 confirmed. No stop condition.

### 2.2 CRITICAL: the independent variable never varies

The comparison's premise is that the two arms differ only in field visibility. They do not differ at all.

**Mechanism, from the code.** `dqa.ranges` defines exactly three consumed paths (`ranges.py:350-352`):

```
CODE_PATH  = "$.code.coding[*].code"
VALUE_PATH = "$.valueQuantity.value"
UNIT_PATH  = "$.valueQuantity.unit"
```

- `record_range_inputs` (`ranges.py:385`), the full-access arm, reads those three off the raw resource and **deliberately does not read `component[*].valueQuantity`**. Its own docstring says so, and gives the reason: reading components "would make the full-access arm consume a field the minimal manifest does not release".
- `projected_range_inputs` (`ranges.py:410`) reads the same three keys off `_projected_fields`.
- **All three width manifests release all three paths.** `minimal/plausibility.yaml` already contains `$.code.coding[*].code`, `$.valueQuantity.value` and `$.valueQuantity.unit`. Wider manifests add `system`, `display`, `referenceRange`, `interpretation`, `component`, and Patient fields, **none of which any range checker reads**.

**Empirical confirmation**, written from the definitions rather than by calling the scoring functions:

| Population | Resources | Input mismatches | Verdict mismatches |
|---|---|---|---|
| Clean cohorts, 3 datasets | 600 | **0** | **0** |
| Injected cohorts, 4 seeds × 3 datasets | 2,400 | **0** | 0 |
| Adversarial synthetics (no unit, non-numeric, bool, multi-coding, unknown code, component-only, no code, null value, Patient, Encounter) | 10 | **0** | 0 |

Even the not-applicable cases coincide exactly: 277 resources yield `None` for the full-access arm and 277 for every width.

**Consequence.** Both arms are the same pure function of the same three inputs. F1 must be identical. `max_abs_gap_pp = 0.0000` is forced by construction and is invariant to seed, cohort, injection rate and dataset. Running more seeds cannot change it, and the four-seed replication adds no information whatsoever.

The bundle states the structural reason (README §3, "The reason is structural") but presents the outcome as a measured finding with twelve cells of evidence. It is a theorem with a unit test.

**Verdict: SUPPORTED as arithmetic, WITHDRAW as an empirical claim. CRITICAL.**

### 2.3 MEDIUM: "full access" is mislabelled

`controlled.py:20-21` describes `baseline_ranges` as "reading the whole FHIR resource". `baseline.py:156` has whole-resource *access* but consumes three fields and explicitly skips components. In the only sense that affects a verdict, the full-access arm does not read the whole record. The label overstates the contrast the experiment is built on.

Note also that `a1_plausibility` (the published envelope baseline) *does* read components while `a1_plausibility_ranges` does not, so switching `--baseline ranges` changes the monolith's field coverage as well as its rule set.

### 2.4 CRITICAL: the endpoint cannot discriminate detectors, and inverts them

Three plausibility detectors, same injected cohorts, same scoring code:

| Detector | Clinical knowledge | Mean F1 | Perfect cells |
|---|---|---|---|
| `trivial` — `value in {-9999, 9999, 1e9, -1}` | none, 5 lines | **1.0000** | **12/12** |
| `envelope` — `value < 0 or value > 9000` | none, the default fallback alone | **1.0000** | **12/12** |
| `ranges` — the shipped 441-line per-analyte file | 322 sourced bounds | **0.9907** | 7/12 |

Per-cell, the range file is never better and is worse in five cells (0.9825, 0.9811, 0.9811, 0.9615, 0.9825).

`IMPLAUSIBLE_VALUES = (-9999.0, 9999.0, 1e9, -1.0)` and the default rule is `[0.0, 9000.0]`, so **all four injected constants fall outside the fallback envelope**. No per-analyte knowledge is required to score perfectly. The elaborate rule file's only measurable effect on the primary endpoint is to lose five cells.

The bundle discloses that the task is easy (README §3, "a five-line set-membership test ... also scores F1 = 1.0000"). It does not disclose that its own clinical rule file scores **lower** than that five-line test.

**Verdict: the "F1 = 1.0000" figures are SUPPORTED but uninformative about detector quality. CRITICAL for interpretation.**

### 2.5 HIGH: escapes are real and the documented cause is wrong

Injected plausibility defects the range checker fails to flag, over 4 seeds × 3 cohorts:

| Count | Cohort | Code | Unit | Injected value |
|---|---|---|---|---|
| 2 | eicu-demo | 1 | `Units/L` | 9999.0 |
| 2 | mimic-iv-demo | 50878 | `IU/L` | 9999.0 |
| 1 | mimic-iv-demo | 51108 | `mL` | 9999.0 |
| 1 | mimic-iv-demo | 51109 | `mL` | 9999.0 |

**Six escapes across four code/unit groups. None of them is the FiO2 group.** The rule file header (`loinc_ranges.yaml:49-56`) documents exactly one escape route, eICU code 7 unit `%`, widened to 10000 for a real FiO2 artefact, and states "consequently 9999.0 is NOT flagged for this 41-row group". That group does not appear among the actual escapes in the evaluated sample. The disclosed cause is not the operative cause.

The operative cause is the same mechanism, applied to analytes whose legitimate physiological ceilings genuinely exceed 9999 (transaminases in IU/L, urine volumes in mL). That is defensible clinical modelling. What is not defensible is a rule file **fitted to a 62,188-value corpus** by a test guard requiring zero real values to fail, when the evaluation only ever sees 323 of those values. Bounds were widened for rows that the experiment never scores, and the widening costs detection on rows it does.

**Verdict: attack E lands. SUPPORTED that the rule file is fitted to data outside the evaluation sample. HIGH.**

### 2.6 The zero-collision claim holds

323 real numeric top-level `valueQuantity.value` readings in the loaded cohorts. Collisions with the four injected constants: `{-9999.0: 0, 9999.0: 0, 1e9: 0, -1.0: 0}`. **SUPPORTED.** Prior 3 confirmed.

---

## 3. P1: secondary results

### 3.1 Prior 2 was partly WRONG, and so is the bundle's explanation

The code defect is real: `run.py:186` computes the exposure numerator from slices of the **injected** resource and the denominator from the **clean** one. But the bundle's account of its effect is not.

Reproduced at the published configuration (legacy allocation, rate 0.10, seed 42, minimal width). My re-derivation matches the published `agentleak_mean` exactly in all three cohorts, so the reproduction is faithful:

| Cohort | Published-style (as shipped) | Consistent (both on injected) | Clean decomposition | Effect of the defect |
|---|---|---|---|---|
| synthea | 0.6100 | 0.6100 | 0.6125 | **0.0000** |
| mimic-iv-demo | 0.6675 | 0.6700 | 0.6700 | −0.0025 |
| eicu-demo | 0.6675 | 0.6700 | 0.6700 | −0.0025 |

README §9 finding 6 claims the defect "is why the published `agentleak_mean` reads 0.6100 against a clean-cohort 0.6125". **On synthea the defect contributes exactly zero to that gap.** Both variants give 0.6100. The 0.6125 → 0.6100 movement there is caused by injection mutating the resources, not by the numerator/denominator asymmetry.

The defect does bite on MIMIC and eICU, at minimal width only, worth −0.0025. Fixing it would move `confined-minimal` from 0.6675 to 0.6700 on two cohorts and change nothing on synthea.

**Verdict: defect SUPPORTED, its documented explanation MISLABELLED. MEDIUM.**

### 3.2 Exposure tracks the sampler, confirmed

Manifests held constant, `limit` varied:

| limit | synthea | mimic-iv-demo | eicu-demo |
|---|---|---|---|
| 60 | 0.5583 | 0.6667 | 0.6667 |
| 120 | 0.5917 | 0.6667 | 0.6667 |
| **200** | **0.6125** | **0.6700** | **0.6700** |
| 300 | 0.6200 | 0.6667 | 0.6667 |

The pooled figure moves with the sample size while the manifests are identical. It is a property of `read_cohort`'s `limit // 3` quota, not of privilege. The bundle says this; the strong version is now measured. **SUPPORTED.**

### 3.3 `union_mean` collapses in all 27 cells

Verified directly: `union_mean == clamped_mean` in **27 of 27** cells (3 cohorts × 3 widths × 3 resource types). `unclamped_mean` diverges (up to 1.5000 against a clamped 1.0000) exactly where the README says. `at_ceiling` gives 55.5% (synthea, 111/200) to 67.0% (eICU, 134/200), matching the claim.

Internal inconsistency: README §2.1 says "all 27 cells", README §9 finding 9 says "all nine cells". The former is right.

### 3.4 HIGH: a withdrawn claim is still live in a shipped artefact

`results/replication_summary.json` reports, for Confined-full minus Baseline:

| Dimension | synthea | mimic-iv-demo | eicu-demo |
|---|---|---|---|
| f1_plausibility | **+25.38 pp** | **+17.55 pp** | **+26.77 pp** |
| macro_f1 | +6.35 pp | +4.39 pp | +6.69 pp |

with `p = 0.0` and `holm_reject = true` on every plausibility row. That is the claim "the confined system beats the baseline", which README §3 explicitly **withdraws**.

The file contains no withdrawal marker, no caveat, and no reference to the controlled comparison that supersedes it. Searched: no occurrence of "withdraw" or "supersed" anywhere in the document. A reader opening the artefact directly gets the retracted result presented as a Holm-corrected significant finding.

**Verdict: HIGH. The artefact must carry the withdrawal, or be renamed to mark it superseded.**

### 3.5 MEDIUM: the non-inferiority test degenerates on this data

`non_inferiority_test` bootstraps over the **four** seed-level differences. Where those four are identical, which is most cells here, the bootstrap has no variability at all:

| Input (4 identical diffs) | p | bootstrap CI | width |
|---|---|---|---|
| +0.00 | 0.0 | (0.0000, 0.0000) | **0.0000** |
| +0.02 | 0.0 | (0.0200, 0.0200) | **0.0000** |
| +0.05 | 1.0 | (0.0500, 0.0500) | **0.0000** |
| −0.10 | 0.0 | (−0.1000, −0.1000) | **0.0000** |

The p-value takes only the values 0.0 and 1.0, flipping at the margin. It is a step function, not a test, and the "95% CI" has width zero. The direction is *correct* (small p rejects inferiority), but the quantity carries no evidential content on degenerate input.

Note `replication_summary.json` does not write the NI `p_value` at all, so the defect-1 fix improved a number that is not published.

Separately, on the non-degenerate rows the percentile bootstrap on n=4 is far narrower than the t-interval computed from the same four points (plausibility synthea: boot 16.33–35.52 against t 7.46–43.30). With four observations the bootstrap cannot see the tails and understates uncertainty.

### 3.6 The README describes the defect-1 fix backwards

README §9 defect 1: *"p now discriminates: 1.0000 at −0.20, 0.0000 at +0.03"*.

Measured: p = **0.0** at −0.20, p = **1.0** at +0.03. Exactly inverted.

The **code is correct** (a large negative difference should reject the inferiority null, giving small p). The documentation of the fix is wrong. LOW as arithmetic, MEDIUM as evidence that fix descriptions were not re-checked against the fixed code.

---

## 4. P2: what is actually wired

### 4.1 Prior 7 confirmed, and it is broader than reported

Production imports, excluding each module's own file:

| Module | Imported by `src/` | Imported by `tests/` | Touches a published number |
|---|---|---|---|
| `exposure.py` | `run.py:48` | yes | **yes** |
| `stats.py` | `replicate.py:66` | yes | **yes** |
| `controlled.py` | — (entry point) | yes | **yes**, the headline |
| `rbac.py` | **none** | `test_rbac.py` | no |
| `audit.py` | **none** | `test_audit.py` | no |
| `departments.py` | **none** | `test_linkage.py` | no |
| `linkage.py` | **none** | `test_linkage.py` | no |
| `phi_text.py` | **none** | `test_phi_text.py` | no |

The bundle admits `rbac`, `audit` and `departments` are unwired. **`linkage.py` and `phi_text.py` are equally unwired**, and that is not admitted: README §2.2 and §2.4 present their outputs as measured results of the study.

`Makefile` targets are `install test lint eval eval-offline eval-stratified controlled replicate replicate-runs gap figure all`. No `audit` target, as the README correctly states.

### 4.2 Two README result tables have no artefact

`0.9142` (surrogate exposure, README §2.2), `90` (note hits, §2.4) and `62188` (LOINC survey size) appear in **no file under `results/`**. They are produced only inside test runs.

Both reproduce correctly by hand:

- **§2.4:** first Synthea bundle contains exactly **45** DocumentReference notes; full-note Safe Harbor hits = **90**; `# Assessment and Plan` section hits = **0**. Matches the README exactly. The 90 decomposes as 45 `bare_date` + 45 `name`.
- **62,188** is printed by `test_ranges.py` during the run and matches.

**Verdict: F2 orphaned but SUPPORTED.** They are true and reproducible; they are not artefacted, so they cannot be checked without re-running the suite.

### 4.3 The word-boundary risk is latent, not active

Finding B says `phi_text` substring matching has no word boundaries, so `Ann` matches inside `Announced`. I checked every hit on the shipped notes for an adjacent alphanumeric character: **0 violations of 90 hits**. The concern is a real code property with zero effect on any reported number. The bundle's characterisation is fair.

Worth noting the names matched are Synthea's digit-suffixed forms (`Abram53`), which are far easier to match than real names. The 90 is not a realistic PHI-detection difficulty.

### 4.4 Test-quality register

Confirmed smells:

| Test | Problem |
|---|---|
| `test_ranges.py` false-positive guard | Requires **zero real cohort values** to fail. This constrains the *data*, not the code: it is a fitting procedure wearing a test's clothes, and it is the direct cause of the bound-widening in §2.5. |
| `test_exposure.py::test_union_stays_equal_to_clamped` | Asserts `stats.union_mean == stats.clamped_mean` where both come from the same `_stats(...)` call. It pins a tautology of the current implementation rather than an external truth. |

To the bundle's credit, `test_the_ordering_invariants_are_enforced_on_values_that_could_break_them` is a genuinely good test: it feeds `_summarise` hand-built triples `(6,2,2), (1,1,4), (0,0,0)` with independently derived expected values, and its docstring correctly says the real-data assertions are vacuous. That is the standard the rest of the suite should meet.

**Prior 5: confirmed.** The `test_ranges` guard is worse than the bundle's catalogue suggests, because it does not merely pass on wrong code, it actively shaped the rule file.

---

## 5. F7: the citation is real, the attribution is loose

`metrics.py:6`: *"AgentLeak is the normalised Safe Harbor field exposure across inter-agent messages, per El Yagoubi et al. (2026)."*

**The paper exists.** *AgentLeak: A Benchmark for Internal-Channel Privacy Leakage in Multi-Agent LLM Systems*, El Yagoubi et al., arXiv 2602.11510, Polytechnique Montréal. It covers inter-agent messages as a leakage channel (C2, 68.8% leakage against 27.2% for final outputs), which is genuinely the concept this code operationalises. **No stop condition. Prior 9 confirmed.**

However, the paper's reported metrics are channel-level leakage rates (ELR, WLS, CLR, ASR) over scenarios. This codebase's metric is a **per-record clamped ratio of exposed to present Safe Harbor field occurrences**, which is not evidently one of them. I could not confirm from the abstract that the paper defines this quantity.

**Verdict: SUPPORTED-WEAK.** The wording "per El Yagoubi et al." implies the metric is theirs as defined. It should say "adapted from", or cite the specific equation. MEDIUM, and it matters more in phase 2 than here.

The three `45 CFR 164.514(b)(2)` citations in `linkage.py:21`, `phi_text.py:55` and `:408` are used correctly in substance: (b)(2)(ii) is the re-identification-code non-derivation rule, (b)(2)(i)(C) is the date-precision rule.

Data provenance re-confirmed: real corpora, real DOIs, counts match.

---

## 6. P3: internal inconsistencies

| Location | Says | Actual |
|---|---|---|
| `README.md:7` vs `:228` | 30 pages / 26 pages | contradictory |
| `README.md:45` | "twelve-key run contract", then "ten of the eleven keys" | `run.py` writes 12 = 11 contract + `cohorts` |
| `README.md:71` vs §9 finding 9 | "all 27 cells" / "all nine cells" | 27 |
| `README.md` §7 | 195 tests | 236 |
| `README.md:184` | 2006 cache entries | 2005 verdicts + 1 contract file |
| `README.md` §9 defect 1 | p = 1.0 at −0.20, 0.0 at +0.03 | inverted |
| `README.md` §9 finding 6 | explains the 0.6100/0.6125 gap | contributes 0.0000 to it on synthea |

Seven documentation defects, five of them numeric. No single one matters. The pattern is that **prose was not re-checked against the code after the code changed**, which is the same process failure that produced the misattributed finding 6 and the inverted defect 1.

---

## 7. E1 register

| # | Finding | P | Severity | Verdict |
|---|---|---|---|---|
| 1 | Both arms consume identical inputs; 0.0000 gap is analytic, not empirical | P0 | **CRITICAL** | WITHDRAW as empirical |
| 2 | Trivial 5-line detector ties, clinical rule file loses 5/12 cells | P0 | **CRITICAL** | SUPPORTED, uninformative |
| 3 | Rule file fitted to a 62,188-value corpus; 6 escapes in 4 undocumented groups | P0 | HIGH | SUPPORTED |
| 4 | Withdrawn claim still live in `replication_summary.json`, Holm-significant, unmarked | P1 | HIGH | must be marked |
| 5 | "Full access" arm reads 3 fields, not the whole record | P0 | MEDIUM | MISLABELLED |
| 6 | Finding-6 explanation wrong on synthea (0.0000 effect, not 0.0025) | P1 | MEDIUM | MISLABELLED |
| 7 | NI test degenerate on zero-variance input: p ∈ {0,1}, CI width 0 | P1 | MEDIUM | SUPPORTED-WEAK |
| 8 | n=4 percentile bootstrap far narrower than t; understates uncertainty | P1 | MEDIUM | SUPPORTED-WEAK |
| 9 | AgentLeak attribution loose ("per" should be "adapted from") | P1 | MEDIUM | SUPPORTED-WEAK |
| 10 | `linkage.py` and `phi_text.py` unwired, not admitted | P2 | MEDIUM | SUPPORTED |
| 11 | §2.2/§2.4 numbers have no artefact (reproduce correctly by hand) | P2 | LOW | F2 orphaned |
| 12 | `test_ranges` FP guard constrains data, not code | P2 | MEDIUM | SUPPORTED |
| 13 | `test_exposure` union assertion is a tautology | P2 | LOW | SUPPORTED |
| 14 | Seven README inconsistencies incl. 195/236 and 2006/2005 | P3 | LOW | MISLABELLED |
| 15 | Headline reproduces byte-identically | P0 | — | SUPPORTED |
| 16 | Zero-collision claim; 0 of 323 | P0 | — | SUPPORTED |
| 17 | Exposure tracks `limit`, manifests held constant | P1 | — | SUPPORTED |
| 18 | El Yagoubi et al. 2026 is a real paper | — | — | SUPPORTED |
| 19 | Word-boundary overcount: 0 of 90 hits | P2 | LOW | latent only |

**Priors:** 1 confirmed, 2 **partly wrong**, 3 confirmed, 4 confirmed and understated, 5 confirmed, 6 pending E2, 7 confirmed and broader, 8 confirmed, 9 confirmed.

---

## 8. Carried into E2

- Attack D: minimum detectable effect. Given §2.2, the answer may be that the design's MDE is undefined rather than large.
- Reproducibility replay: `10 of 60`, `6 of 60`, `4 values`, `21 of 1,800`, `CACHE_CONTRACT` enforcement.
- Whether the legacy `n=2` / consistency `n=0` allocation claims hold.
- Second pass over items 5, 6, 9, and the remaining test-quality catalogue.
- What, if anything, the study still establishes. That is E2's closing question.
