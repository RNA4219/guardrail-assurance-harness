"""実SQLiteの通常run・保存ERROR・独立審査・現在CI・削除を接続する。"""
from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import unittest
from tests import test_regression_integration as helpers
from tests.test_supervised_run import Runtime
from gah.adoption import AdoptionError
from gah.run_contracts import content_ref
from tools.gah_mutation_review import execute
from tools.gah_report import build_report,render_markdown


class MutationReviewIntegrationTests(unittest.TestCase):
    open=helpers.RegressionIntegrationTests.open
    setUp=helpers.RegressionIntegrationTests.setUp
    prepare=helpers.RegressionIntegrationTests.prepare
    begin=helpers.RegressionIntegrationTests.begin
    complete=helpers.RegressionIntegrationTests.complete
    gate_request=helpers.RegressionIntegrationTests.gate_request
    @classmethod
    def setUpClass(cls):
        helpers.RegressionIntegrationTests.setUpClass.__func__(cls)
        helper=cls('runTest');helper.setUp();cls.addClassCleanup(helper.doCleanups)
        selected=[]
        def inject(record,observations):
            if record['variant']=='candidate' and record['scenario'].startswith('mutation:') and not selected:
                selected.append(record['case_id']);observations={**observations,'mutation_applied':False,'detected':False}
            return observations
        cls.prepared=helper.prepare('mutation-review-source');cls.receipt=helper.complete(cls.prepared,mutate=inject)
        cls.evidence_ref=cls.receipt['evidence_ref'];assert len(selected)==1
        matches=[]
        for row in helper.store._db.execute("SELECT attempt_id,attempt_json FROM attempts WHERE run_id='mutation-review-source'"):
            attempt=json.loads(row['attempt_json'])
            if attempt['variant']=='candidate' and attempt['expected_binding']['case_id']==selected[0]:matches.append(row['attempt_id'])
        assert len(matches)==1;cls.attempt_id=matches[0]
        target=Path(cls.temp.name)/'mutation-review-seed.sqlite'
        with closing(sqlite3.connect(target)) as db:helper.store._db.backup(db)
        cls.seed_path=target;cls.at=helper.clock.value+1
    def request(self,action,name,**fields):
        return helpers.request('mutation_review_'+action,name,run_id='mutation-review-source',
            expected_evidence_ref=deepcopy(self.evidence_ref),**fields)
    def validate(self):
        return execute(Runtime(self.store),self.request('validate','review-validate',attempt_id=self.attempt_id,
            rationale='固定入力で前提成立とMutation不成立を確認'))[0]
    def approve(self,value):return execute(Runtime(self.store),self.request('approve','review-approve',validation_ref=value['validation_ref']))[0]
    def current(self):return execute(Runtime(self.store),self.request('current','review-current'))[0]

    def test_real_error_review_preserves_original_receipt_missing_obligations_and_ci(self):
        gate=self.gate_request(self.prepared);before=self.store.dispatch(12004,12004,gate)
        self.assertFalse(before['ci_eligible'])
        original=self.store._db.execute("SELECT payload_json FROM authority_run_receipts WHERE run_id='mutation-review-source'").fetchone()[0]
        validation=self.validate();self.assertEqual(self.current()['counts']['candidate']['pending_exclusions'],1)
        approval=self.approve(validation);current=self.current()
        self.assertEqual(current['counts']['candidate']['approved_exclusions'],1)
        self.assertFalse(current['ci_eligible']);self.assertEqual(current['items'][0]['approval'],approval['approval'])
        self.assertEqual(self.store.dispatch(12004,12004,gate),before)
        self.assertEqual(self.store._db.execute("SELECT payload_json FROM authority_run_receipts WHERE run_id='mutation-review-source'").fetchone()[0],original)
        report=build_report(Runtime(self.store),gate);self.assertFalse(report['ci_eligible'])
        counts=report['measurements']['reviewed_classification']['candidate']
        self.assertEqual(counts,{'mutation_errors_observed':1,'excluded':1,'exclusion_pending':0,'remaining_mutation_errors':0})
        text=render_markdown(report);self.assertIn('INVALID_MUTATION_NOT_APPLIED',text)
        self.assertIn('必須欠損',text);self.assertIn('manager',text)
        self.store.close();self.store=self.open()
        self.assertEqual(self.current(),current)
        self.assertEqual(build_report(Runtime(self.store),gate),report)

    def test_one_approved_exclusion_and_one_error_remain_separate_in_the_same_run(self):
        selected=[]
        def inject(record,observations):
            if record['variant']=='candidate' and record['scenario'].startswith('mutation:') and len(selected)<2:
                selected.append(record['case_id']);return {**observations,'mutation_applied':False,'detected':False}
            return observations
        prepared=self.prepare('two-mutation-errors');receipt=self.complete(prepared,mutate=inject)
        self.assertEqual(len(selected),2)
        attempts=[json.loads(row[0]) for row in self.store._db.execute("SELECT attempt_json FROM attempts WHERE run_id='two-mutation-errors'")]
        chosen=next(a for a in attempts if a['variant']=='candidate' and a['expected_binding']['case_id']==selected[0])
        base={'run_id':'two-mutation-errors','expected_evidence_ref':receipt['evidence_ref']}
        validation,code=execute(Runtime(self.store),helpers.request('mutation_review_validate','two-errors-validate',**base,
            attempt_id=chosen['attempt_id'],rationale='一件だけを独立審査し、もう一件のERRORを残す'))
        self.assertEqual(code,0)
        _,code=execute(Runtime(self.store),helpers.request('mutation_review_approve','two-errors-approve',**base,validation_ref=validation['validation_ref']))
        self.assertEqual(code,0)
        report=build_report(Runtime(self.store),self.gate_request(prepared))
        self.assertEqual(report['measurements']['reviewed_classification']['candidate'],
            {'mutation_errors_observed':2,'excluded':1,'exclusion_pending':0,'remaining_mutation_errors':1})
        self.assertEqual(report['measurements']['counts']['variant']['candidate']['mutation_error'],2)
        self.assertFalse(report['ci_eligible'])

    def test_revocation_disables_review_and_keeps_prior_approval(self):
        value=self.validate();approval=self.approve(value)
        self.store.dispatch(12004,12004,helpers.request('evidence_revoke','review-revoke',run_id='mutation-review-source'))
        current=self.current();self.assertEqual(current['counts']['candidate']['approved_exclusions'],0)
        self.assertEqual(current['counts']['candidate']['pending_exclusions'],1)
        self.assertEqual(current['items'][0]['approval'],approval['approval'])
        with self.assertRaises(AdoptionError):
            self.store.dispatch(12003,12003,self.request('validate','after-revoke',attempt_id=self.attempt_id,rationale='再確認'))
        report=build_report(Runtime(self.store),self.gate_request(self.prepared))
        self.assertFalse(report['ci_eligible']);self.assertEqual(report['measurements']['reviewed_classification']['candidate']['remaining_mutation_errors'],1)

    def test_deleted_evidence_cannot_authorize_review_or_restore_counts(self):
        self.approve(self.validate());fields={'run_id':'mutation-review-source','expected_evidence_ref':self.evidence_ref}
        state=self.store.dispatch(12004,12004,helpers.request('evidence_retention_state','review-retention',**fields))
        plan=self.store.dispatch(12001,12001,helpers.request('evidence_retention_plan','review-delete-plan',**fields,
            expected_hold_ref=state['hold_ref'],reason='EXPLICIT_REMOVAL'))
        self.store.dispatch(12004,12004,helpers.request('evidence_retention_apply','review-delete',**fields,plan_ref=plan['plan_ref']))
        with self.assertRaisesRegex(AdoptionError,'EVIDENCE_DELETED'):
            self.store.dispatch(12004,12004,self.request('current','after-delete'))
        report=build_report(Runtime(self.store),self.gate_request(self.prepared))
        self.assertFalse(report['ci_eligible']);self.assertFalse(report['artifacts_available'])
        self.assertNotIn('measurements',report)

    def test_rehashed_changed_review_and_unchanged_origin_are_rejected(self):
        value=self.validate();self.approve(value)
        changed=deepcopy(value['validation']);changed['request']['rationale']='変更された説明'
        ref=content_ref(changed['kind'],changed['review_id'],changed)
        from gah.wire import canonical_bytes
        self.store._db.execute("UPDATE authority_artifacts SET payload_json=?,digest=? WHERE kind=? AND id=?",
            (canonical_bytes(changed).decode(),ref['digest'],changed['kind'],changed['review_id']))
        with self.assertRaises(AdoptionError):self.store.dispatch(12004,12004,self.request('current','changed-review'))


if __name__=='__main__':unittest.main()
