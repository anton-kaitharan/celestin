# Automatic Knowledge Capture prototype

## Status and boundary

`orbit.capture.KnowledgeCapturePipeline` is an isolated deterministic prototype.
It accepts only an explicitly supplied `CaptureInput`; it does not intercept
conversations, agent events, or repository activity. No LLM extractor currently
exists that is suitable for this job. PageIndex's optional model call navigates
document outlines and is not reused for knowledge extraction.

The pipeline is bound to one configured project identifier and rejects an input
whose project differs. This is a local data-isolation check, not authentication
or authorization. Callers must obtain trusted project scope before hosted
ingestion is considered. The prototype does not write to SQLite, PostgreSQL,
Capability 1, or MCP.

## Input and extraction

Input source types are `conversation_summary` and `execution_report`. Each
input has a required project ID and stable source ID, optional timezone-aware
timestamp/revision and Capability 1 `EvidenceSource` values, and either text or
structured `CaptureEvent` values. Text is limited to 100,000 characters,
structured input to 200 events, and each candidate claim to 2,000 characters.

Text extraction recognizes only explicit labeled lines, for example:

```text
Decision [storage]: Use SQLite for local state because it works offline.
Discovery: The index supports strict and relaxed FTS queries.
Bug fix: Normalize a path before computing its cache key.
Completed: Add project-scoped authorization checks.
Incomplete: Finish the PostgreSQL adapter integration.
Blocker: Waiting for an agreed evidence persistence contract.
Constraint: Preserve existing MCP tool arguments.
Implementation detail: Inspect files at a pinned Git revision.
```

`[storage]` is an optional explicit conflict key. Candidates with the same
category and conflict key but different normalized claims are all kept and
linked as alternatives. There is no semantic conflict detector. Structured
events can provide the category, claim, conflict key, evidence, and locator
directly. Unlabeled prose is ignored; no decision, completion, rationale, or
test result is inferred from it.

The pipeline stores candidate text, source IDs/locators, optional evidence
references, and review metadata only. It does not retain the supplied summary
or full execution report. Secret-like candidate statements and evidence
references matching common credential patterns are suppressed; suppression
records contain the source locator and reason but not the candidate text. This
pattern check is a safeguard, not a general secret scanner. Callers should
still avoid sending secrets.

## Candidate identity and review

Candidate IDs are deterministic for the project, category, normalized claim,
and optional conflict key. Whitespace/case-normalized repeats merge source and
evidence references while a candidate is pending or needs evidence. Replaying
the same input is idempotent within the in-memory repository. Once approved or
rejected, a candidate is immutable: an exact retry returns the existing record,
while changed claim text under the same normalized identity, certainty, source
references, or evidence is reported in `CaptureResult.suppressed` with reason
`terminal_candidate_immutable`. The terminal record and its review history are
left unchanged. A materially different claim or conflict key has a different
candidate ID and starts pending review; approval or rejection is never
inherited.

Distinct claims remain separate, including conflicting claims. Conflict links
are symmetric while both candidates remain reviewable. If a new claim conflicts
with an already approved or rejected candidate, the new candidate links to the
terminal candidate, but the terminal candidate's prior conflict relationships
are not backfilled or changed after review.
For structured input that updates a candidate more than once or links later
conflicts in the same batch, `CaptureResult` returns the final saved version
for each returned candidate ID, so its source and conflict references agree
with the review queue at the end of that call.

New candidates start `pending_review` and `unverified`. Uncertainty words mark
the candidate `uncertain`, but do not change the claim to a fact. `unqualified`
only means the source text contained no recognized uncertainty phrase; it is
not a confidence or verification result. Reviewers can edit pending candidates,
request evidence, approve, or reject them. Candidate edits retain the original
claim and source links. Rejected and approved entries are terminal in this
prototype, including their source/evidence associations and conflict links.
New information for a terminal candidate is explicitly suppressed rather than
silently attached. Reviewer IDs and timestamps are supplied metadata, not
authenticated identities.

Approval changes only review status and metadata. It does not verify the claim,
write shared memory, or make the candidate available to project reconstruction.
The queue is backed by `InMemoryCaptureRepository`; all queue state is lost when
the process exits. It is a testable prototype repository, not durable storage.

## Capability 1 and 2 integration seam

Capture reuses Capability 1's `EvidenceSource`, `EvidenceType`, and
`VerificationStatus`. `evidence_sources_for(candidate, target_project=...)`
maps the capture source reference to a Capability 1 `EvidenceType.OTHER`
reference and retains any additional evidence links. It requires the candidate
project to match the explicit target project and accepts only candidates whose
review status is `approved`. The project match is a domain association guard,
not authentication; callers still need trusted authorization scope before
writing. `capture_provenance_for(candidate, target_project=...)` creates the
storage-neutral provenance envelope carried by `KnowledgeRecord`. It accepts
only approved candidates whose source verification status is unverified;
approval does not set knowledge verification.

The original capture source type, ID, locator, revision, and occurrence time
remain in capture provenance. Mapped `EvidenceType.OTHER` records remain the
authoritative resolution references; `EvidenceRecord.captured_at` is persistence
time and is distinct from source occurrence time. Candidate identity, category,
certainty, review metadata, original edited claim, and conflict candidate IDs
also remain in the provenance envelope. Reviewer labels are not authenticated
identities.

The existing `KnowledgeRecord` gets an optional `capture_provenance` field and
Capability 2 returns it with the knowledge and evidence record IDs. Current
category mappings are explicit; incomplete work and blockers are rejected until
the shared-memory owner agrees on dedicated types or an equivalent mapping.
Rejected and pending candidates are not accepted records. Capability 2 still
reads only supplied `KnowledgeBundle` values and has no review-queue reader.

Before persistent integration, owners must agree on how adapters serialize and
round-trip `capture_provenance`, enforce unique `(project, candidate_id)`
idempotency, query conflicts, and expose approved records under trusted project
scope. This prototype intentionally does not define a database schema, hosted
authorization path, API, or MCP tool.
