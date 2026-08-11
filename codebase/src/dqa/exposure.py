"""Decomposition of the AgentLeak exposure metric.

The pooled AgentLeak mean reported by :mod:`dqa.run` is a single number
over a cohort that mixes resource types. This module exists to make two
properties of that number reproducible in code rather than by argument.

1. **The pooled mean is a cohort composition statistic.** Decomposed by
   ``resourceType`` the per-type means are flat: they do not move with
   manifest width for most cells. What moves the pooled number is the
   mix of Patient / Encounter / Observation records in the cohort, which
   :func:`dqa.run.read_cohort` fixes by quota rather than by policy. A
   metric that is meant to measure the manifests should respond to the
   manifests.

2. **The clamp discards multiplicity.** ``LeakRecord.value`` is
   ``min(1.0, exposed / total)`` (``metrics.py:119``). Because every
   agent whose manifest declares a policy for the type receives its own
   slice, the same Safe Harbor JSONPath is counted once per slice in the
   numerator while the denominator counts it once in the resource. The
   ratio therefore saturates on the majority of records, and the clamp
   throws away how far past 1.0 it went.

Four means are computed side by side so the gap is visible:

``clamped_mean``
    Today's metric, reproduced exactly.
``unclamped_mean``
    The same ratio with no ceiling. Its excess over ``clamped_mean`` is
    the information the clamp removes.
``union_mean``
    Each distinct Safe-Harbor-bearing JSONPath counted once across all
    agent slices instead of once per slice, i.e. what a given agent
    *collectively* learned rather than how many envelopes carried it.
    Measured equal to ``clamped_mean`` in all 27 shipped cells.
``union_unclamped_mean``
    The same union numerator with the ceiling removed. Measured equal to
    ``union_mean`` in all 27 cells too: the clamp was not what made the
    union column redundant. See :class:`ExposureStats` for what was, and
    for :attr:`ExposureStats.multiplicity_inflation`, the column that does
    carry what the union numerator was introduced to show.

Nothing here mutates or re-implements :mod:`dqa.metrics`; ``phi_total``,
``phi_exposed`` and ``SAFE_HARBOR_MARKERS`` are imported from it so the two
stay definitionally locked together.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dqa.manifests import ManifestSet, project
from dqa.metrics import SAFE_HARBOR_MARKERS, phi_exposed, phi_total

__all__ = [
    "ExposureStats",
    "exposure_by_type",
    "exposure_pooled",
    "phi_union",
    "released_slices",
]


@dataclass(frozen=True, slots=True)
class ExposureStats:
    """Exposure summarised over one group of scoreable records.

    A record is scoreable when ``phi_total > 0``; records with no Safe
    Harbor content in the original resource are excluded, matching
    :func:`dqa.metrics.leak_mean`.

    On the union columns
    --------------------
    ``union_mean`` was introduced to answer a different question from
    ``clamped_mean``: not how many envelopes carried a Safe Harbor path,
    but how much of the record an agent *collectively* learned. It does
    not answer it, in the sense that it was measured **equal to**
    ``clamped_mean`` in every one of the 27 shipped cells (3 cohorts x 3
    widths x 3 resource types). The column added nothing to the column
    beside it.

    The obvious suspect was the clamp: ``union_mean`` is itself wrapped in
    ``min(1.0, ...)``, so one would expect it to collapse the way
    ``clamped_mean`` does. **That diagnosis is wrong, and this is the one
    place it is recorded as measured rather than assumed.**
    ``union_unclamped_mean`` computes the same ratio with the ceiling
    removed, and it is equal to ``union_mean`` in all 27 cells, because
    the union numerator never reaches the denominator from above: over all
    1,800 scored records in the three cohorts at all three widths,
    ``max(u/t) == 1.0`` and not one record has ``u > t``. The clamp on the
    union is a no-op on the shipped manifests. It is retained rather than
    deleted because it is not dead in general: ``phi_union`` keys on
    released JSONPaths while ``phi_total`` keys on the nine entries of
    ``SAFE_HARBOR_JSONPATHS``, so a manifest releasing two distinct
    Safe-Harbor-marked paths that fall under one denominator path -- say
    both ``$.name[*]`` and ``$.name[*].family`` -- would put ``u`` above
    ``t``. No shipped manifest does. ``tests/test_exposure.py`` pins the
    no-op so that stops being silent if one ever does.

    The real cause of the redundancy is narrower and worth stating: ``u``
    can differ from the per-slice numerator ``e`` only where a path is
    released to more than one agent, and on these cohorts multiplicity
    occurs only on records where ``e >= t`` already. So wherever the union
    would have said something different, the clamp on ``clamped_mean`` had
    already said the same thing for an unrelated reason.

    What the union does carry, and what the three columns above hid, is
    :attr:`multiplicity_inflation`: the distance between counting a
    released Safe Harbor path once per envelope and counting it once
    overall. That is the quantity the union was introduced to expose, and
    it is far from zero -- e.g. 0.89 against 0.6125 on synthea at
    intermediate width. It is reported as a derived column so the union
    numerator earns its place.

    ``union_mean`` itself is retained, unchanged, rather than removed.
    Removing it was considered and rejected: it is a key in six shipped
    result artefacts (``results_stratified_model``,
    ``results_legacy_model``, ``results_stratified_offline``,
    ``results_stratified_r010`` and the three ``rep_seed*`` files), and
    both deleting the key and silently moving the value under a name
    readers have already read would break the artefacts this bundle exists
    to make reproducible. A mode flag was also considered and rejected: a
    flag would make the *meaning* of a single column depend on how the
    harness was invoked, which is worse than several columns whose names
    each state what they contain.

    ``at_ceiling`` versus ``at_ceiling_strict``
    -------------------------------------------
    ``at_ceiling`` counts ``e >= t``, i.e. it counts ties. A tie is a record
    where the clamp had nothing to discard: ``min(1.0, 1.0) == 1.0``. So
    ``at_ceiling`` cannot be evidence that the clamp destroyed information.
    ``at_ceiling_strict`` counts ``e > t`` only -- records whose unclamped
    exposure strictly exceeds 1.0, which is exactly the set of records where
    the clamp discarded a measured quantity. Both are reported; only the
    strict count supports a claim about clamp loss.
    """

    n: int
    clamped_mean: float
    unclamped_mean: float
    union_mean: float
    at_ceiling: int
    # Appended with defaults, deliberately. ``ExposureStats`` is constructed
    # positionally in this module and elsewhere; appending required fields
    # would break every existing five-argument call site, including ones
    # outside this package. The defaults are never relied on by
    # :func:`_summarise`, which always passes all seven.
    union_unclamped_mean: float = 0.0
    at_ceiling_strict: int = 0

    @property
    def ceiling_fraction(self) -> float:
        """Share of scoreable records where the clamp binds or ties."""
        return self.at_ceiling / self.n if self.n else 0.0

    @property
    def strict_ceiling_fraction(self) -> float:
        """Share of scoreable records where the clamp actually discarded value.

        The counterpart of :attr:`ceiling_fraction` that excludes ties, so
        it is the fraction that can be cited as clamp loss.
        """
        return self.at_ceiling_strict / self.n if self.n else 0.0

    @property
    def clamp_loss(self) -> float:
        """How much mean exposure the clamp removes."""
        return self.unclamped_mean - self.clamped_mean

    @property
    def union_clamp_loss(self) -> float:
        """How much the clamp removes from the union ratio.

        Measured at exactly 0.0 in all 27 shipped cells: the union
        numerator never exceeds the denominator on the shipped manifests.
        See the class docstring for why the clamp is nonetheless kept.
        """
        return self.union_unclamped_mean - self.union_mean

    @property
    def multiplicity_inflation(self) -> float:
        """Mean exposure attributable to re-counting one path across envelopes.

        ``unclamped_mean`` counts a released Safe Harbor path once per
        agent slice that carried it; ``union_unclamped_mean`` counts it
        once. The gap is therefore the share of the raw exposure ratio
        that is the same disclosure counted again, and it is what makes
        ``unclamped_mean`` exceed 1.0 in the first place. This is the
        information the union numerator was introduced to expose;
        ``union_mean`` on its own does not expose it, because it coincides
        with ``clamped_mean`` on the shipped cohorts.
        """
        return self.unclamped_mean - self.union_unclamped_mean


def released_slices(
    resource: dict[str, Any], manifest_set: ManifestSet
) -> list[dict[str, Any]]:
    """The projected-field dicts released to agents for one resource.

    Identical to what :meth:`dqa.agents.Orchestrator.evaluate` hands the
    exposure metric: one entry per manifest that declares a policy for
    this resource type, each entry keyed by JSONPath. Manifests with no
    policy for the type contribute nothing.
    """
    out: list[dict[str, Any]] = []
    for manifest in manifest_set.values():
        sliced = project(resource, manifest)
        if sliced is None:
            continue
        out.append(sliced["_projected_fields"])
    return out


def phi_union(projected_messages: list[dict[str, Any]]) -> int:
    """Safe Harbor occurrences released, de-duplicated across slices.

    :func:`dqa.metrics.phi_exposed` sums occurrences over every slice, so
    a path released to all four agents is counted four times. Here each
    distinct JSONPath contributes its occurrence count exactly once, so
    the numerator is commensurable with :func:`dqa.metrics.phi_total`,
    which counts occurrences in the resource once.
    """
    per_path: dict[str, int] = {}
    for message in projected_messages:
        for path, values in message.items():
            if not any(marker in path for marker in SAFE_HARBOR_MARKERS):
                continue
            count = len(values) if isinstance(values, list) else 1
            if count > per_path.get(path, 0):
                per_path[path] = count
    return sum(per_path.values())


def _summarise(pairs: list[tuple[int, int, int]]) -> ExposureStats:
    """Build stats from ``(exposed, union, total)`` triples.

    Only records with ``total > 0`` are scoreable; the rest are dropped
    exactly as ``leak_mean`` drops them.
    """
    scoreable = [(e, u, t) for e, u, t in pairs if t > 0]
    n = len(scoreable)
    if n == 0:
        return ExposureStats(0, 0.0, 0.0, 0.0, 0, 0.0, 0)

    clamped = sum(min(1.0, e / t) for e, _, t in scoreable) / n
    unclamped = sum(e / t for e, _, t in scoreable) / n
    union = sum(min(1.0, u / t) for _, u, t in scoreable) / n
    union_unclamped = sum(u / t for _, u, t in scoreable) / n
    # ``>= t`` includes ties, where the clamp discarded nothing.
    at_ceiling = sum(1 for e, _, t in scoreable if e >= t)
    # ``> t`` only: the records where min(1.0, e/t) threw a measurement away.
    at_ceiling_strict = sum(1 for e, _, t in scoreable if e > t)
    return ExposureStats(
        n=n,
        clamped_mean=clamped,
        unclamped_mean=unclamped,
        union_mean=union,
        at_ceiling=at_ceiling,
        union_unclamped_mean=union_unclamped,
        at_ceiling_strict=at_ceiling_strict,
    )


def _triple(
    resource: dict[str, Any], manifest_set: ManifestSet
) -> tuple[int, int, int]:
    messages = released_slices(resource, manifest_set)
    return phi_exposed(messages), phi_union(messages), phi_total(resource)


def exposure_by_type(
    cohort: list[dict[str, Any]], manifest_set: ManifestSet
) -> dict[str, ExposureStats]:
    """Exposure statistics per FHIR ``resourceType``.

    Each resource is projected through every manifest in the set, the
    released slices are collected, and the group is scored. Resource
    types with no scoreable records still appear, with ``n == 0``.
    """
    grouped: dict[str, list[tuple[int, int, int]]] = {}
    for resource in cohort:
        resource_type = str(resource.get("resourceType", "Unknown"))
        grouped.setdefault(resource_type, []).append(_triple(resource, manifest_set))
    return {rt: _summarise(triples) for rt, triples in grouped.items()}


def exposure_pooled(
    cohort: list[dict[str, Any]], manifest_set: ManifestSet
) -> ExposureStats:
    """Exposure pooled over every resource type in the cohort.

    ``clamped_mean`` here is by construction the ``agentleak_mean`` that
    :func:`dqa.run.evaluate_dataset` records, and ``n`` is the cohort
    size minus its ``agentleak_excluded_t0``.
    """
    return _summarise([_triple(resource, manifest_set) for resource in cohort])
