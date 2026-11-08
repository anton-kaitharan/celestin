"""Separately configured Auth0/PostgreSQL Streamable HTTP MCP runtime.

The local ``celestin``/``orbit``/``memory-bridge`` entry points remain the
existing stdio/SQLite server. This module is imported only by the explicit
``celestin-hosted`` command.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
import json
import logging
import os
from pathlib import Path
import re
from typing import Any, Mapping
from urllib.parse import urlsplit
from uuid import UUID

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from orbit.mcp_server.hosted_auth import (
    Auth0AccessTokenAuthenticator,
    Auth0TokenVerifier,
    Auth0VerifierConfig,
    READ_SCOPE,
    WRITE_SCOPE,
)
from orbit.mcp_server.hosted_identity import HostedRequestBoundary
from orbit.mcp_server.hosted_scope import (
    HostedAccessDenied,
    HostedAuthorizationUnavailable,
    HostedSourceMessageNotFound,
    PrincipalStatusProvider,
    ResolvedProjectScope,
    VerifiedPrincipal,
    WorkspaceBinding,
)
from orbit.postgres.authorization import (
    ProjectScopedPostgresHostedAuthorizationSessionFactory,
)
from orbit.postgres.unit_of_work import PostgresRequestUnitOfWork
from orbit.evidence import EvidenceSource, EvidenceType, KnowledgeType, VerificationStatus
from orbit.evidence.service import (
    EvidenceNotAssociated,
    EvidenceReferenceMissing,
    EvidenceValidationError,
    KnowledgeNotFound,
)
from orbit.retrieval.service import _search_result_dict

from .server import (
    MCPToolHandlers,
    register_mcp_tools,
    _error,
)


logger = logging.getLogger(__name__)
_workspace_selector: ContextVar[object] = ContextVar(
    "celestin_hosted_workspace_selector", default=None
)
_access_token: ContextVar[object] = ContextVar("celestin_hosted_access_token", default=None)
WORKSPACE_HEADER = b"x-celestin-workspace-id"
PROTECTED_TABLES = (
    "chat_messages",
    "project_memory",
    "celestin_knowledge_records",
    "celestin_evidence_records",
    "celestin_project_producers",
    "celestin_project_source_namespaces",
    "celestin_candidates",
    "celestin_candidate_revisions",
    "celestin_candidate_review_events",
    "celestin_candidate_evidence_records",
    "celestin_candidate_occurrences",
    "celestin_candidate_conversions",
)
EVIDENCE_RUNTIME_TABLES = (
    "celestin_knowledge_records",
    "celestin_evidence_records",
)
PROJECT_RUNTIME_UNEXPOSED_TABLES = tuple(
    table
    for table in PROTECTED_TABLES
    if table not in ("chat_messages", "project_memory", *EVIDENCE_RUNTIME_TABLES)
)
AUTHORIZATION_TABLES = (
    "celestin_users",
    "celestin_workspaces",
    "celestin_workspace_memberships",
    "celestin_projects",
    "celestin_project_memberships",
    "celestin_service_identities",
    "celestin_service_project_grants",
)
AUTHORIZATION_READER_ROLE = "celestin_authorization_reader"
AUTHORIZATION_ADMIN_ROLE = "celestin_authorization_admin"
PROJECT_RUNTIME_ROLE = "celestin_project_runtime"
PRIVILEGED_GROUP_ROLES = (
    PROJECT_RUNTIME_ROLE,
    AUTHORIZATION_READER_ROLE,
    AUTHORIZATION_ADMIN_ROLE,
)
_PROJECT_DSN_ENV_NAME = re.compile(r"^CELESTIN_PROJECT_POSTGRES_DSN_[A-Z0-9_]{1,96}$")
_MAX_CONFIGURED_PROJECT_POOLS = 32
_PROJECT_SCOPE_EXPRESSION = (
    "public.celestin_request_scope_matches(workspace_id, project_id)"
)
_PROJECT_SCOPE_FUNCTION_BODY = """
    SELECT EXISTS (
        SELECT 1
          FROM celestin_private.project_runtime_scopes AS scope
         WHERE scope.login_role_name = session_user::name
           AND scope.workspace_id = row_workspace
           AND scope.project_id = row_project
    )
"""
_PROJECT_WORKSPACE_FUNCTION_BODY = """
    SELECT EXISTS (
        SELECT 1
          FROM celestin_private.project_runtime_scopes AS scope
         WHERE scope.login_role_name = session_user::name
           AND scope.workspace_id = row_workspace
    )
"""
_PROJECT_LOCK_FUNCTION_BODY = """
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM celestin_private.project_runtime_scopes AS scope
         WHERE scope.login_role_name = session_user::name
           AND scope.workspace_id = target_workspace
    ) THEN
        RAISE EXCEPTION 'project authorization scope mismatch'
            USING ERRCODE = '42501';
    END IF;

    PERFORM pg_catalog.pg_advisory_xact_lock_shared(
        pg_catalog.hashtextextended(
            'celestin-auth-v1:' ||
                pg_catalog.jsonb_build_array('workspace', target_workspace)::text,
            0
        )
    );
END;
"""
_READER_LOCK_FUNCTION_BODY = """
DECLARE
    lock_key TEXT;
BEGIN
    FOR lock_key IN
        SELECT DISTINCT key_value
          FROM (VALUES
              (pg_catalog.jsonb_build_array('workspace', target_workspace)::text),
              (pg_catalog.jsonb_build_array(
                  'principal', principal_kind, principal_issuer, principal_subject
              )::text)
          ) AS keys(key_value)
         ORDER BY key_value
    LOOP
        PERFORM pg_catalog.pg_advisory_xact_lock_shared(
            pg_catalog.hashtextextended('celestin-auth-v1:' || lock_key, 0)
        );
    END LOOP;
END;
"""

_ADMIN_AUTHORIZATION_WRITE_FUNCTION_BODIES = {
    "celestin_lock_authorization_write(uuid,text,text,text)": """
DECLARE
    lock_key TEXT;
BEGIN
    FOR lock_key IN
        SELECT DISTINCT key_value
          FROM (
              SELECT pg_catalog.jsonb_build_array('workspace', target_workspace)::text AS key_value
              UNION ALL
              SELECT pg_catalog.jsonb_build_array(
                  'principal', principal_kind, principal_issuer, principal_subject
              )::text
               WHERE principal_kind IS NOT NULL
          ) AS keys
         ORDER BY key_value
    LOOP
        PERFORM pg_catalog.pg_advisory_xact_lock(
            pg_catalog.hashtextextended('celestin-auth-v1:' || lock_key, 0)
        );
    END LOOP;
END;
""",
    "celestin_lock_workspace_authorization_write(uuid)": """
    SELECT pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(
            'celestin-auth-v1:' || pg_catalog.jsonb_build_array('workspace', target_workspace)::text,
            0
        )
    )
""",
    "celestin_lock_principal_authorization_write(text,text,text)": """
    SELECT pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(
            'celestin-auth-v1:' || pg_catalog.jsonb_build_array(
                'principal', principal_kind, principal_issuer, principal_subject
            )::text,
            0
        )
    )
""",
    "celestin_lock_principal_transition_write(text,text,text,text)": """
DECLARE
    lock_key TEXT;
BEGIN
    FOR lock_key IN
        SELECT DISTINCT pg_catalog.jsonb_build_array('principal', 'human', issuer, subject)::text
          FROM (VALUES (old_issuer, old_subject), (new_issuer, new_subject)) AS ids(issuer, subject)
         WHERE issuer IS NOT NULL AND subject IS NOT NULL
         ORDER BY 1
    LOOP
        PERFORM pg_catalog.pg_advisory_xact_lock(
            pg_catalog.hashtextextended('celestin-auth-v1:' || lock_key, 0)
        );
    END LOOP;
END;
""",
}

# These request/admin wrappers accept caller-selected workspace or principal
# keys and pass them to advisory locks without a database-verifiable binding.
# SECURITY DEFINER alone would turn those arguments into an arbitrary lock-key
# capability. Keep their readiness closed until the interface derives or
# validates lock scope against trusted database state.
_UNVERIFIED_LOCK_KEY_SCOPE_SIGNATURES = frozenset({
    "celestin_lock_authorization_request(uuid,text,text,text)",
    *_ADMIN_AUTHORIZATION_WRITE_FUNCTION_BODIES,
})

# Request pools must not acquire arbitrary PostgreSQL advisory locks directly.
# The lock wrappers must be SECURITY DEFINER in a reviewed forward migration;
# the trusted database-owner identity keeps only the runner pair plus the two
# transaction-lock primitives needed by its owned security-definer wrappers.
_ADVISORY_LOCK_ALLOWED_SIGNATURES = {
    "project_runtime": frozenset(),
    "authorization_reader": frozenset(),
    "authorization_admin": frozenset(),
    "migration": frozenset({
        "pg_advisory_lock(bigint)",
        "pg_advisory_unlock(bigint)",
        # The database-owner role owns the reviewed SECURITY DEFINER lock
        # wrappers and therefore needs only the two transaction-lock primitives
        # used by those wrappers, in addition to the migration runner pair.
        "pg_advisory_xact_lock(bigint)",
        "pg_advisory_xact_lock_shared(bigint)",
    }),
}
_ADVISORY_LOCK_SIGNATURES = frozenset({
    "pg_advisory_lock(bigint)",
    "pg_advisory_lock(integer,integer)",
    "pg_advisory_lock_shared(bigint)",
    "pg_advisory_lock_shared(integer,integer)",
    "pg_advisory_unlock(bigint)",
    "pg_advisory_unlock(integer,integer)",
    "pg_advisory_unlock_shared(bigint)",
    "pg_advisory_unlock_shared(integer,integer)",
    "pg_advisory_unlock_all()",
    "pg_advisory_xact_lock(bigint)",
    "pg_advisory_xact_lock(integer,integer)",
    "pg_advisory_xact_lock_shared(bigint)",
    "pg_advisory_xact_lock_shared(integer,integer)",
    "pg_try_advisory_lock(bigint)",
    "pg_try_advisory_lock(integer,integer)",
    "pg_try_advisory_lock_shared(bigint)",
    "pg_try_advisory_lock_shared(integer,integer)",
    "pg_try_advisory_xact_lock(bigint)",
    "pg_try_advisory_xact_lock(integer,integer)",
    "pg_try_advisory_xact_lock_shared(bigint)",
    "pg_try_advisory_xact_lock_shared(integer,integer)",
})

_WORKSPACE_AUTH_TRIGGER_FUNCTION_BODY = """
DECLARE
    old_workspace UUID;
    new_workspace UUID;
    target_workspace UUID;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        IF TG_TABLE_NAME = 'celestin_workspaces' THEN
            old_workspace := (to_jsonb(OLD)->>'id')::uuid;
        ELSE
            old_workspace := (to_jsonb(OLD)->>'workspace_id')::uuid;
        END IF;
    END IF;
    IF TG_OP <> 'DELETE' THEN
        IF TG_TABLE_NAME = 'celestin_workspaces' THEN
            new_workspace := (to_jsonb(NEW)->>'id')::uuid;
        ELSE
            new_workspace := (to_jsonb(NEW)->>'workspace_id')::uuid;
        END IF;
    END IF;
    FOR target_workspace IN
        SELECT DISTINCT workspace_id
          FROM unnest(ARRAY[old_workspace, new_workspace]) AS ids(workspace_id)
         WHERE workspace_id IS NOT NULL
         ORDER BY workspace_id::text
    LOOP
        PERFORM public.celestin_lock_workspace_authorization_write(target_workspace);
    END LOOP;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
"""
_HUMAN_AUTH_TRIGGER_FUNCTION_BODY = """
DECLARE
    lock_key TEXT;
BEGIN
    FOR lock_key IN
        SELECT DISTINCT key_value
          FROM (
              SELECT pg_catalog.jsonb_build_array(
                         'principal', 'human', OLD.issuer, OLD.subject
                     )::text AS key_value
               WHERE OLD.issuer IS NOT NULL AND OLD.subject IS NOT NULL
              UNION ALL
              SELECT pg_catalog.jsonb_build_array(
                         'principal', 'human', NEW.issuer, NEW.subject
                     )::text
               WHERE NEW.issuer IS NOT NULL AND NEW.subject IS NOT NULL
              UNION ALL
              SELECT pg_catalog.jsonb_build_array(
                         'workspace', affected.workspace_id
                     )::text
                FROM (
                    SELECT membership.workspace_id
                      FROM public.celestin_workspace_memberships AS membership
                     WHERE membership.user_id IN (OLD.id, NEW.id)
                    UNION
                    SELECT membership.workspace_id
                      FROM public.celestin_project_memberships AS membership
                     WHERE membership.user_id IN (OLD.id, NEW.id)
                ) AS affected
          ) AS lock_keys
         ORDER BY key_value
    LOOP
        PERFORM pg_catalog.pg_advisory_xact_lock(
            pg_catalog.hashtextextended('celestin-auth-v1:' || lock_key, 0)
        );
    END LOOP;
    RETURN NEW;
