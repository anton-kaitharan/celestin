# Celestin Shared Storage — Final Completion Audit and Plan

**Audit date:** 2026-11-08 16:34 IST
**Branch / audited commit:** `codex/celestin-shared-storage-platform` / `57c84b311b83cb042522b2debb5e839cf977c38a`
**Scope:** repository audit and roadmap only. No tests, database connections, migrations, database changes, or commits were made for this audit.

## Executive assessment

The local SQLite product and most of the hosted PostgreSQL foundation are implemented. The hosted server composes authenticated requests, scoped authorization, project-specific database logins, request-scoped storage/retrieval, six Evidence tools, readiness probes, RLS and last-active-owner enforcement. Recent offline test runs give useful evidence for those code seams.

Celestin is **not production ready**. The PostgreSQL migrations, forced RLS behavior, owner triggers, pool-reset behavior and hosted Evidence operations have not been exercised against a live PostgreSQL server. Hosted readiness intentionally remains fail-closed: the authorization advisory-lock privilege boundary and provider capabilities have not been established. The opt-in PostgreSQL fixture applies migrations to its target, so a database owner must provision and confirm a new disposable Celestin target before live validation. No database was contacted or modified for this audit.

The fastest safe path is to resolve the database lock-privilege design, establish a disposable test target, then run the live suite once. Do not enable hosted serving while lock authority and live tenant isolation remain unverified.

## Audit evidence and architecture

Inspected the current source and docs in local database/storage/Evidence/retrieval, PostgreSQL adapters and migrations, hosted MCP/auth/scope/readiness, and corresponding test suites. Also read root `AGENTS.md`, Loop Engineering core and Celestin-specific rules. The branch was clean at start and tracked `origin/codex/celestin-shared-storage-platform` at the audited SHA. Test figures below are the latest recorded results from the preceding implementation work on this same commit; they were not rerun here. Fake/scripted tests do not establish PostgreSQL server behavior.

| Layer | Current implementation | Main paths |
|---|---|---|
| Local SQLite | Local stdio MCP stays SQLite-backed. SQLite schema version 3 includes manual Evidence/Knowledge records. | `src/orbit/database/engine.py`, `src/orbit/database/migrations/`, `src/orbit/storage/sqlite.py`, `src/orbit/evidence/sqlite.py`, `src/orbit/mcp_server/server.py` |
| Storage contracts | Memory, Evidence and retrieval ports with SQLite and PostgreSQL adapters. | `src/orbit/storage/protocol.py`, `src/orbit/evidence/service.py`, `src/orbit/retrieval/data.py` |
| PostgreSQL adapters/UoW | Memory, Evidence and lexical retrieval share a request-scoped transaction, resolved tenant scope and authorization. | `src/orbit/storage/postgres.py`, `src/orbit/evidence/postgres.py`, `src/orbit/retrieval/postgres_data.py`, `src/orbit/postgres/unit_of_work.py` |
| Authentication / scope | Auth0 token verification; server-side principal, workspace and project resolution; request boundary. | `src/orbit/mcp_server/hosted_auth.py`, `hosted_identity.py`, `hosted_scope.py`, `src/orbit/postgres/authorization.py` |
| Workspace/project lifecycle | Provisioning and revocation helpers; workspace creation with initial owner in one transaction; migration 0011 guards active-owner invariant. | `src/orbit/postgres/provisioning.py`, `authorization_locking.py`, `migrations/0011_active_workspace_owner.sql` |
| Hosted API/MCP | Separate Streamable HTTP hosted server; six additive hosted-only Evidence tools; local contracts unchanged. | `src/orbit/mcp_server/hosted_server.py`, `src/orbit/mcp_server/server.py` |
| Schema / RLS | Packaged PostgreSQL migrations 0001–0011; migrations 0007–0009 establish forced scope policies mapped from `session_user`; 0010 grants Evidence runtime access. | `src/orbit/postgres/migrations/`, `src/orbit/postgres/migrations/runner.py` |
| Readiness/config | Strict hosted settings, role/policy/grant/function/trigger checks, `/healthz` and `/readyz`; fail-closed isolation gate. | `src/orbit/mcp_server/hosted_server.py`, `hosted_preflight.py` |

