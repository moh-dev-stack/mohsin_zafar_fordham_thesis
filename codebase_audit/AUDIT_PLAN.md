# Audit Plan: `3_updated_architectures` codebase and results

> **Naming note (2026-08-07).** The two systems were called `A1` and `A6` when this document was
> written. They are now **Baseline** and **Confined**, and the result-file keys were migrated to
> `baseline` and `confined-<width>`. Labels below are updated; the findings are unchanged.

**This document is the plan, built over two planning iterations. It contains no findings.**

| | |
|---|---|
| **Planning iteration 1** | Scope, fabrication taxonomy, P0-P3 register, two-iteration execution structure, adversarial posture, committed priors. |
| **Planning iteration 2** | This version. Adds external provenance checks (data, clinical thresholds, citations), the rule-file-fitting attack, a verification method section, per-iteration exit criteria, and a relaxed model-run policy. Changes are listed in §10. |
| **Execution** | Separate, and also two iterations: **E1** bottom-up correctness and provenance, **E2** top-down adversarial. See §5 and §6. |

---

## 0. Scope

### In scope

| | |
|---|---|
| `codebase/src/dqa/` | 16 modules, ~4,000 lines. Correctness, and whether each module computes what its docstring says it computes. |
| `codebase/tests/` | 10 files, ~2,900 lines. Whether the suite is a regression net or a smoke test. |
| `codebase/results/` | 15 artefacts. Provenance, regenerability, and whether each number is what it is labelled as. |
| `codebase/manifests/`, `codebase/data/`, `codebase/.llm_cache/` | The inputs. A result is only as pinned as its inputs. Includes whether the data and the clinical rule file are what they claim to be. |
| `codebase/README.md`, `codebase/results/README.md`, `data/PROVENANCE.md`, bundle `README.md` | Only where they make claims **about the code, the data or the results**. Claims under test, not evidence. |

### Out of scope, deferred to phase 2

`paper.tex`, `paper.pdf`, `presentation.html`, `slides/`, and every question of thesis framing, contribution and positioning.

Reason for the split: you cannot judge whether a paper overclaims until you know what the code establishes. This phase produces that ground truth. Phase 2 gets its own plan.

**Deliberate exception:** a number, citation or dataset claim that nothing supports is a fabrication finding and is reported immediately, whatever document it sits in. See §9.

### Non-goal

Improving the result. If the headline survives it survives. If it does not, the audit says so and says why.

---

## 1. Ground rules

| Rule | Detail |
|---|---|
| **Offline first** | The headline path is deterministic and needs no model, so it is verified for free. Static reading, re-derivation in plain Python over the shipped cohorts, and replay against `.llm_cache/` cover most of the audit. |
| **Running the model is permitted when it is the right tool** | Specifically: when a published number cannot be reproduced offline; when a value looks wrong and only a live call settles it; when the cache is the suspect and cache-versus-live has to be separated; when a fix changes a model-backed path and the artefact must be regenerated. Do not spend to re-confirm something already confirmed offline. Each run is logged in the report with its reason, scope and rough cost. Start with the narrowest run that answers the question, one cohort or one dimension before a full sweep. |
| **First principles, not deference** | The bundle ran three prior review passes and documents its own defects. Every one is re-verified independently. A prior finding is a hypothesis. |
| **Independent re-derivation** | Where a number is checked by re-computing it, the check is written from the definition, not by calling the function under test. Calling the same function is how the bundle's own circular test came about. |
| **Evidence or it is a suspicion** | Every finding carries file:line, a reproduced command with output, or a diff. No artefact means it is labelled "unverified suspicion" and ranked below verified findings. |
| **Severity is blast radius, not ugliness** | A bug that cannot move a published number is LOW however bad the code. A one-word mislabel on the headline metric is CRITICAL. |
| **Fixes applied, number-moving fixes proposed** | Safe and clearly correct fixes get applied. Anything that changes a published value is written up with before and after and left for a deliberate call. |

