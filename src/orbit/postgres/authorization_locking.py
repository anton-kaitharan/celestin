"""Transaction advisory-lock calls shared by authorization and provisioning.

The database owns lock-key construction so triggers and Python code use the
same key format. Runtime roles receive only the shared request-lock function;
the authorization-admin role receives the write-lock functions.
"""

from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID


class _Connection(Protocol):
    def execute(self, query: str, params: tuple[Any, ...] = ()) -> Any: ...


def lock_request_authorization(
    connection: _Connection,
    workspace_id: UUID,
    principal_kind: str,
    issuer: str,
    subject: str,
) -> None:
    """Hold shared workspace/principal locks through request transaction end."""
    _execute(
        connection,
        "SELECT public.celestin_lock_authorization_request(%s, %s, %s, %s)",
        (workspace_id, principal_kind, issuer, subject),
    )


def lock_project_request_authorization(
    connection: _Connection, workspace_id: UUID
) -> None:
    """Lock only the workspace mapped to this project login.

    PostgreSQL checks the protected ``session_user`` mapping before acquiring
    the advisory lock. Project credentials do not receive a principal-key
    lock API because a shared project login cannot prove a request principal
    to arbitrary SQL callers.
    """
    _execute(
        connection,
        "SELECT public.celestin_lock_project_authorization_request(%s)",
        (workspace_id,),
    )


def lock_authorization_write(
    connection: _Connection,
    workspace_id: UUID,
    principal: tuple[str, str, str] | None = None,
) -> None:
    """Serialize an authorization writer with requests for the same identities.

    The database function sorts and de-duplicates all keys before taking locks,
    avoiding lock-order inversions when an administrator acts on another user.
    """
    if principal is not None and (
        not isinstance(principal, tuple)
        or len(principal) != 3
        or any(not isinstance(value, str) or not value for value in principal)
    ):
        raise ValueError("authorization principal lock key is invalid")
    params = (workspace_id, *(principal or (None, None, None)))
    _execute(
        connection,
        "SELECT public.celestin_lock_authorization_write(%s, %s, %s, %s)",
        params,
    )


def _execute(connection: _Connection, query: str, params: tuple[Any, ...]) -> None:
    cursor = connection.execute(query, params)
    close = getattr(cursor, "close", None)
    if callable(close):
        close()
