from __future__ import annotations

import asyncio
import os
import shutil
import sys
import unittest
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class StdioProtocolTests(unittest.TestCase):
    def test_startup_tool_discovery_ping_and_safe_validation(self) -> None:
        async def exercise_protocol() -> None:
            # Exercise the installed entry point when available, with a module
            # invocation fallback for environments that have not installed it.
            executable = shutil.which("orbit")
            if executable:
                command, args = executable, []
            else:
                command, args = sys.executable, ["-m", "orbit.mcp_server.server"]

            env = os.environ.copy()
            env.pop("ORBIT_LOCAL_PROJECT_ACCESS", None)
            parameters = StdioServerParameters(
                command=command,
                args=args,
                cwd=REPOSITORY_ROOT,
                env=env,
            )
            async with stdio_client(parameters) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    initialized = await session.initialize()
                    self.assertEqual(initialized.serverInfo.name, "Orbit")
                    listed = await session.list_tools()
                    names = {tool.name for tool in listed.tools}
                    self.assertTrue(
                        {
                            "ingest_chat_message",
                            "save_decision",
                            "search_chat_history",
                            "retrieve_context",
                            "ping",
                        }.issubset(names)
                    )

                    ping_result = await session.call_tool("ping", {})
                    self.assertFalse(ping_result.isError)
                    self.assertEqual(
                        [item.text for item in ping_result.content if hasattr(item, "text")],
                        ["Orbit MCP server is running."],
                    )

                    # Invalid role fails at the MCP tool boundary before any
                    # database operation is attempted.
                    bad_input = await session.call_tool(
                        "ingest_chat_message",
                        {
                            "project": "protocol-test",
                            "conversation_id": "conversation-1",
                            "role": "not-a-role",
                            "content": "private sample text",
                        },
                    )
                    self.assertTrue(bad_input.isError)
                    error_text = " ".join(
                        item.text for item in bad_input.content if hasattr(item, "text")
                    )
                    self.assertIn("INVALID_ARGUMENT", error_text)
                    self.assertNotIn("private sample text", error_text)

                    unauthorized = await session.call_tool(
                        "search_chat_history",
                        {"project": "unconfigured-project", "query": "private term"},
                    )
                    self.assertTrue(unauthorized.isError)
                    denial_text = " ".join(
                        item.text for item in unauthorized.content if hasattr(item, "text")
                    )
                    self.assertIn("ACCESS_DENIED", denial_text)
                    self.assertNotIn("unconfigured-project", denial_text)
                    self.assertNotIn("private term", denial_text)

            legacy_executable = shutil.which("memory-bridge")
            self.assertIsNotNone(legacy_executable)
            async with stdio_client(
                StdioServerParameters(
                    command=legacy_executable,
                    cwd=REPOSITORY_ROOT,
                )
            ) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    initialized = await session.initialize()
                    self.assertEqual(initialized.serverInfo.name, "Orbit")
                    ping_result = await session.call_tool("ping", {})
                    self.assertFalse(ping_result.isError)

        asyncio.run(exercise_protocol())


if __name__ == "__main__":
    unittest.main()
