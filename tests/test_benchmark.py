"""benchmark API の局所境界試験。性能SLOの実測証拠ではない。"""
from fractions import Fraction
import hashlib
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import time
import unittest

from gah import benchmark
from gah.contracts import ContractError
from gah.productization import content_ref
from gah.wire import canonical_bytes


class BenchmarkTests(unittest.TestCase):
    def ref(self, kind, identifier):
        return content_ref(kind, identifier, {
            "schema_version": 1, "kind": kind, "id": identifier,
        })

    def manifest(self, surface="query"):
        empty_digest = hashlib.sha256(canonical_bytes([])).hexdigest()
        source_base = self.ref("snapshot_manifest", "source-base")
        source_candidate = self.ref("snapshot_manifest", "source-candidate")
        work_refs = {
            axis: self.ref("work_set", "set-" + axis)
            for axis in ("cases", "variants", "trials", "stages")
        }
        work_amount = {
            axis: {
                "planned_count": 2, "observed_count": 2,
                "coefficient": {"numerator": 1, "denominator": 1},
                "id_set_ref": work_refs[axis],
            }
            for axis in work_refs
        }
        return {
            "measurement_surface": surface,
            "baseline_source_ref": source_base,
            "candidate_source_ref": source_candidate,
            "input_ref": self.ref("input", "input-1"),
            "expected_ref": self.ref("expected", "expected-1"),
            "resource_profile_ref": self.ref("resource_profile", "resource-1"),
            "contract_ref": self.ref("evaluation_contract", "contract-1"),
            "baseline_ref": self.ref("baseline", "baseline-1"),
            "target_refs": [self.ref("target", "target-1")],
            "os": "linux", "kernel": "6.8", "cpu_model": "test-cpu",
            "python_version": "3.12", "docker_version": "26",
            "vcpus_count": 4, "parallelism_count": 2, "concurrency_count": 1,
            "ram_bytes": 8 * 1024 * 1024 * 1024,
            "storage_free_bytes": 1024 * 1024 * 1024,
            "image_ref": self.ref("container_image", "image-1"),
            "storage_profile_ref": self.ref("storage_profile", "storage-1"),
            "clock_monotonic_ok": True, "clock_wall_ok": True,
            "case_count": 2, "variant_count": 2,
            "planned_trial_count": 2, "planned_stage_count": 2,
            "planned_test_count": 2, "history_run_count": 0,
            "page_size_count": 100, "work_amount": work_amount,
            "work_set_refs": work_refs,
            "cold_iterations_count": 1, "warm_iterations_count": 2,
            "warmup_iterations_count": 0,
            "broker_rss_limit_bytes": 128 * 1024 * 1024,
            "cache_hard_limit_bytes": 16 * 1024 * 1024,
            "cache_entry_cap_count": 32, "cache_entry_max_bytes": 1024 * 1024,
            "source_sha": "a" * 40, "plan_digest": "b" * 64,
            "lane_set": ["lane-0"],
            "intervals": {"start_event": "query_input_decoded",
                          "end_event": "response_ref_digest_verified"},
            "observation_count": 0, "observation_segments": [],
            "observation_digest": empty_digest,
        }

    def plan(self):
        return benchmark.make_plan(
            self.manifest(), identifier="benchmark-plan-1",
            source_ref=self.ref("snapshot_manifest", "plan-source"),
            requirements_ref=self.ref("snapshot_manifest", "plan-requirements"),
            created_at=100, expires_at=200,
        )

    def observation(self, iteration, warmness, wall=100_000_000, *, valid=True,
                    missing=False):
        value = {
            "iteration": iteration, "warmness": warmness, "wall_ns": wall,
            "cpu_ns": 50_000_000, "target_wait_ns": None,
            "harness_wall_ns": None, "rss_group_peak_bytes": 1024,
            "io_read_bytes": None, "io_write_bytes": None, "copy_count": None,
            "serialize_bytes": None, "hash_count": None, "db_scan_count": None,
            "authority_call_count": None, "stored_bytes": None,
            "retry_count": 0, "failure_class": "NONE",
            "operation_status": "COMPLETED", "exit_code": 0,
            "valid_for_slo": valid,
        }
        if missing:
            value.update(wall_ns=None, cpu_ns=None, rss_group_peak_bytes=None,
                         failure_class="UNSUPPORTED_ENVIRONMENT",
                         operation_status="INCOMPLETE", exit_code=2,
                         valid_for_slo=False)
        return value

    def records(self, plan, source_ref, prefix, wall=100_000_000):
        return [
            benchmark.make_observation_artifact(
                plan, source_ref, prefix + "-cold", self.observation(1, "cold", wall)),
            benchmark.make_observation_artifact(
                plan, source_ref, prefix + "-warm-1", self.observation(1, "warm", wall)),
            benchmark.make_observation_artifact(
                plan, source_ref, prefix + "-warm-2", self.observation(2, "warm", wall)),
        ]

    def test_manifest_rejects_unknown_and_bool(self):
        value = self.manifest()
        value["unknown"] = 1
        with self.assertRaises(ContractError):
            benchmark.validate_manifest(value)
        value = self.manifest()
        value["planned_test_count"] = True
        with self.assertRaises(ContractError):
            benchmark.validate_manifest(value)

    def test_manifest_requires_segments_for_observation_count(self):
        value = self.manifest()
        value["observation_count"] = 1
        with self.assertRaises(ContractError):
            benchmark.validate_manifest(value)
        value = self.manifest()
        value["observation_segments"] = [self.ref("wrong", "segment-1")]
        with self.assertRaises(ContractError):
            benchmark.validate_manifest(value)

    def test_metrics_fraction_p95_and_ratio_types(self):
        self.assertEqual(benchmark.metric_summary([1, 2])["median"], Fraction(3, 2))
        self.assertEqual(benchmark.metric_summary(range(1, 101))["p95"], 95)
        self.assertEqual(benchmark.metric_summary([9, 1, 5])["max"], 9)
        self.assertEqual(benchmark.ratio(3, 2), Fraction(3, 2))
        self.assertIsNone(benchmark.ratio(3, 0))
        for candidate, baseline in ((True, 2), (1, True), (0.1, 2), ("1", 2), (-1, 2)):
            with self.subTest(candidate=candidate, baseline=baseline):
                with self.assertRaises(ContractError):
                    benchmark.ratio(candidate, baseline)

    def test_failed_observation_keeps_null_measurements(self):
        checked = benchmark.validate_observation(self.observation(1, "warm", missing=True))
        self.assertIsNone(checked["wall_ns"])
        self.assertIsNone(checked["cpu_ns"])
        self.assertIsNone(checked["rss_group_peak_bytes"])
        forged = dict(checked)
        forged.update(wall_ns=1, failure_class="NONE",
                      operation_status="COMPLETED", exit_code=0,
                      valid_for_slo=True)
        with self.assertRaises(ContractError):
            benchmark.validate_observation(forged)

    def test_compare_failure_is_retained_without_zero_fill(self):
        plan = self.plan()
        baseline_ref = plan["payload"]["baseline_source_ref"]
        candidate_ref = plan["payload"]["candidate_source_ref"]
        baseline = self.records(plan, baseline_ref, "b")
        candidate = self.records(plan, candidate_ref, "c")
        candidate[2]["payload"] = self.observation(2, "warm", missing=True)
        result = benchmark.compare_observations(plan, baseline, candidate)
        self.assertEqual(result["status"], "INCONCLUSIVE")
        self.assertEqual(len(result["failures"]), 1)
        self.assertIsNotNone(result["baseline_metrics"])
        self.assertEqual(result["candidate_metrics"]["count"], 2)

    def test_point_query_cannot_claim_series_slo_with_trusted_evidence_flag(self):
        plan = self.plan()
        baseline_ref = plan["payload"]["baseline_source_ref"]
        candidate_ref = plan["payload"]["candidate_source_ref"]
        baseline = self.records(plan, baseline_ref, "b", wall=500_000_000)
        candidate = self.records(plan, candidate_ref, "c", wall=100_000_000)
        self.assertEqual(benchmark.compare_observations(plan, baseline, candidate)["status"], "INCONCLUSIVE")
        accepted = benchmark.compare_observations(plan, baseline, candidate, evidence_confirmed=True)
        self.assertEqual(accepted["status"], "INCONCLUSIVE")
        self.assertEqual(accepted["reasons"], ["EVIDENCE_UNAVAILABLE"])
        self.assertFalse(accepted["slo_evidence"])
        self.assertEqual(accepted["ratios"]["candidate_over_baseline"],
                         {"numerator": 1, "denominator": 5})
        forged = dict(accepted, status="PASS", reasons=[], slo_evidence=True)
        with self.assertRaises(ContractError):
            benchmark.validate_result(forged)

    def test_non_query_comparison_still_requires_and_uses_trusted_evidence(self):
        manifest = self.manifest("quickstart_total")
        start, end = benchmark.INTERVAL_EVENTS["quickstart_total"]
        manifest["intervals"] = {"start_event": start, "end_event": end}
        plan = benchmark.make_plan(
            manifest, identifier="benchmark-quickstart-plan",
            source_ref=self.ref("snapshot_manifest", "plan-source"),
            requirements_ref=self.ref("snapshot_manifest", "plan-requirements"),
            created_at=100, expires_at=200,
        )
        baseline = self.records(plan, manifest["baseline_source_ref"], "b")
        candidate = self.records(plan, manifest["candidate_source_ref"], "c")
        self.assertEqual(benchmark.compare_observations(plan, baseline, candidate)["status"],
                         "INCONCLUSIVE")
        accepted = benchmark.compare_observations(plan, baseline, candidate, evidence_confirmed=True)
        self.assertEqual(accepted["status"], "PASS")

    def test_compare_rejects_truthy_non_boolean_evidence(self):
        plan = self.plan()
        baseline = self.records(plan, plan["payload"]["baseline_source_ref"], "b")
        candidate = self.records(plan, plan["payload"]["candidate_source_ref"], "c")
        for flag in (1, "confirmed", [], {}):
            with self.subTest(flag=flag), self.assertRaises(ContractError):
                benchmark.compare_observations(plan, baseline, candidate, evidence_confirmed=flag)

    def test_compare_rejects_duplicate_warmness_iteration_with_new_labels(self):
        plan = self.plan()
        baseline_ref = plan["payload"]["baseline_source_ref"]
        candidate_ref = plan["payload"]["candidate_source_ref"]
        baseline = self.records(plan, baseline_ref, "b")
        baseline.append(
            benchmark.make_observation_artifact(
                plan, baseline_ref, "b-warm-duplicate",
                self.observation(2, "warm"),
            )
        )
        with self.assertRaises(ContractError) as raised:
            benchmark.compare_observations(
                plan, baseline, self.records(plan, candidate_ref, "c"),
            )
        self.assertEqual(raised.exception.code, "OBSERVATION_MISSING")

    def test_compare_requires_contiguous_indices_per_warmness(self):
        plan = self.plan()
        baseline_ref = plan["payload"]["baseline_source_ref"]
        candidate_ref = plan["payload"]["candidate_source_ref"]
        baseline = [
            benchmark.make_observation_artifact(
                plan, baseline_ref, "b-cold-1", self.observation(1, "cold"),
            ),
            benchmark.make_observation_artifact(
                plan, baseline_ref, "b-warm-1", self.observation(1, "warm"),
            ),
            benchmark.make_observation_artifact(
                plan, baseline_ref, "b-warm-3", self.observation(3, "warm"),
            ),
        ]
        result = benchmark.compare_observations(
            plan, baseline, self.records(plan, candidate_ref, "c"),
            evidence_confirmed=True,
        )
        self.assertEqual(result["status"], "INCONCLUSIVE")
        self.assertEqual(result["reasons"], ["OBSERVATION_MISSING"])

    def test_measure_once_rejects_unplanned_index_or_type_before_operation(self):
        plan = self.plan()
        calls = []

        def operation(action, request):
            calls.append((action, request))
            return {"exit_code": 0}

        for invalid_iteration in (2, True, "1"):
            with self.subTest(iteration=invalid_iteration):
                with self.assertRaises(ContractError):
                    benchmark.measure_once(
                        plan, "sample-label", recipe="candidate",
                        operation=operation, request={},
                        iteration=invalid_iteration, warmness="cold",
                    )
        self.assertEqual(calls, [])

    def test_measure_recipe_uses_fixed_action(self):
        calls = []
        wall = iter((10, 110)); cpu = iter((20, 70))
        def operation(action, request):
            calls.append((action, request["iteration_id"]))
            return {"exit_code": 0}
        measured = benchmark.measure_recipe(
            "current_ci", operation, {"iteration_id": "iteration-1"},
            monotonic_clock=lambda: next(wall), cpu_clock=lambda: next(cpu),
            rss_sampler=lambda: 2048,
        )
        self.assertEqual(calls, [("ci_check", "iteration-1")])
        self.assertEqual(measured["wall_ns"], 100)
        self.assertEqual(measured["cpu_ns"], 50)
        self.assertTrue(measured["valid_for_slo"])

    def test_measure_without_group_probe_is_incomplete(self):
        wall = iter((10, 110))
        measured = benchmark.measure_recipe(
            "candidate", lambda action, request: {"exit_code": 0},
            {"iteration_id": "iteration-1"}, monotonic_clock=lambda: next(wall),
        )
        self.assertEqual(measured["failure_class"], "MEASUREMENT_ERROR")
        self.assertEqual(measured["operation_status"], "INCOMPLETE")
        self.assertFalse(measured["valid_for_slo"])
        self.assertIsNone(measured["cpu_ns"])
        self.assertIsNone(measured["rss_group_peak_bytes"])

    def test_measure_recipe_rejects_missing_bool_and_unknown_exit_responses(self):
        for returned in (None, {"exit_code": True}, {"exit_code": 4},
                         {"exit_code": "0"}):
            with self.subTest(returned=returned):
                wall = iter((10, 110))
                cpu = iter((20, 70))
                measured = benchmark.measure_recipe(
                    "current_ci",
                    lambda action, request, returned=returned: returned,
                    {"iteration_id": "iteration-1"},
                    monotonic_clock=lambda: next(wall),
                    cpu_clock=lambda: next(cpu),
                    rss_sampler=lambda: 2048,
                )
                self.assertEqual(measured["failure_class"], "TARGET_ERROR")
                self.assertEqual(measured["operation_status"], "INCOMPLETE")
                self.assertFalse(measured["valid_for_slo"])

    def test_cli_plan_measure_and_compare_wires_statuses(self):
        from tools import gah_benchmark

        plan = self.plan()
        baseline_ref = plan["payload"]["baseline_source_ref"]
        candidate_ref = plan["payload"]["candidate_source_ref"]
        baseline = self.records(plan, baseline_ref, "b")
        candidate = self.records(plan, candidate_ref, "c")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "candidate.json").write_text(
                json.dumps(plan, ensure_ascii=False), encoding="utf-8"
            )
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(gah_benchmark.main([
                    "plan", "--workspace", str(root), "--input", "candidate.json",
                    "--output", "plan.json",
                ]), 0)
            self.assertEqual(json.loads(output.getvalue())["command"], "benchmark.plan")
            (root / "observations.json").write_text(
                json.dumps({"baseline": baseline, "candidate": candidate}),
                encoding="utf-8",
            )
            output = io.StringIO()
            with redirect_stdout(output):
                code = gah_benchmark.main([
                    "compare", "--workspace", str(root), "--plan", "plan.json",
                    "--observations", "observations.json", "--output", "result.json",
                ])
            self.assertEqual(code, 2)
            result = json.loads(output.getvalue())
            self.assertEqual(result["command"], "benchmark.compare")
            self.assertEqual(result["operation_status"], "INCOMPLETE")
            self.assertEqual(result["reasons"], ["EVIDENCE_UNAVAILABLE"])
            self.assertTrue((root / "result.json").is_file())
            output = io.StringIO()
            with redirect_stdout(output):
                code = gah_benchmark.main([
                    "measure", "--workspace", str(root), "--plan", "plan.json",
                    "--iteration-id", "iteration-1", "--recipe", "candidate",
                    "--output", "observation.json",
                ])
            self.assertEqual(code, 1)
            measured_request = json.loads(output.getvalue())
            self.assertEqual(measured_request["command"], "benchmark.measure")
            self.assertEqual(measured_request["operation_status"], "REJECTED")
            self.assertEqual(measured_request["reasons"], ["INVALID_INPUT"])


    def test_measure_digest_ignores_variable_container_state(self):
        from tools import gah_benchmark

        plan = self.plan()
        with tempfile.TemporaryDirectory(dir=str(gah_benchmark.ROOT)) as folder:
            root = Path(folder)
            runtime = root / "runtime"
            runtime.mkdir()
            deployment = {
                "prefix": "gah-authority-" + "a" * 32,
                "image_id": "sha256:" + "b" * 64,
                "containers": [],
            }
            (runtime / "deployment.json").write_text(
                json.dumps(deployment), encoding="utf-8",
            )
            first = gah_benchmark._measure_input_digest(
                plan, {"request_id": "request-1"}, runtime,
                recipe="current_ci", series="candidate",
                iteration_id="iteration-1", iteration=1,
                warmness="cold", output="observation.json",
            )
            deployment["containers"] = [
                deployment["prefix"] + "-client-" + "c" * 32,
            ]
            (runtime / "deployment.json").write_text(
                json.dumps(deployment), encoding="utf-8",
            )
            second = gah_benchmark._measure_input_digest(
                plan, {"request_id": "request-1"}, runtime,
                recipe="current_ci", series="candidate",
                iteration_id="iteration-1", iteration=1,
                warmness="cold", output="observation.json",
            )
            self.assertEqual(first, second)

    def test_cli_measure_recovers_after_finish_response_loss_without_remeasure(self):
        from unittest.mock import patch
        from tools import gah_benchmark

        plan = self.plan()
        plan["created_at"] = int(time.time()) - 1
        plan["expires_at"] = int(time.time()) + 3600
        request = {
            "schema_version": 1, "action": "ci_check",
            "request_id": "ci-query-recovery", "run_id": "run-recovery",
            "expected_manifest_ref": self.ref("run_manifest", "run-recovery"),
            "expected_contract_ref": plan["payload"]["contract_ref"],
            "expected_baseline_ref": plan["payload"]["baseline_ref"],
            "expected_target_refs": plan["payload"]["target_refs"],
            "expected_use_cases": ["UC-CI"],
        }

        class FakeRuntime:
            instances = []

            def __init__(self, folder, **kwargs):
                self.calls = []
                self.closed = False
                self.__class__.instances.append(self)

            def client(self, uid, value):
                self.calls.append((uid, value))
                return {
                    "schema_version": 1, "kind": "ci_gate_result",
                    "action": "ci_check", "request_id": value["request_id"],
                    "run_id": value["run_id"], "checked_at": 1,
                    "expected_manifest_ref": value["expected_manifest_ref"],
                    "outputs_ref": self_ref("run_outputs", value["run_id"]),
                    "assurance": "HEALTHY", "reasons": [], "use": True,
                    "ci_eligible": True, "execution_status": "COMPLETED",
                    "exit_code": 0,
                }

            def close_clients(self):
                self.closed = True

        def self_ref(kind, identifier):
            return self.ref(kind, identifier)

        with tempfile.TemporaryDirectory(dir=str(gah_benchmark.ROOT)) as folder:
            root = Path(folder)
            runtime = root / "runtime"
            runtime.mkdir()
            (runtime / "deployment.json").write_text(json.dumps({
                "prefix": "gah-authority-" + "d" * 32,
                "image_id": "sha256:" + "e" * 64,
                "containers": [],
            }), encoding="utf-8")
            (root / "plan.json").write_text(
                json.dumps(plan, ensure_ascii=False), encoding="utf-8",
            )
            (root / "request.json").write_text(
                json.dumps(request, ensure_ascii=False), encoding="utf-8",
            )
            original_finish = gah_benchmark.OperationJournal.finish
            finish_calls = []

            def lose_first_finish(journal, *values):
                finish_calls.append(1)
                if len(finish_calls) == 1:
                    raise RuntimeError("response lost")
                return original_finish(journal, *values)

            args = [
                "measure", "--workspace", str(root), "--plan", "plan.json",
                "--iteration-id", "iteration-1", "--recipe", "current_ci",
                "--runtime", str(runtime), "--request", "request.json",
                "--output", "observation.json",
            ]
            first_output = io.StringIO()
            with patch("tools.authority_runtime.AuthorityRuntime", FakeRuntime),                     patch.object(gah_benchmark.OperationJournal, "finish", lose_first_finish),                     redirect_stdout(first_output):
                first_code = gah_benchmark.main(args)
            self.assertEqual(first_code, 2)
            self.assertEqual(len(FakeRuntime.instances), 1)
            receipts = list((root / ".ga" / "benchmark-receipts").glob("*.json"))
            self.assertEqual(len(receipts), 1)
            first_result = json.loads(first_output.getvalue())
            self.assertEqual(first_result["reasons"], ["IO_ERROR"])

            second_output = io.StringIO()
            with patch("tools.authority_runtime.AuthorityRuntime", FakeRuntime),                     patch.object(gah_benchmark.OperationJournal, "finish", lose_first_finish),                     redirect_stdout(second_output):
                second_code = gah_benchmark.main(args)
            self.assertEqual(second_code, 2)
            self.assertEqual(len(FakeRuntime.instances), 1)
            second_result = json.loads(second_output.getvalue())
            self.assertEqual(second_result["reasons"], ["OBSERVATION_MISSING"])
            self.assertEqual(
                second_result["result_ref"],
                first_result["result_ref"],
            )

    def test_cli_measure_uses_fixed_runtime_and_saves_incomplete_observation(self):
        from unittest.mock import patch
        from tools import gah_benchmark

        plan = self.plan()
        plan["created_at"] = int(time.time()) - 1
        plan["expires_at"] = int(time.time()) + 3600
        request = {
            "schema_version": 1, "action": "ci_check",
            "request_id": "ci-query-1", "run_id": "run-1",
            "expected_manifest_ref": self.ref("run_manifest", "run-1"),
            "expected_contract_ref": plan["payload"]["contract_ref"],
            "expected_baseline_ref": plan["payload"]["baseline_ref"],
            "expected_target_refs": plan["payload"]["target_refs"],
            "expected_use_cases": ["UC-CI"],
        }

        class FakeRuntime:
            instances = []

            def __init__(self, folder, **kwargs):
                self.calls = []
                self.closed = False
                self.__class__.instances.append(self)

            def client(self, uid, value):
                self.calls.append((uid, value))
                return {
                    "schema_version": 1, "kind": "ci_gate_result",
                    "action": "ci_check", "request_id": value["request_id"],
                    "run_id": value["run_id"], "checked_at": 1,
                    "expected_manifest_ref": value["expected_manifest_ref"],
                    "outputs_ref": self_ref("run_outputs", value["run_id"]),
                    "assurance": "HEALTHY", "reasons": [], "use": True,
                    "ci_eligible": True, "execution_status": "COMPLETED",
                    "exit_code": 0,
                }

            def close_clients(self):
                self.closed = True

        def self_ref(kind, identifier):
            return self.ref(kind, identifier)

        with tempfile.TemporaryDirectory(dir=str(gah_benchmark.ROOT)) as folder:
            root = Path(folder)
            runtime = root / "runtime"
            runtime.mkdir()
            (runtime / "deployment.json").write_text(json.dumps({
                "prefix": "gah-authority-" + "a" * 32,
                "image_id": "sha256:" + "b" * 64,
                "containers": [],
            }), encoding="utf-8")
            (root / "plan.json").write_text(
                json.dumps(plan, ensure_ascii=False), encoding="utf-8",
            )
            (root / "request.json").write_text(
                json.dumps(request, ensure_ascii=False), encoding="utf-8",
            )
            output = io.StringIO()
            with patch("tools.authority_runtime.AuthorityRuntime", FakeRuntime), redirect_stdout(output):
                code = gah_benchmark.main([
                    "measure", "--workspace", str(root), "--plan", "plan.json",
                    "--iteration-id", "iteration-1", "--recipe", "current_ci",
                    "--runtime", str(runtime), "--request", "request.json",
                    "--output", "observation.json",
                ])
            self.assertEqual(code, 2)
            result = json.loads(output.getvalue())
            self.assertEqual(result["operation_status"], "INCOMPLETE")
            self.assertEqual(result["reasons"], ["OBSERVATION_MISSING"])
            saved = json.loads((root / "observation.json").read_text(encoding="utf-8"))
            self.assertIsInstance(saved["payload"]["wall_ns"], int)
            self.assertIsNone(saved["payload"]["cpu_ns"])
            self.assertIsNone(saved["payload"]["rss_group_peak_bytes"])
            self.assertEqual(FakeRuntime.instances[-1].calls[0][0], 12004)
            self.assertTrue(FakeRuntime.instances[-1].closed)

            # 同一requestの再配送はjournalの保存結果を返し、runtimeを再実行しない。
            instance_count = len(FakeRuntime.instances)
            output = io.StringIO()
            with patch("tools.authority_runtime.AuthorityRuntime", FakeRuntime), redirect_stdout(output):
                replay_code = gah_benchmark.main([
                    "measure", "--workspace", str(root), "--plan", "plan.json",
                    "--iteration-id", "iteration-1", "--recipe", "current_ci",
                    "--runtime", str(runtime), "--request", "request.json",
                    "--output", "observation.json",
                ])
            self.assertEqual(replay_code, code)
            self.assertEqual(json.loads(output.getvalue()), result)
            self.assertEqual(len(FakeRuntime.instances), instance_count)


    def whole_run_inputs(self):
        manifest = self.manifest("normal_run")
        manifest["intervals"] = {
            "start_event": "run_prepare_requested",
            "end_event": "fresh_ci_check_completed",
        }
        run_request = {
            "schema_version": 1, "run_id": "whole-run-1",
            "contract_series_id": "series-1",
            "expected_contract_ref": manifest["contract_ref"],
            "trigger": "manual",
        }
        ci_request = {
            "schema_version": 1, "action": "ci_check",
            "request_id": "whole-ci-1", "run_id": "whole-run-1",
            "expected_manifest_ref": self.ref("run_manifest", "whole-run-1"),
            "expected_contract_ref": manifest["contract_ref"],
            "expected_baseline_ref": manifest["baseline_ref"],
            "expected_target_refs": manifest["target_refs"],
            "expected_use_cases": ["UC-CI"],
        }
        manifest["run_request_ref"] = content_ref(
            "run_request", run_request["run_id"], run_request,
        )
        manifest["ci_request_ref"] = content_ref(
            "ci_request", ci_request["request_id"], ci_request,
        )
        plan = benchmark.make_plan(
            manifest, identifier="whole-plan-1",
            source_ref=self.ref("snapshot_manifest", "plan-source"),
            requirements_ref=self.ref("snapshot_manifest", "plan-requirements"),
            created_at=100, expires_at=200,
        )
        return plan, run_request, ci_request

    def test_whole_run_manifest_requires_two_fixed_request_refs(self):
        plan, run_request, ci_request = self.whole_run_inputs()
        checked_run, checked_ci, runner_kind = benchmark.validate_whole_run_requests(
            plan, run_request, ci_request,
        )
        self.assertEqual(checked_run, run_request)
        self.assertEqual(checked_ci, ci_request)
        self.assertEqual(runner_kind, "fixture")
        missing = dict(plan["payload"])
        del missing["ci_request_ref"]
        with self.assertRaises(ContractError):
            benchmark.validate_manifest(missing)
        wrong = dict(plan["payload"])
        wrong["ci_request_ref"] = self.ref("run_request", "wrong")
        with self.assertRaises(ContractError):
            benchmark.validate_manifest(wrong)
        changed = dict(ci_request, expected_target_refs=[])
        with self.assertRaises(ContractError):
            benchmark.validate_whole_run_requests(plan, run_request, changed)

    def test_whole_run_recipe_keeps_resource_gaps_incomplete(self):
        wall = iter((10, 110))
        seen = []
        measured = benchmark.measure_recipe(
            "whole_run",
            lambda action, request: (
                seen.append((action, request["run_request"]["run_id"]))
                or {"exit_code": 0}
            ),
            {
                "run_request": {"run_id": "whole-run-1"},
                "ci_request": {"request_id": "whole-ci-1"},
            },
            monotonic_clock=lambda: next(wall),
        )
        self.assertEqual(seen, [("supervised_run", "whole-run-1")])
        self.assertEqual(measured["wall_ns"], 100)
        self.assertIsNone(measured["cpu_ns"])
        self.assertIsNone(measured["rss_group_peak_bytes"])
        self.assertEqual(measured["operation_status"], "INCOMPLETE")
        self.assertEqual(measured["failure_class"], "MEASUREMENT_ERROR")
        self.assertFalse(measured["valid_for_slo"])

    def test_cli_whole_run_uses_fixed_supervision_and_replays_without_rerun(self):
        from unittest.mock import patch
        from tools import gah_benchmark

        plan, run_request, ci_request = self.whole_run_inputs()
        plan["created_at"] = int(time.time()) - 1
        plan["expires_at"] = int(time.time()) + 3600
        execute_calls = []
        lock_state = {"held": False}

        class FakeRuntime:
            instances = []
            run_started = False
            close_lock_states = []

            def __init__(self, folder, **kwargs):
                self.calls = []
                self.closed = False
                self.__class__.instances.append(self)

            def client(self, uid, request):
                self.calls.append((uid, request))
                if request["action"] == "run_status":
                    if not self.__class__.run_started:
                        return {
                            "schema_version": 1, "kind": "authority_error",
                            "reason": "RUN_MISSING", "ci_eligible": False,
                        }
                    return {
                        "schema_version": 1, "kind": "evaluation_authority_result",
                        "action": "run_status",
                        "request_id": request["request_id"],
                        "ci_eligible": False, "run_id": request["run_id"],
                        "manifest": {}, "plan": {},
                        "contract_series_id": "series-1",
                        "contract_generation": 1,
                        "resource_snapshot": {},
                    }
                return {
                    "schema_version": 1, "kind": "ci_gate_result",
                    "action": "ci_check", "request_id": request["request_id"],
                    "run_id": request["run_id"], "checked_at": 1,
                    "expected_manifest_ref": request["expected_manifest_ref"],
                    "outputs_ref": self_ref("run_outputs", request["run_id"]),
                    "assurance": "HEALTHY", "reasons": [], "use": True,
                    "ci_eligible": True, "execution_status": "COMPLETED",
                    "exit_code": 0,
                }

            def close_clients(self):
                self.__class__.close_lock_states.append(lock_state["held"])
                self.closed = True

        class FakeRunner:
            def __init__(self, *args):
                self.args = args

        class LockContext:
            def __enter__(self):
                lock_state["held"] = True

            def __exit__(self, *exc_info):
                lock_state["held"] = False

        def fake_lock(*args):
            return LockContext()

        def self_ref(kind, identifier):
            return self.ref(kind, identifier)

        def fake_execute(runtime, runner, folder, request, mode):
            execute_calls.append((runtime, runner, folder, request, mode))
            FakeRuntime.run_started = True
            return {
                "run_id": request["run_id"],
                "gate": {
                    "expected_manifest_ref": ci_request["expected_manifest_ref"],
                },
                "exit_code": 0,
            }

        with tempfile.TemporaryDirectory(dir=str(gah_benchmark.ROOT)) as folder:
            root = Path(folder)
            runtime = root / "runtime"
            runtime.mkdir()
            (runtime / "deployment.json").write_text(json.dumps({
                "prefix": "gah-authority-" + "f" * 32,
                "image_id": "sha256:" + "e" * 64,
                "containers": [],
            }), encoding="utf-8")
            (root / "plan.json").write_bytes(canonical_bytes(plan))
            (root / "run.json").write_bytes(canonical_bytes(run_request))
            (root / "ci.json").write_bytes(canonical_bytes(ci_request))
            args = [
                "measure", "--workspace", str(root), "--plan", "plan.json",
                "--iteration-id", "whole-iteration-1", "--recipe", "whole_run",
                "--runtime", str(runtime), "--request", "run.json",
                "--ci-request", "ci.json", "--output", "observation.json",
            ]
            first_output = io.StringIO()
            with patch("tools.authority_runtime.AuthorityRuntime", FakeRuntime),                     patch("tools.gah_benchmark.DockerFixtureRunner", FakeRunner),                     patch("tools.gah_benchmark._trusted_measure_principal", return_value="operator-test"), patch("tools.gah_benchmark.operation_lock", fake_lock),                     patch("tools.gah_run.execute", fake_execute),                     redirect_stdout(first_output):
                first_code = gah_benchmark.main(args)
            self.assertEqual(first_code, 2)
            self.assertEqual(len(execute_calls), 1)
            self.assertEqual(len(FakeRuntime.instances), 1)
            ci_calls = [
                item[1] for item in FakeRuntime.instances[0].calls
                if item[1]["action"] == "ci_check"
            ]
            self.assertEqual(ci_calls, [ci_request])
            self.assertTrue(FakeRuntime.instances[0].closed)
            self.assertEqual(FakeRuntime.close_lock_states, [True])
            self.assertEqual([item[1]["action"] for item in FakeRuntime.instances[0].calls], ["run_status", "ci_check"])
            saved = json.loads((root / "observation.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["plan_ref"], content_ref(
                "benchmark_plan", plan["id"], plan,
            ))
            self.assertIsInstance(saved["payload"]["wall_ns"], int)
            self.assertIsNone(saved["payload"]["cpu_ns"])
            self.assertIsNone(saved["payload"]["rss_group_peak_bytes"])
            self.assertFalse(saved["payload"]["valid_for_slo"])

            second_output = io.StringIO()
            with patch("tools.authority_runtime.AuthorityRuntime", FakeRuntime),                     patch("tools.gah_benchmark.DockerFixtureRunner", FakeRunner),                     patch("tools.gah_benchmark._trusted_measure_principal", return_value="operator-test"), patch("tools.gah_benchmark.operation_lock", fake_lock),                     patch("tools.gah_run.execute", fake_execute),                     redirect_stdout(second_output):
                second_code = gah_benchmark.main(args)
            self.assertEqual(second_code, first_code)
            self.assertEqual(len(execute_calls), 1)
            self.assertEqual(len(FakeRuntime.instances), 1)
            self.assertEqual(
                json.loads(second_output.getvalue()),
                json.loads(first_output.getvalue()),
            )

            # run_idを再利用してiteration/request_idだけ変えても、既存runを再計測しない。
            changed_args = list(args) + ["--request-id", "whole-request-2"]
            changed_args[changed_args.index("--iteration-id") + 1] = "whole-iteration-2"
            third_output = io.StringIO()
            with patch("tools.authority_runtime.AuthorityRuntime", FakeRuntime),                     patch("tools.gah_benchmark.DockerFixtureRunner", FakeRunner),                     patch("tools.gah_benchmark._trusted_measure_principal", return_value="operator-test"),                     patch("tools.gah_benchmark.operation_lock", fake_lock),                     patch("tools.gah_run.execute", fake_execute),                     redirect_stdout(third_output):
                third_code = gah_benchmark.main(changed_args)
            self.assertEqual(third_code, 1)
            third_result = json.loads(third_output.getvalue())
            self.assertEqual(third_result["operation_status"], "REJECTED")
            self.assertEqual(third_result["reasons"], ["BINDING_MISMATCH"])
            self.assertEqual(len(execute_calls), 1)
            self.assertEqual(len(FakeRuntime.instances), 2)
            self.assertEqual(FakeRuntime.close_lock_states, [True, True])

if __name__ == "__main__":
    unittest.main()