`docs/hosted-postgres-runtime-audit.md` and parts of older PostgreSQL docs describe earlier implementation states. In particular, statements that Evidence MCP tools do not exist are stale against current source. Preserve historical context but reconcile those claims in the documentation task below.

## Completion matrix

| Feature | Classification | Evidence and remaining boundary |
|---|---|---|
| SQLite schema, migration/connection initialization | **Complete and verified** | `src/orbit/database/engine.py`, `src/orbit/database/migrations/`; recorded full offline run exercised the repository suite. Local mode remains unchanged. |
| SQLite memory/retrieval | **Complete and verified** | `src/orbit/storage/sqlite.py`, `src/orbit/retrieval/sqlite_data.py`, `retrieval/service.py`; `tests/database/`, `tests/storage/`, `tests/retrieval/`, local MCP tests. This does not prove hosted parity. |
| SQLite Evidence service and persistence | **Complete and verified** | `src/orbit/evidence/sqlite.py`, `service.py`, `sqlite_resolver.py`; `tests/evidence/test_sqlite_store.py`, `test_service.py`, `test_sqlite_resolver.py`, `test_local_application.py`. Manual Evidence scope only; producer conversion is outside this feature. |
| PostgreSQL adapters and migration runner | **Implemented but not verified** | `src/orbit/storage/postgres.py`, `src/orbit/evidence/postgres.py`, `src/orbit/retrieval/postgres_data.py`, `src/orbit/postgres/migrations/runner.py`; scripted suites include `tests/postgres/test_migration_runner.py`, `tests/storage/test_postgres_memory_store.py`, `tests/evidence/test_postgres_store.py`, `tests/retrieval/test_postgres_data.py`. SQL/server behavior remains unverified live. |
| Auth0 JWT/API-scope checks | **Complete and verified (offline boundary)** | `src/orbit/mcp_server/hosted_auth.py`; `tests/test_hosted_auth.py` exercises signatures, claims, issuer/profile/client allowlist, scope and JWKS behavior with fixtures. External Auth0 tenant and deployed secret/key operations are not verified. |
| Hosted identity/workspace/project authorization | **Implemented but not verified live** | `hosted_scope.py`, `hosted_identity.py`, `src/orbit/postgres/authorization.py`; tests `tests/test_hosted_scope.py`, `tests/postgres/test_hosted_authorization.py`, `test_hosted_identity_boundary.py`, `test_request_unit_of_work.py`. Fakes check decision ordering; live revocation races/locks remain unverified. |
| Atomic workspace creation / active owner | **Implemented but not verified live** | Migration 0011 deferred constraint triggers; `src/orbit/postgres/provisioning.py` creates workspace and initial active owner transactionally. Offline tests: `test_workspace_and_initial_owner_are_created_in_one_transaction`, `test_workspace_membership_revocation_rejects_owner_and_revokes_grants`. Live tests: `test_workspace_commit_requires_an_active_owner`, `test_last_active_owner_cannot_be_demoted_or_suspended`, `test_last_active_owner_identity_cannot_be_suspended`, `test_concurrent_owner_suspensions_preserve_one_active_owner`; written but not run on PostgreSQL. |
| Request UoW and Evidence composition | **Implemented but not verified live** | `src/orbit/postgres/unit_of_work.py`; `tests/postgres/test_request_unit_of_work.py` includes `test_authorized_memory_and_retrieval_share_one_connection_and_commit_once`, `test_authorized_evidence_write_uses_the_request_connection_and_outer_transaction`, `test_request_composes_evidence_domain_service_and_resolves_message_in_scope`, `test_request_evidence_failure_rolls_back_partial_domain_write`, `test_later_operation_failure_rolls_back_prior_write_and_closes_without_commit`. Fake transaction tests are not driver/server evidence. |
| Hosted Evidence MCP capability | **Implemented but not verified live** | `src/orbit/mcp_server/hosted_server.py` registers `create_knowledge`, `get_knowledge`, `associate_evidence`, `verify_knowledge`, `verify_evidence`, `supersede_knowledge`. `tests/test_hosted_server.py`: `test_hosted_tool_names_preserve_local_contracts_and_add_evidence`, `test_hosted_evidence_create_uses_authorized_request_uow`, `test_hosted_evidence_cross_tenant_denial_precedes_storage`. Actual hosted transport plus live database behavior still needs end-to-end evidence. |
| PostgreSQL RLS and `session_user` tenant mapping | **Implemented but not verified live** | Migrations 0007–0010 force project RLS and grant limited Evidence runtime access; `hosted_server.py` checks policy/role catalogs. Offline tests include `test_policy_catalog_rejects_widened_missing_or_unexpected_policies`, `test_project_pool_selection_requires_exact_resolver_scope_and_never_falls_back`, `test_postgres_pool_reset_clears_session_state_and_disables_prepare_cache`. Live CRUD, missing-scope, role, GUC-forgery and pool-reuse cases remain unexecuted. |
| Hosted startup/readiness | **Partially implemented** | `/readyz` and startup check database connectivity, selected role profiles, policies, grants, helper functions and owner triggers; detailed offline coverage in `tests/test_hosted_server.py` and `tests/test_hosted_server.py`. Does not yet clearly bind readiness to the exact highest applied packaged migration version/checksum; separate privileged migration/admin preflight is not wired as an operator workflow. |
| Hosted connection/pool configuration | **Partially implemented** | Strict environment validation, bounded pools/timeouts, connection rollback/reset and shutdown in `hosted_server.py`; config/pool tests in `tests/test_hosted_server.py`. Neon PgBouncer compatibility with `DISCARD ALL`, `session_user` mapping, and advisory lock operations is unknown. No deployment secret rotation, scaling, failover or load acceptance. |
| Workspace/project lifecycle API | **Partially implemented** | Schema and provisioning/revocation functions exist, but no general cross-backend lifecycle repository or complete production admin interface. See `src/orbit/postgres/provisioning.py`, migration 0001, `docs/shared-storage-readiness.md`. |
| Archive/retention/purge | **Partially implemented / policy blocked** | Schema stores `archived_at` and `purge_after`; authorization prevents archived scope use. No retention schedule, restore flow or purge worker. Do not invent destructive policy. |
| Production deployment, backup/recovery | **Not implemented / not verified** | No verified hosted deployment, production migration release process, secret rotation, backup/restore drill, recovery objectives, incident process, production load/failover or observability acceptance found. |
| Neon provider profile | **Implemented as portable PostgreSQL source; not verified on Neon** | PostgreSQL syntax suggests compatibility, but role/ACL, lock and pooling assumptions are unverified on Neon. See dedicated section below. No Neon project was accessed. |

