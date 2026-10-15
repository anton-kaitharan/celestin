# PostgreSQL integration tests

## What these tests cover

`tests/postgres/test_live_authorization.py` exercises the packaged PostgreSQL
migrations and hosted authorization session against a real PostgreSQL server.
The cases cover migration ledger/checksum behavior, human and
service identity status, workspace membership and explicit project grants,
same-key project isolation across workspaces, archived resources, source-message
scope and foreign keys, SQL checks/uniqueness/restrictive deletion, transaction
commit/rollback, error redaction, resource cleanup, and concurrent grant,
membership, and service-identity revocation.

The suite is opt-in. It never uses SQLite as a fallback, never reads
`CELESTIN_POSTGRES_DSN`, never creates or drops a database, and never uses
`celestin_schema_test_v2`. On the first live fixture setup it applies migrations
only through `orbit.postgres.migrations.runner.apply_migrations()`, once per
fresh test target. Before that call, it checks the exact connected database,
role, loopback server address and port, refuses a superuser connection, and
rejects a pre-existing migration ledger or objects covered by the migration
runner's fresh-database guard. After that single migration call, it verifies
the complete ledger against packaged versions and checksums. Migration-runner
repeat/idempotency behavior is covered by the fake-based runner tests; do not
run `celestin-migrate` on the live-test target before invoking this fixture.

The test database must be freshly provisioned for one live test session. The
schema migrations remain after tests; only uniquely identified fixture rows
are cleaned up. Do not rerun live tests against a database that already has a
migration ledger. If a migration fails after earlier versions were committed,
stop and inspect that test database; use a newly provisioned disposable target
for another attempt. Nothing in pytest drops, truncates, or adopts a database.

## Test modes

Run existing fake-connection PostgreSQL tests without a server:

```powershell
.\.venv\validation\Scripts\python.exe -m pytest -p no:cacheprovider -o addopts='' tests\postgres\test_hosted_authorization.py tests\postgres\test_migration_runner.py tests\postgres\test_migrate_cli.py tests\postgres\test_live_support.py
```

Run only real PostgreSQL tests (requires the dedicated target configuration
below):

```powershell
.\.venv\validation\Scripts\python.exe -m pytest -p no:cacheprovider -o addopts='' --run-postgres-integration -m postgres_integration tests\postgres\test_live_authorization.py
```

Run the ordinary project suite. Live tests are skipped unless the explicit
`--run-postgres-integration` flag is supplied:

```powershell
.\.venv\validation\Scripts\python.exe -m pytest -p no:cacheprovider -o addopts=''
```

The pure target-safety tests run without PostgreSQL. The live suite requires
the optional Psycopg driver. If it is missing, install the project's existing
optional extra in the validation environment:

```powershell
.\.venv\validation\Scripts\python.exe -m pip install -e ".[postgres,test]"
```

This project does not install or configure PostgreSQL as part of pytest.

## Provision and verify a disposable target

The normal option is a **new** local database and matching non-superuser
database-owner role for a single test run. The database and role must share one
random eight-character hexadecimal nonce. The test runner does not create
either one. Example names (choose a new nonce each time):

```text
celestin_it_20261014_a1b2c3d4
celestin_it_user_a1b2c3d4
```

The following targets are retired and explicitly rejected by the harness; do
not reuse them:

- `celestin_it_20261014_a7f3c9` — failed initialization may have left a migration ledger.
- `celestin_it_20261015_b364bbae` — used by a previous live test run.
- `celestin_it_20261015_10ac5ae9` — used by a previous live test run.

Provision a new nonce-named database and matching role for every initialization
run.

The role needs permission to create the Celestin tables and indexes in the
database's `public` schema. It must not have `SUPERUSER`, `CREATEROLE`,
`CREATEDB`, `REPLICATION`, or `BYPASSRLS`, and must not be a direct or indirect
member of another role. The database owner's implicit membership in
`pg_database_owner` is allowed; the fixture rejects other memberships. Do not
use `postgres`, a shared application role, production credentials, or a
database containing data. Provide authentication
through an approved secret manager or a locally protected PostgreSQL password
file; do not put passwords in the DSN, command history, repository, or test
logs.

Set the non-secret target coordinates in the same PowerShell session that will
run pytest. Set the test-only DSN explicitly; do not copy the general migration
DSN variable into it:

```powershell
$nonce = 'a1b2c3d4' # replace with a fresh random eight-hex nonce
$date = Get-Date -Format 'yyyyMMdd'
$env:CELESTIN_TEST_POSTGRES_DATABASE = "celestin_it_${date}_${nonce}"
$env:CELESTIN_TEST_POSTGRES_USER = "celestin_it_user_${nonce}"
$env:CELESTIN_TEST_POSTGRES_HOST = '127.0.0.1'
$env:CELESTIN_TEST_POSTGRES_PORT = '5432'
$env:CELESTIN_TEST_POSTGRES_DSN = "host=127.0.0.1 port=5432 dbname=$env:CELESTIN_TEST_POSTGRES_DATABASE user=$env:CELESTIN_TEST_POSTGRES_USER"
```

Before opting in, verify the selected target interactively with the PostgreSQL
client, substituting the same environment variables. The password prompt is
interactive; never add a password argument:

