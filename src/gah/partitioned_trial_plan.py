"""Pure, in-memory partitioning for oversized logical TrialPlans.

This module does not provide persistence, authority, freshness, or runtime
admission.  Every wire artifact remains independently bounded and strict.
Raw duplicate-key detection belongs to the strict wire decoder; these APIs receive Python values.
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Sequence

from .contracts import ContractError, MAX_INTEGER, require_digest, require_id, require_object, require_ref, require_uint
from .run_contracts import _validate_entry, content_ref, validate_trial_plan


MAX_ARTIFACT_BYTES = 900_000
MAX_SEGMENTS = 16
MAX_ENTRIES = 10_000
MAX_RECONSTRUCTED_BYTES = 8 * 1024 * 1024
MAX_NODES = 100_000
MAX_DEPTH = 16

SEGMENT_KIND = "trial_plan_entries_segment"
INDEX_KIND = "trial_plan_index"
_PLAN_FIELDS = {"schema_version", "kind", "plan_id", "contract_ref", "entries"}
_SEGMENT_FIELDS = {
    "schema_version", "kind", "id", "plan_id", "contract_ref",
    "segment_index", "first_entry_ordinal", "entry_count", "entries",
}
_INDEX_FIELDS = {
    "schema_version", "kind", "plan_id", "contract_ref", "entry_count",
    "segment_count", "ordered_segments", "reconstructed_bytes", "reconstructed_digest",
}


def _invalid(code: str = "PARTITIONED_PLAN_INVALID") -> ContractError:
    return ContractError(code)


def _strict_tree(value: Any) -> tuple[int, int]:
    """Apply the existing plain-JSON type/depth/node/integer rules."""
    pending = [(value, 0)]
    nodes = 0
    while pending:
        current, depth = pending.pop()
        nodes += 1
        if depth > MAX_DEPTH or nodes > MAX_NODES:
            raise _invalid("DOCUMENT_COMPLEXITY")
        if type(current) is dict:
            if any(type(key) is not str for key in current):
                raise _invalid()
            try:
                for key in current:
                    key.encode("utf-8")
            except UnicodeError:
                raise _invalid() from None
            pending.extend((child, depth + 1) for child in current.values())
        elif type(current) is list:
            pending.extend((child, depth + 1) for child in current)
        elif type(current) is int:
            if not -MAX_INTEGER <= current <= MAX_INTEGER:
                raise _invalid("INTEGER_RANGE")
        elif type(current) is str:
            try:
                current.encode("utf-8")
            except UnicodeError:
                raise _invalid() from None
        elif current is not None and type(current) is not bool:
            raise _invalid()
    return nodes, depth


def _canonical(value: Any, *, maximum: int) -> bytes:
    _strict_tree(value)
    try:
        raw = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise _invalid() from None
    if len(raw) > maximum:
        raise _invalid("DOCUMENT_SIZE")
    return raw


def _segment_id(plan_id: str, index: int) -> str:
    digest = hashlib.sha256(plan_id.encode("utf-8")).hexdigest()[:32]
    return f"tp-{digest}-{index:04d}"


def _validate_plan_header(value: Any) -> tuple[str, dict[str, str], list[dict[str, Any]]]:
    require_object(value, _PLAN_FIELDS)
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise _invalid("UNSUPPORTED_VERSION")
    if value["kind"] != "trial_plan":
        raise _invalid()
    require_id(value["plan_id"])
    require_ref(value["contract_ref"])
    if value["contract_ref"]["kind"] != "evaluation_contract":
        raise _invalid("REFERENCE_KIND")
    entries = value["entries"]
    if type(entries) is not list or not 1 <= len(entries) <= MAX_ENTRIES:
        raise _invalid("ENTRY_COUNT")
    return value["plan_id"], value["contract_ref"], entries


def _validate_entries(entries: list[dict[str, Any]]) -> None:
    seen: set[tuple[str, str, str, str]] = set()
    for entry in entries:
        _validate_entry(entry)
        key = (entry["obligation_id"], entry["case_id"], entry["trial_id"], entry["variant"])
        if key in seen:
            raise _invalid("DUPLICATE_ID")
        seen.add(key)


def _logical_plan(plan_id: str, contract_ref: dict[str, str], entries: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "kind": "trial_plan",
        "plan_id": plan_id,
        "contract_ref": contract_ref,
        "entries": entries,
    }


def partition_trial_plan(plan: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return a closed v2 index and ordered segments without mutating input."""
    plan_id, contract_ref, input_entries = _validate_plan_header(plan)
    # Validate each entry before copying or serializing it; this also rejects
    # non-plain values hidden in nested refs/stage arrays.
    _validate_entries(input_entries)
    # Canonical serialization is read-only; detach entries once into returned segments below.
    logical = _logical_plan(plan_id, contract_ref, input_entries)
    reconstructed = _canonical(logical, maximum=MAX_RECONSTRUCTED_BYTES)

    def empty_segment(index: int, ordinal: int) -> dict[str, Any]:
        return {
            "schema_version": 2,
            "kind": SEGMENT_KIND,
            "id": _segment_id(plan_id, index),
            "plan_id": plan_id,
            "contract_ref": contract_ref,
            "segment_index": index,
            "first_entry_ordinal": ordinal,
            "entry_count": 1,
            "entries": [],
        }

    # Measure each canonical entry exactly once. Segment lengths are then
    # accumulated from the canonical header, array delimiters and commas.
    entry_lengths = [
        len(_canonical(entry, maximum=MAX_ARTIFACT_BYTES))
        for entry in input_entries
    ]
    ranges: list[tuple[int, int, int]] = []
    start = 0
    entry_bytes = 0
    fixed_header_bytes = len(_canonical(
        empty_segment(0, 0), maximum=MAX_ARTIFACT_BYTES
    )) - len(b"1") - len(b"[]")

    def exact_projected_size(header_bytes: int, count: int, contents_bytes: int) -> int:
        return header_bytes + len(str(count)) + 2 + contents_bytes + max(0, count - 1)

    for ordinal, size in enumerate(entry_lengths):
        if ordinal > start:
            count = ordinal - start + 1
            projected = exact_projected_size(fixed_header_bytes, count, entry_bytes + size)
            if projected > MAX_ARTIFACT_BYTES:
                previous_count = ordinal - start
                previous_size = exact_projected_size(fixed_header_bytes, previous_count, entry_bytes)
                ranges.append((start, ordinal, previous_size))
                if len(ranges) >= MAX_SEGMENTS:
                    raise _invalid("SEGMENT_LIMIT")
                start = ordinal
                empty = empty_segment(len(ranges), start)
                fixed_header_bytes = len(_canonical(
                    empty, maximum=MAX_ARTIFACT_BYTES
                )) - len(b"1") - len(b"[]")
                entry_bytes = 0
        count = ordinal - start + 1
        projected = exact_projected_size(fixed_header_bytes, count, entry_bytes + size)
        if projected > MAX_ARTIFACT_BYTES:
            raise _invalid("SEGMENT_TOO_LARGE")
        entry_bytes += size
    final_count = len(input_entries) - start
    ranges.append((start, len(input_entries), exact_projected_size(
        fixed_header_bytes, final_count, entry_bytes
    )))
    if not 1 <= len(ranges) <= MAX_SEGMENTS:
        raise _invalid("SEGMENT_LIMIT")

    segment_values: list[dict[str, Any]] = []
    segment_raw_values: list[bytes] = []
    for segment_index, (first, end, predicted_bytes) in enumerate(ranges):
        selected_entries = copy.deepcopy(input_entries[first:end])
        segment = {
            "schema_version": 2,
            "kind": SEGMENT_KIND,
            "id": _segment_id(plan_id, segment_index),
            "plan_id": plan_id,
            "contract_ref": copy.deepcopy(contract_ref),
            "segment_index": segment_index,
            "first_entry_ordinal": first,
            "entry_count": end - first,
            "entries": selected_entries,
        }
        # Assert the incremental counter against the exact bytes whose digest is published.
        segment_raw = _canonical(segment, maximum=MAX_ARTIFACT_BYTES)
        if len(segment_raw) != predicted_bytes:
            raise _invalid("SEGMENT_SIZE_MISMATCH")
        segment_raw_values.append(segment_raw)
        segment_values.append(segment)
    refs = [
        {"kind": SEGMENT_KIND, "id": segment["id"], "digest": hashlib.sha256(raw).hexdigest()}
        for segment, raw in zip(segment_values, segment_raw_values)
    ]
    index = {
        "schema_version": 2,
        "kind": INDEX_KIND,
        "plan_id": plan_id,
        "contract_ref": copy.deepcopy(contract_ref),
        "entry_count": len(input_entries),
        "segment_count": len(segment_values),
        "ordered_segments": refs,
        "reconstructed_bytes": len(reconstructed),
        "reconstructed_digest": hashlib.sha256(reconstructed).hexdigest(),
    }
    _canonical(index, maximum=MAX_ARTIFACT_BYTES)
    return index, segment_values


