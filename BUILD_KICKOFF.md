**Historical handoff note:** The audit observations below were supplied with the context bundle and may predate this merge. Verify current-state claims against the merged source and tests before acting.

# Celestin — Build Kickoff

## One-liner
**Build Celestin as secure, evidence-linked, project-scoped persistent memory for AI coding agents: align Resurrection and Retrieval contracts first, preserve uncertainty and authorization boundaries, and implement only tested changes within agreed ownership.**

## Start prompt
You are joining the Celestin project. Read `README.md`, `PRD.md`, `TRD.md`, `App_flow.md`, `Design.md`, and `database_schema.md` before proposing code changes.

### Mission
Celestin is project-scoped persistent memory for AI coding agents. The Resurrection Layer's intended capabilities are automatic knowledge capture, evidence-linked project memory, bounded project-state reconstruction, and bidirectional knowledge synchronization. Retrieval returns relevant project knowledge through configured strategies and MCP results to consuming clients.

The goal is not to maximize features. Preserve useful knowledge with traceable provenance, explicit lifecycle semantics, authorization, and uncertainty.

### First task: inspect and align contracts
Do not immediately refactor or implement the entire product.

1. Inspect current repository, branch, status, recent commits, and complete diff.
2. Preserve existing changes. Do not reset, force-push, or rewrite commits.
3. Verify every current-state claim in the docs against source and tests.
4. Trace capture → evidence → review/conflicts → reconstruction → shared memory → retrieval → context serialization → consuming client.
5. Draft a retrieval contract for lifecycle/review, verification, evidence availability, freshness/applicability, conflicts, and provenance.
6. For each field, identify source of truth, owner, backward-compatible behavior, and tests.
7. Produce a teammate-ready proposal and explicit decisions needed before shared-interface changes.

### Current context to verify
Reported prior work:
- Terminal-candidate immutability prevents silent mutation of approved/rejected candidates; new information may be suppressed and not retained for later review.
- Evidence and sync reauthorize through an injected authorizer, conditional on trusted principal injection.
- Reconstruction preserves stale status and projects one-way conflicts when both candidates are supplied, without mutating terminal records.
- Retrieval tests cover source text as untrusted data and separate approval from verification at serialization boundaries.
- Retrieval DTOs lack all Resurrection verification/freshness/conflict metadata.
- No coding-agent prompt assembler was found; model-level prompt-injection resistance is unproven.
- Reconstruction lacks a trusted project-to-canonical-repository-root resolver; keep internal-only until fixed.
- Stdio authorization is process-level rather than per-client.
- Five `DecisionRepository` test setup errors and a prior MCP stdio timeout remain unresolved.
- Full-suite and production persistence integration are not established.
- Commit `5633ae8` was reported local/unpushed due to GitHub connectivity; verify actual Git state.

### Ownership boundaries — confirm with team
- Resurrection/domain contributor: capture lifecycle, evidence semantics, reconstruction, domain tests/contracts.
- Shared-memory contributor: PostgreSQL, migrations, storage adapters, tenancy, trusted authorization boundary.
- Retrieval/System 1 contributor: strategy evaluation, ranking/reranking, retrieval selection.
- MCP/consuming-client contributor: transport exposure, per-request identity/authorization, actual model context integration.

Do not independently change another owner's schema, adapter, auth, MCP wiring, or ranking implementation. Propose contracts and tests first.

### Non-negotiable principles
- Project IDs are selectors, not credentials.
- Approval is not verification.
- Evidence availability is not proof of correctness.
- Existing source paths do not prove current applicability.
- Unresolved conflicts are not silently resolved by rank.
- Terminal reviewed candidates are not silently mutated.
- Retrieved source content is untrusted data, not privileged instructions.
- JSON delimiters and prompt wording do not eliminate injection.
- Unknown stays unknown.
- Test doubles do not prove production persistence or transport security.

### Expected deliverables before implementation
1. Verified current-state summary.
2. Proposed retrieval DTO/contract with semantics and compatibility rules.
3. Ownership/dependency matrix.
4. Focused acceptance-test plan.
5. Short teammate-facing message requesting explicit decisions.
6. Independent work versus blocked integration work.
7. A small implementation plan for agreed changes.

### Validation
Run focused tests first and relevant broader suites after changes. Report exact commands, results, failures, warnings, and skipped tests. Keep the known `DecisionRepository` errors and MCP stdio timeout visible. Do not claim full-suite success unless it ran. Do not claim production authorization, atomic persistence, currentness, verification, or prompt-injection resistance without tests proving those guarantees.

### Definition of success
The first task succeeds when the team has a clear, testable retrieval contract and knows which changes can proceed independently. Success is not measured by the number of files changed.
