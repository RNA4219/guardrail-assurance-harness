"""通常runの保存根拠とfresh CIから日本語要約またはJSONを出力する。"""
from copy import deepcopy
from fractions import Fraction
from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path
import sys

from tools.gah_ci import ROOT, AuthorityRuntime, decode_document, response_exit_code, validate_request
from gah.contracts import MAX_DOCUMENT_BYTES, MAX_INTEGER, require_ref
from gah.run_contracts import content_ref
from gah.wire import canonical_bytes


class EvidenceDeleted(ValueError):
    pass


def _deleted_report(runtime,request,tag):
    query={"schema_version":1,"action":"run_retention_state","request_id":tag+"-retention",
        "run_id":request["run_id"],"expected_manifest_ref":request["expected_manifest_ref"]}
    state=runtime.client(12004,query)
    if (type(state) is not dict or type(state.get("schema_version")) is not int or state["schema_version"]!=1
            or state.get("kind")!="evaluation_authority_result" or state.get("action")!=query["action"]
            or state.get("request_id")!=query["request_id"] or state.get("run_id")!=request["run_id"]
            or state.get("ci_eligible") is not False or state.get("deleted") is not True
            or state.get("reproduction")!="REPRODUCTION_UNAVAILABLE"):
        raise ValueError("REPORT_RESPONSE_INVALID")
    meta=state["metadata"]
    if (meta["subject_ref"]!=request["expected_manifest_ref"] or meta["contract_ref"]!=request["expected_contract_ref"]
            or meta["baseline_ref"]!=request["expected_baseline_ref"] or meta["target_refs"]!=request["expected_target_refs"]
            or meta["use_cases"]!=request["expected_use_cases"]):
        raise ValueError("REPORT_BINDING_MISMATCH")
    gate=runtime.client(12004,request);code=response_exit_code(request,gate)
    if gate["ci_eligible"] or "EVIDENCE_DELETED" not in gate["reasons"]:
        raise ValueError("REPORT_BINDING_MISMATCH")
    return {"schema_version":1,"kind":"run_report","run_id":request["run_id"],
        "scope":{"use_cases":meta["use_cases"],"control_ids":meta["control_ids"],"target_refs":meta["target_refs"],
            "profile":meta["profile"],"executed_scope":"targeted" if meta["unexecuted_control_ids"] else "full",
            "unexecuted_control_ids":meta["unexecuted_control_ids"]},
        "checked_at":gate["checked_at"],"observed_at":meta["observed_at"],"valid_until":meta["valid_until"],
        "execution_status":gate["execution_status"],"assurance":gate["assurance"],"observed_assurance":state["saved_assurance"],
        "metrics":[],"metric_scopes":{},"decision_reasons":["REPRODUCTION_UNAVAILABLE"],"ci_reasons":gate["reasons"],
        "findings":[],"plans":[],"artifacts_available":False,"reproduction":"REPRODUCTION_UNAVAILABLE",
        "outputs_ref":None,"source_refs":{"evidence":state["evidence_ref"],"tombstone":state["tombstone_ref"]},
        "ci_eligible":False,"exit_code":code}


def _metrics(values):
    if type(values) is not list:
        raise ValueError("REPORT_METRICS_INVALID")
    result = []
    for metric in values:
        fractions = []
        for field in ("value", "baseline_value"):
            value = metric[field]
            if value is None:
                fractions.append(None)
            elif (type(value) is list and len(value) == 2 and all(type(n) is int for n in value)
                    and value[0] >= 0 and value[1] > 0):
                fractions.append(Fraction(*value))
            else:
                raise ValueError("REPORT_METRICS_INVALID")
        delta = None if None in fractions else fractions[0] - fractions[1]
        result.append({**deepcopy(metric), "difference": None if delta is None else [delta.numerator, delta.denominator],
            "comparison": "NOT_COMPARABLE" if delta is None else "COMPARABLE"})
    return result


