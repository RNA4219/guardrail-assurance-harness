"""Evidence本文の削除計画、保留、tombstoneを同じ認証DBへ結ぶ。"""
from copy import deepcopy
import hashlib
from .adoption import AdoptionError
from .contracts import ContractError, MAX_INTEGER, require_id, require_object, require_ref, require_uint
from . import resources
from .run_contracts import content_ref
from .wire import canonical_bytes

ACTIONS = {"run_retention_state":{"operator","validator"}, "evidence_retention_state":{"manager","operator","validator"},
    "evidence_retention_hold":{"manager"}, "evidence_retention_plan":{"manager"},
    "evidence_retention_apply":{"operator"}}
FRESH_ACTIONS = {"evidence_retention_state","run_retention_state"}
BASE = {"schema_version","action","request_id","run_id","expected_evidence_ref"}
FIELDS = {"run_retention_state":{"schema_version","action","request_id","run_id","expected_manifest_ref"},
    "evidence_retention_state":BASE,
    "evidence_retention_hold":BASE | {"expected_hold_ref","active"},
    "evidence_retention_plan":BASE | {"expected_hold_ref","reason"},
    "evidence_retention_apply":BASE | {"plan_ref"}}
HOLD_KIND = "evidence_retention_hold_event"
PLAN_KIND = "evidence_deletion_plan"
TOMBSTONE_KIND = "evidence_tombstone"


def validate_request(value):
    try:
        action = value.get("action") if type(value) is dict else None
        if type(action) is not str or action not in FIELDS:
            raise ContractError()
        require_object(value,FIELDS[action])
        if type(value["schema_version"]) is not int or value["schema_version"] != 1:
            raise ContractError()
        for key in ("request_id","run_id"):
            require_id(value[key])
        for key,kind in (("expected_manifest_ref","run_manifest"),("expected_evidence_ref","evidence"),("expected_hold_ref","retention_hold"),("plan_ref",PLAN_KIND)):
            if key in value:
                require_ref(value[key])
                if value[key]["kind"] != kind:
                    raise ContractError()
        if any(value[key]["id"] != value["run_id"] for key in ("expected_evidence_ref","expected_manifest_ref") if key in value):
            raise ContractError()
        if action == "evidence_retention_hold" and type(value["active"]) is not bool:
            raise ContractError()
        if action == "evidence_retention_plan" and (type(value["reason"]) is not str
                or value["reason"] not in {"RETENTION_EXPIRED","EXPLICIT_REMOVAL"}):
            raise ContractError()
    except (ContractError,KeyError,TypeError,ValueError):
        raise AdoptionError("INVALID_REQUEST") from None
    return deepcopy(value)


def _id(prefix,value):
    return prefix + hashlib.sha256(canonical_bytes(value)).hexdigest()[:40]


def _read(db,ref,run_id):
    row = db.execute("SELECT * FROM authority_artifacts WHERE kind=? AND id=? AND digest=?",
        tuple(ref[key] for key in ("kind","id","digest"))).fetchone()
    if row is None or row["run_id"] != run_id:
        raise AdoptionError("RETENTION_RECORD_INVALID")
    return resources._unpack(row["payload_json"],row["digest"])


def _save(db,run_id,kind,identifier,payload):
    from .assurance_authority import _save as save_artifact
    return save_artifact(db,run_id,kind,identifier,payload)


