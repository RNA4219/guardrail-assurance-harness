"""重い統合moduleを独立させ、残る全unittestを並行CIへ配分する。"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
import time
import unittest

from tools.test_shard import cases

DEDICATED = (
    ("llm-supervision", "test_llm_supervision_integration"),
    ("llm-revision", "test_llm_revision_integration"),
    ("combined", "test_combined_integration"),
    ("finding-revalidation", "test_finding_revalidation_integration"),
    ("following-contract", "test_following_contract_integration"),
    ("mutation-review", "test_mutation_review_integration"),
)
GENERAL_SHARDS = 6


def partition(suite, *, dedicated=DEDICATED, count=GENERAL_SHARDS):
    if type(count) is not int or not 1 <= count <= 32:
        raise ValueError("INVALID_SHARD_COUNT")
    modules = {}
    ids = []
    for case in cases(suite):
        module = type(case).__module__.removeprefix("tests.")
        modules.setdefault(module, []).append(case)
        ids.append(case.id())
    if not ids or any(n != 1 for n in Counter(ids).values()):
        raise ValueError("EMPTY_OR_DUPLICATE_TEST_INVENTORY")
    lanes = {}
    reserved = set()
    for lane, module in dedicated:
        if lane in lanes or module in reserved or module not in modules:
            raise ValueError("INVALID_DEDICATED_MODULE")
        lanes[lane] = modules[module]
        reserved.add(module)
    remaining = sorted((name for name in modules if name not in reserved),
                       key=lambda name: (-len(modules[name]), name))
    general = [[] for _ in range(min(count, len(remaining)))]
    weights = [0] * len(general)
    for name in remaining:
        index = min(range(len(general)), key=lambda i: (weights[i], i))
        general[index].extend(modules[name])
        weights[index] += len(modules[name])
    for index, group in enumerate(general):
        lane = f"regression-{index}"
        if lane in lanes:
            raise ValueError("DUPLICATE_LANE")
        lanes[lane] = group
    planned = Counter(case.id() for group in lanes.values() for case in group)
    if planned != Counter(ids):
        raise ValueError("INCOMPLETE_TEST_PARTITION")
    return lanes


def describe(lanes):
    inventory = {lane: [case.id() for case in group] for lane, group in lanes.items()}
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True).encode()).hexdigest()
    return {"matrix": {"include": [{"lane": lane, "tests": len(group)}
                                   for lane, group in lanes.items()]},
            "plan_digest": digest, "total_tests": sum(map(len, lanes.values()))}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true")
    mode.add_argument("--lane")
    parser.add_argument("--expected-plan-digest")
    args = parser.parse_args(argv)
    loader = unittest.TestLoader()
    suite = loader.discover("tests")
    if loader.errors:
        for error in loader.errors:
            print(error, file=sys.stderr)
        return 2
    try:
        lanes = partition(suite)
        plan = describe(lanes)
        if args.expected_plan_digest and args.expected_plan_digest != plan["plan_digest"]:
            raise ValueError("TEST_PLAN_CHANGED")
        if args.lane and args.lane not in lanes:
            raise ValueError("UNKNOWN_TEST_LANE")
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2
    if args.plan:
        print(json.dumps(plan))
        return 0
    selected = lanes[args.lane]
    print(f"{args.lane}: {len(selected)} of {plan['total_tests']} tests", flush=True)
    started = time.perf_counter()
    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(selected))
    passed = result.wasSuccessful() and result.testsRun == len(selected)
    print(json.dumps({"lane": args.lane, "planned_tests": len(selected),
                      "tests_run": result.testsRun, "passed": passed,
                      "seconds": round(time.perf_counter() - started, 3),
                      "plan_digest": plan["plan_digest"]}), flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
