"""Immutable records for claims and the evidence associated with them."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class KnowledgeType(str, Enum):
    DECISION = "decision"
    DISCOVERY = "discovery"
    BUG_FIX = "bug_fix"
    IMPLEMENTATION_FINDING = "implementation_finding"
    COMPLETED_WORK = "completed_work"
    REQUIREMENT = "requirement"
    OPEN_QUESTION = "open_question"


class EvidenceType(str, Enum):
    MESSAGE = "message"
    REPOSITORY_FILE = "repository_file"
    GIT_COMMIT = "git_commit"
    TEST_RESULT = "test_result"
    DOCUMENT = "document"
    OTHER = "other"


class VerificationStatus(str, Enum):
    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    STALE = "stale"
    CONFLICTING = "conflicting"


class LifecycleStatus(str, Enum):
    CURRENT = "current"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"


class ReferenceStatus(str, Enum):
    AVAILABLE = "available"
    UNRESOLVED = "unresolved"
    MISSING = "missing"


@dataclass(frozen=True, slots=True)
class EvidenceSource:
    """A reference to an existing source; source contents are not copied."""

    evidence_type: EvidenceType
    source_id: str
    locator: str | None = None
    revision: str | None = None
    content_hash: str | None = None


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    evidence_id: str
    project: str
    evidence_type: EvidenceType
    source_id: str
    locator: str | None
    revision: str | None
    content_hash: str | None
    captured_at: str
    reference_status: ReferenceStatus
    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED
    checked_at: str | None = None
    checked_by: str | None = None
    check_note: str | None = None


@dataclass(frozen=True, slots=True)
class CaptureSourceProvenance:
    """Original capture-source locator and occurrence time, before evidence mapping."""

    source_type: str
    source_id: str
    occurred_at: str | None = None
    revision: str | None = None
    locator: str | None = None


@dataclass(frozen=True, slots=True)
class CaptureKnowledgeProvenance:
    """Review and capture metadata carried with a converted knowledge claim.

    Reviewer labels are provenance supplied by the capture domain. They are not
    authenticated identities and do not establish verification.
    """

    candidate_id: str
    project_id: str
    category: str
    certainty: str
    review_status: str
    candidate_verification_status: str
    source_references: tuple[CaptureSourceProvenance, ...]
    candidate_created_at: str
    candidate_updated_at: str
    original_claim: str | None = None
    reviewed_by: str | None = None
    reviewed_at: str | None = None
    review_note: str | None = None
    conflicts_with: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class KnowledgeRecord:
    knowledge_id: str
    project: str
    knowledge_type: KnowledgeType
    claim: str
    created_at: str
    updated_at: str
    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED
    lifecycle_status: LifecycleStatus = LifecycleStatus.CURRENT
    supersedes_id: str | None = None
    superseded_by_id: str | None = None
    checked_at: str | None = None
    checked_by: str | None = None
    check_note: str | None = None
    capture_provenance: CaptureKnowledgeProvenance | None = None


@dataclass(frozen=True, slots=True)
class KnowledgeBundle:
    record: KnowledgeRecord
    evidence: tuple[EvidenceRecord, ...]
