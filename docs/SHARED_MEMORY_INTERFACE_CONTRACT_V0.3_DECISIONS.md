# Shared-Memory Interface Contract v0.3 — Final Decision Record

**Status:** Final implementation baseline, authorized by the requester  
**Date:** 2026-10-19  
**Scope:** First PostgreSQL-backed Evidence/Knowledge conversion and its producer, authorization, and MCP boundaries

This record resolves the v0.3 review questions for implementation. It supersedes the `PROPOSED` and `UNRESOLVED` labels in the [boundary review](RESURRECTION_INTERFACE_BOUNDARY_REVIEW.md) for the decisions below. It records the requester's decisions; it does not claim that an absent teammate has already acknowledged them. A teammate or their agent can respond **Accept**, **Reject**, or **Modify** against this concrete baseline.

## Decisions at a glance

| Area | Decision | Rationale |
|---|---|---|
| Producer, candidate, revision, delivery, and evidence identity | **MODIFY** the draft proposals as specified below | Separate identities and immutable snapshots make retries and provenance unambiguous. |
| Approval, authorization revocation, replay disclosure, and history | **MODIFY** the draft proposals as specified below | Current authorization must govern every result disclosure; conversion must serialize with revocation. |
| Canonicalization, uniqueness, concurrency, and transactions | **MODIFY** the draft proposals as specified below | Stable canonical bytes and database uniqueness make retries deterministic and safe under concurrency. |
| Public MCP request/response schemas | **ACCEPT** preserving existing schemas; keep enrichment internal | Storage work must not silently become a client-facing API change. |

## 1. Producer, candidate, revision, evidence, and conflicts

### Identity and immutable revisions

- `project_id` is the tenant and authorization boundary. Every candidate, revision, delivery, evidence record, occurrence, conflict, conversion, knowledge record, and audit entry is scoped to exactly one project.
- A producer is identified by a stable `producer_id` registered for that project. The trusted application derives it from the authenticated producer/session; a payload cannot claim another producer's identity.
- Candidate identity is `(project_id, producer_id, candidate_id)`. `candidate_id` is opaque and case-sensitive. The database enforces this unique scope.
- A candidate revision is identified by `(project_id, producer_id, candidate_id, candidate_revision)`. `candidate_revision` is a positive integer, begins at 1, and advances by exactly 1. A missing revision is rejected for later revisions until the gap is supplied.
- A revision's candidate content, source occurrences, and conflict declarations are immutable. An edit creates the next revision. Approval refers to one exact revision and never transfers to a later revision.
- Review decisions are append-only events separate from the immutable candidate payload. The state sequence for one revision is `pending → approved | rejected`, and `approved → revoked`. Rejection or revocation is terminal for that revision; a new revision is required for reconsideration. Conversion uses the latest review event and requires it to be `approved`.

### Delivery retry IDs

- Each submitted delivery carries a producer-generated opaque `delivery_id`, unique within `(project_id, producer_id)`.
- Repeating a `delivery_id` with the same normalized delivery payload returns the original receipt. Reusing it with a different payload returns `idempotency_conflict` and performs no mutation.
- Candidate revision uniqueness is independently enforced. Re-delivering the same candidate revision and payload is a no-op; changing any immutable field under the same revision returns `revision_conflict`. A new payload requires a new revision.
- A retry response is disclosed only after the caller is currently authorized for the corresponding operation and project.

### Evidence occurrence identity and ordering

- Evidence source identity and evidence occurrence identity are separate. A source/evidence record may be referenced by many occurrences; repeated occurrences are preserved and are never deduplicated away.
- An occurrence is identified by `(project_id, producer_id, candidate_id, candidate_revision, ordinal)`. `ordinal` is a contiguous zero-based position in the producer's declared order. It is immutable within that revision and determines order; timestamps do not.
- Each occurrence links to one evidence record and carries its own optional `source_occurred_at`. The evidence record contains `evidence_type`, `source_namespace`, `source_id`, optional `source_revision`, optional `locator`, and optional content hash. Capture/ingest time is recorded separately by the service.
- Evidence records may be deduplicated by `(project_id, evidence_type, source_namespace, source_id, source_revision, locator, content_hash)`. Occurrences remain distinct even when they point to the same deduplicated evidence record.
- `source_namespace` is a stable, project-registered source system namespace; `source_id` is only unique within that namespace and project. `source_revision` is an opaque source-system token, not a candidate revision. Hashes use an explicit algorithm prefix; v1 accepts `sha256:` followed by 64 lowercase hexadecimal digits.

