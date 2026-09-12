"""後続契約の禁止変更と申告差分を無害な構造fixtureで検査する。"""
import importlib.util
from copy import deepcopy
from pathlib import Path
import sys
import unittest

LOCAL = Path(__file__).resolve().parent
ROOT = LOCAL.parent
sys.path.insert(0, str(ROOT / 'src'))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


seed = load('revision_seed', LOCAL / 'test_semantic_conditions.py')
from gah import contract_revision_rules as rules
from gah.contracts import ContractError


def bound(generation, mutation=None):
    def prepare(fixture, plan, manifest):
        fixture['contract'].update(generation=generation, contract_id='revision-' + str(generation))
        fixture['contract']['comparison']['changed_axes'] = []
        if mutation:
            mutation(fixture, plan, manifest)
    return seed.make(prepare, required=True)


class RevisionRulesTests(unittest.TestCase):
    def inspect(self, mutation=None, *, old_mutation=None, generation=3):
        previous, pc = bound(2, old_mutation)
        following, nc = bound(generation, mutation)
        return rules.inspect_revision(previous, following,
            previous_baseline_context=pc, following_baseline_context=nc)

    def test_following_generation_is_only_a_structural_candidate(self):
        result = self.inspect()
        self.assertTrue(result['requires_old_conditions_regression'])
        for field in ('ci_eligible', 'authority_connected', 'adoption_verified', 'target_execution_verified'):
            self.assertFalse(result[field])
        self.assertFalse(result['conditions_compatible_for_revalidation'])

    def test_generation_skip_and_id_reuse_are_rejected(self):
        with self.assertRaisesRegex(ContractError, 'GENERATION_MISMATCH'):
            self.inspect(generation=4)
        with self.assertRaisesRegex(ContractError, 'ID_REUSED'):
            self.inspect(lambda f, p, m: f['contract'].update(contract_id='revision-2'))

    def test_declared_axes_cannot_hide_effective_budget_change(self):
        with self.assertRaisesRegex(ContractError, 'DECLARED_AXES_MISMATCH'):
            self.inspect(lambda f, p, m: m.update(deadline=m['deadline'] - 1))

    def test_critical_cannot_be_downgraded(self):
        def before(f, p, m):
            f['registry']['controls'][0]['criticality'] = 'critical'
        def after(f, p, m):
            f['contract']['comparison']['changed_axes'] = ['policy']
        with self.assertRaisesRegex(ContractError, 'CRITICAL_DOWNGRADED'):
            self.inspect(after, old_mutation=before)

    def test_required_obligation_cannot_be_made_optional(self):
        def optional(f, p, m):
            f['contract']['comparison']['changed_axes'] = ['policy', 'corpus']
            f['registry']['controls'][0]['obligations'][0]['required'] = False
            for entry in p['entries']:
                if entry['obligation_id'] == 'obligation-constraint':
                    entry['required'] = False
        with self.assertRaisesRegex(ContractError, 'MANDATORY_OBLIGATION_REMOVED'):
            self.inspect(optional)

    def test_required_control_cannot_be_removed(self):
        def remove(f,p,m):
            f['registry']['controls']=f['registry']['controls'][1:]
            f['registry']['controls'][0]['dependencies']=[]
            f['contract']['evaluator_refs']=f['contract']['evaluator_refs'][2:]
            f['contract']['use_cases']=['UC-LLM']
            f['contract']['comparison']['changed_axes']=['policy','corpus','evaluator','target']
            p['entries']=[entry for entry in p['entries'] if entry['obligation_id']=='obligation-llm']
            m['control_ids']=['control-llm'];m['use_cases']=['UC-LLM']
        with self.assertRaisesRegex(ContractError,'MANDATORY_CONTROL_REMOVED'):
            self.inspect(remove)

    def test_forbidden_event_cannot_be_weakened(self):
        def before(f,p,m):
            f['registry']['controls'][0]['obligations'][0]['event_policy']='forbidden'
            for entry in p['entries']:
                if entry['obligation_id']=='obligation-constraint':entry['event_policy']='forbidden'
        def after(f,p,m):
            f['contract']['comparison']['changed_axes']=['policy','corpus']
        with self.assertRaisesRegex(ContractError,'FORBIDDEN_EVENT_WEAKENED'):
            self.inspect(after,old_mutation=before)

    def test_target_content_change_and_name_only_change_are_distinct(self):
        previous,context=bound(2)
        for changed in (True,False):
            with self.subTest(content_changed=changed):
                following=deepcopy(previous)
                target=seed.content_ref('target','target-renamed',{'version':2}) if changed else {
                    **previous['registry']['controls'][0]['target_ref'],'id':'target-renamed'}
                for control in following['registry']['controls']:control['target_ref']=target
                for entry in following['plan']['entries']:
                    if entry['variant']=='candidate':entry['target_ref']=target
                c=following['contract'];p=following['plan'];m=following['manifest']
                c.update(contract_id='revision-3',generation=3)
                c['comparison']['changed_axes']=['target']
                c['registry_ref']=seed.content_ref('control_registry',following['registry']['registry_id'],following['registry'])
                p['contract_ref']=seed.content_ref('evaluation_contract',c['contract_id'],c)
                m.update(contract_ref=p['contract_ref'],target_refs=[target],
                    plan_ref=seed.content_ref('trial_plan',p['plan_id'],p))
                result=rules.inspect_revision(previous,following,
                    previous_baseline_context=context,following_baseline_context=context)
                self.assertEqual(result['conditions_compatible_for_revalidation'],changed)
                self.assertEqual({item['content_digest_changed'] for item in result['changed_targets']},{changed})
                self.assertFalse(result['target_execution_verified'])
                self.assertFalse(result['adoption_verified'])

    def test_changed_policy_cannot_reuse_same_adopted_generation(self):
        def stricter(f, p, m):
            f['policy']['thresholds']['noncritical']['recall_min'] = [96, 100]
            f['contract']['comparison']['changed_axes'] = ['policy']
        with self.assertRaisesRegex(ContractError, 'POLICY_GENERATION_REUSED'):
            self.inspect(stricter)
        def adopted(f, p, m):
            stricter(f, p, m)
            f['contract']['policy_generation'] = 2
        result = self.inspect(adopted)
        self.assertFalse(result['conditions_compatible_for_revalidation'])
        self.assertFalse(result['adoption_verified'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
