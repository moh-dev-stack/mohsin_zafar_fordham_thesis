# codebase_audit/

Independent audit of `../codebase/` and its results, run 2026-08-06. Four documents, in the order they
were produced. **Scope was the codebase and its result artefacts only.** `paper.tex`, `paper.pdf`,
`presentation.html` and `slides/` were deliberately excluded and are still unaudited; see "What this
audit does not cover" below.

Not to be confused with `../codebase/audit/`, which is the hash-chained run log that `make audit`
writes. Different thing entirely.

## The documents

| File | What it is | Read it for |
|---|---|---|
| `AUDIT_PLAN.md` | The plan, built over two planning iterations. Scope, a seven-mode fabrication taxonomy, a P0-P3 priority register, the adversarial posture, and **nine priors committed before any checking began** so the audit could be wrong and could not retrofit its expectations. | What was going to be checked, and why that order. |
| `AUDIT_ITERATION_1.md` | **E1, bottom-up.** Every module read against its own docstring, every artefact traced to the command that produces it, provenance matrix, dead-code map, test-quality register. Zero API spend. | What the code actually does, as opposed to what it says. |
| `AUDIT_ITERATION_2.md` | **E2, adversarial.** Assumes the headline is wrong and tries to prove it. Five attacks, the reproducibility replay, the architecture wiring map, verdicts per claim. | Whether the claims survive being attacked. |
| `AUDIT_FINDINGS.md` | Final verdict, everything that was fixed, results-integrity proof, and **corrections to the audit's own wrong findings**. | Start here if you only read one. |

## The short version

Nothing was fabricated. Real corpora, real DOIs, a real citation, every artefact traceable to code. No
stop condition fired.

The headline was arithmetically correct and scientifically vacuous: **the 0.0000 pp result is a
mathematical identity, not a measurement**, because both arms consume byte-identical `(code, value,
unit)` triples. Verified over 600 clean resources, 2,400 injected, three widths and ten adversarial
synthetics, with zero mismatches.

That is now fixed by shipping the experiments that give the number meaning, not by changing it:
`make ablation` (3.85 / 28.04 / 100.00 pp, plus a negative control at 0.0000) and
`--injection-mode hard` (set-membership catches 0 of 83; the per-analyte check catches 83 of 83, and
the gap is still 0.0000 pp).

Three of the audit's own findings were wrong and are corrected in `AUDIT_FINDINGS.md` §2.

## Results integrity

Every numeric leaf in every artefact was diffed against `git HEAD`. **Exactly 16 numbers moved**, all
four eICU cells, all traceable to the de-fitted rule file and explained in `AUDIT_FINDINGS.md` §3.
Every other artefact is byte-identical: 10,199 numbers untouched. `max_abs_gap_pp` is 0.0000 in all
twelve cells before and after.

Post-audit state: **346 tests pass** (from 236), 1 skipped, `ruff` clean, all four offline artefacts
regenerate byte-identically, and the shipped verdict cache is untouched at 2006 files.

## What this audit does not cover

`paper.tex` is **unaudited and currently stale**. It cites `0.9825` four times, a value that no longer
exists in any artefact (it is now `0.9655`), and it contains no mention of the ablation, the hard
injector, or the identity finding. Fixing the codebase created that divergence. Resolving it is phase 2,
and the honest one-sentence reframing to build it around is drafted in `AUDIT_FINDINGS.md` §7.
