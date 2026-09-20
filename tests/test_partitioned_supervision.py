"""Partitioned schema-2 supervision routing without Docker or authority startup."""
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gah.llm_supervised_run import LlmSupervisor
from gah.run_contracts import content_ref


class PartitionedSupervisionTests(unittest.TestCase):
    def supervisor(self):
        value = object.__new__(LlmSupervisor)
        value.run_id = "partitioned-run"
        value.tag = "supervised-test"
        value.request = {"contract_series_id": "series-1"}
        value.call = Mock()
        return value

    def test_artifact_fetch_is_manifest_bound_and_nondurable(self):
        supervisor = self.supervisor()
        manifest_ref = {"kind": "run_manifest", "id": "partitioned-run", "digest": "a" * 64}
        artifact_ref = {"kind": "partitioned_input_artifact", "id": "artifact-1", "digest": "b" * 64}
        compact = {"run_id": "partitioned-run", "binding": {"manifest_ref": manifest_ref}, "ci_eligible": False}
        document = {"schema_version": 1, "kind": "small_artifact"}
        supervisor.call.return_value = {"schema_version": 1, "kind": "evaluation_authority_result",
            "action": "run_input_artifact", "request_id": "supervised-test-input-artifact-0",
            "run_id": "partitioned-run", "manifest_ref": manifest_ref,
            "artifact_ref": artifact_ref, "document": document, "ci_eligible": False}
        import gah.partitioned_llm_admission as admission
        original = admission.resolve_prepared
        seen = []
        try:
            def resolver(value, fetch_ref):
                seen.append(fetch_ref(artifact_ref))
                return {"resolved": True}
            admission.resolve_prepared = resolver
            self.assertEqual(supervisor._resolve_partitioned_prepared(compact), {"resolved": True})
        finally:
            admission.resolve_prepared = original
        self.assertEqual(seen, [document])
        supervisor.call.assert_called_once_with(12004, "run_input_artifact", "input-artifact-0",
            run_id="partitioned-run", expected_manifest_ref=manifest_ref, artifact_ref=artifact_ref)

    def test_partitioned_prepare_keeps_full_plan_and_fixed_count(self):
        from unittest.mock import patch
        import gah.execution_profiles as profiles
        import gah.partitioned_guardrail_results as partitioned_results
        supervisor = self.supervisor()
        supervisor.runner = Mock()
        supervisor.runner.lock = {"worker_digest": "f" * 64, "image_id": "sha256:fixture"}
        supervisor.runner.adapter_digest = "a" * 64
        supervisor.runner.isolation_digest = "i" * 64
        manifest = {"run_id": supervisor.run_id, "purpose": "regression", "profile": "full",
            "use_cases": ["UC-LLM"], "contract_ref": {"kind": "evaluation_contract",
            "id": "contract-1", "digest": "c" * 64}, "baseline_ref": {"kind": "baseline",
            "id": "baseline-1", "digest": "b" * 64}, "plan_ref": None}
        index = {"plan_id": "plan-1", "kind": "trial_plan_index"}
        manifest["plan_ref"] = content_ref("trial_plan_index", "plan-1", index)
        supervisor.request["expected_contract_ref"] = manifest["contract_ref"]
        contract = {"comparison": {"mode": "required"}}
        target_docs = {
            "baseline-v1": {"target_id": "target-base", "behavior_version": "baseline-v1",
                "runtime_image_id": "sha256:fixture"},
            "degraded-v2": {"target_id": "target-new", "behavior_version": "degraded-v2",
                "runtime_image_id": "sha256:fixture"},
        }
        targets = {version: content_ref("target", doc["target_id"], doc) for version, doc in target_docs.items()}
        evaluator = {"kind": "evaluator", "id": "evaluator-1", "digest": "e" * 64}
        entries = []
        for number in range(400):
            for variant, version in (("baseline", "baseline-v1"), ("candidate", "degraded-v2")):
                entries.append({"obligation_id": "obligation-1", "case_id": "case-" + str(number),
                    "trial_id": "trial-1", "variant": variant, "target_ref": targets[version],
                    "evaluator_ref": evaluator, "stage_ids": ["stage-1"]})
        bound = {"manifest": manifest, "contract": contract, "plan": {"plan_id": "plan-1", "entries": entries}}
        prepared = {"bound_run": bound, "materialization": {"kind": "partitioned_query_scale_materialization",
            "run_id": supervisor.run_id, "case_count": 400, "planned_trials": 800, "planned_stages": 800,
            "manifest_ref": content_ref("run_manifest", supervisor.run_id, manifest),
            "plan_index_ref": content_ref("trial_plan_index", "plan-1", index)},
            "plan_index": index, "execution_profile": {"schema_version": 2},
            "target_documents": target_docs}
        supervisor._resolve_partitioned_prepared = Mock(return_value=prepared)
        expected_profile = {"fixture_digest": "f" * 64, "adapter_digests": ["a" * 64],
            "isolation_digest": "i" * 64}
        with patch.object(profiles, "check_plan"), patch.object(profiles, "expected", return_value=expected_profile), \
                patch.object(partitioned_results, "PreparedCases", return_value=object()) as cases:
            self.assertEqual(supervisor._prepare_partitioned({}), manifest)
        cases.assert_called_once_with(prepared)
        self.assertEqual(len(supervisor.entries), 800)
        self.assertEqual(supervisor.case_count, 400)
        self.assertEqual(supervisor.plan_for_status, index)
        self.assertTrue(supervisor.partitioned)

    def test_partitioned_begin_uses_existing_operator_authority_action(self):
        supervisor = self.supervisor()
        supervisor.partitioned = True
        supervisor.manifest_ref = {"kind": "run_manifest", "id": "partitioned-run", "digest": "c" * 64}
        supervisor.begin_run()
        supervisor.call.assert_called_once_with(12004, "run_begin_partitioned", "begin", durable=True,
            run_id="partitioned-run", contract_series_id="series-1", expected_manifest_ref=supervisor.manifest_ref)

    def test_partitioned_runner_and_status_use_fixed_count_and_index(self):
        supervisor = self.supervisor()
        supervisor.partitioned = True
        supervisor.case_count = 400
        supervisor.manifest = {"deadline": 9000}
        supervisor.bound = {"plan": {"entries": []}}
        supervisor.plan_for_status = {"kind": "trial_plan_index", "entry_count": 800}
        supervisor.manifest_ref = {"digest": "d" * 64}
        request = {"schema_version": 1, "kind": "guardrail_case_request"}
        supervisor._case_request = Mock(return_value=request)
        supervisor.runner = Mock()
        supervisor.execute_runner("op-1", {}, {}, 3)
        supervisor.runner.run_partitioned.assert_called_once_with(request, case_count=400,
            run_deadline=9000, timeout_seconds=120)
        supervisor.call = Mock(return_value={"manifest": supervisor.manifest, "plan": supervisor.plan_for_status,
            "contract_series_id": "series-1", "resource_snapshot": {"manifest_digest": "d" * 64}})
        self.assertEqual(supervisor.status(), {"manifest_digest": "d" * 64})


if __name__ == "__main__":
    unittest.main()
