"""Offline composition tests for the separate hosted HTTP runtime."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import replace
from uuid import UUID

import httpx
import pytest

from orbit.mcp_server.hosted_auth import Auth0VerifierConfig
from orbit.mcp_server.hosted_server import (
    HostedRuntimeSettings,
    HostedMCPServices,
    HostedDatabasePools,
    PROTECTED_TABLES,
    WorkspaceContextMiddleware,
    _access_token,
    _assert_safe_authorization_reader,
    _assert_safe_project_runtime,
    _workspace_selector,
    create_hosted_mcp_app,
    create_hosted_mcp_server,
    AUTHORIZATION_TABLES,
    HostedRuntimeConfigurationError,
    _expected_policy_catalog,
    _function_specs,
    _validate_function_catalog,
    _validate_policy_catalog,
)
from orbit.mcp_server.hosted_scope import (
    HostedAuthorizationUnavailable,
    VerifiedPrincipal,
)
from orbit.mcp_server.server import build_runtime, create_mcp_server
from mcp.server.fastmcp.exceptions import ToolError


ISSUER = "https://celestin-example.auth0.com/"
AUDIENCE = "https://api.celestin.example"
PROJECT_SCOPE = (
    UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
    UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
)
GOOD_CONTROL_ROLE_ROW = (
    (False,) * 5 + (True,) * 5 + (False,) + (True,) * 10
)
GOOD_PROJECT_ROLE_ROW = (
    (False,) * 5 + (True,) * 6 + (False,) + (True,) * 26
)


def expected_policy_rows():
    relations, policies = _expected_policy_catalog()
    rows = []
    for (schema, table), (relkind, row_security, force_row_security) in relations.items():
        specs = policies[(schema, table)]
        for name, command, permissive, roles, using, check in specs:
            rows.append((
                schema, table, relkind, row_security, force_row_security,
                name, command, permissive, roles, using, check,
            ))
    return rows


def expected_function_rows(*, project_runtime):
    rows = []
    for signature, (language, volatility, security_definer, config, source) in _function_specs(project_runtime).items():
        rows.append((
            signature, "celestin_migration_owner", True,
            False, False, False, False, False, True, language, volatility,
            security_definer, config, source, False, True, False,
        ))
    return rows


class FakePool:
    def __init__(
        self,
        row=GOOD_PROJECT_ROLE_ROW,
        *,
        project_runtime=None,
        policy_rows=None,
        function_rows=None,
    ):
        self.row = row
        self.project_runtime = (
            row == GOOD_PROJECT_ROLE_ROW if project_runtime is None else project_runtime
        )
        self.policy_rows = expected_policy_rows() if policy_rows is None else policy_rows
        self.function_rows = (
            expected_function_rows(project_runtime=self.project_runtime)
            if function_rows is None else function_rows
        )
        self.open_calls = 0
        self.open_timeouts = []
        self.close_calls = 0
        self.queries = []
        self.returned_connections = 0

    def open(self, *, wait, timeout=None):
        self.open_calls += 1
        self.open_timeouts.append(timeout)

    def close(self):
        self.close_calls += 1

    def getconn(self, *, timeout):
        self.returned_connections += 1
        return object()

    def putconn(self, _connection):
        self.returned_connections -= 1

    @contextmanager
    def connection(self, *, timeout):
        pool = self

        class Connection:
            def execute(self, query, params):
                pool.queries.append((query, params))
                if query.count("%s") != len(params):
                    raise AssertionError("probe placeholder count does not match bound parameters")
                if "pg_catalog.pg_policy AS policy" in query:
                    return Cursor(rows=pool.policy_rows)
                if "procedure.oid::regprocedure::text" in query:
                    return Cursor(rows=pool.function_rows)
                return Cursor(pool.row)

        yield Connection()


class Cursor:
    def __init__(self, row=None, rows=()):
        self.row = row
        self.rows = list(rows)
        self.closed = False

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows

    def close(self):
        self.closed = True


class FakeVerifier:
    async def verify_token(self, token):
        return None


@pytest.fixture
def settings(tmp_path):
    cert = tmp_path / "server.crt"
    key = tmp_path / "server.key"
    cert.write_text("test certificate path only", encoding="utf-8")
    key.write_text("test private key path only", encoding="utf-8")
    return HostedRuntimeSettings(
        authorization_dsn="postgresql://not-a-real-connection",
        project_dsn_env_by_scope={
            PROJECT_SCOPE: "CELESTIN_PROJECT_POSTGRES_DSN_TEST_PROJECT"
        },
        auth=Auth0VerifierConfig(
            ISSUER, AUDIENCE, "auth0", "RS256", "memory:read",
            "memory:write", {"human-client": "human"}
        ),
        public_url="https://celestin.example",
        tls_certfile=cert,
        tls_keyfile=key,
        project_role_isolation_enabled=True,
    )


def fake_pools():
    return HostedDatabasePools(
        FakePool(GOOD_CONTROL_ROLE_ROW, project_runtime=False),
        {PROJECT_SCOPE: FakePool(GOOD_PROJECT_ROLE_ROW, project_runtime=True)},
        timeout=1,
    )


def test_hosted_tool_names_and_schemas_match_local_stdio(settings):
    hosted = create_hosted_mcp_server(
        settings, pools=fake_pools(), token_verifier=FakeVerifier()
    )
    local = create_mcp_server(build_runtime(db_path=":memory:"))

    local_tools = {tool.name: tool for tool in local._tool_manager.list_tools()}
    hosted_tools = {tool.name: tool for tool in hosted._tool_manager.list_tools()}
    assert set(hosted_tools) == set(local_tools) == {
        "ingest_chat_message", "save_decision", "search_chat_history",
        "retrieve_context", "ping",
    }
    for name in local_tools:
        assert hosted_tools[name].parameters == local_tools[name].parameters
        assert hosted_tools[name].fn_metadata.output_schema == local_tools[name].fn_metadata.output_schema
        assert hosted_tools[name].description == local_tools[name].description


def test_hosted_app_health_is_public_but_mcp_requires_a_bearer_token(settings):
    pools = fake_pools()
    app = create_hosted_mcp_app(settings, pools=pools, token_verifier=FakeVerifier())

    async def request():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://test"
        ) as client:
            health = await client.get("/healthz")
            protected = await client.post(
                "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}
            )
            return health, protected

    health, protected = asyncio.run(request())
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert protected.status_code == 401
    assert all(pool.open_calls == pool.close_calls == 0 for pool in pools.all_pools)


def test_workspace_context_is_request_local_and_preserves_duplicate_headers():
    observed = []

    class App:
        async def __call__(self, scope, receive, send):
            observed.append(_workspace_selector.get())

    app = WorkspaceContextMiddleware(App())

    async def invoke(values):
        scope = {
            "type": "http",
            "headers": [(b"x-celestin-workspace-id", value) for value in values],
        }
        await app(scope, None, None)

    asyncio.run(invoke([b"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"]))
    asyncio.run(invoke([
        b"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        b"bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    ]))
    assert observed == [
        ("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",),
        (
            "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        ),
    ]
    assert _workspace_selector.get() is None


@pytest.mark.parametrize("index", range(len(GOOD_CONTROL_ROLE_ROW)))
def test_runtime_refuses_unsafe_authorization_reader(index):
    row = list(GOOD_CONTROL_ROLE_ROW)
    row[index] = not row[index]
    with pytest.raises(RuntimeError, match="authorization-reader"):
        _assert_safe_authorization_reader(FakePool(row), timeout=1)


@pytest.mark.parametrize("index", range(len(GOOD_PROJECT_ROLE_ROW)))
def test_runtime_refuses_unsafe_project_role(index):
    row = list(GOOD_PROJECT_ROLE_ROW)
    row[index] = not row[index]
    with pytest.raises(RuntimeError, match="project-isolation"):
        _assert_safe_project_runtime(FakePool(row), timeout=1, scope=PROJECT_SCOPE)


def test_project_runtime_role_probe_checks_scope_and_privilege_invariants():
    pool = FakePool(GOOD_PROJECT_ROLE_ROW)
    _assert_safe_project_runtime(pool, timeout=1, scope=PROJECT_SCOPE)
    assert len(pool.queries) == 3
    assert tuple(PROTECTED_TABLES + AUTHORIZATION_TABLES) + (
        "project_runtime_scopes", "celestin_schema_migrations",
    ) == tuple(pool.queries[0][1][2])
    assert tuple(PROTECTED_TABLES) == tuple(pool.queries[0][1][3])
    assert tuple(AUTHORIZATION_TABLES) == tuple(pool.queries[0][1][15])
    assert tuple(PROTECTED_TABLES[2:]) + ("celestin_schema_migrations",) == tuple(
        pool.queries[0][1][16]
    )
    assert "relrowsecurity" in pool.queries[0][0]
    assert "relforcerowsecurity" in pool.queries[0][0]
    assert "pg_has_role" in pool.queries[0][0]
    assert "authorization_read_only" in pool.queries[0][0]
    assert "is_grantable" in pool.queries[0][0]
    assert "rolreplication" in pool.queries[0][0]
    assert "exact_project_login_mapping" in pool.queries[0][0]
    assert "current_tool_grants" in pool.queries[0][0]
    assert "current_tool_sequence_grants" in pool.queries[0][0]
    assert "no_unexposed_content_grants" in pool.queries[0][0]
    assert "has_any_column_privilege" in pool.queries[0][0]
    assert "celestin_request_scope_matches" in pool.queries[0][0]
    assert "pg_policy" in pool.queries[1][0]
    assert "pg_get_expr" in pool.queries[1][0]
    assert "pg_proc" in pool.queries[2][0]
    assert "aclexplode" in pool.queries[2][0]
    assert "is_grantable" in pool.queries[2][0]
    assert pool.queries[2][1][0] == "celestin_project_runtime"
    assert len(pool.queries[2][1]) == 4


def test_authorization_reader_role_has_no_tenant_content_access():
    pool = FakePool(GOOD_CONTROL_ROLE_ROW)
    _assert_safe_authorization_reader(pool, timeout=1)
    assert len(pool.queries) == 2
    assert tuple(PROTECTED_TABLES + AUTHORIZATION_TABLES) == tuple(pool.queries[0][1][2])
    assert tuple(AUTHORIZATION_TABLES) == tuple(pool.queries[0][1][3])
    assert tuple(PROTECTED_TABLES) == tuple(pool.queries[0][1][4])


@pytest.mark.parametrize(
    "mutation",
    [
        "unconditional",
        "permissive_or",
        "missing_check",
        "unexpected_role",
        "restrictive",
        "wrong_command",
        "unexpected_policy",
        "missing_policy",
        "disabled_rls",
        "not_forced",
    ],
)
def test_policy_catalog_rejects_widened_missing_or_unexpected_policies(mutation):
    rows = expected_policy_rows()
    target = next(i for i, row in enumerate(rows) if row[5] == "celestin_project_request_scope")
    changed = list(rows[target])
    if mutation == "unconditional":
        changed[9] = "true"
    elif mutation == "permissive_or":
        changed[9] = "public.celestin_request_scope_matches(workspace_id, project_id) OR true"
    elif mutation == "missing_check":
        changed[10] = None
    elif mutation == "unexpected_role":
        changed[8] = ("PUBLIC",)
    elif mutation == "restrictive":
        changed[7] = False
    elif mutation == "wrong_command":
        changed[6] = "r"
    elif mutation == "unexpected_policy":
        rows.append((*rows[target][:5], "celestin_extra", "r", True, ("PUBLIC",), "true", None))
    elif mutation == "missing_policy":
        rows.pop(target)
    elif mutation == "disabled_rls":
        changed[3] = False
    elif mutation == "not_forced":
        changed[4] = False
    if mutation not in {"unexpected_policy", "missing_policy"}:
        rows[target] = tuple(changed)

    with pytest.raises(RuntimeError, match="RLS catalog"):
        _validate_policy_catalog(rows)


@pytest.mark.parametrize(
    "mutation",
    [
        "scope_returns_true",
        "workspace_returns_true",
        "lock_namespace_changed",
        "not_database_owner",
        "owner_is_runtime",
        "owner_bypassrls",
        "owner_replication",
        "owner_membership",
        "security_definer",
        "unsafe_search_path",
        "public_execute",
        "not_executable_by_runtime",
        "unexpected_execute_grant",
        "missing_helper",
    ],
)
def test_function_catalog_rejects_altered_or_overprivileged_helpers(mutation):
    rows = expected_function_rows(project_runtime=True)
    index = 0
    if mutation == "workspace_returns_true":
        index = 1
    elif mutation == "lock_namespace_changed":
        index = 2
    changed = list(rows[index])
    if mutation in {"scope_returns_true", "workspace_returns_true"}:
        changed[13] = "SELECT session_user IS NOT NULL OR true"
    elif mutation == "lock_namespace_changed":
        changed[13] = changed[13].replace("celestin-auth-v1:", "attacker-lock:")
    elif mutation in {"not_database_owner", "owner_is_runtime"}:
        changed[1] = "celestin_project_runtime_login"
        changed[2] = False
    elif mutation == "owner_bypassrls":
        changed[4] = True
    elif mutation == "owner_replication":
        changed[7] = True
    elif mutation == "owner_membership":
        changed[8] = False
    elif mutation == "security_definer":
        changed[11] = True
    elif mutation == "unsafe_search_path":
        changed[12] = ("search_path=public",)
    elif mutation == "public_execute":
        changed[14] = True
    elif mutation == "not_executable_by_runtime":
        changed[15] = False
    elif mutation == "unexpected_execute_grant":
        changed[16] = True
    elif mutation == "missing_helper":
        rows.pop(index)
    if mutation != "missing_helper":
        rows[index] = tuple(changed)

    with pytest.raises(RuntimeError, match="authorization helper"):
        _validate_function_catalog(rows, project_runtime=True)


def test_sql_normalization_preserves_string_literal_semantics():
    from orbit.mcp_server.hosted_server import _canonical_sql_expression

    assert _canonical_sql_expression("public.fn ( row_id )") == "fn(row_id)"
    assert _canonical_sql_expression("SELECT 'Tenant-A'") != _canonical_sql_expression(
        "select 'tenant-a'"
    )


@pytest.mark.parametrize("index", [15, 16, 18])
def test_project_readiness_rejects_negative_scope_probe(index):
    row = list(GOOD_PROJECT_ROLE_ROW)
    row[index] = False
    with pytest.raises(RuntimeError, match="project-isolation"):
        _assert_safe_project_runtime(
            FakePool(tuple(row)), timeout=1, scope=PROJECT_SCOPE
        )


def test_project_pool_selection_requires_exact_resolver_scope_and_never_falls_back():
    from orbit.mcp_server.hosted_scope import ResolvedProjectScope

    principal = VerifiedPrincipal("issuer", "subject", "human")
    scope = ResolvedProjectScope._issue(
        principal=principal,
        principal_id=UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
        workspace_id=PROJECT_SCOPE[0],
        project_id=PROJECT_SCOPE[1],
        project_key="demo",
        permissions=frozenset({"read"}),
    )
    pools = fake_pools()
    lease = pools.project_connection(scope)
    assert lease._pool is pools.project_pools[PROJECT_SCOPE]
    lease.close()
    assert pools.project_pools[PROJECT_SCOPE].returned_connections == 0

    other_scope = ResolvedProjectScope._issue(
        principal=principal,
        principal_id=scope.principal_id,
        workspace_id=PROJECT_SCOPE[0],
        project_id=UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd"),
        project_key="other",
        permissions=frozenset({"read"}),
    )
    with pytest.raises(HostedAuthorizationUnavailable, match="project database is unavailable"):
        pools.project_connection(other_scope)


def test_hosted_settings_require_explicit_isolation_enablement(settings):
    from dataclasses import replace
    from orbit.mcp_server.hosted_server import HostedRuntimeConfigurationError

    with pytest.raises(HostedRuntimeConfigurationError, match="disabled"):
        replace(settings, project_role_isolation_enabled=False)


def test_api_scope_denial_occurs_before_request_database_boundary():
    class Boundary:
        called = False

        def execute_authorized_request(self, *args, **kwargs):
            self.called = True
            raise AssertionError("database boundary must not be called")

    boundary = Boundary()
    services = HostedMCPServices(
        boundary, read_scope="memory:read", write_scope="memory:write"
    )
    token = _access_token.set(type("Token", (), {"scopes": ["memory:read"]})())
    try:
        with pytest.raises(ToolError, match="ACCESS_DENIED"):
            services.ingest_chat_message("project", "conversation", "user", "text")
    finally:
        _access_token.reset(token)
    assert not boundary.called


def test_hosted_operation_scope_mapping_cannot_be_reconfigured():
    with pytest.raises(ValueError, match="memory:read and memory:write"):
        HostedMCPServices(object(), read_scope="read", write_scope="write")


def test_non_loopback_host_requires_explicit_deployment_opt_in(settings):
    with pytest.raises(ValueError, match="binds to loopback"):
        HostedRuntimeSettings(
            settings.authorization_dsn,
            settings.project_dsn_env_by_scope,
            settings.auth,
            settings.public_url,
            settings.tls_certfile,
            settings.tls_keyfile,
            project_role_isolation_enabled=True,
            bind_host="0.0.0.0",
        )

    reviewed = HostedRuntimeSettings(
        settings.authorization_dsn,
        settings.project_dsn_env_by_scope,
        settings.auth,
        settings.public_url,
        settings.tls_certfile,
        settings.tls_keyfile,
        project_role_isolation_enabled=True,
        bind_host="0.0.0.0",
        allow_non_loopback=True,
    )
    assert reviewed.bind_host == "0.0.0.0"


def test_public_url_must_match_the_hosted_mcp_origin(settings):
    with pytest.raises(ValueError, match="HTTPS URL"):
        HostedRuntimeSettings(
            settings.authorization_dsn,
            settings.project_dsn_env_by_scope,
            settings.auth,
            settings.public_url + "/unexpected-prefix",
            settings.tls_certfile,
            settings.tls_keyfile,
            project_role_isolation_enabled=True,
        )


def test_database_timeouts_are_bounded_positive_integers(settings):
    with pytest.raises(ValueError, match="timeouts must be positive"):
        HostedRuntimeSettings(
            settings.authorization_dsn,
            settings.project_dsn_env_by_scope,
            settings.auth,
            settings.public_url,
            settings.tls_certfile,
            settings.tls_keyfile,
            project_role_isolation_enabled=True,
            statement_timeout_ms=0,
        )


def test_missing_tls_configuration_is_rejected_without_opening_pool(settings):
    pools = fake_pools()
    with pytest.raises(ValueError, match="TLS certificate"):
        bad = HostedRuntimeSettings(
            settings.authorization_dsn,
            settings.project_dsn_env_by_scope,
            settings.auth,
            settings.public_url,
            settings.tls_certfile.with_name("missing.crt"),
            settings.tls_keyfile,
            project_role_isolation_enabled=True,
        )
        create_hosted_mcp_app(bad, pools=pools, token_verifier=FakeVerifier())
    assert all(pool.open_calls == 0 for pool in pools.all_pools)
