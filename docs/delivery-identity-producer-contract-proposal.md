# Celestin Delivery Identity and Producer Contract — Proposal

**Status: PROPOSAL PENDING EXPLICIT APPROVAL.** This document supplements but
does not amend [`SHARED_MEMORY_INTERFACE_CONTRACT_V0.3_DECISIONS.md`](SHARED_MEMORY_INTERFACE_CONTRACT_V0.3_DECISIONS.md).
That requester-authorized decision record remains authoritative for rules it
settles. None of the additional delivery-equality or producer-lifecycle choices
below is approved by this proposal, and no teammate acceptance is asserted.

## 1. Scope and source-backed baseline

This proposal defines a candidate ingress boundary for review. It does not add
storage, migrations, authentication, an MCP route, or a Capture adapter.

| Area | Verified in the current repository | Limit |
|---|---|---|
| Capture request | `CaptureInput` in `src/orbit/capture/model.py` carries `project_id`, `source_type`, `source_id`, content, optional source timestamp/revision, and evidence. Structured content is a tuple of `CaptureEvent` values. | It has no producer identity, delivery ID, candidate revision, or occurrence ID. One request may produce multiple candidates. |
| Candidate ID | `KnowledgeCapturePipeline.capture` in `src/orbit/capture/service.py` calls `_candidate_identity(project, category, claim, conflict_key)`. That helper hashes the project, category, NFKC/case-folded whitespace-collapsed claim, and normalized conflict key; it uses the first 24 hex characters. | It is a local fingerprint, has no producer namespace, changes with material claim/category/key changes, and does not prove producer identity. It is not the approved producer candidate ID contract. |
| Candidate revision | `KnowledgeCandidate` has one `candidate_id`, `created_at`, and mutable `updated_at`; it has no revision number or immutable revision reference. `edit_candidate` retains the ID and changes the claim. | The prototype does not implement the approved immutable-revision lifecycle. |
| Candidate storage | `CaptureCandidateRepository` has `get/list/put`; the default `InMemoryCaptureRepository` is process-local. | No durable producer/revision/delivery ledger exists. |
| Retry | `test_approved_candidate_identical_retry_preserves_review_and_is_idempotent` tests replaying a Capture input against the same in-memory fingerprint candidate. | This is not delivery-ID retry behavior, durable idempotency, or concurrent retry coverage. |
| Occurrences | Capture source/evidence values are tuples, but `capture` uses `_unique_sources` and `_unique_evidence`, which deduplicate and sort them. `evidence_sources_for` also returns deduplicated evidence. | Original order and duplicate occurrences are lost; there is no stable occurrence ID or explicit occurrence-to-evidence link in Capture. |
| Source metadata | `EvidenceSource` has evidence type, source ID, locator, revision, and content hash. `CaptureSourceReference` has source type, source ID, source timestamp, revision, and locator. | There is no source namespace field or shared identity crosswalk. The source timestamp is on the capture reference, not each evidence item. |
| Approval | `KnowledgeCandidate.reviewed_by` is a string. `approve` accepts a caller-provided `reviewer_id`; the queue is local and has no authenticated principal integration. | The string is display/provenance metadata only, not authenticated approval. The final decision record's authenticated approval policy is not wired to this prototype. |
| Conflict | Capture uses `conflict_key` to group candidates and stores `conflicts_with` as candidate-ID strings. | No producer-qualified endpoint, candidate revision endpoint, source identity, or persistent conflict declaration exists in Capture. |
| Canonicalization | `src/orbit/evidence/canonicalization.py` implements the final record's conversion profile and keeps knowledge-content hashing separate. Its tests contain the two approved conversion vectors. | It intentionally does not define delivery equality, candidate revision equality, producer authentication, or persistence. |

The checked-in Resurrection boundary review reports no authoritative external
producer package/interface or fixture in this repository. It is a local
boundary review, not evidence that an upstream producer already emits the
required fields. No named owner is assigned in the checked-in sources.

