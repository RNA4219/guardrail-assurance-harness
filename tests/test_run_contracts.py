"""EvaluationContract/TrialPlan/RunManifestのstrict bind試験。"""

from __future__ import annotations

import copy
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gah.contracts import ContractError
from gah.corpus import validate_case_set
from gah.policy import initial_policy_profile
from gah.registry import validate_registry
from gah.run_contracts import (
    bind_evaluation_contract,
    bind_run_manifest,
    bind_trial_plan,
    content_ref,
    validate_evaluation_contract,
    validate_run_manifest,
    validate_trial_plan,
)


def _ref(kind: str, identifier: str, value: object) -> dict[str, str]:
    return content_ref(kind, identifier, value)


def _case_set(purpose: str, case_id: str = "case-1") -> dict:
    initial = {"state": "clean"}
    input_value = {"input": "fixed"}
    return {
        "schema_version": 1,
        "kind": "case_set",
        "case_set_id": purpose + "-set",
        "purpose": purpose,
        "required_categories": ["category-1"],
        "cases": [{
            "case_id": case_id,
            "lineage_group": "lineage-" + case_id,
            "category": "category-1",
            "expected_label": "positive",
            "oracle_ref": _ref("oracle", "oracle-1", {"answer": "detect"}),
            "initial_state_ref": _ref("initial_state", "state-" + case_id, initial),
            "session_steps": [{
                "stage_id": "stage-1",
                "input_ref": _ref("input", "input-" + case_id, input_value),
                "expected_detection": "detect",
                "event_policy": "none",
            }],
            "scored_stage_id": "stage-1",
        }],
    }


def _fixtures(comparison: str = "not_applicable") -> dict:
    target = _ref("target", "target-1", {"version": 1})
    evaluator_constraint = _ref("evaluator", "eval-constraint", {"family": "constraint"})
    evaluator_mutation = _ref("evaluator", "eval-mutation", {"family": "mutation"})
    evaluator_llm = _ref("evaluator", "eval-llm", {"family": "llm"})
    registry = {
        "schema_version": 1,
        "kind": "control_registry",
        "registry_id": "registry-1",
        "controls": [
            {
                "control_id": "control-ci",
                "owner": "owner",
                "invariant": "constraint and mutation are required",
                "criticality": "noncritical",
                "target_ref": target,
                "dependencies": [],
                "obligations": [
                    {"obligation_id": "obligation-constraint", "kind": "constraint", "required": True,
                     "event_policy": "none", "evaluator_ref": evaluator_constraint},
                    {"obligation_id": "obligation-mutation", "kind": "mutation", "required": True,
                     "event_policy": "none", "evaluator_ref": evaluator_mutation},
                ],
                "mutation_applicability": {"status": "applicable", "reason": None},
            },
            {
                "control_id": "control-llm",
                "owner": "owner",
                "invariant": "llm metric is required",
                "criticality": "noncritical",
                "target_ref": target,
                "dependencies": ["control-ci"],
                "obligations": [{"obligation_id": "obligation-llm", "kind": "llm_metric", "required": True,
                                 "event_policy": "none", "evaluator_ref": evaluator_llm}],
                "mutation_applicability": {"status": "not_applicable", "reason": "no mutation"},
            },
        ],
    }
    case_set = _case_set("acceptance")
    calibration_set = _case_set("calibration")
    policy = initial_policy_profile()
    baseline = _ref("baseline", "baseline-1", {"version": 1})
    contract = {
        "schema_version": 1,
        "kind": "evaluation_contract",
        "contract_id": "contract-1",
        "generation": 1,
        "policy_series_id": policy["policy_id"],
        "policy_generation": 1,
        "policy_ref": _ref("policy_profile", policy["policy_id"], policy),
        "registry_ref": _ref("control_registry", registry["registry_id"], registry),
        "case_set_ref": _ref("case_set", case_set["case_set_id"], case_set),
        "calibration_case_set_ref": _ref("case_set", calibration_set["case_set_id"], calibration_set),
        "evaluator_refs": [evaluator_constraint, evaluator_mutation, evaluator_llm],
        "required_categories": ["category-1"],
        "use_cases": ["UC-CI", "UC-LLM"],
        "comparison": {
            "mode": comparison,
            "baseline_ref": baseline if comparison == "required" else None,
            "changed_axes": ["target"],
            "reason": None if comparison == "required" else "initial_baseline_pending",
        },
        "required_outputs": ["decision", "evidence", "findings", "plans", "run_receipt"],
    }
    return {"policy": policy, "registry": registry, "case_set": case_set,
            "calibration_set": calibration_set, "contract": contract, "target": target,
            "evaluators": (evaluator_constraint, evaluator_mutation, evaluator_llm),
            "baseline": baseline}


