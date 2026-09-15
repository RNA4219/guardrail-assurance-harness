"""同じ要求のソース識別を再利用しても、変更や失敗を成功へ昇格しない。"""
from pathlib import Path
import sqlite3,sys,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from gah import read_checks,evaluation_authority as authority
from gah.adoption import AdoptionError

class SourceCheckScopeTests(unittest.TestCase):
    def setUp(self):
        self.guard=patch.object(read_checks,'_source_invalid',False);self.guard.start();self.addCleanup(self.guard.stop)
        self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE sample(value INTEGER)');self.db.commit()
    def test_nested_checks_read_sources_at_request_start_and_end_only(self):
        @read_checks.checked_action
        def inner(db):return authority._source_digest()
        @read_checks.checked_action
        def outer(db):return [authority._source_digest() for _ in range(18)]+[inner(db)]
        with patch.object(authority,'_compute_source_digest',return_value='a'*64) as compute:
            self.assertEqual(outer(self.db),['a'*64]*19);self.assertEqual(compute.call_count,2)
            outer(self.db);self.assertEqual(compute.call_count,4)
        self.assertIsNone(read_checks.source_digest_in_scope())
    def test_changed_source_rolls_back_and_blocks_reuse_until_process_restart(self):
        @read_checks.checked_action
        def write(db):db.execute('INSERT INTO sample VALUES(1)');return authority._source_digest()
        with patch.object(authority,'_compute_source_digest',side_effect=['a'*64,'b'*64]):
            with self.assertRaisesRegex(AdoptionError,'EXTENSION_INVALID'):
                with self.db:write(self.db)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM sample').fetchone()[0],0)
        self.assertIsNone(read_checks._source_context.get())
        with self.assertRaisesRegex(AdoptionError,'EXTENSION_INVALID'):authority._source_digest()
    def test_failed_operation_resets_scope_and_later_request_checks_again(self):
        @read_checks.checked_action
        def fail(db):raise AdoptionError('EXPECTED')
        with patch.object(authority,'_compute_source_digest',return_value='a'*64) as compute:
            for _ in range(2):
                with self.assertRaisesRegex(AdoptionError,'EXPECTED'):fail(self.db)
            self.assertEqual(compute.call_count,4)
        self.assertIsNone(read_checks.source_digest_in_scope())
    def test_final_source_read_failure_rolls_back_and_blocks_later_calls(self):
        @read_checks.checked_action
        def write(db):
            db.execute('INSERT INTO sample VALUES(1)')
        with patch.object(authority, '_compute_source_digest', side_effect=['a'*64, OSError('unreadable')]):
            with self.assertRaisesRegex(AdoptionError, 'EXTENSION_INVALID'):
                with self.db:
                    write(self.db)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM sample').fetchone()[0], 0)
        self.assertIsNone(read_checks._source_context.get())
        with self.assertRaisesRegex(AdoptionError, 'EXTENSION_INVALID'):
            authority._source_digest()

    def test_initial_source_read_failure_does_not_enter_action(self):
        @read_checks.checked_action
        def write(db):
            db.execute('INSERT INTO sample VALUES(1)')
        with patch.object(authority, '_compute_source_digest', side_effect=OSError('unreadable')):
            with self.assertRaises(OSError):
                with self.db:
                    write(self.db)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM sample').fetchone()[0], 0)
        self.assertIsNone(read_checks._source_context.get())
        with patch.object(authority, '_compute_source_digest', return_value='a'*64):
            self.assertEqual(authority._source_digest(), 'a'*64)

    def test_outside_request_is_not_cached(self):
        with patch.object(authority,'_compute_source_digest',side_effect=['a'*64,'b'*64]) as compute:
            self.assertNotEqual(authority._source_digest(),authority._source_digest())
            self.assertEqual(compute.call_count,2)

if __name__=='__main__':unittest.main()
