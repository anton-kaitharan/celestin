"""Offline contract tests for the request-bound PostgreSQL Evidence store."""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
import json
from uuid import UUID

import pytest

from orbit.evidence.model import (
    CaptureKnowledgeProvenance,
    CaptureSourceProvenance,
    EvidenceRecord,
    EvidenceType,
    KnowledgeRecord,
    KnowledgeType,
    LifecycleStatus,
    ReferenceStatus,
    VerificationStatus,
)
from orbit.evidence.postgres import PostgresEvidenceLinkedMemoryStore
from orbit.evidence.service import InvalidKnowledgeTransition, KnowledgeNotFound
from orbit.mcp_server.hosted_scope import HostedAccessDenied, ResolvedProjectScope, VerifiedPrincipal


WORKSPACE = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
PROJECT = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
PRINCIPAL = VerifiedPrincipal("https://issuer.example", "user-1", "human")


class Cursor:
    def __init__(self, row=None, rows=()):
        self.row = row
        self.rows = list(rows)
        self.closed = False

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows

    def close(self):
        self.closed = True


class ScriptedConnection:
    """Models the SQL adapter boundary, not PostgreSQL's SQL semantics."""

    def __init__(self):
        self.knowledge = {}
        self.evidence = {}
        self.queries = []
        self.cursors = []
        self.commits = 0
        self.rollbacks = 0
        self.fail_on = None
        self.fail_after_evidence = None

    def execute(self, query, params=()):
        sql = " ".join(query.split())
        params = tuple(params)
        self.queries.append((sql, params))
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError("injected SQL operation failure")

        row = None
        rows = ()
        if sql.startswith("INSERT INTO celestin_knowledge_records"):
            (
                workspace, project, knowledge_id, kind, claim, created, updated,
                verification, lifecycle, supersedes, superseded_by, checked_at,
                checked_by, note, provenance,
            ) = params
            key = (workspace, project, knowledge_id)
            if key in self.knowledge:
                raise RuntimeError("duplicate knowledge key")
            self.knowledge[key] = (
                knowledge_id, kind, claim, created, updated, verification, lifecycle,
                supersedes, superseded_by, checked_at, checked_by, note,
                json.loads(provenance) if provenance is not None else None,
            )
        elif sql.startswith("INSERT INTO celestin_evidence_records"):
            (
                workspace, project, knowledge_id, evidence_id, position, kind,
                source_id, locator, revision, content_hash, captured_at, reference,
                verification, checked_at, checked_by, note,
            ) = params
            key = (workspace, project, knowledge_id, evidence_id)
            if key in self.evidence:
                raise RuntimeError("duplicate evidence key")
            self.evidence[key] = (
                position, evidence_id, kind, source_id, locator, revision,
                content_hash, captured_at, reference, verification, checked_at,
                checked_by, note,
            )
            if self.fail_after_evidence is not None:
                self.fail_after_evidence -= 1
                if self.fail_after_evidence == 0:
                    self.fail_after_evidence = None
                    raise RuntimeError("injected failure after evidence staging")
        elif sql.startswith("SELECT knowledge_id FROM celestin_knowledge_records"):
            row = (params[2],) if (params[0], params[1], params[2]) in self.knowledge else None
        elif sql.startswith("SELECT evidence_id, evidence_type, source_id") and "AND evidence_type = %s" in sql:
            workspace, project, knowledge_id, kind, source_id, locator, revision, content_hash = params
            matches = [
                value[1:]
                for key, value in self.evidence.items()
                if key[:3] == (workspace, project, knowledge_id)
                and value[2] == kind and value[3] == source_id
                and value[4] == locator and value[5] == revision and value[6] == content_hash
            ]
            matches.sort(key=lambda value: value[0])
            row = matches[0] if matches else None
        elif sql.startswith("SELECT COALESCE(MAX(evidence_position)"):
            workspace, project, knowledge_id = params
            positions = [
                value[0] for key, value in self.evidence.items()
                if key[:3] == (workspace, project, knowledge_id)
            ]
            row = (max(positions, default=-1) + 1,)
        elif sql.startswith("SELECT knowledge_id, knowledge_type"):
            workspace, project, knowledge_id = params
            row = self.knowledge.get((workspace, project, knowledge_id))
        elif sql.startswith("SELECT evidence_id, evidence_type, source_id"):
            workspace, project, knowledge_id = params
            ordered = [
                (value[0], value[1:]) for key, value in self.evidence.items()
                if key[:3] == (workspace, project, knowledge_id)
            ]
            rows = [value for _, value in sorted(ordered, key=lambda item: item[0])]
        elif sql.startswith("SELECT lifecycle_status FROM celestin_knowledge_records"):
            workspace, project, knowledge_id = params
            saved = self.knowledge.get((workspace, project, knowledge_id))
            row = (saved[6],) if saved is not None else None
        elif sql.startswith("UPDATE celestin_knowledge_records") and "SET verification_status" in sql:
            verification, updated_at, checked_at, checked_by, note, workspace, project, knowledge_id = params
            key = (workspace, project, knowledge_id)
            saved = self.knowledge.get(key)
            if saved is not None:
                self.knowledge[key] = (*saved[:4], updated_at, verification, *saved[6:9], checked_at, checked_by, note, saved[12])
                row = (knowledge_id,)
        elif sql.startswith("UPDATE celestin_knowledge_records") and "SET lifecycle_status" in sql:
            lifecycle, successor_id, updated_at, workspace, project, knowledge_id, expected = params
            key = (workspace, project, knowledge_id)
            saved = self.knowledge.get(key)
            if saved is not None and saved[6] == expected:
                self.knowledge[key] = (*saved[:4], updated_at, saved[5], lifecycle, saved[7], successor_id, *saved[9:])
                row = (knowledge_id,)
        elif sql.startswith("UPDATE celestin_evidence_records"):
            verification, checked_at, checked_by, note, workspace, project, knowledge_id, evidence_id = params
            key = (workspace, project, knowledge_id, evidence_id)
            saved = self.evidence.get(key)
            if saved is not None:
                self.evidence[key] = (*saved[:9], verification, checked_at, checked_by, note)
                row = (evidence_id,)
        else:
            raise AssertionError(f"unexpected SQL: {sql}")

        cursor = Cursor(row, rows)
        self.cursors.append(cursor)
        return cursor

    @contextmanager
    def transaction(self):
        before_knowledge = deepcopy(self.knowledge)
        before_evidence = deepcopy(self.evidence)
        try:
            yield
        except Exception:
            self.knowledge = before_knowledge
            self.evidence = before_evidence
            self.rollbacks += 1
            raise
        else:
            self.commits += 1


