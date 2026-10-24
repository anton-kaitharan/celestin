"""Scripted PostgreSQL authorization contract tests; no server is contacted."""

from __future__ import annotations

from contextlib import contextmanager
from uuid import UUID

import pytest

from orbit.mcp_server.hosted_scope import (
    HostedAccessDenied,
    HostedAuthorizationUnavailable,
    HostedSourceMessageNotFound,
    HostedScopeResolver,
    ResolvedProjectScope,
    VerifiedPrincipal,
)
from orbit.postgres.authorization import PostgresHostedAuthorizationSessionFactory


WORKSPACE = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
PROJECT = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
USER = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
SERVICE = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
MESSAGE = 41


class FakeConnection:
    def __init__(self, *, kind="human", identity_status="active", project_archived=None,
                 workspace_archived=None, membership="active", grant=(True, True, None),
                 capabilities=(False, False, False, False),
                 message_id=MESSAGE, fail_query=None, fail_commit=False,
                 identity_missing=False, workspace_missing=False, fail_close=False):
        self.kind = kind
        self.identity_status = identity_status
        self.project_archived = project_archived
        self.workspace_archived = workspace_archived
        self.membership = membership
        self.grant = grant
        self.capabilities = capabilities
        self.message_id = message_id
        self.fail_query = fail_query
        self.fail_commit = fail_commit
        self.identity_missing = identity_missing
        self.workspace_missing = workspace_missing
        self.fail_close = fail_close
        self.queries = []
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def execute(self, query, params=()):
        normalized = " ".join(query.split())
        self.queries.append((normalized, params))
        if self.fail_query and self.fail_query in normalized:
            raise RuntimeError("secret database detail")
        if normalized.startswith("SELECT public.celestin_lock_authorization_request"):
            return Result(None)
        if normalized.startswith("SELECT public.celestin_request_scope_matches"):
            return Result((True,))
        if normalized.startswith("SELECT id FROM celestin_workspaces"):
            return Result(None if self.workspace_missing else (WORKSPACE,))
        if normalized.startswith("SELECT id, status FROM celestin_users"):
            return Result(None if self.identity_missing else (USER, self.identity_status))
        if normalized.startswith("SELECT id, status FROM celestin_service_identities"):
            return Result(None if self.identity_missing else (SERVICE, self.identity_status))
        if normalized.startswith("SELECT archived_at FROM celestin_workspaces"):
            return Result((self.workspace_archived,))
        if normalized.startswith("SELECT id, legacy_project_key, archived_at"):
            return Result((PROJECT, "demo", self.project_archived))
        if normalized.startswith("SELECT status FROM celestin_workspace_memberships"):
            return Result((self.membership,) if self.membership is not None else None)
        if normalized.startswith("SELECT can_read, can_write, can_approve_candidates"):
            if self.grant is None:
                return Result(None)
            return Result(
                (
                    self.grant[0], self.grant[1], *self.capabilities, self.grant[2]
                )
            )
        if normalized.startswith("SELECT can_read, can_write, can_convert_memory"):
            if self.grant is None:
                return Result(None)
            _, can_convert, can_history, can_audit = self.capabilities
            return Result(
                (
                    self.grant[0], self.grant[1], can_convert, can_history,
                    can_audit, self.grant[2],
                )
            )
        if normalized.startswith("SELECT id FROM chat_messages"):
            return Result((self.message_id,) if self.message_id is not None else None)
        raise AssertionError(f"unexpected SQL in test: {normalized}")

    @contextmanager
    def transaction(self):
        try:
            yield
        except Exception:
            self.rolled_back = True
            raise
        else:
            if self.fail_commit:
                self.rolled_back = True
                raise RuntimeError("secret commit failure")
            self.committed = True

    def close(self):
        self.closed = True
        if self.fail_close:
            raise RuntimeError("secret close failure")


