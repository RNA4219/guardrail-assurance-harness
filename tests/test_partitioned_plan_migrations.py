"""Explicit v4-to-v5 migration preserves all existing authority rows."""
from pathlib import Path
import hashlib
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gah.adoption import AdoptionStore
from gah.adoption_migrations import MigrationError
from gah.evaluation_authority import EvaluationExtension
from gah.partitioned_authority import PartitionedEvaluationExtension
from gah.partitioned_plan_migrations import (
    SOURCE_V8_EXTENSION_DIGEST, SOURCE_V8_VALIDATOR_DIGEST,
    migrate_partitioned_store,
)
from gah.wire import canonical_bytes
from tests.test_fixture_admission import FixtureAdmissionTests, request


class PartitionedPlanMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "authority.sqlite"
        store = AdoptionStore(self.path, extension=EvaluationExtension())
        self.source_digest = store._extension_digest
        store.close()
        payload = {"probe": "kept", "ordinal": 1}
        raw = canonical_bytes(payload).decode("utf-8")
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("INSERT INTO eval_objects(kind,id,digest,payload_json) VALUES(?,?,?,?)",
                       ("migration_probe", "kept-row", digest, raw))
            db.execute("UPDATE resource_meta SET value=0 WHERE key='last_clock'")
            db.execute("UPDATE run_evidence_meta SET value=0 WHERE key='last_clock'")
            db.commit()
        self.old_rows = self._existing_rows()

    def _existing_rows(self):
        with closing(sqlite3.connect(self.path)) as db:
            tables = {r[0] for r in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            result = {}
            for table in sorted(tables):
                columns = [r[1] for r in db.execute(f'PRAGMA table_info("{table}")')]
                result[table] = (columns, db.execute(f'SELECT * FROM "{table}"').fetchall())
            return result

    def _seed_adopted_contract(self):
        helper = FixtureAdmissionTests()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        store = AdoptionStore(self.path, clock=helper.clock,
                              bootstrap_policy=helper.policy,
                              extension=EvaluationExtension())
        try:
            helper.adopt_policy(store)
            prepared = helper.prepare(store, run_id="migration-fixture")
            contract = prepared["prepared"]["bound_run"]["contract"]
            store.dispatch(12001, 12001, request(
                "contract_propose", "migration-contract-propose",
                proposal_id="migration-contract-proposal",
                series_id="migration-contract-series", expected_generation=0,
                contract=contract))
            store.dispatch(12003, 12003, request(
                "contract_validate", "migration-contract-validate",
                proposal_id="migration-contract-proposal",
                validation_id="migration-contract-validation"))
            store.dispatch(12001, 12001, request(
                "contract_adopt", "migration-contract-adopt",
                proposal_id="migration-contract-proposal",
                validation_id="migration-contract-validation", expected_generation=0))
        finally:
            store.close()
        self.old_rows = self._existing_rows()
        return helper

    def test_current_v4_pair_migrates_additive_schema_without_rebinding_rows(self):
        helper = self._seed_adopted_contract()
        result = migrate_partitioned_store(self.path, expected_source_digest=self.source_digest)
        self.assertTrue(result["changed"])
        self.assertFalse(result["ci_eligible"])
        self.assertEqual(result["schema_version"], 5)
        after = self._existing_rows()
        for table, rows in self.old_rows.items():
            expected = rows
            if table == "adoption_config":
                expected = (rows[0], [(key, value if key != "extension_digest" else result["extension_digest"]) for key, value in rows[1]])
            elif table == "adoption_meta":
                expected = (rows[0], [(key, value if key != "schema_version" else 5) for key, value in rows[1]])
            self.assertEqual(after[table], expected, table)
        self.assertEqual(after["partition_plan_upload"][1], [])
        self.assertEqual(after["partition_plan_segments"][1], [])
        self.assertEqual(after["partition_plan_commits"][1], [])
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 5)
            config = dict(db.execute("SELECT key,value FROM adoption_config"))
            self.assertEqual(config["extension_digest"], result["extension_digest"])
            self.assertEqual(config["validator_digest"], self._old_config()["validator_digest"])
            meta = dict(db.execute("SELECT key,value FROM adoption_meta"))
            self.assertEqual(meta["schema_version"], 5)
            self.assertEqual(meta["last_clock"], self._old_meta()["last_clock"])
        migrated = AdoptionStore(self.path, clock=helper.clock,
                                 bootstrap_policy=helper.policy,
                                 extension=PartitionedEvaluationExtension())
        try:
            current = migrated.dispatch(12004, 12004, request(
                "contract_current", "migration-contract-current",
                series_id="migration-contract-series"))
            self.assertTrue(current["adopted"])
            self.assertTrue(current["valid"])
        finally:
            migrated.close()

    def test_frozen_source_v8_pair_is_accepted_as_a_distinct_predecessor(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("UPDATE adoption_config SET value=? WHERE key='extension_digest'",
                       (SOURCE_V8_EXTENSION_DIGEST,))
            db.execute("UPDATE adoption_config SET value=? WHERE key='validator_digest'",
                       (SOURCE_V8_VALIDATOR_DIGEST,))
            db.commit()
        result = migrate_partitioned_store(
            self.path, expected_source_digest=SOURCE_V8_EXTENSION_DIGEST)
        self.assertEqual(result["predecessor_extension_digest"], SOURCE_V8_EXTENSION_DIGEST)
        with AdoptionStore(self.path, extension=PartitionedEvaluationExtension()):
            pass

    def test_caller_digest_mismatch_or_unknown_schema_fails_without_changes(self):
        before = self._existing_rows()
        with self.assertRaises(MigrationError) as caught:
            migrate_partitioned_store(self.path, expected_source_digest="0" * 64)
        self.assertEqual(caught.exception.code, "CONFIG_MISMATCH")
        self.assertEqual(self._existing_rows(), before)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("CREATE TABLE unexpected_table(value TEXT)")
        before_unknown = self._existing_rows()
        with self.assertRaises(MigrationError) as caught:
            migrate_partitioned_store(self.path, expected_source_digest=self.source_digest)
        self.assertEqual(caught.exception.code, "UNSUPPORTED_STORE")
        self.assertEqual(self._existing_rows(), before_unknown)

    def test_unknown_column_is_rejected_without_changes(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("ALTER TABLE eval_objects ADD COLUMN unexpected TEXT")
            db.commit()
        before = self._existing_rows()
        with self.assertRaises(MigrationError) as caught:
            migrate_partitioned_store(self.path, expected_source_digest=self.source_digest)
        self.assertEqual(caught.exception.code, "UNSUPPORTED_STORE")
        self.assertEqual(self._existing_rows(), before)

    def test_failure_after_additive_ddl_rolls_back_every_schema_change(self):
        import gah.partitioned_plan_migrations as module
        create = module.plan_store.create_schema

        def fail_after_ddl(db):
            create(db)
            raise sqlite3.OperationalError("injected")

        with patch.object(module.plan_store, "create_schema", side_effect=fail_after_ddl):
            with self.assertRaises(MigrationError) as caught:
                migrate_partitioned_store(self.path, expected_source_digest=self.source_digest)
        self.assertEqual(caught.exception.code, "MIGRATION_FAILED")
        self.assertEqual(self._existing_rows(), self.old_rows)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 4)

    def test_source_digest_change_after_ddl_rolls_back(self):
        import gah.partitioned_plan_migrations as module
        original_digest = module.base._compute_source_digest
        original_create = module.plan_store.create_schema
        state = {"changed": False}

        def changing_digest():
            return "f" * 64 if state["changed"] else original_digest()

        def create_then_change(db):
            original_create(db)
            state["changed"] = True

        with patch.object(module.base, "_compute_source_digest", side_effect=changing_digest):
            with patch.object(module.plan_store, "create_schema", side_effect=create_then_change):
                with self.assertRaises(MigrationError) as caught:
                    migrate_partitioned_store(self.path, expected_source_digest=self.source_digest)
        self.assertEqual(caught.exception.code, "SOURCE_CHANGED")
        self.assertEqual(self._existing_rows(), self.old_rows)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 4)

    def test_readonly_connection_fails_closed_without_mutation(self):
        import gah.partitioned_plan_migrations as module

        def readonly_connect(path, *args, **kwargs):
            uri = Path(path).resolve().as_uri() + "?mode=ro"
            return sqlite3.connect(uri, uri=True, isolation_level=None, timeout=5)

        before = self._existing_rows()
        with patch.object(module, "connect_sqlite", side_effect=readonly_connect):
            with self.assertRaises(MigrationError) as caught:
                migrate_partitioned_store(self.path, expected_source_digest=self.source_digest)
        self.assertEqual(caught.exception.code, "MIGRATION_FAILED")
        self.assertEqual(self._existing_rows(), before)

    def _old_config(self):
        with closing(sqlite3.connect(self.path)) as db:
            return dict(db.execute("SELECT key,value FROM adoption_config"))

    def _old_meta(self):
        with closing(sqlite3.connect(self.path)) as db:
            return dict(db.execute("SELECT key,value FROM adoption_meta"))


if __name__ == "__main__":
    unittest.main()
