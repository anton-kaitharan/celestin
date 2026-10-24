-- Limit project credentials to workspace-scoped request locks. Keep the
-- cross-workspace request-lock API for the trusted authorization reader.
CREATE OR REPLACE FUNCTION public.celestin_lock_authorization_request(
    target_workspace UUID,
    principal_kind TEXT,
    principal_issuer TEXT,
    principal_subject TEXT
)
RETURNS VOID
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
DECLARE
    lock_key TEXT;
BEGIN
    FOR lock_key IN
        SELECT DISTINCT key_value
          FROM (VALUES
              (pg_catalog.jsonb_build_array('workspace', target_workspace)::text),
              (pg_catalog.jsonb_build_array(
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

REVOKE ALL ON FUNCTION public.celestin_lock_authorization_request(UUID, TEXT, TEXT, TEXT)
    FROM PUBLIC, celestin_project_runtime;
GRANT EXECUTE ON FUNCTION public.celestin_lock_authorization_request(UUID, TEXT, TEXT, TEXT)
    TO celestin_authorization_reader;

CREATE FUNCTION public.celestin_lock_project_authorization_request(
    target_workspace UUID
)
RETURNS VOID
LANGUAGE plpgsql
SET search_path = pg_catalog, celestin_private
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM celestin_private.project_runtime_scopes AS scope
         WHERE scope.login_role_name = session_user::name
           AND scope.workspace_id = target_workspace
    ) THEN
        RAISE EXCEPTION 'project authorization scope mismatch'
            USING ERRCODE = '42501';
    END IF;

    PERFORM pg_catalog.pg_advisory_xact_lock_shared(
        pg_catalog.hashtextextended(
            'celestin-auth-v1:' ||
                pg_catalog.jsonb_build_array('workspace', target_workspace)::text,
            0
        )
    );
END;
$$;

REVOKE ALL ON FUNCTION public.celestin_lock_project_authorization_request(UUID)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.celestin_lock_project_authorization_request(UUID)
    TO celestin_project_runtime;

-- A principal status/identity change takes the same workspace locks as a
-- project request for every workspace currently associated with that user.
-- This preserves request-vs-revocation ordering without exposing arbitrary
-- principal lock keys to a project credential.
CREATE OR REPLACE FUNCTION public.celestin_lock_human_identity_auth_row()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    lock_key TEXT;
BEGIN
    FOR lock_key IN
        SELECT DISTINCT key_value
          FROM (
              SELECT pg_catalog.jsonb_build_array(
                         'principal', 'human', OLD.issuer, OLD.subject
                     )::text AS key_value
               WHERE OLD.issuer IS NOT NULL AND OLD.subject IS NOT NULL
              UNION ALL
              SELECT pg_catalog.jsonb_build_array(
                         'principal', 'human', NEW.issuer, NEW.subject
                     )::text
               WHERE NEW.issuer IS NOT NULL AND NEW.subject IS NOT NULL
              UNION ALL
              SELECT pg_catalog.jsonb_build_array(
                         'workspace', affected.workspace_id
                     )::text
                FROM (
                    SELECT membership.workspace_id
                      FROM public.celestin_workspace_memberships AS membership
                     WHERE membership.user_id IN (OLD.id, NEW.id)
                    UNION
                    SELECT membership.workspace_id
                      FROM public.celestin_project_memberships AS membership
                     WHERE membership.user_id IN (OLD.id, NEW.id)
                ) AS affected
          ) AS lock_keys
         ORDER BY key_value
    LOOP
        PERFORM pg_catalog.pg_advisory_xact_lock(
            pg_catalog.hashtextextended('celestin-auth-v1:' || lock_key, 0)
        );
    END LOOP;
    RETURN NEW;
END;
$$;

-- Trigger functions are invoked by their triggers; runtime roles do not need
-- direct EXECUTE grants. Remove PostgreSQL's default PUBLIC function grant.
DO $$
DECLARE
    trigger_function RECORD;
BEGIN
    FOR trigger_function IN
        SELECT procedure.oid::regprocedure AS signature
          FROM pg_catalog.pg_proc AS procedure
          JOIN pg_catalog.pg_namespace AS namespace
            ON namespace.oid = procedure.pronamespace
         WHERE namespace.nspname = 'public'
           AND procedure.prorettype = 'trigger'::regtype
    LOOP
        EXECUTE format(
            'REVOKE ALL ON FUNCTION %s FROM PUBLIC, celestin_project_runtime, '
            'celestin_authorization_reader, celestin_authorization_admin',
            trigger_function.signature
        );
    END LOOP;
END;
$$;
