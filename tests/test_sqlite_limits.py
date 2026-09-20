import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gah import sqlite_limits
from gah.sqlite_limits import (MAX_SQLITE_BYTES, SQLiteLimitError,
                               apply_sqlite_limits, connect_sqlite,
                               inspect_sqlite_limits)


class SQLiteLimitsTests(unittest.TestCase):
    def test_each_connection_gets_bounded_fixed_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.db"
            first = connect_sqlite(path, isolation_level=None)
            first.execute("CREATE TABLE t(value TEXT)")
            first.close()
            second = connect_sqlite(path, isolation_level=None)
            self.assertEqual(second.execute("PRAGMA page_size").fetchone()[0], 4096)
            self.assertEqual(second.execute("PRAGMA max_page_count").fetchone()[0], MAX_SQLITE_BYTES // 4096)
            self.assertEqual(second.execute("PRAGMA journal_mode").fetchone()[0], "delete")
            self.assertEqual(second.execute("PRAGMA synchronous").fetchone()[0], 2)
            self.assertEqual(second.execute("PRAGMA temp_store").fetchone()[0], 2)
            self.assertEqual(second.execute("SELECT count(*) FROM t").fetchone()[0], 0)
            second.close()

    def test_existing_page_size_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.db"
            db = sqlite3.connect(path, isolation_level=None)
            db.execute("PRAGMA page_size=1024")
            db.execute("CREATE TABLE t(value TEXT)")
            db.close()
            db = connect_sqlite(path, isolation_level=None)
            self.assertEqual(db.execute("PRAGMA page_size").fetchone()[0], 1024)
            self.assertEqual(db.execute("PRAGMA max_page_count").fetchone()[0], MAX_SQLITE_BYTES // 1024)
            db.close()

    def test_read_only_inspection_does_not_change_journal_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.db"
            db = sqlite3.connect(path, isolation_level=None)
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE t(value TEXT)")
            before = db.execute("PRAGMA journal_mode").fetchone()[0]
            result = inspect_sqlite_limits(db)
            self.assertEqual(db.execute("PRAGMA journal_mode").fetchone()[0], before)
            self.assertEqual(result["max_bytes"], MAX_SQLITE_BYTES)
            db.close()

    def test_existing_wal_is_rejected_without_journal_mode_change(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.db"
            db = sqlite3.connect(path, isolation_level=None)
            self.assertEqual(db.execute("PRAGMA journal_mode=WAL").fetchone()[0], "wal")
            db.execute("CREATE TABLE t(value TEXT)")
            db.execute("INSERT INTO t VALUES ('preserve')")
            db.close()
            db = sqlite3.connect(path, isolation_level=None)
            with self.assertRaisesRegex(SQLiteLimitError, "SQLITE_LIMIT_JOURNAL_MODE_UNSUPPORTED"):
                apply_sqlite_limits(db)
            self.assertEqual(db.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            self.assertEqual(db.execute("SELECT value FROM t").fetchone()[0], "preserve")
            db.close()

    def test_page_limit_returns_sqlite_full_and_preserves_rows_after_rollback(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bounded.db"
            db = sqlite3.connect(path, isolation_level=None)
            db.execute("CREATE TABLE t(value TEXT)")
            db.execute("INSERT INTO t VALUES ('preserve')")
            page_size = db.execute("PRAGMA page_size").fetchone()[0]
            db.close()
            with patch.object(sqlite_limits, "MAX_SQLITE_BYTES", page_size * 2):
                db = connect_sqlite(path, isolation_level=None)
                self.assertEqual(db.execute("PRAGMA max_page_count").fetchone()[0], 2)
                db.execute("BEGIN")
                with self.assertRaisesRegex(sqlite3.OperationalError, "full"):
                    db.execute("INSERT INTO t VALUES (?)", ("x" * (page_size * 8),))
                db.rollback()
                self.assertEqual(db.execute("SELECT value FROM t").fetchall(), [("preserve",)])
                self.assertLessEqual(db.execute("PRAGMA page_count").fetchone()[0], 2)
                db.close()
                reopened = connect_sqlite(path, isolation_level=None)
                self.assertEqual(reopened.execute("PRAGMA max_page_count").fetchone()[0], 2)
                with self.assertRaisesRegex(sqlite3.OperationalError, "full"):
                    reopened.execute("INSERT INTO t VALUES (?)", ("y" * (page_size * 8),))
                self.assertEqual(reopened.execute("SELECT value FROM t").fetchall(), [("preserve",)])
                reopened.close()

    def test_over_limit_existing_database_is_rejected_without_changing_data(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.db"
            db = sqlite3.connect(path, isolation_level=None)
            db.execute("CREATE TABLE t(value TEXT)")
            db.execute("INSERT INTO t VALUES ('preserve')")
            db.close()
            db = sqlite3.connect(path, isolation_level=None)
            with patch.object(sqlite_limits, "MAX_SQLITE_BYTES", 4096):
                with self.assertRaisesRegex(SQLiteLimitError, "SQLITE_LIMIT_EXCEEDED"):
                    apply_sqlite_limits(db)
            self.assertEqual(db.execute("SELECT value FROM t").fetchone()[0], "preserve")
            db.close()


if __name__ == "__main__":
    unittest.main()
