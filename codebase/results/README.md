# results/

Every result artefact in the bundle lives here. The bundle-level `results/` directory is a pointer to
this one and holds no data of its own.

The current results are `controlled_comparison.json` (the headline) and `results_stratified_model.json`
(the main model run). `results_published_v1.json` is the **superseded** 2026-08-02 run, kept only for
reference.

## Why the old file is superseded, not just old

`results_published_v1.json` was produced by the legacy allocator at injection rate 0.10. That allocator
picks a defect dimension before looking at the resource, so at seed 42 it realised 4 completeness,
5 timeliness, **2 plausibility** and **0 consistency** defects per cell: 9 of every 20 selections were
spent on resources that could not carry the chosen defect. Every plausibility F1 in that file therefore
rests on two positives, and the consistency column is 0.000 everywhere because consistency was never
tested rather than because it failed. `tests/test_inject_stratified.py` pins those exact counts so the
comparison between the two allocators cannot drift.

It also compared two systems that were not running the same check: the monolith applied a single
`[-1000, 100000]` envelope to every analyte while the confined plausibility agent had no reference
ranges at all. That confound is what `controlled_comparison.json` exists to remove.

The file was originally named `results.json` and was byte-identical to the previous edition's, which is
how the audit established that the headline artefact had never been regenerated.

## Every shipped file, and what regenerates it

Commands are run from `codebase/` after `make install`. Where a file has no command, it is historical:
it records a run that was made once, and nothing shipped here reproduces it.

