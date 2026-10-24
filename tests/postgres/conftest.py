"""Opt-in real PostgreSQL fixtures; ordinary tests never contact a database."""

from __future__ import annotations

import os

import pytest

from orbit.postgres.migrations.runner import apply_migrations, load_migrations

from tests.postgres.live_support import (
    LivePostgres,
    UnsafePostgresTestTarget,
    initialize_fresh_database,
    postgres_target_from_environment,
    verify_connection_identity,
    verify_runtime_connection_identity,
)


def pytest_addoption(parser):
    parser.addoption(
        "--run-postgres-integration",
        action="store_true",
        default=False,
        help="run tests that connect to an explicitly configured disposable PostgreSQL database",
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "postgres_integration: requires explicit opt-in and a disposable PostgreSQL database",
    )
    config.addinivalue_line(
        "markers",
        "postgres_evidence_integration: separate Evidence adapter suite; not part of the hosted project-runtime RLS gate",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-postgres-integration"):
        return
    skip_live = pytest.mark.skip(
        reason=(
            "requires --run-postgres-integration and a fresh loopback disposable target "
            "configured with all CELESTIN_TEST_POSTGRES_* variables; see "
            "docs/postgres-integration-tests.md"
        )
    )
    for item in items:
        if item.get_closest_marker("postgres_integration"):
            item.add_marker(skip_live)


def _assert_fresh_database(connection) -> None:
    """Refuse object classes covered by the migration runner's fresh-db guard.

    The migration runner repeats its own authoritative fresh-database check
    while holding the migration advisory lock. This preflight gives the test
    operator an earlier, test-specific stop condition; it never removes objects.
    """
    ledger = connection.execute(
        "SELECT to_regclass('public.celestin_schema_migrations')"
    ).fetchone()[0]
    if ledger is not None:
        raise UnsafePostgresTestTarget(
            "the designated integration database already has a migration ledger"
        )

    existing_objects = connection.execute(
        """
        SELECT EXISTS (
            SELECT 1
              FROM pg_catalog.pg_namespace AS n
             WHERE n.nspname NOT IN ('pg_catalog', 'information_schema', 'public')
               AND n.nspname !~ '^pg_'
            UNION ALL
            SELECT 1
              FROM pg_catalog.pg_class AS c
              JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
             WHERE n.nspname = 'public'
               AND c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')
            UNION ALL
            SELECT 1
              FROM pg_catalog.pg_proc AS p
              JOIN pg_catalog.pg_namespace AS n ON n.oid = p.pronamespace
             WHERE n.nspname = 'public'
            UNION ALL
            SELECT 1
              FROM pg_catalog.pg_type AS t
              JOIN pg_catalog.pg_namespace AS n ON n.oid = t.typnamespace
             WHERE n.nspname = 'public' AND t.typrelid = 0
        )
        """
    ).fetchone()[0]
    if existing_objects:
        raise UnsafePostgresTestTarget(
            "the designated integration database contains unexpected user-created objects"
        )


@pytest.fixture(scope="session")
def live_postgres(request):
    """Verify a fresh local target and initialize it through the migration runner.

    This fixture is intentionally destructive only in the narrow sense that it
    creates schema objects in the explicitly named fresh integration database.
    It never drops, truncates, or cleans database objects.
    """
    if not request.config.getoption("--run-postgres-integration"):
        pytest.skip("pass --run-postgres-integration to opt in")

    missing = [
        name
        for name in (
            "CELESTIN_TEST_POSTGRES_DISPOSABLE",
            "CELESTIN_TEST_POSTGRES_DSN",
            "CELESTIN_TEST_POSTGRES_DATABASE",
            "CELESTIN_TEST_POSTGRES_USER",
            "CELESTIN_TEST_POSTGRES_HOST",
            "CELESTIN_TEST_POSTGRES_PORT",
            "CELESTIN_TEST_POSTGRES_ADMIN_DSN",
            "CELESTIN_TEST_POSTGRES_ADMIN_USER",
            "CELESTIN_TEST_POSTGRES_AUTHORIZATION_DSN",
            "CELESTIN_TEST_POSTGRES_AUTHORIZATION_USER",
            "CELESTIN_TEST_POSTGRES_PROJECT_A_DSN",
            "CELESTIN_TEST_POSTGRES_PROJECT_A_USER",
            "CELESTIN_TEST_POSTGRES_PROJECT_B_DSN",
            "CELESTIN_TEST_POSTGRES_PROJECT_B_USER",
        )
        if not os.environ.get(name, "").strip()
    ]
    if missing:
        pytest.skip(
            "approved disposable PostgreSQL target is not fully configured; missing "
            + ", ".join(missing)
        )

    try:
        target = postgres_target_from_environment(
            os.environ
        )
    except UnsafePostgresTestTarget as exc:
        pytest.fail(str(exc))

    try:
        import psycopg
    except ImportError:
        pytest.fail(
            "Psycopg is missing; install the optional extra in the validation environment with "
            '.venv\\validation\\Scripts\\python.exe -m pip install -e ".[postgres,test]"'
        )

    connection = None
    role_connections = []
    failure: str | None = None
    migration_result = None
    migrations = ()
    try:
        for dsn, expected_user, expected_group in (
            (target.admin_dsn, target.admin_user, "celestin_authorization_admin"),
            (
                target.authorization_dsn,
                target.authorization_user,
                "celestin_authorization_reader",
            ),
            (target.project_a_dsn, target.project_a_user, "celestin_project_runtime"),
            (target.project_b_dsn, target.project_b_user, "celestin_project_runtime"),
        ):
            role_connection = psycopg.connect(dsn, autocommit=True)
            role_connections.append(role_connection)
            verify_runtime_connection_identity(
                role_connection,
                target,
                expected_user=expected_user,
                expected_group=expected_group,
            )
        connection = psycopg.connect(target.dsn, autocommit=True)
        migrations = load_migrations()
        if not migrations:
            raise RuntimeError("no packaged PostgreSQL migrations were found")
        migration_result = initialize_fresh_database(
            connection,
            target,
            migrations,
            verify_identity=verify_connection_identity,
            assert_fresh=_assert_fresh_database,
            migration_runner=apply_migrations,
        )
    except UnsafePostgresTestTarget as exc:
        failure = str(exc)
    except Exception as exc:
        # Driver exceptions can contain DSNs and other connection details.
        failure = f"PostgreSQL preflight or migration failed ({type(exc).__name__}); details withheld"
    finally:
        for role_connection in role_connections:
            try:
                role_connection.close()
            except Exception:
                failure = "a PostgreSQL runtime-role preflight session could not be closed"
        if connection is not None:
            try:
                connection.close()
            except Exception:
                failure = "PostgreSQL preflight connection could not be closed; details withheld"

    if failure:
        pytest.fail(failure)

    return LivePostgres(psycopg, target, migrations, migration_result)


@pytest.fixture
def postgres_connections(live_postgres):
    """Close and verify all test connections and cursors opened by a live test."""
    start = len(live_postgres.connections())
    yield live_postgres
    opened = live_postgres.connections()[start:]
    leaked = [connection for connection in opened if not connection.closed]
    for connection in leaked:
        connection.close()
    open_cursors = [
        cursor
        for connection in opened
        for cursor in connection.cursors
        if not cursor.closed
    ]
    if leaked or open_cursors:
        pytest.fail(
            "live test leaked a PostgreSQL connection or cursor; resources were closed during cleanup"
        )
