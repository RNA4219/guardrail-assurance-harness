"""実行束縛、Attempt、集計、診断最終化を不変保存する独立store。

この部品はOS認証、資源台帳、Artifact/Evidenceの実体照合を代行しない。
したがって、返却する診断receiptは常にci_eligible=Falseであり、上位の管理境界が
現在状態を再照合するまで通常CIの成功へ読み替えられない。
"""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import hashlib
from collections import OrderedDict
from threading import RLock
import json
from pathlib import Path
import sqlite3
from .sqlite_limits import connect_sqlite
import time
from typing import Any, Callable, Iterator, Mapping

from . import aggregation, budget_warning, decision, execution_profiles
from .contracts import ContractError, MAX_DOCUMENT_BYTES, MAX_INTEGER, require_digest, require_id
from .corpus import validate_case_set
from .policy import validate_policy_profile
from .registry import validate_registry
from .run_contracts import (
    bind_run_manifest,
    validate_evaluation_contract,
    validate_run_manifest,
    validate_trial_plan,
)


MAX_ATTEMPT_BYTES = 1 * 1024 * 1024
RETENTION_SECONDS = 90 * 86400
FRESHNESS_SECONDS = 86400
BASELINE_SECONDS = 30 * 86400
_MAX_ATTEMPTS = 20_000
_META_KEYS = {"schema_version", "last_clock", "generation"}
_TABLES = {
    "run_evidence_meta",
    "bound_runs",
    "run_state",
    "attempts",
    "attempt_events",
    "evidence_events",
    "aggregates",
    "decisions",
    "terminals",
}
_COLUMNS = {
    "run_evidence_meta": {"key", "value"},
    "bound_runs": {
        "run_id", "bundle_json", "bundle_digest", "profile_json", "profile_digest",
        "baseline_json", "baseline_digest", "started_at", "updated_at",
    },
    "run_state": {
        "run_id", "state", "hold_reason", "aggregate_digest", "decision_digest",
        "finalized_at", "evidence_state", "evidence_checked_at", "evidence_valid_until",
        "evidence_generation",
    },
    "attempts": {"attempt_id", "run_id", "attempt_json", "attempt_digest", "delivery_count"},
    "attempt_events": {"event_id", "run_id", "attempt_id", "event_kind", "received_digest", "created_at"},
    "evidence_events": {
        "event_id", "run_id", "event_kind", "state", "valid_until", "generation",
        "event_json", "event_digest", "created_at",
    },
    "aggregates": {"run_id", "aggregate_digest", "aggregate_json", "created_at"},
    "decisions": {"run_id", "decision_digest", "decision_json", "created_at"},
    "terminals": {"run_id", "terminal_json", "terminal_digest", "created_at"},
}
_STATES = {"OPEN", "HOLD", "FINALIZED"}
_EVIDENCE_STATES = {"UNKNOWN", "VALID", "REVOKED", "DELETED", "EXPIRED"}
_REASON_PRIORITY = {"HOLD": 0, "DEGRADED": 1, "UNKNOWN": 2, "WARNING": 3, "HEALTHY": 4}

# 共有DBを使う上位authorityが同じ構造検査を利用できるよう公開する。
TABLES = {table: set(columns) for table, columns in _COLUMNS.items()}
COLUMNS = {table: set(columns) for table, columns in _COLUMNS.items()}


class EvidenceError(ValueError):
    """入力値や保存状態を含めない固定エラー。"""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _error(code: str = "INVALID_EVIDENCE") -> EvidenceError:
    return EvidenceError(code)


def _walk_json(value: Any) -> bytes:
    pending = [(value, 0)]
    nodes = 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if depth > 16 or nodes > 100_000:
            raise _error("DOCUMENT_COMPLEXITY")
        if type(item) is dict:
            if any(type(key) is not str for key in item):
                raise _error()
            pending.extend((child, depth + 1) for child in item.values())
        elif type(item) is list:
            pending.extend((child, depth + 1) for child in item)
        elif type(item) is int:
            if not -MAX_INTEGER <= item <= MAX_INTEGER:
                raise _error("INTEGER_RANGE")
        elif item is not None and type(item) not in (str, bool):
            raise _error()
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise _error() from None
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise _error("DOCUMENT_SIZE")
    return raw


def _pack(value: Any) -> tuple[str, str]:
    raw = _walk_json(value)
    return raw.decode("utf-8"), hashlib.sha256(raw).hexdigest()


_LOAD_MARKERS = OrderedDict()
_LOAD_MARKER_LOCK = RLock()
_MAX_LOAD_MARKERS = 128


def _load(raw: Any, digest: Any) -> Any:
    if type(raw) is not str or type(digest) is not str:
        raise _error("STORAGE_CORRUPT")
    try:
        encoded = raw.encode("utf-8")
        if len(encoded) > MAX_DOCUMENT_BYTES or hashlib.sha256(encoded).hexdigest() != digest:
            raise _error("STORAGE_CORRUPT")
        # 全本文を毎回ハッシュし、既に厳格検査した同一本文だけを再利用する。
        # 本文や可変な結果は保持せず、呼出元へは毎回独立したJSONを返す。
        key = (digest, len(encoded), _walk_json, _unique_pairs, _reject_number, json.loads, json.dumps, MAX_INTEGER)
        with _LOAD_MARKER_LOCK:
            known = key in _LOAD_MARKERS
            if known:
                _LOAD_MARKERS.move_to_end(key)
        if known:
            return json.loads(raw)
        value = json.loads(raw, object_pairs_hook=_unique_pairs, parse_float=_reject_number, parse_constant=_reject_number)
        canonical = _walk_json(value)
    except EvidenceError:
        raise _error("STORAGE_CORRUPT") from None
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise _error("STORAGE_CORRUPT") from None
    if canonical != encoded:
        raise _error("STORAGE_CORRUPT")
    with _LOAD_MARKER_LOCK:
        _LOAD_MARKERS[key] = None
        _LOAD_MARKERS.move_to_end(key)
        while len(_LOAD_MARKERS) > _MAX_LOAD_MARKERS:
            _LOAD_MARKERS.popitem(last=False)
    return value


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate key")
        value[key] = item
    return value


def _reject_number(_: str) -> None:
    raise ValueError("non-integer number")


def _digest(value: Any) -> None:
    try:
        require_digest(value)
    except ContractError:
        raise _error("INVALID_DIGEST") from None


def _id(value: Any) -> None:
    try:
        require_id(value)
    except ContractError:
        raise _error("INVALID_ID") from None


def _ref_digest(value: Any) -> str:
    if type(value) is not dict or set(value) != {"kind", "id", "digest"}:
        raise _error("INVALID_REF")
    _id(value["kind"])
    _id(value["id"])
    _digest(value["digest"])
    return value["digest"]


def _profile(value: Any) -> dict[str, Any]:
    if type(value) is dict and "schema_version" in value:
        try:
            return execution_profiles.validate(value)
        except ContractError as error:
            raise _error("DUPLICATE_REFERENCE" if error.code == "DUPLICATE_REFERENCE" else "INVALID_PROFILE") from None
    fields = {"fixture_digest", "adapter_digests", "isolation_digest"}
    if type(value) is not dict or set(value) != fields:
        raise _error("INVALID_PROFILE")
    _digest(value["fixture_digest"])
    _digest(value["isolation_digest"])
    adapters = value["adapter_digests"]
    if type(adapters) is not list or not 1 <= len(adapters) <= 100:
        raise _error("INVALID_PROFILE")
    seen: set[str] = set()
    for item in adapters:
        _digest(item)
        if item in seen:
            raise _error("DUPLICATE_REFERENCE")
        seen.add(item)
    _pack(value)
    return deepcopy(value)


