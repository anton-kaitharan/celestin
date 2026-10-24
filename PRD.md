# Celestin — Product Requirements Document

**Status:** Working draft for team alignment. Verify against the current repository before implementation.

## Product summary
Celestin is project-scoped persistent memory for AI coding agents. It aims to preserve useful decisions, discoveries, tasks, and evidence across conversations and developers, then return bounded, traceable context to authorized clients. Its value is not merely chat-history search: it must preserve provenance, uncertainty, lifecycle, and project boundaries.

## Problem
Project knowledge is scattered across conversations, documents, source code, and agent sessions. Developers repeatedly reconstruct decisions and constraints, risking repeated work and contradictory assumptions.

## Users and use cases
- Developers using coding agents across sessions.
- Teams sharing knowledge across developers and supported AI clients.
- Capture meaningful knowledge; associate evidence; review claims; reconstruct bounded project state; retrieve scoped context; synchronize authorized knowledge across clients.

## Four Resurrection Layer capabilities
1. Automatic knowledge capture.
2. Evidence-linked project memory.
3. Bounded project-state reconstruction.
4. Bidirectional knowledge synchronization.

## Principles
- Project isolation; evidence and provenance; explicit uncertainty.
- Approval is not verification.
- Historical evidence is not necessarily current.
- Conflicts are preserved, not silently resolved by rank.
- Approved/rejected terminal candidates are not silently mutated.
- Retrieved source text is untrusted data, not privileged instructions.
- Agree on contracts before cross-team integration.
- Measure utility against no-memory and manually maintained project-context baselines.

## Scope
**In scope:** capture lifecycle, evidence links, review/conflict semantics, bounded reconstruction, retrieval filtering/context, synchronization contracts, MCP integration where currently supported, regression and integration tests.

**Not an independent change:** PostgreSQL migrations, shared storage adapters, hosted authentication, MCP wiring, or ranking/reranking owned by teammates. Do not claim model-level prompt-injection resistance without testing the actual consuming client.

## Security and integrity requirements
- Evidence/sync operations reauthorize through a trusted injected authorizer.
- A caller-supplied project ID or scope is not authorization.
- External exposure requires trusted per-request identity and project/action authorization.
- Reconstruction requires a trusted project-to-canonical-repository-root resolver before external exposure.
- Retries must be idempotent; materially changed terminal candidates must not inherit review decisions.
- Suppressed new information must be observable; it may currently not be retained for later review.
- Retrieved content remains untrusted.
- Secret capture, redaction, retention, and response minimization need an explicit policy.

## Evaluation
Proposed measures (not proven results): project-isolation tests; duplicate-free retries; no silent terminal mutation; provenance preservation; context-budget compliance; labelled capture precision/recall; useful evidence recall, context noise, task success, latency, and token/API cost versus baselines.

## Reported current state — verify in repository
- Terminal-candidate immutability implemented and tested.
- Evidence and sync reauthorize through injected authorizers, conditional on trusted principal injection.
- Reconstruction preserves stale status and can project one-way conflicts when both candidates are available.
- Retrieval tests cover source text as untrusted content and separate approval from verification at serialization boundaries.
- Retrieval DTOs do not yet carry all Resurrection verification/freshness/conflict metadata.
- No coding-agent prompt assembler was found; model-level injection resistance is untested.
- Reconstruction lacks trusted repository-root resolution; keep internal-only until addressed.
- Stdio authorization is process-level, not independently per client.
- Five known retrieval-strategy setup errors involve undefined `DecisionRepository`; a previous MCP stdio test timed out for an unknown reason.
- Full-suite and production persistence integration are not established.
- Commit `5633ae8` was reported local/unpushed due to GitHub connectivity; verify current Git state.

## Open decisions
Auto-accept policy; retention of suppressed post-review evidence; canonical verification/freshness/conflict fields; conflict link representation; repository resolver; per-request authorization; secret policy; supported clients and sync scope; evaluation dataset and success thresholds.

