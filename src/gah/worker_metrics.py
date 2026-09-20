"""固定 worker の終了前資源スナップショットを検証・永続化する。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import os
import stat
from typing import Any

from .bounded_files import BoundedFileError, write_bounded
from .normalized import validate_binding
from .wire import canonical_bytes


_FIELDS = {
    "schema_version", "kind", "capture_scope", "capture_status", "cpu_ns", "rss_peak_bytes",
    "memory_peak_bytes", "io_read_bytes", "io_write_bytes", "valid_for_slo",
}
_INTEGER_FIELDS = {"cpu_ns", "rss_peak_bytes", "memory_peak_bytes", "io_read_bytes", "io_write_bytes"}
_SID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_MAX_METRIC = (1 << 63) - 1


def validate_worker_metrics(value: Any) -> dict[str, Any]:
    """未知項目や未取得値のゼロ補完を拒否する。"""
    if type(value) is not dict or set(value) != _FIELDS:
        raise ValueError("INVALID_WORKER_METRICS")
    if (type(value["schema_version"]) is not int or value["schema_version"] != 1
            or value["kind"] != "gah_worker_metrics" or value["capture_scope"] != "container_cgroup_until_worker_exit"
            or type(value["capture_status"]) is not str
            or value["capture_status"] not in {"CAPTURED", "MISSING", "INVALID"}
            or type(value["valid_for_slo"]) is not bool or value["valid_for_slo"] is not False):
        raise ValueError("INVALID_WORKER_METRICS")
    for field in _INTEGER_FIELDS:
        item = value[field]
        if item is not None and (type(item) is not int or not 0 <= item <= _MAX_METRIC):
            raise ValueError("INVALID_WORKER_METRICS")
    # Docker cgroup memory.peak includes charged cache and is not an RSS peak.
    # This MVP has no trustworthy aggregate RSS peak, so it cannot satisfy SLO.
    if value["rss_peak_bytes"] is None and value["valid_for_slo"]:
        raise ValueError("INVALID_WORKER_METRICS")
    return dict(value)


def decode_worker_metrics(raw: bytes) -> dict[str, Any]:
    if type(raw) is not bytes or len(raw) > 16384:
        raise ValueError("INVALID_WORKER_METRICS")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if canonical_bytes(value) != raw:
            raise ValueError()
        return validate_worker_metrics(value)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ValueError("INVALID_WORKER_METRICS") from None


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("DUPLICATE_KEY")
        result[key] = value
    return result


def _bounded_integer(value: str) -> int | None:
    value = value.strip()
    if not value.isdigit() or len(value) > 19:
        return None
    parsed = int(value)
    return parsed if parsed <= _MAX_METRIC else None


def collect_cgroup_metrics(read_text=None) -> dict[str, Any]:
    """固定 cgroup v1/v2 の累積CPU、memory peak、block I/Oを読む。"""
    if read_text is None:
        def read_text(path):
            try:
                with Path(path).open("rb") as stream:
                    raw = stream.read(65537)
                if len(raw) > 65536:
                    return None
                return raw.decode("ascii")
            except (OSError, UnicodeError):
                return None

    def keyed(path, key):
        raw = read_text(path)
        if raw is None:
            return None
        for line in raw.splitlines():
            fields = line.split()
            if len(fields) == 2 and fields[0] == key:
                return _bounded_integer(fields[1])
        return None

    cpu_ns = keyed("/sys/fs/cgroup/cpu.stat", "usage_usec")
    if cpu_ns is not None:
        cpu_ns *= 1000
    else:
        raw = read_text("/sys/fs/cgroup/cpuacct/cpuacct.usage")
        cpu_ns = _bounded_integer(raw) if raw is not None else None

    memory_peak = None
    raw = read_text("/sys/fs/cgroup/memory.peak")
    if raw is not None:
        memory_peak = _bounded_integer(raw)
    if memory_peak is None:
        raw = read_text("/sys/fs/cgroup/memory/memory.max_usage_in_bytes")
        memory_peak = _bounded_integer(raw) if raw is not None else None

    read_bytes = write_bytes = None
    raw = read_text("/sys/fs/cgroup/io.stat")
    if raw is not None:
        totals = {"rbytes": 0, "wbytes": 0}
        seen_devices: set[str] = set()
        valid = True
        for line in raw.splitlines():
            fields = line.split()
            if not fields or not re.fullmatch(r"[0-9]+:[0-9]+", fields[0]) or fields[0] in seen_devices:
                valid = False
                break
            seen_devices.add(fields[0])
            found = set()
            for item in fields[1:]:
                pair = item.split("=", 1)
                if len(pair) != 2 or pair[0] in found or _bounded_integer(pair[1]) is None:
                    valid = False
                    break
                found.add(pair[0])
                if pair[0] in totals:
                    totals[pair[0]] += int(pair[1])
                    if totals[pair[0]] > _MAX_METRIC:
                        valid = False
                        break
            if not {"rbytes", "wbytes"}.issubset(found):
                valid = False
            if not valid:
                break
        if valid and seen_devices:
            read_bytes, write_bytes = totals["rbytes"], totals["wbytes"]
    else:
        raw = read_text("/sys/fs/cgroup/blkio/blkio.io_service_bytes_recursive")
        if raw is not None:
            totals = {"Read": 0, "Write": 0}
            seen: set[tuple[str, str]] = set()
            valid = True
            devices: set[str] = set()
            for line in raw.splitlines():
                fields = line.split()
                if not fields or not re.fullmatch(r"[0-9]+:[0-9]+", fields[0]):
                    valid = False
                    break
                device = fields[0]
                devices.add(device)
                if len(fields) == 3 and fields[1] == "Total":
                    if _bounded_integer(fields[2]) is None or (device, "Total") in seen:
                        valid = False
                        break
                    seen.add((device, "Total"))
                    continue
                if len(fields) == 4 and fields[1] in {"Sync", "Async"} and fields[2] in totals:
                    key = (device, fields[1] + " " + fields[2])
                    if _bounded_integer(fields[3]) is None or key in seen:
                        valid = False
                        break
                    seen.add(key)
                    continue
                if (len(fields) != 3 or fields[1] not in totals or _bounded_integer(fields[2]) is None
                        or (device, fields[1]) in seen):
                    valid = False
                    break
                seen.add((device, fields[1]))
                totals[fields[1]] += int(fields[2])
                if totals[fields[1]] > _MAX_METRIC:
                    valid = False
                    break
            if valid and devices and all((device, kind) in seen for device in devices for kind in totals):
                read_bytes, write_bytes = totals["Read"], totals["Write"]

    return validate_worker_metrics({
        "schema_version": 1, "kind": "gah_worker_metrics",
        "capture_scope": "container_cgroup_until_worker_exit", "capture_status": "CAPTURED", "cpu_ns": cpu_ns,
        "rss_peak_bytes": None, "memory_peak_bytes": memory_peak,
        "io_read_bytes": read_bytes, "io_write_bytes": write_bytes,
        "valid_for_slo": False,
    })


def empty_worker_metrics(status: str) -> dict[str, Any]:
    if status not in {"MISSING", "INVALID"}:
        raise ValueError("INVALID_WORKER_METRICS")
    return validate_worker_metrics({
        "schema_version": 1, "kind": "gah_worker_metrics",
        "capture_scope": "container_cgroup_until_worker_exit", "capture_status": status,
        "cpu_ns": None, "rss_peak_bytes": None, "memory_peak_bytes": None,
        "io_read_bytes": None, "io_write_bytes": None, "valid_for_slo": False,
    })


_FOOTER = b"GAH-WORKER-METRICS-V1 "


def split_metrics_footer(stderr: bytes) -> tuple[bytes, dict[str, Any]]:
    """固定prefix付き末尾だけを取り出し、通常stderrを維持する。"""
    if type(stderr) is not bytes:
        return b"", empty_worker_metrics("INVALID")
    position = stderr.rfind(_FOOTER)
    if position < 0 or position > 0 and stderr[position - 1:position] != b"\n":
        return stderr, empty_worker_metrics("MISSING")
    end = stderr.find(b"\n", position)
    if end < 0:
        end = len(stderr)
        trailing = b""
    else:
        trailing = stderr[end + 1:]
    delimiter_start = position - 1
    if delimiter_start > 0 and stderr[delimiter_start - 1:delimiter_start] == b"\r":
        delimiter_start -= 1
    remaining = stderr[:delimiter_start] + trailing
    raw = stderr[position + len(_FOOTER):end]
    if raw.endswith(b"\r"):
        raw = raw[:-1]
    try:
        metrics = decode_worker_metrics(raw)
        if metrics["capture_status"] != "CAPTURED":
            raise ValueError()
        return remaining, metrics
    except ValueError:
        return remaining, empty_worker_metrics("INVALID")


class WorkerMetricsJournal:
    """Immutable bounded sidecar keyed by the fixed run and operation binding."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def close(self) -> None:
        return None

    def _record_path(self, run_id: str, operation_id: str) -> Path:
        key = hashlib.sha256(canonical_bytes([run_id, operation_id])).hexdigest()
        return self.path / (key + ".json")

    @staticmethod
    def _artifact(execution: Any, source: Any, metrics: Any) -> dict[str, Any]:
        if type(execution) is not dict or set(execution) != {"run_id", "operation_id", "request_digest", "binding", "scenario", "image_id"}:
            raise ValueError("INVALID_WORKER_METRICS")
        binding = validate_binding(execution["binding"])
        if (type(execution["run_id"]) is not str or not _SID.fullmatch(execution["run_id"])
                or type(execution["operation_id"]) is not str or not _SID.fullmatch(execution["operation_id"])
                or binding["run_id"] != execution["run_id"] or binding["operation_id"] != execution["operation_id"]
                or type(execution["request_digest"]) is not str or not _DIGEST.fullmatch(execution["request_digest"])
                or type(execution["scenario"]) is not str or len(execution["scenario"]) > 256
                or type(execution["image_id"]) is not str or not re.fullmatch(r"sha256:[0-9a-f]{64}", execution["image_id"])):
            raise ValueError("INVALID_WORKER_METRICS")
        expected_source = {key: binding[key] for key in ("fixture_digest", "evaluator_digest", "adapter_digest", "policy_digest", "target_digest")}
        if type(source) is not dict or set(source) != set(expected_source) | {"worker_digest"}:
            raise ValueError("INVALID_WORKER_METRICS")
        if any(type(value) is not str or not _DIGEST.fullmatch(value) for value in source.values()):
            raise ValueError("INVALID_WORKER_METRICS")
        if any(source[key] != value for key, value in expected_source.items()) or source["worker_digest"] != binding["fixture_digest"]:
            raise ValueError("INVALID_WORKER_METRICS")
        checked = validate_worker_metrics(metrics)
        return {"schema_version": 1, "kind": "gah_worker_metrics_artifact",
                "execution": {**execution, "binding": binding}, "source": dict(source), "metrics": checked}

    def save(self, execution: Any, source: Any, metrics: Any) -> dict[str, Any]:
        artifact = self._artifact(execution, source, metrics)
        digest = hashlib.sha256(canonical_bytes(artifact)).hexdigest()
        record = {**artifact, "artifact_digest": digest}
        payload = canonical_bytes(record)
        path = self._record_path(execution["run_id"], execution["operation_id"])
        try:
            result = write_bounded(path, payload, immutable=True)
        except BoundedFileError:
            raise ValueError("WORKER_METRICS_SAVE_FAILED") from None
        return {**record, "path": result["path"], "replayed": result["replayed"]}

    def metrics_for(self, run_id: str, operation_id: str, request_digest: str | None = None) -> dict[str, Any] | None:
        if (type(run_id) is not str or not _SID.fullmatch(run_id)
                or type(operation_id) is not str or not _SID.fullmatch(operation_id)
                or request_digest is not None and (type(request_digest) is not str or not _DIGEST.fullmatch(request_digest))):
            raise ValueError("INVALID_WORKER_METRICS_QUERY")
        path = self._record_path(run_id, operation_id)
        try:
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or getattr(info, "st_nlink", 1) != 1:
                raise ValueError()
            with path.open("rb") as stream:
                raw = stream.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise ValueError()
            record = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs)
            if raw != canonical_bytes(record):
                raise ValueError()
            execution = record["execution"]
            artifact = {key: record[key] for key in ("schema_version", "kind", "execution", "source", "metrics")}
            digest = hashlib.sha256(canonical_bytes(artifact)).hexdigest()
            if (record["schema_version"] != 1 or record["kind"] != "gah_worker_metrics_artifact"
                    or execution["run_id"] != run_id or execution["operation_id"] != operation_id
                    or request_digest is not None and execution["request_digest"] != request_digest
                    or record["artifact_digest"] != digest):
                raise ValueError()
            self._artifact(execution, record["source"], record["metrics"])
            return record
        except FileNotFoundError:
            return None
        except (OSError, ValueError, UnicodeError, TypeError, KeyError, RecursionError):
            raise ValueError("WORKER_METRICS_CORRUPT_OR_MISMATCH") from None

    def get(self, run_id: str, operation_id: str) -> dict[str, Any] | None:
        return self.metrics_for(run_id, operation_id)


__all__ = ["WorkerMetricsJournal", "collect_cgroup_metrics", "decode_worker_metrics", "empty_worker_metrics", "split_metrics_footer", "validate_worker_metrics"]
