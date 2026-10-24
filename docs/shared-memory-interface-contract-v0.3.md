# Celestin Shared Memory Interface Contract v0.3

> **Authority and status:** The requester-authorized implementation baseline for the first PostgreSQL Evidence/Knowledge work is recorded in [`SHARED_MEMORY_INTERFACE_CONTRACT_V0.3_DECISIONS.md`](SHARED_MEMORY_INTERFACE_CONTRACT_V0.3_DECISIONS.md). This file remains a historical source-backed prototype inventory. Its `PROPOSED` and `OPEN DECISION` labels preserve the earlier review state and are superseded only where the decision record explicitly addresses the same question. Producer fields in that record are requirements, not proof that an upstream producer already supplies them. The decision record does not implement persistence or imply teammate acceptance. Any behavior or implementation detail not specified there remains unresolved; do not infer schema or runtime behavior from this draft.

**Status:** Historical source-backed design draft for review. It describes local prototype behavior and the earlier target proposals. The final decision record governs the initial implementation requirements; this document is not a persistence schema, a guarantee that producers emit the required fields, or proof that Capture-to-Evidence storage is implemented.

**Baseline inspected (historical snapshot):** branch `codex/celestin-core-memory-platform`, commit `eff1b10f926e5f13ab57822c2cbec30330af5160`.

## 1. Scope and terminology

This contract covers the boundary from captured candidate content through review, evidence association, durable knowledge conversion, and later retrieval/reconstruction.

- **Candidate:** a proposed claim awaiting review. Current implementation: `KnowledgeCandidate`.
- **Candidate identity:** identifies a logical candidate independently of edits. Current code has no identity with that guarantee; its ID is a claim fingerprint.
- **Candidate revision:** an immutable snapshot of a candidate's content and provenance. Current code has no revision entity.
- **Delivery identity:** identifies one producer submission so retries can be recognized independently of candidate identity. Current Capture input has no delivery ID.
- **Source identity:** identifies a source within an agreed source/provider namespace. Current records contain source type and source ID strings, but do not define a producer-wide namespace.
- **Occurrence:** one appearance of a source in a candidate's support. Current code has source-reference values and locators, but no stable occurrence ID.
- **Evidence reference:** a typed pointer to a source, without copying the source's contents.
- **Knowledge record:** a persisted claim with evidence, verification, and lifecycle metadata.
- **Conversion:** the operation that creates a knowledge record and its provenance/associations from an approved candidate revision.
- **Authenticated principal:** identity established by a trusted authentication boundary. A display label such as `reviewer_id` is not such proof.

Labels used throughout:

- **IMPLEMENTED** — directly present in current source. If it is prototype-only or test-only, that scope is stated.
- **PROPOSED** — a recommended target behavior that is not implemented or approved.
- **OPEN DECISION** — needs explicit agreement from the producer, product/security, or storage owners.

## 2. Existing contract and source map

No document titled “Shared Memory Interface Contract v0.2” was found under `docs/`. `docs/interfaces.md` defines public MCP tool contracts; it is not the shared-memory domain contract. The closest existing document is `docs/evidence-linked-memory-contract.md`. Its opening section describes the storage-neutral Evidence service prototype, while its later “storage-contract reconciliation review” explicitly calls its recommendations a proposal for agreement.

The current source boundaries are:

| Area | Source symbols | Current boundary |
| --- | --- | --- |
| Capture records | `CaptureSourceType`, `CaptureEvent`, `CaptureInput`, `CaptureSourceReference`, `KnowledgeCandidate`, `CaptureResult` in `src/orbit/capture/model.py` | Frozen in-process data records. |
| Capture behavior | `CaptureCandidateRepository`, `InMemoryCaptureRepository`, `KnowledgeCapturePipeline` in `src/orbit/capture/service.py` | Explicit-input local prototype; its repository is process-local. |
| Evidence/knowledge records | `EvidenceSource`, `EvidenceRecord`, `CaptureSourceProvenance`, `CaptureKnowledgeProvenance`, `KnowledgeRecord`, `KnowledgeBundle` in `src/orbit/evidence/model.py` | Storage-neutral domain records. |
| Evidence service | `EvidenceLinkedMemoryStore`, `EvidenceReferenceResolver`, `EvidenceLinkedMemoryService` in `src/orbit/evidence/service.py` | At this document's historical baseline, only the domain service and protocols were present. A request-bound PostgreSQL store now implements the port in `src/orbit/evidence/postgres.py`; it does not implement Capture conversion or select a backend for MCP. |
| Existing persistence | `MemoryStore` in `src/orbit/storage/protocol.py`; `SQLiteMemoryStore` and `PostgresMemoryStore` | Message and decision persistence only, not candidate or Evidence persistence. |
| Reconstruction | `ProjectStateReconstructor._read_memory` in `src/orbit/project_state/reconstructor.py` | Consumes supplied `KnowledgeBundle` values, scopes them to a project, and derives a reconstruction assessment. |
| Retrieval representation | `KnowledgeStateEnvelope`, `KnowledgeStateReader` in `src/orbit/retrieval/knowledge_state.py` | Version-1 supplemental serialization/read seam; not a canonical conversion payload or persistence format. |

