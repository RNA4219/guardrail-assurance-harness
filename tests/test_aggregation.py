"""aggregation の固定分母、binding、配送、retry 境界を検査する。"""

from __future__ import annotations

import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from gah.aggregation import aggregate
from gah.contracts import ContractError
from gah.run_contracts import bind_run_manifest, content_ref
from test_run_contracts import _fixtures, _manifest, _plan


ZERO = "0" * 64


def _bound():
    f = _fixtures()
    plan = _plan(f)
    return f, plan, bind_run_manifest(
        _manifest(f, plan), f["contract"], plan, f["policy"], f["registry"], f["case_set"]
    )


def _binding(f, plan, obligation_id, *, attempt_id="a-1", stage_id="stage-1",
             case_id="case-1", trial_id="trial-1", variant="candidate"):
    entry = next(e for e in plan["entries"]
                 if e["obligation_id"] == obligation_id and e["variant"] == variant)
    return {
        "run_id": "run-1", "operation_id": "operation-1", "owner_epoch": 1,
        "contract_digest": f["contract_ref_digest"],
        "target_digest": entry["target_ref"]["digest"],
        "obligation_id": obligation_id, "case_id": case_id, "trial_id": trial_id,
        "stage_id": stage_id, "fixture_digest": ZERO, "adapter_digest": "1" * 64,
        "policy_digest": f["policy_ref_digest"],
        "evaluator_digest": entry["evaluator_ref"]["digest"], "isolation_digest": "2" * 64,
    }


def _two_case_bound():
    f = _fixtures()
    f["case_set"]["required_categories"] = ["category-1", "category-2"]
    first = copy.deepcopy(f["case_set"]["cases"][0])
    second = copy.deepcopy(first)
    second["case_id"] = "case-2"
    second["lineage_group"] = "lineage-case-2"
    second["category"] = "category-2"
    second["expected_label"] = "negative"
    second["oracle_ref"] = content_ref("oracle", "oracle-2", {"answer": "allow"})
    second["initial_state_ref"] = content_ref("initial_state", "state-case-2", {"state": "clean"})
    second["session_steps"][0]["input_ref"] = content_ref("input", "input-case-2", {"input": "fixed-2"})
    second["session_steps"][0]["expected_detection"] = "allow"
    f["case_set"]["cases"] = [first, second]
    f["contract"]["required_categories"] = ["category-1", "category-2"]
    f["contract"]["case_set_ref"] = content_ref(
        "case_set", f["case_set"]["case_set_id"], f["case_set"]
    )
    plan = _plan(f)
    plan["entries"].extend(
        {**entry, "case_id": "case-2", "trial_id": "trial-2"}
        for entry in list(plan["entries"])
        if entry["variant"] == "candidate"
    )
    plan["contract_ref"] = content_ref(
        "evaluation_contract", f["contract"]["contract_id"], f["contract"]
    )
    bound = bind_run_manifest(
        _manifest(f, plan), f["contract"], plan, f["policy"], f["registry"], f["case_set"]
    )
    f["contract_ref_digest"] = bound["manifest"]["contract_ref"]["digest"]
    f["policy_ref_digest"] = bound["manifest"]["policy_ref"]["digest"]
    return f, plan, bound


def _two_stage_bound():
    f = _fixtures()
    case = f["case_set"]["cases"][0]
    second_stage = copy.deepcopy(case["session_steps"][0])
    second_stage["stage_id"] = "stage-2"
    case["session_steps"].append(second_stage)
    case["scored_stage_id"] = "stage-2"
    f["contract"]["case_set_ref"] = content_ref(
        "case_set", f["case_set"]["case_set_id"], f["case_set"]
    )
    plan = _plan(f)
    for entry in plan["entries"]:
        entry["stage_ids"] = ["stage-1", "stage-2"]
    plan["contract_ref"] = content_ref(
        "evaluation_contract", f["contract"]["contract_id"], f["contract"]
    )
    bound = bind_run_manifest(
        _manifest(f, plan), f["contract"], plan, f["policy"], f["registry"], f["case_set"]
    )
    f["contract_ref_digest"] = bound["manifest"]["contract_ref"]["digest"]
    f["policy_ref_digest"] = bound["manifest"]["policy_ref"]["digest"]
    return f, plan, bound


