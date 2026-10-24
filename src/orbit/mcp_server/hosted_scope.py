"""Hosted identity-to-project authorization boundary.

This module does not authenticate callers or select a transport. A trusted
authenticator must create ``VerifiedPrincipal`` values, and a server-controlled
workspace binding must select the workspace. Hosted data operations should use
``execute`` so authorization and the operation share the same request session.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal, Protocol, TypeVar
from uuid import UUID


PrincipalKind = Literal["human", "service"]
ProjectAction = Literal[
    "read",
    "write",
    "memory:read",
    "candidate:approve",
    "memory:convert",
    "memory:history",
    "audit:read",
]
ProjectPermission = ProjectAction
_PROJECT_PERMISSIONS = frozenset(
    {
        "read",
        "write",
        "memory:read",
        "candidate:approve",
        "memory:convert",
        "memory:history",
        "audit:read",
    }
)
_SCOPE_ISSUER = object()


class HostedAccessDenied(PermissionError):
    """The principal is not permitted to access the selected project/action."""


class HostedAuthorizationUnavailable(RuntimeError):
    """Authorization state could not be established; the request must fail closed."""


class HostedSourceMessageNotFound(LookupError):
    """A source message is absent from the authorized project scope."""


@dataclass(frozen=True, slots=True)
class VerifiedPrincipal:
    """Identity established by a trusted authenticator, never MCP arguments."""

    issuer: str
    subject: str
    kind: PrincipalKind

    def __post_init__(self) -> None:
        for name, value in (("issuer", self.issuer), ("subject", self.subject)):
            if not isinstance(value, str) or not value.strip() or "\x00" in value:
                raise ValueError(f"{name} must be a non-empty string without NUL")
        if self.kind not in ("human", "service"):
            raise ValueError("kind must be 'human' or 'service'")


@dataclass(frozen=True, slots=True, init=False)
class ResolvedProjectScope:
    """Server-issued workspace/project scope with effective permissions."""

    principal: VerifiedPrincipal
    principal_id: UUID
    workspace_id: UUID
    project_id: UUID
    project_key: str
    permissions: frozenset[ProjectPermission]

    def __init__(
        self,
        principal: VerifiedPrincipal,
        principal_id: UUID,
        workspace_id: UUID,
        project_id: UUID,
        project_key: str,
        permissions: frozenset[ProjectPermission],
        *,
        _issuer: object | None = None,
    ) -> None:
        if _issuer is not _SCOPE_ISSUER:
            raise TypeError("ResolvedProjectScope values are issued by HostedScopeResolver")
        for name, value in (
            ("principal", principal),
            ("principal_id", principal_id),
            ("workspace_id", workspace_id),
            ("project_id", project_id),
            ("project_key", project_key),
            ("permissions", permissions),
        ):
            object.__setattr__(self, name, value)
        self.__post_init__()

    @classmethod
    def _issue(
        cls,
        principal: VerifiedPrincipal,
        principal_id: UUID,
        workspace_id: UUID,
        project_id: UUID,
        project_key: str,
        permissions: frozenset[ProjectPermission],
    ) -> ResolvedProjectScope:
        return cls(
            principal,
            principal_id,
            workspace_id,
            project_id,
            project_key,
            permissions,
            _issuer=_SCOPE_ISSUER,
        )

    def __post_init__(self) -> None:
        if not isinstance(self.principal, VerifiedPrincipal):
            raise TypeError("principal must be a VerifiedPrincipal")
        for name in ("principal_id", "workspace_id", "project_id"):
            if not isinstance(getattr(self, name), UUID):
                raise TypeError(f"{name} must be a UUID")
        if (
            not isinstance(self.project_key, str)
            or not self.project_key.strip()
            or "\x00" in self.project_key
        ):
            raise ValueError("project_key must be a non-empty string")
        if not isinstance(self.permissions, frozenset):
            raise TypeError("permissions must be a frozenset")
        if not self.permissions or not self.permissions <= _PROJECT_PERMISSIONS:
            raise ValueError("permissions contain an unsupported project capability")
        if "write" in self.permissions and "read" not in self.permissions:
            raise ValueError("write permission requires read permission")


@dataclass(frozen=True, slots=True)
class ProjectAccessRecord:
    """Authorization metadata loaded for one identity/workspace/project tuple.

    A missing explicit grant is represented by ``grant_present=False``. For
    human principals, ``membership_status`` must be ``active``. For service
    principals, ``service_status`` must be ``active``. The session query must
    constrain both workspace ID and exact project key and must not read memory
    content, messages, embeddings, or retrieval indexes.
    """

    principal_id: UUID
    workspace_id: UUID
    project_id: UUID
    project_key: str
    workspace_archived: bool
    project_archived: bool
    membership_status: str | None
    service_status: str | None
    grant_present: bool
    grant_revoked: bool
    can_read: bool
    can_write: bool
    can_approve_candidates: bool = False
    can_convert_memory: bool = False
    can_read_history: bool = False
    can_read_audit: bool = False

    def __post_init__(self) -> None:
        for name in ("principal_id", "workspace_id", "project_id"):
            if not isinstance(getattr(self, name), UUID):
                raise TypeError(f"{name} must be a UUID")
        if (
            not isinstance(self.project_key, str)
            or not self.project_key.strip()
            or "\x00" in self.project_key
        ):
            raise ValueError("project_key must be a non-empty string")
        for name in (
            "workspace_archived",
            "project_archived",
            "grant_present",
            "grant_revoked",
            "can_read",
            "can_write",
            "can_approve_candidates",
            "can_convert_memory",
            "can_read_history",
            "can_read_audit",
        ):
            if type(getattr(self, name)) is not bool:
                raise TypeError(f"{name} must be a bool")
        if self.membership_status not in (None, "invited", "active", "suspended"):
            raise ValueError("membership_status is invalid")
        if self.service_status not in (None, "active", "revoked"):
            raise ValueError("service_status is invalid")


@dataclass(frozen=True, slots=True)
class ProjectScopedSourceMessage:
    """Minimal ownership metadata for a source message resolved in a scope."""

    message_id: str
    workspace_id: UUID
    project_id: UUID

    def __post_init__(self) -> None:
        if not isinstance(self.message_id, str) or not self.message_id.strip() or "\x00" in self.message_id:
            raise ValueError("message_id must be a non-empty string")
        if not isinstance(self.workspace_id, UUID) or not isinstance(self.project_id, UUID):
            raise TypeError("source message workspace_id and project_id must be UUIDs")


class PrincipalStatusProvider(Protocol):
    """Checks active human identity state from a trusted identity authority."""

    def is_active(self, principal: VerifiedPrincipal) -> bool: ...


class WorkspaceBinding(Protocol):
    """Resolve an untrusted transport selector for database authorization.

    The selector is not an authorization claim. Implementations return a
    workspace candidate; the authorization session must still verify the
    principal's active binding and project grant in the request transaction.
    """

    def workspace_for(
        self, principal: VerifiedPrincipal, requested_workspace_id: UUID | None = None
    ) -> UUID | None: ...


class HostedAuthorizationSession(Protocol):
    """Request-scoped authorization reads, implemented by hosted storage sessions."""

    def load_project_access(
        self,
        principal: VerifiedPrincipal,
        workspace_id: UUID,
        project_key: str,
    ) -> ProjectAccessRecord | None: ...

    def load_source_message_reference(
        self, scope: ResolvedProjectScope, message_id: str
    ) -> ProjectScopedSourceMessage | None:
        """Look up only an ID in the authorized scope; do not search other tenants."""
        ...


SessionT = TypeVar("SessionT", bound=HostedAuthorizationSession)
ResultT = TypeVar("ResultT")


class HostedScopeResolver:
    """Resolve project selectors inside trusted workspace and grant boundaries."""

    def __init__(
        self,
        workspace_binding: WorkspaceBinding,
        principal_status: PrincipalStatusProvider,
    ) -> None:
        self._workspace_binding = workspace_binding
        self._principal_status = principal_status

    def resolve(
        self,
        session: HostedAuthorizationSession,
        principal: VerifiedPrincipal,
        project_key: str,
        action: ProjectAction,
        *,
        requested_workspace_id: UUID | None = None,
    ) -> ResolvedProjectScope:
        """Return a scope only when all identity, lifecycle, and grant checks pass."""
        if not isinstance(principal, VerifiedPrincipal):
            raise HostedAccessDenied("access denied")
        if action not in _PROJECT_PERMISSIONS:
            raise HostedAccessDenied("access denied")
        if not isinstance(project_key, str) or not project_key.strip() or "\x00" in project_key:
            raise HostedAccessDenied("access denied")

        try:
            workspace_id = self._workspace_binding.workspace_for(
                principal, requested_workspace_id
            )
            if not isinstance(workspace_id, UUID):
                raise HostedAccessDenied("access denied")
            # A database-backed session owns the authoritative identity lookup
            # and row lock, keeping it in the same transaction as the operation.
            session_identity_check = getattr(session, "is_principal_active", None)
            if callable(session_identity_check):
                principal_active = session_identity_check(principal, workspace_id)
            else:
                principal_active = self._principal_status.is_active(principal)
            if type(principal_active) is not bool:
                raise HostedAuthorizationUnavailable(
                    "authorization state is unavailable"
                )
            if not principal_active:
                raise HostedAccessDenied("access denied")
        except HostedAccessDenied:
            raise
        except Exception as exc:
            raise HostedAuthorizationUnavailable(
                "authorization state is unavailable"
            ) from exc

        try:
            record = session.load_project_access(principal, workspace_id, project_key)
        except Exception as exc:
            raise HostedAuthorizationUnavailable(
                "authorization state is unavailable"
            ) from exc

        if record is not None and not isinstance(record, ProjectAccessRecord):
            raise HostedAuthorizationUnavailable("authorization state is unavailable")
        if record is None:
            raise HostedAccessDenied("access denied")
        if (
            record.workspace_id != workspace_id
            or record.project_key != project_key
            or record.workspace_archived
            or record.project_archived
            or not record.grant_present
            or record.grant_revoked
            or (record.can_write and not record.can_read)
        ):
            raise HostedAccessDenied("access denied")

        if principal.kind == "human":
            if record.membership_status != "active":
                raise HostedAccessDenied("access denied")
        elif record.service_status != "active":
            raise HostedAccessDenied("access denied")

        permissions_set: set[ProjectPermission] = set()
        if record.can_read:
            permissions_set.add("read")
            permissions_set.add("memory:read")
        if record.can_write:
            permissions_set.add("write")
        if principal.kind == "human" and record.can_approve_candidates:
            permissions_set.add("candidate:approve")
        if record.can_convert_memory:
            permissions_set.add("memory:convert")
        if record.can_read_history:
            permissions_set.add("memory:history")
        if record.can_read_audit:
            permissions_set.add("audit:read")
        permissions = frozenset(permissions_set)

        if action not in permissions:
            raise HostedAccessDenied("access denied")

        return ResolvedProjectScope._issue(
            principal=principal,
            principal_id=record.principal_id,
            workspace_id=record.workspace_id,
            project_id=record.project_id,
            project_key=record.project_key,
            permissions=permissions,
        )

    def require_source_message(
        self,
        session: HostedAuthorizationSession,
        scope: ResolvedProjectScope,
        message_id: str,
    ) -> ProjectScopedSourceMessage:
        """Require source provenance to resolve within the already-authorized scope.

        The session query must constrain by both IDs from ``scope``. A mismatch
        returned by an implementation is treated exactly like a missing record.
        """
        if not isinstance(scope, ResolvedProjectScope):
            raise HostedAccessDenied("access denied")
        if not isinstance(message_id, str) or not message_id.strip() or "\x00" in message_id:
            raise HostedSourceMessageNotFound("source message not found")
        try:
            reference = session.load_source_message_reference(scope, message_id)
        except Exception as exc:
            raise HostedAuthorizationUnavailable(
                "authorization state is unavailable"
            ) from exc
        if reference is not None and not isinstance(reference, ProjectScopedSourceMessage):
            raise HostedAuthorizationUnavailable("authorization state is unavailable")
        if (
            reference is None
            or reference.message_id != message_id
            or reference.workspace_id != scope.workspace_id
            or reference.project_id != scope.project_id
        ):
            raise HostedSourceMessageNotFound("source message not found")
        return reference

    def execute(
        self,
        session: SessionT,
        principal: VerifiedPrincipal,
        project_key: str,
        action: ProjectAction,
        operation: Callable[[SessionT, ResolvedProjectScope], ResultT],
        *,
        source_message_id: str | None = None,
        requested_workspace_id: UUID | None = None,
    ) -> ResultT:
        """Authorize first, then call the operation with the same request session.

        The caller owns the transaction. The authorization lookup and operation
        must share it so a PostgreSQL implementation can lock/recheck grants and
        serialize revocation against data access.
        """
        scope = self.resolve(
            session,
            principal,
            project_key,
            action,
            requested_workspace_id=requested_workspace_id,
        )
        # PostgreSQL request sessions set transaction-local RLS context only
        # after authorization, but before any source/data query. This matters
        # for source-message validation because chat_messages itself is RLS-
        # protected in the hosted schema.
        bind_scope = getattr(session, "bind_scope", None)
        if callable(bind_scope):
            bind_scope(scope)
        if source_message_id is not None:
            self.require_source_message(session, scope, source_message_id)
        return operation(session, scope)
