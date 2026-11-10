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
from .sqlite import SQLiteEvidenceLinkedMemoryStore
from .sqlite_resolver import SQLiteMessageEvidenceResolver

__all__ = [
    "EvidenceLinkedMemoryService",
    "SQLiteEvidenceLinkedMemoryStore",
    "SQLiteMessageEvidenceResolver",
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
