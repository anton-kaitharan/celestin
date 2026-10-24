"""Unit tests for the PostgreSQL migration runner; no PostgreSQL is contacted."""

from __future__ import annotations

from contextlib import contextmanager
import re
from threading import Lock, Thread
import time

import pytest

from orbit.postgres.migrations.runner import (
    Migration,
    MigrationError,
    _HAS_EXISTING_OBJECTS_SQL,
    apply_migrations,
    load_migrations,
    validate_migrations,
)


class Result:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class FakeDatabase:
    def __init__(self):
        self.migration_lock = Lock()
        self.ledger_exists = False
        self.ledger_schema = ""
        self.ledger_array_type_exists = False
        self.ledger = []
        self.objects = False
        self.unrelated_objects_added_after_ledger = False
        self.object_guard_calls = []
        self.executed_migrations = []


class FakeConnection:
    """Models the SQL protocol and serialized connection lock, not PG itself."""

    def __init__(self, database):
        self.database = database
        self.autocommit = True

    def execute(self, query, params=None, *, prepare=None):
        normalized = query.lstrip()
        if normalized.startswith("SELECT pg_advisory_lock"):
            self.database.migration_lock.acquire()
            return Result([(None,)])
        if normalized.startswith("SELECT pg_advisory_unlock"):
            self.database.migration_lock.release()
            return Result([(True,)])
        if normalized.startswith("SELECT to_regclass"):
            return Result([("public.celestin_schema_migrations",)] if self.database.ledger_exists else [(None,)])
        if normalized.startswith("SET LOCAL search_path TO public"):
            return Result()
        if normalized.startswith("SELECT EXISTS") and "FROM pg_catalog.pg_namespace n" in query:
            ignore_ledger_relation, ignore_ledger_array_type = params
            self.database.object_guard_calls.append(params)
            found = self.database.objects
            if self.database.ledger_exists:
                if not ignore_ledger_relation:
                    found = True
                if self.database.ledger_array_type_exists and not ignore_ledger_array_type:
                    found = True
                if self.database.unrelated_objects_added_after_ledger:
                    found = True
            return Result([(found,)])
        if normalized.startswith("CREATE TABLE public.celestin_schema_migrations"):
            self.database.ledger_exists = True
            # CREATE TABLE creates a composite row type and its associated array type.
            self.database.ledger_array_type_exists = True
            self.database.ledger_schema = normalized
            return Result()
        if normalized.startswith("SELECT version, name, checksum"):
            rows = sorted(self.database.ledger, key=lambda row: row[0])
            return Result(rows)
        if normalized.startswith("INSERT INTO public.celestin_schema_migrations"):
            self.database.ledger.append(params)
            return Result()
        if normalized.startswith("CREATE TEST"):
            if "SLOW" in query:
                time.sleep(0.03)
            if "FAIL" in query:
                raise RuntimeError("simulated DDL failure")
            self.database.executed_migrations.append(normalized)
            return Result()
        raise AssertionError(f"unexpected SQL: {query[:100]}")

    @contextmanager
    def transaction(self):
        state = (
            self.database.ledger_exists,
            list(self.database.ledger),
            list(self.database.executed_migrations),
        )
        try:
            yield
        except Exception:
            (
                self.database.ledger_exists,
                self.database.ledger,
                self.database.executed_migrations,
            ) = state
            raise


def migration(version: int, sql: str = "CREATE TEST;", name: str | None = None) -> Migration:
    return Migration(version, name or f"{version:04d}_test.sql", sql)


