"""Pure in-memory transport partitioning for canonical v1 CaseSets.

This codec does not provide persistence, authority, freshness, or admission.
Every segment is bounded independently; the reconstructed v1 CaseSet remains
subject to the existing 1 MiB document limit and semantic validator.
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Sequence

from .contracts import ContractError, MAX_DOCUMENT_BYTES, require_digest, require_id, require_object, require_ref
from .corpus import _PURPOSES, _require_id_list, validate_case_set
from .partitioned_trial_plan import _canonical
from .run_contracts import content_ref


MAX_ARTIFACT_BYTES = 900_000
MAX_SEGMENTS = 16
MAX_CASES = 1600

SEGMENT_KIND = "case_set_cases_segment"
INDEX_KIND = "case_set_index"
_SEGMENT_FIELDS = {
    "schema_version", "kind", "id", "case_set_id", "segment_index",
    "first_case_ordinal", "case_count", "cases",
}
_INDEX_FIELDS = {
    "schema_version", "kind", "case_set_id", "purpose", "required_categories",
    "case_set_ref", "case_count", "segment_count", "ordered_segments",
    "reconstructed_bytes", "reconstructed_digest",
}


def _invalid(code: str = "PARTITIONED_CASE_SET_INVALID") -> ContractError:
    return ContractError(code)


def _canonical_bytes(value: Any, maximum: int) -> bytes:
    try:
        return _canonical(value, maximum=maximum)
    except ContractError as exc:
        raise _invalid(getattr(exc, "code", "PARTITIONED_CASE_SET_INVALID")) from None


def _segment_id(case_set_id: str, index: int) -> str:
    digest = hashlib.sha256(case_set_id.encode("utf-8")).hexdigest()[:32]
    return f"cs-{digest}-{index:04d}"


def _logical(case_set_id: str, purpose: str, categories: list[str], cases: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "kind": "case_set",
        "case_set_id": case_set_id,
        "purpose": purpose,
        "required_categories": categories,
        "cases": cases,
    }


def partition_case_set(document: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return a v2 index and ordered case segments for a valid v1 CaseSet."""
    try:
        validated = validate_case_set(document)
        case_set_id = validated["case_set_id"]
        purpose = validated["purpose"]
        categories = validated["required_categories"]
        cases = validated["cases"]
        if len(cases) > MAX_CASES:
            raise _invalid("CASE_COUNT")
        reconstructed = _canonical_bytes(validated, MAX_DOCUMENT_BYTES)
    except ContractError as exc:
        raise _invalid(getattr(exc, "code", "PARTITIONED_CASE_SET_INVALID")) from None

    def empty_segment(index: int, ordinal: int) -> dict[str, Any]:
        return {
            "schema_version": 2,
            "kind": SEGMENT_KIND,
            "id": _segment_id(case_set_id, index),
            "case_set_id": case_set_id,
            "segment_index": index,
            "first_case_ordinal": ordinal,
            "case_count": 1,
            "cases": [],
        }

    case_lengths = [_case_length(case) for case in cases]
    ranges: list[tuple[int, int, int]] = []
    start = 0
    payload_bytes = 0
    header_bytes = len(_canonical_bytes(empty_segment(0, 0), MAX_ARTIFACT_BYTES)) - 1 - 2

    def projected_size(header: int, count: int, body: int) -> int:
        return header + len(str(count)) + 2 + body + max(0, count - 1)

    for ordinal, item_size in enumerate(case_lengths):
        if ordinal > start:
            count = ordinal - start + 1
            if projected_size(header_bytes, count, payload_bytes + item_size) > MAX_ARTIFACT_BYTES:
                prior_count = ordinal - start
                ranges.append((start, ordinal, projected_size(header_bytes, prior_count, payload_bytes)))
                if len(ranges) >= MAX_SEGMENTS:
                    raise _invalid("SEGMENT_LIMIT")
                start = ordinal
                header_bytes = len(_canonical_bytes(empty_segment(len(ranges), start), MAX_ARTIFACT_BYTES)) - 1 - 2
                payload_bytes = 0
        count = ordinal - start + 1
        if projected_size(header_bytes, count, payload_bytes + item_size) > MAX_ARTIFACT_BYTES:
            raise _invalid("CASE_TOO_LARGE")
        payload_bytes += item_size
    final_count = len(cases) - start
    ranges.append((start, len(cases), projected_size(header_bytes, final_count, payload_bytes)))
    if not 1 <= len(ranges) <= MAX_SEGMENTS:
        raise _invalid("SEGMENT_LIMIT")

    segments: list[dict[str, Any]] = []
    refs: list[dict[str, str]] = []
    for segment_index, (first, end, predicted_size) in enumerate(ranges):
        segment = {
            "schema_version": 2,
            "kind": SEGMENT_KIND,
            "id": _segment_id(case_set_id, segment_index),
            "case_set_id": case_set_id,
            "segment_index": segment_index,
            "first_case_ordinal": first,
            "case_count": end - first,
            "cases": copy.deepcopy(cases[first:end]),
        }
        raw = _canonical_bytes(segment, MAX_ARTIFACT_BYTES)
        if len(raw) != predicted_size:
            raise _invalid("SEGMENT_SIZE_MISMATCH")
        segments.append(segment)
        refs.append({"kind": SEGMENT_KIND, "id": segment["id"], "digest": hashlib.sha256(raw).hexdigest()})

    index = {
        "schema_version": 2,
        "kind": INDEX_KIND,
        "case_set_id": case_set_id,
        "purpose": purpose,
        "required_categories": copy.deepcopy(categories),
        "case_set_ref": content_ref("case_set", case_set_id, validated),
        "case_count": len(cases),
        "segment_count": len(segments),
        "ordered_segments": refs,
        "reconstructed_bytes": len(reconstructed),
        "reconstructed_digest": hashlib.sha256(reconstructed).hexdigest(),
    }
    _canonical_bytes(index, MAX_ARTIFACT_BYTES)
    return index, segments


