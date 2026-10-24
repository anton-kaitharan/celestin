-- Persist the current storage-neutral EvidenceLinkedMemoryStore domain port.
-- This migration does not add Capture conversion, producer delivery receipts,
-- immutable candidate revisions, review events, or occurrence associations.

CREATE TABLE celestin_knowledge_records (
    workspace_id          UUID NOT NULL,
    project_id            UUID NOT NULL,
    knowledge_id          TEXT NOT NULL CHECK (length(knowledge_id) > 0),
    knowledge_type        TEXT NOT NULL CHECK (knowledge_type IN (
        'decision', 'discovery', 'bug_fix', 'implementation_finding',
        'completed_work', 'requirement', 'open_question'
    )),
    claim                  TEXT NOT NULL,
    created_at             TIMESTAMPTZ NOT NULL,
    updated_at             TIMESTAMPTZ NOT NULL,
    verification_status    TEXT NOT NULL CHECK (verification_status IN (
        'unverified', 'verified', 'stale', 'conflicting'
    )),
    lifecycle_status       TEXT NOT NULL CHECK (lifecycle_status IN (
        'current', 'superseded', 'archived'
    )),
    supersedes_id          TEXT,
    superseded_by_id       TEXT,
    checked_at             TIMESTAMPTZ,
    checked_by             TEXT,
    check_note             TEXT,
    capture_provenance     JSONB,
    PRIMARY KEY (project_id, knowledge_id),
    UNIQUE (workspace_id, project_id, knowledge_id),
    FOREIGN KEY (workspace_id, project_id)
        REFERENCES celestin_projects(workspace_id, id) ON DELETE RESTRICT,
    FOREIGN KEY (workspace_id, project_id, supersedes_id)
        REFERENCES celestin_knowledge_records(workspace_id, project_id, knowledge_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (workspace_id, project_id, superseded_by_id)
        REFERENCES celestin_knowledge_records(workspace_id, project_id, knowledge_id)
        ON DELETE RESTRICT,
    CHECK (supersedes_id IS NULL OR supersedes_id <> knowledge_id),
    CHECK (superseded_by_id IS NULL OR superseded_by_id <> knowledge_id),
    CHECK (lifecycle_status <> 'superseded' OR superseded_by_id IS NOT NULL),
    CHECK (lifecycle_status <> 'current' OR superseded_by_id IS NULL)
);

CREATE UNIQUE INDEX idx_celestin_knowledge_one_successor
    ON celestin_knowledge_records (workspace_id, project_id, supersedes_id)
    WHERE supersedes_id IS NOT NULL;

CREATE INDEX idx_celestin_knowledge_project_lifecycle
    ON celestin_knowledge_records (workspace_id, project_id, lifecycle_status);

CREATE TABLE celestin_evidence_records (
    workspace_id          UUID NOT NULL,
    project_id            UUID NOT NULL,
    knowledge_id          TEXT NOT NULL,
    evidence_id           TEXT NOT NULL CHECK (length(evidence_id) > 0),
    evidence_position     INTEGER NOT NULL CHECK (evidence_position >= 0),
    evidence_type         TEXT NOT NULL CHECK (evidence_type IN (
        'message', 'repository_file', 'git_commit', 'test_result', 'document', 'other'
    )),
    source_id             TEXT NOT NULL CHECK (length(btrim(source_id)) > 0),
    locator               TEXT,
    revision              TEXT,
    content_hash          TEXT,
    captured_at           TIMESTAMPTZ NOT NULL,
    reference_status      TEXT NOT NULL CHECK (reference_status IN (
        'available', 'unresolved', 'missing'
    )),
    verification_status   TEXT NOT NULL CHECK (verification_status IN (
        'unverified', 'verified', 'stale', 'conflicting'
    )),
    checked_at            TIMESTAMPTZ,
    checked_by            TEXT,
    check_note            TEXT,
    PRIMARY KEY (project_id, evidence_id),
    UNIQUE (workspace_id, project_id, knowledge_id, evidence_id),
    UNIQUE (workspace_id, project_id, knowledge_id, evidence_position),
    FOREIGN KEY (workspace_id, project_id, knowledge_id)
        REFERENCES celestin_knowledge_records(workspace_id, project_id, knowledge_id)
        ON DELETE RESTRICT
);
