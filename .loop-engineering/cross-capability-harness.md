# Task: Cross-capability integration test harness

## Acceptance criteria
- [x] Exercise reviewed Capability 3 candidate → Capability 1 knowledge → Capability 2 snapshot using test doubles.
- [x] Exercise Capability 4 publication/retrieval and duplicate delivery without changing approval or verification state.
- [x] Cover conflict, historical decision, missing evidence, provenance/uncertainty, and project-scope mismatch behavior.
- [x] Record every seam that prevents production end-to-end integration.
- [x] Preserve all production interfaces and unrelated implementations.

## Baseline
- Date/time and timezone: 2026-10-15T01:40:50+05:30
- Branch / starting commit: `main` / `78c0cec`
- Initial worktree changes preserved: clean; no preexisting changes.
- Relevant test and development commands: `PYTHONPATH=src .venv/bin/python -m pytest -q --import-mode=importlib tests/integration/test_capability_flow.py`; adjacent focused regression suite.

## Iterations
### Iteration 1 — inspect interfaces and plan
- Change: Inspected Capabilities 1–4, auth scopes, test conventions, and documented project identifier. No Orbit MCP tools were exposed in this session; used checked-in code and docs. Plan is one new integration test module plus required changelog and AI usage entries; no production changes.
- Verification command(s): `git status --short --branch`; read-only interface and test inspection.
- Exit code(s) and actual result: 0; clean worktree at `78c0cec`.
- Failure diagnosis / evidence (or `none`): MCP retrieval tool unavailable in the current tool inventory.
- Retry hypothesis and correction (or `not applicable`): not applicable.
- Criterion status: `verified`

### Iteration 2 — add and run the integration harness
- Change: Added a single test module with a test-only C3→C1 bridge and fake Cap1 store/resolver plus a bound read-only Cap2 view; exercised approved and pending candidate flows, provenance/uncertainty, event idempotency and delivery-state separation, conflict and supersession, missing evidence, and scope filtering. The tests characterize metadata/interface gaps instead of changing production code.
- Verification command(s): `PYTHONPATH=src .venv/bin/python -m pytest -q --import-mode=importlib tests/integration/test_capability_flow.py`; combined adjacent suite recorded below; standalone unrelated regression probes recorded below.
- Exit code(s) and actual result: Integration module — 7 passed. Combined adjacent suite — 143 passed, 12 subtests passed.
- Failure diagnosis / evidence (or `none`): `tests/retrieval/test_strategies.py` — 5 setup errors from `NameError: DecisionRepository` at test fixture line 36. Isolated PageIndex stdio test timed out at 30 seconds, exit 124, no output. Neither failure is caused by test-only edits. No full suite run.
- Retry hypothesis and correction (or `not applicable`): Existing failures were run in isolation; no retries or test modifications were made.
- Criterion status: `verified`

## Final verification and handoff
- Final commands and results: Combined suite — 143 passed, 12 subtests passed; `git diff --check` and trailing-whitespace scan passed. Full suite not run.
- Acceptance criteria still open: None for the test harness. Production integration remains unproven.
- Blockers / remaining risks: Capability 1 lacks a list/bundle read operation; Capabilities 3→1 lack a coordinated approved-candidate adapter and category mapping; uncertainty and conflict links are not represented in the Cap1 knowledge model; Cap3→Cap1 mapping drops source occurrence timestamps; Capability 4 events are not consumed by Capabilities 1–3. A same-batch capture result can return stale conflict links while queue readback is current. See final report for priority order.
- Commit SHA (if committed; never rewrite it): not committed.
