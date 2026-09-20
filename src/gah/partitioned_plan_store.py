"""Transactional persistence for bounded partitioned TrialPlan artifacts."""
from __future__ import annotations
import copy
import hashlib
import json
import sqlite3
from typing import Any
from .adoption import AdoptionError
from .contracts import ContractError, MAX_INTEGER, require_digest, require_id, require_object, require_uint
from .partitioned_trial_plan import MAX_ARTIFACT_BYTES, MAX_SEGMENTS, restore_trial_plan
from .run_contracts import content_ref, validate_evaluation_contract

UPLOAD_TTL = 3600
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_COMMITS = 256
INDEX_KIND = "trial_plan_index"
SEGMENT_KIND = "trial_plan_entries_segment"
_INDEX_FIELDS = {"schema_version", "kind", "plan_id", "contract_ref", "entry_count", "segment_count", "ordered_segments", "reconstructed_bytes", "reconstructed_digest"}
_SEGMENT_FIELDS = {"schema_version", "kind", "id", "plan_id", "contract_ref", "segment_index", "first_entry_ordinal", "entry_count", "entries"}
_BASE = {"schema_version", "action", "request_id"}
_UPLOAD = _BASE | {"upload_id"}
_ACTION_FIELDS = {
    "plan_partition_begin": _BASE | {"upload_id", "contract_series_id", "expected_contract_generation", "index"},
    "plan_partition_put_segment": _UPLOAD | {"segment"},
    "plan_partition_status": _UPLOAD,
    "plan_partition_commit": _UPLOAD,
    "plan_partition_abort": _UPLOAD,
    "plan_partition_read": _BASE | {"plan_ref", "segment_index"},
}
FIELDS = set(_ACTION_FIELDS)
ACTIONS = {
    "plan_partition_begin": {"manager"}, "plan_partition_put_segment": {"manager"},
    "plan_partition_status": {"manager"}, "plan_partition_commit": {"manager"},
    "plan_partition_abort": {"manager"}, "plan_partition_read": {"manager", "validator", "operator"},
}
FRESH_ACTIONS = {"plan_partition_status", "plan_partition_read"}
TABLES = {
    "partition_plan_upload": {"singleton", "upload_id", "plan_id", "contract_series_id", "contract_generation", "contract_ref_json", "index_json", "index_digest", "expected_segments", "stored_bytes", "owner_actor", "owner_context", "permission_generation", "extension_digest", "created_at", "expires_at"},
    "partition_plan_segments": {"plan_id", "segment_index", "segment_json", "segment_digest", "byte_count"},
    "partition_plan_commits": {"plan_id", "contract_series_id", "contract_generation", "contract_ref_json", "index_json", "index_digest", "artifact_bytes", "owner_actor", "owner_context", "permission_generation", "extension_digest", "committed_at"},
}

def _fail(code: str) -> None:
    raise AdoptionError(code)

def _canonical(value: Any, maximum: int = MAX_ARTIFACT_BYTES) -> tuple[str, str, int]:
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        _fail("INVALID_REQUEST")
    if len(raw) > maximum:
        _fail("DOCUMENT_SIZE")
    return raw.decode("utf-8"), hashlib.sha256(raw).hexdigest(), len(raw)

def _validate_ref(value: Any, kind: str) -> None:
    require_object(value, {"kind", "id", "digest"})
    require_id(value["kind"])
    require_id(value["id"])
    require_digest(value["digest"])
    if value["kind"] != kind:
        _fail("REFERENCE_KIND")

def _closed_json(value: Any, maximum: int = MAX_ARTIFACT_BYTES) -> dict[str, Any]:
    if type(value) is not dict:
        _fail("INVALID_REQUEST")
    _canonical(value, maximum)
    return copy.deepcopy(value)

