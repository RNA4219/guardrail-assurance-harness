"""重い統合moduleを独立させ、残る全unittestを並行CIへ配分する。"""
import argparse
from collections import Counter
import hashlib
import io
from fractions import Fraction
import json
from pathlib import Path
import sys
import time
import unittest
import uuid

from tools.test_shard import cases

DEDICATED = (
    ("llm-supervision", "test_llm_supervision_integration"),
    ("llm-revision", "test_llm_revision_integration"),
    ("combined", "test_combined_integration"),
    ("finding-revalidation", "test_finding_revalidation_integration"),
    ("following-contract", "test_following_contract_integration"),
    ("mutation-review", "test_mutation_review_integration"),
    ("partitioned-transition", "test_partitioned_transition_integration"),
)
GENERAL_SHARDS = 6


def _legacy_partition(suite, *, dedicated=DEDICATED, count=GENERAL_SHARDS):
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


TIMING_ALGORITHM_VERSION = "timing-v1"
MIN_MODULE_TIME_NS = 1_000_000_000
_HISTORY_FIELDS = frozenset({
    "schema_version", "kind", "algorithm_version",
    "runner_profile", "python_version", "image_ref",
    "measurement_method_version", "observations",
})
_OBSERVATION_FIELDS = frozenset({
    "module", "elapsed_ns", "test_count", "observed_at", "success",
    "source_sha", "run_id",
})
_CONTEXT_NAMES = (
    "runner_profile", "python_version", "image_ref",
    "measurement_method_version",
)


