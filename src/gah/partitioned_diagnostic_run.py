"""Execute one fixed, non-billed fixture entry for an open v2 diagnostic run.

This is a bounded diagnostic consumer, not a Supervisor replacement: it deliberately
does not finalize evidence, close the diagnostic evidence state, admit a run, or call CI.
The caller must supply the exact response from a successful ``run_begin_v2`` and the
manifest used for that begin. Authority actions still recheck currentness on every call.
"""
from __future__ import annotations

import hashlib
import copy
from pathlib import Path
import time
from typing import Any

from .contracts import ContractError, require_id, require_uint
from .docker_runner import DockerRunner, RunnerError, operation_lock
from .execution_journal import ExecutionJournal, JournalError
from .execution_profiles import check_plan, expected as expected_execution
from .normalized import validate_binding
from .partitioned_run_store import validate_request as validate_v2_request
from .partitioned_run_contracts import validate_partitioned_run_manifest
from .partitioned_trial_plan import INDEX_KIND, MAX_SEGMENTS, restore_trial_plan
from .run_contracts import content_ref
from . import resource_authority
from .supervisor_checkpoint import Checkpoint, CheckpointError, _plain_directory
from .supervised_run import OPERATOR, VALIDATOR, SupervisorError
from .wire import canonical_bytes

SCENARIO = "constraint:C01:good"
ZERO_USAGE = {"input_tokens": 0, "output_tokens": 0, "cost_usd": "0"}
_FALSE_FLAGS = ("authority_connected", "resource_closure_verified", "baseline_freshness_verified",
                "adoption_verified", "admission_verified", "ci_eligible")
_SELECTOR_FIELDS = {"obligation_id", "case_id", "trial_id", "variant"}
_RUN_VIEW_FIELDS = {
    "schema_version", "kind", "run_id", "bundle", "bundle_digest", "execution_profile",
    "baseline_context", "state", "hold_reason", "aggregate_digest", "decision_digest",
    "diagnostic_finalized", "diagnostic_finalized_at", "authority_connected",
    "resource_closure_verified", "baseline_freshness_verified", "adoption_verified", "ci_eligible", "binding",
}
_RUN_FALSE_FLAGS = ("authority_connected", "resource_closure_verified",
                    "baseline_freshness_verified", "adoption_verified", "ci_eligible")


