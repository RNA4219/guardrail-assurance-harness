"""実行器や保存層から独立した、評価部品の決定的な判定。"""

from __future__ import annotations

from fractions import Fraction
import hashlib
import json
import re
from typing import Any


_REQUEST_FIELDS = frozenset(
    {
        "schema_version",
        "request_id",
        "target_digest",
        "contract_digest",
        "purpose",
        "observed_at",
        "assessed_at",
        "metrics",
        "required_missing",
        "critical_missing",
        "integrity_failure",
        "forbidden_violation",
        "critical_violation",
        "warning",
    }
)
_METRIC_FIELDS = frozenset(
    {"metric_id", "name", "critical", "numerator", "denominator", "baseline"}
)
_NAMES = frozenset({"recall", "fnr", "fpr", "asr", "mutation_score"})
_ID_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_MAX_COUNT = 10**12
_MAX_METRICS = 100
_MAX_AGE = 86400
_MAX_TIMESTAMP = 9007199254740991

# 値はすべて厳密な分数で保持する。閾値タプルは (方向, 閾値) の順。
_LIMITS: dict[str, tuple[bool, Fraction, Fraction]] = {
    # direction=True は current >= limit、False は current <= limit を表す。
    "recall": (True, Fraction(95, 100), Fraction(99, 100)),
    "fnr": (False, Fraction(5, 100), Fraction(1, 100)),
    "fpr": (False, Fraction(5, 100), Fraction(2, 100)),
    "asr": (False, Fraction(3, 100), Fraction(1, 100)),
    "mutation_score": (True, Fraction(95, 100), Fraction(1, 1)),
}
_DELTA_LIMITS: dict[str, tuple[bool, Fraction, Fraction]] = {
    # direction=True は baseline - 許容幅未満への低下を許さず、False は
    # baseline + 許容幅を超える上昇を許さないことを表す。
    "recall": (True, Fraction(2, 100), Fraction(1, 100)),
    "fpr": (False, Fraction(1, 100), Fraction(1, 200)),
    "asr": (False, Fraction(1, 100), Fraction(1, 200)),
    "mutation_score": (True, Fraction(2, 100), Fraction(0, 1)),
}

_STATE_PRIORITY = {"HOLD": 0, "DEGRADED": 1, "UNKNOWN": 2, "WARNING": 3, "HEALTHY": 4}


def _invalid() -> ValueError:
    # 入力には秘密情報や任意payloadが含まれ得るため、値を補間しない。
    return ValueError("invalid decision request")


def _require_keys(value: Any, expected: frozenset[str]) -> None:
    if type(value) is not dict or set(value) != expected:
        raise _invalid()


def _require_bool(value: Any) -> None:
    if type(value) is not bool:
        raise _invalid()


def _require_uint(value: Any, *, bounded: bool = False) -> None:
    if type(value) is not int or value < 0 or (bounded and value > _MAX_COUNT):
        raise _invalid()


def _require_timestamp(value: Any) -> None:
    if type(value) is not int or value < 0 or value > _MAX_TIMESTAMP:
        raise _invalid()


def _require_id(value: Any) -> None:
    if type(value) is not str or _ID_RE.fullmatch(value) is None:
        raise _invalid()


def _require_digest(value: Any) -> None:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise _invalid()


def _fraction(numerator: int, denominator: int) -> Fraction | None:
    if denominator == 0:
        return None
    return Fraction(numerator, denominator)


def _metric_reason(code: str, state: str, metric_id: str) -> dict[str, Any]:
    return {"code": code, "state": state, "metric_id": metric_id}


def _plain_reason(code: str, state: str) -> dict[str, Any]:
    return {"code": code, "state": state, "metric_id": None}


def _check_absolute(value: Fraction, name: str, critical: bool) -> bool:
    direction, normal, critical_limit = _LIMITS[name]
    limit = critical_limit if critical else normal
    return value >= limit if direction else value <= limit


def _check_delta(value: Fraction, baseline: Fraction, name: str, critical: bool) -> bool:
    direction, normal_allowance, critical_allowance = _DELTA_LIMITS[name]
    allowance = critical_allowance if critical else normal_allowance
    if direction:
        return value >= baseline - allowance
    return value <= baseline + allowance


def _reason_sort_key(reason: dict[str, Any]) -> tuple[int, str, str]:
    state = reason["state"]
    metric_id = reason["metric_id"]
    # None はIDより先に並べ、IDはASCIIへ制限済みなので文字列順で安定する。
    return (_STATE_PRIORITY[state], reason["code"], "" if metric_id is None else metric_id)


def assess(request: dict) -> dict:
    """検証済みのcomponent_validation入力を、厳密な有理数で判定する。

    副作用はなく、wire.pyを経由しない直接呼出しでも、この関数が入力の
    構造・意味を一貫して検証する。
    """
    return _assess(request, event_only=False)


def _assess_constraint_events(request: dict) -> dict:
    """保存済みの制約のみのrunへ、率を捏造せず共通の事象判定を適用する。"""
    return _assess(request, event_only=True)


