"""実時計のlease失効と根拠撤回後の取消し取得を固定fixtureで検査する。"""
import time
from gah.run_contracts import content_ref
from tools.gah_ci import response_exit_code


def verify(*, success, call, denied, check, runtime, runner, contract, receipts, active, save_observations):
    def req(action, name, **fields):
        return {"schema_version": 1, "action": action, "request_id": "recovery-" + name, **fields}
    run_id = "recovery-runtime"
    prepared = success(12004, req("run_prepare", "prepare", run_id=run_id,
        contract_series_id="fixture-contract-series",
        expected_contract_ref=content_ref("evaluation_contract", contract["contract_id"], contract)))
    bound = prepared["bound_run"]
    manifest = bound["manifest"]
    success(12004, req("run_begin", "begin", manifest=manifest, plan=bound["plan"],
        contract_series_id="fixture-contract-series"))
    owner = {"run_id": run_id, "owner_id": "recovery-begin", "owner_epoch": 1}
    success(12004, req("evidence_open", "open", run_id=run_id))
    claim = req("resource_cancel_claim", "cancel-claim", run_id=run_id, owner_id="recovery-owner")
    for uid in (12001, 12002, 12003):
        check("recovery_claim_role_" + str(uid), denied(uid, claim, "AUTHORITY_DENIED"))
    check("recovery_active_owner_not_stolen", denied(12004, claim, "OWNER_ACTIVE"))
    record = prepared["materialization"]["manifest"]["records"][0]
    entry = next(e for e in bound["plan"]["entries"] if all(e[k] == record[k]
        for k in ("obligation_id", "case_id", "trial_id", "variant")))
    op = "recovery-operation"
    success(12004, req("resource_reserve", "reserve", **owner, operation_id=op,
        entry={k: entry[k] for k in ("obligation_id", "case_id", "trial_id", "variant")}, scenario=record["scenario"]))
    success(12004, req("resource_dispatch", "dispatch", **owner, operation_id=op))
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
    check("recovery_fixture_stopped_and_cleaned", execution.get("execution_status") == "COMPLETED"
        and execution.get("ci_eligible") is False
        and all(execution.get(k) is True for k in ("stop_confirmed", "cleanup_confirmed", "isolation_config_verified"))
        and execution.get("normalized_result", {}).get("binding") == binding)
    lease = success(12004, req("resource_claim", "lease", run_id=run_id, owner_id=owner["owner_id"], recovery=False))
    owner["owner_epoch"] = lease["owner_epoch"]
    gate = req("ci_check", "gate", run_id=run_id,
        expected_manifest_ref=content_ref("run_manifest", run_id, manifest),
        expected_contract_ref=manifest["contract_ref"], expected_baseline_ref=manifest["baseline_ref"],
        expected_target_refs=manifest["target_refs"], expected_use_cases=manifest["use_cases"])
    def gate_at(code):
        value = call(12004, gate)
        if response_exit_code(gate, value) != code:
            raise AssertionError("RECOVERY_GATE_INVALID")
        return value

    def after_revocation():
        # 実時計が返却されたlease期限に達するまで待つ。broker時計は変更しない。
        wait_deadline = time.monotonic() + 65
        while int(time.time()) < lease["lease_until"]:
            if time.monotonic() >= wait_deadline:
                raise AssertionError("LEASE_CLOCK_UNAVAILABLE")
            time.sleep(0.5)
        normal = call(12004, req("resource_claim", "normal-claim", run_id=run_id, owner_id="recovery-owner", recovery=False))
        check("recovery_revoked_source_denies_start", normal.get("kind") == "authority_error")
        prior = call(12004, req("resource_claim", "old-recovery",
            run_id=run_id, owner_id="recovery-owner", recovery=True))
        expected_epoch = lease["owner_epoch"] + 1
        if prior.get("kind") == "authority_error":
            check("recovery_before_deadline_mode_not_required", prior.get("reason") == "RECOVERY_NOT_REQUIRED")
        else:
            # 長い後続世代試験では期限を越え得る。期限後の停止専用取得は正当。
            check("recovery_after_deadline_is_stop_only", prior.get("kind") == "evaluation_authority_result"
                and prior.get("action") == "resource_claim" and prior.get("ci_eligible") is False
                and prior.get("recovery_only") is True
                and prior.get("owner_epoch") == expected_epoch
                and prior.get("lease_until", 0) - 60 >= manifest["deadline"])
            expired_owner = {"run_id": run_id, "owner_id": "recovery-owner", "owner_epoch": expected_epoch}
            check("recovery_after_deadline_cannot_dispatch", call(12004, req("resource_dispatch", "expired-dispatch",
                **expired_owner, operation_id=op)).get("kind") == "authority_error")
        check("recovery_expired_owner_fenced", denied(12004, req("resource_cancel", "old-cancel", **owner), "OWNER_STALE"))
        acquired = success(12004, claim)
        check("recovery_cancel_claim_is_stop_only", acquired["cancelled"] is True and acquired["recovery_only"] is True
            and acquired["owner_epoch"] == expected_epoch
            and acquired["resources"]["slots"] == acquired["resources"]["unsettled"] == 1)
        finalize = req("run_cancel_finalize", "finalize", run_id=run_id)
        check("recovery_missing_stop_stays_incomplete", denied(12004, finalize, "STOP_UNCONFIRMED")
            and gate_at(2)["ci_eligible"] is False)
        success(12003, req("resource_observe", "stop", run_id=run_id, operation_id=op,
            event_id="recovery-stop", stopped=True, usage=None))
        receipt = success(12004, finalize)
        check("recovery_stopped_cancel_keeps_unknown_usage", receipt["execution_status"] == "CANCELLED"
            and receipt["budget_closure"] is False and "BUDGET_OPEN" in gate_at(3)["reasons"])
        success(12003, req("resource_observe", "usage", run_id=run_id, operation_id=op,
            event_id="recovery-usage", stopped=True, usage={"input_tokens": 0, "output_tokens": 0, "cost_usd": "0"}))
        closed = success(12004, req("resource_close", "close", run_id=run_id,
            owner_id=acquired["owner_id"], owner_epoch=acquired["owner_epoch"]))
        check("recovery_late_accounting_closes", closed["closed"] is True
            and closed["budget_closure"] is (not closed["breached"])
            and closed["resources"]["slots"] == closed["resources"]["unsettled"] == 0)
        runtime.restart_broker()
        check("recovery_restart_keeps_receipt", success(12004, finalize) == receipt)
        check("recovery_current_ci_stays_cancelled", gate_at(3)["ci_eligible"] is False)
    return after_revocation
