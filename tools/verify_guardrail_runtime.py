"""固定合成guardrailの400ケースをOS認証・資源台帳・Evidence・初回baselineへ結ぶ。"""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import time
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from gah.guardrail_runner import GuardrailRunner
from gah import guardrail_results
from gah.policy import initial_policy_profile
from gah.run_contracts import content_ref
from gah.wire import canonical_bytes
from tools.authority_runtime import AuthorityRuntime


def request(action,identifier,**fields):
    return {"schema_version":1,"action":action,"request_id":identifier,**fields}


def write(path,value):
    with path.open('xb') as stream:
        stream.write(canonical_bytes(value));stream.flush()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    out=Path(args.output).resolve()
    if not out.is_relative_to(ROOT):raise SystemExit('OUTPUT_OUTSIDE_WORKSPACE')
    out.mkdir(parents=True,exist_ok=False)
    (out/'calls').mkdir();(out/'cases').mkdir()
    runtime=AuthorityRuntime(out/'authority',reuse_clients=True,keep_clients_running=True)
    runner=GuardrailRunner(out/'executions.sqlite')
    checks={};active={};executed=0;stages=0;call_count=0;failure=None
    bound=None;owner=None;counts={key:0 for key in ('TP','FN','FP','TN')}
    sources={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in runtime.lock['source_sha256']}
    if sources!=runtime.lock['source_sha256']:raise SystemExit('SOURCE_LOCK_MISMATCH')
    write(out/'sources.json',{'authority_image':runtime.lock['image_id'],'source_sha256':sources,
        'driver_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'authority_runtime_sha256':hashlib.sha256((ROOT/'tools/authority_runtime.py').read_bytes()).hexdigest()})

    def check(name,value):
        checks[name]=type(value) is bool and value
        print(json.dumps({'check':name,'passed':checks[name]}),flush=True)
        if not checks[name]:raise AssertionError(name)

    def call(uid,value):
        nonlocal call_count
        call_count+=1
        result=runtime.client(uid,value)
        write(out/'calls'/(str(call_count).zfill(5)+'.json'),{'uid':uid,'request':value,'response':result})
        return result

    def success(uid,value):
        result=call(uid,value)
        kind={'evidence_open':'run_evidence_run','evidence_record':'attempt_receipt','evidence_finalize':'authority_run_receipt'}.get(value['action'],'evaluation_authority_result')
        if value['action'].startswith('baseline_'):kind='baseline_authority_result'
        if value['action'] in {'propose','validate','adopt'}:kind='policy_adoption_result'
        if (type(result.get('schema_version')) is not int or result['schema_version']!=1 or result.get('kind')!=kind
                or result.get('action')!=value['action'] or result.get('request_id')!=value['request_id'] or result.get('ci_eligible') is not False):
            error=RuntimeError('AUTHORITY_REJECTED')
            error.detail={'action':value['action'],'reason':result.get('reason')}
            raise error
        return result

    try:
        runtime.prepare()
        for uid in (12001,12002,12003,12004):
            observed=runtime.client(uid,probe=True)
            check('peer_identity_'+str(uid),observed.get('uid')==uid and observed.get('gid')==uid)
        policy=initial_policy_profile()
        success(12001,request('propose','policy-propose',proposal_id='policy-proposal',series_id=policy['policy_id'],expected_generation=0,policy=policy))
        success(12003,request('validate','policy-validate',proposal_id='policy-proposal',validation_id='policy-validation'))
        success(12001,request('adopt','policy-adopt',proposal_id='policy-proposal',validation_id='policy-validation',expected_generation=0))
        prepare_request=request('guardrail_prepare','guardrail-prepare',run_id='llm-initial',policy_series_id=policy['policy_id'],target_version='baseline-v1')
        rejected=call(12002,prepare_request)
        check('candidate_cannot_prepare',rejected.get('kind')=='authority_error' and rejected.get('ci_eligible') is False)
        prepared_response=success(12001,prepare_request);prepared=prepared_response['prepared'];bound=prepared['bound_run']
        calibration=prepared_response['calibration']['measurement']
        check('independent_165_calibration',calibration['evaluator_calibration_passed'] is True and calibration['executed_vector_count']==165 and calibration['target_agreement_passed'] is None)
        check('full_400_inputs_600_stages',len(bound['case_set']['cases'])==400 and len(bound['plan']['entries'])==400 and sum(len(e['stage_ids']) for e in bound['plan']['entries'])==600)
        write(out/'prepared.json',prepared_response)
        for uid,action,extra in (
            (12001,'contract_propose',{'proposal_id':'llm-proposal','series_id':'llm-series','expected_generation':0,'contract':bound['contract']}),
            (12003,'contract_validate',{'proposal_id':'llm-proposal','validation_id':'llm-validation'}),
            (12001,'contract_adopt',{'proposal_id':'llm-proposal','validation_id':'llm-validation','expected_generation':0}),
            (12004,'run_begin',{'manifest':bound['manifest'],'plan':bound['plan'],'contract_series_id':'llm-series'}),
            (12004,'evidence_open',{'run_id':'llm-initial'})):
            success(uid,request(action,action+'-llm',**extra))
        owner={'run_id':'llm-initial','owner_id':'run_begin-llm','owner_epoch':1}
        cases={case['case_id']:case for case in bound['case_set']['cases']}
        for index,entry in enumerate(bound['plan']['entries']):
            suffix=str(index).zfill(4);operation_id='llm-op-'+suffix
            short={key:entry[key] for key in ('obligation_id','case_id','trial_id','variant')}
            started_operation=success(12004,request('resource_start','start-'+suffix,run_id='llm-initial',owner_id=owner['owner_id'],
                operation_id=operation_id,entry=short,scenario='guardrail:baseline-v1',
                expected_manifest_ref=content_ref('run_manifest','llm-initial',bound['manifest'])))
            owner['owner_epoch']=started_operation['owner_epoch']
            case_request=guardrail_results.for_entry(prepared,entry,operation_id,owner['owner_epoch'])
            active[operation_id]=case_request
            started=int(time.time())
            receipt=runner.run(case_request,run_deadline=bound['manifest']['deadline'])
            finished=int(time.time())
            write(out/'cases'/(suffix+'.json'),receipt)
            if not (receipt['execution_status']=='COMPLETED' and receipt['isolation_config_verified'] and receipt['stop_confirmed'] and receipt['cleanup_confirmed']):
                raise RuntimeError(receipt['reason'] or 'EXECUTION_FAILED')
            results=guardrail_results.validate_bundle(receipt['case_result'])
            timings=receipt['case_result']['worker_result']['stage_timings']
            # hostとworkerの壁時計は同一とは限らない。worker時刻は変更せず、
            # authorityが配送意図・停止観測との順序を検査する。
            write(out/'cases'/('host-timing-'+suffix+'.json'),{
                'clock_domain':'supervisor_host_utc','started_at':started,'finished_at':finished,
                'worker_clock_domain':'authority_runtime_utc'})
            if finished<started:raise RuntimeError('HOST_CLOCK_ROLLBACK')
            attempts=[]
            for stage_index,result in enumerate(results):
                attempt={'schema_version':1,'kind':'attempt_record','attempt_id':'llm-attempt-'+suffix+'-'+str(stage_index),
                    'variant':'candidate','retry_of':None,'started_at':timings[stage_index]['started_at'],
                    'finished_at':timings[stage_index]['finished_at'],'stop_confirmed':True,
                    'execution_status':'COMPLETED','state_restored':True,'expected_binding':result['binding'],'result':result}
                attempts.append(attempt)
                if result['binding']['stage_id']==cases[entry['case_id']]['scored_stage_id']:
                    positive=cases[entry['case_id']]['expected_label']=='positive';detect=result['detection']=='detect'
                    counts[('TP' if detect else 'FN') if positive else ('FP' if detect else 'TN')]+=1
            completed=success(12003,request('evidence_complete','complete-'+suffix,run_id='llm-initial',operation_id=operation_id,
                event_id='stop-'+suffix,usage=receipt['case_result']['worker_result']['usage'],attempts=attempts))
            if completed['accepted'] is not True:raise RuntimeError('COMPLETION_REJECTED')
            active.pop(operation_id);executed+=1;stages+=len(results)
            if executed<=2 or executed%20==0:print(json.dumps({'executed_cases':executed,'stages':stages,'authority_calls':call_count}),flush=True)
        check('finite_baseline_counts',counts=={'TP':196,'FN':4,'FP':4,'TN':196})
        closed=success(12004,request('resource_close','close-llm',**owner))
        check('resource_closed',closed['closed'] is True and closed['budget_closure'] is True and closed['resources']['slots']==0 and closed['resources']['unsettled']==0)
        finalized=success(12004,request('evidence_finalize','final-llm',run_id='llm-initial'))
        write(out/'finalized.json',finalized)
        check('initial_limited_healthy',finalized['assurance']=='HEALTHY' and finalized['input_materialization_verified'] is True and finalized['ci_eligible'] is False)
        proposal=success(12001,request('baseline_propose','baseline-propose',proposal_id='llm-baseline-proposal',series_id='llm-baseline',run_id='llm-initial',expected_generation=0))
        success(12003,request('baseline_validate','baseline-validate',proposal_id=proposal['proposal_id'],validation_id='llm-baseline-validation'))
        adopt_request=request('baseline_adopt','baseline-adopt',proposal_id=proposal['proposal_id'],validation_id='llm-baseline-validation',expected_generation=0)
        adopted=success(12001,adopt_request)
        current=success(12004,request('baseline_current','baseline-current',series_id='llm-baseline'))
        write(out/'baseline-current.json',current)
        check('baseline_adopted',current['valid'] is True)
        baseline=current['baseline']
        use_request=request('baseline_use','baseline-use',series_id='llm-baseline',expected_baseline_ref=content_ref('baseline',baseline['baseline_id'],baseline),expected_contract_ref=baseline['contract_ref'])
        check('fresh_baseline_use',success(12004,use_request)['use'] is True)
        runtime.restart_broker()
        check('restart_keeps_adoption',success(12001,adopt_request)==adopted)
        check('restart_fresh_use',success(12004,use_request)['use'] is True)
        check('source_unchanged',all(hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest for name,digest in sources.items()))
    except BaseException as error:
        failure={'type':type(error).__name__,'reason':str(error),'detail':getattr(error,'detail',None)}
        print(json.dumps({'failure':failure}),flush=True)
    finally:
        for operation_id,case_request in active.items():
            try:
                recovered=runner.recover(case_request['stages'][0]['binding']['run_id'],operation_id)
                write(out/('recovered-'+operation_id+'.json'),recovered)
                if recovered['stop_confirmed']:
                    success(12003,request('resource_observe','recover-stop-'+operation_id,run_id='llm-initial',operation_id=operation_id,
                        event_id='recovery-'+operation_id,stopped=True,usage=None))
            except BaseException as error:
                write(out/('recovery-error-'+operation_id+'.json'),{'type':type(error).__name__,'reason':str(error)})
        try:
            runtime.cleanup(remove_state=False)
            checks['owned_containers_removed']=True
        except BaseException as error:
            checks['owned_containers_removed']=False
            write(out/'cleanup-error.json',{'type':type(error).__name__,'reason':str(error)})
        summary={'passed':failure is None and all(checks.values()),'checks':checks,'failure':failure,
            'executed_cases':executed,'stages':stages,'counts':counts,'authority_calls':call_count,
            'authority_image_id':runtime.lock['image_id'],'guardrail_image_id':runner.lock['image_id'],
            'state_volume_retained':True,'trained_model':False,'ci_eligible':False,'full_mvp_accepted':False}
        write(out/'check.json',summary)
    return 0 if summary['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
