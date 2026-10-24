-- Bind hosted tenant RLS to a project-specific PostgreSQL login identity.
-- Roles are provisioned out of band; migrations never create login roles.

DO $$
DECLARE
    required_role TEXT;
    role_row RECORD;
BEGIN
    FOREACH required_role IN ARRAY ARRAY[
        'celestin_project_runtime',
        'celestin_authorization_reader',
        'celestin_authorization_admin'
    ] LOOP
        SELECT rolcanlogin, rolsuper, rolbypassrls, rolcreaterole,
               rolcreatedb, rolreplication, rolinherit, oid
          INTO role_row
          FROM pg_catalog.pg_roles
         WHERE rolname = required_role;
        IF NOT FOUND OR role_row.rolcanlogin OR role_row.rolsuper
           OR role_row.rolbypassrls OR role_row.rolcreaterole
           OR role_row.rolcreatedb OR role_row.rolreplication
           OR NOT role_row.rolinherit
           OR EXISTS (
               SELECT 1 FROM pg_catalog.pg_auth_members
                WHERE member = role_row.oid
           ) THEN
            RAISE EXCEPTION 'required Celestin group role is missing or unsafe: %',
                required_role;
        END IF;
    END LOOP;
END;
$$;

CREATE SCHEMA celestin_private;
REVOKE ALL ON SCHEMA celestin_private FROM PUBLIC;

CREATE TABLE celestin_private.project_runtime_scopes (
    login_role_name NAME PRIMARY KEY,
    workspace_id    UUID NOT NULL,
    project_id      UUID NOT NULL,
    registered_at   TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (workspace_id, project_id)
        REFERENCES public.celestin_projects(workspace_id, id) ON DELETE RESTRICT
);

ALTER TABLE celestin_private.project_runtime_scopes ENABLE ROW LEVEL SECURITY;
CREATE POLICY celestin_project_scope_own_login
    ON celestin_private.project_runtime_scopes
    FOR SELECT TO celestin_project_runtime
    USING (login_role_name = session_user::name);
CREATE POLICY celestin_project_scope_admin_all
    ON celestin_private.project_runtime_scopes
    FOR ALL TO celestin_authorization_admin
    USING (true) WITH CHECK (true);

GRANT USAGE ON SCHEMA celestin_private TO celestin_project_runtime;
GRANT SELECT ON celestin_private.project_runtime_scopes TO celestin_project_runtime;
GRANT USAGE ON SCHEMA celestin_private TO celestin_authorization_admin;
GRANT SELECT, INSERT, DELETE
    ON celestin_private.project_runtime_scopes TO celestin_authorization_admin;

CREATE OR REPLACE FUNCTION public.celestin_request_scope_matches(
    row_workspace UUID,
    row_project UUID
)
RETURNS BOOLEAN
LANGUAGE SQL
STABLE
SET search_path = pg_catalog, celestin_private
AS $$
    SELECT EXISTS (
        SELECT 1
          FROM celestin_private.project_runtime_scopes AS scope
         WHERE scope.login_role_name = session_user::name
           AND scope.workspace_id = row_workspace
           AND scope.project_id = row_project
    )
$$;

CREATE OR REPLACE FUNCTION public.celestin_request_workspace_matches(row_workspace UUID)
RETURNS BOOLEAN
LANGUAGE SQL
STABLE
SET search_path = pg_catalog, celestin_private
AS $$
    SELECT EXISTS (
        SELECT 1
          FROM celestin_private.project_runtime_scopes AS scope
         WHERE scope.login_role_name = session_user::name
           AND scope.workspace_id = row_workspace
    )
$$;

REVOKE ALL ON FUNCTION public.celestin_request_scope_matches(UUID, UUID) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.celestin_request_workspace_matches(UUID) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.celestin_request_scope_matches(UUID, UUID)
    TO celestin_project_runtime;
GRANT EXECUTE ON FUNCTION public.celestin_request_workspace_matches(UUID)
    TO celestin_project_runtime;

