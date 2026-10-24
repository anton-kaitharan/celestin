-- Disposable constraint checks for the hosted-tenancy PostgreSQL draft.
-- This script assumes the schema is already installed in public; it never
-- creates, alters, drops, or truncates persistent schema objects.
--
-- Run only after an owner has approved and provisioned a fresh disposable
-- database that already contains the reviewed schema. Supply the same exact
-- name as the connection target and approved_test_database psql variable:
-- psql -X -W -h 127.0.0.1 -p 5432 -U <test-role> \
--   -d <approved-disposable-database> \
--   -v approved_test_database=<same-approved-disposable-database> \
--   -v ON_ERROR_STOP=1 -f tests/postgres/validate_hosted_schema.sql
--
-- All fixtures and temporary helpers are inside one transaction. Expected
-- failures are caught in PL/pgSQL exception blocks (subtransactions). The
-- script ends with ROLLBACK; if an unexpected SQL error stops psql earlier,
-- closing its connection also rolls back the open transaction.

\set ON_ERROR_STOP on
\pset pager off

\if :{?approved_test_database}
\else
\echo Refusing validation: pass -v approved_test_database=<approved disposable database>
\quit 2
\endif

SELECT current_database() = :'approved_test_database'
       AND current_database() ~ '^celestin_it_[0-9]{8}_[0-9a-f]{8}$'
       AND current_database() NOT IN (
           'celestin_schema_test_v2',
           'celestin_it_20261014_a7f3c9',
           'celestin_it_20261015_b364bbae',
           'celestin_it_20261015_10ac5ae9',
           'celestin_it_20261015_0b8e767c'
       )
       AND inet_server_addr() IS NOT DISTINCT FROM inet '127.0.0.1'
       AS approved_target \gset

\if :approved_target
\echo Supplied target name and loopback endpoint match the guard.
\else
\echo Refusing validation: target must match the supplied approved name, use the disposable naming convention, and be on 127.0.0.1.
\quit 2
\endif

DO $guard$
DECLARE
    found_tables integer;
    has_expected_index boolean;
BEGIN
    IF inet_server_addr() IS DISTINCT FROM inet '127.0.0.1' THEN
        RAISE EXCEPTION 'Refusing validation: expected a loopback PostgreSQL server';
    END IF;

    SELECT count(*)
      INTO found_tables
      FROM unnest(ARRAY[
          'celestin_users',
          'celestin_workspaces',
          'celestin_workspace_memberships',
          'celestin_projects',
          'celestin_project_memberships',
          'celestin_service_identities',
          'celestin_service_project_grants',
          'chat_messages',
          'project_memory'
      ]) AS required(table_name)
     WHERE to_regclass('public.' || quote_ident(required.table_name)) IS NOT NULL;

    IF found_tables <> 9 THEN
        RAISE EXCEPTION 'Refusing validation: expected 9 hosted schema tables, found %', found_tables;
    END IF;

    SELECT EXISTS (
        SELECT 1
          FROM pg_index AS i
          JOIN pg_class AS idx ON idx.oid = i.indexrelid
         WHERE i.indrelid = to_regclass('public.project_memory')
           AND idx.relname = 'idx_project_memory_project_source_message'
           AND i.indisvalid
           AND i.indisready
           AND i.indpred IS NULL
           AND strpos(pg_get_indexdef(i.indexrelid), '(project_id, source_message_id)') > 0
    ) INTO has_expected_index;

    IF NOT has_expected_index THEN
        RAISE EXCEPTION 'Refusing validation: expected non-partial project/source-message index is missing';
    END IF;

    IF EXISTS (
        SELECT 1 FROM celestin_workspaces
         WHERE id IN (
             'a4000000-0000-4000-8000-202610140001',
             'a4000000-0000-4000-8000-202610140002',
             'a4000000-0000-4000-8000-202610140003',
             'a4000000-0000-4000-8000-202610140014'
         ) OR slug LIKE 'p2b-v2-20261014-a91e7d7d-%'
    ) OR EXISTS (
        SELECT 1 FROM celestin_users
         WHERE id IN (
             'a4000000-0000-4000-8000-202610140004',
             'a4000000-0000-4000-8000-202610140005',
             'a4000000-0000-4000-8000-202610140006',
             'a4000000-0000-4000-8000-202610140016',
             'a4000000-0000-4000-8000-202610140017',
             'a4000000-0000-4000-8000-202610140018'
         )
    ) OR EXISTS (
        SELECT 1 FROM celestin_projects
         WHERE id IN (
             'a4000000-0000-4000-8000-202610140007',
             'a4000000-0000-4000-8000-202610140008',
             'a4000000-0000-4000-8000-202610140009'
         ) OR (slug, legacy_project_key) IN (('p2b-a1', 'p2b-a1'),
                                              ('p2b-a2', 'p2b-a2'),
                                              ('p2b-b1', 'p2b-b1'))
    ) OR EXISTS (
        SELECT 1 FROM celestin_service_identities
         WHERE id IN (
             'a4000000-0000-4000-8000-202610140010',
             'a4000000-0000-4000-8000-202610140011',
             'a4000000-0000-4000-8000-202610140012',
             'a4000000-0000-4000-8000-202610140013',
             'a4000000-0000-4000-8000-202610140015'
         )
    ) OR EXISTS (
        SELECT 1 FROM chat_messages WHERE id IN (980014001, 980014002)
    ) OR EXISTS (
        SELECT 1 FROM chat_messages WHERE id IN (980014003, 980014004)
    ) OR EXISTS (
        SELECT 1 FROM project_memory
         WHERE id BETWEEN 980024001 AND 980024006
    ) OR EXISTS (
        SELECT 1 FROM celestin_users
         WHERE issuer = 'p2b-validation'
           AND subject IN ('user-a', 'user-b', 'user-c', 'user-d', 'user-e', 'user-f')
    ) OR EXISTS (
        SELECT 1 FROM celestin_service_identities
         WHERE workspace_id IN (
             'a4000000-0000-4000-8000-202610140001',
             'a4000000-0000-4000-8000-202610140002'
         ) AND issuer = 'p2b-validation'
           AND subject IN (
               'service-a', 'service-b',
               'service-invalid-status', 'service-invalid-revocation', 'service-revoked'
           )
    ) THEN
        RAISE EXCEPTION 'Refusing validation: this fixture identifier prefix is already present';
    END IF;
