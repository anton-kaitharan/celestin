"""PostgreSQL implementation of the retrieval-data port.

This adapter is deliberately not selected by the MCP runtime. Callers must
provide a scope resolved by a trusted hosted request boundary; the public
project argument is only checked against that scope and never selects a tenant.
Each operation opens and closes one connection through the injected factory.
Candidate search and decision authoritative reload share one connection.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import math
import re
from typing import Any, Protocol
from uuid import UUID

from orbit.repositories._validation import validate_project
from orbit.mcp_server.hosted_scope import HostedAccessDenied, ResolvedProjectScope
from orbit.retrieval.data import (
    DecisionRecord,
    MessageRecord,
    RetrievalData,
    SearchMatch,
    SearchPage,
)

_WORD = re.compile(r"[^\W_]+", re.UNICODE)
_QUOTED = re.compile(r'"([^"]+)"')
_IDENTIFIER_LIKE = re.compile(r"[0-9_]|\.\w|\w[/\\]\w")
_STOPWORDS = frozenset(
    "a an the of to in on for and or is are was were be been being how what which who whom whose why "
    "when where does do did can could may might must should would will shall by with from that this "
    "these those it its as at after before over under between about into than then there their they "
    "we our us you your i me my he she his her not no if so but also just".split()
)
_MIN_RELAXED_TERMS = 3
_RELAXED_MATCH_RATIO = 0.6
_RELAXED_POOL_FACTOR = 5


class ProjectScopeMismatch(ValueError):
    """The public project selector does not match the trusted adapter scope."""


class _Cursor(Protocol):
    def fetchall(self) -> Sequence[tuple[Any, ...]]: ...

    def close(self) -> None: ...


class _Connection(Protocol):
    def __enter__(self) -> _Connection: ...

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> Any: ...

    def execute(self, query: str, params: Sequence[object] = ()) -> _Cursor: ...


ConnectionFactory = Callable[[], _Connection]


@dataclass(frozen=True, slots=True)
class PostgresRetrievalScope:
    """Standalone adapter coordinates; not proof of hosted authorization.

    Hosted request code must receive scope only from the authorization resolver
    through the request unit of work.
    """

    workspace_id: UUID | str
    project_id: UUID | str
    project_key: str

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "workspace_id", UUID(str(self.workspace_id)))
            object.__setattr__(self, "project_id", UUID(str(self.project_id)))
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValueError("workspace_id and project_id must be valid UUIDs") from exc
        object.__setattr__(self, "project_key", validate_project(self.project_key))


class PostgresRetrievalData(RetrievalData):
    """Tenant-scoped PostgreSQL full-text search and authoritative record reads.

    Uses PostgreSQL ``simple`` text search with GIN expression indexes. Scores
    are ``-ts_rank_cd`` so the shared layer's lower-is-better ordering
    remains valid. They are not numerically equivalent to SQLite BM25 scores.
    """

    def __init__(
        self, connection_factory: ConnectionFactory, *, scope: PostgresRetrievalScope
    ) -> None:
        if not callable(connection_factory):
            raise TypeError("connection_factory must be callable")
        if not isinstance(scope, PostgresRetrievalScope):
            raise TypeError("scope must be a trusted PostgresRetrievalScope")
        self._connection_factory = connection_factory
        self._scope = scope
        self._request_connection = None
        self._request_permissions: frozenset[str] | None = None

    @classmethod
    def _for_request(
        cls, connection: _Connection, scope: ResolvedProjectScope
    ) -> PostgresRetrievalData:
        """Bind retrieval to an authorized request's connection and scope.

        This adapter borrows the connection and never opens or finalizes a
        transaction. Candidate search and authoritative reload therefore stay
        in the authorization owner's transaction.
        """
        if connection is None or not callable(getattr(connection, "execute", None)):
            raise TypeError("connection must provide execute()")
        if not isinstance(scope, ResolvedProjectScope):
            raise TypeError("scope must be issued by HostedScopeResolver")
        adapter = cls.__new__(cls)
        adapter._scope = PostgresRetrievalScope(
            scope.workspace_id, scope.project_id, scope.project_key
        )
        adapter._connection_factory = None
        adapter._request_connection = connection
        adapter._request_permissions = scope.permissions
        return adapter

    @classmethod
    def from_dsn(
        cls, dsn: str, *, scope: PostgresRetrievalScope
    ) -> PostgresRetrievalData:
        """Create a standalone adapter; it does not authorize its supplied scope.

        Hosted request code must use the request unit of work instead.
        """
        if not isinstance(dsn, str) or not dsn.strip():
            raise ValueError("dsn must be a non-empty string")

        def connect() -> _Connection:
            try:
                import psycopg
            except ImportError as exc:  # pragma: no cover - depends on optional extra
                raise RuntimeError(
                    "PostgreSQL retrieval requires the optional psycopg extra"
                ) from exc
            return psycopg.connect(dsn)

        return cls(connect, scope=scope)

    def search_messages(
        self, project: str, query: str, limit: int, *, relax: bool
    ) -> SearchPage:
        self._check_project(project)
        _validate_search(query, limit)
        strict = _strict_tsquery(query)
        if strict is None:
            return SearchPage((), "strict")
        with self._connection() as connection:
            rows, tier = self._search_tiers(
                connection,
                "chat_messages",
                "content",
                query,
                strict,
                limit,
                relax,
            )
        return SearchPage(
            tuple(
                SearchMatch(
                    record_id=str(row[0]),
                    project=self._scope.project_key,
                    content=str(row[1]),
                    timestamp=_utc_iso(row[2]),
                    source_id=str(row[3]),
                    score=-float(row[4]),
                )
                for row in rows
            ),
            tier,
        )

    def search_decisions(
        self, project: str, query: str, limit: int, *, relax: bool
    ) -> SearchPage:
        self._check_project(project)
        _validate_search(query, limit)
        strict = _strict_tsquery(query)
        if strict is None:
            return SearchPage((), "strict")
        with self._connection() as connection:
            candidate_rows, tier = self._search_tiers(
                connection, "project_memory", "value", query, strict, limit, relax
            )
            if not candidate_rows:
                return SearchPage((), tier)
            ids = [_positive_id(row[0]) for row in candidate_rows]
            records = self._load_decisions(connection, ids)
        matches: list[SearchMatch] = []
        for raw_id, _content, _updated_at, _source_id, score in candidate_rows:
            record_id = str(_positive_id(raw_id))
            record = records.get(record_id)
            if record is None:
                continue  # An archived/stale candidate was not authoritatively visible.
            matches.append(
                SearchMatch(
                    record_id=record.decision_id,
                    project=record.project,
                    content=record.value,
                    timestamp=record.updated_at,
                    source_id=record.source_message_id,
                    score=-float(score),
                    status=record.status,
                )
            )
        return SearchPage(tuple(matches), tier)

    def load_messages(
        self, project: str, message_ids: Sequence[str]
    ) -> dict[str, MessageRecord]:
        self._check_project(project)
        ids = _positive_ids(message_ids)
        if not ids:
            return {}
        with self._connection() as connection:
            rows = _fetchall(
                connection,
                _MESSAGE_LOAD_SQL,
                self._scope_params() + (ids,),
            )
        return {
            str(row[0]): MessageRecord(
                message_id=str(row[0]),
                project=self._scope.project_key,
                conversation_id=str(row[1]),
                content=str(row[2]),
                created_at=_utc_iso(row[3]),
            )
            for row in rows
        }

    def load_decisions(
        self, project: str, decision_ids: Sequence[str]
    ) -> dict[str, DecisionRecord]:
        self._check_project(project)
        ids = _positive_ids(decision_ids)
        if not ids:
            return {}
        with self._connection() as connection:
            return self._load_decisions(connection, ids)

    def _load_decisions(
        self, connection: _Connection, decision_ids: Sequence[int]
    ) -> dict[str, DecisionRecord]:
        rows = _fetchall(
            connection,
            _DECISION_LOAD_SQL,
            self._scope_params() + (list(decision_ids),),
        )
        return {
            str(row[0]): DecisionRecord(
                decision_id=str(row[0]),
                project=self._scope.project_key,
                value=str(row[1]),
                updated_at=_utc_iso(row[2]),
                source_message_id=str(row[3]) if row[3] is not None else None,
                status=str(row[4]),
            )
            for row in rows
        }

    def _search_tiers(
        self,
        connection: _Connection,
        table: str,
        content_column: str,
        query: str,
        strict: str,
        limit: int,
        relax: bool,
    ) -> tuple[list[tuple[Any, ...]], str]:
        run = lambda expression, row_limit: self._run_search(
            connection, table, content_column, expression, row_limit
        )
        rows = run(strict, limit)
        if rows or not relax:
            return rows, "strict"

        stopless = _without_stopwords_tsquery(query)
        if stopless and stopless != strict:
            rows = run(stopless, limit)
            if rows:
                return rows, "no_stopwords"

        plan = _relaxed_query(query)
        if plan is None:
            return [], "strict"
        expression, terms, min_match = plan
        rows = run(expression, limit * _RELAXED_POOL_FACTOR)
        filtered = [
            row for row in rows
            if sum(term in {word.casefold() for word in _WORD.findall(str(row[1]))} for term in terms)
            >= min_match
        ]
        return (filtered[:limit], "relaxed_or") if filtered else ([], "strict")

    def _run_search(
        self,
        connection: _Connection,
        table: str,
        content_column: str,
        expression: str,
        limit: int,
    ) -> list[tuple[Any, ...]]:
        if table == "chat_messages":
            source_select = "record.conversation_id"
            order_time = "record.created_at"
        else:
            source_select = "record.source_message_id"
            order_time = "record.updated_at"
        sql = _search_sql(table, content_column, source_select, order_time)
        return _fetchall(
            connection,
            sql,
            (expression, *self._scope_params(), expression, limit),
        )

    def _scope_params(self) -> tuple[UUID, UUID]:
        return self._scope.workspace_id, self._scope.project_id

    def _check_project(self, project: str) -> None:
        if validate_project(project) != self._scope.project_key:
            raise ProjectScopeMismatch("project does not match the trusted retrieval scope")
        if self._request_permissions is not None and "read" not in self._request_permissions:
            raise HostedAccessDenied("access denied")

    @contextmanager
    def _connection(self):
        """Borrow an authorized request connection or own a standalone one."""
        if self._request_connection is not None:
            yield self._request_connection
            return
        with self._connection_factory() as connection:
            cursor = connection.execute(
                """
                SELECT set_config('celestin.workspace_id', %s, true),
                       set_config('celestin.project_id', %s, true)
                """,
                (str(self._scope.workspace_id), str(self._scope.project_id)),
            )
            close = getattr(cursor, "close", None)
            if callable(close):
                close()
            yield connection


def _search_sql(
    table: str, content_column: str, source_select: str, order_time: str
) -> str:
    # Identifiers come only from the closed call-site constants above.
    vector = f"to_tsvector('simple'::regconfig, record.{content_column})"
    tsquery = "to_tsquery('simple'::regconfig, %s)"
    return f"""
        SELECT record.id, record.{content_column}, record.{order_time.split('.')[-1]},
               {source_select}, ts_rank_cd({vector}, {tsquery}) AS score
          FROM {table} AS record
          JOIN celestin_projects AS project
            ON project.id = record.project_id AND project.workspace_id = record.workspace_id
          JOIN celestin_workspaces AS workspace ON workspace.id = record.workspace_id
         WHERE record.workspace_id = %s::uuid AND record.project_id = %s::uuid
           AND record.archived_at IS NULL
           AND project.archived_at IS NULL AND workspace.archived_at IS NULL
           AND {vector} @@ {tsquery}
         ORDER BY score DESC, {order_time} DESC, record.id ASC
         LIMIT %s
    """


_MESSAGE_LOAD_SQL = """
    SELECT record.id, record.conversation_id, record.content, record.created_at
      FROM chat_messages AS record
      JOIN celestin_projects AS project
        ON project.id = record.project_id AND project.workspace_id = record.workspace_id
      JOIN celestin_workspaces AS workspace ON workspace.id = record.workspace_id
     WHERE record.workspace_id = %s::uuid AND record.project_id = %s::uuid
       AND record.archived_at IS NULL
       AND project.archived_at IS NULL AND workspace.archived_at IS NULL
       AND record.id = ANY(%s::bigint[])
