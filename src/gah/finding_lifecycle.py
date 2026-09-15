"""元runのFindingと、認証された再検証の状態を不変イベントへ結ぶ。"""
from copy import deepcopy
import hashlib
from .adoption import AdoptionError
from .contracts import ContractError, require_object, require_id, require_ref, require_uint
from .run_contracts import content_ref
from .wire import canonical_bytes
from . import assurance_authority, resources, run_outputs, semantic_conditions, transition_acceptance

_BASE = {"schema_version", "action", "request_id", "run_id", "finding_ref"}
FIELDS = {
    "finding_current": _BASE,
    "finding_start": _BASE | {"expected_state_ref"},
    "finding_request_revalidation": _BASE | {"expected_state_ref", "new_run_ref", "changed_target_ref"},
    "finding_confirm": _BASE | {"expected_state_ref"},
    "finding_recur": _BASE | {"expected_state_ref", "new_run_ref", "new_finding_ref"},
    "finding_dispose": _BASE | {"expected_state_ref", "disposition", "authority_ref", "reason_code"},
}
ACTIONS = {"finding_current": {"manager", "validator", "operator"},
           "finding_start": {"operator"}, "finding_request_revalidation": {"operator"},
           "finding_confirm": {"validator"}, "finding_recur": {"operator"}, "finding_dispose": {"manager"}}
FRESH_ACTIONS = {"finding_current"}
EVENT_KIND = "finding_management_event"


def validate_request(request):
    try:
        action = request["action"]
        require_object(request, FIELDS[action])
        if type(request["schema_version"]) is not int or request["schema_version"] != 1:
            raise ContractError()
        require_id(request["request_id"]); require_id(request["run_id"])
        for field, kind in (("finding_ref", "finding"), ("expected_state_ref", "finding_management_state"),
                            ("new_run_ref", "run_manifest"), ("changed_target_ref", "target"), ("new_finding_ref", "finding")):
            if field in request:
                require_ref(request[field])
                if request[field]["kind"] != kind: raise ContractError()
        if action == "finding_dispose":
            if type(request["disposition"]) is not str or request["disposition"] not in {"BASELINE_REVISED", "TARGET_RETIRED"}:
                raise ContractError()
            require_id(request["reason_code"]); require_ref(request["authority_ref"])
            if request["authority_ref"]["kind"] != {"BASELINE_REVISED":"baseline", "TARGET_RETIRED":"target_retirement"}[request["disposition"]]:
                raise ContractError()
    except (ContractError, KeyError, TypeError, ValueError):
        raise AdoptionError("INVALID_REQUEST") from None
    return deepcopy(request)


def _reference(state):
    return content_ref("finding_management_state", state["state_id"], state)


def _evaluation_source(db, bound):
    purpose = bound["manifest"]["purpose"]
    if purpose not in {"regression", "contract_candidate"}:
        raise AdoptionError("REGRESSION_SOURCE_REQUIRED")
    if purpose == "contract_candidate":
        rows = db.execute("SELECT * FROM transition_runs WHERE run_id=?", (bound["manifest"]["run_id"],)).fetchall()
        if len(rows) != 1 or rows[0]["side"] != "new":
            raise AdoptionError("CANDIDATE_SOURCE_REQUIRED")
def _initial(source, finding_ref, db):
    bound = source["bound"]
    _evaluation_source(db, bound)
    finding = run_outputs.fetch(db, bound, source["receipt"], finding_ref)["artifact"]
    if finding.get("status") != "OPEN" or finding.get("disposition") != "NONE":
        raise AdoptionError("SOURCE_FINDING_INVALID")
    tag = hashlib.sha256(canonical_bytes(finding_ref)).hexdigest()[:32]
    state = {"schema_version": 1, "kind": "finding_management_state", "state_id": "fm-" + tag,
        "sequence": 0, "finding_ref": deepcopy(finding_ref),
        "source_run_ref": deepcopy(source["receipt"]["manifest_ref"]),
        "status": "OPEN", "pending": None, "confirmation": None, "recurrences": [],
        "disposition": "NONE", "disposition_reason": None, "disposition_proof": None,
        "created_at": finding["created_at"], "updated_at": finding["created_at"], "ci_eligible": False}
    return state, finding


