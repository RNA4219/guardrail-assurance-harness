"""CI/reportが輸送を排他し、cleanup失敗時に成功を先出ししない。"""
from contextlib import contextmanager, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from tools import gah_ci, gah_report

class CliLifecycleTests(unittest.TestCase):
    def exercise(self, module, *, cleanup_error=False, missing=False, run_error=False):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); folder = root / "runtime"; folder.mkdir()
            if not missing: (folder / "deployment.json").write_text("{}")
            request = root / "request.json"; request.write_text("{}")
            events = []; runtime = Mock()
            def close():
                events.append("close")
                if cleanup_error: raise OSError("cleanup failed")
            runtime.close_clients.side_effect = close
            @contextmanager
            def lock(path, run, operation):
                self.assertEqual((path, run, operation), (folder / "supervised-transport", "deployment", "supervisor"))
                events.append("lock")
                try: yield
                finally: events.append("unlock")
            def execute(rt, document, stream, **kwargs):
                self.assertIs(rt, runtime); events.append("run")
                if run_error: raise OSError("read failed")
                stream.write('{"ci_eligible":true,"exit_code":0}\n')
                return 0
            output = io.StringIO()
            with patch.object(module, "ROOT", root), patch.object(module, "AuthorityRuntime", return_value=runtime) as factory, patch.object(module, "operation_lock", lock), patch.object(module, "run", execute), patch("sys.argv", [module.__name__, "--runtime", str(folder), "--request", str(request)]), redirect_stdout(output):
                code = module.main()
            rows = [json.loads(line) for line in output.getvalue().splitlines()]
            return code, rows, events, factory.call_count

    def test_cleanup_precedes_success_and_shared_lock_covers_it(self):
        for module in (gah_ci, gah_report):
            with self.subTest(module=module.__name__):
                code, rows, events, count = self.exercise(module)
                self.assertEqual((code, len(rows), count), (0, 1, 1))
                self.assertEqual(events, ["lock", "run", "close", "unlock"])

    def test_cleanup_failure_emits_only_failure(self):
        for module in (gah_ci, gah_report):
            with self.subTest(module=module.__name__):
                code, rows, events, _ = self.exercise(module, cleanup_error=True)
                self.assertEqual((code, len(rows)), (2, 1))
                self.assertIs(rows[0]["ci_eligible"], False)
                self.assertEqual(events[-2:], ["close", "unlock"])

    def test_read_exception_still_closes_clients(self):
        for module in (gah_ci, gah_report):
            with self.subTest(module=module.__name__):
                code, rows, events, _ = self.exercise(module, run_error=True)
                self.assertEqual((code, len(rows)), (2, 1))
                self.assertEqual(events[-2:], ["close", "unlock"])

    def test_missing_deployment_does_not_construct_runtime(self):
        for module in (gah_ci, gah_report):
            with self.subTest(module=module.__name__):
                code, rows, events, count = self.exercise(module, missing=True)
                self.assertEqual((code, len(rows), count), (2, 1, 0))
                self.assertEqual(events, [])
