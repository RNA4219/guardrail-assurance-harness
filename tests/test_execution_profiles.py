"""対象と評価器の組ごとに実行器を厳格に固定する。"""
from copy import deepcopy
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from gah import execution_profiles as profiles
from gah.contracts import ContractError


class ExecutionProfileTests(unittest.TestCase):
    def setUp(self):
        self.old={'fixture_digest':'1'*64,'adapter_digests':['2'*64],'isolation_digest':'3'*64}
        self.profile={'schema_version':2,'kind':'execution_profile','isolation_digest':'3'*64,'bindings':[
            {'target_digest':'4'*64,'evaluator_digest':'5'*64,'fixture_digest':'1'*64,'adapter_digests':['2'*64]},
            {'target_digest':'6'*64,'evaluator_digest':'5'*64,'fixture_digest':'7'*64,'adapter_digests':['8'*64]}]}
        self.bound={'manifest':{'environment_ref':{'digest':'3'*64}},'plan':{'entries':[
            {'target_ref':{'digest':x['target_digest']},'evaluator_ref':{'digest':x['evaluator_digest']}}
            for x in self.profile['bindings']]}}

    def test_old_form_is_unchanged_and_target_versions_select_separate_executors(self):
        self.assertEqual(profiles.validate(self.old),self.old)
        self.assertEqual(profiles.expected(self.old,'4'*64,'5'*64),self.old)
        profiles.check_plan(self.profile,self.bound)
        self.assertEqual(profiles.expected(self.profile,'4'*64,'5'*64),self.old)
        self.assertEqual(profiles.expected(self.profile,'6'*64,'5'*64)['fixture_digest'],'7'*64)
        self.assertEqual(profiles.expected(self.profile,'6'*64,'5'*64)['adapter_digests'],['8'*64])

    def test_missing_extra_duplicate_or_unknown_pair_never_uses_another_target(self):
        for change in ('missing','extra','duplicate','wrong-evaluator'):
            value=deepcopy(self.profile)
            if change=='missing':value['bindings'].pop()
            elif change=='extra':value['bindings'].append({**value['bindings'][0],'target_digest':'9'*64})
            elif change=='duplicate':value['bindings'].append(deepcopy(value['bindings'][0]))
            else:value['bindings'][0]['evaluator_digest']='9'*64
            with self.subTest(change=change),self.assertRaises(ContractError):profiles.check_plan(value,self.bound)
        with self.assertRaises(ContractError):profiles.expected(self.profile,'9'*64,'5'*64)
        with self.assertRaises(ContractError):profiles.expected(self.profile,'4'*64,'9'*64)

    def test_shape_boolean_versions_and_isolation_are_strict(self):
        for mutate in (lambda x:x.update(schema_version=True),lambda x:x.update(kind='target'),
                       lambda x:x.update(allow_unknown=True),lambda x:x['bindings'][0].update(adapter_digests=[]),
                       lambda x:x['bindings'][0].update(fixture_digest='unknown')):
            value=deepcopy(self.profile);mutate(value)
            with self.assertRaises(ContractError):profiles.validate(value)
        value=deepcopy(self.profile);value['isolation_digest']='9'*64
        with self.assertRaisesRegex(ContractError,'BINDING_MISMATCH'):profiles.check_plan(value,self.bound)


class ProfileEvidenceIntegrationTests(unittest.TestCase):
    def test_mixed_executors_survive_storage_restart_and_reject_cross_target_results(self):
        import tempfile
        from tests.test_run_evidence import bound_fixture, attempt_for
        from gah.run_evidence import RunEvidenceStore, EvidenceError, bound_bundle_digest
        from gah.aggregation import aggregate
        f,plan,bound=bound_fixture()
        f['contract_ref_digest']=bound['manifest']['contract_ref']['digest']
        f['policy_ref_digest']=bound['manifest']['policy_ref']['digest']
        profile={'schema_version':2,'kind':'execution_profile','isolation_digest':bound['manifest']['environment_ref']['digest'],'bindings':[]}
        attempts=[]
        for index,entry in enumerate(plan['entries']):
            item={'target_digest':entry['target_ref']['digest'],'evaluator_digest':entry['evaluator_ref']['digest'],
                'fixture_digest':str(index+3)*64,'adapter_digests':[str(index+6)*64]}
            profile['bindings'].append(item)
            mode={'obligation-constraint':'constraint','obligation-mutation':'mutation','obligation-llm':'llm'}[entry['obligation_id']]
            attempt=attempt_for(f,plan,entry['obligation_id'],attempt_id='profile-'+str(index),mode=mode)
            for binding in (attempt['expected_binding'],attempt['result']['binding']):
                binding.update(fixture_digest=item['fixture_digest'],adapter_digest=item['adapter_digests'][0],isolation_digest=profile['isolation_digest'])
            attempts.append(attempt)
        allowed={'run-1':bound_bundle_digest(bound)}
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'evidence.sqlite'
            with RunEvidenceStore(path,clock=lambda:100,allowed_bindings=allowed) as store:
                store.start_run(bound,profile)
                wrong=deepcopy(attempts[0]);wrong['attempt_id']='cross-profile'
                for binding in (wrong['expected_binding'],wrong['result']['binding']):binding['fixture_digest']=profile['bindings'][1]['fixture_digest']
                with self.assertRaisesRegex(EvidenceError,'BINDING_MISMATCH'):store.record_attempt(wrong)
                for attempt in attempts:self.assertTrue(store.record_attempt(attempt)['accepted'])
                result=store.aggregate('run-1')
                expected=aggregate(bound,attempts,execution_profile=profile)
                expected['observed_at']=100
                import hashlib
                from gah.wire import canonical_bytes
                expected['aggregate_digest']=hashlib.sha256(canonical_bytes(expected)).hexdigest()
                self.assertEqual(result,expected)
                self.assertFalse(result['ci_eligible'])
                rejected=aggregate(bound,[wrong],execution_profile=profile)
                self.assertTrue(any(issue['code']=='BINDING_MISMATCH' for issue in rejected['issues']))
            with RunEvidenceStore(path,clock=lambda:100,allowed_bindings=allowed) as store:
                self.assertEqual(store.get_run('run-1')['execution_profile'],profile)
                self.assertEqual(store.aggregate('run-1'),result)

    def test_extra_executor_is_rejected_before_any_bound_run_is_saved(self):
        import tempfile
        from tests.test_run_evidence import bound_fixture
        from gah.run_evidence import RunEvidenceStore, EvidenceError, bound_bundle_digest
        _,_,bound=bound_fixture()
        profile={'schema_version':2,'kind':'execution_profile','isolation_digest':bound['manifest']['environment_ref']['digest'],
            'bindings':[{'target_digest':'4'*64,'evaluator_digest':'5'*64,'fixture_digest':'6'*64,'adapter_digests':['7'*64]}]}
        with tempfile.TemporaryDirectory() as folder:
            with RunEvidenceStore(Path(folder)/'evidence.sqlite',clock=lambda:100,allowed_bindings={'run-1':bound_bundle_digest(bound)}) as store:
                with self.assertRaisesRegex(EvidenceError,'INVALID_PROFILE'):store.start_run(bound,profile)
                self.assertIsNone(store._db.execute("SELECT 1 FROM bound_runs").fetchone())


if __name__=='__main__':unittest.main()
