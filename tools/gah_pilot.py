"""pilot補助操作の固定CLI。外部参照と製品runをこの入口で発行しない。"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sys
import time
from typing import Any, Callable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gah import pilot
from gah.docker_runner import operation_lock
from gah.contracts import (ContractError, MAX_INTEGER, require_id, require_ref,
                             require_uint)
from gah.productization import (
    REASONS,
    content_ref,
    operation_result,
    read_document,
    validate_operation_result,
    workspace_path,
    write_document,
)
from gah.productization_journal import OperationJournal
from gah.wire import canonical_bytes

try:
    from tools.authority_runtime import AuthorityRuntime, AuthorityRuntimeError
except (ImportError, OSError):
    AuthorityRuntime = None
    class AuthorityRuntimeError(ValueError):
        pass


_COMMANDS = {
    "register": "pilot.register",
    "plan": "pilot.plan",
    "status": "pilot.status",
    "report": "pilot.report",
    "execute": "pilot.execute",
    "import": "pilot.import",
}
_UIDS = {"manager": 12001, "validator": 12003, "operator": 12004}
_AUTHORITY_BASE_FIELDS = {
    "schema_version", "kind", "action", "request_id", "ci_eligible",
}
_AUTHORITY_REASONS = frozenset({
    "INVALID_REQUEST", "INVALID_ACTION", "UNSUPPORTED_VERSION",
    "PILOT_KIND_UNSUPPORTED", "PILOT_BINDING_MISMATCH", "REFERENCE_KIND",
    "REFERENCE_MISSING", "PILOT_ARTIFACT_CONFLICT", "GENERATION_CONFLICT",
    "AUTHORITY_DENIED", "AUTHORITY_REQUIRED", "AUTHORITY_REVOKED",
    "PLAN_EXPIRED", "STORAGE_CORRUPT", "CLOCK_ROLLBACK",
    "REQUEST_TOO_LARGE", "NOT_STARTED", "BINDING_MISMATCH",
})
_REPORT_WRAPPER_FIELDS = {
    "schema_version", "kind", "id", "pilot_id", "assessment",
    "history_pairs", "clean_changes", "observations", "required_categories",
    "source_refs", "baseline_ref", "scope", "cost", "human_intervention",
    "evidence_refs",
}
_HISTORY_FIELDS = {
    "history_pairs", "clean_changes", "per_repo_pairs",
    "covered_known_mandatory_misses", "covered_known_critical_misses",
    "real_regression_revalidated", "calibration_mismatches",
    "false_action_alerts", "false_critical_alerts", "missing", "unknown",
    "conflicts",
}
_LLM_FIELDS = {
    "observations", "required_categories", "missing", "unknown", "conflicts",
    "positive", "negative", "label_unknown", "oracle_independent",
    "measurement_reproduced", "all_measurement_obligations_met",
    "required_categories_have_each_100", "target_assurance",
}
_MAINTENANCE_FIELDS = {
    "observations", "missing", "unknown", "conflicts", "paired_observations",
    "uc_ci_pairs", "uc_llm_pairs", "baseline_median_ns", "candidate_median_ns",
    "resource_dimensions_nonincreasing", "human_interventions_nonincreasing",
    "oracle_equivalent",
}
_SOURCE_KINDS = pilot.PILOT_KINDS | {"snapshot_manifest"}
_EVIDENCE_KINDS = {"evidence", "pilot_observation", "pilot_result"}
_STATUS_KIND = "pilot_status"
_STATUS_FIELDS = {
    "schema_version", "kind", "id", "pilot_id", "plan_ref",
    "authority_request_id", "adopted", "valid", "generation",
    "validation_ref", "adoption_ref", "reasons", "metadata_only",
    "external_refs_verified", "authority_required", "product_run_authority",
    "checked_at",
}


class _ParseError(ValueError):
    """argparseの終了2を共通結果へ変換する。"""


class _PilotFailure(ValueError):
    def __init__(self, code: str, *, status: str = "REJECTED",
                 result_ref: dict[str, str] | None = None,
                 uncertain: bool = False):
        self.code = code
        self.status = status
        self.result_ref = result_ref
        # authorityへ送信後の応答不明を永続結果へ確定しないための印。
        self.uncertain = uncertain
        super().__init__(code)


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise _ParseError(message)


def _now() -> int:
    value = int(time.time())
    if not 0 <= value <= MAX_INTEGER:
        raise _PilotFailure("CLOCK_UNAVAILABLE", status="INCOMPLETE")
    return value


def _safe_id(value: Any) -> str | None:
    try:
        require_id(value)
    except ContractError:
        return None
    return value


def _request_value(value: Any, fallback: str) -> str | None:
    """明示された不正IDを既定IDへ置換せず、None時だけ既定値を使う。"""
    if value is None:
        return fallback
    return value if _safe_id(value) is not None else None


def _request_from_argv(argv: Sequence[str]) -> str | None:
    for index, value in enumerate(argv):
        if value == "--request-id" and index + 1 < len(argv):
            return _safe_id(argv[index + 1])
    return None


def _invalid(argv: Sequence[str]) -> dict[str, Any]:
    command = "pilot.invalid"
    request_id = None
    if argv and argv[0] in _COMMANDS:
        command = _COMMANDS[argv[0]]
        request_id = _request_from_argv(argv)
    return operation_result(
        command, request_id, "REJECTED", reasons=("INVALID_INPUT",), checked_at=_now(),
    )


def _map_code(code: Any, *, default: str = "AUTHORITY_REQUIRED") -> str:
    if type(code) is str and code in REASONS:
        return code
    mapping = {
        "INVALID_REQUEST": "INVALID_INPUT",
        "INVALID_ACTION": "INVALID_INPUT",
        "UNSUPPORTED_VERSION": "SCHEMA_UNSUPPORTED",
        "REQUEST_TOO_LARGE": "INVALID_INPUT",
        "PILOT_KIND_UNSUPPORTED": "UNSUPPORTED_CAPABILITY",
        "REFERENCE_KIND": "BINDING_MISMATCH",
        "REFERENCE_MISSING": "EVIDENCE_UNAVAILABLE",
        "PILOT_BINDING_MISMATCH": "BINDING_MISMATCH",
        "PILOT_ARTIFACT_CONFLICT": "RESULT_CONFLICT",
        "OBJECT_CONFLICT": "RESULT_CONFLICT",
        "GENERATION_CONFLICT": "BINDING_MISMATCH",
        "AUTHORITY_DENIED": "AUTHORITY_DENIED",
        "AUTHORITY_REVOKED": "AUTHORITY_REVOKED",
        "AUTHORITY_REQUIRED": "AUTHORITY_REQUIRED",
        "PLAN_EXPIRED": "PLAN_EXPIRED",
        "STORAGE_CORRUPT": "IO_ERROR",
        "CLOCK_ROLLBACK": "CLOCK_ROLLBACK",
        "DOCKER_UNAVAILABLE": "DOCKER_UNAVAILABLE",
        "CONFIG_MISMATCH": "DOCKER_UNAVAILABLE",
        "IMAGE_MISMATCH": "IMAGE_UNAVAILABLE",
        "IDENTITY_NOT_CONFIGURED": "IDENTITY_UNAVAILABLE",
        "BROKER_NOT_READY": "RUNTIME_UNAVAILABLE",
        "CLIENT_FAILED": "RUNTIME_UNAVAILABLE",
        "CLIENT_RESPONSE_INVALID": "RUNTIME_UNAVAILABLE",
        "DEPLOYMENT_CONFLICT": "RUNTIME_UNAVAILABLE",
        "INVALID_RUNTIME_MODE": "RUNTIME_UNAVAILABLE",
    }
    return mapping.get(code, default)


def _failure(code: Any, *, status: str | None = None,
             result_ref: dict[str, str] | None = None,
             uncertain: bool = False) -> _PilotFailure:
    mapped = _map_code(code, default="IO_ERROR" if code == "IO_ERROR" else "INVALID_INPUT")
    if status is None:
        status = "INCOMPLETE" if mapped in {
            "IO_ERROR", "OBSERVATION_MISSING", "EVIDENCE_UNAVAILABLE",
            "AUTHORITY_REQUIRED", "RUNTIME_UNAVAILABLE", "IDENTITY_UNAVAILABLE",
            "DOCKER_UNAVAILABLE", "IMAGE_UNAVAILABLE", "CLOCK_UNAVAILABLE",
            "CLOCK_ROLLBACK", "OPERATION_UNKNOWN",
        } else "REJECTED"
    return _PilotFailure(mapped, status=status, result_ref=result_ref,
                         uncertain=uncertain)


def _operation(command: str, request_id: str | None, status: str,
               *, result_ref: dict[str, str] | None = None,
               reasons: Sequence[str] = ()) -> dict[str, Any]:
    unique = list(dict.fromkeys(reasons))
    return operation_result(
        command, request_id, status, result_ref=result_ref, reasons=unique,
        checked_at=_now(),
    )


def _emit(result: Mapping[str, Any]) -> int:
    try:
        value = validate_operation_result(dict(result))
        sys.stdout.write(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")) + "\n")
        sys.stdout.flush()
    except (OSError, UnicodeError, ContractError):
        return 2
    return value["exit_code"]


def _workspace(value: Any) -> Path:
    try:
        base = Path(value).absolute()
        if not base.is_dir():
            raise _failure("PATH_REJECTED")
        return base
    except _PilotFailure:
        raise
    except (OSError, TypeError, ValueError):
        raise _failure("PATH_REJECTED")


def _ensure_parent(base: Path, relative: str | Path) -> Path:
    try:
        target = workspace_path(base, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        return workspace_path(base, relative)
    except ContractError as error:
        raise _failure(error.code) from None
    except OSError:
        raise _failure("IO_ERROR") from None


def _read(base: Path, relative: str | Path) -> dict[str, Any]:
    try:
        return read_document(base, relative)
    except ContractError as error:
        raise _failure(error.code) from None


def _preflight_output(base: Path, relative: str | Path,
                     value: dict[str, Any]) -> None:
    """外部brokerを呼ぶ前に、既存出力の異内容衝突だけを検査する。"""
    try:
        target = _ensure_parent(base, relative)
        if not target.exists():
            return
        if not target.is_file() or target.read_bytes() != canonical_bytes(value):
            raise _failure("RESULT_CONFLICT")
    except _PilotFailure:
        raise
    except (OSError, TypeError, ValueError, UnicodeError, RecursionError):
        raise _failure("IO_ERROR") from None


def _save(base: Path, relative: str | Path, value: dict[str, Any],
          validator: Callable[[Any], dict[str, Any]]) -> dict[str, str]:
    try:
        checked = validator(value)
        _ensure_parent(base, relative)
        ref = write_document(base, relative, checked)
        expected = content_ref(checked["kind"], checked["id"], checked)
        if ref != expected:
            raise _failure("BINDING_MISMATCH")
        return ref
    except _PilotFailure:
        raise
    except ContractError as error:
        raise _failure(error.code) from None
    except (OSError, TypeError, ValueError, UnicodeError, RecursionError):
        raise _failure("IO_ERROR") from None


@contextmanager
def _runtime(base: Path, relative: str | None):
    """既存deploymentを同じgah_run transport lock下で使い、clientを回収する。"""
    if AuthorityRuntime is None:
        raise _PilotFailure("RUNTIME_UNAVAILABLE", status="INCOMPLETE")
    path = relative if relative is not None else ".ga/pilot-runtime"
    try:
        target = workspace_path(base, path)
        deployment = target / "deployment.json"
        if (not target.is_dir() or deployment.is_symlink()
                or not deployment.is_file()):
            raise _failure("RUNTIME_UNAVAILABLE")
    except ContractError as error:
        raise _failure(error.code) from None
    except _PilotFailure:
        raise
    except OSError:
        raise _failure("RUNTIME_UNAVAILABLE") from None
    runtime = None
    cleanup_error = None
    try:
        with operation_lock(target / "supervised-transport",
                           "deployment", "supervisor"):
            try:
                runtime = AuthorityRuntime(target)
            except Exception as error:
                raise _PilotFailure(
                    _map_code(getattr(error, "code", None)), status="INCOMPLETE",
                ) from None
            try:
                yield runtime
            finally:
                if runtime is not None:
                    close_clients = getattr(runtime, "close_clients", None)
                    if callable(close_clients):
                        try:
                            close_clients()
                        except Exception as error:
                            cleanup_error = error
    finally:
        if cleanup_error is not None:
            raise _PilotFailure(
                "RUNTIME_UNAVAILABLE", status="INCOMPLETE", uncertain=True,
            ) from cleanup_error


def _trusted_principal() -> str | None:
    try:
        from gah.operations import _trusted_principal as trusted
        return trusted()
    except (ImportError, OSError, ValueError):
        return None


def _journalled(base: Path, command: str, request_id: str,
                payload: dict[str, Any],
                function: Callable[[], tuple[dict[str, str] | None, Sequence[str]]]
                ) -> dict[str, Any]:
    """外部処理の不明結果をjournalへ確定せず、同request照会で回収する。"""
    principal = _trusted_principal()
    if principal is None:
        return _operation(command, request_id, "INCOMPLETE",
                          reasons=("IDENTITY_UNAVAILABLE",))
    try:
        digest = hashlib.sha256(canonical_bytes(payload)).hexdigest()
    except (ContractError, TypeError, ValueError, UnicodeError, RecursionError):
        return _operation(command, request_id, "REJECTED",
                          reasons=("INVALID_INPUT",))
    journal_path = _ensure_parent(base, ".ga/pilot-journal.sqlite")
    journal = None

    def invoke() -> tuple[dict[str, Any], bool]:
        try:
            result_ref, reasons = function()
            return _operation(command, request_id, "COMPLETED",
                              result_ref=result_ref, reasons=reasons), False
        except _PilotFailure as error:
            return _operation(command, request_id, error.status,
                              result_ref=error.result_ref,
                              reasons=(error.code,)), error.uncertain
        except ContractError as error:
            failure = _failure(error.code)
            return _operation(command, request_id, failure.status,
                              result_ref=failure.result_ref,
                              reasons=(failure.code,)), failure.uncertain
        except (OSError, TypeError, ValueError, UnicodeError, RecursionError):
            # output障害は再配送可能な未完了として保持し、次回同requestで回収する。
            return _operation(command, request_id, "INCOMPLETE",
                              reasons=("IO_ERROR",)), False

    try:
        journal = OperationJournal(journal_path)
        try:
            began = journal.begin(principal, command, request_id, digest)
        except ContractError as error:
            return _operation(command, request_id, "REJECTED",
                              reasons=(_map_code(error.code),))
        if not began["created"] and began["result"] is not None:
            return began["result"]
        result, uncertain = invoke()
        # transport/cleanup不明時はjournalを未確定のまま残す。
        if uncertain:
            return result
        try:
            return journal.finish(principal, command, request_id, digest, result)
        except ContractError as error:
            return _operation(command, request_id, "INCOMPLETE",
                              result_ref=result.get("result_ref"),
                              reasons=(_map_code(error.code, default="IO_ERROR"),))
    except ContractError as error:
        return _operation(command, request_id, "INCOMPLETE",
                          reasons=(_map_code(error.code, default="IO_ERROR"),))
    finally:
        if journal is not None:
            journal.close()


def _validate_authority_result(request: Mapping[str, Any],
                               value: Any) -> dict[str, Any]:
    if type(value) is not dict:
        raise _failure("RUNTIME_UNAVAILABLE")
    action = request["action"]
    required = {
        "pilot_binding_register": {"artifact_ref", "metadata_only",
            "external_refs_verified", "authority_required",
            "product_run_authority"},
        "pilot_plan_register": {"artifact_ref", "metadata_only",
            "external_refs_verified", "authority_required",
            "product_run_authority"},
        "pilot_plan_validate": {"validation_ref", "validation",
            "permission_generation", "metadata_only",
            "external_refs_verified", "authority_required",
            "product_run_authority"},
        "pilot_plan_adopt": {"adoption_ref", "adoption", "adopted",
            "generation", "metadata_only", "external_refs_verified",
            "authority_required", "product_run_authority"},
        "pilot_plan_current": {"plan_ref", "adopted", "valid", "generation",
            "validation_ref", "adoption_ref", "reasons", "metadata_only",
            "external_refs_verified", "authority_required",
            "product_run_authority"},
    }[action]
    optional = ({"permission_generation"} if action in {
        "pilot_plan_adopt", "pilot_plan_current",
    } else set())
    if (type(value.get("schema_version")) is not int
            or value["schema_version"] != 1
            or value.get("kind") != "pilot_authority_result"
            or value.get("action") != action
            or value.get("request_id") != request["request_id"]
            or value.get("ci_eligible") is not False
            or not required <= set(value)
            or bool(set(value) - (_AUTHORITY_BASE_FIELDS | required | optional))):
        raise _failure("RUNTIME_UNAVAILABLE")
    for name in ("metadata_only", "authority_required",
                 "product_run_authority"):
        if type(value[name]) is not bool:
            raise _failure("RUNTIME_UNAVAILABLE")
    if value["metadata_only"] is not True or value["authority_required"] is not True:
        raise _failure("AUTHORITY_REQUIRED")
    if value["product_run_authority"] is not False:
        raise _failure("AUTHORITY_REQUIRED")
    if value["external_refs_verified"] is not False:
        raise _failure("BINDING_MISMATCH")
    if action in {"pilot_binding_register", "pilot_plan_register"}:
        try:
            require_ref(value["artifact_ref"])
        except ContractError:
            raise _failure("RUNTIME_UNAVAILABLE") from None
    elif action == "pilot_plan_validate":
        try:
            require_ref(value["validation_ref"])
            if value["validation_ref"]["kind"] != "pilot_plan_validation":
                raise ContractError
            if type(value["permission_generation"]) is not int:
                raise ContractError
        except ContractError:
            raise _failure("RUNTIME_UNAVAILABLE") from None
    elif action == "pilot_plan_adopt":
        try:
            require_ref(value["adoption_ref"])
            if value["adoption_ref"]["kind"] != "pilot_plan_adoption":
                raise ContractError
            if type(value["adopted"]) is not bool or value["adopted"] is not True:
                raise ContractError
            if type(value["generation"]) is not int or value["generation"] < 1:
                raise ContractError
        except (ContractError, KeyError, TypeError):
            raise _failure("RUNTIME_UNAVAILABLE") from None
    else:
        try:
            if value["plan_ref"] != request["plan_ref"]:
                raise ContractError
            if type(value["adopted"]) is not bool or type(value["valid"]) is not bool:
                raise ContractError
            if type(value["generation"]) is not int or value["generation"] < 0:
                raise ContractError
            if value["validation_ref"] is not None:
                require_ref(value["validation_ref"])
            if value["adoption_ref"] is not None:
                require_ref(value["adoption_ref"])
            reasons = value["reasons"]
            if (type(reasons) is not list
                    or any(type(item) is not str or item not in _AUTHORITY_REASONS
                           for item in reasons)
                    or len(reasons) != len(set(reasons))):
                raise ContractError
        except (ContractError, KeyError, TypeError):
            raise _failure("RUNTIME_UNAVAILABLE") from None
    return dict(value)


def _authority_call(runtime: Any, uid: int, request: dict[str, Any]) -> dict[str, Any]:
    try:
        value = runtime.client(uid, request)
    except Exception as error:
        code = getattr(error, "code", None)
        if not isinstance(code, str) and getattr(error, "args", ()):
            candidate = error.args[0]
            code = candidate if isinstance(candidate, str) else None
        raise _failure(code or "AUTHORITY_REQUIRED", uncertain=True) from None
    if type(value) is dict and value.get("kind") == "authority_error":
        reason = value.get("reason")
        mapped = _map_code(reason)
        status = "REJECTED" if mapped in {
            "INVALID_INPUT", "SCHEMA_UNSUPPORTED", "BINDING_MISMATCH",
            "AUTHORITY_DENIED", "AUTHORITY_REVOKED", "PLAN_EXPIRED",
            "RESULT_CONFLICT", "REFERENCE_MISMATCH",
        } else "INCOMPLETE"
        raise _PilotFailure(mapped, status=status)
    try:
        return _validate_authority_result(request, value)
    except _PilotFailure as error:
        # 応答が壊れていてもauthority側transactionの結果を推測しない。
        error.uncertain = True
        raise


def _validate_status(value: Any) -> dict[str, Any]:
    if type(value) is not dict or set(value) != _STATUS_FIELDS:
        raise ContractError("INVALID_INPUT")
    if (type(value["schema_version"]) is not int or value["schema_version"] != 1
            or value["kind"] != _STATUS_KIND):
        raise ContractError("INVALID_INPUT")
    require_id(value["id"])
    require_id(value["pilot_id"])
    require_ref(value["plan_ref"])
    require_id(value["authority_request_id"])
    for name in ("adopted", "valid", "metadata_only", "external_refs_verified",
                 "authority_required", "product_run_authority"):
        if type(value[name]) is not bool:
            raise ContractError("INVALID_INPUT")
    if type(value["generation"]) is not int or value["generation"] < 0:
        raise ContractError("INVALID_INPUT")
    for name, kind in (("validation_ref", "pilot_plan_validation"),
                       ("adoption_ref", "pilot_plan_adoption")):
        ref = value[name]
        if ref is not None:
            require_ref(ref)
            if ref["kind"] != kind:
                raise ContractError("BINDING_MISMATCH")
    reasons = value["reasons"]
    if (type(reasons) is not list
            or any(type(item) is not str or item not in _AUTHORITY_REASONS
                   for item in reasons)
            or len(reasons) != len(set(reasons))):
        raise ContractError("INVALID_INPUT")
    require_uint(value["checked_at"])
    if value["metadata_only"] is not True or value["authority_required"] is not True:
        raise ContractError("AUTHORITY_REQUIRED")
    if value["external_refs_verified"] is not False or value["product_run_authority"] is not False:
        raise ContractError("BINDING_MISMATCH")
    return dict(value)


def _plan(base: Path, relative: str | Path) -> tuple[dict[str, Any], dict[str, str]]:
    value = _read(base, relative)
    try:
        checked = pilot.validate_pilot_plan(value)
        return checked, content_ref("pilot_plan", checked["id"], checked)
    except ContractError as error:
        raise _failure(error.code) from None


def _binding(base: Path, relative: str | Path) -> tuple[dict[str, Any], dict[str, str]]:
    value = _read(base, relative)
    if type(value) is not dict or value.get("kind") not in {
        "project_binding", "evaluation_target_binding",
    }:
        raise _failure("PILOT_KIND_UNSUPPORTED")
    try:
        if value["kind"] == "project_binding":
            checked = pilot.validate_project_binding(value)
        else:
            checked = pilot.validate_evaluation_target_binding(value)
        return checked, content_ref(checked["kind"], checked["id"], checked)
    except ContractError as error:
        raise _failure(error.code) from None


def _document_refs(value: Any, name: str, kinds: set[str],
                   *, required: bool = False) -> list[dict[str, str]]:
    if value is None:
        if required:
            raise _failure("EVIDENCE_UNAVAILABLE")
        return []
    if type(value) is not list or len(value) > 1000:
        raise _failure("INVALID_INPUT")
    refs = []
    for item in value:
        try:
            require_ref(item)
        except ContractError:
            raise _failure("INVALID_INPUT") from None
        if item["kind"] not in kinds:
            raise _failure("BINDING_MISMATCH")
        refs.append(dict(item))
    if len({(item["kind"], item["id"], item["digest"]) for item in refs}) != len(refs):
        raise _failure("INVALID_INPUT")
    return refs


def _report_input(value: Any, requested_assessment: str | None,
                  plan_id: str) -> tuple[str, dict[str, Any], dict[str, Any]]:
    if type(value) is not dict:
        raise _failure("INVALID_INPUT")
    if "kind" in value:
        if set(value) - _REPORT_WRAPPER_FIELDS:
            raise _failure("INVALID_INPUT")
        if (type(value.get("schema_version")) is not int
                or value["schema_version"] != 1
                or value.get("kind") != "pilot_assessment_input"
                or _safe_id(value.get("id")) is None):
            raise _failure("INVALID_INPUT")
        assessment = value.get("assessment")
        if type(assessment) is not str or assessment not in {
            "history", "llm", "maintenance",
        }:
            raise _failure("INVALID_INPUT")
        if requested_assessment is not None and requested_assessment != assessment:
            raise _failure("INVALID_INPUT")
        if value.get("pilot_id") is not None and value["pilot_id"] != plan_id:
            raise _failure("BINDING_MISMATCH")
        raw = {}
        allowed = {
            "history": _HISTORY_FIELDS, "llm": _LLM_FIELDS,
            "maintenance": _MAINTENANCE_FIELDS,
        }[assessment]
        for name in allowed:
            if name in value:
                raw[name] = value[name]
        source_refs = _document_refs(value.get("source_refs"), "source_refs",
                                     _SOURCE_KINDS)
        baseline = value.get("baseline_ref")
        if baseline is not None:
            baseline = _document_refs([baseline], "baseline_ref",
                                      {"pilot_baseline", "baseline"},
                                      required=True)[0]
        evidence_refs = _document_refs(value.get("evidence_refs"),
                                       "evidence_refs", _EVIDENCE_KINDS)
        for name in ("scope", "cost", "human_intervention"):
            if name in value and type(value[name]) is not dict:
                raise _failure("INVALID_INPUT")
        return assessment, raw, {
            "source_refs": source_refs, "baseline_ref": baseline,
            "scope": value.get("scope", {}),
            "cost": value.get("cost", {}),
            "human_intervention": value.get("human_intervention", {}),
            "evidence_refs": evidence_refs,
        }
    assessment = requested_assessment
    if assessment is None:
        raise _failure("INVALID_INPUT")
    allowed = {
        "history": _HISTORY_FIELDS, "llm": _LLM_FIELDS,
        "maintenance": _MAINTENANCE_FIELDS,
    }[assessment]
    if set(value) - allowed or not set(value) & allowed:
        raise _failure("INVALID_INPUT")
    return assessment, dict(value), {
        "source_refs": [], "baseline_ref": None, "scope": {},
        "cost": {}, "human_intervention": {}, "evidence_refs": [],
    }


def _result_id(request_id: str) -> str:
    return "pilot-result-" + hashlib.sha256(request_id.encode("utf-8")).hexdigest()[:32]


def run_register(base_value: str, binding_path: str, output_path: str,
                 runtime_path: str | None = None,
                 request_id: str | None = None) -> dict[str, Any]:
    base = _workspace(base_value)
    binding, binding_ref = _binding(base, binding_path)
    rid = _request_value(request_id, binding["id"])
    if rid is None:
        return _operation("pilot.register", None, "REJECTED",
                          reasons=("INVALID_INPUT",))
    request = {
        "schema_version": 1, "action": "pilot_binding_register",
        "request_id": rid, "document": binding,
    }
    payload = {"operation": "register", "request": request,
               "binding_path": str(binding_path), "runtime_path": runtime_path,
               "output_path": str(output_path)}
    def work():
        _preflight_output(base, output_path, binding)
        with _runtime(base, runtime_path) as runtime:
            response = _authority_call(runtime, _UIDS["manager"], request)
        if response["artifact_ref"] != binding_ref:
            raise _failure("BINDING_MISMATCH", result_ref=binding_ref)
        output_ref = _save(base, output_path, binding,
                           pilot.validate_pilot_artifact)
        if output_ref != binding_ref:
            raise _failure("BINDING_MISMATCH", result_ref=binding_ref)
        return output_ref, ()
    return _journalled(base, "pilot.register", rid, payload, work)


def run_plan(base_value: str, input_path: str, output_path: str,
             runtime_path: str | None = None,
             request_id: str | None = None) -> dict[str, Any]:
    base = _workspace(base_value)
    plan, plan_ref = _plan(base, input_path)
    rid = _request_value(request_id, plan["id"])
    if rid is None:
        return _operation("pilot.plan", None, "REJECTED",
                          reasons=("INVALID_INPUT",))
    request = {
        "schema_version": 1, "action": "pilot_plan_register",
        "request_id": rid, "document": plan,
    }
    payload = {"operation": "plan", "request": request,
               "input_path": str(input_path), "runtime_path": runtime_path,
               "output_path": str(output_path)}
    def work():
        _preflight_output(base, output_path, plan)
        with _runtime(base, runtime_path) as runtime:
            response = _authority_call(runtime, _UIDS["manager"], request)
        if response["artifact_ref"] != plan_ref:
            raise _failure("BINDING_MISMATCH", result_ref=plan_ref)
        output_ref = _save(base, output_path, plan, pilot.validate_pilot_plan)
        if output_ref != plan_ref:
            raise _failure("BINDING_MISMATCH", result_ref=plan_ref)
        return output_ref, ()
    return _journalled(base, "pilot.plan", rid, payload, work)


def run_status(base_value: str, plan_path: str, runtime_path: str | None = None,
               request_id: str | None = None) -> dict[str, Any]:
    base = _workspace(base_value)
    plan, plan_ref = _plan(base, plan_path)
    rid = _request_value(request_id, _result_id(plan["id"]))
    if rid is None:
        return _operation("pilot.status", None, "REJECTED",
                          reasons=("INVALID_INPUT",))
    request = {
        "schema_version": 1, "action": "pilot_plan_current",
        "request_id": "pilot-current-" + hashlib.sha256(rid.encode("utf-8")).hexdigest()[:32],
        "plan_ref": plan_ref,
    }
    try:
        # statusはread-onlyでも毎回brokerへfresh照会し、operation journalを再利用しない。
        with _runtime(base, runtime_path) as runtime:
            current = _authority_call(runtime, _UIDS["operator"], request)
        if current["plan_ref"] != plan_ref:
            raise _failure("BINDING_MISMATCH", result_ref=plan_ref)
        # current.validはmetadataの有効性だけであり、製品run権限を意味しない。
        status_id = "pilot-status-" + hashlib.sha256(
            canonical_bytes({"plan_ref": plan_ref, "current": current})
        ).hexdigest()[:32]
        artifact = {
            "schema_version": 1, "kind": _STATUS_KIND, "id": status_id,
            "pilot_id": plan["id"], "plan_ref": plan_ref,
            "authority_request_id": current["request_id"],
            "adopted": current["adopted"], "valid": current["valid"],
            "generation": current["generation"],
            "validation_ref": current["validation_ref"],
            "adoption_ref": current["adoption_ref"],
            "reasons": list(current["reasons"]),
            "metadata_only": current["metadata_only"],
            "external_refs_verified": current["external_refs_verified"],
            "authority_required": current["authority_required"],
            "product_run_authority": current["product_run_authority"],
            "checked_at": _now(),
        }
        status_path = ".ga/pilot-status/" + status_id + ".json"
        result_ref = _save(base, status_path, artifact, _validate_status)
        return _operation("pilot.status", rid, "COMPLETED",
                          result_ref=result_ref)
    except _PilotFailure as error:
        return _operation("pilot.status", rid, error.status,
                          result_ref=error.result_ref, reasons=(error.code,))
    except ContractError as error:
        failure = _failure(error.code)
        return _operation("pilot.status", rid, failure.status,
                          result_ref=failure.result_ref, reasons=(failure.code,))
    except (OSError, TypeError, ValueError, UnicodeError, RecursionError):
        return _operation("pilot.status", rid, "INCOMPLETE",
                          reasons=("IO_ERROR",))


def run_report(base_value: str, plan_path: str, observations_path: str,
               output_path: str | None = None,
               assessment: str | None = None,
               request_id: str | None = None) -> dict[str, Any]:
    base = _workspace(base_value)
    plan, plan_ref = _plan(base, plan_path)
    observation_document = _read(base, observations_path)
    assessment_name, input_value, metadata = _report_input(
        observation_document, assessment, plan["id"],
    )
    rid = _request_value(request_id, _result_id(
        plan["id"] + "-" + assessment_name + "-" + str(observations_path),
    ))
    if rid is None:
        return _operation("pilot.report", None, "REJECTED",
                          reasons=("INVALID_INPUT",))
    source_refs = metadata["source_refs"] or [plan_ref]
    for ref in source_refs:
        if ref["kind"] not in _SOURCE_KINDS:
            raise _failure("BINDING_MISMATCH")
    result_id = _result_id(rid)
    path = output_path or (".ga/pilot-results/" + result_id + ".json")
    payload = {
        "operation": "report", "request": {
            "plan_ref": plan_ref, "assessment": assessment_name,
            "observations": input_value,
        },
        "observations_path": str(observations_path), "output_path": str(path),
        "source_refs": metadata["source_refs"], "baseline_ref": metadata["baseline_ref"],
        "scope": metadata["scope"], "cost": metadata["cost"],
        "human_intervention": metadata["human_intervention"],
        "evidence_refs": metadata["evidence_refs"],
        "assessment_document": observation_document,
    }
    def work():
        assessors = {
            "history": pilot.assess_history,
            "llm": pilot.assess_llm,
            "maintenance": pilot.assess_maintenance,
        }
        try:
            analysis = dict(assessors[assessment_name](input_value))
            # CLIは外部ref本文、独立oracle、plan採択を照合できないため、
            # 算術のcountsを保持したまま受入statusを確定しない。
            analysis["pac_status"] = "INCONCLUSIVE"
            missing = list(analysis.get("missing", []))
            for item in ("external_refs_verified", "plan_adoption_receipt",
                         "source_provenance", "oracle_provenance"):
                if item not in missing:
                    missing.append(item)
            analysis["missing"] = missing
            limitations = list(analysis.get("limitations", []))
            if "CLI_EXTERNAL_VERIFICATION_REQUIRED" not in limitations:
                limitations.append("CLI_EXTERNAL_VERIFICATION_REQUIRED")
            analysis["limitations"] = limitations
            artifact = pilot.build_pilot_result(
                analysis, pilot_id=plan["id"], plan_revision_ref=plan_ref,
                result_id=result_id, source_refs=source_refs,
                baseline_ref=metadata["baseline_ref"], scope=metadata["scope"],
                cost=metadata["cost"], human_intervention=metadata["human_intervention"],
                evidence_refs=metadata["evidence_refs"], created_at=_now(),
            )
        except ContractError as error:
            raise _failure(error.code) from None
        ref = _save(base, path, artifact, pilot.validate_pilot_result)
        raise _failure("EVIDENCE_UNAVAILABLE", status="INCOMPLETE",
                       result_ref=ref)
    return _journalled(base, "pilot.report", rid, payload, work)


def run_execute(base_value: str, plan_path: str, runtime_path: str | None = None,
                request_id: str | None = None) -> dict[str, Any]:
    base = _workspace(base_value)
    plan, plan_ref = _plan(base, plan_path)
    rid = _request_value(request_id, _result_id(plan["id"]))
    if rid is None:
        return _operation("pilot.execute", None, "REJECTED",
                          reasons=("INVALID_INPUT",))
    request = {
        "schema_version": 1, "action": "pilot_plan_current",
        "request_id": "pilot-current-" + hashlib.sha256(rid.encode("utf-8")).hexdigest()[:32],
        "plan_ref": plan_ref,
    }
    payload = {"operation": "execute", "plan_ref": plan_ref,
               "plan_path": str(plan_path), "runtime_path": runtime_path,
               "request_id": rid, "current_request": request}
    def work():
        with _runtime(base, runtime_path) as runtime:
            current = _authority_call(runtime, _UIDS["operator"], request)
        if current["plan_ref"] != plan_ref:
            raise _failure("BINDING_MISMATCH", result_ref=plan_ref)
        if current["external_refs_verified"] is not False or current["product_run_authority"] is not False:
            raise _failure("BINDING_MISMATCH", result_ref=plan_ref)
        if current["valid"] is not True or current["adopted"] is not True:
            reasons = current["reasons"]
            if "PLAN_EXPIRED" in reasons:
                raise _failure("PLAN_EXPIRED", status="REJECTED",
                               result_ref=plan_ref)
            raise _failure("AUTHORITY_REQUIRED", status="REJECTED",
                           result_ref=plan_ref)
        # metadataが有効でも製品run authorityは未接続なので開始しない。
        raise _failure("AUTHORITY_REQUIRED", status="INCOMPLETE",
                       result_ref=plan_ref)
    return _journalled(base, "pilot.execute", rid, payload, work)


def run_import(base_value: str, input_path: str, output_path: str,
               request_id: str | None = None) -> dict[str, Any]:
    """利用者提供JSONの算術正規化。独立検査・製品評価の権限は持たない。"""
    from gah.pilot_inputs import validate_import, import_document
    base = _workspace(base_value)
    try:
        document = validate_import(_read(base, input_path), now=_now())
    except ContractError as error:
        raise _failure(error.code) from None
    artifact = import_document(document)
    rid = artifact["id"] if request_id is None else request_id
    try:
        require_id(rid)
        target = workspace_path(base, output_path)
        if target == workspace_path(base, input_path):
            raise _failure("PATH_REJECTED")
    except ContractError as error:
        raise _failure(error.code) from None
    payload = {"input_ref": content_ref(document["kind"], document["id"], document),
               "output": target.relative_to(base.resolve()).as_posix()}
    def work():
        # checked_atは入力時点の決定的記録。現在の認証/鮮度は発行しない。
        ref = _save(base, output_path, artifact, lambda value: value)
        return ref, ()
    return _journalled(base, "pilot.import", rid, payload, work)


def _parser() -> _Parser:
    parser = _Parser(prog="gah_pilot", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    register = sub.add_parser("register", help="固定bindingをbrokerへ登録")
    register.add_argument("--workspace", required=True)
    register.add_argument("--binding", required=True)
    register.add_argument("--output", required=True)
    register.add_argument("--runtime")
    register.add_argument("--request-id")

    plan = sub.add_parser("plan", help="固定pilot planをbrokerへ登録")
    plan.add_argument("--workspace", required=True)
    plan.add_argument("--input", required=True)
    plan.add_argument("--output", required=True)
    plan.add_argument("--runtime")
    plan.add_argument("--request-id")

    status = sub.add_parser("status", help="metadataのfresh current照会")
    status.add_argument("--workspace", required=True)
    status.add_argument("--plan", required=True)
    status.add_argument("--runtime")
    status.add_argument("--request-id")

    report = sub.add_parser("report", help="個別観測からpilot_resultを生成")
    report.add_argument("--workspace", required=True)
    report.add_argument("--plan", required=True)
    report.add_argument("--observations", required=True)
    report.add_argument("--output")
    report.add_argument("--assessment", choices=("history", "llm", "maintenance"))
    report.add_argument("--request-id")

    importer = sub.add_parser("import", help="offline評価結果を検証待ちartifactへ正規化")
    importer.add_argument("--workspace", required=True)
    importer.add_argument("--input", required=True)
    importer.add_argument("--output", required=True)
    importer.add_argument("--request-id")

    execute = sub.add_parser("execute", help="採択metadataをfresh照会")
    execute.add_argument("--workspace", required=True)
    execute.add_argument("--plan", required=True)
    execute.add_argument("--runtime")
    execute.add_argument("--request-id")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    try:
        args = _parser().parse_args(values)
    except _ParseError:
        return _emit(_invalid(values))
    try:
        if args.command == "register":
            result = run_register(args.workspace, args.binding, args.output,
                                  args.runtime, args.request_id)
        elif args.command == "plan":
            result = run_plan(args.workspace, args.input, args.output,
                              args.runtime, args.request_id)
        elif args.command == "status":
            result = run_status(args.workspace, args.plan, args.runtime,
                                args.request_id)
        elif args.command == "report":
            result = run_report(args.workspace, args.plan, args.observations,
                                args.output, args.assessment, args.request_id)
        elif args.command == "import":
            result = run_import(args.workspace, args.input, args.output, args.request_id)
        elif args.command == "execute":
            result = run_execute(args.workspace, args.plan, args.runtime,
                                 args.request_id)
        else:
            result = _invalid(values)
    except _PilotFailure as error:
        command = _COMMANDS.get(getattr(args, "command", ""), "pilot.invalid")
        request_id = _safe_id(getattr(args, "request_id", None))
        result = _operation(command, request_id, error.status,
                            result_ref=error.result_ref, reasons=(error.code,))
    except (ContractError, OSError, TypeError, ValueError, UnicodeError,
            RecursionError):
        command = _COMMANDS.get(getattr(args, "command", ""), "pilot.invalid")
        request_id = _safe_id(getattr(args, "request_id", None))
        result = _operation(command, request_id, "INCOMPLETE",
                            reasons=("IO_ERROR",))
    return _emit(result)


if __name__ == "__main__":
    raise SystemExit(main())
