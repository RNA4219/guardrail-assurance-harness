"""Baseline record と comparison context の純粋な構造・binding 検査。

このモジュールはSQLite、採択、OS認証、Evidenceの保存状態を扱わない。返却値の
``structurally_bound`` は渡された値同士の決定的な照合を示すだけで、authorityに
よる採択、由来認証、通常CI利用を示さない。
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable

from .contracts import (
    ContractError,
    MAX_INTEGER,
    require_digest,
    require_id,
    require_object,
    require_ref,
    require_uint,
)
from .run_contracts import bind_run_manifest, content_ref



from .cache_inputs import bind_run_manifest
_RECORD_FIELDS = {
    "schema_version", "kind", "baseline_id", "baseline_series_id", "generation",
    "contract_ref", "policy_ref", "registry_ref", "case_set_ref",
    "target_refs", "evaluator_refs", "oracle_refs", "repeat_config_ref",
    "source_run_ref", "trial_plan_ref", "decision_ref", "evidence_refs",
    "resource_closure_ref", "comparison_context_ref", "created_at", "valid_until",
}
_CONTEXT_FIELDS = {
    "schema_version", "kind", "comparison_id", "mode", "baseline_ref", "reason",
    "changed_axes", "expected_contract_generation", "expected_baseline_generation",
    "contract_ref", "policy_ref", "target_refs", "evaluator_refs", "case_set_ref",
    "oracle_refs", "repeat_config_ref",
}
_REF_KINDS = {
    "contract_ref": "evaluation_contract",
    "policy_ref": "policy_profile",
    "registry_ref": "control_registry",
    "case_set_ref": "case_set",
    "repeat_config_ref": "repeat_config",
    "source_run_ref": "run_manifest",
    "trial_plan_ref": "trial_plan",
    "decision_ref": "run_decision",
    "resource_closure_ref": "resource_closure",
    "comparison_context_ref": "comparison_context",
}
_LIST_KINDS = {
    "target_refs": "target",
    "evaluator_refs": "evaluator",
    "oracle_refs": "oracle",
    "evidence_refs": "evidence",
}
_CHANGED_AXES = {"target"}
_USAGES = {"normal", "baseline_comparison"}
_MAX_REFS = 1000
_FRESHNESS = {"normal": 86400, "baseline_comparison": 30 * 86400}


def _bad(code: str = "INVALID_BASELINE") -> ContractError:
    return ContractError(code)


def _id(value: Any) -> None:
    try:
        require_id(value)
    except ContractError:
        raise _bad() from None


def _uint(value: Any, *, maximum: int = MAX_INTEGER) -> None:
    try:
        require_uint(value, maximum=maximum)
    except ContractError:
        raise _bad() from None


def _ref(value: Any, kind: str) -> None:
    try:
        require_ref(value)
    except ContractError:
        raise _bad("INVALID_REFERENCE") from None
    if value["kind"] != kind:
        raise _bad("REFERENCE_KIND")


def oracle_ref(value: Any) -> None:
    """既存oracleと固定合成policy oracleを、元のkindを保って検査する。"""
    require_ref(value)
    if value["kind"] not in {"oracle", "synthetic_policy_oracle"}:
        raise _bad("REFERENCE_KIND")


def _refs(value: Any, kind: str, *, minimum: int = 1) -> list[dict[str, str]]:
    if type(value) is not list or not minimum <= len(value) <= _MAX_REFS:
        raise _bad("INVALID_REFERENCE_LIST")
    seen: set[str] = set()
    for item in value:
        oracle_ref(item) if kind == "oracle" else _ref(item, kind)
        if item["id"] in seen:
            raise _bad("DUPLICATE_REFERENCE")
        seen.add(item["id"])
    return value


def _canonical_ref(kind: str, identifier: str, value: Any) -> dict[str, str]:
    try:
        return content_ref(kind, identifier, value)
    except (ContractError, KeyError, TypeError, ValueError, RecursionError):
        raise _bad("REFERENCE_CONTENT_INVALID") from None


def _check_ref_content(actual: Any, kind: str, identifier: Any, value: Any) -> None:
    _ref(actual, kind)
    _id(identifier)
    if actual != _canonical_ref(kind, identifier, value):
        raise _bad("REFERENCE_MISMATCH")


def _strict_object(value: Any, fields: set[str]) -> None:
    try:
        require_object(value, fields)
    except ContractError:
        raise _bad("UNKNOWN_FIELD" if type(value) is dict and set(value) != fields else "INVALID_BASELINE") from None


def validate_baseline_record(value: Any) -> dict[str, Any]:
    """`kind=baseline` の不変候補を検査し、入力を変更せずdeep copyを返す。"""
    try:
        _strict_object(value, _RECORD_FIELDS)
        if value["schema_version"] != 1 or type(value["schema_version"]) is not int:
            raise _bad("UNSUPPORTED_VERSION")
        if value["kind"] != "baseline":
            raise _bad("INVALID_KIND")
        _id(value["baseline_id"])
        _id(value["baseline_series_id"])
        if type(value["generation"]) is not int or not 1 <= value["generation"] <= MAX_INTEGER:
            raise _bad("INVALID_GENERATION")
        for field, kind in _REF_KINDS.items():
            _ref(value[field], kind)
        for field, kind in _LIST_KINDS.items():
            _refs(value[field], kind)
        if type(value["created_at"]) is not int or not 0 <= value["created_at"] <= MAX_INTEGER:
            raise _bad("INVALID_TIME")
        if type(value["valid_until"]) is not int or not 0 <= value["valid_until"] <= MAX_INTEGER:
            raise _bad("INVALID_TIME")
        if value["created_at"] > value["valid_until"]:
            raise _bad("INVALID_TIME")
        _canonical_ref("baseline", value["baseline_id"], value)
        return deepcopy(value)
    except ContractError:
        raise
    except (KeyError, TypeError, ValueError, RecursionError):
        raise _bad() from None


def validate_comparison_context(value: Any) -> dict[str, Any]:
    """比較条件の固定構造を検査する。差分軸は初期実装ではtargetだけ許す。"""
    try:
        _strict_object(value, _CONTEXT_FIELDS)
        if type(value["schema_version"]) is not int or value["schema_version"] != 1:
            raise _bad("UNSUPPORTED_VERSION")
        if value["kind"] != "comparison_context":
            raise _bad("INVALID_KIND")
        _id(value["comparison_id"])
        if value["mode"] not in {"required", "not_applicable"} or type(value["mode"]) is not str:
            raise _bad("INVALID_COMPARISON_MODE")
        if value["baseline_ref"] is not None:
            _ref(value["baseline_ref"], "baseline")
        if type(value["reason"]) is not (str if value["mode"] == "not_applicable" else type(None)):
            raise _bad("INVALID_COMPARISON_REASON")
        if value["mode"] == "not_applicable":
            if value["baseline_ref"] is not None or value["reason"] != "initial_baseline_pending":
                raise _bad("INVALID_COMPARISON_CONTEXT")
        elif value["baseline_ref"] is None or value["reason"] is not None:
            raise _bad("INVALID_COMPARISON_CONTEXT")
        axes = value["changed_axes"]
        if type(axes) is not list or len(axes) > 1 or len(set(axes)) != len(axes):
            raise _bad("INVALID_CHANGED_AXES")
        if any(type(axis) is not str or axis not in _CHANGED_AXES for axis in axes):
            raise _bad("UNSUPPORTED_CHANGED_AXIS")
        if type(value["expected_contract_generation"]) is not int or not 1 <= value["expected_contract_generation"] <= MAX_INTEGER:
            raise _bad("INVALID_GENERATION")
        if type(value["expected_baseline_generation"]) is not int or not 0 <= value["expected_baseline_generation"] <= MAX_INTEGER:
            raise _bad("INVALID_GENERATION")
        if value["mode"] == "not_applicable" and (
                value["expected_contract_generation"] != 1
                or value["expected_baseline_generation"] != 0):
            raise _bad("INVALID_INITIAL_GENERATION")
        if value["mode"] == "required" and value["expected_baseline_generation"] < 1:
            raise _bad("INVALID_BASELINE_GENERATION")
        for field, kind in (("contract_ref", "evaluation_contract"), ("policy_ref", "policy_profile"),
                            ("case_set_ref", "case_set"), ("repeat_config_ref", "repeat_config")):
            _ref(value[field], kind)
        for field, kind in (("target_refs", "target"), ("evaluator_refs", "evaluator"),
                            ("oracle_refs", "oracle")):
            _refs(value[field], kind)
        _canonical_ref("comparison_context", value["comparison_id"], value)
        return deepcopy(value)
    except ContractError:
        raise
    except (KeyError, TypeError, ValueError, RecursionError):
        raise _bad() from None


def _list_evidences(evidences: Any) -> list[dict[str, Any]]:
    if type(evidences) is dict:
        values = list(evidences.values())
    elif type(evidences) is list:
        values = evidences
    else:
        raise _bad("INVALID_EVIDENCE_LIST")
    if not 1 <= len(values) <= _MAX_REFS or any(type(item) is not dict for item in values):
        raise _bad("INVALID_EVIDENCE_LIST")
    return values


_EVIDENCE_FIELDS = {
    "schema_version", "kind", "evidence_id", "subject_ref", "conditions_ref",
    "decision_ref", "closure_ref", "purpose", "observed_at", "collected_at",
    "valid_until", "retention_until", "permission_generation", "authority_connected",
    "resource_closure_verified", "input_materialization_verified", "ci_eligible",
}


def _validate_evidence(value: Any, *, now: int) -> tuple[dict[str, Any], int]:
    _strict_object(value, _EVIDENCE_FIELDS)
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise _bad("UNSUPPORTED_VERSION")
    if value["kind"] != "evidence":
        raise _bad("INVALID_KIND")
    _id(value["evidence_id"])
    for field in ("subject_ref", "conditions_ref", "decision_ref", "closure_ref"):
        try:
            require_ref(value[field])
        except ContractError:
            raise _bad("INVALID_EVIDENCE") from None
    if type(value["purpose"]) is not str or value["purpose"] not in _USAGES:
        raise _bad("INVALID_EVIDENCE_USAGE")
    for field in ("observed_at", "collected_at", "retention_until", "valid_until", "permission_generation"):
        if type(value[field]) is not int or not 0 <= value[field] <= MAX_INTEGER:
            raise _bad("INVALID_EVIDENCE_TIME")
    if value["observed_at"] > value["collected_at"] or value["collected_at"] > now:
        raise _bad("INVALID_EVIDENCE_TIME")
    if value["retention_until"] < value["observed_at"] or value["valid_until"] < value["observed_at"]:
        raise _bad("INVALID_EVIDENCE_TIME")
    for field in ("authority_connected", "resource_closure_verified",
                  "input_materialization_verified", "ci_eligible"):
        if type(value[field]) is not bool:
            raise _bad("INVALID_EVIDENCE_STATE")
    if (value["authority_connected"] is not True
            or value["resource_closure_verified"] is not True
            or value["ci_eligible"] is not False):
        raise _bad("INVALID_EVIDENCE_STATE")
    until = min(value["retention_until"], value["valid_until"],
                value["observed_at"] + _FRESHNESS[value["purpose"]])
    if value["observed_at"] > now or now > until:
        raise _bad("EVIDENCE_EXPIRED")
    return deepcopy(value), until


def _bound_object(bound_run: Any, name: str) -> Any:
    if type(bound_run) is not dict or name not in bound_run:
        raise _bad("BINDING_INPUT_MISSING")
    return bound_run[name]


def repeat_config_for_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """反復は保存Planの全trialで固定し、別の自己申告設定を作らない。"""
    plan_ref = _canonical_ref("trial_plan", plan["plan_id"], plan)
    return {"schema_version": 1, "kind": "repeat_config",
            "repeat_config_id": "repeat-" + plan_ref["digest"], "plan_ref": plan_ref}


def _unique_refs_in_cases(case_set: dict[str, Any]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    cases = case_set.get("cases")
    if type(cases) is not list:
        raise _bad("BINDING_INPUT_INVALID")
    for case in cases:
        if type(case) is not dict or "oracle_ref" not in case:
            raise _bad("BINDING_INPUT_INVALID")
        oracle_ref(case["oracle_ref"])
        key = tuple(case["oracle_ref"][item] for item in ("kind", "id", "digest"))
        if key not in seen:
            seen.add(key)
            result.append(deepcopy(case["oracle_ref"]))
    if not result:
        raise _bad("BINDING_INPUT_INVALID")
    return result


def bind_baseline_record(record: Any, *, bound_run: Any, decision: Any,
                         evidences: Any, closure: Any, now: int,
                         evidence_states: Any = None) -> dict[str, Any]:
    """保存済み実体の代わりに渡された構造化値を一度だけ照合する。

    実際のauthority接続、OS主体、保存状態、撤回世代は検査できないため、返却値の
    ``adoption_verified``、``source_binding_verified``、``ci_eligible`` は常にfalse。
    """
    bound_record = validate_baseline_record(record)
    context = validate_comparison_context(_bound_object(bound_run, "comparison_context"))
    _uint(now)
    try:
        manifest = _bound_object(bound_run, "manifest")
        contract = _bound_object(bound_run, "contract")
        policy = _bound_object(bound_run, "policy")
        registry = _bound_object(bound_run, "registry")
        case_set = _bound_object(bound_run, "case_set")
        plan = _bound_object(bound_run, "plan")
        if bound_run.get("ci_eligible") is not False:
            raise _bad("CI_INELIGIBLE")
        if type(manifest) is not dict or manifest.get("kind") != "run_manifest":
            raise _bad("BINDING_INPUT_INVALID")
        for obj, ident in ((contract, "contract_id"), (policy, "policy_id"),
                           (registry, "registry_id"), (case_set, "case_set_id"),
                           (plan, "plan_id")):
            if type(obj) is not dict or type(obj.get(ident)) is not str:
                raise _bad("BINDING_INPUT_INVALID")
        rebound = bind_run_manifest(
            manifest, contract, plan, policy, registry, case_set,
            baseline_context=bound_run.get("baseline_context"),
        )
        core_fields = ("manifest", "contract", "plan", "policy", "registry",
                       "case_set", "selected_controls", "ci_eligible")
        if any(bound_run.get(field) != rebound[field] for field in core_fields):
            raise _bad("BINDING_MISMATCH")
        # reboundはこの呼出しで生成した独立値。hash計算は本文を変更しない。
        core_bound = {field: rebound[field] for field in core_fields}
        bound_bundle_ref = _canonical_ref("bound_bundle", manifest["run_id"], core_bound)
        _check_ref_content(bound_record["contract_ref"], "evaluation_contract", contract["contract_id"], contract)
        _check_ref_content(bound_record["policy_ref"], "policy_profile", policy["policy_id"], policy)
        _check_ref_content(bound_record["registry_ref"], "control_registry", registry["registry_id"], registry)
        _check_ref_content(bound_record["case_set_ref"], "case_set", case_set["case_set_id"], case_set)
        _check_ref_content(bound_record["trial_plan_ref"], "trial_plan", plan["plan_id"], plan)
        repeat_config = _bound_object(bound_run, "repeat_config")
        if repeat_config != repeat_config_for_plan(plan):
            raise _bad("BINDING_INPUT_INVALID")
        _check_ref_content(bound_record["repeat_config_ref"], "repeat_config",
                           repeat_config["repeat_config_id"], repeat_config)
        _check_ref_content(bound_record["source_run_ref"], "run_manifest", manifest["run_id"], manifest)
        _check_ref_content(bound_record["comparison_context_ref"], "comparison_context",
                           context["comparison_id"], context)
        _check_ref_content(context["contract_ref"], "evaluation_contract", contract["contract_id"], contract)
        _check_ref_content(context["policy_ref"], "policy_profile", policy["policy_id"], policy)
        _check_ref_content(context["case_set_ref"], "case_set", case_set["case_set_id"], case_set)
        if context["target_refs"] != bound_record["target_refs"]:
            raise _bad("TARGET_MISMATCH")
        if context["evaluator_refs"] != bound_record["evaluator_refs"]:
            raise _bad("EVALUATOR_MISMATCH")
        if context["oracle_refs"] != bound_record["oracle_refs"]:
            raise _bad("ORACLE_MISMATCH")
        if contract.get("evaluator_refs") != bound_record["evaluator_refs"]:
            raise _bad("EVALUATOR_MISMATCH")
        if _unique_refs_in_cases(case_set) != bound_record["oracle_refs"]:
            raise _bad("ORACLE_MISMATCH")
        if context["repeat_config_ref"] != bound_record["repeat_config_ref"]:
            raise _bad("REPEAT_CONFIG_MISMATCH")
        if manifest.get("target_refs") != bound_record["target_refs"]:
            raise _bad("TARGET_MISMATCH")
        if manifest.get("purpose") == "baseline_candidate":
            if manifest.get("baseline_ref") is not None or context["mode"] != "not_applicable":
                raise _bad("INITIAL_BASELINE_MISMATCH")
            if bound_record["generation"] != 1 or context["expected_contract_generation"] != 1:
                raise _bad("INITIAL_GENERATION_MISMATCH")
            if context["expected_baseline_generation"] != 0:
                raise _bad("INITIAL_GENERATION_MISMATCH")
        elif manifest.get("purpose") == "regression":
            if manifest.get("baseline_ref") is None or context["mode"] != "required":
                raise _bad("BASELINE_REQUIRED")
            if context["baseline_ref"] != manifest["baseline_ref"]:
                raise _bad("BASELINE_MISMATCH")
            if bound_record["generation"] != context["expected_baseline_generation"] + 1:
                raise _bad("GENERATION_MISMATCH")
        else:
            raise _bad("PURPOSE_MISMATCH")
        if contract.get("comparison") != {
            "mode": context["mode"], "baseline_ref": context["baseline_ref"],
            "changed_axes": context["changed_axes"], "reason": context["reason"]}:
            raise _bad("COMPARISON_MISMATCH")
        if type(contract.get("generation")) is not int or not 1 <= contract["generation"] <= MAX_INTEGER:
            raise _bad("CONTRACT_GENERATION_MISMATCH")
        if context["expected_contract_generation"] != contract["generation"]:
            raise _bad("CONTRACT_GENERATION_MISMATCH")
        if type(manifest.get("run_id")) is not str:
            raise _bad("BINDING_INPUT_INVALID")
        if type(decision) is not dict or decision.get("kind") != "run_decision" or decision.get("run_id") != manifest["run_id"]:
            raise _bad("DECISION_MISMATCH")
        decision_id = decision.get("decision_id", decision.get("run_id"))
        _check_ref_content(bound_record["decision_ref"], "run_decision", decision_id, decision)
        if decision.get("ci_eligible") is not False:
            raise _bad("DECISION_NOT_ELIGIBLE")
        if type(closure) is not dict or closure.get("kind") != "resource_closure":
            raise _bad("CLOSURE_MISMATCH")
        closure_id = closure.get("closure_id", closure.get("run_id"))
        if type(closure_id) is not str or closure.get("run_id") != manifest["run_id"]:
            raise _bad("CLOSURE_MISMATCH")
        manifest_ref = _canonical_ref("run_manifest", manifest["run_id"], manifest)
        if (closure.get("manifest_digest") != manifest_ref["digest"]
                or closure.get("budget_closure") is not True):
            raise _bad("CLOSURE_MISMATCH")
        _check_ref_content(bound_record["resource_closure_ref"], "resource_closure", closure_id, closure)
        evidence_values = _list_evidences(evidences)
        if type(evidence_states) is not dict:
            raise _bad("EVIDENCE_STATE_MISSING")
        if set(evidence_states) != {item["evidence_id"] for item in evidence_values}:
            raise _bad("EVIDENCE_STATE_MISMATCH")
        if len(evidence_values) != len(bound_record["evidence_refs"]):
            raise _bad("EVIDENCE_MISMATCH")
        evidence_by_id: dict[str, dict[str, Any]] = {}
        validities: list[int] = []
        for evidence in evidence_values:
            checked, until = _validate_evidence(evidence, now=now)
            identifier = checked["evidence_id"]
            if identifier in evidence_by_id:
                raise _bad("DUPLICATE_EVIDENCE")
            state = evidence_states[identifier]
            if type(state) is not dict or set(state) != {"revoked", "deleted"}:
                raise _bad("EVIDENCE_STATE_MISMATCH")
            if type(state["revoked"]) is not bool or type(state["deleted"]) is not bool:
                raise _bad("EVIDENCE_STATE_MISMATCH")
            if state["revoked"] or state["deleted"]:
                raise _bad("EVIDENCE_UNAVAILABLE")
            if checked["subject_ref"] != bound_record["source_run_ref"]:
                raise _bad("EVIDENCE_SUBJECT_MISMATCH")
            if checked["conditions_ref"] != bound_bundle_ref:
                raise _bad("EVIDENCE_CONDITIONS_MISMATCH")
            if checked["decision_ref"] != bound_record["decision_ref"]:
                raise _bad("EVIDENCE_DECISION_MISMATCH")
            if checked["closure_ref"] != bound_record["resource_closure_ref"]:
                raise _bad("EVIDENCE_CLOSURE_MISMATCH")
            expected_purpose = "baseline_comparison" if manifest["purpose"] == "baseline_candidate" else "normal"
            if checked["purpose"] != expected_purpose:
                raise _bad("EVIDENCE_USAGE_MISMATCH")
            evidence_by_id[identifier] = checked
            validities.append(until)
        if type(evidences) is dict and set(evidences) != set(evidence_by_id):
            raise _bad("EVIDENCE_MISMATCH")
        expected_evidence_refs = [ref["id"] for ref in bound_record["evidence_refs"]]
        if set(expected_evidence_refs) != set(evidence_by_id):
            raise _bad("EVIDENCE_MISMATCH")
        for evidence_ref in bound_record["evidence_refs"]:
            _check_ref_content(evidence_ref, "evidence", evidence_ref["id"],
                               evidence_by_id[evidence_ref["id"]])
        if bound_record["valid_until"] != min(validities):
            raise _bad("VALIDITY_MISMATCH")
        if bound_record["created_at"] > now or bound_record["created_at"] < manifest.get("created_at", 0):
            raise _bad("INVALID_TIME")
        record_ref = _canonical_ref("baseline", bound_record["baseline_id"], bound_record)
        diagnostic_only = decision.get("purpose") == "diagnostic"
        return {
            "schema_version": 1,
            "kind": "baseline_binding",
            "baseline_ref": record_ref,
            "record": deepcopy(bound_record),
            "comparison_context": deepcopy(context),
            "source_run_ref": deepcopy(bound_record["source_run_ref"]),
            "evidence_refs": deepcopy(bound_record["evidence_refs"]),
            "valid_until": min(validities),
            "structurally_bound": True,
            "diagnostic_only": diagnostic_only,
            "adoption_verified": False,
            "source_binding_verified": False,
            "authority_connected": False,
            "ci_eligible": False,
        }
    except ContractError:
        raise
    except (KeyError, TypeError, ValueError, RecursionError):
        raise _bad() from None


__all__ = ["bind_baseline_record", "validate_baseline_record", "validate_comparison_context"]
