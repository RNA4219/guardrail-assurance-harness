"""監督の実行証拠と、開始前中断のレビュー補正を区別して固定する。"""
from collections import Counter
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import unittest
ROOT=Path(__file__).resolve().parents[2]
LOCAL=Path(__file__).resolve().parent
OUT=ROOT/'docs/evidence/mvp-supervisor-20260912'
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'src'))
from tools.gah_ci import response_exit_code
from gah.evaluation_authority import EvaluationExtension

def read(p):return json.loads(p.read_text('utf-8'))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def tests_in(suite):
    for item in suite:
        if isinstance(item,unittest.TestSuite):yield from tests_in(item)
        else:yield item.id()
def log_tests(path,count):
    text=path.read_text('utf-8')
    matched=re.search(r'Ran (\d+) tests? in ([0-9.]+)s',text)
    ids=re.findall(r'^test_\S+ \(([^)]+)\) \.\.\. ok$',text,re.M)
    assert matched and int(matched[1])==count and len(ids)==count and text.rstrip().endswith('OK'),path
    return {'count':count,'seconds':float(matched[2]),'tests':ids,'sha256':sha(path)}

def main():
    focused=read(LOCAL/'focused-check.json')
    runtime=read(LOCAL/'runtime-01/check.json')
    receipts=read(LOCAL/'runtime-01/execution-receipts.json')
    observations=read(LOCAL/'runtime-01/observations.json')
    reviewed=log_tests(LOCAL/'startup-reviewed-01.log',12)
    catalog=sorted(tests_in(unittest.defaultTestLoader.discover(str(ROOT/'tests'))))
    new=sorted(set(catalog)-set(focused['catalog']))
    assert len(new)==4 and len(catalog)==592 and set(new)<=set(reviewed['tests'])
    expected_before=LOCAL/'before-startup-review/src/gah/supervised_run.py'
    before=expected_before.read_text('utf-8')
    old="        if mode == 'run' or mode == 'resume' and self.checkpoint.get('request-begin') is not None:\n"
    new_line="        if mode in ('run', 'resume'):\n"
    assert before.count(old)==1 and (ROOT/'src/gah/supervised_run.py').read_text('utf-8')==before.replace(old,new_line)
    sources=dict(focused['source_sha256'])
    for path,digest in runtime['source_sha256'].items():
        assert path not in sources or sources[path]==digest
        sources[path]=digest
    changed=[p for p,d in sources.items() if sha(ROOT/p)!=d]
    assert changed==['src/gah/supervised_run.py'] and sha(expected_before)==sources[changed[0]],changed
    original_sources=dict(sources)
    sources[changed[0]]=sha(ROOT/changed[0])
    sources['tests/test_supervised_startup.py']=sha(ROOT/'tests/test_supervised_startup.py')
    counts=Counter(r['normalized_result']['binding']['run_id'] for r in receipts)
    ci=[r for r in observations if r['action']=='ci_check']
    result_codes=[r['response']['exit_code'] for r in ci]
    checked_codes=[response_exit_code({key:r['response'][key] for key in ('request_id','run_id','expected_manifest_ref')},r['response']) for r in ci]
    supervisor=read(LOCAL/'runtime-01/supervisor-results.json')
    primary=read(LOCAL/'privacy-elevated.json');supplement=read(LOCAL/'privacy-supplement.json')
    checks={
        'focused_46_then_reviewed_12_passed':focused['passed'] and focused['focused_test_count']==46
            and reviewed['count']==12 and len(set(focused['focused_tests'])|set(reviewed['tests']))==50,
        'four_new_startup_boundaries_covered':len(new)==4 and set(new)<=set(reviewed['tests']),
        'initial_supervisor_failure_and_correction_preserved':focused['initial_supervisor_failure_preserved']
            and sha(LOCAL/'supervisor-tests-01.log')==focused['initial_supervisor_log_sha256']
            and sha(LOCAL/'output-review.diff')==focused['review_patch_sha256'],
        'pre_correction_startup_defect_reproduced':log_tests(LOCAL/'prepare-before-01.log',1)['count']==1,
        'all_runtime_checks_passed':runtime['passed'] is True and all(runtime['checks'].values()) and runtime['supervisor_enabled'] is True,
        'ninety_one_actual_executions':len(receipts)==91 and counts=={'baseline-runtime-run':15,
            'candidate-old-runtime':15,'candidate-new-runtime':30,'supervised-normal-runtime':30,'supervised-interrupted-runtime':1}
            and len({r['normalized_result']['binding']['operation_id'] for r in receipts})==91,
        'all_executions_stopped_and_cleaned':all(r['execution_status']=='COMPLETED'
            and all(r[k] is True for k in ('stop_confirmed','cleanup_confirmed','isolation_config_verified')) for r in receipts),
        'fresh_ci_responses_match':result_codes==checked_codes and all(item['uid']==12004 for item in ci)
            and set(result_codes)>={0,1,2,3},
        'normal_and_interrupted_outcomes':supervisor['normal']['exit_code']==0 and supervisor['interrupted']['exit_code']==3,
        'runtime_pre_correction_sources_preserved':sha(expected_before)==original_sources['src/gah/supervised_run.py'],
        'seven_protected_sources_match':len(focused['preserved'])==7 and all(sha(ROOT/p)==v['actual_sha256']==v['previous_sha256'] for p,v in focused['preserved'].items()),
        'privacy_coverage_completed':primary['private_reference_matches']==0 and supplement['passed'] is True
            and primary['unreadable_paths']==[supplement['scope']] and supplement['combined_read_errors']==0
            and supplement['primary_scan_sha256']==sha(LOCAL/'privacy-elevated.json'),
        'full_current_suite_not_claimed':focused['full_current_suite_executed'] is False,
    }
    assert all(checks.values()),checks
    assert OUT.is_dir() and {p.name for p in OUT.iterdir()}=={'README.md'}
    files={'assembler.py':Path(__file__),'focused-check.json':LOCAL/'focused-check.json',
        'focused-checker.py':LOCAL/'check_focused.py','authority-runtime.lock.json':ROOT/'config/authority-runtime.lock.json',
        'fixture-runtime.lock.json':ROOT/'config/fixture-runtime.lock.json','privacy-primary.json':LOCAL/'privacy-elevated.json',
        'privacy-supplement.json':LOCAL/'privacy-supplement.json','privacy-supplement-checker.py':LOCAL/'verify_privacy_supplement.py',
        'privacy-native-reader.ps1':LOCAL/'privacy_native_read.ps1','before-connection.json':LOCAL/'before-connection.json',
        'startup-defect-reproducer.py':LOCAL/'test_prepare_resume_before.py'}
    for name in ('operation-tests-01.log','checkpoint-product-tests-01.log','supervisor-tests-01.log','supervisor-tests-02.log',
            'resource-authority-tests-01.log','resources-tests-01.log','prepare-before-01.log','startup-reviewed-01.log',
            'output-review.diff','resource-connection.diff','runtime-integration.diff','startup-review.diff','build-01.log'):
        files[name]=LOCAL/name
    for name in ('check.json','observations.json','execution-receipts.json','manifest.json','deployment.json',
            'supervisor-cli-status.json','supervisor-results.json'):
        files['runtime-'+name]=LOCAL/'runtime-01'/name
    for path in ('src/gah/resource_operation.py','src/gah/baseline_refresh_migration.py','src/gah/supervised_run.py',
            'src/gah/supervisor_checkpoint.py','tools/gah_run.py','tools/supervisor_runtime_checks.py',
            'tests/test_supervised_startup.py','tests/test_supervised_run.py','tests/test_operation_integration.py',
            'tests/test_resource_operation.py','tests/test_supervisor_checkpoint.py'):
        files[path.split('/')[0]+'-'+Path(path).name]=ROOT/path
    files['runtime-source-supervised_run.py']=expected_before
    for path in (LOCAL/'before-output-review').rglob('*.py'):
        files['before-output-review-'+path.name]=path
    assert all(not (OUT/name).exists() for name in files)
    for name,path in files.items():(OUT/name).write_bytes(path.read_bytes())
    prior=ROOT/'docs/evidence/mvp-baseline-refresh-20260912/verification.json'
    record={'schema_version':1,'checked_at':datetime.now(timezone.utc).isoformat(),'passed':True,'checks':checks,
        'verification_mode':'previous_evidence_plus_focused_46_then_startup_correction_and_12_tests',
        'current_catalog_count':len(catalog),'catalog':catalog,'tests':sorted(set(focused['focused_tests'])|set(reviewed['tests'])),
        'focused_unique_test_count':50,'initial_focused_test_count':46,'post_startup_correction_test_count':12,
        'post_startup_correction_tests':reviewed,'full_current_suite_executed':False,
        'previous_verification_sha256':sha(prior),'initial_focused_source_sha256':focused['source_sha256'],
        'runtime_source_sha256':runtime['source_sha256'],'source_sha256':sources,'preserved':focused['preserved'],
        'runtime_check_count':len(runtime['checks']),'runtime_before_startup_correction':True,
        'runtime_after_startup_correction_executed':False,'reviewed_runtime_source_changes':changed,
        'runtime_scope':'初回・候補採択・製品監督の独立経路。直前248項目の再実行ではない。',
        'actual_execution_counts':dict(counts),'observed_ci_exit_codes':result_codes,
        'extension_digest':EvaluationExtension.digest,'image_id':runtime['image_id'],'fixture_image_id':runtime['fixture_image_id'],
        'artifact_sha256':{name:sha(OUT/name) for name in files},'parent_review_completed':True,
        'external_model_review_performed_this_stage':False,'fixed_full_uc_ci_supervisor_connected':True,
        'full_mvp_accepted':False,'release_gate':'no_go','ci_eligible':False,
        'limitations':['固定UC-CI contract 2の全30件のみ。changeはfullへ拡大する。',
            'Dockerは開始前中断の一行補正前。補正後は実SQLiteと合成runnerの12試験を実行した。',
            '対象限定・定期起動サービス・版跨ぎ回収・保持と残る両用途の接続は未完了。',
            '全体592試験の再実行ではなく、既存証拠と今回50種類のfocused試験を結ぶ。']}
    target=OUT/'verification.json';assert not target.exists()
    target.write_text(json.dumps(record,ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps({k:record[k] for k in ('passed','focused_unique_test_count','runtime_check_count','actual_execution_counts')}))

if __name__=='__main__':main()
