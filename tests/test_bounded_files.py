import multiprocessing
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gah.bounded_files import (BoundedFileError, _operation_lock_path, write_bounded)
from gah.storage_budget import StorageBudget, StorageBudgetError


def _concurrent_writer(root, name, start, queue):
    start.wait(10)
    try:
        write_bounded(Path(root) / name, b"x" * 2500, immutable=True,
                      _quota_bytes=8192)
        queue.put("ok")
    except BoundedFileError as error:
        queue.put(error.code)


class BoundedFileTests(unittest.TestCase):
    def test_immutable_replay_and_conflict(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "one.json"
            first = write_bounded(path, b"same")
            used = path.stat().st_size
            replay = write_bounded(path, b"same")
            self.assertFalse(first["replayed"])
            self.assertTrue(replay["replayed"])
            self.assertEqual(path.stat().st_size, used)
            with self.assertRaisesRegex(BoundedFileError, "RESULT_CONFLICT"):
                write_bounded(path, b"different")

    def test_mutable_requires_explicit_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            write_bounded(path, b"one", immutable=True)
            with self.assertRaisesRegex(BoundedFileError, "RESULT_CONFLICT"):
                write_bounded(path, b"two", immutable=True)
            write_bounded(path, b"two", immutable=False)
            self.assertEqual(path.read_bytes(), b"two")

    def test_document_and_capacity_bounds_include_temp_reserve(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(BoundedFileError, "DOCUMENT_TOO_LARGE"):
                write_bounded(root / "large", b"x" * (1024 * 1024 + 1))
            # operation_lock creates a one-byte lock file. Existing 4095 bytes plus
            # the 4096-byte atomic-temp reserve exceed this 8192-byte root quota.
            (root / "existing.bin").write_bytes(b"e" * 4096)
            with self.assertRaisesRegex(BoundedFileError, "CAPACITY_EXCEEDED"):
                write_bounded(root / "new.bin", b"n", _quota_bytes=8192)
            self.assertFalse((root / "new.bin").exists())
            self.assertEqual((root / "existing.bin").stat().st_size, 4096)

    def test_partial_write_failure_does_not_publish_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("gah.storage_budget.os.fsync", side_effect=OSError("injected")):
                with self.assertRaises(BoundedFileError):
                    write_bounded(root / "new.json", b"payload")
            self.assertFalse((root / "new.json").exists())
            self.assertFalse(any(item.name.startswith(".gah-budget-") for item in root.iterdir()))

    def test_crash_leaves_unpublished_temp_and_replay_fails_closed_until_capacity_allows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            code = "\n".join([
                "import os, sys, tempfile",
                "sys.path.insert(0, " + repr(str(Path(__file__).resolve().parents[1] / "src")) + ")",
                "from unittest.mock import patch",
                "from gah.bounded_files import write_bounded",
                "real = tempfile.mkstemp",
                "def crash(*args, **kwargs):",
                "    fd, name = real(*args, **kwargs)",
                "    os.write(fd, b\"partial\")",
                "    os.close(fd)",
                "    os._exit(23)",
                "with patch(\"gah.storage_budget.tempfile.mkstemp\", side_effect=crash):",
                "    write_bounded(sys.argv[1], b\"payload\")",
            ])
            env = os.environ.copy()
            env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
            result = __import__("subprocess").run(
                [sys.executable, "-E", "-B", "-X", "utf8", "-c", code,
                 str(root / "target.json")], env=env, capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 23)
            self.assertFalse((root / "target.json").exists())
            leftovers = [p for p in root.iterdir() if p.name.startswith(".gah-budget-")]
            self.assertEqual(len(leftovers), 1)
            self.assertEqual(leftovers[0].read_bytes(), b"partial")
            # The partial bytes remain accounted for; an intentionally tiny quota
            # fails closed and does not delete/rewrite the crash artifact.
            with self.assertRaises(BoundedFileError):
                write_bounded(root / "target.json", b"payload", _quota_bytes=4096)
            self.assertEqual(leftovers[0].read_bytes(), b"partial")

    def test_same_root_writers_serialize_across_processes(self):
        with tempfile.TemporaryDirectory() as directory:
            context = multiprocessing.get_context("spawn")
            start = context.Event()
            queue = context.Queue()
            processes = [context.Process(target=_concurrent_writer,
                                         args=(directory, f"file-{i}.bin", start, queue))
                         for i in range(2)]
            for process in processes:
                process.start()
            start.set()
            results = [queue.get(timeout=20) for _ in processes]
            for process in processes:
                process.join(20)
                self.assertEqual(process.exitcode, 0)
            self.assertCountEqual(results, ["ok", "CAPACITY_EXCEEDED"])
            self.assertEqual(len(list(Path(directory).glob("file-*.bin"))), 1)

    def test_lock_file_hardlink_is_rejected_before_lock_open(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lock_path = _operation_lock_path(root)
            source = root / "lock-alias"
            source.write_bytes(b"0")
            os.link(source, lock_path)
            with self.assertRaisesRegex(BoundedFileError, "PATH_REJECTED"):
                write_bounded(root / "target", b"payload")
            self.assertFalse((root / "target").exists())

    def test_regular_file_hardlink_is_rejected_by_fresh_stat(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.bin"
            alias = root / "alias.bin"
            source.write_bytes(b"linked")
            try:
                os.link(source, alias)
            except (OSError, NotImplementedError):
                self.skipTest("hardlinks unavailable on this filesystem")
            self.assertGreater(os.stat(alias, follow_symlinks=False).st_nlink, 1)
            with self.assertRaisesRegex(BoundedFileError, "PATH_REJECTED"):
                write_bounded(root / "target.bin", b"payload")
            self.assertFalse((root / "target.bin").exists())

    def test_nested_roots_have_separate_lock_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            nested = root / "child"
            nested.mkdir()
            self.assertNotEqual(_operation_lock_path(root), _operation_lock_path(nested))

    def test_entry_count_limit_is_checked_during_scandir_iteration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "one").write_bytes(b"1")
            (root / "two").write_bytes(b"2")
            with patch("gah.bounded_files.MAX_DIRECTORY_ENTRIES", 2):
                with self.assertRaisesRegex(BoundedFileError, "DIRECTORY_ENTRY_LIMIT"):
                    write_bounded(root / "target", b"x")
            self.assertFalse((root / "target").exists())

    def test_nested_directory_is_not_recursively_scanned_or_counted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            child = root / ".ga"
            child.mkdir()
            large_db = child / "large-existing.db"
            with large_db.open("wb") as stream:
                stream.truncate(300 * 1024 * 1024)
            original_scandir = os.scandir
            def reject_child_scan(path):
                if Path(path).resolve() == child.resolve():
                    raise AssertionError("child directory must remain unscanned")
                return original_scandir(path)
            with patch("gah.bounded_files.os.scandir", side_effect=reject_child_scan):
                write_bounded(root / "record.json", b"ok")
            self.assertEqual(large_db.stat().st_size, 300 * 1024 * 1024)

    def test_oversized_direct_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "existing.bin").write_bytes(b"x" * 5000)
            with self.assertRaisesRegex(BoundedFileError, "CAPACITY_EXCEEDED"):
                write_bounded(root / "record.json", b"ok", _quota_bytes=4096)
            self.assertFalse((root / "record.json").exists())


    def test_external_change_after_budget_creation_is_rejected_by_precheck(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = StorageBudget.atomic_write

            def add_external_file_then_write(budget, relative_path, data, **kwargs):
                (root / "external.bin").write_bytes(b"external")
                return original(budget, relative_path, data, **kwargs)

            with patch.object(StorageBudget, "atomic_write", add_external_file_then_write):
                with self.assertRaisesRegex(BoundedFileError, "IO_ERROR"):
                    write_bounded(root / "target.bin", b"payload")
            self.assertEqual((root / "external.bin").read_bytes(), b"external")
            self.assertFalse((root / "target.bin").exists())

    def test_invalid_external_hardlink_after_budget_creation_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = StorageBudget.atomic_write

            def add_invalid_entry_then_write(budget, relative_path, data, **kwargs):
                source = root / "external.bin"
                source.write_bytes(b"external")
                os.link(source, root / "external-alias.bin")
                # Exercise the actual fresh-stat precheck after initialization.
                return original(budget, relative_path, data, **kwargs)

            with patch.object(StorageBudget, "atomic_write", add_invalid_entry_then_write):
                with self.assertRaisesRegex(BoundedFileError, "PATH_REJECTED"):
                    write_bounded(root / "target.bin", b"payload")
            self.assertFalse((root / "target.bin").exists())

    def test_flat_root_scan_count_scales_with_writes_without_duplicate_walks(self):
        original_scandir = os.scandir

        def run_case(document_count):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                visits = 0
                scans = 0

                class CountingIterator:
                    def __init__(self, iterator):
                        self.iterator = iterator

                    def __enter__(self):
                        self.iterator.__enter__()
                        return self

                    def __exit__(self, *args):
                        return self.iterator.__exit__(*args)

                    def __iter__(self):
                        return self

                    def __next__(self):
                        nonlocal visits
                        entry = next(self.iterator)
                        visits += 1
                        return entry

                def counted_scandir(path):
                    nonlocal scans
                    if Path(path).resolve() == root:
                        scans += 1
                        return CountingIterator(original_scandir(path))
                    return original_scandir(path)

                with patch("os.scandir", side_effect=counted_scandir):
                    for index in range(document_count):
                        write_bounded(root / f"doc-{index}.bin", b"x")
                return scans, visits

        scans_100, visits_100 = run_case(100)
        scans_200, visits_200 = run_case(200)
        self.assertEqual(scans_100, 300)
        self.assertEqual(scans_200, 600)
        self.assertEqual(visits_100, 15250)
        self.assertEqual(visits_200, 60500)


if __name__ == "__main__":
    unittest.main()
