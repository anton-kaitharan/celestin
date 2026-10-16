# Task: Capability 4 bidirectional knowledge synchronization

## Acceptance criteria
- [x] Validate versioned project events and preserve source provenance.
- [x] Enforce supplied `AuthorizedProject` type/action and project isolation at service calls; trusted scope issuance remains a caller responsibility.
- [x] Provide idempotent in-memory ingestion and ordered incremental pages.
- [x] Keep delivery separate from review, approval, verification, and project truth.
- [x] Add focused tests and document persistence/auth integration contracts.

## Baseline
- Date/time and timezone: 2026-10-15T01:11:09+05:30
- Branch / starting commit: `main` / `079df04`
- Initial worktree changes preserved: clean; no preexisting uncommitted changes.
- Relevant test and development commands: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/sync/test_service.py`; targeted Capabilities 1–3 and auth regressions.

## Iterations
### Iteration 1 — inspect and plan
- Change: Inspected current branch, authorization scope, storage and MCP contracts, and Capabilities 1–3. No shared sync event store or reviewed persistence contract exists. Plan is limited to `src/orbit/sync/`, `tests/sync/`, a sync contract doc, and required project logs.
- Verification command(s): `git status --short --branch`; read-only source/docs inspection.
- Exit code(s) and actual result: 0; worktree is clean at `079df04`.
- Failure diagnosis / evidence (or `none`): none.
- Retry hypothesis and correction (or `not applicable`): not applicable.
- Criterion status: `verified`

### Iteration 2 — event contract and service
- Change: Added `orbit.sync` schema v1 records, the storage-neutral `SyncEventStore` port, project-scoped in-memory adapter, scope-checked publish/read service, stable idempotency IDs, strict project-bound sequence cursors, bounded pagination, retention-expiry behavior, and explicit capability integration docs/tests.
- Verification command(s): `PYTHONPATH=src .venv/bin/python -m pytest -q tests/sync/test_service.py` (first attempt found a missing import and an expired-cursor test expectation; both were corrected). Combined regression command below.
- Exit code(s) and actual result: focused final sync run passed; combined regression run `PYTHONPATH=src .venv/bin/python -m pytest -o addopts='' --import-mode=importlib -q tests/sync/test_service.py tests/evidence/test_service.py tests/project_state/test_reconstructor.py tests/capture/test_pipeline.py tests/test_mcp_authorization.py tests/test_hosted_scope.py tests/database/test_decisions.py tests/database/test_messages.py tests/storage/test_postgres_memory_store.py` — 136 passed, 12 subtests passed.
- Failure diagnosis / evidence (or `none`): The initial combined default-import-mode run failed collection because `tests/sync/test_service.py` and `tests/evidence/test_service.py` share a module basename. `--import-mode=importlib` resolved the collection collision. No test failure remained.
- Retry hypothesis and correction (or `not applicable`): Corrected the missing `CandidateCategory` import and updated the expiry test to resume from a retained checkpoint; selected importlib mode for the adjacent multi-directory test run.
- Criterion status: `verified`

## Final verification and handoff
- Final commands and results: Combined regression suite above — 136 passed, 12 subtests passed; `PYTHONPATH=src .venv/bin/python -m compileall -q src/orbit/sync` — passed; `git diff --check` — passed. Full suite not run.
- Acceptance criteria still open: Production durable storage, authenticated client identity, hosted scope issuance/revocation checks, and network transport are intentionally not part of the prototype.
- Blockers / remaining risks: Persistence and authorization owners must agree the contract in `docs/bidirectional-knowledge-synchronization.md` before any durable/hosted integration. In-memory adapter is single-process, non-durable, and not thread-safe. `AuthorizedProject` remains constructible/reusable if a caller bypasses the existing boundary.
- Commit SHA (if committed; never rewrite it): not committed (per task instructions).
