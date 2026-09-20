from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gah.contracts import ContractError
from gah.productization import content_ref
from gah.query_series import (
    DEFAULT_EXECUTION_BUDGET_NS,
    MAX_EXECUTION_BUDGET_NS,
    make_query_series_plan,
    run_query_series,
)


def _plan():
    source = content_ref("snapshot_manifest", "budget-source", {"v": 1})
    requirements = content_ref("snapshot_manifest", "budget-requirements", {"v": 2})
    now = int(time.time())
    return make_query_series_plan(identifier="budget-series", source_ref=source,
        requirements_ref=requirements, created_at=now, expires_at=now + 3600)


class FakeClock:
    def __init__(self, *, fail_at=None):
        self.value = 0
        self.calls = 0
        self.fail_at = fail_at

    def __call__(self):
        self.calls += 1
        if self.calls == self.fail_at:
            raise OSError("clock")
        self.value += 1
        return self.value

    def advance(self, amount):
        self.value += amount


class FakeSession:
    def __init__(self, adapter):
        self.adapter = adapter

    def query(self, request):
        self.adapter.requests += 1
        if self.adapter.timeout_at == self.adapter.requests:
            raise TimeoutError("transport")
        if self.adapter.slow_iteration == request["iteration"]:
            self.adapter.clock.advance(self.adapter.advance_ns)
        return {"exit_code": 0}

    def close(self):
        self.adapter.closed += 1


class FakeAdapter:
    def __init__(self, clock, *, open_advance=0, timeout_at=None,
                 slow_iteration=None, advance_ns=0):
        self.clock = clock
        self.open_advance = open_advance
        self.timeout_at = timeout_at
        self.slow_iteration = slow_iteration
        self.advance_ns = advance_ns
        self.opens = 0
        self.requests = 0
        self.closed = 0

    def open_session(self, **kwargs):
        self.opens += 1
        if self.open_advance:
            self.clock.advance(self.open_advance)
        return FakeSession(self)


def _sink(saved, *, advance=0, clock=None):
    def persist(cell):
        saved.append(cell)
        if advance:
            clock.advance(advance)
        ref = content_ref(cell["kind"], cell["id"], cell)
        return {"schema_version": 1, "kind": "benchmark_query_series_cell_receipt",
                "series_plan_ref": cell["plan_ref"], "cell_ref": ref}
    return persist