### Timestamp and revision meaning

- All stored timestamps use PostgreSQL `timestamptz` and UTC. Canonical form is RFC 3339 UTC with six fractional digits, such as `2026-10-19T12:00:00.000000Z`.
- Producer-supplied `candidate_created_at` and `source_occurred_at` describe claimed event time and remain distinguishable from service-generated `received_at`, `approved_at`, and `converted_at`. Producer timestamps are provenance, not proof of authorization or ordering.
- Candidate ordering uses `candidate_revision`; occurrence ordering uses `ordinal`; delivery retry identity uses `delivery_id`; source ordering/version uses opaque `source_revision`. No timestamp is used as a substitute for any of these identities.

### Source and conflict namespaces and meaning

- Source references are always interpreted as `(project_id, source_namespace, source_id, source_revision, locator)`. Identical strings in different namespaces or projects are different sources.
- A conflict reference identifies `(project_id, producer_id, candidate_id, candidate_revision)`. Cross-project conflict references are rejected. A conflict relation is symmetric and stored once using a canonical sorted endpoint pair.
- A candidate revision cannot conflict with itself, and duplicate declarations of the same unordered pair produce one edge.
- `conflicts_with` means “these candidate revisions may make incompatible claims; review them together.” It does not decide which claim is correct, invalidate either claim, or suppress either from storage by itself.
- A reference to a not-yet-arrived candidate revision is stored as a pending endpoint. It is linked when that exact revision arrives; no candidate or resolution is fabricated. Conflict resolution or dismissal requires a separate authorized, audited review event.

## 2. Approval, authorization, revocation, replay, and correction

### Who can approve

- Approval requires an authenticated project member with the `candidate:approve` capability. The approval event stores the authenticated principal ID and server-generated `approved_at`; a client-supplied reviewer label or ID is ignored/rejected.
- The reviewer must be a different authenticated principal from the producer/submitter. The approval binds to the exact immutable candidate revision.
- In local stdio mode, the application uses a configured local operator principal. Approval and conversion are disabled if no trusted principal is configured. A caller cannot obtain reviewer identity by placing a name in the request.
- Conversion separately requires the authenticated principal to have `memory:convert`. Current reads require `memory:read`; superseded-history reads require `memory:history`; audit-history reads require the project-scoped `audit:read` capability.

### Revocation and transaction ordering

- Membership/permission changes increment a per-project authorization epoch. A conversion transaction obtains a shared lock on that epoch, verifies the current principal's membership and capabilities, and keeps the lock through commit. Revocation obtains an exclusive lock and increments the epoch. Approval revocation serializes on the candidate revision's review-state row.
- All authorization and current approval checks occur inside the conversion transaction before looking up or returning a prior conversion result. The transaction uses a consistent lock order: project authorization epoch, candidate/review state, then conversion key.
- The lock order defines the race: if revocation commits first, conversion is denied; if conversion locks and commits first, conversion succeeds and revocation takes effect afterward. A later revocation does not erase a committed conversion; correction/supersession handles the resulting knowledge state.
- PostgreSQL policies also enforce `project_id` scoping for the application role. The role is not the table owner and has no `BYPASSRLS`; row-level security is forced on protected tables. The trusted application sets transaction-local scope only after authentication. Composite foreign keys include `project_id` so cross-project references cannot be persisted.

### Replay after revocation and audit access

