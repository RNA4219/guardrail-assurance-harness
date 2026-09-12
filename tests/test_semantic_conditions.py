"""条件比較部品を既存の無害な構造fixtureで検査する。"""
from copy import deepcopy
import importlib.util
from pathlib import Path
import sys
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value
seed=module('semantic_seed',ROOT/'tests/test_run_contracts.py')
from gah import semantic_conditions as candidate
from gah.contracts import ContractError
from gah.run_contracts import bind_run_manifest,content_ref


def make(mutate=None,*,required=False):
    f=seed._fixtures('required' if required else 'not_applicable')
    p=seed._plan(f,include_baseline=required)
    m=seed._manifest(f,p,purpose='regression' if required else 'baseline_candidate')
    m['control_ids']=sorted(control['control_id'] for control in f['registry']['controls'])
    if mutate:mutate(f,p,m)
    c=f['contract']
    for field,kind,key in (('policy','policy_profile','policy_id'),('registry','control_registry','registry_id'),('case_set','case_set','case_set_id')):
        c[field+'_ref']=content_ref(kind,f[field][key],f[field])
    p['contract_ref']=content_ref('evaluation_contract',c['contract_id'],c)
    m['contract_ref']=p['contract_ref']
    m['policy_ref']=c['policy_ref']
    m['plan_ref']=content_ref('trial_plan',p['plan_id'],p)
    m['baseline_ref']=c['comparison']['baseline_ref']
    context=None
    if required:context={'baseline_ref':m['baseline_ref'],'targets':[{'control_id':x['control_id'],'target_ref':x['target_ref']} for x in f['registry']['controls']]}
    bound=bind_run_manifest(m,c,p,f['policy'],f['registry'],f['case_set'],baseline_context=context)
    return bound,context


