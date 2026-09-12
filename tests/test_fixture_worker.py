"""固定fixture workerの通常シナリオ試験。probeはhost上で起動しない。"""

from __future__ import annotations

import json
import sys
import subprocess
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "fixtures" / "runtime" / "fixture_worker.py"
sys.path.insert(0, str(WORKER.parent))
import fixture_worker
_FIELDS = (
    "run_id", "operation_id", "owner_epoch", "contract_digest", "target_digest",
    "obligation_id", "case_id", "trial_id", "stage_id", "fixture_digest",
    "adapter_digest", "policy_digest", "evaluator_digest", "isolation_digest",
)


def _binding() -> dict[str, object]:
    result: dict[str, object] = {}
    for field in _FIELDS:
        if field == "owner_epoch":
            result[field] = 1
        elif field.endswith("_digest"):
            result[field] = "a" * 64
        else:
            result[field] = f"fixture-{field}"
    return result


def _run(scenario: str, payload: object | None = None, *extra: str) -> subprocess.CompletedProcess[str]:
    if payload is None:
        payload = _binding()
    return subprocess.run(
        [sys.executable, str(WORKER), "--scenario", scenario, *extra],
        input=json.dumps(payload, ensure_ascii=False),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=5,
        cwd=ROOT,
        check=False,
    )


class FixtureWorkerTests(TestCase):
    def test_all_constraint_boundaries_are_computed(self) -> None:
        for number in range(1, 11):
            with self.subTest(control=f"C{number:02d}"):
                good = _run(f"constraint:C{number:02d}:good")
                bad = _run(f"constraint:C{number:02d}:bad")
                self.assertEqual(good.returncode, 0)
                self.assertEqual(bad.returncode, 0)
                good_data = json.loads(good.stdout)
                bad_data = json.loads(bad.stdout)
                self.assertEqual(set(good_data), {"binding", "kind", "mode", "observations", "schema_version"})
                self.assertEqual(good_data["schema_version"], 1)
                self.assertEqual(good_data["kind"], "gah_generic_result")
                self.assertEqual(good_data["mode"], "constraint")
                self.assertEqual(good_data["binding"], _binding())
                self.assertEqual(good_data["observations"], {"check": "PASS"})
                self.assertEqual(bad_data["observations"], {"check": "FAIL"})
                self.assertNotIn("KILLED", good.stdout + bad.stdout)

    def test_all_mutation_families_compute_trace_observations(self) -> None:
        expected_keys = {"baseline", "detected", "mutation_applied", "reached", "unrelated_failure"}
        for number in range(1, 6):
            with self.subTest(family=f"F{number:02d}"):
                healthy = _run(f"mutation:F{number:02d}:healthy")
                decayed = _run(f"mutation:F{number:02d}:decayed")
                self.assertEqual(healthy.returncode, 0)
                self.assertEqual(decayed.returncode, 0)
                healthy_data = json.loads(healthy.stdout)
                decayed_data = json.loads(decayed.stdout)
                self.assertEqual(healthy_data["mode"], "mutation")
                self.assertEqual(set(healthy_data["observations"]), expected_keys)
                self.assertEqual(healthy_data["observations"]["baseline"], "PASS")
                self.assertTrue(healthy_data["observations"]["mutation_applied"])
                self.assertTrue(healthy_data["observations"]["reached"])
                self.assertTrue(healthy_data["observations"]["detected"])
                self.assertFalse(healthy_data["observations"]["unrelated_failure"])
                self.assertEqual(decayed_data["observations"]["baseline"], "PASS")
                self.assertTrue(decayed_data["observations"]["mutation_applied"])
                self.assertTrue(decayed_data["observations"]["reached"])
                self.assertFalse(decayed_data["observations"]["detected"])
                self.assertFalse(decayed_data["observations"]["unrelated_failure"])
                self.assertNotIn("KILLED", healthy.stdout + decayed.stdout)

    def test_scenario_and_arguments_are_strict(self) -> None:
        unknown = _run("constraint:C99:good")
        extra = _run("constraint:C01:good", _binding(), "--extra")
        self.assertEqual(unknown.returncode, 2)
        self.assertEqual(extra.returncode, 2)

    def test_binding_rejects_bad_types_unknown_and_duplicate_keys(self) -> None:
        bad_type = _binding()
        bad_type["owner_epoch"] = True
        unknown = dict(_binding())
        unknown["extra"] = "no"
        cases = [bad_type, unknown]
        for payload in cases:
            with self.subTest(payload=payload):
                result = _run("constraint:C01:good", payload)
                self.assertEqual(result.returncode, 2)
                self.assertNotIn("fixture-", result.stderr)
        duplicate = json.dumps(_binding()).replace('"run_id":', '"run_id":"duplicate","run_id":', 1)
        result = subprocess.run(
            [sys.executable, str(WORKER), "--scenario", "constraint:C01:good"],
            input=duplicate,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=5,
            cwd=ROOT,
            check=False,
        )
        self.assertEqual(result.returncode, 2)


class IsolationRouteParserTests(TestCase):
    def test_ipv6_loopback_default_is_not_external_but_other_interface_is(self) -> None:
        loopback = " ".join(("0" * 32, "00", "0" * 32, "00", "0" * 32,
                              "00000000", "00000000", "00000000", "00000000", "lo"))
        external = loopback[:-2] + "eth0"
        self.assertTrue(fixture_worker._ipv6_without_external_default(loopback + "\n"))
        self.assertFalse(fixture_worker._ipv6_without_external_default(external + "\n"))
        self.assertFalse(fixture_worker._ipv6_without_external_default("invalid route\n"))
        self.assertFalse(fixture_worker._ipv6_without_external_default(loopback.replace("00000000", "bad", 1) + "\n"))
        invalid_prefix = loopback.split()
        invalid_prefix[1] = "81"
        self.assertFalse(fixture_worker._ipv6_without_external_default(" ".join(invalid_prefix) + "\n"))
        self.assertFalse(fixture_worker._ipv6_without_external_default(None))

    def test_ipv4_parser_is_fail_closed_and_ignores_only_loopback_default(self) -> None:
        header = "Iface Destination Gateway Flags RefCnt Use Metric Mask MTU Window IRTT"
        row = "lo 00000000 00000000 0000 0 0 0 00000000 0 0 0"
        external = row.replace("lo ", "eth0 ")
        self.assertTrue(fixture_worker._ipv4_without_external_default(header + "\n" + row + "\n"))
        self.assertFalse(fixture_worker._ipv4_without_external_default(header + "\n" + external + "\n"))
        self.assertFalse(fixture_worker._ipv4_without_external_default(header + "\nbroken\n"))
        self.assertFalse(fixture_worker._ipv4_without_external_default(None))
