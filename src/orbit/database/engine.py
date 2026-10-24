"""SQLite engine: schema initialization, connection management, and migrations.

SQLite is the source of truth for raw chat history and canonical project
decisions. This module owns all DDL so the schema is defined in exactly one
place.

Migration strategy: ``PRAGMA user_version`` tracks the applied schema
version. ``SCHEMA_MIGRATIONS`` contains ordered sequences of complete SQL
statements; on connect, each pending migration is applied in its own
transaction. A clean checkout therefore initializes itself on first connect
with no manual steps.

Environment:
    ``CELESTIN_DB_PATH`` overrides the database location. The legacy
    ``MEMORY_BRIDGE_DB_PATH`` is still honored when ``CELESTIN_DB_PATH`` is
    unset, so existing Orbit-era client configurations keep working.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = Path("data/memory_bridge.db")

DB_PATH = Path(
    os.environ.get("CELESTIN_DB_PATH")
    or os.environ.get("MEMORY_BRIDGE_DB_PATH")
    or str(DEFAULT_DB_PATH)
)

# Every key in this dict is a schema version. Each value contains complete SQL
# statements; trigger bodies intentionally remain a single statement despite
# their internal semicolons. Applying a migration bumps user_version to its key.
SCHEMA_MIGRATIONS: dict[int, tuple[str, ...]] = {
    1: (
        """
    CREATE TABLE IF NOT EXISTS chat_messages (
        id              INTEGER PRIMARY KEY,
        project         TEXT NOT NULL,
        conversation_id TEXT NOT NULL DEFAULT 'default',
        role            TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
        content         TEXT NOT NULL,
        created_at      TEXT NOT NULL
    )
    """,

        """
    CREATE INDEX IF NOT EXISTS idx_chat_messages_project_conversation
        ON chat_messages (project, conversation_id, id)
    """,

        """
    CREATE TABLE IF NOT EXISTS project_memory (
        id                INTEGER PRIMARY KEY,
        project           TEXT NOT NULL,
        key               TEXT NOT NULL,
        value             TEXT NOT NULL,
        status            TEXT NOT NULL
            CHECK (status IN ('draft', 'approved', 'deprecated')),
        source_message_id INTEGER REFERENCES chat_messages(id) ON DELETE SET NULL,
        updated_at        TEXT NOT NULL,

        UNIQUE (project, key)
    )
    """,

        """
    CREATE INDEX IF NOT EXISTS idx_project_memory_project
        ON project_memory (project)
    """,
    ),
    2: (
        """
    -- FTS5 indexes are maintained alongside the authoritative rows. Metadata
    -- columns stay UNINDEXED but are returned by the retrieval adapter.
    CREATE VIRTUAL TABLE IF NOT EXISTS chat_messages_fts USING fts5(
        message_id UNINDEXED,
        project_id UNINDEXED,
        source_id UNINDEXED,
        created_at UNINDEXED,
        content
    )
    """,

        """
    CREATE TRIGGER IF NOT EXISTS chat_messages_fts_insert
    AFTER INSERT ON chat_messages BEGIN
        INSERT INTO chat_messages_fts
            (rowid, message_id, project_id, source_id, created_at, content)
        VALUES
            (new.id, CAST(new.id AS TEXT), new.project, new.conversation_id,
             new.created_at, new.content);
    END
    """,

        """
    CREATE TRIGGER IF NOT EXISTS chat_messages_fts_delete
    AFTER DELETE ON chat_messages BEGIN
        DELETE FROM chat_messages_fts WHERE rowid = old.id;
    END
    """,

        """
    CREATE TRIGGER IF NOT EXISTS chat_messages_fts_update
    AFTER UPDATE ON chat_messages BEGIN
        DELETE FROM chat_messages_fts WHERE rowid = old.id;
        INSERT INTO chat_messages_fts
            (rowid, message_id, project_id, source_id, created_at, content)
        VALUES
            (new.id, CAST(new.id AS TEXT), new.project, new.conversation_id,
             new.created_at, new.content);
    END
    """,

        """
    CREATE VIRTUAL TABLE IF NOT EXISTS project_memory_fts USING fts5(
        decision_id UNINDEXED,
        project_id UNINDEXED,
        source_id UNINDEXED,
        updated_at UNINDEXED,
        status UNINDEXED,
        value
    )
    """,

        """
    CREATE TRIGGER IF NOT EXISTS project_memory_fts_insert
    AFTER INSERT ON project_memory BEGIN
        INSERT INTO project_memory_fts
            (rowid, decision_id, project_id, source_id, updated_at, status, value)
        VALUES
            (new.id, CAST(new.id AS TEXT), new.project,
             COALESCE(CAST(new.source_message_id AS TEXT), 'decision:' || CAST(new.id AS TEXT)),
             new.updated_at, new.status, new.value);
    END
    """,

        """
    CREATE TRIGGER IF NOT EXISTS project_memory_fts_delete
    AFTER DELETE ON project_memory BEGIN
        DELETE FROM project_memory_fts WHERE rowid = old.id;
    END
    """,

        """
    CREATE TRIGGER IF NOT EXISTS project_memory_fts_update
    AFTER UPDATE ON project_memory BEGIN
        DELETE FROM project_memory_fts WHERE rowid = old.id;
        INSERT INTO project_memory_fts
            (rowid, decision_id, project_id, source_id, updated_at, status, value)
        VALUES
            (new.id, CAST(new.id AS TEXT), new.project,
             COALESCE(CAST(new.source_message_id AS TEXT), 'decision:' || CAST(new.id AS TEXT)),
             new.updated_at, new.status, new.value);
    END
    """,

        """
    INSERT INTO chat_messages_fts
        (rowid, message_id, project_id, source_id, created_at, content)
    SELECT id, CAST(id AS TEXT), project, conversation_id, created_at, content
    FROM chat_messages
    """,

        """
    INSERT INTO project_memory_fts
        (rowid, decision_id, project_id, source_id, updated_at, status, value)
    SELECT id, CAST(id AS TEXT), project,
           COALESCE(CAST(source_message_id AS TEXT), 'decision:' || CAST(id AS TEXT)),
           updated_at, status, value
    FROM project_memory
    """,
    ),
}

_ROLE_VALUES = ("user", "assistant", "system")
_STATUS_VALUES = ("draft", "approved", "deprecated")


class DatabaseError(RuntimeError):
    """Raised for database-level failures (SQL errors, broken migration)."""


def _resolve_path(path: str | Path | None) -> Path:
    """Resolve a database path argument, falling back to DB_PATH."""
    if path is not None:
        return Path(path)
    return DB_PATH


def _create_connection(file_path: Path) -> sqlite3.Connection:
    """Open a configured SQLite connection to ``file_path``."""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        conn = sqlite3.connect(str(file_path), isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
    except sqlite3.Error as exc:  # pragma: no cover - depends on OS state
        raise DatabaseError(f"cannot open database at {file_path}: {exc}") from exc
    return conn


def _migration_applied(conn: sqlite3.Connection, version: int) -> bool:
    row = conn.execute("PRAGMA user_version").fetchone()
    return int(row[0]) >= version


def _apply_migration(
    conn: sqlite3.Connection,
    version: int,
    statements: tuple[str, ...],
) -> None:
    """Apply one migration atomically, rechecking its version under the write lock."""
    try:
        conn.execute("BEGIN IMMEDIATE")
        if _migration_applied(conn, version):
            conn.execute("COMMIT")
            return

        for statement in statements:
            conn.execute(statement)

        conn.execute(f"PRAGMA user_version = {int(version)}")
        conn.execute("COMMIT")
    except BaseException as exc:
        if conn.in_transaction:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error as rollback_error:
                raise DatabaseError(
                    f"schema migration {version} failed and rollback failed: "
                    f"{rollback_error}"
                ) from exc
        if isinstance(exc, sqlite3.Error):
            raise DatabaseError(f"schema migration {version} failed: {exc}") from exc
        raise


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    """Open (and create if needed) the Celestin database.

    Applies any pending schema migrations. Returns an open ``sqlite3``
    connection in autocommit mode (``isolation_level=None``); callers who
    want multi-statement atomicity manage transactions explicitly.
    """
    file_path = _resolve_path(path)
    conn = _create_connection(file_path)
    try:
        _migrate(conn)
    except BaseException:
        conn.close()
        raise
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    for version in sorted(SCHEMA_MIGRATIONS):
        if _migration_applied(conn, version):
            continue
        _apply_migration(conn, version, SCHEMA_MIGRATIONS[version])


def initialize(path: str | Path | None = None) -> sqlite3.Connection:
    """Backwards-friendly alias for :func:`connect`."""
    return connect(path)


# Value sets shared with the repositories layer for validation.
ROLE_VALUES = _ROLE_VALUES
STATUS_VALUES = _STATUS_VALUES
