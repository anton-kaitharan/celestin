"""Pure safety-policy tests for the opt-in PostgreSQL target parser."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.postgres.live_support import (
    TrackedConnection,
    UnsafePostgresTestTarget,
    initialize_fresh_database,
    postgres_target_from_environment,
    server_address_matches_target,
    verify_connection_identity,
    verify_runtime_connection_identity,
)


NONCE = "a1b2c3d4"


def target_environment(**changes):
    database = f"celestin_it_20261014_{NONCE}"
    values = {
        "CELESTIN_TEST_POSTGRES_DISPOSABLE": "true",
        "CELESTIN_TEST_POSTGRES_DSN": (
            f"host=127.0.0.1 port=5432 dbname={database} "
            f"user=celestin_it_user_{NONCE}"
        ),
        "CELESTIN_TEST_POSTGRES_ADMIN_DSN": (
            f"host=127.0.0.1 port=5432 dbname={database} user=celestin_it_admin_{NONCE}"
        ),
        "CELESTIN_TEST_POSTGRES_ADMIN_USER": f"celestin_it_admin_{NONCE}",
        "CELESTIN_TEST_POSTGRES_AUTHORIZATION_DSN": (
            f"host=127.0.0.1 port=5432 dbname={database} user=celestin_it_reader_{NONCE}"
        ),
        "CELESTIN_TEST_POSTGRES_AUTHORIZATION_USER": f"celestin_it_reader_{NONCE}",
        "CELESTIN_TEST_POSTGRES_PROJECT_A_DSN": (
            f"host=127.0.0.1 port=5432 dbname={database} user=celestin_it_project_a_{NONCE}"
        ),
        "CELESTIN_TEST_POSTGRES_PROJECT_A_USER": f"celestin_it_project_a_{NONCE}",
        "CELESTIN_TEST_POSTGRES_PROJECT_B_DSN": (
            f"host=127.0.0.1 port=5432 dbname={database} user=celestin_it_project_b_{NONCE}"
        ),
        "CELESTIN_TEST_POSTGRES_PROJECT_B_USER": f"celestin_it_project_b_{NONCE}",
        "CELESTIN_TEST_POSTGRES_DATABASE": database,
        "CELESTIN_TEST_POSTGRES_USER": f"celestin_it_user_{NONCE}",
        "CELESTIN_TEST_POSTGRES_HOST": "127.0.0.1",
        "CELESTIN_TEST_POSTGRES_PORT": "5432",
    }
    values.update(changes)
    return values


def test_accepts_only_explicit_matching_loopback_target_and_hides_dsn():
    target = postgres_target_from_environment(target_environment())

    assert target.database == f"celestin_it_20261014_{NONCE}"
    assert target.user == f"celestin_it_user_{NONCE}"
    assert target.host == "127.0.0.1"
    assert target.port == 5432
    assert target.dsn not in repr(target)
    assert target.admin_dsn not in repr(target)
    assert target.authorization_dsn not in repr(target)
    assert target.project_a_dsn not in repr(target)
    assert target.project_b_dsn not in repr(target)
    assert "password" not in repr(target).lower()


def test_rejects_the_previously_failed_target_even_with_matching_coordinates():
    database = "celestin_it_20261014_a7f3c9"
    values = target_environment(
        CELESTIN_TEST_POSTGRES_DSN=(
            f"host=127.0.0.1 port=5432 dbname={database} user={database}"
        ),
        CELESTIN_TEST_POSTGRES_DATABASE=database,
        CELESTIN_TEST_POSTGRES_USER=database,
    )

    with pytest.raises(UnsafePostgresTestTarget, match="test database"):
        postgres_target_from_environment(values)


@pytest.mark.parametrize(
    "database",
    [
        "celestin_it_20261015_b364bbae",
        "celestin_it_20261015_10ac5ae9",
        "celestin_it_20261015_0b8e767c",
    ],
)
def test_rejects_retired_targets_even_with_matching_coordinates(database):
    nonce = database.rsplit("_", 1)[-1]
    user = f"celestin_it_user_{nonce}"
    values = target_environment(
        CELESTIN_TEST_POSTGRES_DSN=(
            f"host=127.0.0.1 port=5432 dbname={database} user={user}"
        ),
        CELESTIN_TEST_POSTGRES_DATABASE=database,
        CELESTIN_TEST_POSTGRES_USER=user,
    )

    with pytest.raises(UnsafePostgresTestTarget, match="test database"):
        postgres_target_from_environment(values)


def test_schema_validation_script_requires_a_named_non_retired_target():
    script = (
        Path(__file__).with_name("validate_hosted_schema.sql")
        .read_text(encoding="utf-8")
    )

    assert r"\if :{?approved_test_database}" in script
    assert "current_database() = :'approved_test_database'" in script
    assert "^celestin_it_[0-9]{8}_[0-9a-f]{8}$" in script
    for retired_database in (
        "celestin_schema_test_v2",
        "celestin_it_20261014_a7f3c9",
        "celestin_it_20261015_b364bbae",
        "celestin_it_20261015_10ac5ae9",
        "celestin_it_20261015_0b8e767c",
    ):
        assert f"'{retired_database}'" in script
    assert "-d celestin_schema_test_v2" not in script
    assert "expected celestin_schema_test_v2" not in script


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"CELESTIN_TEST_POSTGRES_DATABASE": "celestin_schema_test_v2"}, "test database"),
        ({"CELESTIN_TEST_POSTGRES_DATABASE": "celestin_prod"}, "test database"),
        ({"CELESTIN_TEST_POSTGRES_DATABASE": "celestin_it_20261014_b7f3c9"}, "test database"),
        ({"CELESTIN_TEST_POSTGRES_DATABASE": "celestin_it_20261014_a7f3c9"}, "test database"),
        ({"CELESTIN_TEST_POSTGRES_USER": "postgres"}, "test role"),
        ({"CELESTIN_TEST_POSTGRES_USER": "celestin_it_user_ffffffff"}, "same eight-hex nonce"),
        ({"CELESTIN_TEST_POSTGRES_HOST": "192.0.2.50"}, "loopback"),
        ({"CELESTIN_TEST_POSTGRES_PORT": "5433"}, "configured target"),
        (
            {
                "CELESTIN_TEST_POSTGRES_DSN": (
                    "host=127.0.0.1 port=5432 dbname=other "
                    "user=celestin_it_user_a1b2c3d4"
                )
            },
            "configured target",
        ),
    ],
)
def test_rejects_unsafe_or_mismatched_target(changes, message):
    with pytest.raises(UnsafePostgresTestTarget, match=message):
        postgres_target_from_environment(target_environment(**changes))


def test_requires_explicit_disposable_database_acknowledgement():
    with pytest.raises(UnsafePostgresTestTarget, match="DISPOSABLE must explicitly be true"):
        postgres_target_from_environment(
            target_environment(CELESTIN_TEST_POSTGRES_DISPOSABLE="false")
        )


def test_requires_distinct_preprovisioned_runtime_identities():
    values = target_environment(
        CELESTIN_TEST_POSTGRES_PROJECT_B_USER=f"celestin_it_project_a_{NONCE}",
        CELESTIN_TEST_POSTGRES_PROJECT_B_DSN=(
            f"host=127.0.0.1 port=5432 dbname=celestin_it_20261014_{NONCE} "
            f"user=celestin_it_project_a_{NONCE}"
        ),
    )
    with pytest.raises(UnsafePostgresTestTarget, match="roles must be distinct"):
        postgres_target_from_environment(values)


def test_rejects_runtime_dsn_with_wrong_role_even_if_target_matches():
    values = target_environment(
        CELESTIN_TEST_POSTGRES_PROJECT_A_DSN=(
            f"host=127.0.0.1 port=5432 dbname=celestin_it_20261014_{NONCE} "
            f"user=celestin_it_project_b_{NONCE}"
        )
    )
    with pytest.raises(UnsafePostgresTestTarget, match="distinct role"):
        postgres_target_from_environment(values)


def test_missing_target_field_is_rejected_without_database_fallback():
    values = target_environment()
    del values["CELESTIN_TEST_POSTGRES_DSN"]

    with pytest.raises(UnsafePostgresTestTarget, match="CELESTIN_TEST_POSTGRES_DSN must be set"):
        postgres_target_from_environment(values)


@pytest.mark.parametrize(
    "dsn, message",
    [
        (
            "host=127.0.0.1 port=5432 dbname=celestin_it_20261014_a1b2c3d4 "
            "user=celestin_it_user_a1b2c3d4 password=do-not-echo",
            "password parameters",
        ),
        (
            "host=127.0.0.1 port=5432 dbname=celestin_it_20261014_a1b2c3d4 "
            "user=celestin_it_user_a1b2c3d4 sslpassword=do-not-echo",
            "password parameters",
        ),
        (
            "host=127.0.0.1 port=5432 dbname=celestin_it_20261014_a1b2c3d4 "
            "user=celestin_it_user_a1b2c3d4 application_name=unexpected",
            "exactly host, port, dbname, and user",
        ),
        (
            "host=127.0.0.1 host=127.0.0.1 port=5432 "
            "dbname=celestin_it_20261014_a1b2c3d4 user=celestin_it_user_a1b2c3d4",
            "duplicate parameter",
        ),
        (
            "host=127.0.0.1 host=127.0.0.2 port=5432 "
            "dbname=celestin_it_20261014_a1b2c3d4 user=celestin_it_user_a1b2c3d4",
            "duplicate parameter",
        ),
        ("postgresql://user:do-not-echo@127.0.0.1:5432/db", "malformed"),
        ("host='127.0.0.1' port=5432", "malformed"),
        ("host= port=5432", "malformed"),
        (
            "host=127.0.0.1 port=bad dbname=celestin_it_20261014_a1b2c3d4 "
            "user=celestin_it_user_a1b2c3d4",
            "configured target",
        ),
    ],
)
def test_rejects_password_unknown_duplicate_and_malformed_dsns_without_echoing_secrets(
    dsn, message
):
    values = target_environment(CELESTIN_TEST_POSTGRES_DSN=dsn)
    with pytest.raises(UnsafePostgresTestTarget, match=message) as error:
        postgres_target_from_environment(values)
    assert "do-not-echo" not in str(error.value)


@pytest.mark.parametrize(
    "observed, expected",
    [
        ("127.0.0.1", "127.0.0.1"),
        ("127.0.0.1/32", "127.0.0.1"),
        ("::1", "::1"),
        ("::1/128", "::1"),
    ],
)
def test_server_address_matches_only_the_configured_loopback(observed, expected):
    assert server_address_matches_target(observed, expected)


@pytest.mark.parametrize(
    "observed, expected",
    [
        ("192.0.2.1", "127.0.0.1"),
        ("192.0.2.1/32", "192.0.2.1"),
        ("127.0.0.2/32", "127.0.0.1"),
        ("not-an-ip", "127.0.0.1"),
        (None, "127.0.0.1"),
        ("127.0.0.1/999", "127.0.0.1"),
        ("127.0.0.1", "not-an-ip"),
    ],
)
def test_server_address_rejects_non_loopback_or_malformed_values(observed, expected):
    assert not server_address_matches_target(observed, expected)


class _IdentityConnection:
    def __init__(self, identity):
        self.identity = identity
        self.query = None

    def execute(self, query, _params=()):
        self.query = query
        return self

    def fetchone(self):
        return self.identity


def _safe_identity(**overrides):
    identity = [
        "celestin_it_20261014_a1b2c3d4",
        "celestin_it_user_a1b2c3d4",
        "celestin_it_user_a1b2c3d4",
        "127.0.0.1/32",
        5432,
        True,  # database owner
        False,  # SUPERUSER
        False,  # CREATEROLE
        False,  # CREATEDB
        False,  # REPLICATION
        False,  # BYPASSRLS
        False,  # disallowed role memberships
    ]
    positions = {
        "database": 0,
        "user": 1,
        "session_user": 2,
        "host": 3,
        "port": 4,
        "owns_database": 5,
        "superuser": 6,
        "create_roles": 7,
        "create_databases": 8,
        "replication": 9,
        "bypass_rls": 10,
        "disallowed_memberships": 11,
    }
    for key, value in overrides.items():
        identity[positions[key]] = value
    return tuple(identity)


def test_identity_accepts_scoped_non_superuser_role():
    connection = _IdentityConnection(_safe_identity())
    target = postgres_target_from_environment(target_environment())

    verify_connection_identity(connection, target)

    assert "pg_database_owner" in connection.query
    assert "database.datdba = role.oid AS owns_database" in connection.query
    assert "pg_catalog.pg_has_role(current_user, granted_role.oid, 'MEMBER')" in connection.query


@pytest.mark.parametrize(
    "attribute",
    ["superuser", "create_roles", "create_databases", "replication", "bypass_rls"],
)
def test_identity_rejects_elevated_role_attributes(attribute):
    connection = _IdentityConnection(_safe_identity(**{attribute: True}))
    target = postgres_target_from_environment(target_environment())

    with pytest.raises(UnsafePostgresTestTarget, match="role privileges"):
        verify_connection_identity(connection, target)


def test_identity_rejects_disallowed_role_memberships():
    connection = _IdentityConnection(_safe_identity(disallowed_memberships=True))
    target = postgres_target_from_environment(target_environment())

    with pytest.raises(UnsafePostgresTestTarget, match="role privileges"):
        verify_connection_identity(connection, target)


def test_identity_rejects_migration_login_that_does_not_own_target_database():
    connection = _IdentityConnection(_safe_identity(owns_database=False))
    target = postgres_target_from_environment(target_environment())

    with pytest.raises(UnsafePostgresTestTarget, match="role privileges"):
        verify_connection_identity(connection, target)


def test_identity_rejects_current_role_that_differs_from_authenticated_session_user():
    connection = _IdentityConnection(
        _safe_identity(session_user="another_role_a1b2c3d4")
    )
    target = postgres_target_from_environment(target_environment())

    with pytest.raises(UnsafePostgresTestTarget, match="identity or role privileges"):
        verify_connection_identity(connection, target)


class _RuntimeIdentityConnection:
    def __init__(self, identity):
        self.identity = identity
        self.query = None

    def execute(self, query, _params=()):
        self.query = query
        return self

    def fetchone(self):
        return self.identity


def _safe_runtime_identity(**overrides):
    identity = [
        "celestin_it_20261014_a1b2c3d4",
        "celestin_it_project_a_a1b2c3d4",
        "celestin_it_project_a_a1b2c3d4",
        "127.0.0.1/32",
        5432,
        False, False, False, False, False, True,
        True, True, True, True, True,
    ]
    positions = {
        "database": 0, "current_user": 1, "session_user": 2,
        "host": 3, "port": 4, "superuser": 5, "create_roles": 6,
        "create_databases": 7, "replication": 8, "bypass_rls": 9,
        "inherit": 10, "expected_group": 11, "other_groups": 12,
        "admin_option": 13, "owns_table": 14, "can_create_schema": 15,
    }
    for key, value in overrides.items():
        identity[positions[key]] = value
    return tuple(identity)


def test_runtime_identity_accepts_only_the_expected_direct_project_login():
    target = postgres_target_from_environment(target_environment())
    connection = _RuntimeIdentityConnection(_safe_runtime_identity())
    verify_runtime_connection_identity(
        connection,
        target,
        expected_user=target.project_a_user,
        expected_group="celestin_project_runtime",
    )
    assert "membership.admin_option" in connection.query


@pytest.mark.parametrize(
    "attribute, value",
    [
        ("database", "different_db"),
        ("current_user", "another_role"),
        ("session_user", "another_role"),
        ("host", "192.0.2.10"),
        ("port", 5433),
        ("superuser", True),
        ("create_roles", True),
        ("create_databases", True),
        ("replication", True),
        ("bypass_rls", True),
        ("inherit", False),
        ("expected_group", False),
        ("other_groups", False),
        ("admin_option", False),
        ("owns_table", False),
        ("can_create_schema", False),
    ],
)
def test_runtime_identity_rejects_wrong_target_or_unsafe_role(attribute, value):
    target = postgres_target_from_environment(target_environment())
    connection = _RuntimeIdentityConnection(_safe_runtime_identity(**{attribute: value}))

    with pytest.raises(UnsafePostgresTestTarget, match="isolated test role"):
        verify_runtime_connection_identity(
            connection,
            target,
            expected_user=target.project_a_user,
            expected_group="celestin_project_runtime",
        )


@pytest.mark.parametrize(
    "attribute, value",
    [("database", "other_db"), ("user", "other_user"), ("host", "192.0.2.5"), ("port", 5433)],
)
def test_identity_still_rejects_target_mismatches(attribute, value):
    connection = _IdentityConnection(_safe_identity(**{attribute: value}))
    target = postgres_target_from_environment(target_environment())

    with pytest.raises(UnsafePostgresTestTarget, match="identity or role privileges"):
        verify_connection_identity(connection, target)


class _MigrationResult:
    def __init__(self, applied_versions, current_version):
        self.applied_versions = tuple(applied_versions)
        self.current_version = current_version


class _LedgerConnection:
    def __init__(self, ledger_rows):
        self.ledger_rows = ledger_rows
        self.queries = []

    def execute(self, query):
        self.queries.append(query)
        return self

    def fetchall(self):
        return self.ledger_rows


def test_fresh_target_checks_identity_and_emptiness_before_one_migration_run():
    from types import SimpleNamespace

    migrations = (
        SimpleNamespace(version=1, name="0001_base.sql", checksum="abc"),
        SimpleNamespace(version=2, name="0002_status.sql", checksum="def"),
    )
    connection = _LedgerConnection([(1, "0001_base.sql", "abc"), (2, "0002_status.sql", "def")])
    events = []

    def runner(_connection, loaded):
        events.append(("migration", loaded))
        return _MigrationResult((1, 2), 2)

    result = initialize_fresh_database(
        connection,
        object(),
        migrations,
        verify_identity=lambda *_: events.append(("identity",)),
        assert_fresh=lambda *_: events.append(("fresh",)),
        migration_runner=runner,
    )

    assert [event[0] for event in events] == ["identity", "fresh", "migration"]
    assert result.applied_versions == (1, 2)
    assert len([event for event in events if event[0] == "migration"]) == 1
    assert len(connection.queries) == 1


def test_fresh_target_never_migrates_after_failed_identity_or_empty_check():
    migration_calls = []
    migrations = (type("M", (), {"version": 1, "name": "0001.sql", "checksum": "abc"})(),)
    for failed_check in ("identity", "fresh"):
        def fail(*_args):
            raise UnsafePostgresTestTarget(f"{failed_check} failed")

        kwargs = {
            "verify_identity": fail if failed_check == "identity" else lambda *_: None,
            "assert_fresh": fail if failed_check == "fresh" else lambda *_: None,
            "migration_runner": lambda *_: migration_calls.append("run"),
        }
        with pytest.raises(UnsafePostgresTestTarget):
            initialize_fresh_database(_LedgerConnection([]), object(), migrations, **kwargs)
    assert migration_calls == []


class _TrackedCursor:
    def __init__(self, *, closed=False, fail_close=False):
        self.closed = closed
        self.fail_close = fail_close
        self.close_calls = 0

    def close(self):
        self.close_calls += 1
        if self.fail_close:
            raise RuntimeError("cursor close failed")
        self.closed = True


class _TrackedRawConnection:
    def __init__(self, *cursors):
        self.closed = False
        self.close_calls = 0
        self._cursors = iter(cursors)

    def execute(self, *_args, **_kwargs):
        return next(self._cursors)

    def close(self):
        self.close_calls += 1
        self.closed = True


def test_tracked_connection_closes_open_cursors_and_is_idempotent():
    open_cursor = _TrackedCursor()
    closed_cursor = _TrackedCursor(closed=True)
    raw = _TrackedRawConnection(open_cursor, closed_cursor)
    connection = TrackedConnection(raw)
    connection.execute("SELECT 1")
    connection.execute("SELECT 2")

    connection.close()
    connection.close()

    assert open_cursor.closed
    assert open_cursor.close_calls == 1
    assert closed_cursor.closed
    assert closed_cursor.close_calls == 0
    assert raw.closed
    assert raw.close_calls == 1


def test_tracked_connection_closes_raw_connection_even_when_cursor_close_fails():
    failing_cursor = _TrackedCursor(fail_close=True)
    following_cursor = _TrackedCursor()
    raw = _TrackedRawConnection(failing_cursor, following_cursor)
    connection = TrackedConnection(raw)
    connection.execute("SELECT 1")
    connection.execute("SELECT 2")

    with pytest.raises(RuntimeError, match="cursor close failed"):
        connection.close()

    assert failing_cursor.close_calls == 1
    assert following_cursor.closed
    assert following_cursor.close_calls == 1
    assert raw.closed
    assert raw.close_calls == 1
