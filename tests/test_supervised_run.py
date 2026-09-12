"""製品監督の状態遷移を実SQLiteと固定合成runnerで検査する。Dockerは起動しない。"""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
spec=importlib.util.spec_from_file_location('supervisor_seed',ROOT/'tests/test_regression_integration.py')
seed=importlib.util.module_from_spec(spec);spec.loader.exec_module(seed)
from gah.adoption import AdoptionError
from gah.assurance_authority import fixed_profile
from gah.docker_runner import DockerRunner, operation_lock, RunnerError
from gah.execution_journal import ExecutionJournal
from gah.normalized import normalize_generic
from gah.supervised_run import Supervisor, SupervisorError, validate_input
from gah.supervisor_checkpoint import Checkpoint, CheckpointError
from gah.wire import canonical_bytes
from tools.gah_run import execute


class Interrupted(BaseException):
    pass


class TransportLost(RuntimeError):
    pass


class Runtime:
    prefix='gah-authority-'+'a'*32
    lock={'image_id':'sha256:'+'b'*64}
    def __init__(self,store):
        self.store=store;self.calls=[];self.drop_action=None
    def client(self,uid,req):
        self.calls.append((uid,deepcopy(req)))
        try:result=self.store.dispatch(uid,uid,req)
        except AdoptionError as error:
            return {'kind':'authority_error','reason':str(error),'ci_eligible':False}
        if req['action']==self.drop_action:
            self.drop_action=None
            raise TransportLost('SYNTHETIC_ACK_LOST')
        return result


class SyntheticRunner:
    target_digest=DockerRunner.target_digest
    def __init__(self,folder,worker):
        self.lock=json.loads((ROOT/'config/fixture-runtime.lock.json').read_text('utf-8'))
        profile=fixed_profile()
        self.adapter_digest=profile['adapter_digests'][0];self.isolation_digest=profile['isolation_digest']
        self.journal_path=folder/'execution.sqlite';self.worker=worker;self.executed=[];self.recovered=[]
    def run(self,scenario,binding,*,run_deadline,timeout_seconds):
        self.executed.append(binding['operation_id'])
        with ExecutionJournal(self.journal_path) as journal:
            row=journal.begin(binding,scenario,self.lock['image_id'],run_deadline=run_deadline,timeout_seconds=timeout_seconds)
            if not row['new']:raise AssertionError('DUPLICATE_EXECUTION')
            args=(binding['run_id'],binding['operation_id'],row['owner_token'])
            cid=hashlib.sha256(binding['operation_id'].encode()).hexdigest()
            journal.advance(*args,'CREATED',container_id=cid)
            journal.advance(*args,'STOPPED',container_id=cid)
            mode,code,state=scenario.split(':')
            observations=(self.worker._constraint_observation(code,state) if mode=='constraint'
                else self.worker._mutation_observation(code,state))
            raw=canonical_bytes({'schema_version':1,'kind':'gah_generic_result','binding':binding,
                'mode':mode,'observations':observations})
            normalized=normalize_generic(raw,binding,execution_status='COMPLETED',exit_code=0,stop_confirmed=True)
            receipt={'schema_version':1,'kind':'fixture_execution','binding':binding,'scenario':scenario,
                'image_id':self.lock['image_id'],'container_id':cid,'execution_status':'COMPLETED',
                'exit_code':0,'stop_confirmed':True,'reason':None,'elapsed_millis':1,
                'isolation_config_verified':True,'output_disposition':'ADMITTED','cleanup_confirmed':True,
                'recovered':False,'normalized_result':normalized,'probe_result':None,'ci_eligible':False}
            return journal.finish(*args,receipt)['receipt']
    def recover(self,run_id,operation_id):
        self.recovered.append(operation_id)
        with ExecutionJournal(self.journal_path) as journal:
            return journal.get(run_id,operation_id)['receipt']


