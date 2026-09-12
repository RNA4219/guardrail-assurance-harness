"""CI consumerは輸送成功だけで終了0を返さない。"""
from copy import deepcopy
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.gah_ci import run


def ref(kind, identifier):
    return {"kind": kind, "id": identifier, "digest": "a" * 64}


class CIClientTests(unittest.TestCase):
    def setUp(self):
        self.request = {"schema_version": 1, "action": "ci_check", "request_id": "ci-request", "run_id": "normal",
            "expected_manifest_ref": ref("run_manifest", "normal"),
            "expected_contract_ref": ref("evaluation_contract", "contract"),
            "expected_baseline_ref": ref("baseline", "baseline"),
            "expected_target_refs": [ref("target", "target")], "expected_use_cases": ["UC-CI"]}
        self.result = {"schema_version": 1, "kind": "ci_gate_result", "action": "ci_check",
            "request_id": "ci-request", "run_id": "normal", "checked_at": 100,
            "expected_manifest_ref": self.request["expected_manifest_ref"], "outputs_ref": ref("run_outputs", "normal"),
            "assurance": "HEALTHY", "reasons": [], "use": True, "ci_eligible": True,
            "execution_status": "COMPLETED", "exit_code": 0}

    def test_success_and_warning_query_operator_then_return_zero(self):
        for state in ("HEALTHY", "WARNING"):
            runtime = Mock()
            runtime.client.return_value = {**self.result, "assurance": state}
            self.assertEqual(run(runtime, self.request, io.StringIO()), 0)
            runtime.client.assert_called_once_with(12004, self.request)

    def test_negative_failed_cancelled_propagate_distinct_codes(self):
        for code, state in ((1, "COMPLETED"), (2, "FAILED"), (3, "CANCELLED")):
            runtime = Mock()
            runtime.client.return_value = {**self.result, "exit_code": code, "execution_status": state,
                "use": False, "ci_eligible": False, "reasons": ["NOT_ALLOWED"]}
            self.assertEqual(run(runtime, self.request, io.StringIO()), code)

    def test_inconsistent_or_wrong_binding_responses_fail(self):
        changes = ({"exit_code": False}, {"ci_eligible": False}, {"outputs_ref": None},
            {"outputs_ref": ref("run_outputs", "other")}, {"assurance": "HOLD"}, {"reasons": ["EXPIRED"]},
            {"request_id": "another-request"}, {"execution_status": "CANCELLED"}, {"unexpected": True})
        for change in changes:
            runtime = Mock()
            runtime.client.return_value = {**deepcopy(self.result), **change}
            with self.subTest(change=change):
                self.assertEqual(run(runtime, self.request, io.StringIO()), 2)

    def test_missing_broker_or_output_failure_never_returns_zero(self):
        runtime = Mock()
        runtime.client.side_effect = OSError("unavailable")
        self.assertEqual(run(runtime, self.request, io.StringIO()), 2)
        runtime.client.side_effect = None
        runtime.client.return_value = self.result
        broken = Mock()
        broken.flush.side_effect = OSError("output failed")
        self.assertEqual(run(runtime, self.request, broken), 2)

    def test_other_action_is_rejected_before_transport(self):
        runtime = Mock()
        self.assertEqual(run(runtime, {"schema_version": 1, "action": "run_outputs", "request_id": "x", "run_id": "normal"}, io.StringIO()), 2)
        runtime.client.assert_not_called()
