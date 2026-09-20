"""性能manifestと厳密な測定値計算の契約。

このmoduleは性能補助操作の入力境界を閉じ、未取得値を0へ補完しない。
外部runner、任意shell、任意import、採択権限はここから提供しない。
"""
from __future__ import annotations

from fractions import Fraction
import re
from typing import Any, Iterable

from .contracts import (
    ContractError,
    MAX_INTEGER,
    decode_document,
    require_digest,
    require_id,
    require_object,
    require_ref,
    require_text,
)
from .wire import canonical_bytes


MANIFEST_FIELDS = frozenset({
    "measurement_surface", "baseline_source_ref", "candidate_source_ref",
    "input_ref", "expected_ref", "resource_profile_ref", "contract_ref",
    "baseline_ref", "target_refs", "os", "kernel", "cpu_model",
    "python_version", "docker_version", "vcpus_count", "parallelism_count",
    "concurrency_count", "ram_bytes", "storage_free_bytes", "image_ref",
    "storage_profile_ref", "clock_monotonic_ok", "clock_wall_ok",
    "case_count", "variant_count", "planned_trial_count",
    "planned_stage_count", "planned_test_count", "history_run_count",
    "page_size_count", "work_amount", "work_set_refs",
    "cold_iterations_count", "warm_iterations_count",
    "warmup_iterations_count", "broker_rss_limit_bytes",
    "cache_hard_limit_bytes", "cache_entry_cap_count", "cache_entry_max_bytes",
    "source_sha", "plan_digest", "lane_set", "intervals",
    "observation_count", "observation_segments", "observation_digest",
})
WHOLE_RUN_MANIFEST_FIELDS = frozenset({
    "run_request_ref", "ci_request_ref",
})
OBSERVATION_FIELDS = frozenset({
    "iteration", "warmness", "wall_ns", "cpu_ns", "target_wait_ns",
    "harness_wall_ns", "rss_group_peak_bytes", "io_read_bytes",
    "io_write_bytes", "copy_count", "serialize_bytes", "hash_count",
    "db_scan_count", "authority_call_count", "stored_bytes", "retry_count",
    "failure_class", "operation_status", "exit_code", "valid_for_slo",
})
MEASUREMENT_SURFACES = frozenset({
    "unit", "normal_run", "initial_preparation", "quickstart_total",
    "query", "ci", "fault_reproduction",
})
WARMNESSES = frozenset({"cold", "warm"})
FAILURE_CLASSES = frozenset({
    "NONE", "INPUT_REJECTED", "AUTHORITY_REJECTED", "STALE_OR_INVALIDATED",
    "CI_PLAN_MISMATCH", "RESOURCE_LIMIT", "TIMEOUT", "IO_OR_STORAGE",
    "DB_INTEGRITY", "TARGET_ERROR", "HARNESS_ERROR",
    "SERIALIZATION_ERROR", "CANCELLED_STOP_CONFIRMED",
    "CANCELLED_STOP_UNCONFIRMED", "MEASUREMENT_ERROR",
    "UNSUPPORTED_ENVIRONMENT",
})
OPERATION_STATUSES = frozenset({"COMPLETED", "REJECTED", "INCOMPLETE", "CANCELLED"})
OPERATION_EXIT_CODES = {
    "COMPLETED": 0, "REJECTED": 1, "INCOMPLETE": 2, "CANCELLED": 3,
}
RECIPES = frozenset({"candidate", "current_ci", "report", "whole_run"})
RECIPE_ACTIONS = {
    "candidate": "contract_candidate_read",
    "current_ci": "ci_check",
    "report": "build_report",
    "whole_run": "supervised_run",
}
RECIPE_SURFACES = {
    "candidate": frozenset({"query"}),
    "current_ci": frozenset({"query"}),
    "report": frozenset({"query"}),
    "whole_run": frozenset({"normal_run"}),
}
INTERVAL_EVENTS = {
    "unit": ("input_validation_complete", "output_serialized_digest_verified"),
    "initial_preparation": ("clean_workspace_request", "image_runtime_broker_ready"),
    "normal_run": ("run_prepare_requested", "fresh_ci_check_completed"),
    "quickstart_total": ("quickstart_started", "sample_evaluation_ci_report_completed"),
    "query": ("query_input_decoded", "response_ref_digest_verified"),
    "ci": ("push_event", "gate_confirmed"),
    "fault_reproduction": ("operation_started", "failure_recorded"),
}
SOURCE_SHA_RE = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")
SECOND_NS = 1_000_000_000


def _checked(value: Any) -> dict:
    try:
        checked = decode_document(canonical_bytes(value))
    except (ContractError, TypeError, ValueError, UnicodeError, RecursionError):
        raise ContractError("INVALID_INPUT") from None
    if type(checked) is not dict:
        raise ContractError("INVALID_INPUT")
    return checked


def _uint(value: Any, *, positive: bool = False) -> None:
    if type(value) is not int or not 0 <= value <= MAX_INTEGER:
        raise ContractError("INVALID_INPUT")
    if positive and value == 0:
        raise ContractError("INVALID_INPUT")


def _text(value: Any) -> None:
    try:
        require_text(value)
    except ContractError:
        raise ContractError("INVALID_INPUT") from None


def _ref(value: Any, *, kind: str | None = None) -> None:
    try:
        require_ref(value)
    except ContractError:
        raise ContractError("INVALID_INPUT") from None
    if kind is not None and value["kind"] != kind:
        raise ContractError("BINDING_MISMATCH")


def _source_sha(value: Any) -> None:
    if type(value) is not str or not SOURCE_SHA_RE.fullmatch(value):
        raise ContractError("INVALID_INPUT")


def _digest(value: Any) -> None:
    try:
        require_digest(value)
    except ContractError:
        raise ContractError("INVALID_INPUT") from None


