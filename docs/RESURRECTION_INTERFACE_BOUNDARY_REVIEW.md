# Celestin Resurrection Interface Boundary Review

**Review date:** 2026-10-18

**Purpose:** Source-backed boundary review between Retrieval/Resurrection and shared-memory/storage. This document records interfaces and open decisions; it does not define or implement a new contract.

## Repository and decision status

The active writable worktree is `retrival` at `0b143b874c1f1632bcc4f593f538b6c7909582a2`. It has no tracked changes and has a pre-existing untracked `docs/current-project-status.md`, which this review leaves untouched. The C1/C2 and knowledge-state implementation files are in the sibling `Retrieval-layer` worktree at `21e57658aaaa6280f538d6b958e1d46c042cac9e`; that worktree already has modifications to `tests/RESURRECTION_ACCEPTANCE_SCENARIOS.md` and `tests/retrieval/test_knowledge_state.py`. They were preserved. Only these two worktrees are listed; no separate shared-storage-owner worktree was accessible.

The active `retrival` checkout is an older, partial product snapshot. The interface facts below were inspected in the `Retrieval-layer` source worktree and product/runtime source available in the same repository. Source paths and line numbers refer to those repository files. `docs/interfaces.md` is the public MCP boundary contract, not a C1 shared-memory contract.

**Status labels:** `EXISTING` means directly present in source or current behavior; it does not imply a team-approved shared-storage decision. `PROPOSED` means a review suggestion, not an adopted rule. `UNRESOLVED` means no source-backed decision was found. `APPROVED` is reserved for a decision with explicit approval evidence.

**Resolution as of 2026-10-19:** the initial v0.3 implementation decisions are recorded in the [final decision record](SHARED_MEMORY_INTERFACE_CONTRACT_V0.3_DECISIONS.md). The `PROPOSED` and `UNRESOLVED` labels in this review describe the 2026-10-18 review snapshot and are superseded for the first PostgreSQL Evidence/Knowledge implementation by that record.

**Implementation status update (2026-10-19):** Since this review, `PostgresEvidenceLinkedMemoryStore` and migrations 0005–0006 were added for the existing Evidence/Knowledge port and candidate/revision structures. The historical statements below that no production store existed describe the review snapshot. The adapter has scripted tests, and live cases are now defined but have not been executed against PostgreSQL. Capture conversion, delivery replay, authenticated producer ingress, and MCP runtime selection remain outside that adapter.

- **APPROVED (coordination only, per the shared-storage owner’s instruction supplied for this review):** the workstreams proceed in parallel and jointly review interface boundaries; neither workstream unilaterally decides candidate identity, revisions, replay, authorization, or public contracts.
- **UNRESOLVED (approval status):** no detailed C1 storage, mapping, conversion, replay, or additive MCP decision was independently verifiable in the inspected repository. The evidence-linked-memory document calls its reconciliation section a design proposal and says no schema or adapter contract has been approved (`docs/evidence-linked-memory-contract.md:3-19,125-134`).

## Shared-storage-side decision register

The following proposals capture the shared-storage perspective supplied for the 2026-10-18 review. Their `PROPOSED` and `Open` labels are historical: the decision record linked above resolves these items for the first PostgreSQL Evidence/Knowledge implementation. Keep this register as review context, not as a current blocker list.

- **ST-REV-01 — Candidate and revision identity** — **PROPOSED; needs joint discussion.** Owner: Capture/Resurrection and Storage. Keep candidate, immutable revision, delivery, conversion operation, and knowledge identities distinct. Bind approval to the exact reviewed revision and retain a durable link from the resulting knowledge record to that revision and conversion. Open: producer ID guarantees, revision format, and how corrections relate to already-converted knowledge.
- **ST-REV-02 — Conversion and idempotency** — **PROPOSED; needs Storage design and joint acceptance.** Owner: Storage, with joint acceptance criteria. For an agreed operation identity, an authorized exact retry should return the original durable knowledge/evidence IDs; a changed canonical payload should conflict without mutation; concurrent identical attempts should converge on one durable outcome. Open: uniqueness key, canonical payload fields, and partial-failure recovery.
- **ST-REV-03 — Authorization and transaction boundary** — **PROPOSED; needs joint discussion.** Owner: Storage and Product/Security. Authenticate and authorize at the trusted application boundary, verify approval for the exact revision, and define how authorization and writes share a consistency boundary, including concurrent revocation. Open: local operator approval policy, hosted identity and permissions, and revocation ordering.
- **ST-REV-04 — Knowledge-state read interface** — **PROPOSED boundary.** Owner: Retrieval/Resurrection defines domain needs; Storage implements the durable adapter. Provide a project-scoped, authorized read port for knowledge, evidence, provenance, and reconstruction relationships, without exposing database-specific details. Open: query/filter shapes, pagination, consistency, and unavailable evidence behavior.
- **ST-REV-05 — MCP compatibility** — **PROPOSED default.** Owner: MCP/API and both workstreams. Keep knowledge-state enrichment internal while its consumer need is unsettled; public MCP response changes require explicit review and compatibility coverage. Open: which consumer-facing feature justifies a public response change.

