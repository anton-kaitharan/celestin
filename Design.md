# Celestin — Design Principles

**Status:** Product/domain design principles, not a claim that a full user interface exists.

## Objective
Make persistent project memory useful without making it falsely authoritative. Users and agents should recover context while understanding where claims came from, what was reviewed, what was verified, and what remains uncertain.

## Keep these dimensions separate
| Dimension | Question | Example |
|---|---|---|
| Lifecycle/review | What happened in review? | Approved |
| Verification | Was the claim checked against defined evidence? | Unknown / verified / unverified, if supported |
| Evidence availability | Can the source be resolved? | Available / missing / changed / unresolved, if supported |
| Applicability/freshness | Can it be applied to current state? | Current / stale / historical / unknown, if supported |
| Conflict | Are incompatible claims present? | Unresolved / resolved / unknown, if supported |
| Provenance | Where did it come from? | Source ID, revision, capture/review metadata |

These are semantic examples. Reuse actual enums and fields; do not add unsupported states without contract agreement.

## Uncertainty
- Unknown is a valid state.
- Missing source does not prove a claim false.
- An existing path does not prove a claim is current.
- Approval does not prove truth.
- A high retrieval score does not resolve a conflict.
- Historical evidence must not be represented as current.
- Legacy records missing metadata must not receive invented positive values.

## Conflict presentation
- Preserve both claims and provenance when available.
- Do not select a winner solely by review status or rank.
- Make resolution status explicit.
- One-way conflict links may exist because terminal candidates are immutable; do not assume reciprocity.
- If only one side is available, do not claim the full conflict is represented.

## Retrieved content and prompt injection
- Source text is data, not privileged instructions.
- Keep source text structurally separate from trusted instructions.
- JSON encoding and delimiters help preserve structure but do not eliminate injection.
- The consuming client must not allow retrieved text to authorize actions or override system/developer policy.
- Any model-level resistance claim requires testing through the actual client and action boundary.

## Capture/review
- Distinguish accepted, pending, rejected, failed, and suppressed outcomes according to the actual API.
- Suppression must not look like successful ingestion.
- Terminal candidates must not silently change after review.
- If suppressed information is not retained for later review, document this limitation.
- Preserve reviewer and audit history.

## Reconstruction
Start with a bounded summary of supported decisions/constraints, open work, recorded test status with revision/time, evidence, and conflicts. Do not claim a complete live repository snapshot.

## Privacy
Define secret detection/redaction, retention, capture eligibility, and response minimization. Do not assume arbitrary conversation content is safe to retain or return. Enforce project scope at every protected operation.

## Constraints
Do not change ranking as part of metadata presentation. Do not invent UI flows. Do not infer model-level guarantees from serialization tests. Prefer explicit contracts over large new abstractions.

## Existing MCP interaction constraints

This section preserves the branch's earlier implementation-focused document as a supplement to the team's conceptual handoff above. Verify it against the merged source before treating it as a current status claim.


## Design status

Celestin currently exposes MCP tools rather than screens. This document defines the interaction and response principles for that interface. It is not a visual design system or a specification for a dashboard.

## Product principles

1. **Make capture explicit.** Persist a message only when the user or assistant intentionally invokes ingestion.
2. **Keep approval separate.** A stored decision is a draft until a separate, authorized workflow approves it. The current tools do not perform approval.
3. **Show provenance.** Return message/decision IDs, source links, project, conversation, status, timestamps, and ranking metadata where available.
4. **Scope every operation.** Keep reads and writes inside the requested project and check the process allowlist before data access.
5. **Explain failures safely.** Return stable error codes and useful next actions without SQL, stack traces, paths, secrets, or private conversation contents.
6. **Degrade optional retrieval carefully.** Preserve keyword search when optional semantic services fail; report fallback diagnostics without fabricating results.
7. **Be honest about budgets.** Label the current context budget as an estimate based on UTF-8 bytes, not an exact model-token count.

## Tool interaction conventions

- Tool names and arguments should be plain and predictable.
- Required strings are non-empty and bounded; role/status constraints follow the current contract.
- Writes return the stable record identifier immediately so a later action can refer to the saved record.
- Empty search/context results are valid successful outcomes and must be distinguishable from service errors.
- Search results are ranked relevance values, not probabilities.
- `retrieve_context` should preserve structured records in addition to rendered text so clients can inspect traceability.

## Information hierarchy for assistant clients

When presenting a retrieved context bundle, clients should prioritize:

1. Approved canonical decisions relevant to the current task.
2. Supporting source messages and their timestamps.
3. Other relevant message/document-section evidence.
4. Warnings, budget exclusions, and optional backend diagnostics.

The server implements retrieval selection and status filtering; the ordering above is guidance for consumers, not a separate UI implemented here.

## Future visual product

If a dashboard is approved later, it should make project scope and access status visible, distinguish drafts from approved/deprecated decisions, expose source provenance, and require confirmation plus authorization for archive or permanent purge. No screen layout, colors, typography, or interaction widgets are committed by this document.