def _history_canonical(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as error:
        raise ValueError("INVALID_TIMING_HISTORY") from error


def timing_history_digest(value):
    _validate_history(value)
    return hashlib.sha256(_history_canonical(value)).hexdigest()


def _history_pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("DUPLICATE_HISTORY_KEY")
        result[key] = value
    return result


def _history_float(_: str):
    raise ValueError("INVALID_TIMING_HISTORY")


def _valid_history_digest(value):
    return (type(value) is str and len(value) == 64
            and all(char in "0123456789abcdef" for char in value))


def _valid_source_sha(value):
    return (type(value) is str and len(value) in (40, 64)
            and all(char in "0123456789abcdef" for char in value))


def _history_text(value):
    return type(value) is str and bool(value.strip()) and len(value) <= 512


def _valid_history_context(name, value):
    if name == "image_ref":
        return (type(value) is dict and set(value) == {"kind", "id", "digest"}
                and _history_text(value.get("kind"))
                and _history_text(value.get("id"))
                and _valid_history_digest(value.get("digest")))
    return _history_text(value)


def _validate_history(value):
    if type(value) is not dict or set(value) != _HISTORY_FIELDS:
        raise ValueError("INVALID_TIMING_HISTORY")
    if (type(value["schema_version"]) is not int
            or value["schema_version"] != 1
            or value["kind"] != "ci_timing_history"
            or value["algorithm_version"] != TIMING_ALGORITHM_VERSION):
        raise ValueError("INVALID_TIMING_HISTORY")
    for name in _CONTEXT_NAMES:
        if not _valid_history_context(name, value[name]):
            raise ValueError("INVALID_TIMING_HISTORY")
    observations = value["observations"]
    if type(observations) is not list:
        raise ValueError("INVALID_TIMING_HISTORY")
    for observation in observations:
        if type(observation) is not dict or set(observation) != _OBSERVATION_FIELDS:
            raise ValueError("INVALID_TIMING_HISTORY")
        if (type(observation["module"]) is not str
                or not observation["module"]
                or len(observation["module"]) > 128
                or type(observation["elapsed_ns"]) is not int
                or type(observation["elapsed_ns"]) is bool
                or not 0 <= observation["elapsed_ns"] <= 2**53 - 1
                or type(observation["test_count"]) is not int
                or type(observation["test_count"]) is bool
                or not 1 <= observation["test_count"] <= 2**53 - 1
                or type(observation["observed_at"]) is not int
                or type(observation["observed_at"]) is bool
                or not 0 <= observation["observed_at"] <= 2**53 - 1
                or type(observation["success"]) is not bool
                or not _valid_source_sha(observation["source_sha"])
                or not _history_text(observation["run_id"])):
            raise ValueError("INVALID_TIMING_HISTORY")
    return value


def _load_timing_history(source, expected_digest=None):
    if expected_digest is not None and not _valid_history_digest(expected_digest):
        raise ValueError("INVALID_TIMING_HISTORY_DIGEST")
    if isinstance(source, (str, Path)):
        try:
            raw = Path(source).read_bytes()
            if len(raw) > 1_048_576:
                return None, None, "HISTORY_INVALID"
            value = json.loads(raw.decode("utf-8"),
                               object_pairs_hook=_history_pairs,
                               parse_float=_history_float,
                               parse_constant=_history_float)
        except (OSError, UnicodeError, ValueError, RecursionError):
            return None, None, "HISTORY_INVALID"
    elif type(source) is dict:
        value = source
    else:
        return None, None, "HISTORY_INVALID"
    try:
        value = _validate_history(value)
        digest = timing_history_digest(value)
    except ValueError:
        return None, None, "HISTORY_INVALID"
    # History is identified by the existing canonical JSON bytes only. A
    # pretty-printed raw file digest is deliberately not interchangeable.
    if expected_digest is not None and expected_digest != digest:
        raise ValueError("TIMING_HISTORY_DIGEST_MISMATCH")
    return value, digest, None

def _history_rows(history):
    if type(history) is not dict:
        return []
    context = {name: history[name] for name in _CONTEXT_NAMES}
    return [
        {
            "module": observation["module"],
            "elapsed_ns": observation["elapsed_ns"],
            "observed_at": observation["observed_at"],
            "test_count": observation["test_count"],
            "success": observation["success"],
            "source_sha": observation["source_sha"],
            "run_id": observation["run_id"],
            "context": context,
            "sequence": sequence,
        }
        for sequence, observation in enumerate(history["observations"])
    ]


def _timing_median(values):
    values = sorted(Fraction(value) for value in values)
    if not values:
        return None
    middle = len(values) // 2
    if len(values) % 2:
        return values[middle]
    return (values[middle - 1] + values[middle]) / 2


def _timing_ceil(numerator, denominator=None):
    value = Fraction(numerator) if denominator is None else Fraction(numerator, denominator)
    return -(-value.numerator // value.denominator)


def resolve_timing_history(modules, history=None, *, history_digest=None, now=None,
                           runner_profile=None, source_sha=None, python_version=None,
                           image_ref=None, measurement_method_version=None):
    if type(modules) is not dict:
        raise ValueError("INVALID_TEST_INVENTORY")
    module_counts = {}
    for name, count in modules.items():
        if (type(name) is not str or not name or type(count) is not int
                or type(count) is bool or count <= 0):
            raise ValueError("INVALID_TEST_INVENTORY")
        module_counts[name] = count
    if history is None and history_digest is not None:
        raise ValueError("TIMING_HISTORY_REQUIRED")
    expected = {
        "runner_profile": runner_profile,
        "source_sha": source_sha,
        "python_version": python_version,
        "image_ref": image_ref,
        "measurement_method_version": measurement_method_version,
    }
    if history is not None:
        if any(expected[name] is None for name in _CONTEXT_NAMES):
            raise ValueError("TIMING_HISTORY_CONTEXT_REQUIRED")
        if any(not _valid_history_context(name, expected[name])
               for name in _CONTEXT_NAMES):
            raise ValueError("TIMING_HISTORY_CONTEXT_INVALID")
    history_value = None
    digest = None
    reason = "HISTORY_MISSING" if history is None else None
    if history is not None:
        history_value, digest, reason = _load_timing_history(history, history_digest)
    rows = _history_rows(history_value)
    valid = [
        row for row in rows
        if row["success"] is True
        and all(row["context"][name] == expected[name] for name in _CONTEXT_NAMES)
    ]
    grouped = {}
    for row in valid:
        grouped.setdefault(row["module"], {}).setdefault(row["run_id"], []).append(row)
    selected = {}
    for name, runs in grouped.items():
        # 同一 module/run_id の再配送は一実行として扱う。
        unique = [max(values, key=lambda row: (row["observed_at"], row["sequence"]))
                  for values in runs.values()]
        unique.sort(key=lambda row: (row["observed_at"], row["sequence"]))
        selected[name] = unique[-3:]
    estimates = {}
    module_elapsed_medians = []
    module_count_medians = []
    for name, values in selected.items():
        elapsed_median = _timing_median([row["elapsed_ns"] for row in values])
        count_median = _timing_median([row["test_count"] for row in values])
        if elapsed_median is None or count_median is None or count_median <= 0:
            continue
        module_elapsed_medians.append(elapsed_median)
        module_count_medians.append(count_median)
        if name in module_counts:
            estimates[name] = _timing_ceil(elapsed_median)
    per_test = MIN_MODULE_TIME_NS
    if module_elapsed_medians:
        elapsed_baseline = _timing_median(module_elapsed_medians)
        count_baseline = _timing_median(module_count_medians)
        per_test = max(
            MIN_MODULE_TIME_NS,
            _timing_ceil(elapsed_baseline, count_baseline),
        )
    fallback_modules = []
    for name, count in module_counts.items():
        if name not in estimates:
            estimates[name] = per_test * count
            fallback_modules.append(name)
    selected_count = sum(len(values) for values in selected.values())
    if history_value is not None and not selected_count:
        reason = "OBSERVATION_MISSING"
    used_history = history_value is not None and bool(selected_count)
    return {
        "algorithm_version": TIMING_ALGORITHM_VERSION,
        "history_digest": digest,
        "history_status": "history" if used_history else "fallback",
        "history_reason": reason,
        "history_observation_count": len(rows),
        "history_valid_observation_count": selected_count,
        "fallback_count": len(fallback_modules),
        "fallback_modules": sorted(fallback_modules),
        "uncertainty": {
            "fallback_modules": sorted(fallback_modules),
            "per_test_fallback_ns": per_test,
        },
        "module_estimates_ns": dict(sorted(estimates.items())),
        "observed_modules": sorted(name for name in estimates
                                  if name in module_counts and name not in fallback_modules),
    }


def _timed_partition(suite, *, dedicated, count, timing_history, timing_history_digest,
                     now=None, runner_profile=None, source_sha=None,
                     python_version=None, image_ref=None,
                     measurement_method_version=None):
    if timing_history is None and timing_history_digest is not None:
        raise ValueError("TIMING_HISTORY_REQUIRED")
    if timing_history is not None:
        context = {
            "runner_profile": runner_profile,
            "python_version": python_version,
            "image_ref": image_ref,
            "measurement_method_version": measurement_method_version,
        }
        if any(value is None for value in context.values()):
            raise ValueError("TIMING_HISTORY_CONTEXT_REQUIRED")
    if type(count) is not int or not 1 <= count <= 32:
        raise ValueError("INVALID_SHARD_COUNT")
    modules = {}
    ids = []
    for case in cases(suite):
        module = type(case).__module__.removeprefix("tests.")
        modules.setdefault(module, []).append(case)
        ids.append(case.id())
    if not ids or any(number != 1 for number in Counter(ids).values()):
        raise ValueError("EMPTY_OR_DUPLICATE_TEST_INVENTORY")
    lanes = {}
    reserved = set()
    for lane, module in dedicated:
        if lane in lanes or module in reserved or module not in modules:
            raise ValueError("INVALID_DEDICATED_MODULE")
        lanes[lane] = modules[module]
        reserved.add(module)
    names = [name for name in modules if name not in reserved]
    timing = resolve_timing_history(
        {name: len(items) for name, items in modules.items()},
        timing_history,
        history_digest=timing_history_digest,
        now=now,
        runner_profile=runner_profile,
        source_sha=source_sha,
        python_version=python_version,
        image_ref=image_ref,
        measurement_method_version=measurement_method_version,
    )
    estimates = timing["module_estimates_ns"]
    names.sort(key=lambda name: (-estimates[name], name))
    general = [[] for _ in range(min(count, len(names)))]
    weights = [0] * len(general)
    for name in names:
        index = min(range(len(general)), key=lambda i: (weights[i], f"regression-{i}"))
        general[index].extend(modules[name])
        weights[index] += estimates[name]
    for index, group in enumerate(general):
        lane = f"regression-{index}"
        if lane in lanes:
            raise ValueError("DUPLICATE_LANE")
        lanes[lane] = group
    planned = Counter(case.id() for group in lanes.values() for case in group)
    if planned != Counter(ids):
        raise ValueError("INCOMPLETE_TEST_PARTITION")
    return lanes, timing


def partition(suite, *, dedicated=DEDICATED, count=GENERAL_SHARDS,
             timing_history=None, timing_history_digest=None, now=None,
             runner_profile=None, source_sha=None, python_version=None,
             image_ref=None, measurement_method_version=None):
    if timing_history is None and timing_history_digest is None:
        return _legacy_partition(suite, dedicated=dedicated, count=count)
    return _timed_partition(
        suite, dedicated=dedicated, count=count, timing_history=timing_history,
        timing_history_digest=timing_history_digest, now=now,
        runner_profile=runner_profile, source_sha=source_sha,
        python_version=python_version, image_ref=image_ref,
        measurement_method_version=measurement_method_version,
    )[0]


def partition_timed(suite, *, dedicated=DEDICATED, count=GENERAL_SHARDS,
                    timing_history, timing_history_digest=None, now=None,
                    runner_profile=None, source_sha=None, python_version=None,
                    image_ref=None, measurement_method_version=None):
    return _timed_partition(
        suite, dedicated=dedicated, count=count, timing_history=timing_history,
        timing_history_digest=timing_history_digest, now=now,
        runner_profile=runner_profile, source_sha=source_sha,
        python_version=python_version, image_ref=image_ref,
        measurement_method_version=measurement_method_version,
    )


def describe(lanes, *, timing=None):
    inventory = {lane: [case.id() for case in group] for lane, group in lanes.items()}
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True).encode()).hexdigest()
    result = {
        "matrix": {
            "include": [
                {"lane": lane, "tests": len(group)}
                for lane, group in lanes.items()
            ],
        },
        "plan_digest": digest,
        "total_tests": sum(map(len, lanes.values())),
    }
    if timing is not None:
        result["timing"] = {
            "algorithm_version": timing["algorithm_version"],
            "history_digest": timing["history_digest"],
            "history_status": timing["history_status"],
            "history_observation_count": timing["history_observation_count"],
            "history_valid_observation_count": timing["history_valid_observation_count"],
            "fallback_count": timing["fallback_count"],
            "fallback_modules": timing["fallback_modules"],
            "uncertainty": timing["uncertainty"],
            "module_estimates_ns": timing["module_estimates_ns"],
        }
        if timing["history_reason"] is not None:
            result["timing"]["history_reason"] = timing["history_reason"]
    return result


