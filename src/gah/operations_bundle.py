"""既存の認証済み読取から、許可した診断metadataだけをbundleへ保存する。"""
from __future__ import annotations
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import shutil
import time
from .adoption import AdoptionError
from .contracts import (ContractError, decode_document, require_digest, require_id,
                        require_object, require_ref, require_uint)
from .productization import (content_ref, operation_result, read_document,
                             workspace_path, write_document)
from .productization_journal import OperationJournal
from .run_contracts import validate_run_manifest, validate_trial_plan
from .wire import canonical_bytes

MAX_BYTES = 64 * 1024 * 1024
MAX_ENTRIES = 256
CHUNK_BYTES = 1024 * 1024
COMMAND = "ops.bundle.create"
BASE = {"schema_version", "kind", "action", "request_id", "ci_eligible"}
COUNTERS = {"case_trial_executions", "model_calls", "input_tokens", "output_tokens",
            "api_cost_usd_micros", "slots", "unsettled", "global_api_cost_usd_micros", "total_tokens"}


def _query(runtime, action, tag, fields, **request_fields):
    request = {"schema_version": 1, "action": action, "request_id": tag, **request_fields}
    try:
        received = runtime.client(12004, request)
    except AdoptionError as error:
        received = {"kind": "authority_error", "reason": error.code}
    value = decode_document(canonical_bytes(received))
    if value.get("kind") == "authority_error":
        # 未知のエラー本文は診断bundleにも流さない。
        reason = value.get("reason")
        mapped = (reason if reason in {"AUTHORITY_DENIED", "AUTHORITY_REVOKED", "CLOCK_ROLLBACK"}
                  else "CAPACITY_EXCEEDED" if reason == "DIAGNOSTIC_LIMIT" else "EVIDENCE_UNAVAILABLE")
        raise ContractError(mapped)
    require_object(value, BASE | set(fields))
    if (type(value["schema_version"]) is not int or value["schema_version"] != 1
            or value["kind"] != "evaluation_authority_result" or value["action"] != action
            or value["request_id"] != tag or value["ci_eligible"] is not False):
        raise ContractError("BINDING_MISMATCH")
    return value


def _diagnostics(runtime, tag):
    names = {"database_schema_version", "permission_generation", "extension_digest", "checked_at"}
    value = _query(runtime, "authority_diagnostics", tag, names)
    for field in names - {"extension_digest"}:
        require_uint(value[field])
    require_digest(value["extension_digest"])
    return {name: value[name] for name in sorted(names)}