END
$guard$;

\echo Approved target and required hosted schema verified.
BEGIN;

CREATE TEMP TABLE _hosted_schema_validation_results (
    test_name text NOT NULL,
    outcome text NOT NULL CHECK (outcome IN ('PASS', 'FAIL')),
    detail text NOT NULL
);

CREATE FUNCTION pg_temp.expect_success(
    test_name text,
    statement text,
    expected_rows bigint DEFAULT 1
)
RETURNS void
LANGUAGE plpgsql
AS $fn$
DECLARE
    actual_state text;
    actual_message text;
    actual_rows bigint;
BEGIN
    BEGIN
        EXECUTE statement;
        GET DIAGNOSTICS actual_rows = ROW_COUNT;
        IF actual_rows = expected_rows THEN
            INSERT INTO _hosted_schema_validation_results VALUES
                (test_name, 'PASS', format('affected expected row count: %s', expected_rows));
        ELSE
            INSERT INTO _hosted_schema_validation_results VALUES
                (test_name, 'FAIL', format(
                    'expected %s affected rows, got %s', expected_rows, actual_rows
                ));
        END IF;
    EXCEPTION WHEN OTHERS THEN
        GET STACKED DIAGNOSTICS
            actual_state = RETURNED_SQLSTATE,
            actual_message = MESSAGE_TEXT;
        INSERT INTO _hosted_schema_validation_results VALUES
            (test_name, 'FAIL', format('unexpected SQLSTATE %s: %s', actual_state, actual_message));
    END;
END
$fn$;

CREATE FUNCTION pg_temp.expect_reject(
    test_name text,
    statement text,
    expected_state text
)
RETURNS void
LANGUAGE plpgsql
AS $fn$
DECLARE
    actual_state text;
    actual_message text;
    command_succeeded boolean := false;
BEGIN
    BEGIN
        EXECUTE statement;
        command_succeeded := true;
    EXCEPTION WHEN OTHERS THEN
        GET STACKED DIAGNOSTICS
            actual_state = RETURNED_SQLSTATE,
            actual_message = MESSAGE_TEXT;
    END;

    IF command_succeeded THEN
        INSERT INTO _hosted_schema_validation_results VALUES
            (test_name, 'FAIL', format('statement succeeded; expected SQLSTATE %s', expected_state));
    ELSIF actual_state = expected_state THEN
        INSERT INTO _hosted_schema_validation_results VALUES
            (test_name, 'PASS', format('rejected with expected SQLSTATE %s', expected_state));
    ELSE
        INSERT INTO _hosted_schema_validation_results VALUES
            (test_name, 'FAIL', format(
                'expected SQLSTATE %s, got %s: %s',
                expected_state, COALESCE(actual_state, 'unknown'), COALESCE(actual_message, 'no message')
            ));
    END IF;
