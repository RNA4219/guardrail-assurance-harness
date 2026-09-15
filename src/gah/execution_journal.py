"""固定fixtureの送信意図と中断状態を記録する独立SQLite journal。

この部品はDockerを起動せず、実行監督が生成した意図・container識別子・停止状態・
最終receiptだけを保存する。receiptは信頼したrunnerがこのモジュールの固定構造で
生成したdictを受け付け、raw bytes・stdout/stderr・任意payloadは受け付けない。
認証、隔離、実際の停止確認、exactly-once実行はこのjournalの保証範囲外である。
"""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterator
import uuid

from .contracts import MAX_INTEGER, ContractError, decode_document, require_digest, require_id, require_uint
from .normalized import validate_binding
from .wire import canonical_bytes


MAX_RECEIPT_BYTES = 512 * 1024
_SCHEMA_VERSION = 1
_STATES = ("INTENT", "CREATED", "STARTING", "RUNNING", "STOPPED", "FINISHED")
_ADVANCE = {state: index for index, state in enumerate(_STATES[:-1])}
_SCENARIO = re.compile(
    r"(?:constraint:C(?:0[1-9]|10):(?:good|bad)|"
    r"mutation:F(?:0[1-5]):(?:healthy|decayed)|"
    r"guardrail:[0-9a-f]{64}|"
    r"probe:(?:isolation|child_timeout|oversized|malformed|rejected_marker|crash))\Z"
)
_IMAGE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_TOKEN = re.compile(r"[0-9a-f]{32}\Z")
_CONTAINER = re.compile(r"gah-[0-9a-f]{32}\Z")
_CONTAINER_ID = re.compile(r"[0-9a-f]{64}\Z")
_RECEIPT_FIELDS = {
    "schema_version", "kind", "binding", "scenario", "image_id",
    "container_id", "execution_status", "exit_code", "stop_confirmed", "reason",
    "elapsed_millis", "isolation_config_verified", "output_disposition",
    "cleanup_confirmed", "recovered", "normalized_result", "probe_result",
    "ci_eligible",
}
_EXECUTION_STATUSES = {"COMPLETED", "FAILED", "TIMEOUT", "CANCELLED"}
_REASONS = {
    "DOCKER_UNAVAILABLE", "IMAGE_MISMATCH", "CONFIG_MISMATCH", "TIMEOUT",
    "RUN_DEADLINE", "CANCEL_REQUESTED", "OUTPUT_TOO_LARGE", "OUTPUT_REJECTED",
    "EXECUTION_FAILURE", "STOP_UNCONFIRMED", "CLEANUP_FAILED", "INTERRUPTED",
    "CLOCK_FAILURE",
}
_OUTPUT_DISPOSITIONS = {"ADMITTED", "REJECTED", "NOT_COLLECTED"}
_NORMALIZED_FIELDS = {
    "schema_version", "kind", "binding", "mode", "observation", "mutation_outcome",
    "detection", "deviation", "error_class", "raw_digest",
}
_NORMALIZED_MODES = {"constraint", "mutation", "llm"}
_NORMALIZED_OBSERVATIONS = {"PASS", "FAIL", "UNKNOWN"}
_NORMALIZED_OUTCOMES = {"KILLED", "SURVIVED", "NO_COVERAGE", "ERROR"}
_NORMALIZED_DETECTIONS = {"detect", "allow", "indeterminate"}
_NORMALIZED_ERRORS = {
    "EXECUTION_FAILURE", "BASELINE_NOT_PASS", "UNRELATED_FAILURE", "MUTATION_NOT_APPLIED",
}
_PROBE_CHECKS = {
    "nonroot", "root_readonly", "network_unreachable", "capabilities_dropped",
    "no_new_privileges", "seccomp_filter", "pid_limit", "memory_limit", "cpu_limit",
}
_TABLES = {"journal_meta", "executions"}
_COLUMNS = {
    "journal_meta": {"key", "value"},
    "executions": {
        "run_id", "operation_id", "request_digest", "binding_json", "scenario",
        "image_id", "run_deadline", "timeout_seconds", "container_name",
        "owner_token", "container_id", "state", "receipt_json", "receipt_digest",
    },
}


