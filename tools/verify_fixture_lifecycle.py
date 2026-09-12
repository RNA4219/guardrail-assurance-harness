"""明示実行専用。固定sleep fixtureの取消し・監督中断・回収排他を実Dockerで確認する。"""
from concurrent.futures import ThreadPoolExecutor
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gah.docker_runner import DockerRunner, RunnerError
from gah.execution_journal import ExecutionJournal, JournalError
from tools.verify_fixture_runtime import binding_for


def make_runner(folder):
    return DockerRunner(ROOT / "config/fixture-runtime.lock.json", folder / "execution.sqlite")


def child_is_running(runner, bound):
    """既知containerだけのprocess数を読み、二つの固定Python処理の存在を確認する。"""
    end = time.monotonic() + 20
    while time.monotonic() < end:
        try:
            with ExecutionJournal(runner.journal_path) as journal:
                record = journal.get(bound["run_id"], bound["operation_id"])
            container = runner._inspect(record)
            if container and container["State"]["Running"]:
                result = runner._capture(["container", "top", record["container_name"], "-eo", "pid,ppid,comm"])
                if result.reason is None and result.returncode == 0:
                    rows = result.stdout.decode("ascii").splitlines()[1:]
                    if sum(len(row.split()) == 3 and row.split()[2].startswith("python") for row in rows) >= 2:
                        return True
        except (JournalError, RunnerError, UnicodeError):
            pass
        time.sleep(0.1)
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--output")
    group.add_argument("--controller")
    args = parser.parse_args()
    folder = Path(args.output or args.controller).resolve()
    if not folder.is_relative_to(ROOT):
        raise SystemExit("OUTPUT_OUTSIDE_WORKSPACE")
    if args.controller:
        config = json.loads((folder / "controller-input.json").read_text(encoding="utf-8"))
        make_runner(folder).run("probe:child_timeout", config["binding"],
                               run_deadline=config["deadline"], timeout_seconds=60)
        return 0
    folder.mkdir(parents=True, exist_ok=False)
    runner = make_runner(folder)
    run_id = "lifecycle-" + uuid.uuid4().hex
    scenario = "probe:child_timeout"
    cancel_binding = binding_for(runner, scenario, run_id, "cancel")
    cancel = threading.Event()
    receipts = []
    checks = {}
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(runner.run, scenario, cancel_binding,
                             run_deadline=int(time.time()) + 120, timeout_seconds=60, cancel_event=cancel)
        try:
            checks["cancel_child_observed"] = child_is_running(runner, cancel_binding)
            try:
                runner.recover(run_id, "cancel")
            except RunnerError as error:
                checks["live_owner_recovery_refused"] = error.code == "OWNER_ACTIVE"
            else:
                checks["live_owner_recovery_refused"] = False
        finally:
            cancel.set()
        receipt = future.result(timeout=30)
    receipts.append(receipt)
    checks["cancel_stopped_and_removed"] = (receipt["execution_status"] == "CANCELLED"
        and receipt["stop_confirmed"] and receipt["cleanup_confirmed"]
        and receipt["normalized_result"] is None and receipt["probe_result"] is None)
    print(json.dumps({"phase": "cancel", "checks": checks}), flush=True)

    bound = binding_for(runner, scenario, run_id, "interrupted")
    deadline = int(time.time()) + 120
    (folder / "controller-input.json").write_text(json.dumps({"binding": bound, "deadline": deadline}), encoding="utf-8")
    process = subprocess.Popen([sys.executable, "-E", "-X", "utf8", "-m", "tools.verify_fixture_lifecycle", "--controller", str(folder)],
        cwd=ROOT, env=runner.environment, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    try:
        checks["interrupted_child_observed"] = child_is_running(runner, bound)
    finally:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=10)
    receipt = runner.recover(run_id, "interrupted")
    receipts.append(receipt)
    checks["interrupted_stopped_and_removed"] = (receipt["execution_status"] == "FAILED"
        and receipt["reason"] == "INTERRUPTED" and receipt["recovered"]
        and receipt["stop_confirmed"] and receipt["cleanup_confirmed"]
        and receipt["normalized_result"] is None and receipt["probe_result"] is None)
    # 一度確定した失敗は同じdispatchを再実行せず、不変receiptを返す。
    original_capture = runner._capture
    def no_dispatch(*args, **kwargs):
        raise AssertionError("UNEXPECTED_REDISPATCH")
    runner._capture = no_dispatch
    try:
        checks["recovery_replay_immutable"] = runner.recover(run_id, "interrupted") == receipt
        checks["run_replay_immutable"] = runner.run(scenario, bound, run_deadline=deadline, timeout_seconds=60) == receipt
    finally:
        runner._capture = original_capture

    for operation, event, deadline_value, expected_status in (
        ("expired", None, int(time.time()) - 1, "TIMEOUT"),
        ("pre-cancelled", cancel, int(time.time()) + 120, "CANCELLED"),
    ):
        current = binding_for(runner, scenario, run_id, operation)
        receipt = runner.run(scenario, current, run_deadline=deadline_value, timeout_seconds=60, cancel_event=event)
        receipts.append(receipt)
        checks[operation + "_never_created"] = (receipt["execution_status"] == expected_status
            and receipt["container_id"] is None and receipt["stop_confirmed"] and receipt["cleanup_confirmed"])
    with ExecutionJournal(runner.journal_path) as journal:
        checks["no_pending_operations"] = not journal.pending()
    checks["no_ci_eligibility"] = all(item["ci_eligible"] is False for item in receipts)
    paths = ["src/gah/docker_runner.py", "src/gah/execution_journal.py", "src/gah/normalized.py",
             "fixtures/runtime/fixture_worker.py", "config/fixture-runtime.lock.json", "tools/verify_fixture_lifecycle.py"]
    report = {"schema_version": 1, "fixture_only": True, "full_mvp_accepted": False,
        "passed": all(checks.values()), "checks": checks, "image_id": runner.lock["image_id"],
        "source_sha256": {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in paths}}
    (folder / "receipts.json").write_text(json.dumps(receipts, indent=2) + "\n", encoding="utf-8")
    (folder / "check.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "checks": checks}), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
