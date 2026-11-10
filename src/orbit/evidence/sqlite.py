"""SQLite implementation of the current evidence-linked memory store port."""

from __future__ import annotations

from contextlib import closing, contextmanager
import json
from pathlib import Path
from typing import Any, Iterator, Sequence

from orbit.database.engine import connect

from .model import (
    CaptureKnowledgeProvenance,
    CaptureSourceProvenance,
    EvidenceRecord,
    EvidenceType,
    KnowledgeRecord,
    KnowledgeType,
    LifecycleStatus,
    ReferenceStatus,
    VerificationStatus,
)
from .service import (
    EvidenceLinkedMemoryStore,
    EvidenceNotAssociated,
    InvalidKnowledgeTransition,
    KnowledgeNotFound,
)


class SQLiteEvidenceLinkedMemoryStore(EvidenceLinkedMemoryStore):
    """Persist manual knowledge and evidence in the local SQLite database.

    Each operation opens a short-lived local connection. Mutations own a
    ``BEGIN IMMEDIATE`` transaction; this adapter is not used by the hosted
    request unit of work and does not implement producer conversion or durable
    Capture occurrence associations. It enforces project-scoped storage keys,
    but a project selector is not authorization; callers must use the existing
    service authorization boundary or another trusted local composition.
    """

    def __init__(self, db_path: str | Path | None = None) -> None:
        self._db_path = db_path

    def create_knowledge(
        self, record: KnowledgeRecord, evidence: Sequence[EvidenceRecord]
    ) -> None:
        project = _record_project(record)
        evidence_rows = tuple(evidence)
        for item in evidence_rows:
            _evidence_project(item, project)

        with self._atomic() as connection:
            self._insert_knowledge(connection, record)
            for position, item in enumerate(evidence_rows):
                self._insert_evidence(connection, item, record.knowledge_id, position)

    def attach_evidence(
        self, project: str, knowledge_id: str, evidence: EvidenceRecord
    ) -> EvidenceRecord:
        project = _required_text(project, "project")
        knowledge_id = _required_text(knowledge_id, "knowledge_id")
        _evidence_project(evidence, project)

        with self._atomic() as connection:
            parent = connection.execute(
                """
                SELECT knowledge_id FROM celestin_knowledge_records
                 WHERE project = ? AND knowledge_id = ?
                """,
                (project, knowledge_id),
            ).fetchone()
            if parent is None:
                raise KnowledgeNotFound(knowledge_id)

            existing = connection.execute(
                """
                SELECT evidence_id, evidence_type, source_id, locator, revision,
                       content_hash, captured_at, reference_status,
                       verification_status, checked_at, checked_by, check_note
                  FROM celestin_evidence_records
                 WHERE project = ? AND knowledge_id = ?
                   AND evidence_type = ? AND source_id = ?
                   AND locator IS ? AND revision IS ? AND content_hash IS ?
                 ORDER BY evidence_position
                 LIMIT 1
                """,
                (
                    project,
                    knowledge_id,
                    _enum_value(evidence.evidence_type, EvidenceType, "evidence_type"),
                    _required_text(evidence.source_id, "source_id"),
                    evidence.locator,
                    evidence.revision,
                    evidence.content_hash,
                ),
            ).fetchone()
            if existing is not None:
                return _evidence_from_row(project, existing)

            position = connection.execute(
                """
                SELECT COALESCE(MAX(evidence_position), -1) + 1
                  FROM celestin_evidence_records
                 WHERE project = ? AND knowledge_id = ?
                """,
                (project, knowledge_id),
            ).fetchone()[0]
            self._insert_evidence(connection, evidence, knowledge_id, int(position))
            return evidence

    def get_knowledge(self, project: str, knowledge_id: str) -> KnowledgeRecord | None:
        project = _required_text(project, "project")
        knowledge_id = _required_text(knowledge_id, "knowledge_id")
        with closing(connect(self._db_path)) as connection:
            row = connection.execute(
                """
                SELECT knowledge_id, knowledge_type, claim, created_at, updated_at,
                       verification_status, lifecycle_status, supersedes_id,
                       superseded_by_id, checked_at, checked_by, check_note,
                       capture_provenance
                  FROM celestin_knowledge_records
                 WHERE project = ? AND knowledge_id = ?
                """,
                (project, knowledge_id),
            ).fetchone()
        return None if row is None else _knowledge_from_row(project, row)

    def list_evidence(self, project: str, knowledge_id: str) -> Sequence[EvidenceRecord]:
        project = _required_text(project, "project")
        knowledge_id = _required_text(knowledge_id, "knowledge_id")
        with closing(connect(self._db_path)) as connection:
            rows = connection.execute(
                """
                SELECT evidence_id, evidence_type, source_id, locator, revision,
                       content_hash, captured_at, reference_status,
                       verification_status, checked_at, checked_by, check_note
                  FROM celestin_evidence_records
                 WHERE project = ? AND knowledge_id = ?
                 ORDER BY evidence_position
                """,
                (project, knowledge_id),
            ).fetchall()
        return tuple(_evidence_from_row(project, row) for row in rows)

    def set_knowledge_verification(
        self, project: str, knowledge_id: str, record: KnowledgeRecord
    ) -> None:
        project = _required_text(project, "project")
        knowledge_id = _required_text(knowledge_id, "knowledge_id")
        _record_project(record, project)
        if record.knowledge_id != knowledge_id:
            raise ValueError("knowledge record ID does not match target")

        with self._atomic() as connection:
            cursor = connection.execute(
                """
                UPDATE celestin_knowledge_records
                   SET verification_status = ?, updated_at = ?, checked_at = ?,
                       checked_by = ?, check_note = ?
                 WHERE project = ? AND knowledge_id = ?
                """,
                (
                    _enum_value(
                        record.verification_status,
                        VerificationStatus,
                        "verification_status",
                    ),
                    _required_text(record.updated_at, "updated_at"),
                    record.checked_at,
                    record.checked_by,
                    record.check_note,
                    project,
                    knowledge_id,
                ),
            )
            if cursor.rowcount == 0:
                raise KnowledgeNotFound(knowledge_id)

    def set_evidence_verification(
        self, project: str, knowledge_id: str, evidence: EvidenceRecord
    ) -> None:
        project = _required_text(project, "project")
        knowledge_id = _required_text(knowledge_id, "knowledge_id")
        _evidence_project(evidence, project)
        with self._atomic() as connection:
            cursor = connection.execute(
                """
                UPDATE celestin_evidence_records
                   SET verification_status = ?, checked_at = ?,
                       checked_by = ?, check_note = ?
                 WHERE project = ? AND knowledge_id = ? AND evidence_id = ?
                """,
                (
                    _enum_value(
                        evidence.verification_status,
                        VerificationStatus,
                        "verification_status",
                    ),
                    evidence.checked_at,
                    evidence.checked_by,
                    evidence.check_note,
                    project,
                    knowledge_id,
                    _required_text(evidence.evidence_id, "evidence_id"),
                ),
            )
            if cursor.rowcount == 0:
                raise EvidenceNotAssociated(evidence.evidence_id)

    def supersede_knowledge(
        self,
        project: str,
        previous_id: str,
        successor: KnowledgeRecord,
        evidence: Sequence[EvidenceRecord],
    ) -> None:
        project = _required_text(project, "project")
        previous_id = _required_text(previous_id, "previous_id")
        _record_project(successor, project)
        if successor.supersedes_id != previous_id:
            raise InvalidKnowledgeTransition("successor does not name the previous record")
        evidence_rows = tuple(evidence)
        for item in evidence_rows:
            _evidence_project(item, project)

        with self._atomic() as connection:
            previous = connection.execute(
                """
                SELECT lifecycle_status FROM celestin_knowledge_records
                 WHERE project = ? AND knowledge_id = ?
                """,
                (project, previous_id),
            ).fetchone()
            if previous is None:
                raise KnowledgeNotFound(previous_id)
            if previous[0] != LifecycleStatus.CURRENT.value:
                raise InvalidKnowledgeTransition("only a current record can be superseded")

            self._insert_knowledge(connection, successor)
            for position, item in enumerate(evidence_rows):
                self._insert_evidence(connection, item, successor.knowledge_id, position)
            cursor = connection.execute(
                """
                UPDATE celestin_knowledge_records
                   SET lifecycle_status = ?, superseded_by_id = ?, updated_at = ?
                 WHERE project = ? AND knowledge_id = ? AND lifecycle_status = ?
                """,
                (
                    LifecycleStatus.SUPERSEDED.value,
                    successor.knowledge_id,
                    _required_text(successor.created_at, "created_at"),
                    project,
                    previous_id,
                    LifecycleStatus.CURRENT.value,
                ),
            )
            if cursor.rowcount != 1:
                raise InvalidKnowledgeTransition("previous record is no longer current")

    @contextmanager
    def _atomic(self) -> Iterator[Any]:
        with closing(connect(self._db_path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.execute("COMMIT")
            except BaseException:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise

    def _insert_knowledge(self, connection: Any, record: KnowledgeRecord) -> None:
        connection.execute(
            """
            INSERT INTO celestin_knowledge_records
                (project, knowledge_id, knowledge_type, claim, created_at, updated_at,
                 verification_status, lifecycle_status, supersedes_id, superseded_by_id,
                 checked_at, checked_by, check_note, capture_provenance)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _record_project(record),
                _required_text(record.knowledge_id, "knowledge_id"),
                _enum_value(record.knowledge_type, KnowledgeType, "knowledge_type"),
                record.claim,
                _required_text(record.created_at, "created_at"),
                _required_text(record.updated_at, "updated_at"),
                _enum_value(record.verification_status, VerificationStatus, "verification_status"),
                _enum_value(record.lifecycle_status, LifecycleStatus, "lifecycle_status"),
                record.supersedes_id,
                record.superseded_by_id,
                record.checked_at,
                record.checked_by,
                record.check_note,
                _provenance_json(record.capture_provenance),
            ),
        )

    def _insert_evidence(
        self, connection: Any, evidence: EvidenceRecord, knowledge_id: str, position: int
    ) -> None:
        connection.execute(
            """
            INSERT INTO celestin_evidence_records
                (project, knowledge_id, evidence_id, evidence_position, evidence_type,
                 source_id, locator, revision, content_hash, captured_at, reference_status,
                 verification_status, checked_at, checked_by, check_note)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _evidence_project(evidence),
                _required_text(knowledge_id, "knowledge_id"),
                _required_text(evidence.evidence_id, "evidence_id"),
                position,
                _enum_value(evidence.evidence_type, EvidenceType, "evidence_type"),
                _required_text(evidence.source_id, "source_id"),
                evidence.locator,
                evidence.revision,
                evidence.content_hash,
                _required_text(evidence.captured_at, "captured_at"),
                _enum_value(evidence.reference_status, ReferenceStatus, "reference_status"),
                _enum_value(
                    evidence.verification_status,
                    VerificationStatus,
                    "verification_status",
                ),
                evidence.checked_at,
                evidence.checked_by,
                evidence.check_note,
            ),
        )


def _record_project(record: KnowledgeRecord, project: str | None = None) -> str:
    if not isinstance(record, KnowledgeRecord):
        raise TypeError("record must be a KnowledgeRecord")
    actual = _required_text(record.project, "project")
    if project is not None and actual != project:
        raise ValueError("knowledge record project does not match target project")
    return actual


def _evidence_project(evidence: EvidenceRecord, project: str | None = None) -> str:
    if not isinstance(evidence, EvidenceRecord):
        raise TypeError("evidence must be an EvidenceRecord")
    actual = _required_text(evidence.project, "project")
    if project is not None and actual != project:
        raise ValueError("evidence project does not match target project")
    return actual


def _required_text(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _enum_value(value: Any, enum_type: type, field: str) -> str:
    if not isinstance(value, enum_type):
        raise ValueError(f"{field} must be a {enum_type.__name__}")
    return value.value


def _provenance_json(value: CaptureKnowledgeProvenance | None) -> str | None:
    if value is None:
        return None
    payload = {
        "candidate_id": value.candidate_id,
        "project_id": value.project_id,
        "category": value.category,
        "certainty": value.certainty,
        "review_status": value.review_status,
        "candidate_verification_status": value.candidate_verification_status,
        "source_references": [
            {
                "source_type": item.source_type,
                "source_id": item.source_id,
                "occurred_at": item.occurred_at,
                "revision": item.revision,
                "locator": item.locator,
            }
            for item in value.source_references
        ],
        "candidate_created_at": value.candidate_created_at,
        "candidate_updated_at": value.candidate_updated_at,
        "original_claim": value.original_claim,
        "reviewed_by": value.reviewed_by,
        "reviewed_at": value.reviewed_at,
        "review_note": value.review_note,
        "conflicts_with": list(value.conflicts_with),
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _provenance_from_json(value: str | None) -> CaptureKnowledgeProvenance | None:
    if value is None:
        return None
    try:
        payload = json.loads(value)
        if not isinstance(payload, dict):
            raise TypeError("capture provenance must be an object")
        return CaptureKnowledgeProvenance(
            candidate_id=payload["candidate_id"],
            project_id=payload["project_id"],
            category=payload["category"],
            certainty=payload["certainty"],
            review_status=payload["review_status"],
            candidate_verification_status=payload["candidate_verification_status"],
            source_references=tuple(
                CaptureSourceProvenance(**item)
                for item in payload["source_references"]
            ),
            candidate_created_at=payload["candidate_created_at"],
            candidate_updated_at=payload["candidate_updated_at"],
            original_claim=payload["original_claim"],
            reviewed_by=payload["reviewed_by"],
            reviewed_at=payload["reviewed_at"],
            review_note=payload["review_note"],
            conflicts_with=tuple(payload["conflicts_with"]),
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("stored capture provenance is invalid") from exc


def _knowledge_from_row(project: str, row: Any) -> KnowledgeRecord:
    try:
        return KnowledgeRecord(
            knowledge_id=row["knowledge_id"],
            project=project,
            knowledge_type=KnowledgeType(row["knowledge_type"]),
            claim=row["claim"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            verification_status=VerificationStatus(row["verification_status"]),
            lifecycle_status=LifecycleStatus(row["lifecycle_status"]),
            supersedes_id=row["supersedes_id"],
            superseded_by_id=row["superseded_by_id"],
            checked_at=row["checked_at"],
            checked_by=row["checked_by"],
            check_note=row["check_note"],
            capture_provenance=_provenance_from_json(row["capture_provenance"]),
        )
    except (TypeError, ValueError) as exc:
        raise RuntimeError("stored knowledge record is invalid") from exc


def _evidence_from_row(project: str, row: Any) -> EvidenceRecord:
    try:
        return EvidenceRecord(
            evidence_id=row["evidence_id"],
            project=project,
            evidence_type=EvidenceType(row["evidence_type"]),
            source_id=row["source_id"],
            locator=row["locator"],
            revision=row["revision"],
            content_hash=row["content_hash"],
            captured_at=row["captured_at"],
            reference_status=ReferenceStatus(row["reference_status"]),
            verification_status=VerificationStatus(row["verification_status"]),
            checked_at=row["checked_at"],
            checked_by=row["checked_by"],
            check_note=row["check_note"],
        )
    except (TypeError, ValueError) as exc:
        raise RuntimeError("stored evidence record is invalid") from exc
