# Celestin project status audit

**Snapshot date:** 2026-10-18  
**Working tree:** `retrival` at `0b143b874c1f` (clean before this report was added)  
**Product reference:** `origin/main` at `8cc34a93`  
**Unmerged retrieval work:** `origin/Retrieval-layer` at `65bd8b9e`

## Executive verdict

Celestin has a useful local SQLite/MCP product and a substantial search stack. The Resurrection Layer (Capabilities 1–4) has meaningful domain prototypes, tests, and explicit contracts, but it is not a production workflow yet. Its evidence store, capture queue, sync event store, and repository reconstruction seam are not joined to the running MCP application or to durable shared storage.

The checked-out `retrival` branch is not the product's current source branch. It is a small, isolated `memory_bridge.retrieval` adapter snapshot. The product code is on `origin/main`; newer retrieval-to-knowledge-state work is on the separate `origin/Retrieval-layer` branch. Treating the current checkout as the complete project would miss most of the implementation.

The completion labels below describe whether work is wired into the product, a tested prototype, or a design/investigation. They are more reliable than a single percentage because the repository does not define one shared phase denominator.

## Phase completion

| Phase / capability | Status | What exists | What remains |
|---|---|---|---|
| Local memory and MCP foundation | **Integrated for local use** | Stdio MCP server, SQLite messages and decisions, project/action allowlist, FTS indexes, persistence and retrieval services. `origin/main` has the install configuration and README setup. | The MCP process uses SQLite by default. Its process-level allowlist does not authenticate individual clients. Decision review/status management is not exposed as a complete MCP workflow. |
| Search and context retrieval | **Integrated; optional methods vary in maturity** | FTS5 keyword search, bounded query relaxation, message/decision retrieval, ranking, context budgets, optional semantic vectors, heading sections, adaptive selection, filtering, and reranking. Evaluation scripts and frozen results are checked in. | Token budgets may be estimates. The evaluation corpus is small. PageIndex has no real-model quality result. Optional model rerankers require separately installed local models/dependencies. |
| Retrieval of Resurrection knowledge state | **Prototype on an unmerged branch** | `origin/Retrieval-layer` adds a versioned `knowledge_state` envelope and an injectable reader that preserves evidence, verification, lifecycle, capture, and conflict references. | The default MCP retrieval service has no reader; the public MCP serializer does not expose the envelope. There is no approved production mapping from retrieval record IDs to knowledge IDs, nor a storage adapter for that read. |
| Shared / hosted memory | **Persistence foundation, not a live shared service** | SQLite is the supported local store. PostgreSQL schema/migrations, a PostgreSQL message/decision adapter, authorization-session code, and guarded live-test harness exist on `origin/main`. | The MCP service factory still constructs the SQLite adapter by default. Hosted migration is an explicit operator command. No hosted MCP transport/deployment is enabled; no live PostgreSQL result is recorded in the phase notes. Resurrection records and sync events are not stored by these adapters. |
| Capability 1 — evidence-linked memory | **Domain prototype** | `KnowledgeRecord`/`EvidenceRecord` contracts, project-scoped service checks, evidence resolution/readback, verification vs lifecycle separation, provenance and supersession rules. | The docs explicitly say it is not wired to SQLite/PostgreSQL, MCP, or a production authorization path. There is no production evidence schema/store or source resolver. Unit tests use a fake store. |
| Capability 2 — project-state reconstruction (“Resurrection”) | **Read-only prototype** | Deterministic inspection of tracked Python files and project markers at an explicit Git revision; structured, source-linked findings; stale/conflict and uncertainty handling. | It does not inspect untracked changes, run tests, prove semantic correctness, or use a production Capability 1 list adapter. The optional knowledge-source seam is not wired to storage or MCP. No trusted project-to-repository resolver is implemented. |
| Capability 3 — automatic capture | **In-memory capture/review prototype** | Bounded labeled-line and structured-event extraction, candidate IDs, duplicate/conflict links, secret-like text suppression, project-bound review states, and approved-only conversion metadata. | Queue state is lost at process exit. Extraction is rule-based, not a production model/agent workflow. No durable queue, MCP review tools, or agreed storage mapping is connected. |
| Capability 4 — bidirectional sync | **In-memory service prototype** | Versioned events, project-scoped publish/read, idempotency, ordered cursors, pagination, and delivery-state separation from approval/verification. | No durable event store, network transport, hosted authorization path, or production consumer connects events to Capabilities 1–3. The current adapter is single-process and not thread-safe. |
| Cross-capability flow | **Test harness only** | Test doubles exercise capture → evidence-linked memory → reconstruction and sync delivery/state separation. | The harness documents production seams: no durable C1 store/list path, incomplete capture-to-memory mapping, and no sync consumer. Passing harness tests do not establish production integration. |
| Remote / Phase 8 | **Investigation only** | Architecture and access-boundary analysis for a tunnel or hosted MCP service. | No remote transport, tunnel, deployment, external identity provider, or approved data-sharing path is enabled. Local stdio remains the supported workflow. |

## Current workflows and boundaries

### Product workflow on `origin/main`

The running application path is:

`MCP stdio → process project/action allowlist → persistence or retrieval service → SQLite repositories/indexes`

The public tools are `ping`, `ingest_chat_message`, `save_decision`, `search_chat_history`, and `retrieve_context`. The retrieval service returns project data as tool output. It does not assemble a consuming agent's prompt, and the consuming client must treat retrieved text as untrusted evidence.

### Resurrection workflow

The intended domain path is:

`explicit source input → Capability 3 candidate/review → Capability 1 knowledge + evidence → Capability 2 read-only reconstruction → retrieval context`

