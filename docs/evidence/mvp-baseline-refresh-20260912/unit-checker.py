"""初回の全体回帰とレビュー補正後の限定回帰を区別して結合する。"""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import re
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[2]
LOCAL=Path(__file__).resolve().parent
OUT=LOCAL/"units-reviewed"
MODULES=("test_baseline_refresh.py", "test_baseline_authority.py", "test_baseline_source.py", "test_baselines.py", "test_contract_updates.py", "test_transition_materialization.py")
CHANGED={"src/gah/baseline_generations.py", "src/gah/baseline_authority.py", "tests/test_baseline_refresh.py"}


def read(path): return json.loads(path.read_text("utf-8"))
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    initial=read(LOCAL/"units-01/check.json")
    previous=read(ROOT/"docs/evidence/mvp-recovery-20260912/verification.json")
    before=read(LOCAL/"pointer-review-before.json")
    assert initial["passed"] and initial["test_counts"]["total"]==560
    hashes={path:sha(ROOT/path) for path in initial["source_sha256"]}
    changed={path for path,digest in hashes.items() if initial["source_sha256"][path]!=digest}
    assert changed==CHANGED
    assert all(before["source_sha256"][p]==initial["source_sha256"][p] for p in CHANGED)
    OUT.mkdir(exist_ok=False)
    tests=set();outcomes=[];elapsed=0
    for module in MODULES:
        log_path=OUT/(module[:-3]+".log")
        with log_path.open("xb") as stream:
            result=subprocess.run([sys.executable,"-E","-B","-X","utf8","-m","unittest","discover","-s","tests","-p",module,"-v"],cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT,timeout=1800)
        log=log_path.read_text("utf-8")
        names=set(location for _,location in re.findall(r"^(test_\S+) \(([^)\r\n]+)\)",log,re.MULTILINE))
        summary=re.search(r"Ran (\d+) tests in ([0-9.]+)s",log)
        passed=result.returncode==0 and log.rstrip().endswith("OK") and summary is not None and int(summary[1])==len(names)
        assert not tests & names
        tests |= names
        elapsed+=float(summary[2]) if summary else 0
        outcomes.append({"module":module,"passed":passed,"exit_code":result.returncode,"test_count":len(names),"log_sha256":sha(log_path)})
        print(json.dumps(outcomes[-1]),flush=True)
    union=set(initial["tests"]) | tests
    new=set(union)-set(previous["tests"])
    checks={"initial_full_regression_passed":initial["passed"],
        "all_44_focused_tests_passed":len(tests)==44 and all(row["passed"] for row in outcomes),
        "ten_refresh_tests_include_pointer_integrity":sum(name.startswith("test_baseline_refresh.") for name in tests)==10
            and any(name.endswith(".test_corrupt_current_pointer_cannot_be_hidden_by_refresh") for name in tests),
        "all_551_previous_test_ids_retained":set(previous["tests"])<=union and len(previous["tests"])==551,
        "one_test_added_after_review":len(union)==561 and len(tests-set(initial["tests"]))==1,
        "only_three_reviewed_sources_changed":changed==CHANGED,
        "sources_unchanged_during_focused_tests":all(sha(ROOT/p)==value for p,value in hashes.items()),
        "protected_seven_unchanged":len(initial["preserved"])==7 and all(sha(ROOT/p)==value["actual_sha256"] for p,value in initial["preserved"].items())}
    record={"schema_version":1,"checked_at":datetime.now(timezone.utc).isoformat(),"passed":all(checks.values()),"checks":checks,
        "verification_mode":"full_pre_review_plus_focused_post_review", "initial_full_tests":560,"focused_test_executions":44,
        "total_test_executions":604,"test_counts":{"total":len(union),"previous_retained":len(previous["tests"]),"new":len(new)},
        "tests":sorted(union),"focused_rechecked_tests":sorted(tests),"focused_outcomes":outcomes,
        "initial_unit_check_sha256":sha(LOCAL/"units-01/check.json"),"initial_unittest_log_sha256":sha(LOCAL/"units-01/unittest.log"),
        "review_observation_sha256":sha(LOCAL/"pointer-before.json"),"reviewed_source_changes":sorted(changed),
        "source_sha256":hashes,"preserved":initial["preserved"],"focused_elapsed_seconds":round(elapsed,3),
        "full_mvp_accepted":False,"ci_eligible":False}
    with (OUT/"check.json").open("x",encoding="utf-8") as stream:json.dump(record,stream,indent=2)
    print(json.dumps({k:record[k] for k in ("passed","checks","test_counts","verification_mode")}))
    return 0 if record["passed"] else 1


if __name__=="__main__": raise SystemExit(main())