END;
"""


def _trigger_specs():
    """Exact authorization lock-trigger attachments accepted by readiness."""
    row_before_insert_update_delete = 1 | 2 | 4 | 8 | 16
    row_before_update = 1 | 2 | 16
    specs = {}
    for table, trigger in (
        ("celestin_workspaces", "celestin_workspace_auth_lock"),
        ("celestin_projects", "celestin_project_auth_lock"),
        ("celestin_workspace_memberships", "celestin_workspace_membership_auth_lock"),
        ("celestin_project_memberships", "celestin_project_membership_auth_lock"),
        ("celestin_service_identities", "celestin_service_identity_auth_lock"),
        ("celestin_service_project_grants", "celestin_service_grant_auth_lock"),
    ):
        specs[("public", table, trigger)] = (
            row_before_insert_update_delete, (), "celestin_lock_workspace_auth_row",
        )
    specs[(
        "celestin_private", "project_runtime_scopes", "celestin_project_scope_auth_lock",
    )] = (
        row_before_insert_update_delete, (), "celestin_lock_workspace_auth_row",
    )
    specs[("public", "celestin_users", "celestin_user_identity_auth_lock")] = (
        row_before_update, tuple(sorted(("status", "issuer", "subject"))),
        "celestin_lock_human_identity_auth_row",
    )
    return specs


def _trigger_function_specs():
    return {
        "celestin_lock_workspace_auth_row": (
            ("search_path=pg_catalog, pg_temp",),
            _WORKSPACE_AUTH_TRIGGER_FUNCTION_BODY,
        ),
        "celestin_lock_human_identity_auth_row": (
            ("search_path=pg_catalog, pg_temp",),
            _HUMAN_AUTH_TRIGGER_FUNCTION_BODY,
        ),
    }


class HostedRuntimeConfigurationError(ValueError):
    """Hosted mode is missing a required non-secret configuration value."""


@dataclass(frozen=True, slots=True)
class HostedRuntimeSettings:
    authorization_dsn: str = field(repr=False)
    project_dsn_env_by_scope: Mapping[tuple[UUID, UUID], str] = field(repr=False)
    auth: Auth0VerifierConfig
    public_url: str
    tls_certfile: Path
    tls_keyfile: Path
    project_role_isolation_enabled: bool = False
    bind_host: str = "127.0.0.1"
    port: int = 8765
    control_pool_min_size: int = 1
    control_pool_max_size: int = 4
    project_pool_max_size: int = 2
    pool_timeout: float = 5.0
    connection_timeout: int = 5
    statement_timeout_ms: int = 10_000
    allow_non_loopback: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.authorization_dsn, str) or not self.authorization_dsn.strip():
            raise HostedRuntimeConfigurationError(
                "CELESTIN_POSTGRES_AUTHORIZATION_DSN is required."
            )
        if type(self.project_role_isolation_enabled) is not bool or not self.project_role_isolation_enabled:
            raise HostedRuntimeConfigurationError(
                "Hosted project-role isolation is disabled until its live security gate is approved."
            )
        if not isinstance(self.project_dsn_env_by_scope, Mapping) or not self.project_dsn_env_by_scope:
            raise HostedRuntimeConfigurationError(
                "At least one project-scoped PostgreSQL pool must be configured."
            )
        if len(self.project_dsn_env_by_scope) > _MAX_CONFIGURED_PROJECT_POOLS:
            raise HostedRuntimeConfigurationError(
                "Hosted project pool configuration exceeds its fixed safety bound."
            )
        if len(set(self.project_dsn_env_by_scope.values())) != len(self.project_dsn_env_by_scope):
            raise HostedRuntimeConfigurationError(
                "Each project scope must use a distinct project DSN environment variable."
            )
        for scope_key, env_name in self.project_dsn_env_by_scope.items():
            if (
                not isinstance(scope_key, tuple)
                or len(scope_key) != 2
                or not all(isinstance(value, UUID) for value in scope_key)
                or not isinstance(env_name, str)
                or not _PROJECT_DSN_ENV_NAME.fullmatch(env_name)
            ):
                raise HostedRuntimeConfigurationError(
                    "Project pool routing must map UUID workspace/project pairs to approved DSN variable names."
                )
        public = urlsplit(self.public_url)
        if (
            public.scheme != "https" or not public.hostname
            or public.username is not None or public.password is not None
            or public.path not in ("", "/") or public.query or public.fragment
        ):
            raise HostedRuntimeConfigurationError(
                "CELESTIN_HOSTED_PUBLIC_URL must be an HTTPS URL without credentials, query, or fragment."
            )
        if not self.tls_certfile.is_file() or not self.tls_keyfile.is_file():
            raise HostedRuntimeConfigurationError(
                "Hosted mode requires readable TLS certificate and key files."
            )
        if not isinstance(self.bind_host, str) or not self.bind_host.strip():
            raise HostedRuntimeConfigurationError("Hosted bind address is invalid.")
        if type(self.allow_non_loopback) is not bool:
            raise HostedRuntimeConfigurationError("Non-loopback bind approval must be an explicit boolean.")
        if not self.allow_non_loopback and not _is_loopback_bind(self.bind_host):
            raise HostedRuntimeConfigurationError(
                "Hosted mode binds to loopback unless a reviewed deployment explicitly enables non-loopback binding."
            )
        if isinstance(self.port, bool) or not isinstance(self.port, int) or not 1 <= self.port <= 65535:
            raise HostedRuntimeConfigurationError("Hosted port must be between 1 and 65535.")
        if (
            isinstance(self.control_pool_min_size, bool)
            or not isinstance(self.control_pool_min_size, int)
            or isinstance(self.control_pool_max_size, bool)
            or not isinstance(self.control_pool_max_size, int)
            or self.control_pool_min_size < 0
            or self.control_pool_max_size < 1
            or self.control_pool_min_size > self.control_pool_max_size
            or self.control_pool_max_size > 4
            or isinstance(self.project_pool_max_size, bool)
            or not isinstance(self.project_pool_max_size, int)
            or not 1 <= self.project_pool_max_size <= 2
        ):
            raise HostedRuntimeConfigurationError(
                "PostgreSQL pool sizes exceed their safety bounds."
            )
        if (
            isinstance(self.connection_timeout, bool)
            or not isinstance(self.connection_timeout, int)
            or self.connection_timeout < 1
            or isinstance(self.statement_timeout_ms, bool)
            or not isinstance(self.statement_timeout_ms, int)
            or self.statement_timeout_ms < 1
        ):
            raise HostedRuntimeConfigurationError("PostgreSQL connection/query timeouts must be positive integers.")

    @classmethod
    def from_environment(cls) -> HostedRuntimeSettings:
        def positive_int(name: str, default: int) -> int:
            raw = os.environ.get(name, str(default))
            try:
                value = int(raw)
            except (ValueError, TypeError):
                raise HostedRuntimeConfigurationError(f"{name} must be an integer.") from None
            return value

        raw_timeout = os.environ.get("CELESTIN_HOSTED_DB_POOL_TIMEOUT", "5")
        try:
            timeout = float(raw_timeout)
        except (TypeError, ValueError):
            raise HostedRuntimeConfigurationError(
                "CELESTIN_HOSTED_DB_POOL_TIMEOUT must be a positive number."
            ) from None
        if timeout <= 0:
            raise HostedRuntimeConfigurationError(
                "CELESTIN_HOSTED_DB_POOL_TIMEOUT must be a positive number."
            )
        authorization_dsn = os.environ.get(
            "CELESTIN_POSTGRES_AUTHORIZATION_DSN", ""
        )
        raw_project_dsns = os.environ.get(
            "CELESTIN_HOSTED_PROJECT_DSN_ENV_MAP", ""
        )
        if not raw_project_dsns:
            raise HostedRuntimeConfigurationError(
                "CELESTIN_HOSTED_PROJECT_DSN_ENV_MAP is required for project-isolated pools."
            )
        try:
            raw_map = json.loads(raw_project_dsns)
        except (TypeError, ValueError):
            raise HostedRuntimeConfigurationError(
                "CELESTIN_HOSTED_PROJECT_DSN_ENV_MAP must be a JSON object."
            ) from None
        if not isinstance(raw_map, dict):
            raise HostedRuntimeConfigurationError(
                "CELESTIN_HOSTED_PROJECT_DSN_ENV_MAP must be a JSON object."
            )
        project_dsns: dict[tuple[UUID, UUID], str] = {}
        for key, env_name in raw_map.items():
            if not isinstance(key, str) or key.count("/") != 1:
                raise HostedRuntimeConfigurationError(
                    "Project pool keys must be canonical workspace UUID/project UUID pairs."
                )
            workspace_text, project_text = key.split("/", 1)
            try:
                workspace_id, project_id = UUID(workspace_text), UUID(project_text)
            except (ValueError, AttributeError):
                raise HostedRuntimeConfigurationError(
                    "Project pool keys must be canonical workspace UUID/project UUID pairs."
                ) from None
            if f"{workspace_id}/{project_id}" != key or (workspace_id, project_id) in project_dsns:
                raise HostedRuntimeConfigurationError(
                    "Project pool keys must be unique canonical UUID pairs."
                )
            if not isinstance(env_name, str) or not _PROJECT_DSN_ENV_NAME.fullmatch(env_name):
                raise HostedRuntimeConfigurationError(
                    "Project pool values must name CELESTIN_PROJECT_POSTGRES_DSN_* variables."
                )
            if not os.environ.get(env_name, "").strip():
                raise HostedRuntimeConfigurationError(
                    "A configured project PostgreSQL DSN environment variable is empty."
                )
            project_dsns[(workspace_id, project_id)] = env_name
        isolation_enabled = os.environ.get(
            "CELESTIN_HOSTED_PROJECT_ROLE_ISOLATION_ENABLED", "false"
        ).casefold() == "true"
        public_url = os.environ.get("CELESTIN_HOSTED_PUBLIC_URL", "")
        cert = os.environ.get("CELESTIN_HOSTED_TLS_CERTFILE", "")
        key = os.environ.get("CELESTIN_HOSTED_TLS_KEYFILE", "")
        if not authorization_dsn or not public_url or not cert or not key:
            raise HostedRuntimeConfigurationError(
                "Hosted mode requires the authorization PostgreSQL DSN, public HTTPS URL, and TLS certificate/key paths."
            )
        return cls(
            authorization_dsn=authorization_dsn,
            project_dsn_env_by_scope=project_dsns,
            auth=Auth0VerifierConfig.from_environment(),
            public_url=public_url,
            tls_certfile=Path(cert),
            tls_keyfile=Path(key),
            project_role_isolation_enabled=isolation_enabled,
            bind_host=os.environ.get("CELESTIN_HOSTED_BIND", "127.0.0.1"),
            port=positive_int("CELESTIN_HOSTED_PORT", 8765),
            control_pool_min_size=positive_int("CELESTIN_HOSTED_CONTROL_POOL_MIN_SIZE", 1),
            control_pool_max_size=positive_int("CELESTIN_HOSTED_CONTROL_POOL_MAX_SIZE", 4),
            project_pool_max_size=positive_int("CELESTIN_HOSTED_PROJECT_POOL_MAX_SIZE", 2),
            pool_timeout=timeout,
            connection_timeout=positive_int("CELESTIN_HOSTED_DB_CONNECT_TIMEOUT", 5),
            statement_timeout_ms=positive_int("CELESTIN_HOSTED_DB_STATEMENT_TIMEOUT_MS", 10_000),
            allow_non_loopback=os.environ.get("CELESTIN_HOSTED_ALLOW_NON_LOOPBACK", "false").lower() == "true",
        )


class ExplicitWorkspaceBinding(WorkspaceBinding):
    """Return only the explicit request selector; PostgreSQL validates access."""

    def workspace_for(
        self,
        principal: VerifiedPrincipal,
        requested_workspace_id=None,
    ):
        if not isinstance(principal, VerifiedPrincipal):
            return None
        return requested_workspace_id


class _DatabasePrincipalStatus(PrincipalStatusProvider):
    """Force principal status to be resolved by the request's locked PG session."""

    def is_active(self, principal: VerifiedPrincipal) -> bool:
        return False


class _PoolConnectionLease:
    """A closeable request lease whose close returns it to psycopg_pool."""

    def __init__(self, pool, timeout: float) -> None:
        self._pool = pool
        self._connection = pool.getconn(timeout=timeout)
        self._closed = False

    def __getattr__(self, name: str):
        return getattr(self._connection, name)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._pool.putconn(self._connection)


class HostedDatabasePools:
    """Fixed, bounded pool registry for one control login and project logins."""

    def __init__(
        self,
        control_pool: Any,
        project_pools: Mapping[tuple[UUID, UUID], Any],
        *,
        timeout: float,
    ) -> None:
        if not isinstance(project_pools, Mapping) or not project_pools:
            raise ValueError("project database pools are required")
        if len(project_pools) > _MAX_CONFIGURED_PROJECT_POOLS:
            raise ValueError("project database pool count exceeds the safety bound")
        self.control_pool = control_pool
        self.project_pools = dict(project_pools)
        self.timeout = timeout

    def project_connection(self, scope: ResolvedProjectScope):
        """Return a lease only for the exact resolver-issued pair; never fallback."""
        if not isinstance(scope, ResolvedProjectScope):
            raise TypeError("project pools require a resolver-issued scope")
        pool = self.project_pools.get((scope.workspace_id, scope.project_id))
        if pool is None:
            raise HostedAuthorizationUnavailable("project database is unavailable")
        return _PoolConnectionLease(pool, self.timeout)

    def open(self) -> None:
        opened = []
        try:
            for pool in self.all_pools:
                # Include the pool before opening it: some implementations can
                # allocate resources before reporting an open failure.
                opened.append(pool)
                pool.open(wait=True, timeout=self.timeout)
        except Exception:
            for pool in reversed(opened):
                try:
                    pool.close()
                except Exception:
                    pass
            raise

    def close(self) -> None:
        first_error = None
        for pool in reversed(self.all_pools):
            try:
                pool.close()
            except Exception as exc:
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise RuntimeError("a hosted PostgreSQL pool could not be closed") from None

    @property
    def all_pools(self) -> tuple[Any, ...]:
        return (self.control_pool, *self.project_pools.values())


class HostedMCPServices:
    """Existing MCP operations composed through Auth0, PostgreSQL and one UoW."""

    def __init__(
        self,
        boundary: HostedRequestBoundary,
        *,
        read_scope: str,
        write_scope: str,
    ) -> None:
        if read_scope != READ_SCOPE or write_scope != WRITE_SCOPE:
            raise ValueError("Hosted MCP scope mapping must use memory:read and memory:write.")
        self._boundary = boundary
        self._required_scopes = {"read": READ_SCOPE, "write": WRITE_SCOPE}

    def _execute(self, project: str, action: str, operation, *, source_message_id=None):
        token = _access_token.get()
        required_scope = self._required_scopes[action]
        if token is None or required_scope not in getattr(token, "scopes", ()):
            raise _error("ACCESS_DENIED", "Access to this project is not permitted.")
        try:
            return self._boundary.execute_authorized_request(
                token,
                project,
                action,
                operation,
                workspace_selector=_workspace_selector.get(),
                source_message_id=source_message_id,
            )
        except HostedAccessDenied:
            raise _error("ACCESS_DENIED", "Access to this project is not permitted.") from None
        except HostedSourceMessageNotFound:
            raise _error(
                "SOURCE_MESSAGE_NOT_FOUND",
                "The source message was not found in this project.",
            ) from None
        except HostedAuthorizationUnavailable:
            logger.error("hosted authorization or database request failed")
            raise _error("STORAGE_ERROR", "The request could not be completed.") from None
        except EvidenceReferenceMissing:
            raise _error("SOURCE_MESSAGE_NOT_FOUND", "The source was not found in this project.") from None
        except KnowledgeNotFound:
            raise _error("KNOWLEDGE_NOT_FOUND", "The requested knowledge was not found.") from None
        except EvidenceNotAssociated:
            raise _error("EVIDENCE_NOT_FOUND", "The requested evidence was not found.") from None
        except (EvidenceValidationError, ValueError, TypeError):
            raise _error("INVALID_ARGUMENT", "Evidence arguments are invalid.") from None
        except Exception as exc:
            logger.error("hosted operation failed (%s)", type(exc).__name__)
            code = "RETRIEVAL_UNAVAILABLE" if action == "read" else "STORAGE_ERROR"
            message = "The retrieval service is unavailable." if action == "read" else "The request could not be completed."
            raise _error(code, message) from None

    def ingest_chat_message(self, project: str, conversation_id: str, role: str, content: str):
        def operation(request: PostgresRequestUnitOfWork):
            message = request.memory_store.insert_message(project, conversation_id, role, content)
            return {
                "project": message.project,
                "message_id": message.message_id,
                "conversation_id": message.conversation_id,
                "role": message.role,
                "content": message.content,
                "created_at": message.created_at,
            }

        return self._execute(project, "write", operation)

    def save_decision(self, project: str, key: str, value: str, source_message_id: str):
        def operation(request: PostgresRequestUnitOfWork):
            decision = request.memory_store.save_decision(
                project, key, value, source_message_id, status="draft"
            )
            return {
                "project": decision.project,
                "decision_id": decision.decision_id,
                "key": decision.key,
                "value": decision.value,
                "status": decision.status,
                "source_message_id": decision.source_message_id,
                "created_at": decision.created_at,
                "updated_at": decision.updated_at,
            }

        return self._execute(
            project,
            "write",
            operation,
            source_message_id=source_message_id,
        )

    def search_chat_history(self, project: str, query: str, limit: int):
        def operation(request: PostgresRequestUnitOfWork):
            results = request.retrieval_service.search_chat_history(project, query, limit)
            return {
                "project": project,
                "query": query,
                "results": [_search_result_dict(result) for result in results],
            }

        return self._execute(project, "read", operation)

    def retrieve_context(self, project: str, query: str, token_budget: int):
        def operation(request: PostgresRequestUnitOfWork):
            service = request.retrieval_service
            bundle = service.retrieve_context(project, query, token_budget)

            def public_record(record: Any) -> dict[str, Any]:
                source_message_id = (
                    record.record_id
                    if record.record_kind == "chat_message"
                    else record.source_id
                )
                source = service.last_sources.get(record.record_id)
                if source is not None:
                    source_message_id = None
                result = {
                    "record_id": record.record_id,
                    "source_message_id": source_message_id,
                    "created_at": record.timestamp,
                    "record_kind": record.record_kind,
                    "status": record.status,
                }
                if source is not None:
                    result["source"] = source
                if hasattr(record, "content"):
                    result["content"] = record.content
                return result

            response = {
                "project": project,
                "query": query,
                "items": [
                    {**public_record(item), "relevance_score": item.relevance_score}
                    for item in bundle.items
                ],
                "rendered_context": bundle.rendered_context,
                "token_count": bundle.token_count,
                "token_budget": bundle.token_budget,
                "token_count_is_estimate": bundle.token_count_is_estimate,
                "token_counter": bundle.token_counter,
                "excluded": [
                    {**public_record(item), "reason": item.reason}
                    for item in bundle.excluded
                    if item.reason in {"token_budget", "duplicate_content"}
                ],
            }
            if service.semantic_enabled or service.documents_enabled:
                response["retrieval"] = service.last_retrieval.as_dict()
            return response

        return self._execute(project, "read", operation)

    def create_knowledge(self, project: str, knowledge_type: str, claim: str, evidence_sources: list[dict[str, Any]]):
        sources = _evidence_sources(evidence_sources)
        def operation(request: PostgresRequestUnitOfWork):
            bundle = request.evidence_service.create_knowledge(
                request.evidence_project("write"), KnowledgeType(knowledge_type), claim, sources
            )
            return _knowledge_bundle_dict(bundle)
        return self._execute(project, "write", operation)

    def get_knowledge(self, project: str, knowledge_id: str):
        def operation(request: PostgresRequestUnitOfWork):
            return _knowledge_bundle_dict(request.evidence_service.get_knowledge(
                request.evidence_project("read"), knowledge_id
            ))
        return self._execute(project, "read", operation)

    def associate_evidence(self, project: str, knowledge_id: str, evidence_source: dict[str, Any]):
        source = _evidence_sources([evidence_source])[0]
        def operation(request: PostgresRequestUnitOfWork):
            return _evidence_record_dict(request.evidence_service.associate_evidence(
                request.evidence_project("write"), knowledge_id, source
            ))
        return self._execute(project, "write", operation)

    def verify_knowledge(self, project: str, knowledge_id: str, status: str, note: str | None = None):
        def operation(request: PostgresRequestUnitOfWork):
            return _knowledge_record_dict(request.evidence_service.set_knowledge_verification(
                request.evidence_project("write"), knowledge_id, VerificationStatus(status),
                checked_by=_authenticated_subject(), note=note
            ))
        return self._execute(project, "write", operation)

    def verify_evidence(self, project: str, knowledge_id: str, evidence_id: str, status: str, note: str | None = None):
        def operation(request: PostgresRequestUnitOfWork):
            return _evidence_record_dict(request.evidence_service.set_evidence_verification(
                request.evidence_project("write"), knowledge_id, evidence_id,
                VerificationStatus(status), checked_by=_authenticated_subject(), note=note
            ))
        return self._execute(project, "write", operation)

    def supersede_knowledge(self, project: str, previous_id: str, knowledge_type: str, claim: str, evidence_sources: list[dict[str, Any]]):
        sources = _evidence_sources(evidence_sources)
        def operation(request: PostgresRequestUnitOfWork):
            return _knowledge_bundle_dict(request.evidence_service.supersede_knowledge(
                request.evidence_project("write"), previous_id, KnowledgeType(knowledge_type), claim, sources
            ))
        return self._execute(project, "write", operation)


