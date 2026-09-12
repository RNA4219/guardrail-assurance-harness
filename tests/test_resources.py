"""ResourceBookの一括予約、停止観測、世代引継ぎ、費用境界を検査する。"""

from copy import deepcopy
import sqlite3
from pathlib import Path
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gah.policy import initial_policy_profile  # noqa: E402
from gah.resources import ResourceBook, ResourceError, create_schema  # noqa: E402


class ResourceBookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "resources.sqlite"
        self.db = sqlite3.connect(str(self.path), isolation_level=None, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.execute("BEGIN IMMEDIATE")
        create_schema(self.db)
        self.db.commit()
        self.book = ResourceBook(self.db)
        self.policy = initial_policy_profile()

    def tearDown(self):
        self.db.close()

    def call(self, name, *args, **kwargs):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            result = getattr(self.book, name)(*args, **kwargs)
        except BaseException:
            self.db.rollback()
            raise
        self.db.commit()
        return result

    def make_run(self, run_id="run-1", owner="owner-1", now=100, deadline=1300):
        return self.call(
            "create_run", run_id, "a" * 64, deepcopy(self.policy), "pr", owner, now, deadline
        )

    @staticmethod
    def reservation(*, cost=1, input_tokens=10, output_tokens=10, billing_mode="metered"):
        return {
            "case_trial_executions": 1,
            "model_calls": 1,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "api_cost_usd_micros": cost,
            "billing_ref": {"kind": "billing_basis", "id": "test-billing-ref", "digest": "b" * 64},
            "billing_mode": billing_mode,
        }

    @staticmethod
    def usage(*, cost="0.0000001", input_tokens=8, output_tokens=9):
        return {"input_tokens": input_tokens, "output_tokens": output_tokens, "cost_usd": cost}

    def reserve(self, operation_id, *, run_id="run-1", owner="owner-1", epoch=1, now=101, reservation=None):
        return self.call(
            "reserve", run_id, operation_id, owner, epoch,
            self.reservation() if reservation is None else reservation, now,
        )

    def dispatch(self, operation_id, *, run_id="run-1", owner="owner-1", epoch=1, now=102):
        return self.call("dispatch_intent", run_id, operation_id, owner, epoch, now)

    def observe(self, operation_id, event_id, *, run_id="run-1", stopped=True, usage=None, now=103):
        return self.call(
            "observe", run_id, operation_id, event_id,
            stopped=stopped, usage=usage, now=now,
        )

    def test_reservation_validates_all_dimensions_before_insert(self):
        self.make_run()
        malformed_billing = self.reservation()
        malformed_billing["billing_mode"] = []
        with self.assertRaisesRegex(ResourceError, "^BILLING_BASIS_REQUIRED$"):
            self.reserve("op-bad-billing", reservation=malformed_billing)
        bad = self.reservation(output_tokens=self.policy["per_call"]["output_tokens_max"] + 1)
        with self.assertRaisesRegex(ResourceError, "^PER_CALL_LIMIT$"):
            self.reserve("op-bad", reservation=bad)
        self.assertIsNone(self.db.execute("SELECT 1 FROM resource_operations WHERE operation_id='op-bad'").fetchone())

        self.reserve("op-1")
        self.reserve("op-2")
        with self.assertRaisesRegex(ResourceError, "^CONCURRENCY_LIMIT$"):
            self.reserve("op-3")
        self.assertIsNone(self.db.execute("SELECT 1 FROM resource_operations WHERE operation_id='op-3'").fetchone())

    def test_dispatch_intent_is_immutable_and_redelivery_cannot_resend(self):
        self.make_run()
        first = self.reserve("op-1")
        replay = self.reserve("op-1")
        self.assertEqual(replay, {"operation_id": "op-1", "existing": True, "dispatch_allowed": True})
        self.assertEqual(first["dispatch_allowed"], True)
        self.dispatch("op-1")
        with self.assertRaisesRegex(ResourceError, "^DISPATCH_NOT_PROVABLY_NEW$"):
            self.dispatch("op-1", now=103)
        replay_after_dispatch = self.reserve("op-1", now=104)
        self.assertFalse(replay_after_dispatch["dispatch_allowed"])

    def test_stopped_without_usage_then_usage_and_usage_then_stopped(self):
        self.make_run()
        self.reserve("op-stop-first")
        self.dispatch("op-stop-first")
        stopped = self.observe("op-stop-first", "event-stop", stopped=True, usage=None, now=103)
        self.assertTrue(stopped["accepted"])
        settled = self.observe(
            "op-stop-first", "event-usage", stopped=False, usage=self.usage(), now=104
        )
        self.assertTrue(settled["accepted"])

        self.reserve("op-usage-first", now=105)
        self.dispatch("op-usage-first", now=106)
        settled_first = self.observe(
            "op-usage-first", "event-usage-first", stopped=False, usage=self.usage(), now=107
        )
        self.assertTrue(settled_first["accepted"])
        stopped_second = self.observe(
            "op-usage-first", "event-stop-second", stopped=True, usage=None, now=108
        )
        self.assertTrue(stopped_second["accepted"])

        snapshot = self.call("snapshot", "run-1", 109)
        self.assertEqual(snapshot["resources"]["unsettled"], 0)
        self.assertEqual(snapshot["resources"]["slots"], 0)
        self.assertEqual(snapshot["resources"]["api_cost_usd_micros"], 2)
        self.assertTrue(snapshot["budget_closure"])

    def test_deadline_equality_cancellation_and_clock_rollback_block_starts(self):
        self.make_run(deadline=150)
        with self.assertRaisesRegex(ResourceError, "^START_DENIED$"):
            self.reserve("op-deadline", now=150)
        with self.assertRaisesRegex(ResourceError, "^START_DENIED$"):
            self.call("claim", "run-1", "owner-1", 150)

        self.make_run(run_id="run-2", owner="owner-2", now=200, deadline=400)
        self.call("cancel", "run-2", "owner-2", 1, 210)
        with self.assertRaisesRegex(ResourceError, "^START_DENIED$"):
            self.call("claim", "run-2", "owner-2", 211)
        with self.assertRaisesRegex(ResourceError, "^START_DENIED$"):
            self.call("reserve", "run-2", "op-cancelled", "owner-2", 1, self.reservation(), 211)
        with self.assertRaisesRegex(ResourceError, "^CLOCK_ROLLBACK$"):
            self.call("snapshot", "run-2", 209)

    def test_recovery_claim_is_allowed_only_for_closed_run_and_cannot_start_work(self):
        self.make_run()
        self.call("cancel", "run-1", "owner-1", 1, 110)
        with self.assertRaisesRegex(ResourceError, "^START_DENIED$"):
            self.call("claim", "run-1", "owner-1", 111)
        with self.assertRaisesRegex(ResourceError, "^OWNER_ACTIVE$"):
            self.call("claim_recovery", "run-1", "owner-2", 111)
        recovery = self.call("claim_recovery", "run-1", "owner-2", 161)
        self.assertEqual(recovery["owner_epoch"], 2)
        self.assertTrue(recovery["recovery_only"])
        with self.assertRaisesRegex(ResourceError, "^START_DENIED$"):
            self.call("reserve", "run-1", "op-after-recovery", "owner-2", 2, self.reservation(), 162)

    def test_observation_after_deadline_is_recorded_as_overrun(self):
        self.make_run(deadline=150)
        self.reserve("op-late")
        self.dispatch("op-late")
        result = self.observe("op-late", "late-event", stopped=True, usage=self.usage(cost="1"), now=151)
        self.assertTrue(result["accepted"])
        self.assertTrue(result["overrun"])
        snapshot = self.call("snapshot", "run-1", 152)
        self.assertTrue(snapshot["breached"])
        self.assertFalse(snapshot["budget_closure"])
        closed = self.call("close", "run-1", "owner-1", 1, 153)
        self.assertTrue(closed["closed"])
        self.assertTrue(closed["breached"])
        self.assertFalse(closed["budget_closure"])

    def test_close_requires_accounting_and_blocks_all_new_starts(self):
        self.make_run()
        self.reserve("op-close")
        with self.assertRaisesRegex(ResourceError, "^CLOSE_DENIED$"):
            self.call("close", "run-1", "owner-1", 1, 102)
        self.dispatch("op-close", now=103)
        self.observe("op-close", "close-event", stopped=True, usage=self.usage(cost="1"), now=104)
        closed = self.call("close", "run-1", "owner-1", 1, 105)
        self.assertTrue(closed["closed"])
        self.assertEqual(closed["closed_at"], 105)
        self.assertFalse(closed["ci_eligible"])
        with self.assertRaisesRegex(ResourceError, "^RUN_CLOSED$"):
            self.call("claim", "run-1", "owner-1", 106)
        with self.assertRaisesRegex(ResourceError, "^RUN_CLOSED$"):
            self.call("claim_recovery", "run-1", "owner-2", 166)
        with self.assertRaisesRegex(ResourceError, "^RUN_CLOSED$"):
            self.reserve("op-after-close", now=106)
        with self.assertRaisesRegex(ResourceError, "^RUN_CLOSED$"):
            self.call("dispatch_intent", "run-1", "missing-op", "owner-1", 1, 106)
        with self.assertRaisesRegex(ResourceError, "^RUN_CLOSED$"):
            self.call("cancel", "run-1", "owner-1", 1, 106)

        self.make_run(run_id="run-2", owner="owner-2", now=200, deadline=400)
        self.call("cancel", "run-2", "owner-2", 1, 210)
        cancelled = self.call("close", "run-2", "owner-2", 1, 211)
        self.assertTrue(cancelled["closed"])
        self.assertTrue(cancelled["cancelled"])
        self.assertFalse(cancelled["ci_eligible"])

    def test_closed_run_can_record_late_conflict_and_drop_budget_closure(self):
        self.make_run()
        self.reserve("op-late-conflict", reservation=self.reservation(cost=1_000_001))
        self.dispatch("op-late-conflict")
        self.observe("op-late-conflict", "stable-event", stopped=True, usage=self.usage(cost="1"), now=103)
        self.call("close", "run-1", "owner-1", 1, 104)
        result = self.observe(
            "op-late-conflict", "stable-event", stopped=True, usage=self.usage(cost="2"), now=105
        )
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"], "EVENT_CONFLICT")
        snapshot = self.call("snapshot", "run-1", 106)
        self.assertTrue(snapshot["closed"])
        self.assertEqual(snapshot["closed_at"], 104)
        self.assertTrue(snapshot["breached"])
        self.assertFalse(snapshot["budget_closure"])
        self.assertEqual(snapshot["resources"]["unsettled"], 1)
        self.assertEqual(snapshot["resources"]["api_cost_usd_micros"], 2_000_000)

    def test_takeover_rejects_old_owner_but_observer_settles_original_operation_once(self):
        self.make_run()
        self.reserve("op-dispatched")
        self.dispatch("op-dispatched")
        self.reserve("op-unstarted", now=103)
        takeover = self.call("claim", "run-1", "owner-2", 161)
        self.assertEqual(takeover["owner_epoch"], 2)
        with self.assertRaisesRegex(ResourceError, "^OWNER_STALE$"):
            self.dispatch("op-unstarted", owner="owner-1", epoch=1, now=162)

        accepted = self.observe(
            "op-dispatched", "observer-event", stopped=True, usage=self.usage(cost="1"), now=163
        )
        self.assertTrue(accepted["accepted"])
        replay = self.observe(
            "op-dispatched", "observer-event", stopped=True, usage=self.usage(cost="1"), now=164
        )
        self.assertEqual(replay, accepted)
        row = self.db.execute(
            "SELECT usage_json,cost_micros,stopped_at FROM resource_operations WHERE operation_id='op-dispatched'"
        ).fetchone()
        self.assertIsNotNone(row["usage_json"])
        self.assertEqual(row["cost_micros"], 1_000_000)
        self.assertIsNotNone(row["stopped_at"])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM resource_events WHERE operation_id='op-dispatched'").fetchone()[0], 1)

    def test_micro_fee_rounds_up_and_conflicting_fee_preserves_exposure(self):
        self.make_run()
        reservation = self.reservation(cost=1_000_001)
        self.reserve("op-fee", reservation=reservation)
        self.dispatch("op-fee")
        accepted = self.observe(
            "op-fee", "fee-event", stopped=True,
            usage=self.usage(cost="1.0000000000000000000000000000001"), now=103,
        )
        self.assertTrue(accepted["accepted"])
        row = self.db.execute("SELECT cost_micros,exposure_micros,conflicted FROM resource_operations WHERE operation_id='op-fee'").fetchone()
        self.assertEqual(row["cost_micros"], 1_000_001)
        self.assertEqual(row["exposure_micros"], 0)
        self.assertEqual(row["conflicted"], 0)

        conflict = self.observe("op-fee", "fee-event", stopped=True, usage=self.usage(cost="2"), now=104)
        self.assertEqual(conflict, {"operation_id": "op-fee", "accepted": False, "conflict": True,
                                    "reason": "EVENT_CONFLICT", "ci_eligible": False})
        row = self.db.execute("SELECT cost_micros,exposure_micros,conflicted FROM resource_operations WHERE operation_id='op-fee'").fetchone()
        self.assertEqual(row["cost_micros"], 1_000_001)
        self.assertEqual(row["exposure_micros"], 2_000_000)
        self.assertEqual(row["conflicted"], 1)
        snapshot = self.call("snapshot", "run-1", 105)
        self.assertTrue(snapshot["breached"])
        self.assertFalse(snapshot["budget_closure"])
        self.assertEqual(snapshot["resources"]["api_cost_usd_micros"], 2_000_000)

    def test_event_id_reuse_across_operations_and_runs_holds_both_runs(self):
        self.make_run()
        self.reserve("op-original")
        self.dispatch("op-original")
        self.observe("op-original", "shared-event", stopped=True, usage=self.usage(cost="1"), now=103)
        original = self.db.execute(
            "SELECT operation_id,event_digest,response_json,response_digest FROM resource_events WHERE event_id='shared-event'"
        ).fetchone()

        self.make_run(run_id="run-2", owner="owner-2", now=200, deadline=1400)
        self.reserve("op-reused", run_id="run-2", owner="owner-2", now=201)
        self.dispatch("op-reused", run_id="run-2", owner="owner-2", now=202)
        conflict = self.observe(
            "op-reused", "shared-event", run_id="run-2", stopped=True,
            usage=self.usage(cost="2"), now=203,
        )
        self.assertEqual(conflict, {"operation_id": "op-reused", "accepted": False, "conflict": True,
                                    "reason": "EVENT_CONFLICT", "ci_eligible": False})

        first = self.call("snapshot", "run-1", 204)
        second = self.call("snapshot", "run-2", 204)
        self.assertTrue(first["breached"])
        self.assertTrue(second["breached"])
        self.assertFalse(first["budget_closure"])
        self.assertFalse(second["budget_closure"])
        self.assertEqual(first["resources"]["api_cost_usd_micros"], 2_000_000)
        self.assertEqual(second["resources"]["api_cost_usd_micros"], 2_000_000)
        current = self.db.execute(
            "SELECT operation_id,event_digest,response_json,response_digest FROM resource_events WHERE event_id='shared-event'"
        ).fetchone()
        self.assertEqual(tuple(current), tuple(original))

    def test_old_unsettled_reservation_remains_in_global_window(self):
        self.make_run(now=100, deadline=1300)
        self.reserve("op-old-unsettled")
        self.reserve("op-old-settled", now=101)
        self.dispatch("op-old-settled", now=102)
        self.observe("op-old-settled", "old-settled-event", stopped=True, usage=self.usage(cost="1"), now=103)

        self.make_run(run_id="run-new", owner="owner-new", now=86_503, deadline=87_703)
        self.call("reserve", "run-new", "op-new", "owner-new", 1, self.reservation(), 86_504)
        snapshot = self.call("snapshot", "run-new", 86_504)
        self.assertEqual(snapshot["resources"]["global_api_cost_usd_micros"], 2)

    def test_independent_connections_serialize_reservations(self):
        path = Path(self.temp.name) / "independent.sqlite"
        setup = sqlite3.connect(str(path), isolation_level=None, timeout=10)
        setup.row_factory = sqlite3.Row
        setup.execute("PRAGMA foreign_keys = ON")
        setup.execute("BEGIN IMMEDIATE")
        create_schema(setup)
        setup.commit()
        policy = initial_policy_profile()
        book = ResourceBook(setup)
        setup.execute("BEGIN IMMEDIATE")
        book.create_run("run-independent", "a" * 64, policy, "pr", "owner-1", 100, 1300)
        setup.commit()
        setup.close()

        barrier = threading.Barrier(2)
        results = []

        def worker(operation_id):
            db = sqlite3.connect(str(path), isolation_level=None, timeout=10)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys = ON")
            try:
                barrier.wait(timeout=5)
                db.execute("BEGIN IMMEDIATE")
                ResourceBook(db).reserve(
                    "run-independent", operation_id, "owner-1", 1,
                    self.reservation(), 101,
                )
                db.commit()
                results.append("ok")
            except BaseException as error:
                db.rollback()
                results.append(type(error).__name__)
            finally:
                db.close()

        threads = [threading.Thread(target=worker, args=(f"op-{index}",)) for index in (1, 2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertEqual(results.count("ok"), 2)


if __name__ == "__main__":
    unittest.main()
