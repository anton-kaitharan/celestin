"""Read-only project-state reconstruction from a pinned Git tree."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from pathlib import Path
from typing import Protocol, Sequence

from orbit.evidence.model import (
    EvidenceRecord,
    EvidenceType,
    KnowledgeBundle,
    LifecycleStatus,
    VerificationStatus,
)

from .model import (
    EvidenceClass,
    EvidenceReference,
    FindingKind,
    ProjectStateSnapshot,
    StateFinding,
)


class RepositoryInspectionError(RuntimeError):
    """Git could not safely inspect the requested repository revision."""


class ReadOnlyKnowledgeSource(Protocol):
    """A caller-bound, read-only source of Capability 1 records.

    An implementation must already be authorized for one project. This
    prototype does not provide an adapter for the Capability 1 fake store.
    """

    def list_records(self) -> Sequence[KnowledgeBundle]: ...


_TEXT_SUFFIXES = {".md", ".rst", ".txt", ".py", ".toml", ".yaml", ".yml"}
_TASK_MARKER = re.compile(r"\b(?:TODO|FIXME|XXX|NOT_IMPLEMENTED)\b", re.IGNORECASE)
_OPEN_CHECKBOX = re.compile(r"^\s*(?:[-*+]\s*)?\[\s\]\s*(.+)$")
_DONE_CHECKBOX = re.compile(r"^\s*(?:[-*+]\s*)?\[[xX]\]\s*(.+)$")
_UNRESOLVED_HEADING = re.compile(
    r"^\s{0,3}#{1,6}\s*(open questions?|unresolved(?: items?)?|blockers?|known limitations)\b.*$",
    re.IGNORECASE,
)


class ProjectStateReconstructor:
    """Inspect a repository at a commit without reading or changing its worktree.

    This internal prototype accepts a repository path directly and does not
    authenticate a caller or bind that path to ``project_id``. Applications
    must resolve the path from a trusted project registry before construction;
    do not expose this constructor to untrusted requests.
    """

    def __init__(
        self,
        repository_root: str | Path,
        project_id: str,
        *,
        knowledge_source: ReadOnlyKnowledgeSource | None = None,
    ) -> None:
        if not isinstance(project_id, str) or not project_id.strip() or "\x00" in project_id:
            raise ValueError("project_id must be a non-empty string")
        try:
            root = Path(repository_root).expanduser().resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise RepositoryInspectionError("repository root is unavailable") from exc
        if not root.is_dir():
            raise RepositoryInspectionError("repository root must be a directory")
        self._root = root
        self._project_id = project_id.strip()
        self._knowledge_source = knowledge_source

    def reconstruct(self, revision: str = "HEAD") -> ProjectStateSnapshot:
        """Build a deterministic snapshot from blobs reachable at ``revision``."""
        if not isinstance(revision, str) or not revision.strip() or "\x00" in revision:
            raise ValueError("revision must be a non-empty Git revision")
        self._require_repository_root()
        commit = self._git_text(
            "rev-parse", "--verify", "--end-of-options", f"{revision.strip()}^{{commit}}"
        )
        head = self._git_text("rev-parse", "--verify", "HEAD^{commit}")
        is_current_head = commit == head
        is_historical = self._is_ancestor(commit, head) and not is_current_head
        revision_class = (
            EvidenceClass.VERIFIED
            if is_current_head
            else EvidenceClass.HISTORICAL
            if is_historical
            else EvidenceClass.UNKNOWN
        )
        blobs = self._list_blobs(commit)
        text_blobs = {
            path: self._read_blob(oid).decode("utf-8", errors="replace")
            for path, oid in blobs.items()
            if Path(path).suffix.lower() in _TEXT_SUFFIXES
        }

        implemented: list[StateFinding] = []
        incomplete: list[StateFinding] = []
        unresolved: list[StateFinding] = []
        recorded_knowledge: list[StateFinding] = []
        for path, oid in sorted(blobs.items()):
            if path.startswith("src/") and path.endswith(".py"):
                implemented.append(
                    self._finding(
                        commit,
                        FindingKind.IMPLEMENTED,
                        f"Tracked Python source artifact exists at {path}.",
                        revision_class,
                        (EvidenceReference("git_blob", oid, commit, path, classification=revision_class),),
                        rationale="This verifies file presence at the inspected revision, not runtime behavior.",
                    )
                )

        for path, content in sorted(text_blobs.items()):
            lines = content.splitlines()
            for number, line in enumerate(lines, start=1):
                reference = self._file_reference(blobs[path], commit, path, number, revision_class)
                open_match = _OPEN_CHECKBOX.match(line)
                done_match = _DONE_CHECKBOX.match(line)
                if open_match:
                    incomplete.append(
                        self._finding(
                            commit,
                            FindingKind.INCOMPLETE,
                            f"Open checklist item: {open_match.group(1).strip()}",
                            EvidenceClass.INFERRED,
                            (reference,),
                            rationale="An unchecked item is evidence of recorded open work, not proof of implementation status.",
                        )
                    )
                elif done_match:
                    implemented.append(
                        self._finding(
                            commit,
                            FindingKind.IMPLEMENTED,
                            f"Checklist marks complete: {done_match.group(1).strip()}",
                            EvidenceClass.UNVERIFIED,
                            (reference,),
                            rationale="The completion marker is recorded; implementation was not independently checked.",
                        )
                    )
                marker = _TASK_MARKER.search(line)
                if marker:
                    incomplete.append(
                        self._finding(
                            commit,
                            FindingKind.INCOMPLETE,
                            f"Unfinished-work marker: {line.strip()}",
                            EvidenceClass.INFERRED,
                            (reference,),
                            rationale="A TODO/FIXME-style marker suggests unfinished work and may need human triage.",
                        )
                    )
            unresolved.extend(
                self._unresolved_sections(commit, path, blobs[path], lines, revision_class)
            )

        decisions, recorded_knowledge, memory_unresolved, memory_limitations = self._read_memory(commit, blobs)
        unresolved.extend(memory_unresolved)
        limitations = [
            "Inspection reads tracked Git blobs only; untracked and working-tree changes are not included.",
            "File presence and text markers do not establish that code works or a feature is complete.",
            "No test result is reported unless a future trusted source supplies an identifiable result record.",
            "Decision applicability is unknown unless an explicit verification process checks it against this revision.",
        ]
        limitations.extend(memory_limitations)

        evidence_refs = tuple(
            ref
            for finding in (*incomplete, *unresolved, *decisions, *recorded_knowledge)
            for ref in finding.evidence
        )
        if incomplete or unresolved:
            next_steps = (
                self._finding(
                    commit,
                    FindingKind.NEXT_STEP,
                    "Review the recorded open-work markers and unresolved items, then select a task with an owner and verification evidence.",
                    EvidenceClass.INFERRED,
                    _unique_refs(evidence_refs),
                    rationale="This recommendation follows from explicit TODO, unchecked, or unresolved-section evidence.",
                ),
            )
        elif decisions or recorded_knowledge:
            next_steps = (
                self._finding(
                    commit,
                    FindingKind.NEXT_STEP,
                    (
                        "Check the applicability of the recorded decisions against the inspected revision before relying on them."
                        if decisions
                        else "Verify recorded completed work against the inspected repository before treating it as implemented."
                    ),
                    EvidenceClass.INFERRED,
                    _unique_refs(evidence_refs),
                    rationale="The memory records do not independently establish current implementation or decision applicability.",
                ),
            )
        else:
            tree_ref = EvidenceReference(
                "git_tree",
                commit,
                commit,
                detail="tracked tree inspection",
                classification=revision_class,
            )
            next_steps = (
                self._finding(
                    commit,
                    FindingKind.NEXT_STEP,
                    "Record explicit open work and decision applicability evidence before choosing a project task.",
                    EvidenceClass.INFERRED,
                    (tree_ref,),
                    rationale="No supported open-work or decision record was available to rank a more specific next task.",
                ),
            )

        return ProjectStateSnapshot(
            project_id=self._project_id,
            repository_id=self._root.name,
            repository_root=str(self._root),
            inspected_revision=commit,
            is_current_head=is_current_head,
            implemented=tuple(sorted(implemented, key=lambda item: item.finding_id)),
            incomplete=tuple(sorted(incomplete, key=lambda item: item.finding_id)),
            unresolved=tuple(sorted(unresolved, key=lambda item: item.finding_id)),
            decisions=tuple(sorted(decisions, key=lambda item: item.finding_id)),
            recorded_knowledge=tuple(sorted(recorded_knowledge, key=lambda item: item.finding_id)),
            next_steps=next_steps,
            limitations=tuple(dict.fromkeys(limitations)),
        )

    def _require_repository_root(self) -> None:
        top = self._git_text("rev-parse", "--show-toplevel")
        try:
            resolved_top = Path(top).resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise RepositoryInspectionError("Git returned an unavailable repository root") from exc
        if resolved_top != self._root:
            raise RepositoryInspectionError("repository_root must be the Git worktree root")

    def _git_text(self, *args: str) -> str:
        return self._git(*args).decode("utf-8", errors="replace").strip()

    def _git(self, *args: str) -> bytes:
        try:
            result = subprocess.run(
                ["git", "-C", str(self._root), *args],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                env=self._git_environment(),
            )
        except OSError as exc:
            raise RepositoryInspectionError("Git could not be started") from exc
        if result.returncode:
            message = result.stderr.decode("utf-8", errors="replace").strip()
            raise RepositoryInspectionError(message or "Git inspection failed")
        return result.stdout

    def _list_blobs(self, commit: str) -> dict[str, str]:
        raw = self._git("ls-tree", "-r", "-z", "--full-tree", commit)
        result: dict[str, str] = {}
        for entry in raw.split(b"\0"):
            if not entry:
                continue
            try:
                metadata, raw_path = entry.split(b"\t", 1)
                _mode, object_type, oid = metadata.decode("ascii").split(" ", 2)
            except (ValueError, UnicodeDecodeError) as exc:
                raise RepositoryInspectionError("Git returned a malformed tree entry") from exc
            if object_type == "blob":
                result[raw_path.decode("utf-8", errors="surrogateescape")] = oid
        return result

    def _read_blob(self, oid: str) -> bytes:
        return self._git("cat-file", "blob", oid)

    def _is_ancestor(self, possible_ancestor: str, commit: str) -> bool:
        try:
            result = subprocess.run(
                ["git", "-C", str(self._root), "merge-base", "--is-ancestor", possible_ancestor, commit],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                env=self._git_environment(),
            )
        except OSError as exc:
            raise RepositoryInspectionError("Git could not be started") from exc
        if result.returncode not in (0, 1):
            raise RepositoryInspectionError("Git history could not be inspected")
        return result.returncode == 0

    @staticmethod
    def _git_environment() -> dict[str, str]:
        # Environment overrides can redirect Git to a different repository or
        # external object store despite using ``git -C`` with the approved root.
        env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        env["GIT_OPTIONAL_LOCKS"] = "0"
        env["GIT_TERMINAL_PROMPT"] = "0"
        return env

    def _file_reference(
        self, oid: str, commit: str, path: str, line: int, revision_class: EvidenceClass
    ) -> EvidenceReference:
        return EvidenceReference(
            "git_blob",
            oid,
            commit,
            path,
            line,
            line,
            classification=revision_class,
        )

    def _unresolved_sections(
        self, commit: str, path: str, oid: str, lines: list[str], revision_class: EvidenceClass
    ) -> list[StateFinding]:
        findings: list[StateFinding] = []
        active = False
        for number, line in enumerate(lines, start=1):
            if line.startswith("#"):
                active = bool(_UNRESOLVED_HEADING.match(line))
                if active:
                    findings.append(
                        self._finding(
                            commit,
                            FindingKind.UNRESOLVED,
                            f"Unresolved section: {line.lstrip('#').strip()}",
                            EvidenceClass.INFERRED,
                            (self._file_reference(oid, commit, path, number, revision_class),),
                            rationale="The document labels this section as open, unresolved, blocked, or a limitation.",
                        )
                    )
                continue
            if not active or not line.strip():
                continue
            if line.lstrip().startswith(("-", "*", "+")):
                findings.append(
                    self._finding(
                        commit,
                        FindingKind.UNRESOLVED,
                        f"Recorded unresolved item: {line.strip().lstrip('-*+ ').strip()}",
                        EvidenceClass.INFERRED,
                        (self._file_reference(oid, commit, path, number, revision_class),),
                        rationale="This item appears under a section explicitly labeled unresolved/open/blocked.",
                    )
                )
        return findings

    def _read_memory(
        self, commit: str, blobs: dict[str, str]
    ) -> tuple[list[StateFinding], list[StateFinding], list[StateFinding], list[str]]:
        if self._knowledge_source is None:
            return [], [], [], ["Capability 1 knowledge is not connected; no saved knowledge records were loaded."]
        try:
            bundles = self._knowledge_source.list_records()
        except Exception as exc:
            return [], [], [], [f"Capability 1 knowledge could not be read: {type(exc).__name__}."]

        ignored_out_of_scope = False
        ignored_out_of_scope_evidence = False
        project_bundles: list[KnowledgeBundle] = []
        conflicting_candidate_ids: set[str] = set()
        for bundle in bundles:
            record = bundle.record
            if record.project != self._project_id:
                ignored_out_of_scope = True
                continue
            project_bundles.append(bundle)
            provenance = record.capture_provenance
            if provenance is not None and provenance.conflicts_with:
                # Terminal capture candidates are immutable, so a conflict can
                # be linked from only one endpoint. Project that relation onto
                # both available records without mutating stored provenance.
                conflicting_candidate_ids.add(provenance.candidate_id)
                conflicting_candidate_ids.update(provenance.conflicts_with)

        decisions: list[StateFinding] = []
        recorded: list[StateFinding] = []
        questions: list[StateFinding] = []
        for bundle in project_bundles:
            record = bundle.record
            project_evidence = tuple(
                item for item in bundle.evidence if item.project == self._project_id
            )
            if len(project_evidence) != len(bundle.evidence):
                ignored_out_of_scope_evidence = True
            evidence = tuple(
                self._memory_evidence_ref(
                    item,
                    commit,
                    blobs,
                )
                for item in project_evidence
            )
            provenance = record.capture_provenance
            has_capture_conflict = provenance is not None and (
                provenance.candidate_id in conflicting_candidate_ids
                or bool(provenance.conflicts_with)
            )
            if record.lifecycle_status is not LifecycleStatus.CURRENT:
                classification = EvidenceClass.HISTORICAL
            elif (
                record.verification_status is VerificationStatus.CONFLICTING
                or has_capture_conflict
            ):
                classification = EvidenceClass.CONFLICTING
            elif record.verification_status is VerificationStatus.STALE:
                classification = EvidenceClass.STALE
            elif not evidence or any(
                item.classification in {EvidenceClass.UNKNOWN, EvidenceClass.STALE}
                for item in evidence
            ):
                classification = EvidenceClass.UNKNOWN
            else:
                classification = EvidenceClass.UNVERIFIED
            if not evidence:
                evidence = (
                    EvidenceReference(
                        "knowledge_record",
                        record.knowledge_id,
                        detail="record has no supporting evidence links",
                    ),
                )
            target = (
                decisions
                if record.knowledge_type.value == "decision"
                else questions
                if record.knowledge_type.value == "open_question"
                else recorded
            )
            kind = (
                FindingKind.DECISION
                if record.knowledge_type.value == "decision"
                else FindingKind.UNRESOLVED
                if record.knowledge_type.value == "open_question"
                else FindingKind.MEMORY
            )
            if has_capture_conflict:
                rationale = (
                    "Capture provenance links this claim to a conflicting candidate; "
                    "no resolution is recorded, so neither claim is selected as settled."
                )
            elif record.verification_status is VerificationStatus.STALE:
                rationale = (
                    "The saved knowledge record is marked stale; its current applicability "
                    "has not been re-established by reconstruction."
                )
            else:
                rationale = (
                    "Saved claims are preserved; current applicability or implementation "
                    "is not inferred from stored status."
                )
            target.append(
                self._finding(
                    commit,
                    kind,
                    record.claim,
                    classification,
                    evidence,
                    rationale=rationale,
                    applicability=EvidenceClass.UNKNOWN,
                    recorded_verification=record.verification_status.value,
                    knowledge_type=record.knowledge_type.value,
                    knowledge_record_id=record.knowledge_id,
                    capture_provenance=record.capture_provenance,
                    verification_checked_at=record.checked_at,
                    verification_checked_by=record.checked_by,
                    verification_check_note=record.check_note,
                    lifecycle_status=record.lifecycle_status.value,
                    supersedes_knowledge_id=record.supersedes_id,
                    superseded_by_knowledge_id=record.superseded_by_id,
                )
            )
        limitations = []
        if ignored_out_of_scope:
            limitations.append(
                "Capability 1 returned records outside the requested project; those records were ignored."
            )
        if ignored_out_of_scope_evidence:
            limitations.append(
                "Capability 1 returned evidence outside the requested project; those references were ignored."
            )
        return decisions, recorded, questions, limitations

    @staticmethod
    def _memory_evidence_ref(
        evidence_record: EvidenceRecord,
        commit: str,
        blobs: dict[str, str],
    ) -> EvidenceReference:
        evidence_type = evidence_record.evidence_type
        source_id = evidence_record.source_id
        locator = evidence_record.locator
        source_revision = evidence_record.revision
        content_hash = evidence_record.content_hash
        path = source_id if evidence_type is EvidenceType.REPOSITORY_FILE else None
        detail = f"{evidence_type.value}: {source_id}"
        if locator:
            detail += f" ({locator})"
        classification = EvidenceClass.UNKNOWN
        check = "source reference not checked"
        if evidence_type is EvidenceType.REPOSITORY_FILE:
            oid = blobs.get(source_id)
            if oid is None:
                classification = EvidenceClass.STALE if source_revision else EvidenceClass.UNKNOWN
                check = "source path is absent at inspected revision"
            elif source_revision == commit:
                classification = EvidenceClass.VERIFIED
                check = "source path exists at inspected revision"
            elif source_revision:
                classification = EvidenceClass.HISTORICAL
                check = "source revision differs; current applicability is unchecked"
            else:
                check = "source path exists, but source revision is unspecified"
        detail = f"{check}; {detail}"
        if content_hash:
            detail += "; saved content hash was not verified"
        return EvidenceReference(
            evidence_type.value,
            source_id,
            source_revision,
            path,
            detail=detail,
            classification=classification,
            evidence_record_id=evidence_record.evidence_id,
            locator=locator,
            content_hash=content_hash,
            recorded_reference_status=evidence_record.reference_status.value,
            recorded_verification_status=evidence_record.verification_status.value,
            checked_at=evidence_record.checked_at,
            checked_by=evidence_record.checked_by,
            check_note=evidence_record.check_note,
        )

    def _finding(
        self,
        commit: str,
        kind: FindingKind,
        claim: str,
        classification: EvidenceClass,
        evidence: tuple[EvidenceReference, ...],
        *,
        rationale: str | None = None,
        applicability: EvidenceClass | None = None,
        recorded_verification: str | None = None,
        knowledge_type: str | None = None,
        knowledge_record_id: str | None = None,
        capture_provenance: CaptureKnowledgeProvenance | None = None,
        verification_checked_at: str | None = None,
        verification_checked_by: str | None = None,
        verification_check_note: str | None = None,
        lifecycle_status: str | None = None,
        supersedes_knowledge_id: str | None = None,
        superseded_by_knowledge_id: str | None = None,
    ) -> StateFinding:
        identity = "\0".join(
            [
                commit,
                kind.value,
                claim,
                *(
                    ":".join(
                        (
                            ref.source_type,
                            ref.source_id,
                            ref.revision or "",
                            ref.path or "",
                            str(ref.line_start or ""),
                            str(ref.line_end or ""),
                            ref.classification.value,
                        )
                    )
                    for ref in evidence
                ),
            ]
        )
        finding_id = hashlib.sha256(identity.encode("utf-8", errors="replace")).hexdigest()[:20]
        return StateFinding(
            finding_id,
            kind,
            claim,
            classification,
            evidence,
            rationale,
            applicability,
            recorded_verification,
            knowledge_type,
            knowledge_record_id,
            capture_provenance,
            verification_checked_at,
            verification_checked_by,
            verification_check_note,
            lifecycle_status,
            supersedes_knowledge_id,
            superseded_by_knowledge_id,
        )


def _unique_refs(references: tuple[EvidenceReference, ...]) -> tuple[EvidenceReference, ...]:
    seen: set[EvidenceReference] = set()
    unique: list[EvidenceReference] = []
    for reference in references:
        if reference not in seen:
            seen.add(reference)
            unique.append(reference)
    return tuple(unique)
