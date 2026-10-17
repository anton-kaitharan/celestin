# Celestin — Persistent Memory for AI Agents

Celestin (formerly Orbit) is a local MCP server that gives AI agents project-scoped persistent
memory. It stores chat messages and project decisions in SQLite, then provides
tools to search chat history and retrieve a bounded, traceable context bundle.
Search is SQLite FTS5 keyword search, optionally combined with local semantic
(embedding) search; see [Semantic search](#semantic-search-optional).
The server communicates with MCP clients over stdio.

## Requirements

- Python 3.10 or newer
- SQLite built with FTS5 support (used for full-text search)
- An MCP-compatible client for the tools; Codex CLI instructions are below

The project uses the [Python MCP SDK](https://github.com/modelcontextprotocol/python-sdk)
and Python's standard-library SQLite interface. It does not bundle an external
dataset. Optional semantic embeddings run locally; experimental PageIndex can
send a query and document outline to the configured model provider when enabled.

## Install

From the repository root, create and activate a virtual environment, then
install Celestin in editable mode:

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

Installation provides the `celestin` command (the pre-rename `orbit` and
`memory-bridge` commands remain as aliases) and the `celestin-index` command. The SQLite database and its schema
are created automatically the first time the server connects. By default, the
database is `data/memory_bridge.db` relative to the process working directory.
Set `CELESTIN_DB_PATH` to an absolute writable path to choose another
location. The older `MEMORY_BRIDGE_DB_PATH` is still honored when
`CELESTIN_DB_PATH` is unset.

### PostgreSQL migration foundation (explicit operator command)

Local SQLite remains the default and is not affected by the PostgreSQL tooling.
For a separately provisioned, empty hosted database, install the optional
`postgres` extra and explicitly run `celestin-migrate` with
`CELESTIN_POSTGRES_DSN` supplied through an approved secret mechanism. The MCP
server never runs hosted migrations during startup. See
[PostgreSQL migrations](docs/postgres-migrations.md) for safety checks and the
required handling for an existing database without a migration ledger.

## Run with Codex CLI

Register Celestin as a local stdio MCP server. Set the database path to a location
you can write to. Run this from a shell where the virtual environment is
activated:

**macOS / Linux**

```sh
codex mcp add celestin \
  --env CELESTIN_DB_PATH=/absolute/path/to/celestin.db \
  --env 'CELESTIN_LOCAL_PROJECT_ACCESS={"project-name":["read","write"]}' \
  -- celestin
codex mcp list
```

If Codex cannot find `celestin`, use the absolute path to `.venv/bin/celestin` after
the `--` separator.

**Windows PowerShell**

```powershell
codex mcp add celestin --env 'CELESTIN_DB_PATH=C:\path\to\celestin.db' --env 'CELESTIN_LOCAL_PROJECT_ACCESS={"project-name":["read","write"]}' -- .venv\Scripts\celestin.exe
codex mcp list
```

Use an absolute executable path if needed, and quote it if the path contains
spaces. Restart Codex after registration if it was already running. To remove
the server later, run `codex mcp remove celestin`.

Other MCP clients can launch `celestin` directly over stdio (or use the absolute
path to the executable in `.venv`). A minimal client configuration is:

```json
{
  "mcpServers": {
    "celestin": {
      "command": "celestin",
      "args": [],
      "env": {
        "CELESTIN_DB_PATH": "/absolute/path/to/celestin.db",
        "CELESTIN_LOCAL_PROJECT_ACCESS": "{\"project-name\":[\"read\",\"write\"]}"
      }
    }
  }
}
```

### Optional Neon project tooling

This checkout's `.codex/config.toml` registers the Neon MCP server with OAuth and
pins its tools to the existing `celestin-dev` Neon project. On first use, reopen
the project in Codex and complete Neon sign-in if prompted. This connection is
for Neon project management; it does not switch Celestin's MCP server or local
SQLite storage to Neon. PostgreSQL migrations remain an explicit operator
action described in [PostgreSQL migrations](docs/postgres-migrations.md).

### Local project access

The stdio server requires an explicit process-level project/action allowlist for
the four data tools. `CELESTIN_LOCAL_PROJECT_ACCESS` is a JSON object whose keys
are exact project names and whose values contain `read`, `write`, or both. An
unset policy denies data-tool calls while `ping` remains available. For existing
client configurations, `ORBIT_LOCAL_PROJECT_ACCESS` remains a fallback; the
Celestin variable takes precedence if both are set.

This policy grants access to the server process, not to an authenticated user.
Every MCP client connected to that process inherits the same grants, and it does
not protect against other untrusted processes running as the same local user.
Keep the server on local stdio; do not expose it remotely.

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
as `2000`. Each tool call is made through the connected MCP client; Celestin does
not include a separate web UI.

## Semantic search (optional)

Celestin keeps **SQLite as the only source of truth** and **FTS5 for exact
keyword search** (rare terms, error codes, identifiers, names). Semantic search
adds a derived vector index so reworded queries ("why did we avoid hosted
infrastructure?") can find a memory saved as "we chose local persistence to
avoid deployment overhead". It is **off by default**.

- Vector store: [sqlite-vec](https://github.com/asg017/sqlite-vec) in a *separate*
  SQLite file (default `<database>.vectors.db`). It holds only source references
  (`chat_message`/`canonical_decision` + SQLite id), project, a content hash and the
  vector, never message text. It can be deleted and rebuilt at any time.
- Embeddings: local `BAAI/bge-small-en-v1.5` (384 dims, MIT) through
  [fastembed](https://github.com/qdrant/fastembed) (ONNX Runtime, no PyTorch).
  The first use downloads the model (about 130 MB) from Hugging Face; after that
  it runs offline (set `HF_HUB_OFFLINE=1` to forbid network access). Your text is
  never sent to an embedding API.
- Ranking: FTS5 and vector results are merged with Reciprocal Rank Fusion (k=60),
  which uses ranks only, since BM25 and cosine scores are not comparable. A record
  found by both paths appears once and ranks higher. Draft and deprecated decisions
  are still excluded and approved decisions still come first. With fusion active,
  `score`/`relevance_score` is `-RRF score` (lower is better, like BM25).
- Only decisions with status `approved` are embedded (drafts and deprecated
  decisions are never returned by retrieval).

Enable it:

```sh
python -m pip install -e ".[semantic]"
export CELESTIN_SEMANTIC_SEARCH=1          # set this for the server (e.g. in the Codex --env)
celestin-index sync                        # backfill existing messages and approved decisions
```

New chat messages are indexed best-effort when they are ingested. Run
`celestin-index sync` after approving decisions, after an embedding outage, or to
backfill an existing database. Commands (they only write the vector file, never
the SQLite database):

- `celestin-index status`: dry run showing what is missing or stale, plus an integrity check.
- `celestin-index sync`: embed missing or changed records, remove stale entries.
  Idempotent and resumable; batches of `--batch-size` (default 64).
- `celestin-index rebuild`: reset the vector file and re-embed everything. Use it after
  changing `CELESTIN_EMBEDDING_MODEL` or if `status` reports corruption.

Settings (environment variables): `CELESTIN_SEMANTIC_SEARCH`,
`CELESTIN_EMBEDDING_MODEL`, `CELESTIN_EMBEDDING_CACHE_DIR`,
`CELESTIN_VECTOR_DB_PATH`, `CELESTIN_SEMANTIC_RELATIVE_MARGIN` (optional: drop hits this far below the best one; see docs/pageindex.md), `CELESTIN_SEMANTIC_MIN_SIMILARITY` (cosine cut-off,
default 0.6). The cut-off keeps unrelated nearest neighbours out; the default was
chosen from a small probe with the default model (related pairs scored 0.68-0.72,
unrelated up to 0.58), so tune it for your data.

When semantic search is enabled the tool responses gain a `retrieval` object:
`{"mode": "hybrid" | "fts_fallback", "warnings": [...]}`. `fts_fallback` means the
embedding model or vector index was unavailable, so the results are keyword-only;
`warnings` says why and which command repairs it. Without the setting, responses
are unchanged.

Verify it end to end (uses a real model, downloads it once):

```sh
CELESTIN_RUN_MODEL_TESTS=1 python -m pytest tests/semantic/test_real_model.py
```

More detail: [docs/semantic-search.md](docs/semantic-search.md).

## Heading-section retrieval and the strategy selector

`celestin-docs index --project my-project docs/` indexes Markdown documents into heading sections (no model needed). `retrieve_context` then also searches those sections with FTS5, and with semantic search too when `CELESTIN_SEMANTIC_SEARCH=1` (run `celestin-index sync`, or let `celestin-docs` embed them). Section results carry a `source` (document, section, line, content hash). Disable with `CELESTIN_DOC_SECTIONS=0`. Setting `CELESTIN_RETRIEVAL_SELECTOR=adaptive` makes retrieval pick one primary strategy plus at most one fallback instead of running every enabled strategy (default `all`); on the frozen evaluation it matched running everything on answer quality with one strategy fewer, but did not reduce context size. Design, results and limits: [docs/retrieval-strategies.md](docs/retrieval-strategies.md).

### Reducing context noise (opt-in)

Section-based retrieval returns many weakly related sections. `CELESTIN_SEMANTIC_SECTION_MARGIN=0.08` keeps only semantic section hits close to the best one: on two evaluation sets it kept every answer and cut 22-35% of the context bytes. It is recommended but off by default; `CELESTIN_SECTION_TOP_K` and `CELESTIN_SECTION_EXCERPT_BYTES` are experimental and lost answers in testing. Diagnosis and results: [docs/context-optimization.md](docs/context-optimization.md).

Heading sections can also be reordered with `CELESTIN_SECTION_RERANK=lexical`,
`lexical_rrf`, `cross_encoder`, or `cross_encoder_rrf`. This is off by default;
the bounded shortlist defaults to 20 (`CELESTIN_SECTION_RERANK_SHORTLIST`). The
lexical modes are deterministic and use no model. Cross-encoder modes use
FastEmbed from the optional `semantic` extra and download
`Xenova/ms-marco-MiniLM-L-6-v2` on first use unless it is already cached;
`CELESTIN_SECTION_RERANK_MODEL` can select another compatible model. The
margin + cross-encoder RRF + top-8 combination improved measured useful
evidence per byte in the dev and held-out sweeps and on six new challenge
questions. The top-8 choice followed review of the earlier held-out results,
and the new challenge sample is small, so keep it experimental and opt-in;
defaults remain unchanged. See the [development results](eval/results_rerank_sweep.md),
[held-out results](eval/results_rerank_sweep_heldout.md), and
[new-query challenge](eval/results_rerank_challenge.md).

## Document retrieval with PageIndex (optional, experimental)

Long Markdown documents (specs, design notes) can be indexed into heading-section trees and searched by having an LLM pick sections from the outline, the approach of [PageIndex](https://github.com/VectifyAI/PageIndex). It is off by default, needs a model API key and sends the query and the outline to that provider. **It has been tested only with a fake navigation model; it has not been shown to improve retrieval with a real LLM.**

```sh
python -m pip install -e ".[pageindex]"
celestin-docs index --project my-project docs/
export CELESTIN_PAGEINDEX=1 CELESTIN_PAGEINDEX_MODEL=<litellm model>   # plus the provider's key variable
```

`retrieve_context` then also returns `document_section` items with a `source` (document, section, line, content hash) and `retrieval.navigation` (latency, estimated and observed tokens, cache hit); a failure of this backend only adds a warning. Maintenance: `celestin-docs status|rebuild|prune|remove`. Routing, cost, caching, security notes, evaluation (`python eval/run_eval.py`) and limits: [docs/pageindex.md](docs/pageindex.md).

## Local System 1 retrieval experiment (opt-in)

The existing `retrieve_context` pipeline remains the default. Install the local
Laya adapter and set `CELESTIN_RETRIEVAL_PATH=system1` to rerank the already
authorized candidates with stock `convaiinnovations/laya-multilingual`:

```sh
python -m pip install -e ".[system1-laya]"
export CELESTIN_RETRIEVAL_PATH=system1
celestin
```

Laya loads lazily on first use and downloads its pinned checkpoint through the
Hugging Face cache if it is not already present. Allow time and disk space for
that first download; warm the model before a demo. Each candidate is evaluated
with the query, complete text, record type, and baseline retrieval evidence,
with a 2,048-token state limit. Oversized inputs and inference errors keep the
baseline order and produce content-free diagnostics. Laya only reranks; it
does not search or persist records. MCP arguments/responses, SQLite,
authorization, decision filters, provenance, and context assembly stay
unchanged. Set `CELESTIN_RETRIEVAL_PATH=baseline` or leave it unset for the
original path. `CELESTIN_RETRIEVAL_PATH=julia1` retains the earlier Julia-1
experiment as a separate local option.

The earlier Julia-1 evaluation is historical and does not measure Laya. Its
results and raw per-query data remain in
[eval/system1_results](eval/system1_results/README.md). Laya has now been
evaluated against H on matched dev, held-out, and challenge questions and
budgets; it showed mixed quality and substantially higher CPU latency, so no
overall improvement is claimed. See the [Laya results and per-query artifacts](eval/laya_results/README.md).

## Behavior and limitations

- Every read and write is scoped to the supplied project name. There is no
  project registry, so an unknown project and a project with no matching data
  both appear empty.
- Newly saved decisions are drafts. Context retrieval excludes draft and
  deprecated decisions by default and prioritizes approved decisions. The
  current tool set does not include an approval or deprecation operation.
- Semantic indexing is not atomic with SQLite writes; `celestin-index sync` reconciles.
  Vector search is exact (brute-force) KNN, suitable for thousands to low
  hundreds of thousands of records, not millions.
- FTS5 tries the strict AND query first; only if it finds nothing, bounded fallbacks run (stopwords removed, then an OR query that needs 60% of the terms and always requires identifier-like terms, for queries with 3+ content terms). Quoted phrases must match adjacently. Set `CELESTIN_FTS_RELAX=0` to disable the fallbacks.
- Context budgets count UTF-8 bytes as an estimate, not exact tokens for a
  particular model. Records are included whole; oversized records are skipped.
- Keyword search uses SQLite FTS5. Queries are treated as plain Unicode terms joined
  with AND; punctuation is discarded. Search scores are relevance values, not
  probabilities.
- The server supports local stdio with a fail-closed process-level project
  allowlist. Stdio does not authenticate individual callers, so all connected
  clients share the server process's grants. This does not protect against
  untrusted same-user processes and is not remote authorization. Do not expose
  the server remotely.

## Tests

Install the test dependency and run the test suite from the repository root
(use `.[test,semantic]` to include the vector-index tests; they are skipped
without the semantic extra):

```sh
python -m pip install -e ".[test,semantic]"
python -m pytest -q
```

The tests use temporary SQLite databases, synthetic records and a deterministic
fake embedding provider (tests only, not semantic search). They do not require
network access after dependencies are installed. The real-model test is opt-in
(see above).

The PostgreSQL migration runner and authorization session also have fake-based
tests that need no server. Live PostgreSQL tests are separately marked and
skipped by default. See [PostgreSQL integration tests](docs/postgres-integration-tests.md)
for the disposable database requirements and explicit opt-in command. Those
tests never create or drop a database and do not enable hosted MCP mode.

## Optional granular commit watcher

The repository-local `commit_granular` agent and PowerShell watcher can create
separate local commits for independent, completed changes. The watcher starts
only from a clean working tree on a named branch, waits for 90 seconds of
quiet (polling every 3 seconds by default), and then asks Codex CLI to review
the changed snapshot. Keep the watcher terminal open while using it:

```powershell
pwsh -NoProfile -File scripts/commit-watcher.ps1
```

Configure the debounce and polling intervals as needed:

```powershell
pwsh -NoProfile -File scripts/commit-watcher.ps1 -QuietPeriodSeconds 120 -PollIntervalSeconds 5
```

Press Ctrl+C to stop. Scripted callers can use `-StopFilePath <path>` and
create the specified file for graceful shutdown. The watcher pauses if the
branch changes, a Git operation or index lock appears, Codex exits with an
error, or changes remain uncommitted after a successful run. Inspect and
resolve the state before restarting. It uses `codex exec` with the
`workspace-write` sandbox and enables the repository custom agent for that
invocation. It never runs remote Git commands or pushes.

Run the disposable watcher checks with:

```powershell
pwsh -NoProfile -File scripts/test-commit-watcher.ps1
```

To exercise actual granular commits, run
`pwsh -NoProfile -File scripts/test-commit-agent.ps1`; it requires a working
local Codex CLI execution environment and uses a disposable repository.

## Event documentation

This project was developed for the DevDay Exchange Community: Kochi hackathon
on 10–11 October 2026. See [RULES.md](RULES.md) for event requirements,
[SUBMISSION.md](SUBMISSION.md) for submission details, [AI_USAGE.md](AI_USAGE.md)
for the Codex usage record, and [CHANGELOG.md](CHANGELOG.md) for the development
log. The [event and registration page](https://luma.com/z10nz8gp) has the event
details.

## Codex skill: Celestin project memory

The repository skill at `.agents/skills/orbit/SKILL.md` helps Codex retrieve saved project context and save explicitly approved messages or decisions with the Celestin MCP tools. Invoke it with `$orbit`, add a topic with `$orbit database migration`, or request the save workflow with `$orbit save`. OpenAI documents skill invocation with the `$skill-name` form; this skill does not register a custom `/orbit` slash command. If the skill is missing, pull the repository and restart Codex so it discovers `.agents/skills/`.

The canonical Celestin project identifier for this repository is `celestin` (legacy names `Orbit`, `orbit`, and `memory-bridge` remain compatibility labels). The `project` argument selects a namespace; it does not grant access. The current local stdio server authorizes the server process, not individual callers, so use only data the connected local process is permitted to access and keep demo data synthetic.

### Configure a repository and teammates

Use the same `celestin` project identifier for this repository. Configure each developer's Celestin MCP entry using the setup above and an absolute database path for that machine. To share data between Codex and the tunnel on this PC, configure both processes to use the same local database path. Repeating a path on another computer does not share the SQLite file. Pulling this repository gives teammates the skill; they must configure their own MCP connection and restart Codex if the skill or tools are not discovered. Check `codex mcp list` and `codex mcp get celestin` when tools are unavailable. Report tool errors as returned, and do not retry using a different project.

`$orbit` and topic-specific retrieval are read-only. `$orbit save` uses the existing ingestion and decision tools; a saved decision starts as a draft and is not automatically approved.
# Local System 1 experiment

The local Julia-1 path is implemented as an opt-in experiment and remains
disabled by default. The paired evaluation found lower answer-bearing context
and MRR, plus substantially higher latency, than the existing path on all six
dataset/budget comparisons. The existing path remains the recommended default.
See the [product brief](PRD.md), [technical design](TRD.md), [flow](App_flow.md),
[architecture](Design.md), [schema impact](database_schema.md), and [measured
results](eval/system1_results/README.md). Hosted inference and training remain
out of scope.

Research: [Julia-1 model card](https://huggingface.co/SupersonicLabs/Julia-1) · [CLM project](https://github.com/Contrastive-LM/CLM) · [TypeSafe AI](https://typesafe.ai/)
