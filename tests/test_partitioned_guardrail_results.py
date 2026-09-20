"""固定query-scaleの生成→worker→結果検査→journal再読込を確認する。"""
from copy import deepcopy
import hashlib
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from gah import guardrail_results, guardrail_runtime, partitioned_guardrail_results as results
from gah import partitioned_llm_materialization as materialization
from gah.contracts import ContractError
from gah.docker_runner import PROFILE
from gah.execution_journal import ExecutionJournal, JournalError
from gah.partitioned_guardrail_runner import PartitionedGuardrailRunner
from gah.partitioned_trial_plan import restore_trial_plan
from gah.policy import initial_policy_profile
from gah.wire import canonical_bytes

spec = importlib.util.spec_from_file_location('query_scale_worker_test', ROOT/'fixtures/llm/guardrail_worker.py')
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


def prepare(count):
    lock = guardrail_runtime.read_lock()
    return materialization.build(initial_policy_profile(), case_count=count, policy_generation=1,
        run_id='scale-worker-'+str(count), now=1000, target_version='baseline-v1',
        image_id=lock['image_id'], worker_digest=lock['worker_digest'], isolation_profile=PROFILE)


class PartitionedGuardrailResultsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.prepared = {count: prepare(count) for count in (400, 800, 1600)}
        cls.cases = {count: results.PreparedCases(value) for count, value in cls.prepared.items()}
        cls.entries = {count: restore_trial_plan(value['plan_index'], value['plan_segments'])['entries']
                       for count, value in cls.prepared.items()}

    def request(self, count=400, index=0):
        return self.cases[count].for_entry(self.entries[count][index], 'scale-op-'+str(index), 1)

    def test_all_cases_reach_worker_and_preserve_identity_and_zero_usage(self):
        for count in (400, 800, 1600):
            seen = set()
            with self.subTest(count=count):
                for ordinal, entry in enumerate(self.entries[count]):
                    request = self.cases[count].for_entry(entry, 'scale-all-'+str(ordinal), 1)
                    output = worker.evaluate(request, clock=lambda:1000_000_000_000)
                    bundle = results.from_worker(canonical_bytes(output), request, case_count=count)
                    normalized = bundle['worker_result']['results'][0]
                    self.assertEqual(normalized['binding'], request['stages'][0]['binding'])
                    self.assertEqual(bundle['worker_result']['initial_counter'], 0)
                    self.assertEqual(bundle['worker_result']['usage'], {'input_tokens':0, 'output_tokens':0, 'cost_usd':'0'})
                    seen.add(normalized['binding']['case_id'])
                self.assertEqual(len(seen), count)

    def test_family_count_stage_input_and_evaluator_changes_fail(self):
        request = self.request()
        for count in (True, 200, 800, 1600):
            with self.subTest(count=count), self.assertRaises(ContractError):
                results.validate_request(request, case_count=count)
        mutations = (
            lambda v:v['stages'][0]['binding'].update(case_id='qg00000'),
            lambda v:v['stages'][0]['binding'].update(evaluator_digest='f'*64),
            lambda v:v['stages'][0]['binding'].update(stage_id='unknown-stage'),
            lambda v:v['stages'][0]['input'].update(expected_label='negative'),
            lambda v:v['target'].update(behavior_version='unknown'),
            lambda v:v['stages'].append(deepcopy(v['stages'][0])),
        )
        for ordinal, change in enumerate(mutations):
            value=deepcopy(request); change(value)
            with self.subTest(mutation=ordinal), self.assertRaises(ContractError):
                results.validate_request(value, case_count=400)

    def test_returned_requests_and_caller_artifacts_do_not_mutate_owned_inputs(self):
        prepared=deepcopy(self.prepared[400])
        session=results.PreparedCases(prepared)
        entry=deepcopy(self.entries[400][0])
        first=session.for_entry(entry,'copy-op',1)
        prepared['manifest']['run_id']='other-run'
        prepared['document_segments'][0]['documents'].clear()
        first['target']['behavior_version']='unknown'
        first['stages'][0]['input']['required'].clear()
        second=session.for_entry(entry,'copy-op',1)
        self.assertEqual(second,self.cases[400].for_entry(entry,'copy-op',1))
        entry['target_ref']['digest']='f'*64
        with self.assertRaisesRegex(ContractError,'ENTRY_NOT_PLANNED'):
            session.for_entry(entry,'copy-op',1)

    def test_unplanned_entry_and_changed_corpus_or_profile_are_rejected(self):
        entry=deepcopy(self.entries[400][0]);entry['case_id']='unknown-case'
        with self.assertRaisesRegex(ContractError,'ENTRY_NOT_PLANNED'):
            self.cases[400].for_entry(entry,'op',1)
        changes=(lambda v:v['corpus_index'].update(reconstructed_digest='f'*64),
                 lambda v:v['execution_profile']['bindings'][0].update(fixture_digest='f'*64),
                 lambda v:v['evaluator_document'].update(evaluator_id='other-evaluator'))
        for change in changes:
            prepared=deepcopy(self.prepared[400]);change(prepared)
            with self.assertRaises(ContractError):results.PreparedCases(prepared)

    def test_request_hot_path_does_not_rebuild_or_restore_entire_corpus(self):
        with patch.object(results,'build_scale_corpus',side_effect=AssertionError('rebuilt corpus')), \
             patch.object(results,'restore_scale_corpus',side_effect=AssertionError('restored corpus')):
            for ordinal in (0,399):
                self.request(400,ordinal)

    def test_legacy_validator_keeps_fixed_pack_boundary(self):
        with self.assertRaises(ContractError):guardrail_results.validate_request(self.request())

    def test_bad_effect_time_usage_and_identity_cannot_be_saved(self):
        request=self.request()
        bundle={'request':request,'worker_result':worker.evaluate(request,clock=lambda:1000_000_000_000)}
        changes=(lambda v:v['effects'][0].update(counter_before=9),
                 lambda v:v['usage'].update(input_tokens=1),
                 lambda v:v['stage_timings'][0].update(finished_at=999),
                 lambda v:v['results'][0]['binding'].update(operation_id='other-op'))
        for change in changes:
            value=deepcopy(bundle);change(value['worker_result'])
            with self.assertRaises(ContractError):guardrail_results.validate_execution_bundle(value)

    def test_last_large_case_is_saved_reopened_and_replayed_with_identical_receipt(self):
        request=self.request(1600,1599)
        bundle={'request':request,'worker_result':worker.evaluate(request,clock=lambda:1000_000_000_000)}
        binding=request['stages'][0]['binding']
        scenario='guardrail:'+hashlib.sha256(canonical_bytes(request)).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'journal.sqlite'
            with ExecutionJournal(path) as journal:
                record=journal.begin(binding,scenario,request['target']['runtime_image_id'],run_deadline=6400,timeout_seconds=120)
                for state in ('CREATED','STARTING','RUNNING','STOPPED'):
                    record=journal.advance(binding['run_id'],binding['operation_id'],record['owner_token'],state,container_id='a'*64)
                runner=object.__new__(PartitionedGuardrailRunner)
                receipt=runner._receipt(record,status='COMPLETED',reason=None,stopped=True,cleaned=True,
                    exit_code=0,elapsed=100,verified=True,normalized=bundle)
                self.assertEqual(journal.finish(binding['run_id'],binding['operation_id'],record['owner_token'],receipt)['receipt'],receipt)
            with ExecutionJournal(path) as journal:
                self.assertEqual(journal.get(binding['run_id'],binding['operation_id'])['receipt'],receipt)
                self.assertEqual(journal.begin(binding,scenario,request['target']['runtime_image_id'],run_deadline=6400,timeout_seconds=120)['receipt'],receipt)

    def test_runner_uses_query_scale_validator_with_existing_execution_path(self):
        request=self.request()
        runner=object.__new__(PartitionedGuardrailRunner);runner.case_count=400
        def execute(scenario,binding,raw,parser,**options):
            self.assertEqual(raw,canonical_bytes(request))
            self.assertEqual(binding,request['stages'][0]['binding'])
            return parser(canonical_bytes(worker.evaluate(request,clock=lambda:1000_000_000_000)))
        with patch.object(runner,'_run_fixed',side_effect=execute):
            bundle=runner.run(request,run_deadline=6400)
        self.assertEqual(bundle['request'],request)


if __name__=='__main__':unittest.main()
