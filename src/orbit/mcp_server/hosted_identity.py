"""Fail-closed boundary between transport authentication and hosted requests.

This module defines contracts and orchestration only. It contains no credential
validator, identity-provider client, or workspace-binding implementation, and it
is not wired into the local SQLite stdio runtime.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Protocol, TypeVar
from uuid import UUID

from orbit.mcp_server.hosted_scope import (
    HostedAccessDenied,
    HostedAuthorizationUnavailable,
    HostedScopeResolver,
    PrincipalStatusProvider,
    ProjectAction,
    VerifiedPrincipal,
    WorkspaceBinding,
)

if TYPE_CHECKING:
    from orbit.postgres.unit_of_work import PostgresRequestUnitOfWork


class PrincipalAuthenticationFailed(PermissionError):
    """A credential was missing, invalid, expired, or otherwise rejected."""


class PrincipalAuthenticatorUnavailable(RuntimeError):
    """Credential verification could not be completed safely."""


class PrincipalAuthenticator(Protocol):
    """Validate transport evidence and return its verified principal.

    Implementations must validate the credential using their configured trust
    source. This protocol and ``VerifiedPrincipal`` do not perform or prove
    cryptographic verification. The context must come from a trusted transport
    adapter, never from ordinary MCP tool arguments.
    """

    def authenticate(self, transport_context: object) -> VerifiedPrincipal: ...


class _AuthorizedRequestExecutor(Protocol):
    """Internal executor owned by the authenticated request composition."""

    def _execute_authorized_request(
        self,
        resolver: HostedScopeResolver,
        principal: VerifiedPrincipal,
        project_key: str,
        action: ProjectAction,
        operation: Callable[[PostgresRequestUnitOfWork], Any],
        *,
        source_message_id: str | None = None,
        requested_workspace_id: UUID | None = None,
    ) -> Any: ...


ResultT = TypeVar("ResultT")


class HostedRequestBoundary:
    """Authenticate transport context, then delegate scoped request execution.

    All dependencies are explicitly injected. Missing authentication,
    workspace/status resolution, or request execution fails closed. There is no
    default authenticator, identity source, workspace mapping, or MCP wiring.
    """

    def __init__(
        self,
        *,
        authenticator: PrincipalAuthenticator | None,
        workspace_binding: WorkspaceBinding | None,
        principal_status: PrincipalStatusProvider | None,
        request_executor: _AuthorizedRequestExecutor | None,
    ) -> None:
        self._authenticator = authenticator
        self._workspace_binding = workspace_binding
        self._principal_status = principal_status
        self._resolver = (
            HostedScopeResolver(workspace_binding, principal_status)
            if workspace_binding is not None and principal_status is not None
            else None
        )
        self._request_executor = request_executor

    def execute_authorized_request(
        self,
        transport_context: object,
        project_key: str,
        action: ProjectAction,
        operation: Callable[[PostgresRequestUnitOfWork], ResultT],
        *,
        workspace_selector: object = None,
        source_message_id: str | None = None,
    ) -> ResultT:
        """Authenticate first; the resolver and executor authorize before work.

        ``project_key`` is only a requested resource selector. No principal or
        workspace ID is accepted as a request argument.
        """
        if not callable(operation):
            raise TypeError("operation must be callable")
        if self._authenticator is None:
            raise HostedAuthorizationUnavailable(
                "hosted authentication is not configured"
            )

        try:
            principal = self._authenticator.authenticate(transport_context)
        except PrincipalAuthenticationFailed:
            raise HostedAccessDenied("access denied") from None
        except Exception:
            # Do not expose provider, credential, or transport details.
            raise HostedAuthorizationUnavailable(
                "authentication service is unavailable"
            ) from None

        if not isinstance(principal, VerifiedPrincipal):
            raise HostedAuthorizationUnavailable(
                "authentication service returned an invalid identity"
            )
        if self._workspace_binding is None:
            raise HostedAuthorizationUnavailable(
                "trusted workspace binding is not configured"
            )
        if self._principal_status is None or self._resolver is None:
            raise HostedAuthorizationUnavailable(
                "principal status authorization is not configured"
            )
        if self._request_executor is None:
            raise HostedAuthorizationUnavailable(
                "hosted request execution is not configured"
            )

        requested_workspace_id = _parse_workspace_selector(workspace_selector)

        # This is the supported hosted application entry point. The executor
        # is an internal dependency and must not be exposed to MCP handlers.
        return self._request_executor._execute_authorized_request(
            self._resolver,
            principal,
            project_key,
            action,
            operation,
            requested_workspace_id=requested_workspace_id,
            source_message_id=source_message_id,
        )


def _parse_workspace_selector(value: object) -> UUID:
    """Require exactly one canonical workspace UUID from the HTTP boundary."""
    if isinstance(value, (tuple, list)):
        if len(value) != 1:
            raise HostedAccessDenied("access denied")
        value = value[0]
    if not isinstance(value, str) or len(value) != 36 or value != value.strip():
        raise HostedAccessDenied("access denied")
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError):
        raise HostedAccessDenied("access denied") from None
    if str(parsed) != value.lower():
        raise HostedAccessDenied("access denied")
    return parsed