## Verification ledger (latest recorded, not rerun here)

- Full offline command: `python -m pytest -p no:cacheprovider -o addopts='' --continue-on-collection-errors`, with bytecode disabled and `PYTHONPATH` extended to a temporary `rfc8785` installation. Recorded result: **1,325 passed, 69 skipped, 2 failed**.
- Failures: `tests/project_state/test_local_projects.py::test_symlinked_root_is_canonicalized` and `tests/project_state/test_reconstructor.py::test_repository_root_boundary_and_symlink_blob_are_safe`; both failed with Windows symlink privilege `WinError 1314` before application behavior was reached.
- `rfc8785>=0.1.4,<0.2` is declared in `pyproject.toml`. The earlier collection error was environmental; temporary installation resolved collection. Do not remove the dependency.
- Focused hosted/server/provisioning/migration/UoW/auth selection recorded **609 passed**. Compilation and diff checks passed during the prior implementation work.
- `tests/postgres/test_live_authorization.py`: **42 collected, 0 executed** because no safe disposable Celestin database was configured. `tests/postgres/test_live_evidence_store.py` is a separate opt-in integration suite and is also unverified.
- The prior full-run summary did not include a per-marker skip breakdown. Record exact skip categories during the next full CI run rather than infer them.
- `pyproject.toml` does not configure a type checker or linter. Inspect CI/workflows in the production-readiness task and report only checks actually configured/adopted.

