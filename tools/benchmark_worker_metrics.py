"""固定supervised runのworker metrics sidecarを計画・ExecutionJournalへ照合する。"""
from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
import stat
from typing import Any

from gah.execution_journal import ExecutionJournal, JournalError
from gah.run_contracts import content_ref, validate_run_manifest, validate_trial_plan
from gah.supervised_run import validate_input
from gah.supervisor_checkpoint import Checkpoint, CheckpointError
from gah.worker_metrics import WorkerMetricsJournal
from gah.wire import canonical_bytes

_MAX_ENTRIES = 1600
_SUM_FIELDS = ("cpu_ns", "io_read_bytes", "io_write_bytes", "memory_peak_bytes")
_TOTAL_KEYS = {
    "cpu_ns": "cpu_ns",
    "io_read_bytes": "io_read_bytes",
    "io_write_bytes": "io_write_bytes",
    "memory_peak_bytes": "cgroup_memory_peak_sum_bytes",
}
_JOURNAL_TABLES = {"journal_meta", "executions"}
_JOURNAL_COLUMNS = {
    "journal_meta": {"key", "value"},
    "executions": {
        "run_id", "operation_id", "request_digest", "binding_json", "scenario",
        "image_id", "run_deadline", "timeout_seconds", "container_name", "owner_token",
        "container_id", "state", "receipt_json", "receipt_digest",
    },
}


def _result(run_id: str | None, planned: int | None, *, missing=None, artifacts=None, totals=None, captured_count=0):
    missing = list(missing or [])
    artifacts = list(artifacts or [])
    return {
        "schema_version": 1,
        "kind": "worker_metrics_observation_set",
        "run_id": run_id,
        "status": "CAPTURE_COMPLETE" if planned is not None and captured_count == planned and not missing else "INCOMPLETE",
        "planned_count": planned,
        "captured_count": captured_count,
        "missing": missing,
        "artifacts": artifacts,
        "totals": totals if totals is not None else {output_name: None for output_name in _TOTAL_KEYS.values()},
        "rss_group_peak_bytes": None,
        "true_group_peak_bytes": None,
        "true_group_peak_measured": False,
        "slo_eligible": False,
        "ci_eligible": False,
    }


def _global_failure(reason: str, run_id: str | None = None, planned: int | None = None):
    return _result(run_id, planned, missing=[{"operation_id": None, "reason": reason}])


