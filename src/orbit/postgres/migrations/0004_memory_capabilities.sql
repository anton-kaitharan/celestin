-- Add explicit, default-deny capabilities required by the v0.3 hosted memory
-- workflow. Candidate approval is available only to human project members.
ALTER TABLE celestin_project_memberships
    ADD COLUMN can_approve_candidates BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN can_convert_memory BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN can_read_history BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN can_read_audit BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE celestin_service_project_grants
    ADD COLUMN can_convert_memory BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN can_read_history BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN can_read_audit BOOLEAN NOT NULL DEFAULT FALSE;
