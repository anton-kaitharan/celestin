"""Deterministic extraction and a project-scoped, in-memory review queue.

This prototype processes only explicit labeled statements or structured events.
It has no persistence, authorization, MCP, or shared-memory integration.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import replace
from datetime import datetime, timezone
from typing import Callable, Protocol, Sequence

from orbit.evidence.model import (
    CaptureKnowledgeProvenance,
    CaptureSourceProvenance,
    EvidenceSource,
    EvidenceType,
    KnowledgeType,
    VerificationStatus,
)

from .model import (
    CandidateCategory,
    CandidateCertainty,
    CaptureEvent,
    CaptureInput,
    CaptureOccurrence,
    CaptureResult,
    CaptureSourceReference,
    CaptureSourceType,
    KnowledgeCandidate,
    ReviewStatus,
    SuppressedCapture,
)


MAX_INPUT_CHARS = 100_000
MAX_EVENTS = 200
MAX_CLAIM_CHARS = 2_000
MAX_REFERENCE_CHARS = 1_024

_LABEL_CATEGORIES = {
    "decision": CandidateCategory.DECISION,
    "discovery": CandidateCategory.DISCOVERY,
    "bug fix": CandidateCategory.BUG_FIX,
    "bugfix": CandidateCategory.BUG_FIX,
    "completed": CandidateCategory.COMPLETED_WORK,
    "completed work": CandidateCategory.COMPLETED_WORK,
    "incomplete": CandidateCategory.INCOMPLETE_WORK,
    "incomplete work": CandidateCategory.INCOMPLETE_WORK,
    "blocker": CandidateCategory.BLOCKER,
    "constraint": CandidateCategory.CONSTRAINT,
    "requirement": CandidateCategory.CONSTRAINT,
    "implementation detail": CandidateCategory.IMPLEMENTATION_DETAIL,
}
_LABELED_LINE = re.compile(
    r"^\s*(?:[-*+]\s*)?(?P<label>[a-z][a-z ]*?)"
    r"(?:\s*\[(?P<key>[^\]]+)\])?\s*:\s*(?P<claim>\S.*)$",
    re.IGNORECASE,
)
_UNCERTAINTY = re.compile(
    r"\b(?:maybe|perhaps|possibly|might|may|could|uncertain|not sure|"
    r"unverified|apparently|seems?)\b",
    re.IGNORECASE,
)
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


class CaptureError(RuntimeError):
    """Base class for input, queue, and review errors."""


class CaptureValidationError(CaptureError, ValueError):
    """Input or review data is malformed or outside its project scope."""


class CandidateNotFound(CaptureError):
    """The candidate is absent from this project's review repository."""


class InvalidReviewTransition(CaptureError):
    """The requested review transition is not allowed."""


class SensitiveContentRejected(CaptureError):
    """A reviewer edit contains secret-like text."""


class CaptureRepositoryError(CaptureError):
    """The candidate repository did not complete the requested operation."""


class CaptureCandidateRepository(Protocol):
    """Minimal project-keyed repository contract for review candidates."""

    def get(self, project_id: str, candidate_id: str) -> KnowledgeCandidate | None: ...

    def list(
        self, project_id: str, status: ReviewStatus | None = None
    ) -> Sequence[KnowledgeCandidate]: ...

    def put(self, candidate: KnowledgeCandidate) -> None: ...


class InMemoryCaptureRepository:
    """Ephemeral prototype repository; state lasts only for this Python process."""

    def __init__(self) -> None:
        self._items: dict[tuple[str, str], KnowledgeCandidate] = {}

    def get(self, project_id: str, candidate_id: str) -> KnowledgeCandidate | None:
        return self._items.get((project_id, candidate_id))

    def list(
        self, project_id: str, status: ReviewStatus | None = None
    ) -> tuple[KnowledgeCandidate, ...]:
        return tuple(
            sorted(
                (
                    candidate
                    for (scope, _), candidate in self._items.items()
                    if scope == project_id and (status is None or candidate.status is status)
                ),
                key=lambda candidate: (candidate.created_at, candidate.candidate_id),
            )
        )

    def put(self, candidate: KnowledgeCandidate) -> None:
        self._items[(candidate.project_id, candidate.candidate_id)] = candidate


