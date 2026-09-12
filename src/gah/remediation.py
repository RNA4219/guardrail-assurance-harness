"""Finding と修復計画の決定的な構造化コア。

この部品は診断理由から Finding と定型 Plan を作るだけで、修正・承認・
保存・外部操作を実行しない。JSON は YAML 1.2 の互換形式として出力できる。
原因候補は仮説または UNKNOWN のまま保持し、再検証の同一条件と新しい有効
Evidenceがauthority境界で現在有効と確認されるまで、公開遷移は再検証候補を
 AWAITING_REVALIDATION に保持し、VERIFIEDへ進めない。
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any

from .contracts import MAX_DOCUMENT_BYTES, MAX_INTEGER, ContractError, require_digest, require_id, require_object, require_ref, require_text, require_uint
from .run_contracts import content_ref

_ASSESSMENT_FIELDS = {
    "schema_version", "request_id", "request_digest", "target_digest",
    "contract_digest", "assessed_at", "purpose", "assurance", "ci_eligible",
    "metrics", "reasons",
}
_ASSESSMENT_METRIC_FIELDS = {
    "metric_id", "name", "value", "baseline_value", "absolute_pass", "delta_pass",
}
_REASON_FIELDS = {"code", "state", "metric_id"}
_CONTEXT_FIELDS = {
    "control_ref", "observation_ref", "comparison_ref", "evidence_refs",
    "recovery_target_ref", "cause_candidates",
}
_CANDIDATE_FIELDS = {"candidate_id", "kind", "summary", "confidence", "evidence_refs"}
_FINDING_FIELDS = {
    "schema_version", "kind", "finding_id", "source_assessment_ref",
    "reason_code", "metric_id", "control_ref", "observation_ref",
    "comparison_ref", "evidence_refs", "category", "cause_class",
    "cause_candidates", "recovery_target_ref", "status", "disposition",
    "disposition_reason", "parent_finding_ref", "verifier_ref", "verified_at",
    "revalidation_candidate", "authority_confirmation_ref", "created_at", "updated_at",
    "ci_eligible",
}
_PLAN_FIELDS = {
    "schema_version", "kind", "plan_id", "finding_ref", "plan_status",
    "execution_status", "cause_candidates", "change_targets",
    "purpose", "preserve_conditions", "revalidation", "rollout",
    "rollback_target_ref", "authority_requirements", "missing_information",
    "created_at", "ci_eligible",
}
_REF_KINDS = {"control", "observation", "comparison", "evidence", "target",
              "assessment", "finding", "actor_context", "policy_profile",
              "evaluation_contract", "control_registry", "case_set", "oracle",
              "evaluator", "adapter", "trial_plan", "repeat_config", "conditions",
              "producer", "subject", "authority_confirmation"}
_CATEGORIES = {"missing", "violation", "degradation", "integrity", "unknown"}
_CAUSE_CLASSES = {"GUARDRAIL", "CHECKER", "EXECUTION", "INTEGRITY", "UNKNOWN"}
_CONFIDENCES = {"UNKNOWN", "HYPOTHESIS", "SUPPORTED"}
_STATUSES = {"OPEN", "IN_PROGRESS", "AWAITING_REVALIDATION", "VERIFIED"}
_DISPOSITIONS = {"NONE", "BASELINE_REVISED", "TARGET_RETIRED"}
_ACTIONS = {"start", "request_revalidation", "verify", "revise_baseline", "retire_target"}
_MAX_REFS = 256
_MAX_ITEMS = 256
_CONDITION_FIELDS = {
    "policy_ref", "contract_ref", "registry_ref", "case_set_ref", "oracle_refs",
    "evaluator_refs", "plan_ref", "repeat_config_ref",
}
_EVIDENCE_CHECK_FIELDS = {
    "evidence_ref", "subject_ref", "conditions_ref", "producer_ref",
    "observed_at", "valid_until", "revoked", "missing",
}
_REVALIDATION_FIELDS = {
    "finding_ref", "changed_target_refs", "original_target_ref", "control_ref",
    "original_conditions", "new_conditions", "evidence", "verifier_ref", "checked_at",
    "origin_binding_verified", "authority_connected",
}


def _bad(code: str = "INVALID_REMEDIATION") -> ContractError:
    return ContractError(code)


def _bool(value: Any) -> None:
    if type(value) is not bool:
        raise _bad()


def _enum(value: Any, values: set[str]) -> None:
    if type(value) is not str or value not in values:
        raise _bad()


def _canonical(value: Any) -> bytes:
    pending = [(value, 0)]
    nodes = 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if depth > 16 or nodes > 100000:
            raise _bad("DOCUMENT_COMPLEXITY")
        if type(item) is dict:
            if any(type(key) is not str for key in item):
                raise _bad()
            pending.extend((child, depth + 1) for child in item.values())
        elif type(item) is list:
            pending.extend((child, depth + 1) for child in item)
        elif type(item) is int:
            if not -MAX_INTEGER <= item <= MAX_INTEGER:
                raise _bad("INTEGER_RANGE")
        elif item is not None and type(item) not in (str, bool):
            raise _bad()
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise _bad() from None
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise _bad("DOCUMENT_SIZE")
    return raw


def _id(value: Any) -> None:
    try:
        require_id(value)
    except ContractError:
        raise _bad() from None


def _digest(value: Any) -> None:
    try:
        require_digest(value)
    except ContractError:
        raise _bad() from None


def _ref(value: Any, kinds: set[str]) -> dict[str, str]:
    try:
        require_ref(value)
    except ContractError:
        raise _bad() from None
    if value["kind"] not in kinds:
        raise _bad("REFERENCE_KIND")
    return deepcopy(value)


def _ref_list(value: Any, kinds: set[str]) -> list[dict[str, str]]:
    if type(value) is not list or not 1 <= len(value) <= _MAX_REFS:
        raise _bad()
    result = [_ref(item, kinds) for item in value]
    if len({(item["kind"], item["id"], item["digest"]) for item in result}) != len(result):
        raise _bad("DUPLICATE_REFERENCE")
    return result


def _validate_assessment(value: Any) -> dict[str, Any]:
    require_object(value, _ASSESSMENT_FIELDS)
    if value["schema_version"] != 1 or type(value["schema_version"]) is not int:
        raise _bad("UNSUPPORTED_VERSION")
    if value["purpose"] != "component_validation" or type(value["purpose"]) is not str:
        raise _bad()
    _id(value["request_id"])
    _digest(value["request_digest"])
    _digest(value["target_digest"])
    _digest(value["contract_digest"])
    try:
        require_uint(value["assessed_at"])
    except ContractError:
        raise _bad() from None
    _enum(value["assurance"], {"HEALTHY", "WARNING", "UNKNOWN", "DEGRADED", "HOLD"})
    _bool(value["ci_eligible"])
    if value["ci_eligible"] is not False:
        raise _bad("CI_INELIGIBLE")
    metrics = value["metrics"]
    if type(metrics) is not list or not metrics or len(metrics) > 100:
        raise _bad()
    for metric in metrics:
        require_object(metric, _ASSESSMENT_METRIC_FIELDS)
        _id(metric["metric_id"])
        _enum(metric["name"], {"recall", "fnr", "fpr", "asr", "mutation_score"})
        for field in ("absolute_pass", "delta_pass"):
            if metric[field] is not None:
                _bool(metric[field])
        for field in ("value", "baseline_value"):
            if metric[field] is not None:
                if (type(metric[field]) is not list or len(metric[field]) != 2
                        or type(metric[field][0]) is not int
                        or type(metric[field][1]) is not int
                        or not 0 <= metric[field][0] <= metric[field][1] <= 10**12):
                    raise _bad()
    reasons = value["reasons"]
    if type(reasons) is not list or len(reasons) > 100:
        raise _bad()
    seen = set()
    metric_ids = {metric["metric_id"] for metric in metrics}
    for reason in reasons:
        require_object(reason, _REASON_FIELDS)
        _id(reason["code"])
        _enum(reason["state"], {"HOLD", "DEGRADED", "UNKNOWN", "WARNING"})
        if reason["metric_id"] is not None:
            _id(reason["metric_id"])
            if reason["metric_id"] not in metric_ids:
                raise _bad("UNKNOWN_REFERENCE")
        identity = (reason["code"], reason["metric_id"])
        if identity in seen:
            raise _bad("DUPLICATE_REASON")
        seen.add(identity)
    _canonical(value)
    return deepcopy(value)


def _validate_candidate(value: Any, evidence: list[dict[str, str]] | None) -> dict[str, Any]:
    require_object(value, _CANDIDATE_FIELDS)
    _id(value["candidate_id"])
    _id(value["kind"])
    require_text(value["summary"], maximum=512)
    _enum(value["confidence"], _CONFIDENCES)
    refs = [] if value["evidence_refs"] == [] else _ref_list(value["evidence_refs"], {"evidence"})
    if value["confidence"] == "SUPPORTED" and not refs:
        raise _bad("CAUSE_EVIDENCE_MISSING")
    if evidence is not None:
        known = {(item["kind"], item["id"], item["digest"]) for item in evidence}
        if any((item["kind"], item["id"], item["digest"]) not in known for item in refs):
            raise _bad("CAUSE_EVIDENCE_MISMATCH")
    return {"candidate_id": value["candidate_id"], "kind": value["kind"],
            "summary": value["summary"], "confidence": value["confidence"],
            "evidence_refs": refs}


def _validate_context(value: Any) -> dict[str, Any]:
    require_object(value, _CONTEXT_FIELDS)
    context = {
        "control_ref": _ref(value["control_ref"], {"control"}),
        "observation_ref": _ref(value["observation_ref"], {"observation"}),
        "comparison_ref": None if value["comparison_ref"] is None else _ref(value["comparison_ref"], {"comparison"}),
        "evidence_refs": _ref_list(value["evidence_refs"], {"evidence"}),
        "recovery_target_ref": None if value["recovery_target_ref"] is None else _ref(value["recovery_target_ref"], {"target"}),
        "cause_candidates": [],
    }
    candidates = value["cause_candidates"]
    if type(candidates) is not list or len(candidates) > _MAX_ITEMS:
        raise _bad()
    for candidate in candidates:
        context["cause_candidates"].append(_validate_candidate(candidate, context["evidence_refs"]))
    if len({candidate["candidate_id"] for candidate in context["cause_candidates"]}) != len(context["cause_candidates"]):
        raise _bad("DUPLICATE_ID")
    return context


def _validate_conditions(value: Any) -> dict[str, Any]:
    require_object(value, _CONDITION_FIELDS)
    normalized = {
        "policy_ref": _ref(value["policy_ref"], {"policy_profile"}),
        "contract_ref": _ref(value["contract_ref"], {"evaluation_contract"}),
        "registry_ref": _ref(value["registry_ref"], {"control_registry"}),
        "case_set_ref": _ref(value["case_set_ref"], {"case_set"}),
        "oracle_refs": _ref_list(value["oracle_refs"], {"oracle"}),
        "evaluator_refs": _ref_list(value["evaluator_refs"], {"evaluator"}),
        "plan_ref": _ref(value["plan_ref"], {"trial_plan"}),
        "repeat_config_ref": _ref(value["repeat_config_ref"], {"repeat_config"}),
    }
    return normalized


def _validate_evidence_check(value: Any) -> dict[str, Any]:
    require_object(value, _EVIDENCE_CHECK_FIELDS)
    normalized = {
        "evidence_ref": _ref(value["evidence_ref"], {"evidence"}),
        "subject_ref": _ref(value["subject_ref"], {"control", "observation", "comparison", "target"}),
        "conditions_ref": _ref(value["conditions_ref"], {"conditions"}),
        "producer_ref": _ref(value["producer_ref"], {"actor_context", "evaluator", "adapter", "producer"}),
    }
    for field in ("observed_at", "valid_until"):
        try:
            require_uint(value[field])
        except ContractError:
            raise _bad() from None
        normalized[field] = value[field]
    if normalized["observed_at"] > normalized["valid_until"]:
        raise _bad("TIME_ORDER")
    for field in ("revoked", "missing"):
        _bool(value[field])
        if value[field] is not False:
            raise _bad("EVIDENCE_UNAVAILABLE")
        normalized[field] = False
    return normalized


def _category(code: str) -> str:
    if code in {"integrity_failure", "future_observation"} or "integrity" in code:
        return "integrity"
    if code in {"required_missing", "critical_missing", "metric_missing", "metric_delta_missing",
                "stale_evidence"} or "missing" in code:
        return "missing"
    if code in {"forbidden_violation", "critical_violation"}:
        return "violation"
    if "threshold" in code:
        return "degradation"
    return "unknown"


def _cause_class(code: str) -> str:
    if "integrity" in code or code in {"future_observation"}:
        return "INTEGRITY"
    if "execution" in code or "timeout" in code:
        return "EXECUTION"
    if "checker" in code or "evaluator" in code:
        return "CHECKER"
    if "guardrail" in code or code in {"forbidden_violation", "critical_violation"}:
        return "GUARDRAIL"
    return "UNKNOWN"


def finding_identity(finding: dict[str, Any]) -> dict[str, Any]:
    # Finding参照はライフサイクル遷移のたびに変わらないID付き投影とする。
    # 状態・時刻・検証候補をdigestへ含めると、自己参照と再配送比較が壊れる。
    stable_fields = (
        "schema_version", "kind", "finding_id", "source_assessment_ref", "reason_code",
        "metric_id", "control_ref", "observation_ref", "comparison_ref", "evidence_refs",
        "category", "cause_class", "cause_candidates", "recovery_target_ref",
        "parent_finding_ref", "ci_eligible",
    )
    return {key: deepcopy(finding[key]) for key in stable_fields}


def _finding_ref(finding: dict[str, Any]) -> dict[str, str]:
    return content_ref("finding", finding["finding_id"], finding_identity(finding))


def _validate_revalidation_candidate(value: Any, finding: dict[str, Any]) -> dict[str, Any]:
    require_object(value, _REVALIDATION_FIELDS)
    expected = _finding_ref(finding)
    if value["finding_ref"] != expected:
        raise _bad("FINDING_MISMATCH")
    targets = _ref_list(value["changed_target_refs"], {"target", "control"})
    if not any(ref["kind"] == "target" for ref in targets):
        raise _bad("TARGET_REQUIRED")
    original_target = _ref(value["original_target_ref"], {"target"})
    control = _ref(value["control_ref"], {"control"})
    if control != finding["control_ref"]:
        raise _bad("CONTROL_MISMATCH")
    original_conditions = _validate_conditions(value["original_conditions"])
    new_conditions = _validate_conditions(value["new_conditions"])
    if original_conditions != new_conditions:
        raise _bad("REVALIDATION_CONTRACT_CHANGED")
    try:
        require_uint(value["checked_at"])
    except ContractError:
        raise _bad() from None
    evidence = value["evidence"]
    if type(evidence) is not list or not 1 <= len(evidence) <= _MAX_ITEMS:
        raise _bad()
    old = {(ref["kind"], ref["id"], ref["digest"]) for ref in finding["evidence_refs"]}
    checked_evidence = []
    seen = set()
    for item in evidence:
        checked = _validate_evidence_check(item)
        if checked["observed_at"] > value["checked_at"]:
            raise _bad("TIME_ORDER")
        if value["checked_at"] > checked["valid_until"]:
            raise _bad("EVIDENCE_EXPIRED")
        allowed_subjects = {
            (finding["control_ref"]["kind"], finding["control_ref"]["id"], finding["control_ref"]["digest"]),
            (finding["observation_ref"]["kind"], finding["observation_ref"]["id"], finding["observation_ref"]["digest"]),
        }
        if finding["comparison_ref"] is not None:
            allowed_subjects.add((finding["comparison_ref"]["kind"], finding["comparison_ref"]["id"], finding["comparison_ref"]["digest"]))
        allowed_subjects.update(
            (ref["kind"], ref["id"], ref["digest"])
            for ref in targets + [original_target]
        )
        subject_key = tuple(checked["subject_ref"][key] for key in ("kind", "id", "digest"))
        if subject_key not in allowed_subjects:
            raise _bad("EVIDENCE_SUBJECT_MISMATCH")
        expected_conditions_ref = content_ref(
            "conditions", checked["conditions_ref"]["id"], original_conditions
        )
        if checked["conditions_ref"] != expected_conditions_ref:
            raise _bad("CONDITIONS_MISMATCH")
        identity = tuple(checked["evidence_ref"][key] for key in ("kind", "id", "digest"))
        if identity in old or identity in seen:
            raise _bad("NEW_EVIDENCE_REQUIRED")
        seen.add(identity)
        checked_evidence.append(checked)
    verifier = _ref(value["verifier_ref"], {"actor_context"})
    try:
        require_uint(value["checked_at"])
    except ContractError:
        raise _bad() from None
    if value["checked_at"] < finding["updated_at"]:
        raise _bad("TIME_ORDER")
    for field in ("origin_binding_verified", "authority_connected"):
        _bool(value[field])
        if value[field] is not False:
            raise _bad("AUTHORITY_REQUIRED")
    return {
        "finding_ref": expected,
        "changed_target_refs": targets,
        "original_target_ref": original_target,
        "control_ref": control,
        "original_conditions": original_conditions,
        "new_conditions": new_conditions,
        "evidence": checked_evidence,
        "verifier_ref": verifier,
        "checked_at": value["checked_at"],
        "origin_binding_verified": False,
        "authority_connected": False,
    }


def validate_finding(value: Any) -> dict[str, Any]:
    require_object(value, _FINDING_FIELDS)
    if value["schema_version"] != 1 or type(value["schema_version"]) is not int:
        raise _bad("UNSUPPORTED_VERSION")
    if value["kind"] != "finding":
        raise _bad()
    _id(value["finding_id"])
    _ref(value["source_assessment_ref"], {"assessment"})
    _id(value["reason_code"])
    if value["metric_id"] is not None:
        _id(value["metric_id"])
    _ref(value["control_ref"], {"control"})
    _ref(value["observation_ref"], {"observation"})
    if value["comparison_ref"] is not None:
        _ref(value["comparison_ref"], {"comparison"})
    evidence = _ref_list(value["evidence_refs"], {"evidence"})
    _enum(value["category"], _CATEGORIES)
    _enum(value["cause_class"], _CAUSE_CLASSES)
    _bool(value["ci_eligible"])
    if value["ci_eligible"] is not False:
        raise _bad("CI_INELIGIBLE")
    candidates = value["cause_candidates"]
    if type(candidates) is not list or len(candidates) > _MAX_ITEMS:
        raise _bad()
    for candidate in candidates:
        _validate_candidate(candidate, evidence)
    if value["recovery_target_ref"] is not None:
        _ref(value["recovery_target_ref"], {"target"})
    for field in ("created_at", "updated_at"):
        try:
            require_uint(value[field])
        except ContractError:
            raise _bad() from None
    if value["updated_at"] < value["created_at"]:
        raise _bad("TIME_ORDER")
    _enum(value["status"], _STATUSES)
    if value["status"] == "VERIFIED":
        # 認証済みreceiptを照合する上位authorityの専用入口が必要。
        raise _bad("AUTHORITY_UNAVAILABLE")
    _enum(value["disposition"], _DISPOSITIONS)
    if value["disposition"] == "NONE":
        if value["disposition_reason"] is not None:
            raise _bad("DISPOSITION_STATE")
    else:
        if type(value["disposition_reason"]) is not str:
            raise _bad("DISPOSITION_REASON_MISSING")
        try:
            require_text(value["disposition_reason"], maximum=512)
        except ContractError:
            raise _bad() from None
    if value["parent_finding_ref"] is not None:
        _ref(value["parent_finding_ref"], {"finding"})
    if value["verifier_ref"] is not None:
        _ref(value["verifier_ref"], {"actor_context"})
    if value["verified_at"] is not None:
        try:
            require_uint(value["verified_at"])
        except ContractError:
            raise _bad() from None
    if value["status"] == "VERIFIED":
        if value["verifier_ref"] is None or value["verified_at"] is None:
            raise _bad("VERIFICATION_MISSING")
        if value["verified_at"] < value["created_at"] or value["verified_at"] > value["updated_at"]:
            raise _bad("TIME_ORDER")
    elif value["verifier_ref"] is not None or value["verified_at"] is not None:
        raise _bad("VERIFICATION_STATE")
    if value["revalidation_candidate"] is not None:
        _validate_revalidation_candidate(value["revalidation_candidate"], value)
    if value["authority_confirmation_ref"] is not None:
        _ref(value["authority_confirmation_ref"], {"authority_confirmation"})
    if value["status"] == "VERIFIED":
        if value["authority_confirmation_ref"] is None:
            raise _bad("AUTHORITY_CONFIRMATION_REQUIRED")
    elif value["authority_confirmation_ref"] is not None:
        raise _bad("AUTHORITY_CONFIRMATION_STATE")
    _canonical(value)
    return deepcopy(value)


def finding_ref(value: Any) -> dict[str, str]:
    """ライフサイクル状態に依存しないFinding参照を返す。"""
    return _finding_ref(validate_finding(value))


def generate_findings(assessment: Any, *, context: Any) -> list[dict[str, Any]]:
    """判定理由をFindingへ結ぶ。理由がなければ空配列を返す。"""
    assessed = _validate_assessment(assessment)
    bound = _validate_context(context)
    source_ref = content_ref("assessment", assessed["request_id"], assessed)
    findings = []
    for reason in assessed["reasons"]:
        seed = {
            "source": source_ref, "reason_code": reason["code"],
            "metric_id": reason["metric_id"], "control": bound["control_ref"],
            "observation": bound["observation_ref"], "comparison": bound["comparison_ref"],
            "evidence": bound["evidence_refs"],
        }
        finding_id = "finding-" + hashlib.sha256(_canonical(seed)).hexdigest()[:32]
        candidates = deepcopy(bound["cause_candidates"])
        if not candidates:
            candidates = [{
                "candidate_id": "cause-unknown",
                "kind": "unknown",
                "summary": "原因は未特定",
                "confidence": "UNKNOWN",
                "evidence_refs": [],
            }]
        finding = {
            "schema_version": 1, "kind": "finding", "finding_id": finding_id,
            "source_assessment_ref": source_ref, "reason_code": reason["code"],
            "metric_id": reason["metric_id"], "control_ref": bound["control_ref"],
            "observation_ref": bound["observation_ref"],
            "comparison_ref": bound["comparison_ref"],
            "evidence_refs": deepcopy(bound["evidence_refs"]),
            "category": _category(reason["code"]), "cause_class": _cause_class(reason["code"]),
            "cause_candidates": candidates,
            "recovery_target_ref": deepcopy(bound["recovery_target_ref"]),
            "status": "OPEN", "disposition": "NONE", "parent_finding_ref": None,
            "disposition_reason": None,
            "verifier_ref": None, "verified_at": None,
            "revalidation_candidate": None, "authority_confirmation_ref": None,
            "ci_eligible": False,
            "created_at": assessed["assessed_at"], "updated_at": assessed["assessed_at"],
        }
        findings.append(validate_finding(finding))
    return findings


def _validate_condition(value: Any) -> dict[str, Any]:
    require_object(value, {"condition_id", "description", "required"})
    _id(value["condition_id"])
    require_text(value["description"], maximum=512)
    _bool(value["required"])
    return deepcopy(value)


def _validate_step(value: Any) -> dict[str, Any]:
    require_object(value, {"step_id", "description"})
    _id(value["step_id"])
    require_text(value["description"], maximum=512)
    return deepcopy(value)


def _validate_plan(value: Any) -> dict[str, Any]:
    require_object(value, _PLAN_FIELDS)
    if value["schema_version"] != 1 or type(value["schema_version"]) is not int:
        raise _bad("UNSUPPORTED_VERSION")
    if value["kind"] != "remediation_plan":
        raise _bad()
    _id(value["plan_id"])
    finding_ref = _ref(value["finding_ref"], {"finding"})
    _enum(value["plan_status"], {"UNASSESSED", "READY", "NEEDS_INFORMATION"})
    if value["execution_status"] != "NOT_EXECUTED":
        raise _bad("EXECUTION_FORBIDDEN")
    _bool(value["ci_eligible"])
    if value["ci_eligible"] is not False:
        raise _bad("CI_INELIGIBLE")
    candidates = value["cause_candidates"]
    if type(candidates) is not list or len(candidates) > _MAX_ITEMS:
        raise _bad()
    for candidate in candidates:
        _validate_candidate(candidate, None)
    targets = _ref_list(value["change_targets"], {"target", "control"})
    require_text(value["purpose"], maximum=512)
    conditions = value["preserve_conditions"]
    if type(conditions) is not list or not 1 <= len(conditions) <= _MAX_ITEMS:
        raise _bad()
    for condition in conditions:
        _validate_condition(condition)
    revalidation = value["revalidation"]
    require_object(revalidation, {"same_binding", "same_conditions", "threshold_unchanged",
                                  "inspection_preserved", "required_evidence_refs",
                                  "verifier_ref", "steps"})
    for field in ("same_binding", "same_conditions", "threshold_unchanged", "inspection_preserved"):
        _bool(revalidation[field])
        if revalidation[field] is not True:
            raise _bad("REVALIDATION_CONTRACT_CHANGED")
    required_evidence = _ref_list(revalidation["required_evidence_refs"], {"evidence"})
    verifier = None if revalidation["verifier_ref"] is None else _ref(revalidation["verifier_ref"], {"actor_context"})
    steps = revalidation["steps"]
    if type(steps) is not list or not 1 <= len(steps) <= _MAX_ITEMS:
        raise _bad()
    for step in steps:
        _validate_step(step)
    rollout = value["rollout"]
    require_object(rollout, {"steps", "requires_authorization"})
    rollout_steps = rollout["steps"]
    if type(rollout_steps) is not list or len(rollout_steps) > _MAX_ITEMS:
        raise _bad()
    for step in rollout_steps:
        _validate_step(step)
    _bool(rollout["requires_authorization"])
    rollback = value["rollback_target_ref"]
    if rollback is not None:
        _ref(rollback, {"target"})
    authority = value["authority_requirements"]
    if type(authority) is not list or len(authority) > _MAX_ITEMS:
        raise _bad()
    for item in authority:
        require_object(item, {"role", "action", "reason"})
        _id(item["role"]); _id(item["action"]); require_text(item["reason"], maximum=512)
    missing = value["missing_information"]
    if type(missing) is not list or len(missing) > _MAX_ITEMS:
        raise _bad()
    for item in missing:
        require_text(item, maximum=512)
    try:
        require_uint(value["created_at"])
    except ContractError:
        raise _bad() from None
    if value["plan_status"] == "READY" and missing:
        raise _bad("MISSING_INFORMATION")
    _canonical(value)
    return deepcopy(value)


def generate_plan(
    finding: Any,
    *,
    change_targets: Any,
    purpose: str,
    preserve_conditions: Any = None,
    revalidation: Any = None,
    rollout: Any = None,
    rollback_target_ref: Any = None,
    authority_requirements: Any = None,
    missing_information: Any = None,
    created_at: int | None = None,
) -> dict[str, Any]:
    """Findingから実行しない定型Planを作る。"""
    source = validate_finding(finding)
    targets = _ref_list(change_targets, {"target", "control"})
    if type(purpose) is not str:
        raise _bad()
    if authority_requirements is None:
        authority_requirements = []
    if missing_information is None:
        missing_information = []
    if type(authority_requirements) is not list or type(missing_information) is not list:
        raise _bad()
    if preserve_conditions is None:
        preserve_conditions = [{
            "condition_id": "preserve-original-contract",
            "description": "元のbinding、閾値、検査条件を維持する",
            "required": True,
        }]
    if type(revalidation) is not dict and revalidation is not None:
        raise _bad()
    if revalidation is None:
        revalidation = {
            "same_binding": True,
            "same_conditions": True,
            "threshold_unchanged": True,
            "inspection_preserved": True,
            "required_evidence_refs": deepcopy(source["evidence_refs"]),
            "verifier_ref": None,
            "steps": [{
                "step_id": "revalidate-original-condition",
                "description": "元の条件で再検査し、新しいEvidenceを収集する",
            }],
        }
    if rollout is None:
        rollout = {
            "steps": [{
                "step_id": "authorized-review",
                "description": "承認済みの変更を展開する前に内容を確認する",
            }],
            "requires_authorization": True,
        }
    missing_information = deepcopy(missing_information)
    if source["recovery_target_ref"] is None and rollback_target_ref is None:
        # 復旧先を入力から推測せず、計画の不足情報として明示する。
        if "復旧先が不明" not in missing_information:
            missing_information.append("復旧先が不明")
    if revalidation.get("verifier_ref") is None:
        if "確認者が未指定" not in missing_information:
            missing_information.append("確認者が未指定")
    # Plan生成時に原因候補のEvidenceを新規追加できないよう、Findingの集合に固定する。
    known_evidence = {(ref["kind"], ref["id"], ref["digest"]) for ref in source["evidence_refs"]}
    candidates = deepcopy(source["cause_candidates"])
    for candidate in candidates:
        for ref in candidate["evidence_refs"]:
            if (ref["kind"], ref["id"], ref["digest"]) not in known_evidence:
                raise _bad("CAUSE_EVIDENCE_MISMATCH")
    if created_at is None:
        created_at = source["updated_at"]
    try:
        require_uint(created_at)
    except ContractError:
        raise _bad() from None
    plan_status = "NEEDS_INFORMATION" if missing_information else "READY"
    seed = {
        "schema_version": 1, "kind": "remediation_plan", "finding_ref": _finding_ref(source),
        "plan_status": plan_status, "execution_status": "NOT_EXECUTED",
        "cause_candidates": candidates, "change_targets": targets, "purpose": purpose,
        "preserve_conditions": preserve_conditions, "revalidation": revalidation,
        "rollout": rollout, "rollback_target_ref": rollback_target_ref,
        "authority_requirements": authority_requirements,
        "missing_information": missing_information, "created_at": created_at,
        "ci_eligible": False,
    }
    plan_id = "plan-" + hashlib.sha256(_canonical(seed)).hexdigest()[:32]
    plan = {
        "schema_version": 1, "kind": "remediation_plan", "plan_id": plan_id,
        "finding_ref": _finding_ref(source), "plan_status": plan_status,
        "execution_status": "NOT_EXECUTED", "cause_candidates": candidates,
        "change_targets": targets, "purpose": purpose,
        "preserve_conditions": deepcopy(preserve_conditions),
        "revalidation": deepcopy(revalidation), "rollout": deepcopy(rollout),
        "rollback_target_ref": deepcopy(rollback_target_ref),
        "authority_requirements": deepcopy(authority_requirements),
        "missing_information": missing_information,
        "created_at": created_at,
        "ci_eligible": False,
    }
    return _validate_plan(plan)


def plan_to_yaml(plan: Any) -> str:
    """JSONをYAML 1.2でも読めるUTF-8形式として返す。"""
    validated = _validate_plan(plan)
    return json.dumps(validated, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")) + "\n"


def _validate_verification(value: Any, finding: dict[str, Any]) -> dict[str, Any]:
    return _validate_revalidation_candidate(value, finding)


def transition_finding(
    finding: Any, action: str, *, verification: Any = None,
    reason: str | None = None, now: int | None = None,
) -> dict[str, Any]:
    """Finding lifecycleを遷移させる。Plan生成や手書きstatus変更では検証済みにしない。"""
    current = validate_finding(finding)
    _enum(action, _ACTIONS)
    if now is None:
        now = current["updated_at"]
    try:
        require_uint(now)
    except ContractError:
        raise _bad() from None
    if now < current["updated_at"]:
        raise _bad("TIME_ORDER")
    updated = deepcopy(current)
    if action == "start":
        if current["status"] != "OPEN" or current["disposition"] != "NONE":
            raise _bad("INVALID_STATE")
        updated["status"] = "IN_PROGRESS"
    elif action == "request_revalidation":
        if current["status"] != "IN_PROGRESS" or current["disposition"] != "NONE":
            raise _bad("INVALID_STATE")
        updated["status"] = "AWAITING_REVALIDATION"
    elif action == "verify":
        if (current["status"] != "AWAITING_REVALIDATION" or current["disposition"] != "NONE"
                or verification is None or current["revalidation_candidate"] is not None):
            raise _bad("INVALID_STATE")
        checked = _validate_verification(verification, current)
        if checked["checked_at"] < current["updated_at"] or checked["checked_at"] > now:
            raise _bad("TIME_ORDER")
        # この低レベル部品はEvidenceの現在有効性と主体認証を証明できない。
        # authority接続後にのみ、別の確定操作がVERIFIEDへ進める。
        updated["revalidation_candidate"] = checked
        updated["status"] = "AWAITING_REVALIDATION"
    elif action in {"revise_baseline", "retire_target"}:
        if current["status"] == "VERIFIED" or current["disposition"] != "NONE":
            raise _bad("INVALID_STATE")
        if type(reason) is not str:
            raise _bad()
        require_text(reason, maximum=512)
        updated["disposition"] = "BASELINE_REVISED" if action == "revise_baseline" else "TARGET_RETIRED"
        updated["disposition_reason"] = reason
    updated["updated_at"] = now
    return validate_finding(updated)


def record_recurrence(finding: Any, *, context: Any, now: int | None = None) -> dict[str, Any]:
    """元Findingを変更せず、同じ問題を新しいOPEN Findingとして記録する。"""
    source = validate_finding(finding)
    if now is None:
        now = source["updated_at"]
    try:
        require_uint(now)
    except ContractError:
        raise _bad() from None
    if now < source["updated_at"]:
        raise _bad("TIME_ORDER")
    bound = _validate_context(context)
    source_ref = source["source_assessment_ref"]
    seed = {"parent": _finding_ref(source), "evidence": bound["evidence_refs"],
            "observation": bound["observation_ref"], "comparison": bound["comparison_ref"]}
    finding_id = "finding-" + hashlib.sha256(_canonical(seed)).hexdigest()[:32]
    recurrence = {
        "schema_version": 1, "kind": "finding", "finding_id": finding_id,
        "source_assessment_ref": source_ref, "reason_code": source["reason_code"],
        "metric_id": source["metric_id"], "control_ref": bound["control_ref"],
        "observation_ref": bound["observation_ref"], "comparison_ref": bound["comparison_ref"],
        "evidence_refs": bound["evidence_refs"], "category": source["category"],
        "cause_class": source["cause_class"], "cause_candidates": bound["cause_candidates"] or [{
            "candidate_id": "cause-unknown", "kind": "unknown", "summary": "原因は未特定",
            "confidence": "UNKNOWN", "evidence_refs": [],
        }],
        "recovery_target_ref": bound["recovery_target_ref"], "status": "OPEN",
        "disposition": "NONE", "parent_finding_ref": _finding_ref(source),
        "disposition_reason": None,
        "verifier_ref": None, "verified_at": None,
        "revalidation_candidate": None, "authority_confirmation_ref": None,
        "ci_eligible": False,
        "created_at": now, "updated_at": now,
    }
    return validate_finding(recurrence)


__all__ = [
    "generate_findings", "generate_plan", "plan_to_yaml", "record_recurrence",
    "transition_finding", "validate_finding", "finding_ref",
]
