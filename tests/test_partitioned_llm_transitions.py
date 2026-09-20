from __future__ import annotations

import copy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gah.contracts import ContractError
from gah.llm_materialization import target_document
from gah.partitioned_llm_materialization import build
from gah.partitioned_llm_transitions import rebind
from gah.partitioned_trial_plan import restore_trial_plan
from gah.policy import initial_policy_profile
from gah.run_contracts import content_ref


IMAGE_ID = "sha256:" + "a" * 64
WORKER_DIGEST = "b" * 64
ISOLATION = {"schema_version": 1, "kind": "test_isolation", "network": "none"}


class PartitionedLlmTransitionRebindTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        policy = initial_policy_profile()
        first = build(policy, case_count=400, policy_generation=1, run_id="qscale-transition-before",
                      now=1000, target_version="baseline-v1", image_id=IMAGE_ID,
                      worker_digest=WORKER_DIGEST, isolation_profile=ISOLATION)
        old_target = target_document("baseline-v1", IMAGE_ID)
        baseline_ref = content_ref("baseline", "qscale-transition-baseline", {"generation": 1})
        cls.prepared = build(policy, case_count=400, policy_generation=1,
            run_id="qscale-transition-candidate", now=2000, target_version="degraded-v2",
            image_id=IMAGE_ID, worker_digest=WORKER_DIGEST, isolation_profile=ISOLATION,
            generation=2, baseline_context={"baseline_ref": baseline_ref,
                "targets": [{"control_id": "LC-query-scale",
                    "target_ref": content_ref("target", old_target["target_id"], old_target)}],
                "contract": first["contract"]})
        from gah.partitioned_run_contracts import materialize_partitioned_run
        value = cls.prepared
        baseline = {key: value['baseline_context'][key] for key in ('baseline_ref', 'targets')}
        value['bound_run'] = materialize_partitioned_run(
            value['manifest'], value['contract'], value['plan_index'], value['plan_segments'],
            value['policy'], value['registry'], value['case_set'], baseline_context=baseline)

    def test_rebind_preserves_all_cases_and_builds_one_baseline_pair(self):
        value = rebind(self.prepared, run_id="qscale-transition-replay", now=3000)
        plan = restore_trial_plan(value["plan_index"], value["plan_segments"])
        self.assertEqual(len(plan["entries"]), 800)
        self.assertEqual(sum(entry["variant"] == "baseline" for entry in plan["entries"]), 400)
        self.assertEqual(sum(entry["variant"] == "candidate" for entry in plan["entries"]), 400)
        self.assertEqual(value["manifest"]["purpose"], "regression")
        self.assertEqual(value["manifest"]["run_id"], "qscale-transition-replay")
        self.assertEqual(value["baseline_context"], {
            "baseline_ref": self.prepared["baseline_context"]["baseline_ref"],
            "targets": self.prepared["baseline_context"]["targets"],
        })
        self.assertFalse(value["ci_eligible"])
        self.assertFalse(value["bound_run"]["ci_eligible"])
        self.assertNotEqual(value["manifest"]["plan_ref"], self.prepared["manifest"]["plan_ref"])

    def test_returned_mutation_does_not_change_prepared_or_next_rebind(self):
        first = rebind(self.prepared, run_id="qscale-transition-copy-a", now=3000)
        expected = rebind(self.prepared, run_id="qscale-transition-copy-b", now=3000)
        expected_plan = restore_trial_plan(expected["plan_index"], expected["plan_segments"])
        first["plan_segments"][0]["entries"].clear()
        first["baseline_context"]["targets"].clear()
        self.assertEqual(self.prepared["manifest"]["run_id"], "qscale-transition-candidate")
        another = rebind(self.prepared, run_id="qscale-transition-copy-b", now=3000)
        self.assertEqual(restore_trial_plan(another["plan_index"], another["plan_segments"]), expected_plan)

    def test_rebind_rejects_wrong_purpose_and_run_reuse(self):
        with self.assertRaises(ContractError):
            rebind(self.prepared, run_id="qscale-transition-bad", now=3000, purpose="contract_candidate")
        with self.assertRaises(ContractError):
            rebind(self.prepared, run_id=self.prepared["manifest"]["run_id"], now=3000)


if __name__ == "__main__":
    unittest.main()
