# Celestin — Technical Requirements Document

**Status:** Working draft. Confirm every detail against source code before coding.

## Architecture
Conceptual flow:

`Capture/staging/direct-agent → Resurrection Layer → shared-memory interface → retrieval strategies → project/lifecycle filtering → ranking/fusion and budget selection → JSON/MCP result → consuming client`

This is an intended architecture, not proof every connection is integrated in production.

## Component responsibilities
- **Capture/staging:** validate inputs, extract candidate knowledge, preserve source identity and provenance, apply identity/retry/lifecycle rules.
- **Resurrection Layer:** capture, evidence, review/conflict handling, bounded reconstruction, synchronization contracts, explicit uncertainty.
- **Shared memory:** persistence, PostgreSQL/migrations, tenancy, storage interfaces, trusted authorization boundary; owned separately.
- **Retrieval:** generate candidates, filter by project/lifecycle, rank/fuse/deduplicate, enforce context budget, preserve available metadata. Do not change ranking without its owner.
- **MCP/client:** MCP returns tool output; the client must treat retrieved text as untrusted and keep it separate from privileged instructions.

## Retrieval strategies
The system has used keyword/FTS, semantic/vector, section-oriented retrieval, and optional PageIndex. A model-based selector/System 1 approach is optional/experimental and evaluated separately. Keep ranking/reranking stable while agreeing on metadata contracts. PageIndex outline snippets can still contain hostile text; restricting returned references to the outline does not eliminate injection risk.

## Required semantic separation
- Lifecycle/review status.
- Verification status.
- Evidence availability/resolution.
- Freshness/applicability.
- Conflict status/relationships.
- Provenance/source identity.

Do not infer verification from approval, evidence existence, or rank. Do not claim currentness merely because a source path still exists.

## Security
- Protected evidence/sync operations reauthorize through a trusted authorizer.
- Caller-constructed scopes do not grant permission.
- Trusted application/transport supplies the principal.
- Project/action authorization is checked on each protected operation.
- Reconstruction repository roots must be bound to authorized projects through a trusted resolver before external exposure.
- Test path traversal/symlink escape where filesystem access is permitted.
- Project IDs, reviewer labels, and source metadata are not credentials.
- Retrieved content is untrusted regardless of approval/rank.
- Define secret handling, retention, limits, and response minimization.

## Known retrieval path
`SQLite/document index → candidates → project/lifecycle filtering, ranking and budget selection → JSON context → MCP result → consuming client`

Source text is returned verbatim in content fields and JSON-Lines `rendered_context`. Items may inherit project scope from a top-level envelope while rendered records include their own `project_id`; consumers must preserve the envelope/provenance association.

Retrieval DTOs do not yet carry all Resurrection verification, freshness, and conflict metadata.

## Reliability
- Idempotent retries create no duplicates.
- Changed identity-bound payloads do not silently overwrite reviewed candidates.
- Terminal approved/rejected candidates remain immutable unless a revision model is explicitly introduced.
- Conflict discovery does not mutate terminal records.
- Define atomicity for multi-record capture and relationship writes; validate with real persistence.
- Context budget, failure recovery, and project isolation need tests.

## Test layers
**Domain:** identity, retries, immutability, provenance, lifecycle, conflicts, stale/historical reconstruction, serialization, authorization through injected policy.

**Integration:** real adapter transactions; per-request principal and project/action authorization; canonical repository resolver; capture-to-retrieval metadata preservation; actual client trust boundary; multi-client sync.

## Known test issues
- `tests/retrieval/test_strategies.py`: five setup errors due to undefined `DecisionRepository`.
- Previous MCP stdio test timed out; cause unknown.
- Selected suites passed, but full suite and production persistence integration are not established.

## Ownership
Confirm with team: Resurrection/domain contributor owns capture lifecycle, evidence semantics, reconstruction, domain tests/contracts; shared-memory contributor owns PostgreSQL, migrations, adapters, tenancy/auth boundary; retrieval/System 1 contributor owns strategy evaluation and ranking; MCP/client contributor owns transport exposure and actual model integration.

## Local MCP and storage implementation reference

This section preserves the branch's earlier implementation-focused document as a supplement to the team's conceptual handoff above. Verify it against the merged source before treating it as a current status claim.


## Verified implementation baseline

