"""合成workerと実journalでLLM監督の段階保存・束縛・中断を検査する。"""
from copy import deepcopy
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from tests import test_llm_target_revision as revision
from tests.test_guardrail_runner import worker
from gah import guardrail_runtime,guardrail_results
from gah.execution_journal import ExecutionJournal
from gah.run_contracts import content_ref
from gah.supervisor_checkpoint import Checkpoint
from gah.llm_supervised_run import LlmSupervisor
from gah.supervised_run import SupervisorError
from gah.wire import canonical_bytes


class SyntheticGuardrailRunner:
    execution_kind='guardrail'
    def __init__(self,folder):
        self.lock=guardrail_runtime.read_lock()
        self.adapter_digest=self.lock['source_sha256']['src/gah/normalized.py']
        from gah.docker_runner import PROFILE
        self.isolation_digest=hashlib.sha256(canonical_bytes(PROFILE)).hexdigest()
        self.journal_path=folder/'execution.sqlite';self.executed=[];self.recovered=[]
    def run(self,request,*,run_deadline,timeout_seconds):
        request=guardrail_results.validate_request(request)
        binding=request['stages'][0]['binding'];self.executed.append(binding['operation_id'])
        scenario='guardrail:'+hashlib.sha256(canonical_bytes(request)).hexdigest()
        with ExecutionJournal(self.journal_path) as journal:
            row=journal.begin(binding,scenario,self.lock['image_id'],run_deadline=run_deadline,timeout_seconds=timeout_seconds)
            if not row['new']:raise AssertionError('DUPLICATE_EXECUTION')
            args=(binding['run_id'],binding['operation_id'],row['owner_token'])
            cid=hashlib.sha256(binding['operation_id'].encode()).hexdigest()
            journal.advance(*args,'CREATED',container_id=cid);journal.advance(*args,'STOPPED',container_id=cid)
            result=worker.evaluate(request,clock=lambda:1000_000_000_000)
            bundle={'request':request,'worker_result':result};guardrail_results.validate_bundle(bundle)
            receipt={'schema_version':1,'kind':'guardrail_execution','binding':binding,'scenario':scenario,
                'image_id':self.lock['image_id'],'container_id':cid,'execution_status':'COMPLETED',
                'exit_code':0,'stop_confirmed':True,'reason':None,'elapsed_millis':1,
                'isolation_config_verified':True,'output_disposition':'ADMITTED','cleanup_confirmed':True,
                'recovered':False,'normalized_result':None,'probe_result':None,'case_result':bundle,'ci_eligible':False}
            return journal.finish(*args,receipt)['receipt']
    def recover(self,run_id,operation_id):
        self.recovered.append(operation_id)
        with ExecutionJournal(self.journal_path) as journal:return journal.get(run_id,operation_id)['receipt']


class PrepareRuntime:
    prefix='gah-authority-'+'a'*32
    lock={'image_id':'sha256:'+'b'*64}
    def __init__(self,prepared):self.prepared=prepared;self.calls=[]
    def client(self,uid,request):
        self.calls.append((uid,deepcopy(request)))
        if request['action']=='run_prepare':return {**deepcopy(self.prepared),'ci_eligible':False}
        if request['action']=='evidence_complete':return {'action':request['action'],'request_id':request['request_id'],'ci_eligible':False,'accepted':True,'records':[{'accepted':True} for _ in request['attempts']]}
        return {'action':request['action'],'request_id':request['request_id'],'ci_eligible':False,'accepted':True,'conflict':False}


class LlmSupervisedRunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        helper=revision.LlmTargetRevisionTests('runTest');helper.setUpClass();cls.prepared=helper.build()['new']
        cls.prepared['bound_run']['manifest']['purpose']='regression'
        from gah.llm_transitions import rebind
        cls.prepared=rebind(cls.prepared,cls.prepared['bound_run'],cls.prepared['baseline_context'])
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.folder=Path(self.temp.name)
        self.runner=SyntheticGuardrailRunner(self.folder);self.runtime=PrepareRuntime(self.prepared)
        b=self.prepared['bound_run'];self.request={'schema_version':1,'run_id':b['manifest']['run_id'],
            'contract_series_id':'llm-series','expected_contract_ref':b['manifest']['contract_ref'],'trigger':'manual'}
        self.supervisor=LlmSupervisor(self.runtime,self.runner,Checkpoint(self.folder/'checkpoints'),self.request,clock=lambda:1000)
    def test_two_stages_have_separate_attempts_and_variant_target_binding(self):
        self.supervisor.prepare()
        op,entry,record=next(x for x in self.supervisor.entries if len(x[1]['stage_ids'])==2 and x[1]['variant']=='candidate')
        receipt=self.supervisor.execute_runner(op,entry,record,1)
        self.supervisor.checked_receipt(op,entry,record,receipt,1)
        self.supervisor.observe(op,receipt)
        self.supervisor.record_attempt(op,entry,receipt,{'started_at':1000,'finished_at':1000})
        rows=[(uid,r) for uid,r in self.runtime.calls if r['action']=='evidence_record']
        self.assertEqual(len(rows),2);self.assertEqual({uid for uid,_ in rows},{12003})
        self.assertEqual([r['attempt']['expected_binding']['stage_id'] for _,r in rows],entry['stage_ids'])
        self.assertEqual({r['attempt']['variant'] for _,r in rows},{'candidate'})
        self.assertEqual({r['attempt']['expected_binding']['target_digest'] for _,r in rows},{entry['target_ref']['digest']})
    def test_worker_times_are_preserved_when_host_clock_has_an_offset(self):
        self.supervisor.prepare();op,entry,record=self.supervisor.entries[0]
        receipt=self.supervisor.execute_runner(op,entry,record,1)
        self.supervisor.record_attempt(op,entry,receipt,{'started_at':1001,'finished_at':1002})
        rows=[r for _,r in self.runtime.calls if r['action']=='evidence_record']
        self.assertTrue(rows)
        self.assertTrue(all(r['attempt']['started_at']==1000 and r['attempt']['finished_at']==1000 for r in rows))
        timing=self.supervisor.completion_timing(1001,1002,receipt)
        self.assertEqual(timing['clock_domain'],'supervisor_host_utc')
        self.assertEqual(timing['worker_clock_domain'],'authority_runtime_utc')
        with self.assertRaisesRegex(SupervisorError,'CLOCK_FAILURE'):
            self.supervisor.record_attempt(op,entry,receipt,{'started_at':1002,'finished_at':1001})
    def test_receipt_from_other_operation_or_changed_result_is_rejected(self):
        self.supervisor.prepare();op,entry,record=self.supervisor.entries[0]
        receipt=self.supervisor.execute_runner(op,entry,record,1);changed=deepcopy(receipt);changed['binding']['operation_id']='different'
        with self.assertRaises(SupervisorError):self.supervisor.checked_receipt(op,entry,record,changed,1)
        with self.assertRaises(SupervisorError):self.supervisor.checked_receipt(op,entry,record,receipt,2)
    def test_new_operation_uses_atomic_start_and_separate_validator_records(self):
        from unittest.mock import patch
        self.supervisor.prepare();op,entry,record=next(x for x in self.supervisor.entries if len(x[1]['stage_ids'])==2)
        original=self.runtime.client
        def client(uid,request):
            if request['action']!='resource_start':return original(uid,request)
            self.runtime.calls.append((uid,deepcopy(request)))
            return {'action':request['action'],'request_id':request['request_id'],'ci_eligible':False,
                'owner_id':request['owner_id'],'owner_epoch':1,'manifest_ref':request['expected_manifest_ref'],
                'operation':{'operation_id':op,'owner_epoch':1,'entry':deepcopy(entry),'scenario':record['scenario'],
                    'conflicted':False,'released':False,'dispatch_intended':True}}
        with patch.object(self.runtime,'client',side_effect=client):
            self.supervisor.begin_operation(op,entry,record)
        actions=[(uid,r['action']) for uid,r in self.runtime.calls]
        self.assertEqual(actions.count((12004,'resource_start')),1)
        self.assertFalse(any(a in ('resource_claim','resource_reserve','resource_dispatch') for _,a in actions))
        self.assertEqual(actions.count((12003,'evidence_complete')),1)
        self.assertFalse(any(a in ('resource_observe','evidence_record') for _,a in actions))
        self.assertEqual(self.runner.executed,[op])

    def test_unacknowledged_atomic_start_enters_existing_recovery_path(self):
        from unittest.mock import patch
        from gah.supervised_run import Supervisor
        self.supervisor.prepare();op,entry,record=self.supervisor.entries[0]
        with patch.object(self.runtime,'client',side_effect=RuntimeError('ACK_LOST')):
            with self.assertRaisesRegex(RuntimeError,'ACK_LOST'):self.supervisor.begin_operation(op,entry,record)
        self.assertEqual(self.runner.executed,[])
        self.assertIsNotNone(self.supervisor.checkpoint.get('request-'+op+'-operation-start'))
        with patch.object(Supervisor,'begin_operation') as recovery:
            self.supervisor.begin_operation(op,entry,record)
            recovery.assert_called_once_with(op,entry,record)

    def test_denied_atomic_start_cannot_execute_runner(self):
        from unittest.mock import patch
        self.supervisor.prepare();op,entry,record=self.supervisor.entries[0]
        with patch.object(self.runtime,'client',return_value={'kind':'authority_error','reason':'START_DENIED','ci_eligible':False}):
            with self.assertRaisesRegex(SupervisorError,'START_DENIED'):self.supervisor.begin_operation(op,entry,record)
        self.assertEqual(self.runner.executed,[])

    def test_image_and_unknown_scope_do_not_start_runner(self):
        self.runner.lock['worker_digest']='f'*64
        with self.assertRaisesRegex(SupervisorError,'EXECUTION_PROFILE_MISMATCH'):self.supervisor.prepare()
        self.assertEqual(self.runner.executed,[])
        request={**self.request,'trigger':'change','changed_refs':[self.request['expected_contract_ref']]}
        other=LlmSupervisor(self.runtime,self.runner,Checkpoint(self.folder/'other'),request,clock=lambda:1000)
        with self.assertRaisesRegex(SupervisorError,'LLM_SCOPE_NOT_CONNECTED'):other.prepare()


if __name__=='__main__':unittest.main()
