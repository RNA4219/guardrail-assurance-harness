"""管理主体による PolicyProfile の提案・検証・採択 core。

このモジュールは peer credential を検査して固定主体へ写像するが、OS 認証や
socket transport の実装ではない。``dispatch`` の peer uid/gid は、上位の broker
が AF_UNIX の SO_PEERCRED から得た値だけを渡す境界である。要求本文の actor、
context、role、uid は主体根拠として受け付けない。PolicyProfile の採択は通常 CI
成功や EvaluationContract / baseline の採択とは別の前段である。
"""

from __future__ import annotations

from contextlib import contextmanager
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from typing import Any, Callable, Iterator
import re

from .contracts import MAX_DOCUMENT_BYTES, MAX_INTEGER, ContractError, decode_document, require_id, require_uint
from .policy import PolicyError, initial_policy_profile, validate_policy_profile
from .wire import canonical_bytes


VALIDATION_TTL = 86400
_SCHEMA_VERSION = 1
_EXTENSION_SCHEMA_VERSION = 3
_MAX_REQUEST_BYTES = MAX_DOCUMENT_BYTES
_PREDECESSOR_VALIDATOR_DIGEST = "acc76f697b442719da8f9465adaf26c6edfd29e02f716bb1faa54c2ff28b1448"
_POLICY_ADOPTION_COLUMNS = (
    "series_id", "generation", "proposal_id", "validation_id", "policy_json",
    "policy_digest", "adopted_at", "actor_id", "context",
)
_ACTORS = {
    (12001, 12001): ("manager", "manager-context"),
    (12002, 12002): ("candidate", "candidate-context"),
    (12003, 12003): ("validator", "validator-context"),
    (12004, 12004): ("operator", "operator-context"),
}
_ROLES = {"manager", "validator", "operator"}
_ACTIONS = {"propose", "validate", "adopt", "current", "receipt", "revoke_actor", "revoke_validation"}
_ACTION_FIELDS = {
    "propose": {"schema_version", "action", "request_id", "proposal_id", "series_id", "expected_generation", "policy"},
    "validate": {"schema_version", "action", "request_id", "proposal_id", "validation_id"},
    "adopt": {"schema_version", "action", "request_id", "proposal_id", "validation_id", "expected_generation"},
    "current": {"schema_version", "action", "request_id", "series_id"},
    "receipt": {"schema_version", "action", "request_id", "adoption_request_id"},
    "revoke_actor": {"schema_version", "action", "request_id", "actor_id"},
    "revoke_validation": {"schema_version", "action", "request_id", "validation_id"},
}
_TABLES = {"adoption_meta", "adoption_config", "proposals", "validations", "current_profiles", "revocations", "idempotency"}
_COLUMNS = {
    "adoption_meta": {"key", "value"},
    "adoption_config": {"key", "value"},
    "proposals": {
        "proposal_id", "series_id", "expected_generation", "policy_json", "policy_digest",
        "created_at", "actor_id", "context", "bootstrap_digest", "validator_digest",
    },
    "validations": {
        "validation_id", "proposal_id", "proposal_digest", "expected_generation", "observed_at",
        "expires_at", "actor_id", "context", "permission_generation", "bootstrap_digest", "validator_digest",
    },
    "current_profiles": {
        "series_id", "generation", "proposal_id", "validation_id", "policy_json", "policy_digest",
        "adopted_at", "actor_id", "context",
    },
    "revocations": {"entity_type", "entity_id", "generation", "observed_at"},
    "idempotency": {"request_id", "request_digest", "actor_id", "context", "response_json", "response_digest"},
}


class AdoptionError(ValueError):
    """入力、権限、状態、時計、保存の固定エラー。"""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _error(code: str) -> AdoptionError:
    return AdoptionError(code)


def _require_text_id(value: Any, code: str = "INVALID_REQUEST") -> None:
    try:
        require_id(value)
    except ContractError:
        raise _error(code) from None


def _require_generation(value: Any) -> None:
    try:
        require_uint(value)
    except ContractError:
        raise _error("INVALID_GENERATION") from None


def _decode_stored(raw: Any) -> dict[str, Any]:
    if type(raw) is not str:
        raise _error("STORAGE_CORRUPT")
    try:
        value = decode_document(raw.encode("utf-8"))
    except (ContractError, UnicodeError):
        raise _error("STORAGE_CORRUPT") from None
    try:
        if canonical_bytes(value).decode("utf-8") != raw:
            raise _error("STORAGE_CORRUPT")
    except (TypeError, ValueError, UnicodeError):
        raise _error("STORAGE_CORRUPT") from None
    return value


def _digest(value: dict[str, Any], *, maximum: int = _MAX_REQUEST_BYTES) -> tuple[bytes, str]:
    try:
        raw = canonical_bytes(value)
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise _error("INVALID_REQUEST") from None
    if len(raw) > maximum:
        raise _error("REQUEST_TOO_LARGE")
    return raw, hashlib.sha256(raw).hexdigest()


def _validate_request(request: Any) -> tuple[dict[str, Any], str, bytes]:
    if type(request) is not dict:
        raise _error("INVALID_REQUEST")
    action = request.get("action")
    if type(action) is not str or action not in _ACTIONS:
        raise _error("INVALID_ACTION")
    if set(request) != _ACTION_FIELDS[action]:
        raise _error("INVALID_REQUEST")
    if type(request["schema_version"]) is not int or request["schema_version"] != 1:
        raise _error("UNSUPPORTED_VERSION")
    _require_text_id(request["request_id"], "INVALID_REQUEST_ID")
    if action in {"propose", "validate", "adopt", "current", "receipt", "revoke_actor", "revoke_validation"}:
        field = {
            "propose": ("proposal_id", "series_id"), "validate": ("proposal_id", "validation_id"),
            "adopt": ("proposal_id", "validation_id"), "current": ("series_id",),
            "receipt": ("adoption_request_id",), "revoke_actor": ("actor_id",),
            "revoke_validation": ("validation_id",),
        }[action]
        for name in field:
            _require_text_id(request[name])
    if action in {"propose", "adopt"}:
        _require_generation(request["expected_generation"])
    if action == "propose":
        try:
            policy = validate_policy_profile(request["policy"])
        except (PolicyError, KeyError, TypeError, ValueError, RecursionError):
            raise _error("INVALID_POLICY") from None
        normalized = dict(request)
        normalized["policy"] = policy
    else:
        normalized = dict(request)
    raw, digest = _digest(normalized)
    return normalized, digest, raw