def _plan(fixtures: dict, *, comparison: str = "not_applicable", include_baseline: bool = False) -> dict:
    target = fixtures["target"]
    evaluator_constraint, evaluator_mutation, evaluator_llm = fixtures["evaluators"]
    entries = []
    for obligation_id, evaluator in (("obligation-constraint", evaluator_constraint),
                                     ("obligation-mutation", evaluator_mutation),
                                     ("obligation-llm", evaluator_llm)):
        entries.append({"obligation_id": obligation_id, "case_id": "case-1", "trial_id": "trial-1",
                        "variant": "candidate", "stage_ids": ["stage-1"], "required": True,
                        "event_policy": "none", "evaluator_ref": evaluator, "target_ref": target})
        if include_baseline:
            entries.append({**entries[-1], "variant": "baseline"})
    return {"schema_version": 1, "kind": "trial_plan", "plan_id": "plan-1",
            "contract_ref": _ref("evaluation_contract", "contract-1", fixtures["contract"]),
            "entries": entries}


class ContentAndShapeTests(unittest.TestCase):
    def test_content_ref_is_canonical_and_strict(self) -> None:
        first = content_ref("target", "target-1", {"b": 2, "a": 1})
        second = content_ref("target", "target-1", {"a": 1, "b": 2})
        self.assertEqual(first, second)
        with self.assertRaises(ContractError):
            content_ref("target", True, {})

    def test_contract_rejects_unknown_fields_duplicate_and_bool_integer(self) -> None:
        value = _fixtures()["contract"]
        for mutate in (
            lambda x: x.update(extra=True),
            lambda x: x.update(generation=True),
            lambda x: x["evaluator_refs"].append(copy.deepcopy(x["evaluator_refs"][0])),
            lambda x: x["comparison"].update(mode="required", baseline_ref=None, reason=None),
        ):
            candidate = copy.deepcopy(value)
            mutate(candidate)
            with self.assertRaises(ContractError):
                validate_evaluation_contract(candidate)

    def test_content_ref_direct_values_follow_wire_limits(self) -> None:
        nested = 0
        for _ in range(16):
            nested = [nested]
        content_ref("target", "target-1", nested)
        content_ref("target", "target-1", {"min": -(2**53 - 1), "max": 2**53 - 1})
        content_ref("target", "target-1", [None] * 99999)
        for value in (
            [nested], [None] * 100000, {"n": 2**53}, {"n": -(2**53)},
            {1: "value"}, {"n": 1.5}, {"n": (1, 2)}, {"n": {1, 2}},
            {"n": "\ud800"},
        ):
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaises(ContractError):
                    content_ref("target", "target-1", value)

    def test_plan_and_manifest_reject_kind_or_stage_renaming(self) -> None:
        fixtures = _fixtures()
        plan = _plan(fixtures)
        renamed = copy.deepcopy(plan)
        renamed["entries"][0]["stage_ids"] = ["renamed-stage"]
        with self.assertRaises(ContractError):
            bind_trial_plan(renamed, fixtures["contract"], fixtures["registry"], fixtures["case_set"],
                            ["control-llm"], [fixtures["target"]])
        manifest = _manifest(fixtures, plan)
        wrong_kind = copy.deepcopy(manifest)
        wrong_kind["policy_ref"]["kind"] = "target"
        with self.assertRaises(ContractError):
            validate_run_manifest(wrong_kind)


def _manifest(fixtures: dict, plan: dict, *, purpose: str = "baseline_candidate", profile: str = "full") -> dict:
    contract = fixtures["contract"]
    policy = fixtures["policy"]
    return {
        "schema_version": 1,
        "kind": "run_manifest",
        "run_id": "run-1",
        "contract_ref": _ref("evaluation_contract", "contract-1", contract),
        "purpose": purpose,
        "use_cases": ["UC-CI", "UC-LLM"],
        "target_refs": [fixtures["target"]],
        "control_ids": ["control-llm"],
        "baseline_ref": None,
        "plan_ref": _ref("trial_plan", "plan-1", plan),
        "policy_ref": _ref("policy_profile", policy["policy_id"], policy),
        "profile": profile,
        "environment_ref": _ref("environment", "env-1", {"os": "test"}),
        "actor_context_ref": _ref("actor_context", "context-1", {"actor": "test"}),
        "created_at": 100,
        "deadline": 5500,
    }