def test_packaged_baseline_is_ordered_and_distinct_from_schema_draft():
    migrations = load_migrations()
    assert [item.version for item in migrations] == [1, 2, 3, 4, 5, 6, 7, 8, 9]
    assert migrations[0].name == "0001_hosted_baseline.sql"
    assert "CREATE TABLE celestin_users" in migrations[0].sql
    assert "BEGIN;" not in migrations[0].sql
    assert "COMMIT;" not in migrations[0].sql
    assert len(migrations[0].checksum) == 64
    assert migrations[1].name == "0002_user_status.sql"
    assert "ADD COLUMN status TEXT NOT NULL DEFAULT 'suspended'" in migrations[1].sql
    assert "CHECK (status IN ('active', 'suspended'))" in migrations[1].sql
    assert migrations[2].name == "0003_retrieval_search_indexes.sql"
    assert "idx_chat_messages_search_vector" in migrations[2].sql
    assert "to_tsvector('simple'::regconfig, content)" in migrations[2].sql
    assert "WHERE archived_at IS NULL" in migrations[2].sql
    assert "idx_project_memory_search_vector" in migrations[2].sql
    assert migrations[3].name == "0004_memory_capabilities.sql"
    assert "can_approve_candidates BOOLEAN NOT NULL DEFAULT FALSE" in migrations[3].sql
    assert "can_convert_memory BOOLEAN NOT NULL DEFAULT FALSE" in migrations[3].sql
    assert "can_read_history BOOLEAN NOT NULL DEFAULT FALSE" in migrations[3].sql
    assert "can_read_audit BOOLEAN NOT NULL DEFAULT FALSE" in migrations[3].sql
    assert migrations[3].sql.count("ALTER TABLE") == 2
    assert migrations[4].name == "0005_evidence_linked_memory.sql"
    assert "CREATE TABLE celestin_knowledge_records" in migrations[4].sql
    assert "CREATE TABLE celestin_evidence_records" in migrations[4].sql
    assert "capture_provenance" in migrations[4].sql
    assert "JSONB" in migrations[4].sql
    assert "created_at             TIMESTAMPTZ NOT NULL" in migrations[4].sql
    assert "captured_at           TIMESTAMPTZ NOT NULL" in migrations[4].sql
    assert "CREATE UNIQUE INDEX idx_celestin_knowledge_one_successor" in migrations[4].sql
    assert migrations[5].name == "0006_candidate_revision_store.sql"
    assert migrations[6].name == "0007_memory_request_rls.sql"
    assert "ALTER TABLE chat_messages FORCE ROW LEVEL SECURITY" in migrations[6].sql
    assert "ALTER TABLE project_memory FORCE ROW LEVEL SECURITY" in migrations[6].sql
    assert migrations[6].sql.count("celestin_request_scope_matches(workspace_id, project_id)") == 4

    assert migrations[7].name == "0008_project_scoped_runtime_roles.sql"
    migration_eight = migrations[7].sql
    normalized_eight = " ".join(migration_eight.split())
    assert "CREATE SCHEMA celestin_private" in migration_eight
    assert "CREATE TABLE celestin_private.project_runtime_scopes" in migration_eight
    assert "login_role_name NAME PRIMARY KEY" in migration_eight
    assert "UNIQUE (workspace_id, project_id)" not in normalized_eight
    assert "CREATE ROLE" not in migration_eight.upper()
    assert "session_user::name" in migration_eight
    assert "CREATE OR REPLACE FUNCTION public.celestin_request_scope_matches" in normalized_eight
    assert "current_setting('celestin.workspace_id'" not in migration_eight
    assert "celestin_project_scope_admin_all" in migration_eight
    assert "celestin_authorization_admin" in migration_eight
    assert "DROP POLICY IF EXISTS" in migration_eight
    assert "FOR ALL TO celestin_project_runtime" in migration_eight
    assert "USING (login_role_name = session_user::name)" in normalized_eight
    assert "WHERE scope.login_role_name = session_user::name" in normalized_eight
    assert "GRANT SELECT ON celestin_private.project_runtime_scopes TO celestin_project_runtime" in normalized_eight
    assert "GRANT SELECT, INSERT, DELETE" in normalized_eight
    assert "GRANT UPDATE ON celestin_private.project_runtime_scopes" not in normalized_eight
    assert "REVOKE ALL ON FUNCTION public.celestin_request_scope_matches(UUID, UUID) FROM PUBLIC" in normalized_eight
    assert "GRANT EXECUTE ON FUNCTION public.celestin_request_scope_matches(UUID, UUID)" in normalized_eight
    for group_role in (
        "celestin_project_runtime",
        "celestin_authorization_reader",
        "celestin_authorization_admin",
    ):
        assert group_role in migration_eight
    for tenant_table in (
        "chat_messages", "project_memory", "celestin_knowledge_records",
        "celestin_evidence_records", "celestin_project_producers",
        "celestin_project_source_namespaces", "celestin_candidates",
        "celestin_candidate_revisions", "celestin_candidate_review_events",
        "celestin_candidate_evidence_records", "celestin_candidate_occurrences",
        "celestin_candidate_conversions",
    ):
        assert f"('{tenant_table}'," in migration_eight
    assert migration_eight.count(
        "CREATE OR REPLACE FUNCTION public.celestin_request_scope_matches"
    ) == 1
    for legacy_name in (
        "celestin_chat_message_request_scope",
        "celestin_project_memory_request_scope",
        "celestin_knowledge_request_scope",
        "celestin_evidence_request_scope",
        "celestin_producer_request_scope",
        "celestin_source_namespace_request_scope",
        "celestin_candidate_request_scope",
        "celestin_candidate_revision_request_scope",
        "celestin_review_request_scope",
        "celestin_candidate_evidence_request_scope",
        "celestin_occurrence_request_scope",
        "celestin_conversion_request_scope",
    ):
        assert legacy_name in migration_eight

    migration_nine = migrations[8]
    assert migration_nine.name == "0009_project_request_lock_scope.sql"
    normalized_nine = " ".join(migration_nine.sql.split())
    assert "CREATE FUNCTION public.celestin_lock_project_authorization_request" in normalized_nine
    assert "scope.login_role_name = session_user::name" in normalized_nine
    assert "scope.workspace_id = target_workspace" in normalized_nine
    assert "celestin_lock_authorization_request(UUID, TEXT, TEXT, TEXT)" in normalized_nine
    assert "TO celestin_authorization_reader" in normalized_nine
    assert "TO celestin_project_runtime" in normalized_nine
    assert "FROM PUBLIC, celestin_project_runtime" in normalized_nine
    assert "OLD.issuer" in migration_nine.sql and "NEW.issuer" in migration_nine.sql
    assert "public.celestin_project_memberships" in migration_nine.sql
    assert "public.celestin_workspace_memberships" in migration_nine.sql
    assert "pg_advisory_xact_lock_shared" in migration_nine.sql
    assert "pg_advisory_xact_lock(" in migration_nine.sql


