# Orbit — Public MCP Tool Contracts

**Status:** MCP boundary contract. This document defines public tool arguments and response schemas; database and retrieval implementation details are documented in `docs/retrieval_integration.md` and `docs/retrieval.md`.

## Ownership and compatibility

The MCP server owns tool names, public arguments, boundary validation, and serialization. It delegates persistence to the database/repository layer and search/ranking/context construction to the retrieval layer through separately agreed service interfaces. Tool handlers must not contain SQL, embedding, ranking, or token-selection logic. Every delegated call carries the requested `project` scope; the implementation must enforce that scope in the underlying operation, including source-message lookup.

The MCP server exposes the tools defined below and adapts the persistence and retrieval implementations to these public DTOs. The schemas are independent of storage names and remain the compatibility boundary for MCP clients.

## Shared conventions

- `project` is a required, non-empty string identifying the project namespace and requested scope. Reject whitespace-only values; preserve the caller's value (do not silently normalize/case-fold it). The server must separately authorize the requested project/action before any read or write; this argument is never proof of permission. All reads and writes, including `source_message_id` resolution and candidate assembly, are restricted to the authorized project. A source found only in another project is treated as not found.
- Text arguments are Unicode strings. Required text must contain at least one non-whitespace character; preserve original text except where a tool's downstream search semantics apply. Reject wrong JSON types (including booleans where integers are required) and invalid values at the MCP boundary.
- MCP boundary limits (Unicode code points): `project` 200; `conversation_id` 512; `role` 32; message `content` 100,000; decision `key` 256; decision `value` 100,000; `source_message_id` 64; and `query` 4,096. Reject embedded NUL characters. `limit` is 1–100 (default 10); `token_budget` is 1–20,000.
- IDs are opaque, non-empty strings. Message IDs are stable IDs assigned by the persistence layer on ingestion and returned as `message_id`. They are project-scoped; clients must not infer their format. `source_message_id` refers to exactly such an ID, and must identify a message in the same requested project.
- Timestamps are serialized as RFC 3339 / ISO 8601 strings with an explicit UTC offset; server-generated timestamps use UTC and `Z` (example `2026-10-11T12:34:56.123Z`). Do not return naive/local timestamps. When retrieval passes through existing timestamps, preserve their instant and serialize in this format.
- Successful tool results are JSON objects matching the schemas below (MCP structured content). Error names below are stable categories; clients should not depend on backend exception text. Invalid arguments: `INVALID_ARGUMENT`; unauthorized project/action: `ACCESS_DENIED`; unknown project: `PROJECT_NOT_FOUND`; missing or cross-project source: `SOURCE_MESSAGE_NOT_FOUND`; configured retrieval/index unavailable: `RETRIEVAL_UNAVAILABLE`; persistence failure: `STORAGE_ERROR`; unexpected internal failure: `INTERNAL_ERROR`. Return an MCP tool error with a safe message and category; do not leak SQL, paths, or provider credentials.
- Error payload convention: `{ "error": { "code": "ERROR_CODE", "message": "safe human-readable explanation" } }`. The MCP SDK may encode this as an error result rather than successful structured content; the category and fields must remain equivalent.
- Arrays have deterministic order as described per tool. Empty results are successful empty arrays, not errors.

## `ingest_chat_message`

**Purpose:** Persist one chat message under a project and conversation, returning its stable source ID and creation time.

Signature: `ingest_chat_message(project: string, conversation_id: string, role: string, content: string) -> IngestChatMessageResult`

| Argument | Required | Type and validation |
|---|---:|---|
| `project` | yes | Non-empty project identifier; shared project rules apply. |
| `conversation_id` | yes | Non-empty opaque string scoped to `project`. |
| `role` | yes | One of `system`, `user`, or `assistant`, matching the database layer's accepted values. |
| `content` | yes | Non-empty after trimming check; original content is stored/returned without trimming. |

Result:

```json
{
  "project": "string",
  "message_id": "string",
  "conversation_id": "string",
  "role": "string",
  "content": "string",
  "created_at": "RFC3339 UTC timestamp"
}
```