## 1. Knowledge-state read port

### Existing shapes

- **EXISTING — `KnowledgeBundle`:** `orbit.evidence.model.KnowledgeBundle` contains one `KnowledgeRecord` and a tuple of associated `EvidenceRecord` values (`src/orbit/evidence/model.py:110-131`). A record carries `knowledge_id`, project, type, claim, timestamps, verification and lifecycle state, supersession IDs, checker metadata, and optional capture provenance (`:110-125`).
- **EXISTING — evidence fields:** each evidence record has `evidence_id`, project, evidence type, source ID, locator, revision, content hash, capture time, reference availability, verification status, and optional check fields (`src/orbit/evidence/model.py:58-72`).
- **EXISTING — capture provenance:** the optional `CaptureKnowledgeProvenance` carries candidate/project IDs, category, certainty, review and candidate-verification status, source references, candidate timestamps, original claim, reviewer label/time/note, and conflict candidate IDs (`src/orbit/evidence/model.py:75-107`). It has no `provenance_version` or approved-candidate revision ID.
- **EXISTING — Retrieval port:** `KnowledgeStateReader.get_for_candidate(project_id, record_kind, record_id) -> KnowledgeBundle | None` is the current optional protocol (`src/orbit/retrieval/knowledge_state.py:254-259`). Its lookup key is a Retrieval identity plus project scope. It is a direct-reader seam, not a storage interface.
- **EXISTING — Retrieval envelope:** `KnowledgeStateEnvelope` has `schema_version: 1`, knowledge ID/project/type/claim/status/timestamps/supersession/check fields, evidence metadata, capture provenance, and optional reconstruction assessment (`src/orbit/retrieval/knowledge_state.py:118-145`). The mapper preserves the modeled evidence and provenance values and rejects project mismatches (`:148-225`).

### Lookup outcomes and limitations

| Lookup case | Current behavior | Label and limitation |
|---|---|---|
| Missing mapping | Reader returns `None`; Retrieval omits `knowledge_state`. A default-constructed service also has no reader. | **EXISTING.** Omission is current seam behavior, not an approved durable missing-record policy. |
| Unique mapping | Reader returns one bundle; Retrieval maps and attaches its envelope. | **EXISTING.** No production lookup currently supplies this mapping. |
| Ambiguous mapping | The protocol has no multiple-result or ambiguity value. | **UNRESOLVED.** Do not select the first result or invent an error/result shape. |
| Cross-project result | The envelope mapper rejects evidence/provenance outside the record project; Retrieval rejects a bundle outside the requested project. | **EXISTING.** These are integrity guards after a reader returns data, not authorization for the read. |

Source: `src/orbit/retrieval/service.py:349-363,479-486`; `src/orbit/retrieval/knowledge_state.py:148-165`; `docs/retrieval_integration.md:73-81,151-169`.

The integration notes state that no supported mapping currently relates `(project_id, record_kind, record_id)` to `knowledge_id`; neither a Retrieval `record_id` nor `source_id` is guaranteed to equal a Capture candidate ID or a knowledge ID (`docs/retrieval_integration.md:139-160`). The current `EvidenceLinkedMemoryStore` supports project-scoped knowledge-by-ID and evidence-by-knowledge reads, but has no candidate mapping or project listing operation (`src/orbit/evidence/service.py:66-109`). The `MemoryStore` used by the application covers messages and decisions only (`src/orbit/storage/protocol.py:47-69`). The inspected `src/` tree has no production implementation of `EvidenceLinkedMemoryStore` (`docs/retrieval_integration.md:130-147`).

### Evidence, provenance, authorization, and budget implications

