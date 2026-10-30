# Change log

Record each meaningful project change during the event, newest first. Update this file in the same commit as the change. Use a real timestamp with timezone, for example `YYYY-MM-DDTHH:MM:SS+05:30`.

## Event build log

## 2026-10-30T14:06:27+05:30 — Audit shared-storage platform readiness

- Changed: Added `docs/shared-storage-readiness.md` with the verified local/hosted architecture, storage capability register, migration and isolation limits, operations gaps, and a bounded SQLite Evidence implementation candidate.
- Why: Establish current source and offline-test evidence before changing the shared-storage foundation.
- Validation: Offline baseline on `origin/main`: selected full storage/runtime suite 1,044 passed, 4 failed, 6 skipped; focused storage/runtime subset 974 passed, 6 skipped. The four full-selection failures are recorded in the report. `git diff --check`, cached diff check, report whitespace scan, and relative Markdown link scan passed.
- Limitations: No source or schema changed in this milestone. No database, SQL, migration, provider, external service, or deployment operation occurred. No live PostgreSQL behavior is verified.

## 2026-10-24T21:30:00+05:30 — Add typed knowledge read result boundary

- Changed: Added `orbit.project_state.knowledge_read` (`KnowledgeReadResult`, outcome/coverage/failure/record-issue enums and types) and `tests/project_state/test_knowledge_read.py`. Outcome (succeeded, failed, scope mismatch), coverage (unknown, partial, reported complete), record-level issues, and limitations are separate fields; empty success differs from failure; there is no "verified complete" status.
- Why: Give the future knowledge reader a boundary that cannot present an unestablished or source-claimed coverage as proven completeness.
- Validation: focused new tests only, system Python with `PYTHONPATH=src`; no full suite, database, or network checks.
- Limitations: Internal model only. No reader implements it, no enumeration API is assumed, and coverage semantics and failure kinds need teammate approval before any producer uses them. Not wired to the reconstructor or resolver.

## 2026-10-24T21:00:00+05:30 — Add trusted local project resolver

- Changed: Added `orbit.project_state.local_projects` (`LocalProjectResolver`, `LocalProjectBinding`, typed `ProjectResolutionError` subclasses) and `tests/project_state/test_local_projects.py`. The resolver maps an exact local project ID to a configured absolute root and checks that root is the Git worktree root, using a `GIT_*`-scrubbed environment with lazy fetch disabled.
- Configuration: supplied by the caller as a mapping or via `LocalProjectResolver.from_json('{"id": "/abs/root"}')`. No registry file, environment variable, or hosted source is read; IDs are not inferred from the cwd or path names, and are not equated with hosted or retrieval project keys.
- Why: Resurrection needs a trusted project-to-repository binding before reconstruction (see `docs/RESURRECTION_INTERFACE_BOUNDARY_REVIEW.md`).
- Validation: new test file only (19 tests passed, run with the system Python and `PYTHONPATH=src`); no full suite, database, or network checks.
- Limitations: Not wired into `ProjectStateReconstructor`, MCP, or any runtime; no production configuration source exists yet.

## 2026-10-24T17:00:27+05:30 — Propose ACL-independent hosted storage architecture

- Changed: Added `docs/hosted-control-plane-data-plane-architecture.md` with current source facts, a threat comparison of shared row locking, per-project databases, isolated per-project workers, and provider alternatives; proposed routing and revocation/drain semantics; migration, provisioning, recovery, provider-decision, and live-test requirements. No application source or tests changed.
- Why: Evaluate a cross-project data boundary that does not depend on unverified built-in advisory-lock ACL changes, while keeping the current hosted gate closed and preserving local SQLite/stdio.
- Validation: The hosted server/scope, fake authorization, request-UoW, provisioning, and fake migration-runner suites passed before and after the documentation change (569 passed each run). Eleven relative Markdown links resolved. `git diff --check` and `git diff --cached --check` passed. No Python modules changed.
- Limitations: The architecture and revocation protocol are proposals awaiting product/security, storage/database, operations, and provider review. No database, SQL, migration, Neon, Auth0, or external-service operation occurred. SQLite persistence tests were not run because this task prohibited database/SQL operations. No live behavior or provider capability is verified.

## 2026-10-24T16:46:46+05:30 — Prepare guarded PostgreSQL retrieval tests

- Changed: Added an opt-in project-runtime retrieval integration case, an offline regression that proves the live fixture skips before configuration/driver/migration access without explicit opt-in, and a scripted PostgreSQL-adapter test for authoritative row reload and omission of stale/ineligible candidates. Clarified hosted lexical retrieval coverage and limits in the PostgreSQL test guide.
- Why: Prepare safe future retrieval validation without contacting a database now or presenting local semantic/hybrid and Evidence adapter tests as hosted request-path evidence.
- Validation: Baseline focused suite: 141 passed, 35 PostgreSQL-marked tests skipped. Final focused retrieval/MCP/SQLite/auth/UoW suite: 226 passed, 43 PostgreSQL tests skipped. Offline migration-runner/provisioning tests: 33 passed. Guard/adapter spot checks: 2 passed; changed tests compiled; staged and unstaged diff checks passed.
- Limitations: No live PostgreSQL/Neon connection or migration was attempted. Hosted project-runtime Evidence access remains intentionally unavailable; live semantic/hybrid retrieval is not implemented in the hosted path.
- Correction: The first post-change run exposed a test double that replaced process-global `os.environ` and broke pytest's own reporting (1 passed, 147 errors). The stub was narrowed to the fixture module and the final runs passed.

## 2026-10-24T16:26:35+05:30 — Harden retrieval provenance diagnostics and regressions

- Changed: Made stale-vector diagnostics refer to missing authoritative project-scoped records rather than SQLite specifically. Added deterministic tests for adversarial/stale semantic metadata, foreign and missing message rows, hybrid deduplication, and foreign-project evidence references.
- Why: The retrieval service is storage-port based, and search metadata must come from scoped persisted records; diagnostics should not misstate the backing store.
- Validation: Focused retrieval tests: 48 passed, 1 skipped (`sqlite_vec` unavailable). Relevant retrieval, database/SQLite, MCP, authorization, end-to-end stdio, and request-UoW suites: 277 passed, 6 skipped (`pageindex` and `sqlite_vec` unavailable). Compilation and Git diff checks passed.
- Limitations: PostgreSQL adapter tests are fake-backed; no live PostgreSQL/Neon, database, or migration behavior was checked. Indexed semantic vectors can still be stale for ranking until the existing index synchronization runs, but persisted result metadata remains authoritative.

## 2026-10-24T16:12:51+05:30 — Preserve speaker role in memory retrieval

- Changed: Retrieval now rehydrates search hits from authoritative project-scoped message rows and returns the persisted speaker role through the existing nullable `role` result field. Updated retrieval contract documentation and added SQLite, scripted PostgreSQL/UoW, semantic-only, MCP, and stdio regression coverage.
- Why: Search results already exposed a `role` field, but it was always `null`, losing available source context and traceability.
- Validation: Relevant offline retrieval/MCP/UoW suite: 242 passed, 6 skipped because optional `pageindex` and `sqlite_vec` packages are unavailable. Changed Python files compiled; `git diff --check` and `git diff --cached --check` checked.
- Limitations: Synthetic adapter tests do not validate live PostgreSQL. Hosted PostgreSQL runtime wiring, authorization behavior, public MCP keys, and request arguments were not changed.

## 2026-10-24T15:58:20+05:30 — Compare hosted isolation architectures

- Changed: Added `docs/hosted-isolation-architecture-decision-matrix.md`, comparing scope-mapped advisory wrappers, database-mediated authorization, row locks, per-project databases, and a provider change against shared threats and operational dimensions.
- Why: Make the architecture choice, revocation/timeout policy, provider evidence, and separate approval gates reviewable without selecting or implementing an unapproved security design.
- Validation: Current source and supporting design documents inspected; all six relative Markdown links resolve, new-document trailing-whitespace check is clean, and `git diff --check` plus `git diff --cached --check` pass. No tests or database operations run.
- Limitations: Architecture remains blocked pending product/security and storage-owner decisions and provider capability evidence. No live PostgreSQL/Neon behavior is verified; hosted mode remains disabled.

## 2026-10-24T13:33:03+05:30 — Specify hosted authorization and revocation protocol

- Changed: Added `docs/hosted-authorization-protocol.md` documenting verified application/database identity boundaries, request/revocation transaction sequences, coordination alternatives, advisory-lock operations, offline-readiness limits, and open approval/provider gates. Cross-linked the audit from the lock-isolation decision, added transaction-isolation semantics to LI-06, and added live acceptance case T-19.
- Why: Make the trusted authorization scope and revocation ordering reviewable before any migration 0010 or hosted enablement decision.
- Validation: Fresh selected offline hosted/auth/UoW/provisioning/migration-runner/SQLite/MCP suite: 662 passed; documentation whitespace and Git diff checks passed. No source modules changed.
- Limitations: Design remains proposed; there is no live PostgreSQL/Neon evidence, no transaction isolation contract in the current request code, and no owner approval for lock architecture or revocation semantics.

## 2026-10-24T13:13:15+05:30 — Add offline hosted onboarding readiness assessment

- Changed: Added a pure offline hosted onboarding evaluator that validates project/configuration bindings, expected profile inventory, evidence freshness/target metadata, and supplied catalog rows using the existing hosted profile validators. Request-runtime and privileged preflight assessments are independent; output is structured and redacted. Added adversarial tests and `docs/hosted-provisioning-readiness.md`.
- Why: Make future operator onboarding observable and testable without provisioning roles or treating synthetic catalog evidence as live PostgreSQL/Neon proof.
- Validation: Selected offline hosted/auth/UoW/provisioning/migration-runner/SQLite/MCP suite: 662 passed; focused offline workflow tests: 15 passed; Python compilation and `git diff --check` passed. `git diff --cached --check` was also run without staging.
- Limitations: Current source continues to block full reader/admin readiness because lock-wrapper scope is unverified. Offline catalog structure never grants permission to serve or provision. Neon capability remains unknown; no database, migration, external service, role, ACL, hosted startup configuration, or MCP schema was changed.

## 2026-10-24T12:37:37+05:30 — Define hosted lock-isolation decision gates

- Changed: Added `docs/hosted-lock-isolation-decision.md` and `docs/hosted-lock-isolation-test-matrix.md` with a source-backed threat model, lock-scope alternatives, revocation ordering, role/readiness separation, provider-dependent blockers, and migration 0010 gates.
- Why: Resolve the architectural review boundary before any forward migration or database operation, without treating offline fixtures as live PostgreSQL/Neon evidence.
- Validation: Selected offline hosted readiness, authorization, request UoW, provisioning, and migration-runner suites: 535 passed. `git diff --check` and documentation whitespace inspection passed.
- Limitations: No source, tests, migrations, database, Neon service, roles, ACLs, or hosted runtime changed. The design remains a proposal; hosted tenant isolation is not ready. No Python modules changed, so compilation was not applicable.

## 2026-10-24T12:27:34+05:30 — Clarify advisory-lock security design gates

- Changed: Added a proposal-only addendum to `docs/postgres-advisory-lock-privilege-boundary-proposal.md` comparing migration and readiness profiles, trusted lock scope, threat impacts, role separation, request versus privileged readiness, and forward-migration recovery gates.
- Why: Make source-level mismatches and unresolved Neon/provider prerequisites explicit before designing migration 0010.
- Validation: Reviewed the full documentation diff; `git diff --check` passed. No tests were run for this documentation-only change.
- Limitations: This is a proposal, not an approved design or live catalog validation. No database, migration, external service, role, ACL, or hosted runtime was changed; tenant isolation remains not ready.

## 2026-10-24T12:15:05+05:30 — Separate request readiness from privileged preflight

- Changed: Added an offline regression in `tests/test_hosted_server.py` proving request-pool readiness checks the authorization-reader and configured project pools without invoking privileged authorization-admin readiness.
- Why: Keep operator-only provisioning checks outside ordinary hosted request startup.
- Validation: Hosted readiness suite: 464 passed; selected offline regression suite including the opt-in live authorization module without its opt-in flag: 617 passed, 35 skipped; changed test module compiled; `git diff --check` and `git diff --cached --check` passed.
- Limitations: The test verifies the application call boundary with fakes; it does not validate live PostgreSQL readiness or provider behavior. Hosted tenant isolation remains not ready.

## 2026-10-24T11:28:08+05:30 — Harden advisory ACL and wrapper readiness

- Changed: Advisory-lock readiness now validates complete ACL entries, grantor/grantee identities, grantability, owner/login identity consistency, and exact effective/direct/PUBLIC access. Reader and authorization-admin wrapper readiness now fails closed while lock keys remain caller-selected and unbound. Added adversarial catalog fixtures, a SQLite connection-pragma regression, and clarified request versus privileged provisioning readiness in the PostgreSQL test guide. Files: `src/orbit/mcp_server/hosted_server.py`, `tests/test_hosted_server.py`, `tests/database/test_engine.py`, `docs/postgres-integration-tests.md`.
- Why: Catalog ACL shape alone cannot make a SECURITY DEFINER wrapper safe when untrusted callers can choose arbitrary advisory-lock keys; the local SQLite connection contract also deserves direct regression coverage.
- Validation: `python -m pytest -p no:cacheprovider -o addopts='' tests/test_hosted_server.py tests/database/test_engine.py` — 473 passed; selected offline regression suite — 616 passed; changed Python modules compiled; `git diff --check` and `git diff --cached --check` passed.
- Limitations: All PostgreSQL catalog tests used offline fixtures. No PostgreSQL server, migration, Neon capability, external service, or hosted tenant-isolation behavior was tested. The tenant-isolation gate remains open.

## 2026-10-24T08:56:32+05:30 — Expand advisory role-profile negative tests

- Changed: Added offline negative cases for every privileged group/member flag and all database-owner attributes checked by authorization-admin function readiness.
- Why: The readiness validator's safety matrix should have direct regression coverage for each privilege-bearing role property and fail closed on incomplete metadata.
- Validation: `python -m pytest -p no:cacheprovider -o addopts='' tests/test_hosted_server.py` — 449 passed. Test module compilation and `git diff --check` passed.
- Limitations: These are fake-catalog tests; no PostgreSQL connection or migration was used.

