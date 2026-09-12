"""評価器校正のcontrolled response境界を検査する。"""

from __future__ import annotations

import copy
import sys
from pathlib import Path
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gah.contracts import ContractError
from gah.evaluation_data import build_pack
from gah.llm_evaluator import MODEL
from gah.measurement_calibration import (
    _SELECTIONS,
    _degradation_checks,
    _fixed_vectors,
    build_calibration_request,
    calibrate_evaluator,
    calibrate_measurement,
)


class MeasurementCalibrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pack = build_pack()

    def test_controlled_calibration_passes_without_target_call(self):
        result = calibrate_measurement(self.pack)
        self.assertTrue(result["evaluator_calibration_passed"])
        self.assertEqual(result["failure_count"], 0)
        self.assertEqual(result["failed_case_ids"], [])
        self.assertEqual(result["vector_count"], 165)
        self.assertEqual(result["executed_vector_count"], 165)
        self.assertEqual(result["case_count"], 18)
        self.assertTrue(all(result["checks"].values()))
        self.assertFalse(result["ci_eligible"])
        self.assertIsNone(result["target_agreement_passed"])

    def test_detection_action_cross_product_is_independent(self):
        self.assertEqual(len(_SELECTIONS), 9)
        self.assertEqual(
            {(item["detection"], item["action"]) for item in _SELECTIONS},
            {(detection, action) for detection in ("detect", "allow", "indeterminate")
             for action in ("apply", "block", "defer")},
        )
        result = calibrate_measurement(self.pack)
        self.assertGreaterEqual(result["vector_count"], len(_SELECTIONS))
        self.assertEqual(result["failed_vector_ids"], [])
        vectors = _fixed_vectors(self.pack)
        valid = [vector for vector in vectors if vector["scenario"] == "valid"]
        self.assertEqual(len(valid), 18 * 9)
        self.assertEqual({sum(vector["case_id"] == case["case_id"] for vector in valid) for case in self.pack["case_sets"]["calibration"]["cases"]}, {9})

    def test_malformed_and_usage_boundary_are_part_of_calibration(self):
        result = calibrate_measurement(self.pack)
        self.assertEqual(result["failure_count"], 0)
        self.assertIn("always_allow_rejected", result["checks"])

    def test_degraded_meters_are_rejected(self):
        result = calibrate_measurement(self.pack)
        self.assertTrue(result["checks"]["always_allow_rejected"])
        self.assertTrue(result["checks"]["always_detect_rejected"])
        self.assertTrue(result["checks"]["detection_complement_rejected"])

    def test_degradation_checks_depend_on_session_measurement(self):
        vectors = _fixed_vectors(self.pack)
        with mock.patch("gah.measurement_calibration._run_vector", return_value=True):
            checks = _degradation_checks(self.pack, vectors)
        self.assertEqual(checks, {
            "always_allow_rejected": False,
            "always_detect_rejected": False,
            "detection_complement_rejected": False,
        })

    def test_direct_request_is_strict_and_pack_is_bound(self):
        request = build_calibration_request(self.pack)
        self.assertEqual(
            calibrate_evaluator(request)["pack_digest"],
            calibrate_measurement(self.pack)["pack_digest"],
        )
        for mutation in (
            lambda value: value.pop("pack"),
            lambda value: value.update(extra=True),
            lambda value: value.__setitem__("schema_version", True),
        ):
            candidate = copy.deepcopy(request)
            mutation(candidate)
            with self.subTest(mutation=mutation):
                with self.assertRaises(ContractError):
                    calibrate_evaluator(candidate)

        changed = copy.deepcopy(self.pack)
        changed["provenance"]["generator"] = "changed"
        with self.assertRaises(ContractError):
            calibrate_measurement(changed)

    def test_evaluator_profile_is_not_target_model_identity(self):
        result = calibrate_measurement(self.pack)
        self.assertNotEqual(result["evaluator_profile_ref"]["id"], MODEL)
        self.assertNotEqual(result["evaluator_profile_ref"]["digest"], result["source_digest"])
        self.assertIn("contracts.py", result["source_digests"])
        self.assertIn("corpus.py", result["source_digests"])

    def test_source_part_change_changes_profile_digest(self):
        baseline = calibrate_measurement(self.pack)
        changed = dict(baseline["source_digests"])
        changed["corpus.py"] = "0" * 64
        with mock.patch("gah.measurement_calibration._source_parts", return_value=changed):
            altered = calibrate_measurement(self.pack)
        self.assertNotEqual(altered["source_digest"], baseline["source_digest"])
        self.assertNotEqual(altered["evaluator_profile_ref"]["digest"], baseline["evaluator_profile_ref"]["digest"])

    def test_source_change_during_run_cannot_pass_calibration(self):
        vectors = _fixed_vectors(self.pack)
        source = {
            "measurement_calibration.py": "a" * 64,
            "llm_evaluator.py": "b" * 64,
            "evaluation_data.py": "c" * 64,
            "contracts.py": "d" * 64,
            "corpus.py": "e" * 64,
        }
        changed = dict(source)
        changed["llm_evaluator.py"] = "f" * 64
        with mock.patch("gah.measurement_calibration._source_parts", side_effect=[source, changed]), \
             mock.patch("gah.measurement_calibration._execute_vectors", return_value=([], len(vectors), {"case"})), \
             mock.patch("gah.measurement_calibration._degradation_checks", return_value={
                 "always_allow_rejected": True,
                 "always_detect_rejected": True,
                 "detection_complement_rejected": True,
             }):
            result = calibrate_measurement(self.pack)
        self.assertFalse(result["source_stable"])
        self.assertFalse(result["evaluator_calibration_passed"])
        self.assertIn("source-integrity", result["failed_vector_ids"])

    def test_mismatch_is_reported_as_fixed_case_id_without_raw_body(self):
        with mock.patch(
            "gah.measurement_calibration._run_vector",
            return_value=False,
        ):
            result = calibrate_measurement(self.pack)
        self.assertFalse(result["evaluator_calibration_passed"])
        self.assertGreater(result["failure_count"], 0)
        self.assertEqual(result["failure_count"], len(result["failed_vector_ids"]))
        self.assertTrue(all(isinstance(case_id, str) for case_id in result["failed_case_ids"]))
        self.assertNotIn("raw", result)
        self.assertNotIn("content", result)


if __name__ == "__main__":
    unittest.main()
