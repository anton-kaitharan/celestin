from __future__ import annotations

import copy
import hashlib
import json
import re

import pytest

from orbit.evidence.canonicalization import (
    CONVERSION_HASH_VERSION_V1,
    CONVERSION_INPUT_V1,
    KNOWLEDGE_CONTENT_V1,
    CanonicalizationError,
    canonical_conversion_payload_bytes,
    canonical_hash,
    canonical_json_bytes,
    conversion_input_hash,
    knowledge_content_hash,
)


VECTOR_1_JSON = (
    '{"approved_at":"2026-10-19T12:00:00.000000Z",'
    '"candidate_created_at":"2026-10-18T08:00:00.000000Z",'
    '"candidate_id":"c1","candidate_revision":1,'
    '"candidate_verification_status":"unverified",'
    '"category":"decision","certainty":"medium",'
    '"claim":"Use UTC.","conflicts":[],"evidence_occurrences":[],'
    '"knowledge_type":"decision","original_claim":null,'
    '"producer_id":"agent-a","project_id":"p1","reviewer_id":"user-a",'
    '"schema_version":1,"supersedes_knowledge_id":null}'
)
VECTOR_1_CANONICAL = VECTOR_1_JSON
VECTOR_1_SHA256 = "a24bbb757806b226435aedd9eea88f6d5f18f164c09421942083669151b45ec6"

VECTOR_2_JSON = r'{"approved_at":"2026-10-19T12:00:00.000000Z","candidate_created_at":"2026-10-18T08:00:00.000000Z","candidate_id":"c-2","candidate_revision":2,"candidate_verification_status":"unverified","category":"decision","certainty":"high","claim":"Café uses NFC.\n","conflicts":[{"candidate_id":"c-1","candidate_revision":1,"producer_id":"agent-b"}],"evidence_occurrences":[{"content_hash":"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","evidence_type":"repository","locator":"README.md:4","ordinal":0,"source_id":"repo-1","source_namespace":"git","source_occurred_at":null,"source_revision":"a1b2"},{"content_hash":"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","evidence_type":"repository","locator":"README.md:4","ordinal":1,"source_id":"repo-1","source_namespace":"git","source_occurred_at":null,"source_revision":"a1b2"}],"knowledge_type":"decision","original_claim":null,"producer_id":"agent-a","project_id":"p1","reviewer_id":"user-a","schema_version":1,"supersedes_knowledge_id":null}'
VECTOR_2_CANONICAL = VECTOR_2_JSON
VECTOR_2_SHA256 = "908a72554596df692c426567afe92ad8b57383246e7687e16b11dfb26d9342cd"


def _vector_1() -> dict[str, object]:
    return json.loads(VECTOR_1_JSON)


def _vector_2() -> dict[str, object]:
    return json.loads(VECTOR_2_JSON)


def _occurrence(ordinal: int, *, source_id: str = "repo-1") -> dict[str, object]:
    return {
        "content_hash": None,
        "evidence_type": "repository",
        "locator": None,
        "ordinal": ordinal,
        "source_id": source_id,
        "source_namespace": "git",
        "source_occurred_at": None,
        "source_revision": None,
    }


def _conflict(candidate_id: str, producer_id: str = "agent-b") -> dict[str, object]:
    return {
        "candidate_id": candidate_id,
        "candidate_revision": 1,
        "producer_id": producer_id,
    }


@pytest.mark.parametrize(
    ("source", "expected_canonical", "expected_hash"),
    [
        (VECTOR_1_JSON, VECTOR_1_CANONICAL, VECTOR_1_SHA256),
        (VECTOR_2_JSON, VECTOR_2_CANONICAL, VECTOR_2_SHA256),
    ],
)
def test_approved_golden_vectors_match_canonical_bytes_and_hash(
    source, expected_canonical, expected_hash
):
    payload = json.loads(source)

    assert canonical_conversion_payload_bytes(payload) == expected_canonical.encode("utf-8")
    hashed = conversion_input_hash(payload)
    assert hashed.profile_id == "smic-conversion-payload-v1+jcs-rfc8785+sha256"
    assert hashed.hash_version == "smic-conversion-payload-v1+jcs-rfc8785+sha256"
    assert hashed.profile_id == CONVERSION_HASH_VERSION_V1
    assert hashed.profile_id == CONVERSION_INPUT_V1
    assert hashed.digest == expected_hash
    assert re.fullmatch(r"[0-9a-f]{64}", hashed.digest)


