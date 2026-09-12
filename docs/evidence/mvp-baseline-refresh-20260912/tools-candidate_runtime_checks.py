"""固定15件・比較30件を実Dockerで処理する候補run検査。"""
import re
import time

from gah.fixture_admission import execution_context
from gah.run_contracts import content_ref
from gah.run_evidence import bound_bundle_digest
from gah.transition_materialization import build_transition_runs


def verify(*, success, denied, check, runtime, runner, prepared, preflight_request,
           transition, receipts, active, save_observations, adopt=False, regression=False, cancellation=False, recovery=False, baseline_refresh=False, call=None):
    def request(action, identifier, **fields):
        return {"schema_version": 1, "action": action, "request_id": identifier, **fields}

    prepare_request = {**preflight_request, "action": "contract_candidate_prepare",
        "request_id": "candidate-prepare-runtime", "candidate_id": "candidate-runtime",
        "old_run_id": "candidate-old-runtime", "new_run_id": "candidate-new-runtime"}
    for uid in (12001, 12002, 12004):
        check(f"candidate_prepare_role_{uid}_rejected", denied(uid, prepare_request, "AUTHORITY_DENIED"))
    candidate = success(12003, prepare_request)
    runs = candidate["runs"]
    worker, lock, profile = execution_context()
    expected = build_transition_runs(transition["previous_contract"], transition["next_contract"],
        baseline_record=transition["baseline_record"], source_prepared=prepared, worker_source=worker,
        runtime_lock=lock, execution_profile=profile,
        now=runs["old"]["bound_run"]["manifest"]["created_at"],
        old_run_id=prepare_request["old_run_id"], new_run_id=prepare_request["new_run_id"])
    payload = {key: prepare_request[key] for key in ("candidate_id", "proposal_id", "baseline_series_id",
        "expected_contract_ref", "expected_baseline_ref")}
    payload.update(proposal_digest=content_ref("evaluation_contract", transition["next_contract"]["contract_id"],
        transition["next_contract"])["digest"], runs=expected)
    check("candidate_factory_matches_saved_full_references", runs == expected
        and candidate["candidate_ref"] == content_ref("contract_candidate", "candidate-runtime", payload)
        and candidate.get("adoption_verified") is False)
    runtime.restart_broker()
    check("candidate_prepare_replay_after_restart", success(12003, prepare_request) == candidate)
    final_receipts = []
    for side, count in (("old", 15), ("new", 30)):
        bound = runs[side]["bound_run"]
        run_id = bound["manifest"]["run_id"]
        begin = request("contract_candidate_begin", "candidate-begin-" + side, candidate_id="candidate-runtime", side=side)
        for uid in (12001, 12002, 12003):
            check(f"candidate_{side}_begin_role_{uid}_rejected", denied(uid, begin, "AUTHORITY_DENIED"))
        check(f"candidate_{side}_normal_begin_rejected", denied(12004, request("run_begin", "normal-candidate-" + side,
            manifest=bound["manifest"], plan=bound["plan"], contract_series_id="fixture-contract-series"), "CANDIDATE_ENTRY_REQUIRED"))
        begun = success(12004, begin)
        snapshot = begun["resource_snapshot"]
        check(f"candidate_{side}_begin_binding", begun.get("candidate_id") == "candidate-runtime"
            and begun.get("side") == side and begun.get("run_id") == run_id
            and begun.get("contract_generation") == bound["contract"]["generation"]
            and snapshot.get("run_id") == run_id and snapshot.get("owner_id") == begin["request_id"]
            and snapshot.get("owner_epoch") == 1 and snapshot.get("deadline") == bound["manifest"]["deadline"]
            and snapshot.get("manifest_digest") == content_ref("run_manifest", run_id, bound["manifest"])["digest"])
        check(f"candidate_{side}_begin_replay", success(12004, begin) == begun)
        success(12004, request("evidence_open", "candidate-open-" + side, run_id=run_id))
        owner = {"run_id": run_id, "owner_id": begin["request_id"], "owner_epoch": 1}
        records = runs[side]["materialization"]["manifest"]["records"]
        check(f"candidate_{side}_record_count", len(records) == count)
        for index, record in enumerate(records):
            entry = next(item for item in bound["plan"]["entries"] if all(item[key] == record[key]
                for key in ("obligation_id", "case_id", "trial_id", "variant")))
            operation_id = f"candidate-{side}-{index:02d}"
            success(12004, request("resource_claim", "claim-" + operation_id,
                run_id=run_id, owner_id=owner["owner_id"], recovery=False))
            success(12004, request("resource_reserve", "reserve-" + operation_id, **owner,
                operation_id=operation_id, entry={key: entry[key] for key in ("obligation_id", "case_id", "trial_id", "variant")},
                scenario=record["scenario"]))
            success(12004, request("resource_dispatch", "dispatch-" + operation_id, **owner, operation_id=operation_id))
            binding = {"run_id": run_id, "operation_id": operation_id, "owner_epoch": 1,
                "contract_digest": bound["manifest"]["contract_ref"]["digest"], "target_digest": entry["target_ref"]["digest"],
                "obligation_id": entry["obligation_id"], "case_id": entry["case_id"], "trial_id": entry["trial_id"],
                "stage_id": entry["stage_ids"][0], "fixture_digest": runner.lock["worker_digest"],
                "adapter_digest": runner.adapter_digest, "policy_digest": bound["manifest"]["policy_ref"]["digest"],
                "evaluator_digest": entry["evaluator_ref"]["digest"], "isolation_digest": runner.isolation_digest}
            active.append({"run_id": run_id, "operation_id": operation_id})
            started = int(time.time())
            receipt = runner.run(record["scenario"], binding, run_deadline=bound["manifest"]["deadline"], timeout_seconds=120)
            finished = int(time.time())
            receipts.append(receipt)
            save_observations()
            normalized = receipt.get("normalized_result")
            expected_result = {"schema_version": 1, "kind": "normalized_result", "binding": binding,
                "mode": record["scenario"].split(":", 1)[0], "observation": "PASS",
                "mutation_outcome": record["expected"].get("mutation_outcome"),
                "detection": None, "deviation": None, "error_class": None}
            check(f"candidate_{side}_{index:02d}_docker_receipt", receipt.get("execution_status") == "COMPLETED"
                and all(receipt.get(key) is True for key in ("stop_confirmed", "cleanup_confirmed", "isolation_config_verified"))
                and receipt.get("ci_eligible") is False and type(normalized) is dict
                and set(normalized) == set(expected_result) | {"raw_digest"}
                and all(normalized[key] == value for key, value in expected_result.items())
                and type(normalized["raw_digest"]) is str and re.fullmatch(r"[0-9a-f]{64}", normalized["raw_digest"]) is not None)
            success(12003, request("resource_observe", "observe-" + operation_id, run_id=run_id,
                operation_id=operation_id, event_id="stop-" + operation_id, stopped=True,
                usage={"input_tokens": 0, "output_tokens": 0, "cost_usd": "0"}))
            success(12003, request("evidence_record", "record-" + operation_id, run_id=run_id,
                attempt={"schema_version": 1, "kind": "attempt_record", "attempt_id": "attempt-" + operation_id,
                    "variant": record["variant"], "retry_of": None, "started_at": started, "finished_at": finished,
                    "stop_confirmed": True, "execution_status": "COMPLETED", "state_restored": True,
                    "expected_binding": binding, "result": normalized}))
        closed = success(12004, request("resource_close", "candidate-close-" + side, **owner))
        resources = closed["resources"]
        check(f"candidate_{side}_resources_settled", closed.get("closed") is True and closed.get("budget_closure") is True
            and resources["case_trial_executions"] == count and all(resources[key] == 0 for key in ("model_calls", "slots", "unsettled")))
        finalize = request("evidence_finalize", "candidate-finalize-" + side, run_id=run_id)
        final = success(12004, finalize)
        expected_kinds = {"manifest_ref": "run_manifest", "bundle_ref": "bound_bundle", "decision_ref": "run_decision",
            "evidence_ref": "evidence", "closure_ref": "resource_closure"}
        check(f"candidate_{side}_terminal_full_references", final.get("run_id") == run_id
            and final.get("manifest_ref") == content_ref("run_manifest", run_id, bound["manifest"])
            and final.get("bundle_ref") == content_ref("bound_bundle", run_id, bound)
            and all(type(final.get(key)) is dict and set(final[key]) == {"kind", "id", "digest"}
                and final[key]["kind"] == kind and final[key]["id"] == run_id
                and type(final[key]["digest"]) is str and re.fullmatch(r"[0-9a-f]{64}", final[key]["digest"]) is not None
                for key, kind in expected_kinds.items()))
        check(f"candidate_{side}_healthy_materialized_evidence", final.get("assurance") == "HEALTHY"
            and final.get("purpose") == bound["manifest"]["purpose"]
            and final.get("input_materialization_verified") is True and final.get("resource_closure_verified") is True
            and final.get("adoption_verified") is False and final.get("ci_eligible") is False)
        runtime.restart_broker()
        check(f"candidate_{side}_terminal_replay_after_restart", success(12004, finalize) == final)
        # fresh経路は同DBの5 artifactとterminalを再読込・照合する。
        current = success(12004, request("evidence_current", "candidate-current-" + side,
            run_id=run_id, expected_bundle_digest=bound_bundle_digest(bound)))
        stored_final = {key: value for key, value in final.items() if key not in {"action", "request_id"}}
        check(f"candidate_{side}_fresh_stored_graph", current.get("receipt") == stored_final
            and current.get("run_id") == run_id and current.get("use") is False
            and current.get("reasons") == ["ADOPTION_NOT_CONNECTED"])
        final_receipts.append(stored_final)
    current = success(12004, request("contract_current", "current-after-candidates", series_id="fixture-contract-series"))
    check("candidate_execution_keeps_old_contract_current", current.get("valid") is True and current.get("generation") == 1
        and current.get("contract") == transition["previous_contract"])
    check("candidate_sides_have_distinct_evidence_and_closure", all(final_receipts[0][key] != final_receipts[1][key]
        for key in ("manifest_ref", "bundle_ref", "decision_ref", "evidence_ref", "closure_ref")))
    if adopt:
        from tools.transition_acceptance_runtime_checks import verify as verify_adoption
        after_adoption = verify_adoption(success=success, denied=denied, check=check, runtime=runtime,
            candidate=candidate, preflight_request=preflight_request, final_receipts=final_receipts)
        if regression:
            from tools.regression_runtime_checks import verify as verify_regression
            after_regression = verify_regression(success=success, call=call, denied=denied, check=check,
                runtime=runtime, runner=runner, contract=transition["next_contract"],
                receipts=receipts, active=active, save_observations=save_observations)
            after_baseline_refresh = None
            if baseline_refresh:
                from tools.baseline_refresh_runtime_checks import verify as verify_refresh
                after_baseline_refresh = verify_refresh(success=success, call=call, denied=denied, check=check, runtime=runtime)
            after_cancellation = None
            if cancellation:
                from tools.cancellation_runtime_checks import verify as verify_cancellation
                after_cancellation = verify_cancellation(success=success, call=call, denied=denied, check=check,
                    runtime=runtime, runner=runner, contract=transition["next_contract"],
                    receipts=receipts, active=active, save_observations=save_observations)
            after_recovery = None
            if recovery:
                from tools.recovery_runtime_checks import verify as verify_recovery
                after_recovery = verify_recovery(success=success, call=call, denied=denied, check=check,
                    runtime=runtime, runner=runner, contract=transition["next_contract"],
                    receipts=receipts, active=active, save_observations=save_observations)
            def after_both():
                after_adoption()
                after_regression()
                if after_baseline_refresh is not None:
                    after_baseline_refresh()
                if after_cancellation is not None:
                    after_cancellation()
                if after_recovery is not None:
                    after_recovery()
            return after_both
        return after_adoption
    # 追加の候補は予約までに留める。撤回後のdispatch拒否を、closedによる
    # 拒否と混同せず検査し、未送信予約を取消して回収する。
    probe_prepare = {**prepare_request, "request_id": "candidate-probe-prepare", "candidate_id": "candidate-probe",
        "old_run_id": "candidate-probe-old", "new_run_id": "candidate-probe-new"}
    probe = success(12003, probe_prepare)
    probe_bound = probe["runs"]["old"]["bound_run"]
    probe_id = probe_bound["manifest"]["run_id"]
    success(12004, request("contract_candidate_begin", "candidate-probe-begin", candidate_id="candidate-probe", side="old"))
    owner = {"run_id": probe_id, "owner_id": "candidate-probe-begin", "owner_epoch": 1}
    entry = probe_bound["plan"]["entries"][0]
    record = probe["runs"]["old"]["materialization"]["manifest"]["records"][0]
    success(12004, request("resource_reserve", "candidate-probe-reserve", **owner, operation_id="candidate-probe-op",
        entry={key: entry[key] for key in ("obligation_id", "case_id", "trial_id", "variant")}, scenario=record["scenario"]))

    def after_revocation():
        check("candidate_revocation_blocks_unstarted_side", denied(12004, request("contract_candidate_begin",
            "candidate-probe-new-begin", candidate_id="candidate-probe", side="new"), "PREREQUISITE_UNAVAILABLE"))
        check("candidate_revocation_blocks_reserved_dispatch", denied(12004, request("resource_dispatch",
            "candidate-probe-dispatch", **owner, operation_id="candidate-probe-op"), "PREREQUISITE_UNAVAILABLE"))
        success(12004, request("resource_cancel", "candidate-probe-cancel", **owner))
        closed = success(12004, request("resource_close", "candidate-probe-close", **owner))
        check("candidate_revocation_allows_reservation_cleanup", closed.get("closed") is True and closed.get("cancelled") is True
            and all(closed["resources"][key] == 0 for key in ("slots", "unsettled", "case_trial_executions")))
        for side, final in zip(("old", "new"), final_receipts):
            bound = runs[side]["bound_run"]
            current = success(12004, request("evidence_current", "candidate-revoked-current-" + side,
                run_id=bound["manifest"]["run_id"], expected_bundle_digest=bound_bundle_digest(bound)))
            check(f"candidate_{side}_current_rechecks_source_revocation", current.get("receipt") == final
                and current.get("use") is False and "ADOPTED_CONDITIONS_UNAVAILABLE" in current.get("reasons", []))
    return after_revocation
