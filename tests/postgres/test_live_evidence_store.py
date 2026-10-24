"""Separate, opt-in tests for the PostgreSQL Evidence store.

These cases are not part of the project-login authorization/RLS gate: migration
0008 intentionally withholds Evidence/Capture table grants from project runtime
identities, and no hosted Evidence request composition is currently supported.
They are retained for adapter-level investigation with the guarded one-run
fixture, not as evidence of project-scoped hosted Evidence access.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from orbit.evidence.model import (
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
from orbit.mcp_server.hosted_identity import HostedRequestBoundary
from orbit.mcp_server.hosted_scope import (
    HostedAccessDenied,
    HostedAuthorizationUnavailable,
    VerifiedPrincipal,
)
from orbit.postgres.authorization import PostgresHostedAuthorizationSessionFactory


pytestmark = pytest.mark.postgres_evidence_integration
_NOW = "2026-10-19T09:00:00.000000Z"
_LATER = "2026-10-19T09:01:00.000000Z"


class _TestAuthenticator:
    def __init__(self, principal: VerifiedPrincipal) -> None:
        self._principal = principal

    def authenticate(self, _transport_context: object) -> VerifiedPrincipal:
        return self._principal


class _FixedWorkspaceBinding:
    def __init__(self, workspace_id: UUID) -> None:
        self._workspace_id = workspace_id

    def workspace_for(
        self, _principal: VerifiedPrincipal, requested_workspace_id: UUID | None = None
    ) -> UUID | None:
        if requested_workspace_id is not None and requested_workspace_id != self._workspace_id:
            return None
        return self._workspace_id


class _ExplicitTestBoundary(HostedRequestBoundary):
    """Supply the test tenant's explicit HTTP workspace selector."""

    def __init__(self, workspace_id: UUID, **kwargs) -> None:
        super().__init__(**kwargs)
        self._test_workspace_selector = str(workspace_id)

    def execute_authorized_request(self, *args, **kwargs):
        kwargs.setdefault("workspace_selector", self._test_workspace_selector)
        return super().execute_authorized_request(*args, **kwargs)


class _ActiveStatusProvider:
    def is_active(self, _principal: VerifiedPrincipal) -> bool:
        return True


@dataclass(frozen=True)
class _EvidenceTenant:
    suffix: str
    workspace_id: UUID
    project_a_id: UUID
    project_b_id: UUID
    project_a_key: str
    project_b_key: str
    user_id: UUID
    issuer: str
    subject: str

    def boundary(self, pg) -> HostedRequestBoundary:
        principal = VerifiedPrincipal(self.issuer, self.subject, "human")
        return _ExplicitTestBoundary(
            self.workspace_id,
            authenticator=_TestAuthenticator(principal),
            workspace_binding=_FixedWorkspaceBinding(self.workspace_id),
            principal_status=_ActiveStatusProvider(),
            request_executor=PostgresHostedAuthorizationSessionFactory(pg.connect),
        )


