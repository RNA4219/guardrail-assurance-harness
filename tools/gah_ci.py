"""既存の固定authorityへ現在のCI利用を問い合わせ、結果に応じた終了値を返す。"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gah.contracts import ContractError, decode_document, require_ref
from gah.regression_runs import validate_request
from tools.authority_runtime import AuthorityRuntime


def response_exit_code(request, value):
    fields = {"schema_version", "kind", "action", "request_id", "run_id", "checked_at",
        "expected_manifest_ref", "outputs_ref", "assurance", "reasons", "use", "ci_eligible",
        "execution_status", "exit_code"}
    if type(value) is not dict or set(value) != fields:
        raise ValueError("CI_RESPONSE_INVALID")
    if (type(value["schema_version"]) is not int or value["schema_version"] != 1
            or value["kind"] != "ci_gate_result" or value["action"] != "ci_check"
            or value["request_id"] != request["request_id"] or value["run_id"] != request["run_id"]
            or value["expected_manifest_ref"] != request["expected_manifest_ref"]
            or type(value["checked_at"]) is not int or value["checked_at"] < 0
            or value["assurance"] not in {"HEALTHY", "WARNING", "UNKNOWN", "DEGRADED", "HOLD"}
            or type(value["reasons"]) is not list or any(type(r) is not str or not r for r in value["reasons"])
            or type(value["exit_code"]) is not int or value["exit_code"] not in {0, 1, 2, 3}):
        raise ValueError("CI_RESPONSE_INVALID")
    code = value["exit_code"]
    if (value["use"] is not (code == 0) or value["ci_eligible"] is not (code == 0)
            or value["execution_status"] != {0: "COMPLETED", 1: "COMPLETED", 2: "FAILED", 3: "CANCELLED"}[code]
            or (code == 0 and (value["reasons"] or value["assurance"] not in {"HEALTHY", "WARNING"}))
            or (code != 0 and not value["reasons"])):
        raise ValueError("CI_RESPONSE_INVALID")
    if value["outputs_ref"] is not None:
        require_ref(value["outputs_ref"])
        if value["outputs_ref"]["kind"] != "run_outputs" or value["outputs_ref"]["id"] != request["run_id"]:
            raise ValueError("CI_RESPONSE_INVALID")
    elif code == 0:
        raise ValueError("CI_RESPONSE_INVALID")
    return code


def run(runtime, request, stream):
    """保存JSONを成功根拠にせず、毎回operatorとしてbrokerへ照会する。"""
    try:
        normalized = validate_request(request)
        if normalized["action"] != "ci_check":
            raise ValueError("CI_REQUEST_REQUIRED")
        value = runtime.client(12004, normalized)
        code = response_exit_code(normalized, value)
        stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
        stream.flush()
        return code
    except Exception:
        try:
            stream.write(json.dumps({"schema_version": 1, "kind": "ci_client_error",
                "reason": "CI_CHECK_FAILED", "ci_eligible": False, "exit_code": 2}) + "\n")
            stream.flush()
        except Exception:
            pass
        return 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", required=True, help="既存authorityのdeployment.jsonを含むrepo内ディレクトリ")
    parser.add_argument("--request", required=True, help="対象の完全参照を指定したci_check JSON")
    args = parser.parse_args()
    try:
        folder = Path(args.runtime).resolve()
        if not folder.is_relative_to(ROOT) or not (folder / "deployment.json").is_file():
            raise ValueError("EXISTING_RUNTIME_REQUIRED")
        with Path(args.request).open("rb") as source:
            request = decode_document(source.read(1024 * 1024 + 1))
        return run(AuthorityRuntime(folder), request, sys.stdout)
    except Exception:
        try:
            print(json.dumps({"schema_version": 1, "kind": "ci_client_error",
                "reason": "CI_INPUT_OR_RUNTIME_UNAVAILABLE", "ci_eligible": False, "exit_code": 2}), flush=True)
        except Exception:
            pass
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
