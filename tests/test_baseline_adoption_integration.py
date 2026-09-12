"""固定packからbaseline採択までの実DB統合境界を検査する。"""

from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gah.adoption import AdoptionError, AdoptionStore
from gah.assurance_authority import fixed_profile
from gah.contracts import ContractError
from gah.evaluation_authority import EvaluationExtension
from gah.normalized import normalize_generic
from gah.policy import initial_policy_profile
from gah.run_contracts import content_ref
from gah.wire import canonical_bytes


def request(action, request_id, **fields):
    return {"schema_version": 1, "action": action, "request_id": request_id, **fields}


class Clock:
    def __init__(self, value=1000):
        self.value = value

    def __call__(self):
        return self.value


class BaselineAdoptionIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "integration.sqlite"
        self.clock = Clock()
        self.policy = initial_policy_profile()
        self.worker_path = ROOT / "fixtures" / "runtime" / "fixture_worker.py"
        self.worker_source = self.worker_path.read_bytes()
        self.worker = self._load_worker()

    def _load_worker(self):
        spec = importlib.util.spec_from_file_location("fixture_worker_integration", self.worker_path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def open(self):
        return AdoptionStore(self.path, clock=self.clock, bootstrap_policy=self.policy,
                             validator_digest="b" * 64, extension=EvaluationExtension())

    def _policy_adopt(self, store):
        store.dispatch(12001, 12001, request("propose", "policy-propose", proposal_id="policy-proposal",
                                             series_id=self.policy["policy_id"], expected_generation=0,
                                             policy=deepcopy(self.policy)))
        store.dispatch(12003, 12003, request("validate", "policy-validate", proposal_id="policy-proposal",
                                             validation_id="policy-validation"))
        return store.dispatch(12001, 12001, request("adopt", "policy-adopt", proposal_id="policy-proposal",
                                                    validation_id="policy-validation", expected_generation=0))

    def _prepare_contract_run(self, store, run_id="fixture-integration-run"):
        self._policy_adopt(store)
        prepared_response = store.dispatch(12001, 12001, request(
            "fixture_prepare", "fixture-prepare-" + run_id, run_id=run_id,
            policy_series_id=self.policy["policy_id"]))
        prepared = prepared_response["prepared"]
        bound = prepared["bound_run"]
        contract = bound["contract"]
        plan = bound["plan"]
        store.dispatch(12001, 12001, request(
            "contract_propose", "contract-propose-" + run_id,
            proposal_id="contract-proposal-" + run_id, series_id="fixture-contract-series",
            expected_generation=0, contract=deepcopy(contract)))
        store.dispatch(12003, 12003, request(
            "contract_validate", "contract-validate-" + run_id,
            proposal_id="contract-proposal-" + run_id, validation_id="contract-validation-" + run_id))
        store.dispatch(12001, 12001, request(
            "contract_adopt", "contract-adopt-" + run_id,
            proposal_id="contract-proposal-" + run_id, validation_id="contract-validation-" + run_id,
            expected_generation=0))
        begun = store.dispatch(12004, 12004, request(
            "run_begin", "run-begin-" + run_id, manifest=deepcopy(bound["manifest"]),
            plan=deepcopy(plan), contract_series_id="fixture-contract-series"))
        self.assertEqual(begun["run_id"], run_id)
        return prepared_response, prepared, {"owner_id": "run-begin-" + run_id, "owner_epoch": 1}

    def _complete_all_entries(self, store, prepared, owner):
        run_id = prepared["bound_run"]["manifest"]["run_id"]
        bound = prepared["bound_run"]
        profile = fixed_profile()
        store.dispatch(12004, 12004, request("evidence_open", "evidence-open-" + run_id, run_id=run_id))
        self.clock.value = 1001
        store.dispatch(12004, 12004, request("resource_claim", "claim-" + run_id, run_id=run_id,
                                             owner_id=owner["owner_id"], recovery=False))
        entries = bound["plan"]["entries"]
        materials = prepared["pack"]["materials"]
        attempts = []
        for index, material in enumerate(materials):
            entry = next(item for item in entries if item["obligation_id"] == material["obligation_id"])
            operation_id = f"operation-{run_id}-{index:02d}"
            reserve_at = 1002 + index * 4
            observe_at = reserve_at + 1
            short_entry = {key: entry[key] for key in ("obligation_id", "case_id", "trial_id", "variant")}
            self.clock.value = reserve_at
            # resource lease は60秒なので、長い15-entry処理でも同じ監督の
            # leaseを実経路のclaim再配送で更新する。
            store.dispatch(12004, 12004, request("resource_claim", f"reclaim-{run_id}-{index:02d}",
                                                 run_id=run_id, owner_id=owner["owner_id"], recovery=False))
            store.dispatch(12004, 12004, request("resource_reserve", f"reserve-{run_id}-{index:02d}",
                run_id=run_id, operation_id=operation_id, owner_id=owner["owner_id"], owner_epoch=1,
                entry=short_entry, scenario=material["scenario"]))
            store.dispatch(12004, 12004, request("resource_dispatch", f"dispatch-{run_id}-{index:02d}",
                run_id=run_id, operation_id=operation_id, owner_id=owner["owner_id"], owner_epoch=1))
            self.clock.value = observe_at
            store.dispatch(12003, 12003, request("resource_observe", f"observe-{run_id}-{index:02d}",
                run_id=run_id, operation_id=operation_id, event_id=f"event-{run_id}-{index:02d}",
                stopped=True, usage={"input_tokens": 0, "output_tokens": 0, "cost_usd": "0"}))
            kind, code, state = material["scenario"].split(":")
            binding = {
                "run_id": run_id, "operation_id": operation_id, "owner_epoch": 1,
                "contract_digest": bound["manifest"]["contract_ref"]["digest"],
                "target_digest": entry["target_ref"]["digest"], "obligation_id": entry["obligation_id"],
                "case_id": entry["case_id"], "trial_id": entry["trial_id"], "stage_id": entry["stage_ids"][0],
                "fixture_digest": profile["fixture_digest"], "adapter_digest": profile["adapter_digests"][0],
                "policy_digest": bound["manifest"]["policy_ref"]["digest"],
                "evaluator_digest": entry["evaluator_ref"]["digest"], "isolation_digest": profile["isolation_digest"],
            }
            observations = (self.worker._constraint_observation(code, state) if kind == "constraint"
                            else self.worker._mutation_observation(code, state))
            raw = canonical_bytes({"schema_version": 1, "kind": "gah_generic_result", "binding": binding,
                                   "mode": kind, "observations": observations})
            normalized = normalize_generic(raw, binding, execution_status="COMPLETED", exit_code=0, stop_confirmed=True)
            attempts.append({"schema_version": 1, "kind": "attempt_record", "attempt_id": f"attempt-{run_id}-{index:02d}",
                             "variant": "candidate", "retry_of": None, "started_at": reserve_at,
                             "finished_at": observe_at, "stop_confirmed": True, "execution_status": "COMPLETED",
                             "state_restored": True, "expected_binding": binding, "result": normalized})
        self.clock.value = 1100
        closed = store.dispatch(12004, 12004, request("resource_close", "close-" + run_id, run_id=run_id, **owner))
        self.assertTrue(closed["budget_closure"])
        for index, attempt in enumerate(attempts):
            self.clock.value = 1101 + index
            recorded = store.dispatch(12003, 12003, request("evidence_record", f"record-{run_id}-{index:02d}",
                                                              run_id=run_id, attempt=attempt))
            self.assertTrue(recorded["accepted"])
        self.clock.value = 1120
        final = store.dispatch(12004, 12004, request("evidence_finalize", "finalize-" + run_id, run_id=run_id))
        self.assertTrue(final["authority_connected"])
        self.assertFalse(final["ci_eligible"])
        return final

    def _adopt_baseline(self, store, run_id):
        self.clock.value = 1200
        proposed = store.dispatch(12001, 12001, request("baseline_propose", "baseline-propose-" + run_id,
            proposal_id="baseline-proposal-" + run_id, series_id="fixture-baseline-series", run_id=run_id,
            expected_generation=0))
        self.assertEqual(proposed["generation"], 1)
        proposal_time = self.clock.value
        self.clock.value = 1201
        validated = store.dispatch(12003, 12003, request("baseline_validate", "baseline-validate-" + run_id,
            proposal_id="baseline-proposal-" + run_id, validation_id="baseline-validation-" + run_id))
        self.assertTrue(validated["passed"])
        self.clock.value = 1202
        adopted = store.dispatch(12001, 12001, request("baseline_adopt", "baseline-adopt-" + run_id,
            proposal_id="baseline-proposal-" + run_id, validation_id="baseline-validation-" + run_id,
            expected_generation=0))
        self.assertTrue(adopted["adoption_verified"])
        self.assertFalse(adopted["ci_eligible"])
        row = store._db.execute("SELECT created_at FROM baseline_proposals WHERE proposal_id=?",
                                ("baseline-proposal-" + run_id,)).fetchone()
        self.assertEqual(row[0], proposal_time)
        return adopted

    def test_full_materialized_run_and_baseline_use(self):
        with self.open() as store:
            _, prepared, owner = self._prepare_contract_run(store)
            final = self._complete_all_entries(store, prepared, owner)
            self.assertTrue(final["input_materialization_verified"])
            adopted = self._adopt_baseline(store, prepared["bound_run"]["manifest"]["run_id"])
            current = store.dispatch(12004, 12004, request("baseline_current", "baseline-current-final",
                series_id="fixture-baseline-series"))
            self.assertTrue(current["valid"])
            baseline = current["baseline"]
            use = store.dispatch(12004, 12004, request("baseline_use", "baseline-use-final",
                series_id="fixture-baseline-series",
                expected_baseline_ref=content_ref("baseline", baseline["baseline_id"], baseline),
                expected_contract_ref=baseline["contract_ref"]))
            self.assertTrue(use["use"])
            self.assertFalse(use["ci_eligible"])
            with self.assertRaisesRegex(AdoptionError, "^BINDING_MISMATCH$"):
                store.dispatch(12004, 12004, request("baseline_use", "baseline-use-wrong-ref",
                    series_id="fixture-baseline-series",
                    expected_baseline_ref=content_ref("baseline", baseline["baseline_id"], {"tampered": True}),
                    expected_contract_ref=baseline["contract_ref"]))
            self.assertEqual(adopted["generation"], 1)

    def test_evidence_revoke_and_policy_actor_revocation_block_fresh_use(self):
        with self.open() as store:
            _, prepared, owner = self._prepare_contract_run(store, "revoke-run")
            final = self._complete_all_entries(store, prepared, owner)
            self._adopt_baseline(store, "revoke-run")
            store.dispatch(12004, 12004, request("revoke_actor", "revoke-validator-revoke-run",
                                                 actor_id="validator"))
            current_after_actor_revoke = store.dispatch(12004, 12004, request(
                "baseline_current", "baseline-current-actor-revoked", series_id="fixture-baseline-series"))
            self.assertFalse(current_after_actor_revoke["valid"])
            store.dispatch(12004, 12004, request("evidence_revoke", "evidence-revoke-revoke-run", run_id="revoke-run"))
            current = store.dispatch(12004, 12004, request("baseline_current", "baseline-current-revoked",
                series_id="fixture-baseline-series"))
            self.assertFalse(current["valid"])
            self.assertIn(current["reason"], {
                "BASELINE_REVOKED", "PREREQUISITE_UNAVAILABLE", "EVIDENCE_REVOKED", "VALIDATION_EXPIRED"
            })
            self.assertFalse(final["ci_eligible"])

    def test_saved_validation_digest_and_current_only_rows_cannot_fake_adoption(self):
        with self.open() as store:
            _, prepared, owner = self._prepare_contract_run(store, "corrupt-run")
            self._complete_all_entries(store, prepared, owner)
            self._adopt_baseline(store, "corrupt-run")
            row = store._db.execute("SELECT validation_id FROM baseline_adoptions WHERE series_id=?",
                                    ("fixture-baseline-series",)).fetchone()
            store._db.execute("UPDATE baseline_validations SET validation_digest=? WHERE validation_id=?",
                              ("0" * 64, row[0]))
            store._db.commit()
            with self.assertRaisesRegex(AdoptionError, "^(STORAGE_CORRUPT|VALIDATION_MISMATCH)$"):
                store.dispatch(12004, 12004, request("baseline_current", "baseline-current-corrupt",
                                                     series_id="fixture-baseline-series"))

    def test_current_row_without_immutable_adoption_history_is_rejected(self):
        with self.open() as store:
            _, prepared, owner = self._prepare_contract_run(store, "history-run")
            self._complete_all_entries(store, prepared, owner)
            self._adopt_baseline(store, "history-run")
            store._db.execute("DELETE FROM baseline_adoptions WHERE series_id=?",
                              ("fixture-baseline-series",))
            store._db.commit()
            with self.assertRaisesRegex(AdoptionError, "^(STORAGE_CORRUPT|ADOPTION_MISSING)$"):
                store.dispatch(12004, 12004, request("baseline_current", "baseline-current-history-missing",
                                                     series_id="fixture-baseline-series"))

    def test_baseline_adoption_trigger_failure_rolls_back_both_rows_and_retries(self):
        with self.open() as store:
            _, prepared, owner = self._prepare_contract_run(store, "trigger-run")
            self._complete_all_entries(store, prepared, owner)
            self.clock.value = 1200
            store.dispatch(12001, 12001, request(
                "baseline_propose", "baseline-propose-trigger-run",
                proposal_id="baseline-proposal-trigger-run", series_id="fixture-baseline-series",
                run_id="trigger-run", expected_generation=0))
            self.clock.value = 1201
            store.dispatch(12003, 12003, request(
                "baseline_validate", "baseline-validate-trigger-run",
                proposal_id="baseline-proposal-trigger-run", validation_id="baseline-validation-trigger-run"))
            adopt_request = request(
                "baseline_adopt", "baseline-adopt-trigger-run",
                proposal_id="baseline-proposal-trigger-run", validation_id="baseline-validation-trigger-run",
                expected_generation=0)
            store._db.execute("""
                CREATE TRIGGER fail_baseline_current
                AFTER INSERT ON baseline_current
                BEGIN
                    SELECT RAISE(ABORT, 'forced integration failure');
                END
            """)
            store._db.commit()
            self.clock.value = 1202
            with self.assertRaisesRegex(AdoptionError, "^STORAGE_FAILURE$"):
                store.dispatch(12001, 12001, adopt_request)
            self.assertEqual(store._db.execute(
                "SELECT COUNT(*) FROM baseline_adoptions WHERE series_id=?",
                ("fixture-baseline-series",)).fetchone()[0], 0)
            self.assertEqual(store._db.execute(
                "SELECT COUNT(*) FROM baseline_current WHERE series_id=?",
                ("fixture-baseline-series",)).fetchone()[0], 0)
            self.assertIsNone(store._db.execute(
                "SELECT response_json FROM idempotency WHERE request_id=?",
                ("baseline-adopt-trigger-run",)).fetchone())
            store._db.execute("DROP TRIGGER fail_baseline_current")
            store._db.commit()
            retried = store.dispatch(12001, 12001, adopt_request)
            self.assertTrue(retried["adoption_verified"])
            self.assertEqual(store._db.execute(
                "SELECT COUNT(*) FROM baseline_adoptions WHERE series_id=?",
                ("fixture-baseline-series",)).fetchone()[0], 1)
            self.assertEqual(store._db.execute(
                "SELECT COUNT(*) FROM baseline_current WHERE series_id=?",
                ("fixture-baseline-series",)).fetchone()[0], 1)

    def test_baseline_revocation_snapshot_tampering_is_rejected_by_current_and_use(self):
        with self.open() as store:
            _, prepared, owner = self._prepare_contract_run(store, "revocation-corrupt-run")
            self._complete_all_entries(store, prepared, owner)
            self._adopt_baseline(store, "revocation-corrupt-run")
            current = store.dispatch(12004, 12004, request(
                "baseline_current", "baseline-current-before-revocation-tamper",
                series_id="fixture-baseline-series"))
            baseline = current["baseline"]
            expected_baseline_ref = content_ref("baseline", baseline["baseline_id"], baseline)
            expected_contract_ref = baseline["contract_ref"]
            adopted_at = store._db.execute(
                "SELECT adopted_at FROM baseline_adoptions WHERE series_id=?",
                ("fixture-baseline-series",)).fetchone()[0]
            original = store._db.execute(
                "SELECT generation, observed_at, actor_id, context FROM baseline_revocations "
                "WHERE series_id=?", ("fixture-baseline-series",)).fetchone()
            self.assertIsNone(original)
            store.dispatch(12004, 12004, request(
                "baseline_revoke", "baseline-revoke-for-snapshot-tamper",
                series_id="fixture-baseline-series"))
            original = store._db.execute(
                "SELECT generation, observed_at, actor_id, context FROM baseline_revocations "
                "WHERE series_id=?", ("fixture-baseline-series",)).fetchone()
            self.assertIsNotNone(original)
            columns = ("generation", "actor_id", "context", "observed_at")
            tampered_values = (
                (0, original["observed_at"], original["actor_id"], original["context"]),
                (original["generation"], original["observed_at"], "manager", original["context"]),
                (original["generation"], original["observed_at"], original["actor_id"], "manager-context"),
                (original["generation"], adopted_at - 1, original["actor_id"], original["context"]),
            )
            for index, values in enumerate(tampered_values):
                with self.subTest(field=columns[index]):
                    store._db.execute(
                        "UPDATE baseline_revocations SET generation=?, observed_at=?, actor_id=?, context=? "
                        "WHERE series_id=?",
                        (*values, "fixture-baseline-series"))
                    store._db.commit()
                    with self.assertRaisesRegex(AdoptionError, "^STORAGE_CORRUPT$"):
                        store.dispatch(12004, 12004, request(
                            "baseline_current", f"baseline-current-revocation-corrupt-{index}",
                            series_id="fixture-baseline-series"))
                    with self.assertRaisesRegex(AdoptionError, "^STORAGE_CORRUPT$"):
                        store.dispatch(12004, 12004, request(
                            "baseline_use", f"baseline-use-revocation-corrupt-{index}",
                            series_id="fixture-baseline-series",
                            expected_baseline_ref=expected_baseline_ref,
                            expected_contract_ref=expected_contract_ref))
                    store._db.execute(
                        "UPDATE baseline_revocations SET generation=?, observed_at=?, actor_id=?, context=? "
                        "WHERE series_id=?",
                        (original["generation"], original["observed_at"],
                         original["actor_id"], original["context"], "fixture-baseline-series"))
                    store._db.commit()


if __name__ == "__main__":
    unittest.main()
