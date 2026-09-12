"""現行部品の全回帰と前工程からの保持を新規ファイルへ記録する。"""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[2]
FOLDER = Path(__file__).resolve().parent / "units-01"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    FOLDER.mkdir(exist_ok=False)
    paths = sorted([*ROOT.glob("src/gah/*.py"), *ROOT.glob("tests/test_*.py")])
    sources = {str(path.relative_to(ROOT)).replace("\\", "/"): digest(path) for path in paths}
    previous = json.loads((ROOT / "docs/evidence/mvp-transition-20260911/verification.json").read_text(encoding="utf-8"))
    with (FOLDER / "sources-start.json").open("x", encoding="utf-8") as stream:
        json.dump(sources, stream, indent=2)
    command = [sys.executable, "-E", "-X", "utf8", "-m", "unittest", "discover", "-s", "tests", "-v"]
    with (FOLDER / "unittest.log").open("xb") as stream:
        completed = subprocess.run(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT, timeout=600)
    log = (FOLDER / "unittest.log").read_text(encoding="utf-8")
    matches = re.findall(r"^(test_\S+) \(([^)\r\n]+)\)", log, re.MULTILINE)
    tests = sorted(set(location for name, location in matches))
    old_tests = set(previous["tests"])
    missing = sorted(old_tests - set(tests))
    summary = re.search(r"Ran (\d+) tests in ([0-9.]+)s", log)
    checks = {
        "unittest_succeeded": completed.returncode == 0 and log.rstrip().endswith("OK"),
        "all_executed_tests_succeeded": summary is not None and int(summary[1]) == len(tests),
        "previous_463_tests_retained": len(old_tests) == 463 and not missing,
        "tested_sources_unchanged": all(digest(ROOT / path) == value for path, value in sources.items()),
        "requirements_policy_examples_preserved": all(digest(ROOT / path) == value["actual_sha256"] for path, value in previous["preserved"].items()),
    }
    record = {"schema_version": 1, "checked_at": datetime.now(timezone.utc).isoformat(),
        "passed": all(checks.values()), "checks": checks, "exit_code": completed.returncode,
        "tests": tests, "test_counts": {"total": len(tests), "previous_retained": len(old_tests)-len(missing), "new": len(set(tests)-old_tests)},
        "missing_previous_tests": missing, "source_sha256": sources, "preserved": previous["preserved"],
        "unittest_log_sha256": digest(FOLDER / "unittest.log"), "checker_sha256": digest(Path(__file__)),
        "full_mvp_accepted": False, "ci_eligible": False}
    with (FOLDER / "check.json").open("x", encoding="utf-8") as stream:
        json.dump(record, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"passed": record["passed"], "checks": checks, "test_counts": record["test_counts"], "missing_count": len(missing)}))
    return 0 if record["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
