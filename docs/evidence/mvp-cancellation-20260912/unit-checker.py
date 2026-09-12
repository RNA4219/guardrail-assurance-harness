"""全回帰と前工程の523件の保持を不変の新規記録へ保存する。"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / 'units-01'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    OUT.mkdir(exist_ok=False)
    paths = sorted([*ROOT.glob('src/gah/*.py'), *ROOT.glob('tests/test_*.py'), *ROOT.glob('tools/**/*.py')])
    hashes = {path.relative_to(ROOT).as_posix(): sha(path) for path in paths}
    previous = json.loads((ROOT / 'docs/evidence/mvp-regression-20260912/verification.json').read_text('utf-8'))
    (OUT / 'sources-start.json').write_text(json.dumps(hashes, indent=2), encoding='utf-8')
    with (OUT / 'unittest.log').open('xb') as stream:
        result = subprocess.run([sys.executable, '-E', '-X', 'utf8', '-m', 'unittest', 'discover', '-s', 'tests', '-v'],
            cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT, timeout=5400)
    log = (OUT / 'unittest.log').read_text('utf-8')
    tests = sorted(set(location for _, location in re.findall(r'^(test_\S+) \(([^)\r\n]+)\)', log, re.MULTILINE)))
    old = set(previous['tests'])
    missing = sorted(old - set(tests))
    summary = re.search(r'Ran (\d+) tests in ([0-9.]+)s', log)
    checks = {'unittest_succeeded': result.returncode == 0 and log.rstrip().endswith('OK'),
        'executed_test_count_matches': summary is not None and int(summary[1]) == len(tests),
        'previous_523_tests_retained': len(old) == 523 and not missing,
        'sources_unchanged': all(sha(ROOT / p) == value for p, value in hashes.items()),
        'seven_protected_sources_unchanged': len(previous['preserved']) == 7 and all(
            sha(ROOT / p) == value['actual_sha256'] for p, value in previous['preserved'].items())}
    record = {'schema_version': 1, 'checked_at': datetime.now(timezone.utc).isoformat(),
        'passed': all(checks.values()), 'checks': checks, 'exit_code': result.returncode,
        'tests': tests, 'test_counts': {'total': len(tests), 'previous_retained': len(old)-len(missing),
            'new': len(set(tests)-old)}, 'elapsed_seconds': float(summary[2]) if summary else None,
        'missing_previous_tests': missing, 'source_sha256': hashes, 'preserved': previous['preserved'],
        'unittest_log_sha256': sha(OUT / 'unittest.log'), 'checker_sha256': sha(Path(__file__)),
        'full_mvp_accepted': False, 'ci_eligible': False}
    with (OUT / 'check.json').open('x', encoding='utf-8') as stream:
        json.dump(record, stream, indent=2)
    print(json.dumps({k: record[k] for k in ('passed', 'checks', 'test_counts', 'elapsed_seconds')}))
    return 0 if record['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
