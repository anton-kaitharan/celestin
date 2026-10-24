# PostgreSQL integration tests

## Scope

The hosted project-runtime authorization/RLS gate covers the packaged migrations and restricted project-login behavior. `tests/postgres/test_live_authorization.py` covers authorization decisions, same-key project separation, lifecycle/revocation, constraints, in-flight authorization changes, rollback, direct SQL/RLS behavior, legacy-GUC forgery, prohibited role switching, and the workspace-lock boundary added in migration 0009.

`tests/postgres/test_live_evidence_store.py` is a separate adapter-level suite marked `postgres_evidence_integration`. It currently uses the authorization/control-plane connection and legacy scope GUCs. Migration 0008 intentionally withholds Evidence/Capture table privileges from project runtime identities, and hosted request composition does not yet provide a project-scoped Evidence capability. Therefore this suite is not evidence that Evidence/Knowledge is available through the hosted project runtime and is excluded from the authorization/RLS gate. Do not broaden role grants to make this suite pass; a reviewed project-scoped Evidence authorization and privilege contract is required first.

These tests do not validate Auth0 against a real tenant or prove that hosted MCP has been deployed. They use synthetic principals and test-only scope bindings. Hosted runtime and database behavior must be reviewed separately before release.

## Test modes

Ordinary tests do not connect to PostgreSQL. Run the offline PostgreSQL tests with the repository validation environment:

```powershell
.\.venv\validation\Scripts\python.exe -m pytest -p no:cacheprovider -o addopts='' tests\postgres\test_hosted_authorization.py tests\postgres\test_migration_runner.py tests\postgres\test_migrate_cli.py tests\postgres\test_live_support.py tests\postgres\test_provisioning.py tests\postgres\test_request_unit_of_work.py
```

The hosted authorization/RLS suite is opt-in and skips unless `--run-postgres-integration` is explicitly supplied. When opted in, the session fixture validates all five configured login identities and the target before applying migrations through the repository's guarded migration runner. The command below can create schema objects in the configured database; run it only after a database owner has explicitly approved and provisioned that disposable target. It runs only the authorization/RLS suite, not the separate Evidence adapter tests.

```powershell
.\.venv\validation\Scripts\python.exe -m pytest -p no:cacheprovider -o addopts='' --run-postgres-integration -m postgres_integration tests\postgres\test_live_authorization.py
```

The Evidence adapter cases can be selected separately only for adapter investigation on a separately approved one-run target. Their current authorization setup does not satisfy the project-login isolation model; do not use them as hosted tenant-isolation acceptance evidence. A future hosted Evidence gate must use the mapped project login and demonstrate positive and cross-project negative access through the intended request path.

```powershell
.\.venv\validation\Scripts\python.exe -m pytest -p no:cacheprovider -o addopts='' --run-postgres-integration -m postgres_evidence_integration tests\postgres\test_live_evidence_store.py
```

The fixture never uses SQLite as a fallback, never reads `CELESTIN_POSTGRES_DSN`, and never creates, drops, truncates, or adopts a database. It calls `orbit.postgres.migrations.runner.apply_migrations()` only after confirming the configured migration connection and fresh-database conditions. A skip is not live validation. Do not install optional dependencies as part of a test run without separate authorization.

## Required approved disposable target

The database owner must explicitly approve a newly provisioned local PostgreSQL database for a one-run integration session. The environment variable `CELESTIN_TEST_POSTGRES_DISPOSABLE=true` is an operator assertion; it does not prove approval, freshness, or safety by itself. The harness additionally requires a loopback IP, exact connection coordinates, fresh nonce-based names, distinct logins, safe role attributes, expected group memberships, and a database with no migration ledger or user-created objects.

Migrations 0008 and 0009 expect these pre-provisioned `NOLOGIN` group roles and do not create or drop roles:

- `celestin_project_runtime`
- `celestin_authorization_reader`
- `celestin_authorization_admin`

For each run, provision one fresh database named `celestin_it_YYYYMMDD_<8-hex-nonce>` and five distinct direct logins with that same nonce:

- Migration/database-owner login: `celestin_it_user_<nonce>`
- Authorization-administration login: `celestin_it_admin_<nonce>`
- Read-only authorization login: `celestin_it_reader_<nonce>`
- Project A content login: `celestin_it_project_a_<nonce>`
- Project B content login: `celestin_it_project_b_<nonce>`

Use a new nonce and a new empty database for each initialization attempt. The migration login must be a non-superuser database owner and must not have `CREATEROLE`, `CREATEDB`, `REPLICATION`, `BYPASSRLS`, or additional role memberships beyond PostgreSQL's implicit `pg_database_owner` relationship. The four runtime logins must be direct logins, non-superusers, `NOBYPASSRLS`, non-owning, and members only of their matching group role (with the implicit `pg_database_owner` exception where applicable). They must not have `ADMIN OPTION`, be able to switch roles, or create objects in protected schemas. The migration fixture verifies that the migration login owns the target database and verifies database/runtime identities and role memberships before applying anything. Hosted readiness rejects unexpected RLS policies, altered authorization-helper definitions or grants, role memberships, and effective table/schema/sequence/function privileges.

Set these non-secret coordinates for the same PowerShell session that will run pytest. Do not place passwords in DSNs, command history, repository files, or logs. Supply credentials through an approved secret manager or protected libpq password file.

```powershell
$nonce = 'a1b2c3d4' # replace with a newly generated eight-hex nonce
$date = Get-Date -Format 'yyyyMMdd'
$env:CELESTIN_TEST_POSTGRES_DISPOSABLE = 'true'
$env:CELESTIN_TEST_POSTGRES_DATABASE = "celestin_it_${date}_${nonce}"
$env:CELESTIN_TEST_POSTGRES_HOST = '127.0.0.1'
$env:CELESTIN_TEST_POSTGRES_PORT = '5432'
$env:CELESTIN_TEST_POSTGRES_USER = "celestin_it_user_${nonce}"
$env:CELESTIN_TEST_POSTGRES_ADMIN_USER = "celestin_it_admin_${nonce}"
$env:CELESTIN_TEST_POSTGRES_AUTHORIZATION_USER = "celestin_it_reader_${nonce}"
$env:CELESTIN_TEST_POSTGRES_PROJECT_A_USER = "celestin_it_project_a_${nonce}"
$env:CELESTIN_TEST_POSTGRES_PROJECT_B_USER = "celestin_it_project_b_${nonce}"
$target = "host=127.0.0.1 port=5432 dbname=$env:CELESTIN_TEST_POSTGRES_DATABASE"
$env:CELESTIN_TEST_POSTGRES_DSN = "$target user=$env:CELESTIN_TEST_POSTGRES_USER"
$env:CELESTIN_TEST_POSTGRES_ADMIN_DSN = "$target user=$env:CELESTIN_TEST_POSTGRES_ADMIN_USER"
$env:CELESTIN_TEST_POSTGRES_AUTHORIZATION_DSN = "$target user=$env:CELESTIN_TEST_POSTGRES_AUTHORIZATION_USER"
$env:CELESTIN_TEST_POSTGRES_PROJECT_A_DSN = "$target user=$env:CELESTIN_TEST_POSTGRES_PROJECT_A_USER"
$env:CELESTIN_TEST_POSTGRES_PROJECT_B_DSN = "$target user=$env:CELESTIN_TEST_POSTGRES_PROJECT_B_USER"
```

The parser accepts exactly `host`, `port`, `dbname`, and `user` in each DSN; each must match the separate environment variables. Password parameters, URIs, duplicate/unknown parameters, quoted values, and non-loopback hosts are rejected before a database connection is opened. The harness also rejects all historical retired targets listed below. The checked-in boolean and naming pattern are safeguards, not an operator approval mechanism.

Before opting in, independently verify with the database owner that the selected host, database, roles, and credentials identify the approved disposable target. The fixture repeats its checks immediately before its single migration-runner call. If configuration is missing, ambiguous, not loopback, role attributes/membership are unsafe, or the database is not fresh, the suite fails closed without attempting cleanup. Do not run `celestin-migrate` on this test target first; the fixture requires it to be fresh and initializes it once.

