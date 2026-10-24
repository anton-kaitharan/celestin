# Bidirectional knowledge synchronization prototype

## Status and boundary

`orbit.sync.ProjectSyncService` defines both logical directions: an explicitly
authorized client publishes a structured event and receives a canonical
acknowledgement, while an authorized project reader retrieves ordered pages
from a checkpoint. Calls are in-process Python calls. `InMemorySyncEventStore`
is process-local and loses every event on restart; it is a contract/test
adapter, not production persistence or multi-machine synchronization.

This prototype adds no database schema, adapter, hosted endpoint, remote
credential, MCP tool, background poller, or network transport. Existing SQLite,
Capability 1 evidence memory, Capability 2 reconstruction, Capability 3 review,
retrieval, and System 1 behavior are not changed.

## Event contract

`SyncEventSubmission` is schema version 1 and contains:

- Project ID and source-client label. Neither value authenticates the sender.
- A client idempotency key, one of the supported `SyncEventType` values, and a
  concise `SyncPayload` with an optional Capability 3 category.
- Optional source-occurrence timestamp, Capability 3 source references,
  Capability 1 `EvidenceSource` references, and correlation ID.

The receiver assigns `ingested_at` in UTC. It is distinct from the optional
source timestamp. The canonical event ID is a stable hash of project, source
client label, and idempotency key. IDs are not ordering tokens. The in-memory
store assigns an increasing per-project sequence in ingestion order, so a late
event with an older source timestamp follows the events already ingested.
Validation rejects unsupported versions, unknown event/category types,
malformed or oversized fields, and common secret-like patterns. Event text is
stored as data and is never executed or interpreted as instructions.

The idempotency key is scoped by project and source-client label. Repeating the
same key with the same structured content returns the first canonical event and
its original ingestion time; reusing it for changed content raises
`IdempotencyConflict`. The source-client label is provenance only. A hosted
adapter must bind idempotency to an authenticated client identity, not trust a
caller-chosen label.

## Authorization, cursors, and delivery

Every publish call requires a `write` project selector; every read requires a
`read` selector. The service re-authorizes the selected project through its
injected `ProjectAuthorizer`, using the principal fixed by the trusted service
factory. The caller-supplied scope's principal is ignored, and the submitted
project must match the selected project. This is a domain guard, not transport
authentication: untrusted callers must not construct the service with a
permissive authorizer or select its principal. Hosted integration must provide
the trusted principal and authorizer at the application boundary and re-check
membership/revocation on every publish and page read. No client project ID or
client label can stand in for that check.

Cursors have the well-defined form `sync1.<project-fingerprint>.<sequence>`.
They bind to a project without exposing its name and represent an exclusive
sequence checkpoint: the next request returns events with a larger sequence.
The page cursor points to its last returned event, including when the page is
the final page; an empty page preserves the incoming position (or returns the
initial sequence-zero cursor). `has_more` reports whether that read had another
page at that moment. A later event receives a larger sequence and can be read
from the returned checkpoint. Reusing the same cursor safely returns the same
page while retained data is unchanged. A malformed, foreign-project, or
future-sequence cursor is invalid. A cursor older than an adapter's retained
floor is expired. The in-memory adapter's `expire_before` simulates that
retention behavior for tests; it does not run automatically.

Delivery is at least once. There is no exactly-once guarantee, and clients
should deduplicate by canonical event ID. A cursor records delivery progress;
it does not mean an event was reviewed, approved, verified, or confirmed as
current project state. Events have no review or evidence-verification fields.

## Required persistence-owner contract

The `SyncEventStore` port is intentionally small: atomic `append(event)` and
project-scoped `read_after(project, sequence, limit)` operations. Before a
durable adapter is built, the persistence owner must agree to:

1. A versioned event row format and project-scoped stable event IDs.
2. A unique idempotency constraint on project plus authenticated source client
   identity plus idempotency key, and an atomic comparison of content on key
   reuse.
3. Atomic per-project sequence allocation with event insertion, independent
   of source/ingestion timestamps.
4. A stable transactionally consistent read page and retention-floor semantics
   so a cursor cannot silently skip events; define whether concurrent inserts
   appear in the current page or the next page.
5. Project scoping for every query, safe cursor version evolution, payload
   limits, provenance retention, and storage error behavior.

No existing `MemoryStore` method represents an event stream or sequence. The
Capability 1 evidence port stores claims and evidence but has no event log;
Capability 3's in-memory review repository stores review candidates, not
delivery records. This prototype therefore does not adapt or share either
store.

## Capability integration boundaries

- Capability 1: event evidence uses its `EvidenceSource` type unchanged. A
  future adapter should map only a Capability 3 approved candidate into an
  explicit Capability 1 knowledge-service call, retaining event/source
  references and leaving verification `unverified` until a separate check.
  Receipt is not evidence verification.
- Capability 2: a future authorized read adapter may expose only reviewed and
  approved knowledge bundles. Raw events can be supplied as historical source
  evidence, but receipt alone must not become a verified or current finding.
- Capability 3: the optional payload category reuses `CandidateCategory`.
  Published execution summaries are not automatically inserted into the
  review queue. A future explicit adapter may convert an event to
  `CaptureInput`; candidates still require review. Pending/rejected candidates
  do not become accepted knowledge through synchronization.

## Limitations

There is no authenticated remote client, server-issued opaque cursor secret,
durable transaction, cross-process idempotency, remote replication, event
retention policy, source resolver, or production authorization wiring. The
secret check is heuristic. The in-memory adapter is single-process and not
thread-safe. Tests establish the Python service/adapter contract only; they do
not establish PostgreSQL transaction behavior or hosted authorization.
