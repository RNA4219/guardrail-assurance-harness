"""固定ローカルfixtureの予約・送信意図・観測を認証済み管理DBへ結ぶ。"""
from copy import deepcopy
import hashlib
from pathlib import Path

from .contracts import ContractError, decode_document, require_id, require_object, require_uint, require_ref
from .resources import ResourceBook, ResourceError
from . import resource_operation
from .run_contracts import content_ref
from .wire import canonical_bytes

TABLES = {"resource_bindings": {"operation_id", "run_id", "entry_digest", "scenario"}}
ACTIONS = {name: {"operator"} for name in (
    "resource_start", "resource_claim", "resource_reserve", "resource_dispatch", "resource_cancel", "resource_cancel_claim", "resource_close")}
ACTIONS["resource_observe"] = {"validator"}
ACTIONS.update(resource_operation.ACTIONS)
# 送信許可・leaseは過去receiptから復活させず毎回再評価する。
FRESH_ACTIONS = set(ACTIONS)
_BASE = {"schema_version", "action", "request_id", "run_id"}
_OWNER = {"owner_id", "owner_epoch"}
FIELDS = {
    "resource_operation": resource_operation.FIELDS,
    "resource_start": _BASE | {"owner_id", "operation_id", "entry", "scenario", "expected_manifest_ref"},
    "resource_claim": _BASE | {"owner_id", "recovery"},
    "resource_reserve": _BASE | _OWNER | {"operation_id", "entry", "scenario"},
    "resource_dispatch": _BASE | _OWNER | {"operation_id"},
    "resource_cancel": _BASE | _OWNER,
    "resource_cancel_claim": _BASE | {"owner_id"},
    "resource_close": _BASE | _OWNER,
    "resource_observe": _BASE | {"operation_id", "event_id", "stopped", "usage"},
}
_ENTRY = {"obligation_id", "case_id", "trial_id", "variant"}
_SCENARIOS = {f"constraint:C{number:02d}:{state}" for number in range(1, 11) for state in ("good", "bad")}
_SCENARIOS |= {f"mutation:F{number:02d}:{state}" for number in range(1, 6) for state in ("healthy", "decayed")}
_SCENARIOS |= {"guardrail:baseline-v1", "guardrail:degraded-v2"}
_BILLING = {"runner": "gah-fixed-fixture-v1", "network": "none", "model_calls": 0, "external_api": False}
BILLING_REF = content_ref("billing_basis", "fixed-local-fixture", _BILLING)
GUARDRAIL_BILLING_REF = content_ref("billing_basis", "synthetic-local-guardrail",
    {"runner": "gah-synthetic-guardrail-v1", "network": "none", "model_calls": 0, "trained_model": False, "external_api": False})


def create_schema(db):
    if not db.in_transaction:
        raise ResourceError("TRANSACTION_REQUIRED")
    db.execute("""CREATE TABLE resource_bindings(operation_id TEXT PRIMARY KEY, run_id TEXT NOT NULL,
        entry_digest TEXT NOT NULL, scenario TEXT NOT NULL, UNIQUE(run_id,entry_digest),
        FOREIGN KEY(operation_id) REFERENCES resource_operations(operation_id))""")


def validate_request(request):
    action = request.get("action") if type(request) is dict else None
    if type(action) is not str or action not in FIELDS:
        raise ResourceError("INVALID_ACTION")
    if action == "resource_operation":
        return resource_operation.validate_request(request)
    require_object(request, FIELDS[action])
    if type(request["schema_version"]) is not int or request["schema_version"] != 1:
        raise ResourceError("UNSUPPORTED_VERSION")
    for field in ("request_id", "run_id", "owner_id", "operation_id", "event_id"):
        if field in request:
            require_id(request[field])
    if "owner_epoch" in request:
        require_uint(request["owner_epoch"])
    if action == "resource_claim" and type(request["recovery"]) is not bool:
        raise ResourceError("INVALID_RECOVERY_MODE")
    if action in {"resource_reserve", "resource_start"}:
        require_object(request["entry"], _ENTRY)
        for value in request["entry"].values():
            require_id(value)
        if request["entry"]["variant"] not in {"candidate", "baseline"}:
            raise ResourceError("INVALID_ENTRY")
        if type(request["scenario"]) is not str or request["scenario"] not in _SCENARIOS:
            raise ResourceError("FIXTURE_NOT_ALLOWED")
    if action == "resource_start":
        require_ref(request["expected_manifest_ref"])
        if request["expected_manifest_ref"]["kind"] != "run_manifest":
            raise ResourceError("MANIFEST_MISMATCH")
    if action == "resource_observe":
        if type(request["stopped"]) is not bool:
            raise ResourceError("INVALID_OBSERVATION")
        if request["usage"] is not None:
            require_object(request["usage"], {"input_tokens", "output_tokens", "cost_usd"})
            for field in ("input_tokens", "output_tokens"):
                require_uint(request["usage"][field])
            # 外部model/料金の認証経路がない間は固定非課金fixtureに限定。
            if request["usage"] != {"input_tokens": 0, "output_tokens": 0, "cost_usd": "0"}:
                raise ResourceError("BILLING_BASIS_MISMATCH")
    return deepcopy(request)


def _lock():
    return decode_document((Path(__file__).resolve().parents[2] / "config/fixture-runtime.lock.json").read_bytes())


