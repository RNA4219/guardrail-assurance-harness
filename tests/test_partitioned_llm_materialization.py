from __future__ import annotations

import copy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gah.contracts import ContractError
from gah.llm_materialization import target_document
from gah.partitioned_llm_materialization import build, evaluator_document
from gah.partitioned_scale_corpus import restore_scale_corpus
from gah.partitioned_trial_plan import restore_trial_plan
from gah.policy import initial_policy_profile
from gah.run_contracts import content_ref


IMAGE_ID = "sha256:" + "a" * 64
WORKER_DIGEST = "b" * 64
ISOLATION = {"schema_version": 1, "kind": "test_isolation", "network": "none"}


class PartitionedLlmMaterializationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy = initial_policy_profile()

    def _build(self, count=400, **kwargs):
        return build(
            self.policy,
            case_count=count,
            policy_generation=1,
            run_id=kwargs.pop("run_id", f"qscale-test-{count}-run"),
            now=1000,
            target_version=kwargs.pop("target_version", "baseline-v1"),
            image_id=IMAGE_ID,
            worker_digest=WORKER_DIGEST,
            isolation_profile=ISOLATION,
            **kwargs,
        )

    def test_fixed_counts_restore_complete_single_stage_corpus_and_plan(self):
        for count in (400, 800, 1600):
            with self.subTest(case_count=count):
                result = self._build(count, run_id=f"qscale-{count}")
                corpus = restore_scale_corpus(
                    result["corpus_index"],
                    result["case_set_index"],
                    result["case_set_segments"],
                    result["document_segments"],
                )
                plan = restore_trial_plan(result["plan_index"], result["plan_segments"])
                self.assertEqual(len(corpus["case_set"]["cases"]), count)
                self.assertEqual(len(plan["entries"]), count)
                self.assertTrue(all(len(case["session_steps"]) == 1 for case in corpus["case_set"]["cases"]))
                self.assertTrue(all(entry["variant"] == "candidate" for entry in plan["entries"]))
                self.assertEqual(result["materialization"]["corpus_family"], "query-scale-single-stage-v1")
                self.assertFalse(result["bound_run"]["ci_eligible"])
                self.assertFalse(result["materialization"]["admission_verified"])

    def test_evaluator_is_bound_to_exact_corpus_index_and_source_hashes(self):
        result = self._build(run_id="qscale-evaluator")
        evaluator = evaluator_document(result["corpus_index"])
        self.assertEqual(evaluator, result["evaluator_document"])
        self.assertEqual(evaluator["corpus_index_ref"], content_ref(
            "query_scale_corpus_index", result["corpus_index"]["corpus_id"], result["corpus_index"]
        ))
        self.assertIn("partitioned_guardrail_results.py", evaluator["source_sha256"])

    def test_generation_two_pairs_each_case_with_fixed_prior_target(self):
        for count in (400, 800, 1600):
            with self.subTest(count=count):
                first = self._build(count, run_id="qscale-before")
                old_target = target_document("baseline-v1", IMAGE_ID)
                baseline_ref = content_ref("baseline", "qscale-baseline", {"generation": 1})
                second = build(
                    self.policy,
                    case_count=count,
                    policy_generation=1,
                    run_id="qscale-after",
                    now=2000,
                    target_version="degraded-v2",
                    image_id=IMAGE_ID,
                    worker_digest=WORKER_DIGEST,
                    isolation_profile=ISOLATION,
                    generation=2,
                    baseline_context={
                        "baseline_ref": baseline_ref,
                        "targets": [{"control_id": "LC-query-scale", "target_ref": content_ref(
                            "target", old_target["target_id"], old_target
                        )}],
                        "contract": first["contract"],
                    },
                )
                plan = restore_trial_plan(second["plan_index"], second["plan_segments"])
                self.assertEqual(len(plan["entries"]), count * 2)
                self.assertEqual({entry["variant"] for entry in plan["entries"]}, {"baseline", "candidate"})
                self.assertEqual(second["manifest"]["baseline_ref"], baseline_ref)
                self.assertEqual(second["baseline_contract"], first["contract"])

    def test_unknown_counts_boolean_and_unknown_target_fail_early(self):
        for count in (True, 399, 401, 2000):
            with self.subTest(count=count), self.assertRaises(ContractError):
                self._build(count)
        with self.assertRaises(ContractError):
            self._build(target_version="future-v3")

    def test_baseline_contract_must_preserve_case_evaluator_and_policy(self):
        first = self._build(run_id="qscale-baseline-input")
        target = target_document("baseline-v1", IMAGE_ID)
        baseline_context = {
            "baseline_ref": content_ref("baseline", "qscale-baseline-invalid", {"generation": 1}),
            "targets": [{"control_id": "LC-query-scale", "target_ref": content_ref(
                "target", target["target_id"], target
            )}],
            "contract": copy.deepcopy(first["contract"]),
        }
        baseline_context["contract"]["case_set_ref"]["digest"] = "0" * 64
        with self.assertRaises(ContractError):
            build(
                self.policy, case_count=400, policy_generation=1, run_id="qscale-baseline-invalid",
                now=2000, target_version="degraded-v2", image_id=IMAGE_ID,
                worker_digest=WORKER_DIGEST, isolation_profile=ISOLATION,
                generation=2, baseline_context=baseline_context,
            )

    def test_repeated_runs_share_contract_but_have_distinct_run_and_plan_refs(self):
        first = self._build(run_id="same-condition-first")
        second = self._build(run_id="same-condition-second")
        self.assertEqual(first["contract"], second["contract"])
        self.assertEqual(first["registry"], second["registry"])
        self.assertNotEqual(first["manifest"]["plan_ref"], second["manifest"]["plan_ref"])
        self.assertNotEqual(first["materialization"]["manifest_ref"], second["materialization"]["manifest_ref"])

    def test_result_trees_are_detached(self):
        first = self._build(run_id="qscale-isolation")
        first["case_set"]["cases"][0]["case_id"] = "mutated"
        first["plan_segments"][0]["entries"][0]["case_id"] = "mutated"
        second = self._build(run_id="qscale-isolation")
        self.assertNotEqual(second["case_set"]["cases"][0]["case_id"], "mutated")
        self.assertNotEqual(second["plan_segments"][0]["entries"][0]["case_id"], "mutated")


if __name__ == "__main__":
    unittest.main()
