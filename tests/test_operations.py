"""productization operations の doctor/setup preview 境界テスト。"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gah.contracts import ContractError
from gah.operations import (
    DOCTOR_CHECK_IDS,
    MIN_FREE_BYTES,
    SETUP_ACTIONS,
    _authority_call,
    _persist_setup_ref,
    _wsl_probe,
    resolve_setup_ref,
    run_doctor,
    run_setup_preview,
    validate_doctor_result,
    validate_setup_payload,
)
from gah.productization import (content_ref, operation_result,
                                      validate_operation_result, validate_plan)


class _Sequence:
    def __init__(self, *values: int):
        self._values = iter(values)

    def __call__(self) -> int:
        return next(self._values)


class OperationsTests(unittest.TestCase):
    def setUp(self):
        # doctorのLinux境界を既定にし、Windows専用ケースだけ局所patchする。
        self._platform_patch = patch(
            "gah.operations.platform.system", return_value="Linux")
        self._platform_patch.start()
        self.addCleanup(self._platform_patch.stop)
        # 120秒doctor観測は専用sleep hookで時間を消費せず検査する。
        self._sleep_patch = patch("gah.operations.time.sleep", return_value=None)
        self._sleep_patch.start()
        self.addCleanup(self._sleep_patch.stop)

    def test_setup_preview_writes_closed_plan_and_replays_same_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "plan.json"
            first, plan = run_setup_preview(
                directory, "sample-ci", output, request_id="preview-1",
                clock=lambda: 1_700_000_000)
            self.assertEqual(first["operation_status"], "COMPLETED")
            self.assertEqual(first["exit_code"], 0)
            self.assertEqual(len(first), 10)
            self.assertIs(first["ci_eligible"], False)
            self.assertIsNotNone(plan)
            assert plan is not None
            validate_plan(plan, kind="setup_plan",
                          payload_validator=validate_setup_payload,
                          now=1_700_000_001)
            self.assertEqual(plan["payload"]["actions"], list(SETUP_ACTIONS))
            self.assertIsNone(plan["payload"]["existing_runtime_ref"])
            self.assertEqual(set(plan["payload"]["output_paths"]),
                             {"runtime", "run_request", "ci_request"})
            raw = output.read_bytes()
            second, replay = run_setup_preview(
                directory, "sample-ci", output, request_id="preview-2",
                clock=lambda: 1_700_000_000)
            self.assertEqual(second["operation_status"], "COMPLETED")
            self.assertEqual(second["result_ref"], first["result_ref"])
            self.assertEqual(replay, plan)
            self.assertEqual(output.read_bytes(), raw)

    def test_setup_preview_rejects_path_escape_and_invalid_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            escaped, _ = run_setup_preview(
                directory, "sample-ci", "../plan.json", request_id="bad-path",
                clock=lambda: 1_700_000_000)
            self.assertEqual(escaped["operation_status"], "REJECTED")
            self.assertEqual(escaped["exit_code"], 1)
            self.assertIn("PATH_REJECTED", escaped["reasons"])
            invalid, _ = run_setup_preview(
                directory, "candidate", "plan.json", request_id="bad-profile",
                clock=lambda: 1_700_000_000)
            self.assertEqual(invalid["operation_status"], "REJECTED")
            self.assertIn("INVALID_INPUT", invalid["reasons"])

    def test_doctor_bootstrap_does_not_require_deployment_or_authority(self):
        with tempfile.TemporaryDirectory() as directory:
            result, payload = run_doctor(
                directory, "bootstrap", request_id="doctor-bootstrap",
                clock=lambda: 1_700_000_000,
                monotonic_clock=lambda: 5_000,
                docker_path="docker",
                disk_usage=lambda _: type("Usage", (), {"free": 2**40})())
            self.assertIsNotNone(payload)
            assert payload is not None
            validate_doctor_result(payload)
            self.assertEqual(len(result), 10)
            self.assertIs(result["ci_eligible"], False)
            self.assertEqual(
                {check["check_id"] for check in payload["checks"]},
                DOCTOR_CHECK_IDS)
            for check in payload["checks"]:
                if check["check_id"] in {"runtime", "image_lock", "authority", "binding", "database"}:
                    self.assertEqual(check["status"], "NOT_APPLICABLE")
                    self.assertIsNone(check["reason_code"])
            self.assertTrue((Path(directory) / ".ga" / "operations" /
                             "doctor" / "doctor-bootstrap.json").is_file())

    def test_doctor_detects_wall_clock_rollback_as_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("gah.operations.subprocess.run", side_effect=self._docker_success):
                result, payload = run_doctor(
                    directory, "bootstrap", request_id="doctor-clock",
                    clock=_Sequence(100, 99, 100),
                    monotonic_clock=lambda: 10,
                    docker_path="docker",
                    disk_usage=lambda _: type("Usage", (), {"free": 2**40})())
            self.assertEqual(result["operation_status"], "REJECTED")
            self.assertEqual(result["exit_code"], 1)
            self.assertEqual(result["reasons"], ["CLOCK_ROLLBACK"])
            self.assertIsNotNone(payload)
            assert payload is not None
            self.assertGreaterEqual(payload["finished_at_utc_s"],
                                    payload["started_at_utc_s"])
            clock_checks = [x for x in payload["checks"] if x["check_id"] == "clock"]
            self.assertEqual(clock_checks[0]["reason_code"], "CLOCK_ROLLBACK")

    def test_authority_response_requires_action_request_and_kind(self):
        request = {"schema_version": 1, "action": "contract_current",
                   "request_id": "authority-check", "series_id": "series"}
        with self.assertRaisesRegex(ValueError, "BINDING_MISMATCH"):
            _authority_call(
                lambda _uid, _request: {"schema_version": 1,
                                        "kind": "evaluation_authority_result",
                                        "ci_eligible": False,
                                        "action": "contract_current"},
                12004, request)
        with self.assertRaisesRegex(ValueError, "BINDING_MISMATCH"):
            _authority_call(
                lambda _uid, _request: {"schema_version": 1,
                                        "kind": "policy_adoption_result",
                                        "action": "contract_current",
                                        "request_id": request["request_id"],
                                        "ci_eligible": False},
                12004, request)

    def test_cli_runs_as_local_process_and_returns_json_result(self):
        with tempfile.TemporaryDirectory() as directory:
            command = [sys.executable, "-E", "-B", "-X", "utf8", "-m",
                       "tools.gah_ops", "setup", "preview", "--workspace",
                       directory, "--profile", "sample-ci", "--output",
                       "plan.json", "--request-id", "cli-preview"]
            completed = subprocess.run(command, cwd=ROOT, capture_output=True,
                                       text=True, encoding="utf-8", check=False)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(completed.stdout)
            validate_operation_result(result)
            self.assertEqual(result["command"], "ops.setup.preview")
            self.assertEqual(result["operation_status"], "COMPLETED")
            self.assertTrue((Path(directory) / "plan.json").is_file())

    def test_cli_syntax_error_is_invalid_sentinel(self):
        completed = subprocess.run(
            [sys.executable, "-E", "-B", "-X", "utf8", "-m", "tools.gah_ops",
             "setup", "preview", "--profile", "sample-ci"],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=False)
        self.assertEqual(completed.returncode, 1)
        result = json.loads(completed.stdout)
        validate_operation_result(result)
        self.assertEqual(result["command"], "ops.setup.preview")
        self.assertIsNone(result["request_id"])
        self.assertEqual(result["reasons"], ["INVALID_INPUT"])

    def test_cli_authority_runtime_uses_existing_deployment_and_fixed_lock(self):
        from tools import gah_ops
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "runtime"
            folder.mkdir()
            (folder / "deployment.json").write_bytes(b"{}")
            events = []
            class FakeAuthority:
                def __init__(self, value):
                    events.append(("authority", Path(value)))
                def close_clients(self):
                    events.append("close")
            class FakeLock:
                def __init__(self, value, run_id, operation_id):
                    events.append(("lock", Path(value), run_id, operation_id))
                def __enter__(self):
                    return self
                def __exit__(self, *_args):
                    events.append("unlock")
                    return False
            with patch("tools.authority_runtime.AuthorityRuntime", FakeAuthority), patch(
                    "gah.docker_runner.operation_lock", FakeLock):
                with gah_ops._authority_runtime(directory, "runtime") as authority:
                    self.assertIsInstance(authority, FakeAuthority)
            self.assertEqual(events[0], ("lock", folder / "supervised-transport",
                                         "deployment", "supervisor"))
            self.assertEqual(events[1:], [("authority", folder), "close", "unlock"])

    def test_cli_retention_rejects_explicit_request_id_mismatch(self):
        from tools import gah_ops
        args = SimpleNamespace(retention_action="plan", workspace="workspace",
                               runtime="runtime", request="request.json",
                               request_id="cli-id")
        with patch.object(gah_ops, "read_document", return_value={"request_id": "body-id"}), patch.object(
                gah_ops, "_authority_runtime") as authority, patch.object(
                gah_ops, "_trusted_principal") as principal:
            result = gah_ops._run_retention_cli(args)
        self.assertEqual(result["operation_status"], "REJECTED")
        self.assertEqual(result["reasons"], ["IDEMPOTENCY_CONFLICT"])
        authority.assert_not_called()
        principal.assert_not_called()

    def test_cli_bundle_rejects_malformed_explicit_request_id_without_fallback(self):
        from tools import gah_ops
        args = SimpleNamespace(workspace="workspace", runtime="runtime",
                               run_id="run-id", output="output", request_id="bad/id")
        with patch.object(gah_ops, "_trusted_principal") as principal:
            result = gah_ops._run_bundle_cli(args)
        self.assertEqual(result["operation_status"], "REJECTED")
        self.assertEqual(result["request_id"], None)
        self.assertEqual(result["reasons"], ["INVALID_INPUT"])
        principal.assert_not_called()

    def test_cli_bundle_cleanup_failure_returns_incomplete_without_success(self):
        from tools import gah_ops
        class FailingContext:
            def __enter__(self):
                return object()
            def __exit__(self, *_args):
                raise gah_ops._CliCleanupError("STOP_UNCONFIRMED")
        args = SimpleNamespace(workspace="workspace", runtime="runtime",
                               run_id="run-id", output="output", request_id="bundle-id")
        completed = {
            "command": "ops.bundle.create", "request_id": "bundle-id",
            "operation_status": "COMPLETED",
        }
        with patch.object(gah_ops, "_trusted_principal", return_value="principal"), patch.object(
                gah_ops, "_authority_runtime", return_value=FailingContext()), patch.object(
                gah_ops, "create_bundle", return_value=completed):
            result = gah_ops._run_bundle_cli(args)
        self.assertEqual(result["operation_status"], "INCOMPLETE")
        self.assertEqual(result["reasons"], ["STOP_UNCONFIRMED"])

    def test_cli_doctor_forwards_baseline_series_id(self):
        from tools import gah_ops
        args = gah_ops._parser().parse_args([
            "doctor", "--phase", "bootstrap", "--workspace", "workspace",
            "--baseline-series-id", "baseline-series-cli",
            "--request-id", "cli-doctor",
        ])
        completed = {
            "command": "ops.doctor",
            "request_id": "cli-doctor",
            "operation_status": "COMPLETED",
        }
        with patch.object(
                gah_ops, "run_doctor",
                return_value=(completed, None)) as doctor:
            result = gah_ops._run_doctor_cli(args)
        self.assertEqual(result, completed)
        self.assertEqual(
            doctor.call_args.kwargs["baseline_series_id"],
            "baseline-series-cli",
        )
        self.assertIsNone(doctor.call_args.kwargs["capacity_profile"])

    def test_cli_doctor_bootstrap_profile_supplies_capacity(self):
        from tools import gah_ops
        args = gah_ops._parser().parse_args([
            "doctor", "--phase", "bootstrap", "--workspace", "workspace",
            "--profile", "sample-ci", "--request-id", "cli-profile",
        ])
        capacity = {
            "profile_ref": {
                "kind": "resource_profile",
                "id": "fixed-sample-ci-capacity-v1",
                "digest": "a" * 64,
            },
            "remaining_write_upper_bound_bytes": 100,
            "reserved_bytes": 200,
            "bound_verified": True,
        }
        completed = {
            "command": "ops.doctor",
            "request_id": "cli-profile",
            "operation_status": "COMPLETED",
        }
        with patch(
                "tools.setup_capacity.capacity_profile",
                return_value=capacity) as profile_factory, patch.object(
                gah_ops, "run_doctor",
                return_value=(completed, None)) as doctor:
            result = gah_ops._run_doctor_cli(args)
        self.assertEqual(result, completed)
        profile_factory.assert_called_once_with("sample-ci")
        self.assertIs(doctor.call_args.kwargs["capacity_profile"], capacity)

    def test_cli_doctor_profile_is_rejected_for_ready(self):
        from tools import gah_ops
        args = gah_ops._parser().parse_args([
            "doctor", "--phase", "ready", "--workspace", "workspace",
            "--runtime", "runtime", "--profile", "sample-ci",
            "--request-id", "cli-profile-ready",
        ])
        result = gah_ops._run_doctor_cli(args)
        self.assertEqual(result["operation_status"], "REJECTED")
        self.assertEqual(result["exit_code"], 1)
        self.assertEqual(result["reasons"], ["INVALID_INPUT"])

    def test_cli_doctor_cleanup_failure_does_not_repeat_diagnostics(self):
        from tools import gah_ops
        class FailingContext:
            def __enter__(self):
                return object()
            def __exit__(self, *_args):
                raise gah_ops._CliCleanupError("STOP_UNCONFIRMED")
        args = SimpleNamespace(phase="ready", runtime="runtime",
                               workspace="workspace", contract_series_id=None,
                               request_id="doctor-id")
        completed = {
            "command": "ops.doctor", "request_id": "doctor-id",
            "operation_status": "COMPLETED",
        }
        with patch.object(gah_ops, "_doctor_authority", return_value=FailingContext()), patch.object(
                gah_ops, "run_doctor", return_value=(completed, None)) as doctor:
            result = gah_ops._run_doctor_cli(args)
        self.assertEqual(result["operation_status"], "INCOMPLETE")
        self.assertEqual(result["reasons"], ["STOP_UNCONFIRMED"])
        doctor.assert_called_once()
    def test_doctor_clock_window_uses_injected_sleep_and_three_samples(self):
        sleeps = []
        clock, mono = self._stable_clock()
        with tempfile.TemporaryDirectory() as directory, patch(
                "gah.operations.subprocess.run",
                side_effect=self._docker_success):
            result, payload = run_doctor(
                directory, "bootstrap", request_id="clock-window",
                clock=clock, monotonic_clock=mono,
                sleep=lambda seconds: sleeps.append(seconds),
                docker_path="docker",
                disk_usage=lambda _: type("Usage", (), {"free": 2**40})(),
                capacity_profile=self._capacity(2**40),
            )
        self.assertEqual(result["operation_status"], "COMPLETED")
        assert payload is not None
        observation = payload["observations"]["clock"]
        self.assertEqual(sleeps, [60.0, 60.0])
        self.assertEqual(observation["sample_count"], 3)
        self.assertEqual(len(observation["samples"]), 3)
        self.assertEqual(observation["step_count"], 0)
        self.assertEqual(observation["rollback_count"], 0)
        self.assertTrue(observation["observation_sufficient"])

    def test_doctor_clock_window_rejects_unexplained_step_as_unknown(self):
        wall = _Sequence(1_700_000_000, 1_700_000_060,
                         1_700_000_060, 1_700_000_060)
        mono_calls = [0]
        def mono():
            mono_calls[0] += 1
            return 0 if mono_calls[0] == 1 else 120_000_000_000
        with tempfile.TemporaryDirectory() as directory, patch(
                "gah.operations.subprocess.run",
                side_effect=self._docker_success):
            result, payload = run_doctor(
                directory, "bootstrap", request_id="clock-step",
                clock=wall, monotonic_clock=mono,
                sleep=lambda _seconds: None,
                docker_path="docker",
                disk_usage=lambda _: type("Usage", (), {"free": 2**40})(),
                capacity_profile=self._capacity(2**40),
            )
        self.assertEqual(result["operation_status"], "INCOMPLETE")
        assert payload is not None
        check = next(item for item in payload["checks"]
                     if item["check_id"] == "clock")
        self.assertEqual(check["status"], "UNKNOWN")
        self.assertEqual(check["reason_code"], "CLOCK_UNAVAILABLE")
        observation = payload["observations"]["clock"]
        self.assertEqual(observation["step_count"], 1)
        self.assertEqual(observation["sample_count"], 2)
        self.assertEqual(observation["samples"][1]["wall_utc_s"],
                         1_700_000_060)

    def test_doctor_preserves_raw_finish_when_wall_clock_reverses(self):
        wall = _Sequence(100, 220, 220, 100)
        mono_calls = [0]
        def mono():
            mono_calls[0] += 1
            return 0 if mono_calls[0] == 1 else 120_000_000_000
        with tempfile.TemporaryDirectory() as directory, patch(
                "gah.operations.subprocess.run",
                side_effect=self._docker_success):
            result, payload = run_doctor(
                directory, "bootstrap", request_id="clock-finish-rollback",
                clock=wall, monotonic_clock=mono,
                sleep=lambda _seconds: None,
                docker_path="docker",
                disk_usage=lambda _: type("Usage", (), {"free": 2**40})(),
                capacity_profile=self._capacity(2**40),
            )
        self.assertEqual(result["operation_status"], "REJECTED")
        self.assertEqual(result["reasons"], ["CLOCK_ROLLBACK"])
        assert payload is not None
        self.assertEqual(payload["observations"]["clock"][
            "raw_finished_at_utc_s"], 100)
        self.assertEqual(payload["finished_at_utc_s"], 220)

    @staticmethod
    def _docker_success(argv, **kwargs):
        if kwargs.get("shell") is not False:
            raise AssertionError("fixed probe must disable shell")
        if argv and str(argv[0]).lower().endswith("whoami.exe"):
            stdout = b'"user","S-1-5-21-111-222-333-1001"\\r\\n'
            return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr=b"")
        if len(argv) >= 4 and argv[3] == "version":
            body = {"Server": {"Version": "27.4.0"}}
        elif len(argv) >= 4 and argv[3] == "info":
            body = {"ServerVersion": "27.4.0", "OSType": "linux",
                    "KernelVersion": "6.8.0-generic"}
        else:
            body = {}
        return subprocess.CompletedProcess(argv, 0,
                                           stdout=json.dumps(body).encode("utf-8"),
                                           stderr=b"")

    @staticmethod
    def _capacity(free: int, upper: int = 4096, reserved: int = 2048) -> dict:
        return {
            "profile_ref": {"kind": "resource_profile", "id": "test-profile",
                            "digest": "a" * 64},
            "remaining_write_upper_bound_bytes": upper,
            "reserved_bytes": reserved,
            "bound_verified": True,
        }

    @staticmethod
    def _stable_clock():
        wall_calls = [0]
        mono_calls = [0]

        def wall():
            wall_calls[0] += 1
            return 1_700_000_000 if wall_calls[0] == 1 else 1_700_000_120

        def mono():
            mono_calls[0] += 1
            return 0 if mono_calls[0] == 1 else 120_000_000_000 + mono_calls[0]

        return wall, mono

    def test_doctor_docker_probe_reads_fixed_linux_engine_and_timing(self):
        calls = []
        def fake_run(argv, **kwargs):
            calls.append(list(argv))
            return self._docker_success(argv, **kwargs)
        clock, mono = self._stable_clock()
        with tempfile.TemporaryDirectory() as directory, patch("gah.operations.subprocess.run", side_effect=fake_run):
            result, payload = run_doctor(
                directory, "bootstrap", request_id="doctor-docker-ok",
                clock=clock, monotonic_clock=mono,
                docker_path="docker",
                disk_usage=lambda _: type("Usage", (), {"free": 2**40})(),
                capacity_profile=self._capacity(2**40),
            )
        self.assertEqual(result["operation_status"], "COMPLETED")
        self.assertIsNotNone(payload)
        assert payload is not None
        docker_check = next(x for x in payload["checks"] if x["check_id"] == "docker")
        self.assertEqual(docker_check["status"], "PASS")
        self.assertGreater(docker_check["observed_elapsed_ns"], 0)
        self.assertEqual(payload["observations"]["docker"]["engine_os"], "linux")
        self.assertEqual(payload["observations"]["docker"]["endpoint"], "unix:///var/run/docker.sock")
        docker_calls = [
            item for item in calls
            if len(item) >= 4 and item[3] in {"version", "info"}
        ]
        self.assertEqual(len(docker_calls), 2)
        self.assertEqual(docker_calls[0][1:4], ["--host", "unix:///var/run/docker.sock", "version"])
        self.assertEqual(docker_calls[1][1:4], ["--host", "unix:///var/run/docker.sock", "info"])
        self.assertTrue(all(item["observed_elapsed_ns"] >= 0 for item in payload["checks"]))
        self.assertEqual(len(payload["observations"]["check_timing"]), 13)

    def test_doctor_distinguishes_stopped_docker_from_unknown_probe(self):
        def stopped(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 1, stdout=b"", stderr=b"daemon stopped")
        def timeout(argv, **kwargs):
            raise subprocess.TimeoutExpired(argv, 5)
        clock, mono = self._stable_clock()
        common = dict(
            clock=clock, monotonic_clock=mono,
            docker_path="docker",
            disk_usage=lambda _: type("Usage", (), {"free": 2**40})(),
            capacity_profile=self._capacity(2**40),
        )
        with tempfile.TemporaryDirectory() as directory, patch("gah.operations.subprocess.run", side_effect=stopped):
            stopped_result, stopped_payload = run_doctor(directory, "bootstrap",
                                                          request_id="docker-stopped", **common)
        self.assertEqual(stopped_result["operation_status"], "REJECTED")
        self.assertEqual(stopped_result["exit_code"], 1)
        self.assertIn("DOCKER_UNAVAILABLE", stopped_result["reasons"])
        self.assertEqual(next(x for x in stopped_payload["checks"] if x["check_id"] == "docker")["status"], "FAIL")
        with tempfile.TemporaryDirectory() as directory, patch("gah.operations.subprocess.run", side_effect=timeout):
            unknown_result, unknown_payload = run_doctor(directory, "bootstrap",
                                                          request_id="docker-unknown", **common)
        self.assertEqual(unknown_result["operation_status"], "INCOMPLETE")
        self.assertEqual(unknown_result["exit_code"], 2)
        docker_check = next(x for x in unknown_payload["checks"] if x["check_id"] == "docker")
        self.assertEqual(docker_check["status"], "UNKNOWN")
        self.assertEqual(docker_check["reason_code"], "OBSERVATION_MISSING")

    def test_doctor_capacity_requires_profile_upper_bound_reservation_and_headroom(self):
        clock, mono = self._stable_clock()
        common = dict(
            clock=clock, monotonic_clock=mono,
            docker_path="docker",
        )
        free = 4096 + 2048 + MIN_FREE_BYTES
        profile = self._capacity(free, upper=4096, reserved=2048)
        with tempfile.TemporaryDirectory() as directory, patch("gah.operations.subprocess.run", side_effect=self._docker_success):
            result, payload = run_doctor(
                directory, "bootstrap", request_id="capacity-boundary",
                disk_usage=lambda _: type("Usage", (), {"free": free})(),
                capacity_profile=profile, **common)
        self.assertEqual(result["operation_status"], "COMPLETED")
        capacity = next(x for x in payload["checks"] if x["check_id"] == "capacity")
        self.assertEqual(capacity["status"], "PASS")
        self.assertEqual(payload["observations"]["capacity"]["required_free_bytes"], free)
        with tempfile.TemporaryDirectory() as directory, patch("gah.operations.subprocess.run", side_effect=self._docker_success):
            result, payload = run_doctor(
                directory, "bootstrap", request_id="capacity-short",
                disk_usage=lambda _: type("Usage", (), {"free": free - 1})(),
                capacity_profile=profile, **common)
        self.assertEqual(result["operation_status"], "REJECTED")
        self.assertIn("CAPACITY_EXCEEDED", result["reasons"])
        self.assertEqual(next(x for x in payload["checks"] if x["check_id"] == "capacity")["status"], "FAIL")
        with tempfile.TemporaryDirectory() as directory, patch("gah.operations.subprocess.run", side_effect=self._docker_success):
            result, payload = run_doctor(
                directory, "bootstrap", request_id="capacity-missing",
                disk_usage=lambda _: type("Usage", (), {"free": 2**40})(),
                **common)
        self.assertEqual(result["operation_status"], "INCOMPLETE")
        self.assertEqual(next(x for x in payload["checks"] if x["check_id"] == "capacity")["status"], "UNKNOWN")

    def test_fixed_sample_capacity_is_estimate_and_low_space_still_fails(self):
        from tools.setup_capacity import capacity_profile
        with tempfile.TemporaryDirectory() as directory:
            profile = capacity_profile("sample-ci", workspace=directory)
            self.assertIs(profile["bound_verified"], False)
            saved = json.loads((Path(directory) / ".ga" / "operations" /
                                "capacity" / "fixed-sample-ci-capacity-v1.json"
                                ).read_text(encoding="utf-8"))
            self.assertIs(saved["measured_slo_evidence"], False)
            self.assertIs(saved["bound_verified"], False)
            with patch("gah.operations.subprocess.run",
                       side_effect=self._docker_success):
                clock, mono = self._stable_clock()
                result, payload = run_doctor(
                    directory, "bootstrap", request_id="capacity-estimate",
                    clock=clock, monotonic_clock=mono, docker_path="docker",
                    disk_usage=lambda _: type("U", (), {"free": 2**50})(),
                    capacity_profile=profile)
            self.assertEqual(result["operation_status"], "INCOMPLETE")
            capacity = next(item for item in payload["checks"]
                            if item["check_id"] == "capacity")
            self.assertEqual(capacity["status"], "UNKNOWN")
            self.assertEqual(capacity["reason_code"], "OBSERVATION_MISSING")
            self.assertIs(payload["observations"]["capacity"][
                "bound_verified"], False)
            with patch("gah.operations.subprocess.run",
                       side_effect=self._docker_success):
                clock, mono = self._stable_clock()
                result, payload = run_doctor(
                    directory, "bootstrap", request_id="capacity-estimate-low",
                    clock=clock, monotonic_clock=mono, docker_path="docker",
                    disk_usage=lambda _: type("U", (), {"free": 0})(),
                    capacity_profile=profile)
            self.assertEqual(result["operation_status"], "REJECTED")
            capacity = next(item for item in payload["checks"]
                            if item["check_id"] == "capacity")
            self.assertEqual(capacity["status"], "FAIL")
            self.assertEqual(capacity["reason_code"], "CAPACITY_EXCEEDED")

    def test_doctor_capacity_requires_boolean_evidence_marker(self):
        profile = self._capacity(2**40)
        profile.pop("bound_verified")
        with tempfile.TemporaryDirectory() as directory, patch(
                "gah.operations.subprocess.run",
                side_effect=self._docker_success):
            result, payload = run_doctor(
                directory, "bootstrap", request_id="capacity-no-evidence",
                clock=lambda: 1_700_000_000, monotonic_clock=lambda: 1,
                docker_path="docker",
                disk_usage=lambda _: type("U", (), {"free": 2**40})(),
                capacity_profile=profile)
        self.assertEqual(result["operation_status"], "REJECTED")
        capacity = next(item for item in payload["checks"]
                        if item["check_id"] == "capacity")
        self.assertEqual(capacity["status"], "FAIL")
        self.assertEqual(capacity["reason_code"], "INVALID_INPUT")

        profile = self._capacity(2**40)
        profile["bound_verified"] = 1
        with tempfile.TemporaryDirectory() as directory, patch(
                "gah.operations.subprocess.run",
                side_effect=self._docker_success):
            result, payload = run_doctor(
                directory, "bootstrap", request_id="capacity-false-marker",
                clock=lambda: 1_700_000_000, monotonic_clock=lambda: 1,
                docker_path="docker",
                disk_usage=lambda _: type("U", (), {"free": 2**40})(),
                capacity_profile=profile)
        self.assertEqual(result["operation_status"], "REJECTED")
        capacity = next(item for item in payload["checks"]
                        if item["check_id"] == "capacity")
        self.assertEqual(capacity["status"], "FAIL")
        self.assertEqual(capacity["reason_code"], "INVALID_INPUT")

    def test_doctor_windows_requires_real_wsl_version_despite_wsl_interop(self):
        calls = []
        def fake_run(argv, **kwargs):
            calls.append(list(argv))
            if len(argv) >= 4 and argv[3] == "version":
                return self._docker_success(argv, **kwargs)
            if len(argv) >= 4 and argv[3] == "info":
                return self._docker_success(argv, **kwargs)
            if argv and str(argv[0]).lower().endswith("whoami.exe"):
                return subprocess.CompletedProcess(
                    argv, 0, stdout=b'"user","S-1-5-21-111-222-333-1001"\\r\\n',
                    stderr=b"")
            return subprocess.CompletedProcess(argv, 0, stdout=b"WSL_INTEROP=present", stderr=b"")
        clock, mono = self._stable_clock()
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"WSL_INTEROP": "1"}), patch("gah.operations.platform.system", return_value="Windows"), patch("gah.operations.subprocess.run", side_effect=fake_run):
            result, payload = run_doctor(
                directory, "bootstrap", request_id="wsl-observation",
                clock=clock, monotonic_clock=mono,
                docker_path="docker", wsl_path=str(Path(directory) / "wsl.exe"),
                disk_usage=lambda _: type("Usage", (), {"free": 2**40})(),
                capacity_profile=self._capacity(2**40),
            )
        self.assertEqual(result["operation_status"], "INCOMPLETE")
        wsl_check = next(x for x in payload["checks"] if x["check_id"] == "wsl2")
        self.assertEqual(wsl_check["status"], "UNKNOWN")
        self.assertEqual(wsl_check["reason_code"], "OBSERVATION_MISSING")
        self.assertTrue(any(item[-1] == "--version" for item in calls))
        self.assertFalse("WSL_INTEROP" in payload["observations"]["wsl2"])

    def test_wsl_probe_accepts_localized_utf16_metadata_only_with_docker_wsl2_kernel(self):
        output = "WSL バージョン: 2.1.5\r\nカーネル バージョン: 5.15.90.1\r\n"
        def fake_run(argv, **kwargs):
            self.assertEqual(argv, ["wsl.exe", "--version"])
            self.assertEqual(kwargs["timeout"], 5)
            return subprocess.CompletedProcess(
                argv, 0, stdout=output.encode("utf-16"), stderr=b"")
        with patch("gah.operations.subprocess.run", side_effect=fake_run):
            observed = _wsl_probe(
                "wsl.exe",
                docker_kernel_version="5.15.90.1-microsoft-standard-WSL2",
            )
        self.assertEqual(observed["status"], "PASS")
        self.assertEqual(observed["wsl_version"], "2.1.5")
        self.assertEqual(observed["kernel_version"], "5.15.90.1")
        self.assertTrue(observed["docker_wsl2_backend_observed"])


    def test_doctor_ready_checks_real_baseline_series_and_ref(self):
        baseline_value = {
            "baseline_id": "baseline-ready-v2",
            "baseline_series_id": "baseline-series-ready-v2",
            "created_at": 1_700_000_000,
            "profile": "sample-ci",
        }
        baseline_ref = content_ref(
            "baseline", baseline_value["baseline_id"], baseline_value)
        contract = {
            "contract_id": "contract-ready-v2",
            "use_cases": ["UC-CI"],
            "comparison": {
                "mode": "required",
                "baseline_ref": baseline_ref,
                "changed_axes": [],
                "reason": None,
            },
        }

        class Authority:
            def __init__(self):
                self.actions = []

            def __call__(self, uid, request):
                self.actions.append((
                    uid, request["action"], request.get("series_id")))
                if request["action"] == "authority_diagnostics":
                    return {
                        "schema_version": 1,
                        "kind": "evaluation_authority_result",
                        "action": "authority_diagnostics",
                        "request_id": request["request_id"],
                        "ci_eligible": False,
                        "database_schema_version": 4,
                        "permission_generation": 7,
                        "extension_digest": "b" * 64,
                        "checked_at": 1_700_000_000,
                    }
                if request["action"] == "contract_current":
                    return {
                        "schema_version": 1,
                        "kind": "evaluation_authority_result",
                        "action": "contract_current",
                        "request_id": request["request_id"],
                        "ci_eligible": False,
                        "series_id": request["series_id"],
                        "adopted": True,
                        "valid": True,
                        "generation": 2,
                        "contract": contract,
                        "proposal_id": None,
                        "validation_id": None,
                    }
                if request["action"] == "baseline_current":
                    return {
                        "schema_version": 1,
                        "kind": "baseline_authority_result",
                        "action": "baseline_current",
                        "request_id": request["request_id"],
                        "ci_eligible": False,
                        "series_id": request["series_id"],
                        "generation": 1,
                        "adopted": True,
                        "baseline": baseline_value,
                        "valid": True,
                        "use": False,
                        "reason": None,
                        "adoption_verified": True,
                    }
                raise AssertionError(request["action"])

        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory) / "runtime"
            runtime.mkdir()
            (runtime / "deployment.json").write_text(json.dumps({
                "prefix": "runtime-ready-v2",
                "image_id": "sha256:" + "a" * 64,
                "containers": [],
            }), encoding="utf-8")
            (runtime / "runtime-metadata.json").write_text(json.dumps({
                "schema_version": 2,
                "kind": "runtime_metadata",
                "contract_series_id": "series-ready-v2",
                "baseline_series_id": "baseline-series-ready-v2",
                "profile": "sample-ci",
            }), encoding="utf-8")
            authority = Authority()
            clock, mono = self._stable_clock()
            with patch("gah.operations.subprocess.run",
                       side_effect=self._docker_success):
                result, payload = run_doctor(
                    directory, "ready", runtime="runtime", authority=authority,
                    request_id="doctor-ready-v2", clock=clock,
                    monotonic_clock=mono, docker_path="docker",
                    disk_usage=lambda _: type("Usage", (), {"free": 2**40})(),
                    capacity_profile=self._capacity(2**40),
                )

        self.assertEqual(result["operation_status"], "COMPLETED")
        self.assertIsNotNone(payload)
        assert payload is not None
        validate_doctor_result(payload)
        self.assertEqual(
            authority.actions,
            [
                (12004, "authority_diagnostics", None),
                (12004, "contract_current", "series-ready-v2"),
                (12004, "baseline_current", "baseline-series-ready-v2"),
            ],
        )
        self.assertEqual(
            next(x for x in payload["checks"]
                 if x["check_id"] == "binding")["status"],
            "PASS",
        )
        self.assertEqual(
            payload["observations"]["runtime"]["baseline_series_id"],
            "baseline-series-ready-v2",
        )
        self.assertEqual(
            payload["observations"]["authority"]["baseline"]["series_source"],
            "runtime_metadata",
        )
        self.assertTrue(
            payload["observations"]["authority"]["baseline"]["valid"])

    def test_doctor_ready_rejects_explicit_baseline_series_mismatch(self):
        baseline_value = {
            "baseline_id": "baseline-series-match",
            "baseline_series_id": "baseline-series-correct",
            "created_at": 1_700_000_000,
            "profile": "sample-ci",
        }
        contract = {
            "contract_id": "contract-series-mismatch",
            "use_cases": ["UC-CI"],
            "comparison": {
                "mode": "required",
                "baseline_ref": content_ref(
                    "baseline", baseline_value["baseline_id"], baseline_value),
                "changed_axes": [],
                "reason": None,
            },
        }

        class Authority:
            def __init__(self):
                self.actions = []

            def __call__(self, uid, request):
                self.actions.append((
                    uid, request["action"], request.get("series_id")))
                if request["action"] == "authority_diagnostics":
                    return {
                        "schema_version": 1,
                        "kind": "evaluation_authority_result",
                        "action": "authority_diagnostics",
                        "request_id": request["request_id"],
                        "ci_eligible": False,
                        "database_schema_version": 4,
                        "permission_generation": 7,
                        "extension_digest": "b" * 64,
                        "checked_at": 1_700_000_000,
                    }
                if request["action"] == "contract_current":
                    return {
                        "schema_version": 1,
                        "kind": "evaluation_authority_result",
                        "action": "contract_current",
                        "request_id": request["request_id"],
                        "ci_eligible": False,
                        "series_id": request["series_id"],
                        "adopted": True,
                        "valid": True,
                        "generation": 2,
                        "contract": contract,
                        "proposal_id": None,
                        "validation_id": None,
                    }
                if request["action"] == "baseline_current":
                    return {
                        "schema_version": 1,
                        "kind": "baseline_authority_result",
                        "action": "baseline_current",
                        "request_id": request["request_id"],
                        "ci_eligible": False,
                        "series_id": request["series_id"],
                        "adopted": False,
                        "use": False,
                        "valid": False,
                        "reason": "NOT_FOUND",
                    }
                raise AssertionError(request["action"])

        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory) / "runtime"
            runtime.mkdir()
            (runtime / "deployment.json").write_text(json.dumps({
                "prefix": "runtime-series-mismatch",
                "image_id": "sha256:" + "a" * 64,
                "containers": [],
            }), encoding="utf-8")
            (runtime / "runtime-metadata.json").write_text(json.dumps({
                "schema_version": 2,
                "kind": "runtime_metadata",
                "contract_series_id": "series-series-mismatch",
                "baseline_series_id": "baseline-series-correct",
                "profile": "sample-ci",
            }), encoding="utf-8")
            authority = Authority()
            clock, mono = self._stable_clock()
            with patch("gah.operations.subprocess.run",
                       side_effect=self._docker_success):
                result, payload = run_doctor(
                    directory, "ready", runtime="runtime", authority=authority,
                    request_id="doctor-series-mismatch", clock=clock,
                    monotonic_clock=mono, docker_path="docker",
                    baseline_series_id="baseline-series-other",
                    disk_usage=lambda _: type("Usage", (), {"free": 2**40})(),
                    capacity_profile=self._capacity(2**40),
                )

        self.assertEqual(result["operation_status"], "REJECTED")
        self.assertIn("BINDING_MISMATCH", result["reasons"])
        self.assertIsNotNone(payload)
        assert payload is not None
        validate_doctor_result(payload)
        self.assertEqual(
            [item for item in authority.actions if item[1] == "baseline_current"],
            [(12004, "baseline_current", "baseline-series-other")],
        )
        binding = next(x for x in payload["checks"]
                       if x["check_id"] == "binding")
        self.assertEqual(binding["status"], "FAIL")
        self.assertEqual(binding["reason_code"], "BINDING_MISMATCH")
        self.assertEqual(
            payload["observations"]["authority"]["baseline"]["baseline_series_id"],
            "baseline-series-other",
        )

    def test_doctor_ready_rejects_baseline_ref_mismatch(self):
        actual_baseline = {
            "baseline_id": "baseline-actual",
            "baseline_series_id": "baseline-series-ref",
            "created_at": 1_700_000_000,
            "profile": "sample-ci",
        }
        wrong_baseline = {
            "baseline_id": "baseline-other",
            "baseline_series_id": "baseline-series-ref",
            "created_at": 1_700_000_000,
            "profile": "sample-ci",
        }
        contract = {
            "contract_id": "contract-ref-mismatch",
            "use_cases": ["UC-CI"],
            "comparison": {
                "mode": "required",
                "baseline_ref": content_ref(
                    "baseline", wrong_baseline["baseline_id"], wrong_baseline),
                "changed_axes": [],
                "reason": None,
            },
        }

        class Authority:
            def __init__(self):
                self.actions = []

            def __call__(self, uid, request):
                self.actions.append((
                    uid, request["action"], request.get("series_id")))
                if request["action"] == "authority_diagnostics":
                    return {
                        "schema_version": 1,
                        "kind": "evaluation_authority_result",
                        "action": "authority_diagnostics",
                        "request_id": request["request_id"],
                        "ci_eligible": False,
                        "database_schema_version": 4,
                        "permission_generation": 7,
                        "extension_digest": "b" * 64,
                        "checked_at": 1_700_000_000,
                    }
                if request["action"] == "contract_current":
                    return {
                        "schema_version": 1,
                        "kind": "evaluation_authority_result",
                        "action": "contract_current",
                        "request_id": request["request_id"],
                        "ci_eligible": False,
                        "series_id": request["series_id"],
                        "adopted": True,
                        "valid": True,
                        "generation": 2,
                        "contract": contract,
                        "proposal_id": None,
                        "validation_id": None,
                    }
                if request["action"] == "baseline_current":
                    return {
                        "schema_version": 1,
                        "kind": "baseline_authority_result",
                        "action": "baseline_current",
                        "request_id": request["request_id"],
                        "ci_eligible": False,
                        "series_id": request["series_id"],
                        "generation": 1,
                        "adopted": True,
                        "baseline": actual_baseline,
                        "valid": True,
                        "use": False,
                        "reason": None,
                        "adoption_verified": True,
                    }
                raise AssertionError(request["action"])

        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory) / "runtime"
            runtime.mkdir()
            (runtime / "deployment.json").write_text(json.dumps({
                "prefix": "runtime-ref-mismatch",
                "image_id": "sha256:" + "a" * 64,
                "containers": [],
            }), encoding="utf-8")
            (runtime / "runtime-metadata.json").write_text(json.dumps({
                "schema_version": 2,
                "kind": "runtime_metadata",
                "contract_series_id": "series-ref-mismatch",
                "baseline_series_id": "baseline-series-ref",
                "profile": "sample-ci",
            }), encoding="utf-8")
            authority = Authority()
            clock, mono = self._stable_clock()
            with patch("gah.operations.subprocess.run",
                       side_effect=self._docker_success):
                result, payload = run_doctor(
                    directory, "ready", runtime="runtime", authority=authority,
                    request_id="doctor-ref-mismatch", clock=clock,
                    monotonic_clock=mono, docker_path="docker",
                    baseline_series_id="baseline-series-ref",
                    disk_usage=lambda _: type("Usage", (), {"free": 2**40})(),
                    capacity_profile=self._capacity(2**40),
                )

        self.assertEqual(result["operation_status"], "REJECTED")
        self.assertIn("BINDING_MISMATCH", result["reasons"])
        self.assertIsNotNone(payload)
        assert payload is not None
        validate_doctor_result(payload)
        self.assertEqual(
            [item for item in authority.actions if item[1] == "baseline_current"],
            [(12004, "baseline_current", "baseline-series-ref")],
        )
        binding = next(x for x in payload["checks"]
                       if x["check_id"] == "binding")
        self.assertEqual(binding["status"], "FAIL")
        self.assertEqual(binding["reason_code"], "BINDING_MISMATCH")
        self.assertFalse(
            payload["observations"]["authority"]["baseline"]["valid"])

    def test_doctor_ready_uses_runtime_metadata_and_authority_diagnostics(self):
        class Authority:
            def __init__(self):
                self.actions = []
            def __call__(self, uid, request):
                self.actions.append((uid, request["action"]))
                if request["action"] == "authority_diagnostics":
                    return {"schema_version": 1, "kind": "evaluation_authority_result",
                            "action": request["action"], "request_id": request["request_id"],
                            "ci_eligible": False, "database_schema_version": 4,
                            "permission_generation": 7, "extension_digest": "b" * 64,
                            "checked_at": 1_700_000_000}
                if request["action"] == "contract_current":
                    return {"schema_version": 1, "kind": "evaluation_authority_result",
                            "action": request["action"], "request_id": request["request_id"],
                            "ci_eligible": False, "series_id": request["series_id"],
                            "adopted": True, "valid": True, "generation": 3,
                            "contract": {"contract_id": "contract-ready", "use_cases": ["UC-CI"]},
                            "proposal_id": None, "validation_id": None}
                raise AssertionError(request["action"])
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory) / "runtime"
            runtime.mkdir()
            (runtime / "deployment.json").write_text(json.dumps({
                "prefix": "runtime-ready", "image_id": "sha256:" + "a" * 64, "containers": [],
            }), encoding="utf-8")
            (runtime / "runtime-metadata.json").write_text(json.dumps({
                "schema_version": 1, "kind": "runtime_metadata",
                "contract_series_id": "series-ready", "profile": "sample-ci",
            }), encoding="utf-8")
            authority = Authority()
            clock, mono = self._stable_clock()
            with patch("gah.operations.subprocess.run", side_effect=self._docker_success):
                result, payload = run_doctor(
                    directory, "ready", runtime="runtime", authority=authority,
                    request_id="doctor-ready", clock=clock,
                    monotonic_clock=mono, docker_path="docker",
                    disk_usage=lambda _: type("Usage", (), {"free": 2**40})(),
                    capacity_profile=self._capacity(2**40),
                )
        self.assertEqual(result["operation_status"], "COMPLETED")
        self.assertEqual([x for x in authority.actions],
                         [(12004, "authority_diagnostics"), (12004, "contract_current")])
        self.assertEqual(next(x for x in payload["checks"] if x["check_id"] == "database")["status"], "PASS")
        self.assertEqual(next(x for x in payload["checks"] if x["check_id"] == "authority")["status"], "PASS")
        self.assertEqual(next(x for x in payload["checks"] if x["check_id"] == "binding")["status"], "PASS")
        self.assertEqual(payload["observations"]["database"]["database_schema_version"], 4)

    def test_doctor_ready_malformed_authority_still_emits_all_thirteen_checks(self):
        def malformed(_uid, request):
            return {"schema_version": 1, "kind": "evaluation_authority_result",
                    "action": request["action"], "request_id": request["request_id"],
                    "ci_eligible": False}
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory) / "runtime"
            runtime.mkdir()
            (runtime / "deployment.json").write_text(json.dumps({
                "prefix": "runtime-malformed", "image_id": "sha256:" + "a" * 64, "containers": [],
            }), encoding="utf-8")
            (runtime / "runtime-metadata.json").write_text(json.dumps({
                "schema_version": 1, "kind": "runtime_metadata",
                "contract_series_id": "series-malformed", "profile": "sample-ci",
            }), encoding="utf-8")
            with patch("gah.operations.subprocess.run", side_effect=self._docker_success):
                result, payload = run_doctor(
                    directory, "ready", runtime="runtime", authority=malformed,
                    request_id="doctor-malformed", clock=lambda: 1_700_000_000,
                    monotonic_clock=lambda: 10, docker_path="docker",
                    disk_usage=lambda _: type("Usage", (), {"free": 2**40})(),
                    capacity_profile=self._capacity(2**40),
                )
        self.assertEqual(result["operation_status"], "REJECTED")
        self.assertIn("BINDING_MISMATCH", result["reasons"])
        self.assertEqual(len(payload["checks"]), 13)
        self.assertEqual(len({x["check_id"] for x in payload["checks"]}), 13)
        self.assertEqual(next(x for x in payload["checks"] if x["check_id"] == "database")["status"], "FAIL")

    def test_setup_preview_persists_all_refs_for_both_sample_profiles(self):
        for profile in ("sample-ci", "sample-llm"):
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "plan.json"
                result, plan = run_setup_preview(
                    directory, profile, output, request_id="preview-" + profile,
                    clock=lambda: 1_700_000_000)
                self.assertEqual(result["operation_status"], "COMPLETED")
                assert plan is not None
                refs = [plan["source_ref"], plan["requirements_ref"]]
                refs.extend(plan["payload"]["image_lock_refs"])
                refs.extend(plan["payload"][name] for name in (
                    "config_ref", "role_recipe_ref", "resource_profile_ref",
                    "sample_contract_ref", "sample_case_set_ref", "evaluator_ref"))
                for ref in refs:
                    resolved = resolve_setup_ref(directory, ref)
                    if resolved.get("kind") == "setup_ref_artifact":
                        self.assertEqual(resolved["ref"], ref)
                    else:
                        from gah.productization import content_ref
                        self.assertEqual(content_ref(ref["kind"], ref["id"], resolved), ref)
                evaluator = resolve_setup_ref(directory, plan["payload"]["evaluator_ref"])
                if profile == "sample-ci":
                    self.assertIsNotNone(evaluator["resolver"])
                else:
                    self.assertEqual(evaluator.get("evaluator_id"),
                                     "finite-llm-evaluator")

    def test_preview_keeps_old_digest_ref_after_source_update_and_new_output(self):
        with tempfile.TemporaryDirectory() as directory:
            first_result, first_plan = run_setup_preview(
                directory, "sample-ci", "plan-v1.json",
                request_id="source-v1", clock=lambda: 1_700_000_000)
            self.assertEqual(first_result["operation_status"], "COMPLETED")
            assert first_plan is not None
            old_source = resolve_setup_ref(directory, first_plan["source_ref"])
            changed_source = json.loads(json.dumps(old_source))
            changed_source["files"].append({
                "path": "synthetic-source-update.py",
                "digest": "c" * 64,
                "size_bytes": 1,
            })
            changed_ref = content_ref(
                "snapshot_manifest", first_plan["source_ref"]["id"],
                changed_source)

            def updated_source(base):
                _persist_setup_ref(Path(base), changed_ref, changed_source)
                return changed_ref

            with patch("gah.operations._setup_source_ref",
                       side_effect=updated_source):
                second_result, second_plan = run_setup_preview(
                    directory, "sample-ci", "plan-v2.json",
                    request_id="source-v2", clock=lambda: 1_700_000_000)
            self.assertEqual(second_result["operation_status"], "COMPLETED")
            assert second_plan is not None
            self.assertNotEqual(first_plan["source_ref"]["digest"],
                                second_plan["source_ref"]["digest"])
            self.assertEqual(resolve_setup_ref(
                directory, first_plan["source_ref"]), old_source)
            self.assertEqual(resolve_setup_ref(
                directory, second_plan["source_ref"]), changed_source)
            self.assertTrue(
                (Path(directory) / ".ga" / "operations" / "refs"
                 / "snapshot_manifest" / first_plan["source_ref"]["id"]
                 / (first_plan["source_ref"]["digest"] + ".json")).is_file())
            self.assertTrue(
                (Path(directory) / ".ga" / "operations" / "refs"
                 / "snapshot_manifest" / second_plan["source_ref"]["id"]
                 / (second_plan["source_ref"]["digest"] + ".json")).is_file())


    def test_cli_migrate_preview_forwards_omitted_request_id(self):
        from tools import gah_ops
        args = gah_ops._parser().parse_args([
            "migrate", "preview", "--workspace", "workspace",
            "--database", "relative.sqlite", "--output", "plan.json",
        ])
        delegated = operation_result(
            "ops.migrate.preview", "migration-preview-generated",
            "COMPLETED", checked_at=1_700_000_000,
        )
        with patch(
                "gah.operations_migration.preview",
                return_value=(delegated, {"ignored": "artifact"})) as preview:
            result = gah_ops._run_migration_cli(args)
        self.assertEqual(result, delegated)
        preview.assert_called_once_with(
            "workspace", "relative.sqlite", "plan.json", request_id=None,
        )

    def test_cli_migrate_apply_forwards_explicit_request_id(self):
        from tools import gah_ops
        args = gah_ops._parser().parse_args([
            "migrate", "apply", "--workspace", "workspace",
            "--database", "relative.sqlite", "--plan", "plan.json",
            "--request-id", "migration-apply-cli",
        ])
        delegated = operation_result(
            "ops.migrate.apply", "migration-apply-cli",
            "REJECTED", reasons=("SCHEMA_UNSUPPORTED",),
            checked_at=1_700_000_000,
        )
        with patch(
                "gah.operations_migration.apply",
                return_value=(delegated, None)) as apply:
            result = gah_ops._run_migration_cli(args)
        self.assertEqual(result, delegated)
        apply.assert_called_once_with(
            "workspace", "relative.sqlite", "plan.json",
            request_id="migration-apply-cli",
        )

    def test_cli_migrate_rejects_invalid_explicit_request_id(self):
        from tools import gah_ops
        args = gah_ops._parser().parse_args([
            "migrate", "preview", "--workspace", "workspace",
            "--database", "relative.sqlite", "--output", "plan.json",
            "--request-id", "invalid/id",
        ])
        with patch("gah.operations_migration.preview") as preview:
            result = gah_ops._run_migration_cli(args)
        self.assertEqual(result["operation_status"], "REJECTED")
        self.assertEqual(result["exit_code"], 1)
        self.assertIsNone(result["request_id"])
        self.assertEqual(result["reasons"], ["INVALID_INPUT"])
        preview.assert_not_called()

    def test_cli_migrate_maps_module_error_to_incomplete_result(self):
        from tools import gah_ops
        args = gah_ops._parser().parse_args([
            "migrate", "preview", "--workspace", "workspace",
            "--database", "relative.sqlite", "--output", "plan.json",
            "--request-id", "migration-module-error",
        ])
        with patch(
                "gah.operations_migration.preview",
                side_effect=RuntimeError("module failure")):
            result = gah_ops._run_migration_cli(args)
        self.assertEqual(result["operation_status"], "INCOMPLETE")
        self.assertEqual(result["exit_code"], 2)
        self.assertEqual(result["request_id"], "migration-module-error")
        self.assertEqual(result["reasons"], ["OPERATION_UNKNOWN"])

    def test_doctor_validator_rejects_boolean_schema_version(self):
        with self.assertRaises(Exception):
            validate_doctor_result({"schema_version": True})

if __name__ == "__main__":
    unittest.main()
