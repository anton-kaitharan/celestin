# Evidence-linked project memory: domain contract

## Status

This document began as a storage-neutral domain and service prototype. The
current `EvidenceLinkedMemoryStore` port has a request-bound PostgreSQL
implementation in `src/orbit/evidence/postgres.py`, with schema in migrations
`0005_evidence_linked_memory.sql` and `0006_candidate_revision_store.sql`.
The adapter is exposed through `PostgresRequestUnitOfWork`; it is not wired to
the SQLite MCP runtime, and there is no SQLite Evidence adapter. Its behavior
has scripted coverage, but PostgreSQL migrations and adapter behavior have not
yet been verified against a live server.

The original `MemoryStore` message/decision port and its persistence behavior
remain separate. The PostgreSQL Evidence adapter does not implement Capture
conversion, delivery receipts, hosted MCP selection, or external credential
authentication.

The service accepts an `AuthorizedProject` as a project/action selector and
re-authorizes each operation through an injected `ProjectAuthorizer`. The
trusted service factory fixes the principal on the service instance; the
principal carried by a caller-supplied scope is not used for authorization.
This is a domain guard, not transport authentication: untrusted callers must
not be allowed to construct a service with a permissive authorizer or choose
its principal. No Resurrection Layer operation is currently exposed through
MCP. Hosted integration must supply the trusted principal and authorizer at
the application boundary and continue checking membership on every request.

## Domain records

`KnowledgeRecord` stores a concise claim, project scope, knowledge type,
timestamps, verification status, lifecycle status, and explicit predecessor /
successor IDs. `EvidenceRecord` stores a stable ID and project-scoped reference
to a message, repository file, commit, test result, document, or other source.
It records a locator, optional revision and content hash, capture time,
resolution state, verification status, and optional check metadata. Source
contents are not copied into the evidence record.

Verification (`unverified`, `verified`, `stale`, `conflicting`) is separate from
lifecycle (`current`, `superseded`, `archived`). New claims and evidence begin
unverified. Setting a checked status requires an explicit `checked_by` value;
this records the caller's check and does not prove that the check occurred.
The service never marks evidence verified automatically. A source that a
resolver cannot check remains `unresolved`; a source the resolver confirms is
missing is rejected before persistence. Readback re-resolves source
availability, but availability alone does not establish freshness or truth.
The service rejects evidence rows returned by a storage adapter when their
project differs from the authorized read scope. Project-state reconstruction
also omits out-of-scope evidence from a supplied bundle and reports that
limitation, so a faulty read adapter cannot expose a foreign source reference
through the snapshot.

Conflicting claims remain separate records. Supersession creates a new record
and asks the persistence adapter to mark the previous record as superseded in
the same atomic operation. It does not delete the old claim or its evidence.
The service does not choose a source of truth by timestamp; that requires
project-specific authority policy and evidence review.

## Capability 3 conversion contract (domain prototype)

`KnowledgeRecord.capture_provenance` is an optional storage-neutral
`CaptureKnowledgeProvenance` value. `capture_provenance_for(candidate,
target_project=...)` creates it only for an approved, unverified candidate in
the matching project. This is a domain project check, not authentication. The
normal `KnowledgeRecord.verification_status` remains `unverified` at creation;
reviewer labels are untrusted provenance and are never copied into
`checked_by`.

