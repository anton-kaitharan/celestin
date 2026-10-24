# Project-State Reconstruction prototype

## Status and boundaries

`orbit.project_state.ProjectStateReconstructor` is a deterministic, read-only
prototype. Callers provide an explicit repository root and project identifier;
the inspector requires that path to be the Git worktree root, resolves a commit,
then reads tracked blobs with Git. It strips inherited `GIT_*` environment
overrides before invoking Git so an ambient `GIT_DIR` or worktree setting cannot
redirect inspection. It does not open arbitrary paths, inspect
untracked/worktree changes, write to the repository or a database, call MCP,
run tests, or contact remote services.

The snapshot includes tracked `src/**/*.py` artifacts, unchecked Markdown tasks,
TODO/FIXME-style markers, and bullets under headings named Open Questions,
Unresolved, Blockers, or Known Limitations. A source file's presence is a
verified repository fact, not proof that the feature works. Checklist-complete
items remain unverified. Open-work markers and next-step suggestions are
inferences with source references.

Each finding carries Git object/revision/path/line references or a Capability 1
record/source reference. A historical commit is reported as historical relative
to the checked-out `HEAD`. Evidence references classify whether the source was
checked at the inspected revision; this is separate from the claim's status.
An older saved repository-file revision whose path is absent is marked stale;
hashes, when present, are reported as unchecked because the Capability 1 model
does not specify a hash algorithm.
Recorded decisions preserve their claim and links, but their applicability is
unknown until a real check compares the decision with the inspected state.
Conflicting records remain separate; the prototype does not choose a winner or
infer semantic contradictions between ordinary text claims. Explicit
`capture_provenance.conflicts_with` links are treated as unresolved conflict
signals. When only one endpoint carries a link (which can happen because
reviewed capture candidates are immutable), reconstruction flags both endpoints
if both are available in the supplied knowledge bundle. It does not modify or
resolve either record. Explicit knowledge verification status `conflicting` is
also preserved as a conflict classification.

The inspector does not report tests as passing. That requires a future trusted,
identifiable test-result source containing at least the result ID, command,
revision, and outcome. It does not inspect CI providers or test-run databases.
Saved completed-work claims are shown as recorded knowledge, usually unknown or
unverified, until repository evidence checks them. Saved open questions are
included in the unresolved list.

## Capability 1 integration seam

The prototype accepts an optional `ReadOnlyKnowledgeSource`, already bound by
its caller to the authorized project, whose `list_records()` returns
`KnowledgeBundle` values from `orbit.evidence.model`. This is a narrow read-only
domain seam, not a persistence adapter. The current Capability 1 prototype
exposes no list operation and has no SQLite/PostgreSQL adapter, so this seam is
not wired to its fake test store or to hosted authorization. An integration
requires an approved project-scoped list contract and trusted request scope;
the caller must not pass a user-selected project string as authorization.

Memory records are not treated as truth based on stored `verified` metadata.
This is deliberate because Capability 1 currently accepts a free-form checker
label. Stored `stale` status remains separate from source inspection and
classifies a current-lifecycle finding as stale; reconstruction does not promote
it because a referenced file happens to exist. Source staleness is classified
separately when the inspected revision shows a missing referenced path.
Evidence whose project does not match the requested project is omitted from a
bundle and reported as a limitation; the claim itself remains unknown when no
in-scope evidence remains.
Superseded/archived records are historical; current decisions remain
unverified or unknown and carry `applicability=unknown` until separately
checked. File references are checked for presence at the selected Git revision;
other reference types remain unknown without a resolver.
When a `KnowledgeRecord` contains Capability 3 provenance, findings preserve that
envelope, the knowledge record ID, and each evidence record ID. Stored evidence
locator, hash, resolution status, verification status, and checker metadata are
carried separately from the reconstruction's source-presence classification.
Knowledge-level checker metadata and lifecycle/supersession links are also
preserved as recorded fields; caller-supplied checker labels are not identity
proof.

## Output and limitations

`ProjectStateSnapshot` contains project/repository identifiers, the resolved
commit, implementation artifacts, incomplete items, unresolved items, saved
decisions and other recorded knowledge, next-step suggestions, and limitations.
Findings have stable IDs derived from commit, finding content, and evidence
locators, and are sorted for repeatable output.

The scanners are intentionally lexical. They can miss tasks written without a
recognized marker and can report stale TODOs or misleading headings. They do
not establish runtime correctness, test outcomes, feature completeness,
blockers outside the repository, or decision applicability. Conflicting prose
is preserved only when an upstream record marks it conflicting through
verification status or an explicit capture conflict link; automatic
natural-language conflict detection is not implemented. Legacy retrieval does
not consume Capability 1 memory and its DTO has no verification, freshness, or
conflict fields, so these distinctions are available through project-state
reconstruction only until the shared-memory/retrieval contract is coordinated.

Future shared-memory integration should supply only records authorized for the
active project and preserve source IDs. Existing Celestin retrieval may later
help select evidence relevant to a resume request, but this prototype does not
call retrieval or replace the selector/System 1 experiment.
