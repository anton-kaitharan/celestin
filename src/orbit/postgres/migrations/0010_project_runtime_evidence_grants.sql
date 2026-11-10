-- Expose only the operations required by the request-scoped Evidence store.
-- The migration runner applies this file and its ledger row in one transaction.
DO $$
DECLARE
    evidence_table_count INTEGER;
    missing_group_count INTEGER;
BEGIN
    SELECT count(*)
      INTO evidence_table_count
      FROM pg_catalog.pg_class AS relation
      JOIN pg_catalog.pg_namespace AS namespace
        ON namespace.oid = relation.relnamespace
     WHERE namespace.nspname = 'public'
       AND relation.relname IN (
           'celestin_knowledge_records',
           'celestin_evidence_records'
       )
       AND relation.relkind = 'r';

    IF evidence_table_count <> 2 THEN
        RAISE EXCEPTION 'expected both public Evidence tables before migration 0010';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM pg_catalog.pg_class AS relation
          JOIN pg_catalog.pg_namespace AS namespace
            ON namespace.oid = relation.relnamespace
         WHERE namespace.nspname = 'public'
           AND relation.relname IN (
               'celestin_knowledge_records',
               'celestin_evidence_records'
           )
           AND relation.relowner <> (
               SELECT owner_role.oid
                 FROM pg_catalog.pg_roles AS owner_role
                WHERE owner_role.rolname = current_user
           )
    ) THEN
        RAISE EXCEPTION 'migration identity must own both Evidence tables';
    END IF;

    SELECT count(*)
      INTO missing_group_count
      FROM (VALUES
          ('celestin_project_runtime'),
          ('celestin_authorization_reader'),
          ('celestin_authorization_admin')
      ) AS expected(role_name)
     WHERE NOT EXISTS (
         SELECT 1
           FROM pg_catalog.pg_roles AS existing_role
          WHERE existing_role.rolname = expected.role_name
     );

    IF missing_group_count <> 0 THEN
        RAISE EXCEPTION 'required hosted group roles are missing for migration 0010';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM pg_catalog.pg_roles AS group_role
         WHERE group_role.rolname IN (
             'celestin_project_runtime',
             'celestin_authorization_reader',
             'celestin_authorization_admin'
         )
           AND (
               group_role.rolcanlogin
               OR group_role.rolsuper
               OR group_role.rolbypassrls
               OR group_role.rolcreaterole
               OR group_role.rolcreatedb
               OR group_role.rolreplication
               OR NOT group_role.rolinherit
               OR EXISTS (
                   SELECT 1
                     FROM pg_catalog.pg_auth_members AS membership
                    WHERE membership.member = group_role.oid
               )
           )
    ) THEN
        RAISE EXCEPTION 'hosted group role profile is unsafe for migration 0010';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM pg_catalog.pg_roles AS migration_role
          JOIN pg_catalog.pg_roles AS group_role
            ON group_role.rolname IN (
                'celestin_project_runtime',
                'celestin_authorization_reader',
                'celestin_authorization_admin'
            )
         WHERE migration_role.rolname = current_user
           AND (
               migration_role.oid = group_role.oid
               OR pg_catalog.pg_has_role(
                   migration_role.oid, group_role.oid, 'MEMBER'
               )
           )
    ) THEN
        RAISE EXCEPTION 'migration identity must be separate from hosted runtime groups';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM pg_catalog.pg_class AS relation
          JOIN pg_catalog.pg_namespace AS namespace
            ON namespace.oid = relation.relnamespace
          JOIN pg_catalog.pg_attribute AS attribute
            ON attribute.attrelid = relation.oid
         WHERE namespace.nspname = 'public'
           AND relation.relname IN (
               'celestin_knowledge_records',
               'celestin_evidence_records'
           )
           AND attribute.attnum > 0
           AND NOT attribute.attisdropped
           AND attribute.attacl IS NOT NULL
    ) THEN
        RAISE EXCEPTION 'unexpected Evidence column ACL; migration 0010 refuses to alter it';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM pg_catalog.pg_class AS relation
          JOIN pg_catalog.pg_namespace AS namespace
            ON namespace.oid = relation.relnamespace
          CROSS JOIN LATERAL pg_catalog.aclexplode(
              COALESCE(
                  relation.relacl,
                  pg_catalog.acldefault('r', relation.relowner)
              )
          ) AS acl
         WHERE namespace.nspname = 'public'
           AND relation.relname IN (
               'celestin_knowledge_records',
               'celestin_evidence_records'
           )
           AND acl.grantee <> relation.relowner
           AND acl.grantee NOT IN (
               0,
               (SELECT oid FROM pg_catalog.pg_roles
                 WHERE rolname = 'celestin_project_runtime'),
               (SELECT oid FROM pg_catalog.pg_roles
                 WHERE rolname = 'celestin_authorization_reader'),
               (SELECT oid FROM pg_catalog.pg_roles
                 WHERE rolname = 'celestin_authorization_admin')
           )
    ) THEN
        RAISE EXCEPTION 'unexpected Evidence table ACL grantee; migration 0010 refuses to alter it';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM pg_catalog.pg_class AS relation
          JOIN pg_catalog.pg_namespace AS namespace
            ON namespace.oid = relation.relnamespace
          CROSS JOIN LATERAL pg_catalog.aclexplode(
              COALESCE(
                  relation.relacl,
                  pg_catalog.acldefault('r', relation.relowner)
              )
          ) AS acl
         WHERE namespace.nspname = 'public'
           AND relation.relname IN (
               'celestin_knowledge_records',
               'celestin_evidence_records'
           )
           AND acl.grantee = relation.relowner
           AND acl.grantor <> relation.relowner
    ) THEN
        RAISE EXCEPTION 'unexpected Evidence owner ACL grantor; migration 0010 refuses to alter it';
    END IF;
END;
$$;

REVOKE ALL PRIVILEGES ON TABLE
    public.celestin_knowledge_records,
    public.celestin_evidence_records
FROM PUBLIC,
    celestin_project_runtime,
    celestin_authorization_reader,
    celestin_authorization_admin;

GRANT SELECT, INSERT, UPDATE ON TABLE
    public.celestin_knowledge_records,
    public.celestin_evidence_records
TO celestin_project_runtime;