| Capability 3 field | Capability 1 representation | Contract semantics |
| --- | --- | --- |
| Candidate ID and project | `capture_provenance.candidate_id/project_id`; `KnowledgeRecord.project` is the destination scope | **Superseded prototype assumption:** the earlier unique `(project, candidate_id)` key and generic replay recommendation are superseded. V0.3 defines candidate identity as `(project_id, producer_id, candidate_id)` and conversion uniqueness as `(project_id, producer_id, candidate_id, candidate_revision)`; conversion retry behavior is governed separately by v0.3. These keys do not define the delivery-payload equality profile or same-revision payload comparison needed for replay/conflict handling; those D-08 details remain unresolved. C1 still validates provenance project against authorized scope. |
| Claim and category | `KnowledgeRecord.claim`, `KnowledgeRecord.knowledge_type`, and provenance `original_claim/category` | Current edited claim is authoritative as `claim`; original text is retained when present. Supported category mappings are decision→decision, discovery→discovery, bug fix→bug fix, completed work→completed work, constraint→requirement, and implementation detail→implementation finding. Incomplete work and blocker have no agreed C1 type and are rejected by the mapping helper. Raw category remains in provenance. |
| Source IDs, source type, locator, revision, occurrence time | Evidence associations are `EvidenceRecord` rows; raw typed origins are `capture_provenance.source_references` | Evidence rows remain authoritative for C1 resolution/verification state. Capture source references preserve original source type and `occurred_at`; their source IDs, locators, and revisions link them to mapped `EvidenceType.OTHER` rows. `EvidenceRecord.captured_at` and `KnowledgeRecord.created_at` are C1 persistence time, distinct from source occurrence and candidate/review timestamps. C2 reports original locator/hash and recorded resolution status. |
| Evidence relationships and IDs | Store associates each `EvidenceRecord` with `knowledge_id`; reconstruction exposes `knowledge_record_id` and `evidence_record_id` | The existing store association is authoritative. Capability 2 carries both generated C1 identifiers in its output. |
| Review status and reviewer provenance | `capture_provenance.review_status`, `reviewed_by`, `reviewed_at`, `review_note` | Only `approved` provenance is accepted. These labels describe a review assertion; they are not authenticated identities and do not imply verification. Candidate creation/update times and original claim are retained in this value too. |
| Verification and checker provenance | `KnowledgeRecord.verification_status`, `checked_by`, `checked_at`, `check_note`; candidate status is retained separately as `candidate_verification_status` | Conversion requires candidate status `unverified` and creates C1 knowledge unverified. A later explicit C1 verification assertion owns the destination checker fields. The C1 service does not authenticate the supplied checker label. C2 carries those assertion fields separately from its own evidence classification. |
| Uncertainty | `capture_provenance.certainty` | Preserved verbatim (`uncertain` or `unqualified`); it is not a confidence score and does not change verification. |
| Conflicts | `capture_provenance.conflicts_with` | Candidate IDs remain linkable across records. No semantic conflict resolution occurs. The adapter must support querying by candidate ID or the conflict IDs are only retained, not conveniently traversable. |
| Lifecycle/supersession | Existing `KnowledgeRecord.lifecycle_status`, `supersedes_id`, `superseded_by_id` | These remain authoritative and separate from capture review. Superseded records retain their own capture provenance and evidence. Capability 3 candidates do not currently express lifecycle transitions. |

This is a domain contract only. Before Capture conversion is persisted,
PostgreSQL/SQLite conversion adapters must agree on a representation that
round-trips `capture_provenance`, preserves source-to-evidence associations,
knowledge/evidence record IDs, checker metadata, lifecycle links, and candidate
idempotency. The current PostgreSQL Evidence adapter implements the base store
port, not Capture conversion or candidate-to-knowledge mapping. No conversion
schema is implied by these dataclasses. An adapter that cannot preserve the
optional value must reject/flag the conversion instead of silently dropping it.

## Storage port required for integration

`EvidenceLinkedMemoryStore` is the minimal storage-neutral service contract in
`src/orbit/evidence/service.py`. An adapter must provide:

- Atomic creation of one knowledge record with its initial evidence records.
- Project-scoped retrieval of a record and all associated evidence.
- Project-scoped evidence association with idempotency for a repeated source
  type, source ID, locator, revision, and content hash.
- Explicit knowledge and evidence verification updates.
- Atomic supersession that preserves the previous record and its evidence.
- Project-scoped identifier checks and transaction/error behavior consistent
  with the existing adapter.

The existing PostgreSQL adapter maps this port to the schema in migration
`0005_evidence_linked_memory.sql` and uses workspace/project scope from the
authorized request unit of work. It has not yet been exercised against a live
PostgreSQL server; schema/runtime behavior therefore remains unverified. There
is no SQLite implementation of this port. The existing decision
`source_message_id` remains a legacy one-source link and cannot represent
multiple sources, independent verification, or supersession history. Reusing
decision upsert behavior would overwrite history and is not suitable.

`EvidenceReferenceResolver` is a second integration seam. The current storage
port has no source-read method, so a source resolver still needs a project-scoped
way to resolve Celestin message IDs. Repository files, commits, tests, and
documents need their own authorized resolver or trusted integration. The
PostgreSQL store can persist the current port directly, but the domain service
cannot be fully composed for source types lacking an authorized resolver.

## Original prototype non-goals

The following boundaries remain in effect; the original statement that no
PostgreSQL Evidence adapter or schema exists has been superseded by migration
0005 and the request-bound adapter. Migration 0006 also adds candidate/revision
storage structures, but does not add Capture conversion:

- No SQLite Evidence adapter, Capture conversion, producer delivery receipts,
  or retry arbitration.
