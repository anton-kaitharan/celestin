"""PostgreSQL retrieval adapter tests using a scripted psycopg-shaped connection."""

from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import datetime, timezone
from uuid import UUID

import pytest

from orbit.retrieval.postgres_data import (
    PostgresRetrievalData,
    PostgresRetrievalScope,
    ProjectScopeMismatch,
)
from orbit.retrieval.service import RetrievalService

WORKSPACE = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
PROJECT = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
OTHER_PROJECT = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
NOW = datetime(2026, 10, 17, 3, 30, 0, tzinfo=timezone.utc)


class Cursor:
    def __init__(self, rows):
        self.rows = list(rows)
        self.closed = False

    def fetchall(self):
        return self.rows

    def close(self):
        self.closed = True


class Connection(AbstractContextManager):
    def __init__(self, database):
        self.database = database
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.closed = True
        return False

    def execute(self, query, params=()):
        self.database.calls.append((query, tuple(params), self))
        rows = self.database.respond(query, tuple(params))
        cursor = Cursor(rows)
        self.database.cursors.append(cursor)
        return cursor


class Database:
    def __init__(self, respond=None):
        self.calls = []
        self.cursors = []
        self.connections = []
        self._respond = respond or (lambda query, params: [])

    def connect(self):
        connection = Connection(self)
        self.connections.append(connection)
        return connection

    def respond(self, query, params):
        return self._respond(query, params)


@pytest.fixture
def scope():
    return PostgresRetrievalScope(WORKSPACE, PROJECT, "celestin")


def adapter(database, scope):
    return PostgresRetrievalData(database.connect, scope=scope)


def data_calls(database):
    return [call for call in database.calls if "set_config('celestin.workspace_id'" not in call[0]]


def assert_query_is_scoped(query, params):
    normalized = " ".join(query.split())
    assert "record.workspace_id = %s::uuid" in normalized
    assert "record.project_id = %s::uuid" in normalized
    assert "record.archived_at IS NULL" in normalized
    assert "project.archived_at IS NULL" in normalized
    assert "workspace.archived_at IS NULL" in normalized
    scope_offset = 1 if "to_tsquery" in query else 0
    assert params[scope_offset:scope_offset + 2] == (WORKSPACE, PROJECT)


def test_scope_is_required_and_public_project_cannot_select_another_scope(scope):
    with pytest.raises(TypeError, match="scope"):
        PostgresRetrievalData(lambda: None)

    database = Database()
    with pytest.raises(ProjectScopeMismatch):
        adapter(database, scope).search_messages("other-project", "query", 10, relax=True)
    assert database.connections == []


def test_message_search_maps_postgres_types_scopes_and_closes_resources(scope):
    def respond(query, params):
        if "FROM chat_messages AS record" in query:
            return [
                (42, "release notes", NOW, "chat-1", 0.75),
                (41, "release notes earlier", NOW, "chat-2", 0.75),
            ]
        return []

    database = Database(respond)
    page = adapter(database, scope).search_messages("celestin", "release notes", 5, relax=False)

    assert [(hit.record_id, hit.source_id, hit.score) for hit in page.matches] == [
        ("42", "chat-1", -0.75), ("41", "chat-2", -0.75)
    ]
    assert page.matches[0].timestamp == "2026-10-17T03:30:00.000000Z"
    assert page.matches[0].project == "celestin"
    query, params, _ = data_calls(database)[0]
    assert_query_is_scoped(query, params)
    assert "@@" in query and "ts_rank_cd" in query and "ORDER BY score DESC" in query
    assert "ORDER BY score DESC, record.created_at DESC, record.id ASC" in query
    assert "release notes" not in query
    assert params == ("release & notes", WORKSPACE, PROJECT, "release & notes", 5)
    assert all(cursor.closed for cursor in database.cursors)
    assert all(connection.closed for connection in database.connections)


@pytest.mark.parametrize("query", ["", "  ", "!!! ???"])
def test_empty_or_tokenless_message_query_does_not_connect(scope, query):
    database = Database()
    page = adapter(database, scope).search_messages("celestin", query, 10, relax=True)
    assert page.matches == () and page.tier == "strict"
    assert database.connections == []


def test_quoted_phrase_is_a_parameterized_adjacency_query(scope):
    database = Database()
    adapter(database, scope).search_messages(
        "celestin", '"release notes"', 10, relax=False
    )
    query, params, _ = data_calls(database)[0]
    assert params[0] == "(release <-> notes)"
    assert '"release notes"' not in query


@pytest.mark.parametrize("query, limit, error", [
    (None, 1, TypeError), ("bad\x00query", 1, ValueError), ("query", 0, ValueError),
    ("query", True, TypeError),
])
def test_invalid_search_inputs_fail_before_connecting(scope, query, limit, error):
    database = Database()
    with pytest.raises(error):
        adapter(database, scope).search_messages("celestin", query, limit, relax=True)
    assert database.connections == []


def test_stopword_relaxation_uses_bounded_tiers_and_parameterized_query(scope):
    def respond(query, params):
        if "FROM chat_messages AS record" not in query:
            return []
        if params[0] == "owns & release & notes":
            return [(7, "Priya owns the release notes", NOW, "chat", 0.5)]
        return []

    database = Database(respond)
    page = adapter(database, scope).search_messages(
        "celestin", "who owns the release notes", 4, relax=True
    )
    assert page.tier == "no_stopwords"
    assert [hit.record_id for hit in page.matches] == ["7"]
    assert len(data_calls(database)) == 2
    assert all(call[1][1:3] == (WORKSPACE, PROJECT) for call in data_calls(database))