Relevant evidence: `CaptureInput`, `CaptureEvent`, `CaptureSourceReference`,
`KnowledgeCandidate`, and `CandidateCertainty` in
`src/orbit/capture/model.py`; `CaptureCandidateRepository`,
`InMemoryCaptureRepository`, `KnowledgeCapturePipeline.capture`,
`edit_candidate`, `approve`, `_link_conflicts`, `evidence_sources_for`,
`capture_provenance_for`, `_candidate_identity`, `_unique_sources`, and
`_unique_evidence` in `src/orbit/capture/service.py`; `EvidenceSource`,
`EvidenceRecord`, and `CaptureKnowledgeProvenance` in
`src/orbit/evidence/model.py`; examples and tests in
`tests/capture/test_pipeline.py`; and the limitations in
`docs/RESURRECTION_INTERFACE_BOUNDARY_REVIEW.md`.

## 2. Keep the five identity boundaries separate

| Identity | Current approved v0.3 rule | Authority and current gap | Retry/conflict behavior |
|---|---|---|---|
| Delivery | Key is `(project_id, producer_id, delivery_id)`; `delivery_id` is producer-generated and unique within that scope. | The record settles key scope and producer generation, but not normalized delivery fields, hash profile, or whether a delivery contains one revision or a batch. Authentication must bind the producer to the project; no such Capture integration exists. | Same key and same normalized delivery returns its original receipt; same key with different normalized content yields `idempotency_conflict`, no mutation. Exact equality details remain this proposal's open question. |
| Candidate | Key is `(project_id, producer_id, candidate_id)`; ID is opaque and case-sensitive. | The final record settles namespace and comparison behavior, but does not explicitly say who assigns `candidate_id`. This proposal recommends the active registered producer assigns a stable ID; that issuer choice needs confirmation. | Same candidate ID remains stable as revisions advance. Do not use a content fingerprint as its identity. |
| Candidate revision | Key is `(project_id, producer_id, candidate_id, candidate_revision)`; positive integer starts at 1 and advances by 1; content, occurrences, and conflict declarations are immutable. | The final record settles key and sequence. Who assigns/submits the integer is not explicit; this proposal recommends producer-submitted revisions validated by the server. | Same revision and same immutable payload is a no-op; changed immutable content is `revision_conflict`. A correction uses the next revision and must be approved again. |
| Conversion | At most one successful conversion per candidate revision, keyed by the candidate-revision tuple above. | Approved by the final record. Conversion is not a delivery and its hash is not a delivery hash. | Exact authorized replay returns the original conversion/knowledge/evidence IDs. Authorization must precede any replay lookup or disclosure. |
| Payload digest | Conversion uses `smic-conversion-payload-v1+jcs-rfc8785+sha256`, with direct SHA-256 of canonical UTF-8 JCS bytes and lowercase hex. | Settled for conversion only. The helper implements it; no durable conversion uses it yet. This profile must not be reused for delivery or candidate-revision equality. | Each identity operation compares only its own versioned payload digest (or equivalent immutable fields). A digest is not an identity key. |

**PROPOSED:** introduce separate `smic-delivery-payload-v1` and
`smic-candidate-revision-payload-v1` equality profiles, each with a
domain-separated SHA-256 preimage. Keep the already approved conversion profile
unchanged. Even if delivery and revision equality use the same normalized
content projection, separate profile identifiers and domain separation prevent
one operation's digest from being treated as another's proof.

## 3. Proposed delivery equality contract

Everything in this section is **PROPOSED — PENDING APPROVAL**. It does not
extend the final decision record's approved conversion-payload field set.

### 3.1 Cardinality and normalized payload

**PROPOSED:** v1 accepts exactly one candidate revision per delivery. A batch
producer must submit one delivery ID for each candidate revision. This keeps a
delivery receipt and candidate-revision outcome unambiguous. The current
`CaptureInput` can hold many events, so a future adapter must split only after
the producer confirms how each event receives a stable candidate ID, revision,
and delivery ID. If Resurrection requires atomic batches, that is a separate
contract decision: the batch's candidate ordering, all-or-nothing semantics,
per-candidate results, and partial rejection behavior must be specified first.

**PROPOSED** normalized delivery equality object (exact fields):

```json
{
  "candidate_created_at": "2026-10-19T12:00:00.000000Z",
  "candidate_id": "c-1",
  "candidate_revision": 1,
  "category": "decision",
  "certainty": "medium",
  "claim": "Use UTC.",
  "conflicts": [],
  "evidence_occurrences": [],
  "producer_id": "agent-a",
  "project_id": "p1",
  "schema_version": 1
}
```

