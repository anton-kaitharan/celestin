"""Storage-neutral sync service and an explicitly ephemeral prototype adapter."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from datetime import datetime, timezone
from typing import Callable, Protocol, Sequence

from orbit.capture.model import CandidateCategory, CaptureSourceReference, CaptureSourceType
from orbit.evidence.model import EvidenceSource, EvidenceType
from orbit.mcp_server.authorization import (
    AuthenticatedPrincipal,
    AuthorizedProject,
    ProjectAccessDenied,
    ProjectAuthorizer,
    READ,
    WRITE,
)

from .model import (
    CURRENT_SCHEMA_VERSION,
    SyncEvent,
    SyncEventPage,
    SyncEventSubmission,
    SyncEventType,
    SyncPayload,
    SyncReceipt,
)


MAX_SUMMARY_CHARS = 8_000
MAX_IDENTIFIER_CHARS = 256
MAX_REFERENCE_CHARS = 1_024
MAX_REFERENCES = 32
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100
_SECRET_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(?:api[_-]?key|client[_-]?secret|secret[_-]?key|password|passwd|"
        r"access[_-]?token|auth[_-]?token)\b\s*[:=]\s*['\"]?[^\s'\"]{8,}",
        r"\bAKIA[0-9A-Z]{16}\b",
        r"\b(?:aws_)?secret_access_key\b\s*[:=]\s*['\"]?[A-Za-z0-9/+=]{16,}",
        r"\b(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{20,}\b",
        r"\bsk-(?:proj-|live-)?[A-Za-z0-9_-]{20,}\b",
        r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b",
        r"\bAIza[0-9A-Za-z_-]{30,}\b",
        r"\bBearer\s+[A-Za-z0-9._~+/=-]{20,}",
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    )
)


class SyncError(RuntimeError):
    """Base class for synchronization contract errors."""


class SyncValidationError(SyncError, ValueError):
    """An event or page request is malformed or outside its authorized scope."""


class UnsupportedSchemaVersion(SyncValidationError):
    """The receiver does not support the submitted event schema."""


class IdempotencyConflict(SyncError):
    """An idempotency key was reused for different event content."""


class InvalidCursor(SyncValidationError):
    """The cursor is malformed, from another project, or beyond the stream."""


class ExpiredCursor(SyncError):
    """The cursor predates retained events and cannot safely resume delivery."""


class SyncStoreError(SyncError):
    """The event store did not complete the requested operation."""


class SyncEventStore(Protocol):
    """Storage port; append must atomically enforce scoped idempotency and order."""

    def append(self, event: SyncEvent) -> tuple[SyncEvent, bool]: ...

    def read_after(
        self, project_id: str, after_sequence: int, limit: int
    ) -> tuple[Sequence[SyncEvent], bool]: ...


class InMemorySyncEventStore:
    """Process-local adapter for contract tests; no durability or replication."""

    def __init__(self) -> None:
        self._events: dict[str, list[SyncEvent]] = {}
        self._idempotency: dict[tuple[str, str, str], tuple[str, SyncEvent]] = {}
        self._next_sequence: dict[str, int] = {}
        self._first_retained: dict[str, int] = {}

    def append(self, event: SyncEvent) -> tuple[SyncEvent, bool]:
        if event.sequence != 0:
            raise ValueError("new events must not supply a stream sequence")
        key = (event.project_id, event.source_client_id, event.idempotency_key)
        fingerprint = _fingerprint(event)
        previous = self._idempotency.get(key)
        if previous is not None:
            old_fingerprint, old_event = previous
            if old_fingerprint != fingerprint:
                raise IdempotencyConflict("idempotency key already identifies different event content")
            return old_event, True

        sequence = self._next_sequence.get(event.project_id, 1)
        canonical = replace(event, sequence=sequence)
        self._events.setdefault(event.project_id, []).append(canonical)
        self._idempotency[key] = (fingerprint, canonical)
        self._next_sequence[event.project_id] = sequence + 1
        self._first_retained.setdefault(event.project_id, 1)
        return canonical, False

    def read_after(
        self, project_id: str, after_sequence: int, limit: int
    ) -> tuple[Sequence[SyncEvent], bool]:
        first_retained = self._first_retained.get(project_id, 1)
        if after_sequence < first_retained - 1:
            raise ExpiredCursor("cursor is older than the retained event stream")
        latest = self._next_sequence.get(project_id, 1) - 1
        if after_sequence > latest:
            raise InvalidCursor("cursor is ahead of the project event stream")
        remaining = [
            event
            for event in self._events.get(project_id, ())
            if event.sequence > after_sequence
        ]
        return tuple(remaining[:limit]), len(remaining) > limit

    def expire_before(self, project_id: str, first_retained_sequence: int) -> None:
        """Simulate an adapter's retention policy for deterministic contract tests."""
        current_floor = self._first_retained.get(project_id, 1)
        if (
            not isinstance(first_retained_sequence, int)
            or isinstance(first_retained_sequence, bool)
            or first_retained_sequence < current_floor
            or first_retained_sequence > self._next_sequence.get(project_id, 1)
        ):
            raise ValueError("first retained sequence is outside the known stream")
        self._events[project_id] = [
            event
            for event in self._events.get(project_id, ())
            if event.sequence >= first_retained_sequence
        ]
        self._first_retained[project_id] = first_retained_sequence


