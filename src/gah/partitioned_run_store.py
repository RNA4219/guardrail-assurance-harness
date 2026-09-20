"""Authority-backed, ref-only storage for diagnostic partitioned runs."""
from __future__ import annotations

import copy
import json
import sqlite3
from typing import Any

from . import evaluation_authority as authority
from . import execution_profiles, partitioned_plan_store, resources, run_evidence
from .adoption import AdoptionError
from .contracts import ContractError, MAX_INTEGER, require_digest, require_id, require_object, require_uint
from .partitioned_run_contracts import bind_partitioned_run_manifest, validate_partitioned_run_manifest
from .partitioned_run_evidence import PartitionedRunEvidenceBook
from .run_contracts import content_ref, validate_evaluation_contract
from .policy import validate_policy_profile
from .registry import validate_registry
from .corpus import validate_case_set

MAX_MANIFEST_BYTES = 1024 * 1024
TABLES = {
    "eval_runs_v2": {
        "run_id", "manifest_json", "manifest_digest", "bundle_digest", "contract_series_id",
        "contract_generation", "permission_generation", "extension_digest", "created_at",
    }
}

_BASE = {"schema_version", "action", "request_id"}
_RUN_BEGIN = _BASE | {"manifest", "contract_series_id", "expected_generation", "execution_profile"}
_ACTION_FIELDS = {
    "run_begin_v2": _RUN_BEGIN,
    "run_status_v2": _BASE | {"run_id"},
    "evidence_record_v2": _BASE | {"run_id", "attempt"},
    "evidence_attempt_v2": _BASE | {"attempt_id"},
    "evidence_finalize_v2": _BASE | {"run_id"},
    "evidence_terminal_v2": _BASE | {"run_id"},
    "evidence_current_v2": _BASE | {"run_id", "expected_bundle_digest"},
}
ACTIONS = {
    "run_begin_v2": {"operator"},
    "run_status_v2": {"manager", "validator", "operator"},
    "evidence_record_v2": {"validator"},
    "evidence_attempt_v2": {"manager", "validator", "operator"},
    "evidence_finalize_v2": {"operator"},
    "evidence_terminal_v2": {"manager", "validator", "operator"},
    "evidence_current_v2": {"manager", "validator", "operator"},
}
# A replay must still re-resolve source, permission, current contract and committed segments.
FRESH_ACTIONS = set(ACTIONS)


def _fail(code: str) -> None:
    raise AdoptionError(code)


def _canonical(value: Any, maximum: int = MAX_MANIFEST_BYTES) -> tuple[str, str]:
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        _fail("INVALID_REQUEST")
    if len(raw) > maximum:
        _fail("DOCUMENT_SIZE")
    import hashlib
    return raw.decode("utf-8"), hashlib.sha256(raw).hexdigest()


def _profile(value: Any) -> dict[str, Any]:
    try:
        return run_evidence._profile(value)
    except run_evidence.EvidenceError as error:
        _fail(error.code)


def validate_request(value: Any) -> dict[str, Any]:
    if type(value) is not dict or type(value.get("action")) is not str:
        _fail("INVALID_REQUEST")
    action = value["action"]
    fields = _ACTION_FIELDS.get(action)
    if fields is None:
        _fail("INVALID_ACTION")
    if set(value) != fields:
        _fail("INVALID_REQUEST")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        _fail("UNSUPPORTED_VERSION")
    try:
        require_id(value["request_id"])
        if action == "run_begin_v2":
            manifest = validate_partitioned_run_manifest(value["manifest"])
            _canonical(manifest)
            require_id(value["contract_series_id"])
            require_uint(value["expected_generation"])
            if value["expected_generation"] != 1:
                _fail("SCOPE_UNSUPPORTED")
            normalized = copy.deepcopy(value)
            normalized["manifest"] = manifest
            normalized["execution_profile"] = _profile(value["execution_profile"])
            _canonical(normalized["execution_profile"])
            return normalized
        if action in {"run_status_v2", "evidence_record_v2", "evidence_finalize_v2", "evidence_terminal_v2", "evidence_current_v2"}:
            require_id(value["run_id"])
        if action == "evidence_record_v2":
            if type(value["attempt"]) is not dict:
                _fail("INVALID_REQUEST")
            _canonical(value["attempt"])
        if action == "evidence_attempt_v2":
            require_id(value["attempt_id"])
        if action == "evidence_current_v2":
            require_digest(value["expected_bundle_digest"])
    except ContractError as error:
        _fail(error.code)
    return copy.deepcopy(value)


