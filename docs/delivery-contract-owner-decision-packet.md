# Celestin Delivery Contract — Owner Decision Packet

**Purpose:** Obtain explicit role-owner decisions on delivery identity and producer-contract extensions before durable delivery persistence is designed. This packet is a request for decisions, not a change to the implementation baseline.

## Authority and current status

- [`SHARED_MEMORY_INTERFACE_CONTRACT_V0.3_DECISIONS.md`](SHARED_MEMORY_INTERFACE_CONTRACT_V0.3_DECISIONS.md) is the requester-authorized v0.3 implementation baseline. It does not claim that absent teammates have acknowledged it.
- [`delivery-identity-producer-contract-proposal.md`](delivery-identity-producer-contract-proposal.md) is explicitly **PROPOSAL PENDING EXPLICIT APPROVAL**. Nothing below upgrades it to approved.
- [`delivery-contract-decision-audit.md`](delivery-contract-decision-audit.md) classifies D-01–D-13 and records the dependencies summarized here.
- Repository ownership is role-level only. No individual decision-maker is identified in the checked-in sources.
- The present Capture implementation is a local prototype. Its fingerprint IDs, mutable candidate edits, string reviewer field, in-memory repository, and deduplicated/sorted source tuples are not evidence of producer conformance.

**Owner response convention:** For each open item, select one response and add a rationale. Leave it blank until the responsible role owner responds. A recommendation is not an approval.

`Owner response: [ ] Accept proposal  [ ] Reject proposal  [ ] Amend / need discussion`
`Owner rationale: __________________________________________________________`

## Gate A — Identity and trust

These decisions must be recorded before accepting or durably storing producer deliveries. The v0.3 key scopes below are already requirements; the open questions concern issuers, delivery shape, equality details, and trusted runtime binding.

### D-01a — Delivery cardinality and batch atomicity

**Question:** Does one `delivery_id` contain exactly one candidate revision, or may it contain a batch? If batching is required, must the batch be all-or-nothing, and what ordering, per-item receipt, and partial-rejection rules apply?

**V0.3 establishes:** A producer-generated `delivery_id` is unique within `(project_id, producer_id)` and retry behavior is defined for that key. Candidate revision identity is separate. It does not specify delivery cardinality or batch receipt shape.

**Options and consequences:**

1. **One candidate revision per delivery.** A receipt and equality projection have one candidate-revision outcome; constraints and retry arbitration stay small. Producers that currently batch must split deliveries and provide a stable ID for each item.
2. **Atomic batch delivery.** One delivery receipt covers an ordered set and all revisions commit or none do. This requires batch-level equality, locking, and rollback semantics; a single invalid member rejects the batch.
3. **Partially accepted batch.** The receipt needs durable per-item outcomes and retry semantics for accepted and rejected members. This is more flexible but substantially expands API, retention, and transaction design.

**Recommendation — PROPOSED, not approved:** Use one candidate revision per delivery for v1; if a producer needs batching, define a separate batch contract rather than implicit partial success.

**Dependencies:** D-01b, D-08, D-12. **Decision role:** Producer/Capture; Storage for receipt and atomicity design.

`Owner response: [ ] Accept proposal  [ ] Reject proposal  [ ] Amend / need discussion`
`Owner rationale: __________________________________________________________`

### D-01b — Candidate ID and revision issuers

**Question:** Who creates the stable `candidate_id`, and who assigns the sequential positive `candidate_revision`? Can the producer guarantee that IDs remain stable across corrections and that revisions are immutable, contiguous, and delivered without gaps or reordering?

**V0.3 establishes:** Candidate identity is `(project_id, producer_id, candidate_id)` with opaque, case-sensitive `candidate_id`; revision identity adds a positive sequential `candidate_revision`, beginning at 1 and increasing by exactly 1. A revision is immutable, and approval binds to that exact revision. The issuer of the candidate ID and revision number is not specified.

**Options and consequences:**

