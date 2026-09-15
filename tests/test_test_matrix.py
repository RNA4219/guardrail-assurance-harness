"""CIの全件保持・独立module・計画照合・失敗伝搬を検証する。"""
from collections import Counter
from contextlib import redirect_stderr, redirect_stdout
import io
import json
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

    def invoke(self, argv, *, errors=(), failure=False):
        loader = unittest.TestLoader()
        loader.errors = list(errors)
        suite = self.fixture()
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


if __name__ == "__main__":
    unittest.main()