def create_schema(db: sqlite3.Connection) -> None:
    if not db.in_transaction:
        _fail("TRANSACTION_REQUIRED")
    db.execute(
        "CREATE TABLE eval_runs_v2("
        "run_id TEXT PRIMARY KEY,manifest_json TEXT NOT NULL,manifest_digest TEXT NOT NULL,"
        "bundle_digest TEXT NOT NULL,contract_series_id TEXT NOT NULL,"
        "contract_generation INTEGER NOT NULL CHECK(contract_generation=1),"
        "permission_generation INTEGER NOT NULL CHECK(permission_generation>=0),"
        "extension_digest TEXT NOT NULL,created_at INTEGER NOT NULL CHECK(created_at>=0),"
        "FOREIGN KEY(run_id) REFERENCES bound_runs(run_id) DEFERRABLE INITIALLY DEFERRED)"
    )


def _load_manifest(row: sqlite3.Row) -> dict[str, Any]:
    try:
        manifest = json.loads(row["manifest_json"])
        raw, digest = authority._packed(manifest)
        manifest = validate_partitioned_run_manifest(manifest)
    except (TypeError, ValueError, UnicodeError, RecursionError, ContractError, AdoptionError):
        _fail("STORAGE_CORRUPT")
    if raw != row["manifest_json"] or digest != row["manifest_digest"]:
        _fail("STORAGE_CORRUPT")
    return manifest


def _current_contract(store: Any, db: sqlite3.Connection, series_id: str, generation: int, now: int) -> dict[str, Any]:
    current = db.execute("SELECT * FROM eval_current WHERE series_id=?", (series_id,)).fetchone()
    history = db.execute("SELECT * FROM eval_adoptions WHERE series_id=? AND generation=?", (series_id, generation)).fetchone()
    if current is None or history is None or current["generation"] != generation or history["generation"] != generation:
        _fail("CONTRACT_INVALID")
    try:
        contract = validate_evaluation_contract(authority._load_json(current, "payload_json", "digest"))
        historical = validate_evaluation_contract(authority._load_json(history, "payload_json", "digest"))
    except (ContractError, AdoptionError, TypeError, ValueError, KeyError, RecursionError):
        _fail("CONTRACT_INVALID")
    if (contract != historical or contract["generation"] != generation
            or current["series_id"] != series_id or history["series_id"] != series_id):
        _fail("CONTRACT_INVALID")
    authority._validate_state(store, db, contract, now)
    authority._assert_current_valid(store, db, current, contract, now)
    if generation != 1 or contract["comparison"]["mode"] != "not_applicable":
        _fail("SCOPE_UNSUPPORTED")
    return contract


def _fresh_context(extension: Any, store: Any, db: sqlite3.Connection, manifest: dict[str, Any],
                   series_id: str, generation: int, profile: dict[str, Any], now: int) -> tuple[dict[str, Any], dict[str, Any]]:
    if db is not getattr(store, "_db", None) or not db.in_transaction:
        _fail("TRANSACTION_REQUIRED")
    if type(now) is not int or not 0 <= now <= MAX_INTEGER:
        _fail("CLOCK_INVALID")
    manifest = validate_partitioned_run_manifest(manifest)
    if not manifest["created_at"] <= now < manifest["deadline"]:
        _fail("RUN_TIME_INVALID")
    if manifest["purpose"] != "diagnostic" or manifest["baseline_ref"] is not None:
        _fail("SCOPE_UNSUPPORTED")
    contract = _current_contract(store, db, series_id, generation, now)
    try:
        policy, policy_ref, _policy_generation = authority.EvaluationExtension._pinned_policy(
            store, db, contract, now, require_current=True
        )
        registry = validate_registry(authority._object(db, contract["registry_ref"]))
        case_set = validate_case_set(authority._object(db, contract["case_set_ref"]))
        if content_ref("evaluation_contract", contract["contract_id"], contract) != manifest["contract_ref"]:
            _fail("REFERENCE_MISMATCH")
        if policy_ref != manifest["policy_ref"]:
            _fail("REFERENCE_MISMATCH")
        loaded = partitioned_plan_store.load_verified_committed_plan(
            extension, store, db, manifest["plan_ref"], contract_series_id=series_id,
            expected_generation=generation, now=now,
        )
        receipt = bind_partitioned_run_manifest(
            manifest, contract, loaded["index"], loaded["segments"], policy, registry, case_set,
            baseline_context=None,
        )
        if profile["isolation_digest"] != manifest["environment_ref"]["digest"]:
            _fail("PROFILE_MISMATCH")
        internal = {
            "manifest": manifest, "contract": contract, "plan": loaded["plan"],
            "policy": policy, "registry": registry, "case_set": case_set,
            "selected_controls": copy.deepcopy(receipt["selected_controls"]), "ci_eligible": False,
        }
        execution_profiles.check_plan(profile, internal)
    except AdoptionError:
        raise
    except ContractError as error:
        _fail(getattr(error, "code", "PARTITIONED_CONTEXT_INVALID"))
    except (TypeError, ValueError, KeyError, IndexError, RecursionError, sqlite3.Error):
        _fail("PARTITIONED_CONTEXT_INVALID")
    return {
        "manifest": manifest, "contract": contract, "index": loaded["index"],
        "segments": loaded["segments"], "policy": policy, "registry": registry,
        "case_set": case_set, "execution_profile": profile, "baseline_context": None,
    }, receipt