class SemanticTests(unittest.TestCase):
    def compare(self,mutate,required=False):
        a,ac=make(required=required);b,bc=make(mutate,required=required)
        return candidate.compare(a,b,previous_baseline_context=ac,following_baseline_context=bc)
    def test_run_plan_contract_identity_and_time_shift_are_not_conditions(self):
        def change(f,p,m):
            f['contract'].update(contract_id='renamed-contract',generation=2)
            p['plan_id']='renamed-plan';m['run_id']='renamed-run';m['created_at']+=10;m['deadline']+=10
        value=self.compare(change)
        self.assertEqual(value['changed_axes'],[])
        self.assertTrue(value['same_evaluation_conditions'])
        self.assertNotEqual(value['previous']['manifest_ref'],value['following']['manifest_ref'])
    def test_target_version_is_separate_from_measurement(self):
        def change(f,p,m):
            target=content_ref('target','target-1',{'version':2})
            for control in f['registry']['controls']:control['target_ref']=target
            for entry in p['entries']:entry['target_ref']=target
            m['target_refs']=[target]
        value=self.compare(change)
        self.assertEqual(value['changed_axes'],['target']);self.assertTrue(value['same_evaluation_conditions'])
    def test_duration_budget_is_not_hidden_by_time_projection(self):
        value=self.compare(lambda f,p,m:m.update(deadline=m['deadline']-1))
        self.assertEqual(value['changed_axes'],['policy']);self.assertFalse(value['same_evaluation_conditions'])
    def test_oracle_and_label_change_is_corpus_change(self):
        def change(f,p,m):
            case=f['case_set']['cases'][0]
            case['expected_label']='negative';case['oracle_ref']=content_ref('oracle','oracle-2',{'answer':'allow'})
            case['session_steps'][0]['expected_detection']='allow'
        self.assertEqual(self.compare(change)['changed_axes'],['corpus'])
    def test_additional_trials_change_measurement(self):
        def change(f,p,m):
            extra=deepcopy(p['entries'])
            for entry in extra:entry['trial_id']='trial-2'
            p['entries'].extend(extra)
        self.assertEqual(self.compare(change)['changed_axes'],['corpus'])
    def test_environment_is_a_condition(self):
        value=self.compare(lambda f,p,m:m.update(environment_ref=content_ref('environment','env-2',{'os':'test2'})))
        self.assertEqual(value['changed_axes'],['environment']);self.assertFalse(value['same_evaluation_conditions'])
    def test_baseline_revision_keeps_measurement_but_changes_evaluation(self):
        def change(f,p,m):f['contract']['comparison']['baseline_ref']=content_ref('baseline','baseline-2',{'version':2})
        value=self.compare(change,required=True)
        self.assertEqual(value['changed_axes'],[]);self.assertTrue(value['baseline_changed'])
        self.assertTrue(value['same_measurement_conditions']);self.assertFalse(value['same_evaluation_conditions'])
    def test_independent_entry_order_is_not_stage_order(self):
        self.assertEqual(self.compare(lambda f,p,m:p['entries'].reverse())['changed_axes'],[])
        bound,context=make();bound['plan']['entries'][0]['stage_ids']=['unknown-stage']
        with self.assertRaises(ContractError):candidate.signature(bound)
    def test_missing_baseline_context_and_broken_refs_fail(self):
        bound,context=make(required=True)
        with self.assertRaisesRegex(ContractError,'BASELINE_CONTEXT_REQUIRED'):candidate.signature(bound)
        bound,_=make();bound['contract']['registry_ref']['digest']='a'*64
        with self.assertRaises(ContractError):candidate.signature(bound)
    def test_evaluator_revision_is_derived_from_real_bindings(self):
        def change(f,p,m):
            old=f['contract']['evaluator_refs'][0]
            new=content_ref('evaluator',old['id'],{'family':'constraint','version':2})
            f['contract']['evaluator_refs'][0]=new
            for control in f['registry']['controls']:
                for obligation in control['obligations']:
                    if obligation['evaluator_ref']==old:obligation['evaluator_ref']=new
            for entry in p['entries']:
                if entry['evaluator_ref']==old:entry['evaluator_ref']=new
        value=self.compare(change)
        self.assertEqual(value['changed_axes'],['evaluator'])
        self.assertFalse(value['same_measurement_conditions'])
    def test_stricter_threshold_is_policy_change(self):
        value=self.compare(lambda f,p,m:f['policy']['thresholds']['noncritical'].update(recall_min=[96,100]))
        self.assertEqual(value['changed_axes'],['policy'])
    def test_dependencies_and_requiredness_are_not_ignored(self):
        value=self.compare(lambda f,p,m:f['registry']['controls'][1].update(dependencies=[]))
        self.assertEqual(value['changed_axes'],['policy'])
        def optional(f,p,m):
            f['registry']['controls'][0]['obligations'][0]['required']=False
            for entry in p['entries']:
                if entry['obligation_id']=='obligation-constraint':entry['required']=False
        value=self.compare(optional)
        self.assertEqual(value['changed_axes'],['corpus','policy'])
    def test_legal_stage_order_change_remains_a_measurement_change(self):
        def two_stages(f,p,m):
            case=f['case_set']['cases'][0]
            second=deepcopy(case['session_steps'][0]);second['stage_id']='stage-2'
            second['input_ref']=content_ref('input','input-2',{'input':'fixed-second'})
            case['session_steps'].append(second)
            for entry in p['entries']:entry['stage_ids']=['stage-1','stage-2']
        def reversed_stages(f,p,m):
            two_stages(f,p,m);f['case_set']['cases'][0]['session_steps'].reverse()
            for entry in p['entries']:entry['stage_ids'].reverse()
        old,_=make(two_stages);new,_=make(reversed_stages)
        value=candidate.compare(old,new)
        self.assertEqual(value['changed_axes'],['corpus'])
    def test_declared_axes_do_not_override_derived_differences(self):
        def change(f,p,m):
            f['contract']['comparison']['changed_axes']=[]
            m['deadline']-=1
        value=self.compare(change)
        self.assertEqual(value['changed_axes'],['policy'])

    def test_equivalent_rational_threshold_is_same_condition(self):
        value=self.compare(lambda f,p,m:f['policy']['thresholds']['noncritical'].update(recall_min=[19,20]))
        self.assertEqual(value['changed_axes'],[])
        self.assertTrue(value['same_evaluation_conditions'])
    def test_baseline_target_substitution_cannot_hide_behind_same_reference(self):
        old,old_context=make(required=True)
        new=deepcopy(old);new_context=deepcopy(old_context)
        target=content_ref('target','baseline-other',{'version':'other'})
        for item in new_context['targets']:item['target_ref']=target
        for entry in new['plan']['entries']:
            if entry['variant']=='baseline':entry['target_ref']=target
        new['manifest']['plan_ref']=content_ref('trial_plan',new['plan']['plan_id'],new['plan'])
        value=candidate.compare(old,new,previous_baseline_context=old_context,following_baseline_context=new_context)
        self.assertTrue(value['same_measurement_conditions'])
        self.assertFalse(value['baseline_changed'])
        self.assertTrue(value['baseline_targets_changed'])
        self.assertFalse(value['same_evaluation_conditions'])
    def test_candidate_target_revision_keeps_original_baseline_targets(self):
        old,context=make(required=True);new=deepcopy(old)
        target=content_ref('target','target-1',{'version':2})
        for control in new['registry']['controls']:control['target_ref']=target
        for entry in new['plan']['entries']:
            if entry['variant']=='candidate':entry['target_ref']=target
        new['contract']['registry_ref']=content_ref('control_registry',new['registry']['registry_id'],new['registry'])
        new['plan']['contract_ref']=content_ref('evaluation_contract',new['contract']['contract_id'],new['contract'])
        new['manifest'].update(target_refs=[target],contract_ref=new['plan']['contract_ref'],
            plan_ref=content_ref('trial_plan',new['plan']['plan_id'],new['plan']))
        value=candidate.compare(old,new,previous_baseline_context=context,following_baseline_context=context)
        self.assertEqual(value['changed_axes'],['target'])
        self.assertTrue(value['same_evaluation_conditions'])
        self.assertFalse(value['baseline_targets_changed'])

    def test_malformed_nested_documents_raise_fixed_contract_errors(self):
        original,_=make()
        for key in ('manifest','contract','plan','policy','registry','case_set'):
            for invalid in (None,True,[],{},'malformed'):
                with self.subTest(field=key,type=type(invalid).__name__):
                    bound=deepcopy(original);bound[key]=invalid
                    with self.assertRaises(ContractError):candidate.signature(bound)
    def test_reference_or_category_declarations_must_match_resolved_objects(self):
        for key in ('evaluator_refs','required_categories'):
            def change(f,p,m):
                f['contract'][key]=[content_ref('evaluator','unresolved',{})] if key=='evaluator_refs' else ['unresolved-category']
            with self.subTest(field=key):
                with self.assertRaises(ContractError):
                    bound,_=make(change);candidate.signature(bound)
    def test_context_must_be_exact_and_bound(self):
        bound,context=make(required=True)
        for invalid in (None,{},[],{**context,'role':'validator'},{**context,'targets':[]}):
            with self.subTest(value_type=type(invalid).__name__):
                with self.assertRaises(ContractError):candidate.signature(bound,baseline_context=invalid)

    def test_return_values_do_not_grant_authority_or_alias_inputs(self):
        bound,_=make();saved=deepcopy(bound)
        value=candidate.signature(bound)
        value['components']['policy']['controls'].clear()
        self.assertEqual(bound,saved)
        result=candidate.compare(bound,bound)
        self.assertFalse(result['authority_connected']);self.assertFalse(result['ci_eligible'])


if __name__=='__main__':unittest.main(verbosity=2)