def build_report(runtime, request, *, candidate=False):
    request = validate_request(request)
    if request["action"] != "ci_check":
        raise ValueError("CI_REQUEST_REQUIRED")
    if type(candidate) is not bool:
        raise ValueError("REPORT_MODE_INVALID")
    outputs_action = "candidate_outputs" if candidate else "run_outputs"
    artifact_action = "candidate_artifact" if candidate else "run_artifact"
    run_id = request["run_id"]
    tag = "report-" + hashlib.sha256(canonical_bytes(request)).hexdigest()[:32]

    def query(action, suffix, **fields):
        identifier = tag + "-" + suffix
        value = runtime.client(12004, {"schema_version": 1, "action": action,
            "request_id": identifier, "run_id": run_id, **fields})
        if (type(value) is dict and value.get("kind") == "authority_error"
                and value.get("reason") == "EVIDENCE_DELETED" and value.get("ci_eligible") is False):
            raise EvidenceDeleted()
        keys = {"schema_version", "kind", "action", "request_id", "ci_eligible"}
        keys |= {"outputs_ref", "outputs"} if action == outputs_action else {"artifact_ref", "artifact"}
        if (type(value) is not dict or set(value) != keys or type(value["schema_version"]) is not int
                or value["schema_version"] != 1 or value["kind"] != "evaluation_authority_result"
                or value["action"] != action or value["request_id"] != identifier or value["ci_eligible"] is not False):
            raise ValueError("REPORT_RESPONSE_INVALID")
        return value

    try:
        fetched = query(outputs_action, "outputs")
    except EvidenceDeleted:
        return _deleted_report(runtime,request,tag)
    outputs, outputs_ref = fetched["outputs"], fetched["outputs_ref"]
    require_ref(outputs_ref)
    if outputs_ref != content_ref("run_outputs", run_id, outputs):
        raise ValueError("REPORT_BINDING_MISMATCH")
    if outputs["run_id"] != run_id or outputs["manifest_ref"] != request["expected_manifest_ref"]:
        raise ValueError("REPORT_BINDING_MISMATCH")
    artifacts = {}
    for field in ("manifest_ref", "decision", "evidence", "findings", "plans", "run_receipt"):
        ref = outputs[field]
        require_ref(ref)
        value = query(artifact_action, field, artifact_ref=ref)
        if value["artifact_ref"] != ref or content_ref(ref["kind"], ref["id"], value["artifact"]) != ref:
            raise ValueError("REPORT_BINDING_MISMATCH")
        artifacts[field] = value["artifact"]
    manifest, decision, evidence = (artifacts[k] for k in ("manifest_ref", "decision", "evidence"))
    if (manifest["contract_ref"] != request["expected_contract_ref"]
            or manifest["baseline_ref"] != request["expected_baseline_ref"]
            or manifest["target_refs"] != request["expected_target_refs"]
            or manifest["use_cases"] != request["expected_use_cases"]):
        raise ValueError("REPORT_BINDING_MISMATCH")
    if candidate and manifest.get("purpose") not in {"contract_candidate", "contract_old_regression"}:
        raise ValueError("CANDIDATE_PURPOSE_REQUIRED")
    aggregate_ref = {"kind":"aggregation", "id":run_id, "digest":decision["aggregate_digest"]}
    require_ref(aggregate_ref)
    measured = query(artifact_action, "aggregation", artifact_ref=aggregate_ref)
    aggregate = measured["artifact"]
    if (measured["artifact_ref"] != aggregate_ref or content_ref("aggregation", run_id, aggregate) != aggregate_ref
            or aggregate.get("schema_version") != 1 or type(aggregate.get("schema_version")) is not int
            or aggregate.get("kind") != "aggregation" or aggregate.get("run_id") != run_id
            or aggregate.get("contract_digest") != request["expected_contract_ref"]["digest"]
            or aggregate.get("ci_eligible") is not False or type(aggregate.get("issues")) is not list):
        raise ValueError("REPORT_BINDING_MISMATCH")
    count_rows = _count_rows(aggregate["counts"])
    from tools.gah_mutation_review import execute as mutation_review
    reviews, review_code = mutation_review(runtime, {"schema_version":1, "action":"mutation_review_current",
        "request_id":tag+"-mutation-reviews", "run_id":run_id, "expected_evidence_ref":outputs["evidence"]})
    if (review_code or reviews["original_decision_ref"] != outputs["decision"]
            or reviews["contract_ref"] != request["expected_contract_ref"]):
        raise ValueError("REPORT_BINDING_MISMATCH")
    reviewed_classification = {}
    for variant in ("baseline", "candidate"):
        observed = aggregate["counts"]["variant"][variant]["mutation_error"]
        excluded = reviews["counts"][variant]["approved_exclusions"]
        pending = reviews["counts"][variant]["pending_exclusions"]
        if excluded + pending > observed:
            raise ValueError("REPORT_COUNTS_INVALID")
        reviewed_classification[variant] = {"mutation_errors_observed":observed,
            "excluded":excluded, "exclusion_pending":pending, "remaining_mutation_errors":observed-excluded}
    management = []
    for index, ref in enumerate(artifacts["findings"]["items"]):
        if ref["kind"] != "finding": continue
        item = query(artifact_action, "finding-" + str(index), artifact_ref=ref)
        if item["artifact_ref"] != ref or content_ref("finding", ref["id"], item["artifact"]) != ref:
            raise ValueError("REPORT_BINDING_MISMATCH")
        if item["artifact"].get("status") != "OPEN": continue
        from tools.gah_finding import execute as finding_execute
        state, state_code = finding_execute(runtime, {"schema_version":1,"action":"finding_current",
            "request_id":tag + "-state-" + str(index),"run_id":run_id,"finding_ref":ref})
        if state_code:
            management.append({"finding_ref":ref,"available":False,"reason":state["reason"]})
        else:
            management.append({"finding_ref":ref,"available":True,"state":state["state"],"state_ref":state["state_ref"],
                "verification_current":state["verification_current"],"checked_at":state["checked_at"],"reasons":state["reasons"]})
    # 読取り中に失効した根拠を成功表示に使わない。成果物取得後に必ず再照会する。
    gate = runtime.client(12004, request)
    code = response_exit_code(request, gate)
    if candidate:
        if gate["ci_eligible"] or code == 0 or "CI_PURPOSE_REQUIRED" not in gate["reasons"]:
            raise ValueError("REPORT_BINDING_MISMATCH")
    elif gate["outputs_ref"] != outputs_ref:
        raise ValueError("REPORT_BINDING_MISMATCH")
    report = {"schema_version": 1, "kind": "run_report", "run_id": run_id,
        "scope": {"use_cases": manifest["use_cases"], "control_ids": manifest["control_ids"],
            "target_refs": manifest["target_refs"], "profile": manifest["profile"],
            "executed_scope": outputs.get("scope", {}).get("executed_scope", "full"),
            "unexecuted_control_ids": outputs.get("scope", {}).get("unexecuted_control_ids", [])},
        "checked_at": gate["checked_at"], "observed_at": evidence["observed_at"],
        "valid_until": evidence["valid_until"], "execution_status": gate["execution_status"],
        "assurance": gate["assurance"], "observed_assurance": decision["assurance"],
        "metrics": _metrics(decision["metrics"]), "metric_scopes": decision["metric_scopes"],
        "measurements": {"aggregate_ref":aggregate_ref, "counts":aggregate["counts"],
            "count_rows":count_rows, "issues":aggregate["issues"],
            "mutation_reviews":reviews, "reviewed_classification":reviewed_classification},
        "decision_reasons": decision["reasons"], "ci_reasons": gate["reasons"],
        "findings": artifacts["findings"]["items"], "plans": artifacts["plans"]["items"], "finding_management": management,
        "outputs_ref": outputs_ref, "source_refs": {**{k: outputs[k] for k in artifacts}, "aggregation":aggregate_ref},
        "ci_eligible": gate["ci_eligible"], "exit_code": code}
    if candidate:
        report["purpose"] = manifest["purpose"]
    if len(canonical_bytes(report)) > MAX_DOCUMENT_BYTES:
        raise ValueError("REPORT_TOO_LARGE")
    return deepcopy(report)


