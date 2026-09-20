"""Schema-v6 migration regression tests; run after the v6 source pin is fixed."""
from pathlib import Path
import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from gah.adoption import AdoptionError, AdoptionStore
from gah.adoption_migrations import MigrationError
from gah.evaluation_authority import EvaluationExtension
from gah.partitioned_authority import PartitionedEvaluationExtension
from gah.partitioned_run_migrations import migrate_partitioned_run_store_v5_to_v6
from gah.wire import canonical_bytes


class PartialRangeAdjacencyTests(unittest.TestCase):
    def test_adjacent_segments_cannot_leave_an_unassigned_entry(self):
        from gah.partitioned_run_migrations import _validate_partial_segment_ranges
        with self.assertRaisesRegex(MigrationError, "^STORAGE_CORRUPT$"):
            _validate_partial_segment_ranges([(0, 0, 2), (1, 3, 5)], 2, 5)


class AdoptionV6TypeGateTests(unittest.TestCase):
    def test_schema6_requires_exact_partitioned_run_extension_type(self):
        class ForgedV6:
            schema_version = 6
            tables = {}
            actions = {}
            fresh_actions = set()
            digest = "a" * 64
            def create_schema(self, db): pass
            def validate_request(self, value): return value
            def execute(self, *args): return {}
        with tempfile.TemporaryDirectory() as scratch:
            bad_path = Path(scratch) / "must-not-be-created.sqlite"
            with self.assertRaises(AdoptionError) as caught:
                AdoptionStore(bad_path, extension=ForgedV6())
            self.assertEqual(caught.exception.code, "EXTENSION_INVALID")
            self.assertFalse(bad_path.exists())


class PartialSegmentRangeTests(unittest.TestCase):
    def test_gap_must_leave_room_for_each_missing_segment(self):
        from gah.partitioned_run_migrations import _validate_partial_segment_ranges
        with self.assertRaises(MigrationError) as caught:
            _validate_partial_segment_ranges([(0, 0, 4), (2, 4, 5)], 3, 5)
        self.assertEqual(caught.exception.code, "STORAGE_CORRUPT")

    def test_prefix_suffix_and_nonadjacent_valid_ranges(self):
        from gah.partitioned_run_migrations import _validate_partial_segment_ranges
        _validate_partial_segment_ranges([(0, 0, 2), (2, 5, 9)], 3, 9)
        with self.assertRaises(MigrationError):
            _validate_partial_segment_ranges([(1, 0, 3)], 3, 9)
        with self.assertRaises(MigrationError):
            _validate_partial_segment_ranges([(2, 5, 8)], 3, 9)


class PartitionedRunMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "authority.sqlite"
        store = AdoptionStore(self.path, extension=PartitionedEvaluationExtension())
        self.source_digest = store._extension_digest
        store.close()
        payload = {"kept": True, "ordinal": 6}
        raw = canonical_bytes(payload).decode("utf-8")
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("INSERT INTO eval_objects(kind,id,digest,payload_json) VALUES(?,?,?,?)",
                       ("migration_probe", "kept-row", digest, raw))
            db.commit()
        self.before = self._rows()

    def _seed_adopted_v5(self):
        from tests.test_fixture_admission import FixtureAdmissionTests, request
        helper = FixtureAdmissionTests()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        store = AdoptionStore(self.path, clock=helper.clock,
                              bootstrap_policy=helper.policy,
                              extension=PartitionedEvaluationExtension())
        try:
            helper.adopt_policy(store)
            prepared = helper.prepare(store, run_id="migration-fixture")
            contract = prepared["prepared"]["bound_run"]["contract"]
            store.dispatch(12001, 12001, request(
                "contract_propose", "migration-v6-contract-propose",
                proposal_id="migration-v6-contract-proposal",
                series_id="migration-v6-contract-series", expected_generation=0,
                contract=contract))
            store.dispatch(12003, 12003, request(
                "contract_validate", "migration-v6-contract-validate",
                proposal_id="migration-v6-contract-proposal",
                validation_id="migration-v6-contract-validation"))
            store.dispatch(12001, 12001, request(
                "contract_adopt", "migration-v6-contract-adopt",
                proposal_id="migration-v6-contract-proposal",
                validation_id="migration-v6-contract-validation", expected_generation=0))
        finally:
            store.close()
        self.contract_series_id = "migration-v6-contract-series"
        with closing(sqlite3.connect(self.path)) as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT payload_json,digest FROM eval_adoptions WHERE series_id=? AND generation=1",
                             (self.contract_series_id,)).fetchone()
            self.contract = json.loads(row["payload_json"])
        return helper

    def _plan_artifacts(self, plan_id):
        from tests.test_run_contracts import _fixtures, _plan
        from gah.partitioned_trial_plan import partition_trial_plan
        from gah.run_contracts import content_ref
        from gah.partitioned_plan_store import _canonical
        fixtures = _fixtures()
        plan = _plan(fixtures)
        plan["plan_id"] = plan_id
        contract_ref = content_ref("evaluation_contract", self.contract["contract_id"], self.contract)
        plan["contract_ref"] = contract_ref
        index, segments = partition_trial_plan(plan)
        raw, digest, size = _canonical(index)
        return contract_ref, index, segments, raw, digest, size, _canonical

    def _insert_commit(self, *, corrupt_index=False, corrupt_segment=False):
        from gah.partitioned_plan_store import _canonical
        contract_ref, index, segments, raw, index_digest, size, _ = self._plan_artifacts("migration-v6-plan")
        contract_raw = json.dumps(contract_ref, sort_keys=True, separators=(",", ":"))
        with closing(sqlite3.connect(self.path)) as db:
            metadata = dict(db.execute("SELECT key,value FROM adoption_config"))
            db.execute("INSERT INTO partition_plan_commits VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (
                index["plan_id"], self.contract_series_id, 1, contract_raw, raw,
                "0" * 64 if corrupt_index else index_digest,
                size + sum(len(_canonical(item)[0].encode("utf-8")) for item in segments),
                "manager", "manager-context", 0, metadata["extension_digest"], 100))
            for segment_index, segment in enumerate(segments):
                segment_raw, segment_digest, byte_count = _canonical(segment)
                if corrupt_segment and segment_index == 0:
                    segment_digest = "0" * 64
                db.execute("INSERT INTO partition_plan_segments VALUES(?,?,?,?,?)", (
                    index["plan_id"], segment_index, segment_raw, segment_digest, byte_count))
            db.commit()

    def _rows(self):
        with closing(sqlite3.connect(self.path)) as db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            result = {}
            for table in sorted(tables):
                columns = [r[1] for r in db.execute(f'PRAGMA table_info("{table}")')]
                result[table] = (columns, db.execute(f'SELECT * FROM "{table}"').fetchall())
            return result

    def test_wrong_digest_rejects_without_mutation(self):
        with self.assertRaises(MigrationError) as caught:
            migrate_partitioned_run_store_v5_to_v6(self.path, expected_source_digest="0" * 64)
        self.assertEqual(caught.exception.code, "CONFIG_MISMATCH")
        self.assertEqual(self._rows(), self.before)

    def test_unknown_table_rejects_without_mutation(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("CREATE TABLE unexpected_table(value TEXT)")
            db.commit()
        before = self._rows()
        with self.assertRaises(MigrationError) as caught:
            migrate_partitioned_run_store_v5_to_v6(self.path, expected_source_digest=self.source_digest)
        self.assertEqual(caught.exception.code, "UNSUPPORTED_STORE")
        self.assertEqual(self._rows(), before)

    def test_v6_schema_adds_only_run_table_and_preserves_old_rows(self):
        result = migrate_partitioned_run_store_v5_to_v6(
            self.path, expected_source_digest=self.source_digest)
        self.assertEqual(result["schema_version"], 6)
        self.assertFalse(result["ci_eligible"])
        self.assertTrue(result["old_plan_rows_preserved"])
        after = self._rows()
        for table, old in self.before.items():
            expected = old
            if table == "adoption_config":
                expected = (old[0], [(key, value if key != "extension_digest" else result["extension_digest"])
                                     for key, value in old[1]])
            elif table == "adoption_meta":
                expected = (old[0], [(key, value if key != "schema_version" else 6)
                                     for key, value in old[1]])
            self.assertEqual(after[table], expected, table)
        self.assertEqual(after["eval_runs_v2"][1], [])
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 6)
            self.assertIsNone(db.execute("PRAGMA foreign_key_check").fetchone())

    def test_corrupt_committed_index_rolls_back_all_rows(self):
        self._seed_adopted_v5()
        self._insert_commit(corrupt_index=True)
        before = self._rows()
        with self.assertRaises(MigrationError) as caught:
            migrate_partitioned_run_store_v5_to_v6(self.path, expected_source_digest=self.source_digest)
        self.assertEqual(caught.exception.code, "STORAGE_CORRUPT")
        self.assertEqual(self._rows(), before)

    def test_corrupt_committed_segment_rolls_back_all_rows(self):
        self._seed_adopted_v5()
        self._insert_commit(corrupt_segment=True)
        before = self._rows()
        with self.assertRaises(MigrationError) as caught:
            migrate_partitioned_run_store_v5_to_v6(self.path, expected_source_digest=self.source_digest)
        self.assertEqual(caught.exception.code, "STORAGE_CORRUPT")
        self.assertEqual(self._rows(), before)

    def test_valid_partial_upload_is_preserved(self):
        self._seed_adopted_v5()
        contract_ref, index, _segments, raw, digest, size, _canonical = self._plan_artifacts("migration-v6-partial")
        contract_raw = json.dumps(contract_ref, sort_keys=True, separators=(",", ":"))
        with closing(sqlite3.connect(self.path)) as db:
            config = dict(db.execute("SELECT key,value FROM adoption_config"))
            db.execute("INSERT INTO partition_plan_upload VALUES(1,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                "migration-v6-upload", index["plan_id"], self.contract_series_id, 1,
                contract_raw, raw, digest, index["segment_count"], size,
                "manager", "manager-context", 0, config["extension_digest"], 100, 3700))
            db.commit()
        before_upload = self._rows()["partition_plan_upload"]
        result = migrate_partitioned_run_store_v5_to_v6(self.path, expected_source_digest=self.source_digest)
        self.assertEqual(result["schema_version"], 6)
        self.assertEqual(self._rows()["partition_plan_upload"], before_upload)
        self.assertEqual(self._rows()["partition_plan_segments"][1], [])

    def test_failure_after_ddl_rolls_back_schema_and_rows(self):
        from gah import partitioned_run_migrations as module
        from gah import partitioned_run_store
        create = partitioned_run_store.create_schema
        def fail_after_ddl(db):
            create(db)
            raise sqlite3.OperationalError("injected")
        with patch.object(partitioned_run_store, "create_schema", side_effect=fail_after_ddl):
            with self.assertRaises(MigrationError) as caught:
                migrate_partitioned_run_store_v5_to_v6(
                    self.path, expected_source_digest=self.source_digest)
        self.assertEqual(caught.exception.code, "MIGRATION_FAILED")
        self.assertEqual(self._rows(), self.before)


if __name__ == "__main__":
    unittest.main()
