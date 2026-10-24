# Celestin — Conceptual Database Schema

**Status:** Conceptual model for discussion only. This is NOT an approved PostgreSQL schema or migration.

## Purpose
Describe relationships needed across capture, review, evidence, conflict handling, reconstruction, and retrieval. The shared-memory owner must reconcile this with the real schema, adapters, tenancy, and migration plan.

## Conceptual entities

### Project
- Stable `project_id`.
- Membership and authorization are enforced by a trusted boundary, not caller-supplied fields.

### SourceMessage / SourceArtifact
- Stable source ID and project association.
- Source type and locator.
- Capture/occurrence timestamps where available.
- Optional revision or content hash.
- Access and retention policy as defined by the product.

### KnowledgeCandidate
- Stable knowledge/candidate ID and project ID.
- Producer/candidate identity as agreed.
- Claim/decision content.
- Lifecycle/review status.
- Certainty only if its meaning is defined by the actual model.
- Created/updated timestamps.
- Terminal review metadata that must not be silently overwritten.

### EvidenceAssociation
- Stable association ID.
- Knowledge/candidate ID.
- Source ID or locator.
- Evidence role/type.
- Optional revision, timestamp, excerpt reference, or content hash.
- Resolution state where supported.

This may need to be a first-class relationship if multiple typed sources are required. Do not assume it exists in the legacy schema.

### ReviewEvent
- Candidate/knowledge ID.
- Review decision and reviewer identity from a trusted source.
- Review timestamp and optional rationale/evidence references.
- Immutable history where required.

Reviewer labels supplied by arbitrary content are not authentication.

### ConflictRelationship
- Stable relationship ID, project ID, endpoint IDs.
- Relationship type/status.
- Discovery provenance and timestamp.
- Explicit resolution event/history if resolved.

The team must decide whether conflicts are directional, reciprocal, or represented as a canonical pair. Terminal-candidate immutability means code cannot assume it can update both endpoint records.

### VerificationRecord (if adopted)
- Knowledge/candidate ID.
- Verification status and method.
- Evidence used.
- Result timestamp.
- Source revision/content hash where applicable.
- Verifier/process identity and limitations.

Do not infer verification from approval or source existence.

### Applicability/Freshness assessment (if adopted)
- Knowledge/candidate ID.
- Applicability/freshness status.
- Assessed revision/time.
- Supporting evidence and assessment source/time.

Unknown must remain possible.

### SyncEvent / ChangeLog (according to agreed design)
- Stable operation/event ID and project ID.
- Producer/client identity from trusted context.
- Candidate identity and payload fingerprint if agreed.
- Outcome/time and retry/conflict reference where needed.

Identity, hashing, atomicity, and retry semantics need an agreed contract.

## Relationship sketch
```text
Project
 ├── SourceMessage / SourceArtifact
 ├── KnowledgeCandidate
 │    ├── EvidenceAssociation ──> SourceMessage / SourceArtifact
 │    ├── ReviewEvent
 │    ├── VerificationRecord
 │    ├── Applicability/Freshness assessment
 │    └── ConflictRelationship <──> KnowledgeCandidate
 └── SyncEvent / ChangeLog
```
Conceptual only; does not prescribe normalized tables or foreign keys.

## Required invariants
- Knowledge, evidence, conflicts, and sync records are correctly project-scoped.
- Project ID is not authorization.
- Evidence points to stable source identity or explicitly unresolved locators.
- Approval, verification, evidence availability, freshness, and conflict remain distinct.
- Terminal candidates are not silently mutated by recapture/conflict discovery.
- Retries follow agreed idempotency rules.
- Conflicts/resolutions preserve history.
- Missing metadata does not become a positive claim.
- Multi-record writes have defined atomicity and transaction ownership.
- Access and revocation behavior belongs to the trusted authorization boundary.

## Decisions required before schema changes
1. Candidate identity and producer scope.
2. Exact retry versus changed payload behavior.
3. Review history representation.
4. Evidence cardinality and typed reference format.
5. Conflict directionality and resolution history.
6. Verification/freshness metadata ownership.
7. Atomicity and transaction ownership.
8. Retention, deletion, and secret policy.
9. Legacy-record compatibility.
10. Migration sequencing and adapter interface.

## Legacy compatibility
Legacy records may only have key/value, lifecycle status, one optional source-message ID, and timestamps. Do not assume typed multi-source evidence, verification, freshness, or conflict history exists.

When absent, preserve lifecycle semantics and represent other dimensions as unknown/unavailable where the contract permits. Never fabricate evidence. Any schema changes must follow the storage owner's additive migration process.

## Non-goals
This document does not authorize a migration, replace the existing storage contract, assert all entities already exist, or authorize changes to a teammate-owned adapter.