def _origin(db,event,expected_actor,ref_field,ref):
    common = {"schema_version","kind","request","run_id","actor_id","context","permission_generation","recorded_at"}
    extras = {HOLD_KIND:{"state"}, PLAN_KIND:{"plan_id","evidence_ref","receipt_ref","hold_ref","reason","metadata","expires_at"},
        TOMBSTONE_KIND:{"evidence_ref","plan_ref","reason","metadata","policy_ref"}}
    kind = event.get("kind") if type(event) is dict else None
    if type(kind) is not str or kind not in extras or ref["kind"] != kind:
        raise AdoptionError("RETENTION_RECORD_INVALID")
    try:
        require_object(event,common | extras[kind])
        if type(event["schema_version"]) is not int or event["schema_version"] != 1:
            raise ContractError()
        require_uint(event["recorded_at"]);require_uint(event["permission_generation"])
    except (ContractError,KeyError,TypeError,ValueError):
        raise AdoptionError("RETENTION_RECORD_INVALID") from None
    request = validate_request(event["request"])
    if (event["actor_id"] != expected_actor or event["context"] != expected_actor + "-context"
            or event["run_id"] != request["run_id"]):
        raise AdoptionError("RETENTION_ORIGIN_INVALID")
    require_uint(event["recorded_at"]);require_uint(event["permission_generation"])
    row = db.execute("SELECT * FROM idempotency WHERE request_id=?",(request["request_id"],)).fetchone()
    if (row is None or row["actor_id"] != event["actor_id"] or row["context"] != event["context"]
            or row["request_digest"] != hashlib.sha256(canonical_bytes(request)).hexdigest()):
        raise AdoptionError("RETENTION_ORIGIN_INVALID")
    response = resources._unpack(row["response_json"],row["response_digest"])
    if (response.get("schema_version") != 1 or type(response.get("schema_version")) is not int
            or response.get("kind") != "evaluation_authority_result" or response.get("action") != request["action"]
            or response.get("request_id") != request["request_id"] or response.get("ci_eligible") is not False
            or response.get(ref_field) != ref):
        raise AdoptionError("RETENTION_ORIGIN_INVALID")
    return request


def _receipt(db,run_id,expected):
    row = db.execute("SELECT * FROM authority_run_receipts WHERE run_id=?",(run_id,)).fetchone()
    if row is None:
        raise AdoptionError("EVIDENCE_MISSING")
    receipt = resources._unpack(row["payload_json"],row["digest"])
    if (receipt.get("kind") != "authority_run_receipt" or receipt.get("run_id") != run_id
            or receipt.get("evidence_ref") != expected):
        raise AdoptionError("EVIDENCE_BINDING_MISMATCH")
    return receipt


def hold_state(db,run_id,evidence_ref,now):
    state = {"run_id":run_id,"evidence_ref":deepcopy(evidence_ref),"active":False,"sequence":0,"updated_at":None}
    rows = db.execute("SELECT * FROM authority_artifacts WHERE kind=? AND run_id=? ORDER BY id",(HOLD_KIND,run_id)).fetchall()
    if len(rows)>1000:
        raise AdoptionError("RETENTION_HISTORY_LIMIT")
    prefix = _id("rh-",evidence_ref)
    for row in rows:
        event = resources._unpack(row["payload_json"],row["digest"])
        ref = {key:row[key] for key in ("kind","id","digest")}
        request = _origin(db,event,"manager","hold_event_ref",ref)
        if (request["action"] != "evidence_retention_hold" or request["expected_evidence_ref"] != evidence_ref
                or request["expected_hold_ref"] != content_ref("retention_hold",run_id,state)
                or event["recorded_at"] > now or state["updated_at"] is not None and event["recorded_at"] < state["updated_at"] or row["id"] != prefix + "-" + str(state["sequence"]+1).zfill(6)):
            raise AdoptionError("RETENTION_HISTORY_INVALID")
        state = {**state,"active":request["active"],"sequence":state["sequence"]+1,"updated_at":event["recorded_at"]}
        if event.get("state") != state:
            raise AdoptionError("RETENTION_HISTORY_INVALID")
    return state


def tombstone(db,run_id,evidence_ref):
    identifier = _id("et-",evidence_ref)
    rows = db.execute("SELECT * FROM authority_artifacts WHERE kind=? AND id=?",(TOMBSTONE_KIND,identifier)).fetchall()
    if not rows:
        return None
    if len(rows)!=1:
        raise AdoptionError("RETENTION_RECORD_INVALID")
    row = rows[0];ref = {key:row[key] for key in ("kind","id","digest")}
    value = _read(db,ref,run_id)
    request = _origin(db,value,"operator","tombstone_ref",ref)
    if (request["action"] != "evidence_retention_apply" or request["expected_evidence_ref"] != evidence_ref
            or value.get("evidence_ref") != evidence_ref or value.get("kind") != TOMBSTONE_KIND):
        raise AdoptionError("RETENTION_RECORD_INVALID")
    plan_ref = request["plan_ref"]
    plan = _read(db,plan_ref,run_id)
    original = _origin(db,plan,"manager","plan_ref",plan_ref)
    receipt = _receipt(db,run_id,evidence_ref)
    if (value["plan_ref"] != plan_ref or original["action"] != "evidence_retention_plan"
            or original["expected_evidence_ref"] != evidence_ref or plan["evidence_ref"] != evidence_ref
            or plan["plan_id"] != plan_ref["id"] or plan["reason"] != original["reason"]
            or plan["reason"] != value["reason"] or plan["metadata"] != value["metadata"]
            or plan["receipt_ref"] != content_ref("authority_run_receipt",run_id,receipt)
            or value["policy_ref"] != plan["metadata"]["policy_ref"]
            or not plan["recorded_at"] <= value["recorded_at"] <= plan["expires_at"]
            or plan["expires_at"] != min(MAX_INTEGER,plan["recorded_at"]+3600)
            or plan["permission_generation"] != value["permission_generation"]):
        raise AdoptionError("RETENTION_RECORD_INVALID")
    if db.execute("SELECT 1 FROM authority_artifacts WHERE kind=? AND id=? AND digest=?",tuple(evidence_ref[key] for key in ("kind","id","digest"))).fetchone():
        raise AdoptionError("EVIDENCE_RESTORED_AFTER_DELETION")
    return value,ref