@dataclass(frozen=True, slots=True)
class _HandlerRuntime:
    persistence: HostedMCPServices
    retrieval: HostedMCPServices


class HostedMCPToolHandlers:
    """Async HTTP wrappers that reuse local validation and public MCP schemas."""

    def __init__(self, services: HostedMCPServices) -> None:
        self._services = services
        self._handlers = MCPToolHandlers(_HandlerRuntime(services, services))

    async def _call(self, handler, *args):
        token = _access_token.set(get_access_token())
        try:
            return await asyncio.to_thread(handler, *args)
        finally:
            _access_token.reset(token)

    async def ingest_chat_message(
        self, project: str, conversation_id: str, role: str, content: str
    ) -> dict[str, Any]:
        return await self._call(
            self._handlers.ingest_chat_message, project, conversation_id, role, content
        )

    async def save_decision(
        self, project: str, key: str, value: str, source_message_id: str
    ) -> dict[str, Any]:
        return await self._call(
            self._handlers.save_decision, project, key, value, source_message_id
        )

    async def search_chat_history(
        self, project: str, query: str, limit: int = 10
    ) -> dict[str, Any]:
        return await self._call(self._handlers.search_chat_history, project, query, limit)

    async def retrieve_context(
        self, project: str, query: str, token_budget: int
    ) -> dict[str, Any]:
        return await self._call(
            self._handlers.retrieve_context, project, query, token_budget
        )

    async def ping(self) -> str:
        return await self._call(self._handlers.ping)

    async def create_knowledge(self, project: str, knowledge_type: str, claim: str, evidence_sources: list[dict[str, Any]]):
        return await self._call(self._services.create_knowledge, project, knowledge_type, claim, evidence_sources)

    async def get_knowledge(self, project: str, knowledge_id: str):
        return await self._call(self._services.get_knowledge, project, knowledge_id)

    async def associate_evidence(self, project: str, knowledge_id: str, evidence_source: dict[str, Any]):
        return await self._call(self._services.associate_evidence, project, knowledge_id, evidence_source)

    async def verify_knowledge(self, project: str, knowledge_id: str, status: str, note: str | None = None):
        return await self._call(self._services.verify_knowledge, project, knowledge_id, status, note)

    async def verify_evidence(self, project: str, knowledge_id: str, evidence_id: str, status: str, note: str | None = None):
        return await self._call(self._services.verify_evidence, project, knowledge_id, evidence_id, status, note)

    async def supersede_knowledge(self, project: str, previous_id: str, knowledge_type: str, claim: str, evidence_sources: list[dict[str, Any]]):
        return await self._call(self._services.supersede_knowledge, project, previous_id, knowledge_type, claim, evidence_sources)


def _register_hosted_evidence_tools(mcp: FastMCP, handlers: HostedMCPToolHandlers) -> None:
    """Add authenticated hosted-only Evidence tools without changing stdio contracts."""
    mcp.tool(description="Create project knowledge linked to existing evidence sources.")(handlers.create_knowledge)
    mcp.tool(description="Read project knowledge and its linked evidence.")(handlers.get_knowledge)
    mcp.tool(description="Link an existing evidence source to project knowledge.")(handlers.associate_evidence)
    mcp.tool(description="Set project knowledge verification status.")(handlers.verify_knowledge)
    mcp.tool(description="Set linked evidence verification status.")(handlers.verify_evidence)
    mcp.tool(description="Supersede project knowledge with a linked successor.")(handlers.supersede_knowledge)


def _evidence_sources(values: list[dict[str, Any]]) -> tuple[EvidenceSource, ...]:
    if not isinstance(values, list) or not values:
        raise _error("INVALID_ARGUMENT", "At least one evidence source is required.")
    fields = {"evidence_type", "source_id", "locator", "revision", "content_hash"}
    try:
        result = []
        for value in values:
            if not isinstance(value, dict) or set(value) - fields or not {"evidence_type", "source_id"} <= set(value):
                raise ValueError
            result.append(EvidenceSource(
                EvidenceType(value["evidence_type"]), value["source_id"],
                value.get("locator"), value.get("revision"), value.get("content_hash"),
            ))
        return tuple(result)
    except (TypeError, ValueError):
        raise _error("INVALID_ARGUMENT", "Evidence source fields are invalid.") from None


def _authenticated_subject() -> str:
    token = _access_token.get()
    claims = getattr(token, "claims", {}) if token is not None else {}
    subject = claims.get("sub") if isinstance(claims, Mapping) else None
    if not isinstance(subject, str) or not subject:
        raise _error("ACCESS_DENIED", "Authenticated principal is unavailable.")
    return subject


def _knowledge_record_dict(record) -> dict[str, Any]:
    return asdict(record)


def _evidence_record_dict(record) -> dict[str, Any]:
    return asdict(record)


def _knowledge_bundle_dict(bundle) -> dict[str, Any]:
    return {"record": _knowledge_record_dict(bundle.record),
            "evidence": [_evidence_record_dict(item) for item in bundle.evidence]}


class WorkspaceContextMiddleware:
    """Carry the untrusted explicit workspace header in request-local context."""

    def __init__(self, app) -> None:
        self._app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self._app(scope, receive, send)
            return
        values = tuple(
            value.decode("ascii", errors="replace")
            for name, value in scope.get("headers", ())
            if name.lower() == WORKSPACE_HEADER
        )
        token = _workspace_selector.set(values if values else None)
        try:
            await self._app(scope, receive, send)
        finally:
            _workspace_selector.reset(token)


def create_hosted_mcp_app(
    settings: HostedRuntimeSettings,
    *,
    pools: HostedDatabasePools | None = None,
    token_verifier=None,
):
    """Compose an HTTPS-only Streamable HTTP app; construction does no DB I/O."""
    mcp = create_hosted_mcp_server(
        settings, pools=pools, token_verifier=token_verifier
    )
    return WorkspaceContextMiddleware(mcp.streamable_http_app())


def create_hosted_mcp_server(
    settings: HostedRuntimeSettings,
    *,
    pools: HostedDatabasePools | None = None,
    token_verifier=None,
) -> FastMCP:
    """Build the authenticated hosted FastMCP composition without opening DB I/O."""
    if not settings.project_role_isolation_enabled:
        raise HostedRuntimeConfigurationError(
            "Hosted project-role isolation is disabled until its live security gate is approved."
        )
    if pools is None:
        pools = _create_pools(settings)
    verifier = token_verifier or Auth0TokenVerifier(settings.auth)
    session_factory = ProjectScopedPostgresHostedAuthorizationSessionFactory(
        lambda: _PoolConnectionLease(pools.control_pool, settings.pool_timeout),
        pools.project_connection,
        statement_timeout_ms=settings.statement_timeout_ms,
    )
    boundary = HostedRequestBoundary(
        authenticator=Auth0AccessTokenAuthenticator(),
        workspace_binding=ExplicitWorkspaceBinding(),
        principal_status=_DatabasePrincipalStatus(),
        request_executor=session_factory,
    )
    services = HostedMCPServices(
        boundary,
        read_scope=settings.auth.read_scope,
        write_scope=settings.auth.write_scope,
    )

    @asynccontextmanager
    async def lifespan(_server):
        await asyncio.to_thread(pools.open)
        try:
            await asyncio.to_thread(_assert_safe_hosted_pools, pools, settings.pool_timeout)
            yield
        finally:
            await asyncio.to_thread(pools.close)

    mcp = FastMCP(
        "Celestin Hosted",
        token_verifier=verifier,
        auth=AuthSettings(
            issuer_url=settings.auth.issuer_url,
            resource_server_url=f"{settings.public_url.rstrip('/')}/mcp",
            validate_token_resource=False,
        ),
        host=settings.bind_host,
        port=settings.port,
        streamable_http_path="/mcp",
        max_request_body_size=2_097_152,
        lifespan=lifespan,
    )
    handlers = HostedMCPToolHandlers(services)
    register_mcp_tools(mcp, handlers)
    _register_hosted_evidence_tools(mcp, handlers)

    async def healthz(_request: Request):
        return JSONResponse({"status": "ok"})

    async def readyz(_request: Request):
        try:
            await asyncio.to_thread(_assert_safe_hosted_pools, pools, settings.pool_timeout)
        except Exception as exc:
            logger.error("hosted readiness check failed (%s)", type(exc).__name__)
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return JSONResponse({"status": "ready"})

    mcp.custom_route("/healthz", methods=["GET"], include_in_schema=False)(healthz)
    mcp.custom_route("/readyz", methods=["GET"], include_in_schema=False)(readyz)
    return mcp


def _reset_postgres_pool_connection(connection) -> None:
    """Clear session state before a pooled PostgreSQL connection is reused.

    A failed reset must propagate so psycopg-pool discards the connection. The
    runtime deliberately disables automatic server-side preparation because
    ``DISCARD ALL`` deallocates PostgreSQL prepared statements.
    """
    connection.rollback()
    previous_autocommit = connection.autocommit
    cursor = None
    try:
        connection.autocommit = True
        cursor = connection.execute("DISCARD ALL")
    finally:
        try:
            if cursor is not None:
                cursor.close()
        finally:
            connection.autocommit = previous_autocommit


def _create_pool(
    conninfo: str,
    settings: HostedRuntimeSettings,
    *,
    min_size: int,
    max_size: int,
):
    try:
        from psycopg_pool import ConnectionPool
    except ImportError as exc:  # pragma: no cover - optional hosted extra
        raise RuntimeError(
            "Hosted PostgreSQL runtime requires the hosted optional dependencies."
        ) from exc
    return ConnectionPool(
        conninfo=conninfo,
        kwargs={
            "connect_timeout": settings.connection_timeout,
            "prepare_threshold": None,
        },
        min_size=min_size,
        max_size=max_size,
        timeout=settings.pool_timeout,
        reset=_reset_postgres_pool_connection,
        open=False,
    )


def _create_pools(settings: HostedRuntimeSettings) -> HostedDatabasePools:
    control_pool = _create_pool(
        settings.authorization_dsn,
        settings,
        min_size=settings.control_pool_min_size,
        max_size=settings.control_pool_max_size,
    )
    project_pools: dict[tuple[UUID, UUID], Any] = {}
    for scope, env_name in settings.project_dsn_env_by_scope.items():
        dsn = os.environ.get(env_name, "")
        if not dsn.strip():
            raise HostedRuntimeConfigurationError(
                "A configured project PostgreSQL DSN environment variable is empty."
            )
        project_pools[scope] = _create_pool(
            dsn,
            settings,
            min_size=0,
            max_size=settings.project_pool_max_size,
        )
    return HostedDatabasePools(
        control_pool,
        project_pools,
        timeout=settings.pool_timeout,
    )


def _is_loopback_bind(host: str) -> bool:
    import ipaddress

    if host.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _assert_safe_hosted_pools(pools: HostedDatabasePools, timeout: float) -> None:
    _assert_safe_authorization_reader(pools.control_pool, timeout)
    for scope, pool in pools.project_pools.items():
        _assert_safe_project_runtime(pool, timeout, scope)


def _policy_spec(name, command, role, using, check=None):
    return (name, command, True, (role,), using, check)


def _expected_policy_catalog():
    """The only catalog policy definitions accepted by hosted readiness.

    Expressions are compared to PostgreSQL's deparsed expression after only
    whitespace and the optional ``public.`` qualification are normalized.
    Unknown definitions fail closed instead of relying on substring checks.
    """
    relations = {}
    policies = {}

    def add(schema, table, *, force_rls, specs):
        relations[(schema, table)] = ("r", True, force_rls)
        policies[(schema, table)] = tuple(specs)

    for table in PROTECTED_TABLES:
        add(
            "public", table, force_rls=True,
            specs=(
                _policy_spec(
                    "celestin_project_request_scope", "*", PROJECT_RUNTIME_ROLE,
                    _PROJECT_SCOPE_EXPRESSION, _PROJECT_SCOPE_EXPRESSION,
                ),
            ),
        )

    project_policies = {
        "celestin_workspaces": _policy_spec(
            "celestin_project_workspace_read", "r", PROJECT_RUNTIME_ROLE,
            "public.celestin_request_workspace_matches(id)",
        ),
        "celestin_projects": _policy_spec(
            "celestin_project_project_read", "r", PROJECT_RUNTIME_ROLE,
            "public.celestin_request_scope_matches(workspace_id, id)",
        ),
        "celestin_workspace_memberships": _policy_spec(
            "celestin_project_workspace_membership_read", "r", PROJECT_RUNTIME_ROLE,
            """
            public.celestin_request_workspace_matches(workspace_id)
            AND EXISTS (
                SELECT 1 FROM public.celestin_project_memberships AS membership
                 WHERE membership.workspace_id = celestin_workspace_memberships.workspace_id
                   AND membership.project_id = (
                       SELECT scope.project_id
                         FROM celestin_private.project_runtime_scopes AS scope
                        WHERE scope.login_role_name = session_user::name
                   )
                   AND membership.user_id = celestin_workspace_memberships.user_id
            )
            """,
        ),
        "celestin_project_memberships": _policy_spec(
            "celestin_project_membership_read", "r", PROJECT_RUNTIME_ROLE,
            "public.celestin_request_scope_matches(workspace_id, project_id)",
        ),
        "celestin_users": _policy_spec(
            "celestin_project_user_read", "r", PROJECT_RUNTIME_ROLE,
            """
            EXISTS (
                SELECT 1 FROM public.celestin_project_memberships AS membership
                 WHERE membership.user_id = celestin_users.id
                   AND public.celestin_request_scope_matches(
                       membership.workspace_id, membership.project_id
                   )
            )
            """,
        ),
        "celestin_service_identities": _policy_spec(
            "celestin_project_service_identity_read", "r", PROJECT_RUNTIME_ROLE,
            """
            EXISTS (
                SELECT 1 FROM public.celestin_service_project_grants AS grant_row
                 WHERE grant_row.workspace_id = celestin_service_identities.workspace_id
                   AND grant_row.service_identity_id = celestin_service_identities.id
                   AND public.celestin_request_scope_matches(
                       grant_row.workspace_id, grant_row.project_id
                   )
            )
            """,
        ),
        "celestin_service_project_grants": _policy_spec(
            "celestin_project_service_grant_read", "r", PROJECT_RUNTIME_ROLE,
            "public.celestin_request_scope_matches(workspace_id, project_id)",
        ),
    }
    for table in AUTHORIZATION_TABLES:
        specs = [
            _policy_spec(
                "celestin_authorization_reader_select", "r",
                AUTHORIZATION_READER_ROLE, "true",
            ),
            _policy_spec(
                "celestin_authorization_admin_all", "*",
                "celestin_authorization_admin", "true", "true",
            ),
        ]
        if table in project_policies:
            specs.append(project_policies[table])
        add("public", table, force_rls=False, specs=specs)

    add(
        "celestin_private", "project_runtime_scopes", force_rls=False,
        specs=(
            _policy_spec(
                "celestin_project_scope_own_login", "r", PROJECT_RUNTIME_ROLE,
                "login_role_name = session_user::name",
            ),
            _policy_spec(
                "celestin_project_scope_admin_all", "*",
                "celestin_authorization_admin", "true", "true",
            ),
        ),
    )
    return relations, policies


def _canonical_sql_expression(expression: str | None) -> str | None:
    if expression is None:
        return None
    # Ignore formatting and ordinary identifier case, but preserve quoted
    # identifiers and string literals exactly. A plain casefold/whitespace
    # removal would also rewrite string contents and could hide a changed
    # advisory-lock namespace or predicate constant.
    result: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(expression):
        char = expression[index]
        if quote is not None:
            result.append(char)
            if char == quote:
                if index + 1 < len(expression) and expression[index + 1] == quote:
                    result.append(expression[index + 1])
                    index += 1
                else:
                    quote = None
        elif char in ("'", '"'):
            quote = char
            result.append(char)
        elif expression[index:index + 7].casefold() == "public.":
            index += 6
        elif not char.isspace():
            result.append(char.casefold())
        index += 1
    return "".join(result)


