"""固定ローカルendpointへ無害な合成caseを送るchild。"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gah.contracts import ContractError, require_id
from gah.evaluation_data import validate_pack
from gah.llm_evaluator import MAX_RESPONSE_BYTES, SyntheticEvaluator


ENDPOINT_HOST = "127.0.0.1"
ENDPOINT_PORT = 18000
ENDPOINT_PATH = "/v1/chat/completions"
HTTP_TIMEOUT_SECONDS = 30
MAX_CHILD_RESULT_BYTES = 65536
_PURPOSES = {"calibration", "acceptance", "development"}


def _invalid() -> ContractError:
    return ContractError()


def _pack_path() -> Path:
    return ROOT / "datasets" / "synthetic-policy-v1" / "pack.json"


def _load_pack() -> dict[str, Any]:
    try:
        with _pack_path().open("rb") as stream:
            raw = stream.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise _invalid()

        def pairs(items):
            result = {}
            for key, item in items:
                if key in result:
                    raise _invalid()
                result[key] = item
            return result

        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs)
        return validate_pack(value)
    except ContractError:
        raise
    except (OSError, UnicodeError, ValueError, RecursionError):
        raise _invalid() from None


def _post(body: dict[str, Any]) -> bytes:
    """固定host/pathへ1回だけPOSTし、応答本文を上限付きで読む。"""

    raw = json.dumps(
        body, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    if len(raw) > 65536:
        raise ContractError("REQUEST_SIZE")
    connection = http.client.HTTPConnection(
        ENDPOINT_HOST, ENDPOINT_PORT, timeout=HTTP_TIMEOUT_SECONDS
    )
    try:
        connection.request(
            "POST",
            ENDPOINT_PATH,
            body=raw,
            headers={"Content-Type": "application/json", "Content-Length": str(len(raw))},
        )
        response = connection.getresponse()
        payload = response.read(MAX_RESPONSE_BYTES + 1)
        if len(payload) > MAX_RESPONSE_BYTES:
            raise ContractError("RESPONSE_SIZE")
        if response.status < 200 or response.status >= 300:
            raise ContractError("HTTP_STATUS")
        return payload
    except (OSError, TimeoutError, http.client.HTTPException):
        raise ContractError("PROVIDER_TIMEOUT") from None
    finally:
        connection.close()


def run_case(purpose: str, case_id: str) -> dict[str, Any]:
    """指定caseの全stageを順に1回ずつ送信し、raw本文を返さず記録する。"""

    if type(purpose) is not str or purpose not in _PURPOSES:
        raise ContractError("INVALID_PURPOSE")
    require_id(case_id)
    pack = _load_pack()
    evaluator = SyntheticEvaluator(pack)
    try:
        session = evaluator.open_case(purpose, case_id)
    except ContractError:
        raise
    records: list[dict[str, Any]] = []
    failed = False
    remote_stop_unknown = False
    while True:
        try:
            prepared = session.prepare()
        except ContractError:
            break
        try:
            response = _post(prepared["request"])
            record = session.accept(response)
        except ContractError as error:
            # 通信障害や固定上限超過も固定codeだけを返し、本文を保持しない。
            record = {
                "case_id": case_id,
                "stage_id": prepared["stage_id"],
                "status": "INVALID_OUTPUT",
                "reason_code": error.code,
                "usage": None,
                "selection": None,
                "response_digest": None,
                "observations": None,
                "effect": None,
            }
            failed = True
            remote_stop_unknown = error.code == "PROVIDER_TIMEOUT"
        records.append(record)
        if failed or record.get("status") != "COMPLETE":
            failed = True
            break
    snapshot = session.snapshot()
    result = {
        "schema_version": 1,
        "kind": "synthetic_llm_case_result",
        "purpose": purpose,
        "case_id": case_id,
        "pack_digest": evaluator.pack_digest,
        "endpoint": f"http://{ENDPOINT_HOST}:{ENDPOINT_PORT}{ENDPOINT_PATH}",
        "provider_model": "qwen3.8-flash-next",
        "records": records,
        "complete": bool(snapshot["complete"]),
        "failed": bool(snapshot["failed"] or failed),
        "remote_stop_unknown": remote_stop_unknown,
        "ci_eligible": False,
    }
    encoded = json.dumps(
        result, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    if len(encoded) > MAX_CHILD_RESULT_BYTES:
        raise ContractError("CHILD_RESULT_SIZE")
    return result


def main(argv: list[str] | None = None) -> int:
    """固定packのpurpose/case_idだけを受け、構造化recordをstdoutへ返す。"""

    parser = argparse.ArgumentParser()
    parser.add_argument("--purpose", choices=sorted(_PURPOSES), required=True)
    parser.add_argument("--case-id", required=True)
    args = parser.parse_args(argv)
    try:
        result = run_case(args.purpose, args.case_id)
        sys.stdout.write(json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n")
        return 0 if result["complete"] else 1
    except ContractError:
        # 利用者入力・provider応答・例外本文をstdout/stderrへ漏らさない。
        sys.stdout.write(
            json.dumps(
                {
                    "schema_version": 1,
                    "kind": "synthetic_llm_case_result",
                    "status": "ERROR",
                    "reason_code": "CHILD_FAILURE",
                    "ci_eligible": False,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            + "\n"
        )
        return 1


__all__ = ["ENDPOINT_HOST", "ENDPOINT_PATH", "ENDPOINT_PORT", "main", "run_case"]


if __name__ == "__main__":
    raise SystemExit(main())
