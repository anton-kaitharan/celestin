from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from orbit.capture.model import (
    CandidateCategory,
    CaptureSourceReference,
    CaptureSourceType,
)
from orbit.evidence.model import EvidenceSource, EvidenceType
from orbit.mcp_server.authorization import ProjectAccessPolicy
from orbit.sync import (
    CURRENT_SCHEMA_VERSION,
    ExpiredCursor,
    IdempotencyConflict,
    InMemorySyncEventStore,
    InvalidCursor,
    ProjectSyncService,
    SyncEventSubmission,
    SyncEventType,
    SyncPayload,
    SyncStoreError,
    SyncValidationError,
    UnsupportedSchemaVersion,
)


NOW = datetime(2026, 10, 15, 1, 15, tzinfo=timezone.utc)


def scopes(project: str = "celestin"):
    policy = ProjectAccessPolicy(local_access={project: ["read", "write"]})
    return policy.authorize(None, project, "write"), policy.authorize(None, project, "read")


def submission(
    *,
    project="celestin",
    key="evt-1",
    source_client="codex",
    event_type=SyncEventType.PROJECT_UPDATE,
    summary="Updated the project implementation notes.",
    source_time="2026-10-14T21:00:00Z",
    evidence=(),
    source_references=(),
    version=CURRENT_SCHEMA_VERSION,
):
    return SyncEventSubmission(
        schema_version=version,
        project_id=project,
        source_client_id=source_client,
        idempotency_key=key,
        event_type=event_type,
        payload=SyncPayload(summary, CandidateCategory.DISCOVERY),
        source_occurred_at=source_time,
        source_references=source_references,
        evidence=evidence,
    )


def make_service(store=None, clock=None):
    return ProjectSyncService(
        store or InMemorySyncEventStore(),
        authorizer=ProjectAccessPolicy(
            local_access={
                "celestin": ["read", "write"],
                "beta": ["read", "write"],
            }
        ),
        clock=clock or (lambda: NOW),
    )


def test_publish_acknowledges_canonical_event_and_preserves_provenance():
    write_scope, _ = scopes()
    evidence = EvidenceSource(EvidenceType.GIT_COMMIT, "commit-abc", revision="abc")
    source = CaptureSourceReference(
        CaptureSourceType.EXECUTION_REPORT,
        "run-23",
        source_timestamp="2026-10-14T20:59:00Z",
        source_revision="worktree-12",
        locator="report:section-2",
    )

    receipt = make_service().publish(
        write_scope,
        submission(evidence=(evidence,), source_references=(source,)),
    )

    assert receipt.duplicate is False
    assert receipt.event.event_id.startswith("sync-")
    assert receipt.event.sequence == 1
    assert receipt.event.source_occurred_at == "2026-10-14T21:00:00Z"
    assert receipt.event.ingested_at == "2026-10-15T01:15:00Z"
    assert receipt.event.source_references == (source,)
    assert receipt.event.evidence == (evidence,)
    assert receipt.event.project_id == "celestin"


@pytest.mark.parametrize("event_type", list(SyncEventType))
def test_each_supported_event_type_is_accepted(event_type):
    write_scope, _ = scopes()
    receipt = make_service().publish(
        write_scope,
        submission(key=event_type.value, event_type=event_type),
    )
    assert receipt.event.event_type is event_type


def test_idempotent_retry_returns_same_event_and_different_content_conflicts():
    write_scope, _ = scopes()
    store = InMemorySyncEventStore()
    current = [NOW]
    service = make_service(store, lambda: current[0])
    first = service.publish(write_scope, submission())
    current[0] += timedelta(seconds=15)
    retry = service.publish(write_scope, submission())

    assert retry.duplicate is True
    assert retry.event == first.event
    assert retry.event.ingested_at == first.event.ingested_at
    assert retry.event.event_id == first.event.event_id
    with pytest.raises(IdempotencyConflict):
        service.publish(write_scope, submission(summary="A different update."))


def test_scope_action_and_event_project_are_enforced():
    write_scope, read_scope = scopes()
    service = make_service()

    with pytest.raises(SyncValidationError, match="selector does not request write"):
        service.publish(read_scope, submission())
    with pytest.raises(SyncValidationError, match="does not match"):
        service.publish(write_scope, submission(project="other-project"))
    with pytest.raises(SyncValidationError, match="project selector"):
        service.publish("celestin", submission())
    with pytest.raises(SyncValidationError, match="selector does not request read"):
        service.retrieve(write_scope)


def test_caller_constructed_scope_does_not_grant_sync_access():
    from orbit.mcp_server.authorization import AuthorizedProject

    store = InMemorySyncEventStore()
    service = ProjectSyncService(store, authorizer=ProjectAccessPolicy(), clock=lambda: NOW)

    with pytest.raises(PermissionError, match="project access denied"):
        service.publish(AuthorizedProject("celestin", "write", None), submission())
    assert store._events == {}


def test_project_a_principal_cannot_retrieve_project_b_events():
    from orbit.mcp_server.authorization import AuthenticatedPrincipal, AuthorizedProject

    member = AuthenticatedPrincipal("test-issuer", "member-a")
    store = InMemorySyncEventStore()
    service = ProjectSyncService(
        store,
        authorizer=ProjectAccessPolicy(
            principal_access={member: {"project-a": ["read", "write"]}}
        ),
        principal=member,
        clock=lambda: NOW,
    )

    with pytest.raises(PermissionError, match="project access denied"):
        service.retrieve(AuthorizedProject("project-b", "read", member))
    assert store._events == {}