## 2026-10-24T08:55:29+05:30 — Propose forward-only advisory ACL boundary

- Changed: Added a proposal documenting the verified privilege gap, lock overload/profile matrix, threat model, least-privilege alternatives, forward-only wrapper/ACL operation, privilege preconditions, rollback limitations, and required offline/live acceptance evidence.
- Why: A wrapper-only fix cannot revoke direct advisory calls exposed through PostgreSQL's `PUBLIC EXECUTE`; database-wide ACL authority and blast-radius limits must be reviewed before live testing.
- Validation: Focused combined offline suite — 559 passed, 35 PostgreSQL tests skipped; changed modules compiled; `git diff --check`, cached diff check, proposal existence, and documentation-link checks passed.
- Limitations: The proposal is not approved or applied. No PostgreSQL catalog, SQL, migration, credentials, Auth0, or external service was accessed; the tenant-isolation gate remains open.

## 2026-10-24T08:53:52+05:30 — Exercise admin lock readiness in the live suite

- Changed: Extended the opt-in authorization tests to run the admin readiness probe and validate exact NOLOGIN group membership from PostgreSQL catalogs. Updated integration-test documentation to describe the source validator and its not-yet-wired provisioning-host boundary.
- Why: The admin connection profile and complete role roster need real-catalog acceptance coverage while remaining separate from hosted request pools.
- Validation: `python -m py_compile tests/postgres/test_live_authorization.py` passed. `python -m pytest -p no:cacheprovider -o addopts='' tests/postgres/test_live_authorization.py` collected 35 live tests and skipped all 35 because opt-in was not enabled.
- Limitations: No PostgreSQL connection or migration was made. These additions are unexecuted against a server; admin provisioning readiness is not wired into a production host.

## 2026-10-24T08:53:10+05:30 — Complete advisory-lock source readiness profiles

- Changed: Added authorization-admin catalog validation for the four authorization-write wrappers, separate effective/direct/PUBLIC advisory-function ACL checks, exact privileged NOLOGIN group-roster validation, and fail-closed handling for missing catalog booleans. Added adversarial offline tests.
- Why: Restricted Celestin wrapper grants do not remove PostgreSQL's default `PUBLIC EXECUTE` on advisory built-ins, and readiness must validate the admin helper profile before a future deployment relies on it.
- Validation: `python -m pytest -p no:cacheprovider -o addopts='' tests/test_hosted_server.py` — 433 passed. `python -m py_compile src/orbit/mcp_server/hosted_server.py tests/test_hosted_server.py` and `git diff --check` passed.
- Limitations: Tests use fakes. No PostgreSQL catalog was inspected, no migration or ACL change was applied, and the tenant-isolation gate remains open.

## 2026-10-24T08:36:08+05:30 — Expand guarded tenant-isolation acceptance cases

- Changed: Expanded the opt-in PostgreSQL suite with role ACL profiles, direct lock attempts, policy/trigger catalog checks across all protected tables, both exposed content tables, additional revocation races, cancellation cleanup, and pool session-reset coverage. Documented the forward-only ACL/wrapper prerequisite, including that migration 0008's authorization-write helpers remain invoker-security and are not independently readiness-validated.
- Why: The offline checks cannot prove PostgreSQL ACL, RLS, trigger, transaction, or pooled-session behavior; the live suite needs explicit, least-privileged acceptance cases for a future approved disposable target.
- Validation: Selected regression run: 472 passed, 35 opt-in tests skipped, and one failure in the existing SQLite process-concurrency test (`database is locked`). An isolated rerun passed (1 passed). Focused hosted suite: 306 passed, 35 skipped. The PostgreSQL opt-in command was not run.
- Limitations: No database was contacted and no migration was applied. Live ACL, isolation, revocation-race, trigger, cancellation, and pool behavior remain unverified; the tenant-isolation gate remains open.

## 2026-10-24T07:18:52+05:30 — Fail closed on advisory-lock and trigger catalog drift

- Changed: Hosted readiness now checks effective `pg_advisory_*` execution across all supported overloads, expects the request lock wrappers to be security-definer functions, and validates the exact authorization-trigger roster, event metadata, function body, ownership, search path, and grants. PostgreSQL pool return now clears session state with `DISCARD ALL` and disables automatic server-side preparation. Added adversarial offline catalog tests.
- Why: PostgreSQL built-in advisory privileges are inherited through `PUBLIC`; revoking Celestin's wrapper grants alone does not remove direct lock access from a project login. Authorization ordering and RLS readiness must fail closed until the built-in ACL and secure-wrapper prerequisites are satisfied.
- Validation: `python -m pytest -p no:cacheprovider -o addopts='' tests/test_hosted_server.py tests/postgres/test_live_authorization.py tests/postgres/test_live_support.py tests/postgres/test_migration_runner.py` — 306 passed, 35 opt-in PostgreSQL tests skipped. Python compilation passed. No PostgreSQL opt-in flag, database, migration, Auth0 request, or external operation was used.
- Limitations: No forward migration was added or applied. The checked-in 0008–0009 setup remains incompatible with the strengthened direct-lock/readiness gate. Live PostgreSQL behavior was not tested; tenant isolation is not declared passed.

## 2026-10-23T22:00:35+05:30 — Separate Evidence adapter tests from the hosted RLS gate

- Changed: Assigned the adapter-level Evidence suite its own pytest marker and documented its exact opt-in command separately from project-login authorization/RLS acceptance. Clarified that these tests currently use a control-plane connection and do not prove project-runtime Evidence access.
- Why: The existing Evidence adapter tests do not exercise the project's restricted login identity and must not be counted as hosted tenant-isolation evidence.
- Validation: The focused combined offline PostgreSQL/readiness suite completed with 226 passed and 28 opt-in live tests skipped; the suite was run without the PostgreSQL opt-in flag. Documentation and staged-diff whitespace checks passed.
- Limitations: No live PostgreSQL target was configured or used. Hosted project-runtime Evidence grants and request composition remain unsupported and require a reviewed contract.

## 2026-10-23T22:00:08+05:30 — Require safe PostgreSQL test-role ownership and memberships

- Changed: The guarded live-test preflight now requires the migration login to own the target database and rejects runtime-role membership with `ADMIN OPTION`; added regression coverage for both checks.
- Why: The disposable test harness must not begin migration setup with an unexpected database owner or roles capable of granting themselves further membership.
- Validation: `python -m pytest -p no:cacheprovider -o addopts='' tests\postgres\test_live_support.py` — 73 passed. No PostgreSQL connection or migration was run.
- Limitations: Database ownership and role catalog results were tested with fakes only; the live target still requires explicit owner approval and PostgreSQL verification.

## 2026-10-23T21:58:56+05:30 — Harden hosted PostgreSQL tenant readiness and locking

- Changed: Replaced substring-based readiness checks with fail-closed catalog comparisons for expected RLS policies and authorization helpers; expanded effective-privilege and role-membership checks; added migration 0009 and request routing so project logins can lock only their mapped workspace while the authorization reader retains broad locks.
- Why: Hosted startup must reject policy/helper drift and escalation, and a project credential must not be able to hold arbitrary authorization locks against another tenant.
- Validation: `python -m pytest -p no:cacheprovider -o addopts='' tests\test_hosted_server.py` — 100 passed. The final combined offline suite also passed (226 passed, 28 live skipped); Python compilation and diff checks passed.
- Limitations: Catalog expectations and migration 0009 have not been checked against a live PostgreSQL server. No database connection, SQL execution, migration, Auth0 request, or external operation was performed.

## 2026-10-23T20:51:32+05:30 — Guard project-role PostgreSQL integration tests

- Changed: Extended the existing live PostgreSQL fixture to require an explicit disposable-target acknowledgement and distinct pre-provisioned authorization/project logins. Added direct-SQL tests for forged GUCs, cross-project access, role switching, and scope-map protection; clarified the live-test setup requirements.
- Why: The RLS isolation claim requires tests to exercise actual project login identities and must not run against an unknown database target.
- Validation: `python -B -m pytest -p no:cacheprovider -o addopts='' tests/postgres/test_live_support.py tests/postgres/test_live_authorization.py` — 71 passed, 20 live PostgreSQL tests skipped. Changed test modules compiled; `git diff --check` and `git diff --cached --check` passed.
- Limitations: The live cases were skipped because no approved disposable PostgreSQL target was configured. SQL, RLS, role, and advisory-lock behavior remains unverified against a server.

## 2026-10-23T20:50:53+05:30 — Route hosted requests through project PostgreSQL pools

- Changed: Updated the hosted runtime to use a read-only authorization pool first, then select only the configured project-specific pool for the resolved scope. Added final in-transaction authorization and startup role checks; documented the configuration and trust boundary while preserving local stdio/SQLite behavior.
- Why: Project-scoped database identities must be selected only after application authorization and checked again in the data transaction.
- Validation: `python -B -m pytest -p no:cacheprovider -o addopts='' tests/test_hosted_server.py` — 53 passed. The hosted module compiled; `git diff --check` and `git diff --cached --check` passed.
- Limitations: Hosted mode remains disabled by default. No live PostgreSQL target, credentials, Auth0 service, or deployment was used; database role and RLS behavior remains unverified.

## 2026-10-23T20:50:28+05:30 — Bind PostgreSQL RLS to project logins

- Changed: Added migration `0008_project_scoped_runtime_roles.sql`, a protected `session_user`-to-project scope map, and authorization-lock coordination for project login provisioning and revocation.
- Why: The former shared runtime credential could choose caller-settable tenant GUCs. Project-specific database identities provide the tenant scope used by RLS policies.
- Validation: `python -B -m pytest -p no:cacheprovider -o addopts='' tests/postgres/test_migration_runner.py tests/postgres/test_provisioning.py tests/postgres/test_request_unit_of_work.py tests/postgres/test_hosted_authorization.py` — 71 passed. Changed PostgreSQL modules compiled; `git diff --check` and `git diff --cached --check` passed.
- Limitations: Live SQL/RLS behavior remains unverified; the live PostgreSQL environment was not used. Hosted runtime routing and the guarded live-test suite are recorded in subsequent entries.

## 2026-10-23T19:35:21+05:30 — Document PostgreSQL RLS trust boundary

- Changed: Added a source-derived inventory for all twelve content tables protected by migrations 0006–0007, listed the authorization/control tables without RLS, and documented that the current custom-GUC scope can be set by the shared runtime role. Clarified that existing live-owner tests do not prove direct-SQL isolation under the application role.
- Why: Prevent the current scope predicates and migration-owner tests from being mistaken for a defense against direct SQL using the hosted runtime credential.
- Validation: Focused offline PostgreSQL/Auth0/UoW/runtime suite — 142 passed, 26 skipped. Full repository suite — 704 passed, 46 skipped after installing the already-declared `rfc8785>=0.1.4,<0.2` only into a temporary validation venv outside the repository. `git diff --check` run after documentation updates.
- Limitations: No database was connected to, inspected, or migrated. No app-role catalog or direct-SQL test ran. A reviewed unforgeable RLS request-context design and explicitly approved fresh local PostgreSQL target/runtime role remain required.

## 2026-10-23T18:53:08+05:30 — Add opt-in hosted Auth0/PostgreSQL runtime

- Changed: Added a separate `celestin-hosted` Streamable HTTP entry point with Auth0 access-token verification, a fixed approved-client allowlist and scopes, explicit workspace context, the existing authorized request UoW for current memory tools, bounded PostgreSQL pool/HTTP settings, TLS requirements, sanitized errors, health/readiness checks, and loopback-by-default binding. Added internal workspace-admin-authorized human/service provisioning and revocation operations. Added migration `0007_memory_request_rls.sql` to apply forced request-scope RLS to `chat_messages` and `project_memory`. Added a runtime-role catalog check for forced RLS, current tool privileges, and absence of excessive/unexposed table privileges. Updated README and PostgreSQL operations documentation.
- Why: Provide a separate, explicit hosted request path while keeping local stdio/SQLite commands and public tool schemas unchanged, and ensure hosted database access fails closed when authentication or database role/RLS prerequisites are unsafe.
- Validation: Python 3.13.9 / pytest 8.4.2: focused hosted/auth/UoW/provisioning/live-test collection command — 127 passed, 26 skipped; broad suite excluding `tests/evidence/test_canonicalization.py` — 663 passed, 46 skipped; changed-Python syntax compilation — 22 files passed; `git diff --check` and `git diff --cached --check` — passed. Unfiltered suite attempted and blocked at collection because the active environment lacks the declared `rfc8785` dependency.
- Limitations: No Auth0 tenant/configuration, secrets, PostgreSQL driver, approved disposable target, database connection, or migration run is available or claimed. Live PostgreSQL/RLS/runtime-role behavior and Auth0 end-to-end behavior remain unverified. Initial workspace/owner bootstrap, Auth0 account deactivation synchronization, durable administrative audit, rate limits/quotas, production proxy/monitoring/backup/restore procedures, Capture conversion, and D-08 delivery receipts/equality/replay remain outside this change. Human security review is pending.

## 2026-10-20T15:52:41+05:30 — Clarify producer-qualified candidate identity

- Changed: Marked the older `(project, candidate_id)` uniqueness and generic replay wording in `docs/evidence-linked-memory-contract.md` as a superseded prototype assumption, and distinguished v0.3 revision-scoped conversion retry behavior from unresolved D-08 delivery/revision payload equality.
- Why: Prevent the historical prototype table from appearing to override the authoritative producer-qualified identity contract.
- Validation: Compared the wording with `docs/SHARED_MEMORY_INTERFACE_CONTRACT_V0.3_DECISIONS.md`; `git diff --check` passed.
- Limitations: D-08 delivery/revision payload-equality details remain unresolved. No code, schema, migration, or runtime behavior changed.

## 2026-10-19T18:22:59+05:30 — Add opt-in live PostgreSQL Evidence tests

