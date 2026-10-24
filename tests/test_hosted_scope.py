from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError
from unittest.mock import Mock
from uuid import UUID

from orbit.mcp_server.hosted_scope import (
    HostedAccessDenied,
    HostedAuthorizationUnavailable,
    HostedScopeResolver,
    ProjectAccessRecord,
    ProjectScopedSourceMessage,
    ResolvedProjectScope,
    HostedSourceMessageNotFound,
    VerifiedPrincipal,
)


WORKSPACE_A = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
WORKSPACE_B = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
PROJECT_A = UUID("11111111-1111-4111-8111-111111111111")
PROJECT_B = UUID("22222222-2222-4222-8222-222222222222")
PRINCIPAL_ID = UUID("33333333-3333-4333-8333-333333333333")
SERVICE_ID = UUID("44444444-4444-4444-8444-444444444444")
PROJECT_KEY = "shared-project"


class FakeStatusProvider:
    def __init__(self, active: bool = True, error: Exception | None = None) -> None:
        self.active = active
        self.error = error
        self.calls: list[VerifiedPrincipal] = []

    def is_active(self, principal: VerifiedPrincipal) -> bool:
        self.calls.append(principal)
        if self.error:
            raise self.error
        return self.active


class FakeWorkspaceBinding:
    def __init__(
        self,
        workspace_id: UUID | None = WORKSPACE_A,
        error: Exception | None = None,
    ) -> None:
        self.workspace_id = workspace_id
        self.error = error
        self.calls: list[VerifiedPrincipal] = []

    def workspace_for(
        self, principal: VerifiedPrincipal, requested_workspace_id: UUID | None = None
    ) -> UUID | None:
        self.calls.append(principal)
        if self.error:
            raise self.error
        return requested_workspace_id or self.workspace_id


class FakeAuthorizationSession:
    def __init__(
        self,
        records=None,
        error: Exception | None = None,
        source_references=None,
        source_error: Exception | None = None,
    ) -> None:
        self.records = records or {}
        self.error = error
        self.source_references = source_references or {}
        self.source_error = source_error
        self.lookups: list[tuple[VerifiedPrincipal, UUID, str]] = []
        self.source_lookups: list[tuple[ResolvedProjectScope, str]] = []
        self.domain_reads = 0

    def load_project_access(self, principal, workspace_id, project_key):
        self.lookups.append((principal, workspace_id, project_key))
        if self.error:
            raise self.error
        return self.records.get((principal, workspace_id, project_key))

    def read_domain_data(self, scope: ResolvedProjectScope) -> str:
        self.domain_reads += 1
        return f"data for {scope.project_id}"

    def load_source_message_reference(self, scope, message_id):
        self.source_lookups.append((scope, message_id))
        if self.source_error:
            raise self.source_error
        return self.source_references.get((scope.workspace_id, scope.project_id, message_id))


def human() -> VerifiedPrincipal:
    return VerifiedPrincipal("https://identity.example", "user-1", "human")


def service() -> VerifiedPrincipal:
    return VerifiedPrincipal("https://identity.example", "agent-1", "service")


def access_record(**changes) -> ProjectAccessRecord:
    fields = {
        "principal_id": PRINCIPAL_ID,
        "workspace_id": WORKSPACE_A,
        "project_id": PROJECT_A,
        "project_key": PROJECT_KEY,
        "workspace_archived": False,
        "project_archived": False,
        "membership_status": "active",
        "service_status": None,
        "grant_present": True,
        "grant_revoked": False,
        "can_read": True,
        "can_write": True,
    }
    fields.update(changes)
    return ProjectAccessRecord(**fields)


def resolver(workspace=WORKSPACE_A, *, status=None, binding=None) -> HostedScopeResolver:
    return HostedScopeResolver(
        binding or FakeWorkspaceBinding(workspace),
        status or FakeStatusProvider(),
    )