def test_conversion_profile_uses_direct_sha256_of_utf8_jcs_bytes():
    canonical = canonical_conversion_payload_bytes(_vector_1())
    hashed = conversion_input_hash(_vector_1())

    assert hashed.digest == hashlib.sha256(canonical).hexdigest()
    assert not hashed.digest.startswith("sha256:")


def test_jcs_uses_utf16_code_unit_order_for_object_keys():
    # U+1F600 sorts before U+E000 in UTF-16 code-unit order, unlike Python's
    # code-point ordering for these two keys.
    assert canonical_json_bytes({"\ue000": 2, "😀": 1}) == '{"😀":1,"":2}'.encode()


def test_conversion_normalizes_all_string_values_to_nfc_without_trimming():
    composed = _vector_2()
    decomposed = copy.deepcopy(composed)
    decomposed["claim"] = "Cafe\u0301 uses NFC.\n"
    decomposed["project_id"] = "proje\u0301ct"
    decomposed["evidence_occurrences"][0]["source_id"] = "re\u0301po-1"
    decomposed["evidence_occurrences"][1]["source_id"] = "re\u0301po-1"
    composed["project_id"] = "projéct"
    composed["evidence_occurrences"][0]["source_id"] = "répo-1"
    composed["evidence_occurrences"][1]["source_id"] = "répo-1"

    assert canonical_conversion_payload_bytes(composed) == canonical_conversion_payload_bytes(
        decomposed
    )

    whitespace = _vector_1()
    whitespace["candidate_id"] = " c1 "
    assert json.loads(canonical_conversion_payload_bytes(whitespace))["candidate_id"] == " c1 "


def test_conversion_normalizes_timestamps_to_utc_with_six_fractional_digits():
    equivalent = _vector_1()
    equivalent["candidate_created_at"] = "2026-10-18T13:30:00+05:30"
    equivalent["approved_at"] = "2026-10-19T14:00:00+02:00"
    assert conversion_input_hash(equivalent) == conversion_input_hash(_vector_1())

    payload = _vector_1()
    payload["candidate_created_at"] = "2026-10-18T13:30:00+05:30"
    payload["approved_at"] = "2026-10-19T14:00:00+02:00"
    occurrence = _occurrence(0)
    occurrence["source_occurred_at"] = "2026-10-19T08:00:00.12-04:00"
    payload["evidence_occurrences"] = [occurrence]

    canonical = json.loads(canonical_conversion_payload_bytes(payload))

    assert canonical["candidate_created_at"] == "2026-10-18T08:00:00.000000Z"
    assert canonical["approved_at"] == "2026-10-19T12:00:00.000000Z"
    assert canonical["evidence_occurrences"][0]["source_occurred_at"] == (
        "2026-10-19T12:00:00.120000Z"
    )


def test_timestamp_format_is_four_digit_year_on_all_platforms():
    payload = _vector_1()
    payload["candidate_created_at"] = "0001-01-01T00:00:00Z"

    canonical = json.loads(canonical_conversion_payload_bytes(payload))

    assert canonical["candidate_created_at"] == "0001-01-01T00:00:00.000000Z"


@pytest.mark.parametrize(
    "timestamp",
    [
        "2026-10-19T12:00:00",  # no timezone
        "2026-10-19T12:00:00.1234567Z",  # precision cannot fit six digits
        "2026-10-19T12:00:00-00:00",  # RFC 3339 unknown offset
        "2026-10-19T12:00:60Z",  # leap-second policy is not defined
    ],
)
def test_unsupported_or_ambiguous_timestamp_inputs_fail_closed(timestamp):
    payload = _vector_1()
    payload["candidate_created_at"] = timestamp

    with pytest.raises(CanonicalizationError):
        conversion_input_hash(payload)


