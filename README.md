# Orbit — Persistent Memory for AI Agents

An MCP-powered persistent memory system for AI agents and coding assistants.
It stores chat messages and project decisions in SQLite and exposes project-scoped
search and context retrieval through the official Python MCP SDK.

## Local setup

Requires Python 3.10 or newer. From the repository root:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e .
```

Run the server over stdio:

```powershell
orbit
```

For Codex CLI, register the local stdio server from the repository environment:

```powershell
codex mcp add orbit --env 'MEMORY_BRIDGE_DB_PATH=C:\path\to\memory_bridge.db' -- orbit
codex mcp list
```

Replace the example database path with an absolute writable path. The server
must be installed in the environment where `orbit` resolves. If needed, use the
absolute path to `.venv\Scripts\orbit.exe` in place of `orbit`. Codex stores
this server in the user's MCP configuration; restart the Codex client after
registration if it is already running. If you previously registered the old
server, remove it with `codex mcp remove memory-bridge` before registering
Orbit. To remove Orbit later, run `codex mcp remove orbit`. Quote the absolute
executable path if it contains spaces.

Other local MCP clients can launch `orbit` directly (or use the environment's
absolute path to `.venv\Scripts\orbit.exe`).
The server exposes `ingest_chat_message`, `save_decision`,
`search_chat_history`, `retrieve_context`, and `ping`.

The database is created at `data/memory_bridge.db` by default. Set `MEMORY_BRIDGE_DB_PATH` before launching the server to choose another path. Saved decisions start in `draft` status; context retrieval gives approved decisions priority and excludes drafts and deprecated decisions.

### Inspect and invoke the MCP tools

Connect another MCP-compatible client over stdio using this command configuration:

```json
{
  "mcpServers": {
    "orbit": {
      "command": "orbit",
      "args": []
    }
  }
}
```

The client should discover the four memory tools and the `ping` health check
through MCP `tools/list` and invoke them through `tools/call`. For example, call
`ingest_chat_message` with
`{"project":"demo","conversation_id":"conv-1","role":"user","content":"We chose Python."}`.
Use its returned `message_id` as `source_message_id` when calling
`save_decision`. Search reads persisted messages and returns their IDs,
conversation IDs, timestamps, and relevance scores. Context retrieval combines
eligible decisions and messages, returning source traceability and token
counter metadata. The current counter is a UTF-8 byte estimate, and
`retrieve_context` accepts budgets from 1 through 20,000.

### Tests

```powershell
python -m pip install -e ".[test]"
python -m pytest -q
```

## DevDay Exchange resources

This project was built from the DevDay Exchange Community: Kochi template. Event guidance remains in [RULES.md](RULES.md), [AGENTS.md](AGENTS.md), and [SUBMISSION.md](SUBMISSION.md). See [AI_USAGE.md](AI_USAGE.md) and [CHANGELOG.md](CHANGELOG.md) for event documentation.

- Event: 10–11 October 2026 at JAIN University, Kochi.
- [Event and registration on Luma](https://luma.com/z10nz8gp)

## Codex skill: Orbit project memory

The repository skill at `.agents/skills/orbit/SKILL.md` helps Codex retrieve saved project context and save explicitly approved messages or decisions with the Orbit MCP tools. In Codex, invoke it with `$orbit`, add a topic with `$orbit database migration`, or request the save workflow with `$orbit save`. A skill does not register a custom `/orbit` slash command. If the skill is not listed, pull the repository and restart Codex so it discovers `.agents/skills/`.

This repository does not yet designate a canonical Orbit project identifier. The skill will ask until the team documents one in this repository; do not use `demo` or `orbit-demo-2026` as an assumed project.

### Configure Orbit for a repository

Choose one stable project identifier and document it in the repository's `AGENTS.md` or README. Each local MCP server process needs an explicit grant for that exact identifier. For Codex CLI, first inspect the existing entry with `codex mcp get orbit`; do not add a duplicate. When registering Orbit in a new Codex configuration, provide the database path and local project/action allowlist:

```powershell
codex mcp add orbit `
  --env "MEMORY_BRIDGE_DB_PATH=C:\path\to\memory_bridge.db" `
  --env 'ORBIT_LOCAL_PROJECT_ACCESS={"project-key":["read","write"]}' `
  -- orbit
```

Replace `project-key` and the database path with the team's chosen values. On this PC, use the same absolute database path for the tunnel-launched server and Codex only when both are intended to share that local database. A matching path on another computer does not share the file. This allowlist is process-level access control, not per-user authentication.

### Teammate setup and troubleshooting

Teammates get the skill by pulling the repository; no separate skill installer is needed for this repo-local skill. They must configure their own Codex MCP entry with the agreed project grant and database path. Check `codex mcp list` and `codex mcp get orbit` if Orbit tools are unavailable. If a call returns `ACCESS_DENIED`, confirm that the exact project identifier and requested `read` or `write` action are granted in the process environment; do not retry using another project. `$orbit` retrieval is read-only. `$orbit save` ingests a source message and creates a decision draft; it does not approve the database decision status.