def _count_rows(counts):
    levels = {"variant":0, "control":1, "obligation":1, "category":1,
        "control_category":2, "obligation_category":2}
    required = {"tp", "fp", "tn", "fn", "planned", "complete", "error", "indeterminate", "detection_missing",
        "retry_count", "duplicate_deliveries", "killed", "survived", "no_coverage", "mutation_error"}
    if type(counts) is not dict or set(counts) != set(levels):
        raise ValueError("REPORT_COUNTS_INVALID")
    result = []
    for group, depth in levels.items():
        variants = counts[group]
        if type(variants) is not dict or set(variants) != {"baseline", "candidate"}:
            raise ValueError("REPORT_COUNTS_INVALID")
        pending = [((group, variant), variants[variant], depth) for variant in ("candidate", "baseline")]
        while pending:
            path, node, remaining = pending.pop()
            if type(node) is not dict or any(type(k) is not str for k in node):
                raise ValueError("REPORT_COUNTS_INVALID")
            if remaining:
                pending.extend((path+(k,), node[k], remaining-1) for k in sorted(node, reverse=True))
            else:
                if (not required <= set(node)
                        or any(type(v) is not int or not 0 <= v <= MAX_INTEGER for v in node.values())):
                    raise ValueError("REPORT_COUNTS_INVALID")
                result.append({"scope":list(path), "counts":deepcopy(node)})
    return result


