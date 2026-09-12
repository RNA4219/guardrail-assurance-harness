"""固定fixtureのraw結果を検査済みNormalizedResultへ変換する。"""

from __future__ import annotations

import hashlib
from copy import deepcopy
from typing import Any

from .contracts import (
    MAX_DOCUMENT_BYTES,
    MAX_INTEGER,
    ContractError,
    decode_document,
    require_digest,
    require_id,
    require_object,
    require_uint,
)


MAX_RAW_BYTES = 256 * 1024
_BINDING_FIELDS = {
    "run_id", "operation_id", "owner_epoch", "contract_digest", "target_digest",
    "obligation_id", "case_id", "trial_id", "stage_id", "fixture_digest",
    "adapter_digest", "policy_digest", "evaluator_digest", "isolation_digest",
}
_RAW_FIELDS = {"schema_version", "kind", "binding", "mode", "observations"}
_CONSTRAINT_FIELDS = {"check"}
_MUTATION_FIELDS = {
    "baseline", "mutation_applied", "reached", "detected", "unrelated_failure",
}
_LLM_FIELDS = {"detection", "deviation"}
_MODES = {"constraint", "mutation", "llm"}
_EXECUTION_STATUSES = {"COMPLETED", "FAILED", "TIMEOUT", "CANCELLED"}
_CHECKS = {"PASS", "FAIL", "UNKNOWN"}
_DETECTIONS = {"detect", "allow", "indeterminate"}
_ERROR_EXECUTION = "EXECUTION_FAILURE"


def _invalid(code: str = "INVALID_CONTRACT") -> ContractError:
    return ContractError(code)


def _require_enum(value: Any, values: set[str]) -> None:
    if type(value) is not str or value not in values:
        raise _invalid()


def _require_bool(value: Any) -> None:
    if type(value) is not bool:
        raise _invalid()


def validate_binding(binding: Any) -> dict[str, Any]:
    """bindingの型と全fieldを検査し、独立したcopyを返す。"""
    require_object(binding, _BINDING_FIELDS)
    for field in _BINDING_FIELDS:
        if field == "owner_epoch":
            require_uint(binding[field], maximum=MAX_INTEGER)
            if binding[field] < 1:
                raise _invalid()
        elif field.endswith("_digest"):
            require_digest(binding[field])
        else:
            require_id(binding[field])
    return deepcopy(binding)


def _error_result(binding: dict[str, Any], error_class: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "kind": "normalized_result",
        "binding": deepcopy(binding),
        "mode": None,
        "observation": None,
        "mutation_outcome": "ERROR",
        "detection": None,
        "deviation": None,
        "error_class": error_class,
        "raw_digest": None,
    }


def _normal_result(
    binding: dict[str, Any],
    mode: str,
    *,
    observation: str | None,
    mutation_outcome: str | None,
    detection: str | None,
    deviation: bool | None,
    raw_digest: str,
    error_class: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "kind": "normalized_result",
        "binding": deepcopy(binding),
        "mode": mode,
        "observation": observation,
        "mutation_outcome": mutation_outcome,
        "detection": detection,
        "deviation": deviation,
        "error_class": error_class,
        "raw_digest": raw_digest,
    }


def _parse_raw(raw: Any) -> tuple[dict[str, Any], str]:
    if type(raw) is not bytes or len(raw) > MAX_RAW_BYTES:
        raise _invalid("RAW_SIZE")
    try:
        parsed = decode_document(raw)
    except ContractError:
        raise
    if len(raw) > MAX_DOCUMENT_BYTES:
        # MAX_RAW_BYTESが先に保証するため到達しないが、上限の関係を固定する。
        raise _invalid("RAW_SIZE")
    return parsed, hashlib.sha256(raw).hexdigest()


def _validate_raw_shape(raw_value: dict[str, Any]) -> tuple[dict[str, Any], str, dict[str, Any]]:
    require_object(raw_value, _RAW_FIELDS)
    if type(raw_value["schema_version"]) is not int or raw_value["schema_version"] != 1:
        raise _invalid("UNSUPPORTED_VERSION")
    if type(raw_value["kind"]) is not str or raw_value["kind"] != "gah_generic_result":
        raise _invalid()
    binding = validate_binding(raw_value["binding"])
    mode = raw_value["mode"]
    _require_enum(mode, _MODES)
    observations = raw_value["observations"]
    if type(observations) is not dict:
        raise _invalid()
    return binding, mode, observations