def _bound(value: Any, baseline_context: Any = None) -> dict[str, Any]:
    # 内容が完全に同じ大規模bundleだけを再利用する。保存状態や時刻を検査する関数ではない。
    from .cache_inputs import plain
    try:
        cases = value['case_set']['cases']
        eligible = type(cases) is list and 400 <= len(cases) <= 415
    except (KeyError, TypeError):
        eligible = False
    if eligible and plain([value, baseline_context]):
        try:
            payload = json.dumps([value, baseline_context], sort_keys=True, ensure_ascii=False,
                separators=(',', ':'), allow_nan=False)
        except (TypeError, ValueError, UnicodeError, RecursionError):
            payload = None
        if payload is not None and len(payload.encode('utf-8')) <= 2 * MAX_DOCUMENT_BYTES:
            from .evaluation_authority import _source_digest
            return json.loads(_cached_bound(_source_digest(), _bound_uncached, payload))
    return _bound_uncached(value, baseline_context)


from .immutable_cache import binding_cache


@binding_cache.memoize
def _cached_bound(source_digest, implementation, payload):
    value, baseline_context = json.loads(payload)
    from .cache_inputs import encode_result
    return encode_result(implementation(value, baseline_context))


@binding_cache.memoize
def _cached_bound_digest(source_digest, implementation, packer, walker, limits, payload):
    """検査済み完全入力からdigestだけを純粋に再利用する。"""
    value, baseline_context = json.loads(payload)
    rebound = implementation(value, baseline_context)
    # packerはmiss時に全bundleのdepth/node/sizeを再検査する。
    # walkerとlimitsはcache identityに含め、検査規則の変更で旧digestを再利用しない。
    return packer(rebound)[1]


def _bound_uncached(value: Any, baseline_context: Any = None) -> dict[str, Any]:
    fields = {"manifest", "contract", "plan", "policy", "registry", "case_set", "selected_controls", "ci_eligible"}
    if type(value) is not dict or set(value) != fields:
        raise _error("INVALID_BOUND_RUN")
    if value["ci_eligible"] is not False:
        raise _error("INVALID_BOUND_RUN")
    try:
        manifest = validate_run_manifest(value["manifest"])
        contract = validate_evaluation_contract(value["contract"])
        plan = validate_trial_plan(value["plan"])
        policy = validate_policy_profile(value["policy"])
        registry = validate_registry(value["registry"])
        case_set = validate_case_set(value["case_set"])
        rebound = bind_run_manifest(
            manifest, contract, plan, policy, registry, case_set,
            baseline_context=baseline_context,
        )
    except (ContractError, ValueError, TypeError, KeyError, RecursionError):
        raise _error("BOUND_RUN_MISMATCH") from None
    if rebound.get("ci_eligible") is not False:
        raise _error("BOUND_RUN_MISMATCH")
    return {
        "manifest": rebound["manifest"], "contract": rebound["contract"],
        "plan": rebound["plan"], "policy": rebound["policy"],
        "registry": rebound["registry"], "case_set": rebound["case_set"],
        "selected_controls": rebound["selected_controls"], "ci_eligible": False,
    }


def bound_bundle_digest(bound_run: Any, baseline_context: Any = None) -> str:
    """実際の比較対象を再束縛して、開始許可表のcanonical digestを返す。"""
    if type(bound_run) is dict and "_partitioned_context" in bound_run:
        return _pack(bound_storage_document(bound_run, baseline_context))[1]
    from .cache_inputs import plain
    try:
        cases = bound_run['case_set']['cases']
        eligible = type(cases) is list and 400 <= len(cases) <= 415
    except (KeyError, TypeError):
        eligible = False
    if eligible and plain([bound_run, baseline_context]):
        try:
            payload = json.dumps([bound_run, baseline_context], sort_keys=True,
                ensure_ascii=False, separators=(',', ':'), allow_nan=False)
        except (TypeError, ValueError, UnicodeError, RecursionError):
            payload = None
        if payload is not None:
            try:
                payload_size = len(payload.encode('utf-8'))
            except UnicodeError:
                payload_size = 2 * MAX_DOCUMENT_BYTES + 1
            if payload_size <= 2 * MAX_DOCUMENT_BYTES:
                from .evaluation_authority import _source_digest
                limits = f"{MAX_DOCUMENT_BYTES}:{MAX_INTEGER}"
                return _cached_bound_digest(
                    _source_digest(), _bound_uncached, _pack, _walk_json,
                    limits, payload,
                )
    return _pack(_bound(bound_run, baseline_context))[1]


def bound_storage_document(bound_run: Any, baseline_context: Any = None) -> dict[str, Any]:
    """保存用documentを返す。v2の大きい内部contextを単一文書にしない。"""
    if type(bound_run) is dict and "_partitioned_context" in bound_run:
        from .partitioned_run_contracts import validate_partitioned_runtime
        return validate_partitioned_runtime(bound_run, baseline_context)["_partitioned_receipt"]
    return _bound(bound_run, baseline_context)


def _binding_summary(bound: dict[str, Any], profile: dict[str, Any], baseline_context: Any) -> dict[str, Any]:
    manifest = bound["manifest"]
    scope = {
        "use_cases": manifest["use_cases"],
        "control_ids": manifest["control_ids"],
        "target_refs": manifest["target_refs"],
        "plan_ref": manifest["plan_ref"],
        "environment_ref": manifest["environment_ref"],
        "actor_context_ref": manifest["actor_context_ref"],
    }
    scope_raw = _walk_json(scope)
    baseline_digest = None if baseline_context is None else _pack(baseline_context)[1]
    return {
        "run_id": manifest["run_id"],
        "contract_digest": manifest["contract_ref"]["digest"],
        "policy_digest": manifest["policy_ref"]["digest"],
        "scope_digest": hashlib.sha256(scope_raw).hexdigest(),
        "profile_digest": _pack(profile)[1],
        "baseline_digest": baseline_digest,
    }


def _evidence_event(run_id: str, state: str, valid_until: int | None,
                    generation: int) -> tuple[dict[str, Any], str, str, str]:
    event = {
        "schema_version": 1, "kind": "evidence_state_event", "run_id": run_id,
        "state": state, "valid_until": valid_until, "revocation_generation": generation,
    }
    event_raw, event_digest = _pack(event)
    event_id = "evidence-" + hashlib.sha256((run_id + event_digest).encode("utf-8")).hexdigest()
    return event, event_raw, event_digest, event_id


def _normalize_allowed_bindings(allowed_bindings: Mapping[str, str] | None) -> dict[str, str]:
    if allowed_bindings is None or type(allowed_bindings) is not dict:
        return {}
    result: dict[str, str] = {}
    for run_id, digest in allowed_bindings.items():
        _id(run_id)
        _digest(digest)
        result[run_id] = digest
    return result


def _validate_schema(db: sqlite3.Connection) -> None:
    try:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not _TABLES.issubset(tables):
            raise _error("UNSUPPORTED_STORE")
        for table, columns in _COLUMNS.items():
            found = {row[1] for row in db.execute('PRAGMA table_info("' + table + '")')}
            if found != columns:
                raise _error("UNSUPPORTED_STORE")
        meta = {row[0]: row[1] for row in db.execute("SELECT key,value FROM run_evidence_meta")}
        if (set(meta) != _META_KEYS or meta["schema_version"] != 1
                or not -1 <= meta["last_clock"] <= MAX_INTEGER
                or not 0 <= meta["generation"] <= MAX_INTEGER):
            raise _error("STORAGE_CORRUPT")
    except EvidenceError:
        raise
    except (sqlite3.Error, KeyError, TypeError, ValueError):
        raise _error("STORAGE_CORRUPT") from None


