"""実行済み証拠、現行ソース、保護対象、文書索引、補完後の非掲載検査を照合する。"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
LOCAL = Path(__file__).resolve().parent
OUT = ROOT / 'docs/evidence/mvp-baseline-refresh-20260912'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text('utf-8'))


def main():
    verification = read(OUT / 'verification.json')
    mapping = read(OUT / 'acceptance-map.json')
    runtime = read(OUT / 'runtime-check.json')
    primary = read(LOCAL / 'privacy-latest-primary-v1.json')
    supplement = read(LOCAL / 'privacy-latest-supplement-v1.json')
    result = subprocess.run([sys.executable, '-E', '-X', 'utf8', '-m', 'tools.workflow', 'check'],
        cwd=ROOT, capture_output=True, timeout=60)
    workflow = json.loads(result.stdout)
    index = read(ROOT / 'docs/birdseye/index.json')
    checks = {'all_32_requirements_tracked_without_acceptance_claim': mapping['requirements_tracked'] == 32
            and mapping['requirements_accepted'] == 0 and mapping['full_mvp_accepted'] is False
            and mapping['verification_sha256'] == sha(OUT / 'verification.json')
            and mapping['audit_source_sha256'] == sha(ROOT / 'docs/mvp-completion-audit.md')
            and mapping['requirements_source_sha256'] == sha(ROOT / 'docs/requirements.md')
            and mapping['acceptance_source_sha256'] == sha(ROOT / 'docs/acceptance-criteria.md')
            and len(mapping['requirements']) == 32
            and {r['requirement_id'] for r in mapping['requirements']} == {f'GAH-R{i:02d}' for i in range(1,33)}
            and all(r['status'] == 'partial' and r['product_acceptance_passed'] is False
                and r['related_unit_tests'] and set(r['related_unit_tests']) <= set(verification['tests'])
                and all(runtime['checks'].get(name) is True for name in r['related_runtime_checks'])
                and all((ROOT / path).is_file() for path in r['pending_connection_specifications'])
                for r in mapping['requirements']),
        'evidence_hashes_match': all(sha(OUT / p) == d for p, d in verification['artifact_sha256'].items()),
        'current_sources_match': all(sha(ROOT / p) == d for p, d in verification['source_sha256'].items()),
        'seven_protected_sources_match': len(verification['preserved']) == 7 and all(
            sha(ROOT / p) == v['actual_sha256'] == v['previous_sha256'] for p, v in verification['preserved'].items()),
        'workflow_passed': result.returncode == 0 and workflow['status'] == 'pass',
        'birdseye_hashes_match': all(hashlib.sha256((ROOT / p).read_text('utf-8').encode()).hexdigest()
            == node['source_sha256'] for p, node in index['nodes'].items()),
        'privacy_coverage_completed': primary['private_reference_matches'] == 0
            and primary['current_product_scan']['read_errors'] == 1
            and primary['unreadable_paths'] == [supplement['scope']]
            and supplement['passed'] is True and supplement['combined_read_errors'] == 0
            and supplement['primary_scan_sha256'] == sha(LOCAL / 'privacy-latest-primary-v1.json'),
        'scope_consistent': verification['passed'] is True and verification['fixed_uc_ci_current_use_connected'] is True
            and verification['normal_cancellation_connected'] is True
            and verification['expired_owner_cancellation_connected'] is True
            and verification['baseline_refresh_connected'] is True
            and verification['full_mvp_accepted'] is False and verification['release_gate'] == 'no_go'}
    files = {'final-checker-v1.py': Path(__file__),
        'acceptance-mapper.py': LOCAL / 'map_acceptance.py',
        'contract-revision-spec.txt': ROOT / 'docs/contract-revision-detail-spec.md',
        'llm-authority-spec.txt': ROOT / 'docs/llm-authority-detail-spec.md',
        'supervised-run-spec.txt': ROOT / 'docs/supervised-run-detail-spec.md',
        'retention-revalidation-spec.txt': ROOT / 'docs/retention-revalidation-detail-spec.md',
        'privacy-latest-primary-v1.json': LOCAL / 'privacy-latest-primary-v1.json',
        'privacy-latest-supplement-v1.json': LOCAL / 'privacy-latest-supplement-v1.json',
        'privacy-latest-checker-v1.py': LOCAL / 'verify_privacy_latest_v1.py',
        'privacy-latest-supplement-checker-v1.py': LOCAL / 'verify_privacy_latest_supplement_v1.py'}
    assert not (OUT / 'final-check-v1.json').exists() and not (OUT / 'workflow-final-v1.json').exists()
    assert all(not (OUT / name).exists() for name in files)
    for name, source in files.items():
        with (OUT / name).open('xb') as stream:
            stream.write(source.read_bytes())
    with (OUT / 'workflow-final-v1.json').open('xb') as stream:
        stream.write(result.stdout)
    final = {'schema_version': 1, 'checked_at': datetime.now(timezone.utc).isoformat(),
        'passed': all(checks.values()), 'checks': checks,
        'verification_sha256': sha(OUT / 'verification.json'),
        'final_artifact_sha256': {name: sha(OUT / name) for name in [*files, 'workflow-final-v1.json', 'acceptance-map.json']},
        'birdseye_generation': index['generated_at'], 'birdseye_nodes': len(index['nodes']),
        'parent_review_completed': True, 'external_model_review_performed_this_stage': False,
        'privacy_remaining_matches': 0, 'privacy_remaining_read_errors': 0,
        'full_mvp_accepted': False, 'ci_eligible': False}
    with (OUT / 'final-check-v1.json').open('x', encoding='utf-8') as stream:
        json.dump(final, stream, ensure_ascii=False, indent=2)
    print(json.dumps(final, ensure_ascii=False))
    return 0 if final['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
