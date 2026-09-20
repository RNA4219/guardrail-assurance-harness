"""中断境界の記録を検査する。製品・OS認証の受入ではない。"""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from gah import supervisor_checkpoint as module


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder=Path(tmp.name)/'run'
        self.book=module.Checkpoint(self.folder)

    def test_restart_and_immutable_replay(self):
        value={'request_id':'send-1','nested':{'epoch':1}}
        saved=self.book.put('intent',value)
        value['nested']['epoch']=2
        saved['nested']['epoch']=3
        self.assertEqual(module.Checkpoint(self.folder).get('intent')['nested']['epoch'],1)
        self.assertEqual(self.book.put('intent',{'request_id':'send-1','nested':{'epoch':1}})['nested']['epoch'],1)
        with self.assertRaisesRegex(module.CheckpointError,'CHECKPOINT_CONFLICT'):
            self.book.put('intent',value)

    def test_input_return_get_and_replay_mutations_are_independent(self):
        original = {"request_id": "send-alias", "nested": {"values": [1, 2]}}
        returned = self.book.put("alias", original)
        original["nested"]["values"].append(3)
        returned["nested"]["values"].append(4)
        self.assertEqual(self.book.get("alias"),
                         {"request_id": "send-alias", "nested": {"values": [1, 2]}})

        first_read = self.book.get("alias")
        first_read["nested"]["values"].clear()
        self.assertEqual(self.book.get("alias")["nested"]["values"], [1, 2])

        replay = self.book.put(
            "alias", {"request_id": "send-alias", "nested": {"values": [1, 2]}})
        replay["nested"]["values"].append(9)
        self.assertEqual(self.book.get("alias")["nested"]["values"], [1, 2])

    def test_fresh_decode_paths_do_not_deepcopy_trees(self):
        with patch.object(module, "deepcopy", create=True,
                          side_effect=AssertionError("unexpected tree deepcopy")) as copied:
            saved = self.book.put("cost", {"nested": {"values": [1, 2, 3]}})
            self.assertEqual(saved["nested"]["values"], [1, 2, 3])
            loaded = self.book.get("cost")
            self.assertEqual(loaded["nested"]["values"], [1, 2, 3])
            copied.assert_not_called()

    def test_failure_before_publish_has_no_partial_record(self):
        with patch.object(module.os,'replace',side_effect=OSError('synthetic')):
            with self.assertRaises(OSError):self.book.put('intent',{'ready':True})
        self.assertIsNone(self.book.get('intent'))
        entries=list(self.folder.iterdir())
        self.assertEqual(len(entries),1)
        self.assertTrue(entries[0].name.startswith('.gah-bounded-write.') and entries[0].name.endswith('.lock'))
        self.assertEqual(self.book.put('intent',{'ready':True}),{'ready':True})

    def test_failure_during_flush_cannot_publish(self):
        with patch.object(module.os,'fsync',side_effect=OSError('synthetic')):
            with self.assertRaises(OSError):self.book.put('intent',{'ready':True})
        self.assertIsNone(self.book.get('intent'))
        entries=list(self.folder.iterdir())
        self.assertEqual(len(entries),1)
        self.assertTrue(entries[0].name.startswith('.gah-bounded-write.') and entries[0].name.endswith('.lock'))

    def test_corruption_and_wrong_key_are_rejected(self):
        self.book.put('intent',{'ready':True})
        p=self.book._path('intent')
        raw=p.read_bytes()
        for body in (b'{',raw.replace(b'true',b'false'),raw.replace(b'intent',b'other1'),raw+b' '):
            p.write_bytes(body)
            with self.assertRaisesRegex(module.CheckpointError,'CHECKPOINT_CORRUPT'):
                self.book.get('intent')
        p.write_bytes(raw)
        self.assertEqual(self.book.get('intent'),{'ready':True})

    def test_unsafe_key_and_oversized_value_rejected(self):
        for key in ('../outside','a/b',''):
            with self.assertRaises(ValueError):self.book.put(key,{})
        with self.assertRaises(ValueError):self.book.put('large',{'data':'x'*module.MAX_DOCUMENT_BYTES})
        self.assertEqual(list(self.folder.iterdir()),[])

    def test_paths_with_reparse_attribute_rejected(self):
        actual=self.folder.lstat()
        class FakeStat:
            st_mode=actual.st_mode
            st_file_attributes=0x400
        with patch.object(Path,'lstat',return_value=FakeStat()):
            with self.assertRaisesRegex(module.CheckpointError,'CHECKPOINT_PATH_INVALID'):
                module.Checkpoint(self.folder)


if __name__=='__main__':unittest.main(verbosity=2)