def _plain_existing_directory(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    return stat.S_ISDIR(info.st_mode) and not (getattr(info, "st_file_attributes", 0) & 0x400)


def _open_execution_journal_readonly(path: Path) -> sqlite3.Connection:
    """Open a fixed rollback-journal DB without creating/recovering sidecars."""
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or getattr(info, "st_nlink", 1) != 1:
            raise JournalError("STORAGE_CORRUPT")
        # This fixed product contract uses DELETE/rollback journaling. SQLite may
        # create -shm when opening a WAL DB, even when mode=ro, so inspect the
        # bounded header and reject any journal sidecars before sqlite3.connect.
        with path.open("rb") as stream:
            header = stream.read(100)
        if len(header) != 100 or header[:16] != b"SQLite format 3\x00":
            raise JournalError("STORAGE_CORRUPT")
        if header[18:20] != b"\x01\x01":
            raise JournalError("UNSUPPORTED_STORE")
        for suffix in ("-journal", "-wal", "-shm"):
            sidecar = path.with_name(path.name + suffix)
            if sidecar.exists() or sidecar.is_symlink():
                raise JournalError("UNSUPPORTED_STORE")
        uri = path.resolve().as_uri() + "?mode=ro"
        db = sqlite3.connect(uri, uri=True, timeout=1)
        db.row_factory = sqlite3.Row
        version = db.execute("PRAGMA user_version").fetchone()[0]
        tables = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if version != 1 or tables != _JOURNAL_TABLES:
            raise JournalError("UNSUPPORTED_STORE")
        for table, columns in _JOURNAL_COLUMNS.items():
            found = {row[1] for row in db.execute('PRAGMA table_info("' + table + '")')}
            if found != columns:
                raise JournalError("UNSUPPORTED_STORE")
        meta = db.execute("SELECT value FROM journal_meta WHERE key='schema_version'").fetchone()
        if meta is None or type(meta[0]) is not int or meta[0] != 1:
            raise JournalError("STORAGE_CORRUPT")
        return db
    except BaseException as error:
        if "db" in locals():
            db.close()
        if isinstance(error, JournalError):
            raise
        raise JournalError("STORAGE_FAILURE") from None


def _execution_row(db: sqlite3.Connection, run_id: str, operation_id: str) -> dict[str, Any]:
    try:
        row = db.execute("SELECT * FROM executions WHERE run_id=? AND operation_id=?",
                         (run_id, operation_id)).fetchone()
        if row is None:
            raise JournalError("NOT_FOUND")
        value, receipt = ExecutionJournal._verify_row(row)
        return ExecutionJournal._output(value, receipt)
    except JournalError:
        raise
    except sqlite3.Error:
        raise JournalError("STORAGE_CORRUPT") from None


def _expected_binding(entry: dict[str, Any], manifest: dict[str, Any], identity: dict[str, Any],
                      run_id: str, operation_id: str, start_binding: Any) -> dict[str, Any] | None:
    if type(start_binding) is not dict:
        return None
    expected = {
        "run_id": run_id,
        "operation_id": operation_id,
        "contract_digest": manifest["contract_ref"]["digest"],
        "target_digest": entry["target_ref"]["digest"],
        "obligation_id": entry["obligation_id"],
        "case_id": entry["case_id"],
        "trial_id": entry["trial_id"],
        "stage_id": entry["stage_ids"][0],
        "fixture_digest": identity["fixture_digest"],
        "adapter_digest": identity["adapter_digest"],
        "policy_digest": manifest["policy_ref"]["digest"],
        "evaluator_digest": entry["evaluator_ref"]["digest"],
        "isolation_digest": identity["isolation_digest"],
    }
    if any(start_binding.get(key) != value for key, value in expected.items()):
        return None
    if type(start_binding.get("owner_epoch")) is not int or start_binding["owner_epoch"] < 1:
        return None
    return start_binding


def collect_worker_observations(run_folder: str | Path, run_request: Any) -> dict[str, Any]:
    """Read bounded checkpoints/journals and return per-run aggregate worker metrics.

    The collector never creates the run folder, a checkpoint directory, or an
    execution database. Missing or mismatched planned operations remain explicit.
    """
    try:
        request = validate_input(run_request)
    except Exception:
        return _global_failure("RUN_REQUEST_INVALID")
    run_id = request["run_id"]
    folder = Path(run_folder)
    if not _plain_existing_directory(folder):
        return _global_failure("RUN_FOLDER_MISSING", run_id)
    checkpoint_dir = folder / "checkpoints"
    if not _plain_existing_directory(checkpoint_dir):
        return _global_failure("CHECKPOINTS_MISSING", run_id)
    try:
        checkpoint = Checkpoint(checkpoint_dir)
        response = checkpoint.get("response-prepare")
        identity_record = checkpoint.get("identity")
    except (CheckpointError, OSError, ValueError):
        return _global_failure("CHECKPOINT_INVALID", run_id)
    if type(response) is not dict or type(response.get("bound_run")) is not dict:
        return _global_failure("PREPARE_BOUND_RUN_MISSING", run_id)
    if type(identity_record) is not dict or identity_record.get("request") != request:
        return _global_failure("SOURCE_IDENTITY_MISMATCH", run_id)
    identity_fields = ("fixture_image", "fixture_digest", "adapter_digest", "isolation_digest")
    if any(type(identity_record.get(key)) is not str for key in identity_fields):
        return _global_failure("SOURCE_IDENTITY_MISSING", run_id)

    bound = response["bound_run"]
    try:
        manifest = validate_run_manifest(bound["manifest"])
        plan = validate_trial_plan(bound["plan"])
        if (manifest["run_id"] != run_id or manifest["purpose"] != "regression"
                or manifest["profile"] != "full"
                or manifest["contract_ref"] != request["expected_contract_ref"]
                or plan["contract_ref"] != manifest["contract_ref"]
                or manifest["plan_ref"] != content_ref("trial_plan", plan["plan_id"], plan)):
            return _global_failure("PLAN_BINDING_MISMATCH", run_id)
        use_cases = manifest["use_cases"]
        expected_count = 30 if use_cases == ["UC-CI"] else 800 if use_cases == ["UC-LLM"] else None
        if expected_count is None or len(plan["entries"]) != expected_count or expected_count > _MAX_ENTRIES:
            return _global_failure("PLAN_ENTRY_COUNT_INVALID", run_id)
    except Exception:
        return _global_failure("PLAN_INVALID", run_id)

    request_tag = "supervised-" + hashlib.sha256(canonical_bytes(request)).hexdigest()[:24]
    planned_count = len(plan["entries"])
    operations = [(request_tag + "-op-" + str(index), entry)
                  for index, entry in enumerate(plan["entries"])]
    journal_path = folder / "execution.sqlite"
    metrics_path = journal_path.with_name(journal_path.name + ".worker-metrics")
    if not journal_path.exists():
        return _result(run_id, planned_count,
                       missing=[{"operation_id": op, "reason": "EXECUTION_JOURNAL_MISSING"} for op, _ in operations])
    try:
        info = journal_path.lstat()
        if (not stat.S_ISREG(info.st_mode) or getattr(info, "st_nlink", 1) != 1
                or info.st_size > 256 * 1024 * 1024):
            return _result(run_id, planned_count,
                           missing=[{"operation_id": op, "reason": "EXECUTION_JOURNAL_INVALID"} for op, _ in operations])
        execution_db = _open_execution_journal_readonly(journal_path)
    except (JournalError, OSError):
        return _result(run_id, planned_count,
                       missing=[{"operation_id": op, "reason": "EXECUTION_JOURNAL_INVALID"} for op, _ in operations])

    metric_journal = WorkerMetricsJournal(metrics_path)
    missing: list[dict[str, str]] = []
    artifacts: list[dict[str, str]] = []
    captured_count = 0
    per_metric: dict[str, list[int | None]] = {name: [] for name in _SUM_FIELDS}
    try:
        for operation_id, entry in operations:
            try:
                start = checkpoint.get("start-" + operation_id)
                if type(start) is not dict:
                    raise _ObservationError("START_BINDING_MISSING")
                planned_binding = _expected_binding(entry, manifest, identity_record, run_id,
                                                    operation_id, start.get("binding"))
                if planned_binding is None:
                    raise _ObservationError("PLAN_SOURCE_BINDING_MISMATCH")
                row = _execution_row(execution_db, run_id, operation_id)
                if row["binding"] != planned_binding:
                    raise _ObservationError("EXECUTION_BINDING_MISMATCH")
                artifact = metric_journal.metrics_for(run_id, operation_id, row["request_digest"])
                if artifact is None:
                    raise _ObservationError("METRICS_MISSING")
                execution = artifact["execution"]
                expected_execution = {key: row[key] for key in
                                      ("run_id", "operation_id", "request_digest", "binding", "scenario", "image_id")}
                if execution != expected_execution:
                    raise _ObservationError("METRICS_EXECUTION_MISMATCH")
                source = artifact["source"]
                expected_source = {
                    "fixture_digest": identity_record["fixture_digest"],
                    "evaluator_digest": entry["evaluator_ref"]["digest"],
                    "adapter_digest": identity_record["adapter_digest"],
                    "policy_digest": manifest["policy_ref"]["digest"],
                    "target_digest": entry["target_ref"]["digest"],
                    "worker_digest": identity_record["fixture_digest"],
                }
                if (source != expected_source or row["image_id"] != identity_record["fixture_image"]):
                    raise _ObservationError("METRICS_SOURCE_MISMATCH")
                metrics = artifact["metrics"]
                if metrics["capture_status"] != "CAPTURED":
                    reason = "METRICS_" + metrics["capture_status"]
                    artifacts.append({"operation_id": operation_id,
                                      "artifact_digest": artifact["artifact_digest"]})
                    raise _ObservationError(reason)
                artifacts.append({"operation_id": operation_id,
                                  "artifact_digest": artifact["artifact_digest"]})
                captured_count += 1
                for name in _SUM_FIELDS:
                    value = metrics["memory_peak_bytes"] if name == "memory_peak_bytes" else metrics[name]
                    per_metric[name].append(value)
            except _ObservationError as error:
                missing.append({"operation_id": operation_id, "reason": str(error)})
            except JournalError as error:
                reason = "EXECUTION_ROW_MISSING" if str(error) == "NOT_FOUND" else "EXECUTION_ROW_INVALID"
                missing.append({"operation_id": operation_id, "reason": reason})
            except (CheckpointError, OSError, ValueError, KeyError, TypeError):
                missing.append({"operation_id": operation_id, "reason": "OBSERVATION_INVALID"})
    finally:
        execution_db.close()

    complete_captures = captured_count == planned_count and not missing
    totals = {
        _TOTAL_KEYS[name]: (sum(values) if complete_captures and len(values) == planned_count
                            and all(value is not None for value in values) else None)
        for name, values in per_metric.items()
    }
    result = _result(run_id, planned_count, missing=missing, artifacts=artifacts,
                     totals=totals, captured_count=captured_count)
    result["capture_complete"] = complete_captures
    result["contract_ref"] = manifest["contract_ref"]
    result["plan_ref"] = manifest["plan_ref"]
    return result


class _ObservationError(ValueError):
    pass


__all__ = ["collect_worker_observations"]