| File | What it is | Regenerate with |
|---|---|---|
| `controlled_comparison.json` | **Current headline result.** Plausibility F1 under one range-based checker on both arms, 4 seeds x 3 cohorts x 3 widths, 12 cells. Max absolute gap 0.0000 pp. **Read with `privilege_ablation.json`:** audited 2026-08-06, that 0.0000 is a mathematical identity, not a measurement, because all three widths release every field the checker consumes | `make controlled` |
| `privilege_ablation.json` | **What the headline cannot tell you.** Withholds one consumed field at a time from the minimal manifest and re-runs the same 12 cells, turning the identity into a dose-response curve: withholding `$.valueQuantity.value` costs 100.00 pp, `$.valueQuantity.unit` 28.04 pp, and `$.code.coding[*].code` *gains* 3.85 pp because every Observation falls back to the `[0, 9000]` default. Carries two controls that must both read 0.0000 pp: the unmodified minimal manifest, and `withhold_ucum_code`, which withholds a field released by every width but consumed by neither arm. That negative control is the evidence that the headline zero is a released-superset-consumed artefact rather than a property of privilege | `make ablation` |
| `controlled_comparison_hard.json` | The headline re-run under `--injection-mode hard`, where each injected value is derived from the analyte's own bound rather than drawn from four constants. Set-membership catches 0 of 83 hard defects, the crude envelope 5 of 83, the per-analyte check 83 of 83, so the endpoint finally ranks detectors correctly. **Max absolute gap is still 0.0000 pp in all twelve cells**, which is the answer to "the equality only holds because the task was trivial" | `.venv/bin/python -m dqa.controlled --injection-mode hard --out results/controlled_comparison_hard.json` |
| `linkage_exposure.json` | Event-resource Safe Harbor exposure at linkage rungs L0 (raw reference) and L1 (run-scoped surrogate), by cohort and manifest width, over 134 event resources per cohort. Evidence for the surrogate result in the paper: 1.0000 to 0.0000 on MIMIC-IV and eICU-CRD, 0.9142 to 0.0000 on Synthea. Generated 2026-08-06, previously the paper's only unartefacted figure | `.venv/bin/python scripts/linkage_exposure.py` (offline, no model) |
| `results_stratified_model.json` | **Current main run.** Stratified allocator, rate 0.30, seed 42, all three cohorts, live model | `make eval-stratified` |
| `rep_seed43.json` | Replication of the main run at seed 43 | `.venv/bin/python -m dqa.run --allocation stratified --rate 0.30 --seed 43 --out results/rep_seed43.json` |
| `rep_seed44.json` | Replication at seed 44 | same command with `--seed 44` and the matching `--out` |
| `rep_seed45.json` | Replication at seed 45 | same command with `--seed 45` and the matching `--out` |
| `replication_summary.json` | Mean, standard deviation, Student's t and percentile-bootstrap 95 per cent intervals of the confined-full-minus-Baseline gap over the four runs above, seeds 42 to 45, plus Holm-Bonferroni across all eighteen cohort-by-dimension comparisons and the pre-declared non-inferiority test. Seed 42 is read from `results_stratified_model.json`, not from a `rep_seed42.json`, which is why there is no such file. **WARNING, added 2026-08-06: this file reports the WITHDRAWN claim.** Its plausibility rows show confined-full beating Baseline by +25.38, +17.55 and +26.77 pp with `holm_reject: true`, which is exactly the "confined system beats the baseline" result that README §3 withdraws as an artefact of the envelope comparator. The file carries no withdrawal marker of its own. Read it only alongside `controlled_comparison.json`. Two further cautions: the bootstrap resamples **four** points, so on the zero-variance rows the 95 per cent interval has width exactly 0 and the non-inferiority p-value is a step function taking only 0 or 1; and the seeds resample the injector only, not patients, sites or time, so no interval here is a population interval | `make replicate` |
| `results_stratified_r010.json` | Stratified allocator at the published rate 0.10, which isolates the rate change from the allocator change | `.venv/bin/python -m dqa.run --allocation stratified --rate 0.10 --out results/results_stratified_r010.json` |
| `results_stratified_offline.json` | Stratified allocator, rate 0.30, model dimensions forced offline. **Detection numbers are a cache artefact and invalid.** The exposure numbers in it are valid | `ANTHROPIC_API_KEY= .venv/bin/python -m dqa.run --allocation stratified --rate 0.30 --out results/results_stratified_offline.json` |
| `results_legacy_model.json` | The legacy path re-run live against the restored cache. This is the run that topped the cache up, and it is what an offline replay reproduces today | `make eval`, which writes `results/results_legacy_rerun.json` so the archive cannot be overwritten |
| `results_legacy_offline.json` | The legacy path replayed offline **against the cache as it stood before that live run**. It is kept because it is the evidence for the cache-dependence described below | historical. `make eval-offline` today writes `results/results_offline.json` and produces the numbers in `results_legacy_model.json`, not these |
| `results_published_v1.json` | **Superseded**, 2026-08-02. The first edition's `results.json`, archived under a name that says what it is. Kept so the ladder in the paper can be checked against its first rung | historical. `make eval` reproduces the configuration but writes elsewhere |
| `loinc_survey.json` | Survey of every numeric observation in all three cohorts, 62,188 values, with units and observed extrema. The evidence base for every bound in `manifests/rules/loinc_ranges.yaml` | historical. No generator script ships. `tests/test_ranges.py` consumes it and asserts it is present |
| `figure1_tradeoff.pdf` | Detection against exposure across the three widths. **Plots superseded numbers.** The paper carries no figure, and says so, because this curve draws the detection results the paper withdraws. Retained for reference only, and referenced by nothing | `make figure`, which reads `results_stratified_model.json` |

Two Makefile targets write filenames that are not in this directory, by design.
`make eval` writes `results_legacy_rerun.json` and `make eval-offline` writes `results_offline.json`,
both so that a rerun cannot silently overwrite `results_published_v1.json` or the archived offline
replay. Neither output ships; both are scratch files you can compare against the archives above.

`slides/figure1_tradeoff.png` at bundle level is the same superseded plot in raster form. No slide
references it.

## Reproducibility

`controlled_comparison.json` calls no model, opens no socket and reads no clock, so `make controlled`
reproduces it byte for byte offline. Nothing below applies to it.

