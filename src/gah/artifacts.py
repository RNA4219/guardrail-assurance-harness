"""許可済み構造化artifactと証拠時刻を保存する。OSの認証境界は呼出側の責任。"""
from contextlib import contextmanager
import hashlib
from pathlib import Path
import sqlite3
import time
from typing import Callable, Iterator, Mapping

from .contracts import (ContractError, MAX_INTEGER, decode_document, require_id,
                        require_digest, require_object, require_ref, require_uint)
from .wire import canonical_bytes

RETENTION_SECONDS = 90 * 86400
FRESHNESS_SECONDS = 86400
BASELINE_SECONDS = 30 * 86400
_KINDS = {"control_registry": "registry_id", "case_set": "case_set_id"}
_REASONS = {"USER_WITHDRAWN", "DATA_POLICY", "RETENTION_EXPIRED", "SUPERSEDED"}
_EVIDENCE_KEYS = {"evidence_id", "subject_ref", "artifact_ref", "conditions_ref", "producer_ref",
                  "observed_at", "collected_at", "retention_until"}


class ArtifactError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _validate_document(kind: str, identifier: str, raw: bytes) -> tuple[dict, bytes, str]:
    require_id(kind)
    require_id(identifier)
    if kind not in _KINDS:
        raise ContractError("UNSUPPORTED_ARTIFACT_KIND")
    value = decode_document(raw)
    if kind == "control_registry":
        from .registry import validate_registry
        value = validate_registry(value)
    else:
        from .corpus import validate_case_set
        value = validate_case_set(value)
    if value[_KINDS[kind]] != identifier:
        raise ContractError("ARTIFACT_BINDING")
    canonical = canonical_bytes(value)
    return value, canonical, hashlib.sha256(canonical).hexdigest()


def _validate_evidence(value: dict) -> None:
    require_object(value, _EVIDENCE_KEYS)
    require_id(value["evidence_id"])
    for key in ("subject_ref", "artifact_ref", "conditions_ref", "producer_ref"):
        require_ref(value[key])
    for key in ("observed_at", "collected_at", "retention_until"):
        require_uint(value[key])
    if not value["observed_at"] <= value["collected_at"] <= value["retention_until"]:
        raise ContractError("EVIDENCE_TIME")
    if value["retention_until"] - value["observed_at"] > RETENTION_SECONDS:
        raise ContractError("EVIDENCE_RETENTION")


