"""候補runのEvidenceを使った世代2採択の実DB統合検査。"""

from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gah.adoption import AdoptionError
from gah.resources import ResourceBook


_HELPER_SPEC = importlib.util.spec_from_file_location(
    "transition_candidate_helpers_for_acceptance",
    ROOT / "tests" / "test_transition_candidate_integration.py",
)
assert _HELPER_SPEC is not None and _HELPER_SPEC.loader is not None
_HELPERS = importlib.util.module_from_spec(_HELPER_SPEC)
_HELPER_SPEC.loader.exec_module(_HELPERS)


def request(action, request_id, **fields):
    return {"schema_version": 1, "action": action, "request_id": request_id, **fields}


class TransitionAcceptanceIntegrationTests(unittest.TestCase):
    """旧・新候補の保存証拠を再検査して採択する境界を検査する。"""

    def setUp(self):
        self.fixture = _HELPERS.TransitionCandidateIntegrationTests("runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def _arrange(self, suffix):
        return self.fixture._arrange("acceptance-" + suffix)

    def _complete(self, store, candidate, values):
        # 候補テストのworker/clock実体は、その内側のbaseline fixtureが保持する。
        worker_fixture = self.fixture.fixture
        old = self.fixture._run_side(store, worker_fixture, candidate, values, "old")
        new = self.fixture._run_side(store, worker_fixture, candidate, values, "new")
        return old, new

    @staticmethod
    def _validate(store, values, suffix="validate"):
        return store.dispatch(12003, 12003, request(
            "contract_candidate_validate", "candidate-" + suffix,
            candidate_id=values["candidate_id"], validation_id="validation-" + suffix))

    @staticmethod
    def _receipt_rows(store, run_ids):
        rows = []
        for run_id in run_ids:
            row = store._db.execute(
                "SELECT run_id,payload_json,digest,permission_generation,created_at,revoked_at "
                "FROM authority_run_receipts WHERE run_id=?", (run_id,)).fetchone()
            rows.append(tuple(row) if row is not None else None)
        return tuple(rows)

    @staticmethod
    def _table_state(store, series_id, candidate_id, validation_id):
        return tuple(store._db.execute(
            "SELECT "
            "(SELECT COUNT(*) FROM eval_current WHERE series_id=?),"
            "(SELECT COUNT(*) FROM eval_adoptions WHERE series_id=? AND generation=2),"
            "(SELECT COUNT(*) FROM eval_validations WHERE id=?),"
            "(SELECT COUNT(*) FROM transition_candidates WHERE candidate_id=?)",
            (series_id, series_id, validation_id, candidate_id)).fetchone())

    def test_complete_candidate_is_validated_adopted_and_replayed_immutably(self):
        store, _, _, candidate, _, values = self._arrange("happy")
        self._complete(store, candidate, values)
        receipt_before = self._receipt_rows(store, (
            values["old_run_id"], values["new_run_id"]))
        # 別runの未送信予約が全体予算を変えても、このrunの精算済み証拠は変わらない。
        book = ResourceBook(store._db)
        now = self.fixture.fixture.clock.value
        policy = candidate["runs"]["old"]["bound_run"]["policy"]
        store._db.execute("BEGIN IMMEDIATE")
        book.create_run("unrelated-budget-run", "f" * 64, policy, "full", "unrelated-owner", now,
            now + policy["profiles"]["full"]["elapsed_seconds"])
        book.reserve("unrelated-budget-run", "unrelated-reservation", "unrelated-owner", 1,
            {"case_trial_executions": 1, "model_calls": 1, "input_tokens": 1, "output_tokens": 1,
             "api_cost_usd_micros": 1, "billing_mode": "metered",
             "billing_ref": {"kind": "billing_basis", "id": "synthetic-billing", "digest": "f" * 64}}, now)
        self.assertEqual(book.snapshot(values["old_run_id"], now)["resources"]["global_api_cost_usd_micros"], 1)
        store._db.commit()
        validated = self._validate(store, values, "happy")
        self.assertEqual(validated["candidate_id"], values["candidate_id"])
        self.assertEqual(validated["validation_id"], "validation-happy")
        self.assertTrue(validated["passed"])
        self.assertFalse(validated["ci_eligible"])

        adopt = request(
            "contract_candidate_adopt", "candidate-adopt-happy",
            candidate_id=values["candidate_id"], validation_id="validation-happy",
            expected_contract_generation=1, expected_baseline_generation=1)
        with self.assertRaises(AdoptionError):
            store.dispatch(12001, 12001, {**adopt, "expected_contract_generation": 2})
        adopted = store.dispatch(12001, 12001, adopt)
        self.assertEqual(adopted["generation"], 2)
        self.assertTrue(adopted["adoption_verified"])
        self.assertFalse(adopted["ci_eligible"])

        current = store.dispatch(12004, 12004, request(
            "contract_current", "candidate-current-after-adopt",
            series_id="fixture-contract-series"))
        self.assertTrue(current["valid"])
        self.assertEqual(current["generation"], 2)
        baseline = store.dispatch(12004, 12004, request(
            "baseline_current", "candidate-baseline-stays-gen1",
            series_id="fixture-baseline-series"))
        self.assertTrue(baseline["valid"])
        self.assertEqual(baseline["generation"], 1)
        self.assertEqual(receipt_before, self._receipt_rows(
            store, (values["old_run_id"], values["new_run_id"])))

        replay = store.dispatch(12001, 12001, adopt)
        self.assertEqual(replay, adopted)
        self.assertEqual(receipt_before, self._receipt_rows(
            store, (values["old_run_id"], values["new_run_id"])))

        store.close()
        restarted = self.fixture.fixture.open()
        self.addCleanup(restarted.close)
        after_restart = restarted.dispatch(12004, 12004, request(
            "contract_current", "candidate-current-after-restart",
            series_id="fixture-contract-series"))
        self.assertTrue(after_restart["valid"])
        self.assertEqual(after_restart["generation"], 2)
        # 新旧根拠の撤回はfresh利用を失効させ、過去の採択を消さない。
        restarted.dispatch(12004, 12004, request("evidence_revoke", "revoke-adopted-candidate-proof",
            run_id=values["new_run_id"]))
        invalid = restarted.dispatch(12004, 12004, request("contract_current", "candidate-current-after-restart",
            series_id="fixture-contract-series"))
        self.assertFalse(invalid["valid"])
        self.assertEqual(invalid["generation"], 2)
        self.assertEqual(restarted.dispatch(12001, 12001, adopt), adopted)

    def test_roles_candidate_validation_and_validation_identity_are_strict(self):
        store, _, _, _, _, values = self._arrange("roles")
        validation = request(
            "contract_candidate_validate", "candidate-validation-role-check",
            candidate_id=values["candidate_id"], validation_id="validation-role-check")
        for uid in (12001, 12002, 12004):
            with self.subTest(action="validate", uid=uid), self.assertRaises(AdoptionError):
                store.dispatch(uid, uid, {**validation,
                                          "request_id": f"candidate-validation-role-{uid}"})
        adopt = request(
            "contract_candidate_adopt", "candidate-adopt-role-check",
            candidate_id=values["candidate_id"], validation_id="validation-role-check",
            expected_contract_generation=1, expected_baseline_generation=1)
        with self.assertRaises(AdoptionError):
            store.dispatch(12002, 12002, adopt)
        with self.assertRaises(AdoptionError):
            store.dispatch(12003, 12003, {**validation, "candidate_id": "missing-candidate",
                                          "request_id": "candidate-validation-missing"})
        with self.assertRaises(AdoptionError):
            store.dispatch(12003, 12003, {**validation, "validation_id": "other-validation",
                                          "request_id": "candidate-validation-other"})

    def test_incomplete_old_or_new_evidence_cannot_be_validated(self):
        store, _, _, candidate, _, values = self._arrange("incomplete")
        store.dispatch(12004, 12004, request(
            "contract_candidate_begin", "candidate-begin-only-old",
            candidate_id=values["candidate_id"], side="old"))
        with self.assertRaises(AdoptionError):
            self._validate(store, values, "incomplete")
        self.assertEqual(self._table_state(
            store, "fixture-contract-series", values["candidate_id"],
            "validation-incomplete")[0:3], (1, 0, 0))

    def test_revoke_source_old_or_new_receipt_invalidates_existing_validation(self):
        store, prepared, _, candidate, _, values = self._arrange("revoked")
        self._complete(store, candidate, values)
        self._validate(store, values, "revoked")
        source_id = prepared["bound_run"]["manifest"]["run_id"]
        for index, run_id in enumerate((source_id, values["old_run_id"], values["new_run_id"])):
            with self.subTest(run_id=run_id):
                store.dispatch(12004, 12004, request(
                    "evidence_revoke", f"candidate-revoke-{index}", run_id=run_id))
                with self.assertRaises(AdoptionError):
                    store.dispatch(12001, 12001, request(
                        "contract_candidate_adopt", f"candidate-adopt-after-revoke-{index}",
                        candidate_id=values["candidate_id"], validation_id="validation-revoked",
                        expected_contract_generation=1, expected_baseline_generation=1))
                # 各subTestを独立に保つため、検査対象の撤回記録だけを検査用DBから戻す。
                receipt = store._db.execute(
                    "SELECT digest FROM authority_run_receipts WHERE run_id=?", (run_id,)).fetchone()
                store._db.execute("DELETE FROM authority_run_events WHERE run_id=?", (run_id,))
                store._db.execute(
                    "UPDATE authority_run_receipts SET revoked_at=NULL WHERE run_id=?", (run_id,))
                store._db.commit()
                self.assertIsNotNone(receipt)

    def test_validation_expiry_and_permission_change_are_rechecked_at_adoption(self):
        store, _, _, candidate, _, values = self._arrange("fresh")
        self._complete(store, candidate, values)
        self._validate(store, values, "fresh")
        row = store._db.execute(
            "SELECT expires_at FROM eval_validations WHERE id=?", ("validation-fresh",)).fetchone()
        self.assertIsNotNone(row)
        original_expiry = row[0]
        self.fixture.fixture.clock.value += 1
        now = self.fixture.fixture.clock.value
        store._db.execute(
            "UPDATE eval_validations SET expires_at=? WHERE id=?", (now, "validation-fresh"))
        store._db.commit()
        adopt = request(
            "contract_candidate_adopt", "candidate-adopt-expired",
            candidate_id=values["candidate_id"], validation_id="validation-fresh",
            expected_contract_generation=1, expected_baseline_generation=1)
        with self.assertRaisesRegex(AdoptionError, "^VALIDATION_EXPIRED$"):
            store.dispatch(12001, 12001, adopt)
        store._db.execute(
            "UPDATE eval_validations SET expires_at=? WHERE id=?",
            (original_expiry, "validation-fresh"))
        store._db.commit()
        store.dispatch(12004, 12004, request(
            "revoke_actor", "revoke-extension-validator", actor_id="validator"))
        with self.assertRaises(AdoptionError):
            store.dispatch(12001, 12001, {**adopt, "request_id": "candidate-adopt-revoked"})

    def test_validation_payload_tamper_is_rejected_without_pointer_change(self):
        store, _, _, candidate, _, values = self._arrange("payload")
        self._complete(store, candidate, values)
        self._validate(store, values, "payload")
        row = store._db.execute(
            "SELECT payload_json,digest FROM eval_validations WHERE id=?",
            ("validation-payload",)).fetchone()
        self.assertIsNotNone(row)
        store._db.execute(
            "UPDATE eval_validations SET payload_json=? WHERE id=?",
            ('{"tampered":true}', "validation-payload"))
        store._db.commit()
        adopt = request(
            "contract_candidate_adopt", "candidate-adopt-payload-tamper",
            candidate_id=values["candidate_id"], validation_id="validation-payload",
            expected_contract_generation=1, expected_baseline_generation=1)
        with self.assertRaises(AdoptionError):
            store.dispatch(12001, 12001, adopt)
        self.assertIsNone(store._db.execute(
            "SELECT 1 FROM eval_adoptions WHERE series_id=? AND generation=2",
            ("fixture-contract-series",)).fetchone())
        self.assertEqual(row["digest"], store._db.execute(
            "SELECT digest FROM eval_validations WHERE id=?", ("validation-payload",)).fetchone()[0])

    def test_adoption_pointer_and_history_rollback_together_on_insert_failure(self):
        store, _, _, candidate, _, values = self._arrange("rollback")
        self._complete(store, candidate, values)
        self._validate(store, values, "rollback")
        before = self._table_state(
            store, "fixture-contract-series", values["candidate_id"], "validation-rollback")
        store._db.execute("""
            CREATE TRIGGER fail_transition_adoption
            AFTER INSERT ON eval_adoptions
            WHEN NEW.generation=2
            BEGIN SELECT RAISE(ABORT, 'forced acceptance history failure'); END
        """)
        store._db.commit()
        adopt = request(
            "contract_candidate_adopt", "candidate-adopt-rollback",
            candidate_id=values["candidate_id"], validation_id="validation-rollback",
            expected_contract_generation=1, expected_baseline_generation=1)
        with self.assertRaises(AdoptionError):
            store.dispatch(12001, 12001, adopt)
        self.assertEqual(before, self._table_state(
            store, "fixture-contract-series", values["candidate_id"], "validation-rollback"))
        self.assertIsNone(store._db.execute(
            "SELECT 1 FROM eval_current WHERE series_id=? AND generation=2",
            ("fixture-contract-series",)).fetchone())
        store._db.execute("DROP TRIGGER fail_transition_adoption")
        store._db.commit()
        adopted = store.dispatch(12001, 12001, adopt)
        self.assertTrue(adopted["adoption_verified"])
        self.assertEqual(adopted["generation"], 2)

    def test_stale_baseline_generation_and_candidate_validation_pair_are_rejected(self):
        store, _, _, candidate, _, values = self._arrange("stale")
        self._complete(store, candidate, values)
        self._validate(store, values, "stale")
        for field in ("expected_baseline_generation", "expected_contract_generation"):
            stale = request(
                "contract_candidate_adopt", "candidate-adopt-stale-" + field,
                candidate_id=values["candidate_id"], validation_id="validation-stale",
                expected_contract_generation=1, expected_baseline_generation=1)
            stale[field] = 2
            with self.subTest(field=field), self.assertRaises(AdoptionError):
                store.dispatch(12001, 12001, stale)
        mismatch = request(
            "contract_candidate_adopt", "candidate-adopt-validation-mismatch",
            candidate_id=values["candidate_id"], validation_id="missing-validation",
            expected_contract_generation=1, expected_baseline_generation=1)
        with self.assertRaises(AdoptionError):
            store.dispatch(12001, 12001, mismatch)
        self.assertEqual(self._table_state(
            store, "fixture-contract-series", values["candidate_id"], "validation-stale")[0:2], (1, 0))


if __name__ == "__main__":
    unittest.main()
