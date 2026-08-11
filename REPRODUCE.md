# How to reproduce this paper

> **Note on supporting records.** This document cites internal verification records by name
> (`SECTION_REFERENCE.md`, `VERIFICATION_STATUS.md`, `check_numbers.py`). Those are working records and
> are not shipped in this submission bundle. Every `make` target and every command below runs without
> them; the citations are kept so each figure still names where it came from.

Every offline result regenerates from this bundle with **no API key and no network**. That covers the
headline table, the privilege ablation, the hard-injector runs, the tiered run and the linkage exposure
figures. It takes four `make` targets, not one, and §3 says which. The model-backed tables are a
different case and are dealt with in §5, honestly.

**Time:** about ten minutes, most of it the test suite, which `SECTION_REFERENCE.md` §3.7 records at
244 s. The rest of that estimate is not separately recorded and should be read as approximate.
**You need:** Python 3.12, `make`, and roughly 600 MB of disk.

Figures in this document were checked against the shipped records and the shipped files on 2026-08-11,
and each one names its source. Nothing was re-executed except `paper_revision/check_numbers.py`, which
is offline and takes seconds.

---

## 0. First, check you have the cohorts

**This matters, and it is easy to miss.** The three cohort directories under `codebase/data/` are about
500 MB and are **deliberately excluded from the git repository**. What you have depends on how you got
this bundle:

| How you obtained it | `codebase/data/` contains |
|---|---|
| The submitted deliverable, or a copy of the full directory | the three cohort directories, **325 files** |
| `git clone` from GitHub | only `README.md`, `PROVENANCE.md` and `COHORT_MANIFEST.json` |

Both figures re-counted directly on 2026-08-11: 325 files under the three cohort directories, 500 MB by
`du`. Check with:

```sh
ls codebase/data/
```

If you see only the three text files, **you have the digests but not the data**, and everything from §2
onward will fail. The verdict cache (`codebase/.llm_cache/`, **2,006 files**, being 2,005 verdicts plus
`CACHE_CONTRACT.json`, counted 2026-08-11) *is* in the repository, so that part is fine either way.

To obtain the cohorts, either take the full bundle directory, or rebuild them from the upstream sources
listed in §5 and use `COHORT_MANIFEST.json` to check whether what you built matches. Be warned that §5
explains why an exact rebuild is not possible.

---

## 1. Install

```sh
cd codebase
make install-locked
```

`install-locked` builds the virtualenv from `requirements-lock.txt`, which holds the exact versions
every published number was computed under. Verified against the shipped lockfile on 2026-08-11:
`anthropic 0.120.2`, `numpy 2.5.1`, `pydantic 2.13.4`, `PyYAML 6.0.3`, `jsonpath-ng 1.8.0`, in a file of
34 pinned entries. `.python-version` records the interpreter, **CPython 3.12.2**, also verified
directly.

Use `make install` instead only if you want to test against current releases rather than reproduce.
The offline paths are pure Python and are insensitive to these versions; `numpy` matters only for the
bootstrap intervals in `dqa.replicate`.

## 2. Check you have the same data

```sh
make verify-data
```

Expected:

```
cohort          corpus    selection   counts
----------------------------------------------------------
synthea         ok        ok          ok
mimic-iv-demo   ok        ok          ok
eicu-demo       ok        ok          ok
----------------------------------------------------------
PASS: cohorts are identical to the pinned manifest
```

This compares against `data/COHORT_MANIFEST.json`, which pins two digests per cohort. `corpus_sha256`
covers every shipped file. `selection_sha256` covers the **ordered `(resourceType, id)` sequence** that
`read_cohort` selects at limit 200, which is what every published number is actually computed from.
The second is the one that matters: order is part of the identity, because the injector draws against
position, so a reordered cohort with the same members is a different experiment.

If this fails, stop. Nothing below will match.

## 3. Reproduce every offline result

`make reproduce` is the first of four targets, not the only one. Read the whole section before running
anything, because two of the four **overwrite** shipped artefacts.

```sh
make reproduce
```

Expected last line:

```
REPRODUCED: all offline artefacts byte-identical
```

**That message overstates what the target checks, and you should know exactly what you are getting.**
Read from `codebase/Makefile:28-40` on 2026-08-11:

- It verifies the data, then regenerates `controlled_comparison.json`,
  `controlled_comparison_hard.json` and `privilege_ablation.json` and compares each against its shipped
  copy. That is **Table 1**, **Table 2** and the hard-injector **controlled comparison**. All three
  match.
- The comparison is `json.load(shipped) != json.load(regenerated)`, so it is parsed-structure equality
  rather than a byte diff. Key order, whitespace and numeric formatting would not be caught.
- It also runs `scripts/linkage_exposure.py`, which **overwrites** `results/linkage_exposure.json` in
  place and is **never diffed** against anything.
