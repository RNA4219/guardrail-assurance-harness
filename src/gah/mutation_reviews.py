"""成立しなかったMutationの審査を、元の判定と分離して不変保存する。"""
from copy import deepcopy
import hashlib
from .adoption import AdoptionError
from .contracts import ContractError, require_object, require_id, require_ref, require_uint
from .run_contracts import content_ref
from .wire import canonical_bytes
from . import assurance_authority, resources
from .read_checks import checked_action

BASE={"schema_version","action","request_id","run_id","expected_evidence_ref"}
FIELDS={"mutation_review_validate":BASE|{"attempt_id","rationale"},
    "mutation_review_approve":BASE|{"validation_ref"},"mutation_review_current":BASE}
ACTIONS={"mutation_review_validate":{"validator"},"mutation_review_approve":{"manager"},
    "mutation_review_current":{"manager","validator","operator"}}
FRESH_ACTIONS={"mutation_review_current"}
VALIDATION="mutation_exclusion_validation"
APPROVAL="mutation_exclusion_approval"
POLICY={"schema_version":1,"kind":"mutation_exclusion_policy","policy_id":"invalid-mutant-review-v1",
    "supported_basis":"MUTATION_NOT_APPLIED","baseline_required":"PASS","single_stage_only":True,
    "retry_chains_supported":False,"changes_original_decision":False,"satisfies_required_obligations":False}
POLICY_REF=content_ref(POLICY["kind"],POLICY["policy_id"],POLICY)


def validate_request(request):
    try:
        action=request["action"];require_object(request,FIELDS[action])
        if type(request["schema_version"]) is not int or request["schema_version"]!=1:raise ContractError()
        require_id(request["request_id"]);require_id(request["run_id"])
        require_ref(request["expected_evidence_ref"])
        if request["expected_evidence_ref"]["kind"]!="evidence":raise ContractError()
        if action=="mutation_review_validate":
            require_id(request["attempt_id"]);rationale=request["rationale"]
            if type(rationale) is not str or not rationale.strip() or len(rationale.encode("utf8"))>2048:raise ContractError()
        if action=="mutation_review_approve":
            require_ref(request["validation_ref"])
            if request["validation_ref"]["kind"]!=VALIDATION:raise ContractError()
    except (ContractError,KeyError,TypeError,ValueError,UnicodeError):
        raise AdoptionError("INVALID_REQUEST") from None
    return deepcopy(request)


def _tag(run_id,attempt_id):
    return "mx-"+hashlib.sha256(canonical_bytes([run_id,attempt_id])).hexdigest()[:40]


def _ref(kind,value):
    return content_ref(kind,value["review_id"],value)


def _save(db,run_id,kind,value):
    if db.execute("SELECT 1 FROM authority_artifacts WHERE kind=? AND id=?",(kind,value["review_id"])).fetchone():
        raise AdoptionError("MUTATION_REVIEW_CONFLICT")
    return assurance_authority._save(db,run_id,kind,value["review_id"],value)


def _load(db,run_id,ref):
    rows=db.execute("SELECT * FROM authority_artifacts WHERE kind=? AND id=?",(ref["kind"],ref["id"])).fetchall()
    if len(rows)!=1 or rows[0]["run_id"]!=run_id:raise AdoptionError("MUTATION_REVIEW_INVALID")
    value=resources._unpack(rows[0]["payload_json"],rows[0]["digest"])
    if _ref(ref["kind"],value)!=ref:raise AdoptionError("MUTATION_REVIEW_INVALID")
    return value


def _origin(db,value,role,field):
    request=validate_request(value["request"])
    row=db.execute("SELECT * FROM idempotency WHERE request_id=?",(request["request_id"],)).fetchone()
    if (row is None or row["actor_id"]!=role or row["context"]!=role+"-context"
            or row["request_digest"]!=hashlib.sha256(canonical_bytes(request)).hexdigest()):
        raise AdoptionError("MUTATION_REVIEW_ORIGIN_INVALID")
    response=resources._unpack(row["response_json"],row["response_digest"])
    if (set(response)!={"schema_version","kind","action","request_id","ci_eligible",field,field+"_ref"}
            or type(response["schema_version"]) is not int or response["schema_version"]!=1
            or response["kind"]!="evaluation_authority_result" or response["action"]!=request["action"]
            or response["request_id"]!=request["request_id"] or response["ci_eligible"] is not False
            or response.get(field)!=value or response.get(field+"_ref")!=_ref(value["kind"],value)):
        raise AdoptionError("MUTATION_REVIEW_ORIGIN_INVALID")