Today, the first four elements are mostly separate prototypes. The cross-capability harness bridges them with test doubles. There is no approved durable candidate-to-knowledge mapping, no shared evidence store/resolver, and no production reconstruction reader. Capability 4 publishes versioned events but no production consumer applies them to that path.

### Search methods and retrieval-agent boundary

The product search stack is broader than the current checkout's adapter:

- FTS5 keyword search with strict matching and bounded relaxation fallback.
- Optional local semantic/vector search and hybrid ranking.
- Heading-section indexing and retrieval for Markdown documents.
- Adaptive strategy selection, relevance filtering, and budgeted context assembly.
- Optional PageIndex outline navigation through a configured model provider.
- Optional reranking integrations, including local model paths; these are not the default baseline.

This is a retrieval service exposed through MCP tools, not a standalone autonomous retrieval agent. The repository documents that retrieved content can contain hostile instructions and that JSON formatting does not prevent prompt injection. There is no model-level regression through the actual consuming agent. PageIndex has only stand-in/oracle evaluation results; the docs state that real-model quality, latency, and cost have not been measured.

The separate `origin/Retrieval-layer` branch improves how Capability 1 state can accompany candidates when a caller injects a reader. Its own documentation says this is not connected to the current MCP adapter and does not supply the missing candidate-to-knowledge mapping.

## Repository and branch state

- `retrival` / `upstream/retrival`: `0b143b87`, 7 commits reachable on this branch. Relative to `origin/main`, it is 108 commits behind and has 2 branch-only commits.
- Local `main`: `5633ae8d`, one commit behind `origin/main`.
- `origin/main`: `8cc34a93`, 113 commits; contains the product README, `pyproject.toml`, application, and phase code.
- `origin/Retrieval-layer`: `65bd8b9e`, one commit behind and five commits ahead of `origin/main`; it is checked out in a second worktree and is not merged into `origin/main`.
- The local refs expose 120 commits in total. I reviewed the complete commit list and changed-path summaries, then traced current implementation and phase evidence for the relevant branches. This is a status audit, not a line-by-line re-review of every historical patch.

The checked-out `retrival` tree has no `README.md`, `pyproject.toml`, or root `.gitignore`, and it tracks **20,532 files under `.venv/`**. Commit `99bdb967` added the environment and generated artifacts; `0b143b87` changes a file inside that environment. This branch should not be treated as a clean, installable product snapshot. `origin/main` contains the project setup files and does not track `.venv/`.

## Verification evidence and gaps

This audit did not run tests. It checked the recorded test results, test coverage files, source, docs, and commit history.

The checked-in task notes report several focused passing runs: Capability 3 and adjacent regressions (100 passed, 6 subtests), the evidence/reconstruction affected suite (123 passed, 12 subtests), the later cross-capability adjacent suite (143 passed, 12 subtests), and sync/storage/auth regressions (136 passed, 12 subtests). These are snapshots from different commits and do not constitute one current full-suite pass.

The reconstruction task notes record a focused 37-pass run, but also record that a bounded full-suite attempt timed out in the PageIndex stdio test; excluding that test, the run stopped at `NameError: DecisionRepository is not defined` in `tests/retrieval/test_strategies.py`. A separate integration harness note records the same retrieval-strategy setup error and the PageIndex timeout. The newest `origin/Retrieval-layer` commits add knowledge-state regression tests, but no post-change test result is recorded in the branch notes inspected here.

There is no GitHub Actions workflow in `origin/main` (only a pull-request template). Hosted PostgreSQL tests require a separately provisioned, guarded target; the notes do not claim a live run. Real PageIndex model evaluation and a consuming-agent prompt-injection regression are also outstanding.

## Severity-ranked findings

1. **High — the checked-out branch is not the current product source.** It is an isolated adapter snapshot, is far behind `origin/main`, lacks package/setup files, and tracks a 20,532-file virtual environment. Branch provenance must be resolved before using this checkout for a release or integration.
2. **High — Resurrection is not end-to-end or durable.** C1–C4 have prototypes, but C1 has no production storage/resolver, C2 has no authorized persistent knowledge reader, C3 is volatile, and C4 has no durable consumer. The test harness uses fakes and cannot close those gaps.
3. **High — shared/hosted persistence is not the running application's backend.** PostgreSQL support is a foundation and explicit operator path; the MCP application defaults to SQLite and exposes no hosted transport. The newer hosted components also do not store Resurrection state.
4. **Medium — knowledge-aware retrieval is unmerged and lacks the production join.** The optional envelope exists on `origin/Retrieval-layer`, but the default MCP path omits it, and no supported mapping links retrieval candidates to Capability 1 records.
5. **Medium — verification and agent-level behavior are not fully demonstrated.** Focused tests pass in recorded snapshots, but the full suite has known blockers and there is no CI workflow. Optional model methods lack real-model evaluation, and retrieval output has not been tested through a consuming agent.

## Recommended order to close the gaps

1. Select the canonical branch and reconcile `retrival`, `origin/main`, and `origin/Retrieval-layer`. Keep generated virtual environments and caches out of the product branch.
2. Agree and implement the Capability 1 durable schema/store and source resolver, including provenance round trips and project-bound identity.
3. Connect Capability 3 review/conversion and Capability 4 event publication/consumption to that durable store without conflating review, verification, lifecycle, or delivery state.
4. Define the authorized project-to-repository and retrieval-candidate-to-knowledge mappings; then decide whether/how reconstruction state is exposed at the MCP boundary.
5. Add a repeatable CI/full-suite gate, then run guarded live PostgreSQL checks, real-model PageIndex evaluation, and a consuming-agent trust-boundary regression.

