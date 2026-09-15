"""製品監督の通常完了・中断回収・fresh CIを固定Dockerで検査する。"""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

from gah.docker_runner import DockerRunner
from gah.execution_journal import ExecutionJournal
from gah.run_contracts import content_ref
from tools.gah_run import execute

ROOT=Path(__file__).resolve().parents[1]


class Interrupted(BaseException):
    pass


class TrackedRunner:
    def __init__(self,runner,receipts,save):
        self.runner=runner;self.receipts=receipts;self.save=save;self.active=[]
    def __getattr__(self,name):return getattr(self.runner,name)
    def run(self,scenario,binding,**kwargs):
        self.active.append((binding['run_id'],binding['operation_id']))
        result=self.runner.run(scenario,binding,**kwargs)
        self.receipts.append(result);self.save()
        return result


def verify(*, runtime, call, check, contract, receipts, save_observations):
    connected=SimpleNamespace(prefix=runtime.prefix,lock=runtime.lock,client=call)
    tracked=[];requests={};folders={}
    def prepare(identifier,trigger,changed_refs=None):
        folder=runtime.folder/'supervised'/hashlib.sha256(identifier.encode()).hexdigest()
        folder.mkdir(parents=True,exist_ok=False)
        runner=TrackedRunner(DockerRunner(ROOT/'config/fixture-runtime.lock.json',folder/'execution.sqlite'),receipts,save_observations)
        tracked.append(runner)
        request={'schema_version':1,'run_id':identifier,'contract_series_id':'fixture-contract-series',
            'expected_contract_ref':content_ref('evaluation_contract',contract['contract_id'],contract),'trigger':trigger}
        if changed_refs is not None:
            request['changed_refs']=changed_refs
        path=runtime.folder/(identifier+'-request.json')
        path.write_text(json.dumps(request,ensure_ascii=False,indent=2),'utf-8')
        requests[identifier]=request;folders[identifier]=folder
        return folder,runner,request
    normal_id='supervised-normal-runtime'
    normal,normal_runner,normal_request=prepare(normal_id,'scheduled_full')
    def progress(stage):
        if stage.startswith('end-'):
            check('supervisor_execution_'+stage[4:],True)
    try:
        normal_result=execute(connected,normal_runner,normal,normal_request,'run',hook=progress)
        check('supervisor_normal_fresh_ci_zero',normal_result['exit_code']==0 and normal_result['ci_eligible'] is True)
        check('supervisor_scheduled_full_scope',normal_result['trigger']=='scheduled_full'
            and normal_result['executed_scope']=='full' and len(normal_result['control_ids'])==15)
        check('supervisor_thirty_real_executions',len(normal_runner.active)==30)
        before=len(receipts)
        repeated=execute(connected,normal_runner,normal,normal_request,'resume')
        check('supervisor_resume_keeps_receipt_without_reexecution',repeated['exit_code']==0
            and repeated['gate']['outputs_ref']==normal_result['gate']['outputs_ref'] and len(receipts)==before)
        cli=subprocess.run([sys.executable,'-E','-B','-X','utf8','-m','tools.gah_run','status',
            '--runtime',str(runtime.folder),'--request',str(runtime.folder/(normal_id+'-request.json'))],
            cwd=ROOT,capture_output=True,timeout=240)
        try:cli_result=json.loads(cli.stdout)
        except (ValueError,UnicodeError):cli_result={}
        check('supervisor_cli_status_uses_current_authority',cli.returncode==0
            and cli_result.get('gate',{}).get('outputs_ref')==normal_result['gate']['outputs_ref']
            and cli_result.get('ci_eligible') is True)
        (runtime.folder/'supervisor-cli-status.json').write_bytes(cli.stdout)

        template=call(12004,{'schema_version':1,'action':'run_prepare','request_id':'supervisor-scope-template',
            'run_id':'supervisor-scope-template','contract_series_id':'fixture-contract-series',
            'expected_contract_ref':normal_request['expected_contract_ref']})
        target=template['bound_run']['registry']['controls'][0]['target_ref']
        scoped_id='supervised-targeted-runtime'
        scoped,scoped_runner,scoped_request=prepare(scoped_id,'change',[target])
        scoped_result=execute(connected,scoped_runner,scoped,scoped_request,'run',hook=progress)
        check('supervisor_targeted_two_executions',len(scoped_runner.active)==2)
        check('supervisor_targeted_scope_ci_zero',scoped_result['exit_code']==0
            and scoped_result['executed_scope']=='targeted' and len(scoped_result['control_ids'])==1
            and len(scoped_result['unexecuted_control_ids'])==14 and scoped_result['ci_eligible'] is True)
        gate={'schema_version':1,'action':'ci_check','request_id':'supervisor-scope-report','run_id':scoped_id,
            'expected_manifest_ref':scoped_result['gate']['expected_manifest_ref'],
            'expected_contract_ref':normal_request['expected_contract_ref'],
            'expected_baseline_ref':contract['comparison']['baseline_ref'],
            'expected_target_refs':[target],'expected_use_cases':['UC-CI']}
        from tools.gah_report import build_report,render_markdown
        report=build_report(connected,gate)
        check('supervisor_targeted_report_omits_unexecuted',report['scope']['executed_scope']=='targeted'
            and report['scope']['unexecuted_control_ids']==scoped_result['unexecuted_control_ids'])
        (runtime.folder/'supervisor-targeted-report.md').write_text(render_markdown(report),encoding='utf-8')
        mismatch=call(12004,{**gate,'request_id':'supervisor-scope-mismatch',
            'expected_target_refs':template['bound_run']['manifest']['target_refs']})
        check('supervisor_targeted_cannot_satisfy_full_ci',mismatch.get('exit_code')==1 and mismatch.get('ci_eligible') is False
            and mismatch.get('reasons')==['CI_TARGET_MISMATCH'])
        baseline_current=call(12004,{'schema_version':1,'action':'baseline_current',
            'request_id':'supervisor-scope-baseline-current','series_id':'fixture-baseline-series'})
        proposed=call(12001,{'schema_version':1,'action':'baseline_propose','request_id':'supervisor-scope-baseline',
            'run_id':scoped_id,'series_id':'fixture-baseline-series','proposal_id':'supervisor-scoped-baseline',
            'expected_generation':baseline_current['generation']})
        check('supervisor_targeted_cannot_promote_baseline',proposed.get('kind')=='authority_error'
            and proposed.get('reason')=='FULL_SCOPE_REQUIRED' and proposed.get('ci_eligible') is False)
        count=len(receipts)
        resumed=execute(connected,scoped_runner,scoped,scoped_request,'resume')
        check('supervisor_targeted_resume_keeps_scope_and_execution_count',resumed['exit_code']==0
            and resumed['unexecuted_control_ids']==scoped_result['unexecuted_control_ids'] and len(receipts)==count)
        interrupted_id='supervised-interrupted-runtime'
        folder,runner,request=prepare(interrupted_id,'manual')
        def interrupt(stage):
            if stage.startswith('runner-returned-'):raise Interrupted()
        interrupted=False
        try:execute(connected,runner,folder,request,'run',hook=interrupt)
        except Interrupted:interrupted=True
        check('supervisor_interruption_after_one_execution',interrupted and len(runner.active)==1)
        recovered=execute(connected,runner,folder,request,'resume')
        check('supervisor_missing_finish_time_cancels',recovered['exit_code']==3
            and recovered['ci_eligible'] is False and len(runner.active)==1)
        check('supervisor_recovered_budget_closed',execute(connected,runner,folder,request,'status')['resources']['budget_closure'] is True)
        runtime.restart_broker()
        check('supervisor_restart_keeps_current_ci',execute(connected,normal_runner,normal,normal_request,'status')['exit_code']==0)
        check('supervisor_restart_keeps_cancelled',execute(connected,runner,folder,request,'status')['exit_code']==3)
        (runtime.folder/'supervisor-results.json').write_text(json.dumps({'normal':normal_result,
            'targeted':scoped_result,'interrupted':recovered,'full_mvp_accepted':False},ensure_ascii=False,indent=2),'utf-8')
    finally:
        recovered_all=True
        for runner in tracked:
            for run_id,operation_id in runner.active:
                try:
                    result=runner.recover(run_id,operation_id)
                    recovered_all &= result['stop_confirmed'] is True and result['cleanup_confirmed'] is True
                except Exception:recovered_all=False
            with ExecutionJournal(runner.journal_path) as journal:
                recovered_all &= journal.pending()==[]
        check('supervisor_all_owned_executions_stopped_and_cleaned',recovered_all)
    def after_revocation():
        result=execute(connected,normal_runner,normal,normal_request,'status')
        check('supervisor_fresh_ci_fails_after_source_revocation',result['exit_code']==1 and result['ci_eligible'] is False)
        check('supervisor_past_outputs_remain_unchanged',result['gate']['outputs_ref']==normal_result['gate']['outputs_ref'])
        targeted=execute(connected,scoped_runner,scoped,scoped_request,'status')
        check('supervisor_targeted_revocation_keeps_past_scope',targeted['exit_code']==1
            and targeted['ci_eligible'] is False and targeted['gate']['outputs_ref']==scoped_result['gate']['outputs_ref'])
        check('supervisor_cancel_cannot_become_success_after_revocation',execute(connected,runner,folder,request,'status')['exit_code']==3)
    return after_revocation