def test_candidate_revision_migration_defines_scoped_history_and_ordered_occurrences():
    migration_sql = load_migrations()[5].sql
    normalized_sql = " ".join(migration_sql.split())

    def table_body(table_name):
        match = re.search(
            rf"CREATE TABLE {re.escape(table_name)} \((.*?)\);",
            migration_sql,
            re.DOTALL,
        )
        assert match is not None, f"missing table definition: {table_name}"
        return " ".join(match.group(1).split())

    assert "PRIMARY KEY (workspace_id, project_id, producer_id, candidate_id)" in table_body(
        "celestin_candidates"
    )
    revision = table_body("celestin_candidate_revisions")
    assert (
        "PRIMARY KEY (workspace_id, project_id, producer_id, candidate_id, candidate_revision)"
        in revision
    )
    assert "CHECK (candidate_revision > 0)" in revision
    assert "REFERENCES celestin_candidates(workspace_id, project_id, producer_id, candidate_id)" in revision

    review = table_body("celestin_candidate_review_events")
    assert "event_type IN ('approved', 'rejected', 'revoked')" in review
    assert "event_number INTEGER NOT NULL DEFAULT 1" in review

    evidence = table_body("celestin_candidate_evidence_records")
    assert "PRIMARY KEY (workspace_id, project_id, evidence_record_id)" in evidence
    assert "FOREIGN KEY (workspace_id, project_id, source_namespace)" in evidence

    occurrence = table_body("celestin_candidate_occurrences")
    assert (
        "PRIMARY KEY ( workspace_id, project_id, producer_id, candidate_id, "
        "candidate_revision, ordinal )"
        in occurrence
    )
    assert "FOREIGN KEY (workspace_id, project_id, evidence_record_id)" in occurrence
    assert "UNIQUE" not in occurrence.upper()
    assert "occurrence ordinal must be contiguous from zero" in normalized_sql
    assert "candidate revision must be the next sequential revision" in normalized_sql

    conversion = table_body("celestin_candidate_conversions")
    assert (
        "PRIMARY KEY ( workspace_id, project_id, producer_id, candidate_id, "
        "candidate_revision )"
        in conversion
    )
    assert (
        "REFERENCES celestin_knowledge_records( workspace_id, project_id, "
        "knowledge_id )"
        in conversion
    )
    assert "smic-conversion-payload-v1+jcs-rfc8785+sha256" in conversion
    assert "celestin_candidate_identity_immutable" in normalized_sql
    assert "celestin_candidate_conversion_approval" in normalized_sql
    assert "celestin_request_scope_matches(workspace_id, project_id)" in normalized_sql
    assert "current_setting('celestin.workspace_id', true)" in normalized_sql
    assert "current_setting('celestin.project_id', true)" in normalized_sql
    assert "delivery_id" not in normalized_sql


def test_migration_order_is_normalized_and_gaps_or_duplicates_are_rejected():
    assert [item.version for item in validate_migrations([migration(2), migration(1)])] == [1, 2]
    with pytest.raises(MigrationError, match="contiguous"):
        validate_migrations([migration(1), migration(3)])
    with pytest.raises(MigrationError, match="contiguous"):
        validate_migrations([migration(1), migration(1, name="0001_other.sql")])


def test_apply_is_repeatable_and_checksums_are_immutable():
    database = FakeDatabase()
    migrations = [migration(1), migration(2, "CREATE TEST TWO;")]

    first = apply_migrations(FakeConnection(database), migrations)
    second = apply_migrations(FakeConnection(database), migrations)

    assert first.applied_versions == (1, 2)
    assert second.applied_versions == ()
    assert second.current_version == 2
    assert len(database.executed_migrations) == 2
    assert [row[0] for row in database.ledger] == [1, 2]
    assert database.object_guard_calls == [(False, False), (True, True)]

    changed = [migration(1, "CREATE TEST CHANGED;"), migrations[1]]
    with pytest.raises(MigrationError, match="checksum or name differs"):
        apply_migrations(FakeConnection(database), changed)
    assert len(database.executed_migrations) == 2


