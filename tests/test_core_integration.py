"""親側で追加する、別プロセスと世代境界の検証。"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gah.ledger import Ledger, LedgerError


class CoreIntegrationTests(unittest.TestCase):
    def test_interruption_rolls_back_before_next_operation(self):
        interrupted = [True]

        def clock():
            if interrupted[0]:
                interrupted[0] = False
                raise KeyboardInterrupt()
            return 1000

        with Ledger(self.db, clock=clock) as ledger:
            with self.assertRaises(KeyboardInterrupt):
                ledger.create_run("first")
            self.assertEqual(ledger.create_run("second")["run_id"], "second")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "ledger.sqlite"
        self.now = 1000

    def test_separate_processes_share_atomic_global_budget(self):
        with Ledger(self.db, clock=lambda: self.now) as ledger:
            for run in ["seed", "a", "b"]:
                ledger.create_run(run, "full"); ledger.claim(run, run)
            ledger.reserve("seed", "seed", 1, "seed-op", "0.000001")
        worker = """
import json,sys
sys.path.insert(0, sys.argv[1])
from gah.ledger import Ledger, LedgerError
sys.stdin.readline()
with Ledger(sys.argv[2], clock=lambda: 1000) as ledger:
    try:
        ledger.reserve(sys.argv[3], sys.argv[3], 1, sys.argv[3]+'-op', '10')
        print(json.dumps({'accepted': True}))
    except LedgerError as error:
        print(json.dumps({'accepted': False, 'code': error.code}))
"""
        processes = [subprocess.Popen([sys.executable, "-E", "-X", "utf8", "-c", worker,
                                      str(ROOT / "src"), str(self.db), run], stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                      encoding="utf-8") for run in ["a", "b"]]
        try:
            for process in processes:
                process.stdin.write("go\n"); process.stdin.flush()
            results = []
            for process in processes:
                stdout, stderr = process.communicate(timeout=15)
                self.assertEqual(process.returncode, 0, stderr)
                results.append(json.loads(stdout))
            self.assertEqual(sum(item["accepted"] for item in results), 1)
            self.assertEqual([item["code"] for item in results if not item["accepted"]], ["budget_exceeded"])
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill(); process.communicate(timeout=5)
        with Ledger(self.db, clock=lambda: self.now) as reopened:
            self.assertEqual(reopened.snapshot("a")["global_accounted_micros"], 10_000_001)

    def test_takeover_fences_old_owner_and_keeps_deadline(self):
        with Ledger(self.db, clock=lambda: self.now) as ledger:
            original = ledger.create_run("run")
            old = ledger.claim("run", "old")
            ledger.reserve("run", "old", old["epoch"], "op", "1")
            self.now = old["lease_until"]
            current = ledger.claim("run", "new")
            self.assertGreater(current["epoch"], old["epoch"])
            with self.assertRaises(LedgerError):
                ledger.reserve("run", "old", old["epoch"], "new-op", "0.1")
            with self.assertRaises(LedgerError):
                ledger.settle("run", "old", old["epoch"], "op", "1")
            self.assertEqual(ledger.snapshot("run")["run"]["deadline"], original["deadline"])
        self.now = original["deadline"]
        with Ledger(self.db, clock=lambda: self.now) as reopened:
            with self.assertRaises(LedgerError):
                reopened.claim("run", "third")
            self.assertEqual(reopened.snapshot("run")["run_accounted_micros"], 1_000_000)

    def test_conflicting_settlement_blocks_another_run_with_exposure(self):
        with Ledger(self.db, clock=lambda: self.now) as ledger:
            ledger.create_run("first", "full"); lease = ledger.claim("first", "owner")
            ledger.reserve("first", "owner", lease["epoch"], "first-op", "1")
            ledger.settle("first", "owner", lease["epoch"], "first-op", "1")
            with self.assertRaises(LedgerError):
                ledger.settle("first", "owner", lease["epoch"], "first-op", "15")
            ledger.create_run("next", "full"); next_lease = ledger.claim("next", "next-owner")
            with self.assertRaises(LedgerError) as raised:
                ledger.reserve("next", "next-owner", next_lease["epoch"], "next-op", "6")
            self.assertEqual(raised.exception.code, "budget_exceeded")
            self.assertEqual(ledger.snapshot("first")["operations"][0]["settled_micros"], 1_000_000)

    def test_settlement_replay_does_not_refresh_rolling_timestamp(self):
        with Ledger(self.db, clock=lambda: self.now) as ledger:
            ledger.create_run("run"); lease = ledger.claim("run", "owner")
            ledger.reserve("run", "owner", lease["epoch"], "op", "1")
            first = ledger.settle("run", "owner", lease["epoch"], "op", "1")
            self.now += 20
            again = ledger.settle("run", "owner", lease["epoch"], "op", "1.0")
            self.assertEqual(first["settled_at"], again["settled_at"])


if __name__ == "__main__":
    unittest.main()
