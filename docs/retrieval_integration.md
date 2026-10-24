# Retrieval service integration contract

The retrieval package combines the production SQLite repositories and FTS5
migrations with Member 2's search, ranking, and context builder. It does not
modify the MCP server or its public tools.

## Public operations

```python
from orbit.retrieval import search_chat_history, retrieve_context

search_chat_history(project: str, query: str, limit: int = 10) -> list[dict]
retrieve_context(project: str, query: str, token_budget: int = 2_000) -> dict
```

Both functions open and close a connection using `orbit.database.connect()`
for each call. The `RetrievalService(connection)` class is also exported for
callers that already own a SQLite connection; its methods return the internal
`SearchResult` and `ContextBundle` DTOs. The public wrappers serialize results
for service-boundary use.

```python
from orbit.database.engine import connect
from orbit.retrieval import RetrievalService
```

Search returns a list of objects shaped like:

```json
{
  "message_id": "17",
  "project": "my-project",
  "source_message_id": "17",
  "conversation_id": "conversation-1",
  "role": null,
  "content": "SQLite stores the project data.",
  "created_at": "2026-10-11T12:34:56.789012Z",
  "score": -0.000001
}
```

The matched message ID is the source ID. `conversation_id` comes from the
production FTS metadata. `role` is `null` because the current FTS mapping does
not store it. BM25 `score` is a relevance value, not a probability.

Context returns an object with `project`, `query`, `items`, `excluded`,
`excluded_counts`, `rendered_context`, `token_count`, `token_budget`,
`token_count_is_estimate`, and `token_counter`. Each included item preserves
`record_id`, `project_id`, `source_message_id`, `created_at`, `record_kind`,
`status`, `content`, and `relevance_score`. Canonical decisions without a
database `source_message_id` retain `null`; the service does not fabricate a
source ID. The builder's JSON Lines context still uses internal DTO keys
`source_id` and `timestamp`.

The MCP adapter uses the top-level `project` as the scope for its `items`
array; its individual item objects omit `project_id`. Each line in
`rendered_context` retains the per-record `project_id`. A consumer that detaches
an MCP item from its response must retain the top-level project association.

## Optional Capability 1 knowledge state

### Direct `RetrievalService` with an injected reader

A caller may construct `RetrievalService(connection,
knowledge_state_reader=reader)`. When the reader returns a Capability 1
`KnowledgeBundle`, Retrieval adds an optional `knowledge_state` envelope with
`schema_version: 1`. The mapper copies the claim, knowledge type, verification
and lifecycle values, checker metadata, supersession IDs, evidence references
and verification metadata, and capture/review provenance. Missing optional
values remain `null`; approval does not imply verification, and no freshness
assessment or reverse conflict link is inferred.

The `KnowledgeStateReader.get_for_candidate(project_id, record_kind,
record_id)` seam returns a `KnowledgeBundle` or `None`. The requested project is
passed to the reader, and Retrieval rejects a returned bundle whose knowledge,
evidence, or capture provenance project differs. The reader implementation
must still scope its own lookup correctly. It must explicitly map an existing
Retrieval candidate to Capability 1 knowledge; it does not create or derive
candidate IDs. This seam accepts a project string and carries no principal or
authorization decision: the project is a lookup scope, not proof that the
caller may read it. A production caller must enforce authorization separately.
The envelope is included in context JSON Lines and counted by the existing
whole-context budget calculation.

### Default `RetrievalService` and current MCP adapter

When `knowledge_state_reader` is omitted, the envelope is absent and legacy
context serialization is unchanged. The current MCP adapter constructs
`RetrievalService` without a reader, and its public item serializer does not
include `knowledge_state`; MCP does not expose the envelope or change its
response shape. The adapter also returns `rendered_context`, which contains the
serialized context lines. Configuring a reader at that boundary would therefore
add the envelope to that string and change its contents and token count even if
the public `items` serializer stayed the same. `knowledge_state.schema_version`
versions only the internal envelope; it does not version the MCP response.
MCP exposure requires a separately approved response contract, with explicit
versioning or client capability negotiation.

### Capability 1 model sources

The envelope maps these Capability 1 domain values:

| Envelope data | Capability 1 source |
|---|---|
| Knowledge ID, project, type, claim, verification and lifecycle status, created/updated/checked timestamps, supersession IDs, and checker metadata | `KnowledgeRecord` |
| Evidence IDs, types, source references, locators, revisions, hashes, capture/check timestamps, reference status, verification status, and checker metadata | `KnowledgeBundle.evidence` entries (`EvidenceRecord`) |
| Capture candidate ID, category, certainty, review state, capture verification value, source references and occurrence times, candidate timestamps, original claim, reviewer labels/timestamp/note, and declared conflict candidate IDs | `KnowledgeRecord.capture_provenance` (`CaptureKnowledgeProvenance`) and its `CaptureSourceProvenance` entries |
| Reconstruction assessment | `EvidenceClass` belongs to Project State, not the Capability 1 record. The current `from_bundle` mapper leaves it `null`. |