def _validate_measurement_profile(value):
    if type(value) is not dict or set(value) != set(_CONTEXT_NAMES):
        raise ValueError("INVALID_TIMING_MEASUREMENT_PROFILE")
    if any(not _valid_history_context(name, value[name])
           for name in _CONTEXT_NAMES):
        raise ValueError("INVALID_TIMING_MEASUREMENT_PROFILE")
    return value


def _complete_result(result, planned_count):
    """全予定testが実際に成功した場合だけlaneを成功にする。"""
    return (
        result.wasSuccessful()
        and result.testsRun == planned_count
        and not result.skipped
        and not result.expectedFailures
        and not result.unexpectedSuccesses
    )


def _execute_timing_suite(suite, *, profile, source_sha, run_id,
                          observed_at=None):
    """全moduleを一度だけ実行し、履歴と全件結果を同時に返す。"""
    _validate_measurement_profile(profile)
    if not _valid_source_sha(source_sha) or not _history_text(run_id):
        raise ValueError("INVALID_TIMING_HISTORY_CONTEXT")
    if observed_at is None:
        observed_at = int(time.time())
    if (type(observed_at) is not int or type(observed_at) is bool
            or not 0 <= observed_at <= 2**53 - 1):
        raise ValueError("INVALID_TIMING_TIMESTAMP")
    modules = {}
    for case in cases(suite):
        module = type(case).__module__.removeprefix("tests.")
        modules.setdefault(module, []).append(case)
    observations = []
    failed_modules = []
    tests_run = 0
    planned_tests = sum(len(group) for group in modules.values())
    for module in sorted(modules):
        group = modules[module]
        started = time.perf_counter_ns()
        result = unittest.TextTestRunner(
            stream=sys.stderr, verbosity=2, buffer=False
        ).run(unittest.TestSuite(group))
        elapsed_ns = time.perf_counter_ns() - started
        tests_run += result.testsRun
        complete = _complete_result(result, len(group))
        if complete:
            observations.append({
                "module": module,
                "elapsed_ns": elapsed_ns,
                "test_count": len(group),
                "observed_at": observed_at,
                "success": True,
                "source_sha": source_sha,
                "run_id": run_id,
            })
        else:
            failed_modules.append(module)
    history = {
        "schema_version": 1,
        "kind": "ci_timing_history",
        "algorithm_version": TIMING_ALGORITHM_VERSION,
        **profile,
        "observations": observations,
    }
    history = _validate_history(history)
    return history, {
        "planned_tests": planned_tests,
        "tests_run": tests_run,
        "passed": not failed_modules and tests_run == planned_tests,
        "failed_modules": failed_modules,
    }


