# Celestin Delivery Contract Decision Audit and Readiness

**Review date:** 2026-10-19
**Status:** **BLOCKED for delivery persistence and producer integration.** The requester-authorized v0.3 conversion baseline is detailed, but material delivery/producer extensions remain unapproved and the current Capture prototype does not satisfy the producer boundary. This audit records no teammate or owner approval.

## 1. Scope and evidence rules

This review compares the requester-authorized [v0.3 decision record](SHARED_MEMORY_INTERFACE_CONTRACT_V0.3_DECISIONS.md), the historical [Resurrection boundary review](RESURRECTION_INTERFACE_BOUNDARY_REVIEW.md), the pending [delivery identity proposal](delivery-identity-producer-contract-proposal.md), the historical v0.3 drafts, and the current Capture/Evidence code and tests. The decision record says it is the implementation baseline and does not claim teammate acknowledgment (§ opening, lines 3–7); its role-level ownership list is at §5, line 143. The proposal says it is pending approval and does not amend that record (§ opening, lines 3–7).

Status meanings in this audit:

- **already specified** — the final record answers the policy question. This does not prove an upstream producer or adapter implements it, nor that another owner acknowledged it.
- **proposal pending approval** — a new, concrete technical rule is proposed but absent from the final record.
- **needs clarification** — part is settled, but a composite question or missing definition must be separated before implementation.
- **blocked** — implementation depends on a contract or trusted integration that is absent and cannot safely be inferred.

Historical `PROPOSED`/`UNRESOLVED` text in the boundary review and draft contract is superseded only where the final record directly answers the same point. The decision-review document is a historical owner-response form: its blank fields do not negate the requester-authorized baseline or count as owner acceptance. Conversely, silence and code behavior do not approve the proposal’s additional rules.

## 2. D-01–D-13 audit

The IDs below are those in the proposal’s §8. Because several IDs bundle independent questions, the “remaining ambiguity” column calls out subdecisions rather than treating a partial answer as a complete approval.

