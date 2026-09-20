"""認証された履歴一覧。本文や現在CIの成功をキャッシュしない。"""
from __future__ import annotations
import base64
from copy import deepcopy
import hashlib
import hmac
import secrets
import time
from .adoption import AdoptionError
from .contracts import ContractError, decode_document, require_id, require_ref, require_uint
from .wire import canonical_bytes

ACTIONS = {"run_catalog_list": {"manager", "operator", "validator"}}
FRESH_ACTIONS = set(ACTIONS)
FIELDS = {"run_catalog_list": {"schema_version", "action", "request_id", "series_id", "scope_ref", "page_size_count", "cursor"}}
MAX_ROWS = 10_000
CURSOR_BYTES = 4096
TTL_SECONDS = 900
_SECRET = secrets.token_bytes(32)
_EPOCH = time.monotonic_ns()


def _error(code="STALE_OR_INVALIDATED"):
    return AdoptionError(code)


def request_key(actor_id, action, request_id):
    return "@run-catalog:" + hashlib.sha256(canonical_bytes([actor_id,action,request_id])).hexdigest()


def validate_request(value):
    try:
        if (type(value) is not dict or value.get("action") != "run_catalog_list"
                or set(value) != FIELDS["run_catalog_list"]
                or type(value["schema_version"]) is not int or value["schema_version"] != 1):
            raise ContractError()
        require_id(value["request_id"]); require_id(value["series_id"]); require_ref(value["scope_ref"])
        if value["scope_ref"]["kind"] != "evaluation_contract":
            raise ContractError()
        size = value["page_size_count"]
        if type(size) is not int or not 1 <= size <= 100:
            raise ContractError()
        cursor = value["cursor"]
        if cursor is not None and (type(cursor) is not str or not 1 <= len(cursor.encode("utf-8")) <= CURSOR_BYTES):
            raise ContractError()
    except (ContractError, KeyError, TypeError, ValueError, UnicodeError):
        raise _error("INVALID_REQUEST") from None
    return deepcopy(value)


def _elapsed():
    value = (time.monotonic_ns() - _EPOCH) // 1_000_000_000
    if value < 0:
        raise _error("CLOCK_ROLLBACK")
    return value


def _encode(value):
    raw = canonical_bytes(value)
    token = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    signature = hmac.new(_SECRET, token.encode("ascii"), hashlib.sha256).hexdigest()
    result = token + "." + signature
    if len(result) > CURSOR_BYTES:
        raise _error("CAPACITY_EXCEEDED")
    return result


