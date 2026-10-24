"""Versioned hashing primitives for Evidence & Provenance payloads.

The conversion profile implements the requester-authorized Shared Memory
Interface Contract v0.3. Delivery retry equality is a separate, unspecified
contract and is deliberately not derived from this conversion hash.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Final

import rfc8785


CONVERSION_PAYLOAD_V1: Final = "smic-conversion-payload-v1"
CONVERSION_HASH_VERSION_V1: Final = (
    f"{CONVERSION_PAYLOAD_V1}+jcs-rfc8785+sha256"
)
# Compatibility name for callers of the provisional helper. Its value now
# identifies the complete, persisted conversion hash version.
CONVERSION_INPUT_V1: Final = CONVERSION_HASH_VERSION_V1
KNOWLEDGE_CONTENT_V1: Final = "celestin-knowledge-content-v1"
_SUPPORTED_PROFILES: Final = frozenset(
    {CONVERSION_HASH_VERSION_V1, KNOWLEDGE_CONTENT_V1}
)

_CONVERSION_FIELDS: Final = frozenset(
    {
        "schema_version",
        "project_id",
        "producer_id",
        "candidate_id",
        "candidate_revision",
        "category",
        "knowledge_type",
        "certainty",
        "claim",
        "original_claim",
        "candidate_verification_status",
        "candidate_created_at",
        "reviewer_id",
        "approved_at",
        "evidence_occurrences",
        "conflicts",
        "supersedes_knowledge_id",
    }
)
_EVIDENCE_OCCURRENCE_FIELDS: Final = frozenset(
    {
        "content_hash",
        "evidence_type",
        "locator",
        "ordinal",
        "source_id",
        "source_namespace",
        "source_occurred_at",
        "source_revision",
    }
)
_CONFLICT_FIELDS: Final = frozenset(
    {"candidate_id", "candidate_revision", "producer_id"}
)
_CATEGORY_TO_KNOWLEDGE_TYPE: Final = {
    "decision": "decision",
    "discovery": "discovery",
    "bug_fix": "bug_fix",
    "completed_work": "completed_work",
    "constraint": "requirement",
    "implementation_detail": "implementation_finding",
}
_CONTENT_HASH_RE: Final = re.compile(r"sha256:[0-9a-f]{64}\Z")
_RFC3339_TIMESTAMP_RE: Final = re.compile(
    r"(?P<date>\d{4}-\d{2}-\d{2})[Tt]"
    r"(?P<time>\d{2}:\d{2}:\d{2})"
    r"(?:\.(?P<fraction>\d+))?"
    r"(?P<zone>[Zz]|[+-]\d{2}:\d{2})\Z"
)


class CanonicalizationError(ValueError):
    """Raised when a profile or payload cannot be canonicalized safely."""


@dataclass(frozen=True, slots=True)
class CanonicalHash:
    """A digest paired with the profile required to interpret it.

    Conversion digests are lowercase hexadecimal without an algorithm prefix;
    their `profile_id` carries the complete hash version. The provisional
    knowledge-content digest retains its historical `sha256:` prefix.
    """

    profile_id: str
    digest: str

    @property
    def hash_version(self) -> str:
        """Expose the contract's persisted field name without breaking callers."""

        return self.profile_id


def canonical_json_bytes(value: object) -> bytes:
    """Encode supported JSON values using RFC 8785 JCS as UTF-8 bytes.

    This generic serializer does not normalize strings. The conversion profile
    performs its contract-required NFC normalization before calling it. Object
    keys are ordered by JCS, arrays retain their supplied order, floats and
    non-JSON Python values are rejected, and tuples retain the previous helper's
    conversion to JSON arrays.
    """

    try:
        safe_value = _copy_json_value(value, path="$", ancestors=set())
        return rfc8785.dumps(safe_value)
    except CanonicalizationError:
        raise
    except (rfc8785.CanonicalizationError, TypeError, ValueError, RecursionError) as exc:
        raise CanonicalizationError(
            "payload cannot be encoded as RFC 8785 canonical UTF-8 JSON"
        ) from exc