def _advance(state, request, actor_id, context, at, confirmation=None):
    if actor_id not in ACTIONS[request["action"]] or context != actor_id + "-context":
        raise AdoptionError("AUTHORITY_DENIED")
    if request["finding_ref"] != state["finding_ref"] or request["expected_state_ref"] != _reference(state):
        raise AdoptionError("FINDING_STATE_CONFLICT")
    require_uint(at)
    if at < state["updated_at"]: raise AdoptionError("TIME_ORDER")
    if state["disposition"] != "NONE": raise AdoptionError("FINDING_DISPOSED")
    updated = deepcopy(state); action = request["action"]
    if action == "finding_start":
        if state["status"] != "OPEN": raise AdoptionError("FINDING_STATE_INVALID")
        updated["status"] = "IN_PROGRESS"
    elif action == "finding_request_revalidation":
        if state["status"] != "IN_PROGRESS": raise AdoptionError("FINDING_STATE_INVALID")
        updated["status"] = "AWAITING_REVALIDATION"
        updated["pending"] = {"new_run_ref": request["new_run_ref"], "changed_target_ref": request["changed_target_ref"],
            "actor_id": actor_id, "context": context, "requested_at": at}
    elif action == "finding_confirm":
        if state["status"] != "AWAITING_REVALIDATION" or not state["pending"] or confirmation is None:
            raise AdoptionError("FINDING_STATE_INVALID")
        if state["pending"]["actor_id"] == actor_id or state["pending"]["context"] == context:
            raise AdoptionError("INDEPENDENT_VALIDATOR_REQUIRED")
        updated["status"] = "VERIFIED"; updated["confirmation"] = deepcopy(confirmation)
    elif action == "finding_dispose":
        if state["status"] == "VERIFIED" or confirmation is None: raise AdoptionError("FINDING_STATE_INVALID")
        updated["disposition"] = request["disposition"]; updated["disposition_reason"] = request["reason_code"]
        updated["disposition_proof"] = deepcopy(confirmation); updated["pending"] = None
    elif action == "finding_recur":
        if state["status"] != "VERIFIED" or confirmation is None:
            raise AdoptionError("FINDING_STATE_INVALID")
        if any(item["new_finding_ref"] == confirmation["new_finding_ref"] for item in state["recurrences"]):
            raise AdoptionError("RECURRENCE_ALREADY_LINKED")
        updated["recurrences"].append(deepcopy(confirmation))
    else:
        raise AdoptionError("INVALID_ACTION")
    updated["sequence"] += 1; updated["updated_at"] = at
    return updated


def _conditions(old, new, finding, pending, resolve_bound, db):
    new_bound = new["bound"]; old_bound = old["bound"]
    if new["receipt"]["manifest_ref"] != pending["new_run_ref"]:
        raise AdoptionError("REVALIDATION_BINDING_MISMATCH")
    _evaluation_source(db, new_bound)
    if new_bound["manifest"]["run_id"] == old_bound["manifest"]["run_id"]:
        raise AdoptionError("NEW_REGRESSION_REQUIRED")
    old_context = resolve_bound(old_bound["manifest"]["run_id"])[1]
    new_context = resolve_bound(new_bound["manifest"]["run_id"])[1]
    comparison = semantic_conditions.compare(old_bound, new_bound,
        previous_baseline_context=old_context, following_baseline_context=new_context)
    if not comparison["same_evaluation_conditions"] or comparison["changed_axes"] != ["target"]:
        raise AdoptionError("REVALIDATION_CONDITIONS_MISMATCH")
    identifier = finding["control_ref"]["id"]
    old_controls = {c["control_id"]: c for c in old_bound["registry"]["controls"]}
    new_controls = {c["control_id"]: c for c in new_bound["registry"]["controls"]}
    if (identifier not in new_bound["selected_controls"] or identifier not in old_controls or identifier not in new_controls
            or new_controls[identifier]["target_ref"] != pending["changed_target_ref"]
            or old_controls[identifier]["target_ref"]["id"] != pending["changed_target_ref"]["id"]
            or old_controls[identifier]["target_ref"]["digest"] == pending["changed_target_ref"]["digest"]):
        raise AdoptionError("CHANGED_TARGET_MISMATCH")
    return comparison


