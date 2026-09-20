"""固定worker cgroup資源観測の契約と永続化試験。Dockerは起動しない。"""
from pathlib import Path
from io import BytesIO
from unittest.mock import patch
import runpy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from unittest import TestCase

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gah.worker_metrics import (
    WorkerMetricsJournal, collect_cgroup_metrics, decode_worker_metrics,
    empty_worker_metrics, split_metrics_footer, validate_worker_metrics,
)
from gah.wire import canonical_bytes
from gah.docker_runner import Capture, DockerRunner, capture_bounded


class WorkerMetricsTests(TestCase):
    def test_v2_cgroup_aggregates_cpu_and_block_io_but_never_claims_rss_slo(self):
        files = {
            "/sys/fs/cgroup/cpu.stat": "usage_usec 71\nuser_usec 41\nsystem_usec 30\n",
            "/sys/fs/cgroup/memory.peak": "4096\n",
            "/sys/fs/cgroup/io.stat": "8:0 rbytes=10 wbytes=20 rios=1\n8:16 rbytes=3 wbytes=4 rios=1\n",
        }
        metrics = collect_cgroup_metrics(files.get)
        self.assertEqual(metrics["cpu_ns"], 71000)
        self.assertEqual(metrics["memory_peak_bytes"], 4096)
        self.assertEqual(metrics["io_read_bytes"], 13)
        self.assertEqual(metrics["io_write_bytes"], 24)
        self.assertIsNone(metrics["rss_peak_bytes"])
        self.assertFalse(metrics["valid_for_slo"])
        partial = collect_cgroup_metrics({**files, "/sys/fs/cgroup/io.stat": "8:0 rbytes=10 rios=1\n"}.get)
        self.assertIsNone(partial["io_read_bytes"])
        self.assertIsNone(partial["io_write_bytes"])
        duplicate = collect_cgroup_metrics({**files, "/sys/fs/cgroup/io.stat": "8:0 rbytes=1 wbytes=2\n8:0 rbytes=1 wbytes=2\n"}.get)
        self.assertIsNone(duplicate["io_read_bytes"])
        self.assertIsNone(duplicate["io_write_bytes"])

    def test_v1_blkio_ignores_checked_sync_async_and_total_rows(self):
        value = """8:0 Read 10
8:0 Write 20
8:0 Sync Read 4
8:0 Async Read 6
8:0 Sync Write 9
8:0 Async Write 11
8:0 Total 30
"""
        files = {
            "/sys/fs/cgroup/cpuacct/cpuacct.usage": "1234\n",
            "/sys/fs/cgroup/memory/memory.max_usage_in_bytes": "4096\n",
            "/sys/fs/cgroup/blkio/blkio.io_service_bytes_recursive": value,
        }
        runtime = collect_cgroup_metrics(files.get)
        self.assertEqual((runtime["io_read_bytes"], runtime["io_write_bytes"]), (10, 20))
        root = Path(__file__).resolve().parents[1]
        import sys
        sys.path.insert(0, str(root / "src"))
        for worker_path in (root / "fixtures/runtime/fixture_worker.py",
                            root / "fixtures/llm/guardrail_worker.py"):
            worker = runpy.run_path(str(worker_path))
            original_open = Path.open
            def fake_open(path, *args, **kwargs):
                value = files.get(str(path).replace("\\", "/"))
                if value is None:
                    raise OSError("missing fixed cgroup file")
                return BytesIO(value.encode("ascii"))
            with patch.object(Path, "open", fake_open):
                metrics = worker["_worker_cgroup_metrics"]()
            self.assertEqual((metrics["io_read_bytes"], metrics["io_write_bytes"]), (10, 20))

    def test_missing_and_malformed_cgroup_fields_remain_null(self):
        metrics = collect_cgroup_metrics(lambda _path: None)
        self.assertEqual(metrics["capture_status"], "CAPTURED")
        self.assertEqual(metrics["cpu_ns"], None)
        self.assertEqual(metrics["rss_peak_bytes"], None)
        self.assertEqual(metrics["memory_peak_bytes"], None)
        self.assertEqual(metrics["io_read_bytes"], None)
        self.assertEqual(metrics["io_write_bytes"], None)
        self.assertFalse(metrics["valid_for_slo"])

    def test_footer_is_removed_without_changing_main_stderr_and_damage_is_inconclusive(self):
        metrics = collect_cgroup_metrics(lambda _path: None)
        footer = b"GAH-WORKER-METRICS-V1 " + canonical_bytes(metrics) + b"\n"
        prior, extracted = split_metrics_footer(b"" + b"\n" + footer)
        self.assertEqual(prior, b"")
        self.assertEqual(extracted, metrics)
        prior, extracted = split_metrics_footer(b"warning\n\n" + footer)
        self.assertEqual(prior, b"warning\n")
        self.assertEqual(extracted, metrics)
        prior, extracted = split_metrics_footer(b"\nGAH-WORKER-METRICS-V1 {broken}\n")
        self.assertEqual(prior, b"")
        self.assertEqual(extracted, empty_worker_metrics("INVALID"))
        prior, extracted = split_metrics_footer(b"")
        self.assertEqual(prior, b"")
        self.assertEqual(extracted, empty_worker_metrics("MISSING"))

    def test_metrics_contract_rejects_bool_unknown_fields_and_false_slo_claim(self):
        valid = empty_worker_metrics("MISSING")
        with self.assertRaises(ValueError):
            validate_worker_metrics({**valid, "cpu_ns": True})
        with self.assertRaises(ValueError):
            validate_worker_metrics({**valid, "unexpected": 1})
        with self.assertRaises(ValueError):
            validate_worker_metrics({**valid, "valid_for_slo": True})
        with self.assertRaises(ValueError):
            validate_worker_metrics({**valid, "cpu_ns": (1 << 63)})
        with self.assertRaises(ValueError):
            decode_worker_metrics(canonical_bytes(valid) + b" ")

    @staticmethod
    def binding(run_id="run-1", operation_id="op-1"):
        digest = "a" * 64
        return {
            "run_id": run_id, "operation_id": operation_id, "owner_epoch": 1,
            "contract_digest": digest, "target_digest": digest, "obligation_id": "obl-1",
            "case_id": "case-1", "trial_id": "trial-1", "stage_id": "stage-1",
            "fixture_digest": digest, "adapter_digest": digest, "policy_digest": digest,
            "evaluator_digest": digest, "isolation_digest": digest,
        }

    @staticmethod
    def execution(binding):
        return {"run_id": binding["run_id"], "operation_id": binding["operation_id"],
                "request_digest": "b" * 64, "binding": binding,
                "scenario": "constraint:C01:good", "image_id": "sha256:" + "c" * 64}

    def test_fixed_workers_emit_footer_from_normal_error_and_probe_branches(self):
        root = Path(__file__).resolve().parents[1]
        interpreter = sys.executable
        binding = self.binding()
        fixture = root / "fixtures/runtime/fixture_worker.py"
        normal = subprocess.run([interpreter, str(fixture), "--scenario", "constraint:C01:good"],
                                input=canonical_bytes(binding), capture_output=True, check=False)
        self.assertEqual(normal.returncode, 0)
        self.assertIn(b"gah_generic_result", normal.stdout)
        prior, metrics = split_metrics_footer(normal.stderr)
        self.assertEqual(prior, b"")
        self.assertEqual(metrics["capture_status"], "CAPTURED")

        failed = subprocess.run([interpreter, str(fixture), "--scenario", "constraint:C01:good"],
                                input=b"{}", capture_output=True, check=False)
        self.assertEqual(failed.returncode, 2)
        prior, metrics = split_metrics_footer(failed.stderr)
        self.assertTrue(prior)
        self.assertEqual(metrics["capture_status"], "CAPTURED")

        probe = subprocess.run([interpreter, str(fixture), "--scenario", "probe:malformed"],
                               input=canonical_bytes(binding), capture_output=True, check=False)
        self.assertEqual(probe.stdout.replace(b"\r\n", b"\n"), b"not-json\n")
        prior, metrics = split_metrics_footer(probe.stderr)
        self.assertEqual(prior, b"")
        self.assertEqual(metrics["capture_status"], "CAPTURED")

        guardrail = root / "fixtures/llm/guardrail_worker.py"
        guardrail_env = dict(os.environ)
        guardrail_env["PYTHONPATH"] = str(root / "src")
        guardrail_error = subprocess.run([interpreter, str(guardrail)], input=b"{}", capture_output=True,
                                         check=False, env=guardrail_env)
        self.assertEqual(guardrail_error.returncode, 2)
        prior, metrics = split_metrics_footer(guardrail_error.stderr)
        self.assertEqual(prior, b"")
        self.assertEqual(metrics["capture_status"], "CAPTURED")

    def test_runner_capture_replay_and_conflict_are_bound_to_execution_and_sources(self):
        root = Path(__file__).resolve().parents[1]
        binding = self.binding()
        request = self.execution(binding)
        runner = object.__new__(DockerRunner)
        with tempfile.TemporaryDirectory() as folder:
            runner.journal_path = Path(folder) / "execution.sqlite"
            runner.lock = {"worker_digest": "a" * 64}
            child = root / "fixtures/runtime/fixture_worker.py"
            captured = capture_bounded(
                [sys.executable, str(child), "--scenario", "probe:malformed"], timeout=10,
                cwd=root, environment=dict(os.environ), input_bytes=canonical_bytes(binding),
            )
            self.assertIsNone(captured.reason)
            self.assertEqual(captured.stdout.replace(b"\r\n", b"\n"), b"not-json\n")
            record = {**request, "container_name": "gah-" + "e" * 32, "owner_token": "f" * 32,
                      "run_deadline": 1, "timeout_seconds": 1, "container_id": "a" * 64, "state": "STOPPED"}
            rest = runner._record_worker_metrics(binding, captured.stderr, record)
            self.assertEqual(rest, b"")
            journal = WorkerMetricsJournal(Path(folder) / "execution.sqlite.worker-metrics")
            saved = journal.metrics_for("run-1", "op-1", "b" * 64)
            self.assertEqual(saved["execution"]["binding"], binding)
            self.assertEqual(saved["source"]["worker_digest"], "a" * 64)
            self.assertEqual(saved["metrics"]["capture_status"], "CAPTURED")

            runner._record_worker_metrics(binding, captured.stderr, record)
            self.assertEqual(journal.metrics_for("run-1", "op-1", "b" * 64), saved)

            changed = dict(saved["metrics"])
            changed["memory_peak_bytes"] = 1024
            changed_footer = b"\nGAH-WORKER-METRICS-V1 " + canonical_bytes(changed) + b"\n"
            runner._record_worker_metrics(binding, changed_footer, record)
            self.assertEqual(journal.metrics_for("run-1", "op-1", "b" * 64), saved)
            with self.assertRaisesRegex(ValueError, "CORRUPT_OR_MISMATCH"):
                journal.metrics_for("run-1", "op-1", "e" * 64)

            missing_binding = self.binding("run-2", "op-2")
            missing_record = {**self.execution(missing_binding), "container_name": "gah-" + "e" * 32,
                              "owner_token": "f" * 32, "run_deadline": 1, "timeout_seconds": 1,
                              "container_id": "a" * 64, "state": "STOPPED"}
            runner._record_worker_metrics(missing_binding, b"", missing_record)
            missing = journal.metrics_for("run-2", "op-2", "b" * 64)
            self.assertEqual(missing["metrics"]["capture_status"], "MISSING")
            self.assertFalse(missing["metrics"]["valid_for_slo"])

    def test_sidecar_survives_reopen_and_only_accepts_identical_replay(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "metrics"
            metrics = empty_worker_metrics("MISSING")
            first = WorkerMetricsJournal(path)
            binding = self.binding()
            execution = self.execution(binding)
            source = {key: binding[key] for key in ("fixture_digest", "evaluator_digest", "adapter_digest", "policy_digest", "target_digest")}
            source["worker_digest"] = "a" * 64
            saved = first.save(execution, source, metrics)
            self.assertEqual(first.metrics_for("run-1", "op-1", "b" * 64)["artifact_digest"], saved["artifact_digest"])
            first.close()
            reopened = WorkerMetricsJournal(path)
            self.assertEqual(reopened.save(execution, source, metrics)["artifact_digest"], saved["artifact_digest"])
            with self.assertRaisesRegex(ValueError, "SAVE_FAILED"):
                reopened.save(execution, source, empty_worker_metrics("INVALID"))
            self.assertIsNone(reopened.metrics_for("run-2", "op-2"))


if __name__ == "__main__":
    import unittest
    unittest.main()
