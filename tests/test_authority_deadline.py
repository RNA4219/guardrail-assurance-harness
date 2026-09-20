"""AuthorityRuntime absolute deadline dispatch boundaries (no Docker)."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tools.authority_runtime import AuthorityRuntime, AuthorityRuntimeError
from gah.wire import canonical_bytes

PREFIX = "gah-authority-" + "b" * 32


def runtime() -> AuthorityRuntime:
    value = object.__new__(AuthorityRuntime)
    value.prefix = PREFIX
    value.lock = {"image_id": "sha256:" + "a" * 64}
    value.state = {"containers": []}
    value.environment = {}
    value.folder = Path(".")
    value.docker = "docker"
    value.endpoint = "unix:///var/run/docker.sock"
    value.reuse_clients = False
    value.keep_clients_running = False
    value._client_mutex = threading.RLock()
    value._running_clients = {}
    value._reusable_clients = {}
    value._last_authority_clock = None
    return value


class AuthorityDeadlineTests(unittest.TestCase):
    def test_strict_deadline_and_expired_deadline_dispatch_nothing(self):
        value = runtime()
        value._client_once = Mock()
        for invalid in (True, False, 1.0, "10", -1, 2**63):
            with self.subTest(deadline=invalid), self.assertRaisesRegex(
                    AuthorityRuntimeError, "^INVALID_DEADLINE$"):
                value.client(12004, {"action": "current"}, deadline_ns=invalid)
        with patch("tools.authority_runtime.time.monotonic_ns", return_value=100):
            with self.assertRaisesRegex(AuthorityRuntimeError, "^TIMEOUT$"):
                value.client(12004, {"action": "current"}, deadline_ns=99)
        value._client_once.assert_not_called()

    def test_invalid_identity_is_rejected_before_request_serialization(self):
        value = runtime()
        value._client_once = Mock()
        for uid in (True, "12004", 0):
            with self.subTest(uid=uid), self.assertRaisesRegex(
                    AuthorityRuntimeError, "^IDENTITY_NOT_CONFIGURED$"):
                value.client(uid, object())
        value._client_once.assert_not_called()

    def test_clock_rejection_is_saved_even_if_deadline_expires_during_response(self):
        value = runtime()
        clock = [0]
        rollback = {"schema_version": 1, "kind": "authority_error",
                    "reason": "CLOCK_ROLLBACK", "ci_eligible": False}
        def invoke(*args, **kwargs):
            clock[0] = 10
            return rollback
        value._client_once = Mock(side_effect=invoke)
        value._record_clock_rejection = Mock()
        with patch("tools.authority_runtime.time.monotonic_ns", side_effect=lambda: clock[0]), \
             patch("tools.authority_runtime.time.sleep") as sleep:
            with self.assertRaisesRegex(AuthorityRuntimeError, "^TIMEOUT$"):
                value.client(12004, {"request_id": "r"}, deadline_ns=10)
        value._record_clock_rejection.assert_called_once_with(
            12004, canonical_bytes({"request_id": "r"}), retry_scheduled=False)
        value._client_once.assert_called_once()
        sleep.assert_not_called()

    def test_command_caps_transport_timeout_and_preserves_timeout_reason(self):
        value = runtime()
        success = SimpleNamespace(reason=None, returncode=0, stdout=b"ok")
        with patch("tools.authority_runtime.time.monotonic_ns", return_value=0), \
             patch("tools.authority_runtime.capture_bounded", return_value=success) as capture:
            self.assertEqual(value.command(["container", "inspect", "x"],
                timeout=45, deadline_ns=250_000_000), b"ok")
        self.assertEqual(capture.call_args.kwargs["timeout"], 0.25)
        timed_out = SimpleNamespace(reason="TIMEOUT", returncode=-9, stdout=b"")
        with patch("tools.authority_runtime.time.monotonic_ns", return_value=0), \
             patch("tools.authority_runtime.capture_bounded", return_value=timed_out) as capture:
            with self.assertRaisesRegex(AuthorityRuntimeError, "^TIMEOUT$"):
                value.command(["container", "start", "--attach", "--interactive", "x"],
                    timeout=45, deadline_ns=500_000_000)
        self.assertEqual(capture.call_args.kwargs["timeout"], 0.5)

    def test_create_inspect_start_receive_same_deadline_and_cleanup_is_separate(self):
        value = runtime()
        value._save = Mock()
        value._verify_config = Mock()
        def dispatch(args, **kwargs):
            if args[:2] == ["container", "inspect"]:
                name = args[-1]
                data = {"Name": "/" + name,
                    "Config": {"Labels": {"org.gah.authority.instance": PREFIX}},
                    "State": {"Running": False, "Pid": 0, "ExitCode": 0}}
                return json.dumps([data]).encode("utf-8")
            return canonical_bytes({"ok": True})
        value.command = Mock(side_effect=dispatch)
        value.inspect = AuthorityRuntime.inspect.__get__(value)
        value.remove_container = Mock()
        deadline = 1_000_000_000
        with patch("tools.authority_runtime.time.monotonic_ns", return_value=0):
            result = value._client_once(12001, {"request_id": "r"}, deadline_ns=deadline)
        self.assertEqual(result, {"ok": True})
        value._save.assert_called_once_with()
        dispatches = value.command.call_args_list
        self.assertEqual([call.args[0][:2] for call in dispatches], [
            ["container", "create"], ["container", "inspect"],
            ["container", "start"], ["container", "inspect"]])
        self.assertTrue(all(call.kwargs["deadline_ns"] == deadline for call in dispatches))
        self.assertEqual(dispatches[2].kwargs["timeout"], 45)
        value.remove_container.assert_called_once()
        self.assertEqual(value.remove_container.call_args.kwargs, {})
        self.assertEqual(len(value.state["containers"]), 1)

    def test_exec_deadline_and_cleanup_failure_keep_owner_record_and_release_mutex(self):
        value = runtime()
        name = PREFIX + "-client-" + "c" * 32
        value._running_clients = {12001: name}
        value._verify_config = Mock()
        running = {"Id": "container-id", "State": {"Running": True, "Pid": 7,
            "Status": "running", "OOMKilled": False, "StartedAt": "fixed", "ExitCode": 0},
            "RestartCount": 0}
        value.inspect = Mock(return_value=running)
        value.command = Mock(return_value=canonical_bytes({"ok": True}))
        deadline = 1_000_000_000
        with patch("tools.authority_runtime.time.monotonic_ns", return_value=0):
            result = value._running_client_once(12001, {"request_id": "r"}, deadline_ns=deadline)
        self.assertEqual(result, {"ok": True})
        self.assertEqual(value.command.call_args.args[0][:2], ["container", "exec"])
        self.assertEqual(value.command.call_args.kwargs["deadline_ns"], deadline)
        self.assertEqual(value.command.call_args.kwargs["timeout"], 45)

        value = runtime()
        value._running_clients = {12001: name}
        value._verify_config = Mock()
        value.inspect = Mock(return_value=running)
        value.command = Mock(side_effect=AuthorityRuntimeError("TIMEOUT"))
        value.remove_container = Mock(side_effect=AuthorityRuntimeError("STOP_UNCONFIRMED"))
        with patch("tools.authority_runtime.time.monotonic_ns", return_value=0):
            with self.assertRaisesRegex(AuthorityRuntimeError, "^STOP_UNCONFIRMED$"):
                value._running_client_once(12001, {"request_id": "r"}, deadline_ns=deadline)
        value.remove_container.assert_called_once_with(name)
        self.assertEqual(value._running_clients[12001], name)
        # A same-thread acquire cannot detect a leaked RLock acquisition.
        acquired = []
        def other_thread():
            held = value._client_mutex.acquire(timeout=0.5)
            acquired.append(held)
            if held:
                value._client_mutex.release()
        checker = threading.Thread(target=other_thread)
        checker.start()
        checker.join(2)
        self.assertFalse(checker.is_alive())
        self.assertEqual(acquired, [True], "mutex must be released after cleanup failure")

    def test_maximum_valid_deadline_does_not_overflow_platform_lock(self):
        value = runtime()
        with patch("tools.authority_runtime.time.monotonic_ns", return_value=0):
            with value._client_lock(2**63 - 1):
                pass

    def test_platform_sized_lock_wait_rechecks_remaining_deadline(self):
        value = runtime()
        clock = [0]
        waits = []
        def acquire(*, timeout):
            waits.append(timeout)
            if len(waits) == 1:
                clock[0] = 250_000_000
                return False
            return True
        value._client_mutex = Mock(acquire=Mock(side_effect=acquire))
        with patch("tools.authority_runtime.TIMEOUT_MAX", 0.25), \
             patch("tools.authority_runtime.time.monotonic_ns", side_effect=lambda: clock[0]):
            with value._client_lock(1_000_000_000):
                pass
        self.assertEqual(waits, [0.25, 0.25])
        value._client_mutex.release.assert_called_once_with()

    def test_mutex_wait_expires_without_dispatch(self):
        value = runtime()
        value.reuse_clients = True
        value._create = Mock()
        value.inspect = Mock()
        value._client_mutex.acquire()
        errors = []
        ready = threading.Event()
        deadline = time.monotonic_ns() + 80_000_000
        def invoke():
            ready.set()
            try:
                value._reused_client_once(12001, {"request_id": "r"}, deadline_ns=deadline)
            except AuthorityRuntimeError as error:
                errors.append(str(error))
        worker = threading.Thread(target=invoke)
        try:
            worker.start()
            self.assertTrue(ready.wait(1))
            worker.join(2)
        finally:
            value._client_mutex.release()
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, ["TIMEOUT"])
        value._create.assert_not_called()
        value.inspect.assert_not_called()

    def test_clock_rollback_retry_shares_deadline_and_skips_retry_when_expired(self):
        value = runtime()
        rollback = {"schema_version": 1, "kind": "authority_error",
                    "reason": "CLOCK_ROLLBACK", "ci_eligible": False}
        value._client_once = Mock(side_effect=[rollback, {"ok": True}])
        value._record_clock_rejection = Mock()
        with patch("tools.authority_runtime.time.monotonic_ns", return_value=0), \
             patch("tools.authority_runtime.time.sleep") as sleep:
            self.assertEqual(value.client(12004, {"request_id": "r"},
                deadline_ns=10_000_000_000), {"ok": True})
        self.assertEqual(value._client_once.call_count, 2)
        self.assertTrue(all(call.kwargs["deadline_ns"] == 10_000_000_000
                            for call in value._client_once.call_args_list))
        value._record_clock_rejection.assert_called_once_with(
            12004, canonical_bytes({"request_id": "r"}), retry_scheduled=True)
        sleep.assert_called_once_with(2)

        value._client_once.reset_mock(side_effect=True)
        value._client_once.side_effect = [rollback]
        value._record_clock_rejection.reset_mock()
        with patch("tools.authority_runtime.time.monotonic_ns", return_value=0), \
             patch("tools.authority_runtime.time.sleep") as sleep:
            with self.assertRaisesRegex(AuthorityRuntimeError, "^TIMEOUT$"):
                value.client(12004, {"request_id": "r"}, deadline_ns=2_000_000_000)
        value._client_once.assert_called_once()
        value._record_clock_rejection.assert_called_once_with(
            12004, canonical_bytes({"request_id": "r"}), retry_scheduled=False)
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