-- The database builds keys so request code and table triggers cannot drift.
CREATE FUNCTION public.celestin_lock_authorization_request(
    target_workspace UUID,
    principal_kind TEXT,
    principal_issuer TEXT,
    principal_subject TEXT
)
RETURNS VOID
LANGUAGE plpgsql
AS $$
DECLARE
    lock_key TEXT;
BEGIN
    FOR lock_key IN
        SELECT DISTINCT key_value
          FROM (VALUES
              (jsonb_build_array('workspace', target_workspace)::text),
              (jsonb_build_array(
                  'principal', principal_kind, principal_issuer, principal_subject
              )::text)
          ) AS keys(key_value)
         ORDER BY key_value
    LOOP
        PERFORM pg_catalog.pg_advisory_xact_lock_shared(
            pg_catalog.hashtextextended('celestin-auth-v1:' || lock_key, 0)
        );
    END LOOP;
END;
$$;

CREATE FUNCTION public.celestin_lock_authorization_write(
    target_workspace UUID,
    principal_kind TEXT,
    principal_issuer TEXT,
    principal_subject TEXT
)
RETURNS VOID
LANGUAGE plpgsql
AS $$
DECLARE
    lock_key TEXT;
BEGIN
    FOR lock_key IN
        SELECT DISTINCT key_value
          FROM (
              SELECT jsonb_build_array('workspace', target_workspace)::text AS key_value
              UNION ALL
              SELECT jsonb_build_array(
                  'principal', principal_kind, principal_issuer, principal_subject
              )::text
               WHERE principal_kind IS NOT NULL
          ) AS keys
         ORDER BY key_value
    LOOP
        PERFORM pg_catalog.pg_advisory_xact_lock(
            pg_catalog.hashtextextended('celestin-auth-v1:' || lock_key, 0)
        );
    END LOOP;
END;
$$;

CREATE FUNCTION public.celestin_lock_workspace_authorization_write(
    target_workspace UUID
)
RETURNS VOID
LANGUAGE SQL
AS $$
    SELECT pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(
            'celestin-auth-v1:' || jsonb_build_array('workspace', target_workspace)::text,
            0
        )
    )
$$;

CREATE FUNCTION public.celestin_lock_principal_authorization_write(
    principal_kind TEXT,
    principal_issuer TEXT,
    principal_subject TEXT
)
RETURNS VOID
LANGUAGE SQL
AS $$
    SELECT pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(
            'celestin-auth-v1:' || jsonb_build_array(
                'principal', principal_kind, principal_issuer, principal_subject
            )::text,
            0
        )
    )
$$;

CREATE FUNCTION public.celestin_lock_principal_transition_write(
    old_issuer TEXT,
    old_subject TEXT,
    new_issuer TEXT,
    new_subject TEXT
)
RETURNS VOID
LANGUAGE plpgsql
AS $$
DECLARE
    lock_key TEXT;
BEGIN
    FOR lock_key IN
        SELECT DISTINCT jsonb_build_array('principal', 'human', issuer, subject)::text
          FROM (VALUES (old_issuer, old_subject), (new_issuer, new_subject)) AS ids(issuer, subject)
         WHERE issuer IS NOT NULL AND subject IS NOT NULL
         ORDER BY 1
    LOOP
        PERFORM pg_catalog.pg_advisory_xact_lock(
            pg_catalog.hashtextextended('celestin-auth-v1:' || lock_key, 0)
        );
    END LOOP;
END;
$$;

REVOKE ALL ON FUNCTION public.celestin_lock_authorization_request(UUID, TEXT, TEXT, TEXT)
    FROM PUBLIC;
REVOKE ALL ON FUNCTION public.celestin_lock_authorization_write(UUID, TEXT, TEXT, TEXT)
    FROM PUBLIC;
REVOKE ALL ON FUNCTION public.celestin_lock_workspace_authorization_write(UUID)
    FROM PUBLIC;
REVOKE ALL ON FUNCTION public.celestin_lock_principal_authorization_write(TEXT, TEXT, TEXT)
    FROM PUBLIC;
REVOKE ALL ON FUNCTION public.celestin_lock_principal_transition_write(TEXT, TEXT, TEXT, TEXT)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.celestin_lock_authorization_request(UUID, TEXT, TEXT, TEXT)
    TO celestin_project_runtime, celestin_authorization_reader;