class KnowledgeCapturePipeline:
    """Capture explicit summaries into a review queue bound to one project.

    The project binding prevents accidental cross-project use inside this
    prototype. It is not authentication or authorization; an application caller
    must obtain trusted project scope before invoking this service.
    """

    def __init__(
        self,
        project_id: str,
        repository: CaptureCandidateRepository | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._project_id = _required_text(project_id, "project_id", MAX_REFERENCE_CHARS)
        self._repository = repository if repository is not None else InMemoryCaptureRepository()
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def capture(self, request: CaptureInput) -> CaptureResult:
        source = self._validate_input(request)
        extracted = self._extract(request, source)
        accepted: list[KnowledgeCandidate] = []
        occurrences: list[CaptureOccurrence] = []
        suppressed: list[SuppressedCapture] = []
        duplicate_count = 0

        for event, reference in extracted:
            occurrence_evidence = (*request.evidence, *event.evidence)
            evidence = _unique_evidence(occurrence_evidence)
            if _contains_secret(event.claim) or any(_evidence_has_secret(item) for item in evidence):
                suppressed.append(SuppressedCapture(event.category, reference, "secret_like_content"))
                continue

            claim = _required_text(event.claim, "claim", MAX_CLAIM_CHARS)
            certainty = (
                CandidateCertainty.UNCERTAIN
                if _UNCERTAINTY.search(claim)
                else CandidateCertainty.UNQUALIFIED
            )
            identity = _candidate_identity(
                self._project_id, event.category, claim, event.conflict_key
            )
            candidate = KnowledgeCandidate(
                candidate_id=identity,
                project_id=self._project_id,
                category=event.category,
                claim=claim,
                source_references=(reference,),
                evidence=evidence,
                status=ReviewStatus.PENDING,
                certainty=certainty,
                verification_status=VerificationStatus.UNVERIFIED,
                created_at=self._now(),
                updated_at=self._now(),
                conflict_key=_normalize_key(event.conflict_key),
            )

            previous = self._get(candidate.candidate_id)
            if previous is not None:
                source_references = _unique_sources((*previous.source_references, reference))
                merged_evidence = _unique_evidence((*previous.evidence, *evidence))
                merged_certainty = (
                    CandidateCertainty.UNCERTAIN
                    if CandidateCertainty.UNCERTAIN in (previous.certainty, certainty)
                    else CandidateCertainty.UNQUALIFIED
                )
                conflict_key = previous.conflict_key or candidate.conflict_key
                changed = (
                    candidate.claim != previous.claim
                    or source_references != previous.source_references
                    or merged_evidence != previous.evidence
                    or merged_certainty is not previous.certainty
                    or conflict_key != previous.conflict_key
                )
                if previous.status in {ReviewStatus.APPROVED, ReviewStatus.REJECTED}:
                    duplicate_count += 1
                    if changed:
                        suppressed.append(
                            SuppressedCapture(
                                event.category,
                                reference,
                                "terminal_candidate_immutable",
                            )
                        )
                    else:
                        accepted.append(previous)
                        occurrences.append(
                            CaptureOccurrence(
                                ordinal=len(occurrences),
                                candidate_id=previous.candidate_id,
                                source_reference=reference,
                                evidence_sources=occurrence_evidence,
                            )
                        )
                    continue
                candidate = replace(
                    previous,
                    source_references=source_references,
                    evidence=merged_evidence,
                    certainty=merged_certainty,
                    conflict_key=conflict_key,
                    updated_at=self._now() if changed else previous.updated_at,
                )
                duplicate_count += 1
            candidate = self._link_conflicts(candidate)
            accepted.append(candidate)
            occurrences.append(
                CaptureOccurrence(
                    ordinal=len(occurrences),
                    candidate_id=candidate.candidate_id,
                    source_reference=reference,
                    evidence_sources=occurrence_evidence,
                )
            )

        # Later events in this batch can merge source references or add conflict
        # links to candidates already returned above. Return the final queue
        # state for each accepted identity so the result is internally consistent.
        if accepted:
            saved_candidates = {item.candidate_id: item for item in self._list()}
            try:
                accepted = [saved_candidates[item.candidate_id] for item in accepted]
            except KeyError as exc:
                raise CaptureRepositoryError(
                    "review repository omitted a candidate accepted in this batch"
                ) from exc
        return CaptureResult(
            tuple(accepted), tuple(suppressed), duplicate_count, tuple(occurrences)
        )

    def list_candidates(
        self, project_id: str, status: ReviewStatus | None = None
    ) -> tuple[KnowledgeCandidate, ...]:
        project = self._require_project(project_id)
        if status is not None and not isinstance(status, ReviewStatus):
            raise CaptureValidationError("status must be a ReviewStatus")
        try:
            candidates = tuple(self._repository.list(project, status))
        except Exception as exc:
            raise CaptureRepositoryError("could not list review candidates") from exc
        if any(candidate.project_id != project for candidate in candidates):
            raise CaptureRepositoryError("repository returned a candidate outside the requested project")
        return candidates

    def get_candidate(self, project_id: str, candidate_id: str) -> KnowledgeCandidate:
        project = self._require_project(project_id)
        candidate_id = _required_text(candidate_id, "candidate_id", MAX_REFERENCE_CHARS)
        try:
            candidate = self._repository.get(project, candidate_id)
        except Exception as exc:
            raise CaptureRepositoryError("could not read review candidate") from exc
        if candidate is None:
            raise CandidateNotFound("candidate was not found in this project")
        if candidate.project_id != project:
            raise CaptureRepositoryError("repository returned a candidate outside the requested project")
        return candidate

    def edit_candidate(
        self,
        project_id: str,
        candidate_id: str,
        claim: str,
        *,
        reviewer_id: str,
        note: str | None = None,
    ) -> KnowledgeCandidate:
        candidate = self.get_candidate(project_id, candidate_id)
        self._require_editable(candidate)
        claim = _required_text(claim, "claim", MAX_CLAIM_CHARS)
        if _contains_secret(claim):
            raise SensitiveContentRejected("edited claim contains secret-like content")
        duplicate = next(
            (
                item
                for item in self._list()
                if item.candidate_id != candidate.candidate_id
                and item.category is candidate.category
                and item.conflict_key == candidate.conflict_key
                and _normalize_claim(item.claim) == _normalize_claim(claim)
            ),
            None,
        )
        if duplicate is not None:
            raise CaptureValidationError("edited claim duplicates another review candidate")
        updated = replace(
            candidate,
            claim=claim,
            original_claim=candidate.original_claim or candidate.claim,
            reviewed_by=_reviewer(reviewer_id),
            reviewed_at=self._now(),
            review_note=_review_note(note),
            updated_at=self._now(),
        )
        return self._save_review_update(updated)

    def approve(
        self,
        project_id: str,
        candidate_id: str,
        *,
        reviewer_id: str,
        note: str | None = None,
    ) -> KnowledgeCandidate:
        return self._review(
            project_id, candidate_id, ReviewStatus.APPROVED, reviewer_id, note
        )

    def reject(
        self,
        project_id: str,
        candidate_id: str,
        *,
        reviewer_id: str,
        note: str | None = None,
    ) -> KnowledgeCandidate:
        return self._review(
            project_id, candidate_id, ReviewStatus.REJECTED, reviewer_id, note
        )

    def request_evidence(
        self,
        project_id: str,
        candidate_id: str,
        *,
        reviewer_id: str,
        note: str | None = None,
    ) -> KnowledgeCandidate:
        return self._review(
            project_id, candidate_id, ReviewStatus.NEEDS_EVIDENCE, reviewer_id, note
        )

    def _review(
        self,
        project_id: str,
        candidate_id: str,
        status: ReviewStatus,
        reviewer_id: str,
        note: str | None,
    ) -> KnowledgeCandidate:
        candidate = self.get_candidate(project_id, candidate_id)
        if candidate.status not in {ReviewStatus.PENDING, ReviewStatus.NEEDS_EVIDENCE}:
            raise InvalidReviewTransition("only pending or needs-evidence candidates can be reviewed")
        updated = replace(
            candidate,
            status=status,
            reviewed_by=_reviewer(reviewer_id),
            reviewed_at=self._now(),
            review_note=_review_note(note),
            updated_at=self._now(),
        )
        return self._save_review_update(updated)

    def _validate_input(self, request: CaptureInput) -> CaptureSourceReference:
        if not isinstance(request, CaptureInput):
            raise CaptureValidationError("request must be a CaptureInput")
        project = _required_text(request.project_id, "project_id", MAX_REFERENCE_CHARS)
        if project != self._project_id:
            raise CaptureValidationError("input project does not match this review queue")
        if not isinstance(request.source_type, CaptureSourceType):
            raise CaptureValidationError("source_type must be a CaptureSourceType")
        source_id = _safe_reference(request.source_id, "source_id")
        timestamp = _optional_text(request.source_timestamp, "source_timestamp")
        if timestamp is not None:
            _validate_timestamp(timestamp)
        revision = _safe_reference(request.source_revision, "source_revision", optional=True)
        if not isinstance(request.evidence, tuple):
            raise CaptureValidationError("evidence must be a tuple of EvidenceSource values")
        for item in request.evidence:
            _validate_evidence_source(item)
        if isinstance(request.content, str):
            if not request.content.strip():
                raise CaptureValidationError("content must not be empty")
            if len(request.content) > MAX_INPUT_CHARS:
                raise CaptureValidationError(f"content exceeds {MAX_INPUT_CHARS} characters")
        elif isinstance(request.content, tuple):
            if not request.content:
                raise CaptureValidationError("structured content must include at least one event")
            if len(request.content) > MAX_EVENTS:
                raise CaptureValidationError(f"structured content exceeds {MAX_EVENTS} events")
            total = 0
            for event in request.content:
                if not isinstance(event, CaptureEvent):
                    raise CaptureValidationError("structured content must contain CaptureEvent values")
                if not isinstance(event.category, CandidateCategory):
                    raise CaptureValidationError("event category must be a CandidateCategory")
                _required_text(event.claim, "event claim", MAX_CLAIM_CHARS)
                _safe_reference(event.conflict_key, "conflict_key", optional=True)
                _safe_reference(event.locator, "event locator", optional=True)
                if not isinstance(event.evidence, tuple):
                    raise CaptureValidationError("event evidence must be a tuple of EvidenceSource values")
                for item in event.evidence:
                    _validate_evidence_source(item)
                total += len(event.claim)
            if total > MAX_INPUT_CHARS:
                raise CaptureValidationError(f"structured content exceeds {MAX_INPUT_CHARS} characters")
        else:
            raise CaptureValidationError("content must be text or a tuple of CaptureEvent values")
        return CaptureSourceReference(
            request.source_type,
            source_id,
            timestamp,
            revision,
        )

    def _extract(
        self, request: CaptureInput, source: CaptureSourceReference
    ) -> tuple[tuple[CaptureEvent, CaptureSourceReference], ...]:
        if isinstance(request.content, tuple):
            return tuple(
                (
                    event,
                    replace(
                        source,
                        locator=event.locator or f"event:{index}",
                    ),
                )
                for index, event in enumerate(request.content, start=1)
            )
        extracted: list[tuple[CaptureEvent, CaptureSourceReference]] = []
        for line_number, line in enumerate(request.content.splitlines(), start=1):
            match = _LABELED_LINE.match(line)
            if match is None:
                continue
            category = _LABEL_CATEGORIES.get(" ".join(match.group("label").lower().split()))
            if category is None:
                continue
            conflict_key = _safe_reference(match.group("key"), "conflict_key", optional=True)
            event = CaptureEvent(
                category=category,
                claim=match.group("claim").strip(),
                evidence=(),
                conflict_key=conflict_key,
            )
            extracted.append((event, replace(source, locator=f"line:{line_number}")))
            if len(extracted) > MAX_EVENTS:
                raise CaptureValidationError(f"input contains more than {MAX_EVENTS} labeled candidates")
        return tuple(extracted)

    def _link_conflicts(self, candidate: KnowledgeCandidate) -> KnowledgeCandidate:
        # Review transitions may save a terminal candidate, but conflict
        # discovery must not rewrite its reviewed relationship history.
        if candidate.status in {ReviewStatus.APPROVED, ReviewStatus.REJECTED}:
            self._put(candidate)
            return candidate
        if not candidate.conflict_key:
            self._put(candidate)
            return candidate
        candidates = {item.candidate_id: item for item in self._list()}
        candidates[candidate.candidate_id] = candidate
        group = [
            item
            for item in candidates.values()
            if item.category is candidate.category and item.conflict_key == candidate.conflict_key
        ]
        updated_group: dict[str, KnowledgeCandidate] = {}
        for item in group:
            conflicts = tuple(
                sorted(
                    other.candidate_id
                    for other in group
                    if other.candidate_id != item.candidate_id
                    and _normalize_claim(other.claim) != _normalize_claim(item.claim)
                )
            )
            if item.status in {ReviewStatus.APPROVED, ReviewStatus.REJECTED}:
                # The new candidate records the conflict. The terminal record
                # remains byte-for-byte stable after its review decision.
                updated_group[item.candidate_id] = item
                continue
            updated = replace(
                item,
                conflicts_with=conflicts,
                updated_at=self._now() if conflicts != item.conflicts_with else item.updated_at,
            )
            updated_group[updated.candidate_id] = updated
        for item in updated_group.values():
            if item.status in {ReviewStatus.APPROVED, ReviewStatus.REJECTED}:
                continue
            self._put(item)
        return updated_group[candidate.candidate_id]

    def _require_project(self, project_id: str) -> str:
        project = _required_text(project_id, "project_id", MAX_REFERENCE_CHARS)
        if project != self._project_id:
            raise CaptureValidationError("project is outside this review queue scope")
        return project

    def _require_editable(self, candidate: KnowledgeCandidate) -> None:
        if candidate.status not in {ReviewStatus.PENDING, ReviewStatus.NEEDS_EVIDENCE}:
            raise InvalidReviewTransition("only pending or needs-evidence candidates can be edited")

    def _get(self, candidate_id: str) -> KnowledgeCandidate | None:
        try:
            candidate = self._repository.get(self._project_id, candidate_id)
        except Exception as exc:
            raise CaptureRepositoryError("could not read review candidate") from exc
        if candidate is not None and candidate.project_id != self._project_id:
            raise CaptureRepositoryError("repository returned a candidate outside the requested project")
        return candidate

    def _list(self) -> tuple[KnowledgeCandidate, ...]:
        try:
            candidates = tuple(self._repository.list(self._project_id))
        except Exception as exc:
            raise CaptureRepositoryError("could not list review candidates") from exc
        if any(candidate.project_id != self._project_id for candidate in candidates):
            raise CaptureRepositoryError("repository returned a candidate outside the requested project")
        return candidates

    def _put(self, candidate: KnowledgeCandidate) -> None:
        if candidate.project_id != self._project_id:
            raise CaptureValidationError("candidate is outside this review queue scope")
        try:
            self._repository.put(candidate)
        except Exception as exc:
            raise CaptureRepositoryError("could not save review candidate") from exc

    def _save_review_update(self, candidate: KnowledgeCandidate) -> KnowledgeCandidate:
        return self._link_conflicts(candidate) if candidate.conflict_key else self._save(candidate)

    def _save(self, candidate: KnowledgeCandidate) -> KnowledgeCandidate:
        self._put(candidate)
        return candidate

    def _now(self) -> str:
        value = self._clock()
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise CaptureValidationError("clock must return a timezone-aware datetime")
        return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
            "+00:00", "Z"
        )