- Changed: Added seven opt-in PostgreSQL integration cases for migrations 0005–0006, Evidence/Knowledge round-trips, attachment idempotency, verification updates, supersession, project isolation, authorization denial, forced RLS, database constraints, savepoint recovery, and outer rollback. Reused the existing fresh-disposable-target fixture and request-bound authorization/UoW. Clarified skip reasons and corrected stale adapter/schema status documentation.
- Why: Exercise the current Evidence adapter and migrations against real PostgreSQL when an explicitly configured safe target is available, without creating another database setup path or implying Capture conversion/runtime integration.
- Validation: Python 3.13.9 / pytest 8.4.2, focused offline command — 92 passed, 7 live tests skipped. The live module was also collected with skip reasons — 7 skipped because the opt-in flag and approved target are not configured. Changed Python modules compiled; `git diff --check` and cached diff check passed.
- Limitations: No PostgreSQL target variables were configured, so the live fixture was not enabled and no connection or migration ran. The live cases and server-side SQL behavior remain unverified. The test authenticator is synthetic and does not verify real transport credentials; hosted MCP remains disabled.

## 2026-10-19T11:30:35+05:30 — Add shared-memory PostgreSQL schema foundation

- Changed: Added migration `0006_candidate_revision_store.sql` with project-scoped producer/source namespace and candidate identities, sequential immutable candidate revisions, append-only review events, candidate evidence records, ordered duplicate-preserving occurrence links, and a conversion-ledger relationship to existing knowledge. Added row-level project/workspace policies and set their transaction-local scope from the authorized request unit of work. Added static migration-definition assertions and scripted UoW scope tests; updated migration documentation.
- Why: Establish relational constraints for the approved candidate/revision and occurrence model while keeping delivery replay out until its separate equality profile is defined.
- Validation: `python -m pytest -p no:cacheprovider -o addopts='' tests/postgres/test_request_unit_of_work.py tests/postgres/test_migration_runner.py tests/postgres/test_hosted_authorization.py tests/postgres/test_hosted_identity_boundary.py tests/evidence/test_postgres_store.py tests/evidence/test_service.py tests/capture/test_pipeline.py tests/test_hosted_scope.py` — 156 passed. `python -m py_compile src/orbit/postgres/unit_of_work.py tests/postgres/test_request_unit_of_work.py tests/postgres/test_migration_runner.py`, `git diff --check`, and `git diff --cached --check` passed.
- Limitations: The D-08 delivery/revision equality projection/profile remains unresolved, so no delivery receipt, delivery-ID uniqueness, or replay implementation was added. No candidate ingress/conversion service, normalized conflict-edge/pending-endpoint graph, or audit-event storage is implemented. Migration SQL has not been run or validated against PostgreSQL; no database or Neon resource was contacted. MCP remains SQLite-backed.

## 2026-10-19T11:16:13+05:30 — Add request-bound PostgreSQL Evidence/Knowledge persistence

- Changed: Implemented the current manual `EvidenceLinkedMemoryStore` contract in a PostgreSQL adapter, added its additive migration, and exposed it through the authorized request unit of work using the resolver-issued project scope and borrowed connection. Added scripted tests for round-trip fields, project isolation, atomic create/supersede, idempotent evidence attachment, rollback, and transaction sharing.
- Why: Establish a durable adapter boundary for existing Evidence/Knowledge operations without inventing producer delivery/revision semantics or wiring incomplete hosted functionality into MCP.
- Validation: Focused Evidence/UoW/migration tests — 72 passed; broader Evidence/Capture/hosted authorization/Retrieval/MCP regression set — 214 passed; changed Python files compiled; `git diff --check` and `git diff --cached --check` passed.
- Limitations: Tests use scripted connections only; PostgreSQL syntax and live transaction/concurrency behavior were not verified against a server. Migration `0005` is not applied. The MCP runtime remains SQLite-backed. Capture occurrence persistence, candidate approval/revision, delivery retry, conversion, shared-memory retrieval, and authenticated hosted runtime integration remain unimplemented.

## 2026-10-19T10:30:03+05:30 — Add default-deny hosted memory capabilities

- Changed: Extended hosted project access records and scope resolution with `candidate:approve`, `memory:convert`, `memory:history`, and `audit:read`; approval is limited to active human project members. Added migration `0004_memory_capabilities.sql`, with new human and service grants defaulting to false, updated scripted authorization and migration tests, and documented the migration.
- Why: The approved v0.3 workflow requires separate project capabilities; the existing hosted grants only represented generic read/write access.
- Validation: Baseline hosted authorization/identity/request-UoW/migration tests — 48 passed. After the change, `python -m pytest -p no:cacheprovider -o addopts='' tests/postgres/test_hosted_authorization.py tests/postgres/test_hosted_identity_boundary.py tests/postgres/test_request_unit_of_work.py tests/postgres/test_migration_runner.py tests/test_hosted_scope.py` — 68 passed; `python -m pytest -p no:cacheprovider -o addopts='' tests/test_mcp_tools.py tests/test_mcp_protocol.py tests/test_mcp_persistence.py tests/test_mcp_end_to_end.py tests/test_mcp_authorization.py tests/integration/test_capability_flow.py tests/capture/test_pipeline.py tests/evidence/test_service.py` — 122 passed; `python -m py_compile src/orbit/mcp_server/hosted_scope.py src/orbit/postgres/authorization.py tests/postgres/test_hosted_authorization.py tests/postgres/test_request_unit_of_work.py tests/postgres/test_migration_runner.py tests/test_hosted_scope.py`, `git diff --check`, and `git diff --cached --check` passed. No live database, migration, or Neon commands were run.
- Limitations: This does not implement a credential verifier, Evidence/Knowledge tables or adapter, conversion/replay, retrieval from shared knowledge, or hosted MCP wiring. The new migration is not applied and the hosted schema remains a draft. The existing canonicalization suite was attempted but could not collect because `rfc8785` is missing from the active interpreter; no package was installed.

## 2026-10-19T10:03:44+05:30 — Prototype lossless Capture occurrences

- Changed: Added an internal typed occurrence record to CaptureResult and emitted one ordered entry per accepted extracted event, preserving duplicate events and their input evidence references independently from deduplicated candidate evidence. Exported the additive Capture model and added focused regression tests.
- Why: Keep local Capture order and multiplicity observable while retaining the existing candidate identity, candidate merge, and evidence deduplication behavior. The per-call ordinal is not a producer-issued or durable ID.
- Validation: `python -m pytest -p no:cacheprovider -o addopts='' tests/capture/test_pipeline.py tests/integration/test_capability_flow.py tests/evidence/test_service.py` — 84 passed; `python -m py_compile src/orbit/capture/model.py src/orbit/capture/service.py src/orbit/capture/__init__.py tests/capture/test_pipeline.py` — passed; `git diff --check` and `git diff --cached --check` — passed.
- Limitations: This is an in-memory Capture prototype only. It does not provide stable producer occurrence IDs, durable occurrence/evidence association IDs, serialization, conversion persistence, Resurrection conformance, or live database behavior.

## 2026-10-19T09:51:21+05:30 — Prepare delivery contract owner decision packet

- Changed: Added `docs/delivery-contract-owner-decision-packet.md` with role-level questions for the unapproved delivery/producer extensions, dependencies, hypothetical flow, persistence gate, and canonicalization verification steps. Updated this record and `AI_USAGE.md`.
- Why: Obtain explicit producer, product/security, and storage decisions before durable delivery persistence; preserve the requester-authorized v0.3 baseline without claiming teammate approval.
- Validation: Repository/source inspection, focused canonicalization test attempt, Markdown/link review, and diff whitespace checks; exact results are recorded in the task report and AI usage entry.
- Limitations: Canonicalization tests could not collect because the active Python 3.13.9 environment lacks `rfc8785`; no dependency was installed. Cross-runtime vectors and all owner decisions remain unverified/open.

## 2026-10-19T03:10:08+05:30 — Add Evidence store conformance tests

- Changed: Added a reusable conformance mixin for the current
  `EvidenceLinkedMemoryStore` protocol and applied it to the in-memory reference
  fake. It covers represented round-trip fields, project-scoped operations,
  atomic staged-failure behavior, supersession, idempotent attachment, and
  missing-record behavior.
- Why: Give future store implementations a shared offline contract suite without
  implying that a fake proves database transaction or durability behavior.
- Validation: `python -m pytest -p no:cacheprovider -o addopts='' tests/evidence/test_service.py`
  passed: 40 tests (Python 3.13.9, pytest 8.4.2).
- Limitations: This is fake-store contract coverage only. No PostgreSQL adapter,
  schema, or live-database behavior was tested.

## 2026-10-19T03:10:08+05:30 — Test process-level SQLite migration concurrency

- Changed: Added a bounded process-level test that starts two independent
  spawned processes against one fresh temporary SQLite file, then verifies
  migration version, expected schema objects, database integrity, and foreign
  key integrity.
- Why: The prior concurrency coverage exercised threads; separate processes
  verify SQLite's file-lock behavior across independent initializers.
- Validation: `python -m pytest -p no:cacheprovider -o addopts='' tests/database/test_engine.py`
  passed: 9 tests (Python 3.13.9, pytest 8.4.2).
- Limitations: No production migration behavior changed. The test covers a local
  temporary database only.

## 2026-10-19T03:03:17+05:30 — Audit delivery identity contract readiness

- Changed: Added `docs/delivery-contract-decision-audit.md` with a traceable
  D-01–D-13 comparison, separated settled v0.3 requirements from pending
  delivery/producer extensions, documented dependencies, verification limits,
  and an owner questionnaire.
- Why: Avoid implementing delivery persistence from proposal details that the
  requester-authorized conversion baseline does not settle or the local Capture
  prototype cannot demonstrate.
- Validation: Inspected the current decision record, proposal, historical
  boundary/draft documents, Capture/Evidence code and tests, and project test
  setup. `git diff --check` and focused documentation link/whitespace checks
  are recorded in the final report. The focused canonicalization test was
  attempted and could not collect because `rfc8785` is unavailable in the
  current interpreter.
- Limitations: No team owner response or upstream producer implementation was
  available in the checkout. No schema, application, test, dependency, or
  database change was made; no test pass is claimed.

## 2026-10-19T02:56:52+05:30 — Add proposed delivery identity vectors

- Changed: Added explicit non-normative delivery and candidate-revision digest
  examples covering exact retry, changed content, omitted versus null fields,
  Unicode normalization, equivalent timestamp offsets, and rejected timestamp
  edge cases. Clarified the limits of the digest verification environment.
- Why: Make the pending delivery-equality proposal reviewable without treating
  it as an approved contract or changing the final decision record.
- Validation: Re-derived the restricted example digests with Python's
  standard-library JSON encoder and checked links/whitespace plus
  `git diff --check`; this was not a test or general JCS implementation check.
- Limitations: `rfc8785` is not importable from the default Python or repository
  validation environment in this continuation; no second runtime was used.

## 2026-10-19T02:54:13+05:30 — Propose delivery and producer identity boundary

- Changed: Added a source-grounded, unapproved delivery/producer contract
  proposal with separate delivery, candidate, revision, conversion, and digest
  identities; retry rules; illustrative vectors; producer/source lifecycle;
  canonicalization edge policies; and future acceptance tests. Corrected stale
  implementation-status descriptions in the two historical v0.3 review drafts.
- Why: The approved conversion profile does not define delivery equality, and
  the Capture prototype lacks producer, delivery, and immutable revision IDs.
- Validation: Local relative Markdown links and whitespace checked in five
  documentation/log files; `git diff --check` and `git diff --cached --check`
  passed. No tests or database commands ran.
- Limitations: All additional delivery, revision-equality, timestamp-edge,
  comparator, certainty-mapping, and producer-lifecycle rules remain proposals
  pending the relevant owner responses. The final decision record was unchanged.

## 2026-10-19T02:39:40+05:30 — Conform conversion hashing to the v0.3 contract

- Changed: Replaced the provisional conversion JSON encoding with RFC 8785 JCS,
  implemented the approved payload version, NFC and UTC timestamp normalization,
  exact payload validation, ordered occurrences, sorted conflicts, and direct
  lowercase-hex SHA-256. Added the bounded `rfc8785` runtime dependency and fixed
  tests for both decision-record vectors. Updated README dependency/runtime notes.
- Why: Conversion replay comparison must use the canonical bytes and digest
  selected by the final shared-memory decision record.
- Validation: Canonicalization tests: 41 passed; Evidence suite: 81 passed;
  Capture mapping tests: 8 passed, 27 deselected. Both changed Python files
  compiled; `git diff --check` and `git diff --cached --check` passed. Tests used
  `rfc8785` 0.1.4 installed into a temporary target directory.
- Limitations: Delivery retry equality remains undefined and is not implemented.
  Inputs with more than six timestamp fractional digits are rejected rather than
  rounded or truncated because that policy is unspecified. No producer integration
  or persistence behavior was added.

## 2026-10-19T02:27:26+05:30 — Establish shared-memory v0.3 decision baseline

- Changed: Added the final v0.3 decision record and Resurrection boundary review
  from the verified `origin/retrival` ref. Marked the local v0.3 contract and
  decision request as historical where the final record supersedes them.
- Why: Establish one implementation baseline without merging retrieval history
  or importing its virtual environment and generated artifacts.
- Validation: Compared both imported documents to their source Git blobs;
  reviewed the reconciliation diff and `git diff --check`.
- Limitations: No tests, database commands, migrations, or implementation
  changes ran. The provisional canonicalization helper does not match the
  JCS/hash profile selected in the decision record and remains unchanged for a
  separate task.

## 2026-10-18T20:26:33+05:30 — Review shared-memory storage decisions

- Changed: Recorded `NEED DISCUSSION` technical dispositions for ST-01,
  ST-02, and ST-03 in the decision review. Producer and Product/Security
  response fields remain unanswered; no proposal is marked approved.
- Why: Canonicalization, conversion identity, and the transaction boundary
  depend on producer and security decisions that are still unresolved.
- Validation: Reviewed the current decision register; document whitespace and
  `git diff --check` passed. No tests or database commands ran.
- Limitations: The repository does not identify an accountable storage owner.
  Codex's dispositions are technical input, not owner sign-off. Orbit context
  retrieval for project `celestin` returned `ACCESS_DENIED`; no alternate
  project was tried.

## 2026-10-18T14:40:07+05:30 — Make SQLite schema migrations atomic

- Changed: Replaced migration script strings with ordered complete-statement
  tuples, executed each statement inside an explicit `BEGIN IMMEDIATE`
  transaction, rechecked `user_version` after acquiring the write lock, and
  committed the schema changes and version together. Migration failures now
  roll back and close the connection opened by `connect()`.
