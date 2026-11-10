# PostgreSQL integration tests

## Scope

The hosted project-runtime authorization/RLS gate covers the packaged migrations and restricted project-login behavior. `tests/postgres/test_live_authorization.py` covers authorization decisions, same-key project separation, lifecycle/revocation, constraints, in-flight authorization changes, rollback, direct SQL/RLS behavior, legacy-GUC forgery, prohibited role switching, the workspace-lock boundary added in migration 0009, and a project-login retrieval case through the request unit of work. The retrieval case checks lexical results, persisted message fields, exclusion of a sibling-project row and a missing ID, and that retrieval borrows the active authorized request connection.

PostgreSQL retrieval currently exposes the lexical adapter only. `tests/retrieval/test_service_integration.py` covers semantic-only stale/foreign candidate metadata and hybrid duplicate merging using local SQLite; those cases are not hosted PostgreSQL behavior. PostgreSQL's expression GIN index is rebuilt from message content and does not carry a separate speaker-role or provenance payload. The adapter reloads visible message rows before constructing results; offline adapter tests exercise a missing/ineligible candidate and authoritative row fields, while only an approved live run can verify the server query behavior. Message-level archive filtering is source- and offline-test-covered; the restricted project login cannot update `chat_messages`, so the live fixture does not manufacture an archived message. Existing live project/workspace archive tests cover authorization denial for archived scopes.

Migration 0008's historical privilege profile withheld Evidence/Knowledge table access from project-runtime logins. The checked-in forward migration 0010 now grants only `SELECT`, `INSERT`, and `UPDATE` on `celestin_knowledge_records` and `celestin_evidence_records` to `celestin_project_runtime`; it leaves `PUBLIC`, `celestin_authorization_reader`, and `celestin_authorization_admin` without those table privileges. This is migration source, not evidence that any database has applied it. Table grants do not replace the forced RLS policies or the project/workspace scope derived from the restricted login. `PostgresRequestUnitOfWork` composes the existing Evidence domain service with its scoped adapter and resolves message references on the same authorized connection. Offline tests cover that composition and foreign-project denial; they do not establish PostgreSQL catalog, transaction, or RLS behavior. Hosted MCP handlers still expose no Evidence operations, so this is an internal hosted composition seam, not an enabled public capability. MCP public request/response compatibility remains covered by `tests/test_mcp_tools.py` and is not altered by the live adapter test.

`tests/postgres/test_live_evidence_store.py` is a separate adapter-level suite marked `postgres_evidence_integration`. It uses the authorization-admin connection and legacy scope GUCs, so it does not exercise the migration 0010 project-runtime grant profile. Migration 0010 intentionally leaves the authorization-admin identity without Evidence table privileges; do not broaden grants to make this suite pass. The hosted project-runtime acceptance case in `tests/postgres/test_live_authorization.py` now exercises Evidence service creation and readback through the request UoW, while the adjacent cases inspect the live grants and cross-project RLS behavior. These cases remain opt-in and require the guarded fresh target below. Offline and scripted tests remain separate from live PostgreSQL catalog, grant, transaction, and RLS verification.

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

## Advisory-lock privilege and session-reset gate

The source-level readiness checks inspect effective execution, direct login grants, PUBLIC grants, and the complete ACL entries for every supported `pg_catalog` advisory-lock/unlock overload. Each entry includes grantor/grantee identities and grantability; non-owner grants must be issued by the function owner, must be non-delegable, and may name only the inspected login when that role's profile permits the overload. The function owner's inherent grant option is the sole exception. Project-runtime, authorization-reader, and authorization-admin identities must have no direct advisory-function execution. The migration/database-owner identity is expected to need only the migration runner's session `pg_advisory_lock(bigint)` / `pg_advisory_unlock(bigint)` pair and the transaction-exclusive/shared bigint primitives used by its owned security-definer lock wrappers. Effective privilege checks reject inherited execution, and the separate role checks reject unexpected memberships. The validator requires the exact reviewed overload roster and fails closed on missing, unexpected, or incomplete catalog metadata. These are source expectations; no live PostgreSQL ACL has been inspected in this task.

Migrations 0008 and 0009 revoke `PUBLIC` from Celestin's wrapper functions, but the request lock wrappers themselves are invoker-security and call PostgreSQL's built-in advisory functions. That does not remove the built-ins' default effective `PUBLIC EXECUTE` privilege. A role-specific revoke from a project login cannot subtract a privilege inherited from `PUBLIC`. Under that default, a holder of a project login can issue direct session- or transaction-lock calls on arbitrary keys and can block application lock users. This is an availability/serialization risk even though the protected `session_user` mapping still controls RLS row scope.

The new hosted readiness gate therefore rejects project or reader pools with any direct advisory-function execution and expects their Celestin request-lock wrappers to be security-definer functions with fixed safe search paths. That catalog shape is necessary but not sufficient: the reader and authorization-admin wrappers accept caller-selected workspace/principal lock keys without a database-verifiable binding. Source readiness now fails closed for those profiles even if their catalog definitions match the current security-definer template. Only the project request wrapper contains an explicit `session_user`-to-protected-scope check. Readiness remains closed for migrations 0008–0009 and for any unscoped reader/admin wrapper; a forward migration must not promote those functions until their inputs are derived from or validated against trusted database state and negative tests establish that callers cannot select arbitrary keys. It does not silently fall back to application-only locking.

