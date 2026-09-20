"""runごとの最初の容量障害を、authorityの状態と分離して保持する。"""
from __future__ import annotations

import errno
import os
from pathlib import Path
import sqlite3

from .bounded_files import BoundedFileError
from .storage_budget import FailureSink, StorageBudgetError
from .supervisor_checkpoint import CheckpointError

FAILURE_SINK_BYTES = 64 * 1024
FAILURE_SINK_NAME = "capacity-failure.bin"
_STAGES = frozenset(("SINK_INIT", "CHECKPOINT_INIT", "SOURCE_LOCK", "SUPERVISOR_INIT", "EXECUTE"))


def capacity_reason(error):
    """型と固定code/errnoのみを見る。例外本文から容量不足を推測しない。"""
    code = None
    if isinstance(error, (StorageBudgetError, BoundedFileError)):
        code = error.code
    elif isinstance(error, CheckpointError):
        prefix = "CHECKPOINT_STORAGE_"
        if str(error).startswith(prefix):
            code = str(error)[len(prefix):]
    if code == "CAPACITY_IO_ERROR":
        return code
    if code in {"CAPACITY_EXCEEDED", "BUDGET_EXCEEDED", "DIRECTORY_ENTRY_LIMIT", "SINK_CAPACITY_EXCEEDED"}:
        return "CAPACITY_EXCEEDED"
    if isinstance(error, OSError) and error.errno in {errno.ENOSPC, getattr(errno, "EDQUOT", -1)}:
        return "CAPACITY_IO_ERROR"
    if isinstance(error, sqlite3.Error):
        number = getattr(error, "sqlite_errorcode", None)
        if type(number) is int and number & 255 == sqlite3.SQLITE_FULL:
            return "CAPACITY_IO_ERROR"
    return None


def prepare_sink(folder: Path, run_id: str, mode: str):
    """呼出側が同runのoperation_lockを保持する。statusは読取りのみ。"""
    target = folder / FAILURE_SINK_NAME
    if mode == "status":
        if os.path.lexists(target):
            FailureSink.read_existing(target, FAILURE_SINK_BYTES, root=folder)
        return None
    if os.path.lexists(target):
        sink = FailureSink.open_existing(target, FAILURE_SINK_BYTES, root=folder)
        previous = sink.read_record()
        if previous is not None and (previous.get("kind") != "supervised_capacity_failure"
                                      or previous.get("run_id") != run_id):
            raise StorageBudgetError("FAILURE_SINK_RUN_MISMATCH")
        return sink
    return FailureSink.preallocate(target, FAILURE_SINK_BYTES, root=folder)


def failure_result(sink, run_id: str, reason: str, stage: str):
    """証跡だけを保存する。停止・取消し・精算・receiptを生成しない。"""
    if reason not in {"CAPACITY_IO_ERROR", "CAPACITY_EXCEEDED", "SUPERVISION_INCOMPLETE"} or stage not in _STAGES:
        raise ValueError("CAPACITY_FAILURE_INVALID")
    recorded = "UNAVAILABLE"
    if sink is not None and reason != "SUPERVISION_INCOMPLETE":
        envelope = {"schema_version": 1, "kind": "supervised_capacity_failure",
                    "run_id": run_id, "reason": reason, "failure_stage": stage,
                    "checkpoint_state": "UNKNOWN", "receipt_state": "UNKNOWN",
                    "unsettled_operations": {"state": "UNKNOWN"},
                    "ci_eligible": False, "exit_code": 2}
        try:
            previous = sink.read_record()
            if previous is not None:
                recorded = "PRIOR_RECORD_PRESERVED"
            else:
                sink.record(envelope)
                recorded = "RECORDED"
        except (Exception, KeyboardInterrupt):
            recorded = "UNAVAILABLE"
    return {"schema_version": 1, "kind": "supervised_run_error", "reason": reason,
            "failure_record": recorded, "ci_eligible": False, "exit_code": 2}
