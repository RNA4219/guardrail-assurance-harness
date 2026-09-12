"""既知旧v4から現行v4への明示的な互換upgradeを検査する。"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gah import adoption_migrations as migrations
from gah.adoption import AdoptionStore
from gah.evaluation_authority import EvaluationExtension
from gah.wire import canonical_bytes


class TransitionAcceptanceMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "old-v4.sqlite"
        shutil.copyfile(
            Path(__file__).resolve().parents[1]
            / ".ga/mvp-adoption-20260911/predecessor/v2-fixture.sqlite",
            self.path,
        )
        # 既知v2を現行コードでv4へ作成した後、固定された旧v4 digestへ戻す。
        # これは旧実運用DBの証拠ではなく、v4互換経路の合成fixtureである。
        migrations.migrate_evaluation_store(self.path)
        self._set_old_digest()

    def _set_old_digest(self):
        db = sqlite3.connect(self.path)
        try:
            db.execute("UPDATE adoption_config SET value=? WHERE key='extension_digest'",
                       (migrations._V4_PREDECESSOR_EXTENSION_DIGEST,))
            db.commit()
        finally:
            db.close()

    def _populated_candidate(self):
        spec = importlib.util.spec_from_file_location("migration_candidate_helpers",
            Path(__file__).parent / "test_transition_candidate_integration.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        fixture = module.TransitionCandidateIntegrationTests("runTest")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        base = fixture.fixture
        base.open = lambda: AdoptionStore(base.path, clock=base.clock, bootstrap_policy=base.policy,
            validator_digest=migrations._V2_VALIDATOR_DIGEST, extension=EvaluationExtension())
        store, _, _, candidate, _, values = fixture._arrange("migration-acceptance")
        store.dispatch(12004, 12004, {"schema_version": 1, "action": "contract_candidate_begin",
            "request_id": "migration-begin-old", "candidate_id": values["candidate_id"], "side": "old"})
        store.close()
        shutil.copyfile(base.path, self.path)
        self._set_old_digest()
        return base, candidate, values

    def _snapshot(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        try:
            tables = [row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            return {
                table: [tuple(row) for row in db.execute('SELECT * FROM "' + table + '" ORDER BY rowid')]
                for table in tables if table != "adoption_config"
            }
        finally:
            db.close()

    def test_known_old_v4_updates_only_extension_digest_and_preserves_rows(self):
        base, candidate, values = self._populated_candidate()
        before = self._snapshot()
        result = migrations.migrate_evaluation_store(self.path)
        self.assertEqual(result["schema_version"], 4)
        self.assertTrue(result["changed"])
        self.assertFalse(result["ci_eligible"])
        self.assertEqual(
            result["predecessor_extension_digest"],
            migrations._V4_PREDECESSOR_EXTENSION_DIGEST,
        )
        self.assertEqual(self._snapshot(), before)
        db = sqlite3.connect(self.path)
        try:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 4)
            self.assertEqual(
                db.execute("SELECT value FROM adoption_config WHERE key='extension_digest'").fetchone()[0],
                result["extension_digest"],
            )
        finally:
            db.close()
        with AdoptionStore(self.path, clock=base.clock, bootstrap_policy=base.policy,
                validator_digest=migrations._V2_VALIDATOR_DIGEST, extension=EvaluationExtension()) as reopened:
            from gah.transition_authority import load_candidate
            _, stored = load_candidate(reopened._db, values["candidate_id"], base.clock.value)
            self.assertEqual(stored["runs"], candidate["runs"])

    def test_unknown_old_v4_digest_is_rejected_without_change(self):
        db = sqlite3.connect(self.path)
        try:
            db.execute("UPDATE adoption_config SET value=? WHERE key='extension_digest'", ("f" * 64,))
            db.commit()
        finally:
            db.close()
        before = self.path.read_bytes()
        with self.assertRaisesRegex(migrations.MigrationError, "^CONFIG_MISMATCH$"):
            migrations.migrate_evaluation_store(self.path)
        self.assertEqual(self.path.read_bytes(), before)

    def test_candidate_materialization_rehash_is_rejected_without_change(self):
        _, _, values = self._populated_candidate()
        db = sqlite3.connect(self.path)
        try:
            value = json.loads(db.execute("SELECT payload_json FROM transition_candidates WHERE candidate_id=?",
                (values["candidate_id"],)).fetchone()[0])
            value["runs"]["old"]["materialization"]["manifest"]["created_at"] += 1
            raw = canonical_bytes(value)
            db.execute("UPDATE transition_candidates SET payload_json=?,digest=? WHERE candidate_id=?",
                (raw.decode(), hashlib.sha256(raw).hexdigest(), values["candidate_id"]))
            db.commit()
        finally:
            db.close()
        before = self.path.read_bytes()
        with self.assertRaisesRegex(migrations.MigrationError, "^STORAGE_CORRUPT$"):
            migrations.migrate_evaluation_store(self.path)
        self.assertEqual(self.path.read_bytes(), before)

    def test_gen2_current_falsely_marked_as_old_v4_is_rejected(self):
        raw = canonical_bytes({})
        digest = hashlib.sha256(raw).hexdigest()
        db = sqlite3.connect(self.path)
        try:
            db.execute("INSERT INTO eval_current VALUES(?,?,?,?,?,?)",
                       ("spoof-series", 2, "spoof-proposal", "spoof-validation",
                        raw.decode(), digest))
            db.execute("INSERT INTO eval_adoptions VALUES(?,?,?,?,?,?)",
                       ("spoof-series", 2, "spoof-proposal", "spoof-validation",
                        raw.decode(), digest))
            db.commit()
        finally:
            db.close()
        before = self.path.read_bytes()
        with self.assertRaisesRegex(migrations.MigrationError, "^STORAGE_CORRUPT$"):
            migrations.migrate_evaluation_store(self.path)
        self.assertEqual(self.path.read_bytes(), before)

    def test_validation_failure_rolls_back_extension_digest_update(self):
        db = sqlite3.connect(self.path)
        try:
            db.execute("CREATE TRIGGER reject_extension AFTER UPDATE OF value ON adoption_config "
                "WHEN NEW.key='extension_digest' BEGIN SELECT RAISE(ABORT,'injected'); END")
            db.commit()
        finally:
            db.close()
        before = self.path.read_bytes()
        with self.assertRaises(migrations.MigrationError):
            migrations.migrate_evaluation_store(self.path)
        self.assertEqual(self.path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
