"""保存されたDecision/EvidenceからFinding・Planを再生成して照合する。"""
from copy import deepcopy
import hashlib

from . import assurance_authority, remediation, resources
from .adoption import AdoptionError
from .contracts import ContractError
from .run_contracts import content_ref
from .wire import canonical_bytes


def _build(db, bound, receipt):
    run_id = bound["manifest"]["run_id"]
    decision = assurance_authority._artifact(db, receipt["decision_ref"], run_id)
    items, findings, plans = [], [], []

    def put(kind, identifier, value):
        ref = content_ref(kind, identifier, value)
        items.append((ref, deepcopy(value)))
        return ref

    for control in bound["registry"]["controls"]:
        control_id = control["control_id"]
        if control_id not in bound["selected_controls"]:
            continue
        tag = hashlib.sha256(canonical_bytes([run_id, control_id])).hexdigest()[:24]
        # Controlの完全参照は採択済みRegistry内の実体へ解決する。
        control_ref = content_ref("control", control_id, control)
        obligations = {item["obligation_id"] for item in control["obligations"]}
        relevant = []
        for reason in decision["reasons"]:
            scope = decision["metric_scopes"].get(reason["metric_id"], {})
            if (scope.get("control_id") in (None, control_id)
                    and scope.get("obligation_id") in obligations | {None}):
                relevant.append(deepcopy(reason))
        # 固定UC-CIの実測metricを保持する。存在しないmetricや原因を補わない。
        assessment = {"schema_version": 1, "request_id": "assessment-" + tag,
            "request_digest": receipt["decision_ref"]["digest"],
            "target_digest": hashlib.sha256(canonical_bytes(bound["manifest"]["target_refs"])).hexdigest(),
            "contract_digest": bound["manifest"]["contract_ref"]["digest"],
            "assessed_at": decision["assessed_at"], "purpose": "component_validation",
            "assurance": decision["assurance"], "ci_eligible": False,
            "metrics": deepcopy(decision["metrics"]), "reasons": relevant}
        put("assessment", assessment["request_id"], assessment)
        observation_ref = put("observation", "observation-" + tag,
            {"run_id": run_id, "control_ref": control_ref, "decision_ref": receipt["decision_ref"],
             "localization": "metric_scope_or_run_wide", "cause_localized": False})
        comparison_ref = put("comparison", "comparison-" + tag,
            {"baseline_ref": bound["manifest"]["baseline_ref"], "plan_ref": bound["manifest"]["plan_ref"],
             "control_ref": control_ref})
        generated = remediation.generate_findings(assessment, context={"control_ref": control_ref,
            "observation_ref": observation_ref, "comparison_ref": comparison_ref,
            "evidence_refs": [receipt["evidence_ref"]], "recovery_target_ref": control["target_ref"],
            "cause_candidates": []})
        for finding in generated:
            findings.append(put("finding", finding["finding_id"], finding))
            # Planの参照は状態に依存しない投影。状態付きsnapshotと両方を保存する。
            put("finding", finding["finding_id"], remediation.finding_identity(finding))
            plan = remediation.generate_plan(finding, change_targets=[control["target_ref"]],
                purpose="観測した問題を元の検査条件で再検証する", rollback_target_ref=control["target_ref"],
                authority_requirements=[{"role": "validator", "action": "revalidate",
                    "reason": "変更と独立した評価主体で元の条件を検証する"}],
                missing_information=["原因の確認が必要"],
                created_at=receipt["created_at"])
            plans.append(put("remediation_plan", plan["plan_id"], plan))
    reports = {}
    for kind, refs in (("findings", findings), ("plans", plans)):
        value = {"schema_version": 1, "kind": kind + "_report", "run_id": run_id,
            "decision_ref": receipt["decision_ref"], "evidence_ref": receipt["evidence_ref"],
            "assessed_controls": deepcopy(bound["selected_controls"]), "items": refs,
            "created_at": receipt["created_at"], "ci_eligible": False}
        reports[kind] = put(kind + "_report", run_id, value)
    value = {"schema_version": 1, "kind": "run_outputs", "run_id": run_id,
        "manifest_ref": receipt["manifest_ref"], "decision": receipt["decision_ref"],
        "evidence": receipt["evidence_ref"], "run_receipt": content_ref(receipt["kind"], run_id, receipt),
        **reports, "created_at": receipt["created_at"], "ci_eligible": False}
    if set(bound["contract"]["required_outputs"]) != {"decision", "evidence", "findings", "plans", "run_receipt"}:
        raise AdoptionError("RUN_OUTPUTS_INVALID")
    ref = put("run_outputs", run_id, value)
    return items, {"outputs_ref": ref, "outputs": value, "ci_eligible": False}


def save(db, bound, receipt):
    try:
        items, result = _build(db, bound, receipt)
        for ref, value in items:
            if assurance_authority._save(db, bound["manifest"]["run_id"], ref["kind"], ref["id"], value) != ref:
                raise AdoptionError("RUN_OUTPUTS_INVALID")
        return result
    except ContractError:
        raise AdoptionError("RUN_OUTPUTS_INVALID") from None


def read(db, bound, receipt):
    try:
        items, result = _build(db, bound, receipt)
        for ref, expected in items:
            row = db.execute("SELECT * FROM authority_artifacts WHERE kind=? AND id=? AND digest=?",
                (ref["kind"], ref["id"], ref["digest"])).fetchone()
            if row is None:
                raise AdoptionError("RUN_OUTPUTS_MISSING")
            if row["run_id"] != bound["manifest"]["run_id"] or resources._unpack(row["payload_json"], row["digest"]) != expected:
                raise AdoptionError("RUN_OUTPUTS_INVALID")
        return result
    except (ContractError, resources.ResourceError, KeyError, TypeError, ValueError):
        raise AdoptionError("RUN_OUTPUTS_INVALID") from None


def fetch(db, bound, receipt, ref):
    """検証済みrunが参照する成果物だけを本文付きで返す。任意DB読取りは提供しない。"""
    read(db, bound, receipt)
    items, _ = _build(db, bound, receipt)
    for expected, value in items:
        if ref == expected:
            return {"artifact_ref": deepcopy(ref), "artifact": deepcopy(value), "ci_eligible": False}
    if ref == content_ref(receipt["kind"], bound["manifest"]["run_id"], receipt):
        return {"artifact_ref": deepcopy(ref), "artifact": deepcopy(receipt), "ci_eligible": False}
    for field in ("manifest_ref", "bundle_ref", "decision_ref", "evidence_ref", "closure_ref"):
        if ref == receipt[field]:
            value = assurance_authority._artifact(db, ref, bound["manifest"]["run_id"])
            return {"artifact_ref": deepcopy(ref), "artifact": value, "ci_eligible": False}
    for control in bound["registry"]["controls"]:
        if control["control_id"] in bound["selected_controls"] and ref == content_ref("control", control["control_id"], control):
            return {"artifact_ref": deepcopy(ref), "artifact": deepcopy(control), "ci_eligible": False}
    raise AdoptionError("RUN_ARTIFACT_MISSING")