def _validate_extension_request(extension: Any, actions: dict[str, set[str]], request: Any) -> tuple[dict[str, Any], str, bytes]:
    if type(request) is not dict:
        raise _error("INVALID_REQUEST")
    try:
        raw_request = canonical_bytes(request)
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise _error("INVALID_REQUEST") from None
    if len(raw_request) > _MAX_REQUEST_BYTES:
        raise _error("REQUEST_TOO_LARGE")
    try:
        normalized = extension.validate_request(copy.deepcopy(request))
    except AdoptionError:
        raise
    except Exception:
        raise _error("INVALID_REQUEST") from None
    if type(normalized) is not dict:
        raise _error("EXTENSION_INVALID")
    action = normalized.get("action")
    if type(normalized.get("schema_version")) is not int or normalized["schema_version"] != 1:
        raise _error("UNSUPPORTED_VERSION")
    if type(action) is not str or action not in actions:
        raise _error("INVALID_ACTION")
    if normalized.get("action") != request.get("action") or normalized.get("request_id") != request.get("request_id"):
        raise _error("EXTENSION_INVALID")
    _require_text_id(normalized.get("request_id"), "INVALID_REQUEST_ID")
    normalized_raw, digest = _digest(normalized)
    return normalized, digest, normalized_raw


def _validate_digest(value: Any) -> None:
    if type(value) is not str or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise _error("STORAGE_CORRUPT")


_SQL_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def _extension_definition(extension: Any) -> tuple[dict[str, set[str]], dict[str, set[str]], set[str], str]:
    """Validate trusted extension metadata before it can affect a database."""
    try:
        tables = extension.tables
        digest = extension.digest
        actions = extension.actions
        fresh_actions = extension.fresh_actions
        methods = (extension.create_schema, extension.validate_request, extension.execute)
    except (AttributeError, TypeError):
        raise _error("EXTENSION_INVALID") from None
    if type(tables) is not dict or type(actions) is not dict or type(fresh_actions) is not set:
        raise _error("EXTENSION_INVALID")
    if (type(digest) is not str or len(digest) != 64
            or digest != digest.lower()
            or any(char not in "0123456789abcdef" for char in digest)):
        raise _error("EXTENSION_INVALID")
    normalized_tables: dict[str, set[str]] = {}
    for table, columns in tables.items():
        if (type(table) is not str or _SQL_IDENTIFIER.fullmatch(table) is None
                or table in _TABLES or type(columns) is not set or not columns):
            raise _error("EXTENSION_INVALID")
        if any(type(column) is not str or _SQL_IDENTIFIER.fullmatch(column) is None for column in columns):
            raise _error("EXTENSION_INVALID")
        normalized_tables[table] = set(columns)
    normalized_actions: dict[str, set[str]] = {}
    for action, allowed in actions.items():
        if (type(action) is not str or _SQL_IDENTIFIER.fullmatch(action) is None
                or action in _ACTIONS or type(allowed) is not set or not allowed
                or any(type(actor) is not str or actor not in _ROLES for actor in allowed)):
            raise _error("EXTENSION_INVALID")
        normalized_actions[action] = set(allowed)
    if any(type(action) is not str or action not in normalized_actions for action in fresh_actions):
        raise _error("EXTENSION_INVALID")
    if any(not callable(method) for method in methods):
        raise _error("EXTENSION_INVALID")
    return normalized_tables, normalized_actions, set(fresh_actions), digest


