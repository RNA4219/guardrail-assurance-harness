"""既知の後続/限定run版からの移行を合成SQLite fixtureで検査する。"""
from contextlib import closing, contextmanager
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gah import adoption_migrations as migrations
from gah.evaluation_authority import EvaluationExtension
from gah.resources import _packed
from gah.wire import canonical_bytes

spec = importlib.util.spec_from_file_location("migration_scope_seed", ROOT / "tests/test_run_scope.py")
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)


@contextmanager
def transaction(path):
    with closing(sqlite3.connect(path)) as db:
        with db:
            yield db


def rows(db):
    return {table: sorted([list(row) for row in db.execute('SELECT * FROM "' + table + '"')], key=canonical_bytes)
            for table in migrations._V4_COLUMNS if table != "adoption_config"}


class FollowingMigrationTests(unittest.TestCase):
    open = helpers.ScopedRunIntegrationTests.open

    @classmethod
    def setUpClass(cls):
        helpers.ScopedRunIntegrationTests.setUpClass()
        cls.addClassCleanup(helpers.ScopedRunIntegrationTests.doClassCleanups)
        helper = helpers.ScopedRunIntegrationTests("runTest")
        helper.setUp(); cls.addClassCleanup(helper.doCleanups)
        _, cls.prepared, _ = helper.scoped("migration-targeted")
        helper.complete(cls.prepared)
        # 開始前に保存されたscopeも検査対象にする。
        helper.scoped("prepared-only")
        cls.at = helper.clock.value
        cls.temp = tempfile.TemporaryDirectory(); cls.addClassCleanup(cls.temp.cleanup)
        cls.seed_path = Path(cls.temp.name) / "seed.sqlite"
        with closing(sqlite3.connect(cls.seed_path)) as target:
            helper.store._db.backup(target)

    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "migration.sqlite"
        with closing(sqlite3.connect(self.seed_path)) as source, closing(sqlite3.connect(self.path)) as target:
            source.backup(target)
        self.clock = helpers.seed.Clock(self.at)
        self.store = self.open()
        self.store._db.execute("UPDATE adoption_config SET value=? WHERE key='extension_digest'", (migrations._V4_SCOPED_EXTENSION_DIGEST,))
        self.before = rows(self.store._db)
        self.store.close()

    def unchanged(self):
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(rows(db), self.before)
            self.assertEqual(db.execute("SELECT value FROM adoption_config WHERE key='extension_digest'").fetchone()[0], migrations._V4_SCOPED_EXTENSION_DIGEST)

    def test_scoped_completed_and_prepared_only_rows_survive_without_rewriting_history(self):
        result = migrations.migrate_evaluation_store(self.path)
        self.assertFalse(result["ci_eligible"])
        self.assertEqual(result["predecessor_extension_digest"], migrations._V4_SCOPED_EXTENSION_DIGEST)
        self.store = self.open()
        self.assertEqual(rows(self.store._db), self.before)
        gate = helpers.seed.RegressionIntegrationTests.gate_request(self, self.prepared)
        self.assertEqual(self.store.dispatch(12004, 12004, gate)["exit_code"], 0)

    def test_preceding_capability_cannot_contain_scope_records(self):
        with transaction(self.path) as db:
            db.execute("UPDATE adoption_config SET value=? WHERE key='extension_digest'", (migrations._V4_FOLLOWING_EXTENSION_DIGEST,))
        with self.assertRaisesRegex(migrations.MigrationError, "STORAGE_CORRUPT"):
            migrations.migrate_evaluation_store(self.path)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT value FROM adoption_config WHERE key='extension_digest'").fetchone()[0], migrations._V4_FOLLOWING_EXTENSION_DIGEST)

    def test_unknown_validator_pair_is_rejected(self):
        with transaction(self.path) as db:
            db.execute("UPDATE adoption_config SET value=? WHERE key='validator_digest'", ("f" * 64,))
        with self.assertRaisesRegex(migrations.MigrationError, "CONFIG_MISMATCH"):
            migrations.migrate_evaluation_store(self.path)
        self.unchanged()

    def test_scope_actor_and_duplicate_request_identity_are_rejected(self):
        with transaction(self.path) as db:
            db.execute("UPDATE idempotency SET actor_id='manager' WHERE request_id='scope-prepare-prepared-only'")
        with self.assertRaisesRegex(migrations.MigrationError, "STORAGE_CORRUPT"):
            migrations.migrate_evaluation_store(self.path)
        with transaction(self.path) as db:
            db.execute("UPDATE idempotency SET actor_id='operator' WHERE request_id='scope-prepare-prepared-only'")
            db.execute("INSERT INTO idempotency SELECT 'unrelated-scope-copy',request_digest,actor_id,context,response_json,response_digest FROM idempotency WHERE request_id='scope-prepare-prepared-only'")
        with self.assertRaisesRegex(migrations.MigrationError, "STORAGE_CORRUPT"):
            migrations.migrate_evaluation_store(self.path)

    def test_verification_clock_advance_is_discarded(self):
        with transaction(self.path) as db:
            db.execute("UPDATE adoption_meta SET value=value+1 WHERE key='last_clock'")
            db.execute("UPDATE resource_meta SET value=value-1 WHERE key='last_clock'")
            self.before = rows(db)
        migrations.migrate_evaluation_store(self.path)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(rows(db), self.before)

    def test_unexpected_verification_write_is_rejected_and_rolled_back(self):
        original = migrations._verify_v3_json_rows
        def mutated(db, **kwargs):
            original(db, **kwargs)
            db.execute("UPDATE adoption_meta SET value=value+1 WHERE key='last_clock'")
        with mock.patch.object(migrations, "_verify_v3_json_rows", side_effect=mutated):
            with self.assertRaisesRegex(migrations.MigrationError, "STORAGE_CORRUPT"):
                migrations.migrate_evaluation_store(self.path)
        self.unchanged()

    def test_update_failure_rolls_back_all_rows_and_can_retry(self):
        with transaction(self.path) as db:
            db.execute("CREATE TRIGGER reject_upgrade BEFORE UPDATE ON adoption_config BEGIN SELECT RAISE(ABORT,'synthetic'); END")
        with self.assertRaisesRegex(migrations.MigrationError, "MIGRATION_FAILED"):
            migrations.migrate_evaluation_store(self.path)
        self.unchanged()
        with transaction(self.path) as db: db.execute("DROP TRIGGER reject_upgrade")
        self.assertTrue(migrations.migrate_evaluation_store(self.path)["changed"])


if __name__ == "__main__":
    unittest.main()
