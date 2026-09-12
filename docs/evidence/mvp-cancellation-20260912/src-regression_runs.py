"""採択済み固定UC-CI契約の通常runと、現在時点のCI利用検査。"""
from copy import deepcopy

from .adoption import AdoptionError
from .contracts import ContractError, require_id, require_object, require_ref, require_uint
from . import resources, transition_authority, transition_acceptance
from .run_contracts import bind_run_manifest, content_ref


ACTIONS = {"run_prepare": {"operator"}, "run_outputs": {"manager", "validator", "operator"},
           "run_artifact": {"manager", "validator", "operator"},
           "run_cancel_finalize": {"operator"},
           "ci_check": {"operator"}}
FRESH_ACTIONS = {"run_outputs", "run_artifact", "ci_check"}
_BASE = {"schema_version", "action", "request_id", "run_id"}
FIELDS = {"run_prepare": _BASE | {"contract_series_id", "expected_contract_ref"},
          "run_outputs": _BASE,
          "run_artifact": _BASE | {"artifact_ref"},
          "run_cancel_finalize": _BASE,
          "ci_check": _BASE | {"expected_manifest_ref", "expected_contract_ref",
              "expected_baseline_ref", "expected_target_refs", "expected_use_cases"}}


def validate_request(request):
    try:
        action = request.get("action") if type(request) is dict else None
        if type(action) is not str or action not in FIELDS:
            raise ContractError()
        require_object(request, FIELDS[action])
        if type(request["schema_version"]) is not int or request["schema_version"] != 1:
            raise ContractError()
        for field in ("request_id", "run_id", "contract_series_id"):
            if field in request:
                require_id(request[field])
        if action == "run_prepare" and len(request["run_id"]) > 64:
            raise ContractError()
        if action == "run_artifact":
            require_ref(request["artifact_ref"])
        for field, kind in (("expected_manifest_ref", "run_manifest"),
                            ("expected_contract_ref", "evaluation_contract"),
                            ("expected_baseline_ref", "baseline")):
            if field in request:
                require_ref(request[field])
                if request[field]["kind"] != kind:
                    raise ContractError()
        if action == "ci_check":
            targets = request["expected_target_refs"]
            if type(targets) is not list or not 0 < len(targets) <= 100:
                raise ContractError()
            for target in targets:
                require_ref(target)
                if target["kind"] != "target":
                    raise ContractError()
            if len({tuple(t[k] for k in ("kind", "id", "digest")) for t in targets}) != len(targets):
                raise ContractError()
            uses = request["expected_use_cases"]
            if (type(uses) is not list or not uses or any(type(x) is not str or x not in {"UC-CI", "UC-LLM"} for x in uses)
                    or len(set(uses)) != len(uses)):
                raise ContractError()
    except (ContractError, KeyError, TypeError, ValueError):
        raise AdoptionError("INVALID_REQUEST") from None
    return deepcopy(request)


def build(db, history, run_id, created_at, now):
    """不変の採択履歴から再生成する。現在の失効や開始許可は呼出側で検査する。"""
    try:
        require_id(run_id)
        require_uint(created_at)
        require_uint(now)
        if len(run_id) > 64 or history is None or history["generation"] != 2 or created_at > now:
            raise ContractError()
        contract = resources._unpack(history["payload_json"], history["digest"])
        validation = transition_acceptance.history(db, history, contract)
        payload = resources._unpack(validation["payload_json"], validation["digest"])
        if created_at < validation["created_at"]:
            raise ContractError()
        _, candidate = transition_authority.load_candidate(db, payload["candidate_id"], now)
        forbidden = {candidate["runs"][side]["bound_run"]["manifest"]["run_id"] for side in ("old", "new")}
        forbidden.add(candidate["runs"]["transition"]["source_run_ref"]["id"])
        if run_id in forbidden or db.execute("SELECT 1 FROM transition_runs WHERE run_id=?", (run_id,)).fetchone():
            raise AdoptionError("CANDIDATE_ENTRY_REQUIRED")
        template = candidate["runs"]["new"]
        old_bound = template["bound_run"]
        plan = deepcopy(old_bound["plan"])
        plan["plan_id"] = "regression-plan-" + run_id
        manifest = deepcopy(old_bound["manifest"])
        manifest.update(run_id=run_id, purpose="regression", created_at=created_at,
            deadline=created_at + old_bound["policy"]["profiles"]["full"]["elapsed_seconds"],
            plan_ref=content_ref("trial_plan", plan["plan_id"], plan),
            actor_context_ref=content_ref("actor_context", "regression-operator-" + run_id,
                {"role": "operator", "purpose": "fixed-regression"}))
        context = deepcopy(template["baseline_context"])
        bound = bind_run_manifest(manifest, contract, plan, old_bound["policy"], old_bound["registry"],
            old_bound["case_set"], baseline_context=context)
        materialization = deepcopy(template["materialization"])
        material = materialization["manifest"]
        material.update(run_id=run_id, materialization_id="materialization-" + run_id, created_at=created_at)
        materialization["manifest_ref"] = content_ref("fixture_manifest", material["materialization_id"], material)
        return {"bound_run": bound, "baseline_context": context, "materialization": materialization}
    except AdoptionError:
        raise
    except (ContractError, resources.ResourceError, KeyError, TypeError, ValueError):
        raise AdoptionError("REGRESSION_BINDING_INVALID") from None


