"""固定ローカルproviderへ一度照会し、検査済みの限定提案だけを返す。"""
import json
from pathlib import Path
import sys
from urllib import request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gah.management import ManagementError, build_request, parse_response

ENDPOINT = "http://127.0.0.1:18000/v1/chat/completions"


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def main():
    # Proxy、redirect、ユーザー指定URLを経由しない。親がプロセス全体を55秒で停止する。
    opener = request.build_opener(request.ProxyHandler({}), NoRedirect())
    body = json.dumps(build_request(), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    message = request.Request(ENDPOINT, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with opener.open(message, timeout=50) as response:
            raw = response.read(65537)
        result = parse_response(raw)
    except ManagementError as error:
        result = {"schema_version": 1, "kind": "management_error", "reason": error.code}
    except Exception:
        result = {"schema_version": 1, "kind": "management_error", "reason": "PROVIDER_UNAVAILABLE"}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0 if result["kind"] == "management_proposal" else 1


if __name__ == "__main__":
    raise SystemExit(main())
