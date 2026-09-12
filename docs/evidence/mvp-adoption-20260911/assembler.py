"""初回固定baseline接続の最終証拠を、新規の証跡ディレクトリへ固定する。"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOCAL = Path(__file__).resolve().parent
DEST = ROOT / "docs/evidence/mvp-adoption-20260911"


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


unit = load(LOCAL / "units-04/check.json")
baseline = load(LOCAL / "baseline-runtime-01/check.json")
authority = load(LOCAL / "authority-runtime-01/check.json")
runtime = load(LOCAL / "evidence-runtime-06/check.json")
observations = load(LOCAL / "baseline-runtime-01/observations.json")
receipts = load(LOCAL / "baseline-runtime-01/execution-receipts.json")
prepared = next(item["response"] for item in observations if item["action"] == "fixture_prepare")
calibration = prepared["calibration"]
qwen = load(LOCAL / "qwen-review-01/receipt.json")
lock = load(ROOT / "config/authority-runtime.lock.json")
checks = {
    "unit_441_passed_previous_357_retained": unit["passed"] and unit["test_counts"] == {"total": 441, "previous_retained": 357, "new": 84},
    "baseline_docker_40": baseline["passed"] and len(baseline["checks"]) == 40 and all(baseline["checks"].values()),
    "authority_docker_22": authority["passed"] and len(authority["checks"]) == 22 and all(authority["checks"].values()),
    "evidence_docker_41": runtime["passed"] and len(runtime["checks"]) == 41 and all(runtime["checks"].values()),
    "same_authority_image": all(item["image_id"] == lock["image_id"] for item in (baseline, authority, runtime)),
    "same_fixture_image": baseline["fixture_image_id"] == runtime["fixture_image_id"] == load(ROOT / "config/fixture-runtime.lock.json")["image_id"],
    "actual_fifteen_clean_docker_receipts": len(receipts) == 15 and all(
        item["execution_status"] == "COMPLETED" and item["stop_confirmed"] is True
        and item["cleanup_confirmed"] is True and item["isolation_config_verified"] is True for item in receipts),
    "fixture_measurement_calibration_36": calibration["passed"] is True and calibration["vector_count"] == 36,
    "requirements_policy_examples_preserved": all(digest(ROOT / path) == value["actual_sha256"] for path, value in unit["preserved"].items()),
    "qwen_design_review_complete": qwen["status"] == "complete" and qwen["finish_reason"] == "stop"
        and digest(LOCAL / "qwen-review-01/submitted-spec.md") == qwen["submitted_sha256"]
        and digest(LOCAL / "qwen-review-01/response-envelope.json") == qwen["response_sha256"]
        and digest(LOCAL / "qwen-review-01/response.txt") == qwen["response_text_sha256"],
}
sources = {}
for record in (unit, baseline, authority, runtime):
    for path, value in record["source_sha256"].items():
        assert path not in sources or sources[path] == value
        sources[path] = value
checks["all_tested_sources_match_current"] = all(digest(ROOT / path) == value for path, value in sources.items())
assert all(checks.values()), checks
files = {
    "unit-check.json": "units-04/check.json", "unittest.log": "units-04/unittest.log",
    "unit-checker.py": "verify_units_04.py", "baseline-check.json": "baseline-runtime-01/check.json",
    "baseline-observations.json": "baseline-runtime-01/observations.json",
    "baseline-execution-receipts.json": "baseline-runtime-01/execution-receipts.json",
    "baseline-deployment.json": "baseline-runtime-01/deployment.json",
    "authority-check.json": "authority-runtime-01/check.json", "authority-observations.json": "authority-runtime-01/observations.json",
    "authority-deployment.json": "authority-runtime-01/deployment.json",
    "runtime-check.json": "evidence-runtime-06/check.json", "runtime-observations.json": "evidence-runtime-06/observations.json",
    "runtime-execution-receipts.json": "evidence-runtime-06/execution-receipts.json",
    "runtime-deployment.json": "evidence-runtime-06/deployment.json",
    "qwen-review-receipt.json": "qwen-review-01/receipt.json",
    "qwen-review-submitted.md": "qwen-review-01/submitted-spec.md",
    "qwen-review-response.json": "qwen-review-01/response-envelope.json",
    "qwen-review-response.txt": "qwen-review-01/response.txt",
    "unit-in-progress-failure.log": "unit-all-01.log",
    "unit-parser-check-01.json": "units-03/check.json",
    "unit-parser-check-02.json": "units-03/check-corrected.json",
    "unit-parser-corrected.json": "units-03/check-corrected-02.json",
    "unit-before-readiness.log": "units-03/unittest.log",
}
for number in range(1, 6):
    files[f"prior-runtime-check-{number:02d}.json"] = f"evidence-runtime-{number:02d}/check.json"
for number in (1, 2):
    files[f"startup-diagnostic-{number:02d}.json"] = f"startup-{number:02d}/check.json"
for value in files.values():
    assert (LOCAL / value).is_file()
DEST.mkdir(exist_ok=False)
for name, source in files.items():
    with (DEST / name).open("xb") as stream:
        stream.write((LOCAL / source).read_bytes())
for name, path in {"authority-runtime.lock.json": ROOT / "config/authority-runtime.lock.json",
                   "fixture-runtime.lock.json": ROOT / "config/fixture-runtime.lock.json",
                   "assembler.py": Path(__file__)}.items():
    with (DEST / name).open("xb") as stream:
        stream.write(path.read_bytes())
record = {"schema_version": 1, "checked_at": datetime.now(timezone.utc).isoformat(), "status": "passed",
    "scope": "fixed_uc_ci_initial_baseline_adoption_and_same_db_evidence",
    "full_mvp_accepted": False, "release_gate": "no_go", "ci_eligible": False,
    "checks": checks, "test_counts": unit["test_counts"], "tests": unit["tests"],
    "source_sha256": sources, "preserved": unit["preserved"],
    "image_id": lock["image_id"], "fixture_image_id": baseline["fixture_image_id"],
    "fixture_case_count": 15, "measurement_calibration_vectors": 36,
    "evidence_sha256": {path.name: digest(path) for path in sorted(DEST.iterdir())},
    "history_disposition": {
        "unit_in_progress": "additional test had an adopted_at lookup error; corrected before final all-tests run",
        "unit_parser": "436 tests succeeded; two parser checks failed on docstrings and CRLF; recheck and final 441-test run retained",
        "runtime_01": "Docker permission boundary; no product acceptance",
        "runtime_02": "constraint-only Decision unavailable; fixed and repeated",
        "runtime_03_04": "early Docker command failures; exact startup cause not established; diagnostics retained; explicit readiness added",
        "runtime_05": "41 passed before readiness helper; superseded by runtime 06 with current supervisor",
    },
    "limitations": ["initial fixed UC-CI generation 1 only", "no generation 2 or old-condition regression acceptance",
        "no authenticated model dispatch or model-resource integration", "model weight revision unresolved",
        "no persistent full orchestrator, Finding/Plan closure or normal CI acceptance",
        "Qwen output is design consultation only; calibration 36 is fixture measurement, not LLM target performance"]}
with (DEST / "verification.json").open("x", encoding="utf-8") as stream:
    json.dump(record, stream, indent=2)
    stream.write("\n")
print(json.dumps({"status": record["status"], "checks": checks, "test_counts": unit["test_counts"], "file_count": len(list(DEST.iterdir()))}))
