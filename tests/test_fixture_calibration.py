from pathlib import Path
import hashlib
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from gah import fixture_calibration, normalized
from gah.contracts import ContractError


class FixtureCalibrationTests(unittest.TestCase):
    def test_real_worker_and_normalizer_pass_fixed_contrasts(self):
        result = fixture_calibration.calibrate_fixed_fixture()
        self.assertTrue(result["passed"], [item for item in result["checks"] if not item["passed"]])
        self.assertEqual(result["vector_count"], 36)
        self.assertFalse(result["authority_connected"])
        self.assertFalse(result["ci_eligible"])

    def test_always_pass_normalizer_is_detected(self):
        original = normalized.normalize_generic
        def faulty(*args, **kwargs):
            result = original(*args, **kwargs)
            if result["mode"] == "constraint":
                result["observation"] = "PASS"
            return result
        with patch.object(normalized, "normalize_generic", side_effect=faulty):
            result = fixture_calibration.calibrate_fixed_fixture()
        self.assertFalse(result["passed"])
        self.assertEqual(sum(not item["passed"] for item in result["checks"]), 10)

    def test_worker_digest_mismatch_stops_before_loading_code(self):
        with patch.object(Path, "read_bytes", return_value=b"{}"), patch.object(
                fixture_calibration.importlib.util, "spec_from_file_location") as loader:
            with self.assertRaisesRegex(ContractError, "^FIXTURE_RUNTIME_MISMATCH$"):
                fixture_calibration.calibrate_fixed_fixture()
            loader.assert_not_called()

    def test_binding_only_degradation_is_rejected(self):
        original = normalized.normalize_generic

        def faulty(*args, **kwargs):
            result = original(*args, **kwargs)
            result["binding"]["fixture_digest"] = "a" * 64
            return result

        with patch.object(normalized, "normalize_generic", side_effect=faulty):
            result = fixture_calibration.calibrate_fixed_fixture()
        self.assertFalse(result["passed"])
        self.assertFalse(result["checks"][0]["passed"])

    def test_mode_only_degradation_is_rejected(self):
        original = normalized.normalize_generic

        def faulty(*args, **kwargs):
            result = original(*args, **kwargs)
            if result["mode"] == "mutation":
                result["mode"] = "constraint"
            return result

        with patch.object(normalized, "normalize_generic", side_effect=faulty):
            result = fixture_calibration.calibrate_fixed_fixture()
        self.assertFalse(result["passed"])
        self.assertTrue(any(not check["passed"] for check in result["checks"][20:]))

    def test_wrong_malformed_error_code_is_rejected(self):
        original = normalized.normalize_generic

        def faulty(raw, *args, **kwargs):
            if raw == b"{":
                raise ContractError("WRONG_ERROR")
            return original(raw, *args, **kwargs)

        with patch.object(normalized, "normalize_generic", side_effect=faulty):
            result = fixture_calibration.calibrate_fixed_fixture()
        self.assertFalse(result["passed"])
        malformed = next(check for check in result["checks"] if check["case"] == "malformed-output")
        self.assertFalse(malformed["passed"])

    def test_lock_duplicate_key_is_rejected(self):
        worker_source = fixture_calibration.WORKER.read_bytes()
        duplicate_lock = b'{"schema_version":1,"worker_digest":"' + b"a" * 64 + b'","worker_digest":"' + b"a" * 64 + b'"}'
        with patch.object(fixture_calibration.Path, "read_bytes",
                          side_effect=[worker_source, duplicate_lock]), patch.object(
                fixture_calibration.importlib.util, "spec_from_file_location") as loader:
            with self.assertRaisesRegex(ContractError, "^FIXTURE_RUNTIME_MISMATCH$"):
                fixture_calibration.calibrate_fixed_fixture()
            loader.assert_not_called()

    def test_profile_binding_uses_verified_sources(self):
        original = normalized.normalize_generic
        seen = []

        def observe(raw, expected_binding, **kwargs):
            seen.append(dict(expected_binding))
            return original(raw, expected_binding, **kwargs)

        with patch.object(normalized, "normalize_generic", side_effect=observe):
            result = fixture_calibration.calibrate_fixed_fixture()
        lock = fixture_calibration._load_lock(
            (fixture_calibration.ROOT / "config/fixture-runtime.lock.json").read_bytes())
        normalizer_digest = hashlib.sha256(Path(normalized.__file__).read_bytes()).hexdigest()
        self.assertEqual(result["profile"]["worker_digest"], lock["worker_digest"])
        self.assertEqual(result["profile"]["normalizer_digest"], normalizer_digest)
        self.assertTrue(seen)
        self.assertEqual(seen[0]["fixture_digest"], lock["worker_digest"])
        self.assertEqual(seen[0]["adapter_digest"], normalizer_digest)
