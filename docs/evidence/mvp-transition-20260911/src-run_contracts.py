"""評価契約、実行計画、開始Manifestの厳格な構造・参照検査。"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any, Iterable

from .contracts import (
    MAX_DOCUMENT_BYTES,
    MAX_INTEGER,
    ContractError,
    require_digest,
    require_id,
    require_object,
    require_ref,
    require_uint,
)
from .corpus import validate_case_set
from .policy import validate_policy_profile
from .registry import dependency_closure, validate_registry


_EVALUATION_FIELDS = {
    "schema_version", "kind", "contract_id", "generation", "policy_series_id",
    "policy_generation", "policy_ref", "registry_ref", "case_set_ref",
    "calibration_case_set_ref", "evaluator_refs", "required_categories",
    "use_cases", "comparison", "required_outputs",
}
_COMPARISON_FIELDS = {"mode", "baseline_ref", "changed_axes", "reason"}
_PLAN_FIELDS = {"schema_version", "kind", "plan_id", "contract_ref", "entries"}
_ENTRY_FIELDS = {
    "obligation_id", "case_id", "trial_id", "variant", "stage_ids", "required",
    "event_policy", "evaluator_ref", "target_ref",
}
_MANIFEST_FIELDS = {
    "schema_version", "kind", "run_id", "contract_ref", "purpose", "use_cases",
    "target_refs", "control_ids", "baseline_ref", "plan_ref", "policy_ref",
    "profile", "environment_ref", "actor_context_ref", "created_at", "deadline",
}

_USE_CASES = frozenset({"UC-CI", "UC-LLM"})
_OUTPUTS = frozenset({"decision", "evidence", "findings", "plans", "run_receipt"})
_CHANGED_AXES = frozenset({"target", "evaluator", "policy", "corpus", "environment"})
_COMPARISON_MODES = frozenset({"required", "not_applicable"})
_PURPOSES = frozenset({"calibration", "contract_validation", "baseline_candidate", "regression", "diagnostic"})
_PROFILES = frozenset({"pr", "full"})
_VARIANTS = frozenset({"candidate", "baseline"})
_EVENT_POLICIES = frozenset({"forbidden", "aggregate", "none"})
_MAX_EVALUATORS = 1000
_MAX_CATEGORIES = 256
_MAX_USE_CASES = 2
_MAX_CHANGED_AXES = 5
_MAX_ENTRIES = 10000
_MAX_STAGES = 2
_MAX_REFS = 1000


def _invalid(code: str = "INVALID_CONTRACT") -> ContractError:
    """入力内容を含まない固定エラーを作る。"""
    return ContractError(code)


def _enum(value: Any, values: Iterable[str]) -> None:
    if type(value) is not str or value not in values:
        raise _invalid()


def _bool(value: Any) -> None:
    if type(value) is not bool:
        raise _invalid()


def _positive(value: Any) -> None:
    if type(value) is not int or value <= 0 or value > 2**53 - 1:
        raise _invalid()


def _unique_ids(value: Any, *, maximum: int, minimum: int = 1) -> None:
    if type(value) is not list or not minimum <= len(value) <= maximum:
        raise _invalid()
    seen: set[str] = set()
    for item in value:
        require_id(item)
        if item in seen:
            raise _invalid("DUPLICATE_REFERENCE")
        seen.add(item)


def _unique_refs(value: Any, *, kind: str, maximum: int, minimum: int = 1) -> None:
    if type(value) is not list or not minimum <= len(value) <= maximum:
        raise _invalid()
    seen: set[str] = set()
    for item in value:
        _ref(item, kind)
        if item["id"] in seen:
            raise _invalid("DUPLICATE_REFERENCE")
        seen.add(item["id"])


def _canonical(value: Any) -> bytes:
    # 直接渡されたPython値にもwireと同じ制限を適用する。json.dumpsの
    # tuple変換や非文字列keyの文字列化をdigestの定義に含めない。
    pending = [(value, 0)]
    nodes = 0
    while pending:
        current, depth = pending.pop()
        nodes += 1
        if depth > 16 or nodes > 100000:
            raise _invalid("DOCUMENT_COMPLEXITY")
        if type(current) is dict:
            if any(type(key) is not str for key in current):
                raise _invalid()
            pending.extend((child, depth + 1) for child in current.values())
        elif type(current) is list:
            pending.extend((child, depth + 1) for child in current)
        elif type(current) is int:
            if not -MAX_INTEGER <= current <= MAX_INTEGER:
                raise _invalid("INTEGER_RANGE")
        elif current is not None and type(current) not in (str, bool):
            raise _invalid()
    try:
        raw = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise _invalid() from None
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise _invalid("DOCUMENT_SIZE")
    return raw


def content_ref(kind: Any, identifier: Any, value: Any) -> dict[str, str]:
    """canonical UTF-8 bytesからkind/id/digest参照を作る。"""
    require_id(kind)
    require_id(identifier)
    digest = hashlib.sha256(_canonical(value)).hexdigest()
    return {"kind": kind, "id": identifier, "digest": digest}


def _ref(value: Any, kind: str) -> None:
    require_ref(value)
    if value["kind"] != kind:
        raise _invalid("REFERENCE_KIND")


def _same_ref(left: dict[str, str], right: dict[str, str]) -> bool:
    return left == right


def _document_size(value: dict[str, Any]) -> None:
    _canonical(value)


def _validate_comparison(value: Any) -> None:
    require_object(value, _COMPARISON_FIELDS)
    _enum(value["mode"], _COMPARISON_MODES)
    _unique_ids(value["changed_axes"], maximum=_MAX_CHANGED_AXES, minimum=0)
    for axis in value["changed_axes"]:
        _enum(axis, _CHANGED_AXES)
    if value["mode"] == "required":
        _ref(value["baseline_ref"], "baseline")
        if value["reason"] is not None:
            raise _invalid()
    else:
        if value["baseline_ref"] is not None or value["reason"] != "initial_baseline_pending":
            raise _invalid()


def validate_evaluation_contract(value: Any) -> dict[str, Any]:
    """EvaluationContractを検査し、独立したcopyを返す。"""
    try:
        require_object(value, _EVALUATION_FIELDS)
        if type(value["schema_version"]) is not int or value["schema_version"] != 1:
            raise _invalid("UNSUPPORTED_VERSION")
        if value["kind"] != "evaluation_contract":
            raise _invalid()
        require_id(value["contract_id"])
        _positive(value["generation"])
        require_id(value["policy_series_id"])
        _positive(value["policy_generation"])
        _ref(value["policy_ref"], "policy_profile")
        _ref(value["registry_ref"], "control_registry")
        _ref(value["case_set_ref"], "case_set")
        _ref(value["calibration_case_set_ref"], "case_set")
        evaluators = value["evaluator_refs"]
        if type(evaluators) is not list or not 1 <= len(evaluators) <= _MAX_EVALUATORS:
            raise _invalid()
        evaluator_ids: set[str] = set()
        for evaluator in evaluators:
            _ref(evaluator, "evaluator")
            if evaluator["id"] in evaluator_ids:
                raise _invalid("DUPLICATE_REFERENCE")
            evaluator_ids.add(evaluator["id"])
        _unique_ids(value["required_categories"], maximum=_MAX_CATEGORIES)
        _unique_ids(value["use_cases"], maximum=_MAX_USE_CASES)
        for use_case in value["use_cases"]:
            _enum(use_case, _USE_CASES)
        _validate_comparison(value["comparison"])
        outputs = value["required_outputs"]
        if type(outputs) is not list or len(outputs) != len(_OUTPUTS):
            raise _invalid()
        if len(set(outputs)) != len(outputs) or set(outputs) != _OUTPUTS:
            raise _invalid("DUPLICATE_REFERENCE")
        for output in outputs:
            _enum(output, _OUTPUTS)
        _document_size(value)
        return deepcopy(value)
    except ContractError:
        raise
    except (KeyError, TypeError, ValueError, RecursionError):
        raise _invalid() from None


def _validated_inputs(policy: Any, registry: Any, case_set: Any) -> tuple[dict, dict, dict]:
    try:
        return validate_policy_profile(policy), validate_registry(registry), validate_case_set(case_set)
    except (ContractError, ValueError, TypeError, KeyError):
        raise _invalid() from None


def _registry_obligations(registry: dict[str, Any], selected: set[str] | None = None) -> dict[str, tuple[dict, dict]]:
    result: dict[str, tuple[dict, dict]] = {}
    for control in registry["controls"]:
        if selected is not None and control["control_id"] not in selected:
            continue
        for obligation in control["obligations"]:
            result[obligation["obligation_id"]] = (control, obligation)
    return result


def _require_ref_match(actual: dict[str, str], expected_kind: str, expected_value: dict[str, Any]) -> None:
    identifier = next((expected_value[field] for field in ("policy_id", "registry_id", "case_set_id", "contract_id", "plan_id") if field in expected_value), None)
    expected = content_ref(expected_kind, identifier, expected_value)
    if not _same_ref(actual, expected):
        raise _invalid("REFERENCE_MISMATCH")


def _family_use_case(kind: str) -> str:
    if kind in {"constraint", "mutation"}:
        return "UC-CI"
    if kind == "llm_metric":
        return "UC-LLM"
    raise _invalid()


def _check_evaluation_binding(contract: dict, policy: dict, registry: dict, case_set: dict, calibration: dict) -> None:
    if case_set["purpose"] != "acceptance" or calibration["purpose"] != "calibration":
        raise _invalid("PURPOSE_MISMATCH")
    _require_ref_match(contract["policy_ref"], "policy_profile", policy)
    _require_ref_match(contract["registry_ref"], "control_registry", registry)
    _require_ref_match(contract["case_set_ref"], "case_set", case_set)
    _require_ref_match(contract["calibration_case_set_ref"], "case_set", calibration)
    if set(contract["required_categories"]) != set(case_set["required_categories"]):
        raise _invalid("CATEGORY_MISMATCH")
    if contract["policy_series_id"] != policy["policy_id"] or contract["policy_generation"] <= 0:
        raise _invalid("POLICY_MISMATCH")
    obligations = _registry_obligations(registry)
    evaluator_refs = {
        (obligation["evaluator_ref"]["id"], obligation["evaluator_ref"]["digest"])
        for _, obligation in obligations.values()
    }
    declared_evaluators = {(ref["id"], ref["digest"]) for ref in contract["evaluator_refs"]}
    if declared_evaluators != evaluator_refs:
        raise _invalid("EVALUATOR_MISMATCH")
    for use_case in contract["use_cases"]:
        if not any(
            obligation["required"] and _family_use_case(obligation["kind"]) == use_case
            for _, obligation in obligations.values()
        ):
            raise _invalid("REQUIRED_OBLIGATION_MISSING")


def bind_evaluation_contract(contract: Any, policy: Any, registry: Any, case_set: Any, calibration_set: Any) -> dict[str, Any]:
    """全参照と用途を照合したEvaluationContractの独立bindingを返す。"""
    c = validate_evaluation_contract(contract)
    p, r, cs = _validated_inputs(policy, registry, case_set)
    try:
        cal = validate_case_set(calibration_set)
        _check_evaluation_binding(c, p, r, cs, cal)
    except (ContractError, ValueError, TypeError, KeyError):
        raise _invalid() from None
    return {"contract": deepcopy(c), "policy": deepcopy(p), "registry": deepcopy(r),
            "case_set": deepcopy(cs), "calibration_set": deepcopy(cal), "ci_eligible": False}


def _validate_entry(value: Any) -> None:
    require_object(value, _ENTRY_FIELDS)
    require_id(value["obligation_id"])
    require_id(value["case_id"])
    require_id(value["trial_id"])
    _enum(value["variant"], _VARIANTS)
    stages = value["stage_ids"]
    _unique_ids(stages, maximum=_MAX_STAGES)
    _bool(value["required"])
    _enum(value["event_policy"], _EVENT_POLICIES)
    _ref(value["evaluator_ref"], "evaluator")
    _ref(value["target_ref"], "target")


def validate_trial_plan(value: Any) -> dict[str, Any]:
    """TrialPlanを検査し、独立したcopyを返す。"""
    try:
        require_object(value, _PLAN_FIELDS)
        if type(value["schema_version"]) is not int or value["schema_version"] != 1:
            raise _invalid("UNSUPPORTED_VERSION")
        if value["kind"] != "trial_plan":
            raise _invalid()
        require_id(value["plan_id"])
        _ref(value["contract_ref"], "evaluation_contract")
        entries = value["entries"]
        if type(entries) is not list or not 1 <= len(entries) <= _MAX_ENTRIES:
            raise _invalid()
        seen: set[tuple[str, str, str, str]] = set()
        for entry in entries:
            _validate_entry(entry)
            key = (entry["obligation_id"], entry["case_id"], entry["trial_id"], entry["variant"])
            if key in seen:
                raise _invalid("DUPLICATE_ID")
            seen.add(key)
        _document_size(value)
        return deepcopy(value)
    except ContractError:
        raise
    except (KeyError, TypeError, ValueError, RecursionError):
        raise _invalid() from None


def _case_stages(case_set: dict[str, Any]) -> dict[str, list[str]]:
    return {case["case_id"]: [stage["stage_id"] for stage in case["session_steps"]] for case in case_set["cases"]}


def _baseline_targets(context, contract, registry, closure):
    targets = {control["control_id"]: control["target_ref"] for control in registry["controls"]
               if control["control_id"] in closure}
    if context is None:
        return targets
    require_object(context, {"baseline_ref", "targets"})
    _document_size(context)
    _ref(context["baseline_ref"], "baseline")
    if (contract["comparison"]["mode"] != "required"
            or context["baseline_ref"] != contract["comparison"]["baseline_ref"]):
        raise _invalid("BASELINE_MISMATCH")
    if type(context["targets"]) is not list or len(context["targets"]) != len(targets):
        raise _invalid("BASELINE_TARGET_MISMATCH")
    old_targets = {}
    for item in context["targets"]:
        require_object(item, {"control_id", "target_ref"})
        require_id(item["control_id"])
        _ref(item["target_ref"], "target")
        if item["control_id"] not in targets or item["control_id"] in old_targets:
            raise _invalid("BASELINE_TARGET_MISMATCH")
        if item["target_ref"] != targets[item["control_id"]] and "target" not in contract["comparison"]["changed_axes"]:
            raise _invalid("UNDECLARED_TARGET_CHANGE")
        old_targets[item["control_id"]] = deepcopy(item["target_ref"])
    return old_targets


def bind_trial_plan(plan: Any, contract: Any, registry: Any, case_set: Any,
                    selected_controls: Any, target_refs: Any, *, baseline_context: Any = None) -> dict[str, Any]:
    """Control依存閉包、CaseSet段階、candidate/baseline対を照合する。"""
    p = validate_trial_plan(plan)
    c = validate_evaluation_contract(contract)
    r = validate_registry(registry)
    cs = validate_case_set(case_set)
    try:
        _require_ref_match(p["contract_ref"], "evaluation_contract", c)
        if cs["purpose"] != "acceptance":
            raise _invalid("PURPOSE_MISMATCH")
        if type(selected_controls) is not list:
            raise _invalid()
        closure = dependency_closure(r, selected_controls)
        baseline_targets = _baseline_targets(baseline_context, c, r, closure)
        _unique_refs(target_refs, kind="target", maximum=_MAX_REFS)
        expected_targets = {(ref["id"], ref["digest"]) for ref in target_refs}
        stages = _case_stages(cs)
        cases = {case["case_id"]: case for case in cs["cases"]}
        obligations = _registry_obligations(r, set(closure))
        groups: dict[tuple[str, str, str], dict[str, dict]] = {}
        actual_targets: set[tuple[str, str]] = set()
        for entry in p["entries"]:
            if entry["obligation_id"] not in obligations or entry["case_id"] not in cases:
                raise _invalid("UNKNOWN_REFERENCE")
            control, obligation = obligations[entry["obligation_id"]]
            if entry["stage_ids"] != stages[entry["case_id"]]:
                raise _invalid("STAGE_MISMATCH")
            if entry["required"] is not obligation["required"] or entry["event_policy"] != obligation["event_policy"]:
                raise _invalid("OBLIGATION_MISMATCH")
            if entry["evaluator_ref"] != obligation["evaluator_ref"]:
                raise _invalid("EVALUATOR_MISMATCH")
            expected_target = baseline_targets[control["control_id"]] if entry["variant"] == "baseline" else control["target_ref"]
            if entry["target_ref"] != expected_target:
                raise _invalid("TARGET_MISMATCH")
            target_key = (entry["target_ref"]["id"], entry["target_ref"]["digest"])
            if entry["variant"] == "candidate":
                if target_key not in expected_targets:
                    raise _invalid("TARGET_MISMATCH")
                actual_targets.add(target_key)
            key = (entry["obligation_id"], entry["case_id"], entry["trial_id"])
            groups.setdefault(key, {})[entry["variant"]] = entry
        if actual_targets != expected_targets:
            raise _invalid("TARGET_MISMATCH")
        if any(len({entry["case_id"] for entry in p["entries"] if entry["trial_id"] == trial}) > 1
               for trial in {entry["trial_id"] for entry in p["entries"]}):
            raise _invalid("TRIAL_CASE_MISMATCH")
        for key, variants in groups.items():
            if "baseline" in variants and "candidate" not in variants:
                raise _invalid("CANDIDATE_MISSING")
            if c["comparison"]["mode"] == "not_applicable" and "baseline" in variants:
                raise _invalid("BASELINE_NOT_APPLICABLE")
            if c["comparison"]["mode"] == "required" and "candidate" in variants and "baseline" not in variants:
                raise _invalid("BASELINE_MISSING")
            if len(variants) == 2:
                candidate, baseline = variants["candidate"], variants["baseline"]
                if (candidate["stage_ids"] != baseline["stage_ids"]
                        or candidate["evaluator_ref"] != baseline["evaluator_ref"]):
                    raise _invalid("VARIANT_MISMATCH")
        required_obligations = {
            obligation_id for obligation_id, (_, obligation) in obligations.items() if obligation["required"]
        }
        candidate_obligations = {entry["obligation_id"] for entry in p["entries"] if entry["variant"] == "candidate"}
        if not required_obligations.issubset(candidate_obligations):
            raise _invalid("REQUIRED_OBLIGATION_MISSING")
        required_llm = {
            obligation_id for obligation_id, (_, obligation) in obligations.items()
            if obligation["required"] and obligation["kind"] == "llm_metric"
        }
        case_ids = set(cases)
        for obligation_id in required_llm:
            planned = {entry["case_id"] for entry in p["entries"]
                       if entry["obligation_id"] == obligation_id and entry["variant"] == "candidate"}
            if planned != case_ids:
                raise _invalid("CASE_COVERAGE_MISSING")
        return {"plan": deepcopy(p), "contract": deepcopy(c), "registry": deepcopy(r),
                "case_set": deepcopy(cs), "selected_controls": deepcopy(closure),
                "target_refs": deepcopy(target_refs), "ci_eligible": False}
    except ContractError:
        raise
    except (KeyError, TypeError, ValueError, RecursionError):
        raise _invalid() from None


def validate_run_manifest(value: Any) -> dict[str, Any]:
    """RunManifestの構造、型、目的別固定条件を検査する。"""
    try:
        require_object(value, _MANIFEST_FIELDS)
        if type(value["schema_version"]) is not int or value["schema_version"] != 1:
            raise _invalid("UNSUPPORTED_VERSION")
        if value["kind"] != "run_manifest":
            raise _invalid()
        require_id(value["run_id"])
        _ref(value["contract_ref"], "evaluation_contract")
        _enum(value["purpose"], _PURPOSES)
        _unique_ids(value["use_cases"], maximum=_MAX_USE_CASES)
        for item in value["use_cases"]:
            _enum(item, _USE_CASES)
        _unique_refs(value["target_refs"], kind="target", maximum=_MAX_REFS)
        _unique_ids(value["control_ids"], maximum=1000)
        baseline = value["baseline_ref"]
        if baseline is not None:
            _ref(baseline, "baseline")
        _ref(value["plan_ref"], "trial_plan")
        _ref(value["policy_ref"], "policy_profile")
        _enum(value["profile"], _PROFILES)
        _ref(value["environment_ref"], "environment")
        _ref(value["actor_context_ref"], "actor_context")
        require_uint(value["created_at"])
        require_uint(value["deadline"])
        if value["deadline"] <= value["created_at"]:
            raise _invalid("DEADLINE_INVALID")
        _document_size(value)
        return deepcopy(value)
    except ContractError:
        raise
    except (KeyError, TypeError, ValueError, RecursionError):
        raise _invalid() from None


def bind_run_manifest(manifest: Any, contract: Any, plan: Any, policy: Any,
                      registry: Any, case_set: Any, *, baseline_context: Any = None) -> dict[str, Any]:
    """RunManifestを契約・計画・依存閉包・期限へ結び、ci_eligible=falseを返す。"""
    m = validate_run_manifest(manifest)
    c = validate_evaluation_contract(contract)
    p = validate_trial_plan(plan)
    policy_value = validate_policy_profile(policy)
    registry_value = validate_registry(registry)
    case_value = validate_case_set(case_set)
    try:
        _require_ref_match(m["contract_ref"], "evaluation_contract", c)
        _require_ref_match(m["plan_ref"], "trial_plan", p)
        _require_ref_match(m["policy_ref"], "policy_profile", policy_value)
        if set(m["use_cases"]) != set(c["use_cases"]):
            raise _invalid("USE_CASE_MISMATCH")
        if m["target_refs"] == []:
            raise _invalid("TARGET_MISMATCH")
        if m["purpose"] in {"calibration", "contract_validation"}:
            raise _invalid("BOOTSTRAP_REQUIRED")
        if m["purpose"] == "baseline_candidate":
            if m["baseline_ref"] is not None or c["comparison"]["mode"] != "not_applicable" or m["profile"] != "full":
                raise _invalid("BASELINE_CANDIDATE_MISMATCH")
        elif m["purpose"] == "regression":
            if m["baseline_ref"] is None or c["comparison"]["mode"] != "required":
                raise _invalid("BASELINE_REQUIRED")
            if m["baseline_ref"] != c["comparison"]["baseline_ref"]:
                raise _invalid("BASELINE_MISMATCH")
        elif m["purpose"] == "diagnostic":
            if c["comparison"]["mode"] == "required" and m["baseline_ref"] != c["comparison"]["baseline_ref"]:
                raise _invalid("BASELINE_MISMATCH")
            if c["comparison"]["mode"] == "not_applicable" and m["baseline_ref"] is not None:
                raise _invalid("BASELINE_NOT_APPLICABLE")
        elapsed = policy_value["profiles"][m["profile"]]["elapsed_seconds"]
        if m["deadline"] > m["created_at"] + elapsed:
            raise _invalid("DEADLINE_INVALID")
        bound_plan = bind_trial_plan(p, c, registry_value, case_value, m["control_ids"], m["target_refs"], baseline_context=baseline_context)
        return {"manifest": deepcopy(m), "contract": deepcopy(c), "plan": deepcopy(p),
                "policy": deepcopy(policy_value), "registry": deepcopy(registry_value),
                "case_set": deepcopy(case_value), "selected_controls": deepcopy(bound_plan["selected_controls"]),
                "ci_eligible": False}
    except ContractError:
        raise
    except (KeyError, TypeError, ValueError, RecursionError):
        raise _invalid() from None


__all__ = [
    "bind_evaluation_contract", "bind_run_manifest", "bind_trial_plan", "content_ref",
    "validate_evaluation_contract", "validate_run_manifest", "validate_trial_plan",
]
