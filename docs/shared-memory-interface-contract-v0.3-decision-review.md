# Celestin Shared Memory Interface Contract v0.3 — Decision Review

> **Historical review notice:** This decision request predates the requester-authorized implementation baseline in [`SHARED_MEMORY_INTERFACE_CONTRACT_V0.3_DECISIONS.md`](SHARED_MEMORY_INTERFACE_CONTRACT_V0.3_DECISIONS.md). Its `PROPOSED` defaults are superseded as implementation choices only where that record explicitly decides the same question. Owner-response fields remain unanswered unless an owner fills them; this record does not claim teammate or external-owner acceptance. `UNRESOLVED` owner-response status remains unresolved as a matter of cross-team acceptance and does not override the implementation baseline. Technical details not answered by the record also remain open.

## Purpose and response instructions

This package requests explicit Producer/Resurrection and Product/Security decisions before Celestin implements an Evidence persistence adapter or finalizes storage semantics. It is based on the unresolved register in [`shared-memory-interface-contract-v0.3.md`](shared-memory-interface-contract-v0.3.md) and the supporting [`evidence-linked-memory-contract.md`](evidence-linked-memory-contract.md).

**No answer is recorded as agreed.** The v0.3 register has no decision IDs, so `PR-*`, `PS-*`, and `ST-*` below are identifiers for this review package only; they are not existing contract IDs. Every default is marked **PROPOSED**. For each register item, owners may select one response and add an owner rationale. Blank response fields mean no owner decision has been recorded:

- **Response:** `[ ] AGREE WITH PROPOSAL` / `[ ] REJECT PROPOSAL` / `[ ] NEED DISCUSSION`
- **Owner rationale:** `____________________________________________________________`

Any separately labeled reviewer disposition is technical input only, not an owner response or sign-off. Supplementary scope questions are explicitly identified and do not add or change a v0.3 register item.

An answer that rejects or modifies a proposal should state the replacement contract precisely enough to derive acceptance tests. A silence or unfilled field leaves the decision unresolved.

The source contract remains the current status authority: local Capture and Evidence prototype behaviors are labeled IMPLEMENTED there; these questions concern target behavior and do not turn proposals into producer guarantees or approved product policy.

## A. Producer / Resurrection decisions

### PR-01 — Candidate identity and namespace

**Question:** Does the producer issue a stable candidate ID? If so, what is its namespace and uniqueness scope: producer, project, both, or another defined scope? Does the ID remain stable when candidate content is edited or corrected?

**PROPOSED default:** Use a producer-issued candidate ID namespaced by producer and project, stable across candidate revisions. Candidate identity names the logical candidate; it is distinct from the content of any one revision.

**Consequences of choosing differently:** A globally unique ID could permit a smaller key; a project-only ID would require a guarantee that producers never collide within a project. A content-derived ID would change when content changes and cannot reliably identify a candidate across corrections. Any alternative changes conversion mapping and conflict-reference design.

**If unresolved:** Celestin cannot reliably match candidate updates or safely finalize candidate/conversion identity. Do not derive a permanent producer key from the local Capture fingerprint.

**Response:** `[ ] AGREE WITH PROPOSAL` / `[ ] REJECT PROPOSAL` / `[ ] NEED DISCUSSION`<br>
**Owner rationale:** `____________________________________________________________`

### PR-02 — Revisions and corrections

**Question:** How does the producer represent a candidate revision, and how does it distinguish a correction from a new candidate? Is revision identity monotonic, opaque, or content-addressed? Can a revision ever be edited after publication?

**PROPOSED default:** Each correction creates an immutable revision/snapshot under the same candidate identity. Approval binds to one exact revision; later revisions require separate approval and do not overwrite earlier conversions.

**Consequences of choosing differently:** Mutable revisions would require version snapshots or another immutable approval binding at the Celestin boundary. Treating corrections as new candidates changes identity continuity and conflict-link handling. A non-orderable revision token cannot support “latest” without an explicit producer current-revision indicator.

**If unresolved:** Approval may become detached from the content it reviewed, and the adapter cannot determine which revision is current. Do not add revision or approval schema.

**Response:** `[ ] AGREE WITH PROPOSAL` / `[ ] REJECT PROPOSAL` / `[ ] NEED DISCUSSION`<br>
**Owner rationale:** `____________________________________________________________`

