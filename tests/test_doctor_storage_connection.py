"""起動済みDocker storage観測をdoctor証跡へ接続する。"""
from contextlib import contextmanager
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gah.operations import run_doctor
from tools import gah_ops
from tools.authority_storage_probe import observe_authority_storage
from tests import test_operations as operation_helpers


class DoctorStorageConnectionTests(unittest.TestCase):
    def setUp(self):
        self.helper = operation_helpers.OperationsTests("runTest")
        self.helper.setUp()
        self.addCleanup(self.helper.doCleanups)

    def test_cli_ready_collects_from_same_runtime_before_cleanup(self):
        runtime = object()
        observation = observe_authority_storage(runtime)
        events = []
        @contextmanager
        def opened(*args):
            events.append("opened")
            try:
                yield runtime
            finally:
                events.append("closed")
        def observe(value):
            self.assertIs(value, runtime)
            events.append("observed")
            return observation
        def doctor(*args, **kwargs):
            self.assertIs(kwargs["authority"], runtime)
            self.assertIs(kwargs["storage_observation"], observation)
            events.append("doctor")
            return {"operation_status": "INCOMPLETE"}, None
        args = gah_ops._parser().parse_args([
            "doctor", "--phase", "ready", "--workspace", "workspace",
            "--runtime", "runtime", "--request-id", "storage-cli"])
        with patch.object(gah_ops, "_doctor_authority", opened), patch(
                "tools.authority_storage_probe.observe_authority_storage", side_effect=observe), patch.object(
                gah_ops, "run_doctor", side_effect=doctor):
            result = gah_ops._run_doctor_cli(args)
        self.assertEqual(result["operation_status"], "INCOMPLETE")
        self.assertEqual(events, ["opened", "observed", "doctor", "closed"])

    def test_unknown_storage_is_saved_without_changing_capacity_to_pass(self):
        observation = observe_authority_storage(object())
        with tempfile.TemporaryDirectory() as temp, patch(
                "gah.operations.subprocess.run", side_effect=self.helper._docker_success):
            result, payload = run_doctor(
                temp, "bootstrap", request_id="storage-note",
                clock=lambda: 1700000000, monotonic_clock=lambda: 5000,
                docker_path="docker", storage_observation=observation,
                disk_usage=lambda _: type("Usage", (), {"free": 2**40})())
            self.assertEqual(payload["observations"]["docker_storage"], observation)
            self.assertEqual(next(c for c in payload["checks"] if c["check_id"] == "capacity")["status"], "UNKNOWN")
            self.assertFalse(result["ci_eligible"])
            saved = json.loads((Path(temp) / ".ga/operations/doctor/storage-note.json").read_text(encoding="utf8"))
            self.assertEqual(saved["observations"]["docker_storage"], observation)

    def test_invalid_storage_document_is_rejected_before_external_probe(self):
        with tempfile.TemporaryDirectory() as temp, patch("gah.operations.subprocess.run") as external:
            result, payload = run_doctor(temp, storage_observation={"status": "PASS"})
            self.assertEqual(result["operation_status"], "REJECTED")
            self.assertEqual(result["reasons"], ["INVALID_INPUT"])
            self.assertIsNone(payload)
            external.assert_not_called()
            self.assertFalse((Path(temp) / ".ga").exists())


if __name__ == "__main__":
    unittest.main()