| ID / question and current proposal | What the final record establishes | Remaining ambiguity, options, and consequences | Recommendation; approving role; status |
|---|---|---|---|
| **D-01 — Delivery cardinality and candidate/revision issuers.** Proposal: one candidate revision per delivery; producer assigns stable candidate IDs and sequential revision numbers (§3.1, §5). | Candidate key and immutable revision key/sequence are established: `(project_id, producer_id, candidate_id)` and that tuple plus positive sequential `candidate_revision` (§1, lines 22–27). Delivery ID is producer-generated (§1, lines 31–34). | The record does not say whether one delivery is one revision or an atomic batch, nor who issues `candidate_id` or `candidate_revision`. Producer-issued versus server-issued IDs changes retry responsibility; batch semantics changes validation, atomicity, receipt shape, and locking. | Split into cardinality, candidate-ID issuer, revision-number issuer, and batch atomicity. Prefer one revision per delivery and producer-issued stable candidate/revision values only as **proposal — not approved**. Producer/Capture, with Storage for receipts. **blocked**. |
| **D-02 — Stable delivery retry ID.** Proposal asks whether the producer supplies and reuses a stable ID (§8). | Yes at the v0.3 baseline: producer-generated opaque `delivery_id`, unique within `(project_id, producer_id)`; same normalized payload returns the original receipt; changed payload returns `idempotency_conflict`; authorization precedes disclosure (§1, lines 29–34). | “Normalized delivery payload” and the receipt shape are not defined by that rule; these are D-08 and a separate API/storage detail. No producer implementation is present to demonstrate it supplies the ID. Without a stable producer key, a durable adapter cannot reliably associate at-least-once retries with the original receipt. | Keep the stated key and replay behavior. Do not reopen the answered policy; confirm producer feasibility separately. Producer/Capture for supplying it; Storage for durable receipt. **already specified** (implementation remains absent). |
| **D-03 — Category, certainty, and candidate timestamp fields.** Proposal asks which values are producer-owned and which enum/crosswalk applies (§8). | Conversion profile includes category, certainty, and `candidate_created_at`; category-to-knowledge mapping is pinned and unknown mappings reject; candidate time is claimed event time, distinct from service times, and canonical timestamps use UTC with six fractional digits (§1, lines 46–48; §3, lines 91–94). | The conversion record does not define a certainty vocabulary/crosswalk. Its vectors use `medium`/`high`, while `CandidateCertainty` in `capture/model.py:34–37` only has `unqualified`/`uncertain`. The local category mapper in `evidence/canonicalization.py:68–75` has explicit mappings and rejects other categories; this does not establish Resurrection’s values. | Preserve the fixed category mapping and reject unknown categories; do not invent a certainty mapping. Ask Producer/Capture to provide actual values and semantics, and Storage/Product to approve any crosswalk. **needs clarification**; Capture-to-producer conversion is blocked. |
| **D-04 — Source namespace and metadata guarantees.** Proposal asks what guarantees apply to namespace, ID, revision, timestamp, locator, hash, and producer permissions (§8). | Source identity is project- and namespace-scoped; namespace is project-registered; source ID is scoped within namespace/project; revision is opaque; evidence fields, hash form, event-time meaning, and source timestamp canonical form are defined (§1, lines 38–48, 52). | Registry lifecycle, who registers namespaces, which producer may use one, and whether an actual producer emits every field are not defined/implemented. Proposal’s producer allowlist and registration lifecycle are additional policy (§5, lines 264–272). | Keep the approved identity tuple. Obtain producer field guarantees and Product/Security/Storage decisions on registry administration and producer allowlists. **needs clarification**. |
| **D-05 — Occurrence identity/order/duplicates and shared Evidence.** Proposal asks whether order and duplicates survive and whether one evidence record may be shared (§8). | Answered: occurrence identity includes project, producer, candidate, revision, and zero-based ordinal; producer order is preserved, duplicates remain distinct, and one evidence record may be referenced by multiple occurrences (§1, lines 36–42; §5 acceptance item 3, line 136). | The checked-in Capture path does not conform: `capture/service.py:174–176, 204–207, 683–702` deduplicates/sorts source and evidence values. The repository’s boundary review reports no authoritative external producer package/interface; this does not prove the actual Resurrection producer can supply ordinals or links. If upstream also collapses duplicates, the approved occurrence provenance cannot be reconstructed without a producer change. | Treat occurrence preservation as a requirement, not an open policy choice. Confirm upstream feasibility and plan an adapter that preserves rows before any evidence deduplication. Producer/Capture. **already specified** (producer conformance unverified; current local Capture is incompatible). |
| **D-06 — Authored conflicts versus grouping hints and target identity.** Proposal asks whether conflicts are authored endpoints or grouping hints (§8). | Answered for v0.3: a conflict names a project-scoped producer/candidate/revision endpoint; cross-project references reject; the relation is symmetric and stored once; missing endpoints remain pending; conflicts mean “review together,” not winner selection or suppression (§1, lines 50–56). | Capture’s `conflict_key` groups local candidates and `conflicts_with` contains plain candidate-ID strings (`capture/model.py:85–86`, `capture/service.py` conflict-linking path); it lacks producer/revision endpoint identity. The final record does not specify the cross-runtime string comparator for canonical endpoint sorting; that is D-10. If the producer emits grouping keys only, they cannot safely be persisted as the approved authored typed conflict graph. | Preserve authored typed endpoint semantics; do not reinterpret a grouping hint as a declaration. Confirm producer can emit typed refs. Resolve comparator only under D-10. Producer/Capture. **already specified** (producer compatibility unresolved). |
| **D-07 — Producer/source namespace retirement.** Proposal: stop new submissions, retain history, do not recycle IDs; pending review after retirement remains a product question (§5, §8). | Project registration is required for producer and source namespace (§1, lines 22–24, 42), but retirement lifecycle and pending-item behavior are not settled. | Delete/reassign versus disable-and-retain changes foreign-key history, attribution, retry lookup, and whether pending candidates can later be approved/converted. | Prefer no recycling and immutable history, with new writes disabled at retirement, as **proposal — not approved**. Product/Security must decide pending approval/conversion; Producer/Capture and Storage define lifecycle mechanics. **blocked** for registry/lifecycle persistence. |
| **D-08 — Delivery/revision equality fields and hash profiles.** Proposal defines separate delivery and revision projections, profile IDs, explicit-null handling, and domain-separated digests (§3.1–3.4). | The final record defines only the **conversion** profile, exact conversion fields, direct SHA-256 of JCS UTF-8, and version string (§3, lines 89–94). It separately defines delivery and candidate-revision identity/replay outcomes (§1, lines 31–34; §3, lines 118–120). It explicitly excludes delivery ID from the conversion payload (§3, line 94). | No delivery equality fields, candidate-revision equality projection, digest version, or delivery receipt fields are specified in the final record. Reusing the conversion digest would compare the wrong projection. Alternative is canonical field comparison without hashes, but durable representation and compatibility still need defining. | Keep conversion profile isolated. Approve exact, separately versioned delivery/revision projections and vectors (or an equally deterministic alternative) before equality storage. Storage/canonicalization, with Producer/Capture. **blocked**. |
| **D-09 — Timestamp and JCS numeric edge cases.** Proposal rejects >6 fractional digits, leap seconds, `-00:00`, floats, and integers outside the JCS safe range (§4). | UTC normalization to six digits, JCS, and integer-only/no-float JSON are specified for conversion (§1, lines 46–48; §3, lines 91–94). | The final record does not explicitly define reject/normalize behavior for >6 fractional digits, leap seconds, `-00:00`, boolean-as-integer input, or the exact integer bounds. Python implementation currently rejects several cases, but code is not policy. A different choice can change equality or lose timestamp information. | Treat reject-in-v1 and explicit safe-integer bounds as **proposal — not approved**; accept only after Producer/Capture and Storage/C14N agreement and shared vectors. **proposal pending approval**. |
| **D-10 — Conflict endpoint ordering.** Proposal uses NFC UTF-8 byte ordering for string tuple members and numeric revision order (§3.2). | The final record requires conflict arrays sorted by `(producer_id, candidate_id, candidate_revision)` (§3, line 92) but does not define string collation across runtimes. | Python Unicode string ordering, database locale collation, and UTF-8 byte order can differ for some strings. A mismatch changes canonical bytes/digest. | Adopt NFC then UTF-8 byte order and numeric revision only as **proposal — not approved**; add multi-runtime vectors before storage. Storage and Producer/Capture. **proposal pending approval**. |
| **D-11 — Trusted producer binding and registry authority.** Proposal asks how transport principals map to registered producers/namespaces and who administers registry (§5, §8). | The final record requires a trusted application to derive a registered project producer from authenticated producer/session context, forbids trusting payload identity, and defines member capabilities/approval rules (§1, lines 22–24; §2, lines 60–72). | No real authenticator or producer registry/administration flow is in the Capture path. `CaptureInput.project_id` is caller data (`capture/model.py:51–60`), and `approve(..., reviewer_id: str)` accepts a string (`capture/service.py:324–334`); neither proves authentication. The “who may register/retire” and credential binding are not supplied by the final record. | Keep the security requirements; do not expose delivery writes until authenticated transport, trusted registry binding, and current project permission checks exist. Product/Security owns auth policy; Producer/Capture supplies producer registration needs. Registry-administration and runtime identity roles are not identified. **blocked**. |
| **D-12 — Whether failed attempts reserve delivery IDs.** Proposal reserves IDs only after successful atomic acceptance; validation/gap/transaction failures leave no receipt (§3.3). | The final record defines exact replay and changed-content conflict for a delivery ID (§1, lines 31–34), plus atomic conversion behavior, but not whether failed ingress attempts create durable terminal receipts. | Success-only reservation permits retry after a prior gap/state change. Durable rejection receipts make failures stable but affect privacy, authorization, retention, and retry-after-state-change. Transaction failure cannot safely leave a receipt if atomic acceptance is required. | Prefer accepted delivery + immutable revision + occurrence rows in one commit, no reservation on rejected precondition, as **proposal — not approved**. Product/Security and Storage, with Producer/Capture retry semantics. **proposal pending approval**. |
| **D-13 — `original_claim`, retirement, and pending review/conversion.** Proposal suggests deriving original claim from immutable revision history; retirement pending behavior remains open (§3.1, §5, §8). | Correction before conversion uses a next immutable revision and fresh approval; post-conversion correction creates successor knowledge and explicit supersession atomically. `original_claim` is a nullable conversion field (§2, lines 81–85; §3, line 94). | The record does not define how ingress obtains `original_claim` (producer field, server derivation, or review-only metadata), especially across multiple corrections. It also does not decide whether a pending historical candidate remains reviewable after producer retirement; that overlaps D-07. Revision identity, knowledge supersession, and retrieval visibility must remain separate. | Split lineage sourcing from retirement behavior. Prefer deriving correction lineage from immutable prior revisions only if the producer contract identifies the lineage and no silent synthesis occurs—**proposal — not approved**. Producer/Capture for lineage; Product/Security for pending review/conversion. **needs clarification**. |

