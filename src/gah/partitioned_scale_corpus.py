"""Pure, bounded segmentation for deterministic query-scale corpus data.

This codec is provisioning-only. It does not authorize, persist, admit, or
claim runtime performance. The restored v1 corpus is always rechecked by its
existing semantic validator.
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Sequence

from .contracts import ContractError, MAX_DOCUMENT_BYTES, require_digest, require_id, require_object, require_ref, require_uint
from .partitioned_case_set import (
    INDEX_KIND as CASE_SET_INDEX_KIND, MAX_ARTIFACT_BYTES, MAX_CASES, MAX_SEGMENTS as MAX_CASE_SET_SEGMENTS,
    SEGMENT_KIND as CASE_SET_SEGMENT_KIND, partition_case_set, restore_case_set,
)
from .partitioned_trial_plan import _canonical
from .query_scale_data import validate_scale_corpus
from .run_contracts import content_ref

MAX_CORPUS_BYTES = 8 * 1024 * 1024
MAX_DOCUMENT_SEGMENTS = 10
MAX_DOCUMENTS = MAX_CASES * 4
INDEX_KIND = "query_scale_corpus_index"
DOCUMENT_SEGMENT_KIND = "query_scale_documents_segment"
_INDEX_FIELDS = {
    "schema_version", "kind", "corpus_id", "case_count", "document_count",
    "claims", "stage_counts", "case_set_index_ref", "ordered_document_segments",
    "reconstructed_bytes", "reconstructed_digest",
}
_DOCUMENT_SEGMENT_FIELDS = {
    "schema_version", "kind", "id", "corpus_id", "segment_index",
    "first_document_ordinal", "document_count", "documents",
}
_DOCUMENT_FIELDS = {"ref", "document"}
_ALLOWED_DOCUMENT_KINDS = {
    "synthetic_policy_input", "synthetic_initial_state", "synthetic_policy_oracle",
}

def _invalid(reason: str = "QUERY_SCALE_PARTITION_INVALID") -> ContractError:
    return ContractError(reason)

def _canonical_snapshot(value: Any, maximum: int) -> tuple[bytes, Any]:
    try:
        raw = _canonical(value, maximum=maximum)
        snapshot = json.loads(raw.decode("utf-8"))
    except (ContractError, TypeError, ValueError, UnicodeError, RecursionError):
        raise _invalid() from None
    return raw, snapshot

def _segment_id(corpus_id: str, index: int) -> str:
    digest = hashlib.sha256(corpus_id.encode("utf-8")).hexdigest()[:32]
    return f"qsd-{digest}-{index:04d}"

def _document_segment(corpus_id: str, index: int, first: int, documents: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": 2, "kind": DOCUMENT_SEGMENT_KIND,
        "id": _segment_id(corpus_id, index), "corpus_id": corpus_id,
        "segment_index": index, "first_document_ordinal": first,
        "document_count": len(documents), "documents": documents,
    }

def _document_segments(corpus_id: str, documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if type(documents) is not list or not 1 <= len(documents) <= MAX_DOCUMENTS:
        raise _invalid("DOCUMENT_COUNT")
    lengths: list[int] = []
    for item in documents:
        if type(item) is not dict or set(item) != _DOCUMENT_FIELDS:
            raise _invalid()
        raw, _ = _canonical_snapshot(item, MAX_ARTIFACT_BYTES)
        lengths.append(len(raw))
    groups: list[tuple[int, int, int]] = []
    start = 0
    payload_bytes = 0

    def header_size(segment_index: int, ordinal: int) -> int:
        empty = _document_segment(corpus_id, segment_index, ordinal, [])
        raw, _ = _canonical_snapshot(empty, MAX_ARTIFACT_BYTES)
        return len(raw) - len(b"0") - len(b"[]")

    fixed_header = header_size(0, 0)

    def projected(header: int, count: int, payload: int) -> int:
        return header + len(str(count)) + 2 + payload + max(0, count - 1)

    for ordinal, item_size in enumerate(lengths):
        if ordinal > start:
            count = ordinal - start + 1
            if projected(fixed_header, count, payload_bytes + item_size) > MAX_ARTIFACT_BYTES:
                previous_count = ordinal - start
                groups.append((start, ordinal, projected(fixed_header, previous_count, payload_bytes)))
                if len(groups) >= MAX_DOCUMENT_SEGMENTS:
                    raise _invalid("DOCUMENT_SEGMENT_LIMIT")
                start = ordinal
                fixed_header = header_size(len(groups), start)
                payload_bytes = 0
        count = ordinal - start + 1
        size = projected(fixed_header, count, payload_bytes + item_size)
        if size > MAX_ARTIFACT_BYTES:
            raise _invalid("DOCUMENT_SEGMENT_TOO_LARGE")
        payload_bytes += item_size
    groups.append((start, len(documents), projected(fixed_header, len(documents) - start, payload_bytes)))
    if not 1 <= len(groups) <= MAX_DOCUMENT_SEGMENTS:
        raise _invalid("DOCUMENT_SEGMENT_LIMIT")

    segments: list[dict[str, Any]] = []
    for segment_index, (first, end, expected_bytes) in enumerate(groups):
        segment = _document_segment(corpus_id, segment_index, first, copy.deepcopy(documents[first:end]))
        raw, _ = _canonical_snapshot(segment, MAX_ARTIFACT_BYTES)
        if len(raw) != expected_bytes:
            raise _invalid("DOCUMENT_SEGMENT_SIZE_MISMATCH")
        segments.append(segment)
    return segments

def partition_scale_corpus(value: Any) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Partition one fixed 400/800/1600 corpus for bounded transport.

    This is provisioning-only; it is not a hot-path cache or runtime admission API.
    """
    raw_input, snapshot = _canonical_snapshot(value, MAX_CORPUS_BYTES)
    if type(snapshot) is not dict:
        raise _invalid()
    try:
        corpus = validate_scale_corpus(snapshot)
    except ContractError as exc:
        raise _invalid(getattr(exc, "code", "QUERY_SCALE_CORPUS_INVALID")) from None
    case_count = len(corpus["case_set"]["cases"])
    if case_count not in (400, 800, 1600):
        raise _invalid("CASE_COUNT")
    case_index, case_segments = partition_case_set(corpus["case_set"])
    document_segments = _document_segments(corpus["corpus_id"], corpus["documents"])
    canonical_corpus, _ = _canonical_snapshot(corpus, MAX_CORPUS_BYTES)
    if canonical_corpus != raw_input:
        raise _invalid("CORPUS_SNAPSHOT_MISMATCH")
    case_index_ref = content_ref(CASE_SET_INDEX_KIND, case_index["case_set_id"], case_index)
    document_refs = [
        {"kind": DOCUMENT_SEGMENT_KIND, "id": segment["id"],
         "digest": hashlib.sha256(_canonical_snapshot(segment, MAX_ARTIFACT_BYTES)[0]).hexdigest()}
        for segment in document_segments
    ]
    index = {
        "schema_version": 2, "kind": INDEX_KIND, "corpus_id": corpus["corpus_id"],
        "case_count": case_count, "document_count": len(corpus["documents"]),
        "claims": copy.deepcopy(corpus["claims"]),
        "stage_counts": {"1": case_count, "2": 0},
        "case_set_index_ref": case_index_ref,
        "ordered_document_segments": document_refs,
        "reconstructed_bytes": len(canonical_corpus),
        "reconstructed_digest": hashlib.sha256(canonical_corpus).hexdigest(),
    }
    _canonical_snapshot(index, MAX_ARTIFACT_BYTES)
    return copy.deepcopy(index), copy.deepcopy(case_index), copy.deepcopy(case_segments), copy.deepcopy(document_segments)