def test_relaxed_or_tier_preserves_identifier_terms_and_pool_bound(scope):
    def respond(query, params):
        if "FROM chat_messages AS record" not in query:
            return []
        if params[0] == "(hosted | deployment | simple | pizza)":
            return [(8, "hosted deployment uses a simple service", NOW, "chat", 0.2)]
        return []

    database = Database(respond)
    page = adapter(database, scope).search_messages(
        "celestin", "why hosted deployment simple pizza", 3, relax=True
    )
    assert page.tier == "relaxed_or"
    assert [hit.record_id for hit in page.matches] == ["8"]
    assert data_calls(database)[-1][1][-1] == 15

    identifier_db = Database()
    identifier_page = adapter(identifier_db, scope).search_messages(
        "celestin", "what failed with ERR_PAYMENT_9999 nightly gateway", 5, relax=True
    )
    assert identifier_page.matches == ()
    assert all(
        "ERR" in call[1][0] and "9999" in call[1][0]
        for call in data_calls(identifier_db)
    )


def test_decision_search_reloads_authoritative_rows_and_filters_archived(scope):
    def respond(query, params):
        if "FROM project_memory AS record" in query:
            if "record.id = ANY" in query:
                return [
                    (12, "authoritative decision", NOW, 42, "approved"),
                    (13, "draft decision", NOW, None, "draft"),
                ]
            return [
                (12, "stale search text", NOW, 41, 0.9),
                (13, "draft candidate", NOW, None, 0.8),
                (14, "archived before reload", NOW, None, 0.7),
            ]
        return []

    database = Database(respond)
    page = adapter(database, scope).search_decisions("celestin", "decision", 5, relax=False)
    assert [hit.record_id for hit in page.matches] == ["12", "13"]
    assert page.matches[0].content == "authoritative decision"
    assert page.matches[0].source_id == "42"
    assert page.matches[0].status == "approved"
    assert page.matches[0].timestamp == "2026-10-17T03:30:00.000000Z"
    assert page.matches[0].score == -0.9
    assert page.matches[1].source_id is None and page.matches[1].status == "draft"
    assert len(database.connections) == 1
    assert len(data_calls(database)) == 2
    for query, params, _ in data_calls(database):
        assert_query_is_scoped(query, params)
    assert all(cursor.closed for cursor in database.cursors)


def test_authoritative_loads_validate_ids_and_filter_project_workspace_archives(scope):
    def respond(query, params):
        if "FROM chat_messages AS record" in query:
            return [(5, "thread", "message", NOW)]
        if "FROM project_memory AS record" in query:
            return [(6, "decision", NOW, 5, "deprecated")]
        return []

    database = Database(respond)
    data = adapter(database, scope)
    messages = data.load_messages("celestin", ["5", "bad", "0", "05"])
    decisions = data.load_decisions("celestin", ["6"])
    assert messages["5"].conversation_id == "thread"
    assert messages["5"].content == "message"
    assert decisions["6"].status == "deprecated"
    assert decisions["6"].source_message_id == "5"
    assert len(data_calls(database)) == 2
    for query, params, _ in data_calls(database):
        assert_query_is_scoped(query, params)
        assert "ANY(%s::bigint[])" in query
    assert data_calls(database)[0][1][-1] == [5]


def test_shared_retrieval_service_accepts_postgres_adapter_records(scope):
    def respond(query, params):
        if "FROM chat_messages AS record" in query:
            return [(21, "The hosted adapter keeps tenant scope explicit.", NOW, "conversation", 0.2)]
        return []

    data = adapter(Database(respond), scope)
    service = RetrievalService(retrieval_data=data, fts_relax=False)
    result = service.search_chat_history("celestin", "tenant scope")
    bundle = service.retrieve_context("celestin", "tenant scope", 1000)

    assert [item.message_id for item in result] == ["21"]
    assert [item.record_id for item in bundle.items] == ["21"]
    assert bundle.items[0].content.startswith("The hosted adapter")


def test_shared_service_keeps_approved_only_decision_policy(scope):
    def respond(query, params):
        if "FROM project_memory AS record" not in query:
            return []
        if "record.id = ANY" in query:
            return [
                (31, "approved", NOW, 51, "approved"),
                (32, "draft", NOW, None, "draft"),
                (33, "deprecated", NOW, None, "deprecated"),
            ]
        return [
            (31, "approved", NOW, 51, 0.9),
            (32, "draft", NOW, None, 0.8),
            (33, "deprecated", NOW, None, 0.7),
        ]

    service = RetrievalService(retrieval_data=adapter(Database(respond), scope))
    bundle = service.retrieve_context("celestin", "decision", 1000)
    assert [item.record_id for item in bundle.items] == ["decision:31"]
    assert bundle.items[0].status == "approved"


def test_scope_is_immutable_and_rejects_invalid_coordinates():
    scope = PostgresRetrievalScope(str(WORKSPACE), str(PROJECT), " celestin ")
    assert scope.project_key == "celestin"
    with pytest.raises(AttributeError):
        scope.project_id = OTHER_PROJECT
    with pytest.raises(ValueError, match="valid UUIDs"):
        PostgresRetrievalScope("not-a-uuid", PROJECT, "celestin")


def test_adapter_constructor_from_dsn_is_lazy(scope):
    adapter_from_dsn = PostgresRetrievalData.from_dsn(
        "host=localhost dbname=unused user=unused", scope=scope
    )
    assert isinstance(adapter_from_dsn, PostgresRetrievalData)