def make_store(connection=None, *, permissions=frozenset({"read", "write", "memory:read"}), project="project-a"):
    connection = connection or ScriptedConnection()
    scope = ResolvedProjectScope._issue(
        PRINCIPAL,
        UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
        WORKSPACE,
        PROJECT,
        project,
        permissions,
    )
    return PostgresEvidenceLinkedMemoryStore._for_request(connection, scope), connection


def record(project="project-a", knowledge_id="knowledge-1", *, supersedes_id=None):
    return KnowledgeRecord(
        knowledge_id=knowledge_id,
        project=project,
        knowledge_type=KnowledgeType.DISCOVERY,
        claim="A durable domain claim.",
        created_at="2026-10-18T09:20:00.000000Z",
        updated_at="2026-10-18T09:25:00.000000Z",
        verification_status=VerificationStatus.VERIFIED,
        lifecycle_status=LifecycleStatus.CURRENT,
        supersedes_id=supersedes_id,
        checked_at="2026-10-18T09:25:00.000000Z",
        checked_by="local-checker-label",
        check_note="A provenance label, not authenticated identity.",
        capture_provenance=CaptureKnowledgeProvenance(
            candidate_id="local-candidate",
            project_id=project,
            category="discovery",
            certainty="uncertain",
            review_status="approved",
            candidate_verification_status="unverified",
            source_references=(CaptureSourceProvenance("report", "run-1", "2026-10-18T09:00:00Z", "r7", "line:2"),),
            candidate_created_at="2026-10-18T09:00:00Z",
            candidate_updated_at="2026-10-18T09:10:00Z",
            original_claim="Before correction.",
            reviewed_by="reviewer-label",
            reviewed_at="2026-10-18T09:15:00Z",
            review_note="review metadata",
            conflicts_with=("other-local-candidate",),
        ),
    )


def evidence(project="project-a", evidence_id="evidence-1", source_id="src-1", **updates):
    values = {
        "evidence_id": evidence_id,
        "project": project,
        "evidence_type": EvidenceType.DOCUMENT,
        "source_id": source_id,
        "locator": None,
        "revision": None,
        "content_hash": None,
        "captured_at": "2026-10-18T09:20:00.000000Z",
        "reference_status": ReferenceStatus.UNRESOLVED,
        "verification_status": VerificationStatus.UNVERIFIED,
        "checked_at": None,
        "checked_by": None,
        "check_note": None,
    }
    values.update(updates)
    return EvidenceRecord(**values)


def test_request_store_round_trips_current_knowledge_provenance_and_ordered_evidence():
    store, connection = make_store()
    knowledge = record()
    sources = (
        evidence(evidence_id="evidence-2", source_id="src-2"),
        evidence(evidence_id="evidence-1", source_id="src-1"),
    )

    store.create_knowledge(knowledge, sources)

    assert store.get_knowledge("project-a", knowledge.knowledge_id) == knowledge
    assert tuple(store.list_evidence("project-a", knowledge.knowledge_id)) == sources
    assert connection.commits == 1 and connection.rollbacks == 0
    assert all(cursor.closed for cursor in connection.cursors)
    assert all(
        "workspace_id = %s AND project_id = %s" in query
        for query, _ in connection.queries
        if query.startswith("SELECT") or query.startswith("UPDATE")
    )


