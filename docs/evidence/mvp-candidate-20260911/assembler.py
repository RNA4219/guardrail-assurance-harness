"""実行が完了した候補runの証跡を、既存記録を上書きせずまとめる。"""
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json

ROOT = Path(__file__).resolve().parents[2]
LOCAL = Path(__file__).resolve().parent
DEST = ROOT / 'docs/evidence/mvp-candidate-20260911'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def main():
    unit = read(LOCAL / 'units-01/check.json')
    runtime = read(LOCAL / 'runtime-02/check.json')
    old = read(ROOT / 'docs/evidence/mvp-transition-20260911/runtime-check.json')
    receipts = read(LOCAL / 'runtime-02/execution-receipts.json')
    qwen = read(LOCAL / 'qwen-candidate-01/receipt.json')
    sources = dict(unit['source_sha256'])
    for path, value in runtime['source_sha256'].items():
        if path in sources:
            assert sources[path] == value, path
        sources[path] = value
    counts = Counter(r['normalized_result']['binding']['run_id'] for r in receipts)
    operations = {r['normalized_result']['binding']['operation_id'] for r in receipts}
    checks = {
        'all_494_tests_passed_previous_463_retained': unit['passed'] and unit['test_counts'] == {'total':494,'previous_retained':463,'new':31},
        'docker_candidate_checks_passed': runtime['passed'] and all(runtime['checks'].values()),
        'previous_52_docker_checks_retained': len(old['checks']) == 52 and set(old['checks']) <= set(runtime['checks']),
        'actual_sixty_isolated_receipts': len(receipts) == len(operations) == 60
            and counts == {'baseline-runtime-run':15,'candidate-old-runtime':15,'candidate-new-runtime':30}
            and all(r['execution_status'] == 'COMPLETED' and all(r.get(k) is True for k in
                ('stop_confirmed','cleanup_confirmed','isolation_config_verified')) and r['ci_eligible'] is False for r in receipts),
        'current_source_binding': all(digest(ROOT / p) == d for p,d in sources.items()),
        'requirements_policy_examples_preserved': all(digest(ROOT / p) == v['actual_sha256'] for p,v in unit['preserved'].items()),
        'qwen_review_completed': qwen['status'] == 'complete' and qwen['finish_reason'] == 'stop',
        'full_mvp_and_ci_unaccepted': runtime['full_mvp'] is False and runtime['ci_eligible'] is False,
    }
    assert all(checks.values()), checks
    DEST.mkdir(exist_ok=False)
    files = {
        'unit-check.json': LOCAL/'units-01/check.json', 'unittest.log': LOCAL/'units-01/unittest.log',
        'unit-checker.py': LOCAL/'verify_units.py', 'runtime-check.json': LOCAL/'runtime-02/check.json',
        'runtime-observations.json': LOCAL/'runtime-02/observations.json',
        'runtime-execution-receipts.json': LOCAL/'runtime-02/execution-receipts.json',
        'runtime-manifest.json': LOCAL/'runtime-02/manifest.json', 'runtime-deployment.json': LOCAL/'runtime-02/deployment.json',
        'authority-runtime.lock.json': ROOT/'config/authority-runtime.lock.json',
        'fixture-runtime.lock.json': ROOT/'config/fixture-runtime.lock.json',
        'qwen-receipt.json': LOCAL/'qwen-candidate-01/receipt.json',
        'qwen-response-envelope.json': LOCAL/'qwen-candidate-01/response-envelope.json',
        'qwen-response.txt': LOCAL/'qwen-candidate-01/response.txt',
        'qwen-submitted-source.txt': LOCAL/'qwen-candidate-01/submitted-source.txt',
        'qwen-checker.py': LOCAL/'ask_qwen.py', 'assembler.py': Path(__file__),
        'transition-spec.txt': ROOT/'docs/contract-transition-detail-spec.md',
        'prior-runtime-check.json': LOCAL/'runtime-01/check.json',
        'prior-receipt-comparison.json': LOCAL/'receipt-comparison.json',
        'predecessor-source.json': LOCAL/'predecessor-source.json',
    }
    for path in (
        'src/gah/adoption.py','src/gah/adoption_migrations.py','src/gah/evaluation_authority.py',
        'src/gah/fixture_admission.py','src/gah/run_contracts.py','src/gah/transition_authority.py',
        'src/gah/transition_materialization.py','src/gah/transition_migrations.py',
        'tests/test_transition_candidate_integration.py','tests/test_transition_materialization.py',
        'tests/test_adoption_migrations.py','tests/test_transition_migrations.py',
        'tools/candidate_runtime_checks.py','tools/prepare_authority_runtime.py','tools/verify_baseline_runtime.py'):
        files[path.split('/')[0] + '-' + Path(path).name] = ROOT/path
    artifacts = {}
    for name, source in files.items():
        with (DEST/name).open('xb') as stream:
            stream.write(source.read_bytes())
        artifacts[name] = digest(DEST/name)
    result = {'schema_version':1, 'checked_at':datetime.now(timezone.utc).isoformat(), 'status':'passed',
        'scope':'fixed_contract_candidate_runs_with_separate_old_regression_and_comparison_evidence',
        'checks':checks,'test_counts':unit['test_counts'],'tests':unit['tests'],
        'runtime_check_count':len(runtime['checks']), 'actual_run_entry_counts':dict(counts),
        'image_id':runtime['image_id'],'fixture_image_id':runtime['fixture_image_id'],
        'source_sha256':sources,'preserved':unit['preserved'],'artifact_sha256':artifacts,
        'qwen_review':{'status':qwen['status'],'usage':qwen['usage'],'disposition':'2 suggestions reviewed; no additional confirmed defect'},
        'migration_scope':'known v2/v3 schema and stored-reference tests; synthetic v3 fixtures, not migration of a deployed legacy volume',
        'full_mvp_accepted':False,'ci_eligible':False,'release_gate':'no_go'}
    with (DEST/'verification.json').open('x',encoding='utf-8',newline='\n') as stream:
        json.dump(result,stream,ensure_ascii=False,indent=2); stream.write('\n')
    print(json.dumps({'checks':checks,'tests':result['test_counts'],'runtime_checks':result['runtime_check_count'],
        'actual_run_entry_counts':dict(counts),'verification_sha256':digest(DEST/'verification.json')}))


if __name__ == '__main__':
    main()