def assert_available(db,run_id,evidence_ref):
    if evidence_ref["kind"] == "evidence" and tombstone(db,run_id,evidence_ref) is not None:
        raise AdoptionError("EVIDENCE_DELETED")


def execute(store,db,request,actor_id,context,now,resolve_source):
    request = validate_request(request)
    action = request["action"];run_id = request["run_id"]
    if action == "run_retention_state":
        row = db.execute("SELECT * FROM authority_run_receipts WHERE run_id=?",(run_id,)).fetchone()
        if row is None:
            raise AdoptionError("EVIDENCE_MISSING")
        saved = resources._unpack(row["payload_json"],row["digest"])
        if saved.get("manifest_ref") != request["expected_manifest_ref"]:
            raise AdoptionError("EVIDENCE_BINDING_MISMATCH")
        manifest = _read(db,request["expected_manifest_ref"],run_id)
        if content_ref("run_manifest",run_id,manifest) != saved["manifest_ref"]:
            raise AdoptionError("EVIDENCE_BINDING_MISMATCH")
        ref = saved["evidence_ref"]
    else:
        ref = request["expected_evidence_ref"]
    receipt = _receipt(db,run_id,ref)
    deleted = tombstone(db,run_id,ref)
    state = hold_state(db,run_id,ref,now)
    hold_ref = content_ref("retention_hold",run_id,state)
    if action in FRESH_ACTIONS and deleted is not None:
        value,tombstone_ref = deleted
        return {"run_id":run_id,"evidence_ref":ref,"hold_state":state,"hold_ref":hold_ref,
            "deleted":True,"tombstone_ref":tombstone_ref,"saved_assurance":receipt["assurance"],
            "metadata":deepcopy(value["metadata"]),"reproduction":"REPRODUCTION_UNAVAILABLE",
            "reasons":["EVIDENCE_DELETED"],"checked_at":now,"ci_eligible":False}
    if deleted is not None:
        raise AdoptionError("EVIDENCE_DELETED")
    source = resolve_source(run_id)
    evidence = _read(db,ref,run_id)
    bound = source["bound"]
    if source["receipt"] != receipt or evidence not in source["evidences"]:
        raise AdoptionError("EVIDENCE_BINDING_MISMATCH")
    metadata = {"run_id":run_id,"evidence_ref":ref,"subject_ref":deepcopy(evidence["subject_ref"]),
        "conditions_ref":deepcopy(evidence["conditions_ref"]),"policy_ref":deepcopy(bound["manifest"]["policy_ref"]),
        "control_ids":deepcopy(bound["manifest"]["control_ids"]),"use_cases":deepcopy(bound["manifest"]["use_cases"]),
        "observed_at":evidence["observed_at"],"valid_until":evidence["valid_until"],"retention_until":evidence["retention_until"],
        "target_refs":deepcopy(bound["manifest"]["target_refs"]),"profile":bound["manifest"]["profile"],
        "contract_ref":deepcopy(bound["manifest"]["contract_ref"]),"baseline_ref":deepcopy(bound["manifest"]["baseline_ref"]),
        "unexecuted_control_ids":[c["control_id"] for c in bound["registry"]["controls"] if c["control_id"] not in bound["manifest"]["control_ids"]]}
    if action in FRESH_ACTIONS:
        return {"run_id":run_id,"evidence_ref":ref,"hold_state":state,"hold_ref":hold_ref,
            "deleted":False,"tombstone_ref":None,"saved_assurance":receipt["assurance"],"metadata":metadata,
            "reproduction":"AVAILABLE","reasons":deepcopy(source["reasons"]),"checked_at":now,"ci_eligible":False}
    event = {"schema_version":1,"request":request,"run_id":run_id,"actor_id":actor_id,"context":context,
        "permission_generation":store._permission_generation(db),"recorded_at":now}
    if action == "evidence_retention_hold":
        if request["expected_hold_ref"] != hold_ref:
            raise AdoptionError("RETENTION_STATE_CONFLICT")
        if state["sequence"]>=1000:
            raise AdoptionError("RETENTION_HISTORY_LIMIT")
        updated = {**state,"sequence":state["sequence"]+1,"active":request["active"],"updated_at":now}
        identifier = _id("rh-",ref) + "-" + str(updated["sequence"]).zfill(6)
        saved = _save(db,run_id,HOLD_KIND,identifier,{**event,"kind":HOLD_KIND,"state":updated})
        return {"hold_state":updated,"hold_ref":content_ref("retention_hold",run_id,updated),"hold_event_ref":saved,"ci_eligible":False}
    if state["active"]:
        raise AdoptionError("RETENTION_HOLD")
    if action == "evidence_retention_plan":
        if request["expected_hold_ref"] != hold_ref:
            raise AdoptionError("RETENTION_STATE_CONFLICT")
        if request["reason"] == "RETENTION_EXPIRED" and now<=evidence["retention_until"]:
            raise AdoptionError("RETENTION_NOT_EXPIRED")
        identifier = _id("rp-",request)
        plan = {**event,"schema_version":1,"kind":PLAN_KIND,"plan_id":identifier,"evidence_ref":ref,
            "receipt_ref":content_ref("authority_run_receipt",run_id,receipt),"hold_ref":hold_ref,
            "reason":request["reason"],"metadata":metadata,"expires_at":min(MAX_INTEGER,now+3600)}
        plan_ref = _save(db,run_id,PLAN_KIND,identifier,plan)
        return {"plan_ref":plan_ref,"plan":plan,"deleted":False,"ci_eligible":False}
    plan = _read(db,request["plan_ref"],run_id)
    original = _origin(db,plan,"manager","plan_ref",request["plan_ref"])
    if (original["action"] != "evidence_retention_plan" or original["expected_evidence_ref"] != ref
            or plan.get("kind") != PLAN_KIND or plan.get("evidence_ref") != ref or plan.get("metadata") != metadata
            or plan["plan_id"] != request["plan_ref"]["id"] or plan["expires_at"] != min(MAX_INTEGER,plan["recorded_at"]+3600)
            or plan["receipt_ref"] != content_ref("authority_run_receipt",run_id,receipt)
            or plan["permission_generation"] != store._permission_generation(db) or store._actor_revoked(db,"manager")
            or not plan["recorded_at"]<=now<=plan["expires_at"] or plan["hold_ref"] != hold_ref):
        raise AdoptionError("RETENTION_PLAN_INVALID")
    if plan["reason"] != original["reason"] or plan["reason"] == "RETENTION_EXPIRED" and now<=evidence["retention_until"]:
        raise AdoptionError("RETENTION_PLAN_INVALID")
    identifier = _id("et-",ref)
    value = {**event,"schema_version":1,"kind":TOMBSTONE_KIND,"evidence_ref":ref,
        "plan_ref":request["plan_ref"],"reason":plan["reason"],"metadata":metadata,"policy_ref":metadata["policy_ref"]}
    tombstone_ref = _save(db,run_id,TOMBSTONE_KIND,identifier,value)
    cursor = db.execute("DELETE FROM authority_artifacts WHERE kind=? AND id=? AND digest=? AND run_id=?",
        (ref["kind"],ref["id"],ref["digest"],run_id))
    if cursor.rowcount != 1:
        raise AdoptionError("RETENTION_DELETE_CONFLICT")
    return {"run_id":run_id,"evidence_ref":ref,"tombstone_ref":tombstone_ref,"deleted":True,
        "reproduction":"REPRODUCTION_UNAVAILABLE","ci_eligible":False}
