"""実採択・SQLiteでv6診断runを確認する。workerやDockerは起動しない。"""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from gah.adoption import AdoptionError
from gah.assurance_authority import fixed_profile
from gah.partitioned_run_authority import PartitionedRunEvaluationExtension
from gah.partitioned_run_evidence import PartitionedRunEvidenceBook
from gah.run_evidence import EvidenceError
from tests.test_fixture_admission import request


class PartitionedRunIntegrationTests(unittest.TestCase):
    def setUp(self):
        # import the fixture class locally so unittest does not collect it again.
        from tests.test_partitioned_authority import PartitionedAuthorityTests
        self.helper = PartitionedAuthorityTests("test_real_adoption_upload_restart_replay_commit_and_fresh_read")
        self.helper.setUp()
        self.addCleanup(self.helper.doCleanups)
        self.store = self.helper.open(PartitionedRunEvaluationExtension())
        self.addCleanup(lambda: self.store.close())
        self.helper.seed(self.store)
        self.helper.begin(self.store)
        self.helper.put(self.store)
        self.call(12001, "plan_partition_commit", "integration-commit", upload_id="upload")
        self.manifest = deepcopy(self.helper.bound["manifest"])
        self.manifest.update(schema_version=2, run_id="partition-diagnostic", purpose="diagnostic",
                             baseline_ref=None, plan_ref=deepcopy(self.helper.plan_ref))
        self.profile = fixed_profile()
        self.begin_request = request("run_begin_v2", "integration-begin", manifest=self.manifest,
            contract_series_id="partition-series", expected_generation=1, execution_profile=self.profile)

    def call(self, uid, action, request_id, **fields):
        return self.store.dispatch(uid, uid, request(action, request_id, **fields))

    def begin(self):
        return self.store.dispatch(12004, 12004, deepcopy(self.begin_request))

    def snapshot(self):
        db = self.store._db
        names = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return {name: sorted((tuple(row) for row in db.execute('SELECT * FROM "'+name+'"')), key=repr) for name in names}

    def assert_diagnostic(self, response):
        self.assertEqual(response["schema_version"], 1)
        self.assertFalse(response["ci_eligible"])
        self.assertFalse(response["resource_closure_verified"])
        self.assertFalse(response["admission_verified"])

    def test_adopted_plan_begin_replay_restart_and_finalization(self):
        begun = self.begin()
        self.assert_diagnostic(begun)
        self.assertEqual(begun["run"]["schema_version"], 2)
        self.assertNotIn("plan", begun["run"]["bundle"])
        self.assertNotIn("manifest", begun["run"]["bundle"])
        self.assertEqual(self.store._db.execute("SELECT COUNT(*) FROM eval_runs_v2").fetchone()[0], 1)
        self.assertEqual(self.store._db.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertTrue(self.begin()["duplicate"])
        row_before = tuple(self.store._db.execute("SELECT * FROM bound_runs WHERE run_id=?", (self.manifest["run_id"],)).fetchone())
        self.store.close()
        self.store = self.helper.open(PartitionedRunEvaluationExtension())
        status = self.call(12001, "run_status_v2", "integration-status", run_id=self.manifest["run_id"])
        self.assert_diagnostic(status)
        self.assertEqual(status["run"]["bundle_digest"], begun["run"]["bundle_digest"])
        self.assertEqual(tuple(self.store._db.execute("SELECT * FROM bound_runs WHERE run_id=?", (self.manifest["run_id"],)).fetchone()), row_before)
        with self.assertRaisesRegex(AdoptionError, "^NOT_FINALIZED$"):
            self.call(12004, "evidence_terminal_v2", "no-terminal", run_id=self.manifest["run_id"])
        terminal = self.call(12004, "evidence_finalize_v2", "integration-finalize", run_id=self.manifest["run_id"])
        self.assert_diagnostic(terminal)
        self.assertEqual(self.store._db.execute("SELECT COUNT(*) FROM terminals").fetchone()[0], 1)
        # Missing observations are not successful execution evidence.
        self.assertEqual(self.store._db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0], 0)
        self.assertEqual(self.store._db.execute("SELECT COUNT(*) FROM authority_run_receipts").fetchone()[0], 0)
        current = self.call(12001, "evidence_current_v2", "integration-current", run_id=self.manifest["run_id"],
                            expected_bundle_digest=begun["run"]["bundle_digest"])
        self.assert_diagnostic(current)
        self.assertFalse(current["evidence"]["use"])
        wrong = self.call(12001, "evidence_current_v2", "integration-wrong-binding", run_id=self.manifest["run_id"],
                          expected_bundle_digest="0"*64)
        self.assertIn("BINDING_MISMATCH", wrong["evidence"]["reasons"])

    def test_validator_records_and_replays_diagnostic_attempt_without_authority_receipt(self):
        from tests.test_assurance_authority import AssuranceAuthorityTests
        self.begin()
        kinds = {o["obligation_id"]: o["kind"] for c in self.helper.bound["registry"]["controls"] for o in c["obligations"]}
        entry = next(e for e in self.helper.bound["plan"]["entries"] if kinds[e["obligation_id"]] == "mutation")
        # A synthetic observation exercises storage only; no actual worker claim is made.
        attempt = AssuranceAuthorityTests._attempt(self, {"manifest": self.manifest, "plan": {"entries": [entry]}})
        attempt.update(started_at=1000, finished_at=1000)
        args = dict(run_id=self.manifest["run_id"], attempt=attempt)
        first = self.call(12003, "evidence_record_v2", "record-diagnostic-attempt", **args)
        self.assert_diagnostic(first)
        self.assertEqual(first["evidence"]["schema_version"], 2)
        self.call(12003, "evidence_record_v2", "record-diagnostic-attempt", **args)
        saved = self.call(12001, "evidence_attempt_v2", "read-diagnostic-attempt", attempt_id=attempt["attempt_id"])
        self.assertEqual(saved["evidence"]["attempt"], attempt)
        self.assertEqual(saved["evidence"]["delivery_count"], 2)
        self.assertEqual(self.store._db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0], 1)
        self.assertEqual(self.store._db.execute("SELECT COUNT(*) FROM authority_attempt_origins").fetchone()[0], 0)
        self.assertEqual(self.store._db.execute("SELECT COUNT(*) FROM authority_run_receipts").fetchone()[0], 0)

    def test_roles_reject_before_begin_and_record_writes(self):
        before = self.snapshot()
        for uid in (12001, 12002, 12003):
            with self.subTest(uid=uid), self.assertRaisesRegex(AdoptionError, "^AUTHORITY_DENIED$"):
                self.store.dispatch(uid, uid, deepcopy(self.begin_request))
        self.assertEqual(self.snapshot(), before)
        self.begin()
        before = self.snapshot()
        with self.assertRaisesRegex(AdoptionError, "^AUTHORITY_DENIED$"):
            self.call(12004, "evidence_record_v2", "wrong-recorder", run_id=self.manifest["run_id"], attempt={})
        self.assertEqual(self.snapshot(), before)

    def test_failure_after_book_writes_rolls_back_route_bound_state_and_request(self):
        before = self.snapshot()
        original = PartitionedRunEvidenceBook.start_partitioned_run
        def fail_after_write(book, *args):
            original(book, *args)
            raise EvidenceError("INJECTED_AFTER_BOOK_WRITE")
        with patch.object(PartitionedRunEvidenceBook, "start_partitioned_run", fail_after_write):
            with self.assertRaisesRegex(AdoptionError, "^INJECTED_AFTER_BOOK_WRITE$"):
                self.begin()
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.begin()["duplicate"])

    def test_replayed_status_checks_current_permission_and_plan_bytes(self):
        self.begin()
        params = dict(run_id=self.manifest["run_id"])
        self.call(12001, "run_status_v2", "fresh-status", **params)
        db = self.store._db
        segment = db.execute("SELECT plan_id,segment_index,segment_json FROM partition_plan_segments LIMIT 1").fetchone()
        db.execute("UPDATE partition_plan_segments SET segment_json=segment_json || ' ' WHERE plan_id=? AND segment_index=?", tuple(segment[:2]))
        corrupt = self.snapshot()
        with self.assertRaisesRegex(AdoptionError, "^STORAGE_CORRUPT$"):
            self.call(12001, "run_status_v2", "fresh-status", **params)
        self.assertEqual(self.snapshot(), corrupt)
        db.execute("UPDATE partition_plan_segments SET segment_json=? WHERE plan_id=? AND segment_index=?", (segment[2], segment[0], segment[1]))
        self.call(12004, "revoke_actor", "revoke-validator", actor_id="validator")
        revoked = self.snapshot()
        with self.assertRaises(AdoptionError):
            self.call(12001, "run_status_v2", "fresh-status", **params)
        self.assertEqual(self.snapshot(), revoked)

    def test_v1_admission_reservation_and_begin_conflict_in_both_directions(self):
        request_reserved = deepcopy(self.begin_request)
        request_reserved["manifest"]["run_id"] = "partition-fixture"
        before = self.snapshot()
        with self.assertRaisesRegex(AdoptionError, "^RUN_CONFLICT$"):
            self.store.dispatch(12004, 12004, request_reserved)
        self.assertEqual(self.snapshot(), before)
        self.begin()
        before = self.snapshot()
        with self.assertRaisesRegex(AdoptionError, "^RUN_CONFLICT$"):
            self.call(12001, "fixture_prepare", "reverse-reservation", run_id=self.manifest["run_id"],
                      policy_series_id=self.helper.policy["policy_id"])
        manifest_v1 = deepcopy(self.helper.bound["manifest"])
        manifest_v1["run_id"] = self.manifest["run_id"]
        with self.assertRaisesRegex(AdoptionError, "^RUN_CONFLICT$"):
            self.call(12004, "run_begin", "reverse-begin", manifest=manifest_v1,
                      plan=self.helper.bound["plan"], contract_series_id="partition-series")
        self.assertEqual(self.snapshot(), before)

    def test_source_change_after_handler_and_invalid_profile_are_atomic(self):
        from gah import partitioned_run_authority as authority, partitioned_run_store as storage
        before = self.snapshot()
        pinned = self.store._extension_digest
        changed = False
        original = storage.handle
        def changed_handle(*args, **kwargs):
            nonlocal changed
            result = original(*args, **kwargs)
            changed = True
            return result
        with patch.object(authority, "source_digest", side_effect=lambda: "f"*64 if changed else pinned), patch.object(storage, "handle", side_effect=changed_handle):
            with self.assertRaisesRegex(AdoptionError, "^EXTENSION_INVALID$"):
                self.begin()
        self.assertEqual(self.snapshot(), before)
        invalid = deepcopy(self.begin_request)
        invalid["execution_profile"]["isolation_digest"] = "0"*64
        with self.assertRaisesRegex(AdoptionError, "^PROFILE_MISMATCH$"):
            self.store.dispatch(12004, 12004, invalid)
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
