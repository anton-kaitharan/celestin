"""Real-server authorization and transaction tests; never run by default."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from threading import Event
from time import monotonic, sleep
from uuid import UUID, uuid4

import pytest

from orbit.mcp_server.hosted_scope import (
    HostedAccessDenied,
    HostedAuthorizationUnavailable,
    HostedScopeResolver,
    HostedSourceMessageNotFound,
    VerifiedPrincipal,
)
from orbit.postgres.authorization import PostgresHostedAuthorizationSessionFactory


pytestmark = pytest.mark.postgres_integration
_LOCK_WAIT_SECONDS = 8.0


class FixedWorkspaceBinding:
    """Test-only stand-in for a trusted server-side credential binding."""

    def __init__(self, workspace_id: UUID) -> None:
        self.workspace_id = workspace_id

    def workspace_for(self, _principal: VerifiedPrincipal) -> UUID:
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
    project_other: UUID
    project_b: UUID
    human_id: UUID
    default_user_id: UUID
    contender_user_id: UUID
    service_id: UUID
    message_a: int
    message_other: int
    message_b: int
    memory_a: int
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
    """Create isolated synthetic tenants and remove only their rows afterward."""
    pg = postgres_connections
    suffix = uuid4().hex
    ids = {
        "workspace_a": uuid4(),
        "workspace_b": uuid4(),
        "project_a": uuid4(),
        "project_other": uuid4(),
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
                   VALUES (%s, %s, 'member', 'active')""",
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
            (
                ids["project_other"],
                ids["workspace_a"],
                f"project-other-{suffix}",
                f"other-key-{suffix}",
            ),
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
            (ids["workspace_a"], ids["project_other"]),
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

        message_a = _insert_message(connection, ids["workspace_a"], ids["project_a"], f"a-{suffix}")
        message_other = _insert_message(
            connection, ids["workspace_a"], ids["project_other"], f"other-{suffix}"
        )
        message_b = _insert_message(connection, ids["workspace_b"], ids["project_b"], f"b-{suffix}")
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

    fixture = TenantFixture(
        suffix=suffix,
        **ids,
        message_a=message_a,
        message_other=message_other,
        message_b=message_b,
        memory_a=int(memory_a),
        project_key=project_key,
        issuer=issuer,
        human_subject=human_subject,
        service_subject=service_subject,
    )
    try:
        yield fixture
    finally:
        with pg.connection() as connection, connection.transaction():
            project_ids = [ids["project_a"], ids["project_other"], ids["project_b"]]
            workspace_ids = [ids["workspace_a"], ids["workspace_b"]]
            user_ids = [ids["human_id"], ids["default_user_id"], ids["contender_user_id"]]
            connection.execute("DELETE FROM project_memory WHERE project_id = ANY(%s)", (project_ids,))
            connection.execute("DELETE FROM chat_messages WHERE project_id = ANY(%s)", (project_ids,))
            connection.execute(
                "DELETE FROM celestin_service_project_grants WHERE project_id = ANY(%s)",
                (project_ids,),
            )
            connection.execute(
                "DELETE FROM celestin_project_memberships WHERE project_id = ANY(%s)",
                (project_ids,),
            )
            connection.execute(
                "DELETE FROM celestin_workspace_memberships WHERE workspace_id = ANY(%s)",
                (workspace_ids,),
            )
            connection.execute("DELETE FROM celestin_projects WHERE id = ANY(%s)", (project_ids,))
            connection.execute(
                "DELETE FROM celestin_service_identities WHERE id = %s", (ids["service_id"],)
            )
            connection.execute("DELETE FROM celestin_users WHERE id = ANY(%s)", (user_ids,))
            connection.execute("DELETE FROM celestin_workspaces WHERE id = ANY(%s)", (workspace_ids,))

        with pg.connection() as verification:
            cleanup_checks = (
                ("SELECT count(*) FROM project_memory WHERE project_id = ANY(%s)", (project_ids,)),
                ("SELECT count(*) FROM chat_messages WHERE project_id = ANY(%s)", (project_ids,)),
                (
                    "SELECT count(*) FROM celestin_service_project_grants WHERE project_id = ANY(%s)",
                    (project_ids,),
                ),
                (
                    "SELECT count(*) FROM celestin_project_memberships WHERE project_id = ANY(%s)",
                    (project_ids,),
                ),
                (
                    "SELECT count(*) FROM celestin_workspace_memberships WHERE workspace_id = ANY(%s)",
                    (workspace_ids,),
                ),
                ("SELECT count(*) FROM celestin_projects WHERE id = ANY(%s)", (project_ids,)),
                (
                    "SELECT count(*) FROM celestin_service_identities WHERE id = %s",
                    (ids["service_id"],),
                ),
                ("SELECT count(*) FROM celestin_users WHERE id = ANY(%s)", (user_ids,)),
                ("SELECT count(*) FROM celestin_workspaces WHERE id = ANY(%s)", (workspace_ids,)),
            )
            for statement, params in cleanup_checks:
                assert verification.execute(statement, params).fetchone()[0] == 0, (
                    "synthetic PostgreSQL fixture cleanup left rows in a scoped table"
                )


