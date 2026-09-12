"""Synthetic LLM runnerの固定境界テスト。実ネットワークは使わない。"""

from __future__ import annotations

import copy
import hashlib
import io
import json
import sys
import tempfile
from pathlib import Path
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gah.evaluation_data import build_pack
from gah.contracts import ContractError
from gah.llm_evaluator import MODEL
from tools import synthetic_llm_provider as provider
from tools import verify_synthetic_llm as verifier


def response(selection: dict[str, str]) -> bytes:
    return json.dumps({
        "model": MODEL,
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        "choices": [{
            "index": 0,
            "finish_reason": "stop",
            "message": {"role": "assistant", "content": json.dumps(selection), "tool_calls": None, "function_call": None, "refusal": None},
        }],
    }, separators=(",", ":")).encode()


def isolated_calibration_stub(pack):
    """予算・並行制御だけを切り出すunit fixture。校正の合格根拠には使わない。"""
    return {"evaluator_calibration_passed": True,
            "pack_digest": hashlib.sha256(verifier._canonical(pack)).hexdigest(),
            "evaluator_profile_ref": {"kind": "evaluator_profile", "id": "test-only", "digest": "a" * 64},
            "ci_eligible": False}


class FakeResponse:
    status = 200

    def __init__(self, payload: bytes):
        self.payload = payload

    def read(self, limit: int) -> bytes:
        return self.payload[:limit]


class FakeConnection:
    instances = []

    def __init__(self, host, port, timeout):
        self.host, self.port, self.timeout = host, port, timeout
        self.requests = []
        self.response = FakeResponse(response({"detection": "detect", "action": "block"}))
        self.closed = False
        self.__class__.instances.append(self)

    def request(self, method, path, body, headers):
        self.requests.append((method, path, body, headers))

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


class FakeProcess:
    def __init__(self, payload: bytes = b"{}", returncode: int = 0, *, timeout: bool = False):
        self.stdout = io.BytesIO(payload)
        self.returncode = returncode
        self.timeout = timeout
        self.killed = False

    def wait(self, timeout=None):
        if self.timeout and not self.killed:
            raise verifier.subprocess.TimeoutExpired("fake", timeout)
        return self.returncode

    def kill(self):
        self.killed = True


class SyntheticLLMRunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pack = build_pack()

    def test_provider_uses_only_fixed_endpoint_and_bounded_response(self):
        FakeConnection.instances.clear()
        with mock.patch.object(provider.http.client, "HTTPConnection", FakeConnection):
            payload = provider._post({"model": MODEL, "messages": []})
        self.assertEqual(payload, response({"detection": "detect", "action": "block"}))
        connection = FakeConnection.instances[0]
        self.assertEqual((connection.host, connection.port, connection.timeout), ("127.0.0.1", 18000, 30))
        self.assertEqual(connection.requests[0][0:2], ("POST", "/v1/chat/completions"))
        self.assertTrue(connection.closed)

    def test_provider_sends_one_request_per_stage_and_returns_no_raw(self):
        case = self.pack["case_sets"]["acceptance"]["cases"][0]
        with mock.patch.object(provider, "_post", return_value=response({"detection": "detect", "action": "block"})) as post:
            result = provider.run_case("acceptance", case["case_id"])
        self.assertTrue(result["complete"])
        self.assertEqual(post.call_count, 1)
        encoded = json.dumps(result, sort_keys=True)
        self.assertNotIn('"content"', encoded)

    def test_provider_does_not_send_later_stage_after_invalid_first_response(self):
        case = self.pack["case_sets"]["acceptance"]["cases"][2]
        with mock.patch.object(provider, "_post", side_effect=[b"not-json", response({"detection": "allow", "action": "apply"})]) as post:
            result = provider.run_case("acceptance", case["case_id"])
        self.assertFalse(result["complete"])
        self.assertTrue(result["failed"])
        self.assertEqual(len(result["records"]), 1)
        post.assert_called_once()

    def test_child_timeout_is_recorded_without_retry(self):
        case = self.pack["case_sets"]["calibration"]["cases"][0]
        with mock.patch.object(verifier, "_wait_child", return_value={"status": "TIMEOUT", "reason_code": "CASE_TIMEOUT", "stdout": b"", "returncode": None, "remote_stop_unknown": True}) as wait:
            result = verifier.run_child_case(self.pack, "calibration", case)
        self.assertEqual(result["status"], "TIMEOUT")
        self.assertTrue(result["remote_stop_unknown"])
        wait.assert_called_once()
        self.assertNotIn("raw", result)

    def test_supervisor_accepts_only_complete_structured_child_record(self):
        case = self.pack["case_sets"]["calibration"]["cases"][0]
        with mock.patch.object(provider, "_post", return_value=response({"detection": "detect", "action": "block"})):
            child_result = provider.run_case("calibration", case["case_id"])
        raw = json.dumps(child_result, ensure_ascii=False, sort_keys=True).encode()
        pack_digest = verifier.hashlib.sha256(verifier._canonical(self.pack)).hexdigest()
        parsed = verifier._parse_child_output(raw, "calibration", case, pack_digest)
        self.assertTrue(parsed["complete"])
        self.assertEqual(len(parsed["records"]), 1)

    def test_malformed_and_oversized_child_output_is_not_saved(self):
        case = self.pack["case_sets"]["calibration"]["cases"][0]
        for child in (
            {"status": "EXITED", "reason_code": None, "stdout": b"not-json", "returncode": 0, "remote_stop_unknown": False},
            {"status": "ERROR", "reason_code": "CHILD_OUTPUT_SIZE", "stdout": b"", "returncode": 0, "remote_stop_unknown": False},
        ):
            with self.subTest(child=child["reason_code"]):
                with mock.patch.object(verifier, "_wait_child", return_value=child):
                    result = verifier.run_child_case(self.pack, "calibration", case)
                self.assertEqual(result["status"], "ERROR")
                self.assertNotIn("not-json", json.dumps(result))
                self.assertNotIn("raw", result)

    def test_monotonic_deadline_prevents_dispatch_despite_wall_clock_rollback(self):
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary) / "run"
            with mock.patch.object(verifier.time, "time", side_effect=[1000, 1]), \
                 mock.patch.object(verifier.time, "monotonic", side_effect=[0, verifier.RUN_TIMEOUT_SECONDS + 1]), \
                 mock.patch.object(verifier, "_case_result") as child:
                result = verifier.run_verification("calibration", out)
            child.assert_not_called()
            self.assertEqual(result["status"], "INCOMPLETE")
            self.assertEqual(result["stop_reason"], "RUN_TIMEOUT")
            self.assertEqual(result["reserved_tokens_remaining"], 0)

    def test_existing_output_directory_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ContractError, "OUTPUT_EXISTS"):
                verifier.run_verification("calibration", directory)

    def test_acceptance_is_held_before_launch_when_reserved_token_budget_is_exceeded(self):
        with tempfile.TemporaryDirectory() as directory:
            small_pack = copy.deepcopy(self.pack)
            small_pack["case_sets"]["acceptance"]["cases"] = small_pack["case_sets"]["acceptance"]["cases"][:5]
            def complete(_purpose, case, _pack):
                stage = case["session_steps"][0]
                detection = stage["expected_detection"]
                action = {"detect": "block", "allow": "apply", "indeterminate": "defer"}[detection]
                return {
                    "case_id": case["case_id"], "purpose": "acceptance", "pack_digest": "x",
                    "status": "COMPLETE", "reason_code": None, "records": [{
                        "stage_id": stage["stage_id"], "status": "COMPLETE",
                        "selection": {"detection": detection, "action": action},
                        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                    }], "complete": True, "failed": False, "remote_stop_unknown": False,
                    "expected_stage_count": 1, "expected_label": case["expected_label"], "ci_eligible": False,
                }
            with mock.patch.object(verifier, "_load_pack", return_value=small_pack), \
                 mock.patch.object(verifier, "calibrate_measurement", side_effect=isolated_calibration_stub), \
                 mock.patch.object(verifier, "_case_result", side_effect=complete) as child:
                result = verifier.run_verification("acceptance", str(Path(directory) / "new-run"))
            self.assertEqual(result["status"], "COMPLETE")
            self.assertEqual(child.call_count, 5)
            files = {path.name for path in (Path(directory) / "new-run").iterdir()}
            self.assertEqual(files, {"records.json", "summary.json", "manifest.json", "case-records", "evaluator-calibration.json"})
            self.assertEqual(len(list((Path(directory) / "new-run" / "case-records").glob("*.json"))), 5)

    def test_record_shape_rejects_untrusted_nested_content_and_counter_drift(self):
        case = self.pack["case_sets"]["calibration"]["cases"][0]
        with mock.patch.object(provider, "_post", return_value=response({"detection": "detect", "action": "block"})):
            child_result = provider.run_case("calibration", case["case_id"])
        digest = verifier.hashlib.sha256(verifier._canonical(self.pack)).hexdigest()
        record = child_result["records"][0]
        for mutation in (
            lambda r: r["effect"].update({"untrusted": "raw"}),
            lambda r: r["effect"].update({"counter_before": 9}),
            lambda r: r["observations"].update({"detection": "allow"}),
        ):
            candidate = copy.deepcopy(child_result)
            mutation(candidate["records"][0])
            with self.subTest(mutation=mutation):
                with self.assertRaises(ContractError):
                    verifier._parse_child_output(
                        json.dumps(candidate, ensure_ascii=False, sort_keys=True).encode(),
                        "calibration", case, digest,
                    )

    def test_complete_requires_usage_and_checks_effect_action_only(self):
        case = self.pack["case_sets"]["calibration"]["cases"][0]
        with mock.patch.object(provider, "_post", return_value=response({"detection": "detect", "action": "block"})):
            child_result = provider.run_case("calibration", case["case_id"])
        digest = verifier.hashlib.sha256(verifier._canonical(self.pack)).hexdigest()
        candidate = copy.deepcopy(child_result)
        candidate["records"][0]["usage"] = None
        with self.assertRaises(ContractError):
            verifier._parse_child_output(
                json.dumps(candidate, ensure_ascii=False, sort_keys=True).encode(),
                "calibration", case, digest,
            )

        candidate = copy.deepcopy(child_result)
        candidate["records"][0]["selection"]["action"] = "apply"
        candidate["records"][0]["effect"] = {
            "counter_before": 0, "counter_after": 1, "applied": True,
        }
        parsed = verifier._parse_child_output(
            json.dumps(candidate, ensure_ascii=False, sort_keys=True).encode(),
            "calibration", case, digest,
        )
        self.assertTrue(parsed["complete"])

    def test_provider_timeout_keeps_remote_stop_unknown(self):
        case = self.pack["case_sets"]["calibration"]["cases"][0]
        with mock.patch.object(provider, "_post", side_effect=ContractError("PROVIDER_TIMEOUT")):
            result = provider.run_case("calibration", case["case_id"])
        self.assertFalse(result["complete"])
        self.assertTrue(result["remote_stop_unknown"])
        self.assertEqual(len(result["records"]), 1)

    def test_supervisor_keeps_timeout_exposure_from_structured_nonzero_child(self):
        case = self.pack["case_sets"]["calibration"]["cases"][0]
        with mock.patch.object(provider, "_post", side_effect=ContractError("PROVIDER_TIMEOUT")):
            child_result = provider.run_case("calibration", case["case_id"])
        with mock.patch.object(verifier, "_wait_child", return_value={
            "status": "EXITED", "reason_code": None,
            "stdout": json.dumps(child_result, sort_keys=True).encode(),
            "returncode": 1, "remote_stop_unknown": False,
        }):
            result = verifier.run_child_case(self.pack, "calibration", case)
        self.assertFalse(result["complete"])
        self.assertTrue(result["remote_stop_unknown"])

    def test_running_reservation_stops_new_submissions_after_bad_child(self):
        small_pack = copy.deepcopy(self.pack)
        small_pack["case_sets"]["calibration"]["cases"] = small_pack["case_sets"]["calibration"]["cases"][:5]
        submitted = []

        def child(_purpose, case, _pack):
            submitted.append(case["case_id"])
            if len(submitted) == 1:
                return verifier._expected_case_record("calibration", case, "digest")
            stage = case["session_steps"][0]
            detection = stage["expected_detection"]
            action = {"detect": "block", "allow": "apply", "indeterminate": "defer"}[detection]
            return {
                "case_id": case["case_id"], "complete": True, "failed": False,
                "status": "COMPLETE", "records": [{
                    "stage_id": stage["stage_id"], "status": "COMPLETE",
                    "selection": {"detection": detection, "action": action},
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                }], "remote_stop_unknown": False,
                "expected_stage_count": 1, "expected_label": case["expected_label"],
            }

        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(verifier, "_load_pack", return_value=small_pack), \
                 mock.patch.object(verifier, "calibrate_measurement", side_effect=isolated_calibration_stub), \
                 mock.patch.object(verifier, "_case_result", side_effect=child):
                result = verifier.run_verification("calibration", str(Path(directory) / "run"))
        self.assertEqual(result["status"], "INCOMPLETE")
        self.assertLessEqual(len(submitted), 4)
        self.assertEqual(result["ci_eligible"], False)

    def test_indeterminate_observation_has_separate_metric_and_calibration_flag(self):
        case = self.pack["case_sets"]["acceptance"]["cases"][0]
        stage = case["session_steps"][0]
        result = {
            "case_id": case["case_id"], "complete": True, "status": "COMPLETE",
            "records": [{
                "stage_id": stage["stage_id"], "status": "COMPLETE",
                "selection": {"detection": "indeterminate", "action": "defer"},
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }],
        }
        summary = verifier._aggregate("acceptance", [case], [result], "digest")
        self.assertEqual(summary["metrics"]["FN"], 0)
        self.assertEqual(summary["metrics"]["FP"], 0)
        self.assertEqual(summary["metrics"]["indeterminate_mismatch"], 1)
        self.assertIsNone(summary["calibration_passed"])
        self.assertFalse(summary["target_agreement_passed"])

    def test_failed_or_wrong_pack_evaluator_calibration_prevents_every_target_call(self):
        for change in ({"evaluator_calibration_passed": False}, {"pack_digest": "b" * 64}):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                failed = {**isolated_calibration_stub(self.pack), **change}
                out = Path(directory) / "run"
                with mock.patch.object(verifier, "calibrate_measurement", return_value=failed), \
                     mock.patch.object(verifier, "_case_result") as child:
                    summary = verifier.run_verification("acceptance", out)
                child.assert_not_called()
                self.assertEqual(summary["status"], "HOLD")
                self.assertEqual(summary["reason_code"], "EVALUATOR_CALIBRATION_FAILED")
                self.assertEqual(summary["model_calls"], 0)
                self.assertEqual(json.loads((out / "evaluator-calibration.json").read_text()), failed)
                self.assertFalse(summary["ci_eligible"])

    def test_invalid_purpose_is_rejected_before_creating_output(self):
        for invalid in (None, [], {}, True, 1, "unknown"):
            with self.subTest(invalid=invalid), tempfile.TemporaryDirectory() as directory:
                out = Path(directory) / "run"
                with self.assertRaises(ContractError):
                    verifier.run_verification(invalid, out)
                self.assertFalse(out.exists())

    def test_target_errors_do_not_invalidate_a_calibrated_measurement(self):
        def controlled_child(argv):
            purpose = argv[argv.index("--purpose") + 1]
            case_id = argv[argv.index("--case-id") + 1]
            payload = provider.run_case(purpose, case_id)
            return {"status": "EXITED", "stdout": json.dumps(payload).encode(),
                    "returncode": 0, "reason_code": None}

        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(provider, "_post", return_value=response({"detection": "allow", "action": "apply"})), \
                 mock.patch.object(verifier, "_wait_child", side_effect=controlled_child):
                summary = verifier.run_verification("calibration", Path(directory) / "run")
            self.assertEqual(summary["status"], "COMPLETE")
            self.assertEqual(summary["complete_case_count"], 18)
            self.assertEqual(summary["metrics"]["FN"], 6)
            self.assertEqual(summary["metrics"]["indeterminate_mismatch"], 6)
            self.assertTrue(summary["evaluator_calibration_passed"])
            self.assertFalse(summary["target_agreement_passed"])
            self.assertFalse(summary["calibration_passed"])
            self.assertFalse(summary["ci_eligible"])

    def test_fake_process_reader_enforces_child_stdout_limit_and_timeout(self):
        with mock.patch.object(verifier.subprocess, "Popen", return_value=FakeProcess(b"x" * (verifier.MAX_CHILD_STDOUT + 1))):
            oversized = verifier._wait_child(["fake"])
        self.assertEqual(oversized["reason_code"], "CHILD_OUTPUT_SIZE")
        with mock.patch.object(verifier.subprocess, "Popen", return_value=FakeProcess(timeout=True)):
            timeout = verifier._wait_child(["fake"])
        self.assertEqual(timeout["reason_code"], "CASE_TIMEOUT")
        self.assertTrue(timeout["remote_stop_unknown"])


if __name__ == "__main__":
    unittest.main()
