"""Real-server authorization and transaction tests; never run by default."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from threading import Event
from time import monotonic, sleep
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from orbit.evidence.model import EvidenceSource, EvidenceType, KnowledgeType, ReferenceStatus
from orbit.mcp_server.hosted_scope import (
    HostedAccessDenied,
    HostedAuthorizationUnavailable,
    HostedScopeResolver,
    HostedSourceMessageNotFound,
    VerifiedPrincipal,
)
from orbit.postgres.authorization import (
    PostgresHostedAuthorizationSessionFactory,
    ProjectScopedPostgresHostedAuthorizationSessionFactory,
)
from orbit.mcp_server.hosted_server import (
    AUTHORIZATION_ADMIN_ROLE,
    AUTHORIZATION_READER_ROLE,
    PRIVILEGED_GROUP_ROLES,
    PROTECTED_TABLES,
    _advisory_lock_privilege_query,
    _assert_expected_policy_catalog,
    _assert_expected_trigger_catalog,
    _assert_safe_authorization_admin,
    _assert_evidence_runtime_privilege_profile,
    _assert_safe_project_runtime,
    _create_pool,
    _privileged_role_membership_query,
    _validate_advisory_lock_privilege_catalog,
    _validate_privileged_role_membership_catalog,
    EVIDENCE_RUNTIME_TABLES,
)


pytestmark = pytest.mark.postgres_integration
_LOCK_WAIT_SECONDS = 8.0


class _ExistingConnectionPool:
    """Adapt a live fixture connection to the read-only readiness probe API."""

    def __init__(self, connection):
        self._connection = connection

    @contextmanager
    def connection(self, *, timeout):
        yield self._connection


class FixedWorkspaceBinding:
    """Test-only stand-in for a trusted server-side credential binding."""

    def __init__(self, workspace_id: UUID) -> None:
        self.workspace_id = workspace_id

    def workspace_for(
        self, _principal: VerifiedPrincipal, requested_workspace_id: UUID | None = None
    ) -> UUID | None:
        if requested_workspace_id is not None and requested_workspace_id != self.workspace_id:
            return None
        return self.workspace_id


class ActiveStatusFallback:
    """PostgreSQL session performs the authoritative status check in these tests."""

    def is_active(self, _principal: VerifiedPrincipal) -> bool:
        return True


@dataclass(frozen=True)
class TenantFixture:
    suffix: str
    workspace_a: UUID
    workspace_b: UUID
    project_a: UUID
    project_b: UUID
    human_id: UUID
    default_user_id: UUID
    contender_user_id: UUID
    service_id: UUID
    message_a: int
    message_b: int
    memory_a: int
    memory_b: int
    project_key: str
    issuer: str
    human_subject: str
    service_subject: str

    def human(self) -> VerifiedPrincipal:
        return VerifiedPrincipal(self.issuer, self.human_subject, "human")

    def service(self) -> VerifiedPrincipal:
        return VerifiedPrincipal(self.issuer, self.service_subject, "service")

    def resolver(self, workspace_id: UUID | None = None) -> HostedScopeResolver:
        return HostedScopeResolver(
            FixedWorkspaceBinding(workspace_id or self.workspace_a),
            ActiveStatusFallback(),
        )


def _insert_message(connection, workspace_id: UUID, project_id: UUID, conversation: str) -> int:
    row = connection.execute(
        """
        INSERT INTO chat_messages
            (workspace_id, project_id, conversation_id, role, content, created_at)
        VALUES (%s, %s, %s, 'user', %s, CURRENT_TIMESTAMP)
        RETURNING id
        """,
        (workspace_id, project_id, conversation, f"synthetic live fixture {conversation}"),
    ).fetchone()
    return int(row[0])


@pytest.fixture
def tenant_fixture(postgres_connections):
    """Create synthetic tenants; rows stay isolated in the one-shot test DB."""
    pg = postgres_connections
    suffix = uuid4().hex
    ids = {
        "workspace_a": uuid4(),
        "workspace_b": uuid4(),
        "project_a": uuid4(),
        "project_b": uuid4(),
        "human_id": uuid4(),
        "default_user_id": uuid4(),
        "contender_user_id": uuid4(),
        "service_id": uuid4(),
    }
    project_key = f"same-key-{suffix}"
    issuer = f"https://celestin-test.invalid/{suffix}"
    human_subject = f"human-{suffix}"
    service_subject = f"service-{suffix}"

    with pg.connection() as connection, connection.transaction():
        connection.execute(
            "INSERT INTO celestin_workspaces (id, slug, name) VALUES (%s, %s, %s)",
            (ids["workspace_a"], f"workspace-a-{suffix}", f"Synthetic A {suffix}"),
        )
        connection.execute(
            "INSERT INTO celestin_workspaces (id, slug, name) VALUES (%s, %s, %s)",
            (ids["workspace_b"], f"workspace-b-{suffix}", f"Synthetic B {suffix}"),
        )
        for user_id, subject, status_sql in (
            (ids["human_id"], human_subject, "'active'"),
            (ids["default_user_id"], f"default-{suffix}", None),
            (ids["contender_user_id"], f"contender-{suffix}", "'active'"),
        ):
            if status_sql is None:
                connection.execute(
                    "INSERT INTO celestin_users (id, issuer, subject) VALUES (%s, %s, %s)",
                    (user_id, issuer, subject),
                )
            else:
                connection.execute(
                    "INSERT INTO celestin_users (id, issuer, subject, status) "
                    "VALUES (%s, %s, %s, %s)",
                    (user_id, issuer, subject, status_sql.strip("'")),
                )

        for workspace_id in (ids["workspace_a"], ids["workspace_b"]):
            connection.execute(
                """INSERT INTO celestin_workspace_memberships
                       (workspace_id, user_id, role, status)
                   VALUES (%s, %s, 'owner', 'active')""",
                (workspace_id, ids["human_id"]),
            )
        connection.execute(
            """INSERT INTO celestin_workspace_memberships
                   (workspace_id, user_id, role, status)
               VALUES (%s, %s, 'member', 'active')""",
            (ids["workspace_a"], ids["contender_user_id"]),
        )

        for project_id, workspace_id, slug, legacy_key in (
            (ids["project_a"], ids["workspace_a"], f"project-a-{suffix}", project_key),
            (ids["project_b"], ids["workspace_b"], f"project-b-{suffix}", project_key),
        ):
            connection.execute(
                """INSERT INTO celestin_projects
                       (id, workspace_id, slug, legacy_project_key)
                   VALUES (%s, %s, %s, %s)""",
                (project_id, workspace_id, slug, legacy_key),
            )

        for workspace_id, project_id in (
            (ids["workspace_a"], ids["project_a"]),
            (ids["workspace_b"], ids["project_b"]),
        ):
            connection.execute(
                """INSERT INTO celestin_project_memberships
                       (workspace_id, project_id, user_id, can_read, can_write)
                   VALUES (%s, %s, %s, TRUE, TRUE)""",
                (workspace_id, project_id, ids["human_id"]),
            )

        connection.execute(
            """INSERT INTO celestin_service_identities
                   (id, workspace_id, issuer, subject, name, status)
               VALUES (%s, %s, %s, %s, %s, 'active')""",
            (ids["service_id"], ids["workspace_a"], issuer, service_subject, f"synthetic-{suffix}"),
        )
        connection.execute(
            """INSERT INTO celestin_service_project_grants
                   (workspace_id, project_id, service_identity_id, can_read, can_write)
               VALUES (%s, %s, %s, TRUE, TRUE)""",
            (ids["workspace_a"], ids["project_a"], ids["service_id"]),
        )
        connection.execute(
            """INSERT INTO celestin_private.project_runtime_scopes
                   (login_role_name, workspace_id, project_id)
               VALUES (%s, %s, %s), (%s, %s, %s)""",
            (
                pg.target.project_a_user, ids["workspace_a"], ids["project_a"],
                pg.target.project_b_user, ids["workspace_b"], ids["project_b"],
            ),
        )

    pg.bind_project_scope(ids["workspace_a"], ids["project_a"], role="project_a")
    pg.bind_project_scope(ids["workspace_b"], ids["project_b"], role="project_b")
    with pg.project_connection(ids["workspace_a"], ids["project_a"]) as connection:
        message_a = _insert_message(
            connection, ids["workspace_a"], ids["project_a"], f"a-{suffix}"
        )
        memory_a = connection.execute(
            """INSERT INTO project_memory
                   (workspace_id, project_id, key, value, status, source_message_id,
                    created_at, updated_at)
               VALUES (%s, %s, %s, %s, 'approved', %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
               RETURNING id""",
            (
                ids["workspace_a"], ids["project_a"], f"memory-{suffix}",
                f"synthetic decision {suffix}", message_a,
            ),
        ).fetchone()[0]
    with pg.project_connection(ids["workspace_b"], ids["project_b"]) as connection:
        message_b = _insert_message(
            connection, ids["workspace_b"], ids["project_b"], f"b-{suffix}"
        )
        memory_b = connection.execute(
            """INSERT INTO project_memory
                   (workspace_id, project_id, key, value, status, source_message_id,
                    created_at, updated_at)
               VALUES (%s, %s, %s, %s, 'approved', %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
               RETURNING id""",
            (
                ids["workspace_b"], ids["project_b"], f"memory-b-{suffix}",
                f"synthetic decision B {suffix}", message_b,
            ),
        ).fetchone()[0]

    fixture = TenantFixture(
        suffix=suffix,
        **ids,
        message_a=message_a,
        message_b=message_b,
        memory_a=int(memory_a),
        memory_b=int(memory_b),
        project_key=project_key,
        issuer=issuer,
        human_subject=human_subject,
        service_subject=service_subject,
    )
    try:
        yield fixture
    finally:
        with pg.connection() as connection, connection.transaction():
            connection.execute(
                "DELETE FROM celestin_private.project_runtime_scopes "
                "WHERE login_role_name = ANY(%s::name[])",
                ([pg.target.project_a_user, pg.target.project_b_user],),
            )


class _LiveProjectScopedFactory(ProjectScopedPostgresHostedAuthorizationSessionFactory):
    """Adapt existing resolver callbacks onto the production request boundary."""

    def _execute_authorized(
        self,
        resolver,
        principal,
        project_key,
        action,
        operation,
        *,
        source_message_id=None,
        requested_workspace_id=None,
    ):
        return self._execute_authorized_request(
            resolver,
            principal,
            project_key,
            action,
            lambda request: operation(
                request._authorization_session, request._scope
            ),
            source_message_id=source_message_id,
            requested_workspace_id=requested_workspace_id,
        )


def _factory(pg) -> _LiveProjectScopedFactory:
    return _LiveProjectScopedFactory(
        pg.connect_authorization,
        lambda scope: pg.connect_project_scope(scope.workspace_id, scope.project_id),
    )


class _LiveReadinessPool:
    """Adapt one fixture-bound project login to the hosted readiness probes."""

    def __init__(self, pg, workspace_id: UUID, project_id: UUID) -> None:
        self.pg = pg
        self.workspace_id = workspace_id
        self.project_id = project_id

    @contextmanager
    def connection(self, *, timeout: float):
        assert timeout > 0
        with self.pg.project_connection(self.workspace_id, self.project_id) as connection:
            yield connection


def _fetchone(pg, query: str, params=(), *, workspace_id=None, project_id=None):
    if workspace_id is not None and project_id is not None:
        connection_context = pg.project_connection(workspace_id, project_id)
    else:
        connection_context = pg.connection()
    with connection_context as connection:
        return connection.execute(query, params).fetchone()


def _expect_sqlstate(
    pg, statement: str, params, expected: str, *, workspace_id=None, project_id=None
) -> None:
    if workspace_id is not None and project_id is not None:
        connection_context = pg.project_connection(workspace_id, project_id)
    else:
        connection_context = pg.connection()
    with connection_context as connection, connection.transaction():
        with pytest.raises(pg.psycopg.Error) as raised:
            with connection.transaction():  # savepoint: keep this fixture transaction usable
                connection.execute(statement, params)
        assert raised.value.sqlstate == expected


def test_real_migrations_and_checksum_ledger_are_applied_once(live_postgres):
    versions = tuple(migration.version for migration in live_postgres.migrations)

    assert live_postgres.migration_result.applied_versions == versions
    assert live_postgres.migration_result.current_version == versions[-1]


def test_new_human_defaults_suspended_then_authorized_only_after_activation(
    postgres_connections, tenant_fixture
):
    pg = postgres_connections
    fixture = tenant_fixture
    assert _fetchone(
        pg, "SELECT status FROM celestin_users WHERE id = %s", (fixture.default_user_id,)
    )[0] == "suspended"

    with pg.connection() as connection, connection.transaction():
        connection.execute(
            "UPDATE celestin_users SET status = 'active' WHERE id = %s",
            (fixture.default_user_id,),
        )
    principal = VerifiedPrincipal(fixture.issuer, f"default-{fixture.suffix}", "human")
    with pytest.raises(HostedAccessDenied):
        _factory(pg)._execute_authorized(
            fixture.resolver(), principal, fixture.project_key, "read",
            lambda *_: pytest.fail("active user without membership must not read"),
        )

    with pg.connection() as connection, connection.transaction():
        connection.execute(
            """INSERT INTO celestin_workspace_memberships
                   (workspace_id, user_id, role, status)
               VALUES (%s, %s, 'member', 'active')""",
            (fixture.workspace_a, fixture.default_user_id),
        )
        connection.execute(
            """INSERT INTO celestin_project_memberships
                   (workspace_id, project_id, user_id, can_read)
               VALUES (%s, %s, %s, TRUE)""",
            (fixture.workspace_a, fixture.project_a, fixture.default_user_id),
        )

    assert (
        _factory(pg)._execute_authorized(
            fixture.resolver(), principal, fixture.project_key, "read",
            lambda session, scope: scope.project_id,
        )
        == fixture.project_a
    )

    with pg.connection() as connection, connection.transaction():
        connection.execute(
            "UPDATE celestin_users SET status = 'suspended' WHERE id = %s",
            (fixture.default_user_id,),
        )
    with pytest.raises(HostedAccessDenied):
        _factory(pg)._execute_authorized(
            fixture.resolver(), principal, fixture.project_key, "read",
            lambda session, scope: pytest.fail("suspended user operation must not run"),
        )


def test_human_explicit_read_write_grants_and_revocation(postgres_connections, tenant_fixture):
    pg, fixture = postgres_connections, tenant_fixture
    resolver = fixture.resolver()
    factory = _factory(pg)
    principal = fixture.human()
    assert factory._execute_authorized(
        resolver, principal, fixture.project_key, "read", lambda _session, scope: scope.project_id
    ) == fixture.project_a
    assert factory._execute_authorized(
        resolver, principal, fixture.project_key, "write", lambda _session, scope: scope.project_id
    ) == fixture.project_a

    with pg.connection() as connection, connection.transaction():
        connection.execute(
            "UPDATE celestin_project_memberships SET can_write = FALSE "
            "WHERE project_id = %s AND user_id = %s",
            (fixture.project_a, fixture.human_id),
        )
    with pytest.raises(HostedAccessDenied):
        factory._execute_authorized(
            resolver, principal, fixture.project_key, "write",
            lambda _session, _scope: pytest.fail("read-only grant must not write"),
        )
    with pg.connection() as connection, connection.transaction():
        connection.execute(
            "UPDATE celestin_project_memberships SET revoked_at = CURRENT_TIMESTAMP "
            "WHERE project_id = %s AND user_id = %s",
            (fixture.project_a, fixture.human_id),
        )
    with pytest.raises(HostedAccessDenied):
        factory._execute_authorized(
            resolver, principal, fixture.project_key, "read",
            lambda _session, _scope: pytest.fail("revoked grant must not read"),
        )


def test_unknown_identity_missing_or_suspended_membership_and_missing_grant_deny(
    postgres_connections, tenant_fixture
):
    pg, fixture = postgres_connections, tenant_fixture
    factory = _factory(pg)
    resolver = fixture.resolver()
    with pytest.raises(HostedAccessDenied):
        factory._execute_authorized(
            resolver, VerifiedPrincipal(fixture.issuer, "unknown", "human"),
            fixture.project_key, "read", lambda *_: pytest.fail("unknown identity must not read"),
        )

    with pg.connection() as connection, connection.transaction():
        connection.execute(
            "UPDATE celestin_workspace_memberships SET status = 'suspended' "
            "WHERE workspace_id = %s AND user_id = %s",
            (fixture.workspace_a, fixture.human_id),
        )
    with pytest.raises(HostedAccessDenied):
        factory._execute_authorized(
            resolver, fixture.human(), fixture.project_key, "read",
            lambda *_: pytest.fail("suspended membership must not read"),
        )

    with pg.connection() as connection, connection.transaction():
        connection.execute(
            "UPDATE celestin_workspace_memberships SET status = 'active' "
            "WHERE workspace_id = %s AND user_id = %s",
            (fixture.workspace_a, fixture.human_id),
        )
        connection.execute(
            "DELETE FROM celestin_project_memberships WHERE project_id = %s AND user_id = %s",
            (fixture.project_a, fixture.human_id),
        )
    with pytest.raises(HostedAccessDenied):
        factory._execute_authorized(
            resolver, fixture.human(), fixture.project_key, "read",
            lambda *_: pytest.fail("missing explicit grant must not read"),
        )


def test_service_identity_grant_and_revocation(postgres_connections, tenant_fixture):
    pg, fixture = postgres_connections, tenant_fixture
    factory, resolver, principal = _factory(pg), fixture.resolver(), fixture.service()
    assert factory._execute_authorized(
        resolver, principal, fixture.project_key, "write", lambda _session, scope: scope.project_id
    ) == fixture.project_a

    with pg.connection() as connection, connection.transaction():
        connection.execute(
            "UPDATE celestin_service_project_grants SET revoked_at = CURRENT_TIMESTAMP "
            "WHERE project_id = %s AND service_identity_id = %s",
            (fixture.project_a, fixture.service_id),
        )
    with pytest.raises(HostedAccessDenied):
        factory._execute_authorized(
            resolver, principal, fixture.project_key, "read",
            lambda *_: pytest.fail("revoked service grant must not read"),
        )
    with pg.connection() as connection, connection.transaction():
        connection.execute(
            "UPDATE celestin_service_project_grants SET revoked_at = NULL "
            "WHERE project_id = %s AND service_identity_id = %s",
            (fixture.project_a, fixture.service_id),
        )
    with pg.connection() as connection, connection.transaction():
        connection.execute(
            "UPDATE celestin_service_project_grants SET revoked_at = CURRENT_TIMESTAMP "
            "WHERE project_id = %s AND service_identity_id = %s",
            (fixture.project_a, fixture.service_id),
        )
    with pytest.raises(HostedAccessDenied):
        factory._execute_authorized(
            resolver, principal, fixture.project_key, "read",
            lambda *_: pytest.fail("revoked service grant must not read"),
        )
    with pg.connection() as connection, connection.transaction():
        connection.execute(
            "UPDATE celestin_service_project_grants SET revoked_at = NULL "
            "WHERE project_id = %s AND service_identity_id = %s",
            (fixture.project_a, fixture.service_id),
        )
    with pg.connection() as connection, connection.transaction():
        connection.execute(
            "UPDATE celestin_service_identities SET status = 'revoked', "
            "revoked_at = CURRENT_TIMESTAMP WHERE id = %s",
            (fixture.service_id,),
        )
    with pytest.raises(HostedAccessDenied):
        factory._execute_authorized(
            resolver, principal, fixture.project_key, "read",
            lambda *_: pytest.fail("revoked service identity must not read"),
        )


def test_archived_resources_and_same_public_key_are_workspace_scoped(postgres_connections, tenant_fixture):
    pg, fixture = postgres_connections, tenant_fixture
    factory, principal = _factory(pg), fixture.human()
    first = factory._execute_authorized(
        fixture.resolver(fixture.workspace_a), principal, fixture.project_key, "read",
        lambda _session, scope: scope.project_id,
    )
    second = factory._execute_authorized(
        fixture.resolver(fixture.workspace_b), principal, fixture.project_key, "read",
        lambda _session, scope: scope.project_id,
    )
    assert first == fixture.project_a
    assert second == fixture.project_b
    assert first != second

    with pg.connection() as connection, connection.transaction():
        connection.execute(
            "UPDATE celestin_projects SET archived_at = CURRENT_TIMESTAMP WHERE id = %s",
            (fixture.project_a,),
        )
    with pytest.raises(HostedAccessDenied):
        factory._execute_authorized(
            fixture.resolver(fixture.workspace_a), principal, fixture.project_key, "read",
            lambda *_: pytest.fail("archived project must not read"),
        )

    with pg.connection() as connection, connection.transaction():
        connection.execute(
            "UPDATE celestin_projects SET archived_at = NULL WHERE id = %s", (fixture.project_a,)
        )
        connection.execute(
            "UPDATE celestin_workspaces SET archived_at = CURRENT_TIMESTAMP WHERE id = %s",
            (fixture.workspace_a,),
        )
    with pytest.raises(HostedAccessDenied):
        factory._execute_authorized(
            fixture.resolver(fixture.workspace_a), principal, fixture.project_key, "read",
            lambda *_: pytest.fail("archived workspace must not read"),
        )


def test_source_message_reference_is_scoped_and_same_project_decision_succeeds(
    postgres_connections, tenant_fixture
):
    pg, fixture = postgres_connections, tenant_fixture
    factory, resolver, principal = _factory(pg), fixture.resolver(), fixture.human()
    result = factory._execute_authorized(
        resolver,
        principal,
        fixture.project_key,
        "write",
        lambda session, scope: (
            scope.project_id,
            session._connection_for_adapters.execute(
                "SELECT id FROM chat_messages WHERE workspace_id = %s AND project_id = %s AND id = %s",
                (scope.workspace_id, scope.project_id, fixture.message_a),
            ).fetchone()[0],
        ),
        source_message_id=str(fixture.message_a),
    )
    assert result == (fixture.project_a, fixture.message_a)

    missing_message_id = max(fixture.message_a, fixture.message_b) + 1000
    for out_of_scope_id in (fixture.message_b, missing_message_id):
        with pytest.raises(HostedSourceMessageNotFound):
            factory._execute_authorized(
                resolver, principal, fixture.project_key, "write",
                lambda *_: pytest.fail("invalid source reference must stop before write"),
                source_message_id=str(out_of_scope_id),
            )

    _expect_sqlstate(
        pg,
        """INSERT INTO project_memory
               (workspace_id, project_id, key, value, status, source_message_id, created_at, updated_at)
           VALUES (%s, %s, %s, 'synthetic invalid reference', 'draft', %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
        (fixture.workspace_a, fixture.project_a, f"cross-project-{fixture.suffix}", fixture.message_b),
        "23503",
        workspace_id=fixture.workspace_a,
        project_id=fixture.project_a,
    )
    _expect_sqlstate(
        pg,
        """INSERT INTO project_memory
               (workspace_id, project_id, key, value, status, source_message_id, created_at, updated_at)
           VALUES (%s, %s, %s, 'synthetic invalid reference', 'draft', %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
        (fixture.workspace_a, fixture.project_a, f"missing-source-{fixture.suffix}", missing_message_id),
        "23503",
        workspace_id=fixture.workspace_a,
        project_id=fixture.project_a,
    )
    _expect_sqlstate(
        pg,
        """INSERT INTO project_memory
               (workspace_id, project_id, key, value, status, source_message_id, created_at, updated_at)
           VALUES (%s, %s, %s, 'synthetic invalid reference', 'draft', %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
        (fixture.workspace_a, fixture.project_a, f"missing-source-{fixture.suffix}", 987654321),
        "23503",
        workspace_id=fixture.workspace_a,
        project_id=fixture.project_a,
    )
    _expect_sqlstate(
        pg,
        """INSERT INTO project_memory
               (workspace_id, project_id, key, value, status, source_message_id, created_at, updated_at)
           VALUES (%s, %s, %s, 'synthetic invalid reference', 'draft', %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
        (fixture.workspace_a, fixture.project_a, f"cross-workspace-{fixture.suffix}", fixture.message_b),
        "23503",
        workspace_id=fixture.workspace_a,
        project_id=fixture.project_a,
    )


def test_project_runtime_retrieval_uses_scoped_request_uow_and_authoritative_rows(
    postgres_connections, tenant_fixture
):
    pg, fixture = postgres_connections, tenant_fixture
    factory = _factory(pg)
    missing_message_id = max(fixture.message_a, fixture.message_b) + 1000

    def retrieve(request):
        connection = request.retrieval_data._request_connection
        assert connection is request._authorization_session._connection_for_adapters
        assert connection.info.transaction_status == pg.psycopg.pq.TransactionStatus.INTRANS

        matches = request.retrieval_service.search_chat_history(
            fixture.project_key, "synthetic live fixture", 10
        )
        records = request.retrieval_data.load_messages(
            fixture.project_key,
            [str(fixture.message_a), str(fixture.message_b), str(missing_message_id)],
        )
        return matches, records

    matches, records = factory._execute_authorized_request(
        fixture.resolver(),
        fixture.human(),
        fixture.project_key,
        "read",
        retrieve,
    )

    assert len(matches) == 1
    match = matches[0]
    assert match.message_id == str(fixture.message_a)
    assert match.project_id == fixture.project_key
    assert match.source_id == f"a-{fixture.suffix}"
    assert match.content == f"synthetic live fixture a-{fixture.suffix}"
    assert match.role == "user"
    assert match.timestamp is not None
    assert set(records) == {str(fixture.message_a)}
    assert records[str(fixture.message_a)].role == match.role
    assert records[str(fixture.message_a)].content == match.content


def test_project_runtime_evidence_service_uses_authorized_request_uow(
    postgres_connections, tenant_fixture
):
    pg, fixture = postgres_connections, tenant_fixture
    factory = _factory(pg)
    def create_and_read(request):
        connection = request.evidence_store._connection
        assert connection is request._authorization_session._connection_for_adapters
        assert connection.info.transaction_status == pg.psycopg.pq.TransactionStatus.INTRANS
        bundle = request.evidence_service.create_knowledge(
            request.evidence_project("write"),
            KnowledgeType.DISCOVERY,
            "Hosted Evidence is request scoped.",
            (EvidenceSource(EvidenceType.MESSAGE, str(fixture.message_a)),),
        )
        readback = request.evidence_service.get_knowledge(
            request.evidence_project("read"),
            bundle.record.knowledge_id,
        )
        return bundle, readback

    bundle = factory._execute_authorized_request(
        fixture.resolver(), fixture.human(), fixture.project_key, "write",
        lambda request: create_and_read(request),
    )
    created, readback = bundle
    assert created.record.knowledge_id
    assert readback.record == created.record
    assert readback.evidence == created.evidence
    assert created.evidence[0].reference_status is ReferenceStatus.AVAILABLE


@pytest.mark.parametrize(
    "own_workspace_attr,own_project_attr,other_workspace_attr,other_project_attr,role",
    [
        ("workspace_a", "project_a", "workspace_b", "project_b", "project_a"),
        ("workspace_b", "project_b", "workspace_a", "project_a", "project_b"),
    ],
)
def test_project_login_rls_resists_forged_gucs_and_direct_cross_project_sql(
    postgres_connections,
    tenant_fixture,
    own_workspace_attr,
    own_project_attr,
    other_workspace_attr,
    other_project_attr,
    role,
):
    pg, fixture = postgres_connections, tenant_fixture
    own_workspace = getattr(fixture, own_workspace_attr)
    own_project = getattr(fixture, own_project_attr)
    other_workspace = getattr(fixture, other_workspace_attr)
    other_project = getattr(fixture, other_project_attr)
    target_login = (
        pg.target.project_a_user if role == "project_a" else pg.target.project_b_user
    )
    other_login = (
        pg.target.project_b_user if role == "project_a" else pg.target.project_a_user
    )

    with pg.project_connection(own_workspace, own_project) as connection:
        with connection.transaction():
            assert connection.execute(
                "SELECT session_user, current_user"
            ).fetchone() == (target_login, target_login)
            assert connection.execute("SELECT count(*) FROM chat_messages").fetchone()[0] == 1
            assert connection.execute("SELECT count(*) FROM project_memory").fetchone()[0] == 1
            assert connection.execute(
                "SELECT count(*) FROM chat_messages WHERE project_id = %s",
                (other_project,),
            ).fetchone()[0] == 0
            assert connection.execute(
                "SELECT count(*) FROM project_memory WHERE project_id = %s",
                (other_project,),
            ).fetchone()[0] == 0
            assert connection.execute(
                "SELECT count(*) FROM chat_messages WHERE id = %s",
                (fixture.message_b if role == "project_a" else fixture.message_a,),
            ).fetchone()[0] == 0
            assert connection.execute(
                "SELECT count(*) FROM project_memory WHERE id = %s",
                (fixture.memory_b if role == "project_a" else fixture.memory_a,),
            ).fetchone()[0] == 0

            # The legacy values are attacker-controlled and must not change scope.
            connection.execute(
                "SELECT set_config('celestin.workspace_id', %s, true)",
                (str(other_workspace),),
            )
            connection.execute(
                "SELECT set_config('celestin.project_id', %s, true)",
                (str(other_project),),
            )
            assert connection.execute("SELECT count(*) FROM chat_messages").fetchone()[0] == 1
            assert connection.execute("SELECT count(*) FROM project_memory").fetchone()[0] == 1
            assert connection.execute(
                "SELECT count(*) FROM chat_messages WHERE project_id = %s",
                (other_project,),
            ).fetchone()[0] == 0
            assert connection.execute(
                "SELECT count(*) FROM project_memory WHERE project_id = %s",
                (other_project,),
            ).fetchone()[0] == 0

            visible_scope_rows = connection.execute(
                "SELECT login_role_name, workspace_id, project_id "
                "FROM celestin_private.project_runtime_scopes"
            ).fetchall()
            assert visible_scope_rows == [(target_login, own_workspace, own_project)]

            with pytest.raises(pg.psycopg.errors.InsufficientPrivilege):
                with connection.transaction():
                    connection.execute(
                        """INSERT INTO chat_messages
                               (workspace_id, project_id, conversation_id, role, content, created_at)
                           VALUES (%s, %s, %s, 'assistant', 'scope probe', CURRENT_TIMESTAMP)""",
                        (other_workspace, other_project, f"rls-denied-{fixture.suffix}"),
                    )

            forbidden_writes = (
                (
                    """INSERT INTO project_memory
                           (workspace_id, project_id, key, value, status, source_message_id,
                            created_at, updated_at)
                       VALUES (%s, %s, %s, 'forged project row', 'draft', %s,
                               CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
                    (
                        other_workspace, other_project, f"forged-memory-{fixture.suffix}",
                        fixture.message_b if role == "project_a" else fixture.message_a,
                    ),
                ),
                (
                    """UPDATE project_memory
                          SET workspace_id = %s, project_id = %s
                        WHERE id = %s""",
                    (other_workspace, other_project, fixture.memory_a if role == "project_a" else fixture.memory_b),
                ),
                (
                    """UPDATE chat_messages
                          SET workspace_id = %s, project_id = %s
                        WHERE id = %s""",
                    (other_workspace, other_project, fixture.message_a if role == "project_a" else fixture.message_b),
                ),
                (
                    "DELETE FROM project_memory WHERE id = %s",
                    (fixture.memory_b if role == "project_a" else fixture.memory_a,),
                ),
                (
                    "DELETE FROM chat_messages WHERE id = %s",
                    (fixture.message_b if role == "project_a" else fixture.message_a,),
                ),
            )
            for statement, params in forbidden_writes:
                with pytest.raises(pg.psycopg.errors.InsufficientPrivilege) as raised:
                    with connection.transaction():
                        connection.execute(statement, params)
                assert raised.value.sqlstate == "42501"

            with pytest.raises(pg.psycopg.errors.InsufficientPrivilege):
                with connection.transaction():
                    connection.execute(f'SET ROLE "{other_login}"')
            with pytest.raises(pg.psycopg.errors.InsufficientPrivilege):
                with connection.transaction():
                    connection.execute(f'SET SESSION AUTHORIZATION "{other_login}"')
            with pytest.raises(pg.psycopg.errors.InsufficientPrivilege):
                with connection.transaction():
                    connection.execute(
                        "UPDATE celestin_private.project_runtime_scopes "
                        "SET project_id = %s WHERE login_role_name = %s",
                        (other_project, target_login),
                    )
            with pytest.raises(pg.psycopg.errors.InsufficientPrivilege):
                with connection.transaction():
                    connection.execute(
                        "INSERT INTO celestin_private.project_runtime_scopes "
                        "(login_role_name, workspace_id, project_id) VALUES (%s, %s, %s)",
                        (f"forged-{target_login}", other_workspace, other_project),
                    )
            with pytest.raises(pg.psycopg.errors.InsufficientPrivilege):
                with connection.transaction():
                    connection.execute(
                        "DELETE FROM celestin_private.project_runtime_scopes "
                        "WHERE login_role_name = %s",
                        (target_login,),
                    )


def test_project_login_cannot_lock_an_unmapped_workspace(postgres_connections, tenant_fixture):
    """A project credential may request only its mapped workspace lock.

    This is a live adversarial case for migration 0009. It remains opt-in and
    was not exercised by ordinary offline test runs.
    """
    pg, fixture = postgres_connections, tenant_fixture
    with pg.project_connection(fixture.workspace_a, fixture.project_a) as connection:
        with connection.transaction():
            connection.execute(
                "SELECT public.celestin_lock_project_authorization_request(%s)",
                (fixture.workspace_a,),
            )

        with pytest.raises(pg.psycopg.errors.InsufficientPrivilege):
            with connection.transaction():
                connection.execute(
                    "SELECT public.celestin_lock_project_authorization_request(%s)",
                    (fixture.workspace_b,),
                )

        with pytest.raises(pg.psycopg.errors.InsufficientPrivilege):
            with connection.transaction():
                connection.execute(
                    "SELECT public.celestin_lock_authorization_request(%s, %s, %s, %s)",
                    (fixture.workspace_b, "human", fixture.issuer, fixture.human_subject),
                )


def test_missing_project_scope_mapping_denies_content_reads_and_writes(
    postgres_connections, tenant_fixture
):
    pg, fixture = postgres_connections, tenant_fixture
    login = pg.target.project_a_user
    with pg.connection() as connection, connection.transaction():
        removed = connection.execute(
            "DELETE FROM celestin_private.project_runtime_scopes "
            "WHERE login_role_name = %s RETURNING login_role_name, workspace_id, project_id",
            (login,),
        ).fetchone()
    assert removed == (login, fixture.workspace_a, fixture.project_a)
    try:
        with pg.project_connection(fixture.workspace_a, fixture.project_a) as connection:
            with connection.transaction():
                assert connection.execute("SELECT count(*) FROM chat_messages").fetchone()[0] == 0
                assert connection.execute("SELECT count(*) FROM project_memory").fetchone()[0] == 0
                with pytest.raises(pg.psycopg.errors.InsufficientPrivilege):
                    with connection.transaction():
                        connection.execute(
                            """INSERT INTO chat_messages
                                   (workspace_id, project_id, conversation_id, role, content, created_at)
                               VALUES (%s, %s, %s, 'user', 'missing scope', CURRENT_TIMESTAMP)""",
                            (fixture.workspace_a, fixture.project_a, f"missing-{fixture.suffix}"),
                        )
                updated = connection.execute(
                    "UPDATE project_memory SET value = 'missing-scope-write' WHERE id = %s",
                    (fixture.memory_a,),
                )
                assert updated.rowcount == 0
                with pytest.raises(pg.psycopg.errors.InsufficientPrivilege):
                    connection.execute(
                        "DELETE FROM chat_messages WHERE id = %s", (fixture.message_a,)
                    )
    finally:
        with pg.connection() as connection, connection.transaction():
            connection.execute(
                "INSERT INTO celestin_private.project_runtime_scopes "
                "(login_role_name, workspace_id, project_id) VALUES (%s, %s, %s)",
                removed,
            )


@pytest.mark.parametrize("role", ["project_a", "project_b"])
def test_project_runtime_live_policy_and_trigger_catalogs_cover_all_protected_tables(
    postgres_connections, tenant_fixture, role
):
    pg, fixture = postgres_connections, tenant_fixture
    workspace_id = fixture.workspace_a if role == "project_a" else fixture.workspace_b
    project_id = fixture.project_a if role == "project_a" else fixture.project_b
    pool = _LiveReadinessPool(pg, workspace_id, project_id)

    assert len(PROTECTED_TABLES) == 12
    _assert_expected_policy_catalog(pool, timeout=5)
    _assert_expected_trigger_catalog(pool, timeout=5)
    _assert_evidence_runtime_privilege_profile(pool, timeout=5)

    with pg.project_connection(workspace_id, project_id) as connection:
        privileges = connection.execute(
            """SELECT table_name,
                      has_table_privilege(current_user, format('public.%I', table_name), 'SELECT'),
                      has_table_privilege(current_user, format('public.%I', table_name), 'INSERT'),
                      has_table_privilege(current_user, format('public.%I', table_name), 'UPDATE'),
                      has_table_privilege(current_user, format('public.%I', table_name), 'DELETE'),
                      has_table_privilege(current_user, format('public.%I', table_name), 'TRUNCATE'),
                      has_table_privilege(current_user, format('public.%I', table_name), 'TRIGGER'),
                      has_table_privilege(current_user, format('public.%I', table_name), 'REFERENCES')
                 FROM unnest(%s::text[]) AS tables(table_name)
                ORDER BY table_name""",
            (list(PROTECTED_TABLES),),
        ).fetchall()
    readable_tables = {"chat_messages", "project_memory", *EVIDENCE_RUNTIME_TABLES}
    writable_tables = {"chat_messages", "project_memory", *EVIDENCE_RUNTIME_TABLES}
    updatable_tables = {"project_memory", *EVIDENCE_RUNTIME_TABLES}
    expected = {
        table: (
            table in readable_tables,
            table in writable_tables,
            table in updatable_tables,
            False,
            False,
            False,
            False,
        )
        for table in PROTECTED_TABLES
    }
    assert {table: tuple(row[1:]) for row in privileges} == expected

    knowledge_id = f"runtime-evidence-profile-{fixture.suffix}"
    evidence_id = f"runtime-evidence-profile-{fixture.suffix}"
    scopes = (
        (fixture.workspace_a, fixture.project_a),
        (fixture.workspace_b, fixture.project_b),
    )
    for scoped_workspace, scoped_project in scopes:
        with pg.project_connection(scoped_workspace, scoped_project) as connection:
            with connection.transaction():
                connection.execute(
                    """INSERT INTO celestin_knowledge_records
                           (workspace_id, project_id, knowledge_id, knowledge_type, claim,
                            created_at, updated_at, verification_status, lifecycle_status)
                       VALUES (%s, %s, %s, 'decision', 'synthetic ACL scope fixture',
                               CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 'unverified', 'current')""",
                    (scoped_workspace, scoped_project, knowledge_id),
                )
                connection.execute(
                    """INSERT INTO celestin_evidence_records
                           (workspace_id, project_id, knowledge_id, evidence_id,
                            evidence_position, evidence_type, source_id, captured_at,
                            reference_status, verification_status)
                       VALUES (%s, %s, %s, %s, 0, 'other', 'synthetic-scope-fixture',
                               CURRENT_TIMESTAMP, 'unresolved', 'unverified')""",
                    (scoped_workspace, scoped_project, knowledge_id, evidence_id),
                )

    for scoped_workspace, scoped_project in scopes:
        with pg.project_connection(scoped_workspace, scoped_project) as connection:
            with connection.transaction():
                assert connection.execute(
                    """SELECT workspace_id, project_id FROM celestin_knowledge_records
                         WHERE knowledge_id = %s""",
                    (knowledge_id,),
                ).fetchall() == [(scoped_workspace, scoped_project)]
                assert connection.execute(
                    """SELECT workspace_id, project_id FROM celestin_evidence_records
                         WHERE evidence_id = %s""",
                    (evidence_id,),
                ).fetchall() == [(scoped_workspace, scoped_project)]

    for own_workspace, own_project, foreign_workspace, foreign_project in (
        (fixture.workspace_a, fixture.project_a, fixture.workspace_b, fixture.project_b),
        (fixture.workspace_b, fixture.project_b, fixture.workspace_a, fixture.project_a),
    ):
        _expect_sqlstate(
            pg,
            """INSERT INTO celestin_knowledge_records
                   (workspace_id, project_id, knowledge_id, knowledge_type, claim,
                    created_at, updated_at, verification_status, lifecycle_status)
               VALUES (%s, %s, %s, 'decision', 'cross-scope insert must fail',
                       CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 'unverified', 'current')""",
            (foreign_workspace, foreign_project, f"forbidden-{fixture.suffix}-{own_project}"),
            "42501",
            workspace_id=own_workspace,
            project_id=own_project,
        )
        _expect_sqlstate(
            pg,
            """INSERT INTO celestin_evidence_records
                   (workspace_id, project_id, knowledge_id, evidence_id,
                    evidence_position, evidence_type, source_id, captured_at,
                    reference_status, verification_status)
               VALUES (%s, %s, %s, %s, 1, 'other', 'foreign-scope-fixture',
                       CURRENT_TIMESTAMP, 'unresolved', 'unverified')""",
            (
                foreign_workspace,
                foreign_project,
                knowledge_id,
                f"forbidden-evidence-{fixture.suffix}-{own_project}",
            ),
            "42501",
            workspace_id=own_workspace,
            project_id=own_project,
        )


@pytest.mark.parametrize("role", ["project_a", "project_b"])
def test_project_runtime_readiness_rejects_direct_advisory_lock_acl(
    postgres_connections, tenant_fixture, role
):
    """Migrations 0001-0009 must not silently pass this strengthened gate."""
    pg, fixture = postgres_connections, tenant_fixture
    workspace_id = fixture.workspace_a if role == "project_a" else fixture.workspace_b
    project_id = fixture.project_a if role == "project_a" else fixture.project_b
    with pg.project_connection(workspace_id, project_id) as connection:
        rows = connection.execute(_advisory_lock_privilege_query()).fetchall()
    with pytest.raises(RuntimeError, match="advisory-lock privileges"):
        _validate_advisory_lock_privilege_catalog(
            rows, role_profile="project_runtime"
        )
    with pytest.raises(RuntimeError, match="advisory-lock privileges"):
        _assert_safe_project_runtime(
            _LiveReadinessPool(pg, workspace_id, project_id),
            timeout=5,
            scope=(workspace_id, project_id),
        )


@pytest.mark.parametrize("role", ["project_a", "project_b"])
def test_project_runtime_direct_session_and_transaction_advisory_locks_are_denied(
    postgres_connections, tenant_fixture, role
):
    pg, fixture = postgres_connections, tenant_fixture
    workspace_id = fixture.workspace_a if role == "project_a" else fixture.workspace_b
    project_id = fixture.project_a if role == "project_a" else fixture.project_b
    statements = (
        "SELECT pg_catalog.pg_advisory_lock(%s::bigint)",
        "SELECT pg_catalog.pg_advisory_lock_shared(%s::bigint)",
        "SELECT pg_catalog.pg_advisory_unlock(%s::bigint)",
        "SELECT pg_catalog.pg_advisory_unlock_all()",
        "SELECT pg_catalog.pg_advisory_xact_lock(%s::bigint)",
        "SELECT pg_catalog.pg_advisory_xact_lock_shared(%s::bigint)",
        "SELECT pg_catalog.pg_try_advisory_xact_lock(%s::bigint)",
    )
    lock_key = 7_238_451_221
    with pg.project_connection(workspace_id, project_id) as connection:
        for statement in statements:
            params = () if "%s" not in statement else (lock_key,)
            with pytest.raises(pg.psycopg.errors.InsufficientPrivilege) as raised:
                with connection.transaction():
                    connection.execute(statement, params)
            assert raised.value.sqlstate == "42501"


def test_live_advisory_function_role_profiles(
    postgres_connections, tenant_fixture
):
    pg = postgres_connections
    profiles = (
        ("migration", pg.migration_connection),
        ("authorization_admin", pg.connection),
        ("authorization_reader", pg.authorization_connection),
    )
    for role_profile, connect in profiles:
        with connect() as connection:
            rows = connection.execute(_advisory_lock_privilege_query()).fetchall()
            _validate_advisory_lock_privilege_catalog(rows, role_profile=role_profile)
            if role_profile == "authorization_admin":
                _assert_safe_authorization_admin(
                    _ExistingConnectionPool(connection), timeout=5
                )

    scopes = (
        (tenant_fixture.workspace_a, tenant_fixture.project_a),
        (tenant_fixture.workspace_b, tenant_fixture.project_b),
    )
    for workspace_id, project_id in scopes:
        with pg.project_connection(workspace_id, project_id) as connection:
            rows = connection.execute(_advisory_lock_privilege_query()).fetchall()
        _validate_advisory_lock_privilege_catalog(
            rows, role_profile="project_runtime"
        )


def test_live_three_authorization_and_runtime_group_membership_roster(
    postgres_connections
):
    pg = postgres_connections
    expected_members = {
        (AUTHORIZATION_ADMIN_ROLE, pg.target.admin_user),
        (AUTHORIZATION_READER_ROLE, pg.target.authorization_user),
        ("celestin_project_runtime", pg.target.project_a_user),
        ("celestin_project_runtime", pg.target.project_b_user),
    }
    with pg.connection() as connection:
        rows = connection.execute(
            _privileged_role_membership_query(), (list(PRIVILEGED_GROUP_ROLES),)
        ).fetchall()
    _validate_privileged_role_membership_catalog(
        rows, expected_members=expected_members
    )


def test_constraints_reject_cross_workspace_membership_and_duplicate_grants(
    postgres_connections, tenant_fixture
):
    pg, fixture = postgres_connections, tenant_fixture
    _expect_sqlstate(
        pg,
        """INSERT INTO celestin_workspace_memberships
               (workspace_id, user_id, role, status)
           VALUES (%s, %s, 'member', 'active')""",
        (fixture.workspace_a, fixture.human_id),
        "23505",
    )
    _expect_sqlstate(
        pg,
        """INSERT INTO celestin_workspace_memberships
               (workspace_id, user_id, role, status)
           VALUES (%s, %s, 'member', 'active')""",
        (fixture.workspace_a, fixture.human_id),
        "23505",
    )
    _expect_sqlstate(
        pg,
        """INSERT INTO celestin_project_memberships
               (workspace_id, project_id, user_id, can_read)
           VALUES (%s, %s, %s, TRUE)""",
        (fixture.workspace_b, fixture.project_a, fixture.contender_user_id),
        "23503",
    )
    _expect_sqlstate(
        pg,
        """INSERT INTO celestin_project_memberships
               (workspace_id, project_id, user_id, can_read)
           VALUES (%s, %s, %s, TRUE)""",
        (fixture.workspace_a, fixture.project_a, fixture.human_id),
        "23505",
    )
    _expect_sqlstate(
        pg,
        """INSERT INTO celestin_service_project_grants
               (workspace_id, project_id, service_identity_id, can_read)
           VALUES (%s, %s, %s, TRUE)""",
        (fixture.workspace_a, fixture.project_a, fixture.service_id),
        "23505",
    )


def test_schema_checks_role_status_and_write_implies_read(postgres_connections, tenant_fixture):
    pg, fixture = postgres_connections, tenant_fixture
    _expect_sqlstate(
        pg,
        """UPDATE celestin_workspace_memberships SET role = 'observer'
           WHERE workspace_id = %s AND user_id = %s""",
        (fixture.workspace_a, fixture.human_id),
        "23514",
    )
    _expect_sqlstate(
        pg,
        """UPDATE celestin_workspace_memberships SET status = 'disabled'
           WHERE workspace_id = %s AND user_id = %s""",
        (fixture.workspace_a, fixture.human_id),
        "23514",
    )
    _expect_sqlstate(
        pg,
        "UPDATE celestin_users SET status = 'disabled' WHERE id = %s",
        (fixture.human_id,),
        "23514",
    )
    _expect_sqlstate(
        pg,
        """INSERT INTO celestin_project_memberships
               (workspace_id, project_id, user_id, can_read, can_write)
           VALUES (%s, %s, %s, FALSE, TRUE)""",
        (fixture.workspace_a, fixture.project_a, fixture.contender_user_id),
        "23514",
    )
    _expect_sqlstate(
        pg,
        "UPDATE celestin_service_identities SET status = 'disabled' WHERE id = %s",
        (fixture.service_id,),
        "23514",
    )


def test_restrictive_deletion_preserves_source_provenance(postgres_connections, tenant_fixture):
    pg, fixture = postgres_connections, tenant_fixture
    _expect_sqlstate(
        pg,
        "DELETE FROM chat_messages WHERE id = %s AND project_id = %s",
        (fixture.message_a, fixture.project_a),
        "23001",
        workspace_id=fixture.workspace_a,
        project_id=fixture.project_a,
    )
    assert _fetchone(
        pg,
        "SELECT source_message_id FROM project_memory WHERE id = %s AND project_id = %s",
        (fixture.memory_a, fixture.project_a),
        workspace_id=fixture.workspace_a,
        project_id=fixture.project_a,
    )[0] == fixture.message_a

    _expect_sqlstate(
        pg,
        "DELETE FROM celestin_projects WHERE id = %s AND workspace_id = %s",
        (fixture.project_a, fixture.workspace_a),
        "23001",
    )
    assert _fetchone(
        pg, "SELECT id FROM celestin_projects WHERE id = %s", (fixture.project_a,)
    )[0] == fixture.project_a


def test_archive_and_purge_checks_reject_invalid_timestamps(postgres_connections, tenant_fixture):
    pg, fixture = postgres_connections, tenant_fixture
    cases = (
        (
            "UPDATE celestin_workspaces SET purge_after = CURRENT_TIMESTAMP WHERE id = %s",
            (fixture.workspace_a,),
        ),
        (
            """UPDATE celestin_projects
                  SET archived_at = CURRENT_TIMESTAMP, purge_after = CURRENT_TIMESTAMP - INTERVAL '1 day'
                WHERE id = %s""",
            (fixture.project_a,),
        ),
        (
            "UPDATE chat_messages SET purge_after = CURRENT_TIMESTAMP WHERE id = %s",
            (fixture.message_a,),
        ),
        (
            """UPDATE project_memory
                  SET archived_at = CURRENT_TIMESTAMP, purge_after = CURRENT_TIMESTAMP - INTERVAL '1 day'
                WHERE id = %s""",
            (fixture.memory_a,),
        ),
    )
    for statement, params in cases:
        if "chat_messages" in statement or "project_memory" in statement:
            _expect_sqlstate(
                pg,
                statement,
                params,
                "23514",
                workspace_id=fixture.workspace_a,
                project_id=fixture.project_a,
            )
        else:
            _expect_sqlstate(pg, statement, params, "23514")


def test_workspace_commit_requires_an_active_owner(postgres_connections, tenant_fixture):
    pg, fixture = postgres_connections, tenant_fixture
    workspace_id = uuid4()
    with pg.connection() as connection, connection.transaction():
        with pytest.raises(pg.psycopg.Error) as raised:
            with connection.transaction():
                connection.execute(
                    "INSERT INTO celestin_workspaces (id, slug, name) VALUES (%s, %s, %s)",
                    (workspace_id, f"ownerless-{fixture.suffix}", "Synthetic ownerless-policy probe"),
                )
                connection.execute(
                    "SET CONSTRAINTS celestin_workspace_active_owner_guard IMMEDIATE"
                )
        assert raised.value.sqlstate == "23514"


@pytest.mark.parametrize(
    ("table", "trigger", "assignment", "identity_column"),
    (
        ("celestin_workspace_memberships", "celestin_membership_active_owner_guard",
         "role = 'member'", "user_id"),
        ("celestin_workspace_memberships", "celestin_membership_active_owner_guard",
         "status = 'suspended'", "user_id"),
    ),
)
def test_last_active_owner_cannot_be_demoted_or_suspended(
    postgres_connections, tenant_fixture, table, trigger, assignment, identity_column
):
    pg, fixture = postgres_connections, tenant_fixture
    user_id = fixture.human_id
    with pg.connection() as connection, connection.transaction():
        with pytest.raises(pg.psycopg.Error) as raised:
            with connection.transaction():
                connection.execute(
                    f"UPDATE {table} SET {assignment} "
                    f"WHERE workspace_id = %s AND {identity_column} = %s",
                    (fixture.workspace_a, user_id),
                )
                connection.execute(f"SET CONSTRAINTS {trigger} IMMEDIATE")
        assert raised.value.sqlstate == "23514"


def test_last_active_owner_identity_cannot_be_suspended(postgres_connections, tenant_fixture):
    pg, fixture = postgres_connections, tenant_fixture
    with pg.connection() as connection, connection.transaction():
        with pytest.raises(pg.psycopg.Error) as raised:
            with connection.transaction():
                connection.execute(
                    "UPDATE celestin_users SET status = 'suspended' WHERE id = %s",
                    (fixture.human_id,),
                )
                connection.execute("SET CONSTRAINTS celestin_user_active_owner_guard IMMEDIATE")
        assert raised.value.sqlstate == "23514"


def test_concurrent_owner_suspensions_preserve_one_active_owner(postgres_connections, tenant_fixture):
    pg, fixture = postgres_connections, tenant_fixture
    with pg.connection() as connection, connection.transaction():
        connection.execute(
            "UPDATE celestin_workspace_memberships SET role = 'owner' "
            "WHERE workspace_id = %s AND user_id = %s",
            (fixture.workspace_a, fixture.contender_user_id),
        )

    start = Event()

    def suspend(user_id):
        start.wait(_LOCK_WAIT_SECONDS)
        try:
            with pg.connection() as connection, connection.transaction():
                connection.execute(
                    "UPDATE celestin_workspace_memberships SET status = 'suspended' "
                    "WHERE workspace_id = %s AND user_id = %s",
                    (fixture.workspace_a, user_id),
                )
            return None
        except pg.psycopg.Error as exc:
            return exc.sqlstate

    with ThreadPoolExecutor(max_workers=2) as executor:
        owner_a = executor.submit(suspend, fixture.human_id)
        owner_b = executor.submit(suspend, fixture.contender_user_id)
        start.set()
        outcomes = (owner_a.result(timeout=_LOCK_WAIT_SECONDS), owner_b.result(timeout=_LOCK_WAIT_SECONDS))

    assert sorted(outcomes, key=lambda value: value or "") == [None, "23514"]
    active_owners = _fetchone(
        pg,
        """SELECT count(*) FROM celestin_workspace_memberships AS membership
             JOIN celestin_users AS identity ON identity.id = membership.user_id
            WHERE membership.workspace_id = %s AND membership.role = 'owner'
              AND membership.status = 'active' AND identity.status = 'active'""",
        (fixture.workspace_a,),
    )[0]
    assert active_owners == 1


def test_authorized_write_commits_and_failed_operation_rolls_back(postgres_connections, tenant_fixture):
    pg, fixture = postgres_connections, tenant_fixture
    opened: list = []

    def open_project_connection(scope):
        connection = pg.connect_project_scope(scope.workspace_id, scope.project_id)
        opened.append(connection)
        return connection

    factory = _LiveProjectScopedFactory(
        pg.connect_authorization,
        open_project_connection,
    )
    resolver, principal = fixture.resolver(), fixture.human()
    committed_key = f"committed-{fixture.suffix}"
    backend_pid = None
    transaction_id = None

    def save(session, scope):
        nonlocal backend_pid, transaction_id
        assert opened and session._connection_for_adapters is opened[-1]
        backend_pid = session._connection_for_adapters.raw.info.backend_pid
        transaction_id = session._connection_for_adapters.execute("SELECT txid_current()").fetchone()[0]
        return session._connection_for_adapters.execute(
            """INSERT INTO project_memory
                   (workspace_id, project_id, key, value, status, source_message_id,
                    created_at, updated_at)
               VALUES (%s, %s, %s, 'synthetic committed result', 'draft', %s,
                       CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
               RETURNING id""",
            (scope.workspace_id, scope.project_id, committed_key, fixture.message_a),
        ).fetchone()[0]

    committed_id = factory._execute_authorized(
        resolver, principal, fixture.project_key, "write", save,
        source_message_id=str(fixture.message_a),
    )
    assert _fetchone(
        pg,
        "SELECT id FROM project_memory WHERE id = %s",
        (committed_id,),
        workspace_id=fixture.workspace_a,
        project_id=fixture.project_a,
    )[0] == committed_id
    assert opened[-1].closed
    assert all(cursor.closed for cursor in opened[-1].cursors)

    rolled_back_conversation = f"rollback-{fixture.suffix}"

    def fail_after_insert(session, scope):
        assert session._connection_for_adapters.raw.info.backend_pid != 0
        assert session._connection_for_adapters.execute("SELECT txid_current()").fetchone()[0]
        assert session._connection_for_adapters is opened[-1]
        session._connection_for_adapters.execute(
            """INSERT INTO chat_messages
                   (workspace_id, project_id, conversation_id, role, content, created_at)
               VALUES (%s, %s, %s, 'assistant', 'synthetic rollback probe', CURRENT_TIMESTAMP)""",
            (scope.workspace_id, scope.project_id, rolled_back_conversation),
        )
        raise RuntimeError("synthetic operation failure")

    with pytest.raises(HostedAuthorizationUnavailable, match="authorized operation could not be completed"):
        factory._execute_authorized(
            resolver, principal, fixture.project_key, "write", fail_after_insert
        )
    assert _fetchone(
        pg,
        "SELECT count(*) FROM chat_messages WHERE project_id = %s AND conversation_id = %s",
        (fixture.project_a, rolled_back_conversation),
        workspace_id=fixture.workspace_a,
        project_id=fixture.project_a,
    )[0] == 0
    assert backend_pid is not None and transaction_id is not None
    assert opened[-1].closed
    assert all(cursor.closed for cursor in opened[-1].cursors)


def test_cancelled_authorized_operation_rolls_back_and_closes_both_sessions(
    postgres_connections, tenant_fixture
):
    pg, fixture = postgres_connections, tenant_fixture
    opened = []

    def control_connection():
        connection = pg.connect_authorization()
        opened.append(connection)
        return connection

    def project_connection(scope):
        connection = pg.connect_project_scope(scope.workspace_id, scope.project_id)
        opened.append(connection)
        return connection

    factory = _LiveProjectScopedFactory(control_connection, project_connection)
    conversation = f"cancelled-{fixture.suffix}"

    def cancel_after_insert(session, scope):
        connection = session._connection_for_adapters
        connection.execute(
            """INSERT INTO chat_messages
                   (workspace_id, project_id, conversation_id, role, content, created_at)
               VALUES (%s, %s, %s, 'assistant', 'cancelled operation', CURRENT_TIMESTAMP)""",
            (scope.workspace_id, scope.project_id, conversation),
        )
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        factory._execute_authorized(
            fixture.resolver(), fixture.human(), fixture.project_key,
            "write", cancel_after_insert,
        )

    assert _fetchone(
        pg,
        "SELECT count(*) FROM chat_messages WHERE conversation_id = %s",
        (conversation,),
        workspace_id=fixture.workspace_a,
        project_id=fixture.project_a,
    )[0] == 0
    assert len(opened) == 2
    assert all(connection.closed for connection in opened)
    assert all(cursor.closed for connection in opened for cursor in connection.cursors)


def test_postgres_pool_reuse_clears_session_state_and_session_advisory_locks(
    postgres_connections
):
    pg = postgres_connections
    pool = _create_pool(
        pg.target.dsn,
        SimpleNamespace(connection_timeout=5, pool_timeout=5),
        min_size=1,
        max_size=1,
    )
    backend_pid = None
    original_search_path = None
    lock_key = 7_238_451_229
    try:
        pool.open(wait=True)
        with pool.connection(timeout=5) as connection:
            backend_pid = connection.execute("SELECT pg_backend_pid()").fetchone()[0]
            original_search_path = connection.execute("SHOW search_path").fetchone()[0]
            with connection.transaction():
                connection.execute("SET search_path TO pg_catalog")
                connection.execute("SET application_name TO 'celestin-session-leak-probe'")
                connection.execute(
                    "SELECT set_config('celestin.pool_probe', 'leaked', false)"
                )
                connection.execute(
                    "SELECT pg_catalog.pg_advisory_lock(%s::bigint)", (lock_key,)
                )

        with pg.migration_connection() as observer:
            assert observer.execute(
                """SELECT EXISTS (
                       SELECT 1 FROM pg_catalog.pg_locks
                        WHERE pid = %s AND locktype = 'advisory' AND granted
                   )""",
                (backend_pid,),
            ).fetchone()[0] is False

        with pool.connection(timeout=5) as reused:
            assert reused.execute("SELECT pg_backend_pid()").fetchone()[0] == backend_pid
            assert reused.execute("SELECT current_user = session_user").fetchone()[0] is True
            assert reused.execute("SHOW search_path").fetchone()[0] == original_search_path
            assert reused.execute("SELECT current_setting('application_name')").fetchone()[0] != (
                "celestin-session-leak-probe"
            )
            custom_setting = reused.execute(
                "SELECT current_setting('celestin.pool_probe', true)"
            ).fetchone()[0]
            assert custom_setting in (None, "")
    finally:
        pool.close()


def test_sql_errors_are_redacted_and_connections_cursors_close(postgres_connections, tenant_fixture):
    pg, fixture = postgres_connections, tenant_fixture
    connection = pg.connect_authorization()
    connection.execute("SET search_path TO pg_catalog")
    factory = PostgresHostedAuthorizationSessionFactory(lambda: connection)
    with pytest.raises(HostedAuthorizationUnavailable) as error:
        factory._execute_authorized(
            fixture.resolver(), fixture.human(), fixture.project_key, "read",
            lambda *_: pytest.fail("authorization query failure must fail closed"),
        )
    assert str(error.value) == "authorization state is unavailable"
    assert "relation" not in str(error.value).lower()
    assert pg.target.authorization_dsn not in str(error.value)
    assert connection.closed
    assert connection.cursors and all(cursor.closed for cursor in connection.cursors)


def _wait_for_lock(postgres_connections, pid: int) -> bool:
    deadline = monotonic() + _LOCK_WAIT_SECONDS
    with postgres_connections.connection(autocommit=True, application_name="celestin-lock-monitor") as monitor:
        while monotonic() < deadline:
            waiting = monitor.execute(
                "SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_locks WHERE pid = %s AND NOT granted)",
                (pid,),
            ).fetchone()[0]
            if waiting:
                return True
            sleep(0.02)
    return False


@pytest.mark.parametrize(
    "revocation",
    [
        "grant",
        "membership",
        "service",
        "human_status",
        "workspace_lifecycle",
        "project_lifecycle",
        "scope_mapping",
    ],
)
def test_inflight_authorized_operation_serializes_before_revocation(
    postgres_connections, tenant_fixture, revocation
):
    """An operation holding the authorization advisory lock may finish; later requests deny."""
    pg, fixture = postgres_connections, tenant_fixture
    if revocation == "service":
        principal = fixture.service()
        update = (
            "UPDATE celestin_service_identities SET status = 'revoked', "
            "revoked_at = CURRENT_TIMESTAMP WHERE id = %s",
            (fixture.service_id,),
        )
    elif revocation == "human_status":
        principal = fixture.human()
        update = (
            "UPDATE celestin_users SET status = 'suspended' WHERE id = %s",
            (fixture.human_id,),
        )
    elif revocation == "membership":
        principal = fixture.human()
        update = (
            "UPDATE celestin_workspace_memberships SET status = 'suspended' "
            "WHERE workspace_id = %s AND user_id = %s",
            (fixture.workspace_a, fixture.human_id),
        )
    elif revocation == "workspace_lifecycle":
        principal = fixture.human()
        update = (
            "UPDATE celestin_workspaces SET archived_at = CURRENT_TIMESTAMP, "
            "purge_after = CURRENT_TIMESTAMP + INTERVAL '1 day' WHERE id = %s",
            (fixture.workspace_a,),
        )
    elif revocation == "project_lifecycle":
        principal = fixture.human()
        update = (
            "UPDATE celestin_projects SET archived_at = CURRENT_TIMESTAMP, "
            "purge_after = CURRENT_TIMESTAMP + INTERVAL '1 day' WHERE id = %s",
            (fixture.project_a,),
        )
    else:
        principal = fixture.human()
        update = (
            "UPDATE celestin_project_memberships SET revoked_at = CURRENT_TIMESTAMP "
            "WHERE workspace_id = %s AND project_id = %s AND user_id = %s",
            (fixture.workspace_a, fixture.project_a, fixture.human_id),
        )

    operation_entered = Event()
    release_operation = Event()
    revoker_started = Event()
    revoker_pid: list[int] = []
    factory = _factory(pg)

    def protected_operation():
        def operation(_session, scope):
            operation_entered.set()
            if not release_operation.wait(_LOCK_WAIT_SECONDS):
                raise TimeoutError("test synchronization timed out")
            return scope.project_id

        return factory._execute_authorized(
            fixture.resolver(), principal, fixture.project_key, "read", operation
        )

    def revoke():
        with pg.connection(application_name=f"celestin-revoker-{fixture.suffix[:8]}") as connection:
            revoker_pid.append(connection.raw.info.backend_pid)
            revoker_started.set()
            with connection.transaction():
                if revocation == "scope_mapping":
                    connection.execute(
                        "DELETE FROM celestin_private.project_runtime_scopes "
                        "WHERE login_role_name = %s",
                        (pg.target.project_a_user,),
                    )
                    connection.execute(
                        """INSERT INTO celestin_private.project_runtime_scopes
                               (login_role_name, workspace_id, project_id)
                           VALUES (%s, %s, %s)""",
                        (pg.target.project_a_user, fixture.workspace_b, fixture.project_b),
                    )
                else:
                    connection.execute(update[0], update[1])

    with ThreadPoolExecutor(max_workers=2) as executor:
        protected = executor.submit(protected_operation)
        try:
            assert operation_entered.wait(_LOCK_WAIT_SECONDS)
            revoker = executor.submit(revoke)
            assert revoker_started.wait(_LOCK_WAIT_SECONDS)
            blocked = _wait_for_lock(pg, revoker_pid[0])
        finally:
            release_operation.set()
        assert protected.result(timeout=_LOCK_WAIT_SECONDS) == fixture.project_a
        revoker.result(timeout=_LOCK_WAIT_SECONDS)

    expected_denials = (
        (HostedAuthorizationUnavailable,)
        if revocation == "scope_mapping"
        else (HostedAccessDenied,)
    )
    with pytest.raises(expected_denials):
        factory._execute_authorized(
            fixture.resolver(), principal, fixture.project_key, "read",
            lambda *_: pytest.fail("a request after committed revocation must be denied"),
        )
    assert blocked, "revocation should wait for the in-flight authorization transaction"


def test_concurrent_duplicate_grant_insert_has_one_unique_conflict(postgres_connections, tenant_fixture):
    pg, fixture = postgres_connections, tenant_fixture
    insert_sql = """INSERT INTO celestin_project_memberships
                         (workspace_id, project_id, user_id, can_read)
                     VALUES (%s, %s, %s, TRUE)"""
    params = (fixture.workspace_a, fixture.project_a, fixture.contender_user_id)
    duplicate_started = Event()
    duplicate_pid: list[int] = []

    def insert_duplicate():
        with pg.connection(application_name=f"celestin-duplicate-{fixture.suffix[:8]}") as connection:
            duplicate_pid.append(connection.raw.info.backend_pid)
            duplicate_started.set()
            try:
                with connection.transaction():
                    connection.execute(insert_sql, params)
            except pg.psycopg.Error as exc:
                return exc.sqlstate
        return None

    with ThreadPoolExecutor(max_workers=1) as executor:
        with pg.connection() as first:
            with first.transaction():
                first.execute(insert_sql, params)
                duplicate = executor.submit(insert_duplicate)
                assert duplicate_started.wait(_LOCK_WAIT_SECONDS)
                # Exiting this transaction commits the winner and releases
                # the second insert to receive the unique violation.
                blocked = _wait_for_lock(pg, duplicate_pid[0])
            sqlstate = duplicate.result(timeout=_LOCK_WAIT_SECONDS)

    assert blocked, "the conflicting insert should wait for the uncommitted unique key"
    assert sqlstate == "23505"