The producer-side Resurrection implementation or versioned producer contract is not present in this checkout. Local Capture examples and tests do not establish guarantees for an external producer.

## 3. Current records and lifecycle

### 3.1 Capture records

**IMPLEMENTED — `src/orbit/capture/model.py`:**

- `CaptureSourceType`: `conversation_summary` or `execution_report`. These are source-kind values, not a producer namespace.
- `CaptureEvent`: `category`, `claim`, `evidence=()`, `conflict_key=None`, `locator=None`.
- `CaptureInput`: `project_id`, `source_type`, `source_id`, `content` (`str` or tuple of events), `source_timestamp=None`, `source_revision=None`, `evidence=()`.
- `CaptureSourceReference`: source type/ID and optional timestamp, revision, locator.
- `KnowledgeCandidate`: candidate/project IDs, category, claim, tuples of source references and evidence, review status, certainty, verification status, creation/update timestamps, optional conflict key, `conflicts_with=()`, original claim, reviewer label/time/note.
- `ReviewStatus`: pending review, approved, rejected, needs evidence. New candidates start pending and unverified in `KnowledgeCapturePipeline.capture`.

**IMPLEMENTED — `src/orbit/capture/service.py`:**

- `InMemoryCaptureRepository` keys current values by `(project_id, candidate_id)` and retains them only in process memory.
- `_candidate_identity` hashes project, category, NFKC/case-folded/whitespace-collapsed claim, and normalized conflict key; it returns a truncated SHA-256-based `capture-…` ID. This is a local fingerprint, not a producer-issued identity or revision.
- Pending/needs-evidence repeats with that fingerprint merge source and evidence values. Exact source/evidence values are deduplicated and sorted. Approved/rejected candidates are not mutated by later input; exact retries return the stored object and changed metadata is suppressed. A materially different claim receives a different local fingerprint and starts pending rather than inheriting review.
- `edit_candidate` changes the existing object in place, retains the ID, and stores only the first `original_claim`. It does not append a revision or snapshot.
- `approve`, `reject`, and `request_evidence` accept a string `reviewer_id`. `_review` allows transitions from pending or needs-evidence and records one current reviewer/time/note tuple. There is no approval event history, revision binding, or Capture authentication/authorization check.
- Capture's project binding is a data-scope check, not caller authentication. The module documentation says an application caller must obtain trusted project scope before hosted use.

**OPEN DECISION:** Whether a producer identity/candidate ID is available, stable across edits, and namespaced by producer cannot be determined from this local prototype.

### 3.2 Evidence and knowledge records

**IMPLEMENTED — `src/orbit/evidence/model.py`:**

- `EvidenceSource`: `evidence_type`, `source_id`, optional `locator`, `revision`, and `content_hash`.
- `EvidenceRecord`: evidence ID, project, typed source ID, locator/revision/hash, `captured_at`, reference status, verification status (default unverified), and optional checker metadata.
- `CaptureSourceProvenance`: source type/ID and optional occurrence time, revision, and locator.
- `CaptureKnowledgeProvenance`: candidate/project identity, category/certainty, review and candidate-verification states, a tuple of source references, candidate creation/update times, and optional original claim, reviewer label/time/note, and `conflicts_with=()`.
- `KnowledgeRecord`: knowledge ID, project/type/claim, creation/update timestamps, verification and lifecycle states, optional predecessor/successor IDs and checker metadata, and optional capture provenance.
- `KnowledgeBundle`: a knowledge record plus a tuple of Evidence records.