def render_markdown(report):
    def literal(value):
        return json.dumps(value, ensure_ascii=True, sort_keys=True, allow_nan=False).replace("|", "\\|").replace("`", "\\u0060").replace("<", "\\u003c").replace(">", "\\u003e")
    def rate(value):
        return "不明" if value is None else str(value[0]) + "/" + str(value[1])
    def time_text(value):
        try:
            stamp = datetime.fromtimestamp(value, timezone.utc).isoformat()
        except (ValueError, OverflowError, OSError):
            stamp = "日時表示範囲外"
        return stamp + " (epoch " + str(value) + ")"
    scope = report["scope"]
    rows = ["# GAH実行結果", "", "run: " + literal(report["run_id"]),
        "実行状態: " + report["execution_status"], "CI照会のAssurance: " + report["assurance"],
        "保存時のAssurance: " + report["observed_assurance"],
        "現在のCI利用: " + ("可" if report["ci_eligible"] else "不可") + " / 終了値 " + str(report["exit_code"]),
        "", "用途: " + literal(scope["use_cases"]), "プロファイル: " + literal(scope["profile"]),
        "評価範囲Control: " + literal(scope["control_ids"]),
        "実行範囲: " + literal(scope["executed_scope"]),
        "未実施Control（合格根拠に含めない）: " + literal(scope["unexecuted_control_ids"]),
        "", "| 時刻（UTC） | 値 |", "|---|---|",
        "| 基準時刻 | " + time_text(report["checked_at"]) + " |",
        "| 観測時刻 | " + time_text(report["observed_at"]) + " |",
        "| Evidence有効期限 | " + time_text(report["valid_until"]) + " |",
        "", "## 指標・差分", "",
        "値は分数で表示する。比較不能な値を0に補完しない。", "",
        "| 指標 | 現在値 | baseline | 差分 | 比較 | 対象範囲 |", "|---|---|---|---|---|---|"]
    if report.get("purpose") in {"contract_candidate", "contract_old_regression"}:
        rows[2:2] = ["契約候補の評価run。通常CIには利用できない。", ""]
    for metric in report["metrics"]:
        rows.append("| " + literal(metric["name"]) + " / " + literal(metric["metric_id"]) + " | "
            + " | ".join((rate(metric["value"]), rate(metric["baseline_value"]), rate(metric["difference"]),
                "可能" if metric["comparison"] == "COMPARABLE" else "不能",
                literal(report["metric_scopes"].get(metric["metric_id"])))) + " |")
    if report.get("artifacts_available") is False:
        rows.append("| Evidence削除により指標を再現できない | — | — | — | — | 不足を0で補わない |")
    elif not report["metrics"]:
        rows.append("| 適用する集合指標なし | — | — | — | — | 義務・欠損は判定理由を確認 |")
    measurements = report.get("measurements")
    rows += ["", "## 件数・未確定・欠損", ""]
    if measurements is None:
        rows.append("保存件数を取得できないため、0件で補完しない。")
    else:
        rows += ["TP/FP/TN/FNは保存した検出判定の計数。段階数や再試行数を独立した標本数に読み替えない。",
            "除外審査は、変更前成立とMutation不成立の保存根拠を独立検証・承認したものに限る。元の判定と必須欠損は保持する。", "",
            "| 対象範囲 / 版 | TP | FP | TN | FN | 検出判定欠損 | 実行ERROR | 未確定 |",
            "|---|---|---|---|---|---|---|---|---|"]
        for row in measurements["count_rows"]:
            counts = row["counts"]
            rows.append("| " + literal(row["scope"]) + " | " + " | ".join(str(counts[k])
                for k in ("tp","fp","tn","fn","detection_missing","error","indeterminate")) + " |")
        rows += ["", "| 対象範囲 / 版 | 完了/予定段階 | 再試行 | 重複配信 | KILLED | SURVIVED | NO_COVERAGE | Mutation ERROR |",
            "|---|---|---|---|---|---|---|---|"]
        for row in measurements["count_rows"]:
            counts = row["counts"]
            rows.append("| " + literal(row["scope"]) + " | " + str(counts["complete"]) + "/" + str(counts["planned"])
                + " | " + " | ".join(str(counts[k]) for k in
                    ("retry_count","duplicate_deliveries","killed","survived","no_coverage","mutation_error")) + " |")
    if measurements is not None:
        rows += ["", "### Mutationの除外審査", "",
            "観測時のERRORは上表に保持し、現在有効な審査だけを以下で別計数する。未確定除外を成功や充足へ加算しない。", "",
            "| 版 | 観測Mutation ERROR | 根拠付き除外 | 除外未確定 | 審査後に残るMutation ERROR |", "|---|---|---|---|---|"]
        for variant, counts in measurements["reviewed_classification"].items():
            rows.append("| " + literal(variant) + " | " + " | ".join(str(counts[k]) for k in
                ("mutation_errors_observed", "excluded", "exclusion_pending", "remaining_mutation_errors")) + " |")
        for item in measurements["mutation_reviews"]["items"]:
            proof = item["validation"]["proof"]
            rows += ["", "除外審査: " + literal(item["status"]) + " / " + literal(proof["attempt_ref"]),
                "対象: " + literal({k:proof[k] for k in ("variant","control_id","obligation_id","case_id","trial_id")}),
                "理由: " + literal(proof["reason_code"]) + " / " + literal(item["validation"]["request"]["rationale"]),
                "独立検証者: " + literal(item["validation"]["reviewed_by"]),
                "承認者: " + literal(None if item["approval"] is None else item["approval"]["approved_by"]),
                "検証・承認根拠: " + literal([item["validation_ref"],item["approval_ref"],proof["evidence_ref"],proof["contract_ref"]])]
    rows += ["", "## 判定理由・欠損", "", "| 種別 | 理由 |", "|---|---|"]
    rows.extend("| 保存された判定 | " + literal(reason) + " |" for reason in report["decision_reasons"])
    rows.extend("| 現在のCI利用 | " + literal(reason) + " |" for reason in report["ci_reasons"])
    if not report["decision_reasons"] and not report["ci_reasons"]:
        rows.append("| 判定理由 | 該当なし |")
    rows += ["", "## Finding・Plan参照", "",
        ("Evidence削除によりFinding・Planの再現を停止" if report.get("artifacts_available") is False else
         "Finding " + str(len(report["findings"])) + "件 / Plan " + str(len(report["plans"])) + "件"), "",
        "| 種別 | ID | digest |", "|---|---|---|"]
    for ref in report["findings"] + report["plans"]:
        rows.append("| " + " | ".join(literal(ref[k]) for k in ("kind", "id", "digest")) + " |")
    for item in report.get("finding_management", []):
        if not item["available"]:
            rows += ["", "Finding管理状態を照合できない: " + literal(item["finding_ref"]) + " / " + literal(item["reason"])]
            continue
        state = item["state"]
        rows += ["", "Finding管理: " + literal(item["finding_ref"]["id"]),
            "保存された対応状態: " + literal(state["status"]),
            "現在の修復確認: " + ("有効" if item["verification_current"] else "未確認または失効"),
            "別処遇: " + literal(state.get("disposition", "NONE"))]
        for link in state.get("recurrences", []):
            rows.append("再発Finding: " + literal(link["new_finding_ref"]))
    rows += ["", "## 対象と保存根拠", "", "| 種別 | ID | digest |", "|---|---|---|"]
    for ref in scope["target_refs"] + [report["outputs_ref"]] + list(report["source_refs"].values()):
        if ref is None:
            continue
        rows.append("| " + " | ".join(literal(ref[k]) for k in ("kind", "id", "digest")) + " |")
    return "\n".join(rows) + "\n"