1. **Producer issues both values.** The producer must persist identity/revision state and retry gaps; storage validates immutability and sequence. This preserves producer continuity but requires an upstream durable identity source.
2. **Producer issues candidate ID; trusted service assigns revision.** The ingress service serializes changes and returns the assigned revision. Producer requests need an unambiguous correction command and receipt; producer must retain the assigned value for retries.
3. **Trusted service issues both.** This fits a service-owned candidate workflow but cannot be assumed for an external producer; it requires a stable producer delivery key and service mapping before candidate creation.

**Recommendation — PROPOSED, not approved:** Keep `candidate_id` producer-stable, and have the trusted ingestion boundary assign or validate the next revision under a serialized candidate write. Choose between producer-issued and service-assigned revisions only after confirming which component owns correction state.

**Dependencies:** D-01a, D-08, D-11, D-13. **Decision role:** Producer/Capture; Storage must accept the transaction and uniqueness implications.

`Owner response: [ ] Accept proposal  [ ] Reject proposal  [ ] Amend / need discussion`
`Owner rationale: __________________________________________________________`

### D-02 — Producer feasibility for delivery retry ID (baseline requirement; not reopened)

**V0.3 establishes:** The producer generates an opaque delivery ID unique within `(project_id, producer_id)`. Same normalized delivery payload returns the original receipt; changed payload under that ID returns `idempotency_conflict` without mutation. Authorization precedes disclosure.

**Confirmation requested:** Can the actual producer persist and resend the same `delivery_id` for every retry of one logical delivery, including after a timeout or uncertain response? Please provide a producer contract, implementation reference, or a precise limitation. This is a conformance check, not a request to reconsider the v0.3 rule.

**Options and consequences:** Confirmed producer support enables durable exact retry handling once D-08 specifies equality. If unavailable, delivery persistence cannot claim the required idempotency; the producer must change or a separately approved contract revision is needed. A transport-generated per-attempt ID is not an equivalent logical retry key.

**Recommendation — PROPOSED:** Supply a stable producer-generated ID and keep it unchanged for a logical retry. **Decision role:** Producer/Capture.

`Conformance evidence supplied: [ ] Yes  [ ] No  [ ] Needs discussion`
`Evidence / rationale: ______________________________________________________`

### D-08 — Separate delivery/revision equality profiles and receipt shape

**Question:** What exact immutable fields define (a) a repeated delivery and (b) the same candidate revision? What normalization, explicit-null rules, profile/version identifiers, canonical bytes, digest or direct-comparison method, and receipt fields are used? How does a retry compare with a previously stored version?

**V0.3 establishes:** Delivery retry and candidate-revision conflict outcomes are required. Conversion has a separate, fixed profile: `smic-conversion-payload-v1+jcs-rfc8785+sha256`, exact fields, direct SHA-256 of JCS UTF-8 bytes, and two golden vectors. The conversion payload deliberately excludes `delivery_id`; it must not be reused as a delivery/revision digest without an approved projection.

**Unspecified:** Delivery/revision equality projections, their versioning and hash preimages, receipt contents, whether stored canonical bytes or field comparisons are retained, and comparison behavior after a future profile change.

**Options and consequences:**

1. **Distinct versioned canonical projections** for delivery and revision, each with golden vectors; persist profile ID and digest (and optionally canonical bytes). Gives compact equality checks and explicit upgrade behavior, but requires complete specs and cross-runtime conformance.
2. **Compare immutable typed fields directly** using a documented field-by-field equality function; store accepted source fields and avoid a digest as the equality authority. Avoids a second hash profile, but requires the same rigorous null, ordering, normalization, and version-compatibility rules and may cost more to compare.
3. **Persist canonical bytes plus a version identifier** and compare bytes, optionally computing a digest only as an index. This is auditable but still requires defining canonical projection and migration/retention behavior.

**Recommendation — PROPOSED, not approved:** Specify distinct, versioned delivery and candidate-revision projections with explicit fields, nulls, and golden vectors; do not reuse the conversion hash. Preserve the accepted projection version with each receipt/revision. Decide whether the digest is authoritative or an optimization as part of that spec.

