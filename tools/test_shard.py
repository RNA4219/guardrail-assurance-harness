"""全unittestをmodule単位で決定的に分割する。試験・class準備は省略しない。"""
import argparse
import hashlib
import sys
import unittest


def cases(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from cases(item)
        else:
            yield item


def select_suite(suite, index, count):
    if (type(index) is not int or type(count) is not int
            or not 1 <= count <= 32 or not 0 <= index < count):
        raise ValueError("INVALID_SHARD")
    selected = unittest.TestSuite()
    for case in cases(suite):
        module = type(case).__module__.removeprefix("tests.")
        owner = int.from_bytes(hashlib.sha256(module.encode()).digest()[:8], "big") % count
        if owner == index:
            selected.addTest(case)
    return selected


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=int, required=True)
    parser.add_argument("--count", type=int, required=True)
    args = parser.parse_args(argv)
    if not 1 <= args.count <= 32 or not 0 <= args.index < args.count:
        parser.error("0 <= index < count <= 32 が必要です")
    loader = unittest.TestLoader()
    suite = loader.discover("tests")
    if loader.errors:
        for error in loader.errors:
            print(error, file=sys.stderr)
        return 2
    total = suite.countTestCases()
    selected = select_suite(suite, args.index, args.count)
    if not total or not selected.countTestCases():
        print("EMPTY_TEST_SHARD", file=sys.stderr)
        return 2
    print(f"shard {args.index + 1}/{args.count}: {selected.countTestCases()} of {total} tests", flush=True)
    return 0 if unittest.TextTestRunner(verbosity=2).run(selected).wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
