import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tools.authority_runtime import AuthorityRuntime, AuthorityRuntimeError
from tools import gah_run


def diagnostic(request, checked_at=1700000000):
    return {
        "schema_version": 1,
        "kind": "evaluation_authority_result",
        "action": "authority_diagnostics",
        "request_id": request["request_id"],
        "database_schema_version": 4,
        "permission_generation": 0,
        "extension_digest": "a" * 64,
        "checked_at": checked_at,
        "ci_eligible": False,
    }


class DiagnosticClient:
    def __init__(self, values):
        self.values = iter(values)
        self.calls = []

    def __call__(self, uid, request):
        self.calls.append((uid, request))
        value = next(self.values)
        if callable(value):
            return value(request)
        return value


class AuthorityClockTests(unittest.TestCase):
    def runtime(self, values):
        runtime = AuthorityRuntime.__new__(AuthorityRuntime)
        runtime._last_authority_clock = None
        client = DiagnosticClient(values)
        runtime.client = client
        return runtime, client

    def test_clock_uses_fresh_operator_diagnostic_and_returns_checked_at(self):
        runtime, client = self.runtime([lambda request: diagnostic(request, 1700000042)])

        self.assertEqual(runtime.clock(), 1700000042)
        self.assertEqual(len(client.calls), 1)
        uid, request = client.calls[0]
        self.assertEqual(uid, 12004)
        self.assertEqual(set(request), {"schema_version", "action", "request_id"})
        self.assertEqual(request["schema_version"], 1)
        self.assertEqual(request["action"], "authority_diagnostics")
        self.assertIs(type(request["request_id"]), str)

    def test_clock_rejects_closed_shape_and_invalid_utc(self):
        malformed = diagnostic({"request_id": "ignored"})
        malformed.pop("checked_at")
        runtime, _ = self.runtime([malformed])
        with self.assertRaisesRegex(AuthorityRuntimeError, "^CLOCK_RESPONSE_INVALID$"):
            runtime.clock()

        invalid = diagnostic({"request_id": "ignored"})
        invalid["checked_at"] = True
        runtime, _ = self.runtime([invalid])
        with self.assertRaisesRegex(AuthorityRuntimeError, "^CLOCK_RESPONSE_INVALID$"):
            runtime.clock()

    def test_clock_rejects_authority_and_local_rollback(self):
        runtime, _ = self.runtime([
            lambda request: diagnostic(request, 100),
            lambda request: diagnostic(request, 99),
        ])
        self.assertEqual(runtime.clock(), 100)
        with self.assertRaisesRegex(AuthorityRuntimeError, "^CLOCK_ROLLBACK$"):
            runtime.clock()

        runtime, _ = self.runtime([
            {"schema_version": 1, "kind": "authority_error",
             "reason": "CLOCK_ROLLBACK", "ci_eligible": False},
        ])
        with self.assertRaisesRegex(AuthorityRuntimeError, "^CLOCK_ROLLBACK$"):
            runtime.clock()

    def test_gah_run_passes_runtime_clock_to_both_supervisors(self):
        request = {
            "schema_version": 1,
            "run_id": "clock-run",
            "contract_series_id": "clock-series",
            "expected_contract_ref": {
                "kind": "evaluation_contract", "id": "clock-contract",
                "digest": "b" * 64,
            },
            "trigger": "manual",
        }
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            for execution_kind, supervisor_name in (
                ("fixture", "Supervisor"),
                ("guardrail", "LlmSupervisor"),
            ):
                with self.subTest(execution_kind=execution_kind):
                    runtime = SimpleNamespace(
                        clock=Mock(return_value=1700000000),
                        prefix="gah-authority-" + "c" * 32,
                        lock={"image_id": "sha256:" + "d" * 64},
                    )
                    runner = SimpleNamespace(execution_kind=execution_kind)
                    supervisor = Mock()
                    supervisor.return_value.execute.return_value = {"exit_code": 0}
                    with patch.object(gah_run, "Checkpoint") as checkpoint_type,                          patch.object(gah_run, supervisor_name, supervisor):
                        checkpoint_type.return_value.put = Mock()
                        self.assertEqual(
                            gah_run.execute(runtime, runner, folder, request, "status"),
                            {"exit_code": 0},
                        )
                    self.assertIs(supervisor.call_args.kwargs["clock"], runtime.clock)


if __name__ == "__main__":
    unittest.main()
