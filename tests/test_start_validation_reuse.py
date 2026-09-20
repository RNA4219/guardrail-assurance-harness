from __future__ import annotations

from copy import deepcopy
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gah import evaluation_authority as authority
from gah.adoption import AdoptionError


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tests" / path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

_HELPERS = _load("start_reuse_helpers", "test_transition_acceptance_integration.py")
_HISTORY = _load("start_reuse_gen1_helpers", "test_evaluation_history.py")


def request(action, request_id, **fields):
    return {"schema_version": 1, "action": action, "request_id": request_id, **fields}


class Generation1StartValidationTests(unittest.TestCase):
    def test_generation1_keeps_final_current_validation(self):
        fixture = _HISTORY.EvaluationHistoryTests("runTest")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        store, _ = fixture._open_ready()
        self.addCleanup(store.close)
        with patch.object(authority, "_assert_current_valid", wraps=authority._assert_current_valid) as current:
            result = fixture._check_start(store)
        current.assert_called_once()
        self.assertEqual(result[0]["run_id"], "history-run")


class Generation2StartValidationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = _HELPERS.TransitionAcceptanceIntegrationTests("runTest")
        self.fixture.setUp()
        self.clock = self.fixture.fixture.fixture.clock
        self.addCleanup(self.fixture.doCleanups)
        store, _, _, candidate, _, values = self.fixture._arrange("start-reuse")
        self.store, self.candidate, self.values = store, candidate, values
        self.fixture._complete(store, candidate, values)
        validated = self.fixture._validate(store, values, "start-reuse")
        self.assertTrue(validated["passed"])
        store.dispatch(12001, 12001, request(
            "contract_candidate_adopt", "start-reuse-adopt",
            candidate_id=values["candidate_id"], validation_id="validation-start-reuse",
            expected_contract_generation=1, expected_baseline_generation=1))
        self.run_id = "start-reuse-ordinary"
        self.synthetic_bound = deepcopy(candidate["runs"]["new"]["bound_run"])
        manifest = deepcopy(self.synthetic_bound["manifest"])
        manifest.update(run_id=self.run_id, purpose="diagnostic")
        plan = deepcopy(self.synthetic_bound["plan"])
        self.synthetic_bound["manifest"] = manifest
        self.synthetic_bound["plan"] = plan
        manifest_raw, manifest_digest = authority._packed(manifest)
        plan_raw, plan_digest = authority._packed(plan)
        with store._transaction() as db:
            db.execute("INSERT INTO eval_runs VALUES(?,?,?,?,?,?,?)", (
                self.run_id, manifest_raw, manifest_digest, plan_raw, plan_digest,
                "fixture-contract-series", 2))

    def _check_start(self):
        with self.store._transaction() as db:
            return self.store._extension._check_start(
                self.store, db, self.run_id, self.clock.value)

    def _final_regression(self):
        # Keep actual SQLite gen2 history/freshness validation; only the unrelated
        # final comparison artifact resolution for this synthetic row is stubbed.
        return patch.object(authority.regression_runs, "for_run", return_value={
            "bound_run": self.synthetic_bound})

    def test_gen2_reuse_write_and_no_transaction_fallback_keep_full_checks(self):
        real_validate = authority._validate_state
        def gen2_calls(spy, position):
            return sum(call.args[position]["generation"] == 2 for call in spy.call_args_list)

        # Prime the fixture's persisted evidence clock; the measured read is then
        # genuinely free of DB writes. Recursive gen1 validation remains required.
        with self._final_regression():
            expected = self._check_start()
        with self.subTest(branch="unchanged_transaction"):
            with self._final_regression(), \
                 patch.object(authority, "_validate_state", wraps=real_validate) as validated, \
                 patch.object(authority, "_assert_current_valid", wraps=authority._assert_current_valid) as current:
                fast = self._check_start()
            self.assertEqual(gen2_calls(validated, 2), 1)
            self.assertEqual(gen2_calls(current, 3), 0)
            self.assertEqual(fast, expected)
            self.assertEqual(fast[0]["run_id"], self.run_id)
            self.assertEqual(fast[1], self.synthetic_bound["plan"])

        def validate_then_write(store, db, contract, now, *, policy_state=None):
            result = real_validate(store, db, contract, now, policy_state=policy_state)
            if contract["generation"] == 2:
                db.execute("UPDATE adoption_meta SET value=value WHERE key='permission_generation'")
            return result
        with self.subTest(branch="database_write"):
            with self._final_regression(), \
                 patch.object(authority, "_validate_state", side_effect=validate_then_write), \
                 patch.object(authority, "_assert_current_valid", wraps=authority._assert_current_valid) as current:
                written = self._check_start()
            self.assertEqual(gen2_calls(current, 3), 1)
            self.assertEqual(written, expected)

        db = self.store._db
        self.assertFalse(db.in_transaction)
        def begin_then_validate(store, conn, contract, now, *, policy_state=None):
            if contract["generation"] == 2:
                self.assertFalse(conn.in_transaction)
                conn.execute("BEGIN")
            return real_validate(store, conn, contract, now, policy_state=policy_state)
        with self.subTest(branch="outside_transaction_entry"):
            try:
                with self._final_regression(), \
                     patch.object(authority, "_validate_state", side_effect=begin_then_validate), \
                     patch.object(authority, "_assert_current_valid", wraps=authority._assert_current_valid) as current:
                    result = self.store._extension._check_start(self.store, db, self.run_id, self.clock.value)
                self.assertEqual(gen2_calls(current, 3), 1)
                self.assertEqual(result, expected)
            finally:
                if db.in_transaction:
                    db.rollback()

        expiry = db.execute("SELECT expires_at FROM eval_validations WHERE id='validation-start-reuse'").fetchone()[0]
        old_now = self.clock.value
        with self.subTest(branch="expiry"):
            try:
                self.clock.value = expiry
                # Policy validation may expire before the generation-2 contract.
                with self._final_regression(), self.assertRaises(AdoptionError) as caught:
                    self._check_start()
                self.assertIn(caught.exception.code, {"PREREQUISITE_UNAVAILABLE", "CONTRACT_INVALID"})
            finally:
                self.clock.value = old_now

        with self.subTest(branch="permission_generation"):
            db.execute("UPDATE adoption_meta SET value=value+1 WHERE key='permission_generation'")
            try:
                with self._final_regression(), self.assertRaisesRegex(AdoptionError, "^PREREQUISITE_UNAVAILABLE$"):
                    self._check_start()
            finally:
                db.execute("UPDATE adoption_meta SET value=value-1 WHERE key='permission_generation'")

        for role in ("validator", "manager"):
            with self.subTest(branch="revoked_role", role=role):
                with self.store._transaction() as conn:
                    conn.execute("INSERT INTO revocations VALUES(?,?,?,?)", ("actor", role, 1, old_now))
                try:
                    with self._final_regression(), self.assertRaisesRegex(AdoptionError, "^PREREQUISITE_UNAVAILABLE$"):
                        self._check_start()
                finally:
                    db.execute("DELETE FROM revocations WHERE entity_type='actor' AND entity_id=?", (role,))

        original = db.execute("SELECT digest FROM eval_adoptions WHERE series_id=? AND generation=2", ("fixture-contract-series",)).fetchone()[0]
        with self.subTest(branch="altered_history"):
            db.execute("UPDATE eval_adoptions SET digest=? WHERE series_id=? AND generation=2", ("f" * 64, "fixture-contract-series"))
            try:
                with self._final_regression(), self.assertRaisesRegex(AdoptionError, "^STORAGE_CORRUPT$"):
                    self._check_start()
            finally:
                db.execute("UPDATE eval_adoptions SET digest=? WHERE series_id=? AND generation=2", (original, "fixture-contract-series"))

        with self.subTest(branch="subsequent_request_rechecks_currentness"):
            with self._final_regression():
                self.assertEqual(self._check_start(), expected)
            with self.store._transaction() as conn:
                conn.execute("INSERT INTO revocations VALUES(?,?,?,?)", ("actor", "manager", 1, old_now))
            with self._final_regression(), self.assertRaisesRegex(AdoptionError, "^PREREQUISITE_UNAVAILABLE$"):
                self._check_start()


if __name__ == "__main__":
    unittest.main()