GRANT EXECUTE ON FUNCTION public.celestin_lock_authorization_write(UUID, TEXT, TEXT, TEXT)
    TO celestin_authorization_admin;
GRANT EXECUTE ON FUNCTION public.celestin_lock_workspace_authorization_write(UUID)
    TO celestin_authorization_admin;
GRANT EXECUTE ON FUNCTION public.celestin_lock_principal_authorization_write(TEXT, TEXT, TEXT)
    TO celestin_authorization_admin;
GRANT EXECUTE ON FUNCTION public.celestin_lock_principal_transition_write(TEXT, TEXT, TEXT, TEXT)
    TO celestin_authorization_admin;

-- Schema and authorization administration uses the migration-injected DSN,
-- never the request runtime pools. Trigger lock functions use a fixed path.
CREATE FUNCTION public.celestin_lock_workspace_auth_row()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    old_workspace UUID;
    new_workspace UUID;
    target_workspace UUID;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        IF TG_TABLE_NAME = 'celestin_workspaces' THEN
            old_workspace := (to_jsonb(OLD)->>'id')::uuid;
        ELSE
            old_workspace := (to_jsonb(OLD)->>'workspace_id')::uuid;
        END IF;
    END IF;
    IF TG_OP <> 'DELETE' THEN
        IF TG_TABLE_NAME = 'celestin_workspaces' THEN
            new_workspace := (to_jsonb(NEW)->>'id')::uuid;
        ELSE
            new_workspace := (to_jsonb(NEW)->>'workspace_id')::uuid;
        END IF;
    END IF;
    FOR target_workspace IN
        SELECT DISTINCT workspace_id
          FROM unnest(ARRAY[old_workspace, new_workspace]) AS ids(workspace_id)
         WHERE workspace_id IS NOT NULL
         ORDER BY workspace_id::text
    LOOP
        PERFORM public.celestin_lock_workspace_authorization_write(target_workspace);
    END LOOP;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;

CREATE FUNCTION public.celestin_lock_human_identity_auth_row()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    PERFORM public.celestin_lock_principal_transition_write(
        OLD.issuer, OLD.subject, NEW.issuer, NEW.subject
    );
    RETURN NEW;
END;
$$;

REVOKE ALL ON FUNCTION public.celestin_lock_workspace_auth_row() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.celestin_lock_human_identity_auth_row() FROM PUBLIC;

CREATE TRIGGER celestin_workspace_auth_lock
BEFORE INSERT OR UPDATE OR DELETE ON public.celestin_workspaces
FOR EACH ROW EXECUTE FUNCTION public.celestin_lock_workspace_auth_row();
CREATE TRIGGER celestin_project_auth_lock
BEFORE INSERT OR UPDATE OR DELETE ON public.celestin_projects
FOR EACH ROW EXECUTE FUNCTION public.celestin_lock_workspace_auth_row();
CREATE TRIGGER celestin_workspace_membership_auth_lock
BEFORE INSERT OR UPDATE OR DELETE ON public.celestin_workspace_memberships
FOR EACH ROW EXECUTE FUNCTION public.celestin_lock_workspace_auth_row();
CREATE TRIGGER celestin_project_membership_auth_lock
BEFORE INSERT OR UPDATE OR DELETE ON public.celestin_project_memberships
FOR EACH ROW EXECUTE FUNCTION public.celestin_lock_workspace_auth_row();
CREATE TRIGGER celestin_service_identity_auth_lock
BEFORE INSERT OR UPDATE OR DELETE ON public.celestin_service_identities
FOR EACH ROW EXECUTE FUNCTION public.celestin_lock_workspace_auth_row();
CREATE TRIGGER celestin_service_grant_auth_lock
BEFORE INSERT OR UPDATE OR DELETE ON public.celestin_service_project_grants
FOR EACH ROW EXECUTE FUNCTION public.celestin_lock_workspace_auth_row();
CREATE TRIGGER celestin_project_scope_auth_lock
BEFORE INSERT OR UPDATE OR DELETE ON celestin_private.project_runtime_scopes
FOR EACH ROW EXECUTE FUNCTION public.celestin_lock_workspace_auth_row();
CREATE TRIGGER celestin_user_identity_auth_lock
BEFORE UPDATE OF status, issuer, subject ON public.celestin_users
FOR EACH ROW EXECUTE FUNCTION public.celestin_lock_human_identity_auth_row();

