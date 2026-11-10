"""Chat message repository tests."""

from __future__ import annotations

import pytest

from orbit.database.engine import connect
from orbit.repositories._validation import NotFoundError, ValidationError
from orbit.repositories.messages import (
    ChatMessageRepository,
    ingest_chat_message,
)


def test_insert_returns_id_and_persists(messages, db_conn):
    msg_id = messages.insert(
        project="proj-a",
        conversation_id="conv-1",
        role="user",
        content="hello",
    )
    assert isinstance(msg_id, int)
    row = db_conn.execute(
        "SELECT project, conversation_id, role, content, created_at"
        " FROM chat_messages WHERE id = ?",
        (msg_id,),
    ).fetchone()
    assert row["project"] == "proj-a"
    assert row["conversation_id"] == "conv-1"
    assert row["role"] == "user"
    assert row["content"] == "hello"
    assert row["created_at"]  # timestamp preserved


def test_insert_minimal_contract_function(db_conn):
    msg_id = ingest_chat_message("proj-a", "conv-1", "user", "hi", conn=db_conn)
    assert isinstance(msg_id, int)
    assert db_conn.execute(
        "SELECT COUNT(*) FROM chat_messages"
    ).fetchone()[0] == 1


def test_get_roundtrip(messages, sample_message_id):
    msg = messages.get(sample_message_id)
    assert msg["id"] == sample_message_id
    assert msg["role"] == "user"
    assert msg["project"] == "proj-a"


def test_get_missing_raises(messages):
    with pytest.raises(NotFoundError):
        messages.get(999_999)


def test_get_for_project_filters_by_id_and_project(messages, sample_message_id):
    other_project_id = messages.insert("proj-b", "conv-1", "user", "private")

    assert messages.get_for_project("proj-a", sample_message_id)["id"] == sample_message_id
    with pytest.raises(NotFoundError):
        messages.get_for_project("proj-a", other_project_id)
    with pytest.raises(NotFoundError):
        messages.get_for_project("proj-a", 999_999)


@pytest.mark.parametrize("message_id", [0, -1, True, "1", None])
def test_get_for_project_rejects_invalid_ids(messages, message_id):
    with pytest.raises(ValidationError):
        messages.get_for_project("proj-a", message_id)


def test_list_conversation_chronological_order(messages):
    ids = [
        messages.insert("proj-a", "conv-1", role, content)
        for role, content in [
            ("user", "first"),
            ("assistant", "second"),
            ("user", "third"),
        ]
    ]
    got = [m["id"] for m in messages.list_for_conversation("proj-a", "conv-1")]
    assert got == ids


def test_limit_is_applied(messages):
    for i in range(5):
        messages.insert("proj-a", "conv-1", "user", f"m{i}")
    got = messages.list_for_conversation("proj-a", "conv-1", limit=2)
    assert len(got) == 2
    assert got[0]["content"] == "m0"
    assert got[1]["content"] == "m1"


def test_project_isolation_messages(messages):
    messages.insert("proj-a", "conv-1", "user", "from A")
    messages.insert("proj-b", "conv-1", "user", "from B")

    a = messages.list_for_conversation("proj-a", "conv-1")
    b = messages.list_for_conversation("proj-b", "conv-1")
    assert [m["content"] for m in a] == ["from A"]
    assert [m["content"] for m in b] == ["from B"]

    proj_a_all = messages.list_for_project("proj-a")
    assert [m["content"] for m in proj_a_all] == ["from A"]

    # Unknown conversation inside known project returns nothing
    assert messages.list_for_conversation("proj-a", "no-such") == []


def test_validation_errors(messages):
    with pytest.raises(ValidationError):
        messages.insert("", "conv-1", "user", "x")
    with pytest.raises(ValidationError):
        messages.insert("proj-a", "  ", "user", "x")
    with pytest.raises(ValidationError):
        messages.insert("proj-a", "conv-1", "root", "x")  # bad role
    with pytest.raises(ValidationError):
        messages.insert("proj-a", "conv-1", "user", 123)


def test_delete_removes_and_missing_raises(messages, sample_message_id):
    messages.delete(sample_message_id)
    with pytest.raises(NotFoundError):
        messages.get(sample_message_id)
    with pytest.raises(NotFoundError):
        messages.delete(sample_message_id)


def test_every_connection_initializes_schema(tmp_path):
    """A clean checkout auto-initializes on first connect."""
    conn = connect(tmp_path / "brand-new.db")
    assert ChatMessageRepository(conn).list_for_project("any") == []
