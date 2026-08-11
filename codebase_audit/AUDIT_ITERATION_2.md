# Audit Execution E2: adversarial pass

> **Naming note (2026-08-07).** The two systems were called `A1` and `A6` when this document was
> written. They are now **Baseline** and **Confined**, and the result-file keys were migrated to
> `baseline` and `confined-<width>`. Labels below are updated; the findings are unchanged.

**Scope:** `codebase/` only. Top-down. Assumes the headline is wrong and tries to prove it.
**Model spend:** one live run (426 Haiku calls) plus a 80-call determinism probe. Justified under `AUDIT_PLAN.md` §1 because three published reproducibility numbers failed offline verification. The shipped `.llm_cache/` was **copied to scratch first and never written to** (verified: still 2006 files).

---

## 0. What E2 changes

E1 established the headline is arithmetically forced. E2 asked what the design *could* have detected, and found the answer is: quite a lot, if the manifests were configured differently. That turns the central finding from a demolition into a repair.

It also **overturns the bundle's own diagnosis of its reproducibility problem.**

---

## 1. Attack D: the design can measure a large effect. The manifests prevent it.

The strongest version of E1's finding is not "the gap is zero" but "the gap could not have been anything else". Tested constructively by building counterfactual manifests that actually withhold a field the checker consumes, then re-running the full 12-cell comparison:

| Manifest variant | Baseline | Confined | max gap |
|---|---|---|---|
| **as shipped (minimal)** | 0.9907 | 0.9907 | **0.0000 pp** |
| withhold `$.valueQuantity.unit` | 0.9907 | 0.9404 | **15.71 pp** |
| withhold `$.code.coding[*].code` | 0.9907 | **1.0000** | **3.85 pp** |
| withhold `$.valueQuantity.value` | 0.9907 | 0.0000 | **100.00 pp** |

Three conclusions.

**1. The instrument works.** It resolves privilege effects from 3.85 pp to 100 pp. The zero is not insensitivity of the method; it is a property of the shipped manifests.

**2. The experiment as configured has no independent variable.** All three widths release all three consumed fields, so the only quantity that could move is held fixed. The minimum detectable effect is not "large", it is **undefined**: no penalty of any magnitude could appear.

**3. Withholding the analyte code makes detection BETTER**, +3.85 pp, up to a perfect 1.0000. Stripping the code forces every Observation onto the default `[0, 9000]` envelope, which catches all four injected constants, whereas the per-analyte bounds have been widened past 9999 for several analytes. This is E1 §2.5 arriving from the opposite direction and it is the sharpest single piece of evidence in the audit: **on this benchmark, less clinical privilege yields strictly better detection.**

That result is genuinely interesting and it is not the result the bundle reports.

---

## 2. Attack on reproducibility: the bundle's diagnosis is wrong

README §4 makes three measurements. Two are wrong and the causal story is unsupported.

### 2.1 The numbers

| Claim | README | Measured | Verdict |
|---|---|---|---|
| Offline replay vs published legacy table | 10 of 60, max delta 0.0085 | **18 of 60, max delta 0.2105** | **WITHDRAW** |
| Archived offline replay vs published | 6 of 60 | 6 of 60, max delta 0.0030 | SUPPORTED |
| Payload lookups with no cache entry | **21** of 1,800 | **478** of 1,800 (legacy), **426** of 1,800 (stratified) | **WITHDRAW** |
| Two fresh no-cache runs at temp 0 | 4 values differ | not re-run in full; see §2.3 | UNVERIFIED |

The "21 of 1,800" is off by a factor of **twenty-three**. The offline replay differs on nearly twice as many cells as claimed, with a maximum delta **25× larger**.

The severity is concentrated in one place: on MIMIC-IV the Confined plausibility column collapses to **0.0000 at all three widths** in the replay, against a published 0.0870 / 0.1053 / 0.2105. The shipped cache does not cover those payloads, so every one collapses to `uncertain`, which scores as "did not flag".

### 2.2 The current main run does not reproduce either

`results_stratified_model.json` is the live headline-adjacent artefact. Replayed both ways:

| Run | Cache hits | Misses / skips | Differs from shipped |
|---|---|---|---|
| Offline replay against shipped cache | 1,374 | 426 skipped | **4 of 60**, max 0.0179 |
| **Live model + shipped cache** (426 real calls) | 1,374 | 426 called live | **6 of 60**, max 0.0179 |

