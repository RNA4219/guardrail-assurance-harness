"""AdoptionStoreの既知schemaを明示的に次世代へ移行する。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any

from .contracts import ContractError, MAX_DOCUMENT_BYTES, MAX_INTEGER
from . import transition_migrations
from .wire import canonical_bytes


_V2_VERSION = 2
_V3_VERSION = 3
_V4_VERSION = 4
_V4_PREDECESSOR_EXTENSION_DIGEST = "d07df9acab34ae3e8add8a1b43f162ae3873155e9519f1b3529ed15e4bc8c38a"
_V4_ADOPTION_EXTENSION_DIGEST = "029327c9dd044101406b4ac9d07d4f8c1255336e96e978379d03a68b1798da2b"
_V2_EXTENSION_DIGEST = "42aebfed356613bfe6c78f00757c06a23251ba3fb9eede237a1358116853ccc9"
_V3_EXTENSION_DIGEST = "e373899c67b3bbeed1bc66f44c8dd7d2186b3b6425a4c84fea62a00a6cc2e58d"
_V2_BOOTSTRAP_DIGEST = "7311fbb7327517ed67c5583c0577ff2db366297532218097e1e257b245a19c2e"
_V2_VALIDATOR_DIGEST = "acc76f697b442719da8f9465adaf26c6edfd29e02f716bb1faa54c2ff28b1448"

_V2_COLUMNS = {
    "adoption_meta": {"key", "value"},
    "adoption_config": {"key", "value"},
    "proposals": {"proposal_id", "series_id", "expected_generation", "policy_json", "policy_digest", "created_at", "actor_id", "context", "bootstrap_digest", "validator_digest"},
    "validations": {"validation_id", "proposal_id", "proposal_digest", "expected_generation", "observed_at", "expires_at", "actor_id", "context", "permission_generation", "bootstrap_digest", "validator_digest"},
    "current_profiles": {"series_id", "generation", "proposal_id", "validation_id", "policy_json", "policy_digest", "adopted_at", "actor_id", "context"},
    "revocations": {"entity_type", "entity_id", "generation", "observed_at"},
    "idempotency": {"request_id", "request_digest", "actor_id", "context", "response_json", "response_digest"},
    "eval_objects": {"kind", "id", "digest", "payload_json"},
    "eval_calibrations": {"id", "payload_json", "digest", "created_at", "expires_at", "permission_generation"},
    "eval_proposals": {"id", "series_id", "generation", "payload_json", "digest", "actor_id", "context"},
    "eval_validations": {"id", "proposal_id", "proposal_digest", "payload_json", "digest", "created_at", "expires_at", "permission_generation"},
    "eval_current": {"series_id", "generation", "proposal_id", "validation_id", "payload_json", "digest"},
    "eval_runs": {"run_id", "manifest_json", "manifest_digest", "plan_json", "plan_digest", "contract_series_id", "contract_generation"},
    "resource_meta": {"key", "value"},
    "resource_runs": {"run_id", "manifest_digest", "policy_json", "policy_digest", "profile", "created_at", "deadline", "owner_id", "owner_epoch", "lease_until", "cancelled", "breached", "closed_at"},
    "resource_operations": {"operation_id", "run_id", "owner_epoch", "reservation_json", "reservation_digest", "intended_at", "stopped_at", "released", "usage_json", "usage_digest", "settled_at", "cost_micros", "conflicted", "exposure_micros"},
    "resource_bindings": {"operation_id", "run_id", "entry_digest", "scenario"},
    "resource_events": {"event_id", "operation_id", "event_digest", "response_json", "response_digest"},
}
_V2_TABLES = set(_V2_COLUMNS)

# v3はsource更新で再計算せず、移行対象を固定する。ここへ新しい表を混ぜると
# v3 DBをv4として誤認するため、列集合も明示的に保持する。
_V3_COLUMNS = {
    **_V2_COLUMNS,
    "eval_adoptions": {"series_id", "generation", "proposal_id", "validation_id", "payload_json", "digest"},
    "policy_adoptions": {"series_id", "generation", "proposal_id", "validation_id", "policy_json", "policy_digest", "adopted_at", "actor_id", "context"},
    "run_evidence_meta": {"key", "value"},
    "bound_runs": {"run_id", "bundle_json", "bundle_digest", "profile_json", "profile_digest", "baseline_json", "baseline_digest", "started_at", "updated_at"},
    "run_state": {"run_id", "state", "hold_reason", "aggregate_digest", "decision_digest", "finalized_at", "evidence_state", "evidence_checked_at", "evidence_valid_until", "evidence_generation"},
    "attempts": {"attempt_id", "run_id", "attempt_json", "attempt_digest", "delivery_count"},
    "attempt_events": {"event_id", "run_id", "attempt_id", "event_kind", "received_digest", "created_at"},
    "evidence_events": {"event_id", "run_id", "event_kind", "state", "valid_until", "generation", "event_json", "event_digest", "created_at"},
    "aggregates": {"run_id", "aggregate_digest", "aggregate_json", "created_at"},
    "decisions": {"run_id", "decision_digest", "decision_json", "created_at"},
    "terminals": {"run_id", "terminal_json", "terminal_digest", "created_at"},
    "authority_artifacts": {"kind", "id", "digest", "payload_json", "run_id"},
    "authority_attempt_origins": {"attempt_id", "attempt_digest", "actor_id", "context", "permission_generation", "recorded_at"},
    "authority_run_receipts": {"run_id", "payload_json", "digest", "permission_generation", "created_at", "revoked_at"},
    "authority_run_events": {"event_id", "run_id", "payload_json", "digest", "created_at"},
    "baseline_proposals": {"proposal_id", "series_id", "run_id", "expected_generation", "contract_generation", "proposal_json", "proposal_digest", "created_at", "actor_id", "context"},
    "baseline_validations": {"validation_id", "proposal_id", "proposal_digest", "validation_json", "validation_digest", "created_at", "expires_at", "permission_generation", "actor_id", "context"},
    "baseline_adoptions": {"adoption_id", "series_id", "generation", "proposal_id", "validation_id", "baseline_json", "baseline_digest", "adopted_at", "actor_id", "context", "permission_generation"},
    "baseline_current": {"series_id", "generation", "adoption_id", "baseline_json", "baseline_digest"},
    "baseline_revocations": {"series_id", "generation", "observed_at", "actor_id", "context"},
    "fixture_admissions": {"run_id", "contract_digest", "payload_json", "digest", "created_at", "permission_generation"},
}
_V3_TABLES = set(_V3_COLUMNS)
_V4_COLUMNS = {**_V3_COLUMNS, **transition_migrations.TABLES}
_V4_TABLES = set(_V4_COLUMNS)
_POLICY_ADOPTION_COLUMNS = {
    "series_id", "generation", "proposal_id", "validation_id", "policy_json",
    "policy_digest", "adopted_at", "actor_id", "context",
}
_POLICY_ADOPTION_ORDER = (
    "series_id", "generation", "proposal_id", "validation_id", "policy_json",
    "policy_digest", "adopted_at", "actor_id", "context",
)


class MigrationError(ValueError):
    """移行失敗を固定codeだけで表す。"""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _error(code: str) -> MigrationError:
    return MigrationError(code)


def _digest(value: Any) -> str:
    try:
        raw = canonical_bytes(value)
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise _error("STORAGE_CORRUPT") from None
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise _error("STORAGE_CORRUPT")
    return hashlib.sha256(raw).hexdigest()


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate")
        value[key] = item
    return value


def _reject_number(_: str) -> None:
    raise ValueError("number")


def _stored_json(raw: Any, digest: Any, *, nullable: bool = False) -> dict[str, Any] | None:
    if nullable and raw is None and digest is None:
        return None
    if type(raw) is not str or type(digest) is not str:
        raise _error("STORAGE_CORRUPT")
    if len(raw.encode("utf-8")) > MAX_DOCUMENT_BYTES:
        raise _error("STORAGE_CORRUPT")
    try:
        value = json.loads(raw, object_pairs_hook=_pairs,
                          parse_float=_reject_number, parse_constant=_reject_number)
        canonical = canonical_bytes(value)
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise _error("STORAGE_CORRUPT") from None
    if type(value) is not dict or canonical.decode("utf-8") != raw:
        raise _error("STORAGE_CORRUPT")
    if hashlib.sha256(canonical).hexdigest() != digest:
        raise _error("STORAGE_CORRUPT")
    return value


def _verify_columns(db: sqlite3.Connection, table_columns: dict[str, set[str]]) -> None:
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if tables != set(table_columns):
        raise _error("UNSUPPORTED_STORE")
    for table, expected in table_columns.items():
        actual = {row[1] for row in db.execute('PRAGMA table_info("' + table + '")')}
        if actual != expected:
            raise _error("UNSUPPORTED_STORE")


def _verify_json_rows(db: sqlite3.Connection) -> None:
    pairs = (
        ("proposals", "policy_json", "policy_digest", False),
        ("current_profiles", "policy_json", "policy_digest", False),
        ("idempotency", "response_json", "response_digest", True),
        ("eval_objects", "payload_json", "digest", False),
        ("eval_calibrations", "payload_json", "digest", False),
        ("eval_proposals", "payload_json", "digest", False),
        ("eval_validations", "payload_json", "digest", False),
        ("eval_current", "payload_json", "digest", False),
        ("eval_runs", "manifest_json", "manifest_digest", False),
        ("eval_runs", "plan_json", "plan_digest", False),
        ("resource_runs", "policy_json", "policy_digest", False),
        ("resource_operations", "reservation_json", "reservation_digest", False),
        ("resource_operations", "usage_json", "usage_digest", True),
        ("resource_events", "response_json", "response_digest", False),
    )
    for table, raw_column, digest_column, nullable in pairs:
        for row in db.execute("SELECT * FROM \"" + table + "\""):
            _stored_json(row[raw_column], row[digest_column], nullable=nullable)

    for row in db.execute("SELECT proposal_id,proposal_digest FROM validations"):
        proposal = db.execute("SELECT policy_digest FROM proposals WHERE proposal_id=?", (row[0],)).fetchone()
        if proposal is None or proposal[0] != row[1]:
            raise _error("STORAGE_CORRUPT")


def _verify_v3_json_rows(db: sqlite3.Connection, *, adopted: bool = False) -> None:
    """v3で追加された保存payloadのcanonical bytes/hashだけを検査する。"""

    _verify_json_rows(db)
    pairs = (
        ("eval_adoptions", "payload_json", "digest", False),
        ("policy_adoptions", "policy_json", "policy_digest", False),
        ("bound_runs", "bundle_json", "bundle_digest", False),
        ("bound_runs", "profile_json", "profile_digest", False),
        ("bound_runs", "baseline_json", "baseline_digest", True),
        ("attempts", "attempt_json", "attempt_digest", False),
        ("attempt_events", None, None, False),
        ("evidence_events", "event_json", "event_digest", False),
        ("aggregates", "aggregate_json", "aggregate_digest", False),
        ("decisions", "decision_json", "decision_digest", False),
        ("terminals", "terminal_json", "terminal_digest", False),
        ("authority_artifacts", "payload_json", "digest", False),
        ("authority_run_receipts", "payload_json", "digest", False),
        ("authority_run_events", "payload_json", "digest", False),
        ("baseline_proposals", "proposal_json", "proposal_digest", False),
        ("baseline_validations", "validation_json", "validation_digest", False),
        ("baseline_adoptions", "baseline_json", "baseline_digest", False),
        ("baseline_current", "baseline_json", "baseline_digest", False),
        ("fixture_admissions", "payload_json", "digest", False),
    )
    for table, raw_column, digest_column, nullable in pairs:
        if raw_column is None:
            continue
        for row in db.execute('SELECT * FROM "' + table + '"'):
            _stored_json(row[raw_column], row[digest_column], nullable=nullable)

    _verify_v3_history_graph(db, adopted=adopted)


def _verify_v3_history_graph(db: sqlite3.Connection, *, adopted: bool = False) -> None:
    """v3のcurrentポインタと不変履歴の参照鎖を再照合する。

    ここでは時刻の鮮度や現在の権限を再計算せず、保存済み行同士の結合だけを
    検査する。移行時点で参照先が欠けているDBをv4として受け入れないための
    保全検査であり、通常のauthority判定の代替ではない。
    """
    # evaluationのvalidationはproposalのdigestとpayload内の同じ参照を持つ。
    for row in db.execute("SELECT * FROM eval_validations"):
        proposal = db.execute("SELECT * FROM eval_proposals WHERE id=?", (row["proposal_id"],)).fetchone()
        if (proposal is None or row["proposal_digest"] != proposal["digest"]):
            raise _error("STORAGE_CORRUPT")
        payload = _stored_json(row["payload_json"], row["digest"])
        if (payload.get("proposal_id") != row["proposal_id"]
                or payload.get("proposal_digest") != row["proposal_digest"]):
            raise _error("STORAGE_CORRUPT")

    def verify_eval_row(row: sqlite3.Row) -> None:
        proposal = db.execute("SELECT * FROM eval_proposals WHERE id=?", (row["proposal_id"],)).fetchone()
        validation = db.execute("SELECT * FROM eval_validations WHERE id=?", (row["validation_id"],)).fetchone()
        if (proposal is None or validation is None
                or row["series_id"] != proposal["series_id"]
                or row["generation"] != proposal["generation"]
                or row["digest"] != proposal["digest"]
                or validation["proposal_id"] != row["proposal_id"]
                or validation["proposal_digest"] != proposal["digest"]):
            raise _error("STORAGE_CORRUPT")

    for table in ("eval_current", "eval_adoptions"):
        for row in db.execute('SELECT * FROM "' + table + '"'):
            verify_eval_row(row)

    # policyの履歴は複数世代を保持できるため、履歴全体をcurrentと同一とは
    # 仮定しない。各履歴行のproposal/validation結合と、currentの同世代行を
    # 個別に検査する。
    def verify_policy_row(row: sqlite3.Row) -> None:
        proposal = db.execute("SELECT * FROM proposals WHERE proposal_id=?", (row["proposal_id"],)).fetchone()
        validation = db.execute("SELECT * FROM validations WHERE validation_id=?", (row["validation_id"],)).fetchone()
        if (proposal is None or validation is None
                or row["series_id"] != proposal["series_id"]
                or type(proposal["expected_generation"]) is not int
                or proposal["expected_generation"] < 0
                or row["generation"] != proposal["expected_generation"] + 1
                or row["policy_digest"] != proposal["policy_digest"]
                or row["policy_json"] != proposal["policy_json"]
                or validation["proposal_id"] != row["proposal_id"]
                or validation["proposal_digest"] != proposal["policy_digest"]):
            raise _error("STORAGE_CORRUPT")

    for row in db.execute("SELECT * FROM policy_adoptions"):
        verify_policy_row(row)
    for row in db.execute("SELECT * FROM current_profiles"):
        verify_policy_row(row)
        history = db.execute(
            "SELECT * FROM policy_adoptions WHERE series_id=? AND generation=?",
            (row["series_id"], row["generation"]),
        ).fetchone()
        if history is None or tuple(history) != tuple(row):
            raise _error("STORAGE_CORRUPT")

    # baseline currentはadoption→proposal/validationの鎖と本文・digestを
    # 完全に一致させる。過去世代のadoptionは将来更新のため保持され得るため、
    # currentから到達する世代を中心に検査する。
    for row in db.execute("SELECT * FROM baseline_current"):
        adopted = db.execute("SELECT * FROM baseline_adoptions WHERE adoption_id=?", (row["adoption_id"],)).fetchone()
        if adopted is None:
            raise _error("STORAGE_CORRUPT")
        proposal = db.execute("SELECT * FROM baseline_proposals WHERE proposal_id=?", (adopted["proposal_id"],)).fetchone()
        validation = db.execute("SELECT * FROM baseline_validations WHERE validation_id=?", (adopted["validation_id"],)).fetchone()
        if (proposal is None or validation is None
                or adopted["series_id"] != row["series_id"]
                or adopted["generation"] != row["generation"]
                or adopted["baseline_json"] != row["baseline_json"]
                or adopted["baseline_digest"] != row["baseline_digest"]
                or validation["proposal_id"] != adopted["proposal_id"]
                or validation["proposal_digest"] != proposal["proposal_digest"]):
            raise _error("STORAGE_CORRUPT")
        proposal_payload = _stored_json(proposal["proposal_json"], proposal["proposal_digest"])
        adopted_baseline = _stored_json(adopted["baseline_json"], adopted["baseline_digest"])
        if (proposal_payload.get("record") != adopted_baseline
                or proposal_payload.get("series_id") != adopted["series_id"]):
            raise _error("STORAGE_CORRUPT")

    # authority receiptの5参照は、同runのartifact行へ一対一で到達できる
    # 必須参照でなければならない。artifact本文そのもののcanonical/hashは
    # 上段で既に検査済みである。
    expected_kinds = {
        "manifest_ref": "run_manifest", "bundle_ref": "bound_bundle",
        "decision_ref": "run_decision", "evidence_ref": "evidence",
        "closure_ref": "resource_closure",
    }
    for receipt in db.execute("SELECT * FROM authority_run_receipts"):
        value = _stored_json(receipt["payload_json"], receipt["digest"])
        if value.get("run_id") != receipt["run_id"]:
            raise _error("STORAGE_CORRUPT")
        for field, kind in expected_kinds.items():
            ref = value.get(field)
            if (type(ref) is not dict or set(ref) != {"kind", "id", "digest"}
                    or ref["kind"] != kind or ref["id"] != receipt["run_id"]):
                raise _error("STORAGE_CORRUPT")
            artifact = db.execute(
                "SELECT run_id FROM authority_artifacts WHERE kind=? AND id=? AND digest=?",
                (ref["kind"], ref["id"], ref["digest"]),
            ).fetchone()
            if artifact is None or artifact["run_id"] != receipt["run_id"]:
                raise _error("STORAGE_CORRUPT")

    # 現在行は不変履歴の同一世代と完全一致していなければならない。
    for row in db.execute("SELECT * FROM eval_current"):
        history = db.execute(
            "SELECT * FROM eval_adoptions WHERE series_id=? AND generation=?",
            (row["series_id"], row["generation"]),
        ).fetchone()
        if history is None or tuple(row) != tuple(history):
            raise _error("STORAGE_CORRUPT")
    for row in db.execute("SELECT * FROM eval_adoptions"):
        current = db.execute(
            "SELECT * FROM eval_current WHERE series_id=?",
            (row["series_id"],),
        ).fetchone()
        if current is None:
            raise _error("STORAGE_CORRUPT")
        if adopted and row["generation"] == 1 and current["generation"] == 2:
            # 既知gen2採択版だけは旧gen1履歴を保持する。両世代のproposal/
            # validationとgen2の専用採択鎖を前後の検査で照合する。
            continue
        if tuple(row) != tuple(current):
            raise _error("STORAGE_CORRUPT")
    for row in db.execute("SELECT run_id FROM run_state"):
        if db.execute("SELECT 1 FROM bound_runs WHERE run_id=?", (row[0],)).fetchone() is None:
            raise _error("STORAGE_CORRUPT")
    for table in ("attempts", "attempt_events", "evidence_events", "aggregates", "decisions", "terminals"):
        for row in db.execute('SELECT run_id FROM "' + table + '"'):
            if db.execute("SELECT 1 FROM bound_runs WHERE run_id=?", (row[0],)).fetchone() is None:
                raise _error("STORAGE_CORRUPT")


def _verify_v4_candidate_rows(db: sqlite3.Connection, *, adopted: bool = False) -> None:
    """旧v4候補を元factoryへ再結合し、fresh失効とは分離して検査する。"""
    from . import transition_authority
    from .adoption import AdoptionError

    from . import transition_acceptance
    for table in ("eval_current", "eval_adoptions"):
        for row in db.execute('SELECT * FROM "' + table + '"'):
            if row["generation"] == 2 and adopted:
                try:
                    transition_acceptance.history(db, row, _stored_json(row["payload_json"], row["digest"]))
                except (AdoptionError, ContractError):
                    raise _error("STORAGE_CORRUPT") from None
            elif row["generation"] != 1:
                raise _error("STORAGE_CORRUPT")
    clock = _meta(db, _V4_VERSION)["last_clock"]
    for candidate in db.execute("SELECT * FROM transition_candidates"):
        try:
            _, value = transition_authority.load_candidate(db, candidate["candidate_id"], clock)
            for side in ("old", "new"):
                bound = value["runs"][side]["bound_run"]
                manifest = bound["manifest"]
                run_id = manifest["run_id"]
                run_row = db.execute("SELECT * FROM eval_runs WHERE run_id=?", (run_id,)).fetchone()
                resource = db.execute("SELECT * FROM resource_runs WHERE run_id=?", (run_id,)).fetchone()
                if run_row is None:
                    if resource is not None:
                        raise _error("STORAGE_CORRUPT")
                    continue
                transition_authority.candidate_run(db, run_row, clock)
                if (resource is None or resource["manifest_digest"] != run_row["manifest_digest"]
                        or resource["profile"] != manifest["profile"]
                        or resource["deadline"] != manifest["deadline"]
                        or _stored_json(resource["policy_json"], resource["policy_digest"]) != bound["policy"]):
                    raise _error("STORAGE_CORRUPT")
        except (AdoptionError, KeyError, TypeError, ValueError, OSError):
            raise _error("STORAGE_CORRUPT") from None
    for mapping in db.execute("SELECT candidate_id FROM transition_runs"):
        if db.execute("SELECT 1 FROM transition_candidates WHERE candidate_id=?", (mapping[0],)).fetchone() is None:
            raise _error("STORAGE_CORRUPT")
    # どちらの旧v4にも通常gen2 runは存在しない。新機能の保存行を旧版へ偽装しない。
    for row in db.execute("SELECT run_id FROM eval_runs WHERE contract_generation=2"):
        if db.execute("SELECT 1 FROM transition_runs WHERE run_id=?", (row["run_id"],)).fetchone() is None:
            raise _error("STORAGE_CORRUPT")

def _seed_policy_adoptions(db: sqlite3.Connection, table_columns: dict[str, set[str]]) -> None:
    """currentの既存行を不変履歴へ移し、既存履歴との不一致は拒否する。"""
    if "policy_adoptions" not in table_columns:
        return
    if table_columns["policy_adoptions"] != _POLICY_ADOPTION_COLUMNS:
        raise _error("UNSUPPORTED_STORE")
    current = [tuple(row) for row in db.execute(
        "SELECT series_id,generation,proposal_id,validation_id,policy_json,policy_digest,"
        "adopted_at,actor_id,context FROM current_profiles ORDER BY series_id")]
    history = [tuple(row) for row in db.execute(
        "SELECT series_id,generation,proposal_id,validation_id,policy_json,policy_digest,"
        "adopted_at,actor_id,context FROM policy_adoptions ORDER BY series_id")]
    if history and history != current:
        raise _error("STORAGE_CORRUPT")
    if not history and current:
        db.executemany(
            "INSERT INTO policy_adoptions(series_id,generation,proposal_id,validation_id,policy_json,"
            "policy_digest,adopted_at,actor_id,context) VALUES(?,?,?,?,?,?,?,?,?)",
            current,
        )
    for row in db.execute("SELECT proposal_id,proposal_digest FROM eval_validations"):
        proposal = db.execute("SELECT digest FROM eval_proposals WHERE id=?", (row[0],)).fetchone()
        if proposal is None or proposal[0] != row[1]:
            raise _error("STORAGE_CORRUPT")

    for row in db.execute("SELECT * FROM current_profiles"):
        proposal = db.execute("SELECT * FROM proposals WHERE proposal_id=?", (row["proposal_id"],)).fetchone()
        validation = db.execute("SELECT * FROM validations WHERE validation_id=?", (row["validation_id"],)).fetchone()
        if (proposal is None or validation is None
                or proposal["series_id"] != row["series_id"]
                or proposal["policy_digest"] != row["policy_digest"]
                or validation["proposal_id"] != row["proposal_id"]
                or validation["proposal_digest"] != row["policy_digest"]):
            raise _error("STORAGE_CORRUPT")

    for row in db.execute("SELECT * FROM eval_current"):
        proposal = db.execute("SELECT * FROM eval_proposals WHERE id=?", (row["proposal_id"],)).fetchone()
        validation = db.execute("SELECT * FROM eval_validations WHERE id=?", (row["validation_id"],)).fetchone()
        if (proposal is None or validation is None
                or proposal["series_id"] != row["series_id"]
                or proposal["generation"] != row["generation"]
                or validation["proposal_id"] != row["proposal_id"]
                or validation["proposal_digest"] != proposal["digest"]):
            raise _error("STORAGE_CORRUPT")

    for row in db.execute("SELECT run_id FROM resource_operations"):
        if db.execute("SELECT 1 FROM resource_runs WHERE run_id=?", (row[0],)).fetchone() is None:
            raise _error("STORAGE_CORRUPT")
    for row in db.execute("SELECT operation_id,run_id FROM resource_bindings"):
        operation = db.execute("SELECT run_id FROM resource_operations WHERE operation_id=?", (row[0],)).fetchone()
        if operation is None or operation[0] != row[1]:
            raise _error("STORAGE_CORRUPT")
    for row in db.execute("SELECT operation_id FROM resource_events"):
        if db.execute("SELECT 1 FROM resource_operations WHERE operation_id=?", (row[0],)).fetchone() is None:
            raise _error("STORAGE_CORRUPT")


def _trusted_extension() -> Any:
    try:
        from .evaluation_authority import EvaluationExtension
        extension = EvaluationExtension()
        schema_version = getattr(extension, "schema_version", None)
        migrate_schema = getattr(extension, "migrate_schema", None)
    except (ImportError, AttributeError, TypeError):
        raise _error("MIGRATION_UNAVAILABLE") from None
    if schema_version != _V4_VERSION or not callable(migrate_schema):
        raise _error("MIGRATION_UNAVAILABLE")
    return extension


def _config(db: sqlite3.Connection) -> dict[str, Any]:
    return dict(db.execute("SELECT key,value FROM adoption_config"))


def _meta(db: sqlite3.Connection, version: int) -> dict[str, Any]:
    value = dict(db.execute("SELECT key,value FROM adoption_meta"))
    if (set(value) != {"schema_version", "last_clock", "permission_generation"}
            or value["schema_version"] != version
            or type(value["last_clock"]) is not int
            or not -1 <= value["last_clock"] <= MAX_INTEGER
            or type(value["permission_generation"]) is not int
            or not 0 <= value["permission_generation"] <= MAX_INTEGER):
        raise _error("STORAGE_CORRUPT")
    return value


def _set_v4(db: sqlite3.Connection, extension: Any, base_columns: dict[str, set[str]]) -> None:
    """遷移表を追加し、版と現行extension digestを最後に更新する。"""

    try:
        transition_migrations.create_schema(db)
    except MigrationError:
        raise
    except Exception:
        raise _error("MIGRATION_FAILED") from None
    _verify_columns(db, {**base_columns, **transition_migrations.TABLES})
    db.execute("UPDATE adoption_meta SET value=? WHERE key='schema_version'", (_V4_VERSION,))
    db.execute("UPDATE adoption_config SET value=? WHERE key='extension_digest'", (extension.digest,))
    db.execute("PRAGMA user_version=4")


def migrate_evaluation_store(path: str | Path) -> dict[str, Any]:
    """既知のv2/v3をv4へ、既知の旧v4を現行v4へ明示移行する。"""

    if not isinstance(path, (str, Path)):
        raise _error("INVALID_PATH")
    target = Path(path)
    if not target.is_file():
        raise _error("STORE_MISSING")
    extension = _trusted_extension()
    try:
        from .policy import initial_policy_profile
        if _digest(initial_policy_profile()) != _V2_BOOTSTRAP_DIGEST:
            raise _error("BOOTSTRAP_MISMATCH")
    except MigrationError:
        raise
    except (TypeError, ValueError, OSError, UnicodeError, RecursionError):
        raise _error("BOOTSTRAP_MISMATCH") from None

    try:
        db = sqlite3.connect(str(target), isolation_level=None, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("BEGIN IMMEDIATE")
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version not in {_V2_VERSION, _V3_VERSION, _V4_VERSION}:
            raise _error("UNSUPPORTED_STORE")
        if version == _V4_VERSION and _config(db).get("extension_digest") == extension.digest:
            raise _error("UNSUPPORTED_STORE")
        if version == _V2_VERSION:
            _verify_columns(db, _V2_COLUMNS)
            config = _config(db)
            if (set(config) != {"bootstrap_digest", "validator_digest", "extension_digest"}
                    or config["bootstrap_digest"] != _V2_BOOTSTRAP_DIGEST
                    or config["validator_digest"] != _V2_VALIDATOR_DIGEST
                    or config["extension_digest"] != _V2_EXTENSION_DIGEST):
                raise _error("CONFIG_MISMATCH")
            _meta(db, _V2_VERSION)
            _verify_json_rows(db)
            # v2の既存移行処理だけを実行し、v3構造を確認してからv4表を追加する。
            try:
                extension.migrate_schema(db)
            except MigrationError:
                raise
            except Exception:
                raise _error("MIGRATION_FAILED") from None
            legacy_tables = dict(_V2_COLUMNS)
            for table, columns in getattr(extension, "tables", {}).items():
                if table not in transition_migrations.TABLES:
                    legacy_tables[table] = set(columns)
            if set(legacy_tables) == _V2_TABLES:
                raise _error("MIGRATION_SCHEMA_EMPTY")
            _verify_columns(db, legacy_tables)
            _seed_policy_adoptions(db, legacy_tables)
            if set(legacy_tables) == _V3_TABLES:
                _verify_v3_json_rows(db)
            _set_v4(db, extension, legacy_tables)
            predecessor = _V2_EXTENSION_DIGEST
        elif version == _V3_VERSION:
            _verify_columns(db, _V3_COLUMNS)
            config = _config(db)
            if (set(config) != {"bootstrap_digest", "validator_digest", "extension_digest"}
                    or config["bootstrap_digest"] != _V2_BOOTSTRAP_DIGEST
                    or config["validator_digest"] != _V2_VALIDATOR_DIGEST
                    or config["extension_digest"] != _V3_EXTENSION_DIGEST):
                raise _error("CONFIG_MISMATCH")
            _meta(db, _V3_VERSION)
            _verify_v3_json_rows(db)
            _set_v4(db, extension, _V3_COLUMNS)
            predecessor = _V3_EXTENSION_DIGEST
        else:
            # v4の構造は変えず、既知の前工程digestから現行digestへ明示的に
            # 更新する。候補の保存束縛と既存履歴は更新せず、同一transactionで
            # 検査とconfig更新を完了する。
            _verify_columns(db, _V4_COLUMNS)
            config = _config(db)
            if (set(config) != {"bootstrap_digest", "validator_digest", "extension_digest"}
                    or config["bootstrap_digest"] != _V2_BOOTSTRAP_DIGEST
                    or config["validator_digest"] != _V2_VALIDATOR_DIGEST
                    or config["extension_digest"] not in {_V4_PREDECESSOR_EXTENSION_DIGEST, _V4_ADOPTION_EXTENSION_DIGEST}):
                raise _error("CONFIG_MISMATCH")
            _meta(db, _V4_VERSION)
            adopted = config["extension_digest"] == _V4_ADOPTION_EXTENSION_DIGEST
            _verify_v3_json_rows(db, adopted=adopted)
            _verify_v4_candidate_rows(db, adopted=adopted)
            db.execute("UPDATE adoption_config SET value=? WHERE key='extension_digest'", (extension.digest,))
            predecessor = config["extension_digest"]
            db.commit()
            return {"schema_version": 4, "kind": "adoption_migration_result", "changed": True,
                    "predecessor_extension_digest": predecessor,
                    "predecessor_validator_digest": _V2_VALIDATOR_DIGEST,
                    "extension_digest": extension.digest, "ci_eligible": False}
        db.commit()
        return {"schema_version": 4, "kind": "adoption_migration_result", "changed": True,
                "predecessor_extension_digest": predecessor,
                "predecessor_validator_digest": _V2_VALIDATOR_DIGEST,
                "extension_digest": extension.digest, "ci_eligible": False}
    except MigrationError:
        try:
            db.rollback()
        except (NameError, sqlite3.Error):
            pass
        raise
    except (sqlite3.Error, OSError):
        try:
            db.rollback()
        except (NameError, sqlite3.Error):
            pass
        raise _error("MIGRATION_FAILED") from None
    finally:
        try:
            db.close()
        except (NameError, sqlite3.Error):
            pass


__all__ = ["MigrationError", "migrate_evaluation_store"]
