from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from orbit.capture import (
    CandidateCategory,
    CandidateCertainty,
    CandidateNotFound,
    CaptureEvent,
    CaptureInput,
    CaptureOccurrence,
    CaptureRepositoryError,
    CaptureSourceType,
    CaptureValidationError,
    InMemoryCaptureRepository,
    InvalidReviewTransition,
    KnowledgeCapturePipeline,
    ReviewStatus,
    SensitiveContentRejected,
    capture_provenance_for,
    evidence_sources_for,
    knowledge_type_for_candidate,
)
from orbit.evidence.model import EvidenceSource, EvidenceType, VerificationStatus


NOW = datetime(2026, 10, 15, 0, 40, tzinfo=timezone.utc)


def pipeline(project="celestin", repository=None):
    return KnowledgeCapturePipeline(
        project,
        repository,
        clock=lambda: NOW,
    )


def request(
    content,
    *,
    project="celestin",
    source_id="summary-1",
    source_type=CaptureSourceType.CONVERSATION_SUMMARY,
    evidence=(),
):
    return CaptureInput(
        project_id=project,
        source_type=source_type,
        source_id=source_id,
        source_timestamp="2026-10-14T23:00:00Z",
        source_revision="rev-123",
        content=content,
        evidence=evidence,
    )


def test_labeled_input_extracts_every_supported_category_with_source_lines():
    text = "\n".join(
        (
            "Decision [storage]: use SQLite for local persistence because it works offline",
            "Discovery: FTS5 supports strict and relaxed search",
            "Bug fix: corrected path normalization after symlink resolution",
            "Completed: added project authorization checks",
            "Incomplete: finish PostgreSQL adapter integration",
            "Blocker: waiting for the shared storage contract",
            "Constraint: preserve legacy MCP tool arguments",
            "Implementation detail: read repository files from pinned Git blobs",
        )
    )

    result = pipeline().capture(request(text))

    assert {item.category for item in result.candidates} == set(CandidateCategory)
    assert len(result.candidates) == 8
    assert all(item.status is ReviewStatus.PENDING for item in result.candidates)
    assert all(item.verification_status is VerificationStatus.UNVERIFIED for item in result.candidates)
    assert "because it works offline" in next(
        item.claim for item in result.candidates if item.category is CandidateCategory.DECISION
    )
    assert {item.source_references[0].locator for item in result.candidates} == {
        f"line:{number}" for number in range(1, 9)
    }


def test_structured_event_preserves_source_and_capability_one_evidence():
    evidence = EvidenceSource(EvidenceType.GIT_COMMIT, "commit-abc", revision="abc")
    event = CaptureEvent(
        CandidateCategory.BUG_FIX,
        "Fixed the cache key collision",
        evidence=(evidence,),
        locator="report:fix-2",
    )

    queue = pipeline()
    candidate = queue.capture(
        request(
            (event,),
            source_type=CaptureSourceType.EXECUTION_REPORT,
            source_id="run-42",
        )
    ).candidates[0]

    assert candidate.source_references[0].source_type is CaptureSourceType.EXECUTION_REPORT
    assert candidate.source_references[0].source_id == "run-42"
    assert candidate.source_references[0].locator == "report:fix-2"
    assert candidate.evidence == (evidence,)
    with pytest.raises(InvalidReviewTransition):
        evidence_sources_for(candidate, target_project="celestin")
    approved = queue.approve("celestin", candidate.candidate_id, reviewer_id="reviewer-1")
    mapped = evidence_sources_for(approved, target_project="celestin")
    assert EvidenceSource(
        EvidenceType.OTHER,
        "run-42",
        locator="execution_report: report:fix-2",
        revision="rev-123",
    ) in mapped
    assert evidence in mapped


