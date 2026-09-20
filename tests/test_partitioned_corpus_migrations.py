"""明示v6→v7 migrationの限定回帰。Docker/image操作は行わない。"""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from gah.adoption import AdoptionError, AdoptionStore
from gah.adoption_migrations import MigrationError
from gah.partitioned_corpus_authority import PartitionedCorpusEvaluationExtension
from gah.partitioned_corpus_migrations import migrate_partitioned_corpus_store_v6_to_v7
from gah.partitioned_run_authority import PartitionedRunEvaluationExtension
from gah.run_contracts import validate_evaluation_contract


def _snapshot(path):
    with closing(sqlite3.connect(path)) as db:
        names = [row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        return {name: ([row[1] for row in db.execute(f'PRAGMA table_info("{name}")')],
                       sorted(db.execute(f'SELECT * FROM "{name}"').fetchall(), key=repr))
                for name in names}


class PartitionedCorpusMigrationTests(unittest.TestCase):
    def setUp(self):
        from tests.test_partitioned_run_integration import PartitionedRunIntegrationTests

        seed = PartitionedRunIntegrationTests("test_adopted_plan_begin_replay_restart_and_finalization")
        seed.setUp()
        self.seed = seed
        self.addCleanup(seed.doCleanups)
        self.path = seed.helper.path
        # setUp created/adopted gen1 and uploaded a committed plan; begin adds a real v6
        # ref-only diagnostic route and evidence receipt through the normal authority API.
        self.begun = seed.begin()
        self.run_id = seed.manifest["run_id"]
        seed.store.close()

    @property
    def source_digest(self):
        return PartitionedRunEvaluationExtension().digest

    def test_nonempty_diagnostic_v6_rows_migrate_and_reopen_without_rewriting(self):
        before = _snapshot(self.path)
        with closing(sqlite3.connect(self.path)) as db:
            db.row_factory = sqlite3.Row
            old_plan = dict(db.execute("SELECT * FROM partition_plan_commits LIMIT 1").fetchone())
            old_route = dict(db.execute("SELECT * FROM eval_runs_v2 WHERE run_id=?", (self.run_id,)).fetchone())
            db.execute("PRAGMA query_only=ON")
            run_count = db.execute("SELECT COUNT(*) FROM eval_runs_v2").fetchone()[0]
        self.assertEqual(run_count, 1)
        result = migrate_partitioned_corpus_store_v6_to_v7(
            self.path, expected_source_digest=self.source_digest)
        self.assertEqual(result["schema_version"], 7)
        self.assertFalse(result["ci_eligible"])
        self.assertTrue(result["old_run_and_plan_rows_preserved"])
        after = _snapshot(self.path)
        for table, old in before.items():
            if table == "adoption_meta":
                expected = (old[0], [(key, value if key != "schema_version" else 7)
                                     for key, value in old[1]])
            elif table == "adoption_config":
                expected = (old[0], [(key, value if key != "extension_digest" else result["extension_digest"])
                                     for key, value in old[1]])
            else:
                expected = old
            self.assertEqual(after[table], expected, table)
        self.assertEqual(old_plan["extension_digest"],
                         next(value for key, value in before["adoption_config"][1]
                              if key == "extension_digest"))
        self.assertEqual(old_route["extension_digest"], old_plan["extension_digest"])
        from gah.partitioned_corpus_store import TABLES as CORPUS_TABLES
        for table in CORPUS_TABLES:
            self.assertEqual(after[table][1], [], table)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 7)
            self.assertIsNone(db.execute("PRAGMA foreign_key_check").fetchone())
        reopened = AdoptionStore(self.path, extension=PartitionedCorpusEvaluationExtension())
        reopened.close()

    def test_explicit_old_plan_digest_is_retained_and_v7_reader_rejects_it(self):
        migrate_partitioned_corpus_store_v6_to_v7(
            self.path, expected_source_digest=self.source_digest)
        with closing(sqlite3.connect(self.path)) as db:
            db.row_factory = sqlite3.Row
            route = db.execute("SELECT * FROM eval_runs_v2 WHERE run_id=?", (self.run_id,)).fetchone()
            self.assertEqual(route["extension_digest"], self.source_digest)
            plan = db.execute("SELECT * FROM partition_plan_commits LIMIT 1").fetchone()
            old_digest = plan["extension_digest"]
        with self.assertRaises(AdoptionError):
            reopened = AdoptionStore(self.path, extension=PartitionedCorpusEvaluationExtension())
            try:
                with reopened._transaction() as db:
                    contract_row = db.execute(
                        "SELECT payload_json,digest FROM eval_adoptions WHERE series_id=? AND generation=1",
                        (plan["contract_series_id"],)).fetchone()
                    contract = validate_evaluation_contract(json.loads(contract_row["payload_json"]))
                    from gah.partitioned_plan_store import _validate_committed_with_plan
                    _validate_committed_with_plan(db, plan, contract, plan["permission_generation"],
                                                  reopened._extension_digest)
            finally:
                reopened.close()
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT extension_digest FROM partition_plan_commits LIMIT 1").fetchone()[0],
                             old_digest)

    def test_wrong_source_pair_and_unknown_version_leave_database_unchanged(self):
        before = _snapshot(self.path)
        with self.assertRaises(MigrationError) as caught:
            migrate_partitioned_corpus_store_v6_to_v7(self.path, expected_source_digest="0" * 64)
        self.assertEqual(caught.exception.code, "CONFIG_MISMATCH")
        self.assertEqual(_snapshot(self.path), before)
        with closing(sqlite3.connect(self.path)) as db:
            config = dict(db.execute("SELECT key,value FROM adoption_config"))
            db.execute("UPDATE adoption_config SET value=? WHERE key='extension_digest'", ("0" * 64,))
            db.commit()
        before_bad_pair = _snapshot(self.path)
        with self.assertRaises(MigrationError) as caught:
            migrate_partitioned_corpus_store_v6_to_v7(self.path, expected_source_digest=self.source_digest)
        self.assertEqual(caught.exception.code, "CONFIG_MISMATCH")
        self.assertEqual(_snapshot(self.path), before_bad_pair)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("UPDATE adoption_config SET value=? WHERE key='extension_digest'", (config["extension_digest"],))
            db.execute("PRAGMA user_version=5")
            db.commit()
        before_bad_version = _snapshot(self.path)
        with self.assertRaises(MigrationError) as caught:
            migrate_partitioned_corpus_store_v6_to_v7(self.path, expected_source_digest=self.source_digest)
        self.assertEqual(caught.exception.code, "UNSUPPORTED_STORE")
        self.assertEqual(_snapshot(self.path), before_bad_version)

    def test_corrupt_diagnostic_route_rolls_back(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("UPDATE eval_runs_v2 SET manifest_json=manifest_json || ' ' WHERE run_id=?",
                       (self.run_id,))
            db.commit()
        before = _snapshot(self.path)
        with self.assertRaises(MigrationError) as caught:
            migrate_partitioned_corpus_store_v6_to_v7(self.path, expected_source_digest=self.source_digest)
        self.assertEqual(caught.exception.code, "STORAGE_CORRUPT")
        self.assertEqual(_snapshot(self.path), before)

    def test_corrupt_saved_v2_evidence_rolls_back(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("UPDATE bound_runs SET bundle_json=bundle_json || ' ' WHERE run_id=?",
                       (self.run_id,))
            db.commit()
        before = _snapshot(self.path)
        with self.assertRaises(MigrationError) as caught:
            migrate_partitioned_corpus_store_v6_to_v7(self.path, expected_source_digest=self.source_digest)
        self.assertEqual(caught.exception.code, "STORAGE_CORRUPT")
        self.assertEqual(_snapshot(self.path), before)

    def test_semantically_invalid_but_rehashed_attempt_is_rejected(self):
        from tests.test_assurance_authority import AssuranceAuthorityTests
        from gah.run_evidence import _pack
        entry = self.seed.helper.bound["plan"]["entries"][0]
        attempt = AssuranceAuthorityTests._attempt(
            object(), {"manifest": self.seed.manifest, "plan": {"entries": [entry]}})
        attempt["started_at"] = 1000
        attempt["finished_at"] = 1000
        # Reopen the v6 authority before writing; setUp closed it after capturing the path.
        self.seed.store = self.seed.helper.open(PartitionedRunEvaluationExtension())
        try:
            self.seed.call(12003, "evidence_record_v2", "migration-rehashed-attempt",
                           run_id=self.run_id, attempt=attempt)
        finally:
            self.seed.store.close()
        changed = json.loads(json.dumps(attempt))
        changed["expected_binding"]["case_id"] = "not-in-the-plan"
        changed["result"]["binding"]["case_id"] = "not-in-the-plan"
        raw, digest = _pack(changed)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("UPDATE attempts SET attempt_json=?,attempt_digest=? WHERE attempt_id=?",
                       (raw, digest, changed["attempt_id"]))
            db.commit()
        before = _snapshot(self.path)
        with self.assertRaises(MigrationError) as caught:
            migrate_partitioned_corpus_store_v6_to_v7(self.path, expected_source_digest=self.source_digest)
        self.assertEqual(caught.exception.code, "STORAGE_CORRUPT")
        self.assertEqual(_snapshot(self.path), before)

    def test_failure_after_additive_ddl_rolls_back_schema_and_rows(self):
        from gah import partitioned_corpus_store
        before = _snapshot(self.path)
        original = partitioned_corpus_store.create_schema

        def fail_after_create(db):
            original(db)
            raise RuntimeError("injected migration failure")

        with patch.object(partitioned_corpus_store, "create_schema", fail_after_create):
            with self.assertRaises(MigrationError) as caught:
                migrate_partitioned_corpus_store_v6_to_v7(
                    self.path, expected_source_digest=self.source_digest)
        self.assertEqual(caught.exception.code, "MIGRATION_FAILED")
        self.assertEqual(_snapshot(self.path), before)


if __name__ == "__main__":
    unittest.main()
