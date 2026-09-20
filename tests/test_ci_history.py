"""成功履歴の取得先・環境分離・再配送と直近3件の集約を検査する。"""
import copy
import hashlib
import os
from unittest.mock import patch
from pathlib import Path
import tempfile
import unittest
from tools import ci_history
from gah.wire import canonical_bytes


def context():
    return {"runner_profile": "Linux:X64", "python_version": "3.12.1",
        "image_ref": {"kind": "ci_image", "id": "host", "digest": "a" * 64},
        "measurement_method_version": "unittest-module-v1"}


def history(rows):
    return {"schema_version": 1, "kind": "ci_timing_history", "algorithm_version": "timing-v1",
            **context(), "observations": rows}


def observation(index, *, module="test_alpha", success=True):
    return {"module": module, "elapsed_ns": index * 1_000_000_000,
        "test_count": 3, "observed_at": index, "success": success,
        "source_sha": "a" * 40, "run_id": "run-" + str(index)}


class CiHistoryTests(unittest.TestCase):
    def test_profile_requires_observed_runner_image(self):
        with self.assertRaisesRegex(ValueError, "CI_PROFILE_UNAVAILABLE"):
            ci_history.profile({})
        env = {"ImageOS": "ubuntu24", "ImageVersion": "build-1", "RUNNER_OS": "Linux", "RUNNER_ARCH": "X64"}
        first = ci_history.profile(env)
        self.assertNotEqual(first["image_ref"], ci_history.profile({**env,"ImageVersion":"build-2"})["image_ref"])

    def test_previous_run_is_default_branch_successful_push_with_artifact(self):
        calls = []
        def api(path, token):
            calls.append(path)
            if "/artifacts?" in path:
                return {"artifacts": [{"name":"gah-timing-history", "expired":False}]}
            return {"workflow_runs": [
                {"id":1,"head_branch":"other","event":"push","conclusion":"success"},
                {"id":2,"head_branch":"main","event":"pull_request","conclusion":"success"},
                {"id":3,"head_branch":"main","event":"push","conclusion":"success"}]}
        self.assertEqual(ci_history.previous_run("owner/project","main","private",api=api),3)
        self.assertEqual(len(calls),2)
        self.assertIn("/runs/3/artifacts?",calls[1])
        self.assertNotIn("private", " ".join(calls))

    def test_previous_run_does_not_take_expired_artifact(self):
        def api(path, token):
            if "/artifacts?" in path:
                return {"artifacts":[{"name":"gah-timing-history","expired":True}]}
            return {"workflow_runs":[{"id":3,"head_branch":"main","event":"push","conclusion":"success"}]}
        self.assertIsNone(ci_history.previous_run("owner/project","main","unused",api=api))

    def test_repository_cannot_change_api_host(self):
        for repository in ("https://other/owner/repo", "owner/repo/extra", "owner/repo?x=y"):
            with self.assertRaises(ValueError):
                ci_history.previous_run(repository,"main","unused",api=lambda *_:self.fail("unexpected network"))

    def test_merge_retains_three_distinct_successful_runs(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder); previous=base/"previous.json"; report=base/"report.json"; output=base/"history.json"
            ci_history.save(previous,history([observation(1),observation(2),observation(3)]))
            ci_history.save(report,history([observation(3),observation(4),observation(5,success=False)]))
            counts=ci_history.collect(previous,[report],output)
            self.assertEqual(counts["observation_count"],3)
            self.assertEqual([r["run_id"] for r in ci_history.load(output)["observations"]],["run-2","run-3","run-4"])

    def test_conflicting_replay_does_not_produce_history(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder); previous=base/"previous.json"; report=base/"report.json"; output=base/"history.json"
            ci_history.save(previous,history([observation(1)]))
            changed=observation(1);changed["elapsed_ns"]+=1
            ci_history.save(report,history([changed]))
            with self.assertRaisesRegex(ValueError,"TIMING_OBSERVATION_CONFLICT"):
                ci_history.collect(previous,[report],output)
            self.assertFalse(output.exists())

    def test_fallback_uses_ratio_of_module_medians(self):
        from tools.test_matrix import resolve_timing_history
        rows = []
        for name, seconds, tests in (("test_a", 100, 1), ("test_b", 2, 3), ("test_c", 9, 100)):
            for iteration in (1, 2, 3):
                row = observation(iteration, module=name)
                row.update(elapsed_ns=seconds * 1_000_000_000, test_count=tests)
                rows.append(row)
        value = history(rows)
        result = resolve_timing_history({"test_a":1,"test_b":3,"test_c":100,"test_new":2},
                                        value, **context())
        # median(100,2,9) / median(1,3,100) = 3秒/test。
        # median(100/1,2/3,9/100)の1秒下限ではない。
        self.assertEqual(result["uncertainty"]["per_test_fallback_ns"], 3_000_000_000)
        self.assertEqual(result["module_estimates_ns"]["test_new"], 6_000_000_000)

    def test_changed_profile_discards_old_estimates(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder); previous=base/"previous.json"; output=base/"prepared.json"
            old=history([observation(1)]);old["python_version"]="3.11.0"
            ci_history.save(previous,old)
            result=ci_history.prepare(previous,output,context())
            self.assertEqual(result["history_status"],"PROFILE_CHANGED")
            self.assertEqual(ci_history.load(output)["observations"],[])