## Prioritized blockers

### P0 — Critical security and data integrity

1. **Hosted readiness is intentionally blocked by unresolved lock privilege design.** Current docs and probes reject unsafe authorization reader/admin/project advisory-lock role profiles. A security-definer wrapper alone is not sufficient if a caller can provide arbitrary lock keys or directly invoke unsafe primitives. Read [lock isolation decision](hosted-lock-isolation-decision.md), [privilege-boundary proposal](postgres-advisory-lock-privilege-boundary-proposal.md), and [runtime audit](hosted-postgres-runtime-audit.md). Do not loosen the startup gate.
2. **RLS and owner concurrency have no live PostgreSQL evidence.** Fake tests and source review cannot establish server trigger ordering, policy enforcement, effective inherited grants or independent-session race outcomes.
3. **No safe disposable PG target is confirmed.** Previous read-only environment inspection found no active listener/service at candidate 5433; 5432 is retired and 5434 is forbidden. The fixture migrates its target, making owner confirmation a prerequisite.

### P1 — Functional/data integrity

4. **Readiness needs exact migration ledger/version validation** for absent, stale, gapped, unknown and checksum-drifted state; never auto-migrate on startup.
5. **Hosted Evidence/retrieval and pool transaction semantics are live-unverified.** Run both authorization and separate Evidence live suites under exact restricted identities; add missing E2E cases where the transport/database path is not represented.
6. **No workspace owner transfer workflow** is provided. Clarify replacement/transfer behavior before production administration even though the database invariant prevents a zero-owner state.
7. **Retention is unspecified.** Duration, holds, restore, purge authority, dependencies, audit and recovery need product/operations decision before destructive behavior.

### P2 — Deployment/operations

8. **Neon role and pool profile is unverified.** Validate actual managed-role membership, RLS bypass, object ownership, lock privileges and pooled/direct operation on an isolated branch.
9. **Deployment operations are not evidenced.** Need secret source/rotation, migration rollout/recovery, connection budget, TLS/ingress, monitoring/alerts, backup/restore and incident procedure.
10. **Hosted retrieval is lexical.** Semantic/PageIndex hosted persistence is not enabled and should not be claimed as supported.

### P3 — Documentation

11. Several docs predate hosted Evidence tools and active-owner enforcement. Reconcile current status while retaining clearly marked historical audit sections.
12. Report exact skip reasons in CI; aggregate skips alone cannot demonstrate the live PostgreSQL gate ran.

## Neon compatibility

This is source-level analysis only; it is neither a Neon test nor deployment approval.

