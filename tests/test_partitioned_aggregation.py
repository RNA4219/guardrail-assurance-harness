from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from gah.aggregation import aggregate
from gah.contracts import ContractError, MAX_DOCUMENT_BYTES
from gah.partitioned_aggregation import MAX_ATTEMPT_INPUT_BYTES, aggregate_partitioned
from gah.partitioned_trial_plan import partition_trial_plan
from gah.query_scale_data import build_scale_corpus
from gah.run_contracts import bind_run_manifest, content_ref
from test_aggregation import ZERO, _binding, _record, _result
from test_run_contracts import _fixtures, _manifest, _plan


def _v2_inputs(fixtures, plan, *, purpose=None, case_set=None):
    if case_set is not None:
        fixtures = copy.deepcopy(fixtures)
        fixtures["case_set"] = case_set
        fixtures["contract"]["case_set_ref"] = content_ref("case_set", case_set["case_set_id"], case_set)
        fixtures["contract"]["required_categories"] = copy.deepcopy(case_set["required_categories"])
    purpose = purpose or "baseline_candidate"
    index, segments = partition_trial_plan(plan)
    policy = fixtures["policy"]
    manifest = {
        "schema_version": 2, "kind": "run_manifest", "run_id": "run-1",
        "contract_ref": content_ref("evaluation_contract", fixtures["contract"]["contract_id"], fixtures["contract"]),
        "purpose": purpose, "use_cases": copy.deepcopy(fixtures["contract"]["use_cases"]),
        "target_refs": [copy.deepcopy(fixtures["target"])],
        "control_ids": [item["control_id"] for item in fixtures["registry"]["controls"]],
        "baseline_ref": copy.deepcopy(fixtures["baseline"]) if purpose == "regression" else None,
        "plan_ref": content_ref("trial_plan_index", index["plan_id"], index),
        "policy_ref": content_ref("policy_profile", policy["policy_id"], policy),
        "profile": "full",
        "environment_ref": content_ref("environment", "env-1", {"os": "test"}),
        "actor_context_ref": content_ref("actor_context", "context-1", {"actor": "test"}),
        "created_at": 100,
        "deadline": 100 + policy["profiles"]["full"]["elapsed_seconds"],
    }
    return fixtures, manifest, index, segments


