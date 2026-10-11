---
name: orbit
description: Retrieve this repository's saved Orbit project context or save an explicitly approved message and decision using the Orbit MCP tools. Use for `$orbit`, project-memory questions, or requested memory saves; never guess a project identifier.
---

# Orbit project memory

Use the Orbit MCP server as the source of saved project context. The skill guides tool use; it does not create a separate memory store.

## Resolve the project

1. Identify the current repository root.
2. Look for a project identifier explicitly designated for Orbit in repository instructions or configuration.
3. Do not infer the identifier from the directory, package, Git remote, a conversation, or sample names such as `demo` or `orbit-demo-2026`.
4. If no single documented identifier is available, ask the user which Orbit project to use and wait. The MCP `project` argument selects a namespace; it does not grant access.

## Retrieve context

- For `$orbit` without a query, call `retrieve_context` with a broad query covering the repository's overview, confirmed decisions, requirements, constraints, research, risks, and open questions. Use `token_budget: 2000`.
- For `$orbit <query>`, pass the topic as the `query` to `retrieve_context`, with the resolved `project` and `token_budget: 2000`.
- Use `search_chat_history` for a focused follow-up when the context bundle does not answer the question. Use its optional `limit` only when needed.
- Report the returned material concisely. Separate saved evidence from inference, preserve source IDs and timestamps when useful, and report empty results or tool failures accurately. Never fill gaps with invented memory.
- Retrieval is read-only. Do not call ingestion or decision tools during a retrieval request.

## Save context with `$orbit save`

1. Identify the specific message or decision the user wants saved from this conversation. Never save the literal word `save`.
2. If the target or project is ambiguous, ask before writing. For a newly proposed architectural decision, require the user's explicit approval of that decision before persisting it. A request to save an already clearly approved decision is sufficient authorization.
3. `save_decision` requires a source message ID. Call `ingest_chat_message` first with the actual source text, its correct `role`, the resolved `project`, and a `conversation_id` supplied by the client or user. Do not invent source IDs or conversation IDs; if no usable conversation ID is available, ask the user.
4. Pass the exact `message_id` returned by ingestion as `source_message_id` to `save_decision`. Do not call the decision tool if ingestion failed or returned no ID.
5. Report the actual message ID, decision ID, and status from successful results. Saving through this tool creates a draft; it does not approve the decision in Orbit. Do not claim that the record is canonical or approved unless the tool result says so.

Use only these registered tool contracts:

- `ingest_chat_message(project, conversation_id, role, content)`
- `save_decision(project, key, value, source_message_id)`
- `search_chat_history(project, query, limit?)`
- `retrieve_context(project, query, token_budget)`

Do not add tools, bypass authorization, or modify database or server configuration as part of a memory request. If Orbit is unavailable or returns `ACCESS_DENIED`, report the actual failure and explain that the operator must configure the local MCP server's database path and project/action allowlist. Do not retry against a different project.
