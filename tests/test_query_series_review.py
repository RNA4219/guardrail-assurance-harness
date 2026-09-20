"""系列測定器の監督側反例。合成clock/adapterは製品SLO証拠ではない。"""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from gah.contracts import ContractError, require_id
from gah.productization import content_ref
from gah.query_series import run_query_series, validate_query_series_plan
from tests.test_query_series import Adapter, Clock, Session, mkplan, sink_for


class QuerySeriesReviewTests(unittest.TestCase):
    def test_expired_or_wrong_requirement_plan_starts_no_session(self):
        plan = mkplan()
        plan.update(created_at=100, expires_at=200)
        adapter = Adapter()
        with patch("gah.query_series.time.time", return_value=201), self.assertRaises(ContractError):
            run_query_series(plan, adapter, sink_for([]), monotonic_clock=Clock())
        self.assertEqual(adapter.opens, [])
        plan = mkplan()
        plan["requirement_ids"] = ["GAH-PR01"]
        with self.assertRaises(ContractError):
            validate_query_series_plan(plan)

    def test_clock_failure_during_query_closes_active_session_and_stops(self):
        for broken in ("exception", "rollback", "boolean"):
            with self.subTest(broken=broken):
                class QueryClock(Clock):
                    bad = False
                    def __call__(self):
                        if self.bad:
                            if broken == "exception":
                                raise OSError("clock unavailable")
                            return -1 if broken == "rollback" else True
                        return super().__call__()
                clock = QueryClock()
                adapter = Adapter()
                class ClockBreakingSession(Session):
                    def query(self, request):
                        value = super().query(request)
                        clock.bad = True
                        return value
                def open_session(**kwargs):
                    adapter.opens.append(dict(kwargs))
                    return ClockBreakingSession(adapter, kwargs["lifecycle"])
                adapter.open_session = open_session
                saved = []
                result = run_query_series(mkplan(), adapter, sink_for(saved), monotonic_clock=clock)
                self.assertEqual(len(adapter.opens), 1)
                self.assertEqual(adapter.closed, 1)
                self.assertEqual(adapter.requests, 1)
                self.assertEqual(result["status"], "INCOMPLETE")
                observation = saved[0]["observations"][0]["payload"]
                self.assertIsNone(observation["wall_ns"])
                self.assertEqual(observation["failure_class"], "MEASUREMENT_ERROR")
                self.assertFalse(observation["valid_for_slo"])
                self.assertTrue(all(row["status"] == "NOT_STARTED" for row in result["cells"][1:]))

    def test_warm_open_failure_does_not_fabricate_attempted_queries(self):
        adapter = Adapter()
        original = adapter.open_session
        def open_session(**kwargs):
            if kwargs["lifecycle"] == "warm":
                adapter.opens.append(dict(kwargs))
                raise OSError("cannot open warm session")
            return original(**kwargs)
        adapter.open_session = open_session
        saved = []
        result = run_query_series(mkplan(), adapter, sink_for(saved), monotonic_clock=Clock())
        cell = saved[0]
        self.assertEqual(cell["attempted_observation_count"], 3)
        self.assertEqual(len(cell["observations"]), 3)
        self.assertEqual(cell["not_run_iterations"], list(range(4, 109)))
        self.assertIsNone(cell["warm_summary"]["p95_ns"])
        self.assertEqual(result["status"], "INCOMPLETE")
        self.assertEqual(adapter.closed, 3 * len(saved))

    def test_original_plan_mutation_cannot_change_active_series_bindings(self):
        plan = mkplan()
        expected = content_ref(plan["kind"], plan["id"], plan)
        adapter = Adapter()
        class MutatingSession(Session):
            def query(self, request):
                plan["payload"]["case_counts"][0] = 999
                plan["source_ref"]["digest"] = "0" * 64
                return super().query(request)
        def open_session(**kwargs):
            adapter.opens.append(dict(kwargs))
            return MutatingSession(adapter, kwargs["lifecycle"])
        adapter.open_session = open_session
        saved = []
        result = run_query_series(plan, adapter, sink_for(saved), monotonic_clock=Clock())
        self.assertEqual(result["plan_ref"], expected)
        self.assertEqual(result["stored_cell_count"], 27)
        self.assertTrue(all(cell["plan_ref"] == expected for cell in saved))
        self.assertEqual({item["case_count"] for item in adapter.opens}, {400, 800, 1600})
        self.assertTrue(all(item["plan_ref"] == expected for cell in saved for item in cell["observations"]))

    def test_maximum_plan_identifier_produces_valid_result_identifier(self):
        plan = mkplan()
        plan["id"] = "p" * 64
        result = run_query_series(plan, Adapter(), sink_for([]), monotonic_clock=Clock())
        require_id(result["id"])
        self.assertLessEqual(len(result["id"]), 64)
        self.assertEqual(result["status"], "COMPLETED")


    def test_warmup_and_warm_cancellation_issue_no_later_queries(self):
        for cancel_at in (4, 9):
            with self.subTest(cancel_at=cancel_at):
                adapter = Adapter(cancel_at=cancel_at)
                saved = []
                result = run_query_series(mkplan(), adapter, sink_for(saved), monotonic_clock=Clock())
                self.assertEqual(adapter.requests, cancel_at)
                self.assertEqual(adapter.closed, 4)
                self.assertEqual(len(adapter.opens), 4)
                self.assertEqual(result["status"], "INCOMPLETE")
                self.assertEqual(result["cells"][1]["status"], "NOT_STARTED")
                self.assertEqual(saved[0]["not_run_iterations"], list(range(cancel_at+1, 109)))
                cancelled = saved[0]["observations"][cancel_at-1]["payload"]
                self.assertEqual(cancelled["failure_class"], "CANCELLED_STOP_UNCONFIRMED")


if __name__ == "__main__":
    unittest.main()
