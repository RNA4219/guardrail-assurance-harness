"""監督レビューで見つけた未観測の合格・参照の付替えを防ぐ回帰試験。"""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from gah.contracts import ContractError
from gah.productization import content_ref
from gah import pilot


class ProductizationReviewTests(unittest.TestCase):
    def assert_not_pass(self, assessor, value):
        try:
            result = assessor(value)
        except ContractError:
            return
        self.assertNotEqual(result["pac_status"], "PASS")

    def test_llm_summary_flags_cannot_replace_cases(self):
        self.assert_not_pass(pilot.assess_llm, {
            "positive_known": 200, "negative_known": 200,
            "oracle_independent": True, "measurement_reproduced": True,
            "all_measurement_obligations_met": True, "required_categories_have_each_100": True})

    def test_history_missing_critical_observation_is_not_zero(self):
        self.assert_not_pass(pilot.assess_history, {
            "history_pairs": 20, "clean_changes": 100, "per_repo_pairs": [10, 10],
            "real_regression_revalidated": 1, "calibration_mismatches": 0,
            "false_action_alerts": 0, "false_critical_alerts": 0,
            "covered_known_mandatory_misses": 0})

    def test_maintenance_summary_cannot_replace_twenty_pairs(self):
        self.assert_not_pass(pilot.assess_maintenance, {
            "paired_observations": 20, "uc_ci_pairs": 10, "uc_llm_pairs": 10,
            "baseline_median_ns": 100, "candidate_median_ns": 70,
            "resource_dimensions_nonincreasing": True,
            "human_interventions_nonincreasing": True, "oracle_equivalent": True})

    def test_catalog_ref_cannot_rename_body_identity(self):
        body = {"schema_version": 1, "kind": "other_kind", "id": "another-id"}
        renamed = content_ref("project_binding", "claimed-id", body)
        self.assertFalse(pilot.ref_matches(renamed, body))


if __name__ == "__main__":
    unittest.main()
