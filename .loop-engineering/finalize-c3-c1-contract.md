# Task log: Finalize Capability 3 to Capability 1 knowledge contract

## Goal
- Preserve meaningful approved-candidate provenance through C1 storage-neutral knowledge records and C2 reconstruction.
- Keep review, verification, and lifecycle states separate; do not change migrations, adapters, MCP, authentication, or model integration.

## Initial state and baseline
- Branch `main`, ahead 1 at `d1f3b3b`.
- Existing unstaged changes from the preceding evidence-scope audit were preserved and extended; no staged or untracked files were present at task start.
- Baseline: `PYTHONPATH=src .venv/bin/python -m pytest -o addopts='' --import-mode=importlib -q tests/capture/test_pipeline.py tests/evidence/test_service.py tests/project_state/test_reconstructor.py tests/integration/test_capability_flow.py tests/test_mcp_authorization.py tests/test_hosted_scope.py tests/database/test_decisions.py tests/database/test_messages.py tests/storage/test_postgres_memory_store.py`
- Result: exit 0; 123 passed, 12 subtests passed.

## Implementation
- Added optional `CaptureKnowledgeProvenance` and source reference value objects to the `KnowledgeRecord` domain model.
- Added approved-only capture provenance conversion, explicit category mappings, C1 validation of project/review/verification and source-evidence links, and C2 preservation of provenance, record IDs, evidence status/checker metadata, and lifecycle links.
- Updated the test-only cross-capability bridge to validate the envelope. No production database adapter or schema was changed.

## Validation
- Focused capture/evidence/reconstruction/integration suite: exit 0; 83 passed.
- Affected suite including authorization and storage regressions: exit 0; 136 passed, 12 subtests passed.
- `git diff --check`: passed.
- Full suite and production persistence/hosted authorization not run.

## Handoff dependency
- Persistence owner must agree on lossless storage for the optional provenance envelope, candidate idempotency, and conflict lookup by candidate ID before a durable adapter is claimed compatible.