The exact object shape above is a proposal, not a producer interface already
implemented. `project_id` and `producer_id` in the canonical object come from
trusted server-side scope and the authenticated producer registration; they
must not be trusted from request fields. If a wire payload includes either
value, the boundary validates it against trusted scope or ignores/rejects it.

| Field group | Proposed equality treatment |
|---|---|
| `schema_version`, project, producer, candidate ID, candidate revision | Included. Project/producer values are trusted-boundary values. Candidate/revision values are opaque, case-sensitive identifiers/sequence from the producer contract. |
| Category, certainty, claim, candidate-created timestamp | Included as immutable revision content. Enums must use approved lowercase values; certainty's allowed values and any local crosswalk remain unresolved. Claim whitespace/case/punctuation are preserved after NFC. |
| Evidence occurrences | Included in producer ordinal order, with duplicates preserved. Each object has exactly `content_hash`, `evidence_type`, `locator`, `ordinal`, `source_id`, `source_namespace`, `source_occurred_at`, and `source_revision`. Optional values are explicit `null`. |
| Conflicts | Included as endpoint objects with exactly `producer_id`, `candidate_id`, and `candidate_revision`, interpreted in the same project. Sort/deduplicate according to the approved undirected-edge rule and proposed portable comparator below. |
| `delivery_id` | **Excluded from payload bytes**; it is the delivery-identity key. The key selects which stored delivery digest to compare. |
| `original_claim` | Excluded from producer delivery under this proposal. It is a review/correction-history value in the local prototype and appears in the conversion payload. Whether it is derived from a prior immutable revision or separately supplied is unresolved; never silently synthesize it. |
| `knowledge_type` | Excluded; derive from the approved category mapping. Unknown/unmapped categories reject. |
| Approval, reviewer, review note, verification decision | Excluded from producer delivery. Approval is a separately authenticated, revision-bound application event; caller-supplied reviewer labels are not trusted. App-owned verification starts `unverified`. |
| Transport and server fields | Excluded: credential/token, request IDs unrelated to `delivery_id`, retry counters, network headers, receive/commit timestamps, generated database IDs, current source-resolution/freshness observations, and response metadata. |

The producer's source timestamp is provenance, not ordering. The timestamp
normalization below affects equality only; source freshness checks and server
receive time are excluded. The receipt should retain a stable accepted outcome
and original assigned IDs, but the exact receipt fields remain an API/storage
decision; no public MCP response is changed by this proposal.

### 3.2 Proposed normalization and hash profile

**PROPOSED:**

1. Require the exact field set above and exact nested object field sets. Optional
   values must be present as JSON `null`; omission is invalid, not equivalent
   to `null`.
2. Normalize every string value to Unicode NFC. Preserve whitespace, case, and
   punctuation; do not trim or case-fold IDs, claims, locators, source revisions,
   or namespace names.
3. Normalize supported timestamps to RFC 3339 UTC with exactly six fractional
   digits and terminal `Z`. Equivalent offsets represent equal instants.
4. Require occurrence ordinals to be contiguous `0..n-1`; sort by ordinal and
   retain repeated, byte-equivalent occurrence rows. Never deduplicate an
   occurrence because its Evidence record can be shared.
5. Sort conflict endpoints by `(producer_id UTF-8 bytes, candidate_id UTF-8
   bytes, numeric candidate_revision)` after NFC; treat a repeated unordered
   edge once, consistent with the final decision record. Do not use locale
   collation or language-default string ordering.
6. Permit JSON integers only in the interoperable safe range
   `-(2^53)+1 .. (2^53)-1`; reject floats, booleans-as-integers, and integers
   outside that range. This makes the JCS numeric domain explicit.
7. Canonicalize with RFC 8785 JCS. For each operation, calculate
   `SHA-256(ASCII(profile_id) || 0x00 || JCS_UTF8_bytes)` and store the
   lowercase hexadecimal digest with the exact profile ID. Proposed profile
   IDs are `smic-delivery-payload-v1+jcs-rfc8785+sha256-domain-separated` and
   `smic-candidate-revision-payload-v1+jcs-rfc8785+sha256-domain-separated`.
   Do not change the approved conversion profile's direct-hash rule.

The safe integer range, UTF-8 tuple comparator, and domain-separated delivery
profiles are additional proposals. The current helper rejects integers outside
the JCS implementation's safe domain but has not separately documented a
contract bound; its Python tuple sort uses Python string order, not the explicit
cross-language comparator above.