---

## 2. What "made up" means, operationally

Seven modes. Planning iteration 1 had five and they were all about numbers. That was too narrow: a study can be fabricated through its inputs and its attributions as easily as through its outputs.

### Fabricated outputs

| # | Mode | Test |
|---|---|---|
| **F1** | **Invented.** A number in prose that exists in no artefact. | Extract numeric literals from the code-facing READMEs. Grep against `results/*.json`. Trace or report every miss. |
| **F2** | **Orphaned.** In an artefact, but no code path produces that artefact. | For each `results/*.json`, identify the generating command. `results/README.md` claims to list them. Verify, do not accept. |
| **F3** | **Drifted.** Both exist, but the code changed after the artefact was written, so re-running gives something else. | Re-run every offline-reproducible artefact and diff. `controlled_comparison.json` first. |
| **F4** | **Mislabelled.** Right arithmetic, wrong name. | Read each metric implementation against its prose definition. Highest risk: `agentleak_mean`, `at_ceiling`, `union_mean`, `f1` denominators, "non-inferiority". |
| **F5** | **Overclaimed.** Right value, right name, prose saying more than it supports. | Compare claim strength to evidence strength. Highest risk: "exactly", "proves", any causal verb, any "zero" that is really "zero on this data". |

### Fabricated inputs and attributions (new in planning iteration 2)

| # | Mode | Test |
|---|---|---|
| **F6** | **Invented knowledge.** Clinical thresholds, Safe Harbor rules or regulatory constraints asserted without a source, or with a source that does not say that. | `manifests/rules/loinc_ranges.yaml`: does each bound carry a `source`, and is that source real and does it support that number? `45 CFR 164.514(b)(2)` citations in `linkage.py:21`, `phi_text.py:55` and `:408`: quote the actual regulation and check it says what the code says it says. |
| **F7** | **Invented attribution.** A citation for a method, metric or dataset that does not exist or does not contain the thing cited. | `metrics.py:6` attributes AgentLeak to **"El Yagoubi et al. (2026)"**. Verify this paper exists and defines this metric this way. A 2026 citation for the study's own exposure metric is the single highest-value attribution check in the codebase. Also verify the three PhysioNet and Synthea DOIs in `data/PROVENANCE.md`, and that `claude-haiku-4-5-20251001` is a real snapshot ID. |

**Already established, not to be re-litigated:** `data/PROVENANCE.md` names real corpora with real DOIs (MIMIC-IV Demo v2.2, eICU-CRD Demo v2.0.1, Synthea), file counts match (125 / 100 / 100), and the doc is candid that most shipped bytes are never read. The remaining data question is only whether the *loaded 200* per cohort are what the results assume, which `PROVENANCE.md` says was checked by SHA-256 over ordered `(resourceType, id)` pairs. That check gets re-run rather than believed.

---

## 3. Priority register

Priority is **blast radius on a published number**, not code quality. This register is the spine of both execution iterations.

### P0 — can move the headline

`controlled_comparison.json`: max gap 0.0000 pp, 12 cells, 3 widths, 4 seeds. Anything on its path.

| Target | Why P0 |
|---|---|
| `controlled.py` | Generates the headline. If the comparison is not actually controlled, nothing downstream matters. |
| `manifests.py::project` | The projection boundary **is** the independent variable. If it leaks, or if it never withholds anything the checker reads, the result is true for the wrong reason. |
| `baseline.py::a1_plausibility_ranges` vs `agents.py::check_plausibility_ranges` | The two arms. Any behavioural asymmetry is the exact confound the module exists to remove. |
| `ranges.py` + `loinc_ranges.yaml` | The shared rules. Both arms must get identical rules, `ranges_hash` must pin them, **and the bounds themselves must not be fitted to the evaluation data.** See §6.1 attack E. |
| `inject.py::stratified_plan`, `inject_planned`, `_eligible_*` | Produces the ground truth both arms are scored against. Five prior defects here. |
| `run.py::read_cohort` | Determines the cohort, therefore every number. The `limit // 3` patient quota is load-bearing on the exposure figures too. |
| `metrics.py::f1`, `_counts` | The endpoint arithmetic. |

