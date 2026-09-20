"""新規保存根と新規SQLite DBの容量境界を強制する小さなprimitive。

このモジュールが扱うのは、呼び出し側が明示した保存根の論理file bytesだけである。
Docker volume/daemon、OS全体の空き、別filesystemの独立性は観測しないため、
このモジュール単体から ``bound_verified`` を生成してはならない。
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import sqlite3
import stat
import struct
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any


MAX_INTEGER = 2**63 - 1
DEFAULT_ATOMIC_TEMP_OVERHEAD_BYTES = 4096
MAX_FAILURE_SINK_BYTES = 64 * 1024 * 1024
_FAILURE_MAGIC = b"GAHFAIL1"
_FAILURE_HEADER_BYTES = len(_FAILURE_MAGIC) + 8
_ZERO_BLOCK = b"\0" * (1024 * 1024)


class StorageBudgetError(ValueError):
    """容量primitiveの固定拒否理由。本文にpathや保存データを含めない。"""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _strict_integer(value: Any, *, name: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum or value > MAX_INTEGER:
        raise StorageBudgetError("INVALID_INTEGER_" + name.upper())
    return value


def _is_link_or_junction(path: Path) -> bool:
    try:
        if not os.path.lexists(path):
            return False
        if path.is_symlink():
            return True
        checker = getattr(path, "is_junction", None)
        if checker is not None and checker():
            return True
        if os.name == "nt":
            info = path.stat(follow_symlinks=False)
            flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            return bool(getattr(info, "st_file_attributes", 0) & flag)
    except (OSError, ValueError):
        return True
    return False


def _plain_directory(value: str | Path) -> Path:
    path = Path(value)
    try:
        if _is_link_or_junction(path) or not path.is_dir():
            raise StorageBudgetError("PATH_REJECTED")
        resolved = path.resolve(strict=True)
        if _is_link_or_junction(resolved) or not resolved.is_dir():
            raise StorageBudgetError("PATH_REJECTED")
        return resolved
    except StorageBudgetError:
        raise
    except (OSError, RuntimeError, ValueError):
        raise StorageBudgetError("PATH_REJECTED") from None


def _within(root: Path, value: Path) -> bool:
    try:
        value.relative_to(root)
    except ValueError:
        return False
    return True


def _safe_relative(value: str | Path) -> Path:
    try:
        raw = os.fspath(value)
    except TypeError:
        raise StorageBudgetError("PATH_REJECTED") from None
    if isinstance(raw, bytes):
        try:
            raw = os.fsdecode(raw)
        except UnicodeError:
            raise StorageBudgetError("PATH_REJECTED") from None
    if type(raw) is not str or not raw or "\0" in raw:
        raise StorageBudgetError("PATH_REJECTED")
    path = Path(raw)
    if path.is_absolute() or getattr(path, "drive", ""):
        raise StorageBudgetError("PATH_REJECTED")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise StorageBudgetError("PATH_REJECTED")
    return path


def _resolve_child(root: Path, relative: str | Path, *, leaf_may_be_missing: bool) -> Path:
    root = root.resolve(strict=True)
    relative_path = _safe_relative(relative)
    candidate = root.joinpath(*relative_path.parts)
    try:
        lexical = candidate.relative_to(root)
    except ValueError:
        raise StorageBudgetError("PATH_REJECTED") from None
    current = root
    for part in lexical.parts:
        current = current / part
        if os.path.lexists(current) and _is_link_or_junction(current):
            raise StorageBudgetError("PATH_REJECTED")
    try:
        resolved = candidate.resolve(strict=not leaf_may_be_missing)
    except (OSError, RuntimeError, ValueError):
        raise StorageBudgetError("PATH_REJECTED") from None
    if not _within(root, resolved):
        raise StorageBudgetError("PATH_REJECTED")
    if not leaf_may_be_missing and not os.path.lexists(resolved):
        raise StorageBudgetError("PATH_REJECTED")
    return resolved


def _measure_root(root: Path, *, recursive: bool = True) -> int:
    total = 0
    pending = [root]
    while pending:
        current = pending.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            raise StorageBudgetError("STORAGE_READ_FAILED") from None
        for entry in entries:
            path = Path(entry.path)
            if _is_link_or_junction(path):
                raise StorageBudgetError("PATH_REJECTED")
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError:
                raise StorageBudgetError("STORAGE_READ_FAILED") from None
            if entry.is_dir(follow_symlinks=False):
                if recursive:
                    pending.append(path)
            elif entry.is_file(follow_symlinks=False) and stat.S_ISREG(info.st_mode):
                total += _strict_integer(info.st_size, name="FILE_SIZE")
                if total > MAX_INTEGER:
                    raise StorageBudgetError("STORAGE_SIZE_OVERFLOW")
            else:
                raise StorageBudgetError("UNSUPPORTED_STORAGE_ENTRY")
    return total


@dataclass(frozen=True)
class _FlatRootMeasurement:
    """Per-operation observation for the private bounded flat-root writer path."""
    root: Path
    used_bytes: int
    entry_count: int
    entry_limit: int


def _measure_flat_root(root: str | Path, *, max_entries: int) -> _FlatRootMeasurement:
    """Strictly inspect one flat directory and return its current logical bytes."""
    resolved = _plain_directory(root)
    max_entries = _strict_integer(max_entries, name="ENTRY_LIMIT", minimum=1)
    entry_count = 0
    total = 0
    try:
        with os.scandir(resolved) as entries:
            for entry in entries:
                entry_count += 1
                if entry_count > max_entries:
                    raise StorageBudgetError("DIRECTORY_ENTRY_LIMIT")
                try:
                    # DirEntry caches metadata and Windows may report st_nlink=0
                    # there. Fresh os.stat is required for the hardlink trust check.
                    info = os.stat(entry.path, follow_symlinks=False)
                except OSError:
                    raise StorageBudgetError("STORAGE_READ_FAILED") from None
                if (stat.S_ISLNK(info.st_mode)
                        or getattr(info, "st_file_attributes", 0) & 0x400):
                    raise StorageBudgetError("PATH_REJECTED")
                if stat.S_ISDIR(info.st_mode):
                    continue
                if not stat.S_ISREG(info.st_mode):
                    raise StorageBudgetError("UNSUPPORTED_STORAGE_ENTRY")
                link_count = getattr(info, "st_nlink", None)
                if type(link_count) is not int or link_count < 1 or link_count > 1:
                    raise StorageBudgetError("PATH_REJECTED")
                total += _strict_integer(info.st_size, name="FILE_SIZE")
                if total > MAX_INTEGER:
                    raise StorageBudgetError("STORAGE_SIZE_OVERFLOW")
    except StorageBudgetError:
        raise
    except OSError:
        raise StorageBudgetError("STORAGE_READ_FAILED") from None
    return _FlatRootMeasurement(resolved, total, entry_count, max_entries)


def _digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while True:
                block = stream.read(len(_ZERO_BLOCK))
                if not block:
                    return digest.hexdigest()
                digest.update(block)
    except (OSError, ValueError):
        raise StorageBudgetError("STORAGE_READ_FAILED") from None


def _json_bytes(value: dict[str, Any]) -> bytes:
    """failure envelopeを固定JSONへ符号化し、NaN等の非JSON値を拒否する。"""

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        raise StorageBudgetError("INVALID_FAILURE_ENVELOPE") from None


def _zero_digest(size: int) -> str:
    """固定長zero領域のdigestを、全量をメモリへ載せずに計算する。"""

    digest = hashlib.sha256()
    remaining = size
    while remaining:
        block = _ZERO_BLOCK if remaining >= len(_ZERO_BLOCK) else b"\0" * remaining
        digest.update(block)
        remaining -= len(block)
    return digest.hexdigest()


def _fsync_directory(path: Path) -> None:
    """POSIXのrename耐久性を確認する。Windowsではdirectory fsync APIがない。"""

    if os.name == "nt":
        return
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        raise StorageBudgetError("DIRECTORY_FSYNC_FAILED") from None
    try:
        os.fsync(fd)
    except OSError:
        raise StorageBudgetError("DIRECTORY_FSYNC_FAILED") from None
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def is_capacity_error(error: BaseException) -> bool:
    """SQLite FULL とOS ENOSPCを成功経路から分離する。"""

    if isinstance(error, OSError) and getattr(error, "errno", None) == errno.ENOSPC:
        return True
    if isinstance(error, sqlite3.Error):
        if getattr(error, "sqlite_errorcode", None) == getattr(sqlite3, "SQLITE_FULL", 13):
            return True
    text = str(error).upper()
    return "ENOSPC" in text or "SQLITE_FULL" in text or "DATABASE OR DISK IS FULL" in text


@dataclass
class _ReservationState:
    token: int
    peak_bytes: int
    label: str
    state: str = "OPEN"


class Reservation:
    """StorageBudgetの単一予約。commit/abortはいずれか一度だけ許可する。"""

    def __init__(self, budget: "StorageBudget", record: _ReservationState):
        self._budget = budget
        self._record = record

    @property
    def token(self) -> int:
        return self._record.token

    @property
    def peak_bytes(self) -> int:
        return self._record.peak_bytes

    @property
    def state(self) -> str:
        return self._record.state

    def commit(self, net_bytes: int) -> dict[str, Any]:
        return self._budget._commit(self._record, net_bytes)

    def abort(self) -> dict[str, Any]:
        return self._budget._abort(self._record)


class StorageBudget:
    """指定rootの論理file bytesと予約を同時に管理する。

    ``quota_bytes`` はroot内の既存bytes、永続化bytes、active peak予約、
    ``rollback_reserve_bytes`` の合計上限である。物理block、volume、daemonの
    上限を証明するものではない。
    """

    def __init__(
        self,
        root: str | Path,
        quota_bytes: int,
        *,
        rollback_reserve_bytes: int,
        atomic_temp_overhead_bytes: int = DEFAULT_ATOMIC_TEMP_OVERHEAD_BYTES,
        recursive: bool = True,
    ) -> None:
        self.root = _plain_directory(root)
        self.quota_bytes = _strict_integer(quota_bytes, name="QUOTA")
        self.rollback_reserve_bytes = _strict_integer(
            rollback_reserve_bytes, name="ROLLBACK_RESERVE"
        )
        self.atomic_temp_overhead_bytes = _strict_integer(
            atomic_temp_overhead_bytes, name="ATOMIC_OVERHEAD"
        )
        if type(recursive) is not bool:
            raise StorageBudgetError("INVALID_RECURSIVE_MODE")
        self.recursive = recursive
        self._flat_entry_limit: int | None = None
        self._lock = threading.RLock()
        self._state = "OPEN"
        self._failure_code: str | None = None
        self._next_token = 1
        self._active: dict[int, _ReservationState] = {}
        self._used_bytes = _measure_root(self.root, recursive=self.recursive)
        if self._used_bytes + self.rollback_reserve_bytes > self.quota_bytes:
            raise StorageBudgetError("BUDGET_EXCEEDED")

    @classmethod
    def _from_flat_measurement(
        cls,
        root: str | Path,
        quota_bytes: int,
        *,
        rollback_reserve_bytes: int,
        atomic_temp_overhead_bytes: int,
        measurement: _FlatRootMeasurement,
    ) -> "StorageBudget":
        """Build the bounded writer budget from its just-locked strict pre-scan.

        Private trust boundary: bounded_files performs this flat-root scan while
        holding the same OS operation lock used through the write. No measurement
        is cached across writes or accepted from untrusted callers.
        """
        resolved = _plain_directory(root)
        if (not isinstance(measurement, _FlatRootMeasurement)
                or measurement.root != resolved
                or measurement.entry_count > measurement.entry_limit):
            raise StorageBudgetError("INVALID_INITIAL_MEASUREMENT")
        if type(atomic_temp_overhead_bytes) is not int:
            raise StorageBudgetError("INVALID_INTEGER_ATOMIC_OVERHEAD")
        budget = cls.__new__(cls)
        budget.root = resolved
        budget.quota_bytes = _strict_integer(quota_bytes, name="QUOTA")
        budget.rollback_reserve_bytes = _strict_integer(
            rollback_reserve_bytes, name="ROLLBACK_RESERVE"
        )
        budget.atomic_temp_overhead_bytes = _strict_integer(
            atomic_temp_overhead_bytes, name="ATOMIC_OVERHEAD"
        )
        budget.recursive = False
        budget._flat_entry_limit = measurement.entry_limit
        budget._lock = threading.RLock()
        budget._state = "OPEN"
        budget._failure_code = None
        budget._next_token = 1
        budget._active = {}
        budget._used_bytes = measurement.used_bytes
        if budget._used_bytes + budget.rollback_reserve_bytes > budget.quota_bytes:
            raise StorageBudgetError("BUDGET_EXCEEDED")
        return budget

    def _measure_current_root(self) -> int:
        if self._flat_entry_limit is None:
            return _measure_root(self.root, recursive=self.recursive)
        return _measure_flat_root(
            self.root, max_entries=self._flat_entry_limit
        ).used_bytes

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    @property
    def used_bytes(self) -> int:
        with self._lock:
            return self._used_bytes

    @property
    def active_reserved_bytes(self) -> int:
        with self._lock:
            return sum(item.peak_bytes for item in self._active.values())

    @property
    def available_bytes(self) -> int:
        with self._lock:
            return self._available_locked()

    def _available_locked(self) -> int:
        return (
            self.quota_bytes
            - self._used_bytes
            - self.rollback_reserve_bytes
            - sum(item.peak_bytes for item in self._active.values())
        )

    def _require_open_locked(self) -> None:
        if self._state == "FAILED":
            raise StorageBudgetError("BUDGET_FAILED")
        if self._state != "OPEN":
            raise StorageBudgetError("BUDGET_CLOSED")

    def reserve(self, peak_bytes: int, *, label: str = "") -> Reservation:
        peak_bytes = _strict_integer(peak_bytes, name="RESERVATION")
        with self._lock:
            self._require_open_locked()
            if peak_bytes > self._available_locked():
                raise StorageBudgetError("BUDGET_EXCEEDED")
            record = _ReservationState(self._next_token, peak_bytes, str(label))
            self._next_token += 1
            self._active[record.token] = record
            return Reservation(self, record)

    def _commit(self, record: _ReservationState, net_bytes: int) -> dict[str, Any]:
        if type(net_bytes) is not int or not -MAX_INTEGER <= net_bytes <= MAX_INTEGER:
            raise StorageBudgetError("INVALID_INTEGER_NET_BYTES")
        with self._lock:
            if record.state != "OPEN" or self._active.get(record.token) is not record:
                raise StorageBudgetError("RESERVATION_FINALIZED")
            if self._state != "OPEN":
                raise StorageBudgetError("BUDGET_FAILED" if self._state == "FAILED" else "BUDGET_CLOSED")
            if (
                net_bytes > record.peak_bytes
                or self._used_bytes + net_bytes < 0
                or self._used_bytes + net_bytes + self.rollback_reserve_bytes > self.quota_bytes
            ):
                self._fail_locked("RESERVATION_MISMATCH")
                record.state = "FAILED"
                self._active.pop(record.token, None)
                raise StorageBudgetError("RESERVATION_MISMATCH")
            self._used_bytes += net_bytes
            record.state = "COMMITTED"
            self._active.pop(record.token, None)
            return self.snapshot()

    def _abort(self, record: _ReservationState) -> dict[str, Any]:
        with self._lock:
            if record.state != "OPEN" or self._active.get(record.token) is not record:
                raise StorageBudgetError("RESERVATION_FINALIZED")
            record.state = "ABORTED"
            self._active.pop(record.token, None)
            return self.snapshot()

    def _fail_locked(self, code: str) -> None:
        if self._state == "OPEN":
            self._state = "FAILED"
            self._failure_code = code

    def fail(self, code: str) -> dict[str, Any]:
        if type(code) is not str or not code:
            raise StorageBudgetError("INVALID_FAILURE_CODE")
        with self._lock:
            self._fail_locked(code)
            return self.snapshot()

    def fail_from(self, error: BaseException) -> dict[str, Any]:
        return self.fail("CAPACITY_IO_ERROR" if is_capacity_error(error) else "STORAGE_IO_ERROR")

    def observe(self) -> dict[str, Any]:
        with self._lock:
            self._require_open_locked()
            actual = self._measure_current_root()
            if actual != self._used_bytes:
                self._fail_locked("UNTRACKED_WRITE")
                raise StorageBudgetError("UNTRACKED_WRITE")
            return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            active = sum(item.peak_bytes for item in self._active.values())
            return {
                "state": self._state,
                "failure_code": self._failure_code,
                "quota_bytes": self.quota_bytes,
                "used_bytes": self._used_bytes,
                "rollback_reserve_bytes": self.rollback_reserve_bytes,
                "active_reserved_bytes": active,
                "available_bytes": self.quota_bytes - self._used_bytes - self.rollback_reserve_bytes - active,
                "measurement": "logical_file_bytes",
            }

    def _ensure_destination_parent_locked(self, destination: Path) -> None:
        relative = destination.parent.relative_to(self.root)
        current = self.root
        for part in relative.parts:
            current = current / part
            if os.path.lexists(current):
                if _is_link_or_junction(current) or not current.is_dir():
                    raise StorageBudgetError("PATH_REJECTED")
            else:
                try:
                    current.mkdir()
                except FileExistsError:
                    if _is_link_or_junction(current) or not current.is_dir():
                        raise StorageBudgetError("PATH_REJECTED") from None
                except OSError:
                    raise StorageBudgetError("STORAGE_WRITE_FAILED") from None

    def atomic_write(
        self,
        relative: str | Path,
        data: bytes,
        *,
        temporary_reserve_bytes: int | None = None,
    ) -> dict[str, Any]:
        if type(data) is not bytes:
            raise StorageBudgetError("INVALID_BYTES")
        if not self.recursive and len(_safe_relative(relative).parts) != 1:
            raise StorageBudgetError("PATH_REJECTED")
        if temporary_reserve_bytes is None:
            temporary_reserve_bytes = len(data)
        temporary_reserve_bytes = _strict_integer(
            temporary_reserve_bytes, name="ATOMIC_TEMP"
        )
        if temporary_reserve_bytes < len(data):
            raise StorageBudgetError("ATOMIC_TEMP_TOO_SMALL")
        with self._lock:
            self._require_open_locked()
            if self._measure_current_root() != self._used_bytes:
                self._fail_locked("UNTRACKED_WRITE")
                raise StorageBudgetError("UNTRACKED_WRITE")
            destination = _resolve_child(self.root, relative, leaf_may_be_missing=True)
            self._ensure_destination_parent_locked(destination)
            old_size = 0
            if os.path.lexists(destination):
                if _is_link_or_junction(destination):
                    raise StorageBudgetError("PATH_REJECTED")
                try:
                    info = destination.lstat()
                except OSError:
                    raise StorageBudgetError("STORAGE_READ_FAILED") from None
                if not stat.S_ISREG(info.st_mode):
                    raise StorageBudgetError("UNSUPPORTED_STORAGE_ENTRY")
                old_size = _strict_integer(info.st_size, name="FILE_SIZE")
            peak = max(temporary_reserve_bytes, max(0, len(data) - old_size))
            peak += self.atomic_temp_overhead_bytes
            reservation = self.reserve(peak, label="atomic:" + Path(relative).as_posix())
            temporary: Path | None = None
            try:
                fd, name = tempfile.mkstemp(prefix=".gah-budget-", dir=str(destination.parent))
                temporary = Path(name)
                try:
                    _resolve_child(
                        self.root,
                        temporary.relative_to(self.root),
                        leaf_may_be_missing=False,
                    )
                except BaseException:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
                    raise
                with os.fdopen(fd, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                if os.path.lexists(destination):
                    if _is_link_or_junction(destination):
                        raise StorageBudgetError("PATH_REJECTED")
                    current_info = destination.stat()
                    if not stat.S_ISREG(current_info.st_mode):
                        raise StorageBudgetError("TARGET_CHANGED")
                    current_size = _strict_integer(current_info.st_size, name="FILE_SIZE")
                    if current_size != old_size:
                        raise StorageBudgetError("TARGET_CHANGED")
                os.replace(temporary, destination)
                temporary = None
                try:
                    _fsync_directory(destination.parent)
                except StorageBudgetError as error:
                    self._fail_locked(error.code)
                    reservation._record.state = "FAILED"
                    self._active.pop(reservation.token, None)
                    raise
                result = reservation.commit(len(data) - old_size)
                if _digest_file(destination) != hashlib.sha256(data).hexdigest():
                    self._fail_locked("WRITE_VERIFY_FAILED")
                    raise StorageBudgetError("WRITE_VERIFY_FAILED")
                if self._measure_current_root() != self._used_bytes:
                    self._fail_locked("WRITE_ACCOUNTING_MISMATCH")
                    raise StorageBudgetError("WRITE_ACCOUNTING_MISMATCH")
                return {
                    "relative_path": Path(relative).as_posix(),
                    "bytes_written": len(data),
                    "net_bytes": len(data) - old_size,
                    "budget": result,
                }
            except StorageBudgetError as error:
                if reservation.state == "OPEN":
                    reservation.abort()
                elif reservation.state == "COMMITTED":
                    self._fail_locked(error.code)
                raise
            except OSError as error:
                self._fail_locked("CAPACITY_IO_ERROR" if is_capacity_error(error) else "STORAGE_IO_ERROR")
                if reservation.state == "OPEN":
                    reservation._record.state = "FAILED"
                    self._active.pop(reservation.token, None)
                raise StorageBudgetError("CAPACITY_IO_ERROR" if is_capacity_error(error) else "STORAGE_IO_ERROR") from None
            finally:
                if temporary is not None:
                    try:
                        temporary.unlink()
                    except FileNotFoundError:
                        pass
                    except OSError:
                        self._fail_locked("TEMP_CLEANUP_FAILED")

    def close(self) -> dict[str, Any]:
        with self._lock:
            if self._active:
                raise StorageBudgetError("ACTIVE_RESERVATIONS")
            if self._state == "OPEN":
                self._state = "CLOSED"
            return self.snapshot()


@dataclass(frozen=True)
class SQLiteConfiguration:
    page_size: int
    max_page_count: int
    journal_mode: str
    synchronous: int
    temp_store: int
    rollback_reserve_bytes: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "page_size": self.page_size,
            "max_page_count": self.max_page_count,
            "journal_mode": self.journal_mode,
            "synchronous": self.synchronous,
            "temp_store": self.temp_store,
            "rollback_reserve_bytes": self.rollback_reserve_bytes,
        }


def _pragma_int(connection: sqlite3.Connection, name: str) -> int:
    try:
        row = connection.execute("PRAGMA " + name).fetchone()
        value = row[0] if row is not None else None
    except sqlite3.Error as error:
        raise StorageBudgetError("SQLITE_CONFIGURATION_FAILED") from error
    return _strict_integer(value, name="SQLITE_" + name.upper())


def configure_new_sqlite(
    connection: sqlite3.Connection,
    *,
    max_page_count: int,
    page_size: int = 4096,
    rollback_reserve_bytes: int = 0,
) -> SQLiteConfiguration:
    """空の新規DBだけを設定し、5つのSQLite設定をreadbackする。

    pathの未存在証明まで必要な場合は ``open_new_sqlite`` を使う。既存テーブルや
    pageがあるconnectionは、設定を変更する前に ``EXISTING_DATABASE`` で拒否する。
    """

    if not isinstance(connection, sqlite3.Connection):
        raise StorageBudgetError("INVALID_SQLITE_CONNECTION")
    max_page_count = _strict_integer(max_page_count, name="MAX_PAGE_COUNT", minimum=1)
    page_size = _strict_integer(page_size, name="PAGE_SIZE", minimum=512)
    rollback_reserve_bytes = _strict_integer(
        rollback_reserve_bytes, name="ROLLBACK_RESERVE"
    )
    if page_size > 65536 or page_size & (page_size - 1):
        raise StorageBudgetError("INVALID_PAGE_SIZE")
    try:
        if connection.in_transaction:
            raise StorageBudgetError("SQLITE_TRANSACTION_ACTIVE")
        page_count = _pragma_int(connection, "page_count")
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if page_count != 0 or tables:
            raise StorageBudgetError("EXISTING_DATABASE")
        connection.execute("PRAGMA page_size = " + str(page_size))
        if _pragma_int(connection, "page_size") != page_size:
            raise StorageBudgetError("SQLITE_READBACK_MISMATCH")
        connection.execute("PRAGMA max_page_count = " + str(max_page_count))
        if _pragma_int(connection, "max_page_count") != max_page_count:
            raise StorageBudgetError("SQLITE_READBACK_MISMATCH")
        connection.execute("PRAGMA journal_mode = DELETE")
        mode_row = connection.execute("PRAGMA journal_mode").fetchone()
        mode = mode_row[0].lower() if mode_row and type(mode_row[0]) is str else None
        if mode != "delete":
            raise StorageBudgetError("SQLITE_READBACK_MISMATCH")
        connection.execute("PRAGMA synchronous = FULL")
        synchronous = _pragma_int(connection, "synchronous")
        if synchronous != 2:
            raise StorageBudgetError("SQLITE_READBACK_MISMATCH")
        connection.execute("PRAGMA temp_store = MEMORY")
        temp_store = _pragma_int(connection, "temp_store")
        if temp_store != 2:
            raise StorageBudgetError("SQLITE_READBACK_MISMATCH")
    except StorageBudgetError:
        raise
    except sqlite3.Error as error:
        raise StorageBudgetError(
            "CAPACITY_IO_ERROR" if is_capacity_error(error) else "SQLITE_CONFIGURATION_FAILED"
        ) from None
    return SQLiteConfiguration(
        page_size=page_size,
        max_page_count=max_page_count,
        journal_mode="delete",
        synchronous=synchronous,
        temp_store=temp_store,
        rollback_reserve_bytes=rollback_reserve_bytes,
    )


def open_new_sqlite(
    path: str | Path,
    *,
    max_page_count: int,
    page_size: int = 4096,
    rollback_reserve_bytes: int = 0,
) -> tuple[sqlite3.Connection, SQLiteConfiguration]:
    """未存在pathをO_EXCLで確保してから、固定SQLite設定を適用する。"""

    target = Path(path)
    parent = _plain_directory(target.parent)
    if os.path.lexists(target):
        raise StorageBudgetError("EXISTING_DATABASE")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(str(target), flags, 0o600)
        os.close(fd)
    except FileExistsError:
        raise StorageBudgetError("EXISTING_DATABASE") from None
    except OSError as error:
        raise StorageBudgetError(
            "CAPACITY_IO_ERROR" if is_capacity_error(error) else "SQLITE_CREATE_FAILED"
        ) from None
    try:
        connection = sqlite3.connect(str(target), isolation_level=None, timeout=5.0)
    except sqlite3.Error as error:
        raise StorageBudgetError(
            "CAPACITY_IO_ERROR" if is_capacity_error(error) else "SQLITE_OPEN_FAILED"
        ) from None
    try:
        configuration = configure_new_sqlite(
            connection,
            max_page_count=max_page_count,
            page_size=page_size,
            rollback_reserve_bytes=rollback_reserve_bytes,
        )
    except BaseException:
        connection.close()
        raise
    return connection, configuration


class FailureSink:
    """事前割当・fsync・readback済みの一回限りfailure envelope保存先。

    同じrunの外部operation_lockを保持しているcallerだけがopen/recordを行う。
    このclassは複数process間の書込lockやcrash durabilityを提供しない。
    """

    def __init__(
        self,
        path: Path,
        capacity_bytes: int,
        *,
        root: Path | None,
        identity: tuple[int, int],
        physical_allocation_verified: bool,
        same_root: bool | None,
        fsync_verified: bool,
        readback_verified: bool,
        recorded_envelope: dict[str, Any] | None = None,
    ) -> None:
        self.path = path
        self.capacity_bytes = capacity_bytes
        self.root = root
        self._identity = identity
        self.physical_allocation_verified = physical_allocation_verified
        self.same_root = same_root
        self._fsync_verified = fsync_verified
        self._readback_verified = readback_verified
        self._recorded = recorded_envelope is not None
        self._envelope = recorded_envelope

    @staticmethod
    def _file_identity(info: os.stat_result) -> tuple[int, int]:
        link_count = getattr(info, "st_nlink", None)
        if (not stat.S_ISREG(info.st_mode) or type(link_count) is not int
                or link_count != 1):
            raise StorageBudgetError("PATH_REJECTED")
        device = getattr(info, "st_dev", None)
        inode = getattr(info, "st_ino", None)
        if type(device) is not int or type(inode) is not int:
            raise StorageBudgetError("PATH_REJECTED")
        return device, inode

    @staticmethod
    def _path_info(path: Path) -> os.stat_result:
        if _is_link_or_junction(path):
            raise StorageBudgetError("PATH_REJECTED")
        try:
            info = os.stat(str(path), follow_symlinks=False)
        except OSError:
            raise StorageBudgetError("SINK_READBACK_FAILED") from None
        FailureSink._file_identity(info)
        return info

    @classmethod
    def _target_and_root(
        cls, path: str | Path, root: str | Path | None
    ) -> tuple[Path, Path | None, bool | None]:
        target_input = Path(path)
        if not target_input.name or _is_link_or_junction(target_input):
            raise StorageBudgetError("PATH_REJECTED")
        parent = _plain_directory(target_input.parent)
        target = parent / target_input.name
        root_path = _plain_directory(root) if root is not None else None
        same_root = None
        if root_path is not None:
            try:
                same_root = _within(root_path, target.resolve(strict=True).parent)
            except (OSError, RuntimeError, ValueError):
                # As in preallocate(), root describes scope; it is not an allowlist.
                try:
                    same_root = _within(root_path, target.parent.resolve(strict=True))
                except (OSError, RuntimeError, ValueError):
                    raise StorageBudgetError("PATH_REJECTED") from None
        return target, root_path, same_root

    @classmethod
    def _open_checked(
        cls,
        target: Path,
        capacity_bytes: int,
        flags: int,
        *,
        expected_identity: tuple[int, int] | None = None,
    ) -> tuple[int, tuple[int, int], os.stat_result]:
        before = cls._path_info(target)
        before_identity = cls._file_identity(before)
        if expected_identity is not None and before_identity != expected_identity:
            raise StorageBudgetError("PATH_REJECTED")
        if before.st_size != capacity_bytes:
            raise StorageBudgetError("SINK_READBACK_FAILED")
        open_flags = (flags | getattr(os, "O_BINARY", 0)
                      | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        fd = os.open(str(target), open_flags)
        try:
            opened = os.fstat(fd)
            opened_identity = cls._file_identity(opened)
            if opened_identity != before_identity:
                raise StorageBudgetError("PATH_REJECTED")
            if expected_identity is not None and opened_identity != expected_identity:
                raise StorageBudgetError("PATH_REJECTED")
            if opened.st_size != capacity_bytes:
                raise StorageBudgetError("SINK_READBACK_FAILED")
            current = cls._path_info(target)
            if cls._file_identity(current) != opened_identity:
                raise StorageBudgetError("PATH_REJECTED")
            if current.st_size != capacity_bytes:
                raise StorageBudgetError("SINK_READBACK_FAILED")
            return fd, opened_identity, opened
        except BaseException:
            os.close(fd)
            raise

    @classmethod
    def _read_stream(cls, stream: Any, capacity_bytes: int) -> dict[str, Any] | None:
        stream.seek(0)
        header = stream.read(_FAILURE_HEADER_BYTES)
        if len(header) < _FAILURE_HEADER_BYTES:
            if any(header):
                raise StorageBudgetError("SINK_READBACK_FAILED")
            remaining = capacity_bytes - len(header)
            while remaining:
                block = stream.read(min(65536, remaining))
                if not block or any(block):
                    raise StorageBudgetError("SINK_READBACK_FAILED")
                remaining -= len(block)
            return None
        if not any(header):
            remaining = capacity_bytes - len(header)
            while remaining:
                block = stream.read(min(65536, remaining))
                if not block or any(block):
                    raise StorageBudgetError("SINK_READBACK_FAILED")
                remaining -= len(block)
            return None
        if header[: len(_FAILURE_MAGIC)] != _FAILURE_MAGIC:
            raise StorageBudgetError("SINK_READBACK_FAILED")
        size = struct.unpack("<Q", header[len(_FAILURE_MAGIC) :])[0]
        if size > capacity_bytes - _FAILURE_HEADER_BYTES:
            raise StorageBudgetError("SINK_READBACK_FAILED")
        payload = stream.read(size)
        if len(payload) != size:
            raise StorageBudgetError("SINK_READBACK_FAILED")
        remaining = capacity_bytes - _FAILURE_HEADER_BYTES - size
        while remaining:
            block = stream.read(min(65536, remaining))
            if not block or any(block):
                raise StorageBudgetError("SINK_READBACK_FAILED")
            remaining -= len(block)
        try:
            value = json.loads(
                payload.decode("utf-8"),
                parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
            )
            if _json_bytes(value) != payload or type(value) is not dict:
                raise StorageBudgetError("SINK_READBACK_FAILED")
            return value
        except StorageBudgetError:
            raise
        except (UnicodeError, ValueError, TypeError, RecursionError):
            raise StorageBudgetError("SINK_READBACK_FAILED") from None

    @staticmethod
    def _allocation_verified(info: os.stat_result, capacity_bytes: int) -> bool:
        blocks = getattr(info, "st_blocks", None)
        if blocks is None:
            return False
        if type(blocks) is not int or blocks < 0 or blocks * 512 < capacity_bytes:
            raise StorageBudgetError("SPARSE_RESERVE")
        return True

    @classmethod
    def _unlink_if_identity(cls, target: Path, identity: tuple[int, int] | None) -> None:
        if identity is None:
            return
        try:
            current = os.stat(str(target), follow_symlinks=False)
            if cls._file_identity(current) == identity:
                target.unlink()
        except (OSError, StorageBudgetError):
            pass

    @classmethod
    def preallocate(
        cls,
        path: str | Path,
        capacity_bytes: int,
        *,
        root: str | Path | None = None,
    ) -> "FailureSink":
        capacity_bytes = _strict_integer(capacity_bytes, name="SINK_CAPACITY", minimum=1)
        if capacity_bytes > MAX_FAILURE_SINK_BYTES:
            raise StorageBudgetError("SINK_CAPACITY_TOO_LARGE")
        target, root_path, same_root = cls._target_and_root(path, root)
        if os.path.lexists(target) or _is_link_or_junction(target):
            raise StorageBudgetError("SINK_EXISTS")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        created_identity = None
        try:
            fd = os.open(str(target), flags, 0o600)
            try:
                created_identity = cls._file_identity(os.fstat(fd))
            except BaseException:
                os.close(fd)
                raise
            with os.fdopen(fd, "wb") as stream:
                remaining = capacity_bytes
                while remaining:
                    block = _ZERO_BLOCK if remaining >= len(_ZERO_BLOCK) else b"\0" * remaining
                    stream.write(block)
                    remaining -= len(block)
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError:
            raise StorageBudgetError("SINK_EXISTS") from None
        except StorageBudgetError:
            cls._unlink_if_identity(target, created_identity)
            raise
        except OSError as error:
            cls._unlink_if_identity(target, created_identity)
            raise StorageBudgetError(
                "CAPACITY_IO_ERROR" if is_capacity_error(error) else "SINK_PREALLOCATE_FAILED"
            ) from None
        try:
            read_fd, identity, info = cls._open_checked(
                target, capacity_bytes, os.O_RDONLY, expected_identity=created_identity)
            with os.fdopen(read_fd, "rb") as stream:
                if cls._read_stream(stream, capacity_bytes) is not None:
                    raise StorageBudgetError("SINK_READBACK_FAILED")
            physical = cls._allocation_verified(info, capacity_bytes)
        except StorageBudgetError:
            cls._unlink_if_identity(target, created_identity)
            raise
        except OSError:
            raise StorageBudgetError("SINK_READBACK_FAILED") from None
        return cls(
            target,
            capacity_bytes,
            root=root_path,
            identity=identity,
            physical_allocation_verified=physical,
            same_root=same_root,
            fsync_verified=True,
            readback_verified=True,
        )

    @classmethod
    def open_existing(
        cls,
        path: str | Path,
        capacity_bytes: int,
        *,
        root: str | Path | None = None,
    ) -> "FailureSink":
        """Open one existing sink for one-shot writing under caller-held run lock.

        This method does not provide an inter-process lock or establish crash
        durability. Caller must serialize all accesses for the same run.
        """
        capacity_bytes = _strict_integer(capacity_bytes, name="SINK_CAPACITY", minimum=1)
        if capacity_bytes > MAX_FAILURE_SINK_BYTES:
            raise StorageBudgetError("SINK_CAPACITY_TOO_LARGE")
        target, root_path, same_root = cls._target_and_root(path, root)
        try:
            fd, identity, info = cls._open_checked(target, capacity_bytes, os.O_RDWR)
            with os.fdopen(fd, "r+b") as stream:
                envelope = cls._read_stream(stream, capacity_bytes)
                current = cls._path_info(target)
                if cls._file_identity(current) != identity:
                    raise StorageBudgetError("PATH_REJECTED")
            physical = cls._allocation_verified(info, capacity_bytes)
        except StorageBudgetError:
            raise
        except OSError:
            raise StorageBudgetError("SINK_READBACK_FAILED") from None
        return cls(
            target,
            capacity_bytes,
            root=root_path,
            identity=identity,
            physical_allocation_verified=physical,
            same_root=same_root,
            fsync_verified=False,
            readback_verified=True,
            recorded_envelope=envelope,
        )

    @property
    def scope(self) -> dict[str, Any]:
        return {
            "preallocated": True,
            "fsync_verified": self._fsync_verified,
            "readback_verified": self._readback_verified,
            "physical_allocation_verified": self.physical_allocation_verified,
            "same_root": self.same_root,
            "durable_after_enospc": False,
            "bound_verified": False,
        }

    def record(self, envelope: dict[str, Any]) -> dict[str, Any]:
        if type(envelope) is not dict:
            raise StorageBudgetError("INVALID_FAILURE_ENVELOPE")
        try:
            payload = _json_bytes(envelope)
        except (TypeError, ValueError):
            raise StorageBudgetError("INVALID_FAILURE_ENVELOPE") from None
        if _FAILURE_HEADER_BYTES + len(payload) > self.capacity_bytes:
            raise StorageBudgetError("SINK_CAPACITY_EXCEEDED")
        try:
            fd, identity, _info = type(self)._open_checked(
                self.path, self.capacity_bytes, os.O_RDWR,
                expected_identity=self._identity)
            with os.fdopen(fd, "r+b") as stream:
                existing = type(self)._read_stream(stream, self.capacity_bytes)
                if existing is not None:
                    if self._recorded and self._envelope != existing:
                        raise StorageBudgetError("SINK_READBACK_FAILED")
                    self._recorded = True
                    self._envelope = existing
                    raise StorageBudgetError("FAILURE_SINK_ALREADY_RECORDED")
                if self._recorded:
                    raise StorageBudgetError("SINK_READBACK_FAILED")
                stream.seek(0)
                header = _FAILURE_MAGIC + struct.pack("<Q", len(payload))
                stream.write(header)
                stream.write(payload)
                remaining = self.capacity_bytes - len(header) - len(payload)
                while remaining:
                    block = _ZERO_BLOCK if remaining >= len(_ZERO_BLOCK) else b"\0" * remaining
                    stream.write(block)
                    remaining -= len(block)
                stream.flush()
                os.fsync(stream.fileno())
                self._fsync_verified = True
                readback = type(self)._read_stream(stream, self.capacity_bytes)
                if readback != envelope:
                    raise StorageBudgetError("SINK_READBACK_FAILED")
                current = type(self)._path_info(self.path)
                if type(self)._file_identity(current) != identity:
                    raise StorageBudgetError("PATH_REJECTED")
        except StorageBudgetError:
            raise
        except OSError as error:
            raise StorageBudgetError(
                "CAPACITY_IO_ERROR" if is_capacity_error(error) else "SINK_WRITE_FAILED"
            ) from None
        self._recorded = True
        self._envelope = readback
        self._readback_verified = True
        return {"recorded": True, "payload_bytes": len(payload), "scope": self.scope}

    @classmethod
    def read_existing(
        cls,
        path: str | Path,
        capacity_bytes: int,
        *,
        root: str | Path | None = None,
    ) -> dict[str, Any] | None:
        """Read a preallocated sink without relying on prior process memory.

        This is a read-only recovery check. It does not establish that the sink is
        physically reserved or durable after future ENOSPC conditions.
        """
        capacity_bytes = _strict_integer(capacity_bytes, name="SINK_CAPACITY", minimum=1)
        if capacity_bytes > MAX_FAILURE_SINK_BYTES:
            raise StorageBudgetError("SINK_CAPACITY_TOO_LARGE")
        target, _root_path, _same_root = cls._target_and_root(path, root)
        try:
            fd, identity, _info = cls._open_checked(
                target, capacity_bytes, os.O_RDONLY)
            with os.fdopen(fd, "rb") as stream:
                value = cls._read_stream(stream, capacity_bytes)
                current = cls._path_info(target)
                if cls._file_identity(current) != identity:
                    raise StorageBudgetError("PATH_REJECTED")
            return value
        except StorageBudgetError:
            raise
        except (OSError, UnicodeError, ValueError, TypeError, RecursionError):
            raise StorageBudgetError("SINK_READBACK_FAILED") from None

    def read_record(self) -> dict[str, Any] | None:
        try:
            fd, identity, _info = type(self)._open_checked(
                self.path, self.capacity_bytes, os.O_RDONLY,
                expected_identity=self._identity)
            with os.fdopen(fd, "rb") as stream:
                value = type(self)._read_stream(stream, self.capacity_bytes)
                current = type(self)._path_info(self.path)
                if type(self)._file_identity(current) != identity:
                    raise StorageBudgetError("PATH_REJECTED")
            if self._recorded and value != self._envelope:
                raise StorageBudgetError("SINK_READBACK_FAILED")
            return value
        except StorageBudgetError:
            raise
        except (OSError, UnicodeError, ValueError, TypeError, RecursionError):
            raise StorageBudgetError("SINK_READBACK_FAILED") from None


__all__ = [
    "FailureSink",
    "Reservation",
    "SQLiteConfiguration",
    "StorageBudget",
    "StorageBudgetError",
    "configure_new_sqlite",
    "is_capacity_error",
    "open_new_sqlite",
]
