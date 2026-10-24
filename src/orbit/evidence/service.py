"""Authorized domain service for evidence-linked knowledge.

Persistence is supplied through a storage-neutral port. The request-bound
PostgreSQL adapter implements that port through the hosted request unit of work;
there is no SQLite Evidence adapter or MCP runtime wiring. See
docs/evidence-linked-memory-contract.md for the domain prototype and limitations.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Callable, Protocol, Sequence
from uuid import uuid4

from orbit.mcp_server.authorization import (
    AuthenticatedPrincipal,
    AuthorizedProject,
    ProjectAccessDenied,
    ProjectAuthorizer,
    READ,
    WRITE,
)

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


class EvidenceMemoryError(RuntimeError):
    """Base class for evidence-linked memory service failures."""


class EvidenceValidationError(EvidenceMemoryError, ValueError):
    """A caller supplied an invalid record or evidence reference."""


class EvidenceReferenceMissing(EvidenceMemoryError):
    """A source resolver confirmed that a referenced source does not exist."""


class EvidenceNotAssociated(EvidenceMemoryError):
    """An evidence ID is not associated with the requested knowledge record."""


class EvidenceReferenceResolutionError(EvidenceMemoryError):
    """A source resolver failed; the reference was not treated as verified."""


class KnowledgeNotFound(EvidenceMemoryError):
    """A knowledge record does not exist in the authorized project."""


class InvalidKnowledgeTransition(EvidenceMemoryError):
    """A lifecycle transition is invalid or would discard history."""


class EvidenceLinkedMemoryStore(Protocol):
    """Minimal persistence contract; adapters must enforce project-scoped IDs.

    Adapters must round-trip optional ``KnowledgeRecord.capture_provenance``
    without dropping or rewriting it. An adapter that cannot preserve it must
    explicitly reject writes that contain capture provenance.

    ``create_knowledge`` and ``supersede_knowledge`` are atomic operations.
    ``attach_evidence`` is idempotent for the same project, knowledge record,
    evidence type, source ID, locator, revision, and content hash, returning the
    existing evidence row.
    """

    def create_knowledge(
        self, record: KnowledgeRecord, evidence: Sequence[EvidenceRecord]
    ) -> None: ...

    def attach_evidence(
        self, project: str, knowledge_id: str, evidence: EvidenceRecord
    ) -> EvidenceRecord: ...

    def get_knowledge(self, project: str, knowledge_id: str) -> KnowledgeRecord | None: ...

    def list_evidence(
        self, project: str, knowledge_id: str
    ) -> Sequence[EvidenceRecord]: ...

    def set_knowledge_verification(
        self, project: str, knowledge_id: str, record: KnowledgeRecord
    ) -> None: ...

    def set_evidence_verification(
        self, project: str, knowledge_id: str, evidence: EvidenceRecord
    ) -> None: ...

    def supersede_knowledge(
        self,
        project: str,
        previous_id: str,
        successor: KnowledgeRecord,
        evidence: Sequence[EvidenceRecord],
    ) -> None: ...


class EvidenceReferenceResolver(Protocol):
    """Resolve a source identifier inside a project, without inferring truth."""

    def resolve(self, project: str, source: EvidenceSource) -> ReferenceStatus: ...


class EvidenceLinkedMemoryService:
    """Create, associate, inspect, verify, and supersede project knowledge.

    A supplied ``AuthorizedProject`` is a project/action selector, not proof of
    authorization. Each operation is checked by the configured authorizer
    using the principal fixed when the trusted caller builds this service.
    Verification status changes are explicit caller assertions; this service
    never checks source truth on its own.
    """

    def __init__(
        self,
        store: EvidenceLinkedMemoryStore,
        resolver: EvidenceReferenceResolver,
        *,
        authorizer: ProjectAuthorizer,
        principal: AuthenticatedPrincipal | None = None,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._resolver = resolver
        self._authorizer = authorizer
        self._principal = principal
        self._id_factory = id_factory or (lambda: str(uuid4()))
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def create_knowledge(
        self,
        scope: AuthorizedProject,
        knowledge_type: KnowledgeType,
        claim: str,
        evidence_sources: Sequence[EvidenceSource],
        *,
        capture_provenance: CaptureKnowledgeProvenance | None = None,
    ) -> KnowledgeBundle:
        project = self._require_scope(scope, WRITE)
        claim = _required_text(claim, "claim")
        if not isinstance(knowledge_type, KnowledgeType):
            raise EvidenceValidationError("knowledge_type must be a KnowledgeType")
        if not evidence_sources:
            raise EvidenceValidationError("at least one evidence source is required")
        _validate_capture_provenance(capture_provenance, project, evidence_sources)

        now = self._now()
        evidence = self._new_evidence(project, evidence_sources, now)
        record = KnowledgeRecord(
            knowledge_id=self._new_id(),
            project=project,
            knowledge_type=knowledge_type,
            claim=claim,
            created_at=now,
            updated_at=now,
            capture_provenance=capture_provenance,
        )
        self._store.create_knowledge(record, evidence)
        return KnowledgeBundle(record=record, evidence=evidence)

    def associate_evidence(
        self,
        scope: AuthorizedProject,
        knowledge_id: str,
        source: EvidenceSource,
    ) -> EvidenceRecord:
        project = self._require_scope(scope, WRITE)
        knowledge_id = _required_text(knowledge_id, "knowledge_id")
        self._require_record(project, knowledge_id)
        evidence = self._new_evidence(project, (source,), self._now())[0]
        return self._store.attach_evidence(project, knowledge_id, evidence)

    def get_knowledge(
        self, scope: AuthorizedProject, knowledge_id: str
    ) -> KnowledgeBundle:
        project = self._require_scope(scope, READ)
        knowledge_id = _required_text(knowledge_id, "knowledge_id")
        record = self._require_record(project, knowledge_id)
        evidence = tuple(self._store.list_evidence(project, knowledge_id))
        if any(
            not isinstance(item, EvidenceRecord) or item.project != project
            for item in evidence
        ):
            raise EvidenceMemoryError(
                "storage returned evidence outside the requested project"
            )
        # Re-resolve references at read time. Availability is not proof that
        # evidence is current or that it supports the claim.
        refreshed = tuple(
            replace(
                item,
                reference_status=self._resolve(
                    project,
                    EvidenceSource(
                        item.evidence_type,
                        item.source_id,
                        item.locator,
                        item.revision,
                        item.content_hash,
                    ),
                ),
            )
            for item in evidence
        )
        return KnowledgeBundle(record=record, evidence=refreshed)

    def set_knowledge_verification(
        self,
        scope: AuthorizedProject,
        knowledge_id: str,
        status: VerificationStatus,
        *,
        checked_by: str | None = None,
        note: str | None = None,
    ) -> KnowledgeRecord:
        project = self._require_scope(scope, WRITE)
        record = self._require_record(project, _required_text(knowledge_id, "knowledge_id"))
        checked_by, note = _verification_metadata(status, checked_by, note)
        now = self._now()
        checked_at = (
            record.checked_at
            if status is VerificationStatus.UNVERIFIED
            else now
        )
        if status is VerificationStatus.UNVERIFIED:
            checked_by, note = record.checked_by, record.check_note
        updated = replace(
            record,
            verification_status=status,
            updated_at=now,
            checked_at=checked_at,
            checked_by=checked_by,
            check_note=note,
        )
        self._store.set_knowledge_verification(project, knowledge_id, updated)
        return updated

    def set_evidence_verification(
        self,
        scope: AuthorizedProject,
        knowledge_id: str,
        evidence_id: str,
        status: VerificationStatus,
        *,
        checked_by: str | None = None,
        note: str | None = None,
    ) -> EvidenceRecord:
        project = self._require_scope(scope, WRITE)
        knowledge_id = _required_text(knowledge_id, "knowledge_id")
        evidence_id = _required_text(evidence_id, "evidence_id")
        self._require_record(project, knowledge_id)
        evidence = self._store.list_evidence(project, knowledge_id)
        target = next((item for item in evidence if item.evidence_id == evidence_id), None)
        if target is None:
            raise EvidenceNotAssociated("evidence is not associated with this knowledge record")
        if target.project != project:
            raise EvidenceMemoryError("storage returned evidence outside the requested project")
        checked_by, note = _verification_metadata(status, checked_by, note)
        checked_at = (
            target.checked_at
            if status is VerificationStatus.UNVERIFIED
            else self._now()
        )
        if status is VerificationStatus.UNVERIFIED:
            checked_by, note = target.checked_by, target.check_note
        updated = replace(
            target,
            verification_status=status,
            checked_at=checked_at,
            checked_by=checked_by,
            check_note=note,
        )
        self._store.set_evidence_verification(project, knowledge_id, updated)
        return updated

    def supersede_knowledge(
        self,
        scope: AuthorizedProject,
        previous_id: str,
        knowledge_type: KnowledgeType,
        claim: str,
        evidence_sources: Sequence[EvidenceSource],
        *,
        capture_provenance: CaptureKnowledgeProvenance | None = None,
    ) -> KnowledgeBundle:
        project = self._require_scope(scope, WRITE)
        previous_id = _required_text(previous_id, "previous_id")
        previous = self._require_record(project, previous_id)
        if previous.lifecycle_status is not LifecycleStatus.CURRENT:
            raise InvalidKnowledgeTransition("only a current record can be superseded")
        claim = _required_text(claim, "claim")
        if not isinstance(knowledge_type, KnowledgeType):
            raise EvidenceValidationError("knowledge_type must be a KnowledgeType")
        if not evidence_sources:
            raise EvidenceValidationError("at least one evidence source is required")
        _validate_capture_provenance(capture_provenance, project, evidence_sources)

        now = self._now()
        evidence = self._new_evidence(project, evidence_sources, now)
        successor = KnowledgeRecord(
            knowledge_id=self._new_id(),
            project=project,
            knowledge_type=knowledge_type,
            claim=claim,
            created_at=now,
            updated_at=now,
            supersedes_id=previous_id,
            capture_provenance=capture_provenance,
        )
        self._store.supersede_knowledge(project, previous_id, successor, evidence)
        return KnowledgeBundle(record=successor, evidence=evidence)

    def _new_evidence(
        self,
        project: str,
        sources: Sequence[EvidenceSource],
        captured_at: str,
    ) -> tuple[EvidenceRecord, ...]:
        validated: list[EvidenceSource] = []
        seen: set[tuple[EvidenceType, str, str | None, str | None, str | None]] = set()
        for source in sources:
            if not isinstance(source, EvidenceSource):
                raise EvidenceValidationError("each evidence source must be an EvidenceSource")
            if not isinstance(source.evidence_type, EvidenceType):
                raise EvidenceValidationError("evidence_type must be an EvidenceType")
            source_id = _required_text(source.source_id, "source_id")
            locator = _optional_text(source.locator, "locator")
            revision = _optional_text(source.revision, "revision")
            content_hash = _optional_text(source.content_hash, "content_hash")
            key = (
                source.evidence_type,
                source_id,
                locator,
                revision,
                content_hash,
            )
            if key in seen:
                continue
            seen.add(key)
            validated.append(
                replace(
                    source,
                    source_id=source_id,
                    locator=locator,
                    revision=revision,
                    content_hash=content_hash,
                )
            )
        if not validated:
            raise EvidenceValidationError("at least one unique evidence source is required")

        records: list[EvidenceRecord] = []
        for source in validated:
            resolution = self._resolve(project, source)
            if resolution is ReferenceStatus.MISSING:
                raise EvidenceReferenceMissing(
                    f"{source.evidence_type.value} source {source.source_id!r} was not found in project {project!r}"
                )
            records.append(
                EvidenceRecord(
                    evidence_id=self._new_id(),
                    project=project,
                    evidence_type=source.evidence_type,
                    source_id=source.source_id,
                    locator=source.locator,
                    revision=source.revision,
                    content_hash=source.content_hash,
                    captured_at=captured_at,
                    reference_status=resolution,
                )
            )
        return tuple(records)

    def _resolve(self, project: str, source: EvidenceSource) -> ReferenceStatus:
        try:
            result = self._resolver.resolve(project, source)
        except Exception as exc:
            raise EvidenceReferenceResolutionError(
                f"could not resolve {source.evidence_type.value} source {source.source_id!r}"
            ) from exc
        if not isinstance(result, ReferenceStatus):
            raise EvidenceReferenceResolutionError("resolver returned an invalid reference status")
        return result

    def _require_record(self, project: str, knowledge_id: str) -> KnowledgeRecord:
        record = self._store.get_knowledge(project, knowledge_id)
        if record is None:
            raise KnowledgeNotFound(
                f"knowledge record {knowledge_id!r} was not found in project {project!r}"
            )
        if record.project != project:
            raise EvidenceMemoryError("storage returned a record outside the requested project")
        return record

    def _new_id(self) -> str:
        value = self._id_factory()
        return _required_text(value, "generated identifier")

    def _require_scope(self, scope: AuthorizedProject, action: str) -> str:
        if not isinstance(scope, AuthorizedProject) or scope.action != action:
            raise PermissionError(f"a project {action} selector is required")
        project = _required_text(scope.project, "project")
        try:
            checked = self._authorizer.authorize(self._principal, project, action)
        except ProjectAccessDenied:
            raise PermissionError("project access denied") from None
        if (
            not isinstance(checked, AuthorizedProject)
            or checked.project != project
            or checked.action != action
        ):
            raise PermissionError("project access denied")
        return project

    def _now(self) -> str:
        value = self._clock()
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise EvidenceValidationError("clock must return a timezone-aware datetime")
        return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
            "+00:00", "Z"
        )


def _required_text(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise EvidenceValidationError(f"{field} must be a non-empty string")
    return value.strip()


def _optional_text(value: str | None, field: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, field)


def _verification_metadata(
    status: VerificationStatus,
    checked_by: str | None,
    note: str | None,
) -> tuple[str | None, str | None]:
    if not isinstance(status, VerificationStatus):
        raise EvidenceValidationError("status must be a VerificationStatus")
    if status is VerificationStatus.UNVERIFIED:
        if checked_by is not None or note is not None:
            raise EvidenceValidationError("unverified status cannot include check metadata")
        return None, None
    if checked_by is None:
        raise EvidenceValidationError("checked_by is required when setting a checked status")
    checked_by = _required_text(checked_by, "checked_by")
    if note is not None and not isinstance(note, str):
        raise EvidenceValidationError("note must be a string or None")
    return checked_by, note.strip() if note else None


def _validate_capture_provenance(
    provenance: CaptureKnowledgeProvenance | None,
    project: str,
    evidence_sources: Sequence[EvidenceSource],
) -> None:
    if provenance is None:
        return
    if not isinstance(provenance, CaptureKnowledgeProvenance):
        raise EvidenceValidationError(
            "capture_provenance must be a CaptureKnowledgeProvenance or None"
        )
    if _required_text(provenance.project_id, "capture provenance project") != project:
        raise EvidenceValidationError(
            "capture provenance project does not match authorized project"
        )
    for field, value in (
        ("candidate_id", provenance.candidate_id),
        ("category", provenance.category),
        ("certainty", provenance.certainty),
        ("review_status", provenance.review_status),
        ("candidate_verification_status", provenance.candidate_verification_status),
        ("candidate_created_at", provenance.candidate_created_at),
        ("candidate_updated_at", provenance.candidate_updated_at),
    ):
        _required_text(value, f"capture provenance {field}")
    if provenance.review_status != "approved":
        raise EvidenceValidationError(
            "only approved capture provenance can be attached to knowledge"
        )
    _required_text(provenance.reviewed_by, "capture provenance reviewed_by")
    _required_text(provenance.reviewed_at, "capture provenance reviewed_at")
    if provenance.candidate_verification_status != VerificationStatus.UNVERIFIED.value:
        raise EvidenceValidationError(
            "capture candidate verification must remain unverified during conversion"
        )
    if not isinstance(provenance.source_references, tuple):
        raise EvidenceValidationError("capture source references must be a tuple")
    for reference in provenance.source_references:
        if not isinstance(reference, CaptureSourceProvenance):
            raise EvidenceValidationError(
                "capture source references must be CaptureSourceProvenance values"
            )
        _required_text(reference.source_type, "capture source type")
        _required_text(reference.source_id, "capture source ID")
        for name in ("occurred_at", "revision", "locator"):
            value = getattr(reference, name)
            if value is not None:
                _required_text(value, f"capture source {name}")
    mapped_sources: set[tuple[str, str, str | None, str | None]] = set()
    for source in evidence_sources:
        if not isinstance(source, EvidenceSource) or not isinstance(
            source.evidence_type, EvidenceType
        ):
            raise EvidenceValidationError("each evidence source must be an EvidenceSource")
        mapped_sources.add(
            (
                source.evidence_type.value,
                _required_text(source.source_id, "source_id"),
                _optional_text(source.locator, "locator"),
                _optional_text(source.revision, "revision"),
            )
        )
    for reference in provenance.source_references:
        mapped_locator = ": ".join(
            part for part in (reference.source_type, reference.locator) if part
        )
        expected = (
            EvidenceType.OTHER.value,
            reference.source_id,
            mapped_locator,
            reference.revision,
        )
        if expected not in mapped_sources:
            raise EvidenceValidationError(
                "capture source provenance has no matching evidence source"
            )
    if not isinstance(provenance.conflicts_with, tuple):
        raise EvidenceValidationError("capture conflict references must be a tuple")
    for candidate_id in provenance.conflicts_with:
        _required_text(candidate_id, "conflicting candidate ID")
    for field in ("reviewed_by", "reviewed_at", "review_note", "original_claim"):
        value = getattr(provenance, field)
        if value is not None and not isinstance(value, str):
            raise EvidenceValidationError(f"capture provenance {field} must be text or None")
