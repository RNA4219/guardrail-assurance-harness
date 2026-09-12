"""現行のfocused試験と旧catalogを照合する。全体回帰の再実行とはしない。"""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import unittest

ROOT=Path(__file__).resolve().parents[2]
LOCAL=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'src'))
sys.path.insert(0,str(ROOT))


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def tests_in(suite):
    for item in suite:
        if isinstance(item,unittest.TestSuite):yield from tests_in(item)
        else:yield item.id()


def main():
    prior_path=ROOT/'docs/evidence/mvp-baseline-refresh-20260912/verification.json'
    prior=json.loads(prior_path.read_text('utf-8'))
    catalog=sorted(tests_in(unittest.defaultTestLoader.discover(str(ROOT/'tests'))))
    assert len(catalog)==len(set(catalog)) and set(prior['tests'])<=set(catalog)
    new=sorted(set(catalog)-set(prior['tests']))
    logs={'operation-tests-01.log':13,'checkpoint-product-tests-01.log':6,
        'supervisor-tests-02.log':8,'resource-authority-tests-01.log':6,'resources-tests-01.log':13}
    records={};executed=[]
    for name,count in logs.items():
        raw=(LOCAL/name).read_text('utf-8')
        found=re.findall(r'^test_\S+ \(([^)]+)\) \.\.\. ok$',raw,re.M)
        match=re.search(r'Ran (\d+) tests in ([0-9.]+)s',raw)
        assert match and int(match[1])==count and len(found)==count and raw.rstrip().endswith('OK'),name
        assert set(found)<=set(catalog)
        executed.extend(found)
        records[name]={'tests':found,'count':count,'seconds':float(match[2]),'sha256':sha(LOCAL/name)}
    initial=(LOCAL/'supervisor-tests-01.log').read_text('utf-8')
    assert 'Ran 8 tests' in initial and 'FAILED (failures=1)' in initial
    assert len(executed)==len(set(executed))==46
    assert len(catalog)==588 and len(new)==27 and set(new)<=set(executed)
    preserved=prior['preserved']
    assert len(preserved)==7 and all(sha(ROOT/p)==v['actual_sha256']==v['previous_sha256'] for p,v in preserved.items())
    paths=('src/gah/resource_operation.py','src/gah/baseline_refresh_migration.py','src/gah/resource_authority.py',
        'src/gah/evaluation_authority.py','src/gah/adoption_migrations.py','src/gah/supervisor_checkpoint.py',
        'src/gah/supervised_run.py','tools/gah_run.py','tools/supervisor_runtime_checks.py',
        'tools/candidate_runtime_checks.py','tools/verify_baseline_runtime.py','tools/prepare_authority_runtime.py',
        'tests/test_resource_operation.py','tests/test_operation_integration.py','tests/test_supervisor_checkpoint.py',
        'tests/test_supervised_run.py','tests/test_resource_authority.py','tests/test_resources.py')
    record={'schema_version':1,'checked_at':datetime.now(timezone.utc).isoformat(),'passed':True,
        'verification_mode':'previous_evidence_plus_current_focused_tests','focused_test_count':46,
        'new_test_count':27,'previous_catalog_count':561,'current_catalog_count':588,
        'full_current_suite_executed':False,'catalog':catalog,'new_tests':new,'focused_tests':sorted(executed),
        'logs':records,'initial_supervisor_failure_preserved':True,
        'initial_supervisor_log_sha256':sha(LOCAL/'supervisor-tests-01.log'),
        'review_patch_sha256':sha(LOCAL/'output-review.diff'),'previous_verification_sha256':sha(prior_path),
        'source_sha256':{p:sha(ROOT/p) for p in paths},'preserved':preserved,
        'full_mvp_accepted':False,'release_gate':'no_go','ci_eligible':False}
    target=LOCAL/'focused-check.json';assert not target.exists()
    target.write_text(json.dumps(record,ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps({key:record[key] for key in ('passed','focused_test_count','new_test_count','current_catalog_count','full_current_suite_executed')}))


if __name__=='__main__':main()
