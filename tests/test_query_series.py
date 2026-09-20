import hashlib
import unittest
import time

from gah.contracts import ContractError
from gah.query_series import (CASE_COUNTS, HISTORY_COUNTS, SURFACES,
    make_query_series_plan, run_query_series, validate_query_series_plan)
from gah.productization import content_ref
from gah.wire import canonical_bytes


class Clock:
    def __init__(self): self.value = 0
    def advance(self, amount): self.value += amount
    def __call__(self):
        value = self.value
        self.value += 1
        return value


class Session:
    def __init__(self, adapter, lifecycle):
        self.adapter, self.lifecycle = adapter, lifecycle
    def query(self, request):
        clock = getattr(self.adapter, "clock", None)
        if clock is not None:
            clock.advance(100 if request["phase"] == "warmup" else 1)
        self.adapter.requests += 1
        if self.adapter.cancel_at == self.adapter.requests:
            return {"exit_code": 3}
        if self.adapter.fail_at == self.adapter.requests:
            return {"exit_code": 1}
        if self.adapter.bad_at == self.adapter.requests:
            return {"exit_code": True}
        return {"exit_code": 0}
    def close(self):
        self.adapter.closed += 1
        if self.adapter.close_fails and self.adapter.closed == 1:
            raise RuntimeError("cleanup")


class Adapter:
    def __init__(self, **kw):
        self.__dict__.update({"requests": 0, "opens": [], "closed": 0, "cancel_at": None,
            "fail_at": None, "bad_at": None, "close_fails": False, **kw})
    def open_session(self, **kw):
        self.opens.append(dict(kw))
        return Session(self, kw["lifecycle"])


def mkplan():
    src = content_ref("snapshot_manifest", "src", {"value": 1})
    req = content_ref("snapshot_manifest", "req", {"value": 2})
    now = int(time.time())
    return make_query_series_plan(identifier="series", source_ref=src,
        requirements_ref=req, created_at=now, expires_at=now+3600)


def sink_for(saved):
    def save(cell):
        ref = content_ref(cell["kind"], cell["id"], cell)
        saved.append(cell)
        return {"schema_version": 1, "kind": "benchmark_query_series_cell_receipt",
                "series_plan_ref": cell["plan_ref"], "cell_ref": ref}
    return save