def _source(source,request):
    if source["receipt"]["evidence_ref"]!=request["expected_evidence_ref"]:
        raise AdoptionError("MUTATION_REVIEW_BINDING_MISMATCH")
    if source["bound"]["manifest"]["run_id"]!=request["run_id"]:
        raise AdoptionError("MUTATION_REVIEW_BINDING_MISMATCH")


def _proof(db,source,attempt_id):
    run_id=source["bound"]["manifest"]["run_id"]
    rows=db.execute("SELECT * FROM attempts WHERE run_id=?",(run_id,)).fetchall()
    attempts=[resources._unpack(row["attempt_json"],row["attempt_digest"]) for row in rows]
    chosen=[a for a in attempts if a["attempt_id"]==attempt_id]
    if len(chosen)!=1:raise AdoptionError("MUTATION_REVIEW_ATTEMPT_MISSING")
    attempt=chosen[0];binding=attempt["expected_binding"];result=attempt["result"]
    identity=lambda a:(a["variant"],*(a["expected_binding"][k] for k in ("obligation_id","case_id","trial_id")))
    if len([a for a in attempts if identity(a)==identity(attempt)])!=1 or attempt["retry_of"] is not None:
        raise AdoptionError("MUTATION_REVIEW_RETRY_UNSUPPORTED")
    entries=[e for e in source["bound"]["plan"]["entries"] if
        (e["variant"],e["obligation_id"],e["case_id"],e["trial_id"])==identity(attempt)]
    if len(entries)!=1 or entries[0]["stage_ids"]!=[binding["stage_id"]]:
        raise AdoptionError("MUTATION_REVIEW_STAGE_UNSUPPORTED")
    owners=[(c,o) for c in source["bound"]["registry"]["controls"] for o in c["obligations"]
        if o["obligation_id"]==binding["obligation_id"]]
    if (len(owners)!=1 or owners[0][1]["kind"]!="mutation" or attempt["execution_status"]!="COMPLETED"
            or attempt["stop_confirmed"] is not True or result is None or result["mode"]!="mutation"
            or result["observation"]!="PASS" or result["mutation_outcome"]!="ERROR"
            or result["error_class"]!="MUTATION_NOT_APPLIED" or result["raw_digest"] is None):
        raise AdoptionError("MUTATION_REVIEW_BASIS_UNSUPPORTED")
    return {"attempt_ref":content_ref("attempt_record",attempt_id,attempt),"evidence_ref":source["receipt"]["evidence_ref"],
        "manifest_ref":source["receipt"]["manifest_ref"],"contract_ref":source["bound"]["manifest"]["contract_ref"],
        "target_ref":entries[0]["target_ref"],"evaluator_ref":entries[0]["evaluator_ref"],
        "control_id":owners[0][0]["control_id"],"obligation_id":binding["obligation_id"],"case_id":binding["case_id"],
        "trial_id":binding["trial_id"],"variant":attempt["variant"],"raw_digest":result["raw_digest"],
        "reason_code":"INVALID_MUTATION_NOT_APPLIED","policy_ref":POLICY_REF}


def _validation(db,source,ref,now):
    value=_load(db,source["bound"]["manifest"]["run_id"],ref)
    require_object(value,{"schema_version","kind","review_id","request","proof","created_at","valid_until","permission_generation","reviewed_by","ci_eligible"})
    for field in ("created_at","valid_until","permission_generation"):require_uint(value[field])
    request=validate_request(value["request"]);_source(source,request)
    if (type(value["schema_version"]) is not int or value["schema_version"]!=1 or value["kind"]!=VALIDATION
            or request["action"]!="mutation_review_validate" or value["reviewed_by"]!={"actor_id":"validator","context":"validator-context"}
            or value["review_id"]!=_tag(request["run_id"],request["attempt_id"]) or value["ci_eligible"] is not False
            or not source["receipt"]["created_at"]<=value["created_at"]<=now
            or value["valid_until"]!=min(value["created_at"]+86400,source["evidences"][0]["valid_until"])
            or value["proof"]!=_proof(db,source,request["attempt_id"])):
        raise AdoptionError("MUTATION_REVIEW_INVALID")
    _origin(db,value,"validator","validation")
    return value