### 3.3 Proposed retry and transaction behavior

The authenticated project/producer operation is resolved before any lookup
which could reveal a receipt, candidate, or digest. All checks and writes then
share one transaction. `delivery_id` and candidate-revision uniqueness are
enforced independently.

1. Validate trusted producer/project binding and current submit permission.
   Reject a payload attempting to act as another project or producer.
2. Validate/normalize the exact payload and compute its delivery digest and
   revision digest under distinct profile IDs.
3. Look up `(project_id, producer_id, delivery_id)` only after authorization.
   Equal version and digest return its stored receipt unchanged. A different
   version/digest returns `idempotency_conflict` without mutation.
4. If the delivery key is new, look up the candidate-revision key. If its
   immutable revision digest matches, do not rewrite the revision; atomically
   record a receipt for this newly accepted delivery pointing to the existing
   revision. If the digest differs, return `revision_conflict` without mutation.
5. Otherwise validate that this is revision 1 or exactly the next revision.
   A gap is rejected until the missing prior revision arrives. Atomically
   persist the immutable revision, distinct occurrence rows/links, and accepted
   delivery receipt.
6. Commit once. Any failure before commit rolls back every row and leaves no
   partial accepted delivery. A retry after an uncertain commit uses the same
   delivery key; the committed receipt is authoritative.

**PROPOSED reservation rule:** only successfully accepted deliveries reserve a
delivery ID. Authentication/validation failures, revision gaps/conflicts, and
transaction failures create no receipt and reserve no ID. A later retry using
the same ID and same content may succeed when the precondition changes (for
example, after a missing earlier revision arrives). This is not in the final
decision record and must be explicitly accepted; alternatively the team may
choose durable terminal receipts for selected deterministic rejections.

**Concurrent behavior:** a unique key serializes same-delivery contenders. The
loser rechecks current authorization before reading the winner, compares the
delivery version/digest, then returns exact replay or `idempotency_conflict`.
Different delivery IDs concurrently targeting one revision serialize on the
candidate-revision key; matching revision content is a no-op plus a receipt,
changed content is `revision_conflict`. Concurrent revision advancement also
requires locking the candidate's revision head so two writers cannot both
accept the same next integer. The final decision record does not yet specify
delivery-ledger locking/receipt field details.

### 3.4 Proposed golden vectors (pending approval)

These are proposed delivery/revision vectors, not additions to the authoritative
conversion vectors in the final decision record. They define candidate test
inputs for owner review; they are not evidence that the current helper supports
these profiles. `delivery_id` is shown as the lookup key and is deliberately
absent from the canonical payload bytes.

The base normalized payload is:

```json
{"candidate_created_at":"2026-10-19T12:00:00.000000Z","candidate_id":"c-1","candidate_revision":1,"category":"decision","certainty":"medium","claim":"Café.","conflicts":[],"evidence_occurrences":[{"content_hash":null,"evidence_type":"repository","locator":null,"ordinal":0,"source_id":"repo-1","source_namespace":"git","source_occurred_at":null,"source_revision":null}],"producer_id":"agent-a","project_id":"p1","schema_version":1}
```

Its proposed delivery-profile digest is
`c7ccdf252c4a3fcdde32091fdb51f8993aedcaff7977a4e1e706607e9e4d0d11`;
its proposed candidate-revision-profile digest is
`6a558ff8f221706cb5b35b8030e654ed31e3cfc40c148be65c6b33498eec9628`.
Each digest is lowercase hex for
`SHA-256(ASCII(profile_id) || 0x00 || JCS_UTF8(payload))` using that
profile's ID from section 3.2.

