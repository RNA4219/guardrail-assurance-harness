"""明示schema-v5拡張の実AdoptionStore接続を親が検証する。Dockerは起動しない。"""
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from gah.adoption import AdoptionStore, AdoptionError
from gah.evaluation_authority import EvaluationExtension
from gah.partitioned_authority import PartitionedEvaluationExtension
from gah import partitioned_authority as authority
from gah import partitioned_plan_store as storage
from gah.partitioned_trial_plan import partition_trial_plan
from gah.policy import initial_policy_profile
from gah.run_contracts import content_ref
from gah.wire import canonical_bytes
from tests.test_fixture_admission import Clock, request


class PartitionedAuthorityTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "authority.sqlite"
        self.clock = Clock()
        self.policy = initial_policy_profile()

    def open(self, extension=None):
        return AdoptionStore(self.path, clock=self.clock,
            bootstrap_policy=self.policy, extension=extension or PartitionedEvaluationExtension())

    def seed(self, store):
        def call(uid, action, **fields):
            return store.dispatch(uid, uid, request(action, "seed-"+action, **fields))
        call(12001, "propose", proposal_id="policy-proposal", series_id=self.policy["policy_id"], expected_generation=0, policy=self.policy)
        call(12003, "validate", proposal_id="policy-proposal", validation_id="policy-validation")
        call(12001, "adopt", proposal_id="policy-proposal", validation_id="policy-validation", expected_generation=0)
        response = call(12001, "fixture_prepare", run_id="partition-fixture", policy_series_id=self.policy["policy_id"])
        self.bound = response["prepared"]["bound_run"]
        call(12001, "contract_propose", proposal_id="contract-proposal", series_id="partition-series", expected_generation=0, contract=self.bound["contract"])
        call(12003, "contract_validate", proposal_id="contract-proposal", validation_id="contract-validation")
        call(12001, "contract_adopt", proposal_id="contract-proposal", validation_id="contract-validation", expected_generation=0)
        self.index, self.segments = partition_trial_plan(self.bound["plan"])
        self.plan_ref = content_ref("trial_plan_index", self.index["plan_id"], self.index)

    def begin(self, store):
        return store.dispatch(12001,12001,request("plan_partition_begin","upload-begin", upload_id="upload", contract_series_id="partition-series", expected_contract_generation=1, index=self.index))

    def put(self, store):
        return [store.dispatch(12001,12001,request("plan_partition_put_segment","put-"+str(i),upload_id="upload",segment=s)) for i,s in enumerate(self.segments)]

    def test_default_v4_store_has_no_partition_tables_or_implicit_migration(self):
        with self.open(EvaluationExtension()) as store:
            self.assertEqual(store._db.execute("PRAGMA user_version").fetchone()[0],4)
            names={r[0] for r in store._db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertFalse(set(storage.TABLES)&names)
        with self.assertRaises(AdoptionError):
            self.open()
        with self.open(EvaluationExtension()) as store:
            self.assertEqual(store._db.execute("PRAGMA user_version").fetchone()[0],4)

    def test_real_adoption_upload_restart_replay_commit_and_fresh_read(self):
        with self.open() as store:
            self.seed(store); self.begin(store); first=self.put(store)
            with self.assertRaisesRegex(AdoptionError,"^PLAN_MISSING$"):
                store.dispatch(12004,12004,request("plan_partition_read","read-incomplete",plan_ref=self.plan_ref,segment_index=None))
        with self.open() as store:
            self.assertEqual(self.put(store),first)
            status=store.dispatch(12001,12001,request("plan_partition_status","status",upload_id="upload"))
            self.assertEqual(status["missing_segments"],[])
            committed=store.dispatch(12001,12001,request("plan_partition_commit","commit",upload_id="upload"))
            self.assertEqual(committed["plan_ref"],self.plan_ref)
            self.assertFalse(committed["ci_eligible"])
        with self.open() as store:
            value=store.dispatch(12004,12004,request("plan_partition_read","read",plan_ref=self.plan_ref,segment_index=None))
            self.assertEqual(value["index"],self.index)
            for i,s in enumerate(self.segments):
                value=store.dispatch(12003,12003,request("plan_partition_read","read-segment-"+str(i),plan_ref=self.plan_ref,segment_index=i))
                self.assertEqual(value["segment"],s)
            store.dispatch(12004,12004,request("revoke_actor","revoke-manager",actor_id="manager"))
            with self.assertRaises(AdoptionError):
                store.dispatch(12004,12004,request("plan_partition_read","read",plan_ref=self.plan_ref,segment_index=None))

    def test_os_identity_role_gate_precedes_storage_handler(self):
        with self.open() as store:
            value=request("plan_partition_status","status",upload_id="upload")
            for uid in (12002,12003,12004):
                with self.subTest(uid=uid), self.assertRaisesRegex(AdoptionError,"^AUTHORITY_DENIED$"):
                    store.dispatch(uid,uid,value)
            with self.assertRaisesRegex(AdoptionError,"^AUTHORITY_MISSING$"):
                store.dispatch(999,999,value)

    def test_source_change_after_write_rolls_back_upload(self):
        with self.open() as store:
            self.seed(store)
            digest=store._extension_digest
            changed=False
            original=storage.handle
            def changed_handle(*args,**kwargs):
                nonlocal changed
                value=original(*args,**kwargs); changed=True
                return value
            with patch.object(authority,"source_digest",side_effect=lambda: "f"*64 if changed else digest), patch.object(storage,"handle",side_effect=changed_handle):
                with self.assertRaisesRegex(AdoptionError,"^EXTENSION_INVALID$"):
                    self.begin(store)
            self.assertEqual(store._db.execute("SELECT COUNT(*) FROM partition_plan_upload").fetchone()[0],0)
            self.assertEqual(store._db.execute("SELECT COUNT(*) FROM idempotency WHERE request_id='upload-begin'").fetchone()[0],0)

    def test_multiple_segments_use_bounded_messages_and_publish_only_after_complete(self):
        with self.open() as store:
            self.seed(store)
            # 保存形式の負荷fixture。runへのcase coverage/admission証拠ではない。
            plan=deepcopy(self.bound["plan"])
            template=plan["entries"]
            plan["entries"]=[]
            for i in range(3200):
                entry=deepcopy(template[i % len(template)])
                entry["trial_id"]="storage-trial-"+str(i)
                plan["entries"].append(entry)
            self.assertGreater(len(canonical_bytes(plan)),1048576)
            self.index,self.segments=partition_trial_plan(plan)
            self.assertGreater(len(self.segments),1)
            self.plan_ref=content_ref("trial_plan_index",self.index["plan_id"],self.index)
            self.begin(store)
            status_request=request("plan_partition_status","fresh-status",upload_id="upload")
            initial=store.dispatch(12001,12001,status_request)
            self.assertEqual(initial["present_segments"],[])
            for i,segment in enumerate(self.segments):
                value=request("plan_partition_put_segment","large-put-"+str(i),upload_id="upload",segment=segment)
                self.assertLessEqual(len(canonical_bytes(segment)),900000)
                self.assertLessEqual(len(canonical_bytes(value)),1048576)
                store.dispatch(12001,12001,value)
                if i == 0:
                    with self.assertRaisesRegex(AdoptionError,"^SEGMENTS_INCOMPLETE$"):
                        store.dispatch(12001,12001,request("plan_partition_commit","incomplete-commit",upload_id="upload"))
                    self.assertEqual(store._db.execute("SELECT COUNT(*) FROM partition_plan_commits").fetchone()[0],0)
            updated=store.dispatch(12001,12001,status_request)
            self.assertEqual(updated["present_segments"],list(range(len(self.segments))))
            committed=store.dispatch(12001,12001,request("plan_partition_commit","large-commit",upload_id="upload"))
            self.assertEqual(committed["entry_count"],3200)
            for i,segment in enumerate(self.segments):
                value=store.dispatch(12004,12004,request("plan_partition_read","large-read-"+str(i),plan_ref=self.plan_ref,segment_index=i))
                self.assertEqual(value["segment"],segment)
                self.assertLessEqual(len(canonical_bytes(value)),1048576)
                self.assertFalse(value["ci_eligible"])

    def test_v2_run_manifest_remains_rejected(self):
        with self.open() as store:
            self.seed(store)
            manifest=deepcopy(self.bound["manifest"])
            manifest["schema_version"]=2
            manifest["plan_ref"]=self.plan_ref
            with self.assertRaises(AdoptionError):
                store.dispatch(12004,12004,request("run_begin","v2-run",manifest=manifest,plan=self.bound["plan"],contract_series_id="partition-series"))
            self.assertEqual(store._db.execute("SELECT COUNT(*) FROM eval_runs").fetchone()[0],0)


if __name__ == "__main__":
    unittest.main()
