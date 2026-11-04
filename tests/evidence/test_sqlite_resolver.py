"""Project-scoped local message Evidence resolution tests."""

from contextlib import closing

import pytest

from orbit.database.engine import connect
from orbit.evidence.model import EvidenceSource, EvidenceType, ReferenceStatus
from orbit.evidence.sqlite_resolver import SQLiteMessageEvidenceResolver
from orbit.repositories.messages import ChatMessageRepository


def _insert_message(db_path, project: str) -> str:
    with closing(connect(db_path)) as connection:
        message_id = ChatMessageRepository(connection).insert(
            project, "conversation", "user", "source message"
        )
    return str(message_id)


def _source(source_id: str, evidence_type: EvidenceType = EvidenceType.MESSAGE):
    return EvidenceSource(evidence_type=evidence_type, source_id=source_id)


def test_resolves_existing_message_in_requested_project(tmp_path):
    db_path = tmp_path / "memory.db"
    message_id = _insert_message(db_path, "project-a")
    resolver = SQLiteMessageEvidenceResolver(db_path)

    assert (
        resolver.resolve("project-a", _source(message_id))
        is ReferenceStatus.AVAILABLE
    )


def test_missing_and_foreign_project_messages_have_same_resolution(tmp_path):
    db_path = tmp_path / "memory.db"
    foreign_id = _insert_message(db_path, "project-b")
    resolver = SQLiteMessageEvidenceResolver(db_path)

    foreign = resolver.resolve("project-a", _source(foreign_id))
    missing = resolver.resolve("project-a", _source("9223372036854775807"))

    assert foreign is ReferenceStatus.MISSING
    assert foreign is missing


@pytest.mark.parametrize(
    "source_id",
    ["", "0", "01", "+1", " 1", "1 ", "١", str(1 << 63), "9" * 5000],
)
def test_malformed_or_noncanonical_message_ids_resolve_as_missing(tmp_path, source_id):
    db_path = tmp_path / "memory.db"
    resolver = SQLiteMessageEvidenceResolver(db_path)

    assert resolver.resolve("project-a", _source(source_id)) is ReferenceStatus.MISSING
    assert not db_path.exists()


@pytest.mark.parametrize(
    "evidence_type",
    [
        EvidenceType.REPOSITORY_FILE,
        EvidenceType.GIT_COMMIT,
        EvidenceType.TEST_RESULT,
        EvidenceType.DOCUMENT,
        EvidenceType.OTHER,
    ],
)
def test_unsupported_source_types_remain_unresolved_without_database_access(
    tmp_path, evidence_type
):
    db_path = tmp_path / "memory.db"
    resolver = SQLiteMessageEvidenceResolver(db_path)

    assert (
        resolver.resolve("project-a", _source("anything", evidence_type))
        is ReferenceStatus.UNRESOLVED
    )
    assert not db_path.exists()