### P1 — can move a published secondary table

| Target | Why P1 |
|---|---|
| `metrics.py` AgentLeak block | `phi_total` / `phi_exposed` / `leak_mean`. **Confirmed by inspection already:** `run.py:186` calls `leak_record(original, projected)` where `original` is clean but `projected` came from `system.evaluate(modified)`. Numerator and denominator computed on different objects. The bundle's "finding 6", and it is real. Size it. |
| `exposure.py` | The exposure decomposition table. `clamped_mean`, `unclamped_mean`, `union_mean`, `at_ceiling`. Findings 8 and 9. |
| `agents.py` | `REQUIRED_FIELDS`, `NOW_ANCHOR` (defect A), `temperature=0.0`, cache key derivation, `extract_verdict`, and the `uncertain`-not-cached rule blamed for 21 missing entries of 1,800. |
| `run.py` contract | 12 keys, 11 contract plus `cohorts`. Does the contract determine a run? The bundle says no. Verify. |
| `stats.py` + `replicate.py` | `replication_summary.json`. Defect 1 (non-inferiority null centred wrong). Bootstrap pairing, Holm family, and whether a t-interval over **n=4 seeds** is presented as more than it is. |
| The verdict cache | 2006 entries claimed. Is `CACHE_CONTRACT.json` enforced or decorative? Every model-backed number's reproducibility rests here. |
| **F7 attribution check** | El Yagoubi et al. (2026). Cheap, and a fabricated citation for the study's own metric would be among the most serious findings available. |

### P2 — ships, is tested, produces no published number

| Target | Why P2 |
|---|---|
| `phi_text.py` | The 90-to-0 note claim. Substring matching with no word boundaries means it is not the lower bound it claims. Quantify the overcount. |
| `linkage.py` | `SurrogateIssuer` non-derivation, the L0/L1/L2 ladder. Never run end to end. |
| `audit.py`, `rbac.py`, `departments.py` | Admitted unwired or unwritten. Establish exactly what imports them, what calls them, what result they touch. If nothing, that is an architecture finding, not a bug. |
| **Test-quality audit** | Self-reported 11 weak assertions, 4 tautologies, 1 circular test. Re-derive independently: for each test, what wrong implementation still passes? |

### P3 — hygiene

Docstring drift, dead code, internal contradictions in code-facing docs, dependency declarations, `Makefile` targets that do not exist.

Two contradictions already found in the bundle `README.md`:

- `:7` says `paper.pdf` is **30 pages**; `:228` says **26 pages**. At most one is right.
- `:45` says a **twelve-key** run contract, then "**ten of the eleven** keys are contract values". `run.py` writes 12: 11 contract plus `cohorts`. The "ten" is wrong.

Trivial individually. Together they show prose edited without re-checking, which is the process that produces F1 and F5.

---

## 4. Adversarial posture

Three rules that decide close calls, fixed now so they cannot be bent later.

**1. The burden of proof is on the claim.** "I could not find a problem" is not a pass. A claim passes only when the audit can state the positive mechanism by which it is true. Anything else is UNVERIFIABLE, a failing grade for a published number.

**2. Self-reported honesty is not evidence of correctness.** This bundle withdraws two prior headlines, lists its own unfixed defects and calls its own tests weak. That candour makes the remaining claims more likely sound, and it is also exactly what a subtly wrong result looks like from outside. Documented limitations are re-verified at the same depth as undocumented ones, and the audit looks specifically for where a self-report **understates** the problem. The `loinc_ranges.yaml` header is the live example: it discloses the bound-widening honestly, and the disclosure does not draw the consequence.

**3. Convenient results get double scrutiny.** 0.0000 with zero variance across every cell, and any F1 of exactly 1.0000, are treated as suspicious until the mechanism is understood. A perfect number is either a strong finding or a design that could not have produced anything else.