class Result:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class WorkspaceBinding:
    def workspace_for(self, principal, requested_workspace_id=None):
        return requested_workspace_id or WORKSPACE


class StatusFallback:
    def __init__(self, active=True):
        self.active = active
        self.calls = 0

    def is_active(self, principal):
        self.calls += 1
        return self.active


def build_resolver():
    return HostedScopeResolver(WorkspaceBinding(), StatusFallback())


def factory_for(connection):
    calls = []

    def connect():
        calls.append(connection)
        return connection

    return PostgresHostedAuthorizationSessionFactory(connect), calls


@pytest.mark.parametrize("kind,expected_table,expected_id", [
    ("human", "celestin_users", USER),
    ("service", "celestin_service_identities", SERVICE),
])
def test_identity_mapping_is_kind_specific_workspace_scoped_and_locked(kind, expected_table, expected_id):
    connection = FakeConnection(kind=kind)
    factory, calls = factory_for(connection)
    assert calls == []  # construction must be inert
    with factory._transaction() as session:
        principal = VerifiedPrincipal("issuer", "subject", kind)
        assert session.is_principal_active(principal, WORKSPACE)
        sql, params = connection.queries[-1]
        assert expected_table in sql
        assert "FOR SHARE" not in sql
        assert any("celestin_lock_authorization_request" in query for query, _ in connection.queries)
        if kind == "service":
            assert "workspace_id = %s" in sql
            assert params == (WORKSPACE, "issuer", "subject")
        else:
            assert params == ("issuer", "subject")
        assert session._identities[(principal, WORKSPACE)][0] == expected_id
    assert connection.committed and connection.closed


@pytest.mark.parametrize("kind,status,missing", [
    ("human", "suspended", False),
    ("service", "revoked", False),
    ("human", "active", True),
    ("service", "active", True),
])
def test_inactive_or_unknown_identity_denies_before_project_or_grant_lookup(kind, status, missing):
    connection = FakeConnection(kind=kind, identity_status=status, identity_missing=missing)
    factory, _ = factory_for(connection)
    resolver = build_resolver()
    principal = VerifiedPrincipal("issuer", "subject", kind)
    with pytest.raises(HostedAccessDenied):
        with factory._transaction() as session:
            resolver.resolve(session, principal, "demo", "read")
    assert not any("celestin_projects" in query for query, _ in connection.queries)
    assert connection.rolled_back and connection.closed


def test_missing_workspace_binding_target_denies_before_identity_lookup():
    connection = FakeConnection(workspace_missing=True)
    factory, _ = factory_for(connection)
    principal = VerifiedPrincipal("issuer", "subject", "human")
    with pytest.raises(HostedAccessDenied):
        with factory._transaction() as session:
            build_resolver().resolve(session, principal, "demo", "read")
    assert not any("celestin_users" in query for query, _ in connection.queries)


@pytest.mark.parametrize("kind", ["human", "service"])
def test_explicit_grant_resolves_scope_and_grant_query_is_project_scoped(kind):
    connection = FakeConnection(kind=kind)
    factory, _ = factory_for(connection)
    principal = VerifiedPrincipal("issuer", "subject", kind)
    resolver = build_resolver()
    with factory._transaction() as session:
        scope = resolver.resolve(session, principal, "demo", "write")
        assert scope.project_id == PROJECT
        assert scope.workspace_id == WORKSPACE
        assert scope.permissions == frozenset({"read", "write", "memory:read"})
    grant_queries = [
        (sql, params) for sql, params in connection.queries
        if "celestin_project_memberships" in sql or "celestin_service_project_grants" in sql
    ]
    assert len(grant_queries) == 1
    assert "workspace_id = %s" in grant_queries[0][0]
    assert "project_id = %s" in grant_queries[0][0]
    assert "FOR SHARE" not in grant_queries[0][0]
    assert any("celestin_lock_authorization_request" in query for query, _ in connection.queries)
    project_query = next((sql, params) for sql, params in connection.queries if "celestin_projects" in sql)
    assert "workspace_id = %s" in project_query[0]
    assert project_query[1] == (WORKSPACE, "demo")


