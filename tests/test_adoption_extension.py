import copy
from pathlib import Path
import sqlite3
import tempfile
import unittest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gah.adoption import AdoptionError, AdoptionStore
from gah.policy import initial_policy_profile


class Clock:
    def __init__(self, value=1000):
        self.value = value

    def __call__(self):
        return self.value


def req(action, request_id, **fields):
    return {"schema_version": 1, "action": action, "request_id": request_id, **fields}


class SampleExtension:
    digest = "e" * 64
    tables = {"extension_records": {"request_id", "actor_id", "context", "value", "seen_at"}}
    actions = {"reserve": {"manager"}, "status": {"manager", "validator", "operator"}}
    fresh_actions = {"status"}

    def create_schema(self, db):
        db.execute("""CREATE TABLE extension_records(
            request_id TEXT PRIMARY KEY, actor_id TEXT NOT NULL, context TEXT NOT NULL,
            value INTEGER NOT NULL, seen_at INTEGER NOT NULL)""")

    def validate_request(self, request):
        action = request.get("action")
        expected = {
            "reserve": {"schema_version", "action", "request_id", "value", "fail"},
            "status": {"schema_version", "action", "request_id"},
        }.get(action)
        if expected is None or set(request) != expected:
            raise AdoptionError("EXTENSION_REQUEST")
        if type(request["schema_version"]) is not int or request["schema_version"] != 1:
            raise AdoptionError("EXTENSION_REQUEST")
        if type(request["request_id"]) is not str or not request["request_id"]:
            raise AdoptionError("EXTENSION_REQUEST")
        if action == "reserve" and (type(request["value"]) is not int or type(request["fail"]) is not bool):
            raise AdoptionError("EXTENSION_REQUEST")
        return copy.deepcopy(request)

    def execute(self, store, db, request, actor_id, context, now):
        if request["action"] == "reserve":
            db.execute("INSERT INTO extension_records VALUES(?,?,?,?,?)",
                       (request["request_id"], actor_id, context, request["value"], now))
            if request["fail"]:
                raise AdoptionError("EXTENSION_ABORT")
            return {"schema_version": 1, "kind": "extension_result", "action": "reserve",
                    "request_id": request["request_id"], "ci_eligible": False,
                    "value": request["value"], "seen_at": now}
        count = db.execute("SELECT count(*) FROM extension_records").fetchone()[0]
        return {"schema_version": 1, "kind": "extension_result", "action": "status",
                "request_id": request["request_id"], "ci_eligible": False, "count": count}


class AdoptionExtensionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "adoption.sqlite"
        self.clock = Clock()
        self.policy = initial_policy_profile()
        self.extension = SampleExtension()

    def open(self, extension=None):
        return AdoptionStore(self.path, clock=self.clock, bootstrap_policy=self.policy,
                             validator_digest="b" * 64, extension=self.extension if extension is None else extension)

    def test_v2_schema_and_extension_request_auth_atomicity(self):
        with self.open() as store:
            self.assertEqual(store._db.execute("PRAGMA user_version").fetchone()[0], 2)
            self.assertEqual(store._db.execute("SELECT value FROM adoption_meta WHERE key='schema_version'").fetchone()[0], 2)
            config = dict(store._db.execute("SELECT key,value FROM adoption_config").fetchall())
            self.assertEqual(config["extension_digest"], self.extension.digest)
            stored = store.dispatch(12001, 12001, req("reserve", "reserve-1", value=7, fail=False))
            self.assertEqual(stored["value"], 7)
            self.assertEqual(store.dispatch(12001, 12001, req("reserve", "reserve-1", value=7, fail=False)), stored)
            with self.assertRaisesRegex(AdoptionError, "^AUTHORITY_DENIED$"):
                store.dispatch(12003, 12003, req("reserve", "validator-reserve", value=1, fail=False))
            with self.assertRaisesRegex(AdoptionError, "^INVALID_ACTION$"):
                store.dispatch(12001, 12001, req([], "unhashable-action"))
            with self.assertRaisesRegex(AdoptionError, "^EXTENSION_ABORT$"):
                store.dispatch(12001, 12001, req("reserve", "reserve-fail", value=9, fail=True))
            self.assertIsNone(store._db.execute(
                "SELECT 1 FROM extension_records WHERE request_id='reserve-fail'").fetchone())
            store.dispatch(12004, 12004, req("revoke_actor", "revoke-manager", actor_id="manager"))
            with self.assertRaisesRegex(AdoptionError, "^AUTHORITY_REVOKED$"):
                store.dispatch(12001, 12001, req("status", "revoked-status"))

    def test_fresh_action_recomputes_and_clock_is_shared(self):
        with self.open() as store:
            first = store.dispatch(12004, 12004, req("status", "status-1"))
            self.assertEqual(first["count"], 0)
            store.dispatch(12001, 12001, req("reserve", "reserve-1", value=3, fail=False))
            second = store.dispatch(12004, 12004, req("status", "status-1"))
            self.assertEqual(second["count"], 1)
            self.clock.value -= 1
            with self.assertRaisesRegex(AdoptionError, "^CLOCK_ROLLBACK$"):
                store.dispatch(12004, 12004, req("status", "status-2"))

    def test_v1_is_not_migrated_or_modified_by_extension_open(self):
        with AdoptionStore(self.path, clock=self.clock, bootstrap_policy=self.policy,
                           validator_digest="b" * 64):
            pass
        with self.assertRaisesRegex(AdoptionError, "^UNSUPPORTED_STORE$"):
            self.open()
        db = sqlite3.connect(self.path)
        try:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertNotIn("extension_records", {
                row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")})
        finally:
            db.close()

    def test_extension_metadata_and_schema_corruption_are_rejected(self):
        with self.open() as store:
            store._db.execute("CREATE TABLE unexpected(id INTEGER)")
        with self.assertRaisesRegex(AdoptionError, "^UNSUPPORTED_STORE$"):
            self.open()

        class BadRole(SampleExtension):
            actions = {"reserve": {"unknown"}}
        with self.assertRaisesRegex(AdoptionError, "^EXTENSION_INVALID$"):
            AdoptionStore(self.path.with_name("bad.sqlite"), clock=self.clock,
                          bootstrap_policy=self.policy, validator_digest="b" * 64,
                          extension=BadRole())


if __name__ == "__main__":
    unittest.main()
