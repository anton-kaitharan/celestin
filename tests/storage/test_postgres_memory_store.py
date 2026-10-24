"""Contract tests for the PostgreSQL store without a live database or driver."""

from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import datetime, timezone

import pytest

from orbit.storage.postgres import PostgresMemoryStore
from orbit.storage.protocol import (
    DecisionNotFoundError,
    ProjectNotFoundError,
    SourceMessageNotFoundError,
)


WORKSPACE_ID = "a0000000-0000-4000-8000-000000000001"
PROJECT_ID = "a0000000-0000-4000-8000-000000000002"
NOW = datetime(2026, 10, 14, 12, 30, tzinfo=timezone.utc)


class Result:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row

    def close(self):
        pass


class ScriptedConnection(AbstractContextManager):
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []
        self.exit_error = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.exit_error = exc_type
        return False

    def execute(self, query, params=()):
        self.calls.append((query, params))
        if "set_config('celestin.workspace_id'" in query:
            return Result((params[0], params[1]))
        return Result(next(self.responses))


def make_store(connection):
    return PostgresMemoryStore(lambda: connection, WORKSPACE_ID)


def test_insert_message_resolves_project_inside_workspace_and_returns_utc_id():
    connection = ScriptedConnection(
        [(PROJECT_ID,), (73, NOW)]
    )

    message = make_store(connection).insert_message(
        "legacy-project", "conversation-a", "user", "synthetic content"
    )

    assert message.message_id == "73"
    assert message.project == "legacy-project"
    assert message.created_at == "2026-10-14T12:30:00.000000Z"
    lookup, scope, insert = connection.calls
    assert "workspace_id = %s::uuid" in lookup[0]
    assert "legacy_project_key = %s" in lookup[0]
    assert lookup[1] == (WORKSPACE_ID, "legacy-project")
    assert "set_config('celestin.workspace_id'" in scope[0]
    assert scope[1] == (WORKSPACE_ID, PROJECT_ID)
    assert "INSERT INTO chat_messages" in insert[0]
    assert "workspace_id" in insert[0] and "project_id" in insert[0]
    assert insert[1][:5] == (WORKSPACE_ID, PROJECT_ID, "conversation-a", "user", "synthetic content")
    assert connection.exit_error is None


def test_save_decision_upserts_with_same_project_source_and_preserves_created_time():
    connection = ScriptedConnection(
        [
            (PROJECT_ID,),
            (1,),
            (91, "draft", 73, NOW, NOW, "storage", "Use PostgreSQL when hosted."),
        ]
    )

    decision = make_store(connection).save_decision(
        "legacy-project", "storage", "Use PostgreSQL when hosted.", "73"
    )

    assert decision.decision_id == "91"
    assert decision.status == "draft"
    assert decision.source_message_id == "73"
    assert decision.created_at == decision.updated_at == "2026-10-14T12:30:00.000000Z"
    scope, source_lookup, upsert = connection.calls[1:]
    assert "set_config('celestin.workspace_id'" in scope[0]
    assert scope[1] == (WORKSPACE_ID, PROJECT_ID)
    assert "workspace_id = %s::uuid AND project_id = %s::uuid AND id = %s" in source_lookup[0]
    assert source_lookup[1] == (WORKSPACE_ID, PROJECT_ID, 73)
    assert "ON CONFLICT (project_id, key) DO UPDATE" in upsert[0]
    assert "created_at = EXCLUDED.created_at" not in upsert[0]
    assert upsert[1][0:6] == (
        WORKSPACE_ID,
        PROJECT_ID,
        "storage",
        "Use PostgreSQL when hosted.",
        "draft",
        73,
    )


def test_missing_project_and_source_fail_before_write():
    missing_project = ScriptedConnection([None])
    with pytest.raises(ProjectNotFoundError):
        make_store(missing_project).insert_message(
            "unprovisioned", "conversation", "user", "synthetic"
        )
    assert len(missing_project.calls) == 1
    assert missing_project.exit_error is ProjectNotFoundError

    missing_source = ScriptedConnection([(PROJECT_ID,), None])
    with pytest.raises(SourceMessageNotFoundError):
        make_store(missing_source).save_decision(
            "legacy-project", "key", "value", "99"
        )
    assert len(missing_source.calls) == 3
    assert "INSERT INTO project_memory" not in missing_source.calls[-1][0]
    assert missing_source.exit_error is SourceMessageNotFoundError


def test_source_ids_are_canonical_positive_decimal_strings():
    for source_id in ("0", "-1", "01", "not-an-id"):
        connection = ScriptedConnection([])
        with pytest.raises(SourceMessageNotFoundError):
            make_store(connection).save_decision(
                "legacy-project", "key", "value", source_id
            )
        assert connection.calls == []


def test_update_status_is_project_scoped_and_reports_missing_decision():
    connection = ScriptedConnection([(PROJECT_ID,), None])

    with pytest.raises(DecisionNotFoundError):
        make_store(connection).update_decision_status(
            "legacy-project", "unknown-key", "approved"
        )

    scope, update = connection.calls[1:]
    assert "set_config('celestin.workspace_id'" in scope[0]
    assert "WHERE workspace_id = %s::uuid AND project_id = %s::uuid AND key = %s" in update[0]
    assert update[1][2:] == (WORKSPACE_ID, PROJECT_ID, "unknown-key")


def test_adapter_rejects_invalid_workspace_uuid():
    with pytest.raises(ValueError, match="workspace_id must be a UUID"):
        PostgresMemoryStore(lambda: None, "not-a-uuid")
