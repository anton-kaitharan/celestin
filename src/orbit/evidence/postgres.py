"""Request-bound PostgreSQL adapter for the existing Evidence store port.

This persists the current manual Evidence/Knowledge domain interface only. It
does not implement Capture conversion, producer delivery, occurrence links,
approval, or conversion replay. Hosted request composition supplies the
resolver-issued scope and owns the outer transaction and connection lifecycle.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import re
from typing import Any, Iterator, Protocol, Sequence

from orbit.mcp_server.hosted_scope import HostedAccessDenied, ResolvedProjectScope

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


class _Connection(Protocol):
    def execute(self, query: str, params: tuple[Any, ...] = ()) -> Any: ...

    def transaction(self) -> Any: ...


class PostgresEvidenceLinkedMemoryStore(EvidenceLinkedMemoryStore):
    """Evidence store bound to one authorized workspace/project transaction.

    There is intentionally no DSN or connection-factory constructor. The unit
    of work is the only supported composition path, so these operations borrow
    its connection and cannot commit, roll back, or close the request session.
    Nested transaction contexts are savepoints under psycopg and make the
    protocol's multi-row create and supersede operations atomic even if a
    trusted request callback handles an operation error.
    """

    def __init__(self, connection: _Connection, scope: ResolvedProjectScope) -> None:
        if connection is None or not callable(getattr(connection, "execute", None)):
            raise TypeError("connection must provide execute()")
        if not callable(getattr(connection, "transaction", None)):
            raise TypeError("connection must provide transaction()")
        if not isinstance(scope, ResolvedProjectScope):
            raise TypeError("scope must be issued by HostedScopeResolver")
        self._connection = connection
        self._scope = scope

    @classmethod
    def _for_request(
        cls, connection: _Connection, scope: ResolvedProjectScope
    ) -> PostgresEvidenceLinkedMemoryStore:
        return cls(connection, scope)

    def create_knowledge(
        self, record: KnowledgeRecord, evidence: Sequence[EvidenceRecord]
    ) -> None:
        project = self._require_write_project(record.project)
        _validate_record_project(record, project)
        evidence_rows = tuple(evidence)
        for item in evidence_rows:
            _validate_evidence_project(item, project)

        with self._atomic():
            self._insert_knowledge(record)
            for position, item in enumerate(evidence_rows):
                self._insert_evidence(item, record.knowledge_id, position)

    def attach_evidence(
        self, project: str, knowledge_id: str, evidence: EvidenceRecord
    ) -> EvidenceRecord:
        project = self._require_write_project(project)
        _validate_evidence_project(evidence, project)
        knowledge_id = _required_text(knowledge_id, "knowledge_id")

        with self._atomic():
            parent = self._fetchone(
                """
                SELECT knowledge_id
                  FROM celestin_knowledge_records
                 WHERE workspace_id = %s AND project_id = %s AND knowledge_id = %s
                 FOR UPDATE
                """,
                (self._scope.workspace_id, self._scope.project_id, knowledge_id),
            )
            if parent is None:
                raise KnowledgeNotFound(knowledge_id)
            existing = self._fetchone(
                """
                SELECT evidence_id, evidence_type, source_id, locator, revision,
                       content_hash, captured_at, reference_status,
                       verification_status, checked_at, checked_by, check_note
                  FROM celestin_evidence_records
                 WHERE workspace_id = %s AND project_id = %s AND knowledge_id = %s
                   AND evidence_type = %s AND source_id = %s
                   AND locator IS NOT DISTINCT FROM %s
                   AND revision IS NOT DISTINCT FROM %s
                   AND content_hash IS NOT DISTINCT FROM %s
                 ORDER BY evidence_position
                 LIMIT 1
                """,
                (
                    self._scope.workspace_id,
                    self._scope.project_id,
                    knowledge_id,
                    evidence.evidence_type.value,
                    evidence.source_id,
                    evidence.locator,
                    evidence.revision,
                    evidence.content_hash,
                ),
            )
            if existing is not None:
                return _evidence_from_row(project, existing)
            position = self._fetchone(
                """
                SELECT COALESCE(MAX(evidence_position), -1) + 1
                  FROM celestin_evidence_records
                 WHERE workspace_id = %s AND project_id = %s AND knowledge_id = %s
                """,
                (self._scope.workspace_id, self._scope.project_id, knowledge_id),
            )[0]
            self._insert_evidence(evidence, knowledge_id, int(position))
            return evidence

    def get_knowledge(self, project: str, knowledge_id: str) -> KnowledgeRecord | None:
        project = self._require_read_project(project)
        knowledge_id = _required_text(knowledge_id, "knowledge_id")
        row = self._fetchone(
            """
            SELECT knowledge_id, knowledge_type, claim, created_at, updated_at,
                   verification_status, lifecycle_status, supersedes_id,
                   superseded_by_id, checked_at, checked_by, check_note,
                   capture_provenance
              FROM celestin_knowledge_records
             WHERE workspace_id = %s AND project_id = %s AND knowledge_id = %s
            """,
            (self._scope.workspace_id, self._scope.project_id, knowledge_id),
        )
        return None if row is None else _knowledge_from_row(project, row)

    def list_evidence(self, project: str, knowledge_id: str) -> Sequence[EvidenceRecord]:
        project = self._require_read_project(project)
        knowledge_id = _required_text(knowledge_id, "knowledge_id")
        rows = self._fetchall(
            """
            SELECT evidence_id, evidence_type, source_id, locator, revision,
                   content_hash, captured_at, reference_status,
                   verification_status, checked_at, checked_by, check_note
              FROM celestin_evidence_records
             WHERE workspace_id = %s AND project_id = %s AND knowledge_id = %s
             ORDER BY evidence_position
            """,
            (self._scope.workspace_id, self._scope.project_id, knowledge_id),
        )
        return tuple(_evidence_from_row(project, row) for row in rows)

    def set_knowledge_verification(
        self, project: str, knowledge_id: str, record: KnowledgeRecord
    ) -> None:
        project = self._require_write_project(project)
        _validate_record_project(record, project)
        knowledge_id = _required_text(knowledge_id, "knowledge_id")
        if record.knowledge_id != knowledge_id:
            raise ValueError("knowledge record ID does not match target")
        with self._atomic():
            row = self._fetchone(
                """
                UPDATE celestin_knowledge_records
                   SET verification_status = %s, updated_at = %s,
                       checked_at = %s, checked_by = %s, check_note = %s
                 WHERE workspace_id = %s AND project_id = %s AND knowledge_id = %s
                 RETURNING knowledge_id
                """,
                (
                    record.verification_status.value,
                    _postgres_timestamp(record.updated_at),
                    _postgres_timestamp(record.checked_at) if record.checked_at else None,
                    record.checked_by,
                    record.check_note,
                    self._scope.workspace_id,
                    self._scope.project_id,
                    knowledge_id,
                ),
            )
            if row is None:
                raise KnowledgeNotFound(knowledge_id)

    def set_evidence_verification(
        self, project: str, knowledge_id: str, evidence: EvidenceRecord
    ) -> None:
        project = self._require_write_project(project)
        _validate_evidence_project(evidence, project)
        knowledge_id = _required_text(knowledge_id, "knowledge_id")
        with self._atomic():
            row = self._fetchone(
                """
                UPDATE celestin_evidence_records
                   SET verification_status = %s, checked_at = %s,
                       checked_by = %s, check_note = %s
                 WHERE workspace_id = %s AND project_id = %s AND knowledge_id = %s
                   AND evidence_id = %s
                 RETURNING evidence_id
                """,
                (
                    evidence.verification_status.value,
                    _postgres_timestamp(evidence.checked_at) if evidence.checked_at else None,
                    evidence.checked_by,
                    evidence.check_note,
                    self._scope.workspace_id,
                    self._scope.project_id,
                    knowledge_id,
                    evidence.evidence_id,
                ),
            )
            if row is None:
                raise EvidenceNotAssociated(evidence.evidence_id)

    def supersede_knowledge(
        self,
        project: str,
        previous_id: str,
        successor: KnowledgeRecord,
        evidence: Sequence[EvidenceRecord],
    ) -> None:
        project = self._require_write_project(project)
        previous_id = _required_text(previous_id, "previous_id")
        _validate_record_project(successor, project)
        if successor.supersedes_id != previous_id:
            raise InvalidKnowledgeTransition("successor does not name the previous record")
        evidence_rows = tuple(evidence)
        for item in evidence_rows:
            _validate_evidence_project(item, project)

        with self._atomic():
            previous = self._fetchone(
                """
                SELECT lifecycle_status
                  FROM celestin_knowledge_records
                 WHERE workspace_id = %s AND project_id = %s AND knowledge_id = %s
                 FOR UPDATE
                """,
                (self._scope.workspace_id, self._scope.project_id, previous_id),
            )
            if previous is None:
                raise KnowledgeNotFound(previous_id)
            if previous[0] != LifecycleStatus.CURRENT.value:
                raise InvalidKnowledgeTransition("only a current record can be superseded")

            self._insert_knowledge(successor)
            for position, item in enumerate(evidence_rows):
                self._insert_evidence(item, successor.knowledge_id, position)
            updated = self._fetchone(
                """
                UPDATE celestin_knowledge_records
                   SET lifecycle_status = %s, superseded_by_id = %s, updated_at = %s
                 WHERE workspace_id = %s AND project_id = %s AND knowledge_id = %s
                   AND lifecycle_status = %s
                 RETURNING knowledge_id
                """,
                (
                    LifecycleStatus.SUPERSEDED.value,
                    successor.knowledge_id,
                    _postgres_timestamp(successor.created_at),
                    self._scope.workspace_id,
                    self._scope.project_id,
                    previous_id,
                    LifecycleStatus.CURRENT.value,
                ),
            )
            if updated is None:
                raise InvalidKnowledgeTransition("previous record is no longer current")

    @contextmanager
    def _atomic(self) -> Iterator[None]:
        # In an authorized request this is a savepoint; outer commit/rollback
        # remains owned by PostgresHostedAuthorizationSessionFactory.
        with self._connection.transaction():
            yield

    def _require_read_project(self, project: str) -> str:
        project = _required_text(project, "project")
        if (
            project != self._scope.project_key
            or not self._scope.permissions.intersection({"read", "memory:read"})
        ):
            raise HostedAccessDenied("access denied")
        return project

    def _require_write_project(self, project: str) -> str:
        project = _required_text(project, "project")
        if project != self._scope.project_key or "write" not in self._scope.permissions:
            raise HostedAccessDenied("access denied")
        return project

    def _insert_knowledge(self, record: KnowledgeRecord) -> None:
        self._execute(
            """
            INSERT INTO celestin_knowledge_records
                (workspace_id, project_id, knowledge_id, knowledge_type, claim,
                 created_at, updated_at, verification_status, lifecycle_status,
                 supersedes_id, superseded_by_id, checked_at, checked_by, check_note,
                 capture_provenance)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            """,
            (
                self._scope.workspace_id,
                self._scope.project_id,
                _required_text(record.knowledge_id, "knowledge_id"),
                record.knowledge_type.value,
                record.claim,
                _postgres_timestamp(record.created_at),
                _postgres_timestamp(record.updated_at),
                record.verification_status.value,
                record.lifecycle_status.value,
                record.supersedes_id,
                record.superseded_by_id,
                _postgres_timestamp(record.checked_at) if record.checked_at else None,
                record.checked_by,
                record.check_note,
                _provenance_json(record.capture_provenance),
            ),
        )

    def _insert_evidence(
        self, evidence: EvidenceRecord, knowledge_id: str, position: int
    ) -> None:
        self._execute(
            """
            INSERT INTO celestin_evidence_records
                (workspace_id, project_id, knowledge_id, evidence_id, evidence_position,
                 evidence_type, source_id, locator, revision, content_hash, captured_at,
                 reference_status, verification_status, checked_at, checked_by, check_note)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                self._scope.workspace_id,
                self._scope.project_id,
                _required_text(knowledge_id, "knowledge_id"),
                _required_text(evidence.evidence_id, "evidence_id"),
                position,
                evidence.evidence_type.value,
                evidence.source_id,
                evidence.locator,
                evidence.revision,
                evidence.content_hash,
                _postgres_timestamp(evidence.captured_at),
                evidence.reference_status.value,
                evidence.verification_status.value,
                _postgres_timestamp(evidence.checked_at) if evidence.checked_at else None,
                evidence.checked_by,
                evidence.check_note,
            ),
        )

    def _execute(self, query: str, params: tuple[Any, ...]) -> None:
        cursor = self._connection.execute(query, params)
        _close_cursor(cursor)

    def _fetchone(self, query: str, params: tuple[Any, ...]) -> Any:
        cursor = self._connection.execute(query, params)
        try:
            return cursor.fetchone()
        finally:
            _close_cursor(cursor)

    def _fetchall(self, query: str, params: tuple[Any, ...]) -> list[Any]:
        cursor = self._connection.execute(query, params)
        try:
            return list(cursor.fetchall())
        finally:
            _close_cursor(cursor)


