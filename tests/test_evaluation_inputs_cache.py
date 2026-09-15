"""保存済み資料の全内容を鍵にした純粋検査と、返却値の独立性。"""
from copy import deepcopy
import unittest
from unittest.mock import patch
from tests import test_llm_target_revision as seed
from gah import cache_inputs,run_contracts,corpus
from gah.contracts import ContractError

class EvaluationInputsCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):seed.LlmTargetRevisionTests.setUpClass.__func__(cls)
    def setUp(self):cache_inputs._evaluation_inputs.cache_clear()
    def arguments(self):
        b=self.initial['bound_run'];sets=self.initial['pack']['case_sets']
        return [b['contract'],b['policy'],b['registry'],b['case_set'],sets['calibration'],
                [sets['calibration'],sets['development']]]
    def test_full_inputs_source_version_and_output_ownership(self):
        args=self.arguments();binder=run_contracts.bind_evaluation_contract;reporter=corpus.corpus_report
        expected={'contract':binder(*args[:5])['contract'],'corpus_report':reporter(args[3],other_sets=args[5])}
        with patch.object(run_contracts,'bind_evaluation_contract',wraps=binder) as bind:
            with patch.object(corpus,'corpus_report',wraps=reporter) as report:
                first=cache_inputs.evaluation_inputs(*args);self.assertEqual(first,expected)
                first['contract']['use_cases'].clear();first['corpus_report'].clear()
                self.assertEqual(cache_inputs.evaluation_inputs(*args),expected)
                self.assertEqual(bind.call_count,1);self.assertEqual(report.call_count,1)
                with patch('gah.evaluation_authority._source_digest',return_value='a'*64):
                    self.assertEqual(cache_inputs.evaluation_inputs(*args),expected)
                self.assertEqual(bind.call_count,2)
    def test_other_set_changes_are_rechecked_and_overlap_is_reported(self):
        args=self.arguments();self.assertFalse(cache_inputs.evaluation_inputs(*args)['corpus_report']['usage_overlaps'])
        changed=deepcopy(args);overlap=deepcopy(changed[3]);overlap['case_set_id']='overlap-set';overlap['purpose']='development'
        changed[5].append(overlap)
        self.assertTrue(cache_inputs.evaluation_inputs(*changed)['corpus_report']['usage_overlaps'])
    def test_invalid_content_and_non_plain_type_do_not_reuse_success(self):
        args=self.arguments();cache_inputs.evaluation_inputs(*args)
        for field in ('case_id','content_digest'):
            wrong=deepcopy(args);wrong[3]['cases'][0][field]='changed'
            with self.assertRaises(ContractError):cache_inputs.evaluation_inputs(*wrong)
        wrong=deepcopy(args);wrong[0]=type('ContractSubclass',(dict,),{})(wrong[0])
        with self.assertRaises(ContractError):cache_inputs.evaluation_inputs(*wrong)

if __name__=='__main__':unittest.main()
