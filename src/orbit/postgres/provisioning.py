"""Authenticated workspace-admin provisioning operations for hosted access.

This module exposes no HTTP or MCP administration surface. Callers must pass
an Auth0-verifier-issued access token, and every mutation rechecks a current
active workspace owner/admin inside the same transaction as the change.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import logging
import re
from typing import Any, Callable, Iterator, Protocol
from uuid import UUID, uuid4

from mcp.server.auth.middleware.bearer_auth import AccessToken

from orbit.mcp_server.hosted_auth import Auth0AccessTokenAuthenticator
from orbit.mcp_server.hosted_identity import PrincipalAuthenticationFailed
from orbit.mcp_server.hosted_scope import VerifiedPrincipal
from orbit.postgres.authorization_locking import lock_authorization_write


logger = logging.getLogger(__name__)
_DATABASE_LOGIN_NAME = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


class ProvisioningDenied(PermissionError):
    """The caller or requested resource is not eligible for administration."""


class ProvisioningUnavailable(RuntimeError):
    """The provisioning transaction could not complete safely."""


class ProvisioningConflict(RuntimeError):
    """The operation would repeat or silently reactivate an existing identity."""


@dataclass(frozen=True, slots=True)
class HumanProjectCapabilities:
    can_read: bool
    can_write: bool
    can_approve_candidates: bool = False
    can_convert_memory: bool = False
    can_read_history: bool = False
    can_read_audit: bool = False

    def __post_init__(self) -> None:
        for name in (
            "can_read", "can_write", "can_approve_candidates",
            "can_convert_memory", "can_read_history", "can_read_audit",
        ):
            if type(getattr(self, name)) is not bool:
                raise TypeError(f"{name} must be a bool")
        if self.can_write and not self.can_read:
            raise ValueError("write capability requires read capability")


@dataclass(frozen=True, slots=True)
class ServiceProjectCapabilities:
    can_read: bool
    can_write: bool
    can_convert_memory: bool = False
    can_read_history: bool = False
    can_read_audit: bool = False

    def __post_init__(self) -> None:
        for name in (
            "can_read", "can_write", "can_convert_memory",
            "can_read_history", "can_read_audit",
        ):
            if type(getattr(self, name)) is not bool:
                raise TypeError(f"{name} must be a bool")
        if self.can_write and not self.can_read:
            raise ValueError("write capability requires read capability")


class _Connection(Protocol):
    def execute(self, query: str, params: tuple[Any, ...] = ()) -> Any: ...

    def transaction(self) -> Any: ...

    def close(self) -> None: ...


class PostgresAccessProvisioner:
    """Grant project access only through an Auth0-verified workspace admin.

    Provide a dedicated administrative connection factory with narrowly
    granted PostgreSQL privileges. Do not reuse the hosted MCP runtime role:
    that role should not be able to alter identities or authorization grants.
    """

    def __init__(self, connection_factory: Callable[[], _Connection]) -> None:
        if not callable(connection_factory):
            raise TypeError("connection_factory must be callable")
        self._connection_factory = connection_factory
        self._authenticator = Auth0AccessTokenAuthenticator()

    def provision_human_principal(
        self,
        access_token: AccessToken,
        *,
        workspace_id: UUID,
        project_id: UUID,
        subject: str,
        capabilities: HumanProjectCapabilities,
    ) -> UUID:
        """Create and activate a human identity with one initial project grant.

        The subject is copied from the operator's configured Auth0 issuer and
        must be confirmed by the administrator against the IdP's user record.
        This method is intentionally not exposed through public MCP or HTTP.
        """
        if not isinstance(capabilities, HumanProjectCapabilities):
            raise TypeError("capabilities must be HumanProjectCapabilities")
        actor = self._verified_admin(access_token)
        workspace_id, project_id = map(_require_uuid, (workspace_id, project_id))
        subject = _required_identity_value("subject", subject)
        user_id = uuid4()
        with self._transaction() as connection:
            lock_authorization_write(
                connection, workspace_id,
                (actor.kind, actor.issuer, actor.subject),
            )
            actor_user_id = self._require_workspace_admin(connection, actor, workspace_id)
            self._require_active_project(connection, workspace_id, project_id)
            inserted = connection.execute(
                """
                INSERT INTO celestin_users (id, issuer, subject, status)
                VALUES (%s, %s, %s, 'active')
                ON CONFLICT (issuer, subject) DO NOTHING
                RETURNING id
                """,
                (user_id, actor.issuer, subject),
            ).fetchone()
            if inserted is None:
                raise ProvisioningConflict(
                    "human identity already exists; use an explicit access update"
                )
            connection.execute(
                """
                INSERT INTO celestin_workspace_memberships
                    (workspace_id, user_id, role, status)
                VALUES (%s, %s, 'member', 'active')
                """,
                (workspace_id, user_id),
            )
            grant = connection.execute(
                """
                INSERT INTO celestin_project_memberships
                    (workspace_id, project_id, user_id, can_read, can_write,
                     can_approve_candidates, can_convert_memory, can_read_history,
                     can_read_audit, granted_at, revoked_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s,
                        clock_timestamp(), NULL)
                RETURNING project_id
                """,
                (
                    workspace_id, project_id, user_id, capabilities.can_read,
                    capabilities.can_write, capabilities.can_approve_candidates,
                    capabilities.can_convert_memory, capabilities.can_read_history,
                    capabilities.can_read_audit,
                ),
            ).fetchone()
            if grant is None:
                raise ProvisioningUnavailable("human project grant was not persisted")
        logger.info(
            "hosted human principal provisioned",
            extra={
                "actor_user_id": str(actor_user_id),
                "workspace_id": str(workspace_id),
                "project_id": str(project_id),
                "target_user_id": str(user_id),
            },
        )
        return user_id

    def provision_human_project_access(
        self,
        access_token: AccessToken,
        *,
        workspace_id: UUID,
        project_id: UUID,
        user_id: UUID,
        capabilities: HumanProjectCapabilities,
    ) -> None:
        """Activate an already-provisioned principal's workspace membership and grant.

        The global user row must already be active through the separate identity
        provisioning/deactivation workflow. This operation never creates an
        identity or changes the account-wide status.
        """
        if not isinstance(capabilities, HumanProjectCapabilities):
            raise TypeError("capabilities must be HumanProjectCapabilities")
        actor = self._verified_admin(access_token)
        workspace_id, project_id, user_id = map(
            _require_uuid, (workspace_id, project_id, user_id)
        )
        with self._transaction() as connection:
            lock_authorization_write(
                connection, workspace_id,
                (actor.kind, actor.issuer, actor.subject),
            )
            actor_user_id = self._require_workspace_admin(connection, actor, workspace_id)
            self._require_active_project(connection, workspace_id, project_id)
            target = connection.execute(
                "SELECT status FROM celestin_users WHERE id = %s FOR SHARE",
                (user_id,),
            ).fetchone()
            if target is None or target[0] != "active":
                raise ProvisioningDenied("access denied")
            connection.execute(
                """
                INSERT INTO celestin_workspace_memberships
                    (workspace_id, user_id, role, status)
                VALUES (%s, %s, 'member', 'active')
                ON CONFLICT (workspace_id, user_id) DO UPDATE
                    SET status = 'active'
                """,
                (workspace_id, user_id),
            )
            grant = connection.execute(
                """
                INSERT INTO celestin_project_memberships
                    (workspace_id, project_id, user_id, can_read, can_write,
                     can_approve_candidates, can_convert_memory, can_read_history,
                     can_read_audit, granted_at, revoked_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s,
                        clock_timestamp(), NULL)
                ON CONFLICT (project_id, user_id) DO UPDATE SET
                    can_read = EXCLUDED.can_read,
                    can_write = EXCLUDED.can_write,
                    can_approve_candidates = EXCLUDED.can_approve_candidates,
                    can_convert_memory = EXCLUDED.can_convert_memory,
                    can_read_history = EXCLUDED.can_read_history,
                    can_read_audit = EXCLUDED.can_read_audit,
                    granted_at = clock_timestamp(),
                    revoked_at = NULL
                WHERE celestin_project_memberships.workspace_id = EXCLUDED.workspace_id
                RETURNING project_id
                """,
                (
                    workspace_id, project_id, user_id, capabilities.can_read,
                    capabilities.can_write, capabilities.can_approve_candidates,
                    capabilities.can_convert_memory, capabilities.can_read_history,
                    capabilities.can_read_audit,
                ),
            ).fetchone()
            if grant is None:
                raise ProvisioningDenied("access denied")
        logger.info(
            "hosted project access provisioned",
            extra={
                "actor_user_id": str(actor_user_id),
                "workspace_id": str(workspace_id),
                "project_id": str(project_id),
                "target_user_id": str(user_id),
            },
        )

    def register_service_project_access(
        self,
        access_token: AccessToken,
        *,
        workspace_id: UUID,
        project_id: UUID,
        issuer: str,
        subject: str,
        name: str,
        capabilities: ServiceProjectCapabilities,
    ) -> UUID:
        """Register a first-party service identity and its initial project grant."""
        if not isinstance(capabilities, ServiceProjectCapabilities):
            raise TypeError("capabilities must be ServiceProjectCapabilities")
        actor = self._verified_admin(access_token)
        workspace_id, project_id = map(_require_uuid, (workspace_id, project_id))
        issuer = _required_identity_value("issuer", issuer)
        subject = _required_identity_value("subject", subject)
        name = _required_identity_value("name", name)
        service_id = uuid4()
        with self._transaction() as connection:
            lock_authorization_write(
                connection, workspace_id,
                (actor.kind, actor.issuer, actor.subject),
            )
            actor_user_id = self._require_workspace_admin(connection, actor, workspace_id)
            self._require_active_project(connection, workspace_id, project_id)
            identity = connection.execute(
                """
                INSERT INTO celestin_service_identities
                    (id, workspace_id, issuer, subject, name, status, revoked_at)
                VALUES (%s, %s, %s, %s, %s, 'active', NULL)
                ON CONFLICT (workspace_id, issuer, subject) DO NOTHING
                RETURNING id
                """,
                (service_id, workspace_id, issuer, subject, name),
            ).fetchone()
            if identity is None:
                raise ProvisioningConflict(
                    "service identity already exists; reactivation is not supported"
                )
            grant = connection.execute(
                """
                INSERT INTO celestin_service_project_grants
                    (workspace_id, project_id, service_identity_id, can_read,
                     can_write, can_convert_memory, can_read_history, can_read_audit,
                     granted_at, revoked_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, clock_timestamp(), NULL)
                RETURNING project_id
                """,
                (
                    workspace_id, project_id, service_id, capabilities.can_read,
                    capabilities.can_write, capabilities.can_convert_memory,
                    capabilities.can_read_history, capabilities.can_read_audit,
                ),
            ).fetchone()
            if grant is None:
                raise ProvisioningUnavailable("service grant was not persisted")
        logger.info(
            "hosted service access provisioned",
            extra={
                "actor_user_id": str(actor_user_id),
                "workspace_id": str(workspace_id),
                "project_id": str(project_id),
                "service_identity_id": str(service_id),
            },
        )
        return service_id

    def revoke_service_identity(
        self,
        access_token: AccessToken,
        *,
        workspace_id: UUID,
        service_identity_id: UUID,
    ) -> None:
        """Revoke a service identity and all its grants in one locked transaction."""
        actor = self._verified_admin(access_token)
        workspace_id, service_identity_id = map(
            _require_uuid, (workspace_id, service_identity_id)
        )
        with self._transaction() as connection:
            lock_authorization_write(
                connection, workspace_id,
                (actor.kind, actor.issuer, actor.subject),
            )
            actor_user_id = self._require_workspace_admin(connection, actor, workspace_id)
            identity = connection.execute(
                """SELECT status FROM celestin_service_identities
                    WHERE workspace_id = %s AND id = %s FOR UPDATE""",
                (workspace_id, service_identity_id),
            ).fetchone()
            if identity is None or identity[0] not in {"active", "revoked"}:
                raise ProvisioningDenied("access denied")
            if identity[0] == "active":
                revoked = connection.execute(
                    """
                    UPDATE celestin_service_identities
                       SET status = 'revoked', revoked_at = clock_timestamp()
                     WHERE workspace_id = %s AND id = %s AND status = 'active'
                    RETURNING id
                    """,
                    (workspace_id, service_identity_id),
                ).fetchone()
                if revoked is None:
                    raise ProvisioningUnavailable("service identity state changed unexpectedly")
                connection.execute(
                    """
                    UPDATE celestin_service_project_grants
                       SET revoked_at = clock_timestamp()
                     WHERE workspace_id = %s AND service_identity_id = %s
                       AND revoked_at IS NULL
                    """,
                    (workspace_id, service_identity_id),
                )
        logger.info(
            "hosted service identity revoked",
            extra={
                "actor_user_id": str(actor_user_id),
                "workspace_id": str(workspace_id),
                "service_identity_id": str(service_identity_id),
            },
        )

    def revoke_human_project_access(
        self,
        access_token: AccessToken,
        *,
        workspace_id: UUID,
        project_id: UUID,
        user_id: UUID,
    ) -> None:
        """Revoke one human project grant; repeated revocation is a no-op."""
        actor = self._verified_admin(access_token)
        workspace_id, project_id, user_id = map(
            _require_uuid, (workspace_id, project_id, user_id)
        )
        with self._transaction() as connection:
            lock_authorization_write(
                connection, workspace_id,
                (actor.kind, actor.issuer, actor.subject),
            )
            actor_user_id = self._require_workspace_admin(connection, actor, workspace_id)
            self._require_active_project(connection, workspace_id, project_id)
            grant = connection.execute(
                """
                SELECT revoked_at FROM celestin_project_memberships
                 WHERE workspace_id = %s AND project_id = %s AND user_id = %s
                 FOR UPDATE
                """,
                (workspace_id, project_id, user_id),
            ).fetchone()
            if grant is None:
                raise ProvisioningDenied("access denied")
            if grant[0] is None:
                connection.execute(
                    """
                    UPDATE celestin_project_memberships
                       SET revoked_at = clock_timestamp()
                     WHERE workspace_id = %s AND project_id = %s AND user_id = %s
                       AND revoked_at IS NULL
                    """,
                    (workspace_id, project_id, user_id),
                )
        logger.info(
            "hosted human project grant revoked",
            extra={
                "actor_user_id": str(actor_user_id),
                "workspace_id": str(workspace_id),
                "project_id": str(project_id),
                "target_user_id": str(user_id),
            },
        )

    def revoke_human_workspace_membership(
        self,
        access_token: AccessToken,
        *,
        workspace_id: UUID,
        user_id: UUID,
    ) -> None:
        """Suspend a human's workspace membership and revoke its project grants.

        Workspace owners cannot be removed through this operation, avoiding a
        last-owner race until a separately reviewed ownership-transfer workflow
        exists. Repeating the operation is safe and keeps grants revoked.
        """
        actor = self._verified_admin(access_token)
        workspace_id, user_id = map(_require_uuid, (workspace_id, user_id))
        with self._transaction() as connection:
            lock_authorization_write(
                connection, workspace_id,
                (actor.kind, actor.issuer, actor.subject),
            )
            actor_user_id = self._require_workspace_admin(connection, actor, workspace_id)
            target = connection.execute(
                """
                SELECT role, status FROM celestin_workspace_memberships
                 WHERE workspace_id = %s AND user_id = %s
                 FOR UPDATE
                """,
                (workspace_id, user_id),
            ).fetchone()
            if target is None or target[0] == "owner":
                raise ProvisioningDenied("access denied")
            if target[1] != "suspended":
                connection.execute(
                    """
                    UPDATE celestin_workspace_memberships
                       SET status = 'suspended'
                     WHERE workspace_id = %s AND user_id = %s
                    """,
                    (workspace_id, user_id),
                )
            connection.execute(
                """
                UPDATE celestin_project_memberships
                   SET revoked_at = clock_timestamp()
                 WHERE workspace_id = %s AND user_id = %s AND revoked_at IS NULL
                """,
                (workspace_id, user_id),
            )
        logger.info(
            "hosted human workspace membership revoked",
            extra={
                "actor_user_id": str(actor_user_id),
                "workspace_id": str(workspace_id),
                "target_user_id": str(user_id),
            },
        )

    def _verified_admin(self, access_token: AccessToken) -> VerifiedPrincipal:
        try:
            principal: VerifiedPrincipal = self._authenticator.authenticate(access_token)
        except PrincipalAuthenticationFailed:
            raise ProvisioningDenied("access denied") from None
        if principal.kind != "human":
            raise ProvisioningDenied("access denied")
        return principal

    @contextmanager
    def _transaction(self) -> Iterator[_Connection]:
        connection = None
        try:
            connection = self._connection_factory()
            with connection.transaction():
                yield connection
        except (ProvisioningDenied, ProvisioningConflict, ProvisioningUnavailable):
            raise
        except Exception as exc:
            logger.error("hosted provisioning failed (%s)", type(exc).__name__)
            raise ProvisioningUnavailable("provisioning request could not be completed") from None
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    raise ProvisioningUnavailable("provisioning session could not be released") from None

    @staticmethod
    def _require_workspace_admin(
        connection: _Connection, principal: VerifiedPrincipal, workspace_id: UUID
    ) -> UUID:
        workspace = connection.execute(
            "SELECT id FROM celestin_workspaces WHERE id = %s AND archived_at IS NULL FOR SHARE",
            (workspace_id,),
        ).fetchone()
        if workspace is None:
            raise ProvisioningDenied("access denied")
        actor = connection.execute(
            """SELECT id, status FROM celestin_users
                 WHERE issuer = %s AND subject = %s FOR SHARE""",
            (principal.issuer, principal.subject),
        ).fetchone()
        if actor is None or actor[1] != "active":
            raise ProvisioningDenied("access denied")
        membership = connection.execute(
            """SELECT role, status FROM celestin_workspace_memberships
                 WHERE workspace_id = %s AND user_id = %s FOR SHARE""",
            (workspace_id, actor[0]),
        ).fetchone()
        if membership is None or membership[0] not in {"owner", "admin"} or membership[1] != "active":
            raise ProvisioningDenied("access denied")
        return actor[0]

    @staticmethod
    def _require_active_project(
        connection: _Connection, workspace_id: UUID, project_id: UUID
    ) -> None:
        project = connection.execute(
            """SELECT id FROM celestin_projects
                 WHERE workspace_id = %s AND id = %s AND archived_at IS NULL
                 FOR SHARE""",
            (workspace_id, project_id),
        ).fetchone()
        if project is None:
            raise ProvisioningDenied("access denied")


class ProjectDatabaseLoginRegistry:
    """Register pre-created project logins in the protected scope mapping.

    PostgreSQL login creation, password/secret storage, and credential
    rotation remain external operator responsibilities. This registry uses a
    separate ``celestin_authorization_admin`` connection; it never creates a
    role or accepts a DSN from a request.
    """

    def __init__(self, connection_factory: Callable[[], _Connection]) -> None:
        if not callable(connection_factory):
            raise TypeError("connection_factory must be callable")
        self._connection_factory = connection_factory

    def register_project_login(
        self,
        login_role_name: str,
        *,
        workspace_id: UUID,
        project_id: UUID,
    ) -> None:
        login_role_name = _require_database_login_name(login_role_name)
        workspace_id, project_id = map(_require_uuid, (workspace_id, project_id))
        with self._transaction() as connection:
            lock_authorization_write(connection, workspace_id)
            project = connection.execute(
                """SELECT id FROM celestin_projects
                     WHERE workspace_id = %s AND id = %s AND archived_at IS NULL
                     FOR SHARE""",
                (workspace_id, project_id),
            ).fetchone()
            if project is None:
                raise ProvisioningDenied("access denied")

            eligible = connection.execute(
                """SELECT role.rolcanlogin
                            AND NOT role.rolsuper
                            AND NOT role.rolbypassrls
                            AND NOT role.rolcreaterole
                            AND NOT role.rolcreatedb
                            AND NOT role.rolreplication
                            AND role.rolinherit
                            AND pg_catalog.pg_has_role(
                                role.oid, 'celestin_project_runtime', 'MEMBER'
                            )
                            AND NOT EXISTS (
                                SELECT 1 FROM pg_catalog.pg_roles AS inherited
                                 WHERE inherited.oid <> role.oid
                                   AND inherited.rolname NOT IN (
                                       'celestin_project_runtime', 'pg_database_owner'
                                   )
                                   AND pg_catalog.pg_has_role(
                                       role.oid, inherited.oid, 'MEMBER'
                                   )
                            )
                            AND NOT EXISTS (
                                SELECT 1 FROM pg_catalog.pg_class AS relation
                                 WHERE relation.relnamespace = 'public'::regnamespace
                                   AND relation.relowner = role.oid
                                   AND relation.relname = ANY(%s::text[])
                            )
                            AND NOT pg_catalog.has_schema_privilege(
                                role.oid, 'public', 'CREATE'
                            )
                            AND NOT pg_catalog.has_schema_privilege(
                                role.oid, 'celestin_private', 'CREATE'
                            )
                       FROM pg_catalog.pg_roles AS role
                      WHERE role.rolname = %s""",
                (
                    [
                        "chat_messages", "project_memory",
                        "celestin_knowledge_records", "celestin_evidence_records",
                        "celestin_project_producers", "celestin_project_source_namespaces",
                        "celestin_candidates", "celestin_candidate_revisions",
                        "celestin_candidate_review_events",
                        "celestin_candidate_evidence_records",
                        "celestin_candidate_occurrences", "celestin_candidate_conversions",
                        "celestin_users", "celestin_workspaces",
                        "celestin_workspace_memberships", "celestin_projects",
                        "celestin_project_memberships", "celestin_service_identities",
                        "celestin_service_project_grants",
                    ],
                    login_role_name,
                ),
            ).fetchone()
            if eligible is None or eligible[0] is not True:
                raise ProvisioningDenied("project database login is not eligible")

            existing = connection.execute(
                """SELECT 1 FROM celestin_private.project_runtime_scopes
                     WHERE login_role_name = %s""",
                (login_role_name,),
            ).fetchone()
            if existing is not None:
                raise ProvisioningConflict("project database login is already registered")
            connection.execute(
                """INSERT INTO celestin_private.project_runtime_scopes
                       (login_role_name, workspace_id, project_id)
                   VALUES (%s, %s, %s)""",
                (login_role_name, workspace_id, project_id),
            )

    def revoke_project_login(self, login_role_name: str) -> None:
        """Remove the scope mapping; an operator must also disable its role."""
        login_role_name = _require_database_login_name(login_role_name)
        with self._transaction() as connection:
            mapping = connection.execute(
                """SELECT workspace_id FROM celestin_private.project_runtime_scopes
                     WHERE login_role_name = %s FOR UPDATE""",
                (login_role_name,),
            ).fetchone()
            if mapping is None:
                raise ProvisioningConflict("project database login is not registered")
            lock_authorization_write(connection, _require_uuid(mapping[0]))
            deleted = connection.execute(
                """DELETE FROM celestin_private.project_runtime_scopes
                     WHERE login_role_name = %s RETURNING login_role_name""",
                (login_role_name,),
            ).fetchone()
            if deleted is None:
                raise ProvisioningConflict("project database login is not registered")

    @contextmanager
    def _transaction(self) -> Iterator[_Connection]:
        try:
            connection = self._connection_factory()
        except Exception:
            raise ProvisioningUnavailable("database login registry is unavailable") from None
        try:
            with connection.transaction():
                yield connection
        except (ProvisioningDenied, ProvisioningConflict, ProvisioningUnavailable):
            raise
        except Exception:
            raise ProvisioningUnavailable(
                "database login registry operation could not be completed"
            ) from None
        finally:
            try:
                connection.close()
            except Exception:
                raise ProvisioningUnavailable(
                    "database login registry could not release its session"
                ) from None


def _require_uuid(value: object) -> UUID:
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        raise ValueError("workspace, project, user, and service IDs must be UUIDs") from None


def _required_identity_value(name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ValueError(f"{name} must be a non-empty string without NUL")
    return value


def _require_database_login_name(value: str) -> str:
    if not isinstance(value, str) or not _DATABASE_LOGIN_NAME.fullmatch(value):
        raise ValueError("database login name must be a lowercase PostgreSQL role identifier")
    return value