END
$fn$;

CREATE FUNCTION pg_temp.expect_true(test_name text, query text)
RETURNS void
LANGUAGE plpgsql
AS $fn$
DECLARE
    observed boolean;
    actual_state text;
    actual_message text;
BEGIN
    BEGIN
        EXECUTE query INTO observed;
        IF observed IS TRUE THEN
            INSERT INTO _hosted_schema_validation_results VALUES
                (test_name, 'PASS', 'condition was true');
        ELSE
            INSERT INTO _hosted_schema_validation_results VALUES
                (test_name, 'FAIL', format('condition was %s', COALESCE(observed::text, 'NULL')));
        END IF;
    EXCEPTION WHEN OTHERS THEN
        GET STACKED DIAGNOSTICS
            actual_state = RETURNED_SQLSTATE,
            actual_message = MESSAGE_TEXT;
        INSERT INTO _hosted_schema_validation_results VALUES
            (test_name, 'FAIL', format('query raised SQLSTATE %s: %s', actual_state, actual_message));
    END;
END
$fn$;

-- Fixed, synthetic IDs are guarded above. Every test and fixture is rolled back.
SELECT pg_temp.expect_success('workspace A', $stmt$
    INSERT INTO celestin_workspaces(id, slug, name)
    VALUES ('a4000000-0000-4000-8000-202610140001',
            'p2b-v2-20261014-a91e7d7d-workspace-a', 'Synthetic test workspace A')
$stmt$);
SELECT pg_temp.expect_success('workspace B', $stmt$
    INSERT INTO celestin_workspaces(id, slug, name)
    VALUES ('a4000000-0000-4000-8000-202610140002',
            'p2b-v2-20261014-a91e7d7d-workspace-b', 'Synthetic test workspace B')
$stmt$);
SELECT pg_temp.expect_success('ownerless workspace schema probe', $stmt$
    INSERT INTO celestin_workspaces(id, slug, name)
    VALUES ('a4000000-0000-4000-8000-202610140003',
            'p2b-v2-20261014-a91e7d7d-ownerless', 'Synthetic owner-policy probe')
$stmt$);
SELECT pg_temp.expect_true('schema permits zero active owners (application policy gap)', $stmt$
    SELECT EXISTS (
        SELECT 1 FROM celestin_workspaces
         WHERE id = 'a4000000-0000-4000-8000-202610140003'
    ) AND NOT EXISTS (
        SELECT 1 FROM celestin_workspace_memberships
         WHERE workspace_id = 'a4000000-0000-4000-8000-202610140003'
           AND role = 'owner' AND status = 'active'
    )
$stmt$);

SELECT pg_temp.expect_success('user A', $stmt$
    INSERT INTO celestin_users(id, issuer, subject)
    VALUES ('a4000000-0000-4000-8000-202610140004', 'p2b-validation', 'user-a')
$stmt$);
SELECT pg_temp.expect_success('user B', $stmt$
    INSERT INTO celestin_users(id, issuer, subject)
    VALUES ('a4000000-0000-4000-8000-202610140005', 'p2b-validation', 'user-b')
$stmt$);
SELECT pg_temp.expect_success('user C', $stmt$
    INSERT INTO celestin_users(id, issuer, subject)
    VALUES ('a4000000-0000-4000-8000-202610140006', 'p2b-validation', 'user-c')
$stmt$);
SELECT pg_temp.expect_success('user D', $stmt$
    INSERT INTO celestin_users(id, issuer, subject)
    VALUES ('a4000000-0000-4000-8000-202610140016', 'p2b-validation', 'user-d')
$stmt$);
SELECT pg_temp.expect_success('user E', $stmt$
    INSERT INTO celestin_users(id, issuer, subject)
    VALUES ('a4000000-0000-4000-8000-202610140017', 'p2b-validation', 'user-e')
$stmt$);
SELECT pg_temp.expect_success('user F', $stmt$
    INSERT INTO celestin_users(id, issuer, subject)
    VALUES ('a4000000-0000-4000-8000-202610140018', 'p2b-validation', 'user-f')
$stmt$);

