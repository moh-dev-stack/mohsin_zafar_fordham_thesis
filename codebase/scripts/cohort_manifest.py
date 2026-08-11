"""Pin the cohorts by digest, so a reader can prove they hold the same data.

    python scripts/cohort_manifest.py --write     # generate data/COHORT_MANIFEST.json
    python scripts/cohort_manifest.py             # verify against it, exit 1 on drift

WHY THIS EXISTS
---------------
The FHIR conversion that produced ``data/`` belonged to the first edition of
this codebase and does not ship here, so cohort *construction* cannot be
repeated from this bundle. That is a real limitation and the paper states it.

What can be made verifiable is cohort *identity*. This manifest records three
things per cohort:

* a SHA-256 over every shipped file, so a reader can detect a single altered
  byte anywhere in the corpus;
* a SHA-256 over the ordered ``(resourceType, id)`` pairs that ``read_cohort``
  actually selects at the pinned limit, which is the sequence every published
  number is computed from;
* the counts the paper quotes, so a claim in the prose can be checked against
  the data without running the evaluation.

The second digest is the load-bearing one. Two corpora could differ in files
the harness never reads and still produce identical results; two corpora that
agree on the selected sequence will produce identical results whatever else
differs. Verifying that sequence is therefore the strongest statement
available short of shipping the converter.

Offline, deterministic, no model.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from dqa.run import DATASETS, EXCLUDED_FILE_PREFIXES, LIMIT_DEFAULT, read_cohort

MANIFEST_PATH = Path(__file__).resolve().parents[1] / "data" / "COHORT_MANIFEST.json"


def file_digests(directory: Path) -> dict[str, str]:
    """SHA-256 per shipped file, keyed by name, sorted."""
    out: dict[str, str] = {}
    for path in sorted(directory.glob("*.json")):
        out[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def selection_digest(cohort: list[dict[str, Any]]) -> str:
    """SHA-256 over the ordered (resourceType, id) pairs the harness selects.

    This is the sequence every published number is computed from. Order is
    part of the identity: the injector draws against position, so a reordered
    cohort with the same members is a different experiment.
    """
    pairs = [[r.get("resourceType"), str(r.get("id", ""))] for r in cohort]
    blob = json.dumps(pairs, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def describe(name: str, directory: Path, limit: int) -> dict[str, Any]:
    cohort = read_cohort(directory, limit)
    by_type: dict[str, int] = {}
    for resource in cohort:
        rtype = str(resource.get("resourceType"))
        by_type[rtype] = by_type.get(rtype, 0) + 1
    files = file_digests(directory)
    return {
        "files_shipped": len(files),
        "files_excluded_by_prefix": sorted(
            p.name for p in directory.glob("*.json")
            if p.name.startswith(EXCLUDED_FILE_PREFIXES)
        ),
        "corpus_sha256": hashlib.sha256(
            json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "selected_at_limit": limit,
        "selected_count": len(cohort),
        "selected_by_type": dict(sorted(by_type.items())),
        "selection_sha256": selection_digest(cohort),
        "per_file_sha256": files,
    }


def build(limit: int) -> dict[str, Any]:
    return {
        "description": (
            "Cohort identity pins. corpus_sha256 covers every shipped file; "
            "selection_sha256 covers the ordered (resourceType, id) sequence "
            "read_cohort selects at the pinned limit, which is what every "
            "published number is computed from."
        ),
        "limit": limit,
        "excluded_file_prefixes": list(EXCLUDED_FILE_PREFIXES),
        "cohorts": {
            name: describe(name, directory, limit)
            for name, directory in DATASETS.items()
            if directory.is_dir()
        },
    }


def verify(limit: int) -> int:
    if not MANIFEST_PATH.is_file():
        print(f"no manifest at {MANIFEST_PATH}; run with --write first", file=sys.stderr)
        return 1
    expected = json.loads(MANIFEST_PATH.read_text())
    actual = build(limit)

    failures = 0
    print(f"{'cohort':<16}{'corpus':<10}{'selection':<12}{'counts'}")
    print("-" * 58)
    for name, want in expected["cohorts"].items():
        got = actual["cohorts"].get(name)
        if got is None:
            print(f"{name:<16}MISSING")
            failures += 1
            continue
        corpus_ok = got["corpus_sha256"] == want["corpus_sha256"]
        select_ok = got["selection_sha256"] == want["selection_sha256"]
        counts_ok = got["selected_by_type"] == want["selected_by_type"]
        failures += (not corpus_ok) + (not select_ok) + (not counts_ok)
        print(
            f"{name:<16}{'ok' if corpus_ok else 'DRIFT':<10}"
            f"{'ok' if select_ok else 'DRIFT':<12}"
            f"{'ok' if counts_ok else 'DRIFT'}"
        )
        if not corpus_ok:
            changed = [
                f for f, d in got["per_file_sha256"].items()
                if want["per_file_sha256"].get(f) != d
            ]
            missing = sorted(set(want["per_file_sha256"]) - set(got["per_file_sha256"]))
            for f in (changed + missing)[:5]:
                print(f"      differs or missing: {f}")

    print("-" * 58)
    print("PASS: cohorts are identical to the pinned manifest" if not failures
          else f"FAIL: {failures} mismatch(es); results are not comparable")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true", help="generate the manifest")
    parser.add_argument("--limit", type=int, default=LIMIT_DEFAULT)
    args = parser.parse_args(argv)

    if args.write:
        MANIFEST_PATH.write_text(json.dumps(build(args.limit), indent=2) + "\n")
        print(f"wrote {MANIFEST_PATH}")
        return 0
    return verify(args.limit)


if __name__ == "__main__":
    raise SystemExit(main())