- Conversion uniqueness is `(project_id, producer_id, candidate_id, candidate_revision)`: at most one successful conversion exists for one candidate revision.
- An exact authorized retry returns the same conversion result and original knowledge/evidence IDs. If membership, permission, or approval has been revoked, the retry returns authorization denial without revealing whether a conversion exists or disclosing any IDs. If membership or permission is later restored and the revision remains approved, the same exact retry returns the original result. A revoked approval is terminal for that revision; create and approve a new revision to convert again.
- Authorization failures use the existing non-enumerating forbidden/not-found mapping for the caller. No pre-authorization replay lookup may reveal row existence, hashes, or identifiers.
- Audit events are append-only and project-scoped. They record actor, action, target identities, decision, timestamps, and result IDs/hashes as needed for accountability. A project member with `audit:read` can read them; audit access does not itself grant access to claim/evidence content. Audit event bodies must not duplicate secret or source payload content unnecessarily.

### Corrections, supersession, and retrieval visibility

- Before conversion, a correction is the next immutable candidate revision and must be approved again.
- After conversion, correcting knowledge creates a new candidate revision and a new knowledge record. Its conversion explicitly names the prior knowledge record it supersedes. Creating the successor, linking the supersession, and marking the prior record superseded occur in the same transaction. A prior knowledge record can have only one direct active successor; further corrections form a chain.
- Knowledge and evidence records are retained as immutable history. Default retrieval returns only active, non-superseded knowledge. An authorized history query may include superseded records and their links. This history option remains internal to the initial MCP integration.

## 3. Canonical payload, conversion uniqueness, and transactions

### Canonicalization and hash version

- The first implementation supports `smic-conversion-payload-v1` only. It normalizes strings to Unicode NFC. It preserves claim, locator, and opaque ID whitespace, case, and punctuation; no trimming or case folding is applied. Machine enums use their defined lowercase values. `knowledge_type` is computed by the pinned v0.3 category-to-knowledge-type mapping; an unknown category/mapping is rejected, never guessed.
- Timestamps are normalized to the UTC form above. Optional values are encoded as JSON `null`, not omitted. Evidence occurrences remain in `ordinal` order. Conflict endpoint arrays are sorted by `(producer_id, candidate_id, candidate_revision)`. JSON numbers are integers only; floating-point values are forbidden.
- Canonical bytes use RFC 8785 JSON Canonicalization Scheme (JCS). `payload_hash` is lowercase hex SHA-256 over the UTF-8 canonical bytes. Persist both `hash_version = "smic-conversion-payload-v1+jcs-rfc8785+sha256"` and the digest. The version is part of the durable conversion record; versions are never silently reinterpreted.
- The canonical payload fields are exactly: `schema_version`, `project_id`, `producer_id`, `candidate_id`, `candidate_revision`, `category`, `knowledge_type`, `certainty`, `claim`, nullable `original_claim`, `candidate_verification_status` (must be `unverified` at conversion), `candidate_created_at`, `reviewer_id`, `approved_at`, ordered `evidence_occurrences`, sorted `conflicts`, and nullable `supersedes_knowledge_id`. The evidence and conflict object fields are those defined in section 1. The reviewer note remains in the audit/review history and is not copied into the knowledge payload. The payload excludes delivery ID, transport metadata, generated database IDs, and server-generated receive/commit timestamps.

### Golden vectors

For both vectors, canonicalize exactly as shown (the `\n` in vector 2 is one newline character in the JSON string), then SHA-256 the UTF-8 bytes.

**Vector 1**

```json
{"approved_at":"2026-10-19T12:00:00.000000Z","candidate_created_at":"2026-10-18T08:00:00.000000Z","candidate_id":"c1","candidate_revision":1,"candidate_verification_status":"unverified","category":"decision","certainty":"medium","claim":"Use UTC.","conflicts":[],"evidence_occurrences":[],"knowledge_type":"decision","original_claim":null,"producer_id":"agent-a","project_id":"p1","reviewer_id":"user-a","schema_version":1,"supersedes_knowledge_id":null}
```

Expected SHA-256: `a24bbb757806b226435aedd9eea88f6d5f18f164c09421942083669151b45ec6`

**Vector 2 — NFC text, a conflict, and two duplicate evidence occurrences**

