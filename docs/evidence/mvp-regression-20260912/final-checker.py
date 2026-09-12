"""実行済み証拠、現行ソース、保護対象、文書索引、補完後の非掲載検査を照合する。"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
LOCAL = Path(__file__).resolve().parent
OUT = ROOT / 'docs/evidence/mvp-regression-20260912'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text('utf-8'))


def main():
    verification = read(OUT / 'verification.json')
    primary = read(LOCAL / 'privacy-latest-primary.json')
    supplement = read(LOCAL / 'privacy-latest-supplement.json')
    result = subprocess.run([sys.executable, '-E', '-X', 'utf8', '-m', 'tools.workflow', 'check'],
        cwd=ROOT, capture_output=True, timeout=60)
    workflow = json.loads(result.stdout)
    index = read(ROOT / 'docs/birdseye/index.json')
    checks = {'evidence_hashes_match': all(sha(OUT / p) == d for p, d in verification['artifact_sha256'].items()),
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
            and supplement['primary_scan_sha256'] == sha(LOCAL / 'privacy-latest-primary.json'),
        'scope_consistent': verification['passed'] is True and verification['fixed_uc_ci_current_use_connected'] is True
            and verification['full_mvp_accepted'] is False and verification['release_gate'] == 'no_go'}
    files = {'final-checker.py': Path(__file__),
        'privacy-latest-primary.json': LOCAL / 'privacy-latest-primary.json',
        'privacy-latest-supplement.json': LOCAL / 'privacy-latest-supplement.json',
        'privacy-latest-checker.py': LOCAL / 'verify_privacy_latest.py',
        'privacy-latest-supplement-checker.py': LOCAL / 'verify_privacy_latest_supplement.py'}
    assert not (OUT / 'final-check.json').exists() and not (OUT / 'workflow-final.json').exists()
    assert all(not (OUT / name).exists() for name in files)
    for name, source in files.items():
        with (OUT / name).open('xb') as stream:
            stream.write(source.read_bytes())
    with (OUT / 'workflow-final.json').open('xb') as stream:
        stream.write(result.stdout)
    final = {'schema_version': 1, 'checked_at': datetime.now(timezone.utc).isoformat(),
        'passed': all(checks.values()), 'checks': checks,
        'verification_sha256': sha(OUT / 'verification.json'),
        'final_artifact_sha256': {name: sha(OUT / name) for name in [*files, 'workflow-final.json']},
        'birdseye_generation': index['generated_at'], 'birdseye_nodes': len(index['nodes']),
        'parent_review_completed': True, 'external_model_review_performed_this_stage': False,
        'privacy_remaining_matches': 0, 'privacy_remaining_read_errors': 0,
        'full_mvp_accepted': False, 'ci_eligible': False}
    with (OUT / 'final-check.json').open('x', encoding='utf-8') as stream:
        json.dump(final, stream, ensure_ascii=False, indent=2)
    print(json.dumps(final, ensure_ascii=False))
    return 0 if final['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