`KnowledgeType`, `EvidenceType`, `ReferenceStatus`, `VerificationStatus`, and `LifecycleStatus` are enums in the same module. Evidence availability, evidence verification, knowledge verification, candidate review, and knowledge lifecycle are separate concepts in source.

### 3.3 Mapping and service behavior

**IMPLEMENTED — `evidence_sources_for`, `capture_provenance_for`, and `knowledge_type_for_candidate` in `src/orbit/capture/service.py`:**

- Mapping requires an approved, unverified candidate whose project matches the supplied target project. This does not authenticate the candidate or reviewer.
- Candidate source references map to `EvidenceType.OTHER`; source type is encoded into the Evidence locator, source revision is copied, and source timestamp remains in capture provenance. Candidate-level evidence is included too.
- Supported category mappings are explicit; unmapped categories raise a validation error.

**IMPLEMENTED — `EvidenceLinkedMemoryService` in `src/orbit/evidence/service.py`:**

- It reauthorizes each supplied project/action selector through its injected authorizer and fixed service principal. The principal constructor argument is optional; the trusted application boundary is responsible for providing it. This is not a transport authenticator.
- `create_knowledge` makes new UUIDs for the knowledge record and each unique Evidence reference, resolves each reference, rejects a confirmed missing reference, preserves unresolved status, then calls `store.create_knowledge` once.
- Evidence references are deduplicated by `(type, source_id, locator, revision, content_hash)` before EvidenceRecord creation.
- The service accepts capture provenance only when it matches the project, says `approved`, includes reviewer label/time, remains unverified, and each source reference has a matching mapped Evidence source. These are field checks; a caller can construct the dataclasses, so they are not proof of an authoritative review.
- The service has no candidate-conversion/replay method. Calling `create_knowledge` again generates new IDs.

### 3.4 Storage and tests

**IMPLEMENTED — existing storage:** `MemoryStore` in `src/orbit/storage/protocol.py` covers inserting messages, saving decisions, and updating decision status. `SQLiteMemoryStore` and `PostgresMemoryStore` implement that port. Neither stores Capture candidates, Evidence records, capture provenance, or conflicts. No production `EvidenceLinkedMemoryStore` implementation was found in `src`.

**PROPOSED — current Evidence storage port:** `EvidenceLinkedMemoryStore` specifies atomic knowledge-plus-initial-evidence creation and supersession, project-scoped reads/updates, and idempotent evidence attachment. Those properties are protocol requirements, not behavior demonstrated by a durable adapter.

Test evidence is deliberately narrower than production guarantees:

- `tests/capture/test_pipeline.py`: `test_repeated_and_normalized_duplicate_input_is_idempotent`, `test_approved_candidate_identical_retry_preserves_review_and_is_idempotent`, `test_review_edit_approval_rejection_and_needs_evidence_are_scoped_and_unverified`, and the conflict tests exercise the in-memory Capture pipeline.
- `tests/evidence/test_service.py`: `FakeEvidenceStore` backs service tests, including `test_association_is_idempotent_at_the_storage_port_boundary`, project authorization checks, source resolution, and supersession. The fake is not a durable store.
- `tests/integration/test_capability_flow.py`: `MemoryStoreDouble` and `_persist_approved_decision` are explicitly test-only; `test_approved_uncertain_candidate_flows_through_capabilities_1_2_and_4` does not prove a production adapter exists.
- `tests/project_state/test_reconstructor.py`: `test_capture_conflict_links_mark_both_approved_claims_unresolved` and `test_dangling_capture_conflict_stays_unresolved_without_fabricating_counterpart` verify reconstruction of supplied provenance.
- `tests/retrieval/test_knowledge_state.py`: serialization tests verify a version-1 retrieval envelope, not canonical storage bytes or conversion hashing.

## 4. Identity, revision, delivery, and occurrences

