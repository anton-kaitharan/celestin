"""Reusable conformance checks for EvidenceLinkedMemoryStore implementations.

Implementations can subclass ``EvidenceLinkedMemoryStoreContract`` from their
own test modules and provide a store factory plus a test-only fault-injection
hook. The fault hook is not part of the production protocol. Passing this suite
against an in-memory fake is not evidence of database durability or transaction
atomicity.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from orbit.evidence.model import (
    CaptureKnowledgeProvenance,
    CaptureSourceProvenance,
    EvidenceRecord,
    EvidenceType,
    KnowledgeBundle,
    KnowledgeRecord,
    KnowledgeType,
    LifecycleStatus,
    ReferenceStatus,
    VerificationStatus,
)


class EvidenceLinkedMemoryStoreContract:
    """Test mixin for the semantics expressed by the current store protocol."""

    def make_store(self):
        raise NotImplementedError

    def arm_failure_after_staging(self, store, operation):
        """Inject a failure after an operation has staged its changes."""
        raise NotImplementedError

    def test_knowledge_and_evidence_round_trip(self):
        store = self.make_store()
        record, evidence = _knowledge_and_evidence("project-a", "round-trip")

        store.create_knowledge(record, evidence)

        loaded_record = store.get_knowledge(record.project, record.knowledge_id)
        loaded_evidence = tuple(
            store.list_evidence(record.project, record.knowledge_id)
        )
        assert loaded_record == record
        assert _bundle(loaded_record, loaded_evidence) == _bundle(record, evidence)

    def test_project_scope_isolation_for_reads_and_mutations(self):
        store = self.make_store()
        record, evidence = _knowledge_and_evidence("project-a", "scoped")
        other_record, other_evidence = _knowledge_and_evidence("project-b", "other")
        store.create_knowledge(record, evidence)
        store.create_knowledge(other_record, other_evidence)

        assert store.get_knowledge("project-b", record.knowledge_id) is None
        assert store.get_knowledge("project-a", other_record.knowledge_id) is None

        # The protocol does not prescribe a particular exception for a missing
        # project-scoped target. Whether the operation raises or is a no-op, it
        # must not change or expose the record in its actual project.
        wrong_project_record = replace(
            record, project="project-b", claim="out-of-scope replacement"
        )
        wrong_project_evidence = replace(
            evidence[0], project="project-b", source_id="out-of-scope source"
        )
        _ignore_rejection(
            lambda: store.attach_evidence(
                "project-b", record.knowledge_id, wrong_project_evidence
            )
        )
        _ignore_rejection(
            lambda: store.set_knowledge_verification(
                "project-b", record.knowledge_id, wrong_project_record
            )
        )
        _ignore_rejection(
            lambda: store.set_evidence_verification(
                "project-b", record.knowledge_id, wrong_project_evidence
            )
        )

        successor, successor_evidence = _knowledge_and_evidence(
            "project-b", "wrong-scope-successor"
        )
        _ignore_rejection(
            lambda: store.supersede_knowledge(
                "project-b",
                record.knowledge_id,
                successor,
                successor_evidence,
            )
        )

        assert store.get_knowledge("project-a", record.knowledge_id) == record
        assert store.get_knowledge("project-b", record.knowledge_id) is None
        assert store.get_knowledge("project-b", successor.knowledge_id) is None
        assert _evidence_or_empty(store, "project-a", record.knowledge_id) == evidence
        assert not _evidence_or_empty(store, "project-b", record.knowledge_id)

    def test_create_knowledge_is_atomic_after_staged_failure(self):
        store = self.make_store()
        record, evidence = _knowledge_and_evidence("project-a", "failed-create")
        self.arm_failure_after_staging(store, "create")

        with pytest.raises(RuntimeError, match="injected create failure"):
            store.create_knowledge(record, evidence)

        assert store.get_knowledge(record.project, record.knowledge_id) is None
        assert not _evidence_or_empty(store, record.project, record.knowledge_id)

    def test_successful_supersede_preserves_both_records_and_evidence(self):
        store = self.make_store()
        previous, previous_evidence = _knowledge_and_evidence(
            "project-a", "superseded"
        )
        successor, successor_evidence = _knowledge_and_evidence(
            "project-a", "current"
        )
        successor = replace(successor, supersedes_id=previous.knowledge_id)
        store.create_knowledge(previous, previous_evidence)

        store.supersede_knowledge(
            previous.project,
            previous.knowledge_id,
            successor,
            successor_evidence,
        )

        loaded_previous = store.get_knowledge(previous.project, previous.knowledge_id)
        loaded_successor = store.get_knowledge(successor.project, successor.knowledge_id)
        assert loaded_previous == replace(
            previous,
            lifecycle_status=LifecycleStatus.SUPERSEDED,
            superseded_by_id=successor.knowledge_id,
            updated_at=successor.created_at,
        )
        assert loaded_successor == successor
        assert _evidence_or_empty(
            store, previous.project, previous.knowledge_id
        ) == previous_evidence
        assert _evidence_or_empty(
            store, successor.project, successor.knowledge_id
        ) == successor_evidence

    def test_supersede_is_atomic_after_staged_failure(self):
        store = self.make_store()
        previous, previous_evidence = _knowledge_and_evidence(
            "project-a", "previous"
        )
        successor, successor_evidence = _knowledge_and_evidence(
            "project-a", "successor"
        )
        store.create_knowledge(previous, previous_evidence)
        self.arm_failure_after_staging(store, "supersede")

        with pytest.raises(RuntimeError, match="injected supersede failure"):
            store.supersede_knowledge(
                previous.project,
                previous.knowledge_id,
                successor,
                successor_evidence,
            )

        assert store.get_knowledge(previous.project, previous.knowledge_id) == previous
        assert store.get_knowledge(successor.project, successor.knowledge_id) is None
        assert _evidence_or_empty(
            store, previous.project, previous.knowledge_id
        ) == previous_evidence
        assert not _evidence_or_empty(
            store, successor.project, successor.knowledge_id
        )

    def test_evidence_attachment_is_idempotent_for_protocol_identity(self):
        store = self.make_store()
        record, initial_evidence = _knowledge_and_evidence("project-a", "attach")
        store.create_knowledge(record, initial_evidence)
        original = initial_evidence[0]
        retry = replace(
            original,
            evidence_id="retry-generated-id",
            captured_at="2026-10-19T12:00:00Z",
            reference_status=ReferenceStatus.UNRESOLVED,
        )

        first = store.attach_evidence(record.project, record.knowledge_id, retry)
        second = store.attach_evidence(record.project, record.knowledge_id, retry)

        assert first.evidence_id == original.evidence_id
        assert second.evidence_id == original.evidence_id
        saved = tuple(store.list_evidence(record.project, record.knowledge_id))
        assert len(saved) == 1
        assert saved[0] == original

    def test_missing_record_read_and_rejected_attach_do_not_fabricate_data(self):
        store = self.make_store()
        assert store.get_knowledge("project-a", "missing") is None
        _, evidence = _knowledge_and_evidence("project-a", "missing-target")

        _ignore_rejection(
            lambda: store.attach_evidence("project-a", "missing", evidence[0])
        )

        assert store.get_knowledge("project-a", "missing") is None
        assert not _evidence_or_empty(store, "project-a", "missing")


def _knowledge_and_evidence(project: str, suffix: str):
    provenance = CaptureKnowledgeProvenance(
        candidate_id=f"candidate-{suffix}",
        project_id=project,
        category="discovery",
        certainty="uncertain",
        review_status="approved",
        candidate_verification_status="unverified",
        source_references=(
            CaptureSourceProvenance(
                "conversation_summary",
                f"summary-{suffix}",
                "2026-10-18T09:15:00.123456+05:30",
                "revision-token",
                "event:2",
            ),
        ),
        candidate_created_at="2026-10-18T09:00:00Z",
        candidate_updated_at="2026-10-18T09:10:00Z",
        original_claim="Original claim.",
        reviewed_by="reviewer-label",
        reviewed_at="2026-10-18T09:12:00Z",
        review_note="Reviewed in the local prototype.",
        conflicts_with=(f"candidate-other-{suffix}",),
    )
    record = KnowledgeRecord(
        knowledge_id=f"knowledge-{suffix}",
        project=project,
        knowledge_type=KnowledgeType.DISCOVERY,
        claim="A represented knowledge claim.",
        created_at="2026-10-18T09:20:00Z",
        updated_at="2026-10-18T09:25:00Z",
        verification_status=VerificationStatus.VERIFIED,
        lifecycle_status=LifecycleStatus.CURRENT,
        checked_at="2026-10-18T09:25:00Z",
        checked_by="checker-label",
        check_note="Check metadata is provenance, not authentication.",
        capture_provenance=provenance,
    )
    evidence = (
        EvidenceRecord(
            evidence_id=f"evidence-{suffix}",
            project=project,
            evidence_type=EvidenceType.DOCUMENT,
            source_id=f"docs/{suffix}.md",
            locator="section:1",
            revision="commit-123",
            content_hash="sha256:abc123",
            captured_at="2026-10-18T09:20:00Z",
            reference_status=ReferenceStatus.AVAILABLE,
            verification_status=VerificationStatus.VERIFIED,
            checked_at="2026-10-18T09:25:00Z",
            checked_by="checker-label",
            check_note="Evidence check metadata.",
        ),
    )
    return record, evidence


def _bundle(record, evidence):
    ordered_evidence = tuple(sorted(evidence, key=lambda item: item.evidence_id))
    return KnowledgeBundle(record=record, evidence=ordered_evidence)


def _evidence_or_empty(store, project, knowledge_id):
    try:
        return tuple(store.list_evidence(project, knowledge_id))
    except Exception:
        # The protocol does not prescribe whether listing an absent record is
        # represented by an empty sequence or a missing-record error.
        return ()


def _ignore_rejection(operation):
    try:
        operation()
    except Exception:
        # Exact store-boundary error types are not part of the current protocol.
        pass
