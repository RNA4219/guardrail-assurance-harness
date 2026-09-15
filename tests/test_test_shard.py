"""CI分割の全件保持とmodule/class準備の分断防止を検証する。"""
from collections import Counter
import unittest
from tools.test_shard import cases, select_suite


class TestShardTests(unittest.TestCase):
    def fixture(self):
        suite = unittest.TestSuite()
        for i in range(24):
            cls = type("Sample", (unittest.TestCase,), {"__module__": f"test_sample_{i}",
                "test_first": lambda self: None, "test_second": lambda self: None})
            suite.addTest(unittest.TestSuite([cls("test_first"), cls("test_second")]))
        return suite

    def test_union_preserves_all_cases_once_and_keeps_modules_together(self):
        for count in (1, 2, 4, 8):
            with self.subTest(count=count):
                suite = self.fixture(); expected = Counter(case.id() for case in cases(suite))
                partitions = [list(cases(select_suite(suite, index, count))) for index in range(count)]
                self.assertEqual(Counter(case.id() for group in partitions for case in group), expected)
                locations = {}
                for index, group in enumerate(partitions):
                    for case in group:
                        self.assertEqual(locations.setdefault(type(case).__module__, index), index)
                self.assertEqual(partitions, [list(cases(select_suite(suite, index, count))) for index in range(count)])

    def test_invalid_partition_parameters_are_rejected(self):
        for index, count in ((0, 0), (-1, 4), (4, 4), (0, 33), (True, 4), (0, True), (0.0, 4)):
            with self.subTest(index=index, count=count), self.assertRaises(ValueError):
                select_suite(self.fixture(), index, count)


if __name__ == "__main__":
    unittest.main()
