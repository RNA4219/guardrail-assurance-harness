"""400ケース初回Evidenceを元に、分割候補の認証・保存・取得を統合検査する。"""
from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from tests import test_llm_admission as admission_tests
from tests import test_guardrail_runner as runner_tests
from gah import guardrail_results,resources
from gah.adoption import AdoptionError,AdoptionStore
from gah.evaluation_authority import EvaluationExtension
from gah.contracts import MAX_DOCUMENT_BYTES
from gah.policy import initial_policy_profile
from gah.run_contracts import content_ref
from gah.wire import canonical_bytes
request=admission_tests.request


class LlmTransitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder=tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.folder.cleanup)
        cls.seed=Path(cls.folder.name)/'seed.sqlite'
        helper=admission_tests.LlmAdmissionTests('test_initial_adoption_records_calibration_and_two_stage_operation_without_claiming_complete')
        helper.setUp();cls.addClassCleanup(helper.doCleanups)
        with helper.open() as store:
            prepared=helper.setup_run(store)['prepared'];bound=prepared['bound_run']
            owner={'run_id':'llm-run','owner_id':'run_begin-llm','owner_epoch':1}
            from tests.llm_case_helpers import observe_entry
            for index, entry in enumerate(bound['plan']['entries']):
                owner['owner_epoch'] = observe_entry(store, prepared, entry, operation_id='seed-op-'+str(index),
                    owner_id=owner['owner_id'], now=1000)
                if (index+1)%100==0:print(json.dumps({'sqlite_seed_cases':index+1}),flush=True)
            store.dispatch(12004,12004,request('resource_close','seed-close',**owner))
            receipt=store.dispatch(12004,12004,request('evidence_finalize','seed-final',run_id='llm-run'))
            if receipt['assurance']!='HEALTHY' or receipt['input_materialization_verified'] is not True:raise AssertionError('INITIAL_SEED_NOT_HEALTHY')
            for uid,action,fields in (
                (12001,'baseline_propose',{'proposal_id':'seed-baseline-proposal','series_id':'llm-baseline','run_id':'llm-run','expected_generation':0}),
                (12003,'baseline_validate',{'proposal_id':'seed-baseline-proposal','validation_id':'seed-baseline-validation'}),
                (12001,'baseline_adopt',{'proposal_id':'seed-baseline-proposal','validation_id':'seed-baseline-validation','expected_generation':0})):
                store.dispatch(uid,uid,request(action,'seed-'+action,**fields))
            current=store.dispatch(12004,12004,request('baseline_current','seed-current',series_id='llm-baseline'))
            if current['valid'] is not True:raise AssertionError('INITIAL_BASELINE_INVALID')
            cls.baseline=current['baseline'];cls.previous=bound['contract']
            cls.next=deepcopy(cls.previous)
            cls.next.update(contract_id='llm-comparison-v2',generation=2,comparison={'mode':'required',
                'baseline_ref':content_ref('baseline',cls.baseline['baseline_id'],cls.baseline),'changed_axes':[],'reason':None})
            store.dispatch(12001,12001,request('contract_propose','comparison-propose',proposal_id='comparison-proposal',series_id='llm-series',expected_generation=1,contract=cls.next))
            with closing(sqlite3.connect(cls.seed)) as destination:store._db.backup(destination)

    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        self.path=Path(self.directory.name)/'test.sqlite';shutil.copyfile(self.seed,self.path)

    def open(self):
        return AdoptionStore(self.path,clock=lambda:1000,bootstrap_policy=initial_policy_profile(),validator_digest='b'*64,extension=EvaluationExtension())

    def candidate(self,identifier='comparison'):
        return request('contract_candidate_prepare','prepare-'+identifier,candidate_id=identifier,proposal_id='comparison-proposal',
            baseline_series_id='llm-baseline',expected_contract_ref=content_ref('evaluation_contract',self.previous['contract_id'],self.previous),
            expected_baseline_ref=content_ref('baseline',self.baseline['baseline_id'],self.baseline),old_run_id=identifier+'-old',new_run_id=identifier+'-new')

    def test_split_sections_keep_complete_input_and_fresh_read_does_not_start_runs(self):
        with self.open() as store:
            prepared=store.dispatch(12003,12003,self.candidate())
            self.assertLess(len(canonical_bytes(prepared)),MAX_DOCUMENT_BYTES)
            self.assertEqual(prepared['runs']['kind'],'candidate_run_sections')
            self.assertEqual(store._db.execute("SELECT COUNT(*) FROM authority_artifacts WHERE kind='candidate_section'").fetchone()[0],3)
            for side,count in (('old',400),('new',800)):
                req=request('contract_candidate_read','read-'+side,candidate_id='comparison',side=side)
                response=store.dispatch(12004,12004,req)
                self.assertLess(len(canonical_bytes(response)),MAX_DOCUMENT_BYTES)
                self.assertEqual(len(response['prepared']['bound_run']['plan']['entries']),count)
                self.assertFalse(response['ci_eligible']);self.assertEqual(response['candidate_ref'],prepared['candidate_ref'])
                self.assertIsNone(store._db.execute('SELECT 1 FROM eval_runs WHERE run_id=?',('comparison-'+side,)).fetchone())
                with self.assertRaises(AdoptionError):store.dispatch(12002,12002,req)
            begin=request('contract_candidate_begin','begin-new',candidate_id='comparison',side='new')
            store.dispatch(12004,12004,begin)
            opened=store.dispatch(12004,12004,request('evidence_open','open-new',run_id='comparison-new'))
            self.assertEqual(opened['run_id'],'comparison-new')
        with self.open() as store:
            response=store.dispatch(12004,12004,request('contract_candidate_read','read-new-again',candidate_id='comparison',side='new'))
            self.assertEqual(response['candidate_ref'],prepared['candidate_ref'])

    def test_section_insertion_failure_rolls_back_candidate_and_run_reservations(self):
        with self.open() as store:
            store._db.execute("CREATE TRIGGER reject_section BEFORE INSERT ON authority_artifacts WHEN NEW.kind='candidate_section' BEGIN SELECT RAISE(ABORT,'fixed'); END")
            with self.assertRaises(AdoptionError):store.dispatch(12003,12003,self.candidate('failure'))
            self.assertEqual(store._db.execute('SELECT COUNT(*) FROM transition_candidates').fetchone()[0],0)
            self.assertEqual(store._db.execute('SELECT COUNT(*) FROM transition_runs').fetchone()[0],0)
            self.assertEqual(store._db.execute("SELECT COUNT(*) FROM authority_artifacts WHERE kind='candidate_section'").fetchone()[0],0)
            store._db.execute('DROP TRIGGER reject_section')
            self.assertEqual(store.dispatch(12003,12003,self.candidate('failure'))['candidate_id'],'failure')

    def test_referenced_section_substitution_is_rejected_after_rehash(self):
        with self.open() as store:
            store.dispatch(12003,12003,self.candidate())
            row=store._db.execute("SELECT * FROM transition_candidates WHERE candidate_id='comparison'").fetchone()
            value=resources._unpack(row['payload_json'],row['digest'])
            value['runs']['new_ref']=value['runs']['old_ref']
            raw,digest=resources._packed(value)
            store._db.execute("UPDATE transition_candidates SET payload_json=?,digest=? WHERE candidate_id='comparison'",(raw,digest))
            with self.assertRaises(AdoptionError):
                store.dispatch(12004,12004,request('contract_candidate_read','read-forged',candidate_id='comparison',side='new'))


if __name__=='__main__':unittest.main()
