# Least-Privilege Multi-Agent EHR Data Quality Assurance

Course proof-of-concept for HINF 6497 (Fordham University, Summer 2026).
Companion code for the paper *Least-Privilege Multi-Agent Data Quality
Assurance for Electronic Health Records* by Mohsin Zafar.

## What it does

Four data-quality agents (completeness, plausibility, consistency, timeliness,
per Kahn et al. 2016) check FHIR resources for injected defects. Each agent sees
only the fields its **privilege manifest** releases; a JSONPath projection step
between the orchestrator and each agent enforces the boundary. Three manifest
widths (minimal, intermediate, full) sweep how much each agent may see. A
monolithic full-access baseline (**Baseline**) provides the detection ceiling.

The headline question is what that projection boundary *costs*. Answering it
requires both arms to run the same check, which is what
`manifests/rules/loinc_ranges.yaml` and `dqa.controlled` are for.

## Quick start

```bash
make install-locked   # frozen versions, CPython 3.12.2
make verify-data      # prove you have the same cohorts
make reproduce        # regenerate every offline result and diff it
```

That needs **no API key and no network**. See `../REPRODUCE.md` for the full procedure and for what
cannot be reproduced.

## Running the model-backed parts

Most of this study is offline. Two of the four agents, plausibility and consistency, can call a
language model, and the runs that use them need credentials.

### The API key

The client is `anthropic.Anthropic()` at `src/dqa/agents.py:578`, constructed with no arguments, so it
reads the standard environment variable:

```
ANTHROPIC_API_KEY
```

Set it in your shell before any target that calls the model:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
make eval-stratified
```

Get a key from https://console.anthropic.com. **Do not commit it.** Nothing in this repository reads a
`.env` file, and no key is stored anywhere in the tree.

To force the offline path deliberately, unset it for one command. This is what `make eval-offline` and
`make audit` do:

```bash
ANTHROPIC_API_KEY= .venv/bin/python -m dqa.run --out results/scratch.json
```

With no key, `agents.py:567` skips the call and the verdict becomes `uncertain`, which scores as "did
not flag". Offline detection numbers are therefore **not** comparable to model-backed ones. The
controlled comparison and the ablation are unaffected, because they call no model at all.

### Changing the model

The snapshot is pinned in two places that must agree, `agents.py:36` and `run.py:92`, both currently:

```
claude-haiku-4-5-20251001
```

Override it without editing code by exporting a second variable:

```bash
export ANTHROPIC_MODEL_SNAPSHOT=claude-sonnet-4-5-20250929
make eval-stratified
```

`run.py:493` writes whatever you set into the `model_snapshot` field of the run contract, so the output
file always records which model produced it.

**Two consequences worth knowing before you change it.** First, the model is part of the cache key
(`agents.py:304`), so a different snapshot means a cold cache and real API spend for every payload.
Second, a run under a different model is not comparable with the published tables, and the run contract
is what tells a reader that.

### Cost and volume

A full stratified run is 1,800 payload lookups per seed. The shipped cache
(`.llm_cache/`, 2,005 verdicts) covers most but not all of them: 478 lookups have no entry, because a
verdict is stored only when it is not `uncertain`. Expect roughly that many live calls on a first run,
and near zero on a repeat with the same model.

## The headline result, in one command

```bash
make install
make controlled     # no API key, no network, no clock
```

Plausibility F1 with an identical per-analyte range checker on both arms,
across four seeds and three cohorts, at all three manifest widths. Every
confined configuration equals the full-access baseline exactly:
**maximum absolute gap 0.0000 pp in all twelve cells.** Output goes to
`results/controlled_comparison.json`.

That is a real statement about field visibility and a weak one about detection
ability; the injector draws implausible values from four constants, and any
adequately equipped checker saturates on them. The paper says so, and so does
the module docstring.

## What things are called

The paper and the result files use different names for the same things. This is the translation, and
`src/dqa/naming.py` is the one place it is defined in code.

**The two systems.**

| Paper | Result files | What it is |
|---|---|---|
| Baseline | `baseline` | One program with full record access. No projection boundary, so it sees every field. |
| Confined, minimal | `confined-minimal` | Four separate agents, each restricted to its manifest slice. Narrowest manifest. |
| Confined, intermediate | `confined-intermediate` | Same four agents, wider manifest. |
| Confined, full | `confined-full` | Same four agents, widest manifest. |

There is no `A2` through `A5`. The design started with six arms and was cut to two; the numbering is
left over from that and means nothing.

**The four departments.** These are the tiered design, and the ids are just tier order.

| Paper | Result files | Holds identifiers? | Job |
|---|---|---|---|
| Triage | `D0` | no | Reads `resourceType` and routes. Holds nothing. |
| Registration | `D1` | **yes** | The Patient tier. Identifiers are its legitimate subject. |
| Records Linkage | `D2` | **yes** | Resolves an event's reference once, then issues a run-scoped surrogate. |
| Clinical Checks | `D3` | no | Judges event content against the surrogate. Never sees a raw reference. |

The whole tiered result rests on the last row. `D3` does the clinical work and cannot link a record to a
person, which is why exposure drops 98.6% while detection is unchanged.

**Use the constants, not the strings.** `naming.py` is the one place these words are defined, so code
compares against the constants:

```python
from dqa.naming import BASELINE, RECORDS_LINKAGE, confined

