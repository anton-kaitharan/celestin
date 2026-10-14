from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from mcp.server.fastmcp.exceptions import ToolError

from orbit.mcp_server import server
from orbit.mcp_server.persistence_services import PersistenceServices
from orbit.mcp_server.retrieval_services import RetrievalServices
from orbit.mcp_server.authorization import ProjectAccessPolicy
from orbit.database.engine import connect
from orbit.retrieval import SearchIndexUnavailable
from orbit.repositories.decisions import DecisionRepository
from orbit.repositories._validation import ValidationError
from orbit.mcp_server.server import (
    MAX_TOKEN_BUDGET,
    ingest_chat_message,
    mcp,
    retrieve_context,
    save_decision,
    search_chat_history,
)


class ToolContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        adapter = PersistenceServices(Path(self._temp_dir.name) / "memory.db")
        self._service_patch = patch.object(server, "_persistence_backend", adapter)
        self._service_patch.start()
        self.db_path = Path(self._temp_dir.name) / "memory.db"
        self._retrieval_patch = patch.object(server, "_retrieval_backend", RetrievalServices(self.db_path))
        self._retrieval_patch.start()
        self._authorization_patch = patch.object(
            server,
            "project_authorizer",
            ProjectAccessPolicy(local_access={
                "alpha": ["read", "write"],
                "beta": ["read", "write"],
                "missing": ["read", "write"],
                "p": ["read", "write"],
                "demo": ["read", "write"],
            }),
        )
        self._authorization_patch.start()

    def tearDown(self) -> None:
        self._service_patch.stop()
        self._retrieval_patch.stop()
        self._authorization_patch.stop()
        self._temp_dir.cleanup()

    def test_all_public_tools_are_registered_once(self) -> None:
        tools = mcp._tool_manager.list_tools()
        names = [tool.name for tool in tools]
        for expected in (
            "ingest_chat_message",
            "save_decision",
            "search_chat_history",
            "retrieve_context",
            "ping",
        ):
            self.assertEqual(names.count(expected), 1)
        schemas = {tool.name: tool.parameters for tool in tools}
        self.assertEqual(set(schemas["ingest_chat_message"]["properties"]), {"project", "conversation_id", "role", "content"})
        self.assertEqual(set(schemas["save_decision"]["properties"]), {"project", "key", "value", "source_message_id"})
        self.assertEqual(set(schemas["search_chat_history"]["properties"]), {"project", "query", "limit"})
        self.assertEqual(set(schemas["retrieve_context"]["properties"]), {"project", "query", "token_budget"})

    def test_fastmcp_registered_tool_is_callable(self) -> None:
        import asyncio

        async def invoke():
            return await mcp._tool_manager.call_tool(
                "ingest_chat_message",
                {"project": "demo", "conversation_id": "conv-1", "role": "user", "content": "hello"},
            )

        result = asyncio.run(invoke())
        self.assertEqual(result["project"], "demo")
        self.assertTrue(result["message_id"].isdigit())

    def test_ingest_shape_and_project_scope(self) -> None:
        result = ingest_chat_message("alpha", "conv-1", "user", "Hello")
        self.assertEqual(set(result), {"project", "message_id", "conversation_id", "role", "content", "created_at"})
        self.assertEqual(result["project"], "alpha")
        self.assertEqual(result["content"], "Hello")
        self.assertTrue(result["message_id"].isdigit())
        self.assertTrue(result["created_at"].endswith("Z"))
        datetime.fromisoformat(result["created_at"].replace("Z", "+00:00"))

    def test_save_decision_is_a_draft(self) -> None:
        message = ingest_chat_message("alpha", "conversation-1", "user", "Use Python")
        result = save_decision("alpha", "runtime", "Python", message["message_id"])
        self.assertEqual(set(result), {"project", "decision_id", "key", "value", "status", "source_message_id", "created_at", "updated_at"})
        self.assertEqual(result["status"], "draft")
        self.assertEqual(result["source_message_id"], message["message_id"])
        self.assertTrue(result["created_at"].endswith("Z"))

    def test_search_shape_and_limit(self) -> None:
        message = ingest_chat_message("alpha", "conv-1", "user", "hello from SQLite")
        result = search_chat_history("alpha", "hello", 1)
        self.assertEqual(set(result), {"project", "query", "results"})
        self.assertLessEqual(len(result["results"]), 1)
        item = result["results"][0]
        self.assertEqual(set(item), {"message_id", "project", "source_message_id", "conversation_id", "role", "content", "created_at", "score"})
        self.assertEqual(item["message_id"], message["message_id"])
        self.assertEqual(item["source_message_id"], message["message_id"])
        self.assertEqual(item["conversation_id"], "conv-1")
        self.assertIsNone(item["role"])
        self.assertEqual(item["created_at"], message["created_at"])

    def test_search_is_project_scoped_and_empty_results_are_successful(self) -> None:
        ingest_chat_message("alpha", "conv-a", "user", "private telescope results")
        ingest_chat_message("beta", "conv-b", "user", "private telescope results")
        self.assertEqual(len(search_chat_history("alpha", "telescope")["results"]), 1)
        self.assertEqual(search_chat_history("missing", "telescope")["results"], [])

    def test_source_message_must_belong_to_requested_project(self) -> None:
        message = ingest_chat_message("alpha", "conv-a", "user", "source text")
        with self.assertRaises(ToolError) as raised:
            save_decision("beta", "key", "value", message["message_id"])
        self.assertEqual(json.loads(str(raised.exception))["error"]["code"], "SOURCE_MESSAGE_NOT_FOUND")

    def test_invalid_decision_status_is_rejected_by_repository(self) -> None:
        with closing(connect(self.db_path)) as connection:
            with self.assertRaises(ValidationError):
                DecisionRepository(connection).update_status("alpha", "key", "published")

    def test_retrieve_context_shape_and_budget(self) -> None:
        message = ingest_chat_message("alpha", "conv-1", "user", "Python runtime decision")
        save_decision("alpha", "runtime", "Python runtime", message["message_id"])
        with closing(connect(self.db_path)) as connection:
            DecisionRepository(connection).update_status("alpha", "runtime", "approved")
        result = retrieve_context("alpha", "Python", MAX_TOKEN_BUDGET)
        self.assertEqual(set(result), {"project", "query", "items", "rendered_context", "token_count", "token_budget", "token_count_is_estimate", "token_counter", "excluded"})
        self.assertLessEqual(result["token_count"], result["token_budget"])
        self.assertTrue(result["token_count_is_estimate"])
        if result["items"]:
            self.assertEqual(set(result["items"][0]), {"record_id", "source_message_id", "created_at", "record_kind", "status", "content", "relevance_score"})
        decision = next(item for item in result["items"] if item["record_kind"] == "canonical_decision")
        self.assertEqual(decision["status"], "approved")
        self.assertEqual(decision["source_message_id"], message["message_id"])
        self.assertEqual(result["token_counter"], "utf8-byte-upper-bound")

    def test_context_excludes_unapproved_memory_and_respects_budget(self) -> None:
        message = ingest_chat_message("alpha", "conv-1", "user", "nebula environment")
        save_decision("alpha", "draft-note", "nebula secret setting", message["message_id"])
        save_decision("alpha", "old-note", "nebula retired configuration", message["message_id"])
        with closing(connect(self.db_path)) as connection:
            DecisionRepository(connection).update_status("alpha", "old-note", "deprecated")
        bundle = retrieve_context("alpha", "nebula", 1)
        self.assertEqual(bundle["items"], [])
        self.assertLessEqual(bundle["token_count"], 1)
        self.assertNotIn("canonical_decision", [item["record_kind"] for item in bundle["items"]])

    def test_missing_fts_index_returns_safe_error(self) -> None:
        with closing(connect(self.db_path)) as connection:
            connection.execute("DROP TABLE chat_messages_fts")
            connection.execute("PRAGMA user_version = 2")
        with self.assertRaises(ToolError) as raised:
            search_chat_history("alpha", "query")
        payload = json.loads(str(raised.exception))
        self.assertEqual(payload["error"]["code"], "RETRIEVAL_UNAVAILABLE")

    def test_tools_delegate_to_service_adapters(self) -> None:
        self.assertIsInstance(server._persistence_backend, PersistenceServices)
        self.assertIsInstance(server._retrieval_backend, RetrievalServices)

    def assert_invalid(self, callback, *args, **kwargs) -> None:
        with self.assertRaises(ToolError) as raised:
            callback(*args, **kwargs)
        payload = json.loads(str(raised.exception))
        self.assertEqual(payload["error"]["code"], "INVALID_ARGUMENT")

    def test_invalid_required_strings_and_role(self) -> None:
        self.assert_invalid(ingest_chat_message, " ", "conv", "user", "text")
        self.assert_invalid(ingest_chat_message, "p", "conv", "function", "text")
        self.assert_invalid(ingest_chat_message, "p", "conv", "user", " \t")
        self.assert_invalid(save_decision, "p", "key", "value", " ")
        self.assert_invalid(search_chat_history, "p", " ")
        self.assert_invalid(retrieve_context, "p", " ", 10)

    def test_invalid_limits_and_token_budgets(self) -> None:
        for limit in (0, 101, True, 1.5):
            self.assert_invalid(search_chat_history, "p", "query", limit)
        for budget in (0, -1, True, 1.5, MAX_TOKEN_BUDGET + 1):
            self.assert_invalid(retrieve_context, "p", "query", budget)

    def test_missing_required_arguments_are_safe(self) -> None:
        import asyncio

        # Omit the required `project` to exercise FastMCP's argument validation.
        async def invoke_missing():
            return await mcp._tool_manager.call_tool(
                "ingest_chat_message",
                {"conversation_id": "conv", "role": "user", "content": "SENSITIVE TEXT"},
            )

        with self.assertRaises(ToolError) as raised:
            asyncio.run(invoke_missing())
        message = str(raised.exception)
        self.assertNotIn("SENSITIVE TEXT", message)
        self.assertNotIn("input_value", message)
        self.assertEqual(json.loads(message)["error"]["code"], "INVALID_ARGUMENT")

    def test_database_and_retrieval_failures_are_sanitized(self) -> None:
        with patch.object(
            server._persistence_backend,
            "ingest_chat_message",
            side_effect=sqlite3.OperationalError("C:/private/db.sqlite credential=secret"),
        ):
            with self.assertLogs(server.logger, level="ERROR") as captured:
                with self.assertRaises(ToolError) as raised:
                    ingest_chat_message("alpha", "conv", "user", "private content")
        message = str(raised.exception)
        self.assertEqual(json.loads(message)["error"]["code"], "STORAGE_ERROR")
        self.assertNotIn("private", message)
        self.assertNotIn("db.sqlite", message)
        self.assertNotIn("secret", message)
        self.assertNotIn("private content", "\n".join(captured.output))

        with patch.object(
            server._retrieval_backend,
            "search_chat_history",
            side_effect=RuntimeError("provider key=secret /private/index.sqlite"),
        ):
            with self.assertLogs(server.logger, level="ERROR") as captured:
                with self.assertRaises(ToolError) as raised:
                    search_chat_history("alpha", "query")
        message = str(raised.exception)
        self.assertEqual(json.loads(message)["error"]["code"], "INTERNAL_ERROR")
        self.assertNotIn("secret", message)
        self.assertNotIn("index.sqlite", message)
        self.assertNotIn("secret", "\n".join(captured.output))

    def test_unavailable_retrieval_dependency_has_distinct_error(self) -> None:
        with patch.object(
            server._retrieval_backend,
            "search_chat_history",
            side_effect=SearchIndexUnavailable("index path /private/index.sqlite"),
        ):
            with self.assertRaises(ToolError) as raised:
                search_chat_history("alpha", "query")
        payload = json.loads(str(raised.exception))
        self.assertEqual(payload["error"]["code"], "RETRIEVAL_UNAVAILABLE")
        self.assertNotIn("private", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
