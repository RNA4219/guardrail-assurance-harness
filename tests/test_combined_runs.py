"""両用途の事前宣言と、実台帳上の合算予約・停止境界を検査する。"""
from copy import deepcopy
from pathlib import Path
import sqlite3
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from gah import combined_runs as combined,resources
from gah.adoption import AdoptionError
from gah.policy import initial_policy_profile


class CombinedBoundaryTests(unittest.TestCase):
    def request(self):
        return {'schema_version':1,'action':'combined_prepare','request_id':'prepare-both','run_id':'both',
            'children':[{'use_case':use,'run_id':'child-'+str(i),'contract_series_id':'series-'+str(i),
                'expected_contract_ref':{'kind':'evaluation_contract','id':'contract-'+str(i),'digest':str(i+1)*64}} for i,use in enumerate(combined.USES)]}
    def test_both_uses_distinct_ids_and_no_status_or_budget_override(self):
        self.assertEqual(combined.validate_request(self.request()),self.request())
        for change in ('missing','duplicate-use','same-child','parent-child','status','budget','reordered'):
            request=self.request()
            if change=='missing':request['children'].pop()
            if change=='duplicate-use':request['children'][1]['use_case']='UC-CI'
            if change=='same-child':request['children'][1]['run_id']=request['children'][0]['run_id']
            if change=='parent-child':request['children'][0]['run_id']='both'
            if change=='status':request['ci_eligible']=True
            if change=='budget':request['budget']={'total_tokens':999999999}
            if change=='reordered':request['children'].reverse()
            with self.subTest(change=change),self.assertRaises(AdoptionError):combined.validate_request(request)
    def test_read_requires_complete_combined_reference(self):
        request={'schema_version':1,'action':'combined_current','request_id':'current','run_id':'both',
            'expected_manifest_ref':{'kind':'combined_run_manifest','id':'both','digest':'a'*64}}
        self.assertEqual(combined.validate_request(request),request)
        request['expected_manifest_ref']['kind']='run_manifest'
        with self.assertRaises(AdoptionError):combined.validate_request(request)


class CombinedBudgetTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:',isolation_level=None);self.db.row_factory=sqlite3.Row;self.addCleanup(self.db.close)
        self.db.execute('BEGIN IMMEDIATE');resources.create_schema(self.db)
        self.book=resources.ResourceBook(self.db);self.policy=initial_policy_profile()
        for run in ('ci','llm'):self.book.create_run(run,'a'*64,self.policy,'full',run+'-owner',1000,6400)
        self.root={'manifest':{'run_id':'both','created_at':1000,'deadline':6400,
            'children':[{'run_id':'ci'},{'run_id':'llm'}],'budget':deepcopy(self.policy['profiles']['full'])}}
        self.addCleanup(patch.stopall)
        patch.object(combined,'for_child',return_value=(self.root,{})).start()
        patch.object(combined,'_cancelled',return_value=False).start()
    def reserve(self,run,op,*,tokens=0):
        value={'case_trial_executions':1,'model_calls':int(tokens>0),'input_tokens':tokens,'output_tokens':0,
            'api_cost_usd_micros':0,'billing_ref':{'kind':'billing_basis','id':'local','digest':'b'*64},'billing_mode':'non_billed_local'}
        combined.check_start(self.db,run,1000,value,op)
        self.book.reserve(run,op,run+'-owner',1,value,1000)
        return value
    def test_four_total_slots_are_allowed_and_fifth_is_rejected_across_children(self):
        for run,op in [('ci','one'),('ci','two'),('llm','three'),('llm','four')]:self.reserve(run,op)
        with self.assertRaisesRegex(AdoptionError,'CONCURRENCY_LIMIT'):self.reserve('llm','five')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM resource_operations').fetchone()[0],4)
        combined.check_start(self.db,'ci',1000)
    def test_two_six_token_reservations_do_not_fit_a_shared_budget_of_ten(self):
        self.root['manifest']['budget']['total_tokens']=10
        first=self.reserve('ci','one',tokens=6)
        with self.assertRaisesRegex(AdoptionError,'RESOURCE_LIMIT'):self.reserve('llm','two',tokens=6)
        combined.check_start(self.db,'ci',1000,first,'one')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM resource_operations').fetchone()[0],1)
    def test_stopped_but_unknown_usage_keeps_shared_reservation(self):
        self.root['manifest']['budget']['total_tokens']=10
        self.reserve('ci','one',tokens=6);self.book.dispatch_intent('ci','one','ci-owner',1,1000)
        self.book.observe('ci','one','stop-one',stopped=True,usage=None,now=1000)
        with self.assertRaisesRegex(AdoptionError,'RESOURCE_LIMIT'):self.reserve('llm','two',tokens=6)
        self.assertEqual(self.book.snapshot('ci',1000)['resources']['unsettled'],1)
    def test_deadline_and_sibling_cancellation_block_new_dispatch(self):
        combined.check_start(self.db,'ci',6399)
        with self.assertRaisesRegex(AdoptionError,'COMBINED_EXPIRED'):combined.check_start(self.db,'ci',6400)
    def test_sibling_cancellation_blocks_new_dispatch(self):
        self.book.cancel('ci','ci-owner',1,1000)
        with self.assertRaisesRegex(AdoptionError,'COMBINED_STOP_REQUIRED'):combined.check_start(self.db,'llm',1000)
    def test_exceeded_case_budget_is_not_hidden_by_zero_model_usage(self):
        self.root['manifest']['budget']['case_trial_executions']=1
        self.reserve('ci','one')
        with self.assertRaisesRegex(AdoptionError,'RESOURCE_LIMIT'):self.reserve('llm','two')


if __name__=='__main__':unittest.main()
