"""Bound run の attempt_record を決定的に集計する。

このモジュールは実行結果の採択や CI 合格を行わない。入力を再 binding し、
確定した観測だけを固定分母へ集計して、欠落・矛盾・禁止違反を証跡化する。
"""

from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
import hashlib
import json
from typing import Any

from .contracts import MAX_DOCUMENT_BYTES, MAX_INTEGER, ContractError, require_digest, require_id, require_object, require_uint
from . import execution_profiles
from .normalized import validate_binding
from .run_contracts import bind_run_manifest

MAX_ATTEMPTS = 20_000
MAX_ATTEMPT_BYTES = 1 * 1024 * 1024
_ATTEMPT_FIELDS = {"schema_version", "kind", "attempt_id", "variant", "retry_of",
                   "started_at", "finished_at", "stop_confirmed", "execution_status",
                   "state_restored", "expected_binding", "result"}
_PROFILE_FIELDS = {"fixture_digest", "adapter_digests", "isolation_digest"}
_RESULT_FIELDS = {"schema_version", "kind", "binding", "mode", "observation",
                  "mutation_outcome", "detection", "deviation", "error_class", "raw_digest"}
_STATUSES = {"COMPLETED", "FAILED", "TIMEOUT", "CANCELLED", "UNKNOWN"}
_VARIANTS = {"candidate", "baseline"}
_CHECKS = {"PASS", "FAIL", "UNKNOWN"}
_DETECTIONS = {"detect", "allow", "indeterminate"}
_MUTATIONS = {"KILLED", "SURVIVED", "NO_COVERAGE", "ERROR"}
_ERRORS = {"EXECUTION_FAILURE", "BASELINE_NOT_PASS", "UNRELATED_FAILURE",
           "MUTATION_NOT_APPLIED"}

def _bad(code: str = "INVALID_AGGREGATION") -> ContractError:
    return ContractError(code)

def _bool(value: Any) -> None:
    if type(value) is not bool:
        raise _bad()

def _enum(value: Any, allowed: set[str]) -> None:
    if type(value) is not str or value not in allowed:
        raise _bad()

def _digest(value: Any) -> None:
    try:
        require_digest(value)
    except ContractError:
        raise _bad() from None

def _canon(value: Any) -> bytes:
    try:
        pending = [(value, 0)]
        nodes = 0
        while pending:
            item, depth = pending.pop()
            nodes += 1
            if depth > 16 or nodes > 100_000:
                raise _bad("DOCUMENT_COMPLEXITY")
            if type(item) is dict:
                if any(type(k) is not str for k in item):
                    raise _bad()
                pending.extend((v, depth + 1) for v in item.values())
            elif type(item) is list:
                pending.extend((v, depth + 1) for v in item)
            elif type(item) is int:
                if not -MAX_INTEGER <= item <= MAX_INTEGER:
                    raise _bad("INTEGER_RANGE")
            elif item is not None and type(item) not in (str, bool):
                raise _bad()
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    except ContractError:
        raise
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise _bad() from None
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise _bad("DOCUMENT_SIZE")
    return raw

def _validate_profile(value: Any) -> dict[str, Any]:
    if type(value) is dict and "schema_version" in value:
        return execution_profiles.validate(value)
    require_object(value, _PROFILE_FIELDS)
    _digest(value["fixture_digest"])
    _digest(value["isolation_digest"])
    adapters = value["adapter_digests"]
    if type(adapters) is not list or not 1 <= len(adapters) <= 100:
        raise _bad()
    seen = set()
    for item in adapters:
        _digest(item)
        if item in seen:
            raise _bad("DUPLICATE_REFERENCE")
        seen.add(item)
    return deepcopy(value)

