"""契約移行候補の検証と、世代2への限定採択を行う内部境界。

このモジュールは候補runの保存実体を再検査するだけで、OS認証、Evidenceの
生成、資源の予約や現在利用の許可を行わない。呼出側が同じAdoptionStoreの
transactionと認証済み主体を渡す。返却値の ``ci_eligible`` は常にFalseである。
"""

from __future__ import annotations

from copy import deepcopy
import sqlite3
from typing import Any, Callable

from .adoption import AdoptionError, VALIDATION_TTL
from .read_checks import checked_read
from . import assurance_authority, resources, run_evidence, transition_authority
from .contracts import (ContractError, MAX_DOCUMENT_BYTES, MAX_INTEGER, require_digest,
                        require_id, require_object, require_ref, require_uint)
from .run_contracts import content_ref, validate_evaluation_contract


_BASE = {"schema_version", "action", "request_id"}
ACTIONS = {
    "contract_candidate_validate": {"validator"},
    "contract_candidate_adopt": {"manager"},
}
FIELDS = {
    "contract_candidate_validate": _BASE | {"candidate_id", "validation_id"},
    "contract_candidate_adopt": _BASE | {"candidate_id", "validation_id",
                                           "expected_contract_generation",
                                           "expected_baseline_generation"},
}
_VALIDATION_FIELDS = {
    "schema_version", "kind", "candidate_id", "candidate_ref", "proposal_id",
    "proposal_digest", "contract", "expected_contract_ref", "expected_baseline_ref",
    "old_receipt_ref", "new_receipt_ref", "checked_at", "permission_generation",
    "actor_id", "context",
}


def _error(code: str = "INVALID_REQUEST") -> AdoptionError:
    return AdoptionError(code)


def _id(value: Any) -> None:
    try:
        require_id(value)
    except ContractError:
        raise _error("INVALID_REQUEST") from None


def _ref(value: Any, kind: str) -> None:
    try:
        require_ref(value)
    except ContractError:
        raise _error("INVALID_REQUEST") from None
    if value["kind"] != kind:
        raise _error("REFERENCE_KIND")


def _uint(value: Any) -> None:
    try:
        require_uint(value)
    except ContractError:
        raise _error("INVALID_REQUEST") from None


def _pack(value: Any) -> tuple[str, str]:
    try:
        raw, digest = resources._packed(value)
    except (ContractError, resources.ResourceError, TypeError, ValueError, UnicodeError, RecursionError):
        raise _error("DOCUMENT_INVALID") from None
    if len(raw.encode("utf-8")) > MAX_DOCUMENT_BYTES:
        raise _error("DOCUMENT_SIZE")
    return raw, digest


def _load(row: sqlite3.Row, raw_name: str, digest_name: str) -> Any:
    try:
        return resources._unpack(row[raw_name], row[digest_name])
    except (resources.ResourceError, TypeError, ValueError, KeyError):
        raise _error("STORAGE_CORRUPT") from None


def validate_request(request: Any) -> dict[str, Any]:
    """候補検証/採択要求をexact schemaで検査し、入力のcopyを返す。"""
    try:
        action = request["action"] if type(request) is dict else None
        if action not in FIELDS:
            raise _error("INVALID_ACTION")
        require_object(request, FIELDS[action])
        if type(request["schema_version"]) is not int or request["schema_version"] != 1:
            raise _error("UNSUPPORTED_VERSION")
        _id(request["request_id"])
        _id(request["candidate_id"])
        _id(request["validation_id"])
        if action == "contract_candidate_adopt":
            _uint(request["expected_contract_generation"])
            _uint(request["expected_baseline_generation"])
    except AdoptionError:
        raise
    except (ContractError, KeyError, TypeError, ValueError):
        raise _error("INVALID_REQUEST") from None
    return deepcopy(request)


def _candidate_value(db: sqlite3.Connection, candidate_id: str, now: int) -> tuple[sqlite3.Row, dict[str, Any]]:
    """保存candidateのpayloadとDB列を最低限照合する。"""
    row, value = transition_authority.load_candidate(db, candidate_id, now)
    if (row["actor_id"] != "validator" or row["context"] != "validator-context"
            or type(row["created_at"]) is not int or not 0 <= row["created_at"] <= now <= MAX_INTEGER
            or type(row["permission_generation"]) is not int
            or not 0 <= row["permission_generation"] <= MAX_INTEGER):
        raise _error("CANDIDATE_INVALID")
    return row, value