**Dependencies:** D-01, D-03, D-04, D-05, D-06, D-09, D-10, D-12. **Decision role:** Storage/Canonicalization with Producer/Capture for emitted fields and Product/Security for receipt disclosure/retention.

`Owner response: [ ] Accept proposal  [ ] Reject proposal  [ ] Amend / need discussion`
`Owner rationale: __________________________________________________________`

### D-11 — Authenticated producer binding and registry authority

**Question:** Which authenticated transport/service principal maps to a registered producer and project? Who may register, bind, disable, or retire producers and source namespaces, and how are allowed namespaces constrained per producer?

**V0.3 establishes:** Project is the authorization boundary. Producer IDs are project-registered and derived by trusted application code from authenticated producer/session context; payload identity cannot select a different producer. The record also requires project-scoped capabilities for approval/conversion/read. It does not provide an authenticator, registry administration policy, or runtime integration.

**Options and consequences:**

1. **Administrator-managed project registry plus credential-to-producer binding.** Registration and permissions are auditable and independent of payload; requires admin workflow, credential lifecycle, and registry tables/services.
2. **Trusted identity provider claims map to pre-provisioned producer IDs.** Reduces local credential mapping but depends on issuer/claim stability and explicit project/namespace authorization; never accept self-asserted claims in request data.
3. **Service-account registry with explicit project and namespace grants.** Supports machine producers, but needs revocation, rotation, and audit semantics before writes are enabled.

**Recommendation — PROPOSED, not approved:** Require an authenticated, server-verified principal mapped to a pre-registered producer/project grant; reject any request whose payload attempts to override that binding. Define a separate trusted administrative operation for registry changes.

**Dependencies:** D-01, D-04, D-07, D-12. **Decision role:** Product/Security for trusted identity and authorization; Producer/Capture for required producer identity; registry administrator role is not identified in current docs.

`Owner response: [ ] Accept proposal  [ ] Reject proposal  [ ] Amend / need discussion`
`Owner rationale: __________________________________________________________`

## Gate B — Lifecycle and semantics

### D-03 — Producer category/certainty values and timestamp fields

**Question:** What exact category and certainty values does the producer emit, and what are their semantics? Which timestamp fields are present, and which describe candidate creation versus source occurrence? What explicit crosswalk maps producer certainty to v0.3 values?

**V0.3 establishes:** The category-to-knowledge-type mapping is pinned; unknown categories reject. `candidate_created_at` is claimed event time, distinct from server-generated receipt/approval/conversion times. Conversion vectors include `certainty` values `medium` and `high`. Actual producer values are not established. Current Capture's `CandidateCertainty` values are `unqualified` and `uncertain`; this is not an approved crosswalk.

**Options and consequences:**

1. **Producer emits the v0.3 certainty vocabulary directly**, after the responsible owners define its allowed values. Simpler conversion, but requires an upstream change if producer semantics differ.
2. **Use an explicit, versioned producer-to-v0.3 crosswalk.** Supports differing vocabularies but must define lossiness, unknown values, and whether the original producer value is retained for audit/equality.
3. **Reject unsupported certainty values until a mapping is approved.** Safest against silent semantic drift, but blocks those producer deliveries.

**Recommendation — PROPOSED, not approved:** Reject unknown values and require a written, versioned crosswalk where producer and v0.3 vocabularies differ. Do not infer certainty from claim text. Keep category mapping already fixed by v0.3.

**Dependencies:** D-08, D-09. **Decision role:** Producer/Capture supplies actual values and meaning; Storage/Canonicalization and Product/Security approve the crosswalk where needed.

`Owner response: [ ] Accept proposal  [ ] Reject proposal  [ ] Amend / need discussion`
`Owner rationale: __________________________________________________________`

### D-04 — Source fields and namespace registry lifecycle

**Question:** Can the producer provide each approved field (`source_namespace`, `source_id`, optional opaque `source_revision`, locator, content hash, `source_occurred_at`) without inference? Who registers namespaces for a project, grants a producer use of them, and manages namespace disablement/retirement?

