"""拡張運用の診断・導入計画・bundle・保持・移行の境界。

このモジュールは、既存の ``AuthorityRuntime`` と broker の action を薄く
接続する。補助操作の完了を製品 CI の成功へ昇格させず、認証できない外部
状態は不明のまま ``INCOMPLETE`` として返す。authority の採択表を直接開い
たり、要求本文の role を認証根拠にしたりしない。
"""

from __future__ import annotations

from copy import deepcopy
import ctypes
import re
import subprocess
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import sqlite3
import sys
import time
import uuid
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import quote

from .contracts import (
    ContractError,
    MAX_DOCUMENT_BYTES,
    MAX_INTEGER,
    decode_document,
    require_digest,
    require_id,
    require_ref,
    require_uint,
)
from .productization import (
    COMMANDS,
    REASONS,
    content_ref,
    operation_result,
    read_document,
    validate_operation_result,
    validate_plan,
    workspace_path,
    write_document,
)
from .wire import canonical_bytes
from .productization_journal import OperationJournal


ROOT = Path(__file__).resolve().parents[2]

# 運用仕様の上限。テストや将来の配布側が値を固定して参照できるよう、
# magic number を操作コードへ散らさない。
BUNDLE_MAX_BYTES = 64 * 1024 * 1024
BUNDLE_MAX_ENTRIES = 256
BUNDLE_CHUNK_BYTES = 1024 * 1024
CACHE_MAX_BYTES = 16 * 1024 * 1024
CACHE_MAX_ENTRIES = 32
PLAN_TTL_SECONDS = 24 * 60 * 60
DOCTOR_MAX_SECONDS = 300
CLOCK_OBSERVATION_SECONDS = 120
MIN_FREE_BYTES = 256 * 1024 * 1024
DOCKER_PROBE_TIMEOUT_SECONDS = 5
WSL_PROBE_TIMEOUT_SECONDS = 5
REF_ARTIFACT_ROOT = Path(".ga") / "operations" / "refs"
CAPACITY_PROFILE_FIELDS = {
    "profile_ref", "remaining_write_upper_bound_bytes", "reserved_bytes",
    "bound_verified",
}
RUNTIME_METADATA_NAMES = (
    "runtime-metadata.json", "runtime_metadata.json", "setup-metadata.json",
)

SETUP_PAYLOAD_FIELDS = {
    "setup_id", "profile", "workspace_path", "platform", "existing_runtime_ref",
    "image_lock_refs", "config_ref", "role_recipe_ref", "resource_profile_ref",
    "sample_contract_ref", "sample_case_set_ref", "evaluator_ref", "output_paths",
    "actions",
}
SETUP_ACTIONS = (
    "image_prepare", "authority_prepare", "contract_validate", "contract_adopt",
    "baseline_run", "baseline_adopt", "contract_transition", "ready_check",
    "requests_write",
)
SETUP_PROFILES = {"sample-ci", "sample-llm"}
SETUP_PLAN_KIND = "setup_plan"
DOCTOR_KIND = "doctor_result"
STAGING_KIND = "setup_staging"
BUNDLE_KIND = "diagnostic_bundle"
RETENTION_RESULT_KIND = "retention_operation_result"
MIGRATION_PLAN_KIND = "migration_plan"
MIGRATION_RESULT_KIND = "migration_operation_result"

DOCTOR_PHASES = {"bootstrap", "ready"}
DOCTOR_CHECK_FIELDS = {
    "check_id", "required", "observed_elapsed_ns", "observed_at_utc_s",
    "status", "reason_code", "affected_scope", "remediation_ref",
}
DOCTOR_STATUSES = {"PASS", "FAIL", "UNKNOWN", "NOT_APPLICABLE"}
DOCTOR_CHECK_IDS = {
    "workspace", "python", "platform", "docker", "wsl2", "clock", "capacity",
    "identity", "runtime", "image_lock", "authority", "binding", "database",
}
DOCTOR_RESULT_FIELDS = {
    "schema_version", "kind", "id", "phase", "workspace_path",
    "started_at_utc_s", "finished_at_utc_s", "started_monotonic_ns",
    "finished_monotonic_ns", "checks", "limits", "runtime_ref",
    "observations",
}

MIGRATION_PLAN_FIELDS = {
    "schema_version", "kind", "id", "source_path", "source_ref",
    "source_schema_version", "target_schema_version", "table_counts",
    "unsettled_runs", "write_stop_required", "snapshot_required",
    "allowed_scope", "source_size_bytes", "source_digest",
}

_AUTHORITY_REASONS = {
    "AUTHORITY_MISSING", "AUTHENTICATION_REQUIRED", "AUTHORITY_DENIED",
    "AUTHORITY_REVOKED", "PEER_AUTH_UNAVAILABLE", "BROKER_IDENTITY_MISMATCH",
    "IDENTITY_NOT_CONFIGURED", "PERMISSION_MISMATCH",
}
_KNOWN_REJECTION_REASONS = {
    "RETENTION_HOLD", "RETENTION_STATE_CONFLICT", "RETENTION_PLAN_INVALID",
    "RETENTION_NOT_EXPIRED", "EVIDENCE_DELETED", "EVIDENCE_REVOKED",
    "BINDING_MISMATCH", "EVIDENCE_BINDING_MISMATCH", "PLAN_EXPIRED",
    "REQUEST_CONFLICT", "PROPOSAL_CONFLICT", "GENERATION_CONFLICT",
    "VALIDATION_REVOKED", "CONTRACT_INVALID", "BASELINE_INVALID",
    "SCHEMA_UNSUPPORTED", "UNSUPPORTED_STORE", "CONFIG_MISMATCH",
}
_INCOMPLETE_REASONS = {
    "DOCKER_UNAVAILABLE", "DOCKER_COMMAND_FAILED", "BROKER_NOT_READY",
    "CLIENT_FAILED", "CLIENT_RESPONSE_INVALID", "REQUEST_TIMEOUT",
    "INCOMPLETE_FRAME", "TRANSPORT_FAILURE", "CLOCK_UNAVAILABLE",
    "STORAGE_FAILURE", "STORAGE_CORRUPT", "EVIDENCE_MISSING", "RUN_MISSING",
    "STOP_UNCONFIRMED", "BUDGET_OPEN", "MIGRATION_FAILED", "OUTPUT_WRITE_FAILED",
}

# 仕様の分冊で先に定義された語と、共通契約の初版が短縮している語を
# 境界で吸収する。共通契約が後から全コードを公開した場合は元の語を使う。
_REASON_FALLBACK = {
    "CHECK_UNAVAILABLE": "OBSERVATION_MISSING",
    "CLOCK_UNSTABLE": "CLOCK_UNAVAILABLE",
    "CLOCK_ROLLBACK": "CLOCK_ROLLBACK",
    "RUNTIME_MISSING": "RUNTIME_UNAVAILABLE",
    "IMAGE_MISSING": "IMAGE_UNAVAILABLE",
    "IMAGE_MISMATCH": "BINDING_MISMATCH",
    "PLATFORM_UNSUPPORTED": "UNSUPPORTED_CAPABILITY",
    "PERMISSION_MISMATCH": "AUTHORITY_REQUIRED",
    "RETENTION_HOLD": "BINDING_MISMATCH",
    "BUNDLE_REDACTION_REQUIRED": "INVALID_INPUT",
    "OUTPUT_WRITE_FAILED": "IO_ERROR",
    "MIGRATION_UNSUPPORTED": "SCHEMA_UNSUPPORTED",
    "MIGRATION_ROLLED_BACK": "IO_ERROR",
    "PLAN_STALE": "BINDING_MISMATCH",
    "STALE_PLAN": "BINDING_MISMATCH",
    "BINDING_STALE": "BINDING_MISMATCH",
    "STORAGE_LOW": "CAPACITY_EXCEEDED",
    "RESOURCE_LIMIT": "CAPACITY_EXCEEDED",
}


class OperationsError(ValueError):
    """運用操作の公開境界で利用する固定エラー。"""

    def __init__(self, code: str, *, detail: Any = None):
        self.code = code
        self.detail = detail
        super().__init__(code)


def _error(code: str, *, detail: Any = None) -> OperationsError:
    return OperationsError(code, detail=detail)


def _public_reason(code: Any) -> str:
    if type(code) is str and code in REASONS:
        return code
    if type(code) is str and code in _REASON_FALLBACK:
        candidate = _REASON_FALLBACK[code]
        if candidate in REASONS:
            return candidate
    return "IO_ERROR"