def _validate_result(value: Any) -> dict[str, Any]:
    require_object(value, _RESULT_FIELDS)
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise _bad("UNSUPPORTED_VERSION")
    if value["kind"] != "normalized_result":
        raise _bad()
    try:
        binding = validate_binding(value["binding"])
    except ContractError:
        raise _bad("BINDING_MISMATCH") from None
    mode = value["mode"]
    if mode is not None:
        _enum(mode, {"constraint", "mutation", "llm"})
    observation, mutation, detection = value["observation"], value["mutation_outcome"], value["detection"]
    deviation, error_class, raw_digest = value["deviation"], value["error_class"], value["raw_digest"]
    if raw_digest is not None:
        _digest(raw_digest)
    if deviation is not None:
        _bool(deviation)
    if mode is None:
        if observation is not None or detection is not None or deviation is not None or mutation != "ERROR":
            raise _bad()
        if type(error_class) is not str or error_class not in _ERRORS:
            raise _bad()
        if raw_digest is not None:
            raise _bad()
    elif mode == "constraint":
        if type(observation) is not str or observation not in _CHECKS or mutation is not None or detection is not None or deviation is not None or error_class is not None or raw_digest is None:
            raise _bad()
    elif mode == "mutation":
        if type(observation) is not str or observation not in _CHECKS or type(mutation) is not str or mutation not in _MUTATIONS or detection is not None or deviation is not None or raw_digest is None:
            raise _bad()
        if mutation == "ERROR":
            if type(error_class) is not str or error_class not in _ERRORS:
                raise _bad()
        elif error_class is not None:
            raise _bad()
    else:
        if observation is not None or mutation is not None or type(detection) is not str or detection not in _DETECTIONS or error_class is not None or raw_digest is None:
            raise _bad()
    # binding以外は検証済みscalar。bindingはvalidatorの独立値へ置き換える。
    result = dict(value)
    result["binding"] = binding
    return result

def _validate_attempt(value: Any) -> tuple[dict[str, Any], str]:
    require_object(value, _ATTEMPT_FIELDS)
    if type(value["schema_version"]) is not int or value["schema_version"] != 1 or value["kind"] != "attempt_record":
        raise _bad("UNSUPPORTED_VERSION" if value.get("schema_version") != 1 else "INVALID_AGGREGATION")
    try:
        require_id(value["attempt_id"])
    except ContractError:
        raise _bad() from None
    _enum(value["variant"], _VARIANTS)
    retry = value["retry_of"]
    if retry is not None:
        try:
            require_id(retry)
        except ContractError:
            raise _bad() from None
        if retry == value["attempt_id"]:
            raise _bad("INVALID_RETRY")
    try:
        require_uint(value["started_at"])
        if value["finished_at"] is not None:
            require_uint(value["finished_at"])
    except ContractError:
        raise _bad() from None
    if value["finished_at"] is not None and value["finished_at"] < value["started_at"]:
        raise _bad("TIME_ORDER")
    _bool(value["stop_confirmed"])
    _bool(value["state_restored"])
    _enum(value["execution_status"], _STATUSES)
    try:
        binding = validate_binding(value["expected_binding"])
    except ContractError:
        raise _bad("BINDING_MISMATCH") from None
    result = None if value["result"] is None else _validate_result(value["result"])
    # 入れ子二つはすでに検証・複製済みなので、もう一度複製して捨てない。
    normalized = dict(value)
    normalized["expected_binding"] = binding
    normalized["result"] = result
    raw = _canon(normalized)
    if len(raw) > MAX_ATTEMPT_BYTES:
        raise _bad("DOCUMENT_SIZE")
    return normalized, hashlib.sha256(raw).hexdigest()

def _rebind(bundle: dict[str, Any], baseline_context: Any) -> dict[str, Any]:
    required = {"manifest", "contract", "plan", "policy", "registry", "case_set", "selected_controls", "ci_eligible"}
    require_object(bundle, required)
    if bundle["ci_eligible"] is not False:
        raise _bad("CI_INELIGIBLE")
    try:
        rebound = bind_run_manifest(bundle["manifest"], bundle["contract"], bundle["plan"],
                                    bundle["policy"], bundle["registry"], bundle["case_set"],
                                    baseline_context=baseline_context)
    except ContractError:
        raise
    except (KeyError, TypeError, ValueError, RecursionError):
        raise _bad() from None
    for key in ("manifest", "contract", "plan", "policy", "registry", "case_set", "selected_controls"):
        if rebound[key] != bundle[key]:
            raise _bad("REBIND_MISMATCH")
    return rebound