**V0.3 establishes:** Source identity is project + namespace + source ID (with revision and locator as source-reference qualifiers); namespace is project-registered; revision is opaque; timestamps distinguish claimed event time from service time; v1 content hashes use `sha256:` plus 64 lowercase hexadecimal digits. It does not establish producer field availability or registry lifecycle/authority.

**Options and consequences:**

1. **Central project namespace registry with per-producer allowlist.** Strongest enforcement and explicit provenance, but needs registry administration and storage constraints.
2. **Project-level registry only, with authorization checked by trusted application.** Simpler but permits any authorized producer to use any registered namespace unless application policy narrows it.
3. **Producer-specific namespaces registered by a trusted administrator.** Clear attribution, but migration/renaming and shared-source representation need explicit policy.

**Recommendation — PROPOSED, not approved:** Use stable project-registered namespaces, bind producer use through explicit grants, retain the namespace string on historical records, and never synthesize absent source metadata. Confirm exact producer field guarantees before adapter mapping.

**Dependencies:** D-07, D-08, D-09, D-11. **Decision role:** Producer/Capture for supplied fields; Product/Security and Storage for registry operations/constraints. No individual registry owner is documented.

`Owner response: [ ] Accept proposal  [ ] Reject proposal  [ ] Amend / need discussion`
`Owner rationale: __________________________________________________________`

### D-05 — Occurrence order, duplicates, and shared evidence (baseline requirement; conformance only)

**V0.3 establishes:** Occurrences are identified by project/producer/candidate/revision plus zero-based ordinal; declared order is preserved; duplicate occurrences remain distinct; multiple occurrences may reference one evidence record. The local Capture prototype currently deduplicates and sorts sources/evidence, so it does not demonstrate this behavior.

**Confirmation requested:** Can the real producer provide stable occurrence order and explicit occurrence-to-evidence links without collapsing duplicate appearances? If not, what upstream change is needed?

**Recommendation — PROPOSED:** Treat the v0.3 occurrence rule as fixed and make the producer conform; do not weaken it to fit current Capture behavior. **Decision role:** Producer/Capture.

`Conformance evidence supplied: [ ] Yes  [ ] No  [ ] Needs discussion`
`Evidence / rationale: ______________________________________________________`

### D-06 — Typed conflict endpoints (baseline requirement; conformance only)

**V0.3 establishes:** A conflict identifies a same-project, producer-qualified candidate revision; the relation is symmetric and stored once; an unavailable endpoint can remain pending; a conflict is a review-together signal, not a truth decision. A grouping key or plain candidate-ID string is insufficient.

**Confirmation requested:** Can the producer emit explicit typed endpoint references with producer, candidate, and revision identity, including a target not yet delivered? Cross-runtime ordering of those endpoints is separately D-10.

**Recommendation — PROPOSED:** Keep the v0.3 typed-endpoint requirement; do not reinterpret grouping hints as authored conflict declarations. **Decision role:** Producer/Capture; Storage/Canonicalization for D-10 ordering.

`Conformance evidence supplied: [ ] Yes  [ ] No  [ ] Needs discussion`
`Evidence / rationale: ______________________________________________________`

### D-07 — Producer/namespace retirement and pending candidates

**Question:** When a producer or source namespace is retired, which new writes stop, what happens to already accepted revisions, and may pending candidates still be reviewed/approved/converted? Are historical identity bindings retained, and can an ID ever be reused?

**V0.3 establishes:** Producer and source namespaces are project-registered and historical rows are project-scoped. It does not define retirement, ID reuse, or the pending-candidate lifecycle.

**Options and consequences:**

1. **Disable new submissions but retain registration/history; allow already accepted items to proceed under current authorization.** Preserves provenance and avoids dangling references, but needs a policy for security-driven revocation versus routine retirement.
2. **Disable all review/conversion as well as new submissions.** Strong shutdown behavior; can strand pending candidates and requires explicit audit/recovery handling.
3. **Hard-delete or recycle registry identity.** Simplifies active registry state but risks attribution ambiguity, broken foreign keys, and replay/authorization confusion; not recommended for durable history.