def validate_request(value: Any) -> dict[str, Any]:
    if type(value) is not dict or type(value.get("action")) is not str:
        _fail("INVALID_REQUEST")
    action = value["action"]
    fields = _ACTION_FIELDS.get(action)
    if fields is None:
        _fail("INVALID_ACTION")
    if set(value) != fields:
        _fail("INVALID_REQUEST")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        _fail("UNSUPPORTED_VERSION")
    try:
        require_id(value["request_id"])
        if action == "plan_partition_read":
            _validate_ref(value["plan_ref"], INDEX_KIND)
            if value["segment_index"] is not None:
                require_uint(value["segment_index"], maximum=MAX_SEGMENTS - 1)
        elif action == "plan_partition_begin":
            require_id(value["upload_id"])
            require_id(value["contract_series_id"])
            require_uint(value["expected_contract_generation"])
            index = _closed_json(value["index"])
            if set(index) != _INDEX_FIELDS:
                _fail("PARTITIONED_PLAN_INVALID")
            if type(index["schema_version"]) is not int or index["schema_version"] != 2 or index["kind"] != INDEX_KIND:
                _fail("PARTITIONED_PLAN_INVALID")
            require_id(index["plan_id"])
            if type(index["segment_count"]) is not int or not 1 <= index["segment_count"] <= MAX_SEGMENTS:
                _fail("PARTITIONED_PLAN_INVALID")
            if type(index["ordered_segments"]) is not list or len(index["ordered_segments"]) != index["segment_count"]:
                _fail("PARTITIONED_PLAN_INVALID")
            require_uint(index["entry_count"], maximum=10_000)
            require_uint(index["reconstructed_bytes"], maximum=8 * 1024 * 1024)
            require_digest(index["reconstructed_digest"])
            _validate_ref(index["contract_ref"], "evaluation_contract")
            for ref in index["ordered_segments"]:
                _validate_ref(ref, SEGMENT_KIND)
        else:
            require_id(value["upload_id"])
            if action == "plan_partition_put_segment":
                segment = _closed_json(value["segment"])
                require_object(segment, _SEGMENT_FIELDS)
                if type(segment["schema_version"]) is not int or segment["schema_version"] != 2 or segment["kind"] != SEGMENT_KIND:
                    _fail("PARTITIONED_PLAN_INVALID")
                require_id(segment["id"])
                require_id(segment["plan_id"])
                _validate_ref(segment["contract_ref"], "evaluation_contract")
                require_uint(segment["segment_index"], maximum=MAX_SEGMENTS - 1)
                require_uint(segment["first_entry_ordinal"], maximum=10_000)
                require_uint(segment["entry_count"], maximum=10_000)
                if type(segment["entries"]) is not list or not segment["entries"] or len(segment["entries"]) != segment["entry_count"]:
                    _fail("PARTITIONED_PLAN_INVALID")
        return copy.deepcopy(value)
    except ContractError:
        _fail("INVALID_REQUEST")

def create_schema(db: sqlite3.Connection) -> None:
    if not db.in_transaction:
        _fail("TRANSACTION_REQUIRED")
    db.execute("CREATE TABLE partition_plan_upload(singleton INTEGER PRIMARY KEY CHECK(singleton=1),upload_id TEXT NOT NULL UNIQUE,plan_id TEXT NOT NULL UNIQUE,contract_series_id TEXT NOT NULL,contract_generation INTEGER NOT NULL,contract_ref_json TEXT NOT NULL,index_json TEXT NOT NULL,index_digest TEXT NOT NULL,expected_segments INTEGER NOT NULL,stored_bytes INTEGER NOT NULL,owner_actor TEXT NOT NULL,owner_context TEXT NOT NULL,permission_generation INTEGER NOT NULL,extension_digest TEXT NOT NULL,created_at INTEGER NOT NULL,expires_at INTEGER NOT NULL)")
    db.execute("CREATE TABLE partition_plan_segments(plan_id TEXT NOT NULL,segment_index INTEGER NOT NULL,segment_json TEXT NOT NULL,segment_digest TEXT NOT NULL,byte_count INTEGER NOT NULL,PRIMARY KEY(plan_id,segment_index))")
    db.execute("CREATE TABLE partition_plan_commits(plan_id TEXT PRIMARY KEY,contract_series_id TEXT NOT NULL,contract_generation INTEGER NOT NULL,contract_ref_json TEXT NOT NULL,index_json TEXT NOT NULL,index_digest TEXT NOT NULL,artifact_bytes INTEGER NOT NULL,owner_actor TEXT NOT NULL,owner_context TEXT NOT NULL,permission_generation INTEGER NOT NULL,extension_digest TEXT NOT NULL,committed_at INTEGER NOT NULL)")

