"""過去の関連証拠と今回のfocused実行を分けて32受入へ対応付ける。"""
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'docs/evidence/mvp-supervisor-20260912'
PRIOR = ROOT / 'docs/evidence/mvp-baseline-refresh-20260912'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    prior = json.loads((PRIOR / 'acceptance-map.json').read_text('utf-8'))
    verification = json.loads((OUT / 'verification.json').read_text('utf-8'))
    runtime = json.loads((OUT / 'runtime-check.json').read_text('utf-8'))
    assert prior['requirements_tracked'] == 32 and verification['passed']
    audit = {}
    for line in (ROOT / 'docs/mvp-completion-audit.md').read_text('utf-8').splitlines():
        match = re.match(r'\| R(\d{2}) ([^|]+)\| ([^|]+)\| ([^|]+)\|$', line)
        if match:
            audit[int(match[1])] = [v.strip() for v in match.groups()[1:]]
    definitions = {}
    for line in (ROOT / 'docs/acceptance-criteria.md').read_text('utf-8').splitlines():
        match = re.match(r'\| GAH-AC(\d{2}) \| GAH-R(\d{2}) \|', line)
        if match:
            assert match[1] == match[2]
            definitions[int(match[1])] = hashlib.sha256(line.encode()).hexdigest()
    assert set(audit) == set(definitions) == set(range(1, 33))
    rows = []
    for old in prior['requirements']:
        number = int(old['requirement_id'][-2:])
        prefixes = {name.split('.')[0] + '.' for name in old['related_unit_tests']}
        if number in {4, 13, 14, 18, 20, 23, 26, 27, 31}:
            prefixes.update({'test_supervised_run.', 'test_supervised_startup.'})
        if number in {4, 13, 18, 23, 26, 31}:
            prefixes.update({'test_resource_operation.', 'test_operation_integration.'})
        if number in {18, 23, 26, 31}:
            prefixes.add('test_supervisor_checkpoint.')
        checks = []
        if number in {4, 12, 13, 14, 18, 20, 23, 26, 27, 31}:
            checks = [name for name, passed in runtime['checks'].items()
                if passed and name.startswith('supervisor_') and not name.startswith('supervisor_execution_')]
        current = [name for name in verification['tests'] if name.startswith(tuple(prefixes))]
        rows.append({'requirement_id': old['requirement_id'], 'acceptance_id': old['acceptance_id'],
            'definition_row_sha256': definitions[number], 'title': audit[number][0],
            'current_implementation_scope': audit[number][1], 'remaining': audit[number][2],
            'status': 'partial', 'product_acceptance_passed': False,
            'historical_related_unit_tests': old['related_unit_tests'],
            'historical_related_runtime_checks': old['related_runtime_checks'],
            'current_stage_related_unit_tests': current, 'current_stage_related_runtime_checks': checks,
            'pending_connection_specifications': old['pending_connection_specifications']})
    result = {'schema_version': 2, 'kind': 'mvp_acceptance_evidence_map',
        'verification_sha256': sha(OUT / 'verification.json'),
        'historical_evidence_map_sha256': sha(PRIOR / 'acceptance-map.json'),
        'requirements_source_sha256': sha(ROOT / 'docs/requirements.md'),
        'acceptance_source_sha256': sha(ROOT / 'docs/acceptance-criteria.md'),
        'audit_source_sha256': sha(ROOT / 'docs/mvp-completion-audit.md'),
        'mapping_meaning': '過去工程と今回実行したfocused試験を分けた関連証拠。受入条件全体の成功を意味しない。',
        'requirements': rows, 'requirements_tracked': 32, 'requirements_accepted': 0,
        'full_mvp_accepted': False, 'release_gate': 'no_go', 'ci_eligible': False}
    target = OUT / 'acceptance-map.json'
    assert not target.exists()
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), 'utf-8')
    print(json.dumps({'requirements_tracked': 32, 'requirements_accepted': 0}))


if __name__ == '__main__':
    main()