def evidence_sources_for(
    candidate: KnowledgeCandidate, *, target_project: str
) -> tuple[EvidenceSource, ...]:
    """Map an approved candidate's origins for the matching project, without saving.

    The project match is a domain association guard, not authentication. Callers
    must still obtain trusted project scope from the application's authorization
    boundary before writing the returned sources.
    """
    _require_approved_candidate(candidate, target_project)
    origins = tuple(
        EvidenceSource(
            EvidenceType.OTHER,
            reference.source_id,
            locator=": ".join(
                part for part in (reference.source_type.value, reference.locator) if part
            ),
            revision=reference.source_revision,
        )
        for reference in candidate.source_references
    )
    return _unique_evidence((*origins, *candidate.evidence))


def capture_provenance_for(
    candidate: KnowledgeCandidate, *, target_project: str
) -> CaptureKnowledgeProvenance:
    """Create the storage-neutral provenance envelope for an approved candidate.

    The envelope retains review and source metadata alongside the claim. Its
    actor labels remain caller-supplied provenance, not authenticated identity.
    """
    _require_approved_candidate(candidate, target_project)
    return CaptureKnowledgeProvenance(
        candidate_id=candidate.candidate_id,
        project_id=candidate.project_id,
        category=candidate.category.value,
        certainty=candidate.certainty.value,
        review_status=candidate.status.value,
        candidate_verification_status=candidate.verification_status.value,
        source_references=tuple(
            CaptureSourceProvenance(
                source_type=reference.source_type.value,
                source_id=reference.source_id,
                occurred_at=reference.source_timestamp,
                revision=reference.source_revision,
                locator=reference.locator,
            )
            for reference in candidate.source_references
        ),
        candidate_created_at=candidate.created_at,
        candidate_updated_at=candidate.updated_at,
        original_claim=candidate.original_claim,
        reviewed_by=candidate.reviewed_by,
        reviewed_at=candidate.reviewed_at,
        review_note=candidate.review_note,
        conflicts_with=candidate.conflicts_with,
    )