def _bounded_sequence(values: Any, maximum: int) -> tuple[Any, ...]:
    if type(values) not in (list, tuple) or len(values) > maximum:
        raise _invalid("SEGMENT_COUNT")
    snapshot = tuple(values)
    if len(snapshot) != len(values) or len(snapshot) > maximum:
        raise _invalid("SEGMENT_COUNT")
    return snapshot

def restore_scale_corpus(index: Any, case_set_index: Any, case_set_segments: Sequence[Any], document_segments: Sequence[Any]) -> dict[str, Any]:
    """Restore and fully validate a detached v1 query-scale corpus."""
    _, index = _canonical_snapshot(index, MAX_ARTIFACT_BYTES)
    if type(index) is not dict:
        raise _invalid()
    try:
        require_object(index, _INDEX_FIELDS)
        if type(index["schema_version"]) is not int or index["schema_version"] != 2 or index["kind"] != INDEX_KIND:
            raise _invalid("UNSUPPORTED_VERSION")
        require_id(index["corpus_id"])
        count = index["case_count"]
        docs_count = index["document_count"]
        if type(count) is not int or count not in (400, 800, 1600):
            raise _invalid("CASE_COUNT")
        if type(docs_count) is not int or not 1 <= docs_count <= count * 4:
            raise _invalid("DOCUMENT_COUNT")
        if index["corpus_id"] != f"gah-query-scale-v1-{count}":
            raise _invalid("CORPUS_BINDING_MISMATCH")
        if type(index["claims"]) is not dict or index["claims"] != {
            "runtime_admission": False, "semantic_oracle_independence": False,
            "real_workload_performance": False,
        } or any(type(flag) is not bool for flag in index["claims"].values()):
            raise _invalid("CLAIMS_INVALID")
        if (type(index["stage_counts"]) is not dict or set(index["stage_counts"]) != {"1", "2"}
                or any(type(v) is not int for v in index["stage_counts"].values())
                or index["stage_counts"] != {"1": count, "2": 0}):
            raise _invalid("STAGE_COUNTS_INVALID")
        require_uint(index["reconstructed_bytes"], maximum=MAX_CORPUS_BYTES)
        if index["reconstructed_bytes"] == 0:
            raise _invalid("CORPUS_SIZE")
        require_digest(index["reconstructed_digest"])
        require_ref(index["case_set_index_ref"])
        if index["case_set_index_ref"]["kind"] != CASE_SET_INDEX_KIND:
            raise _invalid("REFERENCE_KIND")
        if type(index["ordered_document_segments"]) is not list or not 1 <= len(index["ordered_document_segments"]) <= MAX_DOCUMENT_SEGMENTS:
            raise _invalid("DOCUMENT_SEGMENT_COUNT")
        _canonical_snapshot(index, MAX_ARTIFACT_BYTES)

        _, case_index = _canonical_snapshot(case_set_index, MAX_ARTIFACT_BYTES)
        if type(case_index) is not dict or content_ref(CASE_SET_INDEX_KIND, case_index.get("case_set_id"), case_index) != index["case_set_index_ref"]:
            raise _invalid("CASE_SET_INDEX_REF_MISMATCH")
        case_parts = _bounded_sequence(case_set_segments, MAX_CASE_SET_SEGMENTS)
        case_parts = tuple(_canonical_snapshot(part, MAX_ARTIFACT_BYTES)[1] for part in case_parts)
        case_set = restore_case_set(case_index, case_parts)
        if len(case_set["cases"]) != count:
            raise _invalid("CASE_COUNT")

        supplied = _bounded_sequence(document_segments, MAX_DOCUMENT_SEGMENTS)
        refs = index["ordered_document_segments"]
        if len(supplied) != len(refs):
            raise _invalid("DOCUMENT_SEGMENT_COUNT")
        parts = tuple(_canonical_snapshot(part, MAX_ARTIFACT_BYTES)[1] for part in supplied)
        documents: list[dict[str, Any]] = []
        seen_ids: set[tuple[str, str]] = set()
        ordinal = 0
        for i, (reference, segment) in enumerate(zip(refs, parts)):
            require_ref(reference)
            require_object(segment, _DOCUMENT_SEGMENT_FIELDS)
            if (reference["kind"] != DOCUMENT_SEGMENT_KIND
                    or type(segment["schema_version"]) is not int or segment["schema_version"] != 2
                    or segment["kind"] != DOCUMENT_SEGMENT_KIND
                    or segment["corpus_id"] != index["corpus_id"]
                    or type(segment["segment_index"]) is not int or segment["segment_index"] != i
                    or segment["id"] != _segment_id(index["corpus_id"], i)
                    or reference["id"] != segment["id"]
                    or type(segment["first_document_ordinal"]) is not int or segment["first_document_ordinal"] != ordinal):
                raise _invalid("DOCUMENT_SEGMENT_BINDING_MISMATCH")
            entries = segment["documents"]
            if (type(entries) is not list or not entries
                    or type(segment["document_count"]) is not int or segment["document_count"] != len(entries)):
                raise _invalid("DOCUMENT_COUNT")
            seg_raw, _ = _canonical_snapshot(segment, MAX_ARTIFACT_BYTES)
            expected_ref = {"kind": DOCUMENT_SEGMENT_KIND, "id": segment["id"], "digest": hashlib.sha256(seg_raw).hexdigest()}
            if reference != expected_ref:
                raise _invalid("REFERENCE_MISMATCH")
            for item in entries:
                if type(item) is not dict or set(item) != _DOCUMENT_FIELDS:
                    raise _invalid()
                doc_ref = item["ref"]
                require_ref(doc_ref)
                if doc_ref["kind"] not in _ALLOWED_DOCUMENT_KINDS:
                    raise _invalid("DOCUMENT_KIND")
                doc_raw, document = _canonical_snapshot(item["document"], MAX_DOCUMENT_BYTES)
                if type(document) is not dict or hashlib.sha256(doc_raw).hexdigest() != doc_ref["digest"]:
                    raise _invalid("DOCUMENT_DIGEST_MISMATCH")
                key = (doc_ref["kind"], doc_ref["id"])
                if key in seen_ids:
                    raise _invalid("DUPLICATE_DOCUMENT")
                seen_ids.add(key)
                documents.append({"ref": copy.deepcopy(doc_ref), "document": document})
            ordinal += len(entries)
        if ordinal != docs_count:
            raise _invalid("DOCUMENT_COUNT")

        corpus = {
            "schema_version": 1, "kind": "query_scale_corpus",
            "corpus_id": index["corpus_id"], "case_set": case_set,
            "documents": documents, "claims": copy.deepcopy(index["claims"]),
            "stage_counts": {"1": count, "2": 0},
        }
        raw, private_corpus = _canonical_snapshot(corpus, MAX_CORPUS_BYTES)
        if len(raw) != index["reconstructed_bytes"] or hashlib.sha256(raw).hexdigest() != index["reconstructed_digest"]:
            raise _invalid("RECONSTRUCTED_DIGEST_MISMATCH")
        try:
            return validate_scale_corpus(private_corpus)
        except ContractError as exc:
            raise _invalid(getattr(exc, "code", "QUERY_SCALE_CORPUS_INVALID")) from None
    except ContractError as exc:
        raise _invalid(getattr(exc, "code", "QUERY_SCALE_PARTITION_INVALID")) from None

__all__ = [
    "DOCUMENT_SEGMENT_KIND", "INDEX_KIND", "MAX_CORPUS_BYTES", "MAX_DOCUMENT_SEGMENTS",
    "partition_scale_corpus", "restore_scale_corpus",
]