- Why: Python SQLite `executescript()` can implicitly commit an open
  transaction, and concurrent initializers need a locked version recheck.
- Validation: The focused database, FTS retrieval, persistence, and MCP
  regression command passed: 92 tests. `py_compile` and `git diff --check`
  passed.
- Limitations: Tests used temporary SQLite files; the full suite and all live
  PostgreSQL behavior were not tested.

## 2026-10-17T14:30:21+05:30 — Align merged MCP regression with runtime handlers

- Changed: Updated the adversarial retrieved-message regression to invoke the
  per-runtime `MCPToolHandlers` instance instead of removed module-level tool
  functions.
- Why: The incoming test was written against the pre-composition server API;
  retaining the scenario while using the supported runtime seam preserves the
  test's security assertion and current MCP design.
- Validation: Corrected MCP regression: 1 passed. Full suite after merge:
  567 passed, 38 skipped. `git diff --cached --check` and `git diff --check`
  passed. No live PostgreSQL tests were run.
- Limitations: No live PostgreSQL tests are part of this local regression run.

## 2026-10-17T14:18:41+05:30 — Add hosted PostgreSQL runtime audit

- Changed: Added the dated read-only architecture audit at
  `docs/hosted-postgres-runtime-audit.md`.
- Why: Preserve the reviewed runtime, storage, authorization, migration, and
  integration gaps as a handoff reference; the report describes its 2026-10-16
  repository snapshot and does not claim hosted runtime readiness.
- Validation: Reviewed the report and confirmed it is the only pre-existing
  untracked path. No tests were run; no database or Neon resources were
  accessed.
- Limitations: The audit is historical and should be read with current source
  before making implementation decisions.

## 2026-10-17T14:10:10+05:30 — Cover dangling conflict links in reconstruction

- Changed: Added a fake-backed project-state regression for a loaded knowledge record whose capture conflict points to a candidate absent from the returned records. It checks unresolved conflict classification, no fabricated counterpart, preserved capture-source/evidence provenance, unknown applicability, and no selected winner. Existing pinned-revision and read-only tests were left unchanged; no production or shared interface changed.
- Why: Make reconstruction behavior explicit for incomplete knowledge results without treating omission from `list_records()` as confirmed absence or conflating candidate and knowledge IDs.
- Validation: The focused new test passed (1 passed); `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m pytest -p no:cacheprovider -o addopts='' tests/project_state/test_reconstructor.py` — 21 passed. Python 3.12.3 and pytest 7.4.4; the test uses a fake knowledge source and a temporary Git repository. `git diff --check` passed.
- Limitations: The read-only source protocol has no completeness or endpoint-status signal, so it cannot distinguish a temporarily unavailable counterpart from a confirmed absent one. These tests do not establish persistence, adapter behavior, or runtime integration.

## 2026-10-17T14:04:30+05:30 — Test evidence availability and verification separation

