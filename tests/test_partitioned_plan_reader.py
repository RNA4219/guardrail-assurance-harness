"""実AdoptionStore上のcommitted partition plan readerを検査する。"""
from copy import deepcopy
from pathlib import Path
import sqlite3
import json
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gah.adoption import AdoptionError
from gah import partitioned_authority, partitioned_plan_store
from gah.run_contracts import content_ref


def make_fixture(testcase, *, large=False):
    # 避けるべき重複テスト登録をせず、既存実採択fixtureのsetup/seedだけを借用する。
    from tests.test_partitioned_authority import PartitionedAuthorityTests
    helper = PartitionedAuthorityTests("test_real_adoption_upload_restart_replay_commit_and_fresh_read")
    helper.setUp()
    testcase.addCleanup(helper.doCleanups)
    store = helper.open()
    helper.seed(store)
    if large:
        from gah.partitioned_trial_plan import partition_trial_plan
        from gah.wire import canonical_bytes
        plan = deepcopy(helper.bound["plan"])
        template = plan["entries"]
        plan["plan_id"] = "reader-large-plan"
        plan["entries"] = []
        for i in range(3200):
            entry = deepcopy(template[i % len(template)])
            entry["trial_id"] = "reader-trial-" + str(i)
            plan["entries"].append(entry)
        if len(canonical_bytes(plan)) <= 1_048_576:
            raise AssertionError("large fixture must exceed inline limit")
        helper.index, helper.segments = partition_trial_plan(plan)
        helper.plan_ref = content_ref("trial_plan_index", helper.index["plan_id"], helper.index)
    helper.begin(store)
    helper.put(store)
    committed = store.dispatch(12001, 12001, {"schema_version": 1, "action": "plan_partition_commit", "request_id": "reader-commit", "upload_id": "upload"})
    if committed["plan_ref"] != helper.plan_ref:
        raise AssertionError("commit reference mismatch")
    store.close()
    reopened = helper.open()
    testcase.addCleanup(reopened.close)
    return helper, reopened


def read_plan(store, plan_ref, *, series="partition-series", generation=1, now=1000, db=None):
    db = store._db if db is None else db
    return partitioned_plan_store.load_verified_committed_plan(
        store._extension, store, db, plan_ref, contract_series_id=series,
        expected_generation=generation, now=now)


def snapshot(db):
    names = ("partition_plan_commits", "partition_plan_segments", "partition_plan_upload")
    return {name: tuple(tuple(row) for row in db.execute("SELECT * FROM " + name + " ORDER BY 1,2")) for name in names}


