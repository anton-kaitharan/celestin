# Task: Capability 3 automatic knowledge capture

## Acceptance criteria
- [x] Explicit bounded source input produces traceable deterministic candidates.
- [x] Duplicates are idempotent; explicit conflicts remain visible; uncertain claims remain unverified.
- [x] Secret-like candidate text is suppressed without storing its value.
- [x] Project-scoped human review supports list, inspect, edit, approve, reject, and needs-evidence states.
- [x] No database, shared-memory, MCP, authorization, retrieval, or System 1 integration is added.
- [x] Focused tests and a final diff review pass.

## Baseline
- Date/time and timezone: 2026-10-15 00:36 IST
- Branch / starting commit: `main` / `d418c79`
- Initial worktree changes preserved: clean tracked/untracked worktree; branch is three commits ahead of origin.
- Relevant commands: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/capture tests/evidence/test_service.py tests/project_state/test_reconstructor.py`; `git diff --check`.

## Iterations
### Iteration 1 — Repository inspection and contract plan
- Change: Inspected repository status, Capability 1/2 models and contracts, existing repositories/authorization, and extractor/model integrations. Orbit MCP tools are unavailable in this session.
- Verification command(s): `git status --short --branch`; targeted source reads and `rg` searches.
- Exit code(s) and actual result: inspection commands passed; clean worktree on `main` at `d418c79`.
- Failure diagnosis / evidence (or `none`): no existing automatic capture module; no applicable existing extractor. Capability 1 persistence is still a domain port.
- Retry hypothesis and correction (or `not applicable`): not applicable.
- Criterion status: `verified` (implementation plan established)

### Iteration 2 — Add deterministic capture and review queue
- Change: Added `orbit.capture` input/candidate records, labeled-line and structured-event extraction, input bounds, sensitive-content suppression, normalized idempotency, explicit conflict links, project-bound review operations, an ephemeral in-memory repository, Capability 1 evidence conversion for approved items, and documentation. No production database/MCP/authorization/retrieval code changed.
- Verification command(s): `PYTHONPATH=src .venv/bin/python -m pytest -q tests/capture/test_pipeline.py`.
- Exit code(s) and actual result: First run: 2 test failures due to expectations that attempted editing a rejected candidate and conflated duplicate editing with conflict preservation. Tests were aligned with the intended terminal review states and duplicates remain distinct provenance entries; rerun: 18 passed.
- Failure diagnosis / evidence (or `none`): Prototype behavior was correct; initial test cases crossed review-state constraints.
- Retry hypothesis and correction (or `not applicable`): Kept rejection terminal, used a pending candidate for secret-edit validation, and verified explicit conflict groups retain all alternatives.
- Criterion status: `verified`

### Iteration 3 — Verify adjacent capabilities and repository contracts
- Change: Added documentation for ephemeral durability, explicit authorization seam, non-verification semantics, category mapping decisions, and future Capability 1/2 integration.
- Verification command(s): `PYTHONPATH=src .venv/bin/python -m pytest -o addopts='' -q tests/capture/test_pipeline.py tests/evidence/test_service.py tests/project_state/test_reconstructor.py tests/database/test_decisions.py tests/database/test_messages.py tests/test_hosted_scope.py`; `PYTHONPATH=src .venv/bin/python -m compileall -q src/orbit/capture`; `git diff --check`.
- Exit code(s) and actual result: 100 passed, 6 subtests passed; compileall and diff check exited 0.
- Failure diagnosis / evidence (or `none`): no remaining focused failures.
- Retry hypothesis and correction (or `not applicable`): not applicable.
- Criterion status: `verified`

## Final verification and handoff
- Final commands and results: `PYTHONPATH=src .venv/bin/python -m pytest -o addopts='' -q tests/capture/test_pipeline.py tests/evidence/test_service.py tests/project_state/test_reconstructor.py tests/database/test_decisions.py tests/database/test_messages.py tests/test_hosted_scope.py` — 100 passed, 6 subtests passed. Capture module compile check passed. `git diff --check` passed; whitespace scan of new files passed. No full suite, live database, model, MCP, or hosted check was run.
- Acceptance criteria still open: Production durability and authorization are intentionally not part of this prototype.
- Blockers / remaining risks: In-memory queue loses state at process exit; extraction recognizes only explicit labels or structured events; credential matching is heuristic; persistent/category/review identity contracts need shared-memory owner agreement.
- Commit SHA (if committed; never rewrite it): not committed by this task.
