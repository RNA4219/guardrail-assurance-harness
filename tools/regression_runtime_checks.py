"""採択後の固定30件を通常runとして実行し、freshなCI利用まで確認する。"""
import re
import time
import io
from types import SimpleNamespace

from gah.run_contracts import content_ref
from tools.gah_ci import run as consume_ci


def verify(*, success, call, denied, check, runtime, runner, contract,
           receipts, active, save_observations, expected_assurance="HEALTHY"):
    if expected_assurance not in {"HEALTHY", "WARNING"}:
        raise ValueError("EXPECTED_ASSURANCE_INVALID")
    budget_warning = expected_assurance == "WARNING"
    def request(action, identifier, **fields):
        return {"schema_version": 1, "action": action, "request_id": identifier, **fields}

    prepare = request("run_prepare", "regression-prepare", run_id="regression-runtime",
        contract_series_id="fixture-contract-series",
        expected_contract_ref=content_ref("evaluation_contract", contract["contract_id"], contract))
    for uid in (12001, 12002, 12003):
        check(f"regression_prepare_role_{uid}_rejected", denied(uid, prepare, "AUTHORITY_DENIED"))
    prepared = success(12004, prepare)
    bound = prepared["bound_run"]
    manifest = bound["manifest"]
    run_id = manifest["run_id"]
    records = prepared["materialization"]["manifest"]["records"]
    check("regression_fixed_comparison_binding", manifest["purpose"] == "regression"
        and bound["contract"] == contract and len(records) == 30
        and manifest["baseline_ref"] == contract["comparison"]["baseline_ref"])
    begin = request("run_begin", "regression-begin", manifest=manifest, plan=bound["plan"],
        contract_series_id="fixture-contract-series")
    begun = success(12004, begin)
    owner = {"run_id": run_id, "owner_id": begin["request_id"], "owner_epoch": 1}
    check("regression_begun_generation_two", begun["contract_generation"] == 2
        and begun["resource_snapshot"]["owner_id"] == owner["owner_id"])
    success(12004, request("evidence_open", "regression-open", run_id=run_id))
    gate = request("ci_check", "regression-gate", run_id=run_id,
        expected_manifest_ref=content_ref("run_manifest", run_id, manifest),
        expected_contract_ref=manifest["contract_ref"], expected_baseline_ref=manifest["baseline_ref"],
        expected_target_refs=manifest["target_refs"], expected_use_cases=manifest["use_cases"])

    def gate_result(expected_code):
        value = call(12004, gate)
        expected = {"schema_version", "kind", "action", "request_id", "run_id", "checked_at",
            "expected_manifest_ref", "outputs_ref", "assurance", "reasons", "use", "ci_eligible",
            "execution_status", "exit_code"}
        if (set(value) != expected or type(value["schema_version"]) is not int or value["schema_version"] != 1
                or value["kind"] != "ci_gate_result" or value["action"] != "ci_check"
                or value["request_id"] != gate["request_id"] or value["run_id"] != run_id
                or value["expected_manifest_ref"] != gate["expected_manifest_ref"]
                or value["exit_code"] != expected_code or type(value["exit_code"]) is not int
                or value["ci_eligible"] is not (expected_code == 0) or value["use"] is not (expected_code == 0)):
            raise AssertionError("REGRESSION_GATE_RESPONSE_INVALID")
        return value

    check("regression_incomplete_cannot_pass_ci", gate_result(2)["execution_status"] == "FAILED")
    for index, record in enumerate(records):
        entry = next(e for e in bound["plan"]["entries"] if all(e[k] == record[k]
            for k in ("obligation_id", "case_id", "trial_id", "variant")))
        op = f"regression-{index:02d}"
        success(12004, request("resource_claim", op + "-claim", run_id=run_id,
            owner_id=owner["owner_id"], recovery=False))
        success(12004, request("resource_reserve", op + "-reserve", **owner, operation_id=op,
            entry={k: entry[k] for k in ("obligation_id", "case_id", "trial_id", "variant")},
            scenario=record["scenario"]))
        success(12004, request("resource_dispatch", op + "-dispatch", **owner, operation_id=op))
        binding = {"run_id": run_id, "operation_id": op, "owner_epoch": 1,
            "contract_digest": manifest["contract_ref"]["digest"], "target_digest": entry["target_ref"]["digest"],
            **{k: entry[k] for k in ("obligation_id", "case_id", "trial_id")}, "stage_id": entry["stage_ids"][0],
            "fixture_digest": runner.lock["worker_digest"], "adapter_digest": runner.adapter_digest,
            "policy_digest": manifest["policy_ref"]["digest"], "evaluator_digest": entry["evaluator_ref"]["digest"],
            "isolation_digest": runner.isolation_digest}
        active.append({"run_id": run_id, "operation_id": op})
        started = int(time.time())
        receipt = runner.run(record["scenario"], binding, run_deadline=manifest["deadline"], timeout_seconds=120)
        finished = int(time.time())
        receipts.append(receipt)
        save_observations()
        normalized = receipt.get("normalized_result")
        expected = {"schema_version": 1, "kind": "normalized_result", "binding": binding,
            "mode": record["scenario"].split(":", 1)[0], "observation": "PASS",
            "mutation_outcome": record["expected"].get("mutation_outcome"), "detection": None,
            "deviation": None, "error_class": None}
        check(op + "_docker_receipt", receipt.get("execution_status") == "COMPLETED"
            and all(receipt.get(k) is True for k in ("stop_confirmed", "cleanup_confirmed", "isolation_config_verified"))
            and receipt.get("ci_eligible") is False and type(normalized) is dict
            and set(normalized) == set(expected) | {"raw_digest"}
            and all(normalized[k] == v for k, v in expected.items())
            and re.fullmatch(r"[0-9a-f]{64}", normalized.get("raw_digest", "")) is not None)
        success(12003, request("resource_observe", op + "-observe", run_id=run_id, operation_id=op,
            event_id=op + "-stop", stopped=True, usage={"input_tokens": 0, "output_tokens": 0, "cost_usd": "0"}))
        success(12003, request("evidence_record", op + "-record", run_id=run_id,
            attempt={"schema_version": 1, "kind": "attempt_record", "attempt_id": op + "-attempt",
                "variant": record["variant"], "retry_of": None, "started_at": started, "finished_at": finished,
                "stop_confirmed": True, "execution_status": "COMPLETED", "state_restored": True,
                "expected_binding": binding, "result": normalized}))
    closed = success(12004, request("resource_close", "regression-close", **owner))
    check("regression_thirty_entries_settled", closed["closed"] is True and closed["budget_closure"] is True
        and closed["resources"]["case_trial_executions"] == 30
        and all(closed["resources"][k] == 0 for k in ("slots", "unsettled", "model_calls")))
    finalize = request("evidence_finalize", "regression-finalize", run_id=run_id)
    receipt = success(12004, finalize)
    outputs_request = request("run_outputs", "regression-outputs", run_id=run_id)
    outputs = success(12004, outputs_request)
    check("regression_required_outputs_saved", receipt["assurance"] == expected_assurance
        and all(outputs["outputs"].get(k) for k in ("decision", "evidence", "findings", "plans", "run_receipt")))
    for kind in ("findings", "plans"):
        ref = outputs["outputs"][kind]
        report_response = success(12004, request("run_artifact", "regression-read-" + kind,
            run_id=run_id, artifact_ref=ref))
        report_value = report_response["artifact"]
        base_valid = (report_response["artifact_ref"] == ref
            and len(report_value["assessed_controls"]) == 15)
        if budget_warning:
            items = report_value["items"]
            check("regression_" + kind + "_warning_report_retrievable", base_valid
                and type(items) is list and len(items) == 15)
            if kind == "findings" and type(items) is list and len(items) == 15:
                controls = set()
                for index, finding_ref in enumerate(items):
                    finding_response = success(12004, request("run_artifact",
                        f"regression-warning-finding-{index:02d}", run_id=run_id,
                        artifact_ref=finding_ref))
                    finding = finding_response["artifact"]
                    controls.add(finding.get("control_ref", {}).get("id"))
                    check(f"regression_warning_finding_{index:02d}",
                        finding_response["artifact_ref"] == finding_ref
                        and finding.get("reason_code") == "warning" and finding.get("status") == "OPEN")
                check("regression_warning_findings_cover_assessed_controls",
                    controls == set(report_value["assessed_controls"]))
        else:
            check("regression_" + kind + "_report_retrievable", base_valid
                and report_value["items"] == [])
    ready = gate_result(0)
    check("regression_fresh_ci_success", ready["outputs_ref"] == outputs["outputs_ref"]
        and ready["assurance"] == expected_assurance and ready["reasons"] == [])
    check("regression_consumer_returns_zero", consume_ci(SimpleNamespace(client=call), gate, io.StringIO()) == 0)
    if budget_warning:
        decision_response = success(12004, request("run_artifact", "regression-read-budget-warning",
            run_id=run_id, artifact_ref=outputs["outputs"]["decision"]))
        decision = decision_response["artifact"]
        basis = decision.get("budget_warning")
        check("regression_saved_warning_basis", decision_response["artifact_ref"] == outputs["outputs"]["decision"]
            and decision.get("assurance") == "WARNING" and type(basis) is dict
            and basis.get("run_id") == run_id and basis.get("usage", {}).get("case_trial_executions") == 30
            and basis.get("limits", {}).get("case_trial_executions") == 37
            and basis.get("warning_usage_min") == [4, 5])
        from tools.gah_report import build_report, render_markdown
        report = build_report(runtime, gate)
        markdown = render_markdown(report)
        versions = report.get("evaluation_versions", {})
        check("regression_warning_report", report.get("assurance") == "WARNING"
            and report.get("observed_assurance") == "WARNING" and report.get("exit_code") == 0
            and report.get("ci_eligible") is True and report.get("budget_warning") == basis
            and type(versions.get("contract_generation")) is int and versions["contract_generation"] == 2
            and type(versions.get("baseline_generation")) is int and versions["baseline_generation"] == 1
            and "WARNING" in markdown and "予算使用量（保存時）" in markdown
            and "case_trial_executions" in markdown
            and "契約世代" in markdown and "baseline世代" in markdown)
    runtime.restart_broker()
    restarted_gate = gate_result(0)
    check("regression_ci_rechecks_after_restart", restarted_gate["outputs_ref"] == ready["outputs_ref"]
        and restarted_gate["assurance"] == expected_assurance)
    check("regression_historical_receipt_after_restart", success(12004, finalize) == receipt)
    check("regression_outputs_after_restart", success(12004, outputs_request) == outputs)

    def after_revocation():
        check("regression_source_revocation_blocks_ci", gate_result(1)["assurance"] == expected_assurance)
        check("regression_consumer_returns_one_after_revocation",
            consume_ci(SimpleNamespace(client=call), gate, io.StringIO()) == 1)
        check("regression_source_revocation_keeps_history", success(12004, finalize) == receipt
            and success(12004, outputs_request) == outputs)
    return after_revocation