- **EXISTING:** the envelope includes evidence IDs/types/source IDs, locator, revision, content hash, captured time, reference and verification status, and checker metadata; capture provenance includes typed source references and conflict candidate IDs (`src/orbit/retrieval/knowledge_state.py:26-57,60-115,192-225`). Retrieval must not derive missing fields.
- **EXISTING:** the reader receives a project string, not a principal or authorized scope. The integration notes explicitly say project is lookup scope, not proof of access (`docs/retrieval_integration.md:73-81`). A production adapter must authorize and scope mapping, record, and evidence reads before returning data.
- **EXISTING:** an injected envelope is included in full JSONL serialization and the context budget calculation (`src/orbit/retrieval/context_builder.py:65-78,123-166`). The default counter is a UTF-8 byte estimate, not model tokenization (`:28-36`). **UNRESOLVED:** whether/how the public client receives the envelope and which budget unit is authoritative for such output.
- **EXISTING:** the default Retrieval wrappers construct `RetrievalService` without a reader; the MCP service also constructs it without a reader and its public item serializer does not include `knowledge_state` (`src/orbit/retrieval/service.py:780-803`; `src/orbit/mcp_server/retrieval_services.py:63-89,100-159`). `search_chat_history` returns message search results without knowledge-state enrichment (`src/orbit/retrieval/service.py:319-345`). The current public MCP schema is a separate compatibility boundary (`docs/interfaces.md:1-9,131-168`). Any additive MCP exposure is **UNRESOLVED** and requires MCP-owner review.

**PROPOSED — production connection work, subject to joint review:** Storage supplies an authorized, project-scoped mapping/read implementation behind the existing Retrieval seam, and a scoped list/read implementation for Reconstruction. Before implementation, the owners must agree missing, unique, ambiguous, and cross-project semantics, the authorized-scope handoff, evidence/provenance round-trip behavior, and whether public MCP output changes. No adapter or interface change is made by this review.

## 2. Candidate and revision contract

### Existing fields and identity relationships

| Concept | Existing source fields/behavior | Boundary status |
|---|---|---|
| Capture input/source | `CaptureInput` has `project_id`, `source_type`, `source_id`, content, optional source timestamp/revision, and evidence (`src/orbit/capture/model.py:50-60`). `CaptureSourceReference` carries source type/ID, source timestamp/revision, and locator (`:63-70`). | **EXISTING.** No separate producer ID/namespace field is modeled; the intended relationship between `source_id` and producer identity is **UNRESOLVED**. |
| Candidate | `KnowledgeCandidate` has candidate ID, project, category, claim, source references/evidence, review status, certainty, candidate verification status, created/updated times, conflict key/IDs, original claim, and reviewer fields (`src/orbit/capture/model.py:72-90`). | **EXISTING.** No `producer_id`, candidate revision ID, or durable conversion ID is modeled. Their intended meanings are **UNRESOLVED**. |
| Candidate ID generation | `_candidate_identity` hashes project, category, normalized claim, and conflict key (`src/orbit/capture/service.py:662-676`). | **EXISTING implementation only.** This is not evidence of jointly approved identity semantics; do not change it here. |
| Candidate edits/review | Edit/review code accepts pending or needs-evidence candidates; approved/rejected candidates are terminal in the in-memory workflow (`src/orbit/capture/service.py:286-379`). | **EXISTING** domain behavior. The system has no approved revision identity, and no durable compare-and-set behavior. |
| Conversion relationship | Mapping helpers require an approved, same-project, unverified candidate. `capture_provenance_for` carries its candidate ID and review/source metadata; C1 generates a separate `knowledge_id` (`src/orbit/capture/service.py:586-659`; `src/orbit/evidence/service.py:143-172`). | **EXISTING** conversion metadata helpers, not a durable conversion operation. Which candidate snapshot/revision is converted is **UNRESOLVED**. |
| Evidence identity/time | `EvidenceSource` has evidence type, source ID, locator, revision, and hash; `EvidenceRecord` adds evidence ID, project, capture time, availability, verification, and check metadata (`src/orbit/evidence/model.py:47-72`). | **EXISTING.** Capture source occurrence time is held separately as `occurred_at`; it is not an `EvidenceRecord` field (`:75-83`). |
| Conflict links | Candidate and provenance models hold `conflicts_with: tuple[str, ...]` of candidate IDs; there is no edge ID, endpoint revision, or resolution-event model (`src/orbit/capture/model.py:85-90`; `src/orbit/evidence/model.py:86-107`). | **EXISTING** one-sided reference representation. Symmetry, pending-endpoint registration, link identity, and resolution history are **UNRESOLVED**. |