def create_schema(db: sqlite3.Connection) -> None:
    """呼出側のtransaction内へrun evidence表を原子的に作成する。"""
    if not isinstance(db, sqlite3.Connection) or not db.in_transaction:
        raise _error("TRANSACTION_REQUIRED")
    try:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if tables.intersection(_TABLES):
            _validate_schema(db)
            return
        db.execute("CREATE TABLE run_evidence_meta(key TEXT PRIMARY KEY, value INTEGER NOT NULL)")
        db.executemany("INSERT INTO run_evidence_meta VALUES(?,?)", [("schema_version", 1), ("last_clock", -1), ("generation", 0)])
        db.execute("CREATE TABLE bound_runs(run_id TEXT PRIMARY KEY, bundle_json TEXT NOT NULL, bundle_digest TEXT NOT NULL, profile_json TEXT NOT NULL, profile_digest TEXT NOT NULL, baseline_json TEXT, baseline_digest TEXT, started_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)")
        db.execute("CREATE TABLE run_state(run_id TEXT PRIMARY KEY, state TEXT NOT NULL, hold_reason TEXT, aggregate_digest TEXT, decision_digest TEXT, finalized_at INTEGER, evidence_state TEXT NOT NULL, evidence_checked_at INTEGER, evidence_valid_until INTEGER, evidence_generation INTEGER, FOREIGN KEY(run_id) REFERENCES bound_runs(run_id))")
        db.execute("CREATE TABLE attempts(attempt_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, attempt_json TEXT NOT NULL, attempt_digest TEXT NOT NULL, delivery_count INTEGER NOT NULL, FOREIGN KEY(run_id) REFERENCES bound_runs(run_id))")
        db.execute("CREATE TABLE attempt_events(event_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, attempt_id TEXT NOT NULL, event_kind TEXT NOT NULL, received_digest TEXT NOT NULL, created_at INTEGER NOT NULL, FOREIGN KEY(run_id) REFERENCES bound_runs(run_id))")
        db.execute("CREATE TABLE evidence_events(event_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, event_kind TEXT NOT NULL, state TEXT NOT NULL, valid_until INTEGER, generation INTEGER NOT NULL, event_json TEXT NOT NULL, event_digest TEXT NOT NULL, created_at INTEGER NOT NULL, FOREIGN KEY(run_id) REFERENCES bound_runs(run_id))")
        db.execute("CREATE TABLE aggregates(run_id TEXT NOT NULL, aggregate_digest TEXT NOT NULL, aggregate_json TEXT NOT NULL, created_at INTEGER NOT NULL, PRIMARY KEY(run_id, aggregate_digest), FOREIGN KEY(run_id) REFERENCES bound_runs(run_id))")
        db.execute("CREATE TABLE decisions(run_id TEXT NOT NULL, decision_digest TEXT NOT NULL, decision_json TEXT NOT NULL, created_at INTEGER NOT NULL, PRIMARY KEY(run_id, decision_digest), FOREIGN KEY(run_id) REFERENCES bound_runs(run_id))")
        db.execute("CREATE TABLE terminals(run_id TEXT PRIMARY KEY, terminal_json TEXT NOT NULL, terminal_digest TEXT NOT NULL, created_at INTEGER NOT NULL, FOREIGN KEY(run_id) REFERENCES bound_runs(run_id))")
        _validate_schema(db)
    except EvidenceError:
        raise
    except sqlite3.Error:
        raise _error("STORAGE_FAILURE") from None


def _attempt(value: Any) -> tuple[dict[str, Any], str]:
    try:
        normalized, digest = aggregation._validate_attempt(value)
    except (ContractError, ValueError, TypeError, KeyError, RecursionError):
        raise _error("INVALID_ATTEMPT") from None
    try:
        raw = _walk_json(normalized)
    except EvidenceError:
        raise
    if len(raw) > MAX_ATTEMPT_BYTES:
        raise _error("DOCUMENT_SIZE")
    return normalized, digest


def _binding_matches(bound: dict[str, Any], profile: dict[str, Any], attempt: dict[str, Any]) -> None:
    binding = attempt["expected_binding"]
    manifest = bound["manifest"]
    try:
        if binding["run_id"] != manifest["run_id"]:
            raise _error("BINDING_MISMATCH")
        if binding["contract_digest"] != manifest["contract_ref"]["digest"]:
            raise _error("BINDING_MISMATCH")
        if binding["policy_digest"] != manifest["policy_ref"]["digest"]:
            raise _error("BINDING_MISMATCH")
        selected_profile = execution_profiles.expected(profile, binding["target_digest"], binding["evaluator_digest"])
        if binding["fixture_digest"] != selected_profile["fixture_digest"] or binding["isolation_digest"] != selected_profile["isolation_digest"]:
            raise _error("BINDING_MISMATCH")
        if binding["adapter_digest"] not in selected_profile["adapter_digests"]:
            raise _error("BINDING_MISMATCH")
        # 正規化結果にも同じbindingを保持させ、外側の自己申告だけを信頼しない。
        result = attempt.get("result")
        if result is not None and result["binding"] != binding:
            raise _error("BINDING_MISMATCH")
    except (KeyError, ContractError):
        raise _error("BINDING_MISMATCH") from None
    entries = bound["plan"]["entries"]
    matched = [
        item for item in entries
        if item["obligation_id"] == binding["obligation_id"]
        and item["case_id"] == binding["case_id"]
        and item["trial_id"] == binding["trial_id"]
        and item["variant"] == attempt["variant"]
        and binding["stage_id"] in item["stage_ids"]
        and item["target_ref"]["digest"] == binding["target_digest"]
        and item["evaluator_ref"]["digest"] == binding["evaluator_digest"]
    ]
    if len(matched) != 1:
        raise _error("BINDING_MISMATCH")


