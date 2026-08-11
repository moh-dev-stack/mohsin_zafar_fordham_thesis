"""Privilege manifests: schema, loading, and the projection boundary.

A manifest declares, per FHIR resource type, the exact set of JSONPath
expressions one agent is permitted to read. Projection is the single
architectural enforcement point: the orchestrator passes agents the
output of :func:`project`, never the raw resource.

Three widths live under ``manifests/``: minimal, intermediate, full.
"""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from jsonpath_ng.ext import parse as _jsonpath_parse_raw
from pydantic import BaseModel, ConfigDict, Field, field_validator


@lru_cache(maxsize=512)
def jsonpath_parse(expression: str) -> Any:
    """Compile a JSONPath once; parsing dominates runtime otherwise."""
    return _jsonpath_parse_raw(expression)

Dimension = Literal["completeness", "plausibility", "consistency", "timeliness"]
DIMENSIONS: tuple[Dimension, ...] = (
    "completeness",
    "plausibility",
    "consistency",
    "timeliness",
)
Width = Literal["minimal", "intermediate", "full"]
WIDTHS: tuple[Width, ...] = ("minimal", "intermediate", "full")


class AllowedField(BaseModel):
    """One JSONPath expression an agent may read."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    jsonpath_expression: str = Field(min_length=2)
    description: str | None = None

    @field_validator("jsonpath_expression")
    @classmethod
    def _must_be_jsonpath(cls, value: str) -> str:
        if not value.startswith("$"):
            raise ValueError("jsonpath_expression must begin with '$'")
        return value


class ResourceTypePolicy(BaseModel):
    """Allow-list of field projections for one FHIR resource type."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    fhir_resource_type: str = Field(min_length=1)
    allowed_fields: list[AllowedField] = Field(min_length=1)


class NoteAccess(BaseModel):
    """Whether an agent may read free-text note content."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    sections: list[str] | None = None
    max_sentences: int | None = Field(default=None, ge=1, le=500)


class RuleReference(BaseModel):
    """Pointer to a rule definition the agent applies."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: str = Field(min_length=1)
    rule_id: str = Field(min_length=1)


class Manifest(BaseModel):
    """A complete per-agent privilege manifest.

    Validation is fail-closed: an invalid manifest aborts the run before
    any FHIR resource is read.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_id: str = Field(min_length=1)
    dimension: Dimension
    resource_types: list[ResourceTypePolicy] = Field(min_length=1)
    note_access: NoteAccess = Field(default_factory=NoteAccess)
    rule_references: list[RuleReference] = Field(min_length=1)

    def policy_for(self, resource_type: str) -> ResourceTypePolicy | None:
        for policy in self.resource_types:
            if policy.fhir_resource_type == resource_type:
                return policy
        return None


ManifestSet = dict[str, Manifest]


def load_width(manifests_dir: Path, width: Width) -> ManifestSet:
    """Load the four manifests for one privilege width.

    The returned set is keyed by the dimension the file is *named* for, and
    :meth:`dqa.agents.Orchestrator.evaluate` dispatches on that key. A file
    whose own ``dimension:`` field disagreed with its name would therefore be
    routed to the wrong checker with nothing to show it had happened, so the two
    are required to agree here, before any resource is read.
    """
    out: ManifestSet = {}
    for dimension in DIMENSIONS:
        path = manifests_dir / width / f"{dimension}.yaml"
        if not path.is_file():
            raise FileNotFoundError(f"missing manifest: {path}")
        manifest = Manifest(**yaml.safe_load(path.read_text()))
        if manifest.dimension != dimension:
            raise ValueError(
                f"{path}: declares dimension {manifest.dimension!r} but is "
                f"loaded as {dimension!r}; the orchestrator dispatches on the "
                f"filename, so this manifest would reach the wrong checker"
            )
        out[dimension] = manifest
    return out


def load_all_widths(manifests_dir: Path) -> dict[Width, ManifestSet]:
    """Load every width. Used by the three-width sweep."""
    return {width: load_width(manifests_dir, width) for width in WIDTHS}


def manifests_hash(manifest_set: ManifestSet) -> str:
    """Stable SHA-256 over a manifest set, recorded on every run output."""
    blob = json.dumps(
        {dim: m.model_dump() for dim, m in sorted(manifest_set.items())},
        sort_keys=True,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def project(resource: dict[str, Any], manifest: Manifest) -> dict[str, Any] | None:
    """Return the manifest-permitted slice of ``resource``.

    Returns ``None`` when the manifest declares no policy for this
    resource type, which the orchestrator reads as "does not apply".

    The returned slice carries only the JSONPath keys the manifest
    released. Anything absent here is invisible to the agent, which is
    the least-privilege guarantee the architecture rests on.
    """
    resource_type = resource.get("resourceType")
    if not isinstance(resource_type, str):
        return None

    policy = manifest.policy_for(resource_type)
    if policy is None:
        return None

    projected: dict[str, list[Any]] = {}
    for field in policy.allowed_fields:
        values = [m.value for m in jsonpath_parse(field.jsonpath_expression).find(resource)]
        if values:
            projected[field.jsonpath_expression] = values

    return {
        "resourceType": resource_type,
        "_agent_id": manifest.agent_id,
        "_dimension": manifest.dimension,
        "_projected_fields": projected,
    }