| Vector | Proposed input variation | Expected contract result |
|---|---|---|
| Exact retry | Key `(p1, agent-a, del-1)` is submitted again with the base payload. | Same normalized bytes and digest; return the original accepted receipt and IDs without a second write. |
| Changed payload under same delivery key | Keep key `(p1, agent-a, del-1)` and change `claim` to `Café uses UTC.`. | Proposed delivery digest `7e00cea743c448e805baa6deb1af67d37d6efd6679a20453eeadf0e17faacfd9`; return `idempotency_conflict`, with no mutation. |
| New delivery, same revision content | Use a different delivery ID with the base payload. | Delivery lookup key is new; the candidate-revision digest equals the base revision digest, so record a receipt for the new delivery without rewriting the revision. |
| Explicit null versus omitted optional value | Base payload explicitly has `locator: null`. Remove the `locator` property in the comparison input. | Explicit `null` is part of the base canonical bytes and digest. Omission fails exact-shape validation before a digest is produced; it is not treated as null. |
| Unicode normalization | Replace `Café.` with the canonically equivalent decomposed string `Cafe\u0301.` before NFC. | Both inputs normalize to the base payload and the same proposed delivery digest. No whitespace trimming or case folding is implied. |
| Equivalent timestamp offset | Supply `candidate_created_at` as `2026-10-19T17:30:00+05:30`. | Normalize to `2026-10-19T12:00:00.000000Z`; the resulting payload and digest equal the base vector. |
| Timestamp precision beyond microseconds | Use `2026-10-19T12:00:00.1234567Z`. | Reject before hashing; no rounding or truncation. |
| Leap second | Use `2026-10-19T12:00:60Z`. | Reject before hashing; do not map it to another instant. |
| Unknown local offset | Use `2026-10-19T12:00:00-00:00`. | Reject before hashing because it does not identify a known UTC instant. |

The digest values above were calculated in a prior scratch run using the
temporary `rfc8785` package, then re-derived in this review with Python's
standard-library JSON encoder for this restricted payload (ASCII keys, simple
strings, integers, arrays, and null only). The default Python environment and
the repository validation venv could not import `rfc8785` during this review;
the standard-library re-derivation is not a general RFC 8785 implementation.
No second runtime has checked these values. Before approval, the storage /
canonicalization owner must verify every byte and digest with the selected JCS
implementation and publish the vectors as shared fixtures. They remain
provisional until then.

## 4. Canonicalization decisions and current Capture incompatibilities

| Question | Current implementation | **PROPOSED** rule | Required proof / owner |
|---|---|---|---|
| More than six timestamp fractional digits | Conversion helper rejects them because the UTC six-digit rule cannot represent them losslessly. | Reject in v1; never round or truncate silently. Producer must send at most microsecond precision. | Producer confirms its timestamp precision. Add rejection vectors for 7 and more digits. |
| Leap seconds | Python `datetime.fromisoformat` rejects `:60`. | Reject in v1 rather than invent an adjacent-second mapping. | Producer/Resurrection accepts; add explicit reject vector. |
| RFC 3339 `-00:00` | Helper rejects the unknown-local-offset marker because it cannot truthfully normalize it to a UTC instant. | Reject where canonical UTC instant is required; if source wants to preserve unknown-offset provenance, it must use a separate representation outside the canonical instant. | Producer/source owner confirms; add explicit reject vector. |
| Integer range | JCS library rejects values outside its interoperable numeric range; current tests reject `2^53`. | Make the safe integer interval explicit and reject outside it. No float coercion or large-ID-as-number. | Storage/canonicalization owner accepts range; add lower/upper boundary vectors. |
| Conflict tuple ordering | Helper sorts with Python tuple/string ordering. JCS does not define ordering of array elements. | Compare NFC strings by UTF-8 byte sequence, then revision as integer; no locale/default-runtime ordering. | Producer and storage implementations agree; add supplementary-plane/BMP and numeric-revision vectors in at least two runtimes. |
| Certainty | `CandidateCertainty` in `src/orbit/capture/model.py` is `unqualified` or `uncertain`; approved conversion vectors use `medium` and `high`. The canonical helper checks lowercase but does not restrict to an agreed value set. | Do not guess a semantic crosswalk. Define one shared enum/mapping; reject conversion of unmapped values until it exists. | Capture/Resurrection and Evidence owners approve meanings and mapping; add all-values mapping/rejection tests. |
| Source timestamp representation | Capture validates that `source_timestamp` parses and has a timezone, then carries the string; it does not apply the canonical helper's six-digit UTC formatting. | Canonical equality uses normalized UTC instant; whether the raw lexical form is retained separately for audit remains a source-owner decision. | Producer/source owner clarifies event-time semantics; test equivalent offsets and raw-provenance preservation if required. |
| Source namespace and ID | Capture has `CaptureSourceType` and Evidence `EvidenceType`; neither is `source_namespace`. Source IDs are stripped by input validation. | Keep source type classification distinct from a registered namespace; preserve opaque source IDs under the final NFC/no-trim rule. Define a crosswalk only with owner approval. | Producer/source owner supplies namespace registry and identifier rules; add whitespace/case/namespace isolation tests. |
| Occurrence multiplicity | `_unique_sources` and `_unique_evidence` turn values into sets and sort them. | Preserve every occurrence by its stable zero-based ordinal; deduplicate only the separate Evidence record according to the final record's evidence key. | Capture adapter tests must prove identical source occurrences remain distinct and ordered. |