- Changed: Added a fake-backed Evidence service regression for a source changing from `available` to `missing` at read time. It checks that read-time resolution does not mutate the stored reference and remains separate from knowledge/evidence verification, including an explicit `stale` knowledge status. No production code or shared interface changed.
- Why: Make source availability, read-time refresh behavior, and claim verification distinctions executable without implying that an available source proves a claim.
- Validation: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m pytest -p no:cacheprovider -o addopts='' tests/evidence/test_service.py::test_read_time_missing_source_status_is_separate_from_verification` — 1 passed; the same command targeting `tests/evidence/test_service.py` — 33 passed. System `python` was unavailable; system `python3` required `PYTHONPATH=src` because the checkout has no editable install or `.venv`. `git diff --check` passed.
- Limitations: These tests use an in-memory fake store and resolver. They do not verify durable persistence, adapter behavior, trusted capture approval, or end-to-end runtime integration.

## 2026-10-17T13:07:42+05:30 — Narrow hosted PostgreSQL request entry points

- Changed: Made PostgreSQL transaction and request-executor methods internal,
  restricted request UoW creation to its internal binder, hid its session and
  scope fields from the handler-facing API, and separated request-bound adapter
  factories from retained standalone adapter constructors. Added offline tests
  that exercise the request boundary and verify missing authentication cannot
  open a connection.
- Why: Keep normal hosted application composition on the authenticated request
  boundary and prevent accidental substitution of standalone adapters while
  preserving the standalone adapter API for trusted tooling and tests.
- Validation: Hosted identity/UoW/authorization/storage/retrieval tests — 59
  passed; hosted-scope, MCP, and SQLite persistence regressions — 85 passed.
  Python compilation and diff checks passed.
- Limitations: Python code in the same process remains trusted and can call
  underscored internals or open its own connection. No credential verifier,
  workspace-binding provider, hosted MCP wiring, or live PostgreSQL test exists.

## 2026-10-17T12:57:13+05:30 — Define a fail-closed hosted identity boundary

- Changed: Added the `PrincipalAuthenticator` contract and
  `HostedRequestBoundary`, which accepts transport context, requires explicit
  identity/workspace/request dependencies, and delegates to the existing
  authorized request unit of work. Added offline fail-closed tests and clarified
  the current implementation limits in hosted identity documentation.
- Why: Establish a narrow composition seam without selecting an identity
  provider, trusting caller-supplied identity/workspace fields, or enabling
  hosted MCP requests.
- Validation: Offline identity boundary, hosted scope, request UoW,
  authorization, MCP, and SQLite persistence regression command: 110 passed.
  Changed Python modules passed `py_compile`; `git diff --check` passed.
- Limitations: No credential verification implementation, concrete workspace
  binding, hosted MCP wiring, database access, or live authentication testing.

## 2026-10-17T12:00:40+05:30 — Bind PostgreSQL adapters to an authorized request unit of work

- Changed: Added request-bound constructors for PostgreSQL persistence and
  retrieval adapters, plus `PostgresRequestUnitOfWork` and
  `execute_authorized_request` to compose them over the authorization session's
  connection. Updated the hosted identity/scope documentation.
- Why: Ensure hosted authorization checks, source validation, memory operations,
  and retrieval can share one request transaction without adapters acquiring or
  finalizing separate connections.
- Validation: Request UoW plus hosted authorization, PostgreSQL storage, and
  PostgreSQL retrieval tests: 48 passed. SQLite/MCP/hosted-scope and retrieval
  regression command: 241 passed, 6 skipped. Changed Python modules compiled;
  `git diff --check` and `git diff --cached --check` passed.
- Limitations: No authentication provider or MCP hosted runtime integration was
  added. No PostgreSQL/Neon connection or live SQL validation was performed.

## 2026-10-17T09:25:29+05:30 — Add isolated PostgreSQL retrieval adapter

- Changed: Added a `RetrievalData` implementation with explicit workspace/project
  scope, authoritative decision reloads, archive filters, deterministic
  PostgreSQL full-text search, and normalized IDs/timestamps. Added migration
  `0003_retrieval_search_indexes.sql` with partial GIN expression indexes.
- Why: Prepare PostgreSQL retrieval behind the existing data port without
  enabling hosted requests or changing SQLite runtime behavior.
- Validation: Focused adapter, SQLite retrieval/data-port, and migration-runner
  tests: 72 passed; retrieval suite: 164 passed, 6 skipped; non-live PostgreSQL
  suite: 82 passed, 18 skipped; MCP regression tests: 44 passed; full suite:
  491 passed, 38 skipped. Compilation and both diff checks passed.
- Limitations: No database or Neon operation was performed. Live PostgreSQL
  query, index use, and retrieval parity remain unverified.

## 2026-10-16T23:11:03+05:30 — Add explicit MCP runtime composition

- Changed: Added `build_runtime`, `ServerRuntime`, bound `MCPToolHandlers`, and `create_mcp_server`; documented the SQLite-only composition boundary. Updated runtime, authorization, persistence, and command-alias tests for instance-scoped dependencies.
- Why: Make service wiring testable without module-global backends while keeping local SQLite, process-level authorization, stdio, and public MCP contracts intact.
- Validation: Focused MCP runtime/protocol/persistence/authorization tests: 44 passed. Full suite: 473 passed, 38 skipped. Changed Python modules passed `py_compile`; `git diff --check` passed.
- Limitations: Hosted PostgreSQL/Neon is not selectable in the MCP runtime; no database or Neon operation was performed.

## 2026-10-16T21:39:29+05:30 — Add optional Capability 1 knowledge state to Retrieval

- Changed: Added a version 1 optional `knowledge_state` envelope, an explicit fakeable Retrieval reader seam, lossless mapping of existing knowledge/evidence/capture provenance, project checks, and context JSONL/budget integration. Added focused tests and documented the missing production read interface. Ranking, filtering, storage contracts, and MCP handlers were not changed.
- Why: Make Capability 1 verification, evidence, and provenance available alongside existing Retrieval context without treating decision approval as verification or fabricating links.
- Validation: Focused state/context/service suite: 38 passed in 0.41s. Final related suites: Retrieval 163 passed, 6 skipped (1.98s); Evidence 32 passed (0.08s); Capture 35 passed (0.14s); project-state 20 passed (0.84s); Capability-flow integration 7 passed (0.30s). `git diff --check` passed. Repository-wide collection was blocked with 15 skipped and 6 errors: five MCP test modules could not import the missing `mcp` package, and `tests/sync/test_service.py` conflicted with the same-named Evidence test under default import mode. The attempted combined importlib-mode run also could not import `tests/retrieval/test_fts.py`'s `fixtures` helper.
- Limitations: No production Capability 1 reader or adapter exists; production persistence remains blocked by the missing project-scoped candidate-to-knowledge read interface. The MCP adapter does not configure the optional seam.

## 2026-10-16T20:16:00+05:30 — Require an approved target for schema validation

- Changed: Removed the fixed `celestin_schema_test_v2` target from the hosted-schema validator. It now requires an operator-supplied target to match the connected database, the disposable test naming pattern, the loopback endpoint, and the retired-target rejection list. Documented that no replacement database is approved in the repository. Reworked the reconstructor symlink fixture to create an explicit Git mode-120000 blob, preserving the security assertion without requiring Windows symlink privilege.
- Why: The prior example pointed to a retired database and could encourage reuse; an operator-selected database name must not be mistaken for approval or freshness.
- Validation: PostgreSQL support tests: 51 passed. The reconstructor tests passed 17/17 after the fixture was changed to write and assert a mode-120000 Git tree entry, avoiding the Windows `WinError 1314` OS-symlink restriction without weakening the blob assertion. Changed Python files passed `py_compile`; `git diff --check` and `git diff --cached --check` passed. No SQL script, live test, database connection, migration, or Neon request was run.
- Limitations: SQL guard execution against PostgreSQL remains unverified. A database owner must separately approve and provision a new disposable target before the validator can be used.

## 2026-10-16T19:24:02+05:30 — Restore PostgreSQL test safeguards

- Changed: Restored explicit tracked-cursor cleanup and regression tests; corrected the live fixture's workspace-scoped legacy project keys; restored retired-target checks and added the 2026-10-15 one-run database; pinned the project Neon MCP URL and documented retired targets.
- Why: The staged integration snapshot had removed protections needed for deterministic fixture setup, resource cleanup, and preventing reuse of PostgreSQL test databases.
- Validation: `python -m pytest -p no:cacheprovider -o "addopts=" tests/postgres/test_live_support.py` — 50 passed using global Python 3.13.9 because this checkout has no project validation venv. `git diff --check` and `git diff --cached --check` passed. No PostgreSQL connection, live test, migration, or Neon request was made.
- Limitations: Live PostgreSQL behavior was not tested. Changes are intentionally left unstaged to preserve the pre-existing index snapshot.

## 2026-10-16T14:31:22+05:30 — Reconcile team build context and current project documentation

- Changed: Merged the team branch and reconciled the PRD, TRD, application flow, design principles, README, and database model with the supplied team context and current implementation details. Added BUILD_KICKOFF.md with a clear note that its audit observations are historical. Preserved the upstream source and test changes.
- Why: Integrate the team's Resurrection Layer context without replacing local MCP setup guidance or presenting planned integration as verified behavior.
- Validation: Pending local documentation and regression checks.
- Limitations: Team handoff audit observations require verification against the merged code; this documentation reconciliation does not change application behavior.

## 2026-10-16T13:58:26+05:30 — Add project-scoped Neon agent tooling

- Changed: Added the Neon, Neon Postgres, and Neon Object Storage agent skills under `.agents/skills/`; configured the project Codex Neon MCP server using OAuth in `.codex/config.toml`; added the skill source lock file and README setup notes.
- Why: Make official Neon guidance and project-scoped Neon management tools available to contributors without storing an API key in the repository.
- Validation: Confirmed the skills CLI lists all three Neon skills at project scope and verified the generated Codex MCP entry contains only the Neon MCP URL. `git diff --check` was run after documentation updates.
- Limitations: The Neon CLI `skills` wrapper could not run with installed Node.js 22.18.0 (it requires 22.20+) and then reported that its external `skills` executable was unavailable. Used Neon's documented `npx skills add` fallback. Neon MCP OAuth sign-in must be completed by each Codex user on first use. No Neon project was linked or deployed; Celestin remains SQLite-backed.

## 2026-10-16T09:05:44+05:30 — Add Celestin product and build documents

- Changed: Added `PRD.md`, `TRD.md`, `App_flow.md`, `Design.md`, and `database_schema.md`; added a documentation index to `README.md` and recorded this contribution in `AI_USAGE.md`.
- Why: Give the team a consistent, implementation-grounded product, technical, interaction-flow, design, and schema handoff.
- Validation: Reviewed the docs against the checked-out README, MCP server, SQLite engine/repositories, and hosted schema draft; local Markdown links and `git diff --check` passed. No application tests were run for this documentation-only change.
- Limitations: The Orbit memory lookup for project `celestin` returned `ACCESS_DENIED`; no alternate project was queried. The docs describe the local MCP implementation and label the hosted PostgreSQL schema as draft/planning only. No runtime code or database was changed.

## 2026-10-15T23:34:46+05:30 — Preserve linked conflicts and stale status in reconstruction

- Changed: Project-state reconstruction now classifies saved knowledge marked stale as stale and uses capture-provenance conflict links to mark both available candidate endpoints as unresolved conflicts without mutating stored records. Added regression tests and clarified the behavior and retrieval integration gap in `docs/project-state-reconstruction.md`.
- Why: Reconstruction previously ignored Capability 3 conflict links and could downgrade a saved stale knowledge status to unverified when its source path existed.
- Validation: Baseline affected suite: 353 passed, 5 errors, 1 warning. The five errors are the known `DecisionRepository` `NameError` in `tests/retrieval/test_strategies.py`. Post-change project-state tests: 20 passed. Affected capture/evidence/project-state/sync/integration/retrieval (excluding that known failing module)/MCP-handler suite: 355 passed, 1 warning. `git diff --check` passed.
- Limitations: Legacy retrieval does not consume Capability 1 knowledge and its DTO has no verification, freshness, or conflict metadata. The MCP stdio timeout was not part of this focused baseline.

## 2026-10-15T21:27:22+05:30 — Document retrieval content trust boundary

- Changed: Added adversarial-content and status-semantics tests for context JSON serialization and the MCP retrieval handler. Documented that retrieved content is untrusted project data, decision approval is not verification, Capability 1 stale/conflict metadata is not present in the current retrieval contract, and the MCP item array inherits project scope from its response envelope. No retrieval ranking, MCP handler, model, or reranking implementation changed.
- Why: Retrieved messages and excerpts are returned to clients as source content, but the repository has no downstream coding-agent prompt assembler to enforce trust semantics.
- Validation: Baseline focused retrieval/MCP/PageIndex suite: 92 passed, 1 warning. Final focused suite: 98 passed, 1 warning. Broader capture/evidence/project-state/sync/integration/retrieval/MCP-handler/PageIndex suite: 215 passed, 1 warning. The isolated stdio protocol test exceeded a 20-second timeout; cause not established. `git diff --check` passed.
- Limitations: Tests verify serialization and status separation, not model compliance. A consuming-client injection test remains necessary. PageIndex sends document outline snippets in its navigation prompt; replies are restricted to known section references, but prompt injection can still affect which valid same-project section is selected.

## 2026-10-15T20:34:33+05:30 — Reauthorize Resurrection Layer service operations

- Changed: Evidence and synchronization services now treat `AuthorizedProject` as a project/action selector and re-check every operation through an injected `ProjectAuthorizer` using a principal fixed at trusted service construction. Added regressions for caller-constructed scopes, cross-project access, and stable service-principal use. Strengthened the MCP tool registration test to assert that Capabilities 1–4 remain internal-only. Documented the remaining repository-root binding limitation.
- Why: `AuthorizedProject` is a publicly constructible dataclass and its presence alone cannot establish trusted authorization. Project-state reconstruction also accepts an explicit root with no project-to-repository resolver.
- Validation: Pre-change selected baseline passed with `--import-mode=importlib` (the repository's default pytest mode failed collection because `tests/evidence/test_service.py` and `tests/sync/test_service.py` share the same module basename). Final targeted suite: 106 passed, 6 subtests passed. Broader capture/evidence/project-state/sync/integration/auth/storage suite: 195 passed, 15 subtests passed. `git diff --check` passed.
- Limitations: This adds domain reauthorization only when a trusted caller configures the authorizer and pins the principal. It does not authenticate transports. Resurrection capabilities remain unexposed through MCP. Reconstruction needs an application-owned authorized project-to-repository resolver before external exposure; current path and symlink checks are not project authorization.

## 2026-10-15T19:37:01+05:30 — Keep reviewed capture candidates immutable

- Changed: Terminal approved/rejected candidates now remain unchanged on exact retries; additional source/evidence or changed claim surface/certainty under the same normalized identity is reported as suppressed. Different claims or conflict keys remain new pending identities. New conflict candidates can link to terminal claims without backfilling the terminal record's conflict history. Added focused regression tests and clarified the capture contract.
- Why: Recapture previously merged provenance into an approved or rejected candidate without review, and conflict discovery could update terminal conflict links.
- Validation: Baseline capture suite: 29 passed. Focused post-change capture suite: 35 passed. Capture/evidence/project-state/sync/integration suite: 113 passed. `git diff --check` passed. No full suite or persistence integration was run.
- Limitations: The review queue remains in-memory; suppressed new information is not retained as candidate knowledge. Conflict links from new candidates to terminal records are intentionally one-way to preserve the terminal record.

## 2026-10-15T17:24:52+05:30 — Prepare safe PostgreSQL live-test preflight

- Changed: Added read-only SQL checks for database identity, prohibited role attributes and memberships, migration-ledger absence, and unexpected objects to `docs/postgres-integration-tests.md`; clarified the exact acceptance conditions before an opted-in live run.
- Why: Operators need a reproducible preflight for a newly owner-approved disposable database and must not reuse the previously failed target.
- Validation: Focused non-live PostgreSQL tests: 76 passed; complete `tests/postgres` subset: 76 passed, 18 skipped; full suite: 357 passed, 38 skipped; `git diff --check` passed. No database connection, SQL execution, migration, or live test was performed.
- Limitations: The catalog queries are documented for future operator use and were not executed. PostgreSQL catalog behavior and migrations remain unverified against a live server.

## 2026-10-15T17:18:51+05:30 — Reject elevated PostgreSQL live-test roles

- Changed: The live fixture now rejects `SUPERUSER`, `CREATEROLE`, `CREATEDB`, `REPLICATION`, and `BYPASSRLS` role attributes and direct/transitive memberships other than PostgreSQL's implicit `pg_database_owner` membership. Added non-live identity tests and documented the role policy.
- Why: A target-matched, non-superuser role can still carry excessive cluster or inherited privileges.
- Validation: Focused non-live PostgreSQL tests: 76 passed. `git diff --check` passed. No database connection, live test, SQL, or migration was run.
- Limitations: Fake identity rows test fail-closed policy handling but do not verify catalog query behavior on PostgreSQL. A new disposable target and least-privilege role still require owner approval before live testing.

## 2026-10-15T16:41:01+05:30 — Reject the previously failed PostgreSQL test target

- Changed: Removed the one-off allowlist for `celestin_it_20261014_a7f3c9`, added a regression case proving it is rejected, and documented that a newly provisioned nonce-named database and matching least-privilege role are required.
- Why: The earlier migration attempt may have left an empty ledger, so allowing that target to pass name validation could invite an unsafe retry.
- Validation: Non-live PostgreSQL tests and `git diff --check` run for this review; no live tests, database connections, SQL, or migrations were run.
- Limitations: Freshness and PostgreSQL catalog-query behavior must still be checked during a separately authorized live run.

## 2026-10-15T13:45:09+05:30 — Accept PostgreSQL loopback inet notation in live-test identity checks

- Changed: Normalize the server address returned by PostgreSQL before comparing it with the configured loopback target; added coverage for IPv4/IPv6 loopback forms and malformed or non-loopback values in `tests/postgres/live_support.py`, `tests/postgres/conftest.py`, and `tests/postgres/test_live_support.py`.
- Why: PostgreSQL may return `127.0.0.1/32`, while the fixture previously compared that string directly with `127.0.0.1` and rejected the verified local target.
- Validation: Focused non-live tests: 34 passed. `git diff --check` passed. No database connection, SQL, migration, or live integration test was run.
- Limitations: The database's freshness and exclusive reservation remain unverified and must be confirmed by its owner before migration.

## 2026-10-15T12:04:01+05:30 — Fix PostgreSQL migration fresh-database guard

- Changed: Kept the initial object check inclusive of ledger-named objects; the post-ledger check now excludes only the migration ledger table and its catalog-linked array type. Empty pre-existing ledgers fail closed. Added scripted regression coverage and operator guidance.
- Why: PostgreSQL creates a composite row type and associated array type for the ledger table; the previous second guard counted the generated array type as an unrelated public type and stopped before migration 1.
- Validation: Focused runner tests: 13 passed. Complete non-live PostgreSQL subset: 54 passed. Full suite: 335 passed, 38 skipped. `git diff --check` passed. No live database connection or SQL was run for this fix.
- Limitations: Scripted tests do not verify the catalog query against PostgreSQL. The failed target was not inspected and may contain an empty ledger; a database owner must provision a new disposable database before another initialization attempt.

## 2026-10-15T11:08:45+05:30 — Return consistent same-batch capture results

- Changed: Refresh accepted capture candidates from the review repository after processing the complete input batch, so returned source merges and conflict links match the saved queue state. Added a unit regression and updated the cross-capability assertion and capture contract documentation.
- Why: A later candidate in one structured input could add reciprocal conflict links to an earlier candidate, while the earlier object in `CaptureResult` remained stale.
- Validation: Before implementation, the Resurrection Layer baseline passed 106 tests. The new regression failed against the old behavior. After the fix, the capability suite passed 107 tests; the capability plus authorization/storage regression suite passed 160 tests and 12 subtests. Broad suite excluding known broken retrieval setup and stdio tests: 544 passed, 2 skipped, 1 deselected, 15 subtests passed. `git diff --check` passed.
- Limitations: Five tests in `tests/retrieval/test_strategies.py` still fail during setup with `NameError: DecisionRepository`; the PageIndex-enabled stdio test timed out after 35 seconds without output. The broad run excluded that retrieval module and stdio tests. No shared-memory or production persistence behavior was validated.

## 2026-10-15T11:05:36+05:30 — Reject unsafe PostgreSQL test DSNs

- Changed: Replaced driver-normalized DSN parsing with strict validation for exactly four password-free target parameters; added tests for credentials, extra and duplicate parameters, malformed syntax, and target mismatches; documented the accepted form and Windows password-file handling.
- Why: Ensure unsafe or ambiguous connection strings fail before the fixture can connect or invoke migrations.
- Validation: Focused target-safety tests and `git diff --check` passed. No live tests, PostgreSQL connections, SQL, or migrations were run.
- Limitations: Live PostgreSQL behavior remains unverified; the database owner must confirm the target is fresh and exclusively reserved before an authorized live run.

## 2026-10-15T10:36:52+05:30 — Tighten live PostgreSQL test-target exception and migration sequence

- Changed: Allowed only the exact user-approved `celestin_it_20261014_a7f3c9` database/role at `127.0.0.1:5432` in addition to the existing nonce-named target policy. The live fixture now verifies runtime identity and emptiness before one migration-runner invocation, then checks ledger/checksums. Added unit coverage and documented the one-run lifecycle.
- Why: Enable the specifically approved target without admitting arbitrary database names or migrating before fixture checks.
- Validation: Focused PostgreSQL tests: 43 passed, 18 live tests skipped. Full suite: 324 passed, 38 skipped. `git diff --check` passed. No PostgreSQL connection or migrations were run.
- Limitations: Real PostgreSQL integration remains unverified. After the approved target receives migrations it is no longer eligible as a fresh target; future live runs need a new nonce-named database and matching role.

## 2026-10-15T09:51:12+05:30 — Review evidence-linked memory storage semantics

- Changed: Added a proposed contract review in `docs/evidence-linked-memory-contract.md` covering candidate identity, retry behavior, provenance versioning, typed evidence, conflict history, transaction atomicity, and the authorization boundary.
- Why: Give the team a concrete storage contract to ratify before the persistence owner designs an additive PostgreSQL migration.
- Validation: Read-only comparison against the current hosted baseline, `MemoryStore`, and `PostgresMemoryStore`; `git diff --check` passed. No tests or database operations were run.
- Limitations: The referenced Resurrection Layer contract was not present in the local worktree or Git history, so field-level reconciliation remains pending. All semantics in the new document are proposals, not approved schema decisions.

## 2026-10-15T09:03:58+05:30 — Preserve capture provenance through knowledge reconstruction

- Changed: Added optional `CaptureKnowledgeProvenance` to `KnowledgeRecord`, a capture conversion helper and explicit category mapping; validated project, approved/unverified state, and source-to-evidence association. Capability 2 now returns capture provenance, knowledge/evidence record IDs, evidence resolution/verification/checker fields, and lifecycle/supersession fields. Updated contract documentation and integration tests.
- Why: Approved capture metadata was discarded at the Capability 3→1 boundary and reconstructed findings lost generated IDs, checker assertions, evidence statuses, and explicit supersession links.
- Validation: Baseline affected suite before edits — 123 passed, 12 subtests passed. Focused capture/evidence/reconstruction/integration suite after edits — 83 passed. Affected suite with authorization and storage regressions — 136 passed, 12 subtests passed. `git diff --check` passed. No full suite or production database/MCP/hosted validation was run.
- Limitations: The optional provenance value changes the storage-neutral record contract only. Shared-memory adapters/migrations must agree on a round-trip representation, candidate-ID idempotency, and lookup of conflict links. Checker/reviewer labels remain caller-supplied metadata, not authenticated identity.

## 2026-10-15T08:50:27+05:30 — Enforce evidence project scope on read

- Changed: Capability 1 now rejects evidence rows from storage when their project differs from the authorized read scope. Capability 2 omits out-of-project evidence from supplied knowledge bundles, leaves the claim unknown if no in-scope evidence remains, and reports the omission. Added regression tests and clarified both contracts.
- Why: A faulty storage adapter or read-only knowledge source could return evidence belonging to another project alongside an in-scope record; before this change, the foreign source identifier could be returned to the caller.
- Validation: Baseline focused provenance suite — 121 passed, 12 subtests passed. Focused changed-service tests — 44 passed. Post-change capture/evidence/project-state/integration/auth/storage suite — 123 passed, 12 subtests passed. `git diff --check` passed. Full suite and live persistence/hosted authorization were not run.
- Limitations: This blocks foreign evidence leakage at these read boundaries; it does not authenticate the caller or prove that evidence supports a claim. Cross-capability storage fields for capture certainty, review metadata, conflict links, and source-occurrence timestamps remain unagreed.

## 2026-10-15T02:00:40+05:30 — Require project match for capture evidence conversion

- Changed: `evidence_sources_for` now requires an explicit target project and rejects mismatched candidate scope before mapping provenance. Updated Capability 3 contract documentation, unit tests, and the Capability 1↔3 test bridge to exercise the domain guard.
- Why: The prior conversion accepted any approved candidate without knowing which project would receive the resulting evidence, leaving cross-project association prevention only in an ad hoc test bridge.
- Validation: Baseline affected suite — 115 passed, 12 subtests passed. Post-change focused capture and integration tests — 26 passed. Post-change affected capability, integration, and authorization suite — 116 passed, 12 subtests passed. No full suite or production persistence/authorization integration was run.
- Limitations: The project match is not authentication. Callers still must obtain trusted project scope from the authorization boundary; persistence integration remains unimplemented.

## 2026-10-15T01:43:35+05:30 — Add cross-capability integration test harness

- Changed: Added test-only `tests/integration/test_capability_flow.py` composing Capabilities 1–4 through local bridges and fake storage/read adapters; updated `AI_USAGE.md`. No production interface, persistence, authorization, retrieval, System 1, PostgreSQL, or MCP implementation changed.
- Why: Validate reviewed capture → evidence-linked knowledge → reconstructed state and event delivery, while making missing shared adapters and metadata loss visible without claiming production integration.
- Validation: `PYTHONPATH=src .venv/bin/python -m pytest -o addopts='' --import-mode=importlib -q tests/integration/test_capability_flow.py tests/capture/test_pipeline.py tests/evidence/test_service.py tests/project_state/test_reconstructor.py tests/sync/test_service.py tests/test_mcp_authorization.py tests/test_hosted_scope.py tests/database/test_decisions.py tests/database/test_messages.py tests/storage/test_postgres_memory_store.py` — 143 passed, 12 subtests passed. `tests/retrieval/test_strategies.py` produced 5 setup errors from the existing `NameError: DecisionRepository` at line 36. The isolated PageIndex-enabled stdio test exceeded a 30-second timeout (exit 124). No full suite or live database/hosted test was run. `git diff --check` and whitespace checks passed.
- Limitations: Test adapters are not production adapters. Typed capture uncertainty and conflict links do not cross all current interfaces; source occurrence timestamps are dropped by the Cap3→Cap1 evidence conversion. Same-batch `CaptureResult` can contain a stale conflict-link object even though the review queue has the final links.

## 2026-10-15T01:14:22+05:30 — Add bidirectional synchronization contract prototype

- Changed: Added a versioned `orbit.sync` event model, authorized-scope service, idempotent in-memory adapter, project-bound sequence cursors and pagination, focused tests, and `docs/bidirectional-knowledge-synchronization.md`. Updated `AI_USAGE.md`.
- Why: Define and validate both logical synchronization directions without introducing a competing persistence schema, hosted endpoint, MCP tool, or remote authorization path.
- Validation: `PYTHONPATH=src .venv/bin/python -m pytest -o addopts='' --import-mode=importlib -q tests/sync/test_service.py tests/evidence/test_service.py tests/project_state/test_reconstructor.py tests/capture/test_pipeline.py tests/test_mcp_authorization.py tests/test_hosted_scope.py tests/database/test_decisions.py tests/database/test_messages.py tests/storage/test_postgres_memory_store.py` — 136 passed, 12 subtests passed. `PYTHONPATH=src .venv/bin/python -m compileall -q src/orbit/sync` and `git diff --check` passed. The default import-mode attempt hit duplicate `test_service` module names during collection; rerunning with `--import-mode=importlib` passed. Full suite and live database/hosted integration were not run.
- Limitations: The adapter is process-local, non-durable, and not thread-safe. The service requires an `AuthorizedProject` provided by a trusted caller; this prototype cannot prevent a same-process caller from constructing/reusing that dataclass. Persistence, hosted authorization, and network integration require owner contracts.

## 2026-10-15T00:44:16+05:30 — Add isolated automatic knowledge capture prototype

- Changed: Added `orbit.capture` explicit-input extraction, validation, secret-like content suppression, normalized candidate deduplication, explicit conflict links, an ephemeral project-scoped review queue, tests, and `docs/automatic-knowledge-capture.md`. Updated `AI_USAGE.md`.
- Why: Reduce manual knowledge-saving effort while requiring human review and keeping approval separate from verification and shared-memory persistence.
- Validation: `PYTHONPATH=src .venv/bin/python -m pytest -o addopts='' -q tests/capture/test_pipeline.py tests/evidence/test_service.py tests/project_state/test_reconstructor.py tests/database/test_decisions.py tests/database/test_messages.py tests/test_hosted_scope.py` — 100 passed, 6 subtests passed. `PYTHONPATH=src .venv/bin/python -m compileall -q src/orbit/capture` and `git diff --check` passed. No full suite or live database check was run.
- Limitations: Queue state is in-memory only. Extraction requires labeled lines or structured events, conflict detection requires an explicit shared key, and secret detection is heuristic. Hosted authorization, durable storage, and lossless category mapping need owner coordination.

## 2026-10-15T00:20:56+05:30 — Validate Capability 1 and 2 domain integration seam

- Changed: Added read-only `KnowledgeBundle` integration tests for absent records, saved verification versus independently checked evidence, historical source revisions, unresolved message references, and repository/database-artifact non-mutation. Updated this validation record and `AI_USAGE.md`.
- Why: Check reconstruction against Capability 1's declared bundle model without depending on fake-store internals or introducing a persistence adapter.
- Validation: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/evidence/test_service.py tests/project_state/test_reconstructor.py` — 42 passed. The PageIndex-enabled stdio test timed out while awaiting MCP initialization; it also timed out on the parent baseline, and a probe hung at `initialize()` with PageIndex both enabled and disabled. The existing retrieval NameError was reproduced on a temporary archive of the current baseline in unchanged `tests/retrieval/test_strategies.py`. Broad run excluding the stdio test stopped at that NameError. `git diff --check` passed.
- Limitations: Capability 1 has no project-scoped record-list operation, so reconstruction's injected protocol is not wired to its store/service. The stdio handshake cause remains undetermined; no full-suite pass is claimed.