**Recommendation — PROPOSED, not approved:** Retain immutable historical identity and do not recycle IDs; distinguish routine retirement from security revocation, and decide explicitly whether accepted pending work may proceed.

**Dependencies:** D-04, D-11, D-12, D-13. **Decision role:** Product/Security for lifecycle outcomes; Producer/Capture and Storage for mechanics.

`Owner response: [ ] Accept proposal  [ ] Reject proposal  [ ] Amend / need discussion`
`Owner rationale: __________________________________________________________`

### D-09 — Timestamp and JCS numeric edge cases

**Question:** For timestamps with more than six fractional digits, leap seconds, `-00:00`, or unusual offsets, reject, preserve raw text plus normalize a UTC instant, or normalize/round? What exact integer range is accepted by the canonical payload, and are booleans rejected where an integer is required?

**V0.3 establishes:** Conversion timestamps normalize to UTC RFC 3339 with six fractional digits; canonical JSON uses integer values only and forbids floating point. It does not enumerate all edge-input behavior or precise integer bounds.

**Options and consequences:**

1. **Strict rejection of values not representable by the profile**, including unspecified precision or invalid/ambiguous forms; use a documented JCS-compatible integer range and explicit type checks. Deterministic and lossless at acceptance, but some producers must normalize before delivery.
2. **Normalize representable instants** and retain original producer text separately for audit; requires a precise rounding/truncation rule and decides whether equality compares raw or normalized time.
3. **Round/truncate to six digits.** Easiest ingress compatibility but can collapse distinct source timestamps and change replay equality; requires an explicit rule and golden vectors.

**Recommendation — PROPOSED, not approved:** Reject unsupported or ambiguous forms and out-of-profile numbers; never silently round. If preserving original timestamp text is required, store it separately from the normalized instant. Add edge golden vectors before persistence.

**Dependencies:** D-03, D-04, D-08, D-10. **Decision role:** Producer/Capture and Storage/Canonicalization.

`Owner response: [ ] Accept proposal  [ ] Reject proposal  [ ] Amend / need discussion`
`Owner rationale: __________________________________________________________`

### D-10 — Portable conflict endpoint comparator

**Question:** How are conflict arrays sorted identically across producer, service, and database runtimes when endpoint strings contain non-ASCII values? v0.3 names the tuple `(producer_id, candidate_id, candidate_revision)` but does not specify the string comparator.

**Options and consequences:**

1. **Unicode code-point ordering after NFC.** Natural in many language runtimes, but must be implemented identically and differs from locale/database collation.
2. **UTF-8 byte ordering after NFC for string members, numeric ordering for revision.** Explicit and locale-independent; producer and storage implementations must use byte comparison rather than locale collation.
3. **Require a restricted identifier alphabet.** Simplifies ordering but changes the v0.3 opaque/case-sensitive identifier space and therefore requires an explicit contract amendment.

**Recommendation — PROPOSED, not approved:** Use NFC-normalized UTF-8 byte order for producer and candidate strings and numeric order for revision; publish vectors with non-ASCII identifiers in at least two runtimes.

**Dependencies:** D-08 and D-11. **Decision role:** Storage/Canonicalization and Producer/Capture.

`Owner response: [ ] Accept proposal  [ ] Reject proposal  [ ] Amend / need discussion`
`Owner rationale: __________________________________________________________`

### D-12 — Do failed deliveries reserve delivery IDs?

**Question:** Does a deterministic rejected delivery (invalid payload, revision gap, retired producer, or authorization failure) create a durable terminal receipt that permanently consumes its `delivery_id`, or is an ID reserved only when accepted delivery/revision rows commit?

**V0.3 establishes:** Exact accepted retries return their original receipt and changed payload under a delivery ID conflicts. It does not define durable receipts for rejected attempts. A failed transaction must not leave a partial accepted graph under the v0.3 atomicity baseline.

**Options and consequences:**

