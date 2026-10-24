"""Offline contract tests for the fail-closed hosted identity boundary."""

from __future__ import annotations

from uuid import UUID

import pytest

from orbit.mcp_server.hosted_identity import (
    HostedRequestBoundary,
    PrincipalAuthenticationFailed,
    PrincipalAuthenticatorUnavailable,
)
from orbit.mcp_server.hosted_scope import (
    HostedAccessDenied,
    HostedAuthorizationUnavailable,
    VerifiedPrincipal,
)


WORKSPACE = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
PRINCIPAL = VerifiedPrincipal("trusted-issuer", "trusted-subject", "human")


class Authenticator:
    def __init__(self, result=PRINCIPAL, error=None):
        self.result = result
        self.error = error
        self.contexts = []

    def authenticate(self, transport_context):
        self.contexts.append(transport_context)
        if self.error is not None:
            raise self.error
        return self.result


class WorkspaceBinding:
    def __init__(self, workspace=WORKSPACE):
        self.workspace = workspace
        self.principals = []

    def workspace_for(self, principal, requested_workspace_id=None):
        self.principals.append(principal)
        return requested_workspace_id or self.workspace


class PrincipalStatus:
    def is_active(self, principal):
        return True


class RequestExecutor:
    def __init__(self, result="executed"):
        self.result = result
        self.calls = []

    def _execute_authorized_request(
        self, resolver, principal, project_key, action, operation, *,
        source_message_id=None, requested_workspace_id=None
    ):
        self.calls.append(
            (resolver, principal, project_key, action, operation, source_message_id,
             requested_workspace_id)
        )
        return self.result


def boundary(*, authenticator, binding=None, executor=None):
    return HostedRequestBoundary(
        authenticator=authenticator,
        workspace_binding=binding,
        principal_status=PrincipalStatus() if binding is not None else None,
        request_executor=executor,
    )


def test_missing_authenticator_is_explicitly_unavailable_and_never_executes():
    executor = RequestExecutor()
    request = boundary(authenticator=None, binding=WorkspaceBinding(), executor=executor)

    with pytest.raises(HostedAuthorizationUnavailable, match="authentication is not configured"):
        request.execute_authorized_request(object(), "celestin", "read", lambda _uow: None)

    assert executor.calls == []


def test_rejected_credential_fails_closed_before_request_executor():
    executor = RequestExecutor()
    auth = Authenticator(error=PrincipalAuthenticationFailed("sensitive provider detail"))
    request = boundary(authenticator=auth, binding=WorkspaceBinding(), executor=executor)

    with pytest.raises(HostedAccessDenied, match="^access denied$"):
        request.execute_authorized_request(object(), "celestin", "read", lambda _uow: None)

    assert executor.calls == []


def test_authentication_exception_is_redacted_and_never_authorizes():
    executor = RequestExecutor()
    auth = Authenticator(error=PrincipalAuthenticatorUnavailable("secret token detail"))
    request = boundary(authenticator=auth, binding=WorkspaceBinding(), executor=executor)

    with pytest.raises(HostedAuthorizationUnavailable) as error:
        request.execute_authorized_request(object(), "celestin", "read", lambda _uow: None)

    assert "secret token detail" not in str(error.value)
    assert executor.calls == []


def test_unexpected_authenticator_error_is_redacted_and_fails_closed():
    executor = RequestExecutor()
    auth = Authenticator(error=RuntimeError("raw credential and provider error"))
    request = boundary(authenticator=auth, binding=WorkspaceBinding(), executor=executor)

    with pytest.raises(HostedAuthorizationUnavailable) as error:
        request.execute_authorized_request(object(), "celestin", "read", lambda _uow: None)

    assert "raw credential" not in str(error.value)
    assert executor.calls == []


def test_authenticator_must_return_a_principal_not_caller_claims():
    executor = RequestExecutor()
    forged_claims = {"issuer": "attacker", "subject": "owner", "workspace_id": str(WORKSPACE)}
    auth = Authenticator(result=forged_claims)
    request = boundary(authenticator=auth, binding=WorkspaceBinding(), executor=executor)

    with pytest.raises(HostedAuthorizationUnavailable, match="invalid identity"):
        request.execute_authorized_request(forged_claims, "celestin", "read", lambda _uow: None)

    assert auth.contexts == [forged_claims]
    assert executor.calls == []


def test_authenticator_principal_and_injected_resolver_reach_executor():
    executor = RequestExecutor(result="request result")
    auth = Authenticator(result=PRINCIPAL)
    binding = WorkspaceBinding()
    request = boundary(authenticator=auth, binding=binding, executor=executor)
    untrusted_context = {"subject": "attacker", "workspace_id": str(WORKSPACE)}
    operation = lambda _uow: "done"

    result = request.execute_authorized_request(
        untrusted_context,
        "requested-project",
        "read",
        operation,
        workspace_selector=str(WORKSPACE),
        source_message_id="message-7",
    )

    assert result == "request result"
    assert auth.contexts == [untrusted_context]
    resolver, principal, project_key, action, callback, source_id, requested_workspace = executor.calls[0]
    assert principal is PRINCIPAL
    assert project_key == "requested-project"
    assert action == "read"
    assert callback is operation
    assert source_id == "message-7"
    assert requested_workspace == WORKSPACE
    # The resolver passed to the executor owns only the injected server binding.
    assert resolver._workspace_binding is binding
    assert binding.workspace == WORKSPACE
    assert binding.principals == []  # The fake executor does not run authorization.


@pytest.mark.parametrize("selector", [None, "", "not-a-uuid", (str(WORKSPACE), str(WORKSPACE))])
def test_missing_ambiguous_or_malformed_workspace_fails_before_executor(selector):
    executor = RequestExecutor()
    auth = Authenticator()
    request = boundary(authenticator=auth, binding=WorkspaceBinding(), executor=executor)

    with pytest.raises(HostedAccessDenied, match="^access denied$"):
        request.execute_authorized_request(
            object(), "demo", "read", lambda _uow: None,
            workspace_selector=selector,
        )

    assert len(auth.contexts) == 1
    assert executor.calls == []


def test_missing_workspace_binding_fails_after_authentication_before_execution():
    auth = Authenticator()
    executor = RequestExecutor()
    request = boundary(authenticator=auth, binding=None, executor=executor)

    with pytest.raises(HostedAuthorizationUnavailable, match="workspace binding"):
        request.execute_authorized_request(object(), "celestin", "read", lambda _uow: None)

    assert len(auth.contexts) == 1
    assert executor.calls == []


def test_missing_principal_status_provider_fails_closed():
    auth = Authenticator()
    executor = RequestExecutor()
    request = HostedRequestBoundary(
        authenticator=auth,
        workspace_binding=WorkspaceBinding(),
        principal_status=None,
        request_executor=executor,
    )

    with pytest.raises(HostedAuthorizationUnavailable, match="principal status authorization"):
        request.execute_authorized_request(object(), "celestin", "read", lambda _uow: None)

    assert len(auth.contexts) == 1
    assert executor.calls == []


def test_missing_executor_fails_closed_after_authentication():
    auth = Authenticator()
    request = boundary(authenticator=auth, binding=WorkspaceBinding(), executor=None)

    with pytest.raises(HostedAuthorizationUnavailable, match="request execution"):
        request.execute_authorized_request(object(), "celestin", "read", lambda _uow: None)

    assert len(auth.contexts) == 1
