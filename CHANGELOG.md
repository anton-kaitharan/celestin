# Change log

Record each meaningful project change during the event, newest first. Update this file in the same commit as the change. Use a real timestamp with timezone, for example `YYYY-MM-DDTHH:MM:SS+05:30`.

## Event build log

## 2026-10-15T22:06:50+05:30 — Block previously used PostgreSQL test targets

- Changed: The live-test target parser now rejects the two previously used disposable database names in addition to the older failed target. The PostgreSQL integration guide lists all three retired targets and says not to reuse them.
- Why: Those prior names otherwise satisfy the nonce format and could be accidentally selected for another migration run.
- Validation: PostgreSQL non-live subset: 80 passed; full suite with `--import-mode=importlib` reported 464 passed, 38 skipped, and one Windows symlink test failure (`WinError 1314`); rerun excluding that environment-dependent test: 466 passed, 38 skipped, 1 deselected. `git diff --check` passed. No live PostgreSQL connection or test was run.
- Limitations: Real PostgreSQL authorization and cursor cleanup remain unverified. A fresh, owner-approved database and role are still required.

## 2026-10-15T21:55:54+05:30 — Reuse project keys across workspace fixtures

- Changed: Updated the live authorization fixture so projects in separate workspaces share the same `legacy_project_key`, while another project in the first workspace keeps a distinct key.
- Why: Exercise that public project-key resolution stays workspace-scoped when the same key exists in multiple workspaces.
- Validation: `git diff --check` passed. No live PostgreSQL test was run because this change is in the live fixture and no newly approved disposable target was available.
- Limitations: The fixture behavior still needs verification in an authorized live PostgreSQL run; no production authorization behavior changed.

## 2026-10-15T21:52:27+05:30 — Close tracked PostgreSQL cursors in live-test cleanup

- Changed: `TrackedConnection.close()` now closes tracked open cursors, continues attempting remaining cursors if one close fails, and closes the underlying connection in a `finally` block. Added non-live coverage for idempotent closure and cleanup-error handling.
- Why: Live integration assertions intentionally verify both connection and cursor cleanup; Psycopg connection closure alone does not satisfy the tracked cursor checks.
- Validation: `tests/postgres/test_live_support.py` — 47 passed; complete `tests/postgres` suite — 78 passed, 18 skipped; `git diff --check` passed. No PostgreSQL connection or live test was run.
- Limitations: Scripted tests verify the tracking helper’s behavior; this change does not independently establish live Psycopg/PostgreSQL behavior.

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
