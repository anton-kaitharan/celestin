"""Production retrieval service combining SQLite FTS and canonical memory."""

from __future__ import annotations

import logging
import json
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from contextlib import closing
from dataclasses import asdict, replace
from typing import Any

from orbit.database.engine import connect
from orbit.retrieval.context_builder import ContextBundle, ContextItem, build_context_bundle
from orbit.retrieval.fts import SearchResult
from orbit.retrieval.hybrid import RRF_K, RetrievalInfo, reciprocal_rank_fusion
from orbit.retrieval.ranking import RankingPolicy, RetrievalCandidate
from pathlib import Path

from orbit.pageindex.backend import PageIndexBackend
from orbit.pageindex.errors import PageIndexUnavailable
from orbit.retrieval.relevance import top_k_with_exemptions
from orbit.retrieval.rerank import RerankUnavailable
from orbit.retrieval.knowledge_state import (
    KnowledgeStateEnvelope,
    KnowledgeStateReader,
    KnowledgeStateScopeError,
)
from orbit.retrieval.system1 import MAX_CHOICES as SYSTEM1_MAX_CHOICES
from orbit.retrieval.selector import SelectorContext, plan_query
from orbit.retrieval.strategies import (
    Candidate, FtsStrategy, PageIndexStrategy, RetrievalRequest, SectionStrategy, SemanticStrategy, StrategyResult,
)
from orbit.retrieval.data import RetrievalData
from orbit.retrieval.sqlite_data import DECISION_FTS_SCHEMA, MESSAGE_FTS_SCHEMA, SQLiteRetrievalData
from orbit.semantic.search import SemanticSearch, SemanticUnavailable

logger = logging.getLogger(__name__)
MESSAGE_KIND = "chat_message"
DECISION_KIND = "canonical_decision"
DOCUMENT_KIND = "document_section"
LAYA_MODEL_REPO = "convaiinnovations/laya-multilingual"
LAYA_MODEL_REVISION = "1720e3e3357cfe1e281542e223f8273b0890ca34"
LAYA_STATE_TOKENS = 2_048
LAYA_HEAD_TOKENS = 256
LAYA_MAX_LEN = LAYA_STATE_TOKENS + LAYA_HEAD_TOKENS + 1
LAYA_TIMEOUT_SECONDS = 30.0
LAYA_BATCH_SIZE = 8
LAYA_RRF_K = 60
LAYA_CLASSES = {
    "direct": "Directly answers the query with clear evidence.",
    "supporting": "Provides useful evidence needed for an answer, but is incomplete.",
    "related": "Provides related background but is not answer-bearing evidence.",
    "irrelevant": "Does not help answer the query.",
}
LAYA_CLASS_WEIGHTS = {"direct": 3, "supporting": 2, "related": 1, "irrelevant": 0}

_laya_agent = None
_laya_agent_lock = threading.Lock()
_laya_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="celestin-laya")
_laya_inflight_lock = threading.Lock()
_laya_inflight = None


class LayaInputTooLong(Exception):
    """Candidate state exceeded Laya's configured input budget."""


def _get_laya_agent():
    """Load the requested local checkpoint once, only when the opt-in path is used."""
    global _laya_agent
    with _laya_agent_lock:
        if _laya_agent is None:
            try:
                import laya
            except ImportError as exc:
                raise RuntimeError("optional laya dependency is unavailable") from exc
            _laya_agent = laya.load(LAYA_MODEL_REPO, revision=LAYA_MODEL_REVISION)
        return _laya_agent