def test_occurrences_preserve_order_duplicates_and_shared_deduplicated_evidence():
    shared = EvidenceSource(EvidenceType.DOCUMENT, "design.md", "storage")
    repeated = CaptureEvent(
        CandidateCategory.DISCOVERY,
        "SQLite keeps local state available offline",
        evidence=(shared,),
        locator="same-locator",
    )
    later = CaptureEvent(
        CandidateCategory.BUG_FIX,
        "Fixed the project scope check",
        evidence=(shared,),
        locator="same-locator",
    )

    result = pipeline().capture(request((repeated, repeated, later)))

    assert len(result.occurrences) == 3
    assert all(isinstance(item, CaptureOccurrence) for item in result.occurrences)
    assert [item.ordinal for item in result.occurrences] == [0, 1, 2]
    assert [item.candidate_id for item in result.occurrences] == [
        result.candidates[0].candidate_id,
        result.candidates[1].candidate_id,
        result.candidates[2].candidate_id,
    ]
    assert result.occurrences[0].candidate_id == result.occurrences[1].candidate_id
    assert result.occurrences[0] != result.occurrences[1]
    assert result.occurrences[0].source_reference == result.occurrences[1].source_reference
    assert all(item.evidence_sources == (shared,) for item in result.occurrences)
    assert result.candidates[0].evidence == (shared,)
    assert result.candidates[1].evidence == (shared,)
    assert result.candidates[2].evidence == (shared,)


def test_reordering_input_changes_capture_occurrence_order():
    repeated = CaptureEvent(CandidateCategory.DISCOVERY, "same claim")
    later = CaptureEvent(CandidateCategory.BUG_FIX, "different claim")

    forward = pipeline().capture(request((repeated, repeated, later)))
    reverse = pipeline().capture(request((later, repeated, repeated)))

    assert [item.ordinal for item in forward.occurrences] == [0, 1, 2]
    assert [item.ordinal for item in reverse.occurrences] == [0, 1, 2]
    assert [item.candidate_id for item in forward.occurrences] == [
        forward.candidates[0].candidate_id,
        forward.candidates[1].candidate_id,
        forward.candidates[2].candidate_id,
    ]
    assert [item.candidate_id for item in forward.occurrences] == [
        item.candidate_id for item in reverse.occurrences
    ][::-1]
    assert forward.occurrences != reverse.occurrences


def test_evidence_mapping_requires_a_valid_matching_target_project():
    queue = pipeline("alpha")
    candidate = queue.capture(request("Decision: use SQLite", project="alpha")).candidates[0]
    approved = queue.approve("alpha", candidate.candidate_id, reviewer_id="reviewer-1")

    with pytest.raises(CaptureValidationError, match="target_project"):
        evidence_sources_for(approved, target_project=" ")
    with pytest.raises(CaptureValidationError, match="does not match"):
        evidence_sources_for(approved, target_project="beta")

    assert evidence_sources_for(approved, target_project="alpha")


def test_capture_provenance_preserves_review_uncertainty_and_source_occurrence():
    queue = pipeline("alpha")
    candidate = queue.capture(
        request(
            "Decision: this might be temporary",
            project="alpha",
            source_type=CaptureSourceType.EXECUTION_REPORT,
            source_id="run-9",
            evidence=(EvidenceSource(EvidenceType.GIT_COMMIT, "commit-9", revision="abc"),),
        )
    ).candidates[0]
    edited = queue.edit_candidate(
        "alpha",
        candidate.candidate_id,
        "This might be temporary after review",
        reviewer_id="editor-label",
    )
    approved = queue.approve(
        "alpha", candidate.candidate_id, reviewer_id="reviewer-label", note="Reviewed"
    )

    provenance = capture_provenance_for(approved, target_project="alpha")

    assert provenance.candidate_id == approved.candidate_id
    assert provenance.project_id == "alpha"
    assert provenance.category == CandidateCategory.DECISION.value
    assert provenance.certainty == CandidateCertainty.UNCERTAIN.value
    assert provenance.review_status == ReviewStatus.APPROVED.value
    assert provenance.reviewed_by == "reviewer-label"
    assert provenance.review_note == "Reviewed"
    assert provenance.original_claim == edited.original_claim
    assert provenance.candidate_verification_status == VerificationStatus.UNVERIFIED.value
    assert provenance.source_references[0].source_type == CaptureSourceType.EXECUTION_REPORT.value
    assert provenance.source_references[0].source_id == "run-9"
    assert provenance.source_references[0].occurred_at == "2026-10-14T23:00:00Z"
    assert provenance.source_references[0].locator == "line:1"