def _decode(token):
    try:
        encoded, signature = token.split(".")
        expected = hmac.new(_SECRET, encoded.encode("ascii"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError()
        raw = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
        value = decode_document(raw)
        if canonical_bytes(value) != raw:
            raise ValueError()
        return value
    except (ValueError, TypeError, UnicodeError, ContractError):
        raise _error() from None


def _rows(db, series_id):
    # manifest/plan/usageの本文は取得しない。snapshot用のmetadata走査も上限を持つ。
    rows = db.execute("""SELECT e.run_id,e.manifest_digest,e.plan_digest,e.contract_generation,
        r.created_at,r.owner_epoch,r.cancelled,r.breached,r.closed_at
        FROM eval_runs e LEFT JOIN resource_runs r ON e.run_id=r.run_id
        WHERE e.contract_series_id=? ORDER BY r.created_at ASC,e.run_id ASC LIMIT ?""",
        (series_id,MAX_ROWS+1)).fetchall()
    if len(rows) > MAX_ROWS:
        raise _error("CAPACITY_EXCEEDED")
    result = []
    for row in rows:
        value = dict(row)
        try:
            require_id(value["run_id"]);require_uint(value["created_at"]);require_uint(value["contract_generation"])
            for field,kind in (("manifest_digest","run_manifest"),("plan_digest","trial_plan")):
                require_ref({"kind":kind,"id":value["run_id"],"digest":value[field]})
        except (ContractError,TypeError,ValueError):
            raise _error("STORAGE_CORRUPT") from None
        result.append(value)
    operations = db.execute("""SELECT o.operation_id,o.run_id,o.owner_epoch,o.reservation_digest,
        o.intended_at,o.stopped_at,o.released,o.usage_digest,o.settled_at,o.conflicted
        FROM resource_operations o JOIN eval_runs e ON e.run_id=o.run_id
        WHERE e.contract_series_id=? ORDER BY o.operation_id LIMIT ?""",(series_id,MAX_ROWS+1)).fetchall()
    if len(operations) > MAX_ROWS:
        raise _error("CAPACITY_EXCEEDED")
    state = db.execute("""SELECT s.* FROM run_state s JOIN eval_runs e ON e.run_id=s.run_id
        WHERE e.contract_series_id=? ORDER BY s.run_id LIMIT ?""",(series_id,MAX_ROWS+1)).fetchall()
    artifacts = db.execute("""SELECT a.kind,a.id,a.digest,a.run_id FROM authority_artifacts a
        JOIN eval_runs e ON e.run_id=a.run_id WHERE e.contract_series_id=?
        ORDER BY a.kind,a.id,a.digest LIMIT ?""",(series_id,MAX_ROWS+1)).fetchall()
    if len(state)>MAX_ROWS or len(artifacts)>MAX_ROWS:
        raise _error("CAPACITY_EXCEEDED")
    return result, {"operations":[dict(row) for row in operations],
                    "run_states":[dict(row) for row in state],
                    "artifacts":[dict(row) for row in artifacts]}


def execute(store, db, request, actor_id, context, now):
    request = validate_request(request)
    current = db.execute("SELECT generation,digest,payload_json FROM eval_current WHERE series_id=?",(request["series_id"],)).fetchone()
    if current is None:
        raise _error("CONTRACT_MISSING")
    from .resources import _unpack
    contract = _unpack(current["payload_json"],current["digest"])
    scope = {"kind":"evaluation_contract","id":contract.get("contract_id"),"digest":current["digest"]}
    if scope != request["scope_ref"]:
        raise _error()
    rows,operations = _rows(db,request["series_id"])
    permissions = store._permission_generation(db)
    snapshot = hashlib.sha256(canonical_bytes({"scope_ref":scope,"generation":current["generation"],
        "permission_generation":permissions,"extension_digest":store._extension_digest,
        "rows":rows,"operations":operations})).hexdigest()
    if not hasattr(store,"_run_catalog_session"):
        store._run_catalog_session = secrets.token_hex(16)
    binding = {"authority_session":store._run_catalog_session,"principal":actor_id,"context":context,"series_id":request["series_id"],"scope_ref":scope,
               "page_size_count":request["page_size_count"],"snapshot":snapshot}
    elapsed = _elapsed()
    position = 0
    expires = elapsed + TTL_SECONDS
    if request["cursor"] is not None:
        saved = _decode(request["cursor"])
        if (set(saved) != {"binding","after","expires","issued"} or saved["binding"] != binding
                or type(saved["issued"]) is not int or type(saved["expires"]) is not int
                or not saved["issued"] <= elapsed < saved["expires"]
                or saved["expires"]-saved["issued"] != TTL_SECONDS):
            raise _error()
        keys = [[row["created_at"],row["run_id"]] for row in rows]
        if saved["after"] not in keys:
            raise _error()
        position = keys.index(saved["after"]) + 1
        expires = saved["expires"]
    page = rows[position:position+request["page_size_count"]]
    following = None
    if position+len(page) < len(rows):
        last = page[-1]
        following = _encode({"binding":binding,"after":[last["created_at"],last["run_id"]],
                             "issued":expires-TTL_SECONDS,"expires":expires})
    # trial_plan idはplan本文固有ID。本文未読のため推測で完全refを作らない。
    items = [{"run_id":row["run_id"],"created_at":row["created_at"],
              "manifest_ref":{"kind":"run_manifest","id":row["run_id"],"digest":row["manifest_digest"]},
              "plan_digest":row["plan_digest"],"contract_generation":row["contract_generation"]} for row in page]
    return {"series_id":request["series_id"],"scope_ref":scope,"items":items,"next_cursor":following,
            "snapshot_ref":{"kind":"run_catalog_snapshot","id":"catalog-"+snapshot[:32],"digest":snapshot},
            "checked_at":now,"page_size_count":request["page_size_count"],"total_count":len(rows),
            "metadata_rows_materialized":len(rows)+sum(len(items) for items in operations.values()),"current_ci_checked":False}


def validate_response(request, value):
    """CLI境界でaction、scope、page、参照だけの固定応答を照合する。"""
    request=validate_request(request)
    fields={"schema_version","kind","action","request_id","ci_eligible","series_id","scope_ref","items",
            "next_cursor","snapshot_ref","checked_at","page_size_count","total_count","metadata_rows_materialized","current_ci_checked"}
    try:
        if (type(value) is not dict or set(value)!=fields
                or type(value["schema_version"]) is not int or value["schema_version"]!=1
                or value["kind"]!="evaluation_authority_result" or value["ci_eligible"] is not False
                or value["current_ci_checked"] is not False):
            raise ContractError()
        for field in ("action","request_id","series_id","scope_ref","page_size_count"):
            if value[field]!=request[field]:
                raise ContractError()
        require_uint(value["checked_at"]);require_uint(value["total_count"]);require_uint(value["metadata_rows_materialized"])
        require_ref(value["snapshot_ref"])
        if value["snapshot_ref"]["kind"]!="run_catalog_snapshot":
            raise ContractError()
        if type(value["page_size_count"]) is not int:
            raise ContractError()
        items=value["items"]
        if type(items) is not list or len(items)>request["page_size_count"] or value["total_count"]<len(items):
            raise ContractError()
        keys=[]
        for item in items:
            if type(item) is not dict or set(item)!={"run_id","created_at","manifest_ref","plan_digest","contract_generation"}:
                raise ContractError()
            require_id(item["run_id"]);require_uint(item["created_at"]);require_uint(item["contract_generation"])
            require_ref(item["manifest_ref"])
            require_ref({"kind":"trial_plan","id":item["run_id"],"digest":item["plan_digest"]})
            if item["manifest_ref"]["kind"]!="run_manifest" or item["manifest_ref"]["id"]!=item["run_id"]:
                raise ContractError()
            keys.append((item["created_at"],item["run_id"]))
        cursor=value["next_cursor"]
        if (keys!=sorted(set(keys)) or len({item["run_id"] for item in items})!=len(items)
                or (cursor is not None and
            (type(cursor) is not str or not cursor or len(cursor.encode("utf-8"))>CURSOR_BYTES or not items))):
            raise ContractError()
    except (ContractError, KeyError, TypeError, ValueError, UnicodeError):
        raise _error("CATALOG_RESPONSE_INVALID") from None
    return value