def test_conversion_occurrences_are_sorted_by_ordinal_and_duplicates_remain():
    payload = _vector_1()
    repeated_later = _occurrence(1)
    repeated_first = _occurrence(0)
    payload["evidence_occurrences"] = [repeated_later, repeated_first]

    canonical = json.loads(canonical_conversion_payload_bytes(payload))
    occurrences = canonical["evidence_occurrences"]

    assert [item["ordinal"] for item in occurrences] == [0, 1]
    assert occurrences[0] | {"ordinal": 1} == occurrences[1]
    assert len(occurrences) == 2


def test_conversion_rejects_gapped_or_repeated_occurrence_ordinals():
    payload = _vector_1()
    payload["evidence_occurrences"] = [_occurrence(0), _occurrence(2)]

    with pytest.raises(CanonicalizationError, match="unique and contiguous"):
        conversion_input_hash(payload)


def test_conflicts_are_sorted_and_duplicate_edges_are_represented_once():
    payload = _vector_1()
    payload["conflicts"] = [
        _conflict("z-target"),
        _conflict("a-target"),
        _conflict("z-target"),
    ]

    conflicts = json.loads(canonical_conversion_payload_bytes(payload))["conflicts"]

    assert [item["candidate_id"] for item in conflicts] == ["a-target", "z-target"]


def test_candidate_revision_cannot_conflict_with_itself():
    payload = _vector_1()
    payload["conflicts"] = [_conflict("c1", producer_id="agent-a")]

    with pytest.raises(CanonicalizationError, match="cannot conflict with itself"):
        conversion_input_hash(payload)


@pytest.mark.parametrize(
    ("category", "knowledge_type"),
    [
        ("decision", "decision"),
        ("discovery", "discovery"),
        ("bug_fix", "bug_fix"),
        ("completed_work", "completed_work"),
        ("constraint", "requirement"),
        ("implementation_detail", "implementation_finding"),
    ],
)
def test_conversion_uses_the_pinned_category_to_knowledge_type_mapping(
    category, knowledge_type
):
    payload = _vector_1()
    payload["category"] = category
    payload["knowledge_type"] = knowledge_type

    canonical_conversion_payload_bytes(payload)


@pytest.mark.parametrize(
    ("category", "knowledge_type"),
    [("blocker", "open_question"), ("decision", "discovery")],
)
def test_conversion_rejects_unknown_or_mismatched_knowledge_mapping(
    category, knowledge_type
):
    payload = _vector_1()
    payload["category"] = category
    payload["knowledge_type"] = knowledge_type

    with pytest.raises(CanonicalizationError):
        conversion_input_hash(payload)


def test_conversion_requires_exact_fields_and_explicit_nullable_values():
    payload = _vector_1()
    del payload["original_claim"]

    with pytest.raises(CanonicalizationError, match="fields do not match"):
        conversion_input_hash(payload)


def test_conversion_requires_unverified_candidate_content_and_nulls():
    payload = _vector_1()
    payload["candidate_verification_status"] = "verified"
    with pytest.raises(CanonicalizationError, match="must be 'unverified'"):
        conversion_input_hash(payload)

    payload = _vector_1()
    payload["evidence_occurrences"] = [{**_occurrence(0), "locator": "missing"}]
    del payload["evidence_occurrences"][0]["source_revision"]
    with pytest.raises(CanonicalizationError, match="fields do not match"):
        conversion_input_hash(payload)


