-- Extend request-local tenant isolation to the established message and
-- decision tables. Hosted requests set these values only after resolving the
-- current principal, workspace, project, and action authorization.

ALTER TABLE chat_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE chat_messages FORCE ROW LEVEL SECURITY;
CREATE POLICY celestin_chat_message_request_scope ON chat_messages
    USING (celestin_request_scope_matches(workspace_id, project_id))
    WITH CHECK (celestin_request_scope_matches(workspace_id, project_id));

ALTER TABLE project_memory ENABLE ROW LEVEL SECURITY;
ALTER TABLE project_memory FORCE ROW LEVEL SECURITY;
CREATE POLICY celestin_project_memory_request_scope ON project_memory
    USING (celestin_request_scope_matches(workspace_id, project_id))
    WITH CHECK (celestin_request_scope_matches(workspace_id, project_id));

REVOKE ALL ON TABLE chat_messages, project_memory FROM PUBLIC;
