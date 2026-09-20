"""保持wrapperの再配送を、既存の正規run fixtureと実authority DBで確認する。"""
from pathlib import Path
import json
import tempfile
import unittest
from tests import test_evidence_retention as existing
from gah.operations_retention import execute


class OperationsRetentionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        existing.RetentionIntegrationTests.setUpClass()
        cls.addClassCleanup(existing.RetentionIntegrationTests.doClassCleanups)

    def setUp(self):
        self.fixture=existing.RetentionIntegrationTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.folder=tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        fixture=self.fixture
        class Runtime:
            lost_once=False
            calls=0
            def client(inner,uid,request):
                inner.calls+=1
                value=fixture.store.dispatch(uid,uid,request)
                if inner.lost_once:
                    inner.lost_once=False
                    raise OSError('response lost after commit')
                return value
        self.runtime=Runtime()

    def operation(self,request,command):
        return execute(self.folder.name,self.runtime,request,command,
                       authenticated_principal='test-os-principal',clock=self.fixture.clock)

    def plan(self):
        request=self.fixture.req('plan','wrapper-plan',expected_hold_ref=self.fixture.state()['hold_ref'],reason='EXPLICIT_REMOVAL')
        result=self.operation(request,'ops.retention.plan')
        self.assertEqual(result['operation_status'],'COMPLETED',result)
        files=list((Path(self.folder.name)/'.ga/operations/retention').glob('*.json'))
        self.assertEqual(len(files),1)
        return request,result,json.loads(files[0].read_text(encoding='utf-8'))['authority_result']

    def test_plan_and_apply_use_existing_authority_and_replay_without_second_effect(self):
        request,result,plan=self.plan()
        self.assertFalse(result['ci_eligible'])
        calls=self.runtime.calls
        self.assertEqual(self.operation(request,'ops.retention.plan'),result)
        self.assertEqual(self.runtime.calls,calls)
        apply=self.fixture.apply_request(plan)
        deleted=self.operation(apply,'ops.retention.apply')
        self.assertEqual(deleted['operation_status'],'COMPLETED',deleted)
        self.assertTrue(self.fixture.state()['deleted'])
        self.assertEqual(self.operation(apply,'ops.retention.apply'),deleted)
        self.assertFalse(deleted['ci_eligible'])

    def test_lost_apply_response_recovers_same_commit_and_preserves_original_receipt(self):
        _,_,plan=self.plan()
        apply=self.fixture.apply_request(plan)
        self.runtime.lost_once=True
        first=self.operation(apply,'ops.retention.apply')
        self.assertEqual(first['operation_status'],'INCOMPLETE')
        self.assertTrue(self.fixture.state()['deleted'])
        second=self.operation(apply,'ops.retention.apply')
        self.assertEqual(second['operation_status'],'COMPLETED',second)
        count=self.fixture.store._db.execute("SELECT COUNT(*) FROM authority_artifacts WHERE kind='evidence_tombstone' AND run_id='retention-source'").fetchone()[0]
        self.assertEqual(count,1)
        row=self.fixture.store._db.execute("SELECT digest FROM authority_run_receipts WHERE run_id='retention-source'").fetchone()
        self.assertIsNotNone(row)

    def test_different_input_same_operation_id_is_rejected_before_remote_call(self):
        request,_,_=self.plan()
        calls=self.runtime.calls
        result=self.operation({**request,'reason':'RETENTION_EXPIRED'},'ops.retention.plan')
        self.assertEqual(result['operation_status'],'REJECTED')
        self.assertEqual(result['reasons'],['IDEMPOTENCY_CONFLICT'])
        self.assertEqual(self.runtime.calls,calls)


if __name__=='__main__':
    unittest.main()
