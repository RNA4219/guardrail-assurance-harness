import copy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import threading
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


class AdoptionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "adoption.sqlite"
        self.clock = Clock()
        self.policy = initial_policy_profile()
        self.validator_digest = "b" * 64

    def open(self):
        return AdoptionStore(self.path, clock=self.clock, bootstrap_policy=self.policy,
                             validator_digest=self.validator_digest)

    def propose(self, store, request_id="p-request", proposal_id="proposal-1", series_id="series-1"):
        return store.dispatch(12001, 12001, req(
            "propose", request_id, proposal_id=proposal_id, series_id=series_id,
            expected_generation=0, policy=copy.deepcopy(self.policy)))

    def validate(self, store, request_id="v-request", proposal_id="proposal-1", validation_id="validation-1"):
        return store.dispatch(12003, 12003, req(
            "validate", request_id, proposal_id=proposal_id, validation_id=validation_id))

    def adopt(self, store, request_id="a-request", proposal_id="proposal-1", validation_id="validation-1"):
        return store.dispatch(12001, 12001, req(
            "adopt", request_id, proposal_id=proposal_id, validation_id=validation_id,
            expected_generation=0))

    def test_propose_validate_adopt_current_and_restart(self):
        with self.open() as store:
            proposed = self.propose(store)
            self.assertEqual(proposed["action"], "propose")
            self.assertFalse(proposed["ci_eligible"])
            validated = self.validate(store)
            self.assertTrue(validated["passed"])
            adopted = self.adopt(store)
            self.assertEqual(adopted["generation"], 1)
            for field in ("actor_id", "context", "expected_generation", "permission_generation",
                          "bootstrap_digest", "validator_digest", "validation_observed_at",
                          "validation_expires_at", "validation_revocation_generation"):
                self.assertIn(field, adopted)
            current = store.dispatch(12004, 12004, req("current", "c-request", series_id="series-1"))
            self.assertTrue(current["adopted"])
            self.assertTrue(current["valid"])
            self.assertEqual(current["policy_digest"], adopted["proposal_digest"])
        with self.open() as store:
            replay = store.dispatch(12001, 12001, req(
                "adopt", "a-request", proposal_id="proposal-1", validation_id="validation-1",
                expected_generation=0))
            self.assertEqual(replay, adopted)
            receipt = store.dispatch(12003, 12003, req(
                "receipt", "r-request", adoption_request_id="a-request"))
            self.assertEqual(receipt["receipt"]["action"], "adopt")

            second = store.dispatch(12001, 12001, req(
                "propose", "p2-request", proposal_id="proposal-2", series_id="series-1",
                expected_generation=1, policy=copy.deepcopy(self.policy)))
            self.assertEqual(second["expected_generation"], 1)
            self.validate(store, request_id="v2-request", proposal_id="proposal-2", validation_id="validation-2")
            adopted_again = store.dispatch(12001, 12001, req(
                "adopt", "a2-request", proposal_id="proposal-2", validation_id="validation-2",
                expected_generation=1))
            self.assertEqual(adopted_again["generation"], 2)

    def test_peer_roles_and_request_shapes_are_enforced(self):
        with self.open() as store:
            with self.assertRaisesRegex(AdoptionError, "^AUTHORITY_MISSING$"):
                store.dispatch(999, 999, req("current", "unknown", series_id="series-1"))
            with self.assertRaisesRegex(AdoptionError, "^AUTHORITY_DENIED$"):
                store.dispatch(12002, 12002, req("current", "candidate", series_id="series-1"))
            with self.assertRaisesRegex(AdoptionError, "^AUTHORITY_DENIED$"):
                store.dispatch(12003, 12003, req("propose", "validator-propose", proposal_id="p",
                                                series_id="s", expected_generation=0, policy=self.policy))
            with self.assertRaisesRegex(AdoptionError, "^INVALID_REQUEST$"):
                store.dispatch(12001, 12001, req("current", "extra", series_id="s", actor="manager"))
            with self.assertRaisesRegex(AdoptionError, "^INVALID_REQUEST$"):
                store.dispatch(12003, 12003, req("validate", "self-passed", proposal_id="p",
                                                validation_id="v", passed=True))

    def test_redelivery_conflict_and_validation_expiry(self):
        with self.open() as store:
            first = self.propose(store)
            replay = store.dispatch(12001, 12001, req(
                "propose", "p-replay", proposal_id="proposal-1", series_id="series-1",
                expected_generation=0, policy=copy.deepcopy(self.policy)))
            self.assertEqual(replay["proposal_digest"], first["proposal_digest"])
            with self.assertRaisesRegex(AdoptionError, "^REQUEST_CONFLICT$"):
                store.dispatch(12003, 12003, req("current", "p-request", series_id="series-1"))
            self.validate(store)
            self.clock.value += 86400
            with self.assertRaisesRegex(AdoptionError, "^VALIDATION_EXPIRED$"):
                self.adopt(store)

    def test_current_redelivery_rechecks_expiry(self):
        with self.open() as store:
            self.propose(store)
            self.validate(store)
            self.adopt(store)
            current_request = req("current", "current-expiry", series_id="series-1")
            self.assertTrue(store.dispatch(12004, 12004, current_request)["valid"])
            self.clock.value += 86400
            self.assertFalse(store.dispatch(12004, 12004, current_request)["valid"])

    def test_concurrent_adopt_same_generation_only_one_wins(self):
        with self.open() as store:
            self.propose(store)
            self.validate(store)

        barrier = threading.Barrier(2)

        def attempt(request_id):
            with self.open() as store:
                barrier.wait(timeout=5)
                try:
                    return ("ok", store.dispatch(12001, 12001, req(
                        "adopt", request_id, proposal_id="proposal-1", validation_id="validation-1",
                        expected_generation=0)))
                except AdoptionError as error:
                    return ("error", error.code)

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(attempt, ("concurrent-a", "concurrent-b")))
        self.assertEqual(sum(kind == "ok" for kind, _ in results), 1)
        self.assertEqual(sum(value == "GENERATION_CONFLICT" for kind, value in results if kind == "error"), 1)
        with self.open() as store:
            current = store.dispatch(12004, 12004, req("current", "after-concurrent", series_id="series-1"))
            self.assertEqual(current["generation"], 1)

    def test_receipt_rejects_corrupt_idempotency_digests(self):
        with self.open() as store:
            self.propose(store)
            self.validate(store)
            self.adopt(store)
            store._db.execute("UPDATE idempotency SET response_digest=? WHERE request_id=?", ("0" * 64, "a-request"))
            with self.assertRaisesRegex(AdoptionError, "^STORAGE_CORRUPT$"):
                store.dispatch(12003, 12003, req("receipt", "receipt-corrupt", adoption_request_id="a-request"))
            store._db.execute("UPDATE idempotency SET response_json=NULL, response_digest=NULL WHERE request_id=?", ("a-request",))
            with self.assertRaisesRegex(AdoptionError, "^STORAGE_CORRUPT$"):
                store.dispatch(12001, 12001, req(
                    "adopt", "a-request", proposal_id="proposal-1", validation_id="validation-1",
                    expected_generation=0))

    def test_revoke_invalidates_current_and_permission_generation(self):
        with self.open() as store:
            self.propose(store)
            self.validate(store)
            self.adopt(store)
            cached_id = req("current", "current-cache", series_id="series-1")
            self.assertTrue(store.dispatch(12003, 12003, cached_id)["valid"])
            revoked = store.dispatch(12004, 12004, req(
                "revoke_validation", "rv-request", validation_id="validation-1"))
            self.assertEqual(revoked["permission_generation"], 1)
            self.assertFalse(store.dispatch(12003, 12003, cached_id)["valid"])
            current = store.dispatch(12003, 12003, req("current", "current-after-revoke", series_id="series-1"))
            self.assertFalse(current["valid"])
            # The historical adopt receipt remains immutable even after its
            # validation is revoked; current is the API that reflects validity.
            replay = store.dispatch(12001, 12001, req(
                "adopt", "a-request", proposal_id="proposal-1", validation_id="validation-1",
                expected_generation=0))
            self.assertEqual(replay["generation"], 1)
            proposed = store.dispatch(12001, 12001, req(
                "propose", "p2-request", proposal_id="proposal-2", series_id="series-1",
                expected_generation=1, policy=copy.deepcopy(self.policy)))
            self.assertEqual(proposed["expected_generation"], 1)
            self.validate(store, request_id="v2-request", proposal_id="proposal-2", validation_id="validation-2")
            store.dispatch(12004, 12004, req(
                "revoke_validation", "rv2-request", validation_id="validation-2"))
            with self.assertRaisesRegex(AdoptionError, "^VALIDATION_REVOKED$"):
                store.dispatch(12001, 12001, req(
                    "adopt", "adopt-again", proposal_id="proposal-2", validation_id="validation-2",
                    expected_generation=1))

            actor_revoke = store.dispatch(12004, 12004, req(
                "revoke_actor", "ra-request", actor_id="manager"))
            self.assertEqual(actor_revoke["permission_generation"], 3)
            with self.assertRaisesRegex(AdoptionError, "^AUTHORITY_REVOKED$"):
                store.dispatch(12001, 12001, req("current", "manager-current", series_id="series-1"))

    def test_clock_rollback_storage_rollback_and_corruption(self):
        with self.open() as store:
            self.propose(store)
            self.clock.value -= 1
            with self.assertRaisesRegex(AdoptionError, "^CLOCK_ROLLBACK$"):
                store.dispatch(12001, 12001, req("current", "clock-back", series_id="series-1"))
            self.clock.value += 1
            def deny(action, arg1, *_):
                if action == sqlite3.SQLITE_INSERT and arg1 == "idempotency":
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK
            store._db.set_authorizer(deny)
            with self.assertRaisesRegex(AdoptionError, "^STORAGE_FAILURE$"):
                store.dispatch(12003, 12003, req("validate", "denied", proposal_id="proposal-1", validation_id="v2"))
            store._db.set_authorizer(None)
            self.assertIsNone(store._db.execute("SELECT 1 FROM validations WHERE validation_id='v2'").fetchone())
            store._db.execute("UPDATE proposals SET policy_digest=? WHERE proposal_id='proposal-1'", ("c" * 64,))
            with self.assertRaisesRegex(AdoptionError, "^STORAGE_CORRUPT$"):
                store.dispatch(12003, 12003, req("validate", "corrupt", proposal_id="proposal-1", validation_id="v3"))

        with self.assertRaisesRegex(AdoptionError, "^CONFIG_MISMATCH$"):
            AdoptionStore(self.path, clock=self.clock, bootstrap_policy=self.policy, validator_digest="d" * 64)


if __name__ == "__main__":
    unittest.main()