There is a second privilege transition to account for: migration 0008 defines the authorization-write helpers (`celestin_lock_authorization_write`, `celestin_lock_workspace_authorization_write`, `celestin_lock_principal_authorization_write`, and `celestin_lock_principal_transition_write`) as invoker-security functions. Their bodies call `pg_advisory_xact_lock`, so the authorization-admin login currently also relies on effective direct execution of that built-in through the default ACL. Source now includes `_assert_safe_authorization_admin`, which checks the admin login and all four helpers' identities, owner, security mode, fixed path, body, public access, and grants. The opt-in live role-profile test invokes it using the distinct admin test login. Hosted request startup still does not create or check an authorization-admin pool; no production provisioning host is wired to invoke this helper yet. That remains a deployment-integration requirement before an admin credential is used.

Readiness has two separate responsibilities. The request-runtime gate is `_assert_safe_hosted_pools`: it checks the authorization-reader and configured project logins and must remain in the ordinary MCP process. It does not check a migration identity or authorize provisioning. A separate operator-controlled provisioning/migration preflight is not currently wired. Before future use of `celestin-migrate`, `PostgresAccessProvisioner`, or `ProjectDatabaseLoginRegistry`, that preflight must independently verify the migration and authorization-admin identities, exact privileged group/member roster, complete advisory ACL and grantability profile, object ownership, and migration/recovery prerequisites. The future integration points are the operator composition around `migrate_cli.main`/`apply_migrations` and the privileged provisioning composition; they must not expose privileged credentials or operations through hosted request routing. These are design boundaries, not implemented readiness gates.

The required database-wide ACL change, its effect on non-Celestin users, the privilege limitation of the ordinary migration owner, and a forward-only deployment/rollback proposal are documented in [the advisory-lock privilege boundary proposal](postgres-advisory-lock-privilege-boundary-proposal.md). The proposal is not approved or applied. A role-specific revoke cannot subtract `PUBLIC EXECUTE`, and a security-definer wrapper alone does not prevent a direct call to a still-public built-in.

Removing `PUBLIC EXECUTE` from built-in functions changes the ACL for every role in the database, not just Celestin. It must not be applied to a database shared with unrelated workloads without an ACL impact review and an approved restoration/grant plan. PostgreSQL permits ACL changes on functions to their owner or a role with the needed grant authority; the checked-in migration/test role is deliberately non-superuser, and no source evidence proves it can alter built-in function ACLs. A database/platform owner must confirm the provider supports this arrangement. If it does not, the residual arbitrary-lock risk remains; alternatives include an isolated Celestin database with provider-admin-managed ACLs or a separately reviewed locking design. `SECURITY DEFINER` wrappers alone do not prevent a credential holder from calling a still-public built-in directly.

No forward migration was created or applied here. A proposed forward-only database change must (1) use fixed-path, reviewed `SECURITY DEFINER` wrappers for request and authorization-write locks, owned by a narrowly privileged trusted role; (2) preserve the mapped-workspace check before a project lock; (3) remove public direct execution from the supported built-in overloads only after an impact/authority review; (4) grant only the required bigint primitives to the trusted wrapper/migration owner; and (5) retain the migration runner's exact session lock/unlock capability. The database owner must decide whether the provider supports these ACL operations and which database roles need explicitly restored grants.

The runtime's psycopg pools now run `DISCARD ALL` before reusing a returned connection, after rolling back any open transaction. Automatic server-side preparation is disabled because `DISCARD ALL` deallocates server prepared statements. A live acceptance case checks that a session-level advisory lock and session settings do not survive pool reuse. This behavior has not been verified against PostgreSQL in this task.

The live suite now contains acceptance cases for the effective advisory-function profiles of the migration, authorization-admin, authorization-reader, and both project logins; direct project-login session/transaction lock denial; all twelve RLS policy/catalog entries; the expected trigger roster; the three authorization/runtime group-role membership roster; both content tables' actual grants and cross-project operations; additional authorization-revocation races; cancellation cleanup; and pooled session reset. The database-side privilege cases are expected to fail against the unchanged 0008–0009 built-in ACL/wrapper setup until the forward prerequisite is implemented. They were not run here. The existing live fixture's guarded command remains:

```powershell
.\.venv\validation\Scripts\python.exe -m pytest -p no:cacheprovider -o addopts='' --run-postgres-integration -m postgres_integration tests\postgres\test_live_authorization.py
```

**Do not run this command until a database owner has separately approved a fresh disposable target and the forward ACL/wrapper prerequisite has been reviewed and provisioned.** Opt-in still applies migrations to the selected database.

The local stdio MCP runtime remains SQLite-backed. Hosted mode is separate and remains gated by `CELESTIN_HOSTED_PROJECT_ROLE_ISOLATION_ENABLED`, whose default is false. These tests do not enable hosted mode or establish production readiness.
