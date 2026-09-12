import json
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gah.ledger import Ledger, LedgerError


class LedgerTests(unittest.TestCase):
    def test_non_ascii_amounts_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            with Ledger(Path(folder) / "ledger.sqlite", clock=lambda: 1000) as ledger:
                ledger.create_run("run"); lease = ledger.claim("run", "owner")
                for amount in ["0.０００００００", "1０", "0.١"]:
                    with self.subTest(amount=amount), self.assertRaises(LedgerError):
                        ledger.reserve("run", "owner", lease["epoch"], "op", amount)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "ledger.sqlite3"
        self.now = [1_000]

    def tearDown(self):
        self.tmp.cleanup()

    def ledger(self):
        return Ledger(self.path, clock=lambda: self.now[0])

    def start(self, ledger, run="run", profile="pr"):
        ledger.create_run(run, profile)
        return ledger.claim(run, "worker")

    def test_restart_preserves_run_lease_and_operation(self):
        with self.ledger() as first:
            lease = self.start(first)
            reserved = first.reserve("run", "worker", lease["epoch"], "op", "0.0000004")
            self.assertEqual(reserved["reserved_micros"], 1)
        with self.ledger() as second:
            snapshot = second.snapshot("run")
            self.assertEqual(snapshot["operations"][0]["reserved_micros"], 1)
            self.assertEqual(snapshot["run"]["owner"], "worker")

    def test_reservation_is_idempotent_and_conflicting_amount_is_rejected(self):
        with self.ledger() as ledger:
            lease = self.start(ledger, profile="full")
            first = ledger.reserve("run", "worker", lease["epoch"], "op", "0.0000004")
            self.assertEqual(
                ledger.reserve("run", "worker", lease["epoch"], "op", "0.0000004"), first
            )
            with self.assertRaises(LedgerError) as caught:
                ledger.reserve("run", "worker", lease["epoch"], "op", "0.0000005")
            self.assertEqual(caught.exception.code, "conflict")
            self.assertEqual(ledger.snapshot("run")["run_accounted_micros"], 1)

    def test_settlement_replaces_once_and_conflict_holds_run(self):
        with self.ledger() as ledger:
            lease = self.start(ledger)
            ledger.reserve("run", "worker", lease["epoch"], "op", "2")
            settled = ledger.settle("run", "worker", lease["epoch"], "op", "2")
            self.assertEqual(settled["settled_micros"], 2_000_000)
            with self.assertRaises(LedgerError) as caught:
                ledger.settle("run", "worker", lease["epoch"], "op", "2.1")
            self.assertEqual(caught.exception.code, "conflict")
            self.assertTrue(ledger.snapshot("run")["run"]["hold"])
            with self.assertRaises(LedgerError):
                ledger.reserve("run", "worker", lease["epoch"], "new", "0.1")

    def test_takeover_owner_can_settle_old_reserved_operation(self):
        with self.ledger() as ledger:
            old = self.start(ledger)
            ledger.reserve("run", "worker", old["epoch"], "op", "1")
            self.now[0] = old["lease_until"]
            new = ledger.claim("run", "replacement")
            settled = ledger.settle("run", "replacement", new["epoch"], "op", "1")
            self.assertEqual(settled["owner"], "worker")
            self.assertEqual(settled["epoch"], old["epoch"])
            self.assertEqual(settled["settled_micros"], 1_000_000)

    def test_conflicting_settlement_keeps_conservative_exposure_after_window(self):
        with self.ledger() as ledger:
            lease = self.start(ledger, profile="full")
            ledger.reserve("run", "worker", lease["epoch"], "op", "1")
            ledger.settle("run", "worker", lease["epoch"], "op", "1")
            with self.assertRaises(LedgerError):
                ledger.settle("run", "worker", lease["epoch"], "op", "3")
            self.now[0] += 86_400
            snap = ledger.snapshot("run")
            self.assertEqual(snap["operations"][0]["settled_micros"], 1_000_000)
            self.assertEqual(snap["operations"][0]["exposure_micros"], 3_000_000)
            self.assertEqual(snap["global_accounted_micros"], 3_000_000)

    def test_over_reservation_is_recorded_and_holds(self):
        with self.ledger() as ledger:
            lease = self.start(ledger)
            ledger.reserve("run", "worker", lease["epoch"], "op", "1")
            with self.assertRaises(LedgerError) as caught:
                ledger.settle("run", "worker", lease["epoch"], "op", "1.000001")
            self.assertEqual(caught.exception.code, "budget_violation")
            op = ledger.snapshot("run")["operations"][0]
            self.assertEqual(op["reserved_micros"], 1_000_000)
            self.assertEqual(op["settled_micros"], 1_000_001)

    def test_decimal_cutting_is_exact_beyond_default_decimal_precision(self):
        with self.ledger() as ledger:
            lease = self.start(ledger, profile="full")
            amount = "1.0000000000000000000000000000001"
            reserved = ledger.reserve("run", "worker", lease["epoch"], "precise", amount)
            self.assertEqual(reserved["reserved_micros"], 1_000_001)

    def test_rolling_24h_uses_half_open_lower_bound_and_keeps_unsettled(self):
        with self.ledger() as ledger:
            lease = self.start(ledger, profile="full")
            ledger.reserve("run", "worker", lease["epoch"], "old", "10")
            ledger.settle("run", "worker", lease["epoch"], "old", "10")
            self.now[0] += 86_400
            ledger.create_run("new", "full")
            new_lease = ledger.claim("new", "new-worker")
            ledger.reserve("new", "new-worker", new_lease["epoch"], "pending", "10")
            snap = ledger.snapshot("new")
            self.assertEqual(snap["global_accounted_micros"], 10_000_000)

    def test_independent_connections_atomically_compete_for_global_budget(self):
        # 既存の1micros消費後、二つのrunが各10USDを競合予約する。
        with self.ledger() as setup:
            setup.create_run("seed", "full")
            setup.create_run("a", "full")
            setup.create_run("b", "full")
            seed_lease = setup.claim("seed", "seed-owner")
            setup.reserve("seed", "seed-owner", seed_lease["epoch"], "seed-op", "0.000001")
            setup.settle("seed", "seed-owner", seed_lease["epoch"], "seed-op", "0.000001")
            setup.claim("a", "a-owner")
            setup.claim("b", "b-owner")
        barrier = threading.Barrier(2)
        results = []

        def attempt(run, owner):
            try:
                with self.ledger() as ledger:
                    barrier.wait()
                    results.append(ledger.reserve(run, owner, 1, run + "-op", "10"))
            except Exception as exc:  # assertions below inspect the actual type
                results.append(exc)

        threads = [
            threading.Thread(target=attempt, args=("a", "a-owner")),
            threading.Thread(target=attempt, args=("b", "b-owner")),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(6)
        self.assertEqual(len(results), 2)
        self.assertEqual(sum(isinstance(item, dict) for item in results), 1)
        self.assertEqual(sum(isinstance(item, LedgerError) for item in results), 1)

    def test_clock_rollback_rejects_without_reopening_run(self):
        with self.ledger() as ledger:
            self.start(ledger)
            self.now[0] -= 1
            with self.assertRaises(LedgerError) as caught:
                ledger.snapshot("run")
            self.assertEqual(caught.exception.code, "clock_rollback")

    def test_report_is_canonical_immutable_and_hash_checked(self):
        report = {"purpose": "component_validation", "ci_eligible": False, "内容": "固定"}
        with self.ledger() as ledger:
            stored = ledger.store_report("request", report)
            self.assertEqual(ledger.get_report("request"), report)
            duplicate = ledger.store_report("request", {"内容": "固定", "ci_eligible": False, "purpose": "component_validation"})
            self.assertEqual(duplicate["sha256"], stored["sha256"])
            with self.assertRaises(LedgerError) as caught:
                ledger.store_report("request", {"purpose": "component_validation", "ci_eligible": False, "内容": "別"})
            self.assertEqual(caught.exception.code, "conflict")
        db = sqlite3.connect(self.path)
        db.execute("UPDATE reports SET payload=? WHERE request_id='request'", (b"corrupt",))
        db.commit()
        db.close()
        with self.ledger() as ledger:
            with self.assertRaises(LedgerError) as caught:
                ledger.get_report("request")
            self.assertEqual(caught.exception.code, "storage_failure")

    def test_invalid_report_and_existing_anomalous_database_are_rejected(self):
        with self.ledger() as ledger:
            with self.assertRaises(LedgerError):
                ledger.store_report("request", {"purpose": "component_validation", "ci_eligible": True})
        bad = Path(self.tmp.name) / "bad.sqlite3"
        sqlite3.connect(bad).close()
        with self.assertRaises(LedgerError) as caught:
            Ledger(bad, clock=lambda: self.now[0])
        self.assertEqual(caught.exception.code, "unsupported_version")
        partial = Path(self.tmp.name) / "partial.sqlite3"
        partial_db = sqlite3.connect(partial)
        partial_db.execute("CREATE TABLE ledger_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        partial_db.commit()
        partial_db.close()
        with self.assertRaises(LedgerError):
            Ledger(partial, clock=lambda: self.now[0])
        check = sqlite3.connect(partial)
        self.assertEqual(
            check.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(),
            [("ledger_meta",)],
        )
        check.close()

    def test_schema_failure_rolls_back_all_ddl(self):
        target = Path(self.tmp.name) / "ddl-failure.sqlite3"
        real_connect = sqlite3.connect

        class FailingConnection:
            def __init__(self, connection):
                self.connection = connection
                self.create_count = 0

            def execute(self, sql, *args):
                if sql.lstrip().upper().startswith("CREATE"):
                    self.create_count += 1
                    if self.create_count == 2:
                        raise RuntimeError("injected schema failure")
                return self.connection.execute(sql, *args)

            def __getattr__(self, name):
                return getattr(self.connection, name)

        def connect_proxy(*args, **kwargs):
            return FailingConnection(real_connect(*args, **kwargs))

        with patch("gah.ledger.sqlite3.connect", side_effect=connect_proxy):
            with self.assertRaises(LedgerError) as caught:
                Ledger(target, clock=lambda: self.now[0])
        self.assertEqual(caught.exception.code, "storage_failure")
        check = real_connect(target)
        self.assertEqual(
            check.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(),
            [],
        )
        check.close()


if __name__ == "__main__":
    unittest.main()
