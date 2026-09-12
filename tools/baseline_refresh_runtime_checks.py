"""固定通常runに基づくbaseline更新と、既存契約の固定参照を実環境で確認する。"""
from gah.run_contracts import content_ref


def verify(*, success, call, denied, check, runtime):
    run_id = "regression-runtime"
    series = "fixture-baseline-series"
    def request(action, suffix, **fields):
        return {"schema_version": 1, "action": action, "request_id": "baseline-refresh-" + suffix, **fields}
    current_request = request("baseline_current", "current", series_id=series)
    old = success(12004, current_request)["baseline"]
    old_ref = content_ref("baseline", old["baseline_id"], old)
    old_request = request("baseline_resolve", "old", series_id=series,
        expected_baseline_ref=old_ref, expected_contract_ref=old["contract_ref"])
    outputs_request = request("run_outputs", "outputs", run_id=run_id)
    outputs = success(12004, outputs_request)
    manifest = success(12004, request("run_artifact", "manifest", run_id=run_id,
        artifact_ref=outputs["outputs"]["manifest_ref"]))["artifact"]
    evidence = success(12004, request("run_artifact", "evidence", run_id=run_id,
        artifact_ref=outputs["outputs"]["evidence"]))["artifact"]
    final_request = request("evidence_finalize", "receipt", run_id=run_id)
    receipt = success(12004, final_request)
    gate = request("ci_check", "ci", run_id=run_id,
        expected_manifest_ref=outputs["outputs"]["manifest_ref"], expected_contract_ref=manifest["contract_ref"],
        expected_baseline_ref=manifest["baseline_ref"], expected_target_refs=manifest["target_refs"],
        expected_use_cases=manifest["use_cases"])
    def ci(code):
        result = call(12004, gate)
        return (result.get("kind") == "ci_gate_result" and result.get("request_id") == gate["request_id"]
            and result.get("expected_manifest_ref") == gate["expected_manifest_ref"]
            and result.get("outputs_ref") == outputs["outputs_ref"]
            and type(result.get("exit_code")) is int and result["exit_code"] == code
            and result.get("use") is (code == 0) and result.get("ci_eligible") is (code == 0))
    proposal = request("baseline_propose", "propose", proposal_id="baseline-refresh-runtime",
        series_id=series, run_id=run_id, expected_generation=1)
    for uid in (12002, 12003, 12004):
        check(f"baseline_refresh_propose_role_{uid}_rejected", denied(uid, proposal, "AUTHORITY_DENIED"))
    success(12001, proposal)
    validation = request("baseline_validate", "validate", proposal_id="baseline-refresh-runtime",
        validation_id="baseline-refresh-validation")
    check("baseline_refresh_proposer_cannot_validate", denied(12001, validation, "AUTHORITY_DENIED"))
    success(12003, validation)
    adopted = success(12001, request("baseline_adopt", "adopt", proposal_id="baseline-refresh-runtime",
        validation_id="baseline-refresh-validation", expected_generation=1))
    current = success(12004, current_request)
    check("baseline_refresh_generation_two_adopted", adopted.get("generation") == 2
        and current.get("valid") is True and current.get("generation") == 2)
    new = current["baseline"]
    check("baseline_refresh_source_and_ttl_bound", new["source_run_ref"] == outputs["outputs"]["manifest_ref"]
        and new["contract_ref"] == manifest["contract_ref"] and new["valid_until"] == evidence["valid_until"])
    check("baseline_refresh_old_reference_remains_usable", success(12004, old_request).get("use") is True
        and manifest["baseline_ref"] == old_ref)
    check("baseline_refresh_latest_use_rejects_old_reference", denied(12004,
        {**old_request, "action": "baseline_use", "request_id": "baseline-refresh-latest-old"}, "BINDING_MISMATCH"))
    check("baseline_refresh_keeps_historical_outputs", success(12004, outputs_request) == outputs
        and success(12004, final_request) == receipt)
    check("baseline_refresh_existing_ci_remains_successful", ci(0))
    runtime.restart_broker()
    restarted = success(12004, current_request)
    check("baseline_refresh_restart_keeps_generation_and_references", restarted["baseline"] == new
        and restarted.get("valid") is True and success(12004, old_request).get("use") is True)
    check("baseline_refresh_restart_ci_successful", ci(0))
    new_request = request("baseline_resolve", "new", series_id=series,
        expected_baseline_ref=content_ref("baseline", new["baseline_id"], new),
        expected_contract_ref=new["contract_ref"])
    def after_revocation():
        current = success(12004, current_request)
        pinned = success(12004, new_request)
        check("baseline_refresh_dependency_revocation_propagates", current["baseline"] == new
            and current.get("valid") is False and current.get("reason") != "BASELINE_REVOKED"
            and pinned.get("use") is False and pinned.get("valid") is False)
        check("baseline_refresh_dependency_revocation_blocks_source_ci", ci(1))
        check("baseline_refresh_revocation_keeps_historical_outputs", success(12004, outputs_request) == outputs
            and success(12004, final_request) == receipt)
    return after_revocation