class JournalError(ValueError):
    """固定codeだけを公開するjournalエラー。"""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _invalid(code: str = "INVALID_CONTRACT") -> ContractError:
    return ContractError(code)


def _require_scenario(value: Any) -> None:
    if type(value) is not str or not _SCENARIO.fullmatch(value):
        raise _invalid("INVALID_SCENARIO")


def _require_image(value: Any) -> None:
    if type(value) is not str or not _IMAGE.fullmatch(value):
        raise _invalid("INVALID_IMAGE_ID")


def _require_container_id(value: Any, *, allow_none: bool) -> None:
    if allow_none and value is None:
        return
    if type(value) is not str or not _CONTAINER_ID.fullmatch(value):
        raise _invalid("INVALID_CONTAINER_ID")


def _canonical_object(value: Any, *, maximum: int | None = None) -> tuple[dict[str, Any], bytes]:
    if type(value) is not dict:
        raise _invalid()
    try:
        payload = canonical_bytes(value)
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise _invalid() from None
    if maximum is not None and len(payload) > maximum:
        raise _invalid("RECEIPT_TOO_LARGE")
    try:
        parsed = decode_document(payload)
    except ContractError:
        raise _invalid() from None
    if parsed != value:
        raise _invalid()
    return parsed, payload


def _validate_normalized(value: Any, binding: dict[str, Any]) -> None:
    normalized, _ = _canonical_object(value)
    if set(normalized) != _NORMALIZED_FIELDS:
        raise _invalid("INVALID_NORMALIZED_RESULT")
    if type(normalized["schema_version"]) is not int or normalized["schema_version"] != 1 \
            or normalized["kind"] != "normalized_result":
        raise _invalid("INVALID_NORMALIZED_RESULT")
    try:
        normalized_binding = validate_binding(normalized["binding"])
    except ContractError:
        raise _invalid("INVALID_NORMALIZED_RESULT") from None
    if normalized_binding != binding:
        raise _invalid("BINDING_MISMATCH")
    mode = normalized["mode"]
    if mode is not None and (type(mode) is not str or mode not in _NORMALIZED_MODES):
        raise _invalid("INVALID_NORMALIZED_RESULT")
    observation = normalized["observation"]
    if observation is not None and (type(observation) is not str or observation not in _NORMALIZED_OBSERVATIONS):
        raise _invalid("INVALID_NORMALIZED_RESULT")
    outcome = normalized["mutation_outcome"]
    if outcome is not None and (type(outcome) is not str or outcome not in _NORMALIZED_OUTCOMES):
        raise _invalid("INVALID_NORMALIZED_RESULT")
    detection = normalized["detection"]
    if detection is not None and (type(detection) is not str or detection not in _NORMALIZED_DETECTIONS):
        raise _invalid("INVALID_NORMALIZED_RESULT")
    if normalized["deviation"] is not None and type(normalized["deviation"]) is not bool:
        raise _invalid("INVALID_NORMALIZED_RESULT")
    error_class = normalized["error_class"]
    if error_class is not None and (type(error_class) is not str or error_class not in _NORMALIZED_ERRORS):
        raise _invalid("INVALID_NORMALIZED_RESULT")
    raw_digest = normalized["raw_digest"]
    if raw_digest is not None:
        try:
            require_digest(raw_digest)
        except ContractError:
            raise _invalid("INVALID_NORMALIZED_RESULT") from None
    if mode is None:
        if (observation is not None or outcome != "ERROR" or detection is not None
                or normalized["deviation"] is not None or raw_digest is not None
                or error_class is None):
            raise _invalid("INVALID_NORMALIZED_RESULT")
    elif mode == "constraint":
        if (observation is None or outcome is not None or detection is not None
                or normalized["deviation"] is not None or error_class is not None
                or raw_digest is None):
            raise _invalid("INVALID_NORMALIZED_RESULT")
    elif mode == "mutation":
        if (observation is None or outcome is None or detection is not None
                or normalized["deviation"] is not None or raw_digest is None):
            raise _invalid("INVALID_NORMALIZED_RESULT")
    elif mode == "llm":
        if (observation is not None or outcome is not None or detection is None
                or error_class is not None or raw_digest is None):
            raise _invalid("INVALID_NORMALIZED_RESULT")


