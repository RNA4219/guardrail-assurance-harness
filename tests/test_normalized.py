"""NormalizedResultのbinding、実行状態、各modeの境界を検査する。"""

import hashlib
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gah.contracts import ContractError
from gah.normalized import normalize_generic, validate_binding


def binding() -> dict[str, object]:
    return {
        "run_id": "run-1",
        "operation_id": "operation-1",
        "owner_epoch": 3,
        "contract_digest": "a" * 64,
        "target_digest": "b" * 64,
        "obligation_id": "obligation-1",
        "case_id": "case-1",
        "trial_id": "trial-1",
        "stage_id": "stage-1",
        "fixture_digest": "c" * 64,
        "adapter_digest": "d" * 64,
        "policy_digest": "e" * 64,
        "evaluator_digest": "f" * 64,
        "isolation_digest": "0" * 64,
    }


def raw(expected: dict, mode: str, observations: dict) -> bytes:
    return json.dumps(
        {
            "schema_version": 1,
            "kind": "gah_generic_result",
            "binding": expected,
            "mode": mode,
            "observations": observations,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


class NormalizedTests(unittest.TestCase):
    def test_bom_is_rejected_at_either_end(self):
        current = binding()
        document = raw(current, "constraint", {"check": "PASS"})
        for value in (b"\xef\xbb\xbf" + document, document + b"\xef\xbb\xbf"):
            with self.assertRaises(ContractError):
                normalize_generic(value, current, execution_status="COMPLETED", exit_code=0, stop_confirmed=True)

    def call(self, payload: bytes, **kwargs):
        arguments = {
            "execution_status": "COMPLETED",
            "exit_code": 0,
            "stop_confirmed": True,
        }
        arguments.update(kwargs)
        return normalize_generic(payload, binding(), **arguments)

    def test_binding_is_strict_and_independent(self):
        value = validate_binding(binding())
        self.assertEqual(value, binding())
        self.assertIsNot(value, binding())
        value["run_id"] = "changed"
        self.assertEqual(binding()["run_id"], "run-1")
        for field, bad in (("owner_epoch", True), ("owner_epoch", 0), ("run_id", ""), ("target_digest", "A" * 64)):
            invalid = binding()
            invalid[field] = bad
            with self.subTest(field=field, bad=bad), self.assertRaises(ContractError):
                validate_binding(invalid)

    def test_constraint_result_does_not_adopt_mutation_or_ci_claim(self):
        payload = raw(binding(), "constraint", {"check": "FAIL"})
        result = self.call(payload)
        self.assertEqual(result["mode"], "constraint")
        self.assertEqual(result["observation"], "FAIL")
        self.assertIsNone(result["mutation_outcome"])
        self.assertIsNone(result["detection"])
        self.assertEqual(result["raw_digest"], hashlib.sha256(payload).hexdigest())

    def test_mutation_outcomes_preserve_baseline_and_distinguish_coverage(self):
        cases = (
            ({"baseline": "PASS", "mutation_applied": True, "reached": True, "detected": True, "unrelated_failure": False}, "KILLED"),
            ({"baseline": "PASS", "mutation_applied": True, "reached": True, "detected": False, "unrelated_failure": False}, "SURVIVED"),
            ({"baseline": "PASS", "mutation_applied": True, "reached": False, "detected": False, "unrelated_failure": False}, "NO_COVERAGE"),
            ({"baseline": "FAIL", "mutation_applied": True, "reached": True, "detected": True, "unrelated_failure": False}, "ERROR"),
            ({"baseline": "PASS", "mutation_applied": False, "reached": False, "detected": False, "unrelated_failure": False}, "ERROR"),
            ({"baseline": "PASS", "mutation_applied": True, "reached": True, "detected": True, "unrelated_failure": True}, "ERROR"),
        )
        for observations, outcome in cases:
            with self.subTest(outcome=outcome):
                result = self.call(raw(binding(), "mutation", observations))
                self.assertEqual(result["mutation_outcome"], outcome)
                self.assertEqual(result["observation"], observations["baseline"])

    def test_mutation_contradiction_and_mode_shape_are_rejected(self):
        contradictory = {"baseline": "PASS", "mutation_applied": True, "reached": False, "detected": True, "unrelated_failure": False}
        with self.assertRaises(ContractError):
            self.call(raw(binding(), "mutation", contradictory))
        with self.assertRaises(ContractError):
            self.call(raw(binding(), "constraint", {"check": "PASS", "mutation_outcome": "KILLED"}))

    def test_llm_detection_and_deviation_are_separate(self):
        result = self.call(raw(binding(), "llm", {"detection": "detect", "deviation": False}))
        self.assertEqual(result["detection"], "detect")
        self.assertFalse(result["deviation"])
        self.assertIsNone(result["observation"])
        self.assertIsNone(result["mutation_outcome"])

    def test_raw_binding_mismatch_covers_run_operation_and_epoch(self):
        for field, value in (("run_id", "other-run"), ("operation_id", "other-operation"), ("owner_epoch", 4)):
            expected = binding()
            expected[field] = value
            with self.subTest(field=field), self.assertRaises(ContractError):
                self.call(raw(expected, "constraint", {"check": "PASS"}))

    def test_raw_duplicate_unknown_bad_type_and_size_are_rejected(self):
        duplicate = b'{"schema_version":1,"schema_version":1,"kind":"gah_generic_result"}'
        with self.assertRaises(ContractError):
            self.call(duplicate)
        unknown = json.loads(raw(binding(), "constraint", {"check": "PASS"}))
        unknown["extra"] = True
        with self.assertRaises(ContractError):
            self.call(json.dumps(unknown).encode())
        malformed = raw(binding(), "llm", {"detection": "detect", "deviation": 1})
        with self.assertRaises(ContractError):
            self.call(malformed)
        with self.assertRaises(ContractError):
            self.call(b"{" + b" " * (256 * 1024))

    def test_execution_failure_never_adopts_raw_success_text(self):
        for status, exit_code, stopped in (("FAILED", 1, True), ("TIMEOUT", None, False), ("CANCELLED", 3, True), ("COMPLETED", 1, True), ("COMPLETED", 0, False)):
            with self.subTest(status=status, exit_code=exit_code, stopped=stopped):
                result = normalize_generic(
                    b"success KILLED", binding(), execution_status=status,
                    exit_code=exit_code, stop_confirmed=stopped,
                )
                self.assertEqual(result["mutation_outcome"], "ERROR")
                self.assertEqual(result["error_class"], "EXECUTION_FAILURE")
                self.assertIsNone(result["mode"])
                self.assertIsNone(result["raw_digest"])

    def test_execution_inputs_are_strict(self):
        for kwargs in (
            {"execution_status": "finished", "exit_code": 0, "stop_confirmed": True},
            {"execution_status": "COMPLETED", "exit_code": True, "stop_confirmed": True},
            {"execution_status": "COMPLETED", "exit_code": 256, "stop_confirmed": True},
            {"execution_status": "COMPLETED", "exit_code": 0, "stop_confirmed": 1},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ContractError):
                normalize_generic(raw(binding(), "constraint", {"check": "PASS"}), binding(), **kwargs)


if __name__ == "__main__":
    unittest.main()