@pytest.fixture
def evidence_tenant(postgres_connections):
    """Seed synthetic identities/projects in the already verified live target."""
    pg = postgres_connections
    suffix = uuid4().hex
    workspace_id = uuid4()
    project_a_id = uuid4()
    project_b_id = uuid4()
    user_id = uuid4()
    issuer = f"https://evidence-test.invalid/{suffix}"
    subject = f"human-{suffix}"
    project_a_key = f"evidence-a-{suffix}"
    project_b_key = f"evidence-b-{suffix}"
    fixture = _EvidenceTenant(
        suffix=suffix,
        workspace_id=workspace_id,
        project_a_id=project_a_id,
        project_b_id=project_b_id,
        project_a_key=project_a_key,
        project_b_key=project_b_key,
        user_id=user_id,
        issuer=issuer,
        subject=subject,
    )

    with pg.connection() as connection, connection.transaction():
        connection.execute(
            "INSERT INTO celestin_workspaces (id, slug, name) VALUES (%s, %s, %s)",
            (workspace_id, f"evidence-{suffix}", f"Synthetic Evidence {suffix}"),
        )
        connection.execute(
            """INSERT INTO celestin_users (id, issuer, subject, status)
               VALUES (%s, %s, %s, 'active')""",
            (user_id, issuer, subject),
        )
        connection.execute(
            """INSERT INTO celestin_workspace_memberships
                   (workspace_id, user_id, role, status)
               VALUES (%s, %s, 'member', 'active')""",
            (workspace_id, user_id),
        )
        for project_id, key, suffix_part in (
            (project_a_id, project_a_key, "a"),
            (project_b_id, project_b_key, "b"),
        ):
            connection.execute(
                """INSERT INTO celestin_projects
                       (id, workspace_id, slug, legacy_project_key)
                   VALUES (%s, %s, %s, %s)""",
                (project_id, workspace_id, f"evidence-{suffix_part}-{suffix}", key),
            )
            connection.execute(
                """INSERT INTO celestin_project_memberships
                       (workspace_id, project_id, user_id, can_read, can_write)
                   VALUES (%s, %s, %s, TRUE, TRUE)""",
                (workspace_id, project_id, user_id),
            )

    try:
        yield fixture
    finally:
        with pg.connection() as connection, connection.transaction():
            for project_id in (project_a_id, project_b_id):
                connection.execute(
                    "SELECT set_config('celestin.workspace_id', %s, true)",
                    (str(workspace_id),),
                )
                connection.execute(
                    "SELECT set_config('celestin.project_id', %s, true)",
                    (str(project_id),),
                )
                # A successful supersession has a pair of restrictive self-FKs.
                # Restore only these disposable fixture rows to an unlinkable
                # state before removing the synthetic successor.
                connection.execute(
                    """UPDATE celestin_knowledge_records
                          SET lifecycle_status = 'current', superseded_by_id = NULL
                        WHERE workspace_id = %s AND project_id = %s
                          AND lifecycle_status = 'superseded'""",
                    (workspace_id, project_id),
                )
                connection.execute(
                    """DELETE FROM celestin_evidence_records
                        WHERE workspace_id = %s AND project_id = %s""",
                    (workspace_id, project_id),
                )
                connection.execute(
                    """DELETE FROM celestin_knowledge_records
                        WHERE workspace_id = %s AND project_id = %s
                          AND supersedes_id IS NOT NULL""",
                    (workspace_id, project_id),
                )
                connection.execute(
                    """DELETE FROM celestin_knowledge_records
                        WHERE workspace_id = %s AND project_id = %s""",
                    (workspace_id, project_id),
                )
            connection.execute(
                "DELETE FROM celestin_project_memberships WHERE project_id = ANY(%s)",
                ([project_a_id, project_b_id],),
            )
            connection.execute(
                "DELETE FROM celestin_workspace_memberships WHERE workspace_id = %s",
                (workspace_id,),
            )
            connection.execute(
                "DELETE FROM celestin_projects WHERE id = ANY(%s)",
                ([project_a_id, project_b_id],),
            )
            connection.execute("DELETE FROM celestin_users WHERE id = %s", (user_id,))
            connection.execute("DELETE FROM celestin_workspaces WHERE id = %s", (workspace_id,))

        with pg.connection() as verification:
            for project_id in (project_a_id, project_b_id):
                with verification.transaction():
                    verification.execute(
                        "SELECT set_config('celestin.workspace_id', %s, true)",
                        (str(workspace_id),),
                    )
                    verification.execute(
                        "SELECT set_config('celestin.project_id', %s, true)",
                        (str(project_id),),
                    )
                    assert verification.execute(
                        """SELECT count(*) FROM celestin_knowledge_records
                            WHERE workspace_id = %s AND project_id = %s""",
                        (workspace_id, project_id),
                    ).fetchone()[0] == 0
                    assert verification.execute(
                        """SELECT count(*) FROM celestin_evidence_records
                            WHERE workspace_id = %s AND project_id = %s""",
                        (workspace_id, project_id),
                    ).fetchone()[0] == 0


