"""初回baselineから次世代EvaluationContractへの純粋な遷移preflight。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .baselines import repeat_config_for_plan, validate_baseline_record
from .contracts import ContractError
from .corpus import validate_case_set
from .policy import validate_policy_profile
from .registry import validate_registry
from .run_contracts import (
    bind_run_manifest,
    content_ref,
    validate_evaluation_contract,
    validate_run_manifest,
    validate_trial_plan,
)


_CORE_FIELDS = (
    "manifest", "contract", "plan", "policy", "registry",
    "case_set", "selected_controls", "ci_eligible",
)
_SOURCE_FIELDS = set(_CORE_FIELDS)


def _bad(code: str = "INVALID_CONTRACT_TRANSITION") -> ContractError:
    return ContractError(code)


def _ref(value: Any, kind: str, identifier: str, payload: Any) -> None:
    try:
        expected = content_ref(kind, identifier, payload)
    except (ContractError, KeyError, TypeError, ValueError, RecursionError):
        raise _bad("REFERENCE_CONTENT_INVALID") from None
    if value != expected:
        raise _bad("REFERENCE_MISMATCH")


def _oracle_refs(case_set: dict[str, Any]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for case in case_set["cases"]:
        ref = case["oracle_ref"]
        key = (ref["kind"], ref["id"], ref["digest"])
        if key not in seen:
            seen.add(key)
            result.append(deepcopy(ref))
    return result


def _evaluator_refs(registry: dict[str, Any]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for control in registry["controls"]:
        for obligation in control["obligations"]:
            ref = obligation["evaluator_ref"]
            key = (ref["kind"], ref["id"], ref["digest"])
            if key not in seen:
                seen.add(key)
                result.append(deepcopy(ref))
    return result


def _same_without(value: dict[str, Any], fields: set[str]) -> dict[str, Any]:
    return {key: deepcopy(item) for key, item in value.items() if key not in fields}


def _validate_source(source: Any) -> tuple[dict[str, Any], ...]:
    if type(source) is not dict or set(source) != _SOURCE_FIELDS:
        raise _bad("SOURCE_SHAPE")
    try:
        manifest = validate_run_manifest(source["manifest"])
        contract = validate_evaluation_contract(source["contract"])
        plan = validate_trial_plan(source["plan"])
        policy = validate_policy_profile(source["policy"])
        registry = validate_registry(source["registry"])
        case_set = validate_case_set(source["case_set"])
    except (ContractError, KeyError, TypeError, ValueError, RecursionError):
        raise _bad("SOURCE_INVALID") from None
    if source["ci_eligible"] is not False or type(source["selected_controls"]) is not list:
        raise _bad("SOURCE_INVALID")
    return manifest, contract, plan, policy, registry, case_set


def bind_contract_transition(
    previous_contract: Any,
    next_contract: Any,
    *,
    baseline_record: Any,
    baseline_source_bound: Any,
    source_baseline_context: Any = None,
) -> dict[str, Any]:
    """初回baselineを保持した契約更新の構造bindingを返す。

    この関数はDB、採択履歴、OS主体、Evidenceの現在有効性を扱わない。
    baseline_source_boundは保存済み実体から取得した8 core fieldsであり、
    ここでは同じ入力をvalidator/bindへ再投入して内容同値だけを確認する。
    """
    try:
        previous = validate_evaluation_contract(previous_contract)
        if previous['generation'] >= 2:
            from .following_contracts import bind_following_transition
            return bind_following_transition(previous, next_contract, baseline_record=baseline_record,
                baseline_source_bound=baseline_source_bound, source_baseline_context=source_baseline_context)
        if source_baseline_context is not None:
            raise _bad('BASELINE_SOURCE_MISMATCH')
        following = validate_evaluation_contract(next_contract)
        baseline = validate_baseline_record(baseline_record)
        manifest, source_contract, plan, policy, registry, case_set = _validate_source(
            baseline_source_bound
        )
        if source_contract != previous:
            raise _bad("PREVIOUS_CONTRACT_MISMATCH")
        if manifest["purpose"] != "baseline_candidate" or manifest["baseline_ref"] is not None:
            raise _bad("BASELINE_SOURCE_MISMATCH")
        if (previous["generation"] != 1
                or previous["comparison"]["mode"] != "not_applicable"
                or previous["comparison"]["baseline_ref"] is not None
                or previous["comparison"]["reason"] != "initial_baseline_pending"
                or previous["comparison"]["changed_axes"] not in ([], ["target"])):
            raise _bad("PREVIOUS_CONTRACT_MISMATCH")
        if baseline["generation"] != 1:
            raise _bad("BASELINE_GENERATION_MISMATCH")
        try:
            rebound = bind_run_manifest(
                manifest, source_contract, plan, policy, registry, case_set
            )
        except (ContractError, KeyError, TypeError, ValueError, RecursionError):
            raise _bad("SOURCE_BINDING_INVALID") from None
        for field in _CORE_FIELDS:
            if baseline_source_bound[field] != rebound[field]:
                raise _bad("SOURCE_BINDING_MISMATCH")
        if following["contract_id"] == previous["contract_id"]:
            raise _bad("CONTRACT_ID_REUSED")
        if following["generation"] != 2:
            raise _bad("GENERATION_MISMATCH")
        expected_baseline = content_ref("baseline", baseline["baseline_id"], baseline)
        expected_comparison = {
            "mode": "required",
            "baseline_ref": expected_baseline,
            "changed_axes": [],
            "reason": None,
        }
        if following["comparison"] != expected_comparison:
            raise _bad("COMPARISON_MISMATCH")
        if _same_without(following, {"contract_id", "generation", "comparison"}) != _same_without(
            previous, {"contract_id", "generation", "comparison"}
        ):
            raise _bad("UNDECLARED_CONTRACT_CHANGE")
        _ref(baseline["contract_ref"], "evaluation_contract",
             previous["contract_id"], previous)
        _ref(baseline["policy_ref"], "policy_profile", policy["policy_id"], policy)
        _ref(baseline["registry_ref"], "control_registry", registry["registry_id"], registry)
        _ref(baseline["case_set_ref"], "case_set", case_set["case_set_id"], case_set)
        _ref(previous["policy_ref"], "policy_profile", policy["policy_id"], policy)
        _ref(previous["registry_ref"], "control_registry", registry["registry_id"], registry)
        _ref(previous["case_set_ref"], "case_set", case_set["case_set_id"], case_set)
        if previous["required_categories"] != case_set["required_categories"]:
            raise _bad("CATEGORY_MISMATCH")
        if previous["evaluator_refs"] != _evaluator_refs(registry):
            raise _bad("EVALUATOR_MISMATCH")
        _ref(baseline["source_run_ref"], "run_manifest", manifest["run_id"], manifest)
        _ref(baseline["trial_plan_ref"], "trial_plan", plan["plan_id"], plan)
        if baseline["target_refs"] != manifest["target_refs"]:
            raise _bad("TARGET_MISMATCH")
        if baseline["evaluator_refs"] != previous["evaluator_refs"]:
            raise _bad("EVALUATOR_MISMATCH")
        if baseline["oracle_refs"] != _oracle_refs(case_set):
            raise _bad("ORACLE_MISMATCH")
        repeat = repeat_config_for_plan(plan)
        _ref(baseline["repeat_config_ref"], "repeat_config",
             repeat["repeat_config_id"], repeat)
        comparison_context = {
            "schema_version": 1,
            "kind": "comparison_context",
            "comparison_id": baseline["comparison_context_ref"]["id"],
            "mode": previous["comparison"]["mode"],
            "baseline_ref": previous["comparison"]["baseline_ref"],
            "reason": previous["comparison"]["reason"],
            "changed_axes": deepcopy(previous["comparison"]["changed_axes"]),
            "expected_contract_generation": previous["generation"],
            "expected_baseline_generation": 0,
            "contract_ref": deepcopy(baseline["contract_ref"]),
            "policy_ref": deepcopy(baseline["policy_ref"]),
            "target_refs": deepcopy(baseline["target_refs"]),
            "evaluator_refs": deepcopy(baseline["evaluator_refs"]),
            "case_set_ref": deepcopy(baseline["case_set_ref"]),
            "oracle_refs": deepcopy(baseline["oracle_refs"]),
            "repeat_config_ref": deepcopy(baseline["repeat_config_ref"]),
        }
        _ref(baseline["comparison_context_ref"], "comparison_context",
             comparison_context["comparison_id"], comparison_context)
        if baseline["valid_until"] < baseline["created_at"]:
            raise _bad("BASELINE_TIME_INVALID")
        return {
            "schema_version": 1,
            "kind": "contract_transition_binding",
            "previous_contract": deepcopy(previous),
            "next_contract": deepcopy(following),
            "previous_contract_ref": content_ref(
                "evaluation_contract", previous["contract_id"], previous
            ),
            "next_contract_ref": content_ref(
                "evaluation_contract", following["contract_id"], following
            ),
            "source_run_ref": deepcopy(baseline["source_run_ref"]),
            "baseline_ref": deepcopy(expected_baseline),
            "baseline_record": deepcopy(baseline),
            "source_bound": {
                field: deepcopy(rebound[field]) for field in _CORE_FIELDS
            },
            "comparison": deepcopy(expected_comparison),
            "structurally_bound": True,
            "authority_connected": False,
            "adoption_verified": False,
            "ci_eligible": False,
        }
    except ContractError:
        raise
    except (KeyError, TypeError, ValueError, RecursionError):
        raise _bad() from None


__all__ = ["bind_contract_transition"]