def run(runtime, request, stream, *, output_format="markdown", candidate=False):
    try:
        if output_format not in {"markdown", "json"}:
            raise ValueError("INVALID_FORMAT")
        report = build_report(runtime, request, candidate=candidate)
        text = (render_markdown(report) if output_format == "markdown" else
            json.dumps(report, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n")
        if len(text.encode("utf-8")) > MAX_DOCUMENT_BYTES:
            raise ValueError("REPORT_TOO_LARGE")
        stream.write(text)
        stream.flush()
        return report["exit_code"]
    except Exception:
        try:
            stream.write(json.dumps({"schema_version": 1, "kind": "run_report_error",
                "reason": "REPORT_UNAVAILABLE", "ci_eligible": False, "exit_code": 2}) + "\n")
            stream.flush()
        except Exception:
            pass
        return 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", required=True)
    parser.add_argument("--request", required=True, help="完全参照を指定したci_check JSON")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    parser.add_argument("--candidate", action="store_true", help="契約候補runの保存結果を表示する")
    args = parser.parse_args()
    try:
        folder = Path(args.runtime).resolve()
        if not folder.is_relative_to(ROOT) or not (folder / "deployment.json").is_file():
            raise ValueError("EXISTING_RUNTIME_REQUIRED")
        with Path(args.request).open("rb") as source:
            request = decode_document(source.read(MAX_DOCUMENT_BYTES + 1))
        return run(AuthorityRuntime(folder), request, sys.stdout, output_format=args.format, candidate=args.candidate)
    except Exception:
        try:
            print(json.dumps({"schema_version": 1, "kind": "run_report_error",
                "reason": "REPORT_UNAVAILABLE", "ci_eligible": False, "exit_code": 2}), flush=True)
        except Exception:
            pass
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
