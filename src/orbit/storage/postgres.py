"""Explicit PostgreSQL implementation of the memory persistence port.

The adapter is scoped to one configured workspace. The public project string
maps to ``celestin_projects.legacy_project_key`` within that workspace; every
record operation resolves and uses both workspace and project IDs. Creating
the adapter never connects or runs migrations.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import UUID

from orbit.repositories._validation import (
    ValidationError,
    validate_conversation_id,
    validate_key,
    validate_project,
    validate_role,
    validate_status,
    validate_value,
)

from .protocol import (
    DecisionNotFoundError,
    MemoryStore,
    ProjectNotFoundError,
    SourceMessageNotFoundError,
    StoredDecision,
    StoredMessage,
)
from orbit.mcp_server.hosted_scope import HostedAccessDenied, ResolvedProjectScope


class _PostgresConnection(Protocol):
    def execute(self, query: str, params: tuple[Any, ...] = ()) -> Any: ...


ConnectionFactory = Callable[[], Any]


class PostgresMemoryStore(MemoryStore):
    """Persist messages and decisions in a provisioned PostgreSQL workspace.

    ``connection_factory`` must return a context-managed psycopg connection.
    A connection context commits on success, rolls back on error, and closes
    the connection. Passing a factory keeps the optional driver out of local
    SQLite imports and makes transaction behavior testable without a server.
    """

    def __init__(self, connection_factory: ConnectionFactory, workspace_id: str | UUID) -> None:
        if not callable(connection_factory):
            raise TypeError("connection_factory must be callable")
        try:
            self._workspace_id = str(UUID(str(workspace_id)))
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValueError("workspace_id must be a UUID") from exc
        self._connection_factory = connection_factory
        self._request_connection = None
        self._request_scope: ResolvedProjectScope | None = None

    @classmethod
    def _for_request(
        cls, connection: Any, scope: ResolvedProjectScope
    ) -> PostgresMemoryStore:
        """Bind persistence to an already-authorized request transaction.

        The returned adapter borrows the connection: it never acquires,
        commits, rolls back, or closes it. The authorization factory owns that
        lifecycle. The resolver-issued scope supplies both tenant IDs.
        """
        if connection is None or not callable(getattr(connection, "execute", None)):
            raise TypeError("connection must provide execute()")
        if not isinstance(scope, ResolvedProjectScope):
            raise TypeError("scope must be issued by HostedScopeResolver")
        store = cls.__new__(cls)
        store._workspace_id = str(scope.workspace_id)
        store._connection_factory = None
        store._request_connection = connection
        store._request_scope = scope
        return store

    @classmethod
    def from_dsn(cls, dsn: str, workspace_id: str | UUID) -> PostgresMemoryStore:
        """Build an explicitly standalone adapter; it does not authorize callers.

        Hosted request code must use the request unit of work instead. This
        factory remains for trusted single-workspace tooling and adapter tests.
        """
        if not isinstance(dsn, str) or not dsn.strip():
            raise ValueError("dsn must be a non-empty string")

        def connect():
            try:
                import psycopg
            except ImportError as exc:  # pragma: no cover - optional installation
                raise RuntimeError(
                    "PostgreSQL storage requires the optional psycopg dependency"
                ) from exc
            return psycopg.connect(dsn)

        return cls(connect, workspace_id)

    def insert_message(
        self, project: str, conversation_id: str, role: str, content: str
    ) -> StoredMessage:
        project = validate_project(project)
        conversation_id = validate_conversation_id(conversation_id)
        role = validate_role(role)
        if not isinstance(content, str):
            raise ValidationError("content must be a string")

        self._check_request_scope(project, "write")
        with self._connection() as connection:
            project_id = self._resolve_project(connection, project)
            self._bind_standalone_scope(connection, project_id)
            row = connection.execute(
                """
                INSERT INTO chat_messages
                    (workspace_id, project_id, conversation_id, role, content, created_at)
                VALUES (%s::uuid, %s::uuid, %s, %s, %s, %s)
                RETURNING id, created_at
                """,
                (
                    self._workspace_id,
                    project_id,
                    conversation_id,
                    role,
                    content,
                    datetime.now(timezone.utc),
                ),
            ).fetchone()
            if row is None:
                raise RuntimeError("inserted message was not returned by PostgreSQL")
            message_id, created_at = row
            return StoredMessage(
                message_id=str(message_id),
                project=project,
                conversation_id=conversation_id,
                role=role,
                content=content,
                created_at=_utc_iso(created_at),
            )

    def save_decision(
        self,
        project: str,
        key: str,
        value: str,
        source_message_id: str,
        status: str = "draft",
    ) -> StoredDecision:
        project = validate_project(project)
        key = validate_key(key)
        value = validate_value(value)
        status = validate_status(status)
        source_id = _postgres_message_id(source_message_id)

        self._check_request_scope(project, "write")
        with self._connection() as connection:
            project_id = self._resolve_project(connection, project)
            self._bind_standalone_scope(connection, project_id)
            source = connection.execute(
                """
                SELECT 1
                  FROM chat_messages
                 WHERE workspace_id = %s::uuid AND project_id = %s::uuid AND id = %s
                """,
                (self._workspace_id, project_id, source_id),
            ).fetchone()
            if source is None:
                raise SourceMessageNotFoundError

            now = datetime.now(timezone.utc)
            row = connection.execute(
                """
                INSERT INTO project_memory
                    (workspace_id, project_id, key, value, status, source_message_id,
                     created_at, updated_at)
                VALUES (%s::uuid, %s::uuid, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (project_id, key) DO UPDATE SET
                    value = EXCLUDED.value,
                    status = EXCLUDED.status,
                    source_message_id = EXCLUDED.source_message_id,
                    updated_at = EXCLUDED.updated_at
                WHERE project_memory.workspace_id = EXCLUDED.workspace_id
                  AND project_memory.archived_at IS NULL
                RETURNING id, status, source_message_id, created_at, updated_at, key, value
                """,
                (
                    self._workspace_id,
                    project_id,
                    key,
                    value,
                    status,
                    source_id,
                    now,
                    now,
                ),
            ).fetchone()
            if row is None:
                raise DecisionNotFoundError
            return _stored_decision(project, row)

    def update_decision_status(
        self, project: str, key: str, status: str
    ) -> StoredDecision:
        project = validate_project(project)
        key = validate_key(key)
        status = validate_status(status)
        self._check_request_scope(project, "write")
        with self._connection() as connection:
            project_id = self._resolve_project(connection, project)
            self._bind_standalone_scope(connection, project_id)
            row = connection.execute(
                """
                UPDATE project_memory
                   SET status = %s, updated_at = %s
                 WHERE workspace_id = %s::uuid AND project_id = %s::uuid AND key = %s
                   AND archived_at IS NULL
                RETURNING id, status, source_message_id, created_at, updated_at, key, value
                """,
                (
                    status,
                    datetime.now(timezone.utc),
                    self._workspace_id,
                    project_id,
                    key,
                ),
            ).fetchone()
            if row is None:
                raise DecisionNotFoundError
            return _stored_decision(project, row)

    def _resolve_project(self, connection: _PostgresConnection, project: str) -> str:
        if self._request_scope is not None:
            self._check_request_scope(project, "write")
            return str(self._request_scope.project_id)
        row = connection.execute(
            """
            SELECT id::text
              FROM celestin_projects
             WHERE workspace_id = %s::uuid
               AND legacy_project_key = %s
               AND archived_at IS NULL
            """,
            (self._workspace_id, project),
        ).fetchone()
        if row is None:
            raise ProjectNotFoundError
        return str(row[0])

    def _check_request_scope(self, project: str, permission: str) -> None:
        scope = self._request_scope
        if scope is None:
            return
        if project != scope.project_key or permission not in scope.permissions:
            raise HostedAccessDenied("access denied")

    def _bind_standalone_scope(self, connection: _PostgresConnection, project_id: str) -> None:
        """Set RLS context for trusted standalone use inside its owned transaction."""
        if self._request_scope is not None:
            return
        cursor = connection.execute(
            """
            SELECT set_config('celestin.workspace_id', %s, true),
                   set_config('celestin.project_id', %s, true)
            """,
            (self._workspace_id, project_id),
        )
        close = getattr(cursor, "close", None)
        if callable(close):
            close()

    @contextmanager
    def _connection(self):
        """Borrow request connection or preserve standalone ownership semantics."""
        if self._request_connection is not None:
            yield self._request_connection
            return
        with self._connection_factory() as connection:
            yield connection


def _postgres_message_id(source_message_id: str) -> int:
    """Translate the public opaque decimal ID to PostgreSQL's BIGINT edge."""
    try:
        value = int(source_message_id)
    except (TypeError, ValueError) as exc:
        raise SourceMessageNotFoundError from exc
    if value < 1 or str(value) != source_message_id:
        raise SourceMessageNotFoundError
    return value


def _stored_decision(project: str, row: tuple[Any, ...]) -> StoredDecision:
    decision_id, status, source_message_id, created_at, updated_at, key, value = row
    return StoredDecision(
        decision_id=str(decision_id),
        project=project,
        key=key,
        value=value,
        status=status,
        source_message_id=(
            str(source_message_id) if source_message_id is not None else None
        ),
        created_at=_utc_iso(created_at),
        updated_at=_utc_iso(updated_at),
    )


def _utc_iso(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise TypeError("PostgreSQL timestamp must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )
