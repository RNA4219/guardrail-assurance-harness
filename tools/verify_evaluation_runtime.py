"""明示実行専用。合成参照の契約採択と固定fixtureの予約から精算まで実測する。"""
import argparse
import copy
import hashlib
import json
import re
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gah.docker_runner import DockerRunner
from gah.execution_journal import ExecutionJournal
from gah.policy import initial_policy_profile
from tools.authority_runtime import AuthorityRuntime
from tools.evaluation_fixture import documents, manifest


def request(action, request_id, **fields):
    return {"schema_version": 1, "action": action, "request_id": request_id, **fields}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    folder = Path(args.output).resolve()
    if not folder.is_relative_to(ROOT):
        raise SystemExit("OUTPUT_OUTSIDE_WORKSPACE")
    folder.mkdir(parents=True, exist_ok=False)
    runtime = AuthorityRuntime(folder)
    runner = DockerRunner(ROOT / "config/fixture-runtime.lock.json", folder / "execution.sqlite")
    extra = ("tools/authority_runtime.py", "tools/prepare_authority_runtime.py", "tools/verify_evaluation_runtime.py",
             "tools/evaluation_fixture.py", "src/gah/docker_runner.py", "src/gah/execution_journal.py", "src/gah/normalized.py",
             "fixtures/runtime/fixture_worker.py")
    sources = {**runtime.lock["source_sha256"], **{path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in extra}}
    checks, observations, receipts = {}, [], []
    def save():
        (folder / "observations.json").write_text(json.dumps(observations, indent=2) + "\n", encoding="utf-8")
        (folder / "execution-receipts.json").write_text(json.dumps(receipts, indent=2) + "\n", encoding="utf-8")
    def check(name, passed):
        checks[name] = bool(passed)
        print(json.dumps({"check": name, "passed": bool(passed)}), flush=True)
        if not passed:
            raise AssertionError(name)
    def call(uid, value):
        result = runtime.client(uid, value)
        observations.append({"uid": uid, "action": value["action"], "response": result})
        save()
        return result
    def denied(uid, value, reason):
        result = call(uid, value)
        return result.get("kind") == "authority_error" and result.get("reason") == reason
    def required(uid, value):
        result = call(uid, value)
        if result.get("action") != value["action"]:
            raise AssertionError(value["action"])
        return result
    failure = None
    failure_code = None
    failure_detail = None
    active_binding = None
    try:
        runtime.prepare()
        policy = initial_policy_profile()
        required(12001, request("propose", "policy-propose", proposal_id="policy-proposal", series_id=policy["policy_id"], expected_generation=0, policy=policy))
        required(12003, request("validate", "policy-validate", proposal_id="policy-proposal", validation_id="policy-validation"))
        required(12001, request("adopt", "policy-adopt", proposal_id="policy-proposal", validation_id="policy-validation", expected_generation=0))
        scenario = "constraint:C01:good"
        target = {"kind": "target", "id": "fixed-target", "digest": runner.target_digest(scenario)}
        evaluator = {"kind": "evaluator", "id": "fixed-evaluator", "digest": runner.lock["worker_digest"]}
        registry, acceptance, calibration, contract, plan = documents(policy, target, evaluator)
        for index, document in enumerate((registry, acceptance, calibration)):
            required(12001, request("object_register", f"object-{index}", document=document))
        check("registry_and_synthetic_reference_sets_registered", len(acceptance["cases"]) == 400)
        proposal = request("contract_propose", "contract-propose", proposal_id="contract-proposal", series_id="evaluation-main", expected_generation=0, contract=contract)
        check("candidate_contract_proposal_denied", denied(12002, proposal, "AUTHORITY_DENIED"))
        required(12001, proposal)
        validation = request("contract_validate", "contract-validate", proposal_id="contract-proposal", validation_id="contract-validation")
        check("missing_calibration_denies_adoption_validation", denied(12003, validation, "CALIBRATION_UNAVAILABLE"))
        calibration_request = request("calibration_record", "calibration-record", calibration_id="calibration-1", case_set_ref=contract["calibration_case_set_ref"], evaluator_ref=evaluator,
            observations=[{"case_id": case["case_id"], "stage_id": case["scored_stage_id"], "detection": case["session_steps"][0]["expected_detection"]} for case in calibration["cases"]])
        check("manager_self_calibration_denied", denied(12001, calibration_request, "AUTHORITY_DENIED"))
        check("validator_synthetic_calibration_computed", required(12003, calibration_request).get("passed") is True)
        check("contract_independently_validated", required(12003, validation).get("passed") is True)
        adoption = request("contract_adopt", "contract-adopt", proposal_id="contract-proposal", validation_id="contract-validation", expected_generation=0)
        adopted = required(12001, adoption)
        check("initial_contract_adopted", adopted.get("generation") == 1 and adopted.get("ci_eligible") is False)
        current_request = request("contract_current", "contract-current", series_id="evaluation-main")
        check("adopted_contract_current_valid", required(12004, current_request).get("valid") is True)
        runtime.restart_broker()
        check("restart_preserves_adoption_receipt", call(12001, adoption) == adopted)
        environment_ref = {"kind": "environment", "id": "fixed-docker-profile", "digest": runner.isolation_digest}
        run_manifest = manifest(contract, plan, "connected-fixture-run", int(time.time()), environment_ref)
        bad = copy.deepcopy(run_manifest)
        bad["run_id"] = "rejected-run"
        bad["plan_ref"]["digest"] = "0" * 64
        bad_start = call(12004, request("run_begin", "rejected-start", manifest=bad, plan=plan, contract_series_id="evaluation-main"))
        check("bad_binding_has_no_run", bad_start.get("kind") == "authority_error" and denied(12004, request("run_status", "rejected-status", run_id="rejected-run"), "RUN_MISSING"))
        begin = request("run_begin", "supervisor-1", manifest=run_manifest, plan=plan, contract_series_id="evaluation-main")
        check("candidate_run_begin_denied", denied(12002, begin, "AUTHORITY_DENIED"))
        begun = required(12004, begin)
        check("run_and_resources_created", begun["resource_snapshot"]["manifest_digest"] == hashlib.sha256(json.dumps(run_manifest, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest())
        run_id = run_manifest["run_id"]
        evidence_open = request("evidence_open", "evidence-open", run_id=run_id)
        check("candidate_cannot_open_evidence", denied(12002, evidence_open, "AUTHORITY_DENIED"))
        evidence_started = required(12004, evidence_open)
        check("same_db_evidence_opened", evidence_started.get("ci_eligible") is False and bool(evidence_started.get("bundle_digest")))
        owner = {"run_id": run_id, "owner_id": "supervisor-1", "owner_epoch": 1}
        entry = {key: plan["entries"][0][key] for key in ("obligation_id", "case_id", "trial_id", "variant")}
        reserve = request("resource_reserve", "reserve-1", **owner, operation_id="connected-operation", entry=entry, scenario=scenario)
        check("planned_trial_reserved", required(12004, reserve).get("dispatch_allowed") is True)
        duplicate = {**reserve, "request_id": "reserve-other", "operation_id": "other-operation"}
        check("new_id_cannot_repeat_planned_trial", denied(12004, duplicate, "ENTRY_ALREADY_RESERVED"))
        dispatch = request("resource_dispatch", "dispatch-1", **owner, operation_id="connected-operation")
        check("dispatch_intent_saved", required(12004, dispatch).get("state") == "DISPATCHING")
        check("same_request_cannot_authorize_redispatch", denied(12004, dispatch, "DISPATCH_NOT_PROVABLY_NEW"))
        active_binding = {"run_id": run_id, "operation_id": "connected-operation", "owner_epoch": 1,
            "contract_digest": plan["contract_ref"]["digest"], "target_digest": target["digest"], "obligation_id": entry["obligation_id"],
            "case_id": entry["case_id"], "trial_id": entry["trial_id"], "stage_id": "fixture-acceptance", "fixture_digest": runner.lock["worker_digest"],
            "adapter_digest": runner.adapter_digest, "policy_digest": contract["policy_ref"]["digest"], "evaluator_digest": evaluator["digest"], "isolation_digest": runner.isolation_digest}
        attempt_started_at = int(time.time())
        receipt = runner.run(scenario, active_binding, run_deadline=run_manifest["deadline"], timeout_seconds=60)
        attempt_finished_at = int(time.time())
        receipts.append(receipt)
        save()
        check("bound_fixed_fixture_executed_and_removed", receipt["execution_status"] == "COMPLETED" and receipt["stop_confirmed"] and receipt["cleanup_confirmed"] and receipt["isolation_config_verified"] and receipt["ci_eligible"] is False and receipt["normalized_result"]["observation"] == "PASS")
        stop = request("resource_observe", "observe-stop", run_id=run_id, operation_id="connected-operation", event_id="stop-event", stopped=receipt["stop_confirmed"], usage=None)
        check("candidate_usage_claim_denied", denied(12002, stop, "AUTHORITY_DENIED"))
        check("authenticated_stop_observed", required(12003, stop).get("accepted") is True)
        status_request = request("run_status", "status-1", run_id=run_id)
        status = required(12004, status_request)["resource_snapshot"]
        check("stopped_but_unknown_usage_keeps_reservation", status["resources"]["slots"] == 0 and status["resources"]["unsettled"] == 1 and status["budget_closure"] is False)
        check("unknown_usage_cannot_finalize_evidence", denied(12004,
            request("evidence_finalize", "unsettled-finalize", run_id=run_id), "RESOURCE_CLOSURE_REQUIRED"))
        usage = {"input_tokens": 0, "output_tokens": 0, "cost_usd": "0"}
        settlement = request("resource_observe", "observe-usage", run_id=run_id, operation_id="connected-operation", event_id="usage-event", stopped=True, usage=usage)
        settled = required(12003, settlement)
        check("usage_replay_is_immutable", call(12003, settlement) == settled)
        check("reserve_replay_does_not_restore_permission", required(12004, reserve).get("dispatch_allowed") is False)
        required(12004, request("resource_claim", "renew-1", run_id=run_id, owner_id="supervisor-1", recovery=False))
        closed = required(12004, request("resource_close", "close-1", **owner))
        check("accounting_complete_run_closed", closed.get("closed") is True and closed.get("budget_closure") is True and closed["resources"]["case_trial_executions"] == 1 and closed["resources"]["model_calls"] == 0)
        attempt = {"schema_version": 1, "kind": "attempt_record", "attempt_id": "connected-attempt",
            "variant": "candidate", "retry_of": None, "started_at": attempt_started_at,
            "finished_at": attempt_finished_at, "stop_confirmed": receipt["stop_confirmed"],
            "execution_status": receipt["execution_status"], "state_restored": receipt["cleanup_confirmed"],
            "expected_binding": active_binding, "result": receipt["normalized_result"]}
        record_request = request("evidence_record", "evidence-record", run_id=run_id, attempt=attempt)
        check("candidate_cannot_record_observation", denied(12002, record_request, "AUTHORITY_DENIED"))
        check("validator_records_real_docker_result", required(12003, record_request).get("accepted") is True)
        finalized = required(12004, request("evidence_finalize", "evidence-finalize", run_id=run_id))
        check("evidence_receipt_has_resource_and_origin_binding", finalized.get("authority_connected") is True
            and finalized.get("resource_closure_verified") is True and finalized.get("input_materialization_verified") is False
            and finalized.get("ci_eligible") is False)
        use_request = request("evidence_current", "evidence-current", run_id=run_id,
            expected_bundle_digest=evidence_started["bundle_digest"])
        use = required(12004, use_request)
        check("transport_references_cannot_enable_ci", use.get("use") is False
            and "INPUT_MATERIALIZATION_UNVERIFIED" in use.get("reasons", []))
        check("closed_run_cannot_restart", denied(12004, request("resource_claim", "renew-2", run_id=run_id, owner_id="supervisor-1", recovery=False), "RUN_CLOSED"))
        required(12004, request("revoke_validation", "revoke-policy", validation_id="policy-validation"))
        check("same_current_request_rechecks_policy_revocation", required(12004, current_request).get("valid") is False)
        check("historical_adoption_does_not_restore_validity", call(12001, adoption) == adopted)
        replay = required(12004, request("evidence_finalize", "evidence-finalize-again", run_id=run_id))
        check("evidence_receipt_survives_policy_revocation", {k:v for k,v in replay.items() if k not in {"request_id", "action"}}
            == {k:v for k,v in finalized.items() if k not in {"request_id", "action"}})
        use = required(12004, use_request)
        check("evidence_current_rechecks_revoked_conditions", use.get("use") is False
            and "ADOPTED_CONDITIONS_UNAVAILABLE" in use.get("reasons", []))
        required(12004, request("evidence_revoke", "evidence-revoke", run_id=run_id))
        check("evidence_revocation_is_current", "EVIDENCE_REVOKED" in required(12004, use_request).get("reasons", []))
        next_manifest = manifest(contract, plan, "revoked-start-run", int(time.time()), environment_ref)
        check("revoked_policy_denies_new_run", denied(12004, request("run_begin", "revoked-start", manifest=next_manifest, plan=plan, contract_series_id="evaluation-main"), "PREREQUISITE_UNAVAILABLE"))
        with ExecutionJournal(folder / "execution.sqlite") as journal:
            check("execution_journal_has_no_pending_operations", not journal.pending())
    except Exception as error:
        failure = type(error).__name__
        if error.args and type(error.args[0]) is str and re.fullmatch(r"[A-Z_]{1,64}", error.args[0]):
            failure_code = error.args[0]
        failure_detail = getattr(error, "detail", None)
    finally:
        if active_binding is not None:
            try:
                recovered = runner.recover(active_binding["run_id"], active_binding["operation_id"])
            except Exception:
                checks["fixture_cleanup_rechecked"] = False
            else:
                checks["fixture_cleanup_rechecked"] = recovered.get("stop_confirmed") is True and recovered.get("cleanup_confirmed") is True
        try:
            runtime.cleanup(remove_state=False)
            checks["owned_authority_containers_removed"] = True
        except Exception:
            checks["owned_authority_containers_removed"] = False
    checks["tested_sources_unchanged"] = all(hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == value for path, value in sources.items())
    record = {"schema_version": 1, "passed": failure is None and bool(checks) and all(checks.values()), "checks": checks,
        "failure_type": failure, "failure_code": failure_code, "failure_detail": failure_detail,
        "source_sha256": sources, "image_id": runtime.lock["image_id"], "fixture_image_id": runner.lock["image_id"],
        "synthetic_reference_fixture_only": True, "real_400_case_evaluation": False, "full_mvp_accepted": False,
        "ci_eligible": False, "persistent_state_retained": True}
    (folder / "check.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": record["passed"], "count": len(checks), "failure_type": failure}), flush=True)
    return 0 if record["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
