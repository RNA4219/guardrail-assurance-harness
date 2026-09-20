"""診断readの認証、鮮度、DB版、既存採択への非干渉を検査する。"""
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from gah.adoption import AdoptionError, AdoptionStore
from gah.evaluation_authority import EvaluationExtension


class AuthorityDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.now = 1000
        self.store = AdoptionStore(Path(self.folder.name)/"authority.sqlite", clock=lambda:self.now,
                                   extension=EvaluationExtension())
        self.request = {"schema_version":1,"action":"authority_diagnostics","request_id":"diagnostic"}

    def tearDown(self):
        self.store.close()
        self.folder.cleanup()

    def test_fresh_read_returns_database_version_without_granting_ci(self):
        before = self.store._db.execute("SELECT COUNT(*) FROM eval_current").fetchone()[0]
        first = self.store.dispatch(12004,12004,self.request)
        self.assertEqual(first["database_schema_version"],4)
        self.assertEqual(first["permission_generation"],0)
        self.assertFalse(first["ci_eligible"])
        self.assertEqual(first["extension_digest"],EvaluationExtension.digest)
        self.now += 10
        second = self.store.dispatch(12004,12004,self.request)
        self.assertEqual(second["checked_at"],self.now)
        self.assertNotEqual(first["checked_at"],second["checked_at"])
        self.assertEqual(self.store._db.execute("SELECT COUNT(*) FROM eval_current").fetchone()[0],before)

    def test_candidate_unknown_fields_and_revoked_actor_are_rejected(self):
        with self.assertRaisesRegex(AdoptionError,"AUTHORITY_DENIED"):
            self.store.dispatch(12002,12002,self.request)
        with self.assertRaisesRegex(AdoptionError,"INVALID_REQUEST"):
            self.store.dispatch(12004,12004,{**self.request,"query":"schema"})
        self.store.dispatch(12004,12004,{"schema_version":1,"action":"revoke_actor",
            "request_id":"revoke-manager","actor_id":"manager"})
        with self.assertRaisesRegex(AdoptionError,"AUTHORITY_REVOKED"):
            self.store.dispatch(12001,12001,self.request)

    def test_changed_database_version_fails_read_in_existing_connection(self):
        self.store._db.execute("PRAGMA user_version=99")
        with self.assertRaisesRegex(AdoptionError,"UNSUPPORTED_STORE"):
            self.store.dispatch(12004,12004,self.request)


if __name__ == "__main__":
    unittest.main()