def collect(runtime, run_id, tag):
    """本文を出力へコピーせず、固定型の参照と数値へ投影する。"""
    require_id(run_id)
    before = _diagnostics(runtime, tag + "-before")
    run = _query(runtime, "run_status", tag + "-run",
                 {"run_id", "manifest", "plan", "contract_series_id", "contract_generation", "resource_snapshot"},
                 run_id=run_id)
    manifest = validate_run_manifest(run["manifest"])
    plan = validate_trial_plan(run["plan"])
    manifest_ref = content_ref("run_manifest", run_id, manifest)
    if (run["run_id"] != run_id or manifest["run_id"] != run_id
            or manifest["plan_ref"] != content_ref("trial_plan", plan["plan_id"], plan)
            or plan["contract_ref"] != manifest["contract_ref"]):
        raise ContractError("BINDING_MISMATCH")
    require_id(run["contract_series_id"])
    require_uint(run["contract_generation"])
    snapshot = run["resource_snapshot"]
    require_object(snapshot, {"run_id", "manifest_digest", "owner_id", "owner_epoch", "deadline",
                             "cancelled", "breached", "closed", "closed_at", "resources", "budget_closure", "ci_eligible"})
    if (snapshot["run_id"] != run_id or snapshot["manifest_digest"] != manifest_ref["digest"]
            or snapshot["ci_eligible"] is not False):
        raise ContractError("BINDING_MISMATCH")
    for key in ("cancelled", "breached", "closed", "budget_closure"):
        if type(snapshot[key]) is not bool:
            raise ContractError("BINDING_MISMATCH")
    for key in ("owner_epoch", "deadline"):
        require_uint(snapshot[key])
    if snapshot["closed_at"] is not None:
        require_uint(snapshot["closed_at"])
    require_object(snapshot["resources"], COUNTERS)
    for value in snapshot["resources"].values():
        require_uint(value)
    details = _query(runtime, "run_diagnostics", tag + "-details",
        {"run_id", "artifact_refs", "run_state", "resource_limits", "retention",
         "database_schema_version", "permission_generation", "extension_digest", "checked_at", "current_ci_checked"},
        run_id=run_id)
    if details["run_id"] != run_id or details["current_ci_checked"] is not False:
        raise ContractError("BINDING_MISMATCH")
    refs_seen = details["artifact_refs"]
    if type(refs_seen) is not list or len(refs_seen) > 256:
        raise ContractError("BINDING_MISMATCH")
    for ref in refs_seen:
        require_ref(ref)
    if len({(r["kind"], r["id"], r["digest"]) for r in refs_seen}) != len(refs_seen):
        raise ContractError("BINDING_MISMATCH")
    limits = details["resource_limits"]
    require_object(limits, {"elapsed_seconds", "concurrent_evaluations", "case_trial_executions",
                           "model_calls", "total_tokens", "api_cost_usd_micros"})
    for value in limits.values():
        require_uint(value)
    state = details["run_state"]
    if state is not None:
        require_object(state, {"state", "evidence_state", "evidence_generation"})
        if state["state"] not in {"OPEN", "HOLD", "FINALIZED"} or state["evidence_state"] not in {"UNKNOWN", "VALID", "REVOKED", "DELETED", "EXPIRED"}:
            raise ContractError("BINDING_MISMATCH")
        if state["evidence_generation"] is not None:
            require_uint(state["evidence_generation"])
    retention = details["retention"]
    if retention is not None:
        require_object(retention, {"evidence_ref", "hold_active", "hold_sequence", "deleted", "tombstone_ref"})
        require_ref(retention["evidence_ref"]); require_uint(retention["hold_sequence"])
        if type(retention["hold_active"]) is not bool or type(retention["deleted"]) is not bool:
            raise ContractError("BINDING_MISMATCH")
        if retention["deleted"]:
            require_ref(retention["tombstone_ref"])
            if retention["tombstone_ref"]["kind"] != "evidence_tombstone":
                raise ContractError("BINDING_MISMATCH")
        elif retention["tombstone_ref"] is not None:
            raise ContractError("BINDING_MISMATCH")
    for field in ("database_schema_version", "permission_generation", "checked_at"):
        require_uint(details[field])
    require_digest(details["extension_digest"])
    repeated = _query(runtime, "run_status", tag + "-run-after",
        {"run_id", "manifest", "plan", "contract_series_id", "contract_generation", "resource_snapshot"}, run_id=run_id)
    if any(repeated[field] != run[field] for field in run if field != "request_id"):
        raise ContractError("STALE_OR_INVALIDATED")
    after = _diagnostics(runtime, tag + "-after")
    if not before["checked_at"] <= details["checked_at"] <= after["checked_at"]:
        raise ContractError("CLOCK_ROLLBACK")
    if any(details[key] != after[key] for key in after if key != "checked_at"):
        raise ContractError("STALE_OR_INVALIDATED")
    if any(before[key] != after[key] for key in before if key != "checked_at"):
        raise ContractError("STALE_OR_INVALIDATED")
    if after["checked_at"] < before["checked_at"]:
        raise ContractError("CLOCK_ROLLBACK")
    # actor名・path・raw入力・任意の文章を保存しない。必要な参照だけを明示する。
    refs = {"manifest_ref": manifest_ref}
    for name in ("plan_ref", "contract_ref", "baseline_ref", "policy_ref", "environment_ref"):
        refs[name] = deepcopy(manifest[name])
    reasons = []
    if snapshot["cancelled"]:
        reasons.append("OPERATION_CANCELLED")
    if not snapshot["budget_closure"]:
        reasons.append("SETTLEMENT_PENDING")
    if snapshot["breached"]:
        reasons.append("CAPACITY_EXCEEDED")
    return {"schema_version": 1, "kind": "diagnostic_metadata", "id": tag,
            "run_id": run_id, "authority": after, "source_refs": refs,
            "contract_generation": run["contract_generation"],
            "timing": {"created_at": manifest["created_at"], "deadline": snapshot["deadline"],
                       "closed_at": snapshot["closed_at"]},
            "resources": deepcopy(snapshot["resources"]), "owner_epoch": snapshot["owner_epoch"],
            "cancelled": snapshot["cancelled"], "budget_closure": snapshot["budget_closure"],
            "reasons": reasons, "doctor_ref": None, "report_ref": None,
            "artifact_refs": deepcopy(refs_seen), "run_state": deepcopy(state),
            "retention": deepcopy(retention), "resource_limits": deepcopy(limits),
            "uncollected": ["doctor", "rendered_report", "migration_receipt"],
            "current_ci_checked": False, "ci_eligible": False}


