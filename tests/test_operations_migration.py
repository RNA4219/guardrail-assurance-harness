"""固定offline migration入口の局所契約を検査する。"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest

from tests.legacy_adoption_fixture import create_legacy_adoption_fixture

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gah import adoption_migrations as adoption
from gah import operations_migration as migration


class OperationsMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.database = self.root / "adoption.sqlite"
        create_legacy_adoption_fixture(self.database)
        self.clock = lambda: 1_700_000_000

    def _preview(self, request_id="migration-preview-1", output="plan.json"):
        return migration.preview(
            self.root, "adoption.sqlite", output,
            request_id=request_id, clock=self.clock,
        )

    def test_preview_uses_snapshot_and_keeps_source_bytes(self):
        before = self.database.read_bytes()
        result, plan = self._preview()
        self.assertEqual(result["operation_status"], "COMPLETED")
        self.assertFalse(result["ci_eligible"])
        self.assertEqual(result["result_ref"]["kind"], "migration_plan")
        self.assertIsNotNone(plan)
        self.assertEqual(plan["kind"], "migration_plan")
        self.assertEqual(plan["payload"]["source_schema_version"], 2)
        self.assertEqual(plan["payload"]["target_schema_version"], 4)
        self.assertTrue(plan["payload"]["snapshot_required"])
        self.assertEqual(self.database.read_bytes(), before)
        snapshot = self.root / plan["payload"]["snapshot_path"]
        self.assertTrue(snapshot.is_file())
        db = sqlite3.connect(self.database)
        try:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 2)
        finally:
            db.close()

    def test_apply_rechecks_source_and_returns_metadata_only_receipt(self):
        preview_result, plan = self._preview()
        self.assertEqual(preview_result["operation_status"], "COMPLETED")
        result, receipt = migration.apply(
            self.root, "adoption.sqlite", "plan.json",
            request_id="migration-apply-1", clock=self.clock,
        )
        self.assertEqual(result["operation_status"], "COMPLETED")
        self.assertIsNotNone(receipt)
        self.assertEqual(receipt["kind"], "migration_operation_result")
        self.assertFalse(receipt["ci_eligible"])
        self.assertTrue(receipt["metadata_only"])
        self.assertFalse(receipt["external_refs_verified"])
        self.assertFalse(receipt["product_run_authority"])
        db = sqlite3.connect(self.database)
        try:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 4)
        finally:
            db.close()

    def test_same_request_recovers_saved_commit_without_second_migration(self):
        self._preview()
        first, _ = migration._dispatch_apply(
            self.root, "adoption.sqlite", "plan.json",
            "migration-apply-2", self.clock,
        )
        second, receipt = migration.apply(
            self.root, "adoption.sqlite", "plan.json",
            request_id="migration-apply-2", clock=self.clock,
        )
        self.assertEqual(second, first)
        self.assertEqual(second["operation_status"], "COMPLETED")
        self.assertIsNotNone(receipt)

    def test_source_change_is_rejected_before_database_write(self):
        self._preview()
        with self.database.open("ab") as stream:
            stream.write(b"changed")
        result, receipt = migration.apply(
            self.root, "adoption.sqlite", "plan.json",
            request_id="migration-apply-3", clock=self.clock,
        )
        self.assertEqual(result["operation_status"], "REJECTED")
        self.assertIn("BINDING_MISMATCH", result["reasons"])
        self.assertIsNone(receipt)
        db = sqlite3.connect(self.database)
        try:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 2)
        finally:
            db.close()

    def test_unknown_store_is_rejected_without_plan_or_write(self):
        unknown = self.root / "unknown.sqlite"
        db = sqlite3.connect(unknown)
        db.execute("PRAGMA user_version=99")
        db.commit()
        db.close()
        before = unknown.read_bytes()
        result, plan = migration.preview(
            self.root, "unknown.sqlite", "unknown-plan.json",
            request_id="migration-preview-unknown", clock=self.clock,
        )
        self.assertEqual(result["operation_status"], "REJECTED")
        self.assertIn("SCHEMA_UNSUPPORTED", result["reasons"])
        self.assertIsNone(plan)
        self.assertEqual(unknown.read_bytes(), before)

    def test_plan_validator_rejects_boolean_schema_and_source_ref_mismatch(self):
        _, plan = self._preview()
        self.assertIsNotNone(plan)
        malformed = json.loads(json.dumps(plan))
        malformed["schema_version"] = True
        with self.assertRaises(migration.MigrationOperationError):
            migration.validate_migration_plan(malformed)
        malformed = json.loads(json.dumps(plan))
        malformed["payload"]["source_ref"]["id"] = "other"
        with self.assertRaises(migration.MigrationOperationError):
            migration.validate_migration_plan(malformed)

    def test_apply_requires_same_database_argument(self):
        self._preview()
        other = self.root / "other.sqlite"
        shutil.copyfile(self.database, other)
        result, receipt = migration.apply(
            self.root, "other.sqlite", "plan.json",
            request_id="migration-apply-other", clock=self.clock,
        )
        self.assertEqual(result["operation_status"], "REJECTED")
        self.assertIn("BINDING_MISMATCH", result["reasons"])
        self.assertIsNone(receipt)


    def test_apply_passes_fixed_expected_state_to_existing_migration(self):
        self._preview()
        original = adoption.migrate_evaluation_store
        observed = {}

        def wrapped(path, **kwargs):
            observed.update(kwargs)
            return original(path, **kwargs)

        old = migration.migrations.migrate_evaluation_store
        migration.migrations.migrate_evaluation_store = wrapped
        self.addCleanup(setattr, migration.migrations,
                        "migrate_evaluation_store", old)
        result, _ = migration.apply(
            self.root, "adoption.sqlite", "plan.json",
            request_id="migration-apply-guard", clock=self.clock,
        )
        self.assertEqual(result["operation_status"], "COMPLETED")
        self.assertEqual(set(observed), {"expected_state"})
        self.assertEqual(set(observed["expected_state"]), {"before", "after"})
        self.assertIn("table_digests", observed["expected_state"]["before"])
        self.assertIn("table_digests", observed["expected_state"]["after"])

    def test_commit_guard_mismatch_rolls_back_existing_migration(self):
        _, plan = self._preview()
        payload = plan["payload"]
        expected = {
            "before": {
                **payload["source_state"],
                "table_digests": payload["reference_digests"]["before"]["table_digests"],
            },
            "after": {
                **payload["target_state"],
                "logical_digest": "0" * 64,
                "table_digests": payload["reference_digests"]["after"]["table_digests"],
            },
        }
        with self.assertRaisesRegex(adoption.MigrationError, "^BINDING_MISMATCH$"):
            adoption.migrate_evaluation_store(
                self.database, expected_state=expected,
            )
        db = sqlite3.connect(self.database)
        try:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 2)
            self.assertEqual(
                db.execute("SELECT value FROM adoption_config WHERE key='extension_digest'").fetchone()[0],
                adoption._V2_EXTENSION_DIGEST,
            )
        finally:
            db.close()

if __name__ == "__main__":
    unittest.main()