## Existing local SQLite and hosted storage details

This section preserves the branch's earlier implementation-focused document as a supplement to the team's conceptual handoff above. Verify it against the merged source before treating it as a current status claim.


## Runtime schema: local SQLite

SQLite is the current runtime source of truth. The schema is defined in `src/orbit/database/engine.py` and initialized/migrated on connection using `PRAGMA user_version`.

### `chat_messages`

| Column | Type / rule | Meaning |
|---|---|---|
| `id` | `INTEGER PRIMARY KEY` | Local SQLite message identifier. |
| `project` | `TEXT NOT NULL` | Exact project scope. |
| `conversation_id` | `TEXT NOT NULL`, default `default` | Conversation/source grouping. |
| `role` | `TEXT NOT NULL`, one of `user`, `assistant`, `system` | Message speaker. |
| `content` | `TEXT NOT NULL` | Stored message text. |
| `created_at` | `TEXT NOT NULL` | UTC creation timestamp in the repository's serialized format. |

Index: `(project, conversation_id, id)`.

### `project_memory`

| Column | Type / rule | Meaning |
|---|---|---|
| `id` | `INTEGER PRIMARY KEY` | Local SQLite decision identifier. |
| `project` | `TEXT NOT NULL` | Project scope. |
| `key` | `TEXT NOT NULL` | Decision key. |
| `value` | `TEXT NOT NULL` | Decision content. |
| `status` | `TEXT NOT NULL`, one of `draft`, `approved`, `deprecated` | Lifecycle state. |
| `source_message_id` | Nullable integer FK to `chat_messages(id)`, `ON DELETE SET NULL` | Source provenance; SQLite schema does not itself enforce same-project linkage, so repositories validate it. |
| `updated_at` | `TEXT NOT NULL` | UTC update timestamp. |

Unique constraint: `(project, key)`. Index: `(project)`. The persistence adapter returns `updated_at` for both `created_at` and `updated_at` on its public save response because the current table does not retain a separate creation timestamp.

### Full-text search structures

Migration 2 creates FTS5 virtual tables `chat_messages_fts` and `project_memory_fts`, with insert/update/delete triggers and a backfill. These are derived indexes, not authoritative records. FTS search is project-filtered and uses BM25; database initialization manages their schema.

## Local database file selection

1. `CELESTIN_DB_PATH` when configured.
2. Otherwise `MEMORY_BRIDGE_DB_PATH` for legacy installations.
3. Otherwise `data/memory_bridge.db` relative to the process working directory.

The configured database path is read by the engine module. Optional semantic vectors use a separate SQLite file (default suffix `.vectors.db`) and are rebuildable derived data. Optional PageIndex document/section data is managed by its own store configuration; it is not part of the two core tables above.

## Hosted schema draft (not runtime)

`docs/hosted-tenancy-schema.sql` is a non-idempotent fresh-database design artifact. It is not used by SQLite startup and this checkout has no PostgreSQL application adapter.

The draft contains:

- `celestin_users`: external issuer/subject identity mapping.
- `celestin_workspaces`: workspace record and archive/purge eligibility timestamps.
- `celestin_workspace_memberships`: owner/admin/member role and invited/active/suspended status.
- `celestin_projects`: workspace-scoped project slug and legacy project key.
- `celestin_project_memberships`: explicit per-project human read/write grants.
- `celestin_service_identities`: workspace-scoped service principals with active/revoked status.
- `celestin_service_project_grants`: explicit per-project service read/write grants.
- Hosted-form `chat_messages` and `project_memory`: UUID workspace/project scope, identity IDs, UTC timestamps, archive/purge eligibility, restrictive foreign keys, and same-project source-message provenance.

The PostgreSQL draft uses composite foreign keys to reject cross-workspace/project relationships and checks that writes imply reads. Foreign keys use `RESTRICT` to preserve history/provenance. The `purge_after` columns represent eligibility only; they do not implement retention enforcement or an authorized purge operation. A partial active-owner index accelerates queries but does not guarantee at least one active owner. The required application transaction/locking rule remains a known gap.

Do not treat the hosted SQL as a production migration or run it against a non-disposable database. Review [the hosted tenancy notes](docs/hosted-tenancy-spike.md) before any future hosted implementation.

## Migration and data-portability notes

- SQLite migration versions are applied in order and tracked by `PRAGMA user_version`.
- Local integer IDs are not interchangeable with hosted generated IDs. A future importer must map identifiers explicitly and preserve timestamps/source relationships.
- No SQLite-to-PostgreSQL import path is implemented here.
- Hosted migration, authentication, project authorization, retention policy, and owner lifecycle must be designed and tested separately.
