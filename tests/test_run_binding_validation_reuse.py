"""Run manifest binding reuses already validated structural inputs safely."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from gah.contracts import ContractError
from gah import run_contracts
from gah.run_contracts import bind_run_manifest, bind_trial_plan, content_ref
from test_run_contracts import _fixtures, _manifest, _plan


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


class BindingValidationReuseTests(unittest.TestCase):
    def args(self, comparison="not_applicable", include_baseline=False):
        f = _fixtures(comparison)
        plan = _plan(f, comparison=comparison, include_baseline=include_baseline)
        manifest = _manifest(f, plan, purpose="baseline_candidate" if comparison == "not_applicable" else "regression")
        if comparison == "required":
            manifest["baseline_ref"] = copy.deepcopy(f["baseline"])
        return f, plan, manifest

    def test_run_binding_validates_each_shared_document_once_and_keeps_public_validation(self):
        f, plan, manifest = self.args()
        counts = {}
        names = ("validate_trial_plan", "validate_evaluation_contract", "validate_registry", "validate_case_set")
        patches = []
        for name in names:
            original = getattr(run_contracts, name)
            counter = mock.Mock(wraps=original)
            counts[name] = counter
            patches.append(mock.patch.object(run_contracts, name, counter))
        with patches[0], patches[1], patches[2], patches[3]:
            bound = bind_run_manifest(manifest, f["contract"], plan, f["policy"], f["registry"], f["case_set"])
        self.assertEqual({name: counter.call_count for name, counter in counts.items()}, {name: 1 for name in names})
        self.assertIs(bound["ci_eligible"], False)

        bad = copy.deepcopy(plan)
        bad["entries"][0]["stage_ids"] = ["forged-stage"]
        with self.assertRaises(ContractError):
            bind_trial_plan(bad, f["contract"], f["registry"], f["case_set"], ["control-llm"], [f["target"]])
        with self.assertRaises(ContractError):
            bind_trial_plan(None, f["contract"], f["registry"], f["case_set"], ["control-llm"], [f["target"]])

    def test_closure_stages_variants_and_returned_tree_are_bound_and_isolated(self):
        f, plan, manifest = self.args("required", include_baseline=True)
        original_inputs = copy.deepcopy((f, plan, manifest))
        context = {"baseline_ref": copy.deepcopy(f["baseline"]), "targets": [
            {"control_id": control["control_id"], "target_ref": copy.deepcopy(control["target_ref"])}
            for control in f["registry"]["controls"]
        ]}
        manifest["purpose"] = "regression"
        bound = bind_run_manifest(manifest, f["contract"], plan, f["policy"], f["registry"], f["case_set"], baseline_context=context)
        direct = bind_trial_plan(plan, f["contract"], f["registry"], f["case_set"], manifest["control_ids"], manifest["target_refs"], baseline_context=context)
        self.assertEqual(bound["selected_controls"], ["control-ci", "control-llm"])
        self.assertEqual(canonical(bound["selected_controls"]), canonical(direct["selected_controls"]))
        self.assertEqual((f, plan, manifest), original_inputs)
        bound["plan"]["entries"][0]["stage_ids"].append("mutated")
        bound["case_set"]["cases"][0]["session_steps"].clear()
        self.assertEqual(plan, original_inputs[1])
        self.assertEqual(f["case_set"], original_inputs[0]["case_set"])

        expected_codes = {
            "stage": "STAGE_MISMATCH",
            "variant": "BASELINE_MISSING",
            "dependency": "REQUIRED_OBLIGATION_MISSING",
        }
        for invalid_plan, expected_code in expected_codes.items():
            altered_plan = copy.deepcopy(plan)
            altered_manifest = copy.deepcopy(manifest)
            if invalid_plan == "stage":
                altered_plan["entries"][0]["stage_ids"] = ["wrong"]
            elif invalid_plan == "variant":
                altered_plan["entries"] = [entry for entry in altered_plan["entries"] if entry["variant"] != "baseline"]
            else:
                altered_plan["entries"] = [entry for entry in altered_plan["entries"] if entry["obligation_id"] != "obligation-constraint"]
            altered_manifest["plan_ref"] = content_ref("trial_plan", altered_plan["plan_id"], altered_plan)
            with self.subTest(invalid_plan=invalid_plan), self.assertRaisesRegex(ContractError, expected_code):
                bind_run_manifest(altered_manifest, f["contract"], altered_plan, f["policy"], f["registry"], f["case_set"], baseline_context=context)

    def test_public_trial_binding_checks_trial_case_partition_linearly(self):
        for shared_trial, comparison, include_baseline, should_reject in (
            (True, "not_applicable", False, True),
            (False, "not_applicable", False, False),
            (False, "required", True, False),
        ):
            with self.subTest(shared_trial=shared_trial, comparison=comparison, include_baseline=include_baseline):
                f = _fixtures(comparison)
                second_case = copy.deepcopy(f["case_set"]["cases"][0])
                second_case["case_id"] = "case-2"
                second_case["lineage_group"] = "lineage-case-2"
                f["case_set"]["cases"].append(second_case)
                f["contract"]["case_set_ref"] = content_ref("case_set", f["case_set"]["case_set_id"], f["case_set"])
                entries = []
                obligations = (
                    ("obligation-constraint", f["evaluators"][0]),
                    ("obligation-mutation", f["evaluators"][1]),
                    ("obligation-llm", f["evaluators"][2]),
                )
                for case_index, case_id in enumerate(("case-1", "case-2")):
                    trial_id = "shared-trial" if shared_trial else f"trial-{case_index}"
                    for obligation_id, evaluator in obligations:
                        entry = {
                            "obligation_id": obligation_id, "case_id": case_id, "trial_id": trial_id,
                            "variant": "candidate", "stage_ids": ["stage-1"], "required": True,
                            "event_policy": "none", "evaluator_ref": evaluator, "target_ref": f["target"],
                        }
                        entries.append(entry)
                        if include_baseline:
                            entries.append({**entry, "variant": "baseline"})
                plan = {
                    "schema_version": 1, "kind": "trial_plan", "plan_id": "multi-plan",
                    "contract_ref": content_ref("evaluation_contract", f["contract"]["contract_id"], f["contract"]),
                    "entries": entries,
                }
                if should_reject:
                    with self.assertRaisesRegex(ContractError, "TRIAL_CASE_MISMATCH"):
                        bind_trial_plan(plan, f["contract"], f["registry"], f["case_set"], ["control-llm"], [f["target"]])
                else:
                    bound = bind_trial_plan(plan, f["contract"], f["registry"], f["case_set"], ["control-llm"], [f["target"]])
                    self.assertEqual(bound["selected_controls"], ["control-ci", "control-llm"])

    def test_bound_canonical_bytes_match_independent_public_plan_binding(self):
        f, plan, manifest = self.args()
        result = bind_run_manifest(manifest, f["contract"], plan, f["policy"], f["registry"], f["case_set"])
        independently_bound = bind_trial_plan(plan, f["contract"], f["registry"], f["case_set"], manifest["control_ids"], manifest["target_refs"])
        expected = {
            "manifest": run_contracts.validate_run_manifest(manifest),
            "contract": run_contracts.validate_evaluation_contract(f["contract"]),
            "plan": run_contracts.validate_trial_plan(plan),
            "policy": run_contracts.validate_policy_profile(f["policy"]),
            "registry": run_contracts.validate_registry(f["registry"]),
            "case_set": run_contracts.validate_case_set(f["case_set"]),
            "selected_controls": independently_bound["selected_controls"],
            "ci_eligible": False,
        }
        self.assertEqual(canonical(result), canonical(expected))


if __name__ == "__main__":
    unittest.main()
