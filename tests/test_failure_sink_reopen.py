import json
import os
from pathlib import Path
import subprocess
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gah.storage_budget import FailureSink, StorageBudgetError


class FailureSinkReopenTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.target = self.root / "failure.bin"
        self.capacity = 4096

    def assertCode(self, code, callback, *args, **kwargs):
        with self.assertRaises(StorageBudgetError) as caught:
            callback(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)

    def test_new_process_reopens_blank_records_then_rejects_rewrite(self):
        original = {"reason": "CAPACITY_IO_ERROR", "receipt_ref": "fixed-receipt",
                    "unsettled": [{"operation_id": "fixed-op", "settled": False}]}
        FailureSink.preallocate(self.target, self.capacity, root=self.root)
        source = Path(__file__).resolve().parents[1] / "src"
        child = (
            "import json,sys; sys.path.insert(0,sys.argv[1]); "
            "from gah.storage_budget import FailureSink; "
            "s=FailureSink.open_existing(sys.argv[2],int(sys.argv[3]),root=sys.argv[4]); "
            "print(json.dumps(s.record(json.loads(sys.argv[5])),sort_keys=True))"
        )
        completed = subprocess.run(
            [sys.executable, "-I", "-B", "-c", child, str(source), str(self.target),
             str(self.capacity), str(self.root), json.dumps(original, separators=(",", ":"))],
            capture_output=True, text=True, encoding="utf-8", timeout=10, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        reopened = FailureSink.open_existing(self.target, self.capacity, root=self.root)
        self.assertEqual(reopened.read_record(), original)
        self.assertTrue(reopened.scope["readback_verified"])
        self.assertFalse(reopened.scope["fsync_verified"])
        self.assertFalse(reopened.scope["durable_after_enospc"])
        self.assertFalse(reopened.scope["bound_verified"])
        self.assertCode("FAILURE_SINK_ALREADY_RECORDED", reopened.record,
                        {"reason": "second"})
        self.assertEqual(FailureSink.read_existing(self.target, self.capacity,
                                                   root=self.root), original)

    def test_two_open_instances_cannot_overwrite_first_envelope(self):
        first = FailureSink.preallocate(self.target, self.capacity, root=self.root)
        second = FailureSink.open_existing(self.target, self.capacity, root=self.root)
        original = {"reason": "CAPACITY_IO_ERROR", "slot": 1}
        self.assertTrue(first.record(original)["recorded"])
        self.assertCode("FAILURE_SINK_ALREADY_RECORDED", second.record,
                        {"reason": "OTHER", "slot": 2})
        self.assertEqual(FailureSink.read_existing(self.target, self.capacity,
                                                   root=self.root), original)

    def test_recorded_instance_read_record_rejects_same_inode_content_mutation(self):
        sink = FailureSink.preallocate(self.target, self.capacity, root=self.root)
        original = {"reason": "CAPACITY_IO_ERROR", "slot": "original"}
        sink.record(original)
        changed = {"reason": "CAPACITY_IO_ERROR", "slot": "changed"}
        payload = json.dumps(changed, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")).encode("utf-8")
        header = b"GAHFAIL1" + struct.pack("<Q", len(payload))
        with self.target.open("r+b") as stream:
            stream.seek(0)
            stream.write(header + payload + b"\0" *
                         (self.capacity - len(header) - len(payload)))
            stream.flush()
        self.assertEqual(FailureSink.read_existing(self.target, self.capacity,
                                                    root=self.root), changed)
        self.assertCode("SINK_READBACK_FAILED", sink.read_record)

    def test_recorded_instance_does_not_overwrite_if_same_inode_was_blank_reset(self):
        sink = FailureSink.preallocate(self.target, self.capacity, root=self.root)
        original = {"reason": "CAPACITY_IO_ERROR", "slot": 1}
        sink.record(original)
        with self.target.open("r+b") as stream:
            stream.seek(0)
            stream.write(b"\0" * self.capacity)
            stream.flush()
        self.assertCode("SINK_READBACK_FAILED", sink.record,
                        {"reason": "SECOND_WRITE"})
        self.assertEqual(self.target.read_bytes(), b"\0" * self.capacity)

    def test_path_replacement_after_open_is_rejected_without_writing_replacement(self):
        sink = FailureSink.preallocate(self.target, self.capacity, root=self.root)
        original = self.root / "original-inode.bin"
        self.target.replace(original)
        self.target.write_bytes(b"\0" * self.capacity)
        self.assertCode("PATH_REJECTED", sink.record, {"reason": "CAPACITY_IO_ERROR"})
        self.assertEqual(self.target.read_bytes(), b"\0" * self.capacity)
        self.assertEqual(original.read_bytes(), b"\0" * self.capacity)

    def test_open_race_replacing_path_between_stat_and_open_is_rejected(self):
        FailureSink.preallocate(self.target, self.capacity, root=self.root)
        original = self.root / "pre-race.bin"
        real_open = os.open
        swapped = False

        def swap_then_open(path, flags, *args, **kwargs):
            nonlocal swapped
            if Path(path) == self.target and not swapped:
                swapped = True
                self.target.replace(original)
                self.target.write_bytes(b"\0" * self.capacity)
            return real_open(path, flags, *args, **kwargs)

        with patch("gah.storage_budget.os.open", side_effect=swap_then_open):
            self.assertCode("PATH_REJECTED", FailureSink.open_existing,
                            self.target, self.capacity, root=self.root)
        self.assertTrue(swapped)
        self.assertEqual(self.target.read_bytes(), b"\0" * self.capacity)
        self.assertEqual(original.read_bytes(), b"\0" * self.capacity)

    def test_symlink_is_rejected_without_following(self):
        referent = self.root / "referent.bin"
        FailureSink.preallocate(referent, self.capacity, root=self.root)
        link = self.root / "failure-link"
        try:
            link.symlink_to(referent)
        except (OSError, NotImplementedError) as exc:
            self.skipTest("symlink creation unavailable: " + type(exc).__name__)
        self.assertCode("PATH_REJECTED", FailureSink.open_existing,
                        link, self.capacity, root=self.root)

    @unittest.skipUnless(hasattr(os, "mkfifo") and os.name == "posix",
                         "POSIX FIFO support unavailable")
    def test_fifo_is_rejected_without_blocking(self):
        fifo = self.root / "failure.fifo"
        os.mkfifo(fifo)
        self.assertCode("PATH_REJECTED", FailureSink.open_existing,
                        fifo, self.capacity, root=self.root)

    def test_deep_and_nonfinite_existing_payloads_are_rejected(self):
        for name, payload in (("deep", (b"[" * 1500) + b"0" + (b"]" * 1500)),
                              ("nonfinite", b"{\"x\":NaN}")):
            target = self.root / (name + ".bin")
            FailureSink.preallocate(target, self.capacity, root=self.root)
            header = b"GAHFAIL1" + len(payload).to_bytes(8, "little")
            data = header + payload + b"\0" * (self.capacity - len(header) - len(payload))
            target.write_bytes(data)
            self.assertCode("SINK_READBACK_FAILED", FailureSink.open_existing,
                            target, self.capacity, root=self.root)
            self.assertEqual(target.read_bytes(), data)

    def test_corrupt_existing_file_is_rejected_before_any_write(self):
        FailureSink.preallocate(self.target, self.capacity, root=self.root)
        self.target.write_bytes(b"not a failure sink")
        before = self.target.read_bytes()
        self.assertCode("SINK_READBACK_FAILED", FailureSink.open_existing,
                        self.target, self.capacity, root=self.root)
        self.assertEqual(self.target.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