def _record(
    knowledge_id: str,
    project: str,
    claim: str,
    *,
    supersedes_id: str | None = None,
) -> KnowledgeRecord:
    return KnowledgeRecord(
        knowledge_id=knowledge_id,
        project=project,
        knowledge_type=KnowledgeType.DECISION,
        claim=claim,
        created_at=_NOW,
        updated_at=_NOW,
        lifecycle_status=LifecycleStatus.CURRENT,
        supersedes_id=supersedes_id,
    )


def _evidence(evidence_id: str, project: str, source_id: str) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=evidence_id,
        project=project,
        evidence_type=EvidenceType.REPOSITORY_FILE,
        source_id=source_id,
        locator="README.md:1",
        revision="git:fixture",
        content_hash="sha256:" + "a" * 64,
        captured_at=_NOW,
        reference_status=ReferenceStatus.UNRESOLVED,
    )


def _provenance(project_key: str, suffix: str) -> CaptureKnowledgeProvenance:
    return CaptureKnowledgeProvenance(
        candidate_id=f"candidate-{suffix}",
        project_id=project_key,
        category="decision",
        certainty="unqualified",
        review_status="approved",
        candidate_verification_status="unverified",
        source_references=(
            CaptureSourceProvenance(
                source_type="repository",
                source_id=f"repo-{suffix}",
                occurred_at=_NOW,
                revision="git:fixture",
                locator="README.md:1",
            ),
        ),
        candidate_created_at=_NOW,
        candidate_updated_at=_NOW,
        original_claim="original claim",
        reviewed_by="synthetic-review-label",
        reviewed_at=_NOW,
        review_note="synthetic test provenance",
        conflicts_with=(f"other-candidate-{suffix}",),
    )


def test_live_target_migrates_evidence_schema_through_guarded_runner(live_postgres):
    versions = tuple(migration.version for migration in live_postgres.migrations)
    assert 5 in versions
    assert 6 in versions
    assert live_postgres.migration_result.applied_versions == versions
    assert live_postgres.migration_result.current_version == versions[-1]


def test_live_evidence_create_read_attach_verify_and_supersede(
    postgres_connections, evidence_tenant
):
    boundary = evidence_tenant.boundary(postgres_connections)
    project = evidence_tenant.project_a_key
    original_id = f"knowledge-original-{evidence_tenant.suffix}"
    initial_evidence = _evidence(
        f"evidence-initial-{evidence_tenant.suffix}", project, "source-initial"
    )
    original = replace(
        _record(original_id, project, "Original claim"),
        capture_provenance=_provenance(project, evidence_tenant.suffix),
    )

    boundary.execute_authorized_request(
        object(),
        project,
        "write",
        lambda request: request.evidence_store.create_knowledge(original, (initial_evidence,)),
    )

    repeated_source_first = _evidence(
        f"evidence-repeat-first-{evidence_tenant.suffix}", project, "same-source"
    )
    repeated_source_retry = replace(
        _evidence(
            f"evidence-repeat-retry-{evidence_tenant.suffix}", project, "same-source"
        ),
        captured_at=_LATER,
    )

    def attach_twice(request):
        first = request.evidence_store.attach_evidence(project, original_id, repeated_source_first)
        retry = request.evidence_store.attach_evidence(project, original_id, repeated_source_retry)
        return first, retry

    first_attachment, retried_attachment = boundary.execute_authorized_request(
        object(), project, "write", attach_twice
    )
    assert first_attachment.evidence_id == retried_attachment.evidence_id

    def verify_records(request):
        record = request.evidence_store.get_knowledge(project, original_id)
        assert record is not None
        updated_record = replace(
            record,
            verification_status=VerificationStatus.VERIFIED,
            updated_at=_LATER,
            checked_at=_LATER,
            checked_by="synthetic-review-label",
            check_note="checked by live test",
        )
        request.evidence_store.set_knowledge_verification(project, original_id, updated_record)

        evidence_rows = request.evidence_store.list_evidence(project, original_id)
        target = next(
            item for item in evidence_rows
            if item.evidence_id == first_attachment.evidence_id
        )
        request.evidence_store.set_evidence_verification(
            project,
            original_id,
            replace(
                target,
                verification_status=VerificationStatus.VERIFIED,
                checked_at=_LATER,
                checked_by="synthetic-review-label",
                check_note="source checked by live test",
            ),
        )

    boundary.execute_authorized_request(
        object(), project, "write", verify_records
    )

    successor_id = f"knowledge-successor-{evidence_tenant.suffix}"
    successor = replace(
        _record(successor_id, project, "Corrected claim", supersedes_id=original_id),
        capture_provenance=_provenance(project, f"successor-{evidence_tenant.suffix}"),
    )
    successor_evidence = _evidence(
        f"evidence-successor-{evidence_tenant.suffix}", project, "source-successor"
    )
    boundary.execute_authorized_request(
        object(),
        project,
        "write",
        lambda request: request.evidence_store.supersede_knowledge(
            project, original_id, successor, (successor_evidence,)
        ),
    )

    original_read, original_evidence, successor_read, successor_evidence_rows = (
        boundary.execute_authorized_request(
            object(),
            project,
            "read",
            lambda request: (
                request.evidence_store.get_knowledge(project, original_id),
                request.evidence_store.list_evidence(project, original_id),
                request.evidence_store.get_knowledge(project, successor_id),
                request.evidence_store.list_evidence(project, successor_id),
            ),
        )
    )
    assert original_read is not None
    assert original_read.verification_status is VerificationStatus.VERIFIED
    assert original_read.lifecycle_status is LifecycleStatus.SUPERSEDED
    assert original_read.superseded_by_id == successor_id
    assert original_read.capture_provenance == original.capture_provenance
    assert len(original_evidence) == 2
    assert original_evidence[0] == initial_evidence
    assert original_evidence[1].evidence_id == first_attachment.evidence_id
    assert original_evidence[1].verification_status is VerificationStatus.VERIFIED
    assert successor_read is not None
    assert successor_read.supersedes_id == original_id
    assert successor_evidence_rows == (successor_evidence,)


