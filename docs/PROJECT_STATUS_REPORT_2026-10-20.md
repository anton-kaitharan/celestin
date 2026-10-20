# Celestin — Project Status Report

**Snapshot:** 2026-10-20 (after `git fetch --all --prune`)
**Canonical product branch:** `main` — `upstream/main` = `origin/main` = `86030ce8` (PR #19, 2026-10-20)
**This checkout:** `retrival` @ `aa1e7122` — **not** the product branch (see §2)
**Supersedes:** `docs/current-project-status.md` (2026-10-18 snapshot)

---

## 1. Executive summary

- The product (local SQLite MCP memory server + a broad search/retrieval stack) is **working and tested**. On a clean `pip install -e .[test]` of `main`, the full suite passes: **634 passed, 45 skipped, 0 failed**.
- The **Resurrection Layer** (Capabilities 1–4) still consists of separate prototypes. They are not wired into the running MCP server or into durable storage.
- **Hosted/shared memory (PostgreSQL)** now has a large foundation: 6 migrations, RLS, request-bound unit of work, and Evidence/Knowledge and candidate-revision schemas. **None of it has run against a live PostgreSQL server**, and MCP still uses only SQLite.
- The **shared-memory contract v0.3** is final. The **delivery/producer contract (D-01…D-13) is blocked on owner decisions**, and it gates the next persistence step (delivery receipts, replay, conversion).
- Between 10-18 and 10-20, all new work landed on `main` from anton-kaitharan (shared-memory contract, Postgres evidence store, capture occurrences, JCS hashing). Your local `main` is **31 commits behind** and needs a pull.

---

## 2. Git / branch state

| Ref | Commit | vs `upstream/main` (ahead / behind) | Notes |
|---|---|---|---|
| `upstream/main` = `origin/main` | `86030ce8` | 0 / 0 | Canonical product. Remote default HEAD = `main` on both remotes. |
| local `main` | `5633ae8d` | 0 / 31 | **Fast-forward needed** (`git pull`). |
| `retrival` (checked out) = `upstream/retrival` | `aa1e7122` | 4 / 138 | Snapshot of the old adapter. Tracks **20,532 `.venv/` files** plus `__pycache__`/egg-info. Has no `README`, `pyproject.toml` or `.gitignore`. Its two v0.3 docs were already imported into `main` (10-19). |
| `origin/Retrieval-layer` (worktree `../Devday-Retrieval-layer`) | `14b4b93d` | 3 / 27 | **3 unmerged test-only commits**: acceptance scenarios doc, evidence/reconstruction/knowledge-state tests (+270 lines). Worktree is clean. |
| `upstream/codex/orbit-project-memory-skill` | `8eef5e44` | 5 / 105 | Probably superseded by PR #18 (granular watcher). Verify, then close. |
| `upstream/codex/celestin-hosted-tenancy-spike` | `2f3ae6b0` | 2 / 40 | 2 stray commits. Verify, then close. |
| `upstream/{MCP, database, fix-ranking-null-source-tie, codex/orbit-package-migration, codex/granular-commit-watcher, codex/celestin-core-memory-platform}`, `origin/Retrival-layer` | — | 0 / n | Fully merged. Safe to delete (after team agreement). |

**Push status:** Every local branch that has an upstream matches its remote (0 ahead). Nothing is waiting to be pushed, and the working tree is clean.

**Activity on `main`:** 143 commits. Authors: anton-kaitharan 63, ATHUL VR 59, Antony K C 13, Anugrah vibi 8. Commits per day: 10-11 = 44, 10-14 = 22, 10-15 = 21, 10-17 = 11, 10-19/20 = 6 each.

---

## 3. Phase / capability completion

Legend: ✅ Done and wired into the product · 🟡 Foundation/prototype, tested offline, not wired · 🟠 Design or decision only · ⛔ Not started

| # | Phase / capability | Status | Est. | Done | Remaining |
|---|---|---|---|---|---|
| 1 | Local MCP memory foundation | ✅ | ~90% | stdio MCP server; tools `ping`, `ingest_chat_message`, `save_decision`, `search_chat_history`, `retrieve_context`; SQLite with atomic migrations (10-18); runtime composition (`build_runtime`) | **No `approve_decision`/`deprecate_decision` MCP tool.** Decisions saved through MCP stay draft and never reach `retrieve_context`. No update/delete/list tools. |
| 2 | Search & retrieval stack | ✅ | ~85% | FTS5 + relaxation, deterministic ranking, budgeted context builder, semantic/vector, hybrid, heading sections, adaptive selector, relevance filter, rerank, System1/Laya, PageIndex (opt-in), eval harness with frozen results | Byte-based budgets (not tokens); small eval corpus; no real-model PageIndex eval; **16 stale tests break when optional extras are installed** (§4). |
| 3 | Local authorization | ✅ | ~80% | Process-level project/action allowlist (`CELESTIN_LOCAL_PROJECT_ACCESS`) | No per-client identity over stdio. |
| 4 | Rename / packaging | ✅ | 100% | `celestin-memory` 0.2.0; `celestin*` CLIs plus `orbit`/`memory-bridge` aliases | — |
| 5 | C1 — Evidence-linked memory | 🟡 | ~60% | Domain model/service, conformance suite, RFC 8785 JCS conversion hashing, **PostgreSQL Evidence/Knowledge adapter** + migration 0005, guarded live tests (7, skipped) | Not wired to MCP or SQLite runtime; no source resolver; Postgres adapter tested only against scripted connections. |
| 6 | C2 — Project-state reconstruction | 🟡 | ~50% | Read-only reconstruction at a pinned Git revision; stale/conflict/dangling-link handling | No trusted project→repo-root resolver; no production C1 reader; not exposed through MCP. |
| 7 | C3 — Automatic capture | 🟡 | ~45% | Rule-based extraction, review states, dedupe/conflict links, secret suppression, **ordered occurrences** (10-20) | In-memory queue only (lost on exit); no durable delivery/receipt; no MCP review tools. |
| 8 | C4 — Bidirectional sync | 🟡 | ~35% | Versioned events, cursors, idempotency (in-memory) | No durable event store, transport, or consumer. |
| 9 | Knowledge state in retrieval | 🟡 | ~60% | `retrieval/knowledge_state.py` envelope plus reader seam (merged) | MCP default has no reader; no retrieval-ID→knowledge-ID mapping; 3 test commits unmerged on `Retrieval-layer`. |
| 10 | Hosted / shared memory (PostgreSQL) | 🟡 | ~50% | Migrations 0001–0006 (baseline, user status, FTS indexes, memory capabilities, evidence memory, candidate-revision store), RLS, request UoW, `HostedRequestBoundary`, `celestin-migrate` CLI, Postgres retrieval adapter | **No live PostgreSQL run ever recorded**; no credential verifier; MCP cannot select Postgres. |
| 11 | Shared-memory contract | 🟠 | ~55% | v0.3 decision record **final**; delivery audit and owner decision packet written | **D-01…D-13 delivery/producer decisions open** (D-08 equality profile blocks receipts/replay). Owners not yet assigned. |
| 12 | Phase 8 — Remote / hosted transport | 🟠 | ~10% | Investigation doc only | Transport, identity provider, deployment. |
| 13 | Quality gate / release | ⛔ | ~10% | PR template; granular commit watcher | **No CI**; no lint/type-check; `SUBMISSION.md` is all TODO. |

**Rough overall:** the local product is about 85–90% complete. The full PRD vision (Resurrection + shared/hosted memory) is about **45–50%** complete. The percentages are judgment calls based on the remaining items listed above. The repo defines no shared phase denominator.

---

## 4. Verification run (this report)

`main` @ `86030ce8` was extracted with `git archive` into `/tmp`. Results:

| Environment | Result |
|---|---|
| Fresh venv, `pip install -e .[test]` (core deps only) | **634 passed, 45 skipped, 15 subtests passed**, 0 failed |
| Existing `.venv` (semantic/pageindex extras installed) | 762 passed, 27 skipped, **16 errors**, 1 failed |

The 16 errors are real test bugs. The default install skips them, so they stay hidden:
- `tests/retrieval/test_strategies.py` (5): `NameError: DecisionRepository is not defined`. Known since 10-15 and still unfixed.
- `tests/pageindex/test_mcp_and_cli.py` (11): these tests monkeypatch `server.persistence_services`. That attribute disappeared in the 10-16 runtime-composition refactor, so the tests need to target `MCPToolHandlers`/`build_runtime`.

The single failure, `test_mcp_protocol` stdio, is an environment artifact. This checkout's `.venv` has a stale editable install that points `orbit` at the `retrival` tree. The test passes in a clean venv.

Not run: live PostgreSQL (no approved target), real-model PageIndex/rerank, and consuming-agent prompt-injection tests.

---

## 5. Issues to fix (severity-ranked)

1. **High:** This checkout sits on the wrong branch. `retrival` tracks `.venv` (20k files) and has no source `.py` files apart from the old `memory_bridge.retrieval` adapter. Its `.venv` editable install also shadows `orbit` for any other checkout.
2. **High:** The decision workflow is incomplete over MCP. There is no approve/deprecate tool, so `retrieve_context` never returns MCP-saved decisions. This was flagged on 10-11 and is still open.
3. **High:** Resurrection C1–C4 are not durable or end-to-end, and hosted Postgres is unverified against a real server.
4. **High (blocker):** The delivery-contract owner decisions (D-01…D-13) are unanswered, and owners are not named. This blocks capture→storage persistence.
5. **Medium:** 16 stale tests surface only with optional extras installed. There is no CI to catch them.
6. **Medium:** 3 test commits on `Retrieval-layer` are unmerged.
7. **Low:** The stray file `h -u origin fix-ranking-null-source-tie` is still tracked on `main`. 8+ merged branches remain on the remotes. The folder name `Devday ` has a trailing space, which breaks scripts and tool `workdir`s.
8. **Low:** `SUBMISSION.md` is still all TODO.

---

## 6. Recommended next steps

**Housekeeping (quick):**
1. `git checkout main && git pull`. Stop using `retrival` for development; archive it or delete it (team decision).
2. Rebuild `.venv` from `main` (`pip install -e .[test,postgres]`) so the editable install points at the real `src/`.
3. Rebase or merge `Retrieval-layer` onto `main`, run the suite, and open a PR for the 3 test commits.
4. Remove the stray root file. Prune merged branches (with team agreement).

**Phase A: close local product gaps**
5. Add `approve_decision` / `deprecate_decision` MCP tools with an MCP-boundary test.
6. Fix the 16 stale tests (`DecisionRepository` import; pageindex tests → runtime handlers).
7. Add a GitHub Actions CI matrix: core install plus one job with `semantic`/`pageindex` extras, on Python 3.10–3.13.

**Phase B: unblock shared memory**
8. Get owner answers for D-01a/b, D-02, D-08, D-11 (Gate A) in `docs/delivery-contract-owner-decision-packet.md`.
9. Provision an approved disposable Neon/Postgres target and run the 7 guarded live tests plus migrations 0001–0006.

**Phase C: wire Resurrection end-to-end**
10. Durable C3 candidate/revision ingress → C1 conversion (migration 0006 is ready), then a C1 reader for retrieval `knowledge_state`.
11. Durable C4 event store and consumer; trusted repo resolver for C2.
12. Expose review/reconstruction through MCP, then make Postgres selectable in `build_runtime` behind `HostedRequestBoundary`.

**Phase D: hosted (Phase 8)**
13. Credential verifier/identity provider, remote transport, deployment, and a consuming-agent security regression.