-- Remove the legacy caller-settable-GUC policies before installing the
-- project-login policies. PostgreSQL combines permissive policies with OR,
-- so leaving even one legacy policy would preserve its cross-tenant bypass.
DO $$
DECLARE
    table_name TEXT;
    legacy_policy_name TEXT;
BEGIN
    FOR table_name, legacy_policy_name IN
        SELECT * FROM (VALUES
            ('chat_messages', 'celestin_chat_message_request_scope'),
            ('project_memory', 'celestin_project_memory_request_scope'),
            ('celestin_knowledge_records', 'celestin_knowledge_request_scope'),
            ('celestin_evidence_records', 'celestin_evidence_request_scope'),
            ('celestin_project_producers', 'celestin_producer_request_scope'),
            ('celestin_project_source_namespaces', 'celestin_source_namespace_request_scope'),
            ('celestin_candidates', 'celestin_candidate_request_scope'),
            ('celestin_candidate_revisions', 'celestin_candidate_revision_request_scope'),
            ('celestin_candidate_review_events', 'celestin_review_request_scope'),
            ('celestin_candidate_evidence_records', 'celestin_candidate_evidence_request_scope'),
            ('celestin_candidate_occurrences', 'celestin_occurrence_request_scope'),
            ('celestin_candidate_conversions', 'celestin_conversion_request_scope')
        ) AS legacy(table_name, policy_name)
    LOOP
        EXECUTE format(
            'DROP POLICY IF EXISTS %I ON public.%I',
            legacy_policy_name, table_name
        );
        EXECUTE format(
            'CREATE POLICY celestin_project_request_scope ON public.%I '
            'FOR ALL TO celestin_project_runtime '
            'USING (public.celestin_request_scope_matches(workspace_id, project_id)) '
            'WITH CHECK (public.celestin_request_scope_matches(workspace_id, project_id))',
            table_name
        );
    END LOOP;
END;
$$;

DO $$
DECLARE
    table_name TEXT;
    auth_tables TEXT[] := ARRAY[
        'celestin_users', 'celestin_workspaces', 'celestin_workspace_memberships',
        'celestin_projects', 'celestin_project_memberships',
        'celestin_service_identities', 'celestin_service_project_grants'
    ];
BEGIN
    FOREACH table_name IN ARRAY auth_tables LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', table_name);
        EXECUTE format(
            'CREATE POLICY celestin_authorization_reader_select ON public.%I '
            'FOR SELECT TO celestin_authorization_reader USING (true)', table_name
        );
        EXECUTE format(
            'CREATE POLICY celestin_authorization_admin_all ON public.%I '
            'FOR ALL TO celestin_authorization_admin USING (true) WITH CHECK (true)', table_name
        );
    END LOOP;
END;
$$;

CREATE POLICY celestin_project_workspace_read ON public.celestin_workspaces
    FOR SELECT TO celestin_project_runtime
    USING (public.celestin_request_workspace_matches(id));
CREATE POLICY celestin_project_project_read ON public.celestin_projects
    FOR SELECT TO celestin_project_runtime
    USING (public.celestin_request_scope_matches(workspace_id, id));
CREATE POLICY celestin_project_workspace_membership_read
    ON public.celestin_workspace_memberships
    FOR SELECT TO celestin_project_runtime
    USING (
        public.celestin_request_workspace_matches(workspace_id)
        AND EXISTS (
            SELECT 1 FROM public.celestin_project_memberships AS membership
             WHERE membership.workspace_id = celestin_workspace_memberships.workspace_id
               AND membership.project_id = (
                   SELECT scope.project_id
                     FROM celestin_private.project_runtime_scopes AS scope
                    WHERE scope.login_role_name = session_user::name
               )
               AND membership.user_id = celestin_workspace_memberships.user_id
        )
    );
