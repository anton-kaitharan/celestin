"""SQLite-backed conformance and schema tests for EvidenceLinkedMemoryStore."""

from __future__ import annotations

from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import sqlite3
from threading import Barrier
from tempfile import TemporaryDirectory

import pytest

from orbit.database.engine import SCHEMA_MIGRATIONS, connect
from orbit.evidence.model import (
    EvidenceRecord,
    EvidenceType,
    KnowledgeRecord,
    KnowledgeType,
    ReferenceStatus,
    VerificationStatus,
)
from orbit.evidence.service import (
    EvidenceNotAssociated,
    InvalidKnowledgeTransition,
    KnowledgeNotFound,
)
from orbit.evidence.sqlite import SQLiteEvidenceLinkedMemoryStore
from tests.evidence.store_contract import EvidenceLinkedMemoryStoreContract


class TestSQLiteEvidenceLinkedMemoryStoreContract(EvidenceLinkedMemoryStoreContract):
    def setup_method(self):
        self._temporary_directory = TemporaryDirectory()
        self._db_path = f"{self._temporary_directory.name}/memory.db"

    def teardown_method(self):
        self._temporary_directory.cleanup()

    def make_store(self):
        return SQLiteEvidenceLinkedMemoryStore(self._db_path)

    def arm_failure_after_staging(self, store, operation):
        insert_evidence = store._insert_evidence

        def fail_after_insert(connection, evidence, knowledge_id, position):
            insert_evidence(connection, evidence, knowledge_id, position)
            raise RuntimeError(f"injected {operation} failure after staging")

        store._insert_evidence = fail_after_insert


def test_create_preserves_evidence_input_order():
    with TemporaryDirectory() as temporary_directory:
        store = SQLiteEvidenceLinkedMemoryStore(f"{temporary_directory}/memory.db")
        record = _knowledge("project-a", "ordered")
        evidence = (
            _evidence("project-a", "z-first", "source-z"),
            _evidence("project-a", "a-second", "source-a"),
        )

        store.create_knowledge(record, evidence)

        assert tuple(store.list_evidence(record.project, record.knowledge_id)) == evidence


def test_schema_rejects_cross_project_references_and_invalid_evidence_rows():
    with TemporaryDirectory() as temporary_directory:
        db_path = f"{temporary_directory}/memory.db"
        store = SQLiteEvidenceLinkedMemoryStore(db_path)
        store.create_knowledge(_knowledge("project-a", "parent"), ())
        connection = connect(db_path)
        try:
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    _EVIDENCE_INSERT,
                    _evidence_values(
                        project="project-b",
                        knowledge_id="parent",
                        evidence_id="cross-project",
                    ),
                )

            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    _EVIDENCE_INSERT,
                    _evidence_values(
                        project="project-a",
                        knowledge_id="parent",
                        evidence_id="invalid-type",
                        evidence_type="not-an-evidence-type",
                    ),
                )

            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    _EVIDENCE_INSERT,
                    _evidence_values(
                        project="project-a",
                        knowledge_id="parent",
                        evidence_id="negative-position",
                        position=-1,
                    ),
                )
        finally:
            connection.close()


def test_schema_enforces_one_successor_and_unique_evidence_positions():
    with TemporaryDirectory() as temporary_directory:
        db_path = f"{temporary_directory}/memory.db"
        store = SQLiteEvidenceLinkedMemoryStore(db_path)
        previous = _knowledge("project-a", "previous")
        store.create_knowledge(previous, (_evidence("project-a", "e1", "source-1"),))
        first_successor = replace(
            _knowledge("project-a", "successor-1"),
            supersedes_id=previous.knowledge_id,
        )
        store.create_knowledge(first_successor, ())

        second_successor = replace(
            _knowledge("project-a", "successor-2"),
            supersedes_id=previous.knowledge_id,
        )
        with pytest.raises(sqlite3.IntegrityError):
            store.create_knowledge(second_successor, ())

        connection = connect(db_path)
        try:
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    _EVIDENCE_INSERT,
                    _evidence_values(
                        project="project-a",
                        knowledge_id=previous.knowledge_id,
                        evidence_id="duplicate-position",
                        position=0,
                    ),
                )
        finally:
            connection.close()