def has_v2_run_conflict(db: sqlite3.Connection, run_id: str) -> bool:
    """Check concrete run-id namespaces and reserved combined child ids only."""
    for table in ("eval_runs_v2", "eval_runs", "resource_runs", "transition_runs",
                  "fixture_admissions", "bound_runs"):
        if db.execute(f"SELECT 1 FROM {table} WHERE run_id=?", (run_id,)).fetchone():
            return True
    from . import combined_runs
    for row in combined_runs._rows(db):
        request = combined_runs._raw(row)["request"]
        if any(child["run_id"] == run_id for child in request["children"]):
            return True
    return False


def has_v2_run_or_bound(db: sqlite3.Connection, run_id: str) -> bool:
    """Recognize v2 route rows and valid v2 bundle receipts, not ordinary v1 runs."""
    if db.execute("SELECT 1 FROM eval_runs_v2 WHERE run_id=?", (run_id,)).fetchone():
        return True
    row = db.execute("SELECT bundle_json,bundle_digest FROM bound_runs WHERE run_id=?", (run_id,)).fetchone()
    if row is None:
        return False
    try:
        value = run_evidence._load(row["bundle_json"], row["bundle_digest"])
    except run_evidence.EvidenceError:
        # A damaged existing binding cannot safely participate in a cross-version write.
        return True
    return (type(value) is dict and type(value.get("schema_version")) is int
            and value["schema_version"] == 2 and value.get("kind") == "bound_partitioned_run")


def _route_row(db: sqlite3.Connection, run_id: str):
    row = db.execute("SELECT * FROM eval_runs_v2 WHERE run_id=?", (run_id,)).fetchone()
    if row is None:
        _fail("RUN_MISSING")
    try:
        require_id(row["run_id"])
        require_id(row["contract_series_id"])
        require_digest(row["manifest_digest"])
        require_digest(row["bundle_digest"])
        require_digest(row["extension_digest"])
    except ContractError:
        _fail("STORAGE_CORRUPT")
    if (type(row["contract_generation"]) is not int or row["contract_generation"] != 1
            or type(row["permission_generation"]) is not int
            or not 0 <= row["permission_generation"] <= MAX_INTEGER
            or type(row["created_at"]) is not int or not 0 <= row["created_at"] <= MAX_INTEGER):
        _fail("STORAGE_CORRUPT")
    return row