class AdoptionStore:
    """PolicyProfile 採択の原子的な保存 core。

    ``bootstrap_policy`` と ``validator_digest`` は broker 配置側から渡す trusted
    入力であり、dispatch の候補要求からは決して構成しない。このクラス単体は
    peer credential の真正性、OS 隔離、外部認証、通常 CI の合格を保証しない。
    """

    def __init__(self, path: str | Path, *, clock: Callable[[], int] | None = None,
                 bootstrap_policy: dict[str, Any] | None = None,
                 validator_digest: str | None = None, extension: Any | None = None):
        self._clock = clock or (lambda: int(time.time()))
        if extension is None:
            self._extension = None
            self._extension_tables = {}
            self._extension_actions = {}
            self._fresh_actions = set()
            self._extension_digest = None
            self._extension_schema_version = _SCHEMA_VERSION
        else:
            self._extension = extension
            (self._extension_tables, self._extension_actions,
             self._fresh_actions, self._extension_digest) = _extension_definition(extension)
            self._extension_schema_version = getattr(extension, "schema_version", 2)
            if type(self._extension_schema_version) is not int or self._extension_schema_version not in {2, 3, 4}:
                raise _error("EXTENSION_INVALID")
        if bootstrap_policy is None:
            try:
                bootstrap = initial_policy_profile()
            except (PolicyError, OSError, UnicodeError, ValueError, TypeError, RecursionError):
                raise _error("BOOTSTRAP_INVALID") from None
        else:
            try:
                bootstrap = validate_policy_profile(bootstrap_policy)
            except (PolicyError, KeyError, TypeError, ValueError, RecursionError):
                raise _error("BOOTSTRAP_INVALID") from None
        bootstrap_raw, self._bootstrap_digest = _digest(bootstrap)
        self._bootstrap = copy.deepcopy(bootstrap)
        if validator_digest is not None:
            _validate_digest(validator_digest)
            self._validator_digest = validator_digest
        else:
            try:
                digest = hashlib.sha256()
                for source in (
                    Path(__file__),
                    Path(__file__).with_name("policy.py"),
                    Path(__file__).resolve().parents[2] / "config" / "bootstrap-policy.v1.json",
                ):
                    data = source.read_bytes()
                    digest.update(len(data).to_bytes(8, "big"))
                    digest.update(data)
                self._validator_digest = digest.hexdigest()
            except OSError:
                raise _error("BOOTSTRAP_INVALID") from None
        self._db: sqlite3.Connection | None = None
        try:
            db = sqlite3.connect(str(Path(path)), isolation_level=None, timeout=5)
            self._db = db
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            with self._transaction() as connection:
                expected_version = (self._extension_schema_version if self._extension is not None
                                    else _SCHEMA_VERSION)
                expected_tables = _TABLES | set(self._extension_tables)
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if version == 0 and not tables:
                    connection.execute("CREATE TABLE adoption_meta(key TEXT PRIMARY KEY, value INTEGER NOT NULL)")
                    connection.executemany("INSERT INTO adoption_meta(key,value) VALUES(?,?)",
                                           [("schema_version", expected_version), ("last_clock", -1), ("permission_generation", 0)])
                    connection.execute("CREATE TABLE adoption_config(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                    config_rows = [("bootstrap_digest", self._bootstrap_digest),
                                   ("validator_digest", self._validator_digest)]
                    if self._extension is not None:
                        config_rows.append(("extension_digest", self._extension_digest))
                    connection.executemany("INSERT INTO adoption_config(key,value) VALUES(?,?)", config_rows)
                    connection.execute("""CREATE TABLE proposals(
                        proposal_id TEXT PRIMARY KEY, series_id TEXT NOT NULL, expected_generation INTEGER NOT NULL,
                        policy_json TEXT NOT NULL, policy_digest TEXT NOT NULL, created_at INTEGER NOT NULL,
                        actor_id TEXT NOT NULL, context TEXT NOT NULL, bootstrap_digest TEXT NOT NULL,
                        validator_digest TEXT NOT NULL)""")
                    connection.execute("""CREATE TABLE validations(
                        validation_id TEXT PRIMARY KEY, proposal_id TEXT NOT NULL, proposal_digest TEXT NOT NULL,
                        expected_generation INTEGER NOT NULL, observed_at INTEGER NOT NULL, expires_at INTEGER NOT NULL,
                        actor_id TEXT NOT NULL, context TEXT NOT NULL, permission_generation INTEGER NOT NULL,
                        bootstrap_digest TEXT NOT NULL, validator_digest TEXT NOT NULL)""")
                    connection.execute("""CREATE TABLE current_profiles(
                        series_id TEXT PRIMARY KEY, generation INTEGER NOT NULL, proposal_id TEXT NOT NULL,
                        validation_id TEXT NOT NULL, policy_json TEXT NOT NULL, policy_digest TEXT NOT NULL,
                        adopted_at INTEGER NOT NULL, actor_id TEXT NOT NULL, context TEXT NOT NULL)""")
                    connection.execute("""CREATE TABLE revocations(
                        entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, generation INTEGER NOT NULL,
                        observed_at INTEGER NOT NULL, PRIMARY KEY(entity_type,entity_id))""")
                    connection.execute("""CREATE TABLE idempotency(
                        request_id TEXT PRIMARY KEY, request_digest TEXT NOT NULL, actor_id TEXT NOT NULL,
                        context TEXT NOT NULL, response_json TEXT, response_digest TEXT)""")
                    if self._extension is not None:
                        try:
                            self._extension.create_schema(connection)
                        except (AdoptionError, sqlite3.Error):
                            raise
                        except Exception:
                            raise _error("EXTENSION_INVALID") from None
                    connection.execute(f"PRAGMA user_version={expected_version}")
                    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                elif version != expected_version or tables != expected_tables:
                    raise _error("UNSUPPORTED_STORE")
                if tables != expected_tables:
                    raise _error("UNSUPPORTED_STORE")
                for table, columns in _COLUMNS.items():
                    found = {row[1] for row in connection.execute('PRAGMA table_info("' + table + '")')}
                    if found != columns:
                        raise _error("UNSUPPORTED_STORE")
                for table, columns in self._extension_tables.items():
                    found = {row[1] for row in connection.execute('PRAGMA table_info("' + table + '")')}
                    if found != columns:
                        raise _error("UNSUPPORTED_STORE")
                config = {
                    row["key"]: row["value"]
                    for row in connection.execute("SELECT key,value FROM adoption_config")
                }
                expected_config = {"bootstrap_digest", "validator_digest"}
                if self._extension is not None:
                    expected_config.add("extension_digest")
                validator_matches = config.get("validator_digest") == self._validator_digest
                if (self._extension is not None and self._extension_schema_version >= 3
                        and config.get("validator_digest") == _PREDECESSOR_VALIDATOR_DIGEST):
                    validator_matches = True
                if (set(config) != expected_config
                        or config["bootstrap_digest"] != self._bootstrap_digest
                        or not validator_matches):
                    raise _error("CONFIG_MISMATCH")
                if self._extension is not None and config.get("extension_digest") != self._extension_digest:
                    raise _error("CONFIG_MISMATCH")
                meta = {row["key"]: row["value"] for row in connection.execute("SELECT key,value FROM adoption_meta")}
                if set(meta) != {"schema_version", "last_clock", "permission_generation"}:
                    raise _error("STORAGE_CORRUPT")
                if meta["schema_version"] != expected_version or type(meta["schema_version"]) is not int:
                    raise _error("STORAGE_CORRUPT")
                if type(meta["permission_generation"]) is not int or not 0 <= meta["permission_generation"] <= MAX_INTEGER:
                    raise _error("STORAGE_CORRUPT")
                row = connection.execute("SELECT value FROM adoption_meta WHERE key='last_clock'").fetchone()
                if row is None or type(row[0]) is not int or not -1 <= row[0] <= MAX_INTEGER:
                    raise _error("STORAGE_CORRUPT")
                # Read to keep the trusted配置 bytes explicitly rooted at construction.
                if len(bootstrap_raw) > MAX_DOCUMENT_BYTES:
                    raise _error("BOOTSTRAP_INVALID")
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

    def __enter__(self) -> "AdoptionStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        if self._db is not None:
            self._db.close()
            self._db = None

    @staticmethod
    def _meta(db: sqlite3.Connection, key: str) -> int:
        row = db.execute("SELECT value FROM adoption_meta WHERE key=?", (key,)).fetchone()
        if row is None or type(row[0]) is not int:
            raise _error("STORAGE_CORRUPT")
        return row[0]

    def _now(self, db: sqlite3.Connection) -> int:
        try:
            now = self._clock()
        except Exception:
            raise _error("CLOCK_UNAVAILABLE") from None
        if type(now) is not int or not 0 <= now <= MAX_INTEGER:
            raise _error("CLOCK_UNAVAILABLE")
        last = self._meta(db, "last_clock")
        if last < -1 or last > MAX_INTEGER:
            raise _error("STORAGE_CORRUPT")
        if now < last:
            raise _error("CLOCK_ROLLBACK")
        db.execute("UPDATE adoption_meta SET value=? WHERE key='last_clock'", (now,))
        return now

    @staticmethod
    def _actor_revoked(db: sqlite3.Connection, actor_id: str) -> bool:
        row = db.execute("SELECT generation FROM revocations WHERE entity_type='actor' AND entity_id=?", (actor_id,)).fetchone()
        if row is None:
            return False
        if type(row[0]) is not int or not 0 <= row[0] <= MAX_INTEGER:
            raise _error("STORAGE_CORRUPT")
        return True

    @staticmethod
    def _permission_generation(db: sqlite3.Connection) -> int:
        generation = AdoptionStore._meta(db, "permission_generation")
        if not 0 <= generation <= MAX_INTEGER:
            raise _error("STORAGE_CORRUPT")
        return generation

    def _validator_profile_matches(self, value: Any, *, allow_predecessor: bool = False) -> bool:
        """現行profile、または明示移行後の既知旧profileだけを照合する。"""
        if value == self._validator_digest:
            return True
        return (allow_predecessor and self._extension is not None
                and self._extension_schema_version >= 3
                and value == _PREDECESSOR_VALIDATOR_DIGEST)

    @staticmethod
    def _store_result(db: sqlite3.Connection, request_id: str, request_digest: str,
                      actor_id: str, context: str, result: dict[str, Any]) -> dict[str, Any]:
        raw, digest = _digest(result)
        db.execute("INSERT INTO idempotency VALUES(?,?,?,?,?,?)",
                   (request_id, request_digest, actor_id, context, raw.decode("utf-8"), digest))
        return result

    @staticmethod
    def _replay(db: sqlite3.Connection, request_id: str, request_digest: str,
                actor_id: str, context: str) -> dict[str, Any] | None:
        row = db.execute("SELECT * FROM idempotency WHERE request_id=?", (request_id,)).fetchone()
        if row is None:
            return None
        _validate_digest(row["request_digest"])
        if (row["request_digest"] != request_digest or row["actor_id"] != actor_id
                or row["context"] != context or type(row["actor_id"]) is not str
                or type(row["context"]) is not str):
            raise _error("REQUEST_CONFLICT")
        if row["response_json"] is None:
            if row["response_digest"] is not None:
                raise _error("STORAGE_CORRUPT")
            return {}
        result = _decode_stored(row["response_json"])
        _validate_digest(row["response_digest"])
        raw, digest = _digest(result)
        if raw.decode("utf-8") != row["response_json"] or digest != row["response_digest"]:
            raise _error("STORAGE_CORRUPT")
        return result

    @staticmethod
    def _result(action: str, request_id: str, **fields: Any) -> dict[str, Any]:
        return {"schema_version": 1, "kind": "policy_adoption_result", "action": action,
                "request_id": request_id, "ci_eligible": False, **fields}

    def _current_generation(self, db: sqlite3.Connection, series_id: str) -> int:
        row = db.execute("SELECT generation FROM current_profiles WHERE series_id=?", (series_id,)).fetchone()
        if row is None:
            return 0
        if type(row[0]) is not int or not 0 <= row[0] <= MAX_INTEGER:
            raise _error("STORAGE_CORRUPT")
        return row[0]

    def dispatch(self, peer_uid: int, peer_gid: int, request: dict[str, Any]) -> dict[str, Any]:
        if type(peer_uid) is not int or type(peer_gid) is not int:
            raise _error("AUTHORITY_MISSING")
        identity = _ACTORS.get((peer_uid, peer_gid))
        if identity is None:
            raise _error("AUTHORITY_MISSING")
        actor_id, context = identity
        requested_action = request.get("action") if type(request) is dict else None
        if type(requested_action) is str and requested_action in self._extension_actions:
            if self._extension is None:
                raise _error("INVALID_ACTION")
            normalized, request_digest, _ = _validate_extension_request(
                self._extension, self._extension_actions, request)
        else:
            normalized, request_digest, _ = _validate_request(request)
        action = normalized["action"]
        is_extension_action = action in self._extension_actions
        if actor_id == "candidate" or (action not in _ACTIONS and not is_extension_action):
            raise _error("AUTHORITY_DENIED")
        allowed = ({
            "propose": {"manager"}, "validate": {"validator"}, "adopt": {"manager"},
            "current": _ROLES, "receipt": _ROLES, "revoke_actor": {"operator"},
            "revoke_validation": {"operator"},
        }[action] if not is_extension_action else self._extension_actions[action])
        if actor_id not in allowed:
            raise _error("AUTHORITY_DENIED")
        request_id = normalized["request_id"]
        with self._transaction() as db:
            if self._actor_revoked(db, actor_id):
                raise _error("AUTHORITY_REVOKED")
            replay = self._replay(db, request_id, request_digest, actor_id, context)
            if replay is not None and action in self._fresh_actions and replay != {}:
                raise _error("STORAGE_CORRUPT")
            if replay is not None and action not in self._fresh_actions and action != "current":
                # NULL responses are reserved for current's deliberately
                # uncached idempotency marker. A damaged/non-current row must
                # never be returned as an empty successful response.
                if replay == {}:
                    raise _error("STORAGE_CORRUPT")
                return replay
            now = self._now(db)
            if action == "propose":
                result = self._propose(db, normalized, actor_id, context, now)
            elif action == "validate":
                result = self._validate(db, normalized, actor_id, context, now)
            elif action == "adopt":
                result = self._adopt(db, normalized, actor_id, context, now)
            elif action == "current":
                result = self._current(db, normalized, now)
            elif action == "receipt":
                result = self._receipt(db, normalized)
            elif action == "revoke_actor":
                result = self._revoke_actor(db, normalized, actor_id, now)
            elif action == "revoke_validation":
                result = self._revoke_validation(db, normalized, now)
            elif is_extension_action:
                ci_path = False
                try:
                    if action == "ci_check":
                        # 任意extensionの成功宣言には昇格権限を与えない。
                        # 固定実装・保存設定を確認し、組込みのfresh検査を直接呼ぶ。
                        from .evaluation_authority import EvaluationExtension
                        from .regression_runs import ci_check
                        if (type(self._extension) is not EvaluationExtension
                                or self._extension_digest != EvaluationExtension.digest
                                or action not in self._fresh_actions):
                            raise _error("EXTENSION_INVALID")
                        result = ci_check(self, db, copy.deepcopy(normalized), now)
                        ci_path = True
                    else:
                        result = self._extension.execute(self, db, copy.deepcopy(normalized), actor_id, context, now)
                except AdoptionError:
                    raise
                except sqlite3.Error:
                    raise
                except Exception:
                    raise _error("EXTENSION_FAILURE") from None
                if type(result) is not dict:
                    raise _error("EXTENSION_INVALID")
                if (type(result.get("schema_version")) is not int or result["schema_version"] != 1
                        or type(result.get("kind")) is not str or not result["kind"]
                        or result.get("action") != action
                        or result.get("request_id") != request_id
                        or ("ci_eligible" in result
                            and (type(result["ci_eligible"]) is not bool or result["ci_eligible"] and not ci_path))):
                    raise _error("EXTENSION_INVALID")
                _digest(result)
            if action == "current" or action in self._fresh_actions:
                if replay is None:
                    db.execute(
                        "INSERT INTO idempotency(request_id,request_digest,actor_id,context,response_json,response_digest) VALUES(?,?,?,?,NULL,NULL)",
                        (request_id, request_digest, actor_id, context),
                    )
                return result
            return self._store_result(db, request_id, request_digest, actor_id, context, result)

    def _propose(self, db: sqlite3.Connection, request: dict[str, Any], actor_id: str,
                 context: str, now: int) -> dict[str, Any]:
        proposal_id, series_id = request["proposal_id"], request["series_id"]
        expected = request["expected_generation"]
        if expected != self._current_generation(db, series_id):
            raise _error("GENERATION_CONFLICT")
        policy_raw, policy_digest = _digest(request["policy"])
        existing = db.execute("SELECT * FROM proposals WHERE proposal_id=?", (proposal_id,)).fetchone()
        if existing is not None:
            if (existing["series_id"] != series_id or existing["expected_generation"] != expected
                    or existing["policy_digest"] != policy_digest or existing["actor_id"] != actor_id
                    or existing["context"] != context):
                raise _error("PROPOSAL_CONFLICT")
            return self._result("propose", request["request_id"], proposal_id=proposal_id,
                                series_id=series_id, expected_generation=expected,
                                proposal_digest=policy_digest, created_at=existing["created_at"])
        db.execute("INSERT INTO proposals VALUES(?,?,?,?,?,?,?,?,?,?)",
                   (proposal_id, series_id, expected, policy_raw.decode("utf-8"), policy_digest, now,
                    actor_id, context, self._bootstrap_digest, self._validator_digest))
        return self._result("propose", request["request_id"], proposal_id=proposal_id, series_id=series_id,
                            expected_generation=expected, proposal_digest=policy_digest, created_at=now)

    def _validate(self, db: sqlite3.Connection, request: dict[str, Any], actor_id: str,
                  context: str, now: int) -> dict[str, Any]:
        proposal_id, validation_id = request["proposal_id"], request["validation_id"]
        proposal = db.execute("SELECT * FROM proposals WHERE proposal_id=?", (proposal_id,)).fetchone()
        if proposal is None:
            raise _error("PROPOSAL_MISSING")
        _validate_digest(proposal["policy_digest"])
        if (type(proposal["expected_generation"]) is not int
                or not 0 <= proposal["expected_generation"] <= MAX_INTEGER
                or type(proposal["created_at"]) is not int
                or not 0 <= proposal["created_at"] <= MAX_INTEGER):
            raise _error("STORAGE_CORRUPT")
        if (proposal["bootstrap_digest"] != self._bootstrap_digest
                or proposal["validator_digest"] != self._validator_digest):
            raise _error("PROPOSAL_STALE")
        policy = _decode_stored(proposal["policy_json"])
        try:
            validated = validate_policy_profile(policy)
        except (PolicyError, KeyError, TypeError, ValueError, RecursionError):
            raise _error("STORAGE_CORRUPT") from None
        policy_raw, policy_digest = _digest(validated)
        if policy_digest != proposal["policy_digest"]:
            raise _error("STORAGE_CORRUPT")
        if self._actor_revoked(db, actor_id):
            raise _error("AUTHORITY_REVOKED")
        permission_generation = self._permission_generation(db)
        expires_at = now + VALIDATION_TTL
        if expires_at > MAX_INTEGER:
            raise _error("INVALID_TIME")
        existing = db.execute("SELECT * FROM validations WHERE validation_id=?", (validation_id,)).fetchone()
        if existing is not None:
            if (existing["proposal_id"] != proposal_id or existing["proposal_digest"] != policy_digest
                    or existing["actor_id"] != actor_id or existing["context"] != context):
                raise _error("VALIDATION_CONFLICT")
            return self._result("validate", request["request_id"], validation_id=validation_id,
                                proposal_id=proposal_id, proposal_digest=policy_digest,
                                observed_at=existing["observed_at"], expires_at=existing["expires_at"],
                                validator_id=actor_id, validator_context=context,
                                permission_generation=existing["permission_generation"], passed=True)
        db.execute("INSERT INTO validations VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                   (validation_id, proposal_id, policy_digest, proposal["expected_generation"], now, expires_at,
                    actor_id, context, permission_generation, self._bootstrap_digest, self._validator_digest))
        return self._result("validate", request["request_id"], validation_id=validation_id,
                            proposal_id=proposal_id, proposal_digest=policy_digest,
                            observed_at=now, expires_at=expires_at, validator_id=actor_id,
                            validator_context=context, permission_generation=permission_generation, passed=True)

    def _adopt(self, db: sqlite3.Connection, request: dict[str, Any], actor_id: str,
               context: str, now: int) -> dict[str, Any]:
        proposal_id, validation_id = request["proposal_id"], request["validation_id"]
        expected = request["expected_generation"]
        proposal = db.execute("SELECT * FROM proposals WHERE proposal_id=?", (proposal_id,)).fetchone()
        validation = db.execute("SELECT * FROM validations WHERE validation_id=?", (validation_id,)).fetchone()
        if proposal is None:
            raise _error("PROPOSAL_MISSING")
        if validation is None:
            raise _error("VALIDATION_MISSING")
        _validate_digest(validation["proposal_digest"])
        if (type(validation["expected_generation"]) is not int
                or not 0 <= validation["expected_generation"] <= MAX_INTEGER
                or type(validation["observed_at"]) is not int
                or type(validation["expires_at"]) is not int
                or not 0 <= validation["observed_at"] <= validation["expires_at"] <= MAX_INTEGER):
            raise _error("STORAGE_CORRUPT")
        current_generation = self._current_generation(db, proposal["series_id"])
        if expected != current_generation or expected != proposal["expected_generation"] or validation["expected_generation"] != expected:
            raise _error("GENERATION_CONFLICT")
        if proposal["actor_id"] != actor_id or proposal["context"] != context:
            raise _error("AUTHORITY_DENIED")
        if validation["proposal_id"] != proposal_id or validation["proposal_digest"] != proposal["policy_digest"]:
            raise _error("VALIDATION_CONFLICT")
        if (proposal["bootstrap_digest"] != self._bootstrap_digest
                or proposal["validator_digest"] != self._validator_digest
                or validation["bootstrap_digest"] != self._bootstrap_digest
                or validation["validator_digest"] != self._validator_digest):
            raise _error("PROPOSAL_STALE")
        if db.execute("SELECT 1 FROM revocations WHERE entity_type='validation' AND entity_id=?", (validation_id,)).fetchone() is not None:
            raise _error("VALIDATION_REVOKED")
        if validation["permission_generation"] != self._permission_generation(db):
            raise _error("AUTHORITY_REVOKED")
        if self._actor_revoked(db, validation["actor_id"]):
            raise _error("AUTHORITY_REVOKED")
        if type(validation["expires_at"]) is not int or now >= validation["expires_at"]:
            raise _error("VALIDATION_EXPIRED")
        policy = _decode_stored(proposal["policy_json"])
        try:
            policy = validate_policy_profile(policy)
        except (PolicyError, KeyError, TypeError, ValueError, RecursionError):
            raise _error("STORAGE_CORRUPT") from None
        policy_raw, policy_digest = _digest(policy)
        if policy_digest != proposal["policy_digest"]:
            raise _error("STORAGE_CORRUPT")
        if current_generation >= MAX_INTEGER:
            raise _error("GENERATION_EXHAUSTED")
        generation = current_generation + 1
        values = (generation, proposal_id, validation_id, policy_raw.decode("utf-8"), policy_digest,
                  now, actor_id, context, proposal["series_id"])
        if db.execute("SELECT 1 FROM current_profiles WHERE series_id=?", (proposal["series_id"],)).fetchone() is None:
            db.execute("INSERT INTO current_profiles VALUES(?,?,?,?,?,?,?,?,?)",
                       (proposal["series_id"], *values[:-1]))
        else:
            db.execute("""UPDATE current_profiles SET generation=?, proposal_id=?, validation_id=?,
                       policy_json=?, policy_digest=?, adopted_at=?, actor_id=?, context=? WHERE series_id=?""", values)
        # v3だけが持つ不変履歴へ同じ行を保存し、旧v1/v2拡張の表構造は変えない。
        if (self._extension is not None and self._extension_schema_version >= 3
                and "policy_adoptions" in self._extension_tables):
            try:
                db.execute(
                    "INSERT INTO policy_adoptions(series_id,generation,proposal_id,validation_id,"
                    "policy_json,policy_digest,adopted_at,actor_id,context) VALUES(?,?,?,?,?,?,?,?,?)",
                    (proposal["series_id"], *values[:-1]),
                )
            except sqlite3.IntegrityError:
                raise _error("STORAGE_CORRUPT") from None
        return self._result("adopt", request["request_id"], series_id=proposal["series_id"],
                            generation=generation, proposal_id=proposal_id, validation_id=validation_id,
                            proposal_digest=policy_digest, policy=policy, adopted_at=now,
                            actor_id=actor_id, context=context, expected_generation=expected,
                            permission_generation=self._permission_generation(db),
                            bootstrap_digest=self._bootstrap_digest, validator_digest=self._validator_digest,
                            validation_actor_id=validation["actor_id"], validation_context=validation["context"],
                            validation_observed_at=validation["observed_at"], validation_expires_at=validation["expires_at"],
                            validation_revocation_generation=None)

    def _current(self, db: sqlite3.Connection, request: dict[str, Any], now: int) -> dict[str, Any]:
        series_id = request["series_id"]
        row = db.execute("SELECT * FROM current_profiles WHERE series_id=?", (series_id,)).fetchone()
        if row is None:
            return self._result("current", request["request_id"], series_id=series_id, generation=0,
                                adopted=False, valid=False, policy=None, proposal_id=None,
                                validation_id=None, policy_digest=None, adopted_at=None)
        if type(row["generation"]) is not int or not 0 <= row["generation"] <= MAX_INTEGER:
            raise _error("STORAGE_CORRUPT")
        policy = _decode_stored(row["policy_json"])
        try:
            validated = validate_policy_profile(policy)
        except (PolicyError, KeyError, TypeError, ValueError, RecursionError):
            raise _error("STORAGE_CORRUPT") from None
        _, policy_digest = _digest(validated)
        if policy_digest != row["policy_digest"]:
            raise _error("STORAGE_CORRUPT")
        proposal = db.execute("SELECT * FROM proposals WHERE proposal_id=?", (row["proposal_id"],)).fetchone()
        if (proposal is None or proposal["series_id"] != series_id
                or proposal["policy_digest"] != row["policy_digest"]):
            raise _error("STORAGE_CORRUPT")
        _validate_digest(proposal["policy_digest"])
        if not self._validator_profile_matches(proposal["validator_digest"], allow_predecessor=True):
            raise _error("STORAGE_CORRUPT")
        validation = db.execute("SELECT * FROM validations WHERE validation_id=?", (row["validation_id"],)).fetchone()
        valid = validation is not None
        if validation is not None:
            _validate_digest(validation["proposal_digest"])
            if (type(validation["observed_at"]) is not int
                    or type(validation["expires_at"]) is not int
                    or not 0 <= validation["observed_at"] <= validation["expires_at"] <= MAX_INTEGER
                    or type(validation["permission_generation"]) is not int
                    or not 0 <= validation["permission_generation"] <= MAX_INTEGER):
                raise _error("STORAGE_CORRUPT")
            valid = (
                validation["proposal_id"] == row["proposal_id"]
                and validation["proposal_digest"] == row["policy_digest"]
                and validation["bootstrap_digest"] == self._bootstrap_digest
                and validation["validator_digest"] == proposal["validator_digest"]
                and self._validator_profile_matches(validation["validator_digest"], allow_predecessor=True)
                and now < validation["expires_at"]
                and validation["permission_generation"] == self._permission_generation(db)
                and db.execute("SELECT 1 FROM revocations WHERE entity_type='validation' AND entity_id=?", (row["validation_id"],)).fetchone() is None
                and not self._actor_revoked(db, validation["actor_id"])
                and not self._actor_revoked(db, row["actor_id"])
            )
        return self._result("current", request["request_id"], series_id=series_id, generation=row["generation"],
                            adopted=True, valid=valid, policy=validated, proposal_id=row["proposal_id"],
                            validation_id=row["validation_id"], policy_digest=row["policy_digest"], adopted_at=row["adopted_at"])

    def _policy_at(self, db: sqlite3.Connection, series_id: str, generation: int, now: int) -> dict[str, Any]:
        """不変のpolicy_adoptionsから指定世代を再評価する。ポインタは変更しない。"""
        _require_text_id(series_id, "INVALID_SERIES_ID")
        _require_generation(generation)
        if self._extension is None or self._extension_schema_version < 3:
            raise _error("HISTORY_MISSING")
        if "policy_adoptions" not in self._extension_tables:
            raise _error("HISTORY_MISSING")
        row = db.execute(
            "SELECT * FROM policy_adoptions WHERE series_id=? AND generation=?",
            (series_id, generation),
        ).fetchone()
        if row is None:
            return {"series_id": series_id, "generation": generation, "adopted": False,
                    "valid": False, "policy": None, "proposal_id": None,
                    "validation_id": None, "policy_digest": None, "adopted_at": None,
                    "ci_eligible": False}
        if type(row["generation"]) is not int or not 1 <= row["generation"] <= MAX_INTEGER:
            raise _error("STORAGE_CORRUPT")
        policy = _decode_stored(row["policy_json"])
        try:
            validated = validate_policy_profile(policy)
        except (PolicyError, KeyError, TypeError, ValueError, RecursionError):
            raise _error("STORAGE_CORRUPT") from None
        _, policy_digest = _digest(validated)
        if policy_digest != row["policy_digest"]:
            raise _error("STORAGE_CORRUPT")
        proposal = db.execute("SELECT * FROM proposals WHERE proposal_id=?", (row["proposal_id"],)).fetchone()
        if (proposal is None or proposal["series_id"] != series_id
                or proposal["expected_generation"] != generation - 1
                or proposal["policy_digest"] != row["policy_digest"]
                or proposal["actor_id"] != row["actor_id"]
                or proposal["context"] != row["context"]):
            raise _error("STORAGE_CORRUPT")
        _validate_digest(proposal["policy_digest"])
        if (proposal["bootstrap_digest"] != self._bootstrap_digest
                or not self._validator_profile_matches(proposal["validator_digest"], allow_predecessor=True)):
            raise _error("STORAGE_CORRUPT")
        validation = db.execute("SELECT * FROM validations WHERE validation_id=?", (row["validation_id"],)).fetchone()
        if validation is None:
            valid = False
        else:
            _validate_digest(validation["proposal_digest"])
            if (type(validation["expected_generation"]) is not int
                    or type(validation["observed_at"]) is not int
                    or type(validation["expires_at"]) is not int
                    or not 0 <= validation["observed_at"] <= validation["expires_at"] <= MAX_INTEGER):
                raise _error("STORAGE_CORRUPT")
            valid = (
                validation["proposal_id"] == row["proposal_id"]
                and validation["expected_generation"] == generation - 1
                and validation["proposal_digest"] == row["policy_digest"]
                and validation["bootstrap_digest"] == self._bootstrap_digest
                and validation["validator_digest"] == proposal["validator_digest"]
                and self._validator_profile_matches(validation["validator_digest"], allow_predecessor=True)
                and now < validation["expires_at"]
                and validation["permission_generation"] == self._permission_generation(db)
                and db.execute("SELECT 1 FROM revocations WHERE entity_type='validation' AND entity_id=?", (row["validation_id"],)).fetchone() is None
                and not self._actor_revoked(db, validation["actor_id"])
                and not self._actor_revoked(db, row["actor_id"])
            )
        return {"series_id": series_id, "generation": generation, "adopted": True,
                "valid": valid, "policy": validated, "proposal_id": row["proposal_id"],
                "validation_id": row["validation_id"], "policy_digest": row["policy_digest"],
                "adopted_at": row["adopted_at"], "ci_eligible": False}

    def _receipt(self, db: sqlite3.Connection, request: dict[str, Any]) -> dict[str, Any]:
        adoption_request_id = request["adoption_request_id"]
        row = db.execute("SELECT * FROM idempotency WHERE request_id=?", (adoption_request_id,)).fetchone()
        if row is None:
            raise _error("RECEIPT_MISSING")
        # A receipt is a projection of the immutable adopt idempotency record.
        # Verify both digests before exposing the stored response; otherwise a
        # damaged row could be returned as an apparently valid audit receipt.
        _validate_digest(row["request_digest"])
        if (type(row["actor_id"]) is not str or type(row["context"]) is not str
                or type(row["response_json"]) is not str):
            raise _error("STORAGE_CORRUPT")
        if row["response_digest"] is None:
            raise _error("STORAGE_CORRUPT")
        _validate_digest(row["response_digest"])
        receipt = _decode_stored(row["response_json"])
        raw, digest = _digest(receipt)
        if raw.decode("utf-8") != row["response_json"] or digest != row["response_digest"]:
            raise _error("STORAGE_CORRUPT")
        if (receipt.get("action") != "adopt"
                or receipt.get("request_id") != adoption_request_id):
            raise _error("RECEIPT_INVALID")
        return self._result("receipt", request["request_id"], adoption_request_id=adoption_request_id,
                            receipt=receipt)

    def _revoke_actor(self, db: sqlite3.Connection, request: dict[str, Any], actor_id: str, now: int) -> dict[str, Any]:
        target = request["actor_id"]
        if target not in {value[0] for value in _ACTORS.values()}:
            raise _error("INVALID_ACTOR")
        existing = db.execute("SELECT generation FROM revocations WHERE entity_type='actor' AND entity_id=?", (target,)).fetchone()
        if existing is not None:
            raise _error("ACTOR_REVOKED")
        generation = self._permission_generation(db)
        if generation >= MAX_INTEGER:
            raise _error("GENERATION_EXHAUSTED")
        generation += 1
        db.execute("UPDATE adoption_meta SET value=? WHERE key='permission_generation'", (generation,))
        db.execute("INSERT INTO revocations VALUES(?,?,?,?)", ("actor", target, generation, now))
        return self._result("revoke_actor", request["request_id"], actor_id=target,
                            permission_generation=generation, revoked_at=now, revoked_by=actor_id)

    def _revoke_validation(self, db: sqlite3.Connection, request: dict[str, Any], now: int) -> dict[str, Any]:
        validation_id = request["validation_id"]
        if db.execute("SELECT 1 FROM validations WHERE validation_id=?", (validation_id,)).fetchone() is None:
            raise _error("VALIDATION_MISSING")
        if db.execute("SELECT 1 FROM revocations WHERE entity_type='validation' AND entity_id=?", (validation_id,)).fetchone() is not None:
            raise _error("VALIDATION_REVOKED")
        generation = self._permission_generation(db)
        if generation >= MAX_INTEGER:
            raise _error("GENERATION_EXHAUSTED")
        generation += 1
        db.execute("UPDATE adoption_meta SET value=? WHERE key='permission_generation'", (generation,))
        db.execute("INSERT INTO revocations VALUES(?,?,?,?)", ("validation", validation_id, generation, now))
        return self._result("revoke_validation", request["request_id"], validation_id=validation_id,
                            permission_generation=generation, revoked_at=now)


__all__ = ["AdoptionError", "AdoptionStore", "VALIDATION_TTL"]
