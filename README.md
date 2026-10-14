# Orbit — Persistent Memory for AI Agents

Orbit is a local MCP server that gives AI agents project-scoped persistent
memory. It stores chat messages and project decisions in SQLite, then provides
tools to search chat history and retrieve a bounded, traceable context bundle.
The server communicates with MCP clients over stdio.

## Requirements

- Python 3.10 or newer
- SQLite built with FTS5 support (used for full-text search)
- An MCP-compatible client for the tools; Codex CLI instructions are below

The project uses the [Python MCP SDK](https://github.com/modelcontextprotocol/python-sdk)
and Python's standard-library SQLite interface. It does not use an external
dataset or embedding service.

## Install

From the repository root, create and activate a virtual environment, then
install Orbit in editable mode:

**macOS / Linux**

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

**Windows PowerShell**

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e .
```

Installation provides the `orbit` command. The SQLite database and its schema
are created automatically the first time the server connects. By default, the
database is `data/memory_bridge.db` relative to the process working directory.
Set `MEMORY_BRIDGE_DB_PATH` to an absolute writable path to choose another
location.

```powershell
$env:ORBIT_LOCAL_PROJECT_ACCESS='{"demo":["read","write"]}'
orbit
```

Project tools fail closed unless the server operator explicitly configures
`ORBIT_LOCAL_PROJECT_ACCESS` as a JSON object mapping each permitted project to
`read`, `write`, or both. Omit an action to deny it. For example,
`{"demo":["read"]}` allows reads for `demo` and denies writes. This is a
launch-scoped local policy, not caller authentication: stdio does not identify
individual MCP clients, so any process able to connect to this server inherits
the configured project permissions. Do not treat the tool's `project` argument
as authorization, and do not use this policy to expose the server remotely.

For Codex CLI, register the local stdio server from the repository environment:

```powershell
codex mcp add orbit --env 'MEMORY_BRIDGE_DB_PATH=C:\path\to\memory_bridge.db' --env 'ORBIT_LOCAL_PROJECT_ACCESS={"demo":["read","write"]}' -- orbit
codex mcp list
```

**macOS / Linux**

```sh
codex mcp add orbit \
  --env MEMORY_BRIDGE_DB_PATH=/absolute/path/to/orbit.db \
  --env 'ORBIT_LOCAL_PROJECT_ACCESS={"demo":["read","write"]}' \
  -- orbit
codex mcp list
```

If Codex cannot find `orbit`, use the absolute path to `.venv/bin/orbit` after
the `--` separator.

**Windows PowerShell**

```powershell
codex mcp add orbit --env 'MEMORY_BRIDGE_DB_PATH=C:\path\to\orbit.db' -- .venv\Scripts\orbit.exe
codex mcp list
```

Use an absolute executable path if needed, and quote it if the path contains
spaces. Restart Codex after registration if it was already running. To remove
the server later, run `codex mcp remove orbit`.

Other MCP clients can launch `orbit` directly over stdio (or use the absolute
path to the executable in `.venv`). A minimal client configuration is:

```json
{
  "mcpServers": {
    "orbit": {
      "command": "orbit",
      "args": [],
      "env": {
        "MEMORY_BRIDGE_DB_PATH": "/absolute/path/to/orbit.db"
      }
    }
  }
}
```

## Available tools

- `ping` confirms that the server is responding.
- `ingest_chat_message` stores a message under a project and conversation and
  returns its `message_id`.
- `save_decision` stores a project decision as a **draft**, linked to a message
  in the same project. Pass the returned message ID as `source_message_id`.
- `search_chat_history` searches project messages with plain text terms and
  returns ranked results (default limit 10, maximum 100).
- `retrieve_context` combines eligible decisions and relevant messages within
  a required budget from 1 to 20,000 units.

For example, in an MCP client, first call `ingest_chat_message` with:

```json
{
  "project": "demo",
  "conversation_id": "planning",
  "role": "user",
  "content": "We chose SQLite for local storage."
}
```

Use the returned `message_id` in `save_decision`, for example with key
`storage` and value `Use SQLite for local storage.` Then call
`search_chat_history` with project `demo` and query `SQLite local`, or call
`retrieve_context` with the same project and query plus a `token_budget` such
as `2000`. Each tool call is made through the connected MCP client; Orbit does
not include a separate web UI.

## Behavior and limitations

- Every read and write is scoped to the supplied project name. There is no
  project registry, so an unknown project and a project with no matching data
  both appear empty.
- Newly saved decisions are drafts. Context retrieval excludes draft and
  deprecated decisions by default and prioritizes approved decisions. The
  current tool set does not include an approval or deprecation operation.
- Context budgets count UTF-8 bytes as an estimate, not exact tokens for a
  particular model. Records are included whole; oversized records are skipped.
- Search uses SQLite FTS5. Queries are treated as plain Unicode terms joined
  with AND; punctuation is discarded. Search scores are relevance values, not
  probabilities.
- The server currently supports local stdio only and has no caller
  authentication or project authorization. Do not expose it as a remote
  service.

## Tests

Install the optional test dependency and run the test suite from the repository
root:

```sh
python -m pip install -e ".[test]"
python -m pytest -q
```

The tests use temporary SQLite databases and synthetic records. They do not
require network access after dependencies are installed.

## Event documentation

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
