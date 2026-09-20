"""固定sample導入の採択と通常run接続。Dockerを起動しない実SQLite試験。"""
from copy import deepcopy
from pathlib import Path
import unittest
from tests import test_baseline_adoption_integration as baseline
from tests.test_supervised_run import Runtime, SyntheticRunner, TransportLost
from gah.operations import _sample_refs
from gah.supervised_run import SupervisorError
from tools.setup_apply import execute_contract_setup, SetupError
from tools.gah_run import execute
from tools.gah_ci import response_exit_code

class SetupApplyCoreTests(unittest.TestCase):
    def setUp(self):
        self.fixture = baseline.BaselineAdoptionIntegrationTests("runTest"); self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.store = self.fixture.open(); self.addCleanup(self.store.close)
        self.runtime = Runtime(self.store)
        self.folder = self.fixture.path.parent / "setup"; self.folder.mkdir()
        refs = _sample_refs("sample-ci", "setup-sample", self.fixture.clock())
        self.payload = {"setup_id": "setup-sample", "profile": "sample-ci",
                       **dict(zip(("sample_contract_ref", "sample_case_set_ref", "evaluator_ref"), refs))}
        self.runners = {}

    def factory(self, profile, folder):
        if folder not in self.runners: self.runners[folder] = SyntheticRunner(folder, self.fixture.worker)
        return self.runners[folder]

    def setup_sample(self):
        return execute_contract_setup(self.runtime, self.folder, self.payload, clock=self.fixture.clock, runner_factory=self.factory)

    def test_initial_and_both_candidate_sides_adopt_gen2_then_normal_run_works(self):
        result = self.setup_sample()
        self.assertEqual(sum(len(r.executed) for r in self.runners.values()), 60)
        self.assertEqual(len(result["candidate_receipts"]), 2)
        self.assertFalse(result["initial_receipt"]["ci_eligible"])
        before = self.store._db.execute("SELECT COUNT(*) FROM eval_adoptions").fetchone()[0]
        self.assertEqual(before, 2)
        self.assertEqual(self.setup_sample(), result)
        self.assertEqual(sum(len(r.executed) for r in self.runners.values()), 60)
        # setupと通常CLIの秒が異なっても、保存prepareのmanifest refを維持する。
        self.fixture.clock.value += 2
        normal = self.folder / "normal"; normal.mkdir()
        final = execute(self.runtime, self.factory("sample-ci", normal), normal, result["run_request"], "run", clock=self.fixture.clock)
        self.assertEqual(final["exit_code"], 0, final)
        gate = self.runtime.client(12004, result["ci_request"])
        self.assertEqual(response_exit_code(result["ci_request"], gate), 0)
        self.assertEqual(self.store._db.execute("SELECT COUNT(*) FROM eval_adoptions").fetchone()[0], before)

    def test_candidate_adoption_ack_loss_recovers_without_new_executions(self):
        self.runtime.drop_action = "contract_candidate_adopt"
        with self.assertRaises(TransportLost): self.setup_sample()
        count = sum(len(r.executed) for r in self.runners.values())
        self.assertEqual(count, 60)
        result = self.setup_sample()
        self.assertEqual(sum(len(r.executed) for r in self.runners.values()), count)
        self.assertEqual(self.store._db.execute("SELECT COUNT(*) FROM eval_adoptions").fetchone()[0], 2)
        self.assertEqual(result["contract_ref"]["kind"], "evaluation_contract")

    def test_plan_sample_ref_changed_before_contract_adoption_is_rejected(self):
        self.payload["sample_contract_ref"]["digest"] = "0" * 64
        with self.assertRaisesRegex(SetupError, "BINDING_MISMATCH"): self.setup_sample()
        self.assertEqual(self.store._db.execute("SELECT COUNT(*) FROM eval_adoptions").fetchone()[0], 0)
        self.assertEqual(self.runners, {})

    def test_candidate_read_wrong_side_cannot_dispatch(self):
        original = self.runtime.client
        def client(uid, request):
            value = original(uid, request)
            if request["action"] == "contract_candidate_read": value["side"] = "wrong"
            return value
        self.runtime.client = client
        with self.assertRaisesRegex(SetupError, "BINDING_MISMATCH"): self.setup_sample()
        self.assertEqual(sum(len(r.executed) for r in self.runners.values()), 15)
        self.assertEqual(self.store._db.execute("SELECT COUNT(*) FROM eval_adoptions").fetchone()[0], 1)


class SetupApplyLlmTests(unittest.TestCase):
    def test_fixed_llm_initial_and_both_candidate_sides_are_fully_evaluated(self):
        from tests.test_llm_supervised_run import SyntheticGuardrailRunner
        helper = SetupApplyCoreTests("runTest"); helper.setUp()
        self.addCleanup(helper.doCleanups)
        refs = _sample_refs("sample-llm", "setup-llm-sample", helper.fixture.clock())
        helper.payload = {"setup_id": "setup-llm-sample", "profile": "sample-llm",
                          **dict(zip(("sample_contract_ref", "sample_case_set_ref", "evaluator_ref"), refs))}
        runners = {}
        def factory(profile, folder):
            self.assertEqual(profile, "sample-llm")
            if folder not in runners: runners[folder] = SyntheticGuardrailRunner(folder)
            return runners[folder]
        result = execute_contract_setup(helper.runtime, helper.folder, helper.payload,
                                        clock=helper.fixture.clock, runner_factory=factory)
        self.assertEqual(sorted(len(r.executed) for r in runners.values()), [400, 400, 800])
        self.assertEqual(helper.store._db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0], 2400)
        self.assertEqual(helper.store._db.execute("SELECT COUNT(*) FROM eval_adoptions").fetchone()[0], 2)
        self.assertEqual(result["ci_request"]["expected_use_cases"], ["UC-LLM"])
        self.assertEqual(result["ci_request"]["expected_contract_ref"], result["contract_ref"])
        self.assertFalse(result["initial_receipt"]["ci_eligible"])
        self.assertTrue(all(r["input_materialization_verified"] and r["resource_closure_verified"]
                            for r in result["candidate_receipts"].values()))
