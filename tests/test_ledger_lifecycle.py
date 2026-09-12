import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gah.ledger import Ledger, LedgerError


class LifecycleLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "ledger.sqlite3"
        self.now = [1_000]

    def tearDown(self):
        self.temp.cleanup()

    def ledger(self):
        return Ledger(self.path, clock=lambda: self.now[0])

    def claim(self, ledger, run="run", owner="worker", profile="pr"):
        ledger.create_run(run, profile)
        return ledger.claim(run, owner)

    def test_v1_migration_preserves_report_bytes_and_is_idempotent(self):
        report = {"purpose": "component_validation", "ci_eligible": False, "value": "固定"}
        canonical = json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        payload = canonical.encode("utf-8")
        db = sqlite3.connect(self.path)
        db.executescript(
            """
            CREATE TABLE ledger_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE runs (run_id TEXT PRIMARY KEY, profile TEXT NOT NULL,
                started_at INTEGER NOT NULL, deadline INTEGER NOT NULL,
                epoch0 INTEGER NOT NULL DEFAULT 0, owner TEXT,
                owner_epoch INTEGER NOT NULL DEFAULT 0, lease_until INTEGER,
                hold INTEGER NOT NULL DEFAULT 0, hold_reason TEXT);
            CREATE TABLE operations (operation_id TEXT PRIMARY KEY, run_id TEXT NOT NULL,
                owner TEXT NOT NULL, epoch INTEGER NOT NULL, reserved_usd TEXT NOT NULL,
                reserved_micros INTEGER NOT NULL, reserved_at INTEGER NOT NULL,
                settled_usd TEXT, settled_micros INTEGER, settled_at INTEGER,
                status TEXT NOT NULL, hold_reason TEXT,
                exposure_usd TEXT, exposure_micros INTEGER);
            CREATE TABLE reports (request_id TEXT PRIMARY KEY, canonical_json TEXT NOT NULL,
                payload BLOB NOT NULL, sha256 TEXT NOT NULL);
            INSERT INTO ledger_meta VALUES ('last_clock', '999');
            PRAGMA user_version = 1;
            """
        )
        db.execute("INSERT INTO reports VALUES (?,?,?,?)", ("req", canonical, payload, hashlib.sha256(payload).hexdigest()))
        db.commit()
        db.close()
        self.assertEqual(Ledger.migrate_v1(self.path), {"schema_version": 2, "changed": True})
        self.assertEqual(Ledger.migrate_v1(self.path), {"schema_version": 2, "changed": False})
        with Ledger(self.path, clock=lambda: self.now[0]) as ledger:
            self.assertEqual(ledger.get_report("req"), report)

    def test_recovery_claim_settles_after_deadline_and_cannot_resume_execution(self):
        with self.ledger() as ledger:
            lease = self.claim(ledger, profile="full")
            ledger.reserve("run", "worker", lease["epoch"], "op", "1")
            self.now[0] = ledger.snapshot("run")["run"]["deadline"]
            recovery = ledger.claim_recovery("run", "collector")
            self.assertEqual(recovery["lease_kind"], "recovery")
            with self.assertRaises(LedgerError):
                ledger.claim("run", "collector")
            settled = ledger.settle("run", "collector", recovery["epoch"], "op", "1")
            self.assertEqual(settled["settled_micros"], 1_000_000)

    def test_cancel_stop_finalize_and_terminal_are_idempotent(self):
        with self.ledger() as ledger:
            lease = self.claim(ledger)
            self.assertEqual(ledger.request_cancel("run", "worker", lease["epoch"])["status"], "requested")
            self.assertEqual(ledger.request_cancel("run", "worker", lease["epoch"])["status"], "requested")
            self.assertEqual(ledger.confirm_stopped("run", "worker", lease["epoch"])["status"], "confirmed")
            self.assertEqual(ledger.confirm_stopped("run", "worker", lease["epoch"])["status"], "confirmed")
            terminal = ledger.finalize(
                "run", "worker", lease["epoch"], assurance="HEALTHY",
                work_complete=True, required_failure=False,
            )
            self.assertEqual(terminal["execution_status"], "CANCELLED")
            self.assertEqual(terminal["exit_code"], 3)
            self.assertEqual(ledger.finalize(
                "run", "worker", lease["epoch"], assurance="DEGRADED",
                work_complete=False, required_failure=True,
            ), terminal)
            self.assertEqual(ledger.get_terminal("run"), terminal)
            audit = sqlite3.connect(self.path)
            try:
                events = audit.execute(
                    "SELECT event_kind FROM run_events WHERE run_id='run' ORDER BY event_id"
                ).fetchall()
            finally:
                audit.close()
            self.assertEqual(events.count(("cancel_requested",)), 1)

    def test_budget_closure_stays_open_for_unsettled_cancelled_run(self):
        with self.ledger() as ledger:
            lease = self.claim(ledger)
            ledger.reserve("run", "worker", lease["epoch"], "pending", "0.0000004")
            ledger.request_cancel("run", "worker", lease["epoch"])
            ledger.confirm_stopped("run", "worker", lease["epoch"])
            closure = ledger.budget_closure("run")
            self.assertEqual(closure["status"], "OPEN")
            self.assertEqual(closure["pending_operations"], 1)
            terminal = ledger.finalize(
                "run", "worker", lease["epoch"], assurance="HEALTHY",
                work_complete=True, required_failure=False,
            )
            self.assertEqual(terminal["execution_status"], "CANCELLED")
            self.assertEqual(ledger.get_terminal("run"), terminal)


if __name__ == "__main__":
    unittest.main()
