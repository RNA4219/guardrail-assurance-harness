import copy
import hashlib
import json
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gah.decision import assess


_DIGEST = "a" * 64


def request(*metrics, **overrides):
    value = {
        "schema_version": 1,
        "request_id": "run-1",
        "target_digest": _DIGEST,
        "contract_digest": "b" * 64,
        "purpose": "component_validation",
        "observed_at": 100,
        "assessed_at": 100,
        "metrics": list(metrics),
        "required_missing": False,
        "critical_missing": False,
        "integrity_failure": False,
        "forbidden_violation": False,
        "critical_violation": False,
        "warning": False,
    }
    value.update(overrides)
    return value


def metric(metric_id="m1", name="recall", critical=False, numerator=95, denominator=100, baseline=None):
    return {
        "metric_id": metric_id,
        "name": name,
        "critical": critical,
        "numerator": numerator,
        "denominator": denominator,
        "baseline": baseline,
    }


class DecisionTests(unittest.TestCase):
    def test_request_digest_distinguishes_equivalent_input_fractions(self):
        first = assess(request(metric(numerator=95, denominator=100)))
        second = assess(request(metric(numerator=190, denominator=200)))
        self.assertEqual(first["metrics"], second["metrics"])
        self.assertNotEqual(first["request_digest"], second["request_digest"])
        expected = hashlib.sha256(
            json.dumps(
                request(metric(numerator=95, denominator=100)),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        self.assertEqual(first["request_digest"], expected)

    def test_absolute_thresholds_are_table_driven_for_every_metric_and_criticality(self):
        cases = [
            ("recall", False, 95, 100, True, 94, 100, "DEGRADED"),
            ("recall", True, 99, 100, True, 98, 100, "HOLD"),
            ("fnr", False, 5, 100, True, 6, 100, "DEGRADED"),
            ("fnr", True, 1, 100, True, 2, 100, "HOLD"),
            ("fpr", False, 5, 100, True, 6, 100, "DEGRADED"),
            ("fpr", True, 2, 100, True, 3, 100, "HOLD"),
            ("asr", False, 3, 100, True, 4, 100, "DEGRADED"),
            ("asr", True, 1, 100, True, 2, 100, "HOLD"),
            ("mutation_score", False, 95, 100, True, 94, 100, "DEGRADED"),
            ("mutation_score", True, 100, 100, True, 99, 100, "HOLD"),
        ]
        for name, critical, n, d, expected_pass, after_n, after_d, failed_state in cases:
            with self.subTest(name=name, critical=critical, point="boundary"):
                result = assess(request(metric(name=name, critical=critical, numerator=n, denominator=d)))
                self.assertEqual(result["metrics"][0]["absolute_pass"], expected_pass)
                self.assertEqual(result["assurance"], "HEALTHY")
            with self.subTest(name=name, critical=critical, point="just_after"):
                result = assess(
                    request(metric(name=name, critical=critical, numerator=after_n, denominator=after_d))
                )
                self.assertFalse(result["metrics"][0]["absolute_pass"])
                self.assertEqual(result["assurance"], failed_state)

    def test_delta_thresholds_are_table_driven_for_every_comparable_metric(self):
        cases = [
            ("recall", False, 95, 100, 97, 100, 94, 100),
            ("recall", True, 99, 100, 100, 100, 98, 100),
            ("fpr", False, 5, 100, 4, 100, 6, 100),
            ("fpr", True, 150, 10000, 100, 10000, 151, 10000),
            ("asr", False, 3, 100, 2, 100, 4, 100),
            ("asr", True, 90, 10000, 40, 10000, 91, 10000),
            ("mutation_score", False, 95, 100, 97, 100, 94, 100),
            ("mutation_score", True, 100, 100, 100, 100, 99, 100),
        ]
        for name, critical, n, d, bn, bd, after_n, after_d in cases:
            baseline = {"numerator": bn, "denominator": bd}
            with self.subTest(name=name, critical=critical, point="boundary"):
                result = assess(
                    request(metric(name=name, critical=critical, numerator=n, denominator=d, baseline=baseline))
                )
                self.assertTrue(result["metrics"][0]["delta_pass"])
                self.assertEqual(result["assurance"], "HEALTHY")
            with self.subTest(name=name, critical=critical, point="just_after"):
                result = assess(
                    request(
                        metric(
                            name=name,
                            critical=critical,
                            numerator=after_n,
                            denominator=after_d,
                            baseline=baseline,
                        )
                    )
                )
                self.assertFalse(result["metrics"][0]["delta_pass"])
                self.assertEqual(result["assurance"], "HOLD" if critical else "DEGRADED")

    def test_boundary_and_exact_delta_use_fraction(self):
        result = assess(request(metric(baseline={"numerator": 98, "denominator": 100})))
        self.assertEqual(result["assurance"], "DEGRADED")
        self.assertEqual(result["metrics"][0]["value"], [19, 20])
        self.assertTrue(result["metrics"][0]["absolute_pass"])
        self.assertFalse(result["metrics"][0]["delta_pass"])

        result = assess(request(metric(numerator=96, baseline={"numerator": 98, "denominator": 100})))
        self.assertTrue(result["metrics"][0]["delta_pass"])

    def test_critical_threshold_and_zero_denominator(self):
        result = assess(request(metric(critical=True, numerator=98)))
        self.assertEqual(result["assurance"], "HOLD")
        self.assertEqual(result["metrics"][0]["value"], [49, 50])
        self.assertEqual(result["reasons"][0]["code"], "metric_absolute_threshold")

        result = assess(request(metric(numerator=0, denominator=0)))
        self.assertEqual(result["assurance"], "UNKNOWN")
        self.assertIsNone(result["metrics"][0]["absolute_pass"])
        self.assertIsNone(result["metrics"][0]["value"])

    def test_stale_and_future_observations(self):
        self.assertEqual(assess(request(metric(), assessed_at=100 + 86400))["assurance"], "HEALTHY")
        self.assertEqual(assess(request(metric(), assessed_at=100 + 86401))["assurance"], "UNKNOWN")
        self.assertEqual(
            assess(request(metric(critical=True), assessed_at=100 + 86401))["assurance"], "HOLD"
        )
        self.assertEqual(assess(request(metric(), observed_at=101))["assurance"], "HOLD")

    def test_precedence_and_all_reasons_are_retained(self):
        result = assess(
            request(
                metric(numerator=94),
                required_missing=True,
                forbidden_violation=True,
                critical_violation=True,
                warning=True,
            )
        )
        self.assertEqual(result["assurance"], "HOLD")
        self.assertEqual(
            [reason["state"] for reason in result["reasons"]],
            ["HOLD", "DEGRADED", "DEGRADED", "UNKNOWN", "WARNING"],
        )

    def test_input_validation_does_not_echo_payload(self):
        payload = "SECRET-payload"
        bad = request(metric())
        bad["request_id"] = payload
        with self.assertRaises(ValueError) as raised:
            assess(bad)
        self.assertNotIn(payload, str(raised.exception))

        for bad_value in (True, 1.0, "1"):
            bad = request(metric())
            bad["assessed_at"] = bad_value
            with self.assertRaises(ValueError):
                assess(bad)

        bad = request(metric())
        bad["unknown"] = 1
        with self.assertRaises(ValueError):
            assess(bad)

    def test_duplicate_and_invalid_metric_inputs_rejected_without_mutation(self):
        first = metric("m1")
        original = copy.deepcopy(first)
        with self.assertRaises(ValueError):
            assess(request(first, metric("m1", name="fpr", numerator=0)))
        self.assertEqual(first, original)

        with self.assertRaises(ValueError):
            assess(request(metric(name="fnr", numerator=5, baseline={"numerator": 1, "denominator": 1})))
        with self.assertRaises(ValueError):
            assess(request(metric(numerator=101, denominator=100)))


if __name__ == "__main__":
    unittest.main()
