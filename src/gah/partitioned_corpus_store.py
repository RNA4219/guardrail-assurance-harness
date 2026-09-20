"""Authority-backed storage for fixed, partitioned query-scale corpora.

Provisioning only: this module does not adopt a contract, admit runtime work,
or enable CI. Every action is re-authorized and rebound to current policy.
"""
from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from typing import Any

from .adoption import AdoptionError
from .contracts import ContractError, MAX_DOCUMENT_BYTES, MAX_INTEGER, require_digest, require_id, require_ref, require_uint
from .partitioned_case_set import INDEX_KIND as CASE_SET_INDEX_KIND, MAX_ARTIFACT_BYTES, MAX_SEGMENTS as MAX_CASE_SET_SEGMENTS, SEGMENT_KIND as CASE_SET_CODEC_SEGMENT_KIND
from .partitioned_scale_corpus import (
    DOCUMENT_SEGMENT_KIND, INDEX_KIND, MAX_CORPUS_BYTES, MAX_DOCUMENT_SEGMENTS,
    restore_scale_corpus,
)
from .partitioned_trial_plan import _canonical as _codec_canonical
from .query_scale_data import build_scale_corpus
from .run_contracts import content_ref

UPLOAD_TTL = 3600
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_COMMITS = 256
CASE_SET_SEGMENT_KIND = "case_set_segment"  # storage artifact kind
_BASE = {"schema_version", "action", "request_id"}
_UPLOAD = _BASE | {"upload_id"}
_ACTION_FIELDS = {
    "corpus_partition_begin": _BASE | {"upload_id", "policy_series_id", "expected_policy_generation", "expected_permission_generation", "index", "case_set_index"},
    "corpus_partition_put_case_set_segment": _UPLOAD | {"segment"},
    "corpus_partition_put_document_segment": _UPLOAD | {"segment"},
    "corpus_partition_status": _UPLOAD,
    "corpus_partition_commit": _UPLOAD,
    "corpus_partition_abort": _UPLOAD,
    "corpus_partition_read": _BASE | {"policy_series_id", "corpus_ref", "artifact_kind", "segment_index"},
}
FIELDS = set(_ACTION_FIELDS)
ACTIONS = {
    "corpus_partition_begin": {"manager"},
    "corpus_partition_put_case_set_segment": {"manager"},
    "corpus_partition_put_document_segment": {"manager"},
    "corpus_partition_status": {"manager"},
    "corpus_partition_commit": {"manager"},
    "corpus_partition_abort": {"manager"},
    "corpus_partition_read": {"manager", "validator", "operator"},
}
FRESH_ACTIONS = set(ACTIONS)
TABLES = {
    "partition_scale_corpus_upload": {
        "singleton", "upload_id", "corpus_id", "policy_series_id", "policy_generation", "policy_ref_json",
        "corpus_index_json", "corpus_index_digest", "case_set_index_json", "case_set_index_digest",
        "expected_case_set_segments", "expected_document_segments", "stored_bytes", "owner_actor",
        "owner_context", "permission_generation", "extension_digest", "created_at", "expires_at",
    },
    "partition_scale_corpus_segments": {"upload_id", "artifact_kind", "segment_index", "segment_json", "segment_digest", "byte_count"},
    "partition_scale_corpus_commits": {
        "corpus_id", "policy_series_id", "policy_generation", "upload_id", "policy_ref_json",
        "corpus_index_json", "corpus_index_digest", "case_set_index_json", "case_set_index_digest",
        "case_set_ref_json", "artifact_bytes", "owner_actor", "owner_context", "permission_generation",
        "extension_digest", "committed_at",
    },
    "partition_scale_corpus_committed_segments": {
        "corpus_id", "policy_series_id", "policy_generation", "artifact_kind", "segment_index",
        "segment_json", "segment_digest", "byte_count",
    },
}
_ARTIFACT_KINDS = {"corpus_index", "case_set_index", "case_set_segment", "document_segment"}
_INDEX_FIELDS = {
    "schema_version", "kind", "corpus_id", "case_count", "document_count", "claims", "stage_counts",
    "case_set_index_ref", "ordered_document_segments", "reconstructed_bytes", "reconstructed_digest",
}
_CASE_INDEX_FIELDS = {
    "schema_version", "kind", "case_set_id", "purpose", "required_categories", "case_set_ref",
    "case_count", "segment_count", "ordered_segments", "reconstructed_bytes", "reconstructed_digest",
}
_CASE_SEGMENT_FIELDS = {"schema_version", "kind", "id", "case_set_id", "segment_index", "first_case_ordinal", "case_count", "cases"}
_DOC_SEGMENT_FIELDS = {"schema_version", "kind", "id", "corpus_id", "segment_index", "first_document_ordinal", "document_count", "documents"}


def _fail(code: str) -> None:
    raise AdoptionError(code)


def _canonical(value: Any, maximum: int = MAX_ARTIFACT_BYTES) -> tuple[str, str, int]:
    try:
        raw = _canonical_bytes(value, maximum)
    except AdoptionError:
        raise
    except Exception:
        _fail("INVALID_REQUEST")
    if len(raw) > maximum:
        _fail("DOCUMENT_SIZE")
    return raw.decode("utf-8"), hashlib.sha256(raw).hexdigest(), len(raw)


def _canonical_bytes(value: Any, maximum: int) -> bytes:
    try:
        raw = _codec_canonical(value, maximum=maximum)
    except (ContractError, TypeError, ValueError, UnicodeError, RecursionError):
        _fail("INVALID_REQUEST")
    return raw


