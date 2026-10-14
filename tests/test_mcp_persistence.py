from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from mcp.server.fastmcp.exceptions import ToolError

from orbit.database.engine import connect
from orbit.mcp_server import server
from orbit.mcp_server.authorization import ProjectAccessPolicy
from orbit.mcp_server.persistence_services import PersistenceServices
from orbit.repositories import DecisionRepository


class MCPRepositoryIntegrationTests(unittest.TestCase):
    def test_message_and_decision_persist_across_adapter_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "memory.db"
            first_adapter = PersistenceServices(db_path)
            with patch.object(server, "_persistence_backend", first_adapter), patch.object(
                server, "project_authorizer",
                ProjectAccessPolicy(local_access={"project-a": ["read", "write"]}),
            ):
                message = server.ingest_chat_message(
                    "project-a", "conversation-1", "user", "Synthetic test message"
                )
                decision = server.save_decision(
                    "project-a", "test-policy", "Keep the policy as a draft.",
                    message["message_id"],
                )

            # A fresh adapter instance simulates a server restart; saving the
            # same project/key must update the canonical row in place.
            updated_decision = PersistenceServices(db_path).save_decision(
                "project-a", "test-policy", "Updated synthetic policy.",
                message["message_id"],
            )
            with closing(connect(db_path)) as conn:
                stored_message = conn.execute(
                    "SELECT * FROM chat_messages WHERE id = ? AND project = ?",
                    (int(message["message_id"]), "project-a"),
                ).fetchone()
                stored_decision = DecisionRepository(conn).get("project-a", "test-policy")

            self.assertIsNotNone(stored_message)
            self.assertEqual(stored_message["content"], "Synthetic test message")
            self.assertEqual(decision["status"], "draft")
            self.assertEqual(decision["source_message_id"], message["message_id"])
            self.assertEqual(updated_decision["decision_id"], decision["decision_id"])
            self.assertEqual(updated_decision["status"], "draft")
            self.assertEqual(stored_decision["source_message_id"], int(message["message_id"]))
            self.assertEqual(stored_decision["value"], "Updated synthetic policy.")

    def test_source_message_cannot_cross_project_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = PersistenceServices(Path(temp_dir) / "memory.db")
            message = adapter.ingest_chat_message(
                "project-a", "conversation-1", "user", "Private synthetic text"
            )
            with patch.object(server, "_persistence_backend", adapter), patch.object(
                server, "project_authorizer",
                ProjectAccessPolicy(local_access={"project-a": ["write"], "project-b": ["write"]}),
            ):
                with self.assertRaises(ToolError) as raised:
                    server.save_decision(
                        "project-b", "test-policy", "must not be stored",
                        message["message_id"],
                    )

            error = json.loads(str(raised.exception))["error"]
            self.assertEqual(error["code"], "SOURCE_MESSAGE_NOT_FOUND")
            with closing(connect(Path(temp_dir) / "memory.db")) as conn:
                rows = DecisionRepository(conn).list_for_project("project-b")
            self.assertEqual(rows, [])

    def test_database_failure_has_safe_mcp_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_directory = Path(temp_dir) / "is-a-directory"
            database_directory.mkdir()
            adapter = PersistenceServices(database_directory)
            with patch.object(server, "_persistence_backend", adapter), patch.object(
                server, "project_authorizer",
                ProjectAccessPolicy(local_access={"project-a": ["write"]}),
            ):
                with self.assertRaises(ToolError) as raised:
                    server.ingest_chat_message("project-a", "conv", "user", "synthetic")

            payload = str(raised.exception)
            error = json.loads(payload)["error"]
            self.assertEqual(error["code"], "STORAGE_ERROR")
            self.assertNotIn(temp_dir, payload)


if __name__ == "__main__":
    unittest.main()