SELECT pg_temp.expect_success('active owner membership A', $stmt$
    INSERT INTO celestin_workspace_memberships(workspace_id, user_id, role, status)
    VALUES ('a4000000-0000-4000-8000-202610140001',
            'a4000000-0000-4000-8000-202610140004', 'owner', 'active')
$stmt$);
SELECT pg_temp.expect_success('active owner membership B', $stmt$
    INSERT INTO celestin_workspace_memberships(workspace_id, user_id, role, status)
    VALUES ('a4000000-0000-4000-8000-202610140002',
            'a4000000-0000-4000-8000-202610140005', 'owner', 'active')
$stmt$);
SELECT pg_temp.expect_success('active member for constraint probes', $stmt$
    INSERT INTO celestin_workspace_memberships(workspace_id, user_id, role, status)
    VALUES ('a4000000-0000-4000-8000-202610140001',
            'a4000000-0000-4000-8000-202610140016', 'member', 'active')
$stmt$);
SELECT pg_temp.expect_success('active member E for status check', $stmt$
    INSERT INTO celestin_workspace_memberships(workspace_id, user_id, role, status)
    VALUES ('a4000000-0000-4000-8000-202610140001',
            'a4000000-0000-4000-8000-202610140017', 'member', 'active')
$stmt$);
SELECT pg_temp.expect_success('active member F for grant check', $stmt$
    INSERT INTO celestin_workspace_memberships(workspace_id, user_id, role, status)
    VALUES ('a4000000-0000-4000-8000-202610140001',
            'a4000000-0000-4000-8000-202610140018', 'member', 'active')
$stmt$);
SELECT pg_temp.expect_success('project A1', $stmt$
    INSERT INTO celestin_projects(id, workspace_id, slug, legacy_project_key)
    VALUES ('a4000000-0000-4000-8000-202610140007',
            'a4000000-0000-4000-8000-202610140001', 'p2b-a1', 'p2b-a1')
$stmt$);
SELECT pg_temp.expect_success('project A2', $stmt$
    INSERT INTO celestin_projects(id, workspace_id, slug, legacy_project_key)
    VALUES ('a4000000-0000-4000-8000-202610140008',
            'a4000000-0000-4000-8000-202610140001', 'p2b-a2', 'p2b-a2')
$stmt$);
SELECT pg_temp.expect_success('project B1', $stmt$
    INSERT INTO celestin_projects(id, workspace_id, slug, legacy_project_key)
    VALUES ('a4000000-0000-4000-8000-202610140009',
            'a4000000-0000-4000-8000-202610140002', 'p2b-b1', 'p2b-b1')
$stmt$);

SELECT pg_temp.expect_success('active service identity A', $stmt$
    INSERT INTO celestin_service_identities(id, workspace_id, issuer, subject, name, status)
    VALUES ('a4000000-0000-4000-8000-202610140010',
            'a4000000-0000-4000-8000-202610140001',
            'p2b-validation', 'service-a', 'Synthetic service A', 'active')
$stmt$);
SELECT pg_temp.expect_success('active service identity B', $stmt$
    INSERT INTO celestin_service_identities(id, workspace_id, issuer, subject, name, status)
    VALUES ('a4000000-0000-4000-8000-202610140011',
            'a4000000-0000-4000-8000-202610140002',
            'p2b-validation', 'service-b', 'Synthetic service B', 'active')
$stmt$);
SELECT pg_temp.expect_success('revoked service identity retains revocation state', $stmt$
    INSERT INTO celestin_service_identities(id, workspace_id, issuer, subject, name, status, revoked_at)
    VALUES ('a4000000-0000-4000-8000-202610140015',
            'a4000000-0000-4000-8000-202610140001',
            'p2b-validation', 'service-revoked', 'Synthetic revoked service', 'revoked', now())
$stmt$);

SELECT pg_temp.expect_success('valid human project grant', $stmt$
    INSERT INTO celestin_project_memberships(workspace_id, project_id, user_id, can_read, can_write)
    VALUES ('a4000000-0000-4000-8000-202610140001',
            'a4000000-0000-4000-8000-202610140007',
            'a4000000-0000-4000-8000-202610140004', true, true)
$stmt$);
SELECT pg_temp.expect_success('valid service project grant', $stmt$
    INSERT INTO celestin_service_project_grants(workspace_id, project_id, service_identity_id, can_read, can_write)
    VALUES ('a4000000-0000-4000-8000-202610140001',
            'a4000000-0000-4000-8000-202610140007',
            'a4000000-0000-4000-8000-202610140010', true, true)
$stmt$);

