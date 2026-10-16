# Task log: Harden Resurrection Layer domain contracts

## Goal and acceptance criteria
- Identify and fix one high-impact, bounded domain-contract gap across Resurrection Layer capabilities.
- Preserve project scope, evidence provenance, uncertainty, and existing user work.
- Add normal and failure regression coverage; do not add external infrastructure or alter persistence/auth/MCP/retrieval implementations.

## Iteration 1 — inspect and baseline
- Initial state: branch `main`, commit `78c0cec`; pre-existing modified `AI_USAGE.md` and `CHANGELOG.md`, plus untracked `tests/integration/test_capability_flow.py`. Preserved all.
- Gap: `evidence_sources_for(candidate)` mapped an approved candidate without requiring the destination project. The integration test bridge had a separate scope check, masking this production helper gap.
- Baseline command: `PYTHONPATH=src .venv/bin/python -m pytest -o addopts='' --import-mode=importlib -q tests/integration/test_capability_flow.py tests/capture/test_pipeline.py tests/evidence/test_service.py tests/project_state/test_reconstructor.py tests/sync/test_service.py tests/test_mcp_authorization.py tests/test_hosted_scope.py`
- Result: exit 0; 115 passed, 12 subtests passed.

## Iteration 2 — enforce project association at conversion
- Change: Require `target_project` in `evidence_sources_for`; validate it and reject candidate/target mismatch before mapping references. Replace the integration harness's private scope check with the production helper. Document that the check is not authentication.
- Files: `src/orbit/capture/service.py`; `tests/capture/test_pipeline.py`; `tests/integration/test_capability_flow.py`; `docs/automatic-knowledge-capture.md`; `CHANGELOG.md`; `AI_USAGE.md`.
- Focused command: `PYTHONPATH=src .venv/bin/python -m pytest -o addopts='' --import-mode=importlib -q tests/capture/test_pipeline.py tests/integration/test_capability_flow.py`
- Result: exit 0; 26 passed.
- Affected suite command: `PYTHONPATH=src .venv/bin/python -m pytest -o addopts='' --import-mode=importlib -q tests/integration/test_capability_flow.py tests/capture/test_pipeline.py tests/evidence/test_service.py tests/project_state/test_reconstructor.py tests/sync/test_service.py tests/test_mcp_authorization.py tests/test_hosted_scope.py`
- Result: exit 0; 116 passed, 12 subtests passed.

## Review
- [x] Review final diff for unrelated edits and whitespace; `git diff --check` passed. Existing `AI_USAGE.md`, `CHANGELOG.md`, and untracked integration-harness status were present before implementation and retained with only this task's appended/contract updates.
- No PostgreSQL/MCP/hosted authorization or full-suite validation claimed.