def _exact_extension_digest(extension: Any) -> str:
    from . import partitioned_authority as v5
    from . import partitioned_run_authority as v6
    if type(extension) is v5.PartitionedEvaluationExtension:
        digest = v5.source_digest()
    else:
        digest = v6._exact_source_digest(extension)
    return digest


def _authority_context(extension: Any, store: Any, db: sqlite3.Connection, series_id: str, generation: int, now: int) -> tuple[dict[str, Any], int, str]:
    from . import evaluation_authority as authority
    current = db.execute("SELECT * FROM eval_current WHERE series_id=?", (series_id,)).fetchone()
    if current is None or type(current["generation"]) is not int or current["generation"] != generation:
        _fail("CONTRACT_INVALID")
    history = db.execute("SELECT * FROM eval_adoptions WHERE series_id=? AND generation=?", (series_id, generation)).fetchone()
    if history is None:
        _fail("CONTRACT_INVALID")
    try:
        contract = validate_evaluation_contract(authority._load_json(current, "payload_json", "digest"))
        historical = validate_evaluation_contract(authority._load_json(history, "payload_json", "digest"))
    except (ContractError, AdoptionError, TypeError, ValueError, KeyError):
        _fail("CONTRACT_INVALID")
    if (contract["generation"] != generation or historical != contract or current["series_id"] != series_id
            or history["series_id"] != series_id or history["generation"] != generation):
        _fail("CONTRACT_INVALID")
    authority._validate_state(store, db, contract, now)
    authority._assert_current_valid(store, db, history, contract, now)
    permission_generation = store._permission_generation(db)
    digest = _exact_extension_digest(extension)
    if digest != getattr(store, "_extension_digest", None) or getattr(extension, "digest", None) != digest:
        _fail("EXTENSION_INVALID")
    return contract, permission_generation, digest

def _assert_uncommitted(db: sqlite3.Connection, plan_id: str) -> None:
    if db.execute("SELECT 1 FROM partition_plan_commits WHERE plan_id=?", (plan_id,)).fetchone() is not None:
        _fail("STORAGE_CORRUPT")

def _current_upload(db: sqlite3.Connection, upload_id: str, actor_id: str, context: str, permission_generation: int, extension_digest: str, now: int):
    row = db.execute("SELECT * FROM partition_plan_upload WHERE singleton=1").fetchone()
    if row is None or row["upload_id"] != upload_id:
        _fail("UPLOAD_MISSING")
    if row["owner_actor"] != actor_id or row["owner_context"] != context:
        _fail("PERMISSION_DENIED")
    _assert_uncommitted(db, row["plan_id"])
    if row["expires_at"] <= now or row["permission_generation"] != permission_generation or row["extension_digest"] != extension_digest:
        _fail("UPLOAD_STALE")
    return row

def _load_index(row: sqlite3.Row) -> dict[str, Any]:
    try:
        value = json.loads(row["index_json"])
        raw, digest, size = _canonical(value)
    except (TypeError, ValueError, KeyError, RecursionError, AdoptionError):
        _fail("STORAGE_CORRUPT")
    if raw != row["index_json"] or digest != row["index_digest"] or size > MAX_ARTIFACT_BYTES:
        _fail("STORAGE_CORRUPT")
    try:
        validate_request({"schema_version": 1, "action": "plan_partition_begin", "request_id": "stored-check", "upload_id": "stored-check", "contract_series_id": row["contract_series_id"], "expected_contract_generation": row["contract_generation"], "index": value})
    except (AdoptionError, ContractError):
        _fail("STORAGE_CORRUPT")
    return value

