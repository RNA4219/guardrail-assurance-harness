"""自作の固定LLMケースを製品の開始・全段階保存APIへ通す試験helper。"""
from tests import test_guardrail_runner as runner_tests
from gah import guardrail_results
from gah.run_contracts import content_ref


def observe_entry(store, prepared, entry, *, operation_id, owner_id, now):
    """ケース数を減らさず、operator開始とvalidator保存を別主体で実行する。"""
    manifest = prepared['bound_run']['manifest']
    run_id = manifest['run_id']
    def request(action, suffix, **fields):
        return {'schema_version':1, 'action':action, 'request_id':operation_id+'-'+suffix, **fields}
    target = guardrail_results.target_for_entry(prepared, entry)
    begun = store.dispatch(12004, 12004, request('resource_start', 'start',
        run_id=run_id, owner_id=owner_id, operation_id=operation_id,
        entry={key:entry[key] for key in ('obligation_id','case_id','trial_id','variant')},
        scenario='guardrail:'+target['behavior_version'],
        expected_manifest_ref=content_ref('run_manifest', run_id, manifest)))
    epoch = begun['owner_epoch']
    fixed_request = guardrail_results.for_entry(prepared, entry, operation_id, epoch)
    raw = runner_tests.worker.evaluate(fixed_request, clock=lambda:now*1_000_000_000)
    results = guardrail_results.validate_bundle({'request':fixed_request, 'worker_result':raw})
    attempts = []
    for index, result in enumerate(results):
        timing = raw['stage_timings'][index]
        attempts.append({'schema_version':1, 'kind':'attempt_record',
            'attempt_id':operation_id+'-attempt-'+str(index), 'variant':entry['variant'], 'retry_of':None,
            'started_at':timing['started_at'], 'finished_at':timing['finished_at'], 'stop_confirmed':True,
            'execution_status':'COMPLETED', 'state_restored':True,
            'expected_binding':result['binding'], 'result':result})
    completed = store.dispatch(12003, 12003, request('evidence_complete', 'complete',
        run_id=run_id, operation_id=operation_id, event_id=operation_id+'-stop',
        usage=raw['usage'], attempts=attempts))
    if completed['accepted'] is not True:
        raise AssertionError('CASE_NOT_ACCEPTED: '+str(completed))
    return epoch