class BindingTests(unittest.TestCase):
    def test_evaluation_binding_checks_refs_purposes_and_returns_copy(self) -> None:
        fixtures = _fixtures()
        bound = bind_evaluation_contract(fixtures["contract"], fixtures["policy"], fixtures["registry"],
                                         fixtures["case_set"], fixtures["calibration_set"])
        self.assertIs(bound["ci_eligible"], False)
        bound["contract"]["contract_id"] = "changed"
        self.assertEqual(fixtures["contract"]["contract_id"], "contract-1")
        wrong = copy.deepcopy(fixtures["case_set"])
        wrong["purpose"] = "development"
        with self.assertRaises(ContractError):
            bind_evaluation_contract(fixtures["contract"], fixtures["policy"], fixtures["registry"],
                                     wrong, fixtures["calibration_set"])

    def test_trial_binding_checks_dependency_closure_and_all_stage(self) -> None:
        fixtures = _fixtures()
        plan = _plan(fixtures)
        bound = bind_trial_plan(plan, fixtures["contract"], fixtures["registry"], fixtures["case_set"],
                                ["control-llm"], [fixtures["target"]])
        self.assertEqual(bound["selected_controls"], ["control-ci", "control-llm"])
        missing = copy.deepcopy(plan)
        missing["entries"] = missing["entries"][:-1]
        with self.assertRaises(ContractError):
            bind_trial_plan(missing, fixtures["contract"], fixtures["registry"], fixtures["case_set"],
                            ["control-llm"], [fixtures["target"]])

    def test_candidate_baseline_pair_and_non_applicable_rule(self) -> None:
        fixtures = _fixtures("required")
        plan = _plan(fixtures, comparison="required", include_baseline=True)
        bind_trial_plan(plan, fixtures["contract"], fixtures["registry"], fixtures["case_set"],
                        ["control-llm"], [fixtures["target"]])
        incomplete = copy.deepcopy(plan)
        incomplete["entries"] = [entry for entry in incomplete["entries"] if entry["variant"] != "baseline"]
        with self.assertRaises(ContractError):
            bind_trial_plan(incomplete, fixtures["contract"], fixtures["registry"], fixtures["case_set"],
                            ["control-llm"], [fixtures["target"]])
        not_applicable = _fixtures()
        with self.assertRaises(ContractError):
            bind_trial_plan(_plan(not_applicable, include_baseline=True), not_applicable["contract"],
                            not_applicable["registry"], not_applicable["case_set"], ["control-llm"],
                            [not_applicable["target"]])

    def test_manifest_binding_checks_purpose_use_cases_and_deadline(self) -> None:
        fixtures = _fixtures()
        plan = _plan(fixtures)
        manifest = _manifest(fixtures, plan)
        bound = bind_run_manifest(manifest, fixtures["contract"], plan, fixtures["policy"],
                                  fixtures["registry"], fixtures["case_set"])
        self.assertIs(bound["ci_eligible"], False)
        at_boundary = copy.deepcopy(manifest)
        at_boundary["deadline"] = 100 + fixtures["policy"]["profiles"]["full"]["elapsed_seconds"]
        bind_run_manifest(at_boundary, fixtures["contract"], plan, fixtures["policy"],
                          fixtures["registry"], fixtures["case_set"])
        too_late = copy.deepcopy(at_boundary)
        too_late["deadline"] += 1
        with self.assertRaises(ContractError):
            bind_run_manifest(too_late, fixtures["contract"], plan, fixtures["policy"],
                              fixtures["registry"], fixtures["case_set"])
        bootstrap = copy.deepcopy(manifest)
        bootstrap["purpose"] = "calibration"
        with self.assertRaises(ContractError):
            bind_run_manifest(bootstrap, fixtures["contract"], plan, fixtures["policy"],
                              fixtures["registry"], fixtures["case_set"])

    def test_changed_baseline_target_requires_declared_axis_and_bound_context(self) -> None:
        fixtures = _fixtures("required")
        plan = _plan(fixtures, comparison="required", include_baseline=True)
        old_target = content_ref("target", "target-old", {"version": 0})
        for entry in plan["entries"]:
            if entry["variant"] == "baseline":
                entry["target_ref"] = copy.deepcopy(old_target)
        context = {"baseline_ref": copy.deepcopy(fixtures["contract"]["comparison"]["baseline_ref"]),
            "targets": [{"control_id": item["control_id"], "target_ref": copy.deepcopy(old_target)}
                        for item in fixtures["registry"]["controls"]]}
        manifest = _manifest(fixtures, plan, purpose="regression")
        manifest["baseline_ref"] = context["baseline_ref"]
        bind_run_manifest(manifest, fixtures["contract"], plan, fixtures["policy"], fixtures["registry"],
                          fixtures["case_set"], baseline_context=context)
        with self.assertRaisesRegex(ContractError, "TARGET_MISMATCH"):
            bind_trial_plan(plan, fixtures["contract"], fixtures["registry"], fixtures["case_set"],
                            ["control-llm"], [fixtures["target"]])
        for mutate in (lambda x: x["baseline_ref"].update(digest="0" * 64),
                       lambda x: x["targets"].pop(),
                       lambda x: x["targets"][1].update(control_id=x["targets"][0]["control_id"])):
            bad = copy.deepcopy(context)
            mutate(bad)
            with self.assertRaises(ContractError):
                bind_trial_plan(plan, fixtures["contract"], fixtures["registry"], fixtures["case_set"],
                                ["control-llm"], [fixtures["target"]], baseline_context=bad)
        undeclared = copy.deepcopy(fixtures["contract"])
        undeclared["comparison"]["changed_axes"] = ["environment"]
        plan["contract_ref"] = content_ref("evaluation_contract", undeclared["contract_id"], undeclared)
        with self.assertRaisesRegex(ContractError, "UNDECLARED_TARGET_CHANGE"):
            bind_trial_plan(plan, undeclared, fixtures["registry"], fixtures["case_set"],
                            ["control-llm"], [fixtures["target"]], baseline_context=context)


if __name__ == "__main__":
    unittest.main()