def test_project_mismatch_and_missing_write_capability_fail_before_data_queries():
    store, connection = make_store(permissions=frozenset({"read", "memory:read"}))
    with pytest.raises(HostedAccessDenied):
        store.create_knowledge(record(), ())
    with pytest.raises(HostedAccessDenied):
        store.get_knowledge("other-project", "knowledge-1")
    assert connection.queries == []


def test_create_rolls_back_knowledge_and_prior_evidence_after_partial_failure():
    store, connection = make_store()
    connection.fail_after_evidence = 2

    with pytest.raises(RuntimeError, match="after evidence staging"):
        store.create_knowledge(
            record(),
            (evidence(), evidence(evidence_id="evidence-2", source_id="src-2")),
        )

    assert connection.knowledge == {}
    assert connection.evidence == {}
    assert connection.rollbacks == 1 and connection.commits == 0


def test_attach_evidence_is_idempotent_and_returns_the_original_record():
    store, _ = make_store()
    knowledge = record()
    original = evidence(locator="README.md:1", revision="commit-1")
    store.create_knowledge(knowledge, (original,))
    retry = evidence(
        evidence_id="retry-id",
        locator="README.md:1",
        revision="commit-1",
        captured_at="2026-10-19T00:00:00Z",
        reference_status=ReferenceStatus.AVAILABLE,
    )

    assert store.attach_evidence("project-a", knowledge.knowledge_id, retry) == original
    assert tuple(store.list_evidence("project-a", knowledge.knowledge_id)) == (original,)


def test_verification_updates_round_trip_without_changing_claim_or_source_identity():
    store, _ = make_store()
    original = record()
    source = evidence()
    store.create_knowledge(original, (source,))
    updated_record = replace(
        original,
        verification_status=VerificationStatus.STALE,
        updated_at="2026-10-19T00:00:00.000000Z",
        checked_at="2026-10-19T00:00:00.000000Z",
        checked_by="checker-label",
        check_note="Recheck requested.",
    )
    updated_evidence = replace(
        source,
        verification_status=VerificationStatus.CONFLICTING,
        checked_at="2026-10-19T00:01:00.000000Z",
        checked_by="checker-label",
        check_note="Conflicting source state.",
    )

    store.set_knowledge_verification("project-a", original.knowledge_id, updated_record)
    store.set_evidence_verification("project-a", original.knowledge_id, updated_evidence)

    assert store.get_knowledge("project-a", original.knowledge_id) == updated_record
    assert tuple(store.list_evidence("project-a", original.knowledge_id)) == (updated_evidence,)


def test_supersede_is_atomic_and_keeps_both_knowledge_records():
    store, connection = make_store()
    previous = record(knowledge_id="old")
    successor = record(knowledge_id="new", supersedes_id="old")
    store.create_knowledge(previous, (evidence(),))

    store.supersede_knowledge("project-a", "old", successor, (evidence(evidence_id="new-evidence"),))

    loaded_previous = store.get_knowledge("project-a", "old")
    assert loaded_previous.lifecycle_status is LifecycleStatus.SUPERSEDED
    assert loaded_previous.superseded_by_id == "new"
    assert loaded_previous.updated_at == successor.created_at
    assert store.get_knowledge("project-a", "new") == successor
    assert tuple(store.list_evidence("project-a", "old")) == (evidence(),)
    assert tuple(store.list_evidence("project-a", "new")) == (evidence(evidence_id="new-evidence"),)
    assert connection.rollbacks == 0


def test_supersede_failure_restores_previous_and_removes_staged_successor():
    store, connection = make_store()
    previous = record(knowledge_id="old")
    successor = record(knowledge_id="new", supersedes_id="old")
    store.create_knowledge(previous, (evidence(),))
    connection.fail_on = "UPDATE celestin_knowledge_records"

    with pytest.raises(RuntimeError, match="injected SQL"):
        store.supersede_knowledge("project-a", "old", successor, (evidence(evidence_id="new-evidence"),))

    assert store.get_knowledge("project-a", "old") == previous
    assert store.get_knowledge("project-a", "new") is None
    assert connection.rollbacks == 1


def test_missing_knowledge_is_not_fabricated_by_attach():
    store, connection = make_store()
    with pytest.raises(KnowledgeNotFound):
        store.attach_evidence("project-a", "missing", evidence())
    assert connection.evidence == {}
    assert connection.rollbacks == 1