def _case_length(case: Any) -> int:
    return len(_canonical_bytes(case, MAX_ARTIFACT_BYTES))


def restore_case_set(index: Any, segments: Sequence[Any]) -> dict[str, Any]:
    """Validate refs/order/semantics and return an independent v1 CaseSet."""
    try:
        # Own the exact canonical snapshot that is subsequently validated.
        # Caller mutation cannot change a value after its digest was checked.
        index = json.loads(_canonical_bytes(index, MAX_ARTIFACT_BYTES))
        if type(segments) not in (list, tuple) or len(segments) > MAX_SEGMENTS:
            raise _invalid("SEGMENT_COUNT")
        segments = tuple(segments[:MAX_SEGMENTS + 1])
        require_object(index, _INDEX_FIELDS)
        if type(index["schema_version"]) is not int or index["schema_version"] != 2 or index["kind"] != INDEX_KIND:
            raise _invalid("UNSUPPORTED_VERSION")
        case_set_id = index["case_set_id"]
        require_id(case_set_id)
        if type(index["purpose"]) is not str or index["purpose"] not in _PURPOSES:
            raise _invalid()
        categories = index["required_categories"]
        _require_id_list(categories, nonempty=True)
        case_count = index["case_count"]
        segment_count = index["segment_count"]
        if (type(case_count) is not int or not 1 <= case_count <= MAX_CASES
                or type(segment_count) is not int or not 1 <= segment_count <= MAX_SEGMENTS
                or type(segments) not in (list, tuple) or len(segments) != segment_count
                or type(index["ordered_segments"]) is not list
                or len(index["ordered_segments"]) != segment_count):
            raise _invalid("SEGMENT_COUNT")
        require_digest(index["reconstructed_digest"])
        if type(index["reconstructed_bytes"]) is not int or not 1 <= index["reconstructed_bytes"] <= MAX_DOCUMENT_BYTES:
            raise _invalid("DOCUMENT_SIZE")
        _canonical_bytes(index, MAX_ARTIFACT_BYTES)
        case_ref = index["case_set_ref"]
        require_ref(case_ref)
        if case_ref["kind"] != "case_set" or case_ref["id"] != case_set_id:
            raise _invalid("REFERENCE_BINDING_MISMATCH")

        all_cases: list[dict[str, Any]] = []
        ordinal = 0
        seen_refs: set[tuple[str, str, str]] = set()
        for expected_index, (reference, supplied_segment) in enumerate(zip(index["ordered_segments"], segments)):
            raw = _canonical_bytes(supplied_segment, MAX_ARTIFACT_BYTES)
            segment = json.loads(raw)
            require_ref(reference)
            ref_key = (reference["kind"], reference["id"], reference["digest"])
            if ref_key in seen_refs:
                raise _invalid("DUPLICATE_REFERENCE")
            seen_refs.add(ref_key)
            require_object(segment, _SEGMENT_FIELDS)
            if (reference["kind"] != SEGMENT_KIND
                    or type(segment["schema_version"]) is not int or segment["schema_version"] != 2
                    or segment["kind"] != SEGMENT_KIND
                    or type(segment["segment_index"]) is not int or segment["segment_index"] != expected_index
                    or segment["id"] != _segment_id(case_set_id, expected_index)
                    or reference["id"] != segment["id"]
                    or segment["case_set_id"] != case_set_id
                    or type(segment["first_case_ordinal"]) is not int
                    or segment["first_case_ordinal"] != ordinal):
                raise _invalid("SEGMENT_BINDING_MISMATCH")
            cases = segment["cases"]
            if (type(cases) is not list or not cases
                    or type(segment["case_count"]) is not int or segment["case_count"] != len(cases)):
                raise _invalid("CASE_COUNT")
            expected_ref = {"kind": SEGMENT_KIND, "id": segment["id"], "digest": hashlib.sha256(raw).hexdigest()}
            if reference != expected_ref:
                raise _invalid("REFERENCE_MISMATCH")
            all_cases.extend(cases)
            ordinal += len(cases)
        if ordinal != case_count:
            raise _invalid("CASE_COUNT")
        logical = _logical(case_set_id, index["purpose"], copy.deepcopy(categories), all_cases)
        reconstructed = _canonical_bytes(logical, MAX_DOCUMENT_BYTES)
        if (len(reconstructed) != index["reconstructed_bytes"]
                or hashlib.sha256(reconstructed).hexdigest() != index["reconstructed_digest"]):
            raise _invalid("RECONSTRUCTED_DIGEST_MISMATCH")
        if content_ref("case_set", case_set_id, logical) != case_ref:
            raise _invalid("REFERENCE_MISMATCH")
        # The legacy validator owns all CaseSet semantics and returns an isolated v1 tree.
        return validate_case_set(logical)
    except ContractError as exc:
        raise _invalid(getattr(exc, "code", "PARTITIONED_CASE_SET_INVALID")) from None


__all__ = [
    "INDEX_KIND", "MAX_ARTIFACT_BYTES", "MAX_CASES", "MAX_SEGMENTS", "SEGMENT_KIND",
    "partition_case_set", "restore_case_set",
]
