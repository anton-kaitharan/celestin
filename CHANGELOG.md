# Change log

Record each meaningful project change during the event, newest first. Update this file in the same commit as the change. Use a real timestamp with timezone, for example `YYYY-MM-DDTHH:MM:SS+05:30`.

## Event build log

## 2026-10-11T09:11:48+05:30 — Add reusable Orbit project-memory skill

- Changed: Added `.agents/skills/orbit/SKILL.md`, guidance in `AGENTS.md`, and README setup, invocation, and troubleshooting instructions.
- Why: Give Codex a reusable workflow for safe project-scoped retrieval and explicitly approved saves through the existing Orbit MCP tools.
- Validation: `python C:\Users\asus\.codex\skills\.system\skill-creator\scripts\quick_validate.py .agents\skills\orbit` — valid; `python -m pytest -p no:cacheprovider -o addopts=''` — 112 passed; `git diff --check` — clean.
- Limitations: No canonical project identifier is documented yet. Codex's configured `orbit` entry has no explicit project/action allowlist, so project operations may remain denied until its local configuration is set.

## 2026-10-11T09:06:01+05:30 — Align README with the implemented project

- Changed: Rewrote `README.md` with cross-platform installation, database configuration, Codex CLI setup, MCP tool examples, and current behavior and limitations.
- Why: The previous setup was Windows-only and omitted important implementation details for running and demonstrating Orbit.
- Validation: Compared instructions against `pyproject.toml`, the SQLite engine, MCP server, and retrieval documentation; checked Markdown links and `git diff --check`.
- Limitations: No application UI or hosted demo is implemented; the demo uses a connected local MCP client.

## 2026-10-11T05:27:52+05:30 — Add fail-closed local project authorization

- Changed: Added an authorization policy interface with principal/project/action grants, an explicit local stdio project/action allowlist, guarded server-facing persistence and retrieval adapters, authorization tests, and setup/threat-model documentation.
- Why: Prevent project-name arguments from granting access and deny project operations unless the server operator configured the requested project and action.
- Validation: `python -m pytest -p no:cacheprovider -o addopts=''` — 112 passed; `git diff --check` — clean.
- Limitations: Stdio does not authenticate individual clients. All clients connected to one process inherit its configured grants; this does not protect against untrusted local processes. No remote authentication or deployment was added.

## 2026-10-11T05:18:21+05:30 — Validate end-to-end MCP memory workflow

- Changed: Added a two-process stdio MCP integration test using a disposable SQLite database; fixed FastMCP error wrapping so `SOURCE_MESSAGE_NOT_FOUND` keeps its stable category over the protocol.
- Why: Validate persistence across a server restart, cross-session retrieval, project scoping, lifecycle filtering, and the public error envelope without touching project data.
- Validation: `python -m pytest -p no:cacheprovider -o addopts=''` — 107 passed; `git diff --check` — clean. The focused test also passed independently.
- Limitations: No authenticated identity or identity-to-project authorization exists. `project` remains a caller-selected scope; current isolation prevents cross-project record mixing but does not authorize callers. Decision approval was performed through the repository in the test fixture because no approval MCP tool exists.

## Template setup — 10 October 2026

- Changed: Added the event README, rules, agent guide, change log, AI usage record, submission checklist, Git ignore rules, and pull request checklist.
- Why: Give participants a consistent starting point for event rules, basic coding patterns, documented changes, and regular commits.
- Validation: Checked local Markdown links, event detail consistency, required documents, and diff whitespace before publishing.
- Limitations: Documentation scaffolding only; no participant project, application, or working demo is included. GitHub publication and template status are verified separately after creation.
