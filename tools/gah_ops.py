"""拡張運用の補助CLI。出力は共通10-field operation resultに固定する。"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import sys
import time
import uuid
from typing import Iterator, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gah.operations import _trusted_principal, run_doctor, run_setup_preview
from tools.setup_apply import apply as apply_setup
from gah.productization import (
    operation_result, read_document, workspace_path,
)
from gah.operations_retention import execute as execute_retention
from gah.operations_bundle import create as create_bundle


class _ParseError(ValueError):
    pass


class _CliRuntimeError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class _CliCleanupError(RuntimeError):
    def __init__(self, code: str = "STOP_UNCONFIRMED"):
        self.code = code if isinstance(code, str) else "STOP_UNCONFIRMED"
        super().__init__(self.code)


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise _ParseError(message)


def _request_id(argv: Sequence[str]) -> str | None:
    for index, value in enumerate(argv):
        if value == "--request-id" and index + 1 < len(argv):
            candidate = argv[index + 1]
            # operation_result が最終的にIDを検査するため、ここでは文字列だけ返す。
            return candidate
    return None


def _command_hint(argv: Sequence[str]) -> str:
    if argv and argv[0] == "doctor":
        return "ops.doctor"
    if (len(argv) >= 2 and argv[0] == "retention"
            and argv[1] in {"plan", "apply"}):
        return f"ops.retention.{argv[1]}"
    if (len(argv) >= 2 and argv[0] == "migrate"
            and argv[1] in {"preview", "apply"}):
        return f"ops.migrate.{argv[1]}"
    if (len(argv) >= 2 and argv[0] == "bundle"
            and argv[1] == "create"):
        return "ops.bundle.create"
    if len(argv) >= 2 and argv[0] == "setup" and argv[1] == "preview":
        return "ops.setup.preview"
    if len(argv) >= 2 and argv[0] == "setup" and argv[1] == "apply":
        return "ops.setup.apply"
    return "ops.invalid"


def _invalid(argv: Sequence[str]) -> dict:
    command = _command_hint(argv)
    rid = _request_id(argv)
    # 構文不成立時にrequest_idを認証・冪等キーへ使わない。ID形式もここで確定しない。
    if command == "ops.invalid":
        rid = None
    else:
        try:
            # malformed IDはnullへ落とす。
            from gah.contracts import require_id
            require_id(rid)
        except Exception:
            rid = None
    return operation_result(command, rid, "REJECTED", reasons=("INVALID_INPUT",),
                            checked_at=int(time.time()))


def _parser() -> _Parser:
    parser = _Parser(prog="gah_ops", description=__doc__)
    domains = parser.add_subparsers(dest="domain", required=True)

    doctor = domains.add_parser("doctor", help="bootstrap/ready診断")
    doctor.add_argument("--phase", choices=("bootstrap", "ready"), default="bootstrap")
    doctor.add_argument("--workspace", required=True)
    doctor.add_argument("--profile", choices=("sample-ci", "sample-llm"))
    doctor.add_argument("--runtime")
    doctor.add_argument("--contract-series-id")
    doctor.add_argument("--baseline-series-id")
    doctor.add_argument("--request-id")
    doctor.add_argument("--json", action="store_true")

    retention = domains.add_parser("retention", help="保持計画/適用")
    retention_actions = retention.add_subparsers(
        dest="retention_action", required=True)
    retention_plan = retention_actions.add_parser("plan", help="保持計画を照会")
    retention_apply = retention_actions.add_parser("apply", help="保持計画を適用")
    for retention_parser in (retention_plan, retention_apply):
        retention_parser.add_argument("--workspace", required=True)
        retention_parser.add_argument("--runtime", required=True)
        retention_parser.add_argument("--request", required=True)
        retention_parser.add_argument("--request-id")
        retention_parser.add_argument("--json", action="store_true")

    bundle = domains.add_parser("bundle", help="診断metadata bundle")
    bundle_actions = bundle.add_subparsers(dest="bundle_action", required=True)
    create = bundle_actions.add_parser("create", help="新規bundleを作成")
    create.add_argument("--workspace", required=True)
    create.add_argument("--runtime", required=True)
    create.add_argument("--run-id", required=True)
    create.add_argument("--output", required=True)
    create.add_argument("--request-id")
    create.add_argument("--json", action="store_true")

    migrate = domains.add_parser("migrate", help="SQLite migration")
    migration_actions = migrate.add_subparsers(
        dest="migration_action", required=True)
    migration_preview = migration_actions.add_parser(
        "preview", help="save migration plan")
    migration_preview.add_argument("--workspace", required=True)
    migration_preview.add_argument("--database", required=True)
    migration_preview.add_argument("--output", required=True)
    migration_preview.add_argument("--request-id")
    migration_preview.add_argument("--json", action="store_true")
    migration_apply = migration_actions.add_parser(
        "apply", help="apply migration plan")
    migration_apply.add_argument("--workspace", required=True)
    migration_apply.add_argument("--database", required=True)
    migration_apply.add_argument("--plan", required=True)
    migration_apply.add_argument("--request-id")
    migration_apply.add_argument("--json", action="store_true")
    setup = domains.add_parser("setup", help="導入計画")
    setup_actions = setup.add_subparsers(dest="setup_action", required=True)
    preview = setup_actions.add_parser("preview", help="setup planを保存")
    preview.add_argument("--workspace", required=True)
    preview.add_argument("--profile", choices=("sample-ci", "sample-llm"), required=True)
    preview.add_argument("--output", required=True)
    preview.add_argument("--request-id")
    preview.add_argument("--json", action="store_true")
    apply = setup_actions.add_parser("apply", help="setup planを適用")
    apply.add_argument("--workspace", required=True)
    apply.add_argument("--plan", required=True)
    apply.add_argument("--request-id")
    apply.add_argument("--json", action="store_true")
    return parser


@contextmanager
def _authority_runtime(workspace: str, runtime: str) -> Iterator[object]:
    """既存deploymentを固定lock下で開き、client回収を完了してから返す。"""
    try:
        folder = workspace_path(workspace, runtime)
        from gah.operations import _plain
        _plain(folder)
        deployment = folder / "deployment.json"
        _plain(deployment)
        if (not folder.is_dir() or folder.is_symlink()
                or not deployment.is_file() or deployment.is_symlink()):
            raise _CliRuntimeError("RUNTIME_UNAVAILABLE")
    except _CliRuntimeError:
        raise
    except Exception as error:
        raise _CliRuntimeError("RUNTIME_UNAVAILABLE") from error

    from gah.docker_runner import operation_lock
    from tools.authority_runtime import AuthorityRuntime
    # gah_run と同じ固定キーを全補助CLIで共有する。command別keyを作らない。
    with operation_lock(folder / "supervised-transport",
                       "deployment", "supervisor"):
        authority = AuthorityRuntime(folder)
        try:
            yield authority
        finally:
            try:
                authority.close_clients()
            except BaseException as error:
                code = getattr(error, "code", None)
                raise _CliCleanupError(code or "STOP_UNCONFIRMED") from error


@contextmanager
def _doctor_authority(workspace: str, runtime: str | None) -> Iterator[object | None]:
    """ready診断だけを既存AuthorityRuntimeのread clientへ接続する。"""
    if runtime is None:
        yield None
        return
    with _authority_runtime(workspace, runtime) as authority:
        yield authority


def _doctor_capacity_profile(args: argparse.Namespace) -> dict | None:
    profile = getattr(args, "profile", None)
    if profile is None:
        return None
    if args.phase != "bootstrap":
        raise _CliRuntimeError("INVALID_INPUT")
    try:
        from tools.setup_capacity import capacity_profile
        return capacity_profile(profile)
    except Exception as error:
        code = getattr(error, "code", None)
        if code in {"INVALID_INPUT", "PATH_REJECTED", "IO_ERROR"}:
            raise _CliRuntimeError(code) from error
        raise _CliRuntimeError("IO_ERROR") from error


def _run_doctor_cli(args: argparse.Namespace) -> dict:
    try:
        capacity = _doctor_capacity_profile(args)
    except _CliRuntimeError as error:
        status = (
            "REJECTED" if error.code in {"INVALID_INPUT", "PATH_REJECTED"}
            else "INCOMPLETE")
        return operation_result(
            "ops.doctor", _valid_cli_request_id(getattr(args, "request_id", None)),
            status, reasons=(error.code,))
    if args.phase != "ready" or args.runtime is None:
        result, _ = run_doctor(args.workspace, args.phase, runtime=args.runtime,
                               contract_series_id=args.contract_series_id,
                               baseline_series_id=getattr(args, "baseline_series_id", None),
                               capacity_profile=capacity,
                               request_id=args.request_id)
        return result
    try:
        with _doctor_authority(args.workspace, args.runtime) as authority:
            from tools.authority_storage_probe import observe_authority_storage
            storage = observe_authority_storage(authority)
            result, _ = run_doctor(
                args.workspace, args.phase, runtime=args.runtime, authority=authority,
                contract_series_id=args.contract_series_id,
                baseline_series_id=getattr(args, "baseline_series_id", None),
                capacity_profile=capacity, storage_observation=storage,
                request_id=args.request_id)
            return result
    except _CliCleanupError as error:
        code = error.code if error.code in {
            "STOP_UNCONFIRMED", "SETTLEMENT_PENDING", "OPERATION_UNKNOWN",
            "IO_ERROR",
        } else "STOP_UNCONFIRMED"
        return operation_result(
            "ops.doctor", _valid_cli_request_id(args.request_id),
            "INCOMPLETE", reasons=(code,))
    except Exception:
        result, _ = run_doctor(args.workspace, args.phase, runtime=args.runtime,
                               contract_series_id=args.contract_series_id,
                               baseline_series_id=getattr(args, "baseline_series_id", None),
                               capacity_profile=capacity,
                               request_id=args.request_id)
        return result


def _safe_request_id(request: object) -> str | None:
    if not isinstance(request, dict):
        return None
    candidate = request.get("request_id")
    try:
        from gah.contracts import require_id
        require_id(candidate)
    except Exception:
        return None
    return candidate


def _valid_cli_request_id(value: object) -> str | None:
    try:
        from gah.contracts import require_id
        require_id(value)
    except Exception:
        return None
    return value


def _run_migration_cli(args: argparse.Namespace) -> dict:
    """Delegate to the fixed offline migration API without printing artifacts."""
    action = getattr(args, "migration_action", None)
    if action not in {"preview", "apply"}:
        return operation_result(
            "ops.invalid", None, "REJECTED", reasons=("INVALID_INPUT",))
    command = "ops.migrate." + action
    explicit = getattr(args, "request_id", None)
    request_id = None if explicit is None else _valid_cli_request_id(explicit)
    if explicit is not None and request_id is None:
        return operation_result(
            command, None, "REJECTED", reasons=("INVALID_INPUT",))
    try:
        from gah import operations_migration
        if action == "preview":
            result, _ = operations_migration.preview(
                args.workspace, args.database, args.output,
                request_id=request_id,
            )
        else:
            result, _ = operations_migration.apply(
                args.workspace, args.database, args.plan,
                request_id=request_id,
            )
        return result
    except Exception as error:
        code = getattr(error, "code", None)
        if code in {
            "INVALID_INPUT", "PATH_REJECTED", "REFERENCE_MISMATCH",
            "BINDING_MISMATCH", "PLAN_EXPIRED", "IDEMPOTENCY_CONFLICT",
            "RESULT_CONFLICT", "SCHEMA_UNSUPPORTED", "STORE_MISSING",
        }:
            reason = "SCHEMA_UNSUPPORTED" if code == "STORE_MISSING" else code
            return operation_result(
                command, request_id, "REJECTED", reasons=(reason,))
        if code in {"AUTHORITY_DENIED", "AUTHORITY_REVOKED"}:
            return operation_result(
                command, request_id, "REJECTED", reasons=(code,))
        if code == "OPERATION_CANCELLED":
            return operation_result(
                command, request_id, "CANCELLED", reasons=(code,))
        return operation_result(
            command, request_id, "INCOMPLETE",
            reasons=("OPERATION_UNKNOWN",))


def _run_retention_cli(args: argparse.Namespace) -> dict:
    command = (
        "ops.retention.plan"
        if args.retention_action == "plan" else "ops.retention.apply")
    try:
        request = read_document(args.workspace, args.request)
    except Exception:
        return operation_result(command, None, "INCOMPLETE",
                                reasons=("IO_ERROR",))
    rid = _safe_request_id(request)
    explicit = getattr(args, "request_id", None)
    if explicit is not None:
        explicit_rid = _valid_cli_request_id(explicit)
        if explicit_rid is None:
            return operation_result(command, None, "REJECTED",
                                   reasons=("INVALID_INPUT",))
        if rid is None:
            return operation_result(command, explicit_rid, "REJECTED",
                                   reasons=("INVALID_INPUT",))
        if explicit_rid != rid:
            return operation_result(command, explicit_rid, "REJECTED",
                                   reasons=("IDEMPOTENCY_CONFLICT",))
    if rid is None:
        return operation_result(command, None, "REJECTED",
                                reasons=("INVALID_INPUT",))
    principal = _trusted_principal()
    if principal is None:
        return operation_result(command, rid, "INCOMPLETE",
                                reasons=("AUTHORITY_REQUIRED",))
    try:
        with _authority_runtime(args.workspace, args.runtime) as authority:
            return execute_retention(
                args.workspace, authority, request, command,
                authenticated_principal=principal,
            )
    except _CliCleanupError as error:
        code = error.code if error.code in {
            "STOP_UNCONFIRMED", "SETTLEMENT_PENDING", "OPERATION_UNKNOWN",
            "IO_ERROR",
        } else "STOP_UNCONFIRMED"
        return operation_result(command, rid, "INCOMPLETE", reasons=(code,))
    except Exception as error:
        # AuthorityRuntime生成/lock/read clientの不明は成功へ変換しない。
        code = getattr(error, "code", None) or str(error)
        if code in {"PATH_REJECTED", "RUNTIME_UNAVAILABLE"}:
            return operation_result(command, rid, "REJECTED",
                                   reasons=(code,))
        if code in {"AUTHORITY_DENIED", "AUTHORITY_REVOKED"}:
            return operation_result(command, rid, "REJECTED", reasons=(code,))
        if code in {"STOP_UNCONFIRMED", "SETTLEMENT_PENDING",
                    "OPERATION_UNKNOWN", "IO_ERROR"}:
            return operation_result(command, rid, "INCOMPLETE",
                                    reasons=(code,))
        return operation_result(command, rid, "INCOMPLETE",
                                reasons=("AUTHORITY_REQUIRED",))


def _run_bundle_cli(args: argparse.Namespace) -> dict:
    command = "ops.bundle.create"
    request_id = getattr(args, "request_id", None)
    if request_id is None:
        request_id = f"bundle-request-{uuid.uuid4().hex}"
    else:
        request_id = _valid_cli_request_id(request_id)
        if request_id is None:
            return operation_result(command, None, "REJECTED",
                                    reasons=("INVALID_INPUT",))
    principal = _trusted_principal()
    if principal is None:
        return operation_result(command, request_id, "INCOMPLETE",
                                reasons=("AUTHORITY_REQUIRED",))
    try:
        with _authority_runtime(args.workspace, args.runtime) as authority:
            return create_bundle(
                args.workspace, authority, args.run_id, args.output,
                request_id=request_id,
                authenticated_principal=principal,
            )
    except _CliCleanupError as error:
        code = error.code if error.code in {
            "STOP_UNCONFIRMED", "SETTLEMENT_PENDING", "OPERATION_UNKNOWN",
            "IO_ERROR",
        } else "STOP_UNCONFIRMED"
        return operation_result(command, request_id, "INCOMPLETE",
                                reasons=(code,))
    except Exception as error:
        code = getattr(error, "code", None) or str(error)
        if code in {"PATH_REJECTED", "RUNTIME_UNAVAILABLE"}:
            return operation_result(command, request_id, "REJECTED",
                                    reasons=(code,))
        if code in {"RESULT_CONFLICT", "CAPACITY_EXCEEDED",
                    "AUTHORITY_DENIED", "AUTHORITY_REVOKED",
                    "IDEMPOTENCY_CONFLICT"}:
            return operation_result(command, request_id, "REJECTED",
                                    reasons=(code,))
        return operation_result(command, request_id, "INCOMPLETE",
                                reasons=("OPERATION_UNKNOWN",))




def _run_setup_apply_cli(args: argparse.Namespace) -> dict:
    """setup_apply が所有するruntime準備・authority採択経路へ委譲する。"""
    try:
        return apply_setup(
            args.workspace, args.plan, request_id=args.request_id,
        )
    except Exception as error:
        # apply側の境界外例外も成功へ変換せず、request-idは安全に反映する。
        rid = _valid_cli_request_id(getattr(args, "request_id", None))
        code = getattr(error, "code", None) or str(error)
        if code in {"INVALID_INPUT", "PATH_REJECTED", "PLAN_EXPIRED",
                    "RESULT_CONFLICT", "IDEMPOTENCY_CONFLICT",
                    "STALE_OR_INVALIDATED", "CONDITION_MISMATCH"}:
            return operation_result("ops.setup.apply", rid, "REJECTED",
                                    reasons=(code,))
        if code in {"AUTHORITY_DENIED", "AUTHORITY_REVOKED"}:
            return operation_result("ops.setup.apply", rid, "REJECTED",
                                    reasons=(code,))
        if code in {"OPERATION_CANCELLED"}:
            return operation_result("ops.setup.apply", rid, "CANCELLED",
                                    reasons=(code,))
        return operation_result("ops.setup.apply", rid, "INCOMPLETE",
                                reasons=("OPERATION_UNKNOWN",))
def _emit(result: dict) -> int:
    try:
        sys.stdout.write(json.dumps(result, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")) + "\n")
        sys.stdout.flush()
    except (OSError, UnicodeError):
        return 2
    return result["exit_code"]


def main(argv: Sequence[str] | None = None) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    try:
        args = _parser().parse_args(values)
    except _ParseError:
        return _emit(_invalid(values))
    if args.domain == "doctor":
        return _emit(_run_doctor_cli(args))
    if args.domain == "migrate" and args.migration_action in {"preview", "apply"}:
        return _emit(_run_migration_cli(args))
    if args.domain == "retention" and args.retention_action in {"plan", "apply"}:
        return _emit(_run_retention_cli(args))
    if args.domain == "bundle" and args.bundle_action == "create":
        return _emit(_run_bundle_cli(args))
    if args.domain == "setup" and args.setup_action == "preview":
        result, _ = run_setup_preview(args.workspace, args.profile, args.output,
                                      request_id=args.request_id)
        return _emit(result)
    if args.domain == "setup" and args.setup_action == "apply":
        return _emit(_run_setup_apply_cli(args))
    return _emit(_invalid(values))


if __name__ == "__main__":
    raise SystemExit(main())
