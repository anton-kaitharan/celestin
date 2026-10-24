"""Separately configured Auth0/PostgreSQL Streamable HTTP MCP runtime.

The local ``celestin``/``orbit``/``memory-bridge`` entry points remain the
existing stdio/SQLite server. This module is imported only by the explicit
``celestin-hosted`` command.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
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
PROJECT_RUNTIME_ROLE = "celestin_project_runtime"
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


@dataclass(frozen=True, slots=True)
class _HandlerRuntime:
    persistence: HostedMCPServices
    retrieval: HostedMCPServices


class HostedMCPToolHandlers:
    """Async HTTP wrappers that reuse local validation and public MCP schemas."""

    def __init__(self, services: HostedMCPServices) -> None:
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
        kwargs={"connect_timeout": settings.connection_timeout},
        min_size=min_size,
        max_size=max_size,
        timeout=settings.pool_timeout,
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


def _function_specs(project_runtime: bool):
    if project_runtime:
        return {
            "celestin_request_scope_matches(uuid,uuid)": (
                "sql", "s", False,
                ("search_path=pg_catalog, celestin_private",),
                _PROJECT_SCOPE_FUNCTION_BODY,
            ),
            "celestin_request_workspace_matches(uuid)": (
                "sql", "s", False,
                ("search_path=pg_catalog, celestin_private",),
                _PROJECT_WORKSPACE_FUNCTION_BODY,
            ),
            "celestin_lock_project_authorization_request(uuid)": (
                "plpgsql", "v", False,
                ("search_path=pg_catalog, celestin_private",),
                _PROJECT_LOCK_FUNCTION_BODY,
            ),
        }
    return {
        "celestin_lock_authorization_request(uuid,text,text,text)": (
            "plpgsql", "v", False, ("search_path=pg_catalog",),
            _READER_LOCK_FUNCTION_BODY,
        ),
    }


def _validate_function_catalog(rows, *, project_runtime: bool) -> None:
    expected = _function_specs(project_runtime)
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
            signature = _canonical_sql_expression(str(signature))
            if signature in actual:
                raise ValueError("duplicate function signature")
            actual[signature] = (
                str(owner), bool(owner_is_database_owner), bool(owner_superuser), bool(owner_bypassrls),
                bool(owner_createrole), bool(owner_createdb), bool(owner_replication),
                bool(owner_has_no_other_memberships), str(language),
                str(volatility), bool(security_definer), tuple(config or ()),
                _canonical_sql_expression(str(source)), bool(public_execute),
                bool(current_execute), bool(unexpected_execute_grants),
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
            or owner in {AUTHORIZATION_READER_ROLE, PROJECT_RUNTIME_ROLE, "celestin_authorization_admin"}
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


def _function_catalog_query(project_runtime: bool) -> tuple[str, tuple[str, ...]]:
    signatures = tuple(_function_specs(project_runtime))
    regprocedures = tuple(
        "public." + signature for signature in signatures
    )
    expected_execute_role = PROJECT_RUNTIME_ROLE if project_runtime else AUTHORIZATION_READER_ROLE
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


def _assert_authorization_function_catalog(pool, timeout: float, *, project_runtime: bool) -> None:
    query, signatures = _function_catalog_query(project_runtime)
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
    _validate_function_catalog(rows, project_runtime=project_runtime)


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
    if (
        row is None
        or any(bool(value) for value in row[:5])
        or not all(bool(value) for value in row[5:10])
        or bool(row[10])
        or not all(bool(value) for value in row[11:21])
    ):
        raise RuntimeError("PostgreSQL authorization-reader role violates its least-privilege requirements")
    _assert_authorization_function_catalog(pool, timeout, project_runtime=False)


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
            list(PROTECTED_TABLES[2:]) + ["celestin_schema_migrations"],
            tenant_tables,
            list(PROTECTED_TABLES + AUTHORIZATION_TABLES + ("celestin_schema_migrations",)),
        ),
    )
    if (
        row is None
        or any(bool(value) for value in row[:5])
        or not all(bool(value) for value in row[5:10])
        or not bool(row[10])
        or bool(row[11])
        or not all(bool(value) for value in row[12:23])
        or not all(bool(value) for value in row[23:31])
        or not all(bool(value) for value in row[31:38])
    ):
        raise RuntimeError("PostgreSQL project role violates its project-isolation requirements")
    _assert_expected_policy_catalog(pool, timeout)
    _assert_authorization_function_catalog(pool, timeout, project_runtime=True)


def _run_pool_probe(pool, timeout: float, query: str, params: tuple[Any, ...]):
    with pool.connection(timeout=timeout) as connection:
        cursor = connection.execute(query, params)
        try:
            return cursor.fetchone()
        finally:
            cursor.close()

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