def _normalize_constraint(
    observations: dict[str, Any], binding: dict[str, Any], raw_digest: str
) -> dict[str, Any]:
    require_object(observations, _CONSTRAINT_FIELDS)
    check = observations["check"]
    _require_enum(check, _CHECKS)
    return _normal_result(
        binding, "constraint", observation=check, mutation_outcome=None,
        detection=None, deviation=None, raw_digest=raw_digest,
    )


def _normalize_mutation(
    observations: dict[str, Any], binding: dict[str, Any], raw_digest: str
) -> dict[str, Any]:
    require_object(observations, _MUTATION_FIELDS)
    baseline = observations["baseline"]
    _require_enum(baseline, _CHECKS)
    for field in _MUTATION_FIELDS - {"baseline"}:
        _require_bool(observations[field])
    applied = observations["mutation_applied"]
    reached = observations["reached"]
    detected = observations["detected"]
    unrelated_failure = observations["unrelated_failure"]
    if detected and not reached:
        raise _invalid("CONTRADICTORY_OBSERVATION")
    if baseline != "PASS":
        return _normal_result(
            binding, "mutation", observation=baseline,
            mutation_outcome="ERROR", detection=None, deviation=None,
            error_class="BASELINE_NOT_PASS", raw_digest=raw_digest,
        )
    if unrelated_failure:
        error_class = "UNRELATED_FAILURE"
    elif not applied:
        error_class = "MUTATION_NOT_APPLIED"
    elif not reached:
        return _normal_result(
            binding, "mutation", observation=baseline,
            mutation_outcome="NO_COVERAGE", detection=None, deviation=None,
            raw_digest=raw_digest,
        )
    elif detected:
        return _normal_result(
            binding, "mutation", observation=baseline,
            mutation_outcome="KILLED", detection=None, deviation=None,
            raw_digest=raw_digest,
        )
    else:
        return _normal_result(
            binding, "mutation", observation=baseline,
            mutation_outcome="SURVIVED", detection=None, deviation=None,
            raw_digest=raw_digest,
        )
    return _normal_result(
        binding, "mutation", observation=baseline,
        mutation_outcome="ERROR", detection=None, deviation=None,
        error_class=error_class, raw_digest=raw_digest,
    )


def _normalize_llm(
    observations: dict[str, Any], binding: dict[str, Any], raw_digest: str
) -> dict[str, Any]:
    require_object(observations, _LLM_FIELDS)
    detection = observations["detection"]
    _require_enum(detection, _DETECTIONS)
    deviation = observations["deviation"]
    if deviation is not None:
        _require_bool(deviation)
    return _normal_result(
        binding, "llm", observation=None, mutation_outcome=None,
        detection=detection, deviation=deviation, raw_digest=raw_digest,
    )


def normalize_generic(
    raw: Any,
    expected_binding: Any,
    *,
    execution_status: Any,
    exit_code: Any,
    stop_confirmed: Any,
) -> dict[str, Any]:
    """rawを呼出側bindingへ固定し、実行成功時だけ正規化する。"""
    binding = validate_binding(expected_binding)
    _require_enum(execution_status, _EXECUTION_STATUSES)
    if exit_code is not None:
        if type(exit_code) is not int or not 0 <= exit_code <= 255:
            raise _invalid()
    _require_bool(stop_confirmed)
    if execution_status != "COMPLETED" or exit_code != 0 or not stop_confirmed:
        return _error_result(binding, _ERROR_EXECUTION)
    raw_value, raw_digest = _parse_raw(raw)
    raw_binding, mode, observations = _validate_raw_shape(raw_value)
    if raw_binding != binding:
        raise _invalid("BINDING_MISMATCH")
    if mode == "constraint":
        return _normalize_constraint(observations, binding, raw_digest)
    if mode == "mutation":
        return _normalize_mutation(observations, binding, raw_digest)
    return _normalize_llm(observations, binding, raw_digest)


__all__ = ["normalize_generic", "validate_binding"]

