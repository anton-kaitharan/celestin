"""Local Evidence application composition with real temporary SQLite storage."""

from unittest.mock import Mock

import pytest

from orbit.evidence.model import (
    EvidenceSource,
    EvidenceType,
    KnowledgeType,
    LifecycleStatus,
    ReferenceStatus,
    VerificationStatus,
)
from orbit.evidence.service import EvidenceLinkedMemoryService
from orbit.evidence.sqlite import SQLiteEvidenceLinkedMemoryStore
from orbit.evidence.sqlite_resolver import SQLiteMessageEvidenceResolver
from orbit.mcp_server.authorization import (
    READ,
    WRITE,
    AuthorizedProject,
    ProjectAccessPolicy,
)
from orbit.mcp_server.server import build_runtime
from orbit.storage.sqlite import SQLiteMemoryStore


def _scope(runtime, project: str, action: str) -> AuthorizedProject:
    return runtime.authorizer.authorize(None, project, action)


def _message_source(message_id: str) -> EvidenceSource:
    return EvidenceSource(EvidenceType.MESSAGE, message_id)


def test_runtime_evidence_service_uses_real_sqlite_for_full_knowledge_workflow(
    tmp_path,
):
    db_path = tmp_path / "memory.db"
    authorizer = ProjectAccessPolicy(local_access={"project-a": [READ, WRITE]})
    runtime = build_runtime(db_path=db_path, project_authorizer=authorizer)
    messages = SQLiteMemoryStore(db_path)
    first = messages.insert_message("project-a", "conversation", "user", "first source")
    second = messages.insert_message("project-a", "conversation", "user", "second source")
    write_scope = _scope(runtime, "project-a", WRITE)

    original = runtime.evidence_service.create_knowledge(
        write_scope,
        KnowledgeType.DISCOVERY,
        "Original claim",
        (_message_source(first.message_id),),
    )
    assert original.evidence[0].reference_status is ReferenceStatus.AVAILABLE

    attached = runtime.evidence_service.associate_evidence(
        write_scope, original.record.knowledge_id, _message_source(second.message_id)
    )
    assert attached.reference_status is ReferenceStatus.AVAILABLE

    verified_record = runtime.evidence_service.set_knowledge_verification(
        write_scope,
        original.record.knowledge_id,
        VerificationStatus.VERIFIED,
        checked_by="local-reviewer",
        note="checked locally",
    )
    verified_evidence = runtime.evidence_service.set_evidence_verification(
        write_scope,
        original.record.knowledge_id,
        attached.evidence_id,
        VerificationStatus.VERIFIED,
        checked_by="local-reviewer",
    )
    assert verified_record.verification_status is VerificationStatus.VERIFIED
    assert verified_evidence.verification_status is VerificationStatus.VERIFIED

    read_scope = _scope(runtime, "project-a", READ)
    verified_bundle = runtime.evidence_service.get_knowledge(
        read_scope, original.record.knowledge_id
    )
    assert verified_bundle.record.verification_status is VerificationStatus.VERIFIED
    assert verified_bundle.record.checked_by == "local-reviewer"
    reloaded_evidence = next(
        item
        for item in verified_bundle.evidence
        if item.evidence_id == attached.evidence_id
    )
    assert reloaded_evidence.verification_status is VerificationStatus.VERIFIED
    assert reloaded_evidence.checked_by == "local-reviewer"

    successor = runtime.evidence_service.supersede_knowledge(
        write_scope,
        original.record.knowledge_id,
        KnowledgeType.DISCOVERY,
        "Corrected claim",
        (_message_source(second.message_id),),
    )
    previous = runtime.evidence_service.get_knowledge(
        read_scope, original.record.knowledge_id
    )
    loaded_successor = runtime.evidence_service.get_knowledge(
        read_scope, successor.record.knowledge_id
    )

    assert previous.record.lifecycle_status is LifecycleStatus.SUPERSEDED
    assert previous.record.superseded_by_id == successor.record.knowledge_id
    assert len(previous.evidence) == 2
    assert loaded_successor.record.claim == "Corrected claim"
    assert loaded_successor.evidence[0].source_id == second.message_id


def test_unauthorized_selector_is_denied_before_resolver_or_store_access(
    monkeypatch, tmp_path
):
    runtime = build_runtime(
        db_path=tmp_path / "memory.db",
        project_authorizer=ProjectAccessPolicy(),
    )
    resolve = Mock()
    create = Mock()
    monkeypatch.setattr(runtime.evidence_service._resolver, "resolve", resolve)
    monkeypatch.setattr(runtime.evidence_service._store, "create_knowledge", create)
    forged_scope = AuthorizedProject(project="project-a", action=WRITE, principal=None)

    with pytest.raises(PermissionError, match="project access denied"):
        runtime.evidence_service.create_knowledge(
            forged_scope,
            KnowledgeType.DISCOVERY,
            "Not authorized",
            (_message_source("1"),),
        )

    resolve.assert_not_called()
    create.assert_not_called()
    assert not (tmp_path / "memory.db").exists()


def test_project_selector_cannot_switch_the_runtime_service_grant(monkeypatch, tmp_path):
    runtime = build_runtime(
        db_path=tmp_path / "memory.db",
        project_authorizer=ProjectAccessPolicy(
            local_access={"project-a": [READ, WRITE]}
        ),
    )
    resolve = Mock()
    create = Mock()
    monkeypatch.setattr(runtime.evidence_service._resolver, "resolve", resolve)
    monkeypatch.setattr(runtime.evidence_service._store, "create_knowledge", create)
    forged_scope = AuthorizedProject(project="project-b", action=WRITE, principal=None)

    with pytest.raises(PermissionError, match="project access denied"):
        runtime.evidence_service.create_knowledge(
            forged_scope,
            KnowledgeType.DISCOVERY,
            "Out of scope",
            (_message_source("1"),),
        )

    resolve.assert_not_called()
    create.assert_not_called()
    assert not (tmp_path / "memory.db").exists()


def test_runtime_composes_same_path_lazily(monkeypatch, tmp_path):
    import orbit.evidence.sqlite as sqlite_adapter
    import orbit.evidence.sqlite_resolver as sqlite_resolver

    calls = []
    monkeypatch.setattr(
        sqlite_adapter, "connect", lambda path=None: calls.append(("store", path))
    )
    monkeypatch.setattr(
        sqlite_resolver,
        "connect",
        lambda path=None: calls.append(("resolver", path)),
    )
    db_path = tmp_path / "configured.db"
    runtime = build_runtime(
        db_path=db_path,
        project_authorizer=ProjectAccessPolicy(
            local_access={"project-a": [READ, WRITE]}
        ),
    )

    assert isinstance(runtime.evidence_service, EvidenceLinkedMemoryService)
    assert isinstance(
        runtime.evidence_service._store, SQLiteEvidenceLinkedMemoryStore
    )
    assert isinstance(
        runtime.evidence_service._resolver, SQLiteMessageEvidenceResolver
    )
    assert runtime.evidence_service._store._db_path == db_path
    assert runtime.evidence_service._resolver._db_path == db_path
    assert calls == []
