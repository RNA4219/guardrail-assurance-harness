from __future__ import annotations

import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gah.baselines import bind_baseline_record, validate_baseline_record, validate_comparison_context, repeat_config_for_plan
from gah.contracts import ContractError
from gah.run_contracts import content_ref


def ref(kind: str, identifier: str, value: object) -> dict[str, str]:
    return content_ref(kind, identifier, value)


def fixtures() -> dict:
    target = ref("target", "target-1", {"version": 1})
    control = {
        "control_id": "control-1", "target_ref": target,
        "obligations": [], "dependencies": [],
    }
    registry = {"schema_version": 1, "kind": "control_registry", "registry_id": "registry-1",
                "controls": [control]}
    oracle = ref("oracle", "oracle-1", {"answer": "allow"})
    case_set = {"schema_version": 1, "kind": "case_set", "case_set_id": "case-set-1",
                "purpose": "acceptance", "cases": [{"case_id": "case-1", "oracle_ref": oracle}]}
    policy = {"schema_version": 1, "kind": "policy_profile", "policy_id": "policy-1", "version": 1}
    contract = {
        "schema_version": 1, "kind": "evaluation_contract", "contract_id": "contract-1",
        "generation": 1,
        "evaluator_refs": [ref("evaluator", "evaluator-1", {"v": 1})],
        "comparison": {"mode": "not_applicable", "baseline_ref": None,
                        "changed_axes": [], "reason": "initial_baseline_pending"},
    }
    repeat = {"schema_version": 1, "kind": "repeat_config", "repeat_config_id": "repeat-1",
              "repetitions": 1}
    plan = {"schema_version": 1, "kind": "trial_plan", "plan_id": "plan-1"}
    repeat = repeat_config_for_plan(plan)
    manifest = {
        "schema_version": 1, "kind": "run_manifest", "run_id": "run-1",
        "purpose": "baseline_candidate", "baseline_ref": None, "target_refs": [target],
        "created_at": 100,
    }
    context = {
        "schema_version": 1, "kind": "comparison_context", "comparison_id": "comparison-1",
        "mode": "not_applicable", "baseline_ref": None,
        "reason": "initial_baseline_pending", "changed_axes": [],
        "expected_contract_generation": 1, "expected_baseline_generation": 0,
        "contract_ref": ref("evaluation_contract", "contract-1", contract),
        "policy_ref": ref("policy_profile", "policy-1", policy),
        "target_refs": [target], "evaluator_refs": [ref("evaluator", "evaluator-1", {"v": 1})],
        "case_set_ref": ref("case_set", "case-set-1", case_set), "oracle_refs": [oracle],
        "repeat_config_ref": ref("repeat_config", repeat["repeat_config_id"], repeat),
    }
    decision = {"schema_version": 1, "kind": "run_decision", "run_id": "run-1",
                "purpose": "component_validation", "assurance": "HEALTHY", "ci_eligible": False}
    closure = {"schema_version": 1, "kind": "resource_closure", "closure_id": "closure-1",
               "run_id": "run-1", "manifest_digest": ref("run_manifest", "run-1", manifest)["digest"],
               "budget_closure": True}
    evidence = {
        "schema_version": 1, "kind": "evidence",
        "evidence_id": "evidence-1",
        "subject_ref": ref("run_manifest", "run-1", manifest),
        "conditions_ref": ref("bound_bundle", "run-1", {
            "manifest": manifest, "contract": contract, "plan": plan, "policy": policy,
            "registry": registry, "case_set": case_set, "selected_controls": ["control-1"],
            "ci_eligible": False,
        }),
        "decision_ref": ref("run_decision", "run-1", decision),
        "closure_ref": ref("resource_closure", "closure-1", closure),
        "purpose": "baseline_comparison",
        "observed_at": 100, "collected_at": 101, "retention_until": 3000000,
        "valid_until": 3000000, "permission_generation": 0,
        "authority_connected": True, "resource_closure_verified": True,
        "input_materialization_verified": False, "ci_eligible": False,
    }
    record = {
        "schema_version": 1, "kind": "baseline", "baseline_id": "baseline-1",
        "baseline_series_id": "baseline-series-1", "generation": 1,
        "contract_ref": ref("evaluation_contract", "contract-1", contract),
        "policy_ref": ref("policy_profile", "policy-1", policy),
        "registry_ref": ref("control_registry", "registry-1", registry),
        "case_set_ref": ref("case_set", "case-set-1", case_set),
        "target_refs": [target], "evaluator_refs": context["evaluator_refs"],
        "oracle_refs": [oracle], "repeat_config_ref": context["repeat_config_ref"],
        "source_run_ref": ref("run_manifest", "run-1", manifest),
        "trial_plan_ref": ref("trial_plan", "plan-1", plan),
        "decision_ref": ref("run_decision", "run-1", decision),
        "evidence_refs": [ref("evidence", "evidence-1", evidence)],
        "resource_closure_ref": ref("resource_closure", "closure-1", closure),
        "comparison_context_ref": ref("comparison_context", "comparison-1", context),
        "created_at": 101, "valid_until": 2592100,
    }
    return {"target": target, "control": control, "registry": registry, "case_set": case_set,
            "policy": policy, "contract": contract, "repeat": repeat, "plan": plan,
            "manifest": manifest, "context": context, "decision": decision, "closure": closure,
            "evidence": evidence, "record": record}