def knowledge_type_for_candidate(candidate: KnowledgeCandidate) -> KnowledgeType:
    """Map only candidate categories with an equivalent Capability 1 type."""
    mapping = {
        CandidateCategory.DECISION: KnowledgeType.DECISION,
        CandidateCategory.DISCOVERY: KnowledgeType.DISCOVERY,
        CandidateCategory.BUG_FIX: KnowledgeType.BUG_FIX,
        CandidateCategory.COMPLETED_WORK: KnowledgeType.COMPLETED_WORK,
        CandidateCategory.CONSTRAINT: KnowledgeType.REQUIREMENT,
        CandidateCategory.IMPLEMENTATION_DETAIL: KnowledgeType.IMPLEMENTATION_FINDING,
    }
    if not isinstance(candidate, KnowledgeCandidate):
        raise CaptureValidationError("candidate must be a KnowledgeCandidate")
    if not isinstance(candidate.category, CandidateCategory):
        raise CaptureValidationError("candidate category must be a CandidateCategory")
    try:
        return mapping[candidate.category]
    except KeyError as exc:
        raise CaptureValidationError(
            f"candidate category {candidate.category.value!r} has no agreed Capability 1 knowledge type"
        ) from exc


def _require_approved_candidate(
    candidate: KnowledgeCandidate, target_project: str
) -> None:
    target_project = _required_text(
        target_project, "target_project", MAX_REFERENCE_CHARS
    )
    if not isinstance(candidate, KnowledgeCandidate):
        raise CaptureValidationError("candidate must be a KnowledgeCandidate")
    if candidate.project_id != target_project:
        raise CaptureValidationError("candidate project does not match target project")
    if candidate.status is not ReviewStatus.APPROVED:
        raise InvalidReviewTransition("only approved candidates can be mapped to knowledge")
    if candidate.verification_status is not VerificationStatus.UNVERIFIED:
        raise CaptureValidationError(
            "candidate verification metadata is unsupported; conversion requires unverified status"
        )


