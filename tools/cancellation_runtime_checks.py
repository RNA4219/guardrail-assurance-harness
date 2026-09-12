"""固定fixture一件を停止し、取消し確定・後日精算・CI終了コード3を検査する。"""
import io
import time
from types import SimpleNamespace

from gah.run_contracts import content_ref
from tools.gah_ci import run as consume_ci


def verify(*, success, call, denied, check, runtime, runner, contract, receipts, active, save_observations):
    def request(action, identifier, **fields):
        return {"schema_version": 1, "action": action, "request_id": identifier, **fields}

    run_id = "cancellation-runtime"
    prepared = success(12004, request("run_prepare", "cancellation-prepare", run_id=run_id,
        contract_series_id="fixture-contract-series",
        expected_contract_ref=content_ref("evaluation_contract", contract["contract_id"], contract)))
    bound = prepared["bound_run"]
    manifest = bound["manifest"]
    success(12004, request("run_begin", "cancellation-begin", manifest=manifest, plan=bound["plan"],
        contract_series_id="fixture-contract-series"))
    owner = {"run_id": run_id, "owner_id": "cancellation-begin", "owner_epoch": 1}
    success(12004, request("evidence_open", "cancellation-open", run_id=run_id))
    gate = request("ci_check", "cancellation-gate", run_id=run_id,
        expected_manifest_ref=content_ref("run_manifest", run_id, manifest),
        expected_contract_ref=manifest["contract_ref"], expected_baseline_ref=manifest["baseline_ref"],
        expected_target_refs=manifest["target_refs"], expected_use_cases=manifest["use_cases"])

    def gate_result(code):
        value = call(12004, gate)
        from tools.gah_ci import response_exit_code
        if response_exit_code(gate, value) != code:
            raise AssertionError("CANCELLATION_GATE_INVALID")
        return value

    record = prepared["materialization"]["manifest"]["records"][0]
    entry = next(e for e in bound["plan"]["entries"] if all(e[k] == record[k]
        for k in ("obligation_id", "case_id", "trial_id", "variant")))
    op = "cancellation-operation"
    success(12004, request("resource_reserve", "cancellation-reserve", **owner, operation_id=op,
        entry={k: entry[k] for k in ("obligation_id", "case_id", "trial_id", "variant")}, scenario=record["scenario"]))
    success(12004, request("resource_dispatch", "cancellation-dispatch", **owner, operation_id=op))
    binding = {"run_id": run_id, "operation_id": op, "owner_epoch": 1,
        "contract_digest": manifest["contract_ref"]["digest"], "target_digest": entry["target_ref"]["digest"],
        **{k: entry[k] for k in ("obligation_id", "case_id", "trial_id")}, "stage_id": entry["stage_ids"][0],
        "fixture_digest": runner.lock["worker_digest"], "adapter_digest": runner.adapter_digest,
        "policy_digest": manifest["policy_ref"]["digest"], "evaluator_digest": entry["evaluator_ref"]["digest"],
        "isolation_digest": runner.isolation_digest}
    active.append({"run_id": run_id, "operation_id": op})
    execution = runner.run(record["scenario"], binding, run_deadline=manifest["deadline"], timeout_seconds=120)
    receipts.append(execution)
    save_observations()
    check("cancellation_fixture_completed_stopped_and_cleaned", execution.get("execution_status") == "COMPLETED"
        and execution.get("ci_eligible") is False
        and all(execution.get(k) is True for k in ("stop_confirmed", "cleanup_confirmed", "isolation_config_verified"))
        and execution.get("normalized_result", {}).get("binding") == binding
        and execution.get("normalized_result", {}).get("observation") == "PASS")
    success(12004, request("resource_cancel", "cancellation-cancel", **owner))
    finalize = request("run_cancel_finalize", "cancellation-finalize", run_id=run_id)
    check("cancellation_requires_stop_observation", denied(12004, finalize, "STOP_UNCONFIRMED"))
    check("cancellation_pending_is_incomplete", gate_result(2)["execution_status"] == "FAILED")
    success(12003, request("resource_observe", "cancellation-stop-observe", run_id=run_id, operation_id=op,
        event_id="cancellation-stop", stopped=True, usage=None))
    cancelled = success(12004, finalize)
    check("cancellation_terminal_does_not_require_settlement", cancelled.get("execution_status") == "CANCELLED"
        and cancelled.get("resource_stop_verified") is True and cancelled.get("budget_closure") is False
        and cancelled.get("assurance") == "UNKNOWN" and cancelled.get("purpose") == "regression")
    outputs_request = request("run_outputs", "cancellation-outputs", run_id=run_id)
    outputs = success(12004, outputs_request)
    check("cancellation_receipt_has_own_kind", outputs["outputs"]["run_receipt"]["kind"] == "authority_cancel_receipt")
    for field in ("decision", "evidence", "findings", "plans", "run_receipt"):
        ref = outputs["outputs"][field]
        artifact = success(12004, request("run_artifact", "cancellation-read-" + field, run_id=run_id, artifact_ref=ref))
        check("cancellation_" + field + "_retrievable", content_ref(ref["kind"], ref["id"], artifact["artifact"]) == ref)
    pending = gate_result(3)
    check("cancellation_ci_reports_budget_open", "BUDGET_OPEN" in pending["reasons"])
    check("cancellation_consumer_returns_three", consume_ci(SimpleNamespace(client=call), gate, io.StringIO()) == 3)
    runtime.restart_broker()
    check("cancellation_ci_rechecks_after_restart", gate_result(3)["outputs_ref"] == pending["outputs_ref"])
    check("cancellation_receipt_survives_restart", success(12004, finalize) == cancelled)
    success(12003, request("resource_observe", "cancellation-usage-observe", run_id=run_id, operation_id=op,
        event_id="cancellation-usage", stopped=True, usage={"input_tokens": 0, "output_tokens": 0, "cost_usd": "0"}))
    recovery = success(12004, request("resource_claim", "cancellation-recovery-claim", run_id=run_id,
        owner_id=owner["owner_id"], recovery=True))
    owner["owner_epoch"] = recovery["owner_epoch"]
    closed = success(12004, request("resource_close", "cancellation-close", **owner))
    check("cancellation_late_settlement_closes_budget", closed["closed"] is True and closed["budget_closure"] is True
        and closed["resources"]["slots"] == closed["resources"]["unsettled"] == 0
        and closed["resources"]["case_trial_executions"] == 1)
    check("cancellation_ci_stays_three_after_settlement", "BUDGET_OPEN" not in gate_result(3)["reasons"])
    check("cancellation_late_settlement_keeps_receipt_and_outputs", success(12004, finalize) == cancelled
        and success(12004, outputs_request) == outputs)
    repeated = success(12004, request("resource_cancel", "cancellation-repeat", **owner))
    check("cancellation_repeat_is_already_terminal", repeated.get("already_terminal") is True and repeated.get("cancelled") is True)
    normal = success(12004, request("resource_cancel", "normal-repeat", run_id="regression-runtime",
        owner_id="regression-begin", owner_epoch=1))
    check("cancellation_after_normal_terminal_keeps_normal_state", normal.get("already_terminal") is True and normal.get("cancelled") is False)

    def after_revocation():
        check("cancellation_source_revocation_cannot_turn_into_success", gate_result(3)["ci_eligible"] is False)
        check("cancellation_history_after_source_revocation", success(12004, finalize) == cancelled
            and success(12004, outputs_request) == outputs)
    return after_revocation