### Priors, committed before checking

Stated now so the audit can be wrong, and so it cannot retrofit its expectations afterwards.

| # | Prediction | Confidence | Tested in |
|---|---|---|---|
| 1 | The three widths **all release code, value and unit**, so the checker's inputs are identical at every width and the 0.0000 gap is analytically forced, not empirically discovered. | High | §5.2 Q4, §6.1 attack B |
| 2 | The exposure numerator/denominator mismatch at `run.py:186` is real and accounts for the whole `0.6100` vs `0.6125` gap. Confirmed by inspection; only magnitude is open. | Very high | §5.4, §6.2 |
| 3 | The zero-collision claim holds, and the trivial set-membership detector scores 1.0000, so the primary endpoint cannot discriminate detector quality. | High | §6.1 attack A |
| 4 | The `loinc_ranges.yaml` bounds were widened until no real cohort value fails, which is fitting the rule file to the evaluation data. Both arms share it so internal validity survives, but the near-1.0 F1 is partly engineered and the FiO2 widening to 10000 is why some cells read 0.9825 rather than 1.0000. | High | §6.1 attack E |
| 5 | At least one of the 11 self-reported weak tests is worse than reported, and at least one weak test **not** on the bundle's list will be found. | Medium | §5.6 |
| 6 | The n=4 t and bootstrap intervals are arithmetically fine and rhetorically unsupportable, and the `0.03` margin predates the noise floor measured from these same runs, making that comparison post hoc. | Medium | §6.2 |
| 7 | `rbac.py`, `audit.py` and `departments.py` are wired to nothing that produces a published number. | High | §6.4 |
| 8 | The headline reproduces exactly. Its problem is interpretive, not arithmetic. | High | §5.3 |
| 9 | "El Yagoubi et al. (2026)" resolves to a real paper. | Low-to-medium confidence, high impact if wrong | §5.5 |

If prior 8 fails, §9 stops the audit on the spot. If priors 1, 3 and 4 all hold, the headline is arithmetically correct and substantially weaker than it reads, which is the most likely outcome and the thing phase 2 must handle.

---

## 5. Execution iteration E1: does the code do what it says

**Direction:** bottom-up. Read the code, then check the artefacts trace to it. No attacking yet. Establish ground truth.

### 5.1 Baseline health, before anything else

- `cd codebase && make install && .venv/bin/pytest tests/ -q`. Actual pass count against the claimed **195**.
- `ruff` clean, as claimed.
- `.llm_cache/` entry count against the claimed **2006**; `CACHE_CONTRACT.json` present and actually read by `assert_cache_contract`.
- Cohort file counts (125 / 100 / 100) and the `read_cohort` = 200 check from `PROVENANCE.md`, including the SHA-256 over ordered `(resourceType, id)` pairs.

Any mismatch is itself a finding: these are the numbers the bundle uses to assert its own health.

### 5.2 P0 code read

Each module against its docstring, then against the artefact it produces. Specific questions to answer with file:line:

1. **`controlled.py::inject_cohort`** claims to draw the RNG "in exactly the order `run.run_cell` draws it". Verify by construction. Different orders mean the controlled result and the harness result carry different ground truth.
2. **`controlled.py::score_projected`** scores a `None` slice as "not flagged". Does `a1_plausibility_ranges` also pass on those same resources? If Baseline flags a Patient that Confined never sees, the arms are asymmetric and the gap is masked rather than absent.
3. **`manifests.py::project`** returns `{resourceType, _agent_id, _dimension, _projected_fields}`. Confirm what the orchestrator hands `phi_exposed`: markers only match inside `_projected_fields`, so passing the outer dict yields exposure 0 silently.
4. **Do the three widths differ at all on the fields the range checker reads?** It reads code, value, unit. If all three widths release all three, the independent variable never varies. Highest-value question in the audit.
5. **`inject.py`** defects 2, 3, 4, 5, D re-verified, plus adjacent unfixed cases in the same functions.