class LayaCandidateReranker:
    """Batch local pointwise Laya relevance judgments for authorized candidates."""

    name = "laya-multilingual"

    def __init__(self, *, agent=None, timeout_seconds: float = LAYA_TIMEOUT_SECONDS) -> None:
        self._agent = agent
        self._timeout_seconds = timeout_seconds

    def rank_payloads(self, payloads: list[dict[str, Any]]) -> list[str]:
        if not payloads:
            return []
        global _laya_inflight
        with _laya_inflight_lock:
            if _laya_inflight is not None and not _laya_inflight.done():
                raise TimeoutError("Laya inference worker is still processing a prior request")
            future = _laya_executor.submit(self._predict_batch, payloads)
            _laya_inflight = future
        try:
            utilities = future.result(timeout=self._timeout_seconds)
        except FutureTimeout as exc:
            future.cancel()
            raise TimeoutError("Laya inference timed out") from exc
        finally:
            with _laya_inflight_lock:
                if _laya_inflight is future and future.done():
                    _laya_inflight = None
        baseline_position = {row["record_id"]: row["baseline_rank"] for row in payloads}
        return sorted(utilities, key=lambda record_id: (-utilities[record_id], baseline_position[record_id]))

    def _predict_batch(self, payloads: list[dict[str, Any]]) -> dict[str, float]:
        agent = self._agent or _get_laya_agent()
        requests = []
        for payload in payloads:
            state = payload["state"]
            encoded = agent.tok(state, add_special_tokens=False, truncation=False)
            token_ids = encoded["input_ids"]
            if token_ids and isinstance(token_ids[0], list):
                token_ids = token_ids[0]
            if len(token_ids) > LAYA_STATE_TOKENS:
                raise LayaInputTooLong("candidate state exceeds the configured token budget")
            requests.append({"state": state, "questions": {"relevance": {
                "type": "choice", "instructions": "How useful is this candidate for answering the query?",
                "criteria": LAYA_CLASSES,
            }}})
        results = agent.predict_batch(
            [request["state"] for request in requests], requests[0]["questions"],
            batch_size=min(LAYA_BATCH_SIZE, len(requests)), max_len=LAYA_MAX_LEN,
            head_max_len=LAYA_HEAD_TOKENS, sort_by_length=True,
        )
        if not isinstance(results, list) or len(results) != len(payloads):
            raise ValueError("Laya returned an invalid batch")
        utilities: dict[str, float] = {}
        for payload, result in zip(payloads, results):
            usage = result.get("usage", {})
            if usage.get("truncated") or usage.get("state_tokens_dropped", 0):
                raise LayaInputTooLong("Laya reported a truncated candidate state")
            probabilities = result["answers"]["relevance"]["probabilities"]
            if not isinstance(probabilities, dict) or set(probabilities) != set(LAYA_CLASSES):
                raise ValueError("Laya returned invalid relevance classes")
            checked: dict[str, float] = {}
            for label, raw in probabilities.items():
                if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                    raise ValueError("Laya returned a nonnumeric probability")
                value = float(raw)
                if not math.isfinite(value) or not 0 <= value <= 1:
                    raise ValueError("Laya returned an invalid probability")
                checked[label] = value
            if not math.isclose(sum(checked.values()), 1.0, rel_tol=0.0, abs_tol=1e-3):
                raise ValueError("Laya probabilities are not normalized")
            utilities[payload["record_id"]] = sum(
                checked[label] * weight for label, weight in LAYA_CLASS_WEIGHTS.items()
            )
        if len(utilities) != len(payloads):
            raise ValueError("candidate IDs are not unique")
        return utilities


def build_laya_reranker() -> LayaCandidateReranker:
    """Create a lazy local Laya adapter without loading model weights yet."""
    return LayaCandidateReranker()