def canonical_hash(payload: object, *, profile_id: str) -> CanonicalHash:
    """Hash one supported canonicalization profile.

    `smic-conversion-payload-v1+jcs-rfc8785+sha256` hashes the UTF-8 JCS bytes
    directly, as specified by the decision record. The older knowledge-content
    helper remains a distinct provisional, domain-separated profile.
    """

    if type(profile_id) is not str or profile_id not in _SUPPORTED_PROFILES:
        raise CanonicalizationError(
            f"unsupported canonicalization profile: {profile_id!r}"
        )

    if profile_id == CONVERSION_HASH_VERSION_V1:
        canonical = canonical_conversion_payload_bytes(payload)
        digest = hashlib.sha256(canonical).hexdigest()
        return CanonicalHash(profile_id=CONVERSION_HASH_VERSION_V1, digest=digest)

    canonical = canonical_json_bytes(payload)
    preimage = profile_id.encode("ascii") + b"\x00" + canonical
    digest = hashlib.sha256(preimage).hexdigest()
    return CanonicalHash(profile_id=profile_id, digest=f"sha256:{digest}")


def canonical_conversion_payload_bytes(payload: object) -> bytes:
    """Validate and serialize the exact v0.3 conversion payload using JCS."""

    normalized = _normalize_conversion_payload(payload)
    return canonical_json_bytes(normalized)


def conversion_input_hash(payload: dict[str, object]) -> CanonicalHash:
    """Hash a contract-shaped conversion payload under the approved v0.3 profile.

    This is not a delivery/request retry hash. Delivery-ID equality has not been
    fully defined by the decision record, so callers must not use this helper
    for delivery retry arbitration.
    """

    return canonical_hash(payload, profile_id=CONVERSION_HASH_VERSION_V1)


def knowledge_content_hash(
    *, project_id: str, claim: str, category: str
) -> CanonicalHash:
    """Hash a project-scoped assertion under its separate provisional profile.

    The caller's claim is not modified. Evidence, approval, occurrence, and
    timestamp fields are intentionally outside this knowledge-content hash.
    This profile is not the v0.3 conversion-payload hash.
    """

    for field, value in (
        ("project_id", project_id),
        ("claim", claim),
        ("category", category),
    ):
        if type(value) is not str or not value or "\x00" in value:
            raise CanonicalizationError(f"{field} must be non-empty text without NUL")

    return canonical_hash(
        {
            "project_id": project_id,
            "claim": unicodedata.normalize("NFC", claim),
            "category": category,
        },
        profile_id=KNOWLEDGE_CONTENT_V1,
    )


def _normalize_conversion_payload(payload: object) -> dict[str, object]:
    if type(payload) is not dict:
        raise CanonicalizationError(
            "conversion payload must be a top-level JSON object"
        )

    copied = _copy_json_value(payload, path="$", ancestors=set())
    normalized = _normalize_nfc_json(copied, path="$", ancestors=set())
    if type(normalized) is not dict:
        raise CanonicalizationError("conversion payload must be a JSON object")
    _require_exact_fields(normalized, _CONVERSION_FIELDS, path="$")

    if type(normalized["schema_version"]) is not int or normalized["schema_version"] != 1:
        raise CanonicalizationError("schema_version must be integer 1")
    _require_text(normalized["project_id"], "project_id")
    _require_text(normalized["producer_id"], "producer_id")
    _require_text(normalized["candidate_id"], "candidate_id")
    _require_positive_integer(normalized["candidate_revision"], "candidate_revision")
    _require_text(normalized["claim"], "claim")
    _require_optional_text(normalized["original_claim"], "original_claim")
    _require_text(normalized["reviewer_id"], "reviewer_id")
    _require_optional_text(
        normalized["supersedes_knowledge_id"], "supersedes_knowledge_id"
    )

    category = _require_text(normalized["category"], "category")
    knowledge_type = _require_text(normalized["knowledge_type"], "knowledge_type")
    expected_knowledge_type = _CATEGORY_TO_KNOWLEDGE_TYPE.get(category)
    if expected_knowledge_type is None:
        raise CanonicalizationError(
            f"unsupported conversion category or mapping: {category!r}"
        )
    if knowledge_type != expected_knowledge_type:
        raise CanonicalizationError(
            "knowledge_type does not match the v0.3 category mapping"
        )

    certainty = _require_text(normalized["certainty"], "certainty")
    if certainty != certainty.lower():
        raise CanonicalizationError("certainty machine enum values must be lowercase")
    if normalized["candidate_verification_status"] != "unverified":
        raise CanonicalizationError(
            "candidate_verification_status must be 'unverified' at conversion"
        )

    normalized["candidate_created_at"] = _normalize_timestamp(
        normalized["candidate_created_at"], "candidate_created_at"
    )
    normalized["approved_at"] = _normalize_timestamp(
        normalized["approved_at"], "approved_at"
    )
    normalized["evidence_occurrences"] = _normalize_occurrences(
        normalized["evidence_occurrences"]
    )
    normalized["conflicts"] = _normalize_conflicts(
        normalized["conflicts"],
        producer_id=normalized["producer_id"],
        candidate_id=normalized["candidate_id"],
        candidate_revision=normalized["candidate_revision"],
    )
    return normalized


