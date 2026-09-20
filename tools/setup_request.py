"""完了済みsetupから既存CLIの要求pathを解決する。現在CIの許可は発行しない。"""
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gah.contracts import ContractError, require_object
from gah.operations import SETUP_PLAN_KIND, validate_setup_payload
from gah.productization import read_document, validate_plan, workspace_path
from gah.run_contracts import content_ref


def resolve(plan_path, *, root=ROOT):
    root = Path(root)
    # setupを再適用せず、完了済み要求を読む。開始時の期限・権限は既存authorityでfresh照合する。
    plan = validate_plan(read_document(root, plan_path), kind=SETUP_PLAN_KIND,
                         payload_validator=validate_setup_payload, now=None)
    payload = plan["payload"]
    workspace = Path(payload["workspace_path"])
    if not workspace.is_relative_to(root): raise ContractError("PATH_REJECTED")
    result = read_document(workspace, ".ga/operations/setup/" + plan["id"] + "/result.json")
    require_object(result, {"schema_version", "kind", "id", "plan_ref", "source_ref", "profile", "runtime_ref",
        "contract_ref", "baseline_ref", "initial_run_ref", "candidate_run_refs", "run_request_ref", "ci_request_ref",
        "output_paths", "ready_ref", "current_ci_checked", "ci_eligible"})
    if (type(result["schema_version"]) is not int or result["schema_version"] != 1
            or result["kind"] != "setup_result" or result["id"] != plan["id"]
            or result["plan_ref"] != content_ref(SETUP_PLAN_KIND, plan["id"], plan)
            or result["source_ref"] != plan["source_ref"] or result["profile"] != payload["profile"]
            or result["output_paths"] != payload["output_paths"] or result["ci_eligible"] is not False
            or result["current_ci_checked"] is not False):
        raise ContractError("BINDING_MISMATCH")
    paths = {key: workspace_path(workspace, path) for key, path in payload["output_paths"].items()}
    state = read_document(workspace, paths["runtime"] / "deployment.json")
    require_object(state, {"prefix", "image_id", "containers"})
    if result["runtime_ref"] != content_ref("runtime_deployment", state["prefix"], {k: state[k] for k in ("prefix", "image_id")}):
        raise ContractError("BINDING_MISMATCH")
    run = read_document(workspace, paths["run_request"])
    ci = read_document(workspace, paths["ci_request"])
    from gah.supervised_run import validate_input
    from gah.regression_runs import validate_request
    validate_input(run); validate_request(ci)
    if (ci["action"] != "ci_check" or run["run_id"] != ci["run_id"]
            or ci["expected_use_cases"] != (["UC-LLM"] if payload["profile"] == "sample-llm" else ["UC-CI"])
            or run["expected_contract_ref"] != result["contract_ref"]
            or ci["expected_contract_ref"] != result["contract_ref"] or ci["expected_baseline_ref"] != result["baseline_ref"]
            or result["run_request_ref"] != content_ref("run_request", run["run_id"], run)
            or result["ci_request_ref"] != content_ref("ci_request", ci["request_id"], ci)):
        raise ContractError("BINDING_MISMATCH")
    return {**paths, "runner": "guardrail" if payload["profile"] == "sample-llm" else "fixture"}