def test_live_project_scoping_and_forced_rls_isolate_knowledge_and_evidence(
    postgres_connections, evidence_tenant
):
    pg = postgres_connections
    boundary = evidence_tenant.boundary(pg)
    knowledge_id = f"same-knowledge-key-{evidence_tenant.suffix}"
    evidence_id = f"same-evidence-key-{evidence_tenant.suffix}"
    record_a = _record(knowledge_id, evidence_tenant.project_a_key, "Project A")
    record_b = _record(knowledge_id, evidence_tenant.project_b_key, "Project B")

    for project_key, record in (
        (evidence_tenant.project_a_key, record_a),
        (evidence_tenant.project_b_key, record_b),
    ):
        boundary.execute_authorized_request(
            object(),
            project_key,
            "write",
            lambda request, record=record, project_key=project_key: (
                request.evidence_store.create_knowledge(
                    record, (_evidence(evidence_id, project_key, "shared-local-id"),)
                )
            ),
        )

    read_b = boundary.execute_authorized_request(
        object(),
        evidence_tenant.project_b_key,
        "read",
        lambda request: (
            request.evidence_store.get_knowledge(
                evidence_tenant.project_b_key, knowledge_id
            ),
            request.evidence_store.list_evidence(
                evidence_tenant.project_b_key, knowledge_id
            ),
        ),
    )
    assert read_b[0] == record_b
    assert len(read_b[1]) == 1
    assert read_b[1][0].project == evidence_tenant.project_b_key

    with pytest.raises(HostedAccessDenied):
        boundary.execute_authorized_request(
            object(),
            evidence_tenant.project_b_key,
            "read",
            lambda request: request.evidence_store.get_knowledge(
                evidence_tenant.project_a_key, knowledge_id
            ),
        )

    with pg.connection() as connection, connection.transaction():
        rls_state = connection.execute(
            """SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity
                 FROM pg_catalog.pg_class AS c
                WHERE c.oid IN (
                    'public.celestin_knowledge_records'::regclass,
                    'public.celestin_evidence_records'::regclass
                )
                ORDER BY c.relname"""
        ).fetchall()
        assert rls_state == [
            ("celestin_evidence_records", True, True),
            ("celestin_knowledge_records", True, True),
        ]
        assert connection.execute(
            """SELECT knowledge_id FROM celestin_knowledge_records
                WHERE project_id = %s AND knowledge_id = %s""",
            (evidence_tenant.project_a_id, knowledge_id),
        ).fetchone() is None
        assert connection.execute(
            """SELECT evidence_id FROM celestin_evidence_records
                WHERE project_id = %s AND evidence_id = %s""",
            (evidence_tenant.project_a_id, evidence_id),
        ).fetchone() is None

    with pg.connection() as connection, connection.transaction():
        connection.execute(
            "SELECT set_config('celestin.workspace_id', %s, true)",
            (str(evidence_tenant.workspace_id),),
        )
        connection.execute(
            "SELECT set_config('celestin.project_id', %s, true)",
            (str(evidence_tenant.project_b_id),),
        )
        assert connection.execute(
            """SELECT knowledge_id FROM celestin_knowledge_records
                WHERE project_id = %s AND knowledge_id = %s""",
            (evidence_tenant.project_a_id, knowledge_id),
        ).fetchone() is None
        with pytest.raises(pg.psycopg.errors.InsufficientPrivilege) as raised:
            with connection.transaction():
                connection.execute(
                    """INSERT INTO celestin_knowledge_records
                           (workspace_id, project_id, knowledge_id, knowledge_type, claim,
                            created_at, updated_at, verification_status, lifecycle_status)
                       VALUES (%s, %s, %s, 'decision', 'RLS must reject this row',
                               %s, %s, 'unverified', 'current')""",
                    (
                        evidence_tenant.workspace_id,
                        evidence_tenant.project_a_id,
                        f"forbidden-{evidence_tenant.suffix}",
                        datetime(2026, 10, 19, 9, 0, tzinfo=timezone.utc),
                        datetime(2026, 10, 19, 9, 0, tzinfo=timezone.utc),
                    ),
                )
        assert raised.value.sqlstate == "42501"
        with pytest.raises(pg.psycopg.errors.InsufficientPrivilege) as raised:
            with connection.transaction():
                connection.execute(
                    """INSERT INTO celestin_evidence_records
                           (workspace_id, project_id, knowledge_id, evidence_id,
                            evidence_position, evidence_type, source_id, captured_at,
                            reference_status, verification_status)
                       VALUES (%s, %s, %s, %s, 1, 'repository_file', 'foreign-source',
                               %s, 'unresolved', 'unverified')""",
                    (
                        evidence_tenant.workspace_id,
                        evidence_tenant.project_a_id,
                        knowledge_id,
                        f"forbidden-evidence-{evidence_tenant.suffix}",
                        datetime(2026, 10, 19, 9, 0, tzinfo=timezone.utc),
                    ),
                )
        assert raised.value.sqlstate == "42501"


