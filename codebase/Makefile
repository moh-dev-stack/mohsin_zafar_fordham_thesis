# HINF 6497 course PoC.
#
# The paper's headline number comes from `make controlled`, which needs no key
# and no network. `make eval-stratified` regenerates the main results file and
# does need a key. `make eval` is the SUPERSEDED path, kept only so the first
# rung of the paper's correction ladder stays reproducible; see results/README.md.
.PHONY: install install-locked verify-data reproduce test lint ablation-hard tiered eval eval-offline eval-stratified controlled ablation audit replicate replicate-runs gap figure all

install:
	python3 -m venv .venv && .venv/bin/pip install -q -e ".[dev]"

# Reproduction install. Uses the frozen versions every published number was
# computed under, rather than resolving the floors in pyproject.toml to
# whatever is current. Use this one if you are checking the paper's numbers.
install-locked:
	python3 -m venv .venv && .venv/bin/pip install -q -r requirements-lock.txt && .venv/bin/pip install -q -e . --no-deps

# Prove the cohorts are the ones every published number was computed from.
# Checks a digest over every shipped file and, more importantly, a digest over
# the ordered (resourceType, id) sequence read_cohort selects. Exits non-zero
# on any drift.
verify-data:
	.venv/bin/python scripts/cohort_manifest.py

# End-to-end reproduction of every offline result in the paper. No API key, no
# network. Verifies the data first, then regenerates the three artefacts that
# need no model and diffs them against the shipped copies.
reproduce: verify-data
	.venv/bin/python scripts/cohort_manifest.py
	.venv/bin/python -m dqa.controlled --out /tmp/repro_controlled.json
	.venv/bin/python -m dqa.controlled --injection-mode hard --out /tmp/repro_hard.json
	.venv/bin/python -m dqa.ablation --out /tmp/repro_ablation.json
	.venv/bin/python scripts/linkage_exposure.py
	@.venv/bin/python -c 'import json,sys; \
	pairs=[("controlled_comparison.json","/tmp/repro_controlled.json"), \
	       ("controlled_comparison_hard.json","/tmp/repro_hard.json"), \
	       ("privilege_ablation.json","/tmp/repro_ablation.json")]; \
	bad=[a for a,b in pairs if json.load(open("results/"+a))!=json.load(open(b))]; \
	print("REPRODUCED: all offline artefacts byte-identical" if not bad else "DRIFT: "+", ".join(bad)); \
	sys.exit(1 if bad else 0)'

test:
	.venv/bin/pytest -q

lint:
	.venv/bin/ruff check src tests scripts

# SUPERSEDED. Legacy allocator at rate 0.10, which realises 2 plausibility and 0
# consistency defects per cell. This is the configuration behind
# results/results_published_v1.json; it writes elsewhere so the archived file
# cannot be overwritten by accident.
eval:
	.venv/bin/python -m dqa.run --out results/results_legacy_rerun.json

# Offline: LLM dimensions return "uncertain" deterministically.
eval-offline:
	ANTHROPIC_API_KEY= .venv/bin/python -m dqa.run --out results/results_offline.json

# Current main run. Stratified allocator at rate 0.30, so every planned defect is
# realised and all four dimensions carry an n. Needs ANTHROPIC_API_KEY.
eval-stratified:
	.venv/bin/python -m dqa.run --allocation stratified --rate 0.30 \
	  --out results/results_stratified_model.json

# The paper's headline: one range-based plausibility check on both arms, so they
# differ only in field visibility. 4 seeds x 3 cohorts x 3 widths. No model, no
# network, no clock, so it reproduces byte for byte offline.
controlled:
	.venv/bin/python -m dqa.controlled --out results/controlled_comparison.json

# The privilege ablation, which is what `controlled` cannot tell you. The
# controlled comparison holds the checker fixed and varies field visibility, but
# all three widths release every field the checker consumes (code, value, unit),
# so its 0.0000 pp is an identity rather than a measurement. This target
# withholds one consumed field at a time and measures what projection actually
# costs when it bites: 3.85 pp, 28.04 pp and 100.00 pp. It also carries a
# negative control that withholds a released-but-unconsumed field and must score
# 0.0000, which is what shows the headline zero is a released-superset-consumed
# artefact. No model, no network, no clock.
ablation:
	.venv/bin/python -m dqa.ablation --out results/privilege_ablation.json