class PartitionedAggregationTests(unittest.TestCase):
    def test_small_partitioned_result_matches_v1_core_fields(self):
        fixtures = _fixtures()
        plan = _plan(fixtures)
        v1_manifest = _manifest(fixtures, plan)
        bound = bind_run_manifest(v1_manifest, fixtures["contract"], plan, fixtures["policy"], fixtures["registry"], fixtures["case_set"])
        fixtures["contract_ref_digest"] = bound["manifest"]["contract_ref"]["digest"]
        fixtures["policy_ref_digest"] = bound["manifest"]["policy_ref"]["digest"]
        index, segments = partition_trial_plan(plan)
        manifest = copy.deepcopy(v1_manifest)
        manifest["schema_version"] = 2
        manifest["plan_ref"] = content_ref("trial_plan_index", index["plan_id"], index)
        profile = {"fixture_digest": ZERO, "adapter_digests": ["1" * 64], "isolation_digest": "2" * 64}
        binding = _binding(fixtures, plan, "obligation-constraint")
        attempt = _record(binding, result=_result(binding, "constraint", observation="PASS"))

        expected = aggregate(bound, [attempt], execution_profile=profile)
        actual = aggregate_partitioned(
            manifest, fixtures["contract"], index, segments, fixtures["policy"],
            fixtures["registry"], fixtures["case_set"], [attempt], execution_profile=profile,
        )
        self.assertEqual(actual["kind"], "partitioned_aggregation")
        self.assertEqual(actual["schema_version"], 2)
        self.assertIs(actual["ci_eligible"], False)
        self.assertEqual(actual["plan_index_ref"], content_ref("trial_plan_index", index["plan_id"], index))
        self.assertEqual(actual["manifest_ref"], content_ref("run_manifest", manifest["run_id"], manifest))
        actual_core = {k: v for k, v in actual.items() if k not in {"schema_version", "kind", "plan_index_ref", "manifest_ref"}}
        expected_core = {k: v for k, v in expected.items() if k not in {"schema_version", "kind"}}
        self.assertEqual(actual_core, expected_core)

    def test_large_1600_case_3200_entry_uses_only_partitioned_validation(self):
        fixtures = _fixtures("required")
        case_set = build_scale_corpus(1600)["case_set"]
        registry = copy.deepcopy(fixtures["registry"])
        registry["controls"] = [item for item in registry["controls"] if item["control_id"] == "control-llm"]
        registry["controls"][0]["dependencies"] = []
        fixtures["registry"] = registry
        fixtures["case_set"] = case_set
        contract = copy.deepcopy(fixtures["contract"])
        contract["registry_ref"] = content_ref("control_registry", registry["registry_id"], registry)
        contract["case_set_ref"] = content_ref("case_set", case_set["case_set_id"], case_set)
        contract["required_categories"] = copy.deepcopy(case_set["required_categories"])
        contract["evaluator_refs"] = [copy.deepcopy(fixtures["evaluators"][2])]
        fixtures["contract"] = contract
        entries = []
        for case in case_set["cases"]:
            trial_id = "trial-" + case["case_id"] + ("x" * 80)
            for variant in ("candidate", "baseline"):
                entries.append({
                    "obligation_id": "obligation-llm", "case_id": case["case_id"],
                    "trial_id": trial_id, "variant": variant,
                    "stage_ids": [stage["stage_id"] for stage in case["session_steps"]],
                    "required": True, "event_policy": "none",
                    "evaluator_ref": copy.deepcopy(fixtures["evaluators"][2]),
                    "target_ref": copy.deepcopy(fixtures["target"]),
                })
        plan = {
            "schema_version": 1, "kind": "trial_plan", "plan_id": "partition-aggregate-1600",
            "contract_ref": content_ref("evaluation_contract", contract["contract_id"], contract),
            "entries": entries,
        }
        self.assertEqual((len(case_set["cases"]), len(entries)), (1600, 3200))
        fixtures, manifest, index, segments = _v2_inputs(
            fixtures, plan, purpose="regression", case_set=case_set,
        )
        self.assertGreater(index["reconstructed_bytes"], MAX_DOCUMENT_BYTES)
        profile = {"fixture_digest": ZERO, "adapter_digests": ["1" * 64], "isolation_digest": "2" * 64}
        with patch("gah.run_contracts.validate_trial_plan", side_effect=AssertionError("v1 validator used")), \
             patch("gah.partitioned_trial_plan.validate_trial_plan", side_effect=AssertionError("v1 alias used")):
            result = aggregate_partitioned(
                manifest, fixtures["contract"], index, segments, fixtures["policy"],
                fixtures["registry"], case_set, [], execution_profile=profile,
            )
        self.assertEqual(result["counts"]["variant"]["candidate"]["planned"], 1600)
        self.assertEqual(result["counts"]["variant"]["baseline"]["planned"], 1600)
        self.assertTrue(result["required_missing"])

    def test_resigned_bad_ref_and_missing_variant_reject(self):
        fixtures = _fixtures("required")
        plan = _plan(fixtures, comparison="required", include_baseline=True)
        fixtures, manifest, index, segments = _v2_inputs(fixtures, plan, purpose="regression")
        profile = {"fixture_digest": ZERO, "adapter_digests": ["1" * 64], "isolation_digest": "2" * 64}

        bad_index = copy.deepcopy(index)
        bad_index["reconstructed_digest"] = "0" * 64
        bad_manifest = copy.deepcopy(manifest)
        bad_manifest["plan_ref"] = content_ref("trial_plan_index", bad_index["plan_id"], bad_index)
        with self.assertRaises(ContractError):
            aggregate_partitioned(bad_manifest, fixtures["contract"], bad_index, segments, fixtures["policy"], fixtures["registry"], fixtures["case_set"], [], execution_profile=profile)

        missing = copy.deepcopy(plan)
        removed = False
        kept = []
        for entry in missing["entries"]:
            if not removed and entry["variant"] == "baseline":
                removed = True
                continue
            kept.append(entry)
        missing["entries"] = kept
        _, missing_manifest, missing_index, missing_segments = _v2_inputs(fixtures, missing, purpose="regression")
        with self.assertRaises(ContractError):
            aggregate_partitioned(missing_manifest, fixtures["contract"], missing_index, missing_segments, fixtures["policy"], fixtures["registry"], fixtures["case_set"], [], execution_profile=profile)

    def test_attempt_scope_and_profile_binding_mismatch_are_not_accepted(self):
        fixtures = _fixtures()
        plan = _plan(fixtures)
        fixtures, manifest, index, segments = _v2_inputs(fixtures, plan)
        profile = {"fixture_digest": ZERO, "adapter_digests": ["1" * 64], "isolation_digest": "2" * 64}
        v1_bound = bind_run_manifest(_manifest(fixtures, plan), fixtures["contract"], plan,
                                     fixtures["policy"], fixtures["registry"], fixtures["case_set"])
        fixtures["contract_ref_digest"] = v1_bound["manifest"]["contract_ref"]["digest"]
        fixtures["policy_ref_digest"] = v1_bound["manifest"]["policy_ref"]["digest"]
        wrong = _binding(fixtures, plan, "obligation-constraint")
        wrong["run_id"] = "other-run"
        output = aggregate_partitioned(manifest, fixtures["contract"], index, segments, fixtures["policy"], fixtures["registry"], fixtures["case_set"], [_record(wrong, status="FAILED")], execution_profile=profile)
        self.assertTrue(output["integrity_failure"])
        self.assertIn("BINDING_MISMATCH", {item["code"] for item in output["issues"]})

        scoped = {
            "schema_version": 2, "kind": "execution_profile", "isolation_digest": "f" * 64,
            "bindings": [
                {"target_digest": entry["target_ref"]["digest"], "evaluator_digest": entry["evaluator_ref"]["digest"], "fixture_digest": ZERO, "adapter_digests": ["1" * 64]}
                for entry in plan["entries"]
            ],
        }
        unique = {(x["target_digest"], x["evaluator_digest"]): x for x in scoped["bindings"]}
        scoped["bindings"] = list(unique.values())
        with self.assertRaises(ContractError):
            aggregate_partitioned(manifest, fixtures["contract"], index, segments, fixtures["policy"], fixtures["registry"], fixtures["case_set"], [], execution_profile=scoped)

    def test_input_and_result_mutations_are_isolated_and_attempt_cap_is_bounded(self):
        fixtures = _fixtures()
        plan = _plan(fixtures)
        fixtures, manifest, index, segments = _v2_inputs(fixtures, plan)
        profile = {"fixture_digest": ZERO, "adapter_digests": ["1" * 64], "isolation_digest": "2" * 64}
        before = copy.deepcopy((manifest, fixtures, index, segments, profile))
        result = aggregate_partitioned(manifest, fixtures["contract"], index, segments, fixtures["policy"], fixtures["registry"], fixtures["case_set"], [], execution_profile=profile)
        result["counts"]["variant"]["candidate"]["planned"] = -1
        self.assertEqual((manifest, fixtures, index, segments, profile), before)
        again = aggregate_partitioned(manifest, fixtures["contract"], index, segments, fixtures["policy"], fixtures["registry"], fixtures["case_set"], [], execution_profile=profile)
        self.assertGreaterEqual(again["counts"]["variant"]["candidate"]["planned"], 0)
        with self.assertRaises(ContractError):
            aggregate_partitioned(manifest, fixtures["contract"], index, segments, fixtures["policy"], fixtures["registry"], fixtures["case_set"], iter(()), execution_profile=profile)
        self.assertEqual(MAX_ATTEMPT_INPUT_BYTES, 64 * 1024 * 1024)

    def test_attempt_byte_budget_stops_incrementally(self):
        import gah.partitioned_aggregation as module
        with patch.object(module, "MAX_ATTEMPT_INPUT_BYTES", 3), \
             patch.object(module._aggregation, "_canon", side_effect=[b"{}", b"{}", b"{}"] ) as canonical:
            with self.assertRaises(ContractError) as caught:
                module._snapshot_attempts([{}, {}, {}])
        self.assertEqual(caught.exception.code, "INPUT_LIMIT")
        self.assertEqual(canonical.call_count, 2)


    def test_attempt_iteration_cannot_exceed_cap_after_caller_list_growth(self):
        import gah.partitioned_aggregation as module
        values = [{}, {}]
        real_canonical = module._aggregation._canon
        calls = []
        def append_during_snapshot(value):
            calls.append(value)
            values.append({})
            return real_canonical(value)
        with patch.object(module._aggregation, "MAX_ATTEMPTS", 2), patch.object(module._aggregation, "_canon", side_effect=append_during_snapshot):
            with self.assertRaisesRegex(ContractError, "ATTEMPT_LIMIT"):
                module._snapshot_attempts(values)
        self.assertEqual(len(calls), 2)

    def test_oversized_aggregate_output_is_rejected_after_version_refs_added(self):
        import gah.partitioned_aggregation as module
        fixtures = _fixtures()
        plan = _plan(fixtures)
        fixtures, manifest, index, segments = _v2_inputs(fixtures, plan)
        profile = {"fixture_digest": ZERO, "adapter_digests": ["1" * 64], "isolation_digest": "2" * 64}
        core = module._aggregation._aggregate_validated
        def large_output(*args, **kwargs):
            value = core(*args, **kwargs)
            value["issues"] = [{"code": "BINDING_MISMATCH", "case_id": "c" * 64} for _ in range(14000)]
            return value
        with patch.object(module._aggregation, "_aggregate_validated", side_effect=large_output):
            with self.assertRaisesRegex(ContractError, "DOCUMENT_SIZE"):
                aggregate_partitioned(manifest, fixtures["contract"], index, segments, fixtures["policy"], fixtures["registry"], fixtures["case_set"], [], execution_profile=profile)


if __name__ == "__main__":
    unittest.main()
