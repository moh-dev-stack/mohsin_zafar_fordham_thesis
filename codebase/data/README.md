# Data directory

The three cohorts ship with this bundle as real directories, not symlinks and not placeholders. There
is nothing to download and nothing to link before the code will run.

| Directory | Files | Size on disk |
|---|---|---|
| `synthea-fhir` | 125 | 498 MB |
| `mimic-iv-demo-fhir` | 100 | 1.2 MB |
| `eicu-demo-fhir` | 100 | 1.1 MB |

That is 325 files and roughly 500 MB in total, nearly all of it Synthea. All three are public releases
requiring no data use agreement, which is what makes every result in this bundle reproducible by a
reader who has not applied for credentialed access.

`PROVENANCE.md` in this directory records where each cohort came from, its licence, which resource
types the harness actually reads, and how to check that the cohorts load as the published results
assume.

The directories are listed in `.gitignore` because 500 MB is too large to commit, so a clone of the
repository alone will not have them. That is a statement about the repository, not about this bundle.
The bundle is self-contained.