The authorization fixture creates synthetic workspaces, projects, identities, grants, scope mappings, messages, and decisions. The separately selected Evidence adapter suite may create Evidence rows using its adapter-level setup. Fixtures remove the generated project-scope mappings used by RLS tests, but do not remove generated content rows or migration schema. Treat the database as one-run and retire it using the database owner's separately approved cleanup procedure. Pytest does not drop or reset it.

## Retired database targets

The harness explicitly rejects these names; do not reuse or reset them:

- `celestin_schema_test_v2` — former schema-validation target.
- `celestin_it_20261014_a7f3c9` — failed initialization target.
- `celestin_it_20261015_b364bbae` and `celestin_it_20261015_10ac5ae9` — prior live-test targets.
- `celestin_it_20261015_0b8e767c` — prior one-run live target.

The separate `tests/postgres/validate_hosted_schema.sql` script is not an initializer and must not be run without a separately owner-approved, freshly provisioned target. Its historical fixed target is retired. No database is approved by this document.

## Security interpretation and limits

Migration 0008 replaces caller-settable workspace/project GUC policies on twelve tenant tables with policies that compare each row to `celestin_private.project_runtime_scopes` using PostgreSQL `session_user`. Migration 0009 limits project-login authorization locks to the workspace mapped to that login while retaining the broader lock only for the trusted authorization-reader role. The live RLS cases execute direct queries as the two distinct project logins, attempt legacy-GUC forgery, cross-project reads/writes, role switching, unauthorized scope-map access, and out-of-scope authorization-lock requests. They are designed to check isolation even when a tenant predicate is omitted; only an actual opted-in run can establish PostgreSQL behavior.

## Authorization/RLS live acceptance checklist

Record results for each case below. Offline fake tests validate readiness rules and request routing but do not establish PostgreSQL behavior:

- RLS and FORCE RLS are enabled for all twelve protected content tables.
- Forged legacy workspace/project GUC values do not widen row visibility.
- Cross-project reads and writes are denied for both project logins.
- `SET ROLE` to a sibling login/group and `SET SESSION AUTHORIZATION` are denied.
- Project logins cannot read or mutate scope mappings and cannot request an unmapped workspace lock.
- Runtime identities cannot perform prohibited `TRUNCATE`, trigger creation, `REFERENCES`, DDL, or grant changes; cannot modify control tables; and cannot execute administrative lock functions.
- Effective role memberships and grants contain no unexpected membership, `ADMIN OPTION`, PUBLIC grant, column grant, sequence, schema, or function capability.
- Hosted readiness rejects missing, disabled, broadened, unexpected, or altered RLS policies and authorization helpers, plus privilege drift.
- Membership, grant, project/workspace lifecycle, and principal-status revocation ordering is exercised with separate PostgreSQL sessions.
- Local stdio/SQLite regression and public MCP tool-schema parity remain green.

Live race cases are required evidence before hosted release. An offline test or skipped live case does not satisfy them.

The application still centrally holds configured project DSNs. A compromised application process or control-plane credential can choose among those configured project credentials; the database mapping limits each individual login but does not isolate a fully compromised central process or database administration authority. PostgreSQL owners, superusers, `BYPASSRLS` roles, and compromised credential provisioning remain trusted. The test-only `WorkspaceBinding` is not production transport authentication.

Authorization requests and authorization mutations use matching transaction-scoped advisory locks. This allows an already-authorized transaction to complete before a concurrent revocation commits; subsequent requests should observe the change. The live suite contains cross-session revocation ordering cases, but only an actual opted-in PostgreSQL run can establish server behavior.

The local stdio MCP runtime remains SQLite-backed. Hosted mode is separate and remains gated by `CELESTIN_HOSTED_PROJECT_ROLE_ISOLATION_ENABLED`, whose default is false. These tests do not enable hosted mode or establish production readiness.