def test_evidence_and_order_change_conversion_hash_but_not_knowledge_content_hash():
    baseline = _vector_1()
    with_evidence = _vector_1()
    with_evidence["evidence_occurrences"] = [_occurrence(0)]
    reordered_occurrences = _vector_1()
    reordered_occurrences["evidence_occurrences"] = [
        _occurrence(0, source_id="repo-a"),
        _occurrence(1, source_id="repo-b"),
    ]

    assert conversion_input_hash(baseline) != conversion_input_hash(with_evidence)
    assert conversion_input_hash(with_evidence) != conversion_input_hash(
        reordered_occurrences
    )
    def content_hash(payload):
        return knowledge_content_hash(
            project_id=payload["project_id"],
            claim=payload["claim"],
            category=payload["category"],
        )

    assert content_hash(baseline) == content_hash(with_evidence)
    assert content_hash(baseline) == content_hash(reordered_occurrences)


def test_knowledge_content_profile_remains_separate_and_unchanged():
    composed = "café is ready"
    decomposed = "cafe\u0301 is ready"

    first = knowledge_content_hash(
        project_id="alpha", claim=composed, category="decision"
    )
    second = knowledge_content_hash(
        project_id="alpha", claim=decomposed, category="decision"
    )

    assert first == second
    assert first.profile_id == KNOWLEDGE_CONTENT_V1
    assert first.digest == (
        "sha256:16409fdc9f9e83a509e2d97662bbfe9a736d85d4db4bb426822ae88576413682"
    )
    assert first.digest != conversion_input_hash(_vector_1()).digest
    assert decomposed == "cafe\u0301 is ready"


def test_conversion_hash_is_project_scoped_and_uses_contract_version():
    baseline = _vector_1()
    changed_project = _vector_1()
    changed_project["project_id"] = "another-project"

    assert conversion_input_hash(baseline).profile_id == CONVERSION_INPUT_V1
    assert conversion_input_hash(baseline) != conversion_input_hash(changed_project)


def test_opaque_identifier_case_and_whitespace_are_preserved():
    baseline = _vector_1()
    preserved = _vector_1()
    preserved["candidate_id"] = " C1 "
    preserved["evidence_occurrences"] = [_occurrence(0)]
    preserved["evidence_occurrences"][0]["source_revision"] = "Rev-A "

    canonical = json.loads(canonical_conversion_payload_bytes(preserved))
    assert canonical["candidate_id"] == " C1 "
    assert canonical["evidence_occurrences"][0]["source_revision"] == "Rev-A "
    assert conversion_input_hash(preserved) != conversion_input_hash(baseline)

    changed_case = copy.deepcopy(preserved)
    changed_case["candidate_id"] = " c1 "
    assert conversion_input_hash(preserved) != conversion_input_hash(changed_case)


@pytest.mark.parametrize(
    "payload",
    [
        {"float": 1.25},
        {"nan": float("nan")},
        {1: "non-string object key"},
        {"custom": object()},
        {"set": {"unordered"}},
        {"bytes": b"not JSON text"},
        {"surrogate": "\ud800"},
        {"large_integer": 2**53},
    ],
)
def test_jcs_serializer_rejects_values_outside_supported_json_domain(payload):
    with pytest.raises(CanonicalizationError):
        canonical_json_bytes(payload)


def test_conversion_payload_must_be_a_plain_json_object():
    with pytest.raises(CanonicalizationError, match="top-level JSON object"):
        conversion_input_hash(["not", "an", "object"])


def test_circular_json_value_is_rejected_clearly():
    payload = {}
    payload["self"] = payload

    with pytest.raises(CanonicalizationError, match="circular JSON value"):
        canonical_json_bytes(payload)


def test_unknown_profiles_are_rejected_instead_of_reinterpreted():
    with pytest.raises(
        CanonicalizationError, match="unsupported canonicalization profile"
    ):
        canonical_hash({}, profile_id="smic-conversion-payload-v2+jcs-rfc8785+sha256")


def test_knowledge_hash_is_project_scoped_and_includes_category():
    baseline = knowledge_content_hash(
        project_id="alpha", claim="Keep SQLite", category="decision"
    )

    assert baseline != knowledge_content_hash(
        project_id="beta", claim="Keep SQLite", category="decision"
    )
    assert baseline != knowledge_content_hash(
        project_id="alpha", claim="Keep SQLite", category="discovery"
    )
