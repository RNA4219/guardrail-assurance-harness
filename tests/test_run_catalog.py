"""履歴参照のページ境界と状態変化時の拒否を実SQLiteで検査する。"""
from pathlib import Path
import hashlib
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from gah.adoption import AdoptionError, AdoptionStore
from gah.evaluation_authority import EvaluationExtension
from gah.wire import canonical_bytes
from gah import run_catalog


class RunCatalogTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.path=Path(self.folder.name)/"catalog.sqlite"
        self.store=AdoptionStore(self.path,clock=lambda:1000,extension=EvaluationExtension())
        raw=canonical_bytes({"contract_id":"scope-contract"}).decode()
        digest=hashlib.sha256(raw.encode()).hexdigest()
        self.scope={"kind":"evaluation_contract","id":"scope-contract","digest":digest}
        # 一覧のmetadata fixture。製品契約採択・run実行の証拠としては使わない。
        self.store._db.execute("INSERT INTO eval_current VALUES(?,?,?,?,?,?)",("series",1,"p","v",raw,digest))
        self.request={"schema_version":1,"action":"run_catalog_list","request_id":"first",
                      "series_id":"series","scope_ref":self.scope,"page_size_count":2,"cursor":None}
        for index in range(5):
            self.add_run(index)

    def tearDown(self):
        self.store.close();self.folder.cleanup()

    def add_run(self,index):
        run_id="run-"+str(index)
        self.store._db.execute("INSERT INTO eval_runs VALUES(?,?,?,?,?,?,?)",
            (run_id,"unread manifest","a"*64,"unread plan","b"*64,"series",1))
        self.store._db.execute("INSERT INTO resource_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id,"a"*64,"unread policy","c"*64,"full",index//2,2000,"operator",0,1500,0,0,None))

    def call(self,request=None,uid=12004):
        return self.store.dispatch(uid,uid,self.request if request is None else request)

    def continuation(self,first):
        return {**self.request,"request_id":"next","cursor":first["next_cursor"]}

    def test_page_is_bounded_sorted_and_does_not_read_bodies(self):
        queries=[];self.store._db.set_trace_callback(queries.append)
        first=self.call();second=self.call(self.continuation(first))
        third=self.call({**self.continuation(second),"request_id":"last"})
        self.assertEqual([item["run_id"] for page in (first,second,third) for item in page["items"]],
                         ["run-"+str(i) for i in range(5)])
        self.assertIsNone(third["next_cursor"])
        self.assertFalse(first["ci_eligible"]);self.assertFalse(first["current_ci_checked"])
        self.assertEqual(first["metadata_rows_materialized"],5)
        self.assertTrue(all(len(page["items"])<=2 for page in (first,second,third)))
        selects=[query.lower() for query in queries if query.lower().startswith("select") and "eval_runs" in query.lower()]
        self.assertTrue(selects)
        self.assertFalse(any("manifest_json" in query or "plan_json" in query or "usage_json" in query for query in selects))

    def test_cursor_rejects_insert_cancel_artifact_change_or_permission_change(self):
        changes=[lambda:self.add_run(5),
                 lambda:self.store._db.execute("UPDATE resource_runs SET cancelled=1 WHERE run_id='run-1'"),
                 lambda:self.store._db.execute("INSERT INTO authority_artifacts VALUES(?,?,?,?,?)",("evidence_tombstone","tomb","d"*64,"unread","run-1")),
                 lambda:self.store._db.execute("UPDATE adoption_meta SET value=value+1 WHERE key='permission_generation'")]
        for index,change in enumerate(changes):
            first=self.call({**self.request,"request_id":"before-"+str(index)})
            change()
            with self.assertRaisesRegex(AdoptionError,"STALE_OR_INVALIDATED"):
                self.call({**self.continuation(first),"request_id":"after-"+str(index)})

    def test_cursor_cannot_cross_actor_size_restart_or_expiry(self):
        first=self.call();following=self.continuation(first)
        for changed,uid in ((following,12001),({**following,"page_size_count":1},12004),
                            ({**following,"cursor":following["cursor"]+"x"},12004)):
            with self.assertRaisesRegex(AdoptionError,"STALE_OR_INVALIDATED"):
                self.call(changed,uid)
        with patch.object(run_catalog,"_SECRET",b"new process key"):
            with self.assertRaisesRegex(AdoptionError,"STALE_OR_INVALIDATED"):
                self.call(following)
        saved=run_catalog._decode(first["next_cursor"])
        with patch.object(run_catalog,"_elapsed",return_value=saved["expires"]):
            with self.assertRaisesRegex(AdoptionError,"STALE_OR_INVALIDATED"):
                self.call(following)

    def test_page_limits_candidate_and_conflicting_replay_are_rejected(self):
        for size in (0,101,True):
            with self.assertRaisesRegex(AdoptionError,"INVALID_REQUEST"):
                self.call({**self.request,"page_size_count":size})
        with self.assertRaisesRegex(AdoptionError,"AUTHORITY_DENIED"):
            self.call(uid=12002)
        first=self.call()
        with self.assertRaisesRegex(AdoptionError,"REQUEST_CONFLICT"):
            self.call({**self.request,"cursor":first["next_cursor"]})

    def test_existing_run_cli_lists_real_store_metadata_without_runner(self):
        import io,json
        from tools.gah_run import list_runs
        store=self.store
        class Runtime:
            def client(self,uid,request):
                return store.dispatch(uid,uid,request)
        stream=io.StringIO()
        self.assertEqual(list_runs(Runtime(),self.request,stream),0)
        result=json.loads(stream.getvalue())
        self.assertFalse(result["ci_eligible"])
        self.assertEqual(len(result["items"]),2)
        self.add_run(6)
        stream=io.StringIO()
        self.assertEqual(list_runs(Runtime(),self.continuation(result),stream),1)
        self.assertEqual(json.loads(stream.getvalue())["reason"],"STALE_OR_INVALIDATED")


if __name__=="__main__":
    unittest.main()
