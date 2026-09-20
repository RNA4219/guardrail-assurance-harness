from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gah.contracts import ContractError
from gah.productization import content_ref
from gah import pilot


DIGEST = "a" * 64


class PilotAssessmentTests(unittest.TestCase):
    def history_pair(self, index, repository="repo-a", **overrides):
        row = {
            "pair_id": f"pair-{index}",
            "repository_id": repository,
            "status": "COMPLETE",
            "critical_miss": False,
            "mandatory_miss": False,
            "false_action_alert": False,
            "false_critical_alert": False,
            "calibration_mismatch": False,
            "real_regression_revalidated": False,
            "content_digest": f"{index + 1:064x}",
        }
        row.update(overrides)
        return row

    def clean_change(self, index, repository="repo-a", **overrides):
        row = {
            "change_id": f"clean-{index}",
            "repository_id": repository,
            "status": "COMPLETE",
            "false_action_alert": False,
            "false_critical_alert": False,
            "content_digest": f"{index + 1001:064x}",
        }
        row.update(overrides)
        return row

    def llm_case(self, index, label, category):
        return {
            "case_id": f"case-{index}",
            "category": category,
            "expected_label": label,
            "prediction": "detect" if label == "positive" else "allow",
            "status": "COMPLETE",
            "content_digest": f"{index + 1:064x}",
            "oracle_independent": True,
            "measurement_reproduced": True,
            "all_measurement_obligations_met": True,
        }

    def maintenance_observation(self, index, legacy_active=100, gah_active=70,
                                use_case=None):
        if use_case is None:
            use_case = "UC-CI" if index < 10 else "UC-LLM"
        return {
            "observation_id": f"obs-{index}",
            "use_case": use_case,
            "status": "COMPLETE",
            "legacy_active_work_ns": legacy_active,
            "gah_active_work_ns": gah_active,
            "legacy_wall_wait_ns": 10,
            "gah_wall_wait_ns": 10,
            "legacy_model_tool_calls": 2,
            "gah_model_tool_calls": 2,
            "legacy_token": 20,
            "gah_token": 20,
            "legacy_cpu_time_ns": 30,
            "gah_cpu_time_ns": 30,
            "legacy_cost_micro_usd": 4,
            "gah_cost_micro_usd": 4,
            "legacy_peak_rss_bytes": 100,
            "gah_peak_rss_bytes": 100,
            "legacy_storage_bytes": 100,
            "gah_storage_bytes": 100,
            "legacy_human_intervention": 1,
            "gah_human_intervention": 1,
            "legacy_false_alert_handling": 1,
            "gah_false_alert_handling": 1,
            "correctness_equal": True,
            "content_digest": f"{index + 2001:064x}",
        }

    def test_history_pass_is_derived_from_rows(self):
        pairs = [
            self.history_pair(i, "repo-a" if i < 10 else "repo-b",
                              real_regression_revalidated=(i == 0))
            for i in range(20)
        ]
        clean = [
            self.clean_change(i, "repo-a" if i < 50 else "repo-b")
            for i in range(100)
        ]
        result = pilot.assess_history({
            "history_pairs": pairs,
            "clean_changes": clean,
        })
        self.assertEqual(result["pac_status"], "PASS")
        self.assertEqual(result["counts"]["per_repo_pairs"], [10, 10])
        self.assertEqual(result["counts"]["covered_known_critical_misses"], 0)

    def test_history_critical_miss_is_computed(self):
        pairs = [
            self.history_pair(i, "repo-a" if i < 10 else "repo-b",
                              real_regression_revalidated=(i == 0),
                              critical_miss=(i == 3))
            for i in range(20)
        ]
        clean = [self.clean_change(i) for i in range(100)]
        result = pilot.assess_history({
            "history_pairs": pairs, "clean_changes": clean,
        })
        self.assertEqual(result["pac_status"], "FAIL")
        self.assertEqual(result["counts"]["covered_known_critical_misses"], 1)

    def test_history_summary_cannot_pass(self):
        result = pilot.assess_history({
            "history_pairs": 20, "clean_changes": 100,
            "per_repo_pairs": [10, 10],
            "covered_known_mandatory_misses": 0,
            "covered_known_critical_misses": 0,
            "real_regression_revalidated": 1,
            "calibration_mismatches": 0,
            "false_action_alerts": 0,
            "false_critical_alerts": 0,
        })
        self.assertEqual(result["pac_status"], "INCONCLUSIVE")
        self.assertIn("raw_observations", result["missing"])

    def test_llm_pass_is_derived_from_cases(self):
        rows = []
        index = 0
        for category in ("cat-a", "cat-b"):
            for label in ("positive", "negative"):
                for _ in range(100):
                    rows.append(self.llm_case(index, label, category))
                    index += 1
        result = pilot.assess_llm({
            "observations": rows,
            "required_categories": ["cat-a", "cat-b"],
        })
        self.assertEqual(result["pac_status"], "PASS")
        self.assertEqual(result["counts"]["positive_denominator"], 200)
        self.assertEqual(result["counts"]["negative_denominator"], 200)

    def test_llm_summary_alias_and_flags_do_not_establish_pass(self):
        with self.assertRaises(ContractError):
            pilot.assess_llm({
                "positive_known": 200,
                "negative_known": 200,
                "oracle_independent": True,
                "measurement_reproduced": True,
                "all_measurement_obligations_met": True,
                "required_categories_have_each_100": True,
            })
        result = pilot.assess_llm({
            "positive": 200,
            "negative": 200,
            "label_unknown": 0,
            "oracle_independent": True,
            "measurement_reproduced": True,
            "all_measurement_obligations_met": True,
            "required_categories_have_each_100": True,
            "required_categories": ["cat-a"],
        })
        self.assertEqual(result["pac_status"], "INCONCLUSIVE")

    def test_llm_unknown_and_duplicate_do_not_enter_denominator_as_pass(self):
        row = self.llm_case(0, "positive", "cat-a")
        unknown = dict(row)
        unknown["case_id"] = "case-unknown"
        unknown["expected_label"] = "indeterminate"
        duplicate = dict(row)
        result = pilot.assess_llm({
            "observations": [row, duplicate, unknown],
            "required_categories": ["cat-a"],
        })
        self.assertEqual(result["pac_status"], "INCONCLUSIVE")
        self.assertEqual(result["counts"]["positive_denominator"], 1)
        self.assertEqual(result["counts"]["unknown_count"], 1)
        self.assertEqual(result["counts"]["duplicate_count"], 1)

    def test_maintenance_pass_uses_twenty_paired_rows(self):
        result = pilot.assess_maintenance({
            "observations": [self.maintenance_observation(i) for i in range(20)],
        })
        self.assertEqual(result["pac_status"], "PASS")
        self.assertEqual(result["counts"]["reduction_numerator"], 30)
        self.assertEqual(result["counts"]["reduction_denominator"], 100)

    def test_failed_maintenance_result_with_negative_reduction_is_serializable(self):
        rows = [
            self.maintenance_observation(i, 70, 100)
            for i in range(20)
        ]
        analysis = pilot.assess_maintenance({"observations": rows})
        ref = {"kind": "pilot_plan", "id": "plan-1", "digest": DIGEST}
        artifact = pilot.build_pilot_result(
            analysis,
            pilot_id="pilot-1",
            plan_revision_ref=ref,
            result_id="result-fail",
            source_refs=[],
            created_at=1,
        )
        self.assertEqual(artifact["pac_status"], "FAIL")
        self.assertLess(artifact["counts"]["reduction_numerator"], 0)

    def test_maintenance_summary_cannot_pass(self):
        result = pilot.assess_maintenance({
            "paired_observations": 20, "uc_ci_pairs": 10, "uc_llm_pairs": 10,
            "baseline_median_ns": 100, "candidate_median_ns": 70,
            "resource_dimensions_nonincreasing": True,
            "human_interventions_nonincreasing": True,
            "oracle_equivalent": True,
        })
        self.assertEqual(result["pac_status"], "INCONCLUSIVE")
        self.assertIn("raw_observations", result["missing"])

    def test_fractional_median_changes_boundary(self):
        baseline = list(range(1, 21))
        candidate = [1, 2, 3, 4, 5, 6, 7, 7, 7, 7, 8, 8, 9, 10, 11, 12, 13, 14, 15, 16]
        rows = [
            self.maintenance_observation(i, baseline[i], candidate[i])
            for i in range(20)
        ]
        result = pilot.assess_maintenance({"observations": rows})
        self.assertEqual(result["pac_status"], "FAIL")
        self.assertEqual(result["counts"]["baseline_median_ns"],
                         {"numerator": 21, "denominator": 2})
        self.assertEqual(result["counts"]["candidate_median_ns"],
                         {"numerator": 15, "denominator": 2})
        self.assertEqual(result["counts"]["reduction_numerator"], 200)
        self.assertEqual(result["counts"]["reduction_denominator"], 7)


class PilotContractTests(unittest.TestCase):
    def test_ref_requires_body_identity(self):
        body = {"schema_version": 1, "kind": "other_kind", "id": "body-id"}
        ref = content_ref("project_binding", "claimed-id", body)
        self.assertFalse(pilot.ref_matches(ref, body))

    def test_result_maps_are_closed(self):
        ref = {"kind": "pilot_plan", "id": "plan-1", "digest": DIGEST}
        value = {
            "schema_version": 1,
            "kind": "pilot_result",
            "id": "result-1",
            "pilot_id": "pilot-1",
            "plan_revision_ref": ref,
            "pac_status": "INCONCLUSIVE",
            "source_refs": [],
            "baseline_ref": None,
            "scope": {},
            "counts": {},
            "missing": [],
            "unknown": [],
            "conflicts": [],
            "cost": {},
            "human_intervention": {},
            "evidence_refs": [],
            "limitations": [],
            "created_at": 1,
        }
        self.assertEqual(pilot.validate_pilot_result(value)["kind"], "pilot_result")
        value["counts"] = {"untrusted": 1}
        with self.assertRaises(ContractError):
            pilot.validate_pilot_result(value)


if __name__ == "__main__":
    unittest.main()
