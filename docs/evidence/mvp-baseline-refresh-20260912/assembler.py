"""baseline更新の実行結果と固定参照の検証を不変packetへまとめる。"""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
LOCAL = Path(__file__).resolve().parent
OUT = ROOT / "docs/evidence/mvp-baseline-refresh-20260912"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
from gah.evaluation_authority import EvaluationExtension
from tools.gah_ci import response_exit_code


def read(path):
    return json.loads(path.read_text("utf-8"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    units = read(LOCAL / "units-reviewed/check.json")
    runtime = read(LOCAL / "runtime-02/check.json")
    executions = read(LOCAL / "runtime-02/execution-receipts.json")
    previous = read(ROOT / "docs/evidence/mvp-recovery-20260912/runtime-check.json")
    primary = read(LOCAL / "privacy-elevated.json")
    supplement = read(LOCAL / "privacy-supplement.json")
    sources = dict(units["source_sha256"])
    for path, digest in runtime["source_sha256"].items():
        assert path not in sources or sources[path] == digest
        sources[path] = digest
    counts = Counter(r["normalized_result"]["binding"]["run_id"] for r in executions)
    observed = read(LOCAL / "runtime-02/observations.json")
    ci_calls = [item for item in observed if item["action"] == "ci_check"]
    ci_results = [item["response"] for item in ci_calls]
    ci_codes = [r["exit_code"] for r in ci_results]
    validated_codes = [response_exit_code({key: r[key] for key in
        ("request_id", "run_id", "expected_manifest_ref")}, r) for r in ci_results]
    missing_checks = sorted(set(previous["checks"]) - set(runtime["checks"]))
    target_log = (LOCAL / "units-reviewed/test_baseline_refresh.log").read_text("utf-8")
    target_summary = re.search(r"Ran (\d+) tests in ([0-9.]+)s", target_log)
    checks = {
        "ten_refresh_tests_passed": target_summary is not None and int(target_summary[1]) == 10
            and target_log.rstrip().endswith("OK") and sum(t.startswith("test_baseline_refresh.") for t in units["tests"]) == 10,
        "full_560_then_focused_44_verified_with_previous_551_retained": units["passed"] and units["test_counts"] == {"total": 561, "previous_retained": 551, "new": 10},
        "fifteen_refresh_runtime_checks_passed": sum(k.startswith("baseline_refresh_") for k in runtime["checks"]) == 15
            and all(v for k,v in runtime["checks"].items() if k.startswith("baseline_refresh_")),
        "all_runtime_checks_passed": runtime["passed"] and all(runtime["checks"].values())
            and all(runtime[key] for key in ("regression_enabled", "cancellation_enabled", "recovery_enabled", "baseline_refresh_enabled")),
        "previous_runtime_assertions_retained": len(previous["checks"]) == 233 and not missing_checks,
        "ninety_two_actual_executions": len(executions) == 92 and counts == {
            "baseline-runtime-run": 15, "candidate-old-runtime": 15, "candidate-new-runtime": 30,
            "regression-runtime": 30, "cancellation-runtime": 1, "recovery-runtime": 1}
            and len({r["normalized_result"]["binding"]["operation_id"] for r in executions}) == 92,
        "all_executions_stopped_and_cleaned": all(r["execution_status"] == "COMPLETED"
            and all(r[k] is True for k in ("stop_confirmed", "cleanup_confirmed", "isolation_config_verified")) for r in executions),
        "fresh_ci_sequences_match_and_strict_responses_valid": ci_codes == validated_codes
            and all(item["uid"] == 12004 for item in ci_calls)
            and all([r["exit_code"] for r in ci_results if r["run_id"] == run_id] == expected
                for run_id, expected in {"regression-runtime": [2, 0, 0, 0, 0, 0, 1, 1, 1],
                    "cancellation-runtime": [2, 3, 3, 3, 3, 3], "recovery-runtime": [2, 3, 3]}.items())
            and all(len({json.dumps(r["expected_manifest_ref"],sort_keys=True) for r in ci_results if r["run_id"]==run_id})==1
                for run_id in ("regression-runtime", "cancellation-runtime", "recovery-runtime")),
        "current_sources_match": all(sha(ROOT / p) == d for p,d in sources.items()),
        "seven_protected_sources_match": len(units["preserved"]) == 7 and all(
            sha(ROOT / p) == v["actual_sha256"] for p,v in units["preserved"].items()),
        "privacy_read_errors_supplemented": primary["private_reference_matches"] == 0
            and primary["current_product_scan"]["read_errors"] == 1 and supplement["passed"]
            and primary["unreadable_paths"] == [supplement["scope"]]
            and supplement["primary_scan_sha256"] == sha(LOCAL / "privacy-elevated.json")
            and supplement["combined_read_errors"] == 0,
        "full_mvp_not_claimed": units["full_mvp_accepted"] is False and runtime["full_mvp"] is False,
    }
    assert all(checks.values()), checks
    assert OUT.is_dir() and {p.name for p in OUT.iterdir()} == {"README.md"}
    files = {"assembler.py": Path(__file__), "unit-checker.py": LOCAL / "verify_unit_review.py",
        "initial-unit-checker.py": LOCAL / "verify_units.py",
        "initial-unit-check.json": LOCAL / "units-01/check.json",
        "unit-check.json": LOCAL / "units-reviewed/check.json", "unittest.log": LOCAL / "units-01/unittest.log",
        "target-tests-02.txt": LOCAL / "target-tests-02.txt",
        "authority-runtime.lock.json": ROOT / "config/authority-runtime.lock.json",
        "fixture-runtime.lock.json": ROOT / "config/fixture-runtime.lock.json",
        "baseline-refresh-spec.txt": ROOT / "docs/baseline-refresh-detail-spec.md",
        "privacy-primary.json": LOCAL / "privacy-elevated.json", "privacy-supplement.json": LOCAL / "privacy-supplement.json",
        "privacy-native-reader.ps1": LOCAL / "privacy_native_read.ps1",
        "privacy-supplement-checker.py": LOCAL / "verify_privacy_supplement.py", "before.json": LOCAL / "before.json"}
    files["initial-runtime-check.json"] = LOCAL / "runtime-01/check.json"
    files["pointer-before.json"] = LOCAL / "pointer-before.json"
    files["pointer-before-checker.py"] = LOCAL / "check_pointer_before.py"
    files["pointer-correction.py"] = LOCAL / "apply_pointer_review.py"
    files["pointer-review.diff"] = LOCAL / "pointer-review.diff"
    files["pointer-review-before.json"] = LOCAL / "pointer-review-before.json"
    for path in (LOCAL / "units-reviewed").glob("*.log"):
        files["focused-" + path.name] = path
    for path in (LOCAL / "pointer-review-before").rglob("*.py"):
        files["before-review-" + path.name] = path
    for name in ("check.json", "observations.json", "execution-receipts.json", "manifest.json", "deployment.json"):
        files["runtime-" + name] = LOCAL / "runtime-02" / name
    for path in ("src/gah/baseline_generations.py", "src/gah/baseline_authority.py", "src/gah/evaluation_authority.py",
            "src/gah/adoption_migrations.py", "tools/prepare_authority_runtime.py", "tools/candidate_runtime_checks.py",
            "tools/verify_baseline_runtime.py", "tools/baseline_refresh_runtime_checks.py", "tests/test_baseline_refresh.py"):
        files[path.split("/")[0] + "-" + Path(path).name] = ROOT / path
    for name,path in files.items():
        with (OUT / name).open("xb") as stream:
            stream.write(path.read_bytes())
    record = {"schema_version": 1, "checked_at": datetime.now(timezone.utc).isoformat(),
        "extension_digest": EvaluationExtension.digest, "passed": True, "checks": checks,
        "test_counts": units["test_counts"], "tests": units["tests"], "runtime_check_count": len(runtime["checks"]),
        "unit_verification_mode": units["verification_mode"], "initial_full_tests": units["initial_full_tests"],
        "focused_test_executions": units["focused_test_executions"], "total_test_executions": units["total_test_executions"],
        "reviewed_source_changes": units["reviewed_source_changes"],
        "previous_runtime_checks_retained": len(previous["checks"]), "missing_previous_runtime_checks": missing_checks,
        "runtime_fixed_reference_api_change": "With --baseline-refresh, the final gen1 use/revoke checks use baseline_resolve/baseline_revoke_ref after current advances to gen2.",
        "actual_execution_counts": dict(counts), "observed_ci_exit_codes": ci_codes,
        "image_id": runtime["image_id"], "fixture_image_id": runtime["fixture_image_id"],
        "source_sha256": sources, "preserved": units["preserved"], "artifact_sha256": {name: sha(OUT / name) for name in files},
        "parent_takeover_requested": True, "reviewer": "Codex parent", "external_model_review_performed_this_stage": False,
        "fixed_uc_ci_current_use_connected": True, "normal_cancellation_connected": True,
        "expired_owner_cancellation_connected": True, "baseline_refresh_connected": True,
        "full_mvp_accepted": False, "release_gate": "no_go", "ci_eligible": False,
        "limitations": ["自作の固定UC-CI、contract 2、baseline 1から2への更新に限る。",
            "後続契約と条件変更、UC-LLM認証資源、製品監督、保持、Finding修復確認と全32受入は残る。",
            "検証packetは現在の製品CIの成功根拠ではない。毎回brokerへ問い合わせる。"]}
    with (OUT / "verification.json").open("x", encoding="utf-8") as stream:
        json.dump(record, stream, ensure_ascii=False, indent=2)
    print(json.dumps({k: record[k] for k in ("passed", "test_counts", "runtime_check_count", "actual_execution_counts")}))


if __name__ == "__main__":
    main()
