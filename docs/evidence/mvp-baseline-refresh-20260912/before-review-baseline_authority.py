"""保存済みrunから初回baseline候補を作り、同一DBで採択を記録する境界。

この部品はOS認証、Evidenceの作成、通常CIの許可を行わない。呼出側で認証済みの
主体と開始済みtransactionを渡し、保存済みsource graphだけを再検査して使う。
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import sqlite3
from typing import Any, Callable

from .adoption import AdoptionError
from .baselines import repeat_config_for_plan, bind_baseline_record, validate_baseline_record, validate_comparison_context
from .contracts import ContractError, MAX_DOCUMENT_BYTES, MAX_INTEGER, require_digest, require_id, require_object
from .run_contracts import content_ref
from .wire import canonical_bytes


_SCHEMA_VERSION = 1
_MAX_FRESHNESS = 30 * 86400
_TABLES = {
    "baseline_proposals", "baseline_validations",
    "baseline_adoptions", "baseline_current", "baseline_revocations",
}
_COLUMNS = {
    "baseline_proposals": {
        "proposal_id", "series_id", "run_id", "expected_generation", "contract_generation",
        "proposal_json", "proposal_digest", "created_at", "actor_id", "context",
    },
    "baseline_validations": {
        "validation_id", "proposal_id", "proposal_digest", "validation_json", "validation_digest",
        "created_at", "expires_at", "permission_generation", "actor_id", "context",
    },
    "baseline_adoptions": {
        "adoption_id", "series_id", "generation", "proposal_id", "validation_id",
        "baseline_json", "baseline_digest", "adopted_at", "actor_id", "context",
        "permission_generation",
    },
    "baseline_current": {
        "series_id", "generation", "adoption_id", "baseline_json", "baseline_digest",
    },
    "baseline_revocations": {"series_id", "generation", "observed_at", "actor_id", "context"},
}
TABLES = {name: set(columns) for name, columns in _COLUMNS.items()}
COLUMNS = {name: set(columns) for name, columns in _COLUMNS.items()}

_ROLES = {"manager", "validator", "operator"}
ACTIONS = {
    "baseline_propose": {"manager"},
    "baseline_validate": {"validator"},
    "baseline_adopt": {"manager"},
    "baseline_current": _ROLES,
    "baseline_use": _ROLES,
    "baseline_resolve": _ROLES,
    "baseline_revoke_ref": {"operator"},
    "baseline_revoke": {"operator"},
}
FRESH_ACTIONS = {"baseline_current", "baseline_use", "baseline_resolve"}
_BASE = {"schema_version", "action", "request_id"}
FIELDS = {
    "baseline_propose": _BASE | {"proposal_id", "series_id", "run_id", "expected_generation"},
    "baseline_validate": _BASE | {"proposal_id", "validation_id"},
    "baseline_adopt": _BASE | {"proposal_id", "validation_id", "expected_generation"},
    "baseline_current": _BASE | {"series_id"},
    "baseline_use": _BASE | {"series_id", "expected_baseline_ref", "expected_contract_ref"},
    "baseline_revoke": _BASE | {"series_id"},
    "baseline_resolve": _BASE | {"series_id", "expected_baseline_ref", "expected_contract_ref"},
    "baseline_revoke_ref": _BASE | {"series_id", "expected_baseline_ref", "expected_contract_ref"},
}


def _error(code: str = "INVALID_REQUEST") -> AdoptionError:
    return AdoptionError(code)


def _id(value: Any, code: str = "INVALID_REQUEST") -> None:
    try:
        require_id(value)
    except ContractError:
        raise _error(code) from None


def _digest(value: Any, code: str = "INVALID_REQUEST") -> None:
    try:
        require_digest(value)
    except ContractError:
        raise _error(code) from None


def _json_bytes(value: Any) -> bytes:
    pending = [(value, 0)]
    nodes = 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if depth > 16 or nodes > 100_000:
            raise _error("DOCUMENT_COMPLEXITY")
        if type(item) is dict:
            if any(type(key) is not str for key in item):
                raise _error("INVALID_REQUEST")
            pending.extend((child, depth + 1) for child in item.values())
        elif type(item) is list:
            pending.extend((child, depth + 1) for child in item)
        elif type(item) is int:
            if not -MAX_INTEGER <= item <= MAX_INTEGER:
                raise _error("INTEGER_RANGE")
        elif item is not None and type(item) not in (str, bool):
            raise _error("INVALID_REQUEST")
    try:
        raw = canonical_bytes(value)
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise _error("INVALID_REQUEST") from None
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise _error("DOCUMENT_SIZE")
    return raw


def _pack(value: Any) -> tuple[str, str]:
    raw = _json_bytes(value)
    return raw.decode("utf-8"), hashlib.sha256(raw).hexdigest()


def _load(raw: Any, stored_digest: Any) -> dict[str, Any]:
    if type(raw) is not str or type(stored_digest) is not str:
        raise _error("STORAGE_CORRUPT")
    _digest(stored_digest, "STORAGE_CORRUPT")
    try:
        value = json.loads(raw, object_pairs_hook=_unique_pairs, parse_float=_reject_number,
                          parse_constant=_reject_number)
        packed, digest = _pack(value)
    except AdoptionError:
        raise _error("STORAGE_CORRUPT") from None
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise _error("STORAGE_CORRUPT") from None
    if type(value) is not dict or packed != raw or digest != stored_digest:
        raise _error("STORAGE_CORRUPT")
    return value


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _reject_number(_: str) -> None:
    raise ValueError("non-integer number")


def _uint(value: Any, *, allow_minus_one: bool = False) -> None:
    if type(value) is not int or value > MAX_INTEGER or (value < -1 if allow_minus_one else value < 0):
        raise _error("INVALID_GENERATION")


def _ref(value: Any, kind: str | None = None) -> dict[str, str]:
    if type(value) is not dict or set(value) != {"kind", "id", "digest"}:
        raise _error("INVALID_REFERENCE")
    _id(value["kind"], "INVALID_REFERENCE")
    _id(value["id"], "INVALID_REFERENCE")
    _digest(value["digest"], "INVALID_REFERENCE")
    if kind is not None and value["kind"] != kind:
        raise _error("REFERENCE_KIND")
    return value


def _schema(db: sqlite3.Connection) -> None:
    try:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not _TABLES.issubset(tables):
            raise _error("UNSUPPORTED_STORE")
        for table, columns in _COLUMNS.items():
            found = {row[1] for row in db.execute('PRAGMA table_info("' + table + '")')}
            if found != columns:
                raise _error("UNSUPPORTED_STORE")
    except AdoptionError:
        raise
    except (sqlite3.Error, KeyError, TypeError, ValueError):
        raise _error("STORAGE_CORRUPT") from None


def create_schema(db: sqlite3.Connection) -> None:
    """呼出側が開始したtransaction内でだけbaseline表を作成する。"""
    if not isinstance(db, sqlite3.Connection) or not db.in_transaction:
        raise _error("TRANSACTION_REQUIRED")
    try:
        existing = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if existing.intersection(_TABLES):
            _schema(db)
            return
        db.execute("CREATE TABLE baseline_proposals(proposal_id TEXT PRIMARY KEY, series_id TEXT NOT NULL, run_id TEXT NOT NULL, expected_generation INTEGER NOT NULL, contract_generation INTEGER NOT NULL, proposal_json TEXT NOT NULL, proposal_digest TEXT NOT NULL, created_at INTEGER NOT NULL, actor_id TEXT NOT NULL, context TEXT NOT NULL)")
        db.execute("CREATE TABLE baseline_validations(validation_id TEXT PRIMARY KEY, proposal_id TEXT NOT NULL, proposal_digest TEXT NOT NULL, validation_json TEXT NOT NULL, validation_digest TEXT NOT NULL, created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, permission_generation INTEGER NOT NULL, actor_id TEXT NOT NULL, context TEXT NOT NULL)")
        db.execute("CREATE TABLE baseline_adoptions(adoption_id TEXT PRIMARY KEY, series_id TEXT NOT NULL, generation INTEGER NOT NULL, proposal_id TEXT NOT NULL, validation_id TEXT NOT NULL, baseline_json TEXT NOT NULL, baseline_digest TEXT NOT NULL, adopted_at INTEGER NOT NULL, actor_id TEXT NOT NULL, context TEXT NOT NULL, permission_generation INTEGER NOT NULL)")
        db.execute("CREATE TABLE baseline_current(series_id TEXT PRIMARY KEY, generation INTEGER NOT NULL, adoption_id TEXT NOT NULL, baseline_json TEXT NOT NULL, baseline_digest TEXT NOT NULL)")
        db.execute("CREATE TABLE baseline_revocations(series_id TEXT NOT NULL, generation INTEGER NOT NULL, observed_at INTEGER NOT NULL, actor_id TEXT NOT NULL, context TEXT NOT NULL, PRIMARY KEY(series_id,generation))")
        _schema(db)
    except AdoptionError:
        raise
    except sqlite3.Error:
        raise _error("STORAGE_FAILURE") from None


def validate_request(request: Any) -> dict[str, Any]:
    """baseline authorityの要求を厳格検査し、入力のdeepcopyを返す。"""
    if type(request) is not dict:
        raise _error()
    action = request.get("action")
    if type(action) is not str or action not in FIELDS:
        raise _error("INVALID_ACTION")
    if set(request) != FIELDS[action]:
        raise _error("INVALID_REQUEST")
    if type(request["schema_version"]) is not int or request["schema_version"] != 1:
        raise _error("UNSUPPORTED_VERSION")
    _id(request["request_id"], "INVALID_REQUEST_ID")
    for field in ("proposal_id", "series_id", "run_id", "validation_id"):
        if field in request:
            _id(request[field])
    if action in {"baseline_resolve", "baseline_revoke_ref"}:
        _ref(request["expected_baseline_ref"], "baseline")
        _ref(request["expected_contract_ref"], "evaluation_contract")
    if "expected_generation" in request:
        _uint(request["expected_generation"])
    _json_bytes(request)
    return deepcopy(request)


def _trusted_binding(bound: dict[str, Any]) -> str:
    try:
        from . import run_evidence
        derived = run_evidence.bound_bundle_digest(bound)
    except Exception:
        raise _error("BINDING_INVALID") from None
    return derived


def _source_impl(source: Any, run_id: str, now: int, store: Any, *, purpose="baseline_candidate") -> dict[str, Any]:
    required = {"bound", "receipt", "decision", "evidences", "closure", "evidence_states", "reasons"}
    if type(source) is not dict or set(source) != required:
        raise _error("SOURCE_INVALID")
    bound = source["bound"]
    if type(bound) is not dict or type(bound.get("manifest")) is not dict:
        raise _error("SOURCE_INVALID")
    manifest = bound["manifest"]
    if purpose not in {"baseline_candidate", "regression"} or manifest.get("run_id") != run_id or manifest.get("purpose") != purpose:
        raise _error("SOURCE_INVALID")
    _trusted_binding(bound)
    if type(source["reasons"]) is not list or any(type(reason) is not str for reason in source["reasons"]):
        raise _error("SOURCE_INVALID")
    receipt = source["receipt"]
    decision = source["decision"]
    closure = source["closure"]
    if type(receipt) is not dict or type(decision) is not dict or type(closure) is not dict:
        raise _error("SOURCE_INVALID")
    if receipt.get("run_id") != run_id:
        raise _error("SOURCE_INVALID")
    if decision.get("kind") != "run_decision" or decision.get("run_id") != run_id:
        raise _error("SOURCE_INVALID")
    if closure.get("kind") != "resource_closure" or closure.get("run_id") != run_id:
        raise _error("SOURCE_INVALID")
    receipt_refs = {
        "manifest_ref": content_ref("run_manifest", run_id, manifest),
        "bundle_ref": {"kind": "bound_bundle", "id": run_id, "digest": _trusted_binding(bound)},
        "decision_ref": content_ref("run_decision", decision.get("decision_id", run_id), decision),
        "closure_ref": content_ref("resource_closure", closure.get("closure_id", run_id), closure),
    }
    if any(receipt.get(field) != expected for field, expected in receipt_refs.items()):
        raise _error("SOURCE_INVALID")
    evidences = source["evidences"]
    states = source["evidence_states"]
    if type(evidences) is not list or not evidences or type(states) is not dict:
        raise _error("SOURCE_INVALID")
    if set(states) != {item.get("evidence_id") for item in evidences if type(item) is dict}:
        raise _error("SOURCE_INVALID")
    seen_evidence: set[str] = set()
    for evidence in evidences:
        if type(evidence) is not dict or evidence.get("kind") != "evidence":
            raise _error("SOURCE_INVALID")
        evidence_id = evidence.get("evidence_id")
        _id(evidence_id, "SOURCE_INVALID")
        if evidence_id in seen_evidence:
            raise _error("SOURCE_INVALID")
        seen_evidence.add(evidence_id)
        if evidence.get("subject_ref") != content_ref("run_manifest", run_id, manifest):
            raise _error("SOURCE_INVALID")
        if type(evidence.get("observed_at")) is not int or evidence["observed_at"] > now:
            raise _error("SOURCE_INVALID")
        state = states[evidence["evidence_id"]]
        if type(state) is not dict or state.get("revoked") is not False or state.get("deleted") is not False:
            raise _error("PREREQUISITE_UNAVAILABLE")
        valid_until = evidence.get("valid_until")
        if type(valid_until) is not int or not 0 <= valid_until <= MAX_INTEGER:
            raise _error("SOURCE_INVALID")
    if len(evidences) != 1 or receipt.get("evidence_ref") != content_ref("evidence", evidences[0]["evidence_id"], evidences[0]):
        raise _error("SOURCE_INVALID")
    return deepcopy(source)


def _source(source: Any, run_id: str, now: int, store: Any, *, purpose="baseline_candidate") -> dict[str, Any]:
    try:
        return _source_impl(source, run_id, now, store, purpose=purpose)
    except AdoptionError:
        raise
    except (ContractError, KeyError, TypeError, ValueError, RecursionError):
        raise _error("SOURCE_INVALID") from None


def _ready_source(source: Any, run_id: str, now: int, store: Any, *, purpose="baseline_candidate") -> dict[str, Any]:
    """検証・採択・利用に必要な実行完了条件を追加検査する。"""
    checked = _source(source, run_id, now, store, purpose=purpose)
    if checked["reasons"] != []:
        raise _error("PREREQUISITE_UNAVAILABLE")
    if checked["receipt"].get("input_materialization_verified") is not True:
        raise _error("PREREQUISITE_UNAVAILABLE")
    if checked["decision"].get("assurance") not in {"HEALTHY", "WARNING"}:
        raise _error("PREREQUISITE_UNAVAILABLE")
    if checked["closure"].get("budget_closure") is not True:
        raise _error("PREREQUISITE_UNAVAILABLE")
    for evidence in checked["evidences"]:
        if now > evidence["valid_until"]:
            raise _error("PREREQUISITE_UNAVAILABLE")
    return checked


def _refs_from_source(source: dict[str, Any]) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    bound = source["bound"]
    contract = bound.get("contract")
    case_set = bound.get("case_set")
    if type(contract) is not dict or type(case_set) is not dict:
        raise _error("SOURCE_INVALID")
    evaluator_refs = deepcopy(contract.get("evaluator_refs"))
    if type(evaluator_refs) is not list or not evaluator_refs:
        raise _error("SOURCE_INVALID")
    oracle_refs: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for case in case_set.get("cases", []):
        if type(case) is not dict:
            raise _error("SOURCE_INVALID")
        ref = case.get("oracle_ref")
        _ref(ref, "oracle")
        key = tuple(ref[item] for item in ("kind", "id", "digest"))
        if key not in seen:
            seen.add(key)
            oracle_refs.append(deepcopy(ref))
    if not oracle_refs:
        raise _error("SOURCE_INVALID")
    return evaluator_refs, oracle_refs


def _build_candidate(source: dict[str, Any], series_id: str, proposal_id: str, now: int, *, expected_generation=0) -> dict[str, Any]:
    """保存済み初回candidateからbaseline recordと比較contextを決定的に作る。"""
    _id(series_id)
    _id(proposal_id)
    _uint(now)
    if type(expected_generation) is not int or expected_generation not in {0, 1}:
        raise _error("GENERATION_CONFLICT")
    if type(source) is not dict or type(source.get("bound")) is not dict:
        raise _error("SOURCE_INVALID")
    bound = source["bound"]
    manifest = bound.get("manifest")
    contract = bound.get("contract")
    policy = bound.get("policy")
    registry = bound.get("registry")
    case_set = bound.get("case_set")
    plan = bound.get("plan")
    receipt = source.get("receipt")
    if any(type(item) is not dict for item in (manifest, contract, policy, registry, case_set, plan, receipt)):
        raise _error("SOURCE_INVALID")
    run_id = manifest.get("run_id")
    _id(run_id)
    evaluator_refs, oracle_refs = _refs_from_source(source)
    evidence_refs: list[dict[str, str]] = []
    for evidence in source.get("evidences", []):
        evidence_refs.append(content_ref("evidence", evidence["evidence_id"], evidence))
    if not evidence_refs:
        raise _error("SOURCE_INVALID")
    decision = source.get("decision")
    closure = source.get("closure")
    decision_id = decision.get("decision_id", run_id) if type(decision) is dict else run_id
    closure_id = closure.get("closure_id", run_id) if type(closure) is dict else run_id
    decision_ref = content_ref("run_decision", decision_id, decision)
    closure_ref = content_ref("resource_closure", closure_id, closure)
    comparison = {
        "schema_version": 1, "kind": "comparison_context",
        "comparison_id": "comparison-" + proposal_id,
        "mode": contract.get("comparison", {}).get("mode"),
        "baseline_ref": deepcopy(contract.get("comparison", {}).get("baseline_ref")),
        "reason": contract.get("comparison", {}).get("reason"),
        "changed_axes": deepcopy(contract.get("comparison", {}).get("changed_axes")),
        "expected_contract_generation": contract["generation"], "expected_baseline_generation": expected_generation,
        "contract_ref": deepcopy(manifest["contract_ref"]),
        "policy_ref": deepcopy(manifest["policy_ref"]),
        "target_refs": deepcopy(manifest["target_refs"]),
        "evaluator_refs": deepcopy(evaluator_refs),
        "case_set_ref": content_ref("case_set", case_set["case_set_id"], case_set),
        "oracle_refs": deepcopy(oracle_refs),
        "repeat_config_ref": content_ref("repeat_config", repeat_config_for_plan(plan)["repeat_config_id"], repeat_config_for_plan(plan)),
    }
    record = {
        "schema_version": 1, "kind": "baseline",
        "baseline_id": "baseline-" + proposal_id,
        "baseline_series_id": series_id, "generation": expected_generation + 1,
        "contract_ref": deepcopy(manifest["contract_ref"]),
        "policy_ref": deepcopy(manifest["policy_ref"]),
        "registry_ref": content_ref("control_registry", registry["registry_id"], registry),
        "case_set_ref": deepcopy(comparison["case_set_ref"]),
        "target_refs": deepcopy(manifest["target_refs"]),
        "evaluator_refs": deepcopy(evaluator_refs), "oracle_refs": deepcopy(oracle_refs),
        "repeat_config_ref": deepcopy(comparison["repeat_config_ref"]),
        "source_run_ref": content_ref("run_manifest", run_id, manifest),
        "trial_plan_ref": content_ref("trial_plan", plan["plan_id"], plan),
        "decision_ref": decision_ref, "evidence_refs": evidence_refs,
        "resource_closure_ref": closure_ref,
        "comparison_context_ref": content_ref("comparison_context", comparison["comparison_id"], comparison),
        "created_at": now,
        "valid_until": min(evidence["valid_until"] for evidence in source["evidences"]),
    }
    _json_bytes(record)
    _json_bytes(comparison)
    validate_baseline_record(record)
    validate_comparison_context(comparison)
    return {"record": record, "comparison_context": comparison}


def build_candidate(source: dict[str, Any], series_id: str, proposal_id: str, now: int, *, expected_generation=0) -> dict[str, Any]:
    """保存済み初回candidateから固定形を作り、構造例外を固定errorへ変換する。"""
    try:
        return _build_candidate(source, series_id, proposal_id, now, expected_generation=expected_generation)
    except AdoptionError:
        raise
    except (KeyError, TypeError, ValueError, RecursionError):
        raise _error("SOURCE_INVALID") from None


def _permission_generation(store: Any, db: sqlite3.Connection) -> int:
    value = store._permission_generation(db)
    if type(value) is not int or not 0 <= value <= MAX_INTEGER:
        raise _error("STORAGE_CORRUPT")
    return value


def _now_guard(now: int) -> None:
    _uint(now)


def _source_from_resolver(resolve_source: Callable[..., Any], run_id: str) -> Any:
    if not callable(resolve_source):
        raise _error("SOURCE_UNAVAILABLE")
    try:
        return resolve_source(run_id)
    except AdoptionError:
        raise
    except Exception:
        raise _error("SOURCE_UNAVAILABLE") from None


def _result(action: str, request_id: str, **fields: Any) -> dict[str, Any]:
    return {"schema_version": 1, "kind": "baseline_authority_result", "action": action,
            "request_id": request_id, "ci_eligible": False, **fields}


def _validate_proposal_row(row: sqlite3.Row) -> dict[str, Any]:
    if row is None or type(row["proposal_digest"]) is not str:
        raise _error("STORAGE_CORRUPT")
    payload = _load(row["proposal_json"], row["proposal_digest"])
    if type(payload) is dict and payload.get("kind") == "baseline_refresh_proposal":
        from .baseline_generations import validate_proposal_row
        return validate_proposal_row(row, payload)
    require_object(payload, {"schema_version", "kind", "proposal_id", "series_id", "run_id",
        "expected_generation", "contract_generation", "record", "comparison_context", "binding_digest", "created_at"})
    if (type(payload["schema_version"]) is not int or payload["schema_version"] != 1
            or payload.get("kind") != "baseline_proposal"
            or payload.get("proposal_id") != row["proposal_id"]
            or payload.get("series_id") != row["series_id"]
            or payload.get("run_id") != row["run_id"]
            or payload.get("expected_generation") != row["expected_generation"]
            or payload.get("contract_generation") != row["contract_generation"]
            or type(row["created_at"]) is not int
            or not 0 <= row["created_at"] <= MAX_INTEGER
            or payload["created_at"] != row["created_at"]
            or row["actor_id"] != "manager" or row["context"] != "manager-context"
            or type(row["expected_generation"]) is not int or row["expected_generation"] != 0
            or type(row["contract_generation"]) is not int or row["contract_generation"] != 1):
        raise _error("STORAGE_CORRUPT")
    validate_baseline_record(payload["record"])
    validate_comparison_context(payload["comparison_context"])
    if (payload["record"]["created_at"] != payload["created_at"]
            or payload["record"]["baseline_series_id"] != row["series_id"]
            or payload["record"]["source_run_ref"]["id"] != row["run_id"]):
        raise _error("STORAGE_CORRUPT")
    return payload


def _bind_candidate(proposal, source, now, *, db=None):
    if proposal["kind"] == "baseline_refresh_proposal":
        from .baseline_generations import bind_candidate
        return bind_candidate(db, proposal, source, now)
    candidate = build_candidate(source, proposal["series_id"], proposal["proposal_id"], proposal["created_at"])
    if candidate != {"record": proposal["record"], "comparison_context": proposal["comparison_context"]}:
        raise _error("PROPOSAL_MISMATCH")
    if _trusted_binding(source["bound"]) != proposal["binding_digest"]:
        raise _error("BINDING_MISMATCH")
    enriched = {**source["bound"], "comparison_context": candidate["comparison_context"],
                "repeat_config": repeat_config_for_plan(source["bound"]["plan"])}
    bind_baseline_record(candidate["record"], bound_run=enriched, decision=source["decision"],
        evidences=source["evidences"], closure=source["closure"], now=now,
        evidence_states=source["evidence_states"])
    return candidate


def _validation(db, validation_id, proposal_row, proposal):
    row = db.execute("SELECT * FROM baseline_validations WHERE validation_id=?", (validation_id,)).fetchone()
    if row is None:
        raise _error("VALIDATION_MISMATCH")
    value = _load(row["validation_json"], row["validation_digest"])
    require_object(value, {"schema_version", "kind", "validation_id", "proposal_id", "proposal_digest",
        "record_digest", "observed_at", "expires_at", "permission_generation", "passed", "ci_eligible"})
    if (type(value["schema_version"]) is not int or value["schema_version"] != 1
            or value["kind"] != "baseline_validation" or value["validation_id"] != validation_id
            or value["proposal_id"] != proposal["proposal_id"] or row["proposal_id"] != proposal["proposal_id"]
            or row["proposal_digest"] != proposal_row["proposal_digest"]
            or value["proposal_digest"] != row["proposal_digest"]
            or value["record_digest"] != _pack(proposal["record"])[1]
            or row["actor_id"] != "validator" or row["context"] != "validator-context"
            or any(type(value[key]) is not int for key in ("observed_at", "expires_at", "permission_generation"))
            or any(type(row[key]) is not int for key in ("created_at", "expires_at", "permission_generation"))
            or not proposal["created_at"] <= row["created_at"] < row["expires_at"] <= MAX_INTEGER
            or value["observed_at"] != row["created_at"] or value["expires_at"] != row["expires_at"]
            or not 0 <= row["permission_generation"] <= MAX_INTEGER
            or value["permission_generation"] != row["permission_generation"]
            or value["passed"] is not True or value["ci_eligible"] is not False):
        raise _error("STORAGE_CORRUPT")
    return row


def _live_validation(store, db, row, now):
    if (not row["created_at"] <= now < row["expires_at"]
            or row["permission_generation"] != _permission_generation(store, db)
            or store._actor_revoked(db, "manager") or store._actor_revoked(db, "validator")):
        raise _error("VALIDATION_EXPIRED")


def _history(db, current):
    adopted = db.execute("SELECT * FROM baseline_adoptions WHERE adoption_id=?", (current["adoption_id"],)).fetchone()
    if adopted is None:
        raise _error("STORAGE_CORRUPT")
    if adopted["generation"] != 1:
        from .baseline_generations import history
        return history(db, current, adopted)
    proposal_row = db.execute("SELECT * FROM baseline_proposals WHERE proposal_id=?", (adopted["proposal_id"],)).fetchone()
    proposal = _validate_proposal_row(proposal_row)
    validation = _validation(db, adopted["validation_id"], proposal_row, proposal)
    baseline = _load(adopted["baseline_json"], adopted["baseline_digest"])
    if (baseline != proposal["record"] or _load(current["baseline_json"], current["baseline_digest"]) != baseline
            or adopted["baseline_digest"] != current["baseline_digest"]
            or adopted["series_id"] != current["series_id"] or adopted["series_id"] != proposal["series_id"]
            or type(current["generation"]) is not int or current["generation"] != 1 or adopted["generation"] != 1
            or adopted["actor_id"] != proposal_row["actor_id"] or adopted["context"] != proposal_row["context"]
            or type(adopted["adopted_at"]) is not int
            or not validation["created_at"] <= adopted["adopted_at"] < validation["expires_at"]
            or adopted["permission_generation"] != validation["permission_generation"]):
        raise _error("STORAGE_CORRUPT")
    return proposal, baseline, validation


def _current_generation(db: sqlite3.Connection, series_id: str) -> int:
    row = db.execute("SELECT generation FROM baseline_current WHERE series_id=?", (series_id,)).fetchone()
    if row is None:
        return 0
    if type(row[0]) is not int or not 0 <= row[0] <= MAX_INTEGER:
        raise _error("STORAGE_CORRUPT")
    return row[0]


def _execute_propose(store: Any, db: sqlite3.Connection, request: dict[str, Any], actor: str, context: str, now: int, resolve_source: Callable[..., Any]) -> dict[str, Any]:
    if request["expected_generation"] != 0:
        from .baseline_generations import propose
        return propose(store, db, request, actor, context, now, resolve_source)
    if _current_generation(db, request["series_id"]):
        raise _error("PREREQUISITE_UNAVAILABLE")
    source = _source(_source_from_resolver(resolve_source, request["run_id"]), request["run_id"], now, store)
    candidate = build_candidate(source, request["series_id"], request["proposal_id"], now)
    binding_digest = _trusted_binding(source["bound"])
    proposal = {"schema_version": 1, "kind": "baseline_proposal", "proposal_id": request["proposal_id"],
                "series_id": request["series_id"], "run_id": request["run_id"],
                "expected_generation": 0, "contract_generation": 1,
                "record": candidate["record"], "comparison_context": candidate["comparison_context"],
                "binding_digest": binding_digest,
                "created_at": now}
    raw, digest = _pack(proposal)
    existing = db.execute("SELECT * FROM baseline_proposals WHERE proposal_id=?", (request["proposal_id"],)).fetchone()
    if existing is not None:
        if existing["proposal_digest"] != digest:
            raise _error("PROPOSAL_CONFLICT")
    else:
        db.execute("INSERT INTO baseline_proposals VALUES(?,?,?,?,?,?,?,?,?,?)",
                   (request["proposal_id"], request["series_id"], request["run_id"], 0, 1, raw, digest, now, actor, context))
    return _result(request["action"], request["request_id"], proposal_id=request["proposal_id"], series_id=request["series_id"], generation=1, proposal_digest=digest)


def _execute_validate(store: Any, db: sqlite3.Connection, request: dict[str, Any], actor: str, context: str, now: int, resolve_source: Callable[..., Any]) -> dict[str, Any]:
    row = db.execute("SELECT * FROM baseline_proposals WHERE proposal_id=?", (request["proposal_id"],)).fetchone()
    proposal = _validate_proposal_row(row)
    if proposal["expected_generation"] != 0 and _current_generation(db, proposal["series_id"]) != proposal["expected_generation"]:
        raise _error("PREREQUISITE_UNAVAILABLE")
    source = _ready_source(_source_from_resolver(resolve_source, proposal["run_id"]), proposal["run_id"], now, store, purpose="regression" if proposal["expected_generation"] else "baseline_candidate")
    _bind_candidate(proposal, source, now, db=db)
    permission = _permission_generation(store, db)
    expires = min(MAX_INTEGER, now + _MAX_FRESHNESS, proposal["record"]["valid_until"])
    validation = {"schema_version": 1, "kind": "baseline_validation", "validation_id": request["validation_id"],
                  "proposal_id": proposal["proposal_id"], "proposal_digest": row["proposal_digest"],
                  "record_digest": _pack(proposal["record"])[1], "observed_at": now,
                  "expires_at": expires, "permission_generation": permission, "passed": True,
                  "ci_eligible": False}
    raw, digest = _pack(validation)
    existing = db.execute("SELECT * FROM baseline_validations WHERE validation_id=?", (request["validation_id"],)).fetchone()
    if existing is not None:
        if existing["validation_digest"] != digest:
            raise _error("VALIDATION_CONFLICT")
    else:
        db.execute("INSERT INTO baseline_validations VALUES(?,?,?,?,?,?,?,?,?,?)",
                   (request["validation_id"], proposal["proposal_id"], row["proposal_digest"], raw, digest, now, expires, permission, actor, context))
    return _result(request["action"], request["request_id"], proposal_id=proposal["proposal_id"], validation_id=request["validation_id"], validation_digest=digest, expires_at=expires, passed=True)


def _execute_adopt(store: Any, db: sqlite3.Connection, request: dict[str, Any], actor: str, context: str, now: int, resolve_source: Callable[..., Any]) -> dict[str, Any]:
    row = db.execute("SELECT * FROM baseline_proposals WHERE proposal_id=?", (request["proposal_id"],)).fetchone()
    proposal = _validate_proposal_row(row)
    if request["expected_generation"] != _current_generation(db, proposal["series_id"]):
        raise _error("GENERATION_CONFLICT")
    if row["actor_id"] != actor or row["context"] != context:
        raise _error("PROPOSER_MISMATCH")
    validation_row = _validation(db, request["validation_id"], row, proposal)
    _live_validation(store, db, validation_row, now)
    validation = _load(validation_row["validation_json"], validation_row["validation_digest"])
    source = _ready_source(_source_from_resolver(resolve_source, proposal["run_id"]), proposal["run_id"], now, store, purpose="regression" if proposal["expected_generation"] else "baseline_candidate")
    candidate = _bind_candidate(proposal, source, now, db=db)
    if candidate["record"] != proposal["record"] or validation["record_digest"] != _pack(proposal["record"])[1]:
        raise _error("VALIDATION_MISMATCH")
    if db.execute("SELECT 1 FROM baseline_revocations WHERE series_id=?", (proposal["series_id"],)).fetchone() is not None:
        raise _error("BASELINE_REVOKED")
    if proposal["kind"] == "baseline_refresh_proposal":
        from .baseline_generations import adopt
        return adopt(db, request, proposal, actor, context, now, validation)
    adoption_id = "adoption-" + proposal["proposal_id"]
    baseline_raw, baseline_digest = _pack(proposal["record"])
    existing = db.execute("SELECT * FROM baseline_adoptions WHERE adoption_id=?", (adoption_id,)).fetchone()
    current = db.execute("SELECT * FROM baseline_current WHERE series_id=?", (proposal["series_id"],)).fetchone()
    if existing is None:
        if current is not None:
            raise _error("STORAGE_CORRUPT")
        db.execute("INSERT INTO baseline_adoptions VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                   (adoption_id, proposal["series_id"], 1, proposal["proposal_id"], request["validation_id"], baseline_raw, baseline_digest, now, actor, context, validation["permission_generation"]))
        db.execute("INSERT INTO baseline_current VALUES(?,?,?,?,?)",
                   (proposal["series_id"], 1, adoption_id, baseline_raw, baseline_digest))
    else:
        if (current is None
                or _load(existing["baseline_json"], existing["baseline_digest"]) != proposal["record"]
                or _load(current["baseline_json"], current["baseline_digest"]) != proposal["record"]
                or existing["series_id"] != proposal["series_id"]
                or existing["generation"] != 1
                or existing["proposal_id"] != proposal["proposal_id"]
                or existing["validation_id"] != request["validation_id"]
                or existing["baseline_digest"] != baseline_digest
                or current["adoption_id"] != adoption_id
                or current["generation"] != 1
                or current["baseline_digest"] != baseline_digest):
            raise _error("STORAGE_CORRUPT")
    return _result(request["action"], request["request_id"], series_id=proposal["series_id"], generation=1, adoption_id=adoption_id, baseline_digest=baseline_digest, adoption_verified=True)


def _revoked(db, current):
    rows = list(db.execute("SELECT * FROM baseline_revocations WHERE series_id=?", (current["series_id"],)))
    for row in rows:
        adopted = db.execute("SELECT * FROM baseline_adoptions WHERE series_id=? AND generation=?",
            (current["series_id"], row["generation"])).fetchall()
        if (len(adopted) != 1 or type(row["generation"]) is not int or row["generation"] not in {1, 2}
                or type(row["observed_at"]) is not int or not adopted[0]["adopted_at"] <= row["observed_at"] <= MAX_INTEGER
                or row["actor_id"] != "operator" or row["context"] != "operator-context"):
            raise _error("STORAGE_CORRUPT")
        _history(db, adopted[0])
    return any(row["generation"] == current["generation"] for row in rows)


def _fresh(store: Any, db: sqlite3.Connection, request: dict[str, Any], actor: str, now: int, resolve_source: Callable[..., Any], use: bool, *, pinned=None) -> dict[str, Any]:
    row = pinned if pinned is not None else db.execute("SELECT * FROM baseline_current WHERE series_id=?", (request["series_id"],)).fetchone()
    if row is None:
        return _result(request["action"], request["request_id"], series_id=request["series_id"], adopted=False, use=False, valid=False, reason="NOT_FOUND")
    proposal, baseline, validation = _history(db, row)
    if use and (request["expected_baseline_ref"] != content_ref("baseline", baseline["baseline_id"], baseline)
                or request["expected_contract_ref"] != baseline.get("contract_ref")):
        raise _error("BINDING_MISMATCH")
    revoked = _revoked(db, row)
    valid = not revoked and now <= baseline["valid_until"]
    reason = None if valid else ("BASELINE_REVOKED" if revoked else "BASELINE_EXPIRED")
    if valid:
        try:
            _live_validation(store, db, validation, now)
            source = _ready_source(_source_from_resolver(resolve_source, baseline["source_run_ref"]["id"]), baseline["source_run_ref"]["id"], now, store,
                purpose="regression" if proposal["expected_generation"] else "baseline_candidate")
            candidate = _bind_candidate(proposal, source, now, db=db)
            if candidate["record"] != baseline:
                raise _error("SOURCE_MISMATCH")
        except AdoptionError as error:
            valid = False
            reason = error.code
    result = _result(request["action"], request["request_id"], series_id=request["series_id"], generation=row["generation"], adopted=True, baseline=baseline, valid=valid, use=bool(use and valid), reason=reason, adoption_verified=True)
    return result


def execute(store: Any, db: sqlite3.Connection, request: dict[str, Any], actor_id: str,
            context: str, now: int, resolve_source: Callable[..., Any]) -> dict[str, Any]:
    """既存transaction内でbaseline操作を実行する。transaction所有権は呼出側にある。"""
    if not isinstance(db, sqlite3.Connection) or not db.in_transaction:
        raise _error("TRANSACTION_REQUIRED")
    if (type(actor_id) is not str or actor_id not in _ROLES or type(context) is not str
            or context != actor_id + "-context"):
        raise _error("AUTHORITY_INVALID")
    request = validate_request(request)
    if actor_id not in ACTIONS[request["action"]]:
        raise _error("AUTHORITY_DENIED")
    _schema(db)
    _now_guard(now)
    if request["action"] == "baseline_propose":
        result = _execute_propose(store, db, request, actor_id, context, now, resolve_source)
    elif request["action"] == "baseline_validate":
        result = _execute_validate(store, db, request, actor_id, context, now, resolve_source)
    elif request["action"] == "baseline_adopt":
        result = _execute_adopt(store, db, request, actor_id, context, now, resolve_source)
    elif request["action"] == "baseline_current":
        result = _fresh(store, db, request, actor_id, now, resolve_source, False)
    elif request["action"] == "baseline_use":
        result = _fresh(store, db, request, actor_id, now, resolve_source, True)
    elif request["action"] == "baseline_resolve":
        from .baseline_generations import resolve
        result = resolve(store, db, request, actor_id, now, resolve_source)
    elif request["action"] == "baseline_revoke_ref":
        from .baseline_generations import revoke
        result = revoke(db, request, actor_id, context, now)
    else:
        row = db.execute("SELECT 1 FROM baseline_current WHERE series_id=?", (request["series_id"],)).fetchone()
        if row is None:
            raise _error("NOT_FOUND")
        current = db.execute("SELECT generation FROM baseline_current WHERE series_id=?", (request["series_id"],)).fetchone()
        if current is None or type(current[0]) is not int or not 1 <= current[0] <= MAX_INTEGER:
            raise _error("STORAGE_CORRUPT")
        generation = current[0]
        existing = db.execute("SELECT * FROM baseline_revocations WHERE series_id=? AND generation=?", (request["series_id"], generation)).fetchone()
        if existing is None:
            db.execute("INSERT INTO baseline_revocations VALUES(?,?,?,?,?)", (request["series_id"], generation, now, actor_id, context))
        elif (existing["observed_at"] is None or existing["actor_id"] != actor_id
              or existing["context"] != context):
            raise _error("STORAGE_CORRUPT")
        result = _result(request["action"], request["request_id"], series_id=request["series_id"], revoked=True, revocation_generation=generation)
    return result


__all__ = ["ACTIONS", "COLUMNS", "FIELDS", "FRESH_ACTIONS", "TABLES", "build_candidate", "create_schema", "execute", "validate_request"]
