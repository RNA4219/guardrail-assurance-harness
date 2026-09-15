"""大規模bundleの純粋な再束縛cacheは型・文脈・返却値を混同しない。"""
from copy import deepcopy
import unittest
from unittest.mock import patch
from tests import test_llm_target_revision as seed
from gah import run_evidence


class BoundCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        seed.LlmTargetRevisionTests.setUpClass.__func__(cls)
    def setUp(self):run_evidence._cached_bound.cache_clear()
    def test_complete_content_key_and_caller_mutation_are_isolated(self):
        bound=self.changed['bound_run'];original=run_evidence._bound_uncached
        with patch.object(run_evidence,'_bound_uncached',wraps=original) as impl:
            first=run_evidence._bound(bound,self.context)
            self.assertEqual(first,original(bound,self.context))
            first['ci_eligible']=True
            self.assertFalse(run_evidence._bound(bound,self.context)['ci_eligible'])
            self.assertEqual(impl.call_count,1)
            with patch('gah.evaluation_authority._source_digest',return_value='f'*64):
                run_evidence._bound(bound,self.context)
            self.assertEqual(impl.call_count,2)
    def test_four_large_runs_do_not_recompute_on_each_rotation(self):
        from gah.immutable_cache import binding_cache
        binding_cache.clear()
        bundles=[]
        for index in range(4):
            bound=deepcopy(self.changed['bound_run'])
            bound['manifest']['run_id']='cache-rotation-'+str(index)
            bundles.append(bound)
        original=run_evidence._bound_uncached
        with patch.object(run_evidence,'_bound_uncached',wraps=original) as impl:
            for _ in range(3):
                for bound in bundles:
                    actual=run_evidence._bound(bound,self.context)
                    self.assertEqual(actual['manifest']['run_id'],bound['manifest']['run_id'])
            self.assertEqual(impl.call_count,4)
        self.assertLessEqual(binding_cache.info().retained_bytes,16*1024*1024)

    def test_missing_and_changed_baseline_context_cannot_reuse_valid_binding(self):
        run_evidence._bound(self.changed['bound_run'],self.context)
        invalid=deepcopy(self.context);invalid['targets'][0]['target_ref']['digest']='f'*64
        for context in (None,invalid):
            with self.assertRaises(run_evidence.EvidenceError):run_evidence._bound(self.changed['bound_run'],context)
    def test_non_plain_types_are_not_normalized_into_accepted_documents(self):
        class Dictionary(dict):pass
        with self.assertRaises(run_evidence.EvidenceError):run_evidence._bound(Dictionary(self.initial['bound_run']))


if __name__=='__main__':unittest.main()