### PR-03 — Delivery/request idempotency

**Question:** Does the producer provide a stable delivery/request ID reused for retries of the same submission? What scope makes it unique, and what should happen if the same ID is received with different content?

**PROPOSED default:** Use a stable delivery ID scoped by producer and project, distinct from candidate ID and candidate revision. An exact repeated delivery is idempotent; changed canonical input under the same delivery ID is a conflict.

**Consequences of choosing differently:** Without a stable retry ID, transport retries cannot be distinguished from a new delivery. Deriving identity from payload may merge distinct deliveries or fail to identify changed-payload reuse. Candidate-level deduplication alone does not preserve delivery audit history.

**If unresolved:** Celestin cannot promise delivery-level deduplication or reliable retry recovery. Do not claim retry safety from the existing local candidate fingerprint.

**Response:** `[ ] AGREE WITH PROPOSAL` / `[ ] REJECT PROPOSAL` / `[ ] NEED DISCUSSION`<br>
**Owner rationale:** `____________________________________________________________`

### PR-04 — Occurrence identity, order, and duplicates

**Question:** Does each source occurrence have a producer-issued stable occurrence ID? Is order meaningful and supplied? Must repeated appearances of the same source be retained as distinct occurrences?

**PROPOSED default:** Preserve each occurrence with its own identity and explicit ordinal/order. Retain duplicates even when source type, source ID, locator, revision, or hash are equal. Occurrence identity is separate from evidence-record identity.

**Consequences of choosing differently:** If order is irrelevant, canonicalization may sort occurrences only under an explicit rule. If repeated occurrences are semantically redundant, deduplication needs a producer-defined equivalence key. Celestin cannot safely infer either rule from source metadata equality.

**If unresolved:** The adapter risks collapsing or reordering source history. Do not finalize occurrence uniqueness or ordering constraints.

**Response:** `[ ] AGREE WITH PROPOSAL` / `[ ] REJECT PROPOSAL` / `[ ] NEED DISCUSSION`<br>
**Owner rationale:** `____________________________________________________________`

### PR-05 — Occurrence-to-evidence associations and sharing

**Question:** Does the producer explicitly link occurrences to evidence references? Can one occurrence cite multiple evidence references? Can one evidence reference support multiple occurrences? What fields identify each side of the link?

**PROPOSED default:** Preserve explicit association links independently from both occurrence identity and evidence-record identity. Permit evidence sharing across occurrences; permit multiple references per occurrence only if confirmed by the producer. Do not infer links from matching IDs or locators.

**Consequences of choosing differently:** A one-to-one relationship requires a narrower link model; many-to-many relationships require durable association identity and cardinality. Inferring links would make provenance ambiguous and potentially attach evidence to the wrong occurrence.

**If unresolved:** Celestin cannot prove which evidence supports which occurrence or guarantee lossless round-trip. Do not use a source-reference/evidence-record coincidence as an association contract.

**Response:** `[ ] AGREE WITH PROPOSAL` / `[ ] REJECT PROPOSAL` / `[ ] NEED DISCUSSION`<br>
**Owner rationale:** `____________________________________________________________`

### PR-06 — Source and conflict-target namespaces

**Question:** What namespace identifies a source ID (including message-like IDs), and what namespace and stable identity identify a conflict target? Can a conflict target refer across producers or to a particular candidate revision? Are conflicts authored declarations, grouping hints, or both? If declarations are directional, which endpoint authors the declaration? Should derived conflict grouping be retained as separate derived data, and does explicit conflict-resolution history belong in the initial version?

**PROPOSED default:** Carry an explicit source/provider namespace and producer namespace for candidate conflict targets. Keep an authored declaration directional; identify its target by producer namespace and candidate ID, with a target revision only if the producer defines that meaning. Preserve unresolved typed targets without fabricating candidate records.

**Consequences of choosing differently:** Globally unique IDs could reduce namespace fields only with an explicit guarantee. Same string IDs from different sources/producers otherwise collide or point to the wrong record. Undirected conflict grouping differs from retaining a directional authored declaration.

**If unresolved:** Cross-source and cross-producer references cannot be safely joined or constrained. Do not finalize source foreign keys or conflict target keys.

