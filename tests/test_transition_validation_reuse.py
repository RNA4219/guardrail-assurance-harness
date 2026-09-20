from __future__ import annotations

import copy
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gah import evaluation_authority as authority
from gah.adoption import AdoptionError


class _Store:
    def __init__(self):
        self.permission_generation = 7
        self.revoked = set()

    def _permission_generation(self, _db):
        return self.permission_generation

    def _actor_revoked(self, _db, actor):
        return actor in self.revoked


class _Rows:
    def __init__(self, all_rows=None, one=None):
        self._all = all_rows
        self._one = one

    def fetchall(self):
        return self._all

    def fetchone(self):
        return self._one


class _DB:
    def __init__(self, current, previous):
        self.current = current
        self.previous = previous

    def execute(self, sql, _args=()):
        if "generation=? AND digest=?" in sql:
            return _Rows(all_rows=[self.current])
        if "series_id=? AND generation=?" in sql:
            return _Rows(one=self.previous)
        raise AssertionError("unexpected query shape")


def _validation(name: str, generation: int = 7, *, expires: int = 1000):
    return {"id": name, "created_at": 10, "expires_at": expires,
            "permission_generation": generation}


class TransitionValidationReuseTests(unittest.TestCase):
    def test_transition_reuses_explicit_current_history_but_checks_prior_generation(self):
        store = _Store()
        current_contract = {"generation": 2, "contract_id": "current-contract",
                            "registry_ref": {"kind": "control_registry", "id": "registry", "digest": "a" * 64},
                            "use_cases": ["UC-CI"]}
        previous_contract = {"generation": 1, "contract_id": "previous-contract",
                             "registry_ref": {"kind": "control_registry", "id": "registry", "digest": "a" * 64},
                             "use_cases": ["UC-CI"]}
        current_history = {"series_id": "series-1", "generation": 2}
        previous_history = {"series_id": "series-1", "generation": 1}
        current_validation = _validation("validation-current")
        previous_validation = _validation("validation-previous")
        current_payload = {"candidate_id": "candidate-1", "checked_at": 10,
                           "permission_generation": 7}
        previous_payload = {"checked_at": 10, "permission_generation": 7}
        transition = {"marker": "transition-content", "previous_contract": previous_contract}
        record = {"baseline_id": "baseline-1", "source_run_ref": {"id": "source-run"}}
        candidate = {
            "candidate_id": "candidate-1", "baseline_series_id": "baseline-series",
            "expected_baseline_ref": {"kind": "baseline", "id": "baseline-1", "digest": "b" * 64},
            "expected_contract_ref": {"kind": "evaluation_contract", "id": "previous-contract", "digest": "c" * 64},
            "runs": {"transition": transition},
        }
        db = _DB(current_history, previous_history)
        calls = []

        def history(_db, current, contract):
            calls.append(contract["generation"])
            return current_validation if contract["generation"] == 2 else previous_validation

        def load_json(row, *_fields):
            if row is current_validation:
                return current_payload
            if row is previous_validation:
                return previous_payload
            if row is previous_history:
                return previous_contract
            raise AssertionError("unexpected validation/history row")

        def baseline_execute(_store, _db, _request, _actor, _context, _now, resolve_source):
            resolve_source("source-run")
            return {"use": True, "baseline": record}

        with (
            mock.patch.object(authority, "_assert_contract_history", side_effect=history),
            mock.patch.object(authority, "_load_json", side_effect=load_json),
            mock.patch.object(authority, "_packed", return_value=("{}", "d" * 64)),
            mock.patch.object(authority.transition_authority, "load_candidate",
                              return_value=({"permission_generation": 7}, candidate)),
            mock.patch.object(authority, "_validate_state", return_value={"marker": "previous-state"}),
            mock.patch.object(authority.EvaluationExtension, "_baseline_source",
                              return_value={"bound": {"marker": "source-bound"}}),
            mock.patch.object(authority.baseline_authority, "execute", side_effect=baseline_execute),
            mock.patch.object(authority.contract_updates, "bind_contract_transition", return_value=transition),
            mock.patch.object(authority, "_transition_source_context", return_value=None),
            mock.patch.object(authority.transition_acceptance, "validate_live_proof", return_value=None),
        ):
            actual = authority._validate_transition_state(store, db, current_contract, 20)

        # One checked row for current generation; prior generation is still independently checked.
        self.assertEqual(calls, [2, 1])
        expected = {"marker": "previous-state", "contract": copy.deepcopy(current_contract)}
        self.assertEqual(actual, expected)
        self.assertIsNot(actual["contract"], current_contract)
        actual["contract"]["contract_id"] = "mutated-return"
        self.assertEqual(current_contract["contract_id"], "current-contract")

    def test_freshness_checks_expiry_permission_generation_and_both_roles(self):
        store = _Store()
        db = object()
        contract = {"generation": 1}
        row = _validation("validation-fresh")
        payload = {"checked_at": 10, "permission_generation": 7}
        with mock.patch.object(authority, "_assert_contract_history", return_value=row) as history, \
             mock.patch.object(authority, "_load_json", return_value=payload):
            authority._assert_contract_fresh(store, db, {"generation": 1}, contract, 20)
            self.assertEqual(history.call_count, 1)
            with self.assertRaises(AdoptionError):
                authority._assert_contract_fresh(store, db, {"generation": 1}, contract, 1000)
            store.permission_generation = 8
            with self.assertRaises(AdoptionError):
                authority._assert_contract_fresh(store, db, {"generation": 1}, contract, 20)
            store.permission_generation = 7
            for actor in ("manager", "validator"):
                store.revoked = {actor}
                with self.subTest(actor=actor), self.assertRaises(AdoptionError):
                    authority._assert_contract_fresh(store, db, {"generation": 1}, contract, 20)
                store.revoked.clear()
            self.assertEqual(history.call_count, 5)

    def test_changed_validation_row_is_reloaded_on_next_request(self):
        store = _Store()
        db = object()
        contract = {"generation": 2}
        current = {"generation": 2}
        fresh = _validation("fresh")
        expired = _validation("expired", expires=19)
        rows = [fresh, expired]
        with mock.patch.object(authority, "_assert_contract_history", side_effect=rows) as history, \
             mock.patch.object(authority, "_load_json", return_value={"checked_at": 10,
                                                                       "permission_generation": 7}):
            authority._assert_contract_fresh(store, db, current, contract, 19)
            with self.assertRaises(AdoptionError):
                authority._assert_contract_fresh(store, db, current, contract, 19)
        self.assertEqual(history.call_count, 2)


if __name__ == "__main__":
    unittest.main()