def validate_partitioned_trial_plan(index: Any, segments: Sequence[Any]) -> dict[str, Any]:
    """Validate all references and entries, then return an independent v1-shaped plan."""
    require_object(index, _INDEX_FIELDS)
    if type(index["schema_version"]) is not int or index["schema_version"] != 2:
        raise _invalid("UNSUPPORTED_VERSION")
    if index["kind"] != INDEX_KIND:
        raise _invalid()
    plan_id = index["plan_id"]
    require_id(plan_id)
    contract_ref = index["contract_ref"]
    require_ref(contract_ref)
    if contract_ref["kind"] != "evaluation_contract":
        raise _invalid("REFERENCE_KIND")
    entry_count = index["entry_count"]
    segment_count = index["segment_count"]
    if (type(entry_count) is not int or not 1 <= entry_count <= MAX_ENTRIES
            or type(segment_count) is not int or not 1 <= segment_count <= MAX_SEGMENTS
            or type(segments) not in (list, tuple) or len(segments) != segment_count
            or type(index["ordered_segments"]) is not list
            or len(index["ordered_segments"]) != segment_count):
        raise _invalid("SEGMENT_COUNT")
    require_uint(index["reconstructed_bytes"], maximum=MAX_RECONSTRUCTED_BYTES)
    require_digest(index["reconstructed_digest"])
    _canonical(index, maximum=MAX_ARTIFACT_BYTES)

    collected: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    ordinal = 0
    seen_ref_keys: set[tuple[str, str, str]] = set()
    for expected_index, (reference, segment) in enumerate(zip(index["ordered_segments"], segments)):
        require_ref(reference)
        ref_key = (reference["kind"], reference["id"], reference["digest"])
        if ref_key in seen_ref_keys:
            raise _invalid("DUPLICATE_REFERENCE")
        seen_ref_keys.add(ref_key)
        if reference["kind"] != SEGMENT_KIND:
            raise _invalid("REFERENCE_KIND")
        require_object(segment, _SEGMENT_FIELDS)
        if (type(segment["schema_version"]) is not int or segment["schema_version"] != 2
                or segment["kind"] != SEGMENT_KIND
                or segment["id"] != _segment_id(plan_id, expected_index)
                or reference["id"] != segment["id"]
                or segment["plan_id"] != plan_id
                or segment["contract_ref"] != contract_ref
                or type(segment["segment_index"]) is not int
                or segment["segment_index"] != expected_index
                or type(segment["first_entry_ordinal"]) is not int
                or segment["first_entry_ordinal"] != ordinal):
            raise _invalid("SEGMENT_BINDING_MISMATCH")
        entries = segment["entries"]
        if (type(entries) is not list or not entries
                or type(segment["entry_count"]) is not int
                or segment["entry_count"] != len(entries)):
            raise _invalid("ENTRY_COUNT")
        _canonical(segment, maximum=MAX_ARTIFACT_BYTES)
        if content_ref(SEGMENT_KIND, segment["id"], segment) != reference:
            raise _invalid("REFERENCE_MISMATCH")
        for entry in entries:
            _validate_entry(entry)
            key = (entry["obligation_id"], entry["case_id"], entry["trial_id"], entry["variant"])
            if key in seen:
                raise _invalid("DUPLICATE_ID")
            seen.add(key)
            collected.append(copy.deepcopy(entry))
        ordinal += len(entries)
    if ordinal != entry_count:
        raise _invalid("ENTRY_COUNT")

    plan = _logical_plan(plan_id, copy.deepcopy(contract_ref), collected)
    reconstructed = _canonical(plan, maximum=MAX_RECONSTRUCTED_BYTES)
    if (len(reconstructed) != index["reconstructed_bytes"]
            or hashlib.sha256(reconstructed).hexdigest() != index["reconstructed_digest"]):
        raise _invalid("RECONSTRUCTED_DIGEST_MISMATCH")
    # Compare the historical validator for plans that fit its legacy wire cap.
    from .contracts import MAX_DOCUMENT_BYTES
    if len(reconstructed) <= MAX_DOCUMENT_BYTES:
        try:
            if validate_trial_plan(plan) != plan:
                raise _invalid()
        except ContractError as exc:
            raise _invalid(getattr(exc, "code", "PARTITIONED_PLAN_INVALID")) from None
    return plan


def restore_trial_plan(index: Any, segments: Sequence[Any]) -> dict[str, Any]:
    """Alias for the validated reconstruction API; no refs are resolved here."""
    return validate_partitioned_trial_plan(index, segments)


__all__ = [
    "INDEX_KIND", "MAX_ARTIFACT_BYTES", "MAX_ENTRIES", "MAX_RECONSTRUCTED_BYTES",
    "MAX_SEGMENTS", "SEGMENT_KIND", "partition_trial_plan", "restore_trial_plan",
    "validate_partitioned_trial_plan",
]
