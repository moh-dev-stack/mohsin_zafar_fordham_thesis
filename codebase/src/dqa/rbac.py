"""Role-Based Access Control at the orchestrator boundary.

A caller's role determines whether a DQA invocation is permitted on a
given resource at all. The manifest layer is the second RBAC artefact;
this module is the first.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from pathlib import Path


class RolePolicy(BaseModel):
    """What a single role may do."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    role: str = Field(min_length=1)
    allowed_resource_types: list[str] = Field(min_length=1)
    allowed_dimensions: list[str] = Field(min_length=1)


class RBACPolicy(BaseModel):
    """The full RBAC policy file."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(default=1, ge=1)
    roles: list[RolePolicy] = Field(min_length=1)

    @property
    def by_role(self) -> dict[str, RolePolicy]:
        return {p.role: p for p in self.roles}


class AccessDenied(RuntimeError):
    """Raised when a caller cannot be authorised under the loaded RBAC policy."""


def load_rbac(path: Path) -> RBACPolicy:
    return RBACPolicy.model_validate(yaml.safe_load(path.read_text()))


def authorise(
    policy: RBACPolicy,
    role: str,
    resource: dict[str, Any],
    dimensions: list[str],
) -> list[str]:
    """Return the subset of `dimensions` the caller is authorised to run.

    Raises AccessDenied if the role is unknown or the resource type is
    not in the role's allow-list.
    """
    role_policy = policy.by_role.get(role)
    if role_policy is None:
        raise AccessDenied(f"unknown role: {role}")
    resource_type = resource.get("resourceType")
    if resource_type not in role_policy.allowed_resource_types:
        raise AccessDenied(f"role '{role}' may not run DQA on resourceType '{resource_type}'")
    return [d for d in dimensions if d in role_policy.allowed_dimensions]