def _reasons(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        reason = _public_reason(value)
        if reason not in result:
            result.append(reason)
    return result


def _operation(command: str, request_id: str | None, status: str, *,
               result_ref: dict | None = None, reasons: Iterable[str] = (),
               checked_at: int | None = None) -> dict:
    try:
        return operation_result(command, request_id, status,
                                result_ref=result_ref, reasons=_reasons(reasons),
                                checked_at=checked_at)
    except (ContractError, TypeError, ValueError) as exc:
        # 内部で作る応答まで厳密契約を通し、未対応の例外本文を外へ出さない。
        raise _error("INVALID_INPUT") from exc


def _valid_id(value: Any) -> bool:
    try:
        require_id(value)
    except ContractError:
        return False
    return True


def _new_id(prefix: str, value: Any = None) -> str:
    if value is None:
        suffix = uuid.uuid4().hex
    else:
        suffix = hashlib.sha256(canonical_bytes(value)).hexdigest()[:40]
    identifier = prefix + "-" + suffix
    if not _valid_id(identifier):
        raise _error("INVALID_INPUT")
    return identifier


def _now(clock: Callable[[], Any] | None = None) -> int:
    try:
        value = int(time.time()) if clock is None else clock()
    except Exception as exc:
        raise _error("CLOCK_UNAVAILABLE") from exc
    if type(value) is not int or not 0 <= value <= MAX_INTEGER:
        raise _error("CLOCK_UNAVAILABLE")
    return value


def _monotonic_ns(clock: Callable[[], Any] | None = None) -> int:
    try:
        value = time.monotonic_ns() if clock is None else clock()
    except Exception as exc:
        raise _error("CLOCK_UNAVAILABLE") from exc
    if type(value) is not int or value < 0 or value > MAX_INTEGER:
        raise _error("CLOCK_UNAVAILABLE")
    return value


def _plain(path: str | Path) -> Path:
    """既存の構成要素に symlink/reparse がない解決済みpathを返す。"""
    try:
        target = Path(path).absolute()
        for item in (target, *target.parents):
            if not (item.exists() or item.is_symlink()):
                continue
            info = item.lstat()
            if info.st_mode & 0o170000 == 0o120000:
                raise _error("PATH_REJECTED")
            if getattr(info, "st_file_attributes", 0) & 0x400:
                raise _error("PATH_REJECTED")
        return target
    except OperationsError:
        raise
    except (OSError, ValueError, TypeError) as exc:
        raise _error("PATH_REJECTED") from exc


def _workspace(workspace: str | Path) -> Path:
    base = _plain(workspace)
    if not base.is_dir():
        raise _error("RUNTIME_UNAVAILABLE")
    return base


def _child(base: Path, value: str | Path) -> Path:
    try:
        return workspace_path(base, value)
    except (ContractError, TypeError, ValueError) as exc:
        raise _error(_public_reason(getattr(exc, "code", "PATH_REJECTED"))) from exc


def _ensure_parent(base: Path, target: str | Path) -> Path:
    path = _child(base, target)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _plain(path.parent)
    except OperationsError:
        raise
    except OSError as exc:
        raise _error("IO_ERROR") from exc
    return path


def _load_json_file(path: Path) -> dict:
    try:
        with path.open("rb") as stream:
            return decode_document(stream.read(MAX_DOCUMENT_BYTES + 1))
    except (OSError, ContractError, UnicodeError, ValueError) as exc:
        raise _error("IO_ERROR") from exc


def _save_artifact(base: Path, relative: str | Path, value: dict,
                   validator: Callable[[dict], dict] | None = None) -> dict:
    if validator is not None:
        try:
            validator(value)
        except OperationsError:
            raise
        except (ContractError, TypeError, ValueError, KeyError) as exc:
            raise _error(_public_reason(getattr(exc, "code", "INVALID_INPUT"))) from exc
    _ensure_parent(base, relative)
    try:
        return write_document(base, relative, value)
    except (ContractError, TypeError, ValueError) as exc:
        raise _error(_public_reason(getattr(exc, "code", "IO_ERROR"))) from exc


def _request_id(value: Any, prefix: str) -> str | None:
    if value is None:
        return _new_id(prefix)
    return value if _valid_id(value) else None


def _ref_kind(value: Any, kind: str) -> None:
    try:
        require_ref(value)
    except ContractError as exc:
        raise _error("INVALID_INPUT") from exc
    if value["kind"] != kind:
        raise _error("BINDING_MISMATCH")


def _unique_refs(value: Any, kind: str, *, minimum: int = 1, maximum: int = 8) -> None:
    if type(value) is not list or not minimum <= len(value) <= maximum:
        raise _error("INVALID_INPUT")
    seen: set[tuple[str, str, str]] = set()
    for item in value:
        _ref_kind(item, kind)
        key = (item["kind"], item["id"], item["digest"])
        if key in seen:
            raise _error("INVALID_INPUT")
        seen.add(key)


def _snapshot_document(identifier: str, files: Iterable[str], *,
                       purpose: str) -> dict:
    entries = []
    for name in files:
        path = ROOT / name
        try:
            if path.is_symlink() or not path.is_file():
                raise OSError
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            size = path.stat().st_size
        except OSError as exc:
            raise _error("IO_ERROR") from exc
        entries.append({"path": name, "digest": digest, "size_bytes": size})
    return {
        "schema_version": 1, "kind": "snapshot_manifest", "id": identifier,
        "purpose": purpose, "files": entries,
    }


def _snapshot_ref(identifier: str, files: Iterable[str], *,
                  purpose: str) -> dict:
    return content_ref(
        "snapshot_manifest", identifier,
        _snapshot_document(identifier, files, purpose=purpose),
    )


def _ref_artifact_path(ref: Mapping[str, Any]) -> Path:
    try:
        require_ref(ref)
    except ContractError as exc:
        raise _error("INVALID_INPUT") from exc
    # kind/idだけでは同一IDの内容更新で衝突するため、完全refのdigestを保存pathへ含める。
    # idとdigestは require_ref 済みなので separator や親参照を含まない。
    return REF_ARTIFACT_ROOT / ref["kind"] / ref["id"] / (ref["digest"] + ".json")


def _persist_setup_ref(base: Path, ref: dict, value: dict | None,
                       *, resolver: dict | None = None) -> dict:
    """preview が発行した参照を apply が再解決できる管理 artifact として保存する。"""
    try:
        require_ref(ref)
    except ContractError as exc:
        raise _error("INVALID_INPUT") from exc
    if resolver is None:
        if value is None:
            raise _error("INVALID_INPUT")
        try:
            expected = content_ref(ref["kind"], ref["id"], value)
        except (ContractError, TypeError, ValueError) as exc:
            raise _error("INVALID_INPUT") from exc
        if expected != ref:
            raise _error("REFERENCE_MISMATCH")
    artifact = {
        "schema_version": 1,
        "kind": "setup_ref_artifact",
        "id": ref["id"],
        "ref": deepcopy(ref),
        "value": None if resolver is not None else deepcopy(value),
        "resolver": None if resolver is None else deepcopy(resolver),
        "ci_eligible": False,
    }
    _save_artifact(base, _ref_artifact_path(ref), artifact)
    return ref


def resolve_setup_ref(workspace: str | Path, ref: Mapping[str, Any]) -> dict:
    """setup preview が保存した ref 本文をdigest照合して読む。"""
    base = _workspace(workspace)
    path = _ref_artifact_path(ref)
    artifact = _load_json_file(_child(base, path))
    fields = {
        "schema_version", "kind", "id", "ref", "value", "resolver",
        "ci_eligible",
    }
    if (type(artifact) is not dict or set(artifact) != fields
            or artifact.get("schema_version") != 1
            or artifact.get("kind") != "setup_ref_artifact"
            or artifact.get("id") != ref.get("id")
            or artifact.get("ref") != dict(ref)
            or artifact.get("ci_eligible") is not False):
        raise _error("REFERENCE_MISMATCH")
    value = artifact.get("value")
    resolver = artifact.get("resolver")
    if resolver is not None:
        if type(resolver) is not dict or value is not None:
            raise _error("REFERENCE_MISMATCH")
        return artifact
    if type(value) is not dict:
        raise _error("REFERENCE_MISMATCH")
    try:
        if content_ref(ref["kind"], ref["id"], value) != dict(ref):
            raise _error("REFERENCE_MISMATCH")
    except (ContractError, TypeError, ValueError) as exc:
        raise _error("REFERENCE_MISMATCH") from exc
    return value

def validate_setup_payload(value: dict) -> dict:
    """setup_plan の閉じた payload validator。"""
    if type(value) is not dict or set(value) != SETUP_PAYLOAD_FIELDS:
        raise ContractError("INVALID_INPUT")
    if not _valid_id(value["setup_id"]):
        raise ContractError("INVALID_INPUT")
    if type(value["profile"]) is not str or value["profile"] not in SETUP_PROFILES:
        raise ContractError("INVALID_INPUT")
    if type(value["workspace_path"]) is not str or not value["workspace_path"]:
        raise ContractError("INVALID_INPUT")
    if not os.path.isabs(value["workspace_path"]):
        raise ContractError("PATH_REJECTED")
    if value["platform"] not in {"linux-x86_64", "windows-wsl2-x86_64"}:
        raise ContractError("UNSUPPORTED_CAPABILITY")
    if value["existing_runtime_ref"] is not None:
        _ref_kind(value["existing_runtime_ref"], "runtime_deployment")
    _unique_refs(value["image_lock_refs"], "image_lock", maximum=8)
    for name, kind in (("config_ref", "setup_config"),
                       ("role_recipe_ref", "role_recipe"),
                       ("resource_profile_ref", "resource_profile"),
                       ("sample_contract_ref", "evaluation_contract"),
                       ("sample_case_set_ref", "case_set"),
                       ("evaluator_ref", "evaluator")):
        _ref_kind(value[name], kind)
    paths = value["output_paths"]
    if type(paths) is not dict or set(paths) != {"runtime", "run_request", "ci_request"}:
        raise ContractError("INVALID_INPUT")
    for path in paths.values():
        if type(path) is not str or not os.path.isabs(path) or ".." in Path(path).parts:
            raise ContractError("PATH_REJECTED")
    if type(value["actions"]) is not list or tuple(value["actions"]) != SETUP_ACTIONS:
        raise ContractError("INVALID_INPUT")
    return value


def validate_doctor_result(value: dict) -> dict:
    if type(value) is not dict or set(value) != DOCTOR_RESULT_FIELDS:
        raise ContractError("INVALID_INPUT")
    if (type(value["schema_version"]) is not int or value["schema_version"] != 1
            or value["kind"] != DOCTOR_KIND or not _valid_id(value["id"])):
        raise ContractError("INVALID_INPUT")
    if value["phase"] not in DOCTOR_PHASES or type(value["workspace_path"]) is not str:
        raise ContractError("INVALID_INPUT")
    for name in ("started_at_utc_s", "finished_at_utc_s", "started_monotonic_ns", "finished_monotonic_ns"):
        if type(value[name]) is not int or not 0 <= value[name] <= MAX_INTEGER:
            raise ContractError("INVALID_INPUT")
    if value["finished_at_utc_s"] < value["started_at_utc_s"] or value["finished_monotonic_ns"] < value["started_monotonic_ns"]:
        raise ContractError("INVALID_INPUT")
    checks = value["checks"]
    if type(checks) is not list or len(checks) != len(DOCTOR_CHECK_IDS):
        raise ContractError("INVALID_INPUT")
    seen: set[str] = set()
    for check in checks:
        if type(check) is not dict or set(check) != DOCTOR_CHECK_FIELDS:
            raise ContractError("INVALID_INPUT")
        if check["check_id"] not in DOCTOR_CHECK_IDS or check["check_id"] in seen:
            raise ContractError("INVALID_INPUT")
        seen.add(check["check_id"])
        if type(check["required"]) is not bool or type(check["observed_elapsed_ns"]) is not int or check["observed_elapsed_ns"] < 0:
            raise ContractError("INVALID_INPUT")
        if type(check["observed_at_utc_s"]) is not int or not 0 <= check["observed_at_utc_s"] <= MAX_INTEGER:
            raise ContractError("INVALID_INPUT")
        if check["status"] not in DOCTOR_STATUSES:
            raise ContractError("INVALID_INPUT")
        if check["reason_code"] is not None and check["reason_code"] not in REASONS:
            raise ContractError("INVALID_INPUT")
        scope = check["affected_scope"]
        if type(scope) is not list or any(type(item) is not str or not item for item in scope):
            raise ContractError("INVALID_INPUT")
        if check["remediation_ref"] is not None:
            require_ref(check["remediation_ref"])
    limits = value["limits"]
    if type(limits) is not dict or set(limits) != {"max_elapsed_seconds", "min_free_bytes", "bundle_max_bytes", "bundle_max_entries"}:
        raise ContractError("INVALID_INPUT")
    for item in limits.values():
        if type(item) is not int or item < 0:
            raise ContractError("INVALID_INPUT")
    if value["runtime_ref"] is not None:
        _ref_kind(value["runtime_ref"], "runtime_deployment")
    if type(value["observations"]) is not dict:
        raise ContractError("INVALID_INPUT")
    if seen != DOCTOR_CHECK_IDS:
        raise ContractError("INVALID_INPUT")
    return value


def _check(check_id: str, required: bool, status: str, reason: str | None,
           scope: Iterable[str], started_ns: int, *, now: int,
           finished_ns: int, remediation_ref: dict | None = None) -> dict:
    if check_id not in DOCTOR_CHECK_IDS or status not in DOCTOR_STATUSES:
        raise _error("INVALID_INPUT")
    return {"check_id": check_id, "required": required,
            "observed_elapsed_ns": max(0, finished_ns - started_ns),
            "observed_at_utc_s": now, "status": status,
            "reason_code": None if reason is None else _public_reason(reason),
            "affected_scope": list(scope), "remediation_ref": remediation_ref}


def _platform_kind() -> str | None:
    system = platform.system()
    machine = platform.machine().lower()
    if machine not in {"x86_64", "amd64", "amd64p64"}:
        return None
    if system == "Linux":
        return "linux-x86_64"
    if system == "Windows":
        return "windows-wsl2-x86_64"
    return None


def _runtime_folder(base: Path, runtime: str | Path | None) -> Path:
    if runtime is None:
        raise _error("RUNTIME_UNAVAILABLE")
    target = _child(base, runtime)
    if not target.is_dir():
        raise _error("RUNTIME_UNAVAILABLE")
    return target


def _deployment(folder: Path) -> tuple[dict, dict]:
    state_path = folder / "deployment.json"
    if not state_path.is_file() or state_path.is_symlink():
        raise _error("RUNTIME_UNAVAILABLE")
    state = _load_json_file(state_path)
    if set(state) != {"prefix", "image_id", "containers"}:
        raise _error("RUNTIME_UNAVAILABLE")
    if not _valid_id(state["prefix"]) or type(state["image_id"]) is not str or type(state["containers"]) is not list:
        raise _error("RUNTIME_UNAVAILABLE")
    if any(type(item) is not str for item in state["containers"]):
        raise _error("RUNTIME_UNAVAILABLE")
    ref = content_ref("runtime_deployment", state["prefix"], state)
    return state, ref


def _authority_call(authority: Any, uid: int, request: dict, *,
                    expected_kind: str = "evaluation_authority_result") -> dict:
    """固定 UID client の生応答を厳密に照合する。role は要求本文から読まない。"""
    if (type(request) is not dict or type(request.get("schema_version")) is not int
            or request.get("schema_version") != 1 or not _valid_id(request.get("request_id"))
            or type(request.get("action")) is not str or not request["action"]):
        raise _error("INVALID_INPUT")
    try:
        client = authority.client if hasattr(authority, "client") else authority
        if not callable(client):
            raise _error("AUTHORITY_REQUIRED")
        result = client(uid, deepcopy(request))
    except OperationsError:
        raise
    except Exception as exc:
        code = getattr(exc, "code", "AUTHORITY_REQUIRED")
        if code in {"AUTHORITY_DENIED", "AUTHORITY_REVOKED"}:
            raise _error(code) from exc
        if code in _AUTHORITY_REASONS:
            raise _error("AUTHORITY_REQUIRED", detail=code) from exc
        raise _error(_public_reason(code)) from exc
    if type(result) is not dict:
        raise _error("OBSERVATION_MISSING")
    if result.get("kind") == "authority_error":
        if (set(result) != {"schema_version", "kind", "reason", "ci_eligible"}
                or result.get("schema_version") != 1
                or result.get("ci_eligible") is not False
                or type(result.get("reason")) is not str
                or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", result["reason"])):
            raise _error("OBSERVATION_MISSING")
        reason = result["reason"]
        if reason in {"AUTHORITY_DENIED", "AUTHORITY_REVOKED"}:
            raise _error("AUTHORITY_DENIED", detail=reason)
        if reason in _AUTHORITY_REASONS:
            raise _error("AUTHORITY_REQUIRED", detail=reason)
        if reason in _KNOWN_REJECTION_REASONS:
            raise _error(_public_reason(reason), detail=reason)
        raise _error("OBSERVATION_MISSING", detail=reason)
    if (type(result.get("schema_version")) is not int or result.get("schema_version") != 1
            or result.get("kind") != expected_kind
            or result.get("request_id") != request["request_id"]
            or result.get("action") != request["action"]
            or result.get("ci_eligible") is not False):
        raise _error("BINDING_MISMATCH")
    return result


def _map_operation_error(error: BaseException) -> tuple[str, str]:
    code = getattr(error, "code", "IO_ERROR")
    detail = getattr(error, "detail", None)
    if detail in {"AUTHORITY_DENIED", "AUTHORITY_REVOKED"} or code in {"AUTHORITY_DENIED", "AUTHORITY_REVOKED"}:
        return "REJECTED", _authority_denial_reason(error)
    if code in _AUTHORITY_REASONS:
        return "INCOMPLETE", "AUTHORITY_REQUIRED"
    if code in _KNOWN_REJECTION_REASONS:
        return "REJECTED", _public_reason(code)
    if code in {"INVALID_INPUT", "PATH_REJECTED", "UNSUPPORTED_CAPABILITY", "PLAN_EXPIRED", "SCHEMA_UNSUPPORTED"}:
        return "REJECTED", _public_reason(code)
    if code in {"OPERATION_CANCELLED"}:
        return "CANCELLED", code
    return "INCOMPLETE", _public_reason(code)


def _authority_denial_reason(error: BaseException) -> str:
    code = getattr(error, "code", None)
    detail = getattr(error, "detail", None)
    return "AUTHORITY_REVOKED" if code == "AUTHORITY_REVOKED" or detail == "AUTHORITY_REVOKED" else "AUTHORITY_DENIED"


def _trusted_principal() -> str | None:
    """OS token/SIDを実UIDまたはGetSystemDirectoryW配下のwhoamiから取得する。"""
    try:
        uid = os.getuid()
    except (AttributeError, OSError):
        uid = None
    if type(uid) is int and uid >= 0:
        return "osuid-" + str(uid)
    if os.name != "nt":
        return None
    try:
        system_directory = _windows_system_directory()
        if system_directory is None:
            return None
        completed = subprocess.run(
            [str(system_directory / "whoami.exe"), "/user", "/fo", "csv", "/nh"],
            check=True, capture_output=True, timeout=5, shell=False)
        raw = completed.stdout
        if type(raw) is str:
            raw = raw.encode("ascii", "ignore")
        if type(raw) is not bytes:
            return None
        match = re.search(rb"(S-[0-9]+(?:-[0-9]+){1,15})", raw)
        if match is None:
            return None
        sid = match.group(1).decode("ascii")
        return "sid-" + hashlib.sha256(sid.encode("ascii")).hexdigest()
    except (OSError, subprocess.SubprocessError, UnicodeError, ValueError):
        return None


def _local_principal() -> str | None:
    """互換名。認証不能時はNoneを返し、変更操作を進めない。"""
    return _trusted_principal()



def _staging_value(setup_id: str, plan_ref: dict, *, state: str,
                   completed_actions: list[str], runtime_ref: dict | None,
                   reason: str | None = None, authority_observation: dict | None = None,
                   requests_written: bool = False, requests: dict | None = None) -> dict:
    value = {"schema_version": 1, "kind": STAGING_KIND, "id": setup_id,
             "setup_plan_ref": deepcopy(plan_ref), "state": state,
             "completed_actions": list(completed_actions), "runtime_ref": deepcopy(runtime_ref),
             "reason": None if reason is None else _public_reason(reason),
             "authority_observation": {} if authority_observation is None else deepcopy(authority_observation),
             "requests_written": requests_written, "requests": {} if requests is None else deepcopy(requests),
             "ci_eligible": False}
    return value


def validate_staging(value: dict) -> dict:
    fields = {"schema_version", "kind", "id", "setup_plan_ref", "state",
              "completed_actions", "runtime_ref", "reason", "authority_observation",
              "requests_written", "requests", "ci_eligible"}
    if type(value) is not dict or set(value) != fields or value.get("schema_version") != 1 or value.get("kind") != STAGING_KIND:
        raise ContractError("INVALID_INPUT")
    if not _valid_id(value["id"]):
        raise ContractError("INVALID_INPUT")
    _ref_kind(value["setup_plan_ref"], SETUP_PLAN_KIND)
    if value["state"] not in {"PREPARED", "CONTRACT_ADOPTED", "BASELINE_PENDING", "READY", "FAILED"}:
        raise ContractError("INVALID_INPUT")
    if type(value["completed_actions"]) is not list or tuple(value["completed_actions"]) != tuple(dict.fromkeys(value["completed_actions"])):
        raise ContractError("INVALID_INPUT")
    if any(item not in SETUP_ACTIONS for item in value["completed_actions"]):
        raise ContractError("INVALID_INPUT")
    if value["runtime_ref"] is not None:
        _ref_kind(value["runtime_ref"], "runtime_deployment")
    if value["reason"] is not None and value["reason"] not in REASONS:
        raise ContractError("INVALID_INPUT")
    if type(value["authority_observation"]) is not dict or type(value["requests_written"]) is not bool or type(value["requests"]) is not dict or value["ci_eligible"] is not False:
        raise ContractError("INVALID_INPUT")
    return value


# --- operations implementation ---
# --- operations implementation ---


def _doctor_check_result(checks: list[dict]) -> tuple[str, list[str]]:
    """check結果から補助操作の状態を決定する。入力順を保持する。"""
    rejected: list[str] = []
    incomplete: list[str] = []
    for item in checks:
        if not item["required"]:
            continue
        if item["status"] == "FAIL":
            reason = item["reason_code"] or "INVALID_INPUT"
            (incomplete if reason in {"IO_ERROR", "OBSERVATION_MISSING",
                                      "CLOCK_UNAVAILABLE", "IDENTITY_UNAVAILABLE"}
             else rejected).append(reason)
        elif item["status"] == "UNKNOWN":
            incomplete.append(item["reason_code"] or "OBSERVATION_MISSING")
    if rejected:
        return "REJECTED", _reasons(rejected)
    if incomplete:
        return "INCOMPLETE", _reasons(incomplete)
    return "COMPLETED", []


def _process_text(value: Any) -> str:
    """固定 subprocess の JSON stdout を UTF-8 strictで検査する。"""
    if type(value) is str:
        return value
    if type(value) is bytes:
        return value.decode("utf-8", "strict")
    raise ValueError("PROCESS_OUTPUT_TYPE")


def _localized_process_text(value: Any) -> str:
    """WSLの固定観測だけ、UTF-8/UTF-16のOSロケール出力を読む。"""
    if type(value) is str:
        return value
    if type(value) is not bytes:
        raise ValueError("PROCESS_OUTPUT_TYPE")
    for encoding in ("utf-8-sig", "utf-16", "utf-16-le", "utf-16-be"):
        try:
            decoded = value.decode(encoding, "strict")
        except UnicodeError:
            continue
        if decoded and "\x00" not in decoded:
            return decoded
    raise UnicodeError("PROCESS_ENCODING")


def _process_elapsed(start_ns: int) -> int:
    try:
        return max(0, time.monotonic_ns() - start_ns)
    except Exception:
        return 0


def _run_fixed_process(argv: tuple[str, ...], *, timeout_seconds: int) -> dict[str, Any]:
    """外部引数を受けず、固定 argv だけを read-only subprocess で実行する。"""
    started_ns = time.monotonic_ns()
    try:
        # bytesで受け、stdoutだけをUTF-8 strict decodeする。stderrは保存・解釈しない。
        completed = subprocess.run(
            list(argv), check=False, capture_output=True,
            timeout=timeout_seconds, shell=False,
        )
    except subprocess.TimeoutExpired:
        return {"status": "UNKNOWN", "reason": "OBSERVATION_MISSING",
                "elapsed_ns": _process_elapsed(started_ns), "stdout": None}
    except FileNotFoundError:
        return {"status": "FAIL", "reason": "DOCKER_UNAVAILABLE",
                "elapsed_ns": _process_elapsed(started_ns), "stdout": None}
    except (OSError, subprocess.SubprocessError, UnicodeError, ValueError):
        return {"status": "UNKNOWN", "reason": "OBSERVATION_MISSING",
                "elapsed_ns": _process_elapsed(started_ns), "stdout": None}
    returncode = getattr(completed, "returncode", None)
    if type(returncode) is not int:
        return {"status": "UNKNOWN", "reason": "OBSERVATION_MISSING",
                "elapsed_ns": _process_elapsed(started_ns), "stdout": None}
    if returncode != 0:
        return {"status": "FAIL", "reason": "DOCKER_UNAVAILABLE",
                "elapsed_ns": _process_elapsed(started_ns), "stdout": None,
                "returncode": returncode}
    return {"status": "PASS", "reason": None,
            "elapsed_ns": _process_elapsed(started_ns),
            "stdout": getattr(completed, "stdout", None), "returncode": returncode}


def _decode_process_json(result: dict[str, Any]) -> dict[str, Any] | None:
    if result.get("status") != "PASS":
        return None
    try:
        raw = _process_text(result.get("stdout")).encode("utf-8")
        value = decode_document(raw)
    except (ContractError, UnicodeError, TypeError, ValueError):
        return None
    return value if type(value) is dict else None


def _docker_endpoint() -> str:
    """既存 AuthorityRuntime と同じ固定 Docker Linux engine endpoint。"""
    return ("npipe:////./pipe/dockerDesktopLinuxEngine"
            if platform.system() == "Windows" else "unix:///var/run/docker.sock")


def _docker_probe(executable: str | None) -> dict[str, Any]:
    """docker version/info を固定形式で読み、Linux engine の実体を確認する。"""
    base = {
        "available": False, "engine_os": None, "server_version": None,
        "kernel_version": None, "endpoint": _docker_endpoint(),
        "version_elapsed_ns": 0, "info_elapsed_ns": 0,
    }
    if type(executable) is not str or not executable:
        return {"status": "FAIL", "reason": "DOCKER_UNAVAILABLE", **base}
    version = _run_fixed_process(
        (executable, "--host", _docker_endpoint(), "version", "--format", "{{json .}}"),
        timeout_seconds=DOCKER_PROBE_TIMEOUT_SECONDS,
    )
    base["version_elapsed_ns"] = version["elapsed_ns"]
    if version["status"] != "PASS":
        return {"status": version["status"], "reason": version["reason"], **base}
    version_value = _decode_process_json(version)
    server = version_value.get("Server") if version_value is not None else None
    if (type(server) is not dict or type(server.get("Version")) is not str
            or not server["Version"].strip()):
        return {"status": "UNKNOWN", "reason": "OBSERVATION_MISSING", **base}
    info = _run_fixed_process(
        (executable, "--host", _docker_endpoint(), "info", "--format", "{{json .}}"),
        timeout_seconds=DOCKER_PROBE_TIMEOUT_SECONDS,
    )
    base["info_elapsed_ns"] = info["elapsed_ns"]
    if info["status"] != "PASS":
        return {"status": info["status"], "reason": info["reason"], **base}
    info_value = _decode_process_json(info)
    if (info_value is None or type(info_value.get("ServerVersion")) is not str
            or not info_value["ServerVersion"].strip()
            or type(info_value.get("OSType")) is not str
            or type(info_value.get("KernelVersion")) is not str
            or not info_value["KernelVersion"].strip()):
        return {"status": "UNKNOWN", "reason": "OBSERVATION_MISSING", **base}
    if info_value["ServerVersion"].strip() != server["Version"].strip():
        return {"status": "FAIL", "reason": "BINDING_MISMATCH", **base}
    engine_os = info_value["OSType"].strip().lower()
    base.update({
        "engine_os": engine_os,
        "server_version": server["Version"].strip(),
        "kernel_version": info_value["KernelVersion"].strip(),
    })
    if engine_os != "linux":
        return {"status": "FAIL", "reason": "UNSUPPORTED_CAPABILITY", **base}
    base["available"] = True
    return {"status": "PASS", "reason": None, **base}


def _wsl_probe(executable: str | None,
               *, docker_kernel_version: str | None = None) -> dict[str, Any]:
    """Windows の WSL backend を Docker kernel と固定read観測で確認する。"""
    if type(executable) is not str or not executable:
        return {
            "status": "FAIL", "reason": "UNSUPPORTED_CAPABILITY",
            "wsl_version": None, "kernel_version": None,
            "docker_kernel_version": docker_kernel_version,
            "docker_wsl2_backend_observed": False,
            "elapsed_ns": 0,
        }
    result = _run_fixed_process(
        (executable, "--version"), timeout_seconds=WSL_PROBE_TIMEOUT_SECONDS)
    base = {
        "wsl_version": None, "kernel_version": None,
        "docker_kernel_version": docker_kernel_version,
        "docker_wsl2_backend_observed": (
            type(docker_kernel_version) is str
            and re.search(r"(?i)(?:microsoft-standard-)?wsl2",
                          docker_kernel_version) is not None
        ),
        "elapsed_ns": result["elapsed_ns"],
    }
    if result["status"] == "UNKNOWN":
        return {"status": "UNKNOWN", "reason": "OBSERVATION_MISSING", **base}
    if result["status"] == "FAIL":
        # WSL packageのread不能は、Docker kernelの実測結果とは別に残す。
        if base["docker_wsl2_backend_observed"]:
            return {"status": "PASS", "reason": None, **base}
        return {"status": "FAIL", "reason": "UNSUPPORTED_CAPABILITY", **base}
    try:
        lines = _localized_process_text(result.get("stdout")).splitlines()
    except (UnicodeError, TypeError, ValueError):
        if base["docker_wsl2_backend_observed"]:
            return {"status": "PASS", "reason": None, **base}
        return {"status": "UNKNOWN", "reason": "OBSERVATION_MISSING", **base}
    wsl_match = None
    kernel_match = None
    for line in lines:
        if wsl_match is None:
            wsl_match = re.fullmatch(
                r"\s*(?:WSL\s+version|WSL\s+バージョン)\s*:\s*"
                r"([0-9]+(?:\.[0-9]+){1,3})\s*", line)
        if kernel_match is None:
            kernel_match = re.fullmatch(
                r"\s*(?:Kernel\s+version|カーネル\s+バージョン)\s*:\s*"
                r"([0-9]+(?:\.[0-9]+){1,4})(?:-[A-Za-z0-9._-]+)?\s*", line)
    if wsl_match is not None:
        base["wsl_version"] = wsl_match.group(1)
    if kernel_match is not None:
        base["kernel_version"] = kernel_match.group(1)
    # package versionはmetadata。WSL2の合否は実Docker Linux kernel markerで決める。
    if base["docker_wsl2_backend_observed"]:
        return {"status": "PASS", "reason": None, **base}
    return {"status": "UNKNOWN", "reason": "OBSERVATION_MISSING", **base}


def _windows_system_directory() -> Path | None:
    """GetSystemDirectoryWでOSが返すSystem32を取得し、環境値/PATHを信用しない。"""
    if os.name != "nt":
        return None
    try:
        win32 = getattr(ctypes, "WinDLL")
        kernel32 = win32("kernel32", use_last_error=True)
        function = kernel32.GetSystemDirectoryW
        function.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]
        function.restype = ctypes.c_uint32
        buffer = ctypes.create_unicode_buffer(32768)
        length = int(function(buffer, len(buffer)))
        if length <= 0 or length >= len(buffer):
            return None
        result = Path(buffer.value)
        return result if result.is_absolute() else None
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def _fixed_wsl_executable() -> str | None:
    system_directory = _windows_system_directory()
    return (str(system_directory / "wsl.exe")
            if system_directory is not None else None)


def _capacity_values(value: Any) -> dict[str, Any]:
    """Validate a capacity profile and retain its evidence boundary.

    bound_verified=True is accepted only from a trusted internal
    observation adapter or test injection; CLI and pilot inputs cannot
    construct a free-form capacity profile.
    """
    if type(value) is not dict or set(value) != CAPACITY_PROFILE_FIELDS:
        raise _error("INVALID_INPUT")
    if type(value["bound_verified"]) is not bool:
        raise _error("INVALID_INPUT")
    profile_ref = value["profile_ref"]
    try:
        require_ref(profile_ref)
    except ContractError as exc:
        raise _error("INVALID_INPUT") from exc
    if profile_ref["kind"] != "resource_profile":
        raise _error("BINDING_MISMATCH")
    for name in ("remaining_write_upper_bound_bytes", "reserved_bytes"):
        try:
            require_uint(value[name])
        except ContractError as exc:
            raise _error("INVALID_INPUT") from exc
    if value["remaining_write_upper_bound_bytes"] <= 0:
        raise _error("INVALID_INPUT")
    required = (value["remaining_write_upper_bound_bytes"]
                + value["reserved_bytes"] + MIN_FREE_BYTES)
    if required > MAX_INTEGER:
        raise _error("INVALID_INPUT")
    return {
        "profile_ref": deepcopy(profile_ref),
        "remaining_write_upper_bound_bytes": value["remaining_write_upper_bound_bytes"],
        "reserved_bytes": value["reserved_bytes"],
        "bound_verified": value["bound_verified"],
        "required_free_bytes": required,
    }


def _runtime_metadata(folder: Path) -> tuple[dict[str, Any] | None, str | None]:
    """Read only the closed v1/v2 metadata shape from the runtime directory."""
    fields_v1 = {"schema_version", "kind", "contract_series_id", "profile"}
    fields_v2 = fields_v1 | {"baseline_series_id"}
    for name in RUNTIME_METADATA_NAMES:
        path = folder / name
        if not path.exists():
            continue
        if path.is_symlink():
            return None, "PATH_REJECTED"
        try:
            value = _load_json_file(path)
        except OperationsError as exc:
            return None, getattr(exc, "code", "OBSERVATION_MISSING")
        version = value.get("schema_version") if type(value) is dict else None
        shape_ok = (
            type(value) is dict
            and ((type(version) is int and version == 1 and set(value) == fields_v1)
                 or (type(version) is int and version == 2 and set(value) == fields_v2
                     and _valid_id(value.get("baseline_series_id"))))
        )
        if (not shape_ok or value.get("kind") != "runtime_metadata"
                or not _valid_id(value.get("contract_series_id"))
                or value.get("profile") not in SETUP_PROFILES):
            return None, "OBSERVATION_MISSING"
        return value, None
    return None, "OBSERVATION_MISSING"

def _validate_authority_diagnostics(value: dict[str, Any]) -> dict[str, Any]:
    fields = {
        "schema_version", "kind", "action", "request_id", "ci_eligible",
        "database_schema_version", "permission_generation", "extension_digest",
        "checked_at",
    }
    if type(value) is not dict or set(value) != fields:
        raise _error("BINDING_MISMATCH")
    if (type(value["schema_version"]) is not int or value["schema_version"] != 1
            or value["kind"] != "evaluation_authority_result"
            or value["action"] != "authority_diagnostics"
            or not _valid_id(value["request_id"])
            or value["ci_eligible"] is not False):
        raise _error("BINDING_MISMATCH")
    try:
        require_uint(value["database_schema_version"])
        require_uint(value["permission_generation"])
        require_digest(value["extension_digest"])
        require_uint(value["checked_at"])
    except ContractError as exc:
        raise _error("OBSERVATION_MISSING") from exc
    return value


def _validate_contract_current(value: dict[str, Any], series_id: str) -> dict[str, Any]:
    fields = {
        "schema_version", "kind", "action", "request_id", "ci_eligible",
        "series_id", "adopted", "valid", "generation", "contract",
        "proposal_id", "validation_id",
    }
    if type(value) is not dict or set(value) != fields:
        raise _error("BINDING_MISMATCH")
    if (value["series_id"] != series_id
            or type(value["schema_version"]) is not int
            or value["schema_version"] != 1
            or value["kind"] != "evaluation_authority_result"
            or value["action"] != "contract_current"
            or value["ci_eligible"] is not False
            or type(value["adopted"]) is not bool
            or type(value["valid"]) is not bool
            or type(value["generation"]) is not int
            or value["generation"] < 0):
        raise _error("BINDING_MISMATCH")
    if value["contract"] is not None and type(value["contract"]) is not dict:
        raise _error("OBSERVATION_MISSING")
    return value


def _validate_baseline_current(value: dict[str, Any], series_id: str) -> dict[str, Any]:
    common = {"schema_version", "kind", "action", "request_id", "ci_eligible",
              "series_id"}
    found = set(value) if type(value) is dict else set()
    valid_fields = common | {
        "generation", "adopted", "baseline", "valid", "use", "reason",
        "adoption_verified",
    }
    not_found_fields = common | {"adopted", "use", "valid", "reason"}
    if found not in (valid_fields, not_found_fields):
        raise _error("BINDING_MISMATCH")
    if (type(value["schema_version"]) is not int or value["schema_version"] != 1
            or value["kind"] != "baseline_authority_result"
            or value["action"] != "baseline_current"
            or value["request_id"] is None
            or not _valid_id(value["request_id"])
            or value["ci_eligible"] is not False
            or value["series_id"] != series_id):
        raise _error("BINDING_MISMATCH")
    if found == not_found_fields:
        if any(value[name] is not False for name in ("adopted", "use", "valid")):
            raise _error("OBSERVATION_MISSING")
        return value
    if (type(value["generation"]) is not int or value["generation"] < 0
            or type(value["adopted"]) is not bool
            or type(value["valid"]) is not bool
            or type(value["use"]) is not bool
            or value["adoption_verified"] is not True):
        raise _error("OBSERVATION_MISSING")
    return value


def _check_tick(monotonic_clock: Callable[[], Any] | None,
                fallback: int) -> tuple[int, bool]:
    try:
        return _monotonic_ns(monotonic_clock), True
    except OperationsError:
        return fallback, False


def _clock_sample(clock: Callable[[], Any] | None,
                  monotonic_clock: Callable[[], Any] | None,
                  started_ns: int, started_utc: int) -> tuple[str, int | None, int | None]:
    try:
        wall = _now(clock)
        tick = _monotonic_ns(monotonic_clock)
    except OperationsError:
        return "UNKNOWN", None, None
    if tick < started_ns or wall < started_utc:
        return "FAIL", wall, tick
    return "PASS", wall, tick


def _clock_window(clock: Callable[[], Any] | None,
                  monotonic_clock: Callable[[], Any] | None,
                  started_ns: int, started_utc: int,
                  sleeper: Callable[[float], Any] | None) -> dict[str, Any]:
    """同一hostの実時間窓を取り、UTC/monotonicのstepを別々に記録する。"""
    sleep_fn = time.sleep if sleeper is None else sleeper
    interval = CLOCK_OBSERVATION_SECONDS / 2
    samples: list[dict[str, int]] = [
        {"wall_utc_s": started_utc, "monotonic_ns": started_ns},
    ]
    step_examples: list[dict[str, int]] = []
    rollback_count = 0
    step_count = 0
    status = "PASS"
    reason: str | None = None
    previous_wall = started_utc
    previous_tick = started_ns
    for _ in range(2):
        try:
            sleep_fn(interval)
            wall = _now(clock)
            tick = _monotonic_ns(monotonic_clock)
        except (OperationsError, OSError, TypeError, ValueError, OverflowError):
            status = "UNKNOWN"
            reason = "CLOCK_UNAVAILABLE"
            break
        samples.append({"wall_utc_s": wall, "monotonic_ns": tick})
        if wall < previous_wall or tick < previous_tick:
            rollback_count += 1
            status = "FAIL"
            reason = "CLOCK_ROLLBACK"
            break
        wall_delta_ns = (wall - previous_wall) * 1_000_000_000
        monotonic_delta_ns = tick - previous_tick
        # UTCは整数秒のため1秒未満の量子化だけを許容する。これを超える
        # wall/monotonicの差はstepとして観測不能扱いにする。
        if abs(wall_delta_ns - monotonic_delta_ns) > 1_000_000_000:
            step_count += 1
            if len(step_examples) < 4:
                step_examples.append({
                    "wall_delta_ns": wall_delta_ns,
                    "monotonic_delta_ns": monotonic_delta_ns,
                })
            status = "UNKNOWN"
            reason = "CLOCK_UNAVAILABLE"
            break
        previous_wall = wall
        previous_tick = tick
    if status == "PASS" and len(samples) != 3:
        status = "UNKNOWN"
        reason = "OBSERVATION_MISSING"
    last = samples[-1]
    wall_duration_s = last["wall_utc_s"] - started_utc
    monotonic_duration_ns = last["monotonic_ns"] - started_ns
    return {
        "status": status,
        "reason": reason,
        "samples": samples,
        "sample_count": len(samples),
        "rollback_count": rollback_count,
        "step_count": step_count,
        "step_examples": step_examples,
        "wall_duration_s": wall_duration_s,
        "monotonic_duration_ns": monotonic_duration_ns,
        "observation_sufficient": (
            status == "PASS"
            and len(samples) == 3
            and wall_duration_s >= CLOCK_OBSERVATION_SECONDS
            and monotonic_duration_ns >= CLOCK_OBSERVATION_SECONDS * 1_000_000_000
        ),
    }


def run_doctor(workspace: str | Path, phase: str = "bootstrap", *,
               runtime: str | Path | None = None, authority: Any = None,
               request_id: str | None = None,
               clock: Callable[[], Any] | None = None,
               monotonic_clock: Callable[[], Any] | None = None,
               docker_path: str | None = None,
               wsl_path: str | None = None,
               disk_usage: Callable[[str | Path], Any] | None = None,
               capacity_profile: Mapping[str, Any] | None = None,
               storage_observation: Mapping[str, Any] | None = None,
               contract_series_id: str | None = None,
               baseline_series_id: str | None = None,
               sleep: Callable[[float], Any] | None = None) -> tuple[dict, dict | None]:
    """bootstrap/ready 診断を実行し、補助resultと専用payloadを返す。

    すべての外部観測は固定された read-only API または subprocess に限定する。
    ready の契約系列は runtime metadata か明示された selector から取得し、
    欠測時に既知の fixture 系列を推測しない。
    """
    rid = _request_id(request_id, "doctor")
    if phase not in DOCTOR_PHASES:
        return _operation("ops.doctor", rid, "REJECTED", reasons=("INVALID_INPUT",)), None
    if contract_series_id is not None and not _valid_id(contract_series_id):
        return _operation("ops.doctor", rid, "REJECTED",
                          reasons=("INVALID_INPUT",)), None
    if baseline_series_id is not None and not _valid_id(baseline_series_id):
        return _operation("ops.doctor", rid, "REJECTED",
                          reasons=("INVALID_INPUT",)), None
    try:
        base = _workspace(workspace)
        started_utc = _now(clock)
        started_ns = _monotonic_ns(monotonic_clock)
    except OperationsError as exc:
        status, reason = _map_operation_error(exc)
        return _operation("ops.doctor", rid, status, reasons=(reason,)), None

    if storage_observation is not None:
        from .storage_observation import validate_storage_observation
        try:
            storage_observation = validate_storage_observation(storage_observation)
        except ContractError:
            return _operation("ops.doctor", rid, "REJECTED",
                              reasons=("INVALID_INPUT",)), None
    checks: list[dict] = []
    observations: dict[str, Any] = {}
    if storage_observation is not None:
        # This timestamped storage snapshot is auxiliary evidence, not a
        # verified setup write bound or a capacity PASS by itself.
        observations["docker_storage"] = storage_observation
    timing: list[dict[str, Any]] = []

    def add(check_id: str, required: bool, status: str, reason: str | None,
            scope: Iterable[str], *, now: int | None = None,
            tick: int | None = None) -> None:
        observed_at = started_utc if now is None else now
        timing_observed = tick is not None
        if tick is None:
            tick, timing_observed = _check_tick(monotonic_clock, started_ns)
        observed_tick = max(started_ns, tick)
        item = _check(check_id, required, status, reason, scope,
                      started_ns, now=observed_at, finished_ns=observed_tick)
        checks.append(item)
        timing.append({
            "check_id": check_id,
            "observed_elapsed_ns": item["observed_elapsed_ns"],
            "clock_observed": timing_observed,
        })

    add("workspace", True, "PASS", None, ("workspace",))
    version_ok = sys.version_info >= (3, 11)
    add("python", True, "PASS" if version_ok else "FAIL",
        None if version_ok else "PYTHON_UNSUPPORTED", ("python",))
    observations["python"] = {
        "major": sys.version_info.major, "minor": sys.version_info.minor,
    }

    target_platform = _platform_kind()
    platform_ok = target_platform in {"linux-x86_64", "windows-wsl2-x86_64"}
    add("platform", True, "PASS" if platform_ok else "FAIL",
        None if platform_ok else "UNSUPPORTED_CAPABILITY", ("platform",))
    observations["platform"] = {
        "system": platform.system(), "machine": platform.machine(),
        "target": target_platform,
    }

    docker = docker_path if docker_path is not None else shutil.which("docker")
    docker_observation = _docker_probe(docker)
    add("docker", True, docker_observation["status"],
        docker_observation["reason"], ("docker",))
    observations["docker"] = {
        "available": docker_observation["available"],
        "engine_os": docker_observation["engine_os"],
        "server_version": docker_observation["server_version"],
        "kernel_version": docker_observation.get("kernel_version"),
        "endpoint": docker_observation.get("endpoint"),
        "version_elapsed_ns": docker_observation["version_elapsed_ns"],
        "info_elapsed_ns": docker_observation["info_elapsed_ns"],
    }

    system = platform.system()
    if system == "Linux":
        add("wsl2", False, "NOT_APPLICABLE", None, ("wsl2",))
        observations["wsl2"] = {
            "required": False,
            "docker_linux_engine_observed": docker_observation["engine_os"] == "linux",
        }
    elif system == "Windows":
        # wsl_path は信頼済み単体テスト用 hook。通常経路は System32 の固定絶対path。
        wsl = wsl_path if wsl_path is not None else _fixed_wsl_executable()
        wsl_observation = _wsl_probe(
            wsl, docker_kernel_version=docker_observation.get("kernel_version"))
        add("wsl2", True, wsl_observation["status"],
            wsl_observation["reason"], ("wsl2",))
        observations["wsl2"] = {
            "required": True,
            "wsl_version": wsl_observation["wsl_version"],
            "kernel_version": wsl_observation["kernel_version"],
            "docker_kernel_version": wsl_observation.get("docker_kernel_version"),
            "docker_wsl2_backend_observed": wsl_observation.get(
                "docker_wsl2_backend_observed"),
            "elapsed_ns": wsl_observation["elapsed_ns"],
            "docker_linux_engine_observed": docker_observation["engine_os"] == "linux",
        }
    else:
        add("wsl2", True, "FAIL", "UNSUPPORTED_CAPABILITY", ("wsl2",))
        observations["wsl2"] = {
            "required": True, "docker_linux_engine_observed": False,
        }

    clock_observation = _clock_window(
        clock, monotonic_clock, started_ns, started_utc, sleep)
    clock_status = clock_observation["status"]
    clock_samples = clock_observation["samples"]
    clock_now = clock_samples[-1]["wall_utc_s"] if clock_samples else None
    clock_tick = clock_samples[-1]["monotonic_ns"] if clock_samples else None
    clock_reason = clock_observation["reason"]
    if clock_status == "FAIL":
        add("clock", True, "FAIL", clock_reason or "CLOCK_ROLLBACK",
            ("clock",), now=clock_now, tick=clock_tick)
    elif clock_status == "UNKNOWN":
        add("clock", True, "UNKNOWN", clock_reason or "OBSERVATION_MISSING",
            ("clock",), now=clock_now, tick=clock_tick)
    elif not clock_observation["observation_sufficient"]:
        clock_status = "UNKNOWN"
        clock_reason = "OBSERVATION_MISSING"
        add("clock", True, "UNKNOWN", clock_reason, ("clock",),
            now=clock_now, tick=clock_tick)
    else:
        add("clock", True, "PASS", None, ("clock",),
            now=clock_now, tick=clock_tick)
    observations["clock"] = {
        "wall_utc_s": clock_now,
        "monotonic_ns": clock_tick,
        "started_at_utc_s": started_utc,
        "started_monotonic_ns": started_ns,
        "sample_count": clock_observation["sample_count"],
        "samples": clock_samples,
        "rollback_count": clock_observation["rollback_count"],
        "step_count": clock_observation["step_count"],
        "step_examples": clock_observation["step_examples"],
        "observation_required_seconds": CLOCK_OBSERVATION_SECONDS,
        "observation_wall_seconds": clock_observation["wall_duration_s"],
        "observation_monotonic_ns": clock_observation["monotonic_duration_ns"],
        "observation_sufficient": clock_observation["observation_sufficient"],
        "monotonic_domain": "host",
    }

    try:
        usage = disk_usage(base) if disk_usage is not None else shutil.disk_usage(base)
        free = usage.free
        if type(free) is not int:
            raise ValueError("FREE_BYTES_TYPE")
    except (OSError, AttributeError, TypeError, ValueError):
        free = None
    capacity_values = None
    capacity_error = None
    if capacity_profile is not None:
        try:
            capacity_values = _capacity_values(capacity_profile)
        except OperationsError as exc:
            capacity_error = getattr(exc, "code", "OBSERVATION_MISSING")
    if capacity_profile is None:
        add("capacity", True, "UNKNOWN", "OBSERVATION_MISSING", ("workspace",))
        observations["capacity"] = {
            "free_bytes": free,
            "profile_ref": None,
            "remaining_write_upper_bound_bytes": None,
            "reserved_bytes": None,
            "required_free_bytes": None,
            "bound_verified": None,
        }
    elif capacity_error is not None:
        add("capacity", True, "FAIL", capacity_error, ("workspace",))
        observations["capacity"] = {
            "free_bytes": free, "profile_ref": None,
            "remaining_write_upper_bound_bytes": None,
            "reserved_bytes": None, "required_free_bytes": None,
            "bound_verified": None,
        }
    else:
        required_free = capacity_values["required_free_bytes"]
        if type(free) is not int:
            capacity_status, capacity_reason = "UNKNOWN", "OBSERVATION_MISSING"
        elif free < required_free:
            capacity_status, capacity_reason = "FAIL", "CAPACITY_EXCEEDED"
        elif not capacity_values["bound_verified"]:
            # A fixed-sample formula is a budget, not an observed upper bound.
            capacity_status, capacity_reason = "UNKNOWN", "OBSERVATION_MISSING"
        else:
            capacity_status, capacity_reason = "PASS", None
        add("capacity", True, capacity_status, capacity_reason, ("workspace",))
        observations["capacity"] = {
            "free_bytes": free,
            "profile_ref": capacity_values["profile_ref"],
            "remaining_write_upper_bound_bytes":
                capacity_values["remaining_write_upper_bound_bytes"],
            "reserved_bytes": capacity_values["reserved_bytes"],
            "required_free_bytes": required_free,
            "bound_verified": capacity_values["bound_verified"],
        }

    principal = _trusted_principal()
    try:
        uid = os.getuid() if hasattr(os, "getuid") else None
    except OSError:
        uid = None
    identity_ok = principal is not None
    add("identity", True, "PASS" if identity_ok else "UNKNOWN",
        None if identity_ok else "IDENTITY_UNAVAILABLE", ("os_identity",))
    observations["identity"] = {
        "available": identity_ok, "uid": uid,
        "principal_source": "trusted_os_token" if identity_ok else None,
    }

    runtime_ref: dict | None = None
    if phase == "bootstrap":
        for check_id, scope in (
            ("runtime", "runtime"), ("image_lock", "image"),
            ("authority", "authority"), ("binding", "binding"),
            ("database", "authority"),
        ):
            add(check_id, False, "NOT_APPLICABLE", None, (scope,))
    else:
        folder: Path | None = None
        runtime_metadata: dict[str, Any] | None = None
        metadata_reason: str | None = None
        try:
            folder = _runtime_folder(base, runtime)
            state, runtime_ref = _deployment(folder)
            add("runtime", True, "PASS", None, ("runtime",))
            observations["runtime"] = {
                "prefix": state["prefix"],
                "container_count": len(state["containers"]),
            }
            runtime_metadata, metadata_reason = _runtime_metadata(folder)
            if runtime_metadata is not None:
                observations["runtime"]["metadata_profile"] = runtime_metadata["profile"]
                observations["runtime"]["metadata_schema_version"] = runtime_metadata["schema_version"]
                observations["runtime"]["contract_series_id"] = runtime_metadata["contract_series_id"]
                if runtime_metadata["schema_version"] == 2:
                    observations["runtime"]["baseline_series_id"] = runtime_metadata["baseline_series_id"]
            else:
                observations["runtime"]["metadata_reason"] = metadata_reason
        except OperationsError as exc:
            add("runtime", True, "FAIL",
                getattr(exc, "code", "RUNTIME_UNAVAILABLE"), ("runtime",))

        lock_value: dict | None = None
        lock_path = ROOT / "config" / "authority-runtime.lock.json"
        try:
            if not lock_path.is_file() or lock_path.is_symlink():
                raise OSError
            lock_value = _load_json_file(lock_path)
            if type(lock_value.get("image_id")) is not str or not lock_value["image_id"]:
                raise ValueError
        except (OSError, OperationsError, ValueError, TypeError, AttributeError):
            add("image_lock", True, "FAIL", "IMAGE_UNAVAILABLE", ("image",))
        else:
            add("image_lock", True, "PASS", None, ("image",))
            observations["image_lock"] = {"image_id": lock_value["image_id"]}

        diagnostic: dict[str, Any] | None = None
        diagnostic_error: OperationsError | None = None
        if authority is None:
            add("authority", True, "UNKNOWN", "AUTHORITY_REQUIRED", ("authority",))
            add("binding", True, "UNKNOWN", "AUTHORITY_REQUIRED", ("binding",))
            add("database", True, "UNKNOWN", "AUTHORITY_REQUIRED", ("authority",))
        else:
            diagnostics_request = {
                "schema_version": 1, "action": "authority_diagnostics",
                "request_id": _new_id("doctor-authority-diagnostics", rid),
            }
            try:
                diagnostic = _authority_call(
                    authority, 12004, diagnostics_request,
                    expected_kind="evaluation_authority_result")
                _validate_authority_diagnostics(diagnostic)
            except OperationsError as exc:
                diagnostic_error = exc
                code = getattr(exc, "code", "OBSERVATION_MISSING")
                detail = getattr(exc, "detail", None)
                denied = code in {"AUTHORITY_DENIED", "AUTHORITY_REVOKED"} or detail in {
                    "AUTHORITY_DENIED", "AUTHORITY_REVOKED",
                }
                if denied:
                    add("database", True, "FAIL",
                        _authority_denial_reason(exc), ("authority",))
                elif code in {"BINDING_MISMATCH"}:
                    add("database", True, "FAIL", code, ("authority",))
                else:
                    add("database", True, "UNKNOWN",
                        code if code in {"AUTHORITY_REQUIRED", "OBSERVATION_MISSING"}
                        else "OBSERVATION_MISSING", ("authority",))
            else:
                add("database", True, "PASS", None, ("authority",))
                observations["database"] = {
                    "database_schema_version": diagnostic["database_schema_version"],
                    "permission_generation": diagnostic["permission_generation"],
                    "extension_digest": diagnostic["extension_digest"],
                    "checked_at": diagnostic["checked_at"],
                }

            series_id = contract_series_id
            if series_id is None and runtime_metadata is not None:
                series_id = runtime_metadata["contract_series_id"]
            if series_id is None:
                reason = metadata_reason or "OBSERVATION_MISSING"
                add("authority", True, "UNKNOWN", reason, ("authority",))
                add("binding", True, "UNKNOWN", reason, ("binding",))
            else:
                current_request = {
                    "schema_version": 1, "action": "contract_current",
                    "request_id": _new_id(
                        "doctor-contract-current",
                        {"request_id": rid, "series_id": series_id},
                    ),
                    "series_id": series_id,
                }
                try:
                    current = _authority_call(
                        authority, 12004, current_request,
                        expected_kind="evaluation_authority_result")
                    _validate_contract_current(current, series_id)
                except OperationsError as exc:
                    code = getattr(exc, "code", "OBSERVATION_MISSING")
                    detail = getattr(exc, "detail", None)
                    denied = code in {"AUTHORITY_DENIED", "AUTHORITY_REVOKED"} or detail in {
                        "AUTHORITY_DENIED", "AUTHORITY_REVOKED",
                    }
                    if denied:
                        authority_status, authority_reason = "FAIL", _authority_denial_reason(exc)
                    elif code == "BINDING_MISMATCH":
                        authority_status, authority_reason = "FAIL", code
                    else:
                        authority_status, authority_reason = "UNKNOWN", (
                            code if code in {"AUTHORITY_REQUIRED", "OBSERVATION_MISSING"}
                            else "OBSERVATION_MISSING")
                    add("authority", True, authority_status,
                        authority_reason, ("authority",))
                    add("binding", True, "UNKNOWN", authority_reason, ("binding",))
                else:
                    contract = current["contract"]
                    metadata_profile = (
                        runtime_metadata.get("profile")
                        if runtime_metadata is not None else None)
                    expected_use_case = {
                        "sample-ci": "UC-CI", "sample-llm": "UC-LLM",
                    }.get(metadata_profile)
                    contract_ok = (
                        current["adopted"] is True
                        and current["valid"] is True
                        and type(contract) is dict
                        and _valid_id(contract.get("contract_id"))
                        and type(contract.get("use_cases")) is list
                        and (expected_use_case is None
                             or contract["use_cases"] == [expected_use_case])
                    )
                    binding_ok = contract_ok
                    binding_status = "PASS" if contract_ok else "FAIL"
                    binding_reason = None if contract_ok else "BINDING_MISMATCH"
                    baseline_observed: dict[str, Any] = {"checked": False}
                    comparison = contract.get("comparison") if contract_ok else None
                    if (type(comparison) is dict
                            and comparison.get("mode") == "required"):
                        expected_baseline = comparison.get("baseline_ref")
                        selected_baseline_series = baseline_series_id
                        selected_source = "argument" if selected_baseline_series is not None else None
                        if selected_baseline_series is None and runtime_metadata is not None:
                            selected_baseline_series = runtime_metadata.get("baseline_series_id")
                            if selected_baseline_series is not None:
                                selected_source = "runtime_metadata"
                        baseline_observed = {
                            "checked": False,
                            "baseline_series_id": selected_baseline_series,
                            "series_source": selected_source,
                        }
                        expected_ref_ok = False
                        if type(expected_baseline) is dict:
                            try:
                                require_ref(expected_baseline)
                                expected_ref_ok = expected_baseline.get("kind") == "baseline"
                            except ContractError:
                                expected_ref_ok = False
                        if not expected_ref_ok:
                            binding_ok = False
                            binding_status, binding_reason = "FAIL", "BINDING_MISMATCH"
                            baseline_observed.update({"checked": True, "valid": False})
                        elif not _valid_id(selected_baseline_series):
                            # Never infer the series from a content reference.
                            binding_status, binding_reason = "UNKNOWN", "OBSERVATION_MISSING"
                            baseline_observed.update({"reason": "OBSERVATION_MISSING"})
                        else:
                            baseline_request = {
                                "schema_version": 1, "action": "baseline_current",
                                "request_id": _new_id(
                                    "doctor-baseline-current",
                                    {"request_id": rid, "series_id": selected_baseline_series},
                                ),
                                "series_id": selected_baseline_series,
                            }
                            try:
                                baseline = _authority_call(
                                    authority, 12004, baseline_request,
                                    expected_kind="baseline_authority_result")
                                _validate_baseline_current(baseline, selected_baseline_series)
                                baseline_value = baseline.get("baseline")
                                baseline_ok = (
                                    baseline.get("adopted") is True
                                    and baseline.get("valid") is True
                                    and baseline.get("use") is False
                                    and type(baseline_value) is dict
                                    and _valid_id(baseline_value.get("baseline_id"))
                                    and baseline_value.get("baseline_series_id") == selected_baseline_series
                                    and content_ref(
                                        "baseline", baseline_value["baseline_id"],
                                        baseline_value,
                                    ) == expected_baseline
                                )
                                binding_ok = binding_ok and baseline_ok
                                baseline_observed = {
                                    "checked": True,
                                    "valid": baseline_ok,
                                    "generation": baseline.get("generation"),
                                    "baseline_series_id": selected_baseline_series,
                                    "series_source": selected_source,
                                }
                                if not baseline_ok:
                                    binding_status, binding_reason = "FAIL", "BINDING_MISMATCH"
                            except OperationsError as exc:
                                binding_ok = False
                                code = getattr(exc, "code", "OBSERVATION_MISSING")
                                if code in {"AUTHORITY_REQUIRED", "OBSERVATION_MISSING"}:
                                    binding_status, binding_reason = "UNKNOWN", code
                                else:
                                    binding_status, binding_reason = "FAIL", (
                                        _authority_denial_reason(exc)
                                        if code in {"AUTHORITY_DENIED", "AUTHORITY_REVOKED"}
                                        else "BINDING_MISMATCH"
                                    )
                                baseline_observed = {
                                    "checked": True, "valid": False,
                                    "reason": binding_reason,
                                    "baseline_series_id": selected_baseline_series,
                                    "series_source": selected_source,
                                }
                    add("authority", True, "PASS", None, ("authority",))
                    add("binding", True, binding_status, binding_reason,
                        ("contract", "baseline", "target", "use_case"))
                    observations["authority"] = {
                        "series_id": series_id,
                        "contract_adopted": current["adopted"] is True,
                        "contract_valid": current["valid"] is True,
                        "contract_id": contract.get("contract_id") if isinstance(contract, dict) else None,
                        "contract_generation": current["generation"],
                        "use_cases": contract.get("use_cases") if isinstance(contract, dict) else None,
                        "baseline": baseline_observed,
                    }
            if diagnostic is None and "database" not in observations:
                observations["database"] = {
                    "available": False,
                    "reason": getattr(diagnostic_error, "code", "OBSERVATION_MISSING"),
                }

    status, reasons = _doctor_check_result(checks)
    raw_finished_utc: int | None = None
    raw_finished_ns: int | None = None
    finished_utc = started_utc
    finished_ns = started_ns
    try:
        raw_finished_utc = _now(clock)
        raw_finished_ns = _monotonic_ns(monotonic_clock)
        finished_utc = raw_finished_utc
        finished_ns = raw_finished_ns
        if (finished_utc < started_utc
                or (type(clock_now) is int and finished_utc < clock_now)
                or finished_ns < started_ns
                or (type(clock_tick) is int and finished_ns < clock_tick)):
            status, reasons = "REJECTED", ["CLOCK_ROLLBACK"]
    except OperationsError:
        if status != "REJECTED":
            status, reasons = "INCOMPLETE", ["CLOCK_UNAVAILABLE"]
    observations["clock"]["raw_finished_at_utc_s"] = raw_finished_utc
    observations["clock"]["raw_finished_monotonic_ns"] = raw_finished_ns
    observations["clock"]["finished_values_clamped"] = (
        raw_finished_utc is not None
        and (raw_finished_utc < started_utc
             or (type(clock_now) is int and raw_finished_utc < clock_now))
    ) or (
        raw_finished_ns is not None
        and (raw_finished_ns < started_ns
             or (type(clock_tick) is int and raw_finished_ns < clock_tick))
    )
    observations["check_timing"] = timing
    finished_utc = max(
        started_utc,
        clock_now if type(clock_now) is int else started_utc,
        finished_utc,
    )
    finished_ns = max(
        started_ns,
        clock_tick if type(clock_tick) is int else started_ns,
        finished_ns,
    )
    payload = {
        "schema_version": 1, "kind": DOCTOR_KIND,
        "id": rid or _new_id("doctor"),
        "phase": phase, "workspace_path": str(base),
        "started_at_utc_s": started_utc, "finished_at_utc_s": finished_utc,
        "started_monotonic_ns": started_ns,
        "finished_monotonic_ns": finished_ns, "checks": checks,
        "limits": {
            "max_elapsed_seconds": DOCTOR_MAX_SECONDS,
            "min_free_bytes": MIN_FREE_BYTES,
            "bundle_max_bytes": BUNDLE_MAX_BYTES,
            "bundle_max_entries": BUNDLE_MAX_ENTRIES,
        },
        "runtime_ref": runtime_ref, "observations": observations,
    }
    try:
        validate_doctor_result(payload)
    except (ContractError, TypeError, ValueError):
        return _operation("ops.doctor", rid, "INCOMPLETE",
                          reasons=("IO_ERROR",)), None
    if finished_ns - started_ns > DOCTOR_MAX_SECONDS * 1_000_000_000:
        status, reasons = "INCOMPLETE", ["OBSERVATION_MISSING"]
    try:
        artifact_path = Path(".ga") / "operations" / "doctor" / (
            payload["id"] + ".json")
        result_ref = _save_artifact(base, artifact_path, payload,
                                    validate_doctor_result)
    except OperationsError as exc:
        return _operation("ops.doctor", rid, "INCOMPLETE",
                          reasons=(getattr(exc, "code", "IO_ERROR"),)), payload
    return _operation("ops.doctor", rid, status, result_ref=result_ref,
                      reasons=reasons, checked_at=finished_utc), payload

def doctor(*args: Any, **kwargs: Any) -> dict:
    """診断resultだけを返す簡易API。payloadは ``doctor_payload`` で取得する。"""
    return run_doctor(*args, **kwargs)[0]


def doctor_payload(*args: Any, **kwargs: Any) -> tuple[dict, dict | None]:
    return run_doctor(*args, **kwargs)


def _setup_ref(kind: str, identifier: str, value: dict) -> dict:
    """plan本文へ内容を埋め込まず、固定内容の完全refだけを発行する。"""
    try:
        return content_ref(kind, identifier, value)
    except (ContractError, TypeError, ValueError) as exc:
        raise _error("INVALID_INPUT") from exc


def _setup_output_path(base: Path, value: str | Path) -> Path:
    target = _child(base, value)
    if not target.is_absolute() or not target.is_relative_to(base):
        raise _error("PATH_REJECTED")
    # output_paths は新規出力先であり、既存のファイルを暗黙に差し替えない。
    if target.exists() or target.is_symlink():
        raise _error("RESULT_CONFLICT")
    return target


def _setup_source_ref(base: Path | None = None) -> dict:
    manifest = _snapshot_document(
        "productization-operations-source",
        ("src/gah/operations.py", "src/gah/productization.py",
         "src/gah/productization_journal.py", "tools/authority_runtime.py",
         "tools/setup_baseline.py", "tools/setup_apply.py",
         "tools/setup_capacity.py", "tools/setup_request.py",
         "tools/gah_run.py", "tools/gah_ci.py", "tools/gah_report.py",
         "src/gah/supervised_run.py", "src/gah/llm_supervised_run.py",
         "src/gah/supervisor_checkpoint.py"),
        purpose="productization-operations")
    ref = content_ref("snapshot_manifest", manifest["id"], manifest)
    if base is not None:
        _persist_setup_ref(base, ref, manifest)
    return ref


def _setup_requirements_ref(base: Path | None = None) -> dict:
    manifest = _snapshot_document(
        "productization-operations-requirements",
        ("docs/productization-spec.md", "docs/productization-operations-spec.md",
         "docs/productization-requirements.md"),
        purpose="GAH-PR09-PR14")
    ref = content_ref("snapshot_manifest", manifest["id"], manifest)
    if base is not None:
        _persist_setup_ref(base, ref, manifest)
    return ref


def _setup_lock_ref(path: Path, identifier: str, *,
                    base: Path | None = None) -> dict:
    lock = _load_json_file(path)
    if type(lock.get("image_id")) is not str or not lock["image_id"]:
        raise _error("IMAGE_UNAVAILABLE")
    # image tagではなく、lock本文のdigestから生成したIDを使う。
    ref = _setup_ref("image_lock", identifier, lock)
    if base is not None:
        _persist_setup_ref(base, ref, lock)
    return ref


def _setup_id(base: Path, profile: str, output: Path) -> str:
    return _new_id("setup", {"profile": profile, "workspace_path": str(base),
                              "plan_output": str(output)})


def _sample_refs(profile: str, setup_id: str, now: int, *,
                 base: Path | None = None) -> tuple[dict, dict, dict]:
    """既存の固定factoryから sample contract/case/evaluator の実refを算出する。

    contract/case は生成した本文を保存し、fixture evaluator のように既存のrefが
    worker digest を指すものは再計算用 resolver metadata を保存する。
    """
    try:
        from .policy import initial_policy_profile
        policy = initial_policy_profile()
        evaluator_document: dict[str, Any] | None = None
        worker_digest: str | None = None
        if profile == "sample-ci":
            from .fixture_admission import execution_context
            from .fixture_materialization import build_fixture_pack
            worker, lock, execution_profile = execution_context()
            worker_digest = hashlib.sha256(worker).hexdigest()
            built = build_fixture_pack(
                policy, worker, lock, execution_profile, now,
                setup_id, policy_generation=1,
            )
        elif profile == "sample-llm":
            from .docker_runner import PROFILE
            from .llm_materialization import build
            lock = _load_json_file(ROOT / "config" / "guardrail-runtime.lock.json")
            built = build(
                policy, policy_generation=1, run_id=setup_id, now=now,
                target_version="baseline-v1", image_id=lock["image_id"],
                worker_digest=lock["worker_digest"],
                isolation_profile=PROFILE,
            )
            evaluator_document = built.get("evaluator_document")
        else:
            raise _error("INVALID_INPUT")
        bound = built["bound_run"]
        contract = bound["contract"]
        case_set = bound["case_set"]
        evaluator_refs = contract.get("evaluator_refs")
        if (type(contract) is not dict or type(case_set) is not dict
                or type(evaluator_refs) is not list or len(evaluator_refs) != 1):
            raise _error("OBSERVATION_MISSING")
        contract_ref = content_ref(
            "evaluation_contract", contract["contract_id"], contract)
        case_ref = content_ref("case_set", case_set["case_set_id"], case_set)
        evaluator_ref = deepcopy(evaluator_refs[0])
        _ref_kind(evaluator_ref, "evaluator")
        if base is not None:
            _persist_setup_ref(base, contract_ref, contract)
            _persist_setup_ref(base, case_ref, case_set)
            if evaluator_document is not None:
                expected_evaluator = content_ref(
                    "evaluator", evaluator_document["evaluator_id"],
                    evaluator_document,
                )
                if expected_evaluator != evaluator_ref:
                    raise _error("REFERENCE_MISMATCH")
                _persist_setup_ref(base, evaluator_ref, evaluator_document)
            else:
                if worker_digest is None:
                    raise _error("OBSERVATION_MISSING")
                _persist_setup_ref(
                    base, evaluator_ref, {},
                    resolver={
                        "kind": "file_digest",
                        "path": "fixtures/runtime/fixture_worker.py",
                        "sha256": worker_digest,
                    },
                )
        return contract_ref, case_ref, evaluator_ref
    except OperationsError:
        raise
    except OSError as exc:
        raise _error("IO_ERROR") from exc
    except (ContractError, TypeError, ValueError, KeyError, RecursionError) as exc:
        raise _error("OBSERVATION_MISSING") from exc

def _build_setup_plan(base: Path, profile: str, output: Path, *,
                      now: int) -> dict:
    if profile not in SETUP_PROFILES:
        raise _error("INVALID_INPUT")
    if now > MAX_INTEGER - PLAN_TTL_SECONDS:
        raise _error("CLOCK_UNAVAILABLE")
    setup_id = _setup_id(base, profile, output)
    runtime = _setup_output_path(base, Path(".ga") / "runtime" / setup_id)
    run_request = _setup_output_path(base, Path(".ga") / "setup" / setup_id / "run-request.json")
    ci_request = _setup_output_path(base, Path(".ga") / "setup" / setup_id / "ci-request.json")
    try:
        config = _load_json_file(ROOT / "config" / "bootstrap-policy.v1.json")
    except OperationsError:
        raise
    image_lock_refs = [
        _setup_lock_ref(
            ROOT / "config" / "authority-runtime.lock.json",
            "authority-lock-" + setup_id, base=base,
        ),
        _setup_lock_ref(
            ROOT / "config" / "fixture-runtime.lock.json",
            "fixture-lock-" + setup_id, base=base,
        ),
    ]
    if profile == "sample-llm":
        image_lock_refs.append(_setup_lock_ref(
            ROOT / "config" / "guardrail-runtime.lock.json",
            "guardrail-lock-" + setup_id, base=base,
        ))
    profile_name = config.get("management_profile")
    limits = config.get("profiles", {}).get(profile_name) if type(config.get("profiles")) is dict else None
    if type(limits) is not dict:
        raise _error("OBSERVATION_MISSING")
    config_value = {
        "schema_version": 1, "kind": "setup_config",
        "profile": profile, "config": config,
    }
    config_ref = _setup_ref(
        "setup_config", "setup-config-" + setup_id, config_value)
    _persist_setup_ref(base, config_ref, config_value)
    role_value = {
        "schema_version": 1, "kind": "role_recipe",
        "actors": [
            {"role": "manager", "uid": 12001},
            {"role": "validator", "uid": 12003},
            {"role": "operator", "uid": 12004},
        ],
        "broker_uid": 12000,
    }
    role_recipe_ref = _setup_ref(
        "role_recipe", "setup-roles-" + setup_id, role_value)
    _persist_setup_ref(base, role_recipe_ref, role_value)
    resource_value = {
        "schema_version": 1, "kind": "resource_profile",
        "profile": profile_name, "limits": limits,
        "bundle_max_bytes": BUNDLE_MAX_BYTES,
        "bundle_max_entries": BUNDLE_MAX_ENTRIES,
        "cache_max_bytes": CACHE_MAX_BYTES,
        "cache_max_entries": CACHE_MAX_ENTRIES,
    }
    resource_profile_ref = _setup_ref(
        "resource_profile", "setup-resources-" + setup_id, resource_value)
    _persist_setup_ref(base, resource_profile_ref, resource_value)
    sample_contract_ref, sample_case_set_ref, evaluator_ref = _sample_refs(
        profile, setup_id, now, base=base)
    _ref_kind(evaluator_ref, "evaluator")
    payload = {
        "setup_id": setup_id,
        "profile": profile,
        "workspace_path": str(base),
        "platform": _platform_kind(),
        "existing_runtime_ref": None,
        "image_lock_refs": image_lock_refs,
        "config_ref": config_ref,
        "role_recipe_ref": role_recipe_ref,
        "resource_profile_ref": resource_profile_ref,
        "sample_contract_ref": sample_contract_ref,
        "sample_case_set_ref": sample_case_set_ref,
        "evaluator_ref": evaluator_ref,
        "output_paths": {"runtime": str(runtime), "run_request": str(run_request),
                         "ci_request": str(ci_request)},
        "actions": list(SETUP_ACTIONS),
    }
    source_ref = _setup_source_ref(base)
    requirements_ref = _setup_requirements_ref(base)
    plan = {
        "schema_version": 1, "kind": SETUP_PLAN_KIND, "id": setup_id,
        "requirement_ids": ["GAH-PR09", "GAH-PR10", "GAH-PR11", "GAH-PR12", "GAH-PR14"],
        "source_ref": source_ref,
        "requirements_ref": requirements_ref,
        "created_at": now, "expires_at": now + PLAN_TTL_SECONDS,
        "payload": payload,
    }
    try:
        return validate_plan(plan, kind=SETUP_PLAN_KIND,
                             payload_validator=validate_setup_payload, now=now)
    except OperationsError:
        raise
    except (ContractError, TypeError, ValueError, KeyError) as exc:
        raise _error(_public_reason(getattr(exc, "code", "INVALID_INPUT"))) from exc


def _existing_setup_plan(base: Path, target: Path, profile: str,
                         now: int) -> dict | None:
    """同じ出力先の既存planを検査し、現在のsource/profile/期限が同じ時だけ再配送する。"""
    if not target.exists():
        if target.is_symlink():
            raise _error("PATH_REJECTED")
        return None
    if target.is_symlink() or not target.is_file():
        raise _error("RESULT_CONFLICT")
    try:
        existing = _load_json_file(target)
        validate_plan(
            existing, kind=SETUP_PLAN_KIND,
            payload_validator=validate_setup_payload, now=now,
        )
    except OperationsError as exc:
        if getattr(exc, "code", None) == "PLAN_EXPIRED":
            raise
        raise _error("RESULT_CONFLICT") from exc
    except (ContractError, TypeError, ValueError, KeyError) as exc:
        if getattr(exc, "code", None) == "PLAN_EXPIRED":
            raise _error("PLAN_EXPIRED") from exc
        raise _error("RESULT_CONFLICT") from exc
    if (existing["id"] != _setup_id(base, profile, target)
            or existing["payload"]["profile"] != profile
            or existing["payload"]["workspace_path"] != str(base)
            or existing["source_ref"] != _setup_source_ref()
            or existing["requirements_ref"] != _setup_requirements_ref()):
        raise _error("RESULT_CONFLICT")
    # 既存本文と保存ref本文のdigestも同じ再解決経路で確認する。
    for ref in (existing["source_ref"], existing["requirements_ref"]):
        resolve_setup_ref(base, ref)
    for ref in existing["payload"]["image_lock_refs"]:
        resolve_setup_ref(base, ref)
    for name in ("config_ref", "role_recipe_ref", "resource_profile_ref",
                 "sample_contract_ref", "sample_case_set_ref", "evaluator_ref"):
        resolve_setup_ref(base, existing["payload"][name])
    return existing


def run_setup_preview(workspace: str | Path, profile: str, output: str | Path, *,
                      request_id: str | None = None,
                      clock: Callable[[], Any] | None = None) -> tuple[dict, dict | None]:
    """既存環境を変更せず、閉じた setup_plan だけを新規保存する。"""
    rid = _request_id(request_id, "setup-preview")
    try:
        base = _workspace(workspace)
        now = _now(clock)
        target = _child(base, output)
        existing = _existing_setup_plan(base, target, profile, now)
        if existing is not None:
            plan = existing
            plan_ref = content_ref(SETUP_PLAN_KIND, plan["id"], plan)
        else:
            plan = _build_setup_plan(base, profile, target, now=now)
            plan_ref = _save_artifact(
                base, target.relative_to(base), plan,
                lambda value: validate_plan(value, kind=SETUP_PLAN_KIND,
                                            payload_validator=validate_setup_payload,
                                            now=None))
    except OperationsError as exc:
        status, reason = _map_operation_error(exc)
        return _operation("ops.setup.preview", rid, status, reasons=(reason,)), None
    except (OSError, ContractError, TypeError, ValueError, KeyError) as exc:
        status, reason = _map_operation_error(exc)
        return _operation("ops.setup.preview", rid, status, reasons=(reason,)), None
    return _operation("ops.setup.preview", rid, "COMPLETED",
                      result_ref=plan_ref, checked_at=now), plan


def setup_preview(*args: Any, **kwargs: Any) -> tuple[dict, dict | None]:
    return run_setup_preview(*args, **kwargs)


def preview_setup(*args: Any, **kwargs: Any) -> tuple[dict, dict | None]:
    return run_setup_preview(*args, **kwargs)