**Conflict-direction note:** The older reconciliation proposal in `evidence-linked-memory-contract.md` recommends an undirected, canonically ordered pair; v0.3 proposes preserving directional authored declarations separately from derived grouping. Neither is approved. The producer/product owners must explicitly choose the semantics; this package does not silently resolve the difference.

**Response:** `[ ] AGREE WITH PROPOSAL` / `[ ] REJECT PROPOSAL` / `[ ] NEED DISCUSSION`<br>
**Owner rationale:** `____________________________________________________________`

### PR-07 — Timestamp and source-revision semantics

**Question:** For each source type, does a timestamp mean source-event occurrence, source-object creation, producer observation, or delivery/receipt time? What precision/offset must round-trip? Is source revision ordered or opaque, and what does it identify?

**PROPOSED default:** Keep occurrence time distinct from receipt/delivery time. Preserve the producer-supplied timestamp value, representation, and precision as source/audit data; separately define a normalized UTC instant for ordering and equality. Require timezone-aware instants for normalized values. Treat revisions as opaque unless the producer defines their ordering and meaning per source type.

**Consequences of choosing differently:** A normalized UTC-only representation may lose source formatting or precision; interpreting opaque revisions as ordered can produce incorrect freshness decisions. A source-specific timestamp definition changes payload equality and retrieval freshness rules.

**If unresolved:** Celestin cannot make reliable ordering/freshness claims or canonicalize timestamps without risking semantic loss. Do not infer revision order or timestamp meaning.

**Response:** `[ ] AGREE WITH PROPOSAL` / `[ ] REJECT PROPOSAL` / `[ ] NEED DISCUSSION`<br>
**Owner rationale:** `____________________________________________________________`

## B. Product / Security decisions

### PS-01 — Approval authority and identity

**Question:** Which authenticated principals may approve: project owners, owners plus designated reviewers, or any member with a specific permission? What exact project permission grants approval? Is a restricted local-only approval mode permitted when individual authentication is unavailable?

**PROPOSED default:** Hosted approval requires a transport-authenticated principal, active project membership, and explicit project-scoped approval permission. Reviewer labels remain display/provenance metadata, never authenticated identity. Define local-only behavior separately and do not represent a local process label as a verified human identity.

**Consequences of choosing differently:** Owner-only or delegated-reviewer policy changes permission assignment and audit expectations. Allowing any member broadens authority. A local-only mode without an explicit boundary risks being mistaken for hosted authentication.