@pytest.mark.parametrize(
    "category,expected",
    [
        (CandidateCategory.DECISION, "decision"),
        (CandidateCategory.DISCOVERY, "discovery"),
        (CandidateCategory.BUG_FIX, "bug_fix"),
        (CandidateCategory.COMPLETED_WORK, "completed_work"),
        (CandidateCategory.CONSTRAINT, "requirement"),
        (CandidateCategory.IMPLEMENTATION_DETAIL, "implementation_finding"),
    ],
)
def test_supported_candidate_categories_have_explicit_knowledge_mappings(category, expected):
    candidate = pipeline().capture(
        request((CaptureEvent(category, "A categorized claim."),))
    ).candidates[0]

    assert knowledge_type_for_candidate(candidate).value == expected


@pytest.mark.parametrize(
    "category", [CandidateCategory.INCOMPLETE_WORK, CandidateCategory.BLOCKER]
)
def test_unmapped_candidate_categories_fail_explicitly(category):
    candidate = pipeline().capture(
        request((CaptureEvent(category, "A claim requiring a dedicated knowledge type."),))
    ).candidates[0]

    with pytest.raises(CaptureValidationError, match="no agreed Capability 1 knowledge type"):
        knowledge_type_for_candidate(candidate)


@pytest.mark.parametrize(
    "bad_request",
    [
        lambda: request("  "),
        lambda: request("Decision: x", project=" "),
        lambda: request("Decision: x", source_id=""),
        lambda: request((), source_id="source"),
        lambda: request("Decision: x", source_type="chat"),
    ],
)
def test_empty_or_malformed_input_is_rejected(bad_request):
    with pytest.raises(CaptureValidationError):
        pipeline().capture(bad_request())


def test_queue_rejects_mismatched_project_without_accepting_candidates():
    queue = pipeline("alpha")

    with pytest.raises(CaptureValidationError, match="does not match"):
        queue.capture(request("Decision: use SQLite", project="beta"))
    assert queue.list_candidates("alpha") == ()


def test_source_identifiers_are_required_and_input_size_is_bounded():
    with pytest.raises(CaptureValidationError, match="source_id"):
        pipeline().capture(request("Decision: choose X", source_id="  "))
    with pytest.raises(CaptureValidationError, match="exceeds"):
        pipeline().capture(request("x" * 100_001))


def test_repeated_and_normalized_duplicate_input_is_idempotent():
    queue = pipeline()
    first = queue.capture(request("Decision: Use SQLite for local storage."))
    first_candidate = first.candidates[0]
    repeated = queue.capture(request("  decision:   use sqlite FOR local storage.  "))
    merged = queue.capture(
        request(
            "Decision: Use SQLite for local storage.",
            source_id="summary-2",
            source_type=CaptureSourceType.EXECUTION_REPORT,
        )
    )

    assert repeated.duplicate_count == 1
    assert repeated.candidates[0] == first_candidate
    assert merged.duplicate_count == 1
    assert merged.candidates[0].candidate_id == first_candidate.candidate_id
    assert len(merged.candidates[0].source_references) == 2
    assert len(queue.list_candidates("celestin")) == 1


