"""複合確定の不変応答と、組込みのfresh CI照会を分ける認証境界。"""
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from gah.adoption import AdoptionStore, AdoptionError
from gah.evaluation_authority import EvaluationExtension
from gah import combined_runs
from gah.policy import initial_policy_profile
from gah.run_contracts import content_ref


class CombinedCiBoundaryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.store = AdoptionStore(Path(temporary.name)/'authority.sqlite', clock=lambda:1000,
            bootstrap_policy=initial_policy_profile(), validator_digest='b'*64, extension=EvaluationExtension())
        self.addCleanup(self.store.close)
        self.manifest = {'run_id':'both', 'created_at':1000,
            'children':[{'run_id':'child-ci'}, {'run_id':'child-llm'}]}
        self.ref = content_ref('combined_run_manifest', 'both', self.manifest)
        self.root = {'manifest':self.manifest, 'permission_generation':self.store._permission_generation(self.store._db)}
        self.gates = [{'use_case':use, 'gate':{'assurance':'HEALTHY', 'execution_status':'COMPLETED',
            'ci_eligible':True, 'exit_code':0, 'outputs_ref':content_ref('run_outputs', name, {'run_id':name})}}
            for use,name in [('UC-CI','child-ci'),('UC-LLM','child-llm')]]
        # 全件測定は統合試験が担当。この試験は返却権限と不変保存の境界に絞る。
        load = patch.object(combined_runs, 'load', return_value=(self.root, [])); load.start(); self.addCleanup(load.stop)
        gates = patch.object(combined_runs, '_gates', side_effect=lambda *args:deepcopy(self.gates)); gates.start(); self.addCleanup(gates.stop)

    def call(self, action, name):
        return self.store.dispatch(12004,12004,{'schema_version':1,'action':action,
            'request_id':name,'run_id':'both','expected_manifest_ref':self.ref})

    def test_finalization_is_not_a_cached_ci_permission_and_fresh_read_can_pass(self):
        saved = self.call('combined_finalize', 'save')
        self.assertFalse(saved['ci_eligible']); self.assertIsNotNone(saved['receipt'])
        current = self.call('combined_current', 'current')
        self.assertTrue(current['ci_eligible']); self.assertEqual(current['exit_code'], 0)
        self.assertEqual(current['receipt'], saved['receipt'])
        self.gates[1]['gate'].update(ci_eligible=False, exit_code=1)
        changed = self.call('combined_current', 'current')
        self.assertFalse(changed['ci_eligible']); self.assertEqual(changed['receipt'], saved['receipt'])
        self.assertEqual(self.call('combined_finalize', 'save'), saved)

    def test_fresh_read_does_not_trust_instance_extension_success_claim(self):
        with patch.object(self.store._extension, 'execute', side_effect=AssertionError('untrusted extension dispatch')):
            value = self.call('combined_current', 'fresh')
        self.assertFalse(value['ci_eligible']); self.assertIn('COMBINED_OUTPUT_REQUIRED', value['reasons'])

    def test_subclass_cannot_grant_current_ci_permission(self):
        class Untrusted(EvaluationExtension):
            def execute(self, *args):
                raise AssertionError("must not dispatch")
        self.store._extension = Untrusted()
        with self.assertRaisesRegex(AdoptionError, "EXTENSION_INVALID"):
            self.call("combined_current", "subclass")

    def test_source_change_during_current_read_rolls_back_and_poison_fails_closed(self):
        from gah import evaluation_authority, read_checks
        with patch.object(read_checks, "_source_invalid", False):
            with patch.object(evaluation_authority, "_compute_source_digest", side_effect=["a"*64,"b"*64]):
                with self.assertRaisesRegex(AdoptionError, "EXTENSION_INVALID"):
                    self.call("combined_current", "changed-source")
            self.assertIsNone(self.store._db.execute("SELECT 1 FROM idempotency WHERE request_id='changed-source'").fetchone())
            with self.assertRaisesRegex(AdoptionError, "EXTENSION_INVALID"):
                self.call("combined_current", "after-change")

    def test_forged_finalization_origin_does_not_authorize_current_ci(self):
        self.call('combined_finalize', 'save')
        self.store._db.execute("UPDATE idempotency SET actor_id='manager' WHERE request_id='save'")
        with self.assertRaisesRegex(AdoptionError, 'COMBINED_ORIGIN_INVALID'):
            self.call('combined_current', 'current')

    def test_permission_change_preserves_receipt_but_rejects_current_ci(self):
        saved = self.call('combined_finalize', 'save')
        self.root['permission_generation'] += 1
        current = self.call('combined_current', 'current')
        self.assertFalse(current['ci_eligible']); self.assertIn('AUTHORITY_STALE',current['reasons'])
        self.assertEqual(current['receipt'],saved['receipt'])


if __name__ == '__main__': unittest.main()