def test_ledger_contains_version_checksum_and_application_timestamp():
    database = FakeDatabase()
    apply_migrations(FakeConnection(database), [migration(1)])

    ledger_sql = database.ledger_schema.lower()
    assert "version integer primary key" in ledger_sql
    assert "name text not null unique" in ledger_sql
    assert "checksum text not null" in ledger_sql
    assert "applied_at timestamptz not null default current_timestamp" in ledger_sql


def test_refuses_nonempty_untracked_database_without_creating_ledger():
    database = FakeDatabase()
    database.objects = True
    with pytest.raises(MigrationError, match="no Celestin migration ledger"):
        apply_migrations(FakeConnection(database), [migration(1)])
    assert database.ledger_exists is False
    assert database.executed_migrations == []


def test_post_ledger_check_ignores_only_runner_ledger_relation_and_array_type():
    database = FakeDatabase()

    result = apply_migrations(FakeConnection(database), [migration(1)])

    assert result.applied_versions == (1,)
    assert database.ledger_exists is True
    assert database.ledger_array_type_exists is True
    assert database.object_guard_calls == [(False, False), (True, True)]
    assert database.executed_migrations == ["CREATE TEST;"]
    assert (
        "AND (NOT %s OR c.relname <> 'celestin_schema_migrations')"
        in _HAS_EXISTING_OBJECTS_SQL
    )
    assert "ledger_row_type.typarray = t.oid" in _HAS_EXISTING_OBJECTS_SQL


def test_post_ledger_check_still_rejects_unrelated_objects():
    database = FakeDatabase()
    database.unrelated_objects_added_after_ledger = True

    with pytest.raises(MigrationError, match="no applied migration records"):
        apply_migrations(FakeConnection(database), [migration(1)])

    assert database.ledger_exists is True
    assert database.ledger == []
    assert database.object_guard_calls == [(False, False), (True, True)]
    assert database.executed_migrations == []


def test_refuses_to_adopt_a_preexisting_empty_migration_ledger():
    database = FakeDatabase()
    database.ledger_exists = True

    with pytest.raises(MigrationError, match="existing migration ledger has no applied"):
        apply_migrations(FakeConnection(database), [migration(1)])

    assert database.ledger == []
    assert database.object_guard_calls == []
    assert database.executed_migrations == []


def test_refuses_empty_ledger_when_other_schema_objects_exist():
    database = FakeDatabase()
    database.ledger_exists = True
    database.objects = True
    with pytest.raises(MigrationError, match="no applied migration records"):
        apply_migrations(FakeConnection(database), [migration(1)])
    assert database.executed_migrations == []


def test_requires_autocommit_for_per_migration_transactions():
    connection = FakeConnection(FakeDatabase())
    connection.autocommit = False
    with pytest.raises(MigrationError, match="autocommit=True"):
        apply_migrations(connection, [migration(1)])


def test_failed_migration_rolls_back_its_ddl_and_ledger_row_and_releases_lock():
    database = FakeDatabase()
    migrations = [migration(1), migration(2, "CREATE TEST FAIL;")]
    with pytest.raises(MigrationError, match="transaction was rolled back"):
        apply_migrations(FakeConnection(database), migrations)

    assert database.executed_migrations == ["CREATE TEST;"]
    assert [row[0] for row in database.ledger] == [1]
    assert database.migration_lock.acquire(blocking=False)
    database.migration_lock.release()


def test_concurrent_runners_serialize_and_apply_each_migration_once():
    database = FakeDatabase()
    migrations = [migration(1, "CREATE TEST SLOW;")]
    results = []
    errors = []

    def run():
        try:
            results.append(apply_migrations(FakeConnection(database), migrations))
        except Exception as exc:  # surfaced below in the test thread
            errors.append(exc)

    threads = [Thread(target=run), Thread(target=run)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert not errors
    assert all(not thread.is_alive() for thread in threads)
    assert sorted(result.applied_versions for result in results) == [(), (1,)]
    assert database.executed_migrations == ["CREATE TEST SLOW;"]
    assert [row[0] for row in database.ledger] == [1]


def test_ledger_gap_and_unknown_applied_migration_fail_closed():
    database = FakeDatabase()
    database.ledger_exists = True
    database.ledger = [(2, "0002_test.sql", "f" * 64)]
    with pytest.raises(MigrationError, match="not present in this release"):
        apply_migrations(FakeConnection(database), [migration(1)])

    third = migration(3)
    database.ledger = [
        (1, migration(1).name, migration(1).checksum),
        (3, third.name, third.checksum),
    ]
    with pytest.raises(MigrationError, match="version gap"):
        apply_migrations(FakeConnection(database), [migration(1), migration(2), third])