| Concept | Current state | v0.3 target / status |
| --- | --- | --- |
| Candidate identity | **IMPLEMENTED:** local fingerprint of project/category/normalized claim/conflict key. Claim changes can change the ID. | **PROPOSED:** stable `(project, producer_namespace, producer_candidate_id)` identity independent of content and revision. **OPEN DECISION:** producer must confirm whether it supplies these values and their uniqueness scope. |
| Candidate revision | **IMPLEMENTED:** none; edits replace the current in-memory value. | **PROPOSED:** immutable revision/snapshot ID or monotonic revision per candidate. Corrections append a new revision; do not overwrite an approved snapshot. **OPEN DECISION:** producer must define revision ordering and correction semantics. |
| Delivery identity | **IMPLEMENTED:** none in `CaptureInput`. Local content fingerprint is not a request key. | **PROPOSED:** producer-issued delivery/request ID, unique within project and producer namespace, distinct from candidate identity. Same delivery key with changed payload is a conflict. **OPEN DECISION:** producer must guarantee retry stability. |
| Source identity | **IMPLEMENTED:** source kind and opaque string source ID. | **PROPOSED:** qualify IDs by source/provider namespace and project where appropriate. **OPEN DECISION:** producers must define ID namespace, stability, and whether a message ID refers to a message, conversation, or other source object. |
| Occurrence identity | **IMPLEMENTED:** reference fields plus optional locator; events can receive `event:N`/`line:N`. Exact-equal source references collapse and remaining references sort. | **PROPOSED:** stable occurrence ID and ordinal/order, preserving repeated appearances even when source metadata matches. **OPEN DECISION:** producer must state whether occurrence IDs/order are supplied or assigned at ingestion. |
| Occurrence/evidence link | **IMPLEMENTED:** candidate-level evidence tuple and capture source-reference tuple are separate; current mapper does not preserve a per-occurrence edge. | **PROPOSED:** preserve typed evidence references and explicit occurrence-to-evidence associations separately from occurrence identity and evidence identity. Multiple occurrences may link to one deduplicated evidence reference; one occurrence may link to multiple evidence references. **OPEN DECISION:** confirm producer's actual link cardinality and fields. |

The proposed association rule avoids inferring source metadata: adapters may normalize only according to an agreed contract, and must not fabricate a revision, timestamp, locator, hash, producer ID, or occurrence ID when the producer omitted it.

## 5. Canonical conversion, retries, and hashes

### 5.1 Required behavior

**PROPOSED:** A conversion is keyed by project, producer namespace, stable candidate ID, and candidate revision. This permits an intentional later correction without overwriting the earlier conversion. Delivery identity is a separate deduplication key for ingestion.

For one conversion key:

1. Authenticate the caller. Treat workspace, project, candidate, and revision values from the request as selectors, not as trusted scope or proof of authority.
2. Resolve the workspace/project binding server-side, then authorize the authenticated principal for the requested action against that resolved scope. This must happen before looking up a conversion or returning replay data.
3. Evaluate authorization and conversion/revocation state at a defined transactionally consistent point. The exact serialization policy for permission revocation remains open; see the Product/security decision register.
4. Load the authoritative candidate snapshot and approval state in the request transaction. If a conversion already exists for that candidate revision, recompute the canonical payload using the stored payload version. Return the original IDs only when the canonical bytes/hash match and current authorization/revocation policy permits operational replay. A mismatch is a typed idempotency conflict; do not overwrite.
5. An exact retry of an earlier successful conversion is distinct from creating a new conversion. A historical conversion may remain stored after its candidate revision is superseded; a new conversion must use the current approved revision. A stale, rejected, or revoked revision cannot create a new conversion.
6. Atomically create knowledge, provenance, occurrences, evidence records/links, conflict references, and the conversion mapping. Return IDs recorded in that transaction.

**PROPOSED:** Keep a historical conversion record for audit independently of whether the normal conversion endpoint may operationally replay it. If approval is revoked, the normal endpoint should return a typed revoked/non-replayable outcome rather than a knowledge bundle; a separately authorized audit path may expose history. An ordinary read started after revocation commits should not return the revoked knowledge.

**OPEN DECISION:** Product/security must confirm the replay response and retrieval visibility rules after revocation. The proposed behavior is not approved policy.

### 5.2 Payload and hash profile

**PROPOSED payload fields:** `payload_version`; project scope; producer namespace; candidate ID and revision; current claim and category; certainty/uncertainty; source occurrences in contract order with source namespace/ID, occurrence ID, ordinal, timestamp, revision, locator, and explicit evidence-reference IDs/values; typed evidence references with optional revision/hash; authored conflict declarations with typed targets; and the approval reference bound to the snapshot. Include only fields whose source contract defines stable semantics.

**PROPOSED exclusions:** resolver-derived availability, current source freshness, read-time lookup results, retrieval ranking, generated database UUIDs, and local wall-clock observation times. These may change without changing approved candidate content and must not silently alter payload equality.