SELECT pg_temp.expect_success('message in project A1', $stmt$
    INSERT INTO chat_messages(id, workspace_id, project_id, conversation_id, role, content, created_at)
    VALUES (980014001, 'a4000000-0000-4000-8000-202610140001',
            'a4000000-0000-4000-8000-202610140007',
            'synthetic-validation', 'user', 'synthetic source message A1', now())
$stmt$);
SELECT pg_temp.expect_success('message in project B1', $stmt$
    INSERT INTO chat_messages(id, workspace_id, project_id, conversation_id, role, content, created_at)
    VALUES (980014002, 'a4000000-0000-4000-8000-202610140002',
            'a4000000-0000-4000-8000-202610140009',
            'synthetic-validation', 'assistant', 'synthetic source message B1', now())
$stmt$);
SELECT pg_temp.expect_success('decision references a same-project source message', $stmt$
    INSERT INTO project_memory(id, workspace_id, project_id, key, value, status,
                               source_message_id, created_at, updated_at)
    VALUES (980024001, 'a4000000-0000-4000-8000-202610140001',
            'a4000000-0000-4000-8000-202610140007',
            'p2b-valid-source', 'synthetic decision value', 'draft',
            980014001, now(), now())
$stmt$);
SELECT pg_temp.expect_success('decision without a source message is allowed', $stmt$
    INSERT INTO project_memory(id, workspace_id, project_id, key, value, status,
                               source_message_id, created_at, updated_at)
    VALUES (980024002, 'a4000000-0000-4000-8000-202610140001',
            'a4000000-0000-4000-8000-202610140007',
            'p2b-null-source', 'synthetic unlinked decision', 'draft',
            NULL, now(), now())
$stmt$);

-- Foreign-key and uniqueness rejection checks. Each failure is isolated in a
-- PL/pgSQL exception subtransaction and must match the stated SQLSTATE.
SELECT pg_temp.expect_reject('message project/workspace mismatch', $stmt$
    INSERT INTO chat_messages(id, workspace_id, project_id, conversation_id, role, content, created_at)
    VALUES (980014003, 'a4000000-0000-4000-8000-202610140002',
            'a4000000-0000-4000-8000-202610140007',
            'synthetic-validation', 'user', 'invalid tenant pair', now())
$stmt$, '23503');
SELECT pg_temp.expect_reject('memory project/workspace mismatch', $stmt$
    INSERT INTO project_memory(id, workspace_id, project_id, key, value, status, created_at, updated_at)
    VALUES (980024003, 'a4000000-0000-4000-8000-202610140002',
            'a4000000-0000-4000-8000-202610140007',
            'p2b-invalid-tenant', 'synthetic', 'draft', now(), now())
$stmt$, '23503');
SELECT pg_temp.expect_reject('human grant project belongs to another workspace', $stmt$
    INSERT INTO celestin_project_memberships(workspace_id, project_id, user_id, can_read, can_write)
    VALUES ('a4000000-0000-4000-8000-202610140002',
            'a4000000-0000-4000-8000-202610140007',
            'a4000000-0000-4000-8000-202610140005', true, false)
$stmt$, '23503');
SELECT pg_temp.expect_reject('human grant membership belongs to another workspace', $stmt$
    INSERT INTO celestin_project_memberships(workspace_id, project_id, user_id, can_read, can_write)
    VALUES ('a4000000-0000-4000-8000-202610140001',
            'a4000000-0000-4000-8000-202610140007',
            'a4000000-0000-4000-8000-202610140005', true, false)
$stmt$, '23503');
SELECT pg_temp.expect_reject('service grant project belongs to another workspace', $stmt$
    INSERT INTO celestin_service_project_grants(workspace_id, project_id, service_identity_id, can_read, can_write)
    VALUES ('a4000000-0000-4000-8000-202610140001',
            'a4000000-0000-4000-8000-202610140009',
            'a4000000-0000-4000-8000-202610140010', true, false)
$stmt$, '23503');
SELECT pg_temp.expect_reject('service grant identity belongs to another workspace', $stmt$
    INSERT INTO celestin_service_project_grants(workspace_id, project_id, service_identity_id, can_read, can_write)
    VALUES ('a4000000-0000-4000-8000-202610140002',
            'a4000000-0000-4000-8000-202610140009',
            'a4000000-0000-4000-8000-202610140010', true, false)
$stmt$, '23503');
SELECT pg_temp.expect_reject('decision source belongs to another project', $stmt$
    INSERT INTO project_memory(id, workspace_id, project_id, key, value, status,
                               source_message_id, created_at, updated_at)
    VALUES (980024004, 'a4000000-0000-4000-8000-202610140001',
            'a4000000-0000-4000-8000-202610140008',
            'p2b-cross-project-source', 'synthetic', 'draft', 980014001, now(), now())
$stmt$, '23503');
SELECT pg_temp.expect_reject('decision source does not exist', $stmt$
    INSERT INTO project_memory(id, workspace_id, project_id, key, value, status,
                               source_message_id, created_at, updated_at)
    VALUES (980024005, 'a4000000-0000-4000-8000-202610140001',
            'a4000000-0000-4000-8000-202610140007',
            'p2b-missing-source', 'synthetic', 'draft', 989999999, now(), now())
$stmt$, '23503');
SELECT pg_temp.expect_reject('duplicate workspace membership', $stmt$
    INSERT INTO celestin_workspace_memberships(workspace_id, user_id, role, status)
    VALUES ('a4000000-0000-4000-8000-202610140001',
            'a4000000-0000-4000-8000-202610140004', 'owner', 'active')
$stmt$, '23505');
SELECT pg_temp.expect_reject('duplicate human project grant', $stmt$
    INSERT INTO celestin_project_memberships(workspace_id, project_id, user_id, can_read, can_write)
    VALUES ('a4000000-0000-4000-8000-202610140001',
            'a4000000-0000-4000-8000-202610140007',
            'a4000000-0000-4000-8000-202610140004', true, true)
$stmt$, '23505');
SELECT pg_temp.expect_reject('duplicate service project grant', $stmt$
    INSERT INTO celestin_service_project_grants(workspace_id, project_id, service_identity_id, can_read, can_write)
    VALUES ('a4000000-0000-4000-8000-202610140001',
            'a4000000-0000-4000-8000-202610140007',
            'a4000000-0000-4000-8000-202610140010', true, true)
$stmt$, '23505');

