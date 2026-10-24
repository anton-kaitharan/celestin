from __future__ import annotations

import json
import os
import unittest
from unittest.mock import Mock, patch

from mcp.server.fastmcp.exceptions import ToolError

from orbit.mcp_server import server
from orbit.mcp_server.authorization import (
    READ,
    WRITE,
    AuthenticatedPrincipal,
    ProjectAccessDenied,
    ProjectAccessPolicy,
)


def error_code(error: ToolError) -> str:
    return json.loads(str(error))["error"]["code"]


class ProjectAuthorizationTests(unittest.TestCase):
    def test_celestin_environment_name_precedes_legacy_orbit_alias(self) -> None:
        with patch.dict(
            os.environ,
            {
                "CELESTIN_LOCAL_PROJECT_ACCESS": '{"celestin-project":["read"]}',
                "ORBIT_LOCAL_PROJECT_ACCESS": '{"orbit-project":["write"]}',
            },
            clear=True,
        ):
            policy = ProjectAccessPolicy.from_environment()
        policy.authorize(None, "celestin-project", READ)
        with self.assertRaises(ProjectAccessDenied):
            policy.authorize(None, "orbit-project", WRITE)

    def test_legacy_orbit_environment_name_remains_supported(self) -> None:
        with patch.dict(
            os.environ,
            {"ORBIT_LOCAL_PROJECT_ACCESS": '{"legacy-project":["read"]}'},
            clear=True,
        ):
            policy = ProjectAccessPolicy.from_environment()
        policy.authorize(None, "legacy-project", READ)

    def test_unconfigured_local_policy_denies_every_project(self) -> None:
        policy = ProjectAccessPolicy()
        with self.assertRaises(ProjectAccessDenied):
            policy.authorize(None, "orbit", READ)

    def test_unknown_identity_is_denied_and_known_identity_has_scoped_actions(self) -> None:
        member = AuthenticatedPrincipal("https://identity.example", "member-1")
        stranger = AuthenticatedPrincipal("https://identity.example", "unknown")
        policy = ProjectAccessPolicy(
            principal_access={member: {"project-a": [READ]}}
        )

        authorized = policy.authorize(member, "project-a", READ)
        self.assertEqual(authorized.project, "project-a")
        self.assertEqual(authorized.action, READ)
        with self.assertRaises(ProjectAccessDenied):
            policy.authorize(stranger, "project-a", READ)
        with self.assertRaises(ProjectAccessDenied):
            policy.authorize(member, "project-a", WRITE)
        with self.assertRaises(ProjectAccessDenied):
            policy.authorize(member, "project-b", READ)

    def test_access_map_rejects_invalid_actions(self) -> None:
        with self.assertRaises(ValueError):
            ProjectAccessPolicy(local_access={"project-a": ["admin"]})

    def test_denials_cover_all_tools_and_direct_server_adapter_calls(self) -> None:
        # The configured process may read project-a but cannot write it and has
        # no permission at all for project-b. No backend method may run.
        policy = ProjectAccessPolicy(local_access={"project-a": [READ]})
        persistence = Mock()
        retrieval = Mock()
        runtime = server.build_runtime(
            project_authorizer=policy,
            persistence_backend=persistence,
            retrieval_backend=retrieval,
        )
        handlers = server.MCPToolHandlers(runtime)
        denied_calls = (
            lambda: handlers.ingest_chat_message("project-a", "conv", "user", "synthetic"),
            lambda: handlers.save_decision("project-a", "k", "v", "1"),
            lambda: handlers.search_chat_history("project-b", "query"),
            lambda: handlers.retrieve_context("project-b", "query", 100),
            # Direct calls through the runtime's server-facing adapters use the
            # same authorization wrappers as the registered tool handlers.
            lambda: runtime.persistence.ingest_chat_message(
                "project-b", "conv", "user", "synthetic"
            ),
            lambda: runtime.retrieval.search_chat_history("project-b", "query", 10),
        )
        for call in denied_calls:
            with self.subTest(call=call), self.assertRaises(ToolError) as raised:
                call()
            self.assertEqual(error_code(raised.exception), "ACCESS_DENIED")

        persistence.ingest_chat_message.assert_not_called()
        persistence.save_decision.assert_not_called()
        retrieval.search_chat_history.assert_not_called()
        retrieval.retrieve_context.assert_not_called()

    def test_altered_project_argument_is_denied_before_read(self) -> None:
        policy = ProjectAccessPolicy(local_access={"project-a": [READ, WRITE]})
        retrieval = Mock()
        handlers = server.MCPToolHandlers(server.build_runtime(
            project_authorizer=policy,
            retrieval_backend=retrieval,
        ))
        with self.assertRaises(ToolError) as raised:
            handlers.search_chat_history("project-b", "private-term")
        self.assertEqual(error_code(raised.exception), "ACCESS_DENIED")
        retrieval.search_chat_history.assert_not_called()


if __name__ == "__main__":
    unittest.main()