## 2026-10-15T00:01:28+05:30 — Add read-only project-state reconstruction prototype

- Changed: Added a pinned-revision Git inspector and structured findings for tracked source artifacts, open-work markers, unresolved sections, optional saved decisions and other Capability 1 knowledge, and evidence-backed next-step suggestions; added temporary-repository tests and `docs/project-state-reconstruction.md`.
- Why: Let a returning agent inspect current project evidence without writing memory, invoking MCP, or treating saved claims and file presence as proof of correctness.
- Validation: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/project_state/test_reconstructor.py tests/evidence/test_service.py` — 37 passed, including a Git environment-redirection boundary test. Full-suite run timed out in an existing PageIndex-enabled stdio test; excluding that test exposed a pre-existing `NameError` (`DecisionRepository`) in `tests/retrieval/test_strategies.py`. `git diff --check` passed.
- Limitations: Lexical markers can be noisy or incomplete; decisions remain applicability-unknown; no test-result resolver, persistent Capability 1 reader, automatic conflict detector, MCP exposure, or database integration.

## 2026-10-14T23:48:15+05:30 — Add evidence-linked memory domain and service contract

- Changed: Added storage-neutral knowledge/evidence records and an authorized-scope service for explicit provenance, reference resolution, verification metadata, idempotent evidence association, and non-destructive supersession; added deterministic service tests and `docs/evidence-linked-memory-contract.md`.
- Why: Establish the first Resurrection Layer capability without creating an uncoordinated SQLite/PostgreSQL schema or changing existing persistence, authorization, retrieval, or MCP contracts.
- Validation: `PYTHONPATH=src python3 -m pytest -p no:cacheprovider -o addopts='' tests/evidence/test_service.py tests/database/test_decisions.py tests/database/test_messages.py tests/storage/test_postgres_memory_store.py tests/test_hosted_scope.py` — 71 passed. MCP regression collection was attempted but could not run because the system Python environment lacks the `mcp` package; no validation virtual environment is present. `git diff --check` and a trailing-whitespace scan of all changed files passed.
- Limitations: No SQLite/PostgreSQL adapter, migration, source resolver implementation, MCP tool, or production wiring. The storage port is a prototype contract requiring persistence-team agreement; tests use a test-only fake store.

## 2026-10-14T22:28:00+05:30 — Prepare opt-in live PostgreSQL authorization tests

- Changed: Added a dedicated live PostgreSQL test target guard and fixture, synthetic hosted authorization/transaction/constraint/concurrency tests, target-parser unit tests, and operator guidance in the README and PostgreSQL docs.
- Why: Provide a safe way to validate migrations and transaction/locking behavior against an explicitly provisioned disposable PostgreSQL database without making ordinary tests depend on a server.
- Validation: Focused non-live PostgreSQL tests: 38 passed, 18 live tests skipped. Full suite: 319 passed, 38 skipped. `git diff --check` and the untracked-file whitespace scan passed. No PostgreSQL connection, migration, database provisioning, dependency install, staging, commit, or push was performed.
- Limitations: Live tests remain unrun; the fixture deliberately requires a new empty nonce-named local database and a matching non-superuser role. The active-owner invariant remains an application-policy gap.

## 2026-10-14T21:47:50+05:30 — Finalize hosted-foundation commit hygiene

- Changed: Clarified that the authorization session protocol is implemented by hosted storage sessions and removed extra trailing blank lines from five newly added PostgreSQL files.
- Why: Keep the authorization contract description current and make the complete committed diff pass whitespace checks cleanly.
- Validation: Full suite: 309 passed, 20 skipped using `.venv/validation`. Working-tree `git diff --check` passed; removed five extra EOF blank lines found in the prior committed-range check. No database or migration commands.
- Limitations: No runtime behavior changed; live PostgreSQL validation remains outstanding.

## 2026-10-14T21:46:50+05:30 — Commit completed storage and hosted-foundation milestones

- Changed: Recorded the existing completed work as separate commits for Loop Engineering guidance, SQLite persistence and retrieval ports, hosted project scope, PostgreSQL memory storage, schema validation, explicit migrations, and PostgreSQL authorization sessions.
- Why: Preserve reviewable project history and make the substantial completed changes available as coherent local Git checkpoints.
- Validation: Full isolated Python suite: 308 passed, 20 skipped. `git diff --check` and `git diff --cached --check` passed. No PostgreSQL connection or migration was run.
- Limitations: Commits are local and not pushed. PostgreSQL adapters, migration SQL, and hosted authorization have not been validated against a live PostgreSQL database; hosted MCP authentication/backend selection remain incomplete.

## 2026-10-14T21:44:49+05:30 — Add PostgreSQL hosted authorization session foundation

- Changed: Added additive migration `0002_user_status.sql` with a fail-closed suspended default; added a lazy, injectable `PostgresHostedAuthorizationSessionFactory` and scoped authorization reads; connected the existing resolver to the session-bound identity check; documented identity mapping and transaction lock ordering.
- Why: Establish a database-backed authorization boundary for a future hosted caller without changing local SQLite, public tools, server selection, or migration startup behavior.
- Validation: Focused resolver/PostgreSQL checks: 45 passed. Full Python suite: 309 passed, 20 skipped. Task-owned `git diff --check` and whitespace check passed. No PostgreSQL connection or migration was run.
- Limitations: PostgreSQL SQL and lock behavior need validation on a disposable server. No authenticator, hosted MCP wiring, or hosted data-store integration is included.

## 2026-10-14T21:35:20+05:30 — Enforce hosted source-message scope

- Changed: Added a typed source-message ownership reference and required matching it against the resolved workspace/project before a hosted write callback runs. Documented the hosted storage/retrieval scope seam and added synthetic denial tests.
- Why: Prevent decision provenance from crossing the authorized project boundary while keeping local stdio and public MCP contracts unchanged.
- Validation: Focused hosted-scope, local authorization, and persistence tests: 33 passed. An earlier full run passed 292 tests with 20 skipped; the later full run reported 303 passed, 1 failed, 20 skipped. `git diff --check` and `git diff --cached --check` passed.
- Limitations: The later suite failure is `tests/postgres/test_hosted_authorization.py::test_query_errors_are_redacted_and_transaction_rolls_back` (`connection.rolled_back` was false); that pre-existing untracked PostgreSQL work was preserved and not changed. No trusted authenticator is connected to MCP, no hosted backend is selected, and no live PostgreSQL/FK or concurrent revocation test was run.

## 2026-10-14T21:25:50+05:30 — Add hosted identity and project scope boundary

- Changed: Added immutable hosted principal/project-scope types, a fail-closed scope resolver and authorize-before-operation helper, synthetic unit tests, and `docs/hosted-identity-scope.md` describing the trust boundary and next integration seam.
- Why: Establish an explicit application authorization contract before wiring hosted persistence or retrieval, without changing local stdio behavior or public MCP contracts.
- Validation: Existing authorization/persistence baseline: 15 passed. Focused hosted-scope, local-authorization, and SQLite-persistence tests: 29 passed. Full suite: 288 passed, 20 skipped. `git diff --check` on the task-owned tracked documentation and a trailing-whitespace check on task-owned new files passed.
- Limitations: No authenticator, PostgreSQL authorization session, transaction locking/revocation-race implementation, hosted backend selection, or PostgreSQL retrieval is included. The schema has no human-user active status field. No PostgreSQL connection was made.

## 2026-10-14T21:14:32+05:30 — Add an injectable PostgreSQL memory store

- Changed: Added `PostgresMemoryStore` implementing the existing `MemoryStore` contract, workspace-scoped lookup through `legacy_project_key`, transactional message insert/decision upsert/status update, and adapter-level record mapping. Added scripted connection tests and documented that this adapter is not selected by MCP and does not add hosted authorization or PostgreSQL retrieval.
- Why: Establish the first hosted persistence adapter behind the existing storage port without changing SQLite defaults, MCP tool contracts, or automatic migration behavior.
- Validation: Baseline focused suite before edits: 25 passed. Post-change focused suite: 31 passed. Full isolated suite: 274 passed, 20 skipped. All commands used `.venv/validation`; no live PostgreSQL connection or database was used.
- Limitations: Tests exercise adapter SQL calls and transaction-context behavior with a scripted connection, not a live PostgreSQL server. The optional psycopg extra is not installed here. MCP backend selection, authenticated hosted authorization, and PostgreSQL retrieval remain unimplemented.

## 2026-10-14T21:07:12+05:30 — Separate reusable and Celestin-specific loop guidance

- Changed: Extracted the multi-step engineering process into `docs/loop-engineering-core.md`; made `docs/loop-engineering.md` the local task-log template and entry guide; added `docs/celestin-loop-rules.md`; updated `AGENTS.md` to reference both layers and require explicit user approval for every push.
- Why: Keep general engineering workflow portable while recording storage, compatibility, and tenant-security constraints in the Celestin layer.
- Validation: Reviewed the documentation and instruction diffs; `git diff --check -- AGENTS.md CHANGELOG.md AI_USAGE.md` passed. No tests, application commands, database commands, commits, or pushes were run as requested.
- Limitations: Changes are uncommitted and await review; the separate general checkout has not yet received the shared core reference.

## 2026-10-14T20:50:27+05:30 — Add persistent loop-engineering guidance

- Changed: Appended iterative implementation, bounded-retry, verification, and safe-Git rules to `AGENTS.md`; added `docs/loop-engineering.md` with the workflow and iteration log template; ignored local `.loop-engineering/` task logs in `.gitignore`.
- Why: Make the workflow discoverable in future Codex chats using this checkout, while keeping per-task iteration notes local and preserving ongoing PostgreSQL/retrieval work.
- Validation: Reviewed the workflow-file diff and ran `git diff --check` on the workflow changes. No tests were run, as requested.
- Limitations: No verification runner was added; the guide uses the existing project Python/pytest commands. The in-progress application changes remain untouched and untested by this setup task.

## 2026-10-14T20:45:29+05:30 — Add explicit PostgreSQL migration foundation

- Changed: Added the packaged `0001_hosted_baseline.sql`, migration ledger and runner, `celestin-migrate` entry point, optional `postgres` dependency extra, migration unit tests, and operator documentation. Updated the README with the explicit setup path.
- Why: Establish versioned, serialized hosted-schema migrations without changing SQLite startup or connecting to a database.
- Validation: Focused migration tests passed (11); full isolated suite passed (268 passed, 20 skipped); editable install and `celestin-migrate --help` succeeded; packaged SQL matched the hosted schema draft after removing comments and its outer transaction wrapper; `git diff --check` passed.
- Limitations: No live PostgreSQL check, actual session-lock contention test, storage adapter, data import, hosted auth, or existing-schema adoption path was implemented.

## 2026-10-14T20:26:50+05:30 — Add optional granular commit watcher

- Changed: Added the `commit_granular` custom agent, a PowerShell watcher with quiet-period dispatch and safety pauses, disposable watcher and agent test harnesses, and setup instructions in `AGENTS.md` and `README.md`.
- Why: Let Codex make separate local commits for independent completed changes while protecting pre-existing work, unrelated files, and remote Git state.
- Validation: `pwsh -NoProfile -File scripts/test-commit-watcher.ps1` — passed, including clean-start refusal, debounce, CLI failure, branch-change and index-lock pauses, remote-operation checks, and graceful stop. PowerShell parser checks and `git diff --check` passed.
- Limitations: `scripts/test-commit-agent.ps1` was attempted in a disposable repository but could not verify actual commits because the Codex subagent helper failed during setup (`helper_unknown_error: setup refresh had errors`; local `pwc-local` MCP connection was unavailable). The test harness is retained for an environment where the helper can start. Console Ctrl+C was verified by handler registration and cleanup code, not by injecting a real terminal signal.

## 2026-10-14T20:12:13+05:30 — Add retrieval data port and SQLite adapter

- Changed: Added database-neutral retrieval records and a `RetrievalData` protocol; moved FTS5 searches, SQLite ID conversion, row mapping, and project-scoped authoritative reloads into `SQLiteRetrievalData`; updated FTS and semantic strategies to use the port.
- Why: Separate SQLite retrieval mechanics from shared ranking, fusion, decision policy, and context assembly without changing the MCP contracts or optional retrieval behavior.
- Validation: Focused retrieval/FTS/service/MCP command: 77 passed. Full suite: 257 passed, 20 skipped. `git diff --check` passed (Git emitted a line-ending conversion warning for a pre-existing modified persistence file).
- Limitations: No PostgreSQL adapter or PostgreSQL validation was added. PageIndex and vector-index stores remain independently SQLite-backed.

## 2026-10-14T20:04:02+05:30 — Add database-neutral persistence protocol

- Changed: Added stored message/decision records, the `MemoryStore` protocol, and `SQLiteMemoryStore`; rewired `PersistenceServices` to consume the protocol and map records to existing MCP DTOs. Added adapter, source-isolation, commit, status-update, protocol-injection, and best-effort-indexing tests.
- Why: Create a small persistence boundary for future adapters while preserving SQLite behavior and leaving retrieval internals unchanged.
- Validation: Focused database/persistence/authorization tests passed (42 tests); full isolated suite passed (251 passed, 20 skipped); `git diff --check` reviewed after implementation.
- Limitations: No PostgreSQL adapter, schema change, or hosted authorization was introduced. Retrieval remains SQLite-specific. Existing unrelated work in `AI_USAGE.md`, `CHANGELOG.md`, and `tests/postgres/` was preserved.

## 2026-10-14T19:55:17+05:30 — Benchmark local Laya retrieval path

- Changed: Added paired H/L evaluation with three measured runs after local warmup on dev, held-out, and challenge sets at 2,000/20,000 budgets. Saved machine-readable per-query metrics and rendered reports under `eval/laya_results/`; corrected context byte/token reporting. Added timeout-worker backpressure so expired inference cannot build a queue, and kept Julia-1 direct ordering unchanged while Laya alone uses RRF.
- Findings: Laya had zero inference failures, timeouts, or oversized-input skips. It did not show a consistent quality win and took 8.1–10.2 seconds/request on CPU versus 14–18 ms for H. The 614 MiB checkpoint added about 1.6 GiB RSS on load. Julia results are historical and use a different runtime.
- Validation: Focused retrieval/eval/MCP tests: 38 passed. Full suite: 413 passed, 2 skipped, 1 failure because globally installed `orbit`/`memory-bridge` launch a different Orbit server; the current checkout’s MCP source-path protocol checks passed. `git diff --check` passed.
- Limitations: Shared PostgreSQL memory, hosted identity, and membership authorization shown in the diagrams are not implemented or tested. Laya remains opt-in; no improvement is claimed.

## 2026-10-14T19:50:38+05:30 — Make schema validation assertions count affected rows

- Changed: Updated `tests/postgres/validate_hosted_schema.sql` so `expect_success` checks an explicit expected affected-row count (default one), and expanded post-rollback checks to cover all fixture user memberships, message IDs, and memory IDs.
- Why: Prevent zero-row updates from appearing as passes and make cleanup verification account for every fixture record used by success and rejection cases.
- Validation: Static review and `git diff --check`; no PostgreSQL connection, script execution, or schema change.
- Limitations: The SQL has not been executed; runtime behavior remains unverified.

## 2026-10-14T19:38:36+05:30 — Add disposable hosted-schema validation script

- Changed: Added `tests/postgres/validate_hosted_schema.sql` with guarded, synthetic fixtures, explicit SQLSTATE assertions, provenance and deletion checks, archive/purge checks, an ownerless-workspace schema probe, savepoint rollback verification, and an outer transaction rollback.
- Why: Make PostgreSQL constraint validation repeatable against the already-provisioned disposable schema without changing that schema or touching application code.
- Validation: Reviewed the script against `docs/hosted-tenancy-schema.sql` and `docs/hosted-tenancy-spike.md`; `git diff --check` passed. The script was not connected to or executed against PostgreSQL.
- Limitations: Runtime SQL behavior is unverified. Concurrent uniqueness and concurrent last-owner transitions require multiple sessions and are not tested by this single-session script; the schema permits an ownerless workspace and does not implement application authorization, purge authorization, or retention filtering.

## 2026-10-14T18:30:00+05:30 — Add opt-in local Laya retrieval reranking

- Changed: Added the local `convaiinnovations/laya-multilingual` batch reranker to `src/orbit/retrieval/service.py`; wired `CELESTIN_RETRIEVAL_PATH=system1` to Laya while keeping `baseline` as default and preserving Julia-1 under `julia1`. Added a pinned optional Laya dependency and focused tests for payloads, token limits, expected-relevance ordering, equal-weight RRF, exact-FTS protection, and safe fallback. Updated README setup and behavior notes.
- Why: Test System 1 as a context reranker after the existing configured selector and search engines find authorized candidates.
- Validation: Focused Laya/service tests passed; local smoke tests passed with the actual Laya checkpoint. Full suite passed with two unrelated environment-sensitive tests excluded; see handoff for details.
- Limitations: The existing frozen evaluation harness has not been adapted to score Laya. No quality-improvement claim is made. The checkpoint is downloaded lazily; cold-start inference can exceed the request timeout and safely retain baseline order.

## 2026-10-14T17:38:49+05:30 — Add provenance index and owner-concurrency validation requirements

- Changed: Added non-partial `project_memory(project_id, source_message_id)` to the hosted PostgreSQL schema draft; documented its provenance/FK-check purpose, the workspace-row lock/recheck protocol for owner changes, required concurrent-owner and hosted authorization tests, and the user-reported behavioral validation outcome. Clarified that the new index has not been applied to the already-used test database.
- Why: Cover the referencing-side FK index gap and make clear that the active-owner invariant needs serialized application transactions; accurately distinguish reported DB evidence from work independently verified in this update.
- Validation: Static SQL/documentation review and `git diff --check`; no database connection, schema execution, or test suite run.
- Limitations: User-reported validation of the previous draft was not independently rerun. The added index, owner concurrency, hosted application authorization, search/cache isolation, and purge authorization remain unverified or unimplemented.

## 2026-10-14T11:45:20+05:30 — Implement and evaluate local System 1 retrieval experiment

- Changed: Added a lazy, pinned local Julia-1 reranker behind `CELESTIN_RETRIEVAL_PATH=system1`; kept the existing path as the default and preserved public MCP contracts, authorization, SQLite, decision policy, provenance, and context assembly. Added paired H/J evaluation support, regression tests, and frozen-set result artifacts.
- Validation: Local CPU inference completed with no inference failures. Full suite: 404 passed, 2 skipped; `git diff --check` passed.
- Findings: H exceeded J on answer-bearing context and MRR in all six dev/held-out/challenge and budget comparisons. J averaged 691–753 ms per request versus 48–90 ms for H; the 550.5 MiB checkpoint increased RSS by about 996 MiB during load/warmup. Julia-1 did not improve this experiment and remains opt-in. No hosted inference or training was added.

## 2026-10-14T08:53:45+05:30 — Finalize hosted tenancy policy draft and validation plan

- Changed: Rewrote `docs/hosted-tenancy-spike.md` with the explicit-membership-and-grant matrix, ownership/archive/purge rules, enforcement boundaries, updated index review, synthetic PostgreSQL test cases and secure commands; corrected stale PostgreSQL environment notes. Designated `celestin` as the repository's canonical memory project ID in `README.md` per user instruction.
- Why: Turn the approved policy defaults into a reviewable schema-validation plan without implementing hosted runtime behavior.
- Validation: Documentation and SQL reviewed statically; `git diff --check` passed. No PostgreSQL schema command or test was run.
- Limitations: Disposable database credentials, purge policy, administrator permissions, service-identity scope, and live PostgreSQL constraint tests remain outstanding. Orbit memory retrieval for project `celestin` returned `ACCESS_DENIED`; no alternate project was queried.

## 2026-10-14T08:52:24+05:30 — Add archive-first retention to hosted schema draft

- Changed: Added archive and purge-eligibility timestamps to workspaces, projects, messages, and decisions; replaced cascading deletes with restrictive references; kept message provenance until an explicit purge or separately approved detachment; indexed workspace membership lookups and active-owner checks.
- Why: Prevent ordinary parent deletion from cascading into memory and preserve source links through archive periods.
- Validation: SQL reviewed statically; `git diff --check` passed. No SQL was executed against PostgreSQL.
- Limitations: Retention duration, purge authority, owner-transfer transaction, and purge audit process remain policy/application responsibilities and need team approval.

## 2026-10-14T08:51:25+05:30 — Scope hosted records and grants consistently

- Changed: Added workspace scope to hosted messages and decisions with composite project references; retained revoked grant rows; made service identity uniqueness workspace-scoped and recorded revocation timestamps; added indexes for grant foreign keys and removed the standalone project-memory project index.
- Why: Align hosted row relationships with tenant scope and the approved explicit-grant policy while preserving revocation history.
- Validation: SQL and constraints reviewed statically; `git diff --check` passed. PostgreSQL execution is still blocked on credentials for a disposable database.
- Limitations: Access decisions still require authenticated application authorization; this schema does not implement the hosted server. Lifecycle/delete behavior is addressed in the next draft revision.

## 2026-10-13T19:25:02+05:30 — Draft hosted tenancy schema spike

- Changed: Added `docs/hosted-tenancy-spike.md` and `docs/hosted-tenancy-schema.sql` describing a proposed workspace/project membership model and an empty-database PostgreSQL schema with project-scoped message/decision source references.
- Why: Establish a reviewable first step toward hosted shared memory while leaving the local SQLite schema, MCP contracts, and authorization behavior untouched.
- Validation: Reviewed schema relationships; full Python suite with `PYTHONPATH=src` — 245 passed, 1 failed, 15 skipped. The failure launches a stale globally installed `orbit.exe` instead of the current source package. `git diff --cached --check` passed.
- Limitations: PostgreSQL behavior was not tested because no PostgreSQL server is available and Docker Desktop's engine is stopped. The proposed membership defaults and legacy project mapping require team review; no application migration or hosted service is implemented.

## 2026-10-13T18:25:34+05:30 — Enforce local Celestin project access

- Changed: Added server-process project/action authorization before all four data tools; added the Celestin environment name with the Orbit variable as a fallback; updated MCP, stdio, persistence, and compatibility tests; documented access and compatibility; clarified the optional PageIndex query/outline data flow in the README.
- Why: Prevent a local MCP client from selecting an ungranted project through the `project` argument while retaining the current stdio architecture and legacy configuration.
- Validation: Clean Celestin editable install; focused MCP/authorization/compatibility checks — 36 passed; full suite — 246 passed, 20 skipped; `git diff --check` clean.
- Limitations: Stdio does not authenticate individual clients. All clients attached to the server process share its allowlist; this does not protect against untrusted same-user processes or authorize remote access.

## 2026-10-13T18:23:32+05:30 — Make database-path compatibility checks portable

- Changed: Normalize the displayed database path in `tests/test_celestin_rename.py` before comparing expected environment-variable values.
- Why: Windows renders path separators differently from the POSIX strings used by the compatibility cases.
- Validation: `python -m pytest -p no:cacheprovider -o addopts='' tests/test_celestin_rename.py` — 7 passed in the Celestin editable environment.
- Limitations: This changes test comparison only; database path resolution and behavior are unchanged.

## 2026-10-13T12:06:52+05:30 — Evaluate opt-in section reranking

- Changed: Audited the already-committed reranker at `a80e7b8`; added development, held-out, and new-query challenge evaluation artifacts; recorded full candidate ordering and chat query outcomes; added a pinned six-question documentation challenge set; documented controls and findings; corrected service-test queries so they retrieve multiple sections and the approved decision under test. No retrieval behavior, defaults, dependencies, frozen datasets, or MCP contracts changed.
- Why: Determine whether candidate reordering improves answer-bearing evidence per byte on both frozen question sets.
- Validation: Reproduced S and S+0.08 baselines at budget 20,000 with frozen dataset hashes unchanged; evaluated reranking variants on dev and held-out and then on six new documentation questions plus three chat questions. Margin+cross-encoder RRF+top-8 improved measured gold evidence per byte without answer loss in these runs, but top-8 was selected after prior held-out inspection. All 35 targeted reranker/evaluation tests passed. Broad tests passed with optional dependencies after excluding the live-LLM import and three stdio subprocess tests; the no-optional-dependency suite exited successfully with two protocol test files excluded. Full unfiltered collection failed in the installed optional `litellm` module; real-LLM retrieval was not run.
- Limitations: The new challenge has six document questions on the same corpus; it is too small to justify changing defaults. Plain cross-encoder scoring lost one held-out answer. All controls remain opt-in. Memory was not measured; the local cross-encoder artifact was 90.9 MB. No commit or push made.

## 2026-10-13T12:03:32+05:30 — Add an untuned reranking challenge set

- Changed: Added six documentation and three chat questions in `eval/rerank_challenge.py`, integrated the set and its pinned hash with the evaluation harness, recorded the full six-variant sweep and a separate four-variant lexical ablation in `eval/results_rerank_challenge*`, and made the chat-recall table labels reflect the set size. Updated the reranking analysis in `docs/context-optimization.md`.
- Why: Check the candidate ranking signal on questions added after the dev and held-out reranker comparisons.
- Validation: Reproduced S on dev and held-out (dataset hashes unchanged); ran all six variants on the challenge set. Focused reranker/evaluation tests passed (35). The broader suite passed with documented live-LLM and stdio subprocess exclusions; full optional-dependency collection errors in installed `litellm` during its background cost-map retry.
- Limitations: Challenge set is small and cannot replace the two original frozen evaluations. Each variant answered all six documentation questions; chat recall was 2/3 for every variant. Real-LLM evaluation was not run. No commit or push made.

## 2026-10-13T11:06:45+05:30 — Context-noise diagnosis and opt-in section relevance filtering

- Changed: Added `RetrievalService.last_diagnostics` (bounded, content-free stage trace) and `eval/diagnose.py`; section-only semantic margin (`CELESTIN_SEMANTIC_SECTION_MARGIN`, shared `semantic/margin.py`), optional section top-K cap (`CELESTIN_SECTION_TOP_K`) and query-focused excerpts (`CELESTIN_SECTION_EXCERPT_BYTES`) in `retrieval/relevance.py`; evaluation `--noise-sweep` and `--dataset heldout` with a frozen held-out set (`eval/heldout.py`) and answers-lost reporting. No defaults changed; MCP tools and schemas unchanged.
- Why: 90% of section items in the context were irrelevant because weak semantic neighbours pass the 0.6 cut-off and the budget is filled in rank order.
- Validation: see the test counts in the final report; sweeps run on the dev and held-out sets. Section margin 0.08 lost no answer on either set and cut 22-35% of bytes; top-K and excerpts lost answers and are not recommended.
- Limitations: 24 documentation questions on one corpus; the margin depends on the embedding model's similarity scale; no real-LLM evidence; duplicate suppression and budget-aware packing were not implemented because the diagnosis showed no duplicates and no usable relevance signal.

## 2026-10-13T10:31:53+05:30 — Heading-section retrieval, strategy interface and adaptive selector

- Changed: Heading-section retrieval is now a product path (`celestin-docs` store gains an external-content FTS5 index and section embeddings as a third vector kind; `celestin-index` and `celestin-docs` keep them in step). New `retrieval/strategies.py` (FTS5, semantic, sections, PageIndex behind one `Candidate`/`StrategyResult` interface) and `retrieval/selector.py` (deterministic primary + one fallback); `RetrievalService.retrieve_context` now runs strategies and fuses candidates (BM25 kept for FTS-only). `CELESTIN_RETRIEVAL_SELECTOR=adaptive` opts in (default `all`); `CELESTIN_DOC_SECTIONS=0` disables sections. The FTS tier logic is shared (`tiered_match`). Eval harness gains systems H, S, SN, Sd, median bytes, fallback and strategy counts.
- Why: Make the strongest measured baseline (heading sections) available in the product and test whether routing helps.
- Validation: see the test counts in the final report below; evaluation rerun on the frozen corpus (dataset hash unchanged). The adaptive selector matched H on answer-in-context (0.92) with one strategy fewer and did not reduce context size.
- Limitations: Selector rules were revised after failure analysis on the same 12 questions; no real-LLM evidence; an LLM selector was assessed and not built. A behavior change: users who already ran `celestin-docs index` now also get section results unless `CELESTIN_DOC_SECTIONS=0`.

## 2026-10-12T22:16:12+05:30 — Trustworthy evaluation, bounded FTS5 relaxation, PageIndex routing, cost instrumentation and maintenance

- Changed: `eval/run_eval.py` rewritten (frozen corpus in `eval/corpus/`, hashed dataset, per-query records, failure/timeout counts, observed vs estimated tokens, cost estimate, run metadata, real-LLM system N with credential checks, F selectivity sweep, `--fts-relax`); bounded FTS5 relaxation (stopword tier, then OR tier with a 60% threshold and required identifiers; quoted phrases; `CELESTIN_FTS_RELAX=0` disables); optional `CELESTIN_SEMANTIC_RELATIVE_MARGIN`; deterministic PageIndex routing policy with a cheap first stage; navigation stats, bounded retries and a project/index-version-keyed cache; bold-only-heading fix (tree schema v2); `celestin-docs prune|rebuild` and deleted-source reporting; WAL for the document store; Orbit skill text renamed to Celestin (skill id `orbit` kept). PDF support assessed and deferred.
- Why: Make retrieval measurements trustworthy and cut unnecessary navigation cost while keeping PageIndex optional and off by default.
- Validation: with all extras 275 passed, 2 skipped (opt-in real-model and live-LLM tests); without extras 196 passed, 14 skipped. Evaluation rerun on the frozen corpus: strict-FTS results reproduce the first evaluation at budget 20,000; relaxation lifted the existing pipeline (answer rate 0.00 to 0.67, chat recall 0.20 to 0.80).
- Limitations: Still no real-LLM run (no credentials), so PageIndex's real quality, latency and cost are unmeasured and the strongest measured practical baseline remains section-level semantic retrieval. Relaxation thresholds and routing rules were tuned on 12 questions. A real Codex session was not tested.

## 2026-10-12T21:38:30+05:30 — Optional PageIndex document retrieval

- Changed: Added `src/orbit/pageindex/` (Markdown section trees via PageIndex's parser, separate rebuildable store, LLM outline navigator, backend, `celestin-docs` CLI), a `document_section` record kind, and PageIndex routing/fusion in `RetrievalService.retrieve_context`. `retrieve_context` responses show `source` for sections and `retrieval.pageindex` when enabled; no tool contracts changed. New extra `pageindex`. Added `eval/` (dataset, runner, results) and `docs/pageindex.md`. Test isolation fix: tests set `orbit.database.engine.DB_PATH` because it is read at import time.
- Why: Evaluate hierarchical document retrieval for long documents that chat-message storage and whole-record budgeting handle poorly.
- Validation: with all extras, 216 passed and 2 skipped (opt-in real-model and live-LLM tests). Without optional extras, 150 passed and 9 skipped. The stdio server was exercised with PageIndex enabled but unconfigured (all five tools worked). `python eval/run_eval.py` ran (results in `eval/results.md`).
- Limitations: No LLM API key was available, so navigation quality is unmeasured; the evaluation uses a lexical stand-in and an oracle upper bound and shows no evidence yet that PageIndex beats simple section chunking with the existing semantic search. Markdown only; conversations are not indexed by PageIndex by design. `.agents/skills/orbit/SKILL.md` still uses the Orbit name (not changed here).

## 2026-10-11T22:17:11+05:30 — Push reconciled branch to the fork

- Changed: Pushed merge commit `441cb0a` to `origin/main` (`67ddd33..441cb0a`) and set local `main` to track the fork.
- Why: Publish the reconciled histories to the fork so the branch can continue from the synchronized base.
- Validation: Git reported a successful push and configured `origin/main` as the tracking branch.
- Limitations: No application code changed; no application tests were run for this Git and documentation update.

## 2026-10-11T21:57:40+05:30 — Reconcile fork history and integrate the memory skill

- Changed: Merged the three upstream commits into the four-commit Celestin branch, preserving both histories; integrated `.agents/skills/orbit/SKILL.md` and its `AGENTS.md`/README guidance, combined both development logs, and aligned the README's MCP inspection command with the configured `celestin` server name.
- Why: Bring the fork up to date while retaining the Celestin rename and semantic retrieval work already on the branch.
- Validation: Reviewed the merge tree and documentation consistency; `git diff --check` and local Markdown link checks run after resolution. No tests run for this Git and documentation change.
- Limitations: The request did not specify another application feature, so no application code was changed. The repository still has no canonical project identifier for the memory skill.

## 2026-10-11T20:20:22+05:30 — Rename to Celestin and add hybrid semantic retrieval

- Changed: Renamed the product to Celestin (distribution `celestin-memory` 0.2.0, commands `celestin` and `celestin-index`, MCP server name and `ping` text, README and docs). The `orbit` and `memory-bridge` commands, the `orbit` import package, all MCP tool names, the SQLite schema and `MEMORY_BRIDGE_DB_PATH` are kept for compatibility; `CELESTIN_DB_PATH` takes precedence when set. Added an opt-in semantic layer (`src/orbit/semantic/`: fastembed provider, sqlite-vec index in a separate file, reconciliation indexer, `celestin-index` CLI) and Reciprocal Rank Fusion in `RetrievalService` (`src/orbit/retrieval/hybrid.py`). Enabled with `CELESTIN_SEMANTIC_SEARCH=1`; when enabled, tool responses gain a `retrieval` object (`mode`, `warnings`). Tests now locate console scripts next to the interpreter, so they pass without activating the venv.
- Why: Rebrand the product and let reworded queries find saved memories without losing exact FTS5 matching.
- Validation: Full suite with the semantic extra: 171 passed, 1 skipped (opt-in real-model test). The real-model test passed separately with `CELESTIN_RUN_MODEL_TESTS=1`. A venv without the extra: all non-semantic tests passed and the semantic tests skipped. A manual run against a disposable database with the real model found the reworded memory, kept the exact error-code match first, excluded other projects, and returned nothing for an unrelated query.
- Limitations: No plugin manifest, marketplace entry or skill exists in the repository, so none was renamed. The similarity cut-off (0.6) comes from a small probe of one model and should be tuned. No MCP tool approves decisions, so approved decisions need `celestin-index sync`. Not tested on Windows/macOS. Vector search is brute-force. Remote embedding providers are not implemented. Historical entries below keep the Orbit name.

## 2026-10-11T09:11:48+05:30 — Add reusable Orbit project-memory skill

- Changed: Added `.agents/skills/orbit/SKILL.md`, guidance in `AGENTS.md`, and README setup, invocation, and teammate instructions.
- Why: Give Codex a reusable workflow for safe project-scoped retrieval and explicitly approved saves through the existing Orbit MCP tools.
- Validation: Skill metadata validator passed; `python -m pytest -p no:cacheprovider -o addopts=''` on the rebased branch: 107 passed; `git diff --check` clean.
- Limitations: No canonical project identifier is documented for this repository. This adds no project ID, MCP configuration, credentials, or per-user authorization.

## 2026-10-11T09:06:01+05:30 — Align README with the implemented project

- Changed: Rewrote `README.md` with cross-platform installation, database configuration, Codex CLI setup, MCP tool examples, and current behavior and limitations.
- Why: The previous setup was Windows-only and omitted important implementation details for running and demonstrating Orbit.
- Validation: Compared instructions against `pyproject.toml`, the SQLite engine, MCP server, and retrieval documentation; checked Markdown links and `git diff --check`.
- Limitations: No application UI or hosted demo is implemented; the demo uses a connected local MCP client.

## 2026-10-11T05:18:21+05:30 — Validate end-to-end MCP memory workflow

- Changed: Added a two-process stdio MCP integration test using a disposable SQLite database; fixed FastMCP error wrapping so `SOURCE_MESSAGE_NOT_FOUND` keeps its stable category over the protocol.
- Why: Validate persistence across a server restart, cross-session retrieval, project scoping, lifecycle filtering, and the public error envelope without touching project data.
- Validation: `python -m pytest -p no:cacheprovider -o addopts=''` — 107 passed; `git diff --check` — clean. The focused test also passed independently.
- Limitations: No authenticated identity or identity-to-project authorization exists. `project` remains a caller-selected scope; current isolation prevents cross-project record mixing but does not authorize callers. Decision approval was performed through the repository in the test fixture because no approval MCP tool exists.

## Template setup — 10 October 2026

- Changed: Added the event README, rules, agent guide, change log, AI usage record, submission checklist, Git ignore rules, and pull request checklist.
- Why: Give participants a consistent starting point for event rules, basic coding patterns, documented changes, and regular commits.
- Validation: Checked local Markdown links, event detail consistency, required documents, and diff whitespace before publishing.
- Limitations: Documentation scaffolding only; no participant project, application, or working demo is included. GitHub publication and template status are verified separately after creation.

## Template setup — 10 October 2026
