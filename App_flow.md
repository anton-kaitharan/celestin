# Celestin — Application Flow

**Status:** Conceptual flow based on reported architecture; verify routes against repository.

## High-level flow
```text
Developer / AI client
  → supported entry point (staging, direct agent, MCP)
  → input validation and capture
  → candidate identity and retry detection
  → evidence/provenance association
  → review lifecycle and conflict discovery
  → Resurrection Layer
  → shared-memory interface
  → retrieval candidates
  → project/lifecycle filtering
  → ranking/fusion, deduplication, context budget
  → JSON/MCP result
  → consuming client treats result as untrusted evidence
```
Not every connection is proven as a complete production integration.

## Capture
1. Receive source content and metadata.
2. Establish trusted project scope at the application boundary.
3. Extract candidate knowledge.
4. Distinguish identical retry, new evidence, changed content, and conflict.
5. Associate evidence and provenance.
6. Apply lifecycle policy.
7. Do not silently mutate terminal approved/rejected candidates.
8. Report suppressed information explicitly; current behavior may not retain it for later review.
9. Return an explicit outcome according to actual API semantics.

## Review and conflicts
- Review decision and verification are separate.
- Evidence may be missing, changed, available, or unresolved if supported.
- Preserve conflicting claims; do not silently resolve by rank or approval.
- One-way conflict links may exist due to terminal-candidate immutability.
- Reconstruction can flag both linked claims only when both are supplied.
- Do not claim resolution without an explicit supported resolution event.

## Reconstruction
1. Receive authorized project context and relevant knowledge.
2. Resolve repository scope through a trusted project-to-repository resolver before external exposure.
3. Gather supported decisions, open tasks, recorded tests, evidence, and conflicts.
4. Distinguish historical, stale, unverified, conflicted, and unknown applicability.
5. Do not mutate terminal source records.
6. Return a bounded snapshot, not a complete live-repository claim.

**Current blocker:** reconstruction accepts a repository root and project ID without a trusted resolver proving they belong together; keep internal-only until fixed.

## Retrieval
1. Receive query and trusted project scope.
2. Generate candidates using configured strategies.
3. Filter by project and lifecycle.
4. Rank/fuse and deduplicate using existing strategy.
5. Select within context budget.
6. Serialize context with metadata currently supported.
7. Return MCP result.
8. Consuming client treats all source text as untrusted.

**Known gap:** retrieval DTOs do not carry all Resurrection verification/freshness/conflict metadata.

## Synchronization (intended)
Authorized client submits knowledge/change → trusted scope/action revalidation → identity/idempotency checks → provenance and relationships preserved → shared-memory persistence according to agreed atomicity contract → other authorized clients retrieve updates.

Do not claim persistent bidirectional sync until real adapter and multi-client tests prove it.

## Failure flows
- Unauthorized project/action: deny before storage access.
- Missing evidence: retain unknown/unresolved state; do not upgrade verification.
- Terminal recapture: preserve terminal record and report suppression.
- Conflict: preserve both claims and uncertainty.
- Missing indexed record: discard stale index hit rather than return dangling data.
- Unknown freshness: do not assert current applicability.
- Malicious source text: untrusted data; actual client enforcement needs integration testing.
- Persistence failure: verify rollback/atomicity using real adapter.

## Existing local MCP workflows

This section preserves the branch's earlier implementation-focused document as a supplement to the team's conceptual handoff above. Verify it against the merged source before treating it as a current status claim.


## Current interface

Celestin has no graphical application in this checkout. Its user-facing interaction is through MCP tools called by a local MCP-compatible assistant. The flow below describes that tool experience, not a designed web UI.

## First-time local setup

1. Install the package in a Python 3.10+ environment using the README instructions.
2. Register the `celestin` stdio server in the MCP client.
3. Select a writable SQLite path using `CELESTIN_DB_PATH` (or the supported legacy variable).
4. Configure `CELESTIN_LOCAL_PROJECT_ACCESS` with exact project names and read/write actions. If no policy grants the requested scope, data-tool calls are denied.
5. Restart the client if it was already running, then call `ping` to confirm server availability.

## Capture and preserve a decision

```text
Assistant / user
  └─ asks to retain a useful message or decision
       ├─ ingest_chat_message(project, conversation_id, role, content)
       │    └─ returns message_id and created_at
       └─ save_decision(project, key, value, source_message_id=message_id)
            └─ returns decision_id and status=draft
```

The source message must belong to the requested project. Saving a decision creates a draft; it does not approve it. The current MCP tool set has no approval or deprecation operation.

## Find past discussion

1. The assistant calls `search_chat_history` with project and query; `limit` is optional.
2. Celestin authorizes read access before retrieval.
3. The retrieval service searches project-scoped history and returns ranked records with source/conversation identifiers, timestamps, and scores.
4. If no records match, the client receives a successful empty result; backend failures are returned as errors rather than empty success.

## Build context for a new task

1. The assistant calls `retrieve_context(project, query, token_budget)`.
2. Celestin checks read access before querying.
3. The retrieval pipeline combines enabled strategies, prioritizes approved decisions, excludes draft/deprecated decisions by default, and assembles whole records within its documented estimated budget.
4. The response includes item IDs, source references, timestamps/status, rendered context, budget metadata, and eligible exclusions. Optional backends may add retrieval diagnostics or warnings.
5. The assistant uses the returned context and should preserve source traceability in its answer when relevant.

## Error and recovery paths

- Invalid fields or out-of-range limits/budgets: `INVALID_ARGUMENT`; correct the request.
- Project/action not on the local allowlist: `ACCESS_DENIED`; an administrator must update the local process configuration. Do not try alternate project names to bypass access control.
- Unknown or cross-project source message: `SOURCE_MESSAGE_NOT_FOUND`; do not save the decision without a valid source in that project.
- Storage failure: `STORAGE_ERROR`; do not claim a write succeeded.
- Retrieval dependency/service failure: a retrieval error category; optional semantic/PageIndex backends may instead provide documented fallback warnings.

## Collaboration boundary

Separate local SQLite files do not synchronize between machines. Cross-device shared memory requires a hosted authenticated service, which is not implemented by this flow. The hosted tenancy draft is planning material only.
