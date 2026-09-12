"""Docker監督の境界試験。Docker daemonとprobeは起動しない。"""

from __future__ import annotations

import copy
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from unittest import TestCase, mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gah.contracts import ContractError
from gah.docker_runner import (
    Capture,
    DockerRunner,
    RunnerError,
    capture_bounded,
    operation_lock,
    validate_probe,
)
from gah.wire import canonical_bytes


_DIGEST = "a" * 64
_STREAM_LIMIT = 256 * 1024
_EXPECTED_MEMORY = 128 * 1024 * 1024
_EXPECTED_ENTRYPOINT = ["/usr/local/bin/python", "-I", "-B", "/opt/gah/fixture_worker.py"]
_EXPECTED_TMPFS = {
    "/work": "rw,noexec,nosuid,nodev,size=16777216,mode=700,uid=65532,gid=65532",
    "/tmp": "rw,noexec,nosuid,nodev,size=16777216,mode=700,uid=65532,gid=65532",
    "/dev/shm": "ro,noexec,nosuid,nodev,size=1048576",
}
_EXPECTED_PROBE_KEYS = {
    "nonroot", "root_readonly", "network_unreachable", "capabilities_dropped",
    "no_new_privileges", "seccomp_filter", "pid_limit", "memory_limit", "cpu_limit",
}


def binding(run_id: str = "run-1", operation_id: str = "op-1") -> dict[str, object]:
    return {
        "run_id": run_id,
        "operation_id": operation_id,
        "owner_epoch": 1,
        "contract_digest": _DIGEST,
        "target_digest": _DIGEST,
        "obligation_id": "obligation-1",
        "case_id": "case-1",
        "trial_id": "trial-1",
        "stage_id": "stage-1",
        "fixture_digest": _DIGEST,
        "adapter_digest": _DIGEST,
        "policy_digest": _DIGEST,
        "evaluator_digest": _DIGEST,
        "isolation_digest": _DIGEST,
    }


def probe_document(bound: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "schema_version": 1,
        "kind": "gah_isolation_probe",
        "binding": bound or binding(),
        "checks": {key: True for key in _EXPECTED_PROBE_KEYS},
    }


def _fake_runner_for_config() -> DockerRunner:
    runner = object.__new__(DockerRunner)
    runner.lock = {"environment": []}
    return runner


def _container(scenario: str = "constraint:C01:good") -> dict[str, object]:
    host = {
        "NetworkMode": "none",
        "ReadonlyRootfs": True,
        "Privileged": False,
        "CapDrop": ["ALL"],
        "CapAdd": None,
        "SecurityOpt": ["no-new-privileges=true"],
        "Memory": _EXPECTED_MEMORY,
        "MemorySwap": _EXPECTED_MEMORY,
        "NanoCpus": 500000000,
        "PidsLimit": 32,
        "IpcMode": "private",
        "CgroupnsMode": "private",
        "PidMode": None,
        "Binds": None,
        "Devices": None,
        "DeviceRequests": None,
        "VolumesFrom": None,
        "PortBindings": None,
        "ExtraHosts": None,
        "Tmpfs": _EXPECTED_TMPFS,
        "LogConfig": {"Type": "none"},
        "RestartPolicy": {"Name": "no"},
    }
    config = {
        "User": "65532:65532",
        "WorkingDir": "/work",
        "Entrypoint": _EXPECTED_ENTRYPOINT,
        "Cmd": ["--scenario", scenario],
        "Env": [],
        "Volumes": None,
        "ExposedPorts": None,
        "OpenStdin": True,
        "Tty": False,
    }
    mounts = [{"Type": "tmpfs", "Destination": destination} for destination in _EXPECTED_TMPFS]
    return {"HostConfig": host, "Config": config, "Mounts": mounts}