def _resolve_context(extension: Any, store: Any, db: sqlite3.Connection, now: int,
                     receipt: dict[str, Any], stored_profile: dict[str, Any]) -> dict[str, Any]:
    run_id = receipt["manifest_ref"]["id"]
    route = _route_row(db, run_id)
    manifest = _load_manifest(route)
    manifest_ref = content_ref("run_manifest", manifest["run_id"], manifest)
    if (manifest_ref != receipt["manifest_ref"] or manifest["run_id"] != run_id
            or manifest["plan_ref"] != receipt["plan_index_ref"]
            or manifest["contract_ref"] != receipt["contract_ref"]
            or manifest["policy_ref"] != receipt["policy_ref"]
            or route["bundle_digest"] != run_evidence._pack(receipt)[1]
            or route["contract_generation"] != 1
            or route["permission_generation"] != store._permission_generation(db)
            or route["extension_digest"] != store._extension_digest):
        _fail("BINDING_MISMATCH")
    context, rebound = _fresh_context(
        extension, store, db, manifest, route["contract_series_id"], route["contract_generation"],
        stored_profile, now,
    )
    if rebound != receipt:
        _fail("BINDING_MISMATCH")
    return context


def _book(extension: Any, store: Any, db: sqlite3.Connection, now: int,
          receipt: dict[str, Any], profile: dict[str, Any]) -> PartitionedRunEvidenceBook:
    digest = run_evidence._pack(receipt)[1]
    return PartitionedRunEvidenceBook(
        db, now=now, allowed_bindings={receipt["manifest_ref"]["id"]: digest},
        resolve_context=lambda connection, clock, saved_receipt, saved_profile: _resolve_context(
            extension, store, connection, clock, saved_receipt, saved_profile
        ),
    )


def _load_route_binding(extension: Any, store: Any, db: sqlite3.Connection, run_id: str, now: int):
    route = _route_row(db, run_id)
    manifest = _load_manifest(route)
    profile_row = db.execute("SELECT * FROM bound_runs WHERE run_id=?", (run_id,)).fetchone()
    if profile_row is None:
        _fail("STORAGE_CORRUPT")
    try:
        receipt = PartitionedRunEvidenceBook._receipt(profile_row["bundle_json"], profile_row["bundle_digest"])
        profile = run_evidence._load(profile_row["profile_json"], profile_row["profile_digest"])
    except run_evidence.EvidenceError as error:
        _fail(error.code)
    if (route["bundle_digest"] != profile_row["bundle_digest"]
            or route["extension_digest"] != store._extension_digest
            or route["permission_generation"] != store._permission_generation(db)
            or receipt["manifest_ref"] != content_ref("run_manifest", run_id, manifest)):
        _fail("BINDING_MISMATCH")
    _fresh_context(extension, store, db, manifest, route["contract_series_id"],
                   route["contract_generation"], profile, now)
    return route, receipt, profile, _book(extension, store, db, now, receipt, profile)


def _verified_resource_snapshot(db: sqlite3.Connection, route: sqlite3.Row,
                                manifest: dict[str, Any], policy: dict[str, Any],
                                now: int) -> dict[str, Any]:
    book = resources.ResourceBook(db)
    try:
        row = book._run(route["run_id"])
        saved_policy = book._policy(row)
    except resources.ResourceError as error:
        _fail("STORAGE_CORRUPT" if error.code == "RUN_MISSING" else error.code)
    except (ContractError, TypeError, ValueError, KeyError, RecursionError):
        _fail("STORAGE_CORRUPT")
    if (row["run_id"] != route["run_id"]
            or row["manifest_digest"] != route["manifest_digest"]
            or saved_policy != policy
            or row["profile"] != manifest["profile"]
            or row["deadline"] != manifest["deadline"]
            or row["created_at"] != route["created_at"]):
        _fail("STORAGE_CORRUPT")
    try:
        return book.snapshot(route["run_id"], now)
    except resources.ResourceError as error:
        _fail(error.code)


