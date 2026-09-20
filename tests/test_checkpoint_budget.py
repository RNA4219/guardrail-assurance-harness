from pathlib import Path
import tempfile
import unittest
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from gah.storage_budget import StorageBudget
from gah.supervisor_checkpoint import Checkpoint, CheckpointError

class CheckpointBudgetTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name)
    def budget(self,quota):
        return StorageBudget(self.root,quota,rollback_reserve_bytes=0,atomic_temp_overhead_bytes=0)
    def test_replay_does_not_consume_capacity_twice(self):
        budget=self.budget(10000);cp=Checkpoint(self.root,storage_budget=budget)
        cp.put("request-1",{"value":1});used=budget.used_bytes
        self.assertGreater(used,0);cp.put("request-1",{"value":1});self.assertEqual(budget.used_bytes,used)
        with self.assertRaisesRegex(CheckpointError,"CHECKPOINT_CONFLICT"):cp.put("request-1",{"value":2})
    def test_capacity_denial_does_not_publish_checkpoint(self):
        cp=Checkpoint(self.root,storage_budget=self.budget(1))
        with self.assertRaisesRegex(CheckpointError,"BUDGET_EXCEEDED"):cp.put("request-1",{"value":1})
        self.assertIsNone(cp.get("request-1"));self.assertEqual(list(self.root.iterdir()),[])
    def test_reopen_counts_persisted_bytes(self):
        budget=self.budget(10000);cp=Checkpoint(self.root,storage_budget=budget);cp.put("request-1",{"value":1})
        used=budget.used_bytes;budget.close()
        resumed=Checkpoint(self.root,storage_budget=self.budget(used))
        self.assertEqual(resumed.get("request-1"),{"value":1})
        with self.assertRaisesRegex(CheckpointError,"BUDGET_EXCEEDED"):resumed.put("request-2",{"value":2})
        self.assertIsNone(resumed.get("request-2"))
    def test_default_path_uses_bounded_writer_and_preserves_child_directories(self):
        folder=self.root/"run-checkpoints"; child=folder/"benchmark"; child.mkdir(parents=True)
        (child/"journal.db").write_bytes(b"existing-db")
        cp=Checkpoint(folder)
        self.assertIsNone(cp.storage_budget)
        self.assertEqual(cp.put("request-1",{"value":1}),{"value":1})
        self.assertEqual(Checkpoint(folder).get("request-1"),{"value":1})
        self.assertEqual((child/"journal.db").read_bytes(),b"existing-db")
        with self.assertRaisesRegex(CheckpointError,"CHECKPOINT_CONFLICT"):
            cp.put("request-1",{"value":2})

    def test_default_writer_capacity_error_stays_fail_closed(self):
        from unittest.mock import patch
        from gah.bounded_files import BoundedFileError
        cp=Checkpoint(self.root)
        with patch("gah.supervisor_checkpoint.write_bounded", side_effect=BoundedFileError("CAPACITY_EXCEEDED")):
            with self.assertRaisesRegex(CheckpointError,"CHECKPOINT_STORAGE_CAPACITY_EXCEEDED"):
                cp.put("request-1",{"value":1})
        self.assertIsNone(cp.get("request-1"))

    def test_mismatched_root_cannot_use_budget(self):
        with self.assertRaisesRegex(CheckpointError,"CHECKPOINT_BUDGET_MISMATCH"):
            Checkpoint(self.root/"child",storage_budget=self.budget(10000))

    def test_budget_writer_does_not_alias_input_or_return(self):
        cp=Checkpoint(self.root,storage_budget=self.budget(10000))
        value={"nested":{"items":[1,2]}}
        returned=cp.put("isolated",value)
        value["nested"]["items"].append(3)
        returned["nested"]["items"].clear()
        self.assertEqual(cp.get("isolated"),{"nested":{"items":[1,2]}})
        replay=cp.put("isolated",{"nested":{"items":[1,2]}})
        replay["nested"]["items"].append(4)
        self.assertEqual(cp.get("isolated"),{"nested":{"items":[1,2]}})