def _snapshot(value: Any, maximum: int = MAX_ARTIFACT_BYTES) -> dict[str, Any]:
    if type(value) is not dict:
        _fail("INVALID_REQUEST")
    raw, _, _ = _canonical(value, maximum)
    try:
        result = json.loads(raw)
    except (ValueError, RecursionError):
        _fail("INVALID_REQUEST")
    if type(result) is not dict:
        _fail("INVALID_REQUEST")
    return result


def _validate_ref(value: Any, kind: str) -> None:
    try:
        require_ref(value)
    except ContractError:
        _fail("INVALID_REQUEST")
    if value["kind"] != kind:
        _fail("REFERENCE_KIND")


def _uint(value: Any, maximum: int = MAX_INTEGER) -> None:
    try:
        require_uint(value, maximum=maximum)
    except ContractError:
        _fail("INVALID_REQUEST")


def _validate_index(value: Any) -> dict[str, Any]:
    index = _snapshot(value)
    if set(index) != _INDEX_FIELDS or type(index.get("schema_version")) is not int or index["schema_version"] != 2 or index.get("kind") != INDEX_KIND:
        _fail("CORPUS_INVALID")
    try:
        require_id(index["corpus_id"])
        require_digest(index["reconstructed_digest"])
        _uint(index["case_count"], 1600)
        _uint(index["document_count"], 6400)
        _uint(index["reconstructed_bytes"], MAX_CORPUS_BYTES)
        if index["case_count"] not in (400, 800, 1600) or index["document_count"] < 1 or index["reconstructed_bytes"] == 0:
            _fail("CORPUS_INVALID")
        _validate_ref(index["case_set_index_ref"], CASE_SET_INDEX_KIND)
        refs = index["ordered_document_segments"]
        if type(refs) is not list or not 1 <= len(refs) <= MAX_DOCUMENT_SEGMENTS:
            _fail("CORPUS_INVALID")
        for ref in refs:
            _validate_ref(ref, DOCUMENT_SEGMENT_KIND)
        if (type(index["claims"]) is not dict or type(index["stage_counts"]) is not dict):
            _fail("CORPUS_INVALID")
    except (KeyError, ContractError):
        _fail("CORPUS_INVALID")
    return index


def _validate_case_index(value: Any) -> dict[str, Any]:
    index = _snapshot(value)
    if set(index) != _CASE_INDEX_FIELDS or type(index.get("schema_version")) is not int or index["schema_version"] != 2 or index.get("kind") != CASE_SET_INDEX_KIND:
        _fail("CORPUS_INVALID")
    try:
        require_id(index["case_set_id"])
        require_digest(index["reconstructed_digest"])
        _uint(index["case_count"], 1600)
        _uint(index["segment_count"], MAX_CASE_SET_SEGMENTS)
        _uint(index["reconstructed_bytes"], MAX_DOCUMENT_BYTES)
        if index["case_count"] not in (400, 800, 1600) or index["segment_count"] < 1 or index["reconstructed_bytes"] == 0:
            _fail("CORPUS_INVALID")
        _validate_ref(index["case_set_ref"], "case_set")
        refs = index["ordered_segments"]
        if type(refs) is not list or len(refs) != index["segment_count"]:
            _fail("CORPUS_INVALID")
        for ref in refs:
            _validate_ref(ref, CASE_SET_CODEC_SEGMENT_KIND)
    except (KeyError, ContractError):
        _fail("CORPUS_INVALID")
    return index


def _validate_segment(value: Any, *, case_set: bool) -> dict[str, Any]:
    segment = _snapshot(value)
    fields = _CASE_SEGMENT_FIELDS if case_set else _DOC_SEGMENT_FIELDS
    kind = CASE_SET_CODEC_SEGMENT_KIND if case_set else DOCUMENT_SEGMENT_KIND
    if set(segment) != fields or type(segment.get("schema_version")) is not int or segment["schema_version"] != 2 or segment.get("kind") != kind:
        _fail("CORPUS_INVALID")
    try:
        require_id(segment["id"])
        require_id(segment["case_set_id" if case_set else "corpus_id"])
        _uint(segment["segment_index"], (MAX_CASE_SET_SEGMENTS if case_set else MAX_DOCUMENT_SEGMENTS) - 1)
        first_key = "first_case_ordinal" if case_set else "first_document_ordinal"
        count_key = "case_count" if case_set else "document_count"
        _uint(segment[first_key], 6400)
        _uint(segment[count_key], 6400)
        values_key = "cases" if case_set else "documents"
        if type(segment[values_key]) is not list or len(segment[values_key]) != segment[count_key] or not segment[values_key]:
            _fail("CORPUS_INVALID")
        if not case_set:
            for item in segment[values_key]:
                if type(item) is not dict or set(item) != {"ref", "document"}:
                    _fail("CORPUS_INVALID")
    except (KeyError, ContractError):
        _fail("CORPUS_INVALID")
    return segment