- No automatic source scanning, source-content copying, or claim extraction.
- No automatic conflict resolution, freshness inference, or decision approval.
- No new MCP tool and no change to existing retrieval contracts.
- No claim that a test result or commit proves a feature works.

The deterministic unit tests use a test-only fake store. They exercise domain
validation, status distinctions, scope propagation, idempotent port semantics,
and failure handling; they do not validate database durability or PostgreSQL
behavior. The separate PostgreSQL integration tests are opt-in and require a
fresh, explicitly configured disposable target.

---

## Celestin storage-contract reconciliation review

This section preserves the earlier schema-mapping review. It predates the domain/service prototype described above; use the current domain model as the source of truth and treat these notes as persistence compatibility analysis, not as an approved schema contract.


**Status:** Design proposal for team agreement. This document does not approve a
schema change or implement a persistence adapter. It is based on the seven
requirements shared for the Resurrection Layer; the detailed domain contract
was not present in this checkout when this review was written. Reconcile the
field mapping against that contract before implementation.

## Current Celestin fit

The hosted baseline in `src/orbit/postgres/migrations/0001_hosted_baseline.sql`
stores canonical memory in `project_memory` with a generated database ID,
`workspace_id`, `project_id`, `key`, `value`, decision `status`, one nullable
`source_message_id`, creation/update timestamps, and archive/retention fields.
It has `UNIQUE (project_id, key)` and a same-project source-message foreign key.
Migration `0002_user_status.sql` adds fail-closed user status.

The current `MemoryStore` protocol has message insert, decision save/upsert,
and decision-status update methods. `PostgresMemoryStore.save_decision()`
upserts by `(project_id, key)` and stores one message reference. Neither the
protocol nor this adapter carries candidate identity, structured capture
provenance, multiple typed evidence references, or conflict relationships.
The current decision `status` (`draft`, `approved`, `deprecated`) is not a
verification state.

Accordingly, none of the four requirements below is fully implemented by the
current persistence contract or hosted baseline. Keep the existing `key` as a
domain decision key; do not overload it with candidate identity.

## Recommended semantics for team agreement

### 1. Candidate identity

Treat a candidate ID as the stable identity of one Resurrection Layer
conversion candidate, not as a logical-claim fingerprint and not as a source
event ID. One source event may produce multiple candidates, and separate
candidates may express the same logical claim while carrying different
evidence, uncertainty, or review history. A normalized claim fingerprint can
support later duplicate analysis, but must not be the idempotency key.

Recommended uniqueness is `(project_id, producer_id, candidate_id)` because
candidate IDs are commonly local to the producer that issued them. If the
Resurrection contract guarantees that `candidate_id` is globally unique across
all producers within a project, the team may instead use the requested
`(project_id, candidate_id)` key and retain `producer_id` as provenance. Confirm
that guarantee before choosing the smaller key.

### 2. Idempotency and immutable capture

For a given candidate identity, an identical canonical capture payload returns
the existing knowledge-record ID and the existing evidence-association IDs.
Do not generate replacements on retry.

Compare the complete immutable capture payload, including content, provenance
version, source metadata, uncertainty, conflict declarations, occurrence
timestamps, and review provenance. Exclude only adapter-generated IDs and
server-generated persistence timestamps. A retry with changed content or
reviewed provenance returns a typed conflict and leaves the stored record and
links unchanged. It must not silently upsert over reviewed data. A revised
candidate should use a new candidate identity or a separately agreed explicit
revision operation.

The adapter should use deterministic canonical serialization and a digest for
efficient comparison, while retaining the full payload for equality checks
and reconstruction. A digest alone should not be the only retained provenance.

### 3. Provenance version

Include a positive integer `provenance_version` in the capture envelope. This
versions the serialized provenance shape and its interpretation; it is not a
knowledge-record revision, approval state, or verification result. Readers
must preserve supported versions and fail clearly on an unsupported version
rather than dropping unknown fields. Any format conversion must be an explicit
versioned adapter/migration with round-trip tests.

### 4. Typed evidence references

Represent evidence as typed references, not as `OTHER` with a packed locator.
Each reference should preserve, where applicable:

- evidence/source type and stable source ID;
- locator as its own field;
- source revision;
- occurrence time;
- optional content hash; and
- source-specific metadata needed to reconstruct the domain reference.

Keep `source_id`, `locator`, and `revision` distinct. Preserve the occurrence
instant and define whether exact original timestamp text/offset is required in
addition to a normalized database `TIMESTAMPTZ`. For internal chat-message
evidence, enforce the same-project foreign key. For external evidence, retain a
typed source identity and validate project scope at the application boundary;
do not claim a relational FK to a source system Celestin does not own.

