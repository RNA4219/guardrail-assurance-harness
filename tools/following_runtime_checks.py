"""baseline 2を参照する契約3と、製品監督の実Docker接続。"""
from copy import deepcopy
import re
import time
from gah.run_contracts import content_ref
from gah.run_evidence import bound_bundle_digest
from gah.following_contracts import build_following_runs


def verify(*, success, call, denied, check, runtime, runner, receipts, active, save_observations):
    def request(action, identifier, **fields):
        return {'schema_version':1,'action':action,'request_id':identifier,**fields}
    current=success(12004,request('contract_current','following-initial-current',series_id='fixture-contract-series'))
    previous=current['contract']
    baseline=success(12004,request('baseline_current','following-baseline',series_id='fixture-baseline-series'))['baseline']
    source=success(12004,request('run_prepare','regression-prepare',run_id='regression-runtime',
        contract_series_id='fixture-contract-series',expected_contract_ref=content_ref('evaluation_contract',previous['contract_id'],previous)))
    following=deepcopy(previous);following.update(contract_id='fixture-contract-generation-3',generation=3)
    following['comparison']['baseline_ref']=content_ref('baseline',baseline['baseline_id'],baseline)
    success(12001,request('contract_propose','following-propose',proposal_id='following-proposal',
        series_id='fixture-contract-series',expected_generation=2,contract=following))
    preparation=request('contract_candidate_prepare','following-prepare',candidate_id='following-candidate',
        proposal_id='following-proposal',baseline_series_id='fixture-baseline-series',
        expected_contract_ref=content_ref('evaluation_contract',previous['contract_id'],previous),
        expected_baseline_ref=following['comparison']['baseline_ref'],old_run_id='following-old',new_run_id='following-new')
    candidate=success(12003,preparation);runs=candidate['runs']
    expected=build_following_runs(previous,following,baseline_record=baseline,
        source_prepared={k:source[k] for k in ('bound_run','baseline_context','materialization')},
        now=runs['old']['bound_run']['manifest']['created_at'],old_run_id='following-old',new_run_id='following-new')
    check('following_saved_factory_matches',runs==expected)
    check('following_old_and_new_baselines_separate',runs['old']['baseline_context']==source['baseline_context']
        and runs['old']['baseline_context']['baseline_ref']!=runs['new']['baseline_context']['baseline_ref'])
    runtime.restart_broker()
    check('following_preparation_replay_after_restart',success(12003,preparation)==candidate)
    final_receipts = []
    for side, count in (("old", 30), ("new", 30)):
        bound = runs[side]["bound_run"]
        run_id = bound["manifest"]["run_id"]
        begin = request("contract_candidate_begin", "following-begin-" + side, candidate_id="following-candidate", side=side)
        for uid in (12001, 12002, 12003):
            check(f"following_{side}_begin_role_{uid}_rejected", denied(uid, begin, "AUTHORITY_DENIED"))
        check(f"following_{side}_normal_begin_rejected", denied(12004, request("run_begin", "normal-following-" + side,
            manifest=bound["manifest"], plan=bound["plan"], contract_series_id="fixture-contract-series"), "CANDIDATE_ENTRY_REQUIRED"))
        begun = success(12004, begin)
        snapshot = begun["resource_snapshot"]
        check(f"following_{side}_begin_binding", begun.get("candidate_id") == "following-candidate"
            and begun.get("side") == side and begun.get("run_id") == run_id
            and begun.get("contract_generation") == bound["contract"]["generation"]
            and snapshot.get("run_id") == run_id and snapshot.get("owner_id") == begin["request_id"]
            and snapshot.get("owner_epoch") == 1 and snapshot.get("deadline") == bound["manifest"]["deadline"]
            and snapshot.get("manifest_digest") == content_ref("run_manifest", run_id, bound["manifest"])["digest"])
        check(f"following_{side}_begin_replay", success(12004, begin) == begun)
        success(12004, request("evidence_open", "following-open-" + side, run_id=run_id))
        owner = {"run_id": run_id, "owner_id": begin["request_id"], "owner_epoch": 1}
        records = runs[side]["materialization"]["manifest"]["records"]
        check(f"following_{side}_record_count", len(records) == count)
        for index, record in enumerate(records):
            entry = next(item for item in bound["plan"]["entries"] if all(item[key] == record[key]
                for key in ("obligation_id", "case_id", "trial_id", "variant")))
            operation_id = f"following-{side}-{index:02d}"
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
            check(f"following_{side}_{index:02d}_docker_receipt", receipt.get("execution_status") == "COMPLETED"
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
        closed = success(12004, request("resource_close", "following-close-" + side, **owner))
        resources = closed["resources"]
        check(f"following_{side}_resources_settled", closed.get("closed") is True and closed.get("budget_closure") is True
            and resources["case_trial_executions"] == count and all(resources[key] == 0 for key in ("model_calls", "slots", "unsettled")))
        finalize = request("evidence_finalize", "following-finalize-" + side, run_id=run_id)
        final = success(12004, finalize)
        expected_kinds = {"manifest_ref": "run_manifest", "bundle_ref": "bound_bundle", "decision_ref": "run_decision",
            "evidence_ref": "evidence", "closure_ref": "resource_closure"}
        check(f"following_{side}_terminal_full_references", final.get("run_id") == run_id
            and final.get("manifest_ref") == content_ref("run_manifest", run_id, bound["manifest"])
            and final.get("bundle_ref") == content_ref("bound_bundle", run_id, bound)
            and all(type(final.get(key)) is dict and set(final[key]) == {"kind", "id", "digest"}
                and final[key]["kind"] == kind and final[key]["id"] == run_id
                and type(final[key]["digest"]) is str and re.fullmatch(r"[0-9a-f]{64}", final[key]["digest"]) is not None
                for key, kind in expected_kinds.items()))
        check(f"following_{side}_healthy_materialized_evidence", final.get("assurance") == "HEALTHY"
            and final.get("purpose") == bound["manifest"]["purpose"]
            and final.get("input_materialization_verified") is True and final.get("resource_closure_verified") is True
            and final.get("adoption_verified") is False and final.get("ci_eligible") is False)
        runtime.restart_broker()
        check(f"following_{side}_terminal_replay_after_restart", success(12004, finalize) == final)
        # fresh経路は同DBの5 artifactとterminalを再読込・照合する。
        current = success(12004, request("evidence_current", "following-current-" + side,
            run_id=run_id, expected_bundle_digest=bound_bundle_digest(bound)))
        stored_final = {key: value for key, value in final.items() if key not in {"action", "request_id"}}
        check(f"following_{side}_fresh_stored_graph", current.get("receipt") == stored_final
            and current.get("run_id") == run_id and current.get("use") is False
            and current.get("reasons") == ["ADOPTION_NOT_CONNECTED"])
        final_receipts.append(stored_final)
    validation=request('contract_candidate_validate','following-validate',candidate_id='following-candidate',validation_id='following-validation')
    check('following_manager_cannot_validate',denied(12001,validation,'AUTHORITY_DENIED'))
    validated=success(12003,validation)
    adopt=request('contract_candidate_adopt','following-adopt',candidate_id='following-candidate',
        validation_id='following-validation',expected_contract_generation=2,expected_baseline_generation=2)
    for uid in (12002,12003,12004):
        check('following_adopt_role_'+str(uid)+'_rejected',denied(uid,adopt,'AUTHORITY_DENIED'))
    check('following_stale_generation_rejected',denied(12001,{**adopt,'request_id':'following-stale-adopt','expected_contract_generation':1},'GENERATION_CONFLICT'))
    adopted=success(12001,adopt)
    query=request('contract_current','following-current',series_id='fixture-contract-series')
    current=success(12004,query)
    check('following_generation_three_current',adopted['generation']==3 and current['valid'] is True and current['contract']==following)
    runtime.restart_broker()
    check('following_current_and_receipts_after_restart',success(12004,query)==current and success(12001,adopt)==adopted and success(12003,validation)==validated)
    from tools.supervisor_runtime_checks import verify as verify_supervisor
    after_supervisor=verify_supervisor(runtime=runtime,call=call,check=check,contract=following,
        receipts=receipts,save_observations=save_observations)
    def after_revocation():
        current=success(12004,query)
        check('following_source_revocation_invalidates_generation_three',current['generation']==3 and current['valid'] is False)
        check('following_revocation_keeps_adoption_receipt',success(12001,adopt)==adopted)
        after_supervisor()
    return after_revocation