def _validate_policy_catalog(rows) -> None:
    expected_relations, expected_policies = _expected_policy_catalog()
    actual_relations = {}
    actual_policies = {}
    try:
        for row in rows:
            (
                schema, table, relkind, row_security, force_row_security,
                policy_name, command, permissive, roles, using_expression,
                check_expression,
            ) = row
            key = (str(schema), str(table))
            relation = (str(relkind), bool(row_security), bool(force_row_security))
            if key in actual_relations and actual_relations[key] != relation:
                raise ValueError("inconsistent relation metadata")
            actual_relations[key] = relation
            if policy_name is not None:
                actual_policies.setdefault(key, []).append(
                    (
                        str(policy_name), str(command), bool(permissive),
                        tuple(sorted(str(role) for role in (roles or ()))),
                        _canonical_sql_expression(using_expression),
                        _canonical_sql_expression(check_expression),
                    )
                )
    except (TypeError, ValueError):
        raise RuntimeError("PostgreSQL RLS catalog does not match the reviewed policy definitions") from None

    if actual_relations != expected_relations:
        raise RuntimeError("PostgreSQL RLS catalog does not match the reviewed policy definitions")
    for relation, expected_specs in expected_policies.items():
        expected = sorted(
            (
                name, command, permissive, tuple(sorted(roles)),
                _canonical_sql_expression(using),
                _canonical_sql_expression(check),
            )
            for name, command, permissive, roles, using, check in expected_specs
        )
        actual = sorted(actual_policies.get(relation, ()))
        if actual != expected:
            raise RuntimeError("PostgreSQL RLS catalog does not match the reviewed policy definitions")


def _policy_catalog_query() -> str:
    return """
        SELECT namespace.nspname, relation.relname, relation.relkind,
               relation.relrowsecurity, relation.relforcerowsecurity,
               policy.polname, policy.polcmd, policy.polpermissive,
               ARRAY(
                   SELECT COALESCE(role.rolname, 'PUBLIC')
                     FROM unnest(policy.polroles) AS policy_role(role_oid)
                     LEFT JOIN pg_catalog.pg_roles AS role
                       ON role.oid = policy_role.role_oid
                    ORDER BY 1
               ) AS policy_roles,
               pg_catalog.pg_get_expr(policy.polqual, policy.polrelid, false),
               pg_catalog.pg_get_expr(policy.polwithcheck, policy.polrelid, false)
          FROM pg_catalog.pg_class AS relation
          JOIN pg_catalog.pg_namespace AS namespace
            ON namespace.oid = relation.relnamespace
          LEFT JOIN pg_catalog.pg_policy AS policy
            ON policy.polrelid = relation.oid
         WHERE (namespace.nspname = 'public' AND relation.relname = ANY(%s))
            OR (namespace.nspname = 'celestin_private'
                AND relation.relname = 'project_runtime_scopes')
         ORDER BY namespace.nspname, relation.relname, policy.polname
    """


def _assert_expected_policy_catalog(pool, timeout: float) -> None:
    cursor = None
    try:
        with pool.connection(timeout=timeout) as connection:
            cursor = connection.execute(
                _policy_catalog_query(),
                (list(PROTECTED_TABLES + AUTHORIZATION_TABLES),),
            )
            rows = cursor.fetchall()
    except Exception:
        raise RuntimeError("PostgreSQL RLS catalog could not be verified") from None
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                raise RuntimeError("PostgreSQL RLS catalog cursor could not be closed") from None
    _validate_policy_catalog(rows)


def _function_specs(project_runtime: bool, *, authorization_admin: bool = False):
    if project_runtime and authorization_admin:
        raise ValueError("a PostgreSQL function profile cannot be both project and admin")
    if authorization_admin:
        return {
            signature: (
                "sql" if signature in {
                    "celestin_lock_workspace_authorization_write(uuid)",
                    "celestin_lock_principal_authorization_write(text,text,text)",
                } else "plpgsql",
                "v",
                True,
                ("search_path=pg_catalog, pg_temp",),
                body,
            )
            for signature, body in _ADMIN_AUTHORIZATION_WRITE_FUNCTION_BODIES.items()
        }
    if project_runtime:
        return {
            "celestin_request_scope_matches(uuid,uuid)": (
                "sql", "s", False,
                ("search_path=pg_catalog, pg_temp",),
                _PROJECT_SCOPE_FUNCTION_BODY,
            ),
            "celestin_request_workspace_matches(uuid)": (
                "sql", "s", False,
                ("search_path=pg_catalog, pg_temp",),
                _PROJECT_WORKSPACE_FUNCTION_BODY,
            ),
            "celestin_lock_project_authorization_request(uuid)": (
                "plpgsql", "v", True,
                ("search_path=pg_catalog, pg_temp",),
                _PROJECT_LOCK_FUNCTION_BODY,
            ),
        }
    return {
        "celestin_lock_authorization_request(uuid,text,text,text)": (
            "plpgsql", "v", True, ("search_path=pg_catalog, pg_temp",),
            _READER_LOCK_FUNCTION_BODY,
        ),
    }


def _validate_function_catalog(
    rows, *, project_runtime: bool, authorization_admin: bool = False
) -> None:
    expected = _function_specs(
        project_runtime, authorization_admin=authorization_admin
    )
    actual = {}
    try:
        for row in rows:
            (
                signature, owner, owner_is_database_owner, owner_superuser, owner_bypassrls,
                owner_createrole, owner_createdb, owner_replication,
                owner_has_no_other_memberships, language, volatility,
                security_definer, config, source, public_execute, current_execute,
                unexpected_execute_grants,
            ) = row
            if any(
                type(row[index]) is not bool
                for index in (2, 3, 4, 5, 6, 7, 8, 11, 14, 15, 16)
            ):
                raise ValueError("authorization function catalog omitted a boolean property")
            signature = _canonical_sql_expression(str(signature))
            if signature in actual:
                raise ValueError("duplicate function signature")
            actual[signature] = (
                str(owner), owner_is_database_owner, owner_superuser, owner_bypassrls,
                owner_createrole, owner_createdb, owner_replication,
                owner_has_no_other_memberships, str(language),
                str(volatility), security_definer, tuple(config or ()),
                _canonical_sql_expression(str(source)), public_execute,
                current_execute, unexpected_execute_grants,
            )
    except (TypeError, ValueError):
        raise RuntimeError("PostgreSQL authorization helper definition is unsafe") from None
    if set(actual) != set(expected):
        raise RuntimeError("PostgreSQL authorization helper definition is unsafe")
    owners = set()
    for signature, spec in expected.items():
        (
            owner, owner_is_database_owner, superuser, bypassrls, createrole,
            createdb, replication, no_other_memberships, language, volatility,
            secdef, config, source,
            public_execute, current_execute, unexpected_execute_grants,
        ) = actual[signature]
        expected_language, expected_volatility, expected_secdef, expected_config, expected_source = spec
        owners.add(owner)
        if (
            not owner_is_database_owner
            or superuser or bypassrls or createrole or createdb or replication
            or not no_other_memberships
            or owner in {AUTHORIZATION_READER_ROLE, PROJECT_RUNTIME_ROLE, AUTHORIZATION_ADMIN_ROLE}
            or language != expected_language
            or volatility != expected_volatility
            or secdef != expected_secdef
            or tuple(config) != expected_config
            or source != _canonical_sql_expression(expected_source)
            or public_execute
            or current_execute is not True
            or unexpected_execute_grants
        ):
            raise RuntimeError("PostgreSQL authorization helper definition is unsafe")
    if project_runtime and len(owners) != 1:
        raise RuntimeError("PostgreSQL authorization helper ownership is inconsistent")
    if set(expected) & _UNVERIFIED_LOCK_KEY_SCOPE_SIGNATURES:
        raise RuntimeError(
            "PostgreSQL authorization helper lock-key scope is not verified"
        )


def _function_catalog_query(
    project_runtime: bool, *, authorization_admin: bool = False
) -> tuple[str, tuple[str, ...]]:
    signatures = tuple(
        _function_specs(project_runtime, authorization_admin=authorization_admin)
    )
    regprocedures = tuple(
        "public." + signature for signature in signatures
    )
    expected_execute_role = (
        AUTHORIZATION_ADMIN_ROLE if authorization_admin
        else PROJECT_RUNTIME_ROLE if project_runtime
        else AUTHORIZATION_READER_ROLE
    )
    placeholders = ", ".join("pg_catalog.to_regprocedure(%s)" for _ in signatures)
    query = """
        SELECT procedure.oid::regprocedure::text,
               owner.rolname,
               owner.oid = (
                   SELECT database.datdba FROM pg_catalog.pg_database AS database
                    WHERE database.datname = current_database()
               ) AS owner_is_database_owner,
               owner.rolsuper, owner.rolbypassrls,
               owner.rolcreaterole, owner.rolcreatedb, owner.rolreplication,
               NOT EXISTS (
                   SELECT 1 FROM pg_catalog.pg_roles AS inherited
                    WHERE inherited.oid <> owner.oid
                      AND inherited.rolname <> 'pg_database_owner'
                      AND pg_catalog.pg_has_role(owner.oid, inherited.oid, 'MEMBER')
               ) AS owner_has_no_other_memberships,
               language.lanname, procedure.provolatile, procedure.prosecdef,
               procedure.proconfig, procedure.prosrc,
               EXISTS (
                   SELECT 1
                     FROM pg_catalog.aclexplode(
                         COALESCE(procedure.proacl,
                                  pg_catalog.acldefault('f', procedure.proowner))
                     ) AS acl
                    WHERE acl.grantee = 0 AND acl.privilege_type = 'EXECUTE'
               ) AS public_execute,
               pg_catalog.has_function_privilege(current_user, procedure.oid, 'EXECUTE')
                   AS current_role_execute,
               EXISTS (
                   SELECT 1 FROM pg_catalog.aclexplode(
                       COALESCE(procedure.proacl,
                                pg_catalog.acldefault('f', procedure.proowner))
                   ) AS acl
                    WHERE acl.privilege_type = 'EXECUTE'
                      AND acl.grantee <> procedure.proowner
                      AND (
                          acl.is_grantable
                          OR acl.grantee <> COALESCE((
                              SELECT allowed.oid FROM pg_catalog.pg_roles AS allowed
                               WHERE allowed.rolname = %s
                          ), 0)
                      )
               ) AS unexpected_execute_grants
          FROM pg_catalog.pg_proc AS procedure
          JOIN pg_catalog.pg_roles AS owner ON owner.oid = procedure.proowner
          JOIN pg_catalog.pg_language AS language ON language.oid = procedure.prolang
         WHERE procedure.oid IN ({placeholders})
         ORDER BY procedure.oid::regprocedure::text
    """.format(placeholders=placeholders)
    # The role placeholder appears before function placeholders in the SQL.
    return query, (expected_execute_role, *regprocedures)


def _assert_authorization_function_catalog(
    pool, timeout: float, *, project_runtime: bool, authorization_admin: bool = False
) -> None:
    query, signatures = _function_catalog_query(
        project_runtime, authorization_admin=authorization_admin
    )
    cursor = None
    try:
        with pool.connection(timeout=timeout) as connection:
            cursor = connection.execute(query, signatures)
            rows = cursor.fetchall()
    except Exception:
        raise RuntimeError("PostgreSQL authorization helper definition could not be verified") from None
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                raise RuntimeError("PostgreSQL authorization helper cursor could not be closed") from None
    _validate_function_catalog(
        rows,
        project_runtime=project_runtime,
        authorization_admin=authorization_admin,
    )


def _advisory_lock_privilege_query() -> str:
    """Return effective access and complete ACL entries for advisory overloads."""
    return """
        SELECT procedure.oid::regprocedure::text,
               pg_catalog.has_function_privilege(
                   current_user, procedure.oid, 'EXECUTE'
                ) AS effective_execute,
               EXISTS (
                   SELECT 1
                     FROM pg_catalog.aclexplode(
                         COALESCE(procedure.proacl,
                                  pg_catalog.acldefault('f', procedure.proowner))
                     ) AS acl
                    WHERE acl.grantee = (
                        SELECT role.oid FROM pg_catalog.pg_roles AS role
                         WHERE role.rolname = current_user
                    )
                      AND acl.privilege_type = 'EXECUTE'
               ) AS direct_execute,
               EXISTS (
                   SELECT 1
                     FROM pg_catalog.aclexplode(
                         COALESCE(procedure.proacl,
                                  pg_catalog.acldefault('f', procedure.proowner))
                     ) AS acl
                    WHERE acl.grantee = 0
                      AND acl.privilege_type = 'EXECUTE'
               ) AS public_execute,
               owner.oid::bigint AS owner_oid,
               owner.rolname AS owner_name,
               login.oid::bigint AS current_user_oid,
               login.rolname AS current_user_name,
               COALESCE(
                   pg_catalog.jsonb_agg(
                       pg_catalog.jsonb_build_object(
                           'grantor_oid', acl.grantor::bigint,
                           'grantor', grantor.rolname,
                           'grantee_oid', acl.grantee::bigint,
                           'grantee', CASE
                               WHEN acl.grantee = 0 THEN 'PUBLIC'
                               ELSE grantee.rolname
                           END,
                           'privilege_type', acl.privilege_type,
                           'is_grantable', acl.is_grantable
                       ) ORDER BY acl.grantee, acl.grantor, acl.privilege_type
                   ) FILTER (WHERE acl.grantee IS NOT NULL),
                   '[]'::jsonb
               ) AS acl_entries
          FROM pg_catalog.pg_proc AS procedure
          JOIN pg_catalog.pg_namespace AS namespace
            ON namespace.oid = procedure.pronamespace
          JOIN pg_catalog.pg_roles AS owner
            ON owner.oid = procedure.proowner
          JOIN pg_catalog.pg_roles AS login
            ON login.rolname = current_user
          LEFT JOIN LATERAL pg_catalog.aclexplode(
              COALESCE(procedure.proacl,
                       pg_catalog.acldefault('f', procedure.proowner))
          ) AS acl ON true
          LEFT JOIN pg_catalog.pg_roles AS grantor
            ON grantor.oid = acl.grantor
          LEFT JOIN pg_catalog.pg_roles AS grantee
            ON grantee.oid = acl.grantee
         WHERE namespace.nspname = 'pg_catalog'
           AND pg_catalog.left(procedure.proname, 12) = 'pg_advisory_'
         GROUP BY procedure.oid, owner.oid, owner.rolname, login.oid, login.rolname
         ORDER BY procedure.oid::regprocedure::text
    """


def _normalize_function_signature(signature: str) -> str:
    normalized = _canonical_sql_expression(signature) or ""
    if normalized.startswith("pg_catalog."):
        normalized = normalized[len("pg_catalog."):]
    return normalized