def _unique_refs(value: Any, *, minimum: int = 0, maximum: int = 1000,
                 kind: str | None = None) -> None:
    if type(value) is not list or not minimum <= len(value) <= maximum:
        raise ContractError("INVALID_INPUT")
    seen: set[tuple[str, str, str]] = set()
    for item in value:
        _ref(item, kind=kind)
        key = (item["kind"], item["id"], item["digest"])
        if key in seen:
            raise ContractError("INVALID_INPUT")
        seen.add(key)


def _validate_work_amount(value: Any) -> None:
    axes = ("cases", "variants", "trials", "stages")
    fields = {"planned_count", "observed_count", "coefficient", "id_set_ref"}
    if type(value) is not dict or set(value) != set(axes):
        raise ContractError("INVALID_INPUT")
    for axis in axes:
        item = value[axis]
        if type(item) is not dict or set(item) != fields:
            raise ContractError("INVALID_INPUT")
        _uint(item["planned_count"])
        _uint(item["observed_count"])
        coefficient = item["coefficient"]
        if (type(coefficient) is not dict
                or set(coefficient) != {"numerator", "denominator"}):
            raise ContractError("INVALID_INPUT")
        _uint(coefficient["numerator"])
        if type(coefficient["denominator"]) is not int or not 1 <= coefficient["denominator"] <= MAX_INTEGER:
            raise ContractError("INVALID_INPUT")
        _ref(item["id_set_ref"])


def _validate_intervals(value: Any, surface: str) -> None:
    if type(value) is not dict or set(value) != {"start_event", "end_event"}:
        raise ContractError("INVALID_INPUT")
    expected = INTERVAL_EVENTS[surface]
    if value["start_event"] != expected[0] or value["end_event"] != expected[1]:
        raise ContractError("INVALID_INPUT")


def validate_manifest(value: dict) -> dict:
    """性能manifest payloadを未知fieldなしで検査する。"""
    value = _checked(value)
    surface = (
        value.get("measurement_surface")
        if type(value) is dict else None
    )
    allowed_fields = set(MANIFEST_FIELDS)
    if surface == "normal_run":
        allowed_fields.update(WHOLE_RUN_MANIFEST_FIELDS)
    require_object(value, allowed_fields)
    surface = value["measurement_surface"]
    if surface not in MEASUREMENT_SURFACES:
        raise ContractError("INVALID_INPUT")
    _ref(value["baseline_source_ref"])
    _ref(value["candidate_source_ref"])
    if value["baseline_source_ref"] == value["candidate_source_ref"]:
        raise ContractError("BINDING_MISMATCH")
    for name in (
        "input_ref", "expected_ref", "resource_profile_ref",
        "contract_ref", "baseline_ref", "image_ref", "storage_profile_ref",
    ):
        _ref(value[name])
    if surface == "normal_run":
        _ref(value["run_request_ref"], kind="run_request")
        _ref(value["ci_request_ref"], kind="ci_request")
    _unique_refs(value["target_refs"], minimum=1)
    for name in ("os", "kernel", "cpu_model", "python_version", "docker_version"):
        _text(value[name])
    for name in ("vcpus_count", "parallelism_count", "concurrency_count"):
        _uint(value[name], positive=True)
    for name in ("ram_bytes", "storage_free_bytes"):
        _uint(value[name], positive=True)
    if type(value["clock_monotonic_ok"]) is not bool or type(value["clock_wall_ok"]) is not bool:
        raise ContractError("INVALID_INPUT")
    for name in (
        "case_count", "variant_count", "planned_trial_count",
        "planned_stage_count", "planned_test_count", "history_run_count",
        "cold_iterations_count", "warm_iterations_count",
        "warmup_iterations_count", "broker_rss_limit_bytes",
        "cache_hard_limit_bytes", "cache_entry_cap_count",
        "cache_entry_max_bytes",
    ):
        _uint(value[name])
    if type(value["page_size_count"]) is not int or not 1 <= value["page_size_count"] <= 100:
        raise ContractError("INVALID_INPUT")
    _validate_work_amount(value["work_amount"])
    work_refs = value["work_set_refs"]
    if type(work_refs) is not dict or set(work_refs) != {"cases", "variants", "trials", "stages"}:
        raise ContractError("INVALID_INPUT")
    for item in work_refs.values():
        _ref(item)
    _source_sha(value["source_sha"])
    _digest(value["plan_digest"])
    lanes = value["lane_set"]
    if (type(lanes) is not list or not lanes or len(lanes) > 100
            or any(type(item) is not str for item in lanes)
            or len(set(lanes)) != len(lanes)):
        raise ContractError("INVALID_INPUT")
    for item in lanes:
        require_id(item)
    _validate_intervals(value["intervals"], surface)
    _uint(value["observation_count"])
    _unique_refs(value["observation_segments"], maximum=1000, kind="measurement_segment")
    if value["observation_count"] == 0 and value["observation_segments"]:
        raise ContractError("INVALID_INPUT")
    if value["observation_count"] > 0 and not value["observation_segments"]:
        raise ContractError("INVALID_INPUT")
    _digest(value["observation_digest"])
    return value


def validate_whole_run_requests(
    plan: dict,
    run_request: dict,
    ci_request: dict,
) -> tuple[dict, dict, str]:
    """plan固定のrun/ci request本文を照合し、使用する固定runnerを返す。"""
    plan = validate_plan(
        plan, kind="benchmark_plan", payload_validator=validate_manifest,
        now=None,
    )
    manifest = plan["payload"]
    if manifest["measurement_surface"] != "normal_run":
        raise ContractError("UNSUPPORTED_CAPABILITY")
    try:
        from .supervised_run import validate_input
        from .regression_runs import validate_request
        run = validate_input(run_request)
        ci = validate_request(ci_request)
    except Exception:
        raise ContractError("INVALID_INPUT") from None
    if ci.get("action") != "ci_check":
        raise ContractError("INVALID_INPUT")
    if (
        content_ref("run_request", run["run_id"], run)
        != manifest["run_request_ref"]
        or content_ref("ci_request", ci["request_id"], ci)
        != manifest["ci_request_ref"]
        or run["run_id"] != ci["run_id"]
        or run["expected_contract_ref"] != manifest["contract_ref"]
        or ci["expected_manifest_ref"].get("id") != run["run_id"]
        or ci["expected_contract_ref"] != manifest["contract_ref"]
        or ci["expected_baseline_ref"] != manifest["baseline_ref"]
        or ci["expected_target_refs"] != manifest["target_refs"]
    ):
        raise ContractError("BINDING_MISMATCH")
    uses = ci["expected_use_cases"]
    if uses == ["UC-CI"]:
        runner_kind = "fixture"
    elif uses == ["UC-LLM"]:
        runner_kind = "guardrail"
    else:
        raise ContractError("BINDING_MISMATCH")
    return run, ci, runner_kind


