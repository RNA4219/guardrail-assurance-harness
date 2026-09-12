"""開始登録前の中断を現行製品と実SQLiteで再現する。"""
import importlib.util
from pathlib import Path
import sys
import unittest
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'src'))
spec=importlib.util.spec_from_file_location('prepare_seed',ROOT/'tests/test_supervised_run.py')
seed=importlib.util.module_from_spec(spec);spec.loader.exec_module(seed)

class PrepareResumeBeforeTests(unittest.TestCase):
    open=seed.SupervisedRunTests.open
    run_mode=seed.SupervisedRunTests.run_mode
    count=seed.SupervisedRunTests.count
    @classmethod
    def setUpClass(cls):seed.SupervisedRunTests.setUpClass.__func__(cls)
    def setUp(self):seed.SupervisedRunTests.setUp(self)
    def test_interrupted_prepare_cannot_resume_before_correction(self):
        with self.assertRaises(seed.Interrupted):
            self.run_mode(hook=seed.SupervisedRunTests.interrupt_at('response-prepare'))
        self.assertEqual(self.count(),0)
        with self.assertRaisesRegex(seed.SupervisorError,'RUN_MISSING'):
            self.run_mode('resume')
        self.assertEqual(self.count(),0)
        self.assertFalse(any(req['action']=='run_begin' for _,req in self.runtime.calls))

if __name__=='__main__':unittest.main(verbosity=2)
