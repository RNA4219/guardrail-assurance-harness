"""明示実行専用。自作の固定fixtureを実Dockerで検証し、正規化済み証跡を保存する。"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gah.docker_runner import DockerRunner
from gah.execution_journal import ExecutionJournal


def binding_for(runner, scenario, run_id, operation_id):
    return {"run_id": run_id, "operation_id": operation_id, "owner_epoch": 1,
        "contract_digest": "1" * 64, "target_digest": runner.target_digest(scenario),
        "obligation_id": "fixed-fixture", "case_id": operation_id, "trial_id": "trial-1",
        "stage_id": "fixture-acceptance", "fixture_digest": runner.lock["worker_digest"],
        "adapter_digest": runner.adapter_digest, "policy_digest": "2" * 64,
        "evaluator_digest": runner.lock["worker_digest"], "isolation_digest": runner.isolation_digest}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--suite", choices=("smoke", "full"), default="smoke")
    args = parser.parse_args()
    folder = Path(args.output).resolve()
    if not folder.is_relative_to(ROOT):
        raise SystemExit("OUTPUT_OUTSIDE_WORKSPACE")
    folder.mkdir(parents=True, exist_ok=False)
    journal_path = folder / "execution.sqlite"
    runner = DockerRunner(ROOT / "config/fixture-runtime.lock.json", journal_path)
    run_id = "fixture-" + uuid.uuid4().hex
    cases = [("probe:isolation", "COMPLETED", None)]
    for number in range(1, 11 if args.suite == "full" else 2):
        cases += [(f"constraint:C{number:02d}:good", "COMPLETED", "PASS"),
                  (f"constraint:C{number:02d}:bad", "COMPLETED", "FAIL")]
    for number in range(1, 6 if args.suite == "full" else 2):
        cases += [(f"mutation:F{number:02d}:healthy", "COMPLETED", "KILLED"),
                  (f"mutation:F{number:02d}:decayed", "COMPLETED", "SURVIVED")]
    if args.suite == "full":
        cases += [("probe:oversized", "FAILED", "OUTPUT_TOO_LARGE"),
                  ("probe:malformed", "FAILED", "OUTPUT_REJECTED"),
                  ("probe:rejected_marker", "FAILED", "OUTPUT_REJECTED"),
                  ("probe:crash", "FAILED", "EXECUTION_FAILURE"),
                  ("probe:child_timeout", "TIMEOUT", "TIMEOUT")]
    receipts, checks = [], []
    for index, (scenario, expected_status, expected_value) in enumerate(cases):
        binding = binding_for(runner, scenario, run_id, f"operation-{index:03d}")
        timeout = 6 if scenario == "probe:child_timeout" else 60
        receipt = runner.run(scenario, binding, run_deadline=int(time.time()) + 120, timeout_seconds=timeout)
        receipts.append(receipt)
        actual = receipt["execution_status"]
        passed = actual == expected_status and receipt["stop_confirmed"] and receipt["cleanup_confirmed"]
        passed = passed and receipt["isolation_config_verified"] and receipt["ci_eligible"] is False
        normalized = receipt["normalized_result"]
        if scenario.startswith("constraint:"):
            value = normalized["observation"] if normalized else None
        elif scenario.startswith("mutation:"):
            value = normalized["mutation_outcome"] if normalized else None
        elif expected_status != "COMPLETED":
            value = receipt["reason"]
            passed = passed and normalized is None and receipt["probe_result"] is None
        else:
            value = None
            passed = passed and receipt["probe_result"] is not None and all(receipt["probe_result"]["checks"].values())
        passed = bool(passed and value == expected_value)
        checks.append({"scenario": scenario, "passed": passed, "status": actual, "value": value})
        print(json.dumps(checks[-1]), flush=True)
        (folder / "receipts.json").write_text(json.dumps(receipts, indent=2) + "\n", encoding="utf-8")
        if not passed:
            break
    with ExecutionJournal(journal_path) as journal:
        pending = journal.pending()
    checked_paths = [folder / "receipts.json", journal_path,
                     Path(str(journal_path) + "-wal"), Path(str(journal_path) + "-shm")]
    rejected_absent = all(b"GAH_SYNTHETIC_REJECTED_MARKER" not in path.read_bytes()
                          for path in checked_paths if path.exists()) if args.suite == "full" else None
    source_paths = ["src/gah/docker_runner.py", "src/gah/normalized.py", "src/gah/execution_journal.py",
                    "fixtures/runtime/fixture_worker.py", "config/fixture-runtime.lock.json", "tools/verify_fixture_runtime.py"]
    evidence = {"schema_version": 1, "suite": args.suite, "fixture_only": True, "full_mvp_accepted": False,
        "passed": len(checks) == len(cases) and all(item["passed"] for item in checks) and not pending and rejected_absent is not False,
        "rejected_payload_absent": rejected_absent,
        "expected_count": len(cases), "executed_count": len(checks), "checks": checks, "pending_count": len(pending),
        "image_id": runner.lock["image_id"], "source_sha256": {
            path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in source_paths}}
    (folder / "check.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": evidence["passed"], "count": len(checks), "pending": len(pending)}))
    return 0 if evidence["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
