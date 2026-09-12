"""固定合成fixtureの15件を実Dockerで処理し、baseline境界を検査するCLI。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import time
from copy import deepcopy
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from gah.docker_runner import DockerRunner
from gah.execution_journal import ExecutionJournal
from gah.policy import initial_policy_profile
from gah.run_contracts import content_ref
from tools.authority_runtime import AuthorityRuntime, AuthorityRuntimeError


def _request(action: str, request_id: str, **fields: Any) -> dict[str, Any]:
    return {"schema_version": 1, "action": action, "request_id": request_id, **fields}


def _write(path: Path, value: Any) -> None:
    # 出力ディレクトリは新規作成済みであり、途中結果も同じrunへ追記する。
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True,
                               indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--candidate-runs", action="store_true", help="固定した旧条件15件・新条件30件も実行する")
    parser.add_argument("--candidate-adoption", action="store_true", help="候補run完了後のgen2採択・再起動・根拠撤回も検査する")
    parser.add_argument("--regression", action="store_true", help="採択後の通常30件と現在のCI利用も検査する")
    parser.add_argument("--cancellation", action="store_true", help="通常runの停止済み取消しと後日精算も検査する")
    args = parser.parse_args()
    if args.cancellation:
        args.regression = True
    if args.regression:
        args.candidate_adoption = True
    if args.candidate_adoption:
        args.candidate_runs = True
    output = Path(args.output).resolve()
    if not output.is_relative_to(ROOT):
        raise SystemExit("OUTPUT_OUTSIDE_WORKSPACE")
    output.mkdir(parents=True, exist_ok=False)

    checks: dict[str, bool] = {}
    observations: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []
    active: list[dict[str, str]] = []
    failure: str | None = None
    failure_detail: dict[str, Any] | None = None
    runtime: AuthorityRuntime | None = None
    runner: DockerRunner | None = None
    source_hashes: dict[str, str] = {}
    run_id: str | None = None

    def save_observations() -> None:
        _write(output / "observations.json", observations)
        _write(output / "execution-receipts.json", receipts)

    def check(name: str, value: bool) -> None:
        checks[name] = type(value) is bool and value
        print(json.dumps({"check": name, "passed": checks[name]}), flush=True)
        if not checks[name]:
            raise AssertionError(name)

    def call(uid: int, request: dict[str, Any]) -> dict[str, Any]:
        assert runtime is not None
        started = time.monotonic()
        result = runtime.client(uid, request)
        observations.append({"uid": uid, "action": request["action"], "response": result,
            "elapsed_seconds": round(time.monotonic() - started, 3)})
        save_observations()
        return result

    def success(uid: int, request: dict[str, Any]) -> dict[str, Any]:
        result = call(uid, request)
        expected_kind = {"evidence_open": "run_evidence_run", "evidence_record": "attempt_receipt",
                         "evidence_finalize": "authority_run_receipt",
                         "run_cancel_finalize": "authority_cancel_receipt"}.get(request["action"], "evaluation_authority_result")
        if request["action"].startswith("baseline_"):
            expected_kind = "baseline_authority_result"
        elif request["action"] in {"propose", "validate", "adopt", "current", "receipt", "revoke_actor", "revoke_validation"}:
            expected_kind = "policy_adoption_result"
        if (type(result.get("schema_version")) is not int or result["schema_version"] != 1
                or result.get("kind") != expected_kind
                or result.get("action") != request["action"] or result.get("request_id") != request["request_id"]
                or result.get("ci_eligible") is not False):
            raise AssertionError("INVALID_AUTHORITY_RESPONSE")
        return result

    def denied(uid: int, request: dict[str, Any], reason: str) -> bool:
        result = call(uid, request)
        return result.get("kind") == "authority_error" and result.get("reason") == reason

    def sources() -> dict[str, str]:
        assert runtime is not None
        extra = (
            "tools/authority_runtime.py", "tools/prepare_authority_runtime.py",
            "tools/verify_baseline_runtime.py", "src/gah/docker_runner.py",
            "tools/candidate_runtime_checks.py",
            "tools/transition_acceptance_runtime_checks.py",
            "tools/regression_runtime_checks.py",
            "tools/cancellation_runtime_checks.py",
            "tools/gah_ci.py",
            "src/gah/execution_journal.py", "src/gah/normalized.py",
            "src/gah/evaluation_authority.py", "src/gah/assurance_authority.py",
            "src/gah/baseline_authority.py", "src/gah/fixture_admission.py",
            "fixtures/runtime/fixture_worker.py",
        )
        return {**runtime.lock["source_sha256"], **{
            path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in extra
        }}

    try:
        runtime = AuthorityRuntime(output)
        runner = DockerRunner(ROOT / "config/fixture-runtime.lock.json",
                              output / "execution.sqlite")
        runtime.prepare()
        source_hashes = sources()
        _write(output / "manifest.json", {
            "schema_version": 1,
            "kind": "baseline_runtime_manifest",
            "started_at": int(time.time()),
            "source_sha256": source_hashes,
            "entry_count": 15,
            "full_mvp": False,
            "ci_eligible": False,
        })

        policy = initial_policy_profile()
        success(12001, _request("propose", "policy-propose-baseline-runtime",
                                proposal_id="policy-proposal-baseline-runtime",
                                series_id=policy["policy_id"], expected_generation=0,
                                policy=policy))
        success(12003, _request("validate", "policy-validate-baseline-runtime",
                                proposal_id="policy-proposal-baseline-runtime",
                                validation_id="policy-validation-baseline-runtime"))
        success(12001, _request("adopt", "policy-adopt-baseline-runtime",
                                proposal_id="policy-proposal-baseline-runtime",
                                validation_id="policy-validation-baseline-runtime",
                                expected_generation=0))

        prepared_response = success(12001, _request(
            "fixture_prepare", "fixture-prepare-baseline-runtime",
            run_id="baseline-runtime-run", policy_series_id=policy["policy_id"]))
        prepared = prepared_response["prepared"]
        bound = prepared["bound_run"]
        contract = bound["contract"]
        plan = bound["plan"]
        materials = prepared["pack"]["materials"]
        entries = plan["entries"]
        check("fixed_fifteen_materials", len(materials) == 15 and len(entries) == 15)

        proposal = _request("contract_propose", "contract-propose-baseline-runtime",
                            proposal_id="contract-proposal-baseline-runtime",
                            series_id="fixture-contract-series", expected_generation=0,
                            contract=contract)
        check("candidate_contract_role_rejected", denied(12002, proposal, "AUTHORITY_DENIED"))
        success(12001, proposal)
        validation = success(12003, _request("contract_validate", "contract-validate-baseline-runtime",
                                             proposal_id=proposal["proposal_id"],
                                             validation_id="contract-validation-baseline-runtime"))
        check("contract_validation_passed", validation.get("passed") is True)
        adoption_request = _request("contract_adopt", "contract-adopt-baseline-runtime",
                                    proposal_id=proposal["proposal_id"],
                                    validation_id="contract-validation-baseline-runtime",
                                    expected_generation=0)
        adopted = success(12001, adoption_request)
        check("contract_adopted_with_ci_disabled", adopted.get("ci_eligible") is False)

        # broker再起動後も同一採択receiptだけを再生し、履歴がcurrentの上書きで変わらないことを確認する。
        runtime.restart_broker()
        check("adoption_replay_survives_broker_restart",
              call(12001, adoption_request) == adopted)

        # fixture_prepareが保存したmanifestの時刻を保持する。局所時計で
        # created_atを書き換えると、保存済みmaterializationとの束縛が壊れる。
        manifest = dict(bound["manifest"])
        created_at = manifest["created_at"]
        deadline = manifest["deadline"]
        begin_request = _request("run_begin", "run-begin-baseline-runtime",
                                 manifest=manifest, plan=plan,
                                 contract_series_id="fixture-contract-series")
        check("candidate_run_begin_rejected", denied(12002, begin_request, "AUTHORITY_DENIED"))
        success(12004, begin_request)
        run_id = manifest["run_id"]
        elapsed_limit = policy["profiles"]["full"]["elapsed_seconds"]
        check("run_deadline_matches_policy", type(created_at) is int and type(deadline) is int
              and type(elapsed_limit) is int and created_at <= deadline == created_at + elapsed_limit)
        evidence_opened = success(12004, _request("evidence_open", "evidence-open-baseline-runtime", run_id=run_id))

        owner_id = begin_request["request_id"]
        owner = {"run_id": run_id, "owner_id": owner_id, "owner_epoch": 1}
        profile = runner
        for index, material in enumerate(materials):
            if int(time.time()) >= deadline:
                raise AssertionError("RUN_DEADLINE")
            entry = next(item for item in entries if item["obligation_id"] == material["obligation_id"])
            operation_id = f"baseline-operation-{index:02d}"
            # lease更新を各entryの直前に行い、15件処理中に単一leaseを使い回さない。
            success(12004, _request("resource_claim", f"claim-baseline-{index:02d}",
                                    run_id=run_id, owner_id=owner_id, recovery=False))
            short_entry = {key: entry[key] for key in ("obligation_id", "case_id", "trial_id", "variant")}
            success(12004, _request("resource_reserve", f"reserve-baseline-{index:02d}",
                                    **owner, operation_id=operation_id, entry=short_entry,
                                    scenario=material["scenario"]))
            success(12004, _request("resource_dispatch", f"dispatch-baseline-{index:02d}",
                                    **owner, operation_id=operation_id))
            binding = {
                "run_id": run_id, "operation_id": operation_id, "owner_epoch": 1,
                "contract_digest": bound["manifest"]["contract_ref"]["digest"],
                "target_digest": entry["target_ref"]["digest"],
                "obligation_id": entry["obligation_id"], "case_id": entry["case_id"],
                "trial_id": entry["trial_id"], "stage_id": entry["stage_ids"][0],
                "fixture_digest": profile.lock["worker_digest"],
                "adapter_digest": profile.adapter_digest,
                "policy_digest": bound["manifest"]["policy_ref"]["digest"],
                "evaluator_digest": entry["evaluator_ref"]["digest"],
                "isolation_digest": profile.isolation_digest,
            }
            active.append({"run_id": run_id, "operation_id": operation_id})
            started = int(time.time())
            receipt = profile.run(material["scenario"], binding,
                                 run_deadline=deadline, timeout_seconds=120)
            finished = int(time.time())
            receipts.append(receipt)
            save_observations()
            normalized = receipt.get("normalized_result")
            expected = material["expected"]
            expected_normalized = {
                "schema_version": 1, "kind": "normalized_result", "binding": binding,
                "mode": material["scenario"].split(":", 1)[0],
                "observation": "PASS",
                "mutation_outcome": expected.get("mutation_outcome"),
                "detection": None, "deviation": None, "error_class": None,
            }
            normalized_shape_ok = (type(normalized) is dict
                and set(normalized) == set(expected_normalized) | {"raw_digest"}
                and all(normalized.get(key) == value for key, value in expected_normalized.items())
                and type(normalized.get("raw_digest")) is str
                and re.fullmatch(r"[0-9a-f]{64}", normalized["raw_digest"]) is not None)
            check(f"entry_{index:02d}_docker_receipt", receipt.get("execution_status") == "COMPLETED"
                  and receipt.get("stop_confirmed") is True
                  and receipt.get("cleanup_confirmed") is True
                  and receipt.get("isolation_config_verified") is True
                  and normalized_shape_ok
                  and receipt.get("ci_eligible") is False)
            attempt = {
                "schema_version": 1, "kind": "attempt_record",
                "attempt_id": f"baseline-attempt-{index:02d}", "variant": "candidate",
                "retry_of": None, "started_at": started, "finished_at": finished,
                "stop_confirmed": receipt["stop_confirmed"],
                "execution_status": receipt["execution_status"],
                "state_restored": receipt["cleanup_confirmed"], "expected_binding": binding,
                "result": normalized,
            }
            success(12003, _request("resource_observe", f"observe-baseline-{index:02d}",
                                    run_id=run_id, operation_id=operation_id,
                                    event_id=f"stop-baseline-{index:02d}",
                                    stopped=receipt["stop_confirmed"],
                                    usage={"input_tokens": 0, "output_tokens": 0, "cost_usd": "0"}))
            success(12003, _request("evidence_record", f"record-baseline-{index:02d}",
                                    run_id=run_id, attempt=attempt))

        closed = success(12004, _request("resource_close", "close-baseline-runtime", **owner))
        closed_resources = closed.get("resources")
        check("resource_close_accounting", closed.get("closed") is True
              and closed.get("budget_closure") is True and type(closed_resources) is dict
              and closed_resources.get("case_trial_executions") == 15
              and closed_resources.get("model_calls") == 0
              and closed_resources.get("slots") == 0
              and closed_resources.get("unsettled") == 0)
        finalized = success(12004, _request("evidence_finalize", "evidence-finalize-baseline-runtime", run_id=run_id))
        check("fifteen_entries_finalized", finalized.get("resource_closure_verified") is True
              and finalized.get("input_materialization_verified") is True
              and finalized.get("ci_eligible") is False)

        baseline_proposal = success(12001, _request(
            "baseline_propose", "baseline-propose-baseline-runtime",
            proposal_id="baseline-proposal-baseline-runtime",
            series_id="fixture-baseline-series", run_id=run_id, expected_generation=0))
        success(12003, _request("baseline_validate", "baseline-validate-baseline-runtime",
                                proposal_id=baseline_proposal["proposal_id"],
                                validation_id="baseline-validation-baseline-runtime"))
        baseline_adoption_request = _request(
            "baseline_adopt", "baseline-adopt-baseline-runtime",
            proposal_id=baseline_proposal["proposal_id"],
            validation_id="baseline-validation-baseline-runtime", expected_generation=0)
        baseline_adopted = success(12001, baseline_adoption_request)
        check("baseline_adopted_with_ci_disabled", baseline_adopted.get("ci_eligible") is False)
        current = success(12004, _request("baseline_current", "baseline-current-baseline-runtime",
                                          series_id="fixture-baseline-series"))
        check("baseline_current_is_fresh", current.get("valid") is True)
        baseline = current["baseline"]
        baseline_ref = content_ref("baseline", baseline["baseline_id"], baseline)
        old_contract_ref = content_ref("evaluation_contract", contract["contract_id"], contract)
        use_request = _request("baseline_use", "baseline-use-baseline-runtime",
                               series_id="fixture-baseline-series",
                               expected_baseline_ref=baseline_ref,
                               expected_contract_ref=baseline["contract_ref"])
        use = success(12004, use_request)
        check("baseline_use_has_full_binding", use.get("use") is True and use.get("ci_eligible") is False)

        # 旧runを保持したまま、比較契約候補だけを次世代として保存する。
        # preflightは候補の採択や実行を意味せず、OS認証済みvalidatorのfresh照会である。
        transition_contract = deepcopy(contract)
        transition_contract["contract_id"] = "fixture-contract-transition-baseline-runtime"
        transition_contract["generation"] = 2
        transition_contract["comparison"] = {
            "mode": "required", "baseline_ref": baseline_ref,
            "reason": None, "changed_axes": [],
        }
        transition_proposal = _request(
            "contract_propose", "contract-propose-transition-baseline-runtime",
            proposal_id="contract-proposal-transition-baseline-runtime",
            series_id="fixture-contract-series", expected_generation=1,
            contract=transition_contract)
        check("transition_candidate_proposal_role_rejected", denied(
            12002, transition_proposal, "AUTHORITY_DENIED"))
        transition_saved = success(12001, transition_proposal)
        check("transition_proposal_is_distinct_generation", (
            transition_saved.get("generation") == 2
            and transition_saved.get("proposal_id") == transition_proposal["proposal_id"]
            and transition_saved.get("proposal_digest") == content_ref(
                "evaluation_contract", transition_contract["contract_id"], transition_contract)["digest"]))
        old_status_before_preflight = success(12004, _request(
            "run_status", "run-status-before-contract-preflight-baseline-runtime", run_id=run_id))

        runtime.restart_broker()
        check("baseline_adoption_replay_survives_broker_restart",
              call(12001, baseline_adoption_request) == baseline_adopted)
        restarted_current = success(12004, _request(
            "baseline_current", "baseline-current-after-restart-baseline-runtime",
            series_id="fixture-baseline-series"))
        check("baseline_current_fresh_after_restart", restarted_current.get("valid") is True)
        restarted_use = success(12004, _request(
            "baseline_use", "baseline-use-after-restart-baseline-runtime",
            series_id="fixture-baseline-series", expected_baseline_ref=baseline_ref,
            expected_contract_ref=baseline["contract_ref"]))
        check("baseline_use_fresh_after_restart", restarted_use.get("use") is True)
        preflight_request = _request(
            "contract_preflight", "contract-preflight-baseline-runtime",
            proposal_id=transition_proposal["proposal_id"],
            baseline_series_id="fixture-baseline-series",
            expected_contract_ref=old_contract_ref,
            expected_baseline_ref=baseline_ref)
        check("preflight_candidate_role_rejected", denied(
            12002, preflight_request, "AUTHORITY_DENIED"))
        check("preflight_manager_role_rejected", denied(
            12001, preflight_request, "AUTHORITY_DENIED"))
        check("preflight_operator_role_rejected", denied(
            12004, preflight_request, "AUTHORITY_DENIED"))
        wrong_preflight_ref = dict(baseline_ref)
        wrong_preflight_ref["digest"] = "0" * 64
        check("preflight_baseline_reference_mismatch_rejected", denied(
            12003, {**preflight_request,
                    "request_id": "contract-preflight-baseline-mismatch",
                    "expected_baseline_ref": wrong_preflight_ref}, "BINDING_MISMATCH"))
        preflight = success(12003, preflight_request)
        transition = preflight.get("transition")
        source_core = {key: bound[key] for key in (
            "manifest", "contract", "plan", "policy", "registry", "case_set", "selected_controls", "ci_eligible")}
        check("contract_preflight_ready_without_adoption", (
            preflight.get("preflight_ready") is True
            and preflight.get("candidate_run_required") is True
            and preflight.get("adoption_verified") is False
            and preflight.get("ci_eligible") is False
            and preflight.get("proposal_id") == transition_proposal["proposal_id"]
            and preflight.get("proposal_digest") == transition_saved["proposal_digest"]
            and type(preflight.get("checked_at")) is int
            and type(preflight.get("permission_generation")) is int
            and type(transition) is dict
            and type(transition.get("schema_version")) is int and transition.get("schema_version") == 1
            and transition.get("kind") == "contract_transition_binding"
            and transition.get("structurally_bound") is True
            and transition.get("authority_connected") is False
            and transition.get("adoption_verified") is False and transition.get("ci_eligible") is False
            and transition.get("previous_contract_ref") == old_contract_ref
            and transition.get("next_contract_ref") == content_ref(
                "evaluation_contract", transition_contract["contract_id"], transition_contract)
            and transition.get("source_run_ref") == content_ref("run_manifest", run_id, bound["manifest"])
            and transition.get("baseline_ref") == baseline_ref
            and transition.get("baseline_record") == baseline
            and transition.get("previous_contract") == contract
            and transition.get("next_contract") == transition_contract
            and transition.get("comparison") == transition_contract["comparison"]
            and transition.get("source_bound") == source_core))
        current_after_preflight = success(12004, _request(
            "contract_current", "contract-current-after-preflight-baseline-runtime",
            series_id="fixture-contract-series"))
        check("preflight_keeps_generation_one_current", (
            current_after_preflight.get("valid") is True
            and current_after_preflight.get("generation") == 1
            and current_after_preflight.get("contract") == contract
            and current_after_preflight.get("proposal_id") == proposal["proposal_id"]))
        status_after_preflight = success(12004, _request(
            "run_status", "run-status-after-contract-preflight-baseline-runtime", run_id=run_id))
        status_before_comparable = {key: value for key, value in old_status_before_preflight.items()
                                    if key not in {"action", "request_id"}}
        status_after_comparable = {key: value for key, value in status_after_preflight.items()
                                   if key not in {"action", "request_id"}}
        check("old_run_status_unchanged_by_preflight", status_before_comparable == status_after_comparable)
        replayed_final = success(12004, _request(
            "evidence_finalize", "evidence-finalize-baseline-runtime", run_id=run_id))
        check("old_run_receipts_unchanged_by_preflight", replayed_final == finalized)
        check("preflight_does_not_enable_generation_two_validation", denied(
            12003, _request("contract_validate", "contract-validate-transition-blocked",
                           proposal_id=transition_proposal["proposal_id"],
                           validation_id="contract-validation-transition-blocked"), "PREREQUISITE_UNAVAILABLE"))
        bad_ref = dict(baseline_ref)
        bad_ref["digest"] = "0" * 64
        check("baseline_full_reference_mismatch_rejected", denied(
            12004, _request("baseline_use", "baseline-use-mismatch-baseline-runtime",
                            series_id="fixture-baseline-series", expected_baseline_ref=bad_ref,
                            expected_contract_ref=baseline["contract_ref"]), "BINDING_MISMATCH"))
        check("candidate_baseline_role_rejected", denied(
            12002, _request("baseline_current", "baseline-current-candidate-baseline-runtime",
                            series_id="fixture-baseline-series"), "AUTHORITY_DENIED"))

        after_candidate_revocation = None
        if args.candidate_runs:
            from tools.candidate_runtime_checks import verify
            after_candidate_revocation = verify(success=success, denied=denied, check=check, runtime=runtime, runner=runner,
                prepared=prepared, preflight_request=preflight_request, transition=transition,
                receipts=receipts, active=active, save_observations=save_observations,
                adopt=args.candidate_adoption, regression=args.regression, cancellation=args.cancellation, call=call)
        success(12004, _request("evidence_revoke", "evidence-revoke-baseline-runtime", run_id=run_id))
        if after_candidate_revocation is not None:
            after_candidate_revocation()
        revoked_evidence = success(12004, _request(
            "evidence_current", "evidence-current-after-revoke-baseline-runtime",
            run_id=run_id, expected_bundle_digest=evidence_opened["bundle_digest"]))
        check("evidence_revocation_is_reported", "EVIDENCE_REVOKED" in revoked_evidence.get("reasons", []))
        check("same_preflight_request_rechecks_current_generation" if args.candidate_adoption
              else "same_preflight_request_rechecks_revocation", denied(
            12003, preflight_request, "BINDING_MISMATCH" if args.candidate_adoption else "PREREQUISITE_UNAVAILABLE"))
        revoked = success(12004, _request("baseline_use", "baseline-use-revoked-baseline-runtime",
                                        series_id="fixture-baseline-series",
                                        expected_baseline_ref=baseline_ref,
                                        expected_contract_ref=baseline["contract_ref"]))
        check("revocation_blocks_baseline_use", revoked.get("use") is False
              and revoked.get("valid") is False and revoked.get("reason") == "PREREQUISITE_UNAVAILABLE")
        success(12004, _request("baseline_revoke", "baseline-revoke-baseline-runtime",
                              series_id="fixture-baseline-series"))
        baseline_revoked = success(12004, use_request)
        check("baseline_revocation_is_current", baseline_revoked.get("use") is False
              and baseline_revoked.get("reason") == "BASELINE_REVOKED")
        with ExecutionJournal(output / "execution.sqlite") as journal:
            check("execution_journal_has_no_pending", not journal.pending())
    except Exception as error:
        failure = type(error).__name__
        detail = getattr(error, "detail", None)
        if isinstance(error, AuthorityRuntimeError) and type(detail) is dict:
            failure_detail = {k: detail[k] for k in ("operation", "exit_code", "reason", "client_reason") if k in detail}
    finally:
        if runner is not None:
            recovery_results: list[bool] = []
            for binding in active:
                try:
                    recovered = runner.recover(binding["run_id"], binding["operation_id"])
                    recovery_results.append(recovered.get("stop_confirmed") is True
                                            and recovered.get("cleanup_confirmed") is True)
                except Exception:
                    recovery_results.append(False)
            checks["fixture_recovery_confirmed"] = all(recovery_results)
        if runtime is not None:
            names = list(runtime.state.get("containers", []))
            try:
                runtime.cleanup(remove_state=False)
            except Exception:
                checks["authority_cleanup"] = False
            else:
                checks["authority_cleanup"] = True
            for name in names:
                try:
                    runtime._confirm_absent(name)
                except Exception:
                    checks["authority_cleanup_rechecked"] = False
                    break
            else:
                checks["authority_cleanup_rechecked"] = True
        try:
            if not source_hashes:
                source_hashes = sources()
            checks["tested_sources_unchanged"] = sources() == source_hashes
        except Exception:
            checks["tested_sources_unchanged"] = False

    result = {
        "schema_version": 1,
        "kind": "baseline_runtime_result",
        "passed": failure is None and bool(checks) and all(checks.values()),
        "checks": checks,
        "failure_type": failure,
        "failure_detail": failure_detail,
        "source_sha256": source_hashes,
        "image_id": runtime.lock["image_id"] if runtime is not None else None,
        "fixture_image_id": runner.lock["image_id"] if runner is not None else None,
        "entry_count": 91 if args.cancellation else (90 if args.regression else (60 if args.candidate_runs else 15)),
        "baseline_entry_count": 15,
        "candidate_entry_counts": {"old": 15, "new": 30} if args.candidate_runs else {},
        "candidate_runs_enabled": args.candidate_runs,
        "candidate_adoption_enabled": args.candidate_adoption,
        "regression_enabled": args.regression,
        "cancellation_enabled": args.cancellation,
        "synthetic_fixture_only": True,
        "full_mvp": False,
        "ci_eligible": False,
        "persistent_state_retained": True,
    }
    _write(output / "check.json", result)
    print(json.dumps({"passed": result["passed"], "checks": len(checks),
                      "failure_type": failure}), flush=True)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
