"""監督側の移行反例。受入済みreceiptと現在DBの同値を区別する。"""
import json
import sqlite3
import unittest
from unittest.mock import patch
from tests import test_operations_migration as fixtures
from gah import operations_migration as m
from gah.contracts import ContractError

class MigrationReviewTests(unittest.TestCase):
    def setUp(self):
        self.helper = fixtures.OperationsMigrationTests("runTest")
        self.helper.setUp()
        self.addCleanup(self.helper.doCleanups)
        self.root = self.helper.root
        self.before = self.helper.database.read_bytes()
        result, self.plan = self.helper._preview()
        self.assertEqual(result["operation_status"], "COMPLETED")

    def apply(self, request_id="review-apply"):
        return m.apply(self.root, "adoption.sqlite", "plan.json", request_id=request_id, clock=self.helper.clock)

    def version(self, path):
        db = sqlite3.connect(path)
        try: return db.execute("PRAGMA user_version").fetchone()[0]
        finally: db.close()

    def test_backup_stays_original_while_dry_run_and_apply_move_forward(self):
        payload = self.plan["payload"]
        backup = self.root / payload["snapshot_path"]
        dry_run = self.root / payload["dry_run_path"]
        self.assertEqual(backup.read_bytes(), self.before)
        self.assertEqual(self.version(backup), 2)
        self.assertEqual(self.version(dry_run), 4)
        self.assertEqual(self.apply()[0]["operation_status"], "COMPLETED")
        self.assertEqual(backup.read_bytes(), self.before)

    def test_modified_plan_with_same_request_cannot_reuse_prior_result(self):
        self.assertEqual(self.apply()[0]["operation_status"], "COMPLETED")
        self.plan["expires_at"] += 1
        (self.root / "plan.json").write_text(json.dumps(self.plan), encoding="utf-8")
        result, artifact = self.apply()
        self.assertEqual(result["operation_status"], "REJECTED")
        self.assertIn("IDEMPOTENCY_CONFLICT", result["reasons"])
        self.assertIsNone(artifact)

    def test_lost_journal_ack_recovers_saved_receipt_without_migrating_again(self):
        original = m.migrations.migrate_evaluation_store
        with patch.object(m.migrations, "migrate_evaluation_store", wraps=original) as invoked:
            with patch.object(m.OperationJournal, "finish", side_effect=ContractError("IO_ERROR")):
                result, _ = self.apply()
            self.assertEqual(result["operation_status"], "INCOMPLETE")
            recovered, receipt = self.apply()
            self.assertEqual(recovered["operation_status"], "COMPLETED")
            self.assertEqual(invoked.call_count, 1)
            self.assertEqual(receipt["plan_ref"], m._plan_ref(self.plan))

    def test_commit_without_saved_receipt_remains_unknown_even_if_target_matches(self):
        original = m.migrations.migrate_evaluation_store
        def lost(path, **kwargs):
            original(path, **kwargs)
            raise OSError("simulated commit acknowledgement loss")
        with patch.object(m.migrations, "migrate_evaluation_store", side_effect=lost) as invoked:
            first, _ = self.apply()
            second, _ = self.apply()
        self.assertEqual(self.version(self.helper.database), 4)
        for result in (first, second):
            self.assertEqual(result["operation_status"], "INCOMPLETE")
            self.assertIn("OPERATION_UNKNOWN", result["reasons"])
        self.assertEqual(invoked.call_count, 1)

    def test_implementation_change_rejects_before_database_mutation(self):
        with patch.object(m, "_implementation_digest", return_value="0" * 64):
            result, _ = self.apply()
        self.assertNotEqual(result["operation_status"], "COMPLETED")
        self.assertIn("STALE_OR_INVALIDATED", result["reasons"])
        self.assertEqual(self.helper.database.read_bytes(), self.before)

    def test_changed_snapshot_is_rejected_before_database_mutation(self):
        path = self.root / self.plan["payload"]["snapshot_path"]
        with path.open("ab") as stream: stream.write(b"changed")
        result, _ = self.apply()
        self.assertNotEqual(result["operation_status"], "COMPLETED")
        self.assertEqual(self.helper.database.read_bytes(), self.before)

if __name__ == "__main__": unittest.main()