- It does **not** touch `privilege_ablation_hard.json` or `tiered_departments.json`.

So two published artefacts need their own targets:

```sh
make ablation-hard    # regenerates results/privilege_ablation_hard.json  (Section 4.3, Appendix C)
make tiered           # regenerates results/tiered_departments.json       (Section 4.6, the headline)
```

Both write over the shipped copy rather than to a temporary path, and neither diffs. **Take a copy of
`codebase/results/` before running them**, or run them in a scratch clone, then diff by hand:

```sh
cp -R codebase/results codebase/results_shipped
cd codebase && make ablation-hard tiered && cd ..
diff <(python3 -m json.tool codebase/results_shipped/tiered_departments.json) \
     <(python3 -m json.tool codebase/results/tiered_departments.json)
```

`tiered_departments.json` is the source of record for the exposure fall from **0.6079** to **0.0086**,
the 98.6 per cent reduction, and the **0.1644** completeness collapse under no linkage, per
`FIGURES.csv`. It is the most load-bearing offline artefact in the paper and it is the one `reproduce`
leaves out.

Individually, if you prefer:

| Command | Produces | Paper |
|---|---|---|
| `make controlled` | `results/controlled_comparison.json` | Table 1 |
| `make ablation` | `results/privilege_ablation.json` | Table 2 |
| `python -m dqa.controlled --injection-mode hard --out results/controlled_comparison_hard.json` | hard-injector controlled comparison | §4.3, Appendix B |
| `make ablation-hard` | `results/privilege_ablation_hard.json` | §4.3, Appendix C |
| `make tiered` | `results/tiered_departments.json` | §4.6, the headline |
| `python scripts/linkage_exposure.py` | `results/linkage_exposure.json` | surrogate exposure |
| `make replicate` | `results/replication_summary.json` | reads JSON only, no model |
| `make audit` | `audit/run.jsonl` | hash-chained log, verifies its own chain |

## 4. Check the rest

```sh
make test     # 355 passed, 1 skipped, 0 failed
make lint     # ruff over src tests scripts, clean
```

**355 passed, 1 skipped, 0 failed**, taken from `SECTION_REFERENCE.md` §3.7 (which also records 244 s),
`METHOD_STATUS.csv` row "test suite" and `FIGURES.csv` row `tests`. The paper states the same figure in
§3.4. An earlier version of this file said 348, which was stale from 2026-08-06.

The suite is 16 test files. The number worth having beside the pass count is the **false-positive
guard: 62,188 real values read, 4 excused, 0 unexplained** (`SECTION_REFERENCE.md` §3.7, `FIGURES.csv`
row `fp_guard`). The 4 excused are the `known_corrupt_values` allowlist, 3 entries covering 4 rows, all
eICU, per `METHOD_STATUS.csv`. A pass count on its own does not establish that the suite asserts
anything; the guard is what does.

And, from the bundle root, the number gate that ties the prose to the artefacts:

```sh
python3 paper_revision/check_numbers.py
```

**Run it on a flattened body, not on `paper.tex`.** `check_numbers.py:140-141` reads the file it is
given and does not expand `\input`. The body now lives in `sections/`, so pointed at `paper.tex` the
script reports a body of 6,676 characters and traces a document that is almost entirely preamble. It
still prints PASS, and for Gate 2 that PASS is close to meaningless. Flatten first:

```sh
python3 - <<'EOF'
import re, pathlib
root = pathlib.Path('.')
out = []
for line in (root / 'paper.tex').read_text().splitlines():
    m = re.match(r'\s*\\input\{([^}]+)\}', line)
    out.append((root / (m.group(1) + '.tex')).read_text() if m else line)
pathlib.Path('/tmp/paper_flat.tex').write_text('\n'.join(out))
EOF
python3 paper_revision/check_numbers.py /tmp/paper_flat.tex
```

The script bans values the audit withdrew, permits the withdrawn penalty only inside withdrawing
language, checks every `results/*.json` the paper cites exists, and traces every numeric literal in the
body. Run flat on 2026-08-11 it passes all four gates, with 43 Gate 2 literals reported as unmatched.
Every one was inspected: DOI fragments, journal page ranges, APA section numbers, and counts that live
in a test rather than in `results/*.json`. Gate 2's own docstring calls itself deliberately noisy and a
prompt to check rather than a test. No unsourced result number was found.

## 4b. Rebuild the paper and open the deck

```sh
latexmk -pdf paper.tex
```

Verified in `paper.log` on 2026-08-11: **30 pages, 0 undefined references, 0 undefined citations**. The
body runs p4 to p20, 17 pages, with references from p21 and five appendices after them, read from
`paper.toc`. There are **0 em dashes** anywhere under `sections/`, checked directly.