class PartitionedDiagnosticError(ValueError):
    """Fixed, non-sensitive failure from the one-entry diagnostic consumer."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise PartitionedDiagnosticError(code)


def _require_false_flags(value: dict[str, Any]) -> None:
    if any(value.get(name) is not False for name in _FALSE_FLAGS):
        _fail("AUTHORITY_RESPONSE_INVALID")


def _client(runtime: Any, uid: int, request: dict[str, Any], *, kind: str,
            action: str, result_request_id: str) -> dict[str, Any]:
    try:
        response = runtime.client(uid, request)
    except Exception as error:
        code = getattr(error, "code", None)
        if type(code) is str and code and code.replace("_", "").isalnum():
            _fail(code)
        _fail("AUTHORITY_UNAVAILABLE")
    if type(response) is dict and response.get("kind") == "authority_error":
        reason = response.get("reason")
        if type(reason) is str and reason and reason.replace("_", "").isalnum():
            _fail(reason)
        _fail("AUTHORITY_UNAVAILABLE")
    if (type(response) is not dict or response.get("kind") != kind or response.get("action") != action
            or response.get("request_id") != result_request_id
            or type(response.get("schema_version")) is not int or response["schema_version"] != 1
            or response.get("ci_eligible") is not False):
        _fail("AUTHORITY_RESPONSE_INVALID")
    if kind in {"partitioned_run_authority_result", "evaluation_authority_result"}:
        _require_false_flags(response)
    return response


def _now(clock: Any) -> int:
    try:
        value = clock()
    except Exception:
        _fail("CLOCK_FAILURE")
    if type(value) not in (int, float) or not 0 <= value < float("inf"):
        _fail("CLOCK_FAILURE")
    stamp = int(value)
    try:
        require_uint(stamp)
    except ContractError:
        _fail("CLOCK_FAILURE")
    return stamp


def _validate_begin_response(value: Any, manifest: dict[str, Any], begin_request: Any) -> tuple[str, int, dict[str, Any]]:
    fields = {
        "schema_version", "kind", "action", "request_id", "authority_connected",
        "resource_closure_verified", "baseline_freshness_verified", "adoption_verified",
        "admission_verified", "ci_eligible", "run_id", "run", "resource_snapshot", "duplicate",
    }
    if (type(value) is not dict or set(value) != fields
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or value["kind"] != "partitioned_run_authority_result" or value["action"] != "run_begin_v2"
            or value["run_id"] != manifest["run_id"] or type(value["request_id"]) is not str
            or any(value[field] is not False for field in _FALSE_FLAGS)
            or type(value["duplicate"]) is not bool):
        _fail("BEGIN_RESPONSE_INVALID")
    try:
        normalized_begin = validate_v2_request(begin_request)
    except Exception:
        _fail("BEGIN_REQUEST_INVALID")
    if (normalized_begin.get("action") != "run_begin_v2"
            or normalized_begin.get("request_id") != value["request_id"]
            or normalized_begin.get("manifest") != manifest
            or normalized_begin.get("expected_generation") != 1
            or type(normalized_begin.get("expected_generation")) is not int
            or type(value.get("run")) is not dict
            or normalized_begin.get("execution_profile") != value["run"].get("execution_profile")):
        _fail("BEGIN_REQUEST_MISMATCH")
    snapshot = value["resource_snapshot"]
    if (type(snapshot) is not dict or snapshot.get("run_id") != manifest["run_id"]
            or snapshot.get("manifest_digest") != content_ref("run_manifest", manifest["run_id"], manifest)["digest"]
            or type(snapshot.get("owner_id")) is not str or type(snapshot.get("owner_epoch")) is not int
            or snapshot["owner_epoch"] < 1 or snapshot.get("ci_eligible") is not False):
        _fail("BEGIN_RESPONSE_INVALID")
    return snapshot["owner_id"], snapshot["owner_epoch"], normalized_begin


def _run_view(value: dict[str, Any], manifest: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    run = value.get("run")
    if (type(run) is not dict or set(run) != _RUN_VIEW_FIELDS
            or type(run["schema_version"]) is not int or run["schema_version"] != 2
            or run["kind"] != "partitioned_run_evidence_run" or run["run_id"] != manifest["run_id"]
            or run["state"] != "OPEN" or run["diagnostic_finalized"] is not False
            or run["baseline_context"] is not None):
        _fail("RUN_NOT_OPEN")
    if any(run.get(name) is not False for name in _RUN_FALSE_FLAGS):
        _fail("AUTHORITY_RESPONSE_INVALID")
    receipt = run["bundle"]
    if (type(receipt) is not dict or receipt.get("manifest_ref") != content_ref(
            "run_manifest", manifest["run_id"], manifest)
            or receipt.get("plan_index_ref") != manifest["plan_ref"]):
        _fail("RUN_BINDING_MISMATCH")
    return receipt, run["execution_profile"]


def _read_plan(runtime: Any, run_id: str, plan_ref: dict[str, Any], request_prefix: str) -> dict[str, Any]:
    def read(segment_index: int | None) -> dict[str, Any]:
        suffix = "index" if segment_index is None else "segment-" + str(segment_index)
        request = {"schema_version": 1, "action": "plan_partition_read",
                   "request_id": request_prefix + "-" + suffix, "plan_ref": plan_ref,
                   "segment_index": segment_index}
        value = _client(runtime, OPERATOR, request, kind="partitioned_plan_store_result",
                        action="plan_partition_read", result_request_id=request["request_id"])
        if value.get("plan_ref") != plan_ref:
            _fail("PLAN_BINDING_MISMATCH")
        return value

    index_response = read(None)
    index = index_response.get("index")
    if (type(index) is not dict or index.get("plan_id") != plan_ref.get("id")
            or content_ref(INDEX_KIND, index.get("plan_id"), index) != plan_ref
            or type(index.get("segment_count")) is not int or not 1 <= index["segment_count"] <= MAX_SEGMENTS):
        _fail("PLAN_RESPONSE_INVALID")
    segments = []
    for ordinal in range(index["segment_count"]):
        response = read(ordinal)
        segment = response.get("segment")
        if type(segment) is not dict:
            _fail("PLAN_RESPONSE_INVALID")
        segments.append(segment)
    try:
        plan = restore_trial_plan(index, segments)
    except Exception:
        _fail("PLAN_INVALID")
    if type(plan) is not dict or len(plan.get("entries", [])) != 15:
        _fail("DIAGNOSTIC_PLAN_SIZE")
    return plan


def _selector(value: Any) -> dict[str, str]:
    if type(value) is not dict or set(value) != _SELECTOR_FIELDS:
        _fail("INVALID_SELECTOR")
    for name in _SELECTOR_FIELDS:
        if type(value[name]) is not str:
            _fail("INVALID_SELECTOR")
        try:
            require_id(value[name])
        except ContractError:
            _fail("INVALID_SELECTOR")
    if value["variant"] not in {"candidate", "baseline"}:
        _fail("INVALID_SELECTOR")
    return dict(value)


def _binding(manifest: dict[str, Any], entry: dict[str, Any], operation_id: str,
             owner_epoch: int, runner: DockerRunner, profile: dict[str, Any]) -> dict[str, Any]:
    try:
        profile_binding = expected_execution(profile, entry["target_ref"]["digest"], entry["evaluator_ref"]["digest"])
        if (profile_binding["fixture_digest"] != runner.lock["worker_digest"]
                or profile_binding["adapter_digests"] != [runner.adapter_digest]
                or profile_binding["isolation_digest"] != runner.isolation_digest):
            _fail("EXECUTION_PROFILE_MISMATCH")
        value = {
            "run_id": manifest["run_id"], "operation_id": operation_id, "owner_epoch": owner_epoch,
            "contract_digest": manifest["contract_ref"]["digest"],
            "target_digest": entry["target_ref"]["digest"],
            "obligation_id": entry["obligation_id"], "case_id": entry["case_id"],
            "trial_id": entry["trial_id"], "stage_id": entry["stage_ids"][0],
            "fixture_digest": runner.lock["worker_digest"], "adapter_digest": runner.adapter_digest,
            "policy_digest": manifest["policy_ref"]["digest"],
            "evaluator_digest": entry["evaluator_ref"]["digest"],
            "isolation_digest": runner.isolation_digest,
        }
        return validate_binding(value)
    except (ContractError, KeyError, IndexError, TypeError):
        _fail("EXECUTION_PROFILE_MISMATCH")


def _operation_id(manifest: dict[str, Any], selector: dict[str, str]) -> str:
    digest = hashlib.sha256(canonical_bytes([manifest["run_id"], manifest["plan_ref"], selector])).hexdigest()[:40]
    return "diag-op-" + digest


def _verify_receipt(runner: DockerRunner, run_id: str, operation_id: str, binding: dict[str, Any],
                    receipt: Any) -> dict[str, Any]:
    if type(receipt) is not dict:
        _fail("RECEIPT_INVALID")
    try:
        with ExecutionJournal(runner.journal_path) as journal:
            record = journal.get(run_id, operation_id)
    except Exception:
        _fail("RECEIPT_INVALID")
    if (record.get("state") != "FINISHED" or record.get("binding") != binding
            or record.get("receipt") != receipt or receipt.get("kind") != "fixture_execution"
            or receipt.get("binding") != binding or receipt.get("scenario") != SCENARIO
            or receipt.get("image_id") != runner.lock.get("image_id")
            or receipt.get("ci_eligible") is not False):
        _fail("RECEIPT_INVALID")
    return receipt


def _check_success_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    if (receipt.get("execution_status") != "COMPLETED" or receipt.get("reason") is not None
            or receipt.get("exit_code") != 0 or receipt.get("stop_confirmed") is not True
            or receipt.get("cleanup_confirmed") is not True
            or receipt.get("isolation_config_verified") is not True
            or receipt.get("recovered") is not False
            or receipt.get("output_disposition") != "ADMITTED"):
        # Even a timed-out worker that was later killed is not turned into a settled attempt here.
        _fail("EXECUTION_NOT_CONFIRMED")
    result = receipt.get("normalized_result")
    if (type(result) is not dict or result.get("kind") != "normalized_result"
            or result.get("mode") != "constraint" or result.get("binding") != receipt["binding"]
            or receipt.get("probe_result") is not None):
        _fail("RECEIPT_INVALID")
    return result


def _event_request(action: str, suffix: str, op_id: str, run_id: str, **fields: Any) -> dict[str, Any]:
    return {"schema_version": 1, "action": action, "request_id": op_id + "-" + suffix,
            "run_id": run_id, **fields}


def _operation_read(runtime: Any, run_id: str, operation_id: str,
                    manifest_ref: dict[str, Any]) -> dict[str, Any] | None:
    request = _event_request("resource_operation", "inspect", operation_id, run_id,
                             operation_id=operation_id, expected_manifest_ref=manifest_ref)
    try:
        value = _client(runtime, OPERATOR, request, kind="partitioned_run_authority_result",
                        action="resource_operation", result_request_id=request["request_id"])
    except PartitionedDiagnosticError as error:
        if error.code == "OPERATION_MISSING":
            return None
        raise
    if (value.get("operation_id") != operation_id or value.get("run_id") != run_id
            or value.get("dispatch_intended") is not True):
        _fail("OPERATION_RESPONSE_INVALID")
    return value


def _begin_snapshot(runtime: Any, request: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    response = _client(runtime, OPERATOR, request, kind="partitioned_run_authority_result",
                       action="run_begin_v2", result_request_id=request["request_id"])
    _owner, _epoch, _normalized = _validate_begin_response(response, manifest, request)
    snapshot = response["resource_snapshot"]
    if (type(snapshot.get("closed")) is not bool or type(snapshot.get("budget_closure")) is not bool
            or snapshot.get("run_id") != manifest["run_id"]
            or snapshot.get("manifest_digest") != content_ref("run_manifest", manifest["run_id"], manifest)["digest"]):
        _fail("RESOURCE_SNAPSHOT_INVALID")
    if snapshot["closed"] and not snapshot["budget_closure"]:
        _fail("RESOURCE_SNAPSHOT_INVALID")
    return snapshot


def _claim_and_close(runtime: Any, owner_id: str, run_id: str, operation_id: str,
                     manifest_digest: str) -> tuple[dict[str, Any], str]:
    claim_request_id = "closeclaim-" + hashlib.sha256(operation_id.encode("ascii")).hexdigest()[:32]
    claim_request = _event_request("resource_claim", "close-claim", operation_id, run_id,
                                   owner_id=owner_id, recovery=False)
    claim_request["request_id"] = claim_request_id
    close_claim = _client(runtime, OPERATOR, claim_request, kind="evaluation_authority_result",
                          action="resource_claim", result_request_id=claim_request_id)
    if (close_claim.get("owner_id") != owner_id or type(close_claim.get("owner_epoch")) is not int
            or close_claim["owner_epoch"] < 1 or close_claim.get("recovery_only") is not False):
        _fail("RESOURCE_CLAIM_UNCONFIRMED")
    close_epoch = close_claim["owner_epoch"]
    close_request_id = "close-" + hashlib.sha256(
        canonical_bytes([operation_id, owner_id, close_epoch])
    ).hexdigest()[:32]
    close_request = _event_request("resource_close", "close", operation_id, run_id,
                                   owner_id=owner_id, owner_epoch=close_epoch)
    close_request["request_id"] = close_request_id
    closed = _client(runtime, OPERATOR, close_request, kind="evaluation_authority_result",
                     action="resource_close", result_request_id=close_request_id)
    if (closed.get("run_id") != run_id or closed.get("closed") is not True
            or closed.get("budget_closure") is not True
            or closed.get("manifest_digest") != manifest_digest):
        _fail("RESOURCE_CLOSE_UNCONFIRMED")
    return closed, close_request_id


def _save_close_checkpoint(checkpoint: Checkpoint, key: str, snapshot: dict[str, Any], *, source: str,
                           request_id: str) -> None:
    checkpoint.put(key, {
        "schema_version": 1, "kind": "diagnostic_close_receipt", "source": source,
        "request_id": request_id, "run_id": snapshot["run_id"],
        "manifest_digest": snapshot["manifest_digest"], "closed": snapshot["closed"],
        "budget_closure": snapshot["budget_closure"], "snapshot": snapshot,
    })


def _local_record(runner: DockerRunner, run_id: str, operation_id: str,
                  selector: dict[str, str], manifest: dict[str, Any], start_checkpoint: Any) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Read only this operation from the owned journal and pre-run binding."""
    try:
        with ExecutionJournal(runner.journal_path) as journal:
            record = journal.get(run_id, operation_id)
    except Exception:
        return None, None
    if record is None:
        return None, None
    saved_binding = start_checkpoint.get("binding") if type(start_checkpoint) is dict else None
    try:
        binding = validate_binding(saved_binding)
    except Exception:
        _fail("LOCAL_BINDING_UNAVAILABLE")
    expected = {
        "run_id": run_id, "operation_id": operation_id,
        "contract_digest": manifest["contract_ref"]["digest"],
        "policy_digest": manifest["policy_ref"]["digest"],
        "fixture_digest": runner.lock["worker_digest"],
        "adapter_digest": runner.adapter_digest, "isolation_digest": runner.isolation_digest,
        "obligation_id": selector["obligation_id"], "case_id": selector["case_id"],
        "trial_id": selector["trial_id"],
    }
    if (any(binding.get(key) != value for key, value in expected.items())
            or binding.get("target_digest") != runner.target_digest(SCENARIO)
            or binding.get("evaluator_digest") != runner.lock["worker_digest"]
            or type(binding.get("owner_epoch")) is not int or binding["owner_epoch"] < 1
            or record.get("binding") != binding or record.get("scenario") != SCENARIO
            or record.get("image_id") != runner.lock["image_id"]):
        _fail("LOCAL_BINDING_MISMATCH")
    return record, binding