def read_resource_operation_v2(extension: Any, store: Any, db: sqlite3.Connection,
                                request: dict[str, Any], now: int) -> dict[str, Any]:
    """Inspect a v2 resource operation against saved immutable bindings only.

    AdoptionStore has already checked the caller role and actor revocation. This is a
    state read, never a start/replay authorization; current permission generation,
    current contract pointer, and run deadline are deliberately not admission gates.
    ResourceBook/dispatch may advance clock or idempotency bookkeeping, but this path
    does not change resource operation, reservation, dispatch, stop, or settlement state.
    """
    if db is not getattr(store, "_db", None) or not db.in_transaction:
        _fail("TRANSACTION_REQUIRED")
    from . import partitioned_run_authority, resource_operation
    if (extension is not getattr(store, "_extension", None)
            or partitioned_run_authority._exact_source_digest(extension) != getattr(store, "_extension_digest", None)):
        _fail("EXTENSION_INVALID")
    try:
        request = resource_operation.validate_request(request)
    except resources.ResourceError as error:
        _fail(error.code)
    run_id = request["run_id"]
    route = _route_row(db, run_id)
    manifest = _load_manifest(route)
    if (manifest["purpose"] != "diagnostic" or manifest["baseline_ref"] is not None
            or route["contract_generation"] != 1
            or route["extension_digest"] != store._extension_digest):
        _fail("SCOPE_UNSUPPORTED")
    if (not manifest["created_at"] <= route["created_at"] < manifest["deadline"]
            or route["created_at"] > now):
        _fail("STORAGE_CORRUPT")
    if (db.execute("SELECT 1 FROM eval_runs WHERE run_id=?", (run_id,)).fetchone()
            or db.execute("SELECT 1 FROM transition_runs WHERE run_id=?", (run_id,)).fetchone()):
        _fail("RUN_CONFLICT")

    route_ref = content_ref("run_manifest", run_id, manifest)
    saved = db.execute("SELECT * FROM bound_runs WHERE run_id=?", (run_id,)).fetchone()
    if saved is None:
        _fail("STORAGE_CORRUPT")
    try:
        receipt = PartitionedRunEvidenceBook._receipt(saved["bundle_json"], saved["bundle_digest"])
        stored_profile = run_evidence._load(saved["profile_json"], saved["profile_digest"])
        profile = run_evidence._profile(stored_profile)
    except run_evidence.EvidenceError as error:
        _fail(error.code)
    if (route["bundle_digest"] != saved["bundle_digest"]
            or receipt["manifest_ref"] != route_ref
            or receipt["contract_ref"] != manifest["contract_ref"]
            or receipt["plan_index_ref"] != manifest["plan_ref"]
            or receipt["policy_ref"] != manifest["policy_ref"]):
        _fail("BINDING_MISMATCH")

    history = db.execute(
        "SELECT * FROM eval_adoptions WHERE series_id=? AND generation=?",
        (route["contract_series_id"], route["contract_generation"]),
    ).fetchone()
    if history is None:
        _fail("CONTRACT_MISSING")
    try:
        contract = validate_evaluation_contract(
            authority._load_json(history, "payload_json", "digest")
        )
        if (history["series_id"] != route["contract_series_id"]
                or history["generation"] != route["contract_generation"]
                or contract["generation"] != 1
                or contract["comparison"]["mode"] != "not_applicable"):
            _fail("STORAGE_CORRUPT")
        validation = authority._assert_contract_history(db, history, contract)
        validation_payload = authority._load_json(validation, "payload_json", "digest")
        if validation_payload.get("permission_generation") != route["permission_generation"]:
            _fail("STORAGE_CORRUPT")
        policy, policy_ref, _policy_generation = authority.EvaluationExtension._pinned_policy(
            store, db, contract, now, require_current=False
        )
        registry = validate_registry(authority._object(db, contract["registry_ref"]))
        case_set = validate_case_set(authority._object(db, contract["case_set_ref"]))
    except AdoptionError:
        raise
    except ContractError as error:
        _fail(getattr(error, "code", "STORAGE_CORRUPT"))
    except (TypeError, ValueError, KeyError, RecursionError, sqlite3.Error):
        _fail("STORAGE_CORRUPT")
    if (content_ref("evaluation_contract", contract["contract_id"], contract) != manifest["contract_ref"]
            or policy_ref != manifest["policy_ref"]):
        _fail("BINDING_MISMATCH")

    plan_row = db.execute(
        "SELECT * FROM partition_plan_commits WHERE plan_id=?", (manifest["plan_ref"]["id"],)
    ).fetchone()
    if plan_row is None:
        _fail("PLAN_MISSING")
    if (plan_row["contract_series_id"] != route["contract_series_id"]
            or plan_row["contract_generation"] != route["contract_generation"]
            or plan_row["permission_generation"] != route["permission_generation"]
            or plan_row["extension_digest"] != route["extension_digest"]):
        _fail("STORAGE_CORRUPT")
    try:
        index, segments, plan = partitioned_plan_store._validate_committed_with_plan(
            db, plan_row, contract, route["permission_generation"], route["extension_digest"]
        )
        if content_ref(partitioned_plan_store.INDEX_KIND, plan_row["plan_id"], index) != manifest["plan_ref"]:
            _fail("BINDING_MISMATCH")
        bound_receipt = bind_partitioned_run_manifest(
            manifest, contract, index, segments, policy, registry, case_set, baseline_context=None
        )
        if bound_receipt != receipt:
            _fail("BINDING_MISMATCH")
        internal = {
            "manifest": manifest, "contract": contract, "plan": plan, "policy": policy,
            "registry": registry, "case_set": case_set,
            "selected_controls": copy.deepcopy(bound_receipt["selected_controls"]),
            "ci_eligible": False,
        }
        execution_profiles.check_plan(profile, internal)
    except AdoptionError:
        raise
    except ContractError as error:
        _fail(getattr(error, "code", "STORAGE_CORRUPT"))
    except (TypeError, ValueError, KeyError, IndexError, RecursionError, sqlite3.Error):
        _fail("STORAGE_CORRUPT")

    resource_book = resources.ResourceBook(db)
    try:
        resource_run = resource_book._run(run_id)
        saved_policy = resource_book._policy(resource_run)
        if (resource_run["manifest_digest"] != route["manifest_digest"]
                or resource_run["profile"] != manifest["profile"]
                or resource_run["deadline"] != manifest["deadline"]
                or resource_run["created_at"] != route["created_at"]
                or content_ref("policy_profile", saved_policy["policy_id"], saved_policy) != manifest["policy_ref"]):
            _fail("STORAGE_CORRUPT")
        binding = db.execute("SELECT scenario FROM resource_bindings WHERE operation_id=?",
                             (request["operation_id"],)).fetchone()
        if binding is not None and type(binding["scenario"]) is str and binding["scenario"].startswith("guardrail:"):
            # v2 diagnostic begin cannot produce a legacy LLM admission; never fall back to it here.
            _fail("STORAGE_CORRUPT")
        bound = {"manifest": manifest, "plan": plan, "contract": contract, "policy": policy,
                 "registry": registry, "case_set": case_set, "ci_eligible": False}
        def read_bound(identifier):
            if identifier != run_id:
                _fail("BINDING_MISMATCH")
            return bound, None
        return resource_operation.bound_operation(db, request, now, read_bound)
    except AdoptionError:
        raise
    except resources.ResourceError as error:
        _fail(error.code)
    except (ContractError, TypeError, ValueError, KeyError, RecursionError):
        _fail("STORAGE_CORRUPT")


