"""AdoptionStoreの明示的なv2/v3からv4への移行を検査する。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest

from tests.legacy_adoption_fixture import create_legacy_adoption_fixture
from unittest import mock

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gah import adoption_migrations as migrations
from gah.adoption import AdoptionStore
from gah.evaluation_authority import EvaluationExtension
from gah.policy import initial_policy_profile


class MigrationExtension:
    """テスト専用の、固定されたv3拡張。"""

    schema_version = 4
    digest = "3" * 64
    tables = {
        **{name: set(columns) for name, columns in migrations._V2_COLUMNS.items()},
        "authority_evidence": {"evidence_id", "payload_json", "payload_digest"},
        "policy_adoptions": set(migrations._POLICY_ADOPTION_COLUMNS),
        **{name: set(columns) for name, columns in migrations.transition_migrations.TABLES.items()},
    }

    def migrate_schema(self, db):
        db.execute(
            """CREATE TABLE authority_evidence(
                evidence_id TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                payload_digest TEXT NOT NULL)"""
        )
        db.execute(
            """CREATE TABLE policy_adoptions(
                series_id TEXT NOT NULL, generation INTEGER NOT NULL,
                proposal_id TEXT NOT NULL, validation_id TEXT NOT NULL,
                policy_json TEXT NOT NULL, policy_digest TEXT NOT NULL,
                adopted_at INTEGER NOT NULL, actor_id TEXT NOT NULL,
                context TEXT NOT NULL, PRIMARY KEY(series_id,generation))"""
        )


class FailingMigrationExtension(MigrationExtension):
    def migrate_schema(self, db):
        db.execute("CREATE TABLE partial_migration(value INTEGER NOT NULL)")
        raise RuntimeError("failure")


class LegacyExtension:
    """v2拡張が自動移行されないことを表すテストdouble。"""

    schema_version = 2


class AdoptionMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "adoption.sqlite"
        create_legacy_adoption_fixture(self.path)

    def _migrate(self, extension=None):
        extension = extension or MigrationExtension()
        with mock.patch.object(migrations, "_trusted_extension", return_value=extension):
            return migrations.migrate_evaluation_store(self.path)

    def _tables(self):
        db = sqlite3.connect(self.path)
        try:
            return {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            db.close()

    def _synthetic_v3(self, *, extension_digest=migrations._V3_EXTENSION_DIGEST):
        """現行の空DBから遷移表だけを除いた合成v3 DBを作る。"""
        path = Path(self.temp.name) / "synthetic-v3.sqlite"
        with AdoptionStore(path, validator_digest=migrations._V2_VALIDATOR_DIGEST,
                           extension=EvaluationExtension()):
            pass
        db = sqlite3.connect(path)
        try:
            db.execute("PRAGMA foreign_keys=OFF")
            for table in ("transition_runs", "transition_candidates"):
                db.execute("DROP TABLE IF EXISTS " + table)
            db.execute("UPDATE adoption_meta SET value=3 WHERE key='schema_version'")
            db.execute("UPDATE adoption_config SET value=? WHERE key='extension_digest'",
                       (extension_digest,))
            db.execute("PRAGMA user_version=3")
            db.commit()
        finally:
            db.close()
        return path

    def test_known_predecessor_is_migrated_atomically(self):
        before = self.path.read_bytes()
        result = self._migrate()
        self.assertEqual(result["kind"], "adoption_migration_result")
        self.assertFalse(result["ci_eligible"])
        self.assertEqual(result["predecessor_extension_digest"], migrations._V2_EXTENSION_DIGEST)
        self.assertEqual(result["extension_digest"], MigrationExtension.digest)
        db = sqlite3.connect(self.path)
        try:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 4)
            self.assertEqual(
                db.execute("SELECT value FROM adoption_meta WHERE key='schema_version'").fetchone()[0], 4
            )
            self.assertEqual(
                db.execute("SELECT value FROM adoption_config WHERE key='extension_digest'").fetchone()[0],
                MigrationExtension.digest,
            )
        finally:
            db.close()
        self.assertNotEqual(before, self.path.read_bytes())
        self.assertEqual(self._tables(), set(MigrationExtension.tables))

    def test_old_rows_are_not_rewritten_and_old_validator_is_retained(self):
        db = sqlite3.connect(self.path)
        try:
            payload = {"kind": "policy_profile", "value": 1}
            raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            digest = hashlib.sha256(raw.encode()).hexdigest()
            db.execute(
                "INSERT INTO eval_objects(kind,id,digest,payload_json) VALUES(?,?,?,?)",
                ("artifact", "old-object", digest, raw),
            )
            db.execute(
                "INSERT INTO proposals VALUES(?,?,?,?,?,?,?,?,?,?)",
                ("proposal", "policy-series", 0, raw, digest, 1, "actor", "context",
                 migrations._V2_BOOTSTRAP_DIGEST, migrations._V2_VALIDATOR_DIGEST),
            )
            db.execute(
                "INSERT INTO validations VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                ("validation", "proposal", digest, 0, 1, 100, "validator", "validator-context",
                 0, migrations._V2_BOOTSTRAP_DIGEST, migrations._V2_VALIDATOR_DIGEST),
            )
            db.execute(
                "INSERT INTO current_profiles VALUES(?,?,?,?,?,?,?,?,?)",
                ("policy-series", 1, "proposal", "validation", raw, digest, 10, "actor", "context"),
            )
            db.commit()
        finally:
            db.close()
        self._migrate()
        db = sqlite3.connect(self.path)
        try:
            row = db.execute("SELECT kind,id,digest,payload_json FROM eval_objects").fetchone()
            self.assertEqual(row, ("artifact", "old-object", digest, raw))
            self.assertEqual(
                db.execute("SELECT series_id,generation,proposal_id,validation_id,policy_json,"
                           "policy_digest,adopted_at,actor_id,context FROM policy_adoptions").fetchone(),
                ("policy-series", 1, "proposal", "validation", raw, digest, 10, "actor", "context"),
            )
            self.assertEqual(
                db.execute("SELECT value FROM adoption_config WHERE key='validator_digest'").fetchone()[0],
                migrations._V2_VALIDATOR_DIGEST,
            )
        finally:
            db.close()

    def test_migration_is_explicit_and_current_extension_without_v3_is_rejected(self):
        with mock.patch.object(
                migrations, "_trusted_extension",
                side_effect=migrations.MigrationError("MIGRATION_UNAVAILABLE")):
            with self.assertRaisesRegex(migrations.MigrationError, "^MIGRATION_UNAVAILABLE$"):
                migrations.migrate_evaluation_store(self.path)
        db = sqlite3.connect(self.path)
        try:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 2)
        finally:
            db.close()
        self.assertNotEqual(self._tables(), set(MigrationExtension.tables))

    def test_unknown_config_is_rejected_without_change(self):
        before = self.path.read_bytes()
        db = sqlite3.connect(self.path)
        try:
            db.execute("UPDATE adoption_config SET value=? WHERE key='validator_digest'", ("f" * 64,))
            db.commit()
        finally:
            db.close()
        changed = self.path.read_bytes()
        with self.assertRaisesRegex(migrations.MigrationError, "^CONFIG_MISMATCH$"):
            self._migrate()
        self.assertEqual(self.path.read_bytes(), changed)
        self.assertNotEqual(before, changed)

    def test_unknown_table_is_rejected(self):
        db = sqlite3.connect(self.path)
        try:
            db.execute("CREATE TABLE untrusted_table(value TEXT)")
            db.commit()
        finally:
            db.close()
        with self.assertRaisesRegex(migrations.MigrationError, "^UNSUPPORTED_STORE$"):
            self._migrate()

    def test_corrupt_canonical_payload_is_rejected_and_not_migrated(self):
        db = sqlite3.connect(self.path)
        try:
            db.execute(
                "INSERT INTO eval_objects(kind,id,digest,payload_json) VALUES(?,?,?,?)",
                ("artifact", "bad-object", "0" * 64, '{"value":1}'),
            )
            db.commit()
        finally:
            db.close()
        with self.assertRaisesRegex(migrations.MigrationError, "^STORAGE_CORRUPT$"):
            self._migrate()
        self.assertEqual(self._tables(), migrations._V2_TABLES)
        db = sqlite3.connect(self.path)
        try:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 2)
        finally:
            db.close()

    def test_current_profile_reference_mismatch_is_rejected(self):
        db = sqlite3.connect(self.path)
        try:
            payload = {"kind": "policy_profile", "value": 1}
            raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            digest = hashlib.sha256(raw.encode()).hexdigest()
            db.execute("INSERT INTO current_profiles VALUES(?,?,?,?,?,?,?,?,?)",
                       ("series", 1, "missing-proposal", "missing-validation", raw, digest, 1, "actor", "context"))
            db.commit()
        finally:
            db.close()
        with self.assertRaisesRegex(migrations.MigrationError, "^STORAGE_CORRUPT$"):
            self._migrate()

    def test_extension_failure_rolls_back_partial_schema(self):
        with self.assertRaisesRegex(migrations.MigrationError, "^MIGRATION_FAILED$"):
            self._migrate(FailingMigrationExtension())
        self.assertEqual(self._tables(), migrations._V2_TABLES)
        db = sqlite3.connect(self.path)
        try:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 2)
        finally:
            db.close()

    def test_v3_adoption_appends_an_immutable_policy_history_row(self):
        path = Path(self.temp.name) / "fresh-v3.sqlite"
        policy = initial_policy_profile()
        with AdoptionStore(path, bootstrap_policy=policy,
                           validator_digest=migrations._V2_VALIDATOR_DIGEST,
                           extension=EvaluationExtension()) as store:
            def request(action, request_id, **fields):
                return {"schema_version": 1, "action": action, "request_id": request_id, **fields}

            store.dispatch(12001, 12001, request(
                "propose", "history-propose", proposal_id="history-proposal",
                series_id="history-series", expected_generation=0, policy=policy))
            store.dispatch(12003, 12003, request(
                "validate", "history-validate", proposal_id="history-proposal",
                validation_id="history-validation"))
            adopted = store.dispatch(12001, 12001, request(
                "adopt", "history-adopt", proposal_id="history-proposal",
                validation_id="history-validation", expected_generation=0))
            row = store._db.execute(
                "SELECT generation,proposal_id,validation_id,policy_digest FROM policy_adoptions "
                "WHERE series_id=?", ("history-series",)
            ).fetchone()
            self.assertEqual(row[0], adopted["generation"])
            self.assertEqual(row[1:3], ("history-proposal", "history-validation"))
            self.assertEqual(row[3], adopted["proposal_digest"])
            first = store._policy_at(store._db, "history-series", 1, 1000)
            self.assertTrue(first["valid"])
            store.dispatch(12004, 12004, request(
                "revoke_validation", "history-revoke", validation_id="history-validation"))
            revoked = store._policy_at(store._db, "history-series", 1, 1000)
            self.assertFalse(revoked["valid"])
            self.assertEqual(revoked["policy_digest"], first["policy_digest"])
            store.dispatch(12001, 12001, request(
                "propose", "history-propose-2", proposal_id="history-proposal-2",
                series_id="history-series", expected_generation=1, policy=policy))
            store.dispatch(12003, 12003, request(
                "validate", "history-validate-2", proposal_id="history-proposal-2",
                validation_id="history-validation-2"))
            store.dispatch(12001, 12001, request(
                "adopt", "history-adopt-2", proposal_id="history-proposal-2",
                validation_id="history-validation-2", expected_generation=1))
            self.assertEqual(store._db.execute(
                "SELECT COUNT(*) FROM policy_adoptions WHERE series_id=?", ("history-series",)
            ).fetchone()[0], 2)
            self.assertTrue(store._policy_at(store._db, "history-series", 2, 1000)["valid"])

    def test_known_synthetic_v3_is_upgraded_to_v4_with_empty_transition_tables(self):
        path = self._synthetic_v3()
        result = migrations.migrate_evaluation_store(path)
        self.assertEqual(result["schema_version"], 4)
        self.assertFalse(result["ci_eligible"])
        self.assertEqual(result["predecessor_extension_digest"], migrations._V3_EXTENSION_DIGEST)
        db = sqlite3.connect(path)
        try:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 4)
            self.assertEqual(
                db.execute("SELECT value FROM adoption_meta WHERE key='schema_version'").fetchone()[0], 4
            )
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM transition_candidates").fetchone()[0], 0
            )
            self.assertEqual(db.execute("SELECT COUNT(*) FROM transition_runs").fetchone()[0], 0)
        finally:
            db.close()

    def test_v3_policy_current_and_history_are_rechecked_as_a_chain(self):
        """policyの現行行・履歴・proposal・validationが揃うv3を受け入れる。"""
        path = Path(self.temp.name) / "policy-v3.sqlite"
        policy = initial_policy_profile()
        with AdoptionStore(path, bootstrap_policy=policy,
                           validator_digest=migrations._V2_VALIDATOR_DIGEST,
                           extension=EvaluationExtension()) as store:
            def request(action, request_id, **fields):
                return {"schema_version": 1, "action": action, "request_id": request_id, **fields}

            store.dispatch(12001, 12001, request(
                "propose", "migration-propose", proposal_id="migration-proposal",
                series_id="migration-series", expected_generation=0, policy=policy))
            store.dispatch(12003, 12003, request(
                "validate", "migration-validate", proposal_id="migration-proposal",
                validation_id="migration-validation"))
            store.dispatch(12001, 12001, request(
                "adopt", "migration-adopt", proposal_id="migration-proposal",
                validation_id="migration-validation", expected_generation=0))
        db = sqlite3.connect(path)
        try:
            db.execute("PRAGMA foreign_keys=OFF")
            db.execute("DROP TABLE transition_runs")
            db.execute("DROP TABLE transition_candidates")
            db.execute("UPDATE adoption_meta SET value=3 WHERE key='schema_version'")
            db.execute("UPDATE adoption_config SET value=? WHERE key='extension_digest'",
                       (migrations._V3_EXTENSION_DIGEST,))
            db.execute("PRAGMA user_version=3")
            db.commit()
        finally:
            db.close()
        result = migrations.migrate_evaluation_store(path)
        self.assertEqual(result["schema_version"], 4)

    def test_v3_policy_history_proposal_digest_tamper_is_rejected(self):
        """v3のvalidationが別proposalを指す場合は移行しない。"""
        path = Path(self.temp.name) / "policy-tampered-v3.sqlite"
        policy = initial_policy_profile()
        with AdoptionStore(path, bootstrap_policy=policy,
                           validator_digest=migrations._V2_VALIDATOR_DIGEST,
                           extension=EvaluationExtension()) as store:
            def request(action, request_id, **fields):
                return {"schema_version": 1, "action": action, "request_id": request_id, **fields}

            store.dispatch(12001, 12001, request(
                "propose", "tamper-propose", proposal_id="tamper-proposal",
                series_id="tamper-series", expected_generation=0, policy=policy))
            store.dispatch(12003, 12003, request(
                "validate", "tamper-validate", proposal_id="tamper-proposal",
                validation_id="tamper-validation"))
            store.dispatch(12001, 12001, request(
                "adopt", "tamper-adopt", proposal_id="tamper-proposal",
                validation_id="tamper-validation", expected_generation=0))
        db = sqlite3.connect(path)
        try:
            db.execute("PRAGMA foreign_keys=OFF")
            db.execute("UPDATE validations SET proposal_digest=? WHERE validation_id=?",
                       ("f" * 64, "tamper-validation"))
            db.execute("DROP TABLE transition_runs")
            db.execute("DROP TABLE transition_candidates")
            db.execute("UPDATE adoption_meta SET value=3 WHERE key='schema_version'")
            db.execute("UPDATE adoption_config SET value=? WHERE key='extension_digest'",
                       (migrations._V3_EXTENSION_DIGEST,))
            db.execute("PRAGMA user_version=3")
            db.commit()
        finally:
            db.close()
        with self.assertRaisesRegex(migrations.MigrationError, "^STORAGE_CORRUPT$"):
            migrations.migrate_evaluation_store(path)

    def test_unknown_v3_digest_is_rejected_without_schema_change(self):
        path = self._synthetic_v3(extension_digest="f" * 64)
        before = path.read_bytes()
        with self.assertRaisesRegex(migrations.MigrationError, "^CONFIG_MISMATCH$"):
            migrations.migrate_evaluation_store(path)
        self.assertEqual(path.read_bytes(), before)

    def test_unknown_schema_version_is_rejected_without_schema_change(self):
        path = self._synthetic_v3()
        db = sqlite3.connect(path)
        try:
            db.execute("UPDATE adoption_meta SET value=5 WHERE key='schema_version'")
            db.execute("PRAGMA user_version=5")
            db.commit()
        finally:
            db.close()
        before = path.read_bytes()
        with self.assertRaisesRegex(migrations.MigrationError, "^UNSUPPORTED_STORE$"):
            migrations.migrate_evaluation_store(path)
        self.assertEqual(path.read_bytes(), before)

    def test_v3_transition_ddl_failure_rolls_back_everything(self):
        path = self._synthetic_v3()
        before = path.read_bytes()

        def fail_after_first_table(db):
            db.execute(
                "CREATE TABLE transition_candidates(candidate_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL)"
            )
            raise RuntimeError("forced migration failure")

        with mock.patch.object(migrations.transition_migrations, "create_schema", side_effect=fail_after_first_table):
            with self.assertRaisesRegex(migrations.MigrationError, "^MIGRATION_FAILED$"):
                migrations.migrate_evaluation_store(path)
        self.assertEqual(path.read_bytes(), before)
        db = sqlite3.connect(path)
        try:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 3)
            self.assertIsNone(db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='transition_candidates'"
            ).fetchone())
            self.assertIsNone(db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='transition_runs'"
            ).fetchone())
        finally:
            db.close()

    def test_v3_history_without_current_is_rejected(self):
        path = self._synthetic_v3()
        raw = "{}"
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        db = sqlite3.connect(path)
        try:
            db.execute(
                "INSERT INTO eval_adoptions VALUES(?,?,?,?,?,?)",
                ("orphan-series", 1, "proposal", "validation", raw, digest),
            )
            db.commit()
        finally:
            db.close()
        with self.assertRaisesRegex(migrations.MigrationError, "^STORAGE_CORRUPT$"):
            migrations.migrate_evaluation_store(path)

    def test_invalid_path_is_rejected_without_opening_a_database(self):
        with self.assertRaisesRegex(migrations.MigrationError, "^INVALID_PATH$"):
            migrations.migrate_evaluation_store(object())
        with self.assertRaisesRegex(migrations.MigrationError, "^STORE_MISSING$"):
            migrations.migrate_evaluation_store(Path(self.temp.name) / "missing.sqlite")


if __name__ == "__main__":
    unittest.main()