class RunEvidenceStore:
    """bound runと正規化済み観測を不変に保存する。"""

    def __init__(self, path: str | Path, *, clock: Callable[[], int] | None = None,
                 allowed_bindings: Mapping[str, str] | None = None):
        self._clock = clock or (lambda: int(time.time()))
        self._allowed = _normalize_allowed_bindings(allowed_bindings)
        self._db: sqlite3.Connection | None = None
        try:
            db = connect_sqlite(str(Path(path)), isolation_level=None, timeout=5)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            self._db = db
            with self._transaction() as connection:
                tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not tables:
                    create_schema(connection)
                elif tables.intersection(_TABLES):
                    _validate_schema(connection)
                else:
                    raise _error("UNSUPPORTED_STORE")
        except BaseException as error:
            self.close()
            if isinstance(error, (sqlite3.Error, OSError)):
                raise _error("STORAGE_FAILURE") from None
            raise

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        if self._db is None:
            raise _error("STORE_CLOSED")
        try:
            self._db.execute("BEGIN IMMEDIATE")
            yield self._db
            self._db.commit()
        except BaseException as error:
            try:
                self._db.rollback()
            except sqlite3.Error:
                pass
            if isinstance(error, sqlite3.Error):
                raise _error("STORAGE_FAILURE") from None
            raise

    def close(self) -> None:
        if self._db is not None:
            self._db.close()
            self._db = None

    def __enter__(self) -> "RunEvidenceStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _now(self, db: sqlite3.Connection) -> int:
        try:
            value = self._clock()
        except Exception:
            raise _error("CLOCK_UNAVAILABLE") from None
        if type(value) is not int or not 0 <= value <= MAX_INTEGER:
            raise _error("CLOCK_UNAVAILABLE")
        row = db.execute("SELECT value FROM run_evidence_meta WHERE key='last_clock'").fetchone()
        if row is None or type(row[0]) is not int or not -1 <= row[0] <= MAX_INTEGER:
            raise _error("STORAGE_CORRUPT")
        if value < row[0]:
            raise _error("CLOCK_ROLLBACK")
        if value > row[0]:
            db.execute("UPDATE run_evidence_meta SET value=? WHERE key='last_clock'", (value,))
        return value

    @staticmethod
    def _run_row(db: sqlite3.Connection, run_id: str) -> sqlite3.Row:
        row = db.execute("SELECT * FROM bound_runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            raise _error("RUN_NOT_FOUND")
        return row

    @staticmethod
    def _load_run(row: sqlite3.Row) -> tuple[dict[str, Any], dict[str, Any], Any]:
        bound = _load(row["bundle_json"], row["bundle_digest"])
        profile = _load(row["profile_json"], row["profile_digest"])
        baseline = None if row["baseline_json"] is None else _load(row["baseline_json"], row["baseline_digest"])
        # The default v1 store never interprets the separate v2 receipt schema.
        if type(bound) is not dict or "kind" in bound or "schema_version" in bound:
            raise _error("STORAGE_CORRUPT")
        return bound, profile, baseline

    def _resolve_run_context(self, db: sqlite3.Connection, row: sqlite3.Row,
                             now: int) -> tuple[dict[str, Any], dict[str, Any], Any]:
        """Return context for one operation; specialized books may resolve it freshly."""
        return self._load_run(row)

    def _binding_summary(self, bound: dict[str, Any], profile: dict[str, Any],
                         baseline: Any) -> dict[str, Any]:
        return _binding_summary(bound, profile, baseline)

    def _aggregate_value(self, bound: dict[str, Any], attempts: list[dict[str, Any]],
                         profile: dict[str, Any], baseline: Any) -> dict[str, Any]:
        return aggregation.aggregate(bound, attempts, execution_profile=profile,
                                     baseline_context=baseline)

    def _aggregate_kind(self) -> str:
        return "aggregation"

    def _check_context(self, db: sqlite3.Connection, row: sqlite3.Row, now: int,
                       bound: dict[str, Any], profile: dict[str, Any], baseline: Any) -> None:
        execution_profiles.check_plan(profile, bound)
        if _bound(bound, baseline) != bound or _profile(profile) != profile:
            raise _error("STORAGE_CORRUPT")

    @staticmethod
    def _state(db: sqlite3.Connection, run_id: str) -> sqlite3.Row:
        row = db.execute("SELECT * FROM run_state WHERE run_id=?", (run_id,)).fetchone()
        if row is None or row["state"] not in _STATES or row["evidence_state"] not in _EVIDENCE_STATES:
            raise _error("STORAGE_CORRUPT")
        for field, lower in (("evidence_checked_at", 0), ("evidence_valid_until", 0), ("evidence_generation", 0)):
            value = row[field]
            if value is not None and (type(value) is not int or not lower <= value <= MAX_INTEGER):
                raise _error("STORAGE_CORRUPT")
        return row

    def _run_view(self, db: sqlite3.Connection, run_id: str, now: int) -> dict[str, Any]:
        row = self._run_row(db, run_id)
        bound, profile, baseline = self._resolve_run_context(db, row, now)
        state = self._state(db, run_id)
        return {
            "schema_version": 1, "kind": "run_evidence_run", "run_id": run_id,
            # _load_runでこの呼出し専用に復号した値を返す。二度目の複製は不要。
            "bundle": bound, "bundle_digest": row["bundle_digest"],
            "execution_profile": profile, "baseline_context": baseline,
            "state": state["state"], "hold_reason": state["hold_reason"],
            "aggregate_digest": state["aggregate_digest"], "decision_digest": state["decision_digest"],
            "diagnostic_finalized": state["finalized_at"] is not None,
            "diagnostic_finalized_at": state["finalized_at"],
            "authority_connected": False, "resource_closure_verified": False,
            "baseline_freshness_verified": False, "adoption_verified": False,
            "ci_eligible": False,
        }

    def _validate_store_contents(self, db: sqlite3.Connection, run_id: str, now: int,
                                 row: sqlite3.Row, bound: dict[str, Any],
                                 profile: dict[str, Any], baseline: Any,
                                 state: sqlite3.Row) -> None:
        """現在利用判定の前に、run内の保存payloadと参照を再検査する。"""
        try:
            self._check_context(db, row, now, bound, profile, baseline)
            if row["run_id"] != run_id or type(row["started_at"]) is not int or type(row["updated_at"]) is not int:
                raise _error("STORAGE_CORRUPT")
            for value in (row["started_at"], row["updated_at"]):
                if not 0 <= value <= MAX_INTEGER or value > now:
                    raise _error("STORAGE_CORRUPT")
            for value in (state["finalized_at"], state["evidence_checked_at"]):
                if value is not None and (type(value) is not int or not 0 <= value <= MAX_INTEGER or value > now):
                    raise _error("STORAGE_CORRUPT")
            if (type(state["evidence_generation"]) is not int
                    or not 0 <= state["evidence_generation"] <= MAX_INTEGER):
                raise _error("STORAGE_CORRUPT")
            if (state["evidence_valid_until"] is not None
                    and (type(state["evidence_valid_until"]) is not int
                         or not 0 <= state["evidence_valid_until"] <= MAX_INTEGER)):
                raise _error("STORAGE_CORRUPT")
            if state["hold_reason"] is not None and type(state["hold_reason"]) is not str:
                raise _error("STORAGE_CORRUPT")
            for field in ("aggregate_digest", "decision_digest"):
                if state[field] is not None:
                    _digest(state[field])

            attempts: dict[str, dict[str, Any]] = {}
            for item in db.execute("SELECT * FROM attempts WHERE run_id=? ORDER BY attempt_id", (run_id,)):
                if item["run_id"] != run_id or item["attempt_id"] in attempts:
                    raise _error("STORAGE_CORRUPT")
                if type(item["delivery_count"]) is not int or item["delivery_count"] < 1:
                    raise _error("STORAGE_CORRUPT")
                attempt = _load(item["attempt_json"], item["attempt_digest"])
                if attempt.get("attempt_id") != item["attempt_id"]:
                    raise _error("STORAGE_CORRUPT")
                normalized, digest = _attempt(attempt)
                if normalized != attempt or digest != item["attempt_digest"]:
                    raise _error("STORAGE_CORRUPT")
                _binding_matches(bound, profile, attempt)
                for value in (attempt["started_at"], attempt["finished_at"]):
                    if value is not None and value > now:
                        raise _error("STORAGE_CORRUPT")
                attempts[item["attempt_id"]] = attempt

            event_kinds = {"ATTEMPT_CONFLICT", "LATE_ATTEMPT", "FUTURE_ATTEMPT"}
            for item in db.execute("SELECT * FROM attempt_events WHERE run_id=? ORDER BY event_id", (run_id,)):
                if item["run_id"] != run_id or item["event_kind"] not in event_kinds:
                    raise _error("STORAGE_CORRUPT")
                _id(item["event_id"])
                _id(item["attempt_id"])
                _digest(item["received_digest"])
                if type(item["created_at"]) is not int or not 0 <= item["created_at"] <= now:
                    raise _error("STORAGE_CORRUPT")
                prefix = {"ATTEMPT_CONFLICT": "conflict-", "LATE_ATTEMPT": "late-", "FUTURE_ATTEMPT": "future-"}[item["event_kind"]]
                if item["event_id"] != prefix + hashlib.sha256((item["attempt_id"] + item["received_digest"]).encode("utf-8")).hexdigest():
                    raise _error("STORAGE_CORRUPT")
                if item["event_kind"] == "ATTEMPT_CONFLICT" and item["attempt_id"] not in attempts:
                    raise _error("STORAGE_CORRUPT")

            evidence_rows = list(db.execute("SELECT * FROM evidence_events WHERE run_id=? ORDER BY created_at,event_id", (run_id,)))
            valid_times: list[int] = []
            valid_deadlines: list[int] = []
            for item in evidence_rows:
                if item["run_id"] != run_id or item["state"] not in _EVIDENCE_STATES:
                    raise _error("STORAGE_CORRUPT")
                _id(item["event_id"])
                event = _load(item["event_json"], item["event_digest"])
                expected, _, digest, event_id = _evidence_event(
                    run_id, item["state"], item["valid_until"], item["generation"]
                )
                if (event != expected or item["event_digest"] != digest or item["event_id"] != event_id
                        or item["event_kind"] != "EVIDENCE_" + item["state"]
                        or type(item["generation"]) is not int or not 0 <= item["generation"] <= MAX_INTEGER
                        or (item["valid_until"] is not None and (type(item["valid_until"]) is not int or not 0 <= item["valid_until"] <= MAX_INTEGER))
                        or type(item["created_at"]) is not int or not 0 <= item["created_at"] <= now):
                    raise _error("STORAGE_CORRUPT")
                if item["state"] == "VALID":
                    valid_times.append(item["created_at"])
                    if item["valid_until"] is not None:
                        valid_deadlines.append(item["valid_until"])
            if state["evidence_state"] == "UNKNOWN":
                if evidence_rows:
                    raise _error("STORAGE_CORRUPT")
            else:
                current_event = _evidence_event(
                    run_id, state["evidence_state"], state["evidence_valid_until"], state["evidence_generation"]
                )
                current_row = db.execute("SELECT * FROM evidence_events WHERE event_id=?", (current_event[3],)).fetchone()
                if current_row is None or current_row["created_at"] > now:
                    raise _error("STORAGE_CORRUPT")
                if state["evidence_state"] == "VALID":
                    if not valid_times or state["evidence_checked_at"] != min(valid_times):
                        raise _error("STORAGE_CORRUPT")
                    if valid_deadlines and state["evidence_valid_until"] != min(valid_deadlines):
                        raise _error("STORAGE_CORRUPT")
                elif state["evidence_checked_at"] != current_row["created_at"]:
                    raise _error("STORAGE_CORRUPT")

            aggregate_rows = list(db.execute("SELECT * FROM aggregates WHERE run_id=? ORDER BY aggregate_digest", (run_id,)))
            current_aggregate_row = None
            current_aggregate_value = None
            if state["aggregate_digest"] is not None:
                current_aggregate_row = db.execute(
                    "SELECT * FROM aggregates WHERE run_id=? AND aggregate_digest=?",
                    (run_id, state["aggregate_digest"]),
                ).fetchone()
                if current_aggregate_row is None:
                    raise _error("STORAGE_CORRUPT")
                current_aggregate_value = _load(
                    current_aggregate_row["aggregate_json"], current_aggregate_row["aggregate_digest"]
                )
            expected_aggregate = self._aggregate_value(bound, list(attempts.values()), profile, baseline)
            observed = [a["finished_at"] if a["finished_at"] is not None else a["started_at"] for a in attempts.values()]
            expected_aggregate["observed_at"] = min(observed) if observed else row["started_at"]
            expected_aggregate_digest = _pack(expected_aggregate)[1]
            aggregate_by_digest = {}
            for item in aggregate_rows:
                aggregate_value = _load(item["aggregate_json"], item["aggregate_digest"])
                if (aggregate_value.get("kind") != self._aggregate_kind() or aggregate_value.get("run_id") != run_id
                        or aggregate_value.get("contract_digest") != bound["manifest"]["contract_ref"]["digest"]
                        or aggregate_value.get("execution_profile") != profile
                        or aggregate_value.get("ci_eligible") is not False
                        or type(aggregate_value.get("observed_at")) is not int
                        or aggregate_value["observed_at"] > now
                        or type(item["created_at"]) is not int or not 0 <= item["created_at"] <= now):
                    raise _error("STORAGE_CORRUPT")
                if item["aggregate_digest"] == state["aggregate_digest"] and aggregate_value != expected_aggregate:
                    raise _error("STORAGE_CORRUPT")
                aggregate_by_digest[item["aggregate_digest"]] = aggregate_value
            if state["aggregate_digest"] is not None and not aggregate_rows:
                raise _error("STORAGE_CORRUPT")
            if state["aggregate_digest"] is not None and state["aggregate_digest"] != expected_aggregate_digest:
                raise _error("STORAGE_CORRUPT")

            decision_rows = list(db.execute("SELECT * FROM decisions WHERE run_id=? ORDER BY decision_digest", (run_id,)))
            decision_digests = set()
            aggregate_digests = {item["aggregate_digest"] for item in aggregate_rows}
            for item in decision_rows:
                value = _load(item["decision_json"], item["decision_digest"])
                if (value.get("kind") != "run_decision" or value.get("run_id") != run_id
                        or value.get("ci_eligible") is not False or value.get("aggregate_digest") not in aggregate_digests
                        or type(item["created_at"]) is not int or not 0 <= item["created_at"] <= now):
                    raise _error("STORAGE_CORRUPT")
                if type(value.get("assessed_at")) is not int or value["assessed_at"] != item["created_at"]:
                    raise _error("STORAGE_CORRUPT")
                aggregate_value = aggregate_by_digest.get(value["aggregate_digest"])
                if aggregate_value is None:
                    raise _error("STORAGE_CORRUPT")
                decision_input = deepcopy(aggregate_value)
                decision_input["aggregate_digest"] = value["aggregate_digest"]
                if self._decision_from_aggregate(bound, decision_input, value["assessed_at"],
                        budget_warning_basis=value.get("budget_warning")) != value:
                    raise _error("STORAGE_CORRUPT")
                decision_digests.add(item["decision_digest"])
            if state["decision_digest"] is not None and not decision_rows:
                raise _error("STORAGE_CORRUPT")
            if state["decision_digest"] is not None and state["decision_digest"] not in decision_digests:
                raise _error("STORAGE_CORRUPT")

            terminal = db.execute("SELECT * FROM terminals WHERE run_id=?", (run_id,)).fetchone()
            if terminal is None:
                if state["finalized_at"] is not None:
                    raise _error("STORAGE_CORRUPT")
            else:
                value = _load(terminal["terminal_json"], terminal["terminal_digest"])
                binding = self._binding_summary(bound, profile, baseline)
                decision_digest = value.get("decision_digest")
                if (value.get("kind") != "run_evidence_finalization" or value.get("run_id") != run_id
                        or value.get("binding") != binding or value.get("state") not in {"HOLD", "FINALIZED"}
                        or decision_digest not in decision_digests
                        or value.get("decision") != _load(
                            db.execute("SELECT decision_json FROM decisions WHERE run_id=? AND decision_digest=?", (run_id, decision_digest)).fetchone()[0], decision_digest
                        )
                        or value.get("diagnostic_finalized") is not True
                        or value.get("authority_connected") is not False
                        or value.get("resource_closure_verified") is not False
                        or value.get("ci_eligible") is not False
                        or state["finalized_at"] is None
                        or type(terminal["created_at"]) is not int or not 0 <= terminal["created_at"] <= now):
                    raise _error("STORAGE_CORRUPT")
        except EvidenceError:
            raise _error("STORAGE_CORRUPT") from None
        except (ContractError, TypeError, ValueError, KeyError, IndexError, sqlite3.Error, RecursionError):
            raise _error("STORAGE_CORRUPT") from None

    def start_run(self, bound_run: Any, execution_profile: Any, baseline_context: Any = None) -> dict[str, Any]:
        profile = _profile(execution_profile)
        bound = _bound(bound_run, baseline_context)
        try:
            execution_profiles.check_plan(profile, bound)
        except ContractError:
            raise _error("INVALID_PROFILE") from None
        run_id = bound["manifest"]["run_id"]
        _, bundle_digest = _pack(bound)
        if self._allowed.get(run_id) != bundle_digest:
            raise _error("BINDING_NOT_ADMITTED")
        profile_raw, profile_digest = _pack(profile)
        baseline_raw = baseline_digest = None
        if baseline_context is not None:
            baseline_raw, baseline_digest = _pack(baseline_context)
        bundle_raw, bundle_digest = _pack(bound)
        with self._transaction() as db:
            now = self._now(db)
            existing = db.execute("SELECT * FROM bound_runs WHERE run_id=?", (run_id,)).fetchone()
            if existing is not None:
                if (existing["bundle_digest"] != bundle_digest or existing["profile_digest"] != profile_digest
                        or existing["baseline_digest"] != baseline_digest):
                    raise _error("RUN_CONFLICT")
                view = self._run_view(db, run_id, now)
                view["binding"] = self._binding_summary(bound, profile, baseline_context)
                return view
            db.execute("INSERT INTO bound_runs VALUES(?,?,?,?,?,?,?,?,?)", (run_id, bundle_raw, bundle_digest, profile_raw, profile_digest, baseline_raw, baseline_digest, now, now))
            db.execute("INSERT INTO run_state VALUES(?,?,?,?,?,?,?,?,?,?)", (run_id, "OPEN", None, None, None, None, "UNKNOWN", now, None, 0))
            view = self._run_view(db, run_id, now)
            view["binding"] = self._binding_summary(bound, profile, baseline_context)
            return view

    def get_run(self, run_id: str) -> dict[str, Any]:
        _id(run_id)
        with self._transaction() as db:
            now = self._now(db)
            view = self._run_view(db, run_id, now)
            view["binding"] = self._binding_summary(view["bundle"], view["execution_profile"], view["baseline_context"])
            return view

    def record_evidence_state(self, run_id: str, evidence_state: Any) -> dict[str, Any]:
        """上位Artifact authorityが取得した現在状態のスナップショットを保持する。

        この部品単体ではpeer認証やArtifactStoreとの接続を検査できないため、
        この記録だけで利用可否やCI成功を許可しない。
        """
        _id(run_id)
        if type(evidence_state) is not dict or set(evidence_state) != {"state", "valid_until", "revocation_generation"}:
            raise _error("INVALID_EVIDENCE_STATE")
        state = evidence_state["state"]
        if type(state) is not str or state not in _EVIDENCE_STATES:
            raise _error("INVALID_EVIDENCE_STATE")
        valid_until = evidence_state["valid_until"]
        if valid_until is not None and (type(valid_until) is not int or not 0 <= valid_until <= MAX_INTEGER):
            raise _error("INVALID_EVIDENCE_STATE")
        generation = evidence_state["revocation_generation"]
        if type(generation) is not int or not 0 <= generation <= MAX_INTEGER:
            raise _error("INVALID_EVIDENCE_STATE")
        with self._transaction() as db:
            now = self._now(db)
            run_row = self._run_row(db, run_id)
            self._resolve_run_context(db, run_row, now)
            current = self._state(db, run_id)
            current_generation = current["evidence_generation"]
            if generation < current_generation:
                raise _error("EVIDENCE_STATE_CONFLICT")
            if current["evidence_state"] != "UNKNOWN" and state == "UNKNOWN":
                raise _error("EVIDENCE_STATE_CONFLICT")
            if current["evidence_state"] in {"REVOKED", "DELETED"} and state not in {current["evidence_state"]}:
                raise _error("EVIDENCE_STATE_CONFLICT")
            if state == "VALID":
                prior = db.execute(
                    "SELECT MIN(created_at), MIN(valid_until) FROM evidence_events "
                    "WHERE run_id=? AND state='VALID'",
                    (run_id,),
                ).fetchone()
                if prior[1] is not None and (valid_until is None or valid_until > prior[1]):
                    raise _error("EVIDENCE_STATE_CONFLICT")
            event, event_raw, event_digest, event_id = _evidence_event(run_id, state, valid_until, generation)
            previous = db.execute("SELECT * FROM evidence_events WHERE event_id=?", (event_id,)).fetchone()
            if previous is not None:
                if previous["event_digest"] != event_digest or previous["run_id"] != run_id:
                    raise _error("STORAGE_CORRUPT")
                if _load(previous["event_json"], previous["event_digest"]) != event:
                    raise _error("STORAGE_CORRUPT")
                checked_at = current["evidence_checked_at"] if state == "VALID" else previous["created_at"]
                return {
                    "schema_version": 1, "kind": "evidence_state_receipt", "run_id": run_id,
                    "state": state, "checked_at": checked_at, "valid_until": valid_until,
                    "revocation_generation": generation, "event_id": event_id,
                    "event_digest": event_digest, "authority_connected": False, "ci_eligible": False,
                }
            db.execute(
                "INSERT INTO evidence_events VALUES(?,?,?,?,?,?,?,?,?)",
                (event_id, run_id, "EVIDENCE_" + state, state, valid_until, generation, event_raw, event_digest, now),
            )
            checked_at = now
            if state == "VALID" and prior[0] is not None:
                checked_at = min(now, prior[0])
            db.execute("UPDATE run_state SET evidence_state=?, evidence_checked_at=?, evidence_valid_until=?, evidence_generation=? WHERE run_id=?", (state, checked_at, valid_until, generation, run_id))
            return {
                "schema_version": 1, "kind": "evidence_state_receipt", "run_id": run_id,
                "state": state, "checked_at": checked_at, "valid_until": valid_until,
                "revocation_generation": generation, "event_id": event_id,
                "event_digest": event_digest, "authority_connected": False, "ci_eligible": False,
            }

    def record_attempt(self, attempt: Any) -> dict[str, Any]:
        normalized, digest = _attempt(attempt)
        run_id = normalized["expected_binding"]["run_id"]
        with self._transaction() as db:
            now = self._now(db)
            row = self._run_row(db, run_id)
            bound, profile, _ = self._resolve_run_context(db, row, now)
            _binding_matches(bound, profile, normalized)
            state = self._state(db, run_id)
            existing = db.execute("SELECT * FROM attempts WHERE attempt_id=?", (normalized["attempt_id"],)).fetchone()
            if existing is not None:
                if existing["run_id"] != run_id:
                    raise _error("ATTEMPT_CONFLICT")
                previous = _load(existing["attempt_json"], existing["attempt_digest"])
                if existing["attempt_digest"] == digest and previous == normalized:
                    db.execute("UPDATE attempts SET delivery_count=delivery_count+1 WHERE attempt_id=?", (normalized["attempt_id"],))
                    return {"schema_version": 1, "kind": "attempt_receipt", "attempt_id": normalized["attempt_id"], "run_id": run_id, "accepted": True, "duplicate": True, "delivery_count": existing["delivery_count"] + 1, "attempt_digest": digest, "ci_eligible": False}
                event_id = "conflict-" + hashlib.sha256((normalized["attempt_id"] + digest).encode("utf-8")).hexdigest()
                db.execute("INSERT OR IGNORE INTO attempt_events VALUES(?,?,?,?,?,?)", (event_id, run_id, normalized["attempt_id"], "ATTEMPT_CONFLICT", digest, now))
                db.execute("UPDATE run_state SET state='HOLD', hold_reason='ATTEMPT_CONFLICT' WHERE run_id=?", (run_id,))
                return {"schema_version": 1, "kind": "attempt_receipt", "attempt_id": normalized["attempt_id"], "run_id": run_id, "accepted": False, "duplicate": False, "event_id": event_id, "reason": "ATTEMPT_CONFLICT", "ci_eligible": False}
            if state["finalized_at"] is not None:
                event_id = "late-" + hashlib.sha256((normalized["attempt_id"] + digest).encode("utf-8")).hexdigest()
                db.execute("INSERT OR IGNORE INTO attempt_events VALUES(?,?,?,?,?,?)", (event_id, run_id, normalized["attempt_id"], "LATE_ATTEMPT", digest, now))
                db.execute("UPDATE run_state SET state='HOLD', hold_reason='LATE_ATTEMPT' WHERE run_id=?", (run_id,))
                return {"schema_version": 1, "kind": "attempt_receipt", "attempt_id": normalized["attempt_id"], "run_id": run_id, "accepted": False, "event_id": event_id, "reason": "LATE_ATTEMPT", "ci_eligible": False}
            if normalized["started_at"] > now or (normalized["finished_at"] is not None and normalized["finished_at"] > now):
                event_id = "future-" + hashlib.sha256((normalized["attempt_id"] + digest).encode("utf-8")).hexdigest()
                db.execute("INSERT OR IGNORE INTO attempt_events VALUES(?,?,?,?,?,?)", (event_id, run_id, normalized["attempt_id"], "FUTURE_ATTEMPT", digest, now))
                db.execute("UPDATE run_state SET state='HOLD', hold_reason='FUTURE_ATTEMPT' WHERE run_id=?", (run_id,))
                return {"schema_version": 1, "kind": "attempt_receipt", "attempt_id": normalized["attempt_id"], "run_id": run_id, "accepted": False, "event_id": event_id, "reason": "FUTURE_ATTEMPT", "ci_eligible": False}
            count = db.execute("SELECT count(*) FROM attempts WHERE run_id=?", (run_id,)).fetchone()[0]
            if count >= _MAX_ATTEMPTS:
                raise _error("ATTEMPT_LIMIT")
            raw, _ = _pack(normalized)
            db.execute("INSERT INTO attempts VALUES(?,?,?,?,?)", (normalized["attempt_id"], run_id, raw, digest, 1))
            # 新しい観測が入ったら、過去集計を現在値として再利用しない。
            db.execute("UPDATE run_state SET aggregate_digest=NULL, decision_digest=NULL WHERE run_id=?", (run_id,))
            return {"schema_version": 1, "kind": "attempt_receipt", "attempt_id": normalized["attempt_id"], "run_id": run_id, "accepted": True, "duplicate": False, "delivery_count": 1, "attempt_digest": digest, "ci_eligible": False}

    def get_attempt(self, attempt_id: str) -> dict[str, Any]:
        _id(attempt_id)
        with self._transaction() as db:
            now = self._now(db)
            row = db.execute("SELECT * FROM attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
            if row is None:
                raise _error("ATTEMPT_NOT_FOUND")
            run_row = self._run_row(db, row["run_id"])
            self._resolve_run_context(db, run_row, now)
            return {"schema_version": 1, "kind": "attempt_record", "attempt": _load(row["attempt_json"], row["attempt_digest"]), "attempt_digest": row["attempt_digest"], "delivery_count": row["delivery_count"], "ci_eligible": False}

    def _aggregate_in_tx(self, db: sqlite3.Connection, run_id: str, now: int) -> dict[str, Any]:
        row = self._run_row(db, run_id)
        bound, profile, baseline = self._resolve_run_context(db, row, now)
        rows = db.execute("SELECT * FROM attempts WHERE run_id=? ORDER BY attempt_id", (run_id,)).fetchall()
        attempts = [_load(item["attempt_json"], item["attempt_digest"]) for item in rows]
        observed_times = [
            attempt["finished_at"] if attempt["finished_at"] is not None else attempt["started_at"]
            for attempt in attempts
        ]
        if any(value > now for value in observed_times):
            raise _error("FUTURE_ATTEMPT")
        try:
            result = self._aggregate_value(bound, attempts, profile, baseline)
        except (ContractError, ValueError, TypeError, KeyError, RecursionError):
            raise _error("AGGREGATION_INVALID") from None
        result["observed_at"] = min(observed_times) if observed_times else row["started_at"]
        if result["integrity_failure"]:
            db.execute("UPDATE run_state SET state='HOLD', hold_reason=COALESCE(hold_reason,'AGGREGATION_INTEGRITY') WHERE run_id=?", (run_id,))
        raw, digest = _pack(result)
        existing = db.execute("SELECT 1 FROM aggregates WHERE run_id=? AND aggregate_digest=?", (run_id, digest)).fetchone()
        if existing is None:
            db.execute("INSERT INTO aggregates VALUES(?,?,?,?)", (run_id, digest, raw, now))
        db.execute("UPDATE run_state SET aggregate_digest=? WHERE run_id=?", (digest, run_id))
        result = deepcopy(result)
        result["aggregate_digest"] = digest
        result["ci_eligible"] = False
        return result

    def aggregate(self, run_id: str) -> dict[str, Any]:
        _id(run_id)
        with self._transaction() as db:
            now = self._now(db)
            return self._aggregate_in_tx(db, run_id, now)

    @staticmethod
    def _decision_from_aggregate(bound: dict[str, Any], aggregate_value: dict[str, Any], now: int,
                                 *, budget_warning_basis=None) -> dict[str, Any]:
        basis = None
        warning = False
        if budget_warning_basis is not None:
            warning = bool(budget_warning.warning_dimensions(bound, budget_warning_basis, now))
            basis = deepcopy(budget_warning_basis)
        metrics = aggregate_value.get("metrics")
        if type(metrics) is not list:
            raise _error("DECISION_UNAVAILABLE")
        if not metrics:
            kinds = {obligation["kind"] for control in bound["registry"]["controls"]
                     if control["control_id"] in bound["selected_controls"]
                     for obligation in control["obligations"]}
            if kinds != {"constraint"}:
                raise _error("DECISION_UNAVAILABLE")
        manifest = bound["manifest"]
        target_digest = hashlib.sha256(_walk_json(manifest["target_refs"])).hexdigest()
        contract_digest = manifest["contract_ref"]["digest"]
        components = []
        all_metrics = []
        reasons: list[dict[str, Any]] = []
        for index in range(0, max(1, len(metrics)), 100):
            request = {
                "schema_version": 1, "request_id": f"decision-part-{index // 100}",
                "target_digest": target_digest, "contract_digest": contract_digest,
                "purpose": "component_validation", "observed_at": aggregate_value["observed_at"], "assessed_at": now,
                "metrics": deepcopy(metrics[index:index + 100]),
                "required_missing": aggregate_value["required_missing"] if index == 0 else False,
                "critical_missing": aggregate_value["critical_missing"] if index == 0 else False,
                "integrity_failure": aggregate_value["integrity_failure"] if index == 0 else False,
                "forbidden_violation": aggregate_value["forbidden_violation"] if index == 0 else False,
                "critical_violation": aggregate_value["critical_violation"] if index == 0 else False,
                "warning": warning if index == 0 else False,
            }
            try:
                component = (decision.assess(request) if metrics
                             else decision._assess_constraint_events(request))
            except (ValueError, TypeError, KeyError, RecursionError):
                raise _error("DECISION_UNAVAILABLE") from None
            components.append(component)
            all_metrics.extend(component["metrics"])
            reasons.extend(component["reasons"])
        if aggregate_value["counts"]["variant"]["candidate"]["constraint_fail"] > 0:
            reasons.append({"code": "constraint_violation", "state": "DEGRADED", "metric_id": None})
        unique: dict[str, dict[str, Any]] = {}
        for reason in reasons:
            key = _pack(reason)[1]
            unique[key] = reason
        reasons = sorted(unique.values(), key=lambda item: (_REASON_PRIORITY[item["state"]], item["code"], item.get("metric_id") or ""))
        assurance = "HEALTHY" if not reasons else reasons[0]["state"]
        return {
            "schema_version": 1, "kind": "run_decision", "run_id": manifest["run_id"],
            "aggregate_digest": aggregate_value["aggregate_digest"],
            "assessed_at": now, "purpose": "component_validation", "assurance": assurance,
            "metrics": all_metrics, "metric_scopes": deepcopy(aggregate_value.get("metric_scopes", {})),
            "reasons": reasons, "components": components, "ci_eligible": False,
            **({"budget_warning": basis} if basis is not None else {}),
        }

    def finalize(self, run_id: str, *, budget_warning_basis=None) -> dict[str, Any]:
        _id(run_id)
        with self._transaction() as db:
            now = self._now(db)
            row = self._run_row(db, run_id)
            bound, profile, baseline = self._resolve_run_context(db, row, now)
            state = self._state(db, run_id)
            terminal = db.execute("SELECT * FROM terminals WHERE run_id=?", (run_id,)).fetchone()
            if terminal is not None:
                value = _load(terminal["terminal_json"], terminal["terminal_digest"])
                if budget_warning_basis is not None:
                    basis = budget_warning.validate_basis(bound, budget_warning_basis, value["decision"]["assessed_at"])
                    if value["decision"].get("budget_warning") != basis:
                        raise _error("BUDGET_WARNING_MISMATCH")
                return deepcopy(value)
            aggregate_value = self._aggregate_in_tx(db, run_id, now)
            decision_value = self._decision_from_aggregate(bound, aggregate_value, now,
                budget_warning_basis=budget_warning_basis)
            raw, digest = _pack(decision_value)
            if db.execute("SELECT 1 FROM decisions WHERE run_id=? AND decision_digest=?", (run_id, digest)).fetchone() is None:
                db.execute("INSERT INTO decisions VALUES(?,?,?,?)", (run_id, digest, raw, now))
            binding = self._binding_summary(bound, profile, baseline)
            current_state = self._state(db, run_id)
            if decision_value["assurance"] == "HOLD" and current_state["state"] != "HOLD":
                db.execute("UPDATE run_state SET state='HOLD', hold_reason=COALESCE(hold_reason,'DECISION_HOLD') WHERE run_id=?", (run_id,))
                current_state = self._state(db, run_id)
            final_state = "HOLD" if current_state["state"] == "HOLD" else "FINALIZED"
            terminal_value = {
                "schema_version": 1, "kind": "run_evidence_finalization", "run_id": run_id,
                "decision_digest": digest, "decision": decision_value,
                "binding": binding, "state": final_state,
                "diagnostic_finalized": True, "authority_connected": False,
                "resource_closure_verified": False, "baseline_freshness_verified": False,
                "adoption_verified": False,
                "ci_eligible": False,
            }
            terminal_raw, terminal_digest = _pack(terminal_value)
            db.execute("INSERT INTO terminals VALUES(?,?,?,?)", (run_id, terminal_raw, terminal_digest, now))
            db.execute("UPDATE run_state SET state=?, decision_digest=?, finalized_at=? WHERE run_id=?", (final_state, digest, now, run_id))
            return deepcopy(terminal_value)

    def get_terminal(self, run_id: str) -> dict[str, Any]:
        _id(run_id)
        with self._transaction() as db:
            now = self._now(db)
            row = db.execute("SELECT * FROM terminals WHERE run_id=?", (run_id,)).fetchone()
            if row is None:
                raise _error("NOT_FINALIZED")
            run_row = self._run_row(db, run_id)
            self._resolve_run_context(db, run_row, now)
            return deepcopy(_load(row["terminal_json"], row["terminal_digest"]))

    def current_use(self, run_id: str, expected_binding: Any) -> dict[str, Any]:
        _id(run_id)
        if type(expected_binding) is not dict:
            raise _error("INVALID_BINDING")
        with self._transaction() as db:
            now = self._now(db)
            row = self._run_row(db, run_id)
            bound, profile, baseline = self._resolve_run_context(db, row, now)
            state = self._state(db, run_id)
            from .evidence_snapshot_cache import check
            check(db, run_id, now, row, bound, profile, baseline, state, self._validate_store_contents)
            expected = self._binding_summary(bound, profile, baseline)
            binding_match = expected_binding == expected
            reasons: list[str] = []
            if not binding_match:
                reasons.append("BINDING_MISMATCH")
            if db.execute("SELECT 1 FROM terminals WHERE run_id=?", (run_id,)).fetchone() is None:
                reasons.append("NOT_FINALIZED")
            if state["aggregate_digest"] is None and db.execute("SELECT 1 FROM aggregates WHERE run_id=?", (run_id,)).fetchone() is not None:
                reasons.append("AGGREGATE_NOT_CURRENT")
            if state["state"] == "HOLD":
                reasons.append(state["hold_reason"] or "HOLD")
            evidence_state = state["evidence_state"]
            if evidence_state != "UNKNOWN":
                event, _, event_digest, event_id = _evidence_event(
                    run_id, evidence_state, state["evidence_valid_until"], state["evidence_generation"]
                )
                evidence_row = db.execute("SELECT * FROM evidence_events WHERE event_id=?", (event_id,)).fetchone()
                if (evidence_row is None or evidence_row["run_id"] != run_id
                        or evidence_row["event_digest"] != event_digest
                        or _load(evidence_row["event_json"], evidence_row["event_digest"]) != event):
                    raise _error("STORAGE_CORRUPT")
            checked_at = state["evidence_checked_at"]
            if (evidence_state == "VALID" and (checked_at is None
                    or now > checked_at + FRESHNESS_SECONDS
                    or (state["evidence_valid_until"] is not None and now > state["evidence_valid_until"]))):
                evidence_state = "EXPIRED"
            if evidence_state != "VALID":
                reasons.append("EVIDENCE_NOT_CURRENT")
            # snapshotは上位authorityの認証済み接続を表さないため、VALIDの
            # 自己申告だけではreview readyにしない。
            reasons.append("EVIDENCE_UNVERIFIED")
            # このstoreは上位Artifact/authority/資源台帳へ未接続なので、常にfalse。
            reasons.append("AUTHORITY_NOT_CONNECTED")
            return {
                "schema_version": 1, "kind": "run_use_decision", "run_id": run_id,
                "checked_at": now, "expected_binding": deepcopy(expected_binding),
                "current_binding": expected, "ready_for_authority_review": False,
                "use": False, "reasons": reasons, "authority_connected": False,
                "resource_closure_verified": False, "baseline_freshness_verified": False,
                "adoption_verified": False, "ci_eligible": False,
            }


class RunEvidenceBook(RunEvidenceStore):
    """既存transaction内でRunEvidenceStoreの共通処理を実行するbook。"""

    def __init__(self, db: sqlite3.Connection, *, now: int,
                 allowed_bindings: dict[str, str]):
        if not isinstance(db, sqlite3.Connection) or not db.in_transaction:
            raise _error("TRANSACTION_REQUIRED")
        if type(now) is not int or not 0 <= now <= MAX_INTEGER:
            raise _error("CLOCK_UNAVAILABLE")
        if type(allowed_bindings) is not dict:
            raise _error("INVALID_BINDINGS")
        db.row_factory = sqlite3.Row
        _validate_schema(db)
        self._db = db
        self._book_now = now
        self._allowed = _normalize_allowed_bindings(allowed_bindings)
        self._clock = lambda: self._book_now

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        if self._db is None or not self._db.in_transaction:
            raise _error("TRANSACTION_REQUIRED")
        yield self._db

    def _now(self, db: sqlite3.Connection) -> int:
        row = db.execute("SELECT value FROM run_evidence_meta WHERE key='last_clock'").fetchone()
        if row is None or type(row[0]) is not int or not -1 <= row[0] <= MAX_INTEGER:
            raise _error("STORAGE_CORRUPT")
        if self._book_now < row[0]:
            raise _error("CLOCK_ROLLBACK")
        if self._book_now > row[0]:
            db.execute("UPDATE run_evidence_meta SET value=? WHERE key='last_clock'", (self._book_now,))
        return self._book_now

    def close(self) -> None:
        # 接続の所有者はauthority側なので、bookからcloseしない。
        return None


__all__ = [
    "EvidenceError", "RunEvidenceStore", "RunEvidenceBook", "bound_bundle_digest",
    "create_schema", "TABLES", "COLUMNS", "MAX_ATTEMPT_BYTES", "BASELINE_SECONDS",
    "FRESHNESS_SECONDS", "RETENTION_SECONDS",
]
