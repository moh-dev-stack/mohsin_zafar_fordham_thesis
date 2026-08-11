# Cohort data: provenance and licensing

Three FHIR-shaped cohorts, complete and unmodified. None requires a credentialed data use agreement,
which is what makes every result in this bundle reproducible by a reader without applying for access.

| Cohort | Files | Size | Source | Licence |
|---|---|---|---|---|
| `synthea-fhir` | 125 | 498 MB | Synthea synthetic patient generator, FHIR R4 output | Synthetic. No real patient data. Generator is Apache 2.0 |
| `mimic-iv-demo-fhir` | 100 | 1.1 MB | MIMIC-IV Clinical Database Demo v2.2, PhysioNet | Open Data Commons ODbL v1.0, open access |
| `eicu-demo-fhir` | 100 | 1.0 MB | eICU Collaborative Research Database Demo v2.0.1, PhysioNet | Open Data Commons ODbL v1.0, open access |

**The shipped bundles are unmodified once converted, but two of the three are subsets of their upstream release.** Synthea is a local 123-patient generation. The MIMIC-IV demo holds 100 subjects and all 100 are converted. The eICU demo holds over 2,500 unit stays, of which 100 were converted, one Encounter per Patient. Corrected 2026-08-06: an earlier version of this line claimed nothing had been sampled, which was wrong for eICU.

## Rebuilding the two PhysioNet cohorts

`scripts/csv_to_fhir.py` is the adapter that produced them, and it ships with this bundle:

```sh
python scripts/csv_to_fhir.py --source mimic-iv-demo --src <csv dir> --max-patients 100 --max-obs-per-patient 20
python scripts/csv_to_fhir.py --source eicu-demo     --src <csv dir> --max-patients 100 --max-obs-per-patient 20
```

Those defaults are the subsample rule: first 100 patients or stays in CSV file order, at most 20
observations each. Deterministic given the same sources. `make verify-data` checks a rebuild against
`COHORT_MANIFEST.json`.

Synthea is the exception: no generator version or seed was recorded, so that cohort is verifiable by
digest but not regenerable.

## Which resources the harness actually uses

Worth knowing when reading the code, because most of what is here is never touched.

`read_cohort` in `src/dqa/run.py` admits only `Patient`, `Observation` and `Encounter`. Every other
resource type in a Synthea bundle, principally `Claim` and `ExplanationOfBenefit` which together are
most of the bytes, is skipped at load time. The function also reserves `limit // 3` positions for
Patient resources and fills the remainder with events, walking files in sorted order, so at the pinned
limit of 200 it consumes the first 66 Synthea bundles and never reaches the rest.

The full corpus is shipped anyway so that a reader can change the limit, admit further resource types,
or run an entirely different analysis without having to source the data again.

## Verifying the cohorts load as the results assume

```sh
cd codebase && make install
.venv/bin/python -c "
from pathlib import Path
from dqa.run import read_cohort
for c in ('synthea-fhir','mimic-iv-demo-fhir','eicu-demo-fhir'):
    print(c, len(read_cohort(Path('data')/c, 200)))"
```

Each cohort must report 200. The exact resource selection was checked against a SHA-256 over the
ordered list of `(resourceType, id)` pairs and matches the corpus the published results were computed
on.

## Citation

Johnson, A., Bulgarelli, L., Pollard, T., Horng, S., Celi, L. A., & Mark, R. (2023). MIMIC-IV Clinical
Database Demo (version 2.2). PhysioNet. https://doi.org/10.13026/dp1f-ex47

Pollard, T., Johnson, A., Raffa, J., Celi, L. A., Badawi, O., & Mark, R. (2019). eICU Collaborative
Research Database Demo (version 2.0.1). PhysioNet. https://doi.org/10.13026/gxmm-es70

Walonoski, J., Kramer, M., Nichols, J., et al. (2018). Synthea: An approach, method, and software
mechanism for generating synthetic patients and the synthetic electronic health care record.
Journal of the American Medical Informatics Association, 25(3), 230-238.