def _candidate_parts(candidate: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    try:
        # DBに保存されるcandidate payload（dispatchの応答wrapperではない）を
        # 検査する。wrapperを受け入れると、保存本文とのref検査を迂回できる。
        require_object(candidate, {"candidate_id", "proposal_id",
                                   "proposal_digest", "baseline_series_id",
                                   "expected_contract_ref", "expected_baseline_ref", "runs"})
        for field in ("candidate_id", "proposal_id", "baseline_series_id"):
            _id(candidate[field])
        _ref(candidate["expected_contract_ref"], "evaluation_contract")
        _ref(candidate["expected_baseline_ref"], "baseline")
        try:
            require_digest(candidate["proposal_digest"])
        except ContractError:
            raise _error("CANDIDATE_INVALID") from None
        runs = candidate["runs"]
        require_object(runs, {"transition", "old", "new", "structurally_bound", "authority_connected", "ci_eligible"})
        if runs["structurally_bound"] is not True or runs["authority_connected"] is not False or runs["ci_eligible"] is not False:
            raise _error("CANDIDATE_INVALID")
        transition = runs["transition"]
        old = runs["old"]
        new = runs["new"]
        if type(transition) is not dict:
            raise _error("CANDIDATE_INVALID")
        previous = validate_evaluation_contract(transition["previous_contract"])
        following = validate_evaluation_contract(transition["next_contract"])
        if (candidate["expected_contract_ref"] != content_ref(
                    "evaluation_contract", previous["contract_id"], previous)
                or candidate["expected_baseline_ref"] != transition["baseline_ref"]):
            raise _error("CANDIDATE_INVALID")
        _ref(transition["baseline_ref"], "baseline")
        _ref(transition["source_run_ref"], "run_manifest")
        baseline = transition["baseline_record"]
        if (type(baseline) is not dict or type(baseline.get("generation")) is not int
                or not 1 <= baseline["generation"] <= MAX_INTEGER
                or following["generation"] != previous["generation"] + 1
                or (previous["generation"] == 1 and baseline["generation"] != 1)
                or (previous["generation"] >= 2 and baseline["generation"] < 2)
                or content_ref("baseline", baseline.get("baseline_id"), baseline)
                   != transition["baseline_ref"]):
            raise _error("CANDIDATE_INVALID")
        if transition["source_run_ref"]["id"] in {
                old.get("bound_run", {}).get("manifest", {}).get("run_id"),
                new.get("bound_run", {}).get("manifest", {}).get("run_id")}:
            raise _error("CANDIDATE_INVALID")
        for item in (old, new):
            if type(item) is not dict or type(item.get("bound_run")) is not dict:
                raise _error("CANDIDATE_INVALID")
        if (old["bound_run"]["contract"] != previous
                or new["bound_run"]["contract"] != following):
            raise _error("CANDIDATE_INVALID")
        return transition, old, new
    except (ContractError, KeyError, TypeError):
        raise _error("CANDIDATE_INVALID") from None


def _receipt_ref(source: dict[str, Any], run_id: str) -> dict[str, str]:
    receipt = source.get("receipt")
    if type(receipt) is not dict or receipt.get("kind") != "authority_run_receipt" or receipt.get("run_id") != run_id:
        raise _error("SOURCE_INVALID")
    return content_ref("authority_run_receipt", run_id, receipt)


def _check_source(db: sqlite3.Connection, source: Any, bound: dict[str, Any], now: int,
                  *, allow_unhealthy: bool = False) -> dict[str, str]:
    """general baseline_sourceの返却を、候補bundle/全operationと再照合する。"""
    if type(source) is not dict:
        raise _error("SOURCE_INVALID")
    if source.get("bound") != bound:
        raise _error("SOURCE_BINDING_MISMATCH")
    manifest = bound.get("manifest")
    if type(manifest) is not dict:
        raise _error("SOURCE_INVALID")
    run_id = manifest.get("run_id")
    _id(run_id)
    receipt = source.get("receipt")
    if type(receipt) is not dict or receipt.get("authority_connected") is not True \
            or receipt.get("resource_closure_verified") is not True \
            or receipt.get("input_materialization_verified") is not True \
            or receipt.get("adoption_verified") is not False \
            or receipt.get("ci_eligible") is not False \
            or receipt.get("run_id") != run_id \
            or receipt.get("kind") != "authority_run_receipt":
        raise _error("SOURCE_INVALID")
    reasons = source.get("reasons")
    if type(reasons) is not list or any(type(reason) is not str for reason in reasons):
        raise _error("SOURCE_INVALID")
    decision = source.get("decision")
    states = {"HEALTHY", "WARNING", "DEGRADED", "UNKNOWN", "HOLD"} if allow_unhealthy else {"HEALTHY", "WARNING"}
    if type(decision) is not dict or decision.get("assurance") not in states:
        raise _error("SOURCE_INVALID")
    closure = source.get("closure")
    if type(closure) is not dict or closure.get("budget_closure") is not True or closure.get("run_id") != run_id:
        raise _error("SOURCE_INVALID")
    evidences = source.get("evidences")
    states = source.get("evidence_states")
    if type(evidences) is not list or len(evidences) != 1 or type(states) is not dict:
        raise _error("SOURCE_INVALID")
    evidence = evidences[0]
    if type(evidence) is not dict or evidence.get("kind") != "evidence":
        raise _error("SOURCE_INVALID")
    state = states.get(evidence.get("evidence_id"))
    if (type(state) is not dict or type(state.get("revoked")) is not bool
            or type(state.get("deleted")) is not bool):
        raise _error("SOURCE_INVALID")
    for key in ("observed_at", "valid_until", "retention_until"):
        _uint(evidence.get(key))
    if (evidence["observed_at"] > now
            or not evidence["observed_at"] <= evidence["valid_until"]
            or not evidence["observed_at"] <= evidence["retention_until"]):
        raise _error("SOURCE_EXPIRED")
    if evidence.get("subject_ref") != content_ref("run_manifest", run_id, manifest):
        raise _error("SOURCE_BINDING_MISMATCH")
    _ref(evidence.get("conditions_ref"), "bound_bundle")
    _ref(evidence.get("decision_ref"), "run_decision")
    _ref(evidence.get("closure_ref"), "resource_closure")
    if (evidence["conditions_ref"] != content_ref("bound_bundle", run_id, bound)
            or evidence["decision_ref"] != content_ref("run_decision", run_id, decision)
            or evidence["closure_ref"] != content_ref("resource_closure", run_id, closure)
            or closure.get("manifest_digest") != content_ref("run_manifest", run_id, manifest)["digest"]):
        raise _error("SOURCE_BINDING_MISMATCH")
    try:
        snapshot = resources.ResourceBook(db).snapshot(run_id, now)
    except (resources.ResourceError, KeyError, TypeError, ValueError):
        raise _error("SOURCE_OPERATION_INVALID") from None
    # 全runの24時間予算は別runの予約でも変化する。過去のclosureの
    # 不変性はreceipt側で検査し、ここではこのrun固有の精算額だけを比べる。
    per_run = lambda values: {k: v for k, v in values.items() if k != "global_api_cost_usd_micros"}
    if (snapshot.get("closed") is not True or snapshot.get("budget_closure") is not True
            or snapshot.get("cancelled") is True
            or per_run(snapshot["resources"]) != per_run(closure["resources"])):
        raise _error("SOURCE_OPERATION_INVALID")
    for attempt_row in db.execute("SELECT * FROM attempts WHERE run_id=? ORDER BY attempt_id", (run_id,)):
        try:
            attempt = resources._unpack(attempt_row["attempt_json"], attempt_row["attempt_digest"])
            assurance_authority._operation(db, attempt, now)
        except (AdoptionError, resources.ResourceError, run_evidence.EvidenceError, ContractError, KeyError, TypeError, ValueError):
            raise _error("SOURCE_OPERATION_INVALID") from None
    # 完了した否定判定と、保存・実行の障害を分けるため、実体照合を先に終える。
    if now > min(evidence["valid_until"], evidence["retention_until"]):
        raise _error("SOURCE_EXPIRED")
    if reasons or state["revoked"] or state["deleted"]:
        raise _error("SOURCE_NOT_READY")
    return _receipt_ref(source, run_id)


def _proposal(db: sqlite3.Connection, proposal_id: str) -> tuple[sqlite3.Row, dict[str, Any]]:
    row = db.execute("SELECT * FROM eval_proposals WHERE id=?", (proposal_id,)).fetchone()
    if row is None:
        raise _error("PROPOSAL_MISSING")
    payload = _load(row, "payload_json", "digest")
    try:
        contract = validate_evaluation_contract(payload)
    except (ContractError, KeyError, TypeError, ValueError, RecursionError):
        raise _error("STORAGE_CORRUPT") from None
    if row["digest"] != content_ref("evaluation_contract", contract["contract_id"], contract)["digest"] \
            or row["generation"] != contract["generation"]:
        raise _error("STORAGE_CORRUPT")
    return row, contract


def _build_validation(candidate_row: sqlite3.Row, candidate: dict[str, Any], old_receipt: dict[str, str],
                      new_receipt: dict[str, str], now: int, permission: int) -> dict[str, Any]:
    transition, old, new = _candidate_parts(candidate)
    contract = validate_evaluation_contract(transition["next_contract"])
    old_contract = validate_evaluation_contract(transition["previous_contract"])
    candidate_ref = content_ref("contract_candidate", candidate["candidate_id"], candidate)
    baseline_ref = transition.get("baseline_ref")
    expected_old_ref = content_ref("evaluation_contract", old_contract["contract_id"], old_contract)
    _ref(baseline_ref, "baseline")
    value = {
        "schema_version": 1, "kind": "contract_transition_validation",
        "candidate_id": candidate["candidate_id"], "candidate_ref": candidate_ref,
        "proposal_id": candidate["proposal_id"], "proposal_digest": candidate["proposal_digest"],
        "contract": deepcopy(contract), "expected_contract_ref": expected_old_ref,
        "expected_baseline_ref": deepcopy(baseline_ref), "old_receipt_ref": old_receipt,
        "new_receipt_ref": new_receipt, "checked_at": now,
        "permission_generation": permission, "actor_id": "validator", "context": "validator-context",
    }
    require_object(value, _VALIDATION_FIELDS)
    return value


def _validate_payload(payload: Any) -> dict[str, Any]:
    try:
        require_object(payload, _VALIDATION_FIELDS)
        if (type(payload["schema_version"]) is not int or payload["schema_version"] != 1
                or payload["kind"] != "contract_transition_validation"):
            raise _error("STORAGE_CORRUPT")
        _id(payload["candidate_id"]); _id(payload["proposal_id"])
        _ref(payload["candidate_ref"], "contract_candidate")
        _ref(payload["expected_contract_ref"], "evaluation_contract")
        _ref(payload["expected_baseline_ref"], "baseline")
        _ref(payload["old_receipt_ref"], "authority_run_receipt")
        _ref(payload["new_receipt_ref"], "authority_run_receipt")
        validate_evaluation_contract(payload["contract"])
        for key in ("checked_at", "permission_generation"):
            _uint(payload[key])
        if payload["actor_id"] != "validator" or payload["context"] != "validator-context":
            raise _error("STORAGE_CORRUPT")
    except AdoptionError:
        raise
    except (ContractError, KeyError, TypeError, ValueError, RecursionError):
        raise _error("STORAGE_CORRUPT") from None
    return deepcopy(payload)


def _validation_row(db: sqlite3.Connection, validation_id: str) -> tuple[sqlite3.Row, dict[str, Any]]:
    row = db.execute("SELECT * FROM eval_validations WHERE id=?", (validation_id,)).fetchone()
    if row is None:
        raise _error("VALIDATION_MISSING")
    payload = _validate_payload(_load(row, "payload_json", "digest"))
    raw, digest = _pack(payload)
    if (row["digest"] != digest or row["payload_json"] != raw
            or row["proposal_id"] != payload["proposal_id"]
            or row["proposal_digest"] != payload["proposal_digest"]
            or row["created_at"] != payload["checked_at"]
            or row["permission_generation"] != payload["permission_generation"]
            or type(row["expires_at"]) is not int
            or not row["created_at"] < row["expires_at"] <= MAX_INTEGER
            or row["expires_at"] > min(MAX_INTEGER, row["created_at"] + VALIDATION_TTL)):
        raise _error("STORAGE_CORRUPT")
    return row, payload


def _stored_receipt_ref(db: sqlite3.Connection, ref: dict[str, str]) -> None:
    """履歴読取時にもreceipt参照と保存本文を結ぶ（現在状態は検査しない）。"""
    _ref(ref, "authority_run_receipt")
    row = db.execute("SELECT * FROM authority_run_receipts WHERE run_id=?", (ref["id"],)).fetchone()
    if row is None:
        raise _error("STORAGE_CORRUPT")
    try:
        receipt = resources._unpack(row["payload_json"], row["digest"])
    except (resources.ResourceError, TypeError, ValueError, KeyError):
        raise _error("STORAGE_CORRUPT") from None
    try:
        expected = content_ref("authority_run_receipt", ref["id"], receipt)
    except (ContractError, TypeError, ValueError, KeyError):
        raise _error("STORAGE_CORRUPT") from None
    if expected != ref or receipt.get("kind") != "authority_run_receipt" or receipt.get("run_id") != ref["id"]:
        raise _error("STORAGE_CORRUPT")


def _source_ids(candidate: dict[str, Any]) -> tuple[str, str]:
    try:
        _, old, new = _candidate_parts(candidate)
        old_id = old["bound_run"]["manifest"]["run_id"]
        new_id = new["bound_run"]["manifest"]["run_id"]
        _id(old_id); _id(new_id)
        if old_id == new_id:
            raise _error("CANDIDATE_INVALID")
        return old_id, new_id
    except (KeyError, TypeError):
        raise _error("CANDIDATE_INVALID") from None


def _live_sources(db: sqlite3.Connection, candidate: dict[str, Any], now: int,
                  resolve_source: Callable[[str], Any]) -> tuple[dict[str, str], dict[str, str]]:
    _, old, new = _candidate_parts(candidate)
    old_id, new_id = _source_ids(candidate)
    if not callable(resolve_source):
        raise _error("SOURCE_UNAVAILABLE")
    try:
        old_source = resolve_source(old_id)
        new_source = resolve_source(new_id)
    except AdoptionError:
        raise
    except Exception:
        raise _error("SOURCE_UNAVAILABLE") from None
    return (_check_source(db, old_source, old["bound_run"], now),
            _check_source(db, new_source, new["bound_run"], now))


def validate_live_proof(store: Any, db: sqlite3.Connection, validation_payload: dict[str, Any],
                        now: int, resolve_source: Callable[[str], Any]) -> None:
    """保存validationを現在時刻で再検査する。採択権限やEvidence生成は持たない。"""
    payload = _validate_payload(validation_payload)
    _uint(now)
    if not payload["checked_at"] <= now:
        raise _error("VALIDATION_TIME_INVALID")
    if payload["permission_generation"] != store._permission_generation(db):
        raise _error("VALIDATION_EXPIRED")
    if store._actor_revoked(db, "validator") or store._actor_revoked(db, "manager"):
        raise _error("VALIDATION_EXPIRED")
    row, candidate = _candidate_value(db, payload["candidate_id"], now)
    if (row["permission_generation"] != payload["permission_generation"]
            or content_ref("contract_candidate", payload["candidate_id"], candidate) != payload["candidate_ref"]
            or candidate["proposal_id"] != payload["proposal_id"]
            or candidate["proposal_digest"] != payload["proposal_digest"]):
        raise _error("CANDIDATE_INVALID")
    transition, _, _ = _candidate_parts(candidate)
    if validate_evaluation_contract(transition["next_contract"]) != payload["contract"]:
        raise _error("CONTRACT_INVALID")
    proposal_row, proposal_contract = _proposal(db, payload["proposal_id"])
    if proposal_row["digest"] != payload["proposal_digest"] or proposal_contract != payload["contract"] \
            or proposal_row["actor_id"] != "manager" or proposal_row["context"] != "manager-context":
        raise _error("PROPOSAL_MISMATCH")
    old_receipt, new_receipt = _live_sources(db, candidate, now, resolve_source)
    if old_receipt != payload["old_receipt_ref"] or new_receipt != payload["new_receipt_ref"]:
        raise _error("SOURCE_MISMATCH")


@checked_read
def history(db: sqlite3.Connection, current: sqlite3.Row, contract: dict[str, Any]) -> sqlite3.Row:
    """gen2 currentと専用validation/historyの不変結合を返す。"""
    try:
        contract = validate_evaluation_contract(contract)
        if current is None or current["generation"] != contract["generation"] or contract["generation"] < 2:
            raise _error("STORAGE_CORRUPT")
        current_contract = _load(current, "payload_json", "digest")
        if current_contract != contract:
            raise _error("STORAGE_CORRUPT")
        validation_row, payload = _validation_row(db, current["validation_id"])
        if (current["proposal_id"] != validation_row["proposal_id"]
                or current["digest"] != content_ref("evaluation_contract", contract["contract_id"], contract)["digest"]
                or payload["contract"] != contract):
            raise _error("STORAGE_CORRUPT")
        proposal_row, proposal_contract = _proposal(db, current["proposal_id"])
        if (proposal_row["series_id"] != current["series_id"] or proposal_row["actor_id"] != "manager"
                or proposal_row["context"] != "manager-context" or proposal_contract != contract
                or proposal_row["digest"] != payload["proposal_digest"]):
            raise _error("STORAGE_CORRUPT")
        adopted = db.execute("SELECT * FROM eval_adoptions WHERE series_id=? AND generation=?",
                             (current["series_id"], current["generation"])).fetchone()
        if adopted is None or any(adopted[key] != current[key] for key in
                                  ("series_id", "generation", "proposal_id", "validation_id", "payload_json", "digest")):
            raise _error("STORAGE_CORRUPT")
        candidate_row, candidate = _candidate_value(db, payload["candidate_id"], payload["checked_at"])
        transition, old, new = _candidate_parts(candidate)
        if (content_ref("contract_candidate", payload["candidate_id"], candidate) != payload["candidate_ref"]
                or candidate_row["permission_generation"] != payload["permission_generation"]
                or candidate["proposal_id"] != payload["proposal_id"]
                or candidate["proposal_digest"] != payload["proposal_digest"]
                or candidate["expected_contract_ref"] != payload["expected_contract_ref"]
                or candidate["expected_baseline_ref"] != payload["expected_baseline_ref"]
                or transition["next_contract"] != payload["contract"]):
            raise _error("STORAGE_CORRUPT")
        old_id, new_id = _source_ids(candidate)
        if (payload["old_receipt_ref"]["id"] != old_id or payload["new_receipt_ref"]["id"] != new_id):
            raise _error("STORAGE_CORRUPT")
        _stored_receipt_ref(db, payload["old_receipt_ref"])
        _stored_receipt_ref(db, payload["new_receipt_ref"])
        return validation_row
    except AdoptionError:
        raise
    except (ContractError, KeyError, TypeError, ValueError, RecursionError):
        raise _error("STORAGE_CORRUPT") from None


def _insert_validation(db: sqlite3.Connection, request: dict[str, Any], payload: dict[str, Any],
                       proposal_digest: str, now: int, permission: int) -> tuple[str, int]:
    raw, digest = _pack(payload)
    expires = min(MAX_INTEGER, now + VALIDATION_TTL)
    existing = db.execute("SELECT * FROM eval_validations WHERE id=?", (request["validation_id"],)).fetchone()
    if existing is not None:
        if (existing["digest"] != digest or existing["payload_json"] != raw
                or existing["proposal_id"] != payload["proposal_id"]
                or existing["proposal_digest"] != proposal_digest
                or existing["created_at"] != now or existing["permission_generation"] != permission):
            raise _error("VALIDATION_CONFLICT")
        _validation_row(db, request["validation_id"])
        return digest, existing["expires_at"]
    db.execute("INSERT INTO eval_validations VALUES(?,?,?,?,?,?,?,?)",
               (request["validation_id"], payload["proposal_id"], proposal_digest, raw, digest,
                now, expires, permission))
    return digest, expires


def execute(store: Any, db: sqlite3.Connection, request: dict[str, Any], actor_id: str,
            context: str, now: int, check_candidate: Callable[[str], Any],
            resolve_source: Callable[[str], Any]) -> dict[str, Any]:
    """既存AdoptionStore transaction内で候補検証/gen2採択を行う。"""
    if not isinstance(db, sqlite3.Connection) or not db.in_transaction:
        raise _error("TRANSACTION_REQUIRED")
    request = validate_request(request)
    action = request["action"]
    if actor_id not in ACTIONS[action] or context != actor_id + "-context":
        raise _error("AUTHORITY_DENIED")
    _uint(now)
    if not callable(check_candidate):
        raise _error("CANDIDATE_UNAVAILABLE")
    try:
        fresh = check_candidate(request["candidate_id"])
    except AdoptionError:
        raise
    except Exception:
        raise _error("CANDIDATE_UNAVAILABLE") from None
    if type(fresh) is not tuple or len(fresh) != 2:
        raise _error("CANDIDATE_UNAVAILABLE")
    candidate_row, candidate = fresh
    if type(candidate_row) is not sqlite3.Row and not hasattr(candidate_row, "keys"):
        raise _error("CANDIDATE_UNAVAILABLE")
    if type(candidate) is not dict or candidate.get("candidate_id") != request["candidate_id"]:
        raise _error("CANDIDATE_INVALID")
    transition, old, new = _candidate_parts(candidate)
    old_receipt, new_receipt = _live_sources(db, candidate, now, resolve_source)
    permission = store._permission_generation(db)
    payload = _build_validation(candidate_row, candidate, old_receipt, new_receipt, now, permission)
    proposal_row, contract = _proposal(db, payload["proposal_id"])
    if proposal_row["actor_id"] != "manager" or proposal_row["context"] != "manager-context" \
            or proposal_row["digest"] != payload["proposal_digest"] or contract != payload["contract"]:
        raise _error("PROPOSAL_MISMATCH")
    if action == "contract_candidate_validate":
        digest, expires = _insert_validation(db, request, payload, proposal_row["digest"], now, permission)
        return {"validation_id": request["validation_id"], "candidate_id": request["candidate_id"],
                "validation_digest": digest, "expires_at": expires, "permission_generation": permission,
                "passed": True, "adoption_verified": False}

    old_generation = transition['previous_contract']['generation']
    new_generation = transition['next_contract']['generation']
    if (request['expected_contract_generation'] != old_generation
            or request['expected_baseline_generation'] != transition['baseline_record']['generation']):
        raise _error("GENERATION_CONFLICT")
    validation_row, saved_payload = _validation_row(db, request["validation_id"])
    validate_live_proof(store, db, saved_payload, now, resolve_source)
    # 時刻だけは検証実行時刻として変化し得るが、候補・proposal・receipt・
    # 権限世代など証明の意味部分は同一でなければならない。
    comparable_saved = deepcopy(saved_payload)
    comparable_now = deepcopy(payload)
    comparable_saved.pop("checked_at", None)
    comparable_now.pop("checked_at", None)
    if comparable_saved != comparable_now:
        raise _error("VALIDATION_MISMATCH")
    if not validation_row["created_at"] <= now < validation_row["expires_at"]:
        raise _error("VALIDATION_EXPIRED")
    if candidate_row["context"] != "validator-context":
        raise _error("CANDIDATE_INVALID")
    current = db.execute("SELECT * FROM eval_current WHERE series_id=?", (proposal_row["series_id"],)).fetchone()
    if current is None or current["generation"] != old_generation:
        raise _error("GENERATION_CONFLICT")
    old_contract = validate_evaluation_contract(transition["previous_contract"])
    old_raw, old_digest = _pack(old_contract)
    if _load(current, "payload_json", "digest") != old_contract or current["digest"] != old_digest:
        raise _error("CURRENT_MISMATCH")
    old_history = db.execute("SELECT * FROM eval_adoptions WHERE series_id=? AND generation=?",
                             (proposal_row["series_id"], old_generation)).fetchone()
    if old_history is None or old_history["digest"] != old_digest or _load(old_history, "payload_json", "digest") != old_contract:
        raise _error("HISTORY_MISMATCH")
    new_raw, new_digest = _pack(contract)
    updated = db.execute("UPDATE eval_current SET generation=?,proposal_id=?,validation_id=?,payload_json=?,digest=? "
                         "WHERE series_id=? AND generation=? AND digest=?",
                         (new_generation, proposal_row["id"], request["validation_id"], new_raw, new_digest,
                          proposal_row["series_id"], old_generation, old_digest))
    if updated.rowcount != 1:
        raise _error("GENERATION_CONFLICT")
    db.execute("INSERT INTO eval_adoptions VALUES(?,?,?,?,?,?)",
               (proposal_row["series_id"], new_generation, proposal_row["id"], request["validation_id"], new_raw, new_digest))
    current2 = db.execute("SELECT * FROM eval_current WHERE series_id=?", (proposal_row["series_id"],)).fetchone()
    history(db, current2, contract)
    return {"candidate_id": request["candidate_id"], "series_id": proposal_row["series_id"],
            "generation": new_generation, "proposal_id": proposal_row["id"], "validation_id": request["validation_id"],
            "contract_digest": content_ref("evaluation_contract", contract["contract_id"], contract)["digest"],
            "adoption_verified": True}


__all__ = ["ACTIONS", "FIELDS", "VALIDATION_TTL", "execute", "history", "validate_live_proof", "validate_request"]