def _validate_advisory_lock_privilege_catalog(rows, *, role_profile: str) -> None:
    """Require the exact effective and grantable advisory ACL profile for a login.

    PostgreSQL grants function EXECUTE to PUBLIC by default. Since ACLs are
    additive, a role-specific GRANT/REVOKE cannot deny that inherited access.
    The request identities therefore get no direct advisory functions; reviewed
    SECURITY DEFINER wrappers own the lock operation. The migration/database
    owner gets only the runner's session lock/unlock pair and the exclusive and
    shared transaction-lock primitives needed by its owned wrappers. The
    function owner is the only accepted grantor; owner grant options are
    inherent, while every non-owner grant must be direct, non-delegable, and
    granted to the inspected login.
    """
    allowed = _ADVISORY_LOCK_ALLOWED_SIGNATURES.get(role_profile)
    if allowed is None:
        raise RuntimeError("PostgreSQL advisory-lock role profile is unknown")
    actual = {}
    inspected_login = None
    acl_fields = {
        "grantor_oid",
        "grantor",
        "grantee_oid",
        "grantee",
        "privilege_type",
        "is_grantable",
    }
    try:
        for row in rows:
            if len(row) != 9:
                raise ValueError("advisory-lock catalog row has an unexpected shape")
            (
                signature,
                effective_execute,
                direct_execute,
                public_execute,
                owner_oid,
                owner_name,
                current_user_oid,
                current_user_name,
                acl_entries,
            ) = row
            if any(
                type(value) is not bool
                for value in (effective_execute, direct_execute, public_execute)
            ):
                raise ValueError("advisory-lock catalog omitted a privilege property")
            if (
                type(owner_oid) is not int
                or owner_oid <= 0
                or type(current_user_oid) is not int
                or current_user_oid <= 0
                or not isinstance(owner_name, str)
                or not owner_name
                or not isinstance(current_user_name, str)
                or not current_user_name
                or not isinstance(acl_entries, (list, tuple))
            ):
                raise ValueError("advisory-lock catalog omitted role or ACL metadata")
            login_identity = (current_user_oid, current_user_name)
            if inspected_login is None:
                inspected_login = login_identity
            elif inspected_login != login_identity:
                raise ValueError("advisory-lock rows disagree on current login identity")
            normalized = _normalize_function_signature(str(signature))
            if not normalized or normalized in actual:
                raise ValueError("duplicate advisory function")
            owner_entries = 0
            current_user_entries = 0
            public_entries = 0
            seen_entries = set()
            for entry in acl_entries:
                if not isinstance(entry, Mapping) or set(entry) != acl_fields:
                    raise ValueError("advisory-lock ACL entry has an unexpected shape")
                grantor_oid = entry["grantor_oid"]
                grantee_oid = entry["grantee_oid"]
                grantor = entry["grantor"]
                grantee = entry["grantee"]
                privilege = entry["privilege_type"]
                grantable = entry["is_grantable"]
                if (
                    type(grantor_oid) is not int
                    or grantor_oid <= 0
                    or type(grantee_oid) is not int
                    or grantee_oid < 0
                    or not isinstance(grantor, str)
                    or not grantor
                    or not isinstance(grantee, str)
                    or not grantee
                    or privilege != "EXECUTE"
                    or type(grantable) is not bool
                ):
                    raise ValueError("advisory-lock ACL entry is incomplete")
                entry_key = (
                    grantor_oid,
                    grantor,
                    grantee_oid,
                    grantee,
                    privilege,
                    grantable,
                )
                if entry_key in seen_entries:
                    raise ValueError("duplicate advisory-lock ACL entry")
                seen_entries.add(entry_key)

                if grantee_oid == 0:
                    if grantee != "PUBLIC":
                        raise ValueError("PUBLIC ACL identity is inconsistent")
                    public_entries += 1
                    raise ValueError("PUBLIC has advisory-lock execution")
                if grantor_oid != owner_oid or grantor != owner_name:
                    raise ValueError("advisory-lock grantor is not the function owner")
                if grantee_oid == owner_oid:
                    if grantee != owner_name or not grantable:
                        raise ValueError("function-owner ACL metadata is inconsistent")
                    owner_entries += 1
                    if owner_entries > 1:
                        raise ValueError("duplicate function-owner ACL entry")
                    continue
                if (
                    grantee_oid != current_user_oid
                    or grantee != current_user_name
                    or grantable
                    or normalized not in allowed
                ):
                    raise ValueError("unexpected advisory-lock grant")
                current_user_entries += 1

            if (
                current_user_entries > 1
                or direct_execute != bool(current_user_entries)
                or public_execute != bool(public_entries)
            ):
                raise ValueError("advisory-lock ACL flags disagree with entries")
            actual[normalized] = (
                effective_execute,
                direct_execute,
                public_execute,
                current_user_entries,
            )
    except (TypeError, ValueError):
        raise RuntimeError("PostgreSQL advisory-lock privileges are unsafe") from None
    if (
        set(actual) != _ADVISORY_LOCK_SIGNATURES
        or any(
            effective != (signature in allowed)
            or direct != (signature in allowed)
            or public
            or direct_count != int(signature in allowed)
            for signature, (effective, direct, public, direct_count) in actual.items()
        )
    ):
        raise RuntimeError("PostgreSQL advisory-lock privileges are unsafe")


def _assert_advisory_lock_privileges(
    pool, timeout: float, *, role_profile: str
) -> None:
    cursor = None
    try:
        with pool.connection(timeout=timeout) as connection:
            cursor = connection.execute(_advisory_lock_privilege_query(), ())
            rows = cursor.fetchall()
    except Exception:
        raise RuntimeError("PostgreSQL advisory-lock privileges could not be verified") from None
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                raise RuntimeError("PostgreSQL advisory-lock privilege cursor could not be closed") from None
    _validate_advisory_lock_privilege_catalog(rows, role_profile=role_profile)


def _trigger_catalog_query() -> str:
    return """
        SELECT relation_namespace.nspname,
               relation.relname,
               catalog_trigger.tgname,
               catalog_trigger.tgenabled,
               catalog_trigger.tgtype,
               ARRAY(
                   SELECT attribute.attname::text
                     FROM unnest(catalog_trigger.tgattr::smallint[])
                          WITH ORDINALITY AS trigger_column(attnum, ordinal)
                     JOIN pg_catalog.pg_attribute AS attribute
                       ON attribute.attrelid = relation.oid
                      AND attribute.attnum = trigger_column.attnum
                    ORDER BY trigger_column.ordinal
               ) AS update_columns,
               catalog_trigger.tgisinternal,
               catalog_trigger.tgqual IS NOT NULL AS has_when_condition,
               catalog_trigger.tgnargs,
               catalog_trigger.tgconstraint <> 0 AS is_constraint_trigger,
               function_namespace.nspname,
               procedure.proname,
               pg_catalog.pg_get_function_identity_arguments(procedure.oid),
               owner.rolname,
               owner.oid = (
                   SELECT database.datdba FROM pg_catalog.pg_database AS database
                    WHERE database.datname = current_database()
               ) AS owner_is_database_owner,
               owner.rolsuper, owner.rolbypassrls,
               owner.rolcreaterole, owner.rolcreatedb, owner.rolreplication,
               NOT EXISTS (
                   SELECT 1 FROM pg_catalog.pg_roles AS inherited
                    WHERE inherited.oid <> owner.oid
                      AND inherited.rolname <> 'pg_database_owner'
                      AND pg_catalog.pg_has_role(owner.oid, inherited.oid, 'MEMBER')
               ) AS owner_has_no_other_memberships,
               language.lanname,
               procedure.provolatile,
               procedure.prosecdef,
               procedure.proconfig,
               procedure.prosrc,
               EXISTS (
                   SELECT 1
                     FROM pg_catalog.aclexplode(
                         COALESCE(procedure.proacl,
                                  pg_catalog.acldefault('f', procedure.proowner))
                     ) AS acl
                    WHERE acl.grantee = 0 AND acl.privilege_type = 'EXECUTE'
               ) AS public_execute,
               pg_catalog.has_function_privilege(
                   current_user, procedure.oid, 'EXECUTE'
               ) AS current_role_execute,
               EXISTS (
                   SELECT 1 FROM pg_catalog.aclexplode(
                       COALESCE(procedure.proacl,
                                pg_catalog.acldefault('f', procedure.proowner))
                   ) AS acl
                    WHERE acl.privilege_type = 'EXECUTE'
                      AND acl.grantee <> procedure.proowner
               ) AS unexpected_execute_grants
          FROM pg_catalog.pg_trigger AS catalog_trigger
          JOIN pg_catalog.pg_class AS relation
            ON relation.oid = catalog_trigger.tgrelid
          JOIN pg_catalog.pg_namespace AS relation_namespace
            ON relation_namespace.oid = relation.relnamespace
          JOIN pg_catalog.pg_proc AS procedure
            ON procedure.oid = catalog_trigger.tgfoid
          JOIN pg_catalog.pg_namespace AS function_namespace
            ON function_namespace.oid = procedure.pronamespace
          JOIN pg_catalog.pg_roles AS owner
            ON owner.oid = procedure.proowner
          JOIN pg_catalog.pg_language AS language
            ON language.oid = procedure.prolang
         WHERE NOT catalog_trigger.tgisinternal
           AND catalog_trigger.tgname NOT IN (
               'celestin_workspace_active_owner_guard',
               'celestin_membership_active_owner_guard',
               'celestin_user_active_owner_guard'
           )
           AND (
               (relation_namespace.nspname = 'public'
                AND relation.relname = ANY(%s::text[]))
               OR (relation_namespace.nspname = 'celestin_private'
                   AND relation.relname = 'project_runtime_scopes')
           )
         ORDER BY relation_namespace.nspname, relation.relname, trigger.tgname
    """


def _validate_trigger_catalog(rows) -> None:
    expected_triggers = _trigger_specs()
    expected_functions = _trigger_function_specs()
    actual_triggers = {}
    actual_functions = {}
    try:
        for row in rows:
            (
                relation_schema, table, trigger_name, enabled, tgtype, attributes,
                is_internal, has_when, argument_count, is_constraint,
                function_schema, function_name, identity_arguments,
                owner, owner_is_database_owner, owner_superuser, owner_bypassrls,
                owner_createrole, owner_createdb, owner_replication,
                owner_has_no_other_memberships, language, volatility,
                security_definer, config, source, public_execute, current_execute,
                unexpected_execute_grants,
            ) = row
            key = (str(relation_schema), str(table), str(trigger_name))
            if key in actual_triggers:
                raise ValueError("duplicate trigger")
            actual_triggers[key] = (
               str(enabled), int(tgtype), tuple(sorted(attributes or ())),
                bool(is_internal), bool(has_when), int(argument_count),
                bool(is_constraint), str(function_schema), str(function_name),
                str(identity_arguments or ""),
            )
            function_key = str(function_name)
            function_properties = (
                str(owner), bool(owner_is_database_owner), bool(owner_superuser),
                bool(owner_bypassrls), bool(owner_createrole), bool(owner_createdb),
                bool(owner_replication), bool(owner_has_no_other_memberships),
                str(language), str(volatility), bool(security_definer),
                tuple(config or ()), _canonical_sql_expression(str(source)),
                bool(public_execute), bool(current_execute),
                bool(unexpected_execute_grants),
            )
            previous = actual_functions.setdefault(function_key, function_properties)
            if previous != function_properties:
                raise ValueError("inconsistent trigger function")
    except (TypeError, ValueError):
        raise RuntimeError("PostgreSQL authorization trigger catalog is unsafe") from None

    if set(actual_triggers) != set(expected_triggers):
        raise RuntimeError("PostgreSQL authorization trigger catalog is unsafe")
    if set(actual_functions) != set(expected_functions):
        raise RuntimeError("PostgreSQL authorization trigger catalog is unsafe")

    for key, (tgtype, attributes, function_name) in expected_triggers.items():
        expected = (
            "O", tgtype, attributes, False, False, 0, False,
            "public", function_name, "",
        )
        if actual_triggers[key] != expected:
            raise RuntimeError("PostgreSQL authorization trigger catalog is unsafe")

    for function_name, (config, source) in expected_functions.items():
        (
            _owner, owner_is_database_owner, superuser, bypassrls, createrole,
            createdb, replication, no_other_memberships, language, volatility,
            security_definer, actual_config, actual_source,
            public_execute, current_execute, unexpected_execute_grants,
        ) = actual_functions[function_name]
        if (
            not owner_is_database_owner
            or superuser or bypassrls or createrole or createdb or replication
            or not no_other_memberships
            or language != "plpgsql"
            or volatility != "v"
            or not security_definer
            or actual_config != config
            or actual_source != _canonical_sql_expression(source)
            or public_execute or current_execute or unexpected_execute_grants
        ):
            raise RuntimeError("PostgreSQL authorization trigger catalog is unsafe")


def _assert_expected_trigger_catalog(pool, timeout: float) -> None:
    cursor = None
    try:
        relation_names = sorted({table for schema, table, _ in _trigger_specs() if schema == "public"})
        with pool.connection(timeout=timeout) as connection:
            cursor = connection.execute(_trigger_catalog_query(), (relation_names,))
            rows = cursor.fetchall()
    except Exception:
        raise RuntimeError("PostgreSQL authorization trigger catalog could not be verified") from None
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                raise RuntimeError("PostgreSQL authorization trigger cursor could not be closed") from None
    _validate_trigger_catalog(rows)


def _assert_active_workspace_owner_guard(pool, timeout: float) -> None:
    """Require the exact deferred owner invariant installed by migration 0011."""
    query = """
        SELECT relation.relname, trg.tgname, trg.tgenabled, trg.tgtype,
               trg.tgconstraint <> 0, constraint_row.condeferrable,
               constraint_row.condeferred, procedure.prosrc,
               procedure.prosecdef, procedure.proconfig, language.lanname,
               procedure.provolatile,
               function_namespace.nspname,
               owner.oid = (SELECT datdba FROM pg_catalog.pg_database
                              WHERE datname = current_database()),
               EXISTS (SELECT 1 FROM pg_catalog.aclexplode(
                   COALESCE(procedure.proacl, pg_catalog.acldefault('f', procedure.proowner))
               ) acl WHERE acl.grantee = 0 AND acl.privilege_type = 'EXECUTE'),
               pg_catalog.has_function_privilege(current_user, procedure.oid, 'EXECUTE'),
               EXISTS (SELECT 1 FROM pg_catalog.aclexplode(
                   COALESCE(procedure.proacl, pg_catalog.acldefault('f', procedure.proowner))
               ) acl WHERE acl.grantee <> procedure.proowner)
          FROM pg_catalog.pg_trigger AS trg
          JOIN pg_catalog.pg_class relation ON relation.oid = trg.tgrelid
          JOIN pg_catalog.pg_namespace namespace ON namespace.oid = relation.relnamespace
          JOIN pg_catalog.pg_constraint constraint_row ON constraint_row.oid = trg.tgconstraint
          JOIN pg_catalog.pg_proc procedure ON procedure.oid = trg.tgfoid
          JOIN pg_catalog.pg_namespace function_namespace ON function_namespace.oid = procedure.pronamespace
          JOIN pg_catalog.pg_language language ON language.oid = procedure.prolang
          JOIN pg_catalog.pg_roles owner ON owner.oid = procedure.proowner
         WHERE namespace.nspname = 'public' AND trg.tgname = ANY(%s::text[])
         ORDER BY trg.tgname
    """
    names = (
        "celestin_workspace_active_owner_guard",
        "celestin_membership_active_owner_guard",
        "celestin_user_active_owner_guard",
    )
    cursor = None
    try:
        with pool.connection(timeout=timeout) as connection:
            cursor = connection.execute(query, (list(names),))
            rows = cursor.fetchall()
    except Exception:
        raise RuntimeError("PostgreSQL active-owner invariant could not be verified") from None
    finally:
        if cursor is not None:
            cursor.close()
    expected = {
        "celestin_workspace_active_owner_guard": ("celestin_workspaces", 29),
        "celestin_membership_active_owner_guard": ("celestin_workspace_memberships", 29),
        "celestin_user_active_owner_guard": ("celestin_users", 17),
    }
    try:
        from orbit.postgres.migrations.runner import load_migrations
        migration = next(item for item in load_migrations() if item.version == 11)
        match = re.search(
            r"CREATE FUNCTION public\.celestin_require_active_workspace_owner\(\).*?AS \$\$(.*?)\$\$;",
            migration.sql, flags=re.IGNORECASE | re.DOTALL,
        )
        if match is None:
            raise ValueError
        expected_source = _canonical_sql_expression(match.group(1))
        if len(rows) != len(expected):
            raise ValueError
        for row in rows:
            (table, name, enabled, event_mask, is_constraint, deferrable, deferred,
             source, security_definer, config, language, volatility, function_schema,
             database_owner, public_execute, current_execute, unexpected_execute) = row
            expected_table, expected_event = expected[name]
            if (
                table != expected_table or enabled != "O" or event_mask != expected_event
                or is_constraint is not True or deferrable is not True or deferred is not True
                or _canonical_sql_expression(source) != expected_source
                or security_definer is not True or tuple(config or ()) != ("search_path=pg_catalog, public",)
                or language != "plpgsql" or volatility != "v" or function_schema != "public"
                or database_owner is not True or public_execute is not False
                or current_execute is not False or unexpected_execute is not False
            ):
                raise ValueError
            expected.pop(name)
        if expected:
            raise ValueError
    except Exception:
        raise RuntimeError("PostgreSQL active-owner invariant is unsafe") from None


