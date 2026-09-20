import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

from gah.storage_budget import FailureSink, StorageBudgetError


class FailureSinkRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def assertCode(self, code, function, *args, **kwargs):
        with self.assertRaises(StorageBudgetError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)

    def test_recorded_envelope_reads_after_new_process_without_memory_flag(self):
        target = self.root / "failure.bin"
        sink = FailureSink.preallocate(target, 4096, root=self.root)
        envelope = {
            "state": "INCOMPLETE",
            "reason": "CAPACITY_IO_ERROR",
            "synthetic_fixture": True,
            "original_receipt": {"receipt_ref": "fixed-original-receipt"},
            "unsettled_reservations": [{"reservation_id": "synthetic-open-reservation",
                                        "state": "OPEN"}],
        }
        self.assertIsNone(FailureSink.read_existing(target, 4096, root=self.root))
        sink.record(envelope)
        self.assertEqual(sink.read_record(), envelope)
        self.assertFalse(sink.scope["durable_after_enospc"])
        self.assertFalse(sink.scope["bound_verified"])
        del sink

        source = Path(__file__).resolve().parents[1] / "src"
        child = (
            "import json,sys; sys.path.insert(0,sys.argv[1]); "
            "from gah.storage_budget import FailureSink; "
            "print(json.dumps(FailureSink.read_existing(sys.argv[2],int(sys.argv[3]),root=sys.argv[4]),"
            "sort_keys=True,separators=(',',':')))"
        )
        completed = subprocess.run(
            [sys.executable, "-I", "-B", "-c", child, str(source), str(target),
             "4096", str(self.root)],
            capture_output=True, text=True, encoding="utf-8", timeout=10, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout), envelope)

    def test_blank_record_size_and_root_contracts(self):
        target = self.root / "blank.bin"
        FailureSink.preallocate(target, 64, root=self.root)
        self.assertIsNone(FailureSink.read_existing(target, 64, root=self.root))
        self.assertCode("SINK_READBACK_FAILED", FailureSink.read_existing,
                        target, 63, root=self.root)
        other = self.root / "outside"
        other.mkdir()
        # root follows preallocate's same_root metadata semantics; it is not an allowlist.
        self.assertIsNone(FailureSink.read_existing(target, 64, root=other))
        target.write_bytes(b"x" * 65)
        self.assertCode("SINK_READBACK_FAILED", FailureSink.read_existing,
                        target, 64, root=self.root)

    def test_deep_json_is_rejected_with_fixed_error(self):
        target = self.root / "deep-json.bin"
        FailureSink.preallocate(target, 8192, root=self.root)
        payload = (b"[" * 1500) + b"0" + (b"]" * 1500)
        header = b"GAHFAIL1" + struct.pack("<Q", len(payload))
        target.write_bytes(header + payload + b"\0" * (8192 - len(header) - len(payload)))
        self.assertCode("SINK_READBACK_FAILED", FailureSink.read_existing,
                        target, 8192, root=self.root)

    @unittest.skipUnless(hasattr(os, "mkfifo") and os.name == "posix",
                         "POSIX FIFO support unavailable")
    def test_fifo_is_rejected_without_blocking_reader(self):
        target = self.root / "sink.fifo"
        os.mkfifo(target)
        self.assertCode("PATH_REJECTED", FailureSink.read_existing,
                        target, 128, root=self.root)

    def test_rejects_corrupt_header_payload_padding_and_hardlink(self):
        bad_header = self.root / "bad-header.bin"
        FailureSink.preallocate(bad_header, 128, root=self.root)
        with bad_header.open("r+b") as stream:
            stream.write(b"NOT-A-HEADER!!!")
        self.assertCode("SINK_READBACK_FAILED", FailureSink.read_existing,
                        bad_header, 128, root=self.root)

        bad_json = self.root / "bad-json.bin"
        FailureSink.preallocate(bad_json, 128, root=self.root)
        header = b"GAHFAIL1" + struct.pack("<Q", 1)
        bad_json.write_bytes(header + b"\xff" + b"\0" * (128 - len(header) - 1))
        self.assertCode("SINK_READBACK_FAILED", FailureSink.read_existing,
                        bad_json, 128, root=self.root)

        for name, payload in (("non-object.bin", b"[]"),
                              ("duplicate-key.bin", b'{"x":1,"x":2}')):
            target_json = self.root / name
            FailureSink.preallocate(target_json, 128, root=self.root)
            head = b"GAHFAIL1" + struct.pack("<Q", len(payload))
            target_json.write_bytes(head + payload + b"\0" * (128 - len(head) - len(payload)))
            self.assertCode("SINK_READBACK_FAILED", FailureSink.read_existing,
                            target_json, 128, root=self.root)

        bad_padding = self.root / "bad-padding.bin"
        FailureSink.preallocate(bad_padding, 128, root=self.root)
        header = b"GAHFAIL1" + struct.pack("<Q", 2)
        bad_padding.write_bytes(header + b"{}" + b"\0" * (128 - len(header) - 3) + b"x")
        self.assertCode("SINK_READBACK_FAILED", FailureSink.read_existing,
                        bad_padding, 128, root=self.root)

        target = self.root / "hardlink.bin"
        FailureSink.preallocate(target, 128, root=self.root)
        alias = self.root / "hardlink-alias.bin"
        try:
            os.link(target, alias)
        except (OSError, NotImplementedError) as exc:
            self.skipTest("filesystem does not support hardlinks: " + type(exc).__name__)
        self.addCleanup(lambda: alias.unlink(missing_ok=True))
        self.assertCode("PATH_REJECTED", FailureSink.read_existing,
                        target, 128, root=self.root)


if __name__ == "__main__":
    unittest.main()