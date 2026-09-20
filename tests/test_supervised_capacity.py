"""容量記録がauthorityの完了・停止・精算を代用しないことを検査する。"""
import errno
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from gah.storage_budget import FailureSink, StorageBudgetError
from gah.supervised_capacity import FAILURE_SINK_BYTES, FAILURE_SINK_NAME, capacity_reason
from gah.supervisor_checkpoint import Checkpoint, CheckpointError
from tools import gah_run


class SupervisedCapacityTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        self.request = {"schema_version": 1, "run_id": "capacity-run", "contract_series_id": "series",
                        "expected_contract_ref": {"kind": "evaluation_contract", "id": "contract", "digest": "a" * 64},
                        "trigger": "manual"}

    def run_mode(self, mode="run"):
        return gah_run.execute(object(), object(), self.folder, self.request, mode)

    def envelope(self):
        return FailureSink.read_existing(self.folder / FAILURE_SINK_NAME, FAILURE_SINK_BYTES, root=self.folder)

    def test_capacity_detection_requires_errno_or_fixed_typed_code(self):
        self.assertIsNone(capacity_reason(ValueError("ENOSPC SQLITE_FULL DATABASE OR DISK IS FULL")))
        self.assertIsNone(capacity_reason(OSError(errno.EIO, "ENOSPC")))
        self.assertEqual(capacity_reason(OSError(errno.ENOSPC, "private")), "CAPACITY_IO_ERROR")
        error = sqlite3.OperationalError("private")
        error.sqlite_errorcode = sqlite3.SQLITE_FULL
        self.assertEqual(capacity_reason(error), "CAPACITY_IO_ERROR")
        self.assertEqual(capacity_reason(CheckpointError("CHECKPOINT_STORAGE_CAPACITY_EXCEEDED")), "CAPACITY_EXCEEDED")

    def test_first_source_lock_failure_is_recorded_without_constructing_supervisor(self):
        checkpoint = Checkpoint(self.folder / "checkpoints")
        checkpoint.put("existing", {"value": "stable"})
        with patch.object(Checkpoint, "put", side_effect=CheckpointError("CHECKPOINT_STORAGE_CAPACITY_IO_ERROR")), \
                patch.object(gah_run, "Supervisor") as supervisor:
            result = self.run_mode()
        supervisor.assert_not_called()
        self.assertEqual((result["exit_code"], result["failure_record"]), (2, "RECORDED"))
        self.assertFalse(result["ci_eligible"])
        self.assertEqual(checkpoint.get("existing"), {"value": "stable"})
        value = self.envelope()
        self.assertEqual(value["failure_stage"], "SOURCE_LOCK")
        self.assertEqual(value["receipt_state"], "UNKNOWN")
        self.assertEqual(value["unsettled_operations"], {"state": "UNKNOWN"})
        self.assertNotIn("expected_contract_ref", value)
        self.assertNotIn("private", json.dumps(value))

    def test_resume_preserves_first_envelope_on_another_capacity_failure(self):
        with patch.object(Checkpoint, "put", side_effect=CheckpointError("CHECKPOINT_STORAGE_CAPACITY_IO_ERROR")):
            self.assertEqual(self.run_mode()["failure_record"], "RECORDED")
        before = (self.folder / FAILURE_SINK_NAME).read_bytes()
        with patch.object(Checkpoint, "put", side_effect=CheckpointError("CHECKPOINT_STORAGE_CAPACITY_EXCEEDED")):
            result = self.run_mode("resume")
        self.assertEqual(result["failure_record"], "PRIOR_RECORD_PRESERVED")
        self.assertEqual(result["reason"], "CAPACITY_EXCEEDED")
        self.assertEqual((self.folder / FAILURE_SINK_NAME).read_bytes(), before)

    def test_sink_failure_does_not_claim_the_error_was_saved(self):
        with patch.object(Checkpoint, "put", side_effect=CheckpointError("CHECKPOINT_STORAGE_CAPACITY_IO_ERROR")), \
                patch.object(FailureSink, "record", side_effect=StorageBudgetError("CAPACITY_IO_ERROR")):
            result = self.run_mode()
        self.assertEqual((result["exit_code"], result["failure_record"]), (2, "UNAVAILABLE"))
        self.assertIsNone(self.envelope())

    def test_generic_io_failure_does_not_create_a_capacity_envelope(self):
        with patch.object(Checkpoint, "put", side_effect=OSError(errno.EIO, "ENOSPC private")):
            with self.assertRaises(OSError):
                self.run_mode()
        self.assertIsNone(self.envelope())

    def test_failed_preallocation_prevents_new_run(self):
        with patch.object(FailureSink, "preallocate", side_effect=StorageBudgetError("CAPACITY_IO_ERROR")), \
                patch.object(gah_run, "Supervisor") as supervisor:
            result = self.run_mode()
        self.assertEqual(result["exit_code"], 2)
        self.assertEqual(result["failure_record"], "UNAVAILABLE")
        supervisor.assert_not_called()

    def test_sink_unavailability_does_not_prevent_cancel_or_status(self):
        for mode in ("cancel", "status"):
            with self.subTest(mode=mode), \
                    patch.object(gah_run, "prepare_sink", side_effect=StorageBudgetError("SINK_READBACK_FAILED")), \
                    patch.object(gah_run, "Supervisor") as supervisor:
                expected = {"exit_code": 2, "ci_eligible": False, "current": mode}
                supervisor.return_value.execute.return_value = expected
                self.assertEqual(self.run_mode(mode), expected)
                supervisor.return_value.execute.assert_called_once_with(mode)

    def test_other_runs_sink_is_preserved_and_not_used(self):
        sink = FailureSink.preallocate(self.folder / FAILURE_SINK_NAME, FAILURE_SINK_BYTES, root=self.folder)
        sink.record({"kind": "supervised_capacity_failure", "run_id": "another-run"})
        before = (self.folder / FAILURE_SINK_NAME).read_bytes()
        with patch.object(gah_run, "Supervisor") as supervisor:
            self.assertEqual(self.run_mode()["exit_code"], 2)
        supervisor.assert_not_called()
        self.assertEqual((self.folder / FAILURE_SINK_NAME).read_bytes(), before)


class SupervisedCapacityIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.test_supervised_run import SupervisedRunTests
        SupervisedRunTests.setUpClass.__func__(cls)

    def setUp(self):
        from tests.test_supervised_run import SupervisedRunTests
        SupervisedRunTests.setUp(self)

    def open(self):
        from tests.test_supervised_run import SupervisedRunTests
        return SupervisedRunTests.open(self)

    def test_completed_runner_receipt_and_unsettled_reservation_survive_checkpoint_failure(self):
        from gah.execution_journal import ExecutionJournal
        original = Checkpoint.put
        failed = []
        def fail_after_runner(checkpoint, key, payload):
            if key.startswith("end-") and not failed:
                failed.append(key)
                raise CheckpointError("CHECKPOINT_STORAGE_CAPACITY_IO_ERROR")
            return original(checkpoint, key, payload)
        before_receipts = tuple(self.store._db.execute("SELECT * FROM authority_run_receipts ORDER BY run_id"))
        with patch.object(Checkpoint, "put", new=fail_after_runner):
            result = gah_run.execute(self.runtime, self.runner, self.folder, self.input, "run", clock=self.clock)
        self.assertEqual(len(failed), 1)
        self.assertEqual((result["exit_code"], result["failure_record"]), (2, "RECORDED"))
        self.assertEqual(tuple(self.store._db.execute("SELECT * FROM authority_run_receipts ORDER BY run_id")), before_receipts)
        operations = tuple(self.store._db.execute(
            "SELECT operation_id,intended_at,stopped_at,settled_at FROM resource_operations WHERE run_id=?",
            (self.input["run_id"],)))
        self.assertEqual(len(operations), 1)
        self.assertIsNotNone(operations[0][1])
        self.assertIsNone(operations[0][2])
        self.assertIsNone(operations[0][3])
        with ExecutionJournal(self.folder / "execution.sqlite") as journal:
            stored = journal.get(self.input["run_id"], operations[0][0])
        self.assertEqual(stored["state"], "FINISHED")
        self.assertIsNotNone(stored["receipt"])
        self.assertEqual(self.store._db.execute("SELECT COUNT(*) FROM attempts WHERE run_id=?", (self.input["run_id"],)).fetchone()[0], 0)
        # Reading the diagnostic after a reopen does not settle or create receipts.
        sink = FailureSink.open_existing(self.folder / FAILURE_SINK_NAME, FAILURE_SINK_BYTES, root=self.folder)
        self.assertEqual(sink.read_record()["unsettled_operations"], {"state": "UNKNOWN"})
        self.assertEqual(tuple(self.store._db.execute("SELECT operation_id,intended_at,stopped_at,settled_at FROM resource_operations WHERE run_id=?", (self.input["run_id"],))), operations)


if __name__ == "__main__":
    unittest.main()