1. **Reserve only on successful atomic acceptance.** Failed validation/precondition attempts can be retried if conditions change; simpler atomic graph. A client may receive different outcomes on retry after state changes.
2. **Persist selected deterministic rejection receipts.** Makes repeated rejection stable, but requires defining which failures are safe to disclose and how authorization, retention, and changed-payload conflicts apply to rejected IDs.
3. **Reserve every authenticated syntactically valid attempt.** Strong attempt audit but risks allowing transient errors or temporary authorization state to permanently consume producer IDs.

**Recommendation — PROPOSED, not approved:** Consume the delivery key only in the transaction that accepts and commits the complete delivery/revision graph; do not leave a receipt on transaction rollback. Decide separately whether a non-sensitive terminal rejection ledger is required.

**Dependencies:** D-01a, D-08, D-11, D-07, D-09. **Decision role:** Product/Security and Storage, with Producer/Capture for retry behavior.

`Owner response: [ ] Accept proposal  [ ] Reject proposal  [ ] Amend / need discussion`
`Owner rationale: __________________________________________________________`

### D-13a — `original_claim` and correction lineage

**Question:** Is `original_claim` an explicit producer field, a server-derived value from a specified immutable predecessor revision, or review-only metadata? For repeated corrections, what exact ancestor does it denote? How is lineage preserved if an earlier revision is unavailable?

**V0.3 establishes:** `original_claim` is nullable in the conversion payload. Corrections create new immutable revisions with fresh approval; after conversion a successor knowledge record names the prior knowledge record it supersedes. It does not specify the producer source or derivation of `original_claim`.

**Options and consequences:**

1. **Producer supplies lineage explicitly.** Preserves producer intent but requires stable predecessor references and equality rules.
2. **Service derives it from an immutable revision-history rule.** Avoids duplicated producer state but requires defining whether it means the immediately prior claim or the first claim and what to do if history has gaps.
3. **Keep it null unless review explicitly records it.** Avoids fabricated lineage; product consumers lose a convenience field but immutable revisions/supersession remain available.

**Recommendation — PROPOSED, not approved:** Do not synthesize `original_claim`; accept it only from a specified source, or leave null. Keep candidate revision identity separate from knowledge supersession.

**Dependencies:** D-01b, D-08, D-13b. **Decision role:** Producer/Capture for lineage semantics; Storage for representation.

`Owner response: [ ] Accept proposal  [ ] Reject proposal  [ ] Amend / need discussion`
`Owner rationale: __________________________________________________________`

### D-13b — Pending candidate behavior after producer retirement

**Question:** If a producer is retired while an already accepted candidate is awaiting review or conversion, may it still be approved/converted by a currently authorized reviewer, or must it be frozen? How is this distinguished from credential/producer revocation for a security incident?

**V0.3 establishes:** Rejected/revoked approval is terminal for that revision; a new revision is required for reconsideration. It requires current authorization at conversion. It does not define producer retirement or whether pending accepted work survives retirement.

**Options and consequences:**

1. **Routine retirement stops new deliveries but lets accepted candidates proceed under current project/review authorization.** Preserves queued work; incident revocation must be a separate stronger action.
2. **Retirement freezes all pending work.** Strong containment but requires an explicit recovery or migration path to avoid silently losing reviewable items.
3. **Make behavior configurable by retirement reason.** Supports routine and security cases but adds states, audit reasons, and test permutations.

**Recommendation — PROPOSED, not approved:** Distinguish routine retirement from security revocation; retain history and decide an explicit state transition for already accepted candidates.

**Dependencies:** D-07, D-11, D-13a. **Decision role:** Product/Security and Producer/Capture.

`Owner response: [ ] Accept proposal  [ ] Reject proposal  [ ] Amend / need discussion`
`Owner rationale: __________________________________________________________`

## Gate C — Implementation readiness

### Persistence decision gate

Durable producer delivery persistence is **blocked** until the identity/trust/lifecycle equality contract is complete. A relational unique key cannot supply a missing producer identity, define equality, authenticate the producer, or decide whether a failed attempt consumes an ID.

