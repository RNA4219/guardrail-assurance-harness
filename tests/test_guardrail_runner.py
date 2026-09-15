"""固定入力、複数段階、改変結果、再配送journalの部品境界を確認する。"""
from copy import deepcopy
import hashlib
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from gah import guardrail_runtime,guardrail_results,llm_materialization
from gah.contracts import ContractError
from gah.policy import initial_policy_profile
from gah.docker_runner import PROFILE
from gah.guardrail_runner import GuardrailRunner
from gah.execution_journal import ExecutionJournal,JournalError
from gah.wire import canonical_bytes
spec=importlib.util.spec_from_file_location('guardrail_worker_test',ROOT/'fixtures/llm/guardrail_worker.py')
worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)


def example():
    lock=guardrail_runtime.read_lock()
    value=llm_materialization.build(initial_policy_profile(),policy_generation=1,run_id='guardrail-example',now=1000,
        target_version='baseline-v1',image_id=lock['image_id'],worker_digest=lock['worker_digest'],isolation_profile=PROFILE)
    entry=next(e for e in value['bound_run']['plan']['entries'] if len(e['stage_ids'])==2)
    return guardrail_results.for_entry(value,entry,'guardrail-op',1)


class GuardrailRunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.request=example()
        cls.result=worker.evaluate(cls.request,clock=lambda:1000_000_000_000)
        cls.bundle={'request':cls.request,'worker_result':cls.result}

    def test_two_stages_have_fixed_inputs_separate_bindings_and_observed_effects(self):
        normalized=guardrail_results.validate_bundle(self.bundle)
        self.assertEqual(len(normalized),2)
        self.assertEqual([n['binding']['stage_id'] for n in normalized],
            [s['binding']['stage_id'] for s in self.request['stages']])
        self.assertEqual(self.result['initial_counter'],0)
        self.assertEqual(worker.evaluate(self.request,clock=lambda:1000_000_000_000),self.result)
        self.assertNotIn('expected_label',self.request['stages'][0]['input'])
        self.assertTrue(all(n['mode']=='llm' for n in normalized))

    def test_unknown_input_target_stage_and_binding_are_rejected(self):
        mutations=[lambda x:x['target'].update(behavior_version=[]),lambda x:x['target'].update(runtime_image_id='sha256:'+'f'*64),
            lambda x:x['stages'][0]['input'].update(expected_label='negative'),lambda x:x['stages'].reverse(),
            lambda x:x['stages'][1]['binding'].update(operation_id='other-op'),lambda x:x['stages'].pop()]
        for mutate in mutations:
            value=deepcopy(self.request);mutate(value)
            with self.subTest(mutate=mutations.index(mutate)),self.assertRaises(ContractError):
                guardrail_results.validate_request(value)

    def test_state_residue_missing_results_and_fake_usage_never_become_success(self):
        mutations=[lambda x:x.update(initial_counter=1),lambda x:x['results'].pop(),
            lambda x:x['effects'][1].update(counter_before=999),lambda x:x['usage'].update(input_tokens=True),
            lambda x:x.update(trained_model=True),lambda x:x['results'][0]['binding'].update(stage_id='another-stage')]
        for mutate in mutations:
            value=deepcopy(self.bundle);mutate(value['worker_result'])
            with self.subTest(mutate=mutations.index(mutate)),self.assertRaises(ContractError):
                guardrail_results.validate_bundle(value)

    def test_stage_times_are_measured_separately_and_overlap_or_clock_rollback_is_rejected(self):
        ticks=iter([1000_000_000_000,1001_000_000_000,1002_000_000_000,1003_000_000_000])
        result=worker.evaluate(self.request,clock=lambda:next(ticks))
        self.assertEqual([(t['started_at'],t['finished_at']) for t in result['stage_timings']],[(1000,1001),(1002,1003)])
        guardrail_results.validate_bundle({'request':self.request,'worker_result':result})
        result['stage_timings'][1]['started_at']=1000
        with self.assertRaisesRegex(ContractError,'STAGE_TIME_INVALID'):
            guardrail_results.validate_bundle({'request':self.request,'worker_result':result})
        ticks=iter([1000_000_000_000,999_000_000_000])
        with self.assertRaisesRegex(ValueError,'STAGE_CLOCK_INVALID'):worker.evaluate(self.request,clock=lambda:next(ticks))

    def test_journal_replay_keeps_both_stages_and_rejects_changed_second_stage(self):
        binding=self.request['stages'][0]['binding'];scenario='guardrail:'+hashlib.sha256(canonical_bytes(self.request)).hexdigest()
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'journal.sqlite'
            with ExecutionJournal(path) as journal:
                record=journal.begin(binding,scenario,self.request['target']['runtime_image_id'],run_deadline=6400,timeout_seconds=120)
                for state in ('CREATED','STARTING','RUNNING','STOPPED'):
                    record=journal.advance(binding['run_id'],binding['operation_id'],record['owner_token'],state,container_id='a'*64)
                runner=object.__new__(GuardrailRunner)
                receipt=runner._receipt(record,status='COMPLETED',reason=None,stopped=True,cleaned=True,exit_code=0,
                    elapsed=100,verified=True,normalized=self.bundle)
                bad=deepcopy(receipt);bad['case_result']['worker_result']['effects'][1]['counter_before']=999
                with self.assertRaises((ContractError,JournalError)):
                    journal.finish(binding['run_id'],binding['operation_id'],record['owner_token'],bad)
                self.assertEqual(journal.finish(binding['run_id'],binding['operation_id'],record['owner_token'],receipt)['receipt'],receipt)
            with ExecutionJournal(path) as journal:
                self.assertEqual(journal.get(binding['run_id'],binding['operation_id'])['receipt'],receipt)
                changed=deepcopy(self.request);changed['stages'][1]['input']['required'].reverse()
                other='guardrail:'+hashlib.sha256(canonical_bytes(changed)).hexdigest()
                with self.assertRaises(JournalError):
                    journal.begin(binding,other,self.request['target']['runtime_image_id'],run_deadline=6400,timeout_seconds=120)


if __name__=='__main__':unittest.main()
