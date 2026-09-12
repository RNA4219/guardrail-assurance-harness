from pathlib import Path
import hashlib,json,re
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[2];LOCAL=Path(__file__).resolve().parent
read=lambda p:json.loads(p.read_text(encoding="utf-8"))
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
first=read(LOCAL/"units-01/check.json");review=read(LOCAL/"units-reviewed/check.json")
log=(LOCAL/"units-01/unittest.log").read_text(encoding="utf-8")
second=(LOCAL/"units-reviewed/resource-authority.log").read_text(encoding="utf-8")
failed=re.findall(r"^(?:FAIL|ERROR): .+? \(([^)\r\n]+)\)$",log,re.M)
expected="test_resource_authority.ResourceAuthorityTests.test_every_action_is_fresh_and_duplicate_reserve_has_no_new_row"
p="tests/test_resource_authority.py"
before=(LOCAL/"test_resource_authority.py.before").read_text(encoding="utf-8")
a='            "resource_close", "resource_observe",'
b='            "resource_close", "resource_observe", "resource_cancel_claim",'
checks=dict(review["checks"])
checks["initial_result_and_log_are_preserved"]=review["initial_check_sha256"]==sha(LOCAL/"units-01/check.json") and first["unittest_log_sha256"]==sha(LOCAL/"units-01/unittest.log")
checks["initial_failure_is_only_expected_list"]=failed==[expected] and log.rstrip().endswith("FAILED (failures=1)")
checks["recheck_is_complete_and_successful"]=second.rstrip().endswith("OK") and "Ran 6 tests" in second and review["focused_log_sha256"]==sha(LOCAL/"units-reviewed/resource-authority.log")
checks["expectation_delta_is_exact"]=before.count(a)==1 and (ROOT/p).read_text(encoding="utf-8")==before.replace(a,b)
checks["current_sources_match"]=all(sha(ROOT/name)==value for name,value in review["source_sha256"].items())
checks["other_sources_match_first_run"]=all(name==p or sha(ROOT/name)==value for name,value in first["source_sha256"].items())
result={**review,"checked_at":datetime.now(timezone.utc).isoformat(),"passed":all(checks.values()),"checks":checks,
 "checker_sha256":sha(Path(__file__)),"initial_checker_sha256":first["checker_sha256"],"prior_resolution_sha256":sha(LOCAL/"units-reviewed/check.json")}
with (LOCAL/"units-reviewed/final-check.json").open("x",encoding="utf-8") as f:json.dump(result,f,ensure_ascii=False,indent=2)
print(json.dumps({"passed":result["passed"],"checks":checks,"test_counts":result["test_counts"]}))
raise SystemExit(0 if result["passed"] else 1)