def _candidate_identity(
    project_id: str,
    category: CandidateCategory,
    claim: str,
    conflict_key: str | None,
) -> str:
    raw = "\0".join(
        (project_id, category.value, _normalize_claim(claim), _normalize_key(conflict_key) or "")
    )
    return "capture-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _normalize_claim(claim: str) -> str:
    normalized = unicodedata.normalize("NFKC", claim).casefold()
    return " ".join(normalized.split())


def _normalize_key(key: str | None) -> str | None:
    return _normalize_claim(key) if key is not None else None


def _unique_sources(
    sources: Sequence[CaptureSourceReference],
) -> tuple[CaptureSourceReference, ...]:
    return tuple(sorted(set(sources), key=lambda item: (
        item.source_type.value,
        item.source_id,
        item.source_revision or "",
        item.locator or "",
        item.source_timestamp or "",
    )))


def _unique_evidence(sources: Sequence[EvidenceSource]) -> tuple[EvidenceSource, ...]:
    return tuple(sorted(set(sources), key=lambda item: (
        item.evidence_type.value,
        item.source_id,
        item.locator or "",
        item.revision or "",
        item.content_hash or "",
    )))


def _validate_evidence_source(source: EvidenceSource) -> None:
    if not isinstance(source, EvidenceSource) or not isinstance(source.evidence_type, EvidenceType):
        raise CaptureValidationError("evidence entries must be EvidenceSource values")
    _safe_reference(source.source_id, "evidence source_id")
    _safe_reference(source.locator, "evidence locator", optional=True)
    _safe_reference(source.revision, "evidence revision", optional=True)
    _safe_reference(source.content_hash, "evidence content_hash", optional=True)