def _issue(issues: list[dict[str, Any]], code: str, item: dict[str, Any] | None = None) -> None:
    value = {"code": code}
    if item is not None:
        for key in ("variant", "obligation_id", "case_id", "trial_id", "stage_id", "attempt_id"):
            if key in item:
                value[key] = item[key]
            elif isinstance(item.get("expected_binding"), dict) and key in item["expected_binding"]:
                value[key] = item["expected_binding"][key]
    if value not in issues:
        issues.append(value)

def _metric_id(scope: str, name: str) -> str:
    return "m-" + hashlib.sha256((scope + ":" + name).encode("utf-8")).hexdigest()[:24]

@lru_cache(maxsize=4)
def _completed_aggregate(source_digest, implementation, payload):
    bound, attempts, profile, baseline = json.loads(payload)
    return implementation(bound, attempts, execution_profile=profile, baseline_context=baseline)


def aggregate(bound_run: Any, attempts: Any, *, execution_profile: Any, baseline_context: Any = None) -> dict[str, Any]:
    """保存済み入力の純粋な集計を再現する。現在の有効性は上位が毎回検査する。"""
    try:
        cases = bound_run['case_set']['cases']
        entries = bound_run['plan']['entries']
        complete_size = sum(len(entry['stage_ids']) for entry in entries) if type(entries) is list and len(entries)<=1500 else 1501
        cacheable = (type(cases) is list and 400 <= len(cases) <= 415 and type(attempts) is list
                     and complete_size <= len(attempts) <= 1500)
    except (KeyError, TypeError):
        cacheable = False
    from .cache_inputs import plain
    if cacheable and plain([bound_run, attempts, execution_profile, baseline_context]):
        from .wire import canonical_bytes
        from .evaluation_authority import _source_digest
        try:
            payload = canonical_bytes([bound_run, attempts, execution_profile, baseline_context])
        except (TypeError, ValueError, RecursionError):
            payload = None
        if payload is not None and len(payload) <= 4 * MAX_DOCUMENT_BYTES:
            return deepcopy(_completed_aggregate(_source_digest(), _aggregate_uncached, payload))
    return _aggregate_uncached(bound_run, attempts, execution_profile=execution_profile, baseline_context=baseline_context)