class HostedScopeTests(unittest.TestCase):
    def test_two_workspaces_with_same_project_key_resolve_only_bound_workspace(self) -> None:
        principal = human()
        record_a = access_record()
        record_b = access_record(
            workspace_id=WORKSPACE_B,
            project_id=PROJECT_B,
            principal_id=UUID("55555555-5555-4555-8555-555555555555"),
        )
        session = FakeAuthorizationSession(
            {
                (principal, WORKSPACE_A, PROJECT_KEY): record_a,
                (principal, WORKSPACE_B, PROJECT_KEY): record_b,
            }
        )

        scope = resolver(WORKSPACE_A).resolve(session, principal, PROJECT_KEY, "read")

        self.assertEqual(scope.workspace_id, WORKSPACE_A)
        self.assertEqual(scope.project_id, PROJECT_A)
        self.assertEqual(len(session.lookups), 1)
        self.assertEqual(session.lookups[0][1], WORKSPACE_A)

    def test_unknown_principal_is_denied_before_project_lookup(self) -> None:
        status = FakeStatusProvider(active=False)
        session = FakeAuthorizationSession()

        with self.assertRaises(HostedAccessDenied):
            resolver(status=status).resolve(session, human(), PROJECT_KEY, "read")

        self.assertEqual(session.lookups, [])

    def test_non_boolean_identity_status_fails_closed(self) -> None:
        with self.assertRaises(HostedAuthorizationUnavailable):
            resolver(status=FakeStatusProvider(active="active")).resolve(
                FakeAuthorizationSession(), human(), PROJECT_KEY, "read"
            )

    def test_missing_workspace_binding_is_denied_before_project_lookup(self) -> None:
        session = FakeAuthorizationSession()
        with self.assertRaises(HostedAccessDenied):
            resolver(binding=FakeWorkspaceBinding(workspace_id=None)).resolve(
                session, human(), PROJECT_KEY, "read"
            )
        self.assertEqual(session.lookups, [])

    def test_unknown_project_and_missing_grant_are_denied(self) -> None:
        principal = human()
        with self.assertRaises(HostedAccessDenied):
            resolver().resolve(FakeAuthorizationSession(), principal, PROJECT_KEY, "read")

        session = FakeAuthorizationSession(
            {(principal, WORKSPACE_A, PROJECT_KEY): access_record(grant_present=False)}
        )
        with self.assertRaises(HostedAccessDenied):
            resolver().resolve(session, principal, PROJECT_KEY, "read")

        revoked_grant = access_record(
            principal_id=SERVICE_ID,
            membership_status=None,
            service_status="active",
            grant_revoked=True,
        )
        session = FakeAuthorizationSession(
            {(principal, WORKSPACE_A, PROJECT_KEY): revoked_grant}
        )
        with self.assertRaises(HostedAccessDenied):
            resolver().resolve(session, principal, PROJECT_KEY, "read")

    def test_read_grant_does_not_authorize_write_and_denied_execute_never_reads_data(self) -> None:
        principal = human()
        session = FakeAuthorizationSession(
            {
                (principal, WORKSPACE_A, PROJECT_KEY): access_record(
                    can_read=True, can_write=False
                )
            }
        )

        with self.assertRaises(HostedAccessDenied):
            resolver().execute(
                session,
                principal,
                PROJECT_KEY,
                "write",
                lambda request_session, scope: request_session.read_domain_data(scope),
            )

        self.assertEqual(session.domain_reads, 0)

    def test_active_read_grant_executes_with_the_resolved_scope(self) -> None:
        principal = human()
        session = FakeAuthorizationSession(
            {
                (principal, WORKSPACE_A, PROJECT_KEY): access_record(
                    can_read=True, can_write=False
                )
            }
        )

        result = resolver().execute(
            session,
            principal,
            PROJECT_KEY,
            "read",
            lambda request_session, scope: request_session.read_domain_data(scope),
        )

        self.assertEqual(result, f"data for {PROJECT_A}")
        self.assertEqual(session.domain_reads, 1)

    def test_source_message_reference_must_match_resolved_workspace_and_project(self) -> None:
        principal = human()
        valid_ref = ProjectScopedSourceMessage("message-1", WORKSPACE_A, PROJECT_A)
        session = FakeAuthorizationSession(
            {(principal, WORKSPACE_A, PROJECT_KEY): access_record()},
            source_references={(WORKSPACE_A, PROJECT_A, "message-1"): valid_ref},
        )
        callback = Mock(side_effect=lambda _session, scope: scope.project_id)

        result = resolver().execute(
            session,
            principal,
            PROJECT_KEY,
            "write",
            callback,
            source_message_id="message-1",
        )

        self.assertEqual(result, PROJECT_A)
        self.assertEqual(session.source_lookups[0][0].project_id, PROJECT_A)
        callback.assert_called_once()

    def test_cross_project_or_workspace_source_message_is_not_found_before_write(self) -> None:
        principal = human()
        for reference in (
            ProjectScopedSourceMessage("message-1", WORKSPACE_A, PROJECT_B),
            ProjectScopedSourceMessage("message-1", WORKSPACE_B, PROJECT_A),
        ):
            with self.subTest(reference=reference):
                session = FakeAuthorizationSession(
                    {(principal, WORKSPACE_A, PROJECT_KEY): access_record()},
                    # Simulate a mis-scoped or defective data adapter result; the
                    # resolver still rejects the reference before the write callback.
                    source_references={
                        (WORKSPACE_A, PROJECT_A, "message-1"): reference
                    },
                )
                callback = Mock()
                with self.assertRaises(HostedSourceMessageNotFound):
                    resolver().execute(
                        session,
                        principal,
                        PROJECT_KEY,
                        "write",
                        callback,
                        source_message_id="message-1",
                    )
                callback.assert_not_called()

    def test_missing_or_unavailable_source_reference_fails_closed(self) -> None:
        principal = human()
        records = {(principal, WORKSPACE_A, PROJECT_KEY): access_record()}
        with self.assertRaises(HostedSourceMessageNotFound):
            resolver().execute(
                FakeAuthorizationSession(records),
                principal,
                PROJECT_KEY,
                "write",
                Mock(),
                source_message_id="missing",
            )

        with self.assertRaises(HostedAuthorizationUnavailable):
            resolver().execute(
                FakeAuthorizationSession(records, source_error=OSError("private detail")),
                principal,
                PROJECT_KEY,
                "write",
                Mock(),
                source_message_id="message-1",
            )

        with self.assertRaises(HostedAuthorizationUnavailable):
            resolver().execute(
                FakeAuthorizationSession(
                    records,
                    source_references={
                        (WORKSPACE_A, PROJECT_A, "message-1"): object()
                    },
                ),
                principal,
                PROJECT_KEY,
                "write",
                Mock(),
                source_message_id="message-1",
            )

    def test_suspended_human_membership_is_denied_even_with_a_grant(self) -> None:
        principal = human()
        session = FakeAuthorizationSession(
            {(principal, WORKSPACE_A, PROJECT_KEY): access_record(membership_status="suspended")}
        )
        with self.assertRaises(HostedAccessDenied):
            resolver().resolve(session, principal, PROJECT_KEY, "read")

    def test_revoked_service_identity_and_service_grant_are_denied(self) -> None:
        principal = service()
        revoked_identity = access_record(
            principal_id=SERVICE_ID,
            membership_status=None,
            service_status="revoked",
        )
        session = FakeAuthorizationSession(
            {(principal, WORKSPACE_A, PROJECT_KEY): revoked_identity}
        )
        with self.assertRaises(HostedAccessDenied):
            resolver().resolve(session, principal, PROJECT_KEY, "read")

    def test_active_service_identity_with_explicit_grant_is_authorized(self) -> None:
        principal = service()
        session = FakeAuthorizationSession(
            {
                (principal, WORKSPACE_A, PROJECT_KEY): access_record(
                    principal_id=SERVICE_ID,
                    membership_status=None,
                    service_status="active",
                )
            }
        )
        scope = resolver().resolve(session, principal, PROJECT_KEY, "write")
        self.assertEqual(scope.principal_id, SERVICE_ID)
        self.assertEqual(
            scope.permissions, frozenset({"read", "write", "memory:read"})
        )

    def test_archived_workspace_or_project_is_denied(self) -> None:
        principal = human()
        for changes in (
            {"workspace_archived": True},
            {"project_archived": True},
        ):
            with self.subTest(changes=changes):
                session = FakeAuthorizationSession(
                    {(principal, WORKSPACE_A, PROJECT_KEY): access_record(**changes)}
                )
                with self.assertRaises(HostedAccessDenied):
                    resolver().resolve(session, principal, PROJECT_KEY, "read")

    def test_scope_denies_a_mismatched_workspace_or_project_key(self) -> None:
        principal = human()
        for record in (
            access_record(workspace_id=WORKSPACE_B),
            access_record(project_key="other-project"),
        ):
            session = FakeAuthorizationSession(
                {(principal, WORKSPACE_A, PROJECT_KEY): record}
            )
            with self.assertRaises(HostedAccessDenied):
                resolver().resolve(session, principal, PROJECT_KEY, "read")

    def test_bad_write_without_read_and_unknown_action_fail_closed(self) -> None:
        principal = human()
        session = FakeAuthorizationSession(
            {
                (principal, WORKSPACE_A, PROJECT_KEY): access_record(
                    can_read=False, can_write=True
                )
            }
        )
        with self.assertRaises(HostedAccessDenied):
            resolver().resolve(session, principal, PROJECT_KEY, "write")
        with self.assertRaises(HostedAccessDenied):
            resolver().resolve(FakeAuthorizationSession(), principal, PROJECT_KEY, "admin")

    def test_unavailable_identity_binding_or_authorization_data_fails_closed(self) -> None:
        principal = human()
        cases = (
            resolver(status=FakeStatusProvider(error=OSError("private detail"))),
            resolver(binding=FakeWorkspaceBinding(error=OSError("private detail"))),
        )
        for hosted_resolver in cases:
            with self.subTest(hosted_resolver=hosted_resolver):
                with self.assertRaises(HostedAuthorizationUnavailable) as raised:
                    hosted_resolver.resolve(FakeAuthorizationSession(), principal, PROJECT_KEY, "read")
                self.assertEqual(str(raised.exception), "authorization state is unavailable")

        with self.assertRaises(HostedAuthorizationUnavailable) as raised:
            resolver().resolve(
                FakeAuthorizationSession(error=OSError("private detail")),
                principal,
                PROJECT_KEY,
                "read",
            )
        self.assertEqual(str(raised.exception), "authorization state is unavailable")

    def test_malformed_scope_is_rejected_and_valid_scope_is_immutable(self) -> None:
        principal = human()
        with self.assertRaises(TypeError):
            ResolvedProjectScope(
                principal,
                PRINCIPAL_ID,
                WORKSPACE_A,
                PROJECT_A,
                PROJECT_KEY,
                frozenset({"write"}),
            )
        with self.assertRaises(ValueError):
            ResolvedProjectScope._issue(
                principal,
                PRINCIPAL_ID,
                WORKSPACE_A,
                PROJECT_A,
                PROJECT_KEY,
                frozenset({"write"}),
            )

        scope = resolver().resolve(
            FakeAuthorizationSession(
                {(principal, WORKSPACE_A, PROJECT_KEY): access_record()}
            ),
            principal,
            PROJECT_KEY,
            "write",
        )
        with self.assertRaises(FrozenInstanceError):
            scope.project_key = "changed"


if __name__ == "__main__":
    unittest.main()