def test_live_authorization_denial_precedes_evidence_write(
    postgres_connections, evidence_tenant
):
    pg = postgres_connections
    boundary = evidence_tenant.boundary(pg)
    candidate_id = f"denied-{evidence_tenant.suffix}"
    with pg.connection() as connection, connection.transaction():
        connection.execute(
            """UPDATE celestin_project_memberships
                  SET can_write = FALSE
                WHERE workspace_id = %s AND project_id = %s AND user_id = %s""",
            (
                evidence_tenant.workspace_id,
                evidence_tenant.project_a_id,
                evidence_tenant.user_id,
            ),
        )

    callback_invoked = False

    def forbidden_write(request):
        nonlocal callback_invoked
        callback_invoked = True
        request.evidence_store.create_knowledge(
            _record(candidate_id, evidence_tenant.project_a_key, "must not persist"),
            (),
        )

    with pytest.raises(HostedAccessDenied):
        boundary.execute_authorized_request(
            object(), evidence_tenant.project_a_key, "write", forbidden_write
        )
    assert not callback_invoked
    assert boundary.execute_authorized_request(
        object(),
        evidence_tenant.project_a_key,
        "read",
        lambda request: request.evidence_store.get_knowledge(
            evidence_tenant.project_a_key, candidate_id
        ),
    ) is None


