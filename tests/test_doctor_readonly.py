"""doctor bootstrap の容量profileがworkspaceを書き換えない回帰試験。"""
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import json
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gah.productization import operation_result
from tools import gah_ops


def snapshot(root: Path):
    if not root.exists():
        return None
    rows = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_dir():
            rows.append((relative, "dir", None))
        elif path.is_file():
            rows.append((relative, "file", hashlib.sha256(path.read_bytes()).hexdigest()))
        else:
            rows.append((relative, "other", None))
    return rows


class DoctorReadOnlyTests(unittest.TestCase):
    def _invoke_with_stub(self, workspace, request_id):
        completed = operation_result("ops.doctor", request_id, "COMPLETED")
        with patch.object(gah_ops, "run_doctor", return_value=(completed, None)) as doctor:
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = gah_ops.main([
                    "doctor", "--phase", "bootstrap", "--workspace", str(workspace),
                    "--profile", "sample-ci", "--request-id", request_id, "--json",
                ])
        self.assertEqual(code, 0)
        self.assertEqual(doctor.call_count, 1)
        self.assertIs(doctor.call_args.kwargs["capacity_profile"]["bound_verified"], False)
        return stdout.getvalue()

    def test_profile_keeps_existing_workspace_unchanged(self):
        with tempfile.TemporaryDirectory(prefix="gah-doctor-readonly-") as temp:
            workspace = Path(temp) / "existing"
            workspace.mkdir()
            (workspace / "sentinel.txt").write_text("keep", encoding="utf-8")
            before = snapshot(workspace)
            output = self._invoke_with_stub(workspace, "doctor-readonly-existing")
            self.assertEqual(snapshot(workspace), before)
            self.assertIn('"operation_status":"COMPLETED"', output)
            self.assertFalse((workspace / ".ga" / "operations" / "capacity").exists())

    def test_profile_does_not_create_missing_workspace(self):
        with tempfile.TemporaryDirectory(prefix="gah-doctor-readonly-") as temp:
            workspace = Path(temp) / "not-created"
            self.assertFalse(workspace.exists())
            output = self._invoke_with_stub(workspace, "doctor-readonly-missing")
            self.assertFalse(workspace.exists())
            self.assertIn('"operation_status":"COMPLETED"', output)

    def test_invalid_profile_is_rejected_without_filesystem_changes(self):
        with tempfile.TemporaryDirectory(prefix="gah-doctor-readonly-") as temp:
            workspace = Path(temp) / "existing"
            workspace.mkdir()
            (workspace / "sentinel.txt").write_text("keep", encoding="utf-8")
            before = snapshot(workspace)
            stdout, stderr = io.StringIO(), io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = gah_ops.main([
                    "doctor", "--phase", "bootstrap", "--workspace", str(workspace),
                    "--profile", "untrusted-profile", "--request-id", "doctor-invalid",
                ])
            self.assertEqual(code, 1)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["operation_status"], "REJECTED")
            self.assertIn("INVALID_INPUT", payload["reasons"])
            self.assertEqual(snapshot(workspace), before)
            self.assertEqual(stderr.getvalue(), "")


if __name__ == "__main__":
    unittest.main()