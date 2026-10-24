"""Explicit input and review records for the automatic-capture prototype."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from orbit.evidence.model import EvidenceSource, VerificationStatus


class CaptureSourceType(str, Enum):
    CONVERSATION_SUMMARY = "conversation_summary"
    EXECUTION_REPORT = "execution_report"


class CandidateCategory(str, Enum):
    DECISION = "decision"
    DISCOVERY = "discovery"
    BUG_FIX = "bug_fix"
    COMPLETED_WORK = "completed_work"
    INCOMPLETE_WORK = "incomplete_work"
    BLOCKER = "blocker"
    CONSTRAINT = "constraint"
    IMPLEMENTATION_DETAIL = "implementation_detail"


class ReviewStatus(str, Enum):
    PENDING = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    NEEDS_EVIDENCE = "needs_evidence"


class CandidateCertainty(str, Enum):
    UNQUALIFIED = "unqualified"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True, slots=True)
class CaptureEvent:
    """One explicitly categorized source statement; its claim is never inferred."""

    category: CandidateCategory
    claim: str
    evidence: tuple[EvidenceSource, ...] = ()
    conflict_key: str | None = None
    locator: str | None = None


@dataclass(frozen=True, slots=True)
class CaptureInput:
    """A caller-supplied summary/report, not an authorization credential."""

    project_id: str
    source_type: CaptureSourceType
    source_id: str
    content: str | tuple[CaptureEvent, ...]
    source_timestamp: str | None = None
    source_revision: str | None = None
    evidence: tuple[EvidenceSource, ...] = ()


@dataclass(frozen=True, slots=True)
class CaptureSourceReference:
    source_type: CaptureSourceType
    source_id: str
    source_timestamp: str | None = None
    source_revision: str | None = None
    locator: str | None = None


@dataclass(frozen=True, slots=True)
class CaptureOccurrence:
    """One accepted occurrence in a single local Capture invocation.

    ``ordinal`` records this result's accepted input order. It is not a
    producer-issued occurrence ID or a durable identity. ``evidence_sources``
    retains references supplied with this local event; it is not a persistent
    EvidenceRecord association. Candidate-level evidence may independently
    deduplicate those references.
    """

    ordinal: int
    candidate_id: str
    source_reference: CaptureSourceReference
    evidence_sources: tuple[EvidenceSource, ...] = ()


@dataclass(frozen=True, slots=True)
class KnowledgeCandidate:
    candidate_id: str
    project_id: str
    category: CandidateCategory
    claim: str
    source_references: tuple[CaptureSourceReference, ...]
    evidence: tuple[EvidenceSource, ...]
    status: ReviewStatus
    certainty: CandidateCertainty
    verification_status: VerificationStatus
    created_at: str
    updated_at: str
    conflict_key: str | None = None
    conflicts_with: tuple[str, ...] = ()
    original_claim: str | None = None
    reviewed_by: str | None = None
    reviewed_at: str | None = None
    review_note: str | None = None


@dataclass(frozen=True, slots=True)
class SuppressedCapture:
    """Safe suppression metadata; deliberately contains no captured claim text."""

    category: CandidateCategory
    source_reference: CaptureSourceReference
    reason: str


@dataclass(frozen=True, slots=True)
class CaptureResult:
    candidates: tuple[KnowledgeCandidate, ...]
    suppressed: tuple[SuppressedCapture, ...]
    duplicate_count: int
    occurrences: tuple[CaptureOccurrence, ...] = ()
