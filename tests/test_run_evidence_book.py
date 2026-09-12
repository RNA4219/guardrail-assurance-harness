"""共有SQLite transaction内のRunEvidenceBook境界を検査する。"""

from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from gah.run_evidence import EvidenceError, RunEvidenceBook, RunEvidenceStore, bound_bundle_digest, create_schema
from test_run_evidence import ZERO, attempt_for, bound_fixture


class RunEvidenceBookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fixtures, self.plan, self.bound = bound_fixture()
        self.fixtures["contract_ref_digest"] = self.bound["manifest"]["contract_ref"]["digest"]
        self.fixtures["policy_ref_digest"] = self.bound["manifest"]["policy_ref"]["digest"]
        self.profile = {"fixture_digest": ZERO, "adapter_digests": ["1" * 64], "isolation_digest": "2" * 64}

    def _connection(self):
        db = sqlite3.connect(str(Path(self.temp.name) / "shared.sqlite"), isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("BEGIN IMMEDIATE")
        create_schema(db)
        return db

    def test_schema_requires_outer_transaction_and_does_not_change_user_version(self):
        db = sqlite3.connect(":memory:", isolation_level=None)
        self.addCleanup(db.close)
        self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 0)
        with self.assertRaisesRegex(EvidenceError, "^TRANSACTION_REQUIRED$"):
            create_schema(db)
        db.execute("BEGIN IMMEDIATE")
        create_schema(db)
        self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 0)
        db.rollback()

    def test_book_requires_transaction_and_does_not_own_connection(self):
        db = sqlite3.connect(":memory:", isolation_level=None)
        self.addCleanup(db.close)
        with self.assertRaisesRegex(EvidenceError, "^TRANSACTION_REQUIRED$"):
            RunEvidenceBook(db, now=100, allowed_bindings={})
        db.execute("BEGIN IMMEDIATE")
        create_schema(db)
        book = RunEvidenceBook(db, now=100, allowed_bindings={})
        book.close()
        self.assertTrue(db.in_transaction)
        db.rollback()

    def test_book_and_store_have_same_start_result(self):
        allowed = {"run-1": bound_bundle_digest(self.bound)}
        path = Path(self.temp.name) / "store.sqlite"
        with RunEvidenceStore(path, clock=lambda: 100, allowed_bindings=allowed) as store:
            store_result = store.start_run(self.bound, self.profile)
        db = self._connection()
        try:
            book = RunEvidenceBook(db, now=100, allowed_bindings=allowed)
            book_result = book.start_run(self.bound, self.profile)
            self.assertEqual(book_result, store_result)
            self.assertTrue(db.in_transaction)
            db.commit()
        finally:
            db.close()

    def test_book_and_store_share_record_and_aggregate_results(self):
        allowed = {"run-1": bound_bundle_digest(self.bound)}
        attempt = attempt_for(self.fixtures, self.plan, "obligation-constraint",
                              attempt_id="shared-attempt", mode="constraint")
        store_path = Path(self.temp.name) / "store-results.sqlite"
        with RunEvidenceStore(store_path, clock=lambda: 100, allowed_bindings=allowed) as store:
            store.start_run(self.bound, self.profile)
            store_receipt = store.record_attempt(attempt)
            store_aggregate = store.aggregate("run-1")
        db = sqlite3.connect(":memory:", isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN IMMEDIATE")
            create_schema(db)
            book = RunEvidenceBook(db, now=100, allowed_bindings=allowed)
            book.start_run(self.bound, self.profile)
            book_receipt = book.record_attempt(attempt)
            book_aggregate = book.aggregate("run-1")
            self.assertEqual(book_receipt, store_receipt)
            self.assertEqual(book_aggregate, store_aggregate)
            db.rollback()
        finally:
            db.close()

    def test_all_book_changes_rollback_with_outer_transaction(self):
        allowed = {"run-1": bound_bundle_digest(self.bound)}
        db = self._connection()
        try:
            book = RunEvidenceBook(db, now=100, allowed_bindings=allowed)
            book.start_run(self.bound, self.profile)
            self.assertEqual(db.execute("SELECT count(*) FROM bound_runs").fetchone()[0], 1)
            db.rollback()
            db.execute("BEGIN IMMEDIATE")
            create_schema(db)
            self.assertEqual(db.execute("SELECT count(*) FROM bound_runs").fetchone()[0], 0)
            db.commit()
        finally:
            db.close()

    def test_book_uses_fixed_authority_time_and_rechecks_same_transaction(self):
        allowed = {"run-1": bound_bundle_digest(self.bound)}
        db = self._connection()
        try:
            book = RunEvidenceBook(db, now=100, allowed_bindings=allowed)
            started = book.start_run(self.bound, self.profile)
            attempt = attempt_for(self.fixtures, self.plan, "obligation-constraint",
                                  attempt_id="book-attempt", mode="constraint")
            receipt = book.record_attempt(attempt)
            self.assertTrue(receipt["accepted"])
            aggregate = book.aggregate("run-1")
            self.assertEqual(aggregate["observed_at"], 100)
            self.assertEqual(book.get_attempt("book-attempt")["attempt_digest"], receipt["attempt_digest"])
            self.assertTrue(db.in_transaction)
            db.commit()
            db.execute("BEGIN IMMEDIATE")
            book = RunEvidenceBook(db, now=99, allowed_bindings=allowed)
            with self.assertRaisesRegex(EvidenceError, "^CLOCK_ROLLBACK$"):
                book.get_run("run-1")
            db.rollback()
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
