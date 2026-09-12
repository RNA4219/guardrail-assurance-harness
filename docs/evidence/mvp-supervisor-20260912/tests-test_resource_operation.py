"""操作状態の部品検査。実Dockerの認証検証は別証跡。"""
from pathlib import Path
import importlib.util
import sqlite3
import sys
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from gah import resource_operation as candidate
from gah.resources import ResourceBook, ResourceError, create_schema
from gah.policy import initial_policy_profile
from gah.resource_authority import BILLING_REF
ZERO={"input_tokens":0,"output_tokens":0,"cost_usd":"0"}


class OperationCandidateTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(":memory:",isolation_level=None)
        self.db.row_factory=sqlite3.Row
        self.addCleanup(self.db.close)
        self.book=ResourceBook(self.db)
        self.db.execute("BEGIN IMMEDIATE");create_schema(self.db);self.db.commit()
        self.tx(self.book.create_run,"sample-run","a"*64,initial_policy_profile(),"pr","owner",100,1300)
        self.reservation={"case_trial_executions":1,"model_calls":0,"input_tokens":0,"output_tokens":0,
            "api_cost_usd_micros":0,"billing_ref":BILLING_REF,"billing_mode":"non_billed_local"}
        self.tx(self.book.reserve,"sample-run","sample-op","owner",1,self.reservation,101)
        self.request={"schema_version":1,"action":"resource_operation","request_id":"read-op",
            "run_id":"sample-run","operation_id":"sample-op",
            "expected_manifest_ref":{"kind":"run_manifest","id":"sample-run","digest":"a"*64}}

    def tx(self,fn,*args,**kwargs):
        self.db.execute("BEGIN IMMEDIATE")
        try: result=fn(*args,**kwargs)
        except BaseException: self.db.rollback();raise
        else: self.db.commit();return result

    def read(self,now=102,**fields):
        return self.tx(candidate.inspect_operation,self.db,{**self.request,**fields},now)

    def dispatch(self):
        self.tx(self.book.dispatch_intent,"sample-run","sample-op","owner",1,102)

    def test_reserved_status_is_not_send_permission(self):
        value=self.read()
        self.assertFalse(value["dispatch_intended"])
        self.assertFalse(value["stopped"])
        self.assertFalse(value["settled"])
        self.assertIsNone(value["usage"])
        self.assertIsNone(value["cost_usd_micros"])
        self.assertNotIn("dispatch_allowed",value)
        self.assertFalse(value["ci_eligible"])

    def test_stop_and_usage_remain_separate_and_read_recomputes(self):
        self.dispatch()
        self.assertTrue(self.read()["dispatch_intended"])
        self.tx(self.book.observe,"sample-run","sample-op","stop",stopped=True,usage=None,now=103)
        value=self.read(103)
        self.assertTrue(value["stopped"]);self.assertFalse(value["settled"])
        self.assertIsNone(value["cost_usd_micros"])
        self.tx(self.book.observe,"sample-run","sample-op","usage",stopped=True,usage=ZERO,now=104)
        value=self.read(104)
        self.assertTrue(value["settled"]);self.assertEqual(value["cost_usd_micros"],0)
        self.tx(self.book.close,"sample-run","owner",1,105)
        self.assertTrue(self.read(1400)["settled"])

    def test_cancel_releases_only_unsent_and_read_does_not_need_owner(self):
        self.tx(self.book.cancel,"sample-run","owner",1,102)
        value=self.read(200)
        self.assertTrue(value["released"]);self.assertFalse(value["dispatch_intended"])
        self.assertEqual(value["owner_epoch"],1)

    def test_wrong_reference_and_cross_run_fail(self):
        with self.assertRaisesRegex(ResourceError,"^BINDING_MISMATCH$"):
            self.read(expected_manifest_ref={**self.request["expected_manifest_ref"],"digest":"b"*64})
        with self.assertRaisesRegex(ResourceError,"^OPERATION_MISSING$"):
            self.read(operation_id="other-op")
        self.tx(self.book.create_run,"other-run","a"*64,initial_policy_profile(),"pr","other-owner",102,1300)
        with self.assertRaisesRegex(ResourceError,"^OPERATION_MISSING$"):
            self.read(run_id="other-run",expected_manifest_ref={**self.request["expected_manifest_ref"],"id":"other-run"})

    def test_corrupt_usage_and_flags_fail_without_accounting_change(self):
        self.dispatch()
        self.db.execute("UPDATE resource_operations SET stopped_at=101 WHERE operation_id='sample-op'")
        with self.assertRaisesRegex(ResourceError,"^STORAGE_CORRUPT$"):
            self.read()
        self.db.execute("UPDATE resource_operations SET stopped_at=NULL,usage_json='{}' WHERE operation_id='sample-op'")
        with self.assertRaisesRegex(ResourceError,"^STORAGE_CORRUPT$"):
            self.read()
        self.assertIsNone(self.db.execute("SELECT settled_at FROM resource_operations").fetchone()[0])
        self.db.execute("UPDATE resource_operations SET usage_json=NULL,released=2 WHERE operation_id='sample-op'")
        with self.assertRaisesRegex(ResourceError,"^STORAGE_CORRUPT$"):
            self.read()

    def test_return_mutation_and_unknown_inputs_cannot_change_state(self):
        value=self.read();value["reservation"]["model_calls"]=10
        self.assertEqual(self.read()["reservation"]["model_calls"],0)
        for fields in ({"schema_version":True},{"owner_epoch":1},{"operation_id":True},
                {"expected_manifest_ref":{**self.request["expected_manifest_ref"],"kind":"baseline"}}):
            with self.subTest(fields=fields),self.assertRaises(ResourceError):
                self.read(**fields)

    def test_transaction_and_clock_are_required(self):
        with self.assertRaisesRegex(ResourceError,"^TRANSACTION_REQUIRED$"):
            candidate.inspect_operation(self.db,self.request,102)
        self.read(104)
        with self.assertRaisesRegex(ResourceError,"^CLOCK_ROLLBACK$"):
            self.read(103)


if __name__=="__main__":unittest.main(verbosity=2)