### 5.3 P0 artefact reproduction

- Re-run `python -m dqa.controlled` to a scratch path, diff against `controlled_comparison.json`.
- Confirm `manifests_hash` and `ranges_hash` match the shipped files today.
- Verify `n_plausibility` per cell (27 / 27 / 29) is what `stratified_plan` actually produces at rate 0.30.
- **Verify the non-1.0 cells by hand** (0.9825, 0.9811, 0.9615): which resource is misscored, FP or FN, and is the FiO2 widening the cause? A near-perfect score with an unexplained miss is where mislabelling hides.

### 5.4 P1 and P2 code read and reproduction

As registered in §3. Each `results/` artefact gets its generating command identified, re-run if offline, diffed. Model-backed artefacts get cache-replay first; a live run only under the §1 policy.

### 5.5 Provenance matrix, including F6 and F7

One table: **claim -> source artefact -> generating command -> reproduced?**

Numbers that must resolve:

- `max_abs_gap_pp = 0.0000` across 12 cells
- Exposure: `0.6125` Synthea pooled, `0.6700` MIMIC and eICU, `134/200`, `0.1138` Patient at full width, `55.5` to `67.0` per cent at ceiling
- Injector: legacy plausibility `n=2`, consistency `n=0`, `9 of 20` wasted; stratified `n=27-29`
- Stratified baseline plausibility F1 `0.6500` / `0.7143` / `0.6818` vs the old constant `0.6666666666666666`
- PHI text `90 -> 0` over `45` notes
- Surrogate exposure `1.0000 -> 0.0000`, Synthea `0.9142`
- Reproducibility `10 of 60`, `6 of 60`, `4 values`, max delta `0.0085`, `21 of 1,800`
- Noise floor `0.0005 to 0.003` vs the `0.03` margin
- `loinc_survey.json` `62,188` values
- Counts: `195` tests, `2006` cache entries, `325` cohort files, per-file test counts `44` / `13` / `10` / `16`
- eICU FiO2 artefacts: `4000` and `10000`, `n=3`, `41-observation` group; temperature `99.8`, `n=3`

Plus the attribution checks: El Yagoubi et al. (2026), the two PhysioNet DOIs, Synthea (Walonoski et al. 2018), the three `45 CFR 164.514(b)(2)` citations, and the model snapshot ID.

### 5.6 Test-quality register

For each test: what wrong implementation still passes? Hunt for

- a function asserted against itself or a re-implementation of itself
- assertions on type or shape but never value
- fixtures synthesised by the code under test
- literals captured from output rather than derived independently
- **guard tests that constrain the data rather than the code**, such as the `test_ranges.py` false-positive guard requiring zero real values to fail, which turns a test into a fitting procedure

### E1 exit criteria

E1 is done when: the suite has been run and counted; every P0 module has been read against its docstring; `controlled_comparison.json` has been regenerated and diffed; every `results/` artefact has a generating command or is marked orphaned; every number in §5.5 is matched, traced or reported; and every finding has a P-level, a severity and evidence.

**Output:** `AUDIT_ITERATION_1.md`.

---

## 6. Execution iteration E2: try to break it

**Direction:** top-down and adversarial. Assume the headline is wrong and try to prove it. Runs only after E1, because you cannot attack a claim whose mechanism you have not read.

### 6.1 P0 attacks on the headline

Claim under attack: *field projection costs zero detection, exactly, everywhere.*