**Future acceptance-test requirement (not an existing or passed test):** Contract [§9](shared-memory-interface-contract-v0.3.md#9-failure-cases-and-acceptance-tests) requires a negative test showing that an authenticated active project member without the explicit approval permission is denied before candidate state changes or conversion becomes possible.

**If unresolved:** No secure hosted approval path can be enabled; current Capture `reviewer_id` is insufficient evidence of authority.

**Response:** `[ ] AGREE WITH PROPOSAL` / `[ ] REJECT PROPOSAL` / `[ ] NEED DISCUSSION`<br>
**Owner rationale:** `____________________________________________________________`

### PS-02 — Membership/permission revocation during conversion

**Question:** At what transactionally consistent point is membership and approval permission evaluated? If revocation races conversion, may an already-authorized conversion finish? Which membership/permission state must be rechecked or serialized?

**PROPOSED default:** Define a shared serialization point for authorization and conversion. If a membership/permission revocation commits before conversion reaches its authorized decision point, conversion is denied. If conversion serializes first, it may finish; revocation governs later operations. State which rows/versions or equivalent authorization facts provide this guarantee.

**Consequences of choosing differently:** Instant cancellation of in-flight work requires stronger coordination than denying new operations after revocation. Snapshot-only checks may let a conversion commit after a revocation that product expects to take effect first.

**Future acceptance-test requirement (not an existing or passed test):** Contract [§9](shared-memory-interface-contract-v0.3.md#9-failure-cases-and-acceptance-tests) requires both membership/permission revocation transaction orderings to be tested against the approved authorization consistency point.

**If unresolved:** Concurrent authorization outcomes are undefined; do not claim immediate revocation or implement hosted conversion without explicit race semantics and tests.

**Response:** `[ ] AGREE WITH PROPOSAL` / `[ ] REJECT PROPOSAL` / `[ ] NEED DISCUSSION`<br>
**Owner rationale:** `____________________________________________________________`

### PS-03 — Approval revocation racing conversion

**Question:** If approval revocation and conversion run concurrently, which operation wins? Does the same ordering apply to rejection or correction of the approved candidate revision?

**PROPOSED default:** Serialize conversion and approval revocation on authoritative approval/revision state. Revocation committed first blocks new conversion. Conversion serialized first may commit; its historical result remains recorded, then post-revocation visibility rules apply. Corrections create separately approved revisions rather than rewriting the prior approval.

**Consequences of choosing differently:** Allowing conversion to win regardless of a prior committed revocation could create knowledge from revoked approval. Letting revocation retroactively erase committed conversion would conflict with audit/history guarantees.

**Future acceptance-test requirement (not an existing or passed test):** Contract [§9](shared-memory-interface-contract-v0.3.md#9-failure-cases-and-acceptance-tests) requires both approval-revocation/conversion orderings, including an already-authorized in-flight conversion, and the approved behavior for a correction after approval.

**If unresolved:** There is no deterministic authorization or audit outcome for the race. Do not finalize approval/conversion transition constraints.

**Response:** `[ ] AGREE WITH PROPOSAL` / `[ ] REJECT PROPOSAL` / `[ ] NEED DISCUSSION`<br>
**Owner rationale:** `____________________________________________________________`

### Supplementary scope question — corrected-revision supersession (cross-reference PR-02 and PS-05; not a v0.3 register entry)

If a corrected, separately approved candidate revision is treated as replacing prior knowledge, should it supersede that knowledge, how should the supersession relationship be represented, and should the prior knowledge remain visible in ordinary retrieval? Candidate revision identity, knowledge supersession, and retrieval visibility are separate decisions; this question does not select a policy.

**Supplementary owner response:** `[ ] AGREE WITH A SPECIFIED POLICY` / `[ ] REJECT A SPECIFIED POLICY` / `[ ] NEED DISCUSSION`<br>
**Owner rationale:** `____________________________________________________________`

**Future acceptance-test requirement (not an existing or passed test):** Contract [§9](shared-memory-interface-contract-v0.3.md#9-failure-cases-and-acceptance-tests) requires correction-after-approval behavior to preserve the prior revision and approval while requiring separate approval for the new revision. Ordinary-retrieval visibility of a superseded record remains an open product decision and must be tested only after that policy is approved.

### PS-04 — Historical conversion versus operational replay after revocation

**Question:** When an exact retry addresses a conversion whose approval was revoked, should the normal conversion endpoint return the historical result, a revoked status, or reject the operation? Which separate permission allows audit access?

**PROPOSED default:** Preserve the conversion as history, but make ordinary operational replay return a typed revoked/non-replayable result rather than a knowledge bundle. Provide historical details only through a separately authorized audit path.

**Consequences of choosing differently:** Returning the bundle risks re-exposing revoked knowledge through a retry. Returning only a status may require clients to handle a non-success outcome despite an earlier successful request. Rejecting may simplify semantics but can make retry recovery less informative.

**Future acceptance-test requirement (not an existing or passed test):** Contract [§9](shared-memory-interface-contract-v0.3.md#9-failure-cases-and-acceptance-tests) requires the approved outcome for exact replay after approval revocation and separately authorized audit access; the operational response must not restore revoked knowledge to ordinary retrieval.

**If unresolved:** Idempotency and revocation can conflict, and clients cannot distinguish an exact historical retry from a currently usable result. Do not finalize replay response semantics.

**Response:** `[ ] AGREE WITH PROPOSAL` / `[ ] REJECT PROPOSAL` / `[ ] NEED DISCUSSION`<br>
**Owner rationale:** `____________________________________________________________`

### PS-05 — Ordinary retrieval after revocation

**Question:** Should new ordinary retrievals exclude knowledge associated with revoked approval as soon as the revocation transaction commits? Should the record remain visible in separately authorized audit history?

**PROPOSED default:** Exclude revoked knowledge from ordinary reads that begin after revocation commits; preserve it for authorized audit/history access. Define how an already-running read is ordered relative to the revocation transaction.

**Consequences of choosing differently:** Keeping revoked knowledge in ordinary retrieval preserves availability but may contradict revocation intent. Deleting history harms auditability. Immediate visibility changes require retrieval and caches to observe the same revocation state.

**Future acceptance-test requirement (not an existing or passed test):** Contract [§9](shared-memory-interface-contract-v0.3.md#9-failure-cases-and-acceptance-tests) requires new ordinary reads after revocation commits to follow the approved visibility policy and authorized audit reads to retain the required history.

**If unresolved:** The system cannot promise retrieval revocation or audit retention. Do not finalize lifecycle filters, cache invalidation, or public replay/read behavior.

**Response:** `[ ] AGREE WITH PROPOSAL` / `[ ] REJECT PROPOSAL` / `[ ] NEED DISCUSSION`<br>
**Owner rationale:** `____________________________________________________________`

## C. Storage / Canonicalization dependencies

These are storage-owner decisions and implementation dependencies, not producer guarantees or product permission policy. The older reconciliation section in `evidence-linked-memory-contract.md` is explicitly a proposal for team agreement. It contains additional candidate-key and conflict-shape recommendations; those do not override v0.3 or constitute approval.

**Reviewer boundary:** The repository does not assign a named storage owner. The dispositions below are Codex's technical review input for the storage/canonicalization area, not an owner sign-off or final decision. Each item remains UNRESOLVED until the accountable owner confirms it.

### ST-01 — Canonical conversion payload and hash/version profile

**Status:** UNRESOLVED.<br>
**Depends on:** PR-01 through PR-07 for stable IDs, revision/delivery semantics, occurrence and link shape, namespaces, timestamps, and source revisions; PS-01 and PS-03 for approval identity and revision binding.

**PROPOSED default:** After those inputs are agreed, specify exact canonical fields, null/absent handling, normalization, array ordering, timestamp representation, encoding, hash/domain prefix, version compatibility, and golden vectors. Exclude resolver-derived availability/freshness and generated persistence IDs from approved payload equality.

**Consequences if unresolved:** Without an agreed canonicalization and hash profile, exact-replay comparison versus changed-payload detection is unreliable. Do not treat a locally computed hash as a canonical payload identity.

**Choices not settled by the final record:** Defining delivery-ID equality fields/profile and candidate-revision payload equality; confirming that an upstream producer can supply the required fields and reproduce canonical bytes; adding persistence hash/version columns and implementing changed-payload conflicts. The final decision record selects the conversion JCS/hash profile and its two vectors as the requester-authorized implementation baseline. Those decisions do not prove producer support or teammate acceptance. Without the delivery and producer fields, delivery vectors would still canonize guesses.

**Constraint versus policy:** Database checks may require a non-null supported version and hash format once agreed, and may preserve immutable payload rows. Which fields represent an approved claim and how normalization affects meaning are domain/application contract, not facts a SQL constraint can decide.

**Safe preparation:** Compare the current `src/orbit/evidence/canonicalization.py` implementation with the final decision record and with any producer implementation; review the fixed conversion vectors without treating them as evidence that Resurrection emits these fields; separately propose delivery and revision-equality vectors for owner review. Do not infer producer support or finalize a delivery profile.

**Implementation note (does not change the historical owner-response field):** The current local module uses RFC 8785 JCS and matches the final decision record's fixed conversion vectors. The conversion profile is selected by that requester-authorized record. The ST-01 response in this older review remains unfilled as a cross-team response; delivery equality, producer reproduction, and durable hash persistence remain unimplemented or unresolved.

**Response:** `[ ] AGREE WITH PROPOSAL` / `[ ] REJECT PROPOSAL` / `[ ] NEED DISCUSSION`<br>
**Owner rationale:** `____________________________________________________________`<br>
**Reviewer disposition — technical input, not owner sign-off:** The versioned-payload and golden-vector gate is sound, but the exact field set, normalization, timestamp policy, serializer, and hash profile depend on PR-01–PR-07. This input does not select a canonicalization implementation.

### ST-02 — Conversion identity, uniqueness, and concurrent retry recovery

**Status:** UNRESOLVED.<br>
**Depends on:** PR-01 through PR-03 for candidate/revision/delivery identities; ST-01 for payload comparison; ST-03 for the authorized transaction/replay boundary; PS-03 and PS-04 for revocation and operational replay outcomes.

**PROPOSED default:** One conversion per agreed project + producer namespace + candidate ID + candidate revision. Enforce structural uniqueness in storage. On a same-key collision, load the committed conversion, compare using its stored payload version, return its original IDs only on exact match and permitted replay, otherwise return a typed conflict. Specify bounded transaction retry behavior separately.

**Consequences if unresolved:** Undefined conversion identity and retry semantics risk overwriting an earlier conversion or creating duplicate conversions during retries/concurrent requests. A unique constraint alone does not define safe replay recovery.

**Choices that must wait:** Final unique key, foreign keys, conflict/re-read algorithm, whether multiple conversion profiles can exist, and all adapter APIs that imply conversion identity.

**Constraint versus policy:** Unique keys and referential integrity are database invariants after identity semantics are agreed. Whether a caller is authorized to replay, whether revoked knowledge is visible, and what public outcome a mismatch produces are application/product policy and must not be represented as authentication enforced by a constraint.

**Safe preparation:** Write adapter-neutral state-transition tables and failure-injection test scenarios without naming final columns or keys. Do not add schema or a durable adapter.

**Response:** `[ ] AGREE WITH PROPOSAL` / `[ ] REJECT PROPOSAL` / `[ ] NEED DISCUSSION`<br>
**Owner rationale:** `____________________________________________________________`<br>
**Reviewer disposition — technical input, not owner sign-off:** Structural uniqueness and exact-match replay are appropriate goals, but the proposed key depends on unconfirmed producer/project namespaces, candidate revision identity, payload versioning, PS replay/revocation policy, and ST-03's transaction boundary. Do not freeze the key or recovery behavior yet.

### ST-03 — Authorization and replay transaction boundary

**Status:** UNRESOLVED.<br>
**Depends on:** PS-01 through PS-05 for principal, permission, revocation ordering, replay, and retrieval visibility; producer decisions that determine the authoritative candidate/revision loaded by the transaction; ST-01 and ST-02 for payload comparison and conversion identity/recovery.

**PROPOSED default:** Resolve trusted scope server-side, authorize the resolved project/action before conversion lookup, and use one request-scoped transaction for authoritative candidate/approval checks, conversion comparison or write, and related evidence/provenance links. Define the transactionally consistent authorization point and serialization mechanism for membership/permission and approval revocation.

**Consequences if unresolved:** A replay or write may race revocation or use a different connection/authorization snapshot from the conversion operation. Results can therefore reflect an authorization decision inconsistent with the state governing the write or replay.

**Choices that must wait:** Request-unit-of-work API, lock/conditional-update strategy, isolation requirements, replay response, and any schema representation of approval/revocation state.

**Constraint versus policy:** Foreign keys, same-project relationships, and unique conversion identity can be database-enforced. Authentication, permission assignment, authorization before reads, revocation ordering, and audit-access policy remain application/security responsibilities. Do not encode “owner only” or another unapproved role policy as a permanent database check.

**Safe preparation:** Draw transaction sequence diagrams and define fake/scripted test cases for each race ordering. These can proceed without a schema, but their expected outcomes remain provisional until Product/Security decides.

**Response:** `[ ] AGREE WITH PROPOSAL` / `[ ] REJECT PROPOSAL` / `[ ] NEED DISCUSSION`<br>
**Owner rationale:** `____________________________________________________________`<br>
**Reviewer disposition — technical input, not owner sign-off:** Authorization before conversion lookup and a single request transaction are sound design goals, but the authorization point and serialization strategy depend on unresolved PS-01–PS-05. This input does not decide the principal, permission policy, or revocation race outcome.

## Decision state and next gate

The response fields in this historical review remain **UNRESOLVED** until the relevant owner records a response and rationale. The requester-authorized decision record establishes the implementation baseline for the questions it covers; it does not imply cross-team acceptance or authorize guessing at technical details it does not specify.

Before Evidence schema or producer-adapter work begins, record producer confirmation for PR-01–PR-07, Product/Security decisions for PS-01–PS-05, and Storage agreement for ST-01–ST-03. Then reconcile any rejected or modified defaults into v0.3 and derive deterministic acceptance tests. Persistence-free documentation, serialization-library inventory, and provisional transaction/race test design may proceed, but must remain non-normative until the relevant decisions are recorded.
