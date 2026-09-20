"""CIの全件保持・独立module・計画照合・失敗伝搬を検証する。"""
from collections import Counter
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools import test_matrix


class TestMatrixTests(unittest.TestCase):
    def fixture(self):
        suite = unittest.TestSuite()
        for index, size in enumerate((1, 9, 2, 8, 3, 7, 4, 6)):
            methods = {f"test_{i}": lambda self: None for i in range(size)}
            cls = type("Sample", (unittest.TestCase,),
                       {"__module__": f"test_sample_{index}", **methods})
            suite.addTest(unittest.defaultTestLoader.loadTestsFromTestCase(cls))
        return suite

    def test_every_case_once_and_modules_stay_together(self):
        suite = self.fixture()
        lanes = test_matrix.partition(suite, dedicated=(("slow", "test_sample_0"),), count=3)
        expected = Counter(case.id() for case in test_matrix.cases(suite))
        self.assertEqual(Counter(case.id() for group in lanes.values() for case in group), expected)
        owners = {}
        for lane, group in lanes.items():
            for case in group:
                self.assertEqual(owners.setdefault(type(case).__module__, lane), lane)
        self.assertEqual(owners["test_sample_0"], "slow")
        sizes = [len(lanes[f"regression-{i}"]) for i in range(3)]
        self.assertLessEqual(max(sizes) - min(sizes), 2)

    def test_partition_is_deterministic_and_has_no_empty_lanes(self):
        first = test_matrix.describe(test_matrix.partition(self.fixture(), dedicated=(), count=32))
        second = test_matrix.describe(test_matrix.partition(self.fixture(), dedicated=(), count=32))
        self.assertEqual(first, second)
        self.assertTrue(all(row["tests"] > 0 for row in first["matrix"]["include"]))

    def test_missing_or_duplicate_dedicated_module_fails(self):
        for dedicated in ((("slow", "missing"),),
                          (("a", "test_sample_0"), ("b", "test_sample_0")),
                          (("a", "test_sample_0"), ("a", "test_sample_1")),
                          (("regression-0", "test_sample_0"),)):
            with self.subTest(dedicated=dedicated), self.assertRaises(ValueError):
                test_matrix.partition(self.fixture(), dedicated=dedicated)

    def test_empty_duplicate_or_invalid_count_fails(self):
        with self.assertRaises(ValueError):
            test_matrix.partition(unittest.TestSuite(), dedicated=())
        suite = self.fixture()
        suite.addTest(next(test_matrix.cases(suite)))
        with self.assertRaises(ValueError):
            test_matrix.partition(suite, dedicated=())
        for count in (0, 33, True, 1.0):
            with self.subTest(count=count), self.assertRaises(ValueError):
                test_matrix.partition(self.fixture(), dedicated=(), count=count)

    def invoke(self, argv, *, errors=(), failure=False, case_methods=None):
        loader = unittest.TestLoader()
        loader.errors = list(errors)
        suite = self.fixture()
        if case_methods is not None:
            cls = type("Observed", (unittest.TestCase,), {
                "__module__": "test_sample_nonpass", **case_methods,
            })
            suite = unittest.defaultTestLoader.loadTestsFromTestCase(cls)
        if failure:
            case = next(test_matrix.cases(suite))
            setattr(case, case._testMethodName, lambda: self.fail("EXPECTED_FAILURE"))
        output = io.StringIO()
        partition = test_matrix.partition
        with patch.object(test_matrix.unittest, "TestLoader", return_value=loader), \
             patch.object(loader, "discover", return_value=suite), \
             patch.object(test_matrix, "partition", side_effect=lambda suite: partition(suite, dedicated=(), count=2)), \
             redirect_stdout(output), redirect_stderr(io.StringIO()):
            code = test_matrix.main(argv)
        return code, output.getvalue()

    def test_plan_is_json_and_contains_all_cases(self):
        code, output = self.invoke(["--plan"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output)["total_tests"], 40)

    def test_import_failure_is_not_silently_skipped(self):
        code, _ = self.invoke(["--plan"], errors=("IMPORT_FAILED",))
        self.assertEqual(code, 2)

    def test_unknown_lane_and_stale_plan_fail(self):
        for argv in (["--lane", "missing"],
                     ["--lane", "regression-0", "--expected-plan-digest", "stale"]):
            with self.subTest(argv=argv):
                self.assertEqual(self.invoke(argv)[0], 2)

    def test_selected_lane_runs_only_its_cases(self):
        code, output = self.invoke(["--lane", "regression-0"])
        result = json.loads(output.splitlines()[-1])
        self.assertEqual(code, 0)
        self.assertTrue(result["passed"])
        self.assertEqual(result["tests_run"], result["planned_tests"])
        self.assertLess(result["tests_run"], 40)

    def test_failed_test_fails_the_lane(self):
        results = [self.invoke(["--lane", f"regression-{i}"], failure=True)[0] for i in range(2)]
        self.assertEqual(sorted(results), [0, 1])

    def test_plain_lane_rejects_skip_expected_failure_and_unexpected_success(self):
        examples = (
            ("skipped", unittest.skip("diagnostic")(lambda self: None)),
            ("expected_failures", unittest.expectedFailure(lambda self: self.fail("expected"))),
            ("unexpected_successes", unittest.expectedFailure(lambda self: None)),
        )
        for field, method in examples:
            with self.subTest(field=field):
                code, output = self.invoke(
                    ["--lane", "regression-0"], case_methods={"test_sample": method},
                )
                result = json.loads(output.splitlines()[-1])
                self.assertEqual(code, 1)
                self.assertFalse(result["passed"])
                self.assertEqual(result[field], 1)
                self.assertEqual(result["tests_run"], result["planned_tests"])

    def test_plain_lane_reports_setup_error_and_unrun_planned_test(self):
        def setup_class(cls):
            raise RuntimeError("setup failure")
        code, output = self.invoke(
            ["--lane", "regression-0"],
            case_methods={"setUpClass": classmethod(setup_class), "test_ok": lambda self: None},
        )
        result = json.loads(output.splitlines()[-1])
        self.assertEqual(code, 1)
        self.assertFalse(result["passed"])
        self.assertEqual(result["errors"], 1)
        self.assertEqual(result["tests_run"], 0)
        self.assertEqual(result["planned_tests"], 1)

    def timing_context(self):
        return {
            "runner_profile": "runner-linux-x64",
            "python_version": "3.12.4",
            "image_ref": {
                "kind": "container_image",
                "id": "gah-ci",
                "digest": "b" * 64,
            },
            "measurement_method_version": "measure-1",
        }

    def timing_history(self, observations):
        return {
            "schema_version": 1,
            "kind": "ci_timing_history",
            "algorithm_version": test_matrix.TIMING_ALGORITHM_VERSION,
            **self.timing_context(),
            "observations": observations,
        }

    def observation(self, module, elapsed_ns, test_count, observed_at, run_id,
                    *, success=True, source_sha="a" * 40):
        return {
            "module": module,
            "elapsed_ns": elapsed_ns,
            "test_count": test_count,
            "observed_at": observed_at,
            "success": success,
            "source_sha": source_sha,
            "run_id": run_id,
        }

    def resolve(self, modules, history, **kwargs):
        return test_matrix.resolve_timing_history(
            modules, history,
            runner_profile=self.timing_context()["runner_profile"],
            python_version=self.timing_context()["python_version"],
            image_ref=self.timing_context()["image_ref"],
            measurement_method_version=self.timing_context()[
                "measurement_method_version"
            ],
            **kwargs,
        )

    def test_timing_history_uses_module_medians_and_fixed_fallback_rate(self):
        rows = [
            self.observation("fast", 4_000_000_000, 1, 1, "run-1"),
            self.observation("fast", 6_000_000_000, 1, 2, "run-2"),
            self.observation("fast", 8_000_000_000, 1, 3, "run-3"),
            self.observation("wide", 9_000_000_000, 9, 1, "run-1"),
            self.observation("wide", 18_000_000_000, 9, 2, "run-2"),
            self.observation("wide", 27_000_000_000, 9, 3, "run-3"),
            self.observation("unknown", 3_000_000_000, 3, 1, "run-1"),
        ]
        result = self.resolve({"fast": 1, "wide": 9, "missing": 2},
                              self.timing_history(rows))
        self.assertEqual(result["history_status"], "history")
        self.assertEqual(result["history_valid_observation_count"], 7)
        self.assertEqual(result["module_estimates_ns"], {
            "fast": 6_000_000_000,
            "missing": 4_000_000_000,
            "wide": 18_000_000_000,
        })
        self.assertEqual(result["uncertainty"]["per_test_fallback_ns"],
                         2_000_000_000)
        self.assertEqual(result["fallback_modules"], ["missing"])

    def test_timing_history_deduplicates_same_module_run(self):
        rows = [
            self.observation("module", 1_000_000_000, 1, 1, "run-1"),
            self.observation("module", 9_000_000_000, 1, 2, "run-1"),
            self.observation("module", 3_000_000_000, 1, 3, "run-2"),
        ]
        result = self.resolve({"module": 1}, self.timing_history(rows))
        self.assertEqual(result["history_valid_observation_count"], 2)
        self.assertEqual(result["module_estimates_ns"]["module"],
                         6_000_000_000)

    def test_timing_history_rejects_failed_or_mismatched_observations(self):
        rows = [
            self.observation("module", 4_000_000_000, 1, 1, "run-1",
                             success=False),
            self.observation("other", 4_000_000_000, 1, 1, "run-1"),
        ]
        value = self.timing_history(rows)
        value["runner_profile"] = "different-runner"
        result = self.resolve({"module": 2}, value)
        self.assertEqual(result["history_status"], "fallback")
        self.assertEqual(result["history_reason"], "OBSERVATION_MISSING")
        self.assertEqual(result["history_valid_observation_count"], 0)
        self.assertEqual(result["module_estimates_ns"]["module"],
                         2_000_000_000)

    def test_timing_history_selection_does_not_depend_on_job_now(self):
        rows = [
            self.observation("module", 2_000_000_000, 1, 1, "run-1"),
        ]
        value = self.timing_history(rows)
        first = self.resolve({"module": 1}, value, now=0)
        second = self.resolve({"module": 1}, value, now=10**12)
        self.assertEqual(first, second)

    def test_timing_history_digest_is_canonical_only(self):
        value = self.timing_history([
            self.observation("module", 2_000_000_000, 1, 1, "run-1"),
        ])
        canonical = test_matrix.timing_history_digest(value)
        pretty = json.dumps(value, indent=2).encode("utf-8")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "history.json"
            path.write_bytes(pretty)
            raw_digest = hashlib.sha256(pretty).hexdigest()
            with self.assertRaises(ValueError):
                test_matrix.resolve_timing_history(
                    {"module": 1}, path, history_digest=raw_digest,
                    **self.timing_context(),
                )
            result = test_matrix.resolve_timing_history(
                {"module": 1}, path, history_digest=canonical,
                **self.timing_context(),
            )
            self.assertEqual(result["history_digest"], canonical)

    def test_timed_partition_keeps_modules_together_and_is_deterministic(self):
        suite = self.fixture()
        rows = []
        for index, size in enumerate((1, 9, 2, 8, 3, 7, 4, 6)):
            rows.append(self.observation(
                f"test_sample_{index}", (index + 1) * 1_000_000_000,
                size, index + 1, f"run-{index}",
            ))
        history = self.timing_history(rows)
        first = test_matrix.partition_timed(
            suite, dedicated=(), count=3, timing_history=history,
            **self.timing_context(),
        )[0]
        second = test_matrix.partition_timed(
            self.fixture(), dedicated=(), count=3, timing_history=history,
            **self.timing_context(),
        )[0]
        self.assertEqual(test_matrix.describe(first), test_matrix.describe(second))
        owners = {}
        for lane, group in first.items():
            for case in group:
                module = type(case).__module__
                self.assertEqual(owners.setdefault(module, lane), lane)

    def test_timing_history_has_closed_observation_fields(self):
        value = self.timing_history([
            self.observation("module", 2_000_000_000, 1, 1, "run-1"),
        ])
        row = value["observations"][0]
        row["wall_ns"] = row.pop("elapsed_ns")
        result = test_matrix.resolve_timing_history(
            {"module": 1}, value, **self.timing_context(),
        )
        self.assertEqual(result["history_status"], "fallback")
        self.assertEqual(result["history_reason"], "HISTORY_INVALID")
        value = self.timing_history([
            self.observation("module", 2_000_000_000, 1, 1, "run-1"),
        ])
        with self.assertRaises(ValueError):
            test_matrix.resolve_timing_history(
                {"module": 1}, value, image_ref="image",
                runner_profile=self.timing_context()["runner_profile"],
                python_version=self.timing_context()["python_version"],
                measurement_method_version=self.timing_context()[
                    "measurement_method_version"
                ],
            )

    def test_timing_collection_rejects_skip_expected_failure_and_setup_failure(self):
        skipped = unittest.skip("diagnostic")(
            lambda self: None
        )
        expected = unittest.expectedFailure(
            lambda self: self.fail("expected")
        )
        def set_up_class(cls):
            raise RuntimeError("setup failure")
        cases_to_check = (
            ("test_skip", {"test_skip": skipped}),
            ("test_expected", {"test_expected": expected}),
            ("test_setup", {
                "setUpClass": classmethod(set_up_class),
                "test_ok": lambda self: None,
            }),
        )
        for module, methods in cases_to_check:
            with self.subTest(module=module):
                cls = type("Observed", (unittest.TestCase,), {
                    "__module__": module, **methods
                })
                suite = unittest.defaultTestLoader.loadTestsFromTestCase(cls)
                history, execution = test_matrix._execute_timing_suite(
                    suite,
                    profile=self.timing_context(),
                    source_sha="d" * 40,
                    run_id="run-failure",
                    observed_at=123,
                )
                self.assertFalse(execution["passed"])
                expected_run = 0 if module == "test_setup" else execution["planned_tests"]
                self.assertEqual(execution["tests_run"], expected_run)
                self.assertEqual(history["observations"], [])
                self.assertEqual(execution["failed_modules"], [module])

    def test_collect_and_save_timing_history_preserve_run_and_success_only(self):
        suite = self.fixture()
        source_sha = "c" * 40
        history, execution = test_matrix._execute_timing_suite(
            suite,
            profile=self.timing_context(),
            source_sha=source_sha,
            run_id="local-run",
            observed_at=123,
        )
        self.assertTrue(execution["passed"])
        self.assertEqual(execution["tests_run"], execution["planned_tests"])
        self.assertTrue(history["observations"])
        self.assertTrue(all(item["run_id"] == "local-run"
                            and item["source_sha"] == source_sha
                            and item["success"] is True
                            for item in history["observations"]))
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "history.json"
            digest = test_matrix.write_timing_history(output, history)
            self.assertEqual(digest, test_matrix.timing_history_digest(history))
            self.assertEqual(test_matrix.write_timing_history(output, history),
                             digest)
            output.write_bytes(b"{}")
            with self.assertRaises(ValueError):
                test_matrix.write_timing_history(output, history)


if __name__ == "__main__":
    unittest.main()
