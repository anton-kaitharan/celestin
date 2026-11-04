# Offline Hosted Provisioning and Readiness Assessment

**Status: source-level/offline tooling only. It does not provision a database, verify a live catalog, or authorize hosted serving.** The hosted tenant-isolation gate remains closed under [the lock-isolation decision](hosted-lock-isolation-decision.md).

## Purpose and boundary

`orbit.mcp_server.hosted_preflight.assess_hosted_onboarding` provides a small, deterministic assessment over an onboarding request, non-secret project configuration, and an operator-supplied catalog evidence bundle. It reuses the role-probe and catalog validators used by hosted runtime readiness. It does not open a connection, execute SQL, invoke `PostgresAccessProvisioner` or `ProjectDatabaseLoginRegistry`, apply migrations, create roles, change grants, or repair evidence.

The API has no field for a DSN, password, token, or other credential. Diagnostics use fixed codes and messages; they never echo supplied identifiers, catalog values, or exception text. `HostedReadinessReport.as_dict()` is the machine-readable representation.

## Independent assessments

- **Onboarding** checks request/configuration workspace and project UUIDs, exact binding, database/provider/schema metadata, role login inventory, and the configured maximum evidence age.
- **Request runtime** evaluates only the project-runtime and authorization-reader profiles, their role probe and advisory ACL rows, plus RLS, function, and trigger catalogs. It does not invoke privileged authorization-admin or migration operations.
- **Privileged preflight** separately evaluates the authorization-admin and migration profiles, the configured direct membership roster, and shared policy/trigger catalogs. It is an assessment only, not an operator execution path.

The offline evidence-bundle model does not currently carry the dedicated Evidence-table ACL rows. Its structural result therefore does not establish the exact Evidence runtime grant profile. The actual hosted project-pool startup check independently inspects effective privileges and table ACL catalog entries for both Evidence tables; the offline assessment must not be presented as validating that profile.

The profile inventory names match the current hosted readiness profiles: `project_runtime`, `authorization_reader`, `authorization_admin`, and `migration`. The configured group-member roster is compared exactly with the supplied privileged-role catalog rows. Other project runtime logins may be included in that explicit roster.

`profile_structure_status: STRUCTURAL_PASS` means supplied row shapes and values passed the source-level validators except for the explicitly diagnosed unverified lock-scope gate. `status: BLOCKED` remains the full profile result whenever that gate or any other required check fails. A structural pass must not be described as a live-ready result.

## Evidence freshness and trust

The caller must provide an aware `now` value and an explicit positive `max_evidence_age_seconds` policy. The evaluator rejects missing timestamps, future timestamps, stale bundles, database-identity/schema-version mismatches, duplicate profile snapshots, profile/login mismatches, and incomplete catalogs. It validates raw rows with the existing fail-closed validators in `hosted_server.py`.

The evidence bundle is still untrusted input. The evaluator has no signed export, authenticated collector, database identity handshake, or provenance check. Matching rows prove only that the supplied values match the current Python validator contract. They do not prove those values came from PostgreSQL, from the claimed database, or from the configured login.

## Operational gate

The report always returns:

- `evidence_level: offline_structural_only`
- `live_database_verified: false`
- `service_permitted: false`
- `provisioning_permitted: false`
- `operational_status: BLOCKED`

Provider advisory-lock and ownership capability remains unverified. This workflow does not turn an operator assertion such as `provider_capability="supported"` into evidence. Live PostgreSQL/Neon validation, provider capability confirmation, and the decisions in the lock-isolation record remain separate gates.

The evaluator is not called from normal hosted request startup. Existing startup readiness remains responsible for checking actual configured runtime pools, and [the regression test](../tests/test_hosted_server.py) continues to verify that request readiness does not run authorization-admin preflight. Local stdio/SQLite composition is unchanged.

## Offline verification scope

`tests/test_hosted_server.py` exercises valid and malformed onboarding inputs, scope mismatches, missing profiles and catalog sections, stale and contradictory evidence, excessive advisory privileges, independent assessment outcomes, provider-unknown blocking, redacted diagnostics, and cross-project scope mismatch. Existing hosted readiness tests cover the request-startup/admin-preflight separation. These tests use fixtures and do not establish live PostgreSQL or Neon behavior.
