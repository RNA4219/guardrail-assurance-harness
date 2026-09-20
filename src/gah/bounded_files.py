"""専用保存directoryで通常ファイルwriterを論理容量内に制限する。"""
from __future__ import annotations

import errno
import hashlib
import os
from pathlib import Path
import stat
import time
from typing import Any

MAX_DIRECTORY_BYTES = 256 * 1024 * 1024
MAX_DOCUMENT_BYTES = 1024 * 1024
MAX_DIRECTORY_ENTRIES = 65536


class BoundedFileError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _io_error(error: OSError) -> BoundedFileError:
    capacity = error.errno in {errno.ENOSPC, getattr(errno, "EDQUOT", -1)}
    return BoundedFileError("CAPACITY_IO_ERROR" if capacity else "IO_ERROR")


def _check_component(path: Path) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    except OSError as error:
        raise _io_error(error) from None
    if (stat.S_ISLNK(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & 0x400):
        raise BoundedFileError("PATH_REJECTED")


def _ensure_directory(path: Path) -> Path:
    absolute = Path(os.path.abspath(path))
    for item in reversed((absolute, *absolute.parents)):
        _check_component(item)
    try:
        absolute.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise _io_error(error) from None
    _check_component(absolute)
    try:
        if not absolute.is_dir():
            raise BoundedFileError("PATH_REJECTED")
        # Readback confirms the directory itself, not an inferred bound.
        info = absolute.stat()
        if not stat.S_ISDIR(info.st_mode):
            raise BoundedFileError("PATH_REJECTED")
    except OSError as error:
        raise _io_error(error) from None
    return absolute.resolve()


def _bounded_tree(root: Path) -> int:
    """Inspect only direct entries; child directories are separately budgeted roots."""
    from gah.storage_budget import StorageBudgetError, _measure_flat_root
    try:
        return _measure_flat_root(
            root, max_entries=MAX_DIRECTORY_ENTRIES
        ).entry_count
    except StorageBudgetError as error:
        code = error.code
        if code in {"STORAGE_READ_FAILED", "UNSUPPORTED_STORAGE_ENTRY"}:
            code = "IO_ERROR"
        raise BoundedFileError(code) from None


def _operation_lock_path(root: Path) -> Path:
    """docker_runner.operation_lockと同じ固定pathを、open前に検査する。"""
    from gah.wire import canonical_bytes
    key = hashlib.sha256(canonical_bytes(["bounded-files", "write"])).hexdigest()
    return root / (".gah-bounded-write." + key + ".lock")


def _validate_lock_file(path: Path) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    except OSError as error:
        raise _io_error(error) from None
    if (stat.S_ISLNK(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & 0x400
            or not stat.S_ISREG(info.st_mode)
            or getattr(info, "st_nlink", 1) > 1):
        raise BoundedFileError("PATH_REJECTED")


def _prepare_lock_file(path: Path) -> None:
    """Create/readback the one-byte lock file before operation_lock opens it."""
    deadline = time.monotonic() + 30.0
    while True:
        _validate_lock_file(path)
        try:
            fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL
                         | getattr(os, "O_BINARY", 0), 0o600)
        except FileExistsError:
            try:
                info = path.lstat()
            except OSError as error:
                raise _io_error(error) from None
            if info.st_size == 1:
                return
            if info.st_size != 0 or time.monotonic() >= deadline:
                raise BoundedFileError("PATH_REJECTED")
            time.sleep(0.005)
            continue
        except OSError as error:
            raise _io_error(error) from None
        try:
            if os.write(fd, b"0") != 1:
                raise BoundedFileError("IO_ERROR")
            os.fsync(fd)
        except OSError as error:
            raise _io_error(error) from None
        finally:
            os.close(fd)
        _validate_lock_file(path)
        try:
            info = path.lstat()
        except OSError as error:
            raise _io_error(error) from None
        if info.st_size != 1:
            raise BoundedFileError("PATH_REJECTED")
        return


def _readback_equal(path: Path, raw: bytes) -> bool:
    try:
        with path.open("r+b") as stream:
            existing = stream.read(MAX_DOCUMENT_BYTES + 1)
            if existing != raw:
                return False
            os.fsync(stream.fileno())
            return True
    except OSError as error:
        raise _io_error(error) from None


def write_bounded(path: str | Path, raw: bytes, *, immutable: bool = True,
                  _quota_bytes: int = MAX_DIRECTORY_BYTES) -> dict[str, Any]:
    """Write one bounded document beneath a dedicated, OS-locked directory.

    The logical quota applies only to regular files directly in ``path.parent``;
    child directories are independent roots. It is not a physical or aggregate disk
    guarantee. Same-root writers serialize; nested roots are not mutually serialized.
    ``_quota_bytes`` is reserved for trusted tests.
    """
    if type(raw) is not bytes:
        raise BoundedFileError("INVALID_BYTES")
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise BoundedFileError("DOCUMENT_TOO_LARGE")
    if type(immutable) is not bool:
        raise BoundedFileError("INVALID_MODE")
    if type(_quota_bytes) is not int or not 1 <= _quota_bytes <= MAX_DIRECTORY_BYTES:
        raise BoundedFileError("INVALID_QUOTA")
    target_input = Path(path)
    if not target_input.name or target_input.name in {".", ".."}:
        raise BoundedFileError("PATH_REJECTED")
    root = _ensure_directory(target_input.parent)
    target = root / target_input.name
    _check_component(target)
    try:
        from gah.docker_runner import RunnerError, operation_lock
        lock_path = _operation_lock_path(root)
        _prepare_lock_file(lock_path)
        deadline = time.monotonic() + 30.0
        while True:
            try:
                lock_context = operation_lock(root / ".gah-bounded-write", "bounded-files", "write")
                lock_context.__enter__()
                break
            except RunnerError as error:
                if error.code != "OWNER_ACTIVE" or time.monotonic() >= deadline:
                    raise BoundedFileError("OWNER_ACTIVE") from None
                time.sleep(0.01)
        try:
            from gah.storage_budget import (
                StorageBudget, StorageBudgetError, _measure_flat_root,
            )
            try:
                measurement = _measure_flat_root(
                    root, max_entries=MAX_DIRECTORY_ENTRIES
                )
            except StorageBudgetError as exc:
                code = exc.code
                if code in {"STORAGE_READ_FAILED", "UNSUPPORTED_STORAGE_ENTRY"}:
                    code = "IO_ERROR"
                raise BoundedFileError(code) from None
            if os.path.lexists(target):
                try:
                    info = target.lstat()
                except OSError as error:
                    raise _io_error(error) from None
                if not stat.S_ISREG(info.st_mode) or getattr(info, "st_nlink", 1) > 1:
                    raise BoundedFileError("PATH_REJECTED")
                if immutable:
                    if not _readback_equal(target, raw):
                        raise BoundedFileError("RESULT_CONFLICT")
                    return {"path": target.name, "bytes_written": 0,
                            "replayed": True, "sha256": hashlib.sha256(raw).hexdigest()}
            budget = None
            try:
                budget = StorageBudget._from_flat_measurement(
                    root, _quota_bytes, rollback_reserve_bytes=0,
                    atomic_temp_overhead_bytes=4096, measurement=measurement,
                )
                result = budget.atomic_write(target.name, raw)
            except StorageBudgetError as exc:
                code = ("CAPACITY_EXCEEDED" if exc.code == "BUDGET_EXCEEDED" else
                        exc.code if exc.code in {"DIRECTORY_ENTRY_LIMIT", "PATH_REJECTED",
                                                 "CAPACITY_IO_ERROR"} else
                        "IO_ERROR")
                raise BoundedFileError(code) from None
            finally:
                if budget is not None:
                    budget.close()
            return {"path": target.name, "bytes_written": len(raw),
                    "replayed": False, "sha256": hashlib.sha256(raw).hexdigest(),
                    "budget": result["budget"]}
        finally:
            lock_context.__exit__(None, None, None)
    except BoundedFileError:
        raise
    except OSError as error:
        raise _io_error(error) from None
    except Exception:
        # Keep provider path, file data, and implementation detail out of errors.
        raise BoundedFileError("IO_ERROR") from None


__all__ = ["BoundedFileError", "MAX_DIRECTORY_BYTES", "MAX_DOCUMENT_BYTES",
           "write_bounded"]
