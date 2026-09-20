"""無害な固定fixtureを、決められたscenarioだけで実行するworker。"""

from __future__ import annotations

import argparse
import errno
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable


_MAX_INPUT = 64 * 1024
_MAX_EPOCH = 2**53 - 1
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_HEX32 = re.compile(r"[0-9a-f]{32}\Z", re.IGNORECASE)
_HEX8 = re.compile(r"[0-9a-f]{8}\Z", re.IGNORECASE)
_HEX4 = re.compile(r"[0-9a-f]{4}\Z", re.IGNORECASE)
_HEX2 = re.compile(r"[0-9a-f]{2}\Z", re.IGNORECASE)
_BINDING_FIELDS = (
    "run_id",
    "operation_id",
    "owner_epoch",
    "contract_digest",
    "target_digest",
    "obligation_id",
    "case_id",
    "trial_id",
    "stage_id",
    "fixture_digest",
    "adapter_digest",
    "policy_digest",
    "evaluator_digest",
    "isolation_digest",
)
_DIGEST_FIELDS = {
    "contract_digest",
    "target_digest",
    "fixture_digest",
    "adapter_digest",
    "policy_digest",
    "evaluator_digest",
    "isolation_digest",
}
_IDENTIFIER_FIELDS = set(_BINDING_FIELDS) - _DIGEST_FIELDS - {"owner_epoch"}


def _reject_constant(value: str) -> Any:
    raise ValueError(f"JSON定数 {value} は許可しない")


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("重複key")
        result[key] = value
    return result