The conversion helper maps source origins to `EvidenceType.OTHER` and packs source type with locator for the evidence projection, while `capture_provenance_for` separately retains source type, occurrence time, revision, and locator (`src/orbit/capture/service.py:562-619`). The reconciliation review recommends typed, separate evidence fields and a positive provenance version, but explicitly labels the section a design proposal (`docs/evidence-linked-memory-contract.md:125-134,194-224`). That recommendation is **PROPOSED**, not an approved field change.

### Existing conflict behavior and open identity/revision decisions

- **EXISTING:** Capture conflict discovery updates nonterminal in-memory candidates; it does not rewrite already approved/rejected candidates (`src/orbit/capture/service.py:467-508`).
- **EXISTING:** Reconstruction can infer a conflict classification for available endpoints from a one-sided `conflicts_with` reference. If an endpoint is absent from `list_records()`, it preserves the reference, does not fabricate the other record, and does not report a resolution (`src/orbit/project_state/reconstructor.py:373-410`; `tests/project_state/test_reconstructor.py:292-360`). `list_records()` has no completeness signal, so absent is not confirmed missing.
- **PROPOSED in the storage reconciliation review:** candidate identity may include a producer scope; a changed reviewed payload may require a new identity or explicit revision; conflict links may be symmetric and pending until the other candidate is converted. None is **APPROVED** (`docs/evidence-linked-memory-contract.md:157-188,226-247`).
- **UNRESOLVED:** whether producer belongs in identity, whether IDs are globally or producer scoped, how an approved edit is represented, what revision means, how a dangling reference is later resolved to a knowledge record, and which authorized operation records a conflict resolution. Do not choose these in Retrieval or Storage independently.

## 3. Conversion and replay port

### Existing conversion/storage boundary

- **EXISTING:** `knowledge_type_for_candidate`, `evidence_sources_for`, and `capture_provenance_for` map candidate values in memory. They do not persist a candidate or execute a coordinated conversion (`src/orbit/capture/service.py:562-641`).
- **EXISTING:** `EvidenceLinkedMemoryService.create_knowledge(scope, type, claim, evidence_sources, capture_provenance=...)` authorizes the scope, creates a generated knowledge ID and evidence records, calls `store.create_knowledge`, and returns a `KnowledgeBundle` (`src/orbit/evidence/service.py:143-172`). This generic C1 create operation does not accept a candidate ID/revision as a first-class request, record a conversion/replay result, or create conflict links.
- **EXISTING:** the `EvidenceLinkedMemoryStore` protocol requires atomic creation of one knowledge record with initial evidence, atomic supersession, and idempotent evidence attachment for the documented evidence identity tuple (`src/orbit/evidence/service.py:66-107`). This is a domain-port requirement; no production adapter implements it. It does not establish atomicity across candidate review state, all conflicts, replay state, or hosted authorization.
- **EXISTING:** Capture uses `InMemoryCaptureRepository` and binds its queue to one project (`src/orbit/capture/service.py:121-165`). This is process-local state, not durable review state or a transaction boundary.

### Proposed port responsibilities, not a method signature

- **PROPOSED:** the conversion boundary should receive enough information to validate trusted project/action scope, the reviewed candidate state, evidence/provenance, and declared conflict references as one coherent request. Exact fields, version selectors, and identity keys remain **UNRESOLVED**.
- **PROPOSED:** its outcome should let callers distinguish persistence success, any recognized replay, payload mismatch, stale review, validation/authorization denial, and persistence failure, and return the durable IDs needed by Retrieval/Reconstruction. The actual enum/result shape and which outcomes exist remain **UNRESOLVED**.
- **PROPOSED:** if the team requires a conversion replay record, its lifecycle and relationship to knowledge/evidence/conflict writes must be specified with the transaction boundary. No replay-record model or port exists today.

### Replay and concurrency cases requiring a joint contract

