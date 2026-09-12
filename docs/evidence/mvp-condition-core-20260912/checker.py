"""条件比較部品の実行済み試験・保存source・非掲載検査を固定する。"""
from datetime import datetime,timezone
import hashlib,json,re,subprocess,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
LOCAL=Path(__file__).resolve().parent
OUT=ROOT/'docs/evidence/mvp-condition-core-20260912'
PRIOR=ROOT/'docs/evidence/mvp-supervisor-20260912'
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'src'))
from gah.evaluation_authority import EvaluationExtension

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def read(path):return json.loads(path.read_text('utf-8'))
def test_ids(suite):
    for item in suite:
        if isinstance(item,unittest.TestSuite):yield from test_ids(item)
        else:yield item.id()
def main():
    prior=read(PRIOR/'verification.json')
    catalog=sorted(test_ids(unittest.defaultTestLoader.discover(str(ROOT/'tests'))))
    new=sorted(set(catalog)-set(prior['catalog']))
    assert len(catalog)==622 and len(new)==30 and set(prior['catalog'])<=set(catalog)
    logs={};executed=[]
    for name,count in {'semantic-product-tests-01.log':21,'revision-product-tests-02.log':9,
            'run-contracts-01.log':9,'contract-updates-01.log':12}.items():
        text=(LOCAL/name).read_text('utf-8')
        ids=re.findall(r'^test_\S+ \(([^)]+)\) \.\.\. ok$',text,re.M)
        matched=re.search(r'Ran (\d+) tests? in ([0-9.]+)s',text)
        assert matched and int(matched[1])==count and len(ids)==count and text.rstrip().endswith('OK')
        logs[name]={'count':count,'tests':ids,'seconds':float(matched[2]),'sha256':sha(LOCAL/name)}
        executed.extend(ids)
    primary=read(LOCAL/'privacy-latest-primary-v1.json');supplement=read(LOCAL/'privacy-latest-supplement-v1.json')
    completed=subprocess.run([sys.executable,'-E','-B','-X','utf8','-m','tools.workflow','check'],cwd=ROOT,capture_output=True,timeout=60)
    workflow=json.loads(completed.stdout);index=read(ROOT/'docs/birdseye/index.json')
    checks={'new_30_and_existing_21_passed':len(executed)==len(set(executed))==51 and set(new)<=set(executed)<=set(catalog),
        'previous_product_sources_unchanged':all(sha(ROOT/path)==digest for path,digest in prior['source_sha256'].items()),
        'authority_extension_and_runtime_lock_unchanged':EvaluationExtension.digest==prior['extension_digest']
            and sha(ROOT/'config/authority-runtime.lock.json')==sha(PRIOR/'authority-runtime.lock.json'),
        'protected_requirements_unchanged':len(prior['preserved'])==7 and all(sha(ROOT/path)==v['actual_sha256']==v['previous_sha256'] for path,v in prior['preserved'].items()),
        'workflow_passed':completed.returncode==0 and workflow['status']=='pass',
        'birdseye_sources_match':all(hashlib.sha256((ROOT/path).read_text('utf-8').encode()).hexdigest()==node['source_sha256'] for path,node in index['nodes'].items()),
        'privacy_completed':primary['private_reference_matches']==0 and primary['current_product_scan']['read_errors']==1
            and primary['unreadable_paths']==[supplement['scope']] and supplement['passed'] is True
            and supplement['combined_read_errors']==0 and supplement['primary_scan_sha256']==sha(LOCAL/'privacy-latest-primary-v1.json')}
    assert all(checks.values()),checks
    sources=('src/gah/semantic_conditions.py','src/gah/contract_revision_rules.py','tests/test_semantic_conditions.py',
        'tests/test_contract_revision_rules.py','tests/test_run_contracts.py','tests/test_contract_updates.py',
        'src/gah/run_contracts.py','src/gah/policy.py','src/gah/registry.py','src/gah/corpus.py')
    files={name:LOCAL/name for name in logs}
    files.update({'checker.py':Path(__file__),'prototype-fixture-failure.log':LOCAL/'semantic-tests-02.log',
        'prototype-fixture-corrected.log':LOCAL/'semantic-tests-04.log',
        'before-component-connection.json':LOCAL/'before-component-connection.json',
        'semantic-conditions-spec.txt':ROOT/'docs/semantic-conditions-detail-spec.md',
        'parent-review.txt':ROOT/'docs/reviews/mvp-condition-core-20260912.md',
        'privacy-primary.json':LOCAL/'privacy-latest-primary-v1.json',
        'privacy-supplement.json':LOCAL/'privacy-latest-supplement-v1.json',
        'privacy-checker.py':LOCAL/'verify_privacy_latest_v1.py',
        'privacy-supplement-checker.py':LOCAL/'verify_privacy_latest_supplement_v1.py',
        'privacy-native-reader.ps1':LOCAL/'privacy_native_read.ps1'})
    for path in sources[:4]:files[path.split('/')[0]+'-'+Path(path).name]=ROOT/path
    assert {path.name for path in OUT.iterdir()}=={'README.md'}
    for name,path in files.items():(OUT/name).write_bytes(path.read_bytes())
    (OUT/'workflow-check.json').write_bytes(completed.stdout)
    result={'schema_version':1,'checked_at':datetime.now(timezone.utc).isoformat(),'passed':True,'checks':checks,
        'focused_test_count':51,'new_test_count':30,'current_catalog_count':622,'tests':sorted(executed),'new_tests':new,'catalog':catalog,'logs':logs,
        'full_current_suite_executed':False,'docker_executed_this_stage':False,
        'previous_verification_sha256':sha(PRIOR/'verification.json'),'source_sha256':{path:sha(ROOT/path) for path in sources},
        'preserved':prior['preserved'],'artifact_sha256':{name:sha(OUT/name) for name in [*files,'workflow-check.json']},
        'birdseye_generation':index['generated_at'],'birdseye_nodes':len(index['nodes']),
        'authority_extension_digest':EvaluationExtension.digest,'authority_connected':False,
        'following_contract_adoption_connected':False,'target_execution_verified':False,
        'parent_review_completed':True,'external_model_review_performed_this_stage':False,
        'full_mvp_accepted':False,'release_gate':'no_go','ci_eligible':False}
    (OUT/'verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps({key:result[key] for key in ('passed','focused_test_count','new_test_count','current_catalog_count','authority_connected')}))

if __name__=='__main__':main()