CREATE POLICY celestin_project_membership_read ON public.celestin_project_memberships
    FOR SELECT TO celestin_project_runtime
    USING (public.celestin_request_scope_matches(workspace_id, project_id));
CREATE POLICY celestin_project_user_read ON public.celestin_users
    FOR SELECT TO celestin_project_runtime
    USING (
        EXISTS (
            SELECT 1 FROM public.celestin_project_memberships AS membership
             WHERE membership.user_id = celestin_users.id
               AND public.celestin_request_scope_matches(
                   membership.workspace_id, membership.project_id
               )
        )
    );
CREATE POLICY celestin_project_service_identity_read
    ON public.celestin_service_identities
    FOR SELECT TO celestin_project_runtime
    USING (
        EXISTS (
            SELECT 1 FROM public.celestin_service_project_grants AS grant_row
             WHERE grant_row.workspace_id = celestin_service_identities.workspace_id
               AND grant_row.service_identity_id = celestin_service_identities.id
               AND public.celestin_request_scope_matches(
                   grant_row.workspace_id, grant_row.project_id
               )
        )
    );
CREATE POLICY celestin_project_service_grant_read
    ON public.celestin_service_project_grants
    FOR SELECT TO celestin_project_runtime
    USING (public.celestin_request_scope_matches(workspace_id, project_id));

GRANT USAGE ON SCHEMA public TO
    celestin_project_runtime, celestin_authorization_reader, celestin_authorization_admin;
GRANT SELECT ON TABLE
    public.celestin_users, public.celestin_workspaces,
    public.celestin_workspace_memberships, public.celestin_projects,
    public.celestin_project_memberships, public.celestin_service_identities,
    public.celestin_service_project_grants
TO celestin_project_runtime, celestin_authorization_reader;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE
    public.celestin_users, public.celestin_workspaces,
    public.celestin_workspace_memberships, public.celestin_projects,
    public.celestin_project_memberships, public.celestin_service_identities,
    public.celestin_service_project_grants
TO celestin_authorization_admin;

GRANT SELECT, INSERT ON TABLE public.chat_messages TO celestin_project_runtime;
GRANT SELECT, INSERT, UPDATE ON TABLE public.project_memory TO celestin_project_runtime;
DO $$
DECLARE
    sequence_name TEXT;
BEGIN
    SELECT pg_catalog.pg_get_serial_sequence('public.chat_messages', 'id')
      INTO sequence_name;
    IF sequence_name IS NULL THEN
        RAISE EXCEPTION 'chat_messages identity sequence is missing';
    END IF;
    EXECUTE format('GRANT USAGE, SELECT ON SEQUENCE %s TO celestin_project_runtime',
                   sequence_name::regclass);
    SELECT pg_catalog.pg_get_serial_sequence('public.project_memory', 'id')
      INTO sequence_name;
    IF sequence_name IS NULL THEN
        RAISE EXCEPTION 'project_memory identity sequence is missing';
    END IF;
    EXECUTE format('GRANT USAGE, SELECT ON SEQUENCE %s TO celestin_project_runtime',
                   sequence_name::regclass);
END;
$$;

REVOKE ALL ON TABLE
    public.celestin_users, public.celestin_workspaces,
    public.celestin_workspace_memberships, public.celestin_projects,
    public.celestin_project_memberships, public.celestin_service_identities,
    public.celestin_service_project_grants
FROM PUBLIC;
REVOKE ALL ON TABLE celestin_private.project_runtime_scopes FROM PUBLIC;
REVOKE ALL ON TABLE
    public.celestin_knowledge_records, public.celestin_evidence_records,
    public.celestin_project_producers, public.celestin_project_source_namespaces,
    public.celestin_candidates, public.celestin_candidate_revisions,
    public.celestin_candidate_review_events, public.celestin_candidate_evidence_records,
    public.celestin_candidate_occurrences, public.celestin_candidate_conversions
FROM celestin_project_runtime, celestin_authorization_reader, celestin_authorization_admin;
REVOKE ALL ON TABLE public.celestin_schema_migrations
FROM celestin_project_runtime, celestin_authorization_reader, celestin_authorization_admin;
