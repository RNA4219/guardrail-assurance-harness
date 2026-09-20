"""固定CI小集合のbind_run_manifest cacheを検査する。"""
from copy import deepcopy
import unittest
from unittest.mock import patch

from gah import cache_inputs, run_contracts


class SmallManifestCacheTests(unittest.TestCase):
    def setUp(self):
        cache_inputs._bound_manifest.cache_clear()
        cache_inputs.binding_cache.clear()
        self.calls = 0

    @staticmethod
    def args(count=15, use_case="UC-CI"):
        return [
            {"schema_version": 1, "kind": "run_manifest", "run_id": "small-run",
             "use_cases": [use_case], "profile": "full"},
            {"schema_version": 1, "kind": "evaluation_contract",
             "contract_id": "small-contract", "generation": 1,
             "use_cases": [use_case]},
            {"schema_version": 1, "kind": "trial_plan",
             "plan_id": "small-plan", "entries": []},
            {"schema_version": 1, "kind": "policy_profile",
             "policy_id": "small-policy", "generation": 1},
            {"schema_version": 1, "kind": "control_registry",
             "registry_id": "small-registry", "controls": []},
            {"schema_version": 1, "kind": "case_set",
             "case_set_id": "small-cases-" + str(count), "purpose": "acceptance",
             "cases": [{"case_id": "case-" + str(i), "value": i}
                       for i in range(count)]},
            {"targets": [{"target_ref": {"id": "target"}}], "worker": "worker"},
        ]

    def fake_bind(self, manifest, contract, plan, policy, registry, case_set,
                  *, baseline_context=None):
        self.calls += 1
        return {"manifest": manifest, "contract": contract, "plan": plan,
                "policy": policy, "registry": registry, "case_set": case_set,
                "baseline_context": baseline_context}

    @staticmethod
    def bind(args):
        return cache_inputs.bind_run_manifest(
            *args[:6], baseline_context=args[6])

    def test_small_ci_15_and_30_cold_warm_and_isolated(self):
        with patch.object(run_contracts, "bind_run_manifest",
                          side_effect=self.fake_bind):
            for count in (15, 30):
                args = self.args(count)
                cold = self.bind(args)
                expected = deepcopy(cold)
                cold["case_set"]["cases"][0]["case_id"] = "caller-change"
                self.assertEqual(self.bind(args), expected)
        self.assertEqual(self.calls, 2)

    def test_all_inputs_and_source_digest_invalidate(self):
        base = self.args()
        changes = (
            (0, lambda value: value[0].update(run_id="changed")),
            (1, lambda value: value[1].update(generation=2)),
            (2, lambda value: value[2].update(plan_id="changed")),
            (3, lambda value: value[3].update(generation=2)),
            (4, lambda value: value[4].update(registry_id="changed")),
            (5, lambda value: value[5]["cases"][0].update(value=99)),
            (6, lambda value: value[6].update(worker="changed")),
        )
        with patch("gah.evaluation_authority._source_digest",
                   return_value="a" * 64), \
             patch.object(run_contracts, "bind_run_manifest",
                          side_effect=self.fake_bind):
            self.bind(base)
            for index, change in changes:
                changed = deepcopy(base)
                change(changed)
                self.bind(changed)
            self.assertEqual(self.calls, 8)
            with patch("gah.evaluation_authority._source_digest",
                       return_value="b" * 64):
                self.bind(base)
        self.assertEqual(self.calls, 9)

    def test_non_ci_scope_size_and_nonplain_payload_fall_back(self):
        cases = (self.args(15, "UC-LLM"), self.args(14, "UC-CI"))
        oversized = self.args()
        oversized[0]["padding"] = "x" * (3 * 1024 * 1024)
        nonplain = self.args()
        nonplain[0]["opaque"] = object()
        with patch.object(run_contracts, "bind_run_manifest",
                          side_effect=self.fake_bind):
            for args in (*cases, oversized, nonplain):
                self.bind(args)
                self.bind(args)
        self.assertEqual(self.calls, 8)

    def test_original_implementation_error_is_preserved(self):
        def raising(*args, **kwargs):
            raise RuntimeError("implementation-sentinel")

        with patch("gah.evaluation_authority._source_digest",
                   return_value="a" * 64), \
             patch.object(run_contracts, "bind_run_manifest",
                          side_effect=raising):
            for _ in range(2):
                with self.assertRaisesRegex(RuntimeError,
                                            "^implementation-sentinel$"):
                    self.bind(self.args())


if __name__ == "__main__":
    unittest.main()