def for_run(db, row, now):
    if row is None:
        raise AdoptionError("RUN_MISSING")
    manifest = resources._unpack(row["manifest_json"], row["manifest_digest"])
    history = db.execute("SELECT * FROM eval_adoptions WHERE series_id=? AND generation=?",
        (row["contract_series_id"], row["contract_generation"])).fetchone()
    result = build(db, history, row["run_id"], manifest["created_at"], now)
    if (manifest != result["bound_run"]["manifest"]
            or resources._unpack(row["plan_json"], row["plan_digest"]) != result["bound_run"]["plan"]):
        raise AdoptionError("REGRESSION_BINDING_INVALID")
    return result


def materialized_run(db, bound, now):
    row = db.execute("SELECT * FROM eval_runs WHERE run_id=?", (bound["manifest"]["run_id"],)).fetchone()
    if for_run(db, row, now)["bound_run"] != bound:
        raise AdoptionError("REGRESSION_BINDING_INVALID")
    return True


def prepare(store, db, request, now):
    from .evaluation_authority import _validate_state, _assert_current_valid
    current = db.execute("SELECT * FROM eval_current WHERE series_id=?", (request["contract_series_id"],)).fetchone()
    if current is None:
        raise AdoptionError("CONTRACT_MISSING")
    contract = resources._unpack(current["payload_json"], current["digest"])
    if request["expected_contract_ref"] != content_ref("evaluation_contract", contract["contract_id"], contract):
        raise AdoptionError("BINDING_MISMATCH")
    for table in ("eval_runs", "resource_runs", "transition_runs", "fixture_admissions"):
        if db.execute("SELECT 1 FROM " + table + " WHERE run_id=?", (request["run_id"],)).fetchone():
            raise AdoptionError("RUN_CONFLICT")
    _validate_state(store, db, contract, now)
    _assert_current_valid(store, db, current, contract, now)
    return build(db, current, request["run_id"], now, now)