class RetrievalService:
    """Project-scoped search and context assembly over the production schema.

    Pass the connection returned by :func:`orbit.database.engine.connect`.
    Schema migration 2 creates and synchronizes the two FTS5 indexes used here.
    """

    def __init__(
        self,
        connection=None,
        *,
        retrieval_data: RetrievalData | None = None,
        knowledge_state_reader: KnowledgeStateReader | None = None,
        candidate_limit: int = 100,
        semantic: SemanticSearch | None = None,
        documents: PageIndexBackend | None = None,
        rrf_k: int = RRF_K,
        fts_relax: bool = True,
        sections: str | Path | None = None,
        selector: str = "all",
        section_top_k: int | None = None,
        section_excerpt_bytes: int | None = None,
        section_reranker=None,
        system1_reranker=None,
        rerank_shortlist: int = 20,
    ) -> None:
        if retrieval_data is not None and connection is not None:
            raise ValueError("provide either connection or retrieval_data, not both")
        if retrieval_data is None:
            if connection is None:
                raise TypeError("connection or retrieval_data is required")
            retrieval_data = SQLiteRetrievalData(connection)
        if isinstance(candidate_limit, bool) or not isinstance(candidate_limit, int):
            raise TypeError("candidate_limit must be an integer")
        if candidate_limit < 1:
            raise ValueError("candidate_limit must be at least 1")

        if knowledge_state_reader is not None and not callable(
            getattr(knowledge_state_reader, "get_for_candidate", None)
        ):
            raise TypeError("knowledge_state_reader must provide get_for_candidate")
        self._data = retrieval_data
        self._knowledge_state_reader = knowledge_state_reader
        self._candidate_limit = candidate_limit
        self._semantic = semantic
        self._rrf_k = rrf_k
        self._fts_relax = fts_relax
        self._documents = documents
        if selector not in {"all", "adaptive"}:
            raise ValueError("selector must be 'all' or 'adaptive'")
        self._selector = selector
        if section_top_k is not None and section_top_k < 1:
            raise ValueError("section_top_k must be at least 1")
        if section_excerpt_bytes is not None and section_excerpt_bytes < 200:
            raise ValueError("section_excerpt_bytes must be at least 200")
        self._section_top_k = section_top_k
        if rerank_shortlist < 2:
            raise ValueError("rerank_shortlist must be at least 2")
        self._reranker = section_reranker
        self._system1_reranker = system1_reranker
        self._rerank_shortlist = rerank_shortlist
        self._rerank_note: str | None = None
        self._system1_warnings: list[str] = []
        self._section_order: list[str] = []
        self._rerank_trace: dict = {}
        self._system1_trace: dict = {}
        self._sections = SectionStrategy(Path(sections), semantic, section_excerpt_bytes) if sections is not None else None
        self._strategies: dict = {
            "fts": FtsStrategy(self._data),
        }
        if semantic is not None:
            self._strategies["semantic"] = SemanticStrategy(semantic, self._data)
        if self._sections is not None:
            self._strategies["sections"] = self._sections
        if documents is not None:
            self._strategies["pageindex"] = PageIndexStrategy(documents)
        self._executed: dict[str, StrategyResult] = {}
        self.last_diagnostics: dict = {}
        self.last_retrieval = RetrievalInfo()
        # record_id -> source reference for document_section items of the last retrieve_context.
        self.last_sources: dict[str, dict[str, Any]] = {}
        self._navigation: dict | None = None

    @property
    def semantic_enabled(self) -> bool:
        return self._semantic is not None

    @property
    def documents_enabled(self) -> bool:
        return self._documents is not None or self._sections is not None or self._selector == "adaptive"

    def _semantic_hits(self, project: str, query: str, kinds: tuple[str, ...], limit: int):
        """Vector hits per kind, or None when degraded to FTS5 (reason recorded)."""
        if self._semantic is None:
            self.last_retrieval = RetrievalInfo("fts")
            return None
        if not query.strip():
            self.last_retrieval = RetrievalInfo("hybrid")
            return {kind: [] for kind in kinds}
        try:
            hits = self._semantic.search(project, query, kinds=kinds, limit=limit)
        except SemanticUnavailable as exc:
            logger.warning("semantic search unavailable, using FTS5 only: %s", exc)
            self.last_retrieval = RetrievalInfo(
                "fts_fallback", (f"semantic search unavailable: {exc}",)
            )
            return None
        self.last_retrieval = RetrievalInfo("hybrid")
        return hits

    def _note_stale(self, count: int) -> None:
        if count:
            self.last_retrieval = RetrievalInfo(
                "hybrid",
                (f"ignored {count} vector hit(s) with no matching SQLite record; "
                 "run: celestin-index sync",),
            )

    def _fuse_messages(
        self, project: str, fts: list[SearchResult], vector_hits
    ) -> tuple[list[SearchResult], int]:
        """Merge FTS5 and vector message hits with RRF into SearchResults.

        ``relevance_score`` of fused results is ``-rrf_score`` so that, like
        BM25, a lower value is better.
        """
        by_id = {str(r.message_id): r for r in fts}
        rows = self._data.load_messages(
            project, [str(h.record_id) for h in vector_hits if str(h.record_id) not in by_id]
        )
        semantic_ids = [
            str(h.record_id) for h in vector_hits
            if str(h.record_id) in by_id or str(h.record_id) in rows
        ]
        stale = len(vector_hits) - len(semantic_ids)
        fused = reciprocal_rank_fusion([list(by_id), semantic_ids], self._rrf_k)
        results = []
        for record_id, score in fused:
            if str(record_id) in by_id:
                base = by_id[str(record_id)]
                results.append(SearchResult(
                    base.message_id, base.project_id, base.source_id,
                    base.content, base.timestamp, -score,
                ))
            else:
                row = rows[str(record_id)]
                results.append(SearchResult(
                    row.message_id, row.project, row.conversation_id,
                    row.content, row.created_at, -score,
                ))
        return results, stale

    def search_chat_history(
        self, project: str, query: str, limit: int = 10
    ) -> list[SearchResult]:
        """Return project-scoped message search matches ordered by BM25.

        Each result includes the chat message ID, project, conversation ID in
        ``source_id``, content, timestamp, and relevance score.
        """
        fts_page = self._data.search_messages(
            project,
            query,
            max(limit, self._candidate_limit) if self._semantic is not None else limit,
            relax=self._fts_relax,
        )
        fts = [
            SearchResult(
                match.record_id, match.project, match.source_id or "", match.content,
                match.timestamp, match.score,
            )
            for match in fts_page.matches
        ]
        hits = self._semantic_hits(project, query, (MESSAGE_KIND,), self._candidate_limit)
        if hits is None:
            return fts[:limit]
        fused, stale = self._fuse_messages(project, fts, hits[MESSAGE_KIND])
        self._note_stale(stale)
        return fused[:limit]

    # ---- strategy-based context retrieval -------------------------------------------------

    def _knowledge_state_for_candidate(
        self, project: str, record_project: str, record_kind: str, record_id: str
    ) -> KnowledgeStateEnvelope | None:
        reader = self._knowledge_state_reader
        if reader is None or record_project != project:
            return None
        bundle = reader.get_for_candidate(project, record_kind, record_id)
        if bundle is None:
            return None
        state = KnowledgeStateEnvelope.from_bundle(bundle)
        if state.project_id != project:
            raise KnowledgeStateScopeError(
                "Capability 1 knowledge project does not match the requested project"
            )
        return state

    def _available(self) -> frozenset[str]:
        names = {"fts"}
        if self._semantic is not None:
            names.add("semantic")
        if self._sections is not None:
            names.add("sections")
        if self._documents is not None:
            names.add("pageindex")
        return frozenset(names)

    def _run(self, name: str, request: RetrievalRequest, **kwargs) -> StrategyResult:
        strategy = self._strategies[name]
        start = time.perf_counter()
        result = strategy.retrieve(request, **kwargs)
        result.elapsed_ms = (time.perf_counter() - start) * 1000
        self._executed[name] = result
        return result

    def _execute(self, request: RetrievalRequest) -> dict | None:
        """Run the strategies for this request; returns selector info in adaptive mode."""
        self._executed = {}
        available = self._available()
        if self._selector != "adaptive":
            fts = self._run("fts", request)
            approved = self._approved_strict_hit(fts)
            request = replace(request, approved_strict_hit=approved)
            for name in ("semantic", "sections", "pageindex"):
                if name in available:
                    self._run(name, request)
            return None
        has_documents = "sections" in available and self._strategies["sections"].has_documents(request.project)
        nav_tokens = None
        if "pageindex" in available:
            try:
                prepared = self._documents.prepare(request.project)
                has_documents = has_documents or prepared is not None
                nav_tokens = prepared.est_prompt_tokens if prepared else None
            except PageIndexUnavailable:
                available = available - {"pageindex"}
        plan = plan_query(request.query, SelectorContext(available, has_documents, request.token_budget, nav_tokens))
        info = {"mode": "adaptive", **plan.as_dict(), "fallback_used": False}
        if plan.primary == "fts":
            first = self._run("fts", request)
        else:
            # FTS5 always runs: it is local and cheap, keeps exact matches in chat memory reachable, and
            # is how approved decisions stay reachable whatever the primary strategy is.
            decisions = self._run("fts", request)
            request = replace(request, approved_strict_hit=self._approved_strict_hit(decisions))
            first = self._run(plan.primary, request)
        if plan.fallback and not (first.status == "ok" and first.strong_hits > 0):
            info["fallback_used"] = True
            self._run(plan.fallback, request)
        info["executed"] = list(self._executed)
        return info

    @staticmethod
    def _approved_strict_hit(fts: StrategyResult) -> bool:
        return fts.decision_tier == "strict" and any(
            c.record_type == "canonical_decision" and c.status == "approved" for c in fts.candidates
        )

    def retrieve_context(
        self, project: str, query: str, token_budget: int = 2_000
    ) -> ContextBundle:
        """Assemble ranked message/decision/section candidates into a bounded bundle.

        Strategies (FTS5, semantic, heading sections, PageIndex) find candidates. When more than
        one retrieval method contributed, scores are Reciprocal Rank Fusion values (-score, lower
        is better); with FTS5 alone the BM25 scores are kept. Eligibility policy, deduplication
        and the byte budget are applied by the shared ranking and context builder.
        """
        request = RetrievalRequest(project, query, self._candidate_limit, token_budget, self._fts_relax)
        t_start = time.perf_counter()
        selector_info = self._execute(request)
        t_strategies = time.perf_counter()
        results = self._executed

        candidates_by_type: dict[str, dict[str, Candidate]] = {}
        lists: dict[tuple[str, str], list[Candidate]] = {}
        for name in ("fts", "semantic", "sections", "pageindex"):
            for c in results[name].candidates if name in results else []:
                lists.setdefault((c.record_type, c.method), []).append(c)
                candidates_by_type.setdefault(c.record_type, {}).setdefault(c.record_id, c)
        semantic_ok = "semantic" in results and results["semantic"].status == "ok"
        fused = semantic_ok or any(method != "fts" for _, method in lists)

        self.last_sources = {}
        self._rerank_note, self._rerank_trace, self._section_order = None, {}, []
        self._system1_warnings = []
        self._system1_trace = {}
        candidates: list[RetrievalCandidate] = []
        capped = 0
        for record_type, by_id in candidates_by_type.items():
            if fused:
                rankings = [[c.record_id for c in sorted(group, key=lambda c: c.rank)]
                            for (rt, _), group in lists.items() if rt == record_type]
                scores = {rid: -score for rid, score in reciprocal_rank_fusion(rankings, self._rrf_k)}
            else:
                scores = {rid: c.score for rid, c in by_id.items()}
            if record_type == "document_section":
                self._section_order = [rid for rid, _ in sorted(scores.items(), key=lambda kv: (kv[1], kv[0]))]
            if self._system1_reranker is not None:
                scores = self._rerank_system1(record_type, request, by_id, scores, lists)
            if record_type == "document_section" and self._reranker is not None:
                scores = self._rerank_sections(request.query, by_id, scores, lists)
            if record_type == "document_section" and self._section_top_k is not None:
                # Rank cap for heading sections only. Sections with a strong keyword hit (all terms or all
                # non-stopword terms matched) are exempt, so exact lookups are never dropped by the cap.
                exempt = {rid for rid, c in by_id.items()
                          if any(m.method == "sections_fts" and m.tier != "relaxed_or" for m in _candidate_hits(lists, rid))}
                ordered = [rid for rid, _ in sorted(scores.items(), key=lambda kv: (kv[1], kv[0]))]
                kept = top_k_with_exemptions(ordered, self._section_top_k, exempt)
                capped += len(by_id) - len(kept)
                by_id = {rid: c for rid, c in by_id.items() if rid in kept}
            for rid, c in by_id.items():
                candidates.append(RetrievalCandidate(
                    record_id=c.record_id, project_id=c.project, source_id=c.source_id, content=c.content,
                    timestamp=c.timestamp, relevance_score=scores[rid], record_kind=c.record_type, status=c.status,
                    knowledge_state=self._knowledge_state_for_candidate(
                        project, c.project, c.record_type, c.record_id
                    ),
                ))
                if c.source:
                    self.last_sources[c.record_id] = c.source
        self.last_retrieval = self._describe(results, selector_info)
        t_fused = time.perf_counter()
        bundle = build_context_bundle(
            candidates,
            project_id=project,
            token_budget=token_budget,
            ranking_policy=RankingPolicy(
                approved_decisions_first=True,
                include_drafts=False,
                include_deprecated=False,
            ),
        )
        self.last_diagnostics = self._diagnostics(
            results, lists, candidates_by_type, bundle,
            (t_strategies - t_start, t_fused - t_strategies, time.perf_counter() - t_fused),
        )
        self.last_diagnostics["capped_sections"] = capped
        if self._system1_reranker is not None:
            total_rerank_ms = round(sum(group["ms"] for group in self._system1_trace.values()), 2)
            self._rerank_trace.update({
                "reranker": self._system1_reranker.name,
                "ms": total_rerank_ms,
                "failures": sum(group["failures"] for group in self._system1_trace.values()),
                "input_skips": sum(group["input_skips"] for group in self._system1_trace.values()),
                "groups": self._system1_trace,
            })
        self.last_diagnostics["rerank"] = self._rerank_trace
        if self._system1_reranker is not None:
            self.last_diagnostics["system1"] = self._system1_trace
        self.last_diagnostics["section_order"] = list(self._section_order)
        self.last_diagnostics["semantic_filter"] = dict(getattr(self._semantic, "last_filter", {}) or {})
        return bundle

    @staticmethod
    def _diagnostics(results, lists, candidates_by_type, bundle, stage_seconds) -> dict:
        """Bounded, content-free trace of one retrieval: counts, ranks, native scores, reasons, timings."""
        methods_by_id: dict[str, dict[str, dict]] = {}
        for (_, method), group in lists.items():
            for c in group:
                methods_by_id.setdefault(c.record_id, {})[method] = {
                    "rank": c.rank, "score": None if c.score is None else round(float(c.score), 4)}
        return {
            "strategies": {
                name: {
                    "status": r.status, "ms": round(getattr(r, "elapsed_ms", 0.0), 2), "fts_tier": r.fts_tier,
                    "candidates": {m: sum(c.method == m for c in r.candidates) for m in sorted({c.method for c in r.candidates})},
                }
                for name, r in results.items()
            },
            "fused": {t: len(by_id) for t, by_id in candidates_by_type.items()},
            "selected": [
                {"record_id": i.record_id, "type": i.record_kind, "bytes": len(i.content.encode("utf-8")),
                 "methods": methods_by_id.get(i.record_id, {})}
                for i in bundle.items
            ],
            "excluded": {row.reason: row.count for row in bundle.excluded_counts},
            "context_bytes": len(bundle.rendered_context.encode("utf-8")),
            "stage_ms": {k: round(v * 1000, 2) for k, v in zip(("strategies", "fusion", "build"), stage_seconds)},
        }

    def _rerank_sections(self, query: str, by_id: dict, scores: dict, lists) -> dict:
        """Reorder the top of the fused section list; scores stay rank-based (-1/(k+position)).

        Only a bounded shortlist is reranked, nothing is dropped, and sections with a strong keyword hit
        (all terms or all non-stopword terms matched) stay ahead so exact lookups are never demoted.
        If the reranker is unavailable the original order and scores are kept and a warning is recorded.
        """
        ordered = [rid for rid, _ in sorted(scores.items(), key=lambda kv: (kv[1], kv[0]))]
        shortlist, rest = ordered[: self._rerank_shortlist], ordered[self._rerank_shortlist:]
        start = time.perf_counter()
        try:
            reranked = self._reranker.rerank(query, [by_id[rid] for rid in shortlist])
        except RerankUnavailable as exc:
            logger.warning("section reranker unavailable, keeping the original order: %s", exc)
            self._rerank_note = f"section reranker unavailable: {exc}"
            return scores
        if sorted(reranked) != sorted(shortlist):  # a reranker may only reorder
            self._rerank_note = "section reranker returned an inconsistent order; ignored"
            return scores
        strong = {rid for rid in shortlist
                  if any(c.method == "sections_fts" and c.tier != "relaxed_or" for c in _candidate_hits(lists, rid))}
        final = [r for r in reranked if r in strong] + [r for r in reranked if r not in strong] + rest
        self._rerank_trace = {"reranker": self._reranker.name, "shortlist": len(shortlist),
                              "before": ordered, "after": final,
                              "ms": round((time.perf_counter() - start) * 1000, 2)}
        return {rid: -1.0 / (self._rrf_k + pos) for pos, rid in enumerate(final, start=1)}

    def _rerank_system1(self, record_type: str, request: RetrievalRequest, by_id: dict,
                        scores: dict, lists) -> dict:
        """Rerank authorized candidates while preserving baseline scores and strong FTS order."""
        ordered = [rid for rid, _ in sorted(scores.items(), key=lambda kv: (kv[1], kv[0]))]
        eligible = [rid for rid in ordered if by_id[rid].project == request.project and (
            record_type != "canonical_decision" or by_id[rid].status == "approved"
        )]
        shortlist_ids = eligible[: min(self._rerank_shortlist, SYSTEM1_MAX_CHOICES)]
        shortlist = [by_id[rid] for rid in shortlist_ids]
        group = {"before": ordered, "after": ordered, "eligible": len(eligible),
                 "shortlist": len(shortlist), "calls": int(len(shortlist) >= 2), "failures": 0,
                 "input_skips": 0, "ms": 0.0}
        started = time.perf_counter()
        if len(shortlist) >= 2:
            try:
                if isinstance(self._system1_reranker, LayaCandidateReranker):
                    evidence: dict[str, list[dict[str, Any]]] = {rid: [] for rid in shortlist_ids}
                    for (_, method), hits in lists.items():
                        for hit in hits:
                            if hit.record_id in evidence:
                                evidence[hit.record_id].append({
                                    "method": method, "rank": hit.rank, "score": hit.score,
                                })
                    payloads = []
                    for baseline_rank, candidate in enumerate(shortlist, start=1):
                        candidate_evidence = evidence[candidate.record_id] or [{
                            "method": candidate.method, "rank": candidate.rank, "score": candidate.score,
                        }]
                        payload_state = {
                            "query": request.query,
                            "baseline_rank": baseline_rank,
                            "candidate_type": candidate.record_type,
                            "candidate_text": candidate.content,
                            "retrieval_evidence": candidate_evidence,
                        }
                        payloads.append({
                            "record_id": candidate.record_id,
                            "baseline_rank": baseline_rank,
                            "state": json.dumps(payload_state, ensure_ascii=False, separators=(",", ":")),
                        })
                    model_order = self._system1_reranker.rank_payloads(payloads)
                else:
                    model_order = self._system1_reranker.rerank(request.query, shortlist)
                if len(model_order) != len(shortlist_ids) or set(model_order) != set(shortlist_ids):
                    raise ValueError("reranker returned inconsistent candidate IDs")
                strong = {rid for rid in shortlist_ids if any(
                    c.method in {"fts", "sections_fts"} and c.tier != "relaxed_or"
                    for c in _candidate_hits(lists, rid)
                )}
                baseline_rank = {rid: rank for rank, rid in enumerate(shortlist_ids, start=1)}
                if isinstance(self._system1_reranker, LayaCandidateReranker):
                    model_rank = {rid: rank for rank, rid in enumerate(model_order, start=1)}
                    rrf_scores = {
                        rid: 1.0 / (LAYA_RRF_K + baseline_rank[rid])
                        + 1.0 / (LAYA_RRF_K + model_rank[rid])
                        for rid in shortlist_ids
                    }
                    fused_order = sorted(
                        shortlist_ids, key=lambda rid: (-rrf_scores[rid], baseline_rank[rid]),
                    )
                else:
                    fused_order = model_order
                strong_in_order = [rid for rid in shortlist_ids if rid in strong]
                fused_non_strong = [rid for rid in fused_order if rid not in strong]
                tail = [rid for rid in ordered if rid not in set(shortlist_ids)]
                group["after"] = strong_in_order + fused_non_strong + tail
            except LayaInputTooLong:
                group["input_skips"] = 1
                self._system1_warnings.append(
                    f"Laya reranking skipped for {record_type} (input_over_budget); baseline order retained"
                )
                logger.warning("Laya reranking skipped for %s (input_over_budget); baseline order retained", record_type)
            except Exception as exc:
                group["failures"] = 1
                self._system1_warnings.append(
                    f"System 1 reranking failed for {record_type} ({type(exc).__name__}); baseline order retained"
                )
                logger.warning("System 1 reranking failed for %s (%s); baseline order retained",
                               record_type, type(exc).__name__)
        group["ms"] = round((time.perf_counter() - started) * 1000, 2)
        self._system1_trace[record_type] = group
        if record_type == "document_section":
            self._rerank_trace = {
                "reranker": self._system1_reranker.name, "shortlist": group["shortlist"],
                "before": group["before"], "after": group["after"], "ms": group["ms"],
                "failures": group["failures"],
            }
        if group["after"] == ordered:
            return scores
        updated = dict(scores)
        shortlist_scores = sorted(scores[rid] for rid in shortlist_ids)
        final_shortlist = group["after"][:len(shortlist_ids)]
        for position, (record_id, score) in enumerate(zip(final_shortlist, shortlist_scores)):
            updated[record_id] = score + position * 1e-12
        return updated

    def _describe(self, results: dict[str, StrategyResult], selector_info: dict | None) -> RetrievalInfo:
        warnings: list[str] = []
        mode, pageindex, sections, navigation = "fts", None, None, None
        sem = results.get("semantic")
        if sem is not None:
            if sem.status == "ok":
                mode = "hybrid"
                if sem.stale:
                    warnings.append(f"ignored {sem.stale} vector hit(s) with no matching SQLite record; "
                                    "run: celestin-index sync")
            else:
                mode = "fts_fallback"
                warnings.append(f"semantic search unavailable: {sem.detail}")
        elif self._semantic is not None and self._selector != "adaptive":
            mode = "hybrid"
        sec = results.get("sections")
        if self._sections is not None:
            if sec is None:
                sections = "not_selected"
            elif sec.status == "ok":
                sections = "used"
                if sec.detail:
                    warnings.append(sec.detail)
                if sec.stale:
                    warnings.append(f"ignored {sec.stale} section vector hit(s) with no matching section; "
                                    "run: celestin-index sync")
            else:
                sections = f"{sec.status}: {sec.detail}"
        pi = results.get("pageindex")
        if self._documents is not None:
            if pi is None:
                pageindex = "not_selected"
            elif pi.status == "ok":
                pageindex = "used"
            elif pi.status == "skipped" and pi.detail == "no_documents":
                pageindex = "no_documents"
            else:
                pageindex = f"{pi.status}: {pi.detail}"
                if pi.status == "unavailable":
                    warnings.append(f"document retrieval unavailable: {pi.detail}")
            navigation = pi.navigation if pi is not None else None
        if self._rerank_note:
            warnings.append(self._rerank_note)
        warnings.extend(self._system1_warnings)
        return RetrievalInfo(mode, tuple(warnings), pageindex, navigation, sections, selector_info)


