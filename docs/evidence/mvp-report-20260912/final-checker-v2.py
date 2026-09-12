"""要約の原文、閲覧用文書、実行ソース、先行証跡を最終照合する。"""
from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from tools.gah_report import render_markdown
OUT=ROOT/'docs/evidence/mvp-report-20260912'
read=lambda p:json.loads(p.read_text(encoding='utf-8'))
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
v=read(OUT/'verification-v2.json')
prior=read(OUT/'verification.json')
end=ROOT/'docs/evidence/mvp-cancellation-20260912/final-check-v2.json'
cancellation=read(end)
checks={'artifact_hashes_match':all(sha(OUT/p)==d for p,d in v['artifact_sha256'].items()),
 'current_sources_match':all(sha(ROOT/p)==d for p,d in v['source_sha256'].items()),
 'protected_seven_match':len(v['preserved'])==7 and all(sha(ROOT/p)==d['actual_sha256']==d['previous_sha256'] for p,d in v['preserved'].items()),
 'prior_verification_preserved':sha(OUT/'verification.json')==v['prior_verification_sha256'],
 'workflow_and_privacy_passed':cancellation['passed'] and cancellation['checks']['workflow_passed'] and cancellation['checks']['privacy_coverage_completed'],
 'scope_consistent':v['passed'] and v['scope']=='fixed_uc_ci_read_only_report' and v['full_mvp_accepted'] is False and v['ci_eligible'] is False}
for run_id in ('regression-runtime','cancellation-runtime'):
 name=run_id+'-report.md';mapping=v['prior_artifact_raw_mapping'][name]
 raw=(OUT/mapping['raw_path']).read_bytes()
 report=read(OUT/(run_id+'-stdout.json'))
 checks[run_id+'_raw_preserved']=hashlib.sha256(raw).hexdigest()==mapping['raw_sha256']==prior['artifact_sha256'][name]
 checks[run_id+'_human_same_json_grounding']=render_markdown(report).encode('utf-8')==raw.replace(b'\r\n',b'\n')
 checks[run_id+'_wrapper_preserves_body']=(OUT/name).read_bytes().endswith(raw)
record={'schema_version':1,'checked_at':datetime.now(timezone.utc).isoformat(),'passed':all(checks.values()),'checks':checks,
 'verification_sha256':sha(OUT/'verification-v2.json'),'preceding_final_check_sha256':sha(end),
 'birdseye_generation':cancellation['birdseye_generation'],'birdseye_nodes':cancellation['birdseye_nodes'],
 'test_counts':v['test_counts'],'parent_review_completed':True,'full_mvp_accepted':False,'release_gate':'no_go','ci_eligible':False}
(OUT/'final-checker-v2.py').write_bytes(Path(__file__).read_bytes())
record['checker_sha256']=sha(OUT/'final-checker-v2.py')
record['prior_failed_final_check_sha256']=sha(OUT/'final-check.json')
record['render_comparison_newline_normalization']='CRLF_to_LF_only; raw bytes preserved'
with (OUT/'final-check-v2.json').open('x',encoding='utf-8') as stream:json.dump(record,stream,ensure_ascii=False,indent=2)
print(json.dumps(record,ensure_ascii=False))
raise SystemExit(0 if record['passed'] else 1)