def _assert_safe_authorization_reader(pool, timeout: float) -> None:
    query = """
        SELECT role.rolsuper, role.rolbypassrls,
               role.rolcreaterole, role.rolcreatedb, role.rolreplication,
               current_user = session_user AS exact_login,
               pg_catalog.pg_has_role(current_user, %s, 'MEMBER') AS has_reader_group,
               NOT EXISTS (
                   SELECT 1 FROM pg_catalog.pg_roles AS inherited
                    WHERE inherited.rolname <> current_user
                      AND inherited.rolname NOT IN (%s, 'pg_database_owner')
                      AND pg_catalog.pg_has_role(current_user, inherited.oid, 'MEMBER')
               ) AS no_other_role_memberships,
               NOT EXISTS (
                   SELECT 1 FROM pg_catalog.pg_auth_members AS membership
                    WHERE membership.member = role.oid AND membership.admin_option
               ) AS no_membership_admin_option,
               NOT EXISTS (
                   SELECT 1 FROM pg_catalog.pg_class AS relation
                     JOIN pg_catalog.pg_namespace AS namespace
                       ON namespace.oid = relation.relnamespace
                    WHERE namespace.nspname = 'public'
                      AND relation.relname = ANY(%s)
                      AND relation.relowner IN (
                          role.oid,
                          (SELECT oid FROM pg_catalog.pg_roles
                            WHERE rolname = 'celestin_authorization_reader')
                      )
               ) AS owns_no_tenant_or_auth_table,
               has_schema_privilege(current_user, 'public', 'CREATE') AS can_create_public_objects,
               NOT EXISTS (
                   SELECT 1 FROM unnest(%s::text[]) AS auth(table_name)
                    WHERE NOT has_table_privilege(current_user,
                              format('public.%I', auth.table_name), 'SELECT')
                       OR has_table_privilege(current_user,
                              format('public.%I', auth.table_name), 'INSERT')
                       OR has_table_privilege(current_user,
                              format('public.%I', auth.table_name), 'UPDATE')
                       OR has_table_privilege(current_user,
                              format('public.%I', auth.table_name), 'DELETE')
                       OR has_table_privilege(current_user,
                              format('public.%I', auth.table_name), 'TRUNCATE')
                       OR has_table_privilege(current_user,
                              format('public.%I', auth.table_name), 'TRIGGER')
                       OR has_table_privilege(current_user,
                              format('public.%I', auth.table_name), 'REFERENCES')
                       OR has_any_column_privilege(current_user,
                              format('public.%I', auth.table_name), 'INSERT')
                       OR has_any_column_privilege(current_user,
                              format('public.%I', auth.table_name), 'UPDATE')
                       OR has_any_column_privilege(current_user,
                              format('public.%I', auth.table_name), 'REFERENCES')
               ) AS authorization_read_only,
               NOT EXISTS (
                   SELECT 1 FROM unnest(%s::text[]) AS tenant(table_name)
                    WHERE has_table_privilege(current_user,
                              format('public.%I', tenant.table_name), 'SELECT')
                       OR has_table_privilege(current_user,
                              format('public.%I', tenant.table_name), 'INSERT')
                       OR has_table_privilege(current_user,
                              format('public.%I', tenant.table_name), 'UPDATE')
                       OR has_table_privilege(current_user,
                              format('public.%I', tenant.table_name), 'DELETE')
                       OR has_table_privilege(current_user,
                              format('public.%I', tenant.table_name), 'TRUNCATE')
                       OR has_table_privilege(current_user,
                              format('public.%I', tenant.table_name), 'TRIGGER')
                       OR has_table_privilege(current_user,
                              format('public.%I', tenant.table_name), 'REFERENCES')
                       OR has_any_column_privilege(current_user,
                              format('public.%I', tenant.table_name), 'SELECT')
                       OR has_any_column_privilege(current_user,
                              format('public.%I', tenant.table_name), 'INSERT')
                       OR has_any_column_privilege(current_user,
                              format('public.%I', tenant.table_name), 'UPDATE')
                       OR has_any_column_privilege(current_user,
                              format('public.%I', tenant.table_name), 'REFERENCES')
               ) AS no_tenant_data_access,
               has_schema_privilege(current_user, 'public', 'USAGE') AS can_use_public_schema,
               NOT has_schema_privilege(current_user, 'celestin_private', 'USAGE')
                   AND NOT has_schema_privilege(current_user, 'celestin_private', 'CREATE')
                   AS cannot_use_private_schema,
               NOT EXISTS (
                   SELECT 1 FROM pg_catalog.pg_namespace AS namespace
                    WHERE namespace.nspname !~ '^pg_'
                      AND namespace.nspname NOT IN (
                          'information_schema', 'public', 'celestin_private'
                      )
                      AND (has_schema_privilege(current_user, namespace.oid, 'USAGE')
                           OR has_schema_privilege(current_user, namespace.oid, 'CREATE'))
               ) AS no_other_application_schema_privileges,
               NOT EXISTS (
                   SELECT 1 FROM pg_catalog.pg_class AS relation
                    WHERE relation.relnamespace = 'public'::regnamespace
                      AND relation.relkind IN ('r', 'p', 'v', 'm', 'f')
                      AND relation.relname <> ALL(%s::text[])
                      AND (has_table_privilege(current_user, relation.oid, 'SELECT')
                           OR has_table_privilege(current_user, relation.oid, 'INSERT')
                           OR has_table_privilege(current_user, relation.oid, 'UPDATE')
                           OR has_table_privilege(current_user, relation.oid, 'DELETE')
                           OR has_table_privilege(current_user, relation.oid, 'TRUNCATE')
                           OR has_table_privilege(current_user, relation.oid, 'TRIGGER')
                           OR has_table_privilege(current_user, relation.oid, 'REFERENCES')
                           OR has_any_column_privilege(current_user, relation.oid, 'SELECT')
                           OR has_any_column_privilege(current_user, relation.oid, 'INSERT')
                           OR has_any_column_privilege(current_user, relation.oid, 'UPDATE')
                           OR has_any_column_privilege(current_user, relation.oid, 'REFERENCES'))
               ) AS no_other_public_relation_privileges,
               NOT EXISTS (
                   SELECT 1 FROM pg_catalog.pg_class AS sequence
                    WHERE sequence.relnamespace = 'public'::regnamespace
                      AND sequence.relkind = 'S'
                      AND (has_sequence_privilege(current_user, sequence.oid, 'USAGE')
                           OR has_sequence_privilege(current_user, sequence.oid, 'SELECT')
                           OR has_sequence_privilege(current_user, sequence.oid, 'UPDATE'))
               ) AS no_sequence_privileges,
               NOT has_function_privilege(current_user,
                   'public.celestin_lock_authorization_write(uuid,text,text,text)', 'EXECUTE')
                   AND NOT has_function_privilege(current_user,
                   'public.celestin_lock_workspace_authorization_write(uuid)', 'EXECUTE')
                   AND NOT has_function_privilege(current_user,
                   'public.celestin_lock_principal_authorization_write(text,text,text)', 'EXECUTE')
                   AND NOT has_function_privilege(current_user,
                   'public.celestin_lock_principal_transition_write(text,text,text,text)', 'EXECUTE')
                   AS no_admin_write_lock_execution,
               NOT EXISTS (
                   SELECT 1 FROM pg_catalog.pg_proc AS procedure
                     JOIN pg_catalog.pg_namespace AS namespace
                       ON namespace.oid = procedure.pronamespace
                    WHERE namespace.nspname !~ '^pg_'
                      AND namespace.nspname <> 'information_schema'
                      AND procedure.prorettype <> 'trigger'::regtype
                      AND has_function_privilege(current_user, procedure.oid, 'EXECUTE')
                      AND procedure.oid <> ALL(ARRAY[
                          'public.celestin_lock_authorization_request(uuid,text,text,text)'::regprocedure::oid
                      ])
               ) AS no_unexpected_function_execution,
               NOT EXISTS (
                   SELECT 1
                     FROM pg_catalog.pg_namespace AS namespace
                     CROSS JOIN LATERAL pg_catalog.aclexplode(
                         COALESCE(namespace.nspacl,
                                  pg_catalog.acldefault('n', namespace.nspowner))
                     ) AS acl
                    WHERE namespace.nspname !~ '^pg_'
                      AND namespace.nspname <> 'information_schema'
                      AND acl.is_grantable
                      AND CASE
                          WHEN acl.grantee = 0 THEN true
                          WHEN acl.grantee = role.oid THEN true
                          ELSE pg_catalog.pg_has_role(role.oid, acl.grantee, 'MEMBER')
                      END
               )
               AND NOT EXISTS (
                   SELECT 1
                     FROM pg_catalog.pg_class AS relation
                     JOIN pg_catalog.pg_namespace AS relation_namespace
                       ON relation_namespace.oid = relation.relnamespace
                     CROSS JOIN LATERAL pg_catalog.aclexplode(
                         COALESCE(relation.relacl,
                                  pg_catalog.acldefault(
                                      CASE WHEN relation.relkind = 'S' THEN 'S' ELSE 'r' END,
                                      relation.relowner
                                  ))
                     ) AS acl
                    WHERE relation_namespace.nspname !~ '^pg_'
                      AND relation_namespace.nspname <> 'information_schema'
                      AND acl.is_grantable
                      AND CASE
                          WHEN acl.grantee = 0 THEN true
                          WHEN acl.grantee = role.oid THEN true
                          ELSE pg_catalog.pg_has_role(role.oid, acl.grantee, 'MEMBER')
                      END
               )
               AND NOT EXISTS (
                   SELECT 1
                     FROM pg_catalog.pg_attribute AS attribute
                     JOIN pg_catalog.pg_class AS relation
                       ON relation.oid = attribute.attrelid
                     JOIN pg_catalog.pg_namespace AS namespace
                       ON namespace.oid = relation.relnamespace
                     CROSS JOIN LATERAL pg_catalog.aclexplode(attribute.attacl) AS acl
                    WHERE namespace.nspname !~ '^pg_'
                      AND namespace.nspname <> 'information_schema'
                      AND attribute.attnum > 0
                      AND attribute.attacl IS NOT NULL
                      AND acl.is_grantable
                      AND CASE
                          WHEN acl.grantee = 0 THEN true
                          WHEN acl.grantee = role.oid THEN true
                          ELSE pg_catalog.pg_has_role(role.oid, acl.grantee, 'MEMBER')
                      END
               )
               AND NOT EXISTS (
                   SELECT 1
                     FROM pg_catalog.pg_proc AS procedure
                     JOIN pg_catalog.pg_namespace AS namespace
                       ON namespace.oid = procedure.pronamespace
                     CROSS JOIN LATERAL pg_catalog.aclexplode(
                         COALESCE(procedure.proacl,
                                  pg_catalog.acldefault('f', procedure.proowner))
                     ) AS acl
                    WHERE namespace.nspname !~ '^pg_'
                      AND namespace.nspname <> 'information_schema'
                      AND acl.is_grantable
                      AND CASE
                          WHEN acl.grantee = 0 THEN true
                          WHEN acl.grantee = role.oid THEN true
                          ELSE pg_catalog.pg_has_role(role.oid, acl.grantee, 'MEMBER')
                      END
               ) AS no_privilege_grant_options
          FROM pg_catalog.pg_roles AS role
         WHERE role.rolname = current_user
    """
    row = _run_pool_probe(
        pool,
        timeout,
        query,
        (
            AUTHORIZATION_READER_ROLE,
            AUTHORIZATION_READER_ROLE,
            list(PROTECTED_TABLES + AUTHORIZATION_TABLES),
            list(AUTHORIZATION_TABLES),
            list(PROTECTED_TABLES),
            list(PROTECTED_TABLES + AUTHORIZATION_TABLES + ("celestin_schema_migrations",)),
        ),
    )
    _validate_authorization_reader_probe(row)
    _assert_login_membership_options(pool, timeout, AUTHORIZATION_READER_ROLE)
    _assert_advisory_lock_privileges(
        pool, timeout, role_profile="authorization_reader"
    )
    _assert_authorization_function_catalog(pool, timeout, project_runtime=False)


def _assert_safe_authorization_admin(pool, timeout: float) -> None:
    """Require the provisioner login to use only the reviewed admin lock wrappers."""
    query = """
        /* authorization_admin_role_readiness */
        SELECT role.rolsuper, role.rolbypassrls,
               role.rolcreaterole, role.rolcreatedb, role.rolreplication,
               role.rolcanlogin, role.rolinherit,
               current_user = session_user AS exact_login,
               pg_catalog.pg_has_role(current_user, %s, 'MEMBER') AS has_admin_group,
               NOT EXISTS (
                   SELECT 1 FROM pg_catalog.pg_roles AS inherited
                    WHERE inherited.rolname <> current_user
                      AND inherited.rolname NOT IN (%s, 'pg_database_owner')
                      AND pg_catalog.pg_has_role(current_user, inherited.oid, 'MEMBER')
               ) AS no_other_role_memberships,
               NOT EXISTS (
                   SELECT 1 FROM pg_catalog.pg_auth_members AS membership
                    WHERE membership.member = role.oid AND membership.admin_option
               ) AS no_membership_admin_option,
               NOT EXISTS (
                   SELECT 1 FROM pg_catalog.pg_class AS relation
                    WHERE relation.relowner = role.oid
                      AND relation.relname = ANY(%s::text[])
               ) AS owns_no_celestin_table,
               NOT has_schema_privilege(current_user, 'public', 'CREATE')
                   AS cannot_create_public_objects,
               NOT has_schema_privilege(current_user, 'celestin_private', 'CREATE')
                   AS cannot_create_private_objects
          FROM pg_catalog.pg_roles AS role
         WHERE role.rolname = current_user
    """
    row = _run_pool_probe(
        pool,
        timeout,
        query,
        (
            AUTHORIZATION_ADMIN_ROLE,
            AUTHORIZATION_ADMIN_ROLE,
            list(PROTECTED_TABLES + AUTHORIZATION_TABLES + ("project_runtime_scopes",)),
        ),
    )
    _validate_authorization_admin_probe(row)
    _assert_login_membership_options(pool, timeout, AUTHORIZATION_ADMIN_ROLE)
    _assert_advisory_lock_privileges(
        pool, timeout, role_profile="authorization_admin"
    )
    _assert_authorization_function_catalog(
        pool, timeout, project_runtime=False, authorization_admin=True
    )


def _privileged_role_membership_query() -> str:
    """Return protected group-role settings and every direct member."""
    return """
        SELECT group_role.rolname,
               group_role.rolcanlogin, group_role.rolsuper,
               group_role.rolbypassrls, group_role.rolcreaterole,
               group_role.rolcreatedb, group_role.rolreplication,
               group_role.rolinherit,
               NOT EXISTS (
                   SELECT 1 FROM pg_catalog.pg_auth_members AS parent_membership
                    WHERE parent_membership.member = group_role.oid
               ) AS group_has_no_parent_membership,
               member.rolname,
               membership.admin_option,
               member.rolcanlogin, member.rolsuper, member.rolbypassrls,
               member.rolcreaterole, member.rolcreatedb,
               member.rolreplication, member.rolinherit,
               CASE
                   WHEN membership.member IS NULL THEN NULL
                   WHEN current_setting('server_version_num')::integer >= 160000
                     THEN (pg_catalog.to_jsonb(membership)->>'inherit_option')::boolean
                   ELSE member.rolinherit
               END AS membership_inherit_option,
               CASE
                   WHEN membership.member IS NULL THEN NULL
                   WHEN current_setting('server_version_num')::integer >= 160000
                     THEN (pg_catalog.to_jsonb(membership)->>'set_option')::boolean
                   ELSE TRUE
               END AS membership_set_option,
               CASE
                   WHEN membership.member IS NULL THEN NULL
                   WHEN current_setting('server_version_num')::integer < 160000 THEN TRUE
                   ELSE COALESCE(
                       (pg_catalog.to_jsonb(membership) ? 'inherit_option')
                       AND (pg_catalog.to_jsonb(membership) ? 'set_option'),
                       FALSE
                   )
               END AS membership_options_complete
          FROM pg_catalog.pg_roles AS group_role
          LEFT JOIN pg_catalog.pg_auth_members AS membership
            ON membership.roleid = group_role.oid
          LEFT JOIN pg_catalog.pg_roles AS member
            ON member.oid = membership.member
         WHERE group_role.rolname = ANY(%s::text[])
         ORDER BY group_role.rolname, member.rolname
    """


def _validate_privileged_role_membership_catalog(rows, *, expected_members) -> None:
    """Require safe NOLOGIN groups and the exact direct-membership profile."""
    expected_groups = set(PRIVILEGED_GROUP_ROLES)
    try:
        materialized = [tuple(row) for row in rows]
        if len(materialized) != len(set(materialized)):
            raise ValueError("duplicate role catalog row")
        group_rows = {}
        actual_members = set()
        for row in materialized:
            if len(row) != 21:
                raise ValueError("unexpected role catalog row shape")
            (
                group_name, group_can_login, group_superuser, group_bypassrls,
                group_createrole, group_createdb, group_replication, group_inherit,
                group_has_no_parent, member_name, admin_option,
                member_can_login, member_superuser, member_bypassrls,
                member_createrole, member_createdb, member_replication,
                member_inherit, membership_inherit_option,
                membership_set_option, membership_options_complete,
            ) = row
            if any(type(row[index]) is not bool for index in range(1, 9)):
                raise ValueError("privileged group catalog omitted a boolean property")
            if group_name not in expected_groups:
                raise ValueError("unknown privileged group role")
            group_profile = (
                group_can_login, group_superuser, group_bypassrls,
                group_createrole, group_createdb, group_replication,
                group_inherit, group_has_no_parent,
            )
            if group_profile != (False, False, False, False, False, False, True, True):
                raise ValueError("unsafe privileged group role")
            previous = group_rows.setdefault(group_name, group_profile)
            if previous != group_profile:
                raise ValueError("inconsistent privileged group role")
            if member_name is None:
                if any(value is not None for value in row[10:]):
                    raise ValueError("malformed empty role roster row")
                continue
            if any(type(row[index]) is not bool for index in range(10, 21)):
                raise ValueError("privileged member catalog omitted a boolean property")
            member_profile = (
                member_can_login, member_superuser, member_bypassrls,
                member_createrole, member_createdb, member_replication,
                member_inherit,
            )
            if (
                admin_option is not False
                or member_profile != (True, False, False, False, False, False, True)
                or membership_inherit_option is not True
                or membership_set_option is not True
                or membership_options_complete is not True
            ):
                raise ValueError("unsafe privileged role member")
            actual_members.add((group_name, member_name))
    except (TypeError, ValueError):
        raise RuntimeError("PostgreSQL privileged role membership catalog is unsafe") from None
    if (
        set(group_rows) != expected_groups
        or actual_members != set(expected_members)
    ):
        raise RuntimeError("PostgreSQL privileged role membership catalog is unsafe")