def _candidate_hits(lists, record_id):
    return [c for group in lists.values() for c in group if c.record_id == record_id]


def _search_result_dict(result: SearchResult) -> dict[str, Any]:
    return {
        "message_id": result.message_id,
        "project": result.project_id,
        "source_message_id": result.message_id,
        "conversation_id": result.source_id,
        "role": None,
        "content": result.content,
        "created_at": result.timestamp,
        "score": result.relevance_score,
    }


def _context_bundle_dict(bundle: ContextBundle, query: str) -> dict[str, Any]:
    return {
        "project": bundle.project_id,
        "query": query,
        "items": [
            _context_item_dict(item) for item in bundle.items
        ],
        "excluded": [
            {
                "record_id": item.record_id,
                "project_id": item.project_id,
                "source_message_id": item.source_id,
                "created_at": item.timestamp,
                "record_kind": item.record_kind,
                "status": item.status,
                "reason": item.reason,
            }
            for item in bundle.excluded
        ],
        "excluded_counts": [asdict(item) for item in bundle.excluded_counts],
        "rendered_context": bundle.rendered_context,
        "token_count": bundle.token_count,
        "token_budget": bundle.token_budget,
        "token_count_is_estimate": bundle.token_count_is_estimate,
        "token_counter": bundle.token_counter,
    }


def _context_item_dict(item: ContextItem) -> dict[str, object]:
    result: dict[str, object] = {
        "record_id": item.record_id,
        "project_id": item.project_id,
        "source_message_id": item.source_id,
        "created_at": item.timestamp,
        "record_kind": item.record_kind,
        "status": item.status,
        "content": item.content,
        "relevance_score": item.relevance_score,
    }
    if item.knowledge_state is not None:
        result["knowledge_state"] = item.knowledge_state.as_dict()
    return result


def search_chat_history(
    project: str,
    query: str,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Search messages using the configured production SQLite database."""
    with closing(connect()) as connection:
        results = RetrievalService(connection).search_chat_history(
            project, query, limit
        )
    return [_search_result_dict(result) for result in results]


def retrieve_context(
    project: str,
    query: str,
    token_budget: int = 2_000,
) -> dict[str, Any]:
    """Return a serializable, traceable context bundle for one project."""
    with closing(connect()) as connection:
        bundle = RetrievalService(connection).retrieve_context(
            project, query, token_budget
        )
    return _context_bundle_dict(bundle, query)
