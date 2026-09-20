from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from gah.contracts import ContractError
from gah.productization import operation_result
from gah.productization_journal import OperationJournal


class ProductizationJournalTests(unittest.TestCase):
    def test_replay_conflict_and_principal_separation(self):
        with tempfile.TemporaryDirectory() as folder:
            with OperationJournal(Path(folder)/"journal.db", clock=lambda: 10) as journal:
                args=("operator", "ops.setup.apply", "request", "a"*64)
                self.assertEqual(journal.begin(*args), {"created": True, "result": None})
                self.assertEqual(journal.begin(*args), {"created": False, "result": None})
                with self.assertRaisesRegex(ContractError,"IDEMPOTENCY_CONFLICT"):
                    journal.begin(*args[:-1], "b"*64)
                self.assertTrue(journal.begin("manager", *args[1:])["created"])

    def test_restart_preserves_unknown_and_finished_result(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"journal.db";args=("operator","ops.setup.apply","request","a"*64)
            with OperationJournal(path,clock=lambda:10) as journal:
                journal.begin(*args)
            result=operation_result("ops.setup.apply","request","COMPLETED",checked_at=11)
            with OperationJournal(path,clock=lambda:11) as journal:
                self.assertFalse(journal.begin(*args)["created"])
                self.assertEqual(journal.finish(*args,result), result)
            with OperationJournal(path,clock=lambda:12) as journal:
                self.assertEqual(journal.begin(*args)["result"],result)
                self.assertEqual(journal.finish(*args,result),result)
                changed=operation_result("ops.setup.apply","request","REJECTED",reasons=["INVALID_INPUT"],checked_at=12)
                with self.assertRaisesRegex(ContractError,"RESULT_CONFLICT"):
                    journal.finish(*args,changed)

    def test_concurrent_begin_only_one_creator(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"journal.db"
            def begin(_):
                with OperationJournal(path,clock=lambda:10) as journal:
                    return journal.begin("operator","ops.setup.apply","request","a"*64)["created"]
            with ThreadPoolExecutor(max_workers=4) as executor:
                outcomes=list(executor.map(begin,range(8)))
            self.assertEqual(sum(outcomes),1)

    def test_corrupt_result_is_not_replayed(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"journal.db";args=("operator","ops.setup.apply","request","a"*64)
            with OperationJournal(path,clock=lambda:10) as journal:
                journal.begin(*args)
                journal.finish(*args,operation_result("ops.setup.apply","request","COMPLETED",checked_at=10))
                journal.db.execute("UPDATE operations SET result=?",(b'{}',))
                with self.assertRaisesRegex(ContractError,"BINDING_MISMATCH"):
                    journal.begin(*args)

    def test_clock_rollback_and_invalid_id_fail_without_intent(self):
        with tempfile.TemporaryDirectory() as folder:
            now=[10]
            with OperationJournal(Path(folder)/"journal.db",clock=lambda:now[0]) as journal:
                journal.begin("operator","ops.setup.apply","request","a"*64)
                now[0]=9
                with self.assertRaisesRegex(ContractError,"CLOCK_ROLLBACK"):
                    journal.begin("operator","ops.setup.apply","other","a"*64)
                now[0]=10
                self.assertTrue(journal.begin("operator","ops.setup.apply","other","a"*64)["created"])
                with self.assertRaises(ContractError):
                    journal.begin("operator","ops.setup.apply",None,"a"*64)

    def test_binding_and_time_are_validated_before_finish(self):
        with tempfile.TemporaryDirectory() as folder:
            with OperationJournal(Path(folder)/"journal.db",clock=lambda:10) as journal:
                args=("operator","ops.setup.apply","request","a"*64)
                journal.begin(*args)
                for command,request,checked in (("pilot.execute","request",10),("ops.setup.apply","other",10),("ops.setup.apply","request",11)):
                    value=operation_result(command,request,"COMPLETED",checked_at=checked)
                    with self.assertRaisesRegex(ContractError,"BINDING_MISMATCH"):
                        journal.finish(*args,value)
                self.assertIsNone(journal.begin(*args)["result"])

    def test_unsupported_database_is_not_initialized(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"other.db"
            with closing(sqlite3.connect(path)) as db:
                db.execute("CREATE TABLE other(x INTEGER)")
            before=path.read_bytes()
            with self.assertRaisesRegex(ContractError,"SCHEMA_UNSUPPORTED"):
                OperationJournal(path)
            self.assertEqual(path.read_bytes(),before)


if __name__ == "__main__":
    unittest.main()
