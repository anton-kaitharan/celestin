# SQLite FTS5 Retrieval

## Current implementation

`orbit.retrieval.FtsMessageSearch` queries an existing FTS5 message
table and returns `SearchResult` values with message ID, project ID, source ID,
content, optional timestamp, and BM25 relevance score. Lower (typically more
negative) BM25 scores rank ahead of higher scores. Ties use SQLite's parsed
timestamp descending, then message ID and source ID ascending. Missing or
unparseable SQLite timestamps sort after valid timestamps and remain available
as returned values.

`FtsMessageSearch` itself does not create, migrate, rebuild, or delete database
objects. The production `database.connect()` migration path creates and
maintains `chat_messages_fts` and `project_memory_fts`; isolated adapter tests
also create temporary FTS5 fixtures. `FtsSchema` maps these production tables
in `retrieval.service`.

## Requirements and query behavior

- Python's SQLite runtime must be compiled with FTS5 support. The tests exercise
  the actual runtime by creating a temporary virtual table.
- The configured table must be an FTS5 virtual table with indexed content and
  retrievable metadata columns. Production migration 2 creates the indexes,
  backfills existing rows, and installs insert/update/delete triggers for
  messages and canonical decisions.
- For a non-empty query, the adapter checks `sqlite_master` for a virtual table
  declared with FTS5 before searching. Missing tables, ordinary tables, and
  incompatible column declarations raise `SearchIndexUnavailable`. An empty
  or punctuation-only query returns immediately without checking the table.
- Identifiers are validated and quoted. Query text, project ID, and limit are
  SQL parameters. Every search includes a project ID predicate.
- Query text is matched against the configured content column and treated as
  plain Unicode word terms joined with `AND`.
  Punctuation is discarded; FTS operators, prefixes, and phrase syntax are not
  exposed. Empty and punctuation-only queries return an empty list.
- Unicode word matching uses Python's Unicode word-character pattern to prepare
  terms and SQLite FTS5's configured tokenizer to index/match content. Exact
  tokenization behavior can differ across tokenizer options; those options are
  not configured by this adapter.
- A missing or incompatible configured FTS table raises
  `SearchIndexUnavailable`. Invalid Python input raises `TypeError` or
  `ValueError` as appropriate. Other SQLite operational errors propagate.
- `RetrievalService` searches messages and decisions separately, then loads
  authoritative decision rows through `DecisionRepository.list_for_project`
  and maps results to retrieval candidates. Decision scores are BM25 scores
  from the decision-value FTS index; SQLite status and provenance fields remain
  authoritative.

## Candidate ranking policy

`rank_candidates` accepts retrieval DTOs and applies project and status filters
before sorting. Its default rules are:

1. Approved canonical decisions precede historical messages. This is a
   decision-priority tier, not a claim that the decision matched the query
   better. Its FTS relevance score remains unchanged.
2. Within a priority tier, lower FTS5 BM25 scores rank first.
3. Recency is a configurable tie-breaker (`recency_tiebreaker=True` by
   default): newer ISO 8601 timestamps precede older ones only when relevance
   scores tie. No arbitrary relevance/recency weights are combined.
4. Record ID and source ID provide deterministic final tie-breaks.
   Known ISO 8601 timestamps rank ahead of missing timestamps; naive ISO
   datetimes are interpreted as UTC. Non-ISO timestamp values are rejected by
   the ranking DTO.
5. Draft and deprecated decisions are excluded by default. Explicit
   `include_drafts=True` includes drafts in a separate tier after messages;
   they retain `status="draft"`. Explicit `include_deprecated=True` includes
   deprecated decisions in the last tier.

The ranking layer rechecks project IDs so cross-project candidates cannot
enter the result list. The integrated service applies approved-first and
exclude-draft/deprecated defaults. Decision BM25 relevance stays separate from
the approved-priority rule.

## Context bundles and token budget

`build_context_bundle` accepts retrieval candidates, re-applies the ranking and
status policy, and defaults to a 2,000-unit budget. Included records are
rendered as one compact JSON object per line. Each line preserves record ID,
project ID, source ID, timestamp, record kind, status, relevance score, and full
content. The budget applies to the complete rendered text, including metadata.

Every retrieved `content` value is untrusted project data, including content
from an approved canonical decision. JSON encoding preserves field boundaries
and escapes control characters; it does not prevent a language model from
following instructions contained in the text. The `status` field for a
canonical decision means its decision lifecycle (`draft`, `approved`, or
`deprecated`), not evidence verification. This retrieval contract carries no
Capability 1 verification, conflict, or stale status. Consumers must treat
tool results as evidence to inspect, never as privileged instructions or proof
that a claim is correct.

No compatible tokenizer dependency is installed in this workspace. The default
`Utf8ByteTokenEstimator` counts UTF-8 bytes and reports `token_count_is_estimate`
as true; it does not claim an exact model-token count. Byte count is a
conservative upper bound for tokenizers whose tokens each represent one or
more UTF-8 bytes, but different tokenizer families can have different
accounting. A model-specific `TokenCounter` can be injected later without
changing selection logic.

Selection scans candidates in their ranking order. It includes a complete
record only if the full rendered bundle remains within budget; it never clips
content. An oversized candidate is reported with reason `token_budget` and the
scan continues, so a later smaller record may fit. Exact duplicate content
(case-insensitive after whitespace normalization) is included once; later
copies are reported as `duplicate_content`. Empty content is not used as a
deduplication key. Same-project exclusions keep record, project, source,
timestamp, type, and status metadata. Project-mismatched records are never
included in per-record exclusion details; only their count appears in
`excluded_counts`, avoiding cross-project metadata disclosure. Exclusion counts
cover all rejected inputs by reason. Duplicate detection is textual, not
semantic.

The integrated retrieval service uses the 2,000 default and approved-decision
priority. Its module-level functions open a connection through the production
database initializer; callers using `RetrievalService(connection)` own that
connection. A decision without `source_message_id` retains a null source ID in
the context DTO rather than receiving a synthetic provenance ID.

## Production service and limitations

`orbit.retrieval.search_chat_history(project, query, limit=10)` returns
traceable result dictionaries. `retrieve_context(project, query,
token_budget=2000)` returns a JSON-serializable context dictionary. Both use
`database.connect()` and apply production migrations. See
`docs/retrieval_integration.md` for response examples and MCP adapter guidance.

There is no project registry, so an unknown project is indistinguishable from
an empty project; both return empty results. Search includes the conversation
ID but not role because production FTS metadata does not store role. Missing
FTS raises `SearchIndexUnavailable`; other SQLite/repository failures
propagate. The model-specific tokenizer remains unknown; counts are estimates
unless a compatible `TokenCounter` is injected into `build_context_bundle`.

## Tests and dependencies

Run the retrieval suite with:

```sh
python3 -m pytest -q
```

The project uses Python 3.10+, pytest, SQLite with FTS5, and the Python
standard library in production retrieval code. Tests use temporary synthetic
databases and do not require network access. There is no embeddings
package/provider implementation yet; that abstraction remains a later planned
phase.
