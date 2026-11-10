-- Require every committed workspace to retain an active human owner.
-- Existing BEFORE authorization triggers serialize writes by workspace before
-- the deferred check observes final transaction state.
CREATE FUNCTION public.celestin_require_active_workspace_owner()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    old_workspace UUID;
    new_workspace UUID;
    target_workspace UUID;
    has_active_owner BOOLEAN;
BEGIN
    IF TG_TABLE_NAME = 'celestin_workspaces' THEN
        IF TG_OP <> 'INSERT' THEN old_workspace := (to_jsonb(OLD)->>'id')::uuid; END IF;
        IF TG_OP <> 'DELETE' THEN new_workspace := (to_jsonb(NEW)->>'id')::uuid; END IF;
    ELSIF TG_TABLE_NAME = 'celestin_workspace_memberships' THEN
        IF TG_OP <> 'INSERT' THEN old_workspace := (to_jsonb(OLD)->>'workspace_id')::uuid; END IF;
        IF TG_OP <> 'DELETE' THEN new_workspace := (to_jsonb(NEW)->>'workspace_id')::uuid; END IF;
    END IF;

    IF TG_TABLE_NAME = 'celestin_users' THEN
        FOR target_workspace IN
            SELECT DISTINCT membership.workspace_id
              FROM public.celestin_workspace_memberships AS membership
             WHERE membership.user_id IN (OLD.id, NEW.id)
             ORDER BY membership.workspace_id
        LOOP
            PERFORM public.celestin_lock_workspace_authorization_write(target_workspace);
            IF EXISTS (SELECT 1 FROM public.celestin_workspaces WHERE id = target_workspace) THEN
                SELECT EXISTS (
                    SELECT 1
                      FROM public.celestin_workspace_memberships AS membership
                      JOIN public.celestin_users AS identity ON identity.id = membership.user_id
                     WHERE membership.workspace_id = target_workspace
                       AND membership.role = 'owner'
                       AND membership.status = 'active'
                       AND identity.status = 'active'
                ) INTO has_active_owner;
                IF NOT has_active_owner THEN
                    RAISE EXCEPTION 'workspace must retain an active owner'
                        USING ERRCODE = '23514', CONSTRAINT = 'celestin_workspace_active_owner_required';
                END IF;
            END IF;
        END LOOP;
        RETURN NEW;
    END IF;

    FOR target_workspace IN
        SELECT DISTINCT workspace_id
          FROM unnest(ARRAY[old_workspace, new_workspace]) AS ids(workspace_id)
         WHERE workspace_id IS NOT NULL
         ORDER BY workspace_id
    LOOP
        PERFORM public.celestin_lock_workspace_authorization_write(target_workspace);
        IF EXISTS (SELECT 1 FROM public.celestin_workspaces WHERE id = target_workspace) THEN
            SELECT EXISTS (
                SELECT 1
                  FROM public.celestin_workspace_memberships AS membership
                  JOIN public.celestin_users AS identity ON identity.id = membership.user_id
                 WHERE membership.workspace_id = target_workspace
                   AND membership.role = 'owner'
                   AND membership.status = 'active'
                   AND identity.status = 'active'
            ) INTO has_active_owner;
            IF NOT has_active_owner THEN
                RAISE EXCEPTION 'workspace must retain an active owner'
                    USING ERRCODE = '23514', CONSTRAINT = 'celestin_workspace_active_owner_required';
            END IF;
        END IF;
    END LOOP;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END;
$$;

REVOKE ALL ON FUNCTION public.celestin_require_active_workspace_owner() FROM PUBLIC;

CREATE CONSTRAINT TRIGGER celestin_workspace_active_owner_guard
AFTER INSERT OR UPDATE OR DELETE ON public.celestin_workspaces
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION public.celestin_require_active_workspace_owner();
CREATE CONSTRAINT TRIGGER celestin_membership_active_owner_guard
AFTER INSERT OR UPDATE OR DELETE ON public.celestin_workspace_memberships
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION public.celestin_require_active_workspace_owner();
CREATE CONSTRAINT TRIGGER celestin_user_active_owner_guard
AFTER UPDATE ON public.celestin_users
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION public.celestin_require_active_workspace_owner();
