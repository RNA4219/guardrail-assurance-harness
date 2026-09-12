"""同じ時刻の再照会は書込みを増やさず、時計巻戻しは拒否する。"""
from pathlib import Path
import sqlite3,sys,unittest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from gah import resources,run_evidence
from gah.contracts import ContractError

class ReadClockTests(unittest.TestCase):
    def db(self):
        db=sqlite3.connect(':memory:',isolation_level=None);db.row_factory=sqlite3.Row;self.addCleanup(db.close);return db
    def test_resource_clock_same_time_does_not_write_and_rollback_rejected(self):
        db=self.db();db.execute('BEGIN IMMEDIATE');resources.create_schema(db)
        book=resources.ResourceBook(db);book._touch(7);before=db.total_changes
        book._touch(7);self.assertEqual(db.total_changes,before)
        with self.assertRaisesRegex(resources.ResourceError,'CLOCK_ROLLBACK'):book._touch(6)
        self.assertEqual(db.total_changes,before);book._touch(8);self.assertEqual(db.total_changes,before+1)
        db.rollback()
    def test_evidence_book_clock_same_time_does_not_write(self):
        db=self.db();db.execute('BEGIN IMMEDIATE');run_evidence.create_schema(db)
        book=run_evidence.RunEvidenceBook(db,now=7,allowed_bindings={})
        self.assertEqual(book._now(db),7);before=db.total_changes
        self.assertEqual(book._now(db),7);self.assertEqual(db.total_changes,before)
        other=run_evidence.RunEvidenceBook(db,now=6,allowed_bindings={})
        with self.assertRaisesRegex(run_evidence.EvidenceError,'CLOCK_ROLLBACK'):other._now(db)
        self.assertEqual(db.total_changes,before);db.rollback()
    def test_corrupt_clock_does_not_become_accepted_no_op(self):
        db=self.db();db.execute('BEGIN IMMEDIATE');resources.create_schema(db)
        db.execute("UPDATE resource_meta SET value='invalid' WHERE key='last_clock'")
        with self.assertRaisesRegex(resources.ResourceError,'STORAGE_CORRUPT'):resources.ResourceBook(db)._touch(7)
        db.rollback()
    def test_clock_update_failure_still_raises(self):
        db=self.db();db.execute('BEGIN IMMEDIATE');resources.create_schema(db)
        book=resources.ResourceBook(db);book._touch(7)
        db.execute("CREATE TRIGGER fail_clock BEFORE UPDATE ON resource_meta BEGIN SELECT RAISE(ABORT,'test'); END")
        book._touch(7)
        with self.assertRaises(sqlite3.IntegrityError):book._touch(8)
        self.assertEqual(db.execute("SELECT value FROM resource_meta WHERE key='last_clock'").fetchone()[0],7)
        db.rollback()

if __name__=='__main__':unittest.main(verbosity=2)