| Attack | What it means if it lands |
|---|---|
| **A. The task is too easy** | Self-admitted. Verify the zero-collision claim exhaustively across all three cohorts. Then build the trivial 5-line set-membership detector and confirm it also scores 1.0000. If a detector with no clinical knowledge ties a range-based one, the endpoint cannot discriminate detectors and the equality is near-tautological. |
| **B. The independent variable never varies** | If every field the checker reads survives every width, the result is **analytically necessary, not empirical**. The bundle states this as the structural explanation without drawing the consequence. Highest-probability attack. |
| **C. The arms are not the same checker** | Differential-fuzz `a1_plausibility_ranges` against `check_plausibility_ranges` over every resource in every cohort, plus adversarial synthetics: missing unit, non-numeric value, multi-component Observation, unknown code, unit alias, null value. Any disagreement means "controlled" holds by luck on this data. |
| **D. The design cannot detect a penalty** | Zero variance over 12 cells at F1 near 1.0. Compute the minimum detectable difference. If the design could not have found a real penalty, "no penalty found" is weak evidence of absence. |
| **E. The rule file is fitted to the evaluation data** | `loinc_ranges.yaml` states its bounds were widened until zero real cohort values fail, enforced by a test. One widening (FiO2 to 10000) explicitly lets the injected 9999.0 through. So the shared clinical knowledge was calibrated on the cohorts it is evaluated against, and the injected constant interacts with a bound tuned because of a real value near it. Both arms share the file, so the *controlled* comparison survives; what does not survive unqualified is any claim that the F1 levels represent detector quality on unseen data. Quantify: how many bounds were widened, by how much, and how many injected defects are undetectable as a result. |

Attack E is new in planning iteration 2 and did not exist in the bundle's own three review passes.

### 6.2 P1 attacks on the secondary results

- **Exposure numerator/denominator mismatch** (`run.py:186`): compute both consistent variants, report how far `agentleak_mean` moves, verify the `0.6100` vs `0.6125` arithmetic exactly.
- **Exposure measures cohort composition, not manifests.** Pooled `0.6700` is `134/200`, fixed by `limit // 3`. Test the strong version: hold manifests constant, vary `limit`, show the metric moves. If it does, the pooled figure is a property of the sampler and must never be reported alone.
- **`at_ceiling` counts ties**, so it cannot evidence clamp loss. Build the strict variant, report the real number.
- **`union_mean == clamped_mean` in all 9 cells.** Confirm the cause and that the column is therefore uninformative as published.
- **Statistics.** n=4. Seeds resample the *injector*, not patients, sites or time, so any CI is an interval over injector randomness only. Check `stats.py` and `replicate.py` do not present it as wider. Check degenerate cells (sd=0, mean=0) are not producing empty p=1.0 rows presented as evidence. Establish whether `0.03` was pre-declared or chosen after the noise floor was measured from these runs.

### 6.3 P1 attack on reproducibility

The bundle's most interesting claim: the run contract does not determine a run, and the cache is a primary experimental artefact.

- Independently reproduce `10 of 60`, `6 of 60`, `4 values`, max delta `0.0085`.
- Verify the `agents.py:265` mechanism by counting real cache misses in a replay against the claimed 21 of 1,800.
- Test enforcement: mutate a `CACHE_CONTRACT.json` field in a scratch copy, confirm the harness refuses to replay.
- **This is the most likely place a live model run is justified.** Separating "the cache is stale" from "the provider is non-deterministic" may require fresh calls. Scope it to one cohort and one dimension first.
- Answer plainly: if reproducibility requires shipping a cache, are the model-backed results reproducible in any useful sense? The answer applies to every non-headline table.

### 6.4 P2 attack on the architecture as built

Three systems, tested for equality:

1. the system the code **describes** in its docstrings
2. the system the code **implements**
3. the system the evaluation **exercises**

Gaps to size precisely:

- `audit.py` produces hash-chained records that no shipped pipeline writes to disk. No `audit/` directory, no `make audit`.
- `departments.py` unwired, and its dimension assignments disagree with the shipped manifests.
- `rbac.py` "restored": does the orchestrator enforce it or merely hold it?
- The L0/L1/L2 ladder never run end to end, because `REQUIRED_FIELDS` craters completeness under surrogates. A design, not a result.
- **The headline path contains no language model.** Plausibility only, deterministic range checker both arms. State what fraction of the claimed architecture the central result exercises.

