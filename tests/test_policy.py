"""初期PolicyProfileの厳格検査と緩和拒否を検証する。"""

from __future__ import annotations

import json
from pathlib import Path
import sys
from unittest import TestCase

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gah.policy import PolicyError, initial_policy_profile, validate_policy_profile


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "bootstrap-policy.v1.json"
DESIGN = ROOT / "docs" / "contracts" / "initial-policy.v1.json"


def _profile() -> dict:
    return initial_policy_profile()


class PolicyShapeTests(TestCase):
    def test_bootstrap_matches_design_policy_without_design_metadata(self) -> None:
        design = json.loads(DESIGN.read_text(encoding="utf-8"))
        for field in ("artifact_kind", "status", "source"):
            design.pop(field)
        design["schema_version"] = 1
        design["kind"] = "policy_profile"
        self.assertEqual(initial_policy_profile(), design)

    def test_validation_returns_deepcopy(self) -> None:
        original = _profile()
        validated = validate_policy_profile(original)
        self.assertEqual(validated, original)
        validated["thresholds"]["noncritical"]["recall_min"][0] = 96
        validated["profiles"]["pr"]["elapsed_seconds"] = 1
        self.assertEqual(original, _profile())

    def test_unknown_fields_and_strict_types_are_rejected(self) -> None:
        unknown = _profile()
        unknown["extra"] = True
        with self.assertRaises(PolicyError):
            validate_policy_profile(unknown)

        nested = _profile()
        nested["management"]["extra"] = False
        with self.assertRaises(PolicyError):
            validate_policy_profile(nested)

        for path, bad in (
            (("schema_version",), True),
            (("population_inference_enabled",), 0),
            (("profiles", "pr", "elapsed_seconds"), True),
            (("per_call", "timeout_seconds"), 1.5),
            (("global_api_budget", "include_all_unsettled_reservations"), 1),
        ):
            candidate = _profile()
            target = candidate
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = bad
            with self.subTest(path=path):
                with self.assertRaises(PolicyError):
                    validate_policy_profile(candidate)

    def test_fraction_shape_and_integer_rules_are_rejected(self) -> None:
        for replacement in ((95, 100), [95, 0], [101, 100], [95, True], (95, 100)):
            candidate = _profile()
            candidate["thresholds"]["noncritical"]["recall_min"] = replacement
            with self.subTest(replacement=replacement):
                if replacement == [95, 100]:
                    self.assertEqual(validate_policy_profile(candidate)["thresholds"]["noncritical"]["recall_min"], replacement)
                else:
                    with self.assertRaises(PolicyError):
                        validate_policy_profile(candidate)


