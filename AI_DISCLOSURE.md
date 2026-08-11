# AI Disclosure Statement

**Mohsin Zafar · HINF 6497, Fordham University · 11 August 2026**

Submitted per the HINF 6497 AI Disclosure Requirement, which asks for the tools used, where they were
used, a summary of the prompts, and what was manually verified.

---

## Two uses of a Claude model, and they are not the same thing

This distinction matters more than anything else in this document, so it comes first.

**One: the measured instrument.** The system *being studied* calls a Claude model.
`claude-haiku-4-5-20251001`, at temperature 0.0, 512 output tokens and 1024 on retry, acts as the
plausibility and consistency checker in the multi-agent arm. It is the thing under study. Its snapshot
is pinned in `CACHE_CONTRACT.json` and in every `results/*.json` that records a model-backed score,
and its verdicts ship as a primary
experimental artefact in `codebase/.llm_cache/`. **It is not an authoring tool, and nothing it produced
was written into the paper as prose.**

**Two: the authoring assistant.** **Claude Code**, Anthropic's repository-integrated writing and
code-review assistant, was used to help write code, run experiments, and draft and revise the
manuscript.

The two uses are separate and must not be counted as one. Slide 33 of the deck, "Did AI write this
thesis?", states the same distinction on screen.

---

## Where the assistant was used

| Area | What the assistant did |
|---|---|
| **Code** | Helped build the experimental pipeline: the stratified allocator, the controlled comparison, the privilege ablation, the linkage ladder, the tiered department pipeline and the four-seed replication. |
| **Experiments** | Executed the model runs against the pinned snapshot, with the verdict cache shipped as an artefact. |
| **Review** | Ran adversarial review passes against the paper's own claims, which overturned a draft headline and established that the controlled comparison's zero is a mathematical identity rather than a measured effect. |
| **Codebase audit** | Produced a defect register, then fix passes. Results in `codebase_audit/`. |
| **Writing** | Assisted with drafting, revision and formatting across several passes: rewriting prose for clarity and length, restructuring sections, and moving material into appendices. |
| **Citations** | APA 7 formatting mechanics only: hanging indents, entry ordering and DOI link form. **Every reference was verified against its source by the author**, not by the assistant. |

**Attribution, stated plainly.** The research question, the experimental design and the responsibility
for every claim are mine. The prose and the section structure as they now stand were substantially
drafted and reshaped by the assistant, under my direction and review. Neither of those sentences
should be read as smaller than it is.

**What that cost.** AI-assisted revision introduced or failed to catch several false claims, each
fluent and consistent with the paper's argument, and each caught later by checking the claim against
an artefact rather than by rereading the prose. That is the case for the verification apparatus
described below.

---

## Prompt summary

Prompts were project-scoped and iterative, not single-shot generation. They were largely of these
forms:

- "Re-run configuration X and report every cell that differs."
- "Argue against this claim as a hostile examiner would."
- "Check that every number in the paper resolves to a file in `results/`."
- "Re-derive every headline number from `codebase/results/` yourself. Do not trust the previous draft."
- "Shorter is allowed. Weaker is forbidden. Do not soften an admission for length."
- "Audit this against the syllabus and APA 7, and mark each item compliant or not with the evidence."

No prompt asked for text to be produced and used unexamined.

---

## Manual verification

- **Citations.** Every reference was verified **by the author**, by hand, against its DOI, arXiv
  identifier or publisher page. The paper carries 20 entries. Two were found to cite the wrong source
  for the claim attached to them and were corrected. One attribution overstated what the cited work
  contains and was rewritten.
- **Numbers.** Every numeric result in prose and in tables was cross-checked against the source files
  in `codebase/results/`.
- **Data.** Cohort identity is pinned by SHA-256 in `codebase/data/COHORT_MANIFEST.json` and verified
  by `make verify-data`.
- **Code.** All changes are gated through the repository's test and lint targets: **355 passed, 1
  skipped, 0 failed** across 16 test files, `ruff` clean. Beside that sits the false-positive guard,
  which reads 62,188 real values with 4 excused and 0 unexplained, because a pass count alone does not
  establish that a suite asserts anything. `make reproduce` regenerates the offline artefacts it
  covers and diffs them against the shipped copies.

**No fabricated citation, result or dataset appears in this paper.** Where a figure could not be
traced to an artefact it was removed or replaced by one that could.

---

## PHI compliance

No protected health information, no screenshot containing PHI, and no prompt containing PHI was
uploaded to any AI tool.

The study uses only publicly released de-identified demonstration cohorts (MIMIC-IV Demo, eICU-CRD
Demo, both open under ODbL v1.0) and synthetic data (Synthea). The projected slices dispatched to the
model instrument are drawn from those cohorts and contain no real patient identifiers.

---

## Responsibility

I remain responsible for the accuracy of the citations, code, analysis and claims submitted. The
verification steps above were run to discharge that responsibility, and where they found the work
wanting, the work was changed rather than the claim softened.