class ProjectSyncService:
    """Publish and read project events behind a configured project authorizer.

    A supplied scope is a project/action selector, not proof of authorization.
    Each operation is reauthorized using the principal fixed by the trusted
    caller that builds this service.
    """

    def __init__(
        self,
        store: SyncEventStore,
        *,
        authorizer: ProjectAuthorizer,
        principal: AuthenticatedPrincipal | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._authorizer = authorizer
        self._principal = principal
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def publish(self, scope: AuthorizedProject, submission: SyncEventSubmission) -> SyncReceipt:
        project = self._require_scope(scope, WRITE)
        _validate_submission(submission, project)
        received_at = self._now()
        event_id = _event_id(project, submission.source_client_id, submission.idempotency_key)
        proposed = SyncEvent(
            event_id=event_id,
            schema_version=submission.schema_version,
            project_id=project,
            source_client_id=submission.source_client_id,
            idempotency_key=submission.idempotency_key,
            event_type=submission.event_type,
            payload=submission.payload,
            source_occurred_at=submission.source_occurred_at,
            ingested_at=received_at,
            source_references=submission.source_references,
            evidence=submission.evidence,
            correlation_id=submission.correlation_id,
            sequence=0,
        )
        try:
            event, duplicate = self._store.append(proposed)
        except (IdempotencyConflict, SyncValidationError):
            raise
        except Exception as exc:
            raise SyncStoreError("could not publish project event") from exc
        if event.project_id != project:
            raise SyncStoreError("event store returned an event outside the authorized project")
        return SyncReceipt(event=event, duplicate=duplicate)

    def retrieve(
        self,
        scope: AuthorizedProject,
        *,
        cursor: str | None = None,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> SyncEventPage:
        project = self._require_scope(scope, READ)
        if (
            not isinstance(page_size, int)
            or isinstance(page_size, bool)
            or page_size < 1
            or page_size > MAX_PAGE_SIZE
        ):
            raise SyncValidationError(f"page_size must be between 1 and {MAX_PAGE_SIZE}")
        after_sequence = _decode_cursor(cursor, project)
        try:
            events, has_more = self._store.read_after(project, after_sequence, page_size)
        except (InvalidCursor, ExpiredCursor):
            raise
        except Exception as exc:
            raise SyncStoreError("could not retrieve project events") from exc
        if any(event.project_id != project for event in events):
            raise SyncStoreError("event store returned an event outside the authorized project")
        if len(events) > page_size or any(event.sequence <= after_sequence for event in events):
            raise SyncStoreError("event store returned events outside the requested page")
        if any(a.sequence >= b.sequence for a, b in zip(events, events[1:])):
            raise SyncStoreError("event store returned events outside deterministic sequence order")
        last_sequence = events[-1].sequence if events else after_sequence
        return SyncEventPage(
            events=tuple(events),
            next_cursor=_encode_cursor(project, last_sequence),
            has_more=bool(has_more),
        )

    def _now(self) -> str:
        value = self._clock()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise SyncValidationError("clock must return a timezone-aware datetime")
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    def _require_scope(self, scope: AuthorizedProject, action: str) -> str:
        if not isinstance(scope, AuthorizedProject):
            raise SyncValidationError("a project selector is required")
        if scope.action != action:
            raise SyncValidationError(f"project selector does not request {action}")
        project = _required_text(scope.project, "project", MAX_IDENTIFIER_CHARS)
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


def _validate_submission(submission: SyncEventSubmission, authorized_project: str) -> None:
    if not isinstance(submission, SyncEventSubmission):
        raise SyncValidationError("submission must be a SyncEventSubmission")
    if (
        not isinstance(submission.schema_version, int)
        or isinstance(submission.schema_version, bool)
    ):
        raise SyncValidationError("schema_version must be an integer")
    if submission.schema_version != CURRENT_SCHEMA_VERSION:
        raise UnsupportedSchemaVersion(
            f"unsupported schema_version {submission.schema_version}; supported: {CURRENT_SCHEMA_VERSION}"
        )
    project_id = _required_text(submission.project_id, "project_id", MAX_IDENTIFIER_CHARS)
    if project_id != authorized_project:
        raise SyncValidationError("event project_id does not match the authorized project scope")
    source_client = _required_text(
        submission.source_client_id, "source_client_id", MAX_IDENTIFIER_CHARS
    )
    idempotency_key = _required_text(
        submission.idempotency_key, "idempotency_key", MAX_IDENTIFIER_CHARS
    )
    _reject_secret(source_client)
    _reject_secret(idempotency_key)
    if not isinstance(submission.event_type, SyncEventType):
        raise SyncValidationError("event_type is unsupported")
    if not isinstance(submission.payload, SyncPayload):
        raise SyncValidationError("payload must be a SyncPayload")
    summary = _required_text(submission.payload.summary, "payload.summary", MAX_SUMMARY_CHARS)
    _reject_secret(summary)
    if submission.payload.category is not None and not isinstance(
        submission.payload.category, CandidateCategory
    ):
        raise SyncValidationError("payload.category must be a CandidateCategory or None")
    if submission.source_occurred_at is not None:
        _validate_timestamp(submission.source_occurred_at, "source_occurred_at")
    if submission.correlation_id is not None:
        correlation_id = _required_text(
            submission.correlation_id, "correlation_id", MAX_IDENTIFIER_CHARS
        )
        _reject_secret(correlation_id)
    for name, references, expected_type in (
        ("source_references", submission.source_references, CaptureSourceReference),
        ("evidence", submission.evidence, EvidenceSource),
    ):
        if not isinstance(references, tuple) or len(references) > MAX_REFERENCES:
            raise SyncValidationError(f"{name} must be a tuple of at most {MAX_REFERENCES} references")
        if any(not isinstance(reference, expected_type) for reference in references):
            raise SyncValidationError(f"{name} contains an unsupported reference")
        for reference in references:
            _validate_reference(reference, name)


def _validate_reference(reference: CaptureSourceReference | EvidenceSource, name: str) -> None:
    if isinstance(reference, CaptureSourceReference):
        if not isinstance(reference.source_type, CaptureSourceType):
            raise SyncValidationError(f"{name} contains an invalid capture source type")
    elif not isinstance(reference.evidence_type, EvidenceType):
        raise SyncValidationError(f"{name} contains an invalid evidence type")
    source_id = _required_text(reference.source_id, f"{name}.source_id", MAX_REFERENCE_CHARS)
    _reject_secret(source_id)
    for field in ("locator", "revision", "content_hash", "source_timestamp", "source_revision"):
        value = getattr(reference, field, None)
        if value is not None:
            _required_text(value, f"{name}.{field}", MAX_REFERENCE_CHARS)
            _reject_secret(value)


def _validate_timestamp(value: str, name: str) -> None:
    if not isinstance(value, str) or len(value) > 64:
        raise SyncValidationError(f"{name} must be an ISO 8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SyncValidationError(f"{name} must be an ISO 8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SyncValidationError(f"{name} must include a timezone")


def _required_text(value: str, name: str, max_chars: int) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip() or "\x00" in value:
        raise SyncValidationError(f"{name} must be non-empty text without surrounding whitespace")
    if len(value) > max_chars:
        raise SyncValidationError(f"{name} exceeds {max_chars} characters")
    return value


def _reject_secret(value: str) -> None:
    if any(pattern.search(value) for pattern in _SECRET_PATTERNS):
        raise SyncValidationError("secret-like content is not accepted in sync events")


def _event_id(project: str, source_client: str, key: str) -> str:
    raw = f"{project}\0{source_client}\0{key}".encode("utf-8")
    return "sync-" + hashlib.sha256(raw).hexdigest()[:32]


def _fingerprint(event: SyncEvent) -> str:
    data = {
        "schema_version": event.schema_version,
        "project_id": event.project_id,
        "source_client_id": event.source_client_id,
        "idempotency_key": event.idempotency_key,
        "event_type": event.event_type.value,
        "payload": {
            "summary": event.payload.summary,
            "category": event.payload.category.value if event.payload.category else None,
        },
        "source_occurred_at": event.source_occurred_at,
        "source_references": [_reference_json(item) for item in event.source_references],
        "evidence": [_reference_json(item) for item in event.evidence],
        "correlation_id": event.correlation_id,
    }
    encoded = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _reference_json(reference: CaptureSourceReference | EvidenceSource) -> dict[str, str | None]:
    if isinstance(reference, CaptureSourceReference):
        return {
            "kind": "capture",
            "source_type": reference.source_type.value,
            "source_id": reference.source_id,
            "source_timestamp": reference.source_timestamp,
            "source_revision": reference.source_revision,
            "locator": reference.locator,
        }
    return {
        "kind": "evidence",
        "evidence_type": reference.evidence_type.value,
        "source_id": reference.source_id,
        "locator": reference.locator,
        "revision": reference.revision,
        "content_hash": reference.content_hash,
    }


def _project_cursor_id(project: str) -> str:
    return hashlib.sha256(project.encode("utf-8")).hexdigest()[:16]


def _encode_cursor(project: str, sequence: int) -> str:
    return f"sync1.{_project_cursor_id(project)}.{sequence}"


def _decode_cursor(cursor: str | None, project: str) -> int:
    if cursor is None:
        return 0
    if not isinstance(cursor, str):
        raise InvalidCursor("cursor must be text or None")
    if len(cursor) > 64:
        raise InvalidCursor("cursor is too long")
    parts = cursor.split(".")
    if len(parts) != 3 or parts[0] != "sync1" or parts[1] != _project_cursor_id(project):
        raise InvalidCursor("cursor is malformed or belongs to another project")
    if (
        not parts[2].isdecimal()
        or len(parts[2]) > 20
        or (len(parts[2]) > 1 and parts[2].startswith("0"))
    ):
        raise InvalidCursor("cursor sequence is malformed")
    sequence = int(parts[2])
    return sequence
