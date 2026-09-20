
"""AuthorityRuntimeから固定cgroup v2 snapshotを取得するprovider。

このmoduleは任意container列挙、任意command、任意path、URLを受け付けない。
AuthorityRuntimeが保持する固定deploymentのbroker/known clientだけをinspectし、
固定したPython literalを同じcontainer内で実行する。実runtimeへの接続は
callerが行い、通常試験はfake runtimeだけをDIする。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import time
from typing import Any, Iterable, Mapping

from gah.contracts import (
    ContractError,
    MAX_INTEGER,
    decode_document,
    require_id,
    require_text,
)
from gah.resource_probe import parse_snapshot
from gah.wire import canonical_bytes


BROKER_UID = 12000
DEFAULT_FIXED_UID = 12004
FIXED_CLIENT_UIDS = frozenset({12001, 12002, 12003, 12004})
MAX_OUTPUT_BYTES = 65536
MAX_CLIENTS = 64
CGROUP_PATHS = (
    ("cpu_stat", "cpu.stat"),
    ("memory_current", "memory.current"),
    ("memory_peak", "memory.peak"),
    ("io_stat", "io.stat"),
)
CGROUP_PAYLOAD_FIELDS = frozenset(name for name, _ in CGROUP_PATHS)
CPU_STAT_FIELDS = frozenset({
    "usage_usec",
    "user_usec",
    "system_usec",
    "nr_periods",
    "nr_throttled",
    "throttled_usec",
    "nr_bursts",
    "burst_usec",
})
IO_STAT_FIELDS = frozenset({
    "rbytes",
    "wbytes",
    "rios",
    "wios",
    "dbytes",
    "dios",
})
CLIENT_MODES = frozenset({"client", "client-host", "probe"})
COVERAGE_REASONS = frozenset({
    "WORKER_SCOPE_UNCONNECTED",
    "CLIENT_SCOPE_UNCONNECTED",
    "BROKER_UNAVAILABLE",
    "CLIENT_UNAVAILABLE",
    "SCOPE_NOT_RUNNING",
    "CONFIG_MISMATCH",
    "STATS_UNAVAILABLE",
    "CGROUP_PAYLOAD_INVALID",
    "SCOPE_DISAPPEARED",
    "IDENTITY_CHANGED",
    "RESTART_DETECTED",
    "CLOCK_UNAVAILABLE",
    "HOST_CPU_UNAVAILABLE",
    "RUNTIME_STATE_INVALID",
})
_NAME_RE = re.compile(r"gah-authority-[0-9a-f]{32}\Z")
_CLIENT_RE_TEMPLATE = r"{prefix}-client-[0-9a-f]{{32}}\Z"
_UID_RE = re.compile(r"(12001|12002|12003|12004):(12001|12002|12003|12004)\Z")
_DEVICE_RE = re.compile(r"[0-9]+:[0-9]+\Z")
_CGROUP_READ_SCRIPT = (
    "import json,pathlib\n"
    "p=pathlib.Path('/sys/fs/cgroup')\n"
    "def r(n):\n"
    "    try:\n"
    "        with (p/n).open('rb') as f:\n"
    "            data=f.read(16385)\n"
    "        if len(data)>16384:\n"
    "            return None\n"
    "        return data.decode('ascii')\n"
    "    except (OSError,UnicodeError):\n"
    "        return None\n"
    "print(json.dumps({'cpu_stat':r('cpu.stat'),'memory_current':r('memory.current'),"
    "'memory_peak':r('memory.peak'),'io_stat':r('io.stat')},"
    "separators=(',',':'),sort_keys=True))"
)
FIXED_CGROUP_READ_SCRIPT = _CGROUP_READ_SCRIPT


class AuthorityResourceProbeError(ContractError):
    """固定Authority resource providerのエラー。"""


def _uint(value: Any, code: str) -> int:
    if type(value) is not int or not 0 <= value <= MAX_INTEGER:
        raise AuthorityResourceProbeError(code)
    return value


def _text(value: Any, code: str) -> str:
    try:
        require_text(value)
    except ContractError:
        raise AuthorityResourceProbeError(code) from None
    return value


def _parse_decimal(value: Any, code: str) -> int | None:
    if value is None:
        return None
    if type(value) is not str:
        raise AuthorityResourceProbeError(code)
    stripped = value.strip()
    if not stripped or not stripped.isdecimal():
        raise AuthorityResourceProbeError(code)
    try:
        parsed = int(stripped, 10)
    except ValueError:
        raise AuthorityResourceProbeError(code) from None
    return _uint(parsed, code)


def _parse_counter_lines(
    value: str | None,
    *,
    allowed: frozenset[str],
    required: frozenset[str],
) -> dict[str, int] | None:
    if value is None:
        return None
    if type(value) is not str:
        raise AuthorityResourceProbeError("CGROUP_PAYLOAD_INVALID")
    lines = value.splitlines()
    if not lines or any(not line.strip() for line in lines):
        raise AuthorityResourceProbeError("CGROUP_PAYLOAD_INVALID")
    parsed: dict[str, int] = {}
    for line in lines:
        parts = line.split()
        if len(parts) != 2 or parts[0] in parsed or parts[0] not in allowed:
            raise AuthorityResourceProbeError("CGROUP_PAYLOAD_INVALID")
        parsed[parts[0]] = _parse_decimal(parts[1], "CGROUP_PAYLOAD_INVALID")  # type: ignore[assignment]
    if not required.issubset(parsed):
        return None
    return parsed


def _parse_io_stat(value: str | None) -> tuple[int | None, int | None]:
    if value is None:
        return None, None
    if type(value) is not str:
        raise AuthorityResourceProbeError("CGROUP_PAYLOAD_INVALID")
    lines = value.splitlines()
    if not lines:
        # 空のio.statではdeviceの観測がない。0と推定せずI/Oだけを欠測にする。
        return None, None
    if any(not line.strip() for line in lines):
        raise AuthorityResourceProbeError("CGROUP_PAYLOAD_INVALID")
    devices: set[str] = set()
    reads = 0
    writes = 0
    for line in lines:
        parts = line.split()
        if len(parts) < 2 or parts[0] in devices or not _DEVICE_RE.fullmatch(parts[0]):
            raise AuthorityResourceProbeError("CGROUP_PAYLOAD_INVALID")
        devices.add(parts[0])
        values: dict[str, int] = {}
        for item in parts[1:]:
            if "=" not in item:
                raise AuthorityResourceProbeError("CGROUP_PAYLOAD_INVALID")
            key, raw = item.split("=", 1)
            if key in values or key not in IO_STAT_FIELDS:
                raise AuthorityResourceProbeError("CGROUP_PAYLOAD_INVALID")
            values[key] = _uint(
                _parse_decimal(raw, "CGROUP_PAYLOAD_INVALID"),
                "CGROUP_PAYLOAD_INVALID",
            )
        if "rbytes" not in values or "wbytes" not in values:
            return None, None
        reads += values["rbytes"]
        writes += values["wbytes"]
        if reads > MAX_INTEGER or writes > MAX_INTEGER:
            raise AuthorityResourceProbeError("CGROUP_PAYLOAD_INVALID")
    return reads, writes


def _checked_payload(value: Any) -> dict[str, Any]:
    if type(value) is not dict:
        raise AuthorityResourceProbeError("CGROUP_PAYLOAD_INVALID")
    try:
        checked = decode_document(canonical_bytes(value))
    except (ContractError, TypeError, ValueError, UnicodeError, RecursionError):
        raise AuthorityResourceProbeError("CGROUP_PAYLOAD_INVALID") from None
    if set(checked) != set(CGROUP_PAYLOAD_FIELDS):
        raise AuthorityResourceProbeError("CGROUP_PAYLOAD_INVALID")
    return checked


def parse_cgroup_payload(value: dict[str, Any]) -> dict[str, Any]:
    """固定cgroup v2 read payloadをCPU ns/IO bytes/memory bytesへ変換する。"""
    value = _checked_payload(value)
    cpu_lines = _parse_counter_lines(
        value["cpu_stat"],
        allowed=CPU_STAT_FIELDS,
        required=frozenset({"usage_usec"}),
    )
    cpu_ns = None if cpu_lines is None else cpu_lines["usage_usec"] * 1000
    if cpu_ns is not None and cpu_ns > MAX_INTEGER:
        raise AuthorityResourceProbeError("CGROUP_PAYLOAD_INVALID")
    memory_current = _parse_decimal(
        value["memory_current"], "CGROUP_PAYLOAD_INVALID",
    )
    memory_peak = _parse_decimal(
        value["memory_peak"], "CGROUP_PAYLOAD_INVALID",
    )
    io_read, io_write = _parse_io_stat(value["io_stat"])
    missing = [
        name for name, observed in (
            ("cpu_ns", cpu_ns),
            ("memory_current_bytes", memory_current),
            ("memory_peak_bytes", memory_peak),
            ("io_read_bytes", io_read),
            ("io_write_bytes", io_write),
        )
        if observed is None
    ]
    return {
        "cpu_ns": cpu_ns,
        "memory_current_bytes": memory_current,
        "memory_peak_bytes": memory_peak,
        "io_read_bytes": io_read,
        "io_write_bytes": io_write,
        "missing": missing,
    }


def parse_cgroup_response(raw: bytes | str) -> dict[str, Any]:
    """Decode and parse a bounded fixed exec response."""
    if type(raw) is bytes:
        raw_bytes = raw
    elif type(raw) is str:
        try:
            raw_bytes = raw.encode("utf-8")
        except UnicodeError:
            raise AuthorityResourceProbeError("CGROUP_PAYLOAD_INVALID") from None
    else:
        raise AuthorityResourceProbeError("CGROUP_PAYLOAD_INVALID")
    if len(raw_bytes) > MAX_OUTPUT_BYTES:
        raise AuthorityResourceProbeError("CGROUP_PAYLOAD_INVALID")
    try:
        value = decode_document(raw_bytes)
    except (ContractError, UnicodeError, ValueError, RecursionError):
        raise AuthorityResourceProbeError("CGROUP_PAYLOAD_INVALID") from None
    return parse_cgroup_payload(value)


def _scope_state(data: Any, name: str, uid: int, mode: str) -> dict[str, Any]:
    if type(data) is not dict:
        raise AuthorityResourceProbeError("CONFIG_MISMATCH")
    if data.get("Name") != "/" + name:
        raise AuthorityResourceProbeError("CONFIG_MISMATCH")
    config = data.get("Config")
    state = data.get("State")
    if type(config) is not dict or type(state) is not dict:
        raise AuthorityResourceProbeError("CONFIG_MISMATCH")
    if config.get("User") != f"{uid}:{uid}" or config.get("Cmd") != [mode]:
        raise AuthorityResourceProbeError("CONFIG_MISMATCH")
    identifier = _text(data.get("Id"), "CONFIG_MISMATCH")
    started_at = _text(state.get("StartedAt"), "CONFIG_MISMATCH")
    pid = state.get("Pid")
    if type(pid) is not int or pid < 0:
        raise AuthorityResourceProbeError("CONFIG_MISMATCH")
    restart_count = _uint(data.get("RestartCount"), "CONFIG_MISMATCH")
    running = state.get("Running")
    status = state.get("Status")
    oom_killed = state.get("OOMKilled")
    if type(running) is not bool or type(status) is not str:
        raise AuthorityResourceProbeError("CONFIG_MISMATCH")
    if type(oom_killed) is not bool:
        raise AuthorityResourceProbeError("CONFIG_MISMATCH")
    return {
        "id": identifier,
        "started_at": started_at,
        "pid": pid,
        "restart_count": restart_count,
        "running": running,
        "status": status,
        "oom_killed": oom_killed,
        "uid": uid,
        "mode": mode,
    }


def _client_uid_mode(data: Any) -> tuple[int, str]:
    if type(data) is not dict or type(data.get("Config")) is not dict:
        raise AuthorityResourceProbeError("CONFIG_MISMATCH")
    config = data["Config"]
    user = config.get("User")
    if type(user) is not str:
        raise AuthorityResourceProbeError("CONFIG_MISMATCH")
    match = _UID_RE.fullmatch(user)
    if match is None or match.group(1) != match.group(2):
        raise AuthorityResourceProbeError("CONFIG_MISMATCH")
    uid = int(match.group(1), 10)
    command = config.get("Cmd")
    if type(command) is not list or len(command) != 1 or command[0] not in CLIENT_MODES:
        raise AuthorityResourceProbeError("CONFIG_MISMATCH")
    return uid, command[0]


class AuthorityResourceProbeProvider:
    """固定Authority deploymentのbroker/known client cgroup provider。"""

    def __init__(
        self,
        runtime: Any,
        *,
        run_id: str,
        fixed_uid: int = DEFAULT_FIXED_UID,
        max_output_bytes: int = MAX_OUTPUT_BYTES,
    ) -> None:
        try:
            require_id(run_id)
        except ContractError:
            raise AuthorityResourceProbeError("INVALID_INPUT") from None
        if type(fixed_uid) is not int or fixed_uid not in FIXED_CLIENT_UIDS:
            raise AuthorityResourceProbeError("INVALID_INPUT")
        if (
            type(max_output_bytes) is not int
            or not 1024 <= max_output_bytes <= MAX_OUTPUT_BYTES
        ):
            raise AuthorityResourceProbeError("INVALID_INPUT")
        self.runtime = runtime
        self.run_id = run_id
        self.fixed_uid = fixed_uid
        self.max_output_bytes = max_output_bytes
        self._coverage_reasons: list[str] = []
        self._epochs: dict[str, int] = {}
        self._markers: dict[str, tuple[str, str]] = {}
        self._sample_seq = 0
        self._prefix = self._validate_runtime_state()
        self._broker_name = self._prefix + "-broker"
        self._client_names = self._known_client_names()
        # このproviderはworkerの実scopeをまだ受け取るcontractへ接続していない。
        self._add_coverage("WORKER_SCOPE_UNCONNECTED")
        self._expected_scope_ids = (
            (self._broker_name,) + self._client_names + (self._host_scope_id,)
        )

    @property
    def endpoint(self) -> str | None:
        value = getattr(self.runtime, "endpoint", None)
        return value if type(value) is str else None

    @property
    def host_scope_id(self) -> str:
        return self._host_scope_id

    @property
    def expected_scope_ids(self) -> tuple[str, ...]:
        return self._expected_scope_ids

    @property
    def coverage_complete(self) -> bool:
        return not self._coverage_reasons

    @property
    def coverage_reasons(self) -> tuple[str, ...]:
        return tuple(self._coverage_reasons)

    @property
    def coverage(self) -> dict[str, Any]:
        return {
            "complete": self.coverage_complete,
            "reasons": list(self._coverage_reasons),
            "expected_scope_ids": list(self._expected_scope_ids),
        }

    def _add_coverage(self, reason: str) -> None:
        if reason not in COVERAGE_REASONS:
            raise AuthorityResourceProbeError("INVALID_INPUT")
        if reason not in self._coverage_reasons:
            self._coverage_reasons.append(reason)

    def _validate_runtime_state(self) -> str:
        prefix = getattr(self.runtime, "prefix", None)
        if type(prefix) is not str or _NAME_RE.fullmatch(prefix) is None:
            raise AuthorityResourceProbeError("RUNTIME_STATE_INVALID")
        state = getattr(self.runtime, "state", None)
        if type(state) is not dict or state.get("prefix") != prefix:
            raise AuthorityResourceProbeError("RUNTIME_STATE_INVALID")
        names = state.get("containers")
        if (
            type(names) is not list
            or len(names) > MAX_CLIENTS + 1
            or any(type(name) is not str for name in names)
            or len(set(names)) != len(names)
        ):
            raise AuthorityResourceProbeError("RUNTIME_STATE_INVALID")
        for name in names:
            if not (
                name == prefix + "-broker"
                or re.fullmatch(_CLIENT_RE_TEMPLATE.format(prefix=re.escape(prefix)), name)
            ):
                raise AuthorityResourceProbeError("RUNTIME_STATE_INVALID")
        return prefix

    @property
    def _host_scope_id(self) -> str:
        return self._prefix + "-host-harness"

    def _known_client_names(self) -> tuple[str, ...]:
        names: list[str] = []
        state_names = self.runtime.state["containers"]
        candidates: list[Any] = list(state_names)
        for attribute in ("_running_clients", "_reusable_clients"):
            mapping = getattr(self.runtime, attribute, {})
            if type(mapping) is not dict:
                self._add_coverage("RUNTIME_STATE_INVALID")
                continue
            candidates.extend(mapping.values())
        client_re = re.compile(_CLIENT_RE_TEMPLATE.format(prefix=re.escape(self._prefix)))
        for name in candidates:
            if type(name) is not str:
                self._add_coverage("RUNTIME_STATE_INVALID")
                continue
            if client_re.fullmatch(name) and name not in names:
                names.append(name)
        return tuple(names)

    def _verify(self, data: Any, uid: int, mode: str) -> dict[str, Any]:
        verify = getattr(self.runtime, "_verify_config", None)
        if not callable(verify):
            raise AuthorityResourceProbeError("CONFIG_MISMATCH")
        try:
            verify(data, uid, mode)
        except Exception:
            raise AuthorityResourceProbeError("CONFIG_MISMATCH") from None
        return _scope_state(data, data["Name"][1:], uid, mode)

    def _inspect(self, name: str, uid: int, mode: str) -> dict[str, Any]:
        inspect = getattr(self.runtime, "inspect", None)
        if not callable(inspect):
            raise AuthorityResourceProbeError("CONFIG_MISMATCH")
        try:
            data = inspect(name)
        except Exception:
            raise AuthorityResourceProbeError("SCOPE_DISAPPEARED") from None
        return self._verify(data, uid, mode)

    def _epoch(self, name: str, meta: dict[str, Any]) -> int:
        marker = (meta["id"], meta["started_at"])
        previous = self._markers.get(name)
        if previous is None:
            self._markers[name] = marker
            self._epochs[name] = 0
        elif previous != marker:
            self._markers[name] = marker
            self._epochs[name] = self._epochs.get(name, 0) + 1
            self._add_coverage("IDENTITY_CHANGED")
        return self._epochs[name]

    def _exec_args(self, name: str, uid: int) -> list[str]:
        return [
            "container",
            "exec",
            "--interactive",
            "--user",
            f"{uid}:{uid}",
            name,
            "/usr/local/bin/python",
            "-I",
            "-B",
            "-c",
            FIXED_CGROUP_READ_SCRIPT,
        ]

    def _read_payload(self, name: str, uid: int) -> dict[str, Any]:
        command = getattr(self.runtime, "command", None)
        if not callable(command):
            raise AuthorityResourceProbeError("STATS_UNAVAILABLE")
        try:
            raw = command(
                self._exec_args(name, uid),
                timeout=10,
                limit=self.max_output_bytes,
            )
        except Exception:
            raise AuthorityResourceProbeError("STATS_UNAVAILABLE") from None
        return parse_cgroup_response(raw)

    @staticmethod
    def _empty_scope(
        name: str,
        kind: str,
        meta: dict[str, Any],
        epoch: int,
    ) -> dict[str, Any]:
        return {
            "scope_id": name,
            "scope_kind": kind,
            "identity": meta["id"],
            "epoch": epoch,
            "restart_count": meta["restart_count"],
            "cpu_ns": None,
            "io_read_bytes": None,
            "io_write_bytes": None,
            "rss_bytes": None,
            "memory_current_bytes": None,
            "memory_peak_bytes": None,
            "parent_scope_id": None,
        }

    def _read_container(
        self,
        name: str,
        *,
        uid: int,
        mode: str,
        scope_kind: str = "container",
    ) -> dict[str, Any] | None:
        try:
            before = self._inspect(name, uid, mode)
        except AuthorityResourceProbeError as exc:
            if exc.code in {"SCOPE_DISAPPEARED", "STATS_UNAVAILABLE"}:
                self._add_coverage("SCOPE_DISAPPEARED")
            else:
                self._add_coverage(exc.code if exc.code in COVERAGE_REASONS else "CONFIG_MISMATCH")
            return None
        epoch = self._epoch(name, before)
        if (
            before["running"] is not True
            or before["status"] != "running"
            or before["pid"] <= 0
            or before["oom_killed"] is not False
        ):
            self._add_coverage("SCOPE_NOT_RUNNING")
            return None
        result = self._empty_scope(name, scope_kind, before, epoch)
        try:
            parsed = self._read_payload(name, uid)
        except AuthorityResourceProbeError as exc:
            self._add_coverage(
                exc.code if exc.code in COVERAGE_REASONS else "STATS_UNAVAILABLE",
            )
        else:
            result.update(
                cpu_ns=parsed["cpu_ns"],
                io_read_bytes=parsed["io_read_bytes"],
                io_write_bytes=parsed["io_write_bytes"],
                memory_current_bytes=parsed["memory_current_bytes"],
                memory_peak_bytes=parsed["memory_peak_bytes"],
            )
            if parsed["missing"]:
                self._add_coverage("STATS_UNAVAILABLE")
        try:
            after = self._inspect(name, uid, mode)
        except AuthorityResourceProbeError as exc:
            self._add_coverage(
                "SCOPE_DISAPPEARED"
                if exc.code == "SCOPE_DISAPPEARED"
                else "CONFIG_MISMATCH",
            )
            for field in (
                "cpu_ns",
                "io_read_bytes",
                "io_write_bytes",
                "memory_current_bytes",
                "memory_peak_bytes",
            ):
                result[field] = None
            return result
        if (
            after["id"] != before["id"]
            or after["started_at"] != before["started_at"]
            or after["pid"] != before["pid"]
            or after["restart_count"] != before["restart_count"]
            or after["running"] is not True
            or after["status"] != "running"
            or after["oom_killed"] is not False
        ):
            if after["id"] != before["id"] or after["started_at"] != before["started_at"]:
                self._add_coverage("IDENTITY_CHANGED")
            if after["restart_count"] != before["restart_count"]:
                self._add_coverage("RESTART_DETECTED")
            for field in (
                "cpu_ns",
                "io_read_bytes",
                "io_write_bytes",
                "memory_current_bytes",
                "memory_peak_bytes",
            ):
                result[field] = None
        return result

    def _host_scope(self) -> dict[str, Any]:
        try:
            cpu_ns = time.process_time_ns()
        except Exception:
            self._add_coverage("HOST_CPU_UNAVAILABLE")
            cpu_ns = None
        pid = os.getpid()
        return {
            "scope_id": self._host_scope_id,
            "scope_kind": "host_harness",
            "identity": f"pid-{pid}",
            "epoch": 0,
            "restart_count": 0,
            "cpu_ns": cpu_ns,
            "io_read_bytes": None,
            "io_write_bytes": None,
            "rss_bytes": None,
            "memory_current_bytes": None,
            "memory_peak_bytes": None,
            "parent_scope_id": None,
        }

    def final_client_observations(self) -> dict[str, Any]:
        """終了前の既知clientを観測。開始後追加のscopeも欠測を隠さず保存する。"""
        scopes = []
        missing = []
        for name in self._known_client_names():
            try:
                data = self.runtime.inspect(name)
                uid, mode = _client_uid_mode(data)
                scope = self._read_container(name, uid=uid, mode=mode)
            except Exception:
                # Runtime errors may contain Docker command details. Keep only
                # fixed coverage codes and continue collecting other clients.
                self._add_coverage("CLIENT_UNAVAILABLE")
                self._add_coverage("CLIENT_SCOPE_UNCONNECTED")
                scope = None
            if scope is None:
                missing.append(name)
            else:
                scopes.append(scope)
        return {
            "scope": "client_cgroup_cumulative_before_cleanup",
            "scopes": scopes, "missing_scope_ids": missing,
            "present_at_interval_start": list(self._client_names),
            "coverage": self.coverage,
            "valid_for_slo": False, "ci_eligible": False,
        }

    def snapshot(self) -> Mapping[str, Any]:
        """broker/known clientのinspect前後と固定cgroup readを一度だけ行う。"""
        try:
            captured_ns = time.perf_counter_ns()
        except Exception:
            self._add_coverage("CLOCK_UNAVAILABLE")
            raise AuthorityResourceProbeError("CLOCK_UNAVAILABLE") from None
        self._sample_seq += 1
        scopes: list[dict[str, Any]] = []
        broker = self._read_container(
            self._broker_name, uid=BROKER_UID, mode="broker",
        )
        if broker is not None:
            scopes.append(broker)
        for name in self._client_names:
            client = None
            try:
                # clientのUID/modeはinspectしたConfigから読み、外部入力では指定しない。
                inspect = getattr(self.runtime, "inspect", None)
                if not callable(inspect):
                    raise AuthorityResourceProbeError("CONFIG_MISMATCH")
                data = inspect(name)
                uid, mode = _client_uid_mode(data)
                if uid not in FIXED_CLIENT_UIDS:
                    self._add_coverage("CLIENT_SCOPE_UNCONNECTED")
                    continue
                # Use only UID/mode validated by inspect and the fixed allowlist.
                # fixed_uid is the default client UID; do not silently drop other fixed roles.
                client = self._read_container(
                    name, uid=uid, mode=mode,
                )
            except AuthorityResourceProbeError as exc:
                self._add_coverage("CLIENT_SCOPE_UNCONNECTED")
                self._add_coverage(
                    exc.code if exc.code in COVERAGE_REASONS else "CONFIG_MISMATCH",
                )
            except Exception:
                # AuthorityRuntime can report a collected/removed client as a
                # command failure after reconnect. Treat it as missing and keep
                # the broker, remaining clients, and host sample.
                self._add_coverage("CLIENT_UNAVAILABLE")
                self._add_coverage("CLIENT_SCOPE_UNCONNECTED")
            if client is not None:
                scopes.append(client)
        scopes.append(self._host_scope())
        value = {
            "schema_version": 1,
            "captured_ns": captured_ns,
            "sample_seq": self._sample_seq,
            "scopes": scopes,
        }
        try:
            return parse_snapshot(value).to_dict()
        except ContractError:
            self._add_coverage("STATS_UNAVAILABLE")
            # host scopeは常に存在するため、ここは通常到達しない。
            return parse_snapshot({
                "schema_version": 1,
                "captured_ns": captured_ns,
                "sample_seq": self._sample_seq,
                "scopes": [self._host_scope()],
            }).to_dict()


__all__ = [
    "BROKER_UID",
    "DEFAULT_FIXED_UID",
    "FIXED_CLIENT_UIDS",
    "MAX_OUTPUT_BYTES",
    "CGROUP_PATHS",
    "FIXED_CGROUP_READ_SCRIPT",
    "COVERAGE_REASONS",
    "AuthorityResourceProbeError",
    "parse_cgroup_payload",
    "parse_cgroup_response",
    "AuthorityResourceProbeProvider",
]
