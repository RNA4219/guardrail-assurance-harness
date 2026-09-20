"""Pure tests for immutable usage warning basis creation and validation."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gah.budget_warning import AXES, build_basis, validate_basis, warning_dimensions
from gah.contracts import ContractError
from gah.run_contracts import content_ref
from tests.test_run_evidence import bound_fixture


class BudgetWarningTests(unittest.TestCase):
    def setUp(self):
        self.fixtures, self.plan, self.bound = bound_fixture()
        self.manifest = self.bound["manifest"]
        self.policy = self.bound["policy"]
        self.profile = self.manifest["profile"]
        self.limits = {axis: self.policy["profiles"][self.profile][axis] for axis in AXES}
        self.started = self.manifest["created_at"]

    def make_snapshot(self, usage=None, **edits):
        usage = {axis: 0 for axis in AXES} if usage is None else dict(usage)
        closed_at = self.started + usage["elapsed_seconds"]
        resources = {
            "case_trial_executions": usage["case_trial_executions"],
            "model_calls": usage["model_calls"],
            "input_tokens": usage["total_tokens"],
            "output_tokens": 0,
            "api_cost_usd_micros": usage["api_cost_usd_micros"],
            "slots": 0,
            "unsettled": 0,
            "total_tokens": usage["total_tokens"],
            "global_api_cost_usd_micros": usage["api_cost_usd_micros"],
        }
        snapshot = {
            "run_id": self.manifest["run_id"],
            "manifest_digest": content_ref(
                "run_manifest", self.manifest["run_id"], self.manifest)["digest"],
            "owner_id": "warning-owner",
            "owner_epoch": 1,
            "deadline": self.manifest["deadline"],
            "cancelled": False,
            "breached": False,
            "closed": True,
            "closed_at": closed_at,
            "resources": resources,
            "budget_closure": True,
            "ci_eligible": False,
        }
        snapshot.update(edits)
        return snapshot

    def test_exact_integer_thresholds_cover_each_policy_axis(self):
        threshold_n, threshold_d = self.policy["warning_usage_min"]
        self.assertEqual((threshold_n, threshold_d), (4, 5))
        below = {axis: limit * 79 // 100 for axis, limit in self.limits.items()}
        below_basis = build_basis(self.bound, self.make_snapshot(below), self.started)
        self.assertEqual(warning_dimensions(self.bound, below_basis, below_basis["closed_at"]), [])

        for axis in AXES:
            with self.subTest(axis=axis):
                at_threshold = dict(below)
                at_threshold[axis] = self.limits[axis] * threshold_n // threshold_d
                basis = build_basis(self.bound, self.make_snapshot(at_threshold), self.started)
                self.assertEqual(warning_dimensions(self.bound, basis, basis["closed_at"]), [axis])

        full_usage = dict(self.limits)
        full_basis = build_basis(self.bound, self.make_snapshot(full_usage), self.started)
        self.assertEqual(warning_dimensions(self.bound, full_basis, full_basis["closed_at"]), list(AXES))

    def test_partitioned_manifest_keeps_warning_thresholds_and_ref_binding(self):
        from gah.partitioned_trial_plan import partition_trial_plan
        from gah.partitioned_run_contracts import materialize_partitioned_run
        index, segments = partition_trial_plan(self.bound['plan'])
        self.manifest = {**self.manifest, 'schema_version': 2,
            'plan_ref': content_ref('trial_plan_index', index['plan_id'], index)}
        bound = materialize_partitioned_run(self.manifest, self.bound['contract'], index, segments,
            self.policy, self.bound['registry'], self.bound['case_set'])
        usage = {axis: 0 for axis in AXES}
        usage['case_trial_executions'] = self.limits['case_trial_executions'] * 4 // 5
        basis = build_basis(bound, self.make_snapshot(usage), self.started)
        self.assertEqual(warning_dimensions(bound, basis, basis['closed_at']), ['case_trial_executions'])
        altered = deepcopy(bound)
        altered['_partitioned_receipt']['manifest_ref']['digest'] = '0' * 64
        with self.assertRaises(ContractError):
            validate_basis(altered, basis, basis['closed_at'])

    def test_input_and_return_values_are_not_aliased(self):
        snapshot = self.make_snapshot()
        bound_before = deepcopy(self.bound)
        snapshot_before = deepcopy(snapshot)
        basis = build_basis(self.bound, snapshot, self.started)
        self.assertEqual(self.bound, bound_before)
        self.assertEqual(snapshot, snapshot_before)
        basis["usage"]["model_calls"] = 1
        self.assertEqual(snapshot["resources"]["model_calls"], 0)

        valid = build_basis(self.bound, snapshot, self.started)
        checked = validate_basis(self.bound, valid, valid["closed_at"])
        checked["limits"]["model_calls"] = 1
        self.assertEqual(valid["limits"]["model_calls"], self.limits["model_calls"])

    def test_rejects_snapshot_state_binding_and_measurement_failures(self):
        bad_snapshots = (
            {"closed": False},
            {"budget_closure": False},
            {"cancelled": True},
            {"breached": True},
            {"manifest_digest": "f" * 64},
            {"run_id": "other-run"},
            {"resources": {"slots": 1}},
            {"resources": {"unsettled": 1}},
            {"resources": {"total_tokens": True}},
            {"resources": {"total_tokens": 1}},
            {"closed_at": self.manifest["deadline"] + 1},
        )
        for edit in bad_snapshots:
            with self.subTest(edit=edit):
                snapshot = self.make_snapshot()
                if "resources" in edit:
                    snapshot["resources"].update(edit["resources"])
                else:
                    snapshot.update(edit)
                with self.assertRaises(ContractError) as caught:
                    build_basis(self.bound, snapshot, self.started)
                self.assertEqual(caught.exception.code, "BUDGET_WARNING_INVALID")

        excessive = dict(self.limits)
        excessive["model_calls"] += 1
        with self.assertRaises(ContractError) as caught:
            build_basis(self.bound, self.make_snapshot(excessive), self.started)
        self.assertEqual(caught.exception.code, "BUDGET_WARNING_INVALID")

    def test_rejects_malformed_basis_and_future_assessment(self):
        basis = build_basis(self.bound, self.make_snapshot(), self.started)
        invalid_values = []
        extra = deepcopy(basis); extra["extra"] = True; invalid_values.append(extra)
        wrong_ref = deepcopy(basis); wrong_ref["policy_ref"]["digest"] = "f" * 64; invalid_values.append(wrong_ref)
        wrong_limit = deepcopy(basis); wrong_limit["limits"]["model_calls"] += 1; invalid_values.append(wrong_limit)
        bool_count = deepcopy(basis); bool_count["usage"]["model_calls"] = True; invalid_values.append(bool_count)
        over_limit = deepcopy(basis); over_limit["usage"]["model_calls"] = self.limits["model_calls"] + 1; invalid_values.append(over_limit)
        for value in invalid_values:
            with self.subTest(value=value):
                with self.assertRaises(ContractError) as caught:
                    validate_basis(self.bound, value, basis["closed_at"])
                self.assertEqual(caught.exception.code, "BUDGET_WARNING_INVALID")
        with self.assertRaises(ContractError) as caught:
            validate_basis(self.bound, basis, basis["closed_at"] - 1)
        self.assertEqual(caught.exception.code, "BUDGET_WARNING_INVALID")


    def test_warning_does_not_override_stronger_assurance(self):
        from gah.run_evidence import RunEvidenceStore
        usage = {axis: 0 for axis in AXES}
        usage["model_calls"] = self.limits["model_calls"] * 4 // 5
        basis = build_basis(self.bound, self.make_snapshot(usage), self.started)
        aggregate = {"aggregate_digest": "a" * 64, "observed_at": self.started,
            "metrics": [{"metric_id": "mutation", "name": "mutation_score", "critical": False,
                         "numerator": 1, "denominator": 1, "baseline": None}],
            "required_missing": False, "critical_missing": False, "integrity_failure": False,
            "forbidden_violation": False, "critical_violation": False,
            "counts": {"variant": {"candidate": {"constraint_fail": 0}}}}
        for field, expected in ((None, "WARNING"), ("critical_violation", "HOLD"),
                                ("required_missing", "UNKNOWN"), ("constraint_fail", "DEGRADED")):
            with self.subTest(state=expected):
                value = deepcopy(aggregate)
                if field == "constraint_fail":
                    value["counts"]["variant"]["candidate"][field] = 1
                elif field is not None:
                    value[field] = True
                result = RunEvidenceStore._decision_from_aggregate(
                    self.bound, value, self.started, budget_warning_basis=basis)
                self.assertEqual(result["assurance"], expected)
                self.assertEqual(result["budget_warning"], basis)
                self.assertIn({"code": "warning", "state": "WARNING", "metric_id": None}, result["reasons"])
        legacy = RunEvidenceStore._decision_from_aggregate(self.bound, aggregate, self.started)
        self.assertEqual(legacy["assurance"], "HEALTHY")
        self.assertNotIn("budget_warning", legacy)

    def test_saved_basis_survives_reopen_and_conflicting_finalize_is_rejected(self):
        from tests.test_run_evidence import RunEvidenceTests
        from gah.run_evidence import EvidenceError
        fixture = RunEvidenceTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        usage = {axis: 0 for axis in AXES}
        usage["model_calls"] = self.limits["model_calls"] * 4 // 5
        basis = build_basis(self.bound, self.make_snapshot(usage), self.started)
        with fixture.open() as store:
            fixture.start(store)
            fixture.records(store)
            saved = store.finalize("run-1", budget_warning_basis=basis)
            self.assertEqual(saved["decision"]["budget_warning"], basis)
        with fixture.open() as store:
            self.assertEqual(store.finalize("run-1", budget_warning_basis=basis), saved)
            conflicting = deepcopy(basis)
            conflicting["usage"]["model_calls"] += 1
            with self.assertRaisesRegex(EvidenceError, "BUDGET_WARNING_MISMATCH"):
                store.finalize("run-1", budget_warning_basis=conflicting)
            self.assertEqual(store.get_terminal("run-1"), saved)


if __name__ == "__main__":
    unittest.main()