| Area | Assessment | Required proof |
|---|---|---|
| PostgreSQL features | Migrations use standard PostgreSQL tables, constraints, indexes, triggers, PL/pgSQL, transaction advisory locks and RLS. No `CREATE EXTENSION` statement was found in packaged migration SQL. Compatibility is plausible, not proven. | Apply all migrations and run the complete live matrix on a new isolated Neon branch at the selected PostgreSQL version. |
| Roles / ownership | Celestin expects distinct migration owner, NOLOGIN capability groups, project login roles and restricted authorization logins. Neon documents that default/console/API-created roles can inherit `neon_superuser`, which includes broad powers and (for newer projects) `BYPASSRLS`; SQL-created roles require explicit grants. See [Neon role management](https://neon.com/docs/manage/roles). | Provision least-privilege roles with a reviewed supported path; inspect effective memberships, owners, ACLs and `BYPASSRLS` using exact Celestin probes. Never use an elevated Neon owner for application requests. Confirm migration role's actual DDL/ownership abilities. |
| RLS | Core PostgreSQL RLS is available in principle, but Celestin maps `session_user` to a protected project scope. A role with RLS bypass or object ownership can defeat assumptions. | Exercise all 12 protected relations, CRUD, missing scope, forged GUC, role switching, and inherited role cases on Neon under runtime identities. |
| Pooling | Neon pooled endpoints use PgBouncer transaction mode. Neon documents that session settings and session advisory locks are unsupported through transaction pooling. Celestin sets transaction scope, checks `session_user`, and resets with `ROLLBACK`/`DISCARD ALL`; compatibility of this combination is unknown. See [Neon pooling guidance](https://neon.com/docs/connect/connection-pooling). | Test exact app pool and driver behavior. If pooler breaks reset/session semantics, use direct connections with bounded app pools or revise implementation without weakening isolation. Migrations use direct connection. |
| Advisory locks | Runner uses a session advisory lock; authorization paths use transaction advisory locks and ACL probes. Runtime lock wrapper/grant safety is unresolved even on a local server. | Verify provider permits required narrowly scoped authority; test no direct unsafe invocation and all concurrency cases. If unsupported, revise lock model and tests before opening readiness. |
| Extensions | No extension prerequisite appears in current packaged migration SQL. Optional local semantic/PageIndex features are not hosted storage. Neon extension availability is version-dependent; consult [current extension docs](https://neon.com/docs/extensions/pg-extensions) if a hosted feature later adds one. | Do not add `CREATE EXTENSION` requirements without a shipped feature needing one; verify availability on the intended version/branch first. |
| Migrations | Explicit runner relies on session lock and controlled object ownership. Neon recommends a direct/non-pooled connection for migrations. | Run `celestin-migrate` with reviewed restricted migration identity and direct endpoint on isolated branch; verify recovery/retry and DDL ownership. |
| Database/schema ownership | Hosted readiness rejects runtime roles owning protected objects; managed provider ownership may differ from local PostgreSQL. | Confirm owner of database, `public`, `celestin_private`, functions and tables; review ACL/default ACL and migration privileges. Keep runtime login non-owner and non-bypass. |

## Fastest safe completion sequence

### 1. Resolve the hosted authorization-lock privilege design — P0

- **Files:** `docs/hosted-lock-isolation-decision.md`, `docs/postgres-advisory-lock-privilege-boundary-proposal.md`, `docs/hosted-postgres-runtime-audit.md`; after design acceptance only, add a forward migration `src/orbit/postgres/migrations/0012_*.sql`, adjust readiness in `src/orbit/mcp_server/hosted_server.py`, and extend `tests/test_hosted_server.py` plus `tests/postgres/test_live_authorization.py`.
- **Change / reason:** Define who may acquire request/workspace/principal locks and prevent untrusted lock-key selection or direct execution of unsafe primitives. Confirm the migration/provider owner can install reviewed grants. This is why hosted startup remains closed.
- **Verify:** Offline catalog allow/deny cases; migration order/checksum tests; then live direct-call denials, role-profile probes, revocation/request races and owner concurrency. Readiness accepts only the exact profile. Any database-wide built-in ACL change requires explicit impact review.
- **Depends on:** Design review can start now. SQL change depends on task 2 and owner review. Never alter migrations 0001–0011.

### 2. Establish a fresh disposable PostgreSQL target and run guarded tests — P0

- **Files:** operator/environment setup; harness/docs only if a real gap appears: `tests/postgres/conftest.py`, `tests/postgres/live_support.py`, `docs/postgres-integration-tests.md`.
- **Change / reason:** Database owner provisions a new isolated Celestin test database with loopback endpoint, name matching `celestin_it_YYYYMMDD_<nonce>`, and least-privileged migration, auth-admin, auth-reader, project-A and project-B logins plus NOLOGIN groups. Set required `CELESTIN_TEST_POSTGRES_*` secrets securely. Earlier inspection did not confirm a listener on 5433; reconfirm. Never use retired 5432 or prohibited 5434. Never drop, reset or truncate; fixture applies migrations after strict fresh-target checks.
- **Verify:** Review sanitized target identity and distinct roles without printing secrets. With explicit owner approval only, run the exact guarded commands in `docs/postgres-integration-tests.md` for `tests/postgres/test_live_authorization.py`, then separately `tests/postgres/test_live_evidence_store.py`. Record version, exact counts and migration state. Preserve ambiguous/failed targets.
- **Depends on:** Owner-created/confirmed target and resolution of any lock prerequisite needed for security acceptance. No connection was attempted in this audit.

### 3. Finish tenant isolation and owner live matrix — P0/P1

- **Files:** `tests/postgres/test_live_authorization.py`, `tests/postgres/test_live_evidence_store.py`, `tests/postgres/live_support.py`; affected implementation only for reproduced defects, using a new forward migration if schema changes.
- **Change / reason:** Validate per-project/workspace SELECT/INSERT/UPDATE/DELETE, absent/mismatched scope, forged GUC, role switching, suspended/revoked user/service grants, archive denial, source/evidence references, rollback, pool reuse and concurrent last-owner changes under restricted logins.
- **Verify:** Both live suites pass on the approved disposable server; inspect applied ledger, role/policy catalogs and trigger profile. Do not skip or weaken failing tests.
- **Depends on:** Tasks 1–2.

### 4. Make readiness prove exact schema version — P1

- **Files:** `src/orbit/mcp_server/hosted_server.py`, `src/orbit/mcp_server/hosted_preflight.py`, `tests/test_hosted_server.py`, `tests/test_hosted_server.py`, `docs/shared-storage-readiness.md`.
- **Change / reason:** Compare applied migration versions/checksums with packaged contiguous migrations; diagnose absent, behind, gap, unknown, checksum drift, missing schema objects, policies or triggers. Never run migrations automatically.
- **Verify:** Offline tests for each drift and safe actionable `/readyz` output; live ready on target after explicit operator migration; safe catalog mutation cases return unavailable. Keep credentials and SQL values out of diagnostics.
- **Depends on:** Task 2; task 3 is recommended for live confirmation.

### 5. Complete hosted HTTP/MCP Evidence end-to-end coverage — P1

- **Files:** `src/orbit/mcp_server/hosted_server.py`, `hosted_identity.py`, `src/orbit/postgres/unit_of_work.py`, `tests/test_hosted_server.py`, `tests/test_hosted_auth.py`, live PostgreSQL tests and hosted docs.
- **Change / reason:** Test each of six Evidence operations through HTTP/MCP token verification, workspace selection, scope resolution, authorization and UoW. Add missing cases for absent token/scope, wrong workspace/project, read-only grant, suspended/revoked member, archived project, foreign source and DB failure. Each denial should happen before mutation; preserve local MCP contracts and safe error mapping.
- **Verify:** In-process HTTP tests with generated JWT keys plus live integration tests; authorized create/read/associate/verify/supersede succeeds; failure after partial write rolls back.
- **Depends on:** Tasks 2–4.

### 6. Obtain lifecycle policy decisions before destructive implementation — P1

- **Files:** `docs/shared-storage-readiness.md` and a focused accepted policy record; later, only approved scoped source/migration/tests such as `src/orbit/postgres/provisioning.py` and a new migration.
- **Change / reason:** Decide owner transfer/replacement, archive restoration, retention duration/legal hold, purge authority/dependencies/audit and recovery. Until decided, keep non-destructive metadata and deny unsafe operations; add no guessed purge schedule or worker.
- **Verify:** Derive policy acceptance tests from decisions: owner invariant, authorization, dependency behavior, auditability and recoverability. No policy-dependent deletion can be accepted beforehand.
- **Depends on:** Tasks 1–3; product/operations approval.

### 7. Qualify Neon as a specific provider — P1/P2

- **Files:** provider test config/docs as needed; hosted readiness, migration runner/tests and live suites only for concrete compatibility gaps.
- **Change / reason:** On a new isolated Neon branch, validate role memberships, object ownership, `BYPASSRLS`, advisory lock grants, direct migration path, driver reset and transaction pooler semantics. Choose direct bounded app pools if pooler behavior is incompatible; do not weaken isolation to fit provider defaults.
- **Verify:** Run tasks 2–5 against the branch under exact production role profile and pool path; capture provider/server version and readiness report. No Neon connection occurred for this audit.
- **Depends on:** Tasks 1–5 and explicit provider environment authorization.

### 8. Complete deployment and recovery readiness — P2

- **Files:** Existing CI/deploy workflow files (inspect before changing), hosted configuration docs, `README.md` only if setup changes, `docs/shared-storage-readiness.md`, `CHANGELOG.md`, `AI_USAGE.md`.
- **Change / reason:** Define secret separation/rotation, TLS/ingress, migration release/recovery, connection budget, pool/acquire/statement timeout, shutdown/cancellation, metrics/redacted logs, alerts, backups, restore objective/drill and incident owner. Keep SQLite local behavior independent.
- **Verify:** CI checks offline/live suites; approved deployment smoke test authenticates an actual client; restore drill meets approved target; failure/load/connection tests pass without secrets in logs.
- **Depends on:** Tasks 1–7 and operations/product decisions.

### 9. Full verification and documentation reconciliation — P2/P3

- **Files:** `docs/shared-storage-readiness.md`, `docs/postgres-integration-tests.md`, `docs/hosted-postgres-runtime-audit.md`, `README.md` if instructions change, `CHANGELOG.md`, `AI_USAGE.md`.
- **Change / reason:** Reconcile stale claims, distinguish implemented from live-verified, record exact skip marker reasons and release limitations.
- **Verify:** Full offline suite; guarded live auth and Evidence suites; migration validation; hosted transport/security tests; configured static/type/lint checks; documentation links and diff whitespace. Report exact pass/fail/skip without hidden failures.
- **Depends on:** Tasks 1–8.

## Product decisions still open

1. Who can transfer ownership and what replacement process applies when an owner leaves or is suspended?
2. What are archive restore and retention durations, including legal hold and evidence dependencies?
3. Who may purge, what dependencies are retained/deleted, and what audit/recovery is required?
4. Which Neon region/plan, direct-vs-pooled path, connection budget and recovery objective are selected?
5. Is hosted launch lexical retrieval only? Hosted semantic/PageIndex and producer conversion are not completed capabilities.

These questions block destructive lifecycle work and production sign-off; they do not block non-destructive audit or live test setup design.

## Production-readiness decision

**NOT READY for production or untrusted hosted multi-tenant exposure.** The current fail-closed posture is appropriate. Sign off only after lock authority is resolved and tested; live migrations, owner concurrency, RLS CRUD/missing-scope and pool tests pass under least-privileged roles; readiness verifies the exact migration ledger; hosted HTTP/MCP E2E tests pass; the selected provider is verified; and deployment, retention, backup/recovery and operations decisions are documented and exercised.

## References

- [Shared-storage readiness](shared-storage-readiness.md)
- [PostgreSQL integration tests](postgres-integration-tests.md)
- [PostgreSQL migrations](postgres-migrations.md)
- [Hosted runtime audit](hosted-postgres-runtime-audit.md)
- [Hosted lock isolation decision](hosted-lock-isolation-decision.md)
- [Advisory-lock privilege boundary proposal](postgres-advisory-lock-privilege-boundary-proposal.md)
- [Neon role management](https://neon.com/docs/manage/roles)
- [Neon connection pooling](https://neon.com/docs/connect/connection-pooling)
- [Neon extension support](https://neon.com/docs/extensions/pg-extensions)