A codebase finding, not a paper finding. The question here is only "what is wired to what". What it means is phase 2.

### 6.5 Second pass over E1

Re-check every E1 finding marked unverified, and re-read the P0 modules knowing what the attacks found. E2 is also the second look at the first look.

### E2 exit criteria

E2 is done when: every attack A-E has a result, not an opinion; every claim in the E1 provenance matrix has one of the five §7 verdicts; the architecture wiring map is complete; the statistics register is complete; and every E1 unverified suspicion has been resolved or explicitly left open with a reason.

**Output:** `AUDIT_ITERATION_2.md`.

---

## 7. Verdict and severity

| Verdict | Meaning |
|---|---|
| **SUPPORTED** | Reproduced, description matches evidence strength. |
| **SUPPORTED-WEAK** | Reproduced, evidence thinner than the description. Reword, do not retract. |
| **UNVERIFIABLE** | No artefact, or not regenerable. Not necessarily false, cannot stand as published. |
| **MISLABELLED** | Number right, description of it wrong. |
| **WITHDRAW** | Contradicted by re-derivation. Remove or correct. |

Severity, independent of P-level:

- **CRITICAL** moves the headline
- **HIGH** moves a published table
- **MEDIUM** description overclaims, no number moves
- **LOW** cosmetic, internal inconsistency, dead code

---

## 8. Deliverables

| File | Contents |
|---|---|
| `AUDIT_PLAN.md` | This document. |
| `AUDIT_ITERATION_1.md` | E1: provenance matrix, defect register, dead-code map, test-quality register. |
| `AUDIT_ITERATION_2.md` | E2: attack results, verdicts, architecture wiring map, statistics register. |
| `AUDIT_FINDINGS.md` | Merged, deduplicated, severity-ranked, each with a recommended action and whether applied or proposed. |
| Verification scripts | Written to the scratchpad, not into the repo, unless one deserves to become a real test. Re-derivations are written from definitions, never by calling the function under test. |
| Applied fixes | Safe and clearly correct only. Number-moving fixes proposed with before and after. |
| Model-run log | Every live call: reason, scope, cost. Empty if none were needed. |

---

## 9. Stop conditions

Execution halts and reports immediately, without finishing the remaining checks, on any of:

- A headline number that cannot be reproduced from the shipped code and data.
- A results artefact whose contents could not have been produced by any code in the repository.
- A results artefact contradicted by re-running its own generating command.
- A citation for a core method, metric or dataset that does not exist.

These are the fabrication conditions. Everything else is a finding, not a stop.

---

## 10. What planning iteration 2 changed

| # | Change | Why |
|---|---|---|
| 1 | Model runs permitted under stated conditions rather than near-prohibited | The original rule would have blocked the one place a live call is genuinely diagnostic: separating a stale cache from a non-deterministic provider. |
| 2 | Added F6 and F7, fabricated inputs and attributions | The taxonomy only covered outputs. A study can be fabricated through invented clinical thresholds or a non-existent citation just as easily. `metrics.py:6` cites a 2026 paper for the study's own exposure metric and nothing yet verifies it. |
| 3 | Added attack E, the rule file is fitted to the evaluation data | Found while checking F6. `loinc_ranges.yaml` discloses that bounds were widened until no real value fails and that FiO2 was widened to 10000 so the injected 9999.0 escapes. None of the bundle's three prior review passes raised this. |
| 4 | Added the independent re-derivation rule | The bundle's own circular test happened because a check called the code it was checking. The audit must not repeat that. |
| 5 | Added guard-tests-that-constrain-data to the test-quality hunt | Same root cause as attack E, and it is a test smell worth naming. |
| 6 | Added exit criteria to E1 and E2 | "Done" was undefined, which is how an audit quietly stops at the easy findings. |
| 7 | Split planning iterations from execution iterations explicitly | They were conflated in version 1. |
| 8 | Recorded what is already established about data provenance | Avoids re-doing settled work and keeps the audit's attention on what is open. |