def _proof(db, old, finding, pending, now, resolve_source, resolve_bound, *, fresh):
    new = resolve_source(pending["new_run_ref"]["id"])
    comparison = _conditions(old, new, finding, pending, resolve_bound, db)
    if fresh:
        transition_acceptance._check_source(db, new, new["bound"], now, allow_unhealthy=True)
    evidence = new["evidences"][0]
    if not pending["requested_at"] <= evidence["observed_at"] <= new["receipt"]["created_at"] <= now:
        raise AdoptionError("NEW_EVIDENCE_REQUIRED")
    if evidence["valid_until"] < now or evidence["retention_until"] < now:
        raise AdoptionError("SOURCE_EXPIRED")
    items, _ = run_outputs._build(db, new["bound"], new["receipt"])
    run_outputs.read(db, new["bound"], new["receipt"])
    if any(ref["kind"] == "finding" and value.get("control_ref", {}).get("id") == finding["control_ref"]["id"]
           and "status" in value for ref, value in items):
        raise AdoptionError("REVALIDATION_NOT_CLEAR")
    return {"new_run_ref": deepcopy(new["receipt"]["manifest_ref"]),
        "evidence_ref": deepcopy(new["receipt"]["evidence_ref"]),
        "decision_ref": deepcopy(new["receipt"]["decision_ref"]),
        "conditions_ref": comparison["following"]["conditions_ref"],
        "changed_target_ref": deepcopy(pending["changed_target_ref"]),
        "observed_at": evidence["observed_at"], "confirmed_at": now}


def _deleted_confirmation(db, old, finding, pending, saved, confirmed_at, now, resolve_bound):
    # 正規の削除記録から既存確認の参照だけを検査する。新しい有効なEvidenceは生成しない。
    from . import evidence_retention
    require_object(saved, {"new_run_ref", "evidence_ref", "decision_ref", "conditions_ref", "changed_target_ref", "observed_at", "confirmed_at"})
    run_id = pending["new_run_ref"]["id"]
    bound, _ = resolve_bound(run_id)
    receipt = evidence_retention._receipt(db, run_id, saved["evidence_ref"])
    deleted = evidence_retention.tombstone(db, run_id, saved["evidence_ref"])
    if deleted is None: raise AdoptionError("EVIDENCE_MISSING")
    tombstone, _ = deleted; metadata = tombstone["metadata"]
    if not confirmed_at <= tombstone["recorded_at"] <= now: raise AdoptionError("TIME_ORDER")
    manifest = bound["manifest"]
    if (metadata["subject_ref"] != content_ref("run_manifest", run_id, manifest)
            or metadata["conditions_ref"] != content_ref("bound_bundle", run_id, bound)
            or metadata["contract_ref"] != manifest["contract_ref"] or metadata["baseline_ref"] != manifest["baseline_ref"]
            or metadata["target_refs"] != manifest["target_refs"] or metadata["use_cases"] != manifest["use_cases"]
            or metadata["control_ids"] != manifest["control_ids"] or metadata["policy_ref"] != manifest["policy_ref"]
            or receipt["manifest_ref"] != metadata["subject_ref"]):
        raise AdoptionError("CONFIRMATION_MISMATCH")
    new = {"bound":bound,"receipt":receipt}
    comparison = _conditions(old, new, finding, pending, resolve_bound, db)
    observed = metadata["observed_at"]
    if not pending["requested_at"] <= observed <= receipt["created_at"] <= confirmed_at <= min(metadata["valid_until"],metadata["retention_until"]):
        raise AdoptionError("NEW_EVIDENCE_REQUIRED")
    # 保存されたDecisionとFinding集合も当時の確認と整合しなければならない。
    items, _ = run_outputs._build(db, bound, receipt)
    run_outputs.read(db, bound, receipt)
    if any(ref["kind"] == "finding" and value.get("control_ref", {}).get("id") == finding["control_ref"]["id"] and "status" in value for ref,value in items):
        raise AdoptionError("REVALIDATION_NOT_CLEAR")
    return {"new_run_ref":deepcopy(receipt["manifest_ref"]),"evidence_ref":deepcopy(receipt["evidence_ref"]),
        "decision_ref":deepcopy(receipt["decision_ref"]),"conditions_ref":comparison["following"]["conditions_ref"],
        "changed_target_ref":deepcopy(pending["changed_target_ref"]),"observed_at":observed,"confirmed_at":confirmed_at}


