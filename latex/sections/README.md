# How the paper is laid out

`paper.tex` at the bundle root **remains a master file of `\input` lines only**: preamble, packages,
title macros, and thirty-one ordered `\input` lines. It holds no prose. Every word of the paper lives
in one of the files below.

Build from the bundle root:

```
latexmk -pdf paper.tex
```

The title page carries a `Build <timestamp>` line that re-evaluates on every run, so you can always
tell two PDFs apart. It is also written to the PDF Subject field, readable with `pdfinfo paper.pdf`.

## The folders

Verified against the filesystem and against `paper.toc` on 2026-08-11. Two rounds of merging mean the
folder no longer holds one file per subsection: §2 and §3 each have fewer subsections than files.

| Folder | Files | Holds |
|---|---|---|
| `00_front_matter/` | 1 | Title page, abstract and table of contents |
| `01_introduction/` | 1 | Section 1 |
| `02_background/` | 5 | Section 2 and its **two** subsections (3 of the 5 files are stubs) |
| `03_method/` | 8 | Section 3 and its **four** subsections (1 of the 8 files is a stub) |
| `04_findings/` | 7 | Section 4 and its six subsections |
| `05_limitations/` | 2 | Section 5 and 5.1 |
| `06_conclusion/` | 1 | Section 6 |
| `07_references/` | 1 | The reference list, 20 entries |
| `08_appendices/` | 5 | Appendices **A, B, C, D and E** |

## The files, and which subsection each one is now

| File | Now part of | Label(s) it carries |
|---|---|---|
| `00_front_matter/front_matter.tex` | title page, abstract, ToC | |
| `01_introduction/introduction.tex` | §1 | `sec:intro` |
| `02_background/00_opening.tex` | §2 heading | `sec:background` |
| `02_background/01_quality_dimensions.tex` | §2.1 | |
| `02_background/02_least_privilege.tex` | **stub** | |
| `02_background/03_related_work.tex` | §2.2 | `sec:threat` |
| `02_background/04_threat_model.tex` | **stub** | |
| `03_method/00_opening.tex` | §3 heading and opening | `sec:method` |
| `03_method/01_three_designs.tex` | §3.1 | `sec:designs` |
| `03_method/02_fair_comparison.tex` | §3.2 | `sec:fair`, `sec:ablationdesign`, `sec:injection` |
| `03_method/06_ablation.tex` | §3.2, runs on under it | |
| `03_method/03_defect_injection.tex` | **stub** | |
| `03_method/04_cohorts.tex` | §3.3 | `sec:data` |
| `03_method/05_metrics_reproducibility.tex` | §3.4 | `sec:repro` |
| `03_method/07_reproducibility.tex` | §3.4, runs on under it | `sec:reprodetail` |
| `04_findings/00_opening.tex` | §4 heading and opening | `sec:findings` |
| `04_findings/01_controlled_comparison.tex` | §4.1 | `sec:controlled`, `sec:identity` |
| `04_findings/02_projection_cost.tex` | §4.2 | `sec:ablation` |
| `04_findings/03_harder_task.tex` | §4.3 | `sec:hard`, `sec:ablationhard` |
| `04_findings/04_benchmark_flaw.tex` | §4.4 | `sec:benchmark` |
| `04_findings/05_inter_agent_exposure.tex` | §4.5 | `sec:exposure` |
| `04_findings/06_tiered_design.tex` | §4.6 | `sec:tiered` |
| `05_limitations/00_limitations.tex` | §5 | `sec:limits` |
| `05_limitations/01_risk_ethics_privacy.tex` | §5.1 | `sec:ethics` |
| `06_conclusion/conclusion.tex` | §6 | `sec:conclusion`, `page:refs` |
| `07_references/references.tex` | References | |
| `08_appendices/a_exposure_by_resource_type.tex` | Appendix A | |
| `08_appendices/b_hard_injector_cells.tex` | Appendix B | |
| `08_appendices/c_ablation_hard_injector.tex` | Appendix C | |
| `08_appendices/d_rule_file_and_pinning.tex` | Appendix D | |
| `08_appendices/e_benchmark_and_cohort_detail.tex` | Appendix E | |

The `\input` order in `paper.tex` is **not** the alphabetical order of the filenames. `06_ablation`
is input between `02_fair_comparison` and `03_defect_injection`, and `07_reproducibility` last in the
Method, because Pass 3 reordered the merged material and filenames were deliberately not renamed.

## The four stub files

Four files are stubs left behind when Pass 2 merged §2 from four subsections to two and §3 from six
to four. Each holds a comment naming where its content and its `\label` went, and nothing else.

| Stub | Contents | Where the content and label went |
|---|---|---|
| `02_background/00_opening.tex` | the `\section{Background and related work}` heading and `\label{sec:background}`, no prose | §2 lost its opening roadmap paragraph in Pass 2; the file is kept because it carries the heading and the label |
| `02_background/02_least_privilege.tex` | `% merged into 01_quality_dimensions.tex` | §2.1, which is now "The four quality dimensions, and least privilege" |
| `02_background/04_threat_model.tex` | `% merged into 03_related_work.tex, with \label{sec:threat}` | §2.2, which now carries `\label{sec:threat}` on its heading |
| `03_method/03_defect_injection.tex` | `% Merged into 02_fair_comparison.tex; sec:injection now labels Section 3.2.` | §3.2, which carries `\label{sec:injection}` alongside `sec:fair` and `sec:ablationdesign` |

**Keep the stubs and keep their `\input` lines.** The comment inside each one is the only record of
where its label went, and `paper.tex` reading in file order is what makes a missing file obvious. Two
other files, `03_method/06_ablation.tex` and `03_method/07_reproducibility.tex`, are **not** stubs:
they are live prose that lost its own `\subsection` heading in the same merge and now runs on under
§3.2 and §3.4.

## Three rules when editing

1. **Order lives in `paper.tex`.** Moving a subsection means moving its `\input` line, not renaming
   files. Adding one means creating the file and adding an `\input` line.
2. **Labels are global.** `\label{sec:fair}` in `03_method/02_fair_comparison.tex` is still reachable
   as `\ref{sec:fair}` from any other file. Nothing about the split changes cross-referencing.
3. **A merge moves the label, it does not delete it.** Every `\ref` site must be grepped before a
   heading is removed, and the surviving heading takes the old label as a second `\label`. That is
   why §3.2 carries three. The build reports 0 undefined references, and it has to stay that way.

## Why it was split

The paper had been edited as one 1,100-line file. Splitting it means a change to the Findings cannot
disturb the Method, several people (or several agents) can work on different subsections at once, and
the word count of any one subsection is visible at a glance:

```
wc -w sections/*/*.tex | sort -n
```

The whole of `sections/` is currently 1,118 lines and 9,052 words of LaTeX source, which builds to a
30-page PDF with a 17-page body.