## 3. Cross-document consistency findings

1. **D-02 partly re-asks a settled rule.** The final record already mandates producer-generated, project/producer-scoped delivery IDs and exact normalized-payload replay behavior (§1, lines 31–34). Narrow D-02 follow-up to whether the upstream producer can honor that requirement and to the payload equality details owned by D-08.
2. **Several proposal IDs combine independent decisions.** Split D-01 into delivery cardinality and candidate/revision issuers; D-03 into category mapping, certainty vocabulary, and timestamp field guarantees; D-04 into source field guarantees and registry administration; D-08 into delivery projection, revision projection, profile/version, and receipt shape; D-11 into credential-to-producer binding and registry authority; D-13 into lineage source and retirement behavior.
3. **D-05 and the core of D-06 are settled requirements, not open policy questions.** They remain producer conformance checks. Current Capture sorts/deduplicates occurrence values and has only grouping/string conflict representations, so it is not evidence that Resurrection conforms.
4. **The authoritative record and proposal are compatible when scoped correctly.** The record settles v0.3 conversion semantics; the proposal adds delivery/revision equality, producer lifecycle, comparator, and edge-validation details. Those additions require explicit change/extension approval, not silent interpretation of “final baseline.” In particular, the record’s statement that v0.3 has no unresolved design choices (§5, line 143) does not itself approve the additional delivery profile.
5. **Canonicalization implementation is not a substitute for specification or producer proof.** `canonicalization.py` implements the approved conversion profile and not delivery/revision hashing (module docstring lines 1–5, `conversion_input_hash` lines 160–168). Its Python conflict sort is not the proposal’s explicitly portable UTF-8 comparator (proposal §4, line 251). The two decision-record vectors are in the canonicalization test file, but this review environment cannot import the declared JCS package, so no fresh test pass is claimed.
6. **No named approver or authoritative Resurrection package is identified.** The decision record assigns role-level responsibility only (§5, line 143). The boundary review’s repository snapshot and the proposal §1, lines 27–30, report no authoritative upstream producer package/interface in this checkout. This is not evidence that no such external owner or implementation exists; it is a local evidence limit.
7. **Historical response forms are not approval evidence.** The historical decision review’s response fields remain unanswered; they neither override requirements explicitly set in the final record nor approve the later D-01–D-13 proposal.

