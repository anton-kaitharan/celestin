-- Project-scoped candidate revisions and their review/evidence graph.
-- Delivery receipts are intentionally absent: the delivery/revision equality
-- projection is not yet defined by an approved contract extension.

CREATE TABLE celestin_project_producers (
    workspace_id UUID NOT NULL,
    project_id   UUID NOT NULL,
    producer_id  TEXT COLLATE "C" NOT NULL CHECK (length(producer_id) > 0),
    registered_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (workspace_id, project_id, producer_id),
    FOREIGN KEY (workspace_id, project_id)
        REFERENCES celestin_projects(workspace_id, id) ON DELETE RESTRICT
);

CREATE TABLE celestin_project_source_namespaces (
    workspace_id    UUID NOT NULL,
    project_id      UUID NOT NULL,
    source_namespace TEXT COLLATE "C" NOT NULL
        CHECK (length(source_namespace) > 0),
    registered_at   TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (workspace_id, project_id, source_namespace),
    FOREIGN KEY (workspace_id, project_id)
        REFERENCES celestin_projects(workspace_id, id) ON DELETE RESTRICT
);

CREATE TABLE celestin_candidates (
    workspace_id UUID NOT NULL,
    project_id   UUID NOT NULL,
    producer_id  TEXT COLLATE "C" NOT NULL,
    candidate_id TEXT COLLATE "C" NOT NULL CHECK (length(candidate_id) > 0),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (workspace_id, project_id, producer_id, candidate_id),
    FOREIGN KEY (workspace_id, project_id, producer_id)
        REFERENCES celestin_project_producers(workspace_id, project_id, producer_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (workspace_id, project_id)
        REFERENCES celestin_projects(workspace_id, id) ON DELETE RESTRICT
);

CREATE TABLE celestin_candidate_revisions (
    workspace_id                    UUID NOT NULL,
    project_id                      UUID NOT NULL,
    producer_id                     TEXT COLLATE "C" NOT NULL,
    candidate_id                    TEXT COLLATE "C" NOT NULL,
    candidate_revision              INTEGER NOT NULL CHECK (candidate_revision > 0),
    category                        TEXT COLLATE "C" NOT NULL CHECK (length(category) > 0),
    certainty                       TEXT COLLATE "C" NOT NULL CHECK (length(certainty) > 0),
    claim                           TEXT NOT NULL,
    original_claim                  TEXT,
    candidate_verification_status   TEXT COLLATE "C" NOT NULL CHECK (
        candidate_verification_status IN ('unverified', 'verified', 'stale', 'conflicting')
    ),
    candidate_created_at            TIMESTAMPTZ NOT NULL,
    received_at                     TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    submitted_by_kind               TEXT COLLATE "C" NOT NULL CHECK (
        submitted_by_kind IN ('human', 'service')
    ),
    submitted_by_issuer             TEXT COLLATE "C" NOT NULL
        CHECK (length(submitted_by_issuer) > 0),
    submitted_by_subject            TEXT COLLATE "C" NOT NULL
        CHECK (length(submitted_by_subject) > 0),
    submitted_by_user_id            UUID,
    submitted_by_service_identity_id UUID,
    conflict_declarations            JSONB NOT NULL DEFAULT '[]'::jsonb
        CHECK (jsonb_typeof(conflict_declarations) = 'array'),
    PRIMARY KEY (workspace_id, project_id, producer_id, candidate_id, candidate_revision),
    FOREIGN KEY (workspace_id, project_id, producer_id, candidate_id)
        REFERENCES celestin_candidates(workspace_id, project_id, producer_id, candidate_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (workspace_id, project_id)
        REFERENCES celestin_projects(workspace_id, id) ON DELETE RESTRICT,
    FOREIGN KEY (workspace_id, submitted_by_user_id)
        REFERENCES celestin_workspace_memberships(workspace_id, user_id) ON DELETE RESTRICT,
    FOREIGN KEY (workspace_id, submitted_by_service_identity_id)
        REFERENCES celestin_service_identities(workspace_id, id) ON DELETE RESTRICT,
    CHECK (
        (submitted_by_kind = 'human'
            AND submitted_by_user_id IS NOT NULL
            AND submitted_by_service_identity_id IS NULL)
        OR
        (submitted_by_kind = 'service'
            AND submitted_by_user_id IS NULL
            AND submitted_by_service_identity_id IS NOT NULL)
    )
);

CREATE FUNCTION celestin_enforce_candidate_revision_sequence()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    expected_revision INTEGER;
BEGIN
    PERFORM 1
      FROM celestin_candidates
     WHERE workspace_id = NEW.workspace_id
       AND project_id = NEW.project_id
       AND producer_id = NEW.producer_id
       AND candidate_id = NEW.candidate_id
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'candidate identity is not registered';
    END IF;

    SELECT COALESCE(MAX(candidate_revision), 0) + 1
      INTO expected_revision
      FROM celestin_candidate_revisions
     WHERE workspace_id = NEW.workspace_id
       AND project_id = NEW.project_id
       AND producer_id = NEW.producer_id
       AND candidate_id = NEW.candidate_id;
    IF NEW.candidate_revision <> expected_revision THEN
        RAISE EXCEPTION 'candidate revision must be the next sequential revision';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER celestin_candidate_revision_sequence
BEFORE INSERT ON celestin_candidate_revisions
FOR EACH ROW EXECUTE FUNCTION celestin_enforce_candidate_revision_sequence();

CREATE TABLE celestin_candidate_review_events (
    workspace_id       UUID NOT NULL,
    project_id         UUID NOT NULL,
    producer_id        TEXT COLLATE "C" NOT NULL,
    candidate_id       TEXT COLLATE "C" NOT NULL,
    candidate_revision INTEGER NOT NULL,
    event_number       INTEGER NOT NULL DEFAULT 1 CHECK (event_number > 0),
    event_type         TEXT COLLATE "C" NOT NULL CHECK (
        event_type IN ('approved', 'rejected', 'revoked')
    ),
    reviewer_user_id   UUID NOT NULL,
    event_at           TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    review_note        TEXT,
    PRIMARY KEY (
        workspace_id, project_id, producer_id, candidate_id,
        candidate_revision, event_number
    ),
    FOREIGN KEY (
        workspace_id, project_id, producer_id, candidate_id, candidate_revision
    ) REFERENCES celestin_candidate_revisions(
        workspace_id, project_id, producer_id, candidate_id, candidate_revision
    ) ON DELETE RESTRICT,
    FOREIGN KEY (project_id, reviewer_user_id)
        REFERENCES celestin_project_memberships(project_id, user_id) ON DELETE RESTRICT
);

CREATE INDEX idx_celestin_candidate_review_latest
    ON celestin_candidate_review_events (
        workspace_id, project_id, producer_id, candidate_id,
        candidate_revision, event_number DESC
    );

CREATE FUNCTION celestin_validate_candidate_review_event()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    previous_number INTEGER;
    previous_type   TEXT;
    submitter_user  UUID;
BEGIN
    SELECT submitted_by_user_id
      INTO submitter_user
      FROM celestin_candidate_revisions
     WHERE workspace_id = NEW.workspace_id
       AND project_id = NEW.project_id
       AND producer_id = NEW.producer_id
       AND candidate_id = NEW.candidate_id
       AND candidate_revision = NEW.candidate_revision
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'candidate revision does not exist';
    END IF;

    SELECT event_number, event_type
      INTO previous_number, previous_type
      FROM celestin_candidate_review_events
     WHERE workspace_id = NEW.workspace_id
       AND project_id = NEW.project_id
       AND producer_id = NEW.producer_id
       AND candidate_id = NEW.candidate_id
       AND candidate_revision = NEW.candidate_revision
     ORDER BY event_number DESC
     LIMIT 1;

    NEW.event_number := COALESCE(previous_number, 0) + 1;
    IF previous_number IS NULL THEN
        IF NEW.event_type NOT IN ('approved', 'rejected') THEN
            RAISE EXCEPTION 'first review event must approve or reject';
        END IF;
    ELSIF previous_type <> 'approved' OR NEW.event_type <> 'revoked' THEN
        RAISE EXCEPTION 'only an approved revision can be revoked';
    END IF;

    IF NEW.event_type = 'approved' AND submitter_user = NEW.reviewer_user_id THEN
        RAISE EXCEPTION 'submitter cannot approve their own candidate revision';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER celestin_candidate_review_transition
BEFORE INSERT ON celestin_candidate_review_events
FOR EACH ROW EXECUTE FUNCTION celestin_validate_candidate_review_event();

CREATE TABLE celestin_candidate_evidence_records (
    workspace_id           UUID NOT NULL,
    project_id             UUID NOT NULL,
    evidence_record_id      UUID NOT NULL,
    evidence_type           TEXT COLLATE "C" NOT NULL CHECK (length(evidence_type) > 0),
    source_namespace        TEXT COLLATE "C" NOT NULL,
    source_id               TEXT COLLATE "C" NOT NULL CHECK (length(source_id) > 0),
    source_revision         TEXT COLLATE "C",
    locator                 TEXT,
    content_hash            TEXT COLLATE "C",
    received_at             TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (workspace_id, project_id, evidence_record_id),
    FOREIGN KEY (workspace_id, project_id, source_namespace)
        REFERENCES celestin_project_source_namespaces(
            workspace_id, project_id, source_namespace
        ) ON DELETE RESTRICT,
    FOREIGN KEY (workspace_id, project_id)
        REFERENCES celestin_projects(workspace_id, id) ON DELETE RESTRICT,
    CHECK (
        content_hash IS NULL OR
        content_hash ~ '^sha256:[0-9a-f]{64}$'
    )
);

CREATE TABLE celestin_candidate_occurrences (
    workspace_id              UUID NOT NULL,
    project_id                UUID NOT NULL,
    producer_id               TEXT COLLATE "C" NOT NULL,
    candidate_id              TEXT COLLATE "C" NOT NULL,
    candidate_revision        INTEGER NOT NULL,
    ordinal                   INTEGER NOT NULL CHECK (ordinal >= 0),
    evidence_record_id        UUID NOT NULL,
    source_occurred_at        TIMESTAMPTZ,
    PRIMARY KEY (
        workspace_id, project_id, producer_id, candidate_id,
        candidate_revision, ordinal
    ),
    FOREIGN KEY (
        workspace_id, project_id, producer_id, candidate_id, candidate_revision
    ) REFERENCES celestin_candidate_revisions(
        workspace_id, project_id, producer_id, candidate_id, candidate_revision
    ) ON DELETE RESTRICT,
    FOREIGN KEY (workspace_id, project_id, evidence_record_id)
        REFERENCES celestin_candidate_evidence_records(
            workspace_id, project_id, evidence_record_id
        ) ON DELETE RESTRICT
);

CREATE FUNCTION celestin_enforce_occurrence_order()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    expected_ordinal INTEGER;
BEGIN
    PERFORM 1
      FROM celestin_candidate_revisions
     WHERE workspace_id = NEW.workspace_id
       AND project_id = NEW.project_id
       AND producer_id = NEW.producer_id
       AND candidate_id = NEW.candidate_id
       AND candidate_revision = NEW.candidate_revision
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'candidate revision does not exist';
    END IF;

    IF EXISTS (
        SELECT 1 FROM celestin_candidate_review_events
         WHERE workspace_id = NEW.workspace_id
           AND project_id = NEW.project_id
           AND producer_id = NEW.producer_id
           AND candidate_id = NEW.candidate_id
           AND candidate_revision = NEW.candidate_revision
    ) THEN
        RAISE EXCEPTION 'occurrences cannot be added after review begins';
    END IF;

    SELECT COALESCE(MAX(ordinal), -1) + 1
      INTO expected_ordinal
      FROM celestin_candidate_occurrences
     WHERE workspace_id = NEW.workspace_id
       AND project_id = NEW.project_id
       AND producer_id = NEW.producer_id
       AND candidate_id = NEW.candidate_id
       AND candidate_revision = NEW.candidate_revision;
    IF NEW.ordinal <> expected_ordinal THEN
        RAISE EXCEPTION 'occurrence ordinal must be contiguous from zero';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER celestin_candidate_occurrence_order
BEFORE INSERT ON celestin_candidate_occurrences
FOR EACH ROW EXECUTE FUNCTION celestin_enforce_occurrence_order();

CREATE TABLE celestin_candidate_conversions (
    workspace_id        UUID NOT NULL,
    project_id          UUID NOT NULL,
    producer_id         TEXT COLLATE "C" NOT NULL,
    candidate_id        TEXT COLLATE "C" NOT NULL,
    candidate_revision  INTEGER NOT NULL,
    knowledge_id        TEXT COLLATE "C" NOT NULL,
    payload_hash_version TEXT COLLATE "C" NOT NULL CHECK (
        payload_hash_version = 'smic-conversion-payload-v1+jcs-rfc8785+sha256'
    ),
    payload_hash        TEXT COLLATE "C" NOT NULL CHECK (
        payload_hash ~ '^[0-9a-f]{64}$'
    ),
    converted_by_kind   TEXT COLLATE "C" NOT NULL CHECK (
        converted_by_kind IN ('human', 'service')
    ),
    converted_by_issuer TEXT COLLATE "C" NOT NULL
        CHECK (length(converted_by_issuer) > 0),
    converted_by_subject TEXT COLLATE "C" NOT NULL
        CHECK (length(converted_by_subject) > 0),
    converted_at        TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (
        workspace_id, project_id, producer_id, candidate_id, candidate_revision
    ),
    UNIQUE (workspace_id, project_id, knowledge_id),
    FOREIGN KEY (
        workspace_id, project_id, producer_id, candidate_id, candidate_revision
    ) REFERENCES celestin_candidate_revisions(
        workspace_id, project_id, producer_id, candidate_id, candidate_revision
    ) ON DELETE RESTRICT,
    FOREIGN KEY (workspace_id, project_id, knowledge_id)
        REFERENCES celestin_knowledge_records(
            workspace_id, project_id, knowledge_id
        ) ON DELETE RESTRICT
);

CREATE FUNCTION celestin_require_approved_candidate_conversion()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    latest_review   TEXT;
    verification    TEXT;
BEGIN
    SELECT candidate_verification_status
      INTO verification
      FROM celestin_candidate_revisions
     WHERE workspace_id = NEW.workspace_id
       AND project_id = NEW.project_id
       AND producer_id = NEW.producer_id
       AND candidate_id = NEW.candidate_id
       AND candidate_revision = NEW.candidate_revision
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'candidate revision does not exist';
    END IF;

    SELECT event_type INTO latest_review
      FROM celestin_candidate_review_events
     WHERE workspace_id = NEW.workspace_id
       AND project_id = NEW.project_id
       AND producer_id = NEW.producer_id
       AND candidate_id = NEW.candidate_id
       AND candidate_revision = NEW.candidate_revision
     ORDER BY event_number DESC
     LIMIT 1;
    IF latest_review IS DISTINCT FROM 'approved' THEN
        RAISE EXCEPTION 'candidate revision is not currently approved';
    END IF;
    IF verification <> 'unverified' THEN
        RAISE EXCEPTION 'candidate verification status is not convertible';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER celestin_candidate_conversion_approval
BEFORE INSERT ON celestin_candidate_conversions
FOR EACH ROW EXECUTE FUNCTION celestin_require_approved_candidate_conversion();

CREATE FUNCTION celestin_reject_immutable_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'immutable shared-memory history cannot be updated or deleted';
END;
$$;

CREATE TRIGGER celestin_candidate_identity_immutable
BEFORE UPDATE OR DELETE ON celestin_candidates
FOR EACH ROW EXECUTE FUNCTION celestin_reject_immutable_mutation();

CREATE TRIGGER celestin_candidate_revision_immutable
BEFORE UPDATE OR DELETE ON celestin_candidate_revisions
FOR EACH ROW EXECUTE FUNCTION celestin_reject_immutable_mutation();

CREATE TRIGGER celestin_candidate_review_immutable
BEFORE UPDATE OR DELETE ON celestin_candidate_review_events
FOR EACH ROW EXECUTE FUNCTION celestin_reject_immutable_mutation();

CREATE TRIGGER celestin_candidate_evidence_immutable
BEFORE UPDATE OR DELETE ON celestin_candidate_evidence_records
FOR EACH ROW EXECUTE FUNCTION celestin_reject_immutable_mutation();

CREATE TRIGGER celestin_candidate_occurrence_immutable
BEFORE UPDATE OR DELETE ON celestin_candidate_occurrences
FOR EACH ROW EXECUTE FUNCTION celestin_reject_immutable_mutation();

CREATE TRIGGER celestin_candidate_conversion_immutable
BEFORE UPDATE OR DELETE ON celestin_candidate_conversions
FOR EACH ROW EXECUTE FUNCTION celestin_reject_immutable_mutation();

CREATE FUNCTION celestin_request_scope_matches(row_workspace UUID, row_project UUID)
RETURNS BOOLEAN LANGUAGE SQL STABLE PARALLEL SAFE AS $$
    SELECT row_workspace = NULLIF(current_setting('celestin.workspace_id', true), '')::uuid
       AND row_project = NULLIF(current_setting('celestin.project_id', true), '')::uuid
$$;

ALTER TABLE celestin_knowledge_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE celestin_knowledge_records FORCE ROW LEVEL SECURITY;
CREATE POLICY celestin_knowledge_request_scope ON celestin_knowledge_records
    USING (celestin_request_scope_matches(workspace_id, project_id))
    WITH CHECK (celestin_request_scope_matches(workspace_id, project_id));

ALTER TABLE celestin_evidence_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE celestin_evidence_records FORCE ROW LEVEL SECURITY;
CREATE POLICY celestin_evidence_request_scope ON celestin_evidence_records
    USING (celestin_request_scope_matches(workspace_id, project_id))
    WITH CHECK (celestin_request_scope_matches(workspace_id, project_id));

ALTER TABLE celestin_project_producers ENABLE ROW LEVEL SECURITY;
ALTER TABLE celestin_project_producers FORCE ROW LEVEL SECURITY;
CREATE POLICY celestin_producer_request_scope ON celestin_project_producers
    USING (celestin_request_scope_matches(workspace_id, project_id))
    WITH CHECK (celestin_request_scope_matches(workspace_id, project_id));

ALTER TABLE celestin_project_source_namespaces ENABLE ROW LEVEL SECURITY;
ALTER TABLE celestin_project_source_namespaces FORCE ROW LEVEL SECURITY;
CREATE POLICY celestin_source_namespace_request_scope ON celestin_project_source_namespaces
    USING (celestin_request_scope_matches(workspace_id, project_id))
    WITH CHECK (celestin_request_scope_matches(workspace_id, project_id));

ALTER TABLE celestin_candidates ENABLE ROW LEVEL SECURITY;
ALTER TABLE celestin_candidates FORCE ROW LEVEL SECURITY;
CREATE POLICY celestin_candidate_request_scope ON celestin_candidates
    USING (celestin_request_scope_matches(workspace_id, project_id))
    WITH CHECK (celestin_request_scope_matches(workspace_id, project_id));

ALTER TABLE celestin_candidate_revisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE celestin_candidate_revisions FORCE ROW LEVEL SECURITY;
CREATE POLICY celestin_candidate_revision_request_scope ON celestin_candidate_revisions
    USING (celestin_request_scope_matches(workspace_id, project_id))
    WITH CHECK (celestin_request_scope_matches(workspace_id, project_id));

ALTER TABLE celestin_candidate_review_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE celestin_candidate_review_events FORCE ROW LEVEL SECURITY;
CREATE POLICY celestin_review_request_scope ON celestin_candidate_review_events
    USING (celestin_request_scope_matches(workspace_id, project_id))
    WITH CHECK (celestin_request_scope_matches(workspace_id, project_id));

ALTER TABLE celestin_candidate_evidence_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE celestin_candidate_evidence_records FORCE ROW LEVEL SECURITY;
CREATE POLICY celestin_candidate_evidence_request_scope ON celestin_candidate_evidence_records
    USING (celestin_request_scope_matches(workspace_id, project_id))
    WITH CHECK (celestin_request_scope_matches(workspace_id, project_id));

ALTER TABLE celestin_candidate_occurrences ENABLE ROW LEVEL SECURITY;
ALTER TABLE celestin_candidate_occurrences FORCE ROW LEVEL SECURITY;
CREATE POLICY celestin_occurrence_request_scope ON celestin_candidate_occurrences
    USING (celestin_request_scope_matches(workspace_id, project_id))
    WITH CHECK (celestin_request_scope_matches(workspace_id, project_id));

ALTER TABLE celestin_candidate_conversions ENABLE ROW LEVEL SECURITY;
ALTER TABLE celestin_candidate_conversions FORCE ROW LEVEL SECURITY;
CREATE POLICY celestin_conversion_request_scope ON celestin_candidate_conversions
    USING (celestin_request_scope_matches(workspace_id, project_id))
    WITH CHECK (celestin_request_scope_matches(workspace_id, project_id));

REVOKE ALL ON TABLE
    celestin_knowledge_records,
    celestin_evidence_records,
    celestin_project_producers,
    celestin_project_source_namespaces,
    celestin_candidates,
    celestin_candidate_revisions,
    celestin_candidate_review_events,
    celestin_candidate_evidence_records,
    celestin_candidate_occurrences,
    celestin_candidate_conversions
FROM PUBLIC;
