"""Request-bound PostgreSQL adapters share one authorized transaction."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import UUID

import pytest

from orbit.mcp_server.hosted_scope import (
    HostedAccessDenied,
    HostedAuthorizationUnavailable,
    HostedSourceMessageNotFound,
    VerifiedPrincipal,
)
from orbit.mcp_server.hosted_identity import HostedRequestBoundary
from orbit.evidence.postgres import PostgresEvidenceLinkedMemoryStore
from orbit.evidence.model import (
    EvidenceRecord,
    EvidenceType,
    KnowledgeRecord,
    KnowledgeType,
    ReferenceStatus,
)
from orbit.postgres.authorization import (
    PostgresHostedAuthorizationSessionFactory,
    ProjectScopedPostgresHostedAuthorizationSessionFactory,
)
from orbit.postgres.unit_of_work import PostgresRequestUnitOfWork
from orbit.retrieval.postgres_data import PostgresRetrievalData
from orbit.storage.postgres import PostgresMemoryStore


WORKSPACE = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
PROJECT = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
USER = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
MESSAGE_ID = 41
NOW = datetime(2026, 10, 17, 6, 0, tzinfo=timezone.utc)


class Result:
    def __init__(self, row=None, rows=()):
        self.row = row
        self.rows = list(rows)
        self.closed = False

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows

    def close(self):
        self.closed = True


class Connection:
    def __init__(self, *, project_archived=None, workspace_archived=None,
                 grant=(True, True, None), message_id=MESSAGE_ID,
                 capabilities=(False, False, False, False),
                 fail_on=None, scope_matches=True):
        self.project_archived = project_archived
        self.workspace_archived = workspace_archived
        self.grant = grant
        self.capabilities = capabilities
        self.message_id = message_id
        self.fail_on = fail_on
        self.scope_matches = scope_matches
        self.queries = []
        self.cursors = []
        self.commits = 0
        self.rollbacks = 0
        self.close_calls = 0
        self.closed = False
        self.transaction_depth = 0
        self.scope_check_transaction_depth = None
        self.scope_cursor = None

    def execute(self, query, params=()):
        sql = " ".join(query.split())
        self.queries.append((sql, tuple(params)))
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError("private database detail")

        if sql.startswith((
            "SELECT public.celestin_lock_authorization_request",
            "SELECT public.celestin_lock_project_authorization_request",
        )):
            row, rows = None, ()
        elif sql.startswith("SELECT public.celestin_request_scope_matches"):
            self.scope_check_transaction_depth = self.transaction_depth
            row, rows = (self.scope_matches,), ()
        elif sql.startswith("SELECT set_config('statement_timeout'"):
            self.statement_timeout_transaction_depth = self.transaction_depth
            row, rows = (params[0],), ()
        elif sql.startswith("SELECT id FROM celestin_workspaces"):
            row = (WORKSPACE,)
            rows = ()
        elif sql.startswith("SELECT id, status FROM celestin_users"):
            row, rows = (USER, "active"), ()
        elif sql.startswith("SELECT archived_at FROM celestin_workspaces"):
            row, rows = (self.workspace_archived,), ()
        elif sql.startswith("SELECT id, legacy_project_key, archived_at"):
            row, rows = (PROJECT, "demo", self.project_archived), ()
        elif sql.startswith("SELECT status FROM celestin_workspace_memberships"):
            row, rows = ("active",), ()
        elif sql.startswith("SELECT can_read, can_write, can_approve_candidates"):
            row, rows = (*self.grant[:2], *self.capabilities, self.grant[2]), ()
        elif sql.startswith("SELECT can_read, can_write, can_convert_memory"):
            _, can_convert, can_history, can_audit = self.capabilities
            row, rows = (
                self.grant[0], self.grant[1], can_convert, can_history,
                can_audit, self.grant[2],
            ), ()
        elif sql.startswith("SELECT id FROM chat_messages"):
            row, rows = ((self.message_id,) if self.message_id is not None else None), ()
        elif sql.startswith("INSERT INTO chat_messages"):
            row, rows = (81, NOW), ()
        elif sql.startswith("SELECT 1 FROM chat_messages"):
            row, rows = ((1,) if self.message_id is not None else None), ()
        elif sql.startswith("INSERT INTO project_memory"):
            row, rows = (91, "draft", MESSAGE_ID, NOW, NOW, "topic", "value"), ()
        elif sql.startswith("INSERT INTO celestin_knowledge_records"):
            row, rows = None, ()
        elif sql.startswith("INSERT INTO celestin_evidence_records"):
            row, rows = None, ()
        elif sql.startswith("SELECT knowledge_id, knowledge_type"):
            row, rows = None, ()
        elif "FROM chat_messages AS record" in sql:
            row, rows = None, [(81, "synthetic memory", NOW, "conversation", 0.7)]
        else:
            raise AssertionError(f"unexpected SQL: {sql}")

        cursor = Result(row, rows)
        self.cursors.append(cursor)
        if sql.startswith("SELECT public.celestin_request_scope_matches"):
            self.scope_cursor = cursor
        return cursor

    @contextmanager
    def transaction(self):
        outermost = self.transaction_depth == 0
        self.transaction_depth += 1
        try:
            yield
        except Exception:
            if outermost:
                self.rollbacks += 1
            raise
        else:
            if outermost:
                self.commits += 1
        finally:
            self.transaction_depth -= 1

    def close(self):
        self.close_calls += 1
        self.closed = True


class WorkspaceBinding:
    def workspace_for(self, principal, requested_workspace_id=None):
        return requested_workspace_id or WORKSPACE


class StatusProvider:
    def is_active(self, principal):
        return True


def build_request(connection):
    connect_calls = []

    def connect():
        connect_calls.append(connection)
        return connection

    factory = PostgresHostedAuthorizationSessionFactory(connect)
    return factory, connect_calls


def build_project_scoped_request(control_connection, project_connection, events=None):
    calls = []
    events = events if events is not None else []

    def connect_control():
        calls.append("control")
        events.append("connect-control")
        return control_connection

    def connect_project(scope):
        calls.append((scope.workspace_id, scope.project_id))
        events.append(("connect-project", control_connection.commits))
        return project_connection

    factory = ProjectScopedPostgresHostedAuthorizationSessionFactory(
        connect_control, connect_project
    )
    return factory, calls


def test_statement_timeout_is_transaction_local_before_authorization():
    connection = Connection()
    factory = PostgresHostedAuthorizationSessionFactory(
        lambda: connection, statement_timeout_ms=2500
    )

    with factory._transaction():
        pass

    assert connection.queries[0] == (
        "SELECT set_config('statement_timeout', %s, true)", ("2500ms",)
    )
    assert connection.statement_timeout_transaction_depth == 1
    assert connection.commits == 1 and connection.rollbacks == 0
    assert connection.close_calls == 1


@pytest.mark.parametrize("timeout", [0, -1, True, 1.5])
def test_statement_timeout_configuration_must_be_positive_integer(timeout):
    with pytest.raises(ValueError, match="positive integer"):
        PostgresHostedAuthorizationSessionFactory(lambda: Connection(), statement_timeout_ms=timeout)


class Authenticator:
    def authenticate(self, _transport_context):
        return PRINCIPAL


def request_boundary(factory):
    return HostedRequestBoundary(
        authenticator=Authenticator(),
        workspace_binding=WorkspaceBinding(),
        principal_status=StatusProvider(),
        request_executor=factory,
    )


def execute_request(factory, project, action, operation, *, source_message_id=None):
    return request_boundary(factory).execute_authorized_request(
        object(),
        project,
        action,
        operation,
        workspace_selector=str(WORKSPACE),
        source_message_id=source_message_id,
    )


PRINCIPAL = VerifiedPrincipal("issuer", "subject", "human")


def test_authorized_memory_and_retrieval_share_one_connection_and_commit_once():
    connection = Connection()
    factory, connect_calls = build_request(connection)

    def operation(request):
        scope_query, scope_params = next(
            (sql, params) for sql, params in connection.queries
            if sql.startswith("SELECT public.celestin_request_scope_matches")
        )
        assert scope_params == (str(WORKSPACE), str(PROJECT))
        assert scope_query.startswith("SELECT public.celestin_request_scope_matches")
        assert connection.scope_check_transaction_depth == 1
        assert connection.scope_cursor.closed
        assert request.memory_store._request_connection is connection
        assert request.evidence_store._connection is connection
        assert request.retrieval_data._request_connection is connection
        assert not hasattr(request, "authorization_session")
        assert not hasattr(request, "scope")
        assert not hasattr(request, "connection")

        message = request.memory_store.insert_message(
            "demo", "conversation", "user", "synthetic memory"
        )
        decision = request.memory_store.save_decision(
            "demo", "topic", "value", message.message_id
        )
        results = request.retrieval_service.search_chat_history(
            "demo", "synthetic memory", 5
        )
        assert message.message_id == "81"
        assert decision.decision_id == "91"
        assert results[0].message_id == "81"
        assert not connection.commits and not connection.rollbacks
        assert not connection.closed and connection.close_calls == 0
        return "complete"

    result = execute_request(factory, "demo", "write", operation)

    assert result == "complete"
    assert connect_calls == [connection]
    assert connection.commits == 1
    assert connection.rollbacks == 0
    assert connection.close_calls == 1 and connection.closed
    assert any("INSERT INTO chat_messages" in sql for sql, _ in connection.queries)
    assert any("INSERT INTO project_memory" in sql for sql, _ in connection.queries)
    assert any("FROM chat_messages AS record" in sql for sql, _ in connection.queries)
    assert all(
        "record.workspace_id = %s::uuid" in sql
        and "record.project_id = %s::uuid" in sql
        for sql, _ in connection.queries
        if "FROM chat_messages AS record" in sql
    )


def test_project_scoped_factory_authorizes_before_pool_selection_and_rechecks_on_project_login():
    control = Connection()
    project = Connection()
    events = []
    factory, calls = build_project_scoped_request(control, project, events)

    result = request_boundary(factory).execute_authorized_request(
        object(), "demo", "write",
        lambda request: (
            request.memory_store._request_connection is project,
            request.retrieval_data._request_connection is project,
            request.evidence_store._connection is project,
        ),
        workspace_selector=str(WORKSPACE),
    )

    assert result == (True, True, True)
    assert calls == ["control", (WORKSPACE, PROJECT)]
    assert events == ["connect-control", ("connect-project", 1)]
    assert control.commits == 1 and control.rollbacks == 0 and control.close_calls == 1
    assert project.commits == 1 and project.rollbacks == 0 and project.close_calls == 1
    assert any(
        sql.startswith("SELECT public.celestin_request_scope_matches")
        for sql, _ in project.queries
    )
    project_lock_queries = [
        (sql, params) for sql, params in project.queries
        if sql.startswith("SELECT public.celestin_lock_project_authorization_request")
    ]
    assert project_lock_queries == [
        ("SELECT public.celestin_lock_project_authorization_request(%s)", (WORKSPACE,))
    ]
    assert not any(
        sql.startswith("SELECT public.celestin_lock_authorization_request")
        for sql, _ in project.queries
    )
    assert any(
        sql.startswith("SELECT public.celestin_lock_authorization_request")
        and params == (WORKSPACE, PRINCIPAL.kind, PRINCIPAL.issuer, PRINCIPAL.subject)
        for sql, params in control.queries
    )


def test_project_pool_is_never_selected_after_preliminary_denial():
    control = Connection(grant=(False, False, None))
    project = Connection()
    factory, calls = build_project_scoped_request(control, project)
    called = False

    def operation(_request):
        nonlocal called
        called = True

    with pytest.raises(HostedAccessDenied):
        request_boundary(factory).execute_authorized_request(
            object(), "demo", "read", operation,
            workspace_selector=str(WORKSPACE),
        )

    assert calls == ["control"]
    assert not called
    assert control.rollbacks == 1 and control.closed
    assert not project.queries and not project.close_calls


def test_project_transaction_rechecks_revocation_before_operation():
    control = Connection()
    project = Connection(grant=(False, False, None))
    factory, calls = build_project_scoped_request(control, project)
    called = False

    def operation(_request):
        nonlocal called
        called = True

    with pytest.raises(HostedAccessDenied):
        request_boundary(factory).execute_authorized_request(
            object(), "demo", "read", operation,
            workspace_selector=str(WORKSPACE),
        )

    assert calls == ["control", (WORKSPACE, PROJECT)]
    assert not called
    assert control.commits == 1 and control.close_calls == 1
    assert project.rollbacks == 1 and project.close_calls == 1


def test_project_login_scope_mapping_mismatch_denies_before_handler():
    control = Connection()
    project = Connection(scope_matches=False)
    factory, calls = build_project_scoped_request(control, project)
    called = False

    def operation(_request):
        nonlocal called
        called = True

    with pytest.raises(HostedAuthorizationUnavailable, match="database identity"):
        request_boundary(factory).execute_authorized_request(
            object(), "demo", "read", operation,
            workspace_selector=str(WORKSPACE),
        )

    assert calls == ["control", (WORKSPACE, PROJECT)]
    assert not called
    assert control.commits == 1 and project.rollbacks == 1
    assert project.close_calls == 1


def test_evidence_store_is_bound_to_resolver_scope_and_same_authorized_session():
    connection = Connection()
    factory, connect_calls = build_request(connection)

    def operation(request):
        store = request.evidence_store
        assert isinstance(store, PostgresEvidenceLinkedMemoryStore)
        assert store._scope.project_id == PROJECT
        assert store._scope.workspace_id == WORKSPACE
        assert store._scope.project_key == "demo"
        assert store._connection is connection
        assert store.get_knowledge("demo", "missing") is None
        with pytest.raises(HostedAccessDenied):
            store.get_knowledge("another-project", "missing")

    execute_request(factory, "demo", "read", operation)
    assert connect_calls == [connection]
    assert connection.commits == 1 and connection.rollbacks == 0
    assert connection.close_calls == 1 and connection.closed
    query, params = next(
        (sql, params) for sql, params in connection.queries
        if sql.startswith("SELECT knowledge_id, knowledge_type")
    )
    assert "workspace_id = %s AND project_id = %s" in query
    assert params == (WORKSPACE, PROJECT, "missing")


def test_authorized_evidence_write_uses_the_request_connection_and_outer_transaction():
    connection = Connection()
    factory, connect_calls = build_request(connection)
    knowledge = KnowledgeRecord(
        knowledge_id="knowledge-uow",
        project="demo",
        knowledge_type=KnowledgeType.DISCOVERY,
        claim="Scoped write.",
        created_at="2026-10-17T06:00:00.000000Z",
        updated_at="2026-10-17T06:00:00.000000Z",
    )
    evidence = EvidenceRecord(
        evidence_id="evidence-uow",
        project="demo",
        evidence_type=EvidenceType.DOCUMENT,
        source_id="doc-uow",
        locator=None,
        revision=None,
        content_hash=None,
        captured_at="2026-10-17T06:00:00.000000Z",
        reference_status=ReferenceStatus.UNRESOLVED,
    )

    def operation(request):
        request.evidence_store.create_knowledge(knowledge, (evidence,))
        assert connection.commits == 0 and connection.rollbacks == 0
        assert connection.transaction_depth == 1
        assert connection.close_calls == 0

    execute_request(factory, "demo", "write", operation)

    assert connect_calls == [connection]
    assert connection.commits == 1 and connection.rollbacks == 0
    assert connection.close_calls == 1 and connection.closed
    scoped_inserts = [
        params for sql, params in connection.queries
        if sql.startswith(
            (
                "INSERT INTO celestin_knowledge_records",
                "INSERT INTO celestin_evidence_records",
            )
        )
    ]
    assert len(scoped_inserts) == 2
    assert all(params[:2] == (WORKSPACE, PROJECT) for params in scoped_inserts)


def test_later_operation_failure_rolls_back_prior_write_and_closes_without_commit():
    connection = Connection(fail_on="FROM chat_messages AS record")
    factory, connect_calls = build_request(connection)

    with pytest.raises(HostedAuthorizationUnavailable) as error:
        execute_request(
            factory,
            "demo",
            "write",
            lambda request: (
                request.memory_store.insert_message(
                    "demo", "conversation", "user", "synthetic"
                ),
                request.retrieval_service.search_chat_history("demo", "synthetic", 5),
            ),
        )

    assert "private database detail" not in str(error.value)
    assert connect_calls == [connection]
    assert connection.commits == 0
    assert connection.rollbacks == 1
    assert connection.close_calls == 1 and connection.closed
    assert any("INSERT INTO chat_messages" in sql for sql, _ in connection.queries)


def test_read_only_scope_cannot_write_and_project_mismatch_does_not_query():
    connection = Connection(grant=(True, False, None))
    factory, _ = build_request(connection)

    def operation(request):
        before = len(connection.queries)
        with pytest.raises(HostedAccessDenied, match="access denied"):
            request.memory_store.insert_message(
                "demo", "conversation", "user", "synthetic"
            )
        with pytest.raises(HostedAccessDenied, match="access denied"):
            request.memory_store.insert_message(
                "other-project", "conversation", "user", "synthetic"
            )
        with pytest.raises(ValueError, match="trusted retrieval scope"):
            request.retrieval_data.search_messages(
                "other-project", "query", 5, relax=False
            )
        assert len(connection.queries) == before

    execute_request(factory, "demo", "read", operation)
    assert not any("INSERT INTO chat_messages" in sql for sql, _ in connection.queries)
    assert connection.commits == 1 and connection.close_calls == 1


@pytest.mark.parametrize(
    "connection",
    [Connection(project_archived=NOW), Connection(workspace_archived=NOW),
     Connection(message_id=None)],
    ids=["archived-project", "archived-workspace", "missing-source"],
)
def test_archived_scope_or_missing_source_denies_before_operation(connection):
    factory, _ = build_request(connection)
    called = False

    def operation(request):
        nonlocal called
        called = True

    kwargs = {"source_message_id": str(MESSAGE_ID)} if connection.message_id is None else {}
    with pytest.raises((
        HostedAccessDenied,
        HostedAuthorizationUnavailable,
        HostedSourceMessageNotFound,
    )):
        execute_request(factory, "demo", "write", operation, **kwargs)
    assert not called
    assert connection.rollbacks == 1 and connection.close_calls == 1
    assert connection.commits == 0
    source_query_indices = [
        i for i, (sql, _params) in enumerate(connection.queries)
        if sql.startswith("SELECT id FROM chat_messages")
    ]
    if source_query_indices:
        scope_query_index = next(
            i for i, (sql, _params) in enumerate(connection.queries)
            if sql.startswith("SELECT public.celestin_request_scope_matches")
        )
        assert scope_query_index < source_query_indices[0]
    else:
        # Authorization may deny archived resources before any data-scope
        # binding is needed; no protected source read is made in that case.
        assert not any(
            sql.startswith("SELECT public.celestin_request_scope_matches")
            for sql, _params in connection.queries
        )
    assert not called


def test_source_validation_uses_workspace_and_project_ids_before_operation():
    connection = Connection()
    factory, _ = build_request(connection)
    called = False

    def operation(request):
        nonlocal called
        called = True

    execute_request(
        factory,
        "demo",
        "write",
        operation,
        source_message_id=str(MESSAGE_ID),
    )
    source_query, params = next(
        (sql, params) for sql, params in connection.queries
        if sql.startswith("SELECT id FROM chat_messages")
    )
    assert "workspace_id = %s" in source_query and "project_id = %s" in source_query
    assert params == (WORKSPACE, PROJECT, MESSAGE_ID)
    scope_query_index = next(
        i for i, (sql, _params) in enumerate(connection.queries)
        if sql.startswith("SELECT public.celestin_request_scope_matches")
    )
    source_query_index = next(
        i for i, (sql, _params) in enumerate(connection.queries)
        if sql.startswith("SELECT id FROM chat_messages")
    )
    assert scope_query_index < source_query_index
    assert called


def test_request_uow_cannot_be_constructed_with_standalone_adapters(monkeypatch):
    connection = Connection()
    factory, _connect_calls = build_request(connection)

    def forbidden(*_args, **_kwargs):
        pytest.fail("standalone PostgreSQL adapters must not be selected by request composition")

    monkeypatch.setattr(PostgresMemoryStore, "from_dsn", classmethod(forbidden))
    monkeypatch.setattr(PostgresRetrievalData, "from_dsn", classmethod(forbidden))
    with pytest.raises(TypeError):
        PostgresRequestUnitOfWork(
            object(), object(), PostgresMemoryStore, PostgresRetrievalData,
            object(), object(),
        )

    result = execute_request(factory, "demo", "read", lambda request: request.retrieval_data)
    assert isinstance(result, PostgresRetrievalData)
    assert connection.commits == 1 and connection.close_calls == 1


def test_request_factory_has_no_public_executor_and_missing_auth_never_connects():
    connection = Connection()
    factory, connect_calls = build_request(connection)
    assert not hasattr(factory, "execute_authorized")
    assert not hasattr(factory, "execute_authorized_request")
    assert not hasattr(factory, "transaction")

    boundary = HostedRequestBoundary(
        authenticator=None,
        workspace_binding=WorkspaceBinding(),
        principal_status=StatusProvider(),
        request_executor=factory,
    )
    with pytest.raises(HostedAuthorizationUnavailable, match="authentication is not configured"):
        boundary.execute_authorized_request(
            object(), "demo", "read", lambda _request: pytest.fail("must not run"),
            workspace_selector=str(WORKSPACE),
        )
    assert connect_calls == []
    assert connection.queries == []