out["systems"][BASELINE] = ...        # the full-access arm
if entry == RECORDS_LINKAGE: ...      # compares "D2", reads as its job
```

`tests/test_naming.py` pins every constant to its wire value and checks the shipped artefacts still open
with them, so the two naming schemes cannot drift apart silently.

## Layout

```
src/dqa/            17 modules, 4,240 lines
  manifests.py      schema, loading, projection boundary   (the enforcement point)
  agents.py         the four Confined agents + orchestrator
  baseline.py       Baseline, both variants: envelope and ranges
  ranges.py         per-analyte physiological rules + the pure checks over them
  controlled.py     the controlled comparison: same checker, different visibility
  inject.py         seeded defect injection: legacy and stratified allocators
  metrics.py        detection F1 + AgentLeak exposure
  exposure.py       exposure decomposed by resourceType; clamp and union means
  run.py            harness + CLI
  phi_text.py       linkage-aware Safe Harbor detection over free text
  linkage.py        reference-rewriting ladder
  departments.py    departmental scoping of the manifests
  rbac.py           role-to-manifest binding
  audit.py          append-only verdict register
  stats.py          bootstrap CIs, Holm-Bonferroni
  __init__.py
manifests/          minimal/ intermediate/ full/  (4 YAML manifests each)
  rules/            loinc_ranges.yaml: 322 entries over 292 codes plus a default
tests/              195 tests in 12 files, 2,813 lines
data/               the three cohorts themselves, 325 files and about 500 MB
results/            run outputs; see results/README.md for which file is current
scripts/            make_figure.py
```

**Eleven of the seventeen modules are reachable from `dqa.run`, `dqa.controlled` or `dqa.replicate`**
(`run`, `controlled`, `agents`, `baseline`, `ranges`, `inject`, `manifests`,
`metrics`, `exposure`). The remaining seven -- `phi_text`, `linkage`,
`departments`, `rbac`, `audit`, `stats`, `__init__` -- are exercised by the test
suite only. They are designed extensions that are not yet wired into the
evaluation, and nothing in the paper's results depends on them. Saying so here
is deliberate: an earlier edition of this README described a codebase that was
not the one in the folder.

No framework. The orchestrator is a deterministic loop; the LLM agents call the
Anthropic API directly with a pinned snapshot at temperature 0.0.

## Commands

| Target | What it runs | Needs a key |
|---|---|---|
| `make install-locked` | Virtualenv from `requirements-lock.txt`, the versions the paper used | no |
| `make verify-data` | Cohort digests against `data/COHORT_MANIFEST.json` | no |
| `make reproduce` | Every offline artefact, regenerated and diffed | no |
| `make test` | 348 tests | no |
| `make lint` | `ruff check src tests scripts` | no |
| `make ablation` | Privilege ablation -> `results/privilege_ablation.json` | no |
| `make audit` | Hash-chained run log -> `audit/run.jsonl`, verifies its own chain | no |
| `make controlled` | The headline: `dqa.controlled` -> `results/controlled_comparison.json` | no |
| `make eval-stratified` | Main run: stratified allocator, rate 0.30 -> `results/results_stratified_model.json` | yes |
| `make gap` | Envelope against ranges on both arms; prints the checker effect | no |
| `make eval` | **Superseded** legacy path, rate 0.10 -> `results/results_legacy_rerun.json` | yes |
| `make eval-offline` | Legacy path with the LLM dimensions returning "uncertain" | no |
| `make figure` | Figure 1 from `results/results_stratified_model.json` | no |

### CLI flags

```
python -m dqa.run [--dataset synthea|mimic-iv-demo|eicu-demo|all]
                  [--limit N] [--rate R] [--seed S] [--out PATH]
                  [--allocation legacy|stratified]
                  [--baseline envelope|ranges]
                  [--exposure-basis published|consistent]
                  [--audit-log PATH]
                  [--no-cache]