class QuerySeriesBudgetTests(unittest.TestCase):
    def test_budget_is_strictly_bounded_and_default_is_reported(self):
        plan = _plan()
        for invalid in (True, False, 0, -1, MAX_EXECUTION_BUDGET_NS + 1):
            with self.subTest(invalid=invalid):
                adapter = FakeAdapter(FakeClock())
                with self.assertRaises(ContractError):
                    run_query_series(plan, adapter, _sink([]), execution_budget_ns=invalid)
                self.assertEqual(adapter.opens, 0)
        self.assertEqual(run_query_series.__kwdefaults__["execution_budget_ns"],
                         DEFAULT_EXECUTION_BUDGET_NS)

    def test_open_consuming_budget_closes_and_preserves_all_unrun_slots(self):
        clock = FakeClock()
        adapter = FakeAdapter(clock, open_advance=60)
        saved = []
        result = run_query_series(_plan(), adapter, _sink(saved), monotonic_clock=clock,
                                  execution_budget_ns=50)
        self.assertEqual((adapter.opens, adapter.requests, adapter.closed), (1, 0, 1))
        self.assertEqual(result["stop_reason"], "SERIES_BUDGET_EXHAUSTED")
        self.assertTrue(result["execution_budget_exceeded"])
        self.assertEqual(result["execution_budget_ns"], 50)
        self.assertGreater(result["execution_overrun_ns"], 0)
        self.assertEqual(result["status"], "INCOMPLETE")
        self.assertEqual(result["planned_observation_count"], 2916)
        self.assertEqual(result["attempted_observation_count"], 0)
        self.assertEqual(result["not_run_observation_count"], 2916)
        self.assertEqual(saved[0]["failure"], "SERIES_BUDGET_EXHAUSTED")
        self.assertEqual(saved[0]["attempted_observation_count"], 0)
        self.assertEqual(saved[0]["not_run_iterations"], list(range(1, 109)))
        self.assertEqual(result["cells"][1]["status"], "NOT_STARTED")
        self.assertEqual(result["cells"][1]["not_run_iterations"], list(range(1, 109)))
        self.assertEqual(len(result["cells"]), 27)

    def test_exact_budget_boundary_forbids_open_and_query_dispatch(self):
        plan = _plan()
        before_open = FakeClock()
        adapter = FakeAdapter(before_open)
        saved = []
        result = run_query_series(plan, adapter, _sink(saved), monotonic_clock=before_open,
                                  execution_budget_ns=3)
        self.assertEqual((adapter.opens, adapter.requests, adapter.closed), (0, 0, 0))
        self.assertEqual(result["stop_reason"], "SERIES_BUDGET_EXHAUSTED")
        self.assertTrue(result["execution_budget_exceeded"])
        self.assertEqual(saved[0]["not_run_iterations"], list(range(1, 109)))

        before_query = FakeClock()
        adapter = FakeAdapter(before_query)
        saved = []
        result = run_query_series(plan, adapter, _sink(saved), monotonic_clock=before_query,
                                  execution_budget_ns=5)
        self.assertEqual((adapter.opens, adapter.requests, adapter.closed), (1, 0, 1))
        self.assertEqual(result["stop_reason"], "SERIES_BUDGET_EXHAUSTED")
        self.assertTrue(result["execution_budget_exceeded"])
        # Cleanup and evidence persistence still run after the exact-boundary refusal.
        self.assertGreater(result["execution_overrun_ns"], 0)
        self.assertEqual(saved[0]["not_run_iterations"], list(range(1, 109)))

    def test_overrun_during_warm_query_keeps_observation_closes_and_stops(self):
        clock = FakeClock()
        adapter = FakeAdapter(clock, slow_iteration=9, advance_ns=600)
        saved = []
        result = run_query_series(_plan(), adapter, _sink(saved), monotonic_clock=clock,
                                  execution_budget_ns=500)
        cell = saved[0]
        self.assertEqual((adapter.opens, adapter.closed, adapter.requests), (4, 4, 9))
        self.assertEqual(cell["failure"], "SERIES_BUDGET_EXHAUSTED")
        self.assertEqual(cell["status"], "INCOMPLETE")
        self.assertEqual(cell["observations"][-1]["payload"]["iteration"], 9)
        self.assertEqual(cell["observations"][-1]["payload"]["failure_class"], "NONE")
        self.assertGreater(cell["observations"][-1]["payload"]["wall_ns"], 500)
        self.assertEqual(cell["not_run_iterations"], list(range(10, 109)))
        self.assertEqual(result["stop_reason"], "SERIES_BUDGET_EXHAUSTED")
        self.assertEqual(result["attempted_observation_count"], 9)
        self.assertEqual(result["not_run_observation_count"], 99 + 26 * 108)
        self.assertTrue(all(row["status"] == "NOT_STARTED" for row in result["cells"][1:]))

    def test_first_cell_persistence_overrun_is_recorded_in_result(self):
        clock = FakeClock()
        adapter = FakeAdapter(clock)
        saved = []
        result = run_query_series(_plan(), adapter,
            _sink(saved, advance=1_000_000_010, clock=clock),
            monotonic_clock=clock, execution_budget_ns=1_000_000_000)
        self.assertEqual((adapter.opens, adapter.requests, adapter.closed), (4, 108, 4))
        self.assertEqual(result["stored_cell_count"], 1)
        self.assertEqual(result["cells"][0]["status"], "COMPLETED")
        self.assertEqual(result["cells"][1]["status"], "NOT_STARTED")
        self.assertEqual(result["stop_reason"], "SERIES_BUDGET_EXHAUSTED")
        self.assertTrue(result["execution_budget_exceeded"])
        self.assertGreater(result["execution_overrun_ns"], 0)
        self.assertEqual(result["attempted_observation_count"], 108)
        self.assertEqual(result["not_run_observation_count"], 26 * 108)

    def test_final_cell_persistence_overrun_keeps_all_evidence_but_incompletes_series(self):
        clock = FakeClock()
        adapter = FakeAdapter(clock)
        saved = []
        calls = 0

        def persist(cell):
            nonlocal calls
            calls += 1
            saved.append(cell)
            if calls == 27:
                clock.advance(1_000_000_010)
            ref = content_ref(cell["kind"], cell["id"], cell)
            return {"schema_version": 1, "kind": "benchmark_query_series_cell_receipt",
                    "series_plan_ref": cell["plan_ref"], "cell_ref": ref}

        result = run_query_series(_plan(), adapter, persist, monotonic_clock=clock,
                                  execution_budget_ns=1_000_000_000)
        self.assertEqual(calls, 27)
        self.assertEqual((adapter.requests, adapter.closed), (2916, 108))
        self.assertEqual(result["stored_cell_count"], 27)
        self.assertEqual(result["planned_observation_count"], 2916)
        self.assertEqual(result["attempted_observation_count"], 2916)
        self.assertEqual(result["observation_count"], 2916)
        self.assertTrue(all(row["observation_count"] == 108 for row in result["cells"]))
        self.assertEqual(result["not_run_observation_count"], 0)
        self.assertEqual(result["status"], "INCOMPLETE")
        self.assertEqual(result["stop_reason"], "SERIES_BUDGET_EXHAUSTED")
        self.assertTrue(result["execution_budget_exceeded"])
        self.assertGreater(result["execution_overrun_ns"], 0)
        self.assertTrue(all(row["status"] == "COMPLETED" for row in result["cells"]))

    def test_cold_and_warm_session_open_timeout_is_observed_without_assumed_cleanup(self):
        class TimeoutOpenAdapter(FakeAdapter):
            def __init__(self, clock, timeout_lifecycle):
                super().__init__(clock)
                self.timeout_lifecycle = timeout_lifecycle

            def open_session(self, **kwargs):
                self.opens += 1
                if kwargs["lifecycle"] == self.timeout_lifecycle:
                    raise TimeoutError("open timeout")
                return FakeSession(self)

        for lifecycle in ("cold", "warm"):
            with self.subTest(lifecycle=lifecycle):
                clock = FakeClock()
                adapter = TimeoutOpenAdapter(clock, lifecycle)
                saved = []
                result = run_query_series(_plan(), adapter, _sink(saved),
                    monotonic_clock=clock, execution_budget_ns=10_000)
                cell = saved[0]
                self.assertEqual(result["stop_reason"], "TIMEOUT")
                self.assertEqual(cell["status"], "INCOMPLETE")
                self.assertEqual(cell["observations"][-1]["payload"]["failure_class"], "TIMEOUT")
                self.assertEqual(cell["observations"][-1]["payload"]["operation_status"], "INCOMPLETE")
                if lifecycle == "cold":
                    self.assertEqual((adapter.opens, adapter.requests, adapter.closed), (1, 0, 0))
                    self.assertEqual(cell["observations"][0]["payload"]["iteration"], 1)
                    self.assertEqual(cell["not_run_iterations"], list(range(2, 109)))
                else:
                    self.assertEqual((adapter.opens, adapter.requests, adapter.closed), (4, 3, 3))
                    self.assertEqual(cell["observations"][-1]["payload"]["iteration"], 4)
                    self.assertEqual(cell["not_run_iterations"], list(range(5, 109)))
                self.assertEqual(result["cells"][1]["status"], "NOT_STARTED")

    def test_timeout_and_cancel_take_precedence_over_simultaneous_budget_overrun(self):
        class TimedFailureSession(FakeSession):
            def query(self, request):
                self.adapter.requests += 1
                self.adapter.clock.advance(100)
                if self.adapter.clock_break:
                    self.adapter.clock.fail_at = self.adapter.clock.calls + 1
                if self.adapter.timeout_at:
                    raise TimeoutError("query timeout")
                return {"exit_code": 3}

        class TimedFailureAdapter(FakeAdapter):
            def __init__(self, clock, *, timeout_at=False, clock_break=False):
                super().__init__(clock, timeout_at=timeout_at)
                self.clock_break = clock_break

            def open_session(self, **kwargs):
                self.opens += 1
                return TimedFailureSession(self)

        for timeout, clock_break in ((True, False), (False, False), (True, True)):
            with self.subTest(timeout=timeout, clock_break=clock_break):
                clock = FakeClock()
                adapter = TimedFailureAdapter(clock, timeout_at=timeout,
                                              clock_break=clock_break)
                saved = []
                result = run_query_series(_plan(), adapter, _sink(saved),
                    monotonic_clock=clock, execution_budget_ns=6)
                expected = "TIMEOUT" if timeout else "CANCELLED_STOP_UNCONFIRMED"
                expected_stop = "CLOCK_UNAVAILABLE_OR_ROLLBACK" if clock_break else expected
                self.assertEqual(result["stop_reason"], expected_stop)
                if clock_break:
                    self.assertIsNone(result["execution_budget_exceeded"])
                else:
                    self.assertTrue(result["execution_budget_exceeded"])
                self.assertEqual(saved[0]["observations"][0]["payload"]["failure_class"], expected)
                self.assertEqual((adapter.opens, adapter.requests, adapter.closed), (1, 1, 1))
                self.assertEqual(saved[0]["not_run_iterations"], list(range(2, 109)))
                self.assertEqual(result["cells"][1]["status"], "NOT_STARTED")

    def test_cleanup_and_persistence_errors_keep_priority_over_budget(self):
        class CleanupFailureSession(FakeSession):
            def query(self, request):
                self.adapter.requests += 1
                self.adapter.clock.advance(100)
                return {"exit_code": 0}

            def close(self):
                self.adapter.closed += 1
                raise OSError("cleanup")

        class CleanupFailureAdapter(FakeAdapter):
            def open_session(self, **kwargs):
                self.opens += 1
                return CleanupFailureSession(self)

        clock = FakeClock()
        adapter = CleanupFailureAdapter(clock)
        saved = []
        result = run_query_series(_plan(), adapter, _sink(saved),
                                  monotonic_clock=clock, execution_budget_ns=6)
        self.assertEqual(result["stop_reason"], "CLEANUP_FAILED")
        self.assertTrue(result["execution_budget_exceeded"])
        self.assertEqual(saved[0]["observations"][0]["payload"]["failure_class"], "NONE")
        self.assertEqual(saved[0]["cleanup_errors"], ["CLEANUP_FAILED"])
        self.assertEqual((adapter.opens, adapter.requests, adapter.closed), (1, 1, 1))

        clock = FakeClock()
        adapter = FakeAdapter(clock)
        saved = []
        def broken_slow_sink(cell):
            clock.advance(1_000_000_010)
            raise OSError("persist")
        result = run_query_series(_plan(), adapter, broken_slow_sink,
                                  monotonic_clock=clock, execution_budget_ns=1_000_000_000)
        self.assertEqual(result["stop_reason"], "PERSIST_FAILED")
        self.assertTrue(result["execution_budget_exceeded"])
        self.assertEqual(result["stored_cell_count"], 0)
        self.assertEqual(result["cells"][1]["status"], "NOT_STARTED")

    def test_clock_failure_before_open_or_query_forbids_dispatch(self):
        plan = _plan()
        before_open = FakeClock(fail_at=4)
        adapter = FakeAdapter(before_open)
        saved = []
        result = run_query_series(plan, adapter, _sink(saved), monotonic_clock=before_open)
        self.assertEqual((adapter.opens, adapter.requests, adapter.closed), (0, 0, 0))
        self.assertEqual(result["stop_reason"], "CLOCK_UNAVAILABLE_OR_ROLLBACK")
        self.assertEqual(saved[0]["not_run_iterations"], list(range(1, 109)))
        self.assertEqual(saved[0]["attempted_observation_count"], 0)

        before_query = FakeClock(fail_at=6)
        adapter = FakeAdapter(before_query)
        saved = []
        result = run_query_series(plan, adapter, _sink(saved), monotonic_clock=before_query)
        self.assertEqual((adapter.opens, adapter.requests, adapter.closed), (1, 0, 1))
        self.assertEqual(result["stop_reason"], "CLOCK_UNAVAILABLE_OR_ROLLBACK")
        self.assertEqual(saved[0]["not_run_iterations"], list(range(1, 109)))
        self.assertEqual(saved[0]["attempted_observation_count"], 0)

    def test_timeout_error_is_observed_and_stops_without_claiming_cancel(self):
        clock = FakeClock()
        adapter = FakeAdapter(clock, timeout_at=1)
        saved = []
        result = run_query_series(_plan(), adapter, _sink(saved),
                                  monotonic_clock=clock, execution_budget_ns=10_000)
        payload = saved[0]["observations"][0]["payload"]
        self.assertEqual(payload["failure_class"], "TIMEOUT")
        self.assertEqual(payload["operation_status"], "INCOMPLETE")
        self.assertEqual(result["stop_reason"], "TIMEOUT")
        self.assertEqual((adapter.opens, adapter.requests, adapter.closed), (1, 1, 1))
        self.assertEqual(saved[0]["not_run_iterations"], list(range(2, 109)))
        self.assertNotEqual(result["stop_reason"], "CANCELLED_STOP_UNCONFIRMED")
        self.assertTrue(all(row["status"] == "NOT_STARTED" for row in result["cells"][1:]))


if __name__ == "__main__":
    unittest.main()
