"""採択済み固定UC-CI契約の通常runと、現在時点のCI利用検査。"""
from copy import deepcopy

from .adoption import AdoptionError
from .read_checks import checked_read, checked_action
from .contracts import ContractError, require_id, require_object, require_ref, require_uint
from . import resources, transition_authority, transition_acceptance, run_scope
from .run_contracts import bind_run_manifest, content_ref



from .cache_inputs import bind_run_manifest
ACTIONS = {"run_prepare": {"operator"}, "run_prepare_scoped": {"operator"}, "run_outputs": {"manager", "validator", "operator"},
           "run_artifact": {"manager", "validator", "operator"},
           "run_cancel_finalize": {"operator"},
           "ci_check": {"operator"}}
FRESH_ACTIONS = {"run_outputs", "run_artifact", "ci_check"}
_BASE = {"schema_version", "action", "request_id", "run_id"}
FIELDS = {"run_prepare": _BASE | {"contract_series_id", "expected_contract_ref"},
          "run_prepare_scoped": _BASE | {"contract_series_id", "expected_contract_ref", "changed_refs"},
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
        if action in {"run_prepare", "run_prepare_scoped"} and len(request["run_id"]) > 64:
            raise ContractError()
        if action == "run_prepare_scoped":
            run_scope.normalize_refs(request["changed_refs"])
            if request["request_id"] != run_scope.request_id(request["run_id"]):
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
    result = deepcopy(request)
    if action == "run_prepare_scoped":
        result["changed_refs"] = run_scope.normalize_refs(result["changed_refs"])
    return result


def build(db, history, run_id, created_at, now, *, changed_refs=None):
    """不変の採択履歴から再生成する。現在の失効や開始許可は呼出側で検査する。"""
    try:
        require_id(run_id)
        require_uint(created_at)
        require_uint(now)
        if len(run_id) > 64 or history is None or history["generation"] < 2 or created_at > now:
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
        if old_bound["manifest"].get("schema_version") == 2:
            if changed_refs is not None or run_scope.load(db, history, run_id) is not None:
                raise AdoptionError("LLM_SCOPE_NOT_CONNECTED")
            from .partitioned_llm_transitions import rebind
            return rebind(template, run_id=run_id, now=created_at, purpose="regression")
        plan = deepcopy(old_bound["plan"])
        plan["plan_id"] = "regression-plan-" + run_id
        manifest = deepcopy(old_bound["manifest"])
        context = deepcopy(template["baseline_context"])
        materialization = deepcopy(template["materialization"])
        saved_scope = run_scope.load(db, history, run_id)
        if saved_scope is not None:
            changed_refs = saved_scope["scope"]["changed_refs"]
        if changed_refs is not None and old_bound["manifest"]["use_cases"] == ["UC-LLM"]:
            raise AdoptionError("LLM_SCOPE_NOT_CONNECTED")
        scope = None if changed_refs is None else run_scope.derive(old_bound, run_id, changed_refs)
        if scope is not None:
            run_scope.restrict(scope, plan, manifest, context, materialization, old_bound["registry"])
        manifest.update(run_id=run_id, purpose="regression", created_at=created_at,
            deadline=created_at + old_bound["manifest"]["deadline"] - old_bound["manifest"]["created_at"],
            plan_ref=content_ref("trial_plan", plan["plan_id"], plan),
            actor_context_ref=content_ref("actor_context", "regression-operator-" + run_id,
                {"role": "operator", "purpose": "fixed-regression"}))
        if scope is not None:
            manifest["actor_context_ref"] = content_ref("actor_context", "regression-operator-" + run_id,
                {"role": "operator", "purpose": "scoped-regression", "scope": scope})
        bound = bind_run_manifest(manifest, contract, plan, old_bound["policy"], old_bound["registry"],
            old_bound["case_set"], baseline_context=context)
        if bound["manifest"]["use_cases"] == ["UC-LLM"]:
            if scope is not None:
                raise AdoptionError("LLM_SCOPE_NOT_CONNECTED")
            from .llm_transitions import rebind
            return rebind(template, bound, context)
        material = materialization["manifest"]
        material.update(run_id=run_id, materialization_id="materialization-" + run_id, created_at=created_at)
        materialization["manifest_ref"] = content_ref("fixture_manifest", material["materialization_id"], material)
        result = {"bound_run": bound, "baseline_context": context, "materialization": materialization}
        if scope is not None:
            result["scope"] = scope
        if saved_scope is not None:
            from .evaluation_authority import _result
            if saved_scope != _result("run_prepare_scoped", run_scope.request_id(run_id), **result):
                raise AdoptionError("RUN_SCOPE_INVALID")
        return result
    except AdoptionError:
        raise
    except (ContractError, resources.ResourceError, KeyError, TypeError, ValueError):
        raise AdoptionError("REGRESSION_BINDING_INVALID") from None


@checked_read
def for_run(db, row, now):
    if row is None:
        raise AdoptionError("RUN_MISSING")
    manifest = resources._unpack(row["manifest_json"], row["manifest_digest"])
    history = db.execute("SELECT * FROM eval_adoptions WHERE series_id=? AND generation=?",
        (row["contract_series_id"], row["contract_generation"])).fetchone()
    result = build(db, history, row["run_id"], manifest["created_at"], now)
    stored_plan = result["plan_index"] if manifest.get("schema_version") == 2 else result["bound_run"]["plan"]
    if (manifest != result["bound_run"]["manifest"]
            or resources._unpack(row["plan_json"], row["plan_digest"]) != stored_plan):
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
    from . import combined_runs
    grouped = combined_runs.for_child(db, request["run_id"], now)
    if grouped is not None:
        if "changed_refs" in request:
            raise AdoptionError("COMBINED_BINDING_INVALID")
        prepared = grouped[1]
        child = next(c for c in grouped[0]["manifest"]["children"] if c["run_id"] == request["run_id"])
        if (prepared["bound_run"]["manifest"]["contract_ref"] != request["expected_contract_ref"]
                or child["contract_series_id"] != request["contract_series_id"]):
            raise AdoptionError("COMBINED_BINDING_INVALID")
        return prepared
    return build(db, current, request["run_id"], now, now, changed_refs=request.get("changed_refs"))


@checked_action
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
        has_cancel_receipt = run_cancellation.exists(db, request["run_id"])
        if not has_cancel_receipt:
            # A single marker read avoids scanning every resource operation for
            # ordinary CI calls. It only selects the exceptional path; _stopped
            # re-reads the complete snapshot and proves every operation stopped.
            marker = db.execute("SELECT cancelled FROM resource_runs WHERE run_id=?",
                                (request["run_id"],)).fetchone()
            if marker is not None and type(marker[0]) is int and marker[0] == 1:
                if not _matches(request, manifest):
                    raise AdoptionError("CI_TARGET_MISMATCH")
                run_cancellation._stopped(db, request["run_id"], now)
                # Fully stopped but not yet terminal still has no usable receipt.
                raise AdoptionError("NOT_FINALIZED")
        if has_cancel_receipt:
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
        try:
            transition_acceptance._check_source(db, source, bound, now, allow_unhealthy=True)
        except AdoptionError as error:
            # A failed earlier integrity check is not the final revocation condition.
            # Preserve it without inspecting possibly malformed source data.
            if error.code != "SOURCE_NOT_READY":
                raise
            # _check_source validates bindings, closure, operations and expiry before
            # collapsing a fresh evidence revocation into SOURCE_NOT_READY. Preserve
            # the public revocation reason only for that verified terminal condition.
            revoked = False
            if type(source) is dict:
                reasons_value = source.get("reasons")
                revoked = (type(reasons_value) is list
                           and any(type(item) is str and item == "EVIDENCE_REVOKED"
                                   for item in reasons_value))
                evidences_value = source.get("evidences")
                states_value = source.get("evidence_states")
                if (type(evidences_value) is list and len(evidences_value) == 1
                        and type(evidences_value[0]) is dict and type(states_value) is dict):
                    evidence_id = evidences_value[0].get("evidence_id")
                    state_value = states_value.get(evidence_id)
                    revoked = revoked or (type(state_value) is dict
                                          and type(state_value.get("revoked")) is bool
                                          and state_value["revoked"] is True)
            if revoked:
                raise AdoptionError("EVIDENCE_REVOKED") from None
            raise
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
            "SOURCE_NOT_READY", "EVIDENCE_REVOKED", "SOURCE_EXPIRED", "SOURCE_OPERATION_INVALID", "NOT_FINALIZED",
            "STOP_UNCONFIRMED",
            "PREREQUISITE_UNAVAILABLE", "CANDIDATE_EXPIRED", "EVIDENCE_DELETED", "EVIDENCE_RESTORED_AFTER_DELETION", "TARGET_RETIRED", "RETIREMENT_INVALID", "RETIREMENT_ORIGIN_INVALID"}
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
        "CI_TARGET_MISMATCH", "CONTRACT_INVALID", "SOURCE_NOT_READY", "EVIDENCE_REVOKED", "SOURCE_EXPIRED",
        "PREREQUISITE_UNAVAILABLE", "CANDIDATE_EXPIRED"}
    exit_code = 3 if cancelled else (0 if use else (1 if set(reasons) <= completed_rejections else 2))
    return {"schema_version": 1, "kind": "ci_gate_result", "action": "ci_check",
        "request_id": request["request_id"], "run_id": request["run_id"], "checked_at": now,
        "expected_manifest_ref": deepcopy(request["expected_manifest_ref"]), "outputs_ref": outputs_ref,
        "assurance": assurance, "reasons": reasons, "use": use, "ci_eligible": use,
        "execution_status": {0: "COMPLETED", 1: "COMPLETED", 2: "FAILED", 3: "CANCELLED"}[exit_code], "exit_code": exit_code}
