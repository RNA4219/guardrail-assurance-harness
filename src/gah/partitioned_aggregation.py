"""Pure aggregation entry point for v2 partitioned run plans.

This module does not establish authority freshness, persistence, or CI eligibility.
"""
from __future__ import annotations

import json
from typing import Any

from . import aggregation as _aggregation
from .contracts import ContractError, MAX_DOCUMENT_BYTES
from .partitioned_run_contracts import bind_partitioned_run_manifest
from .partitioned_trial_plan import MAX_ARTIFACT_BYTES, MAX_SEGMENTS, restore_trial_plan, _canonical

MAX_ATTEMPT_INPUT_BYTES = 64 * 1024 * 1024


def _snapshot(value: Any, *, maximum: int = MAX_DOCUMENT_BYTES) -> Any:
    """Canonical strict JSON snapshot with an explicit byte ceiling."""
    raw = _aggregation._canon(value)
    if len(raw) > maximum:
        raise ContractError("DOCUMENT_SIZE")
    try:
        return json.loads(raw)
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise ContractError("INVALID_AGGREGATION") from None


def _snapshot_partition(value: Any) -> Any:
    raw = _canonical(value, maximum=MAX_ARTIFACT_BYTES)
    try:
        return json.loads(raw)
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise ContractError("PARTITIONED_PLAN_INVALID") from None


def _snapshot_attempts(value: Any) -> list[dict[str, Any]]:
    if type(value) is not list or len(value) > _aggregation.MAX_ATTEMPTS:
        raise ContractError("ATTEMPT_LIMIT")
    result: list[dict[str, Any]] = []
    total = 0
    for ordinal, attempt in enumerate(value):
        if ordinal >= _aggregation.MAX_ATTEMPTS:
            raise ContractError("ATTEMPT_LIMIT")
        # Bound and strict-check each item before copying it; never serialize or
        # deepcopy the caller's entire potentially huge attempts list at once.
        raw = _aggregation._canon(attempt)
        total += len(raw)
        if total > MAX_ATTEMPT_INPUT_BYTES:
            raise ContractError("INPUT_LIMIT")
        try:
            result.append(json.loads(raw))
        except (TypeError, ValueError, UnicodeError, RecursionError):
            raise ContractError("INVALID_AGGREGATION") from None
    return result


def aggregate_partitioned(
    manifest: Any,
    contract: Any,
    index: Any,
    segments: Any,
    policy: Any,
    registry: Any,
    case_set: Any,
    attempts: Any,
    *,
    execution_profile: Any,
    baseline_context: Any = None,
) -> dict[str, Any]:
    """Validate a v2 plan, snapshot all inputs, and reuse the private aggregate core.

    The reconstructed plan is never passed through v1 TrialPlan/RunManifest
    validators or the public v1 aggregate cache. Callers remain responsible for
    fresh authority/source/permission checks on each operation.
    """
    if type(segments) not in (list, tuple) or not 1 <= len(segments) <= MAX_SEGMENTS:
        raise ContractError("SEGMENT_COUNT")
    segments = tuple(segments[:MAX_SEGMENTS + 1])
    if not 1 <= len(segments) <= MAX_SEGMENTS:
        raise ContractError("SEGMENT_COUNT")
    manifest_value = _snapshot_partition(manifest)
    contract_value = _snapshot(contract)
    index_value = _snapshot_partition(index)
    segment_values = [_snapshot_partition(segment) for segment in segments]
    policy_value = _snapshot(policy)
    registry_value = _snapshot(registry)
    case_set_value = _snapshot(case_set)
    profile_value = _snapshot(execution_profile)
    baseline_value = None if baseline_context is None else _snapshot(baseline_context)
    attempt_values = _snapshot_attempts(attempts)

    # This performs the complete v2 manifest/index/segment and semantic bind.
    bound_refs = bind_partitioned_run_manifest(
        manifest_value, contract_value, index_value, segment_values,
        policy_value, registry_value, case_set_value,
        baseline_context=baseline_value,
    )
    logical_plan = restore_trial_plan(index_value, segment_values)
    internal_bound = {
        "manifest": manifest_value,
        "contract": contract_value,
        "plan": logical_plan,
        "policy": policy_value,
        "registry": registry_value,
        "case_set": case_set_value,
        "selected_controls": bound_refs["selected_controls"],
        "ci_eligible": False,
    }
    value = _aggregation._aggregate_validated(
        internal_bound, attempt_values, execution_profile=profile_value,
    )
    result = {
        **value,
        "schema_version": 2,
        "kind": "partitioned_aggregation",
        "plan_index_ref": bound_refs["plan_index_ref"],
        "manifest_ref": bound_refs["manifest_ref"],
        "ci_eligible": False,
    }
    # Enforce the existing bounded strict output contract after adding v2 refs.
    _aggregation._canon(result)
    return result


__all__ = ["MAX_ATTEMPT_INPUT_BYTES", "aggregate_partitioned"]