**Historical implementation snapshot (the baseline inspected above):** At that earlier baseline, the local helper used its provisional JSON/hash behavior. That description is retained as history only; it is not the current implementation or the normative profile.

**OPEN DECISION:** The local profile behavior is not approved as the normative producer/storage contract. Before adoption, agree on:

- Object key ordering and UTF-8 encoding.
- Whether strings are preserved byte-for-byte or normalized (recommend preserving claim/source strings; only identifiers with an explicit normalization rule may be normalized).
- `null` versus absent-field semantics and defaults.
- Array semantics: occurrences are ordered by explicit ordinal; set-like fields are sorted only if the producer contract declares order irrelevant.
- Timestamp parsing and canonical representation.
- Hash algorithm and domain/version prefix. SHA-256 is a reasonable proposal, not a current conversion guarantee.
- Version compatibility and behavior when a prior payload version is unavailable.

The final decision record supersedes this historical open-decision text for conversion canonicalization. The local candidate SHA fingerprint and `KnowledgeStateEnvelope(schema_version=1)` are still not canonical conversion payload hashes.

**Current implementation status:** `src/orbit/evidence/canonicalization.py` now implements the final record's `smic-conversion-payload-v1+jcs-rfc8785+sha256` profile with RFC 8785 JCS, NFC normalization, UTC timestamps at six fractional digits, explicit nullable fields, ordinal-ordered occurrences, sorted conflicts, and direct lowercase-hex SHA-256 over canonical UTF-8 bytes. The approved fixed vectors are exercised in `tests/evidence/test_canonicalization.py`. The separate knowledge-content hash remains provisional. Delivery retry equality and candidate-revision payload equality are not implemented; this helper is not wired to Capture, durable persistence, conversion/replay, or Retrieval. A profile change requires a new profile identifier; existing profile semantics must not be silently reinterpreted.

## 6. Approval, correction, revocation, and conflicts

### 6.1 Approval and audit

**IMPLEMENTED:** local review state is a mutable status plus caller-provided reviewer label/time/note; it is not authenticated approval.

**PROPOSED:** Approval must reference an immutable candidate revision and be performed by a transport-authenticated principal with active membership and an explicit project-scoped approval permission. The server supplies the project scope. Store principal ID, action, target revision/hash, decision time, and optional display label/note as separate audit fields. Labels never substitute for principal identity. Authentication and authorization must precede conversion and replay reads.

**OPEN DECISION:** Product/security must select who receives approval permission, how local-only prototype use is distinguished from hosted authenticated use, and whether/how approvals are revoked. Until a trusted local policy exists, the local prototype's `reviewer_id` must not be described as authenticated approval.

### 6.2 Corrections and revocation

**PROPOSED:** A correction creates a new immutable candidate revision, requires approval for that revision, and creates a distinct conversion. If the product considers the new claim a replacement, link the new knowledge record to the prior record through explicit supersession; retain prior knowledge and audit history. Revocation is an append-only event, not deletion or mutation of the approved snapshot.

**OPEN DECISION:** Decide whether a revoked approval immediately removes the associated knowledge from ordinary retrieval, whether old knowledge remains available only historically/audit-wise, and how a corrected revision affects the prior record. Current Capture has no revocation or correction-history operation. Capability 1 has generic knowledge supersession fields, but these are not wired to Capture review.

### 6.3 Conflicts

**IMPLEMENTED:** Capture derives `conflicts_with` from same-category/same-conflict-key candidates with different normalized claims. There is no separate author-declaration entity. Terminal candidates are not rewritten; reconstruction projects one-way links onto available endpoints. Missing targets are preserved as IDs without fabricated counterpart records.

**PROPOSED:** Keep producer-authored declarations distinct from Celestin-derived conflict grouping. A declaration records its declaring candidate revision and a typed target `(producer_namespace, candidate_id[, revision])`; its direction is the authored direction. Reverse lookup may project a symmetric view but must not fabricate a reverse declaration. An unresolved target remains a typed pending reference and can link when that candidate appears. Do not infer cross-producer targets without a namespace.

**OPEN DECISION:** Producer and product owners must decide whether conflicts are declarations, grouping hints, or both; target revision semantics; whether derived grouping is retained; and whether explicit resolution history is in v1. No current service resolves conflicts or chooses a winner. The v1 recommendation is to preserve unresolved declarations and defer resolution events unless a consumer requires them.

## 7. Timestamps and source revisions

