"""planned fixed worker observations are joined to run checkpoints and journal."""
from copy import deepcopy
import hashlib
import sqlite3
import shutil
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))
from gah.execution_journal import ExecutionJournal, JournalError
from gah.supervisor_checkpoint import Checkpoint
from gah.worker_metrics import WorkerMetricsJournal
from gah.wire import canonical_bytes
from benchmark_worker_metrics import (collect_worker_observations, _open_execution_journal_readonly, _result)

spec = importlib.util.spec_from_file_location("regression_seed_helpers", ROOT / "tests/test_regression_integration.py")
seed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(seed)


class CollectorReadOnlyTests(unittest.TestCase):
    def test_totals_have_identical_keys_for_empty_incomplete_and_complete_results(self):
        expected = {"cpu_ns", "io_read_bytes", "io_write_bytes", "cgroup_memory_peak_sum_bytes"}
        empty = _result("run-x", 1)
        incomplete = _result("run-x", 1, missing=[{"operation_id": "op", "reason": "METRICS_MISSING"}])
        complete = _result("run-x", 1, artifacts=[{"operation_id": "op", "artifact_digest": "a" * 64}],
                           captured_count=1, totals={key: 1 for key in expected})
        self.assertEqual(set(empty["totals"]), expected)
        self.assertEqual(set(incomplete["totals"]), expected)
        self.assertEqual(set(complete["totals"]), expected)
        self.assertIsNone(empty["totals"]["cgroup_memory_peak_sum_bytes"])
        self.assertIsNone(incomplete["totals"]["cgroup_memory_peak_sum_bytes"])

    def test_readonly_journal_inspection_never_changes_or_creates_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            valid = root / "execution.sqlite"
            writer = ExecutionJournal(valid)
            writer.close()
            before = valid.read_bytes()
            db = _open_execution_journal_readonly(valid)
            try:
                self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 1)
                with self.assertRaises(sqlite3.OperationalError):
                    db.execute("CREATE TABLE unwanted(value INTEGER)")
            finally:
                db.close()
            self.assertEqual(valid.read_bytes(), before)
            self.assertEqual({item.name for item in root.iterdir()}, {"execution.sqlite"})

    def test_wal_database_and_sidecars_are_rejected_without_any_change(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            origin = root / "origin"
            origin.mkdir()
            source = origin / "execution.sqlite"
            writer = sqlite3.connect(source)
            try:
                mode = writer.execute("PRAGMA journal_mode=WAL").fetchone()[0]
                self.assertEqual(mode.lower(), "wal")
                writer.execute("CREATE TABLE sample(value INTEGER)")
                writer.execute("INSERT INTO sample VALUES(1)")
                writer.commit()
                writer.execute("SELECT * FROM sample").fetchall()
                self.assertTrue(Path(str(source) + "-wal").exists())
                target_dir = root / "wal-copy"
                target_dir.mkdir()
                wal_copy = target_dir / "execution.sqlite"
                shutil.copyfile(source, wal_copy)
                shutil.copyfile(Path(str(source) + "-wal"), Path(str(wal_copy) + "-wal"))
                self.assertEqual(wal_copy.read_bytes()[18:20], b"\x02\x02")
                before = {item.name: hashlib.sha256(item.read_bytes()).hexdigest()
                          for item in target_dir.iterdir()}
                with self.assertRaises(JournalError):
                    _open_execution_journal_readonly(wal_copy)
                after = {item.name: hashlib.sha256(item.read_bytes()).hexdigest()
                         for item in target_dir.iterdir()}
                self.assertEqual(after, before)
                self.assertNotIn("execution.sqlite-shm", after)
            finally:
                writer.close()

    def test_missing_or_invalid_journal_is_not_created_or_rewritten(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            missing = root / "missing.sqlite"
            with self.assertRaises(JournalError):
                _open_execution_journal_readonly(missing)
            self.assertFalse(missing.exists())
            invalid = root / "invalid.sqlite"
            invalid.write_bytes(b"not sqlite db; unchanged")
            before = invalid.read_bytes()
            with self.assertRaises(JournalError):
                _open_execution_journal_readonly(invalid)
            self.assertEqual(invalid.read_bytes(), before)
            self.assertEqual({item.name for item in root.iterdir()}, {"invalid.sqlite"})


class BenchmarkWorkerMetricsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.regression = seed.RegressionIntegrationTests("runTest")
        cls.regression.setUpClass()

    @classmethod
    def tearDownClass(cls):
        cls.regression.doClassCleanups()

    def setUp(self):
        self.regression.setUp()
        self.addCleanup(self.regression.doCleanups)
        self.prepared = self.regression.prepare("metrics-" + hashlib.sha256(self._testMethodName.encode("utf-8")).hexdigest()[:24])
        self.bound = self.prepared["bound_run"]
        self.manifest = self.bound["manifest"]
        self.request = {"schema_version": 1, "run_id": self.manifest["run_id"],
            "contract_series_id": "fixture-contract-series",
            "expected_contract_ref": self.manifest["contract_ref"], "trigger": "manual"}
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.checkpoints = Checkpoint(self.folder / "checkpoints")
        self.identity = {"request": self.request, "runtime_prefix": "fixture",
            "authority_image": "sha256:" + "a" * 64,
            "fixture_image": "sha256:" + "b" * 64,
            "fixture_digest": "c" * 64, "adapter_digest": "d" * 64,
            "isolation_digest": "e" * 64}
        self.checkpoints.put("identity", self.identity)
        self.checkpoints.put("response-prepare", self.prepared)
        self.entries = self.bound["plan"]["entries"]
        self.tag = "supervised-" + hashlib.sha256(canonical_bytes(self.request)).hexdigest()[:24]

    def _populate(self, *, omit=None, bad_sidecar_binding=None, replay=False):
        journal_path = self.folder / "execution.sqlite"
        execution_journal = ExecutionJournal(journal_path)
        metrics_journal = WorkerMetricsJournal(journal_path.with_name("execution.sqlite.worker-metrics"))
        records = self.prepared["materialization"]["manifest"]["records"]
        for index, entry in enumerate(self.entries):
            op = f"{self.tag}-op-{index}"
            binding = {"run_id": self.manifest["run_id"], "operation_id": op, "owner_epoch": 1,
                "contract_digest": self.manifest["contract_ref"]["digest"],
                "target_digest": entry["target_ref"]["digest"], "obligation_id": entry["obligation_id"],
                "case_id": entry["case_id"], "trial_id": entry["trial_id"],
                "stage_id": entry["stage_ids"][0], "fixture_digest": self.identity["fixture_digest"],
                "adapter_digest": self.identity["adapter_digest"],
                "policy_digest": self.manifest["policy_ref"]["digest"],
                "evaluator_digest": entry["evaluator_ref"]["digest"],
                "isolation_digest": self.identity["isolation_digest"]}
            self.checkpoints.put("start-" + op, {"binding": binding, "container_name": "test"})
            record = next(r for r in records if all(r[k] == entry[k] for k in ("obligation_id", "case_id", "trial_id", "variant")))
            image_id = self.identity["fixture_image"]
            row = execution_journal.begin(binding, record["scenario"], image_id,
                run_deadline=self.manifest["deadline"], timeout_seconds=120)
            execution_journal.advance(self.manifest["run_id"], op, row["owner_token"], "CREATED", container_id=(f"{index+1:064x}"))
            execution_journal.advance(self.manifest["run_id"], op, row["owner_token"], "STOPPED", container_id=(f"{index+1:064x}"))
            if index == omit:
                continue
            sidecar_binding = dict(binding)
            if index == bad_sidecar_binding:
                sidecar_binding["target_digest"] = "f" * 64
            execution = {"run_id": self.manifest["run_id"], "operation_id": op,
                "request_digest": row["request_digest"], "binding": sidecar_binding,
                "scenario": row["scenario"], "image_id": image_id}
            source = {"fixture_digest": self.identity["fixture_digest"],
                "evaluator_digest": entry["evaluator_ref"]["digest"],
                "adapter_digest": self.identity["adapter_digest"],
                "policy_digest": self.manifest["policy_ref"]["digest"],
                "target_digest": sidecar_binding["target_digest"],
                "worker_digest": self.identity["fixture_digest"]}
            metrics = {"schema_version": 1, "kind": "gah_worker_metrics",
                "capture_scope": "container_cgroup_until_worker_exit", "capture_status": "CAPTURED",
                "cpu_ns": index + 10, "rss_peak_bytes": None, "memory_peak_bytes": index + 30,
                "io_read_bytes": index + 1, "io_write_bytes": index + 2, "valid_for_slo": False}
            metrics_journal.save(execution, source, metrics)
            if replay and index == 0:
                self.assertTrue(metrics_journal.save(execution, source, metrics)["replayed"])
        execution_journal.close()

    def test_missing_observation_counts_against_plan_and_nulls_totals(self):
        self._populate(omit=0)
        result = collect_worker_observations(self.folder, self.request)
        self.assertEqual(result["planned_count"], 30)
        self.assertEqual(result["captured_count"], 29)
        self.assertEqual(result["missing"][0]["reason"], "METRICS_MISSING")
        self.assertIsNone(result["totals"]["cpu_ns"])
        self.assertFalse(result["capture_complete"])
        self.assertFalse(result["slo_eligible"])

    def test_sidecar_with_other_binding_is_rejected(self):
        self._populate(bad_sidecar_binding=0)
        result = collect_worker_observations(self.folder, self.request)
        self.assertEqual(result["captured_count"], 29)
        self.assertEqual(result["missing"][0]["reason"], "METRICS_EXECUTION_MISMATCH")

    def test_replay_does_not_duplicate_operation_and_full_plan_sums(self):
        self._populate(replay=True)
        result = collect_worker_observations(self.folder, self.request)
        self.assertEqual(result["status"], "CAPTURE_COMPLETE")
        self.assertEqual(result["planned_count"], 30)
        self.assertEqual(result["captured_count"], 30)
        self.assertEqual(len(result["artifacts"]), 30)
        self.assertEqual(result["totals"]["cpu_ns"], sum(range(10, 40)))
        self.assertEqual(result["totals"]["io_read_bytes"], sum(range(1, 31)))
        self.assertEqual(result["totals"]["io_write_bytes"], sum(range(2, 32)))
        self.assertEqual(result["totals"]["cgroup_memory_peak_sum_bytes"], sum(range(30, 60)))
        self.assertIsNone(result["rss_group_peak_bytes"])
        self.assertFalse(result["true_group_peak_measured"])
        self.assertFalse(result["ci_eligible"])

    def test_absent_folder_does_not_create_anything(self):
        absent = self.folder / "absent"
        result = collect_worker_observations(absent, self.request)
        self.assertEqual(result["missing"][0]["reason"], "RUN_FOLDER_MISSING")
        self.assertFalse(absent.exists())


if __name__ == "__main__":
    unittest.main()