`KnowledgeRecord` and `EvidenceRecord` carry recorded verification values;
approval does not set them. `VerificationStatus.STALE` is a recorded status,
not a computed freshness assessment, and the models have no separate freshness
field. `VerificationStatus` has no `UNKNOWN` member and `KnowledgeRecord`
defaults an omitted value to `UNVERIFIED`; the current typed mapper cannot tell
whether that value was explicitly stored or supplied by a model/adapter
default. Optional checker fields and absent capture provenance remain absent.
The direct mapper preserves the `KnowledgeBundle` it receives. When the bundle
is read through `EvidenceLinkedMemoryService.get_knowledge`, that service
re-resolves each evidence `reference_status` through its resolver at read time;
availability still does not establish truth or freshness.

`CaptureKnowledgeProvenance.conflicts_with` contains capture candidate IDs,
not resolved knowledge IDs or a conflict assessment. The mapper retains those
declared references as supplied. A reference may be one-way or have no endpoint
in the returned bundle; Retrieval does not resolve endpoints, add reverse
links, or turn them into a verified conflict fact.

### Existing read interfaces and production gap

`EvidenceLinkedMemoryStore` defines `get_knowledge(project, knowledge_id)` and
`list_evidence(project, knowledge_id)`. `EvidenceLinkedMemoryService.get_knowledge`
wraps those reads in a `READ` check using its configured `ProjectAuthorizer` and
principal, then returns a `KnowledgeBundle`. This is a domain read contract,
not a production persistence implementation: no implementation of
`EvidenceLinkedMemoryStore` is present in `src/`. The application `MemoryStore`
and its SQLite/PostgreSQL implementations cover chat messages and decisions;
they do not read Capability 1 knowledge or evidence records.

Project State also defines `ReadOnlyKnowledgeSource.list_records()`, but it is
a caller-bound prototype seam without a production adapter. It lists bundles
for reconstruction and does not map a Retrieval candidate to a knowledge
record. The Capture `candidate_id` is retained in capture provenance, but
Retrieval candidates use their own `record_kind` and `record_id` (for example,
a message or decision ID). Neither `record_id` nor `source_id` is guaranteed to
equal the capture candidate ID or `knowledge_id`; source references are
provenance, not a universal one-to-one join key. No supported candidate-to-
knowledge lookup currently exists.

### Smallest future production integration

Keep the existing injected `KnowledgeStateReader.get_for_candidate(project_id,
record_kind, record_id) -> KnowledgeBundle | None` boundary. A production
implementation needs an owner-approved, authoritative mapping from that
Retrieval identity to exactly one Capability 1 `knowledge_id`, followed by a
project-scoped read of the knowledge record and its associated evidence. The
Storage owner must provide or approve that mapping/read contract, its behavior
for missing or ambiguous mappings, and how the record, evidence, and optional
capture provenance are returned without rewriting or dropping them. No
existing Storage API supplies the candidate-to-knowledge mapping, and no new
schema, adapter, or join key is assumed here.

The lookup must constrain the mapping and record/evidence reads to the same
authorized project, and reject out-of-scope data before returning it. Retrieval's
post-read checks are integrity checks, not authorization. A project ID alone
does not authorize access: production wiring must preserve the caller's trusted
authorization path (for example, the configured `ProjectAuthorizer` used by
`EvidenceLinkedMemoryService`) and must not treat the string passed through the
Retrieval seam as a credential. Production persistence remains unconnected;
the current seam has fake-backed tests only.

## Trust boundary

All returned `content`, including approved-decision values and document
excerpts, is untrusted project data. A project ID and source reference identify
where a record came from; they do not authenticate the source, prove the claim,
or grant the content instruction authority. JSON Lines preserve field
boundaries but are not a prompt-injection defense. The decision `status` is
approval/lifecycle state only. The default `RetrievalService` and current
retrieval MCP adapter carry no Capability 1 state; a direct service caller gets
it only when a reader returns a bundle. Consumers must not infer verification,
freshness, or conflict state from `approved`.

Celestin currently returns retrieval context from MCP as tool output. The
repository contains no coding-agent prompt assembler or model call that inserts
`retrieve_context` results into a consuming agent's conversation. Therefore
the consuming client must keep its own system/developer instructions separate,
mark retrieved content as untrusted evidence, and must not execute or obey
instructions found in it. Labels and prompt wording reduce ambiguity but do
not eliminate prompt injection. A model-level regression still needs to run
through the actual consuming client and verify that hostile retrieved text
cannot override a privileged instruction or trigger an unauthorized tool
action.