def _normalize_occurrences(value: object) -> list[dict[str, object]]:
    if type(value) not in (list, tuple):
        raise CanonicalizationError("evidence_occurrences must be a JSON array")

    occurrences: list[dict[str, object]] = []
    for index, item in enumerate(value):
        path = f"evidence_occurrences[{index}]"
        if type(item) is not dict:
            raise CanonicalizationError(f"{path} must be an object")
        _require_exact_fields(item, _EVIDENCE_OCCURRENCE_FIELDS, path=path)
        _require_text(item["evidence_type"], f"{path}.evidence_type")
        if item["evidence_type"] != item["evidence_type"].lower():
            raise CanonicalizationError(f"{path}.evidence_type must be lowercase")
        _require_text(item["source_id"], f"{path}.source_id")
        _require_text(item["source_namespace"], f"{path}.source_namespace")
        _require_optional_text(item["locator"], f"{path}.locator")
        _require_optional_text(item["source_revision"], f"{path}.source_revision")
        _require_nonnegative_integer(item["ordinal"], f"{path}.ordinal")

        content_hash = item["content_hash"]
        if content_hash is not None and (
            type(content_hash) is not str or _CONTENT_HASH_RE.fullmatch(content_hash) is None
        ):
            raise CanonicalizationError(
                f"{path}.content_hash must be null or lowercase sha256 hex"
            )
        occurred_at = item["source_occurred_at"]
        if occurred_at is not None:
            item["source_occurred_at"] = _normalize_timestamp(
                occurred_at, f"{path}.source_occurred_at"
            )
        occurrences.append(item)

    occurrences.sort(key=lambda occurrence: occurrence["ordinal"])
    if [item["ordinal"] for item in occurrences] != list(range(len(occurrences))):
        raise CanonicalizationError(
            "evidence occurrence ordinals must be unique and contiguous from zero"
        )
    return occurrences


def _normalize_conflicts(
    value: object,
    *,
    producer_id: object,
    candidate_id: object,
    candidate_revision: object,
) -> list[dict[str, object]]:
    if type(value) not in (list, tuple):
        raise CanonicalizationError("conflicts must be a JSON array")

    conflicts: list[dict[str, object]] = []
    for index, item in enumerate(value):
        path = f"conflicts[{index}]"
        if type(item) is not dict:
            raise CanonicalizationError(f"{path} must be an object")
        _require_exact_fields(item, _CONFLICT_FIELDS, path=path)
        _require_text(item["producer_id"], f"{path}.producer_id")
        _require_text(item["candidate_id"], f"{path}.candidate_id")
        _require_positive_integer(item["candidate_revision"], f"{path}.candidate_revision")
        if (
            item["producer_id"],
            item["candidate_id"],
            item["candidate_revision"],
        ) == (producer_id, candidate_id, candidate_revision):
            raise CanonicalizationError("a candidate revision cannot conflict with itself")
        conflicts.append(item)

    conflicts.sort(
        key=lambda conflict: (
            conflict["producer_id"],
            conflict["candidate_id"],
            conflict["candidate_revision"],
        )
    )
    unique: list[dict[str, object]] = []
    previous_key: tuple[str, str, int] | None = None
    for conflict in conflicts:
        key = (
            conflict["producer_id"],
            conflict["candidate_id"],
            conflict["candidate_revision"],
        )
        if key != previous_key:
            unique.append(conflict)
            previous_key = key
    return unique