def bound(f: dict) -> dict:
    return {"manifest": f["manifest"], "contract": f["contract"], "policy": f["policy"],
            "registry": f["registry"], "case_set": f["case_set"], "plan": f["plan"],
            "selected_controls": ["control-1"], "repeat_config": f["repeat"],
            "comparison_context": f["context"], "ci_eligible": False}


def bind_record(f: dict, *, decision: dict | None = None, evidence: dict | None = None,
                evidence_states: dict | None = None) -> dict:
    core = {key: bound(f)[key] for key in
            ("manifest", "contract", "plan", "policy", "registry", "case_set",
             "selected_controls", "ci_eligible")}
    with patch("gah.baselines.bind_run_manifest", return_value=core):
        return bind_baseline_record(
            f["record"], bound_run=bound(f), decision=decision or f["decision"],
            evidences=[evidence or f["evidence"]], closure=f["closure"], now=200,
            evidence_states=evidence_states or {"evidence-1": {"revoked": False, "deleted": False}},
        )


class BaselineTests(unittest.TestCase):
    def test_initial_record_binds_without_authority_claim(self):
        f = fixtures()
        result = bind_record(f)
        self.assertTrue(result["structurally_bound"])
        self.assertFalse(result["adoption_verified"])
        self.assertFalse(result["source_binding_verified"])
        self.assertFalse(result["ci_eligible"])
        self.assertEqual(result["valid_until"], 2592100)

    def test_validators_are_strict_and_non_mutating(self):
        f = fixtures()
        original = copy.deepcopy(f["record"])
        self.assertEqual(validate_baseline_record(f["record"]), original)
        self.assertEqual(f["record"], original)
        for mutate in (lambda x: x.update(extra=True), lambda x: x.update(kind="baseline_record"),
                       lambda x: x.update(generation=True)):
            candidate = copy.deepcopy(f["record"])
            mutate(candidate)
            with self.assertRaises(ContractError):
                validate_baseline_record(candidate)
        bad_context = copy.deepcopy(f["context"])
        bad_context["changed_axes"] = ["evaluator"]
        with self.assertRaises(ContractError):
            validate_comparison_context(bad_context)

    def test_initial_cycle_and_content_ref_mismatch_are_rejected(self):
        f = fixtures()
        bad = copy.deepcopy(f["record"])
        bad["source_run_ref"] = bad["comparison_context_ref"]
        with self.assertRaises(ContractError):
            bind_record({**f, "record": bad})
        bad = copy.deepcopy(f["record"])
        bad["contract_ref"]["digest"] = "0" * 64
        with self.assertRaises(ContractError):
            bind_record({**f, "record": bad})

    def test_evidence_freshness_and_binding_failures_are_rejected(self):
        f = fixtures()
        for mutate in (
            lambda x: x.update(observed_at=201),
            lambda x: x.update(valid_until=150),
            lambda x: x.update(subject_ref=ref("run_manifest", "other", {"v": 1})),
        ):
            evidence = copy.deepcopy(f["evidence"])
            mutate(evidence)
            with self.subTest(mutate=mutate):
                with self.assertRaises(ContractError):
                    bind_record(f, evidence=evidence)
        with self.assertRaises(ContractError):
            bind_record(f, evidence_states={"evidence-1": {"revoked": True, "deleted": False}})

    def test_diagnostic_decision_is_structural_only(self):
        f = fixtures()
        decision = copy.deepcopy(f["decision"])
        decision["purpose"] = "diagnostic"
        f["record"]["decision_ref"] = ref("run_decision", "run-1", decision)
        evidence = copy.deepcopy(f["evidence"])
        evidence["decision_ref"] = f["record"]["decision_ref"]
        f["record"]["evidence_refs"] = [ref("evidence", "evidence-1", evidence)]
        result = bind_record(f, decision=decision, evidence=evidence)
        self.assertTrue(result["structurally_bound"])
        self.assertTrue(result["diagnostic_only"])
        self.assertFalse(result["adoption_verified"])

    def test_core_rebind_and_authority_refs_cannot_be_self_declared(self):
        f = fixtures()
        with patch("gah.baselines.bind_run_manifest", side_effect=ContractError("INVALID_CONTRACT")):
            with self.assertRaises(ContractError):
                bind_baseline_record(
                    f["record"], bound_run=bound(f), decision=f["decision"],
                    evidences=[f["evidence"]], closure=f["closure"], now=200,
                    evidence_states={"evidence-1": {"revoked": False, "deleted": False}},
                )
        evidence = copy.deepcopy(f["evidence"])
        evidence["conditions_ref"] = f["record"]["policy_ref"]
        f["record"]["evidence_refs"] = [ref("evidence", "evidence-1", evidence)]
        with self.assertRaises(ContractError):
            bind_record(f, evidence=evidence)

    def test_separate_decision_closure_and_state_identity_are_required(self):
        f = fixtures()
        decision = copy.deepcopy(f["decision"])
        decision["run_id"] = "other-run"
        f["record"]["decision_ref"] = ref("run_decision", "other-run", decision)
        with self.assertRaises(ContractError):
            bind_record(f, decision=decision)
        f = fixtures()
        with self.assertRaises(ContractError):
            bind_record(f, evidence_states={"evidence-1": {"revoked": False, "deleted": True}})


if __name__ == "__main__":
    unittest.main()