- Distribution: `celestin-memory` (Python 3.10+); import package remains `orbit`.
- MCP framework: Python MCP SDK with FastMCP.
- Transport: local stdio.
- Runtime persistence: Python `sqlite3`; SQLite is authoritative.
- Search: SQLite FTS5/BM25. Optional local vector search and optional PageIndex document retrieval are separate derived/auxiliary capabilities.
- The source tree now includes hosted PostgreSQL storage and authorization components, but the local MCP startup still defaults to SQLite. `docs/hosted-tenancy-schema.sql` remains a separate draft and is not wired into MCP startup.

## Runtime components

```text
MCP client
   │ stdio / FastMCP
   ▼
src/orbit/mcp_server/server.py
   ├── process-level project authorization
   ├── persistence_services.py ── repositories ── SQLite engine
   └── retrieval_services.py ── retrieval service ── FTS5 / optional backends
```

- `server.py` validates the four production tool arguments, authorizes the requested project/action, maps failures to safe MCP errors, and registers `ping`.
- `persistence_services.py` delegates ingestion and decision writes to `ChatMessageRepository` and `DecisionRepository`.
- `retrieval_services.py` constructs the shared retrieval service and serializes public result bundles.
- `database/engine.py` owns SQLite connection setup and `PRAGMA user_version` migrations.
- `retrieval/` owns FTS search, ranking/fusion, filtering, strategy selection, and context assembly.
- `semantic/` contains optional embeddings and a separate vector index. `pageindex/` contains optional document-section/navigation storage and retrieval.

## MCP operations

| Tool | Inputs | Result |
|---|---|---|
| `ping` | none | Health-check string. |
| `ingest_chat_message` | `project`, `conversation_id`, `role`, `content` | Saved message fields, `message_id`, `created_at`. |
| `save_decision` | `project`, `key`, `value`, `source_message_id` | Decision fields, `decision_id`, draft status, source ID, timestamps. Source must be in the same project. |
| `search_chat_history` | `project`, `query`, optional `limit` (default 10; 1–100) | Project/query and ordered results containing IDs, conversation, content, timestamp, and score. |
| `retrieve_context` | `project`, `query`, `token_budget` (1–20,000) | Traceable items, rendered text, budget/count metadata, exclusions, and optional retrieval metadata. |

Validation limits and stable error envelopes are enforced at the MCP boundary; implementation details are in `src/orbit/mcp_server/server.py`.

## Configuration and compatibility

- `CELESTIN_DB_PATH` selects the local SQLite file when set.
- Otherwise `MEMORY_BRIDGE_DB_PATH` is honored for legacy installations.
- Otherwise the default is `data/memory_bridge.db` relative to the process working directory.
- `CELESTIN_LOCAL_PROJECT_ACCESS` is a JSON mapping from exact project names to `read` and/or `write`; `ORBIT_LOCAL_PROJECT_ACCESS` is a fallback. The Celestin variable takes precedence.
- The allowlist is bound to the server process, not an authenticated MCP caller. All clients connected to that process share the process grants.
- CLI names: `celestin`, `celestin-index`, and `celestin-docs`; compatibility server aliases: `orbit`, `memory-bridge`.

## Retrieval behavior

- FTS5 is the exact keyword baseline and uses BM25 plus deterministic tie-breaking. Query relaxation is bounded and can be disabled.
- Optional semantic retrieval combines ranked lists with reciprocal-rank fusion; the vector file stores references and vectors rather than message text.
- Optional PageIndex document navigation sends a query and document outline to the configured model provider when enabled. It is experimental and failures degrade to a warning.
- Approved decisions are prioritized; draft and deprecated decisions are excluded from normal context.
- Context budget is a UTF-8-byte-based estimate, not an exact count for a particular model tokenizer; whole records are selected or skipped.

## Security and privacy boundary

- Keep the MCP server on local stdio. There is no per-user authentication or safe remote transport in this runtime.
- Project names select a data scope but are not identity or proof of authorization.
- Do not place secrets in configuration examples, logs, or repository files.
- PageIndex query/outline data can leave the machine if a provider-backed model is configured. Semantic embeddings are local under the documented default provider.

## Future hosted direction (not current runtime)

`docs/hosted-tenancy-schema.sql` sketches users, workspaces, memberships, projects, service identities, grants, messages, and decisions. It is not a production migration or active adapter. The draft requires server-authenticated identity, explicit project authorization, transactionally protected owner lifecycle, retention/purge controls, and a separately reviewed migration/import plan before hosted use.

## Verification

Install and test commands are maintained in [README.md](README.md). Existing tests cover SQLite repositories, MCP protocol behavior, authorization, retrieval, optional semantic components, and PageIndex using temporary/test fixtures. A passing mocked or local suite does not establish hosted PostgreSQL application support.