-- Schema CHECK constraints for role/status enumerations and grant implication.
SELECT pg_temp.expect_reject('invalid workspace membership role', $stmt$
    INSERT INTO celestin_workspace_memberships(workspace_id, user_id, role, status)
    VALUES ('a4000000-0000-4000-8000-202610140001',
            'a4000000-0000-4000-8000-202610140016', 'viewer', 'active')
$stmt$, '23514');
SELECT pg_temp.expect_reject('invalid workspace membership status', $stmt$
    INSERT INTO celestin_workspace_memberships(workspace_id, user_id, role, status)
    VALUES ('a4000000-0000-4000-8000-202610140001',
            'a4000000-0000-4000-8000-202610140017', 'member', 'disabled')
$stmt$, '23514');
SELECT pg_temp.expect_reject('invalid service identity status', $stmt$
    INSERT INTO celestin_service_identities(id, workspace_id, issuer, subject, name, status)
    VALUES ('a4000000-0000-4000-8000-202610140012',
            'a4000000-0000-4000-8000-202610140001',
            'p2b-validation', 'service-invalid-status', 'Synthetic invalid status', 'disabled')
$stmt$, '23514');
SELECT pg_temp.expect_reject('active service identity cannot have revoked_at', $stmt$
    INSERT INTO celestin_service_identities(id, workspace_id, issuer, subject, name, status, revoked_at)
    VALUES ('a4000000-0000-4000-8000-202610140013',
            'a4000000-0000-4000-8000-202610140001',
            'p2b-validation', 'service-invalid-revocation', 'Synthetic invalid revocation', 'active', now())
$stmt$, '23514');
SELECT pg_temp.expect_reject('invalid chat message role', $stmt$
    INSERT INTO chat_messages(id, workspace_id, project_id, conversation_id, role, content, created_at)
    VALUES (980014004, 'a4000000-0000-4000-8000-202610140001',
            'a4000000-0000-4000-8000-202610140007',
            'synthetic-validation', 'tool', 'synthetic invalid role', now())
$stmt$, '23514');
SELECT pg_temp.expect_reject('invalid memory decision status', $stmt$
    INSERT INTO project_memory(id, workspace_id, project_id, key, value, status, created_at, updated_at)
    VALUES (980024006, 'a4000000-0000-4000-8000-202610140001',
            'a4000000-0000-4000-8000-202610140007',
            'p2b-invalid-status', 'synthetic', 'pending', now(), now())
$stmt$, '23514');
SELECT pg_temp.expect_reject('human write grant implies read', $stmt$
    INSERT INTO celestin_project_memberships(workspace_id, project_id, user_id, can_read, can_write)
    VALUES ('a4000000-0000-4000-8000-202610140001',
            'a4000000-0000-4000-8000-202610140007',
            'a4000000-0000-4000-8000-202610140006', false, true)
$stmt$, '23514');
SELECT pg_temp.expect_reject('service write grant implies read', $stmt$
    INSERT INTO celestin_service_project_grants(workspace_id, project_id, service_identity_id, can_read, can_write)
    VALUES ('a4000000-0000-4000-8000-202610140001',
            'a4000000-0000-4000-8000-202610140007',
            'a4000000-0000-4000-8000-202610140010', false, true)
$stmt$, '23514');
SELECT pg_temp.expect_reject('human grant revocation cannot predate grant', $stmt$
    INSERT INTO celestin_project_memberships(
        workspace_id, project_id, user_id, can_read, can_write, granted_at, revoked_at
    ) VALUES (
        'a4000000-0000-4000-8000-202610140001',
        'a4000000-0000-4000-8000-202610140007',
        'a4000000-0000-4000-8000-202610140018', true, false,
        now(), now() - interval '1 day'
    )
$stmt$, '23514');
SELECT pg_temp.expect_reject('service grant revocation cannot predate grant', $stmt$
    INSERT INTO celestin_service_project_grants(
        workspace_id, project_id, service_identity_id, can_read, can_write, granted_at, revoked_at
    ) VALUES (
        'a4000000-0000-4000-8000-202610140001',
        'a4000000-0000-4000-8000-202610140007',
        'a4000000-0000-4000-8000-202610140010', true, false,
        now(), now() - interval '1 day'
    )
$stmt$, '23514');