def _nullable_uint(value: Any) -> None:
    if value is not None:
        _uint(value)


def validate_observation(value: dict) -> dict:
    """segment payload内の1観測を厳密に検査する。"""
    value = _checked(value)
    require_object(value, set(OBSERVATION_FIELDS))
    _uint(value["iteration"], positive=True)
    if value["warmness"] not in WARMNESSES:
        raise ContractError("INVALID_INPUT")
    for name in ("wall_ns", "cpu_ns", "rss_group_peak_bytes"):
        _nullable_uint(value[name])
    _uint(value["retry_count"])
    for name in (
        "target_wait_ns", "harness_wall_ns", "io_read_bytes",
        "io_write_bytes", "copy_count", "serialize_bytes", "hash_count",
        "db_scan_count", "authority_call_count", "stored_bytes",
    ):
        _nullable_uint(value[name])
    if value["failure_class"] not in FAILURE_CLASSES:
        raise ContractError("INVALID_INPUT")
    status = value["operation_status"]
    if status not in OPERATION_STATUSES:
        raise ContractError("INVALID_INPUT")
    if type(value["exit_code"]) is not int or value["exit_code"] != OPERATION_EXIT_CODES[status]:
        raise ContractError("INVALID_INPUT")
    if type(value["valid_for_slo"]) is not bool:
        raise ContractError("INVALID_INPUT")
    if value["failure_class"] == "NONE" and status != "COMPLETED":
        raise ContractError("INVALID_INPUT")
    if value["failure_class"] != "NONE" and value["valid_for_slo"]:
        raise ContractError("INVALID_INPUT")
    required = ("wall_ns", "cpu_ns", "rss_group_peak_bytes")
    if value["valid_for_slo"] and (
            status != "COMPLETED" or value["exit_code"] != 0
            or any(value[name] is None for name in required)
    ):
        raise ContractError("OBSERVATION_MISSING")
    return value


def _fraction(value: Any, *, allow_negative: bool = True) -> Fraction:
    if type(value) is not dict or set(value) != {"numerator", "denominator"}:
        raise ContractError("INVALID_INPUT")
    numerator = value["numerator"]
    denominator = value["denominator"]
    if type(numerator) is not int or not -MAX_INTEGER <= numerator <= MAX_INTEGER:
        raise ContractError("INVALID_INPUT")
    if type(denominator) is not int or not 1 <= denominator <= MAX_INTEGER:
        raise ContractError("INVALID_INPUT")
    if not allow_negative and numerator < 0:
        raise ContractError("INVALID_INPUT")
    return Fraction(numerator, denominator)


def _fraction_wire(value: Fraction | None) -> dict | None:
    if value is None:
        return None
    return {"numerator": value.numerator, "denominator": value.denominator}


def fraction_wire(value: Fraction | int) -> dict:
    """Fraction/intを保存可能な分子・分母へ変換する。"""
    if type(value) is int:
        value = Fraction(value)
    if not isinstance(value, Fraction):
        raise ContractError("INVALID_INPUT")
    return _fraction_wire(value)  # type: ignore[return-value]


