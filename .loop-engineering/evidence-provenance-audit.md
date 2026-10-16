# Task log: Evidence provenance integrity audit

## Goal and acceptance criteria
- Trace Capability 3 capture through Capability 1 evidence-linked memory into Capability 2 reconstruction.
- Preserve prior work; identify and fix one bounded correctness defect without database, MCP, authentication, or model integration.
- Run baseline, focused, integration, and relevant regression tests; report unresolved contract gaps honestly.

## Iteration 1 — inspect and baseline
- State: branch `main`, ahead 1 at `d1f3b3b`; worktree clean at task start. Read `RULES.md`, inspected models/services/docs/tests for Capabilities 1–3 and the four-capability harness.
- Baseline command: `PYTHONPATH=src .venv/bin/python -m pytest -o addopts='' --import-mode=importlib -q tests/capture/test_pipeline.py tests/evidence/test_service.py tests/project_state/test_reconstructor.py tests/integration/test_capability_flow.py tests/test_mcp_authorization.py tests/test_hosted_scope.py tests/database/test_decisions.py tests/database/test_messages.py tests/storage/test_postgres_memory_store.py`
- Result: exit 0; 121 passed, 12 subtests passed.
- Defect: Capability 1 checked the returned knowledge record project but not each returned evidence row; Capability 2 filtered foreign-project records but not foreign-project evidence within a valid record bundle. Such a source identifier could cross the read boundary.

## Iteration 2 — enforce evidence project scope
- Change: Capability 1 fails closed on malformed or foreign-project evidence rows. Capability 2 omits evidence whose project differs from the requested project, reports a limitation, and leaves the knowledge claim unknown if no in-scope support remains.
- Files: `src/orbit/evidence/service.py`, `src/orbit/project_state/reconstructor.py`, `tests/evidence/test_service.py`, `tests/project_state/test_reconstructor.py`, `docs/evidence-linked-memory-contract.md`, `docs/project-state-reconstruction.md`, `CHANGELOG.md`, `AI_USAGE.md`.
- Focused command: `PYTHONPATH=src .venv/bin/python -m pytest -o addopts='' --import-mode=importlib -q tests/evidence/test_service.py tests/project_state/test_reconstructor.py`
- Result: exit 0; 44 passed.
- Full affected command: same baseline command above.
- Result: exit 0; 123 passed, 12 subtests passed.

## Final review
- `git diff --check` passed; review the final diff and status before handoff.
- Existing tests already cover conversion metadata loss, unresolved/missing sources, unverified claims, preserved supersession, separate conflicting claims, approval gating, project mismatch, and refusal to treat stored `verified` as proof. Metadata losses requiring model/storage contract agreement are documented, not encoded into unrelated fields.
- No full suite, live PostgreSQL, hosted authorization, or MCP integration claimed.
