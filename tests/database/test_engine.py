"""Engine tests: initialization, idempotency, persistence across restarts."""

from __future__ import annotations

import multiprocessing
import queue
import sqlite3
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor

import pytest

import orbit.database.engine as engine
from orbit.database.engine import (
    DatabaseError,
    SCHEMA_MIGRATIONS,
    connect,
)


def _initialize_database_in_process(database_path, start_barrier, results):
    """Initialize one database in a spawned process and report its state."""
    process_name = multiprocessing.current_process().name
    try:
        migration_applied = engine._migration_applied
        first_version = min(SCHEMA_MIGRATIONS)
        initial_check = True

        def synchronize_initial_version_check(conn, version):
            nonlocal initial_check
            applied = migration_applied(conn, version)
            if initial_check and version == first_version:
                initial_check = False
                start_barrier.wait(timeout=15)
            return applied

        engine._migration_applied = synchronize_initial_version_check
        try:
            conn = connect(database_path)
        finally:
            engine._migration_applied = migration_applied
        try:
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            conn.close()
    except BaseException:
        results.put(("error", f"{process_name}:\n{traceback.format_exc()}"))
        raise
    results.put(("ok", version, integrity, process_name))


def test_connect_creates_tables_from_clean_checkout(tmp_path):
    conn = connect(tmp_path / "fresh.db")
    names = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert {"chat_messages", "project_memory"} <= names


def test_migrations_are_idempotent(tmp_path):
    path = tmp_path / "fresh.db"
    first = connect(path)
    first.close()
    second = connect(path)  # must not raise or duplicate anything
    row = second.execute("PRAGMA user_version").fetchone()
    assert int(row[0]) == max(SCHEMA_MIGRATIONS)


def test_schema_check_constraints_present(tmp_path):
    conn = connect(tmp_path / "fresh.db")
    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='project_memory'"
    ).fetchone()[0]
    assert "'draft', 'approved', 'deprecated'" in sql
    assert "UNIQUE (project, key)" in sql
    assert "REFERENCES chat_messages(id)" in sql


def test_persists_across_restart(tmp_path):
    first = connect(tmp_path / "test.db")
    first.execute(
        "INSERT INTO chat_messages (project, conversation_id, role, content, created_at)"
        " VALUES ('proj-a', 'conv-1', 'user', 'remember me', '2026-01-01T00:00:00Z')"
    )
    first.commit()
    first.close()

    # Simulate a restart: brand-new connection to the same file.
    second = connect(tmp_path / "test.db")
    rows = second.execute(
        "SELECT content FROM chat_messages ORDER BY id"
    ).fetchall()
    assert [r[0] for r in rows] == ["remember me"]


def test_check_constraint_rejects_bad_status(tmp_path):
    conn = connect(tmp_path / "fresh.db")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO project_memory"
            " (project, key, value, status, source_message_id, updated_at)"
            " VALUES ('p', 'k', 'v', 'deleted', NULL, '2026-01-01T00:00:00Z')"
        )


def test_migration_failure_rolls_back_schema_version_and_connection(
    tmp_path, monkeypatch
):
    path = tmp_path / "failed-migration.db"
    opened_connections = []
    create_connection = engine._create_connection

    def capture_connection(file_path):
        conn = create_connection(file_path)
        opened_connections.append(conn)
        return conn

    monkeypatch.setattr(engine, "_create_connection", capture_connection)
    monkeypatch.setattr(
        engine,
        "SCHEMA_MIGRATIONS",
        {
            1: (
                "CREATE TABLE migration_source (id INTEGER PRIMARY KEY, value TEXT)",
                "CREATE TABLE migration_effect (value TEXT)",
                """
                CREATE TRIGGER migration_source_insert
                AFTER INSERT ON migration_source BEGIN
                    INSERT INTO migration_effect(value) VALUES (new.value);
                    UPDATE migration_source SET value = value WHERE id = new.id;
                END
                """,
                "THIS IS NOT VALID SQL",
            )
        },
    )

    with pytest.raises(DatabaseError, match="schema migration 1 failed"):
        engine.connect(path)

    assert len(opened_connections) == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        opened_connections[0].execute("SELECT 1")

    inspection = sqlite3.connect(path)
    try:
        objects = inspection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE name IN ('migration_source', 'migration_effect', "
            "'migration_source_insert')"
        ).fetchall()
        assert objects == []
        assert inspection.execute("PRAGMA user_version").fetchone()[0] == 0
    finally:
        inspection.close()