## Release gate
Do not claim production-secure/integration-ready until contracts are agreed, authorization and repository boundaries are validated, persistence integration and regression tests pass, known failures are investigated, and actual consumer-boundary testing exists for model-level security claims.

## Local MCP MVP reference

This section preserves the branch's earlier implementation-focused document as a supplement to the team's conceptual handoff above. Verify it against the merged source before treating it as a current status claim.


## Status and scope

This PRD describes the implemented local MVP and separates future hosted goals from current behavior. The current product is a Python MCP server used by coding and research assistants; it does not include a graphical interface or hosted multi-user service.

## Problem

AI assistants lose useful project context between chats and tools. Users need a project-scoped place to persist selected messages and decisions, search previous work, and provide a bounded, traceable context bundle to a later session.

## Users

- Developers or researchers using MCP-compatible assistants.
- Local agents that need durable project context across sessions.
- Project collaborators who operate a shared hosted service in a future release (planned, not implemented in the current runtime).

## Product goals

1. Persist chat messages and decision drafts locally with project and conversation scope.
2. Find relevant history through predictable keyword search and optional retrieval extensions.
3. Return source IDs, timestamps, status, and bounded context so assistants can trace what they use.
4. Make local setup reproducible and keep existing Orbit command, import, and database-path compatibility.
5. Fail closed when local project access is not configured.

## Current MVP requirements

| Capability | Requirement | Current behavior |
|---|---|---|
| Health | An MCP client can check that the server responds. | `ping` returns a health message. |
| Ingestion | Store a validated message under a project and conversation. | `ingest_chat_message` returns a message ID and UTC creation timestamp. |
| Decision capture | Save a decision linked to a source message in the same project. | `save_decision` creates or updates a draft; approval is not a tool operation. |
| History search | Search message content within one project. | `search_chat_history` returns ranked messages and provenance fields. |
| Context retrieval | Return useful, traceable records under a requested budget. | `retrieve_context` returns items, rendered context, estimate metadata, exclusions, and optional retrieval diagnostics. |
| Access | Prevent an unconfigured local process from accessing project data. | A process-level project/action allowlist is checked before data calls. |

## Non-functional requirements

- Local-first: SQLite is the runtime source of truth; the server uses stdio.
- Data isolation: retrieval and persistence queries are project-scoped.
- Safe failures: malformed input, denied access, missing source messages, and storage/retrieval failures have stable error categories without leaking internals.
- Traceability: results retain IDs, project, source/conversation, status, and timestamps where applicable.
- Optionality: semantic and PageIndex features remain opt-in and degrade with warnings where documented.
- Compatibility: preserve `orbit` package/imports, `orbit` and `memory-bridge` executable aliases, and `MEMORY_BRIDGE_DB_PATH`; `CELESTIN_DB_PATH` takes precedence.

## Out of scope for the current MVP

- A web dashboard or GUI.
- Authenticated multi-user identity, remote MCP transport, or hosted project authorization.
- Automatic approval of captured decisions.
- Automatic import of other assistants' chat histories.
- Hosted PostgreSQL storage components exist in this branch, but hosted backend selection is not the local runtime default. The hosted tenancy SQL under `docs/` remains a design/validation artifact.

## Success criteria

- A local MCP client can discover and call the tools over stdio.
- Ingested messages and decisions persist in SQLite and can be found by later sessions.
- Search and context results stay within the requested project and preserve available provenance.
- Invalid inputs and denied projects fail predictably.
- Setup and limitations are accurately described in the README.

## Open product decisions

- Who may approve, deprecate, archive, and purge canonical knowledge in a hosted product.
- Hosted identity provider and how workspace memberships and explicit project grants are administered.
- Retention duration, legal-hold behavior, and audit requirements before permanent purge.
- Whether a future user interface is needed; none is specified by the current product.
