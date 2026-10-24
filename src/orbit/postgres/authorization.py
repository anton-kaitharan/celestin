"""Request-scoped PostgreSQL authorization for the hosted scope resolver.

This module is inert at import time. Constructing a factory stores only an
injected connection factory; it does not connect or run migrations.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID

from orbit.mcp_server.hosted_scope import (
    HostedAccessDenied,
    HostedAuthorizationSession,
    HostedAuthorizationUnavailable,
    ProjectAction,
    HostedScopeResolver,
    HostedSourceMessageNotFound,
    ProjectAccessRecord,
    ProjectScopedSourceMessage,
    ResolvedProjectScope,
    VerifiedPrincipal,
)
from orbit.postgres.authorization_locking import (
    lock_project_request_authorization,
    lock_request_authorization,
)

if TYPE_CHECKING:
    from orbit.postgres.unit_of_work import PostgresRequestUnitOfWork


class _Connection(Protocol):
    def execute(self, query: str, params: tuple[Any, ...] = ()) -> Any: ...

    def transaction(self) -> Any: ...

    def close(self) -> None: ...


ConnectionFactory = Callable[[], _Connection]


class PostgresHostedAuthorizationSession(HostedAuthorizationSession):
    """Authorization reads protected by transaction-scoped advisory locks.

    The runtime role stays read-only on authorization tables. Database triggers
    and the authorization provisioner take the matching exclusive lock before
    a membership, grant, lifecycle, or principal-status change can commit.
    """

    def __init__(
        self, connection: _Connection, *, project_scoped_lock: bool = False
    ) -> None:
        self._connection = connection
        self._project_scoped_lock = project_scoped_lock
        self._identities: dict[tuple[VerifiedPrincipal, UUID], tuple[UUID, str]] = {}
        self._bound_scope: ResolvedProjectScope | None = None

    @property
    def _connection_for_adapters(self) -> _Connection:
        """Connection for trusted request-composition code only."""
        return self._connection

    def bind_scope(self, scope: ResolvedProjectScope) -> None:
        """Require this login identity to map to the resolved project scope."""
        if not isinstance(scope, ResolvedProjectScope):
            raise HostedAuthorizationUnavailable("authorization state is unavailable")
        if self._bound_scope is not None:
            if self._bound_scope != scope:
                raise HostedAuthorizationUnavailable("authorization state is unavailable")
            return
        try:
            cursor = self._connection.execute(
                "SELECT public.celestin_request_scope_matches(%s, %s)",
                (str(scope.workspace_id), str(scope.project_id)),
            )
            row = cursor.fetchone()
            close = getattr(cursor, "close", None)
            if callable(close):
                close()
            if row is None or row[0] is not True:
                raise HostedAuthorizationUnavailable(
                    "database identity is not bound to the authorized project"
                )
            self._bound_scope = scope
        except HostedAuthorizationUnavailable:
            raise
        except Exception:
            raise HostedAuthorizationUnavailable(
                "authorization state is unavailable"
            ) from None

    def is_principal_active(
        self, principal: VerifiedPrincipal, workspace_id: UUID
    ) -> bool:
        """Map trusted issuer/subject/kind to an active internal identity."""
        try:
            if self._project_scoped_lock:
                lock_project_request_authorization(self._connection, workspace_id)
            else:
                lock_request_authorization(
                    self._connection,
                    workspace_id,
                    principal.kind,
                    principal.issuer,
                    principal.subject,
                )
            workspace = self._connection.execute(
                """SELECT id FROM celestin_workspaces
                     WHERE id = %s""",
                (workspace_id,),
            ).fetchone()
            if workspace is None:
                return False

            if principal.kind == "human":
                row = self._connection.execute(
                    """SELECT id, status FROM celestin_users
                         WHERE issuer = %s AND subject = %s""",
                    (principal.issuer, principal.subject),
                ).fetchone()
                active_status = "active"
            else:
                row = self._connection.execute(
                    """SELECT id, status FROM celestin_service_identities
                         WHERE workspace_id = %s AND issuer = %s AND subject = %s""",
                    (workspace_id, principal.issuer, principal.subject),
                ).fetchone()
                active_status = "active"

            if row is None:
                return False
            identity_id, status = row
            if not isinstance(identity_id, UUID):
                identity_id = UUID(str(identity_id))
            self._identities[(principal, workspace_id)] = (identity_id, str(status))
            return status == active_status
        except Exception:
            raise HostedAuthorizationUnavailable(
                "authorization state is unavailable"
            ) from None

    def load_project_access(
        self,
        principal: VerifiedPrincipal,
        workspace_id: UUID,
        project_key: str,
    ) -> ProjectAccessRecord | None:
        """Load lifecycle and explicit grant facts, scoped to one workspace."""
        try:
            identity = self._identities.get((principal, workspace_id))
            if identity is None:
                if not self.is_principal_active(principal, workspace_id):
                    return None
                identity = self._identities[(principal, workspace_id)]
            principal_id, identity_status = identity

            workspace = self._connection.execute(
                """SELECT archived_at FROM celestin_workspaces
                     WHERE id = %s""",
                (workspace_id,),
            ).fetchone()
            if workspace is None:
                return None
            project = self._connection.execute(
                """SELECT id, legacy_project_key, archived_at
                     FROM celestin_projects
                    WHERE workspace_id = %s AND legacy_project_key = %s""",
                (workspace_id, project_key),
            ).fetchone()
            if project is None:
                return None
            project_id, stored_key, project_archived_at = project
            if not isinstance(project_id, UUID):
                project_id = UUID(str(project_id))

            membership_status: str | None = None
            service_status: str | None = None
            if principal.kind == "human":
                service_status = None
                membership = self._connection.execute(
                    """SELECT status FROM celestin_workspace_memberships
                         WHERE workspace_id = %s AND user_id = %s""",
                    (workspace_id, principal_id),
                ).fetchone()
                if membership is not None:
                    membership_status = str(membership[0])
                grant = self._connection.execute(
                    """SELECT can_read, can_write, can_approve_candidates,
                              can_convert_memory, can_read_history, can_read_audit,
                              revoked_at
                         FROM celestin_project_memberships
                        WHERE workspace_id = %s AND project_id = %s AND user_id = %s""",
                    (workspace_id, project_id, principal_id),
                ).fetchone()
            else:
                service_status = identity_status
                membership = None
                grant = self._connection.execute(
                    """SELECT can_read, can_write, can_convert_memory,
                              can_read_history, can_read_audit, revoked_at
                         FROM celestin_service_project_grants
                        WHERE workspace_id = %s AND project_id = %s
                          AND service_identity_id = %s""",
                    (workspace_id, project_id, principal_id),
                ).fetchone()

            if grant is None:
                can_read = can_write = False
                can_approve_candidates = can_convert_memory = False
                can_read_history = can_read_audit = False
                grant_revoked = False
            elif principal.kind == "human":
                (
                    can_read,
                    can_write,
                    can_approve_candidates,
                    can_convert_memory,
                    can_read_history,
                    can_read_audit,
                    revoked_at,
                ) = grant
                grant_revoked = revoked_at is not None
            else:
                (
                    can_read,
                    can_write,
                    can_convert_memory,
                    can_read_history,
                    can_read_audit,
                    revoked_at,
                ) = grant
                can_approve_candidates = False
                grant_revoked = revoked_at is not None

            return ProjectAccessRecord(
                principal_id=principal_id,
                workspace_id=workspace_id,
                project_id=project_id,
                project_key=str(stored_key),
                workspace_archived=workspace[0] is not None,
                project_archived=project_archived_at is not None,
                membership_status=membership_status,
                service_status=service_status,
                grant_present=grant is not None,
                grant_revoked=grant_revoked,
                can_read=bool(can_read),
                can_write=bool(can_write),
                can_approve_candidates=bool(can_approve_candidates),
                can_convert_memory=bool(can_convert_memory),
                can_read_history=bool(can_read_history),
                can_read_audit=bool(can_read_audit),
            )
        except HostedAuthorizationUnavailable:
            raise
        except Exception:
            raise HostedAuthorizationUnavailable(
                "authorization state is unavailable"
            ) from None

    def load_source_message_reference(
        self, scope: ResolvedProjectScope, message_id: str
    ) -> ProjectScopedSourceMessage | None:
        """Resolve a decimal public message ID only inside the resolved scope."""
        if not isinstance(message_id, str) or not message_id.isdecimal():
            return None
        try:
            numeric_id = int(message_id)
            if numeric_id < 1 or str(numeric_id) != message_id:
                return None
            row = self._connection.execute(
                """SELECT id FROM chat_messages
                    WHERE workspace_id = %s AND project_id = %s AND id = %s
                    """,
                (scope.workspace_id, scope.project_id, numeric_id),
            ).fetchone()
            if row is None:
                return None
            return ProjectScopedSourceMessage(
                message_id=str(row[0]),
                workspace_id=scope.workspace_id,
                project_id=scope.project_id,
            )
        except Exception:
            raise HostedAuthorizationUnavailable(
                "authorization state is unavailable"
            ) from None


class PostgresHostedAuthorizationSessionFactory:
    """Open one connection/transaction per authorized hosted operation."""

    def __init__(
        self,
        connection_factory: ConnectionFactory,
        *,
        statement_timeout_ms: int | None = None,
    ) -> None:
        if not callable(connection_factory):
            raise TypeError("connection_factory must be callable")
        if statement_timeout_ms is not None and (
            isinstance(statement_timeout_ms, bool)
            or not isinstance(statement_timeout_ms, int)
            or statement_timeout_ms < 1
        ):
            raise ValueError("statement_timeout_ms must be a positive integer")
        self._connection_factory = connection_factory
        self._statement_timeout_ms = statement_timeout_ms

    @classmethod
    def from_dsn(cls, dsn: str) -> PostgresHostedAuthorizationSessionFactory:
        """Create a lazy optional-psycopg factory; no import or connection now."""
        if not isinstance(dsn, str) or not dsn.strip():
            raise ValueError("dsn must be a non-empty string")

        def connect() -> _Connection:
            try:
                import psycopg
            except ImportError as exc:  # pragma: no cover - optional dependency
                raise RuntimeError(
                    "Hosted PostgreSQL authorization requires the psycopg extra"
                ) from exc
            return psycopg.connect(dsn)

        return cls(connect)

    @contextmanager
    def _transaction(
        self,
        connection_factory: ConnectionFactory | None = None,
        *,
        project_scoped_lock: bool = False,
    ) -> Iterator[PostgresHostedAuthorizationSession]:
        """Internal transaction owner; commit, rollback, and always close."""
        try:
            connection = (connection_factory or self._connection_factory)()
        except Exception:
            raise HostedAuthorizationUnavailable(
                "authorization service is unavailable"
            ) from None
        try:
            with connection.transaction():
                if self._statement_timeout_ms is not None:
                    cursor = connection.execute(
                        "SELECT set_config('statement_timeout', %s, true)",
                        (f"{self._statement_timeout_ms}ms",),
                    )
                    close = getattr(cursor, "close", None)
                    if callable(close):
                        close()
                yield PostgresHostedAuthorizationSession(
                    connection, project_scoped_lock=project_scoped_lock
                )
        except (HostedAccessDenied, HostedSourceMessageNotFound, HostedAuthorizationUnavailable):
            raise
        except Exception:
            # This also covers commit failures. Do not expose SQL, DSNs, or driver details.
            raise HostedAuthorizationUnavailable(
                "authorized operation could not be completed"
            ) from None
        finally:
            try:
                connection.close()
            except Exception:
                # Do not claim success if the request resource could not close.
                raise HostedAuthorizationUnavailable(
                    "authorization service could not release its database session"
                ) from None

    def _execute_authorized(
        self,
        resolver: HostedScopeResolver,
        principal: VerifiedPrincipal,
        project_key: str,
        action: ProjectAction,
        operation: Callable[[PostgresHostedAuthorizationSession, ResolvedProjectScope], Any],
        *,
        source_message_id: str | None = None,
        requested_workspace_id: UUID | None = None,
    ) -> Any:
        """Run resolver authorization and callback in one owned transaction."""
        with self._transaction() as session:
            result = resolver.execute(
                session,
                principal,
                project_key,
                action,
                operation,
                source_message_id=source_message_id,
                requested_workspace_id=requested_workspace_id,
            )
        return result

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
    ) -> Any:
        """Bind request adapters to this factory's authorized connection.

        This base factory remains useful for offline/scripted and explicit
        standalone uses. Hosted MCP composition uses the project-scoped
        subclass, which has no shared-role fallback.
        """
        if not callable(operation):
            raise TypeError("operation must be callable")
        from orbit.postgres.unit_of_work import PostgresRequestUnitOfWork

        def invoke(session, scope):
            request = PostgresRequestUnitOfWork._bind(session, resolver, scope)
            return operation(request)

        return self._execute_authorized(
            resolver,
            principal,
            project_key,
            action,
            invoke,
            source_message_id=source_message_id,
            requested_workspace_id=requested_workspace_id,
        )


class ProjectScopedPostgresHostedAuthorizationSessionFactory(
    PostgresHostedAuthorizationSessionFactory
):
    """Authorize for routing, then reauthorize inside a project-login UoW.

    The control connection is read-only for tenant content. It resolves the
    authorized project before a project-specific connection can be acquired.
    The project connection repeats every authorization check and validates
    ``session_user`` scope in the same transaction as protected operations.
    There is deliberately no shared-runtime fallback.
    """

    def __init__(
        self,
        control_connection_factory: ConnectionFactory,
        project_connection_factory: Callable[[ResolvedProjectScope], _Connection],
        *,
        statement_timeout_ms: int | None = None,
    ) -> None:
        if not callable(project_connection_factory):
            raise TypeError("project_connection_factory must be callable")
        super().__init__(
            control_connection_factory,
            statement_timeout_ms=statement_timeout_ms,
        )
        self._project_connection_factory = project_connection_factory

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
    ) -> Any:
        if not callable(operation):
            raise TypeError("operation must be callable")
        # Keep this import local because unit_of_work imports this module for
        # the session type. The hosted executor needs the concrete binder only
        # after the project connection has passed final authorization.
        from orbit.postgres.unit_of_work import PostgresRequestUnitOfWork

        # This read-only control transaction authorizes routing only. Its scope
        # and locks are discarded before acquiring a content-capable login.
        with self._transaction() as control_session:
            route_scope = resolver.resolve(
                control_session,
                principal,
                project_key,
                action,
                requested_workspace_id=requested_workspace_id,
            )

        def invoke(session, resolved_scope):
            if resolved_scope != route_scope:
                raise HostedAccessDenied("access denied")
            request = PostgresRequestUnitOfWork._bind(
                session, resolver, resolved_scope
            )
            return operation(request)

        def connect_to_project() -> _Connection:
            try:
                return self._project_connection_factory(route_scope)
            except Exception:
                # Never fall back to the control/shared credential or expose a
                # pool key, DSN, project ID, or secret lookup details.
                raise HostedAuthorizationUnavailable(
                    "project database is unavailable"
                ) from None

        with self._transaction(
            connect_to_project, project_scoped_lock=True
        ) as project_session:
            result = resolver.execute(
                project_session,
                principal,
                project_key,
                action,
                invoke,
                source_message_id=source_message_id,
                requested_workspace_id=requested_workspace_id,
            )
        return result