Errors: `INVALID_ARGUMENT`, `PROJECT_NOT_FOUND` (if projects are provisioned entities), `STORAGE_ERROR`, `INTERNAL_ERROR`.

## `save_decision`

**Purpose:** Persist a project decision linked to the message that records its source.

Signature: `save_decision(project: string, key: string, value: string, source_message_id: string) -> SaveDecisionResult`

| Argument | Required | Type and validation |
|---|---:|---|
| `project` | yes | Non-empty project identifier; shared project rules apply. |
| `key` | yes | Non-empty decision key. |
| `value` | yes | Non-empty decision text/value. |
| `source_message_id` | yes | Non-empty message ID; must resolve to a message in the same project. |

Result:

```json
{
  "project": "string",
  "decision_id": "string",
  "key": "string",
  "value": "string",
  "status": "draft | approved | deprecated",
  "source_message_id": "string",
  "created_at": "RFC3339 UTC timestamp",
  "updated_at": "RFC3339 UTC timestamp"
}
```

Status policy proposal: statuses are exactly `draft`, `approved`, and `deprecated`. A newly saved decision should be `draft`; approval/deprecation is a lifecycle transition and is not expressible in this four-tool API. The owning team must decide which existing/admin mechanism performs transitions before implementation. Until then, the MCP tool must not imply that saving approves a decision. Retrieval defaults to excluding `draft` and `deprecated`; approved canonical decisions receive priority over historical messages. Deprecated decisions are excluded by default. This is consistent with the retrieval branch's proposed ranking policy.

Errors: `INVALID_ARGUMENT`, `SOURCE_MESSAGE_NOT_FOUND` (including cross-project source IDs), `PROJECT_NOT_FOUND` (if applicable), `STORAGE_ERROR`, `INTERNAL_ERROR`.

## `search_chat_history`

**Purpose:** Find matching chat messages in one project. Search semantics/ranking are owned by retrieval; this tool is only the public adapter.

Signature: `search_chat_history(project: string, query: string, limit?: integer = 10) -> SearchChatHistoryResult`

| Argument | Required | Type and validation |
|---|---:|---|
| `project` | yes | Non-empty project identifier; shared project rules apply. |
| `query` | yes | Non-empty after trimming check. Retrieval draft currently treats query as plain Unicode terms joined with AND; punctuation is discarded and FTS operators/phrase syntax are not public. Confirm this behavior at integration. |
| `limit` | no | Integer, not boolean, range 1–100; default 10. Proposed public addition matching retrieval's draft `limit` default. |

Result:

```json
{
  "project": "string",
  "query": "string",
  "results": [
    {
      "message_id": "string",
      "project": "string",
      "source_message_id": "string",
      "conversation_id": "string | null",
      "role": "string | null",
      "content": "string",
      "created_at": "RFC3339 UTC timestamp",
      "score": "number"
    }
  ]
}
```

`source_message_id` is the ID of the matched message itself, allowing direct linking to `save_decision`; `message_id` is an equivalent explicit result identifier. `score` is the retrieval relevance value (FTS5 BM25 may be negative; smaller/more negative ranks first under the current implementation), not a probability. Results are ordered by retrieval rank and capped at `limit`. The adapter maps the retrieval result's message ID to `message_id` and `source_message_id`, its `source_id` (the conversation ID in chat search) to `conversation_id`, `content` to `content`, `timestamp` to `created_at`, and `relevance_score` to `score`. The current retrieval schema does not include message role, so `role` is `null`.

Errors: `INVALID_ARGUMENT`, `PROJECT_NOT_FOUND` (if applicable), `RETRIEVAL_UNAVAILABLE`, `INTERNAL_ERROR`.

## `retrieve_context`

**Purpose:** Return a bounded, traceable context bundle for a project/query, combining relevant messages and eligible canonical decisions.

Signature: `retrieve_context(project: string, query: string, token_budget: integer) -> RetrieveContextResult`

| Argument | Required | Type and validation |
|---|---:|---|
| `project` | yes | Non-empty project identifier; shared project rules apply. |
| `query` | yes | Non-empty after trimming check; retrieval semantics apply. |
| `token_budget` | yes | Positive integer, not boolean. Maximum is implementation-configured; requests above it return `INVALID_ARGUMENT` with the accepted maximum. |

