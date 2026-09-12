"""固定したauthority imageの入口。brokerか限定clientを起動する。"""
import argparse
import errno
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gah.authority import AuthorityError, MAX_FRAME, call, serve
from gah.contracts import ContractError, decode_document

SOCKET = "/ipc/authority.sock"
DATABASE = "/state/authority.sqlite"


def isolation_probe():
    if not sys.platform.startswith("linux") or os.geteuid() not in {12001, 12002, 12003, 12004}:
        raise AuthorityError("CLIENT_IDENTITY_MISMATCH")
    status = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines() if ":" in line)
    checks = {"nonroot": os.geteuid() == os.getegid(),
        "capabilities_dropped": int(status["CapEff"].strip(), 16) == 0,
        "no_new_privileges": status["NoNewPrivs"].strip() == "1",
        "no_extra_groups": set(os.getgroups()) <= {os.getegid()},
        "socket_not_replaceable": False, "database_unavailable": False, "identity_not_changeable": False}
    try:
        os.unlink(SOCKET)
    except OSError as error:
        checks["socket_not_replaceable"] = error.errno in {errno.EROFS, errno.EACCES, errno.EPERM}
    try:
        with open(DATABASE, "rb"):
            pass
    except OSError as error:
        checks["database_unavailable"] = error.errno in {errno.ENOENT, errno.EACCES}
    destination = 12001 if os.geteuid() != 12001 else 12002
    try:
        os.setuid(destination)
    except OSError as error:
        checks["identity_not_changeable"] = error.errno == errno.EPERM
    return {"schema_version": 1, "kind": "authority_client_probe", "uid": os.geteuid(), "gid": os.getegid(),
            "groups": os.getgroups(), "checks": checks, "ci_eligible": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("broker", "client", "probe"))
    args = parser.parse_args()
    if args.mode == "broker":
        serve(SOCKET, DATABASE)
        return 0
    if args.mode == "probe":
        result = isolation_probe()
    else:
        raw = sys.stdin.buffer.read(MAX_FRAME + 1)
        if len(raw) > MAX_FRAME:
            raise AuthorityError("FRAME_SIZE")
        result = call(SOCKET, decode_document(raw))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AuthorityError, ContractError) as error:
        print(json.dumps({"schema_version": 1, "kind": "authority_client_error", "reason": error.code, "ci_eligible": False}))
        raise SystemExit(2)
    except Exception:
        print(json.dumps({"schema_version": 1, "kind": "authority_client_error", "reason": "CLIENT_FAILURE", "ci_eligible": False}))
        raise SystemExit(2)