## 4. Decision dependencies

```text
D-01 cardinality + candidate/revision issuers ─┐
D-03 category/certainty/timestamp mapping ─────┤
D-04 source field + namespace registry contract ├──> D-08 delivery/revision equality projections
D-05 occurrence conformance ───────────────────┤       + profile/version + golden vectors
D-06 typed conflict endpoint availability ────┘                │
                                                                ├──> D-12 receipt/reservation transaction
D-09 timestamp/numeric acceptance ─────────────────────────────┤
D-10 cross-runtime conflict comparator ─────────────────────────┘

D-07 producer/namespace retirement ─┬──> D-13 pending review/conversion policy
D-01 revision lifecycle ────────────┘

D-11 authenticated principal ↔ registered producer/namespace
     + current permission check ───────> safe ingress and authorized replay

Approved v0.3 conversion profile/transaction requirements ─────> conversion adapter
Actual source fields + D-03/04/05/06 + D-11 ────────────────────> producer adapter
```

The implementation gate requires both the relevant specification decisions and evidence that the actual producer can provide the agreed contract. A PostgreSQL unique key cannot compensate for missing identity, authenticated producer binding, or deterministic payload projection.

## 5. Canonicalization verification evidence and limits

- The repository requires Python `>=3.10`; runtime dependency `rfc8785>=0.1.4,<0.2` is in `pyproject.toml` lines 8–12. README documents `python -m pytest -q` at line 355.
- This checkout’s default `python` is Python 3.13.9 and pytest is 8.4.2. `python -c "import rfc8785"` failed with `ModuleNotFoundError`; the task log records that the expected repository validation interpreter `.venv\validation\Scripts\python.exe` is absent.
- Focused command attempted: `python -m pytest -q tests/evidence/test_canonicalization.py`. It exited 1 during collection because `src/orbit/evidence/canonicalization.py:17` imports missing module `rfc8785`. Pytest also emitted cache-write permission warnings. **No canonicalization tests passed in this verification.** No packages or virtual environments were installed or created.
- The source defines the approved conversion field set/profile and direct SHA-256 over RFC 8785 output (`canonicalization.py:20–29, 32–75, 108–168`). Its tests include conversion-profile vectors, not delivery/revision profiles. The delivery/revision vectors in the proposal (§3.4) are explicitly pending and were not validated by an installed runtime here.
- No second language/runtime was available or used. Cross-runtime JCS and conflict-order verification remains outstanding. Earlier task-log statements about temporary-package checks are historical and are not represented as a fresh pass in this audit.