def _verify_recovered_finished(runner: DockerRunner, run_id: str, operation_id: str,
                               binding: dict[str, Any], recovered: Any) -> dict[str, Any]:
    receipt = _verify_receipt(runner, run_id, operation_id, binding, recovered)
    if (receipt.get("execution_status") not in {"COMPLETED", "FAILED", "TIMEOUT", "CANCELLED"}
            or receipt.get("stop_confirmed") is not True or receipt.get("cleanup_confirmed") is not True
            or receipt.get("scenario") != SCENARIO
            or receipt.get("image_id") != runner.lock["image_id"]):
        _fail("INTERRUPTED_OPERATION")
    return receipt


def _recover_ledger(runtime: Any, owner_id: str, run_id: str, operation_id: str,
                    operation_state: dict[str, Any], now: int) -> None:
    """Settle only a journal-confirmed stop; never create an Attempt or success."""
    if operation_state.get("reservation") != resource_authority._reservation(SCENARIO):
        _fail("NON_BILLED_RESERVATION_MISMATCH")
    claim_request = _event_request("resource_cancel_claim", "recovery-claim", operation_id, run_id,
                                   owner_id=owner_id)
    claim_request["request_id"] = "recoverclaim-" + hashlib.sha256(
        canonical_bytes([operation_id, now])
    ).hexdigest()[:24]
    try:
        claim = _client(runtime, OPERATOR, claim_request, kind="partitioned_run_authority_result",
                        action="resource_cancel_claim", result_request_id=claim_request["request_id"])
    except PartitionedDiagnosticError as error:
        if error.code == "RUN_CLOSED" and operation_state.get("stopped") is True and operation_state.get("settled") is True:
            return
        raise
    if (claim.get("recovery_only") is not True or claim.get("cancelled") is not True
            or claim.get("owner_id") != owner_id or type(claim.get("owner_epoch")) is not int
            or claim["owner_epoch"] < 1):
        _fail("RECOVERY_CLAIM_UNCONFIRMED")
    observe_request = _event_request("resource_observe", "recovery-observe", operation_id, run_id,
        operation_id=operation_id, event_id="stop-" + hashlib.sha256((operation_id + "recovery").encode("ascii")).hexdigest()[:32],
        stopped=True, usage=dict(ZERO_USAGE))
    observe_request["request_id"] = "recoverobserve-" + hashlib.sha256(operation_id.encode("ascii")).hexdigest()[:24]
    observed = _client(runtime, VALIDATOR, observe_request, kind="evaluation_authority_result",
        action="resource_observe", result_request_id=observe_request["request_id"])
    if observed.get("accepted") is not True or observed.get("conflict") is not False:
        _fail("RECOVERY_OBSERVATION_UNCONFIRMED")
    close_request = _event_request("resource_close", "recovery-close", operation_id, run_id,
                                   owner_id=owner_id, owner_epoch=claim["owner_epoch"])
    close_request["request_id"] = "recoverclose-" + hashlib.sha256(
        canonical_bytes([operation_id, claim["owner_epoch"]])
    ).hexdigest()[:24]
    closed = _client(runtime, OPERATOR, close_request, kind="evaluation_authority_result",
                     action="resource_close", result_request_id=close_request["request_id"])
    if closed.get("closed") is not True or closed.get("budget_closure") is not True:
        _fail("RECOVERY_CLOSE_UNCONFIRMED")


