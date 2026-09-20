"""拡張の補助契約とローカルartifact。認証・採択・製品CIは発行しない。"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
import time
from typing import Callable

from .bounded_files import BoundedFileError, write_bounded
from .contracts import (ContractError, MAX_DOCUMENT_BYTES, decode_document,
                        require_id, require_object, require_ref, require_uint)
from .wire import canonical_bytes

COMMANDS = frozenset({
    "ops.doctor", "ops.setup.preview", "ops.setup.apply", "ops.bundle.create",
    "ops.retention.plan", "ops.retention.apply", "ops.migrate.preview", "ops.migrate.apply",
    "benchmark.plan", "benchmark.measure", "benchmark.compare", "pilot.register",
    "pilot.plan", "pilot.execute", "pilot.status", "pilot.report", "pilot.import",
    "ops.invalid", "benchmark.invalid", "pilot.invalid",
})
EXIT_CODES = {"COMPLETED": 0, "REJECTED": 1, "INCOMPLETE": 2, "CANCELLED": 3}
REASONS = frozenset({
    "INVALID_INPUT", "IO_ERROR", "OBSERVATION_MISSING", "UNSUPPORTED_CAPABILITY",
    "AUTHORITY_REQUIRED", "IDEMPOTENCY_CONFLICT", "PLAN_EXPIRED", "BINDING_MISMATCH",
    "SLO_FAILED", "OPERATION_CANCELLED", "CLOCK_ROLLBACK", "CLOCK_UNAVAILABLE",
    "DOCKER_UNAVAILABLE", "PYTHON_UNSUPPORTED", "PLATFORM_UNSUPPORTED", "DISK_INSUFFICIENT",
    "IDENTITY_UNAVAILABLE", "RUNTIME_UNAVAILABLE", "SCHEMA_UNSUPPORTED", "IMAGE_UNAVAILABLE",
    "STOP_UNCONFIRMED", "SETTLEMENT_PENDING", "EVIDENCE_UNAVAILABLE", "OPERATION_UNKNOWN",
    "DATA_INCOMPLETE", "CONDITION_MISMATCH", "CAPACITY_EXCEEDED", "PATH_REJECTED",
    "REFERENCE_MISMATCH", "RESULT_CONFLICT", "NOT_STARTED",
    "AUTHORITY_DENIED", "AUTHORITY_REVOKED", "STALE_OR_INVALIDATED",
    "RETENTION_NOT_EXPIRED", "RETENTION_HOLD", "RETENTION_STATE_CONFLICT", "RETENTION_PLAN_INVALID",
})
PLAN_FIELDS = {"schema_version", "kind", "id", "requirement_ids", "source_ref",
               "requirements_ref", "created_at", "expires_at", "payload"}
RESULT_FIELDS = {"schema_version", "kind", "command", "request_id", "operation_status",
                 "checked_at", "result_ref", "reasons", "ci_eligible", "exit_code"}
ACCEPTANCE_FIELDS = {"schema_version", "kind", "id", "requirement_id", "acceptance_id",
                     "plan_ref", "source_ref", "evidence_refs", "status", "reasons", "checked_at"}
REQUIREMENTS = frozenset(f"GAH-PR{i:02d}" for i in range(1, 15))


def _checked(value: dict) -> dict:
    """メモリ上の呼出しにもwireの深度・数値・容量制限を適用し、aliasを切る。"""
    try:
        return decode_document(canonical_bytes(value))
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise ContractError("INVALID_INPUT") from None


def _version(value: dict) -> None:
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ContractError("INVALID_INPUT")


def _reasons(value: object, *, required: bool) -> None:
    if (type(value) is not list or any(type(item) is not str or item not in REASONS for item in value)
            or len(value) != len(set(value)) or (required and not value)):
        raise ContractError("INVALID_INPUT")


def content_ref(kind: str, identifier: str, value: dict) -> dict:
    require_id(kind)
    require_id(identifier)
    document = _checked(value)
    return {"kind": kind, "id": identifier,
            "digest": hashlib.sha256(canonical_bytes(document)).hexdigest()}


def validate_operation_result(value: dict) -> dict:
    value = _checked(value)
    require_object(value, RESULT_FIELDS)
    _version(value)
    if (value["kind"] != "extension_operation_result"
            or type(value["command"]) is not str or value["command"] not in COMMANDS
            or type(value["operation_status"]) is not str or value["operation_status"] not in EXIT_CODES
            or value["ci_eligible"] is not False):
        raise ContractError("INVALID_INPUT")
    if value["request_id"] is not None:
        require_id(value["request_id"])
    require_uint(value["checked_at"])
    if value["result_ref"] is not None:
        require_ref(value["result_ref"])
    code = value["exit_code"]
    if type(code) is not int or code != EXIT_CODES[value["operation_status"]]:
        raise ContractError("INVALID_INPUT")
    _reasons(value["reasons"], required=value["operation_status"] != "COMPLETED")
    if value["request_id"] is None and value["operation_status"] == "COMPLETED":
        raise ContractError("INVALID_INPUT")
    if value["command"].endswith(".invalid") and (
            value["operation_status"] != "REJECTED" or value["result_ref"] is not None
            or value["reasons"] != ["INVALID_INPUT"]):
        raise ContractError("INVALID_INPUT")
    return value


def operation_result(command: str, request_id: str | None, status: str, *,
                     result_ref: dict | None = None, reasons=(), checked_at: int | None = None) -> dict:
    if type(status) is not str or status not in EXIT_CODES:
        raise ContractError("INVALID_INPUT")
    return validate_operation_result({
        "schema_version": 1, "kind": "extension_operation_result", "command": command,
        "request_id": request_id, "operation_status": status,
        "checked_at": int(time.time()) if checked_at is None else checked_at,
        "result_ref": result_ref, "reasons": list(reasons), "ci_eligible": False,
        "exit_code": EXIT_CODES[status],
    })


def validate_plan(value: dict, *, kind: str, payload_validator: Callable[[dict], dict | None],
                  now: int | None = None) -> dict:
    """kindとpayload validatorは呼出側の固定dispatchから渡す。採択を証明しない。"""
    value = _checked(value)
    require_object(value, PLAN_FIELDS)
    _version(value)
    require_id(kind)
    if value["kind"] != kind:
        raise ContractError("INVALID_INPUT")
    require_id(value["id"])
    ids = value["requirement_ids"]
    if (type(ids) is not list or not ids
            or any(type(item) is not str or item not in REQUIREMENTS for item in ids)
            or len(ids) != len(set(ids))):
        raise ContractError("INVALID_INPUT")
    for name in ("source_ref", "requirements_ref"):
        require_ref(value[name])
        if value[name]["kind"] != "snapshot_manifest":
            raise ContractError("BINDING_MISMATCH")
    for name in ("created_at", "expires_at"):
        require_uint(value[name])
    if value["expires_at"] <= value["created_at"]:
        raise ContractError("INVALID_INPUT")
    if now is not None:
        require_uint(now)
        if not value["created_at"] <= now < value["expires_at"]:
            raise ContractError("PLAN_EXPIRED")
    if type(value["payload"]) is not dict:
        raise ContractError("INVALID_INPUT")
    payload_validator(value["payload"])
    return value


def validate_acceptance_record(value: dict) -> dict:
    value = _checked(value)
    require_object(value, ACCEPTANCE_FIELDS)
    _version(value)
    require_id(value["id"])
    if (value["kind"] != "productization_acceptance_record"
            or type(value["requirement_id"]) is not str or value["requirement_id"] not in REQUIREMENTS
            or value["acceptance_id"] != "GAH-PAC" + value["requirement_id"][-2:]
            or type(value["status"]) is not str
            or value["status"] not in {"NOT_RUN", "PASS", "FAIL", "INCONCLUSIVE"}):
        raise ContractError("INVALID_INPUT")
    require_ref(value["source_ref"])
    if value["source_ref"]["kind"] != "snapshot_manifest":
        raise ContractError("BINDING_MISMATCH")
    if value["plan_ref"] is not None:
        require_ref(value["plan_ref"])
    elif value["status"] != "NOT_RUN":
        raise ContractError("INVALID_INPUT")
    refs = value["evidence_refs"]
    if type(refs) is not list or len(refs) > 1000:
        raise ContractError("INVALID_INPUT")
    for ref in refs:
        require_ref(ref)
    if len({(r["kind"], r["id"], r["digest"]) for r in refs}) != len(refs):
        raise ContractError("INVALID_INPUT")
    if (value["status"] == "NOT_RUN" and refs) or (value["status"] == "PASS" and not refs):
        raise ContractError("INVALID_INPUT")
    _reasons(value["reasons"], required=value["status"] in {"FAIL", "INCONCLUSIVE"})
    require_uint(value["checked_at"])
    return value


def workspace_path(workspace: str | Path, path: str | Path) -> Path:
    """管理下workspaceで使用する。OSのアクセス制御・認証境界の代替ではない。"""
    try:
        base = Path(workspace).absolute()
        target = Path(path)
        if ".." in target.parts:
            raise ContractError("PATH_REJECTED")
        target = target.absolute() if target.is_absolute() else base / target
        if not base.is_dir() or not target.is_relative_to(base) or target == base:
            raise ContractError("PATH_REJECTED")
        # Windows junctionも含めて各構成要素を確認する。
        for part in (*reversed(target.parents), target):
            try:
                info = part.lstat()
            except FileNotFoundError:
                continue
            if (stat.S_ISLNK(info.st_mode)
                    or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)):
                raise ContractError("PATH_REJECTED")
        if not target.resolve().is_relative_to(base.resolve()):
            raise ContractError("PATH_REJECTED")
        return target
    except OSError:
        raise ContractError("IO_ERROR") from None


def read_document(workspace: str | Path, path: str | Path) -> dict:
    target = workspace_path(workspace, path)
    try:
        with target.open("rb") as source:
            return decode_document(source.read(MAX_DOCUMENT_BYTES + 1))
    except OSError:
        raise ContractError("IO_ERROR") from None


def write_document(workspace: str | Path, path: str | Path, value: dict) -> dict:
    """閉じた専用validatorを呼出側で実行後に、容量制限付きで不変保存する。"""
    value = _checked(value)
    require_id(value.get("kind"))
    require_id(value.get("id"))
    ref = content_ref(value["kind"], value["id"], value)
    raw = canonical_bytes(value)
    target = workspace_path(workspace, path)
    if not target.parent.is_dir():
        raise ContractError("IO_ERROR")
    try:
        write_bounded(target, raw, immutable=True)
    except BoundedFileError as error:
        if error.code == "RESULT_CONFLICT":
            raise ContractError("RESULT_CONFLICT") from None
        if error.code == "CAPACITY_EXCEEDED":
            raise ContractError("CAPACITY_EXCEEDED") from None
        if error.code == "PATH_REJECTED":
            raise ContractError("PATH_REJECTED") from None
        raise ContractError("IO_ERROR") from None
    return ref
