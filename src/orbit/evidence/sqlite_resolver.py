"""Project-scoped source resolution for local SQLite Evidence references."""

from __future__ import annotations

from contextlib import closing
from pathlib import Path

from orbit.database.engine import connect
from orbit.repositories._validation import NotFoundError
from orbit.repositories.messages import ChatMessageRepository

from .model import EvidenceSource, EvidenceType, ReferenceStatus
from .service import EvidenceReferenceResolver

_MAX_SQLITE_INTEGER = (1 << 63) - 1


class SQLiteMessageEvidenceResolver(EvidenceReferenceResolver):
    """Resolve local message IDs against authoritative project-scoped rows.

    Other Evidence types are retained as unresolved; this resolver makes no
    claims about repository files, commits, tests, documents, or generic sources.
    """

    def __init__(self, db_path: str | Path | None = None) -> None:
        self._db_path = db_path

    def resolve(self, project: str, source: EvidenceSource) -> ReferenceStatus:
        if source.evidence_type is not EvidenceType.MESSAGE:
            return ReferenceStatus.UNRESOLVED

        message_id = _canonical_message_id(source.source_id)
        if message_id is None:
            return ReferenceStatus.MISSING

        with closing(connect(self._db_path)) as connection:
            try:
                ChatMessageRepository(connection).get_for_project(project, message_id)
            except NotFoundError:
                return ReferenceStatus.MISSING
        return ReferenceStatus.AVAILABLE


def _canonical_message_id(value: object) -> int | None:
    """Parse only the positive decimal ID representation emitted by SQLite."""
    if (
        not isinstance(value, str)
        or not value
        or len(value) > len(str(_MAX_SQLITE_INTEGER))
        or not value.isascii()
        or not value.isdecimal()
        or value.startswith("0")
    ):
        return None
    parsed = int(value)
    return parsed if parsed <= _MAX_SQLITE_INTEGER else None