| Scenario | Existing evidence | Required contract/test decision |
|---|---|---|
| Identical retry | Generic evidence attachment has a documented idempotency tuple; no candidate conversion replay exists. | **UNRESOLVED:** key, equality fields, returned IDs/result, and whether replay state is stored. Test only after these are agreed. |
| Changed payload on same candidate/revision | No durable conversion implementation. | **UNRESOLVED:** compare fields/canonicalization and whether to reject, version, or update. No silent-upsert assumption. |
| Concurrent identical attempts | No durable capture/conversion arbiter or production adapter. | **UNRESOLVED:** uniqueness scope, winner/loser result, lock/retry strategy, and one-result guarantee. |
| Partial write failure | Existing port documents atomic `create_knowledge`/initial evidence and supersession; not the whole conversion graph. | **UNRESOLVED:** operation boundary and rollback/uncertain-commit behavior for knowledge, all evidence, conflicts, and replay metadata. Inject failure at every agreed write stage. |
| Candidate edit/rejection racing conversion | Helpers validate the supplied candidate snapshot as approved and unverified; there is no persisted review version or compare-and-set (`src/orbit/capture/service.py:644-659`). | **UNRESOLVED:** how conversion proves approval is current at commit time. Test stale approval/rejection races after choosing a revision/transaction rule. |

**UNRESOLVED (approval status):** no replay or conversion semantics above were explicitly approved in the inspected contract documents. `docs/evidence-linked-memory-contract.md:157-268` presents identity, retry, conflict, and transaction rules as recommendations for team agreement.

## 4. Authorization and transaction boundary

### Existing domain and runtime paths

- **EXISTING — C1 domain check:** `EvidenceLinkedMemoryService` is constructed with a `ProjectAuthorizer` and a principal fixed on the service. Each operation calls `_require_scope`, which reauthorizes the project/action before store access (`src/orbit/evidence/service.py:120-141,412-426`). The supplied `AuthorizedProject` is a selector, not transport authentication; factory/principal trust remains an application responsibility (`docs/evidence-linked-memory-contract.md:11-19`).
- **EXISTING — local runtime:** the current stdio MCP server sets `_stdio_principal()` to `None`, loads a process-level `ProjectAccessPolicy`, and authorizes before its current persistence/retrieval calls (`src/orbit/mcp_server/server.py:81-122`; `src/orbit/mcp_server/authorization.py:73-128`). This is not per-client identity and does not wire C1.
- **EXISTING foundation, not live C1 runtime:** `HostedScopeResolver` accepts a `VerifiedPrincipal`, workspace binding, request authorization session, project key, and action. It checks identity status, workspace/project lifecycle, membership/grant, and permission, then issues a scoped project value (`src/orbit/mcp_server/hosted_scope.py:234-319`). `execute` uses the same session for authorization and the supplied operation; `PostgresHostedAuthorizationSessionFactory.execute_authorized` owns one transaction (`:353-370`; `src/orbit/postgres/authorization.py:267-287`). The hosted-identity document says this foundation is not connected to MCP, no token authenticator is integrated, and no C1 adapter accepts this scope (`docs/hosted-identity-scope.md:3-10,104-115`).
- **EXISTING test limits:** hosted scope tests use synthetic principals and fake/scripted authorization sessions; they are not live PostgreSQL, authenticator, transport, or concurrent-revocation tests (`docs/hosted-identity-scope.md:104-112`).
- **EXISTING Reconstruction seam:** `ReadOnlyKnowledgeSource.list_records()` takes no project, principal, revision, or snapshot argument. Its docstring says an implementation must already be authorized for one project; `ProjectStateReconstructor` accepts a repository path and project string without authenticating or binding them (`src/orbit/project_state/reconstructor.py:33-40,53-79`). Git content is read from the requested commit, but memory records are loaded separately by `list_records()` (`:81-103,360-379`).

### Boundary requirements to decide

- **PROPOSED:** every C1 read/write and retrieval/reconstruction memory read must use a trusted principal and server-issued, project-scoped authorization result; a project string and reviewer label are not credentials.
- **PROPOSED:** membership/revocation check and protected read/write should share a transaction or other explicitly agreed consistency mechanism. The hosted authorization foundation documents row locks and transaction ordering for existing hosted operations, but C1 has no adapter using them (`docs/hosted-identity-scope.md:45-60`). Do not describe this as C1 integration.
- **UNRESOLVED:** exact read/write actions for mapping, candidate review/conversion, evidence resolution, conflict updates, Retrieval reads, and Reconstruction snapshots; revocation linearization point; whether a combined Git revision and memory snapshot is consistent; and how auth failures map to existing public errors.
- **EXISTING:** current public MCP errors and output schemas are documented; changing them is outside this boundary review (`docs/interfaces.md:11-20,131-186`).