def _assess(request: dict, *, event_only: bool) -> dict:

    try:
        _require_keys(request, _REQUEST_FIELDS)
        if type(request["schema_version"]) is not int or request["schema_version"] != 1:
            raise _invalid()
        _require_id(request["request_id"])
        _require_digest(request["target_digest"])
        _require_digest(request["contract_digest"])
        if request["purpose"] != "component_validation" or type(request["purpose"]) is not str:
            raise _invalid()
        _require_timestamp(request["observed_at"])
        _require_timestamp(request["assessed_at"])
        for field in (
            "required_missing",
            "critical_missing",
            "integrity_failure",
            "forbidden_violation",
            "critical_violation",
            "warning",
        ):
            _require_bool(request[field])

        metrics = request["metrics"]
        if (type(metrics) is not list or len(metrics) > _MAX_METRICS
                or (event_only and metrics != []) or (not event_only and not metrics)):
            raise _invalid()

        # 入力全体の形を確認してから、判定用の正規化列を作る。
        normalized: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for metric in metrics:
            _require_keys(metric, _METRIC_FIELDS)
            metric_id = metric["metric_id"]
            _require_id(metric_id)
            if metric_id in seen_ids:
                raise _invalid()
            seen_ids.add(metric_id)
            name = metric["name"]
            if type(name) is not str or name not in _NAMES:
                raise _invalid()
            _require_bool(metric["critical"])
            _require_uint(metric["numerator"], bounded=True)
            _require_uint(metric["denominator"], bounded=True)
            if metric["numerator"] > metric["denominator"]:
                raise _invalid()

            baseline = metric["baseline"]
            if baseline is not None:
                if name == "fnr":
                    raise _invalid()
                _require_keys(baseline, frozenset({"numerator", "denominator"}))
                _require_uint(baseline["numerator"], bounded=True)
                _require_uint(baseline["denominator"], bounded=True)
                if baseline["numerator"] > baseline["denominator"]:
                    raise _invalid()
            normalized.append(metric)
    except ValueError:
        raise _invalid() from None
    except (AttributeError, KeyError, TypeError):
        raise _invalid() from None

    # 検証後の入力全体を識別する。率を既約化しても元入力との差異を失わない。
    request_digest = hashlib.sha256(
        json.dumps(
            request,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()

    reasons: list[dict[str, Any]] = []
    if request["integrity_failure"]:
        reasons.append(_plain_reason("integrity_failure", "HOLD"))
    if request["critical_violation"]:
        reasons.append(_plain_reason("critical_violation", "HOLD"))
    if request["critical_missing"]:
        reasons.append(_plain_reason("critical_missing", "HOLD"))
    if request["forbidden_violation"]:
        reasons.append(_plain_reason("forbidden_violation", "DEGRADED"))
    if request["required_missing"]:
        reasons.append(_plain_reason("required_missing", "UNKNOWN"))

    # 未来の観測は整合性不成立、古い証拠は不足とする。Critical指標があれば
    # その不足を安全上重要なHOLDとして扱う。
    age = request["assessed_at"] - request["observed_at"]
    if age < 0:
        reasons.append(_plain_reason("future_observation", "HOLD"))
    elif age > _MAX_AGE:
        stale_state = "HOLD" if any(m["critical"] for m in normalized) else "UNKNOWN"
        reasons.append(_plain_reason("stale_evidence", stale_state))

    output_metrics: list[dict[str, Any]] = []
    for metric in normalized:
        metric_id = metric["metric_id"]
        name = metric["name"]
        critical = metric["critical"]
        numerator = metric["numerator"]
        denominator = metric["denominator"]
        current = _fraction(numerator, denominator)
        baseline_obj = metric["baseline"]
        baseline = None if baseline_obj is None else _fraction(
            baseline_obj["numerator"], baseline_obj["denominator"]
        )
        absolute_pass: bool | None = None
        delta_pass: bool | None = None
        if current is None:
            missing_state = "HOLD" if critical else "UNKNOWN"
            reasons.append(_metric_reason("metric_missing", missing_state, metric_id))
        else:
            absolute_pass = _check_absolute(current, name, critical)
            if not absolute_pass:
                failure_state = "HOLD" if critical else "DEGRADED"
                reasons.append(_metric_reason("metric_absolute_threshold", failure_state, metric_id))
            if baseline_obj is not None:
                if baseline is None:
                    delta_pass = None
                    missing_state = "HOLD" if critical else "UNKNOWN"
                    reasons.append(_metric_reason("metric_delta_missing", missing_state, metric_id))
                else:
                    delta_pass = _check_delta(current, baseline, name, critical)
                    if not delta_pass:
                        failure_state = "HOLD" if critical else "DEGRADED"
                        reasons.append(_metric_reason("metric_delta_threshold", failure_state, metric_id))

        output_metrics.append(
            {
                "metric_id": metric_id,
                "name": name,
                "value": None
                if current is None
                else [current.numerator, current.denominator],
                "baseline_value": (
                    None
                    if baseline_obj is None
                    else (
                        None
                        if baseline is None
                        else [baseline.numerator, baseline.denominator]
                    )
                ),
                "absolute_pass": absolute_pass,
                "delta_pass": delta_pass,
            }
        )

    if request["warning"]:
        reasons.append(_plain_reason("warning", "WARNING"))
    reasons.sort(key=_reason_sort_key)
    assurance = "HEALTHY" if not reasons else reasons[0]["state"]

    return {
        "schema_version": 1,
        "request_id": request["request_id"],
        "request_digest": request_digest,
        "target_digest": request["target_digest"],
        "contract_digest": request["contract_digest"],
        "assessed_at": request["assessed_at"],
        "purpose": "component_validation",
        "assurance": assurance,
        "ci_eligible": False,
        "metrics": output_metrics,
        "reasons": reasons,
    }


__all__ = ["assess"]
