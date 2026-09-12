"""回収済み検証DBを別のローカル導線から読み、製品CLIの要約を確認する。"""
from pathlib import Path
import hashlib
import json
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
LOCAL=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
from tools.authority_runtime import AuthorityRuntime
from tools.gah_report import render_markdown
from gah.run_contracts import content_ref


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    previous=ROOT/'.ga/mvp-cancellation-20260912/runtime-01'
    prior=json.loads((previous/'check.json').read_text(encoding='utf-8'))
    assert prior['passed'] and prior['checks']['authority_cleanup'] and prior['checks']['authority_cleanup_rechecked']
    local=LOCAL/'runtime-02'
    local.mkdir(exist_ok=False)
    deployment=json.loads((previous/'deployment.json').read_text(encoding='utf-8'))
    # 旧試験のdeploymentと観測記録は更新しない。回収済みの同じ保存volumeを読む。
    (local/'deployment.json').write_text(json.dumps({**deployment,'containers':[]}),encoding='utf-8')
    observations=json.loads((previous/'observations.json').read_text(encoding='utf-8'))
    manifests={item['response']['bound_run']['manifest']['run_id']:item['response']['bound_run']['manifest']
        for item in observations if item['action']=='run_prepare' and 'bound_run' in item['response']}
    paths=['tools/gah_report.py','tools/gah_ci.py','tools/authority_runtime.py','tests/test_run_report.py',
        'config/authority-runtime.lock.json']
    sources={p:sha(ROOT/p) for p in paths}
    runtime=AuthorityRuntime(local)
    checks={}
    failure=None
    reports=[]
    try:
        runtime.prepare()
        for run_id,expected in [('regression-runtime',1),('cancellation-runtime',3)]:
            manifest=manifests[run_id]
            request={'schema_version':1,'action':'ci_check','request_id':'live-report-'+run_id,'run_id':run_id,
                'expected_manifest_ref':content_ref('run_manifest',run_id,manifest),
                'expected_contract_ref':manifest['contract_ref'],'expected_baseline_ref':manifest['baseline_ref'],
                'expected_target_refs':manifest['target_refs'],'expected_use_cases':manifest['use_cases']}
            file=local/(run_id+'-request.json')
            file.write_text(json.dumps(request),encoding='utf-8')
            result=subprocess.run([sys.executable,'-E','-B','-X','utf8','-m','tools.gah_report',
                '--runtime',str(local),'--request',str(file),'--format','json'],cwd=ROOT,capture_output=True,timeout=120)
            (local/(run_id+'-stdout.json')).write_bytes(result.stdout)
            (local/(run_id+'-stderr.txt')).write_bytes(result.stderr)
            report=json.loads(result.stdout)
            reports.append(report)
            checks[run_id+'_product_cli_exit']=result.returncode==expected and report.get('exit_code')==expected
            checks[run_id+'_report_shape']=report.get('kind')=='run_report' and report.get('run_id')==run_id and report.get('ci_eligible') is False
            if report.get('kind')!='run_report':
                continue
            checks[run_id+'_scope_binding']=report['scope']['target_refs']==manifest['target_refs'] and report['source_refs']['manifest_ref']==request['expected_manifest_ref']
            checks[run_id+'_time_and_refs']=report['observed_at']<=report['checked_at'] and report['valid_until']>=report['observed_at'] and report['outputs_ref']['id']==run_id
            checks[run_id+'_execution_status']=report['execution_status']==('CANCELLED' if expected==3 else 'COMPLETED')
            text=render_markdown(report)
            (local/(run_id+'-report.md')).write_text(text,encoding='utf-8')
            checks[run_id+'_human_same_grounding']='現在のCI利用: 不可' in text and report['outputs_ref']['digest'] in text and all(str(report[k]) in text for k in ('checked_at','observed_at','valid_until'))
    except Exception as error:
        failure=type(error).__name__
    finally:
        try:
            runtime=AuthorityRuntime(local)
            runtime.cleanup(remove_state=False)
            for name in runtime.state['containers']:
                runtime._confirm_absent(name)
            checks['authority_cleanup_rechecked']=True
        except Exception:
            checks['authority_cleanup_rechecked']=False
    checks['sources_unchanged']=all(sha(ROOT/p)==d for p,d in sources.items())
    checks['original_deployment_unchanged']=json.loads((previous/'deployment.json').read_text(encoding='utf-8'))==deployment
    record={'schema_version':1,'passed':failure is None and len(checks)==15 and all(checks.values()),
        'checks':checks,'failure':failure,'source_sha256':sources,'original_deployment_sha256':sha(previous/'deployment.json'),
        'image_id':runtime.lock['image_id'],'report_exit_codes':[r.get('exit_code') for r in reports],
        'fixed_uc_ci_only':True,'full_mvp_accepted':False,'ci_eligible':False}
    (local/'check.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(record,ensure_ascii=False))
    return 0 if record['passed'] else 1


if __name__=='__main__':
    raise SystemExit(main())