Before schema or adapter work begins, record decisions for D-01a/b, D-03, D-04, D-07, D-08, D-09, D-10, D-11, D-12, D-13a/b; capture D-02 feasibility evidence; and confirm D-05/D-06 producer conformance or a required upstream change. Then add an explicit approved version/extension to the authoritative contract. Do not edit that decision record merely to make blank responses appear complete.

**Separate v0.3 implementation baseline:** The requester-authorized conversion payload, approval policy, authorization-before-replay rule, revocation ordering, conversion uniqueness, atomicity, supersession, and unchanged MCP schemas remain applicable as written. A delivery extension must not reuse or silently change those conversion rules.

**Database invariants vs policy:** After the contract settles identities, SQL constraints can enforce project-scoped uniqueness, foreign keys, immutable revision keys, and occurrence ordinals. Product/security decides who is authorized, whether a producer is active, how revocation behaves, and what a caller may learn. Application code must authenticate and authorize before a protected lookup; a database key is not authentication.

### Proposed dependencies

```text
D-01 cardinality/issuers ─┐
D-03 certainty/field values ├─> D-08 equality projections + versioned vectors
D-04 source fields/registry ┤
D-05 occurrence conformance ┤
D-06 typed conflict conformance┘
                                      ├─> D-12 accepted/rejected receipt transaction
D-09 timestamp/numeric edges ─────────┤
D-10 conflict comparator ─────────────┘

D-07 retirement ───────────┬─> D-13b pending-item behavior
D-01 revision lifecycle ───┴─> D-13a correction lineage
D-11 trusted producer binding ─> any protected delivery write/replay

All Gate A/B decisions + producer contract evidence + reproducible vectors
                                      ─> schema and durable adapter design
```

### Acceptance checklist

- [ ] Producer and service responsibilities for delivery, candidate, and revision IDs are explicit; cardinality and gaps are defined.
- [ ] Producer feasibility for stable delivery retries is evidenced (not inferred from the local Capture fake/prototype).
- [ ] Delivery/revision equality projections and receipt shape are versioned separately from conversion hashing, with fixed vectors.
- [ ] Trusted authentication, project/producer binding, producer/namespace registration authority, grants, and revocation are defined.
- [ ] Producer category/certainty/source fields and source timestamp/revision semantics are confirmed; unknown values reject or follow a recorded crosswalk.
- [ ] Occurrence ordinal/order, duplicate preservation, and explicit occurrence-to-evidence links meet the v0.3 baseline in the actual producer.
- [ ] Typed conflict endpoints meet v0.3; cross-runtime ordering vectors are fixed.
- [ ] Namespace and producer retirement, pending candidate behavior, and ID reuse policy are recorded.
- [ ] Timestamp, numeric, Unicode, null/absent, and normalization boundary behavior has approved golden vectors.
- [ ] Failed-delivery ID reservation and uncertain-commit recovery semantics are decided.
- [ ] Correction lineage (`original_claim`) is sourced without synthesis; knowledge supersession remains a separate relation.
- [ ] Canonicalization tests pass with the declared JCS dependency in the intended environment. Cross-runtime conformance is separately tested; a local Python pass alone does not prove it.
- [ ] Required role-owner responses and any contract amendment are recorded in the authoritative decision/change record.

## Hypothetical flow — illustrates policy choices, not runtime behavior

Assume, solely for this example, the proposed one-revision-per-delivery rule and a trusted authentication binding. These assumptions are not current producer behavior and have no owner approval here.