def collect_timing_history(suite, *, runner_profile, python_version,
                           image_ref, measurement_method_version,
                           source_sha, run_id, observed_at=None):
    """suite を module ごとに一度実行し、成功観測だけを返す。"""
    profile = {
        "runner_profile": runner_profile,
        "python_version": python_version,
        "image_ref": image_ref,
        "measurement_method_version": measurement_method_version,
    }
    history, _ = _execute_timing_suite(
        suite, profile=profile, source_sha=source_sha, run_id=run_id,
        observed_at=observed_at,
    )
    return history


def write_timing_history(path, history):
    """履歴を canonical JSON で保存し、同内容の再配送だけを許可する。"""
    _validate_history(history)
    raw = _history_canonical(history)
    target = Path(path)
    if target.is_symlink():
        raise ValueError("UNSAFE_TIMING_OUTPUT_PATH")
    try:
        with target.open("xb") as stream:
            stream.write(raw)
    except FileExistsError:
        try:
            if target.is_symlink() or target.read_bytes() != raw:
                raise ValueError("TIMING_HISTORY_OUTPUT_CONFLICT")
        except OSError as error:
            raise ValueError("TIMING_HISTORY_OUTPUT_ERROR") from error
    except OSError as error:
        raise ValueError("TIMING_HISTORY_OUTPUT_ERROR") from error
    return timing_history_digest(history)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true")
    mode.add_argument("--lane")
    parser.add_argument("--expected-plan-digest")
    parser.add_argument("--timing-history")
    parser.add_argument("--timing-history-digest")
    parser.add_argument("--timing-output")
    parser.add_argument("--timing-run-id")
    parser.add_argument("--timing-measurement-profile")
    parser.add_argument("--runner-profile")
    parser.add_argument("--source-sha")
    parser.add_argument("--python-version")
    parser.add_argument("--image-ref")
    parser.add_argument("--measurement-method-version")
    args = parser.parse_args(argv)
    loader = unittest.TestLoader()
    suite = loader.discover("tests")
    if loader.errors:
        for error in loader.errors:
            print(error, file=sys.stderr)
        return 2

    def parse_image(raw):
        if raw is None:
            return None
        try:
            value = json.loads(raw)
        except (TypeError, ValueError, UnicodeError) as error:
            raise ValueError("INVALID_IMAGE_REF") from error
        if not _valid_history_context("image_ref", value):
            raise ValueError("INVALID_IMAGE_REF")
        return value

    def parse_measurement_profile(raw, fallback):
        if raw is None:
            return _validate_measurement_profile(fallback)
        try:
            candidate = Path(raw)
            if candidate.is_file():
                value = json.loads(
                    candidate.read_bytes().decode("utf-8"),
                    object_pairs_hook=_history_pairs,
                    parse_float=_history_float,
                    parse_constant=_history_float,
                )
            else:
                value = json.loads(
                    raw,
                    object_pairs_hook=_history_pairs,
                    parse_float=_history_float,
                    parse_constant=_history_float,
                )
        except (OSError, TypeError, ValueError, UnicodeError, RecursionError) as error:
            raise ValueError("INVALID_TIMING_MEASUREMENT_PROFILE") from error
        return _validate_measurement_profile(value)

    try:
        history = args.timing_history
        history_digest = args.timing_history_digest
        plan_image_ref = parse_image(args.image_ref) if (
            history is not None or history_digest is not None
            or args.timing_output is not None
        ) else None
        plan_profile = {
            "runner_profile": args.runner_profile,
            "python_version": args.python_version,
            "image_ref": plan_image_ref,
            "measurement_method_version": args.measurement_method_version,
        }
        if args.timing_output is not None:
            if args.plan or args.lane is None:
                raise ValueError("TIMING_OUTPUT_LANE_REQUIRED")
            if history is None and history_digest is not None:
                raise ValueError("TIMING_HISTORY_REQUIRED")
            if history is None and history_digest is None:
                lanes, timing = _timed_partition(
                    suite, dedicated=DEDICATED, count=GENERAL_SHARDS,
                    timing_history=None, timing_history_digest=None,
                )
            else:
                lanes, timing = _timed_partition(
                    suite, dedicated=DEDICATED, count=GENERAL_SHARDS,
                    timing_history=history,
                    timing_history_digest=history_digest,
                    runner_profile=plan_profile["runner_profile"],
                    source_sha=args.source_sha,
                    python_version=plan_profile["python_version"],
                    image_ref=plan_profile["image_ref"],
                    measurement_method_version=plan_profile[
                        "measurement_method_version"
                    ],
                )
            plan = describe(lanes, timing=timing)
            if args.expected_plan_digest and args.expected_plan_digest != plan["plan_digest"]:
                raise ValueError("TEST_PLAN_CHANGED")
            if args.lane not in lanes:
                raise ValueError("UNKNOWN_TEST_LANE")
            measurement_profile = parse_measurement_profile(
                args.timing_measurement_profile, plan_profile
            )
            run_id = args.timing_run_id or uuid.uuid4().hex
            started = time.perf_counter()
            measured_history, execution = _execute_timing_suite(
                unittest.TestSuite(lanes[args.lane]),
                profile=measurement_profile,
                source_sha=args.source_sha,
                run_id=run_id,
            )
            output_digest = write_timing_history(
                args.timing_output, measured_history
            )
            print(json.dumps({
                "lane": args.lane,
                "planned_tests": execution["planned_tests"],
                "tests_run": execution["tests_run"],
                "passed": execution["passed"],
                "failed_modules": execution["failed_modules"],
                "seconds": round(time.perf_counter() - started, 3),
                "plan_digest": plan["plan_digest"],
                "timing_history_digest": output_digest,
            }), flush=True)
            return 0 if execution["passed"] else 1

        if history is None and history_digest is None:
            lanes = partition(suite)
            plan = describe(lanes)
        else:
            lanes, timing = _timed_partition(
                suite, dedicated=DEDICATED, count=GENERAL_SHARDS,
                timing_history=history,
                timing_history_digest=history_digest,
                runner_profile=plan_profile["runner_profile"],
                source_sha=args.source_sha,
                python_version=plan_profile["python_version"],
                image_ref=plan_profile["image_ref"],
                measurement_method_version=plan_profile[
                    "measurement_method_version"
                ],
            )
            plan = describe(lanes, timing=timing)
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
    passed = _complete_result(result, len(selected))
    print(json.dumps({
        "lane": args.lane, "planned_tests": len(selected),
        "tests_run": result.testsRun, "passed": passed,
        "failures": len(result.failures), "errors": len(result.errors),
        "skipped": len(result.skipped),
        "expected_failures": len(result.expectedFailures),
        "unexpected_successes": len(result.unexpectedSuccesses),
        "seconds": round(time.perf_counter() - started, 3),
        "plan_digest": plan["plan_digest"],
    }), flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
