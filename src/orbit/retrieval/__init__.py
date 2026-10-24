"""Retrieval, ranking, and context assembly."""

from .context_builder import (
    DEFAULT_TOKEN_BUDGET,
    ContextBundle,
    ContextItem,
    ExclusionCount,
    ExcludedRecord,
    Utf8ByteTokenEstimator,
    build_context_bundle,
)
from .fts import (
    FtsMessageSearch,
    FtsSchema,
    SearchIndexUnavailable,
    SearchResult,
)
from .hybrid import RRF_K, RetrievalInfo, reciprocal_rank_fusion
from .data import DecisionRecord, MessageRecord, RetrievalData, SearchMatch, SearchPage
from .sqlite_data import SQLiteRetrievalData
from .ranking import (
    RankingPolicy,
    RetrievalCandidate,
    rank_candidates,
)
from .knowledge_state import (
    KnowledgeStateEnvelope,
    KnowledgeStateReader,
    KnowledgeStateScopeError,
)
from .service import (
    DECISION_FTS_SCHEMA,
    MESSAGE_FTS_SCHEMA,
    RetrievalService,
    retrieve_context,
    search_chat_history,
)

__all__ = [
    "RRF_K",
    "RetrievalInfo",
    "reciprocal_rank_fusion",
    "DecisionRecord",
    "MessageRecord",
    "RetrievalData",
    "SearchMatch",
    "SearchPage",
    "SQLiteRetrievalData",
    "FtsMessageSearch",
    "FtsSchema",
    "SearchIndexUnavailable",
    "SearchResult",
    "RankingPolicy",
    "RetrievalCandidate",
    "rank_candidates",
    "KnowledgeStateEnvelope",
    "KnowledgeStateReader",
    "KnowledgeStateScopeError",
    "DEFAULT_TOKEN_BUDGET",
    "ContextBundle",
    "ContextItem",
    "ExclusionCount",
    "ExcludedRecord",
    "Utf8ByteTokenEstimator",
    "build_context_bundle",
    "MESSAGE_FTS_SCHEMA",
    "DECISION_FTS_SCHEMA",
    "RetrievalService",
    "search_chat_history",
    "retrieve_context",
]