class ArtifactStore:
    """許可表はtrustedな監督が構成する。入力本文から許可表を作らない。"""

    def __init__(self, path: str | Path, *, allowed_artifacts: Mapping[tuple[str, str], str],
                 clock: Callable[[], int] | None = None):
        self._allowed = dict(allowed_artifacts)
        for key, digest in self._allowed.items():
            if type(key) is not tuple or len(key) != 2 or key[0] not in _KINDS:
                raise ContractError("INVALID_ADMISSION_POLICY")
            require_id(key[1])
            require_digest(digest)
        self._clock = clock or (lambda: int(time.time()))
        self._db = None
        try:
            db = sqlite3.connect(str(Path(path)), isolation_level=None, timeout=5)
            self._db = db
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA secure_delete=ON")
            with self._transaction() as connection:
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if version == 0 and not tables:
                    connection.execute("CREATE TABLE artifact_meta(key TEXT PRIMARY KEY, value INTEGER NOT NULL)")
                    connection.executemany("INSERT INTO artifact_meta VALUES(?,?)", [("last_clock", -1), ("generation", 0)])
                    connection.execute("CREATE TABLE artifacts(kind TEXT, id TEXT, payload BLOB, digest TEXT NOT NULL, PRIMARY KEY(kind,id))")
                    connection.execute("CREATE TABLE evidence(id TEXT PRIMARY KEY, artifact_kind TEXT, artifact_id TEXT, artifact_digest TEXT, payload BLOB NOT NULL, digest TEXT NOT NULL, FOREIGN KEY(artifact_kind,artifact_id) REFERENCES artifacts(kind,id))")
                    connection.execute("CREATE TABLE revocations(entity_type TEXT, kind TEXT, id TEXT, reason TEXT NOT NULL, observed_at INTEGER NOT NULL, generation INTEGER NOT NULL, PRIMARY KEY(entity_type,kind,id))")
                    connection.execute("PRAGMA user_version=1")
                elif version != 1 or tables != {"artifact_meta", "artifacts", "evidence", "revocations"}:
                    raise ArtifactError("UNSUPPORTED_STORE")
                # 名前だけ同じ異種/途中Schemaもopen時に拒否する。
                required = {"artifact_meta": {"key", "value"}, "artifacts": {"kind", "id", "payload", "digest"},
                            "evidence": {"id", "artifact_kind", "artifact_id", "artifact_digest", "payload", "digest"},
                            "revocations": {"entity_type", "kind", "id", "reason", "observed_at", "generation"}}
                for table, columns in required.items():
                    found = {row[1] for row in connection.execute('PRAGMA table_info("' + table + '")')}
                    if found != columns:
                        raise ArtifactError("UNSUPPORTED_STORE")
                self._meta(connection, "generation")
                self._meta(connection, "last_clock", clock=True)
        except BaseException as error:
            self.close()
            if isinstance(error, (sqlite3.Error, OSError)):
                raise ArtifactError("STORAGE_FAILURE") from None
            raise

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        if self._db is None:
            raise ArtifactError("STORE_CLOSED")
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
                raise ArtifactError("STORAGE_FAILURE") from None
            raise

    def __enter__(self) -> "ArtifactStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        if self._db is not None:
            self._db.close()
            self._db = None

    @staticmethod
    def _meta(db: sqlite3.Connection, key: str, *, clock: bool = False) -> int:
        row = db.execute("SELECT value FROM artifact_meta WHERE key=?", (key,)).fetchone()
        if row is None or type(row[0]) is not int or not (-1 if clock else 0) <= row[0] <= MAX_INTEGER:
            raise ArtifactError("STORAGE_CORRUPT")
        return row[0]

    def _now(self, db: sqlite3.Connection) -> int:
        try:
            now = self._clock()
        except Exception:
            raise ArtifactError("CLOCK_UNAVAILABLE") from None
        require_uint(now)
        if now < self._meta(db, "last_clock", clock=True):
            raise ArtifactError("CLOCK_ROLLBACK")
        db.execute("UPDATE artifact_meta SET value=? WHERE key='last_clock'", (now,))
        return now

    @staticmethod
    def _row(db: sqlite3.Connection, ref: dict) -> sqlite3.Row:
        row = db.execute("SELECT * FROM artifacts WHERE kind=? AND id=?", (ref["kind"], ref["id"])).fetchone()
        if row is None:
            raise ArtifactError("ARTIFACT_MISSING")
        if row["digest"] != ref["digest"]:
            raise ArtifactError("ARTIFACT_BINDING")
        return row

    @staticmethod
    def _verified_artifact(row: sqlite3.Row) -> dict:
        if row["payload"] is None:
            raise ArtifactError("ARTIFACT_DELETED")
        try:
            value, canonical, digest = _validate_document(row["kind"], row["id"], row["payload"])
            if canonical != row["payload"] or digest != row["digest"]:
                raise ContractError()
            return value
        except (ContractError, KeyError, TypeError):
            raise ArtifactError("STORAGE_CORRUPT") from None

    def put(self, kind: str, identifier: str, raw: bytes) -> dict:
        _, canonical, digest = _validate_document(kind, identifier, raw)
        if self._allowed.get((kind, identifier)) != digest:
            raise ArtifactError("DATA_NOT_ADMITTED")
        ref = {"kind": kind, "id": identifier, "digest": digest}
        with self._transaction() as db:
            self._now(db)
            row = db.execute("SELECT * FROM artifacts WHERE kind=? AND id=?", (kind, identifier)).fetchone()
            if row is not None:
                self._verified_artifact(row)
                if row["payload"] != canonical or row["digest"] != digest:
                    raise ArtifactError("ARTIFACT_CONFLICT")
            else:
                db.execute("INSERT INTO artifacts VALUES(?,?,?,?)", (kind, identifier, canonical, digest))
        return ref

    def get(self, ref: dict) -> dict:
        require_ref(ref)
        with self._transaction() as db:
            self._now(db)
            return self._verified_artifact(self._row(db, ref))

    @staticmethod
    def _evidence_row(db: sqlite3.Connection, identifier: str) -> sqlite3.Row:
        row = db.execute("SELECT * FROM evidence WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise ArtifactError("EVIDENCE_MISSING")
        return row

    @staticmethod
    def _verified_evidence(row: sqlite3.Row) -> dict:
        try:
            value = decode_document(row["payload"])
            _validate_evidence(value)
            if (canonical_bytes(value) != row["payload"]
                    or hashlib.sha256(row["payload"]).hexdigest() != row["digest"]
                    or value["evidence_id"] != row["id"]
                    or value["artifact_ref"] != {"kind": row["artifact_kind"], "id": row["artifact_id"], "digest": row["artifact_digest"]}):
                raise ContractError()
            return value
        except (ContractError, KeyError, TypeError):
            raise ArtifactError("STORAGE_CORRUPT") from None

    def record_evidence(self, identifier: str, *, artifact_ref: dict, subject_ref: dict,
                        conditions_ref: dict, producer_ref: dict, observed_at: int,
                        retention_until: int | None = None) -> dict:
        require_id(identifier)
        for ref in (artifact_ref, subject_ref, conditions_ref, producer_ref):
            require_ref(ref)
        require_uint(observed_at)
        if retention_until is None:
            retention_until = observed_at + RETENTION_SECONDS
        require_uint(retention_until)
        with self._transaction() as db:
            now = self._now(db)
            value = {"evidence_id": identifier, "artifact_ref": artifact_ref, "subject_ref": subject_ref,
                     "conditions_ref": conditions_ref, "producer_ref": producer_ref,
                     "observed_at": observed_at, "collected_at": now, "retention_until": retention_until}
            existing = db.execute("SELECT * FROM evidence WHERE id=?", (identifier,)).fetchone()
            if existing is not None:
                previous = self._verified_evidence(existing)
                value["collected_at"] = previous["collected_at"]
                _validate_evidence(value)
                if value != previous:
                    raise ArtifactError("EVIDENCE_CONFLICT")
                return previous  # 元記録だけを返し、有効性を更新しない。
            _validate_evidence(value)
            self._verified_artifact(self._row(db, artifact_ref))
            payload = canonical_bytes(value)
            db.execute("INSERT INTO evidence VALUES(?,?,?,?,?,?)", (identifier, artifact_ref["kind"], artifact_ref["id"], artifact_ref["digest"], payload, hashlib.sha256(payload).hexdigest()))
            return decode_document(payload)

    def read_evidence(self, identifier: str) -> dict:
        require_id(identifier)
        with self._transaction() as db:
            now = self._now(db)
            value = self._verified_evidence(self._evidence_row(db, identifier))
            return {"evidence": value, "use": self._validity(db, value, now, baseline=False)}

    def _validity(self, db: sqlite3.Connection, value: dict, now: int, *, baseline: bool) -> dict:
        identifier = value["evidence_id"]
        row = self._row(db, value["artifact_ref"])
        revoked = db.execute("SELECT 1 FROM revocations WHERE entity_type='evidence' AND kind='evidence' AND id=?", (identifier,)).fetchone()
        until = min(value["retention_until"], value["observed_at"] + (BASELINE_SECONDS if baseline else FRESHNESS_SECONDS))
        if row["payload"] is None:
            state = "DELETED"
        else:
            self._verified_artifact(row)
            state = "REVOKED" if revoked else "EXPIRED" if now > until else "VALID"
        return {"evidence_id": identifier, "state": state, "valid": state == "VALID",
                "checked_at": now, "valid_until": until, "revocation_generation": self._meta(db, "generation"),
                "scope": "artifact_integrity_and_time", "ci_eligible": False}

    def check_evidence(self, identifier: str, *, baseline: bool = False) -> dict:
        require_id(identifier)
        if type(baseline) is not bool:
            raise ContractError()
        with self._transaction() as db:
            now = self._now(db)
            value = self._verified_evidence(self._evidence_row(db, identifier))
            return self._validity(db, value, now, baseline=baseline)

    def _revoke(self, db: sqlite3.Connection, entity: str, kind: str, identifier: str, reason: str, now: int) -> int:
        row = db.execute("SELECT generation FROM revocations WHERE entity_type=? AND kind=? AND id=?", (entity, kind, identifier)).fetchone()
        if row is not None:
            return row[0]
        generation = self._meta(db, "generation") + 1
        if generation > MAX_INTEGER:
            raise ArtifactError("GENERATION_EXHAUSTED")
        db.execute("INSERT INTO revocations VALUES(?,?,?,?,?,?)", (entity, kind, identifier, reason, now, generation))
        db.execute("UPDATE artifact_meta SET value=? WHERE key='generation'", (generation,))
        return generation

    def revoke_evidence(self, identifier: str, *, reason: str = "USER_WITHDRAWN") -> int:
        require_id(identifier)
        if type(reason) is not str or reason not in _REASONS:
            raise ContractError()
        with self._transaction() as db:
            now = self._now(db)
            self._verified_evidence(self._evidence_row(db, identifier))
            return self._revoke(db, "evidence", "evidence", identifier, reason, now)

    def delete_artifact(self, ref: dict, *, reason: str = "USER_WITHDRAWN") -> int:
        require_ref(ref)
        if type(reason) is not str or reason not in _REASONS:
            raise ContractError()
        with self._transaction() as db:
            now = self._now(db)
            self._row(db, ref)
            generation = self._revoke(db, "artifact", ref["kind"], ref["id"], reason, now)
            db.execute("UPDATE artifacts SET payload=NULL WHERE kind=? AND id=?", (ref["kind"], ref["id"]))
            return generation