**IMPLEMENTED:** Capture accepts an optional ISO timestamp only when it includes a timezone, but retains the supplied text. Capture's own timestamps are emitted in UTC with microseconds. `EvidenceRecord.captured_at` and `KnowledgeRecord` timestamps are service persistence times, distinct from source occurrence time.

**PROPOSED:** Name and store timestamps by meaning: source occurrence time, capture receipt time, approval time, conversion time, and later verification-check time. Require an explicit timezone for supplied instants. Preserve the producer's original value if audit fidelity requires it; use a separately specified normalized UTC instant for ordering/equality.

**OPEN DECISION:** Producer owners must define whether source timestamps are event occurrence, message creation, or delivery time, and whether offsets/precision must round-trip. They must define source revision meaning (immutable source version, sequence, commit, or opaque token) per source type. Current code treats source IDs/revisions as opaque strings and does not impose a shared namespace or ordering.

## 8. Proposed service and repository boundaries

These are target boundaries, not current APIs or final schema commitments.

1. **Trusted request boundary:** authenticate first; construct request-local principal and resolve the requested project/action server-side. Caller-supplied reviewer/project labels are selectors or display provenance only.
2. **Candidate/review repository:** retrieve candidate identity and immutable revisions; append revisions and approval/revocation events; enforce project scope and conditional state transitions. Do not rely on `InMemoryCaptureRepository` for durability or concurrency.
3. **Conversion service:** accept trusted request context and an authoritative candidate revision reference; validate current approval and create a canonical payload using an explicit version.
4. **Transaction-bound shared-memory store:** expose one `convert_approved_candidate` operation that atomically creates or returns the conversion mapping, knowledge record, provenance, occurrence/evidence associations, and conflict declarations. It must enforce uniqueness and compare payload hashes under concurrent retries. It must not open an unrelated connection inside the transaction.
5. **Evidence resolver:** resolve a typed source within authorized project/source scope. Availability does not imply freshness, verification, or truth.
6. **Readers/retrieval:** read knowledge, evidence, provenance, and conflicts using the same project scope; keep approval state, verification state, lifecycle state, and resolver availability distinct.

**PROPOSED transaction invariants:** resolve trusted scope, then authorize before any conversion lookup or replay result. In the conversion transaction, recheck or lock the authoritative candidate revision, approval/revocation state, and the membership/permission facts required by the selected authorization policy; enforce project ownership and source scope; compare an existing conversion's canonical version/hash; create all conversion rows and associations together; rollback all on any failure.

**PROPOSED serialization default:** conversion and permission/approval revocation must contend on shared authoritative state or an equivalent serialization mechanism. If revocation commits before conversion reaches its authorized decision point, conversion is denied. If conversion is serialized first, it may finish; revocation then governs subsequent operations and ordinary reads after the revocation transaction commits. This does not promise cancellation of an already-authorized in-flight transaction. The product/security owners must approve or replace this rule. A unique conversion constraint plus a defined conflict/re-read/compare path is required for concurrent same-key retries; the constraint alone does not define the losing request's behavior.

No final table layout, key encoding, SQLite/PostgreSQL schema, or adapter API is committed by this document.

## 9. Failure cases and acceptance tests

All tests below are requirements for future implementation; none were run for this document.