def _different_baseline_bound():
    f = _fixtures("required")
    old_target = content_ref("target", "target-old", {"version": 0})
    plan = _plan(f, comparison="required", include_baseline=True)
    for entry in plan["entries"]:
        if entry["variant"] == "baseline":
            entry["target_ref"] = old_target
    plan["contract_ref"] = content_ref(
        "evaluation_contract", f["contract"]["contract_id"], f["contract"]
    )
    manifest = _manifest(f, plan, purpose="regression")
    manifest["baseline_ref"] = f["baseline"]
    context = {
        "baseline_ref": f["baseline"],
        "targets": [
            {"control_id": "control-ci", "target_ref": old_target},
            {"control_id": "control-llm", "target_ref": old_target},
        ],
    }
    bound = bind_run_manifest(
        manifest, f["contract"], plan, f["policy"], f["registry"], f["case_set"],
        baseline_context=context,
    )
    f["contract_ref_digest"] = bound["manifest"]["contract_ref"]["digest"]
    f["policy_ref_digest"] = bound["manifest"]["policy_ref"]["digest"]
    return f, plan, bound


def _record(binding, *, status="COMPLETED", stop=True, result=None,
            attempt_id="a-1", retry_of=None, restored=False, started_at=110,
            finished_at=111, variant="candidate"):
    return {
        "schema_version": 1, "kind": "attempt_record", "attempt_id": attempt_id,
        "variant": variant, "retry_of": retry_of, "started_at": started_at,
        "finished_at": finished_at, "stop_confirmed": stop, "execution_status": status,
        "state_restored": restored, "expected_binding": binding, "result": result,
    }


def _result(binding, mode, **values):
    result = {
        "schema_version": 1, "kind": "normalized_result", "binding": copy.deepcopy(binding),
        "mode": mode, "observation": None, "mutation_outcome": None,
        "detection": None, "deviation": None, "error_class": None, "raw_digest": ZERO,
    }
    result.update(values)
    return result


