"""通常LLM監督の並列上限、呼出主体、故障後の停止を検査する。"""
from pathlib import Path
import sys
import threading
import unittest
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gah.llm_supervised_run import LlmSupervisor
from gah.supervised_run import SupervisorError


class Pipeline(LlmSupervisor):
    def __init__(self, limit=2, count=6, *, previous=(), fail=None, barrier=False):
        self.bound = {"policy": {"profiles": {"full": {"concurrent_evaluations": limit}}}}
        self.manifest = {"profile": "full"}
        self.entries = [(str(i), {}, {}) for i in range(count)]
        self.previous = set(previous)
        self.fail = fail
        self.coordinator = threading.get_ident()
        self.clock = lambda: 1001
        self.lock = threading.Lock()
        self.active = self.maximum = 0
        self.started = []
        self.finished = []
        self.resumed = []
        self.worker_threads = set()
        self.barrier = threading.Barrier(limit) if barrier else None

    def on_coordinator(self):
        if threading.get_ident() != self.coordinator:
            raise AssertionError("AUTHORITY_OR_CHECKPOINT_OFF_COORDINATOR")

    def has_previous_operation(self, op):
        self.on_coordinator()
        return op in self.previous

    def begin_operation(self, op, entry, record):
        self.on_coordinator()
        if op not in self.previous:
            raise AssertionError("UNEXPECTED_RECOVERY")
        self.resumed.append(op)

    def begin_new_operation(self, op, entry, record):
        self.on_coordinator()
        self.owner = {"owner_epoch": int(op) + 11}
        return dict(self.owner)

    def prepare_operation(self, op, entry, record, status):
        self.on_coordinator()
        return 1000

    def execute_runner(self, op, entry, record, epoch):
        if threading.get_ident() == self.coordinator:
            raise AssertionError("WORKER_NOT_PARALLEL")
        with self.lock:
            self.active += 1
            self.maximum = max(self.maximum, self.active)
            self.started.append(op)
            self.worker_threads.add(threading.get_ident())
        try:
            if self.barrier is not None:
                self.barrier.wait(timeout=5)
            if op == self.fail or self.fail == "*":
                raise RuntimeError("WORKER_FAILED")
            return {"operation_id": op, "owner_epoch": epoch}
        finally:
            with self.lock:
                self.active -= 1

    def finish_operation(self, op, entry, record, epoch, started, receipt, *, finished=None):
        self.on_coordinator()
        if (receipt != {"operation_id": op, "owner_epoch": int(op) + 11}
                or epoch != int(op) + 11 or started != 1000 or finished != 1001):
            raise AssertionError("OPERATION_BINDING_OR_TIMING_CHANGED")
        self.finished.append(op)


class LlmParallelSupervisionTests(unittest.TestCase):
    def test_worker_overlap_respects_limit_and_coordinator_keeps_writes(self):
        run = Pipeline(barrier=True)
        run.run_entries()
        self.assertEqual(run.maximum, 2)
        self.assertEqual(sorted(run.started), [str(i) for i in range(6)])
        self.assertEqual(sorted(run.finished), sorted(run.started))
        self.assertEqual(len(run.worker_threads), 2)
        self.assertEqual(run.active, 0)

    def test_policy_can_reduce_concurrency_to_one(self):
        run = Pipeline(limit=1, count=3)
        run.run_entries()
        self.assertEqual(run.maximum, 1)
        self.assertEqual(run.finished, ["0", "1", "2"])

    def test_full_limit_four_is_bounded(self):
        run = Pipeline(limit=4, count=8, barrier=True)
        run.run_entries()
        self.assertEqual(run.maximum, 4)
        self.assertEqual(len(set(run.finished)), 8)

    def test_invalid_limit_starts_nothing(self):
        for limit in (True, 0, 5, "2"):
            with self.subTest(limit=limit):
                run = Pipeline(limit=limit)
                with self.assertRaisesRegex(SupervisorError, "CONCURRENCY_LIMIT"):
                    run.run_entries()
                self.assertEqual(run.started, [])

    def test_saved_operation_uses_recovery_without_worker_reexecution(self):
        run = Pipeline(count=4, previous=("0", "2"))
        run.run_entries()
        self.assertEqual(run.resumed, ["0", "2"])
        self.assertEqual(sorted(run.started), ["1", "3"])
        self.assertEqual(sorted(run.finished), ["1", "3"])

    def test_worker_failure_drains_inflight_and_stops_new_dispatch(self):
        run = Pipeline(fail="*", barrier=True)
        with self.assertRaisesRegex(SupervisorError, "EXECUTION_UNAVAILABLE"):
            run.run_entries()
        self.assertEqual(set(run.started), {"0", "1"})
        self.assertEqual(run.active, 0)


if __name__ == "__main__":
    unittest.main()