class CaptureBoundedTests(TestCase):
    def _python(self, source: str, *, timeout: float, limit: int = _STREAM_LIMIT,
                cancel_event: threading.Event | None = None) -> Capture:
        return capture_bounded(
            [sys.executable, "-c", source],
            timeout=timeout,
            cwd=Path.cwd(),
            environment=dict(os.environ),
            limit=limit,
            cancel_event=cancel_event,
        )

    def test_each_stream_limit_discards_collected_output(self) -> None:
        result = self._python("import sys; sys.stdout.write('x' * 1000)", timeout=5, limit=32)
        self.assertEqual(result.reason, "OUTPUT_TOO_LARGE")
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"")

    def test_exact_limit_is_accepted_and_stderr_overflow_is_discarded(self) -> None:
        result = self._python("import sys; sys.stdout.write('x' * 32)", timeout=5, limit=32)
        self.assertIsNone(result.reason)
        self.assertEqual(result.stdout, b"x" * 32)
        result = self._python("import sys; sys.stderr.write('x' * 33)", timeout=5, limit=32)
        self.assertEqual(result.reason, "OUTPUT_TOO_LARGE")
        self.assertEqual((result.stdout, result.stderr), (b"", b""))

    def test_timeout_kills_fixed_child_and_does_not_admit_output(self) -> None:
        result = self._python("import time; time.sleep(10)", timeout=0.1)
        self.assertEqual(result.reason, "TIMEOUT")
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"")

    def test_pre_requested_cancel_avoids_process_creation(self) -> None:
        event = threading.Event()
        event.set()
        with mock.patch("gah.docker_runner.subprocess.Popen") as popen:
            result = self._python("raise SystemExit(0)", timeout=5, cancel_event=event)
        self.assertEqual(result.reason, "CANCEL_REQUESTED")
        popen.assert_not_called()

    def test_cancel_during_collection_kills_child(self) -> None:
        event = threading.Event()

        def cancel() -> None:
            time.sleep(0.05)
            event.set()

        setter = threading.Thread(target=cancel)
        setter.start()
        try:
            result = self._python("import time; time.sleep(10)", timeout=5, cancel_event=event)
        finally:
            setter.join(timeout=1)
        self.assertEqual(result.reason, "CANCEL_REQUESTED")
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"")


class ProbeValidationTests(TestCase):
    def test_valid_probe_requires_four_fields_and_all_boolean_checks(self) -> None:
        document = probe_document()
        self.assertEqual(validate_probe(canonical_bytes(document), binding()), document)
        self.assertEqual(set(document["checks"]), _EXPECTED_PROBE_KEYS)

    def test_probe_rejects_type_and_binding_drift_without_returning_payload(self) -> None:
        bad_type = probe_document()
        bad_type["checks"] = dict(bad_type["checks"])
        bad_type["checks"]["pid_limit"] = 1
        with self.assertRaises(ContractError):
            validate_probe(canonical_bytes(bad_type), binding())

        mismatch = probe_document(binding("other-run", "op-1"))
        with self.assertRaisesRegex(ContractError, "^BINDING_MISMATCH$"):
            validate_probe(canonical_bytes(mismatch), binding())

        extra = probe_document()
        extra["unexpected"] = True
        with self.assertRaises(ContractError):
            validate_probe(canonical_bytes(extra), binding())

        with self.assertRaisesRegex(ContractError, "^OUTPUT_TOO_LARGE$"):
            validate_probe(b"x" * (_STREAM_LIMIT + 1), binding())


class DockerConfigurationTests(TestCase):
    def test_configuration_drift_is_rejected_for_network_and_mount_policy(self) -> None:
        runner = _fake_runner_for_config()
        container = _container()
        runner._verify_config(container, "constraint:C01:good")

        for field, value in (("NetworkMode", "bridge"), ("ReadonlyRootfs", False)):
            with self.subTest(field=field):
                drift = copy.deepcopy(container)
                drift["HostConfig"][field] = value
                with self.assertRaisesRegex(RunnerError, "^CONFIG_MISMATCH$"):
                    runner._verify_config(drift, "constraint:C01:good")

        drift = copy.deepcopy(container)
        drift["HostConfig"]["Binds"] = ["/host:/work"]
        with self.assertRaisesRegex(RunnerError, "^CONFIG_MISMATCH$"):
            runner._verify_config(drift, "constraint:C01:good")


