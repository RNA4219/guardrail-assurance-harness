"""32受入の関連証拠と未完了を対応付ける。対応表を受入成功にしない。"""
from pathlib import Path
import hashlib
import json
import re
ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs/evidence/mvp-baseline-refresh-20260912"
MODULES = {
 1: ("registry", "evaluation_authority"), 2: ("run_contracts", "baseline_refresh", "contract_updates"),
 3: ("docker_runner", "fixture_worker"), 4: ("resources", "resource_authority", "cancel_claim"),
 5: ("normalized", "run_evidence_book"), 6: ("constraint_evidence", "fixture_admission"),
 7: ("normalized", "fixture_worker"), 8: ("promptfoo_adapter", "normalized"),
 9: ("decision", "llm_evaluator", "run_evidence"), 10: ("run_evidence", "decision"),
 11: ("baselines", "baseline_refresh", "contract_updates"), 12: ("decision", "core_integration"),
 13: ("baseline_source", "baseline_refresh", "run_evidence_book"), 14: ("run_cancellation", "cancel_claim"),
 15: ("remediation", "regression_integration"), 16: ("remediation", "regression_integration"),
 17: ("remediation", "llm_evaluator"), 18: ("execution_journal", "run_cancellation", "cancel_claim"),
 19: ("run_report", "regression_gate"), 20: ("regression_gate", "regression_integration"),
 21: ("llm_evaluator", "normalized", "promptfoo_adapter"), 22: ("artifacts", "baseline_refresh"),
 23: ("fixture_calibration", "measurement_calibration", "docker_runner"),
 24: ("run_report", "run_evidence_book"), 25: ("management", "baseline_refresh", "transition_acceptance_integration"),
 26: ("run_cancellation", "cancel_claim", "execution_journal"), 27: ("registry", "regression_gate"),
 28: ("evaluation_data", "measurement_calibration"), 29: ("run_evidence", "decision"),
 30: ("llm_evaluator", "synthetic_llm_runner"), 31: ("evaluation_authority", "run_evidence_book"),
 32: ("remediation",),
}
SPECS = {
 "contract-revision-detail-spec.md": {1,2,6,7,11,13,18,23,24,25,28,31},
 "llm-authority-detail-spec.md": {2,3,4,5,8,9,10,12,13,14,17,19,21,23,24,28,29,30,31},
 "supervised-run-detail-spec.md": {1,4,6,7,10,14,18,20,23,26,27,30,31},
 "retention-revalidation-detail-spec.md": {13,15,16,18,21,22,23,25,31,32},
}
RUNTIME_PREFIXES = {
 1: ("regression_fixed_",), 2: ("baseline_refresh_", "regression_fixed_"),
 3: ("fixture_recovery_", "runtime_cleanup_"), 4: ("regression_thirty_", "recovery_", "cancellation_"),
 5: ("regression_required_",), 6: ("candidate_old_",), 7: ("candidate_new_",),
 8: (), 9: (), 10: ("regression_incomplete_",), 11: ("baseline_refresh_",),
 12: ("regression_fresh_ci_",), 13: ("baseline_refresh_dependency_", "regression_source_revocation_"),
 14: ("recovery_", "cancellation_"), 15: ("regression_findings_",), 16: ("regression_plans_",),
 17: (), 18: ("regression_historical_", "recovery_restart_"),
 19: ("regression_required_", "regression_outputs_"), 20: ("regression_consumer_",),
 21: (), 22: ("evidence_revocation_", "baseline_revocation_"),
 23: ("recovery_",), 24: ("regression_outputs_", "baseline_refresh_keeps_"),
 25: ("baseline_refresh_propose_role_", "baseline_refresh_proposer_", "baseline_refresh_generation_"),
 26: ("cancellation_", "recovery_"), 27: ("regression_fixed_",),
 28: (), 29: (), 30: (), 31: ("candidate_baseline_role_", "baseline_refresh_propose_role_"), 32: (),
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    verification = json.loads((OUT / "verification.json").read_text("utf-8"))
    runtime = json.loads((OUT / "runtime-check.json").read_text("utf-8"))
    assert verification["passed"] and not verification["full_mvp_accepted"]
    definitions = {}
    for line in (ROOT / "docs/acceptance-criteria.md").read_text("utf-8").splitlines():
        match = re.match(r"\| GAH-AC(\d{2}) \| GAH-R(\d{2}) \|", line)
        if match:
            assert match[1] == match[2]
            definitions[int(match[1])] = hashlib.sha256(line.encode()).hexdigest()
    audit = {}
    for line in (ROOT / "docs/mvp-completion-audit.md").read_text("utf-8").splitlines():
        match = re.match(r"\| R(\d{2}) ([^|]+)\| ([^|]+)\| ([^|]+)\|$", line)
        if match:
            audit[int(match[1])] = [value.strip() for value in match.groups()[1:]]
    assert set(definitions) == set(audit) == set(MODULES) == set(range(1,33))
    rows = []
    for number in range(1,33):
        prefixes = tuple("test_" + name + "." for name in MODULES[number])
        tests = [name for name in verification["tests"] if name.startswith(prefixes)]
        assert tests, number
        runtime_checks = [name for name,value in runtime["checks"].items()
            if value is True and name.startswith(RUNTIME_PREFIXES[number])]
        rows.append({"requirement_id": f"GAH-R{number:02d}", "acceptance_id": f"GAH-AC{number:02d}",
            "definition_row_sha256": definitions[number], "title": audit[number][0],
            "current_implementation_scope": audit[number][1], "remaining": audit[number][2],
            "status": "partial", "product_acceptance_passed": False,
            "related_unit_tests": tests, "related_runtime_checks": runtime_checks,
            "pending_connection_specifications": ["docs/" + path for path,ids in SPECS.items() if number in ids]})
    result = {"schema_version": 1, "kind": "mvp_acceptance_evidence_map", "verification_sha256": sha(OUT / "verification.json"),
        "requirements_source_sha256": sha(ROOT / "docs/requirements.md"),
        "acceptance_source_sha256": sha(ROOT / "docs/acceptance-criteria.md"),
        "audit_source_sha256": sha(ROOT / "docs/mvp-completion-audit.md"),
        "mapping_meaning": "関連する実行済み試験への対応であり、受入条件の全操作を実行済みとするものではない。",
        "requirements": rows, "requirements_tracked": 32, "requirements_accepted": 0,
        "full_mvp_accepted": False, "release_gate": "no_go", "ci_eligible": False}
    with (OUT / "acceptance-map.json").open("x", encoding="utf-8") as stream:
        json.dump(result,stream,ensure_ascii=False,indent=2)
    print(json.dumps({k:result[k] for k in ("requirements_tracked","requirements_accepted","full_mvp_accepted")}))


if __name__ == "__main__":
    main()