def test_approved_candidate_identical_retry_preserves_review_and_is_idempotent():
    queue = pipeline()
    source = request("Decision: Use SQLite for local storage.")
    candidate = queue.capture(source).candidates[0]
    approved = queue.approve(
        "celestin", candidate.candidate_id, reviewer_id="reviewer-1", note="Checked"
    )

    retry = queue.capture(source)

    assert retry.candidates == (approved,)
    assert retry.suppressed == ()
    assert retry.duplicate_count == 1
    assert queue.get_candidate("celestin", approved.candidate_id) == approved
    assert retry.candidates[0].reviewed_by == "reviewer-1"
    assert retry.candidates[0].review_note == "Checked"


def test_approved_candidate_does_not_gain_evidence_after_review():
    queue = pipeline()
    source = request("Decision: Use SQLite for local storage.")
    candidate = queue.capture(source).candidates[0]
    approved = queue.approve(
        "celestin", candidate.candidate_id, reviewer_id="reviewer-1", note="Checked"
    )
    added_evidence = EvidenceSource(EvidenceType.GIT_COMMIT, "commit-new", revision="abc123")

    result = queue.capture(
        request(source.content, evidence=(added_evidence,))
    )

    assert result.candidates == ()
    assert result.duplicate_count == 1
    assert len(result.suppressed) == 1
    assert result.suppressed[0].reason == "terminal_candidate_immutable"
    assert queue.get_candidate("celestin", approved.candidate_id) == approved
    assert added_evidence not in queue.get_candidate("celestin", approved.candidate_id).evidence
    assert approved.reviewed_by == "reviewer-1"
    assert approved.review_note == "Checked"


def test_approved_candidate_does_not_inherit_changed_certainty():
    queue = pipeline()
    # NFKC normalization gives these claims the same candidate identity, while
    # the deterministic uncertainty classifier recognizes only the ASCII form.
    initial = request("Decision: ｍｉｇｈｔ use SQLite")
    candidate = queue.capture(initial).candidates[0]
    assert candidate.certainty is CandidateCertainty.UNQUALIFIED
    approved = queue.approve("celestin", candidate.candidate_id, reviewer_id="reviewer-1")

    changed = queue.capture(request("Decision: might use SQLite"))

    assert changed.candidates == ()
    assert changed.suppressed[0].reason == "terminal_candidate_immutable"
    assert queue.get_candidate("celestin", approved.candidate_id) == approved
    assert queue.get_candidate("celestin", approved.candidate_id).certainty is CandidateCertainty.UNQUALIFIED


def test_materially_different_claim_does_not_inherit_terminal_review():
    queue = pipeline()
    candidate = queue.capture(request("Decision: Use SQLite")).candidates[0]
    approved = queue.approve("celestin", candidate.candidate_id, reviewer_id="reviewer-1")

    different = queue.capture(request("Decision: Use PostgreSQL"))

    assert different.candidates[0].candidate_id != approved.candidate_id
    assert different.candidates[0].status is ReviewStatus.PENDING
    assert different.candidates[0].reviewed_by is None
    assert queue.get_candidate("celestin", approved.candidate_id) == approved


def test_rejected_candidate_does_not_gain_new_source_references():
    queue = pipeline()
    candidate = queue.capture(request("Discovery: FTS5 supports strict search")).candidates[0]
    rejected = queue.reject(
        "celestin", candidate.candidate_id, reviewer_id="reviewer-2", note="Not relevant"
    )

    result = queue.capture(
        request("Discovery: FTS5 supports strict search", source_id="summary-2")
    )

    assert result.candidates == ()
    assert result.suppressed[0].reason == "terminal_candidate_immutable"
    assert result.suppressed[0].source_reference.source_id == "summary-2"
    assert queue.get_candidate("celestin", rejected.candidate_id) == rejected
    assert rejected.reviewed_by == "reviewer-2"
    assert rejected.review_note == "Not relevant"