def validate_request(value: Any) -> dict[str, Any]:
    if type(value) is not dict or type(value.get("action")) is not str:
        _fail("INVALID_REQUEST")
    action = value["action"]
    fields = _ACTION_FIELDS.get(action)
    if fields is None:
        _fail("INVALID_ACTION")
    if set(value) != fields:
        _fail("INVALID_REQUEST")
    if type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        _fail("UNSUPPORTED_VERSION")
    try:
        require_id(value["request_id"])
        if action == "corpus_partition_begin":
            require_id(value["upload_id"])
            require_id(value["policy_series_id"])
            require_uint(value["expected_policy_generation"])
            require_uint(value["expected_permission_generation"])
            index = _validate_index(value["index"])
            case_index = _validate_case_index(value["case_set_index"])
            if (index["case_count"] != case_index["case_count"]
                    or index["case_set_index_ref"] != content_ref(CASE_SET_INDEX_KIND, case_index["case_set_id"], case_index)):
                _fail("REFERENCE_MISMATCH")
        elif action == "corpus_partition_read":
            require_id(value["policy_series_id"])
            _validate_ref(value["corpus_ref"], INDEX_KIND)
            if type(value["artifact_kind"]) is not str or value["artifact_kind"] not in _ARTIFACT_KINDS:
                _fail("INVALID_REQUEST")
            segment_index = value["segment_index"]
            if value["artifact_kind"] in {"corpus_index", "case_set_index"}:
                if segment_index is not None:
                    _fail("INVALID_REQUEST")
            else:
                maximum = MAX_CASE_SET_SEGMENTS if value["artifact_kind"] == "case_set_segment" else MAX_DOCUMENT_SEGMENTS
                require_uint(segment_index, maximum=maximum - 1)
        else:
            require_id(value["upload_id"])
            if action == "corpus_partition_put_case_set_segment":
                _validate_segment(value["segment"], case_set=True)
            elif action == "corpus_partition_put_document_segment":
                _validate_segment(value["segment"], case_set=False)
        return copy.deepcopy(value)
    except ContractError:
        _fail("INVALID_REQUEST")


def create_schema(db: sqlite3.Connection) -> None:
    if not db.in_transaction:
        _fail("TRANSACTION_REQUIRED")
    db.execute("CREATE TABLE partition_scale_corpus_upload(singleton INTEGER PRIMARY KEY CHECK(singleton=1),upload_id TEXT NOT NULL UNIQUE,corpus_id TEXT NOT NULL,policy_series_id TEXT NOT NULL,policy_generation INTEGER NOT NULL,policy_ref_json TEXT NOT NULL,corpus_index_json TEXT NOT NULL,corpus_index_digest TEXT NOT NULL,case_set_index_json TEXT NOT NULL,case_set_index_digest TEXT NOT NULL,expected_case_set_segments INTEGER NOT NULL,expected_document_segments INTEGER NOT NULL,stored_bytes INTEGER NOT NULL,owner_actor TEXT NOT NULL,owner_context TEXT NOT NULL,permission_generation INTEGER NOT NULL,extension_digest TEXT NOT NULL,created_at INTEGER NOT NULL,expires_at INTEGER NOT NULL)")
    db.execute("CREATE TABLE partition_scale_corpus_segments(upload_id TEXT NOT NULL REFERENCES partition_scale_corpus_upload(upload_id) ON DELETE CASCADE,artifact_kind TEXT NOT NULL,segment_index INTEGER NOT NULL,segment_json TEXT NOT NULL,segment_digest TEXT NOT NULL,byte_count INTEGER NOT NULL,PRIMARY KEY(upload_id,artifact_kind,segment_index))")
    db.execute("CREATE TABLE partition_scale_corpus_commits(corpus_id TEXT NOT NULL,policy_series_id TEXT NOT NULL,policy_generation INTEGER NOT NULL,upload_id TEXT NOT NULL UNIQUE,policy_ref_json TEXT NOT NULL,corpus_index_json TEXT NOT NULL,corpus_index_digest TEXT NOT NULL,case_set_index_json TEXT NOT NULL,case_set_index_digest TEXT NOT NULL,case_set_ref_json TEXT NOT NULL,artifact_bytes INTEGER NOT NULL,owner_actor TEXT NOT NULL,owner_context TEXT NOT NULL,permission_generation INTEGER NOT NULL,extension_digest TEXT NOT NULL,committed_at INTEGER NOT NULL,PRIMARY KEY(corpus_id,policy_series_id,policy_generation))")
    db.execute("CREATE TABLE partition_scale_corpus_committed_segments(corpus_id TEXT NOT NULL,policy_series_id TEXT NOT NULL,policy_generation INTEGER NOT NULL,artifact_kind TEXT NOT NULL,segment_index INTEGER NOT NULL,segment_json TEXT NOT NULL,segment_digest TEXT NOT NULL,byte_count INTEGER NOT NULL,PRIMARY KEY(corpus_id,policy_series_id,policy_generation,artifact_kind,segment_index),FOREIGN KEY(corpus_id,policy_series_id,policy_generation) REFERENCES partition_scale_corpus_commits(corpus_id,policy_series_id,policy_generation) ON DELETE CASCADE)")


def _source_digest(extension: Any, store: Any) -> str:
    from .partitioned_corpus_authority import PartitionedCorpusEvaluationExtension, source_digest
    if type(extension) is not PartitionedCorpusEvaluationExtension or extension is not getattr(store, "_extension", None):
        _fail("EXTENSION_INVALID")
    try:
        digest = source_digest()
    except Exception:
        _fail("EXTENSION_INVALID")
    if type(digest) is not str or digest != getattr(store, "_extension_digest", None) or digest != getattr(extension, "digest", None):
        _fail("EXTENSION_INVALID")
    return digest


