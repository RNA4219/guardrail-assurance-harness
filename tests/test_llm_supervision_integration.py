"""採択後の固定LLM比較を通常監督・CI・表示・中断回収まで通す。Dockerは起動しない。"""
from copy import deepcopy
from pathlib import Path
import sys
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from tests import test_llm_transitions as seed
from tests import test_llm_supervised_run as synthetic
from tests import test_supervised_run as supervisor_helpers
from gah import guardrail_results
from gah.run_contracts import content_ref
from tools.gah_run import execute
request=seed.request


def complete_side(store,candidate_id,side,*,now=1000):
    tag=candidate_id+'-'+side
    prepared=store.dispatch(12004,12004,request('contract_candidate_read',tag+'-read',candidate_id=candidate_id,side=side))['prepared']
    run_id=prepared['bound_run']['manifest']['run_id']
    store.dispatch(12004,12004,request('contract_candidate_begin',tag+'-begin',candidate_id=candidate_id,side=side))
    store.dispatch(12004,12004,request('evidence_open',tag+'-open',run_id=run_id))
    owner={'run_id':run_id,'owner_id':tag+'-begin','owner_epoch':1}
    from tests.llm_case_helpers import observe_entry
    for index, entry in enumerate(prepared['bound_run']['plan']['entries']):
        owner['owner_epoch'] = observe_entry(store, prepared, entry, operation_id=tag+'-op-'+str(index),
            owner_id=owner['owner_id'], now=now)
        if (index+1)%200==0:print(tag+'_saved_trials='+str(index+1),flush=True)
    store.dispatch(12004,12004,request('resource_close',tag+'-close',**owner))
    return store.dispatch(12004,12004,request('evidence_finalize',tag+'-final',run_id=run_id))


class LlmSupervisionIntegrationTests(unittest.TestCase):
    open=seed.LlmTransitionTests.open
    candidate=seed.LlmTransitionTests.candidate
    setUp=seed.LlmTransitionTests.setUp
    @classmethod
    def setUpClass(cls):seed.LlmTransitionTests.setUpClass.__func__(cls)

    def test_adopted_comparison_supervision_ci_report_and_checkpoint_recovery(self):
        with self.open() as store:
            store.dispatch(12003,12003,self.candidate())
            for side in ('old','new'):
                final=complete_side(store,'comparison',side)
                self.assertEqual(final['assurance'],'HEALTHY',side)
                self.assertFalse(final['ci_eligible'])
            valid=store.dispatch(12003,12003,request('contract_candidate_validate','comparison-validate',candidate_id='comparison',validation_id='comparison-valid'))
            self.assertTrue(valid['passed'])
            adopted=store.dispatch(12001,12001,request('contract_candidate_adopt','comparison-adopt',candidate_id='comparison',
                validation_id='comparison-valid',expected_contract_generation=1,expected_baseline_generation=1))
            self.assertTrue(adopted['adoption_verified']);self.assertEqual(adopted['generation'],2)
            runtime=supervisor_helpers.Runtime(store)
            contract_ref=content_ref('evaluation_contract',self.next['contract_id'],self.next)
            original=tuple(store._db.execute('SELECT run_id,digest FROM authority_run_receipts ORDER BY run_id'))
            folder=self.path.parent/'full';folder.mkdir();runner=synthetic.SyntheticGuardrailRunner(folder)
            normal={'schema_version':1,'run_id':'llm-normal','contract_series_id':'llm-series',
                'expected_contract_ref':contract_ref,'trigger':'scheduled_full'}
            progress=lambda stage: print('normal_completed_trials='+str(len(runner.executed)),flush=True) if stage.startswith('end-') and len(runner.executed)%200==0 else None
            result=execute(runtime,runner,folder,normal,'run',clock=lambda:1000,hook=progress)
            self.assertEqual(result['exit_code'],0,result);self.assertEqual(result['gate']['assurance'],'HEALTHY')
            self.assertEqual(len(runner.executed),800)
            from tools.gah_report import build_report,render_markdown
            ci_request=next(r for _,r in reversed(runtime.calls) if r['action']=='ci_check' and r['run_id']=='llm-normal')
            report=build_report(runtime,ci_request)
            self.assertEqual(report['scope']['use_cases'],['UC-LLM']);self.assertEqual(report['assurance'],'HEALTHY')
            self.assertTrue(report['ci_eligible']);self.assertTrue(report['metrics'])
            self.assertIn('HEALTHY',render_markdown(report));self.assertEqual(report,build_report(runtime,ci_request))
            self.assertEqual(store._db.execute("SELECT COUNT(*) FROM attempts WHERE run_id='llm-normal'").fetchone()[0],1200)
            for mode in ('resume','status','cancel'):
                self.assertEqual(execute(runtime,runner,folder,normal,mode,clock=lambda:1000)['exit_code'],0)
            self.assertEqual(len(runner.executed),800)
            self.assertEqual(original,tuple(store._db.execute("SELECT run_id,digest FROM authority_run_receipts WHERE run_id!='llm-normal' ORDER BY run_id")))
            # end保存後の全段階保存ACKを失っても、再配送だけで回収する。
            for index,stop in enumerate(('runner-returned-','start-','response-case-complete')):
                with self.subTest(checkpoint=stop):
                    folder=self.path.parent/('interrupt-'+str(index));folder.mkdir();runner=synthetic.SyntheticGuardrailRunner(folder)
                    req={**normal,'run_id':'llm-interrupted-'+str(index)}
                    def hook(stage):
                        match=(stage.startswith(stop) if stop!='response-case-complete' else stage.startswith('response-') and stage.endswith('-complete'))
                        if match:raise supervisor_helpers.Interrupted(stage)
                    with self.assertRaises(supervisor_helpers.Interrupted):execute(runtime,runner,folder,req,'run',clock=lambda:1000,hook=hook)
                    count=len(runner.executed)
                    cancelled=execute(runtime,runner,folder,req,'cancel',clock=lambda:1000)
                    self.assertEqual(cancelled['exit_code'],2 if stop=='start-' else 3,cancelled)
                    self.assertEqual(len(runner.executed),count)
                    if stop=='start-':
                        self.assertEqual(cancelled['unresolved_operations'][0]['reason'],'STOP_UNCONFIRMED')
            self.assertEqual({uid for uid,r in runtime.calls if r['action'] in {'evidence_record','evidence_complete'}},{12003})
            self.assertEqual({uid for uid,r in runtime.calls if r['action'] in {'resource_dispatch','resource_start'}},{12004})


if __name__=='__main__':unittest.main()