def test_live_unique_constraint_rolls_back_partial_create_to_savepoint(
    postgres_connections, evidence_tenant
):
    pg = postgres_connections
    boundary = evidence_tenant.boundary(pg)
    project = evidence_tenant.project_a_key
    partial_id = f"partial-{evidence_tenant.suffix}"
    duplicate_id = f"duplicate-evidence-{evidence_tenant.suffix}"
    first = _evidence(duplicate_id, project, "first-source")
    second = _evidence(duplicate_id, project, "second-source")
    committed_id = f"after-savepoint-{evidence_tenant.suffix}"

    def exercise_savepoint(request):
        with pytest.raises(pg.psycopg.errors.UniqueViolation) as raised:
            request.evidence_store.create_knowledge(
                _record(partial_id, project, "must roll back"), (first, second)
            )
        assert raised.value.sqlstate == "23505"
        assert request.evidence_store.get_knowledge(project, partial_id) is None
        request.evidence_store.create_knowledge(
            _record(committed_id, project, "outer transaction remains usable"),
            (_evidence(f"valid-evidence-{evidence_tenant.suffix}", project, "valid-source"),),
        )

    boundary.execute_authorized_request(
        object(), project, "write", exercise_savepoint
    )
    persisted = boundary.execute_authorized_request(
        object(),
        project,
        "read",
        lambda request: (
            request.evidence_store.get_knowledge(project, partial_id),
            request.evidence_store.get_knowledge(project, committed_id),
        ),
    )
    assert persisted == (
        None,
        _record(committed_id, project, "outer transaction remains usable"),
    )


def test_live_database_check_constraints_reject_invalid_knowledge_type(
    postgres_connections, evidence_tenant
):
    pg = postgres_connections
    with pg.connection() as connection, connection.transaction():
        connection.execute(
            "SELECT set_config('celestin.workspace_id', %s, true)",
            (str(evidence_tenant.workspace_id),),
        )
        connection.execute(
            "SELECT set_config('celestin.project_id', %s, true)",
            (str(evidence_tenant.project_a_id),),
        )
        with pytest.raises(pg.psycopg.errors.CheckViolation) as raised:
            with connection.transaction():
                connection.execute(
                    """INSERT INTO celestin_knowledge_records
                           (workspace_id, project_id, knowledge_id, knowledge_type, claim,
                            created_at, updated_at, verification_status, lifecycle_status)
                       VALUES (%s, %s, %s, 'not-a-knowledge-type', 'invalid',
                               %s, %s, 'unverified', 'current')""",
                    (
                        evidence_tenant.workspace_id,
                        evidence_tenant.project_a_id,
                        f"invalid-type-{evidence_tenant.suffix}",
                        datetime(2026, 10, 19, 9, 0, tzinfo=timezone.utc),
                        datetime(2026, 10, 19, 9, 0, tzinfo=timezone.utc),
                    ),
                )
        assert raised.value.sqlstate == "23514"


def test_live_outer_request_rollback_removes_knowledge_and_evidence(
    postgres_connections, evidence_tenant
):
    boundary = evidence_tenant.boundary(postgres_connections)
    project = evidence_tenant.project_a_key
    knowledge_id = f"outer-rollback-{evidence_tenant.suffix}"
    evidence = _evidence(
        f"outer-rollback-evidence-{evidence_tenant.suffix}", project, "rollback-source"
    )

    def create_then_fail(request):
        request.evidence_store.create_knowledge(
            _record(knowledge_id, project, "must not commit"), (evidence,)
        )
        raise RuntimeError("synthetic failure after nested adapter savepoint")

    with pytest.raises(HostedAuthorizationUnavailable):
        boundary.execute_authorized_request(
            object(), project, "write", create_then_fail
        )

    after_rollback = boundary.execute_authorized_request(
        object(),
        project,
        "read",
        lambda request: (
            request.evidence_store.get_knowledge(project, knowledge_id),
            request.evidence_store.list_evidence(project, knowledge_id),
        ),
    )
    assert after_rollback == (None, ())
