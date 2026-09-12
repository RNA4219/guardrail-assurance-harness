"""assurance authorityと固定fixture台帳の接続境界を検査する。"""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from gah.adoption import AdoptionError, AdoptionStore
from gah.assurance_authority import fixed_profile
from gah.evaluation_authority import EvaluationExtension
from gah.policy import initial_policy_profile
from gah.run_contracts import content_ref
from gah.wire import canonical_bytes
from tools.evaluation_fixture import documents, manifest


def request(action, request_id, **fields):
    return {"schema_version": 1, "action": action, "request_id": request_id, **fields}


class Clock:
    def __init__(self, value=1000):
        self.value = value

    def __call__(self):
        return self.value


class AssuranceAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "authority.sqlite"
        self.clock = Clock()
        self.policy = initial_policy_profile()
        lock = json.loads((ROOT / "config" / "fixture-runtime.lock.json").read_text(encoding="utf-8"))
        self.worker_digest = lock["worker_digest"]
        self.scenario = "mutation:F01:healthy"
        self.target = {"kind": "target", "id": "fixed-target", "digest": hashlib.sha256(
            canonical_bytes({"worker_digest": self.worker_digest, "scenario": self.scenario})
        ).hexdigest()}
        self.evaluator = {"kind": "evaluator", "id": "fixed-evaluator", "digest": self.worker_digest}

    def open(self):
        return AdoptionStore(
            self.path, clock=self.clock, bootstrap_policy=self.policy,
            validator_digest="b" * 64, extension=EvaluationExtension(),
        )

    @staticmethod
    def _policy_request(action, request_id, **fields):
        return request(action, request_id, **fields)

    def _setup_run(self, store, run_id="run-1"):
        policy = self.policy
        registry, acceptance, calibration, contract, plan = documents(policy, self.target, self.evaluator)
        obligation = registry["controls"][0]["obligations"][0]
        obligation["kind"] = "mutation"
        registry["controls"][0]["mutation_applicability"] = {
            "status": "applicable", "reason": None,
        }
        store.dispatch(12001, 12001, self._policy_request(
            "propose", "policy-propose", proposal_id="policy-proposal",
            series_id=policy["policy_id"], expected_generation=0, policy=policy,
        ))
        store.dispatch(12003, 12003, self._policy_request(
            "validate", "policy-validate", proposal_id="policy-proposal", validation_id="policy-validation",
        ))
        store.dispatch(12001, 12001, self._policy_request(
            "adopt", "policy-adopt", proposal_id="policy-proposal", validation_id="policy-validation",
            expected_generation=0,
        ))
        refs = []
        for index, document in enumerate((registry, acceptance, calibration)):
            refs.append(store.dispatch(12001, 12001, request(
                "object_register", f"object-{index}", document=document,
            ))["content_ref"])
        registry_ref, acceptance_ref, calibration_ref = refs
        observations = [{
            "case_id": case["case_id"], "stage_id": case["scored_stage_id"],
            "detection": case["session_steps"][0]["expected_detection"],
        } for case in calibration["cases"]]
        store.dispatch(12003, 12003, request(
            "calibration_record", "calibration-record", calibration_id="calibration-1",
            case_set_ref=calibration_ref, evaluator_ref=self.evaluator, observations=observations,
        ))
        policy_ref = content_ref("policy_profile", policy["policy_id"], policy)
        contract["registry_ref"] = registry_ref
        contract["case_set_ref"] = acceptance_ref
        contract["calibration_case_set_ref"] = calibration_ref
        contract["policy_ref"] = policy_ref
        contract_ref = content_ref("evaluation_contract", contract["contract_id"], contract)
        plan["contract_ref"] = contract_ref
        # documents() already contains these references; replacing them with the
        # registered references keeps the test independent of object identifiers.
        store.dispatch(12001, 12001, request(
            "contract_propose", "contract-propose", proposal_id="contract-proposal",
            series_id="evaluation-main", expected_generation=0, contract=contract,
        ))
        store.dispatch(12003, 12003, request(
            "contract_validate", "contract-validate", proposal_id="contract-proposal",
            validation_id="contract-validation",
        ))
        store.dispatch(12001, 12001, request(
            "contract_adopt", "contract-adopt", proposal_id="contract-proposal",
            validation_id="contract-validation", expected_generation=0,
        ))
        env = {"kind": "environment", "id": "fixed-docker-profile",
               "digest": fixed_profile()["isolation_digest"]}
        run_manifest = manifest(contract, plan, run_id, self.clock.value, env)
        begun = store.dispatch(12004, 12004, request(
            "run_begin", "begin-" + run_id, manifest=run_manifest, plan=plan,
            contract_series_id="evaluation-main",
        ))
        owner = {"run_id": run_id, "owner_id": "begin-" + run_id, "owner_epoch": 1}
        return {
            "registry": registry, "acceptance": acceptance, "calibration": calibration,
            "contract": contract, "plan": plan, "manifest": run_manifest,
            "begun": begun, "owner": owner,
        }

    def _open_and_reserve(self, store, run_id="run-1"):
        values = self._setup_run(store, run_id)
        opened = store.dispatch(12004, 12004, request(
            "evidence_open", "evidence-open-" + run_id, run_id=run_id,
        ))
        entry = values["plan"]["entries"][0]
        short_entry = {key: entry[key] for key in ("obligation_id", "case_id", "trial_id", "variant")}
        owner = values["owner"]
        self.clock.value = 1001
        store.dispatch(12004, 12004, request(
            "resource_claim", "claim-" + run_id, run_id=run_id,
            owner_id=owner["owner_id"], recovery=False,
        ))
        self.clock.value = 1002
        store.dispatch(12004, 12004, request(
            "resource_reserve", "reserve-" + run_id, **owner,
            operation_id="operation-" + run_id, entry=short_entry, scenario=self.scenario,
        ))
        self.clock.value = 1002
        store.dispatch(12004, 12004, request(
            "resource_dispatch", "dispatch-" + run_id, **owner,
            operation_id="operation-" + run_id,
        ))
        return values, opened, entry

    def _attempt(self, values, *, case_id=None, operation_id=None):
        entry = values["plan"]["entries"][0]
        profile = fixed_profile()
        binding = {
            "run_id": values["manifest"]["run_id"],
            "operation_id": operation_id or "operation-" + values["manifest"]["run_id"],
            "owner_epoch": 1,
            "contract_digest": values["manifest"]["contract_ref"]["digest"],
            "target_digest": entry["target_ref"]["digest"],
            "obligation_id": entry["obligation_id"],
            "case_id": case_id or entry["case_id"],
            "trial_id": entry["trial_id"], "stage_id": entry["stage_ids"][0],
            "fixture_digest": profile["fixture_digest"],
            "adapter_digest": profile["adapter_digests"][0],
            "policy_digest": values["manifest"]["policy_ref"]["digest"],
            "evaluator_digest": entry["evaluator_ref"]["digest"],
            "isolation_digest": profile["isolation_digest"],
        }
        result = {
            "schema_version": 1, "kind": "normalized_result", "binding": deepcopy(binding),
            "mode": "mutation", "observation": "PASS", "mutation_outcome": "KILLED",
            "detection": None, "deviation": None, "error_class": None, "raw_digest": "a" * 64,
        }
        return {
            "schema_version": 1, "kind": "attempt_record",
            "attempt_id": "attempt-" + values["manifest"]["run_id"], "variant": entry["variant"],
            "retry_of": None, "started_at": 1003, "finished_at": 1004,
            "stop_confirmed": True, "execution_status": "COMPLETED", "state_restored": True,
            "expected_binding": binding, "result": result,
        }

    def _settle_and_close(self, store, values):
        run_id = values["manifest"]["run_id"]
        owner = values["owner"]
        usage = {"input_tokens": 0, "output_tokens": 0, "cost_usd": "0"}
        self.clock.value = 1005
        observed = store.dispatch(12003, 12003, request(
            "resource_observe", "observe-" + run_id, run_id=run_id,
            operation_id="operation-" + run_id, event_id="event-" + run_id,
            stopped=True, usage=usage,
        ))
        self.assertTrue(observed["accepted"])
        return store.dispatch(12004, 12004, request(
            "resource_close", "close-" + run_id, **owner,
        ))

    def _advance_policy_generation(self, store):
        """同じ固定方針を次世代へ採択し、run側の旧束縛を検査する。"""
        proposal = store.dispatch(12001, 12001, request(
            "propose", "policy-propose-2", proposal_id="policy-proposal-2",
            series_id=self.policy["policy_id"], expected_generation=1, policy=self.policy,
        ))
        self.assertEqual(proposal["expected_generation"], 1)
        store.dispatch(12003, 12003, request(
            "validate", "policy-validate-2", proposal_id="policy-proposal-2",
            validation_id="policy-validation-2",
        ))
        return store.dispatch(12001, 12001, request(
            "adopt", "policy-adopt-2", proposal_id="policy-proposal-2",
            validation_id="policy-validation-2", expected_generation=1,
        ))

    def test_fixed_fixture_full_evidence_lifecycle_and_current_is_ineligible(self):
        with self.open() as store:
            values, opened, _ = self._open_and_reserve(store)
            self._settle_and_close(store, values)
            attempt = self._attempt(values)
            recorded = store.dispatch(12003, 12003, request(
                "evidence_record", "evidence-record-1", run_id="run-1", attempt=attempt,
            ))
            self.assertTrue(recorded["accepted"])
            finalized = store.dispatch(12004, 12004, request(
                "evidence_finalize", "evidence-finalize-1", run_id="run-1",
            ))
            self.assertTrue(finalized["authority_connected"])
            self.assertFalse(finalized["ci_eligible"])
            current = store.dispatch(12004, 12004, request(
                "evidence_current", "evidence-current-1", run_id="run-1",
                expected_bundle_digest=opened["bundle_digest"],
            ))
            self.assertFalse(current["use"])
            self.assertFalse(current["ci_eligible"])
            self.assertIn("INPUT_MATERIALIZATION_UNVERIFIED", current["reasons"])

    def test_candidate_cannot_open_or_record_and_unsettled_run_cannot_finalize(self):
        with self.open() as store:
            values, _, _ = self._open_and_reserve(store)
            with self.assertRaisesRegex(AdoptionError, "^AUTHORITY_DENIED$"):
                store.dispatch(12002, 12002, request("evidence_open", "candidate-open", run_id="run-1"))
            with self.assertRaisesRegex(AdoptionError, "^AUTHORITY_DENIED$"):
                store.dispatch(12002, 12002, request(
                    "evidence_record", "candidate-record", run_id="run-1", attempt=self._attempt(values),
                ))
            with self.assertRaisesRegex(AdoptionError, "^RESOURCE_CLOSURE_REQUIRED$"):
                store.dispatch(12004, 12004, request("evidence_finalize", "early-finalize", run_id="run-1"))

    def test_wrong_case_is_rejected_and_failed_record_rolls_back(self):
        with self.open() as store:
            values, _, _ = self._open_and_reserve(store)
            self._settle_and_close(store, values)
            bad = self._attempt(values, case_id="not-planned")
            with self.assertRaisesRegex(AdoptionError, "^(ENTRY_NOT_PLANNED|BINDING_MISMATCH)$"):
                store.dispatch(12003, 12003, request("evidence_record", "bad-record", run_id="run-1", attempt=bad))
            self.assertEqual(store._db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0], 0)
            self.assertEqual(store._db.execute("SELECT COUNT(*) FROM authority_attempt_origins").fetchone()[0], 0)

    def test_resource_manifest_replacement_is_rejected_before_evidence(self):
        with self.open() as store:
            values, _, _ = self._open_and_reserve(store)
            store._db.execute("BEGIN IMMEDIATE")
            try:
                store._db.execute("UPDATE resource_runs SET manifest_digest=? WHERE run_id=?", ("f" * 64, "run-1"))
                store._db.commit()
            except BaseException:
                store._db.rollback()
                raise
            with self.assertRaisesRegex(AdoptionError, "^(RESOURCE_MANIFEST_MISMATCH|RESOURCE_BINDING_MISMATCH|STORAGE_CORRUPT|OPERATION_NOT_SETTLED)$"):
                store.dispatch(12003, 12003, request(
                    "evidence_record", "manifest-replaced", run_id="run-1", attempt=self._attempt(values),
                ))
            self.assertEqual(store._db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0], 0)

    def test_finalize_replay_is_immutable_and_revoke_blocks_current_use(self):
        with self.open() as store:
            values, opened, _ = self._open_and_reserve(store)
            self._settle_and_close(store, values)
            store.dispatch(12003, 12003, request(
                "evidence_record", "record-for-replay", run_id="run-1", attempt=self._attempt(values),
            ))
            first = store.dispatch(12004, 12004, request("evidence_finalize", "finalize-1", run_id="run-1"))
            replay = store.dispatch(12004, 12004, request("evidence_finalize", "finalize-2", run_id="run-1"))
            immutable_first = {key: value for key, value in first.items() if key not in {"action", "request_id"}}
            immutable_replay = {key: value for key, value in replay.items() if key not in {"action", "request_id"}}
            self.assertEqual(immutable_first, immutable_replay)
            revoked = store.dispatch(12004, 12004, request("evidence_revoke", "revoke-1", run_id="run-1"))
            self.assertTrue(revoked["revoked"])
            current = store.dispatch(12004, 12004, request(
                "evidence_current", "current-after-revoke", run_id="run-1",
                expected_bundle_digest=opened["bundle_digest"],
            ))
            self.assertFalse(current["use"])
            self.assertIn("EVIDENCE_REVOKED", current["reasons"])

    def test_artifact_graph_corruption_is_rejected(self):
        with self.open() as store:
            values, opened, _ = self._open_and_reserve(store)
            self._settle_and_close(store, values)
            store.dispatch(12003, 12003, request(
                "evidence_record", "record-for-corruption", run_id="run-1", attempt=self._attempt(values),
            ))
            receipt = store.dispatch(12004, 12004, request("evidence_finalize", "finalize-corruption", run_id="run-1"))
            evidence_ref = receipt["evidence_ref"]
            row = store._db.execute(
                "SELECT payload_json FROM authority_artifacts WHERE kind=? AND id=? AND digest=?",
                (evidence_ref["kind"], evidence_ref["id"], evidence_ref["digest"]),
            ).fetchone()
            changed = json.loads(row[0])
            changed["subject_ref"] = deepcopy(changed["closure_ref"])
            raw = canonical_bytes(changed)
            store._db.execute(
                "UPDATE authority_artifacts SET payload_json=?,digest=? WHERE kind=? AND id=? AND digest=?",
                (raw.decode("utf-8"), hashlib.sha256(raw).hexdigest(), evidence_ref["kind"], evidence_ref["id"], evidence_ref["digest"]),
            )
            with self.assertRaisesRegex(AdoptionError, "^STORAGE_CORRUPT$"):
                store.dispatch(12004, 12004, request(
                    "evidence_current", "current-corrupt", run_id="run-1",
                    expected_bundle_digest=opened["bundle_digest"],
                ))

    def test_finalize_failure_rolls_back_all_late_rows_and_retries_same_request(self):
        with self.open() as store:
            values, _, _ = self._open_and_reserve(store)
            self._settle_and_close(store, values)
            store.dispatch(12003, 12003, request(
                "evidence_record", "record-trigger", run_id="run-1", attempt=self._attempt(values),
            ))
            store._db.execute("""
                CREATE TRIGGER fail_assurance_receipt AFTER INSERT ON authority_run_receipts
                BEGIN SELECT RAISE(ABORT, 'injected-finalize-failure'); END
            """)
            finalize = request("evidence_finalize", "finalize-trigger", run_id="run-1")
            with self.assertRaisesRegex(AdoptionError, "^(STORAGE_FAILURE|EXTENSION_FAILURE)$"):
                store.dispatch(12004, 12004, finalize)
            for table in ("terminals", "aggregates", "authority_artifacts", "authority_run_receipts"):
                self.assertEqual(store._db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0, table)
            self.assertIsNone(store._db.execute(
                "SELECT finalized_at FROM run_state WHERE run_id='run-1'"
            ).fetchone()[0])
            store._db.execute("DROP TRIGGER fail_assurance_receipt")
            retried = store.dispatch(12004, 12004, finalize)
            self.assertTrue(retried["authority_connected"])
            self.assertFalse(retried["ci_eligible"])

    def test_permission_revocation_keeps_old_receipt_but_blocks_current_use(self):
        with self.open() as store:
            values, opened, _ = self._open_and_reserve(store)
            self._settle_and_close(store, values)
            store.dispatch(12003, 12003, request(
                "evidence_record", "record-revocation", run_id="run-1", attempt=self._attempt(values),
            ))
            first = store.dispatch(12004, 12004, request("evidence_finalize", "finalize-revocation-1", run_id="run-1"))
            store.dispatch(12004, 12004, request("revoke_actor", "revoke-validator", actor_id="validator"))
            replay = store.dispatch(12004, 12004, request("evidence_finalize", "finalize-revocation-2", run_id="run-1"))
            self.assertEqual(
                {key: value for key, value in first.items() if key not in {"action", "request_id"}},
                {key: value for key, value in replay.items() if key not in {"action", "request_id"}},
            )
            current = store.dispatch(12004, 12004, request(
                "evidence_current", "current-revoked", run_id="run-1",
                expected_bundle_digest=opened["bundle_digest"],
            ))
            self.assertFalse(current["use"])
            self.assertIn("EVIDENCE_ORIGIN_INVALID", current["reasons"])

    def test_next_policy_generation_does_not_replace_old_run_binding(self):
        with self.open() as store:
            values, _, _ = self._open_and_reserve(store)
            self._settle_and_close(store, values)
            adopted = self._advance_policy_generation(store)
            self.assertEqual(adopted["generation"], 2)
            current = store.dispatch(12004, 12004, request(
                "current", "policy-current-2", series_id=self.policy["policy_id"],
            ))
            self.assertEqual(current["generation"], 2)
            recorded = store.dispatch(12003, 12003, request(
                "evidence_record", "record-old-policy", run_id="run-1", attempt=self._attempt(values),
            ))
            self.assertTrue(recorded["accepted"])
            finalized = store.dispatch(12004, 12004, request(
                "evidence_finalize", "finalize-old-policy", run_id="run-1",
            ))
            self.assertTrue(finalized["authority_connected"])
            self.assertFalse(finalized["ci_eligible"])


if __name__ == "__main__":
    unittest.main()