| Failure/condition | Required test invariant |
| --- | --- |
| Missing/invalid principal or inactive membership | Approval, conversion, and replay return denial before protected data is exposed or modified. |
| Caller forges reviewer label, project, producer, candidate, or role | Trusted scope/identity comes only from the authenticated request boundary; forged selectors cannot authorize another scope. |
| Authenticated active member lacks the explicit approval permission | Approval is denied before candidate state changes or conversion becomes possible. |
| Edit/correction after approval | Prior revision and approval remain unchanged; new revision is pending until separately approved. |
| Exact same delivery retry | Returns the same candidate/delivery result without duplicate occurrences or side effects. |
| Same delivery key with changed bytes | Returns a typed conflict and preserves original data. |
| Exact conversion replay | After authorization and current policy checks, returns original conversion/knowledge/evidence IDs. |
| Same conversion identity with changed canonical payload | Returns a typed conflict; no overwrite or duplicate links. |
| Stale or rejected revision | Cannot create a new conversion; an already stored historical conversion is not overwritten. |
| Approval revocation races conversion | The approved serialization rule determines one outcome; test both commit orders, including the rule for an already-authorized in-flight conversion. |
| Replay after approval revocation | The approved policy determines whether the operational endpoint returns a typed revoked result or another safe response; it must not restore the record to ordinary retrieval. Audit access is tested separately. |
| Ordinary retrieval after revocation commits | New ordinary reads exclude the revoked record if that policy is approved; authorized audit reads retain the required history. |
| Membership/permission revocation races conversion | Test both transaction orderings and verify the approved consistency point; no unauthorized conversion may pass the defined authorization point. |
| Repeated identical-looking source occurrence | Distinct occurrence IDs/order survive; evidence references may be deduplicated only while occurrence links remain distinct. |
| Evidence reference missing/unresolved or metadata incomplete | Missing follows explicit policy; unresolved stays unresolved; absent metadata is not fabricated. |
| Authored/derived conflict or unavailable target | Direction/provenance is retained; missing endpoint stays pending; no winner or reverse declaration is invented. |
| Failure during knowledge, evidence, mapping, or conflict write | All conversion writes rollback together; retry after rollback can complete once. |
| Concurrent same-key conversion | One conversion is committed; contender returns same IDs for equal hash or conflict for unequal hash. |
| Serialization/version change | Golden vectors produce stable bytes/hash; unknown payload versions fail safely rather than silently reinterpreting. |
| Retrieval/reconstruction | Authorized project scope, historical/superseded state, unresolved evidence/conflicts, and audit metadata round-trip without turning approval into verification. |

Test layers must be reported separately: pure model/canonicalization unit tests; scripted repository/transaction tests; SQLite adapter tests; live PostgreSQL tests on an approved disposable database; and real authenticated transport tests. A fake store does not prove database atomicity or authorization integration.

## 10. Decision register

Every item below remains **UNRESOLVED**. Defaults are recommendations marked **PROPOSED**, not owner decisions or approved policy. No named individual owner or approved team decision record was found in the inspected repository; role labels identify the team that needs to answer, not a verified assignment. Until a decision is recorded, do not encode its proposal as a permanent schema constraint or claim a producer guarantee.

### 10.1 Producer / Resurrection decisions

| Decision | Status | PROPOSED default | If left unresolved |
| --- | --- | --- | --- |
| Stable candidate ID and namespace | UNRESOLVED | Producer-issued ID, namespaced by producer and project; stable across candidate edits and revisions. Candidate identity is not the revision. | The adapter cannot reliably match the same logical candidate across updates; project/producer uniqueness and foreign references remain undefined. Do not finalize candidate keys or conversion mapping. |
| Candidate revisions and corrections | UNRESOLVED | Each correction creates an immutable revision/snapshot under the same candidate identity; approval binds to exactly one revision. | Approval could attach to mutable content or corrections could overwrite history. Do not add revision or approval schema. |
| Delivery/request retry identity | UNRESOLVED | Stable producer delivery ID, separate from candidate ID and revision. Identical delivery replay is idempotent; changed bytes for the same delivery ID conflict. | Retries cannot be distinguished from new submissions, risking duplicate candidates or silent payload replacement. Do not promise delivery idempotency. |
| Occurrence identity, order, and duplicates | UNRESOLVED | Give each source occurrence its own stable identity and ordinal; retain duplicate occurrences even if source metadata is equal. Occurrence identity is distinct from evidence-record identity. | The adapter may collapse repeated source occurrences or reorder them, losing provenance. Do not finalize occurrence uniqueness or ordering constraints. |
| Occurrence-to-evidence links and shared evidence | UNRESOLVED | Persist explicit association rows; multiple occurrences may point to one deduplicated evidence reference, and one occurrence may link to multiple references if the producer confirms that cardinality. | The adapter cannot losslessly reconstruct which source supported which occurrence; do not infer links from matching IDs or locators. |
| Source and conflict-target namespaces | UNRESOLVED | Carry an explicit source/provider namespace and producer namespace for candidate conflict targets; keep declaration direction, and preserve unresolved typed targets without fabricating records. | Equal source/candidate strings from different producers may collide or point to the wrong target. Do not finalize cross-producer foreign keys or conflict keys. |
| Timestamp and source-revision semantics | UNRESOLVED | Distinguish occurrence time from delivery/receipt time; require timezone-aware instants while retaining original precision for audit. Treat source revisions as opaque unless the producer defines ordering and meaning per source type. | Freshness, ordering, and audit fields could be misinterpreted. Do not normalize timestamps or compare source revisions as ordered values without a source contract. |

