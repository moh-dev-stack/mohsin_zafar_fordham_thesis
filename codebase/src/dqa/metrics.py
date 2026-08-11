"""Scoring: detection F1 per dimension plus the AgentLeak exposure score.

Detection treats each resource as one row: the injected dimension (or
none) against the set of dimensions the system flagged.

The exposure score is a **per-record normalised count of Safe Harbor field
occurrences released across inter-agent messages**: the occurrences appearing in
any agent's projected slice, over the occurrences present in the resource,
clamped at 1.0. It is defined here, in :func:`phi_exposed`, :func:`phi_total`
and :class:`LeakRecord`, and nowhere else.

ATTRIBUTION, corrected 2026-08-06. The name and the motivating idea are
**adapted from** El Yagoubi et al. (2026), *AgentLeak: A Benchmark for
Internal-Channel Privacy Leakage in Multi-Agent LLM Systems*, arXiv:2602.11510,
which establishes that inter-agent messages are a leakage channel that
output-only auditing misses. The specific quantity computed here is **not** one
of that paper's metrics: it reports channel-level leakage rates over scenarios,
whereas this is a per-record field-occurrence ratio over one FHIR resource.
Earlier editions of this docstring said the metric was "per El Yagoubi et al.",
which implied the definition was theirs. It is not. Cite the paper for the
channel argument and this module for the metric.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dqa.manifests import DIMENSIONS, Dimension, jsonpath_parse

# ------------------------------------------------------------------ detection


@dataclass(frozen=True, slots=True)
class Row:
    """Ground truth versus prediction for one resource."""

    resource_id: str
    true_dimension: Dimension | None
    predicted_failures: frozenset[Dimension]


def _counts(rows: list[Row], dimension: Dimension) -> tuple[int, int, int]:
    tp = fp = fn = 0
    for row in rows:
        is_true = row.true_dimension == dimension
        is_pred = dimension in row.predicted_failures
        if is_true and is_pred:
            tp += 1
        elif not is_true and is_pred:
            fp += 1
        elif is_true and not is_pred:
            fn += 1
    return tp, fp, fn


def f1(rows: list[Row], dimension: Dimension) -> float:
    tp, fp, fn = _counts(rows, dimension)
    p = tp / (tp + fp) if (tp + fp) else 0.0
    r = tp / (tp + fn) if (tp + fn) else 0.0
    return 2 * p * r / (p + r) if (p + r) else 0.0


def score(rows: list[Row]) -> dict[str, float]:
    """Per-dimension F1 plus macro F1."""
    out = {f"f1_{d}": f1(rows, d) for d in DIMENSIONS}
    out["macro_f1"] = sum(out.values()) / len(DIMENSIONS)
    return out


# ------------------------------------------------------------------ AgentLeak

SAFE_HARBOR_JSONPATHS: dict[str, list[str]] = {
    "name": ["$.name[*]"],
    "address": ["$.address[*]"],
    "telecom": ["$.telecom[*]"],
    "birthdate": ["$.birthDate"],
    "identifier": ["$.identifier[*].value"],
    "patient_reference": ["$.subject.reference"],
    "encounter_reference": ["$.encounter.reference"],
    "practitioner_reference": ["$.recorder.reference", "$.performer[*].reference"],
    "device_reference": ["$.device.reference"],
}

# A projected-fields dict is keyed by JSONPath. A key containing one of
# these markers means the manifest released Safe Harbor content.
#
# Public because ``dqa.exposure`` decomposes the same metric and must use the
# same marker list or the two stop being definitionally locked together.
#
# WHY these exact strings: the numerator (``phi_exposed``, matching markers
# against released keys) and the denominator (``phi_total``, evaluating
# SAFE_HARBOR_JSONPATHS against the resource) have to be counting the same
# thing, or the ratio is not a proportion of anything. Two markers used to be
# looser than their paths -- bare ``"identifier"`` against
# ``$.identifier[*].value``, and bare ``"performer"`` against
# ``$.performer[*].reference`` -- so a manifest releasing ``$.identifier[*]
# .system`` or ``$.performer[*].display`` would have counted in the numerator
# and not in the denominator. No shipped manifest releases either, so no
# published number moves; the assertion below is what stops one from being
# added silently.
SAFE_HARBOR_MARKERS = (
    ".name",
    ".address",
    ".telecom",
    ".birthDate",
    "identifier[*].value",
    "subject.reference",
    "encounter.reference",
    "recorder.reference",
    "performer[*].reference",
    "device.reference",
)

# Retained so any caller written against the private name keeps working.
_SAFE_HARBOR_MARKERS = SAFE_HARBOR_MARKERS


def _assert_markers_cover_paths() -> None:
    """Every counted path must be matchable, and every marker must count.

    Runs at import so a divergence between the two definitions is a startup
    failure rather than a quietly wrong exposure score.
    """
    for label, paths in SAFE_HARBOR_JSONPATHS.items():
        for path in paths:
            if not any(marker in path for marker in SAFE_HARBOR_MARKERS):
                raise AssertionError(
                    f"SAFE_HARBOR_JSONPATHS[{label!r}] path {path!r} is counted "
                    f"in phi_total but no marker matches it, so phi_exposed can "
                    f"never count it: the exposure ratio is not a proportion"
                )
    for marker in SAFE_HARBOR_MARKERS:
        if not any(
            marker in path for paths in SAFE_HARBOR_JSONPATHS.values() for path in paths
        ):
            raise AssertionError(
                f"marker {marker!r} matches no path in SAFE_HARBOR_JSONPATHS, so "
                f"it can put occurrences in the numerator that the denominator "
                f"never counts"
            )


_assert_markers_cover_paths()


def phi_total(resource: dict[str, Any]) -> int:
    """Safe Harbor field-occurrences present in the original resource."""
    total = 0
    for paths in SAFE_HARBOR_JSONPATHS.values():
        for path in paths:
            total += sum(1 for _ in jsonpath_parse(path).find(resource))
    return total


def phi_exposed(projected_messages: list[dict[str, Any]]) -> int:
    """Safe Harbor field-occurrences released across all agent slices."""
    total = 0
    for message in projected_messages:
        for path, values in message.items():
            if any(marker in path for marker in SAFE_HARBOR_MARKERS):
                total += len(values) if isinstance(values, list) else 1
    return total


@dataclass(frozen=True, slots=True)
class LeakRecord:
    """Per-record exposure: E released out of T present."""

    resource_id: str
    exposed: int
    total: int

    @property
    def scoreable(self) -> bool:
        return self.total > 0

    @property
    def value(self) -> float:
        return min(1.0, self.exposed / self.total) if self.total else 0.0


def leak_record(
    resource: dict[str, Any], projected_messages: list[dict[str, Any]]
) -> LeakRecord:
    return LeakRecord(
        resource_id=str(resource.get("id", "")),
        exposed=phi_exposed(projected_messages),
        total=phi_total(resource),
    )


def leak_mean(records: list[LeakRecord]) -> tuple[float, int]:
    """Mean over scoreable records; the count of T=0 records excluded."""
    scoreable = [r.value for r in records if r.scoreable]
    excluded = len(records) - len(scoreable)
    return (sum(scoreable) / len(scoreable) if scoreable else 0.0), excluded
