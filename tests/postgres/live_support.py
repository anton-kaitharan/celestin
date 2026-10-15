"""Safety checks and connection tracking for opt-in PostgreSQL tests."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import ipaddress
import re
from typing import Callable, Mapping


class UnsafePostgresTestTarget(ValueError):
    """The configured target does not satisfy the live-test safety policy."""


_DATABASE_NAME = re.compile(r"^celestin_it_[0-9]{8}_[a-f0-9]{8}$")
_ROLE_NAME = re.compile(r"^celestin_it_user_[a-f0-9]{8}$")
_UNUSABLE_TEST_DATABASES = frozenset(
    {
        "celestin_schema_test_v2",
        "celestin_it_20261014_a7f3c9",
        "celestin_it_20261015_b364bbae",
        "celestin_it_20261015_10ac5ae9",
    }
)
_DSN_KEY = re.compile(r"^[a-z][a-z0-9_]*$")
_DSN_VALUE = re.compile(r"^[A-Za-z0-9_.:-]+$")
_ALLOWED_DSN_KEYS = frozenset({"host", "port", "dbname", "user"})
_PASSWORD_DSN_KEYS = frozenset({"password", "sslpassword"})
_REQUIRED_ENV = (
    "CELESTIN_TEST_POSTGRES_DSN",
    "CELESTIN_TEST_POSTGRES_DATABASE",
    "CELESTIN_TEST_POSTGRES_USER",
    "CELESTIN_TEST_POSTGRES_HOST",
    "CELESTIN_TEST_POSTGRES_PORT",
)
_IDENTITY_SQL = """
SELECT current_database(), current_user, inet_server_addr()::text,
       inet_server_port(), role.rolsuper, role.rolcreaterole,
       role.rolcreatedb, role.rolreplication, role.rolbypassrls,
       EXISTS (
           SELECT 1
             FROM pg_catalog.pg_roles AS granted_role
            WHERE granted_role.oid <> role.oid
              AND granted_role.rolname <> 'pg_database_owner'
              AND pg_catalog.pg_has_role(current_user, granted_role.oid, 'MEMBER')
       ) AS has_disallowed_memberships
  FROM pg_catalog.pg_roles AS role
 WHERE role.rolname = current_user