An evidence-association ID must be stable across identical retries. Its
uniqueness key should be based on the knowledge record and a canonical typed
evidence identity, not only the display locator.

### 5. Conflict relationships and resolution history

Recommended default: a conflict is an undirected relationship between two
candidate identities within one project. Persist the pair in canonical order
and enforce uniqueness so reversing the endpoints cannot create a duplicate.
Reject self-links. Preserve the conflict as unresolved unless an explicit,
authorized resolution operation is invoked; capture/conversion must never
resolve it automatically.

A reference to a candidate that has not yet been converted should remain a
typed pending candidate identity, scoped to the same project/producer namespace.
When the other candidate is converted, the relationship can be linked to its
knowledge record without replacing the original candidate reference. The
Resurrection contract must confirm that candidate identities are available
before conversion; otherwise pending references need an explicit registry
design.

Resolution should append an auditable resolution event (outcome, occurrence
time, reviewer/checker provenance, and any rationale) while retaining the
original edge and capture payload. Do not delete or rewrite the original
conflict evidence. The team must confirm whether conflicts are always symmetric
and define allowed resolution outcomes before schema design.

### 6. Atomicity and concurrent retries

Expose one persistence operation for capturing a knowledge candidate and its
provenance/evidence/conflict associations. It must create or reuse the
knowledge record and create/reuse all applicable links in one PostgreSQL
transaction. Any failed validation, evidence insert, or conflict-link insert
rolls back the entire capture.

Use database uniqueness constraints as the concurrency arbiter. When concurrent
requests claim the same candidate identity, one inserts; the other waits,
loads the committed row, compares the canonical payload, and either returns
the same IDs or raises the changed-payload conflict. Unique evidence and
conflict keys prevent duplicate associations. Define bounded retry behavior
for serialization failures/deadlocks; never convert those failures into a
successful result.

The current `PostgresMemoryStore` methods open their own connection contexts.
They cannot be composed as independent calls to guarantee one transaction for
this multi-record capture. The future operation must own one transaction or
accept a transaction-bound storage session.

### 7. Authorization and reviewer provenance

The application boundary supplies the trusted, already-authorized project
scope. A project string or reviewer/checker label supplied by the caller is not
authentication. Keep reviewer identity as provenance exactly as the domain
contract defines it; do not substitute the authenticated `celestin_users`
identity or claim the label has been authenticated.

When hosted identity authorization is integrated, project authorization and
the capture operation must use the same request transaction/connection to
avoid a check-then-write gap. Approval and verification are separate concepts:
the existing decision `status` must not imply that a claim was verified, and a
verification outcome must not silently approve a decision.

## Mapping to a future PostgreSQL migration

Do not edit migrations `0001_hosted_baseline.sql` or `0002_user_status.sql`.
After the domain contract and choices above are approved, the persistence owner
should design an additive migration. At minimum it will need to support:

1. A versioned, lossless capture-provenance payload and candidate identity on
   the knowledge record (with a uniqueness constraint for the agreed scope).
2. Normalized typed evidence associations with stable IDs and uniqueness keys.
3. Queryable, project-scoped conflict identities/edges that can remain pending
   until a candidate is converted, plus append-only resolution history if
   resolutions are included in the first release.
4. Appropriate workspace/project composite foreign keys and indexes, plus a
   clear transaction-bound persistence method.

Existing records must remain readable with absent provenance during an
additive rollout. Do not synthesize candidate IDs or reviewer identities for
legacy decisions. The migration and adapter should be tested with duplicate
requests, payload mismatch, partial-link failure/rollback, concurrent inserts,
pending conflict conversion, and round-trip serialization of every domain
field.

## Decisions to ratify before implementation

1. Are candidate IDs unique only per producer, or globally unique within a
   project? This determines the unique key.
2. Which fields define identical capture payloads, and what operation creates a
   revised candidate after a mismatch?
3. What is the provenance-version compatibility policy?
4. What evidence types are in the current domain contract, and which ownable
   source types have relational records in Celestin?
5. Are conflict links symmetric? How are pending endpoints represented, and
   which explicit resolution outcomes/events are supported?
6. Which actor/time fields are reviewer provenance versus authenticated audit
   fields? Approval and verification must remain distinct.
7. Must timestamps round-trip the original offset/text or only their instant?

Once these are agreed against the actual Resurrection Layer contract, the
persistence owner can map them into an additive PostgreSQL migration and a
transactional storage operation without changing the Resurrection Layer.