Everything else that touches the model depends on the cache, and the cache is why the numbers move.

**Re-measured 2026-08-06; the previous figures here were wrong.** Replaying the legacy path offline
against the shipped `.llm_cache` today differs from `results_published_v1.json` on **18 of the 60**,
with a maximum delta of **0.2105**. The superseded claim was 10 of 60 at a maximum delta of 0.0085.

The mechanism is as described, and its magnitude was understated by a factor of twenty-three: **478** of
the run's 1,800 payload lookups have no cache entry (not 21), because `agents.py` stores a verdict only
when it is not `uncertain`, and a lookup with no entry collapses to `uncertain`. The worst case is
MIMIC-IV, where the Confined plausibility column falls to **0.0000 at all three widths** against a published
0.0870 / 0.1053 / 0.2105.

The current main run does not reproduce either: replaying `results_stratified_model.json` offline
differs on 4 of 60, and running it **with the model live** differs on 6 of 60, both at a maximum delta
of 0.0179. Running the model live reproduces the published table *worse* than replaying offline.

An earlier edition attributed the residual drift to non-greedy decoding at the provider. That is
**unsupported**: 40 real payloads asked twice at temperature 0 with the cache disabled produced 0
disagreements, on verdict and on justification text. Cache coverage explains everything observed.

The archived `results_legacy_offline.json` records the same replay differing on only **6 of 60**,
with a maximum delta of 0.0030. Nothing in the code changed between the two. What changed is the cache:
the live legacy run wrote entries the earlier replay did not have, and those entries carry different
verdicts from the ones the published table was computed with.

That is the point the paper makes about the cache being a primary experimental artefact rather than an
optimisation. **The contents of the cache, not the run contract, determine which numbers reproduce.**
Temperature is pinned at 0.0, which is necessary and not sufficient; ship a different cache and the
same command gives a different table.


## Regenerated on 2026-08-05, after the third review pass

Every stratified artefact in this directory was regenerated, because the files shipped before that pass
were produced by superseded code. They carried a nine-key run contract, so they predated the fixed
evaluation clock and the shared range file, and they predated the removal of the Encounter
period-ordering check from the timeliness rule. Two things changed as a result.

**The timeliness gap was an artefact.** The old files reported an Confined-minus-Baseline timeliness gap of
-15.90 pp on Synthea and -22.58 pp on eICU-CRD. That was one false positive per consistency-injected
Encounter, produced by the timeliness rule claiming a defect the injector labels consistency. Against
current code the gap is 0.0000 pp on all three cohorts at all three widths, with zero variance across
the four seeds.

**The reply parser was returning the model's retracted answer.** The verdict extraction took the span
from the first opening brace to the last closing brace, which breaks when the model answers, reconsiders
in prose, and answers again. Such a reply either failed to parse, collapsing the verdict to `uncertain`,
or -- when it hit the 256-token output cap before the correction arrived -- parsed the first object
alone and recorded the answer the model had withdrawn. The extraction now takes the last object carrying
a `verdict` key, the cap is 512 with one retry at 1024, and `call_stats` in every run file counts how
often the model answered twice. It did so 40 times in 7,200 calls across the four seeds.

Consequently `.llm_cache/` was rebuilt. The 2,086-entry cache written under the old parser and cap is
not shipped: a stored entry gives no sign of which extraction produced it, so it cannot be told apart
from a correct one by inspection. The replacement has 2,006 entries, covers all four seeds where the old
one covered only seed 42, and carries `CACHE_CONTRACT.json` recording the model, both output caps, the
temperature, the extraction rule and a digest of each system prompt. `dqa.agents.assert_cache_contract`
refuses to read a cache whose contract does not match the code.

All four regenerated runs report `model_errors: 0`. `controlled_comparison.json` is unaffected by any of
this, because it calls no model, and still reproduces at a maximum gap of 0.0000 pp over twelve cells.