def _normalize_timestamp(value: object, field: str) -> str:
    if type(value) is not str:
        raise CanonicalizationError(f"{field} must be a timezone-aware RFC 3339 string")
    match = _RFC3339_TIMESTAMP_RE.fullmatch(value)
    if match is None:
        raise CanonicalizationError(
            f"{field} must be a timezone-aware RFC 3339 timestamp"
        )

    fraction = match.group("fraction") or ""
    if len(fraction) > 6:
        raise CanonicalizationError(
            f"{field} has more than six fractional digits; rounding policy is undefined"
        )
    zone = match.group("zone")
    if zone == "-00:00":
        raise CanonicalizationError(
            f"{field} uses RFC 3339's unknown local offset and cannot be normalized"
        )

    offset = "+00:00" if zone in ("Z", "z") else zone
    normalized_input = (
        f"{match.group('date')}T{match.group('time')}"
        f".{fraction.ljust(6, '0')}"
        f"{offset}"
    )
    try:
        parsed = datetime.fromisoformat(normalized_input)
    except ValueError as exc:
        raise CanonicalizationError(f"{field} is not a supported RFC 3339 timestamp") from exc

    if parsed.tzinfo is None:
        raise CanonicalizationError(f"{field} must include a timezone offset")
    try:
        utc_value = parsed.astimezone(timezone.utc)
        return (
            f"{utc_value.year:04d}-{utc_value.month:02d}-{utc_value.day:02d}"
            f"T{utc_value.hour:02d}:{utc_value.minute:02d}:{utc_value.second:02d}"
            f".{utc_value.microsecond:06d}Z"
        )
    except (OverflowError, ValueError) as exc:
        raise CanonicalizationError(
            f"{field} cannot be represented as a UTC timestamp"
        ) from exc


def _normalize_nfc_json(value: object, *, path: str, ancestors: set[int]) -> object:
    if type(value) is str:
        return unicodedata.normalize("NFC", value)
    if value is None or type(value) in (bool, int):
        return value
    if type(value) not in (dict, list):
        raise CanonicalizationError(f"unsupported JSON value at {path}")

    identity = id(value)
    if identity in ancestors:
        raise CanonicalizationError(f"circular JSON value at {path}")
    ancestors.add(identity)
    try:
        if type(value) is dict:
            normalized: dict[str, object] = {}
            for key, item in value.items():
                if type(key) is not str:
                    raise CanonicalizationError(
                        f"JSON object key at {path} must be text"
                    )
                normalized_key = unicodedata.normalize("NFC", key)
                if normalized_key in normalized:
                    raise CanonicalizationError(
                        f"object keys collide after NFC normalization at {path}"
                    )
                normalized[normalized_key] = _normalize_nfc_json(
                    item, path=f"{path}.{normalized_key}", ancestors=ancestors
                )
            return normalized
        return [
            _normalize_nfc_json(item, path=f"{path}[{index}]", ancestors=ancestors)
            for index, item in enumerate(value)
        ]
    finally:
        ancestors.remove(identity)


def _require_exact_fields(
    value: dict[str, object], expected: frozenset[str], *, path: str
) -> None:
    actual = frozenset(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise CanonicalizationError(
            f"{path} fields do not match v0.3 payload; missing={missing}, extra={extra}"
        )


def _require_text(value: object, field: str) -> str:
    if type(value) is not str:
        raise CanonicalizationError(f"{field} must be text")
    return value


def _require_optional_text(value: object, field: str) -> None:
    if value is not None and type(value) is not str:
        raise CanonicalizationError(f"{field} must be text or null")


def _require_positive_integer(value: object, field: str) -> None:
    if type(value) is not int or value < 1:
        raise CanonicalizationError(f"{field} must be a positive integer")


def _require_nonnegative_integer(value: object, field: str) -> None:
    if type(value) is not int or value < 0:
        raise CanonicalizationError(f"{field} must be a nonnegative integer")


def _copy_json_value(value: object, *, path: str, ancestors: set[int]) -> object:
    if value is None or type(value) in (bool, int, str):
        return value

    if type(value) not in (dict, list, tuple):
        raise CanonicalizationError(
            f"unsupported JSON value at {path}: {type(value).__name__}"
        )

    identity = id(value)
    if identity in ancestors:
        raise CanonicalizationError(f"circular JSON value at {path}")

    ancestors.add(identity)
    try:
        if type(value) is dict:
            items = tuple(value.items())
            copied: dict[str, object] = {}
            for key, item in items:
                if type(key) is not str:
                    raise CanonicalizationError(
                        f"JSON object key at {path} must be text"
                    )
                copied[key] = _copy_json_value(
                    item, path=f"{path}.{key}", ancestors=ancestors
                )
            return copied

        items = tuple(value)
        return [
            _copy_json_value(item, path=f"{path}[{index}]", ancestors=ancestors)
            for index, item in enumerate(items)
        ]
    finally:
        ancestors.remove(identity)
