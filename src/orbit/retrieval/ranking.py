"""Deterministic policy ranking for already retrieved records."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable

from .fts import SearchResult
from .knowledge_state import KnowledgeStateEnvelope


@dataclass(frozen=True, slots=True)
class RetrievalCandidate:
    """A retrieval-layer record independent of database schema."""

    record_id: str
    project_id: str
    source_id: str | None
    content: str
    timestamp: str | None
    relevance_score: float
    record_kind: str
    status: str | None = None
    knowledge_state: KnowledgeStateEnvelope | None = field(default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        for field_name in ("record_id", "project_id", "content"):
            value = getattr(self, field_name)
            if not isinstance(value, str):
                raise TypeError(f"{field_name} must be a string")
        if not self.record_id or not self.project_id:
            raise ValueError("record_id and project_id must be non-empty")
        if self.source_id is not None and (
            not isinstance(self.source_id, str) or not self.source_id
        ):
            raise ValueError("source_id must be a non-empty string or None")
        if self.record_kind not in {"chat_message", "canonical_decision", "document_section"}:
            raise ValueError("record_kind must be chat_message, canonical_decision, or document_section")
        if self.record_kind != "canonical_decision" and self.status is not None:
            raise ValueError(f"{self.record_kind} status must be None")
        if self.record_kind == "canonical_decision" and self.status not in {
            "approved",
            "draft",
            "deprecated",
        }:
            raise ValueError("canonical_decision status must be approved, draft, or deprecated")
        if isinstance(self.relevance_score, bool) or not isinstance(self.relevance_score, (int, float)):
            raise TypeError("relevance_score must be a number")
        if not math.isfinite(self.relevance_score):
            raise ValueError("relevance_score must be finite")
        if self.timestamp is not None and not isinstance(self.timestamp, str):
            raise TypeError("timestamp must be a string or None")
        _timestamp_key(self.timestamp)
        if self.knowledge_state is not None:
            if not isinstance(self.knowledge_state, KnowledgeStateEnvelope):
                raise TypeError("knowledge_state must be a KnowledgeStateEnvelope or None")
            if self.knowledge_state.project_id != self.project_id:
                raise ValueError("knowledge_state project_id must match the candidate project_id")

    @classmethod
    def from_message_search_result(cls, result: SearchResult) -> RetrievalCandidate:
        """Map the FTS baseline DTO into the cross-source ranking DTO."""
        if not isinstance(result, SearchResult):
            raise TypeError("result must be a SearchResult")
        return cls(
            record_id=result.message_id,
            project_id=result.project_id,
            source_id=result.source_id,
            content=result.content,
            timestamp=result.timestamp,
            relevance_score=result.relevance_score,
            record_kind="chat_message",
        )


@dataclass(frozen=True, slots=True)
class RankingPolicy:
    """Explicit policy; recency breaks relevance ties without score weights."""

    include_drafts: bool = False
    include_deprecated: bool = False
    approved_decisions_first: bool = True
    recency_tiebreaker: bool = True


def _timestamp_key(timestamp: str | None) -> float | None:
    if timestamp is None:
        return None
    normalized = timestamp[:-1] + "+00:00" if timestamp.endswith("Z") else timestamp
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"timestamp must be an ISO 8601 datetime: {timestamp!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).timestamp()


def rank_candidates(
    candidates: Iterable[RetrievalCandidate],
    *,
    project_id: str,
    policy: RankingPolicy = RankingPolicy(),
) -> list[RetrievalCandidate]:
    ranked, _ = _rank_candidates_with_exclusions(
        candidates,
        project_id=project_id,
        policy=policy,
    )
    return ranked


def _rank_candidates_with_exclusions(
    candidates: Iterable[RetrievalCandidate],
    *,
    project_id: str,
    policy: RankingPolicy = RankingPolicy(),
) -> tuple[list[RetrievalCandidate], list[tuple[RetrievalCandidate, str]]]:
    """Rank eligible records and report candidates removed by project/status filters.

    Ordering is lexicographic: approved canonical decision priority (when
    enabled), FTS relevance, optional recency tie-break, then stable record and
    source IDs. Scores remain untouched and represent keyword relevance only.
    """
    if not isinstance(project_id, str):
        raise TypeError("project_id must be a string")
    if not project_id.strip():
        raise ValueError("project_id must be non-empty")
    if not isinstance(policy, RankingPolicy):
        raise TypeError("policy must be a RankingPolicy")

    eligible = []
    excluded: list[tuple[RetrievalCandidate, str]] = []
    for candidate in candidates:
        if not isinstance(candidate, RetrievalCandidate):
            raise TypeError("candidates must contain RetrievalCandidate values")
        if candidate.project_id != project_id:
            excluded.append((candidate, "project_mismatch"))
            continue
        if candidate.record_kind == "canonical_decision":
            if candidate.status == "deprecated" and not policy.include_deprecated:
                excluded.append((candidate, "deprecated_status"))
                continue
            if candidate.status == "draft" and not policy.include_drafts:
                excluded.append((candidate, "draft_status"))
                continue
        eligible.append(candidate)

    def ordering(candidate: RetrievalCandidate) -> tuple[object, ...]:
        if candidate.record_kind == "canonical_decision":
            if candidate.status == "approved":
                category = 0 if policy.approved_decisions_first else 1
            elif candidate.status == "draft":
                category = 2
            else:
                category = 3
        else:
            category = 1 if policy.approved_decisions_first else 0
        timestamp_key = _timestamp_key(candidate.timestamp)
        recency = -timestamp_key if policy.recency_tiebreaker and timestamp_key is not None else (
            math.inf if policy.recency_tiebreaker else 0
        )
        return (
            category,
            candidate.relevance_score,
            recency,
            candidate.record_id,
            candidate.source_id is not None,
            candidate.source_id if candidate.source_id is not None else "",
        )

    return sorted(eligible, key=ordering), excluded
