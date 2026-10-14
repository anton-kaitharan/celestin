"""Project authorization for the local Orbit MCP boundary.

Stdio does not authenticate individual MCP clients. The local policy therefore
authorizes the server process for an explicit project/action allowlist and
denies all projects when no policy is configured. It does not protect against
untrusted processes running as the same local user.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Literal, Mapping, Protocol


READ = "read"
WRITE = "write"
PROJECT_ACTIONS = frozenset({READ, WRITE})
ProjectAction = Literal["read", "write"]
LOCAL_PROJECT_ACCESS_ENV = "ORBIT_LOCAL_PROJECT_ACCESS"


class ProjectAccessDenied(PermissionError):
    """The caller has no configured permission for a project/action."""


@dataclass(frozen=True, slots=True)
class AuthenticatedPrincipal:
    """Identity established by a future trusted transport authenticator."""

    issuer: str
    subject: str


@dataclass(frozen=True, slots=True)
class AuthorizedProject:
    """Server-issued project scope returned only after a policy decision."""

    project: str
    action: str
    principal: AuthenticatedPrincipal | None


class ProjectAuthorizer(Protocol):
    """Authorize an authenticated identity or the local stdio process."""

    def authorize(
        self,
        principal: AuthenticatedPrincipal | None,
        project: str,
        action: ProjectAction,
    ) -> AuthorizedProject: ...


def _validated_access_map(
    access: Mapping[str, object],
) -> dict[str, frozenset[str]]:
    validated: dict[str, frozenset[str]] = {}
    for project, actions in access.items():
        if not isinstance(project, str) or not project.strip() or "\x00" in project:
            raise ValueError("authorization project names must be non-empty strings")
        if not isinstance(actions, (list, tuple, set, frozenset)):
            raise ValueError("authorization actions must be an array")
        action_set = frozenset(actions)
        if not action_set or not action_set <= PROJECT_ACTIONS:
            raise ValueError("authorization actions must contain read and/or write")
        validated[project] = action_set
    return validated


class ProjectAccessPolicy:
    """Fail-closed local allowlist plus exact principal/project/action grants.

    The optional principal map is an interface seam for a future authenticated
    transport. It is not loaded from caller input and is not enabled by stdio.
    """

    def __init__(
        self,
        local_access: Mapping[str, object] | None = None,
        principal_access: Mapping[
            AuthenticatedPrincipal, Mapping[str, object]
        ] | None = None,
    ) -> None:
        self._local_access = _validated_access_map(local_access or {})
        self._principal_access = {
            principal: _validated_access_map(projects)
            for principal, projects in (principal_access or {}).items()
        }

    @classmethod
    def from_environment(cls) -> ProjectAccessPolicy:
        """Load the explicit local allowlist; absent configuration denies all."""
        raw = os.environ.get(LOCAL_PROJECT_ACCESS_ENV)
        if raw is None or not raw.strip():
            return cls()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{LOCAL_PROJECT_ACCESS_ENV} must contain valid JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"{LOCAL_PROJECT_ACCESS_ENV} must be a JSON object")
        return cls(local_access=payload)

    def authorize(
        self,
        principal: AuthenticatedPrincipal | None,
        project: str,
        action: ProjectAction,
    ) -> AuthorizedProject:
        if action not in PROJECT_ACTIONS:
            raise ProjectAccessDenied("project access denied")
        grants = (
            self._local_access
            if principal is None
            else self._principal_access.get(principal, {})
        )
        if action not in grants.get(project, frozenset()):
            raise ProjectAccessDenied("project access denied")
        return AuthorizedProject(project=project, action=action, principal=principal)