### 10.2 Product / security decisions

| Decision | Status | PROPOSED default | If left unresolved |
| --- | --- | --- | --- |
| Approval authority and local/hosted identity | UNRESOLVED | Hosted approval requires an authenticated principal, active project membership, and explicit project-scoped approval permission. `reviewer_id` remains display/provenance metadata. Define a restricted local-only mode separately; never call its label authenticated identity. | The product cannot safely authorize approvals, and Capture's current caller-provided reviewer string must not be treated as authority. Do not expose hosted approval. |
| Membership/permission revocation during conversion | UNRESOLVED | Define a transactionally consistent authorization point. Proposed ordering: revocation committed before that point denies conversion; conversion serialized first may finish, with revocation affecting subsequent operations. | Concurrent operations have nondeterministic authorization outcomes. Do not claim immediate revocation or implement hosted conversion without a serialization rule and race tests. |
| Approval revocation versus conversion | UNRESOLVED | Serialize both on authoritative approval state. If revocation commits first, no new conversion; if conversion commits first, retain history and apply the approved post-revocation retrieval policy. | The system cannot define whether conversion wins a race or whether a revoked approval can create knowledge. Do not implement the conversion state transition. |
| Historical conversion versus operational replay after revocation | UNRESOLVED | Preserve the historical conversion for audit, but have ordinary replay return a typed revoked/non-replayable outcome rather than a knowledge bundle. Keep audit retrieval separately authorized. | A retry could unintentionally disclose or re-expose revoked knowledge, or an operator could mistake history retention for replay authorization. Do not finalize replay response semantics. |
| Revoked knowledge visibility in ordinary retrieval | UNRESOLVED | Exclude it from new ordinary reads once revocation commits; retain it for explicitly authorized audit history. | Revocation may leave knowledge visible indefinitely or erase needed history. Do not define retrieval filters or lifecycle constraints yet. |

### 10.3 Storage / canonicalization decisions

| Decision | Status | PROPOSED default | If left unresolved |
| --- | --- | --- | --- |
| Canonical payload and hash/version profile | UNRESOLVED | Approve exact fields, normalization, null/absent rules, ordering, timestamp form, encoding, hash/domain prefix, versioning, and golden vectors before implementation. | Exact replay cannot be distinguished reliably from changed-payload reuse. Do not store a purported canonical hash. |
| Conversion identity and concurrent retry recovery | UNRESOLVED | Uniqueness by project + producer namespace + candidate ID + candidate revision. On a key collision, reread the committed winner and compare using its payload version; return original IDs only for an exact match, otherwise conflict. | Corrections may overwrite history or concurrent callers may create duplicates; a unique constraint alone does not define safe recovery. Do not finalize conversion keys or claim concurrent idempotency. |
| Authorization/replay transaction boundary | UNRESOLVED | Resolve trusted scope, authorize before conversion lookup, then use one request transaction to recheck authoritative candidate/approval state and atomically write or compare the conversion. Specify how permission revocation is serialized with it. | A replay or write may race revocation or use a different connection/snapshot from authorization. Do not finalize the unit-of-work API or transaction schema. |

The responsibilities above are proposed ownership boundaries only; no specific person or assigned team is verified in this checkout. The producer questions require a versioned interface or explicit producer response. Product/security questions require an explicit product/security decision. Storage questions require an agreed contract and golden vectors before schema review.

## 11. Decisions required before schema finalization

1. Producer namespace, stable candidate identity, immutable revision, and correction rules.
2. Stable delivery retry key distinct from candidate identity.
3. Stable occurrence identity, order/multiplicity rules, and occurrence-to-evidence link cardinality.
4. Source ID namespaces, source revision meaning, and timestamp semantics.
5. Approval authority, authenticated project permission, local-only behavior, and revocation/retrieval policy.
6. Authored conflict semantics, target identity, pending target behavior, and v1 resolution scope.
7. Canonical payload fields, normalization, timestamp/null/array rules, hash algorithm, and version compatibility, backed by golden vectors.
8. Conversion key, exact replay and mismatch behavior, stale-revision handling, and atomic/concurrent transaction guarantees.

Until these decisions have explicit answers, v0.3 is a source-backed review draft. It is not sufficient authority to create Evidence tables, finalize key or permission constraints, claim producer guarantees, or assert that conversion, durable Capture review, hosted approval authorization, revocation ordering, or atomic persistence exists.
