"""Build traceable context bundles under a documented token estimate/budget."""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from typing import Protocol, Sequence

from .knowledge_state import KnowledgeStateEnvelope
from .ranking import RankingPolicy, RetrievalCandidate, _rank_candidates_with_exclusions

DEFAULT_TOKEN_BUDGET = 2_000
_WHITESPACE = re.compile(r"\s+")


class TokenCounter(Protocol):
    """Replaceable counter for the serialized context sent to a model."""

    name: str
    is_estimate: bool

    def count_tokens(self, text: str) -> int:
        """Return the token count for text."""


@dataclass(frozen=True, slots=True)
class Utf8ByteTokenEstimator:
    """Conservative estimate for byte-tokenizer families; not an exact count."""

    name: str = "utf8-byte-upper-bound"
    is_estimate: bool = True

    def count_tokens(self, text: str) -> int:
        return len(text.encode("utf-8"))


@dataclass(frozen=True, slots=True)
class ContextItem:
    record_id: str
    project_id: str
    source_id: str | None
    timestamp: str | None
    record_kind: str
    status: str | None
    relevance_score: float
    content: str
    knowledge_state: KnowledgeStateEnvelope | None = None

    @classmethod
    def from_candidate(cls, candidate: RetrievalCandidate) -> ContextItem:
        return cls(
            record_id=candidate.record_id,
            project_id=candidate.project_id,
            source_id=candidate.source_id,
            timestamp=candidate.timestamp,
            record_kind=candidate.record_kind,
            status=candidate.status,
            relevance_score=candidate.relevance_score,
            content=candidate.content,
            knowledge_state=candidate.knowledge_state,
        )

    def as_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "record_id": self.record_id,
            "project_id": self.project_id,
            "source_id": self.source_id,
            "timestamp": self.timestamp,
            "record_kind": self.record_kind,
            "status": self.status,
            "relevance_score": self.relevance_score,
            "content": self.content,
        }
        if self.knowledge_state is not None:
            result["knowledge_state"] = self.knowledge_state.as_dict()
        return result


@dataclass(frozen=True, slots=True)
class ExcludedRecord:
    record_id: str
    project_id: str
    source_id: str | None
    timestamp: str | None
    record_kind: str
    status: str | None
    reason: str

    @classmethod
    def from_candidate(cls, candidate: RetrievalCandidate, reason: str) -> ExcludedRecord:
        return cls(
            record_id=candidate.record_id,
            project_id=candidate.project_id,
            source_id=candidate.source_id,
            timestamp=candidate.timestamp,
            record_kind=candidate.record_kind,
            status=candidate.status,
            reason=reason,
        )


@dataclass(frozen=True, slots=True)
class ExclusionCount:
    reason: str
    count: int


@dataclass(frozen=True, slots=True)
class ContextBundle:
    project_id: str
    items: tuple[ContextItem, ...]
    excluded: tuple[ExcludedRecord, ...]
    excluded_counts: tuple[ExclusionCount, ...]
    rendered_context: str
    token_count: int
    token_budget: int
    token_count_is_estimate: bool
    token_counter: str


def _render(items: Sequence[ContextItem]) -> str:
    return "\n".join(
        json.dumps(item.as_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for item in items
    )


def _duplicate_key(content: str) -> str:
    return _WHITESPACE.sub(" ", content).strip().casefold()


def _excluded(candidate: RetrievalCandidate, reason: str) -> ExcludedRecord:
    return ExcludedRecord.from_candidate(candidate, reason)


def build_context_bundle(
    candidates: Sequence[RetrievalCandidate],
    *,
    project_id: str,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
    ranking_policy: RankingPolicy = RankingPolicy(),
    token_counter: TokenCounter | None = None,
) -> ContextBundle:
    """Select full records in ranking order while keeping rendered context in budget.

    If a candidate does not fit, it is excluded whole and selection continues
    with later candidates. Exact normalized-content duplicates are excluded
    after the first copy that fits. The emitted JSON Lines include all source
    metadata, and the budget is checked against that full rendered text.
    """
    if isinstance(token_budget, bool) or not isinstance(token_budget, int):
        raise TypeError("token_budget must be an integer")
    if token_budget < 1:
        raise ValueError("token_budget must be at least 1")
    if not isinstance(candidates, Sequence):
        raise TypeError("candidates must be a sequence of RetrievalCandidate values")

    counter = token_counter if token_counter is not None else Utf8ByteTokenEstimator()
    if not callable(getattr(counter, "count_tokens", None)):
        raise TypeError("token_counter must provide count_tokens(text)")
    if not isinstance(getattr(counter, "name", None), str):
        raise TypeError("token_counter must provide a string name")
    if not isinstance(getattr(counter, "is_estimate", None), bool):
        raise TypeError("token_counter must provide a boolean is_estimate")

    ranked, policy_excluded = _rank_candidates_with_exclusions(
        candidates,
        project_id=project_id,
        policy=ranking_policy,
    )
    selected: list[ContextItem] = []
    excluded: list[ExcludedRecord] = []
    excluded_reason_counts: Counter[str] = Counter()
    for candidate, reason in policy_excluded:
        excluded_reason_counts[reason] += 1
        if reason != "project_mismatch":
            excluded.append(_excluded(candidate, reason))
    seen_content: set[str] = set()

    for candidate in ranked:
        duplicate_key = _duplicate_key(candidate.content)
        if duplicate_key and duplicate_key in seen_content:
            excluded.append(_excluded(candidate, "duplicate_content"))
            excluded_reason_counts["duplicate_content"] += 1
            continue

        proposed_items = [*selected, ContextItem.from_candidate(candidate)]
        proposed_context = _render(proposed_items)
        token_count = counter.count_tokens(proposed_context)
        if isinstance(token_count, bool) or not isinstance(token_count, int) or token_count < 0:
            raise ValueError("token_counter must return a non-negative integer")
        if token_count > token_budget:
            excluded.append(_excluded(candidate, "token_budget"))
            excluded_reason_counts["token_budget"] += 1
            continue

        selected = proposed_items
        if duplicate_key:
            seen_content.add(duplicate_key)

    rendered_context = _render(selected)
    final_token_count = counter.count_tokens(rendered_context) if rendered_context else 0
    if isinstance(final_token_count, bool) or not isinstance(final_token_count, int) or final_token_count < 0:
        raise ValueError("token_counter must return a non-negative integer")
    if final_token_count > token_budget:
        raise ValueError("selected context exceeds token_budget under the configured token counter")

    return ContextBundle(
        project_id=project_id,
        items=tuple(selected),
        excluded=tuple(excluded),
        excluded_counts=tuple(
            ExclusionCount(reason, count)
            for reason, count in sorted(excluded_reason_counts.items())
        ),
        rendered_context=rendered_context,
        token_count=final_token_count,
        token_budget=token_budget,
        token_count_is_estimate=counter.is_estimate,
        token_counter=counter.name,
    )
