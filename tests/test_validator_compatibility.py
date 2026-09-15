"""既知v4からの移行後も履歴を保持し、現在の権限・期限を検査する。"""
from pathlib import Path
from contextlib import closing
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from gah.adoption import AdoptionError, AdoptionStore, VALIDATION_TTL
from gah.adoption_migrations import migrate_evaluation_store, _V4_RUNTIME_VALIDATOR_DIGEST, _V4_LLM_INITIAL_EXTENSION_DIGEST
from gah.evaluation_authority import EvaluationExtension
from gah.policy import initial_policy_profile
from tests.test_adoption_extension import SampleExtension


class ValidatorCompatibilityTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "store.sqlite"
        self.now = 1000

    def open(self, **kwargs):
        return AdoptionStore(self.path, clock=lambda: self.now,
                             extension=EvaluationExtension(), **kwargs)

    def call(self, store, action, request_id, uid=12001, **fields):
        return store.dispatch(uid, uid, {"schema_version": 1, "action": action,
                              "request_id": request_id, **fields})

    def seed(self):
        with self.open(validator_digest=_V4_RUNTIME_VALIDATOR_DIGEST) as store:
            self.call(store, "propose", "p", proposal_id="p", series_id="policy",
                      expected_generation=0, policy=initial_policy_profile())
            self.call(store, "validate", "v", 12003, proposal_id="p", validation_id="v")
            self.receipt = self.call(store, "adopt", "a", proposal_id="p", validation_id="v", expected_generation=0)
            self.call(store, "propose", "pending", proposal_id="pending", series_id="policy",
                      expected_generation=1, policy=initial_policy_profile())
            self.call(store, "validate", "pending-v", 12003, proposal_id="pending", validation_id="pending-v")

    def current(self, store):
        return self.call(store, "current", "current", 12004, series_id="policy")

    def test_known_migrated_history_opens_with_default_factory_and_original_receipt(self):
        self.seed()
        with self.open() as store:
            self.assertTrue(self.current(store)["valid"])
            self.assertTrue(store._policy_at(store._db, "policy", 1, self.now)["valid"])
            self.assertEqual(self.call(store, "receipt", "receipt", 12004, adoption_request_id="a")["receipt"], self.receipt)
            self.assertEqual(dict(store._db.execute("SELECT key,value FROM adoption_config"))["validator_digest"], _V4_RUNTIME_VALIDATOR_DIGEST)
        with self.open() as store:
            self.assertTrue(self.current(store)["valid"])

    def test_official_initial_migration_can_be_opened_without_validator_override(self):
        self.seed()
        with closing(sqlite3.connect(self.path, isolation_level=None)) as db:
            db.execute("UPDATE adoption_config SET value=? WHERE key='extension_digest'", (_V4_LLM_INITIAL_EXTENSION_DIGEST,))
            # 既知移行元は資源・Evidence時計を初期化済み。
            for table in ("resource_meta", "run_evidence_meta"):
                db.execute("UPDATE " + table + " SET value=? WHERE key='last_clock'", (self.now,))
        with self.assertRaisesRegex(AdoptionError, "CONFIG_MISMATCH"):
            self.open()
        migrated = migrate_evaluation_store(self.path)
        self.assertTrue(migrated["changed"])
        self.assertFalse(migrated["ci_eligible"])
        with self.open() as store:
            self.assertTrue(self.current(store)["valid"])
            self.assertEqual(self.call(store, "receipt", "receipt", 12004, adoption_request_id="a")["receipt"], self.receipt)

    def test_pending_old_proposal_needs_new_proposal_and_validation(self):
        self.seed()
        with self.open() as store:
            with self.assertRaisesRegex(AdoptionError, "PROPOSAL_STALE"):
                self.call(store, "validate", "new-validation", 12003, proposal_id="pending", validation_id="new-validation")
            with self.assertRaisesRegex(AdoptionError, "PROPOSAL_STALE"):
                self.call(store, "adopt", "old-adopt", proposal_id="pending", validation_id="pending-v", expected_generation=1)
            self.call(store, "propose", "p2", proposal_id="p2", series_id="policy", expected_generation=1, policy=initial_policy_profile())
            self.call(store, "validate", "v2", 12003, proposal_id="p2", validation_id="v2")
            self.assertEqual(self.call(store, "adopt", "a2", proposal_id="p2", validation_id="v2", expected_generation=1)["generation"], 2)

    def test_expired_old_validation_stays_invalid(self):
        self.seed(); self.now += VALIDATION_TTL
        with self.open() as store:
            self.assertFalse(self.current(store)["valid"])
            self.assertFalse(store._policy_at(store._db, "policy", 1, self.now)["valid"])

    def test_revoked_old_validation_stays_invalid(self):
        self.seed()
        with self.open() as store:
            self.call(store, "revoke_validation", "revoke", 12004, validation_id="v")
            self.assertFalse(self.current(store)["valid"])
            self.assertFalse(store._policy_at(store._db, "policy", 1, self.now)["valid"])

    def test_actor_revocation_stays_invalid(self):
        self.seed()
        with self.open() as store:
            self.call(store, "revoke_actor", "revoke", 12004, actor_id="validator")
            self.assertFalse(self.current(store)["valid"])
            self.assertFalse(store._policy_at(store._db, "policy", 1, self.now)["valid"])

    def test_unknown_validator_is_rejected(self):
        with self.open(validator_digest="f" * 64):
            pass
        with self.assertRaisesRegex(AdoptionError, "CONFIG_MISMATCH"):
            self.open()

    def test_extension_digest_still_requires_explicit_migration(self):
        self.seed()
        with closing(sqlite3.connect(self.path, isolation_level=None)) as db:
            db.execute("UPDATE adoption_config SET value=? WHERE key='extension_digest'", ("f" * 64,))
        with self.assertRaisesRegex(AdoptionError, "CONFIG_MISMATCH"):
            self.open()

    def test_v4_predecessor_is_not_allowed_on_earlier_schema(self):
        for version in (1, 2, 3):
            with self.subTest(version=version):
                path = self.path.with_name(str(version) + ".sqlite")
                extension = None if version == 1 else SampleExtension()
                if extension is not None:
                    extension.schema_version = version
                with AdoptionStore(path, clock=lambda: self.now, extension=extension,
                                   validator_digest=_V4_RUNTIME_VALIDATOR_DIGEST):
                    pass
                with self.assertRaisesRegex(AdoptionError, "CONFIG_MISMATCH"):
                    AdoptionStore(path, clock=lambda: self.now, extension=extension)

    def test_changed_history_validator_is_rejected(self):
        self.seed()
        with self.open() as store:
            store._db.execute("UPDATE proposals SET validator_digest=? WHERE proposal_id='p'", ("f" * 64,))
            with self.assertRaisesRegex(AdoptionError, "STORAGE_CORRUPT"):
                self.current(store)
            with self.assertRaisesRegex(AdoptionError, "STORAGE_CORRUPT"):
                store._policy_at(store._db, "policy", 1, self.now)
