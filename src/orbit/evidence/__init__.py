"""Storage-neutral evidence-linked project memory domain."""

from .model import (
    CaptureKnowledgeProvenance,
    CaptureSourceProvenance,
    EvidenceRecord,
    EvidenceSource,
    EvidenceType,
    KnowledgeBundle,
    KnowledgeRecord,
    KnowledgeType,
    LifecycleStatus,
    ReferenceStatus,
    VerificationStatus,
)
from .service import EvidenceLinkedMemoryService

__all__ = [
    "EvidenceLinkedMemoryService",
    "CaptureKnowledgeProvenance",
    "CaptureSourceProvenance",
    "EvidenceRecord",
    "EvidenceSource",
    "EvidenceType",
    "KnowledgeBundle",
    "KnowledgeRecord",
    "KnowledgeType",
    "LifecycleStatus",
    "ReferenceStatus",
    "VerificationStatus",
]
