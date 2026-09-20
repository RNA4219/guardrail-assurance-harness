"""初回baselineの完走・再配送・中断を実SQLiteと固定合成runnerで検査する。"""
from copy import deepcopy
from pathlib import Path
import unittest
from unittest.mock import patch
from tests import test_baseline_adoption_integration as baseline
request = baseline.request
from tests.test_supervised_run import Runtime, SyntheticRunner, Interrupted, TransportLost
from tests.test_llm_supervised_run import SyntheticGuardrailRunner
from tools.setup_baseline import execute_initial_baseline, _InitialGuardrail
from gah.supervisor_checkpoint import Checkpoint
from gah.supervised_run import SupervisorError


class SetupBaselineTests(unittest.TestCase):
    def setUp(self):
        self.fixture = baseline.BaselineAdoptionIntegrationTests('runTest'); self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.store = self.fixture.open(); self.addCleanup(self.store.close)
        self.fixture._policy_adopt(self.store)
        self.folder = self.fixture.path.parent / 'initial'; self.folder.mkdir()
        self.runtime = Runtime(self.store)
        self.runner = SyntheticRunner(self.folder, self.fixture.worker)

    def prepare(self, llm=False):
        action = 'guardrail_prepare' if llm else 'fixture_prepare'
        extra = {'target_version': 'baseline-v1'} if llm else {}
        prepared = self.store.dispatch(12001,12001,request(action,'prepare-initial',
            run_id='initial-sample',policy_series_id=self.fixture.policy['policy_id'],**extra))['prepared']
        for uid,action,fields in (
            (12001,'contract_propose',{'proposal_id':'proposal','series_id':'initial-contract',
                'expected_generation':0,'contract':prepared['bound_run']['contract']}),
            (12003,'contract_validate',{'proposal_id':'proposal','validation_id':'validation'}),
            (12001,'contract_adopt',{'proposal_id':'proposal','validation_id':'validation','expected_generation':0})):
            self.store.dispatch(uid,uid,request(action,action,**fields))
        return prepared

    def execute(self, prepared, hook=None):
        return execute_initial_baseline(self.runtime,self.runner,self.folder,prepared,
            request_prefix='setup-request',contract_series_id='initial-contract',clock=self.fixture.clock,hook=hook)

    def test_fifteen_entry_initial_baseline_and_replay_preserve_receipt(self):
        prepared=self.prepare(); result=self.execute(prepared)
        self.assertEqual(len(self.runner.executed),15)
        self.assertFalse(result['ci_eligible']); self.assertTrue(result['input_materialization_verified'])
        self.assertTrue(result['resource_closure_verified'])
        self.assertEqual(self.execute(prepared),result)
        self.assertEqual(len(self.runner.executed),15)
        self.assertEqual({uid for uid,r in self.runtime.calls if r['action']=='evidence_record'},{12003})
        self.assertEqual(self.store._db.execute('SELECT COUNT(*) FROM baseline_adoptions').fetchone()[0],0)

    def test_lost_finalize_response_recovers_without_rerunning_workers(self):
        prepared=self.prepare(); self.runtime.drop_action='evidence_finalize'
        with self.assertRaises(TransportLost): self.execute(prepared)
        self.assertEqual(len(self.runner.executed),15)
        result=self.execute(prepared)
        self.assertTrue(result['resource_closure_verified'])
        self.assertEqual(len(self.runner.executed),15)

    def test_interrupted_after_start_does_not_synthesize_a_completion(self):
        prepared=self.prepare()
        def stop(stage):
            if stage.startswith('start-'): raise Interrupted()
        with self.assertRaises(Interrupted): self.execute(prepared,stop)
        self.assertEqual(self.runner.executed,[])
        with self.assertRaises(Exception): self.execute(prepared)
        self.assertEqual(self.runner.executed,[])
        self.assertEqual(self.store._db.execute('SELECT COUNT(*) FROM authority_run_receipts').fetchone()[0],0)

    def test_complete_worker_without_host_end_time_is_not_accepted_as_success(self):
        prepared=self.prepare()
        def stop(stage):
            if stage.startswith('runner-returned-'): raise Interrupted()
        with self.assertRaises(Interrupted): self.execute(prepared,stop)
        self.assertEqual(len(self.runner.executed),1)
        with self.assertRaises(SupervisorError): self.execute(prepared)
        self.assertEqual(len(self.runner.executed),1)
        self.assertEqual(self.store._db.execute('SELECT COUNT(*) FROM authority_run_receipts').fetchone()[0],0)

    def test_recovery_receipt_from_other_binding_cannot_confirm_stop(self):
        prepared=self.prepare()
        original=self.runner.recover
        def changed(run_id, operation_id):
            receipt=deepcopy(original(run_id,operation_id))
            receipt["binding"]["run_id"]="other-run"
            return receipt
        self.runner.recover=changed
        def stop(stage):
            if stage.startswith("runner-returned-"): raise Interrupted()
        with self.assertRaises(Interrupted): self.execute(prepared,stop)
        self.assertEqual(len(self.runner.executed),1)
        self.assertFalse(any(r["action"]=="resource_observe" for _,r in self.runtime.calls))
        saved=Checkpoint(self.folder/"initial-checkpoints").get("initial-recovery-incomplete")
        self.assertEqual(saved,{"reason":"RECOVERY_UNCONFIRMED","ci_eligible":False})

    def test_changed_sample_is_rejected_before_run_begin(self):
        prepared=self.prepare(); changed=deepcopy(prepared)
        changed['pack']['materials'][0]['scenario']='constraint:C01:bad'
        with self.assertRaisesRegex(SupervisorError,'INITIAL_SAMPLE_MISMATCH'): self.execute(changed)
        self.assertEqual(self.runner.executed,[])
        self.assertEqual(self.store._db.execute('SELECT COUNT(*) FROM eval_runs').fetchone()[0],0)

    def test_llm_initial_requires_all_four_hundred_cases(self):
        prepared=self.prepare(llm=True); self.runner=SyntheticGuardrailRunner(self.folder)
        supervisor=_InitialGuardrail(self.runtime,self.runner,Checkpoint(self.folder/'prepare-checkpoints'),prepared,
            request_prefix='setup-request',contract_series_id='initial-contract',clock=self.fixture.clock)
        supervisor.prepare()
        self.assertEqual(len(supervisor.entries),400)
        self.assertEqual(sum(len(e['stage_ids']) for _,e,_ in supervisor.entries),600)
        self.assertTrue(all(e['variant']=='candidate' for _,e,_ in supervisor.entries))
        result=self.execute(prepared)
        self.assertEqual(len(self.runner.executed),400)
        self.assertTrue(result['input_materialization_verified']);self.assertFalse(result['ci_eligible'])
        self.assertEqual(self.store._db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0],600)


if __name__=='__main__': unittest.main()