```powershell
& 'C:\Program Files\PostgreSQL\18\bin\psql.exe' -X -W `
  -h $env:CELESTIN_TEST_POSTGRES_HOST `
  -p $env:CELESTIN_TEST_POSTGRES_PORT `
  -U $env:CELESTIN_TEST_POSTGRES_USER `
  -d $env:CELESTIN_TEST_POSTGRES_DATABASE `
  -v ON_ERROR_STOP=1 `
  -c "SELECT current_database(), current_user, inet_server_addr(), inet_server_port();"
```

The authenticated user must also confirm through `psql` that no migration
ledger, user schema, or application objects exist. The live fixture independently
checks the actual connection identity and catalog state before invoking the
migration runner. If the database is missing, the role differs, the endpoint
is not loopback, the database/role names do not follow the new nonce convention,
the role has a prohibited attribute or membership, the user is a superuser, or
any unexpected objects exist, the fixture stops without cleaning or modifying
them.

### Read-only preflight checklist

Only after the database owner has provisioned and approved the new disposable
database and matching role, connect with the interactive `psql -W` command
above. Do not use any retired target listed above or `celestin_schema_test_v2`.
Run these read-only queries in that `psql` session before opting into pytest:

```sql
SELECT current_database() AS database,
       current_user AS role,
       inet_server_addr() AS server_address,
       inet_server_port() AS server_port;

SELECT rolname,
       rolsuper,
       rolcreaterole,
       rolcreatedb,
       rolreplication,
       rolbypassrls
FROM pg_catalog.pg_roles
WHERE rolname = current_user;

SELECT granted_role.rolname,
       pg_catalog.pg_has_role(current_user, granted_role.oid, 'USAGE') AS inherits_privileges,
       pg_catalog.pg_has_role(current_user, granted_role.oid, 'SET') AS can_set_role
FROM pg_catalog.pg_roles AS granted_role
WHERE granted_role.oid <> (
          SELECT self_role.oid
          FROM pg_catalog.pg_roles AS self_role
          WHERE self_role.rolname = current_user
      )
  AND granted_role.rolname <> 'pg_database_owner'
  AND pg_catalog.pg_has_role(current_user, granted_role.oid, 'MEMBER')
ORDER BY granted_role.rolname;

SELECT to_regclass('public.celestin_schema_migrations') AS migration_ledger;

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
) AS has_unexpected_user_objects;
```

Proceed only when the first row exactly matches the approved database and role
on `127.0.0.1:5432`; the role-attribute flags are all `false`; the membership
query returns no rows (the implicit `pg_database_owner` membership is omitted);
the migration-ledger result is `NULL`; and `has_unexpected_user_objects` is
`false`. Stop and ask the database owner if any result is unexpected or
uncertain. These commands do not display or inspect password-file contents.
The harness repeats identity, role-privilege, and freshness checks immediately
before its one migration-runner call; the SQL preflight is not a substitute for
those checks.

The DSN is held only in `CELESTIN_TEST_POSTGRES_DSN` for the local test process.
The fixture accepts exactly four unquoted keyword/value parameters in the DSN:
`host`, `port`, `dbname`, and `user`, each once and matching the separate
environment variables. Password parameters, duplicate keys, URI-form DSNs,
quoted values, and all other parameters are rejected before opening a database
connection. Do not put credentials in the DSN. Supply authentication through
`PGPASSFILE` or an approved secret manager supported by Psycopg/libpq.

On Windows, libpq's default password file is
`%APPDATA%\postgresql\pgpass.conf`; `PGPASSFILE` can point to another local
file. Lines use `host:port:database:user:password` format. Create and manage it
outside the repository with an approved credential tool. PostgreSQL does not
check password-file ACLs on Windows, so ensure the file is in a protected user
directory and its NTFS ACL restricts access to the user and trusted system
administrators. Verify that the file exists without displaying its contents.
Do not print the environment, use PowerShell transcript capture while entering
secrets, or paste credentials into Codex. The fixture sanitizes driver failures
and never includes the DSN in target representations or assertion messages.

After a run, fixture records are deleted by their generated workspace/project
IDs in foreign-key-safe order. The migration schema is intentionally retained.
Have the database administrator retire the one-run test database and role
through the team's separate, approved environment-cleanup procedure; pytest
does not issue database-level cleanup commands.

## Interpreting results and limits

The live concurrency tests use distinct PostgreSQL connections and bounded
events/lock polling. They verify the documented ordering: an operation that
already holds `FOR SHARE` authorization locks can finish; a concurrent
suspension or revocation waits for that transaction; requests after the
revocation commits are denied. This is not instantaneous revocation.

One live test intentionally demonstrates a **known schema/application gap**:
PostgreSQL currently allows a workspace with no active owner. Its PASS means
that the test observed this gap; it does not mean the invariant is enforced.
Preventing concurrent last-owner removal still needs transactional application
logic that locks the workspace row and rechecks ownership.

These tests do not authenticate external callers, enable hosted MCP selection,
or wire PostgreSQL into the local stdio server. The workspace binding in live
tests is a synthetic test double. Passing this suite is evidence about the
PostgreSQL repository/session and schema only, not production authentication,
remote transport, or complete hosted authorization.
