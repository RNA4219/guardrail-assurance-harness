"""実SQLiteと固定workerで、case全段階のvalidator保存・再配送・rollbackを確認する。"""
from copy import deepcopy
import unittest
from unittest.mock import patch
from tests import test_llm_admission as seed
from tests import test_guardrail_runner as runner_seed
from gah import guardrail_results,run_evidence
from gah.adoption import AdoptionError
from gah.run_contracts import content_ref
request=seed.request


class EvidenceCompleteTests(unittest.TestCase):
    open=seed.LlmAdmissionTests.open
    prepare=seed.LlmAdmissionTests.prepare
    adopt_policy=seed.LlmAdmissionTests.adopt_policy
    setup_run=seed.LlmAdmissionTests.setup_run
    def setUp(self):
        seed.LlmAdmissionTests.setUp(self)
        self.store=self.open();self.addCleanup(self.store.close)
        prepared=self.setup_run(self.store)['prepared'];bound=prepared['bound_run']
        self.entry=next(e for e in bound['plan']['entries'] if len(e['stage_ids'])==2)
        self.store.dispatch(12004,12004,request('resource_start','case-start',run_id='llm-run',owner_id='run_begin-llm',
            operation_id='case-operation',entry={k:self.entry[k] for k in ('obligation_id','case_id','trial_id','variant')},
            scenario='guardrail:baseline-v1',expected_manifest_ref=content_ref('run_manifest','llm-run',bound['manifest'])))
        req=guardrail_results.for_entry(prepared,self.entry,'case-operation',1)
        raw=runner_seed.worker.evaluate(req,clock=lambda:1000_000_000_000)
        results=guardrail_results.validate_bundle({'request':req,'worker_result':raw})
        attempts=[{'schema_version':1,'kind':'attempt_record','attempt_id':'case-attempt-'+str(i),
            'variant':self.entry['variant'],'retry_of':None,'started_at':1000,'finished_at':1000,'stop_confirmed':True,
            'execution_status':'COMPLETED','state_restored':True,'expected_binding':result['binding'],'result':result}
            for i,result in enumerate(results)]
        self.req=request('evidence_complete','case-complete',run_id='llm-run',operation_id='case-operation',
            event_id='case-stop',usage=raw['usage'],attempts=attempts)
    def state(self):
        return {table:[tuple(r) for r in self.store._db.execute('SELECT * FROM '+table+' ORDER BY 1')]
            for table in ('resource_operations','resource_events','attempts','authority_attempt_origins','run_state')}
    def test_validator_saves_all_stages_and_replay_is_immutable(self):
        for uid in (12001,12002,12004):
            with self.assertRaises(AdoptionError):self.store.dispatch(uid,uid,self.req)
        saved=self.store.dispatch(12003,12003,self.req)
        self.assertTrue(saved['accepted']);self.assertFalse(saved['ci_eligible']);self.assertEqual(len(saved['records']),2)
        state=self.state();self.assertEqual(len(state['attempts']),2);self.assertEqual(len(state['authority_attempt_origins']),2)
        self.assertEqual(self.store.dispatch(12003,12003,self.req),saved);self.assertEqual(self.state(),state)
        for i,attempt in enumerate(self.req['attempts']):
            replay=self.store.dispatch(12003,12003,request('evidence_record','legacy-record-'+str(i),run_id='llm-run',attempt=attempt))
            self.assertTrue(replay['accepted'])
        self.assertEqual(self.store._db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0],2)
    def test_second_stage_storage_failure_rolls_back_stop_usage_and_all_records(self):
        before=self.state();original=run_evidence.RunEvidenceBook.record_attempt;calls=[]
        def record(book,attempt):
            calls.append(attempt)
            if len(calls)==2:raise run_evidence.EvidenceError('INJECTED_STORAGE_FAILURE')
            return original(book,attempt)
        with patch.object(run_evidence.RunEvidenceBook,'record_attempt',new=record):
            with self.assertRaises(AdoptionError):self.store.dispatch(12003,12003,self.req)
        self.assertEqual(len(calls),2);self.assertEqual(self.state(),before)
        self.assertTrue(self.store.dispatch(12003,12003,self.req)['accepted'])
    def test_missing_reordered_mixed_or_unmeasured_stages_do_not_settle_operation(self):
        before=self.state()
        for kind in ('missing','order','mixed','unknown_usage','not_stopped','not_completed'):
            req=deepcopy(self.req);req['request_id']='invalid-'+kind
            if kind=='missing':req['attempts'].pop()
            elif kind=='order':req['attempts'].reverse()
            elif kind=='mixed':
                for binding in (req['attempts'][1]['expected_binding'],req['attempts'][1]['result']['binding']):binding['operation_id']='other-operation'
            elif kind=='unknown_usage':req['usage']=None
            elif kind=='not_stopped':req['attempts'][0]['stop_confirmed']=False
            else:req['attempts'][0]['execution_status']='UNKNOWN'
            with self.subTest(case=kind),self.assertRaises(AdoptionError):self.store.dispatch(12003,12003,req)
            self.assertEqual(self.state(),before)
    def test_future_worker_time_is_rejected_against_authority_observation_without_correction(self):
        before=self.state();future=deepcopy(self.req)
        for attempt in future['attempts']:
            attempt['started_at']=1001;attempt['finished_at']=1001
        with self.assertRaises(AdoptionError):self.store.dispatch(12003,12003,future)
        self.assertEqual(self.state(),before)
        self.assertTrue(all(attempt['started_at']==1001 for attempt in future['attempts']))

    def test_existing_attempt_conflict_is_not_erased_as_an_atomic_failure(self):
        self.store.dispatch(12003,12003,self.req)
        changed=deepcopy(self.req);changed['request_id']='changed-case'
        changed['attempts'][0]['result']['raw_digest']='f'*64
        result=self.store.dispatch(12003,12003,changed)
        self.assertFalse(result['accepted']);self.assertFalse(result['ci_eligible'])
        self.assertEqual(self.store._db.execute("SELECT state FROM run_state WHERE run_id='llm-run'").fetchone()[0],'HOLD')
        self.assertGreater(self.store._db.execute('SELECT COUNT(*) FROM attempt_events').fetchone()[0],0)


if __name__=='__main__':unittest.main()