Result:

```json
{
  "project": "string",
  "query": "string",
  "items": [
    {
      "record_id": "string",
      "source_message_id": "string",
      "created_at": "RFC3339 UTC timestamp",
      "record_kind": "chat_message | canonical_decision",
      "status": "draft | approved | deprecated | null",
      "content": "string",
      "relevance_score": "number"
    }
  ],
  "rendered_context": "string",
  "token_count": "integer",
  "token_budget": "integer",
  "token_count_is_estimate": "boolean",
  "token_counter": "string",
  "excluded": [
    {
      "record_id": "string",
      "source_message_id": "string",
      "created_at": "RFC3339 UTC timestamp",
      "record_kind": "chat_message | canonical_decision",
      "status": "draft | approved | deprecated | null",
      "reason": "token_budget | duplicate_content"
    }
  ]
}
```

The output item and exclusion fields map the retrieval branch's current `ContextBundle` DTO. `project_id` is represented publicly as `project`; timestamps map to `created_at`. For chat records, `source_message_id` is the record's own message ID. For canonical decisions, it is the stored source-message provenance ID. Context must contain only records from the requested project. Approved canonical decisions take priority in ordering over historical messages. Deprecated decisions are excluded by default; drafts are also excluded by default. The public tool has no inclusion flags.

Budget behavior: the complete serialized `rendered_context`, including metadata, must have `token_count <= token_budget`. Include whole records only; never truncate a record to fit. Scan ranked candidates in order, skip and report records that do not fit, and continue so a later smaller record can fit. Exact normalized-content duplicates may be skipped and reported. An empty context is valid, with count zero. Return the counter name and whether the count is an estimate. Existing retrieval code defaults to a UTF-8 byte estimator and labels it estimated; this may differ from model tokenization. The public request has no default budget (it is required), while a 2,000-unit internal default exists in retrieval code; team should confirm that distinction and define a maximum.

Errors: `INVALID_ARGUMENT`, `PROJECT_NOT_FOUND` (if applicable), `RETRIEVAL_UNAVAILABLE`, `INTERNAL_ERROR`.

## Decisions requiring team approval

1. Confirm the public names and result mappings, particularly `source_message_id` versus retrieval DTO `source_id`, and whether `search_chat_history` needs `conversation_id`/`role` in returned records.
2. Confirm allowed message roles and whether arbitrary roles must be accepted.
3. Confirm status lifecycle: `save_decision` creates `draft`, and identify the separate mechanism for approval/deprecation; there is currently no transition tool.
4. Confirm search `limit` default/range and the retrieval draft's plain-term query behavior.
5. Confirm token-budget units/counter, maximum, and whether the public tool requires a budget (as proposed) despite the internal 2,000 default.
6. Confirm MCP error encoding and whether a nonexistent project is distinguishable from an empty project. The current database contract is absent, so `PROJECT_NOT_FOUND` is conditional.
7. Approve the timestamp serialization and result envelope. Existing retrieval candidates validate ISO 8601 but do not mandate UTC serialization.

The server has no project registry, so an unknown but authorized project is indistinguishable from a valid project with no records; reads may return empty results and writes may provision its namespace. An unconfigured or unauthorized project/action returns `ACCESS_DENIED` before reaching persistence or retrieval. `ORBIT_LOCAL_PROJECT_ACCESS` configures the current stdio server's explicit process-level project/action allowlist. Because stdio does not establish an individual caller identity, this policy applies to every client connected to that server process and does not protect against untrusted local processes. A source ID absent from the requested project returns `SOURCE_MESSAGE_NOT_FOUND`.

The server currently supports local stdio transport only. Before exposing it over a network or other remote transport, add authentication and transport security, and review authorization and project access controls. Do not expose an unauthenticated remote endpoint.

No existing public function signature is changed by this document. The proposed four tools are new contracts; current `ping()` remains compatible. Any implementation proposal that changes these contracts must be brought to the team for approval before changing public signatures.