def test_read_only_grant_allows_read_but_denies_write():
    principal = VerifiedPrincipal("issuer", "subject", "human")
    resolver = build_resolver()
    read_factory, _ = factory_for(FakeConnection(grant=(True, False, None)))
    with read_factory._transaction() as session:
        assert resolver.resolve(session, principal, "demo", "read").permissions == frozenset(
            {"read", "memory:read"}
        )

    write_factory, _ = factory_for(FakeConnection(grant=(True, False, None)))
    with pytest.raises(HostedAccessDenied):
        with write_factory._transaction() as session:
            resolver.resolve(session, principal, "demo", "write")

    memory_read_factory, _ = factory_for(
        FakeConnection(grant=(True, False, None))
    )
    with memory_read_factory._transaction() as session:
        memory_scope = resolver.resolve(session, principal, "demo", "memory:read")
    assert "memory:read" in memory_scope.permissions
    assert "write" not in memory_scope.permissions


def test_v03_capabilities_are_explicit_default_deny_and_scope_bound():
    principal = VerifiedPrincipal("issuer", "subject", "human")
    resolver = build_resolver()
    capability_cases = (
        ("candidate:approve", (True, False, False, False)),
        ("memory:convert", (False, True, False, False)),
        ("memory:history", (False, False, True, False)),
        ("audit:read", (False, False, False, True)),
    )

    for action, capabilities in capability_cases:
        denied_factory, _ = factory_for(FakeConnection())
        with pytest.raises(HostedAccessDenied):
            with denied_factory._transaction() as session:
                resolver.resolve(session, principal, "demo", action)

        connection = FakeConnection(
            grant=(False, False, None), capabilities=capabilities
        )
        factory, _ = factory_for(connection)
        with factory._transaction() as session:
            scope = resolver.resolve(session, principal, "demo", action)
            assert scope.permissions == frozenset({action})

        grant_query = next(
            (sql, params)
            for sql, params in connection.queries
            if "celestin_project_memberships" in sql
        )
        assert "FOR SHARE" not in grant_query[0]
        assert any("celestin_lock_authorization_request" in query for query, _ in connection.queries)
        assert grant_query[1] == (WORKSPACE, PROJECT, USER)


def test_candidate_approval_is_human_only_and_convert_does_not_imply_write():
    resolver = build_resolver()
    service = VerifiedPrincipal("issuer", "subject", "service")
    service_factory, _ = factory_for(
        FakeConnection(kind="service", capabilities=(True, True, True, True))
    )
    with pytest.raises(HostedAccessDenied):
        with service_factory._transaction() as session:
            resolver.resolve(session, service, "demo", "candidate:approve")

    principal = VerifiedPrincipal("issuer", "subject", "human")
    convert_factory, _ = factory_for(
        FakeConnection(grant=(False, False, None), capabilities=(False, True, False, False))
    )
    with convert_factory._transaction() as session:
        scope = resolver.resolve(session, principal, "demo", "memory:convert")
    assert scope.permissions == frozenset({"memory:convert"})
    assert "read" not in scope.permissions
    assert "write" not in scope.permissions


def test_missing_membership_grant_archived_resource_and_write_without_read_deny():
    principal = VerifiedPrincipal("issuer", "subject", "human")
    for connection in (
        FakeConnection(membership=None),
        FakeConnection(membership="suspended"),
        FakeConnection(membership="invited"),
        FakeConnection(grant=None),
        FakeConnection(grant=(True, True, "2026-01-01")),
        FakeConnection(project_archived="2026-01-01"),
        FakeConnection(workspace_archived="2026-01-01"),
        FakeConnection(grant=(False, True, None)),
        FakeConnection(grant=(False, False, None)),
    ):
        factory, _ = factory_for(connection)
        with pytest.raises(HostedAccessDenied):
            with factory._transaction() as session:
                build_resolver().resolve(session, principal, "demo", "read")