class AggregationTests(unittest.TestCase):
    def setUp(self):
        f, plan, bound = _bound()
        # manifest/contract refs are the canonical values used by the binding.
        f["contract_ref_digest"] = bound["manifest"]["contract_ref"]["digest"]
        f["policy_ref_digest"] = bound["manifest"]["policy_ref"]["digest"]
        self.f, self.plan, self.bound = f, plan, bound
        self.profile = {
            "fixture_digest": ZERO, "adapter_digests": ["1" * 64],
            "isolation_digest": "2" * 64,
        }

    def test_empty_attempts_keep_required_missing_and_zero_denominators(self):
        output = aggregate(self.bound, [], execution_profile=self.profile)
        self.assertTrue(output["required_missing"])
        self.assertFalse(output["ci_eligible"])
        self.assertTrue(all(metric["denominator"] == 0 and metric["baseline"] is None
                            for metric in output["metrics"]))

    def test_valid_result_and_same_content_delivery_fold(self):
        binding = _binding(self.f, self.plan, "obligation-constraint")
        record = _record(binding, result=_result(binding, "constraint", observation="PASS"))
        output = aggregate(self.bound, [record, copy.deepcopy(record)],
                           execution_profile=self.profile)
        self.assertEqual(output["counts"]["variant"]["candidate"]["delivery_count"], 2)
        self.assertEqual(output["counts"]["variant"]["candidate"]["duplicate_deliveries"], 1)
        self.assertEqual(output["counts"]["variant"]["candidate"]["constraint_pass"], 1)
        self.assertTrue(output["required_missing"])  # other planned obligations are absent

    def test_stop_unconfirmed_is_not_adopted(self):
        binding = _binding(self.f, self.plan, "obligation-constraint")
        record = _record(binding, stop=False,
                         result=_result(binding, "constraint", observation="PASS"))
        output = aggregate(self.bound, [record], execution_profile=self.profile)
        self.assertEqual(output["counts"]["variant"]["candidate"]["complete"], 0)
        self.assertTrue(output["required_missing"])

    def test_one_stopped_fault_then_restored_retry_is_selected(self):
        binding = _binding(self.f, self.plan, "obligation-constraint")
        failed = _record(binding, status="FAILED", result=None, attempt_id="a-1")
        retry = _record(binding, result=_result(binding, "constraint", observation="PASS"),
                        attempt_id="a-2", retry_of="a-1", restored=True, started_at=112, finished_at=113)
        output = aggregate(self.bound, [failed, retry], execution_profile=self.profile)
        self.assertEqual(output["counts"]["variant"]["candidate"]["complete"], 1)
        self.assertEqual(output["counts"]["variant"]["candidate"]["constraint_pass"], 1)
        self.assertFalse(any(issue["code"] == "INVALID_RETRY" for issue in output["issues"]))

    def test_llm_confusion_and_mutation_denominator_are_separate(self):
        constraint_binding = _binding(self.f, self.plan, "obligation-constraint",
                                      attempt_id="a-constraint")
        mutation_binding = _binding(self.f, self.plan, "obligation-mutation",
                                    attempt_id="a-mutation")
        llm_binding = _binding(self.f, self.plan, "obligation-llm",
                               attempt_id="a-llm")
        attempts = [
            _record(constraint_binding, attempt_id="a-constraint",
                    result=_result(constraint_binding, "constraint", observation="PASS")),
            _record(mutation_binding, attempt_id="a-mutation",
                    result=_result(mutation_binding, "mutation",
                                   observation="PASS", mutation_outcome="KILLED")),
            _record(llm_binding, attempt_id="a-llm",
                    result=_result(llm_binding, "llm", detection="detect", deviation=False)),
        ]
        output = aggregate(self.bound, attempts, execution_profile=self.profile)
        candidate = output["counts"]["variant"]["candidate"]
        self.assertEqual(candidate["tp"], 1)
        self.assertEqual(candidate["fn"], 0)
        self.assertEqual(candidate["killed"], 1)
        self.assertEqual(candidate["survived"] + candidate["no_coverage"], 0)
        self.assertEqual(next(m for m in output["metrics"] if m["name"] == "recall")
                         ["denominator"], 1)

    def test_positive_and_negative_categories_keep_independent_counts(self):
        f, plan, bound = _two_case_bound()
        profile = {"fixture_digest": ZERO, "adapter_digests": ["1" * 64],
                   "isolation_digest": "2" * 64}
        positive = _binding(f, plan, "obligation-llm",
                            attempt_id="a-positive")
        negative = _binding(f, plan, "obligation-llm", attempt_id="a-negative",
                            case_id="case-2", trial_id="trial-2")
        attempts = [
            _record(positive, attempt_id="a-positive",
                    result=_result(positive, "llm", detection="detect", deviation=False)),
            _record(negative, attempt_id="a-negative",
                    result=_result(negative, "llm", detection="allow", deviation=False)),
        ]
        output = aggregate(bound, attempts, execution_profile=profile)
        candidate = output["counts"]["variant"]["candidate"]
        self.assertEqual(candidate["tp"], 1)
        self.assertEqual(candidate["tn"], 1)
        self.assertEqual(output["counts"]["category"]["candidate"]["category-1"]["tp"], 1)
        self.assertEqual(output["counts"]["category"]["candidate"]["category-2"]["tn"], 1)
        self.assertIn("category-1",
                      output["counts"]["control_category"]["candidate"]["control-ci"])
        self.assertIn("category-2",
                      output["counts"]["obligation_category"]["candidate"]["obligation-llm"])
        self.assertTrue(all(scope["scope"] != "variant"
                            for scope in output["metric_scopes"].values()))

    def test_two_stage_missing_or_indeterminate_never_scores(self):
        f, plan, bound = _two_stage_bound()
        profile = {"fixture_digest": ZERO, "adapter_digests": ["1" * 64],
                   "isolation_digest": "2" * 64}
        stage2 = _binding(f, plan, "obligation-llm", stage_id="stage-2")
        result2 = _result(stage2, "llm", detection="detect", deviation=False)
        output = aggregate(bound, [_record(stage2, result=result2)],
                           execution_profile=profile)
        self.assertEqual(output["counts"]["variant"]["candidate"]["tp"], 0)
        self.assertTrue(output["required_missing"])
        stage1 = _binding(f, plan, "obligation-llm", stage_id="stage-1")
        result1 = _result(stage1, "llm", detection="indeterminate", deviation=False)
        output = aggregate(
            bound,
            [_record(stage1, result=result1),
             _record(stage2, result=result2, attempt_id="a-stage-2")],
            execution_profile=profile,
        )
        self.assertEqual(output["counts"]["variant"]["candidate"]["tp"], 0)
        self.assertEqual(output["counts"]["variant"]["candidate"]["detection_missing"], 1)
        self.assertTrue(output["required_missing"])

    def test_baseline_context_allows_declared_target_change_and_pairs_scope(self):
        f, plan, bound = _different_baseline_bound()
        profile = {"fixture_digest": ZERO, "adapter_digests": ["1" * 64],
                   "isolation_digest": "2" * 64}
        candidate = _binding(f, plan, "obligation-llm", variant="candidate")
        baseline = _binding(f, plan, "obligation-llm", variant="baseline",
                            attempt_id="a-baseline")
        attempts = [
            _record(candidate, attempt_id="a-candidate",
                    result=_result(candidate, "llm", detection="detect", deviation=False)),
            _record(baseline, attempt_id="a-baseline", variant="baseline",
                    result=_result(baseline, "llm", detection="detect", deviation=False)),
        ]
        output = aggregate(
            bound, attempts, execution_profile=profile,
            baseline_context={
                "baseline_ref": f["baseline"],
                "targets": [
                    {"control_id": "control-ci", "target_ref": plan["entries"][1]["target_ref"]},
                    {"control_id": "control-llm", "target_ref": plan["entries"][3]["target_ref"]},
                ],
            },
        )
        recall = next(metric for metric in output["metrics"]
                      if metric["name"] == "recall" and metric["baseline"] is not None)
        self.assertEqual(recall["baseline"], {"numerator": 1, "denominator": 1})

    def test_detection_and_deviation_missing_are_separate(self):
        binding = _binding(self.f, self.plan, "obligation-llm")
        result = _result(binding, "llm", detection="detect", deviation=None)
        output = aggregate(self.bound, [_record(binding, result=result)],
                           execution_profile=self.profile)
        candidate = output["counts"]["variant"]["candidate"]
        self.assertEqual(candidate["tp"], 1)
        self.assertEqual(candidate["deviation_missing"], 1)
        self.assertTrue(output["required_missing"])

    def test_constraint_unknown_is_missing_even_when_recorded(self):
        binding = _binding(self.f, self.plan, "obligation-constraint")
        result = _result(binding, "constraint", observation="UNKNOWN")
        output = aggregate(self.bound, [_record(binding, result=result)],
                           execution_profile=self.profile)
        candidate = output["counts"]["variant"]["candidate"]
        self.assertEqual(candidate["constraint_unknown"], 1)
        self.assertTrue(output["required_missing"])

    def test_execution_error_result_can_be_retry_parent(self):
        binding = _binding(self.f, self.plan, "obligation-constraint")
        error = _result(binding, None, mutation_outcome="ERROR",
                        error_class="EXECUTION_FAILURE", raw_digest=None)
        failed = _record(binding, status="FAILED", result=error)
        retry = _record(
            binding, attempt_id="a-retry", retry_of="a-1", restored=True,
            started_at=112, finished_at=113,
            result=_result(binding, "constraint", observation="PASS"),
        )
        output = aggregate(self.bound, [failed, retry], execution_profile=self.profile)
        self.assertEqual(output["counts"]["variant"]["candidate"]["complete"], 1)
        self.assertNotIn("RESULT_MODE_MISMATCH",
                         [issue["code"] for issue in output["issues"]])

    def test_definitive_result_retry_is_hold_material(self):
        binding = _binding(self.f, self.plan, "obligation-constraint")
        first = _record(binding, result=_result(binding, "constraint", observation="PASS"),
                        attempt_id="a-1")
        retry = _record(binding, result=_result(binding, "constraint", observation="FAIL"),
                        attempt_id="a-2", retry_of="a-1", restored=True, started_at=112, finished_at=113)
        output = aggregate(self.bound, [first, retry], execution_profile=self.profile)
        self.assertTrue(any(issue["code"] == "INVALID_RETRY" for issue in output["issues"]))
        self.assertEqual(output["counts"]["variant"]["candidate"]["complete"], 1)

    def test_unknown_attempt_field_is_fixed_contract_error(self):
        binding = _binding(self.f, self.plan, "obligation-constraint")
        record = _record(binding, result=_result(binding, "constraint", observation="PASS"))
        record["untrusted"] = True
        with self.assertRaises(ContractError):
            aggregate(self.bound, [record], execution_profile=self.profile)


if __name__ == "__main__":
    unittest.main()
