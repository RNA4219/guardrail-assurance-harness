"""契約遷移表のtransaction境界と制約を検査する。"""

import sqlite3
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gah import transition_migrations
from gah.adoption import AdoptionError


class TransitionMigrationTests(unittest.TestCase):
    def test_schema_requires_existing_transaction_and_has_exact_columns(self):
        db = sqlite3.connect(":memory:")
        self.addCleanup(db.close)
        with self.assertRaisesRegex(AdoptionError, "^TRANSACTION_REQUIRED$"):
            transition_migrations.create_schema(db)
        db.execute("BEGIN IMMEDIATE")
        transition_migrations.create_schema(db)
        self.assertEqual(
            {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' ")},
            set(transition_migrations.TABLES),
        )
        for table, expected in transition_migrations.TABLES.items():
            actual = {row[1] for row in db.execute('PRAGMA table_info("' + table + '")')}
            self.assertEqual(actual, expected, table)
        db.rollback()

    def test_second_ddl_failure_rolls_back_first_table(self):
        db = sqlite3.connect(":memory:")
        self.addCleanup(db.close)
        db.execute("BEGIN IMMEDIATE")

        def deny_second_table(action, first, *_):
            if action == sqlite3.SQLITE_CREATE_TABLE and first == "transition_runs":
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        db.set_authorizer(deny_second_table)
        with self.assertRaises(sqlite3.DatabaseError):
            transition_migrations.create_schema(db)
        db.rollback()
        self.assertEqual(
            {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}, set()
        )

    def test_candidate_side_is_unique_and_requires_candidate(self):
        db = sqlite3.connect(":memory:")
        self.addCleanup(db.close)
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("BEGIN IMMEDIATE")
        transition_migrations.create_schema(db)
        db.execute(
            "INSERT INTO transition_candidates VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("candidate-1", "proposal-1", "a" * 64, "series-1", "{}", "b" * 64, 1, 0, "validator", "validator-context"),
        )
        db.execute("INSERT INTO transition_runs VALUES(?,?,?)", ("old-run", "candidate-1", "old"))
        with self.assertRaises(sqlite3.IntegrityError):
            db.execute("INSERT INTO transition_runs VALUES(?,?,?)", ("old-run-2", "candidate-1", "old"))
        with self.assertRaises(sqlite3.IntegrityError):
            db.execute("INSERT INTO transition_runs VALUES(?,?,?)", ("orphan-run", "missing", "new"))
        db.rollback()


if __name__ == "__main__":
    unittest.main()