def cancel_resource_v2(extension: Any, store: Any, db: sqlite3.Connection,
                       request: dict[str, Any], now: int) -> dict[str, Any]:
    """Cancel/reclaim only a v2 run resource row without fresh admission.

    AdoptionStore has already enforced actor role and revocation. ResourceBook still
    enforces the owner/lease epoch, and all mutations remain in the caller transaction.
    Current contract/permission freshness is intentionally not a stop gate.
    """
    if db is not getattr(store, "_db", None) or not db.in_transaction:
        _fail("TRANSACTION_REQUIRED")
    from . import partitioned_run_authority, resource_authority
    if (extension is not getattr(store, "_extension", None)
            or partitioned_run_authority._exact_source_digest(extension) != getattr(store, "_extension_digest", None)):
        _fail("EXTENSION_INVALID")
    try:
        request = resource_authority.validate_request(request)
    except AdoptionError:
        raise
    except resources.ResourceError as error:
        _fail(error.code)
    action, run_id = request["action"], request["run_id"]
    if action not in {"resource_cancel", "resource_cancel_claim"}:
        _fail("INVALID_ACTION")
    route = _route_row(db, run_id)
    manifest = _load_manifest(route)
    # Never interpret a v2 cancellation through an overlapping legacy run namespace.
    if (db.execute("SELECT 1 FROM eval_runs WHERE run_id=?", (run_id,)).fetchone()
            or db.execute("SELECT 1 FROM transition_runs WHERE run_id=?", (run_id,)).fetchone()):
        _fail("RUN_CONFLICT")
    if route["extension_digest"] != store._extension_digest:
        _fail("EXTENSION_INVALID")
    resource_book = resources.ResourceBook(db)
    try:
        resource_run = resource_book._run(run_id)
        saved_policy = resource_book._policy(resource_run)
        saved_policy_ref = content_ref("policy_profile", saved_policy["policy_id"], saved_policy)
        if (resource_run["manifest_digest"] != route["manifest_digest"]
                or resource_run["profile"] != manifest["profile"]
                or resource_run["deadline"] != manifest["deadline"]
                or resource_run["created_at"] != route["created_at"]
                or saved_policy_ref != manifest["policy_ref"]):
            _fail("STORAGE_CORRUPT")
        fields = resource_authority.execute(
            db, request, now, lambda _run_id: _fail("BINDING_UNAVAILABLE"),
            terminal_pending=False,
        )
    except AdoptionError:
        raise
    except resources.ResourceError as error:
        _fail(error.code)
    except (ContractError, TypeError, ValueError, KeyError, RecursionError):
        _fail("STORAGE_CORRUPT")
    return _result(action, request["request_id"], **fields)