def test_concurrent_duplicate_attachments_leave_one_association():
    with TemporaryDirectory() as temporary_directory:
        store = SQLiteEvidenceLinkedMemoryStore(f"{temporary_directory}/memory.db")
        record = _knowledge("project-a", "concurrent-attach")
        store.create_knowledge(record, ())
        barrier = Barrier(2)
        candidates = (
            _evidence("project-a", "evidence-a", "same-source"),
            _evidence("project-a", "evidence-b", "same-source"),
        )

        def attach(candidate):
            barrier.wait(timeout=5)
            return store.attach_evidence(
                record.project, record.knowledge_id, candidate
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            returned = tuple(executor.map(attach, candidates))

        saved = tuple(store.list_evidence(record.project, record.knowledge_id))
        assert len(saved) == 1
        assert returned[0].evidence_id == returned[1].evidence_id == saved[0].evidence_id


def test_concurrent_supersessions_allow_only_one_successor():
    with TemporaryDirectory() as temporary_directory:
        store = SQLiteEvidenceLinkedMemoryStore(f"{temporary_directory}/memory.db")
        previous = _knowledge("project-a", "concurrent-previous")
        store.create_knowledge(previous, ())
        successors = tuple(
            replace(
                _knowledge("project-a", f"concurrent-successor-{index}"),
                supersedes_id=previous.knowledge_id,
            )
            for index in (1, 2)
        )
        barrier = Barrier(2)

        def supersede(successor):
            barrier.wait(timeout=5)
            try:
                store.supersede_knowledge(
                    previous.project, previous.knowledge_id, successor, ()
                )
            except InvalidKnowledgeTransition:
                return False
            return True

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = tuple(executor.map(supersede, successors))

        assert sorted(results) == [False, True]
        assert store.get_knowledge(
            previous.project, previous.knowledge_id
        ).lifecycle_status.value == "superseded"
        saved_successors = tuple(
            item
            for item in successors
            if store.get_knowledge(item.project, item.knowledge_id) is not None
        )
        assert len(saved_successors) == 1


def test_version_two_database_upgrade_preserves_existing_local_data():
    with TemporaryDirectory() as temporary_directory:
        db_path = f"{temporary_directory}/memory.db"
        connection = sqlite3.connect(db_path, isolation_level=None)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            for version in (1, 2):
                for statement in SCHEMA_MIGRATIONS[version]:
                    connection.execute(statement)
            connection.execute(
                """
                INSERT INTO chat_messages
                    (id, project, conversation_id, role, content, created_at)
                VALUES (1, 'project-a', 'conversation-a', 'user', 'existing message', 'time')
                """
            )
            connection.execute(
                """
                INSERT INTO project_memory
                    (id, project, key, value, status, source_message_id, updated_at)
                VALUES (1, 'project-a', 'decision', 'existing decision', 'approved', 1, 'time')
                """
            )
            connection.execute("PRAGMA user_version = 2")
        finally:
            connection.close()

        store = SQLiteEvidenceLinkedMemoryStore(db_path)
        assert store.get_knowledge("project-a", "missing") is None

        connection = connect(db_path)
        try:
            assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
            assert connection.execute(
                "SELECT content FROM chat_messages WHERE id = 1"
            ).fetchone()[0] == "existing message"
            assert connection.execute(
                "SELECT value FROM project_memory WHERE id = 1"
            ).fetchone()[0] == "existing decision"
        finally:
            connection.close()


def test_missing_verification_targets_raise_without_creating_records():
    with TemporaryDirectory() as temporary_directory:
        store = SQLiteEvidenceLinkedMemoryStore(f"{temporary_directory}/memory.db")
        record = _knowledge("project-a", "missing")
        evidence = _evidence("project-a", "missing-evidence", "source")

        with pytest.raises(KnowledgeNotFound):
            store.set_knowledge_verification("project-a", record.knowledge_id, record)
        with pytest.raises(EvidenceNotAssociated):
            store.set_evidence_verification(
                "project-a", record.knowledge_id, evidence
            )
        assert store.get_knowledge("project-a", record.knowledge_id) is None


def _knowledge(project: str, knowledge_id: str) -> KnowledgeRecord:
    return KnowledgeRecord(
        knowledge_id=knowledge_id,
        project=project,
        knowledge_type=KnowledgeType.DISCOVERY,
        claim="A durable domain claim.",
        created_at="2026-10-18T09:20:00Z",
        updated_at="2026-10-18T09:25:00Z",
        capture_provenance=None,
    )


def _evidence(
    project: str, evidence_id: str, source_id: str
) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=evidence_id,
        project=project,
        evidence_type=EvidenceType.DOCUMENT,
        source_id=source_id,
        locator=None,
        revision=None,
        content_hash=None,
        captured_at="2026-10-18T09:20:00Z",
        reference_status=ReferenceStatus.UNRESOLVED,
        verification_status=VerificationStatus.UNVERIFIED,
    )


_EVIDENCE_INSERT = """
    INSERT INTO celestin_evidence_records
        (project, knowledge_id, evidence_id, evidence_position, evidence_type,
         source_id, locator, revision, content_hash, captured_at, reference_status,
         verification_status, checked_at, checked_by, check_note)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def _evidence_values(
    *,
    project: str,
    knowledge_id: str,
    evidence_id: str,
    evidence_type: str = EvidenceType.DOCUMENT.value,
    position: int = 0,
) -> tuple:
    return (
        project,
        knowledge_id,
        evidence_id,
        position,
        evidence_type,
        "source",
        None,
        None,
        None,
        "2026-10-18T09:20:00Z",
        ReferenceStatus.UNRESOLVED.value,
        VerificationStatus.UNVERIFIED.value,
        None,
        None,
        None,
    )
