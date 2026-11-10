"""Offline composition tests for the separate hosted HTTP runtime."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
import sys
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest

import orbit.mcp_server.hosted_server as hosted_server
from orbit.mcp_server.hosted_auth import Auth0VerifierConfig
from orbit.mcp_server.hosted_preflight import (
    HostedCatalogEvidence,
    HostedOnboardingRequest,
    HostedProfileCatalogEvidence,
    HostedProjectConfiguration,
    assess_hosted_onboarding,
)
from orbit.mcp_server.hosted_server import (
    HostedRuntimeSettings,
    HostedMCPServices,
    HostedDatabasePools,
    PROTECTED_TABLES,
    WorkspaceContextMiddleware,
    _access_token,
    _assert_safe_authorization_reader,
    _assert_safe_authorization_admin,
    _assert_safe_hosted_pools,
    _assert_safe_project_runtime,
    _workspace_selector,
    create_hosted_mcp_app,
    create_hosted_mcp_server,
    AUTHORIZATION_TABLES,
    HostedRuntimeConfigurationError,
    _expected_policy_catalog,
    _function_specs,
    _trigger_specs,
    _trigger_function_specs,
    _ADVISORY_LOCK_ALLOWED_SIGNATURES,
    _ADVISORY_LOCK_SIGNATURES,
    _advisory_lock_privilege_query,
    _create_pool,
    _reset_postgres_pool_connection,
    _validate_advisory_lock_privilege_catalog,
    _validate_evidence_runtime_privilege_catalog,
    _validate_trigger_catalog,
    _assert_expected_trigger_catalog,
    _validate_function_catalog,
    _validate_policy_catalog,
    _validate_privileged_role_membership_catalog,
    _login_membership_options_query,
    _validate_login_membership_options,
    PRIVILEGED_GROUP_ROLES,
    AUTHORIZATION_ADMIN_ROLE,
    EVIDENCE_RUNTIME_TABLES,
    PROJECT_RUNTIME_UNEXPOSED_TABLES,
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
GOOD_AUTHORIZATION_ADMIN_ROLE_ROW = (False,) * 5 + (True,) * 9
GOOD_PROJECT_ROLE_ROW = (
    (False,) * 5 + (True,) * 6 + (False,) + (True,) * 26
)
GOOD_LOGIN_MEMBERSHIP_OPTION_ROW = (True, True, True, True, True, True, False, True)
_EVIDENCE_OWNER_OID = 10
_EVIDENCE_OWNER_NAME = "celestin_migration_owner"
_EVIDENCE_RUNTIME_OID = 30
_EVIDENCE_LOGIN_OID = 20
_EVIDENCE_OWNER_PRIVILEGES = (
    "SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"
)
_EVIDENCE_RUNTIME_PRIVILEGES = ("SELECT", "INSERT", "UPDATE")


def expected_evidence_privilege_rows():
    rows = []
    for table_name in EVIDENCE_RUNTIME_TABLES:
        acl_entries = [
            {
                "grantor_oid": _EVIDENCE_OWNER_OID,
                "grantor_name": _EVIDENCE_OWNER_NAME,
                "grantee_oid": _EVIDENCE_OWNER_OID,
                "grantee_name": _EVIDENCE_OWNER_NAME,
                "privilege_type": privilege,
                "is_grantable": True,
            }
            for privilege in _EVIDENCE_OWNER_PRIVILEGES
        ]
        acl_entries.extend(
            {
                "grantor_oid": _EVIDENCE_OWNER_OID,
                "grantor_name": _EVIDENCE_OWNER_NAME,
                "grantee_oid": _EVIDENCE_RUNTIME_OID,
                "grantee_name": "celestin_project_runtime",
                "privilege_type": privilege,
                "is_grantable": False,
            }
            for privilege in _EVIDENCE_RUNTIME_PRIVILEGES
        )
        rows.append(
            (
                table_name,
                _EVIDENCE_OWNER_OID,
                _EVIDENCE_OWNER_NAME,
                _EVIDENCE_RUNTIME_OID,
                "celestin_project_runtime",
                True,
                _EVIDENCE_LOGIN_OID,
                "celestin_project_login",
                True, True, True, False, False, False, False,
                json.dumps(acl_entries),
                False,
            )
        )
    return rows


def expected_active_owner_guard_rows():
    from orbit.postgres.migrations.runner import load_migrations

    sql = next(item.sql for item in load_migrations() if item.version == 11)
    source = re.search(
        r"CREATE FUNCTION public\.celestin_require_active_workspace_owner\(\).*?AS \$\$(.*?)\$\$;",
        sql, flags=re.IGNORECASE | re.DOTALL,
    ).group(1)
    return [
        (table, name, "O", event, True, True, True, source, True,
         ("search_path=pg_catalog, public",), "plpgsql", "v", "public", True, False, False, False)
        for table, name, event in (
            ("celestin_workspaces", "celestin_workspace_active_owner_guard", 29),
            ("celestin_workspace_memberships", "celestin_membership_active_owner_guard", 29),
            ("celestin_users", "celestin_user_active_owner_guard", 17),
        )
    ]


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


def expected_function_rows(*, project_runtime, authorization_admin=False):
    rows = []
    for signature, (language, volatility, security_definer, config, source) in _function_specs(
        project_runtime, authorization_admin=authorization_admin
    ).items():
        rows.append((
            signature, "celestin_migration_owner", True,
            False, False, False, False, False, True, language, volatility,
            security_definer, config, source, False, True, False,
        ))
    return rows


def expected_trigger_rows():
    rows = []
    specs = _trigger_specs()
    functions = _trigger_function_specs()
    for (schema, table, name), (tgtype, attributes, function_name) in specs.items():
        config, source = functions[function_name]
        rows.append((
            schema, table, name, "O", tgtype, attributes, False, False, 0, False,
            "public", function_name, "", "celestin_migration_owner", True,
            False, False, False, False, False, True, "plpgsql", "v", True,
            config, source, False, False, False,
        ))
    return rows


_ADVISORY_SIGNATURES = tuple(sorted(_ADVISORY_LOCK_SIGNATURES))
_ADVISORY_TEST_OWNER_OID = 10
_ADVISORY_TEST_OWNER = "celestin_catalog_owner"
_ADVISORY_TEST_CURRENT_USER_OID = 20
_ADVISORY_TEST_CURRENT_USERS = {
    "project_runtime": "celestin_project_login",
    "authorization_reader": "celestin_reader_login",
    "authorization_admin": "celestin_admin_login",
    "migration": "celestin_migration_login",
}


def expected_advisory_rows(role_profile):
    allowed = _ADVISORY_LOCK_ALLOWED_SIGNATURES[role_profile]
    current_user = _ADVISORY_TEST_CURRENT_USERS[role_profile]
    rows = []
    for signature in _ADVISORY_SIGNATURES:
        entries = [
            {
                "grantor_oid": _ADVISORY_TEST_OWNER_OID,
                "grantor": _ADVISORY_TEST_OWNER,
                "grantee_oid": _ADVISORY_TEST_OWNER_OID,
                "grantee": _ADVISORY_TEST_OWNER,
                "privilege_type": "EXECUTE",
                "is_grantable": True,
            }
        ]
        if signature in allowed:
            entries.append(
                {
                    "grantor_oid": _ADVISORY_TEST_OWNER_OID,
                    "grantor": _ADVISORY_TEST_OWNER,
                    "grantee_oid": _ADVISORY_TEST_CURRENT_USER_OID,
                    "grantee": current_user,
                    "privilege_type": "EXECUTE",
                    "is_grantable": False,
                }
            )
        rows.append(
            (
                signature,
                signature in allowed,
                signature in allowed,
                False,
                _ADVISORY_TEST_OWNER_OID,
                _ADVISORY_TEST_OWNER,
                _ADVISORY_TEST_CURRENT_USER_OID,
                current_user,
                entries,
            )
        )
    return rows


def expected_privileged_role_membership_rows(expected_members):
    rows = []
    for group in PRIVILEGED_GROUP_ROLES:
        members = sorted(member for role, member in expected_members if role == group)
        if not members:
            rows.append((group, False, False, False, False, False, False, True, True,
                         None, None, None, None, None, None, None, None, None,
                         None, None, None))
        for member in members:
            rows.append((group, False, False, False, False, False, False, True, True,
                         member, False, True, False, False, False, False, False, True,
                         True, True, True))
    return rows


class FakePool:
    def __init__(
        self,
        row=GOOD_PROJECT_ROLE_ROW,
        *,
        project_runtime=None,
        policy_rows=None,
        function_rows=None,
        trigger_rows=None,
        advisory_rows=None,
        evidence_privilege_rows=None,
        evidence_privilege_error=None,
        authorization_admin=False,
        authorization_admin_role_row=None,
        membership_option_row=None,
        owner_guard_rows=None,
    ):
        self.row = row
        self.authorization_admin = authorization_admin
        self.authorization_admin_role_row = (
            GOOD_AUTHORIZATION_ADMIN_ROLE_ROW
            if authorization_admin_role_row is None else authorization_admin_role_row
        )
        self.project_runtime = (
            (row == GOOD_PROJECT_ROLE_ROW and not authorization_admin)
            if project_runtime is None else project_runtime
        )
        self.policy_rows = expected_policy_rows() if policy_rows is None else policy_rows
        self.function_rows = (
            expected_function_rows(
                project_runtime=self.project_runtime,
                authorization_admin=authorization_admin,
            )
            if function_rows is None else function_rows
        )
        profile = (
            "authorization_admin" if authorization_admin else
            "project_runtime" if self.project_runtime else "authorization_reader"
        )
        self.trigger_rows = (
            expected_trigger_rows() if trigger_rows is None else trigger_rows
        )
        self.advisory_rows = (
            expected_advisory_rows(profile) if advisory_rows is None else advisory_rows
        )
        self.evidence_privilege_rows = (
            expected_evidence_privilege_rows()
            if evidence_privilege_rows is None else evidence_privilege_rows
        )
        self.evidence_privilege_error = evidence_privilege_error
        self.membership_option_row = (
            GOOD_LOGIN_MEMBERSHIP_OPTION_ROW
            if membership_option_row is None else membership_option_row
        )
        self.owner_guard_rows = (
            expected_active_owner_guard_rows() if owner_guard_rows is None else owner_guard_rows
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
                if "celestin_evidence_runtime_privilege_catalog" in query:
                    if pool.evidence_privilege_error is not None:
                        raise pool.evidence_privilege_error
                    return Cursor(rows=pool.evidence_privilege_rows)
                if "celestin_login_membership_option_profile" in query:
                    return Cursor(row=pool.membership_option_row)
                if "pg_catalog.pg_trigger AS catalog_trigger" in query:
                    return Cursor(rows=pool.trigger_rows)
                if "constraint_row.condeferrable" in query:
                    return Cursor(rows=pool.owner_guard_rows)
                if "authorization_admin_role_readiness" in query:
                    return Cursor(row=pool.authorization_admin_role_row)
                if "left(procedure.proname, 12) = 'pg_advisory_'" in query:
                    return Cursor(rows=pool.advisory_rows)
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


def test_hosted_tool_names_preserve_local_contracts_and_add_evidence(settings):
    hosted = create_hosted_mcp_server(
        settings, pools=fake_pools(), token_verifier=FakeVerifier()
    )
    local = create_mcp_server(build_runtime(db_path=":memory:"))

    local_tools = {tool.name: tool for tool in local._tool_manager.list_tools()}
    hosted_tools = {tool.name: tool for tool in hosted._tool_manager.list_tools()}
    assert set(local_tools) == {
        "ingest_chat_message", "save_decision", "search_chat_history",
        "retrieve_context", "ping",
    }
    assert set(hosted_tools) == set(local_tools) | {
        "create_knowledge", "get_knowledge", "associate_evidence",
        "verify_knowledge", "verify_evidence", "supersede_knowledge",
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


@pytest.mark.parametrize("index", [1, 8, 9, 10])
def test_project_runtime_readiness_rejects_bypass_membership_and_ownership(index):
    row = list(GOOD_PROJECT_ROLE_ROW)
    row[index] = not row[index]
    with pytest.raises(RuntimeError, match="project-isolation"):
        _assert_safe_project_runtime(FakePool(row), timeout=1, scope=PROJECT_SCOPE)


def test_project_runtime_role_probe_checks_scope_and_privilege_invariants():
    pool = FakePool(GOOD_PROJECT_ROLE_ROW)
    _assert_safe_project_runtime(pool, timeout=1, scope=PROJECT_SCOPE)
    assert len(pool.queries) == 8
    assert "constraint_row.condeferrable" in pool.queries[-1][0]
    assert pool.queries[-1][1] == ([
        "celestin_workspace_active_owner_guard",
        "celestin_membership_active_owner_guard",
        "celestin_user_active_owner_guard",
    ],)
    assert "celestin_login_membership_option_profile" in pool.queries[1][0]
    assert pool.queries[1][1] == ("celestin_project_runtime",)
    assert "celestin_evidence_runtime_privilege_catalog" in pool.queries[2][0]
    assert "has_table_privilege" in pool.queries[2][0]
    assert "aclexplode" in pool.queries[2][0]
    assert "pg_attribute" in pool.queries[2][0]
    assert "owner_is_separate_from_hosted_runtime_roles" in pool.queries[2][0]
    assert "pg_catalog.pg_has_role" in pool.queries[2][0]
    assert pool.queries[2][1] == (
        "celestin_project_runtime", list(EVIDENCE_RUNTIME_TABLES)
    )
    assert "left(procedure.proname, 12) = 'pg_advisory_'" in pool.queries[3][0]
    assert "has_function_privilege" in pool.queries[3][0]
    assert tuple(PROTECTED_TABLES + AUTHORIZATION_TABLES) + (
        "project_runtime_scopes", "celestin_schema_migrations",
    ) == tuple(pool.queries[0][1][2])
    assert tuple(PROTECTED_TABLES) == tuple(pool.queries[0][1][3])
    assert tuple(AUTHORIZATION_TABLES) == tuple(pool.queries[0][1][15])
    assert tuple(PROJECT_RUNTIME_UNEXPOSED_TABLES) + ("celestin_schema_migrations",) == tuple(
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
    assert "pg_policy" in pool.queries[4][0]
    assert "pg_get_expr" in pool.queries[4][0]
    assert "pg_proc" in pool.queries[5][0]
    assert "aclexplode" in pool.queries[5][0]
    assert "is_grantable" in pool.queries[5][0]
    assert pool.queries[5][1][0] == "celestin_project_runtime"
    assert len(pool.queries[5][1]) == 4
    assert "pg_trigger" in pool.queries[6][0]
    assert "tgenabled" in pool.queries[6][0]
    assert "tgtype" in pool.queries[6][0]
    assert "prosrc" in pool.queries[6][0]
    assert pool.queries[6][1] == (sorted({
        table for schema, table, _ in _trigger_specs() if schema == "public"
    }),)


def test_evidence_runtime_privilege_catalog_accepts_exact_profile():
    rows = expected_evidence_privilege_rows()
    _validate_evidence_runtime_privilege_catalog(rows)

    # Ownership privileges are inherent and may be represented by the catalog
    # default rather than explicit ACL entries.
    without_explicit_owner_acl = deepcopy(rows)
    for index, source_row in enumerate(without_explicit_owner_acl):
        row = list(source_row)
        entries = json.loads(row[15])
        row[15] = json.dumps([
            entry for entry in entries
            if entry["grantee_oid"] != _EVIDENCE_OWNER_OID
        ])
        without_explicit_owner_acl[index] = tuple(row)
    _validate_evidence_runtime_privilege_catalog(without_explicit_owner_acl)


def test_project_runtime_startup_fails_closed_when_evidence_catalog_is_unavailable():
    pool = FakePool(
        evidence_privilege_error=RuntimeError("secret-like connection diagnostic")
    )
    with pytest.raises(RuntimeError, match="Evidence table privileges could not be verified") as error:
        _assert_safe_project_runtime(pool, timeout=1, scope=PROJECT_SCOPE)
    assert "secret-like connection diagnostic" not in str(error.value)
    assert len(pool.queries) == 3


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_select",
        "missing_insert",
        "missing_update",
        "delete",
        "truncate",
        "trigger",
        "references",
        "maintain",
        "public_grant",
        "direct_login_grant",
        "authorization_reader_grant",
        "authorization_admin_grant",
        "grant_option",
        "column_grant",
        "wrong_owner",
        "owner_in_runtime_group",
        "different_table_owner",
        "wrong_grantor",
        "malformed_acl",
        "missing_table",
        "duplicate_table",
        "unexpected_table",
    ],
)
def test_evidence_runtime_privilege_catalog_rejects_unsafe_profiles(mutation):
    rows = deepcopy(expected_evidence_privilege_rows())
    row = list(rows[0])
    effective_index = {
        "select": 8,
        "insert": 9,
        "update": 10,
        "delete": 11,
        "truncate": 12,
        "trigger": 13,
        "references": 14,
    }
    privilege = {
        "delete": "DELETE",
        "truncate": "TRUNCATE",
        "trigger": "TRIGGER",
        "references": "REFERENCES",
        "maintain": "MAINTAIN",
    }
    entries = json.loads(row[15])

    if mutation in {"missing_select", "missing_insert", "missing_update"}:
        name = mutation.removeprefix("missing_")
        row[effective_index[name]] = False
        entries = [
            entry for entry in entries
            if not (
                entry["grantee_oid"] == _EVIDENCE_RUNTIME_OID
                and entry["privilege_type"] == name.upper()
            )
        ]
        row[15] = json.dumps(entries)
        rows[0] = tuple(row)
    elif mutation in privilege:
        if mutation != "maintain":
            row[effective_index[mutation]] = True
        entries.append({
            "grantor_oid": _EVIDENCE_OWNER_OID,
            "grantor_name": _EVIDENCE_OWNER_NAME,
            "grantee_oid": _EVIDENCE_RUNTIME_OID,
            "grantee_name": "celestin_project_runtime",
            "privilege_type": privilege[mutation],
            "is_grantable": False,
        })
        row[15] = json.dumps(entries)
        rows[0] = tuple(row)
    elif mutation == "public_grant":
        entries.append({
            "grantor_oid": _EVIDENCE_OWNER_OID,
            "grantor_name": _EVIDENCE_OWNER_NAME,
            "grantee_oid": 0,
            "grantee_name": "PUBLIC",
            "privilege_type": "SELECT",
            "is_grantable": False,
        })
        row[15] = json.dumps(entries)
        rows[0] = tuple(row)
    elif mutation == "direct_login_grant":
        entries.append({
            "grantor_oid": _EVIDENCE_OWNER_OID,
            "grantor_name": _EVIDENCE_OWNER_NAME,
            "grantee_oid": _EVIDENCE_LOGIN_OID,
            "grantee_name": "celestin_project_login",
            "privilege_type": "SELECT",
            "is_grantable": False,
        })
        row[15] = json.dumps(entries)
        rows[0] = tuple(row)
    elif mutation in {"authorization_reader_grant", "authorization_admin_grant"}:
        entries.append({
            "grantor_oid": _EVIDENCE_OWNER_OID,
            "grantor_name": _EVIDENCE_OWNER_NAME,
            "grantee_oid": 40 if mutation == "authorization_reader_grant" else 50,
            "grantee_name": (
                "celestin_authorization_reader"
                if mutation == "authorization_reader_grant"
                else "celestin_authorization_admin"
            ),
            "privilege_type": "SELECT",
            "is_grantable": False,
        })
        row[15] = json.dumps(entries)
        rows[0] = tuple(row)
    elif mutation == "grant_option":
        entries[-1]["is_grantable"] = True
        row[15] = json.dumps(entries)
        rows[0] = tuple(row)
    elif mutation == "column_grant":
        row[16] = True
        rows[0] = tuple(row)
    elif mutation == "wrong_owner":
        row[1] = _EVIDENCE_LOGIN_OID
        row[2] = "celestin_project_login"
        row[5] = False
        rows[0] = tuple(row)
    elif mutation == "owner_in_runtime_group":
        row[5] = False
        rows[0] = tuple(row)
    elif mutation == "different_table_owner":
        second = list(rows[1])
        second[1] = 11
        second[2] = "other_schema_owner"
        rows[1] = tuple(second)
    elif mutation == "wrong_grantor":
        entries[-1]["grantor_oid"] = 41
        entries[-1]["grantor_name"] = "other_schema_owner"
        row[15] = json.dumps(entries)
        rows[0] = tuple(row)
    elif mutation == "malformed_acl":
        row[15] = "not-json"
        rows[0] = tuple(row)
    elif mutation == "missing_table":
        rows.pop()
    elif mutation == "duplicate_table":
        rows.append(rows[0])
    elif mutation == "unexpected_table":
        extra = list(rows[0])
        extra[0] = "unreviewed_evidence_table"
        rows.append(tuple(extra))

    with pytest.raises(RuntimeError, match="privilege profile is unsafe"):
        _validate_evidence_runtime_privilege_catalog(rows)


def test_authorization_reader_profile_fails_closed_for_unscoped_lock_wrapper():
    pool = FakePool(GOOD_CONTROL_ROLE_ROW)
    with pytest.raises(RuntimeError, match="lock-key scope is not verified"):
        _assert_safe_authorization_reader(pool, timeout=1)
    assert len(pool.queries) == 4
    assert tuple(PROTECTED_TABLES + AUTHORIZATION_TABLES) == tuple(pool.queries[0][1][2])
    assert tuple(AUTHORIZATION_TABLES) == tuple(pool.queries[0][1][3])
    assert tuple(PROTECTED_TABLES) == tuple(pool.queries[0][1][4])
    assert "celestin_login_membership_option_profile" in pool.queries[1][0]
    assert pool.queries[1][1] == ("celestin_authorization_reader",)
    assert "left(procedure.proname, 12) = 'pg_advisory_'" in pool.queries[2][0]
    assert "pg_catalog.pg_proc" in pool.queries[3][0]


@pytest.mark.parametrize("index", range(len(GOOD_AUTHORIZATION_ADMIN_ROLE_ROW)))
def test_runtime_refuses_unsafe_authorization_admin_role(index):
    row = list(GOOD_AUTHORIZATION_ADMIN_ROLE_ROW)
    row[index] = not row[index]
    with pytest.raises(RuntimeError, match="authorization-admin"):
        _assert_safe_authorization_admin(
            FakePool(authorization_admin=True, authorization_admin_role_row=row),
            timeout=1,
        )


def test_authorization_admin_probe_keeps_unscoped_lock_helpers_unready():
    pool = FakePool(authorization_admin=True)
    with pytest.raises(RuntimeError, match="lock-key scope is not verified"):
        _assert_safe_authorization_admin(pool, timeout=1)
    assert len(pool.queries) == 4
    assert "authorization_admin_role_readiness" in pool.queries[0][0]
    assert "celestin_login_membership_option_profile" in pool.queries[1][0]
    assert pool.queries[1][1] == (AUTHORIZATION_ADMIN_ROLE,)
    assert "has_function_privilege" in pool.queries[2][0]
    assert pool.queries[3][1][0] == AUTHORIZATION_ADMIN_ROLE
    assert tuple(pool.queries[3][1][1:]) == tuple(
        "public." + signature
        for signature in _function_specs(False, authorization_admin=True)
    )


def test_request_pool_readiness_does_not_run_privileged_admin_preflight(monkeypatch):
    pools = fake_pools()
    checked = []

    def check_reader(pool, timeout):
        checked.append(("reader", pool, timeout))

    def check_project(pool, timeout, scope):
        checked.append(("project", scope, pool, timeout))

    def reject_admin_preflight(*_args, **_kwargs):
        raise AssertionError("request readiness must not run privileged provisioning checks")

    monkeypatch.setattr(hosted_server, "_assert_safe_authorization_reader", check_reader)
    monkeypatch.setattr(hosted_server, "_assert_safe_project_runtime", check_project)
    monkeypatch.setattr(hosted_server, "_assert_safe_authorization_admin", reject_admin_preflight)

    _assert_safe_hosted_pools(pools, timeout=2)

    assert checked == [
        ("reader", pools.control_pool, 2),
        ("project", PROJECT_SCOPE, pools.project_pools[PROJECT_SCOPE], 2),
    ]


@pytest.mark.parametrize(
    "project_runtime,authorization_admin",
    ((False, False), (False, True)),
)
def test_function_catalog_does_not_accept_unscoped_reader_or_admin_locks(
    project_runtime, authorization_admin
):
    rows = expected_function_rows(
        project_runtime=project_runtime,
        authorization_admin=authorization_admin,
    )
    with pytest.raises(RuntimeError, match="lock-key scope is not verified"):
        _validate_function_catalog(
            rows,
            project_runtime=project_runtime,
            authorization_admin=authorization_admin,
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_helper",
        "wrong_body",
        "wrong_owner",
        "owner_superuser",
        "owner_bypassrls",
        "owner_createrole",
        "owner_createdb",
        "owner_replication",
        "owner_role_membership",
        "wrong_language",
        "wrong_volatility",
        "invoker_security",
        "unsafe_search_path",
        "public_execute",
        "not_executable_by_admin",
        "unexpected_execute_grant",
        "missing_boolean_metadata",
    ],
)
def test_authorization_admin_function_catalog_fails_closed(mutation):
    rows = expected_function_rows(project_runtime=False, authorization_admin=True)
    index = 0
    changed = list(rows[index])
    if mutation == "missing_helper":
        rows.pop(index)
    elif mutation == "wrong_body":
        changed[13] += " -- changed"
    elif mutation == "wrong_owner":
        changed[1] = "celestin_authorization_admin_login"
        changed[2] = False
    elif mutation == "owner_superuser":
        changed[3] = True
    elif mutation == "owner_bypassrls":
        changed[4] = True
    elif mutation == "owner_createrole":
        changed[5] = True
    elif mutation == "owner_createdb":
        changed[6] = True
    elif mutation == "owner_replication":
        changed[7] = True
    elif mutation == "owner_role_membership":
        changed[8] = False
    elif mutation == "wrong_language":
        changed[9] = "sql"
    elif mutation == "wrong_volatility":
        changed[10] = "s"
    elif mutation == "invoker_security":
        changed[11] = False
    elif mutation == "unsafe_search_path":
        changed[12] = ("search_path=public",)
    elif mutation == "public_execute":
        changed[14] = True
    elif mutation == "not_executable_by_admin":
        changed[15] = False
    elif mutation == "unexpected_execute_grant":
        changed[16] = True
    elif mutation == "missing_boolean_metadata":
        changed[3] = None
    if mutation != "missing_helper":
        rows[index] = tuple(changed)

    with pytest.raises(RuntimeError, match="authorization helper"):
        _validate_function_catalog(
            rows, project_runtime=False, authorization_admin=True
        )


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
        "not_security_definer",
        "unexpected_security_definer",
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
    elif mutation == "not_security_definer":
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
    elif mutation == "not_security_definer":
        changed[11] = False
    elif mutation == "unexpected_security_definer":
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


@pytest.mark.parametrize("role_profile", tuple(_ADVISORY_LOCK_ALLOWED_SIGNATURES))
def test_advisory_lock_privilege_profiles_allow_only_required_locks(role_profile):
    _validate_advisory_lock_privilege_catalog(
        expected_advisory_rows(role_profile), role_profile=role_profile
    )


@pytest.mark.parametrize("role_profile", tuple(_ADVISORY_LOCK_ALLOWED_SIGNATURES))
@pytest.mark.parametrize("signature", _ADVISORY_SIGNATURES)
@pytest.mark.parametrize("field_index", (1, 2, 3))
def test_advisory_lock_privilege_profiles_reject_unexpected_effective_access(
    role_profile, signature, field_index
):
    rows = expected_advisory_rows(role_profile)
    index = _ADVISORY_SIGNATURES.index(signature)
    changed = list(rows[index])
    changed[field_index] = not changed[field_index]
    rows[index] = tuple(changed)
    with pytest.raises(RuntimeError, match="advisory-lock privileges"):
        _validate_advisory_lock_privilege_catalog(rows, role_profile=role_profile)


def test_advisory_lock_catalog_rejects_missing_core_functions_duplicate_rows_and_unknown_roles():
    rows = expected_advisory_rows("project_runtime")
    with pytest.raises(RuntimeError, match="advisory-lock privileges"):
        _validate_advisory_lock_privilege_catalog(
            [row for row in rows if row[0] != "pg_advisory_xact_lock(bigint)"],
            role_profile="project_runtime",
        )
    with pytest.raises(RuntimeError, match="advisory-lock privileges"):
        _validate_advisory_lock_privilege_catalog(
            rows + [rows[0]], role_profile="project_runtime"
        )
    with pytest.raises(RuntimeError, match="advisory-lock privileges"):
        _validate_advisory_lock_privilege_catalog(
            [row for row in rows if row[0] != "pg_try_advisory_xact_lock_shared(integer,integer)"],
            role_profile="project_runtime",
        )
    with pytest.raises(RuntimeError, match="advisory-lock privileges"):
        _validate_advisory_lock_privilege_catalog(
            rows + [("pg_advisory_unreviewed(bigint)", False, False, False)],
            role_profile="project_runtime",
        )
    with pytest.raises(RuntimeError, match="role profile"):
        _validate_advisory_lock_privilege_catalog(rows, role_profile="unknown")


@pytest.mark.parametrize(
    "bad_row",
    [
        ("pg_advisory_lock(bigint)", None, False, False),
        ("pg_advisory_lock(bigint)", False, False, None),
        ("pg_advisory_lock(bigint)", False, False),
    ],
)
def test_advisory_lock_catalog_rejects_missing_or_inconsistent_acl_metadata(bad_row):
    rows = expected_advisory_rows("project_runtime")
    rows[0] = bad_row
    with pytest.raises(RuntimeError, match="advisory-lock privileges"):
        _validate_advisory_lock_privilege_catalog(rows, role_profile="project_runtime")


@pytest.mark.parametrize(
    "mutation",
    (
        "grant_option",
        "public_grant",
        "unexpected_grantee",
        "unexpected_grantor",
        "unknown_grantor",
        "owner_identity_mismatch",
        "current_identity_mismatch",
        "unknown_grantee",
        "missing_grantability",
        "duplicate_acl_entry",
    ),
)
def test_advisory_lock_catalog_rejects_unsafe_acl_entries(mutation):
    rows = expected_advisory_rows("migration")
    index = _ADVISORY_SIGNATURES.index("pg_advisory_lock(bigint)")
    row = list(rows[index])
    entries = deepcopy(row[8])
    migration_entry = next(entry for entry in entries if entry["grantee_oid"] == 20)

    if mutation == "grant_option":
        migration_entry["is_grantable"] = True
    elif mutation == "public_grant":
        entries.append(
            {
                "grantor_oid": _ADVISORY_TEST_OWNER_OID,
                "grantor": _ADVISORY_TEST_OWNER,
                "grantee_oid": 0,
                "grantee": "PUBLIC",
                "privilege_type": "EXECUTE",
                "is_grantable": False,
            }
        )
    elif mutation == "unexpected_grantee":
        entries.append(
            {
                "grantor_oid": _ADVISORY_TEST_OWNER_OID,
                "grantor": _ADVISORY_TEST_OWNER,
                "grantee_oid": 30,
                "grantee": "celestin_unapproved_role",
                "privilege_type": "EXECUTE",
                "is_grantable": False,
            }
        )
    elif mutation == "unexpected_grantor":
        migration_entry["grantor_oid"] = 40
        migration_entry["grantor"] = "celestin_unapproved_grantor"
    elif mutation == "unknown_grantor":
        migration_entry["grantor"] = None
    elif mutation == "owner_identity_mismatch":
        row[5] = "different_catalog_owner"
    elif mutation == "current_identity_mismatch":
        row[7] = "different_migration_login"
    elif mutation == "unknown_grantee":
        migration_entry["grantee"] = None
    elif mutation == "missing_grantability":
        migration_entry.pop("is_grantable")
    elif mutation == "duplicate_acl_entry":
        entries.append(deepcopy(migration_entry))

    row[8] = entries
    rows[index] = tuple(row)
    with pytest.raises(RuntimeError, match="advisory-lock privileges"):
        _validate_advisory_lock_privilege_catalog(rows, role_profile="migration")


def test_advisory_lock_catalog_rejects_inherited_access_without_a_direct_grant():
    rows = expected_advisory_rows("project_runtime")
    index = _ADVISORY_SIGNATURES.index("pg_advisory_lock(bigint)")
    row = list(rows[index])
    row[1] = True  # Effective EXECUTE inherited through an unexpected membership.
    rows[index] = tuple(row)

    with pytest.raises(RuntimeError, match="advisory-lock privileges"):
        _validate_advisory_lock_privilege_catalog(rows, role_profile="project_runtime")


def test_advisory_lock_catalog_rejects_inconsistent_login_identity_across_overloads():
    rows = expected_advisory_rows("project_runtime")
    row = list(rows[-1])
    row[6] = 30
    row[7] = "another_project_login"
    rows[-1] = tuple(row)

    with pytest.raises(RuntimeError, match="advisory-lock privileges"):
        _validate_advisory_lock_privilege_catalog(rows, role_profile="project_runtime")


def test_advisory_lock_readiness_query_checks_effective_catalog_privileges():
    query = _advisory_lock_privilege_query()
    assert "pg_catalog.pg_proc" in query
    assert "namespace.nspname = 'pg_catalog'" in query
    assert "pg_catalog.left(procedure.proname, 12) = 'pg_advisory_'" in query
    assert "has_function_privilege" in query
    assert "pg_catalog.aclexplode" in query
    assert "pg_catalog.acldefault" in query
    assert "direct_execute" in query
    assert "public_execute" in query
    assert "acl.is_grantable" in query
    assert "acl.grantor" in query
    assert "acl.grantee" in query
    assert "grantor.rolname" in query
    assert "grantee.rolname" in query
    assert "jsonb_agg" in query


def test_privileged_role_membership_catalog_accepts_exact_no_login_group_roster():
    expected = {
        ("celestin_project_runtime", "celestin_it_project_a_1234abcd"),
        ("celestin_project_runtime", "celestin_it_project_b_1234abcd"),
        ("celestin_authorization_reader", "celestin_it_reader_1234abcd"),
        ("celestin_authorization_admin", "celestin_it_admin_1234abcd"),
    }
    _validate_privileged_role_membership_catalog(
        expected_privileged_role_membership_rows(expected),
        expected_members=expected,
    )


@pytest.mark.parametrize("index", range(len(GOOD_LOGIN_MEMBERSHIP_OPTION_ROW)))
def test_login_membership_option_validator_fails_closed_on_profile_drift(index):
    row = list(GOOD_LOGIN_MEMBERSHIP_OPTION_ROW)
    row[index] = not row[index]
    with pytest.raises(RuntimeError, match="membership options"):
        _validate_login_membership_options(row)


def test_login_membership_option_validator_rejects_missing_catalog_metadata():
    row = list(GOOD_LOGIN_MEMBERSHIP_OPTION_ROW)
    row[-1] = False
    with pytest.raises(RuntimeError, match="membership options"):
        _validate_login_membership_options(row)


def test_login_membership_option_query_has_pre_16_catalog_fallbacks():
    query = _login_membership_options_query()
    assert "server_version_num" in query
    assert "to_jsonb(membership)->>'inherit_option'" in query
    assert "to_jsonb(membership)->>'set_option'" in query
    assert "ELSE login_role.rolinherit" in query
    assert "ELSE TRUE" in query
    assert "membership.inherit_option" not in query
    assert "membership.set_option" not in query


@pytest.mark.parametrize("index", [4, 5, 6, 7])
def test_project_runtime_readiness_rejects_membership_option_drift(index):
    membership_row = list(GOOD_LOGIN_MEMBERSHIP_OPTION_ROW)
    membership_row[index] = not membership_row[index]
    pool = FakePool(membership_option_row=membership_row)
    with pytest.raises(RuntimeError, match="membership options"):
        hosted_server._assert_safe_project_runtime(pool, timeout=1, scope=PROJECT_SCOPE)


@pytest.mark.parametrize("profile", ["authorization_reader", "authorization_admin"])
def test_control_login_readiness_checks_membership_options(profile):
    membership_row = list(GOOD_LOGIN_MEMBERSHIP_OPTION_ROW)
    membership_row[5] = False
    if profile == "authorization_reader":
        pool = FakePool(
            GOOD_CONTROL_ROLE_ROW,
            project_runtime=False,
            membership_option_row=membership_row,
        )
        expected_group = "celestin_authorization_reader"
    else:
        pool = FakePool(
            GOOD_AUTHORIZATION_ADMIN_ROLE_ROW,
            project_runtime=False,
            authorization_admin=True,
            membership_option_row=membership_row,
        )
        expected_group = AUTHORIZATION_ADMIN_ROLE

    with pytest.raises(RuntimeError, match="membership options"):
        if profile == "authorization_reader":
            _assert_safe_authorization_reader(pool, timeout=1)
        else:
            _assert_safe_authorization_admin(pool, timeout=1)
    assert any(
        "celestin_login_membership_option_profile" in query and params == (expected_group,)
        for query, params in pool.queries
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_group",
        "unexpected_group",
        "missing_member",
        "unexpected_member",
        "group_login",
        "group_superuser",
        "group_bypassrls",
        "group_createrole",
        "group_createdb",
        "group_replication",
        "group_no_inherit",
        "group_parent_membership",
        "group_missing_metadata",
        "group_admin_option",
        "member_not_login",
        "member_superuser",
        "member_bypassrls",
        "member_createrole",
        "member_createdb",
        "member_replication",
        "member_no_inherit",
        "member_admin_option",
        "member_missing_admin_option",
        "member_inherit_option",
        "member_set_option",
        "member_incomplete_options",
        "member_missing_metadata",
        "duplicate_row",
        "malformed_row",
    ],
)
def test_privileged_role_membership_catalog_rejects_unsafe_or_incomplete_roster(mutation):
    expected = {
        ("celestin_project_runtime", "project_a"),
        ("celestin_authorization_reader", "reader"),
        ("celestin_authorization_admin", "admin"),
    }
    rows = expected_privileged_role_membership_rows(expected)
    if mutation == "missing_group":
        rows = [row for row in rows if row[0] != AUTHORIZATION_ADMIN_ROLE]
    elif mutation == "unexpected_group":
        rows.append(("unexpected_group", False, False, False, False, False, False, True, True,
                     None, None, None, None, None, None, None, None, None,
                     None, None, None))
    elif mutation == "missing_member":
        rows = [row for row in rows if row[9] != "admin"]
    elif mutation == "unexpected_member":
        rows.append((AUTHORIZATION_ADMIN_ROLE, False, False, False, False, False, False, True,
                     True, "unexpected", False, True, False, False, False, False, False, True,
                     True, True, True))
    elif mutation in {
        "group_login", "group_superuser", "group_bypassrls", "group_createrole",
        "group_createdb", "group_replication", "group_no_inherit",
        "group_parent_membership", "group_missing_metadata", "group_admin_option",
    }:
        target = next(i for i, row in enumerate(rows) if row[0] == AUTHORIZATION_ADMIN_ROLE)
        changed = list(rows[target])
        group_indices = {
            "group_login": 1,
            "group_superuser": 2,
            "group_bypassrls": 3,
            "group_createrole": 4,
            "group_createdb": 5,
            "group_replication": 6,
            "group_no_inherit": 7,
            "group_parent_membership": 8,
            "group_admin_option": 10,
        }
        if mutation == "group_missing_metadata":
            changed[2] = None
        else:
            changed[group_indices[mutation]] = (
                None if mutation == "group_admin_option" else not changed[group_indices[mutation]]
            )
        rows[target] = tuple(changed)
    elif mutation in {
        "member_not_login", "member_superuser", "member_bypassrls",
        "member_createrole", "member_createdb", "member_replication",
        "member_no_inherit", "member_admin_option", "member_missing_admin_option",
        "member_inherit_option", "member_set_option", "member_incomplete_options",
        "member_missing_metadata",
    }:
        target = next(i for i, row in enumerate(rows) if row[0] == AUTHORIZATION_ADMIN_ROLE)
        changed = list(rows[target])
        index = {
            "member_not_login": 11,
            "member_superuser": 12,
            "member_bypassrls": 13,
            "member_createrole": 14,
            "member_createdb": 15,
            "member_replication": 16,
            "member_no_inherit": 17,
            "member_admin_option": 10,
            "member_missing_admin_option": 10,
            "member_missing_metadata": 12,
            "member_inherit_option": 18,
            "member_set_option": 19,
            "member_incomplete_options": 20,
        }[mutation]
        changed[index] = (
            None
            if mutation in {
                "member_missing_metadata", "member_missing_admin_option",
                "member_incomplete_options",
            }
            else not changed[index]
        )
        rows[target] = tuple(changed)
    elif mutation == "duplicate_row":
        rows.append(rows[0])
    elif mutation == "malformed_row":
        rows[0] = rows[0][:-1]

    with pytest.raises(RuntimeError, match="membership catalog"):
        _validate_privileged_role_membership_catalog(rows, expected_members=expected)


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "disabled",
        "retargeted_relation",
        "unexpected",
        "wrong_events",
        "wrong_update_columns",
        "internal",
        "when_condition",
        "arguments",
        "constraint",
        "wrong_function_schema",
        "wrong_function_identity",
        "wrong_function_arguments",
        "function_owner",
        "owner_bypassrls",
        "owner_membership",
        "wrong_language",
        "wrong_volatility",
        "not_security_definer",
        "unsafe_search_path",
        "altered_function_body",
        "public_execute",
        "runtime_execute",
        "unexpected_execute_grant",
    ],
)
def test_trigger_catalog_rejects_missing_disabled_or_altered_authorization_triggers(mutation):
    rows = expected_trigger_rows()
    index = 0
    changed = list(rows[index])
    if mutation == "missing":
        rows.pop(index)
    elif mutation == "disabled":
        changed[3] = "D"
    elif mutation == "retargeted_relation":
        changed[1] = "other_relation"
    elif mutation == "unexpected":
        changed[2] = "celestin_unexpected_lock"
    elif mutation == "wrong_events":
        changed[4] ^= 4
    elif mutation == "wrong_update_columns":
        index = next(i for i, row in enumerate(rows) if row[2] == "celestin_user_identity_auth_lock")
        changed = list(rows[index])
        changed[5] = ("issuer", "subject")
    elif mutation == "internal":
        changed[6] = True
    elif mutation == "when_condition":
        changed[7] = True
    elif mutation == "arguments":
        changed[8] = 1
    elif mutation == "constraint":
        changed[9] = True
    elif mutation == "wrong_function_schema":
        changed[10] = "celestin_private"
    elif mutation == "wrong_function_identity":
        changed[11] = "celestin_other_lock"
    elif mutation == "wrong_function_arguments":
        changed[12] = "target_workspace uuid"
    elif mutation == "function_owner":
        changed[14] = False
    elif mutation == "owner_bypassrls":
        changed[16] = True
    elif mutation == "owner_membership":
        changed[20] = False
    elif mutation == "wrong_language":
        changed[21] = "sql"
    elif mutation == "wrong_volatility":
        changed[22] = "s"
    elif mutation == "not_security_definer":
        changed[23] = False
    elif mutation == "unsafe_search_path":
        changed[24] = ("search_path=public",)
    elif mutation == "altered_function_body":
        changed[25] = changed[25].replace("RETURN NEW;", "RETURN OLD;")
    elif mutation == "public_execute":
        changed[26] = True
    elif mutation == "runtime_execute":
        changed[27] = True
    elif mutation == "unexpected_execute_grant":
        changed[28] = True
    if mutation not in {"missing", "unexpected"}:
        rows[index] = tuple(changed)
    elif mutation == "unexpected":
        rows.append(tuple(changed))

    with pytest.raises(RuntimeError, match="authorization trigger catalog"):
        _validate_trigger_catalog(rows)


def test_trigger_catalog_accepts_expected_attachments_and_checks_all_auth_relations():
    pool = FakePool()
    _assert_expected_trigger_catalog(pool, timeout=1)
    query, params = pool.queries[-1]
    assert "pg_catalog.pg_trigger AS catalog_trigger" in query
    assert "catalog_trigger.tgenabled" in query
    assert "catalog_trigger.tgtype" in query
    assert "catalog_trigger.tgqual IS NOT NULL" in query
    assert "procedure.prosecdef" in query
    assert "pg_catalog.aclexplode" in query
    assert params == (sorted({
        table for schema, table, _ in _trigger_specs() if schema == "public"
    }),)


def test_trigger_column_catalog_order_does_not_change_update_of_meaning():
    rows = expected_trigger_rows()
    index = next(
        i for i, row in enumerate(rows)
        if row[2] == "celestin_user_identity_auth_lock"
    )
    row = list(rows[index])
    row[5] = tuple(reversed(row[5]))
    rows[index] = tuple(row)
    _validate_trigger_catalog(rows)


def test_trigger_expectations_match_checked_in_0008_and_0009_migrations():
    migration_dir = Path(__file__).parents[1] / "src" / "orbit" / "postgres" / "migrations"
    migration_sql = "\n".join(
        (migration_dir / name).read_text(encoding="utf-8")
        for name in (
            "0008_project_scoped_runtime_roles.sql",
            "0009_project_request_lock_scope.sql",
        )
    )
    for function_name, (_config, expected_body) in _trigger_function_specs().items():
        matches = list(re.finditer(
            rf"CREATE(?: OR REPLACE)? FUNCTION public\.{re.escape(function_name)}\s*\(\)"
            rf".*?AS \$\$(.*?)\$\$;",
            migration_sql,
            flags=re.IGNORECASE | re.DOTALL,
        ))
        assert matches, f"missing migration body for {function_name}"
        match = matches[-1]
        from orbit.mcp_server.hosted_server import _canonical_sql_expression

        assert _canonical_sql_expression(match.group(1)) == _canonical_sql_expression(
            expected_body
        )

    event_clauses = {
        "celestin_workspace_auth_lock": "BEFORE INSERT OR UPDATE OR DELETE ON public.celestin_workspaces",
        "celestin_project_auth_lock": "BEFORE INSERT OR UPDATE OR DELETE ON public.celestin_projects",
        "celestin_workspace_membership_auth_lock": "BEFORE INSERT OR UPDATE OR DELETE ON public.celestin_workspace_memberships",
        "celestin_project_membership_auth_lock": "BEFORE INSERT OR UPDATE OR DELETE ON public.celestin_project_memberships",
        "celestin_service_identity_auth_lock": "BEFORE INSERT OR UPDATE OR DELETE ON public.celestin_service_identities",
        "celestin_service_grant_auth_lock": "BEFORE INSERT OR UPDATE OR DELETE ON public.celestin_service_project_grants",
        "celestin_project_scope_auth_lock": "BEFORE INSERT OR UPDATE OR DELETE ON celestin_private.project_runtime_scopes",
        "celestin_user_identity_auth_lock": "BEFORE UPDATE OF status, issuer, subject ON public.celestin_users",
    }
    assert set(event_clauses) == {key[2] for key in _trigger_specs()}
    for trigger_name, clause in event_clauses.items():
        match = re.search(
            rf"CREATE TRIGGER {re.escape(trigger_name)}\b(.*?);",
            migration_sql,
            flags=re.IGNORECASE | re.DOTALL,
        )
        assert match is not None, f"missing migration trigger {trigger_name}"
        assert clause.casefold() in " ".join(match.group(1).split()).casefold()


def test_postgres_pool_reset_clears_session_state_and_disables_prepare_cache(monkeypatch):
    captured = {}

    class FakePool:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setitem(
        sys.modules,
        "psycopg_pool",
        SimpleNamespace(ConnectionPool=FakePool),
    )
    pool = _create_pool(
        "host=localhost dbname=unit_test",
        SimpleNamespace(connection_timeout=3, pool_timeout=4),
        min_size=0,
        max_size=1,
    )
    assert isinstance(pool, FakePool)
    assert captured["kwargs"] == {
        "connect_timeout": 3,
        "prepare_threshold": None,
    }
    assert captured["reset"] is _reset_postgres_pool_connection
    assert captured["open"] is False

    class Cursor:
        closed = False

        def close(self):
            self.closed = True

    class Connection:
        autocommit = False

        def __init__(self):
            self.rollback_calls = 0
            self.queries = []
            self.cursor = Cursor()

        def rollback(self):
            self.rollback_calls += 1

        def execute(self, query):
            self.queries.append(query)
            return self.cursor

    connection = Connection()
    _reset_postgres_pool_connection(connection)
    assert connection.rollback_calls == 1
    assert connection.queries == ["DISCARD ALL"]
    assert connection.cursor.closed
    assert connection.autocommit is False


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


def _good_preflight_inputs():
    now = datetime(2026, 10, 24, 8, 0, tzinfo=timezone.utc)
    workspace_id, project_id = map(str, PROJECT_SCOPE)
    logins = {
        "project_runtime": "celestin_project_login",
        "authorization_reader": "celestin_reader_login",
        "authorization_admin": "celestin_admin_login",
        "migration": "celestin_migration_login",
    }
    members = (
        ("celestin_project_runtime", logins["project_runtime"]),
        ("celestin_authorization_reader", logins["authorization_reader"]),
        ("celestin_authorization_admin", logins["authorization_admin"]),
    )
    request = HostedOnboardingRequest(workspace_id, project_id)
    configuration = HostedProjectConfiguration(
        workspace_id=workspace_id,
        project_id=project_id,
        database_identity="offline-test-db",
        provider_name="provider-unknown",
        expected_schema_version=11,
        profile_logins=logins,
        expected_group_members=members,
        max_evidence_age_seconds=300,
        provider_capability="unknown",
    )
    evidence = HostedCatalogEvidence(
        database_identity="offline-test-db",
        schema_version=11,
        observed_at=now - timedelta(seconds=10),
        profiles=(
            HostedProfileCatalogEvidence(
                "project_runtime", logins["project_runtime"],
                GOOD_PROJECT_ROLE_ROW, tuple(expected_advisory_rows("project_runtime")),
                workspace_id, project_id,
            ),
            HostedProfileCatalogEvidence(
                "authorization_reader", logins["authorization_reader"],
                GOOD_CONTROL_ROLE_ROW, tuple(expected_advisory_rows("authorization_reader")),
            ),
            HostedProfileCatalogEvidence(
                "authorization_admin", logins["authorization_admin"],
                GOOD_AUTHORIZATION_ADMIN_ROLE_ROW,
                tuple(expected_advisory_rows("authorization_admin")),
            ),
            HostedProfileCatalogEvidence(
                "migration", logins["migration"],
                advisory_lock_rows=tuple(expected_advisory_rows("migration")),
            ),
        ),
        function_catalog_rows={
            "project_runtime": tuple(expected_function_rows(project_runtime=True)),
            "authorization_reader": tuple(expected_function_rows(project_runtime=False)),
            "authorization_admin": tuple(expected_function_rows(
                project_runtime=False, authorization_admin=True
            )),
        },
        policy_catalog_rows=tuple(expected_policy_rows()),
        trigger_catalog_rows=tuple(expected_trigger_rows()),
        privileged_role_membership_rows=tuple(
            expected_privileged_role_membership_rows(set(members))
        ),
    )
    return request, configuration, evidence, now


def test_offline_preflight_separates_profile_structure_from_security_blockers():
    request, configuration, evidence, now = _good_preflight_inputs()
    report = assess_hosted_onboarding(request, configuration, evidence, now=now)
    assert report.onboarding.status == "STRUCTURAL_PASS"
    # Catalog shapes match, but the current source intentionally rejects the
    # unscoped reader/admin lock wrappers; neither assessment is operationally ready.
    assert report.request_runtime.profile_structure_status == "STRUCTURAL_PASS"
    assert report.privileged_preflight.profile_structure_status == "STRUCTURAL_PASS"
    assert report.request_runtime.status == "BLOCKED"
    assert report.privileged_preflight.status == "BLOCKED"
    assert "lock_scope_unverified" in report.request_runtime.diagnostics
    assert "lock_scope_unverified" in report.privileged_preflight.diagnostics
    serialized = report.as_dict()
    assert serialized["evidence_level"] == "offline_structural_only"
    assert serialized["live_database_verified"] is False
    assert serialized["service_permitted"] is False
    assert serialized["provisioning_permitted"] is False
    assert serialized["operational_status"] == "BLOCKED"


def test_offline_runtime_structure_is_independent_when_privileged_profiles_are_missing():
    request, configuration, evidence, now = _good_preflight_inputs()
    evidence = replace(
        evidence,
        profiles=tuple(
            profile for profile in evidence.profiles
            if profile.profile not in {"authorization_admin", "migration"}
        ),
    )
    report = assess_hosted_onboarding(request, configuration, evidence, now=now)
    assert report.request_runtime.profile_structure_status == "STRUCTURAL_PASS"
    assert report.privileged_preflight.status == "BLOCKED"


def test_offline_privileged_structure_is_independent_when_runtime_acl_is_missing():
    request, configuration, evidence, now = _good_preflight_inputs()
    profiles = tuple(
        replace(profile, advisory_lock_rows=None)
        if profile.profile == "project_runtime" else profile
        for profile in evidence.profiles
    )
    report = assess_hosted_onboarding(
        request, configuration, replace(evidence, profiles=profiles), now=now
    )
    assert report.request_runtime.status == "BLOCKED"
    assert report.privileged_preflight.profile_structure_status == "STRUCTURAL_PASS"
    assert "missing_catalog_evidence" in report.request_runtime.diagnostics


@pytest.mark.parametrize(
    ("request_update", "configuration_update", "expected_code"),
    [
        ({"workspace_id": None}, {}, "invalid_request"),
        ({}, {"project_id": "cccccccc-cccc-4ccc-8ccc-cccccccccccc"},
         "project_binding_mismatch"),
        ({}, {"profile_logins": {"project_runtime": "celestin_project_login"}},
         "invalid_role_inventory"),
    ],
)
def test_offline_onboarding_rejects_missing_conflicting_or_incomplete_inputs(
    request_update, configuration_update, expected_code
):
    request, configuration, evidence, now = _good_preflight_inputs()
    report = assess_hosted_onboarding(
        replace(request, **request_update),
        replace(configuration, **configuration_update),
        evidence,
        now=now,
    )
    assert report.onboarding.status == "BLOCKED"
    assert expected_code in report.onboarding.diagnostics
    assert report.request_runtime.status == "BLOCKED"


def test_offline_preflight_rejects_unexpected_advisory_lock_privilege():
    request, configuration, evidence, now = _good_preflight_inputs()
    rows = [list(row) for row in expected_advisory_rows("project_runtime")]
    rows[0][2] = True
    profiles = tuple(
        replace(profile, advisory_lock_rows=tuple(tuple(row) for row in rows))
        if profile.profile == "project_runtime" else profile
        for profile in evidence.profiles
    )
    report = assess_hosted_onboarding(
        request, configuration, replace(evidence, profiles=profiles), now=now
    )
    assert report.request_runtime.status == "BLOCKED"
    assert "catalog_profile_invalid" in report.request_runtime.diagnostics


@pytest.mark.parametrize(
    ("profile_name", "malformed_probe"),
    [
        ("project_runtime", GOOD_PROJECT_ROLE_ROW[:-1]),
        ("authorization_reader", GOOD_CONTROL_ROLE_ROW[:-1]),
    ],
)
def test_offline_preflight_rejects_incomplete_role_probe_rows(
    profile_name, malformed_probe
):
    request, configuration, evidence, now = _good_preflight_inputs()
    profiles = tuple(
        replace(profile, role_probe_row=malformed_probe)
        if profile.profile == profile_name else profile
        for profile in evidence.profiles
    )
    report = assess_hosted_onboarding(
        request, configuration, replace(evidence, profiles=profiles), now=now
    )
    assert report.request_runtime.status == "BLOCKED"
    assert "catalog_profile_invalid" in report.request_runtime.diagnostics


@pytest.mark.parametrize(
    ("evidence_update", "expected_code"),
    [
        ({"observed_at": datetime(2026, 10, 24, 7, 0, tzinfo=timezone.utc)},
         "stale_catalog_evidence"),
        ({"database_identity": "another-db"}, "contradictory_catalog_evidence"),
        ({"policy_catalog_rows": None}, "policy_catalog_invalid"),
        ({"trigger_catalog_rows": ()}, "trigger_catalog_invalid"),
    ],
)
def test_offline_preflight_rejects_stale_contradictory_or_incomplete_catalogs(
    evidence_update, expected_code
):
    request, configuration, evidence, now = _good_preflight_inputs()
    report = assess_hosted_onboarding(
        request, configuration, replace(evidence, **evidence_update), now=now
    )
    assert report.request_runtime.status == "BLOCKED"
    assert expected_code in report.request_runtime.diagnostics


def test_unknown_provider_capability_and_secret_like_values_stay_blocked_and_redacted():
    request, configuration, evidence, now = _good_preflight_inputs()
    evidence = replace(
        evidence,
        policy_catalog_rows=(("postgres://operator:do-not-print@example.invalid/db",),),
    )
    report = assess_hosted_onboarding(request, configuration, evidence, now=now)
    output = str(report.as_dict())
    assert report.as_dict()["operational_status"] == "BLOCKED"
    assert "policy_catalog_invalid" in report.request_runtime.diagnostics
    assert "do-not-print" not in output
    assert "example.invalid" not in output
    assert "provider_capability_unverified" in {
        diagnostic["code"]
        for diagnostic in report.as_dict()["operational_diagnostics"]
    }


def test_offline_preflight_rejects_catalog_for_another_project_scope():
    request, configuration, evidence, now = _good_preflight_inputs()
    profiles = tuple(
        replace(profile, project_id="cccccccc-cccc-4ccc-8ccc-cccccccccccc")
        if profile.profile == "project_runtime" else profile
        for profile in evidence.profiles
    )
    report = assess_hosted_onboarding(
        request, configuration, replace(evidence, profiles=profiles), now=now
    )
    assert report.request_runtime.status == "BLOCKED"
    assert "contradictory_catalog_evidence" in report.request_runtime.diagnostics


def test_offline_preflight_rejects_duplicate_role_evidence():
    request, configuration, evidence, now = _good_preflight_inputs()
    evidence = replace(evidence, profiles=evidence.profiles + (evidence.profiles[0],))
    report = assess_hosted_onboarding(request, configuration, evidence, now=now)
    assert report.request_runtime.status == "BLOCKED"
    assert report.privileged_preflight.status == "BLOCKED"
    assert "contradictory_catalog_evidence" in report.request_runtime.diagnostics


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


def test_hosted_evidence_create_uses_authorized_request_uow():
    from orbit.evidence.model import KnowledgeBundle, KnowledgeRecord, KnowledgeType

    class Evidence:
        def create_knowledge(self, scope, knowledge_type, claim, sources):
            assert scope == "resolved-project-write"
            assert knowledge_type.value == "decision"
            assert claim == "approved direction"
            assert sources[0].source_id == "source-1"
            return KnowledgeBundle(KnowledgeRecord(
                "k-1", "project-a", KnowledgeType.DECISION, "approved direction",
                "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00",
            ), ())

    class Request:
        evidence_service = Evidence()

        def evidence_project(self, action):
            assert action == "write"
            return "resolved-project-write"

    class Boundary:
        def execute_authorized_request(self, token, project, action, operation, **kwargs):
            assert token is token_value and project == "project-a" and action == "write"
            return operation(Request())

    services = HostedMCPServices(Boundary(), read_scope="memory:read", write_scope="memory:write")
    token_value = type("Token", (), {"scopes": ["memory:write"], "claims": {"sub": "user-a"}})()
    token = _access_token.set(token_value)
    try:
        result = services.create_knowledge("project-a", "decision", "approved direction", [
            {"evidence_type": "document", "source_id": "source-1"}
        ])
    finally:
        _access_token.reset(token)
    assert result["record"]["knowledge_id"] == "k-1"


def test_hosted_evidence_cross_tenant_denial_precedes_storage():
    from orbit.mcp_server.hosted_scope import HostedAccessDenied

    class Boundary:
        def execute_authorized_request(self, *args, **kwargs):
            raise HostedAccessDenied("denied")

    services = HostedMCPServices(Boundary(), read_scope="memory:read", write_scope="memory:write")
    token = _access_token.set(type("Token", (), {"scopes": ["memory:read"]})())
    try:
        with pytest.raises(ToolError, match="ACCESS_DENIED"):
            services.get_knowledge("project-b", "tenant-a-knowledge-id")
    finally:
        _access_token.reset(token)


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