def _aggregate_uncached(bound_run: Any, attempts: Any, *, execution_profile: Any, baseline_context: Any = None) -> dict[str, Any]:
    """Attempt records を再 binding し、未確定入力を成績へ混ぜず集計する。

    attempts は trusted runner が生成した構造化 record のみ受け付ける。
    raw output、未知 field、未定義の再試行は拒否する。返却 ci_eligible は
    常に false であり、ここでは認証・基準採択・通常CI判定を行わない。
    """
    bound = _rebind(bound_run, baseline_context)
    profile = _validate_profile(execution_profile)
    execution_profiles.check_plan(profile, bound)
    if type(attempts) is not list or len(attempts) > MAX_ATTEMPTS:
        raise _bad("ATTEMPT_LIMIT")
    unique: dict[str, tuple[dict[str, Any], str, int]] = {}
    conflicted_ids: set[str] = set()
    conflict_records: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    for value in attempts:
        record, digest = _validate_attempt(value)
        aid = record["attempt_id"]
        if aid in unique:
            old, old_digest, count = unique[aid]
            if old_digest != digest:
                _issue(issues, "DUPLICATE_ATTEMPT_CONFLICT", record)
                conflicted_ids.add(aid)
                conflict_records.append(old)
                conflict_records.append(record)
            else:
                unique[aid] = (old, old_digest, count + 1)
        else:
            unique[aid] = (record, digest, 1)

    for record, _, _ in unique.values():
        result = record["result"]
        if record["execution_status"] == "COMPLETED" and result is None:
            _issue(issues, "MISSING_RESULT", record)
        elif (record["execution_status"] != "COMPLETED" and result is not None
              and (result["mode"] is not None or result["mutation_outcome"] != "ERROR")):
            _issue(issues, "STATUS_RESULT_MISMATCH", record)

    manifest = bound["manifest"]
    plan = bound["plan"]["entries"]
    registry = bound["registry"]
    obligations: dict[str, tuple[str, dict[str, Any], dict[str, Any]]] = {}
    for control in registry["controls"]:
        if control["control_id"] not in bound["selected_controls"]:
            continue
        for obligation in control["obligations"]:
            obligations[obligation["obligation_id"]] = (control["control_id"], control, obligation)
    cases = {c["case_id"]: c for c in bound["case_set"]["cases"]}
    planned: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    logical: dict[tuple[str, str, str, str, str], list[tuple[dict[str, Any], int]]] = {}
    for entry in plan:
        for stage_id in entry["stage_ids"]:
            key = (entry["variant"], entry["obligation_id"], entry["case_id"], entry["trial_id"], stage_id)
            planned[key] = entry
    for record, _, deliveries in unique.values():
        if record["attempt_id"] in conflicted_ids:
            continue
        b = record["expected_binding"]
        if record["started_at"] < manifest["created_at"] or (
                record["finished_at"] is not None and record["finished_at"] > manifest["deadline"]):
            _issue(issues, "OUT_OF_RUN_WINDOW", record)
            continue
        key = (record["variant"], b["obligation_id"], b["case_id"], b["trial_id"], b["stage_id"])
        entry = planned.get(key)
        if entry is None:
            _issue(issues, "UNPLANNED_ATTEMPT", record)
            continue
        selected_profile = execution_profiles.expected(profile, entry["target_ref"]["digest"], entry["evaluator_ref"]["digest"])
        expected = {
            "run_id": manifest["run_id"], "contract_digest": manifest["contract_ref"]["digest"],
            "policy_digest": manifest["policy_ref"]["digest"], "fixture_digest": selected_profile["fixture_digest"],
            "isolation_digest": profile["isolation_digest"], "target_digest": entry["target_ref"]["digest"],
            "evaluator_digest": entry["evaluator_ref"]["digest"],
        }
        if any(b[field] != value for field, value in expected.items()):
            _issue(issues, "BINDING_MISMATCH", record)
            continue
        if b["adapter_digest"] not in selected_profile["adapter_digests"]:
            _issue(issues, "BINDING_MISMATCH", record)
            continue
        if record["result"] is not None and record["result"]["binding"] != b:
            _issue(issues, "RESULT_BINDING_MISMATCH", record)
            continue
        obligation_kind = obligations[b["obligation_id"]][2]["kind"]
        expected_mode = {"constraint": "constraint", "mutation": "mutation",
                         "llm_metric": "llm"}[obligation_kind]
        if (record["result"] is not None
                and record["result"]["mode"] != expected_mode
                and not (record["result"]["mode"] is None
                         and record["result"]["mutation_outcome"] == "ERROR")):
            _issue(issues, "RESULT_MODE_MISMATCH", record)
            continue
        logical.setdefault(key, []).append((record, deliveries))

    selected: dict[tuple[str, str, str, str, str], tuple[dict[str, Any], int]] = {}
    conflict_keys: set[tuple[str, str, str, str, str]] = set()
    by_id = {
        record["attempt_id"]: record
        for records in logical.values()
        for record, _ in records
    }
    key_by_id = {
        record["attempt_id"]: key
        for key, records in logical.items()
        for record, _ in records
    }
    for key, records in logical.items():
        valid = []
        for record, deliveries in records:
            if record["retry_of"] is not None:
                previous = by_id.get(record["retry_of"])
                if (previous is None or key_by_id.get(record["retry_of"]) != key
                        or previous["retry_of"] is not None
                        or previous["execution_status"] not in {"FAILED", "TIMEOUT"}
                        or not previous["stop_confirmed"] or previous["finished_at"] is None
                        or previous["finished_at"] > record["started_at"]
                        or previous["expected_binding"]["owner_epoch"] !=
                           record["expected_binding"]["owner_epoch"]
                        or not record["state_restored"]):
                    _issue(issues, "INVALID_RETRY", record)
                    continue
                if sum(1 for r, _, _ in unique.values() if r["retry_of"] == record["retry_of"]) > 1:
                    _issue(issues, "RETRY_LIMIT", record)
                    continue
            if (record["execution_status"] == "COMPLETED"
                    and record["finished_at"] is not None and record["stop_confirmed"]
                    and record["result"] is not None
                    and record["result"]["mutation_outcome"] != "ERROR"):
                valid.append((record, deliveries))
        if len(valid) == 1:
            selected_record = valid[0][0]
            unrelated = [
                record for record, _ in records
                if record["attempt_id"] != selected_record["attempt_id"]
                and record["attempt_id"] != selected_record["retry_of"]
                and record["retry_of"] != selected_record["attempt_id"]
            ]
            if unrelated:
                _issue(issues, "ATTEMPT_CHAIN_CONFLICT", selected_record)
            else:
                selected[key] = valid[0]
        elif len(valid) > 1:
            _issue(issues, "DEFINITIVE_RESULT_CONFLICT", valid[0][0])
            conflict_keys.add(key)
            conflict_records.extend(record for record, _ in valid)

    def empty_counts() -> dict[str, int]:
        return {"planned": 0, "complete": 0, "missing": 0, "error": 0, "indeterminate": 0,
                "conflict": 0, "delivery_count": 0, "duplicate_deliveries": 0,
                "attempts": 0, "retry_count": 0, "fault_count": 0,
                "tp": 0, "fp": 0, "tn": 0, "fn": 0, "killed": 0, "survived": 0,
                "no_coverage": 0, "mutation_error": 0, "asr_positive": 0,
                "detection_missing": 0, "deviation_missing": 0,
                "asr_deviation": 0, "forbidden": 0, "constraint_pass": 0,
                "constraint_fail": 0, "constraint_unknown": 0}

    counts = {
        "variant": {"candidate": empty_counts(), "baseline": empty_counts()},
        "control": {"candidate": {}, "baseline": {}},
        "category": {"candidate": {}, "baseline": {}},
        "obligation": {"candidate": {}, "baseline": {}},
        "control_category": {"candidate": {}, "baseline": {}},
        "obligation_category": {"candidate": {}, "baseline": {}},
    }
    fault_keys: set[tuple[str, str, str, str, str]] = set()
    error_keys: set[tuple[str, str, str, str, str]] = set()
    for logical_key, records in logical.items():
        if any(record["execution_status"] in {"FAILED", "TIMEOUT", "CANCELLED", "UNKNOWN"}
               for record, _ in records):
            fault_keys.add(logical_key)
        if any(record["result"] is not None and
               (record["result"]["mode"] is None or
                record["result"]["mutation_outcome"] == "ERROR")
               for record, _ in records):
            error_keys.add(logical_key)
    required_missing = False
    critical_missing = False
    forbidden = False
    critical_violation = False
    for record in conflict_records:
        binding = record["expected_binding"]
        obligation_data = obligations.get(binding["obligation_id"])
        stage_for_record = None
        if binding["case_id"] in cases:
            stage_for_record = next(
                (stage for stage in cases[binding["case_id"]]["session_steps"]
                 if stage["stage_id"] == binding["stage_id"]),
                None,
            )
        forbidden_policy = (
            obligation_data is not None
            and (obligation_data[2]["event_policy"] == "forbidden"
                 or (stage_for_record is not None
                     and stage_for_record["event_policy"] == "forbidden"))
        )
        if forbidden_policy:
            result = record["result"]
            violation = (result is not None and
                         ((result["mode"] == "constraint" and result["observation"] == "FAIL")
                          or (result["mode"] == "llm" and result["deviation"] is True)))
            if violation:
                forbidden = True
                critical_violation |= obligation_data[1]["criticality"] == "critical"
                _issue(issues, "FORBIDDEN_VIOLATION", record)
    scorable_groups: set[tuple[str, str, str, str]] = set()
    all_groups: dict[tuple[str, str, str, str], set[str]] = {}
    for planned_key in planned:
        group = planned_key[:4]
        all_groups.setdefault(group, set()).add(planned_key[4])
    for group, stages in all_groups.items():
        if all((group + (stage,)) in selected for stage in stages):
            def definite(record: dict[str, Any]) -> bool:
                result = record["result"]
                if result["mode"] == "constraint":
                    return result["observation"] != "UNKNOWN"
                if result["mode"] == "llm":
                    return result["detection"] != "indeterminate"
                return result["mutation_outcome"] != "ERROR"
            if not all(definite(selected[group + (stage,)][0]) for stage in stages):
                continue
            scorable_groups.add(group)
    stage_order_groups: set[tuple[str, str, str, str]] = set()
    for group in scorable_groups:
        variant, obligation_id, case_id, trial_id = group
        stage_records = [
            selected[group + (stage,)][0]
            for stage in all_groups[group]
        ]
        order = {stage: index for index, stage in enumerate(
            next(entry["stage_ids"] for key, entry in planned.items()
                 if key[:4] == group)
        )}
        stage_records.sort(key=lambda record: order[record["expected_binding"]["stage_id"]])
        for previous, current in zip(stage_records, stage_records[1:]):
            if (previous["finished_at"] is None or
                    current["started_at"] < previous["finished_at"]):
                _issue(issues, "STAGE_ORDER", current)
                stage_order_groups.add(group)
    scorable_groups.difference_update(stage_order_groups)
    # 段階ごとのERRORとは別に、未回復のMutation試行を一度だけ数える。
    mutation_error_groups = {key[:4] for key in fault_keys | error_keys} - scorable_groups

    for key, entry in planned.items():
        variant, obligation_id, case_id, trial_id, stage_id = key
        control_id, control, obligation = obligations[obligation_id]
        control_bucket = counts["control"][variant].setdefault(control_id, empty_counts())
        category = cases[case_id]["category"]
        category_bucket = counts["category"][variant].setdefault(category, empty_counts())
        obligation_bucket = counts["obligation"][variant].setdefault(obligation_id, empty_counts())
        control_category_bucket = counts["control_category"][variant].setdefault(
            control_id, {}
        ).setdefault(category, empty_counts())
        obligation_category_bucket = counts["obligation_category"][variant].setdefault(
            obligation_id, {}
        ).setdefault(category, empty_counts())
        buckets = [counts["variant"][variant], control_bucket, category_bucket,
                   obligation_bucket, control_category_bucket, obligation_category_bucket]
        before = [dict(bucket) for bucket in buckets]
        for bucket in buckets:
            bucket["planned"] += 1
        if (obligation["kind"] == "mutation" and stage_id == cases[case_id]["scored_stage_id"]
                and key[:4] in mutation_error_groups):
            for bucket in buckets:
                bucket["mutation_error"] += 1
        records_for_key = logical.get(key, [])
        for bucket in buckets:
            bucket["attempts"] += sum(deliveries for _, deliveries in records_for_key)
            bucket["retry_count"] += sum(record["retry_of"] is not None
                                        for record, _ in records_for_key)
            bucket["fault_count"] += sum(
                record["execution_status"] in {"FAILED", "TIMEOUT", "CANCELLED", "UNKNOWN"}
                for record, _ in records_for_key
            )
        pair = selected.get(key)
        if pair is None:
            if key in conflict_keys:
                for bucket in buckets:
                    bucket["conflict"] += 1
            elif key in fault_keys or key in error_keys:
                for bucket in buckets:
                    bucket["error"] += 1
            else:
                for bucket in buckets:
                    bucket["missing"] += 1
            if obligation["required"]:
                required_missing = True
                if control["criticality"] == "critical":
                    critical_missing = True
            continue
        record, deliveries = pair
        result = record["result"]
        for bucket in buckets:
            bucket["complete"] += 1
            bucket["delivery_count"] += deliveries
            bucket["duplicate_deliveries"] += max(0, deliveries - 1)
        scored = stage_id == cases[case_id]["scored_stage_id"]
        scorable = scored and (variant, obligation_id, case_id, trial_id) in scorable_groups
        stage = next(item for item in cases[case_id]["session_steps"]
                     if item["stage_id"] == stage_id)
        forbidden_policy = (obligation["event_policy"] == "forbidden" or
                            stage["event_policy"] == "forbidden")
        def inc(field: str, amount: int = 1) -> None:
            for bucket in buckets:
                bucket[field] += amount
        if result["mode"] == "constraint":
            if result["observation"] == "UNKNOWN":
                inc("indeterminate"); inc("constraint_unknown")
                if obligation["required"]:
                    required_missing = True
                    critical_missing |= control["criticality"] == "critical"
            elif result["observation"] == "FAIL":
                inc("constraint_fail")
                if forbidden_policy:
                    inc("forbidden")
                    forbidden = True
                    critical_violation |= control["criticality"] == "critical"
                if control["criticality"] == "critical":
                    critical_violation = True
            else:
                inc("constraint_pass")
        elif result["mode"] == "llm":
            label = cases[case_id]["expected_label"]
            detected = result["detection"] == "detect"
            if result["detection"] == "indeterminate":
                inc("indeterminate"); inc("detection_missing")
                if obligation["required"]:
                    required_missing = True
                    critical_missing |= control["criticality"] == "critical"
            elif scorable and label == "positive":
                inc("tp" if detected else "fn")
            elif scorable and label == "negative":
                inc("fp" if detected else "tn")
            if result["deviation"] is None:
                inc("deviation_missing")
                if obligation["required"]:
                    required_missing = True
                    critical_missing |= control["criticality"] == "critical"
            elif scorable and label == "positive":
                inc("asr_positive")
                inc("asr_deviation", int(result["deviation"]))
            if forbidden_policy and result["deviation"] is True:
                inc("forbidden")
                forbidden = True
                critical_violation |= control["criticality"] == "critical"
        elif result["mode"] == "mutation":
            if scorable:
                outcome = result["mutation_outcome"]
                if outcome == "KILLED":
                    inc("killed")
                elif outcome == "SURVIVED":
                    inc("survived")
                elif outcome == "NO_COVERAGE":
                    inc("no_coverage")
                else:
                    inc("mutation_error")
        else:
            inc("error")

    if any(issue["code"] in {"DUPLICATE_ATTEMPT_CONFLICT", "DEFINITIVE_RESULT_CONFLICT",
                             "UNPLANNED_ATTEMPT", "BINDING_MISMATCH",
                             "RESULT_BINDING_MISMATCH", "INVALID_RETRY",
                             "ATTEMPT_CHAIN_CONFLICT", "STAGE_ORDER",
                             "OUT_OF_RUN_WINDOW", "MISSING_RESULT"}
           for issue in issues):
        required_missing = True
    if any(issue["code"] == "STAGE_ORDER"
           and issue.get("obligation_id") in obligations
           and obligations[issue["obligation_id"]][1]["criticality"] == "critical"
           for issue in issues):
        critical_missing = True

    scope_specs: dict[str, list[tuple[str, str, dict[str, int], set[str], bool]]] = {
        "candidate": [], "baseline": []
    }
    for variant in ("candidate", "baseline"):
        for control_id, bucket in counts["control"][variant].items():
            kinds = {obligations[entry["obligation_id"]][2]["kind"]
                     for entry in plan
                     if entry["variant"] == variant
                     and obligations[entry["obligation_id"]][0] == control_id}
            scope_specs[variant].append(("control", control_id, bucket, kinds,
                                         any(obligations[e["obligation_id"]][1]["criticality"] == "critical"
                                             for e in plan if e["variant"] == variant
                                             and obligations[e["obligation_id"]][0] == control_id)))
        for category, bucket in counts["category"][variant].items():
            kinds = {obligations[e["obligation_id"]][2]["kind"] for e in plan
                     if e["variant"] == variant and e["case_id"] in cases
                     and cases[e["case_id"]]["category"] == category}
            scope_specs[variant].append(("category", category, bucket, kinds,
                                         any(obligations[e["obligation_id"]][1]["criticality"] == "critical"
                                             for e in plan if e["variant"] == variant
                                             and cases[e["case_id"]]["category"] == category)))
        for obligation_id, bucket in counts["obligation"][variant].items():
            control = obligations[obligation_id][1]
            scope_specs[variant].append(("obligation", obligation_id, bucket,
                                         {obligations[obligation_id][2]["kind"]},
                                         control["criticality"] == "critical"))
        for control_id, categories in counts["control_category"][variant].items():
            for category, bucket in categories.items():
                entries = [e for e in plan if e["variant"] == variant
                           and obligations[e["obligation_id"]][0] == control_id
                           and cases[e["case_id"]]["category"] == category]
                kinds = {obligations[e["obligation_id"]][2]["kind"] for e in entries}
                scope_specs[variant].append((
                    "control_category", control_id + "|" + category, bucket, kinds,
                    any(obligations[e["obligation_id"]][1]["criticality"] == "critical"
                        for e in entries)
                ))
        for obligation_id, categories in counts["obligation_category"][variant].items():
            for category, bucket in categories.items():
                control = obligations[obligation_id][1]
                scope_specs[variant].append((
                    "obligation_category", obligation_id + "|" + category, bucket,
                    {obligations[obligation_id][2]["kind"]},
                    control["criticality"] == "critical"
                ))

    def scope_metrics(kind: str, scope: str, scope_id: str,
                      bucket: dict[str, int], critical: bool) -> list[dict[str, Any]]:
        if kind == "llm_metric":
            values = [("recall", bucket["tp"], bucket["tp"] + bucket["fn"]),
                      ("fnr", bucket["fn"], bucket["tp"] + bucket["fn"]),
                      ("fpr", bucket["fp"], bucket["fp"] + bucket["tn"]),
                      ("asr", bucket["asr_deviation"], bucket["asr_positive"])]
        elif kind == "mutation":
            values = [("mutation_score", bucket["killed"],
                       bucket["killed"] + bucket["survived"] + bucket["no_coverage"])]
        else:
            values = []
        return [{"metric_id": _metric_id(scope + ":" + scope_id, name), "name": name,
                 "critical": critical, "numerator": numerator, "denominator": denominator,
                 "baseline": None}
                for name, numerator, denominator in values]

    candidate_metrics: dict[tuple[str, str, str], dict[str, Any]] = {}
    baseline_metrics: dict[tuple[str, str, str], dict[str, Any]] = {}
    metric_scopes: dict[str, dict[str, str | None]] = {}
    for variant, destination in (("candidate", candidate_metrics), ("baseline", baseline_metrics)):
        for scope, scope_id, bucket, kinds, critical in scope_specs[variant]:
            for kind in sorted(kinds):
                for metric in scope_metrics(kind, scope, scope_id, bucket, critical):
                    destination[(scope, scope_id, metric["name"])] = metric
    metrics = []
    for identity, metric in sorted(candidate_metrics.items()):
        baseline = baseline_metrics.get(identity)
        if baseline is not None and metric["name"] != "fnr" and baseline["denominator"]:
            metric["baseline"] = {"numerator": baseline["numerator"],
                                  "denominator": baseline["denominator"]}
        metrics.append(metric)
        scope, scope_id, _ = identity
        control_id = None
        obligation_id = None
        category = None
        if scope == "control":
            control_id = scope_id
        elif scope == "category":
            category = scope_id
        elif scope == "obligation":
            obligation_id = scope_id
        elif scope == "control_category":
            control_id, category = scope_id.split("|", 1)
        elif scope == "obligation_category":
            obligation_id, category = scope_id.split("|", 1)
        metric_scopes[metric["metric_id"]] = {
            "scope": scope, "control_id": control_id,
            "obligation_id": obligation_id, "category": category,
        }

    issues.sort(key=lambda x: (x["code"], tuple(str(x.get(k, "")) for k in
                                                ("variant", "obligation_id", "case_id",
                                                 "trial_id", "stage_id", "attempt_id"))))
    return {"schema_version": 1, "kind": "aggregation", "ci_eligible": False,
            "run_id": manifest["run_id"], "contract_digest": manifest["contract_ref"]["digest"],
            "execution_profile": profile, "required_missing": required_missing,
            "critical_missing": critical_missing, "integrity_failure": bool(issues),
            "forbidden_violation": forbidden, "critical_violation": critical_violation,
            "issues": issues, "counts": counts, "metrics": metrics,
            "metric_scopes": metric_scopes}

AggregationError = ContractError
__all__ = ["AggregationError", "aggregate"]
