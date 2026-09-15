"""認証DBの保持・削除・冪等性・CI失効を正規runの保存根拠で検証する。"""
from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import sys
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from tests import test_run_scope as helpers
from gah.adoption import AdoptionError
from gah import resources
from gah.run_contracts import content_ref
request=helpers.request


class RetentionIntegrationTests(unittest.TestCase):
    open=helpers.ScopedRunIntegrationTests.open
    prepare=helpers.ScopedRunIntegrationTests.prepare
    begin=helpers.ScopedRunIntegrationTests.begin
    complete=helpers.ScopedRunIntegrationTests.complete
    scoped=helpers.ScopedRunIntegrationTests.scoped
    gate_request=helpers.ScopedRunIntegrationTests.gate_request
    setUp=helpers.ScopedRunIntegrationTests.setUp

    @classmethod
    def setUpClass(cls):
        helpers.ScopedRunIntegrationTests.setUpClass.__func__(cls)
        helper=cls('runTest');helper.setUp();cls.addClassCleanup(helper.doCleanups)
        _,prepared,_=helper.scoped('retention-source')
        receipt=helper.complete(prepared)
        cls.prepared=prepared;cls.receipt=receipt
        cls.evidence_ref=receipt['evidence_ref']
        target=Path(cls.temp.name)/'retention-seed.sqlite'
        with closing(sqlite3.connect(target)) as db:helper.store._db.backup(db)
        cls.seed_path=target;cls.at=helper.clock.value+1

    def req(self,action,name,**fields):
        return request('evidence_retention_'+action,'retention-'+name,run_id='retention-source',expected_evidence_ref=deepcopy(self.evidence_ref),**fields)

    def state(self):
        return self.store.dispatch(12004,12004,self.req('state','state'))

    def plan(self,name='plan',reason='EXPLICIT_REMOVAL'):
        return self.store.dispatch(12001,12001,self.req('plan',name,expected_hold_ref=self.state()['hold_ref'],reason=reason))

    def apply_request(self,plan):
        return self.req('apply','apply',plan_ref=plan['plan_ref'])

    def test_delete_keeps_original_receipt_but_invalidates_current_ci_and_restart(self):
        before=self.state();gate=self.gate_request(self.prepared)
        self.assertTrue(self.store.dispatch(12004,12004,gate)['ci_eligible'])
        decision=self.store.dispatch(12004,12004,request('run_artifact','retained-decision',run_id='retention-source',
            artifact_ref=self.receipt['decision_ref']))['artifact']
        counts_request=request('run_artifact','retained-counts',run_id='retention-source',
            artifact_ref={'kind':'aggregation','id':'retention-source','digest':decision['aggregate_digest']})
        self.assertFalse(self.store.dispatch(12004,12004,counts_request)['ci_eligible'])
        plan=self.plan();req=self.apply_request(plan)
        for uid in (12001,12002,12003):
            with self.assertRaisesRegex(AdoptionError,'AUTHORITY_DENIED'):self.store.dispatch(uid,uid,req)
        result=self.store.dispatch(12004,12004,req)
        self.assertTrue(result['deleted']);self.assertEqual(self.store.dispatch(12004,12004,req),result)
        with self.assertRaisesRegex(AdoptionError,'EVIDENCE_DELETED'):
            self.store.dispatch(12004,12004,counts_request)
        current=self.state();self.assertTrue(current['deleted']);self.assertEqual(current['metadata'],before['metadata'])
        self.assertEqual(current['saved_assurance'],self.receipt['assurance'])
        self.assertEqual(current['reproduction'],'REPRODUCTION_UNAVAILABLE')
        self.assertEqual(self.store._db.execute("SELECT COUNT(*) FROM authority_artifacts WHERE kind='evidence' AND run_id='retention-source'").fetchone()[0],0)
        rejected=self.store.dispatch(12004,12004,gate)
        self.assertFalse(rejected['ci_eligible']);self.assertIn('EVIDENCE_DELETED',rejected['reasons'])
        with self.assertRaisesRegex(AdoptionError,'EVIDENCE_DELETED'):
            self.store.dispatch(12004,12004,request('evidence_finalize','refinalize',run_id='retention-source'))
        self.store.close();self.store=self.open()
        self.assertEqual(self.state()['tombstone_ref'],current['tombstone_ref'])
        from tools.gah_report import build_report,render_markdown
        report=build_report(helpers.supervisor.Runtime(self.store),gate)
        self.assertFalse(report['ci_eligible']);self.assertFalse(report['artifacts_available'])
        self.assertEqual(report['scope']['control_ids'],before['metadata']['control_ids'])
        self.assertEqual(report['observed_assurance'],self.receipt['assurance'])
        text=render_markdown(report)
        self.assertIn('Evidence削除により指標を再現できない',text)
        self.assertIn('EVIDENCE_DELETED',text)

    def test_hold_cas_invalidates_preexisting_plan_and_requires_new_plan(self):
        before=self.state();plan=self.plan()
        held=self.store.dispatch(12001,12001,self.req('hold','hold',expected_hold_ref=before['hold_ref'],active=True))
        with self.assertRaisesRegex(AdoptionError,'RETENTION_HOLD'):self.plan('held')
        with self.assertRaisesRegex(AdoptionError,'RETENTION_HOLD'):self.store.dispatch(12004,12004,self.apply_request(plan))
        with self.assertRaisesRegex(AdoptionError,'RETENTION_STATE_CONFLICT'):
            self.store.dispatch(12001,12001,self.req('hold','stale-release',expected_hold_ref=before['hold_ref'],active=False))
        self.store.dispatch(12001,12001,self.req('hold','release',expected_hold_ref=held['hold_ref'],active=False))
        with self.assertRaisesRegex(AdoptionError,'RETENTION_PLAN_INVALID'):self.store.dispatch(12004,12004,self.apply_request(plan))
        self.assertFalse(self.state()['deleted'])

    def test_retention_boundary_and_plan_expiry_do_not_delete_early(self):
        until=self.state()['metadata']['retention_until'];self.clock.value=until
        with self.assertRaisesRegex(AdoptionError,'RETENTION_NOT_EXPIRED'):self.plan('boundary','RETENTION_EXPIRED')
        self.clock.value=until+1;plan=self.plan('expired','RETENTION_EXPIRED')
        self.clock.value=plan['plan']['expires_at']+1
        with self.assertRaisesRegex(AdoptionError,'RETENTION_PLAN_INVALID'):self.store.dispatch(12004,12004,self.apply_request(plan))
        self.assertFalse(self.state()['deleted'])
        plan=self.plan('renew','RETENTION_EXPIRED')
        self.assertTrue(self.store.dispatch(12004,12004,self.apply_request(plan))['deleted'])

    def test_storage_failure_rolls_back_delete_and_allows_same_request_retry(self):
        plan=self.plan();req=self.apply_request(plan)
        self.store._db.execute("CREATE TRIGGER reject_delete BEFORE DELETE ON authority_artifacts WHEN OLD.kind='evidence' BEGIN SELECT RAISE(ABORT,'fixed'); END")
        with self.assertRaises(AdoptionError):self.store.dispatch(12004,12004,req)
        self.assertFalse(self.state()['deleted'])
        self.assertEqual(self.store._db.execute("SELECT COUNT(*) FROM authority_artifacts WHERE kind='evidence_tombstone'").fetchone()[0],0)
        self.store._db.execute('DROP TRIGGER reject_delete')
        self.assertTrue(self.store.dispatch(12004,12004,req)['deleted'])

    def test_tombstone_origin_and_restored_content_are_rejected(self):
        row=self.store._db.execute("SELECT * FROM authority_artifacts WHERE kind='evidence' AND run_id='retention-source'").fetchone()
        self.store.dispatch(12004,12004,self.apply_request(self.plan()))
        self.store._db.execute("UPDATE idempotency SET actor_id='candidate' WHERE request_id='retention-apply'")
        with self.assertRaisesRegex(AdoptionError,'RETENTION_ORIGIN_INVALID'):self.state()
        self.store._db.execute("UPDATE idempotency SET actor_id='operator' WHERE request_id='retention-apply'")
        self.store._db.execute('INSERT INTO authority_artifacts VALUES(?,?,?,?,?)',tuple(row))
        with self.assertRaisesRegex(AdoptionError,'EVIDENCE_RESTORED_AFTER_DELETION'):self.state()

    def test_cli_selects_separate_roles_and_errors_never_claim_deletion(self):
        from tools.gah_retention import execute
        runtime=helpers.supervisor.Runtime(self.store)
        state,code=execute(runtime,self.req('state','cli-state'));self.assertEqual(code,0)
        plan,code=execute(runtime,self.req('plan','cli-plan',expected_hold_ref=state['hold_ref'],reason='EXPLICIT_REMOVAL'))
        self.assertEqual(code,0);self.assertEqual(runtime.calls[-1][0],12001)
        result,code=execute(runtime,self.apply_request(plan));self.assertEqual(code,0);self.assertTrue(result['deleted'])
        self.assertEqual(runtime.calls[-1][0],12004)


if __name__=='__main__':unittest.main()
