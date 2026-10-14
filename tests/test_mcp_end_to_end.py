from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import asynccontextmanager, closing
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from orbit.database.engine import connect
from orbit.repositories.decisions import DecisionRepository


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _tool_payload(result):
    if result.isError:
        text = " ".join(item.text for item in result.content if hasattr(item, "text"))
        raise AssertionError(f"MCP tool returned an error: {text}")
    if result.structuredContent is not None:
        return result.structuredContent
    text = "\n".join(item.text for item in result.content if hasattr(item, "text"))
    return json.loads(text)


class EndToEndStdioIntegrationTests(unittest.TestCase):
    def test_memory_workflow_across_independent_stdio_sessions(self) -> None:
        async def exercise(database_path: Path) -> None:
            executable = shutil.which("orbit")
            if executable:
                command, args = executable, []
            else:
                command, args = sys.executable, ["-m", "orbit.mcp_server.server"]

            env = os.environ.copy()
            env["MEMORY_BRIDGE_DB_PATH"] = str(database_path)
            env["ORBIT_LOCAL_PROJECT_ACCESS"] = json.dumps({
                "orbit-e2e-alpha": ["read", "write"],
                "orbit-e2e-beta": ["read", "write"],
            })

            @asynccontextmanager
            async def connected_session():
                parameters = StdioServerParameters(
                    command=command,
                    args=args,
                    cwd=REPOSITORY_ROOT,
                    env=env,
                )
                async with stdio_client(parameters) as streams:
                    async with ClientSession(*streams) as session:
                        await session.initialize()
                        yield session

            async with connected_session() as session_one:
                ping = await session_one.call_tool("ping", {})
                self.assertFalse(ping.isError)

                message = _tool_payload(await session_one.call_tool(
                    "ingest_chat_message",
                    {
                        "project": "orbit-e2e-alpha",
                        "conversation_id": "synthetic-conversation",
                        "role": "user",
                        "content": "Synthetic policy says use SQLite for local storage.",
                    },
                ))
                self.assertTrue(message["message_id"])
                self.assertTrue(message["created_at"].endswith("Z"))

                decision = _tool_payload(await session_one.call_tool(
                    "save_decision",
                    {
                        "project": "orbit-e2e-alpha",
                        "key": "storage-policy",
                        "value": "Use SQLite for local storage.",
                        "source_message_id": message["message_id"],
                    },
                ))
                self.assertEqual(decision["status"], "draft")
                self.assertEqual(decision["source_message_id"], message["message_id"])

                deprecated = _tool_payload(await session_one.call_tool(
                    "save_decision",
                    {
                        "project": "orbit-e2e-alpha",
                        "key": "retired-policy",
                        "value": "Deprecated synthetic SQLite policy.",
                        "source_message_id": message["message_id"],
                    },
                ))
                self.assertEqual(deprecated["status"], "draft")

                # Approval/deprecation are repository lifecycle transitions,
                # outside the public four-tool MCP API.
                with closing(connect(database_path)) as connection:
                    repository = DecisionRepository(connection)
                    repository.update_status("orbit-e2e-alpha", "storage-policy", "approved")
                    repository.update_status("orbit-e2e-alpha", "retired-policy", "deprecated")

                other_project_message = _tool_payload(await session_one.call_tool(
                    "ingest_chat_message",
                    {
                        "project": "orbit-e2e-beta",
                        "conversation_id": "other-conversation",
                        "role": "assistant",
                        "content": "Synthetic unrelated SQLite record.",
                    },
                ))
                self.assertNotEqual(message["message_id"], other_project_message["message_id"])

            # A second OS process and MCP ClientSession read the same DB after
            # the first server process has exited (restart/persistence check).
            async with connected_session() as session_two:
                search = _tool_payload(await session_two.call_tool(
                    "search_chat_history",
                    {"project": "orbit-e2e-alpha", "query": "SQLite", "limit": 10},
                ))
                self.assertEqual(search["project"], "orbit-e2e-alpha")
                self.assertEqual(len(search["results"]), 1)
                self.assertEqual(search["results"][0]["message_id"], message["message_id"])
                self.assertEqual(search["results"][0]["source_message_id"], message["message_id"])
                self.assertEqual(search["results"][0]["created_at"], message["created_at"])

                context = _tool_payload(await session_two.call_tool(
                    "retrieve_context",
                    {"project": "orbit-e2e-alpha", "query": "SQLite", "token_budget": 20_000},
                ))
                self.assertLessEqual(context["token_count"], context["token_budget"])
                self.assertEqual(context["token_budget"], 20_000)
                self.assertEqual(context["token_count_is_estimate"], True)
                self.assertEqual(context["token_counter"], "utf8-byte-upper-bound")
                self.assertTrue(context["items"])
                self.assertEqual(context["items"][0]["record_kind"], "canonical_decision")
                self.assertEqual(context["items"][0]["status"], "approved")
                self.assertEqual(context["items"][0]["source_message_id"], message["message_id"])
                self.assertTrue(context["items"][0]["created_at"])
                self.assertNotIn("retired-policy", context["rendered_context"])
                self.assertNotIn("orbit-e2e-beta", context["rendered_context"])

                small_budget = _tool_payload(await session_two.call_tool(
                    "retrieve_context",
                    {"project": "orbit-e2e-alpha", "query": "SQLite", "token_budget": 1},
                ))
                self.assertEqual(small_budget["items"], [])
                self.assertEqual(small_budget["token_count"], 0)
                self.assertTrue(any(row["reason"] == "token_budget" for row in small_budget["excluded"]))

                beta_search = _tool_payload(await session_two.call_tool(
                    "search_chat_history",
                    {"project": "orbit-e2e-alpha", "query": "unrelated", "limit": 10},
                ))
                self.assertEqual(beta_search["results"], [])
                beta_own_search = _tool_payload(await session_two.call_tool(
                    "search_chat_history",
                    {"project": "orbit-e2e-beta", "query": "unrelated", "limit": 10},
                ))
                self.assertEqual(len(beta_own_search["results"]), 1)
                self.assertEqual(beta_own_search["results"][0]["project"], "orbit-e2e-beta")

                bad_role = await session_two.call_tool(
                    "ingest_chat_message",
                    {"project": "orbit-e2e-alpha", "conversation_id": "x", "role": "tool", "content": "synthetic"},
                )
                self.assertTrue(bad_role.isError)
                self.assertIn("INVALID_ARGUMENT", " ".join(item.text for item in bad_role.content if hasattr(item, "text")))

                bad_budget = await session_two.call_tool(
                    "retrieve_context",
                    {"project": "orbit-e2e-alpha", "query": "SQLite", "token_budget": 20_001},
                )
                self.assertTrue(bad_budget.isError)
                self.assertIn("INVALID_ARGUMENT", " ".join(item.text for item in bad_budget.content if hasattr(item, "text")))

                cross_project_source = await session_two.call_tool(
                    "save_decision",
                    {
                        "project": "orbit-e2e-alpha",
                        "key": "invalid-cross-source",
                        "value": "Must not save.",
                        "source_message_id": other_project_message["message_id"],
                    },
                )
                self.assertTrue(cross_project_source.isError)
                error_text = " ".join(item.text for item in cross_project_source.content if hasattr(item, "text"))
                self.assertIn("SOURCE_MESSAGE_NOT_FOUND", error_text, msg=repr(cross_project_source))
                with closing(connect(database_path)) as connection:
                    self.assertFalse(
                        any(
                            row["key"] == "invalid-cross-source"
                            for row in DecisionRepository(connection).list_for_project(
                                "orbit-e2e-alpha"
                            )
                        )
                    )

        with tempfile.TemporaryDirectory(prefix="orbit-mcp-e2e-") as temp_dir:
            asyncio.run(exercise(Path(temp_dir) / "disposable.sqlite"))


if __name__ == "__main__":
    unittest.main()