class SupervisedRunTests(unittest.TestCase):
    open=seed.RegressionIntegrationTests.open
    @classmethod
    def setUpClass(cls):seed.RegressionIntegrationTests.setUpClass.__func__(cls)
    def setUp(self):
        seed.RegressionIntegrationTests.setUp(self)
        self.folder=self.path.parent/'supervised';self.folder.mkdir()
        self.runtime=Runtime(self.store)
        self.runner=SyntheticRunner(self.folder,self.worker)
        self.input={'schema_version':1,'run_id':'supervised-run','contract_series_id':'fixture-contract-series',
            'expected_contract_ref':deepcopy(self.contract_ref),'trigger':'manual'}
    def run_mode(self,mode='run',hook=None):
        return execute(self.runtime,self.runner,self.folder,self.input,mode,clock=self.clock,hook=hook)
    @staticmethod
    def interrupt_at(prefix):
        def hook(stage):
            if stage.startswith(prefix):raise Interrupted(stage)
        return hook
    def count(self):return len(self.runner.executed)

    def test_normal_run_resume_status_do_not_repeat_executions(self):
        result=self.run_mode()
        self.assertEqual(result['exit_code'],0,result)
        self.assertEqual(self.count(),30)
        prior=tuple(self.store._db.execute('SELECT * FROM authority_artifacts ORDER BY kind,id,digest'))
        for mode in ('resume','status','cancel'):
            self.assertEqual(self.run_mode(mode)['exit_code'],0)
        self.assertEqual(self.count(),30)
        self.assertEqual(tuple(self.store._db.execute('SELECT * FROM authority_artifacts ORDER BY kind,id,digest')),prior)
        self.assertEqual({uid for uid,req in self.runtime.calls if req['action']=='evidence_record'},{12003})
        self.assertEqual({uid for uid,req in self.runtime.calls if req['action']=='resource_dispatch'},{12004})

    def test_dispatch_ack_loss_resolves_without_duplicate_send(self):
        self.runtime.drop_action='resource_dispatch'
        with self.assertRaises(TransportLost):self.run_mode()
        self.assertEqual(self.count(),0)
        self.assertEqual(self.run_mode('resume')['exit_code'],0)
        self.assertEqual(self.count(),30)
        dispatches=[req['operation_id'] for uid,req in self.runtime.calls if req['action']=='resource_dispatch']
        self.assertEqual(len(dispatches),30)
        self.assertEqual(len(set(dispatches)),30)

    def test_runner_return_without_end_timestamp_cancels_without_rerun(self):
        with self.assertRaises(Interrupted):self.run_mode(hook=self.interrupt_at('runner-returned-'))
        self.assertEqual(self.count(),1)
        result=self.run_mode('resume')
        self.assertEqual(result['exit_code'],3,result)
        self.assertEqual(self.count(),1)
        self.assertFalse(result['ci_eligible'])
        self.assertEqual(self.run_mode('status')['exit_code'],3)

    def test_missing_journal_keeps_stop_unconfirmed(self):
        with self.assertRaises(Interrupted):self.run_mode(hook=self.interrupt_at('start-'))
        self.assertEqual(self.count(),0)
        result=self.run_mode('cancel')
        self.assertEqual(result['exit_code'],2,result)
        self.assertEqual(result['unresolved_operations'],[{'operation_id':self.runner.recovered[0],
            'reason':'STOP_UNCONFIRMED'}])
        status=self.run_mode('status')
        self.assertEqual(status['exit_code'],2)
        self.assertEqual(status['unresolved_operations'][0]['reason'],'STOP_UNCONFIRMED')
        self.assertFalse(status['unresolved_operations'][0]['stopped'])
        self.assertIsNone(self.store._db.execute("SELECT usage_json FROM resource_operations WHERE run_id='supervised-run'").fetchone()[0])
        self.assertEqual(self.count(),0)

    def test_revocation_after_observation_prevents_new_dispatch(self):
        with self.assertRaises(Interrupted):self.run_mode(hook=lambda s: (_ for _ in ()).throw(Interrupted(s))
            if s.startswith('response-') and s.endswith('-record') else None)
        self.store.dispatch(12004,12004,seed.request('evidence_revoke','supervised-revoke',run_id=self.source_id))
        result=self.run_mode('resume')
        self.assertEqual(result['exit_code'],3,result)
        self.assertEqual(self.count(),1)
        self.assertEqual(self.store._db.execute("SELECT count(*) FROM attempts WHERE run_id='supervised-run'").fetchone()[0],1)

    def test_checkpoint_identity_and_role_input_cannot_be_replaced(self):
        with self.assertRaises(Interrupted):self.run_mode(hook=self.interrupt_at('response-prepare'))
        with self.assertRaises(ValueError):validate_input({**self.input,'role':'validator'})
        self.input['expected_contract_ref']['digest']='d'*64
        with self.assertRaises(CheckpointError):self.run_mode('resume')
        self.assertEqual(self.count(),0)

    def test_run_lock_rejects_second_supervisor(self):
        with operation_lock(self.folder/'execution.sqlite',self.input['run_id'],'supervisor'):
            with self.assertRaisesRegex(RunnerError,'^OWNER_ACTIVE$'):self.run_mode()
        self.assertEqual(self.count(),0)
        self.assertEqual(self.runtime.calls,[])

    def test_unknown_trigger_is_not_treated_as_full_run(self):
        self.input['trigger']='shell'
        with self.assertRaises(SupervisorError):self.run_mode()
        self.assertEqual(self.runtime.calls,[])
        self.assertEqual(self.count(),0)


if __name__=='__main__':unittest.main(verbosity=2)
