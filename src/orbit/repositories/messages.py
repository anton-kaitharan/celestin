"""Chat message repository: raw conversation history persistence."""

from __future__ import annotations

import sqlite3

from orbit.repositories._validation import (
    NotFoundError,
    ValidationError,
    validate_conversation_id,
    validate_project,
    validate_role,
    utc_now_iso,
)


class ChatMessageRepository:
    """CRUD for the ``chat_messages`` table."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    def insert(
        self,
        project: str,
        conversation_id: str,
        role: str,
        content: str,
        created_at: str | None = None,
    ) -> int:
        """Persist one message and return its generated ID."""
        project = validate_project(project)
        conversation_id = validate_conversation_id(conversation_id)
        role = validate_role(role)
        if not isinstance(content, str):
            raise ValidationError("content must be a string")
        created_at = created_at if created_at is not None else utc_now_iso()

        cur = self._conn.execute(
            """
            INSERT INTO chat_messages (project, conversation_id, role, content, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (project, conversation_id, role, content, created_at),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get(self, message_id: int) -> dict:
        """Return one message as a dict, or raise ``NotFoundError``."""
        row = self._conn.execute(
            "SELECT * FROM chat_messages WHERE id = ?", (message_id,)
        ).fetchone()
        if row is None:
            raise NotFoundError(f"chat message {message_id} not found")
        return dict(row)

    def get_for_project(self, project: str, message_id: int) -> dict:
        """Return one message only when its ID belongs to ``project``."""
        project = validate_project(project)
        if isinstance(message_id, bool) or not isinstance(message_id, int) or message_id < 1:
            raise ValidationError("message_id must be a positive integer")
        row = self._conn.execute(
            "SELECT * FROM chat_messages WHERE project = ? AND id = ?",
            (project, message_id),
        ).fetchone()
        if row is None:
            raise NotFoundError(f"chat message {message_id} not found")
        return dict(row)

    def list_for_conversation(
        self,
        project: str,
        conversation_id: str,
        limit: int | None = None,
    ) -> list[dict]:
        """List a conversation's messages in chronological order.

        Always scoped to ``project``; returns ``[]`` for unknown projects.
        """
        project = validate_project(project)
        conversation_id = validate_conversation_id(conversation_id)
        sql = """
            SELECT * FROM chat_messages
            WHERE project = ? AND conversation_id = ?
            ORDER BY id ASC
        """
        params: list[object] = [project, conversation_id]
        if limit is not None:
            if not isinstance(limit, int) or limit <= 0:
                raise ValidationError("limit must be a positive integer")
            sql += " LIMIT ?"
            params.append(limit)
        rows = self._conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def list_for_project(self, project: str) -> list[dict]:
        """List every message for a project across conversations."""
        project = validate_project(project)
        rows = self._conn.execute(
            "SELECT * FROM chat_messages WHERE project = ? ORDER BY id ASC",
            (project,),
        ).fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    def delete(self, message_id: int) -> None:
        """Delete one message; raises ``NotFoundError`` when absent."""
        cur = self._conn.execute(
            "DELETE FROM chat_messages WHERE id = ?", (message_id,)
        )
        if cur.rowcount == 0:
            raise NotFoundError(f"chat message {message_id} not found")
        self._conn.commit()


def ingest_chat_message(
    project: str,
    conversation_id: str,
    role: str,
    content: str,
    conn: sqlite3.Connection | None = None,
    db_path: str | None = None,
) -> int:
    """Persist a message and return its ID (team contract signature).

    Uses the default database when no ``conn`` is supplied.
    """
    if conn is None:
        from orbit.database.engine import connect

        conn = connect(db_path)
    return ChatMessageRepository(conn).insert(
        project, conversation_id, role, content
    )