def _login_membership_options_query() -> str:
    """Inspect the exact login-to-group edge without requiring PostgreSQL 16 columns."""
    return """
        /* celestin_login_membership_option_profile */
        SELECT current_user = session_user AS exact_login,
               login_role.rolinherit,
               group_role.oid IS NOT NULL AND NOT group_role.rolcanlogin
                   AS expected_group_is_no_login,
               membership.member IS NOT NULL AS direct_membership_exists,
               CASE
                   WHEN current_setting('server_version_num')::integer >= 160000
                     THEN (pg_catalog.to_jsonb(membership)->>'inherit_option')::boolean
                   ELSE login_role.rolinherit
               END AS membership_inherit_option,
               CASE
                   WHEN current_setting('server_version_num')::integer >= 160000
                     THEN (pg_catalog.to_jsonb(membership)->>'set_option')::boolean
                   ELSE TRUE
               END AS membership_set_option,
               membership.admin_option,
               CASE
                   WHEN current_setting('server_version_num')::integer < 160000 THEN TRUE
                   ELSE COALESCE(
                       (pg_catalog.to_jsonb(membership) ? 'inherit_option')
                       AND (pg_catalog.to_jsonb(membership) ? 'set_option'),
                       FALSE
                   )
               END AS membership_options_complete
          FROM pg_catalog.pg_roles AS login_role
          LEFT JOIN pg_catalog.pg_roles AS group_role
            ON group_role.rolname = %s
          LEFT JOIN pg_catalog.pg_auth_members AS membership
            ON membership.roleid = group_role.oid
           AND membership.member = login_role.oid
         WHERE login_role.rolname = current_user
    """


def _validate_login_membership_options(row) -> None:
    """Require inheritable membership in the one assigned group only.

    SET ROLE remains allowed for that single assigned group, matching the
    pre-16 PostgreSQL behavior and the existing sibling-role denial contract.
    Other memberships are rejected by each login's role-readiness probe.
    """
    expected = (True, True, True, True, True, True, False, True)
    if (
        row is None
        or len(row) != len(expected)
        or any(type(value) is not bool for value in row)
        or tuple(row) != expected
    ):
        raise RuntimeError("PostgreSQL login role membership options are unsafe")


def _assert_login_membership_options(pool, timeout: float, expected_group: str) -> None:
    try:
        row = _run_pool_probe(
            pool, timeout, _login_membership_options_query(), (expected_group,)
        )
    except Exception:
        raise RuntimeError(
            "PostgreSQL login role membership options could not be verified"
        ) from None
    _validate_login_membership_options(row)


def _assert_safe_project_runtime(
    pool, timeout: float, scope: tuple[UUID, UUID]
) -> None:
    query = """
        SELECT role.rolsuper, role.rolbypassrls,
               role.rolcreaterole, role.rolcreatedb, role.rolreplication,
               role.rolinherit,
               current_user = session_user AS exact_login,
               pg_catalog.pg_has_role(current_user, %s, 'MEMBER') AS has_project_group,
               NOT EXISTS (
                   SELECT 1 FROM pg_catalog.pg_roles AS inherited
                    WHERE inherited.rolname <> current_user
                      AND inherited.rolname NOT IN (%s, 'pg_database_owner')
                      AND pg_catalog.pg_has_role(current_user, inherited.oid, 'MEMBER')
               ) AS no_other_role_memberships,
               NOT EXISTS (
                   SELECT 1 FROM pg_catalog.pg_auth_members AS membership
                    WHERE membership.member = role.oid AND membership.admin_option
               ) AS no_membership_admin_option,
               NOT EXISTS (
                   SELECT 1 FROM pg_catalog.pg_class AS relation
                     JOIN pg_catalog.pg_namespace AS namespace
                       ON namespace.oid = relation.relnamespace
                    WHERE namespace.nspname IN ('public', 'celestin_private')
                      AND relation.relname = ANY(%s)
                      AND relation.relowner IN (
                          role.oid,
                          (SELECT oid FROM pg_catalog.pg_roles
                            WHERE rolname = 'celestin_project_runtime')
                      )
               ) AS owns_no_tenant_or_auth_table,
               has_schema_privilege(current_user, 'public', 'CREATE') AS can_create_public_objects,
               (
                   SELECT count(*) = cardinality(%s::text[])
                     FROM pg_catalog.pg_class AS relation
                     JOIN pg_catalog.pg_namespace AS namespace
                       ON namespace.oid = relation.relnamespace
                    WHERE namespace.nspname = 'public'
                      AND relation.relname = ANY(%s)
                      AND relation.relkind IN ('r', 'p')
                      AND relation.relrowsecurity AND relation.relforcerowsecurity
               ) AS all_tenant_tables_force_rls,
               (
                   SELECT count(*) = 1
                          AND bool_and(login_role_name = session_user::name
                              AND workspace_id = %s AND project_id = %s)
                     FROM celestin_private.project_runtime_scopes
                    WHERE login_role_name = session_user::name
               ) AS exact_project_login_mapping,
               public.celestin_request_scope_matches(%s::uuid, %s::uuid)
                   AS request_scope_matches,
               NOT public.celestin_request_scope_matches(%s::uuid, %s::uuid)
                   AS scope_rejects_other_project,
               NOT public.celestin_request_scope_matches(%s::uuid, %s::uuid)
                   AS scope_rejects_other_workspace,
               public.celestin_request_workspace_matches(%s::uuid)
                   AS workspace_scope_matches,
               NOT public.celestin_request_workspace_matches(%s::uuid)
                   AS workspace_scope_rejects_other_workspace,
               has_schema_privilege(current_user, 'celestin_private', 'CREATE')
                   AS can_create_private_objects,
               has_schema_privilege(current_user, 'celestin_private', 'USAGE')
                   AS can_use_private_schema,
               has_table_privilege(current_user,
                   'celestin_private.project_runtime_scopes', 'SELECT')
                   AND NOT has_table_privilege(current_user,
                       'celestin_private.project_runtime_scopes', 'INSERT')
                   AND NOT has_table_privilege(current_user,
                       'celestin_private.project_runtime_scopes', 'UPDATE')
                   AND NOT has_table_privilege(current_user,
                       'celestin_private.project_runtime_scopes', 'DELETE')
                   AND NOT has_table_privilege(current_user,
                       'celestin_private.project_runtime_scopes', 'TRUNCATE')
                   AND NOT has_table_privilege(current_user,
                       'celestin_private.project_runtime_scopes', 'TRIGGER')
                   AND NOT has_table_privilege(current_user,
                       'celestin_private.project_runtime_scopes', 'REFERENCES')
                   AND NOT has_any_column_privilege(current_user,
                       'celestin_private.project_runtime_scopes', 'INSERT')
                   AND NOT has_any_column_privilege(current_user,
                       'celestin_private.project_runtime_scopes', 'UPDATE')
                   AND NOT has_any_column_privilege(current_user,
                       'celestin_private.project_runtime_scopes', 'REFERENCES')
                   AS read_only_scope_mapping,
               NOT EXISTS (
                   SELECT 1 FROM unnest(%s::text[]) AS auth(table_name)
                    WHERE NOT has_table_privilege(current_user,
                              format('public.%I', auth.table_name), 'SELECT')
                       OR has_table_privilege(current_user,
                              format('public.%I', auth.table_name), 'INSERT')
                       OR has_table_privilege(current_user,
                              format('public.%I', auth.table_name), 'UPDATE')
                       OR has_table_privilege(current_user,
                              format('public.%I', auth.table_name), 'DELETE')
                        OR has_table_privilege(current_user,
                               format('public.%I', auth.table_name), 'TRUNCATE')
                        OR has_table_privilege(current_user,
                               format('public.%I', auth.table_name), 'TRIGGER')
                        OR has_table_privilege(current_user,
                               format('public.%I', auth.table_name), 'REFERENCES')
                       OR has_any_column_privilege(current_user,
                              format('public.%I', auth.table_name), 'INSERT')
                       OR has_any_column_privilege(current_user,
                              format('public.%I', auth.table_name), 'UPDATE')
                       OR has_any_column_privilege(current_user,
                              format('public.%I', auth.table_name), 'REFERENCES')
               ) AS authorization_read_only,
               (
                   has_table_privilege(current_user, 'public.chat_messages', 'SELECT')
                   AND has_table_privilege(current_user, 'public.chat_messages', 'INSERT')
                   AND NOT has_table_privilege(current_user, 'public.chat_messages', 'UPDATE')
                    AND NOT has_table_privilege(current_user, 'public.chat_messages', 'DELETE')
                    AND NOT has_table_privilege(current_user, 'public.chat_messages', 'TRUNCATE')
                    AND NOT has_table_privilege(current_user, 'public.chat_messages', 'TRIGGER')
                    AND NOT has_table_privilege(current_user, 'public.chat_messages', 'REFERENCES')
                   AND NOT has_any_column_privilege(current_user, 'public.chat_messages', 'UPDATE')
                   AND has_table_privilege(current_user, 'public.project_memory', 'SELECT')
                   AND has_table_privilege(current_user, 'public.project_memory', 'INSERT')
                   AND has_table_privilege(current_user, 'public.project_memory', 'UPDATE')
                    AND NOT has_table_privilege(current_user, 'public.project_memory', 'DELETE')
                    AND NOT has_table_privilege(current_user, 'public.project_memory', 'TRUNCATE')
                    AND NOT has_table_privilege(current_user, 'public.project_memory', 'TRIGGER')
                    AND NOT has_table_privilege(current_user, 'public.project_memory', 'REFERENCES')
               ) AS current_tool_grants,
               (
                   has_sequence_privilege(current_user,
                       pg_catalog.pg_get_serial_sequence('public.chat_messages', 'id'), 'USAGE')
                   AND has_sequence_privilege(current_user,
                       pg_catalog.pg_get_serial_sequence('public.project_memory', 'id'), 'USAGE')
                    AND has_sequence_privilege(current_user,
                       pg_catalog.pg_get_serial_sequence('public.chat_messages', 'id'), 'SELECT')
                    AND has_sequence_privilege(current_user,
                       pg_catalog.pg_get_serial_sequence('public.project_memory', 'id'), 'SELECT')
                    AND NOT has_sequence_privilege(current_user,
                       pg_catalog.pg_get_serial_sequence('public.chat_messages', 'id'), 'UPDATE')
                    AND NOT has_sequence_privilege(current_user,
                       pg_catalog.pg_get_serial_sequence('public.project_memory', 'id'), 'UPDATE')
               ) AS current_tool_sequence_grants,
               NOT EXISTS (
                   SELECT 1 FROM unnest(%s::text[]) AS unexposed(table_name)
                    WHERE has_table_privilege(current_user,
                              format('public.%I', unexposed.table_name), 'SELECT')
                       OR has_table_privilege(current_user,
                              format('public.%I', unexposed.table_name), 'INSERT')
                       OR has_table_privilege(current_user,
                              format('public.%I', unexposed.table_name), 'UPDATE')
                       OR has_table_privilege(current_user,
                              format('public.%I', unexposed.table_name), 'DELETE')
                        OR has_table_privilege(current_user,
                               format('public.%I', unexposed.table_name), 'TRUNCATE')
                        OR has_table_privilege(current_user,
                               format('public.%I', unexposed.table_name), 'TRIGGER')
                        OR has_table_privilege(current_user,
                               format('public.%I', unexposed.table_name), 'REFERENCES')
                       OR has_any_column_privilege(current_user,
                              format('public.%I', unexposed.table_name), 'SELECT')
                       OR has_any_column_privilege(current_user,
                              format('public.%I', unexposed.table_name), 'INSERT')
                       OR has_any_column_privilege(current_user,
                              format('public.%I', unexposed.table_name), 'UPDATE')
                       OR has_any_column_privilege(current_user,
                              format('public.%I', unexposed.table_name), 'REFERENCES')
               ) AS no_unexposed_content_grants,
               NOT EXISTS (
                   SELECT 1 FROM unnest(%s::text[]) AS tenant(table_name)
                    WHERE has_table_privilege(current_user,
                              format('public.%I', tenant.table_name), 'DELETE')
                       OR has_table_privilege(current_user,
                              format('public.%I', tenant.table_name), 'TRUNCATE')
                       OR has_table_privilege(current_user,
                              format('public.%I', tenant.table_name), 'TRIGGER')
                       OR has_table_privilege(current_user,
                              format('public.%I', tenant.table_name), 'REFERENCES')
                       OR has_any_column_privilege(current_user,
                              format('public.%I', tenant.table_name), 'REFERENCES')
               ) AS no_excess_content_grants,
               has_function_privilege(current_user,
                   'public.celestin_lock_project_authorization_request(uuid)',
                   'EXECUTE') AS can_lock_project_request_authorization,
               NOT has_function_privilege(current_user,
                   'public.celestin_lock_authorization_request(uuid, text, text, text)',
                   'EXECUTE') AS cannot_lock_arbitrary_request_authorization,
               has_function_privilege(current_user,
                   'public.celestin_request_scope_matches(uuid, uuid)', 'EXECUTE')
                   AS can_check_project_scope,
               has_function_privilege(current_user,
                   'public.celestin_request_workspace_matches(uuid)', 'EXECUTE')
                   AS can_check_workspace_scope,
               has_schema_privilege(current_user, 'public', 'USAGE') AS can_use_public_schema,
               NOT EXISTS (
                   SELECT 1 FROM pg_catalog.pg_namespace AS namespace
                    WHERE namespace.nspname !~ '^pg_'
                      AND namespace.nspname NOT IN (
                          'information_schema', 'public', 'celestin_private'
                      )
                      AND (has_schema_privilege(current_user, namespace.oid, 'USAGE')
                           OR has_schema_privilege(current_user, namespace.oid, 'CREATE'))
               ) AS no_other_application_schema_privileges,
               NOT EXISTS (
                   SELECT 1 FROM pg_catalog.pg_class AS relation
                    WHERE relation.relnamespace = 'public'::regnamespace
                      AND relation.relkind IN ('r', 'p', 'v', 'm', 'f')
                      AND relation.relname <> ALL(%s::text[])
                      AND (has_table_privilege(current_user, relation.oid, 'SELECT')
                           OR has_table_privilege(current_user, relation.oid, 'INSERT')
                           OR has_table_privilege(current_user, relation.oid, 'UPDATE')
                           OR has_table_privilege(current_user, relation.oid, 'DELETE')
                           OR has_table_privilege(current_user, relation.oid, 'TRUNCATE')
                           OR has_table_privilege(current_user, relation.oid, 'TRIGGER')
                           OR has_table_privilege(current_user, relation.oid, 'REFERENCES')
                           OR has_any_column_privilege(current_user, relation.oid, 'SELECT')
                           OR has_any_column_privilege(current_user, relation.oid, 'INSERT')
                           OR has_any_column_privilege(current_user, relation.oid, 'UPDATE')
                           OR has_any_column_privilege(current_user, relation.oid, 'REFERENCES'))
               ) AS no_other_public_relation_privileges,
               NOT EXISTS (
                   SELECT 1 FROM pg_catalog.pg_class AS sequence
                    WHERE sequence.relnamespace = 'public'::regnamespace
                      AND sequence.relkind = 'S'
                      AND CASE
                          WHEN sequence.oid = ANY(ARRAY[
                              pg_catalog.to_regclass(pg_catalog.pg_get_serial_sequence(
                                  'public.chat_messages', 'id')),
                              pg_catalog.to_regclass(pg_catalog.pg_get_serial_sequence(
                                  'public.project_memory', 'id'))
                          ]) THEN
                              NOT has_sequence_privilege(current_user, sequence.oid, 'USAGE')
                              OR NOT has_sequence_privilege(current_user, sequence.oid, 'SELECT')
                              OR has_sequence_privilege(current_user, sequence.oid, 'UPDATE')
                          ELSE
                              has_sequence_privilege(current_user, sequence.oid, 'USAGE')
                              OR has_sequence_privilege(current_user, sequence.oid, 'SELECT')
                              OR has_sequence_privilege(current_user, sequence.oid, 'UPDATE')
                      END
               ) AS no_unexpected_sequence_privileges,
               NOT has_function_privilege(current_user,
                   'public.celestin_lock_authorization_write(uuid,text,text,text)', 'EXECUTE')
                   AND NOT has_function_privilege(current_user,
                   'public.celestin_lock_workspace_authorization_write(uuid)', 'EXECUTE')
                   AND NOT has_function_privilege(current_user,
                   'public.celestin_lock_principal_authorization_write(text,text,text)', 'EXECUTE')
                   AND NOT has_function_privilege(current_user,
                   'public.celestin_lock_principal_transition_write(text,text,text,text)', 'EXECUTE')
                   AS no_admin_write_lock_execution,
               NOT EXISTS (
                   SELECT 1 FROM pg_catalog.pg_proc AS procedure
                     JOIN pg_catalog.pg_namespace AS namespace
                       ON namespace.oid = procedure.pronamespace
                    WHERE namespace.nspname IN ('public', 'celestin_private')
                      AND procedure.prorettype <> 'trigger'::regtype
                      AND has_function_privilege(current_user, procedure.oid, 'EXECUTE')
                      AND procedure.oid <> ALL(ARRAY[
                          'public.celestin_request_scope_matches(uuid,uuid)'::regprocedure::oid,
                          'public.celestin_request_workspace_matches(uuid)'::regprocedure::oid,
                          'public.celestin_lock_project_authorization_request(uuid)'::regprocedure::oid
                      ])
               ) AS no_unexpected_function_execution,
               NOT EXISTS (
                   SELECT 1
                     FROM pg_catalog.pg_namespace AS namespace
                     CROSS JOIN LATERAL pg_catalog.aclexplode(
                         COALESCE(namespace.nspacl,
                                  pg_catalog.acldefault('n', namespace.nspowner))
                     ) AS acl
                    WHERE namespace.nspname IN ('public', 'celestin_private')
                      AND acl.is_grantable
                      AND CASE
                          WHEN acl.grantee = 0 THEN true
                          WHEN acl.grantee = role.oid THEN true
                          ELSE pg_catalog.pg_has_role(role.oid, acl.grantee, 'MEMBER')
                      END
               )
               AND NOT EXISTS (
                   SELECT 1
                     FROM pg_catalog.pg_class AS relation
                     CROSS JOIN LATERAL pg_catalog.aclexplode(
                         COALESCE(relation.relacl,
                                  pg_catalog.acldefault(
                                      CASE WHEN relation.relkind = 'S' THEN 'S' ELSE 'r' END,
                                      relation.relowner
                                  ))
                     ) AS acl
                    WHERE relation.relnamespace = 'public'::regnamespace
                      AND acl.is_grantable
                      AND CASE
                          WHEN acl.grantee = 0 THEN true
                          WHEN acl.grantee = role.oid THEN true
                          ELSE pg_catalog.pg_has_role(role.oid, acl.grantee, 'MEMBER')
                      END
               )
               AND NOT EXISTS (
                   SELECT 1
                     FROM pg_catalog.pg_attribute AS attribute
                     JOIN pg_catalog.pg_class AS relation
                       ON relation.oid = attribute.attrelid
                     JOIN pg_catalog.pg_namespace AS namespace
                       ON namespace.oid = relation.relnamespace
                     CROSS JOIN LATERAL pg_catalog.aclexplode(attribute.attacl) AS acl
                    WHERE namespace.nspname !~ '^pg_'
                      AND namespace.nspname <> 'information_schema'
                      AND attribute.attnum > 0
                      AND attribute.attacl IS NOT NULL
                      AND acl.is_grantable
                      AND CASE
                          WHEN acl.grantee = 0 THEN true
                          WHEN acl.grantee = role.oid THEN true
                          ELSE pg_catalog.pg_has_role(role.oid, acl.grantee, 'MEMBER')
                      END
               )
               AND NOT EXISTS (
                   SELECT 1
                     FROM pg_catalog.pg_proc AS procedure
                     JOIN pg_catalog.pg_namespace AS namespace
                       ON namespace.oid = procedure.pronamespace
                     CROSS JOIN LATERAL pg_catalog.aclexplode(
                         COALESCE(procedure.proacl,
                                  pg_catalog.acldefault('f', procedure.proowner))
                     ) AS acl
                    WHERE namespace.nspname !~ '^pg_'
                      AND namespace.nspname <> 'information_schema'
                      AND acl.is_grantable
                      AND CASE
                          WHEN acl.grantee = 0 THEN true
                          WHEN acl.grantee = role.oid THEN true
                          ELSE pg_catalog.pg_has_role(role.oid, acl.grantee, 'MEMBER')
                      END
               ) AS no_privilege_grant_options
          FROM pg_catalog.pg_roles AS role
         WHERE role.rolname = current_user
    """
    tenant_tables = list(PROTECTED_TABLES)
    auth_tables = list(AUTHORIZATION_TABLES)
    row = _run_pool_probe(
        pool,
        timeout,
        query,
        (
            PROJECT_RUNTIME_ROLE,
            PROJECT_RUNTIME_ROLE,
            tenant_tables + auth_tables + ["project_runtime_scopes", "celestin_schema_migrations"],
            tenant_tables,
            tenant_tables,
            scope[0], scope[1],
            scope[0], scope[1],
            scope[0], UUID(int=scope[1].int ^ 1),
            UUID(int=scope[0].int ^ 1), scope[1],
            scope[0], UUID(int=scope[0].int ^ 1),
            auth_tables,
            list(PROJECT_RUNTIME_UNEXPOSED_TABLES) + ["celestin_schema_migrations"],
            tenant_tables,
            list(PROTECTED_TABLES + AUTHORIZATION_TABLES + ("celestin_schema_migrations",)),
        ),
    )
    _validate_project_runtime_probe(row)
    _assert_login_membership_options(pool, timeout, PROJECT_RUNTIME_ROLE)
    _assert_evidence_runtime_privilege_profile(pool, timeout)
    _assert_advisory_lock_privileges(
        pool, timeout, role_profile="project_runtime"
    )
    _assert_expected_policy_catalog(pool, timeout)
    _assert_authorization_function_catalog(pool, timeout, project_runtime=True)
    _assert_expected_trigger_catalog(pool, timeout)
    _assert_active_workspace_owner_guard(pool, timeout)


