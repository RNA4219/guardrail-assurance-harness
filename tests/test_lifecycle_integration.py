"""期限後回収・取消し競合・DB移行を独立プロセスと実SQLiteで検査する。"""
from contextlib import closing
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gah.ledger import Ledger, LedgerError
from gah.cli import main


# 最初のリリースのDB契約を固定する。現在の初期化実装から生成しない。
V1_SCHEMA = """
CREATE TABLE ledger_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE runs(run_id TEXT PRIMARY KEY, profile TEXT NOT NULL, started_at INTEGER NOT NULL,
 deadline INTEGER NOT NULL, epoch0 INTEGER NOT NULL DEFAULT 0, owner TEXT,
 owner_epoch INTEGER NOT NULL DEFAULT 0, lease_until INTEGER, hold INTEGER NOT NULL DEFAULT 0, hold_reason TEXT);
CREATE TABLE operations(operation_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id),
 owner TEXT NOT NULL, epoch INTEGER NOT NULL, reserved_usd TEXT NOT NULL, reserved_micros INTEGER NOT NULL,
 reserved_at INTEGER NOT NULL, settled_usd TEXT, settled_micros INTEGER, settled_at INTEGER,
 status TEXT NOT NULL, hold_reason TEXT, exposure_usd TEXT, exposure_micros INTEGER);
CREATE INDEX operations_run_idx ON operations(run_id);
CREATE INDEX operations_settled_idx ON operations(settled_at);
CREATE TABLE reports(request_id TEXT PRIMARY KEY, canonical_json TEXT NOT NULL, payload BLOB NOT NULL, sha256 TEXT NOT NULL);
INSERT INTO ledger_meta VALUES('last_clock','1000');
INSERT INTO runs(run_id,profile,started_at,deadline,owner,owner_epoch,lease_until) VALUES('old','pr',1000,2200,'owner',1,1060);
INSERT INTO operations(operation_id,run_id,owner,epoch,reserved_usd,reserved_micros,reserved_at,status)
 VALUES('old-op','old','owner',1,'1',1000000,1000,'reserved');
PRAGMA user_version=1;
"""


def create_v1(path):
    report = {"purpose": "component_validation", "ci_eligible": False, "request_id": "legacy"}
    canonical = json.dumps(report, sort_keys=True, separators=(",", ":"))
    payload = canonical.encode("utf-8")
    with closing(sqlite3.connect(path)) as database:
        database.executescript(V1_SCHEMA)
        database.execute("INSERT INTO reports VALUES(?,?,?,?)",
                         ("legacy", canonical, payload, hashlib.sha256(payload).hexdigest()))
        database.commit()
    return payload


class LifecycleIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "ledger.sqlite"
        self.now = 1000

    def open(self):
        return Ledger(self.db, clock=lambda: self.now)

    def cli(self, *arguments):
        return subprocess.run([sys.executable, "-E", "-X", "utf8", "-m", "tools.gah_cli", *arguments],
                              cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=20)

    def test_explicit_upgrade_preserves_v1_records_and_is_idempotent(self):
        payload = create_v1(self.db)
        with self.assertRaises(LedgerError):
            self.open()
        with closing(sqlite3.connect(self.db)) as db:
            operation_before = db.execute("SELECT * FROM operations").fetchall()
            report_before = db.execute("SELECT * FROM reports").fetchall()
        upgraded = self.cli("db-upgrade", "--db", str(self.db))
        self.assertEqual(upgraded.returncode, 1, upgraded.stderr)
        self.assertEqual(json.loads(upgraded.stdout)["schema_version"], 2)
        self.assertIs(json.loads(upgraded.stdout)["changed"], True)
        with self.open() as ledger:
            self.assertEqual(ledger.get_report("legacy"), json.loads(payload))
            current = ledger.snapshot("old")["run"]
            self.assertEqual(current["deadline"], 2200)
            self.assertEqual(current["owner_epoch"], 1)
            self.assertEqual(current["lease_kind"], "execution")
            self.assertIsNone(current["cancel_requested_at"])
            self.assertIsNone(current["stop_confirmed_at"])
        with closing(sqlite3.connect(self.db)) as db:
            self.assertEqual(db.execute("SELECT * FROM operations").fetchall(), operation_before)
            self.assertEqual(db.execute("SELECT * FROM reports").fetchall(), report_before)
        again = self.cli("db-upgrade", "--db", str(self.db))
        self.assertEqual(again.returncode, 1, again.stderr)
        self.assertIs(json.loads(again.stdout)["changed"], False)

    def test_upgrade_fault_rolls_back_schema_and_report_bytes(self):
        create_v1(self.db)
        real_connect = sqlite3.connect

        def connect(*args, **kwargs):
            connection = real_connect(*args, **kwargs)
            connection.set_authorizer(lambda action, one, two, database, source:
                                     sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_CREATE_TABLE
                                     and one == "terminal_records" else sqlite3.SQLITE_OK)
            return connection

        with patch("gah.ledger.sqlite3.connect", side_effect=connect):
            with self.assertRaises(LedgerError):
                Ledger.migrate_v1(self.db)
        with closing(real_connect(self.db)) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertNotIn("lease_kind", {row[1] for row in db.execute("PRAGMA table_info(runs)")})
            canonical, payload, digest = db.execute("SELECT canonical_json,payload,sha256 FROM reports").fetchone()
            self.assertEqual(canonical.encode(), payload)
            self.assertEqual(hashlib.sha256(payload).hexdigest(), digest)
        self.assertTrue(Ledger.migrate_v1(self.db)["changed"])

    def test_cancelled_receipt_remains_immutable_after_deadline_recovery(self):
        with self.open() as ledger:
            run = ledger.create_run("run")
            lease = ledger.claim("run", "first")
            epoch = lease["owner_epoch"]
            ledger.reserve("run", "first", epoch, "op", "1")
            ledger.request_cancel("run", "first", epoch)
            waiting = ledger.finalize("run", "first", epoch, assurance="HOLD", work_complete=False, required_failure=False)
            self.assertEqual(waiting["execution_status"], "WAITING")
            with self.assertRaises(LedgerError):
                ledger.get_terminal("run")
            ledger.confirm_stopped("run", "first", epoch)
            receipt = ledger.finalize("run", "first", epoch, assurance="HOLD", work_complete=False, required_failure=False)
            self.assertEqual(receipt["execution_status"], "CANCELLED")
            self.assertEqual(receipt["exit_code"], 3)
            self.assertEqual(receipt["budget_closure"]["status"], "OPEN")
            self.assertEqual(ledger.budget_closure("run")["run_accounted_micros"], 1_000_000)
        self.now = run["deadline"]
        with self.open() as ledger:
            recovery = ledger.claim_recovery("run", "second")
            with self.assertRaises(LedgerError):
                ledger.settle("run", "first", epoch, "op", "0.5")
            with self.assertRaises(LedgerError):
                ledger.reserve("run", "second", recovery["owner_epoch"], "new-op", "0.1")
            ledger.settle("run", "second", recovery["owner_epoch"], "op", "0.5")
            self.assertEqual(ledger.budget_closure("run")["status"], "CLOSED")
            self.assertEqual(ledger.get_terminal("run"), receipt)
            self.assertEqual(ledger.finalize("run", "second", recovery["owner_epoch"], assurance="HEALTHY",
                                            work_complete=True, required_failure=False), receipt)
        displayed = self.cli("terminal-show", "--id", "run", "--db", str(self.db))
        self.assertEqual(displayed.returncode, 1, displayed.stderr)
        self.assertEqual(json.loads(displayed.stdout), receipt)
        budget = self.cli("budget-show", "--id", "run", "--db", str(self.db))
        self.assertEqual(budget.returncode, 1, budget.stderr)
        self.assertEqual(json.loads(budget.stdout)["status"], "CLOSED")
        self.assertIs(json.loads(budget.stdout)["ci_eligible"], False)

    def test_terminal_and_event_save_are_atomic(self):
        with self.open() as ledger:
            ledger.create_run("run")
            epoch = ledger.claim("run", "owner")["owner_epoch"]
            ledger.confirm_stopped("run", "owner", epoch)
            ledger._db.set_authorizer(lambda action, one, two, database, source:
                                     sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_INSERT
                                     and one == "run_events" else sqlite3.SQLITE_OK)
            with self.assertRaises(LedgerError):
                ledger.finalize("run", "owner", epoch, assurance="HEALTHY", work_complete=True, required_failure=False)
            ledger._db.set_authorizer(None)
            with self.assertRaises(LedgerError):
                ledger.get_terminal("run")
            receipt = ledger.finalize("run", "owner", epoch, assurance="HEALTHY", work_complete=True, required_failure=False)
            self.assertEqual(receipt["execution_status"], "COMPLETED")
            self.assertIs(receipt["ci_eligible"], False)

    def test_cancellation_and_finalize_are_serialized_across_processes(self):
        with self.open() as ledger:
            ledger.create_run("run")
            ledger.claim("run", "owner")
            ledger.confirm_stopped("run", "owner", 1)
        script = """
import json,sys
sys.path.insert(0, sys.argv[1])
from gah.ledger import Ledger
sys.stdin.readline()
with Ledger(sys.argv[2], clock=lambda:1000) as ledger:
    if sys.argv[3] == 'cancel':
        result = ledger.request_cancel('run','owner',1)
    else:
        result = ledger.finalize('run','owner',1,assurance='HEALTHY',work_complete=True,required_failure=False)
    print(json.dumps(result))
"""
        processes = [subprocess.Popen([sys.executable, "-E", "-X", "utf8", "-c", script,
                                      str(ROOT / "src"), str(self.db), operation], stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
                     for operation in ("cancel", "finalize")]
        try:
            for process in processes:
                process.stdin.write("go\n")
                process.stdin.flush()
            for process in processes:
                stdout, stderr = process.communicate(timeout=20)
                self.assertEqual(process.returncode, 0, stderr)
                self.assertIsInstance(json.loads(stdout), dict)
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.communicate(timeout=5)
        with self.open() as ledger:
            current = ledger.snapshot("run")["run"]
            receipt = ledger.get_terminal("run")
            expected = "COMPLETED" if current["cancel_requested_at"] is None else "CANCELLED"
            self.assertEqual(receipt["execution_status"], expected)
            self.assertIs(receipt["ci_eligible"], False)

    def test_read_and_upgrade_missing_database_have_no_side_effect(self):
        for command in ("budget-show", "terminal-show", "db-upgrade"):
            arguments = [] if command == "db-upgrade" else ["--id", "missing"]
            result = self.cli(command, *arguments, "--db", str(self.db))
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stderr)["error"], "DATABASE_NOT_FOUND")
            self.assertFalse(self.db.exists())

    def test_corrupt_terminal_fails_without_echo(self):
        with self.open() as ledger:
            ledger.create_run("run")
            epoch = ledger.claim("run", "owner")["owner_epoch"]
            ledger.finalize("run", "owner", epoch, assurance="HOLD", work_complete=False, required_failure=True)
        with closing(sqlite3.connect(self.db)) as db:
            db.execute("UPDATE terminal_records SET payload=?", (b"synthetic-corruption-marker",))
            db.commit()
        result = self.cli("terminal-show", "--id", "run", "--db", str(self.db))
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("synthetic-corruption-marker", result.stderr + result.stdout)
        self.assertIs(json.loads(result.stderr)["ci_eligible"], False)

    def test_closed_error_stream_still_returns_failure(self):
        closed = io.StringIO()
        closed.close()
        with patch("gah.cli.sys.stderr", closed):
            self.assertEqual(main(["budget-show", "--id", "missing", "--db", str(self.db)]), 2)

    def test_migration_rejects_incomplete_v1_without_changing_version(self):
        variants = {
            "missing-exposure": "ALTER TABLE operations DROP COLUMN exposure_micros",
            "missing-clock": "DELETE FROM ledger_meta WHERE key='last_clock'",
            "partial-upgrade": "ALTER TABLE runs ADD COLUMN lease_kind TEXT DEFAULT 'recovery'",
            "corrupt-report": "UPDATE reports SET payload=X'00'",
        }
        for name, mutation in variants.items():
            with self.subTest(name=name):
                target = self.db.with_name(name + ".sqlite")
                create_v1(target)
                with closing(sqlite3.connect(target)) as db:
                    db.execute(mutation)
                    db.commit()
                with self.assertRaises(LedgerError):
                    Ledger.migrate_v1(target)
                with closing(sqlite3.connect(target)) as db:
                    self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 1)
                    self.assertEqual(db.execute("SELECT count(*) FROM sqlite_master WHERE name='terminal_records'").fetchone()[0], 0)

    def test_failed_terminal_blocks_execution_claim_and_invalid_replay(self):
        with self.open() as ledger:
            ledger.create_run("run")
            epoch = ledger.claim("run", "owner")["owner_epoch"]
            receipt = ledger.finalize("run", "owner", epoch, assurance="UNKNOWN", work_complete=False, required_failure=True)
            self.assertEqual(receipt["execution_status"], "FAILED")
            with self.assertRaises(LedgerError):
                ledger.claim("run", "owner")
            with self.assertRaises((LedgerError, ValueError)):
                ledger.finalize("run", "owner", epoch, assurance="invalid", work_complete=True, required_failure=False)
            self.assertEqual(ledger.get_terminal("run"), receipt)

    def test_unknown_operation_or_incomplete_settlement_cannot_close_budget(self):
        for invalid in ("unknown-status", "missing-amount"):
            with self.subTest(invalid=invalid):
                target = self.db.with_name(invalid + ".sqlite")
                with Ledger(target, clock=lambda: 1000) as ledger:
                    ledger.create_run("run")
                    ledger.claim("run", "owner")
                    ledger.reserve("run", "owner", 1, "op", "1")
                    ledger.confirm_stopped("run", "owner", 1)
                    if invalid == "unknown-status":
                        ledger._db.execute("UPDATE operations SET status='unrecognized'")
                    else:
                        ledger._db.execute("UPDATE operations SET status='settled', settled_usd=NULL, settled_micros=NULL, settled_at=1000")
                    with self.assertRaises(LedgerError):
                        ledger.budget_closure("run")
                    with self.assertRaises(LedgerError):
                        ledger.finalize("run", "owner", 1, assurance="HEALTHY", work_complete=True, required_failure=False)
                    self.assertEqual(ledger._db.execute("SELECT count(*) FROM terminal_records").fetchone()[0], 0)

    def test_persisted_budget_hold_cannot_be_lowered_by_finalize_input(self):
        with self.open() as ledger:
            ledger.create_run("run")
            ledger.claim("run", "owner")
            ledger.reserve("run", "owner", 1, "op", "0.2")
            with self.assertRaises(LedgerError):
                ledger.settle("run", "owner", 1, "op", "0.3")
            ledger.confirm_stopped("run", "owner", 1)
            receipt = ledger.finalize("run", "owner", 1, assurance="HEALTHY", work_complete=True, required_failure=False)
            self.assertEqual(receipt["assurance"], "HOLD")
            self.assertEqual(receipt["ledger_hold_reason"], "settlement_over_reservation")
            self.assertEqual(receipt["execution_status"], "COMPLETED")
            self.assertIs(receipt["ci_eligible"], False)

    def test_stopped_run_can_finish_after_owner_restart_before_deadline(self):
        with self.open() as ledger:
            run = ledger.create_run("run")
            lease = ledger.claim("run", "old")
            ledger.confirm_stopped("run", "old", lease["owner_epoch"])
        self.now = lease["lease_until"]
        self.assertLess(self.now, run["deadline"])
        with self.open() as ledger:
            recovery = ledger.claim_recovery("run", "new")
            self.assertGreater(recovery["owner_epoch"], lease["owner_epoch"])
            with self.assertRaises(LedgerError):
                ledger.reserve("run", "new", recovery["owner_epoch"], "forbidden-new-work", "0.1")
            receipt = ledger.finalize("run", "new", recovery["owner_epoch"], assurance="HEALTHY", work_complete=True, required_failure=False)
            self.assertEqual(receipt["execution_status"], "COMPLETED")
            self.assertEqual(receipt["deadline"], run["deadline"])

    def test_late_execution_lease_cannot_settle_without_recovery(self):
        create_v1(self.db)
        with closing(sqlite3.connect(self.db)) as db:
            db.execute("UPDATE runs SET lease_until=2500")
            db.commit()
        Ledger.migrate_v1(self.db)
        self.now = 2300
        with self.open() as ledger:
            with self.assertRaises(LedgerError):
                ledger.settle("old", "owner", 1, "old-op", "1")
            recovery = ledger.claim_recovery("old", "owner")
            ledger.settle("old", "owner", recovery["owner_epoch"], "old-op", "1")
            self.assertEqual(ledger.budget_closure("old")["status"], "CLOSED")

    def test_late_cancel_and_stop_events_are_kept_once_without_rewriting_terminal(self):
        with self.open() as ledger:
            ledger.create_run("run")
            ledger.claim("run", "owner")
            receipt = ledger.finalize("run", "owner", 1, assurance="HOLD", work_complete=False, required_failure=True)
            before = ledger._db.execute("SELECT count(*) FROM run_events").fetchone()[0]
            for operation in (ledger.request_cancel, ledger.confirm_stopped):
                first = operation("run", "owner", 1)
                self.assertEqual(first["status"], "already_terminal")
                self.now += 1
                self.assertEqual(operation("run", "owner", 1), first)
            after = ledger._db.execute("SELECT count(*) FROM run_events").fetchone()[0]
            self.assertEqual(after - before, 2)
            self.assertEqual(ledger.get_terminal("run"), receipt)

    def test_migrated_lower_conflicting_cost_keeps_original_v1_exposure(self):
        create_v1(self.db)
        with closing(sqlite3.connect(self.db)) as db:
            db.execute("""UPDATE operations SET status='settled',settled_usd='1',settled_micros=1000000,
                       settled_at=1000,exposure_usd='0.5',exposure_micros=500000""")
            db.execute("UPDATE runs SET hold=1,hold_reason='settlement_conflict'")
            db.commit()
        Ledger.migrate_v1(self.db)
        with self.open() as ledger:
            snapshot = ledger.snapshot("old")
            self.assertEqual(snapshot["operations"][0]["exposure_micros"], 500000)
            self.assertEqual(snapshot["global_accounted_micros"], 1000000)
            self.assertEqual(ledger.budget_closure("old")["status"], "OPEN")

    def test_terminal_digest_does_not_replace_run_binding_check(self):
        with self.open() as ledger:
            for run_id in ("first", "second"):
                ledger.create_run(run_id)
                ledger.claim(run_id, "owner")
                ledger.finalize(run_id, "owner", 1, assurance="HOLD", work_complete=False, required_failure=True)
            source = ledger._db.execute("SELECT canonical_json,payload,sha256 FROM terminal_records WHERE run_id='second'").fetchone()
            ledger._db.execute("UPDATE terminal_records SET canonical_json=?,payload=?,sha256=? WHERE run_id='first'", tuple(source))
            with self.assertRaises(LedgerError):
                ledger.get_terminal("first")
            self.assertEqual(ledger.get_terminal("second")["run_id"], "second")
