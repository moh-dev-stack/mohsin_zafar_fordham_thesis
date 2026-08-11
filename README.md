# Least-Privilege Multi-Agent EHR Data Quality Assurance

**Mohsin Zafar · HINF 6497 · Fordham University**

Submission bundle for *Least-Privilege Multi-Agent Data Quality Assurance for
Electronic Health Records*.

Submitted as the Final Research Paper and Supporting Materials for HINF 6497.
The slide deck and the oral presentation are delivered separately.

## What is here

| Path | What it is |
| --- | --- |
| `paper.pdf` | The thesis. Read this first. |
| `AI_DISCLOSURE.md` | Tools used, where, the prompts, and what was manually verified. |
| `REPRODUCE.md` | How to rerun the experiment, and what does and does not reproduce. |
| `latex/` | LaTeX source for the PDF. `paper.tex` plus every section under `sections/`. |
| `codebase/` | The experiment: source, tests, manifests, results, and the model verdict cache. |
| `codebase_audit/` | Defect register and fix passes run against the codebase. |

## Where each required element lives

| Required element | Where |
| --- | --- |
| Research question and aim | `paper.pdf` §1 |
| Background and literature grounding | `paper.pdf` §2 |
| Methods, data source, analytic approach | `paper.pdf` §3 |
| Reproducibility note | `paper.pdf` §3, then `REPRODUCE.md` |
| Findings, with tables | `paper.pdf` §4 and Appendices A to E |
| Limitations, risk, ethics, privacy, bias | `paper.pdf` §5 |
| Conclusion and implications | `paper.pdf` §6 |
| References, APA 7 | `paper.pdf`, 20 entries |
| Code and reproducibility package | `codebase/`, `codebase_audit/`, `latex/` |
| AI disclosure | `AI_DISCLOSURE.md` |

## Rebuilding the paper

```
cd latex && latexmk -pdf paper.tex
```

## Running the experiment

See `REPRODUCE.md`, then `codebase/README.md`.

## What is not in this bundle

- **The cohort data.** The three corpora under `codebase/data/` are roughly
  500 MB of public data and are not shipped. `codebase/data/PROVENANCE.md` and
  `codebase/data/README.md` say how to obtain them, and
  `codebase/data/COHORT_MANIFEST.json` pins them by SHA-256 so a reader can
  prove they hold the same data.
- **The slide deck.** Presentation material, not part of this submission.
- **Virtualenvs and build artefacts.** Regenerate from
  `codebase/requirements-lock.txt`.

`codebase/.llm_cache/` **is** included on purpose. The model call is not a pure
function, so that verdict cache is what makes the model-backed numbers
reproduce, and the paper treats it as a primary experimental artefact.
