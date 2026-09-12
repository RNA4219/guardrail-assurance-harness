"""開始登録前と登録ACK喪失から、同じ予定runを一度だけ実行する。"""
import importlib.util
from pathlib import Path
import sys
import unittest
ROOT=Path(__file__).resolve().parents[1] if Path(__file__).parent.name=='tests' else Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'src'))
spec=importlib.util.spec_from_file_location('startup_seed',ROOT/'tests/test_supervised_run.py')
seed=importlib.util.module_from_spec(spec);spec.loader.exec_module(seed)

class SupervisedStartupTests(unittest.TestCase):
    open=seed.SupervisedRunTests.open
    run_mode=seed.SupervisedRunTests.run_mode
    count=seed.SupervisedRunTests.count
    @classmethod
    def setUpClass(cls):seed.SupervisedRunTests.setUpClass.__func__(cls)
    def setUp(self):seed.SupervisedRunTests.setUp(self)
    def assert_resume(self,stage):
        with self.assertRaises(seed.Interrupted):
            self.run_mode(hook=seed.SupervisedRunTests.interrupt_at(stage))
        self.assertEqual(self.count(),0)
        result=self.run_mode('resume')
        self.assertEqual(result['exit_code'],0,result)
        self.assertEqual(self.count(),30)
        dispatches=[req['operation_id'] for _,req in self.runtime.calls if req['action']=='resource_dispatch']
        self.assertEqual(len(dispatches),30);self.assertEqual(len(set(dispatches)),30)
        prior=result['gate']['outputs_ref']
        self.assertEqual(self.run_mode('resume')['gate']['outputs_ref'],prior)
        self.assertEqual(self.count(),30)
    def test_before_prepare_send(self):self.assert_resume('request-prepare')
    def test_after_prepare_ack(self):self.assert_resume('response-prepare')
    def test_before_begin_send(self):self.assert_resume('request-begin')
    def test_begin_ack_lost(self):
        self.runtime.drop_action='run_begin'
        with self.assertRaises(seed.TransportLost):self.run_mode()
        self.assertEqual(self.count(),0)
        self.assertEqual(self.run_mode('resume')['exit_code'],0)
        self.assertEqual(self.count(),30)
        rows=self.store._db.execute("SELECT count(*) FROM resource_runs WHERE run_id=?",(self.input['run_id'],)).fetchone()
        self.assertEqual(rows[0],1)

if __name__=='__main__':unittest.main(verbosity=2)