@pytest.mark.parametrize(
    "bad_event",
    [
        submission(version=2),
        submission(project=" "),
        submission(source_client=""),
        submission(key="  "),
        submission(source_time="2026-10-14T21:00:00"),
        submission(summary=" "),
        submission(summary="x" * 8_001),
        submission(summary="password=top-secret-token-value"),
        SyncEventSubmission(1, "celestin", "codex", "k", "unknown", SyncPayload("x")),
    ],
)
def test_malformed_unsupported_oversized_and_secret_like_events_are_rejected(bad_event):
    write_scope, _ = scopes()
    exception = UnsupportedSchemaVersion if getattr(bad_event, "schema_version", None) == 2 else SyncValidationError
    with pytest.raises(exception):
        make_service().publish(write_scope, bad_event)


def test_retrieval_is_ordered_paginated_and_retries_do_not_skip():
    write_scope, read_scope = scopes()
    service = make_service()
    written = [
        service.publish(write_scope, submission(key=f"evt-{n}", summary=f"Update {n}."))
        for n in range(1, 4)
    ]

    first = service.retrieve(read_scope, page_size=2)
    retried = service.retrieve(read_scope, page_size=2)
    second = service.retrieve(read_scope, cursor=first.next_cursor, page_size=2)

    assert [item.event.event_id for item in written] == [event.event_id for event in first.events + second.events]
    assert first == retried
    assert first.has_more is True
    assert second.has_more is False
    assert [event.sequence for event in first.events + second.events] == [1, 2, 3]
    assert second.next_cursor.endswith(".3")


def test_late_arriving_source_timestamp_follows_ingestion_sequence():
    write_scope, read_scope = scopes()
    service = make_service()
    first = service.publish(
        write_scope,
        submission(key="newer-source", source_time="2026-10-14T22:00:00Z"),
    )
    checkpoint = service.retrieve(read_scope).next_cursor
    late = service.publish(
        write_scope,
        submission(key="late-source", source_time="2020-01-01T00:00:00Z"),
    )
    page = service.retrieve(read_scope, cursor=checkpoint)

    assert first.event.sequence == 1
    assert late.event.sequence == 2
    assert [event.event_id for event in page.events] == [late.event.event_id]


def test_empty_stream_checkpoint_and_page_boundary():
    write_scope, read_scope = scopes()
    store = InMemorySyncEventStore()
    service = make_service(store)
    empty = service.retrieve(read_scope)
    assert empty.events == ()
    assert empty.has_more is False
    assert empty.next_cursor.endswith(".0")

    service.publish(write_scope, submission())
    after_empty = service.retrieve(read_scope, cursor=empty.next_cursor)
    assert len(after_empty.events) == 1
    assert after_empty.events[0].sequence == 1
    with pytest.raises(SyncValidationError, match="page_size"):
        service.retrieve(read_scope, page_size=101)


def test_invalid_foreign_and_expired_cursors_are_distinguished():
    write_scope, read_scope = scopes()
    beta_write, beta_read = scopes("beta")
    store = InMemorySyncEventStore()
    service = make_service(store)
    with pytest.raises(InvalidCursor):
        service.retrieve(read_scope, cursor="garbage")
    foreign_cursor = service.retrieve(beta_read).next_cursor
    with pytest.raises(InvalidCursor):
        service.retrieve(read_scope, cursor=foreign_cursor)

    for index in range(3):
        service.publish(write_scope, submission(key=f"evt-{index}"))
    old_cursor = service.retrieve(read_scope, page_size=1).next_cursor
    with pytest.raises(InvalidCursor, match="ahead"):
        service.retrieve(read_scope, cursor="sync1." + old_cursor.split(".")[1] + ".99")
    valid_retained_cursor = service.retrieve(
        read_scope, cursor=old_cursor, page_size=1
    ).next_cursor
    store.expire_before("celestin", 3)
    with pytest.raises(ExpiredCursor):
        service.retrieve(read_scope, cursor=old_cursor)

    # A separately authorized project sees only its own stream.
    service.publish(beta_write, submission(project="beta", key="beta-only"))
    remaining = service.retrieve(read_scope, cursor=valid_retained_cursor)
    assert [event.sequence for event in remaining.events] == [3]
    beta_events = service.retrieve(beta_read)
    assert [event.project_id for event in beta_events.events] == ["beta"]


def test_delivery_does_not_claim_review_approval_or_verification():
    write_scope, read_scope = scopes()
    service = make_service()
    receipt = service.publish(
        write_scope,
        submission(event_type=SyncEventType.EXECUTION_SUMMARY_PUBLISHED),
    )
    delivered = service.retrieve(read_scope).events[0]

    assert delivered == receipt.event
    assert not hasattr(delivered, "status")
    assert not hasattr(delivered, "verification_status")
    assert not hasattr(delivered, "reviewed_by")


class FailingStore:
    def append(self, event):
        raise OSError("unavailable")

    def read_after(self, project_id, after_sequence, limit):
        raise OSError("unavailable")


def test_store_failures_are_reported_without_fabricated_success():
    write_scope, read_scope = scopes()
    service = make_service(FailingStore())
    with pytest.raises(SyncStoreError, match="publish"):
        service.publish(write_scope, submission())
    with pytest.raises(SyncStoreError, match="retrieve"):
        service.retrieve(read_scope)