class PartitionedPlanReaderTests(unittest.TestCase):
    def transact(self, store):
        store._db.execute("BEGIN")
        self.addCleanup(lambda: store._db.rollback() if store._db is not None and store._db.in_transaction else None)

    def test_large_plan_restores_once_and_results_are_isolated_after_reopen(self):
        helper, store = make_fixture(self, large=True)
        before = snapshot(store._db)
        self.transact(store)
        original_restore = partitioned_plan_store.restore_trial_plan
        with patch.object(partitioned_plan_store, "restore_trial_plan", wraps=original_restore) as restore:
            first = read_plan(store, helper.plan_ref)
            self.assertEqual(restore.call_count, 1)
        self.assertGreater(len(first["plan"]["entries"]), 3000)
        self.assertEqual(first["index"], helper.index)
        self.assertEqual(first["segments"], helper.segments)
        expected_trial_id = first["plan"]["entries"][0]["trial_id"]
        first["plan"]["entries"][0]["trial_id"] = "mutated"
        first["segments"][0]["entries"].clear()
        second = read_plan(store, helper.plan_ref)
        self.assertEqual(second["plan"]["entries"][0]["trial_id"], expected_trial_id)
        self.assertTrue(second["segments"][0]["entries"])
        self.assertEqual(snapshot(store._db), before)

    def test_transaction_connection_extension_and_bindings_are_mandatory(self):
        helper, store = make_fixture(self)
        with self.assertRaisesRegex(AdoptionError, "^TRANSACTION_REQUIRED$"):
            read_plan(store, helper.plan_ref)
        self.transact(store)
        other = sqlite3.connect(str(Path(store._db.execute("PRAGMA database_list").fetchone()[2])))
        try:
            other.execute("BEGIN")
            with self.assertRaisesRegex(AdoptionError, "^STORE_MISMATCH$"):
                read_plan(store, helper.plan_ref, db=other)
            other.rollback()
        finally:
            other.close()
        with self.assertRaisesRegex(AdoptionError, "^EXTENSION_INVALID$"):
            partitioned_plan_store.load_verified_committed_plan(object(), store, store._db, helper.plan_ref, contract_series_id="partition-series", expected_generation=1, now=1000)
        with self.assertRaisesRegex(AdoptionError, "^PLAN_STALE$"):
            read_plan(store, helper.plan_ref, generation=2)
        with self.assertRaisesRegex(AdoptionError, "^PLAN_STALE$"):
            read_plan(store, helper.plan_ref, series="other-series")
        bad_ref = dict(helper.plan_ref, digest="f" * 64)
        with self.assertRaisesRegex(AdoptionError, "^REFERENCE_MISMATCH$"):
            read_plan(store, bad_ref)
        for changes in ({"expected_generation": True}, {"now": True}, {"contract_series_id": ""}):
            args = {"contract_series_id": "partition-series", "expected_generation": 1, "now": 1000}
            args.update(changes)
            with self.assertRaises(AdoptionError):
                partitioned_plan_store.load_verified_committed_plan(store._extension, store, store._db, helper.plan_ref, **args)

    def test_permission_source_and_segment_corruption_fail_closed(self):
        helper, store = make_fixture(self)
        self.transact(store)
        # Simulate permission-generation advancement without revoking a required role.
        store._db.execute("UPDATE adoption_meta SET value=CAST(value AS INTEGER)+1 WHERE key='permission_generation'")
        with self.assertRaisesRegex(AdoptionError, "^PREREQUISITE_UNAVAILABLE$"):
            read_plan(store, helper.plan_ref)

        # Restore a clean fixture for independent source and segment corruption checks.
        helper2, store2 = make_fixture(self)
        self.transact(store2)
        with patch.object(partitioned_authority, "source_digest", return_value="f" * 64):
            with self.assertRaisesRegex(AdoptionError, "^EXTENSION_INVALID$"):
                read_plan(store2, helper2.plan_ref)
        pinned = store2._extension_digest
        calls = iter((pinned, pinned, "f" * 64))
        with patch.object(partitioned_authority, "source_digest", side_effect=lambda: next(calls)):
            with self.assertRaisesRegex(AdoptionError, "^EXTENSION_INVALID$"):
                read_plan(store2, helper2.plan_ref)
        store2._db.execute("UPDATE partition_plan_segments SET segment_json=segment_json || ' '")
        with self.assertRaisesRegex(AdoptionError, "^STORAGE_CORRUPT$"):
            read_plan(store2, helper2.plan_ref)


    def test_persisted_json_recursion_is_storage_corrupt_and_readonly(self):
        helper, store = make_fixture(self)
        self.transact(store)
        db=store._db
        before=snapshot(db)
        meta_before=tuple(tuple(row) for row in db.execute("SELECT key,value FROM adoption_meta ORDER BY key"))
        changes_before=db.total_changes
        self.assertTrue(db.in_transaction)
        write_actions={sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE,sqlite3.SQLITE_DELETE}
        def readonly_authorizer(action, _arg1, _arg2, _database, _source):
            return sqlite3.SQLITE_DENY if action in write_actions else sqlite3.SQLITE_OK
        db.set_authorizer(readonly_authorizer)
        original=json.loads
        try:
            self.assertEqual(read_plan(store, helper.plan_ref)["index"], helper.index)
            self.assertTrue(db.in_transaction)
            self.assertEqual(db.total_changes, changes_before)
            targets=(
                db.execute("SELECT index_json FROM partition_plan_commits WHERE plan_id=?",(helper.index["plan_id"],)).fetchone()[0],
                db.execute("SELECT segment_json FROM partition_plan_segments WHERE plan_id=? ORDER BY segment_index LIMIT 1",(helper.index["plan_id"],)).fetchone()[0],
                db.execute("SELECT contract_ref_json FROM partition_plan_commits WHERE plan_id=?",(helper.index["plan_id"],)).fetchone()[0],
            )
            for target in targets:
                def injected(value,*args,**kwargs):
                    if value==target:raise RecursionError('injected persisted payload decoder failure')
                    return original(value,*args,**kwargs)
                with self.subTest(target_bytes=len(target)),patch.object(partitioned_plan_store.json,'loads',side_effect=injected):
                    with self.assertRaisesRegex(AdoptionError,'^STORAGE_CORRUPT$'):
                        read_plan(store,helper.plan_ref)
                    self.assertTrue(db.in_transaction,'reader must leave caller transaction open')
                    self.assertEqual(db.total_changes,changes_before)
                    self.assertEqual(snapshot(db),before)
                    self.assertEqual(tuple(tuple(row) for row in db.execute("SELECT key,value FROM adoption_meta ORDER BY key")),meta_before)
        finally:
            db.set_authorizer(None)

    def test_expired_contract_and_missing_current_do_not_fallback_to_history(self):
        helper,store=make_fixture(self)
        self.transact(store)
        expiry=store._db.execute("SELECT v.expires_at FROM eval_validations v JOIN eval_current c ON c.validation_id=v.id WHERE c.series_id=?",('partition-series',)).fetchone()[0]
        self.assertIs(type(expiry),int)
        with self.assertRaisesRegex(AdoptionError,'^PREREQUISITE_UNAVAILABLE$'):
            read_plan(store,helper.plan_ref,now=expiry)

        helper2,store2=make_fixture(self)
        self.transact(store2)
        store2._db.execute("DELETE FROM eval_current WHERE series_id=?",('partition-series',))
        self.assertEqual(store2._db.execute("SELECT COUNT(*) FROM eval_adoptions WHERE series_id=? AND generation=1",('partition-series',)).fetchone()[0],1)
        with self.assertRaisesRegex(AdoptionError,'^CONTRACT_INVALID$'):
            read_plan(store2,helper2.plan_ref)


if __name__ == "__main__":
    unittest.main()