def _result(run_id: str, operation_id: str, attempt_id: str, execution_status: str) -> dict[str, Any]:
    return {
        "schema_version": 1, "kind": "partitioned_diagnostic_entry_result", "run_id": run_id,
        "operation_id": operation_id, "attempt_id": attempt_id,
        "execution_status": execution_status, "resource_closed": True,
        "diagnostic_finalized": False, "baseline_freshness_verified": False, "ci_eligible": False,
        "authority_connected": False, "adoption_verified": False,
        "admission_verified": False, "resource_closure_verified": False,
    }


def execute_one_entry(runtime: Any, runner: DockerRunner, folder: str | Path,
                      begin_response: Any, manifest: Any, selector: Any, *, begin_request: Any,
                      clock=None) -> dict[str, Any]:
    """Run one preselected entry from an already-begun, exact 15-entry v2 diagnostic plan.

    It uses only the fixed ``constraint:C01:good`` fixture. A replay reuses the same
    operation/journal identity and never starts a second Docker dispatch. Timeout,
    malformed receipt, or uncertain cleanup intentionally leaves resources unsettled.
    """
    try:
        manifest = validate_partitioned_run_manifest(manifest)
        selector = _selector(selector)
        owner_id, begin_epoch, begin_request = _validate_begin_response(begin_response, manifest, begin_request)
    except PartitionedDiagnosticError:
        raise
    except (ContractError, TypeError, ValueError, KeyError):
        _fail("INVALID_INPUT")
    if (manifest["purpose"] != "diagnostic" or manifest["baseline_ref"] is not None
            or manifest["profile"] not in {"pr", "full"}):
        _fail("SCOPE_UNSUPPORTED")
    if type(runner) is not DockerRunner:
        _fail("FIXED_RUNNER_REQUIRED")
    try:
        runner_lock = runner.lock
        if (type(runner_lock["worker_digest"]) is not str
                or type(runner.adapter_digest) is not str or type(runner.isolation_digest) is not str
                or type(runner_lock["image_id"]) is not str):
            _fail("IMAGE_MISMATCH")
    except (AttributeError, KeyError, TypeError):
        _fail("IMAGE_MISMATCH")
    base = _plain_directory(folder)
    journal_path = Path(runner.journal_path).resolve()
    if journal_path != (base / "execution.sqlite").resolve():
        _fail("JOURNAL_PATH_MISMATCH")
    clock = clock or getattr(runtime, "clock", None) or time.time
    run_id = manifest["run_id"]
    manifest_ref = content_ref("run_manifest", run_id, manifest)
    operation_id = _operation_id(manifest, selector)
    request_prefix = "diag-" + hashlib.sha256(operation_id.encode("ascii")).hexdigest()[:24]
    checkpoint = Checkpoint(base / "checkpoints")
    identity = {
        "run_id": run_id, "manifest_ref": manifest_ref, "plan_ref": manifest["plan_ref"],
        "selector": selector, "scenario": SCENARIO, "image_id": runner.lock["image_id"],
        "worker_digest": runner.lock["worker_digest"], "adapter_digest": runner.adapter_digest,
        "isolation_digest": runner.isolation_digest,
    }
    with operation_lock(journal_path, run_id, "supervisor"):
        try:
            checkpoint.put("partitioned-diagnostic-identity", identity)
        except (CheckpointError, OSError):
            _fail("CHECKPOINT_FAILURE")
        started_key, end_key = "start-" + operation_id, "end-" + operation_id
        start_value = checkpoint.get("diagnostic-resource-start-" + operation_id)
        saved_end = checkpoint.get(end_key)
        # Saved dispatches use the historical read/recovery path before any fresh
        # start-only status/plan reads. Stale currentness never authorizes new work,
        # but it also must not prevent cleanup of this exact journal-owned operation.
        started_checkpoint = checkpoint.get(started_key)
        local_record = local_binding = recovered_finished = recovery_error = None
        if saved_end is None:
            local_record, local_binding = _local_record(
                runner, run_id, operation_id, selector, manifest, started_checkpoint,
            )
            if local_record is not None:
                try:
                    recovered = runner.recover(run_id, operation_id)
                    checkpoint.put("recovered-" + operation_id, {"receipt": recovered})
                except Exception as error:
                    recovered, recovery_error = None, error
                if recovered is not None:
                    try:
                        # recover() may finalize a RUNNING/STOPPED journal as FINISHED.
                        recovered_finished = _verify_recovered_finished(
                            runner, run_id, operation_id, local_binding, recovered,
                        )
                    except PartitionedDiagnosticError as error:
                        recovery_error = error
        try:
            operation_state = _operation_read(runtime, run_id, operation_id, manifest_ref)
        except PartitionedDiagnosticError:
            # A stale/unauthorized authority read must not keep our exact local worker alive.
            # Preserve that original authority error and do not attempt ledger mutations.
            raise
        if operation_state is not None:
            saved_close = checkpoint.get("diagnostic-resource-close-" + operation_id)
            if saved_close is not None:
                _record, close_binding = _local_record(
                    runner, run_id, operation_id, selector, manifest,
                    checkpoint.get(started_key),
                )
                saved_attempt = checkpoint.get("diagnostic-attempt-" + operation_id)
                close_snapshot = saved_close.get("snapshot") if type(saved_close) is dict else None
                if (set(saved_close) != {"schema_version", "kind", "source", "request_id", "run_id",
                                         "manifest_digest", "closed", "budget_closure", "snapshot"}
                        or saved_close.get("schema_version") != 1
                        or saved_close.get("kind") != "diagnostic_close_receipt"
                        or saved_close.get("run_id") != run_id
                        or saved_close.get("manifest_digest") != manifest_ref["digest"]
                        or saved_close.get("source") not in {"resource_close", "begin_replay"}
                        or type(saved_close.get("request_id")) is not str
                        or saved_close.get("closed") is not True or saved_close.get("budget_closure") is not True
                        or type(close_snapshot) is not dict or close_snapshot.get("run_id") != run_id
                        or close_snapshot.get("manifest_digest") != manifest_ref["digest"]
                        or close_snapshot.get("closed") is not True
                        or close_snapshot.get("budget_closure") is not True
                        or type(saved_end) is not dict or type(saved_attempt) is not dict
                        or saved_attempt.get("accepted") is not True):
                    _fail("CHECKPOINT_CORRUPT")
                recovered_receipt = _verify_receipt(
                    runner, run_id, operation_id, close_binding, saved_end.get("receipt"),
                )
                _check_success_receipt(recovered_receipt)
                attempt_value = saved_attempt.get("attempt")
                if (type(attempt_value) is not dict or attempt_value.get("attempt_id") != operation_id + "-attempt"
                        or attempt_value.get("expected_binding") != close_binding
                        or attempt_value.get("execution_status") != "COMPLETED"
                        or attempt_value.get("stop_confirmed") is not True):
                    _fail("CHECKPOINT_CORRUPT")
                return _result(run_id, operation_id, attempt_value["attempt_id"], recovered_receipt["execution_status"])
            if (operation_state.get("entry") is None
                    or any(operation_state["entry"].get(key) != selector[key] for key in _SELECTOR_FIELDS)
                    or operation_state.get("scenario") != SCENARIO):
                _fail("OPERATION_BINDING_MISMATCH")
            if saved_end is None:
                if recovered_finished is not None:
                    _recover_ledger(runtime, owner_id, run_id, operation_id, operation_state, _now(clock))
                elif isinstance(recovery_error, PartitionedDiagnosticError) and recovery_error.code in {
                        "RECEIPT_INVALID", "LOCAL_BINDING_MISMATCH", "LOCAL_BINDING_UNAVAILABLE"}:
                    _fail(recovery_error.code)
                _fail("INTERRUPTED_OPERATION")
        if saved_end is None and local_record is not None:
            _fail("INTERRUPTED_OPERATION")
        try:
            status_request = {"schema_version": 1, "action": "run_status_v2",
                              "request_id": request_prefix + "-status", "run_id": run_id}
            status = _client(runtime, OPERATOR, status_request, kind="partitioned_run_authority_result",
                             action="run_status_v2", result_request_id=status_request["request_id"])
            receipt, profile = _run_view(status, manifest)
            if (profile != begin_response["run"].get("execution_profile")
                    or receipt.get("plan_index_ref") != manifest["plan_ref"]
                    or receipt.get("manifest_ref") != manifest_ref):
                _fail("RUN_BINDING_MISMATCH")
            plan = _read_plan(runtime, run_id, manifest["plan_ref"], request_prefix)
            matches = [entry for entry in plan["entries"]
                       if all(entry.get(key) == selector[key] for key in _SELECTOR_FIELDS)]
            if len(matches) != 1:
                _fail("ENTRY_NOT_UNIQUE")
            entry = matches[0]
            if (len(entry["stage_ids"]) != 1 or entry["evaluator_ref"]["digest"] != runner.lock["worker_digest"]
                    or entry["target_ref"]["digest"] != runner.target_digest(SCENARIO)):
                _fail("FIXTURE_BINDING_MISMATCH")
            try:
                check_plan(profile, {"manifest": manifest, "plan": plan})
            except ContractError:
                _fail("EXECUTION_PROFILE_MISMATCH")

        except PartitionedDiagnosticError:
            # The saved successful journal end is enough to stop/settle only this
            # exact fixed operation if a later currentness/plan read has expired.
            # Preserve the original read failure and never synthesize an Attempt.
            if saved_end is not None and operation_state is not None:
                try:
                    _record, recovery_binding = _local_record(
                        runner, run_id, operation_id, selector, manifest, started_checkpoint,
                    )
                    recovered_receipt = _verify_receipt(
                        runner, run_id, operation_id, recovery_binding, saved_end.get("receipt"),
                    )
                    _check_success_receipt(recovered_receipt)
                    _recover_ledger(runtime, owner_id, run_id, operation_id,
                                    operation_state, _now(clock))
                except Exception:
                    pass
            raise
        started_new = False
        if operation_state is None:
            if start_value is not None or saved_end is not None:
                _fail("OPERATION_STATE_LOST")
            start_request = _event_request("resource_start", "start", operation_id, run_id,
                owner_id=owner_id, operation_id=operation_id,
                entry={key: selector[key] for key in _SELECTOR_FIELDS}, scenario=SCENARIO,
                expected_manifest_ref=manifest_ref)
            try:
                start_request = copy.deepcopy(start_request)
                # Reuse the installed strict request validator for exact fields, uints and refs.
                normalized_start = resource_authority.validate_request(start_request)
            except Exception:
                _fail("INVALID_START_REQUEST")
            checkpoint.put("diagnostic-resource-request-" + operation_id, {"request": normalized_start})
            response = _client(runtime, OPERATOR, normalized_start, kind="evaluation_authority_result",
                               action="resource_start", result_request_id=normalized_start["request_id"])
            if (response.get("manifest_ref") != manifest_ref or response.get("owner_id") != owner_id
                    or type(response.get("owner_epoch")) is not int or response["owner_epoch"] < 1
                    or response["owner_epoch"] < begin_epoch
                    or type(response.get("operation")) is not dict
                    or response["operation"].get("operation_id") != operation_id
                    or response["operation"].get("entry") != entry
                    or response["operation"].get("scenario") != SCENARIO
                    or response["operation"].get("dispatch_intended") is not True):
                _fail("START_RESPONSE_INVALID")
            checkpoint.put("diagnostic-resource-start-" + operation_id, response)
            start_value, operation_state, started_new = response, response["operation"], True
        elif operation_state is None or operation_state.get("entry") != entry or operation_state.get("scenario") != SCENARIO:
            _fail("OPERATION_BINDING_MISMATCH")

        owner_epoch = start_value["owner_epoch"] if start_value is not None else operation_state.get("owner_epoch")
        if (type(owner_epoch) is not int or owner_epoch < 1
                or operation_state.get("dispatch_intended") is not True
                or ("stopped" in operation_state and type(operation_state["stopped"]) is not bool)):
            _fail("OPERATION_STATE_INVALID")
        binding = _binding(manifest, entry, operation_id, owner_epoch, runner, profile)

        if saved_end is not None:
            started_at, finished_at, worker_receipt = saved_end.get("started_at"), saved_end.get("finished_at"), saved_end.get("receipt")
            if (type(started_at) is not int or type(finished_at) is not int or finished_at < started_at):
                _fail("CHECKPOINT_CORRUPT")
            worker_receipt = _verify_receipt(runner, run_id, operation_id, binding, worker_receipt)
            normalized_result = _check_success_receipt(worker_receipt)
        elif started_new:
            started_at = _now(clock)
            checkpoint.put(started_key, {"started_at": started_at, "binding": binding})
            try:
                worker_receipt = runner.run(SCENARIO, binding, run_deadline=manifest["deadline"], timeout_seconds=120)
            except KeyboardInterrupt:
                try:
                    recovery_receipt = runner.recover(run_id, operation_id)
                    checkpoint.put("recovered-" + operation_id, {"receipt": recovery_receipt})
                except Exception:
                    pass
                _fail("INTERRUPTED_OPERATION")
            except Exception as error:
                try:
                    recovery_receipt = runner.recover(run_id, operation_id)
                    checkpoint.put("recovered-" + operation_id, {"receipt": recovery_receipt})
                except Exception:
                    pass
                code = getattr(error, "code", None)
                _fail(code if type(code) is str and code else "EXECUTION_UNRESOLVED")
            finished_at = _now(clock)
            if finished_at < started_at:
                _fail("CLOCK_FAILURE")
            worker_receipt = _verify_receipt(runner, run_id, operation_id, binding, worker_receipt)
            normalized_result = _check_success_receipt(worker_receipt)
            saved_end = {"started_at": started_at, "finished_at": finished_at, "receipt": worker_receipt}
            checkpoint.put(end_key, saved_end)
        else:
            # An existing dispatch without an immutable successful end checkpoint is
            # never replayed. Recover only its own journal/container and leave unsettled.
            try:
                recovered = runner.recover(run_id, operation_id)
                checkpoint.put("recovered-" + operation_id, {"receipt": recovered})
            except Exception:
                pass
            _fail("INTERRUPTED_OPERATION")

        if (operation_state.get("stopped") is True and operation_state.get("settled") is True):
            observed = True
        else:
            observe_request = _event_request("resource_observe", "observe", operation_id, run_id,
                operation_id=operation_id, event_id=operation_id + "-stop", stopped=True,
                usage=dict(ZERO_USAGE))
            observed_value = _client(runtime, VALIDATOR, observe_request, kind="evaluation_authority_result",
                action="resource_observe", result_request_id=observe_request["request_id"])
            if observed_value.get("accepted") is not True or observed_value.get("conflict") is not False:
                _fail("OBSERVATION_UNCONFIRMED")
            observed = True
        if not observed:
            _fail("OBSERVATION_UNCONFIRMED")

        attempt = {
            "schema_version": 1, "kind": "attempt_record", "attempt_id": operation_id + "-attempt",
            "variant": entry["variant"], "retry_of": None, "started_at": started_at,
            "finished_at": finished_at, "stop_confirmed": True,
            "execution_status": "COMPLETED", "state_restored": True,
            "expected_binding": binding, "result": normalized_result,
        }
        evidence_request = _event_request("evidence_record_v2", "record", operation_id, run_id,
                                          attempt=attempt)
        evidence = _client(runtime, VALIDATOR, evidence_request, kind="partitioned_run_authority_result",
                           action="evidence_record_v2", result_request_id=evidence_request["request_id"])
        saved_attempt = evidence.get("evidence")
        if (type(saved_attempt) is not dict or saved_attempt.get("attempt_id") != attempt["attempt_id"]
                or saved_attempt.get("accepted") is not True or saved_attempt.get("ci_eligible") is not False):
            _fail("ATTEMPT_NOT_ACCEPTED")
        checkpoint.put("diagnostic-attempt-" + operation_id, {
            "attempt": attempt, "attempt_digest": saved_attempt.get("attempt_digest"),
            "accepted": True,
        })

        close_key = "diagnostic-resource-close-" + operation_id
        try:
            close_snapshot = _begin_snapshot(runtime, begin_request, manifest)
        except PartitionedDiagnosticError:
            # Preserve a stale snapshot error, but if a verified successful
            # receipt and the current close authority still permit it, finish
            # the already-observed resource closure without returning success.
            try:
                _record, recovery_binding = _local_record(
                    runner, run_id, operation_id, selector, manifest,
                    checkpoint.get(started_key),
                )
                recovered_receipt = _verify_receipt(
                    runner, run_id, operation_id, recovery_binding, saved_end.get("receipt"),
                )
                _check_success_receipt(recovered_receipt)
                _claim_and_close(runtime, owner_id, run_id, operation_id, manifest_ref["digest"])
            except Exception:
                pass
            raise
        if close_snapshot["closed"]:
            _save_close_checkpoint(checkpoint, close_key, close_snapshot, source="begin_replay",
                                   request_id=begin_request["request_id"])
            return _result(run_id, operation_id, attempt["attempt_id"], worker_receipt["execution_status"])
        # Always renew through the fresh start-authorized path before closing. The
        # operation and Attempt keep their dispatch epoch; only close uses this epoch.
        closed, close_request_id = _claim_and_close(
            runtime, owner_id, run_id, operation_id, manifest_ref["digest"],
        )
        _save_close_checkpoint(checkpoint, close_key, closed, source="resource_close",
                               request_id=close_request_id)
        return _result(run_id, operation_id, attempt["attempt_id"], worker_receipt["execution_status"])


__all__ = ["PartitionedDiagnosticError", "execute_one_entry"]