def _fresh_policy(extension: Any, store: Any, db: sqlite3.Connection, series_id: str, now: int, *, expected_generation: int | None = None, expected_permission: int | None = None) -> tuple[dict[str, Any], dict[str, str], int, int, str]:
    from . import evaluation_authority
    before = _source_digest(extension, store)
    try:
        policy, ref, generation = evaluation_authority._policy(store, db, series_id, now)
        permission = store._permission_generation(db)
    except AdoptionError:
        raise
    except Exception:
        _fail("POLICY_STALE")
    if policy.get("policy_id") != series_id:
        _fail("POLICY_STALE")
    if expected_generation is not None and generation != expected_generation:
        _fail("POLICY_STALE")
    if expected_permission is not None and permission != expected_permission:
        _fail("PERMISSION_STALE")
    after = _source_digest(extension, store)
    if before != after:
        _fail("EXTENSION_INVALID")
    return policy, ref, generation, permission, before


def _load_json(raw: str, digest: str, maximum: int = MAX_ARTIFACT_BYTES) -> dict[str, Any]:
    try:
        value = json.loads(raw)
        canonical, actual_digest, size = _canonical(value, maximum)
    except (TypeError, ValueError, KeyError, RecursionError, AdoptionError):
        _fail("STORAGE_CORRUPT")
    if type(value) is not dict or canonical != raw or actual_digest != digest or size > maximum:
        _fail("STORAGE_CORRUPT")
    return value


def _upload(db: sqlite3.Connection, upload_id: str, actor_id: str, context: str) -> sqlite3.Row:
    row = db.execute("SELECT * FROM partition_scale_corpus_upload WHERE singleton=1").fetchone()
    if row is None or row["upload_id"] != upload_id:
        _fail("UPLOAD_MISSING")
    if row["owner_actor"] != actor_id or row["owner_context"] != context:
        _fail("PERMISSION_DENIED")
    if db.execute("SELECT 1 FROM partition_scale_corpus_commits WHERE upload_id=?", (upload_id,)).fetchone() is not None:
        _fail("STORAGE_CORRUPT")
    return row


def _check_upload(row: sqlite3.Row, permission: int, source: str, now: int) -> tuple[dict[str, Any], dict[str, Any]]:
    if type(row["expires_at"]) is not int or row["expires_at"] <= now:
        _fail("UPLOAD_STALE")
    if row["permission_generation"] != permission:
        _fail("PERMISSION_STALE")
    if row["extension_digest"] != source:
        _fail("EXTENSION_INVALID")
    index = _load_json(row["corpus_index_json"], row["corpus_index_digest"])
    case_index = _load_json(row["case_set_index_json"], row["case_set_index_digest"])
    try:
        _validate_index(index)
        _validate_case_index(case_index)
    except AdoptionError:
        _fail("STORAGE_CORRUPT")
    if (index["corpus_id"] != row["corpus_id"] or index["case_count"] != case_index["case_count"]
            or index["case_set_index_ref"] != content_ref(CASE_SET_INDEX_KIND, case_index["case_set_id"], case_index)
            or row["expected_case_set_segments"] != case_index["segment_count"]
            or row["expected_document_segments"] != len(index["ordered_document_segments"])):
        _fail("STORAGE_CORRUPT")
    return index, case_index


