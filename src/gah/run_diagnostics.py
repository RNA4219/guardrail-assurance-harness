"""診断bundle用の固定metadata read。raw本文やCI採択を返さない。"""
from copy import deepcopy
import hashlib
from .adoption import AdoptionError
from .contracts import ContractError, require_id, require_ref, require_uint
from . import resources, evidence_retention
from .policy import validate_policy_profile
from .wire import canonical_bytes

ACTIONS = {"run_diagnostics": {"operator", "validator"}}
FRESH_ACTIONS = set(ACTIONS)
FIELDS = {"run_diagnostics": {"schema_version", "action", "request_id", "run_id"}}
MAX_REFS = 256


def request_key(actor_id, action, request_id):
    return "@run-diagnostics:" + hashlib.sha256(canonical_bytes([actor_id, action, request_id])).hexdigest()


def validate_request(value):
    try:
        if (type(value) is not dict or set(value) != FIELDS["run_diagnostics"]
                or value["action"] != "run_diagnostics" or type(value["schema_version"]) is not int
                or value["schema_version"] != 1):
            raise ContractError()
        require_id(value["request_id"]); require_id(value["run_id"])
    except (ContractError, KeyError, TypeError, ValueError):
        raise AdoptionError("INVALID_REQUEST") from None
    return deepcopy(value)


def execute(store, db, request, now):
    run_id = request["run_id"]
    row = db.execute("SELECT policy_json,policy_digest,profile FROM resource_runs WHERE run_id=?", (run_id,)).fetchone()
    if row is None:
        raise AdoptionError("RUN_MISSING")
    try:
        policy = validate_policy_profile(resources._unpack(row["policy_json"], row["policy_digest"]))
        if row["profile"] not in {"pr", "full"}:
            raise ContractError()
        # 複数runの全履歴を展開せず、対象runのref列だけを上限付きで読む。
        rows = db.execute("SELECT kind,id,digest FROM authority_artifacts WHERE run_id=? ORDER BY kind,id,digest LIMIT ?",
                          (run_id, MAX_REFS + 1)).fetchall()
        if len(rows) > MAX_REFS:
            raise AdoptionError("DIAGNOSTIC_LIMIT")
        refs = [{name: item[name] for name in ("kind", "id", "digest")} for item in rows]
        for ref in refs:
            require_ref(ref)
        state = db.execute("SELECT state,evidence_state,evidence_generation FROM run_state WHERE run_id=?", (run_id,)).fetchone()
        state_value = None
        if state is not None:
            if state["state"] not in {"OPEN", "HOLD", "FINALIZED"} or state["evidence_state"] not in {"UNKNOWN", "VALID", "REVOKED", "DELETED", "EXPIRED"}:
                raise ContractError()
            if state["evidence_generation"] is not None:
                require_uint(state["evidence_generation"])
            state_value = {name: state[name] for name in ("state", "evidence_state", "evidence_generation")}
        receipt_row = db.execute("SELECT payload_json,digest FROM authority_run_receipts WHERE run_id=?", (run_id,)).fetchone()
        retention = None
        if receipt_row is not None:
            receipt = resources._unpack(receipt_row["payload_json"], receipt_row["digest"])
            ref = receipt["evidence_ref"]
            require_ref(ref)
            if receipt.get("run_id") != run_id:
                raise ContractError()
            hold = evidence_retention.hold_state(db, run_id, ref, now)
            deleted = evidence_retention.tombstone(db, run_id, ref)
            if type(hold["active"]) is not bool:
                raise ContractError()
            retention = {"evidence_ref": ref, "hold_active": hold["active"], "hold_sequence": hold["sequence"],
                         "deleted": deleted is not None, "tombstone_ref": None if deleted is None else deleted[1]}
        version = store._meta(db, "schema_version")
        if db.execute("PRAGMA user_version").fetchone()[0] != version:
            raise ContractError()
        return {"run_id": run_id, "artifact_refs": refs, "run_state": state_value,
                "resource_limits": deepcopy(policy["profiles"][row["profile"]]),
                "retention": retention, "database_schema_version": version,
                "permission_generation": store._permission_generation(db),
                "extension_digest": store._extension_digest, "checked_at": now,
                "current_ci_checked": False, "ci_eligible": False}
    except AdoptionError:
        raise
    except (ContractError, resources.ResourceError, KeyError, TypeError, ValueError):
        raise AdoptionError("STORAGE_CORRUPT") from None