class DispatchRecoveryTests(TestCase):
    def test_cancel_after_image_check_does_not_create_container(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runner = object.__new__(DockerRunner)
            runner.journal_path = Path(directory) / "journal.sqlite"
            runner.lock = {"image_id": "sha256:" + _DIGEST, "worker_digest": _DIGEST}
            runner.adapter_digest = runner.isolation_digest = _DIGEST
            runner.target_digest = lambda _: _DIGEST
            runner.clock = lambda: 1000
            event = threading.Event()
            def verify_image(timeout, cancel_event):
                self.assertIs(cancel_event, event)
                event.set()
            runner._verify_image = verify_image
            runner._inspect = lambda _: None
            runner._capture = mock.Mock(side_effect=AssertionError("UNEXPECTED_CREATE"))
            result = runner.run("constraint:C01:good", binding(), run_deadline=2000,
                                timeout_seconds=30, cancel_event=event)
            self.assertEqual(result["execution_status"], "CANCELLED")
            self.assertIsNone(result["container_id"])
            runner._capture.assert_not_called()

    def test_live_owner_excludes_recovery_and_os_lock_is_released(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runner = object.__new__(DockerRunner)
            runner.journal_path = Path(directory) / "journal.sqlite"
            with operation_lock(runner.journal_path, "run-1", "op-1"):
                with self.assertRaisesRegex(RunnerError, "^OWNER_ACTIVE$"):
                    runner.recover("run-1", "op-1")
            with operation_lock(runner.journal_path, "run-1", "op-1"):
                pass

    def test_disappeared_container_needs_previously_persisted_stop(self) -> None:
        runner = object.__new__(DockerRunner)
        runner._inspect = lambda _: None
        record = {"container_id": "c" * 64, "state": "STARTING"}
        self.assertEqual(runner._cleanup(None, record)[:2], (False, False))
        record["state"] = "STOPPED"
        self.assertEqual(runner._cleanup(None, record)[:2], (True, True))

    def test_observed_stop_is_saved_before_container_removal(self) -> None:
        from gah.execution_journal import ExecutionJournal
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "journal.sqlite"
            with ExecutionJournal(path) as journal:
                record = journal.begin(binding(), "constraint:C01:good", "sha256:" + _DIGEST,
                                       run_deadline=2000, timeout_seconds=30)
                record = journal.advance("run-1", "op-1", record["owner_token"], "CREATED", container_id="c" * 64)
                runner = object.__new__(DockerRunner)
                runner._inspect = mock.Mock(side_effect=[{"State": {"Running": False, "Pid": 0, "Status": "created", "ExitCode": 0}}, None])
                def remove(args, **kwargs):
                    self.assertEqual(args[:2], ["container", "rm"])
                    self.assertEqual(journal.get("run-1", "op-1")["state"], "STOPPED")
                    return Capture(0, b"", b"", None)
                runner._capture = remove
                self.assertEqual(runner._cleanup(journal, record)[:2], (True, True))

    def test_pending_intent_is_not_redispatched(self) -> None:
        from gah.execution_journal import ExecutionJournal

        scenario = "constraint:C01:good"
        image_id = "sha256:" + _DIGEST
        current = binding()
        with tempfile.TemporaryDirectory() as directory:
            journal_path = Path(directory) / "journal.sqlite"
            with ExecutionJournal(journal_path) as journal:
                journal.begin(current, scenario, image_id, run_deadline=2000, timeout_seconds=30)

            runner = object.__new__(DockerRunner)
            runner.lock = {"image_id": image_id, "worker_digest": _DIGEST}
            runner.adapter_digest = _DIGEST
            runner.isolation_digest = _DIGEST
            runner.journal_path = journal_path
            runner.target_digest = lambda requested: current["target_digest"]
            with self.assertRaisesRegex(RunnerError, "^DISPATCH_UNRESOLVED$"):
                runner.run(scenario, current, run_deadline=2000, timeout_seconds=30)

    def test_unknown_daemon_state_keeps_stop_unconfirmed(self) -> None:
        runner = object.__new__(DockerRunner)
        calls: list[list[str]] = []

        def inspect(_record: dict[str, object]) -> dict[str, object]:
            if calls:
                raise RunnerError("DOCKER_UNAVAILABLE")
            calls.append(["inspect"])
            return {"State": {"Running": True, "Pid": 42, "Status": "running"}}

        runner._inspect = inspect
        runner._capture = lambda args, **_: calls.append(args) or Capture(None, b"", b"", "DOCKER_UNAVAILABLE")
        record = {
            "container_id": "c" * 64,
            "container_name": "gah-" + "a" * 32,
        }
        stopped, cleaned, exit_code, _ = runner._cleanup(None, record)
        self.assertFalse(stopped)
        self.assertFalse(cleaned)
        self.assertIsNone(exit_code)
        self.assertIn(["container", "kill", record["container_name"]], calls)


if __name__ == "__main__":
    import unittest

    unittest.main()