def _validate_probe(value: Any, binding: dict[str, Any]) -> None:
    probe, _ = _canonical_object(value)
    if set(probe) != {"schema_version", "kind", "binding", "checks"}:
        raise _invalid("INVALID_PROBE_RESULT")
    if type(probe["schema_version"]) is not int or probe["schema_version"] != 1 \
            or probe["kind"] != "gah_isolation_probe":
        raise _invalid("INVALID_PROBE_RESULT")
    try:
        probe_binding = validate_binding(probe["binding"])
    except ContractError:
        raise _invalid("INVALID_PROBE_RESULT") from None
    if probe_binding != binding:
        raise _invalid("BINDING_MISMATCH")
    checks = probe["checks"]
    if type(checks) is not dict or set(checks) != _PROBE_CHECKS:
        raise _invalid("INVALID_PROBE_RESULT")
    if any(type(value) is not bool for value in checks.values()):
        raise _invalid("INVALID_PROBE_RESULT")


def _request(binding: dict[str, Any], scenario: str, image_id: str,
             deadline: int, timeout: int) -> tuple[str, str]:
    request = {
        "binding": binding,
        "scenario": scenario,
        "image_id": image_id,
        "run_deadline": deadline,
        "timeout_seconds": timeout,
    }
    payload = canonical_bytes(request)
    return payload.decode("utf-8"), hashlib.sha256(payload).hexdigest()


def _validate_begin(binding: Any, scenario: Any, image_id: Any,
                    run_deadline: Any, timeout_seconds: Any) -> tuple[dict[str, Any], str, str, int, int, str]:
    try:
        validated = validate_binding(binding)
    except ContractError:
        raise
    _require_scenario(scenario)
    _require_image(image_id)
    try:
        require_uint(run_deadline)
        if not 1 <= timeout_seconds <= 120 or type(timeout_seconds) is not int:
            raise _invalid("INVALID_TIMEOUT")
    except TypeError:
        raise _invalid("INVALID_TIMEOUT") from None
    _, request_digest = _request(
        validated, scenario, image_id, run_deadline, timeout_seconds
    )
    binding_json = canonical_bytes(validated).decode("utf-8")
    return validated, scenario, image_id, run_deadline, timeout_seconds, binding_json + "\x00" + request_digest


