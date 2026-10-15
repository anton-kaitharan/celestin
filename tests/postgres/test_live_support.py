"""Pure safety-policy tests for the opt-in PostgreSQL target parser."""

from __future__ import annotations

import pytest

from tests.postgres.live_support import (
    TrackedConnection,
    UnsafePostgresTestTarget,
    initialize_fresh_database,
    postgres_target_from_environment,
    server_address_matches_target,
    verify_connection_identity,
)


NONCE = "a1b2c3d4"


def target_environment(**changes):
    values = {
        "CELESTIN_TEST_POSTGRES_DSN": (
            f"host=127.0.0.1 port=5432 dbname=celestin_it_20261014_{NONCE} "
            f"user=celestin_it_user_{NONCE}"
        ),
        "CELESTIN_TEST_POSTGRES_DATABASE": f"celestin_it_20261014_{NONCE}",
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

    def execute(self, query):
        self.query = query
        return self

    def fetchone(self):
        return self.identity


def _safe_identity(**overrides):
    identity = [
        "celestin_it_20261014_a1b2c3d4",
        "celestin_it_user_a1b2c3d4",
        "127.0.0.1/32",
        5432,
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
        "host": 2,
        "port": 3,
        "superuser": 4,
        "create_roles": 5,
        "create_databases": 6,
        "replication": 7,
        "bypass_rls": 8,
        "disallowed_memberships": 9,
    }
    for key, value in overrides.items():
        identity[positions[key]] = value
    return tuple(identity)


def test_identity_accepts_scoped_non_superuser_role():
    connection = _IdentityConnection(_safe_identity())
    target = postgres_target_from_environment(target_environment())

    verify_connection_identity(connection, target)

    assert "pg_database_owner" in connection.query
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
