"""400実入力を正規契約へ固定する部品。実runtime採択の試験とは区別する。"""
from copy import deepcopy
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from gah.llm_materialization import build,prepared_response
from gah.policy import initial_policy_profile
from gah.run_contracts import content_ref
from gah.semantic_conditions import compare
from gah.contracts import ContractError,MAX_DOCUMENT_BYTES
from gah.wire import canonical_bytes


class LlmMaterializationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.args=dict(policy_generation=1,run_id='llm-initial',now=100,target_version='baseline-v1',
            image_id='sha256:'+'a'*64,worker_digest='b'*64,isolation_profile={'network':'none'})
        cls.initial=build(initial_policy_profile(),**cls.args)
        cls.baseline={'baseline_ref':content_ref('baseline','llm-baseline',{'synthetic_reference':True}),
            'targets':[{'control_id':c['control_id'],'target_ref':c['target_ref']} for c in cls.initial['bound_run']['registry']['controls']]}
        cls.old=build(initial_policy_profile(),**{**cls.args,'run_id':'llm-old','generation':2,'baseline_context':cls.baseline})
        cls.new=build(initial_policy_profile(),**{**cls.args,'run_id':'llm-new','generation':3,'baseline_context':cls.baseline,'target_version':'degraded-v2'})

    def test_complete_inputs_and_stage_order_are_fixed_within_transport_limit(self):
        for value,trials,stages in ((self.initial,400,600),(self.old,800,1200),(self.new,800,1200)):
            material=value['materialization'];bound=value['bound_run'];plan=bound['plan']
            self.assertEqual(material['case_count'],400)
            self.assertEqual(material['planned_trials'],trials);self.assertEqual(material['planned_stages'],stages)
            cases={case['case_id']:case for case in value['pack']['case_sets']['acceptance']['cases']}
            for entry in plan['entries']:
                self.assertEqual(entry['stage_ids'],[s['stage_id'] for s in cases[entry['case_id']]['session_steps']])
            response=prepared_response(value)
            self.assertLess(len(canonical_bytes(response)),MAX_DOCUMENT_BYTES-1024)
            self.assertNotIn('pack',response)
            self.assertFalse(material['runtime_verified']);self.assertFalse(material['authority_connected'])
            self.assertFalse(response['ci_eligible'])
            self.assertFalse(value['target_document']['trained_model'])

    def test_only_target_changes_while_evaluator_and_baseline_conditions_stay_fixed(self):
        value=compare(self.old['bound_run'],self.new['bound_run'],previous_baseline_context=self.baseline,following_baseline_context=self.baseline)
        self.assertEqual(value['changed_axes'],['target']);self.assertTrue(value['same_evaluation_conditions'])
        self.assertEqual(self.old['evaluator_document'],self.new['evaluator_document'])
        old_targets={canonical_bytes(e['target_ref']) for e in self.new['bound_run']['plan']['entries'] if e['variant']=='baseline'}
        self.assertEqual(old_targets,{canonical_bytes(self.baseline['targets'][0]['target_ref'])})
        self.assertEqual(len(self.new['execution_profile']['bindings']),2)

    def test_baseline_oracles_preserve_full_synthetic_references_and_reject_other_kinds(self):
        from gah.baseline_authority import _refs_from_source
        from gah.baselines import _unique_refs_in_cases, oracle_ref
        bound=self.initial['bound_run']
        evaluators,oracles=_refs_from_source({'bound':bound})
        self.assertEqual(evaluators,bound['contract']['evaluator_refs'])
        self.assertEqual(oracles,_unique_refs_in_cases(bound['case_set']))
        self.assertEqual(len(oracles),400)
        self.assertTrue(all(ref['kind']=='synthetic_policy_oracle' for ref in oracles))
        with self.assertRaises(ContractError):oracle_ref(dict(oracles[0],kind='target'))

    def test_missing_baseline_unknown_version_and_unpinned_image_are_rejected(self):
        for fields in ({'generation':2},{'baseline_context':self.baseline},{'target_version':'unapproved'},
                       {'image_id':'mutable:latest'},{'worker_digest':'unknown'},{'policy_generation':True}):
            with self.subTest(fields=fields),self.assertRaises(ContractError):
                build(initial_policy_profile(),**{**self.args,**fields})


if __name__=='__main__':unittest.main()