The shared-storage owner must supply or jointly approve: an authorized project-scope/session handoff accepted by the C1 adapter; project-constrained mapping and record/evidence reads; the transaction/session contract for multi-record conversion; the identity/revision/replay and conflict decisions; and storage conformance tests. Retrieval/Resurrection must supply the candidate/provenance/evidence requirements and acceptance cases without choosing those storage policies.

## Decision table

Individual owner names were not present in the inspected sources. “Owner” below means the responsible workstream; approval is joint where shown.

| Decision | Owner | Dependency | Acceptance test |
|---|---|---|---|
| Candidate lookup key and zero/one/many/foreign-project outcomes | Shared Storage + Retrieval | Approved mapping and cardinality contract | Mapping tests prove missing, unique, ambiguous, and cross-project behavior; no arbitrary selection. |
| Producer/candidate/approved-revision identity and edit behavior | Capture + Shared Storage; joint review | Candidate model and producer guarantees; no final identity selected | Approved candidate snapshot, post-approval edit, and stale review/conversion race cases. |
| Evidence/provenance representation and version compatibility | Capture + Shared Storage | Typed source fields, occurrence timestamps, legacy/unknown versions | Durable round-trip of every modeled field or explicit approved rejection; compare retrieval envelope. |
| Conversion request/outcome and replay policy | Capture + Shared Storage | Identity, canonicalization, retry-result decision | Identical, changed-payload, and concurrent attempts with final durable state and caller outcomes. |
| Atomicity of knowledge, evidence, conflicts, and replay state | Shared Storage; joint review | Transaction/session API and agreed operation boundary | Fault injection at each write point; verify agreed rollback and uncertain-commit behavior. |
| Conflict edge/pending endpoint/resolution lifecycle | Capture + Shared Storage + Reconstruction | Symmetry, endpoint namespace, resolution event contract | Dangling endpoint stays unresolved; later endpoint links without fabricated resolution; explicit resolution audit test. |
| Trusted principal, membership, revocation, and project isolation | Runtime/Auth + Shared Storage + Retrieval | Authenticator and authorized session/scope handoff | Revoke between successive reads; deny before data serialization; cross-project ID never leaks. |
| MCP exposure and context budget | MCP + Retrieval + consuming client | Public compatibility/version decision; counter/unit | Legacy shape remains compatible; approved state subset is counted in actual serialized context. |
| Pinned Git plus knowledge snapshot | Reconstruction + Shared Storage + Auth | Project-to-repository trust mapping and snapshot-consistency rule | Pinned Git revision excludes worktree changes and is paired with the authorized snapshot version. |

## Questions for joint review

1. What authoritative relation maps a Retrieval `(project, kind, record)` identity to a C1 knowledge record, and what are the observable missing, ambiguous, and cross-project outcomes?
2. Does producer identity participate in candidate identity? How are candidate identity, approved revision, source event, and knowledge-record identity related? What happens when an approved candidate is revised?
3. Which candidate/review/evidence/provenance fields define a replay match? What are the caller-visible results for identical, changed-payload, concurrent, and uncertain-commit cases?
4. Does conversion atomically include knowledge, all initial evidence associations, conflict links, and any replay metadata? Which failures must roll back all of them?
5. Which evidence fields remain typed and separate, how is `provenance_version` introduced, and what do readers do with unsupported or legacy values?
6. Are conflict links symmetric? How are missing endpoints represented and linked later? Which explicit resolution outcomes and audit events are allowed?
7. Which trusted principal and project-scope value reaches each C1 operation? How are membership revocation and authorization/data consistency ordered in the production transaction?
8. Is knowledge state exposed through MCP `items`, `retrieve_context`, `search_chat_history`, or only a negotiated/versioned subset? Which budget unit/counter applies?
9. What consistency guarantee binds the pinned Git revision to the project-scoped knowledge snapshot used by Reconstruction?

## Smallest safe next implementation after review

After joint decisions, start with a **read-only, storage-owned adapter** behind the existing Retrieval reader boundary and the approved Reconstruction read boundary. Add adapter contract tests for authorized project scope, mapping cardinality, evidence/provenance preservation, and retrieval ordering/legacy behavior. Keep the existing public MCP shape unchanged unless its owner approves an explicit versioned change. Defer a durable conversion/replay port until candidate/revision identity, payload equality, transaction, conflict, and stale-review decisions are ratified. No interface or implementation change is proposed by this document.