def check_start_v2(extension: Any, store: Any, db: sqlite3.Connection,
                   run_id: str, now: int) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return the freshly rebound v2 manifest/plan for existing resource actions."""
    if db is not getattr(store, "_db", None) or not db.in_transaction:
        _fail("TRANSACTION_REQUIRED")
    try:
        route, _receipt, _profile, book = _load_route_binding(extension, store, db, run_id, now)
        row = book._run_row(db, run_id)
        bound, _fresh_profile, baseline = book._resolve_run_context(db, row, now)
        state = book._state(db, run_id)
    except run_evidence.EvidenceError as error:
        _fail(error.code)
    if state["finalized_at"] is not None or state["state"] == "FINALIZED":
        _fail("RUN_FINALIZED")
    if state["state"] != "OPEN":
        _fail("RUN_HELD")
    if baseline is not None:
        _fail("BASELINE_UNSUPPORTED")
    manifest, plan = bound["manifest"], bound["plan"]
    authority.target_retirement.check_targets(db, manifest["target_refs"], now)
    _verified_resource_snapshot(db, route, manifest, bound["policy"], now)
    return manifest, plan


def _evidence_view(value: dict[str, Any], kind: str) -> dict[str, Any]:
    """Expose the legacy book payload through a schema-v2 partitioned view."""
    result = copy.deepcopy(value)
    result["schema_version"] = 2
    result["kind"] = kind
    result["ci_eligible"] = False
    return result


def _result(action: str, request_id: str, **fields: Any) -> dict[str, Any]:
    return {"schema_version": 1, "kind": "partitioned_run_authority_result", "action": action,
            "request_id": request_id, "authority_connected": False,
            "resource_closure_verified": False, "baseline_freshness_verified": False,
            "adoption_verified": False, "admission_verified": False,
            "ci_eligible": False, **fields}


def handle(extension: Any, store: Any, db: sqlite3.Connection, request: Any,
           actor_id: str, context: str, now: int) -> dict[str, Any]:
    if db is not getattr(store, "_db", None) or not db.in_transaction:
        _fail("TRANSACTION_REQUIRED")
    from . import partitioned_run_authority
    if (extension is not getattr(store, "_extension", None)
            or partitioned_run_authority._exact_source_digest(extension) != getattr(store, "_extension_digest", None)):
        _fail("EXTENSION_INVALID")
    request = validate_request(request)
    action, request_id = request["action"], request["request_id"]
    if type(actor_id) is not str or type(context) is not str:
        _fail("PERMISSION_DENIED")
    if action == "run_begin_v2":
        manifest = request["manifest"]
        run_id = manifest["run_id"]
        existing_route = db.execute("SELECT * FROM eval_runs_v2 WHERE run_id=?", (run_id,)).fetchone()
        v1_run = db.execute("SELECT 1 FROM eval_runs WHERE run_id=?", (run_id,)).fetchone()
        transition_run = db.execute("SELECT 1 FROM transition_runs WHERE run_id=?", (run_id,)).fetchone()
        bound_row = db.execute("SELECT * FROM bound_runs WHERE run_id=?", (run_id,)).fetchone()
        if existing_route is None and has_v2_run_conflict(db, run_id):
            _fail("RUN_CONFLICT")
        if existing_route is not None and (v1_run or transition_run or bound_row is None):
            _fail("STORAGE_CORRUPT")
        profile = request["execution_profile"]
        bound_context, receipt = _fresh_context(
            extension, store, db, manifest, request["contract_series_id"],
            request["expected_generation"], profile, now,
        )
        manifest_raw, manifest_digest = authority._packed(manifest)
        bundle_digest = run_evidence._pack(receipt)[1]
        permission_generation = store._permission_generation(db)
        extension_digest = store._extension_digest
        if existing_route is None:
            db.execute(
                "INSERT INTO eval_runs_v2 VALUES(?,?,?,?,?,?,?,?,?)",
                (run_id, manifest_raw, manifest_digest, bundle_digest, request["contract_series_id"],
                 request["expected_generation"], permission_generation, extension_digest, now),
            )
            try:
                resource_snapshot = resources.ResourceBook(db).create_run(
                    run_id, manifest_digest, bound_context["policy"], manifest["profile"],
                    request_id, now, manifest["deadline"],
                )
            except resources.ResourceError as error:
                _fail(error.code)
            except ContractError as error:
                _fail(error.code)
        elif (existing_route["manifest_json"] != manifest_raw
                or existing_route["manifest_digest"] != manifest_digest
                or existing_route["bundle_digest"] != bundle_digest
                or existing_route["contract_series_id"] != request["contract_series_id"]
                or existing_route["contract_generation"] != request["expected_generation"]
                or existing_route["permission_generation"] != permission_generation
                or existing_route["extension_digest"] != extension_digest):
            _fail("RUN_CONFLICT")
        else:
            resource_snapshot = _verified_resource_snapshot(
                db, existing_route, manifest, bound_context["policy"], now,
            )
        book = _book(extension, store, db, now, receipt, profile)
        view = book.start_partitioned_run(receipt, profile)
        return _result(action, request_id, run_id=run_id, run=view,
                       resource_snapshot=resource_snapshot, duplicate=existing_route is not None)
    if action == "run_status_v2":
        route, receipt, profile, book = _load_route_binding(extension, store, db, request["run_id"], now)
        view = book.get_run(request["run_id"])
        if view["bundle_digest"] != route["bundle_digest"]:
            _fail("STORAGE_CORRUPT")
        return _result(action, request_id, run=view)
    if action == "evidence_record_v2":
        _, _, _, book = _load_route_binding(extension, store, db, request["run_id"], now)
        evidence = _evidence_view(book.record_attempt(request["attempt"]), "partitioned_attempt_receipt")
        return _result(action, request_id, evidence=evidence)
    if action == "evidence_attempt_v2":
        row = db.execute("SELECT run_id FROM attempts WHERE attempt_id=?", (request["attempt_id"],)).fetchone()
        if row is None:
            _fail("ATTEMPT_NOT_FOUND")
        _, _, _, book = _load_route_binding(extension, store, db, row["run_id"], now)
        return _result(action, request_id, evidence=book.get_attempt(request["attempt_id"]))
    if action == "evidence_finalize_v2":
        _, _, _, book = _load_route_binding(extension, store, db, request["run_id"], now)
        evidence = _evidence_view(book.finalize(request["run_id"]), "partitioned_run_evidence_finalization")
        return _result(action, request_id, evidence=evidence)
    if action == "evidence_terminal_v2":
        _, _, _, book = _load_route_binding(extension, store, db, request["run_id"], now)
        evidence = _evidence_view(book.get_terminal(request["run_id"]), "partitioned_run_evidence_finalization")
        return _result(action, request_id, evidence=evidence)
    if action == "evidence_current_v2":
        _, receipt, profile, book = _load_route_binding(extension, store, db, request["run_id"], now)
        row = book._run_row(db, request["run_id"])
        bound, current_profile, baseline = book._resolve_run_context(db, row, now)
        binding = book._binding_summary(bound, current_profile, baseline)
        current = book.current_use(request["run_id"], binding)
        if request["expected_bundle_digest"] != run_evidence._pack(receipt)[1]:
            current["reasons"] = list(current.get("reasons", [])) + ["BINDING_MISMATCH"]
        current["ci_eligible"] = False
        current["authority_connected"] = False
        evidence = _evidence_view(current, "partitioned_run_use_decision")
        return _result(action, request_id, evidence=evidence)
    _fail("INVALID_ACTION")


__all__ = ["ACTIONS", "FRESH_ACTIONS", "TABLES", "create_schema", "handle", "validate_request"]