def _evidence_runtime_privilege_catalog_query() -> str:
    """Inspect effective Evidence access and the exact table/column ACLs."""
    return """
        /* celestin_evidence_runtime_privilege_catalog */
        SELECT relation.relname,
               relation.relowner::bigint,
               owner_role.rolname,
               runtime_role.oid::bigint,
               runtime_role.rolname,
               (
                   runtime_role.oid IS NOT NULL
                   AND reader_role.oid IS NOT NULL
                   AND admin_role.oid IS NOT NULL
                   AND owner_role.oid NOT IN (
                       runtime_role.oid, reader_role.oid, admin_role.oid
                   )
                   AND NOT pg_catalog.pg_has_role(
                       owner_role.oid, runtime_role.oid, 'MEMBER'
                   )
                   AND NOT pg_catalog.pg_has_role(
                       owner_role.oid, reader_role.oid, 'MEMBER'
                   )
                   AND NOT pg_catalog.pg_has_role(
                       owner_role.oid, admin_role.oid, 'MEMBER'
                   )
               ) AS owner_is_separate_from_hosted_runtime_roles,
               login_role.oid::bigint,
               login_role.rolname,
               pg_catalog.has_table_privilege(current_user, relation.oid, 'SELECT'),
               pg_catalog.has_table_privilege(current_user, relation.oid, 'INSERT'),
               pg_catalog.has_table_privilege(current_user, relation.oid, 'UPDATE'),
               pg_catalog.has_table_privilege(current_user, relation.oid, 'DELETE'),
               pg_catalog.has_table_privilege(current_user, relation.oid, 'TRUNCATE'),
               pg_catalog.has_table_privilege(current_user, relation.oid, 'TRIGGER'),
               pg_catalog.has_table_privilege(current_user, relation.oid, 'REFERENCES'),
               COALESCE((
                   SELECT pg_catalog.jsonb_agg(
                       pg_catalog.jsonb_build_object(
                           'grantor_oid', acl.grantor::bigint,
                           'grantor_name', pg_catalog.pg_get_userbyid(acl.grantor),
                           'grantee_oid', acl.grantee::bigint,
                           'grantee_name', CASE
                               WHEN acl.grantee = 0 THEN 'PUBLIC'
                               ELSE pg_catalog.pg_get_userbyid(acl.grantee)
                           END,
                           'privilege_type', acl.privilege_type,
                           'is_grantable', acl.is_grantable
                       ) ORDER BY acl.grantee, acl.grantor, acl.privilege_type
                   )::text
                     FROM pg_catalog.aclexplode(
                         COALESCE(
                             relation.relacl,
                             pg_catalog.acldefault('r', relation.relowner)
                         )
                     ) AS acl
               ), '[]') AS table_acl_entries,
               EXISTS (
                   SELECT 1
                     FROM pg_catalog.pg_attribute AS attribute
                    WHERE attribute.attrelid = relation.oid
                      AND attribute.attnum > 0
                      AND NOT attribute.attisdropped
                      AND attribute.attacl IS NOT NULL
               ) AS has_column_acl
          FROM pg_catalog.pg_class AS relation
          JOIN pg_catalog.pg_namespace AS namespace
            ON namespace.oid = relation.relnamespace
          JOIN pg_catalog.pg_roles AS owner_role
            ON owner_role.oid = relation.relowner
          JOIN pg_catalog.pg_roles AS login_role
            ON login_role.rolname = current_user
          LEFT JOIN pg_catalog.pg_roles AS runtime_role
            ON runtime_role.rolname = %s
          LEFT JOIN pg_catalog.pg_roles AS reader_role
            ON reader_role.rolname = 'celestin_authorization_reader'
          LEFT JOIN pg_catalog.pg_roles AS admin_role
            ON admin_role.rolname = 'celestin_authorization_admin'
         WHERE namespace.nspname = 'public'
           AND relation.relname = ANY(%s::text[])
           AND relation.relkind = 'r'
         ORDER BY relation.relname
    """


def _assert_evidence_runtime_privilege_profile(pool, timeout: float) -> None:
    """Require the reviewed Evidence table grants before hosted readiness."""
    cursor = None
    try:
        with pool.connection(timeout=timeout) as connection:
            cursor = connection.execute(
                _evidence_runtime_privilege_catalog_query(),
                (PROJECT_RUNTIME_ROLE, list(EVIDENCE_RUNTIME_TABLES)),
            )
            rows = cursor.fetchall()
    except Exception:
        raise RuntimeError(
            "PostgreSQL Evidence table privileges could not be verified"
        ) from None
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                raise RuntimeError(
                    "PostgreSQL Evidence privilege cursor could not be closed"
                ) from None
    _validate_evidence_runtime_privilege_catalog(rows)


def _validate_evidence_runtime_privilege_catalog(rows) -> None:
    """Validate exact effective and ACL state for both Evidence tables.

    The table owner retains its inherent ownership ACL. The only permitted
    non-owner ACL is a non-grantable SELECT/INSERT/UPDATE grant to the existing
    project-runtime group. Effective checks run as the exact project login.
    """
    expected_tables = set(EVIDENCE_RUNTIME_TABLES)
    expected_runtime_privileges = {"SELECT", "INSERT", "UPDATE"}
    owner_privileges = {
        "SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES",
        "TRIGGER", "MAINTAIN",
    }
    owner_privilege_sets = (
        set(),
        owner_privileges - {"MAINTAIN"},
        owner_privileges,
    )
    expected_effective = (True, True, True, False, False, False, False)
    acl_fields = {
        "grantor_oid", "grantor_name", "grantee_oid", "grantee_name",
        "privilege_type", "is_grantable",
    }
    actual_tables = {}
    owner_identity = None
    try:
        if not isinstance(rows, (list, tuple)):
            raise ValueError("catalog rows are missing")
        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) != 17:
                raise ValueError("catalog row has an unexpected shape")
            (
                table_name,
                owner_oid,
                owner_name,
                runtime_oid,
                runtime_name,
                owner_is_separate,
                login_oid,
                login_name,
                *properties,
            ) = row
            if (
                not isinstance(table_name, str)
                or table_name not in expected_tables
                or table_name in actual_tables
                or type(owner_oid) is not int
                or owner_oid <= 0
                or not isinstance(owner_name, str)
                or not owner_name
                or type(runtime_oid) is not int
                or runtime_oid <= 0
                or runtime_name != PROJECT_RUNTIME_ROLE
                or type(owner_is_separate) is not bool
                or not owner_is_separate
                or type(login_oid) is not int
                or login_oid <= 0
                or not isinstance(login_name, str)
                or not login_name
                or owner_oid in {runtime_oid, login_oid}
                or runtime_oid == login_oid
            ):
                raise ValueError("catalog identity is missing or inconsistent")
            effective = tuple(properties[:7])
            acl_json, has_column_acl = properties[7:]
            if (
                len(properties) != 9
                or any(type(value) is not bool for value in effective)
                or effective != expected_effective
                or type(has_column_acl) is not bool
                or has_column_acl
                or not isinstance(acl_json, str)
            ):
                raise ValueError("effective privileges or column ACLs are unsafe")

            entries = json.loads(acl_json)
            if not isinstance(entries, list):
                raise ValueError("table ACL entries are missing")
            seen_entries = set()
            owner_entries = set()
            runtime_entries = set()
            for entry in entries:
                if not isinstance(entry, Mapping) or set(entry) != acl_fields:
                    raise ValueError("table ACL entry has an unexpected shape")
                grantor_oid = entry["grantor_oid"]
                grantor_name = entry["grantor_name"]
                grantee_oid = entry["grantee_oid"]
                grantee_name = entry["grantee_name"]
                privilege = entry["privilege_type"]
                grantable = entry["is_grantable"]
                if (
                    type(grantor_oid) is not int
                    or grantor_oid <= 0
                    or not isinstance(grantor_name, str)
                    or not grantor_name
                    or type(grantee_oid) is not int
                    or grantee_oid < 0
                    or not isinstance(grantee_name, str)
                    or not grantee_name
                    or privilege not in owner_privileges
                    or type(grantable) is not bool
                ):
                    raise ValueError("table ACL metadata is incomplete")
                key = (
                    grantor_oid, grantor_name, grantee_oid, grantee_name,
                    privilege, grantable,
                )
                if key in seen_entries:
                    raise ValueError("duplicate table ACL entry")
                seen_entries.add(key)

                if grantee_oid == owner_oid:
                    if (
                        grantor_oid != owner_oid
                        or grantor_name != owner_name
                        or grantee_name != owner_name
                        or not grantable
                    ):
                        raise ValueError("owner ACL identity is inconsistent")
                    owner_entries.add(privilege)
                elif grantee_oid == runtime_oid:
                    if (
                        grantor_oid != owner_oid
                        or grantor_name != owner_name
                        or grantee_name != runtime_name
                        or privilege not in expected_runtime_privileges
                        or grantable
                    ):
                        raise ValueError("project-runtime ACL grant is unsafe")
                    runtime_entries.add(privilege)
                else:
                    # Rejects PUBLIC, direct login grants, auth roles, and all
                    # unreviewed grantees regardless of effective access.
                    raise ValueError("unexpected Evidence table grantee")

            if (
                owner_entries not in owner_privilege_sets
                or runtime_entries != expected_runtime_privileges
            ):
                raise ValueError("Evidence table ACL does not match the exact profile")
            actual_tables[table_name] = True
            if owner_identity is None:
                owner_identity = (owner_oid, owner_name)
            elif owner_identity != (owner_oid, owner_name):
                raise ValueError("Evidence table owners do not match")
    except (TypeError, ValueError):
        raise RuntimeError(
            "PostgreSQL Evidence table privilege profile is unsafe"
        ) from None

    if set(actual_tables) != expected_tables:
        raise RuntimeError("PostgreSQL Evidence table privilege profile is unsafe")


def _run_pool_probe(pool, timeout: float, query: str, params: tuple[Any, ...]):
    with pool.connection(timeout=timeout) as connection:
        cursor = connection.execute(query, params)
        try:
            return cursor.fetchone()
        finally:
            cursor.close()


def _validate_authorization_reader_probe(row) -> None:
    """Validate the reader-role probe result without opening a connection."""
    if (
        row is None
        or len(row) != 21
        or any(type(value) is not bool for value in row)
        or any(row[:5])
        or not all(row[5:10])
        or row[10]
        or not all(row[11:])
    ):
        raise RuntimeError(
            "PostgreSQL authorization-reader role violates its least-privilege requirements"
        )


def _validate_authorization_admin_probe(row) -> None:
    """Validate the authorization-admin probe result without a database."""
    if (
        row is None
        or len(row) != 14
        or any(type(value) is not bool for value in row)
        or any(row[:5])
        or not all(row[5:])
    ):
        raise RuntimeError(
            "PostgreSQL authorization-admin role violates its least-privilege requirements"
        )


def _validate_project_runtime_probe(row) -> None:
    """Validate the project-role probe result without opening a connection."""
    if (
        row is None
        or len(row) != 38
        or any(type(value) is not bool for value in row)
        or any(row[:5])
        or not all(row[5:10])
        or not row[10]
        or row[11]
        or not all(row[12:23])
        or not all(row[23:31])
        or not all(row[31:])
    ):
        raise RuntimeError(
            "PostgreSQL project role violates its project-isolation requirements"
        )

def main() -> None:
    """Start the separate hosted runtime with required TLS and explicit config."""
    settings = HostedRuntimeSettings.from_environment()
    app = create_hosted_mcp_app(settings)
    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover - optional hosted extra
        raise RuntimeError("Hosted HTTP runtime requires the hosted optional dependencies.") from exc
    uvicorn.run(
        app,
        host=settings.bind_host,
        port=settings.port,
        ssl_certfile=str(settings.tls_certfile),
        ssl_keyfile=str(settings.tls_keyfile),
        proxy_headers=False,
        server_header=False,
        date_header=False,
        limit_concurrency=64,
        timeout_keep_alive=5,
    )