An unresolved `certainty` mapping is a concrete producer-adapter blocker: the
Capture enum cannot be safely translated into the conversion vector vocabulary
from current repository evidence. The helper's acceptance of arbitrary
lowercase certainty strings is not a semantic mapping.

## 5. Producer and source-namespace lifecycle proposal

| Lifecycle concern | Repository/decision-record status | **PROPOSED — PENDING APPROVAL** |
|---|---|---|
| Producer assignment | The final record requires a stable project-registered `producer_id` derived by the trusted application from authenticated producer/session evidence. No authenticator or registry is present in the Capture path. | A project administrator registers a non-recycled producer ID and binds an authenticated service credential/transport subject to it. Ingress obtains the ID from that trusted binding, never from an untrusted body field. |
| Candidate ID issuer | The final record defines opaque, case-sensitive project/producer-scoped uniqueness; issuer is not explicit. Capture instead creates a content fingerprint. | The producer assigns a stable opaque candidate ID and reuses it for every revision. The server rejects collisions within the registered producer scope. Do not derive identity from claim text. |
| Revision issuer | The final record specifies contiguous positive revisions, immutable snapshots, and a new revision for corrections; it does not say whether the integer is assigned by producer or server. | Producer submits the next revision; server verifies against the stored head under a lock. A material edit is a new revision, never an update to an accepted row. |
| Producer permissions | The final record says producer identity is authenticated and the payload cannot claim another producer. The concrete credential/capability mechanism is absent. | A credential may submit only for its registered producer and active project, and only within configured source namespaces. It cannot approve its own candidate; the final approval policy requires a distinct authenticated member with `candidate:approve`. |
| Namespace registration | The final record requires a stable project-registered `source_namespace`; no registry/interface is implemented. | Project administration registers namespace names and authorized producer bindings. Source IDs are opaque within `(project_id, source_namespace)`; revision is opaque and not assumed sortable. Validate each occurrence against the active registry. |
| Producer retirement | No Capture/producer lifecycle state exists. | Retirement disables new deliveries and new revisions, never deletes or rewrites accepted history, and never recycles the ID. Whether still-pending historical candidates may be approved/converted after producer retirement requires Product/Security confirmation. |
| Namespace retirement | No source-namespace lifecycle exists. | Stop new references to a retired namespace while retaining old evidence and occurrences unchanged. Whether reactivation is allowed and how an unavailable source affects verification/retrieval remain open. |
| Occurrences | The final record defines `(project, producer, candidate, revision, ordinal)` and duplicate preservation. Capture deduplicates/sorts and has no occurrence ID. | Use that tuple as occurrence identity; do not add random occurrence IDs unless retrieval/storage establishes a need. Give every submitted event an ordinal before any mapping/deduplication. |

The trusted `project_id` and `producer_id` must be derived before the request is
given to a domain service. A project selector or reviewer display string is
not identity. The current stdio Capture prototype lacks per-producer
authentication and must not be presented as satisfying this producer boundary.

## 6. Additive domain boundary (proposal only)

No code DTO was added because the producer's ID issuer, delivery cardinality,
certainty vocabulary, and credential binding are not fully settled. After
approval, a narrow internal port could separate trusted identity from untrusted
delivery content:

```python
@dataclass(frozen=True)
class AuthenticatedProducerContext:
    project_id: str                 # server-resolved project scope
    producer_id: str                # registry-bound authenticated producer
    allowed_source_namespaces: frozenset[str]

@dataclass(frozen=True)
class CandidateRevisionDelivery:
    delivery_id: str                # producer-generated retry key
    candidate_id: str               # opaque stable producer candidate key
    candidate_revision: int         # immutable revision sequence
    category: str
    certainty: str
    claim: str
    candidate_created_at: str
    evidence_occurrences: tuple["EvidenceOccurrenceInput", ...]
    conflicts: tuple["CandidateRevisionRef", ...]

class CandidateDeliveryIngestor(Protocol):
    def accept(
        self,
        producer: AuthenticatedProducerContext,
        delivery: CandidateRevisionDelivery,
    ) -> "DeliveryReceipt": ...
```

