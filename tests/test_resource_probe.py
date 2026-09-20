
"""resource_probeの固定parser/session局所試験。実Dockerや外部通信は行わない。"""
from __future__ import annotations

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gah import resource_probe
from gah.contracts import ContractError


PLAN_REF = {"kind": "benchmark_plan", "id": "plan-1", "digest": "a" * 64}
SOURCE_REF = {"kind": "snapshot_manifest", "id": "source-1", "digest": "b" * 64}
REQUEST_DIGEST = "c" * 64


class FakeProvider:
    def __init__(self, values):
        self._values = list(values)

    def snapshot(self):
        value = self._values.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


class ResourceProbeTests(unittest.TestCase):
    def scope(
        self,
        scope_id,
        kind,
        *,
        identity="identity-1",
        epoch=1,
        restart_count=0,
        cpu=0,
        read=0,
        write=0,
        rss=0,
        memory=0,
        parent=None,
    ):
        return {
            "scope_id": scope_id,
            "scope_kind": kind,
            "identity": identity,
            "epoch": epoch,
            "restart_count": restart_count,
            "cpu_ns": cpu,
            "io_read_bytes": read,
            "io_write_bytes": write,
            "rss_bytes": rss,
            "memory_current_bytes": (
                None if memory is None else max(0, memory - 1000)
            ),
            "memory_peak_bytes": memory,
            "parent_scope_id": parent,
        }

    def snapshot(self, captured, seq, scopes):
        return {
            "schema_version": 1,
            "captured_ns": captured,
            "sample_seq": seq,
            "scopes": scopes,
        }

    def session(self, provider, **kwargs):
        return resource_probe.ProbeSession(
            provider,
            plan_ref=PLAN_REF,
            source_ref=SOURCE_REF,
            request_digest=REQUEST_DIGEST,
            **kwargs,
        )

    def base_scopes(self, c_cpu, c_read, c_write, c_rss, c_memory, p_cpu,
                    p_read, p_write, p_rss, h_cpu, h_read, h_write):
        return [
            self.scope(
                "container-1", "container", cpu=c_cpu, read=c_read,
                write=c_write, rss=c_rss, memory=c_memory,
            ),
            self.scope(
                "process-1", "process", cpu=p_cpu, read=p_read,
                write=p_write, rss=p_rss, memory=None,
                parent="container-1",
            ),
            self.scope(
                "harness-1", "host_harness", cpu=h_cpu, read=h_read,
                write=h_write, rss=None, memory=None,
            ),
        ]

    def valid_provider(self):
        return FakeProvider([
            self.snapshot(
                100, 1,
                self.base_scopes(100, 1000, 2000, 10000, 50000,
                                 40, 400, 800, 4000, 10, 100, 200),
            ),
            self.snapshot(
                200, 2,
                self.base_scopes(140, 1200, 2200, 15000, 60000,
                                 80, 800, 1200, 5000, 13, 110, 220),
            ),
            self.snapshot(
                300, 3,
                self.base_scopes(190, 1300, 2300, 12000, 70000,
                                 110, 1100, 1500, 4500, 16, 120, 240),
            ),
        ])

    def test_parser_is_closed_and_rejects_bad_scalar_and_scope_graph(self):
        valid = self.snapshot(
            1, 1, [self.scope("c", "container")],
        )
        checked = resource_probe.parse_snapshot(valid)
        self.assertEqual(checked.to_dict(), valid)

        unknown = dict(valid)
        unknown["unknown"] = 1
        with self.assertRaisesRegex(ContractError, "INVALID_SNAPSHOT"):
            resource_probe.parse_snapshot(unknown)

        boolean_counter = self.snapshot(
            1, 1, [self.scope("c", "container", cpu=True)],
        )
        with self.assertRaisesRegex(ContractError, "INVALID_SCOPE"):
            resource_probe.parse_snapshot(boolean_counter)

        missing_parent = self.snapshot(
            1, 1, [self.scope("p", "process", parent="missing")],
        )
        with self.assertRaisesRegex(ContractError, "INVALID_SCOPE"):
            resource_probe.parse_snapshot(missing_parent)

        cycle = self.snapshot(
            1, 1, [
                self.scope("a", "container", parent="b"),
                self.scope("b", "container", parent="a"),
            ],
        )
        with self.assertRaisesRegex(ContractError, "INVALID_SCOPE"):
            resource_probe.parse_snapshot(cycle)

    def test_delta_uses_non_overlapping_parent_and_keeps_harness_cpu_separate(self):
        result = self.session(self.valid_provider()).begin()
        self.assertIsNotNone(result)
        provider = self.valid_provider()
        session = self.session(provider)
        session.begin()
        session.sample()
        result = session.end()

        metrics = result["metrics"]
        self.assertEqual(metrics["wall_ns"], 200)
        self.assertEqual(metrics["cpu_ns"], 90)
        self.assertEqual(metrics["host_harness_cpu_ns"], 6)
        self.assertEqual(metrics["total_cpu_ns"], 96)
        self.assertEqual(metrics["io_read_bytes"], 300)
        self.assertEqual(metrics["io_write_bytes"], 300)
        self.assertEqual(metrics["host_harness_io_read_bytes"], 20)
        self.assertEqual(metrics["host_harness_io_write_bytes"], 40)
        self.assertEqual(metrics["rss_group_peak_bytes"], 15000)
        self.assertEqual(metrics["rss_group_peak_precision"], "sampled")
        self.assertIsNone(metrics["rss_group_true_peak_bytes"])
        self.assertEqual(metrics["cgroup_memory_peak_bytes"], 70000)
        self.assertNotEqual(metrics["cgroup_memory_peak_bytes"],
                             metrics["rss_group_peak_bytes"])
        self.assertEqual(result["scope"]["excluded_scope_ids"]["cpu_ns"],
                         ["process-1"])
        self.assertEqual(result["scope"]["excluded_scope_ids"]["rss_group_peak_bytes"],
                         ["process-1"])
        self.assertTrue(result["scope"]["non_overlapping"])
        self.assertTrue(result["measurement_complete"])
        self.assertFalse(result["valid_for_slo"])
        self.assertEqual(result["slo_blockers"], ["PRODUCT_SLO_NOT_CONNECTED"])
        self.assertEqual(result["operation_status"], "COMPLETED")
        self.assertEqual(result["binding"]["request_digest"], REQUEST_DIGEST)
        self.assertEqual(result["identity"]["start"][0]["identity"],
                         "identity-1")

    def test_rss_is_observed_sampled_peak_not_true_peak(self):
        provider = self.valid_provider()
        session = self.session(provider)
        session.begin()
        session.sample()
        result = session.end()
        self.assertEqual(result["metrics"]["rss_group_peak_precision"], "sampled")
        self.assertIsNone(result["metrics"]["rss_group_true_peak_bytes"])
        self.assertEqual(result["metrics"]["cgroup_memory_peak_by_scope"],
                         {"container-1": 70000})

    def test_counter_reset_invalidates_resource_metrics_without_zero_fill(self):
        scopes1 = [self.scope("c", "container", cpu=100, read=100,
                              write=100, rss=1000, memory=2000),
                   self.scope("h", "host_harness", cpu=10, read=10, write=10,
                              rss=None, memory=None)]
        scopes2 = [self.scope("c", "container", cpu=90, read=90,
                              write=90, rss=1100, memory=2100),
                   self.scope("h", "host_harness", cpu=11, read=11, write=11,
                              rss=None, memory=None)]
        scopes3 = [self.scope("c", "container", cpu=120, read=120,
                              write=120, rss=1200, memory=2200),
                   self.scope("h", "host_harness", cpu=12, read=12, write=12,
                              rss=None, memory=None)]
        session = self.session(FakeProvider([
            self.snapshot(10, 1, scopes1),
            self.snapshot(20, 2, scopes2),
            self.snapshot(30, 3, scopes3),
        ]))
        session.begin()
        session.sample()
        result = session.end()
        self.assertIn("COUNTER_RESET", result["reasons"])
        self.assertIsNone(result["metrics"]["cpu_ns"])
        self.assertIsNone(result["metrics"]["io_read_bytes"])
        self.assertIsNone(result["metrics"]["rss_group_peak_bytes"])
        self.assertFalse(result["valid_for_slo"])
        self.assertEqual(result["metrics"]["wall_ns"], 20)

    def test_sample_gap_invalidates_counters_and_retains_wall(self):
        scopes = [
            self.scope("c", "container", cpu=100, read=100,
                       write=100, rss=1000, memory=2000),
            self.scope("h", "host_harness", cpu=10, read=10,
                       write=10, rss=None, memory=None),
        ]
        session = self.session(FakeProvider([
            self.snapshot(10, 1, scopes),
            RuntimeError("provider unavailable"),
            self.snapshot(30, 3, [
                self.scope("c", "container", cpu=120, read=120,
                           write=120, rss=1200, memory=2200),
                self.scope("h", "host_harness", cpu=12, read=12,
                           write=12, rss=None, memory=None),
            ]),
        ]))
        session.begin()
        self.assertIsNone(session.sample())
        result = session.end()
        self.assertIn("PROVIDER_FAILURE", result["reasons"])
        self.assertIn("SAMPLE_GAP", result["reasons"])
        self.assertIsNone(result["metrics"]["cpu_ns"])
        self.assertIsNone(result["metrics"]["rss_group_peak_bytes"])
        self.assertEqual(result["metrics"]["wall_ns"], 20)
        self.assertFalse(result["valid_for_slo"])

    def test_identity_epoch_and_restart_changes_are_not_reused(self):
        start = [
            self.scope("c", "container", identity="old", epoch=1,
                       restart_count=0, cpu=10, read=10, write=10,
                       rss=100, memory=1000),
            self.scope("h", "host_harness", cpu=1, read=1, write=1,
                       rss=None, memory=None),
        ]
        end = [
            self.scope("c", "container", identity="new", epoch=2,
                       restart_count=1, cpu=20, read=20, write=20,
                       rss=200, memory=2000),
            self.scope("h", "host_harness", cpu=2, read=2, write=2,
                       rss=None, memory=None),
        ]
        session = self.session(FakeProvider([
            self.snapshot(10, 1, start),
            self.snapshot(20, 2, end),
        ]))
        session.begin()
        result = session.end()
        self.assertIn("IDENTITY_CHANGED", result["reasons"])
        self.assertIn("EPOCH_CHANGED", result["reasons"])
        self.assertIn("RESTART_DETECTED", result["reasons"])
        self.assertIsNone(result["metrics"]["cpu_ns"])
        self.assertIsNone(result["metrics"]["rss_group_peak_bytes"])
        self.assertFalse(result["valid_for_slo"])

    def test_missing_harness_scope_is_null_and_not_slo_valid(self):
        scopes = [
            self.scope("c", "container", cpu=10, read=10,
                       write=10, rss=100, memory=1000),
        ]
        session = self.session(FakeProvider([
            self.snapshot(10, 1, scopes),
            self.snapshot(20, 2, [
                self.scope("c", "container", cpu=20, read=20,
                           write=20, rss=200, memory=2000),
            ]),
        ]))
        session.begin()
        result = session.end()
        self.assertEqual(result["metrics"]["cpu_ns"], 10)
        self.assertIsNone(result["metrics"]["host_harness_cpu_ns"])
        self.assertIn("SCOPE_INSUFFICIENT", result["reasons"])
        self.assertIn("REQUIRED_METRIC_MISSING", result["reasons"])
        self.assertFalse(result["valid_for_slo"])
        self.assertEqual(result["operation_status"], "INCOMPLETE")

    def test_binding_and_provider_boundary_are_strict(self):
        with self.assertRaisesRegex(ContractError, "PROVIDER_INVALID"):
            self.session(object())

        with self.assertRaisesRegex(ContractError, "INVALID_BINDING"):
            resource_probe.ProbeSession(
                FakeProvider([]),
                plan_ref=PLAN_REF,
                source_ref=SOURCE_REF,
                request_digest=True,
            )

        session = self.session(FakeProvider([]))
        result = session.end()
        self.assertIn("INTERVAL_NOT_STARTED", result["reasons"])
        self.assertFalse(result["valid_for_slo"])

    def test_measure_interval_rejects_bad_count_and_handles_fixed_samples(self):
        with self.assertRaisesRegex(ContractError, "INVALID_INPUT"):
            resource_probe.measure_interval(
                self.valid_provider(),
                plan_ref=PLAN_REF,
                source_ref=SOURCE_REF,
                request_digest=REQUEST_DIGEST,
                sample_count=True,
            )
        result = resource_probe.measure_interval(
            self.valid_provider(),
            plan_ref=PLAN_REF,
            source_ref=SOURCE_REF,
            request_digest=REQUEST_DIGEST,
            sample_count=1,
        )
        self.assertEqual(result["interval"]["sample_count"], 3)
        self.assertTrue(result["measurement_complete"])
        self.assertFalse(result["valid_for_slo"])


if __name__ == "__main__":
    unittest.main()