Running the model *live* reproduces the published table **worse** than replaying offline. The shipped artefact cannot be regenerated from the shipped inputs by either route.

### 2.3 The provider is not the culprit

README §4 concludes that `temperature=0.0` "is necessary but **not sufficient**: greedy decoding at the provider is still not bit-reproducible".

Tested directly: 40 real projected payloads, each asked twice, cache disabled, temperature 0.

> **0 of 40 disagreements**, on verdict *and* on justification text.

This does not prove full determinism. With 0/40 the per-call disagreement rate is bounded at roughly 7% at 95% confidence, and the claimed drift is small enough to hide under that. But it provides no support for the provider-nondeterminism story, and there is a better-evidenced explanation available: **426 to 478 uncovered payloads per run**, which resolve to `uncertain` offline and to a fresh sample live. Cache coverage is a sufficient explanation for everything observed; provider nondeterminism is an unnecessary one.

The mechanism the bundle identifies is real and correctly located (`agents.py:265`, `uncertain` verdicts are never written to the cache, so those payloads are re-asked forever). Its magnitude is understated by more than an order of magnitude, and the blame is placed on the wrong component.

### 2.4 What is sound here

`CACHE_CONTRACT.json` **is enforced**, not decorative. Mutating `temperature` to 0.7 in a scratch copy and calling `assert_cache_contract` raises `RuntimeError` and refuses the replay. Claim SUPPORTED.

The bundle's headline reproducibility *conclusion* also survives, and is in fact strengthened: the run contract does not determine a run, and the results are not reproducible from code plus data alone. What fails is every specific number offered as evidence for it, and the attribution of cause.

---

## 3. Second pass over E1

### 3.1 Allocator claims: fully SUPPORTED

README §2.3 reproduced exactly, all three cohorts:

| | legacy 0.10 | stratified 0.10 | stratified 0.30 |
|---|---|---|---|
| completeness | 4 | 4 | 12 |
| plausibility | **2** | 9 | **27** (eICU 29) |
| consistency | **absent** | 3 | 9 (eICU 7) |
| timeliness | 5 | 4 | 12 |
| wasted | **9 of 20** | 0 | 0 |

Every figure in the README table matches, including the "27 to 29" and "7 to 9" ranges. The claim that the published primary endpoint rested on **n = 2** true defects per cell is correct, and it is the most damning true statement the bundle makes about its own predecessor.

### 3.2 Items carried from E1, re-checked

| E1 item | Second look |
|---|---|
| #5 "full access" mislabel | Confirmed and worse than stated: with `--baseline ranges` the monolith *loses* component coverage that `a1_plausibility` has. The flag changes field coverage, not only the rule set. |
| #6 finding-6 misattribution | Confirmed. Effect on synthea is exactly 0.0000; the 0.6125→0.6100 movement is injection mutation. |
| #9 AgentLeak attribution | Unchanged: paper real, metric correspondence unconfirmed. SUPPORTED-WEAK. |
| #12 `test_ranges` FP guard | Confirmed as the causal mechanism behind §1's inversion, via attack D's "withhold code" row. |

### 3.3 One more stale number

`agents.py:299` refers to "the 1,494 shipped entries". The cache holds **2,005** verdicts. An eighth documentation inconsistency, same root cause as the other seven.

---

## 4. Architecture: what is actually wired

| Layer | Described | Implemented | Exercised by a published number |
|---|---|---|---|
| Manifest projection | yes | yes | **yes** |
| Range checker, both arms | yes | yes | **yes**, the headline |
| Stratified injector | yes | yes | **yes** |
| Exposure / AgentLeak | yes | yes | **yes** |
| LLM agents | yes | yes | yes, secondary tables only |
| Bootstrap / Holm / NI | yes | yes | yes, `replication_summary.json` |
| **RBAC** | yes | yes | **no** — tests only |
| **Hash-chained audit** | yes | yes | **no** — tests only, never written to disk |
| **Department registry** | yes | yes | **no** — tests only, disagrees with shipped manifests |
| **Linkage ladder L0/L1/L2** | yes | partial | **no** — never run end to end |
| **Text PHI detector** | yes | yes | **no** — tests only |

Five of eleven components produce nothing that any results artefact depends on.

**The headline contains no language model at all.** It is a deterministic range check on both arms, on one dimension. The claimed architecture is a privilege-bounded multi-agent LLM system; the central result exercises the projection layer and a rule file. Every component that makes the system *agentic* sits outside the evidence for the main claim.

