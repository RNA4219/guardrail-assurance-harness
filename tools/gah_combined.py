"""UC-CIとUC-LLMを事前固定した複合runを開始・再開・取消し・照会する。"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from gah.combined_runs import validate_request,USES
from gah.contracts import MAX_DOCUMENT_BYTES,decode_document
from gah.docker_runner import DockerRunner,operation_lock
from gah.guardrail_runner import GuardrailRunner
from gah.supervisor_checkpoint import Checkpoint,_plain_directory
from gah.wire import canonical_bytes
from tools.authority_runtime import AuthorityRuntime
from tools.gah_run import execute as run_child
from tools.gah_report import build_report,render_markdown as render_child


def _check(value,action,request_id):
    if (type(value) is not dict or value.get('kind')!='evaluation_authority_result'
            or value.get('action')!=action or value.get('request_id')!=request_id):
        raise ValueError('COMBINED_AUTHORITY_UNAVAILABLE')
    return value


def execute(runtime,runners,folder,request,mode,*,clock=None,hook=None):
    request=validate_request(request)
    if request['action']!='combined_prepare' or mode not in ('run','resume','cancel','status') or set(runners)!=set(USES):
        raise ValueError('INVALID_REQUEST')
    checkpoint=Checkpoint(folder/'checkpoints')
    checkpoint.put('identity',{'request':request,'authority_image':runtime.lock['image_id'],'runtime_prefix':runtime.prefix,
        'runners':{use:{'image':runners[use].lock['image_id'],'worker':runners[use].lock['worker_digest']} for use in USES},
        'sources':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in
            ('tools/gah_combined.py','src/gah/combined_runs.py','tools/gah_run.py','src/gah/llm_supervised_run.py','src/gah/supervised_run.py')}})
    if mode!='run' and checkpoint.get('prepare-request') is None:raise ValueError('CHECKPOINT_REQUIRED')
    checkpoint.put('prepare-request',request)
    prepared=_check(runtime.client(12004,request),'combined_prepare',request['request_id'])
    checkpoint.put('prepare-response',prepared)
    ref=prepared['manifest_ref'];manifest=prepared['manifest']
    tag='combined-'+hashlib.sha256(canonical_bytes(request)).hexdigest()[:24]
    def query(action,suffix):
        req={'schema_version':1,'action':action,'request_id':tag+'-'+suffix,'run_id':request['run_id'],'expected_manifest_ref':ref}
        value=_check(runtime.client(12004,req),action,req['request_id'])
        if value.get('manifest_ref')!=ref:raise ValueError('COMBINED_BINDING_MISMATCH')
        return value
    current=query('combined_current','current')
    if mode=='status':return current
    if current['receipt'] is not None:return _report(runtime,current,query)
    cancelling=mode=='cancel' or current['cancelled']
    if cancelling and not current['cancelled']:query('combined_cancel','cancel')
    def child_input(child):
        return {'schema_version':1,'run_id':child['run_id'],'contract_series_id':child['contract_series_id'],
            'expected_contract_ref':child['contract_ref'],'trigger':'manual'}
    for child in manifest['children']:
        child_folder=folder/child['use_case'];child_folder.mkdir(exist_ok=True)
        child_mode='cancel' if cancelling else ('resume' if Checkpoint(child_folder/'checkpoints').get('request-prepare') is not None else 'run')
        if cancelling:
            status=runtime.client(12004,{'schema_version':1,'action':'run_status','request_id':tag+'-status-'+child['use_case'],'run_id':child['run_id']})
            if status.get('kind')=='authority_error' and status.get('reason')=='RUN_MISSING':continue
        result=run_child(runtime,runners[child['use_case']],child_folder,child_input(child),child_mode,clock=clock,hook=hook)
        if result['exit_code'] in (2,3) and not cancelling:
            query('combined_cancel','cancel');cancelling=True
    current=query('combined_current','current')
    if not current['cancelled'] or current['active_operations']==0:
        query('combined_finalize','finalize')
    return _report(runtime,query('combined_current','current'),query)


def _report(runtime,current,query):
    reports=[]
    for child,summary in zip(current['manifest']['children'],current['children']):
        report=None
        if summary['gate']['outputs_ref'] is not None:
            req={'schema_version':1,'action':'ci_check','request_id':'combined-report-'+child['run_id'],
                'run_id':child['run_id'],'expected_manifest_ref':child['manifest_ref'],
                'expected_contract_ref':child['contract_ref'],'expected_baseline_ref':child['baseline_ref'],
                'expected_target_refs':child['target_refs'],'expected_use_cases':[child['use_case']]}
            report=build_report(runtime,req)
        reports.append({'use_case':child['use_case'],'report':report})
    # 子の表示を取得した後も、複合run全体の現在有効性を再確認する。
    final=query('combined_current','current')
    return {**final,'reports':reports}


def render_markdown(result):
    lines=['# 両用途の評価結果','', '判定: '+result['assurance'],
        'CI利用: '+('可' if result['ci_eligible'] else '不可'),
        '理由: '+(', '.join(result['reasons']) or 'なし'),'']
    for item in result.get('reports',[]):
        lines.extend(['## '+item['use_case'],'',render_child(item['report']) if item['report'] is not None else '必要な成果物が未完了。',''])
    return '\n'.join(lines)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=('run','resume','cancel','status'))
    parser.add_argument('--runtime',required=True);parser.add_argument('--request',required=True)
    parser.add_argument('--format',choices=('json','markdown'),default='json');args=parser.parse_args()
    try:
        folder=_plain_directory(Path(args.runtime))
        if not folder.is_relative_to(ROOT) or not (folder/'deployment.json').is_file():raise ValueError('EXISTING_RUNTIME_REQUIRED')
        with Path(args.request).open('rb') as stream:request=validate_request(decode_document(stream.read(MAX_DOCUMENT_BYTES+1)))
        group=_plain_directory(folder/'combined'/hashlib.sha256(request['run_id'].encode()).hexdigest())
        if args.mode!='run' and not group.is_dir():raise ValueError('CHECKPOINT_REQUIRED')
        group.mkdir(parents=True,exist_ok=True)
        with operation_lock(folder/'supervised-transport','deployment','supervisor'),operation_lock(group/'execution.sqlite',request['run_id'],'combined'):
            runtime=AuthorityRuntime(folder, reuse_clients=True, keep_clients_running=True)
            try:
                for use in USES:(group/use).mkdir(exist_ok=True)
                runners={'UC-CI':DockerRunner(ROOT/'config/fixture-runtime.lock.json',group/'UC-CI'/'execution.sqlite'),
                    'UC-LLM':GuardrailRunner(group/'UC-LLM'/'execution.sqlite')}
                result=execute(runtime,runners,group,request,args.mode)
            finally:
                runtime.close_clients()
        sys.stdout.write((render_markdown(result) if args.format=='markdown' else json.dumps(result,ensure_ascii=False,sort_keys=True,allow_nan=False))+'\n')
        return result['exit_code']
    except (Exception,KeyboardInterrupt):
        print(json.dumps({'schema_version':1,'kind':'combined_run_error','reason':'COMBINED_INCOMPLETE','ci_eligible':False,'exit_code':2}))
        return 2


if __name__=='__main__':raise SystemExit(main())