def test_new_conflict_links_do_not_mutate_terminal_candidate_history():
    class TrackingRepository(InMemoryCaptureRepository):
        def __init__(self):
            super().__init__()
            self.put_candidate_ids = []

        def put(self, candidate):
            self.put_candidate_ids.append(candidate.candidate_id)
            super().put(candidate)

    repository = TrackingRepository()
    queue = pipeline(repository=repository)
    sqlite = queue.capture(request("Decision [storage]: Use SQLite.")).candidates[0]
    approved = queue.approve(
        "celestin", sqlite.candidate_id, reviewer_id="reviewer-1", note="Selected"
    )
    repository.put_candidate_ids.clear()

    postgres = queue.capture(
        request("Decision [storage]: Use PostgreSQL.", source_id="summary-2")
    ).candidates[0]

    stored_terminal = queue.get_candidate("celestin", approved.candidate_id)
    assert stored_terminal == approved
    assert stored_terminal.conflicts_with == ()
    assert approved.candidate_id not in repository.put_candidate_ids
    assert postgres.conflicts_with == (approved.candidate_id,)
    assert postgres.status is ReviewStatus.PENDING


def test_explicit_conflicts_are_linked_without_selecting_a_winner():
    queue = pipeline()
    sqlite = queue.capture(request("Decision [storage]: Use SQLite.")).candidates[0]
    postgres = queue.capture(
        request("Decision [storage]: Use PostgreSQL.", source_id="summary-2")
    ).candidates[0]

    sqlite_after = queue.get_candidate("celestin", sqlite.candidate_id)
    postgres_after = queue.get_candidate("celestin", postgres.candidate_id)

    assert sqlite_after.conflicts_with == (postgres.candidate_id,)
    assert postgres_after.conflicts_with == (sqlite.candidate_id,)
    assert sqlite_after.status is postgres_after.status is ReviewStatus.PENDING
    assert all(item.verification_status is VerificationStatus.UNVERIFIED for item in (sqlite_after, postgres_after))


def test_same_batch_conflicts_are_reflected_in_every_returned_candidate():
    queue = pipeline()
    result = queue.capture(
        request(
            (
                CaptureEvent(CandidateCategory.DECISION, "Use SQLite.", conflict_key="storage"),
                CaptureEvent(CandidateCategory.DECISION, "Use PostgreSQL.", conflict_key="storage"),
            )
        )
    )

    sqlite, postgres = result.candidates
    assert sqlite.conflicts_with == (postgres.candidate_id,)
    assert postgres.conflicts_with == (sqlite.candidate_id,)
    assert queue.get_candidate("celestin", sqlite.candidate_id) == sqlite
    assert queue.get_candidate("celestin", postgres.candidate_id) == postgres


def test_uncertain_completion_stays_uncertain_unverified_and_pending():
    queue = pipeline()
    result = queue.capture(
        request("Completed: the migration might be finished, but needs a check")
    )
    candidate = result.candidates[0]
    uncategorized = queue.capture(request("The migration is complete and tests passed."))

    assert candidate.category is CandidateCategory.COMPLETED_WORK
    assert candidate.certainty is CandidateCertainty.UNCERTAIN
    assert candidate.status is ReviewStatus.PENDING
    assert candidate.verification_status is VerificationStatus.UNVERIFIED
    assert uncategorized.candidates == ()


def test_secret_like_candidate_is_suppressed_without_storing_its_value():
    queue = pipeline()
    secret = "sk_live_example_secret_value_12345"
    result = queue.capture(
        request(f"Constraint: API_KEY={secret} is required for the service")
    )

    assert result.candidates == ()
    assert len(result.suppressed) == 1
    assert result.suppressed[0].reason == "secret_like_content"
    assert secret not in repr(result)
    assert queue.list_candidates("celestin") == ()


