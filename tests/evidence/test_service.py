"""Unit tests for the storage-neutral evidence-linked memory service."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from orbit.evidence.model import (
    CaptureKnowledgeProvenance,
    CaptureSourceProvenance,
    EvidenceRecord,
    EvidenceSource,
    EvidenceType,
    KnowledgeRecord,
    KnowledgeType,
    LifecycleStatus,
    ReferenceStatus,
    VerificationStatus,
)
from orbit.evidence.service import (
    EvidenceNotAssociated,
    EvidenceMemoryError,
    EvidenceLinkedMemoryService,
    EvidenceReferenceMissing,
    EvidenceReferenceResolutionError,
    EvidenceValidationError,
    KnowledgeNotFound,
)
from orbit.mcp_server.authorization import READ, WRITE, AuthorizedProject, ProjectAccessPolicy
from tests.evidence.store_contract import EvidenceLinkedMemoryStoreContract


NOW = datetime(2026, 10, 14, 12, 30, tzinfo=timezone.utc)


def authorized(project: str, action: str = WRITE) -> AuthorizedProject:
    # Deliberately constructible: the service must authorize the selector again.
    return AuthorizedProject(project=project, action=action, principal=None)


class Resolver:
    def __init__(self, statuses=None, error=None):
        self.statuses = statuses or {}
        self.error = error
        self.calls = []

    def resolve(self, project, source):
        self.calls.append((project, source))
        if self.error:
            raise self.error
        return self.statuses.get(
            (project, source.evidence_type, source.source_id),
            ReferenceStatus.UNRESOLVED,
        )


class FakeEvidenceStore:
    """Test double only; no production or cross-adapter storage behavior."""

    def __init__(self):
        self.records = {}
        self.evidence = {}
        self.fail_on = None
        self.fail_after_staging = None

    def _fail(self, operation):
        if self.fail_on == operation:
            raise OSError(f"{operation} failed")

    def _fail_after_staging(self, operation):
        if self.fail_after_staging == operation:
            self.fail_after_staging = None
            raise RuntimeError(f"injected {operation} failure after staging")

    def create_knowledge(self, record, evidence):
        self._fail("create")
        key = (record.project, record.knowledge_id)
        staged_records = self.records.copy()
        staged_evidence = self.evidence.copy()
        staged_records[key] = record
        staged_evidence[key] = {item.evidence_id: item for item in evidence}
        self._fail_after_staging("create")
        self.records = staged_records
        self.evidence = staged_evidence

    def attach_evidence(self, project, knowledge_id, evidence):
        self._fail("attach")
        key = (project, knowledge_id)
        if key not in self.records:
            raise KnowledgeNotFound(knowledge_id)
        identity = _evidence_identity(evidence)
        for saved in self.evidence[key].values():
            if _evidence_identity(saved) == identity:
                return saved
        self.evidence[key][evidence.evidence_id] = evidence
        return evidence

    def get_knowledge(self, project, knowledge_id):
        self._fail("get")
        return self.records.get((project, knowledge_id))

    def list_evidence(self, project, knowledge_id):
        self._fail("list")
        return tuple(self.evidence.get((project, knowledge_id), {}).values())

    def set_knowledge_verification(self, project, knowledge_id, record):
        self._fail("verify_knowledge")
        key = (project, knowledge_id)
        if key not in self.records or record.project != project:
            raise KnowledgeNotFound(knowledge_id)
        self.records[key] = record

    def set_evidence_verification(self, project, knowledge_id, evidence):
        self._fail("verify_evidence")
        key = (project, knowledge_id)
        if key not in self.records:
            raise KnowledgeNotFound(knowledge_id)
        if (
            evidence.project != project
            or evidence.evidence_id not in self.evidence.get(key, {})
        ):
            raise EvidenceNotAssociated(evidence.evidence_id)
        self.evidence[key][evidence.evidence_id] = evidence

    def supersede_knowledge(self, project, previous_id, successor, evidence):
        self._fail("supersede")
        old_key = (project, previous_id)
        new_key = (project, successor.knowledge_id)
        if old_key not in self.records:
            raise KnowledgeNotFound(previous_id)
        old = self.records[old_key]
        staged_records = self.records.copy()
        staged_evidence = self.evidence.copy()
        staged_records[old_key] = replace(
            old,
            lifecycle_status=LifecycleStatus.SUPERSEDED,
            superseded_by_id=successor.knowledge_id,
            updated_at=successor.created_at,
        )
        staged_records[new_key] = successor
        staged_evidence[new_key] = {item.evidence_id: item for item in evidence}
        self._fail_after_staging("supersede")
        self.records = staged_records
        self.evidence = staged_evidence


def _evidence_identity(evidence: EvidenceRecord):
    return (
        evidence.evidence_type,
        evidence.source_id,
        evidence.locator,
        evidence.revision,
        evidence.content_hash,
    )


def make_service(store=None, resolver=None):
    ids = iter(f"id-{n}" for n in range(1, 30))
    return EvidenceLinkedMemoryService(
        store or FakeEvidenceStore(),
        resolver or Resolver(),
        authorizer=ProjectAccessPolicy(
            local_access={
                project: [READ, WRITE]
                for project in ("p", "project-a", "project-b")
            }
        ),
        id_factory=lambda: next(ids),
        clock=lambda: NOW,
    )


def test_create_record_with_multiple_evidence_sources_and_retrieve_provenance():
    store = FakeEvidenceStore()
    resolver = Resolver(
        {
            ("project-a", EvidenceType.MESSAGE, "message-1"): ReferenceStatus.AVAILABLE,
            ("project-a", EvidenceType.GIT_COMMIT, "commit-abcd"): ReferenceStatus.AVAILABLE,
        }
    )
    service = make_service(store, resolver)
    created = service.create_knowledge(
        authorized("project-a"),
        KnowledgeType.DECISION,
        "Use SQLite for local persistence.",
        (
            EvidenceSource(EvidenceType.MESSAGE, "message-1", "conversation-2#14"),
            EvidenceSource(EvidenceType.GIT_COMMIT, "commit-abcd", revision="abcd"),
        ),
    )

    loaded = service.get_knowledge(authorized("project-a", READ), created.record.knowledge_id)

    assert created.record.verification_status is VerificationStatus.UNVERIFIED
    assert len(loaded.evidence) == 2
    assert {item.evidence_type for item in loaded.evidence} == {
        EvidenceType.MESSAGE,
        EvidenceType.GIT_COMMIT,
    }
    assert all(item.reference_status is ReferenceStatus.AVAILABLE for item in loaded.evidence)
    assert all(item.verification_status is VerificationStatus.UNVERIFIED for item in loaded.evidence)
    assert loaded.evidence[0].project == "project-a"
    assert loaded.evidence[0].captured_at == "2026-10-14T12:30:00.000000Z"


def test_identical_sources_in_one_create_are_deduplicated():
    service = make_service()
    source = EvidenceSource(EvidenceType.DOCUMENT, "design.md", "Architecture/Storage")

    result = service.create_knowledge(
        authorized("p"), KnowledgeType.DISCOVERY, "SQLite is local.", (source, source)
    )

    assert len(result.evidence) == 1


def test_unresolved_reference_is_preserved_and_reported_on_read():
    service = make_service()
    created = service.create_knowledge(
        authorized("p"),
        KnowledgeType.IMPLEMENTATION_FINDING,
        "File may contain the configuration.",
        (EvidenceSource(EvidenceType.REPOSITORY_FILE, "src/config.py", "L1-L8"),),
    )

    loaded = service.get_knowledge(authorized("p", READ), created.record.knowledge_id)

    assert loaded.evidence[0].reference_status is ReferenceStatus.UNRESOLVED
    assert loaded.record.verification_status is VerificationStatus.UNVERIFIED


def test_read_rejects_evidence_returned_outside_the_authorized_project():
    store = FakeEvidenceStore()
    service = make_service(store=store)
    created = service.create_knowledge(
        authorized("p"),
        KnowledgeType.DECISION,
        "Use SQLite.",
        (EvidenceSource(EvidenceType.MESSAGE, "message-1"),),
    )
    key = ("p", created.record.knowledge_id)
    evidence_id = created.evidence[0].evidence_id
    store.evidence[key][evidence_id] = replace(
        created.evidence[0], project="another-project"
    )

    with pytest.raises(EvidenceMemoryError, match="outside the requested project"):
        service.get_knowledge(authorized("p", READ), created.record.knowledge_id)


@pytest.mark.parametrize(
    "project_id,review_status,candidate_verification_status",
    [
        ("other", "approved", "unverified"),
        ("p", "pending_review", "unverified"),
        ("p", "approved", "verified"),
    ],
)
def test_capture_provenance_requires_matching_project_approval_and_unverified_candidate(
    project_id, review_status, candidate_verification_status
):
    provenance = CaptureKnowledgeProvenance(
        candidate_id="candidate-1",
        project_id=project_id,
        category="decision",
        certainty="uncertain",
        review_status=review_status,
        candidate_verification_status=candidate_verification_status,
        source_references=(),
        candidate_created_at="2026-10-14T12:00:00Z",
        candidate_updated_at="2026-10-14T12:00:00Z",
        reviewed_by="reviewer-label",
        reviewed_at="2026-10-14T12:00:00Z",
    )
    service = make_service()

    with pytest.raises(EvidenceValidationError):
        service.create_knowledge(
            authorized("p"),
            KnowledgeType.DECISION,
            "Use SQLite.",
            (EvidenceSource(EvidenceType.MESSAGE, "message-1"),),
            capture_provenance=provenance,
        )


def test_capture_source_provenance_must_match_an_associated_evidence_source():
    provenance = CaptureKnowledgeProvenance(
        candidate_id="candidate-1",
        project_id="p",
        category="decision",
        certainty="unqualified",
        review_status="approved",
        candidate_verification_status=VerificationStatus.UNVERIFIED.value,
        source_references=(
            CaptureSourceProvenance(
                "conversation_summary", "summary-1", "2026-10-14T12:00:00Z", None, "event:1"
            ),
        ),
        candidate_created_at="2026-10-14T12:00:00Z",
        candidate_updated_at="2026-10-14T12:00:00Z",
        reviewed_by="reviewer-label",
        reviewed_at="2026-10-14T12:00:00Z",
    )

    with pytest.raises(EvidenceValidationError, match="no matching evidence source"):
        make_service().create_knowledge(
            authorized("p"),
            KnowledgeType.DECISION,
            "A claim.",
            (EvidenceSource(EvidenceType.OTHER, "different-summary"),),
            capture_provenance=provenance,
        )


def test_missing_source_fails_before_persistence():
    store = FakeEvidenceStore()
    resolver = Resolver(
        {("p", EvidenceType.MESSAGE, "missing"): ReferenceStatus.MISSING}
    )
    service = make_service(store, resolver)

    with pytest.raises(EvidenceReferenceMissing):
        service.create_knowledge(
            authorized("p"),
            KnowledgeType.DECISION,
            "A claim",
            (EvidenceSource(EvidenceType.MESSAGE, "missing"),),
        )

    assert store.records == {}


def test_read_time_missing_source_status_is_separate_from_verification():
    store = FakeEvidenceStore()
    source_key = ("p", EvidenceType.MESSAGE, "message-1")
    resolver = Resolver({source_key: ReferenceStatus.AVAILABLE})
    service = make_service(store, resolver)
    created = service.create_knowledge(
        authorized("p"),
        KnowledgeType.DECISION,
        "A recorded claim.",
        (EvidenceSource(EvidenceType.MESSAGE, "message-1"),),
    )
    evidence_id = created.evidence[0].evidence_id
    storage_key = ("p", created.record.knowledge_id)

    assert created.evidence[0].reference_status is ReferenceStatus.AVAILABLE
    assert created.record.verification_status is VerificationStatus.UNVERIFIED

    resolver.statuses[source_key] = ReferenceStatus.MISSING
    refreshed = service.get_knowledge(authorized("p", READ), created.record.knowledge_id)

    assert refreshed.evidence[0].reference_status is ReferenceStatus.MISSING
    assert refreshed.evidence[0].verification_status is VerificationStatus.UNVERIFIED
    assert refreshed.record.verification_status is VerificationStatus.UNVERIFIED
    assert store.evidence[storage_key][evidence_id].reference_status is ReferenceStatus.AVAILABLE

    stale = service.set_knowledge_verification(
        authorized("p"),
        created.record.knowledge_id,
        VerificationStatus.STALE,
        checked_by="reviewer-1",
        note="Current applicability needs another check.",
    )
    stale_read = service.get_knowledge(authorized("p", READ), created.record.knowledge_id)

    assert stale.verification_status is VerificationStatus.STALE
    assert stale_read.record.verification_status is VerificationStatus.STALE
    assert stale_read.evidence[0].reference_status is ReferenceStatus.MISSING
    assert stale_read.evidence[0].verification_status is VerificationStatus.UNVERIFIED


def test_resolver_failure_is_explicit_and_does_not_create_a_record():
    store = FakeEvidenceStore()
    service = make_service(store, Resolver(error=OSError("resolver unavailable")))

    with pytest.raises(EvidenceReferenceResolutionError):
        service.create_knowledge(
            authorized("p"),
            KnowledgeType.DECISION,
            "A claim",
            (EvidenceSource(EvidenceType.MESSAGE, "message-1"),),
        )

    assert store.records == {}


@pytest.mark.parametrize(
    "status",
    [
        VerificationStatus.VERIFIED,
        VerificationStatus.STALE,
        VerificationStatus.CONFLICTING,
        VerificationStatus.UNVERIFIED,
    ],
)
def test_verification_states_are_explicit_and_checked_metadata_is_recorded(status):
    store = FakeEvidenceStore()
    service = make_service(store)
    created = service.create_knowledge(
        authorized("p"),
        KnowledgeType.BUG_FIX,
        "The timeout fix is present.",
        (EvidenceSource(EvidenceType.GIT_COMMIT, "commit-1"),),
    )
    checked = status is not VerificationStatus.UNVERIFIED

    updated = service.set_knowledge_verification(
        authorized("p"),
        created.record.knowledge_id,
        status,
        checked_by="reviewer-1" if checked else None,
        note="Checked against the source." if checked else None,
    )

    assert updated.verification_status is status
    assert (updated.checked_at is not None) is checked
    assert updated.checked_by == ("reviewer-1" if checked else None)
    assert store.records[("p", created.record.knowledge_id)] == updated


def test_evidence_verification_requires_explicit_checker_and_preserves_metadata():
    service = make_service()
    created = service.create_knowledge(
        authorized("p"),
        KnowledgeType.COMPLETED_WORK,
        "The regression test passed.",
        (EvidenceSource(EvidenceType.TEST_RESULT, "test-run-9", "tests/test_x.py::test_y"),),
    )
    evidence = created.evidence[0]

    with pytest.raises(EvidenceValidationError, match="checked_by is required"):
        service.set_evidence_verification(
            authorized("p"),
            created.record.knowledge_id,
            evidence.evidence_id,
            VerificationStatus.VERIFIED,
        )

    checked = service.set_evidence_verification(
        authorized("p"),
        created.record.knowledge_id,
        evidence.evidence_id,
        VerificationStatus.VERIFIED,
        checked_by="reviewer-1",
        note="Command and commit inspected.",
    )
    assert checked.verification_status is VerificationStatus.VERIFIED
    assert checked.checked_by == "reviewer-1"
    assert checked.check_note == "Command and commit inspected."
    assert checked.checked_at == "2026-10-14T12:30:00.000000Z"


def test_resetting_verification_keeps_the_last_check_metadata():
    service = make_service()
    created = service.create_knowledge(
        authorized("p"),
        KnowledgeType.DECISION,
        "A claim checked against evidence.",
        (EvidenceSource(EvidenceType.MESSAGE, "message-1"),),
    )
    verified = service.set_knowledge_verification(
        authorized("p"),
        created.record.knowledge_id,
        VerificationStatus.VERIFIED,
        checked_by="reviewer-1",
        note="Checked once.",
    )

    reset = service.set_knowledge_verification(
        authorized("p"),
        created.record.knowledge_id,
        VerificationStatus.UNVERIFIED,
    )

    assert reset.verification_status is VerificationStatus.UNVERIFIED
    assert reset.checked_at == verified.checked_at
    assert reset.checked_by == "reviewer-1"
    assert reset.check_note == "Checked once."


def test_superseding_preserves_the_old_claim_and_its_evidence():
    store = FakeEvidenceStore()
    service = make_service(store)
    old = service.create_knowledge(
        authorized("p"),
        KnowledgeType.DECISION,
        "Use SQLite.",
        (EvidenceSource(EvidenceType.MESSAGE, "message-1"),),
    )

    new = service.supersede_knowledge(
        authorized("p"),
        old.record.knowledge_id,
        KnowledgeType.DECISION,
        "Use PostgreSQL for hosted storage.",
        (EvidenceSource(EvidenceType.MESSAGE, "message-2"),),
    )

    previous = store.records[("p", old.record.knowledge_id)]
    assert previous.claim == "Use SQLite."
    assert previous.lifecycle_status is LifecycleStatus.SUPERSEDED
    assert previous.superseded_by_id == new.record.knowledge_id
    assert len(store.evidence[("p", old.record.knowledge_id)]) == 1
    assert new.record.supersedes_id == old.record.knowledge_id
    assert new.record.lifecycle_status is LifecycleStatus.CURRENT


def test_association_is_idempotent_at_the_storage_port_boundary():
    store = FakeEvidenceStore()
    service = make_service(store)
    record = service.create_knowledge(
        authorized("p"),
        KnowledgeType.DISCOVERY,
        "A useful finding.",
        (EvidenceSource(EvidenceType.DOCUMENT, "notes.md", "# Finding"),),
    )
    source = EvidenceSource(EvidenceType.DOCUMENT, "notes.md", "# Finding")

    first = service.associate_evidence(authorized("p"), record.record.knowledge_id, source)
    second = service.associate_evidence(authorized("p"), record.record.knowledge_id, source)

    assert first.evidence_id == second.evidence_id
    assert len(store.evidence[("p", record.record.knowledge_id)]) == 1


def test_project_scope_isolation_and_missing_record_errors():
    store = FakeEvidenceStore()
    service = make_service(store)
    created = service.create_knowledge(
        authorized("project-a"),
        KnowledgeType.DISCOVERY,
        "Private project finding.",
        (EvidenceSource(EvidenceType.MESSAGE, "message-1"),),
    )

    with pytest.raises(KnowledgeNotFound):
        service.get_knowledge(authorized("project-b", READ), created.record.knowledge_id)
    with pytest.raises(KnowledgeNotFound):
        service.associate_evidence(
            authorized("project-b"),
            created.record.knowledge_id,
            EvidenceSource(EvidenceType.DOCUMENT, "other.md"),
        )


def test_caller_constructed_scope_does_not_grant_evidence_access():
    store = FakeEvidenceStore()
    service = EvidenceLinkedMemoryService(
        store,
        Resolver(),
        authorizer=ProjectAccessPolicy(local_access={"project-a": [READ]}),
        clock=lambda: NOW,
    )

    with pytest.raises(PermissionError, match="project access denied"):
        service.create_knowledge(
            AuthorizedProject("project-a", WRITE, None),
            KnowledgeType.DISCOVERY,
            "caller supplied scope",
            (EvidenceSource(EvidenceType.MESSAGE, "message-1"),),
        )
    assert store.records == {}


def test_forged_scope_cannot_switch_the_service_principal_to_another_project():
    from orbit.mcp_server.authorization import AuthenticatedPrincipal

    member = AuthenticatedPrincipal("test-issuer", "member-a")
    attacker = AuthenticatedPrincipal("test-issuer", "attacker")
    service = EvidenceLinkedMemoryService(
        FakeEvidenceStore(),
        Resolver(),
        authorizer=ProjectAccessPolicy(
            principal_access={member: {"project-a": [READ, WRITE]}}
        ),
        principal=member,
        clock=lambda: NOW,
    )

    # The scope's principal is ignored; the service uses its pinned principal.
    with pytest.raises(KnowledgeNotFound):
        service.get_knowledge(AuthorizedProject("project-a", READ, attacker), "missing")
    with pytest.raises(PermissionError, match="project access denied"):
        service.get_knowledge(AuthorizedProject("project-b", READ, member), "missing")


def test_authorized_action_is_required_for_each_operation():
    service = make_service()
    with pytest.raises(PermissionError, match="write selector"):
        service.create_knowledge(
            authorized("p", READ),
            KnowledgeType.DISCOVERY,
            "Claim",
            (EvidenceSource(EvidenceType.MESSAGE, "m-1"),),
        )
    with pytest.raises(PermissionError, match="read selector"):
        service.get_knowledge(authorized("p", WRITE), "missing")


@pytest.mark.parametrize(
    "claim,sources",
    [
        (" ", (EvidenceSource(EvidenceType.MESSAGE, "m-1"),)),
        ("claim", ()),
        ("claim", (EvidenceSource(EvidenceType.MESSAGE, "  "),)),
    ],
)
def test_malformed_claim_or_source_is_rejected(claim, sources):
    service = make_service()
    with pytest.raises(EvidenceValidationError):
        service.create_knowledge(authorized("p"), KnowledgeType.DECISION, claim, sources)


@pytest.mark.parametrize("operation", ["create", "get", "list", "attach", "supersede", "verify_knowledge", "verify_evidence"])
def test_storage_errors_are_propagated_without_fabricated_success(operation):
    store = FakeEvidenceStore()
    service = make_service(store)
    created = service.create_knowledge(
        authorized("p"),
        KnowledgeType.DECISION,
        "Persisted claim.",
        (EvidenceSource(EvidenceType.MESSAGE, "message-1"),),
    )
    store.fail_on = operation

    if operation == "create":
        call = lambda: service.create_knowledge(
            authorized("p"), KnowledgeType.DECISION, "Another claim.",
            (EvidenceSource(EvidenceType.MESSAGE, "message-2"),),
        )
    elif operation == "get":
        call = lambda: service.get_knowledge(authorized("p", READ), created.record.knowledge_id)
    elif operation == "list":
        # Evidence verification also exercises the project-scoped list operation.
        call = lambda: service.set_evidence_verification(
            authorized("p"), created.record.knowledge_id, created.evidence[0].evidence_id,
            VerificationStatus.VERIFIED, checked_by="reviewer",
        )
    elif operation == "attach":
        call = lambda: service.associate_evidence(
            authorized("p"), created.record.knowledge_id,
            EvidenceSource(EvidenceType.DOCUMENT, "new.md"),
        )
    elif operation == "supersede":
        call = lambda: service.supersede_knowledge(
            authorized("p"), created.record.knowledge_id, KnowledgeType.DECISION,
            "Replacement.", (EvidenceSource(EvidenceType.MESSAGE, "message-2"),),
        )
    elif operation == "verify_knowledge":
        call = lambda: service.set_knowledge_verification(
            authorized("p"), created.record.knowledge_id,
            VerificationStatus.VERIFIED, checked_by="reviewer",
        )
    else:
        call = lambda: service.set_evidence_verification(
            authorized("p"), created.record.knowledge_id, created.evidence[0].evidence_id,
            VerificationStatus.VERIFIED, checked_by="reviewer",
        )

    with pytest.raises(OSError, match=f"{operation} failed"):
        call()


class TestFakeEvidenceLinkedMemoryStoreContract(EvidenceLinkedMemoryStoreContract):
    """Run the reusable protocol checks against the in-memory reference fake."""

    def make_store(self):
        return FakeEvidenceStore()

    def arm_failure_after_staging(self, store, operation):
        store.fail_after_staging = operation
