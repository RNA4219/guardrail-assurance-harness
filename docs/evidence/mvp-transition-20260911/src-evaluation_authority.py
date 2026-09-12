"""評価契約の保存・検証・採択と実行開始を管理DBへ接続する拡張。

この拡張は外部I/Oや実行そのものを行わず、AdoptionStore が開始した
transaction の中で、保存済みartifact、方針採択、校正、有効期限、資源予約を
一貫して照合する。校正・集合・実モデルの実運用上の認証はこの境界の保証外で
あり、返却値の ``ci_eligible`` は常に偽である。
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any

from . import corpus, registry, resources, resource_authority, run_evidence, assurance_authority, baseline_authority, fixture_admission, contract_updates
from .adoption import AdoptionError
from .contracts import ContractError, MAX_DOCUMENT_BYTES, MAX_INTEGER, require_id, require_ref, require_uint
from .policy import validate_policy_profile
from .run_contracts import (
    bind_evaluation_contract,
    bind_run_manifest,
    bind_trial_plan,
    content_ref,
    validate_evaluation_contract,
    validate_run_manifest,
    validate_trial_plan,
)
from .wire import canonical_bytes


VALIDATION_TTL = 86400
_SCHEMA_VERSION = 1
_KIND = "evaluation_authority_result"
_ROLES = {"manager", "validator", "operator"}
_ACTIONS = {
    "object_register", "calibration_record", "contract_propose",
    "contract_validate", "contract_adopt", "contract_current",
    "run_begin", "run_status", "fixture_prepare", "contract_preflight",
}
_ACTION_ROLES = {
    "object_register": {"manager"},
    "calibration_record": {"validator"},
    "contract_propose": {"manager"},
    "contract_validate": {"validator"},
    "contract_adopt": {"manager"},
    "contract_current": _ROLES,
    "run_begin": {"operator"},
    "run_status": _ROLES,
    "fixture_prepare": {"manager"},
    "contract_preflight": {"validator"},
}
_FRESH_ACTIONS = {"contract_current", "run_status", "contract_preflight"}
_ACTION_FIELDS = {
    "object_register": {"schema_version", "action", "request_id", "document"},
    "calibration_record": {"schema_version", "action", "request_id", "calibration_id", "case_set_ref", "evaluator_ref", "observations"},
    "contract_propose": {"schema_version", "action", "request_id", "proposal_id", "series_id", "expected_generation", "contract"},
    "contract_validate": {"schema_version", "action", "request_id", "proposal_id", "validation_id"},
    "contract_adopt": {"schema_version", "action", "request_id", "proposal_id", "validation_id", "expected_generation"},
    "contract_current": {"schema_version", "action", "request_id", "series_id"},
    "run_begin": {"schema_version", "action", "request_id", "manifest", "plan", "contract_series_id"},
    "run_status": {"schema_version", "action", "request_id", "run_id"},
    "fixture_prepare": {"schema_version", "action", "request_id", "run_id", "policy_series_id"},
    "contract_preflight": {"schema_version", "action", "request_id", "proposal_id", "baseline_series_id", "expected_contract_ref", "expected_baseline_ref"},
}

_EVAL_TABLES = {
    "eval_objects": {"kind", "id", "digest", "payload_json"},
    "eval_calibrations": {"id", "payload_json", "digest", "created_at", "expires_at", "permission_generation"},
    "eval_proposals": {"id", "series_id", "generation", "payload_json", "digest", "actor_id", "context"},
    "eval_validations": {"id", "proposal_id", "proposal_digest", "payload_json", "digest", "created_at", "expires_at", "permission_generation"},
    "eval_current": {"series_id", "generation", "proposal_id", "validation_id", "payload_json", "digest"},
    "eval_runs": {"run_id", "manifest_json", "manifest_digest", "plan_json", "plan_digest", "contract_series_id", "contract_generation"},
}
_HISTORY_TABLES = {
    "eval_adoptions": _EVAL_TABLES["eval_current"],
    "policy_adoptions": {"series_id", "generation", "proposal_id", "validation_id", "policy_json", "policy_digest", "adopted_at", "actor_id", "context"},
}
TABLES = {**resources.TABLES, **resource_authority.TABLES, **_EVAL_TABLES,
          **_HISTORY_TABLES, **run_evidence.COLUMNS, **assurance_authority.TABLES,
          **baseline_authority.TABLES, **fixture_admission.TABLES}


def _error(code: str = "INVALID_REQUEST") -> AdoptionError:
    return AdoptionError(code)


def _invalid() -> AdoptionError:
    return _error("INVALID_REQUEST")


def _canonical(value: Any) -> bytes:
    try:
        raw = canonical_bytes(value)
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise _invalid() from None
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise _error("DOCUMENT_SIZE")
    return raw


def _packed(value: Any) -> tuple[str, str]:
    raw = _canonical(value)
    return raw.decode("utf-8"), hashlib.sha256(raw).hexdigest()


def _validate_ref(value: Any, kind: str) -> dict[str, str]:
    try:
        require_ref(value)
    except ContractError:
        raise _invalid() from None
    if value["kind"] != kind:
        raise _error("REFERENCE_KIND")
    return copy.deepcopy(value)


def _validate_request(request: Any) -> dict[str, Any]:
    if type(request) is not dict:
        raise _invalid()
    action = request.get("action")
    if type(action) is not str or action not in (set(_ACTION_FIELDS) | set(resource_authority.FIELDS) | set(assurance_authority.FIELDS) | set(baseline_authority.FIELDS)):
        raise _error("INVALID_ACTION")
    if action in assurance_authority.FIELDS:
        return assurance_authority.validate_request(request)
    if action in baseline_authority.FIELDS:
        return baseline_authority.validate_request(request)
    if action in resource_authority.FIELDS:
        try:
            return resource_authority.validate_request(request)
        except AdoptionError:
            raise
        except resource_authority.ResourceError as error:
            raise _error(getattr(error, "code", "INVALID_REQUEST")) from None
        except ContractError:
            raise _invalid() from None
    if set(request) != _ACTION_FIELDS[action]:
        raise _invalid()
    if type(request.get("schema_version")) is not int or request["schema_version"] != _SCHEMA_VERSION:
        raise _error("UNSUPPORTED_VERSION")
    try:
        require_id(request["request_id"])
    except ContractError:
        raise _error("INVALID_REQUEST_ID") from None
    normalized = copy.deepcopy(request)
    try:
        if action == "object_register":
            if type(normalized["document"]) is not dict:
                raise _invalid()
        elif action == "calibration_record":
            _validate_ref(normalized["case_set_ref"], "case_set")
            _validate_ref(normalized["evaluator_ref"], "evaluator")
            require_id(normalized["calibration_id"])
            if type(normalized["observations"]) is not list:
                raise _invalid()
        elif action == "contract_propose":
            require_id(normalized["proposal_id"])
            require_id(normalized["series_id"])
            require_uint(normalized["expected_generation"])
            normalized["contract"] = validate_evaluation_contract(normalized["contract"])
        elif action == "contract_validate":
            require_id(normalized["proposal_id"])
            require_id(normalized["validation_id"])
        elif action == "contract_preflight":
            require_id(normalized["proposal_id"])
            require_id(normalized["baseline_series_id"])
            _validate_ref(normalized["expected_contract_ref"], "evaluation_contract")
            _validate_ref(normalized["expected_baseline_ref"], "baseline")
        elif action == "contract_adopt":
            require_id(normalized["proposal_id"])
            require_id(normalized["validation_id"])
            require_uint(normalized["expected_generation"])
        elif action == "contract_current":
            require_id(normalized["series_id"])
        elif action == "run_begin":
            normalized["manifest"] = validate_run_manifest(normalized["manifest"])
            normalized["plan"] = validate_trial_plan(normalized["plan"])
            require_id(normalized["contract_series_id"])
        elif action == "run_status":
            require_id(normalized["run_id"])
        elif action == "fixture_prepare":
            require_id(normalized["run_id"])
            require_id(normalized["policy_series_id"])
    except (ContractError, TypeError, ValueError, KeyError, RecursionError):
        raise _invalid() from None
    return normalized


def _source_digest() -> str:
    """拡張の動作を決めるソース群から固定digestを作る。"""
    digest = hashlib.sha256()
    paths = (
        Path(__file__),
        Path(__file__).with_name("run_contracts.py"),
        Path(__file__).with_name("resources.py"),
        Path(__file__).with_name("resource_authority.py"),
        Path(__file__).with_name("ledger.py"),
        Path(__file__).with_name("registry.py"),
        Path(__file__).with_name("corpus.py"),
        *(Path(__file__).with_name(name + ".py") for name in (
            "adoption", "wire", "contracts", "policy", "run_evidence", "aggregation",
            "decision", "normalized", "docker_runner", "execution_journal", "assurance_authority",
            "adoption_migrations", "baselines", "baseline_authority", "fixture_admission",
            "fixture_materialization", "fixture_calibration", "contract_updates")),
        Path(__file__).resolve().parents[2] / "fixtures" / "runtime" / "fixture_worker.py",
        Path(__file__).resolve().parents[2] / "config" / "fixture-runtime.lock.json",
    )
    try:
        for path in paths:
            data = path.read_bytes()
            digest.update(len(data).to_bytes(8, "big"))
            digest.update(data)
    except OSError:
        # AdoptionStoreが設定digestを保存する前に失敗させるため、呼出側に
        # 可変の例外内容を渡さない。
        raise _error("EXTENSION_INVALID") from None
    return digest.hexdigest()


def create_schema(db: sqlite3.Connection) -> None:
    """評価テーブルと資源台帳を同じtransactionへ作成する。"""
    if not db.in_transaction:
        raise _error("TRANSACTION_REQUIRED")
    db.execute("CREATE TABLE eval_objects(kind TEXT NOT NULL, id TEXT NOT NULL, digest TEXT NOT NULL, payload_json TEXT NOT NULL, PRIMARY KEY(kind,id))")
    db.execute("CREATE TABLE eval_calibrations(id TEXT PRIMARY KEY, payload_json TEXT NOT NULL, digest TEXT NOT NULL, created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, permission_generation INTEGER NOT NULL)")
    db.execute("CREATE TABLE eval_proposals(id TEXT PRIMARY KEY, series_id TEXT NOT NULL, generation INTEGER NOT NULL, payload_json TEXT NOT NULL, digest TEXT NOT NULL, actor_id TEXT NOT NULL, context TEXT NOT NULL)")
    db.execute("CREATE TABLE eval_validations(id TEXT PRIMARY KEY, proposal_id TEXT NOT NULL, proposal_digest TEXT NOT NULL, payload_json TEXT NOT NULL, digest TEXT NOT NULL, created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, permission_generation INTEGER NOT NULL)")
    db.execute("CREATE TABLE eval_current(series_id TEXT PRIMARY KEY, generation INTEGER NOT NULL, proposal_id TEXT NOT NULL, validation_id TEXT NOT NULL, payload_json TEXT NOT NULL, digest TEXT NOT NULL)")
    db.execute("CREATE TABLE eval_runs(run_id TEXT PRIMARY KEY, manifest_json TEXT NOT NULL, manifest_digest TEXT NOT NULL, plan_json TEXT NOT NULL, plan_digest TEXT NOT NULL, contract_series_id TEXT NOT NULL, contract_generation INTEGER NOT NULL)")
    resources.create_schema(db)
    resource_authority.create_schema(db)
    migrate_schema(db)


def migrate_schema(db: sqlite3.Connection) -> None:
    """既知v2の同transactionへ新表を追加し、現行採択の内容だけを履歴へ残す。"""
    if not db.in_transaction:
        raise _error("TRANSACTION_REQUIRED")
    db.execute("CREATE TABLE eval_adoptions(series_id TEXT NOT NULL, generation INTEGER NOT NULL, proposal_id TEXT NOT NULL, validation_id TEXT NOT NULL, payload_json TEXT NOT NULL, digest TEXT NOT NULL, PRIMARY KEY(series_id,generation))")
    db.execute("INSERT INTO eval_adoptions SELECT * FROM eval_current")
    db.execute("CREATE TABLE policy_adoptions(series_id TEXT NOT NULL, generation INTEGER NOT NULL, proposal_id TEXT NOT NULL, validation_id TEXT NOT NULL, policy_json TEXT NOT NULL, policy_digest TEXT NOT NULL, adopted_at INTEGER NOT NULL, actor_id TEXT NOT NULL, context TEXT NOT NULL, PRIMARY KEY(series_id,generation))")
    db.execute("INSERT INTO policy_adoptions SELECT * FROM current_profiles")
    run_evidence.create_schema(db)
    assurance_authority.create_schema(db)
    baseline_authority.create_schema(db)
    fixture_admission.create_schema(db)


def _load_json(row: sqlite3.Row, json_column: str, digest_column: str) -> dict[str, Any]:
    raw = row[json_column]
    stored_digest = row[digest_column]
    if type(raw) is not str or type(stored_digest) is not str or len(stored_digest) != 64:
        raise _error("STORAGE_CORRUPT")
    try:
        value = json.loads(raw)
        expected_raw, expected_digest = _packed(value)
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise _error("STORAGE_CORRUPT") from None
    if expected_raw != raw or expected_digest != stored_digest:
        raise _error("STORAGE_CORRUPT")
    if type(value) is not dict:
        raise _error("STORAGE_CORRUPT")
    return value


def _store_object(db: sqlite3.Connection, kind: str, identifier: str, document: dict[str, Any]) -> dict[str, str]:
    ref = content_ref(kind, identifier, document)
    raw, digest = _packed(document)
    existing = db.execute("SELECT digest FROM eval_objects WHERE kind=? AND id=?", (kind, identifier)).fetchone()
    if existing is not None and existing[0] != digest:
        raise _error("OBJECT_CONFLICT")
    if existing is None:
        db.execute("INSERT INTO eval_objects VALUES(?,?,?,?)", (kind, identifier, digest, raw))
    return ref


def _object(db: sqlite3.Connection, ref: dict[str, str]) -> dict[str, Any]:
    require_ref(ref)
    row = db.execute("SELECT * FROM eval_objects WHERE kind=? AND id=?", (ref["kind"], ref["id"])).fetchone()
    if row is None or row["digest"] != ref["digest"]:
        raise _error("REFERENCE_MISSING")
    value = _load_json(row, "payload_json", "digest")
    try:
        if content_ref(ref["kind"], ref["id"], value) != dict(ref):
            raise _error("STORAGE_CORRUPT")
    except (ContractError, ValueError, TypeError, KeyError):
        raise _error("STORAGE_CORRUPT") from None
    return value


def _policy(store: Any, db: sqlite3.Connection, series_id: str, now: int) -> tuple[dict[str, Any], dict[str, Any], int]:
    request = {"schema_version": 1, "action": "current", "request_id": "authority-policy-check", "series_id": series_id}
    current = store._current(db, request, now)
    if current.get("adopted") is not True or current.get("valid") is not True or type(current.get("policy")) is not dict:
        raise _error("PREREQUISITE_UNAVAILABLE")
    try:
        policy = validate_policy_profile(current["policy"])
        ref = content_ref("policy_profile", policy["policy_id"], policy)
    except (ContractError, TypeError, ValueError, KeyError, RecursionError):
        raise _error("PREREQUISITE_UNAVAILABLE") from None
    if current.get("policy_digest") != ref["digest"]:
        raise _error("STORAGE_CORRUPT")
    generation = current.get("generation")
    if type(generation) is not int or generation <= 0 or generation > MAX_INTEGER:
        raise _error("PREREQUISITE_UNAVAILABLE")
    return policy, ref, generation


def _proposal(db: sqlite3.Connection, proposal_id: str) -> tuple[sqlite3.Row, dict[str, Any]]:
    row = db.execute("SELECT * FROM eval_proposals WHERE id=?", (proposal_id,)).fetchone()
    if row is None:
        raise _error("PROPOSAL_MISSING")
    payload = _load_json(row, "payload_json", "digest")
    try:
        contract = validate_evaluation_contract(payload)
    except (ContractError, TypeError, ValueError, KeyError, RecursionError):
        raise _error("STORAGE_CORRUPT") from None
    if row["generation"] != contract["generation"]:
        raise _error("STORAGE_CORRUPT")
    return row, payload


def _evaluation_generation(db: sqlite3.Connection, series_id: str) -> int:
    """評価契約系列の世代を、方針系列とは分離して読む。"""
    row = db.execute("SELECT generation FROM eval_current WHERE series_id=?", (series_id,)).fetchone()
    if row is None:
        return 0
    if type(row[0]) is not int or not 0 <= row[0] <= MAX_INTEGER:
        raise _error("STORAGE_CORRUPT")
    return row[0]


def _check_prerequisite(contract: dict[str, Any], expected_generation: int) -> None:
    # 初回の非比較契約だけをこの未接続境界で扱う。旧契約やbaselineの
    # 根拠を想像して採択可能にしない。
    if expected_generation != 0 or contract["generation"] != 1 or contract["comparison"]["mode"] != "not_applicable":
        raise _error("PREREQUISITE_UNAVAILABLE")


def _calibration_rows(db: sqlite3.Connection, case_set_ref: dict[str, str], evaluator_ref: dict[str, str], now: int, permission_generation: int) -> bool:
    rows = db.execute("SELECT * FROM eval_calibrations").fetchall()
    for row in rows:
        if (type(row["created_at"]) is not int or type(row["expires_at"]) is not int
                or type(row["permission_generation"]) is not int
                or not 0 <= row["created_at"] < row["expires_at"] <= MAX_INTEGER
                or row["expires_at"] <= now
                or row["permission_generation"] != permission_generation):
            continue
        payload = _load_json(row, "payload_json", "digest")
        if payload.get("case_set_ref") != case_set_ref or payload.get("evaluator_ref") != evaluator_ref:
            continue
        report = payload.get("report")
        if (type(report) is dict and report.get("passed") is True
                and report.get("case_set_id") == case_set_ref["id"]):
            return True
    return False


def _validate_state(store: Any, db: sqlite3.Connection, contract: dict[str, Any], now: int,
                    policy_state=None) -> dict[str, Any]:
    _check_prerequisite(contract, 0)
    policy, policy_ref, policy_generation = (_policy(store, db, contract["policy_series_id"], now)
        if policy_state is None else policy_state)
    if contract["policy_generation"] != policy_generation:
        raise _error("PREREQUISITE_UNAVAILABLE")
    registry_value = _object(db, contract["registry_ref"])
    acceptance = _object(db, contract["case_set_ref"])
    calibration = _object(db, contract["calibration_case_set_ref"])
    try:
        bound = bind_evaluation_contract(contract, policy, registry_value, acceptance, calibration)
        other_rows = db.execute("SELECT * FROM eval_objects WHERE kind='case_set' AND id<>? ORDER BY id", (acceptance["case_set_id"],)).fetchall()
        other_sets = tuple(_load_json(row, "payload_json", "digest") for row in other_rows)
        report = corpus.corpus_report(acceptance, other_sets=other_sets)
    except (ContractError, AdoptionError, TypeError, ValueError, KeyError, RecursionError):
        raise _error("CONTRACT_INVALID") from None
    requirements = report.get("structural_requirements", {})
    permission_generation = store._permission_generation(db)
    admission = fixture_admission.for_contract(db, contract, now, permission_generation)
    fixed_ci = admission is not None and contract["use_cases"] == ["UC-CI"]
    if (not fixed_ci and requirements.get("acceptance_minimums_met") is not True) or report.get("usage_overlaps"):
        raise _error("ACCEPTANCE_PREREQUISITE_UNAVAILABLE")
    calibration_status: list[dict[str, Any]] = []
    for evaluator_ref in contract["evaluator_refs"]:
        passed = (admission["calibration"]["passed"] if fixed_ci else
            _calibration_rows(db, contract["calibration_case_set_ref"], evaluator_ref, now, permission_generation))
        calibration_status.append({"evaluator_ref": copy.deepcopy(evaluator_ref), "passed": passed})
        if not passed:
            raise _error("CALIBRATION_UNAVAILABLE")
    payload = {
        "contract": copy.deepcopy(bound["contract"]),
        "policy_ref": policy_ref,
        "registry_ref": copy.deepcopy(contract["registry_ref"]),
        "case_set_ref": copy.deepcopy(contract["case_set_ref"]),
        "calibration_case_set_ref": copy.deepcopy(contract["calibration_case_set_ref"]),
        "corpus_report": report,
        "calibration_status": calibration_status,
        "permission_generation": permission_generation,
        "checked_at": now,
    }
    return payload


def _assert_contract_history(db: sqlite3.Connection, current: sqlite3.Row,
                             contract: dict[str, Any]) -> sqlite3.Row:
    """採択historyと契約・proposal・validationの不変結合を照合する。"""
    validation = db.execute("SELECT * FROM eval_validations WHERE id=?", (current["validation_id"],)).fetchone()
    if validation is None:
        raise _error("CONTRACT_INVALID")
    proposal = db.execute("SELECT * FROM eval_proposals WHERE id=?", (current["proposal_id"],)).fetchone()
    if proposal is None:
        raise _error("CONTRACT_INVALID")
    proposal_payload = _load_json(proposal, "payload_json", "digest")
    validation_payload = _load_json(validation, "payload_json", "digest")
    contract_digest = _packed(contract)[1]
    expected_validation_fields = {
        "contract", "policy_ref", "registry_ref", "case_set_ref", "calibration_case_set_ref",
        "corpus_report", "calibration_status", "permission_generation", "checked_at",
        "proposal_id", "proposal_digest",
    }
    if (type(current["series_id"]) is not str or type(current["generation"]) is not int
            or not 0 <= current["generation"] <= MAX_INTEGER
            or current["generation"] != contract["generation"]
            or proposal["series_id"] != current["series_id"]
            or proposal["generation"] != contract["generation"]
            or proposal["actor_id"] != "manager" or proposal["context"] != "manager-context"
            or proposal_payload != contract or proposal["digest"] != contract_digest
            or validation["proposal_id"] != current["proposal_id"]
            or validation["proposal_digest"] != proposal["digest"]
            or set(validation_payload) != expected_validation_fields
            or validation_payload.get("proposal_id") != current["proposal_id"]
            or validation_payload.get("proposal_digest") != proposal["digest"]
            or validation_payload.get("contract") != contract
            or validation_payload.get("policy_ref") != contract["policy_ref"]
            or validation_payload.get("registry_ref") != contract["registry_ref"]
            or validation_payload.get("case_set_ref") != contract["case_set_ref"]
            or validation_payload.get("calibration_case_set_ref") != contract["calibration_case_set_ref"]
            or type(validation_payload.get("checked_at")) is not int
            or not 0 <= validation_payload["checked_at"] <= MAX_INTEGER
            or type(validation_payload.get("permission_generation")) is not int
            or not 0 <= validation_payload["permission_generation"] <= MAX_INTEGER
            or type(validation_payload.get("corpus_report")) is not dict
            or type(validation_payload.get("calibration_status")) is not list):
        raise _error("CONTRACT_INVALID")
    return validation


def _assert_contract_fresh(store: Any, db: sqlite3.Connection, current: sqlite3.Row,
                           contract: dict[str, Any], now: int) -> None:
    """不変結合に加えてvalidation期限とpermission世代を照合する。"""
    validation = _assert_contract_history(db, current, contract)
    validation_payload = _load_json(validation, "payload_json", "digest")
    if (type(validation["created_at"]) is not int
            or type(validation["expires_at"]) is not int
            or not 0 <= validation["created_at"] < validation["expires_at"] <= MAX_INTEGER
            or not validation["created_at"] <= now < validation["expires_at"]
            or type(validation["permission_generation"]) is not int
            or validation["permission_generation"] != store._permission_generation(db)
            or validation_payload["checked_at"] != validation["created_at"]
            or validation_payload["permission_generation"] != validation["permission_generation"]
            or store._actor_revoked(db, "manager")
            or store._actor_revoked(db, "validator")):
        raise _error("CONTRACT_INVALID")


def _assert_current_valid(store: Any, db: sqlite3.Connection, current: sqlite3.Row,
                          contract: dict[str, Any], now: int) -> None:
    """評価契約の不変結合と現在の有効期限を毎回照合する。"""
    _assert_contract_fresh(store, db, current, contract, now)


class EvaluationExtension:
    """AdoptionStore用の固定評価接続拡張。"""

    tables = TABLES
    schema_version = 3
    actions = {**_ACTION_ROLES, **resource_authority.ACTIONS, **assurance_authority.ACTIONS, **baseline_authority.ACTIONS}
    fresh_actions = _FRESH_ACTIONS | resource_authority.FRESH_ACTIONS | assurance_authority.FRESH_ACTIONS | baseline_authority.FRESH_ACTIONS
    digest = _source_digest()

    def create_schema(self, db: sqlite3.Connection) -> None:
        create_schema(db)

    def migrate_schema(self, db: sqlite3.Connection) -> None:
        migrate_schema(db)

    def validate_request(self, request: Any) -> dict[str, Any]:
        return _validate_request(request)

    @staticmethod
    def _pinned_policy(store, db, contract, now, *, require_current):
        current = store._policy_at(db, contract["policy_series_id"], contract["policy_generation"], now)
        if not current.get("adopted") or require_current and not current.get("valid"):
            raise _error("PREREQUISITE_UNAVAILABLE")
        policy = validate_policy_profile(current["policy"])
        ref = content_ref("policy_profile", policy["policy_id"], policy)
        if current["generation"] != contract["policy_generation"] or ref != contract["policy_ref"]:
            raise _error("STORAGE_CORRUPT")
        return policy, ref, current["generation"]

    def _check_start(self, store: Any, db: sqlite3.Connection, run_id: str, now: int) -> tuple[dict[str, Any], dict[str, Any]]:
        """resource_authorityから呼ばれる開始前の最新束縛検査。"""
        row = db.execute("SELECT * FROM eval_runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            raise _error("RUN_MISSING")
        manifest = _load_json(row, "manifest_json", "manifest_digest")
        plan = _load_json(row, "plan_json", "plan_digest")
        current = db.execute("SELECT * FROM eval_adoptions WHERE series_id=? AND generation=?", (row["contract_series_id"], row["contract_generation"])).fetchone()
        if current is None or current["generation"] != row["contract_generation"]:
            raise _error("CONTRACT_INVALID")
        contract = validate_evaluation_contract(_load_json(current, "payload_json", "digest"))
        proposal = db.execute("SELECT digest FROM eval_proposals WHERE id=?", (current["proposal_id"],)).fetchone()
        if proposal is None or contract["generation"] != row["contract_generation"] or _packed(contract)[1] != proposal[0]:
            raise _error("CONTRACT_INVALID")
        policy_state = self._pinned_policy(store, db, contract, now, require_current=True)
        _validate_state(store, db, contract, now, policy_state=policy_state)
        _assert_current_valid(store, db, current, contract, now)
        policy, _, _ = policy_state
        registry_value = _object(db, contract["registry_ref"])
        acceptance = _object(db, contract["case_set_ref"])
        try:
            bound = bind_run_manifest(manifest, contract, plan, policy, registry_value, acceptance)
        except (ContractError, TypeError, ValueError, KeyError, RecursionError):
            raise _error("CONTRACT_INVALID") from None
        return bound["manifest"], bound["plan"]

    def _bound_evidence_run(self, store: Any, db: sqlite3.Connection, run_id: str, now: int):
        row = db.execute("SELECT * FROM eval_runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            raise _error("RUN_MISSING")
        manifest = _load_json(row, "manifest_json", "manifest_digest")
        plan = _load_json(row, "plan_json", "plan_digest")
        history = db.execute("SELECT * FROM eval_adoptions WHERE series_id=? AND generation=?",
            (row["contract_series_id"], row["contract_generation"])).fetchone()
        if history is None:
            raise _error("CONTRACT_MISSING")
        contract = validate_evaluation_contract(_load_json(history, "payload_json", "digest"))
        # 旧runは当時の採択世代を使うが、historyとproposal/validationの
        # 不変結合は毎回検査する。期限・permissionのfresh判定はreceipt/current側に委ねる。
        _assert_contract_history(db, history, contract)
        if contract["comparison"]["mode"] == "required":
            raise _error("BASELINE_CONTEXT_UNAVAILABLE")
        policy, _, _ = self._pinned_policy(store, db, contract, now, require_current=False)
        bound = bind_run_manifest(manifest, contract, plan, policy,
            _object(db, contract["registry_ref"]), _object(db, contract["case_set_ref"]))
        return bound, None

    def _baseline_source(self, store, db, run_id, now):
        source = assurance_authority.baseline_source(store, db, run_id, now,
            lambda identifier: self._bound_evidence_run(store, db, identifier, now))
        try:
            self._check_start(store, db, run_id, now)
        except (AdoptionError, ContractError):
            source["reasons"].append("ADOPTED_CONDITIONS_UNAVAILABLE")
        return source

    def execute(self, store: Any, db: sqlite3.Connection, request: dict[str, Any], actor_id: str, context: str, now: int) -> dict[str, Any]:
        action = request["action"]
        request_id = request["request_id"]
        if action == "contract_preflight":
            # 候補runを始める前の条件照合だけを行う。validationや採択を
            # 作らず、同じrequest IDでも現在の失効状態を再計算する。
            proposal, next_contract = _proposal(db, request["proposal_id"])
            if proposal["actor_id"] != "manager" or proposal["context"] != "manager-context":
                raise _error("STORAGE_CORRUPT")
            current = db.execute("SELECT * FROM eval_current WHERE series_id=?", (proposal["series_id"],)).fetchone()
            if current is None:
                raise _error("CONTRACT_MISSING")
            history = db.execute("SELECT * FROM eval_adoptions WHERE series_id=? AND generation=?",
                (current["series_id"], current["generation"])).fetchone()
            if history is None or dict(history) != dict(current):
                raise _error("STORAGE_CORRUPT")
            previous = validate_evaluation_contract(_load_json(history, "payload_json", "digest"))
            if request["expected_contract_ref"] != content_ref("evaluation_contract", previous["contract_id"], previous):
                raise _error("BINDING_MISMATCH")
            if next_contract["generation"] != previous["generation"] + 1:
                raise _error("GENERATION_CONFLICT")
            _validate_state(store, db, previous, now)
            _assert_current_valid(store, db, history, previous, now)
            use_request = {"schema_version": 1, "action": "baseline_use", "request_id": request_id,
                "series_id": request["baseline_series_id"],
                "expected_baseline_ref": request["expected_baseline_ref"],
                "expected_contract_ref": request["expected_contract_ref"]}
            resolved_sources = {}

            def resolve_source(run_id):
                source = self._baseline_source(store, db, run_id, now)
                resolved_sources[run_id] = source
                return source

            try:
                current_baseline = baseline_authority.execute(store, db, use_request, actor_id, context, now,
                    resolve_source)
                if current_baseline.get("use") is not True:
                    raise _error("PREREQUISITE_UNAVAILABLE")
                baseline = current_baseline["baseline"]
                source_row = db.execute("SELECT contract_series_id, contract_generation FROM eval_runs WHERE run_id=?",
                    (baseline["source_run_ref"]["id"],)).fetchone()
                if (source_row is None or source_row["contract_series_id"] != proposal["series_id"]
                        or source_row["contract_generation"] != previous["generation"]):
                    raise _error("BINDING_MISMATCH")
                # 現在利用検査に使った同じsourceを条件比較へ渡す。
                source = resolved_sources[baseline["source_run_ref"]["id"]]
                transition = contract_updates.bind_contract_transition(previous, next_contract,
                    baseline_record=baseline, baseline_source_bound=source["bound"])
            except (ContractError, run_evidence.EvidenceError, resources.ResourceError) as error:
                raise _error(error.code) from None
            return _result(action, request_id, proposal_id=proposal["id"], proposal_digest=proposal["digest"],
                preflight_ready=True, candidate_run_required=True, adoption_verified=False,
                checked_at=now, permission_generation=store._permission_generation(db), transition=transition)
        if action == "fixture_prepare":
            policy, _, generation = _policy(store, db, request["policy_series_id"], now)
            try:
                value = fixture_admission.prepare(db, policy, generation, request["run_id"], now,
                    store._permission_generation(db))
                prepared = value["prepared"]
                bound = prepared["bound_run"]
                for document in (bound["registry"], bound["case_set"], prepared["calibration_case_set"]):
                    kind = document["kind"]
                    identifier = document["registry_id"] if kind == "control_registry" else document["case_set_id"]
                    _store_object(db, kind, identifier, document)
            except ContractError as error:
                raise _error(error.code) from None
            return _result(action, request_id, prepared=prepared,
                calibration=value["calibration"], materialization_ref=value["materialization"]["manifest_ref"])
        if action in baseline_authority.FIELDS:
            try:
                fields = baseline_authority.execute(store, db, request, actor_id, context, now,
                    lambda run_id: self._baseline_source(store, db, run_id, now))
            except (ContractError, run_evidence.EvidenceError, resources.ResourceError) as error:
                raise _error(error.code) from None
            return fields
        if action in assurance_authority.FIELDS:
            try:
                if action == "evidence_open":
                    self._check_start(store, db, request["run_id"], now)
                fields = assurance_authority.execute(store, db, request, actor_id, context, now,
                    lambda run_id: self._bound_evidence_run(store, db, run_id, now))
                if action == "evidence_current":
                    try:
                        self._check_start(store, db, request["run_id"], now)
                    except (AdoptionError, ContractError):
                        fields["reasons"].append("ADOPTED_CONDITIONS_UNAVAILABLE")
            except (run_evidence.EvidenceError, resources.ResourceError) as error:
                raise _error(error.code) from None
            return _result(action, request_id, **fields)
        if action in resource_authority.FIELDS:
            try:
                fields = resource_authority.execute(
                    db, request, now,
                    lambda run_id: self._check_start(store, db, run_id, now),
                )
            except AdoptionError:
                raise
            except resource_authority.ResourceError as error:
                raise _error(getattr(error, "code", "RESOURCE_FAILURE")) from None
            except ContractError:
                raise _invalid() from None
            return _result(action, request_id, **fields)
        if action == "object_register":
            document = request["document"]
            if document.get("kind") == "control_registry":
                try:
                    document = registry.validate_registry(document)
                except (ContractError, TypeError, ValueError, KeyError, RecursionError):
                    raise _error("OBJECT_INVALID") from None
                ref = _store_object(db, "control_registry", document["registry_id"], document)
            elif document.get("kind") == "case_set":
                try:
                    document = corpus.validate_case_set(document)
                except (ContractError, TypeError, ValueError, KeyError, RecursionError):
                    raise _error("OBJECT_INVALID") from None
                ref = _store_object(db, "case_set", document["case_set_id"], document)
            else:
                raise _error("OBJECT_KIND")
            return _result(action, request_id, content_ref=ref)

        if action == "calibration_record":
            ref = request["case_set_ref"]
            case_set = _object(db, ref)
            try:
                report = corpus.check_calibration(case_set, request["observations"])
            except (ContractError, TypeError, ValueError, KeyError, RecursionError):
                raise _error("CALIBRATION_INVALID") from None
            permission_generation = store._permission_generation(db)
            payload = {"case_set_ref": copy.deepcopy(ref), "evaluator_ref": copy.deepcopy(request["evaluator_ref"]), "observations": copy.deepcopy(request["observations"]), "report": report}
            raw, digest = _packed(payload)
            expires = min(MAX_INTEGER, now + VALIDATION_TTL)
            existing = db.execute("SELECT * FROM eval_calibrations WHERE id=?", (request["calibration_id"],)).fetchone()
            if existing is not None:
                if (existing["digest"] != digest
                        or type(existing["created_at"]) is not int
                        or type(existing["expires_at"]) is not int
                        or type(existing["permission_generation"]) is not int
                        or not 0 <= existing["created_at"] < existing["expires_at"] <= MAX_INTEGER):
                    raise _error("CALIBRATION_CONFLICT")
                stored_payload = _load_json(existing, "payload_json", "digest")
                if stored_payload != payload:
                    raise _error("CALIBRATION_CONFLICT")
                return _result(action, request_id, calibration_id=request["calibration_id"], report=stored_payload["report"], expires_at=existing["expires_at"], passed=bool(stored_payload["report"].get("passed")))
            db.execute("INSERT INTO eval_calibrations VALUES(?,?,?,?,?,?)", (request["calibration_id"], raw, digest, now, expires, permission_generation))
            return _result(action, request_id, calibration_id=request["calibration_id"], report=report, expires_at=expires, passed=bool(report.get("passed")))

        if action == "contract_propose":
            contract = request["contract"]
            expected = _evaluation_generation(db, request["series_id"])
            if request["expected_generation"] != expected:
                raise _error("GENERATION_CONFLICT")
            if contract["generation"] != expected + 1:
                raise _error("GENERATION_CONFLICT")
            raw, digest = _packed(contract)
            existing = db.execute("SELECT * FROM eval_proposals WHERE id=?", (request["proposal_id"],)).fetchone()
            if existing is not None:
                if (existing["digest"] != digest or existing["series_id"] != request["series_id"]
                        or existing["generation"] != contract["generation"]
                        or existing["actor_id"] != actor_id or existing["context"] != context):
                    raise _error("PROPOSAL_CONFLICT")
            else:
                db.execute("INSERT INTO eval_proposals VALUES(?,?,?,?,?,?,?)", (request["proposal_id"], request["series_id"], contract["generation"], raw, digest, actor_id, context))
            return _result(action, request_id, proposal_id=request["proposal_id"], series_id=request["series_id"], generation=contract["generation"], proposal_digest=digest)

        if action == "contract_validate":
            row, payload = _proposal(db, request["proposal_id"])
            contract = payload
            validation_payload = _validate_state(store, db, contract, now)
            validation_payload.update({"proposal_id": request["proposal_id"], "proposal_digest": row["digest"]})
            raw, digest = _packed(validation_payload)
            permission_generation = store._permission_generation(db)
            expires = min(MAX_INTEGER, now + VALIDATION_TTL)
            existing = db.execute("SELECT * FROM eval_validations WHERE id=?", (request["validation_id"],)).fetchone()
            if existing is not None:
                if (existing["digest"] != digest or existing["proposal_id"] != request["proposal_id"]
                        or existing["proposal_digest"] != row["digest"]
                        or type(existing["created_at"]) is not int
                        or type(existing["expires_at"]) is not int
                        or type(existing["permission_generation"]) is not int
                        or not 0 <= existing["created_at"] < existing["expires_at"] <= MAX_INTEGER):
                    raise _error("VALIDATION_CONFLICT")
                stored_payload = _load_json(existing, "payload_json", "digest")
                if stored_payload != validation_payload:
                    raise _error("VALIDATION_CONFLICT")
                return _result(action, request_id, proposal_id=request["proposal_id"], validation_id=request["validation_id"], validation_digest=existing["digest"], expires_at=existing["expires_at"], permission_generation=existing["permission_generation"], passed=True)
            db.execute("INSERT INTO eval_validations VALUES(?,?,?,?,?,?,?,?)", (request["validation_id"], request["proposal_id"], row["digest"], raw, digest, now, expires, permission_generation))
            return _result(action, request_id, proposal_id=request["proposal_id"], validation_id=request["validation_id"], validation_digest=digest, expires_at=expires, permission_generation=permission_generation, passed=True)

        if action == "contract_adopt":
            row, payload = _proposal(db, request["proposal_id"])
            if request["expected_generation"] != _evaluation_generation(db, row["series_id"]):
                raise _error("GENERATION_CONFLICT")
            if row["actor_id"] != actor_id:
                raise _error("PROPOSER_MISMATCH")
            validation = db.execute("SELECT * FROM eval_validations WHERE id=?", (request["validation_id"],)).fetchone()
            if validation is None or validation["proposal_id"] != request["proposal_id"] or validation["proposal_digest"] != row["digest"]:
                raise _error("VALIDATION_MISMATCH")
            if not validation["created_at"] <= now < validation["expires_at"] or validation["permission_generation"] != store._permission_generation(db):
                raise _error("VALIDATION_EXPIRED")
            contract = payload
            checked = _validate_state(store, db, contract, now)
            validation_payload = _load_json(validation, "payload_json", "digest")
            if validation_payload.get("proposal_digest") != row["digest"] or validation_payload.get("permission_generation") != validation["permission_generation"]:
                raise _error("VALIDATION_MISMATCH")
            # 検証時の束縛内容（時刻だけを除く）が採択時の再検査結果と
            # 同じであることを確認し、validationの自己申告や改ざんを採用しない。
            fresh_checked = dict(checked)
            saved_checked = dict(validation_payload)
            for value in (fresh_checked, saved_checked):
                value.pop("checked_at", None)
                value.pop("proposal_id", None)
                value.pop("proposal_digest", None)
            if fresh_checked != saved_checked:
                raise _error("VALIDATION_MISMATCH")
            current = db.execute("SELECT * FROM eval_current WHERE series_id=?", (row["series_id"],)).fetchone()
            if current is not None:
                raise _error("GENERATION_CONFLICT")
            current_payload = copy.deepcopy(contract)
            raw, digest = _packed(current_payload)
            db.execute("INSERT INTO eval_current VALUES(?,?,?,?,?,?)", (row["series_id"], contract["generation"], request["proposal_id"], request["validation_id"], raw, digest))
            db.execute("INSERT INTO eval_adoptions VALUES(?,?,?,?,?,?)", (row["series_id"], contract["generation"], request["proposal_id"], request["validation_id"], raw, digest))
            return _result(action, request_id, series_id=row["series_id"], generation=contract["generation"], proposal_id=request["proposal_id"], validation_id=request["validation_id"], contract_digest=content_ref("evaluation_contract", contract["contract_id"], contract)["digest"])

        if action == "contract_current":
            row = db.execute("SELECT * FROM eval_current WHERE series_id=?", (request["series_id"],)).fetchone()
            if row is None:
                return _result(action, request_id, series_id=request["series_id"], adopted=False, valid=False, generation=0, contract=None, proposal_id=None, validation_id=None)
            payload = _load_json(row, "payload_json", "digest")
            contract = validate_evaluation_contract(payload)
            valid = True
            try:
                _validate_state(store, db, contract, now)
                _assert_current_valid(store, db, row, contract, now)
            except (AdoptionError, ContractError, TypeError, ValueError, KeyError, RecursionError):
                valid = False
            return _result(action, request_id, series_id=request["series_id"], adopted=True, valid=valid, generation=row["generation"], contract=contract, proposal_id=row["proposal_id"], validation_id=row["validation_id"])

        if action == "run_begin":
            current = db.execute("SELECT * FROM eval_current WHERE series_id=?", (request["contract_series_id"],)).fetchone()
            if current is None:
                raise _error("CONTRACT_MISSING")
            current_payload = _load_json(current, "payload_json", "digest")
            contract = validate_evaluation_contract(current_payload)
            _validate_state(store, db, contract, now)
            _assert_current_valid(store, db, current, contract, now)
            policy, _, _ = _policy(store, db, contract["policy_series_id"], now)
            registry_value = _object(db, contract["registry_ref"])
            acceptance = _object(db, contract["case_set_ref"])
            bound = bind_run_manifest(request["manifest"], contract, request["plan"], policy, registry_value, acceptance)
            manifest = bound["manifest"]
            if not manifest["created_at"] <= now < manifest["deadline"]:
                raise _error("RUN_TIME_INVALID")
            manifest_raw, manifest_digest = _packed(manifest)
            plan_raw, plan_digest = _packed(bound["plan"])
            book = resources.ResourceBook(db)
            snapshot = book.create_run(manifest["run_id"], manifest_digest, policy, manifest["profile"], request_id, now, manifest["deadline"])
            db.execute("INSERT INTO eval_runs VALUES(?,?,?,?,?,?,?)", (manifest["run_id"], manifest_raw, manifest_digest, plan_raw, plan_digest, request["contract_series_id"], contract["generation"]))
            return _result(action, request_id, run_id=manifest["run_id"], contract_generation=contract["generation"], resource_snapshot=snapshot)

        if action == "run_status":
            row = db.execute("SELECT * FROM eval_runs WHERE run_id=?", (request["run_id"],)).fetchone()
            if row is None:
                raise _error("RUN_MISSING")
            manifest = _load_json(row, "manifest_json", "manifest_digest")
            plan = _load_json(row, "plan_json", "plan_digest")
            snapshot = resources.ResourceBook(db).snapshot(request["run_id"], now)
            return _result(action, request_id, run_id=request["run_id"], manifest=manifest, plan=plan, contract_series_id=row["contract_series_id"], contract_generation=row["contract_generation"], resource_snapshot=snapshot)
        raise _error("INVALID_ACTION")


def _result(action: str, request_id: str, **fields: Any) -> dict[str, Any]:
    return {"schema_version": 1, "kind": _KIND, "action": action, "request_id": request_id, "ci_eligible": False, **fields}


__all__ = ["EvaluationExtension", "TABLES", "VALIDATION_TTL", "create_schema"]