The presentation is **`slides/index.html`**: 44 slides, 24 core running 0:00 to 9:19, an appendix
divider at slide 25, then a 19-slide appendix in five groups. `slides/cheatsheet.html` is the speaker
crib. There is no `presentation.html`; it was removed from the bundle.

---

## 5. What you cannot reproduce, and why

Three honest limits. The first two do not affect the results above. The third is a limit on the paper
itself and is reported as such.

### Cohort construction: the two PhysioNet cohorts CAN be rebuilt

`scripts/csv_to_fhir.py` is the adapter that produced them. Download the demos from PhysioNet, both open
under ODbL v1.0 with no data use agreement, then:

```sh
python scripts/csv_to_fhir.py --source mimic-iv-demo --src <csv dir> --max-patients 100 --max-obs-per-patient 20
python scripts/csv_to_fhir.py --source eicu-demo     --src <csv dir> --max-patients 100 --max-obs-per-patient 20
```

Those are the defaults, and they are the subsample rule: the adapter walks each CSV in file order and
takes the first 100 patients or stays it encounters, with at most 20 observations each. That is what
"100-stay subsample" means for eICU, whose demo holds over 2,500 stays. The selection is deterministic
given the same source CSVs, so `make verify-data` will tell you whether your rebuild matches.

- MIMIC-IV Clinical Database Demo v2.2, https://doi.org/10.13026/dp1f-ex47
- eICU-CRD Demo v2.0.1, https://doi.org/10.13026/4mxk-na84

### Synthea cannot be rebuilt exactly

**No Synthea version or seed was recorded.** Synthea's generator is seeded, so without the seed a fresh
`-p 100` run produces different patients. The cohort is verifiable by digest but not regenerable.

- Synthea, Walonoski et al. (2018), https://github.com/synthetichealth/synthea

This is the one genuine hole in the data chain, and the manifest exists precisely because of it: it pins
the result where the process cannot be repeated.

### Model-backed results

`results_stratified_model.json` and the three seed replications call Claude Haiku 4.5 at snapshot
`claude-haiku-4-5-20251001`. They need `ANTHROPIC_API_KEY`, and **they do not fully reproduce even with
it**.

**478 of a run's 1,800** payload lookups have no cache entry, because `agents.py` stores a verdict only
when it is not `uncertain`. An uncached lookup collapses to `uncertain` on replay. Replaying the legacy
path offline against the superseded published figures differs on **18 of 60** scores, maximum delta
**0.2105**. Replaying this paper's own main run offline differs on **4 of 60** at maximum delta
**0.0179**, from **426** uncached lookups, and running it live against the shipped cache differs on
**6 of 60** at the same maximum delta. Figures from `SECTION_REFERENCE.md` §3.7 and `README.md` §4, and
stated in the paper at §3.4.

**What reproduces is the harness, not the model-backed measurement inside it.** That is the paper's own
framing and it is not softened anywhere in this bundle.

The shipped `.llm_cache/` (2,005 verdicts plus `CACHE_CONTRACT.json`) is therefore a **primary
experimental artefact, not an optimisation**. The harness verifies the contract before replaying
anything; mutate a field in it and replay is refused.

To attempt it anyway:

```sh
export ANTHROPIC_API_KEY=...
make eval-stratified
```

None of this touches the offline results. `make controlled` calls no model, opens no socket and reads
no clock.

### Historical figures

Counts from earlier review passes (146 tests before the third pass, 27 defects found, 23 fixed) are not
reconstructible: no prior version of the codebase ships, and the repository history begins with this
work rather than with the editions those counts describe. They are reported as history, not as
artefacts.

`VERIFICATION_STATUS.md` §3 lists these and two further gaps in the checking apparatus itself.

---

## 6. If something does not match

| Symptom | Likely cause |
|---|---|
| `verify-data` says a cohort directory is missing | You cloned from git. See §0: the 500 MB cohorts are not in the repository. |
| `verify-data` reports DRIFT | The cohorts differ. Nothing downstream is comparable; re-obtain `data/`. |
| `reproduce` reports DRIFT | Source changed since the artefacts were written. Diff the JSON directly; the run contract at the top of each file records the configuration. |
| `reproduce` passes but a §4.3 or §4.6 number still looks wrong | Expected. `reproduce` does not regenerate `privilege_ablation_hard.json` or `tiered_departments.json`. See §3. |
| `check_numbers.py` reports a tiny body and passes instantly | You pointed it at `paper.tex`. Flatten the `\input` chain first. See §4. |
| Tests fail on `ruff` only | Lint version drift. Harmless for results. |
| Model-backed numbers differ | Expected. See §5. |
| `make audit` reports chain failure | The audit log was modified after writing. That is the mechanism working. |
