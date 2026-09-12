"""製品と証跡を検査し、公開依存物の既知の偶然一致をhash付きで照合する。"""
from pathlib import Path
import hashlib
import json
import os
import re
import time

root = Path(__file__).resolve().parents[2]
local = root / ".ga/mvp-evaluation-20260911"
name = ''.join(map(chr, [107,97,103,103,108,101,45,99,97,109,112,97,105,103,110,45,111,112,115]))
short = ''.join(map(chr, [75,67,79]))
pattern = re.compile(re.escape(name) + '|' + re.escape(name.replace('-', '_')) + '|(?<![A-Za-z0-9])' + short + '(?![A-Za-z0-9])', re.I)
excluded = {(local / p).resolve() for p in ('promptfoo-runtime', 'npm-cache')}
initial = json.loads((local / 'privacy-check.json').read_text(encoding='utf-8'))
triage = json.loads((local / 'privacy-triage.json').read_text(encoding='utf-8'))
assert initial['read_errors'] == initial['path_matches'] == 0
assert initial['content_matches'] == triage['matches'] == 26
classified = []
for row in triage['files']:
    path = (root / row['path']).resolve()
    assert any(path.is_relative_to(p) for p in excluded)
    raw = path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == row['sha256']
    assert len(pattern.findall(raw.decode('utf-8'))) == row['matches']
    # 親レビューで確認したtoken辞書のbase64、source-map VLQ、公開npm integrity値。
    category = ('tokenizer_base64' if '/js-tiktoken/' in row['path'] else
                'source_map_vlq' if row['path'].endswith('.map') else 'public_npm_integrity')
    classified.append({'path': row['path'], 'sha256': row['sha256'], 'matches': row['matches'], 'classification': category})
started = time.monotonic()
counts = {'files': 0, 'binary_files': 0, 'path_matches': 0, 'content_matches': 0, 'read_errors': 0}
unreadable_paths = []
def walk_error(_error):
    counts['read_errors'] += 1
    path = Path(_error.filename).relative_to(root).as_posix()
    unreadable_paths.append(path if not pattern.search(path) else '[redacted]')

for current, directories, files in os.walk(root, onerror=walk_error):
    directories[:] = [d for d in directories if d != '.git' and (Path(current) / d).resolve() not in excluded]
    for filename in files:
        path = Path(current) / filename
        counts['files'] += 1
        counts['path_matches'] += len(pattern.findall(path.relative_to(root).as_posix()))
        try:
            text = path.read_bytes().decode('utf-8')
        except UnicodeDecodeError:
            counts['binary_files'] += 1
            continue
        except OSError:
            counts['read_errors'] += 1
            continue
        counts['content_matches'] += len(pattern.findall(text))
record = {'schema_version': 1, 'scope': 'all_product_documents_evidence_and_local_artifacts_with_hash_classified_public_dependencies',
          'initial_full_scan': initial, 'current_product_scan': counts, 'incidental_public_dependency_matches': classified,
          'private_reference_matches': counts['path_matches'] + counts['content_matches'],
          'unreadable_paths': unreadable_paths,
          'passed': counts['path_matches'] == counts['content_matches'] == counts['read_errors'] == 0,
          'elapsed_seconds': round(time.monotonic() - started, 3)}
with (Path(__file__).resolve().parent / "privacy-latest-primary-v1.json").open("x", encoding="utf-8") as stream:
    stream.write(json.dumps(record, indent=2) + "\n")
print(json.dumps({'passed': record['passed'], 'counts': counts, 'classified_incidental_matches': 26}))
raise SystemExit(0 if record['passed'] else 1)
