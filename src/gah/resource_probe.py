
"""固定 ownership の resource snapshot を厳密に集計する primitive。

このmoduleはDocker、OS、shell、URLへ直接接続しない。trusted runtimeがDIで
渡す snapshot provider だけを読み、欠測値を0へ補完せず、測定範囲と欠測理由を
保存可能な辞書へ変換する。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Protocol

from .contracts import (
    ContractError,
    MAX_INTEGER,
    decode_document,
    require_digest,
    require_ref,
    require_text,
)
from .wire import canonical_bytes


SCHEMA_VERSION = 1
MAX_SCOPES = 256
SCOPE_KINDS = frozenset({"container", "process", "host_harness"})
COUNTER_FIELDS = ("cpu_ns", "io_read_bytes", "io_write_bytes")
RESOURCE_FIELDS = (
    "cpu_ns",
    "io_read_bytes",
    "io_write_bytes",
    "rss_bytes",
    "memory_current_bytes",
    "memory_peak_bytes",
)
RESULT_METRICS = (
    "wall_ns",
    "cpu_ns",
    "host_harness_cpu_ns",
    "total_cpu_ns",
    "io_read_bytes",
    "io_write_bytes",
    "rss_group_peak_bytes",
    "cgroup_memory_current_bytes",
    "host_harness_io_read_bytes",
    "host_harness_io_write_bytes",
    "cgroup_memory_peak_bytes",
)
ESSENTIAL_METRICS = frozenset({
    "wall_ns",
    "cpu_ns",
    "host_harness_cpu_ns",
    "io_read_bytes",
    "io_write_bytes",
    "rss_group_peak_bytes",
})
SNAPSHOT_FIELDS = frozenset({"schema_version", "captured_ns", "sample_seq", "scopes"})
SCOPE_FIELDS = frozenset({
    "scope_id",
    "scope_kind",
    "identity",
    "epoch",
    "restart_count",
    "cpu_ns",
    "io_read_bytes",
    "io_write_bytes",
    "rss_bytes",
    "memory_current_bytes",
    "memory_peak_bytes",
    "parent_scope_id",
})
PROBE_REASONS = frozenset({
    "INVALID_SNAPSHOT",
    "INVALID_SCOPE",
    "INVALID_BINDING",
    "PROVIDER_INVALID",
    "PROVIDER_FAILURE",
    "INTERVAL_NOT_STARTED",
    "ALREADY_STARTED",
    "ALREADY_ENDED",
    "TIME_RESET",
    "SAMPLE_GAP",
    "IDENTITY_CHANGED",
    "EPOCH_CHANGED",
    "RESTART_DETECTED",
    "SCOPE_CHANGED",
    "SCOPE_INSUFFICIENT",
    "COUNTER_RESET",
    "REQUIRED_METRIC_MISSING",
})
_GLOBAL_INVALID_REASONS = frozenset({
    "PROVIDER_FAILURE",
    "INVALID_SNAPSHOT",
    "TIME_RESET",
    "SAMPLE_GAP",
    "IDENTITY_CHANGED",
    "EPOCH_CHANGED",
    "RESTART_DETECTED",
    "SCOPE_CHANGED",
    "COUNTER_RESET",
})


class ProbeError(ContractError):
    """resource probe入力または測定状態の固定エラー。"""


class ObservationProvider(Protocol):
    """固定runtimeがDIする、引数なしsnapshot provider。"""

    def snapshot(self) -> Mapping[str, Any]:
        """Docker/OSの取得はprovider側で行い、任意commandは受けない。"""
        ...


def _checked_dict(value: Any, code: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise ProbeError(code)
    try:
        checked = decode_document(canonical_bytes(value))
    except (ContractError, TypeError, ValueError, UnicodeError, RecursionError):
        raise ProbeError(code) from None
    if type(checked) is not dict:
        raise ProbeError(code)
    return checked


def _uint(value: Any, code: str = "INVALID_SNAPSHOT") -> int:
    if type(value) is not int or not 0 <= value <= MAX_INTEGER:
        raise ProbeError(code)
    return value


def _nullable_uint(value: Any, code: str = "INVALID_SNAPSHOT") -> int | None:
    if value is None:
        return None
    return _uint(value, code)


def _text(value: Any, code: str) -> str:
    try:
        require_text(value)
    except ContractError:
        raise ProbeError(code) from None
    return value


def _ref(value: Any) -> dict[str, Any]:
    if type(value) is not dict:
        raise ProbeError("INVALID_BINDING")
    try:
        require_ref(value)
        return _checked_dict(value, "INVALID_BINDING")
    except (ContractError, TypeError, ValueError):
        raise ProbeError("INVALID_BINDING") from None


@dataclass(frozen=True, slots=True)
class ScopeSample:
    """一つの非負 counter/sample と、その ownership identity。"""

    scope_id: str
    scope_kind: str
    identity: str
    epoch: int
    restart_count: int
    cpu_ns: int | None
    io_read_bytes: int | None
    io_write_bytes: int | None
    rss_bytes: int | None
    memory_current_bytes: int | None
    memory_peak_bytes: int | None
    parent_scope_id: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope_id": self.scope_id,
            "scope_kind": self.scope_kind,
            "identity": self.identity,
            "epoch": self.epoch,
            "restart_count": self.restart_count,
            "cpu_ns": self.cpu_ns,
            "io_read_bytes": self.io_read_bytes,
            "io_write_bytes": self.io_write_bytes,
            "rss_bytes": self.rss_bytes,
            "memory_current_bytes": self.memory_current_bytes,
            "memory_peak_bytes": self.memory_peak_bytes,
            "parent_scope_id": self.parent_scope_id,
        }


@dataclass(frozen=True, slots=True)
class ResourceSnapshot:
    """固定 schema を通過した provider snapshot。"""

    schema_version: int
    captured_ns: int
    sample_seq: int
    scopes: tuple[ScopeSample, ...]

    @property
    def scope_map(self) -> dict[str, ScopeSample]:
        return {scope.scope_id: scope for scope in self.scopes}

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "captured_ns": self.captured_ns,
            "sample_seq": self.sample_seq,
            "scopes": [scope.to_dict() for scope in self.scopes],
        }


def _parse_scope(value: Any) -> ScopeSample:
    value = _checked_dict(value, "INVALID_SCOPE")
    if set(value) != set(SCOPE_FIELDS):
        raise ProbeError("INVALID_SCOPE")
    scope_kind = value["scope_kind"]
    if type(scope_kind) is not str or scope_kind not in SCOPE_KINDS:
        raise ProbeError("INVALID_SCOPE")
    scope_id = _text(value["scope_id"], "INVALID_SCOPE")
    identity = _text(value["identity"], "INVALID_SCOPE")
    parent = value["parent_scope_id"]
    if parent is not None:
        parent = _text(parent, "INVALID_SCOPE")
    return ScopeSample(
        scope_id=scope_id,
        scope_kind=scope_kind,
        identity=identity,
        epoch=_uint(value["epoch"], "INVALID_SCOPE"),
        restart_count=_uint(value["restart_count"], "INVALID_SCOPE"),
        cpu_ns=_nullable_uint(value["cpu_ns"], "INVALID_SCOPE"),
        io_read_bytes=_nullable_uint(value["io_read_bytes"], "INVALID_SCOPE"),
        io_write_bytes=_nullable_uint(value["io_write_bytes"], "INVALID_SCOPE"),
        rss_bytes=_nullable_uint(value["rss_bytes"], "INVALID_SCOPE"),
        memory_current_bytes=_nullable_uint(value["memory_current_bytes"], "INVALID_SCOPE"),
        memory_peak_bytes=_nullable_uint(value["memory_peak_bytes"], "INVALID_SCOPE"),
        parent_scope_id=parent,
    )


def parse_snapshot(value: dict[str, Any]) -> ResourceSnapshot:
    """Docker/cgroup providerのsnapshotを閉じた型へ変換する。

    snapshotはschema_version/captured_ns/sample_seq/scopesだけを受け付け、
    counter値は整数またはnullに限定する。未知field、親不在、親循環、
    float、bool、negative値は拒否する。
    """
    value = _checked_dict(value, "INVALID_SNAPSHOT")
    if set(value) != set(SNAPSHOT_FIELDS):
        raise ProbeError("INVALID_SNAPSHOT")
    if _uint(value["schema_version"]) != SCHEMA_VERSION:
        raise ProbeError("INVALID_SNAPSHOT")
    captured_ns = _uint(value["captured_ns"])
    sample_seq = _uint(value["sample_seq"])
    raw_scopes = value["scopes"]
    if type(raw_scopes) is not list or not 1 <= len(raw_scopes) <= MAX_SCOPES:
        raise ProbeError("INVALID_SCOPE")
    scopes = tuple(_parse_scope(item) for item in raw_scopes)
    ids = [scope.scope_id for scope in scopes]
    if len(set(ids)) != len(ids):
        raise ProbeError("INVALID_SCOPE")
    by_id = {scope.scope_id: scope for scope in scopes}
    for scope in scopes:
        parent = scope.parent_scope_id
        if parent is None:
            continue
        if parent not in by_id or parent == scope.scope_id:
            raise ProbeError("INVALID_SCOPE")
        seen = {scope.scope_id}
        current = parent
        while current is not None:
            if current in seen:
                raise ProbeError("INVALID_SCOPE")
            seen.add(current)
            current = by_id[current].parent_scope_id
    return ResourceSnapshot(
        schema_version=SCHEMA_VERSION,
        captured_ns=captured_ns,
        sample_seq=sample_seq,
        scopes=scopes,
    )


@dataclass(frozen=True, slots=True)
class MeasurementBinding:
    """plan/source/requestを一つのprobe intervalへ固定するbinding。"""

    plan_ref: dict[str, Any]
    source_ref: dict[str, Any]
    request_digest: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_ref": dict(self.plan_ref),
            "source_ref": dict(self.source_ref),
            "request_digest": self.request_digest,
        }


def _make_binding(
    plan_ref: dict[str, Any],
    source_ref: dict[str, Any],
    request_digest: str,
) -> MeasurementBinding:
    checked_plan = _ref(plan_ref)
    checked_source = _ref(source_ref)
    if type(request_digest) is not str:
        raise ProbeError("INVALID_BINDING")
    try:
        require_digest(request_digest)
    except ContractError:
        raise ProbeError("INVALID_BINDING") from None
    return MeasurementBinding(checked_plan, checked_source, request_digest)


class ProbeSession:
    """固定providerのsnapshot列から一つのwhole_run intervalを作る。"""

    def __init__(
        self,
        provider: ObservationProvider,
        *,
        plan_ref: dict[str, Any],
        source_ref: dict[str, Any],
        request_digest: str,
        expected_scope_ids: Iterable[str] | None = None,
        required_metrics: Iterable[str] | None = None,
    ) -> None:
        snapshot_method = getattr(provider, "snapshot", None)
        if not callable(snapshot_method):
            raise ProbeError("PROVIDER_INVALID")
        self._provider = provider
        self._binding = _make_binding(plan_ref, source_ref, request_digest)
        if expected_scope_ids is None:
            self._expected_scope_ids: tuple[str, ...] | None = None
        else:
            if isinstance(expected_scope_ids, (str, bytes)):
                raise ProbeError("INVALID_SCOPE")
            ids = tuple(expected_scope_ids)
            if not ids or len(ids) > MAX_SCOPES:
                raise ProbeError("INVALID_SCOPE")
            if any(type(item) is not str for item in ids):
                raise ProbeError("INVALID_SCOPE")
            for item in ids:
                _text(item, "INVALID_SCOPE")
            if len(set(ids)) != len(ids):
                raise ProbeError("INVALID_SCOPE")
            self._expected_scope_ids = ids
        if required_metrics is None:
            requested = set(ESSENTIAL_METRICS)
        else:
            if isinstance(required_metrics, (str, bytes)):
                raise ProbeError("INVALID_INPUT")
            requested = set(required_metrics)
            if any(type(item) is not str for item in requested):
                raise ProbeError("INVALID_INPUT")
        if not requested.issubset(set(RESULT_METRICS)):
            raise ProbeError("INVALID_INPUT")
        self._required_metrics = frozenset(requested | ESSENTIAL_METRICS)
        self._started = False
        self._ended = False
        self._start: ResourceSnapshot | None = None
        self._end: ResourceSnapshot | None = None
        self._snapshots: list[ResourceSnapshot] = []
        self._reasons: list[str] = []
        self._scope_shape_invalid = False
        self._cached_result: dict[str, Any] | None = None

    @property
    def binding(self) -> MeasurementBinding:
        return self._binding

    @property
    def reasons(self) -> tuple[str, ...]:
        return tuple(self._reasons)

    def _add_reason(self, reason: str) -> None:
        if reason not in PROBE_REASONS:
            raise ProbeError("INVALID_INPUT")
        if reason not in self._reasons:
            self._reasons.append(reason)

    def _read(self) -> ResourceSnapshot | None:
        try:
            raw = self._provider.snapshot()
        except Exception:
            self._add_reason("PROVIDER_FAILURE")
            return None
        try:
            return parse_snapshot(raw)  # type: ignore[arg-type]
        except ProbeError as exc:
            self._add_reason(
                exc.code if exc.code in PROBE_REASONS else "INVALID_SNAPSHOT",
            )
            return None

    def _record(self, snapshot: ResourceSnapshot) -> None:
        previous = self._snapshots[-1] if self._snapshots else None
        if self._expected_scope_ids is None:
            self._expected_scope_ids = tuple(scope.scope_id for scope in snapshot.scopes)
        expected = set(self._expected_scope_ids)
        actual = set(snapshot.scope_map)
        if actual != expected:
            self._scope_shape_invalid = True
            self._add_reason("SCOPE_INSUFFICIENT")
        if previous is None:
            self._snapshots.append(snapshot)
            return
        if snapshot.sample_seq <= previous.sample_seq:
            self._add_reason("SAMPLE_GAP")
        elif snapshot.sample_seq != previous.sample_seq + 1:
            self._add_reason("SAMPLE_GAP")
        if snapshot.captured_ns < previous.captured_ns:
            self._add_reason("TIME_RESET")
        previous_map = previous.scope_map
        for scope_id in sorted(expected & actual):
            old = previous_map.get(scope_id)
            new = snapshot.scope_map.get(scope_id)
            if old is None or new is None:
                self._scope_shape_invalid = True
                self._add_reason("SCOPE_INSUFFICIENT")
                continue
            if old.identity != new.identity:
                self._add_reason("IDENTITY_CHANGED")
            if old.epoch != new.epoch:
                self._add_reason("EPOCH_CHANGED")
            if old.restart_count != new.restart_count:
                self._add_reason("RESTART_DETECTED")
            if (
                old.scope_kind != new.scope_kind
                or old.parent_scope_id != new.parent_scope_id
            ):
                self._add_reason("SCOPE_CHANGED")
            for field in COUNTER_FIELDS:
                old_value = getattr(old, field)
                new_value = getattr(new, field)
                if (
                    old_value is not None
                    and new_value is not None
                    and new_value < old_value
                ):
                    self._add_reason("COUNTER_RESET")
        self._snapshots.append(snapshot)

    def begin(self) -> ResourceSnapshot | None:
        if self._started:
            raise ProbeError("ALREADY_STARTED")
        self._started = True
        snapshot = self._read()
        if snapshot is not None:
            self._start = snapshot
            self._record(snapshot)
        return snapshot

    def sample(self) -> ResourceSnapshot | None:
        if not self._started:
            raise ProbeError("INTERVAL_NOT_STARTED")
        if self._ended:
            raise ProbeError("ALREADY_ENDED")
        snapshot = self._read()
        if snapshot is None:
            self._add_reason("SAMPLE_GAP")
            return None
        self._record(snapshot)
        return snapshot

    def end(self) -> dict[str, Any]:
        if self._cached_result is not None:
            return self._cached_result
        if self._ended:
            raise ProbeError("ALREADY_ENDED")
        self._ended = True
        if not self._started:
            self._add_reason("INTERVAL_NOT_STARTED")
            self._cached_result = self._build_result()
            return self._cached_result
        snapshot = self._read()
        if snapshot is None:
            self._add_reason("SAMPLE_GAP")
        else:
            self._end = snapshot
            self._record(snapshot)
        self._cached_result = self._build_result()
        return self._cached_result

    def _invalid_for_measurement(self) -> bool:
        return self._scope_shape_invalid or any(
            reason in _GLOBAL_INVALID_REASONS
            for reason in self._reasons
        )

    def _effective_ids(
        self,
        field: str,
        *,
        all_samples: bool,
    ) -> tuple[list[str], list[str]]:
        if self._start is None or self._end is None:
            return [], []
        base = self._start.scope_map
        snapshots = self._snapshots if all_samples else [self._start, self._end]
        available: list[str] = []
        for scope in self._start.scopes:
            values_present = True
            for snapshot in snapshots:
                item = snapshot.scope_map.get(scope.scope_id)
                if item is None or getattr(item, field) is None:
                    values_present = False
                    break
            if values_present:
                available.append(scope.scope_id)
        available_set = set(available)
        selected: list[str] = []
        excluded: list[str] = []
        for scope_id in available:
            parent = base[scope_id].parent_scope_id
            has_available_ancestor = False
            while parent is not None:
                if parent in available_set:
                    has_available_ancestor = True
                    break
                parent = base[parent].parent_scope_id
            if has_available_ancestor:
                excluded.append(scope_id)
            else:
                selected.append(scope_id)
        return selected, excluded

    def _delta(
        self,
        field: str,
        scope_ids: list[str],
        *,
        allowed_kinds: set[str] | None = None,
    ) -> int | None:
        if self._start is None or self._end is None or self._invalid_for_measurement():
            return None
        start_map = self._start.scope_map
        end_map = self._end.scope_map
        selected = [
            scope_id for scope_id in scope_ids
            if allowed_kinds is None
            or start_map[scope_id].scope_kind in allowed_kinds
        ]
        if not selected:
            self._add_reason("SCOPE_INSUFFICIENT")
            return None
        total = 0
        for scope_id in selected:
            start_value = getattr(start_map[scope_id], field)
            end_value = getattr(end_map[scope_id], field)
            if start_value is None or end_value is None:
                self._add_reason("SCOPE_INSUFFICIENT")
                return None
            if end_value < start_value:
                self._add_reason("COUNTER_RESET")
                return None
            total += end_value - start_value
        return total

    def _rss_peak(self, scope_ids: list[str]) -> int | None:
        if self._start is None or self._end is None or self._invalid_for_measurement():
            return None
        if not scope_ids:
            self._add_reason("SCOPE_INSUFFICIENT")
            return None
        peaks: list[int] = []
        for snapshot in self._snapshots:
            current = 0
            for scope_id in scope_ids:
                item = snapshot.scope_map.get(scope_id)
                if item is None or item.rss_bytes is None:
                    self._add_reason("SCOPE_INSUFFICIENT")
                    return None
                current += item.rss_bytes
            peaks.append(current)
        return max(peaks) if peaks else None

    def _cgroup_current(
        self,
        scope_ids: list[str],
    ) -> tuple[int | None, dict[str, int]]:
        if self._start is None or self._end is None or not scope_ids:
            return None, {}
        values: dict[str, int] = {}
        for scope_id in scope_ids:
            item = self._end.scope_map.get(scope_id)
            if item is not None and item.memory_current_bytes is not None:
                values[scope_id] = item.memory_current_bytes
        if len(values) == 1:
            return next(iter(values.values())), values
        return None, values

    def _cgroup_peak(
        self,
        scope_ids: list[str],
    ) -> tuple[int | None, dict[str, int]]:
        if self._start is None or self._end is None or not scope_ids:
            return None, {}
        values: dict[str, int] = {}
        for scope_id in scope_ids:
            observed = [
                item.memory_peak_bytes
                for snapshot in self._snapshots
                for item in [snapshot.scope_map.get(scope_id)]
                if item is not None and item.memory_peak_bytes is not None
            ]
            if observed:
                values[scope_id] = max(observed)
        if len(values) == 1:
            return next(iter(values.values())), values
        return None, values

    @staticmethod
    def _identity_records(
        snapshot: ResourceSnapshot | None,
    ) -> list[dict[str, Any]]:
        if snapshot is None:
            return []
        return [
            {
                "scope_id": scope.scope_id,
                "scope_kind": scope.scope_kind,
                "identity": scope.identity,
                "epoch": scope.epoch,
                "restart_count": scope.restart_count,
                "parent_scope_id": scope.parent_scope_id,
            }
            for scope in snapshot.scopes
        ]

    def _build_result(self) -> dict[str, Any]:
        start = self._start
        end = self._end
        all_scope_ids: list[str] = []
        for snapshot in self._snapshots:
            for scope in snapshot.scopes:
                if scope.scope_id not in all_scope_ids:
                    all_scope_ids.append(scope.scope_id)
        cpu_ids, cpu_excluded = self._effective_ids("cpu_ns", all_samples=False)
        read_ids, read_excluded = self._effective_ids("io_read_bytes", all_samples=False)
        write_ids, write_excluded = self._effective_ids("io_write_bytes", all_samples=False)
        rss_ids, rss_excluded = self._effective_ids("rss_bytes", all_samples=True)
        current_ids, current_excluded = self._effective_ids(
            "memory_current_bytes", all_samples=True,
        )
        memory_ids, memory_excluded = self._effective_ids(
            "memory_peak_bytes", all_samples=True,
        )
        target_kinds = {"container", "process"}
        target_cpu = self._delta("cpu_ns", cpu_ids, allowed_kinds=target_kinds)
        host_cpu = self._delta("cpu_ns", cpu_ids, allowed_kinds={"host_harness"})
        target_read = self._delta(
            "io_read_bytes", read_ids, allowed_kinds=target_kinds,
        )
        target_write = self._delta(
            "io_write_bytes", write_ids, allowed_kinds=target_kinds,
        )
        host_read = self._delta(
            "io_read_bytes", read_ids, allowed_kinds={"host_harness"},
        )
        host_write = self._delta(
            "io_write_bytes", write_ids, allowed_kinds={"host_harness"},
        )
        rss_target_ids = [
            scope_id for scope_id in rss_ids
            if start is not None
            and start.scope_map[scope_id].scope_kind in target_kinds
        ]
        rss_peak = self._rss_peak(rss_target_ids)
        current_target_ids = [
            scope_id for scope_id in current_ids
            if start is not None
            and start.scope_map[scope_id].scope_kind == "container"
        ]
        memory_target_ids = [
            scope_id for scope_id in memory_ids
            if start is not None
            and start.scope_map[scope_id].scope_kind == "container"
        ]
        cgroup_current, cgroup_current_by_scope = self._cgroup_current(
            current_target_ids,
        )
        cgroup_peak, cgroup_by_scope = self._cgroup_peak(memory_target_ids)
        start_ns = start.captured_ns if start is not None else None
        end_ns = end.captured_ns if end is not None else None
        wall_ns: int | None = None
        if start_ns is not None and end_ns is not None:
            if end_ns >= start_ns and "TIME_RESET" not in self._reasons:
                wall_ns = end_ns - start_ns
            elif end_ns < start_ns:
                self._add_reason("TIME_RESET")
        total_cpu = (
            target_cpu + host_cpu
            if target_cpu is not None and host_cpu is not None
            else None
        )
        metrics: dict[str, Any] = {
            "wall_ns": wall_ns,
            "cpu_ns": target_cpu,
            "host_harness_cpu_ns": host_cpu,
            "total_cpu_ns": total_cpu,
            "io_read_bytes": target_read,
            "io_write_bytes": target_write,
            "rss_group_peak_bytes": rss_peak,
            "rss_group_peak_precision": "sampled" if rss_peak is not None else None,
            "rss_group_true_peak_bytes": None,
            "cgroup_memory_current_bytes": cgroup_current,
            "host_harness_io_read_bytes": host_read,
            "host_harness_io_write_bytes": host_write,
            "cgroup_memory_peak_bytes": cgroup_peak,
            "cgroup_memory_peak_by_scope": cgroup_by_scope,
            "cgroup_memory_current_by_scope": cgroup_current_by_scope,
        }
        selected_scope_ids = {
            "cpu_ns": [
                scope_id for scope_id in cpu_ids
                if start is not None
                and start.scope_map[scope_id].scope_kind in target_kinds
            ],
            "host_harness_cpu_ns": [
                scope_id for scope_id in cpu_ids
                if start is not None
                and start.scope_map[scope_id].scope_kind == "host_harness"
            ],
            "io_read_bytes": [
                scope_id for scope_id in read_ids
                if start is not None
                and start.scope_map[scope_id].scope_kind in target_kinds
            ],
            "io_write_bytes": [
                scope_id for scope_id in write_ids
                if start is not None
                and start.scope_map[scope_id].scope_kind in target_kinds
            ],
            "rss_group_peak_bytes": rss_target_ids,
            "cgroup_memory_current_bytes": current_target_ids,
            "cgroup_memory_peak_bytes": memory_target_ids,
        }
        excluded_scope_ids = {
            "cpu_ns": cpu_excluded,
            "io_read_bytes": read_excluded,
            "io_write_bytes": write_excluded,
            "rss_group_peak_bytes": rss_excluded,
            "cgroup_memory_current_bytes": current_excluded,
            "cgroup_memory_peak_bytes": memory_excluded,
        }
        interval = {
            "start_ns": start_ns,
            "end_ns": end_ns,
            "start_sample_seq": start.sample_seq if start else None,
            "end_sample_seq": end.sample_seq if end else None,
            "sample_count": len(self._snapshots),
        }
        scope_complete = (
            start is not None
            and end is not None
            and not self._scope_shape_invalid
            and not any(reason in _GLOBAL_INVALID_REASONS for reason in self._reasons)
        )
        missing = [
            metric for metric in sorted(self._required_metrics)
            if metrics.get(metric) is None
        ]
        if missing:
            self._add_reason("REQUIRED_METRIC_MISSING")
        measurement_complete = (
            scope_complete
            and not missing
            and not any(reason in _GLOBAL_INVALID_REASONS for reason in self._reasons)
        )
        # このprimitiveは取得完全性を返すだけで、全child scope、真のRSS peak、
        # copy/serialization boundary、accepted planの独立照合を証明しない。
        valid_for_slo = False
        status = "COMPLETED" if measurement_complete else "INCOMPLETE"
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": "resource_probe_result",
            "binding": self._binding.to_dict(),
            "interval": interval,
            "identity": {
                "start": self._identity_records(start),
                "end": self._identity_records(end),
            },
            "metrics": metrics,
            "scope": {
                "expected_scope_ids": list(self._expected_scope_ids or ()),
                "observed_scope_ids": all_scope_ids,
                "selected_scope_ids": selected_scope_ids,
                "excluded_scope_ids": excluded_scope_ids,
                "non_overlapping": not self._scope_shape_invalid,
                "scope_complete": scope_complete,
            },
            "operation_status": status,
            "measurement_complete": measurement_complete,
            "valid_for_slo": valid_for_slo,
            "slo_blockers": ["PRODUCT_SLO_NOT_CONNECTED"],
            "reasons": list(self._reasons),
        }


def measure_interval(
    provider: ObservationProvider,
    *,
    plan_ref: dict[str, Any],
    source_ref: dict[str, Any],
    request_digest: str,
    expected_scope_ids: Iterable[str] | None = None,
    required_metrics: Iterable[str] | None = None,
    sample_count: int = 0,
) -> dict[str, Any]:
    """providerを一度のintervalとして測る便利な固定入口。"""
    if type(sample_count) is not int or sample_count < 0 or sample_count > MAX_SCOPES:
        raise ProbeError("INVALID_INPUT")
    session = ProbeSession(
        provider,
        plan_ref=plan_ref,
        source_ref=source_ref,
        request_digest=request_digest,
        expected_scope_ids=expected_scope_ids,
        required_metrics=required_metrics,
    )
    session.begin()
    for _ in range(sample_count):
        session.sample()
    return session.end()


__all__ = [
    "SCHEMA_VERSION",
    "SCOPE_KINDS",
    "COUNTER_FIELDS",
    "RESOURCE_FIELDS",
    "RESULT_METRICS",
    "ESSENTIAL_METRICS",
    "SNAPSHOT_FIELDS",
    "SCOPE_FIELDS",
    "PROBE_REASONS",
    "ProbeError",
    "ObservationProvider",
    "ScopeSample",
    "ResourceSnapshot",
    "MeasurementBinding",
    "parse_snapshot",
    "ProbeSession",
    "measure_interval",
]
