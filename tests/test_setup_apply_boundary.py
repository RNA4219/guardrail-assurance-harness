"""導入CLIの実行前拒否、ready/cleanup前の出力禁止を検査する。"""
from contextlib import ExitStack
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from tools import setup_apply
from gah import operations
from gah.productization import operation_result

class SetupApplyBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.patch = patch.object(setup_apply, "ROOT", self.root); self.patch.start(); self.addCleanup(self.patch.stop)
        self.plan_path = self.root / "plan.json"
        result, self.plan = operations.run_setup_preview(self.root, "sample-ci", self.plan_path, clock=lambda: 1000)
        self.assertEqual(result["operation_status"], "COMPLETED", result)
        # _check_planの固定配布lock読取りは実装ROOTを使うため、必要な設定だけを置く。
        (self.root / "config").mkdir()
        for name in ("authority", "fixture"):
            source = operations.ROOT / ("config/" + name + "-runtime.lock.json")
            (self.root / "config" / source.name).write_bytes(source.read_bytes())
        self.paths = self.plan["payload"]["output_paths"]
        self.runtime = Mock()
        self.runtime.prefix = "gah-authority-" + "a" * 32
        self.runtime.lock = json.loads((self.root / "config/authority-runtime.lock.json").read_bytes())
        self.runtime.state = {"containers": []}
        self.runtime.command.side_effect = lambda args: json.dumps([{"Id": args[-1]}])
        self.factory = Mock(return_value=self.runtime)
        self.core = Mock(return_value=self.result())
        self.doctor = Mock(return_value=(operation_result("ops.doctor", "doctor-test", "COMPLETED", checked_at=1000), {}))

    def result(self):
        ref = lambda kind: {"kind": kind, "id": "sample", "digest": "a" * 64}
        return {"contract_series_id": "sample", "baseline_series_id": "sample-baseline", "contract_ref": ref("evaluation_contract"),
                "baseline_ref": ref("baseline"), "initial_receipt": {"manifest_ref": ref("run_manifest")},
                "candidate_receipts": {s: {"manifest_ref": ref("run_manifest")} for s in ("old", "new")},
                "run_request": {"run_id": "sample"}, "ci_request": {"request_id": "sample"}}

    def apply(self, **kwargs):
        with patch.object(setup_apply, "execute_contract_setup", self.core), patch.object(operations, "_trusted_principal", return_value="os-test"):
            return setup_apply.apply(self.root, self.plan_path, runtime_factory=self.factory, doctor=self.doctor, clock=lambda: 1000, **kwargs)

    def assert_no_requests(self):
        for name in ("run_request", "ci_request"):
            self.assertFalse(Path(self.paths[name]).exists())

    def test_system_clock_fraction_is_normalized_for_journal_and_doctor(self):
        self.doctor.return_value = (operation_result("ops.doctor", "doctor-test", "INCOMPLETE", reasons=["CAPACITY_EXCEEDED"], checked_at=1000), {})
        with patch.object(operations, "_trusted_principal", return_value="os-test"):
            result = setup_apply.apply(self.root, self.plan_path, runtime_factory=self.factory,
                                       doctor=self.doctor, clock=lambda: 1000.25)
        self.assertEqual(result["reasons"], ["CAPACITY_EXCEEDED"])
        self.doctor.assert_called_once()
        self.assertIs(type(self.doctor.call_args.kwargs["clock"]()), int)
        self.factory.assert_not_called()

    def test_bootstrap_failure_prevents_runtime_creation(self):
        self.doctor.return_value = (operation_result("ops.doctor", "doctor-test", "INCOMPLETE", reasons=["CLOCK_UNAVAILABLE"], checked_at=1000), {})
        result = self.apply()
        self.assertEqual(result["operation_status"], "INCOMPLETE", result)
        self.factory.assert_not_called(); self.core.assert_not_called(); self.assert_no_requests()

    def test_ready_failure_leaves_partial_setup_without_requests(self):
        self.doctor.side_effect = [self.doctor.return_value, (operation_result("ops.doctor", "doctor-test", "INCOMPLETE", reasons=["BINDING_MISMATCH"], checked_at=1000), {})]
        result = self.apply()
        self.assertEqual(result["reasons"], ["BINDING_MISMATCH"], result)
        self.runtime.close_clients.assert_called_once(); self.assert_no_requests()

    def test_cleanup_failure_cannot_publish_requests(self):
        self.runtime.close_clients.side_effect = OSError("cleanup failed")
        result = self.apply()
        self.assertEqual(result["operation_status"], "INCOMPLETE", result)
        self.assert_no_requests()

    def test_interrupt_is_not_a_stop_confirmed_cancellation(self):
        self.core.side_effect = KeyboardInterrupt()
        result = self.apply()
        self.assertEqual(result["operation_status"], "INCOMPLETE", result)
        self.assertIn("OPERATION_UNKNOWN", result["reasons"])
        self.runtime.close_clients.assert_called_once(); self.assert_no_requests()

    def test_existing_output_is_not_overwritten_or_dispatched(self):
        target = Path(self.paths["run_request"]); target.parent.mkdir(parents=True); target.write_text("keep")
        result = self.apply()
        self.assertEqual(result["reasons"], ["RESULT_CONFLICT"], result)
        self.factory.assert_not_called(); self.assertEqual(target.read_text(), "keep")

    def test_invalid_request_id_is_rejected_without_principal_fallback(self):
        result = self.apply(request_id="")
        self.assertEqual(result["operation_status"], "REJECTED", result)
        self.assertIsNone(result["request_id"])
        self.factory.assert_not_called(); self.assert_no_requests()