## 6. Work that can proceed safely

**Can proceed without changing the contract or schema:**

- Ask the responsible role owners to answer the narrowed questionnaire below; record responses without changing status until explicit acceptance is supplied.
- Prepare non-normative producer conformance examples and test outlines against the already specified v0.3 fields, clearly marking unsupported upstream fields.
- Restore a project-approved Python environment through the repository’s normal dependency workflow only when separately authorized; then rerun the existing conversion-vector tests. Do not install dependencies as part of this audit.
- Design tests for conversion-level approved behavior without creating a durable producer/delivery schema.

**Must wait for explicit clarification/approval:**

- Delivery cardinality, candidate/revision ID issuer and exact producer wire contract.
- Delivery/revision payload projections, hash/version profile, receipt fields, and failure reservation semantics.
- Certainty mapping and producer timestamp/source metadata guarantees.
- Producer and source namespace registry administration, permissions, and retirement behavior.
- Confirmation that occurrence multiplicity/ordinals, typed conflict endpoints, and evidence sharing are available from the actual producer.
- Authenticator and registry integration, and pending-item behavior after producer retirement.
- Any delivery ledger, producer registry, immutable revision, occurrence-link, or receipt schema/adapter implementation.

No database operation, schema design, code, test, dependency, MCP API, or migration was performed by this audit.

## 7. Acceptance criteria for the next implementation milestone

Before producer/delivery persistence is considered ready, tests must demonstrate all of the following against the agreed profile and trusted ingress boundary:

1. Project and producer identity come from authenticated server-side context; forged body IDs cannot change scope, and authorization runs before receipt/revision lookup.
2. Delivery IDs are producer-generated and scoped exactly as specified; exact retries return the same accepted receipt, while changed content under the same ID conflicts without mutation.
3. Candidate identity remains stable across immutable sequential revisions; same-revision same-content retry is a no-op, changed content conflicts, gaps are handled per an explicit policy, and a new revision needs fresh approval.
4. Concurrent identical deliveries converge to one durable result; different payloads under one delivery key or revision produce the specified conflict with no partial writes.
5. Rejected/invalid/gap/transaction-failure ID reservation behavior matches the approved policy; uncertain commit recovery uses the same key without duplicate revision or occurrence rows.
6. Duplicate source occurrences survive with contiguous ordered ordinals, may share a deduplicated evidence record, and preserve source namespace, ID, revision, locator, hash, and event timestamp exactly under the agreed normalization rules.
7. Category/certainty crosswalk rejects unmapped values. Timestamp and numeric boundary vectors include >6 digits, leap seconds, `-00:00`, explicit null versus omission, JCS integer bounds, and floats/booleans as agreed.
8. Conflict declarations preserve typed endpoint identity, pending endpoints, project isolation, duplicate-edge behavior, and a portable ordering vector in at least two runtimes.
9. Producer/namespace retirement and pending-review behavior match recorded policy without rewriting historical attribution.
10. Storage injection failures leave no partial accepted delivery/revision/occurrence graph; concurrent revision advancement cannot accept the same next revision twice.
11. Existing conversion golden vectors remain unchanged and pass in the declared runtime; delivery and candidate-revision profiles have separate versioned vectors.
12. Existing MCP request, response, and public error schemas remain unchanged; no internal receipt or knowledge-state detail is exposed incidentally.

These criteria extend the v0.3 conversion acceptance list (§5, lines 132–141); they are proposed tests for the additional producer/delivery milestone, not tests already implemented or passed.

## 8. Owner questionnaire and next gate

The repository names role-level owners only; it does not identify individuals. Please have the roles below accept, reject, or amend each item with a rationale:

### Producer/Capture (and Resurrection producer owner, if different)

1. Is one delivery exactly one candidate revision, or can it be a batch? If batched, what are ordering, all-or-nothing, per-item receipt, and partial-rejection semantics?
2. Who issues stable candidate IDs and candidate revision numbers? Can the actual producer guarantee contiguous immutable revisions and stable producer-generated delivery IDs on every retry?
3. Which exact category, certainty, candidate timestamp, source namespace/ID/revision/locator/hash, occurrence ordinal/link, and conflict endpoint fields are emitted? What are their meanings and allowed values?
4. Are duplicate occurrences and their order preserved by the producer? Can multiple occurrences reference one source/evidence identity?
5. What does `original_claim` mean for successive corrections, and what lineage reference does the producer provide?

### Product/Security

6. Which authenticated credential/transport subject binds to a registered producer and allowed project/namespaces? Which trusted role may register, disable, or retire producers/namespaces?
7. After producer retirement, may already accepted pending candidates still be approved or converted? Which actions are immediately disabled, and which audit/history reads remain available?
8. Confirm any decision to reserve a delivery ID only for successful accepted transactions, or specify which deterministic rejections get durable terminal receipts.

### Storage/Canonicalization

9. Approve or replace the exact delivery and candidate-revision equality objects, explicit-null rules, normalization, independent profile IDs, hash preimages, receipt shape, and success/rejection reservation behavior. Confirm vectors in the actual JCS runtime.
10. Approve the rejection rules for timestamp edge cases and exact JCS integer range; specify the cross-runtime conflict comparator and publish golden vectors.
11. Confirm transaction/unique-key behavior for same-delivery retries, distinct delivery IDs targeting one revision, concurrent revision advancement, rollback, and uncertain commit recovery.

**Readiness verdict:** **BLOCKED** for durable delivery identity/producer integration. The v0.3 conversion baseline is requester-authorized and sufficiently normative for its stated semantics, but it does not supply the additional delivery/revision equality profile, producer cardinality/issuer contract, lifecycle policy, or a live trusted producer binding. The single next step is a recorded role-owner response to the split questions above, followed by updating the proposal as a versioned extension or change record before designing its schema. No implementation should infer these answers from the local Capture prototype.