These are review shapes only, not current source interfaces. A `DeliveryReceipt`
would refer to the accepted delivery and candidate revision and be stable on
authorized replay. It must not expose whether an unauthorized key exists.
Context construction belongs to a trusted application/transport boundary; a
Python dataclass alone is not a security boundary. Keep this internal and do
not change MCP tool arguments, responses, or public errors.

## 7. Required acceptance tests before producer integration

All items below are **future requirements**, not tests already implemented or
passed:

1. Authenticated transport maps to exactly one active `(project, producer)`;
   forged body project/producer values cannot change it; unknown, retired, or
   unauthorized producer is rejected before receipt lookup.
2. Candidate IDs from two producers can have the same text without collision;
   the same producer/project cannot create the same candidate ID twice as a
   different candidate.
3. Revision 1 then revision 2 yields immutable snapshots. A correction does
   not overwrite revision 1; approval of revision 1 does not authorize
   revision 2. A gap is rejected and can later be retried under the same
   unreserved delivery ID.
4. Same delivery key + exact normalized payload returns the byte/field-identical
   stored receipt and original candidate IDs without another write.
5. Same delivery key + changed candidate, revision, content, occurrence, or
   conflict returns `idempotency_conflict` and changes nothing.
6. Different delivery keys targeting the same revision with identical content
   preserve one revision and produce stable accepted receipts; different
   content returns `revision_conflict`.
7. Concurrent identical deliveries converge to one accepted receipt; concurrent
   changed payloads produce one winner and one conflict, with no partial rows.
8. Inject failure after each persistence stage; no accepted delivery, candidate
   revision, occurrence, or link remains after rollback. Same-key retry after
   an uncertain commit returns the committed receipt or safely accepts after
   rollback.
9. Occurrences with identical source fields remain separate ordered ordinals;
   several occurrences can link to one Evidence record; duplicate declarations
   of one conflict edge follow the approved canonical representation.
10. Fixed vectors cover delivery vs revision domain-separated digests, exact
    retries, changed payloads, explicit-null vs omitted fields, composed vs
    decomposed Unicode, offset-equivalent timestamps, invalid timestamp edge
    cases, safe integer bounds, and cross-runtime conflict endpoint order.
11. Producer/namespace retirement blocks new delivery without altering history;
    tests for pending-candidate approval/convertability follow the product
    decision.
12. Current approval policy tests require authenticated, active project
    membership, `candidate:approve`, and a reviewer distinct from the producer;
    local caller-supplied reviewer labels cannot pass as authentication.

## 8. Owner decision packet

The groups below come from the checked-in contract roles; no named accountable
individual appears in the repository. Record each response as **ACCEPT**,
**REJECT**, or **MODIFY**, with rationale. These proposal IDs are local to this
packet and do not add entries to or change the final decision record.

### Producer / Resurrection

| Proposal ID | Decision requested | Proposed default — not approved | If different |
|---|---|---|---|
| D-01 | Is each delivery exactly one candidate revision, or can a delivery be an ordered batch? Who issues candidate IDs and revision numbers? | One revision per delivery; producer issues stable opaque candidate ID and next positive revision; server enforces scope and sequence. | Batch atomicity changes payload arrays, transaction granularity, receipt shape, and concurrency tests. Server-issued identities change producer retry responsibility and source mapping. |
| D-02 | Does the producer supply a stable delivery ID, and does it reuse the same ID for all retries? | Yes; new ID per logical delivery, same ID for retries; ID is scoped to authenticated project and producer. | No stable ID means at-least-once delivery cannot return a stable receipt without another upstream key. |
| D-03 | Are `certainty`, `category`, and candidate timestamps producer-owned values? What exact certainty vocabulary and mapping applies? | Producer submits contract enums; unsupported local values fail closed until explicit mapping. | A different mapping changes canonical bytes, vectors, source DTOs, and which Capture candidates are convertible. |
| D-04 | What guarantees source namespace, source ID, source revision, timestamp, locator, and content-hash fields? Which producer may use each namespace? | Namespaces are registered per project and allowlisted per producer; IDs/revisions remain opaque and namespaced. | A looser namespace affects evidence deduplication, project isolation, and freshness checks; a different timestamp profile affects equality. |
| D-05 | Must source occurrence duplicates and order be preserved, and can one evidence identity be referenced by many occurrences? | Yes; ordinal is stable occurrence identity; evidence records may be shared but occurrences are never collapsed. | Deduplication or order-insensitive semantics change the revision payload hash and provenance reconstruction. |
| D-06 | Are conflicts authored endpoint declarations or computed grouping hints? What exact producer and candidate-revision endpoint identity is provided? | Authored typed candidate-revision endpoint; symmetric graph is derived/stored once per final decision record. | Grouping hints need distinct fields and must not be persisted as authored relationships. |
| D-07 | What happens to pending revisions, namespaces, and source references when a producer or namespace is retired? | Disable new submission, retain all history, do not recycle IDs; historical review behavior needs the product decision below. | Deletion/reassignment breaks foreign keys, provenance, audit, and retry results. |