@checked_action
def execute(store,db,request,actor_id,context,now,resolve_source):
    request=validate_request(request);action=request["action"];run_id=request["run_id"]
    if actor_id not in ACTIONS[action] or context!=actor_id+"-context":raise AdoptionError("AUTHORITY_DENIED")
    source=resolve_source(run_id);_source(source,request)
    permission=store._permission_generation(db)
    fresh=(not source["reasons"] and not store._actor_revoked(db,"validator") and not store._actor_revoked(db,"manager"))
    if action=="mutation_review_validate":
        if not fresh:raise AdoptionError("MUTATION_REVIEW_SOURCE_UNAVAILABLE")
        proof=_proof(db,source,request["attempt_id"])
        value={"schema_version":1,"kind":VALIDATION,"review_id":_tag(run_id,request["attempt_id"]),"request":request,
            "proof":proof,"created_at":now,"valid_until":min(now+86400,source["evidences"][0]["valid_until"]),
            "permission_generation":permission,"reviewed_by":{"actor_id":actor_id,"context":context},"ci_eligible":False}
        ref=_save(db,run_id,VALIDATION,value)
        return {"validation_ref":ref,"validation":value,"ci_eligible":False}
    if action=="mutation_review_approve":
        validation=_validation(db,source,request["validation_ref"],now)
        if not fresh or now>validation["valid_until"] or validation["permission_generation"]!=permission:
            raise AdoptionError("MUTATION_REVIEW_SOURCE_UNAVAILABLE")
        value={"schema_version":1,"kind":APPROVAL,"review_id":validation["review_id"],"request":request,
            "validation_ref":request["validation_ref"],"created_at":now,"permission_generation":permission,
            "approved_by":{"actor_id":actor_id,"context":context},"ci_eligible":False}
        ref=_save(db,run_id,APPROVAL,value)
        return {"approval_ref":ref,"approval":value,"ci_eligible":False}
    rows=db.execute("SELECT * FROM authority_artifacts WHERE kind=? AND run_id=? ORDER BY id LIMIT 1001",(VALIDATION,run_id)).fetchall()
    if len(rows)>1000:raise AdoptionError("MUTATION_REVIEW_LIMIT")
    known={row["id"] for row in rows}
    approvals=db.execute("SELECT id FROM authority_artifacts WHERE kind=? AND run_id=?",(APPROVAL,run_id)).fetchall()
    if any(row["id"] not in known for row in approvals):raise AdoptionError("MUTATION_REVIEW_INVALID")
    items=[];counts={variant:{"approved_exclusions":0,"pending_exclusions":0} for variant in ("baseline","candidate")}
    for row in rows:
        ref={"kind":VALIDATION,"id":row["id"],"digest":row["digest"]};validation=_validation(db,source,ref,now)
        approvals=db.execute("SELECT * FROM authority_artifacts WHERE kind=? AND id=?",(APPROVAL,row["id"])).fetchall()
        if len(approvals)>1:raise AdoptionError("MUTATION_REVIEW_INVALID")
        approval=None;approval_ref=None
        active=fresh and now<=validation["valid_until"] and validation["permission_generation"]==permission
        if approvals:
            approval_ref={"kind":APPROVAL,"id":row["id"],"digest":approvals[0]["digest"]}
            approval=_load(db,run_id,approval_ref)
            require_object(approval,{"schema_version","kind","review_id","request","validation_ref","created_at","permission_generation","approved_by","ci_eligible"})
            for field in ("created_at","permission_generation"):require_uint(approval[field])
            approved_request=validate_request(approval["request"]);_source(source,approved_request)
            if (type(approval["schema_version"]) is not int or approval["schema_version"]!=1 or approval["kind"]!=APPROVAL
                    or approved_request["action"]!="mutation_review_approve" or approval["review_id"]!=validation["review_id"]
                    or approval["validation_ref"]!=ref or approved_request["validation_ref"]!=ref
                    or approval["approved_by"]!={"actor_id":"manager","context":"manager-context"}
                    or not validation["created_at"]<=approval["created_at"]<=min(now,validation["valid_until"])
                    or approval["ci_eligible"] is not False):raise AdoptionError("MUTATION_REVIEW_INVALID")
            _origin(db,approval,"manager","approval");active &= approval["permission_generation"]==permission
        else:active=False
        status="EXCLUDED" if active else "EXCLUSION_PENDING"
        counts[validation["proof"]["variant"]]["approved_exclusions" if active else "pending_exclusions"]+=1
        items.append({"validation_ref":ref,"approval_ref":approval_ref,"validation":validation,"approval":approval,"status":status})
    return {"run_id":run_id,"evidence_ref":request["expected_evidence_ref"],"contract_ref":source["bound"]["manifest"]["contract_ref"],
        "policy_ref":POLICY_REF,"checked_at":now,"items":items,"counts":counts,
        "original_decision_ref":source["receipt"]["decision_ref"],"original_decision_unchanged":True,
        "required_obligations_unchanged":True,"ci_eligible":False}