# The same ablation under the hard injector. Worth running separately because
# the two disagree in a way that matters: under the legacy injector, withholding
# the analyte code APPEARS to help, because every observation falls back to the
# [0, 9000] envelope which happens to catch all four sentinel constants. Under
# the hard injector that fallback is useless and the same ablation costs 100 pp.
# The apparent gain was an artefact of the saturated endpoint.
# The department-tiered system, run end to end across the L0/L1/L2 linkage
# ladder. This is the result in Section 4.8: exposure falls 98.6 per cent with
# no detection loss on any dimension. Offline, rule-only checkers, no model.
tiered:
	.venv/bin/python -m dqa.tiered --out results/tiered_departments.json

ablation-hard:
	.venv/bin/python -m dqa.ablation --injection-mode hard --out results/privilege_ablation_hard.json

# The hash-chained audit trail, actually written to disk. `src/dqa/audit.py` has
# always been able to produce these records and nothing ever called it, so
# auditability was a claimed property with no artefact behind it. This target
# runs the offline evaluation with `--audit-log`, which emits one record per
# scored system plus run_start and run_complete, and verifies the chain replays
# before exiting non-zero if it does not. Offline, so it costs nothing.
#
# Written to audit/ rather than results/ because it is a process record, not an
# experimental result, and it carries wall-clock timestamps so it is not
# byte-reproducible by design.
audit:
	@mkdir -p audit
	ANTHROPIC_API_KEY= .venv/bin/python -m dqa.run \
	  --out audit/run_for_audit.json --audit-log audit/run.jsonl

# The paper's confidence intervals. Reduces the four stratified runs (seeds 42
# to 45) to the Confined-full minus Baseline gap per dimension and cohort, with a t
# interval, a bootstrap interval, Holm-Bonferroni across every comparison and
# the pre-declared non-inferiority test. Reads JSON only: no model, no network.
# This was the one headline artefact no shipped code produced.
replicate:
	.venv/bin/python -m dqa.replicate --out results/replication_summary.json

# Regenerate the four runs the summary reduces, then the summary. Needs
# ANTHROPIC_API_KEY. Seed 42 is the main run and is written to its own file.
replicate-runs:
	.venv/bin/python -m dqa.run --allocation stratified --rate 0.30 --seed 42 \
	  --out results/results_stratified_model.json
	.venv/bin/python -m dqa.run --allocation stratified --rate 0.30 --seed 43 \
	  --out results/rep_seed43.json
	.venv/bin/python -m dqa.run --allocation stratified --rate 0.30 --seed 44 \
	  --out results/rep_seed44.json
	.venv/bin/python -m dqa.run --allocation stratified --rate 0.30 --seed 45 \
	  --out results/rep_seed45.json
	$(MAKE) replicate

# The checker confound, measured. Two runs over the same cohorts and the same
# seeded injection, differing only in --baseline: `envelope` gives Baseline the wide
# [-1000, 100000] envelope, `ranges` gives Baseline and every Confined width the shared
# manifests/rules/loinc_ranges.yaml. The delta is the checker effect that used
# to be reported as a privilege effect. Run offline so the Confined column isolates
# the rule change rather than mixing in model variance.
gap:
	ANTHROPIC_API_KEY= .venv/bin/python -m dqa.run --allocation stratified --rate 0.30 \
	  --baseline envelope --out results/gap_envelope.json
	ANTHROPIC_API_KEY= .venv/bin/python -m dqa.run --allocation stratified --rate 0.30 \
	  --baseline ranges --out results/gap_ranges.json
	@.venv/bin/python -c 'import json; f=lambda c,s: c["systems"][s]["scores"]["f1_plausibility"]; e=json.load(open("results/gap_envelope.json")); r=json.load(open("results/gap_ranges.json")); print("plausibility F1                Baseline envelope   Baseline ranges   delta pp     confined-full envelope   confined-full ranges"); [print("{:<30} {:.4f}        {:.4f}      {:+6.2f}       {:.4f}             {:.4f}".format(a["dataset"], f(a,"Baseline"), f(b,"Baseline"), 100*(f(b,"Baseline")-f(a,"Baseline")), f(a,"confined-full"), f(b,"confined-full"))) for a, b in zip(e["cohorts"], r["cohorts"])]'

# Regenerate Figure 1 from results/results_stratified_model.json.
figure:
	.venv/bin/python scripts/make_figure.py

all: lint test controlled ablation
