"""対象版差の800試行を認証SQLiteへ保存し、否定結果・Finding・CI拒否へ接続する。"""
from copy import deepcopy
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from tests import test_llm_transitions as seed
from gah import llm_admission,llm_materialization,guardrail_runtime,guardrail_results
from gah.adoption import AdoptionError
from gah.docker_runner import PROFILE
from gah.run_contracts import content_ref
request=seed.request


class LlmRevisionIntegrationTests(unittest.TestCase):
    open=seed.LlmTransitionTests.open
    candidate=seed.LlmTransitionTests.candidate
    setUp=seed.LlmTransitionTests.setUp

    @classmethod
    def setUpClass(cls):
        seed.LlmTransitionTests.setUpClass.__func__(cls)

    def test_new_target_is_measured_against_baseline_and_degraded_result_is_not_adopted(self):
        with self.open() as store:
            initial=llm_admission.for_run(store._db,'llm-run',1000)['prepared']
            lock=guardrail_runtime.read_lock()
            context={'baseline_ref':content_ref('baseline',self.baseline['baseline_id'],self.baseline),
                'targets':[{'control_id':c['control_id'],'target_ref':c['target_ref']} for c in initial['bound_run']['registry']['controls']]}
            generated=llm_materialization.build(initial['bound_run']['policy'],policy_generation=1,run_id='delta-new',now=1000,
                target_version='degraded-v2',image_id=lock['image_id'],worker_digest=lock['worker_digest'],
                isolation_profile=PROFILE,generation=2,baseline_context=context)
            contract=generated['bound_run']['contract']
            store.dispatch(12001,12001,request('object_register','delta-registry',document=generated['bound_run']['registry']))
            store.dispatch(12001,12001,request('contract_propose','delta-propose',proposal_id='delta-proposal',series_id='llm-series',expected_generation=1,contract=contract))
            candidate=self.candidate('delta');candidate['proposal_id']='delta-proposal'
            prepared=store.dispatch(12003,12003,candidate)
            new=store.dispatch(12004,12004,request('contract_candidate_read','delta-read',candidate_id='delta',side='new'))['prepared']
            store.dispatch(12004,12004,request('contract_candidate_begin','delta-begin',candidate_id='delta',side='new'))
            store.dispatch(12004,12004,request('evidence_open','delta-open',run_id='delta-new'))
            owner={'run_id':'delta-new','owner_id':'delta-begin','owner_epoch':1}
            store.dispatch(12004,12004,request('resource_claim','delta-claim',run_id='delta-new',owner_id=owner['owner_id'],recovery=False))
            bound=new['bound_run']
            for index,entry in enumerate(reversed(bound['plan']['entries'])):
                op='delta-op-'+str(index);short={k:entry[k] for k in ('obligation_id','case_id','trial_id','variant')}
                target=guardrail_results.target_for_entry(new,entry)
                scenario='guardrail:'+target['behavior_version']
                if index==0:
                    with self.assertRaises(AdoptionError):
                        store.dispatch(12004,12004,request('resource_reserve','delta-wrong-target',**owner,operation_id=op,entry=short,scenario='guardrail:baseline-v1'))
                from tests.llm_case_helpers import observe_entry
                owner['owner_epoch'] = observe_entry(store, new, entry, operation_id=op,
                    owner_id=owner['owner_id'], now=1000)
                if (index+1)%100==0:print('delta_saved_trials='+str(index+1),flush=True)
            closure=store.dispatch(12004,12004,request('resource_close','delta-close',**owner))
            self.assertTrue(closure['budget_closure']);self.assertEqual(closure['resources']['unsettled'],0)
            final=store.dispatch(12004,12004,request('evidence_finalize','delta-final',run_id='delta-new'))
            self.assertEqual(final['assurance'],'DEGRADED');self.assertTrue(final['input_materialization_verified']);self.assertFalse(final['ci_eligible'])
            outputs=store.dispatch(12004,12004,request('candidate_outputs','delta-outputs',run_id='delta-new'))
            findings=store.dispatch(12004,12004,request('candidate_artifact','delta-findings',run_id='delta-new',artifact_ref=outputs['outputs']['findings']))['artifact']
            self.assertTrue(findings['items'])
            gate=store.dispatch(12004,12004,request('ci_check','delta-ci',run_id='delta-new',expected_manifest_ref=content_ref('run_manifest','delta-new',bound['manifest']),
                expected_contract_ref=bound['manifest']['contract_ref'],expected_baseline_ref=bound['manifest']['baseline_ref'],
                expected_target_refs=bound['manifest']['target_refs'],expected_use_cases=bound['manifest']['use_cases']))
            self.assertFalse(gate['ci_eligible']);self.assertIn('CI_PURPOSE_REQUIRED',gate['reasons'])
            with self.assertRaises(AdoptionError):
                store.dispatch(12003,12003,request('contract_candidate_validate','delta-validate',candidate_id='delta',validation_id='delta-validation'))
            self.assertEqual(store._db.execute("SELECT generation FROM eval_current WHERE series_id='llm-series'").fetchone()[0],1)
        with self.open() as store:
            again=store.dispatch(12004,12004,request('candidate_outputs','delta-outputs-again',run_id='delta-new'))
            self.assertEqual(again['outputs_ref'],outputs['outputs_ref']);self.assertFalse(again['ci_eligible'])


if __name__=='__main__':unittest.main()