class CiHistoryBindingTests(unittest.TestCase):
    def plan(self, base):
        previous = base / "history.json"
        ci_history.save(previous, history([]))
        plan = {"schema_version": 1, "kind": "ci_execution_plan", "source_sha": "a"*40,
            "history_digest": hashlib.sha256(canonical_bytes(history([]))).hexdigest(),
            "plan": {"matrix": {"include": [{"lane":"regression-0","tests":3}]},
                     "total_tests":3,"plan_digest":"b"*64},
            "lane_modules": {"regression-0":["test_alpha"]}}
        target = base / "plan.json"
        ci_history.save(target, plan)
        return previous, target, hashlib.sha256(canonical_bytes(plan)).hexdigest()

    def test_lane_checks_locked_source_history_and_lane_before_execution(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder); previous,plan,lock=self.plan(base)
            for source,lane,key,error in (("b"*40,"regression-0",lock,"CI_PLAN_LOCK_CHANGED"),
                                          ("a"*40,"regression-0","c"*64,"CI_PLAN_LOCK_CHANGED"),
                                          ("a"*40,"regression-9",lock,"UNKNOWN_TEST_LANE")):
                with patch.object(ci_history,"_source_sha",return_value=source), patch("tools.test_matrix.main") as run:
                    with self.assertRaisesRegex(ValueError,error):
                        ci_history.run_lane(previous,plan,lane,base/"out.json",key)
                    run.assert_not_called()
            self.assertFalse((base/"out.profile.json").exists())

    def test_lane_passes_frozen_and_observed_profile_separately_and_runs_once(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder); previous,plan,lock=self.plan(base)
            observed={**context(),"python_version":"3.12.2"}
            with patch.object(ci_history,"_source_sha",return_value="a"*40), patch.object(ci_history,"profile",return_value=observed), patch.dict(os.environ,{"GITHUB_RUN_ID":"100","GITHUB_RUN_ATTEMPT":"2"}), patch("tools.test_matrix.main",return_value=1) as run:
                self.assertEqual(ci_history.run_lane(previous,plan,"regression-0",base/"out.json",lock),1)
            run.assert_called_once()
            args=run.call_args.args[0]
            self.assertEqual(args[args.index("--python-version")+1],"3.12.1")
            self.assertEqual(ci_history.load(args[args.index("--timing-measurement-profile")+1]),observed)
            self.assertEqual(args[args.index("--timing-run-id")+1],"100-2")

    def test_collect_requires_complete_reports_and_current_observations(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder); previous,plan,lock=self.plan(base)
            report=base/"timing-regression-0.json"
            with patch.object(ci_history,"_source_sha",return_value="a"*40):
                with self.assertRaisesRegex(ValueError,"TIMING_REPORTS_INCOMPLETE"):
                    ci_history.collect(previous,[],base/"missing.json",plan_path=plan,expected_lock=lock,expected_run_id="100-2")
                row=observation(1);ci_history.save(report,history([row]))
                with self.assertRaisesRegex(ValueError,"TIMING_REPORT_BINDING_MISMATCH"):
                    ci_history.collect(previous,[report],base/"wrong.json",plan_path=plan,expected_lock=lock,expected_run_id="100-2")
                self.assertFalse((base/"wrong.json").exists())
                self.assertEqual(ci_history.collect(previous,[report],base/"valid.json",plan_path=plan,expected_lock=lock,expected_run_id="run-1")["observation_count"],1)

    def test_malformed_api_response_is_not_an_unhandled_exception(self):
        for response in (None,[],{}, {"workflow_runs":{}}, {"workflow_runs":[None]}):
            try:
                value=ci_history.previous_run("owner/project","main","unused",api=lambda *_:response)
                self.assertIsNone(value)
            except ValueError as error:
                self.assertEqual(str(error),"CI_HISTORY_API_INVALID")

    def test_modified_checkout_cannot_claim_commit_measurements(self):
        with patch("tools.ci_history.subprocess.check_output",return_value="a"*40), patch("tools.ci_history.subprocess.run") as run:
            run.return_value.returncode=1
            with self.assertRaisesRegex(ValueError,"CI_SOURCE_DIRTY"):
                ci_history._source_sha()


if __name__=="__main__":
    unittest.main()
