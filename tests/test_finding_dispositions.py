"""基準改訂・対象廃止を正規採択と結び、修復成功との混同を拒否する。"""
from copy import deepcopy
import unittest
from tests import test_finding_lifecycle as helpers
from gah.adoption import AdoptionError
from gah.run_contracts import content_ref
from gah import target_retirement
request = helpers.request


class FindingDispositionTests(unittest.TestCase):
    open = helpers.FindingLifecycleIntegrationTests.open
    prepare = helpers.FindingLifecycleIntegrationTests.prepare
    begin = helpers.FindingLifecycleIntegrationTests.begin
    complete = helpers.FindingLifecycleIntegrationTests.complete
    scoped = helpers.FindingLifecycleIntegrationTests.scoped
    gate_request = helpers.FindingLifecycleIntegrationTests.gate_request
    setUp = helpers.FindingLifecycleIntegrationTests.setUp
    req = helpers.FindingLifecycleIntegrationTests.req
    current = helpers.FindingLifecycleIntegrationTests.current
    start = helpers.FindingLifecycleIntegrationTests.start
    @classmethod
    def setUpClass(cls): helpers.FindingLifecycleIntegrationTests.setUpClass.__func__(cls)
    def retirement(self):
        return request('target_retire','retire-target',contract_series_id='fixture-contract-series',
            expected_contract_ref=self.contract_ref,target_ref=self.old_target,reason_code='SERVICE_CLOSED')
    def dispose(self,kind,reference):
        return self.req('finding_dispose','dispose',expected_state_ref=self.current()['state_ref'],
            disposition=kind,authority_ref=reference,reason_code='OWNER_DECISION')
    def test_retirement_is_authenticated_and_blocks_target_without_verifying_finding(self):
        prepared = self.prepare('after-retirement')
        req = self.retirement()
        for uid in (12002,12003,12004):
            with self.assertRaisesRegex(AdoptionError,'AUTHORITY_DENIED'): self.store.dispatch(uid,uid,req)
        from tools.gah_target import execute as target_execute
        retired,code = target_execute(helpers.helpers.supervisor.Runtime(self.store),req)
        self.assertEqual(code,0)
        self.assertEqual(self.store.dispatch(12001,12001,req),retired)
        with self.assertRaisesRegex(AdoptionError,'TARGET_RETIRED'): self.begin(prepared)
        self.assertIsNone(self.store._db.execute("SELECT 1 FROM eval_runs WHERE run_id='after-retirement'").fetchone())
        from tools.gah_finding import execute
        runtime=helpers.helpers.supervisor.Runtime(self.store)
        result,code=execute(runtime,self.dispose('TARGET_RETIRED',retired['retirement_ref']))
        self.assertEqual(code,0);self.assertEqual(result['state']['disposition'],'TARGET_RETIRED')
        self.assertEqual(result['state']['status'],'OPEN');self.assertFalse(self.current()['verification_current'])
        with self.assertRaisesRegex(AdoptionError,'FINDING_DISPOSED'):self.start()
        self.store.close();self.store=self.open()
        self.assertEqual(self.current()['state'],result['state'])
        gate=self.store.dispatch(12004,12004,self.gate_request({'bound_run':self.source_bound}))
        self.assertFalse(gate['ci_eligible']);self.assertIn('TARGET_RETIRED',gate['reasons'])
    def test_new_adopted_baseline_is_separate_disposition_and_original_finding_is_kept(self):
        with self.assertRaisesRegex(AdoptionError,'NEW_BASELINE_REQUIRED'):
            self.store.dispatch(12001,12001,self.dispose('BASELINE_REVISED',self.source_bound['manifest']['baseline_ref']))
        prepared=self.prepare('refresh-source');self.complete(prepared)
        baseline_ref=self.source_bound['manifest']['baseline_ref']
        row=self.store._db.execute('SELECT * FROM baseline_adoptions WHERE baseline_digest=?',(baseline_ref['digest'],)).fetchone()
        for uid,action,fields in (
            (12001,'baseline_propose',dict(proposal_id='refresh-proposal',series_id=row['series_id'],run_id='refresh-source',expected_generation=row['generation'])),
            (12003,'baseline_validate',dict(proposal_id='refresh-proposal',validation_id='refresh-validation')),
            (12001,'baseline_adopt',dict(proposal_id='refresh-proposal',validation_id='refresh-validation',expected_generation=row['generation']))):
            self.store.dispatch(uid,uid,request(action,'disposition-'+action,**fields))
        current=self.store.dispatch(12004,12004,request('baseline_current','new-baseline',series_id=row['series_id']))
        record=current['baseline'];ref=content_ref('baseline',record['baseline_id'],record)
        result=self.store.dispatch(12001,12001,self.dispose('BASELINE_REVISED',ref))
        self.assertEqual(result['state']['disposition'],'BASELINE_REVISED');self.assertEqual(result['state']['status'],'OPEN')
        self.assertFalse(self.current()['verification_current'])
        original=self.store.dispatch(12004,12004,request('run_artifact','original-after-revision',run_id='finding-source',artifact_ref=self.finding_ref))
        self.assertEqual(original['artifact'],self.finding)
    def test_retirement_origin_tamper_and_storage_failure_are_not_accepted(self):
        self.store._db.execute("CREATE TRIGGER reject_retire BEFORE INSERT ON authority_artifacts WHEN NEW.kind='target_retirement' BEGIN SELECT RAISE(ABORT,'synthetic'); END")
        with self.assertRaises(AdoptionError):self.store.dispatch(12001,12001,self.retirement())
        self.assertIsNone(self.store._db.execute("SELECT 1 FROM idempotency WHERE request_id='retire-target'").fetchone())
        self.store._db.execute('DROP TRIGGER reject_retire')
        retired=self.store.dispatch(12001,12001,self.retirement())
        self.store._db.execute("UPDATE idempotency SET actor_id='operator' WHERE request_id='retire-target'")
        with self.assertRaisesRegex(AdoptionError,'RETIREMENT_ORIGIN_INVALID'):
            target_retirement.resolve(self.store._db,retired['retirement_ref'],self.clock.value)
    def test_disposition_storage_failure_rolls_back_the_finding_event(self):
        retired=self.store.dispatch(12001,12001,self.retirement());before=self.current()
        self.store._db.execute("CREATE TRIGGER reject_disposition BEFORE INSERT ON authority_artifacts WHEN NEW.kind='finding_management_event' BEGIN SELECT RAISE(ABORT,'synthetic'); END")
        req=self.dispose('TARGET_RETIRED',retired['retirement_ref'])
        with self.assertRaises(AdoptionError):self.store.dispatch(12001,12001,req)
        self.assertEqual(self.current()['state'],before['state'])
        self.assertIsNone(self.store._db.execute("SELECT 1 FROM idempotency WHERE request_id='finding-dispose'").fetchone())


if __name__=='__main__':unittest.main()