def _recurrence(db, old, finding, state, request, now, resolve_source, resolve_bound, *, fresh):
    if state["status"] != "VERIFIED" or state["confirmation"] is None:
        raise AdoptionError("FINDING_STATE_INVALID")
    new = resolve_source(request["new_run_ref"]["id"])
    if new["receipt"]["manifest_ref"] != request["new_run_ref"] or new["bound"]["manifest"]["run_id"] == old["bound"]["manifest"]["run_id"]:
        raise AdoptionError("RECURRENCE_BINDING_MISMATCH")
    _, item = _initial(new, request["new_finding_ref"], db)
    if any(item[k] != finding[k] for k in ("reason_code", "metric_id")) or item["control_ref"]["id"] != finding["control_ref"]["id"]:
        raise AdoptionError("RECURRENCE_FINDING_MISMATCH")
    before = resolve_bound(old["bound"]["manifest"]["run_id"])[1]
    after = resolve_bound(new["bound"]["manifest"]["run_id"])[1]
    conditions = semantic_conditions.compare(old["bound"], new["bound"], previous_baseline_context=before, following_baseline_context=after)
    if not conditions["same_evaluation_conditions"] or any(axis != "target" for axis in conditions["changed_axes"]):
        raise AdoptionError("REVALIDATION_CONDITIONS_MISMATCH")
    control_id = finding["control_ref"]["id"]
    target = lambda bound: next(c["target_ref"] for c in bound["registry"]["controls"] if c["control_id"] == control_id)
    if target(old["bound"])["id"] != target(new["bound"])["id"]:
        raise AdoptionError("CHANGED_TARGET_MISMATCH")
    if fresh:
        transition_acceptance._check_source(db, new, new["bound"], now, allow_unhealthy=True)
    if not state["confirmation"]["confirmed_at"] < new["evidences"][0]["observed_at"] <= now:
        raise AdoptionError("NEW_EVIDENCE_REQUIRED")
    return {"parent_finding_ref": deepcopy(state["finding_ref"]),
        "new_finding_ref": deepcopy(request["new_finding_ref"]), "new_run_ref": deepcopy(request["new_run_ref"]),
        "evidence_ref": deepcopy(new["receipt"]["evidence_ref"]), "linked_at": now}


def _load(db, source, finding_ref, now, resolve_source, resolve_bound, store=None):
    state, finding = _initial(source, finding_ref, db)
    prefix = state["state_id"] + "-"
    rows = list(db.execute("SELECT * FROM authority_artifacts WHERE kind=? AND run_id=? AND id LIKE ? ORDER BY id",
        (EVENT_KIND, source["bound"]["manifest"]["run_id"], prefix + "%")))
    if len(rows) > 1000: raise AdoptionError("FINDING_HISTORY_LIMIT")
    last_generation = None
    for row in rows:
        event = resources._unpack(row["payload_json"], row["digest"])
        require_object(event, {"kind", "event_id", "request", "actor_id", "context", "permission_generation", "recorded_at", "state"})
        request = validate_request(event["request"])
        if request["action"] == "finding_current": raise AdoptionError("FINDING_HISTORY_INVALID")
        require_uint(event["permission_generation"]); require_uint(event["recorded_at"])
        if event["recorded_at"] > now or event["kind"] != EVENT_KIND:
            raise AdoptionError("FINDING_HISTORY_INVALID")
        identifier = prefix + str(state["sequence"] + 1).zfill(6)
        if row["id"] != identifier or event["event_id"] != identifier or request["run_id"] != source["bound"]["manifest"]["run_id"]:
            raise AdoptionError("FINDING_HISTORY_INVALID")
        origin = db.execute("SELECT * FROM idempotency WHERE request_id=?", (request["request_id"],)).fetchone()
        if (origin is None or origin["actor_id"] != event["actor_id"] or origin["context"] != event["context"]
                or origin["request_digest"] != hashlib.sha256(canonical_bytes(request)).hexdigest()):
            raise AdoptionError("FINDING_ORIGIN_INVALID")
        confirmation = None
        if request["action"] == "finding_confirm":
            try:
                confirmation = _proof(db, source, finding, state["pending"], event["recorded_at"], resolve_source, resolve_bound, fresh=False)
            except AdoptionError as error:
                if error.code != "EVIDENCE_DELETED": raise
                confirmation = _deleted_confirmation(db, source, finding, state["pending"], event["state"]["confirmation"], event["recorded_at"], now, resolve_bound)
        if request["action"] == "finding_recur":
            confirmation = _recurrence(db, source, finding, state, request, event["recorded_at"], resolve_source, resolve_bound, fresh=False)
        if request["action"] == "finding_dispose":
            from . import finding_dispositions
            confirmation = finding_dispositions.proof(store, db, source, finding, request, event["recorded_at"], resolve_source, fresh=False)
        state = _advance(state, request, event["actor_id"], event["context"], event["recorded_at"], confirmation)
        response = resources._unpack(origin["response_json"], origin["response_digest"])
        if (event["state"] != state or response.get("state") != state or response.get("state_ref") != _reference(state)
                or type(response.get("schema_version")) is not int or response["schema_version"] != 1
                or response.get("kind") != "evaluation_authority_result" or response.get("ci_eligible") is not False
                or response.get("action") != request["action"] or response.get("request_id") != request["request_id"]):
            raise AdoptionError("FINDING_HISTORY_INVALID")
        last_generation = event["permission_generation"]
    return state, finding, last_generation


