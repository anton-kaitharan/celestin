"""Test-only composition harness for Resurrection Layer Capabilities 1–4.

The bridge and repositories below are fixtures, not production adapters. They
make the current contracts executable together while leaving their interfaces
and hosted/persistent implementations unchanged.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from orbit.capture import (
    CandidateCategory,
    CandidateCertainty,
    CaptureEvent,
    CaptureInput,
    CaptureSourceType,
    CaptureValidationError,
    InMemoryCaptureRepository,
    InvalidReviewTransition,
    KnowledgeCapturePipeline,
    ReviewStatus,
    capture_provenance_for,
    evidence_sources_for,
    knowledge_type_for_candidate,
)
from orbit.evidence.model import (
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
from orbit.evidence.service import (
    EvidenceLinkedMemoryService,
    EvidenceReferenceMissing,
)
from orbit.mcp_server.authorization import READ, WRITE, AuthorizedProject, ProjectAccessPolicy
from orbit.project_state import EvidenceClass, ProjectStateReconstructor
from orbit.sync import (
    CURRENT_SCHEMA_VERSION,
    IdempotencyConflict,
    InMemorySyncEventStore,
    ProjectSyncService,
    SyncEventSubmission,
    SyncEventType,
    SyncPayload,
    SyncValidationError,
)


PROJECT = "celestin"
CAPTURED_AT = datetime(2026, 10, 15, 1, 40, tzinfo=timezone.utc)
SOURCE_AT = "2026-10-14T23:10:00Z"


def _scope(project: str, action: str) -> AuthorizedProject:
    policy = ProjectAccessPolicy(local_access={project: [READ, WRITE]})
    return policy.authorize(None, project, action)


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "Integration Test",
            "GIT_AUTHOR_EMAIL": "integration@example.invalid",
            "GIT_COMMITTER_NAME": "Integration Test",
            "GIT_COMMITTER_EMAIL": "integration@example.invalid",
        },
    )
    return result.stdout.strip()


def _repository(tmp_path: Path, *, source_file: str = "src/storage.py") -> tuple[Path, str]:
    root = tmp_path / "sample-project"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Integration Test")
    _git(root, "config", "user.email", "integration@example.invalid")
    path = root / source_file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("BACKEND = 'sqlite'\n", encoding="utf-8")
    _git(root, "add", "--all")
    _git(root, "commit", "-q", "-m", "fixture")
    return root, _git(root, "rev-parse", "HEAD")


class EvidenceResolver:
    def __init__(self, statuses=None, default=ReferenceStatus.UNRESOLVED):
        self.statuses = statuses or {}
        self.default = default
        self.calls = []

    def resolve(self, project: str, source: EvidenceSource) -> ReferenceStatus:
        self.calls.append((project, source))
        return self.statuses.get(
            (project, source.evidence_type, source.source_id), self.default
        )


class MemoryStoreDouble:
    """Small test double implementing Cap1 writes plus a separate Cap2 read view."""

    def __init__(self):
        self._records: dict[tuple[str, str], KnowledgeRecord] = {}
        self._evidence: dict[tuple[str, str], tuple[EvidenceRecord, ...]] = {}

    def create_knowledge(self, record, evidence):
        key = (record.project, record.knowledge_id)
        self._records[key] = record
        self._evidence[key] = tuple(evidence)

    def attach_evidence(self, project, knowledge_id, evidence):
        key = (project, knowledge_id)
        current = list(self._evidence[key])
        identity = _evidence_identity(evidence)
        match = next((item for item in current if _evidence_identity(item) == identity), None)
        if match is not None:
            return match
        current.append(evidence)
        self._evidence[key] = tuple(current)
        return evidence

    def get_knowledge(self, project, knowledge_id):
        return self._records.get((project, knowledge_id))

    def list_evidence(self, project, knowledge_id):
        return self._evidence.get((project, knowledge_id), ())

    def set_knowledge_verification(self, project, knowledge_id, record):
        self._records[(project, knowledge_id)] = record

    def set_evidence_verification(self, project, knowledge_id, evidence):
        key = (project, knowledge_id)
        self._evidence[key] = tuple(
            evidence if item.evidence_id == evidence.evidence_id else item
            for item in self._evidence[key]
        )

    def supersede_knowledge(self, project, previous_id, successor, evidence):
        previous_key = (project, previous_id)
        previous = self._records[previous_key]
        self._records[previous_key] = replace(
            previous,
            lifecycle_status=LifecycleStatus.SUPERSEDED,
            superseded_by_id=successor.knowledge_id,
            updated_at=successor.created_at,
        )
        successor_key = (project, successor.knowledge_id)
        self._records[successor_key] = successor
        self._evidence[successor_key] = tuple(evidence)

    def list_bundles(self, project: str) -> tuple[KnowledgeBundle, ...]:
        """Test-adapter extension; the production Capability 1 port lacks this."""
        return tuple(
            KnowledgeBundle(record, self._evidence[key])
            for key, record in self._records.items()
            if key[0] == project
        )


def _evidence_identity(item: EvidenceRecord):
    return (item.evidence_type, item.source_id, item.locator, item.revision, item.content_hash)


class BoundKnowledgeView:
    """Read-only Capability 2 protocol adapter bound to one project in tests."""

    def __init__(self, store: MemoryStoreDouble, project: str):
        self._store = store
        self._project = project

    def list_records(self):
        return self._store.list_bundles(self._project)


class StaticKnowledgeView:
    def __init__(self, bundles):
        self._bundles = tuple(bundles)

    def list_records(self):
        return self._bundles


def _memory_service(store=None, resolver=None):
    ids = iter(f"knowledge-{index}" for index in range(1, 100))
    return EvidenceLinkedMemoryService(
        store or MemoryStoreDouble(),
        resolver or EvidenceResolver(),
        authorizer=ProjectAccessPolicy(local_access={PROJECT: [READ, WRITE]}),
        id_factory=lambda: next(ids),
        clock=lambda: CAPTURED_AT,
    )


def _capture_pipeline(project=PROJECT):
    return KnowledgeCapturePipeline(
        project,
        InMemoryCaptureRepository(),
        clock=lambda: CAPTURED_AT,
    )


def _candidate(
    pipeline: KnowledgeCapturePipeline,
    revision: str,
    claim: str,
    *,
    evidence: tuple[EvidenceSource, ...] = (),
    conflict_key: str | None = None,
    source_id: str = "conversation-42",
):
    event = CaptureEvent(
        CandidateCategory.DECISION,
        claim,
        evidence=evidence,
        conflict_key=conflict_key,
        locator="summary:decision-1",
    )
    result = pipeline.capture(
        CaptureInput(
            project_id=PROJECT,
            source_type=CaptureSourceType.CONVERSATION_SUMMARY,
            source_id=source_id,
            source_timestamp=SOURCE_AT,
            source_revision=revision,
            content=(event,),
        )
    )
    assert len(result.candidates) == 1
    return result.candidates[0]


def _persist_approved_decision(memory, scope, candidate):
    """Test-only C3→C1 bridge; no production adapter currently exists."""
    if candidate.category is not CandidateCategory.DECISION:
        raise ValueError("test bridge maps decisions only")
    sources = evidence_sources_for(candidate, target_project=scope.project)
    return memory.create_knowledge(
        scope,
        knowledge_type_for_candidate(candidate),
        candidate.claim,
        sources,
        capture_provenance=capture_provenance_for(
            candidate, target_project=scope.project
        ),
    )


def test_approved_uncertain_candidate_flows_through_capabilities_1_2_and_4(tmp_path):
    root, revision = _repository(tmp_path)
    source = EvidenceSource(
        EvidenceType.REPOSITORY_FILE,
        "src/storage.py",
        revision=revision,
    )
    capture = _capture_pipeline()
    pending = _candidate(
        capture,
        revision,
        "Maybe keep SQLite for local persistence until the hosted migration is approved.",
        evidence=(source,),
    )
    assert pending.status is ReviewStatus.PENDING
    assert pending.certainty is CandidateCertainty.UNCERTAIN
    with pytest.raises(InvalidReviewTransition):
        evidence_sources_for(pending, target_project=PROJECT)

    approved = capture.approve(
        PROJECT, pending.candidate_id, reviewer_id="reviewer-test"
    )
    assert approved.status is ReviewStatus.APPROVED
    assert approved.certainty is CandidateCertainty.UNCERTAIN

    store = MemoryStoreDouble()
    resolver = EvidenceResolver(
        {(PROJECT, EvidenceType.REPOSITORY_FILE, "src/storage.py"): ReferenceStatus.AVAILABLE}
    )
    memory = _memory_service(store, resolver)
    bundle = _persist_approved_decision(memory, _scope(PROJECT, WRITE), approved)

    assert bundle.record.claim == approved.claim
    assert bundle.record.verification_status is VerificationStatus.UNVERIFIED
    assert bundle.record.capture_provenance.candidate_id == approved.candidate_id
    assert bundle.record.capture_provenance.certainty == CandidateCertainty.UNCERTAIN.value
    assert bundle.record.capture_provenance.review_status == ReviewStatus.APPROVED.value
    assert bundle.record.capture_provenance.reviewed_by == "reviewer-test"
    assert {item.source_id for item in bundle.evidence} == {
        "conversation-42",
        "src/storage.py",
    }
    conversation_evidence = next(
        item for item in bundle.evidence if item.source_id == "conversation-42"
    )
    assert conversation_evidence.locator == "conversation_summary: summary:decision-1"
    assert approved.source_references[0].source_timestamp == SOURCE_AT
    assert (
        bundle.record.capture_provenance.source_references[0].occurred_at
        == SOURCE_AT
    )

    snapshot = ProjectStateReconstructor(
        root,
        PROJECT,
        knowledge_source=BoundKnowledgeView(store, PROJECT),
    ).reconstruct(revision)
    finding = next(item for item in snapshot.decisions if item.claim == approved.claim)
    assert finding.recorded_verification == "unverified"
    assert finding.applicability is EvidenceClass.UNKNOWN
    assert finding.classification is EvidenceClass.UNKNOWN
    assert finding.claim.startswith("Maybe ")
    assert {item.source_id for item in finding.evidence} == {
        "conversation-42",
        "src/storage.py",
    }
    assert finding.knowledge_record_id == bundle.record.knowledge_id
    assert finding.capture_provenance == bundle.record.capture_provenance
    assert finding.capture_provenance.certainty == CandidateCertainty.UNCERTAIN.value
    file_evidence = next(item for item in bundle.evidence if item.source_id == "src/storage.py")
    file_reference = next(item for item in finding.evidence if item.source_id == "src/storage.py")
    assert file_reference.evidence_record_id == file_evidence.evidence_id
    repository_evidence = next(
        item for item in finding.evidence if item.source_id == "src/storage.py"
    )
    assert repository_evidence.classification is EvidenceClass.VERIFIED

    sync = ProjectSyncService(
        InMemorySyncEventStore(),
        authorizer=ProjectAccessPolicy(local_access={PROJECT: [READ, WRITE]}),
        clock=lambda: CAPTURED_AT,
    )
    submission = SyncEventSubmission(
        schema_version=CURRENT_SCHEMA_VERSION,
        project_id=PROJECT,
        source_client_id="codex-test",
        idempotency_key="decision-42",
        event_type=SyncEventType.DECISION_RECORDED,
        payload=SyncPayload(approved.claim, CandidateCategory.DECISION),
        source_occurred_at=SOURCE_AT,
        source_references=approved.source_references,
        evidence=approved.evidence,
        correlation_id="run-42",
    )
    write_scope = _scope(PROJECT, WRITE)
    first = sync.publish(write_scope, submission)
    duplicate = sync.publish(write_scope, submission)
    page = sync.retrieve(_scope(PROJECT, READ))

    assert first.duplicate is False
    assert duplicate.duplicate is True
    assert duplicate.event == first.event
    changed_submission = replace(
        submission,
        payload=SyncPayload("A conflicting decision payload.", CandidateCategory.DECISION),
    )
    with pytest.raises(IdempotencyConflict):
        sync.publish(write_scope, changed_submission)
    assert page.events == (first.event,)
    assert page.events[0].source_references == approved.source_references
    assert page.events[0].evidence == approved.evidence
    assert bundle.record.verification_status is VerificationStatus.UNVERIFIED
    assert not hasattr(page.events[0], "verification_status")
    assert not hasattr(page.events[0], "review_status")


def test_conflicting_capture_claims_keep_their_links_through_reconstruction(tmp_path):
    root, revision = _repository(tmp_path)
    pipeline = _capture_pipeline()
    evidence = EvidenceSource(EvidenceType.REPOSITORY_FILE, "src/storage.py", revision=revision)
    result = pipeline.capture(
        CaptureInput(
            PROJECT,
            CaptureSourceType.EXECUTION_REPORT,
            "run-conflict-7",
            (
                CaptureEvent(CandidateCategory.DECISION, "Use SQLite.", evidence=(evidence,), conflict_key="database"),
                CaptureEvent(CandidateCategory.DECISION, "Use PostgreSQL.", evidence=(evidence,), conflict_key="database"),
            ),
            source_timestamp=SOURCE_AT,
            source_revision=revision,
        )
    )
    assert len(result.candidates) == 2
    assert all(candidate.status is ReviewStatus.PENDING for candidate in result.candidates)
    queue_candidates = tuple(
        pipeline.get_candidate(PROJECT, item.candidate_id) for item in result.candidates
    )
    assert all(len(candidate.conflicts_with) == 1 for candidate in queue_candidates)

    store = MemoryStoreDouble()
    memory = _memory_service(store)
    saved = []
    for candidate in queue_candidates:
        approved = pipeline.approve(PROJECT, candidate.candidate_id, reviewer_id="reviewer-test")
        saved.append(_persist_approved_decision(memory, _scope(PROJECT, WRITE), approved))

    assert {item.record.claim for item in saved} == {"Use SQLite.", "Use PostgreSQL."}
    assert all(item.record.capture_provenance.conflicts_with for item in saved)
    snapshot = ProjectStateReconstructor(
        root,
        PROJECT,
        knowledge_source=BoundKnowledgeView(store, PROJECT),
    ).reconstruct(revision)
    assert {item.claim for item in snapshot.decisions} == {"Use SQLite.", "Use PostgreSQL."}
    findings = {item.claim: item for item in snapshot.decisions}
    assert findings["Use SQLite."].capture_provenance.conflicts_with == (
        queue_candidates[1].candidate_id,
    )
    assert findings["Use PostgreSQL."].capture_provenance.conflicts_with == (
        queue_candidates[0].candidate_id,
    )


def test_same_batch_capture_result_contains_final_conflict_links():
    pipeline = _capture_pipeline()
    result = pipeline.capture(
        CaptureInput(
            PROJECT,
            CaptureSourceType.CONVERSATION_SUMMARY,
            "conversation-batch-conflict",
            (
                CaptureEvent(CandidateCategory.DECISION, "Use SQLite.", conflict_key="database"),
                CaptureEvent(CandidateCategory.DECISION, "Use PostgreSQL.", conflict_key="database"),
            ),
        )
    )
    stored_first = pipeline.get_candidate(PROJECT, result.candidates[0].candidate_id)
    stored_second = pipeline.get_candidate(PROJECT, result.candidates[1].candidate_id)

    assert stored_first.conflicts_with == (stored_second.candidate_id,)
    assert stored_second.conflicts_with == (stored_first.candidate_id,)
    assert result.candidates == (stored_first, stored_second)


def test_sync_delivery_of_pending_candidate_does_not_approve_or_persist_knowledge(tmp_path):
    _root, revision = _repository(tmp_path)
    candidate = _candidate(_capture_pipeline(), revision, "Use SQLite.")
    assert candidate.status is ReviewStatus.PENDING
    memory_store = MemoryStoreDouble()
    sync = ProjectSyncService(
        InMemorySyncEventStore(),
        authorizer=ProjectAccessPolicy(local_access={PROJECT: [READ, WRITE]}),
        clock=lambda: CAPTURED_AT,
    )
    submission = SyncEventSubmission(
        CURRENT_SCHEMA_VERSION,
        PROJECT,
        "codex-test",
        "pending-decision-1",
        SyncEventType.DECISION_RECORDED,
        SyncPayload(candidate.claim, CandidateCategory.DECISION),
        source_references=candidate.source_references,
    )

    receipt = sync.publish(_scope(PROJECT, WRITE), submission)
    delivered = sync.retrieve(_scope(PROJECT, READ)).events[0]

    assert delivered == receipt.event
    assert candidate.status is ReviewStatus.PENDING
    assert memory_store.list_bundles(PROJECT) == ()
    assert not hasattr(delivered, "status")
    assert not hasattr(delivered, "verification_status")


def test_superseded_decision_is_historical_and_both_claims_remain_visible(tmp_path):
    root, revision = _repository(tmp_path)
    pipeline = _capture_pipeline()
    candidate = _candidate(pipeline, revision, "Use SQLite.")
    approved = pipeline.approve(PROJECT, candidate.candidate_id, reviewer_id="reviewer-test")
    store = MemoryStoreDouble()
    memory = _memory_service(store)
    first = _persist_approved_decision(memory, _scope(PROJECT, WRITE), approved)

    successor = memory.supersede_knowledge(
        _scope(PROJECT, WRITE),
        first.record.knowledge_id,
        KnowledgeType.DECISION,
        "Use PostgreSQL after shared database authorization is available.",
        evidence_sources_for(approved, target_project=PROJECT),
    )
    snapshot = ProjectStateReconstructor(
        root,
        PROJECT,
        knowledge_source=BoundKnowledgeView(store, PROJECT),
    ).reconstruct(revision)
    by_claim = {item.claim: item for item in snapshot.decisions}

    assert set(by_claim) == {
        "Use SQLite.",
        "Use PostgreSQL after shared database authorization is available.",
    }
    assert by_claim["Use SQLite."].classification is EvidenceClass.HISTORICAL
    assert by_claim["Use SQLite."].recorded_verification == "unverified"
    assert by_claim["Use SQLite."].knowledge_record_id == first.record.knowledge_id
    assert by_claim["Use SQLite."].capture_provenance.candidate_id == approved.candidate_id
    assert by_claim["Use SQLite."].lifecycle_status == LifecycleStatus.SUPERSEDED.value
    assert by_claim["Use SQLite."].superseded_by_knowledge_id == successor.record.knowledge_id
    assert successor.record.supersedes_id == first.record.knowledge_id
    assert by_claim[successor.record.claim].supersedes_knowledge_id == first.record.knowledge_id
    assert by_claim[successor.record.claim].applicability is EvidenceClass.UNKNOWN


def test_missing_evidence_is_rejected_or_reported_unknown_at_reconstruction(tmp_path):
    root, revision = _repository(tmp_path)
    missing_source = EvidenceSource(
        EvidenceType.REPOSITORY_FILE,
        "src/not-present.py",
        revision=revision,
    )
    store = MemoryStoreDouble()
    missing_resolver = EvidenceResolver(
        {(PROJECT, EvidenceType.REPOSITORY_FILE, "src/not-present.py"): ReferenceStatus.MISSING}
    )
    memory = _memory_service(store, missing_resolver)
    with pytest.raises(EvidenceReferenceMissing):
        memory.create_knowledge(
            _scope(PROJECT, WRITE),
            KnowledgeType.DECISION,
            "Use the missing module.",
            (missing_source,),
        )
    assert store.list_bundles(PROJECT) == ()

    unresolved_store = MemoryStoreDouble()
    unresolved_memory = _memory_service(unresolved_store, EvidenceResolver())
    unresolved = unresolved_memory.create_knowledge(
        _scope(PROJECT, WRITE),
        KnowledgeType.DECISION,
        "The absent file may hold the storage adapter.",
        (missing_source,),
    )
    snapshot = ProjectStateReconstructor(
        root,
        PROJECT,
        knowledge_source=StaticKnowledgeView((unresolved,)),
    ).reconstruct(revision)
    finding = snapshot.decisions[0]
    assert finding.classification is EvidenceClass.UNKNOWN
    assert finding.evidence[0].classification is EvidenceClass.STALE
    assert "absent at inspected revision" in finding.evidence[0].detail
    assert finding.recorded_verification == "unverified"


def test_project_scope_mismatches_are_rejected_or_ignored_at_each_available_boundary(tmp_path):
    root, revision = _repository(tmp_path)
    pipeline = _capture_pipeline()
    with pytest.raises(CaptureValidationError, match="does not match"):
        pipeline.capture(
            CaptureInput(
                "other-project",
                CaptureSourceType.CONVERSATION_SUMMARY,
                "foreign-conversation",
                "Decision: Use SQLite.",
            )
        )

    candidate = _candidate(pipeline, revision, "Use SQLite.")
    approved = pipeline.approve(PROJECT, candidate.candidate_id, reviewer_id="reviewer-test")
    memory = _memory_service()
    with pytest.raises(CaptureValidationError, match="does not match"):
        _persist_approved_decision(memory, _scope("other-project", WRITE), approved)

    sync = ProjectSyncService(
        InMemorySyncEventStore(),
        authorizer=ProjectAccessPolicy(local_access={PROJECT: [READ, WRITE]}),
        clock=lambda: CAPTURED_AT,
    )
    bad_submission = SyncEventSubmission(
        CURRENT_SCHEMA_VERSION,
        "other-project",
        "codex-test",
        "foreign-event",
        SyncEventType.PROJECT_UPDATE,
        SyncPayload("This must not cross project scope."),
    )
    with pytest.raises(SyncValidationError, match="does not match"):
        sync.publish(_scope(PROJECT, WRITE), bad_submission)

    foreign_record = KnowledgeRecord(
        "foreign-record",
        "other-project",
        KnowledgeType.DECISION,
        "Private foreign claim.",
        "2026-10-14T00:00:00Z",
        "2026-10-14T00:00:00Z",
    )
    snapshot = ProjectStateReconstructor(
        root,
        PROJECT,
        knowledge_source=StaticKnowledgeView((KnowledgeBundle(foreign_record, ()),)),
    ).reconstruct(revision)
    assert snapshot.decisions == ()
    assert any("outside the requested project" in item for item in snapshot.limitations)
