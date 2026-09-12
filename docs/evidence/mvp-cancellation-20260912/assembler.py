"""通常runの実行・CI利用と制限を、検証済みの不変packetへまとめる。"""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOCAL = Path(__file__).resolve().parent
OUT = ROOT / 'docs/evidence/mvp-cancellation-20260912'


def read(path):
    return json.loads(path.read_text('utf-8'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    units = read(LOCAL / 'units-01/check.json')
    runtime = read(LOCAL / 'runtime-01/check.json')
    executions = read(LOCAL / 'runtime-01/execution-receipts.json')
    previous = read(ROOT / 'docs/evidence/mvp-regression-20260912/runtime-check.json')
    primary = read(LOCAL / 'privacy-elevated.json')
    supplement = read(LOCAL / 'privacy-supplement.json')
    sources = dict(units['source_sha256'])
    for path, digest in runtime['source_sha256'].items():
        assert path not in sources or sources[path] == digest
        sources[path] = digest
    counts = Counter(r['normalized_result']['binding']['run_id'] for r in executions)
    observed = read(LOCAL / 'runtime-01/observations.json')
    ci_calls = [item for item in observed if item['action'] == 'ci_check']
    ci_results = [item['response'] for item in ci_calls]
    missing_checks = sorted(set(previous['checks']) - set(runtime['checks']))
    checks = {
        'all_tests_and_previous_523_passed': units['passed'] and units['test_counts']['previous_retained'] == 523,
        'cancellation_runtime_passed': runtime['passed'] and all(runtime['checks'].values()) and runtime['regression_enabled'] and runtime['cancellation_enabled'],
        'previous_runtime_checks_retained': len(previous['checks']) == 198 and not missing_checks,
        'ninety_one_actual_executions': len(executions) == 91 and counts == {
            'baseline-runtime-run': 15, 'candidate-old-runtime': 15, 'candidate-new-runtime': 30,
            'regression-runtime': 30, 'cancellation-runtime': 1} and len({r['normalized_result']['binding']['operation_id'] for r in executions}) == 91,
        'all_executions_stopped_and_cleaned': all(r['execution_status'] == 'COMPLETED'
            and all(r[k] is True for k in ('stop_confirmed', 'cleanup_confirmed', 'isolation_config_verified')) for r in executions),
        'fresh_ci_sequence_matches_observations': [r.get('exit_code') for r in ci_results] == [2, 0, 0, 0, 2, 3, 3, 3, 3, 1, 1, 3]
            and all(item['uid'] == 12004 for item in ci_calls)
            and all(r['kind'] == 'ci_gate_result' and r['ci_eligible'] is (r['exit_code'] == 0)
                and r['use'] is (r['exit_code'] == 0) for r in ci_results)
            and all([r['exit_code'] for r in ci_results if r['run_id'] == run_id] == codes
                for run_id, codes in {'regression-runtime': [2, 0, 0, 0, 1, 1],
                    'cancellation-runtime': [2, 3, 3, 3, 3, 3]}.items())
            and all(len({json.dumps(r['expected_manifest_ref'],sort_keys=True) for r in ci_results if r['run_id']==run_id})==1
                for run_id in ('regression-runtime','cancellation-runtime')),
        'current_sources_match': all(sha(ROOT / p) == d for p, d in sources.items()),
        'seven_protected_sources_match': len(units['preserved']) == 7 and all(
            sha(ROOT / p) == v['actual_sha256'] for p, v in units['preserved'].items()),
        'privacy_read_errors_supplemented': primary['private_reference_matches'] == 0
            and primary['current_product_scan']['read_errors'] == 1 and supplement['passed']
            and primary['unreadable_paths'] == [supplement['scope']]
            and supplement['primary_scan_sha256'] == sha(LOCAL / 'privacy-elevated.json')
            and supplement['combined_read_errors'] == 0,
        'full_mvp_not_claimed': units['full_mvp_accepted'] is False and runtime['full_mvp'] is False,
    }
    assert all(checks.values()), checks
    OUT.mkdir(exist_ok=False)
    files = {'assembler.py': Path(__file__), 'unit-checker.py': LOCAL / 'verify_units.py',
        'unit-check.json': LOCAL / 'units-01/check.json', 'unittest.log': LOCAL / 'units-01/unittest.log',
        'authority-runtime.lock.json': ROOT / 'config/authority-runtime.lock.json',
        'fixture-runtime.lock.json': ROOT / 'config/fixture-runtime.lock.json',
        'cancellation-spec.txt': ROOT / 'docs/run-cancellation-detail-spec.md',
        'privacy-primary.json': LOCAL / 'privacy-elevated.json',
        'privacy-supplement.json': LOCAL / 'privacy-supplement.json',
        'privacy-native-reader.ps1': LOCAL / 'privacy_native_read.ps1',
        'privacy-supplement-checker.py': LOCAL / 'verify_privacy_supplement.py',
        'prior-target-tests-01.txt': LOCAL / 'target-tests-01.txt'}
    for name in ('check.json', 'observations.json', 'execution-receipts.json', 'manifest.json', 'deployment.json'):
        files['runtime-' + name] = LOCAL / 'runtime-01' / name
    before = read(LOCAL / 'before.json')['source_sha256']
    changed = sorted(p for p, digest in sources.items() if p.endswith('.py') and before.get(p) != digest
        and (p.startswith('src/gah/') or p.startswith('tests/') or p.startswith('tools/') and p.count('/') == 1))
    for path in changed:
        files[path.split('/')[0] + '-' + Path(path).name] = ROOT / path
    for name, path in files.items():
        with (OUT / name).open('xb') as stream:
            stream.write(path.read_bytes())
    record = {'schema_version': 1, 'checked_at': datetime.now(timezone.utc).isoformat(),
        'passed': True, 'checks': checks, 'test_counts': units['test_counts'], 'tests': units['tests'],
        'runtime_check_count': len(runtime['checks']), 'previous_runtime_checks_retained': len(previous['checks']),
        'missing_previous_runtime_checks': missing_checks, 'actual_execution_counts': dict(counts),
        'observed_ci_exit_codes': [r['exit_code'] for r in ci_results],
        'image_id': runtime['image_id'], 'fixture_image_id': runtime['fixture_image_id'],
        'source_sha256': sources, 'preserved': units['preserved'],
        'artifact_sha256': {name: sha(OUT / name) for name in files},
        'parent_takeover_requested': True, 'reviewer': 'Codex parent',
        'external_model_review_performed_this_stage': False,
        'fixed_uc_ci_current_use_connected': True, 'normal_cancellation_connected': True, 'full_mvp_accepted': False,
        'release_gate': 'no_go', 'ci_eligible': False,
        'limitations': ['対象は自作の固定UC-CI fixtureに限る。',
            '条件変更を伴う採択、UC-LLMの認証・資源接続、常設管理、Findingの修復確認と全32要求の受入は残る。',
            '検証packetは本番CIのreceiptではない。現在のCI利用はbrokerへ毎回問い合わせる。',
            '過去の読取り失敗を保持し、Windowsの読取りで未読ディレクトリを補完する。']}
    with (OUT / 'verification.json').open('x', encoding='utf-8') as stream:
        json.dump(record, stream, indent=2)
    print(json.dumps({k: record[k] for k in ('passed', 'test_counts', 'runtime_check_count', 'actual_execution_counts')}))


if __name__ == '__main__':
    main()
