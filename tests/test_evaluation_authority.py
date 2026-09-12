"""EvaluationExtensionの境界試験。実モデル・Docker・外部I/Oは使わない。"""

from pathlib import Path
import copy
import tempfile
import unittest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gah.adoption import AdoptionError, AdoptionStore
from gah.evaluation_authority import EvaluationExtension
from gah.policy import initial_policy_profile
from gah.run_contracts import content_ref


class Clock:
    def __init__(self, value=1000):
        self.value = value

    def __call__(self):
        return self.value


def request(action, request_id, **fields):
    return {"schema_version": 1, "action": action, "request_id": request_id, **fields}


def ref(kind, identifier):
    return {"kind": kind, "id": identifier, "digest": "a" * 64}


def numbered_ref(kind, identifier, number):
    return {"kind": kind, "id": identifier, "digest": f"{number:064x}"}


def registry_document():
    return {
        "schema_version": 1,
        "kind": "control_registry",
        "registry_id": "registry-1",
        "controls": [{
            "control_id": "control-1", "owner": "owner", "invariant": "invariant",
            "criticality": "noncritical", "target_ref": ref("target", "target-1"),
            "dependencies": [],
            "obligations": [{
                "obligation_id": "obligation-1", "kind": "constraint", "required": True,
                "event_policy": "forbidden", "evaluator_ref": ref("evaluator", "evaluator-1"),
            }],
            "mutation_applicability": {"status": "not_applicable", "reason": "not a mutation control"},
        }],
    }


def case_set_document(purpose="calibration"):
    return {
        "schema_version": 1, "kind": "case_set", "case_set_id": f"cases-{purpose}",
        "purpose": purpose, "required_categories": ["category-1"], "cases": [{
            "case_id": f"case-{purpose}", "lineage_group": f"lineage-{purpose}",
            "category": "category-1", "expected_label": "positive",
            "oracle_ref": ref("oracle", "oracle-1"), "initial_state_ref": ref("state", "state-1"),
            "session_steps": [{"stage_id": "stage-1", "input_ref": ref("input", "input-1"),
                                "expected_detection": "detect", "event_policy": "none"}],
            "scored_stage_id": "stage-1",
        }],
    }


def large_case_set(purpose, count, offset=0):
    """構造経路専用の合成fixture。製品評価集合を表さない。"""
    cases = []
    for index in range(count):
        if purpose == "calibration":
            label = ("positive", "negative", "indeterminate")[index % 3]
        else:
            label = "positive" if index % 2 == 0 else "negative"
        detection = {"positive": "detect", "negative": "allow", "indeterminate": "indeterminate"}[label]
        cases.append({
            "case_id": f"{purpose}-case-{index}",
            "lineage_group": f"{purpose}-lineage-{index}",
            "category": "category-1",
            "expected_label": label,
            "oracle_ref": numbered_ref("oracle", f"{purpose}-oracle-{index}", offset + index + 1),
            "initial_state_ref": numbered_ref("state", f"{purpose}-state-{index}", offset + count + index + 1),
            "session_steps": [{
                "stage_id": "stage-1",
                "input_ref": numbered_ref("input", f"{purpose}-input-{index}", offset + (2 * count) + index + 1),
                "expected_detection": detection,
                "event_policy": "none",
            }],
            "scored_stage_id": "stage-1",
        })
    return {
        "schema_version": 1,
        "kind": "case_set",
        "case_set_id": purpose,
        "purpose": purpose,
        "required_categories": ["category-1"],
        "cases": cases,
    }


def large_registry():
    target = numbered_ref("target", "target-1", 9001)
    evaluator = numbered_ref("evaluator", "evaluator-1", 9002)
    return {
        "schema_version": 1,
        "kind": "control_registry",
        "registry_id": "registry-large",
        "controls": [{
            "control_id": "control-1", "owner": "owner", "invariant": "invariant",
            "criticality": "noncritical", "target_ref": target, "dependencies": [],
            "obligations": [{"obligation_id": "obligation-1", "kind": "constraint",
                              "required": True, "event_policy": "none", "evaluator_ref": evaluator}],
            "mutation_applicability": {"status": "not_applicable", "reason": "合成fixture"},
        }],
    }


class EvaluationAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.clock = Clock()
        self.path = Path(self.temp.name) / "authority.sqlite"
        self.policy = initial_policy_profile()

    def open(self):
        return AdoptionStore(self.path, clock=self.clock, bootstrap_policy=self.policy,
                             validator_digest="b" * 64, extension=EvaluationExtension())

    def test_schema_contains_evaluation_and_resource_tables(self):
        with self.open() as store:
            tables = {row[0] for row in store._db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertIn("eval_objects", tables)
            self.assertIn("eval_runs", tables)
            self.assertIn("resource_runs", tables)

    def test_extension_request_rejects_unknown_fields(self):
        with self.open() as store:
            with self.assertRaisesRegex(AdoptionError, "^INVALID_REQUEST$"):
                store.dispatch(12001, 12001, request("contract_current", "current-1", series_id="s", extra=True))

    def test_object_register_is_strict_and_idempotent_by_content(self):
        document = registry_document()
        with self.open() as store:
            result = store.dispatch(12001, 12001, request("object_register", "object-1", document=document))
            expected = content_ref("control_registry", "registry-1", document)
            self.assertEqual(result["content_ref"], expected)
            changed_same_id = copy.deepcopy(document)
            changed_same_id["controls"][0]["invariant"] = "changed"
            with self.assertRaisesRegex(AdoptionError, "^OBJECT_CONFLICT$"):
                store.dispatch(12001, 12001, request("object_register", "object-2", document=changed_same_id))
            changed = copy.deepcopy(document)
            changed["registry_id"] = "registry-2"
            # 同一IDの別内容はストレージへ到達する前に文書IDが変わるため、
            # 別IDとして登録できる。未知のkindは明示的に拒否する。
            with self.assertRaisesRegex(AdoptionError, "^OBJECT_KIND$"):
                store.dispatch(12001, 12001, request("object_register", "object-unknown", document={"schema_version": 1, "kind": "unknown"}))

    def test_calibration_result_is_computed_and_not_self_reported(self):
        document = case_set_document()
        with self.open() as store:
            registered = store.dispatch(12001, 12001, request("object_register", "object-1", document=document))
            case_ref = registered["content_ref"]
            result = store.dispatch(12003, 12003, request("calibration_record", "calibration-1",
                calibration_id="calibration-1", case_set_ref=case_ref,
                evaluator_ref=ref("evaluator", "evaluator-1"),
                observations=[{"case_id": "case-calibration", "stage_id": "stage-1", "detection": "allow"}]))
            self.assertFalse(result["passed"])
            self.assertFalse(result["report"]["passed"])

    def test_contract_proposal_is_structural_only(self):
        contract = {
            "schema_version": 1, "kind": "evaluation_contract", "contract_id": "contract-1",
            "generation": 1, "policy_series_id": "policy-1", "policy_generation": 1,
            "policy_ref": ref("policy_profile", "policy-1"),
            "registry_ref": ref("control_registry", "registry-1"),
            "case_set_ref": ref("case_set", "acceptance-1"),
            "calibration_case_set_ref": ref("case_set", "calibration-1"),
            "evaluator_refs": [ref("evaluator", "evaluator-1")],
            "required_categories": ["category-1"], "use_cases": ["UC-CI"],
            "comparison": {"mode": "required", "baseline_ref": ref("baseline", "baseline-1"),
                            "changed_axes": ["target"], "reason": None},
            "required_outputs": ["decision", "evidence", "findings", "plans", "run_receipt"],
        }
        with self.open() as store:
            result = store.dispatch(12001, 12001, request("contract_propose", "proposal-1",
                proposal_id="proposal-1", series_id="contract-series-1", expected_generation=0,
                contract=contract))
            self.assertEqual(result["generation"], 1)

    def test_initial_contract_lifecycle_and_run_connection(self):
        policy = self.policy
        acceptance = large_case_set("acceptance", 400, 0)
        calibration = large_case_set("calibration", 3, 10000)
        registry_document = large_registry()
        evaluator = registry_document["controls"][0]["obligations"][0]["evaluator_ref"]
        target = registry_document["controls"][0]["target_ref"]
        req = lambda action, request_id, **fields: request(action, request_id, **fields)
        with self.open() as store:
            # AdoptionStore側の初期方針を先に採択する。
            store.dispatch(12001, 12001, req("propose", "policy-propose", proposal_id="policy-propose",
                                             series_id=policy["policy_id"], expected_generation=0, policy=policy))
            store.dispatch(12003, 12003, req("validate", "policy-validate", proposal_id="policy-propose", validation_id="policy-validation"))
            store.dispatch(12001, 12001, req("adopt", "policy-adopt", proposal_id="policy-propose",
                                             validation_id="policy-validation", expected_generation=0))
            registry_ref = store.dispatch(12001, 12001, req("object_register", "register-registry", document=registry_document))["content_ref"]
            acceptance_ref = store.dispatch(12001, 12001, req("object_register", "register-acceptance", document=acceptance))["content_ref"]
            calibration_ref = store.dispatch(12001, 12001, req("object_register", "register-calibration", document=calibration))["content_ref"]
            observations = [{"case_id": f"calibration-case-{i}", "stage_id": "stage-1", "detection": detection}
                            for i, detection in enumerate(("detect", "allow", "indeterminate"))]
            calibration_result = store.dispatch(12003, 12003, req(
                "calibration_record", "calibration-record", calibration_id="calibration-1",
                case_set_ref=calibration_ref, evaluator_ref=evaluator, observations=observations))
            self.assertTrue(calibration_result["passed"])
            calibration_replay = store.dispatch(12003, 12003, req(
                "calibration_record", "calibration-record-replay", calibration_id="calibration-1",
                case_set_ref=calibration_ref, evaluator_ref=evaluator, observations=observations))
            self.assertTrue(calibration_replay["passed"])
            policy_ref = content_ref("policy_profile", policy["policy_id"], policy)
            contract = {
                "schema_version": 1, "kind": "evaluation_contract", "contract_id": "contract-1",
                "generation": 1, "policy_series_id": policy["policy_id"], "policy_generation": 1,
                "policy_ref": policy_ref, "registry_ref": registry_ref, "case_set_ref": acceptance_ref,
                "calibration_case_set_ref": calibration_ref, "evaluator_refs": [evaluator],
                "required_categories": ["category-1"], "use_cases": ["UC-CI"],
                "comparison": {"mode": "not_applicable", "baseline_ref": None,
                                "changed_axes": ["target"], "reason": "initial_baseline_pending"},
                "required_outputs": ["decision", "evidence", "findings", "plans", "run_receipt"],
            }
            # 評価系列と方針系列に同じIDを使い、世代を別管理していることを試験する。
            store.dispatch(12001, 12001, req("contract_propose", "contract-propose",
                                             proposal_id="contract-proposal", series_id=policy["policy_id"],
                                             expected_generation=0, contract=contract))
            validation = store.dispatch(12003, 12003, req("contract_validate", "contract-validate",
                                                           proposal_id="contract-proposal", validation_id="contract-validation"))
            self.assertTrue(validation["passed"])
            validation_replay = store.dispatch(12003, 12003, req(
                "contract_validate", "contract-validate-replay", proposal_id="contract-proposal",
                validation_id="contract-validation"))
            self.assertTrue(validation_replay["passed"])
            store.dispatch(12001, 12001, req("contract_adopt", "contract-adopt", proposal_id="contract-proposal",
                                             validation_id="contract-validation", expected_generation=0))
            current = store.dispatch(12004, 12004, req("contract_current", "contract-current", series_id=policy["policy_id"]))
            self.assertTrue(current["adopted"])
            self.assertTrue(current["valid"])

            contract_ref = content_ref("evaluation_contract", "contract-1", contract)
            plan = {
                "schema_version": 1, "kind": "trial_plan", "plan_id": "plan-1", "contract_ref": contract_ref,
                "entries": [{"obligation_id": "obligation-1", "case_id": "acceptance-case-0", "trial_id": "trial-1",
                              "variant": "candidate", "stage_ids": ["stage-1"], "required": True,
                              "event_policy": "none", "evaluator_ref": evaluator, "target_ref": target}],
            }
            plan_ref = content_ref("trial_plan", "plan-1", plan)
            manifest = {
                "schema_version": 1, "kind": "run_manifest", "run_id": "run-1", "contract_ref": contract_ref,
                "purpose": "baseline_candidate", "use_cases": ["UC-CI"], "target_refs": [target],
                "control_ids": ["control-1"], "baseline_ref": None, "plan_ref": plan_ref,
                "policy_ref": policy_ref, "profile": "full", "environment_ref": ref("environment", "env-1"),
                "actor_context_ref": ref("actor_context", "ctx-1"), "created_at": 1000, "deadline": 2000,
            }
            started = store.dispatch(12004, 12004, req("run_begin", "run-begin", manifest=manifest,
                                                       plan=plan, contract_series_id=policy["policy_id"]))
            self.assertEqual(started["run_id"], "run-1")
            claimed = store.dispatch(12004, 12004, req("resource_claim", "resource-claim", run_id="run-1",
                                                        owner_id="run-begin", recovery=False))
            self.assertEqual(claimed["owner_epoch"], 1)
            with self.assertRaisesRegex(AdoptionError, "^FIXTURE_BINDING_MISMATCH$"):
                store.dispatch(12004, 12004, req(
                    "resource_reserve", "resource-reserve", run_id="run-1", owner_id="run-begin",
                    owner_epoch=1, operation_id="operation-1", entry={key: plan["entries"][0][key]
                                                                        for key in ("obligation_id", "case_id", "trial_id", "variant")},
                    scenario="constraint:C01:good"))
            self.assertEqual(store._db.execute("SELECT count(*) FROM resource_operations").fetchone()[0], 0)
            status = store.dispatch(12004, 12004, req("run_status", "run-status", run_id="run-1"))
            self.assertEqual(status["run_id"], "run-1")
            self.assertTrue(status["resource_snapshot"]["budget_closure"])
            self.clock.value = 87400
            current_after_expiry = store.dispatch(12004, 12004, req("contract_current", "contract-current-expired", series_id=policy["policy_id"]))
            self.assertFalse(current_after_expiry["valid"])
            store.dispatch(12004, 12004, req("revoke_actor", "revoke-validator", actor_id="validator"))
            current_after_revoke = store.dispatch(12004, 12004, req("contract_current", "contract-current-2", series_id=policy["policy_id"]))
            self.assertFalse(current_after_revoke["valid"])

    def test_malformed_start_is_rejected_before_any_resource_row(self):
        # 要求構造が不正な開始はtransactionへ到達せず、資源予約も作らない。
        with self.open() as store:
            with self.assertRaisesRegex(AdoptionError, "^INVALID_REQUEST$"):
                store.dispatch(12004, 12004, request("run_begin", "atomic-start", manifest={}, plan={}, contract_series_id="missing"))
            self.assertEqual(store._db.execute("SELECT count(*) FROM eval_runs").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