def _write_chunked(path, raw):
    # 1MiBを超える単一writeを行わない。完了markerは全entryの永続化後に置く。
    with path.open("xb") as stream:
        for offset in range(0, len(raw), CHUNK_BYTES):
            stream.write(raw[offset:offset + CHUNK_BYTES])
        stream.flush()
        os.fsync(stream.fileno())


def create(workspace, runtime, run_id, output, *, request_id, authenticated_principal, clock=None):
    """出力先は新規directory。同一要求の完成bundleだけを回収できる。"""
    now = clock or (lambda: int(time.time()))
    rid = request_id
    try:
        require_id(rid); require_id(run_id); require_id(authenticated_principal)
        target = workspace_path(workspace, output)
        root = workspace_path(workspace, ".ga/operations/bundles")
        root.mkdir(parents=True, exist_ok=True)
        identity = "bundle-" + hashlib.sha256(canonical_bytes([authenticated_principal, COMMAND, rid])).hexdigest()
        runtime_folder = workspace_path(workspace, runtime.folder)
        deployment = read_document(workspace, runtime_folder / "deployment.json")
        # prefix/imageの同じruntimeに要求を固定し、他runtimeのreceiptを再利用しない。
        require_object(deployment, {"prefix", "image_id", "containers"})
        if (type(deployment["prefix"]) is not str or not deployment["prefix"]
                or type(deployment["image_id"]) is not str or not deployment["image_id"]
                or type(deployment["containers"]) is not list):
            raise ContractError("INVALID_INPUT")
        inputs = {"run_id": run_id, "output": str(target), "runtime": str(runtime_folder),
                  "deployment": {key: deployment[key] for key in ("prefix", "image_id")}}
        digest = hashlib.sha256(canonical_bytes(inputs)).hexdigest()
        with OperationJournal(workspace_path(workspace, root / "journal.sqlite"), clock=now) as journal:
            intent = journal.begin(authenticated_principal, COMMAND, rid, digest)
            if intent["result"] is not None:
                return intent["result"]
            completed = workspace_path(workspace, root / (identity + "-result.json"))
            if not intent["created"]:
                if not completed.is_file():
                    return operation_result(COMMAND, rid, "INCOMPLETE", reasons=["OPERATION_UNKNOWN"], checked_at=now())
                saved = read_document(workspace, completed)
                index = read_document(workspace, target / "manifest.json")
                require_object(saved, {"schema_version", "kind", "id", "request_digest", "bundle_ref", "actual_bytes", "file_count", "ci_eligible"})
                require_object(index, {"schema_version", "kind", "id", "request_digest", "entries", "file_count", "limit_bytes", "limit_entries", "free_bytes_before", "ci_eligible"})
                if (type(index["schema_version"]) is not int or index["schema_version"] != 1
                        or type(saved["schema_version"]) is not int or saved["schema_version"] != 1
                        or index["kind"] != "diagnostic_bundle" or saved["kind"] != "bundle_operation_result"
                        or index["id"] != identity or saved["id"] != identity
                        or index["request_digest"] != digest or index["ci_eligible"] is not False
                        or saved["ci_eligible"] is not False or type(index["file_count"]) is not int
                        or index["file_count"] != 2 or saved["file_count"] != 2
                        or type(index["entries"]) is not list or len(index["entries"]) != 1
                        or {item.name for item in target.iterdir()} != {"manifest.json", "diagnostics.json"}):
                    raise ContractError("BINDING_MISMATCH")
                ref = content_ref(index["kind"], index["id"], index)
                if saved.get("bundle_ref") != ref or saved.get("request_digest") != digest:
                    raise ContractError("BINDING_MISMATCH")
                for entry in index["entries"]:
                    require_object(entry, {"path", "bytes", "sha256"})
                    require_uint(entry["bytes"]); require_digest(entry["sha256"])
                    if entry["path"] != "diagnostics.json":
                        raise ContractError("BINDING_MISMATCH")
                    data = read_document(workspace, target / entry["path"])
                    raw = canonical_bytes(data)
                    if len(raw) != entry["bytes"] or hashlib.sha256(raw).hexdigest() != entry["sha256"]:
                        raise ContractError("BINDING_MISMATCH")
                result = operation_result(COMMAND, rid, "COMPLETED",
                    result_ref=content_ref(saved["kind"], saved["id"], saved), checked_at=now())
                return journal.finish(authenticated_principal, COMMAND, rid, digest, result)
            if target.exists():
                result = operation_result(COMMAND, rid, "REJECTED", reasons=["RESULT_CONFLICT"], checked_at=now())
                return journal.finish(authenticated_principal, COMMAND, rid, digest, result)
            data = collect(runtime, run_id, identity)
            raw = canonical_bytes(data)
            free = shutil.disk_usage(target.parent).free
            entry = {"path": "diagnostics.json", "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
            index = {"schema_version": 1, "kind": "diagnostic_bundle", "id": identity,
                     "request_digest": digest, "entries": [entry], "file_count": 2,
                     "limit_bytes": MAX_BYTES, "limit_entries": MAX_ENTRIES, "free_bytes_before": free,
                     "ci_eligible": False}
            index_raw = canonical_bytes(index)
            total = len(raw) + len(index_raw)
            if total > MAX_BYTES or 2 > MAX_ENTRIES or free < total:
                raise ContractError("CAPACITY_EXCEEDED")
            target = workspace_path(workspace, target)
            target.mkdir()  # 既存directoryへの追加・上書きはしない。
            _write_chunked(workspace_path(workspace, target / "diagnostics.json"), raw)
            _write_chunked(workspace_path(workspace, target / "manifest.json"), index_raw)
            bundle_ref = content_ref("diagnostic_bundle", identity, index)
            saved = {"schema_version": 1, "kind": "bundle_operation_result", "id": identity,
                     "request_digest": digest, "bundle_ref": bundle_ref, "actual_bytes": total,
                     "file_count": 2, "ci_eligible": False}
            ref = write_document(workspace, completed, saved)
            result = operation_result(COMMAND, rid, "COMPLETED", result_ref=ref, checked_at=now())
            return journal.finish(authenticated_principal, COMMAND, rid, digest, result)
    except ContractError as error:
        try: require_id(rid)
        except ContractError: rid = None
        reason = error.code if error.code in {"INVALID_INPUT", "PATH_REJECTED", "RESULT_CONFLICT",
            "IDEMPOTENCY_CONFLICT", "CAPACITY_EXCEEDED", "BINDING_MISMATCH", "CLOCK_ROLLBACK",
            "STALE_OR_INVALIDATED", "AUTHORITY_DENIED", "AUTHORITY_REVOKED", "EVIDENCE_UNAVAILABLE"} else "IO_ERROR"
        status = "REJECTED" if reason in {"INVALID_INPUT", "PATH_REJECTED", "RESULT_CONFLICT",
            "IDEMPOTENCY_CONFLICT", "CAPACITY_EXCEEDED", "AUTHORITY_DENIED", "AUTHORITY_REVOKED"} else "INCOMPLETE"
        return operation_result(COMMAND, rid, status, reasons=[reason])
    except Exception:
        # 出力途中のdirectoryは証拠として残す。推測で削除・再生成しない。
        return operation_result(COMMAND, rid, "INCOMPLETE", reasons=["OPERATION_UNKNOWN"])
