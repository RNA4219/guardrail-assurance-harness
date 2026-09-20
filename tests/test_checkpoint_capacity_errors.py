import errno
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gah.bounded_files import BoundedFileError
from gah.supervisor_checkpoint import Checkpoint, CheckpointError


class CheckpointCapacityErrorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name) / "run"
        self.checkpoint = Checkpoint(self.folder)
        self.checkpoint.put("stable", {"value": "original"})

    def _inject_fsync_errno(self, expected_call, error_number):
        real_fsync = os.fsync
        calls = 0

        def injected(fd):
            nonlocal calls
            calls += 1
            if calls == expected_call:
                raise OSError(error_number, "synthetic private path must not escape")
            return real_fsync(fd)

        return patch("gah.storage_budget.os.fsync", side_effect=injected), lambda: calls

    def _assert_unpublished_and_clean(self):
        self.assertEqual(self.checkpoint.get("stable"), {"value": "original"})
        self.assertIsNone(self.checkpoint.get("new-record"))
        self.assertFalse(any(p.name.startswith(".gah-budget-")
                             for p in self.folder.iterdir()))

    def test_os_enospc_survives_writer_and_checkpoint_mapping(self):
        # The existing lock file is reused; fsync #1 is the atomic temp document.
        injected, calls = self._inject_fsync_errno(1, errno.ENOSPC)
        with injected:
            with self.assertRaisesRegex(
                    CheckpointError, "CHECKPOINT_STORAGE_CAPACITY_IO_ERROR") as caught:
                self.checkpoint.put("new-record", {"value": "must not publish"})
        self.assertEqual(calls(), 1)
        self.assertEqual(str(caught.exception), "CHECKPOINT_STORAGE_CAPACITY_IO_ERROR")
        self._assert_unpublished_and_clean()

    def test_other_os_io_error_keeps_generic_fixed_mapping(self):
        injected, calls = self._inject_fsync_errno(1, errno.EIO)
        with injected:
            with self.assertRaisesRegex(OSError, "CHECKPOINT_WRITE_FAILED") as caught:
                self.checkpoint.put("new-record", {"value": "must not publish"})
        self.assertEqual(calls(), 1)
        self.assertEqual(str(caught.exception), "CHECKPOINT_WRITE_FAILED")
        self._assert_unpublished_and_clean()

    def test_first_lock_capacity_failure_is_not_lost(self):
        for code in (errno.ENOSPC, getattr(errno, "EDQUOT", errno.ENOSPC)):
            with self.subTest(errno=code):
                folder = self.folder / ("first-" + str(code))
                checkpoint = Checkpoint(folder)
                injected, calls = self._inject_fsync_errno(1, code)
                with injected, self.assertRaisesRegex(
                        CheckpointError, "^CHECKPOINT_STORAGE_CAPACITY_IO_ERROR$"):
                    checkpoint.put("first", {"value": "not published"})
                self.assertEqual(calls(), 1)
                self.assertIsNone(checkpoint.get("first"))
                self.assertFalse(any(p.name.startswith(".gah-budget-") for p in folder.iterdir()))

    def test_directory_creation_and_existing_readback_preserve_capacity_errno(self):
        from gah.bounded_files import write_bounded
        with patch("gah.bounded_files.Path.mkdir", side_effect=OSError(errno.ENOSPC, "private")):
            with self.assertRaisesRegex(BoundedFileError, "^CAPACITY_IO_ERROR$"):
                write_bounded(self.folder / "missing" / "data", b"data")
        target = self.folder / "existing-data"
        write_bounded(target, b"existing")
        with patch("gah.bounded_files.os.fsync", side_effect=OSError(errno.ENOSPC, "private")):
            with self.assertRaisesRegex(BoundedFileError, "^CAPACITY_IO_ERROR$"):
                write_bounded(target, b"existing")
        self.assertEqual(target.read_bytes(), b"existing")

    def test_budget_limit_is_not_reclassified_as_os_capacity_failure(self):
        with patch("gah.supervisor_checkpoint.write_bounded",
                   side_effect=BoundedFileError("CAPACITY_EXCEEDED")):
            with self.assertRaisesRegex(CheckpointError,
                                        "CHECKPOINT_STORAGE_CAPACITY_EXCEEDED") as caught:
                self.checkpoint.put("new-record", {"value": "quota"})
        self.assertEqual(str(caught.exception), "CHECKPOINT_STORAGE_CAPACITY_EXCEEDED")
        self._assert_unpublished_and_clean()


if __name__ == "__main__":
    unittest.main()