def test_review_edit_approval_rejection_and_needs_evidence_are_scoped_and_unverified():
    queue = pipeline()
    extracted = queue.capture(
        request(
            (
                CaptureEvent(CandidateCategory.DECISION, "Use SQLite"),
                CaptureEvent(CandidateCategory.BLOCKER, "Need a storage contract"),
                CaptureEvent(CandidateCategory.DISCOVERY, "Captured discovery"),
            )
        )
    ).candidates
    decision = extracted[0]
    blocker = extracted[1]
    discovery = extracted[2]

    edited = queue.edit_candidate(
        "celestin", decision.candidate_id, "Use SQLite for local state", reviewer_id="reviewer-1"
    )
    needs_evidence = queue.request_evidence(
        "celestin", decision.candidate_id, reviewer_id="reviewer-1", note="Check deployment target"
    )
    approved = queue.approve(
        "celestin", decision.candidate_id, reviewer_id="reviewer-1", note="Reviewed"
    )
    rejected = queue.reject(
        "celestin", blocker.candidate_id, reviewer_id="reviewer-2", note="No longer blocked"
    )

    assert edited.original_claim == "Use SQLite"
    assert edited.reviewed_by == "reviewer-1"
    assert needs_evidence.status is ReviewStatus.NEEDS_EVIDENCE
    assert approved.status is ReviewStatus.APPROVED
    assert approved.verification_status is VerificationStatus.UNVERIFIED
    assert rejected.status is ReviewStatus.REJECTED
    assert {item.status for item in queue.list_candidates("celestin", ReviewStatus.APPROVED)} == {
        ReviewStatus.APPROVED
    }
    with pytest.raises(InvalidReviewTransition):
        queue.edit_candidate("celestin", decision.candidate_id, "Changed", reviewer_id="reviewer-1")
    with pytest.raises(SensitiveContentRejected):
        queue.edit_candidate(
            "celestin", discovery.candidate_id, "Password=secret_value_123", reviewer_id="reviewer-2"
        )


def test_project_isolation_and_foreign_scope_reads_are_rejected():
    shared = InMemoryCaptureRepository()
    alpha = pipeline("alpha", shared)
    beta = pipeline("beta", shared)
    alpha_candidate = alpha.capture(request("Discovery: alpha only", project="alpha")).candidates[0]
    beta.capture(request("Discovery: beta only", project="beta"))

    assert [item.claim for item in alpha.list_candidates("alpha")] == ["alpha only"]
    assert [item.claim for item in beta.list_candidates("beta")] == ["beta only"]
    with pytest.raises(CaptureValidationError, match="outside"):
        alpha.list_candidates("beta")
    with pytest.raises(CandidateNotFound):
        beta.get_candidate("beta", alpha_candidate.candidate_id)


def test_capture_output_is_deterministic_for_the_same_input_and_clock():
    source = request("Discovery: deterministic extraction")

    first = pipeline().capture(source)
    second = pipeline().capture(source)

    assert first == second


def test_repository_failures_are_reported_without_claiming_capture_succeeded():
    class BrokenRepository:
        def get(self, project_id, candidate_id):
            return None

        def list(self, project_id, status=None):
            return ()

        def put(self, candidate):
            raise OSError("repository write failed")

    with pytest.raises(CaptureRepositoryError, match="save review candidate"):
        pipeline(repository=BrokenRepository()).capture(request("Decision: capture this"))


def test_conflict_group_links_all_alternatives_without_erasing_claims():
    queue = pipeline()
    sqlite = queue.capture(request("Decision [storage]: Use SQLite.")).candidates[0]
    postgres = queue.capture(
        request("Decision [storage]: Use PostgreSQL.", source_id="summary-2")
    ).candidates[0]

    mysql = queue.capture(
        request("Decision [storage]: Use MySQL.", source_id="summary-3")
    ).candidates[0]
    assert set(queue.get_candidate("celestin", sqlite.candidate_id).conflicts_with) == {
        postgres.candidate_id, mysql.candidate_id
    }
    assert set(queue.get_candidate("celestin", postgres.candidate_id).conflicts_with) == {
        sqlite.candidate_id, mysql.candidate_id
    }
    assert set(queue.get_candidate("celestin", mysql.candidate_id).conflicts_with) == {
        sqlite.candidate_id, postgres.candidate_id
    }
    assert len(queue.list_candidates("celestin")) == 3
