"""監督工程の証拠・修正後source・32対応表・文書索引を照合する。"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
LOCAL = Path(__file__).resolve().parent
OUT = ROOT / 'docs/evidence/mvp-supervisor-20260912'
PRIOR = ROOT / 'docs/evidence/mvp-baseline-refresh-20260912'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text('utf-8'))


def main():
    verification = read(OUT / 'verification.json')
    mapping = read(OUT / 'acceptance-map.json')
    runtime = read(OUT / 'runtime-check.json')
    prior_map = read(PRIOR / 'acceptance-map.json')
    primary = read(LOCAL / 'privacy-latest-primary-v1.json')
    supplement = read(LOCAL / 'privacy-latest-supplement-v1.json')
    completed = subprocess.run([sys.executable, '-E', '-B', '-X', 'utf8', '-m', 'tools.workflow', 'check'],
        cwd=ROOT, capture_output=True, timeout=60)
    workflow = json.loads(completed.stdout)
    index = read(ROOT / 'docs/birdseye/index.json')
    rows = mapping['requirements']
    checks = {
        '32_partial_requirements_tracked': mapping['requirements_tracked'] == len(rows) == 32
            and mapping['requirements_accepted'] == 0 and not mapping['full_mvp_accepted']
            and {r['requirement_id'] for r in rows} == {f'GAH-R{i:02d}' for i in range(1, 33)}
            and all(r['status'] == 'partial' and not r['product_acceptance_passed']
                and set(r['current_stage_related_unit_tests']) <= set(verification['tests'])
                and all(runtime['checks'][key] for key in r['current_stage_related_runtime_checks'])
                and r['historical_related_unit_tests'] == prior_map['requirements'][int(r['requirement_id'][-2:]) - 1]['related_unit_tests']
                and all((ROOT / name).is_file() for name in r['pending_connection_specifications']) for r in rows),
        'mapping_sources_match': mapping['verification_sha256'] == sha(OUT / 'verification.json')
            and mapping['historical_evidence_map_sha256'] == sha(PRIOR / 'acceptance-map.json')
            and mapping['audit_source_sha256'] == sha(ROOT / 'docs/mvp-completion-audit.md')
            and mapping['requirements_source_sha256'] == sha(ROOT / 'docs/requirements.md')
            and mapping['acceptance_source_sha256'] == sha(ROOT / 'docs/acceptance-criteria.md'),
        'evidence_hashes_match': all(sha(OUT / name) == digest for name, digest in verification['artifact_sha256'].items()),
        'current_sources_match': all(sha(ROOT / name) == digest for name, digest in verification['source_sha256'].items()),
        'seven_protected_sources_match': len(verification['preserved']) == 7 and all(
            sha(ROOT / name) == value['actual_sha256'] == value['previous_sha256'] for name, value in verification['preserved'].items()),
        'workflow_passed': completed.returncode == 0 and workflow['status'] == 'pass',
        'birdseye_sources_match': all(hashlib.sha256((ROOT / name).read_text('utf-8').encode()).hexdigest()
            == value['source_sha256'] for name, value in index['nodes'].items()),
        'privacy_coverage_completed': primary['private_reference_matches'] == 0
            and primary['current_product_scan']['read_errors'] == 1
            and primary['unreadable_paths'] == [supplement['scope']] and supplement['passed']
            and supplement['combined_read_errors'] == 0
            and supplement['primary_scan_sha256'] == sha(LOCAL / 'privacy-latest-primary-v1.json'),
        'verification_scope_preserved': verification['passed'] and verification['focused_unique_test_count'] == 50
            and verification['current_catalog_count'] == 592 and not verification['full_current_suite_executed']
            and verification['runtime_before_startup_correction'] is True
            and not verification['runtime_after_startup_correction_executed']
            and verification['release_gate'] == 'no_go' and not verification['full_mvp_accepted'],
        'task_and_acceptance_not_completed': '\nstatus: in_progress\n' in (ROOT / 'docs/tasks/TASK.mvp-completion-09-11-2026.md').read_text('utf-8')
            and '\nstatus: draft\n' in (ROOT / 'docs/acceptance/AC-20260911-05.md').read_text('utf-8')}
    assert all(checks.values()), checks
    files = {'final-checker-v1.py': Path(__file__), 'acceptance-mapper.py': LOCAL / 'map_acceptance.py',
        'privacy-latest-primary-v1.json': LOCAL / 'privacy-latest-primary-v1.json',
        'privacy-latest-supplement-v1.json': LOCAL / 'privacy-latest-supplement-v1.json',
        'privacy-latest-checker-v1.py': LOCAL / 'verify_privacy_latest_v1.py',
        'privacy-latest-supplement-checker-v1.py': LOCAL / 'verify_privacy_latest_supplement_v1.py',
        'parent-review.txt': ROOT / 'docs/reviews/mvp-supervisor-20260912.md',
        'supervised-run-spec.txt': ROOT / 'docs/supervised-run-detail-spec.md'}
    assert all(not (OUT / name).exists() for name in files)
    assert not (OUT / 'final-check-v1.json').exists()
    for name, source in files.items():
        (OUT / name).write_bytes(source.read_bytes())
    (OUT / 'workflow-final-v1.json').write_bytes(completed.stdout)
    document_paths = ['README.md', 'HUB.codex.md', 'AGENTS.md', 'CHANGELOG.md', 'tools/README.md',
        'docs/tasks/TASK.mvp-completion-09-11-2026.md', 'docs/acceptance/AC-20260911-05.md',
        'docs/mvp-completion-audit.md', 'docs/supervised-run-detail-spec.md', 'docs/evidence/README.md',
        'docs/evidence/mvp-supervisor-20260912/README.md', 'docs/reviews/mvp-supervisor-20260912.md']
    result = {'schema_version': 1, 'checked_at': datetime.now(timezone.utc).isoformat(),
        'passed': True, 'checks': checks, 'verification_sha256': sha(OUT / 'verification.json'),
        'artifact_sha256': {name: sha(OUT / name) for name in [*files, 'acceptance-map.json', 'workflow-final-v1.json']},
        'document_sha256': {name: sha(ROOT / name) for name in document_paths},
        'birdseye_generation': index['generated_at'], 'birdseye_nodes': len(index['nodes']),
        'privacy_remaining_matches': 0, 'privacy_remaining_read_errors': 0,
        'parent_review_completed': True, 'external_model_review_performed_this_stage': False,
        'full_mvp_accepted': False, 'ci_eligible': False, 'release_gate': 'no_go'}
    (OUT / 'final-check-v1.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), 'utf-8')
    print(json.dumps({key: result[key] for key in ('passed', 'birdseye_generation', 'birdseye_nodes', 'full_mvp_accepted')}))


if __name__ == '__main__':
    main()