1. An authenticated service principal is mapped by trusted application code to a registered `(project_id, producer_id)`. **V0.3 requirement:** payload-supplied identity cannot override this binding. **Implementation status:** current Capture has no such transport authentication or producer registry integration.
2. The producer sends `delivery_id = d-17`, `candidate_id = c-4`, and revision 1 with two declared source occurrences at ordinals 0 and 1 that reference the same evidence identity. **V0.3:** the delivery key is producer-generated and scoped by project/producer; candidate key is project/producer/candidate; occurrence duplicates/order are retained and may share one evidence record. **Open:** candidate/revision issuer, one-revision delivery cardinality, and exact delivery equality fields.
3. The same `delivery_id` and normalized payload arrive again after a lost response. **V0.3 outcome:** return the original receipt after authorization. **Open:** which fields are in the normalized delivery projection and how its version/digest is compared; no current durable delivery receipt exists.
4. The producer reuses `d-17` but changes the claim. **V0.3 outcome:** return `idempotency_conflict` without mutation. **Open:** exact comparison profile and rejection receipt behavior. Do not use the conversion hash as a substitute.
5. A later correction keeps candidate identity and submits revision 2 under a new delivery ID. **V0.3:** revision 2 is immutable, needs fresh approval, and cannot inherit approval. If revision 1 was converted, revision 2 produces a new knowledge record that atomically supersedes the prior one; ordinary retrieval hides superseded knowledge. **Open:** who assigns revision 2, `original_claim` lineage, and what happens if the producer was retired while the candidate was pending.

## Canonicalization verification plan

### Repository facts

- `README.md` documents the project environment: create `.venv`, activate it, then `python -m pip install -e .` (POSIX and Windows commands are both present).
- `pyproject.toml` requires Python `>=3.10` and declares `rfc8785>=0.1.4,<0.2` as a runtime dependency.
- `tests/evidence/test_canonicalization.py::test_approved_golden_vectors_match_canonical_bytes_and_hash` asserts the two approved v0.3 canonical JSON byte strings and SHA-256 digests from the decision record. Other tests cover normalization, ordering, optional values, and rejected input.
- The implementation imports the real `rfc8785` package; the tests must not replace it with a handwritten serializer.

### Current verification and limitation

At packet preparation, only system Python 3.13.9 / pytest 8.4.2 was available. `pip show rfc8785` reported the package missing; `.venv` and `.venv/validation` interpreters were absent. The focused test command below was executed and failed at collection with `ModuleNotFoundError: No module named 'rfc8785'`; zero tests ran. No package was installed. No cross-runtime conformance run was performed.

### Reproduction steps for an approved/prepared local environment (not executed here)

From the repository root, on Windows PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
python -c "import sys; print(sys.executable); print(sys.version)"
python -m pip show rfc8785
python -m pytest -p no:cacheprovider -o addopts='' tests/evidence/test_canonicalization.py
```

Record the interpreter/version and the `rfc8785` version shown by `pip show`, the pytest exit code, and whether both expected hashes matched. The test asserts exact canonical bytes plus the v0.3 digests `a24bbb757806b226435aedd9eea88f6d5f18f164c09421942083669151b45ec6` and `908a72554596df692c426567afe92ad8b57383246e7687e16b11dfb26d9342cd`.

The dependency-install command is a future environment setup step and was not run for this task. Cross-runtime conformance remains unverified until the same vectors run against the producer's actual implementation/runtime and an independently controlled implementation. Do not describe Python-only vectors as cross-runtime proof.

## Evidence reviewed

- `docs/SHARED_MEMORY_INTERFACE_CONTRACT_V0.3_DECISIONS.md` — final v0.3 decision record, especially §§1–5.
- `docs/RESURRECTION_INTERFACE_BOUNDARY_REVIEW.md` — local evidence limits and known Retrieval/Capture integration boundaries.
- `docs/delivery-identity-producer-contract-proposal.md` — non-normative delivery extension options.
- `docs/delivery-contract-decision-audit.md` — D-01–D-13 status and dependency analysis.
- `src/orbit/capture/model.py`, `src/orbit/capture/service.py` — current local candidate/request behavior.
- `src/orbit/evidence/model.py`, `src/orbit/evidence/service.py` — represented Evidence/Knowledge model and storage protocol.
- `src/orbit/evidence/canonicalization.py`, `tests/evidence/test_canonicalization.py`, `pyproject.toml`, `README.md` — conversion hash implementation, vectors, dependency, and setup instructions.

## Owner confirmation requested

Please return a response for every open item using its response field and a short rationale. D-02, D-05, and D-06 request conformance evidence against the v0.3 baseline; they do not invite changing that baseline. If a proposed extension is accepted or amended, record it in an authoritative versioned decision/change record before schema design begins.
