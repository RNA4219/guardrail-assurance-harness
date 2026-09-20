import errno
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest import mock
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gah.storage_budget import (  # noqa: E402
    FailureSink,
    StorageBudget,
    StorageBudgetError,
    configure_new_sqlite,
    is_capacity_error,
    open_new_sqlite,
)


class _ReadbackMismatchConnection(sqlite3.Connection):
    """SQLite接続のreadbackだけを固定的に壊すDI用fake。"""

    def execute(self, sql, parameters=()):
        if sql.strip().upper() == "PRAGMA PAGE_SIZE":
            return _OneRowCursor((8192,))
        return super().execute(sql, parameters)


class _FullConnection(sqlite3.Connection):
    """設定時のSQLITE_FULL境界を再現するDI用fake。"""

    def execute(self, sql, parameters=()):
        if sql.strip().upper() == "PRAGMA PAGE_SIZE = 4096":
            raise sqlite3.DatabaseError("database or disk is full")
        return super().execute(sql, parameters)


class _OneRowCursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row

    def __iter__(self):
        return iter((self._row,))


class StorageBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def assertCode(self, code, callback, *args, **kwargs):
        with self.assertRaises(StorageBudgetError) as context:
            callback(*args, **kwargs)
        self.assertEqual(context.exception.code, code)

    def test_existing_bytes_and_rollback_are_reserved(self):
        (self.root / "existing.bin").write_bytes(b"x" * 30)
        budget = StorageBudget(
            self.root,
            100,
            rollback_reserve_bytes=20,
            atomic_temp_overhead_bytes=0,
        )
        self.assertEqual(budget.used_bytes, 30)
        self.assertEqual(budget.available_bytes, 50)
        reservation = budget.reserve(50, label="test")
        self.assertEqual(budget.active_reserved_bytes, 50)
        self.assertCode("BUDGET_EXCEEDED", budget.reserve, 1)
        snapshot = reservation.commit(30)
        self.assertEqual(snapshot["used_bytes"], 60)
        self.assertEqual(snapshot["available_bytes"], 20)
        self.assertEqual(budget.state, "OPEN")
        self.assertEqual(budget.close()["state"], "CLOSED")

    def test_general_budget_keeps_existing_hardlink_accounting(self):
        source = self.root / "source.bin"
        alias = self.root / "alias.bin"
        source.write_bytes(b"linked")
        try:
            alias.hardlink_to(source)
        except (OSError, NotImplementedError):
            self.skipTest("hardlinks unavailable on this filesystem")

        budget = StorageBudget(
            self.root,
            100,
            rollback_reserve_bytes=0,
            atomic_temp_overhead_bytes=0,
            recursive=False,
        )
        self.assertEqual(budget.used_bytes, len(b"linked") * 2)

    def test_reservations_are_atomic_under_concurrency(self):
        budget = StorageBudget(
            self.root,
            60,
            rollback_reserve_bytes=0,
            atomic_temp_overhead_bytes=0,
        )
        barrier = threading.Barrier(2)
        results = []
        result_lock = threading.Lock()

        def worker():
            try:
                reservation = budget.reserve(60, label="parallel")
            except StorageBudgetError as error:
                with result_lock:
                    results.append(error.code)
            else:
                with result_lock:
                    results.append("RESERVED")
                barrier.wait(timeout=2)
                reservation.abort()
                return
            barrier.wait(timeout=2)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive())
        self.assertCountEqual(results, ["RESERVED", "BUDGET_EXCEEDED"])
        self.assertEqual(budget.active_reserved_bytes, 0)

    def test_double_commit_and_failed_budget_do_not_reopen(self):
        budget = StorageBudget(
            self.root,
            100,
            rollback_reserve_bytes=0,
            atomic_temp_overhead_bytes=0,
        )
        reservation = budget.reserve(20)
        reservation.commit(10)
        self.assertCode("RESERVATION_FINALIZED", reservation.commit, 0)
        self.assertCode("RESERVATION_FINALIZED", reservation.abort)

        budget.fail_from(OSError(errno.ENOSPC, "disk full"))
        self.assertEqual(budget.state, "FAILED")
        self.assertEqual(budget.snapshot()["failure_code"], "CAPACITY_IO_ERROR")
        self.assertCode("BUDGET_FAILED", budget.reserve, 1)

    def test_atomic_write_accounts_for_temp_and_existing_file(self):
        (self.root / "old.bin").write_bytes(b"old!!")
        budget = StorageBudget(
            self.root,
            100,
            rollback_reserve_bytes=20,
            atomic_temp_overhead_bytes=0,
        )
        result = budget.atomic_write("nested/output.json", b"123456789")
        target = self.root / "nested" / "output.json"
        self.assertEqual(target.read_bytes(), b"123456789")
        self.assertEqual(result["net_bytes"], 9)
        self.assertEqual(budget.used_bytes, 14)

        overwrite = budget.atomic_write("nested/output.json", b"x")
        self.assertEqual(overwrite["net_bytes"], -8)
        self.assertEqual(target.read_bytes(), b"x")
        self.assertEqual(budget.observe()["used_bytes"], 6)

    def test_atomic_write_rejects_untracked_root_change_and_short_budget(self):
        budget = StorageBudget(
            self.root,
            5,
            rollback_reserve_bytes=0,
            atomic_temp_overhead_bytes=0,
        )
        self.assertCode(
            "BUDGET_EXCEEDED",
            budget.atomic_write,
            "too-large",
            b"123456",
        )
        self.assertFalse((self.root / "too-large").exists())

        (self.root / "external.bin").write_bytes(b"external")
        self.assertCode(
            "UNTRACKED_WRITE",
            budget.atomic_write,
            "after-external-change",
            b"x",
        )
        self.assertEqual(budget.state, "FAILED")

    def test_atomic_write_directory_fsync_failure_is_not_committed(self):
        budget = StorageBudget(
            self.root,
            100,
            rollback_reserve_bytes=0,
            atomic_temp_overhead_bytes=0,
        )
        with mock.patch(
            "gah.storage_budget._fsync_directory",
            side_effect=StorageBudgetError("DIRECTORY_FSYNC_FAILED"),
        ):
            self.assertCode(
                "DIRECTORY_FSYNC_FAILED",
                budget.atomic_write,
                "after-rename",
                b"payload",
            )
        self.assertEqual(budget.state, "FAILED")
        self.assertEqual(budget.active_reserved_bytes, 0)
        self.assertEqual((self.root / "after-rename").read_bytes(), b"payload")

    def test_atomic_write_readback_failure_closes_budget_after_replace(self):
        budget = StorageBudget(
            self.root,
            100,
            rollback_reserve_bytes=0,
            atomic_temp_overhead_bytes=0,
        )
        with mock.patch(
            "gah.storage_budget._digest_file",
            side_effect=StorageBudgetError("STORAGE_READ_FAILED"),
        ):
            self.assertCode(
                "STORAGE_READ_FAILED",
                budget.atomic_write,
                "readback-failure",
                b"payload",
            )
        self.assertEqual(budget.state, "FAILED")
        self.assertEqual(budget.active_reserved_bytes, 0)
        self.assertEqual((self.root / "readback-failure").read_bytes(), b"payload")

    def test_strict_integer_boundaries_cover_all_entry_points(self):
        self.assertCode(
            "INVALID_INTEGER_QUOTA",
            StorageBudget,
            self.root,
            True,
            rollback_reserve_bytes=0,
        )
        budget = StorageBudget(
            self.root,
            100,
            rollback_reserve_bytes=0,
            atomic_temp_overhead_bytes=0,
        )
        self.assertCode("INVALID_INTEGER_RESERVATION", budget.reserve, True)
        reservation = budget.reserve(1)
        self.assertCode("INVALID_INTEGER_NET_BYTES", reservation.commit, True)
        reservation.abort()
        self.assertCode(
            "INVALID_INTEGER_SINK_CAPACITY",
            FailureSink.preallocate,
            self.root / "sink",
            True,
        )
        connection = sqlite3.connect(":memory:")
        self.addCleanup(connection.close)
        self.assertCode(
            "INVALID_INTEGER_MAX_PAGE_COUNT",
            configure_new_sqlite,
            connection,
            max_page_count=True,
        )

    def test_sqlite_new_settings_are_read_back_and_existing_is_rejected(self):
        path = self.root / "new.sqlite"
        connection, configuration = open_new_sqlite(
            path,
            max_page_count=8,
            page_size=1024,
            rollback_reserve_bytes=2048,
        )
        try:
            self.assertEqual(configuration.page_size, 1024)
            self.assertEqual(configuration.max_page_count, 8)
            self.assertEqual(configuration.journal_mode, "delete")
            self.assertEqual(configuration.synchronous, 2)
            self.assertEqual(configuration.temp_store, 2)
            self.assertEqual(connection.execute("PRAGMA page_size").fetchone()[0], 1024)
            self.assertEqual(connection.execute("PRAGMA max_page_count").fetchone()[0], 8)
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "delete")
            self.assertEqual(connection.execute("PRAGMA synchronous").fetchone()[0], 2)
            self.assertEqual(connection.execute("PRAGMA temp_store").fetchone()[0], 2)
        finally:
            connection.close()

        existing = self.root / "existing.sqlite"
        existing_connection = sqlite3.connect(existing)
        existing_connection.execute("CREATE TABLE t (value TEXT)")
        existing_connection.commit()
        self.assertCode(
            "EXISTING_DATABASE",
            configure_new_sqlite,
            existing_connection,
            max_page_count=8,
        )
        existing_connection.close()
        self.assertCode(
            "EXISTING_DATABASE",
            open_new_sqlite,
            existing,
            max_page_count=8,
        )

    def test_sqlite_readback_and_sqlite_full_are_fail_closed(self):
        mismatch = sqlite3.connect(":memory:", factory=_ReadbackMismatchConnection)
        self.addCleanup(mismatch.close)
        self.assertCode(
            "SQLITE_READBACK_MISMATCH",
            configure_new_sqlite,
            mismatch,
            max_page_count=8,
        )

        full = sqlite3.connect(":memory:", factory=_FullConnection)
        self.addCleanup(full.close)
        self.assertCode(
            "CAPACITY_IO_ERROR",
            configure_new_sqlite,
            full,
            max_page_count=8,
        )
        self.assertTrue(is_capacity_error(sqlite3.DatabaseError("database or disk is full")))
        self.assertTrue(is_capacity_error(OSError(errno.ENOSPC, "no space")))

    def test_failure_sink_preallocates_reads_back_and_is_not_capacity_proof(self):
        sink = FailureSink.preallocate(
            self.root / "failure.bin",
            1024,
            root=self.root,
        )
        scope = sink.scope
        self.assertTrue(scope["preallocated"])
        self.assertTrue(scope["fsync_verified"])
        self.assertTrue(scope["readback_verified"])
        self.assertTrue(scope["same_root"])
        self.assertFalse(scope["durable_after_enospc"])
        self.assertFalse(scope["bound_verified"])
        value = {"state": "INCOMPLETE", "reason": "CAPACITY_IO_ERROR"}
        self.assertEqual(sink.read_record(), None)
        self.assertTrue(sink.record(value)["recorded"])
        self.assertEqual(sink.read_record(), value)
        self.assertCode("FAILURE_SINK_ALREADY_RECORDED", sink.record, value)

    def test_failure_sink_does_not_delete_existing_target(self):
        target = self.root / "failure.bin"
        target.write_bytes(b"keep")
        self.assertCode("SINK_EXISTS", FailureSink.preallocate, target, 1024)
        self.assertEqual(target.read_bytes(), b"keep")


if __name__ == "__main__":
    unittest.main()
