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
from orbit.storage.protocol import (
    MemoryStore,
    SourceMessageNotFoundError,
    StoredDecision,
    StoredMessage,
)
from orbit.storage.sqlite import SQLiteMemoryStore


class MCPRepositoryIntegrationTests(unittest.TestCase):
    def test_sqlite_store_inserts_message_with_opaque_id_and_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "memory.db"
            message = SQLiteMemoryStore(db_path).insert_message(
                "project-a", "conversation-1", "user", "Synthetic text"
            )

            self.assertIsInstance(message, StoredMessage)
            self.assertTrue(message.message_id)
            self.assertEqual(message.project, "project-a")
            self.assertEqual(message.conversation_id, "conversation-1")
            self.assertEqual(message.role, "user")
            self.assertEqual(message.content, "Synthetic text")
            self.assertTrue(message.created_at.endswith("Z"))
            with closing(connect(db_path)) as conn:
                row = conn.execute(
                    "SELECT content FROM chat_messages WHERE id = ? AND project = ?",
                    (int(message.message_id), "project-a"),
                ).fetchone()
            self.assertEqual(row["content"], "Synthetic text")

    def test_sqlite_store_saves_upserts_and_updates_decisions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "memory.db"
            store = SQLiteMemoryStore(db_path)
            message = store.insert_message(
                "project-a", "conversation-1", "user", "Decision source"
            )

            created = store.save_decision(
                "project-a", "storage", "Use SQLite.", message.message_id
            )
            updated = store.save_decision(
                "project-a", "storage", "Keep SQLite locally.", message.message_id
            )
            approved = store.update_decision_status(
                "project-a", "storage", "approved"
            )

            self.assertIsInstance(created, StoredDecision)
            self.assertEqual(created.status, "draft")
            self.assertEqual(created.source_message_id, message.message_id)
            self.assertEqual(created.created_at, created.updated_at)
            self.assertEqual(updated.decision_id, created.decision_id)
            self.assertEqual(updated.value, "Keep SQLite locally.")
            self.assertEqual(approved.decision_id, created.decision_id)
            self.assertEqual(approved.status, "approved")
            with closing(connect(db_path)) as conn:
                stored = DecisionRepository(conn).get("project-a", "storage")
            self.assertEqual(stored["status"], "approved")
            self.assertEqual(stored["source_message_id"], int(message.message_id))

    def test_sqlite_store_rejects_missing_and_cross_project_source_messages(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SQLiteMemoryStore(Path(temp_dir) / "memory.db")
            message = store.insert_message(
                "project-a", "conversation-1", "user", "Synthetic source"
            )
            for project, source_id in (
                ("project-b", message.message_id),
                ("project-a", "999999"),
                ("project-a", "not-an-id"),
            ):
                with self.subTest(project=project, source_id=source_id):
                    with self.assertRaises(SourceMessageNotFoundError):
                        store.save_decision(project, "key", "value", source_id)

    def test_persistence_service_depends_on_store_protocol_and_maps_dto(self) -> None:
        class FakeStore:
            def insert_message(self, project, conversation_id, role, content):
                return StoredMessage("opaque-message-id", project, conversation_id, role, content,
                                     "2026-10-14T12:00:00.000000Z")

            def save_decision(self, project, key, value, source_message_id, status="draft"):
                return StoredDecision("opaque-decision-id", project, key, value, status,
                                      source_message_id, "2026-10-14T12:00:00Z",
                                      "2026-10-14T12:00:00Z")

            def update_decision_status(self, project, key, status):
                return StoredDecision("opaque-decision-id", project, key, "value", status,
                                      "opaque-message-id", "2026-10-14T12:00:00Z",
                                      "2026-10-14T12:00:00Z")

        store: MemoryStore = FakeStore()
        service = PersistenceServices(store=store)
        message = service.ingest_chat_message("project-a", "conv", "user", "synthetic")
        decision = service.save_decision(
            "project-a", "key", "value", message["message_id"]
        )
        self.assertEqual(message["message_id"], "opaque-message-id")
        self.assertEqual(message["created_at"], "2026-10-14T12:00:00.000000Z")
        self.assertEqual(decision["decision_id"], "opaque-decision-id")
        self.assertEqual(decision["status"], "draft")
        self.assertEqual(decision["source_message_id"], "opaque-message-id")

    def test_semantic_index_failure_does_not_fail_committed_message(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "memory.db"
            with patch("orbit.storage.sqlite.index_after_write", side_effect=RuntimeError("index unavailable")):
                message = SQLiteMemoryStore(db_path).insert_message(
                    "project-a", "conversation-1", "user", "Synthetic text"
                )
            with closing(connect(db_path)) as conn:
                row = conn.execute(
                    "SELECT content FROM chat_messages WHERE id = ?",
                    (int(message.message_id),),
                ).fetchone()
            self.assertEqual(row["content"], "Synthetic text")

    def test_message_and_decision_persist_across_adapter_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "memory.db"
            first_adapter = PersistenceServices(db_path)
            handlers = server.MCPToolHandlers(server.build_runtime(
                project_authorizer=ProjectAccessPolicy(local_access={"project-a": ["read", "write"]}),
                persistence_backend=first_adapter,
            ))
            message = handlers.ingest_chat_message(
                "project-a", "conversation-1", "user", "Synthetic test message"
            )
            decision = handlers.save_decision(
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
            handlers = server.MCPToolHandlers(server.build_runtime(
                persistence_backend=adapter,
                project_authorizer=ProjectAccessPolicy(local_access={
                    "project-a": ["write"],
                    "project-b": ["write"],
                }),
            ))
            with self.assertRaises(ToolError) as raised:
                handlers.save_decision(
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
            handlers = server.MCPToolHandlers(server.build_runtime(
                persistence_backend=adapter,
                project_authorizer=ProjectAccessPolicy(local_access={"project-a": ["write"]}),
            ))
            with self.assertRaises(ToolError) as raised:
                handlers.ingest_chat_message("project-a", "conv", "user", "synthetic")

            payload = str(raised.exception)
            error = json.loads(payload)["error"]
            self.assertEqual(error["code"], "STORAGE_ERROR")
            self.assertNotIn(temp_dir, payload)


if __name__ == "__main__":
    unittest.main()
