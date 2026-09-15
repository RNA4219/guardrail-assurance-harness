"""無害な固定400ケースの後続比較構造。作成した参照を実Evidenceとして扱わない。"""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from gah import baseline_authority, llm_materialization, llm_transitions
from gah.contracts import ContractError
from gah.docker_runner import PROFILE
from gah.policy import initial_policy_profile
from gah.run_contracts import content_ref


def data():
    image = 'sha256:' + 'a' * 64
    target = llm_materialization.target_document('baseline-v1', image)
    context = {'baseline_ref': {'kind':'baseline', 'id':'synthetic-baseline-one', 'digest':'b'*64},
               'targets':[{'control_id':'LC-guardrail', 'target_ref':content_ref('target', target['target_id'], target)}]}
    generated = llm_materialization.build(initial_policy_profile(), policy_generation=1,
        run_id='synthetic-normal-two', now=1000, target_version='baseline-v1', image_id=image,
        worker_digest='c'*64, isolation_profile=PROFILE, generation=2, baseline_context=context)
    prepared = llm_transitions.rebind(generated, generated['bound_run'], context)
    source = {'bound':prepared['bound_run'], 'receipt':{},
              'decision':{'decision_id':'synthetic-decision'}, 'closure':{'closure_id':'synthetic-closure'},
              'evidences':[{'evidence_id':'synthetic-evidence', 'valid_until':9000}]}
    baseline = baseline_authority.build_candidate(source, 'synthetic-series', 'synthetic-proposal',
        1000, expected_generation=1)['record']
    previous = prepared['bound_run']['contract']
    following = deepcopy(previous)
    following.update(contract_id='synthetic-contract-three', generation=3)
    following['comparison']['baseline_ref'] = content_ref('baseline', baseline['baseline_id'], baseline)
    return previous, following, baseline, prepared


class LlmFollowingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inputs = data()

    def setUp(self):
        self.previous, self.following, self.baseline, self.prepared = deepcopy(self.inputs)

    def build(self, old_id='llm-following-old', new_id='llm-following-new'):
        return llm_transitions.build_following(self.previous, self.following,
            baseline_record=self.baseline, source_prepared=self.prepared, now=1001,
            old_run_id=old_id, new_run_id=new_id)

    def test_complete_old_comparison_and_new_baseline_both_have_eight_hundred_trials(self):
        result = self.build()
        self.assertEqual(result['transition']['schema_version'], 2)
        self.assertEqual(result['old']['baseline_context'], self.prepared['baseline_context'])
        self.assertNotEqual(result['old']['baseline_context']['baseline_ref'], result['new']['baseline_context']['baseline_ref'])
        self.assertEqual(result['old']['bound_run']['plan']['entries'], self.prepared['bound_run']['plan']['entries'])
        for side in ('old', 'new'):
            bound = result[side]['bound_run']
            self.assertEqual(len(bound['plan']['entries']), 800)
            self.assertEqual(result[side]['materialization']['planned_stages'], 1200)
            self.assertEqual(bound['manifest']['deadline'] - bound['manifest']['created_at'], 5400)
            self.assertFalse(result[side]['materialization']['ci_eligible'])
        self.assertFalse(result['authority_connected'])
        self.assertFalse(result['ci_eligible'])

    def test_following_candidate_sections_fit_storage_and_preserve_every_trial(self):
        import sqlite3
        from gah import candidate_sections
        from gah.contracts import MAX_DOCUMENT_BYTES
        from gah.wire import canonical_bytes
        runs = self.build()
        with sqlite3.connect(':memory:') as db:
            db.row_factory = sqlite3.Row
            db.execute('CREATE TABLE authority_artifacts(kind,id,digest,payload_json,run_id,PRIMARY KEY(kind,id,digest))')
            saved = candidate_sections.pack(db, 'following-size', runs)
            self.assertEqual(candidate_sections.unpack(db, 'following-size', saved), runs)
            self.assertEqual(candidate_sections.reference({'candidate_id':'following-size', 'runs':runs}),
                content_ref('contract_candidate', 'following-size', {'candidate_id':'following-size', 'runs':saved}))
            for row in db.execute('SELECT payload_json FROM authority_artifacts'):
                self.assertLessEqual(len(row[0].encode('utf8')), MAX_DOCUMENT_BYTES)
            self.assertLess(len(canonical_bytes(saved)), MAX_DOCUMENT_BYTES)

    def test_unknown_source_fields_and_materialization_substitution_are_rejected(self):
        self.prepared['materialization']['planned_trials'] = 799
        with self.assertRaises(ContractError):
            self.build()
        self.prepared = deepcopy(self.inputs[3])
        self.prepared['extra'] = True
        with self.assertRaises(ContractError):
            self.build()

    def test_previous_baseline_and_reused_run_ids_are_rejected(self):
        self.following['comparison']['baseline_ref'] = self.prepared['baseline_context']['baseline_ref']
        with self.assertRaises(ContractError):
            self.build()
        self.following = deepcopy(self.inputs[1])
        with self.assertRaises(ContractError):
            self.build(old_id=self.prepared['bound_run']['manifest']['run_id'])
        with self.assertRaises(ContractError):
            self.build(old_id='same', new_id='same')

    def test_cached_structure_rechecks_complete_inputs_and_implementation(self):
        from unittest.mock import patch
        llm_transitions._cached_following.cache_clear()
        original = llm_transitions._build_following
        with patch.object(llm_transitions, '_build_following', wraps=original) as build:
            self.build()
            self.build()
            self.assertEqual(build.call_count, 1)
            self.prepared['materialization']['planned_stages'] = 1199
            with self.assertRaises(ContractError):
                self.build()
            self.assertEqual(build.call_count, 2)
        self.prepared = deepcopy(self.inputs[3])
        with patch.object(llm_transitions, '_build_following', wraps=original) as changed:
            self.build()
            self.assertEqual(changed.call_count, 1)

    def test_generation_jump_and_non_json_input_are_rejected(self):
        self.following['generation'] = 4
        with self.assertRaises(ContractError):
            self.build()
        self.following = deepcopy(self.inputs[1])
        self.prepared['bound_run']['plan']['entries'] = tuple(self.prepared['bound_run']['plan']['entries'])
        with self.assertRaises(ContractError):
            self.build()

    def test_output_changes_do_not_modify_source(self):
        saved = deepcopy(self.prepared)
        result = self.build()
        result['old']['bound_run']['plan']['entries'].clear()
        result['new']['materialization']['planned_trials'] = 0
        self.assertEqual(self.prepared, saved)


if __name__ == '__main__':
    unittest.main()