def metric_summary(values: Iterable[int]) -> dict[str, Any]:
    """非空の観測値だけから中央値、p95、最大を計算する。"""
    try:
        rows = list(values)
    except (TypeError, ValueError):
        raise ContractError("INVALID_INPUT") from None
    if not rows or any(type(item) is not int or item < 0 or item > MAX_INTEGER for item in rows):
        raise ContractError("OBSERVATION_MISSING")
    ordered = sorted(rows)
    count = len(ordered)
    if count % 2:
        median = Fraction(ordered[count // 2])
    else:
        median = Fraction(ordered[count // 2 - 1] + ordered[count // 2], 2)
    index = (19 * count + 19) // 20 - 1
    return {
        "count": count,
        "median": median,
        "p95": ordered[index],
        "max": ordered[-1],
    }


def _fraction_input(value: Any) -> Fraction:
    if type(value) is int:
        return Fraction(value)
    if type(value) is Fraction:
        return value
    raise ContractError("INVALID_INPUT")


def ratio(candidate: int | Fraction, baseline: int | Fraction) -> Fraction | None:
    """baselineが正の場合だけcandidate/baselineを厳密Fractionで返す。"""
    candidate_value = _fraction_input(candidate)
    baseline_value = _fraction_input(baseline)
    if candidate_value < 0:
        raise ContractError("INVALID_INPUT")
    if baseline_value <= 0:
        return None
    return candidate_value / baseline_value


def improvement_percent(candidate: int | Fraction, baseline: int | Fraction) -> Fraction | None:
    value = ratio(candidate, baseline)
    return None if value is None else Fraction(100) * (1 - value)


import hashlib
import time
from typing import Callable, Mapping, Sequence

from .productization import content_ref, validate_operation_result, validate_plan


OBSERVATION_ARTIFACT_FIELDS = frozenset({
    "schema_version", "kind", "id", "plan_ref", "source_ref",
    "iteration_id", "payload",
})
MEASUREMENT_RECEIPT_FIELDS = frozenset({
    "schema_version", "kind", "id", "request_digest", "artifact_ref",
    "cleanup_confirmed", "operation_result",
})
RESULT_FIELDS = frozenset({
    "schema_version", "kind", "id", "plan_ref", "baseline_source_ref",
    "candidate_source_ref", "measurement_surface", "status",
    "baseline_metrics", "candidate_metrics", "ratios", "thresholds",
    "observation_count", "valid_observation_count", "failures",
    "condition_match", "slo_evidence", "ci_eligible", "reasons",
})
METRIC_FIELDS = frozenset({"count", "median", "p95", "max"})
RATIO_FIELDS = frozenset({"candidate_over_baseline", "improvement_percent"})
THRESHOLD_FIELDS = frozenset({
    "median_ns", "p95_ns", "max_ns", "baseline_ratio",
})
FAILURE_FIELDS = frozenset({
    "series", "index", "iteration_id", "failure_class",
    "operation_status", "exit_code", "valid_for_slo",
})
RESULT_REASONS = frozenset({
    "EVIDENCE_UNAVAILABLE", "OBSERVATION_MISSING", "SLO_FAILED",
})


class MeasurementUnavailable(ContractError):
    """固定recipeを実行するtrusted runtimeが渡されていない。"""


def validate_observation_artifact(value: dict) -> dict:
    value = _checked(value)
    require_object(value, set(OBSERVATION_ARTIFACT_FIELDS))
    _uint(value["schema_version"])
    if value["schema_version"] != 1 or value["kind"] != "benchmark_observation":
        raise ContractError("INVALID_INPUT")
    require_id(value["id"])
    _ref(value["plan_ref"])
    _ref(value["source_ref"])
    require_id(value["iteration_id"])
    validate_observation(value["payload"])
    return value


def validate_measurement_receipt(value: dict) -> dict:
    """保存済み観測と入力digestを結ぶ、journal回収用の完了receipt。"""
    value = _checked(value)
    require_object(value, set(MEASUREMENT_RECEIPT_FIELDS))
    _uint(value["schema_version"])
    if value["schema_version"] != 1 or value["kind"] != "benchmark_measurement_receipt":
        raise ContractError("INVALID_INPUT")
    require_id(value["id"])
    _digest(value["request_digest"])
    _ref(value["artifact_ref"], kind="benchmark_observation")
    if type(value["cleanup_confirmed"]) is not bool:
        raise ContractError("INVALID_INPUT")
    result = validate_operation_result(value["operation_result"])
    if result["command"] != "benchmark.measure":
        raise ContractError("BINDING_MISMATCH")
    if result["result_ref"] != value["artifact_ref"]:
        raise ContractError("BINDING_MISMATCH")
    if value["cleanup_confirmed"] is False and result["operation_status"] == "COMPLETED":
        raise ContractError("INVALID_INPUT")
    return value








def _validate_metric(value: Any) -> dict:
    if type(value) is not dict or set(value) != set(METRIC_FIELDS):
        raise ContractError("INVALID_INPUT")
    _uint(value["count"], positive=True)
    _fraction(value["median"], allow_negative=False)
    for name in ("p95", "max"):
        _uint(value[name])
    return value


def _validate_ratio(value: Any) -> None:
    if type(value) is not dict or set(value) != set(RATIO_FIELDS):
        raise ContractError("INVALID_INPUT")
    if value["candidate_over_baseline"] is not None:
        _fraction(value["candidate_over_baseline"], allow_negative=False)
    if value["improvement_percent"] is not None:
        _fraction(value["improvement_percent"], allow_negative=True)


def _validate_thresholds(value: Any) -> None:
    if type(value) is not dict or set(value) != set(THRESHOLD_FIELDS):
        raise ContractError("INVALID_INPUT")
    for name in ("median_ns", "p95_ns", "max_ns"):
        if value[name] is not None:
            _uint(value[name])
    if value["baseline_ratio"] is not None:
        _fraction(value["baseline_ratio"], allow_negative=False)


def _validate_failures(value: Any) -> None:
    if type(value) is not list or len(value) > 10000:
        raise ContractError("INVALID_INPUT")
    for item in value:
        if type(item) is not dict or set(item) != set(FAILURE_FIELDS):
            raise ContractError("INVALID_INPUT")
        if item["series"] not in {"baseline", "candidate"}:
            raise ContractError("INVALID_INPUT")
        _uint(item["index"])
        require_id(item["iteration_id"])
        if item["failure_class"] not in FAILURE_CLASSES:
            raise ContractError("INVALID_INPUT")
        status = item["operation_status"]
        if status not in OPERATION_STATUSES:
            raise ContractError("INVALID_INPUT")
        if type(item["exit_code"]) is not int or item["exit_code"] != OPERATION_EXIT_CODES[status]:
            raise ContractError("INVALID_INPUT")
        if type(item["valid_for_slo"]) is not bool:
            raise ContractError("INVALID_INPUT")


def validate_result(value: dict) -> dict:
    """比較artifactの厳密validator。"""
    value = _checked(value)
    require_object(value, set(RESULT_FIELDS))
    _uint(value["schema_version"])
    if value["schema_version"] != 1 or value["kind"] != "benchmark_result":
        raise ContractError("INVALID_INPUT")
    require_id(value["id"])
    _ref(value["plan_ref"])
    _ref(value["baseline_source_ref"])
    _ref(value["candidate_source_ref"])
    if value["baseline_source_ref"] == value["candidate_source_ref"]:
        raise ContractError("BINDING_MISMATCH")
    if value["measurement_surface"] not in MEASUREMENT_SURFACES:
        raise ContractError("INVALID_INPUT")
    if value["status"] not in {"PASS", "FAIL", "INCONCLUSIVE"}:
        raise ContractError("INVALID_INPUT")
    if value["baseline_metrics"] is not None:
        _validate_metric(value["baseline_metrics"])
    if value["candidate_metrics"] is not None:
        _validate_metric(value["candidate_metrics"])
    _validate_ratio(value["ratios"])
    _validate_thresholds(value["thresholds"])
    _uint(value["observation_count"])
    _uint(value["valid_observation_count"])
    if value["valid_observation_count"] > value["observation_count"]:
        raise ContractError("INVALID_INPUT")
    _validate_failures(value["failures"])
    reasons = value["reasons"]
    if (type(reasons) is not list
            or len(reasons) != len(set(reasons))
            or any(type(item) is not str or item not in RESULT_REASONS for item in reasons)):
        raise ContractError("INVALID_INPUT")
    if type(value["condition_match"]) is not bool:
        raise ContractError("INVALID_INPUT")
    if type(value["slo_evidence"]) is not bool:
        raise ContractError("INVALID_INPUT")
    if value["slo_evidence"] and not value["condition_match"]:
        raise ContractError("INVALID_INPUT")
    # This artifact has one point-query stream, not the required independent
    # cold/warm, size, history and surface matrix. It cannot certify query SLOs.
    if value["measurement_surface"] == "query" and (
            value["status"] != "INCONCLUSIVE" or value["slo_evidence"]):
        raise ContractError("EVIDENCE_UNAVAILABLE")
    if value["status"] == "PASS" and (
            not value["condition_match"] or not value["slo_evidence"]
            or value["candidate_metrics"] is None or reasons):
        raise ContractError("INVALID_INPUT")
    if value["status"] != "PASS" and not reasons:
        raise ContractError("INVALID_INPUT")
    if value["status"] == "FAIL" and not value["condition_match"]:
        raise ContractError("INVALID_INPUT")
    if value["ci_eligible"] is not False:
        raise ContractError("INVALID_INPUT")
    return value


def _metric_wire(values: Iterable[int]) -> dict:
    summary = metric_summary(values)
    return {
        "count": summary["count"],
        "median": fraction_wire(summary["median"]),
        "p95": summary["p95"],
        "max": summary["max"],
    }


def _record_from_item(item: Any, *, plan_ref: dict, source_ref: dict) -> dict:
    if type(item) is dict and set(item) == set(OBSERVATION_ARTIFACT_FIELDS):
        validate_observation_artifact(item)
        if item["plan_ref"] != plan_ref or item["source_ref"] != source_ref:
            raise ContractError("BINDING_MISMATCH")
        return {
            "plan_ref": item["plan_ref"], "source_ref": item["source_ref"],
            "iteration_id": item["iteration_id"], "payload": item["payload"],
        }
    observation = validate_observation(item)
    return {
        "plan_ref": plan_ref, "source_ref": source_ref,
        "iteration_id": f"iteration-{observation['iteration']}",
        "payload": observation,
    }


def _records(items: Any, *, plan_ref: dict, source_ref: dict) -> list[dict]:
    if type(items) is not list or not items or len(items) > 1000:
        raise ContractError("OBSERVATION_MISSING")
    records = [
        _record_from_item(item, plan_ref=plan_ref, source_ref=source_ref)
        for item in items
    ]
    identifiers = [record["iteration_id"] for record in records]
    coordinates = [
        (record["payload"]["warmness"], record["payload"]["iteration"])
        for record in records
    ]
    if (
        len(set(identifiers)) != len(identifiers)
        or len(set(coordinates)) != len(coordinates)
    ):
        raise ContractError("OBSERVATION_MISSING")
    return records


def _usable(record: Mapping[str, Any]) -> bool:
    observation = record["payload"]
    return (
        observation["valid_for_slo"] is True
        and observation["failure_class"] == "NONE"
        and observation["operation_status"] == "COMPLETED"
        and observation["exit_code"] == 0
        and all(observation[name] is not None
                for name in ("wall_ns", "cpu_ns", "rss_group_peak_bytes"))
    )


def _failure_item(record: Mapping[str, Any], index: int, series: str) -> dict:
    observation = record["payload"]
    return {
        "series": series,
        "index": index,
        "iteration_id": record["iteration_id"],
        "failure_class": observation["failure_class"],
        "operation_status": observation["operation_status"],
        "exit_code": observation["exit_code"],
        "valid_for_slo": observation["valid_for_slo"],
    }


def _series_complete(records: Sequence[Mapping[str, Any]], manifest: Mapping[str, Any]) -> bool:
    expected = manifest["cold_iterations_count"] + manifest["warm_iterations_count"]
    if expected <= 0 or len(records) != expected:
        return False
    expected_counts = {
        "cold": manifest["cold_iterations_count"],
        "warm": manifest["warm_iterations_count"],
    }
    indices = {"cold": [], "warm": []}
    for record in records:
        payload = record["payload"]
        warmness = payload["warmness"]
        if warmness not in indices:
            return False
        indices[warmness].append(payload["iteration"])
    return all(
        sorted(indices[warmness]) == list(range(1, expected_counts[warmness] + 1))
        for warmness in ("cold", "warm")
    )


def _thresholds(surface: str) -> tuple[dict, dict[str, Fraction | int | None]]:
    values: dict[str, Fraction | int | None] = {
        "median_ns": None, "p95_ns": None, "max_ns": None,
        "baseline_ratio": None,
    }
    if surface == "query":
        values.update(p95_ns=SECOND_NS, max_ns=2 * SECOND_NS)
    elif surface in {"normal_run", "ci"}:
        values.update(
            median_ns=1800 * SECOND_NS,
            max_ns=2700 * SECOND_NS,
            baseline_ratio=Fraction(3, 5),
        )
    elif surface == "quickstart_total":
        values.update(median_ns=1800 * SECOND_NS)
    wire = {
        name: (_fraction_wire(item) if isinstance(item, Fraction) else item)
        for name, item in values.items()
    }
    return wire, values


def _slo_result(surface: str, baseline: Mapping[str, Any],
                candidate: Mapping[str, Any]) -> bool | None:
    if surface == "query":
        # A pooled point-query metric cannot establish the PR07 series SLO.
        return None
    if surface in {"normal_run", "ci"}:
        candidate_median = Fraction(
            candidate["median"]["numerator"], candidate["median"]["denominator"],
        )
        baseline_median = Fraction(
            baseline["median"]["numerator"], baseline["median"]["denominator"],
        )
        candidate_ratio = ratio(candidate_median, baseline_median)
        return (
            candidate_median <= 1800 * SECOND_NS
            and candidate["max"] <= 2700 * SECOND_NS
            and candidate_ratio is not None
            and candidate_ratio <= Fraction(3, 5)
        )
    if surface == "quickstart_total":
        median = Fraction(
            candidate["median"]["numerator"], candidate["median"]["denominator"],
        )
        return median <= 1800 * SECOND_NS
    return None


def compare_observations(
    plan: dict,
    baseline_observations: list[dict],
    candidate_observations: list[dict],
    *,
    evidence_confirmed: bool = False,
    result_id: str | None = None,
) -> dict:
    """基準/候補の生観測を比較する。

    evidence_confirmedは、trustedな保存層がsource・入力・時計・
    実観測を独立確認したときだけ指定する。既定では合成観測を
    実SLO PASSへ昇格しない。
    """
    if type(evidence_confirmed) is not bool:
        raise ContractError("INVALID_INPUT")
    plan = validate_plan(
        plan, kind="benchmark_plan", payload_validator=validate_manifest,
        now=None,
    )
    plan_ref = content_ref("benchmark_plan", plan["id"], plan)
    manifest = plan["payload"]
    baseline_ref = manifest["baseline_source_ref"]
    candidate_ref = manifest["candidate_source_ref"]
    baseline = _records(
        baseline_observations, plan_ref=plan_ref, source_ref=baseline_ref,
    )
    candidate = _records(
        candidate_observations, plan_ref=plan_ref, source_ref=candidate_ref,
    )
    failures: list[dict] = []
    baseline_values: list[int] = []
    candidate_values: list[int] = []
    for series, records, target in (
        ("baseline", baseline, baseline_values),
        ("candidate", candidate, candidate_values),
    ):
        for index, record in enumerate(records):
            if _usable(record):
                target.append(record["payload"]["wall_ns"])
            else:
                failures.append(_failure_item(record, index, series))
    complete = (
        _series_complete(baseline, manifest)
        and _series_complete(candidate, manifest)
        and not failures
    )
    baseline_metric = _metric_wire(baseline_values) if baseline_values else None
    candidate_metric = _metric_wire(candidate_values) if candidate_values else None
    baseline_median = (
        Fraction(
            baseline_metric["median"]["numerator"],
            baseline_metric["median"]["denominator"],
        )
        if baseline_metric is not None else None
    )
    candidate_median = (
        Fraction(
            candidate_metric["median"]["numerator"],
            candidate_metric["median"]["denominator"],
        )
        if candidate_metric is not None else None
    )
    candidate_ratio = (
        ratio(candidate_median, baseline_median)
        if candidate_median is not None and baseline_median is not None else None
    )
    improvement = (
        improvement_percent(candidate_median, baseline_median)
        if candidate_median is not None and baseline_median is not None else None
    )
    thresholds_wire, _ = _thresholds(manifest["measurement_surface"])
    if not complete or baseline_metric is None or candidate_metric is None:
        result_status = "INCONCLUSIVE"
        result_reasons = ["OBSERVATION_MISSING"]
    elif not evidence_confirmed or manifest["measurement_surface"] == "query":
        result_status = "INCONCLUSIVE"
        result_reasons = ["EVIDENCE_UNAVAILABLE"]
    else:
        decision = _slo_result(
            manifest["measurement_surface"], baseline_metric, candidate_metric,
        )
        result_status = (
            "PASS" if decision is True else "FAIL" if decision is False
            else "INCONCLUSIVE"
        )
        result_reasons = [] if decision is True else (
            ["SLO_FAILED"] if decision is False else ["OBSERVATION_MISSING"]
        )
    if result_id is None:
        result_id = "benchmark-result-" + hashlib.sha256(canonical_bytes({
            "plan": plan_ref, "baseline": baseline, "candidate": candidate,
        })).hexdigest()[:40]
    require_id(result_id)
    result = {
        "schema_version": 1,
        "kind": "benchmark_result",
        "id": result_id,
        "plan_ref": plan_ref,
        "baseline_source_ref": baseline_ref,
        "candidate_source_ref": candidate_ref,
        "measurement_surface": manifest["measurement_surface"],
        "status": result_status,
        "baseline_metrics": baseline_metric,
        "candidate_metrics": candidate_metric,
        "ratios": {
            "candidate_over_baseline": _fraction_wire(candidate_ratio),
            "improvement_percent": _fraction_wire(improvement),
        },
        "thresholds": thresholds_wire,
        "observation_count": len(baseline) + len(candidate),
        "valid_observation_count": len(baseline_values) + len(candidate_values),
        "failures": failures,
        "condition_match": True,
        "slo_evidence": bool(
            evidence_confirmed and complete and manifest["measurement_surface"] != "query"
        ),
        "ci_eligible": False,
        "reasons": result_reasons,
    }
    return validate_result(result)




def _clock_value(clock: Callable[[], Any]) -> int:
    try:
        value = clock()
    except Exception:
        raise MeasurementUnavailable("CLOCK_UNAVAILABLE") from None
    if type(value) is not int or not 0 <= value <= MAX_INTEGER:
        raise MeasurementUnavailable("CLOCK_UNAVAILABLE")
    return value


def _observation(
    *,
    iteration: int,
    warmness: str,
    wall_ns: int | None,
    cpu_ns: int | None,
    rss_bytes: int | None,
    failure_class: str,
    operation_status: str,
    valid_for_slo: bool,
) -> dict:
    return validate_observation({
        "iteration": iteration,
        "warmness": warmness,
        "wall_ns": wall_ns,
        "cpu_ns": cpu_ns,
        "target_wait_ns": None,
        "harness_wall_ns": None,
        "rss_group_peak_bytes": rss_bytes,
        "io_read_bytes": None,
        "io_write_bytes": None,
        "copy_count": None,
        "serialize_bytes": None,
        "hash_count": None,
        "db_scan_count": None,
        "authority_call_count": None,
        "stored_bytes": None,
        "retry_count": 0,
        "failure_class": failure_class,
        "operation_status": operation_status,
        "exit_code": OPERATION_EXIT_CODES[operation_status],
        "valid_for_slo": valid_for_slo,
    })


def _read_rss_sampler(sampler: Callable[[], Any] | None) -> int | None:
    if sampler is None:
        return None
    try:
        value = sampler()
    except Exception:
        return None
    if type(value) is int and 0 <= value <= MAX_INTEGER:
        return value
    return None


def measure_recipe(
    recipe: str,
    operation: Callable[[str, dict], Any],
    request: dict,
    *,
    iteration: int = 1,
    warmness: str = "cold",
    monotonic_clock: Callable[[], Any] | None = None,
    cpu_clock: Callable[[], Any] | None = None,
    rss_sampler: Callable[[], Any] | None = None,
) -> dict:
    """固定query recipeをtrusted callableで一度だけ実測する。

    callableは既存runtimeの候補照会、current CI照会、report構築の
    adapterだけが渡す。未取得のCPU/RSSはnullで保持し、観測をSLOから
   除外する。
    """
    if recipe not in RECIPES:
        raise ContractError("INVALID_INPUT")
    if not callable(operation):
        raise ContractError("UNSUPPORTED_CAPABILITY")
    if type(request) is not dict:
        raise ContractError("INVALID_INPUT")
    request = _checked(request)
    _uint(iteration, positive=True)
    if warmness not in WARMNESSES:
        raise ContractError("INVALID_INPUT")
    monotonic_clock = monotonic_clock or time.perf_counter_ns
    start_wall: int | None = None
    end_wall: int | None = None
    start_cpu: int | None = None
    end_cpu: int | None = None
    failure_class = "NONE"
    status = "COMPLETED"
    try:
        start_wall = _clock_value(monotonic_clock)
    except MeasurementUnavailable:
        return _observation(
            iteration=iteration, warmness=warmness, wall_ns=None, cpu_ns=None,
            rss_bytes=None, failure_class="MEASUREMENT_ERROR",
            operation_status="INCOMPLETE", valid_for_slo=False,
        )
    if cpu_clock is not None:
        try:
            start_cpu = _clock_value(cpu_clock)
        except MeasurementUnavailable:
            failure_class = "MEASUREMENT_ERROR"
            status = "INCOMPLETE"
    try:
        if failure_class == "NONE":
            returned = operation(RECIPE_ACTIONS[recipe], request)
            # adapterの応答は明示的な整数exit_codeを必須とする。
            # boolはintとの等値性を持つが、wire上の終了コードとしては拒否する。
            if (type(returned) is not dict
                    or type(returned.get("exit_code")) is not int
                    or returned["exit_code"] not in OPERATION_EXIT_CODES.values()):
                raise MeasurementUnavailable("TARGET_RESPONSE_INVALID")
            if returned["exit_code"] in {1, 2, 3}:
                status = {
                    1: "REJECTED", 2: "INCOMPLETE", 3: "CANCELLED",
                }[returned["exit_code"]]
                failure_class = {
                    "REJECTED": "TARGET_ERROR",
                    "INCOMPLETE": "TARGET_ERROR",
                    "CANCELLED": "CANCELLED_STOP_CONFIRMED",
                }[status]
    except Exception:
        failure_class = "TARGET_ERROR"
        status = "INCOMPLETE"
    try:
        end_wall = _clock_value(monotonic_clock)
    except MeasurementUnavailable:
        failure_class = "MEASUREMENT_ERROR"
        status = "INCOMPLETE"
    if cpu_clock is not None:
        try:
            end_cpu = _clock_value(cpu_clock)
        except MeasurementUnavailable:
            failure_class = "MEASUREMENT_ERROR"
            status = "INCOMPLETE"
    if start_wall is not None and end_wall is not None and end_wall < start_wall:
        failure_class = "MEASUREMENT_ERROR"
        status = "INCOMPLETE"
    if start_cpu is not None and end_cpu is not None and end_cpu < start_cpu:
        failure_class = "MEASUREMENT_ERROR"
        status = "INCOMPLETE"
    wall_ns = (
        None if start_wall is None or end_wall is None
        else end_wall - start_wall
    )
    cpu_ns = (
        None if start_cpu is None or end_cpu is None
        else end_cpu - start_cpu
    )
    if wall_ns is not None and wall_ns < 0:
        wall_ns = None
    if cpu_ns is not None and cpu_ns < 0:
        cpu_ns = None
    rss_bytes = _read_rss_sampler(rss_sampler)
    if cpu_ns is None or rss_bytes is None:
        if failure_class == "NONE":
            failure_class = "MEASUREMENT_ERROR"
            status = "INCOMPLETE"
    valid = (
        failure_class == "NONE"
        and status == "COMPLETED"
        and wall_ns is not None
        and cpu_ns is not None
        and rss_bytes is not None
    )
    return _observation(
        iteration=iteration, warmness=warmness, wall_ns=wall_ns,
        cpu_ns=cpu_ns, rss_bytes=rss_bytes, failure_class=failure_class,
        operation_status=status, valid_for_slo=valid,
    )


def measure_once(
    plan: dict,
    iteration_id: str,
    *,
    recipe: str,
    operation: Callable[[str, dict], Any] | None = None,
    request: dict | None = None,
    iteration: int = 1,
    warmness: str = "cold",
    monotonic_clock: Callable[[], Any] | None = None,
    cpu_clock: Callable[[], Any] | None = None,
    rss_sampler: Callable[[], Any] | None = None,
    now: int | None = None,
) -> dict:
    """planに結び付いた固定recipeをtrusted runtimeから一度だけ測る。"""
    plan = validate_plan(
        plan, kind="benchmark_plan", payload_validator=validate_manifest,
        now=now,
    )
    require_id(iteration_id)
    if recipe not in RECIPES:
        raise ContractError("INVALID_INPUT")
    if type(warmness) is not str or warmness not in WARMNESSES:
        raise ContractError("INVALID_INPUT")
    _uint(iteration, positive=True)
    planned_count = plan["payload"][warmness + "_iterations_count"]
    if iteration > planned_count:
        raise ContractError("INVALID_INPUT")
    if plan["payload"]["measurement_surface"] not in RECIPE_SURFACES[recipe]:
        raise ContractError("UNSUPPORTED_CAPABILITY")
    if operation is None or not callable(operation):
        raise MeasurementUnavailable("UNSUPPORTED_CAPABILITY")
    if request is None:
        request = {
            "iteration_id": iteration_id,
            "plan_ref": content_ref("benchmark_plan", plan["id"], plan),
        }
    if type(request) is not dict:
        raise ContractError("INVALID_INPUT")
    request = dict(request)
    request.setdefault("iteration_id", iteration_id)
    return measure_recipe(
        recipe, operation, request, iteration=iteration, warmness=warmness,
        monotonic_clock=monotonic_clock, cpu_clock=cpu_clock,
        rss_sampler=rss_sampler,
    )


def make_plan(
    payload: dict,
    *,
    identifier: str,
    source_ref: dict,
    requirements_ref: dict,
    created_at: int,
    expires_at: int,
    requirement_ids: Sequence[str] = (
        "GAH-PR05", "GAH-PR06", "GAH-PR07", "GAH-PR08", "GAH-PR13",
    ),
    now: int | None = None,
) -> dict:
    """閉じたmanifestを共通plan外枠へ詰める。採択状態は発行しない。"""
    validate_manifest(payload)
    plan = {
        "schema_version": 1,
        "kind": "benchmark_plan",
        "id": identifier,
        "requirement_ids": list(requirement_ids),
        "source_ref": source_ref,
        "requirements_ref": requirements_ref,
        "created_at": created_at,
        "expires_at": expires_at,
        "payload": payload,
    }
    return validate_plan(
        plan, kind="benchmark_plan", payload_validator=validate_manifest,
        now=now,
    )


def make_observation_artifact(
    plan: dict,
    source_ref: dict,
    iteration_id: str,
    observation: dict,
    *,
    identifier: str | None = None,
) -> dict:
    plan = validate_plan(
        plan, kind="benchmark_plan", payload_validator=validate_manifest,
        now=None,
    )
    _ref(source_ref)
    require_id(iteration_id)
    validate_observation(observation)
    plan_ref = content_ref("benchmark_plan", plan["id"], plan)
    if identifier is None:
        identifier = "benchmark-observation-" + hashlib.sha256(
            canonical_bytes({
                "plan_ref": plan_ref, "source_ref": source_ref,
                "iteration_id": iteration_id, "payload": observation,
            })
        ).hexdigest()[:40]
    require_id(identifier)
    return validate_observation_artifact({
        "schema_version": 1,
        "kind": "benchmark_observation",
        "id": identifier,
        "plan_ref": plan_ref,
        "source_ref": source_ref,
        "iteration_id": iteration_id,
        "payload": observation,
    })


def make_measurement_receipt(
    request_digest: str,
    artifact_ref: dict,
    operation_result: dict,
    cleanup_confirmed: bool,
    *,
    identifier: str | None = None,
) -> dict:
    """観測artifactをCLI入力digestとcleanup結果へ固定する。"""
    _digest(request_digest)
    _ref(artifact_ref, kind="benchmark_observation")
    result = validate_operation_result(operation_result)
    if result["command"] != "benchmark.measure" or result["result_ref"] != artifact_ref:
        raise ContractError("BINDING_MISMATCH")
    if type(cleanup_confirmed) is not bool:
        raise ContractError("INVALID_INPUT")
    if identifier is None:
        identifier = "benchmark-measurement-receipt-" + hashlib.sha256(
            canonical_bytes({
                "request_digest": request_digest,
                "artifact_ref": artifact_ref,
            })
        ).hexdigest()[:40]
    require_id(identifier)
    return validate_measurement_receipt({
        "schema_version": 1,
        "kind": "benchmark_measurement_receipt",
        "id": identifier,
        "request_digest": request_digest,
        "artifact_ref": artifact_ref,
        "cleanup_confirmed": cleanup_confirmed,
        "operation_result": result,
    })


def make_result_artifact(result: dict, *, identifier: str | None = None) -> dict:
    result = validate_result(result)
    if identifier is not None and identifier != result["id"]:
        raise ContractError("BINDING_MISMATCH")
    return result




__all__ = [
    "MANIFEST_FIELDS", "WHOLE_RUN_MANIFEST_FIELDS", "OBSERVATION_FIELDS", "MEASUREMENT_SURFACES",
    "WARMNESSES", "FAILURE_CLASSES", "OPERATION_STATUSES",
    "OPERATION_EXIT_CODES", "RECIPES", "RECIPE_ACTIONS", "RECIPE_SURFACES",
    "INTERVAL_EVENTS", "SECOND_NS", "validate_manifest",
    "validate_whole_run_requests",
    "validate_observation", "metric_summary", "ratio", "improvement_percent",
    "fraction_wire", "OBSERVATION_ARTIFACT_FIELDS",
    "MEASUREMENT_RECEIPT_FIELDS", "RESULT_FIELDS", "RESULT_REASONS",
    "validate_observation_artifact", "validate_measurement_receipt",
    "validate_result", "compare_observations",
    "MeasurementUnavailable", "measure_recipe", "measure_once",
    "make_plan", "make_observation_artifact", "make_measurement_receipt",
    "make_result_artifact",
]