class QuerySeriesTests(unittest.TestCase):
    def test_full_matrix_counts_warmup_budget_and_mutation_isolation(self):
        plan = mkplan(); adapter = Adapter(); saved = []; clock = Clock(); adapter.clock = clock
        result = run_query_series(plan, adapter, sink_for(saved), monotonic_clock=clock)
        self.assertEqual((result["planned_cell_count"], result["stored_cell_count"]), (27, 27))
        self.assertEqual(result["observation_count"], 27 * 108)
        self.assertEqual(len(adapter.opens), 27 * 4)
        self.assertEqual(adapter.requests, 27 * 108)
        self.assertEqual(adapter.closed, len(adapter.opens))
        first = saved[0]
        self.assertEqual(len(first["observations"]), 108)
        self.assertFalse(first["lifecycle_verified"])
        self.assertEqual(first["warm_summary"]["count"], 100)
        self.assertNotIn(4, [o["payload"]["iteration"] for o in first["observations"]
                             if o["payload"]["iteration"] > 8])
        self.assertEqual(first["warmup_wall_ns"], 505)
        self.assertEqual(first["warm_summary"]["p95_ns"], 2)
        self.assertEqual(result["ratios"][0]["ratio"], {"numerator": 1, "denominator": 1})
        self.assertFalse(result["valid_for_slo"]); self.assertFalse(result["ci_eligible"])
        saved[0]["observations"][0]["payload"]["wall_ns"] = 999
        self.assertNotEqual(content_ref(first["kind"], first["id"], first),
                            result["ordered_cell_refs"][0])

    def test_cold_wall_includes_open_but_warm_query_does_not(self):
        class DelayedOpenAdapter(Adapter):
            def open_session(self, **kw):
                session = super().open_session(**kw)
                if kw["lifecycle"] == "cold":
                    self.clock.advance(100)
                return session
        clock = Clock(); adapter = DelayedOpenAdapter(); adapter.clock = clock; saved = []
        run_query_series(mkplan(), adapter, sink_for(saved), monotonic_clock=clock)
        cell = saved[0]
        self.assertEqual(cell["cold_summary"]["count"], 3)
        self.assertGreater(cell["cold_summary"]["p95_ns"], 100)
        self.assertEqual(cell["warm_summary"]["p95_ns"], 2)

    def test_cold_open_clock_failure_closes_and_stops(self):
        class OpeningClock(Clock):
            broken = False
            def __call__(self):
                if self.broken: raise OSError("clock")
                return super().__call__()
        class BreakDuringOpen(Adapter):
            def open_session(self, **kw):
                session = super().open_session(**kw)
                self.clock.broken = True
                return session
        clock = OpeningClock(); adapter = BreakDuringOpen(); adapter.clock = clock; saved = []
        result = run_query_series(mkplan(), adapter, sink_for(saved), monotonic_clock=clock)
        self.assertEqual((adapter.requests, adapter.closed, len(adapter.opens)), (0, 1, 1))
        self.assertEqual(saved[0]["observations"][0]["payload"]["failure_class"], "MEASUREMENT_ERROR")
        self.assertIsNone(saved[0]["observations"][0]["payload"]["wall_ns"])
        self.assertEqual(result["cells"][1]["status"], "NOT_STARTED")

    def test_slow_cold_and_warm_observations_are_retained_without_timeout(self):
        class SlowSession(Session):
            def query(self, request):
                result = super().query(request)
                self.adapter.clock.advance(2_100_000_000)
                return result
        class SlowAdapter(Adapter):
            def open_session(self, **kw):
                self.opens.append(dict(kw))
                if kw["lifecycle"] == "cold":
                    self.clock.advance(3_100_000_000)
                return SlowSession(self, kw["lifecycle"])
        clock = Clock(); adapter = SlowAdapter(); adapter.clock = clock; saved = []
        result = run_query_series(mkplan(), adapter, sink_for(saved), monotonic_clock=clock)
        self.assertEqual(result["stored_cell_count"], 27)
        self.assertEqual((adapter.requests, len(adapter.opens), adapter.closed), (2916, 108, 108))
        first = saved[0]
        cold = [x["payload"] for x in first["observations"] if x["payload"]["warmness"] == "cold"]
        warm = [x["payload"] for x in first["observations"] if x["payload"]["warmness"] == "warm"]
        self.assertEqual(len(cold), 3)
        self.assertEqual(len(warm), 105)
        self.assertGreater(min(x["wall_ns"] for x in cold), 5_000_000_000)
        self.assertGreater(first["warm_summary"]["p95_ns"], 2_000_000_000)
        self.assertTrue(all(x["failure_class"] == "NONE" for x in cold + warm))
        self.assertEqual(first["status"], "COMPLETED")
        self.assertIsNone(result["stop_reason"])

    def test_cancel_and_cleanup_failure_stop_later_sessions(self):
        adapter = Adapter(cancel_at=1); saved = []
        result = run_query_series(mkplan(), adapter, sink_for(saved), monotonic_clock=Clock())
        self.assertEqual(len(adapter.opens), 1)
        self.assertEqual(result["stored_cell_count"], 1)
        self.assertEqual(saved[0]["not_run_iterations"], list(range(2, 109)))
        self.assertEqual(result["cells"][1]["status"], "NOT_STARTED")
        adapter = Adapter(close_fails=True); saved = []
        result = run_query_series(mkplan(), adapter, sink_for(saved), monotonic_clock=Clock())
        self.assertEqual(len(adapter.opens), 1)
        self.assertEqual(result["stored_cell_count"], 1)
        self.assertEqual(saved[0]["cleanup_errors"], ["CLEANUP_FAILED"])
        self.assertEqual(result["cells"][1]["status"], "NOT_STARTED")

    def test_persistence_receipt_mismatch_and_sink_mutation_are_isolated(self):
        adapter = Adapter(); seen = []
        def mutate(cell):
            ref = content_ref(cell["kind"], cell["id"], cell)
            cell["surface"] = "report"
            seen.append((ref, cell))
            return {"schema_version": 1, "kind": "benchmark_query_series_cell_receipt",
                    "series_plan_ref": cell["plan_ref"], "cell_ref": ref}
        result = run_query_series(mkplan(), adapter, mutate, monotonic_clock=Clock())
        self.assertEqual(len(adapter.opens), 4)
        self.assertEqual(result["stored_cell_count"], 0)
        self.assertEqual(result["cells"][0]["persistence_error"], "RECEIPT_MISMATCH")
        self.assertTrue(all(row["status"] == "NOT_STARTED" for row in result["cells"][1:]))
        self.assertNotEqual(seen[0][0], content_ref(seen[0][1]["kind"], seen[0][1]["id"], seen[0][1]))

    def test_failed_response_does_not_count_toward_warm_hundred(self):
        adapter = Adapter(fail_at=9); saved = []
        result = run_query_series(mkplan(), adapter, sink_for(saved), monotonic_clock=Clock())
        self.assertEqual(len(adapter.opens), 27 * 4)
        self.assertEqual(saved[0]["warm_summary"]["count"], 99)
        self.assertIsNone(saved[0]["warm_summary"]["p95_ns"])
        self.assertFalse(saved[0]["valid_for_slo"])
        self.assertEqual(result["stored_cell_count"], 27)

    def test_boolean_or_unknown_response_is_failure_not_success(self):
        adapter = Adapter(bad_at=1); saved = []
        result = run_query_series(mkplan(), adapter, sink_for(saved), monotonic_clock=Clock())
        self.assertEqual(saved[0]["observations"][0]["payload"]["failure_class"], "TARGET_ERROR")
        self.assertEqual(saved[0]["status"], "INCOMPLETE")
        self.assertEqual(saved[0]["warm_summary"]["count"], 100)
        self.assertEqual(result["status"], "INCOMPLETE")
        self.assertIsNone(result["ratios"][0]["ratio"])
        self.assertIsNotNone(result["ratios"][1]["ratio"])

    def test_plan_rejects_unknown_bool_and_duplicate_dimensions(self):
        plan = mkplan()
        for mutate in (
            lambda x: x.update(extra=1),
            lambda x: x["payload"].update(page_size=True),
            lambda x: x["payload"].update(history_run_counts=[1, 1, 100]),
            lambda x: x["payload"].update(case_counts=[True, 800, 1600]),
        ):
            candidate = {**plan, "payload": dict(plan["payload"])}
            mutate(candidate)
            with self.assertRaises(ContractError): validate_query_series_plan(candidate)
        with self.assertRaises(ContractError):
            make_query_series_plan(identifier="x", source_ref=plan["source_ref"],
                requirements_ref=plan["requirements_ref"], page_size=101)

    def test_clock_failure_and_sink_exception_are_retained_and_stop(self):
        plan = mkplan(); original = canonical_bytes(plan)
        class BadClock:
            calls = 0
            def __call__(self):
                self.calls += 1
                if self.calls == 2: raise OSError("clock")
                return self.calls
        adapter = Adapter(); saved = []
        result = run_query_series(plan, adapter, sink_for(saved), monotonic_clock=BadClock())
        self.assertEqual(len(adapter.opens), 0)
        self.assertEqual(result["stop_reason"], "CLOCK_UNAVAILABLE_OR_ROLLBACK")
        self.assertEqual(result["stored_cell_count"], 1)
        self.assertEqual(result["cells"][0]["status"], "INCOMPLETE")
        self.assertEqual(canonical_bytes(plan), original)
        adapter = Adapter(); calls = []
        def broken_sink(cell):
            calls.append(cell["id"])
            raise OSError("sink")
        result = run_query_series(mkplan(), adapter, broken_sink, monotonic_clock=Clock())
        self.assertEqual(calls, ["query-candidate-400-1"])
        self.assertEqual(len(adapter.opens), 4)
        self.assertEqual(result["stored_cell_count"], 0)
        self.assertTrue(all(row["status"] == "NOT_STARTED" for row in result["cells"][1:]))

    def test_artifact_is_cell_bounded_and_under_wire_limit(self):
        saved = []
        clock = Clock(); adapter = Adapter(); adapter.clock = clock
        run_query_series(mkplan(), adapter, sink_for(saved), monotonic_clock=clock)
        for cell in saved:
            raw = canonical_bytes(cell)
            self.assertLessEqual(len(raw), 1_048_576)
            self.assertEqual(len(cell["observations"]), 108)
            self.assertEqual(len(cell["not_run_iterations"]), 0)
            self.assertEqual(len(cell["intervals"]), 108 + 8)


if __name__ == "__main__":
    unittest.main()
