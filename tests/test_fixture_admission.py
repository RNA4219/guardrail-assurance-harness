"""固定fixture admissionのAdoptionStore統合境界を検査する。"""

from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gah.adoption import AdoptionError, AdoptionStore
from gah import fixture_admission
from gah.evaluation_authority import EvaluationExtension
from gah.policy import initial_policy_profile
from gah.run_contracts import content_ref


def request(action, request_id, **fields):
    return {"schema_version": 1, "action": action, "request_id": request_id, **fields}


class Clock:
    def __init__(self, value=1000):
        self.value = value

    def __call__(self):
        return self.value


class FixtureAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "fixture-admission.sqlite"
        self.clock = Clock()
        self.policy = initial_policy_profile()

    def open(self):
        return AdoptionStore(self.path, clock=self.clock, bootstrap_policy=self.policy,
                             validator_digest="b" * 64, extension=EvaluationExtension())

    def adopt_policy(self, store):
        store.dispatch(12001, 12001, request(
            "propose", "policy-propose", proposal_id="policy-proposal",
            series_id=self.policy["policy_id"], expected_generation=0,
            policy=deepcopy(self.policy)))
        store.dispatch(12003, 12003, request(
            "validate", "policy-validate", proposal_id="policy-proposal",
            validation_id="policy-validation"))
        return store.dispatch(12001, 12001, request(
            "adopt", "policy-adopt", proposal_id="policy-proposal",
            validation_id="policy-validation", expected_generation=0))

    def prepare(self, store, run_id="fixture-run", request_id=None):
        return store.dispatch(12001, 12001, request(
            "fixture_prepare", request_id or "fixture-prepare-" + run_id, run_id=run_id,
            policy_series_id=self.policy["policy_id"]))

    def test_prepare_contract_adoption_and_run_begin_use_real_bound_objects(self):
        with self.open() as store:
            adopted = self.adopt_policy(store)
            response = self.prepare(store)
            prepared = response["prepared"]
            bound = prepared["bound_run"]
            contract = bound["contract"]
            plan = bound["plan"]
            manifest = bound["manifest"]

            self.assertEqual(adopted["generation"], 1)
            self.assertEqual(response["kind"], "evaluation_authority_result")
            self.assertEqual(response["calibration"]["kind"], "fixture_measurement_calibration")
            self.assertTrue(response["calibration"]["passed"])
            self.assertFalse(response["calibration"]["authority_connected"])
            self.assertFalse(response["calibration"]["ci_eligible"])
            self.assertEqual(prepared["calibration_case_set"]["purpose"], "calibration")
            self.assertEqual(len(prepared["calibration_case_set"]["cases"]), 36)
            self.assertEqual(len(bound["case_set"]["cases"]), 15)
            self.assertEqual(store._db.execute("SELECT COUNT(*) FROM eval_objects").fetchone()[0], 3)

            proposed = store.dispatch(12001, 12001, request(
                "contract_propose", "contract-propose", proposal_id="fixture-proposal",
                series_id="fixture-contract-series", expected_generation=0,
                contract=deepcopy(contract)))
            self.assertEqual(proposed["generation"], 1)
            validated = store.dispatch(12003, 12003, request(
                "contract_validate", "contract-validate", proposal_id="fixture-proposal",
                validation_id="fixture-validation"))
            self.assertTrue(validated["passed"])
            adopted_contract = store.dispatch(12001, 12001, request(
                "contract_adopt", "contract-adopt", proposal_id="fixture-proposal",
                validation_id="fixture-validation", expected_generation=0))
            self.assertEqual(adopted_contract["generation"], 1)

            begun = store.dispatch(12004, 12004, request(
                "run_begin", "run-begin", manifest=deepcopy(manifest), plan=deepcopy(plan),
                contract_series_id="fixture-contract-series"))
            self.assertEqual(begun["run_id"], manifest["run_id"])
            self.assertEqual(begun["contract_generation"], 1)
            self.assertFalse(begun["ci_eligible"])
            status = store.dispatch(12004, 12004, request(
                "run_status", "run-status", run_id=manifest["run_id"]))
            self.assertEqual(status["manifest"], manifest)
            self.assertEqual(status["plan"], plan)

    def test_candidate_cannot_prepare_even_after_policy_adoption(self):
        with self.open() as store:
            self.adopt_policy(store)
            with self.assertRaisesRegex(AdoptionError, "^AUTHORITY_DENIED$"):
                store.dispatch(12002, 12002, request(
                    "fixture_prepare", "candidate-prepare", run_id="candidate-run",
                    policy_series_id=self.policy["policy_id"]))
            self.assertEqual(store._db.execute("SELECT COUNT(*) FROM fixture_admissions").fetchone()[0], 0)

    def test_prepare_requires_an_adopted_current_policy(self):
        with self.open() as store:
            with self.assertRaisesRegex(AdoptionError, "^PREREQUISITE_UNAVAILABLE$"):
                self.prepare(store, "unadopted-run")
            self.assertEqual(store._db.execute("SELECT COUNT(*) FROM fixture_admissions").fetchone()[0], 0)

    def test_saved_pack_payload_digest_conflict_is_rejected(self):
        with self.open() as store:
            self.adopt_policy(store)
            self.prepare(store)
            row = store._db.execute(
                "SELECT payload_json,digest FROM fixture_admissions WHERE run_id=?",
                ("fixture-run",),
            ).fetchone()
            self.assertIsNotNone(row)
            store._db.execute(
                "UPDATE fixture_admissions SET digest=? WHERE run_id=?",
                ("0" * 64, "fixture-run"),
            )
            store._db.commit()
            with self.assertRaisesRegex(AdoptionError, "^FIXTURE_ADMISSION_INVALID$"):
                self.prepare(store, request_id="fixture-prepare-recheck")
            unchanged = store._db.execute(
                "SELECT payload_json,digest FROM fixture_admissions WHERE run_id=?",
                ("fixture-run",),
            ).fetchone()
            self.assertEqual(unchanged[0], row[0])
            self.assertEqual(unchanged[1], "0" * 64)

    def test_execution_context_change_during_reverify_rolls_back_admission(self):
        with self.open() as store:
            self.adopt_policy(store)
            original = fixture_admission.execution_context
            calls = []
            lock = json.loads((ROOT / "config" / "fixture-runtime.lock.json").read_text(encoding="utf-8"))

            def changed_context():
                calls.append(True)
                if len(calls) == 1:
                    return original()
                fake_lock = deepcopy(lock)
                fake_lock["worker_digest"] = "a" * 64
                fake_profile = {
                    "fixture_digest": "b" * 64,
                    "adapter_digest": "c" * 64,
                    "isolation_digest": "d" * 64,
                }
                return b"changed-worker", fake_lock, fake_profile

            with patch.object(fixture_admission, "execution_context",
                              side_effect=changed_context):
                with self.assertRaisesRegex(AdoptionError, "^FIXTURE_ADMISSION_INVALID$"):
                    self.prepare(store, "context-drift-run")
            self.assertEqual(len(calls), 2)
            self.assertEqual(store._db.execute(
                "SELECT COUNT(*) FROM fixture_admissions").fetchone()[0], 0)
            self.assertEqual(store._db.execute(
                "SELECT COUNT(*) FROM eval_objects").fetchone()[0], 0)

    @staticmethod
    def _llm_case_set(purpose, count, evaluator):
        cases = []
        labels = ("positive", "negative", "indeterminate")
        for index in range(count):
            label = labels[index % 3] if purpose == "calibration" else (
                "positive" if index % 2 == 0 else "negative")
            detection = {"positive": "detect", "negative": "allow",
                         "indeterminate": "indeterminate"}[label]
            payload = {"purpose": purpose, "slot": index, "evaluator": evaluator["id"]}
            cases.append({
                "case_id": f"llm-{purpose}-{index}",
                "lineage_group": f"llm-{purpose}-lineage-{index}",
                "category": "llm-category",
                "expected_label": label,
                "oracle_ref": content_ref("oracle", f"llm-{purpose}-oracle-{index}",
                                           {**payload, "answer": detection}),
                "initial_state_ref": content_ref("initial_state", f"llm-{purpose}-state-{index}",
                                                  {**payload, "state": "clean"}),
                "session_steps": [{
                    "stage_id": f"llm-{purpose}-stage-{index}",
                    "input_ref": content_ref("input", f"llm-{purpose}-input-{index}", payload),
                    "expected_detection": detection,
                    "event_policy": "none",
                }],
                "scored_stage_id": f"llm-{purpose}-stage-{index}",
            })
        return {
            "schema_version": 1, "kind": "case_set",
            "case_set_id": f"llm-low-{purpose}", "purpose": purpose,
            "required_categories": ["llm-category"], "cases": cases,
        }

    def _register_low_count_llm_objects(self, store, prepared):
        base_contract = prepared["bound_run"]["contract"]
        evaluator = deepcopy(base_contract["evaluator_refs"][0])
        target = deepcopy(prepared["bound_run"]["manifest"]["target_refs"][0])
        acceptance = self._llm_case_set("acceptance", 20, evaluator)
        calibration = self._llm_case_set("calibration", 3, evaluator)
        registry = {
            "schema_version": 1, "kind": "control_registry",
            "registry_id": "llm-low-registry", "controls": [{
                "control_id": "llm-control", "owner": "fixture-supervisor",
                "invariant": "LLM detection metric is required",
                "criticality": "noncritical", "target_ref": target, "dependencies": [],
                "obligations": [{
                    "obligation_id": "llm-obligation", "kind": "llm_metric", "required": True,
                    "event_policy": "none", "evaluator_ref": evaluator,
                }],
                "mutation_applicability": {"status": "not_applicable", "reason": "LLM metric"},
            }],
        }
        for index, document in enumerate((registry, acceptance, calibration)):
            store.dispatch(12001, 12001, request(
                "object_register", f"llm-object-{index}", document=document))
        calibration_ref = content_ref("case_set", calibration["case_set_id"], calibration)
        observations = [{
            "case_id": case["case_id"], "stage_id": case["scored_stage_id"],
            "detection": case["session_steps"][0]["expected_detection"],
        } for case in calibration["cases"]]
        store.dispatch(12003, 12003, request(
            "calibration_record", "llm-calibration-record", calibration_id="llm-calibration",
            case_set_ref=calibration_ref, evaluator_ref=evaluator, observations=observations))
        contract = {
            "schema_version": 1, "kind": "evaluation_contract",
            "contract_id": "llm-low-contract", "generation": 1,
            "policy_series_id": base_contract["policy_series_id"],
            "policy_generation": base_contract["policy_generation"],
            "policy_ref": deepcopy(base_contract["policy_ref"]),
            "registry_ref": content_ref("control_registry", registry["registry_id"], registry),
            "case_set_ref": content_ref("case_set", acceptance["case_set_id"], acceptance),
            "calibration_case_set_ref": calibration_ref,
            "evaluator_refs": [evaluator], "required_categories": ["llm-category"],
            "use_cases": ["UC-LLM"],
            "comparison": {"mode": "not_applicable", "baseline_ref": None,
                           "changed_axes": ["target"], "reason": "initial_baseline_pending"},
            "required_outputs": deepcopy(base_contract["required_outputs"]),
        }
        return contract, acceptance, calibration

    def test_low_count_llm_contract_cannot_use_fixed_ci_admission(self):
        with self.open() as store:
            self.adopt_policy(store)
            prepared = self.prepare(store)["prepared"]
            low_count, acceptance, calibration = self._register_low_count_llm_objects(store, prepared)
            self.assertLess(len(acceptance["cases"]), 400)
            self.assertEqual(calibration["purpose"], "calibration")
            store.dispatch(12001, 12001, request(
                "contract_propose", "llm-contract-propose", proposal_id="llm-proposal",
                series_id="llm-series", expected_generation=0, contract=low_count))
            with self.assertRaisesRegex(AdoptionError, "^ACCEPTANCE_PREREQUISITE_UNAVAILABLE$"):
                store.dispatch(12003, 12003, request(
                    "contract_validate", "llm-contract-validate", proposal_id="llm-proposal",
                    validation_id="llm-validation"))
            self.assertIsNone(store._db.execute(
                "SELECT 1 FROM eval_current WHERE series_id=?", ("llm-series",)).fetchone())


if __name__ == "__main__":
    unittest.main()
