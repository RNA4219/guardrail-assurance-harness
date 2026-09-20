"""性能分冊の固定recipe補助CLI。

plan/measure/compareの補助結果だけを返し、常にci_eligible=falseとする。
入力は共通workspaceのcanonical artifactに限る。任意shell、任意import、
外部targetの文字列指定は受け付けない。
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import time
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gah.benchmark import (
    MeasurementUnavailable,
    OPERATION_EXIT_CODES,
    RECIPES,
    RECIPE_SURFACES,
    compare_observations,
    make_measurement_receipt,
    make_observation_artifact,
    make_plan,
    measure_once,
    validate_manifest,
    validate_measurement_receipt,
    validate_observation_artifact,
    validate_whole_run_requests,
)
from gah.contracts import (
    ContractError, decode_document, require_digest, require_id,
    require_object, require_ref,
)
from gah.docker_runner import DockerRunner as DockerFixtureRunner, operation_lock
from gah.guardrail_runner import GuardrailRunner as DockerGuardrailRunner
from gah.supervisor_checkpoint import Checkpoint, _plain_directory
from gah.productization import (
    content_ref,
    operation_result,
    read_document,
    validate_plan,
    write_document,
)
from gah.productization_journal import OperationJournal
from gah.wire import canonical_bytes


class _ParseError(ValueError):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise _ParseError(message)


def _request_id(argv: Sequence[str]) -> str | None:
    for index, value in enumerate(argv):
        if value == "--request-id" and index + 1 < len(argv):
            return argv[index + 1]
    return None


def _command_hint(argv: Sequence[str]) -> str:
    if argv and argv[0] in {"plan", "measure", "compare"}:
        return "benchmark." + argv[0]
    return "benchmark.invalid"


def _invalid(argv: Sequence[str], *, command: str | None = None) -> dict:
    actual = _command_hint(argv) if command is None else command
    request_id = _request_id(argv)
    if actual.endswith(".invalid"):
        request_id = None
    else:
        try:
            require_id(request_id)
        except (ContractError, TypeError, ValueError):
            request_id = None
    return operation_result(
        actual, request_id, "REJECTED", reasons=("INVALID_INPUT",),
        checked_at=int(time.time()),
    )


def _parser() -> _Parser:
    parser = _Parser(prog="gah_benchmark", description=__doc__)
    commands = parser.add_subparsers(dest="operation", required=True)

    plan = commands.add_parser("plan", help="manifestを検査して保存")
    plan.add_argument("--workspace", default=".")
    plan.add_argument("--input", required=True)
    plan.add_argument("--output", required=True)
    plan.add_argument("--request-id")

    measure = commands.add_parser("measure", help="固定recipeの一反復を計測")
    measure.add_argument("--workspace", default=".")
    measure.add_argument("--plan", required=True)
    measure.add_argument("--iteration-id", required=True)
    measure.add_argument("--recipe", choices=tuple(sorted(RECIPES)), required=True)
    measure.add_argument("--runtime", required=True,
                        help="既存AuthorityRuntimeのdeployment.jsonを含むrepo内ディレクトリ")
    measure.add_argument("--request", required=True,
                        help="queryでは固定authority request、whole_runでは固定run request JSON")
    measure.add_argument("--ci-request",
                        help="whole_runだけで使う固定ci_check request JSON")
    measure.add_argument("--series", choices=("baseline", "candidate"), default="candidate",
                        help="planのbaseline/candidate source refへの結び付け")
    measure.add_argument("--iteration", type=int, default=1)
    measure.add_argument("--warmness", choices=("cold", "warm"), default="cold")
    measure.add_argument("--output", required=True)
    measure.add_argument("--request-id")

    compare = commands.add_parser("compare", help="基準/候補観測を比較")
    compare.add_argument("--workspace", default=".")
    compare.add_argument("--plan", required=True)
    compare.add_argument("--observations", required=True)
    compare.add_argument("--output", required=True)
    compare.add_argument("--request-id")
    return parser


def _emit(result: dict) -> int:
    try:
        sys.stdout.write(json.dumps(
            result, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        ) + "\n")
        sys.stdout.flush()
    except (OSError, UnicodeError, TypeError, ValueError):
        return 2
    return result["exit_code"]


def _rid(args: Any, command: str, value: Any = None) -> str | None:
    if args.request_id is not None:
        try:
            require_id(args.request_id)
        except (ContractError, TypeError, ValueError):
            return None
        return args.request_id
    source = {"command": command, "value": value}
    return "benchmark-request-" + hashlib.sha256(
        canonical_bytes(source)
    ).hexdigest()[:40]


def _read(base: Path, path: str, command: str, request_id: str | None) -> tuple[dict | None, dict | None]:
    try:
        return read_document(base, path), None
    except ContractError as exc:
        code = getattr(exc, "code", "INVALID_INPUT")
        if code == "PATH_REJECTED":
            result = operation_result(
                command, request_id, "REJECTED", reasons=("INVALID_INPUT",),
            )
        elif code == "IO_ERROR":
            result = operation_result(
                command, request_id, "INCOMPLETE", reasons=("IO_ERROR",),
            )
        else:
            result = operation_result(
                command, request_id, "REJECTED", reasons=("INVALID_INPUT",),
            )
        return None, result
    except (TypeError, ValueError, OSError, UnicodeError):
        return None, operation_result(
            command, request_id, "INCOMPLETE", reasons=("IO_ERROR",),
        )


def _save(base: Path, path: str, value: dict, command: str,
          request_id: str | None) -> tuple[dict | None, dict | None]:
    try:
        return write_document(base, path, value), None
    except ContractError as exc:
        code = getattr(exc, "code", "IO_ERROR")
        if code in {"RESULT_CONFLICT", "BINDING_MISMATCH", "INVALID_INPUT"}:
            status = "REJECTED"
            reason = "INVALID_INPUT" if code == "INVALID_INPUT" else code
        else:
            status = "INCOMPLETE"
            reason = "IO_ERROR"
        return None, operation_result(
            command, request_id, status, reasons=(reason,),
        )
    except (TypeError, ValueError, OSError, UnicodeError):
        return None, operation_result(
            command, request_id, "INCOMPLETE", reasons=("IO_ERROR",),
        )


def _run_plan(args: Any, raw: Sequence[str]) -> int:
    command = "benchmark.plan"
    request_id = _rid(args, command, {"input": args.input, "output": args.output})
    if request_id is None:
        return _emit(_invalid(raw, command=command))
    base = Path(args.workspace).absolute()
    value, error = _read(base, args.input, command, request_id)
    if error is not None:
        return _emit(error)
    try:
        checked = validate_plan(
            value, kind="benchmark_plan", payload_validator=validate_manifest,
            now=None,
        )
    except (ContractError, TypeError, ValueError, KeyError):
        return _emit(operation_result(
            command, request_id, "REJECTED", reasons=("INVALID_INPUT",),
        ))
    ref, error = _save(base, args.output, checked, command, request_id)
    if error is not None:
        return _emit(error)
    return _emit(operation_result(
        command, request_id, "COMPLETED", result_ref=ref,
    ))



def _runtime_folder(value: str) -> Path:
    try:
        folder = Path(value).resolve()
        if (not folder.is_relative_to(ROOT)
                or not (folder / "deployment.json").is_file()):
            raise ContractError("RUNTIME_UNAVAILABLE")
        return folder
    except (OSError, RuntimeError):
        raise ContractError("RUNTIME_UNAVAILABLE") from None


_RUNTIME_PREFIX = re.compile(r"gah-authority-[0-9a-f]{32}\Z")
_RUNTIME_IMAGE = re.compile(r"sha256:[0-9a-f]{64}\Z")


def _runtime_identity(runtime_folder: Path) -> dict:
    """deployment全体ではなく、固定runtime identityだけを入力digestへ束ねる。"""
    try:
        value = decode_document((runtime_folder / "deployment.json").read_bytes())
        require_object(value, {"prefix", "image_id", "containers"})
        prefix = value["prefix"]
        image_id = value["image_id"]
        containers = value["containers"]
        if (type(prefix) is not str or _RUNTIME_PREFIX.fullmatch(prefix) is None
                or type(image_id) is not str or _RUNTIME_IMAGE.fullmatch(image_id) is None
                or type(containers) is not list
                or len(containers) > 10000
                or any(type(item) is not str for item in containers)
                or len(set(containers)) != len(containers)):
            raise ContractError("RUNTIME_UNAVAILABLE")
        require_digest(image_id[7:])
        return {
            "path": str(runtime_folder),
            "prefix": prefix,
            "image_id": image_id,
        }
    except ContractError:
        raise ContractError("RUNTIME_UNAVAILABLE") from None
    except (OSError, TypeError, ValueError, UnicodeError, RecursionError):
        raise ContractError("RUNTIME_UNAVAILABLE") from None


def _trusted_measure_principal() -> str:
    """入力JSON由来ではないOS認証主体をjournal鍵へ使う。"""
    try:
        from gah.operations import _trusted_principal
        principal = _trusted_principal()
        require_id(principal)
    except Exception:
        raise ContractError("AUTHORITY_REQUIRED") from None
    return principal


def _validate_recipe_request(recipe: str, request: Any) -> dict:
    if type(request) is not dict:
        raise ContractError("INVALID_INPUT")
    try:
        if recipe == "candidate":
            from gah.transition_authority import validate_request
            checked = validate_request(request)
            expected_action = "contract_candidate_read"
        else:
            from gah.regression_runs import validate_request
            checked = validate_request(request)
            expected_action = "ci_check"
    except Exception as error:
        code = getattr(error, "code", "INVALID_REQUEST")
        raise ContractError(
            "BINDING_MISMATCH" if code == "BINDING_MISMATCH"
            else "INVALID_INPUT",
        ) from None
    if checked.get("action") != expected_action:
        raise ContractError("INVALID_INPUT")
    return checked


def _bind_request_to_plan(recipe: str, request: dict, plan: dict) -> None:
    """CI照会requestの契約・baseline・対象を計画へ結び付ける。"""
    if recipe == "candidate":
        return
    manifest = plan["payload"]
    if (request["expected_contract_ref"] != manifest["contract_ref"]
            or request["expected_baseline_ref"] != manifest["baseline_ref"]
            or request["expected_target_refs"] != manifest["target_refs"]):
        raise ContractError("BINDING_MISMATCH")


def _request_for_authority(request: dict) -> dict:
    # measure_onceが保持する局所iteration/plan metadataはwire requestへ混ぜない。
    return {
        key: value for key, value in request.items()
        if key not in {"iteration_id", "plan_ref"}
    }


def _validate_candidate_response(value: Any, request: dict, action: str) -> dict:
    """候補queryの返却wireを固定field・要求・完全refで検証する。"""
    fields = {
        "schema_version", "kind", "action", "request_id", "ci_eligible",
        "candidate_id", "side", "candidate_ref", "prepared",
        "adoption_verified",
    }
    try:
        require_object(value, fields)
        require_id(value["request_id"])
        require_id(value["candidate_id"])
        require_ref(value["candidate_ref"])
    except (ContractError, KeyError, TypeError):
        raise ValueError("CANDIDATE_RESPONSE_INVALID") from None
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or value["kind"] != "evaluation_authority_result"
        or value["action"] != action
        or value["request_id"] != request["request_id"]
        or value["candidate_id"] != request["candidate_id"]
        or value["side"] != request["side"]
        or value["side"] not in {"old", "new"}
        or value["candidate_ref"]["kind"] != "contract_candidate"
        or value["candidate_ref"]["id"] != value["candidate_id"]
        or type(value["prepared"]) is not dict
        or value["adoption_verified"] is not False
        or value["ci_eligible"] is not False
    ):
        raise ValueError("CANDIDATE_RESPONSE_INVALID")
    return value


def _runtime_operation(recipe: str, runtime: Any, request: dict):
    """既存authorityの固定actionだけを呼ぶ。任意callable/import/shellは受けない。"""
    checked = _validate_recipe_request(recipe, request)
    action = checked["action"]

    def operation(_fixed_action: str, measured_request: dict) -> dict:
        wire_request = _request_for_authority(measured_request)
        if recipe == "candidate":
            from gah.contracts import require_id
            value = runtime.client(12004, wire_request)
            _validate_candidate_response(value, wire_request, action)
            return {"exit_code": 0}
        if recipe == "current_ci":
            from tools.gah_ci import response_exit_code
            value = runtime.client(12004, wire_request)
            return {"exit_code": response_exit_code(wire_request, value)}
        from tools.gah_report import build_report
        # build_report itself performs all fixed output/artifact ref and fresh-CI checks.
        value = build_report(runtime, wire_request, candidate=False)
        if type(value) is not dict or type(value.get("exit_code")) is not int:
            raise ValueError("REPORT_RESPONSE_INVALID")
        return {"exit_code": value["exit_code"]}

    return operation, checked


def _whole_run_folder(runtime_folder: Path, run_id: str) -> Path:
    """既存gah_runと同じrun folderを、symlinkなしで解決する。"""
    require_id(run_id)
    try:
        folder = _plain_directory(
            runtime_folder / "supervised" / hashlib.sha256(
                run_id.encode("utf-8")
            ).hexdigest()
        )
        if not folder.is_relative_to(ROOT):
            raise ContractError("RUNTIME_UNAVAILABLE")
        folder.mkdir(parents=True, exist_ok=True)
        return folder
    except ContractError:
        raise
    except (OSError, RuntimeError, TypeError, ValueError):
        raise ContractError("RUNTIME_UNAVAILABLE") from None


def _whole_run_runner(runner_kind: str, run_folder: Path) -> Any:
    """CI requestの用途だけから既存の固定runnerを選択する。"""
    if runner_kind == "fixture":
        return DockerFixtureRunner(
            ROOT / "config" / "fixture-runtime.lock.json",
            run_folder / "execution.sqlite",
        )
    if runner_kind == "guardrail":
        return DockerGuardrailRunner(run_folder / "execution.sqlite")
    raise ContractError("BINDING_MISMATCH")


def _whole_run_status(runtime: Any, run_id: str) -> bool:
    """既存 authority の run_status で同runの存在を fresh に確認する。"""
    require_id(run_id)
    status_request = {
        "schema_version": 1,
        "action": "run_status",
        "request_id": "benchmark-run-status-" + hashlib.sha256(
            run_id.encode("utf-8")
        ).hexdigest()[:40],
        "run_id": run_id,
    }
    value = runtime.client(12004, status_request)
    if type(value) is not dict:
        raise ValueError("RUN_STATUS_RESPONSE_INVALID")
    if value.get("kind") == "authority_error":
        require_object(value, {
            "schema_version", "kind", "reason", "ci_eligible",
        })
        if (
            type(value["schema_version"]) is not int
            or value["schema_version"] != 1
            or value["ci_eligible"] is not False
        ):
            raise ValueError("RUN_STATUS_RESPONSE_INVALID")
        if value["reason"] == "RUN_MISSING":
            return False
        raise ValueError("RUN_STATUS_UNAVAILABLE")
    require_object(value, {
        "schema_version", "kind", "action", "request_id",
        "ci_eligible", "run_id", "manifest", "plan",
        "contract_series_id", "contract_generation", "resource_snapshot",
    })
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or value["kind"] != "evaluation_authority_result"
        or value["action"] != "run_status"
        or value["request_id"] != status_request["request_id"]
        or value["ci_eligible"] is not False
        or value["run_id"] != run_id
        or type(value["manifest"]) is not dict
        or type(value["plan"]) is not dict
        or type(value["resource_snapshot"]) is not dict
        or type(value["contract_series_id"]) is not str
        or type(value["contract_generation"]) is not int
        or value["contract_generation"] < 0
    ):
        raise ValueError("RUN_STATUS_RESPONSE_INVALID")
    return True


def _whole_run_checkpoint_exists(run_folder: Path) -> bool:
    """既存Checkpointの保存領域を再利用せず、開始意図を検出する。"""
    checkpoint_folder = _plain_directory(run_folder / "checkpoints")
    try:
        if not checkpoint_folder.exists():
            return False
        if not checkpoint_folder.is_dir():
            raise ContractError("RUNTIME_UNAVAILABLE")
        checkpoint = Checkpoint(checkpoint_folder)
        # Supervisorが使う既知キーを正規APIで検証し、壊れた保存を成功扱いしない。
        for key in (
            "identity", "request-prepare", "response-prepare",
            "request-begin", "response-begin",
        ):
            if checkpoint.get(key) is not None:
                return True
        # 未知の残存fileや途中書込みも、再実行可能なfresh状態とはみなさない。
        for item in checkpoint_folder.iterdir():
            _plain_directory(item)
            return True
        return False
    except ContractError:
        raise
    except (OSError, RuntimeError, TypeError, ValueError):
        raise ContractError("RUNTIME_UNAVAILABLE") from None


def _begin_resource_probe(runtime: Any, plan: dict, request: dict):
    """固定providerを用意する。計測不能でも製品runの実行を捏造しない。"""
    from gah.resource_probe import ProbeSession
    from tools.authority_resource_probe import AuthorityResourceProbeProvider
    provider = AuthorityResourceProbeProvider(runtime, run_id=request["run_request"]["run_id"])
    session = ProbeSession(
        provider, plan_ref=content_ref("benchmark_plan", plan["id"], plan),
        source_ref=plan["source_ref"],
        request_digest=hashlib.sha256(canonical_bytes(request)).hexdigest(),
        expected_scope_ids=provider.expected_scope_ids,
    )
    session.begin()
    session.authority_provider = provider
    return session


def _finish_resource_probe(session, run_folder: Path, plan: dict, request: dict):
    try:
        result = session.end() if session is not None else None
    except Exception:
        result = None
    provider = getattr(session, "authority_provider", None)
    try:
        clients = provider.final_client_observations() if provider is not None else None
    except Exception:
        clients = None
    try:
        from tools.benchmark_worker_metrics import collect_worker_observations
        workers = collect_worker_observations(run_folder, request["run_request"])
    except Exception:
        workers = None
    artifact = {
        "schema_version": 1, "kind": "benchmark_resource_observation",
        "plan_ref": content_ref("benchmark_plan", plan["id"], plan),
        "source_ref": plan["source_ref"],
        "request_digest": hashlib.sha256(canonical_bytes(request)).hexdigest(),
        "coverage": "authority_and_host_only", "all_children_observed": False,
        "resource_interval": "run_through_current_ci_before_client_cleanup",
        "client_lifecycle_endpoints": clients,
        "worker_lifecycle_endpoints": workers,
        "valid_for_slo": False, "ci_eligible": False,
        "result": result,
        "missing": ["worker_lifecycle_counters", "true_group_peak", "copy_serialize_hash_db_counts"],
    }
    if result is None:
        artifact["missing"].append("resource_provider_unavailable")
    if clients is None:
        artifact["missing"].append("client_lifecycle_unavailable")
    if workers is None:
        artifact["missing"].append("worker_metrics_unavailable")
    Checkpoint(run_folder).put("resource-probe", artifact)
    return artifact


def _measure_whole_run_with_runtime(
    plan: dict,
    iteration_id: str,
    *,
    run_request: dict,
    ci_request: dict,
    runner_kind: str,
    runtime_folder: Path,
    iteration: int,
    warmness: str,
) -> tuple[dict, Exception | None]:
    """固定run requestをfresh Supervisor経路で一度実行し、CIとcleanupまで計時する。"""
    from tools.authority_runtime import AuthorityRuntime
    from tools.gah_ci import response_exit_code
    from tools.gah_run import execute

    run_folder = _whole_run_folder(runtime_folder, run_request["run_id"])
    transport = runtime_folder / "supervised-transport"
    close_errors: list[Exception] = []
    runtime = None
    runtime_closed = False

    with operation_lock(transport, "deployment", "supervisor"):
        try:
            runtime = AuthorityRuntime(
                runtime_folder, reuse_clients=True,
                keep_clients_running=True,
            )
            if _whole_run_status(runtime, run_request["run_id"]):
                raise ContractError("BINDING_MISMATCH")
            if _whole_run_checkpoint_exists(run_folder):
                raise ContractError("BINDING_MISMATCH")
            runner = _whole_run_runner(runner_kind, run_folder)

            def operation(_fixed_action: str, measured_request: dict) -> dict:
                nonlocal runtime_closed
                final_code = 2
                try:
                    resource_session = _begin_resource_probe(runtime, plan, measured_request)
                except Exception:
                    resource_session = None
                try:
                    supervised = execute(
                        runtime, runner, run_folder,
                        measured_request["run_request"], "run",
                    )
                    if (
                        type(supervised) is not dict
                        or type(supervised.get("exit_code")) is not int
                        or supervised["exit_code"] not in OPERATION_EXIT_CODES.values()
                    ):
                        raise ValueError("SUPERVISOR_RESPONSE_INVALID")
                    gate = supervised.get("gate")
                    if (
                        type(gate) is not dict
                        or gate.get("expected_manifest_ref")
                        != measured_request["ci_request"]["expected_manifest_ref"]
                    ):
                        raise ValueError("WHOLE_RUN_BINDING_MISMATCH")
                    ci_value = runtime.client(
                        12004, measured_request["ci_request"],
                    )
                    ci_code = response_exit_code(
                        measured_request["ci_request"], ci_value,
                    )
                    final_code = (
                        supervised["exit_code"]
                        if supervised["exit_code"] != 0 else ci_code
                    )
                finally:
                    # cgroup is removed when clients stop. Capture before cleanup;
                    # the enclosing whole-run wall interval still includes cleanup.
                    try:
                        _finish_resource_probe(resource_session, run_folder, plan, measured_request)
                    finally:
                        try:
                            runtime.close_clients()
                        except Exception as error:
                            close_errors.append(error)
                            final_code = 2
                        runtime_closed = True
                return {"exit_code": final_code}

            observation = measure_once(
                plan, iteration_id, recipe="whole_run", operation=operation,
                request={"run_request": run_request, "ci_request": ci_request},
                iteration=iteration, warmness=warmness,
                monotonic_clock=time.perf_counter_ns, now=int(time.time()),
            )
        finally:
            if runtime is not None and not runtime_closed:
                try:
                    runtime.close_clients()
                except Exception as error:
                    close_errors.append(error)
    return observation, (close_errors[0] if close_errors else None)


def _measure_with_runtime(
    plan: dict,
    iteration_id: str,
    *,
    recipe: str,
    request: dict,
    runtime_folder: Path,
    iteration: int,
    warmness: str,
) -> tuple[dict, Exception | None]:
    from tools.authority_runtime import AuthorityRuntime

    # runtimeを閉じ込めたadapterを作る。固定UID operator以外は利用しない。
    close_error: Exception | None = None
    observation = None
    runtime = None
    transport = runtime_folder / "supervised-transport"
    try:
        with operation_lock(transport, "deployment", "supervisor"):
            runtime = AuthorityRuntime(
                runtime_folder, reuse_clients=True, keep_clients_running=False,
            )
            try:
                operation, _ = _runtime_operation(recipe, runtime, request)
                observation = measure_once(
                    plan, iteration_id, recipe=recipe, operation=operation,
                    request=request, iteration=iteration, warmness=warmness,
                    monotonic_clock=time.perf_counter_ns, now=int(time.time()),
                )
            finally:
                try:
                    runtime.close_clients()
                except Exception as error:
                    close_error = error
    except Exception:
        if observation is not None:
            raise
        raise
    return observation, close_error


def _measure_input_digest(
    plan: dict,
    request: dict,
    runtime_folder: Path,
    *,
    recipe: str,
    series: str,
    iteration_id: str,
    iteration: int,
    warmness: str,
    output: str,
) -> str:
    """計画・要求本文とruntime/反復/outputを同じjournal入力へ固定する。"""
    value = {
        "plan": plan,
        "request": request,
        "runtime": _runtime_identity(runtime_folder),
        "recipe": recipe,
        "series": series,
        "iteration_id": iteration_id,
        "iteration": iteration,
        "warmness": warmness,
        "output": output,
    }
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _open_measure_journal(base: Path) -> OperationJournal:
    try:
        path = base / ".ga" / "benchmark-journal.sqlite"
        path.parent.mkdir(parents=True, exist_ok=True)
        return OperationJournal(path)
    except ContractError:
        raise
    except OSError:
        raise ContractError("IO_ERROR") from None


def _receipt_relpath(request_digest: str) -> str:
    require_digest(request_digest)
    return ".ga/benchmark-receipts/" + request_digest + ".json"


def _save_measurement_receipt(
    base: Path,
    request_digest: str,
    receipt: dict,
    command: str,
    request_id: str,
) -> tuple[dict | None, dict | None]:
    path = _receipt_relpath(request_digest)
    try:
        (base / ".ga" / "benchmark-receipts").mkdir(
            parents=True, exist_ok=True,
        )
    except OSError:
        return None, operation_result(
            command, request_id, "INCOMPLETE", reasons=("IO_ERROR",),
        )
    return _save(base, path, receipt, command, request_id)


def _recover_measurement(
    base: Path,
    output: str,
    plan: dict,
    source_ref: dict,
    iteration_id: str,
    request_id: str,
    request_digest: str,
    principal: str,
    journal: OperationJournal,
) -> dict | None:
    """完了receiptを検証してjournal.finishだけを回収し、runtimeを再実行しない。"""
    try:
        receipt_value = read_document(
            base, _receipt_relpath(request_digest),
        )
        receipt = validate_measurement_receipt(receipt_value)
        if receipt["request_digest"] != request_digest:
            return None
        observation = read_document(base, output)
        observation = validate_observation_artifact(observation)
        expected_plan_ref = content_ref("benchmark_plan", plan["id"], plan)
        if (
            observation["plan_ref"] != expected_plan_ref
            or observation["source_ref"] != source_ref
            or observation["iteration_id"] != iteration_id
        ):
            return None
        artifact_ref = content_ref(
            "benchmark_observation", observation["id"], observation,
        )
        if receipt["artifact_ref"] != artifact_ref:
            return None
        expected_receipt_id = (
            "benchmark-measurement-receipt-"
            + hashlib.sha256(canonical_bytes({
                "request_digest": request_digest,
                "artifact_ref": artifact_ref,
            })).hexdigest()[:40]
        )
        if receipt["id"] != expected_receipt_id:
            return None
        result = receipt["operation_result"]
        if (
            result["command"] != "benchmark.measure"
            or result["request_id"] != request_id
            or result["result_ref"] != artifact_ref
        ):
            return None
        return journal.finish(
            principal, "benchmark.measure", request_id,
            request_digest, result,
        )
    except (ContractError, OSError, TypeError, ValueError, UnicodeError):
        return None
    except Exception:
        return None


def _run_measure(args: Any, raw: Sequence[str]) -> int:
    command = "benchmark.measure"
    request_id = _rid(args, command, {
        "plan": args.plan, "iteration_id": args.iteration_id,
        "recipe": args.recipe, "series": args.series,
        "ci_request": args.ci_request,
        "iteration": args.iteration, "warmness": args.warmness,
        "runtime": args.runtime, "request": args.request,
        "output": args.output,
    })
    if request_id is None:
        return _emit(_invalid(raw, command=command))
    base = Path(args.workspace).absolute()
    plan, error = _read(base, args.plan, command, request_id)
    if error is not None:
        return _emit(error)
    request, error = _read(base, args.request, command, request_id)
    if error is not None:
        return _emit(error)
    ci_request = None
    if args.recipe == "whole_run":
        if args.ci_request is None:
            return _emit(operation_result(
                command, request_id, "REJECTED",
                reasons=("INVALID_INPUT",),
            ))
        ci_request, error = _read(base, args.ci_request, command, request_id)
        if error is not None:
            return _emit(error)
    elif args.ci_request is not None:
        return _emit(operation_result(
            command, request_id, "REJECTED",
            reasons=("INVALID_INPUT",),
        ))

    journal = None
    digest = None
    try:
        plan = validate_plan(
            plan, kind="benchmark_plan", payload_validator=validate_manifest,
            now=int(time.time()),
        )
        require_id(args.iteration_id)
        source_ref = plan["payload"][args.series + "_source_ref"]
        run_request = None
        runner_kind = None
        if args.recipe == "whole_run":
            run_request, ci_request, runner_kind = validate_whole_run_requests(
                plan, request, ci_request,
            )
            measure_request = {
                "run_request": run_request,
                "ci_request": ci_request,
            }
        else:
            request = _validate_recipe_request(args.recipe, request)
            _bind_request_to_plan(args.recipe, request, plan)
            measure_request = request
        runtime_folder = _runtime_folder(args.runtime)
        principal = _trusted_measure_principal()
        digest = _measure_input_digest(
            plan, measure_request, runtime_folder, recipe=args.recipe,
            series=args.series, iteration_id=args.iteration_id,
            iteration=args.iteration, warmness=args.warmness,
            output=args.output,
        )
        journal = _open_measure_journal(base)
        try:
            began = journal.begin(principal, command, request_id, digest)
        except ContractError as exc:
            code = getattr(exc, "code", "IO_ERROR")
            if code == "IDEMPOTENCY_CONFLICT":
                return _emit(operation_result(
                    command, request_id, "REJECTED",
                    reasons=("IDEMPOTENCY_CONFLICT",),
                ))
            return _emit(operation_result(
                command, request_id, "INCOMPLETE", reasons=("IO_ERROR",),
            ))
        if not began["created"]:
            if began["result"] is not None:
                return _emit(began["result"])
            recovered = _recover_measurement(
                base, args.output, plan, source_ref, args.iteration_id,
                request_id, digest, principal, journal,
            )
            if recovered is not None:
                return _emit(recovered)
            # 出力artifact/receiptが揃わない未知intentは再計測しない。
            return _emit(operation_result(
                command, request_id, "INCOMPLETE",
                reasons=("OPERATION_UNKNOWN",),
            ))
        try:
            if args.recipe == "whole_run":
                observation, close_error = _measure_whole_run_with_runtime(
                    plan, args.iteration_id,
                    run_request=run_request, ci_request=ci_request,
                    runner_kind=runner_kind, runtime_folder=runtime_folder,
                    iteration=args.iteration, warmness=args.warmness,
                )
            else:
                observation, close_error = _measure_with_runtime(
                    plan, args.iteration_id, recipe=args.recipe,
                    request=measure_request, runtime_folder=runtime_folder,
                    iteration=args.iteration, warmness=args.warmness,
                )
            artifact = make_observation_artifact(
                plan, source_ref, args.iteration_id, observation,
            )
        except MeasurementUnavailable:
            result = operation_result(
                command, request_id, "INCOMPLETE",
                reasons=("UNSUPPORTED_CAPABILITY",),
            )
        except ContractError as exc:
            code = getattr(exc, "code", "INVALID_INPUT")
            if code == "UNSUPPORTED_CAPABILITY":
                status, reasons = "INCOMPLETE", ("UNSUPPORTED_CAPABILITY",)
            elif code in {"RUNTIME_UNAVAILABLE", "DOCKER_UNAVAILABLE",
                          "OWNER_ACTIVE", "CLOCK_UNAVAILABLE"}:
                status, reasons = "INCOMPLETE", ("RUNTIME_UNAVAILABLE",)
            elif code == "AUTHORITY_REQUIRED":
                status, reasons = "INCOMPLETE", ("AUTHORITY_REQUIRED",)
            elif code == "BINDING_MISMATCH":
                status, reasons = "REJECTED", ("BINDING_MISMATCH",)
            elif code == "IO_ERROR":
                status, reasons = "INCOMPLETE", ("IO_ERROR",)
            else:
                status, reasons = "REJECTED", ("INVALID_INPUT",)
            result = operation_result(
                command, request_id, status, reasons=reasons,
            )
        except (OSError, UnicodeError, TypeError, ValueError, KeyError):
            # runtime初期化・固定adapter応答・時計の失敗は入力成功に変換しない。
            result = operation_result(
                command, request_id, "INCOMPLETE",
                reasons=("RUNTIME_UNAVAILABLE",),
            )
        else:
            ref, save_error = _save(
                base, args.output, artifact, command, request_id,
            )
            if save_error is not None:
                result = save_error
            else:
                reasons = []
                # close_clients失敗は観測が完了していても全体操作を未完了にする。
                if close_error is not None:
                    status = "INCOMPLETE"
                    reasons.append("RUNTIME_UNAVAILABLE")
                elif observation["operation_status"] == "CANCELLED":
                    status = "CANCELLED"
                    reasons.append("OPERATION_CANCELLED")
                elif observation["operation_status"] == "REJECTED":
                    status = "REJECTED"
                    reasons.append("OBSERVATION_MISSING")
                elif observation["operation_status"] == "INCOMPLETE":
                    status = "INCOMPLETE"
                    reasons.append("OBSERVATION_MISSING")
                else:
                    status = "COMPLETED"
                result = operation_result(
                    command, request_id, status, result_ref=ref,
                    reasons=tuple(dict.fromkeys(reasons)),
                )
                try:
                    receipt = make_measurement_receipt(
                        digest, ref, result, close_error is None,
                    )
                except (ContractError, TypeError, ValueError, UnicodeError):
                    receipt = None
                    receipt_error = operation_result(
                        command, request_id, "INCOMPLETE",
                        result_ref=ref, reasons=("IO_ERROR",),
                    )
                else:
                    _, receipt_error = _save_measurement_receipt(
                        base, digest, receipt, command, request_id,
                    )
                if receipt_error is not None:
                    # 観測は残るが回収用receiptがないため、完了扱いにしない。
                    result = operation_result(
                        command, request_id, "INCOMPLETE",
                        result_ref=ref, reasons=("IO_ERROR",),
                    )
        try:
            result = journal.finish(
                principal, command, request_id, digest, result,
            )
        except ContractError as exc:
            code = getattr(exc, "code", "IO_ERROR")
            if code in {"IDEMPOTENCY_CONFLICT", "RESULT_CONFLICT",
                        "BINDING_MISMATCH"}:
                result = operation_result(
                    command, request_id, "REJECTED",
                    result_ref=result.get("result_ref"), reasons=(code,),
                )
            else:
                result = operation_result(
                    command, request_id, "INCOMPLETE",
                    result_ref=result.get("result_ref"),
                    reasons=("IO_ERROR",),
                )
        except Exception:
            # finishの応答消失時はreceiptを残したまま次回の回収へ委ねる。
            result = operation_result(
                command, request_id, "INCOMPLETE",
                result_ref=result.get("result_ref"),
                reasons=("IO_ERROR",),
            )
        return _emit(result)
    except MeasurementUnavailable:
        return _emit(operation_result(
            command, request_id, "INCOMPLETE",
            reasons=("UNSUPPORTED_CAPABILITY",),
        ))
    except ContractError as exc:
        code = getattr(exc, "code", "INVALID_INPUT")
        if code == "PLAN_EXPIRED":
            status, reasons = "REJECTED", ("PLAN_EXPIRED",)
        elif code == "BINDING_MISMATCH":
            status, reasons = "REJECTED", ("BINDING_MISMATCH",)
        elif code == "AUTHORITY_REQUIRED":
            status, reasons = "INCOMPLETE", ("AUTHORITY_REQUIRED",)
        elif code in {"RUNTIME_UNAVAILABLE", "DOCKER_UNAVAILABLE",
                      "OWNER_ACTIVE", "CLOCK_UNAVAILABLE"}:
            status, reasons = "INCOMPLETE", ("RUNTIME_UNAVAILABLE",)
        elif code == "IO_ERROR":
            status, reasons = "INCOMPLETE", ("IO_ERROR",)
        else:
            status, reasons = "REJECTED", ("INVALID_INPUT",)
        return _emit(operation_result(
            command, request_id, status, reasons=reasons,
        ))
    except (OSError, UnicodeError, TypeError, ValueError, KeyError):
        return _emit(operation_result(
            command, request_id, "INCOMPLETE",
            reasons=("RUNTIME_UNAVAILABLE",),
        ))
    finally:
        if journal is not None:
            try:
                journal.close()
            except Exception:
                pass


def _read_observation_groups(value: Any) -> tuple[list[dict], list[dict]]:
    if type(value) is not dict or set(value) != {"baseline", "candidate"}:
        raise ContractError("INVALID_INPUT")
    baseline = value["baseline"]
    candidate = value["candidate"]
    if type(baseline) is not list or type(candidate) is not list:
        raise ContractError("OBSERVATION_MISSING")
    if not baseline or not candidate:
        raise ContractError("OBSERVATION_MISSING")
    for item in baseline + candidate:
        validate_observation_artifact(item)
    return baseline, candidate


def _run_compare(args: Any, raw: Sequence[str]) -> int:
    command = "benchmark.compare"
    request_id = _rid(args, command, {
        "plan": args.plan, "observations": args.observations,
        "output": args.output,
    })
    if request_id is None:
        return _emit(_invalid(raw, command=command))
    base = Path(args.workspace).absolute()
    plan, error = _read(base, args.plan, command, request_id)
    if error is not None:
        return _emit(error)
    observations, error = _read(base, args.observations, command, request_id)
    if error is not None:
        return _emit(error)
    try:
        baseline, candidate = _read_observation_groups(observations)
        result = compare_observations(
            plan, baseline, candidate, evidence_confirmed=False,
        )
    except ContractError as exc:
        code = getattr(exc, "code", "INVALID_INPUT")
        status = "INCOMPLETE" if code == "OBSERVATION_MISSING" else "REJECTED"
        reason = "OBSERVATION_MISSING" if status == "INCOMPLETE" else (
            "BINDING_MISMATCH" if code == "BINDING_MISMATCH" else "INVALID_INPUT"
        )
        return _emit(operation_result(command, request_id, status, reasons=(reason,)))
    except (TypeError, ValueError, KeyError):
        return _emit(operation_result(
            command, request_id, "REJECTED", reasons=("INVALID_INPUT",),
        ))
    ref, error = _save(base, args.output, result, command, request_id)
    if error is not None:
        return _emit(error)
    if result["status"] == "FAIL":
        status, reasons = "REJECTED", ("SLO_FAILED",)
    elif result["status"] == "INCONCLUSIVE":
        status = "INCOMPLETE"
        reasons = tuple(result["reasons"])
    else:
        status, reasons = "COMPLETED", ()
    return _emit(operation_result(
        command, request_id, status, result_ref=ref, reasons=reasons,
    ))


def main(argv: Sequence[str] | None = None) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    try:
        args = _parser().parse_args(values)
    except SystemExit as exc:
        return int(exc.code or 0)
    except _ParseError:
        return _emit(_invalid(values))
    try:
        if args.operation == "plan":
            return _run_plan(args, values)
        if args.operation == "measure":
            return _run_measure(args, values)
        if args.operation == "compare":
            return _run_compare(args, values)
    except (OSError, UnicodeError):
        command = _command_hint(values)
        return _emit(operation_result(
            command, _request_id(values), "INCOMPLETE",
            reasons=("IO_ERROR",),
        ))
    return _emit(_invalid(values))


if __name__ == "__main__":
    raise SystemExit(main())
