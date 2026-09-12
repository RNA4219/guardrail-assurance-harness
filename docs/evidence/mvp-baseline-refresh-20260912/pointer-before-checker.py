"""自作DBでcurrentの破損を更新が拒否するか確認する。"""
from pathlib import Path
import importlib.util
import json
ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location("refresh_pointer_review",ROOT/"tests/test_baseline_refresh.py")
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
Case=module.BaselineRefreshTests
Case.setUpClass()
results=[]
try:
    for stage in ("propose","validate","adopt"):
        helper=Case("runTest");helper.setUp()
        try:
            helper.source()
            if stage in {"validate","adopt"}: helper.propose()
            if stage=="adopt": helper.validate()
            helper.store._db.execute("UPDATE baseline_current SET baseline_digest=?",("0"*64,))
            try:
                getattr(helper,stage)()
                outcome="accepted"
            except module.AdoptionError as error:
                outcome=error.code
            results.append({"stage":stage,"corrupt_pointer_result":outcome})
        finally:
            helper.doCleanups()
finally:
    Case.doClassCleanups()
result={"schema_version":1,"scope":"local synthetic store consistency", "observations":results,
    "all_stages_reject":all(row["corrupt_pointer_result"]!="accepted" for row in results),"ci_eligible":False}
output=Path(__file__).with_name("pointer-before.json")
with output.open("x",encoding="utf-8") as stream: json.dump(result,stream,indent=2)
print(json.dumps(result))
