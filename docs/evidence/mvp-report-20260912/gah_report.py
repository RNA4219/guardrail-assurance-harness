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
from gah.contracts import MAX_DOCUMENT_BYTES, require_ref
from gah.run_contracts import content_ref
from gah.wire import canonical_bytes


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


def build_report(runtime, request):
    request = validate_request(request)
    if request["action"] != "ci_check":
        raise ValueError("CI_REQUEST_REQUIRED")
    run_id = request["run_id"]
    tag = "report-" + hashlib.sha256(canonical_bytes(request)).hexdigest()[:32]

    def query(action, suffix, **fields):
        identifier = tag + "-" + suffix
        value = runtime.client(12004, {"schema_version": 1, "action": action,
            "request_id": identifier, "run_id": run_id, **fields})
        keys = {"schema_version", "kind", "action", "request_id", "ci_eligible"}
        keys |= {"outputs_ref", "outputs"} if action == "run_outputs" else {"artifact_ref", "artifact"}
        if (type(value) is not dict or set(value) != keys or type(value["schema_version"]) is not int
                or value["schema_version"] != 1 or value["kind"] != "evaluation_authority_result"
                or value["action"] != action or value["request_id"] != identifier or value["ci_eligible"] is not False):
            raise ValueError("REPORT_RESPONSE_INVALID")
        return value

    fetched = query("run_outputs", "outputs")
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
        value = query("run_artifact", field, artifact_ref=ref)
        if value["artifact_ref"] != ref or content_ref(ref["kind"], ref["id"], value["artifact"]) != ref:
            raise ValueError("REPORT_BINDING_MISMATCH")
        artifacts[field] = value["artifact"]
    manifest, decision, evidence = (artifacts[k] for k in ("manifest_ref", "decision", "evidence"))
    if (manifest["contract_ref"] != request["expected_contract_ref"]
            or manifest["baseline_ref"] != request["expected_baseline_ref"]
            or manifest["target_refs"] != request["expected_target_refs"]
            or manifest["use_cases"] != request["expected_use_cases"]):
        raise ValueError("REPORT_BINDING_MISMATCH")
    # 読取り中に失効した根拠を成功表示に使わない。成果物取得後に必ず再照会する。
    gate = runtime.client(12004, request)
    code = response_exit_code(request, gate)
    if gate["outputs_ref"] != outputs_ref:
        raise ValueError("REPORT_BINDING_MISMATCH")
    report = {"schema_version": 1, "kind": "run_report", "run_id": run_id,
        "scope": {"use_cases": manifest["use_cases"], "control_ids": manifest["control_ids"],
            "target_refs": manifest["target_refs"], "profile": manifest["profile"]},
        "checked_at": gate["checked_at"], "observed_at": evidence["observed_at"],
        "valid_until": evidence["valid_until"], "execution_status": gate["execution_status"],
        "assurance": gate["assurance"], "observed_assurance": decision["assurance"],
        "metrics": _metrics(decision["metrics"]), "metric_scopes": decision["metric_scopes"],
        "decision_reasons": decision["reasons"], "ci_reasons": gate["reasons"],
        "findings": artifacts["findings"]["items"], "plans": artifacts["plans"]["items"],
        "outputs_ref": outputs_ref, "source_refs": {k: outputs[k] for k in artifacts},
        "ci_eligible": gate["ci_eligible"], "exit_code": code}
    if len(canonical_bytes(report)) > MAX_DOCUMENT_BYTES:
        raise ValueError("REPORT_TOO_LARGE")
    return deepcopy(report)


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
        "", "| 時刻（UTC） | 値 |", "|---|---|",
        "| 基準時刻 | " + time_text(report["checked_at"]) + " |",
        "| 観測時刻 | " + time_text(report["observed_at"]) + " |",
        "| Evidence有効期限 | " + time_text(report["valid_until"]) + " |",
        "", "## 指標・差分", "",
        "値は分数で表示する。比較不能な値を0に補完しない。", "",
        "| 指標 | 現在値 | baseline | 差分 | 比較 | 対象範囲 |", "|---|---|---|---|---|---|"]
    for metric in report["metrics"]:
        rows.append("| " + literal(metric["name"]) + " / " + literal(metric["metric_id"]) + " | "
            + " | ".join((rate(metric["value"]), rate(metric["baseline_value"]), rate(metric["difference"]),
                "可能" if metric["comparison"] == "COMPARABLE" else "不能",
                literal(report["metric_scopes"].get(metric["metric_id"])))) + " |")
    if not report["metrics"]:
        rows.append("| 適用する集合指標なし | — | — | — | — | 義務・欠損は判定理由を確認 |")
    rows += ["", "## 判定理由・欠損", "", "| 種別 | 理由 |", "|---|---|"]
    rows.extend("| 保存された判定 | " + literal(reason) + " |" for reason in report["decision_reasons"])
    rows.extend("| 現在のCI利用 | " + literal(reason) + " |" for reason in report["ci_reasons"])
    if not report["decision_reasons"] and not report["ci_reasons"]:
        rows.append("| 判定理由 | 該当なし |")
    rows += ["", "## Finding・Plan参照", "",
        "Finding " + str(len(report["findings"])) + "件 / Plan " + str(len(report["plans"])) + "件", "",
        "| 種別 | ID | digest |", "|---|---|---|"]
    for ref in report["findings"] + report["plans"]:
        rows.append("| " + " | ".join(literal(ref[k]) for k in ("kind", "id", "digest")) + " |")
    rows += ["", "## 対象と保存根拠", "", "| 種別 | ID | digest |", "|---|---|---|"]
    for ref in scope["target_refs"] + [report["outputs_ref"]] + list(report["source_refs"].values()):
        rows.append("| " + " | ".join(literal(ref[k]) for k in ("kind", "id", "digest")) + " |")
    return "\n".join(rows) + "\n"


def run(runtime, request, stream, *, output_format="markdown"):
    try:
        if output_format not in {"markdown", "json"}:
            raise ValueError("INVALID_FORMAT")
        report = build_report(runtime, request)
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
    args = parser.parse_args()
    try:
        folder = Path(args.runtime).resolve()
        if not folder.is_relative_to(ROOT) or not (folder / "deployment.json").is_file():
            raise ValueError("EXISTING_RUNTIME_REQUIRED")
        with Path(args.request).open("rb") as source:
            request = decode_document(source.read(MAX_DOCUMENT_BYTES + 1))
        return run(AuthorityRuntime(folder), request, sys.stdout, output_format=args.format)
    except Exception:
        try:
            print(json.dumps({"schema_version": 1, "kind": "run_report_error",
                "reason": "REPORT_UNAVAILABLE", "ci_eligible": False, "exit_code": 2}), flush=True)
        except Exception:
            pass
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