"""


@dataclass(frozen=True, repr=False)
class PostgresTestTarget:
    """Verified connection coordinates; the secret DSN is omitted from repr."""

    dsn: str = field(repr=False)
    database: str
    user: str
    host: str
    port: int

    def __repr__(self) -> str:
        return (
            "PostgresTestTarget(database={!r}, user={!r}, host={!r}, port={!r})"
        ).format(self.database, self.user, self.host, self.port)


def postgres_target_from_environment(
    environ: Mapping[str, str],
) -> PostgresTestTarget:
    """Require a password-free, exact, local disposable database configuration."""

    values: dict[str, str] = {}
    for name in _REQUIRED_ENV:
        value = environ.get(name, "").strip()
        if not value:
            raise UnsafePostgresTestTarget(f"{name} must be set")
        values[name] = value

    database = values["CELESTIN_TEST_POSTGRES_DATABASE"]
    if database in _UNUSABLE_TEST_DATABASES or not _DATABASE_NAME.fullmatch(database):
        raise UnsafePostgresTestTarget(
            "the test database must use celestin_it_YYYYMMDD_<8-hex-nonce> "
            "with a newly provisioned nonce"
        )

    user = values["CELESTIN_TEST_POSTGRES_USER"]
    if not _ROLE_NAME.fullmatch(user):
        raise UnsafePostgresTestTarget(
            "the test role must use celestin_it_user_<8-hex-nonce>"
        )
    if user.rsplit("_", 1)[-1] != database.rsplit("_", 1)[-1]:
        raise UnsafePostgresTestTarget(
            "the database and test role must use the same eight-hex nonce"
        )

    host = values["CELESTIN_TEST_POSTGRES_HOST"]
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        raise UnsafePostgresTestTarget("the test host must be a loopback IP address") from None
    if not address.is_loopback:
        raise UnsafePostgresTestTarget("the test host must be a loopback IP address")

    try:
        port = int(values["CELESTIN_TEST_POSTGRES_PORT"])
    except ValueError:
        raise UnsafePostgresTestTarget("the test port must be an integer") from None
    if not 1 <= port <= 65535:
        raise UnsafePostgresTestTarget("the test port is out of range")
    options = _parse_test_dsn(values["CELESTIN_TEST_POSTGRES_DSN"])
    expected = {"dbname": database, "user": user, "host": host, "port": str(port)}
    if options != expected:
        raise UnsafePostgresTestTarget(
            "the password-free DSN must contain only host, port, dbname, and user "
            "with values matching the configured target"
        )

    return PostgresTestTarget(
        dsn=values["CELESTIN_TEST_POSTGRES_DSN"],
        database=database,
        user=user,
        host=str(address),
        port=port,
    )


def server_address_matches_target(server_address: str | None, target_host: str) -> bool:
    """Match PostgreSQL's inet text to the configured loopback IP only.

    PostgreSQL may render ``inet_server_addr()`` with a host prefix such as
    ``127.0.0.1/32``. Accept that representation only when its address exactly
    matches the configured loopback address; never accept a remote address.
    """
    try:
        observed = ipaddress.ip_interface(server_address).ip
        expected = ipaddress.ip_address(target_host)
    except (TypeError, ValueError):
        return False
    return expected.is_loopback and observed.is_loopback and observed == expected


def verify_connection_identity(connection, target: PostgresTestTarget) -> None:
    """Require the exact target and a minimally privileged database role.

    The database owner's implicit ``pg_database_owner`` membership is expected
    and excluded. Any other direct or transitive role membership is rejected.
    """
    identity = connection.execute(_IDENTITY_SQL).fetchone()
    if identity is None:
        raise UnsafePostgresTestTarget("could not verify the PostgreSQL connection identity")

    (
        database,
        user,
        host,
        port,
        is_superuser,
        can_create_roles,
        can_create_databases,
        can_replicate,
        can_bypass_row_security,
        has_disallowed_memberships,
    ) = identity
    if (
        database != target.database
        or user != target.user
        or not server_address_matches_target(host, target.host)
        or port != target.port
        or is_superuser
        or can_create_roles
        or can_create_databases
        or can_replicate
        or can_bypass_row_security
        or has_disallowed_memberships
    ):
        raise UnsafePostgresTestTarget(
            "the connected PostgreSQL identity or role privileges do not match "
            "the approved local test target"
        )


def _parse_test_dsn(dsn: str) -> dict[str, str]:
    """Parse the deliberately small keyword DSN format accepted by live tests.

    Requiring simple unquoted values makes duplicate keys visible before a
    driver can normalize them into a mapping. Credentials must come from the
    approved libpq password-file mechanism, never from this DSN.
    """
    if not dsn or dsn != dsn.strip() or "\n" in dsn or "\r" in dsn:
        raise UnsafePostgresTestTarget("the test DSN is malformed")

    options: dict[str, str] = {}
    for token in dsn.split():
        if token.count("=") != 1:
            raise UnsafePostgresTestTarget("the test DSN is malformed")
        key, value = token.split("=", 1)
        if not _DSN_KEY.fullmatch(key) or not value or not _DSN_VALUE.fullmatch(value):
            raise UnsafePostgresTestTarget("the test DSN is malformed")
        if key in options:
            raise UnsafePostgresTestTarget("the test DSN contains a duplicate parameter")
        options[key] = value

    if _PASSWORD_DSN_KEYS.intersection(options):
        raise UnsafePostgresTestTarget(
            "password parameters are not allowed in the test DSN; use a protected password file"
        )
    if options.keys() != _ALLOWED_DSN_KEYS:
        raise UnsafePostgresTestTarget(
            "the test DSN must contain exactly host, port, dbname, and user"
        )
    return options


class TrackedConnection:
    """Track cursors and connection closure for real-driver cleanup checks."""

    def __init__(self, connection) -> None:
        self.raw = connection
        self.cursors = []

    def execute(self, query, params=(), **kwargs):
        cursor = self.raw.execute(query, params, **kwargs)
        self.cursors.append(cursor)
        return cursor

    def transaction(self):
        return self.raw.transaction()

    def close(self) -> None:
        cursor_error: Exception | None = None
        try:
            for cursor in self.cursors:
                try:
                    if not cursor.closed:
                        cursor.close()
                except Exception as exc:
                    if cursor_error is None:
                        cursor_error = exc
        finally:
            if not self.closed:
                self.raw.close()
        if cursor_error is not None:
            raise cursor_error

    @property
    def closed(self) -> bool:
        return bool(self.raw.closed)


class LivePostgres:
    """A verified target and tracked per-test connections."""

    def __init__(self, psycopg, target: PostgresTestTarget, migrations, migration_result):
        self.psycopg = psycopg
        self.target = target
        self.migrations = migrations
        self.migration_result = migration_result
        self._connections: list[TrackedConnection] = []

    def connect(self, **kwargs) -> TrackedConnection:
        connection = TrackedConnection(self.psycopg.connect(self.target.dsn, **kwargs))
        self._connections.append(connection)
        return connection

    @contextmanager
    def connection(self, **kwargs):
        """Open and reliably close one tracked connection."""
        connection = self.connect(**kwargs)
        try:
            yield connection
        finally:
            connection.close()

    def connections(self) -> tuple[TrackedConnection, ...]:
        return tuple(self._connections)

    def close_connections(self) -> None:
        for connection in self._connections:
            if not connection.closed:
                connection.close()


def initialize_fresh_database(
    connection,
    target: PostgresTestTarget,
    migrations,
    *,
    verify_identity: Callable,
    assert_fresh: Callable,
    migration_runner: Callable,
):
    """Verify, migrate once through the runner, and verify the resulting ledger."""

    verify_identity(connection, target)
    assert_fresh(connection)
    result = migration_runner(connection, migrations)
    ledger_rows = connection.execute(
        "SELECT version, name, checksum FROM public.celestin_schema_migrations ORDER BY version"
    ).fetchall()
    expected_rows = [
        (migration.version, migration.name, migration.checksum)
        for migration in migrations
    ]
    if (
        result.applied_versions != tuple(migration.version for migration in migrations)
        or result.current_version != migrations[-1].version
        or ledger_rows != expected_rows
    ):
        raise UnsafePostgresTestTarget(
            "migration result or ledger does not match the packaged migrations"
        )
    return result