def _segments(db: sqlite3.Connection, upload_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    rows = db.execute("SELECT * FROM partition_scale_corpus_segments WHERE upload_id=? ORDER BY artifact_kind,segment_index", (upload_id,)).fetchall()
    case_segments: list[dict[str, Any]] = []
    doc_segments: list[dict[str, Any]] = []
    total = 0
    seen: set[tuple[str, int]] = set()
    for row in rows:
        kind, ordinal = row["artifact_kind"], row["segment_index"]
        if kind not in {"case_set_segment", "document_segment"} or type(ordinal) is not int or (kind, ordinal) in seen:
            _fail("STORAGE_CORRUPT")
        seen.add((kind, ordinal))
        segment = _load_json(row["segment_json"], row["segment_digest"])
        try:
            segment = _validate_segment(segment, case_set=(kind == "case_set_segment"))
        except AdoptionError:
            _fail("STORAGE_CORRUPT")
        if segment["segment_index"] != ordinal or row["byte_count"] != len(row["segment_json"].encode("utf-8")):
            _fail("STORAGE_CORRUPT")
        total += row["byte_count"]
        (case_segments if kind == "case_set_segment" else doc_segments).append(segment)
    return case_segments, doc_segments, total


def _restore(index: dict[str, Any], case_index: dict[str, Any], case_segments: list[dict[str, Any]], doc_segments: list[dict[str, Any]]) -> dict[str, Any]:
    try:
        corpus = restore_scale_corpus(index, case_index, case_segments, doc_segments)
        fixed = build_scale_corpus(index["case_count"])
        fixed_raw = _canonical_bytes(fixed, MAX_CORPUS_BYTES)
        restored_raw = _canonical_bytes(corpus, MAX_CORPUS_BYTES)
    except (ContractError, AdoptionError):
        _fail("CORPUS_INVALID")
    if fixed_raw != restored_raw:
        _fail("CORPUS_INVALID")
    return corpus


def _validate_committed(db: sqlite3.Connection, row: sqlite3.Row, permission: int, source: str) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    if row["permission_generation"] != permission:
        _fail("PERMISSION_STALE")
    if row["extension_digest"] != source:
        _fail("EXTENSION_INVALID")
    policy_ref = _load_json(row["policy_ref_json"], hashlib.sha256(row["policy_ref_json"].encode("utf-8")).hexdigest(), maximum=MAX_ARTIFACT_BYTES)
    index = _load_json(row["corpus_index_json"], row["corpus_index_digest"])
    case_index = _load_json(row["case_set_index_json"], row["case_set_index_digest"])
    try:
        _validate_index(index)
        _validate_case_index(case_index)
    except AdoptionError:
        _fail("STORAGE_CORRUPT")
    if row["corpus_id"] != index["corpus_id"] or row["policy_generation"] <= 0:
        _fail("STORAGE_CORRUPT")
    case_segments: list[dict[str, Any]] = []
    doc_segments: list[dict[str, Any]] = []
    segment_rows = db.execute("SELECT * FROM partition_scale_corpus_committed_segments WHERE corpus_id=? AND policy_series_id=? AND policy_generation=? ORDER BY artifact_kind,segment_index", (row["corpus_id"], row["policy_series_id"], row["policy_generation"])).fetchall()
    seen: set[tuple[str, int]] = set()
    total = len(row["corpus_index_json"].encode("utf-8")) + len(row["case_set_index_json"].encode("utf-8"))
    for item in segment_rows:
        kind, ordinal = item["artifact_kind"], item["segment_index"]
        if kind not in {"case_set_segment", "document_segment"} or type(ordinal) is not int or (kind, ordinal) in seen:
            _fail("STORAGE_CORRUPT")
        seen.add((kind, ordinal))
        segment = _load_json(item["segment_json"], item["segment_digest"])
        try:
            segment = _validate_segment(segment, case_set=(kind == "case_set_segment"))
        except AdoptionError:
            _fail("STORAGE_CORRUPT")
        if segment["segment_index"] != ordinal or item["byte_count"] != len(item["segment_json"].encode("utf-8")):
            _fail("STORAGE_CORRUPT")
        total += item["byte_count"]
        (case_segments if kind == "case_set_segment" else doc_segments).append(segment)
    corpus = _restore(index, case_index, case_segments, doc_segments)
    case_ref = content_ref("case_set", corpus["case_set"]["case_set_id"], corpus["case_set"])
    if row["case_set_ref_json"] != _canonical(case_ref)[0] or row["artifact_bytes"] != total:
        _fail("STORAGE_CORRUPT")
    return policy_ref, index, case_segments, doc_segments, corpus


def _result(action: str, request_id: str, **fields: Any) -> dict[str, Any]:
    return {"schema_version": 1, "kind": "partitioned_corpus_store_result", "action": action,
            "request_id": request_id, "ci_eligible": False, "adoption_verified": False,
            "runtime_verified": False, "authority_connected": False,
            "baseline_freshness_verified": False, **fields}


def _source_stable(extension: Any, store: Any, before: str) -> None:
    if _source_digest(extension, store) != before:
        _fail("EXTENSION_INVALID")


def handle(extension: Any, store: Any, db: sqlite3.Connection, request: Any, actor_id: str, context: str, now: int) -> dict[str, Any]:
    if not isinstance(db, sqlite3.Connection) or not db.in_transaction:
        _fail("TRANSACTION_REQUIRED")
    request = validate_request(request)
    action, request_id = request["action"], request["request_id"]
    if type(now) is not int or not 0 <= now <= MAX_INTEGER - UPLOAD_TTL:
        _fail("CLOCK_INVALID")
    if type(actor_id) is not str or type(context) is not str:
        _fail("PERMISSION_DENIED")

    if action == "corpus_partition_begin":
        policy, policy_ref, generation, permission, source = _fresh_policy(extension, store, db, request["policy_series_id"], now, expected_generation=request["expected_policy_generation"], expected_permission=request["expected_permission_generation"])
        index, case_index = request["index"], request["case_set_index"]
        if index["case_count"] not in (400, 800, 1600) or case_index["case_count"] != index["case_count"]:
            _fail("CORPUS_INVALID")
        try:
            from .partitioned_scale_corpus import partition_scale_corpus
            expected_index, expected_case_index, _, _ = partition_scale_corpus(build_scale_corpus(index["case_count"]))
        except (ContractError, ValueError, TypeError):
            _fail("CORPUS_INVALID")
        if index != expected_index or case_index != expected_case_index:
            _fail("CORPUS_INVALID")
        corpus_id = index["corpus_id"]
        cref = _canonical(policy_ref)[0]
        iraw, idigest, isize = _canonical(index)
        craw, cdigest, csize = _canonical(case_index)
        committed = db.execute("SELECT * FROM partition_scale_corpus_commits WHERE corpus_id=? AND policy_series_id=? AND policy_generation=?", (corpus_id, request["policy_series_id"], generation)).fetchone()
        if committed is not None:
            committed_policy, committed_index, _committed_cases, _committed_docs, _committed_corpus = _validate_committed(db, committed, permission, source)
            if committed["upload_id"] != request["upload_id"]:
                _fail("CORPUS_CONFLICT")
            if committed["owner_actor"] != actor_id or committed["owner_context"] != context:
                _fail("PERMISSION_DENIED")
            if committed_policy != policy_ref or committed_index != index:
                _fail("CORPUS_CONFLICT")
            if committed["corpus_index_json"] != iraw or committed["case_set_index_json"] != craw:
                _fail("CORPUS_CONFLICT")
            _source_stable(extension, store, source)
            return _result(action, request_id, upload_id=request["upload_id"], corpus_id=corpus_id,
                corpus_ref=content_ref(INDEX_KIND, corpus_id, index), expected_case_set_segments=case_index["segment_count"],
                expected_document_segments=len(index["ordered_document_segments"]), stored_bytes=committed["artifact_bytes"],
                expires_at=now, resumed=True, already_committed=True, committed_at=committed["committed_at"])

        current = db.execute("SELECT * FROM partition_scale_corpus_upload WHERE singleton=1").fetchone()
        if current is not None and current["expires_at"] <= now:
            if db.execute("SELECT 1 FROM partition_scale_corpus_commits WHERE upload_id=?", (current["upload_id"],)).fetchone() is not None:
                _fail("STORAGE_CORRUPT")
            db.execute("DELETE FROM partition_scale_corpus_segments WHERE upload_id=?", (current["upload_id"],))
            db.execute("DELETE FROM partition_scale_corpus_upload WHERE singleton=1")
            current = None
        if current is not None:
            if current["upload_id"] != request["upload_id"]:
                _fail("UPLOAD_BUSY")
            if current["owner_actor"] != actor_id or current["owner_context"] != context:
                _fail("PERMISSION_DENIED")
            _fresh_upload = _check_upload(current, permission, source, now)
            if (current["corpus_id"] != corpus_id or current["policy_series_id"] != request["policy_series_id"]
                    or current["policy_generation"] != generation or current["policy_ref_json"] != cref
                    or current["corpus_index_json"] != iraw or current["case_set_index_json"] != craw):
                _fail("UPLOAD_STALE")
            case_segments, doc_segments, segment_bytes = _segments(db, request["upload_id"])
            actual = isize + csize + segment_bytes
            if actual != current["stored_bytes"]:
                _fail("STORAGE_CORRUPT")
            _source_stable(extension, store, source)
            return _result(action, request_id, upload_id=request["upload_id"], corpus_id=corpus_id,
                corpus_ref=content_ref(INDEX_KIND, corpus_id, index), expected_case_set_segments=case_index["segment_count"],
                expected_document_segments=len(index["ordered_document_segments"]), stored_bytes=actual,
                expires_at=current["expires_at"], resumed=True, already_committed=False, committed_at=None)

        used = db.execute("SELECT COALESCE(SUM(artifact_bytes),0) FROM partition_scale_corpus_commits").fetchone()[0]
        count = db.execute("SELECT COUNT(*) FROM partition_scale_corpus_commits").fetchone()[0]
        staged_size = isize + csize
        if type(used) is not int or type(count) is not int:
            _fail("STORAGE_CORRUPT")
        if count >= MAX_COMMITS or used + staged_size > MAX_TOTAL_BYTES:
            _fail("STORAGE_LIMIT")
        expires = now + UPLOAD_TTL
        db.execute("INSERT INTO partition_scale_corpus_upload VALUES(1,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (request["upload_id"], corpus_id, request["policy_series_id"], generation, cref, iraw, idigest,
             craw, cdigest, case_index["segment_count"], len(index["ordered_document_segments"]), staged_size,
             actor_id, context, permission, source, now, expires))
        _source_stable(extension, store, source)
        return _result(action, request_id, upload_id=request["upload_id"], corpus_id=corpus_id,
            corpus_ref=content_ref(INDEX_KIND, corpus_id, index), expected_case_set_segments=case_index["segment_count"],
            expected_document_segments=len(index["ordered_document_segments"]), stored_bytes=staged_size,
            expires_at=expires, resumed=False, already_committed=False, committed_at=None)

    if action == "corpus_partition_read":
        policy, policy_ref, generation, permission, source = _fresh_policy(extension, store, db, request["policy_series_id"], now)
        row = db.execute("SELECT * FROM partition_scale_corpus_commits WHERE corpus_id=? AND policy_series_id=? AND policy_generation=?", (request["corpus_ref"]["id"], request["policy_series_id"], generation)).fetchone()
        if row is None:
            _fail("CORPUS_MISSING")
        committed_policy, index, case_segments, doc_segments, corpus = _validate_committed(db, row, permission, source)
        if row["policy_ref_json"] != _canonical(policy_ref)[0] or committed_policy != policy_ref:
            _fail("POLICY_STALE")
        ref = content_ref(INDEX_KIND, row["corpus_id"], index)
        if request["corpus_ref"] != ref:
            _fail("REFERENCE_MISMATCH")
        kind, ordinal = request["artifact_kind"], request["segment_index"]
        if kind == "corpus_index":
            artifact = index
        elif kind == "case_set_index":
            artifact = _load_json(row["case_set_index_json"], row["case_set_index_digest"])
        elif kind == "case_set_segment":
            if not 0 <= ordinal < len(case_segments):
                _fail("CORPUS_INVALID")
            artifact = case_segments[ordinal]
        else:
            if not 0 <= ordinal < len(doc_segments):
                _fail("CORPUS_INVALID")
            artifact = doc_segments[ordinal]
        _source_stable(extension, store, source)
        return _result(action, request_id, corpus_ref=ref, policy_series_id=request["policy_series_id"],
            policy_generation=generation, artifact_kind=kind, artifact=copy.deepcopy(artifact))

    upload = db.execute("SELECT * FROM partition_scale_corpus_upload WHERE singleton=1").fetchone()
    if upload is None or upload["upload_id"] != request["upload_id"]:
        if action == "corpus_partition_commit":
            committed = db.execute("SELECT * FROM partition_scale_corpus_commits WHERE upload_id=?", (request["upload_id"],)).fetchone()
            if committed is not None:
                if committed["owner_actor"] != actor_id or committed["owner_context"] != context:
                    _fail("PERMISSION_DENIED")
                _policy, policy_ref, generation, permission, source = _fresh_policy(extension, store, db, committed["policy_series_id"], now)
                if generation != committed["policy_generation"] or committed["policy_ref_json"] != _canonical(policy_ref)[0]:
                    _fail("POLICY_STALE")
                _committed_policy, index, _case_segments, _doc_segments, corpus = _validate_committed(db, committed, permission, source)
                _source_stable(extension, store, source)
                return _result(action, request_id,
                    corpus_ref=content_ref(INDEX_KIND, committed["corpus_id"], index),
                    case_set_ref=content_ref("case_set", corpus["case_set"]["case_set_id"], corpus["case_set"]),
                    case_count=len(corpus["case_set"]["cases"]), document_count=len(corpus["documents"]),
                    reconstructed_bytes=index["reconstructed_bytes"], reconstructed_digest=index["reconstructed_digest"],
                    artifact_bytes=committed["artifact_bytes"], committed_at=committed["committed_at"], already_committed=True)
        _fail("UPLOAD_MISSING")
    if upload["owner_actor"] != actor_id or upload["owner_context"] != context:
        _fail("PERMISSION_DENIED")
    upload = _upload(db, request["upload_id"], actor_id, context)
    policy, policy_ref, generation, permission, source = _fresh_policy(extension, store, db, upload["policy_series_id"], now)
    if generation != upload["policy_generation"] or _canonical(policy_ref)[0] != upload["policy_ref_json"]:
        _fail("POLICY_STALE")
    index, case_index = _check_upload(upload, permission, source, now)
    case_segments, doc_segments, segment_bytes = _segments(db, upload["upload_id"])
    if upload["stored_bytes"] != len(upload["corpus_index_json"].encode("utf-8")) + len(upload["case_set_index_json"].encode("utf-8")) + segment_bytes:
        _fail("STORAGE_CORRUPT")

    if action == "corpus_partition_abort":
        if db.execute("SELECT 1 FROM partition_scale_corpus_commits WHERE upload_id=?", (upload["upload_id"],)).fetchone() is not None:
            _fail("STORAGE_CORRUPT")
        db.execute("DELETE FROM partition_scale_corpus_segments WHERE upload_id=?", (upload["upload_id"],))
        db.execute("DELETE FROM partition_scale_corpus_upload WHERE singleton=1")
        _source_stable(extension, store, source)
        return _result(action, request_id, upload_id=upload["upload_id"], aborted=True)

    if action == "corpus_partition_status":
        case_rows = [r[0] for r in db.execute("SELECT segment_index FROM partition_scale_corpus_segments WHERE upload_id=? AND artifact_kind='case_set_segment' ORDER BY segment_index", (upload["upload_id"],)).fetchall()]
        doc_rows = [r[0] for r in db.execute("SELECT segment_index FROM partition_scale_corpus_segments WHERE upload_id=? AND artifact_kind='document_segment' ORDER BY segment_index", (upload["upload_id"],)).fetchall()]
        if (any(type(i) is not int for i in case_rows + doc_rows) or len(set(case_rows)) != len(case_rows)
                or len(set(doc_rows)) != len(doc_rows) or any(i < 0 or i >= upload["expected_case_set_segments"] for i in case_rows)
                or any(i < 0 or i >= upload["expected_document_segments"] for i in doc_rows)):
            _fail("STORAGE_CORRUPT")
        _source_stable(extension, store, source)
        return _result(action, request_id, upload_id=upload["upload_id"], corpus_id=upload["corpus_id"],
            present_case_set_segments=case_rows, missing_case_set_segments=[i for i in range(upload["expected_case_set_segments"]) if i not in case_rows],
            present_document_segments=doc_rows, missing_document_segments=[i for i in range(upload["expected_document_segments"]) if i not in doc_rows], expires_at=upload["expires_at"])

    if action in {"corpus_partition_put_case_set_segment", "corpus_partition_put_document_segment"}:
        is_case = action == "corpus_partition_put_case_set_segment"
        segment = request["segment"]
        kind = "case_set_segment" if is_case else "document_segment"
        ordinal = segment["segment_index"]
        expected_refs = case_index["ordered_segments"] if is_case else index["ordered_document_segments"]
        if ordinal >= len(expected_refs):
            _fail("SEGMENT_BINDING_MISMATCH")
        bound_id = segment["case_set_id"] if is_case else segment["corpus_id"]
        expected_id = case_index["case_set_id"] if is_case else index["corpus_id"]
        expected_ref = expected_refs[ordinal]
        if bound_id != expected_id or content_ref(expected_ref["kind"], segment["id"], segment) != expected_ref:
            _fail("SEGMENT_BINDING_MISMATCH")
        raw, digest, size = _canonical(segment)
        existing = db.execute("SELECT segment_json,segment_digest,byte_count FROM partition_scale_corpus_segments WHERE upload_id=? AND artifact_kind=? AND segment_index=?", (upload["upload_id"], kind, ordinal)).fetchone()
        if existing is not None:
            if existing["segment_json"] != raw or existing["segment_digest"] != digest or existing["byte_count"] != size:
                _fail("SEGMENT_CONFLICT")
            _source_stable(extension, store, source)
            return _result(action, request_id, upload_id=upload["upload_id"], artifact_kind=kind, segment_index=ordinal, duplicate=True)
        committed_bytes = db.execute("SELECT COALESCE(SUM(artifact_bytes),0) FROM partition_scale_corpus_commits").fetchone()[0]
        if type(committed_bytes) is not int or upload["stored_bytes"] + size > MAX_TOTAL_BYTES or committed_bytes + upload["stored_bytes"] + size > MAX_TOTAL_BYTES:
            _fail("STORAGE_LIMIT")
        db.execute("INSERT INTO partition_scale_corpus_segments VALUES(?,?,?,?,?,?)", (upload["upload_id"], kind, ordinal, raw, digest, size))
        db.execute("UPDATE partition_scale_corpus_upload SET stored_bytes=stored_bytes+? WHERE singleton=1", (size,))
        _source_stable(extension, store, source)
        return _result(action, request_id, upload_id=upload["upload_id"], artifact_kind=kind, segment_index=ordinal, duplicate=False)

    if action == "corpus_partition_commit":
        if len(case_segments) != upload["expected_case_set_segments"] or len(doc_segments) != upload["expected_document_segments"]:
            _fail("SEGMENTS_INCOMPLETE")
        try:
            corpus = _restore(index, case_index, case_segments, doc_segments)
        except AdoptionError as exc:
            if exc.code == "CORPUS_INVALID":
                raise
            _fail("CORPUS_INVALID")
        corpus_ref = content_ref(INDEX_KIND, index["corpus_id"], index)
        case_set_ref = content_ref("case_set", corpus["case_set"]["case_set_id"], corpus["case_set"])
        case_ref_json = _canonical(case_set_ref)[0]
        artifact_bytes = upload["stored_bytes"]
        if artifact_bytes != len(upload["corpus_index_json"].encode("utf-8")) + len(upload["case_set_index_json"].encode("utf-8")) + segment_bytes:
            _fail("STORAGE_CORRUPT")
        existing = db.execute("SELECT * FROM partition_scale_corpus_commits WHERE corpus_id=? AND policy_series_id=? AND policy_generation=?", (upload["corpus_id"], upload["policy_series_id"], upload["policy_generation"])).fetchone()
        if existing is not None:
            committed_policy, committed_index, committed_cases, committed_docs, _ = _validate_committed(db, existing, permission, source)
            same = (existing["upload_id"] == upload["upload_id"] and existing["policy_ref_json"] == upload["policy_ref_json"]
                    and existing["corpus_index_json"] == upload["corpus_index_json"]
                    and existing["case_set_index_json"] == upload["case_set_index_json"]
                    and committed_cases == case_segments and committed_docs == doc_segments
                    and existing["case_set_ref_json"] == case_ref_json and existing["artifact_bytes"] == artifact_bytes)
            if not same:
                _fail("CORPUS_CONFLICT")
            db.execute("DELETE FROM partition_scale_corpus_segments WHERE upload_id=?", (upload["upload_id"],))
            db.execute("DELETE FROM partition_scale_corpus_upload WHERE singleton=1")
            _source_stable(extension, store, source)
            return _result(action, request_id, corpus_ref=corpus_ref, case_set_ref=case_set_ref,
                case_count=len(corpus["case_set"]["cases"]), document_count=len(corpus["documents"]),
                reconstructed_bytes=index["reconstructed_bytes"], reconstructed_digest=index["reconstructed_digest"],
                artifact_bytes=artifact_bytes, committed_at=existing["committed_at"], already_committed=True)
        used = db.execute("SELECT COALESCE(SUM(artifact_bytes),0) FROM partition_scale_corpus_commits").fetchone()[0]
        count = db.execute("SELECT COUNT(*) FROM partition_scale_corpus_commits").fetchone()[0]
        if type(used) is not int or type(count) is not int or count >= MAX_COMMITS or used + artifact_bytes > MAX_TOTAL_BYTES:
            _fail("STORAGE_LIMIT")
        db.execute("INSERT INTO partition_scale_corpus_commits VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (upload["corpus_id"], upload["policy_series_id"], upload["policy_generation"], upload["upload_id"],
             upload["policy_ref_json"], upload["corpus_index_json"], upload["corpus_index_digest"],
             upload["case_set_index_json"], upload["case_set_index_digest"], case_ref_json, artifact_bytes,
             actor_id, context, permission, source, now))
        rows = db.execute("SELECT artifact_kind,segment_index,segment_json,segment_digest,byte_count FROM partition_scale_corpus_segments WHERE upload_id=? ORDER BY artifact_kind,segment_index", (upload["upload_id"],)).fetchall()
        for row in rows:
            db.execute("INSERT INTO partition_scale_corpus_committed_segments VALUES(?,?,?,?,?,?,?,?)",
                (upload["corpus_id"], upload["policy_series_id"], upload["policy_generation"], row["artifact_kind"], row["segment_index"], row["segment_json"], row["segment_digest"], row["byte_count"]))
        db.execute("DELETE FROM partition_scale_corpus_segments WHERE upload_id=?", (upload["upload_id"],))
        db.execute("DELETE FROM partition_scale_corpus_upload WHERE singleton=1")
        _source_stable(extension, store, source)
        return _result(action, request_id, corpus_ref=corpus_ref, case_set_ref=case_set_ref,
            case_count=len(corpus["case_set"]["cases"]), document_count=len(corpus["documents"]),
            reconstructed_bytes=index["reconstructed_bytes"], reconstructed_digest=index["reconstructed_digest"],
            artifact_bytes=artifact_bytes, committed_at=now, already_committed=False)

    _fail("INVALID_ACTION")


__all__ = ["ACTIONS", "FIELDS", "FRESH_ACTIONS", "TABLES", "create_schema", "handle", "validate_request"]
