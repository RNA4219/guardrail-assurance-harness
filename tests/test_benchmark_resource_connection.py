from pathlib import Path
import tempfile
import unittest
from tools import gah_benchmark
from gah.supervisor_checkpoint import Checkpoint

class BenchmarkResourceConnectionTests(unittest.TestCase):
    def test_saved_resource_record_keeps_missing_children_and_slo_false(self):
        plan={"id":"plan-1","source_ref":{"kind":"snapshot_manifest","id":"source-1","digest":"a"*64}}
        class Session:
            def end(self): return {"metrics":{"host_harness_cpu_ns":123},"valid_for_slo":True}
        with tempfile.TemporaryDirectory() as folder:
            value=gah_benchmark._finish_resource_probe(Session(),Path(folder),plan,{"run_id":"run-1"})
            self.assertFalse(value["valid_for_slo"]);self.assertFalse(value["all_children_observed"])
            self.assertEqual(Checkpoint(folder).get("resource-probe"),value)
            self.assertIn("true_group_peak",value["missing"])
    def test_provider_error_is_a_durable_missing_observation(self):
        plan={"id":"plan-1","source_ref":{"kind":"snapshot_manifest","id":"source-1","digest":"a"*64}}
        class Broken:
            def end(self): raise OSError("private-detail")
        with tempfile.TemporaryDirectory() as folder:
            value=gah_benchmark._finish_resource_probe(Broken(),Path(folder),plan,{"run_id":"run-1"})
            self.assertIsNone(value["result"]);self.assertIn("resource_provider_unavailable",value["missing"])
            self.assertNotIn("private-detail",str(value))

    def test_client_endpoint_error_is_not_a_fabricated_zero(self):
        class Provider:
            def final_client_observations(self): raise OSError("private-detail")
        class Session:
            authority_provider = Provider()
            def end(self): return {"metrics": {"host_harness_cpu_ns": 123}}
        plan={"id":"plan-1","source_ref":{"kind":"snapshot_manifest","id":"source-1","digest":"a"*64}}
        with tempfile.TemporaryDirectory() as directory:
            value = gah_benchmark._finish_resource_probe(Session(),Path(directory),plan,{"run_id":"run-1"})
            self.assertIsNone(value["client_lifecycle_endpoints"])
            self.assertNotIn("private-detail",str(value))

    def test_whole_run_captures_before_cleanup_and_cleans_on_save_error(self):
        from unittest.mock import patch
        from contextlib import nullcontext
        from tests.test_benchmark import BenchmarkTests
        plan, run_request, ci_request = BenchmarkTests().whole_run_inputs()
        for save_error in (False, True):
            with self.subTest(save_error=save_error), tempfile.TemporaryDirectory(dir=gah_benchmark.ROOT) as directory:
                events=[]
                class Runtime:
                    def __init__(self,*args,**kwargs): pass
                    def client(self,*args): events.append("ci"); return {}
                    def close_clients(self): events.append("close")
                class Session:
                    def end(self): events.append("end"); return {"metrics":{}}
                def finish(session,*args):
                    session.end()
                    if save_error: raise OSError("disk failure")
                    return {}
                def measure(*args,**kwargs):
                    return kwargs["operation"]("whole_run",kwargs["request"])
                with patch("tools.authority_runtime.AuthorityRuntime",Runtime), \
                     patch.object(gah_benchmark,"_whole_run_status",return_value=False), \
                     patch.object(gah_benchmark,"_whole_run_checkpoint_exists",return_value=False), \
                     patch.object(gah_benchmark,"_whole_run_runner",return_value=object()), \
                     patch.object(gah_benchmark,"operation_lock",return_value=nullcontext()), \
                     patch.object(gah_benchmark,"_begin_resource_probe",return_value=Session()), \
                     patch.object(gah_benchmark,"_finish_resource_probe",side_effect=finish), \
                     patch.object(gah_benchmark,"measure_once",side_effect=measure), \
                     patch("tools.gah_ci.response_exit_code",return_value=0), \
                     patch("tools.gah_run.execute",return_value={"exit_code":0,"gate":{"expected_manifest_ref":ci_request["expected_manifest_ref"]}}):
                    kwargs=dict(run_request=run_request,ci_request=ci_request,runner_kind="fixture",runtime_folder=Path(directory),iteration=1,warmness="cold")
                    if save_error:
                        with self.assertRaises(OSError): gah_benchmark._measure_whole_run_with_runtime(plan,"iter-1",**kwargs)
                    else:
                        value,error=gah_benchmark._measure_whole_run_with_runtime(plan,"iter-1",**kwargs)
                        self.assertEqual(value["exit_code"],0);self.assertIsNone(error)
                self.assertEqual(events,["ci","end","close"])
