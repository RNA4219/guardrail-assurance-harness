"""契約更新の事前照合を、固定packの実DB経路で検査する。"""

from copy import deepcopy
import hashlib
import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gah.adoption import AdoptionError
from gah.run_contracts import content_ref
from gah.wire import canonical_bytes


_HELPER_SPEC = importlib.util.spec_from_file_location(
    "baseline_integration_helpers", ROOT / "tests" / "test_baseline_adoption_integration.py"
)
assert _HELPER_SPEC is not None and _HELPER_SPEC.loader is not None
_HELPERS = importlib.util.module_from_spec(_HELPER_SPEC)
_HELPER_SPEC.loader.exec_module(_HELPERS)


def request(action, request_id, **fields):
    return {"schema_version": 1, "action": action, "request_id": request_id, **fields}


class ContractPreflightIntegrationTests(unittest.TestCase):
    """既存の固定15件helperを一つのモジュールとして利用する。"""

    def setUp(self):
        self.fixture = _HELPERS.BaselineAdoptionIntegrationTests("runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def _arrange(self, run_id="contract-preflight-run"):
        store = self.fixture.open()
        self.addCleanup(store.close)
        _, prepared, owner = self.fixture._prepare_contract_run(store, run_id)
        final = self.fixture._complete_all_entries(store, prepared, owner)
        self.fixture._adopt_baseline(store, run_id)
        baseline_current = store.dispatch(12004, 12004, request(
            "baseline_current", "preflight-baseline-current-" + run_id,
            series_id="fixture-baseline-series"))
        baseline = deepcopy(baseline_current["baseline"])
        old_contract = deepcopy(prepared["bound_run"]["contract"])
        old_ref = content_ref("evaluation_contract", old_contract["contract_id"], old_contract)
        baseline_ref = content_ref("baseline", baseline["baseline_id"], baseline)

        candidate = deepcopy(old_contract)
        candidate["contract_id"] = "contract-update-" + run_id
        candidate["generation"] = 2
        candidate["comparison"] = {
            "mode": "required",
            "baseline_ref": deepcopy(baseline_ref),
            "reason": None,
            "changed_axes": [],
        }
        proposal_id = "preflight-proposal-" + run_id
        store.dispatch(12001, 12001, request(
            "contract_propose", "preflight-propose-" + run_id,
            proposal_id=proposal_id, series_id="fixture-contract-series",
            expected_generation=1, contract=candidate))
        return store, prepared, final, old_contract, old_ref, baseline, baseline_ref, candidate, proposal_id

    def _preflight_request(self, proposal_id, contract_ref, baseline_ref, request_id="preflight-request"):
        return request(
            "contract_preflight", request_id, proposal_id=proposal_id,
            baseline_series_id="fixture-baseline-series",
            expected_contract_ref=deepcopy(contract_ref),
            expected_baseline_ref=deepcopy(baseline_ref),
        )

    def test_preflight_is_ready_without_mutating_current_or_creating_run(self):
        (store, prepared, final, old_contract, old_ref, baseline, baseline_ref,
         candidate, proposal_id) = self._arrange()
        current_before = tuple(store._db.execute(
            "SELECT series_id,generation,proposal_id,validation_id,payload_json,digest "
            "FROM eval_current WHERE series_id=?", ("fixture-contract-series",)).fetchone())
        adoption_before = tuple(store._db.execute(
            "SELECT series_id,generation,proposal_id,validation_id,payload_json,digest "
            "FROM eval_adoptions WHERE series_id=? AND generation=1",
            ("fixture-contract-series",)).fetchone())
        run_count = store._db.execute("SELECT COUNT(*) FROM eval_runs").fetchone()[0]
        validation_count = store._db.execute("SELECT COUNT(*) FROM eval_validations").fetchone()[0]
        operation_count = store._db.execute("SELECT COUNT(*) FROM resource_operations").fetchone()[0]
        terminal_before = tuple(store._db.execute(
            "SELECT run_id,terminal_json,terminal_digest,created_at FROM terminals WHERE run_id=?",
            (prepared["bound_run"]["manifest"]["run_id"],)).fetchone())

        result = store.dispatch(12003, 12003, self._preflight_request(
            proposal_id, old_ref, baseline_ref))
        self.assertTrue(result["preflight_ready"])
        self.assertTrue(result["candidate_run_required"])
        self.assertFalse(result["adoption_verified"])
        self.assertFalse(result["ci_eligible"])
        self.assertEqual(current_before, tuple(store._db.execute(
            "SELECT series_id,generation,proposal_id,validation_id,payload_json,digest "
            "FROM eval_current WHERE series_id=?", ("fixture-contract-series",)).fetchone()))
        self.assertEqual(adoption_before, tuple(store._db.execute(
            "SELECT series_id,generation,proposal_id,validation_id,payload_json,digest "
            "FROM eval_adoptions WHERE series_id=? AND generation=1",
            ("fixture-contract-series",)).fetchone()))
        self.assertEqual(run_count, store._db.execute("SELECT COUNT(*) FROM eval_runs").fetchone()[0])
        self.assertEqual(validation_count, store._db.execute("SELECT COUNT(*) FROM eval_validations").fetchone()[0])
        self.assertEqual(operation_count, store._db.execute("SELECT COUNT(*) FROM resource_operations").fetchone()[0])
        self.assertEqual(terminal_before, tuple(store._db.execute(
            "SELECT run_id,terminal_json,terminal_digest,created_at FROM terminals WHERE run_id=?",
            (prepared["bound_run"]["manifest"]["run_id"],)).fetchone()))
        self.assertEqual(final["run_id"], prepared["bound_run"]["manifest"]["run_id"])
        self.assertEqual(old_contract["generation"], 1)
        self.assertEqual(old_ref, baseline["contract_ref"])

    def test_same_request_id_recomputes_after_evidence_revocation(self):
        store, _, _, old_contract, old_ref, baseline, baseline_ref, candidate, proposal_id = self._arrange("preflight-revoke-evidence")
        req = self._preflight_request(proposal_id, old_ref, baseline_ref, "preflight-fresh-evidence")
        first = store.dispatch(12003, 12003, req)
        self.assertTrue(first["preflight_ready"])
        store.dispatch(12004, 12004, request(
            "evidence_revoke", "preflight-evidence-revoke", run_id="preflight-revoke-evidence"))
        with self.assertRaises(AdoptionError):
            store.dispatch(12003, 12003, req)

    def test_same_request_id_recomputes_after_baseline_revocation(self):
        store, _, _, old_contract, old_ref, baseline, baseline_ref, candidate, proposal_id = self._arrange("preflight-revoke-baseline")
        req = self._preflight_request(proposal_id, old_ref, baseline_ref, "preflight-fresh-baseline")
        first = store.dispatch(12003, 12003, req)
        self.assertTrue(first["preflight_ready"])
        store.dispatch(12004, 12004, request(
            "baseline_revoke", "preflight-baseline-revoke", series_id="fixture-baseline-series"))
        with self.assertRaises(AdoptionError):
            store.dispatch(12003, 12003, req)

    def test_only_validator_may_preflight(self):
        store, _, _, old_contract, old_ref, baseline, baseline_ref, candidate, proposal_id = self._arrange("preflight-roles")
        for uid in (12001, 12002, 12004):
            with self.subTest(uid=uid), self.assertRaises(AdoptionError):
                store.dispatch(uid, uid, self._preflight_request(
                    proposal_id, old_ref, baseline_ref, f"preflight-role-{uid}"))

    def test_full_reference_mismatch_is_rejected(self):
        store, _, _, old_contract, old_ref, baseline, baseline_ref, candidate, proposal_id = self._arrange("preflight-ref")
        wrong_contract = deepcopy(old_ref)
        wrong_contract["digest"] = "0" * 64
        with self.assertRaises(AdoptionError):
            store.dispatch(12003, 12003, self._preflight_request(
                proposal_id, wrong_contract, baseline_ref, "preflight-wrong-contract-ref"))
        wrong_baseline = deepcopy(baseline_ref)
        wrong_baseline["digest"] = "f" * 64
        with self.assertRaises(AdoptionError):
            store.dispatch(12003, 12003, self._preflight_request(
                proposal_id, old_ref, wrong_baseline, "preflight-wrong-baseline-ref"))

    def test_proposal_actor_or_context_tamper_is_rejected(self):
        store, _, _, old_contract, old_ref, baseline, baseline_ref, candidate, proposal_id = self._arrange("preflight-proposal-tamper")
        store._db.execute("UPDATE eval_proposals SET actor_id=? WHERE id=?", ("validator", proposal_id))
        store._db.commit()
        with self.assertRaises(AdoptionError):
            store.dispatch(12003, 12003, self._preflight_request(
                proposal_id, old_ref, baseline_ref, "preflight-tampered-actor"))
        store._db.execute(
            "UPDATE eval_proposals SET actor_id=?, context=? WHERE id=?",
            ("manager", "tampered-context", proposal_id))
        store._db.commit()
        with self.assertRaises(AdoptionError):
            store.dispatch(12003, 12003, self._preflight_request(
                proposal_id, old_ref, baseline_ref, "preflight-tampered-context"))

    def test_current_history_one_sided_mismatch_is_rejected(self):
        store, _, _, old_contract, old_ref, baseline, baseline_ref, candidate, proposal_id = self._arrange("preflight-history")
        store._db.execute(
            "DELETE FROM eval_adoptions WHERE series_id=? AND generation=1",
            ("fixture-contract-series",))
        store._db.commit()
        with self.assertRaises(AdoptionError):
            store.dispatch(12003, 12003, self._preflight_request(
                proposal_id, old_ref, baseline_ref, "preflight-history-missing"))

    def test_policy_or_baseline_condition_tamper_is_rejected(self):
        store, _, _, old_contract, old_ref, baseline, baseline_ref, candidate, proposal_id = self._arrange("preflight-condition")
        row = store._db.execute(
            "SELECT baseline_json FROM baseline_current WHERE series_id=?",
            ("fixture-baseline-series",)).fetchone()
        self.assertIsNotNone(row)
        store._db.execute(
            "UPDATE baseline_current SET baseline_json=? WHERE series_id=?",
            ('{"tampered":true}', "fixture-baseline-series"))
        store._db.commit()
        with self.assertRaises(AdoptionError):
            store.dispatch(12003, 12003, self._preflight_request(
                proposal_id, old_ref, baseline_ref, "preflight-condition-tamper"))

    def test_normal_shape_policy_generation_change_is_rejected_immutably(self):
        (store, prepared, final, old_contract, old_ref, baseline, baseline_ref,
         candidate, proposal_id) = self._arrange("preflight-policy-generation")
        current_before = tuple(store._db.execute(
            "SELECT series_id,generation,proposal_id,validation_id,payload_json,digest "
            "FROM eval_current WHERE series_id=?", ("fixture-contract-series",)).fetchone())
        terminal_before = tuple(store._db.execute(
            "SELECT run_id,terminal_json,terminal_digest,created_at FROM terminals WHERE run_id=?",
            (prepared["bound_run"]["manifest"]["run_id"],)).fetchone())

        changed = deepcopy(candidate)
        changed["policy_generation"] = 2
        raw = canonical_bytes(changed)
        digest = hashlib.sha256(raw).hexdigest()
        store._db.execute(
            "UPDATE eval_proposals SET payload_json=?, digest=? WHERE id=?",
            (raw.decode("utf-8"), digest, proposal_id))
        store._db.commit()
        with self.assertRaises(AdoptionError):
            store.dispatch(12003, 12003, self._preflight_request(
                proposal_id, old_ref, baseline_ref, "preflight-policy-generation-change"))
        self.assertEqual(current_before, tuple(store._db.execute(
            "SELECT series_id,generation,proposal_id,validation_id,payload_json,digest "
            "FROM eval_current WHERE series_id=?", ("fixture-contract-series",)).fetchone()))
        self.assertEqual(terminal_before, tuple(store._db.execute(
            "SELECT run_id,terminal_json,terminal_digest,created_at FROM terminals WHERE run_id=?",
            (prepared["bound_run"]["manifest"]["run_id"],)).fetchone()))


if __name__ == "__main__":
    unittest.main()