def execute(store, db, request, actor_id, context, now, resolve_source, resolve_bound):
    request = validate_request(request)
    source = resolve_source(request["run_id"])
    state, finding, generation = _load(db, source, request["finding_ref"], now, resolve_source, resolve_bound, store)
    if request["action"] == "finding_current":
        current = False; reasons = []
        if state["status"] == "VERIFIED":
            try:
                if generation != store._permission_generation(db) or store._actor_revoked(db, "validator"):
                    raise AdoptionError("VERIFIER_AUTHORITY_STALE")
                proof = _proof(db, source, finding, state["pending"], now, resolve_source, resolve_bound, fresh=True)
                current = all(proof[k] == state["confirmation"][k] for k in proof if k != "confirmed_at")
                if not current: raise AdoptionError("CONFIRMATION_MISMATCH")
            except (AdoptionError, ContractError, resources.ResourceError) as error:
                reasons.append(getattr(error, "code", "VERIFICATION_UNAVAILABLE"))
        return {"state": state, "state_ref": _reference(state), "verification_current": current,
                "reasons": reasons, "checked_at": now, "ci_eligible": False}
    if request["expected_state_ref"] != _reference(state): raise AdoptionError("FINDING_STATE_CONFLICT")
    proof = None
    if request["action"] == "finding_request_revalidation":
        new_bound, _ = resolve_bound(request["new_run_ref"]["id"])
        new = {"bound": new_bound, "receipt": {"manifest_ref": content_ref("run_manifest", new_bound["manifest"]["run_id"], new_bound["manifest"])}}
        _conditions(source, new, finding, request, resolve_bound, db)
        if db.execute("SELECT 1 FROM attempts WHERE run_id=?", (request["new_run_ref"]["id"],)).fetchone() or db.execute("SELECT 1 FROM authority_run_receipts WHERE run_id=?", (request["new_run_ref"]["id"],)).fetchone():
            raise AdoptionError("NEW_EVIDENCE_REQUIRED")
    if request["action"] == "finding_confirm":
        if state["status"] != "AWAITING_REVALIDATION" or state["pending"] is None:
            raise AdoptionError("FINDING_STATE_INVALID")
        proof = _proof(db, source, finding, state["pending"], now, resolve_source, resolve_bound, fresh=True)
    if request["action"] == "finding_recur":
        proof = _recurrence(db, source, finding, state, request, now, resolve_source, resolve_bound, fresh=True)
    if request["action"] == "finding_dispose":
        from . import finding_dispositions
        proof = finding_dispositions.proof(store, db, source, finding, request, now, resolve_source, fresh=True)
    updated = _advance(state, request, actor_id, context, now, proof)
    identifier = state["state_id"] + "-" + str(updated["sequence"]).zfill(6)
    if db.execute("SELECT 1 FROM authority_artifacts WHERE kind=? AND id=?", (EVENT_KIND, identifier)).fetchone():
        raise AdoptionError("FINDING_STATE_CONFLICT")
    event = {"kind": EVENT_KIND, "event_id": identifier, "request": request, "actor_id": actor_id,
        "context": context, "permission_generation": store._permission_generation(db), "recorded_at": now, "state": updated}
    assurance_authority._save(db, request["run_id"], EVENT_KIND, identifier, event)
    return {"state": updated, "state_ref": _reference(updated), "ci_eligible": False}