def _load_segment(row: sqlite3.Row) -> dict[str, Any]:
    try:
        value = json.loads(row["segment_json"])
        raw, digest, size = _canonical(value)
    except (TypeError, ValueError, KeyError, RecursionError, AdoptionError):
        _fail("STORAGE_CORRUPT")
    if raw != row["segment_json"] or digest != row["segment_digest"] or size != row["byte_count"]:
        _fail("STORAGE_CORRUPT")
    try:
        validate_request({"schema_version": 1, "action": "plan_partition_put_segment", "request_id": "stored-check", "upload_id": "stored-check", "segment": value})
    except (AdoptionError, ContractError):
        _fail("STORAGE_CORRUPT")
    return value

def _validate_committed_with_plan(db: sqlite3.Connection, row: sqlite3.Row, contract: dict[str, Any], permission_generation: int, extension_digest: str) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    if row["permission_generation"] != permission_generation or row["extension_digest"] != extension_digest or row["contract_generation"] != contract["generation"]:
        _fail("PLAN_STALE")
    try:
        stored_ref = json.loads(row["contract_ref_json"])
    except (TypeError, ValueError, RecursionError):
        _fail("STORAGE_CORRUPT")
    expected_contract_ref = content_ref("evaluation_contract", contract["contract_id"], contract)
    if stored_ref != expected_contract_ref or json.dumps(stored_ref, sort_keys=True, separators=(",", ":")) != row["contract_ref_json"]:
        _fail("STORAGE_CORRUPT")
    index = _load_index(row)
    if (index["plan_id"] != row["plan_id"] or index["contract_ref"] != expected_contract_ref
            or content_ref(INDEX_KIND, row["plan_id"], index) != {"kind": INDEX_KIND, "id": row["plan_id"], "digest": row["index_digest"]}):
        _fail("STORAGE_CORRUPT")
    rows = db.execute("SELECT * FROM partition_plan_segments WHERE plan_id=? ORDER BY segment_index", (row["plan_id"],)).fetchall()
    if len(rows) != index["segment_count"] or any(item["segment_index"] != i for i, item in enumerate(rows)):
        _fail("STORAGE_CORRUPT")
    segments = [_load_segment(item) for item in rows]
    try:
        plan = restore_trial_plan(index, segments)
    except (ContractError, AdoptionError):
        _fail("STORAGE_CORRUPT")
    total = len(row["index_json"].encode("utf-8")) + sum(item["byte_count"] for item in rows)
    if plan["plan_id"] != row["plan_id"] or row["artifact_bytes"] != total:
        _fail("STORAGE_CORRUPT")
    return index, segments, plan