-- RESTRICT must retain both the source row and its memory provenance link.
SELECT pg_temp.expect_reject('source message deletion is restricted', $stmt$
    DELETE FROM chat_messages WHERE id = 980014001
$stmt$, '23001');
SELECT pg_temp.expect_reject('project deletion is restricted while project data exists', $stmt$
    DELETE FROM celestin_projects
     WHERE id = 'a4000000-0000-4000-8000-202610140007'
$stmt$, '23001');
SELECT pg_temp.expect_reject('workspace deletion is restricted while workspace data exists', $stmt$
    DELETE FROM celestin_workspaces
     WHERE id = 'a4000000-0000-4000-8000-202610140001'
$stmt$, '23001');
SELECT pg_temp.expect_true('source message and decision provenance remain after rejected delete', $stmt$
    SELECT EXISTS (
        SELECT 1 FROM chat_messages
         WHERE id = 980014001 AND project_id = 'a4000000-0000-4000-8000-202610140007'
    ) AND EXISTS (
        SELECT 1 FROM project_memory
         WHERE id = 980024001 AND project_id = 'a4000000-0000-4000-8000-202610140007'
           AND source_message_id = 980014001
    )
$stmt$);

-- Valid and invalid archive/purge timestamp combinations across all four data tables.
SELECT pg_temp.expect_success('valid project archive and purge dates', $stmt$
    UPDATE celestin_projects
       SET archived_at = now(), purge_after = now() + interval '1 day'
     WHERE id = 'a4000000-0000-4000-8000-202610140008'
$stmt$);
SELECT pg_temp.expect_reject('workspace purge date requires archive', $stmt$
    UPDATE celestin_workspaces SET purge_after = now()
     WHERE id = 'a4000000-0000-4000-8000-202610140002'
$stmt$, '23514');
SELECT pg_temp.expect_reject('project purge date requires archive', $stmt$
    UPDATE celestin_projects SET purge_after = now()
     WHERE id = 'a4000000-0000-4000-8000-202610140009'
$stmt$, '23514');
SELECT pg_temp.expect_reject('message purge date requires archive', $stmt$
    UPDATE chat_messages SET purge_after = now()
     WHERE id = 980014002
$stmt$, '23514');
SELECT pg_temp.expect_reject('decision purge date requires archive', $stmt$
    UPDATE project_memory SET purge_after = now()
     WHERE id = 980024002
$stmt$, '23514');
SELECT pg_temp.expect_reject('workspace purge date cannot predate archive', $stmt$
    UPDATE celestin_workspaces
       SET archived_at = now(), purge_after = now() - interval '1 day'
     WHERE id = 'a4000000-0000-4000-8000-202610140002'
$stmt$, '23514');
SELECT pg_temp.expect_reject('project purge date cannot predate archive', $stmt$
    UPDATE celestin_projects
       SET archived_at = now(), purge_after = now() - interval '1 day'
     WHERE id = 'a4000000-0000-4000-8000-202610140009'
$stmt$, '23514');
SELECT pg_temp.expect_reject('message purge date cannot predate archive', $stmt$
    UPDATE chat_messages
       SET archived_at = now(), purge_after = now() - interval '1 day'
     WHERE id = 980014002
$stmt$, '23514');
SELECT pg_temp.expect_reject('decision purge date cannot predate archive', $stmt$
    UPDATE project_memory
       SET archived_at = now(), purge_after = now() - interval '1 day'
     WHERE id = 980024002
$stmt$, '23514');