## Production FTS mapping

Schema migration 2 creates `chat_messages_fts` and `project_memory_fts`,
backfills existing rows, and installs insert/update/delete triggers. SQLite
FTS5 must be enabled. The service uses these mappings:

| Field | Messages index | Decisions index |
|---|---|---|
| table | `chat_messages_fts` | `project_memory_fts` |
| record ID | `message_id` | `decision_id` |
| project field | `project_id` | `project_id` |
| FTS source metadata | `source_id` (`conversation_id`) | `source_id` (indexed value; service reads authoritative `source_message_id`) |
| timestamp | `created_at` | `updated_at` |
| indexed content | `content` | `value` |

The local SQLite service queries production tables after `connect()` applies
migrations; callers do not create or manage FTS tables. Migration triggers
maintain both indexes for inserts, updates, and deletes.

## Candidate assembly and ranking

`search_chat_history` searches only messages scoped to the requested project.
`retrieve_context` searches the message and decision FTS indexes, then loads
authoritative decision rows with `DecisionRepository.list_for_project(project,
include_deprecated=True)`. It uses each FTS index's BM25 score; no score is
invented from decision status or timestamps. Message candidates use the
matched message ID as provenance. Decision provenance is the stored
`source_message_id`, or `null` if absent.

The service applies `RankingPolicy`: approved canonical decisions precede
historical messages independent of their BM25 values; drafts and deprecated
decisions are excluded. The service limits each FTS result set to 100 by
default; `candidate_limit` on `RetrievalService` changes that internal limit.

## Budget, errors, and limitations

The requested context budget is passed to `build_context_bundle`. Its default
counter estimates tokens by counting UTF-8 bytes and reports
`token_count_is_estimate=True`; it is not an exact model-token count. The
builder measures the complete rendered JSON Lines, never truncates a record,
and skips oversized records while continuing to later candidates.

An unknown project is indistinguishable from a valid project with no matches
because there is no project registry; both return empty results. Empty and
punctuation-only queries return empty results. Missing/incompatible FTS tables
raise `SearchIndexUnavailable`; database and repository errors propagate
rather than becoming successful empty results.

The MCP branch's mock search handler currently returns a
`{"project", "query", "results"}` envelope, while this retrieval operation
returns the requested `list[dict]`. Member 3 can wrap the list at the MCP
boundary if that envelope remains the agreed tool contract. No MCP server
handler was changed as part of this integration.

## Isolated PostgreSQL retrieval adapter

`PostgresRetrievalData` in `src/orbit/retrieval/postgres_data.py` implements
the same `RetrievalData` port without changing the MCP runtime. Its constructor
requires an explicit `PostgresRetrievalScope` containing trusted workspace and
project UUIDs plus the legacy project key. The project string passed to the
port is checked against that fixed scope; it cannot choose a workspace or
authenticate a caller. A future hosted request boundary must authenticate the
principal, authorize the project, and construct the scope before calling this
adapter. Every candidate query and authoritative reload filters by both UUIDs
and excludes archived records, projects, and workspaces. Decision candidates
are reloaded from `project_memory` on the same connection before results are
returned. The injected connection factory owns connection creation; each port
operation uses and closes a connection context, and the adapter closes each
cursor explicitly.

PostgreSQL uses `to_tsvector('simple'::regconfig, ...)`, parameterized
`to_tsquery`, and `ts_rank_cd`. Migration
`0003_retrieval_search_indexes.sql` supplies matching partial GIN expression
indexes for unarchived `chat_messages.content` and `project_memory.value`;
there is no new extension dependency or generated search column. The adapter
returns the negated `ts_rank_cd` value so the shared lower-is-better ranking
contract is preserved. These values are not numerically equivalent to SQLite
BM25 values. Query relaxation follows the existing strict, stopword-removed,
then bounded-OR tiers, but PostgreSQL tokenization and ranking can differ from
FTS5, especially for punctuation, identifiers, language configuration, and
phrase normalization. Live query plans, index use, and behavioral parity have
not been verified against PostgreSQL.

The adapter is directly injectable into `RetrievalService` for isolated use,
but it is not exported through or selected by the MCP runtime. MCP continues to
compose SQLite persistence and retrieval only. This work does not implement
hosted identity, request-level authorization, or a secure remote MCP path.
It does not provide a hosted semantic-vector index or PageIndex store; those
optional local components must not be enabled for hosted requests until their
storage and tenant-isolation behavior are designed and tested.

## Tests

`tests/retrieval/test_service_integration.py` uses synthetic rows in temporary
databases created by the production migration code. It covers FTS
synchronization, project isolation, decision/message assembly, ranking,
traceability, public serialization, missing indexes, database errors, and
budget accounting. Run all tests with `PYTHONPATH=src python -m pytest -q`.