def _validate_committed(db: sqlite3.Connection, row: sqlite3.Row, contract: dict[str, Any], permission_generation: int, extension_digest: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    index, segments, _plan = _validate_committed_with_plan(db, row, contract, permission_generation, extension_digest)
    return index, segments

def load_verified_committed_plan(extension: Any, store: Any, db: sqlite3.Connection, plan_ref: Any, *, contract_series_id: Any, expected_generation: Any, now: Any) -> dict[str, Any]:
    """内部consumer向けにcommitted planを再検証して復元する。

    呼出元が既存authority actionでのrole/action認可を済ませていることが前提。
    このhelper単体は認可を実施せず、DB変更・commit・cacheも行わない。
    """
    if not isinstance(db, sqlite3.Connection) or not db.in_transaction:
        _fail("TRANSACTION_REQUIRED")
    if db is not getattr(store, "_db", None):
        _fail("STORE_MISMATCH")
    if extension is not getattr(store, "_extension", None):
        _fail("EXTENSION_INVALID")
    if type(contract_series_id) is not str or type(expected_generation) is not int or type(now) is not int:
        _fail("INVALID_REQUEST")
    try:
        require_id(contract_series_id)
        require_uint(expected_generation)
        if not 0 <= now <= MAX_INTEGER - UPLOAD_TTL:
            _fail("CLOCK_INVALID")
        _validate_ref(plan_ref, INDEX_KIND)
    except ContractError:
        _fail("INVALID_REQUEST")
    if type(plan_ref) is not dict:
        _fail("INVALID_REQUEST")

    def pinned_source_digest() -> str:
        try:
            value = _exact_extension_digest(extension)
        except AdoptionError:
            raise
        except Exception:
            _fail("EXTENSION_INVALID")
        if type(value) is not str or value != getattr(store, "_extension_digest", None):
            _fail("EXTENSION_INVALID")
        return value

    before_source = pinned_source_digest()
    row = db.execute("SELECT * FROM partition_plan_commits WHERE plan_id=?", (plan_ref["id"],)).fetchone()
    if row is None:
        _fail("PLAN_MISSING")
    if row["contract_series_id"] != contract_series_id or row["contract_generation"] != expected_generation:
        _fail("PLAN_STALE")
    contract, permission_generation, extension_digest = _authority_context(
        extension, store, db, contract_series_id, expected_generation, now)
    index, segments, plan = _validate_committed_with_plan(
        db, row, contract, permission_generation, extension_digest)
    expected_ref = content_ref(INDEX_KIND, row["plan_id"], index)
    if plan_ref != expected_ref:
        _fail("REFERENCE_MISMATCH")
    after_source = pinned_source_digest()
    if before_source != after_source:
        _fail("EXTENSION_INVALID")
    contract_ref = content_ref("evaluation_contract", contract["contract_id"], contract)
    return {
        "index": copy.deepcopy(index),
        "segments": copy.deepcopy(segments),
        "plan": copy.deepcopy(plan),
        "contract_series_id": contract_series_id,
        "contract_generation": expected_generation,
        "contract_ref": copy.deepcopy(contract_ref),
        "permission_generation": permission_generation,
        "extension_digest": extension_digest,
    }

def _result(action: str, request_id: str, **fields: Any) -> dict[str, Any]:
    return {"schema_version": 1, "kind": "partitioned_plan_store_result", "action": action, "request_id": request_id, "ci_eligible": False, **fields}

def handle(extension: Any, store: Any, db: sqlite3.Connection, request: Any, actor_id: str, context: str, now: int) -> dict[str, Any]:
    if not db.in_transaction:
        _fail("TRANSACTION_REQUIRED")
    request = validate_request(request)
    action, request_id = request["action"], request["request_id"]
    if type(now) is not int or not 0 <= now <= MAX_INTEGER - UPLOAD_TTL:
        _fail("CLOCK_INVALID")
    if type(actor_id) is not str or type(context) is not str:
        _fail("PERMISSION_DENIED")
    if action == "plan_partition_begin":
        contract, permission, source = _authority_context(extension, store, db, request["contract_series_id"], request["expected_contract_generation"], now)
        index = request["index"]
        cref = content_ref("evaluation_contract", contract["contract_id"], contract)
        if index["contract_ref"] != cref:
            _fail("REFERENCE_MISMATCH")
        raw, digest, size = _canonical(index)
        committed = db.execute("SELECT * FROM partition_plan_commits WHERE plan_id=?", (index["plan_id"],)).fetchone()
        if committed is not None:
            stored_index, _ = _validate_committed(db, committed, contract, permission, source)
            if stored_index != index:
                _fail("PLAN_CONFLICT")
            return _result(action, request_id, upload_id=request["upload_id"], plan_ref=content_ref(INDEX_KIND, index["plan_id"], index), already_committed=True)
        current = db.execute("SELECT * FROM partition_plan_upload WHERE singleton=1").fetchone()
        if current is not None and current["expires_at"] <= now:
            _assert_uncommitted(db, current["plan_id"])
            db.execute("DELETE FROM partition_plan_segments WHERE plan_id=?", (current["plan_id"],))
            db.execute("DELETE FROM partition_plan_upload WHERE singleton=1")
            current = None
        if current is not None:
            if current["upload_id"] != request["upload_id"] or current["plan_id"] != index["plan_id"] or current["index_digest"] != digest:
                _fail("UPLOAD_BUSY")
            resumed = _current_upload(db, request["upload_id"], actor_id, context, permission, source, now)
            if (resumed["contract_series_id"] != request["contract_series_id"] or resumed["contract_generation"] != request["expected_contract_generation"]
                    or resumed["expected_segments"] != index["segment_count"] or json.loads(resumed["contract_ref_json"]) != cref or _load_index(resumed) != index):
                _fail("UPLOAD_STALE")
            staged = db.execute("SELECT COALESCE(SUM(byte_count),0) FROM partition_plan_segments WHERE plan_id=?", (resumed["plan_id"],)).fetchone()[0]
            if type(staged) is not int or resumed["stored_bytes"] != staged + len(resumed["index_json"].encode("utf-8")):
                _fail("STORAGE_CORRUPT")
            return _result(action, request_id, upload_id=request["upload_id"], plan_id=index["plan_id"], segment_count=index["segment_count"], expires_at=resumed["expires_at"], resumed=True)
        if db.execute("SELECT COUNT(*) FROM partition_plan_commits").fetchone()[0] >= MAX_COMMITS:
            _fail("PLAN_LIMIT")
        used = db.execute("SELECT COALESCE(SUM(artifact_bytes),0) FROM partition_plan_commits").fetchone()[0]
        if type(used) is not int or used + size > MAX_TOTAL_BYTES:
            _fail("STORAGE_LIMIT")
        expires = now + UPLOAD_TTL
        db.execute("INSERT INTO partition_plan_upload VALUES(1,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (request["upload_id"], index["plan_id"], request["contract_series_id"], request["expected_contract_generation"], json.dumps(index["contract_ref"], sort_keys=True, separators=(",", ":")), raw, digest, index["segment_count"], size, actor_id, context, permission, source, now, expires))
        return _result(action, request_id, upload_id=request["upload_id"], plan_id=index["plan_id"], segment_count=index["segment_count"], expires_at=expires, resumed=False)
    if action == "plan_partition_read":
        row = db.execute("SELECT * FROM partition_plan_commits WHERE plan_id=?", (request["plan_ref"]["id"],)).fetchone()
        if row is None:
            _fail("PLAN_MISSING")
        contract, permission, source = _authority_context(extension, store, db, row["contract_series_id"], row["contract_generation"], now)
        index, segments = _validate_committed(db, row, contract, permission, source)
        ref = content_ref(INDEX_KIND, row["plan_id"], index)
        if request["plan_ref"] != ref:
            _fail("REFERENCE_MISMATCH")
        ordinal = request["segment_index"]
        if ordinal is None:
            return _result(action, request_id, plan_ref=ref, index=index)
        if not 0 <= ordinal < len(segments):
            _fail("STORAGE_CORRUPT")
        return _result(action, request_id, plan_ref=ref, segment_index=ordinal, segment=segments[ordinal])
    upload = db.execute("SELECT * FROM partition_plan_upload WHERE singleton=1").fetchone()
    if upload is None or upload["upload_id"] != request["upload_id"]:
        _fail("UPLOAD_MISSING")
    if upload["owner_actor"] != actor_id or upload["owner_context"] != context:
        _fail("PERMISSION_DENIED")
    if action == "plan_partition_abort":
        _assert_uncommitted(db, upload["plan_id"])
        db.execute("DELETE FROM partition_plan_segments WHERE plan_id=?", (upload["plan_id"],))
        db.execute("DELETE FROM partition_plan_upload WHERE singleton=1")
        return _result(action, request_id, upload_id=request["upload_id"], aborted=True)
    contract, permission, source = _authority_context(extension, store, db, upload["contract_series_id"], upload["contract_generation"], now)
    upload = _current_upload(db, request["upload_id"], actor_id, context, permission, source, now)
    index = _load_index(upload)
    if action == "plan_partition_status":
        rows = db.execute("SELECT segment_index FROM partition_plan_segments WHERE plan_id=? ORDER BY segment_index", (upload["plan_id"],)).fetchall()
        present = [r[0] for r in rows]
        if any(type(i) is not int or not 0 <= i < index["segment_count"] for i in present):
            _fail("STORAGE_CORRUPT")
        return _result(action, request_id, upload_id=request["upload_id"], plan_id=upload["plan_id"], present_segments=present, missing_segments=[i for i in range(index["segment_count"]) if i not in present], expires_at=upload["expires_at"])
    if action == "plan_partition_put_segment":
        segment = request["segment"]
        ordinal = segment["segment_index"]
        if segment["plan_id"] != upload["plan_id"] or segment["contract_ref"] != index["contract_ref"] or not 0 <= ordinal < index["segment_count"]:
            _fail("SEGMENT_BINDING_MISMATCH")
        raw, digest, size = _canonical(segment)
        if content_ref(SEGMENT_KIND, segment["id"], segment) != index["ordered_segments"][ordinal]:
            _fail("REFERENCE_MISMATCH")
        _assert_uncommitted(db, upload["plan_id"])
        existing = db.execute("SELECT segment_digest FROM partition_plan_segments WHERE plan_id=? AND segment_index=?", (upload["plan_id"], ordinal)).fetchone()
        if existing is not None:
            if existing[0] != digest:
                _fail("SEGMENT_CONFLICT")
            return _result(action, request_id, upload_id=request["upload_id"], segment_index=ordinal, duplicate=True)
        staged = db.execute("SELECT COALESCE(SUM(byte_count),0) FROM partition_plan_segments WHERE plan_id=?", (upload["plan_id"],)).fetchone()[0]
        if type(staged) is not int or upload["stored_bytes"] != staged + len(upload["index_json"].encode("utf-8")):
            _fail("STORAGE_CORRUPT")
        committed_bytes = db.execute("SELECT COALESCE(SUM(artifact_bytes),0) FROM partition_plan_commits").fetchone()[0]
        if upload["stored_bytes"] + size > MAX_ARTIFACT_BYTES * (MAX_SEGMENTS + 1) or committed_bytes + upload["stored_bytes"] + size > MAX_TOTAL_BYTES:
            _fail("STORAGE_LIMIT")
        db.execute("INSERT INTO partition_plan_segments VALUES(?,?,?,?,?)", (upload["plan_id"], ordinal, raw, digest, size))
        db.execute("UPDATE partition_plan_upload SET stored_bytes=stored_bytes+? WHERE singleton=1", (size,))
        return _result(action, request_id, upload_id=request["upload_id"], segment_index=ordinal, duplicate=False)
    if action == "plan_partition_commit":
        rows = db.execute("SELECT * FROM partition_plan_segments WHERE plan_id=? ORDER BY segment_index", (upload["plan_id"],)).fetchall()
        if len(rows) != index["segment_count"] or any(item["segment_index"] != i for i, item in enumerate(rows)):
            _fail("SEGMENTS_INCOMPLETE")
        segments = [_load_segment(item) for item in rows]
        try:
            plan = restore_trial_plan(index, segments)
        except ContractError as error:
            _fail(getattr(error, "code", "PARTITIONED_PLAN_INVALID"))
        if plan["plan_id"] != upload["plan_id"] or content_ref(INDEX_KIND, upload["plan_id"], index) != {"kind": INDEX_KIND, "id": upload["plan_id"], "digest": upload["index_digest"]}:
            _fail("STORAGE_CORRUPT")
        artifact_bytes = len(upload["index_json"].encode("utf-8")) + sum(item["byte_count"] for item in rows)
        if upload["stored_bytes"] != artifact_bytes:
            _fail("STORAGE_CORRUPT")
        used = db.execute("SELECT COALESCE(SUM(artifact_bytes),0) FROM partition_plan_commits").fetchone()[0]
        count = db.execute("SELECT COUNT(*) FROM partition_plan_commits").fetchone()[0]
        if count >= MAX_COMMITS or used + artifact_bytes > MAX_TOTAL_BYTES:
            _fail("STORAGE_LIMIT")
        db.execute("INSERT INTO partition_plan_commits VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (upload["plan_id"], upload["contract_series_id"], upload["contract_generation"], upload["contract_ref_json"], upload["index_json"], upload["index_digest"], artifact_bytes, actor_id, context, permission, source, now))
        db.execute("DELETE FROM partition_plan_upload WHERE singleton=1")
        ref = content_ref(INDEX_KIND, upload["plan_id"], index)
        return _result(action, request_id, upload_id=request["upload_id"], plan_ref=ref, segment_count=len(segments), entry_count=len(plan["entries"]), committed_at=now)
    _fail("INVALID_ACTION")

__all__ = ["ACTIONS", "FIELDS", "FRESH_ACTIONS", "TABLES", "create_schema", "handle", "load_verified_committed_plan", "validate_request"]