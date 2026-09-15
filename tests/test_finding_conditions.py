"""修復の意味的条件を実400ケースbundleで検査する。これは認証受入ではない。"""
from copy import deepcopy
import sqlite3
import unittest
from tests import test_llm_target_revision as seed
from gah import finding_lifecycle, llm_transitions
from gah.adoption import AdoptionError
from gah.run_contracts import content_ref


class FindingConditionsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        seed.LlmTargetRevisionTests.setUpClass.__func__(cls)
        cls.old=llm_transitions.build(cls.initial['bound_run']['contract'],cls.changed['bound_run']['contract'],
            baseline_record=cls.baseline,source_prepared=cls.initial,now=1000,old_run_id='old-check',new_run_id='bad-check',
            following_registry=cls.changed['bound_run']['registry'])['new']
        contract=deepcopy(cls.initial['bound_run']['contract'])
        contract.update(contract_id='fixed-contract',generation=2,comparison={'mode':'required','baseline_ref':cls.context['baseline_ref'],'changed_axes':[],'reason':None})
        cls.new=llm_transitions.build(cls.initial['bound_run']['contract'],contract,baseline_record=cls.baseline,
            source_prepared=cls.initial,now=1000,old_run_id='repair-old',new_run_id='repair-new')['new']
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.db.row_factory=sqlite3.Row;self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE transition_runs(run_id TEXT,candidate_id TEXT,side TEXT)')
        self.db.executemany('INSERT INTO transition_runs VALUES(?,?,?)',[('bad-check','bad','new'),('repair-new','repair','new')])
        self.finding={'control_ref':content_ref('control',self.old['bound_run']['registry']['controls'][0]['control_id'],self.old['bound_run']['registry']['controls'][0])}
        self.pending={'new_run_ref':content_ref('run_manifest','repair-new',self.new['bound_run']['manifest']),
            'changed_target_ref':self.new['bound_run']['registry']['controls'][0]['target_ref']}
    def compare(self):
        old={'bound':self.old['bound_run']};new={'bound':self.new['bound_run'],'receipt':{'manifest_ref':self.pending['new_run_ref']}}
        def resolve(run_id):
            prepared=self.old if run_id=='bad-check' else self.new
            return prepared['bound_run'],prepared['baseline_context']
        return finding_lifecycle._conditions(old,new,self.finding,self.pending,resolve,self.db)
    def test_authenticated_candidate_shape_can_preserve_conditions_while_target_changes(self):
        result=self.compare();self.assertTrue(result['same_evaluation_conditions']);self.assertEqual(result['changed_axes'],['target'])
        self.pending=deepcopy(self.pending);self.pending['changed_target_ref']['id']='unrelated-target'
        with self.assertRaisesRegex(AdoptionError,'CHANGED_TARGET_MISMATCH'):self.compare()
    def test_candidate_without_new_side_mapping_is_rejected(self):
        self.db.execute("UPDATE transition_runs SET side='old' WHERE run_id='repair-new'")
        with self.assertRaisesRegex(AdoptionError,'CANDIDATE_SOURCE_REQUIRED'):self.compare()


if __name__=='__main__':unittest.main()