def test_migration_trigger_statements_track_message_and_decision_changes(tmp_path):
    conn = connect(tmp_path / "trigger-migration.db")
    try:
        message_id = conn.execute(
            "INSERT INTO chat_messages "
            "(project, conversation_id, role, content, created_at) "
            "VALUES ('p', 'c', 'user', 'before', '2026-01-01T00:00:00Z')"
        ).lastrowid
        assert tuple(
            conn.execute(
                "SELECT message_id, content FROM chat_messages_fts WHERE rowid = ?",
                (message_id,),
            ).fetchone()
        ) == (str(message_id), "before")

        conn.execute(
            "UPDATE chat_messages SET content = 'after' WHERE id = ?", (message_id,)
        )
        assert conn.execute(
            "SELECT content FROM chat_messages_fts WHERE rowid = ?", (message_id,)
        ).fetchone()[0] == "after"
        conn.execute("DELETE FROM chat_messages WHERE id = ?", (message_id,))
        assert conn.execute(
            "SELECT count(*) FROM chat_messages_fts WHERE rowid = ?", (message_id,)
        ).fetchone()[0] == 0

        decision_id = conn.execute(
            "INSERT INTO project_memory "
            "(project, key, value, status, updated_at) "
            "VALUES ('p', 'k', 'first', 'draft', '2026-01-01T00:00:00Z')"
        ).lastrowid
        assert tuple(
            conn.execute(
                "SELECT decision_id, value FROM project_memory_fts WHERE rowid = ?",
                (decision_id,),
            ).fetchone()
        ) == (str(decision_id), "first")

        conn.execute(
            "UPDATE project_memory SET value = 'second' WHERE id = ?", (decision_id,)
        )
        assert conn.execute(
            "SELECT value FROM project_memory_fts WHERE rowid = ?", (decision_id,)
        ).fetchone()[0] == "second"
        conn.execute("DELETE FROM project_memory WHERE id = ?", (decision_id,))
        assert conn.execute(
            "SELECT count(*) FROM project_memory_fts WHERE rowid = ?", (decision_id,)
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_concurrent_initializers_recheck_version_after_write_lock(tmp_path, monkeypatch):
    path = tmp_path / "concurrent-migration.db"
    first_version_check = threading.Barrier(2)
    thread_state = threading.local()
    migration_applied = engine._migration_applied

    def synchronize_initial_version_checks(conn, version):
        applied = migration_applied(conn, version)
        if version == 1 and not getattr(thread_state, "checked", False):
            thread_state.checked = True
            first_version_check.wait(timeout=10)
        return applied

    monkeypatch.setattr(
        engine, "_migration_applied", synchronize_initial_version_checks
    )

    def initialize():
        conn = engine._create_connection(path)
        try:
            engine._migrate(conn)
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        initializers = [executor.submit(initialize) for _ in range(2)]
        for initializer in initializers:
            initializer.result(timeout=20)

    monkeypatch.setattr(engine, "_migration_applied", migration_applied)
    conn = connect(path)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == max(
            SCHEMA_MIGRATIONS
        )
        for table in ("chat_messages_fts", "project_memory_fts"):
            assert conn.execute(
                "SELECT count(*) FROM sqlite_master "
                "WHERE type = 'table' AND name = ?",
                (table,),
            ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT count(*) FROM sqlite_master WHERE type = 'trigger'"
        ).fetchone()[0] == 6
    finally:
        conn.close()


def test_process_concurrent_initializers_migrate_fresh_database(tmp_path):
    path = tmp_path / "process-concurrent-migration.db"
    context = multiprocessing.get_context("spawn")
    start_barrier = context.Barrier(2)
    results = context.Queue()
    processes = [
        context.Process(
            target=_initialize_database_in_process,
            args=(str(path), start_barrier, results),
        )
        for _ in range(2)
    ]
    started = []

    try:
        for process in processes:
            process.start()
            started.append(process)
        deadline = time.monotonic() + 30
        for process in processes:
            process.join(timeout=max(0, deadline - time.monotonic()))

        still_running = [process.pid for process in processes if process.is_alive()]
        assert not still_running, (
            "database initializers exceeded the 30-second timeout: "
            f"processes={still_running}"
        )

        outcomes = []
        for process in processes:
            try:
                outcomes.append(results.get(timeout=5))
            except queue.Empty:
                outcomes.append(("missing", f"process {process.pid} returned no result"))

        failures = [outcome[1] for outcome in outcomes if outcome[0] != "ok"]
        assert not failures, "process initialization failed:\n" + "\n".join(failures)
        assert [process.exitcode for process in processes] == [0, 0]
        assert all(outcome[1] == max(SCHEMA_MIGRATIONS) for outcome in outcomes)
        assert all(outcome[2] == "ok" for outcome in outcomes)

        # Inspect directly so this assertion cannot hide an incomplete migration
        # by initializing the database a third time.
        inspection = sqlite3.connect(path)
        try:
            assert inspection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert inspection.execute("PRAGMA foreign_key_check").fetchall() == []
            assert int(inspection.execute("PRAGMA user_version").fetchone()[0]) == max(
                SCHEMA_MIGRATIONS
            )
            objects = {
                row[0]
                for row in inspection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE name NOT LIKE 'sqlite_%'"
                ).fetchall()
            }
            assert {
                "chat_messages",
                "project_memory",
                "chat_messages_fts",
                "project_memory_fts",
                "idx_chat_messages_project_conversation",
                "idx_project_memory_project",
                "chat_messages_fts_insert",
                "chat_messages_fts_delete",
                "chat_messages_fts_update",
                "project_memory_fts_insert",
                "project_memory_fts_delete",
                "project_memory_fts_update",
            } <= objects
        finally:
            inspection.close()
    finally:
        for process in started:
            if process.is_alive():
                process.terminate()
        for process in started:
            process.join(timeout=5)
        results.close()
        results.join_thread()