def _bound_entry(db, request, plan, now):
    matches = [item for item in plan["entries"] if all(item[key] == request["entry"][key] for key in _ENTRY)]
    if len(matches) != 1:
        raise ResourceError("ENTRY_NOT_PLANNED")
    entry = matches[0]
    if request["scenario"].startswith("guardrail:"):
        from .llm_admission import check_entry
        check_entry(db, request["run_id"], entry, request["scenario"], now)
    else:
        lock = _lock()
        expected = hashlib.sha256(canonical_bytes({"worker_digest": lock["worker_digest"], "scenario": request["scenario"]})).hexdigest()
        if (entry["target_ref"]["digest"] != expected or entry["evaluator_ref"]["digest"] != lock["worker_digest"]
                or len(entry["stage_ids"]) != 1):
            raise ResourceError("FIXTURE_BINDING_MISMATCH")
    digest = content_ref("trial_entry", "entry", entry)["digest"]
    previous = db.execute("SELECT * FROM resource_bindings WHERE run_id=? AND entry_digest=?", (request["run_id"], digest)).fetchone()
    if previous is not None and previous["operation_id"] != request["operation_id"]:
        raise ResourceError("ENTRY_ALREADY_RESERVED")
    previous = db.execute("SELECT * FROM resource_bindings WHERE operation_id=?", (request["operation_id"],)).fetchone()
    if previous is not None and (previous["run_id"] != request["run_id"] or previous["entry_digest"] != digest or previous["scenario"] != request["scenario"]):
        raise ResourceError("OPERATION_CONFLICT")
    return digest


def _reservation(scenario):
    return {"case_trial_executions":1, "model_calls":0, "input_tokens":0, "output_tokens":0,
        "api_cost_usd_micros":0, "billing_ref":GUARDRAIL_BILLING_REF if scenario.startswith("guardrail:") else BILLING_REF,
        "billing_mode":"non_billed_local"}


def execute(db, request, now, check_start, *, terminal_pending=False, read_bound=None):
    """check_startは同じDBで現在の採択を照合しmanifest/planを返す。"""
    action, run_id = request["action"], request["run_id"]
    book = ResourceBook(db)
    if action == "resource_operation":
        if not callable(read_bound):
            raise ResourceError("BINDING_UNAVAILABLE")
        return resource_operation.bound_operation(db, request, now, read_bound)
    if action == "resource_cancel_claim":
        return book.claim_for_cancel(run_id, request["owner_id"], now,
            terminal_pending=terminal_pending)
    if action == "resource_start":
        # 同じtransaction・現在前提の検査一回で、新規操作だけを予約し配送意図まで結ぶ。
        manifest, plan = check_start(run_id)
        if content_ref("run_manifest", run_id, manifest) != request["expected_manifest_ref"]:
            raise ResourceError("MANIFEST_MISMATCH")
        if db.execute("SELECT 1 FROM resource_operations WHERE operation_id=?", (request["operation_id"],)).fetchone():
            raise ResourceError("DISPATCH_NOT_PROVABLY_NEW")
        digest = _bound_entry(db, request, plan, now)
        reservation = _reservation(request["scenario"])
        from . import combined_runs
        combined_runs.check_start(db, run_id, now, reservation, request["operation_id"])
        owner = book.claim(run_id, request["owner_id"], now, recovery=False)
        book.reserve(run_id, request["operation_id"], owner["owner_id"], owner["owner_epoch"], reservation, now)
        db.execute("INSERT INTO resource_bindings VALUES(?,?,?,?)", (request["operation_id"], run_id, digest, request["scenario"]))
        book.dispatch_intent(run_id, request["operation_id"], owner["owner_id"], owner["owner_epoch"], now)
        entry = next(item for item in plan["entries"] if all(item[key] == request["entry"][key] for key in _ENTRY))
        return {"owner_id":owner["owner_id"], "owner_epoch":owner["owner_epoch"],
            "lease_until":owner["lease_until"], "manifest_ref":request["expected_manifest_ref"],
            "operation":{"operation_id":request["operation_id"], "owner_epoch":owner["owner_epoch"],
                "entry":entry, "scenario":request["scenario"], "dispatch_intended":True,
                "conflicted":False, "released":False}}
    if action == "resource_claim":
        if not request["recovery"]:
            check_start(run_id)
        return book.claim(run_id, request["owner_id"], now, recovery=request["recovery"])
    if action == "resource_reserve":
        _, plan = check_start(run_id)
        digest = _bound_entry(db, request, plan, now)
        reservation = _reservation(request["scenario"])
        from . import combined_runs
        combined_runs.check_start(db, run_id, now, reservation, request["operation_id"])
        result = book.reserve(run_id, request["operation_id"], request["owner_id"], request["owner_epoch"], reservation, now)
        db.execute("INSERT OR IGNORE INTO resource_bindings VALUES(?,?,?,?)", (request["operation_id"], run_id, digest, request["scenario"]))
        return result
    if action == "resource_dispatch":
        check_start(run_id)
        from . import combined_runs
        combined_runs.check_start(db, run_id, now)
        if db.execute("SELECT 1 FROM resource_bindings WHERE run_id=? AND operation_id=?", (run_id, request["operation_id"])).fetchone() is None:
            raise ResourceError("OPERATION_MISSING")
        return book.dispatch_intent(run_id, request["operation_id"], request["owner_id"], request["owner_epoch"], now)
    if action == "resource_observe":
        if db.execute("SELECT 1 FROM resource_bindings WHERE run_id=? AND operation_id=?", (run_id, request["operation_id"])).fetchone() is None:
            raise ResourceError("OPERATION_MISSING")
        return book.observe(run_id, request["operation_id"], request["event_id"], stopped=request["stopped"], usage=request["usage"], now=now)
    if action == "resource_cancel":
        return book.cancel(run_id, request["owner_id"], request["owner_epoch"], now,
            terminal_pending=terminal_pending)
    if action == "resource_close":
        return book.close(run_id, request["owner_id"], request["owner_epoch"], now)
    raise ResourceError("INVALID_ACTION")