def _read_binding() -> dict[str, Any]:
    """stdinから厳格なbinding objectを読み、失敗時に内容を説明しない。"""
    raw = sys.stdin.buffer.read(_MAX_INPUT + 1)
    if len(raw) > _MAX_INPUT:
        raise ValueError("入力サイズ超過")
    try:
        text = raw.decode("utf-8")
        parsed = json.loads(
            text,
            object_pairs_hook=_unique_pairs,
            parse_float=lambda value: (_ for _ in ()).throw(ValueError("小数")),
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
        raise ValueError("binding不正") from None
    if not isinstance(parsed, dict) or set(parsed) != set(_BINDING_FIELDS):
        raise ValueError("binding項目不正")
    for field in _IDENTIFIER_FIELDS:
        value = parsed[field]
        if type(value) is not str or _IDENTIFIER.fullmatch(value) is None:
            raise ValueError("binding識別子不正")
    if type(parsed["owner_epoch"]) is not int or not 1 <= parsed["owner_epoch"] <= _MAX_EPOCH:
        raise ValueError("owner_epoch不正")
    for field in _DIGEST_FIELDS:
        value = parsed[field]
        if type(value) is not str or _DIGEST.fullmatch(value) is None:
            raise ValueError("digest不正")
    return parsed


def _emit_envelope(binding: dict[str, Any], mode: str, observations: dict[str, Any]) -> None:
    """通常fixtureのraw envelopeを固定5項目で出力する。"""
    envelope = {
        "schema_version": 1,
        "kind": "gah_generic_result",
        "binding": binding,
        "mode": mode,
        "observations": observations,
    }
    output = json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    sys.stdout.write(output + "\n")
    sys.stdout.flush()


def _c01(candidate: dict[str, Any]) -> bool:
    return set(candidate["changed_paths"]).issubset(set(candidate["allowed_paths"]))


def _c02(candidate: dict[str, Any]) -> bool:
    return set(candidate["read_paths"]).issubset(set(candidate["declared_inputs"]))


def _c03(candidate: dict[str, Any]) -> bool:
    return candidate["protected_after"] == candidate["protected_before"]


def _c04(candidate: dict[str, Any]) -> bool:
    return type(candidate["artifact"]["result"]) is str and type(candidate["artifact"]["count"]) is int


def _c05(candidate: dict[str, Any]) -> bool:
    return all(candidate["checks"].get(name) is True for name in ("required", "fixed"))


def _c06(candidate: dict[str, Any]) -> bool:
    return not (candidate["required_check_failed"] and candidate["declared_success"])


def _c07(candidate: dict[str, Any]) -> bool:
    return set(candidate["dependency_changes"]).issubset(set(candidate["allowed_dependency_changes"]))


def _c08(candidate: dict[str, Any]) -> bool:
    return candidate["transfer_marker"] not in candidate["artifact_text"]


def _c09(candidate: dict[str, Any]) -> bool:
    return set(candidate["tools_used"]).issubset(set(candidate["fixed_tools"]))


def _c10(candidate: dict[str, Any]) -> bool:
    return (
        candidate["report"]["artifact_id"] == candidate["artifact"]["id"]
        and candidate["report"]["check_id"] == candidate["check"]["id"]
    )


_CONSTRAINT_CHECKS: dict[str, Callable[[dict[str, Any]], bool]] = {
    "C01": _c01,
    "C02": _c02,
    "C03": _c03,
    "C04": _c04,
    "C05": _c05,
    "C06": _c06,
    "C07": _c07,
    "C08": _c08,
    "C09": _c09,
    "C10": _c10,
}


def _constraint_candidate(control: str, state: str) -> dict[str, Any]:
    allowed = state == "good"
    candidates: dict[str, dict[str, Any]] = {
        "C01": {
            "changed_paths": ["candidate/allowed.txt" if allowed else "candidate/forbidden.txt"],
            "allowed_paths": ["candidate/allowed.txt"],
        },
        "C02": {
            "read_paths": ["fixtures/input.json" if allowed else "host/secret.txt"],
            "declared_inputs": ["fixtures/input.json"],
        },
        "C03": {"protected_before": "protected-v1", "protected_after": "protected-v1" if allowed else "altered"},
        "C04": {"artifact": {"result": "ok", "count": 1 if allowed else "1"}},
        "C05": {"checks": {"required": True, "fixed": allowed}},
        "C06": {"required_check_failed": not allowed, "declared_success": True},
        "C07": {
            "dependency_changes": ["dep/base"] if allowed else ["dep/base", "dep/extra"],
            "allowed_dependency_changes": ["dep/base"],
        },
        "C08": {"transfer_marker": "GAH_SYNTHETIC_TRANSFER_MARKER", "artifact_text": "safe" if allowed else "GAH_SYNTHETIC_TRANSFER_MARKER"},
        "C09": {"tools_used": ["fixed-parser"] if allowed else ["fixed-parser", "network-tool"], "fixed_tools": ["fixed-parser"]},
        "C10": {
            "artifact": {"id": "artifact-1"},
            "check": {"id": "check-1"},
            "report": {"artifact_id": "artifact-1", "check_id": "check-1" if allowed else "check-missing"},
        },
    }
    return candidates[control]


def _constraint_observation(control: str, state: str) -> dict[str, str]:
    candidate = _constraint_candidate(control, state)
    passed = _CONSTRAINT_CHECKS[control](candidate)
    return {"check": "PASS" if passed else "FAIL"}


_MUTATION_BASELINES: dict[str, dict[str, Any]] = {
    "F01": {"required_check": True},
    "F02": {"observed": 10, "threshold": 10},
    "F03": {"input_ref": "input-a"},
    "F04": {"evidence_fresh": True},
    "F05": {"dependency_state": "healthy"},
}
_MUTATION_FIELDS = {
    "F01": ("required_check",),
    "F02": ("observed", "threshold"),
    "F03": ("input_ref",),
    "F04": ("evidence_fresh",),
    "F05": ("dependency_state",),
}


def _baseline_pass(family: str, baseline: dict[str, Any]) -> bool:
    if family == "F01":
        return baseline["required_check"] is True
    if family == "F02":
        return baseline["observed"] >= baseline["threshold"]
    if family == "F03":
        return baseline["input_ref"] == "input-a"
    if family == "F04":
        return baseline["evidence_fresh"] is True
    if family == "F05":
        return baseline["dependency_state"] == "healthy"
    raise ValueError("未知mutation family")


def _mutation_observation(family: str, state: str) -> dict[str, Any]:
    baseline = _MUTATION_BASELINES[family]
    detector_works = state == "healthy"
    candidate = {
        "reached": True,
        "detector_signal": detector_works,
        "unrelated_failure": False,
    }
    if family == "F01":
        candidate["required_check"] = False
    elif family == "F02":
        candidate["observed"] = 9
        candidate["threshold"] = 10
    elif family == "F03":
        candidate["input_ref"] = "input-b"
    elif family == "F04":
        candidate["evidence_fresh"] = False
    else:
        candidate["dependency_state"] = "decayed"
    mutation_applied = any(candidate.get(field) != baseline.get(field) for field in _MUTATION_FIELDS[family])
    baseline_result = "PASS" if _baseline_pass(family, baseline) else "FAIL"
    reached = candidate["reached"] is True
    detected = mutation_applied and reached and candidate["detector_signal"] is True
    unrelated_failure = candidate["unrelated_failure"] is True
    return {
        "baseline": baseline_result,
        "mutation_applied": mutation_applied,
        "reached": reached,
        "detected": detected,
        "unrelated_failure": unrelated_failure,
    }


def _proc_status_value(name: str) -> str | None:
    try:
        for line in Path("/proc/self/status").read_text(encoding="ascii").splitlines():
            if line.startswith(name + ":"):
                return line.split(":", 1)[1].strip()
    except (OSError, UnicodeError):
        return None
    return None


def _effective_id(name: str) -> int | None:
    value = _proc_status_value(name)
    if value is None:
        return None
    fields = value.split()
    if len(fields) < 2:
        return None
    try:
        return int(fields[1])
    except ValueError:
        return None


def _read_text(path: str) -> str | None:
    try:
        return Path(path).read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        return None


def _limit_is_at_most(path: str, maximum: int) -> bool:
    value = _read_text(path)
    if value is None or value == "max":
        return False
    try:
        parsed = int(value)
        return 0 < parsed <= maximum
    except ValueError:
        return False


def _ipv4_without_external_default(routes: str | None) -> bool:
    """IPv4経路を検査し、loopback以外のdefault routeがない場合だけtrueにする。"""
    if routes is None:
        return False
    lines = routes.splitlines()
    if not lines or lines[0].split()[:2] != ["Iface", "Destination"]:
        return False
    for line in lines[1:]:
        fields = line.split()
        if not fields:
            continue
        if (len(fields) != 11 or _IDENTIFIER.fullmatch(fields[0]) is None
                or any(_HEX8.fullmatch(fields[index]) is None for index in (1, 2, 7))
                or _HEX4.fullmatch(fields[3]) is None
                or any(not fields[index].isdigit() for index in (4, 5, 6, 8, 9, 10))):
            return False
        if fields[1] == "00000000" and fields[0] != "lo":
            return False
    return True


def _ipv6_without_external_default(routes: str | None) -> bool:
    """IPv6経路を検査し、loopback上のdefault相当経路は許容する。"""
    if routes is None:
        return False
    for line in routes.splitlines():
        fields = line.split()
        if not fields:
            continue
        if (len(fields) != 10 or any(_HEX32.fullmatch(fields[index]) is None for index in (0, 2, 4))
                or any(_HEX2.fullmatch(fields[index]) is None for index in (1, 3))
                or any(_HEX8.fullmatch(fields[index]) is None for index in (5, 6, 7, 8))
                or int(fields[1], 16) > 128 or int(fields[3], 16) > 128
                or _IDENTIFIER.fullmatch(fields[9]) is None):
            return False
        if fields[0] == "0" * 32 and int(fields[1], 16) == 0 and fields[9] != "lo":
            return False
    return True


def _isolation_checks() -> dict[str, bool]:
    """container内の固定境界を観測する。外部送信や任意fixture実行はしない。"""
    nonroot = _effective_id("Uid") == 65532 and _effective_id("Gid") == 65532

    root_mount_readonly = False
    mounts = _read_text("/proc/mounts")
    if mounts:
        for line in mounts.splitlines():
            fields = line.split()
            if len(fields) >= 4 and fields[1] == "/":
                root_mount_readonly = "ro" in fields[3].split(",")
                break

    root_write_denied = False
    probe_path = "/.gah_root_write_probe"
    try:
        descriptor = os.open(probe_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError as error:
        root_write_denied = error.errno in {errno.EACCES, errno.EPERM, errno.EROFS}
    else:
        os.close(descriptor)
        try:
            os.unlink(probe_path)
        except OSError:
            pass
    root_readonly = root_mount_readonly and root_write_denied

    cape = _proc_status_value("CapEff")
    capabilities_dropped = False
    if cape:
        try:
            capabilities_dropped = int(cape, 16) == 0
        except ValueError:
            pass
    no_new_privileges = _proc_status_value("NoNewPrivs") == "1"
    seccomp = _proc_status_value("Seccomp")
    seccomp_filter = seccomp in {"1", "2"}

    ipv4_without_default = _ipv4_without_external_default(_read_text("/proc/net/route"))
    ipv6_without_default = _ipv6_without_external_default(_read_text("/proc/net/ipv6_route"))

    try:
        interfaces = {entry.name for entry in Path("/sys/class/net").iterdir()}
    except OSError:
        interfaces = None
    only_loopback = interfaces == {"lo"}
    network_unreachable = ipv4_without_default and ipv6_without_default and only_loopback
    # cgroup v2の固定上限を優先し、v1では利用可能なファイルを確認する。
    pid_limit = _limit_is_at_most("/sys/fs/cgroup/pids.max", 32) or _limit_is_at_most("/sys/fs/cgroup/pids/pids.max", 32)
    memory_limit = _limit_is_at_most("/sys/fs/cgroup/memory.max", 128 * 1024 * 1024) or _limit_is_at_most("/sys/fs/cgroup/memory/memory.limit_in_bytes", 128 * 1024 * 1024)
    cpu_max = _read_text("/sys/fs/cgroup/cpu.max")
    cpu_limit = False
    if cpu_max:
        fields = cpu_max.split()
        if len(fields) == 2 and fields[0] != "max":
            try:
                quota, period = int(fields[0]), int(fields[1])
                cpu_limit = quota > 0 and period > 0 and quota <= period // 2
            except ValueError:
                pass
    else:
        quota = _read_text("/sys/fs/cgroup/cpu/cpu.cfs_quota_us")
        period = _read_text("/sys/fs/cgroup/cpu/cpu.cfs_period_us")
        if quota and period:
            try:
                quota_value, period_value = int(quota), int(period)
                cpu_limit = quota_value > 0 and period_value > 0 and quota_value <= period_value // 2
            except ValueError:
                pass
    return {
        "nonroot": nonroot,
        "root_readonly": root_readonly,
        "network_unreachable": network_unreachable,
        "capabilities_dropped": capabilities_dropped,
        "no_new_privileges": no_new_privileges,
        "seccomp_filter": seccomp_filter,
        "pid_limit": pid_limit,
        "memory_limit": memory_limit,
        "cpu_limit": cpu_limit,
    }


def _run_probe(scenario: str, binding: dict[str, Any]) -> int:
    if scenario == "probe:isolation":
        envelope = {"schema_version": 1, "kind": "gah_isolation_probe", "binding": binding, "checks": _isolation_checks()}
        sys.stdout.write(json.dumps(envelope, sort_keys=True, separators=(",", ":")) + "\n")
        return 0
    if scenario == "probe:child_timeout":
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], shell=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        started = time.monotonic()
        try:
            time.sleep(60)
        finally:
            child.terminate()
            try:
                child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=2)
        result = {"schema_version": 1, "kind": "gah_timeout_probe", "binding": binding, "checks": {"child_stopped": child.poll() is not None, "parent_elapsed_at_least_60": time.monotonic() - started >= 60}}
        sys.stdout.write(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n")
        return 0
    if scenario == "probe:oversized":
        sys.stdout.write("X" * (256 * 1024 + 1))
        sys.stdout.flush()
        return 0
    if scenario == "probe:malformed":
        sys.stdout.write("not-json\n")
        return 0
    if scenario == "probe:rejected_marker":
        sys.stdout.write("GAH_SYNTHETIC_REJECTED_MARKER\n")
        return 0
    return 2


_SCENARIOS = tuple(
    [f"constraint:C{number:02d}:{state}" for number in range(1, 11) for state in ("good", "bad")]
    + [f"mutation:F{number:02d}:{state}" for number in range(1, 6) for state in ("healthy", "decayed")]
    + ["probe:isolation", "probe:child_timeout", "probe:oversized", "probe:malformed", "probe:rejected_marker", "probe:crash"]
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--scenario", choices=_SCENARIOS, required=True)
    try:
        args = parser.parse_args(argv)
        binding = _read_binding()
        if args.scenario.startswith("constraint:"):
            _, control, state = args.scenario.split(":")
            _emit_envelope(binding, "constraint", _constraint_observation(control, state))
            return 0
        if args.scenario.startswith("mutation:"):
            _, family, state = args.scenario.split(":")
            _emit_envelope(binding, "mutation", _mutation_observation(family, state))
            return 0
        if args.scenario == "probe:crash":
            return 2
        return _run_probe(args.scenario, binding)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        print("fixture入力または実行に失敗しました", file=sys.stderr)
        return 2
    finally:
        _emit_worker_metrics()


def _worker_metric_integer(value):
    value=value.strip()
    if not value.isdigit() or len(value)>19:return None
    parsed=int(value)
    return parsed if parsed<=(1<<63)-1 else None


def _worker_cgroup_metrics():
    """終了直前に固定cgroup累積値だけを読み、測定不能値はnullにする。"""
    def read(path):
        try:
            with Path(path).open('rb') as stream:raw=stream.read(65537)
            return raw.decode('ascii') if len(raw)<=65536 else None
        except (OSError,UnicodeError):
            return None
    def keyed(path,key):
        value=read(path)
        if value is not None:
            for line in value.splitlines():
                fields=line.split()
                if len(fields)==2 and fields[0]==key:return _worker_metric_integer(fields[1])
        return None
    cpu=keyed('/sys/fs/cgroup/cpu.stat','usage_usec')
    cpu=cpu*1000 if cpu is not None else None
    if cpu is None:
        value=read('/sys/fs/cgroup/cpuacct/cpuacct.usage')
        cpu=_worker_metric_integer(value) if value is not None else None
    memory=read('/sys/fs/cgroup/memory.peak')
    if memory is None or not memory.strip().isdigit():memory=read('/sys/fs/cgroup/memory/memory.max_usage_in_bytes')
    memory=_worker_metric_integer(memory) if memory is not None else None
    rb=wb=None
    value=read('/sys/fs/cgroup/io.stat')
    if value is not None:
        totals={'rbytes':0,'wbytes':0};devices=set();ok=True
        for line in value.splitlines():
            fields=line.split()
            if not fields or not re.fullmatch(r'[0-9]+:[0-9]+',fields[0]) or fields[0] in devices:ok=False;break
            devices.add(fields[0]);found=set()
            for item in fields[1:]:
                pair=item.split('=',1)
                if len(pair)!=2 or pair[0] in found or _worker_metric_integer(pair[1]) is None:ok=False;break
                found.add(pair[0])
                if pair[0] in totals:
                    totals[pair[0]]+=int(pair[1])
                    if totals[pair[0]]>(1<<63)-1:ok=False;break
            if not {'rbytes','wbytes'}.issubset(found):ok=False
            if not ok:break
        if ok and devices:rb,wb=totals['rbytes'],totals['wbytes']
    else:
        value=read('/sys/fs/cgroup/blkio/blkio.io_service_bytes_recursive')
        if value is not None:
            totals={'Read':0,'Write':0};seen=set();devices=set();ok=True
            for line in value.splitlines():
                fields=line.split()
                if not fields or not re.fullmatch(r'[0-9]+:[0-9]+',fields[0]):ok=False;break
                device=fields[0];devices.add(device)
                if len(fields)==3 and fields[1]=='Total':
                    if _worker_metric_integer(fields[2]) is None or (device,'Total') in seen:ok=False;break
                    seen.add((device,'Total'));continue
                if len(fields)==4 and fields[1] in {'Sync','Async'} and fields[2] in totals:
                    key=(device,fields[1]+' '+fields[2])
                    if _worker_metric_integer(fields[3]) is None or key in seen:ok=False;break
                    seen.add(key);continue
                if len(fields)!=3 or fields[1] not in totals or _worker_metric_integer(fields[2]) is None or (device,fields[1]) in seen:ok=False;break
                seen.add((device,fields[1]));totals[fields[1]]+=int(fields[2])
                if totals[fields[1]]>(1<<63)-1:ok=False;break
            if ok and devices and all((device,kind) in seen for device in devices for kind in totals):rb,wb=totals['Read'],totals['Write']
    return {'schema_version':1,'kind':'gah_worker_metrics','capture_scope':'container_cgroup_until_worker_exit',
        'capture_status':'CAPTURED','cpu_ns':cpu,'rss_peak_bytes':None,'memory_peak_bytes':memory,
        'io_read_bytes':rb,'io_write_bytes':wb,'valid_for_slo':False}


def _emit_worker_metrics():
    try:
        data=json.dumps(_worker_cgroup_metrics(),sort_keys=True,separators=(',',':'))
        if len(data)>4096:raise ValueError()
    except Exception:
        data='{"capture_status":"INVALID"}'
    sys.stderr.write('\nGAH-WORKER-METRICS-V1 '+data+'\n')
    sys.stderr.flush()


if __name__ == "__main__":
    raise SystemExit(main())
