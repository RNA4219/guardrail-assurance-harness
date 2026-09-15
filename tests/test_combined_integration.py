"""同DBの両用途を複合監督へ通す製品統合試験。実行器は固定合成worker。"""
from contextlib import closing
from copy import deepcopy
from pathlib import Path
import shutil
import sqlite3
import sys
import unittest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from tests import test_llm_transitions as seed
from tests import test_llm_supervision_integration as llm
from tests import test_llm_supervised_run as llm_runner
from tests import test_supervised_run as ci_runner
from tests.combined_helpers import add_ci_contract
from gah.adoption import AdoptionError
from gah import combined_runs
from gah.run_contracts import content_ref
from tools.gah_combined import execute,render_markdown
request=seed.request


class CombinedIntegrationTests(unittest.TestCase):
    open=seed.LlmTransitionTests.open
    candidate=seed.LlmTransitionTests.candidate
    @classmethod
    def setUpClass(cls):
        seed.LlmTransitionTests.setUpClass.__func__(cls)
        helper=cls('runTest');seed.LlmTransitionTests.setUp(helper);cls.addClassCleanup(helper.doCleanups)
        with helper.open() as store:
            store.dispatch(12003,12003,helper.candidate())
            for side in ('old','new'):
                if llm.complete_side(store,'comparison',side)['assurance']!='HEALTHY':raise AssertionError('LLM_NOT_HEALTHY')
            store.dispatch(12003,12003,request('contract_candidate_validate','combined-llm-validate',candidate_id='comparison',validation_id='combined-llm-valid'))
            store.dispatch(12001,12001,request('contract_candidate_adopt','combined-llm-adopt',candidate_id='comparison',validation_id='combined-llm-valid',expected_contract_generation=1,expected_baseline_generation=1))
            cls.ci_ref,cls.worker=add_ci_contract(store)
            cls.llm_ref=content_ref('evaluation_contract',cls.next['contract_id'],cls.next)
            saved=Path(cls.folder.name)/'combined-seed.sqlite'
            with closing(sqlite3.connect(saved)) as destination:store._db.backup(destination)
            cls.seed=saved
    def setUp(self):
        seed.LlmTransitionTests.setUp(self);self.store=self.open();self.addCleanup(self.store.close)
        self.group=self.path.parent/'group';self.group.mkdir()
        for use in combined_runs.USES:(self.group/use).mkdir()
        self.runners={'UC-CI':ci_runner.SyntheticRunner(self.group/'UC-CI',self.worker),
            'UC-LLM':llm_runner.SyntheticGuardrailRunner(self.group/'UC-LLM')}
        self.runtime=ci_runner.Runtime(self.store)
        self.request=request('combined_prepare','both-prepare',run_id='both',children=[
            {'use_case':'UC-CI','run_id':'both-ci','contract_series_id':'fixture-contract-series','expected_contract_ref':self.ci_ref},
            {'use_case':'UC-LLM','run_id':'both-llm','contract_series_id':'llm-series','expected_contract_ref':self.llm_ref}])
    def query(self,action,ref,suffix='current'):
        return self.store.dispatch(12004,12004,request(action,'both-'+suffix,run_id='both',expected_manifest_ref=ref))
    def test_both_uses_complete_with_one_manifest_reports_and_fresh_revocation(self):
        def progress(stage):
            n=len(self.runners['UC-LLM'].executed)
            if stage.startswith('end-') and n and n%200==0:print('combined_llm_trials='+str(n),flush=True)
        result=execute(self.runtime,self.runners,self.group,self.request,'run',clock=lambda:1000,hook=progress)
        self.assertEqual(result['exit_code'],0,result.get('reasons'));self.assertTrue(result['ci_eligible'])
        self.assertEqual(result['manifest']['planned_trials'],830)
        self.assertEqual([c['gate']['assurance'] for c in result['children']],['HEALTHY','HEALTHY'])
        self.assertEqual([len(self.runners[u].executed) for u in combined_runs.USES],[30,800])
        self.assertEqual([r['report']['scope']['use_cases'] for r in result['reports']],[['UC-CI'],['UC-LLM']])
        measured=result['reports'][1]['report']['measurements']['counts']['variant']
        for variant in ('candidate','baseline'):
            self.assertEqual([measured[variant][k] for k in ('tp','fp','tn','fn')],[196,4,196,4])
            self.assertEqual([measured[variant][k] for k in ('complete','planned')],[600,600])
        text=render_markdown(result);self.assertIn('UC-CI',text);self.assertIn('UC-LLM',text)
        replay=execute(self.runtime,self.runners,self.group,self.request,'resume',clock=lambda:1000)
        self.assertEqual(replay['receipt'],result['receipt']);self.assertEqual(len(self.runners['UC-LLM'].executed),800)
        self.store.dispatch(12004,12004,request('evidence_revoke','both-ci-revoke',run_id='both-ci'))
        current=self.query('combined_current',result['manifest_ref'],'after-revoke')
        self.assertFalse(current['ci_eligible']);self.assertEqual(current['receipt'],result['receipt'])
        self.assertTrue(current['children'][1]['gate']['ci_eligible']);self.assertFalse(current['children'][0]['gate']['ci_eligible'])
        receipt=self.store._db.execute('SELECT payload_json FROM authority_run_receipts WHERE run_id=?',('both-ci',)).fetchone()
        import json
        evidence_ref=json.loads(receipt[0])['evidence_ref']
        fields={'run_id':'both-ci','expected_evidence_ref':evidence_ref}
        retention=self.store.dispatch(12004,12004,request('evidence_retention_state','both-retention-state',**fields))
        plan=self.store.dispatch(12001,12001,request('evidence_retention_plan','both-retention-plan',**fields,
            expected_hold_ref=retention['hold_ref'],reason='EXPLICIT_REMOVAL'))
        self.store.dispatch(12004,12004,request('evidence_retention_apply','both-retention-apply',**fields,plan_ref=plan['plan_ref']))
        deleted=self.query('combined_current',result['manifest_ref'],'after-delete')
        self.assertFalse(deleted['ci_eligible']);self.assertEqual(deleted['receipt'],result['receipt'])
        self.assertTrue(deleted['children'][1]['gate']['ci_eligible']);self.assertFalse(deleted['children'][0]['gate']['ci_eligible'])
        self.assertIsNone(self.store._db.execute("SELECT 1 FROM authority_artifacts WHERE kind='evidence' AND id='both-ci'").fetchone())
    def test_missing_second_use_is_not_success_and_cancel_keeps_completed_first_use(self):
        def hook(stage):
            if len(self.runners['UC-CI'].executed)==30 and not self.runners['UC-LLM'].executed and stage=='response-prepare':
                raise ci_runner.Interrupted(stage)
        with self.assertRaises(ci_runner.Interrupted):execute(self.runtime,self.runners,self.group,self.request,'run',clock=lambda:1000,hook=hook)
        prepared=self.store.dispatch(12004,12004,self.request)
        current=self.query('combined_current',prepared['manifest_ref'])
        self.assertFalse(current['ci_eligible']);self.assertIsNone(current['receipt'])
        self.assertTrue(current['children'][0]['gate']['ci_eligible'])
        cancelled=execute(self.runtime,self.runners,self.group,self.request,'cancel',clock=lambda:1000)
        self.assertEqual(cancelled['exit_code'],3);self.assertFalse(cancelled['ci_eligible'])
        self.assertEqual([len(self.runners[u].executed) for u in combined_runs.USES],[30,0])
    def test_roles_ids_original_contract_series_and_origin_are_checked(self):
        for uid in (12001,12002,12003):
            with self.assertRaisesRegex(AdoptionError,'AUTHORITY_DENIED'):self.store.dispatch(uid,uid,self.request)
        prepared=self.store.dispatch(12004,12004,self.request)
        bad=deepcopy(self.request);bad['request_id']='other-prepare';bad['run_id']='other'
        with self.assertRaisesRegex(AdoptionError,'RUN_CONFLICT'):self.store.dispatch(12004,12004,bad)
        child=self.store.dispatch(12004,12004,request('combined_child_read','both-read-ci',run_id='both',expected_manifest_ref=prepared['manifest_ref'],use_case='UC-CI'))['prepared']
        with self.assertRaisesRegex(AdoptionError,'COMBINED_BINDING_INVALID'):
            self.store.dispatch(12004,12004,request('run_begin','both-wrong-series',manifest=child['bound_run']['manifest'],plan=child['bound_run']['plan'],contract_series_id='llm-series'))
        self.assertIsNone(self.store._db.execute("SELECT 1 FROM eval_runs WHERE run_id='both-ci'").fetchone())
        self.store._db.execute("UPDATE idempotency SET actor_id='manager' WHERE request_id='both-prepare'")
        with self.assertRaisesRegex(AdoptionError,'COMBINED_ORIGIN_INVALID'):self.query('combined_current',prepared['manifest_ref'])
    def test_prepare_storage_failure_rolls_back_parent_and_reserved_children(self):
        self.store._db.execute("CREATE TRIGGER reject_combined BEFORE INSERT ON authority_artifacts WHEN NEW.kind='combined_run_binding' BEGIN SELECT RAISE(ABORT,'synthetic'); END")
        with self.assertRaises(AdoptionError):self.store.dispatch(12004,12004,self.request)
        self.assertIsNone(self.store._db.execute("SELECT 1 FROM idempotency WHERE request_id='both-prepare'").fetchone())
        self.assertIsNone(self.store._db.execute("SELECT 1 FROM authority_artifacts WHERE kind='combined_run_binding'").fetchone())
        self.store._db.execute('DROP TRIGGER reject_combined')
        self.assertFalse(self.store.dispatch(12004,12004,self.request)['ci_eligible'])


if __name__=='__main__':unittest.main()
