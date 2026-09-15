"""全入力が同じ純粋な契約検査だけを再利用し、入出力は独立させる。"""
from copy import deepcopy
import unittest
from unittest.mock import patch
from tests import test_llm_target_revision as seed
from gah import contract_updates as transitions
from gah.contracts import ContractError


class TransitionCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):seed.LlmTargetRevisionTests.setUpClass.__func__(cls)
    def setUp(self):transitions._checked_transition.cache_clear()
    def arguments(self):
        return [self.initial['bound_run']['contract'], self.changed['bound_run']['contract']], {
            'baseline_record':self.baseline, 'baseline_source_bound':self.initial['bound_run'],
            'following_registry':self.changed['bound_run']['registry']}
    def test_matches_uncached_validation_and_separates_results(self):
        args,kw=self.arguments();original=transitions._bind_contract_transition
        expected=original(*args,**kw)
        with patch.object(transitions,'_bind_contract_transition',wraps=original) as impl:
            first=transitions.bind_contract_transition(*args,**kw)
            self.assertEqual(first,expected)
            first['source_bound']['case_set']['cases'].clear()
            first['next_contract'].clear()
            self.assertEqual(transitions.bind_contract_transition(*args,**kw),expected)
            self.assertEqual(impl.call_count,1)
            with patch('gah.evaluation_authority._source_digest',return_value='f'*64):
                self.assertEqual(transitions.bind_contract_transition(*args,**kw),expected)
            self.assertEqual(impl.call_count,2)
    def test_changes_in_any_semantic_input_are_not_cached_as_valid(self):
        args,kw=self.arguments();transitions.bind_contract_transition(*args,**kw)
        for field in ('baseline_record','baseline_source_bound','following_registry'):
            altered=deepcopy(kw);altered[field]['unexpected']=True
            with self.subTest(field=field),self.assertRaises(ContractError):
                transitions.bind_contract_transition(*args,**altered)
        for i in (0,1):
            altered=deepcopy(args);altered[i]['generation']=True
            with self.subTest(contract=i),self.assertRaises(ContractError):
                transitions.bind_contract_transition(*altered,**kw)
        with self.assertRaises(ContractError):
            transitions.bind_contract_transition(*args,**kw,source_baseline_context={})
    def test_json_encoding_does_not_legalize_non_plain_input(self):
        args,kw=self.arguments();args[0]=type('ContractSubclass',(dict,),{})(args[0])
        with self.assertRaises(ContractError):transitions.bind_contract_transition(*args,**kw)


if __name__=='__main__':unittest.main()