def _validate_receipt(value: Any, *, binding: dict[str, Any], scenario: str,
                      image_id: str, container_id: str | None) -> tuple[dict[str, Any], bytes, str]:
    receipt, payload = _canonical_object(value, maximum=MAX_RECEIPT_BYTES)
    guardrail = scenario.startswith("guardrail:")
    if set(receipt) != (_RECEIPT_FIELDS | {"case_result"} if guardrail else _RECEIPT_FIELDS):
        raise _invalid("INVALID_RECEIPT")
    if type(receipt["schema_version"]) is not int or receipt["schema_version"] != 1:
        raise _invalid("UNSUPPORTED_VERSION")
    if receipt["kind"] != ("guardrail_execution" if guardrail else "fixture_execution") or receipt["ci_eligible"] is not False:
        raise _invalid("INVALID_RECEIPT")
    try:
        receipt_binding = validate_binding(receipt["binding"])
    except ContractError:
        raise _invalid("INVALID_RECEIPT") from None
    if receipt_binding != binding or receipt["scenario"] != scenario or receipt["image_id"] != image_id:
        raise _invalid("BINDING_MISMATCH")
    receipt_container_id = receipt.get("container_id")
    _require_container_id(receipt_container_id, allow_none=True)
    if receipt_container_id != container_id:
        raise _invalid("BINDING_MISMATCH")
    if type(receipt["execution_status"]) is not str or receipt["execution_status"] not in _EXECUTION_STATUSES:
        raise _invalid("INVALID_RECEIPT")
    if receipt["exit_code"] is not None and (type(receipt["exit_code"]) is not int
                                              or not 0 <= receipt["exit_code"] <= 255):
        raise _invalid("INVALID_RECEIPT")
    if type(receipt["stop_confirmed"]) is not bool:
        raise _invalid("INVALID_RECEIPT")
    if receipt["reason"] is not None:
        if type(receipt["reason"]) is not str or receipt["reason"] not in _REASONS:
            raise _invalid("INVALID_RECEIPT")
    try:
        require_uint(receipt["elapsed_millis"])
    except ContractError:
        raise _invalid("INVALID_RECEIPT") from None
    if type(receipt["isolation_config_verified"]) is not bool:
        raise _invalid("INVALID_RECEIPT")
    if (type(receipt["output_disposition"]) is not str
            or receipt["output_disposition"] not in _OUTPUT_DISPOSITIONS):
        raise _invalid("INVALID_RECEIPT")
    for field in ("cleanup_confirmed", "recovered"):
        if type(receipt[field]) is not bool:
            raise _invalid("INVALID_RECEIPT")
    if guardrail:
        if receipt["normalized_result"] is not None or receipt["probe_result"] is not None:
            raise _invalid("INVALID_RECEIPT")
        if receipt["case_result"] is not None:
            from .guardrail_results import validate_bundle
            try:
                validate_bundle(receipt["case_result"])
                request = receipt["case_result"]["request"]
                if ("guardrail:" + hashlib.sha256(canonical_bytes(request)).hexdigest() != scenario
                        or request["stages"][0]["binding"] != binding or request["target"]["runtime_image_id"] != image_id):
                    raise ContractError()
            except (ContractError, KeyError, TypeError, ValueError):
                raise _invalid("INVALID_RECEIPT") from None
    if receipt["normalized_result"] is not None:
        _validate_normalized(receipt["normalized_result"], binding)
    if receipt["probe_result"] is not None:
        _validate_probe(receipt["probe_result"], binding)

    status = receipt["execution_status"]
    reason = receipt["reason"]
    normalized = receipt["normalized_result"]
    probe = receipt["probe_result"]
    if status == "COMPLETED":
        if (receipt["exit_code"] != 0 or receipt_container_id is None
                or not receipt["stop_confirmed"] or not receipt["cleanup_confirmed"]
                or not receipt["isolation_config_verified"] or reason is not None
                or receipt["recovered"] or receipt["output_disposition"] != "ADMITTED"):
            raise _invalid("INVALID_RECEIPT")
        wants_probe = scenario == "probe:isolation"
        if (guardrail and receipt["case_result"] is None) or (not guardrail and ((wants_probe and (probe is None or normalized is not None)) or (
                not wants_probe and (normalized is None or probe is not None)))):
            raise _invalid("INVALID_RECEIPT")
        if wants_probe:
            if not all(probe["checks"].values()):
                raise _invalid("INVALID_RECEIPT")
        elif not guardrail and (scenario.startswith("probe:") or normalized["mode"] != scenario.split(":", 1)[0]):
            raise _invalid("INVALID_RECEIPT")
    else:
        if (normalized is not None or probe is not None or guardrail and receipt["case_result"] is not None
                or receipt["output_disposition"] == "ADMITTED"):
            raise _invalid("INVALID_RECEIPT")
        if status == "CANCELLED":
            valid_reasons = {"CANCEL_REQUESTED"}
        elif status == "TIMEOUT":
            valid_reasons = {"TIMEOUT", "RUN_DEADLINE"}
        else:
            valid_reasons = _REASONS - {"TIMEOUT", "RUN_DEADLINE", "CANCEL_REQUESTED"}
        if reason not in valid_reasons:
            raise _invalid("INVALID_RECEIPT")
        expected_disposition = (
            "REJECTED" if reason in {"OUTPUT_REJECTED", "OUTPUT_TOO_LARGE"}
            else "NOT_COLLECTED"
        )
        if receipt["output_disposition"] != expected_disposition:
            raise _invalid("INVALID_RECEIPT")
    return receipt, payload, hashlib.sha256(payload).hexdigest()