def _factory(pg) -> PostgresHostedAuthorizationSessionFactory:
    return PostgresHostedAuthorizationSessionFactory(pg.connect)


def _fetchone(pg, query: str, params=()):
    with pg.connection() as connection:
        return connection.execute(query, params).fetchone()


def _expect_sqlstate(pg, statement: str, params, expected: str) -> None:
    with pg.connection() as connection, connection.transaction():
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
        _factory(pg).execute_authorized(
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
        _factory(pg).execute_authorized(
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
        _factory(pg).execute_authorized(
            fixture.resolver(), principal, fixture.project_key, "read",
            lambda session, scope: pytest.fail("suspended user operation must not run"),
        )


def test_human_explicit_read_write_grants_and_revocation(postgres_connections, tenant_fixture):
    pg, fixture = postgres_connections, tenant_fixture
    resolver = fixture.resolver()
    factory = _factory(pg)
    principal = fixture.human()
    assert factory.execute_authorized(
        resolver, principal, fixture.project_key, "read", lambda _session, scope: scope.project_id
    ) == fixture.project_a
    assert factory.execute_authorized(
        resolver, principal, fixture.project_key, "write", lambda _session, scope: scope.project_id
    ) == fixture.project_a

    with pg.connection() as connection, connection.transaction():
        connection.execute(
            "UPDATE celestin_project_memberships SET can_write = FALSE "
            "WHERE project_id = %s AND user_id = %s",
            (fixture.project_a, fixture.human_id),
        )
    with pytest.raises(HostedAccessDenied):
        factory.execute_authorized(
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
        factory.execute_authorized(
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
        factory.execute_authorized(
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
        factory.execute_authorized(
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
        factory.execute_authorized(
            resolver, fixture.human(), fixture.project_key, "read",
            lambda *_: pytest.fail("missing explicit grant must not read"),
        )


def test_service_identity_grant_and_revocation(postgres_connections, tenant_fixture):
    pg, fixture = postgres_connections, tenant_fixture
    factory, resolver, principal = _factory(pg), fixture.resolver(), fixture.service()
    assert factory.execute_authorized(
        resolver, principal, fixture.project_key, "write", lambda _session, scope: scope.project_id
    ) == fixture.project_a

    with pg.connection() as connection, connection.transaction():
        connection.execute(
            "UPDATE celestin_service_project_grants SET revoked_at = CURRENT_TIMESTAMP "
            "WHERE project_id = %s AND service_identity_id = %s",
            (fixture.project_a, fixture.service_id),
        )
    with pytest.raises(HostedAccessDenied):
        factory.execute_authorized(
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
        factory.execute_authorized(
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
        factory.execute_authorized(
            resolver, principal, fixture.project_key, "read",
            lambda *_: pytest.fail("revoked service identity must not read"),
        )


def test_archived_resources_and_same_public_key_are_workspace_scoped(postgres_connections, tenant_fixture):
    pg, fixture = postgres_connections, tenant_fixture
    factory, principal = _factory(pg), fixture.human()
    first = factory.execute_authorized(
        fixture.resolver(fixture.workspace_a), principal, fixture.project_key, "read",
        lambda _session, scope: scope.project_id,
    )
    second = factory.execute_authorized(
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
        factory.execute_authorized(
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
        factory.execute_authorized(
            fixture.resolver(fixture.workspace_a), principal, fixture.project_key, "read",
            lambda *_: pytest.fail("archived workspace must not read"),
        )


def test_source_message_reference_is_scoped_and_same_project_decision_succeeds(
    postgres_connections, tenant_fixture
):
    pg, fixture = postgres_connections, tenant_fixture
    factory, resolver, principal = _factory(pg), fixture.resolver(), fixture.human()
    result = factory.execute_authorized(
        resolver,
        principal,
        fixture.project_key,
        "write",
        lambda session, scope: (
            scope.project_id,
            session.connection.execute(
                "SELECT id FROM chat_messages WHERE workspace_id = %s AND project_id = %s AND id = %s",
                (scope.workspace_id, scope.project_id, fixture.message_a),
            ).fetchone()[0],
        ),
        source_message_id=str(fixture.message_a),
    )
    assert result == (fixture.project_a, fixture.message_a)

    missing_message_id = max(fixture.message_a, fixture.message_other, fixture.message_b) + 1000
    for out_of_scope_id in (fixture.message_other, fixture.message_b, missing_message_id):
        with pytest.raises(HostedSourceMessageNotFound):
            factory.execute_authorized(
                resolver, principal, fixture.project_key, "write",
                lambda *_: pytest.fail("invalid source reference must stop before write"),
                source_message_id=str(out_of_scope_id),
            )

    _expect_sqlstate(
        pg,
        """INSERT INTO project_memory
               (workspace_id, project_id, key, value, status, source_message_id, created_at, updated_at)
           VALUES (%s, %s, %s, 'synthetic invalid reference', 'draft', %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
        (fixture.workspace_a, fixture.project_a, f"cross-project-{fixture.suffix}", fixture.message_other),
        "23503",
    )
    _expect_sqlstate(
        pg,
        """INSERT INTO project_memory
               (workspace_id, project_id, key, value, status, source_message_id, created_at, updated_at)
           VALUES (%s, %s, %s, 'synthetic invalid reference', 'draft', %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
        (fixture.workspace_a, fixture.project_a, f"missing-source-{fixture.suffix}", missing_message_id),
        "23503",
    )
    _expect_sqlstate(
        pg,
        """INSERT INTO project_memory
               (workspace_id, project_id, key, value, status, source_message_id, created_at, updated_at)
           VALUES (%s, %s, %s, 'synthetic invalid reference', 'draft', %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
        (fixture.workspace_a, fixture.project_a, f"missing-source-{fixture.suffix}", 987654321),
        "23503",
    )
    _expect_sqlstate(
        pg,
        """INSERT INTO project_memory
               (workspace_id, project_id, key, value, status, source_message_id, created_at, updated_at)
           VALUES (%s, %s, %s, 'synthetic invalid reference', 'draft', %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
        (fixture.workspace_a, fixture.project_a, f"cross-workspace-{fixture.suffix}", fixture.message_b),
        "23503",
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
    )
    assert _fetchone(
        pg,
        "SELECT source_message_id FROM project_memory WHERE id = %s AND project_id = %s",
        (fixture.memory_a, fixture.project_a),
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
        _expect_sqlstate(pg, statement, params, "23514")


def test_workspace_can_be_created_without_an_active_owner(postgres_connections, tenant_fixture):
    """The current schema accepts this state; the invariant needs app-level enforcement."""
    pg, fixture = postgres_connections, tenant_fixture
    workspace_id = uuid4()
    with pg.connection() as connection, connection.transaction():
        connection.execute(
            "INSERT INTO celestin_workspaces (id, slug, name) VALUES (%s, %s, %s)",
            (workspace_id, f"ownerless-{fixture.suffix}", "Synthetic ownerless-policy probe"),
        )
        assert connection.execute(
            """SELECT count(*) FROM celestin_workspace_memberships
                 WHERE workspace_id = %s AND role = 'owner' AND status = 'active'""",
            (workspace_id,),
        ).fetchone()[0] == 0
        connection.execute("DELETE FROM celestin_workspaces WHERE id = %s", (workspace_id,))


def test_authorized_write_commits_and_failed_operation_rolls_back(postgres_connections, tenant_fixture):
    pg, fixture = postgres_connections, tenant_fixture
    opened: list = []

    def open_request_connection():
        connection = pg.connect()
        opened.append(connection)
        return connection

    factory = PostgresHostedAuthorizationSessionFactory(open_request_connection)
    resolver, principal = fixture.resolver(), fixture.human()
    committed_key = f"committed-{fixture.suffix}"
    backend_pid = None
    transaction_id = None

    def save(session, scope):
        nonlocal backend_pid, transaction_id
        assert opened and session.connection is opened[-1]
        backend_pid = session.connection.raw.info.backend_pid
        transaction_id = session.connection.execute("SELECT txid_current()").fetchone()[0]
        return session.connection.execute(
            """INSERT INTO project_memory
                   (workspace_id, project_id, key, value, status, source_message_id,
                    created_at, updated_at)
               VALUES (%s, %s, %s, 'synthetic committed result', 'draft', %s,
                       CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
               RETURNING id""",
            (scope.workspace_id, scope.project_id, committed_key, fixture.message_a),
        ).fetchone()[0]

    committed_id = factory.execute_authorized(
        resolver, principal, fixture.project_key, "write", save,
        source_message_id=str(fixture.message_a),
    )
    assert _fetchone(
        pg, "SELECT id FROM project_memory WHERE id = %s", (committed_id,)
    )[0] == committed_id
    assert opened[-1].closed
    assert all(cursor.closed for cursor in opened[-1].cursors)

    rolled_back_conversation = f"rollback-{fixture.suffix}"

    def fail_after_insert(session, scope):
        assert session.connection.raw.info.backend_pid != 0
        assert session.connection.execute("SELECT txid_current()").fetchone()[0]
        assert session.connection is opened[-1]
        session.connection.execute(
            """INSERT INTO chat_messages
                   (workspace_id, project_id, conversation_id, role, content, created_at)
               VALUES (%s, %s, %s, 'assistant', 'synthetic rollback probe', CURRENT_TIMESTAMP)""",
            (scope.workspace_id, scope.project_id, rolled_back_conversation),
        )
        raise RuntimeError("synthetic operation failure")

    with pytest.raises(HostedAuthorizationUnavailable, match="authorized operation could not be completed"):
        factory.execute_authorized(
            resolver, principal, fixture.project_key, "write", fail_after_insert
        )
    assert _fetchone(
        pg,
        "SELECT count(*) FROM chat_messages WHERE project_id = %s AND conversation_id = %s",
        (fixture.project_a, rolled_back_conversation),
    )[0] == 0
    assert backend_pid is not None and transaction_id is not None
    assert opened[-1].closed
    assert all(cursor.closed for cursor in opened[-1].cursors)


def test_sql_errors_are_redacted_and_connections_cursors_close(postgres_connections, tenant_fixture):
    pg, fixture = postgres_connections, tenant_fixture
    connection = pg.connect()
    connection.execute("SET search_path TO pg_catalog")
    factory = PostgresHostedAuthorizationSessionFactory(lambda: connection)
    with pytest.raises(HostedAuthorizationUnavailable) as error:
        factory.execute_authorized(
            fixture.resolver(), fixture.human(), fixture.project_key, "read",
            lambda *_: pytest.fail("authorization query failure must fail closed"),
        )
    assert str(error.value) == "authorization state is unavailable"
    assert "relation" not in str(error.value).lower()
    assert pg.target.dsn not in str(error.value)
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


@pytest.mark.parametrize("revocation", ["grant", "membership", "service"])
def test_inflight_authorized_operation_serializes_before_revocation(
    postgres_connections, tenant_fixture, revocation
):
    """An operation holding FOR SHARE may finish; later requests see the revocation."""
    pg, fixture = postgres_connections, tenant_fixture
    if revocation == "service":
        principal = fixture.service()
        update = (
            "UPDATE celestin_service_identities SET status = 'revoked', "
            "revoked_at = CURRENT_TIMESTAMP WHERE id = %s",
            (fixture.service_id,),
        )
    elif revocation == "membership":
        principal = fixture.human()
        update = (
            "UPDATE celestin_workspace_memberships SET status = 'suspended' "
            "WHERE workspace_id = %s AND user_id = %s",
            (fixture.workspace_a, fixture.human_id),
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

        return factory.execute_authorized(
            fixture.resolver(), principal, fixture.project_key, "read", operation
        )

    def revoke():
        with pg.connection(application_name=f"celestin-revoker-{fixture.suffix[:8]}") as connection:
            revoker_pid.append(connection.raw.info.backend_pid)
            revoker_started.set()
            with connection.transaction():
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

    with pytest.raises(HostedAccessDenied):
        factory.execute_authorized(
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