-- A savepoint probe verifies rollback within the fixture transaction.
SAVEPOINT rollback_probe;
SELECT pg_temp.expect_success('insert rollback probe workspace', $stmt$
    INSERT INTO celestin_workspaces(id, slug, name)
    VALUES ('a4000000-0000-4000-8000-202610140014',
            'p2b-v2-20261014-a91e7d7d-savepoint', 'Synthetic rollback probe')
$stmt$);
ROLLBACK TO SAVEPOINT rollback_probe;
RELEASE SAVEPOINT rollback_probe;
SELECT pg_temp.expect_true('savepoint rollback removed probe row', $stmt$
    SELECT NOT EXISTS (
        SELECT 1 FROM celestin_workspaces
         WHERE id = 'a4000000-0000-4000-8000-202610140014'
    )
$stmt$);

\echo --- Individual validation results ---
TABLE pg_temp._hosted_schema_validation_results;
SELECT COALESCE(bool_or(outcome = 'FAIL'), false)::text AS validation_failed
  FROM pg_temp._hosted_schema_validation_results
\gset

ROLLBACK;

\echo --- Final fixture cleanup verification ---
SELECT ((
    (SELECT count(*) FROM celestin_workspaces
      WHERE slug LIKE 'p2b-v2-20261014-a91e7d7d-%')
  + (SELECT count(*) FROM celestin_users
      WHERE id IN (
          'a4000000-0000-4000-8000-202610140004',
          'a4000000-0000-4000-8000-202610140005',
          'a4000000-0000-4000-8000-202610140006',
          'a4000000-0000-4000-8000-202610140016',
          'a4000000-0000-4000-8000-202610140017',
          'a4000000-0000-4000-8000-202610140018'
      ))
  + (SELECT count(*) FROM celestin_projects
      WHERE id IN (
          'a4000000-0000-4000-8000-202610140007',
          'a4000000-0000-4000-8000-202610140008',
          'a4000000-0000-4000-8000-202610140009'
      ))
  + (SELECT count(*) FROM celestin_workspace_memberships
      WHERE user_id IN (
          'a4000000-0000-4000-8000-202610140004',
          'a4000000-0000-4000-8000-202610140005',
          'a4000000-0000-4000-8000-202610140006',
          'a4000000-0000-4000-8000-202610140016',
          'a4000000-0000-4000-8000-202610140017',
          'a4000000-0000-4000-8000-202610140018'
      ))
  + (SELECT count(*) FROM celestin_project_memberships
      WHERE project_id = 'a4000000-0000-4000-8000-202610140007')
  + (SELECT count(*) FROM celestin_service_identities
      WHERE id IN (
          'a4000000-0000-4000-8000-202610140010',
          'a4000000-0000-4000-8000-202610140011',
          'a4000000-0000-4000-8000-202610140012',
          'a4000000-0000-4000-8000-202610140013',
          'a4000000-0000-4000-8000-202610140015'
      ))
  + (SELECT count(*) FROM celestin_service_project_grants
      WHERE project_id = 'a4000000-0000-4000-8000-202610140007')
  + (SELECT count(*) FROM chat_messages WHERE id IN (980014001, 980014002, 980014003, 980014004))
  + (SELECT count(*) FROM project_memory WHERE id BETWEEN 980024001 AND 980024006)
) > 0)::text AS fixtures_remain
\gset

\if :validation_failed
    \echo FAIL: one or more schema assertions failed
\else
    \echo PASS: all schema assertions passed
\endif
\if :fixtures_remain
    \echo FAIL: fixture rows remain after the outer ROLLBACK
    \set validation_failed true
\else
    \echo PASS: outer ROLLBACK left zero fixture rows
\endif
\if :validation_failed
    \quit 1
\else
    \echo Validation complete
\endif