### Product / Security / Storage

| Proposal ID | Decision requested | Proposed default — not approved | If different |
|---|---|---|---|
| D-08 | Accept delivery/revision canonical projections, profile IDs, domain-separated hash preimages, explicit-null rules, success-only delivery-ID reservation, and proposed vectors? | Accept the exact field list and rules in sections 3.1–3.4, or return a modified field-by-field profile. | Changes require new vectors and version IDs; identity indexes and receipt rows cannot be finalized yet. |
| D-09 | Accept reject-in-v1 rules for >6 fractional digits, leap seconds, `-00:00`, and integers outside the JCS safe interval? | Reject all explicitly; never silently round, truncate, or coerce. | Different choices require a new timestamp/numeric representation and cross-runtime vectors. |
| D-10 | Accept UTF-8 bytewise ordering for conflict endpoint string components and integer ordering for revision? | Yes, after NFC, no locale ordering. | Other comparators require common implementation and golden vectors in each supported runtime. |
| D-11 | How does the authenticated transport bind principals to registered producers and source namespaces; who can register/retire those rows? | Trusted server-side registry; never trust IDs in tool/request body. | No approved binding means no producer-facing adapter can securely accept deliveries. |
| D-12 | Should only successfully accepted deliveries reserve IDs, with auth/validation/gap/transaction failures unreserved? | Yes; accepted receipt + revision + occurrences commit together; rejected attempts leave no receipt. | Durable rejection receipts affect privacy, retry-after-state-change behavior, retention, and no-enumeration rules. |
| D-13 | How is `original_claim` populated for a corrected revision; can pending historical revisions still be approved/converted after producer retirement? | Derive correction lineage from immutable prior revision; never overwrite. Keep retired history, but block new producer writes; product separately decides pending review/conversion. | Different handling changes conversion payload values, history/retrieval, and lifecycle authorization tests. |

The final decision record already sets the approval baseline: authenticated,
active project member with `candidate:approve`, reviewer differs from producer,
and conversion/replay authorization is checked transactionally before result
disclosure. This proposal does not reopen those approved rules. The missing
piece is a real authenticator and producer registration/permission integration;
the Capture `reviewer_id` string is not that integration.

## 9. Implementation gate and sequencing

1. Resurrection confirms candidate/delivery cardinality, ID issuer, revision
   issuer, source occurrence and namespace guarantees, source time/revision
   meaning, and certainty mapping.
2. Product/Security confirms producer authentication/registration, producer
   retirement, and pending historical review/conversion behavior. Approval and
   revocation remain governed by the final decision record unless an authorized
   owner changes it.
3. Storage/canonicalization owners accept delivery and candidate-revision
   payload fields, independent profiles, exact failure reservation semantics,
   timestamp/numeric edge rules, portable conflict ordering, and golden vectors.
4. Only then freeze typed domain DTOs/ports and write producer adapter contract
   tests. Implement schema, transaction logic, and persistence in a separate
   reviewed milestone after the DTO contract passes vectors.
5. Connect transport authentication and request-scoped authorization only after
   a verified producer binding exists. Keep current MCP request/response/error
   schemas unchanged unless a separate explicit public API decision is made.

**GO/NO-GO:** No-go for a producer-facing adapter, delivery/revision schema, or
durable equality implementation until D-01 through D-13 receive explicit
responses (some may be combined with a recorded rationale). Persistence-free
review of the DTO shape and test-vector harness may continue. No PostgreSQL
schema, migration, Capture model, or runtime behavior is changed by this
proposal.