python -m dqa.controlled [--out PATH] [--rate R] [--limit N] [--seeds S ...]
                         [--injection-mode legacy|hard]

python -m dqa.ablation   [--out PATH] [--rate R] [--limit N] [--seeds S ...]

python scripts/cohort_manifest.py [--write] [--limit N]
python scripts/csv_to_fhir.py --source mimic-iv-demo|eicu-demo --src DIR
                              [--max-patients N] [--max-obs-per-patient N]
python scripts/linkage_exposure.py
```

`--allocation` (default `legacy`) selects the injector. `legacy` reproduces the
published results exactly: it picks a dimension before looking at the resource,
so at seed 42 and rate 0.10 it realises 4 completeness, 5 timeliness, **2
plausibility** and **0 consistency** defects, wasting 9 of 20 selections.
`stratified` allocates against eligibility, so every planned defect is realised.
`tests/test_inject_stratified.py` pins both.

`--baseline` (default `envelope`) selects the plausibility rule, **for both arms
at once**. `envelope` is the published configuration: Baseline applies its wide
`[-1000, 100000]` numeric envelope and the Confined plausibility agent calls the
model. `ranges` hands Baseline and every Confined width the same
`manifests/rules/loinc_ranges.yaml`, which removes the checker from the
comparison and leaves field visibility as the only difference. Changing the rule
for one arm and not the other is what the flag exists to prevent.

`--injection-mode` (default `legacy`) selects the plausibility injector.
`legacy` substitutes one of four constants that no real value collides with, so
a five-line set-membership test scores F1 = 1.0000 and the endpoint cannot rank
detectors. `hard` derives each value from the analyte's own bound in the rule
file, 2 to 15 per cent outside it. Measured over 83 hard defects: set-membership
catches 0, the crude `[0, 9000]` envelope 5, the per-analyte check 83.

`--exposure-basis` (default `published`) selects which object the AgentLeak
ratio is computed on. `published` reproduces the shipped numbers exactly, taking
the numerator from the injected resource and the denominator from the clean one.
`consistent` uses the injected resource for both. Both means are written to
every Confined cell regardless, so the choice affects which is headline, not what is
recorded.

`--audit-log PATH` writes a hash-chained JSONL record of the run and verifies
the chain before exiting. Off by default.

`--no-cache` ignores the on-disk verdict cache, forcing the deterministic
offline path.

## The run contract

`dqa.run` writes thirteen top-level keys, twelve of them contract values and the
last the results payload. A cell whose contract differs is not comparable with one
whose does not.

| Key | Meaning |
|---|---|
| `model_snapshot` | the pinned model, or the `ANTHROPIC_MODEL_SNAPSHOT` override |
| `limit` | resources read per cohort (default 200) |
| `injection_rate` | share of the cohort selected for a defect |
| `seed` | seeds the injector, and nothing else |
| `allocation` | `legacy` or `stratified` |
| `baseline` | `envelope` or `ranges` |
| `now_anchor` | the fixed evaluation clock the timeliness rules use |
| `resource_types` | the admitted FHIR types, from `ADMITTED_RESOURCE_TYPES` |
| `cache_enabled` | whether verdicts may come from the disk cache |
| `primary_endpoint` | the single pre-declared endpoint, `f1_plausibility` |
| `cohorts` | the per-dataset results |

Each cohort additionally records `manifests_hash` per width and
`realised_defects` per dimension, because a detection score is uninterpretable
without the n behind it.

Two determinants are deliberately constants rather than flags: the injector's
far-future anchor (`inject.FUTURE_ANCHOR`, 2126-01-01) and the decoding
temperature, pinned at 0.0. Temperature 0.0 is necessary for reproducibility and
not sufficient for it.

`now_anchor` is 2026-08-05T00:00:00+00:00, the day the current results were
regenerated. Both timeliness rules previously read `datetime.now()`, so
"implausibly old" and "in the future" slid forward daily and a published
timeliness F1 was reproducible only on the day it was computed. Sweeping every
timestamp the rules read across all three cohorts, the nearest approaches the
30-year past boundary by 1,865 days and the present boundary by 2,403 days, so
any anchor within about five years of this one gives identical verdicts. The
switch left every published number unchanged.

## Datasets

All public, no data use agreement required. All three ship inside `data/` as real
directories, so there is nothing to download or link before the code will run.

- Synthea (Walonoski et al. 2018): synthetic FHIR R4 bundles, 125 files, 498 MB
- MIMIC-IV Clinical Database Demo v2.2 (Johnson et al. 2023), converted to FHIR
  by the adapter in the v1 codebase, 100 files, 1.2 MB
- eICU-CRD Demo v2.0.1 (Pollard et al. 2018), same adapter, 100 files, 1.1 MB

`data/PROVENANCE.md` records the source, licence and citation for each, which
resource types the harness actually reads, and how to check that the cohorts
load as the published results assume.

The three directories are listed in `.gitignore`, because 500 MB is too large to
commit. That is a statement about the repository and not about this bundle: a
clone will not carry them, the bundle does. `data/README.md` and
`data/PROVENANCE.md` are explicitly un-ignored so the documentation travels with
the repository even when the corpora do not.

No PHI, real or synthetic, is sent anywhere except the manifest-permitted slices
to the Anthropic API.

## Known cohort artefacts

MIMIC-IV de-identification shifts dates into the 2100s, so every clean MIMIC
resource fails the future-timestamp rule; timeliness F1 is uniformly low on that
cohort at every width and for Baseline. This is a cohort-shape effect, not a manifest
effect (it is identical across all widths), and it is discussed in the paper's
Results section.

No Observation in any of the three cohorts carries a FHIR `referenceRange`
element -- verified by sweeping all three directories. That is why the confined
plausibility agent had no ranges to work from, and why the shared rule file had
to be built rather than read out of the data.

## Relationship to the superseded bundle

This is the third edition. The first (`../../1_primary/`) is the one this
replaces, and the differences that matter are these.

**What changed in the science.** The first edition's primary endpoint was
computed over two injected plausibility defects per cell and zero consistency
defects, so its headline F1 figures measured the allocator rather than the
systems. Its two arms were never running the same plausibility check. Its
exposure metric moved with cohort composition rather than with manifest width.
Its model calls ran at the API's default sampling temperature, so the study was
not reproducible from its own contract. Each of those is now measured, pinned by
a test, and replicated across four seeds.

The correction reversed one of this project's own claims. An earlier draft
reported that the confined system *beat* the full-access baseline by 10 to 22
points at rate 0.30. Giving the baseline the same range file raises it to
1.0000, 1.0000 and 0.9825 and reverses the direction. Neither figure was about
privilege. The paper reports the reversal rather than quietly dropping it.

**What changed in the code.** Seven modules and 1,039 lines became seventeen
modules and 4,240 lines; one test file with 18 tests became twelve files with 195.
(The first edition's README said "six modules"; it did not count `__init__.py`.
`tests/test_dqa.py` is byte-identical to the first edition's only test file, so
those 18 tests are still run unchanged.) `ranges.py`, `exposure.py`,
`phi_text.py`, `linkage.py`, `departments.py`, `rbac.py`, `audit.py`,
`stats.py` and `controlled.py` are new; `inject.py` gained the stratified
allocator alongside the legacy one; the run contract grew from three recorded
keys to eleven.

**What is compatible.** The legacy path is intact. `make eval` and
`--allocation legacy --baseline envelope` still produce the first edition's
configuration, and Baseline's scores under it are bit-identical to
`results/results_published_v1.json`. That file is the first edition's
`results.json`, archived under a name that says what it is; see
`results/README.md`.
