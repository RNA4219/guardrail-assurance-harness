"""authority内の純粋な契約再束縛は型・完全入力・ソースを保持する。"""
from copy import deepcopy
import unittest
from unittest.mock import patch
from tests import test_llm_target_revision as seed
from gah import cache_inputs,run_contracts
from gah.contracts import ContractError


class BindingCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):seed.LlmTargetRevisionTests.setUpClass.__func__(cls)
    def setUp(self):cache_inputs._bound_manifest.cache_clear()
    def arguments(self):
        b=self.changed['bound_run']
        return [b[k] for k in ('manifest','contract','plan','policy','registry','case_set')]
    def test_complete_input_matches_original_and_returns_independent_objects(self):
        args=self.arguments();original=run_contracts.bind_run_manifest
        expected=original(*args,baseline_context=self.context)
        with patch.object(run_contracts,'bind_run_manifest',wraps=original) as function:
            first=cache_inputs.bind_run_manifest(*args,baseline_context=self.context)
            self.assertEqual(first,expected);first['case_set']['cases'][0]['case_id']='caller-change'
            self.assertEqual(cache_inputs.bind_run_manifest(*args,baseline_context=self.context),expected)
            self.assertEqual(function.call_count,1)
            with patch('gah.evaluation_authority._source_digest',return_value='a'*64):
                self.assertEqual(cache_inputs.bind_run_manifest(*args,baseline_context=self.context),expected)
            self.assertEqual(function.call_count,2)
    def test_both_binding_paths_reuse_four_full_runs_within_shared_limit(self):
        from gah import run_evidence
        from gah.immutable_cache import binding_cache
        binding_cache.clear()
        bundles=[]
        for index in range(4):
            bound=deepcopy(self.changed['bound_run'])
            bound['manifest']['run_id']='shared-rotation-'+str(index)
            bundles.append(bound)
        binder=run_contracts.bind_run_manifest
        evidence_binder=run_evidence._bound_uncached
        with patch.object(run_contracts,'bind_run_manifest',wraps=binder) as first, \
             patch.object(run_evidence,'_bound_uncached',wraps=evidence_binder) as second:
            for _ in range(3):
                for bound in bundles:
                    args=[bound[k] for k in ('manifest','contract','plan','policy','registry','case_set')]
                    self.assertEqual(cache_inputs.bind_run_manifest(*args,baseline_context=self.context),bound)
                    self.assertEqual(run_evidence._bound(bound,self.context),bound)
            self.assertEqual(first.call_count,4)
            self.assertEqual(second.call_count,4)
        self.assertEqual(binding_cache.info().currsize,8)
        self.assertLessEqual(binding_cache.info().retained_bytes,16*1024*1024)

    def test_changed_or_missing_context_and_modified_manifest_are_rejected(self):
        args=self.arguments();cache_inputs.bind_run_manifest(*args,baseline_context=self.context)
        wrong=deepcopy(self.context);wrong['targets'][0]['target_ref']['digest']='f'*64
        for context in (None,wrong):
            with self.assertRaises(ContractError):cache_inputs.bind_run_manifest(*args,baseline_context=context)
        args=deepcopy(args);args[0]['target_refs'][0]['digest']='f'*64
        with self.assertRaises(ContractError):cache_inputs.bind_run_manifest(*args,baseline_context=self.context)
    def test_non_plain_types_are_checked_before_json_encoding(self):
        args=self.arguments();args[0]=type('ManifestSubclass',(dict,),{})(args[0])
        with self.assertRaises(ContractError):cache_inputs.bind_run_manifest(*args,baseline_context=self.context)


if __name__=='__main__':unittest.main()