This is a codebase observation. What it means for the thesis is phase 2, but it will be the first thing a reviewer asks.

---

## 5. Verdicts

| # | Claim | Verdict | Severity |
|---|---|---|---|
| 1 | Field projection costs 0.0000 pp, 12 cells | **SUPPORTED as arithmetic / WITHDRAW as empirical** | CRITICAL |
| 2 | The comparison is "controlled" | SUPPORTED-WEAK: controlled to the point of having no independent variable | CRITICAL |
| 3 | Result replicated across 4 seeds | **WITHDRAW**: replication of an identity is not evidence | HIGH |
| 4 | Detection task is easy but structural argument survives | SUPPORTED-WEAK: the structural argument *is* the whole result | CRITICAL |
| 5 | Range file is shared clinical knowledge | SUPPORTED-WEAK: fitted to a 62,188-value corpus, degrades detection | HIGH |
| 6 | Offline replay differs on 10 of 60, max 0.0085 | **WITHDRAW**: 18 of 60, max 0.2105 | HIGH |
| 7 | 21 of 1,800 lookups uncached | **WITHDRAW**: 478 (legacy) / 426 (stratified) | HIGH |
| 8 | Provider nondeterminism causes residual drift | **UNVERIFIABLE**, and unsupported by 0/40 paired live calls | MEDIUM |
| 9 | Cache is a primary artefact and makes results reproducible | SUPPORTED as principle, **WITHDRAW** as achieved: neither table reproduces | HIGH |
| 10 | `CACHE_CONTRACT` enforced | **SUPPORTED** | — |
| 11 | Legacy allocator wasted 9 of 20; plausibility n=2; consistency n=0 | **SUPPORTED**, exactly | — |
| 12 | Withdrawn "confined beats baseline" claim | still live and unmarked in `replication_summary.json` | HIGH |
| 13 | Non-inferiority test discriminates | SUPPORTED-WEAK: step function on zero-variance input; README describes it backwards | MEDIUM |
| 14 | Surrogates take events off the ceiling; notes 90→0 | **SUPPORTED**, reproduced by hand; no artefact | LOW |
| 15 | Exposure reports cohort composition | **SUPPORTED**, and now measured against `limit` | — |
| 16 | El Yagoubi et al. (2026) | **SUPPORTED** (real paper); attribution loose | MEDIUM |
| 17 | Data provenance | **SUPPORTED** | — |

**Priors scorecard:** 1 ✅, 2 ❌ (partly wrong), 3 ✅, 4 ✅ (understated), 5 ✅, 6 ✅, 7 ✅ (broader), 8 ✅, 9 ✅. Eight of nine held; committing them in advance was worth it, and prior 2 being wrong is the reason §3.1 of E1 exists.

---

## 6. What the study still establishes

Stated plainly, because the demolition above is not the whole picture.

**Stands, and is worth publishing:**

1. **The published v1 primary endpoint rested on n = 2 true defects per cell**, and consistency on n = 0. Reproduced exactly. This is a genuine, well-evidenced methodological finding about the prior work.
2. **The exposure metric reports cohort composition, not privilege.** Demonstrated by holding manifests fixed and varying `limit`.
3. **The results are not reproducible from code plus data.** True, and stronger than the bundle argues: neither the legacy nor the current table regenerates, by cache replay or live model.
4. **`CACHE_CONTRACT` enforcement** is a genuinely good piece of engineering.
5. **Surrogate non-derivation and section-limited note projection** both work as claimed.

**Needs reframing, not retraction:**

6. The headline. It is a **design property demonstrated**, not an effect measured: *a range-based plausibility check consumes only code, value and unit, none of which is a Safe Harbor identifier, so a manifest can withhold every identifier without affecting it.* That is a true and useful statement. It needs no seeds, no cohorts and no F1 table; it needs the counterfactual manifests of §1 to show the boundary is real and measurable.

**Must be withdrawn or corrected:**

7. Every specific reproducibility number in README §4.
8. The claim that four-seed replication adds evidence.
9. The unmarked withdrawn claim inside `replication_summary.json`.

---

## 7. Carried to phase 2

- Does `paper.tex` present the 0.0000 as measured or as structural? E1 §2.2 makes this the paper's central integrity question.
- Does the paper report the four-seed replication as independent evidence?
- Does the abstract carry the caveat, or is it only in a late section?
- Does the paper cite `replication_summary.json` for a claim the bundle withdrew?
