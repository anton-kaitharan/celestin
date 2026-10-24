"""Structured, evidence-referenced project-state results."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from orbit.evidence.model import CaptureKnowledgeProvenance


class FindingKind(str, Enum):
    IMPLEMENTED = "implemented"
    INCOMPLETE = "incomplete"
    UNRESOLVED = "unresolved"
    DECISION = "decision"
    MEMORY = "recorded_knowledge"
    NEXT_STEP = "next_step"


class EvidenceClass(str, Enum):
    VERIFIED = "verified"
    HISTORICAL = "historical"
    INFERRED = "inferred"
    UNVERIFIED = "unverified"
    STALE = "stale"
    CONFLICTING = "conflicting"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class EvidenceReference:
    """A locator into the inspected Git tree or a bound memory source."""

    source_type: str
    source_id: str
    revision: str | None = None
    path: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    detail: str | None = None
    classification: EvidenceClass = EvidenceClass.UNKNOWN
    evidence_record_id: str | None = None
    locator: str | None = None
    content_hash: str | None = None
    recorded_reference_status: str | None = None
    recorded_verification_status: str | None = None
    checked_at: str | None = None
    checked_by: str | None = None
    check_note: str | None = None


@dataclass(frozen=True, slots=True)
class StateFinding:
    """A single claim plus its evidence and epistemic status."""

    finding_id: str
    kind: FindingKind
    claim: str
    classification: EvidenceClass
    evidence: tuple[EvidenceReference, ...]
    rationale: str | None = None
    applicability: EvidenceClass | None = None
    recorded_verification: str | None = None
    knowledge_type: str | None = None
    knowledge_record_id: str | None = None
    capture_provenance: CaptureKnowledgeProvenance | None = None
    verification_checked_at: str | None = None
    verification_checked_by: str | None = None
    verification_check_note: str | None = None
    lifecycle_status: str | None = None
    supersedes_knowledge_id: str | None = None
    superseded_by_knowledge_id: str | None = None


@dataclass(frozen=True, slots=True)
class ProjectStateSnapshot:
    project_id: str
    repository_id: str
    repository_root: str
    inspected_revision: str
    is_current_head: bool
    implemented: tuple[StateFinding, ...]
    incomplete: tuple[StateFinding, ...]
    unresolved: tuple[StateFinding, ...]
    decisions: tuple[StateFinding, ...]
    recorded_knowledge: tuple[StateFinding, ...]
    next_steps: tuple[StateFinding, ...]
    limitations: tuple[str, ...]