```json
{"approved_at":"2026-10-19T12:00:00.000000Z","candidate_created_at":"2026-10-18T08:00:00.000000Z","candidate_id":"c-2","candidate_revision":2,"candidate_verification_status":"unverified","category":"decision","certainty":"high","claim":"Café uses NFC.\n","conflicts":[{"candidate_id":"c-1","candidate_revision":1,"producer_id":"agent-b"}],"evidence_occurrences":[{"content_hash":"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","evidence_type":"repository","locator":"README.md:4","ordinal":0,"source_id":"repo-1","source_namespace":"git","source_occurred_at":null,"source_revision":"a1b2"},{"content_hash":"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","evidence_type":"repository","locator":"README.md:4","ordinal":1,"source_id":"repo-1","source_namespace":"git","source_occurred_at":null,"source_revision":"a1b2"}],"knowledge_type":"decision","original_claim":null,"producer_id":"agent-a","project_id":"p1","reviewer_id":"user-a","schema_version":1,"supersedes_knowledge_id":null}
```

Expected SHA-256: `908a72554596df692c426567afe92ad8b57383246e7687e16b11dfb26d9342cd`

### Conversion replay and concurrency

- For the unique conversion key above, equal hash version and equal payload hash mean replay: return the existing result and do not write new rows. A different payload hash under the same key returns `payload_conflict` and performs no mutation. The caller must create and approve a new candidate revision for changed content.
- Concurrent identical conversions converge on one durable result through the database unique constraint. The losing transaction reads the winning row only after authorization, compares version and digest, then returns replay or `payload_conflict`.
- A retry after an uncertain commit uses the same key and payload. The database row determines whether the original transaction committed; the adapter never creates a second knowledge record to “repair” an unknown outcome.

### Transaction boundary

One PostgreSQL transaction contains: current authorization and approval checks; conversion-ledger uniqueness/arbitration; the knowledge row; all initial evidence records and ordered occurrence links; all declared conflict edges/pending endpoints; any supersession links and prior lifecycle update; and the final conversion result/IDs. Any failure rolls back the entire conversion graph. No network/source-resolution call is made while this transaction is open. Use database constraints for project-scoped foreign keys and uniqueness; retry only recognized serialization/deadlock failures with the same conversion key.

## 4. Product/API compatibility

**ACCEPT.** The initial implementation preserves every existing MCP request and response schema, field, and public error shape. Evidence/Knowledge enrichment stays internal to storage and retrieval adapters. No existing tool gains response fields or different semantics as part of this work. A public change requires a separately versioned API decision and compatibility acceptance coverage.

## 5. Frozen acceptance criteria and ownership

These are the v0.3 acceptance criteria for the implementation and its contract tests:

1. Producer/candidate/revision/delivery uniqueness is enforced at the stated project and producer scopes; exact delivery retries succeed and changed-payload reuse conflicts without mutation.
2. Candidate revisions are immutable and sequential; approval is bound to one revision, reviewer identity is trusted/authenticated, and rejected/revoked revisions cannot convert.
3. Duplicate evidence occurrences survive round-trip with distinct ordinals and may point to the same evidence record; source and candidate revision namespaces never collide.
4. Conflict edges are symmetric and project-scoped; missing endpoints remain pending; conflict status changes only through an explicit authorized audit event.
5. Golden vectors match byte-for-byte. Same-key identical and concurrent retries return one stable result; changed payload conflicts; injected failure at each conversion write leaves no partial graph.
6. Membership, capability, and approval revocation races obey the transaction lock ordering. Unauthorized retries disclose no prior result; authorized replay returns original IDs.
7. Correction creates a successor and supersedes the prior record atomically; normal retrieval hides superseded records and authorized history includes them.
8. Existing MCP request/response schemas and public error shapes remain unchanged.

Individual owner names are not available in the inspected repository, so implementation ownership is by role: **Producer/Capture** owns producer IDs, candidate revisions, and source occurrence inputs; **Storage** owns PostgreSQL constraints, canonicalization, conversion transactions, and adapter conformance; **Product/Security** owns authenticated principals, capabilities, review and revocation policy; **MCP/API** owns compatibility verification. There are no unresolved design choices for this v0.3 implementation baseline. Any deviation requires a written change to this record before the affected schema or adapter behavior is built.