def _evidence_has_secret(source: EvidenceSource) -> bool:
    return any(
        value is not None and _contains_secret(value)
        for value in (source.source_id, source.locator, source.revision, source.content_hash)
    )


def _contains_secret(value: str) -> bool:
    return any(pattern.search(value) for pattern in _SECRET_PATTERNS)


def _required_text(value: str, field: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise CaptureValidationError(f"{field} must be non-empty text")
    value = value.strip()
    if len(value) > limit:
        raise CaptureValidationError(f"{field} exceeds {limit} characters")
    return value


def _optional_text(value: str | None, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or "\x00" in value:
        raise CaptureValidationError(f"{field} must be text or None")
    value = value.strip()
    if len(value) > MAX_REFERENCE_CHARS:
        raise CaptureValidationError(f"{field} exceeds {MAX_REFERENCE_CHARS} characters")
    return value or None


def _safe_reference(value: str | None, field: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    text = _required_text(value, field, MAX_REFERENCE_CHARS)
    if _contains_secret(text):
        raise CaptureValidationError(f"{field} contains secret-like content")
    return text


def _validate_timestamp(value: str) -> None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CaptureValidationError("source_timestamp must be an ISO 8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise CaptureValidationError("source_timestamp must include a timezone")


def _reviewer(value: str) -> str:
    text = _required_text(value, "reviewer_id", MAX_REFERENCE_CHARS)
    if _contains_secret(text):
        raise SensitiveContentRejected("reviewer_id contains secret-like content")
    return text


def _review_note(value: str | None) -> str | None:
    note = _optional_text(value, "note")
    if note is not None and _contains_secret(note):
        raise SensitiveContentRejected("review note contains secret-like content")
    return note