"""

_DECISION_LOAD_SQL = """
    SELECT record.id, record.value, record.updated_at, record.source_message_id, record.status
      FROM project_memory AS record
      JOIN celestin_projects AS project
        ON project.id = record.project_id AND project.workspace_id = record.workspace_id
      JOIN celestin_workspaces AS workspace ON workspace.id = record.workspace_id
     WHERE record.workspace_id = %s::uuid AND record.project_id = %s::uuid
       AND record.archived_at IS NULL
       AND project.archived_at IS NULL AND workspace.archived_at IS NULL
       AND record.id = ANY(%s::bigint[])
"""


def _fetchall(
    connection: _Connection, query: str, params: Sequence[object]
) -> list[tuple[Any, ...]]:
    cursor = connection.execute(query, params)
    try:
        return list(cursor.fetchall())
    finally:
        cursor.close()


def _validate_search(query: str, limit: int) -> None:
    if not isinstance(query, str):
        raise TypeError("query must be a string")
    if "\x00" in query:
        raise ValueError("query must not contain NUL characters")
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise TypeError("limit must be an integer")
    if limit < 1:
        raise ValueError("limit must be at least 1")


def _strict_tsquery(query: str) -> str | None:
    phrases = [_WORD.findall(part) for part in _QUOTED.findall(query)]
    terms = _WORD.findall(_QUOTED.sub(" ", query))
    parts = [" <-> ".join(words) for words in phrases if words]
    parts.extend(terms)
    return " & ".join(f"({part})" if " <-> " in part else part for part in parts) or None


def _without_stopwords_tsquery(query: str) -> str:
    terms = _WORD.findall(_QUOTED.sub(lambda match: match.group(1), query))
    kept = [term for term in terms if term.casefold() not in _STOPWORDS]
    return " & ".join(kept) if kept and len(kept) != len(terms) else ""


def _relaxed_query(query: str) -> tuple[str, list[str], int] | None:
    raw = re.findall(r"\S+", _QUOTED.sub(lambda match: match.group(1), query))
    required: list[str] = []
    optional: list[str] = []
    for token in raw:
        words = _WORD.findall(token)
        if _IDENTIFIER_LIKE.search(token):
            required.extend(word for word in words if word not in required)
        else:
            optional.extend(
                word for word in words
                if word.casefold() not in _STOPWORDS and word not in optional and word not in required
            )
    terms = required + optional
    if len(terms) < _MIN_RELAXED_TERMS or not optional:
        return None
    min_match = max(len(required) + 1, math.ceil(len(terms) * _RELAXED_MATCH_RATIO))
    optional_expression = " | ".join(optional)
    expression = (
        f"{' & '.join(required)} & ({optional_expression})"
        if required else f"({optional_expression})"
    )
    return expression, [term.casefold() for term in terms], min_match


def _positive_ids(values: Sequence[str]) -> list[int]:
    result: list[int] = []
    for value in values:
        try:
            numeric = int(value)
        except (TypeError, ValueError):
            continue
        if numeric > 0 and str(numeric) == str(value):
            result.append(numeric)
    return result


def _positive_id(value: Any) -> int:
    numeric = int(value)
    if numeric < 1 or str(numeric) != str(value):
        raise ValueError("PostgreSQL returned an invalid record ID")
    return numeric


def _utc_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise TypeError("PostgreSQL timestamp must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )
