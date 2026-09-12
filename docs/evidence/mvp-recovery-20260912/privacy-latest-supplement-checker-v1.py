"""Pythonで列挙できなかった固定一箇所をnative読取りで補完する。ACLは変更しない。"""
import base64
import hashlib
import json
from pathlib import Path
import subprocess

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
previous = json.loads((HERE / 'privacy-latest-primary-v1.json').read_text('utf-8'))
target = '.ga/authority-images/a3117c1630933eb7-xa1d5cpf'
assert previous['unreadable_paths'] == [target]
assert previous['current_product_scan']['read_errors'] == 1
assert previous['private_reference_matches'] == 0
# 検索語を再掲せず、既存checkerの同じcodepoint定義・依存分類を使用する。
source = (HERE / 'verify_privacy.py').read_text('utf-8')
prefix = source.split('started = time.monotonic()', 1)[0]
scope = {'__file__': str(HERE / 'verify_privacy.py')}
exec(compile(prefix, str(HERE / 'verify_privacy.py'), 'exec'), scope)
pattern = scope['pattern']
result = subprocess.run(['pwsh', '-NoProfile', '-NonInteractive', '-File',
    str(HERE / 'privacy_native_read.ps1'), '-Target', str(ROOT / target)],
    capture_output=True, timeout=30)
assert result.returncode == 0 and len(result.stdout) < 2 * 1024 * 1024
rows = json.loads(result.stdout)
assert isinstance(rows, list) and rows
matches = 0
hashes = {}
for row in rows:
    matches += len(pattern.findall(target + '/' + row['path']))
    if row['directory']:
        continue
    data = base64.b64decode(row['data'], validate=True)
    matches += len(pattern.findall(data.decode('utf-8')))
    hashes[row['path']] = hashlib.sha256(data).hexdigest()
record = {'schema_version': 1, 'method': 'native_read_only_supplement',
    'scope': target, 'source_sha256': hashes, 'files': len(hashes),
    'private_reference_matches': matches, 'read_errors': 0,
    'primary_scan_sha256': hashlib.sha256((HERE / 'privacy-latest-primary-v1.json').read_bytes()).hexdigest(),
    'passed': matches == 0, 'combined_read_errors': 0,
    'historical_read_failures_preserved': True, 'acl_changed': False}
with (HERE / 'privacy-latest-supplement-v1.json').open('x', encoding='utf-8') as stream:
    json.dump(record, stream, indent=2)
print(json.dumps({k: record[k] for k in ('passed', 'files', 'private_reference_matches', 'combined_read_errors')}))
raise SystemExit(0 if record['passed'] else 1)