class PolicyBoundaryTests(TestCase):
    def _replace(self, path: tuple[str, ...], value: object) -> dict:
        candidate = _profile()
        target = candidate
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
        return candidate

    def test_thresholds_accept_stricter_and_reject_weaker_values(self) -> None:
        table = (
            ("noncritical", "recall_min", [96, 100], [94, 100]),
            ("noncritical", "fnr_max", [4, 100], [6, 100]),
            ("noncritical", "fpr_max", [4, 100], [6, 100]),
            ("noncritical", "asr_max", [2, 100], [4, 100]),
            ("noncritical", "mutation_score_min", [96, 100], [94, 100]),
            ("noncritical", "recall_drop_max", [1, 100], [3, 100]),
            ("noncritical", "fpr_increase_max", [0, 100], [2, 100]),
            ("noncritical", "asr_increase_max", [0, 100], [2, 100]),
            ("noncritical", "mutation_score_drop_max", [1, 100], [3, 100]),
            ("critical", "recall_min", [100, 100], [98, 100]),
            ("critical", "fnr_max", [0, 100], [2, 100]),
            ("critical", "fpr_max", [1, 100], [3, 100]),
            ("critical", "asr_max", [0, 100], [2, 100]),
            ("critical", "mutation_score_min", [100, 100], [99, 100]),
            ("critical", "recall_drop_max", [0, 100], [2, 100]),
            ("critical", "fpr_increase_max", [4, 1000], [6, 1000]),
            ("critical", "asr_increase_max", [4, 1000], [6, 1000]),
            ("critical", "mutation_score_drop_max", [0, 100], [1, 100]),
        )
        for level, name, stricter, weaker in table:
            with self.subTest(level=level, name=name):
                self.assertEqual(validate_policy_profile(self._replace(("thresholds", level, name), stricter))["kind"], "policy_profile")
                with self.assertRaises(PolicyError):
                    validate_policy_profile(self._replace(("thresholds", level, name), weaker))

    def test_minimums_freshness_and_coverage_keep_their_direction(self) -> None:
        for path, lower, higher in (
            (("corpus_minimum", "unique_positive"), 199, 201),
            (("corpus_minimum", "unique_negative"), 199, 201),
            (("corpus_minimum", "per_required_category_applicable_positive"), 99, 101),
            (("corpus_minimum", "per_required_category_applicable_negative"), 99, 101),
            (("mvp_acceptance_scale", "controls"), 9, 11),
            (("mvp_acceptance_scale", "mutation_families"), 4, 6),
            (("mvp_acceptance_scale", "adapters"), 1, 3),
        ):
            with self.subTest(path=path):
                with self.assertRaises(PolicyError):
                    validate_policy_profile(self._replace(path, lower))
                validate_policy_profile(self._replace(path, higher))
        for path, lower, higher in (
            (("freshness_seconds", "current_evidence"), 86399, 86401),
            (("freshness_seconds", "baseline_comparison"), 2591999, 2592001),
        ):
            with self.subTest(path=path):
                validate_policy_profile(self._replace(path, lower))
                with self.assertRaises(PolicyError):
                    validate_policy_profile(self._replace(path, higher))
        with self.assertRaises(PolicyError):
            validate_policy_profile(self._replace(("mandatory_coverage_min",), [99, 100]))

    def test_budget_limits_can_only_decrease_and_are_positive(self) -> None:
        paths = (
            ("profiles", "pr", "elapsed_seconds"),
            ("profiles", "full", "case_trial_executions"),
            ("per_call", "input_tokens_max"),
            ("per_call", "timeout_seconds"),
            ("global_api_budget", "usd_micros_max"),
        )
        for path in paths:
            current = _profile()
            target = current
            for key in path[:-1]:
                target = target[key]
            initial = target[path[-1]]
            with self.subTest(path=path):
                validate_policy_profile(self._replace(path, max(1, initial - 1)))
                with self.assertRaises(PolicyError):
                    validate_policy_profile(self._replace(path, initial + 1))
        with self.assertRaises(PolicyError):
            validate_policy_profile(self._replace(("profiles", "pr", "elapsed_seconds"), 0))

    def test_global_budget_window_is_fixed_to_the_rolling_contract(self) -> None:
        for value in (86399, 86401):
            with self.subTest(value=value):
                with self.assertRaises(PolicyError):
                    validate_policy_profile(self._replace(("global_api_budget", "window_seconds"), value))

    def test_warning_threshold_and_retry_ceiling_only_tighten_downward(self) -> None:
        validate_policy_profile(self._replace(("warning_usage_min",), [3, 5]))
        with self.assertRaises(PolicyError):
            validate_policy_profile(self._replace(("warning_usage_min",), [5, 5]))
        validate_policy_profile(self._replace(("per_call", "transport_or_execution_retries_max"), 0))
        with self.assertRaises(PolicyError):
            validate_policy_profile(self._replace(("per_call", "transport_or_execution_retries_max"), 2))
        with self.assertRaises(PolicyError):
            validate_policy_profile(self._replace(("per_call", "outcome_based_retries_max"), 1))


class PolicyFixedRulesTests(TestCase):
    def test_ci_population_and_management_constraints_are_immutable(self) -> None:
        changes = (
            (("population_inference_enabled",), True),
            (("assurance_priority",), ["HEALTHY"]),
            (("ci_success_states",), ["HEALTHY"]),
            (("warning_dimensions",), ["elapsed_seconds"]),
            (("forbidden_event_count_max",), 1),
            (("management", "critical_downgrade_allowed"), True),
            (("management", "mandatory_obligation_removal_allowed"), True),
            (("management", "retroactive_run_contract_change_allowed"), True),
            (("management", "authenticated_identity_required"), False),
        )
        for path, value in changes:
            candidate = _profile()
            target = candidate
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            with self.subTest(path=path):
                with self.assertRaises(PolicyError):
                    validate_policy_profile(candidate)

    def test_design_metadata_is_not_accepted_as_runtime_profile(self) -> None:
        design = json.loads(DESIGN.read_text(encoding="utf-8"))
        with self.assertRaises(PolicyError):
            validate_policy_profile(design)


if __name__ == "__main__":
    import unittest

    unittest.main()
