"""実案件 pilot の専用契約、参照照合、決定的な判定コア。

このモジュールは、拡張仕様の計画と観測結果を既存の製品 CI から分離して
扱う。入力から runner、shell、認証主体を組み立てることはなく、判定は保存
済みの観測値だけを用いる。実案件の対象、許可、model 版、担当者をこの
モジュールが作り出すこともない。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import copy
import hashlib
import math
from fractions import Fraction
from typing import Any

from .contracts import (
    ContractError,
    MAX_INTEGER,
    require_digest,
    require_id,
    require_object,
    require_ref,
    require_uint,
)
from .productization import (
    ACCEPTANCE_FIELDS,
    PLAN_FIELDS,
    content_ref,
    read_document,
    validate_acceptance_record,
    validate_operation_result,
    validate_plan,
    write_document,
)
from .wire import canonical_bytes


# 専用 artifact の kind は閉じた集合にする。未知の kind を受け取って
# 汎用 JSON 登録器になることを避ける。
PILOT_KINDS = frozenset({
    "project_binding",
    "evaluation_target_binding",
    "pilot_plan",
    "pilot_selection",
    "pilot_baseline",
    "pilot_oracle",
    "pilot_observation",
    "pilot_result",
    "pilot_acceptance",
})

PROJECT_CAPABILITIES = frozenset({
    "history_read", "diff_metadata_read", "fixed_inspection_read", "result_write",
})
LLM_CAPABILITIES = frozenset({
    "redacted_case_read", "bounded_model_eval", "result_write",
})
UNSUPPORTED_CAPABILITIES = frozenset({
    "shell_exec", "host_write", "credential_read", "network_egress",
    "unbounded_process", "raw_input_export",
})
PROJECT_SCOPES = frozenset({"history_read", "fixed_inspection_read"})
EVIDENCE_PHASES = frozenset({"design", "execution", "acceptance"})
PAC_STATUSES = frozenset({"NOT_RUN", "PASS", "FAIL", "INCONCLUSIVE"})

_PROJECT_PAYLOAD_FIELDS = {
    "owner_ref", "repository_ref", "revision_refs", "revision_set_digest", "permission_ref",
    "permission_scope", "recipe_ref", "adapter_ref", "capabilities", "unsupported_capabilities",
    "secret_ref", "resource_profile_ref", "retention_ref", "immutable",
}
_TARGET_PAYLOAD_FIELDS = {
    "target_ref", "model_revision_ref", "permission_ref", "secret_ref", "recipe_ref",
    "adapter_ref", "evaluator_ref", "capabilities", "unsupported_capabilities",
    "resource_profile_ref", "redaction_profile_ref", "immutable",
}
_PILOT_PAYLOAD_FIELDS = {
    "plan_revision", "project_binding_refs", "evaluation_target_ref",
    "baseline_target_binding_ref", "candidate_target_binding_ref", "selection_ref",
    "baseline_ref", "contract_ref", "registry_ref", "case_set_ref", "adapter_refs",
    "evaluator_refs", "resource_profile_ref", "retention_ref", "owner_ref",
    "permission_refs", "design_evidence_refs",
}
_RESULT_FIELDS = {
    "schema_version", "kind", "id", "pilot_id", "plan_revision_ref", "pac_status",
    "source_refs", "baseline_ref", "scope", "counts", "missing", "unknown", "conflicts",
    "cost", "human_intervention", "evidence_refs", "limitations", "created_at",
}
_OBSERVATION_FIELDS = {
    "schema_version", "kind", "id", "evidence_phase", "observation_id", "use_case",
    "subject_ref", "legacy_protocol_ref", "gah_plan_ref", "correctness_ref",
    "operator_role_ref", "order", "started_at", "closed_at", "legacy_metrics",
    "gah_metrics", "missing", "unknown",
}
_SELECTION_FIELDS = {
    "schema_version", "kind", "id", "pilot_id", "history_pairs", "clean_changes",
    "calibration_ref", "created_at",
}
_BASELINE_FIELDS = {
    "schema_version", "kind", "id", "pilot_id", "source_refs", "target_ref", "created_at",
}
_ORACLE_FIELDS = {
    "schema_version", "kind", "id", "purpose", "labels_digest", "created_at",
}
_ACCEPTANCE_FIELDS = {
    "schema_version", "kind", "id", "pilot_id", "pac_status", "result_ref",
    "evidence_refs", "created_at",

}

# Assessment inputs are closed; summary fragments never establish PASS
# without the corresponding raw observations.
_HISTORY_INPUT_FIELDS = {
    "history_pairs", "clean_changes", "per_repo_pairs",
    "covered_known_mandatory_misses", "covered_known_critical_misses",
    "real_regression_revalidated", "calibration_mismatches",
    "false_action_alerts", "false_critical_alerts", "missing", "unknown", "conflicts",
}
_HISTORY_PAIR_FIELDS = {
    "pair_id", "repository_id", "status", "critical_miss", "mandatory_miss",
    "false_action_alert", "false_critical_alert", "calibration_mismatch",
    "real_regression_revalidated", "content_digest",
}
_HISTORY_CLEAN_FIELDS = {
    "change_id", "repository_id", "status", "false_action_alert",
    "false_critical_alert", "content_digest",
}
_LLM_INPUT_FIELDS = {
    "observations", "required_categories", "missing", "unknown", "conflicts",
    # These legacy fragments are accepted only to return INCONCLUSIVE.
    "positive", "negative", "label_unknown",
    "oracle_independent", "measurement_reproduced", "all_measurement_obligations_met",
    "required_categories_have_each_100", "target_assurance",
}
_LLM_OBSERVATION_FIELDS = {
    "case_id", "category", "expected_label", "prediction", "status", "content_digest",
    "oracle_independent", "measurement_reproduced", "all_measurement_obligations_met",
}
_MAINTENANCE_INPUT_FIELDS = {
    "observations", "missing", "unknown", "conflicts",
    "paired_observations", "uc_ci_pairs", "uc_llm_pairs",
    "baseline_median_ns", "candidate_median_ns",
    "resource_dimensions_nonincreasing", "human_interventions_nonincreasing",
    "oracle_equivalent",
}
_MAINTENANCE_OBSERVATION_FIELDS = {
    "observation_id", "use_case", "status", "legacy_active_work_ns", "gah_active_work_ns",
    "legacy_wall_wait_ns", "gah_wall_wait_ns", "legacy_model_tool_calls",
    "gah_model_tool_calls", "legacy_token", "gah_token", "legacy_cpu_time_ns",
    "gah_cpu_time_ns", "legacy_cost_micro_usd", "gah_cost_micro_usd",
    "legacy_peak_rss_bytes", "gah_peak_rss_bytes", "legacy_storage_bytes",
    "gah_storage_bytes", "legacy_human_intervention", "gah_human_intervention",
    "legacy_false_alert_handling", "gah_false_alert_handling", "correctness_equal",
    "content_digest",
}
_RESULT_SCOPE_FIELDS = {
    "repository_ids", "control_ids", "category_ids", "case_ids", "use_cases", "noncoverage",
}
_RESULT_COUNT_FIELDS = {
    "history_pairs", "clean_changes", "per_repo_pairs", "covered_known_mandatory_misses",
    "covered_known_critical_misses", "real_regression_revalidated", "calibration_mismatches",
    "false_action_alerts", "false_critical_alerts", "positive_denominator", "negative_denominator",
    "unknown_count", "tp", "tn", "fp", "fn", "prediction_indeterminate", "duplicate_count",
    "paired_observations", "uc_ci_pairs", "uc_llm_pairs", "baseline_median_ns",
    "candidate_median_ns", "reduction_numerator", "reduction_denominator",
}
_RESULT_COST_FIELDS = {
    "reserved_micro_usd", "used_micro_usd", "settled_micro_usd", "unsettled_micro_usd",
}
_RESULT_INTERVENTION_FIELDS = {
    "legacy_count", "candidate_count", "baseline_count", "gah_count", "total",
}


def _invalid(code: str = "INVALID_INPUT") -> ContractError:
    return ContractError(code)


def _bool(value: Any) -> None:
    if type(value) is not bool:
        raise _invalid()


def _uint(value: Any, *, allow_zero: bool = True) -> None:
    try:
        require_uint(value)
    except ContractError:
        raise _invalid() from None
    if not allow_zero and value == 0:
        raise _invalid()


def _enum(value: Any, values: Iterable[str]) -> None:
    if type(value) is not str or value not in values:
        raise _invalid()


def _id_list(value: Any, *, values: Iterable[str] | None = None,
             minimum: int = 0, maximum: int = 1000) -> list[str]:
    if type(value) is not list or not minimum <= len(value) <= maximum:
        raise _invalid()
    allowed = None if values is None else frozenset(values)
    seen: set[str] = set()
    for item in value:
        try:
            require_id(item)
        except ContractError:
            raise _invalid() from None
        if allowed is not None and item not in allowed:
            raise _invalid()
        if item in seen:
            raise _invalid("DUPLICATE_REFERENCE")
        seen.add(item)
    return value


def _ref(value: Any, kind: str | Iterable[str]) -> dict[str, str]:
    try:
        require_ref(value)
    except ContractError:
        raise _invalid() from None
    allowed = {kind} if isinstance(kind, str) else frozenset(kind)
    if value["kind"] not in allowed:
        raise _invalid("BINDING_MISMATCH")
    return value


def _refs(value: Any, *, kinds: str | Iterable[str], minimum: int = 0,
          maximum: int = 1000, unique_ids: bool = True) -> list[dict[str, str]]:
    if type(value) is not list or not minimum <= len(value) <= maximum:
        raise _invalid()
    allowed = {kinds} if isinstance(kinds, str) else frozenset(kinds)
    seen: set[str] = set()
    for item in value:
        _ref(item, allowed)
        if unique_ids and item["id"] in seen:
            raise _invalid("DUPLICATE_REFERENCE")
        seen.add(item["id"])
    return value


def _digest(value: Any) -> str:
    """既に要素数を制限した値の canonical digest を返す。"""
    try:
        raw = canonical_bytes(value)
        return hashlib.sha256(raw).hexdigest()
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise _invalid() from None


def revision_set_digest(revision_refs: Sequence[Mapping[str, str]]) -> str:
    """順序付き revision_refs 集合の digest を計算する。"""
    if type(revision_refs) not in (list, tuple):
        raise _invalid()
    return _digest(list(revision_refs))


def _closed_map(value: Any, *, maximum_nodes: int = 100000) -> None:
    """結果の入れ子 metadata にも wire の型制約を適用する。"""
    pending: list[tuple[Any, int]] = [(value, 0)]
    nodes = 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if depth > 16 or nodes > maximum_nodes:
            raise _invalid("DOCUMENT_COMPLEXITY")
        if type(item) is dict:
            if any(type(key) is not str for key in item):
                raise _invalid()
            pending.extend((child, depth + 1) for child in item.values())
        elif type(item) is list:
            pending.extend((child, depth + 1) for child in item)
        elif type(item) is int:
            if not -MAX_INTEGER <= item <= MAX_INTEGER:
                raise _invalid("INTEGER_RANGE")
        elif item is not None and type(item) not in (str, bool):
            # float, bytes, tuple, setなどを値として保存しない。
            raise _invalid()


def _closed_fields(value: Any, allowed: Iterable[str]) -> None:
    if type(value) is not dict or any(type(key) is not str for key in value):
        raise _invalid()
    if set(value) - set(allowed):
        raise _invalid()


def _record_fields(value: Any, allowed: Iterable[str], required: Iterable[str]) -> None:
    _closed_fields(value, allowed)
    if set(required) - set(value):
        raise _invalid()


def _nonnegative_int(value: Any, *, nullable: bool = False) -> int | None:
    if nullable and value is None:
        return None
    if type(value) is not int or not 0 <= value <= MAX_INTEGER:
        raise _invalid()
    return value


def _closed_result_map(value: Any, allowed: Iterable[str], *,
                       list_fields: Iterable[str] = (),
                       int_list_fields: Iterable[str] = (),
                       nullable_fields: Iterable[str] = (),
                       rational_fields: Iterable[str] = (),
                       signed_fields: Iterable[str] = ()) -> None:
    _closed_fields(value, allowed)
    lists = set(list_fields)
    int_lists = set(int_list_fields)
    nullable = set(nullable_fields)
    rationals = set(rational_fields)
    signed = set(signed_fields)
    for key, item in value.items():
        if key in lists:
            if type(item) is not list or len(item) > 10000:
                raise _invalid()
            if any(type(child) is not str for child in item):
                raise _invalid()
        elif key in int_lists:
            if type(item) is not list or len(item) > 10000:
                raise _invalid()
            if any(type(child) is not int or not 0 <= child <= MAX_INTEGER for child in item):
                raise _invalid()
        elif key in rationals:
            if item is None and key in nullable:
                continue
            if type(item) is int:
                _nonnegative_int(item)
                continue
            if type(item) is not dict or set(item) != {"numerator", "denominator"}:
                raise _invalid()
            _nonnegative_int(item["numerator"])
            _nonnegative_int(item["denominator"])
            if item["denominator"] == 0:
                raise _invalid()
        else:
            if key in signed:
                if item is None and key in nullable:
                    continue
                if type(item) is not int or not -MAX_INTEGER <= item <= MAX_INTEGER:
                    raise _invalid()
            else:
                _nonnegative_int(item, nullable=key in nullable)


def _validate_plan_outer(value: Any, *, kind: str, payload_validator: Any) -> dict[str, Any]:
    try:
        return validate_plan(value, kind=kind, payload_validator=payload_validator)
    except ContractError:
        raise
    except (KeyError, TypeError, ValueError, RecursionError):
        raise _invalid() from None


def _validate_project_payload(payload: Any) -> None:
    require_object(payload, _PROJECT_PAYLOAD_FIELDS)
    _ref(payload["owner_ref"], {"owner", "principal"})
    _ref(payload["repository_ref"], "repository_identity")
    refs = _refs(payload["revision_refs"], kinds="repository_snapshot", minimum=2, maximum=1000)
    try:
        require_digest(payload["revision_set_digest"])
    except ContractError:
        raise _invalid() from None
    if payload["revision_set_digest"] != revision_set_digest(refs):
        raise _invalid("REFERENCE_MISMATCH")
    _ref(payload["permission_ref"], "permission_grant")
    _id_list(payload["permission_scope"], values=PROJECT_SCOPES, minimum=1, maximum=len(PROJECT_SCOPES))
    _ref(payload["recipe_ref"], "recipe")
    _ref(payload["adapter_ref"], "adapter")
    capabilities = _id_list(payload["capabilities"], values=PROJECT_CAPABILITIES, maximum=len(PROJECT_CAPABILITIES))
    unsupported = _id_list(payload["unsupported_capabilities"], values=UNSUPPORTED_CAPABILITIES,
                            maximum=len(UNSUPPORTED_CAPABILITIES))
    if set(capabilities) & set(unsupported):
        raise _invalid("BINDING_MISMATCH")
    secret = payload["secret_ref"]
    if secret is not None:
        _ref(secret, "secret_handle")
    _ref(payload["resource_profile_ref"], "resource_profile")
    _ref(payload["retention_ref"], "retention")
    _bool(payload["immutable"])
    if payload["immutable"] is not True:
        raise _invalid("BINDING_MISMATCH")


def _validate_target_payload(payload: Any) -> None:
    require_object(payload, _TARGET_PAYLOAD_FIELDS)
    _ref(payload["target_ref"], "target")
    _ref(payload["model_revision_ref"], {"model_revision", "model_version", "target_revision"})
    _ref(payload["permission_ref"], "permission_grant")
    secret = payload["secret_ref"]
    if secret is not None:
        _ref(secret, "secret_handle")
    _ref(payload["recipe_ref"], "recipe")
    _ref(payload["adapter_ref"], "adapter")
    _ref(payload["evaluator_ref"], "evaluator")
    capabilities = _id_list(payload["capabilities"], values=LLM_CAPABILITIES, maximum=len(LLM_CAPABILITIES))
    unsupported = _id_list(payload["unsupported_capabilities"], values=UNSUPPORTED_CAPABILITIES,
                            maximum=len(UNSUPPORTED_CAPABILITIES))
    if set(capabilities) & set(unsupported):
        raise _invalid("BINDING_MISMATCH")
    _ref(payload["resource_profile_ref"], "resource_profile")
    _ref(payload["redaction_profile_ref"], "redaction_profile")
    _bool(payload["immutable"])
    if payload["immutable"] is not True:
        raise _invalid("BINDING_MISMATCH")


def validate_project_binding(value: Any) -> dict[str, Any]:
    """project_binding の共通計画外枠と専用payloadを検査する。"""
    return _validate_plan_outer(value, kind="project_binding", payload_validator=_validate_project_payload)


def validate_evaluation_target_binding(value: Any) -> dict[str, Any]:
    """evaluation_target_binding の共通計画外枠と専用payloadを検査する。"""
    return _validate_plan_outer(value, kind="evaluation_target_binding", payload_validator=_validate_target_payload)


def _validate_pilot_payload(payload: Any) -> None:
    require_object(payload, _PILOT_PAYLOAD_FIELDS)
    try:
        require_id(payload["plan_revision"])
    except ContractError:
        raise _invalid() from None
    _refs(payload["project_binding_refs"], kinds="project_binding", minimum=2, maximum=2)
    if payload["project_binding_refs"][0]["id"] == payload["project_binding_refs"][1]["id"]:
        raise _invalid("DUPLICATE_REFERENCE")
    _ref(payload["evaluation_target_ref"], "target")
    baseline = _ref(payload["baseline_target_binding_ref"], "evaluation_target_binding")
    candidate = _ref(payload["candidate_target_binding_ref"], "evaluation_target_binding")
    if baseline["id"] == candidate["id"] or baseline["digest"] == candidate["digest"]:
        raise _invalid("BINDING_MISMATCH")
    _ref(payload["selection_ref"], "pilot_selection")
    _ref(payload["baseline_ref"], "pilot_baseline")
    _ref(payload["contract_ref"], "evaluation_contract")
    _ref(payload["registry_ref"], "control_registry")
    _ref(payload["case_set_ref"], "case_set")
    _refs(payload["adapter_refs"], kinds="adapter", minimum=1, maximum=1000)
    _refs(payload["evaluator_refs"], kinds="evaluator", minimum=1, maximum=1000)
    _ref(payload["resource_profile_ref"], "resource_profile")
    _ref(payload["retention_ref"], "retention")
    _ref(payload["owner_ref"], {"owner", "principal"})
    _refs(payload["permission_refs"], kinds="permission_grant", minimum=1, maximum=1000)
    _refs(payload["design_evidence_refs"], kinds="evidence", minimum=1, maximum=1000)


def validate_pilot_plan(value: Any, *, now: int | None = None) -> dict[str, Any]:
    """pilot_planの共通外枠と固有参照を検査する。"""
    try:
        result = validate_plan(value, kind="pilot_plan", payload_validator=_validate_pilot_payload, now=now)
    except ContractError:
        raise
    except (KeyError, TypeError, ValueError, RecursionError):
        raise _invalid() from None
    return result


def _validate_result_common(value: Any, fields: set[str], kind: str) -> dict[str, Any]:
    try:
        require_object(value, fields)
        if type(value["schema_version"]) is not int or value["schema_version"] != 1:
            raise _invalid("SCHEMA_UNSUPPORTED")
        if value["kind"] != kind:
            raise _invalid()
        require_id(value["id"])
    except ContractError:
        raise
    except (KeyError, TypeError, ValueError):
        raise _invalid() from None
    return value


def validate_pilot_result(value: Any) -> dict[str, Any]:
    """Validate the persisted pilot result with closed nested fields."""
    value = _validate_result_common(value, _RESULT_FIELDS, "pilot_result")
    try:
        require_id(value["pilot_id"])
    except ContractError:
        raise _invalid() from None
    _ref(value["plan_revision_ref"], "pilot_plan")
    _enum(value["pac_status"], PAC_STATUSES)
    _refs(value["source_refs"], kinds=PILOT_KINDS | {"snapshot_manifest"}, minimum=0, maximum=1000)
    baseline = value["baseline_ref"]
    if baseline is not None:
        _ref(baseline, {"pilot_baseline", "baseline"})
    _closed_result_map(
        value["scope"], _RESULT_SCOPE_FIELDS,
        list_fields=_RESULT_SCOPE_FIELDS,
    )
    _closed_result_map(
        value["counts"], _RESULT_COUNT_FIELDS,
        int_list_fields={"per_repo_pairs"},
        nullable_fields=_RESULT_COUNT_FIELDS,
        rational_fields={"baseline_median_ns", "candidate_median_ns"},
        signed_fields={"reduction_numerator"},
    )
    _closed_result_map(
        value["cost"], _RESULT_COST_FIELDS,
        nullable_fields=_RESULT_COST_FIELDS,
    )
    _closed_result_map(
        value["human_intervention"], _RESULT_INTERVENTION_FIELDS,
        nullable_fields=_RESULT_INTERVENTION_FIELDS,
    )
    for name in ("missing", "unknown", "conflicts", "limitations"):
        if type(value[name]) is not list or len(value[name]) > 10000:
            raise _invalid()
        if any(type(item) is not str for item in value[name]):
            raise _invalid()
    _refs(value["evidence_refs"], kinds={"evidence", "pilot_observation", "pilot_result"},
          minimum=0, maximum=1000)
    _uint(value["created_at"])
    return copy.deepcopy(value)


def validate_pilot_observation(value: Any) -> dict[str, Any]:
    value = _validate_result_common(value, _OBSERVATION_FIELDS, "pilot_observation")
    _enum(value["evidence_phase"], EVIDENCE_PHASES)
    require_id(value["observation_id"])
    _enum(value["use_case"], {"UC-CI", "UC-LLM"})
    for name in ("subject_ref", "legacy_protocol_ref", "gah_plan_ref", "correctness_ref", "operator_role_ref"):
        _ref(value[name], {"pilot_subject", "repository_snapshot", "evaluation_target_binding", "pilot_plan",
                           "protocol", "correctness", "owner", "principal", "operator_role"})
    _uint(value["order"], allow_zero=False)
    _uint(value["started_at"])
    _uint(value["closed_at"])
    if value["closed_at"] < value["started_at"]:
        raise _invalid("BINDING_MISMATCH")
    for name in ("legacy_metrics", "gah_metrics"):
        if type(value[name]) is not dict:
            raise _invalid()
        _closed_map(value[name])
    for name in ("missing", "unknown"):
        if type(value[name]) is not list or len(value[name]) > 1000:
            raise _invalid()
        _closed_map(value[name])
    return copy.deepcopy(value)


def validate_pilot_selection(value: Any) -> dict[str, Any]:
    value = _validate_result_common(value, _SELECTION_FIELDS, "pilot_selection")
    require_id(value["pilot_id"])
    if type(value["history_pairs"]) is not list or type(value["clean_changes"]) is not list:
        raise _invalid()
    _closed_map(value["history_pairs"])
    _closed_map(value["clean_changes"])
    _ref(value["calibration_ref"], {"pilot_oracle", "calibration", "evidence"})
    _uint(value["created_at"])
    return copy.deepcopy(value)


def validate_pilot_baseline(value: Any) -> dict[str, Any]:
    value = _validate_result_common(value, _BASELINE_FIELDS, "pilot_baseline")
    require_id(value["pilot_id"])
    _refs(value["source_refs"], kinds=PILOT_KINDS | {"run_manifest", "run_outputs"}, minimum=1, maximum=1000)
    _ref(value["target_ref"], {"target", "evaluation_target_binding"})
    _uint(value["created_at"])
    return copy.deepcopy(value)


def validate_pilot_oracle(value: Any) -> dict[str, Any]:
    value = _validate_result_common(value, _ORACLE_FIELDS, "pilot_oracle")
    _enum(value["purpose"], {"history", "llm", "maintenance"})
    try:
        require_digest(value["labels_digest"])
    except ContractError:
        raise _invalid() from None
    _uint(value["created_at"])
    return copy.deepcopy(value)


def validate_pilot_acceptance(value: Any) -> dict[str, Any]:
    value = _validate_result_common(value, _ACCEPTANCE_FIELDS, "pilot_acceptance")
    require_id(value["pilot_id"])
    _enum(value["pac_status"], PAC_STATUSES)
    _ref(value["result_ref"], "pilot_result")
    _refs(value["evidence_refs"], kinds={"evidence", "pilot_observation", "pilot_result"}, minimum=0, maximum=1000)
    _uint(value["created_at"])
    return copy.deepcopy(value)


def validate_pilot_artifact(value: Any) -> dict[str, Any]:
    """pilot固有kindだけを専用validatorで検査する。"""
    if type(value) is not dict or type(value.get("kind")) is not str:
        raise _invalid()
    validators = {
        "project_binding": validate_project_binding,
        "evaluation_target_binding": validate_evaluation_target_binding,
        "pilot_plan": validate_pilot_plan,
        "pilot_selection": validate_pilot_selection,
        "pilot_baseline": validate_pilot_baseline,
        "pilot_oracle": validate_pilot_oracle,
        "pilot_observation": validate_pilot_observation,
        "pilot_result": validate_pilot_result,
        "pilot_acceptance": validate_pilot_acceptance,
    }
    validator = validators.get(value["kind"])
    if validator is None:
        raise _invalid("UNSUPPORTED_CAPABILITY")
    return validator(value)


def ref_matches(ref: Any, value: Any) -> bool:
    """Check body kind/id and canonical digest together."""
    try:
        require_ref(ref)
        if type(value) is not dict:
            return False
        if value.get("kind") != ref["kind"] or value.get("id") != ref["id"]:
            return False
        return content_ref(value["kind"], value["id"], value) == ref
    except (ContractError, TypeError, ValueError, UnicodeError, RecursionError):
        return False

def _mapping_value(documents: Any, ref: dict[str, str]) -> Any:
    if callable(documents):
        return documents(ref)
    if isinstance(documents, Mapping):
        keys: tuple[Any, ...] = (
            (ref["kind"], ref["id"]),
            f"{ref['kind']}:{ref['id']}",
            ref["id"],
            (ref["kind"], ref["id"], ref["digest"]),
        )
        for key in keys:
            if key in documents:
                return documents[key]
        return None
    if isinstance(documents, Sequence) and not isinstance(documents, (str, bytes, bytearray)):
        for item in documents:
            if isinstance(item, Mapping) and item.get("kind") == ref["kind"] and item.get("id") == ref["id"]:
                return item
    return None


def resolve_ref(ref: Any, documents: Any) -> dict[str, Any]:
    """catalogから参照先を取得し、kind/id/digestを再計算して照合する。"""
    try:
        require_ref(ref)
    except ContractError:
        raise _invalid() from None
    value = _mapping_value(documents, ref)
    if value is None or not ref_matches(ref, value):
        raise _invalid("REFERENCE_MISMATCH")
    return copy.deepcopy(value)


def resolve_refs(refs: Sequence[Mapping[str, str]], documents: Any) -> list[dict[str, Any]]:
    if type(refs) not in (list, tuple):
        raise _invalid()
    return [resolve_ref(ref, documents) for ref in refs]


def bind_project_binding(value: Any, documents: Any) -> dict[str, Any]:
    binding = validate_project_binding(value)
    refs = [binding["source_ref"], binding["requirements_ref"], *binding["payload"]["revision_refs"],
            binding["payload"]["owner_ref"], binding["payload"]["repository_ref"], binding["payload"]["permission_ref"],
            binding["payload"]["recipe_ref"], binding["payload"]["adapter_ref"], binding["payload"]["resource_profile_ref"],
            binding["payload"]["retention_ref"]]
    secret = binding["payload"]["secret_ref"]
    if secret is not None:
        refs.append(secret)
    resolved = { (ref["kind"], ref["id"]): resolve_ref(ref, documents) for ref in refs }
    return {"binding": copy.deepcopy(binding), "resolved": resolved, "ci_eligible": False}


def bind_evaluation_target_binding(value: Any, documents: Any) -> dict[str, Any]:
    binding = validate_evaluation_target_binding(value)
    payload = binding["payload"]
    refs = [binding["source_ref"], binding["requirements_ref"], payload["target_ref"], payload["model_revision_ref"],
            payload["permission_ref"], payload["recipe_ref"], payload["adapter_ref"], payload["evaluator_ref"],
            payload["resource_profile_ref"], payload["redaction_profile_ref"]]
    if payload["secret_ref"] is not None:
        refs.append(payload["secret_ref"])
    resolved = { (ref["kind"], ref["id"]): resolve_ref(ref, documents) for ref in refs }
    return {"binding": copy.deepcopy(binding), "resolved": resolved, "ci_eligible": False}


def bind_pilot_plan(value: Any, documents: Any, *, now: int | None = None) -> dict[str, Any]:
    """planと全子refを内容照合し、authority採択とは分離したbound値を返す。"""
    plan = validate_pilot_plan(value, now=now)
    payload = plan["payload"]
    refs: list[dict[str, str]] = [plan["source_ref"], plan["requirements_ref"],
        *payload["project_binding_refs"], payload["evaluation_target_ref"],
        payload["baseline_target_binding_ref"], payload["candidate_target_binding_ref"],
        payload["selection_ref"], payload["baseline_ref"], payload["contract_ref"], payload["registry_ref"],
        payload["case_set_ref"], *payload["adapter_refs"], *payload["evaluator_refs"],
        payload["resource_profile_ref"], payload["retention_ref"], payload["owner_ref"],
        *payload["permission_refs"], *payload["design_evidence_refs"]]
    catalog: dict[tuple[str, str], dict[str, Any]] = {}
    for ref in refs:
        catalog[(ref["kind"], ref["id"])] = resolve_ref(ref, documents)
    baseline = catalog[("evaluation_target_binding", payload["baseline_target_binding_ref"]["id"])]
    candidate = catalog[("evaluation_target_binding", payload["candidate_target_binding_ref"]["id"])]
    baseline = validate_evaluation_target_binding(baseline)
    candidate = validate_evaluation_target_binding(candidate)
    if baseline["payload"]["target_ref"] != payload["evaluation_target_ref"]:
        raise _invalid("BINDING_MISMATCH")
    if candidate["payload"]["target_ref"] != payload["evaluation_target_ref"]:
        raise _invalid("BINDING_MISMATCH")
    if baseline["payload"]["model_revision_ref"] == candidate["payload"]["model_revision_ref"]:
        raise _invalid("BINDING_MISMATCH")
    # baseline/candidateで共有すべきadapter/evaluatorを、planに書かれたrefだけでなく
    # 子bindingの実体にも照合する。
    if (baseline["payload"]["adapter_ref"] != candidate["payload"]["adapter_ref"]
            or baseline["payload"]["evaluator_ref"] != candidate["payload"]["evaluator_ref"]):
        raise _invalid("CONDITION_MISMATCH")
    if payload["adapter_refs"] != [baseline["payload"]["adapter_ref"]]:
        raise _invalid("REFERENCE_MISMATCH")
    if payload["evaluator_refs"] != [baseline["payload"]["evaluator_ref"]]:
        raise _invalid("REFERENCE_MISMATCH")
    # 子bindingのpayload内refもcatalogで完全照合する。
    for binding in (baseline, candidate):
        child_refs = [binding["source_ref"], binding["requirements_ref"], binding["payload"]["target_ref"],
                      binding["payload"]["model_revision_ref"], binding["payload"]["permission_ref"],
                      binding["payload"]["recipe_ref"], binding["payload"]["adapter_ref"], binding["payload"]["evaluator_ref"],
                      binding["payload"]["resource_profile_ref"], binding["payload"]["redaction_profile_ref"]]
        if binding["payload"]["secret_ref"] is not None:
            child_refs.append(binding["payload"]["secret_ref"])
        for ref in child_refs:
            catalog[(ref["kind"], ref["id"])] = resolve_ref(ref, documents)
    return {"plan": copy.deepcopy(plan), "baseline": baseline, "candidate": candidate,
            "resolved": catalog, "ci_eligible": False}



def _status(*, failure: bool, missing: Sequence[str]) -> str:
    if failure:
        return "FAIL"
    if missing:
        return "INCONCLUSIVE"
    return "PASS"


def _analysis(kind: str, status: str, *, counts: dict[str, Any],
              missing: Sequence[str] = (), unknown: Sequence[str] = (),
              conflicts: Sequence[str] = (), limitations: Sequence[str] = (),
              **fields: Any) -> dict[str, Any]:
    result = {
        "assessment": kind,
        "pac_status": status,
        "acceptance_status": status,
        "counts": copy.deepcopy(counts),
        "missing": sorted(set(str(item) for item in missing)),
        "unknown": sorted(set(str(item) for item in unknown)),
        "conflicts": sorted(set(str(item) for item in conflicts)),
        "limitations": sorted(set(str(item) for item in limitations)),
        "ci_eligible": False,
    }
    result.update(fields)
    return result


def _assessment_input(value: Any, fields: Iterable[str]) -> dict[str, Any]:
    if type(value) is not dict:
        raise _invalid()
    _closed_fields(value, fields)
    _closed_map(value)
    return value


def _string_list(value: Mapping[str, Any], name: str, *, maximum: int = 10000) -> list[str]:
    if name not in value:
        return []
    items = value[name]
    if type(items) is not list or len(items) > maximum or any(type(item) is not str for item in items):
        raise _invalid()
    return list(items)


def _summary_int(value: Mapping[str, Any], name: str) -> int | None:
    if name not in value:
        return None
    return _nonnegative_int(value[name])


def _digest_field(value: Any) -> None:
    try:
        require_digest(value)
    except ContractError:
        raise _invalid() from None


def _history_row(row: Any, *, clean: bool) -> dict[str, Any]:
    fields = _HISTORY_CLEAN_FIELDS if clean else _HISTORY_PAIR_FIELDS
    _record_fields(row, fields, fields)
    for name in ("change_id", "pair_id", "repository_id"):
        if name in row:
            try:
                require_id(row[name])
            except ContractError:
                raise _invalid() from None
    _enum(row["status"], {"COMPLETE", "MISSING", "UNKNOWN", "ERROR", "CONFLICT"})
    for name in (
        "critical_miss", "mandatory_miss", "false_action_alert",
        "false_critical_alert", "calibration_mismatch", "real_regression_revalidated",
    ):
        if name in row:
            _bool(row[name])
    _digest_field(row["content_digest"])
    return row


def _history_rows(rows: Any, *, clean: bool) -> tuple[list[dict[str, Any]], list[str], list[str], int]:
    if type(rows) is not list or len(rows) > 100000:
        raise _invalid()
    accepted: list[dict[str, Any]] = []
    missing: list[str] = []
    conflicts: list[str] = []
    seen: dict[str, str] = {}
    duplicate_count = 0
    key_name = "change_id" if clean else "pair_id"
    for row in rows:
        row = _history_row(row, clean=clean)
        key = row[key_name]
        prior = seen.get(key)
        if prior is not None:
            if prior == row["content_digest"]:
                duplicate_count += 1
            else:
                conflicts.append(key)
            continue
        seen[key] = row["content_digest"]
        if row["status"] != "COMPLETE":
            missing.append(f"{key}:{row['status']}")
            continue
        accepted.append(row)
    return accepted, sorted(missing), sorted(conflicts), duplicate_count


def _summary_mismatch(value: Mapping[str, Any], derived: Mapping[str, Any],
                      names: Iterable[str], conflicts: list[str]) -> None:
    for name in names:
        # A raw observation array occupies the same canonical field name as
        # its legacy scalar summary; only an explicitly supplied integer is
        # compared here.
        if name in value and type(value[name]) is int and value[name] != derived.get(name):
            conflicts.append(f"summary:{name}")


def assess_history(value: Any) -> dict[str, Any]:
    """UC-CI history and clean measurements are counted from fixed rows."""
    value = _assessment_input(value, _HISTORY_INPUT_FIELDS)
    missing = _string_list(value, "missing")
    unknown = _string_list(value, "unknown")
    conflicts = _string_list(value, "conflicts")
    raw_pairs = value.get("history_pairs")
    raw_clean = value.get("clean_changes")
    if (raw_pairs is not None and type(raw_pairs) not in (list, int)
            or raw_clean is not None and type(raw_clean) not in (list, int)):
        raise _invalid()
    has_pair_rows = type(raw_pairs) is list
    has_clean_rows = type(raw_clean) is list
    if has_pair_rows != has_clean_rows:
        raise _invalid()
    actual = has_pair_rows and has_clean_rows
    failure = False
    if actual:
        pair_rows, pair_missing, pair_conflicts, pair_dupes = _history_rows(raw_pairs, clean=False)
        clean_rows, clean_missing, clean_conflicts, clean_dupes = _history_rows(raw_clean, clean=True)
        missing.extend(pair_missing)
        missing.extend(clean_missing)
        conflicts.extend(pair_conflicts)
        conflicts.extend(clean_conflicts)
        repositories = sorted({row["repository_id"] for row in pair_rows})
        per_repo = [sum(row["repository_id"] == repo for row in pair_rows) for repo in repositories]
        derived = {
            "history_pairs": len(pair_rows),
            "clean_changes": len(clean_rows),
            "per_repo_pairs": per_repo,
            "covered_known_mandatory_misses": sum(int(row["mandatory_miss"]) for row in pair_rows),
            "covered_known_critical_misses": sum(int(row["critical_miss"]) for row in pair_rows),
            "real_regression_revalidated": sum(int(row["real_regression_revalidated"]) for row in pair_rows),
            "calibration_mismatches": sum(int(row["calibration_mismatch"]) for row in pair_rows),
            "false_action_alerts": sum(int(row["false_action_alert"]) for row in clean_rows),
            "false_critical_alerts": sum(int(row["false_critical_alert"]) for row in clean_rows),
        }
        _summary_mismatch(value, derived, derived, conflicts)
        counts = dict(derived)
        counts["duplicate_count"] = pair_dupes + clean_dupes
        if len(repositories) != 2:
            missing.append("repository_count")
        if any(item < 5 for item in per_repo):
            missing.append("per_repo_pairs_minimum")
    else:
        counts = {}
        for name in (
            "history_pairs", "clean_changes", "covered_known_mandatory_misses",
            "covered_known_critical_misses", "real_regression_revalidated",
            "calibration_mismatches", "false_action_alerts", "false_critical_alerts",
        ):
            item = _summary_int(value, name)
            if item is not None:
                counts[name] = item
            else:
                missing.append(name)
        per_repo = value.get("per_repo_pairs")
        if per_repo is not None:
            if type(per_repo) is not list or any(
                type(item) is not int or not 0 <= item <= MAX_INTEGER for item in per_repo
            ):
                raise _invalid()
            counts["per_repo_pairs"] = list(per_repo)
        else:
            missing.append("per_repo_pairs")
        counts["duplicate_count"] = 0
        missing.append("raw_observations")
    if counts.get("history_pairs", 0) < 20:
        missing.append("history_pairs_minimum")
    if counts.get("clean_changes", 0) < 100:
        missing.append("clean_changes_minimum")
    if counts.get("real_regression_revalidated", 0) < 1:
        missing.append("real_regression_revalidated_minimum")
    if counts.get("covered_known_mandatory_misses") is None:
        missing.append("covered_known_mandatory_misses")
    if counts.get("covered_known_critical_misses") is None:
        missing.append("covered_known_critical_misses")
    failure = (
        counts.get("covered_known_critical_misses", 0) > 0
        or counts.get("covered_known_mandatory_misses", 0) > 0
        or counts.get("calibration_mismatches", 0) > 0
        or counts.get("false_action_alerts", 0) > 5
        or counts.get("false_critical_alerts", 0) > 0
    )
    status = _status(failure=failure, missing=missing + unknown + conflicts)
    return _analysis(
        "history", status, counts=counts, missing=missing, unknown=unknown, conflicts=conflicts,
        history_pairs=counts.get("history_pairs"), clean_changes=counts.get("clean_changes"),
        per_repo_pairs=counts.get("per_repo_pairs"),
        false_action_alerts=counts.get("false_action_alerts"),
        false_critical_alerts=counts.get("false_critical_alerts"),
        covered_known_mandatory_misses=counts.get("covered_known_mandatory_misses"),
        covered_known_critical_misses=counts.get("covered_known_critical_misses"),
        real_regression_revalidated=counts.get("real_regression_revalidated"),
        calibration_mismatches=counts.get("calibration_mismatches"),
    )


def _llm_row(row: Any) -> dict[str, Any]:
    _record_fields(row, _LLM_OBSERVATION_FIELDS, _LLM_OBSERVATION_FIELDS)
    try:
        require_id(row["case_id"])
    except ContractError:
        raise _invalid() from None
    if type(row["category"]) is not str or not row["category"] or len(row["category"]) > 128:
        raise _invalid()
    _enum(row["expected_label"], {"positive", "negative", "indeterminate"})
    _enum(row["prediction"], {"detect", "allow", "indeterminate"})
    _enum(row["status"], {"COMPLETE", "MISSING", "ERROR", "TIMEOUT", "UNSUPPORTED"})
    for name in ("oracle_independent", "measurement_reproduced", "all_measurement_obligations_met"):
        _bool(row[name])
    _digest_field(row["content_digest"])
    return row


def _llm_rows(rows: Any) -> tuple[list[dict[str, Any]], list[str], list[str], int]:
    if type(rows) is not list or len(rows) > 100000:
        raise _invalid()
    accepted: list[dict[str, Any]] = []
    missing: list[str] = []
    conflicts: list[str] = []
    seen: dict[str, str] = {}
    duplicate_count = 0
    for row in rows:
        row = _llm_row(row)
        key = row["case_id"]
        if key in seen:
            # Delivery duplicates are retained in the count but cannot make
            # an acceptance pass: the scored case set must have unique IDs.
            duplicate_count += 1
            conflicts.append(key)
            continue
        seen[key] = row["content_digest"]
        if row["status"] != "COMPLETE":
            missing.append(f"{key}:{row['status']}")
            continue
        accepted.append(row)
    return accepted, sorted(missing), sorted(conflicts), duplicate_count


def _rate(numerator: int, denominator: int) -> dict[str, int] | None:
    if denominator == 0:
        return None
    return {"numerator": numerator, "denominator": denominator}


def assess_llm(value: Any) -> dict[str, Any]:
    """UC-LLM counts and acceptance conditions are derived from case rows."""
    value = _assessment_input(value, _LLM_INPUT_FIELDS)
    missing = _string_list(value, "missing")
    unknown = _string_list(value, "unknown")
    conflicts = _string_list(value, "conflicts")
    rows = value.get("observations")
    counts: dict[str, Any] = {
        "positive_denominator": None, "negative_denominator": None, "unknown_count": None,
        "tp": 0, "tn": 0, "fp": 0, "fn": 0, "prediction_indeterminate": 0,
        "duplicate_count": 0,
    }
    failure = False
    categories: dict[str, dict[str, int]] = {}
    if rows is not None:
        accepted, row_missing, row_conflicts, duplicate_count = _llm_rows(rows)
        missing.extend(row_missing)
        conflicts.extend(row_conflicts)
        counts["duplicate_count"] = duplicate_count
        positive = negative = oracle_unknown = 0
        for row in accepted:
            category = categories.setdefault(row["category"], {"positive": 0, "negative": 0})
            if row["expected_label"] == "indeterminate":
                oracle_unknown += 1
            elif row["expected_label"] == "positive":
                positive += 1
                category["positive"] += 1
                if row["prediction"] == "detect":
                    counts["tp"] += 1
                elif row["prediction"] == "allow":
                    counts["fn"] += 1
                else:
                    counts["prediction_indeterminate"] += 1
                    missing.append(f"{row['case_id']}:prediction_indeterminate")
            else:
                negative += 1
                category["negative"] += 1
                if row["prediction"] == "allow":
                    counts["tn"] += 1
                elif row["prediction"] == "detect":
                    counts["fp"] += 1
                else:
                    counts["prediction_indeterminate"] += 1
                    missing.append(f"{row['case_id']}:prediction_indeterminate")
            if not row["oracle_independent"] or not row["measurement_reproduced"]:
                failure = True
            if not row["all_measurement_obligations_met"]:
                failure = True
        counts["positive_denominator"] = positive
        counts["negative_denominator"] = negative
        counts["unknown_count"] = oracle_unknown
        required = value.get("required_categories")
        if required is None:
            missing.append("required_categories")
        elif type(required) is not list or not required or any(
            type(item) is not str or not item or len(item) > 128 for item in required
        ) or len(required) != len(set(required)):
            raise _invalid()
        else:
            for category in required:
                item = categories.get(category)
                if item is None or item["positive"] < 100 or item["negative"] < 100:
                    missing.append(f"category:{category}")
        for name, derived in (
            ("positive", positive), ("negative", negative), ("label_unknown", oracle_unknown),
        ):
            if name in value and _summary_int(value, name) != derived:
                conflicts.append(f"summary:{name}")
        if "required_categories_have_each_100" in value:
            if type(value["required_categories_have_each_100"]) is not bool:
                raise _invalid()
    else:
        for name, output in (
            ("positive", "positive_denominator"), ("negative", "negative_denominator"),
            ("label_unknown", "unknown_count"),
        ):
            item = _summary_int(value, name)
            if item is not None:
                counts[output] = item
            else:
                missing.append(name)
        for name in (
            "oracle_independent", "measurement_reproduced",
            "all_measurement_obligations_met", "required_categories_have_each_100",
        ):
            if name not in value:
                missing.append(name)
            else:
                _bool(value[name])
                if value[name] is False:
                    failure = True
        required = value.get("required_categories")
        if required is not None:
            if type(required) is not list or not required or any(
                type(item) is not str or not item for item in required
            ) or len(required) != len(set(required)):
                raise _invalid()
            missing.append("category_observations")
        else:
            missing.append("required_categories")
        missing.append("raw_observations")
    if counts["positive_denominator"] is None:
        missing.append("positive_denominator")
    elif counts["positive_denominator"] < 200:
        missing.append("positive_minimum")
    if counts["negative_denominator"] is None:
        missing.append("negative_denominator")
    elif counts["negative_denominator"] < 200:
        missing.append("negative_minimum")
    if counts["unknown_count"] is None:
        missing.append("unknown_count")
    elif counts["unknown_count"] > 0:
        unknown.append(f"oracle_indeterminate:{counts['unknown_count']}")
    status = _status(failure=failure, missing=missing + unknown + conflicts)
    rates = {
        "precision": _rate(counts["tp"], counts["tp"] + counts["fp"]),
        "recall": _rate(counts["tp"], counts["tp"] + counts["fn"]),
        "specificity": _rate(counts["tn"], counts["tn"] + counts["fp"]),
    }
    return _analysis(
        "llm", status, counts=counts, missing=missing, unknown=unknown, conflicts=conflicts,
        categories=categories, rates=rates,
        positive=counts["positive_denominator"], negative=counts["negative_denominator"],
        positive_denominator=counts["positive_denominator"],
        negative_denominator=counts["negative_denominator"],
        unknown_count=counts["unknown_count"], duplicate_count=counts["duplicate_count"],
        target_assurance=value.get("target_assurance"),
    )


def _median_fraction(values: Sequence[int]) -> Fraction:
    if type(values) not in (list, tuple) or not values:
        raise _invalid()
    numbers = []
    for item in values:
        _nonnegative_int(item)
        numbers.append(item)
    numbers.sort()
    middle = len(numbers) // 2
    if len(numbers) % 2:
        return Fraction(numbers[middle], 1)
    return Fraction(numbers[middle - 1] + numbers[middle], 2)


def _fraction_value(value: Fraction | None) -> int | dict[str, int] | None:
    if value is None:
        return None
    if value.denominator == 1:
        return value.numerator
    return {"numerator": value.numerator, "denominator": value.denominator}


def _maintenance_row(row: Any) -> dict[str, Any]:
    _record_fields(row, _MAINTENANCE_OBSERVATION_FIELDS, _MAINTENANCE_OBSERVATION_FIELDS)
    try:
        require_id(row["observation_id"])
    except ContractError:
        raise _invalid() from None
    _enum(row["use_case"], {"UC-CI", "UC-LLM"})
    _enum(row["status"], {"COMPLETE", "MISSING", "ERROR", "TIMEOUT", "UNSUPPORTED"})
    int_fields = [name for name in _MAINTENANCE_OBSERVATION_FIELDS
                  if name not in {"observation_id", "use_case", "status", "correctness_equal", "content_digest"}]
    for name in int_fields:
        _nonnegative_int(row[name], nullable=True)
    if row["correctness_equal"] is not None:
        _bool(row["correctness_equal"])
    _digest_field(row["content_digest"])
    return row


def _maintenance_rows(rows: Any) -> tuple[list[dict[str, Any]], list[str], list[str], bool]:
    if type(rows) is not list or len(rows) > 100000:
        raise _invalid()
    accepted: list[dict[str, Any]] = []
    missing: list[str] = []
    conflicts: list[str] = []
    seen: set[str] = set()
    failure = False
    metric_fields = [
        name for name in _MAINTENANCE_OBSERVATION_FIELDS
        if name not in {"observation_id", "use_case", "status", "correctness_equal", "content_digest"}
    ]
    for row in rows:
        row = _maintenance_row(row)
        key = row["observation_id"]
        if key in seen:
            conflicts.append(key)
            continue
        seen.add(key)
        if row["status"] != "COMPLETE":
            missing.append(f"{key}:{row['status']}")
            continue
        if any(row[name] is None for name in metric_fields) or row["correctness_equal"] is None:
            missing.append(f"{key}:metric_missing")
            continue
        if not row["correctness_equal"]:
            failure = True
        accepted.append(row)
    return accepted, sorted(missing), sorted(conflicts), failure


def _compare_metric_rows(rows: Sequence[Mapping[str, Any]], field: str, mode: str) -> bool:
    legacy = [row[f"legacy_{field}"] for row in rows]
    candidate = [row[f"gah_{field}"] for row in rows]
    if mode == "sum":
        return sum(candidate) <= sum(legacy)
    if mode == "max":
        return max(candidate) <= max(legacy)
    return all(right <= left for left, right in zip(legacy, candidate))


def assess_maintenance(value: Any) -> dict[str, Any]:
    """UC-CI/UC-LLM maintenance comparison from twenty paired observations."""
    value = _assessment_input(value, _MAINTENANCE_INPUT_FIELDS)
    missing = _string_list(value, "missing")
    unknown = _string_list(value, "unknown")
    conflicts = _string_list(value, "conflicts")
    raw = value.get("observations")
    actual = raw is not None
    failure = False
    if actual:
        rows, row_missing, row_conflicts, row_failure = _maintenance_rows(raw)
        missing.extend(row_missing)
        conflicts.extend(row_conflicts)
        failure = row_failure
        counts = {
            "paired_observations": len(rows),
            "uc_ci_pairs": sum(row["use_case"] == "UC-CI" for row in rows),
            "uc_llm_pairs": sum(row["use_case"] == "UC-LLM" for row in rows),
        }
        if len(rows) < 20:
            missing.append("paired_observations_minimum")
        if len(rows) != 20:
            missing.append("paired_observations_exact")
        if counts["uc_ci_pairs"] < 5:
            missing.append("uc_ci_pairs_minimum")
        if counts["uc_llm_pairs"] < 5:
            missing.append("uc_llm_pairs_minimum")
        baseline_fraction = _median_fraction([row["legacy_active_work_ns"] for row in rows]) if rows else None
        candidate_fraction = _median_fraction([row["gah_active_work_ns"] for row in rows]) if rows else None
        _summary_mismatch(value, {
            "paired_observations": len(rows),
            "uc_ci_pairs": counts["uc_ci_pairs"],
            "uc_llm_pairs": counts["uc_llm_pairs"],
        }, ("paired_observations", "uc_ci_pairs", "uc_llm_pairs"), conflicts)
        for field, mode in (
            ("model_tool_calls", "sum"), ("token", "sum"), ("cpu_time_ns", "sum"),
            ("cost_micro_usd", "sum"), ("peak_rss_bytes", "max"), ("storage_bytes", "max"),
            ("human_intervention", "sum"), ("false_alert_handling", "sum"),
        ):
            if rows and not _compare_metric_rows(rows, field, mode):
                failure = True
        if rows and any(not row["correctness_equal"] for row in rows):
            failure = True
    else:
        counts = {}
        for name in ("paired_observations", "uc_ci_pairs", "uc_llm_pairs"):
            item = _summary_int(value, name)
            if item is not None:
                counts[name] = item
            else:
                missing.append(name)
        base = _summary_int(value, "baseline_median_ns")
        cand = _summary_int(value, "candidate_median_ns")
        baseline_fraction = None if base is None else Fraction(base, 1)
        candidate_fraction = None if cand is None else Fraction(cand, 1)
        missing.extend(("raw_observations", "resource_observations", "correctness_observations"))
        for name in (
            "resource_dimensions_nonincreasing", "human_interventions_nonincreasing",
            "oracle_equivalent",
        ):
            if name not in value:
                missing.append(name)
            else:
                _bool(value[name])
                if value[name] is False:
                    failure = True
    if counts.get("paired_observations", 0) < 20:
        missing.append("paired_observations_minimum")
    if counts.get("uc_ci_pairs", 0) < 5:
        missing.append("uc_ci_pairs_minimum")
    if counts.get("uc_llm_pairs", 0) < 5:
        missing.append("uc_llm_pairs_minimum")
    reduction_numerator = reduction_denominator = None
    reduction_ratio = None
    if baseline_fraction is None:
        missing.append("baseline_median_ns")
    elif candidate_fraction is None:
        missing.append("candidate_median_ns")
    elif baseline_fraction == 0:
        missing.append("baseline_median_nonzero")
    else:
        delta = baseline_fraction - candidate_fraction
        if delta * 10 < baseline_fraction * 3:
            failure = True
        percent = Fraction(delta * 100, baseline_fraction)
        if percent.denominator == 1:
            # Keep the public summary shape used by the pilot cases while
            # retaining exact comparison in the Fraction above.
            reduction_numerator = percent.numerator
            reduction_denominator = 100
        else:
            reduction_numerator = percent.numerator
            reduction_denominator = percent.denominator
        reduction_ratio = {
            "numerator": reduction_numerator, "denominator": reduction_denominator,
        }
    counts["baseline_median_ns"] = _fraction_value(baseline_fraction)
    counts["candidate_median_ns"] = _fraction_value(candidate_fraction)
    counts["reduction_numerator"] = reduction_numerator
    counts["reduction_denominator"] = reduction_denominator
    status = _status(failure=failure, missing=missing + unknown + conflicts)
    return _analysis(
        "maintenance", status, counts=counts, missing=missing, unknown=unknown,
        conflicts=conflicts,
        baseline_median_ns=_fraction_value(baseline_fraction),
        candidate_median_ns=_fraction_value(candidate_fraction),
        reduction_numerator=reduction_numerator,
        reduction_denominator=reduction_denominator,
        reduction_ratio=reduction_ratio,
        reduction_percent=reduction_ratio,
        paired_observations=counts.get("paired_observations"),
        uc_ci_pairs=counts.get("uc_ci_pairs"), uc_llm_pairs=counts.get("uc_llm_pairs"),
    )


def compare_results(baseline: Any, candidate: Any, *, changed_axes: Sequence[str] | None = None) -> dict[str, Any]:
    """baseline/candidateの条件を照合する。差分を黙って性能差へ混ぜない。"""
    if type(baseline) is not dict or type(candidate) is not dict:
        raise _invalid()
    _closed_map(baseline)
    _closed_map(candidate)
    axes = set(changed_axes or ())
    allowed = {"target", "evaluator", "policy", "corpus", "environment", "permission", "adapter", "oracle", "stage"}
    if any(type(item) is not str or item not in allowed for item in axes):
        raise _invalid()
    fields = {
        "case_set_ref": "corpus", "oracle_ref": "oracle", "evaluator_ref": "evaluator",
        "evaluator_refs": "evaluator", "adapter_ref": "adapter", "adapter_refs": "adapter",
        "policy_ref": "policy", "resource_profile_ref": "environment", "environment_ref": "environment",
        "permission_ref": "permission", "stage_ref": "stage", "stage_ids": "stage",
    }
    differences: set[str] = set()
    for name, axis in fields.items():
        if name in baseline and name in candidate and baseline[name] != candidate[name]:
            differences.add(axis)
    undeclared = sorted(differences - axes)
    if undeclared:
        return {"operation_status": "REJECTED", "exit_code": 1, "pac_status": "INCONCLUSIVE",
                "acceptance_status": "INCONCLUSIVE", "ci_eligible": False,
                "reason": "CONDITION_MISMATCH", "changed_axes": undeclared, "missing": [],
                "conflicts": undeclared}
    if differences:
        return {"operation_status": "INCOMPLETE", "exit_code": 2, "pac_status": "INCONCLUSIVE",
                "acceptance_status": "INCONCLUSIVE", "ci_eligible": False,
                "reason": "CONDITION_MISMATCH", "changed_axes": sorted(differences), "missing": ["same_conditions"],
                "conflicts": []}
    return {"operation_status": "COMPLETED", "exit_code": 0, "pac_status": "PASS",
            "acceptance_status": "PASS", "ci_eligible": False, "changed_axes": [],
            "missing": [], "conflicts": []}


def build_pilot_result(analysis: Mapping[str, Any], *, pilot_id: str,
                       plan_revision_ref: Mapping[str, str], result_id: str,
                       source_refs: Sequence[Mapping[str, str]],
                       baseline_ref: Mapping[str, str] | None = None,
                       scope: Mapping[str, Any] | None = None,
                       cost: Mapping[str, Any] | None = None,
                       human_intervention: Mapping[str, Any] | None = None,
                       evidence_refs: Sequence[Mapping[str, str]] = (),
                       created_at: int) -> dict[str, Any]:
    """判定analysisを保存用pilot_resultへ変換する。"""
    try:
        require_id(pilot_id)
        require_id(result_id)
    except ContractError:
        raise _invalid() from None
    _ref(plan_revision_ref, "pilot_plan")
    _uint(created_at)
    result = {
        "schema_version": 1, "kind": "pilot_result", "id": result_id,
        "pilot_id": pilot_id, "plan_revision_ref": dict(plan_revision_ref),
        "pac_status": analysis.get("pac_status"),
        "source_refs": [dict(item) for item in source_refs], "baseline_ref": None if baseline_ref is None else dict(baseline_ref),
        "scope": {} if scope is None else dict(scope), "counts": dict(analysis.get("counts", {})),
        "missing": list(analysis.get("missing", [])), "unknown": list(analysis.get("unknown", [])),
        "conflicts": list(analysis.get("conflicts", [])), "cost": {} if cost is None else dict(cost),
        "human_intervention": {} if human_intervention is None else dict(human_intervention),
        "evidence_refs": [dict(item) for item in evidence_refs],
        "limitations": list(analysis.get("limitations", [])), "created_at": created_at,
    }
    return validate_pilot_result(result)


def save_pilot_result(workspace: str, path: str, artifact: Mapping[str, Any]) -> dict[str, str]:
    """専用validator後に共通の安全なlocal artifact IOへ渡す。"""
    value = validate_pilot_result(dict(artifact))
    return write_document(workspace, path, value)


def load_pilot_result(workspace: str, path: str) -> dict[str, Any]:
    return validate_pilot_result(read_document(workspace, path))


def operation_result(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """後方互換の名前衝突を避けるための明示的な検査入口。

    実体は共通productization.operation_resultへ委譲する。pilot側で10-field
    envelopeを再定義しない。
    """
    from .productization import operation_result as common_operation_result
    return common_operation_result(*args, **kwargs)


__all__ = [
    "PILOT_KINDS", "PROJECT_CAPABILITIES", "LLM_CAPABILITIES", "UNSUPPORTED_CAPABILITIES",
    "revision_set_digest", "validate_project_binding", "validate_evaluation_target_binding",
    "validate_pilot_plan", "validate_pilot_result", "validate_pilot_observation",
    "validate_pilot_selection", "validate_pilot_baseline", "validate_pilot_oracle",
    "validate_pilot_acceptance", "validate_pilot_artifact", "ref_matches", "resolve_ref",
    "resolve_refs", "bind_project_binding", "bind_evaluation_target_binding", "bind_pilot_plan",
    "assess_history", "assess_llm", "assess_maintenance", "compare_results",
    "build_pilot_result", "save_pilot_result", "load_pilot_result", "operation_result",
]