def test_source_message_lookup_constrains_workspace_project_under_request_lock():
    connection = FakeConnection()
    factory, _ = factory_for(connection)
    principal = VerifiedPrincipal("issuer", "subject", "human")
    resolver = build_resolver()
    with factory._transaction() as session:
        scope = resolver.resolve(session, principal, "demo", "write")
        reference = resolver.require_source_message(session, scope, str(MESSAGE))
        assert reference.message_id == str(MESSAGE)
    sql, params = next((sql, params) for sql, params in connection.queries if "chat_messages" in sql)
    assert "workspace_id = %s" in sql and "project_id = %s" in sql
    assert "FOR KEY SHARE" not in sql
    assert any("celestin_lock_authorization_request" in query for query, _ in connection.queries)
    assert params == (WORKSPACE, PROJECT, MESSAGE)


def test_unknown_or_cross_scope_source_reference_is_not_found():
    connection = FakeConnection(message_id=None)
    factory, _ = factory_for(connection)
    principal = VerifiedPrincipal("issuer", "subject", "human")
    with pytest.raises(HostedSourceMessageNotFound, match="source message not found"):
        with factory._transaction() as session:
            scope = build_resolver().resolve(session, principal, "demo", "write")
            build_resolver().require_source_message(session, scope, str(MESSAGE))
    assert connection.rolled_back and connection.closed


def test_query_errors_are_redacted_and_transaction_rolls_back():
    connection = FakeConnection(fail_query="celestin_users")
    factory, _ = factory_for(connection)
    with pytest.raises(HostedAuthorizationUnavailable) as error:
        with factory._transaction() as session:
            session.is_principal_active(VerifiedPrincipal("issuer", "subject", "human"), WORKSPACE)
    assert "secret database detail" not in str(error.value)
    assert connection.rolled_back and connection.closed


def test_authorized_operation_result_is_returned_only_after_commit():
    connection = FakeConnection()
    factory, _ = factory_for(connection)
    events = []

    def operation(session, scope):
        events.append("operation")
        assert session._connection_for_adapters is connection
        assert not connection.committed
        return "done"

    result = factory._execute_authorized(
        build_resolver(),
        VerifiedPrincipal("issuer", "subject", "human"),
        "demo",
        "read",
        operation,
    )
    events.append("returned")
    assert result == "done"
    assert events == ["operation", "returned"]
    assert connection.committed and connection.closed


def test_operation_failure_rolls_back_and_commit_failure_never_returns_success():
    connection = FakeConnection()
    factory, _ = factory_for(connection)
    with pytest.raises(HostedAuthorizationUnavailable):
        factory._execute_authorized(
            build_resolver(), VerifiedPrincipal("issuer", "subject", "human"), "demo", "read",
            lambda session, scope: (_ for _ in ()).throw(RuntimeError("internal")),
        )
    assert connection.rolled_back and connection.closed

    commit_connection = FakeConnection(fail_commit=True)
    commit_factory, _ = factory_for(commit_connection)
    with pytest.raises(HostedAuthorizationUnavailable):
        commit_factory._execute_authorized(
            build_resolver(), VerifiedPrincipal("issuer", "subject", "human"), "demo", "read",
            lambda session, scope: "must not escape before commit",
        )
    assert commit_connection.rolled_back and commit_connection.closed


def test_close_failure_is_redacted_and_does_not_report_success():
    connection = FakeConnection(fail_close=True)
    factory, _ = factory_for(connection)
    with pytest.raises(HostedAuthorizationUnavailable) as error:
        factory._execute_authorized(
            build_resolver(), VerifiedPrincipal("issuer", "subject", "human"), "demo", "read",
            lambda session, scope: "not returned",
        )
    assert "secret close failure" not in str(error.value)
    assert connection.committed and connection.closed