class ExecutionJournal:
    """Docker実行監督の送信意図・状態・receiptを保存するSQLite v1。"""

    def __init__(self, path: str | Path):
        self._db: sqlite3.Connection | None = None
        try:
            db = sqlite3.connect(str(Path(path)), isolation_level=None, timeout=5)
            self._db = db
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            with self._transaction() as connection:
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                tables = {
                    row[0] for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                if version == 0 and not tables:
                    connection.execute(
                        "CREATE TABLE journal_meta(key TEXT PRIMARY KEY, value INTEGER NOT NULL)"
                    )
                    connection.execute(
                        "INSERT INTO journal_meta(key,value) VALUES('schema_version',1)"
                    )
                    connection.execute(
                        """CREATE TABLE executions(
                        run_id TEXT NOT NULL,
                        operation_id TEXT NOT NULL,
                        request_digest TEXT NOT NULL,
                        binding_json TEXT NOT NULL,
                        scenario TEXT NOT NULL,
                        image_id TEXT NOT NULL,
                        run_deadline INTEGER NOT NULL,
                        timeout_seconds INTEGER NOT NULL,
                        container_name TEXT NOT NULL,
                        owner_token TEXT NOT NULL,
                        container_id TEXT,
                        state TEXT NOT NULL,
                        receipt_json TEXT,
                        receipt_digest TEXT,
                        PRIMARY KEY(run_id, operation_id)
                        )"""
                    )
                    connection.execute("PRAGMA user_version=1")
                elif version != _SCHEMA_VERSION or tables != _TABLES:
                    raise JournalError("UNSUPPORTED_STORE")
                for table, columns in _COLUMNS.items():
                    found = {
                        row[1] for row in connection.execute(
                            'PRAGMA table_info("' + table + '")'
                        )
                    }
                    if found != columns:
                        raise JournalError("UNSUPPORTED_STORE")
                row = connection.execute(
                    "SELECT value FROM journal_meta WHERE key='schema_version'"
                ).fetchone()
                if row is None or type(row[0]) is not int or row[0] != _SCHEMA_VERSION:
                    raise JournalError("STORAGE_CORRUPT")
        except BaseException as error:
            self.close()
            if isinstance(error, (sqlite3.Error, OSError)):
                raise JournalError("STORAGE_FAILURE") from None
            raise

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        if self._db is None:
            raise JournalError("JOURNAL_CLOSED")
        try:
            self._db.execute("BEGIN IMMEDIATE")
            yield self._db
            self._db.commit()
        except BaseException as error:
            try:
                self._db.rollback()
            except sqlite3.Error:
                pass
            if isinstance(error, sqlite3.Error):
                raise JournalError("STORAGE_FAILURE") from None
            raise

    def __enter__(self) -> "ExecutionJournal":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        if self._db is not None:
            self._db.close()
            self._db = None

    @staticmethod
    def _verify_row(row: sqlite3.Row) -> tuple[dict[str, Any], dict[str, Any] | None]:
        try:
            run_id = row["run_id"]
            operation_id = row["operation_id"]
            require_id(run_id)
            require_id(operation_id)
            binding = decode_document(row["binding_json"].encode("utf-8"))
            binding = validate_binding(binding)
            if binding["run_id"] != run_id or binding["operation_id"] != operation_id:
                raise JournalError("STORAGE_CORRUPT")
            _require_scenario(row["scenario"])
            _require_image(row["image_id"])
            require_uint(row["run_deadline"])
            if type(row["timeout_seconds"]) is not int or not 1 <= row["timeout_seconds"] <= 120:
                raise JournalError("STORAGE_CORRUPT")
            if type(row["container_name"]) is not str or not _CONTAINER.fullmatch(row["container_name"]):
                raise JournalError("STORAGE_CORRUPT")
            if type(row["owner_token"]) is not str or not _TOKEN.fullmatch(row["owner_token"]):
                raise JournalError("STORAGE_CORRUPT")
            _require_container_id(row["container_id"], allow_none=True)
            if row["state"] not in _STATES:
                raise JournalError("STORAGE_CORRUPT")
            _, request_digest = _request(
                binding, row["scenario"], row["image_id"],
                row["run_deadline"], row["timeout_seconds"]
            )
            if (canonical_bytes(binding).decode("utf-8") != row["binding_json"]
                    or request_digest != row["request_digest"]):
                raise JournalError("STORAGE_CORRUPT")
            receipt = None
            if row["receipt_json"] is None or row["receipt_digest"] is None:
                if row["receipt_json"] is not None or row["receipt_digest"] is not None:
                    raise JournalError("STORAGE_CORRUPT")
            else:
                receipt = decode_document(row["receipt_json"].encode("utf-8"))
                receipt, payload, digest = _validate_receipt(
                    receipt, binding=binding, scenario=row["scenario"],
                    image_id=row["image_id"], container_id=row["container_id"]
                )
                if payload.decode("utf-8") != row["receipt_json"] or digest != row["receipt_digest"]:
                    raise JournalError("STORAGE_CORRUPT")
            if (row["state"] == "FINISHED") != (receipt is not None):
                raise JournalError("STORAGE_CORRUPT")
            value = {
                "run_id": run_id, "operation_id": operation_id,
                "request_digest": row["request_digest"], "binding": binding,
                "scenario": row["scenario"], "image_id": row["image_id"],
                "run_deadline": row["run_deadline"], "timeout_seconds": row["timeout_seconds"],
                "container_name": row["container_name"], "owner_token": row["owner_token"],
                "container_id": row["container_id"], "state": row["state"],
            }
            return value, receipt
        except JournalError:
            raise
        except (ContractError, KeyError, TypeError, AttributeError, UnicodeError, ValueError):
            raise JournalError("STORAGE_CORRUPT") from None

    @staticmethod
    def _output(value: dict[str, Any], receipt: dict[str, Any] | None, *, new: bool | None = None) -> dict[str, Any]:
        result = dict(value)
        if receipt is not None:
            result["receipt"] = receipt
        if new is not None:
            result["new"] = new
        return result

    def begin(self, binding: Any, scenario: Any, image_id: Any, *,
              run_deadline: int, timeout_seconds: int) -> dict[str, Any]:
        validated, scenario, image_id, deadline, timeout, packed = _validate_begin(
            binding, scenario, image_id, run_deadline, timeout_seconds
        )
        binding_json, request_digest = packed.split("\x00", 1)
        run_id = validated["run_id"]
        operation_id = validated["operation_id"]
        with self._transaction() as db:
            row = db.execute(
                "SELECT * FROM executions WHERE run_id=? AND operation_id=?",
                (run_id, operation_id),
            ).fetchone()
            if row is not None:
                current, receipt = self._verify_row(row)
                if current["request_digest"] != request_digest:
                    raise JournalError("REQUEST_CONFLICT")
                return self._output(current, receipt, new=False)
            token = uuid.uuid4().hex
            container_name = "gah-" + uuid.uuid4().hex
            db.execute(
                """INSERT INTO executions(
                run_id,operation_id,request_digest,binding_json,scenario,image_id,
                run_deadline,timeout_seconds,container_name,owner_token,container_id,
                state,receipt_json,receipt_digest) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (run_id, operation_id, request_digest, binding_json, scenario, image_id,
                 deadline, timeout, container_name, token, None, "INTENT", None, None),
            )
            row = db.execute(
                "SELECT * FROM executions WHERE run_id=? AND operation_id=?",
                (run_id, operation_id),
            ).fetchone()
            current, receipt = self._verify_row(row)
            return self._output(current, receipt, new=True)

    def advance(self, run_id: str, operation_id: str, owner_token: str, state: str, *,
                container_id: str | None = None) -> dict[str, Any]:
        require_id(run_id)
        require_id(operation_id)
        if type(owner_token) is not str or not _TOKEN.fullmatch(owner_token):
            raise ContractError("INVALID_OWNER_TOKEN")
        if state not in _STATES[:-1]:
            raise ContractError("INVALID_STATE")
        _require_container_id(container_id, allow_none=True)
        with self._transaction() as db:
            row = db.execute(
                "SELECT * FROM executions WHERE run_id=? AND operation_id=?",
                (run_id, operation_id),
            ).fetchone()
            if row is None:
                raise JournalError("NOT_FOUND")
            current, receipt = self._verify_row(row)
            if current["owner_token"] != owner_token:
                raise JournalError("OWNER_MISMATCH")
            if current["state"] == "FINISHED":
                raise JournalError("ALREADY_FINISHED")
            current_index = _ADVANCE[current["state"]]
            target_index = _ADVANCE[state]
            direct_stop = state == "STOPPED" and current["state"] in {"CREATED", "STARTING", "RUNNING"}
            if target_index < current_index or (target_index > current_index + 1 and not direct_stop):
                raise JournalError("INVALID_TRANSITION")
            if target_index == current_index:
                if container_id != current["container_id"]:
                    raise JournalError("STATE_CONFLICT")
                return self._output(current, receipt)
            if state == "INTENT":
                raise JournalError("INVALID_TRANSITION")
            if container_id is None:
                raise ContractError("CONTAINER_ID_REQUIRED")
            if current["container_id"] is not None and current["container_id"] != container_id:
                raise JournalError("CONTAINER_CONFLICT")
            db.execute(
                "UPDATE executions SET state=?, container_id=? WHERE run_id=? AND operation_id=?",
                (state, container_id, run_id, operation_id),
            )
            row = db.execute(
                "SELECT * FROM executions WHERE run_id=? AND operation_id=?",
                (run_id, operation_id),
            ).fetchone()
            current, receipt = self._verify_row(row)
            return self._output(current, receipt)

    def finish(self, run_id: str, operation_id: str, owner_token: str, receipt: Any) -> dict[str, Any]:
        require_id(run_id)
        require_id(operation_id)
        if type(owner_token) is not str or not _TOKEN.fullmatch(owner_token):
            raise ContractError("INVALID_OWNER_TOKEN")
        with self._transaction() as db:
            row = db.execute(
                "SELECT * FROM executions WHERE run_id=? AND operation_id=?",
                (run_id, operation_id),
            ).fetchone()
            if row is None:
                raise JournalError("NOT_FOUND")
            current, previous_receipt = self._verify_row(row)
            if current["owner_token"] != owner_token:
                raise JournalError("OWNER_MISMATCH")
            checked, payload, digest = _validate_receipt(
                receipt, binding=current["binding"], scenario=current["scenario"],
                image_id=current["image_id"], container_id=current["container_id"]
            )
            if not checked["stop_confirmed"] or not checked["cleanup_confirmed"]:
                raise ContractError("INVALID_RECEIPT")
            if current["state"] == "FINISHED":
                if previous_receipt != checked:
                    raise JournalError("RECEIPT_CONFLICT")
                return self._output(current, previous_receipt)
            if current["container_id"] is not None and current["state"] != "STOPPED":
                raise JournalError("INVALID_STATE")
            db.execute(
                "UPDATE executions SET state='FINISHED', receipt_json=?, receipt_digest=? WHERE run_id=? AND operation_id=?",
                (payload.decode("utf-8"), digest, run_id, operation_id),
            )
            row = db.execute(
                "SELECT * FROM executions WHERE run_id=? AND operation_id=?",
                (run_id, operation_id),
            ).fetchone()
            current, checked = self._verify_row(row)
            return self._output(current, checked)

    def get(self, run_id: str, operation_id: str) -> dict[str, Any]:
        require_id(run_id)
        require_id(operation_id)
        with self._transaction() as db:
            row = db.execute(
                "SELECT * FROM executions WHERE run_id=? AND operation_id=?",
                (run_id, operation_id),
            ).fetchone()
            if row is None:
                raise JournalError("NOT_FOUND")
            current, receipt = self._verify_row(row)
            return self._output(current, receipt)

    def pending(self) -> list[dict[str, Any]]:
        with self._transaction() as db:
            rows = db.execute(
                "SELECT * FROM executions ORDER BY run_id, operation_id"
            ).fetchall()
            result = []
            for row in rows:
                current, receipt = self._verify_row(row)
                if current["state"] != "FINISHED":
                    result.append(self._output(current, receipt))
            return result


__all__ = ["ExecutionJournal", "JournalError", "MAX_RECEIPT_BYTES"]
