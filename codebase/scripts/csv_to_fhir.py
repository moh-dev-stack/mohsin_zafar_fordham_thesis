"""Minimal CSV -> FHIR R4 Bundle adapter for the MIMIC-IV Demo and eICU-CRD Demo cohorts.

Emits one Bundle JSON per patient into the target directory, so the existing
harness (`src/dqa/eval/harness.py`, which globs ``*.json`` and picks Observation
+ Encounter resources) can consume it without change.

The adapter is deliberately narrow: it produces just enough FHIR to exercise
the four DQA dimensions (completeness, plausibility, consistency, timeliness),
which need ``status``, ``code``, ``subject``, ``effectiveDateTime``,
``valueQuantity`` on Observations and ``status``, ``period.start``,
``period.end``, ``subject.reference`` on Encounters. No attempt at full FHIR
conformance -- this is a research harness input, not an interoperability layer.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import IO, cast

REPO_ROOT = Path(__file__).resolve().parents[1]


def _open_csv(path: Path) -> IO[str]:
    return cast("IO[str]", gzip.open(path, "rt", newline="", encoding="utf-8", errors="replace"))


def _iso(ts: str) -> str | None:
    """Parse MIMIC datetimes to ISO-8601 UTC; return None on failure."""
    if not ts:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(ts, fmt).replace(tzinfo=UTC).isoformat()
        except ValueError:
            continue
    return None


def _observation(*, obs_id: str, subject: str, encounter: str | None, code: str,
                 display: str, effective: str | None, value: str | None,
                 unit: str | None) -> dict:
    obs: dict = {
        "resourceType": "Observation",
        "id": obs_id,
        "status": "final",
        "category": [{
            "coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category",
                        "code": "laboratory", "display": "Laboratory"}]
        }],
        "code": {"coding": [{"system": "http://loinc.org", "code": code, "display": display}]},
        "subject": {"reference": f"Patient/{subject}"},
        "effectiveDateTime": effective,
    }
    if encounter:
        obs["encounter"] = {"reference": f"Encounter/{encounter}"}
    if value:
        try:
            obs["valueQuantity"] = {"value": float(value), "unit": unit or ""}
        except ValueError:
            obs["valueString"] = value
    return obs


def _encounter(*, enc_id: str, subject: str, start: str | None, end: str | None) -> dict:
    return {
        "resourceType": "Encounter",
        "id": enc_id,
        "status": "finished",
        "class": {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
                  "code": "IMP", "display": "inpatient encounter"},
        "subject": {"reference": f"Patient/{subject}"},
        "period": {"start": start, "end": end},
    }


def _bundle(entries: list[dict]) -> dict:
    return {
        "resourceType": "Bundle",
        "type": "collection",
        "entry": [{"resource": r} for r in entries],
    }


def convert_mimic_iv_demo(src: Path, dst: Path, max_patients: int, max_obs_per_patient: int) -> int:
    """Convert MIMIC-IV Demo hosp/{admissions,labevents,d_labitems} into per-patient Bundles."""
    hosp = src / "hosp"

    with _open_csv(hosp / "d_labitems.csv.gz") as f:
        labitem: dict[str, tuple[str, str]] = {
            row["itemid"]: (row.get("label") or "unknown", row.get("category") or "")
            for row in csv.DictReader(f)
        }

    with _open_csv(hosp / "admissions.csv.gz") as f:
        admissions_by_subject: dict[str, list[tuple[str, str, str]]] = {}
        for row in csv.DictReader(f):
            admissions_by_subject.setdefault(row["subject_id"], []).append(
                (row["hadm_id"], _iso(row["admittime"]) or "", _iso(row["dischtime"]) or "")
            )

    dst.mkdir(parents=True, exist_ok=True)
    per_patient_obs: dict[str, list[dict]] = {}
    with _open_csv(hosp / "labevents.csv.gz") as f:
        for row in csv.DictReader(f):
            subj = row.get("subject_id")
            if not subj or subj not in admissions_by_subject:
                continue
            bucket = per_patient_obs.setdefault(subj, [])
            if len(bucket) >= max_obs_per_patient:
                continue
            code = row.get("itemid") or ""
            display = labitem.get(code, (code, ""))[0]
            bucket.append(_observation(
                obs_id=row["labevent_id"], subject=subj,
                encounter=row.get("hadm_id") or None,
                code=code, display=display,
                effective=_iso(row.get("charttime") or ""),
                value=row.get("valuenum") or row.get("value") or None,
                unit=row.get("valueuom") or None,
            ))
            if sum(1 for xs in per_patient_obs.values() if xs) >= max_patients and \
               all(len(v) >= max_obs_per_patient for v in per_patient_obs.values()):
                break

    written = 0
    for subj, obs_list in per_patient_obs.items():
        if not obs_list:
            continue
        entries: list[dict] = [
            {"resourceType": "Patient", "id": subj,
             "identifier": [{"system": "urn:mimic:subject", "value": subj}]}
        ]
        for hadm, start, end in admissions_by_subject.get(subj, []):
            entries.append(_encounter(enc_id=hadm, subject=subj, start=start, end=end))
        entries.extend(obs_list)
        (dst / f"mimic_{subj}.json").write_text(json.dumps(_bundle(entries)))
        written += 1
        if written >= max_patients:
            break
    return written


def convert_eicu_demo(src: Path, dst: Path, max_patients: int, max_obs_per_patient: int) -> int:
    """Convert eICU-CRD Demo patient + lab CSVs into per-patient Bundles."""
    with _open_csv(src / "patient.csv.gz") as f:
        patients: dict[str, dict] = {}
        for row in csv.DictReader(f):
            stay = row["patientunitstayid"]
            patients[stay] = {"gender": row.get("gender") or "unknown"}

    dst.mkdir(parents=True, exist_ok=True)

    # eICU uses relative minute offsets from admission; anchor at a fixed epoch
    # so injectors have real datetimes to bend.
    epoch = datetime(2020, 1, 1, tzinfo=UTC)

    per_stay_obs: dict[str, list[dict]] = {}
    with _open_csv(src / "lab.csv.gz") as f:
        for row in csv.DictReader(f):
            stay = row.get("patientunitstayid")
            if not stay or stay not in patients:
                continue
            bucket = per_stay_obs.setdefault(stay, [])
            if len(bucket) >= max_obs_per_patient:
                continue
            try:
                offset_min = int(row.get("labresultoffset") or "0")
            except ValueError:
                offset_min = 0
            effective = (epoch + timedelta(minutes=offset_min)).isoformat()
            bucket.append(_observation(
                obs_id=row["labid"], subject=stay, encounter=stay,
                code=row.get("labtypeid") or "0",
                display=row.get("labname") or "unknown",
                effective=effective,
                value=row.get("labresult") or None,
                unit=row.get("labmeasurenamesystem") or None,
            ))
            if len(per_stay_obs) >= max_patients and \
               all(len(v) >= max_obs_per_patient for v in per_stay_obs.values()):
                break

    written = 0
    for stay, obs_list in per_stay_obs.items():
        if not obs_list:
            continue
        entries: list[dict] = [
            {"resourceType": "Patient", "id": stay,
             "identifier": [{"system": "urn:eicu:stay", "value": stay}]},
            _encounter(enc_id=stay, subject=stay,
                       start=epoch.isoformat(),
                       end=(epoch + timedelta(days=3)).isoformat()),
        ]
        entries.extend(obs_list)
        (dst / f"eicu_{stay}.json").write_text(json.dumps(_bundle(entries)))
        written += 1
        if written >= max_patients:
            break
    return written


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, choices=("mimic-iv-demo", "eicu-demo"))
    parser.add_argument("--src", type=Path, default=None,
                        help="Source directory (default: data/raw/<source>).")
    parser.add_argument("--dst", type=Path, default=None,
                        help="Destination directory (default: data/<source>-fhir).")
    parser.add_argument("--max-patients", type=int, default=100)
    parser.add_argument("--max-obs-per-patient", type=int, default=20)
    args = parser.parse_args()

    src = args.src or (REPO_ROOT / "data" / "raw" / args.source)
    dst = args.dst or (REPO_ROOT / "data" / f"{args.source}-fhir")

    if not src.exists():
        print(f"error: source dir not found: {src}", file=sys.stderr)
        sys.exit(2)

    if args.source == "mimic-iv-demo":
        n = convert_mimic_iv_demo(src, dst, args.max_patients, args.max_obs_per_patient)
    else:
        n = convert_eicu_demo(src, dst, args.max_patients, args.max_obs_per_patient)
    print(f"wrote {n} bundles to {dst}")


if __name__ == "__main__":
    main()