def ci_check(store, db, request, now):
    """固定brokerの決定的検査。入力の成功宣言や過去のCI応答を採用しない。"""
    from . import assurance_authority, run_evidence, run_outputs, run_cancellation
    from .evaluation_authority import EvaluationExtension
    reasons, assurance, outputs_ref = [], "UNKNOWN", None
    cancelled = False
    try:
        row = db.execute("SELECT * FROM eval_runs WHERE run_id=?", (request["run_id"],)).fetchone()
        if row is None:
            raise AdoptionError("RUN_MISSING")
        manifest = resources._unpack(row["manifest_json"], row["manifest_digest"])
        if manifest["purpose"] != "regression":
            raise AdoptionError("CI_PURPOSE_REQUIRED")
        extension = EvaluationExtension()
        if run_cancellation.exists(db, request["run_id"]):
            bound, baseline = extension._bound_evidence_run(store, db, request["run_id"], now)
            cancellation = run_cancellation.load(db, bound, baseline, now)
            assurance = cancellation["assurance"]
            outputs_ref = cancellation["outputs"]["outputs_ref"]
            if not _matches(request, manifest):
                raise AdoptionError("CI_TARGET_MISMATCH")
            cancelled = True
            reasons.append("CANCEL_REQUESTED")
            if not cancellation["snapshot"]["budget_closure"]:
                reasons.append("BUDGET_OPEN")
            return _ci_result(request, now, reasons, assurance, outputs_ref, cancelled=True)
        source = assurance_authority.baseline_source(store, db, request["run_id"], now,
            lambda run_id: extension._bound_evidence_run(store, db, run_id, now))
        bound = source["bound"]
        if source["decision"]["assurance"] in {"HEALTHY", "WARNING", "DEGRADED", "UNKNOWN", "HOLD"}:
            assurance = source["decision"]["assurance"]
        outputs_ref = run_outputs.read(db, bound, source["receipt"])["outputs_ref"]
        transition_acceptance._check_source(db, source, bound, now, allow_unhealthy=True)
        current = db.execute("SELECT * FROM eval_current WHERE series_id=?", (row["contract_series_id"],)).fetchone()
        if (current is None or current["generation"] != row["contract_generation"]
                or current["digest"] != manifest["contract_ref"]["digest"]):
            raise AdoptionError("CURRENT_CONTRACT_MISMATCH")
        if not _matches(request, manifest):
            raise AdoptionError("CI_TARGET_MISMATCH")
        extension._check_start(store, db, request["run_id"], now)
    except (AdoptionError, ContractError, resources.ResourceError, run_evidence.EvidenceError) as error:
        allowed = {"RUN_MISSING", "CI_PURPOSE_REQUIRED", "CURRENT_CONTRACT_MISMATCH", "CI_TARGET_MISMATCH",
            "CONTRACT_INVALID", "REGRESSION_BINDING_INVALID", "RUN_OUTPUTS_MISSING", "RUN_OUTPUTS_INVALID",
            "SOURCE_NOT_READY", "SOURCE_EXPIRED", "SOURCE_OPERATION_INVALID", "NOT_FINALIZED",
            "PREREQUISITE_UNAVAILABLE", "CANDIDATE_EXPIRED"}
        reasons.append(error.code if error.code in allowed else "EVIDENCE_UNAVAILABLE")
    return _ci_result(request, now, reasons, assurance, outputs_ref, cancelled=cancelled)


def _matches(request, manifest):
    return (request["expected_manifest_ref"] == content_ref("run_manifest", request["run_id"], manifest)
        and request["expected_contract_ref"] == manifest["contract_ref"]
        and request["expected_baseline_ref"] == manifest["baseline_ref"]
        and request["expected_target_refs"] == manifest["target_refs"]
        and request["expected_use_cases"] == manifest["use_cases"])


def _ci_result(request, now, reasons, assurance, outputs_ref, *, cancelled=False):
    if assurance not in {"HEALTHY", "WARNING"}:
        reasons.append("ASSURANCE_NOT_ALLOWED")
    use = not reasons
    completed_rejections = {"ASSURANCE_NOT_ALLOWED", "CI_PURPOSE_REQUIRED", "CURRENT_CONTRACT_MISMATCH",
        "CI_TARGET_MISMATCH", "CONTRACT_INVALID", "SOURCE_NOT_READY", "SOURCE_EXPIRED",
        "PREREQUISITE_UNAVAILABLE", "CANDIDATE_EXPIRED"}
    exit_code = 3 if cancelled else (0 if use else (1 if set(reasons) <= completed_rejections else 2))
    return {"schema_version": 1, "kind": "ci_gate_result", "action": "ci_check",
        "request_id": request["request_id"], "run_id": request["run_id"], "checked_at": now,
        "expected_manifest_ref": deepcopy(request["expected_manifest_ref"]), "outputs_ref": outputs_ref,
        "assurance": assurance, "reasons": reasons, "use": use, "ci_eligible": use,
        "execution_status": {0: "COMPLETED", 1: "COMPLETED", 2: "FAILED", 3: "CANCELLED"}[exit_code], "exit_code": exit_code}
