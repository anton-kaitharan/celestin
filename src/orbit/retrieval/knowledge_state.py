"""Optional, versioned Capability 1 state carried by Retrieval context items."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from orbit.evidence.model import (
    EvidenceType,
    KnowledgeBundle,
    KnowledgeRecord,
    KnowledgeType,
    LifecycleStatus,
    ReferenceStatus,
    VerificationStatus,
)
from orbit.project_state.model import EvidenceClass

KNOWLEDGE_STATE_SCHEMA_VERSION = 1


class KnowledgeStateScopeError(ValueError):
    """A reader returned knowledge or evidence outside the requested project."""


@dataclass(frozen=True, slots=True)
class EvidenceState:
    """Capability 1 evidence metadata, retaining source references as supplied."""

    evidence_id: str
    evidence_type: EvidenceType
    source_id: str
    locator: str | None
    revision: str | None
    content_hash: str | None
    captured_at: str
    reference_status: ReferenceStatus
    verification_status: VerificationStatus
    checked_at: str | None
    checked_by: str | None
    check_note: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "evidence_id": self.evidence_id,
            "evidence_type": self.evidence_type.value,
            "source_id": self.source_id,
            "locator": self.locator,
            "revision": self.revision,
            "content_hash": self.content_hash,
            "captured_at": self.captured_at,
            "reference_status": self.reference_status.value,
            "verification_status": self.verification_status.value,
            "checked_at": self.checked_at,
            "checked_by": self.checked_by,
            "check_note": self.check_note,
        }


@dataclass(frozen=True, slots=True)
class CaptureSourceState:
    """Capture-time source provenance; not a replacement for evidence records."""

    source_type: str
    source_id: str
    occurred_at: str | None
    revision: str | None
    locator: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "source_type": self.source_type,
            "source_id": self.source_id,
            "occurred_at": self.occurred_at,
            "revision": self.revision,
            "locator": self.locator,
        }


@dataclass(frozen=True, slots=True)
class CaptureKnowledgeState:
    """Original Capability 1 capture and review provenance."""

    candidate_id: str
    project_id: str
    category: str
    certainty: str
    review_status: str
    candidate_verification_status: str
    source_references: tuple[CaptureSourceState, ...]
    candidate_created_at: str
    candidate_updated_at: str
    original_claim: str | None
    reviewed_by: str | None
    reviewed_at: str | None
    review_note: str | None
    conflicts_with: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "candidate_id": self.candidate_id,
            "project_id": self.project_id,
            "category": self.category,
            "certainty": self.certainty,
            "review_status": self.review_status,
            "candidate_verification_status": self.candidate_verification_status,
            "source_references": [item.as_dict() for item in self.source_references],
            "candidate_created_at": self.candidate_created_at,
            "candidate_updated_at": self.candidate_updated_at,
            "original_claim": self.original_claim,
            "reviewed_by": self.reviewed_by,
            "reviewed_at": self.reviewed_at,
            "review_note": self.review_note,
            "conflicts_with": list(self.conflicts_with),
        }


@dataclass(frozen=True, slots=True)
class KnowledgeStateEnvelope:
    """Version 1 view of one Capability 1 bundle attached to a Retrieval item.

    The envelope is supplemental metadata. Its verification and lifecycle
    values do not change the candidate's Retrieval status or ranking.
    """

    schema_version: Literal[1]
    knowledge_id: str
    project_id: str
    knowledge_type: KnowledgeType
    claim: str
    verification_status: VerificationStatus
    lifecycle_status: LifecycleStatus
    created_at: str
    updated_at: str
    supersedes_id: str | None
    superseded_by_id: str | None
    checked_at: str | None
    checked_by: str | None
    check_note: str | None
    evidence: tuple[EvidenceState, ...]
    capture_provenance: CaptureKnowledgeState | None
    reconstruction_assessment: EvidenceClass | None = None

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != KNOWLEDGE_STATE_SCHEMA_VERSION:
            raise ValueError("unsupported knowledge_state schema_version")

    @classmethod
    def from_bundle(cls, bundle: KnowledgeBundle) -> KnowledgeStateEnvelope:
        """Map stored Capability 1 values without deriving missing metadata."""
        if not isinstance(bundle, KnowledgeBundle):
            raise TypeError("bundle must be a KnowledgeBundle")
        record = bundle.record
        if not isinstance(record, KnowledgeRecord):
            raise TypeError("bundle.record must be a KnowledgeRecord")
        if any(item.project != record.project for item in bundle.evidence):
            raise KnowledgeStateScopeError("Capability 1 evidence project does not match its knowledge record")

        provenance = record.capture_provenance
        capture_state = None
        if provenance is not None:
            if provenance.project_id != record.project:
                raise KnowledgeStateScopeError(
                    "Capability 1 capture provenance project does not match its knowledge record"
                )
            capture_state = CaptureKnowledgeState(
                candidate_id=provenance.candidate_id,
                project_id=provenance.project_id,
                category=provenance.category,
                certainty=provenance.certainty,
                review_status=provenance.review_status,
                candidate_verification_status=provenance.candidate_verification_status,
                source_references=tuple(
                    CaptureSourceState(
                        source_type=source.source_type,
                        source_id=source.source_id,
                        occurred_at=source.occurred_at,
                        revision=source.revision,
                        locator=source.locator,
                    )
                    for source in provenance.source_references
                ),
                candidate_created_at=provenance.candidate_created_at,
                candidate_updated_at=provenance.candidate_updated_at,
                original_claim=provenance.original_claim,
                reviewed_by=provenance.reviewed_by,
                reviewed_at=provenance.reviewed_at,
                review_note=provenance.review_note,
                conflicts_with=tuple(provenance.conflicts_with),
            )

        return cls(
            schema_version=KNOWLEDGE_STATE_SCHEMA_VERSION,
            knowledge_id=record.knowledge_id,
            project_id=record.project,
            knowledge_type=record.knowledge_type,
            claim=record.claim,
            verification_status=record.verification_status,
            lifecycle_status=record.lifecycle_status,
            created_at=record.created_at,
            updated_at=record.updated_at,
            supersedes_id=record.supersedes_id,
            superseded_by_id=record.superseded_by_id,
            checked_at=record.checked_at,
            checked_by=record.checked_by,
            check_note=record.check_note,
            evidence=tuple(
                EvidenceState(
                    evidence_id=item.evidence_id,
                    evidence_type=item.evidence_type,
                    source_id=item.source_id,
                    locator=item.locator,
                    revision=item.revision,
                    content_hash=item.content_hash,
                    captured_at=item.captured_at,
                    reference_status=item.reference_status,
                    verification_status=item.verification_status,
                    checked_at=item.checked_at,
                    checked_by=item.checked_by,
                    check_note=item.check_note,
                )
                for item in bundle.evidence
            ),
            capture_provenance=capture_state,
        )

    def as_dict(self) -> dict[str, object]:
        """Return the stable JSON representation used by context serialization."""
        return {
            "schema_version": self.schema_version,
            "knowledge_id": self.knowledge_id,
            "project_id": self.project_id,
            "knowledge_type": self.knowledge_type.value,
            "claim": self.claim,
            "verification_status": self.verification_status.value,
            "lifecycle_status": self.lifecycle_status.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "supersedes_id": self.supersedes_id,
            "superseded_by_id": self.superseded_by_id,
            "checked_at": self.checked_at,
            "checked_by": self.checked_by,
            "check_note": self.check_note,
            "evidence": [item.as_dict() for item in self.evidence],
            "capture_provenance": (
                None if self.capture_provenance is None else self.capture_provenance.as_dict()
            ),
            "reconstruction_assessment": (
                None if self.reconstruction_assessment is None else self.reconstruction_assessment.value
            ),
        }


class KnowledgeStateReader(Protocol):
    """Optional Retrieval seam; implementations explicitly map an existing candidate."""

    def get_for_candidate(
        self, project_id: str, record_kind: str, record_id: str
    ) -> KnowledgeBundle | None: ...
