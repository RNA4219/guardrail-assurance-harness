"""PAC11 report states using real AdoptionStore dispatch and saved evidence."""
from copy import deepcopy
import io
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gah import adoption_migrations as migrations
from gah.adoption import AdoptionError, AdoptionStore
from gah.evaluation_authority import EvaluationExtension
from gah.policy import initial_policy_profile
from tests import test_regression_integration as seed
from tests import test_run_cancellation as cancellation
from tests.test_supervised_run import Runtime
from tools.gah_report import run as report_run


class ReportAuthorityStatesTests(unittest.TestCase):
    """Use the existing small regression fixture; never fabricate gate replies."""

    setUp = seed.RegressionIntegrationTests.setUp
    open = seed.RegressionIntegrationTests.open
    prepare = seed.RegressionIntegrationTests.prepare
    begin = seed.RegressionIntegrationTests.begin
    complete = seed.RegressionIntegrationTests.complete
    gate_request = seed.RegressionIntegrationTests.gate_request
    cancel = cancellation.CancellationIntegrationTests.cancel
    operation = cancellation.CancellationIntegrationTests.operation
    observe = cancellation.CancellationIntegrationTests.observe
    finalize_cancel = cancellation.CancellationIntegrationTests.finalize

    @classmethod
    def setUpClass(cls):
        seed.RegressionIntegrationTests.setUpClass.__func__(cls)

    def _report_pair(self, prepared, *, expected_exit, expected_reason=None,
                     expected_assurance=None, expected_versions=None):
        runtime = Runtime(self.store)
        request = self.gate_request(prepared)
        json_stream, markdown_stream = io.StringIO(), io.StringIO()
        json_exit = report_run(runtime, request, json_stream, output_format="json")
        markdown_exit = report_run(runtime, request, markdown_stream, output_format="markdown")
        self.assertEqual(json_exit, expected_exit)
        self.assertEqual(markdown_exit, expected_exit)
        report = json.loads(json_stream.getvalue())
        markdown = markdown_stream.getvalue()
        self.assertIn("# GAH実行結果", markdown)
        self.assertIn("実行状態: " + report["execution_status"], markdown)
        if report.get("assurance") is not None:
            self.assertIn("CI照会のAssurance: " + report["assurance"], markdown)
        if report.get("observed_assurance") is not None:
            self.assertIn("保存時のAssurance: " + report["observed_assurance"], markdown)
        for reason in set(report.get("ci_reasons", [])) | set(report.get("reasons", [])):
            self.assertIn(json.dumps(reason, ensure_ascii=True), markdown)
        if expected_reason is not None:
            self.assertIn(expected_reason, report.get("ci_reasons", []) + report.get("reasons", []))
            self.assertIn(json.dumps(expected_reason), markdown)
        if expected_assurance is not None:
            self.assertEqual(report["observed_assurance"], expected_assurance)
        self.assertIs(report["ci_eligible"], expected_exit == 0)
        if expected_versions is not None:
            self.assertEqual(report["evaluation_versions"], expected_versions)
            self.assertIn("契約世代: " + str(expected_versions["contract_generation"]), markdown)
            self.assertIn("baseline世代: " + str(expected_versions["baseline_generation"]), markdown)
        self.assertIn("次の操作:", markdown)
        self.assertIn("必要な役割:", markdown)
        return report

    def test_healthy_report_uses_fresh_gate_and_saved_receipt(self):
        prepared = self.prepare()
        receipt = self.complete(prepared)
        self.assertEqual(receipt["assurance"], "HEALTHY")
        manifest = prepared["bound_run"]["manifest"]
        report = self._report_pair(prepared, expected_exit=0,
            expected_assurance="HEALTHY", expected_versions={
                "contract_ref": manifest["contract_ref"],
                "contract_generation": prepared["bound_run"]["contract"]["generation"],
                "baseline_ref": manifest["baseline_ref"], "baseline_generation": 1})
        self.assertEqual(report["kind"], "run_report")
        self.assertTrue(report["ci_eligible"])

    def test_known_violation_is_reported_from_real_attempts(self):
        def negative(record, observation):
            if record["scenario"].startswith("mutation:F01:") and record["variant"] == "candidate":
                return {**observation, "detected": False}
            return observation

        prepared = self.prepare()
        receipt = self.complete(prepared, mutate=negative)
        self.assertIn(receipt["assurance"], {"DEGRADED", "HOLD"})
        report = self._report_pair(prepared, expected_exit=1,
            expected_reason="ASSURANCE_NOT_ALLOWED", expected_assurance=receipt["assurance"])
        self.assertFalse(report["ci_eligible"])
        self.assertTrue(report["decision_reasons"])

    def test_missing_evidence_is_not_filled_with_zero_or_called_success(self):
        prepared = self.prepare()
        self.begin(prepared)
        report = self._report_pair(prepared, expected_exit=2)
        self.assertEqual(report["kind"], "run_report_failure")
        self.assertFalse(report["ci_eligible"])
        self.assertEqual(report["checked_at"], self.clock.value)
        self.assertIn("NOT_FINALIZED", report["ci_reasons"])

    def test_cancelled_run_keeps_cancelled_terminal_semantics(self):
        prepared = self.prepare()
        owner = self.begin(prepared)
        self.cancel(owner)
        receipt = self.finalize_cancel()
        self.assertEqual(receipt["kind"], "authority_cancel_receipt")
        report = self._report_pair(prepared, expected_exit=3,
            expected_reason="CANCEL_REQUESTED")
        self.assertEqual(report["execution_status"], "CANCELLED")

    def test_unconfirmed_stop_remains_a_failure_in_both_formats(self):
        prepared = self.prepare()
        owner = self.begin(prepared)
        self.operation(prepared, owner, stop=False)
        self.cancel(owner)
        report = self._report_pair(prepared, expected_exit=2,
            expected_reason="STOP_UNCONFIRMED")
        self.assertEqual(report["kind"], "run_report_failure")
        self.assertFalse(report["ci_eligible"])

    def test_stopped_but_unsettled_operation_reports_open_budget(self):
        prepared = self.prepare()
        owner = self.begin(prepared)
        self.operation(prepared, owner, stop=True, usage=None)
        self.cancel(owner)
        receipt = self.finalize_cancel()
        self.assertFalse(receipt["budget_closure"])
        report = self._report_pair(prepared, expected_exit=3,
            expected_reason="BUDGET_OPEN")
        self.assertEqual(report["kind"], "run_report")
        self.assertIsNone(report["measurements"]["mutation_reviews"])
        self.assertIsNone(report["measurements"]["reviewed_classification"])
        self.assertFalse(report["ci_eligible"])

    def test_revoked_run_is_not_resurrected_by_saved_report(self):
        prepared = self.prepare()
        self.complete(prepared)
        self.store.dispatch(12004, 12004, seed.request("evidence_revoke", "revoke-report-source",
            run_id=prepared["bound_run"]["manifest"]["run_id"]))
        report = self._report_pair(prepared, expected_exit=1,
            expected_reason="EVIDENCE_REVOKED")
        self.assertEqual(report["kind"], "run_report")
        self.assertFalse(report["ci_eligible"])


if __name__ == "__main__":
    unittest.main()