def _close_cursor(cursor: Any) -> None:
    close = getattr(cursor, "close", None)
    if callable(close):
        close()


def _validate_record_project(record: KnowledgeRecord, project: str) -> None:
    if not isinstance(record, KnowledgeRecord) or record.project != project:
        raise HostedAccessDenied("access denied")


def _validate_evidence_project(evidence: EvidenceRecord, project: str) -> None:
    if not isinstance(evidence, EvidenceRecord) or evidence.project != project:
        raise HostedAccessDenied("access denied")


def _required_text(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


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


def _provenance_from_json(value: Any) -> CaptureKnowledgeProvenance | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise RuntimeError("stored capture provenance is invalid")
    try:
        return CaptureKnowledgeProvenance(
            candidate_id=value["candidate_id"],
            project_id=value["project_id"],
            category=value["category"],
            certainty=value["certainty"],
            review_status=value["review_status"],
            candidate_verification_status=value["candidate_verification_status"],
            source_references=tuple(
                CaptureSourceProvenance(**item)
                for item in value["source_references"]
            ),
            candidate_created_at=value["candidate_created_at"],
            candidate_updated_at=value["candidate_updated_at"],
            original_claim=value["original_claim"],
            reviewed_by=value["reviewed_by"],
            reviewed_at=value["reviewed_at"],
            review_note=value["review_note"],
            conflicts_with=tuple(value["conflicts_with"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("stored capture provenance is invalid") from exc


def _knowledge_from_row(project: str, row: tuple[Any, ...]) -> KnowledgeRecord:
    (
        knowledge_id,
        knowledge_type,
        claim,
        created_at,
        updated_at,
        verification_status,
        lifecycle_status,
        supersedes_id,
        superseded_by_id,
        checked_at,
        checked_by,
        check_note,
        capture_provenance,
    ) = row
    try:
        return KnowledgeRecord(
            knowledge_id=str(knowledge_id),
            project=project,
            knowledge_type=KnowledgeType(knowledge_type),
            claim=claim,
            created_at=_utc_iso(created_at),
            updated_at=_utc_iso(updated_at),
            verification_status=VerificationStatus(verification_status),
            lifecycle_status=LifecycleStatus(lifecycle_status),
            supersedes_id=supersedes_id,
            superseded_by_id=superseded_by_id,
            checked_at=_utc_iso(checked_at) if checked_at is not None else None,
            checked_by=checked_by,
            check_note=check_note,
            capture_provenance=_provenance_from_json(capture_provenance),
        )
    except ValueError as exc:
        raise RuntimeError("stored knowledge record is invalid") from exc


def _evidence_from_row(project: str, row: tuple[Any, ...]) -> EvidenceRecord:
    (
        evidence_id,
        evidence_type,
        source_id,
        locator,
        revision,
        content_hash,
        captured_at,
        reference_status,
        verification_status,
        checked_at,
        checked_by,
        check_note,
    ) = row
    try:
        return EvidenceRecord(
            evidence_id=str(evidence_id),
            project=project,
            evidence_type=EvidenceType(evidence_type),
            source_id=source_id,
            locator=locator,
            revision=revision,
            content_hash=content_hash,
            captured_at=_utc_iso(captured_at),
            reference_status=ReferenceStatus(reference_status),
            verification_status=VerificationStatus(verification_status),
            checked_at=_utc_iso(checked_at) if checked_at is not None else None,
            checked_by=checked_by,
            check_note=check_note,
        )
    except ValueError as exc:
        raise RuntimeError("stored evidence record is invalid") from exc


def _utc_iso(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("PostgreSQL timestamp must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


_UTC_MICROSECONDS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")


def _postgres_timestamp(value: str) -> datetime:
    """Parse the v0.3 persisted timestamp form without losing precision."""
    if not isinstance(value, str) or _UTC_MICROSECONDS.fullmatch(value) is None:
        raise ValueError("timestamp must be RFC 3339 UTC with six fractional digits")
    parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    return parsed.astimezone(timezone.utc)
