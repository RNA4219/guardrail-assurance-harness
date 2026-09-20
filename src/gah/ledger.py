"""SQLiteによるrun・lease・予算・reportの永続台帳。

決定的なローカル台帳だけを実装し、OS主体の認証や外部provider実行は
実装しない。
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from .sqlite_limits import connect_sqlite
import time
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Iterator


_SCHEMA_VERSION = 2
_LEASE_SECONDS = 60
_WINDOW_SECONDS = 86_400
_RUN_LIMITS = {"pr": 2_000_000, "full": 10_000_000}
_GLOBAL_LIMIT = 20_000_000
_MAX_INT64 = 2**63 - 1
_MAX_TIME = 2**53 - 1
_ID_RE = re.compile(r"^[^\x00\s]{1,128}$")
_OWNER_RE = re.compile(r"^[^\x00\s]{1,256}$")
_MAX_REPORT_BYTES = 4 * 1024 * 1024
_ASSURANCE_STATES = frozenset({"HEALTHY", "WARNING", "DEGRADED", "UNKNOWN", "HOLD"})


class LedgerError(Exception):
    """決定的な台帳拒否。``code``は安定した分類、本文は固定理由とする。"""

    def __init__(self, code: str, reason: str) -> None:
        self.code = code
        self.reason = reason
        super().__init__(reason)


class Ledger:
    """*path* のSQLite DBを使う永続台帳。"""

    def __init__(self, path: str | Path, *, clock: Callable[[], int] | None = None):
        if not isinstance(path, (str, Path)):
            raise LedgerError("invalid_input", "invalid input")
        self.path = Path(path)
        self._clock = clock or (lambda: int(time.time()))
        self._closed = False
        existed = self.path.exists()
        try:
            self._db = connect_sqlite(str(self.path), timeout=5.0, isolation_level=None)
            self._db.row_factory = sqlite3.Row
            self._db.execute("PRAGMA foreign_keys = ON")
            self._db.execute("PRAGMA busy_timeout = 5000")
            self._open_or_initialize(existed)
        except LedgerError:
            self._close_quietly()
            raise
        except (sqlite3.Error, OSError) as exc:
            self._close_quietly()
            raise LedgerError("storage_failure", "storage failure") from exc
        except BaseException:
            self._close_quietly()
            raise

    def _open_or_initialize(self, existed: bool) -> None:
        version = int(self._db.execute("PRAGMA user_version").fetchone()[0])
        if not existed:
            if version != 0:
                raise LedgerError("unsupported_version", "unsupported database version")
            self._create_schema()
            return
        # 既存ファイルは修復・再初期化しない。空ファイルやテーブル欠落も
        # 異常DBとして拒否する。
        if version != _SCHEMA_VERSION:
            raise LedgerError("unsupported_version", "unsupported database version")
        self._validate_v2_connection(self._db)

    def _create_schema(self) -> None:
        try:
            self._db.execute("BEGIN IMMEDIATE")
            # 未存在判定の直後に別接続が初期化した場合は、検証済みの
            # 完成DBをそのまま利用する。壊れたDBは修復しない。
            current_version = int(self._db.execute("PRAGMA user_version").fetchone()[0])
            table_names = {
                row[0]
                for row in self._db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            required = {"ledger_meta", "runs", "operations", "reports"}
            if current_version == _SCHEMA_VERSION and required.issubset(table_names):
                self._validate_v2_connection(self._db)
                self._db.commit()
                return
            if current_version != 0 or table_names:
                raise LedgerError("invalid_database", "invalid database")
            statements = [
                """CREATE TABLE ledger_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )""",
                """CREATE TABLE runs (
                    run_id TEXT PRIMARY KEY,
                    profile TEXT NOT NULL,
                    started_at INTEGER NOT NULL,
                    deadline INTEGER NOT NULL,
                    epoch0 INTEGER NOT NULL DEFAULT 0,
                    owner TEXT,
                    owner_epoch INTEGER NOT NULL DEFAULT 0,
                    lease_until INTEGER,
                    lease_kind TEXT NOT NULL DEFAULT 'execution',
                    cancel_requested_at INTEGER,
                    stop_confirmed_at INTEGER,
                    hold INTEGER NOT NULL DEFAULT 0,
                    hold_reason TEXT
                )""",
                """CREATE TABLE operations (
                    operation_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    owner TEXT NOT NULL,
                    epoch INTEGER NOT NULL,
                    reserved_usd TEXT NOT NULL,
                    reserved_micros INTEGER NOT NULL,
                    reserved_at INTEGER NOT NULL,
                    settled_usd TEXT,
                    settled_micros INTEGER,
                    settled_at INTEGER,
                    status TEXT NOT NULL,
                    hold_reason TEXT,
                    exposure_usd TEXT,
                    exposure_micros INTEGER
                )""",
                "CREATE INDEX operations_run_idx ON operations(run_id)",
                "CREATE INDEX operations_settled_idx ON operations(settled_at)",
                """CREATE TABLE reports (
                    request_id TEXT PRIMARY KEY,
                    canonical_json TEXT NOT NULL,
                    payload BLOB NOT NULL,
                    sha256 TEXT NOT NULL
                )""",
                """CREATE TABLE terminal_records (
                    run_id TEXT PRIMARY KEY REFERENCES runs(run_id),
                    canonical_json TEXT NOT NULL,
                    payload BLOB NOT NULL,
                    sha256 TEXT NOT NULL
                )""",
                """CREATE TABLE run_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    event_kind TEXT NOT NULL,
                    observed_at INTEGER NOT NULL,
                    owner TEXT,
                    owner_epoch INTEGER,
                    details_json TEXT NOT NULL
                )""",
                "CREATE INDEX run_events_run_idx ON run_events(run_id, event_id)",
                "INSERT INTO ledger_meta(key, value) VALUES ('last_clock', '-1')",
            ]
            for statement in statements:
                self._db.execute(statement)
            self._db.execute("PRAGMA user_version = 2")
            self._db.commit()
        except LedgerError:
            try:
                self._db.rollback()
            except Exception:
                pass
            raise
        except Exception as exc:
            try:
                self._db.rollback()
            except Exception:
                pass
            raise LedgerError("storage_failure", "storage failure") from exc
        except BaseException:
            try:
                self._db.rollback()
            except Exception:
                pass
            raise

    @staticmethod
    def _verify_report_parts(canonical: Any, payload: Any, digest: Any) -> dict[str, Any]:
        try:
            expected = canonical.encode("utf-8")
        except (AttributeError, UnicodeError) as exc:
            raise LedgerError("invalid_database", "invalid database") from exc
        if not isinstance(payload, bytes) or payload != expected:
            raise LedgerError("invalid_database", "invalid database")
        if hashlib.sha256(payload).hexdigest() != digest:
            raise LedgerError("invalid_database", "invalid database")
        try:
            value = json.loads(canonical)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise LedgerError("invalid_database", "invalid database") from exc
        if not isinstance(value, dict):
            raise LedgerError("invalid_database", "invalid database")
        return value

    @classmethod
    def _validate_v2_connection(
        cls, db: sqlite3.Connection, *, verify_reports: bool = False
    ) -> None:
        names = {
            row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        required = {"ledger_meta", "runs", "operations", "reports", "terminal_records", "run_events"}
        if not required.issubset(names):
            raise LedgerError("invalid_database", "invalid database")
        expected = {
            "ledger_meta": {"key", "value"},
            "runs": {"run_id", "profile", "started_at", "deadline", "epoch0", "owner", "owner_epoch", "lease_until", "lease_kind", "cancel_requested_at", "stop_confirmed_at", "hold", "hold_reason"},
            "operations": {"operation_id", "run_id", "owner", "epoch", "reserved_usd", "reserved_micros", "reserved_at", "settled_usd", "settled_micros", "settled_at", "status", "hold_reason", "exposure_usd", "exposure_micros"},
            "reports": {"request_id", "canonical_json", "payload", "sha256"},
            "terminal_records": {"run_id", "canonical_json", "payload", "sha256"},
            "run_events": {"event_id", "run_id", "event_kind", "observed_at", "owner", "owner_epoch", "details_json"},
        }
        for table, columns in expected.items():
            actual = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
            if not columns.issubset(actual):
                raise LedgerError("invalid_database", "invalid database")
        clock = db.execute(
            "SELECT value FROM ledger_meta WHERE key='last_clock'"
        ).fetchone()
        if clock is None or not cls._valid_clock_storage(clock[0]):
            raise LedgerError("invalid_database", "invalid database")
        if verify_reports:
            for row in db.execute("SELECT canonical_json,payload,sha256 FROM reports"):
                cls._verify_report_parts(row[0], row[1], row[2])
            for row in db.execute("SELECT canonical_json,payload,sha256 FROM terminal_records"):
                cls._verify_report_parts(row[0], row[1], row[2])

    @staticmethod
    def _valid_clock_storage(value: Any) -> bool:
        if isinstance(value, bool):
            return False
        try:
            text = str(value)
            if not re.fullmatch(r"-?(?:0|[1-9][0-9]*)", text):
                return False
            number = int(text)
        except (TypeError, ValueError, OverflowError):
            return False
        return -1 <= number <= _MAX_TIME

    @classmethod
    def _validate_v1_connection(cls, db: sqlite3.Connection) -> None:
        """変更前のv1構造と保存reportを検査する。中間状態は受け入れない。"""
        names = {
            row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if {"terminal_records", "run_events"} & names:
            raise LedgerError("invalid_database", "invalid database")
        required = {"ledger_meta", "runs", "operations", "reports"}
        if not required.issubset(names):
            raise LedgerError("invalid_database", "invalid database")
        expected = {
            "ledger_meta": {"key", "value"},
            "runs": {
                "run_id", "profile", "started_at", "deadline", "epoch0", "owner",
                "owner_epoch", "lease_until", "hold", "hold_reason",
            },
            "operations": {
                "operation_id", "run_id", "owner", "epoch", "reserved_usd",
                "reserved_micros", "reserved_at", "settled_usd", "settled_micros",
                "settled_at", "status", "hold_reason", "exposure_usd", "exposure_micros",
            },
            "reports": {"request_id", "canonical_json", "payload", "sha256"},
        }
        for table, columns in expected.items():
            actual = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
            if not columns.issubset(actual):
                raise LedgerError("invalid_database", "invalid database")
        run_columns = {row[1] for row in db.execute("PRAGMA table_info(runs)")}
        if {"lease_kind", "cancel_requested_at", "stop_confirmed_at"} & run_columns:
            raise LedgerError("invalid_database", "invalid database")
        clock = db.execute(
            "SELECT value FROM ledger_meta WHERE key='last_clock'"
        ).fetchone()
        if clock is None or not cls._valid_clock_storage(clock[0]):
            raise LedgerError("invalid_database", "invalid database")
        for row in db.execute("SELECT canonical_json,payload,sha256 FROM reports"):
            cls._verify_report_parts(row[0], row[1], row[2])

    @classmethod
    def migrate_v1(cls, path: str | Path) -> dict[str, Any]:
        """明示指定された既存v1 DBだけを単一transactionでv2へ移行する。"""
        if not isinstance(path, (str, Path)):
            raise LedgerError("invalid_input", "invalid input")
        target = Path(path)
        if not target.is_file():
            raise LedgerError("invalid_database", "invalid database")
        db: sqlite3.Connection | None = None
        try:
            db = connect_sqlite(str(target), timeout=5.0, isolation_level=None)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys = ON")
            # 版・構造・保存内容の検査をロック取得後に行い、検査と変更を
            # 同一transactionへ収める。
            db.execute("BEGIN IMMEDIATE")
            version = int(db.execute("PRAGMA user_version").fetchone()[0])
            if version == _SCHEMA_VERSION:
                cls._validate_v2_connection(db, verify_reports=True)
                db.commit()
                return {"schema_version": 2, "changed": False}
            if version != 1:
                raise LedgerError("unsupported_version", "unsupported database version")
            cls._validate_v1_connection(db)
            # transaction開始後に版を再確認し、並行移行を二重適用しない。
            if int(db.execute("PRAGMA user_version").fetchone()[0]) != 1:
                raise LedgerError("invalid_database", "invalid database")
            run_columns = {row[1] for row in db.execute("PRAGMA table_info(runs)")}
            for column, definition in (
                ("lease_kind", "TEXT NOT NULL DEFAULT 'execution'"),
                ("cancel_requested_at", "INTEGER"),
                ("stop_confirmed_at", "INTEGER"),
            ):
                if column not in run_columns:
                    db.execute(f"ALTER TABLE runs ADD COLUMN {column} {definition}")
            db.execute(
                """CREATE TABLE terminal_records (
                    run_id TEXT PRIMARY KEY REFERENCES runs(run_id),
                    canonical_json TEXT NOT NULL,
                    payload BLOB NOT NULL,
                    sha256 TEXT NOT NULL
                )"""
            )
            db.execute(
                """CREATE TABLE run_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    event_kind TEXT NOT NULL,
                    observed_at INTEGER NOT NULL,
                    owner TEXT,
                    owner_epoch INTEGER,
                    details_json TEXT NOT NULL
                )"""
            )
            db.execute("CREATE INDEX run_events_run_idx ON run_events(run_id, event_id)")
            db.execute("PRAGMA user_version = 2")
            db.commit()
            return {"schema_version": 2, "changed": True}
        except LedgerError:
            if db is not None:
                try:
                    db.rollback()
                except Exception:
                    pass
            raise
        except Exception as exc:
            if db is not None:
                try:
                    db.rollback()
                except Exception:
                    pass
            raise LedgerError("storage_failure", "storage failure") from exc
        except BaseException:
            if db is not None:
                try:
                    db.rollback()
                except Exception:
                    pass
            raise
        finally:
            if db is not None:
                try:
                    db.close()
                except Exception:
                    pass

    def __enter__(self) -> "Ledger":
        self._ensure_open()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    def close(self) -> None:
        if not self._closed:
            self._close_quietly()

    def _close_quietly(self) -> None:
        db = getattr(self, "_db", None)
        self._closed = True
        if db is not None:
            try:
                db.close()
            except Exception:
                pass

    def _ensure_open(self) -> None:
        if self._closed:
            raise LedgerError("closed", "ledger is closed")

    @contextmanager
    def _immediate(self) -> Iterator[sqlite3.Connection]:
        self._ensure_open()
        try:
            self._db.execute("BEGIN IMMEDIATE")
            yield self._db
            self._db.commit()
        except LedgerError:
            try:
                self._db.rollback()
            except Exception:
                pass
            raise
        except Exception as exc:
            try:
                self._db.rollback()
            except Exception:
                pass
            raise LedgerError("storage_failure", "storage failure") from exc
        except BaseException:
            try:
                self._db.rollback()
            except Exception:
                pass
            raise

    def _now(self, db: sqlite3.Connection) -> int:
        try:
            value = self._clock()
        except Exception as exc:  # clock障害は成功扱いにしない
            raise LedgerError("clock_invalid", "invalid clock") from exc
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
            or value > _MAX_TIME
        ):
            raise LedgerError("clock_invalid", "invalid clock")
        row = db.execute("SELECT value FROM ledger_meta WHERE key='last_clock'").fetchone()
        if row is None:
            raise LedgerError("invalid_database", "invalid database")
        try:
            previous = int(row[0])
        except (TypeError, ValueError) as exc:
            raise LedgerError("invalid_database", "invalid database") from exc
        if value < previous:
            raise LedgerError("clock_rollback", "clock rollback")
        db.execute(
            "UPDATE ledger_meta SET value=? WHERE key='last_clock'", (str(value),)
        )
        return value

    @staticmethod
    def _validate_id(value: Any, *, request: bool = False) -> str:
        if not isinstance(value, str) or not _ID_RE.fullmatch(value):
            raise LedgerError("invalid_input", "invalid input")
        return value

    @staticmethod
    def _validate_owner(value: Any) -> str:
        if not isinstance(value, str) or not _OWNER_RE.fullmatch(value):
            raise LedgerError("invalid_input", "invalid input")
        return value

    @staticmethod
    def _parse_usd(value: Any) -> tuple[str, int]:
        if not isinstance(value, str) or len(value) > 128 or not value:
            raise LedgerError("invalid_amount", "invalid amount")
        if value == "0":
            return value, 0
        # 指数表記を受け入れるとwire表現が曖昧になるため、十進表記を固定する。
        if not re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?", value):
            raise LedgerError("invalid_amount", "invalid amount")
        # 小数の桁数に依存しない整数演算で、百万分の一への切上げを
        # 行う。Decimalの既定precisionによる丸めは判定に使わない。
        integer, _, fraction = value.partition(".")
        fraction = fraction or ""
        try:
            whole_micros = int(integer) * 1_000_000
            fractional_micros = int((fraction + "000000")[:6] or "0")
        except (ValueError, OverflowError) as exc:
            raise LedgerError("invalid_amount", "invalid amount") from exc
        remainder = fraction[6:]
        micros = whole_micros + fractional_micros
        if any(char != "0" for char in remainder):
            micros += 1
        if micros <= 0 or micros > _MAX_INT64:
            raise LedgerError("invalid_amount", "invalid amount")
        return value, micros

    @staticmethod
    def _decimal_equal(left: str, right: str) -> bool:
        try:
            return Decimal(left) == Decimal(right)
        except (InvalidOperation, TypeError, ValueError):
            return False

    @staticmethod
    def _run_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "run_id": row["run_id"],
            "profile": row["profile"],
            "started_at": row["started_at"],
            "deadline": row["deadline"],
            "epoch0": row["epoch0"],
            "owner": row["owner"],
            "epoch": row["owner_epoch"],
            "owner_epoch": row["owner_epoch"],
            "lease_until": row["lease_until"],
            "lease_kind": row["lease_kind"],
            "cancel_requested_at": row["cancel_requested_at"],
            "stop_confirmed_at": row["stop_confirmed_at"],
            "hold": bool(row["hold"]),
            "hold_reason": row["hold_reason"],
        }

    @staticmethod
    def _operation_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "operation_id": row["operation_id"],
            "run_id": row["run_id"],
            "owner": row["owner"],
            "epoch": row["epoch"],
            "reserved_usd": row["reserved_usd"],
            "reserved_micros": row["reserved_micros"],
            "reserved_at": row["reserved_at"],
            "settled_usd": row["settled_usd"],
            "settled_micros": row["settled_micros"],
            "settled_at": row["settled_at"],
            "status": row["status"],
            "hold_reason": row["hold_reason"],
            "exposure_usd": row["exposure_usd"],
            "exposure_micros": row["exposure_micros"],
        }

    def _get_run(self, db: sqlite3.Connection, run_id: str) -> sqlite3.Row:
        row = db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            raise LedgerError("not_found", "run not found")
        return row

    @staticmethod
    def _record_event(
        db: sqlite3.Connection,
        run_id: str,
        event_kind: str,
        observed_at: int,
        owner: str | None,
        owner_epoch: int | None,
        details: dict[str, Any],
    ) -> None:
        try:
            details_json = json.dumps(
                details, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"), allow_nan=False,
            )
        except (TypeError, ValueError, UnicodeError) as exc:
            raise LedgerError("invalid_event", "invalid event") from exc
        if len(details_json.encode("utf-8")) > 64 * 1024:
            raise LedgerError("invalid_event", "invalid event")
        db.execute(
            """INSERT INTO run_events(
                run_id,event_kind,observed_at,owner,owner_epoch,details_json
            ) VALUES(?,?,?,?,?,?)""",
            (run_id, event_kind, observed_at, owner, owner_epoch, details_json),
        )

    def _require_lease(
        self,
        db: sqlite3.Connection,
        run_id: str,
        owner: str,
        epoch: Any,
        now: int,
        *,
        allow_after_deadline: bool,
        expected_kind: str | None = None,
    ) -> sqlite3.Row:
        if (
            isinstance(epoch, bool)
            or not isinstance(epoch, int)
            or epoch < 1
            or epoch > _MAX_INT64
        ):
            raise LedgerError("invalid_input", "invalid input")
        row = self._get_run(db, run_id)
        if expected_kind is not None and row["lease_kind"] != expected_kind:
            raise LedgerError("lease_kind_invalid", "lease kind invalid")
        if row["owner"] != owner or row["owner_epoch"] != epoch:
            raise LedgerError("lease_invalid", "lease invalid")
        lease_until = row["lease_until"]
        if lease_until is None or now >= lease_until:
            raise LedgerError("lease_expired", "lease expired")
        if not allow_after_deadline and now >= row["deadline"]:
            raise LedgerError("deadline_exceeded", "deadline exceeded")
        if allow_after_deadline and now >= row["deadline"] and row["lease_kind"] != "recovery":
            raise LedgerError("lease_kind_invalid", "lease kind invalid")
        return row

    def create_run(self, run_id: str, profile: str = "pr") -> dict[str, Any]:
        run_id = self._validate_id(run_id)
        if not isinstance(profile, str) or profile not in _RUN_LIMITS:
            raise LedgerError("invalid_input", "invalid input")
        with self._immediate() as db:
            now = self._now(db)
            duration = 1200 if profile == "pr" else 5400
            if now > _MAX_TIME - duration:
                raise LedgerError("invalid_input", "invalid input")
            try:
                db.execute(
                    "INSERT INTO runs(run_id,profile,started_at,deadline) VALUES(?,?,?,?)",
                    (run_id, profile, now, now + duration),
                )
            except sqlite3.IntegrityError as exc:
                raise LedgerError("duplicate", "run already exists") from exc
            self._record_event(db, run_id, "run_created", now, None, 0, {"profile": profile})
            return self._run_dict(self._get_run(db, run_id))

    def claim(self, run_id: str, owner: str) -> dict[str, Any]:
        run_id = self._validate_id(run_id)
        owner = self._validate_owner(owner)
        with self._immediate() as db:
            now = self._now(db)
            row = self._get_run(db, run_id)
            if now >= row["deadline"]:
                raise LedgerError("deadline_exceeded", "deadline exceeded")
            if row["lease_kind"] != "execution" or row["cancel_requested_at"] is not None or row["stop_confirmed_at"] is not None or row["hold"]:
                raise LedgerError("execution_closed", "execution closed")
            if db.execute(
                "SELECT 1 FROM terminal_records WHERE run_id=?", (run_id,)
            ).fetchone() is not None:
                raise LedgerError("execution_closed", "execution closed")
            current_epoch = row["owner_epoch"]
            if type(current_epoch) is not int or current_epoch < 0 or current_epoch > _MAX_INT64:
                raise LedgerError("invalid_database", "invalid database")
            current_owner = row["owner"]
            lease_until = row["lease_until"]
            if current_owner is not None and lease_until is not None and now < lease_until:
                if current_owner != owner:
                    raise LedgerError("lease_busy", "lease is held")
                epoch = row["owner_epoch"]
            else:
                epoch = row["owner_epoch"] + 1
            if epoch > _MAX_INT64:
                raise LedgerError("invalid_input", "invalid input")
            new_lease = min(row["deadline"], now + _LEASE_SECONDS)
            db.execute(
                "UPDATE runs SET owner=?, owner_epoch=?, lease_until=?, lease_kind='execution' WHERE run_id=?",
                (owner, epoch, new_lease, run_id),
            )
            self._record_event(db, run_id, "execution_claimed", now, owner, epoch, {"lease_until": new_lease})
            return {
                "run_id": run_id,
                "owner": owner,
                "epoch": epoch,
                "owner_epoch": epoch,
                "lease_until": new_lease,
            }

    def reserve(
        self,
        run_id: str,
        owner: str,
        epoch: int,
        operation_id: str,
        usd: str,
    ) -> dict[str, Any]:
        run_id = self._validate_id(run_id)
        owner = self._validate_owner(owner)
        operation_id = self._validate_id(operation_id)
        reserved_usd, micros = self._parse_usd(usd)
        with self._immediate() as db:
            now = self._now(db)
            run = self._require_lease(
                db, run_id, owner, epoch, now, allow_after_deadline=False,
                expected_kind="execution",
            )
            if run["cancel_requested_at"] is not None or run["stop_confirmed_at"] is not None:
                raise LedgerError("execution_closed", "execution closed")
            if db.execute(
                "SELECT 1 FROM terminal_records WHERE run_id=?", (run_id,)
            ).fetchone() is not None:
                raise LedgerError("execution_closed", "execution closed")
            existing = db.execute(
                "SELECT * FROM operations WHERE operation_id=?", (operation_id,)
            ).fetchone()
            if existing is not None:
                self._validate_operation_row(existing)
                if (
                    existing["run_id"] != run_id
                    or existing["reserved_micros"] != micros
                    or not self._decimal_equal(existing["reserved_usd"], reserved_usd)
                ):
                    raise LedgerError("conflict", "operation binding conflict")
                # 再配送は元レコードを返し、予算も時刻も変更しない。
                return self._operation_dict(existing)
            if run["hold"]:
                raise LedgerError("hold", "run is on hold")
            run_total = self._run_accounted(db, run_id)
            global_total = self._global_accounted(db, now)
            if run_total + micros > _RUN_LIMITS[run["profile"]]:
                raise LedgerError("budget_exceeded", "run budget exceeded")
            if global_total + micros > _GLOBAL_LIMIT:
                raise LedgerError("budget_exceeded", "global budget exceeded")
            db.execute(
                """INSERT INTO operations(
                    operation_id,run_id,owner,epoch,reserved_usd,reserved_micros,
                    reserved_at,status
                ) VALUES(?,?,?,?,?,?,?,?)""",
                (operation_id, run_id, owner, epoch, reserved_usd, micros, now, "reserved"),
            )
            self._record_event(
                db, run_id, "operation_reserved", now, owner, epoch,
                {"operation_id": operation_id, "reserved_micros": micros},
            )
            return self._operation_dict(
                db.execute("SELECT * FROM operations WHERE operation_id=?", (operation_id,)).fetchone()
            )

    def settle(
        self,
        run_id: str,
        owner: str,
        epoch: int,
        operation_id: str,
        usd: str,
    ) -> dict[str, Any]:
        run_id = self._validate_id(run_id)
        owner = self._validate_owner(owner)
        operation_id = self._validate_id(operation_id)
        settled_usd, micros = self._parse_usd(usd)
        deferred_error: LedgerError | None = None
        result: dict[str, Any] | None = None
        with self._immediate() as db:
            now = self._now(db)
            run = self._require_lease(
                db, run_id, owner, epoch, now, allow_after_deadline=True
            )
            op = db.execute(
                "SELECT * FROM operations WHERE operation_id=?", (operation_id,)
            ).fetchone()
            if op is None or op["run_id"] != run_id:
                raise LedgerError("not_found", "operation not found")
            self._validate_operation_row(op)
            if op["status"] == "settled":
                if (
                    op["settled_micros"] == micros
                    and self._decimal_equal(op["settled_usd"], settled_usd)
                ):
                    return self._operation_dict(op)
                # 確定額を変更せず、元の確定額も含めた最大候補を
                # 未解消exposureとして保持する。
                previous_exposure = op["exposure_micros"] or 0
                exposure_micros = max(op["settled_micros"], previous_exposure, micros)
                if exposure_micros == micros:
                    exposure_usd = settled_usd
                elif exposure_micros == op["settled_micros"]:
                    exposure_usd = op["settled_usd"]
                else:
                    exposure_usd = op["exposure_usd"]
                db.execute(
                    """UPDATE operations SET exposure_usd=?, exposure_micros=?
                       WHERE operation_id=?""",
                    (exposure_usd, exposure_micros, operation_id),
                )
                db.execute(
                    "UPDATE runs SET hold=1, hold_reason=? WHERE run_id=?",
                    ("settlement_conflict", run_id),
                )
                if exposure_micros > previous_exposure:
                    self._record_event(
                        db, run_id, "settlement_conflict", now, owner, epoch,
                        {"operation_id": operation_id, "exposure_micros": exposure_micros},
                    )
                # 矛盾を返す前に永続HOLDをcommitする。
                deferred_error = LedgerError("conflict", "settlement conflict")
            elif op["status"] != "reserved":
                raise LedgerError("conflict", "operation state conflict")
            else:
                hold_reason = None
                if micros > op["reserved_micros"]:
                    hold_reason = "settlement_over_reservation"
                db.execute(
                    """UPDATE operations SET settled_usd=?, settled_micros=?,
                        settled_at=?, status='settled', hold_reason=?
                        WHERE operation_id=?""",
                    (settled_usd, micros, now, hold_reason, operation_id),
                )
                if hold_reason is not None:
                    db.execute(
                        "UPDATE runs SET hold=1, hold_reason=? WHERE run_id=?",
                        (hold_reason, run_id),
                    )
                    self._record_event(
                        db, run_id, "operation_settled_over_reservation", now,
                        owner, epoch,
                        {"operation_id": operation_id, "settled_micros": micros},
                    )
                    deferred_error = LedgerError(
                        "budget_violation", "settlement exceeds reservation"
                    )
                else:
                    self._record_event(
                        db, run_id, "operation_settled", now, owner, epoch,
                        {"operation_id": operation_id, "settled_micros": micros},
                    )
                    result = self._operation_dict(
                        db.execute(
                            "SELECT * FROM operations WHERE operation_id=?", (operation_id,)
                        ).fetchone()
                    )
        if deferred_error is not None:
            raise deferred_error
        assert result is not None
        return result

    @staticmethod
    def _valid_timestamp(value: Any) -> bool:
        return (
            type(value) is int
            and 0 <= value <= _MAX_TIME
        )

    @classmethod
    def _validate_operation_row(cls, row: sqlite3.Row) -> None:
        """操作の状態と金額を検査し、壊れた値をCLOSED扱いにしない。"""
        status = row["status"]
        if status not in {"reserved", "settled"}:
            raise LedgerError("invalid_database", "invalid database")
        try:
            _, reserved_micros = cls._parse_usd(row["reserved_usd"])
        except LedgerError as exc:
            raise LedgerError("invalid_database", "invalid database") from exc
        if row["reserved_micros"] != reserved_micros or not cls._valid_timestamp(row["reserved_at"]):
            raise LedgerError("invalid_database", "invalid database")
        settlement_values = (row["settled_usd"], row["settled_micros"], row["settled_at"])
        if status == "reserved":
            if any(value is not None for value in settlement_values):
                raise LedgerError("invalid_database", "invalid database")
        else:
            if any(value is None for value in settlement_values):
                raise LedgerError("invalid_database", "invalid database")
            try:
                _, settled_micros = cls._parse_usd(row["settled_usd"])
            except LedgerError as exc:
                raise LedgerError("invalid_database", "invalid database") from exc
            if row["settled_micros"] != settled_micros or not cls._valid_timestamp(row["settled_at"]):
                raise LedgerError("invalid_database", "invalid database")
        exposure_values = (row["exposure_usd"], row["exposure_micros"])
        if (exposure_values[0] is None) != (exposure_values[1] is None):
            raise LedgerError("invalid_database", "invalid database")
        if exposure_values[0] is not None:
            try:
                _, exposure_micros = cls._parse_usd(row["exposure_usd"])
            except LedgerError as exc:
                raise LedgerError("invalid_database", "invalid database") from exc
            if row["exposure_micros"] != exposure_micros:
                raise LedgerError("invalid_database", "invalid database")
            # v1では確定額より小さい矛盾候補も保存する。元記録を変えず、
            # 会計では確定額と候補額の大きい方を留保する。
            if status != "settled":
                raise LedgerError("invalid_database", "invalid database")

    @classmethod
    def _run_accounted(cls, db: sqlite3.Connection, run_id: str) -> int:
        total = 0
        for row in db.execute("SELECT * FROM operations WHERE run_id=?", (run_id,)):
            cls._validate_operation_row(row)
            total += (
                max(row["settled_micros"], row["exposure_micros"] or 0)
                if row["status"] == "settled" else row["reserved_micros"]
            )
        if total > _MAX_INT64:
            raise LedgerError("invalid_database", "invalid database")
        return total

    @classmethod
    def _global_accounted(cls, db: sqlite3.Connection, now: int) -> int:
        lower = now - _WINDOW_SECONDS
        total = 0
        for row in db.execute("SELECT * FROM operations"):
            cls._validate_operation_row(row)
            included = (
                row["status"] == "reserved"
                or row["exposure_micros"] is not None
                or (row["settled_at"] > lower and row["settled_at"] <= now)
            )
            if included:
                total += (
                    max(row["settled_micros"], row["exposure_micros"] or 0)
                    if row["status"] == "settled" else row["reserved_micros"]
                )
        if total > _MAX_INT64:
            raise LedgerError("invalid_database", "invalid database")
        return total

    def _budget_closure(
        self, db: sqlite3.Connection, run_id: str, now: int
    ) -> dict[str, Any]:
        """操作状態を検査したうえで、runとrolling24hの精算状況を返す。"""
        self._get_run(db, run_id)
        pending = 0
        unresolved = 0
        for row in db.execute("SELECT * FROM operations WHERE run_id=?", (run_id,)):
            self._validate_operation_row(row)
            if row["status"] == "reserved":
                pending += 1
            if row["exposure_micros"] is not None:
                unresolved += 1
        return {
            "status": "CLOSED" if pending == 0 and unresolved == 0 else "OPEN",
            "pending_operations": pending,
            "unresolved_exposures": unresolved,
            "run_accounted_micros": self._run_accounted(db, run_id),
            "global_accounted_micros": self._global_accounted(db, now),
            "financial_only": True,
            "purpose": "component_validation",
            "ci_eligible": False,
        }

    def claim_recovery(self, run_id: str, owner: str) -> dict[str, Any]:
        """期限後・取消し・HOLDの精算回収専用leaseを取得する。"""
        run_id = self._validate_id(run_id)
        owner = self._validate_owner(owner)
        with self._immediate() as db:
            now = self._now(db)
            row = self._get_run(db, run_id)
            terminal = db.execute(
                "SELECT 1 FROM terminal_records WHERE run_id=?", (run_id,)
            ).fetchone() is not None
            eligible = (
                now >= row["deadline"]
                or row["cancel_requested_at"] is not None
                or row["stop_confirmed_at"] is not None
                or row["hold"]
                or terminal
            )
            if not eligible:
                raise LedgerError("recovery_not_allowed", "recovery not allowed")
            current_valid = (
                row["owner"] is not None
                and row["lease_until"] is not None
                and now < row["lease_until"]
            )
            if current_valid and row["owner"] != owner:
                raise LedgerError("lease_busy", "lease is held")
            current_epoch = row["owner_epoch"]
            if type(current_epoch) is not int or current_epoch < 0 or current_epoch > _MAX_INT64:
                raise LedgerError("invalid_database", "invalid database")
            if current_valid and row["lease_kind"] == "recovery":
                epoch = current_epoch
            else:
                epoch = current_epoch + 1
            if epoch > _MAX_INT64 or now > _MAX_TIME - _LEASE_SECONDS:
                raise LedgerError("invalid_input", "invalid input")
            lease_until = now + _LEASE_SECONDS
            db.execute(
                """UPDATE runs SET owner=?, owner_epoch=?, lease_until=?,
                   lease_kind='recovery' WHERE run_id=?""",
                (owner, epoch, lease_until, run_id),
            )
            self._record_event(
                db, run_id, "recovery_claimed", now, owner, epoch,
                {"lease_until": lease_until},
            )
            return {
                "run_id": run_id, "owner": owner, "epoch": epoch,
                "owner_epoch": epoch, "lease_until": lease_until,
                "lease_kind": "recovery",
            }

    def request_cancel(self, run_id: str, owner: str, epoch: int) -> dict[str, Any]:
        run_id = self._validate_id(run_id)
        owner = self._validate_owner(owner)
        with self._immediate() as db:
            now = self._now(db)
            row = self._require_lease(
                db, run_id, owner, epoch, now, allow_after_deadline=True
            )
            if db.execute("SELECT 1 FROM terminal_records WHERE run_id=?", (run_id,)).fetchone():
                if db.execute(
                    """SELECT 1 FROM run_events
                       WHERE run_id=? AND owner_epoch=? AND event_kind=?""",
                    (run_id, epoch, "cancel_after_terminal"),
                ).fetchone() is None:
                    self._record_event(
                        db, run_id, "cancel_after_terminal", now, owner, epoch, {}
                    )
                return {"run_id": run_id, "status": "already_terminal"}
            if row["cancel_requested_at"] is None:
                db.execute(
                    "UPDATE runs SET cancel_requested_at=? WHERE run_id=?", (now, run_id)
                )
                self._record_event(
                    db, run_id, "cancel_requested", now, owner, epoch, {}
                )
                row = self._get_run(db, run_id)
            return {
                "run_id": run_id, "status": "requested",
                "cancel_requested_at": row["cancel_requested_at"],
            }

    def confirm_stopped(self, run_id: str, owner: str, epoch: int) -> dict[str, Any]:
        run_id = self._validate_id(run_id)
        owner = self._validate_owner(owner)
        with self._immediate() as db:
            now = self._now(db)
            row = self._require_lease(
                db, run_id, owner, epoch, now, allow_after_deadline=True
            )
            if db.execute("SELECT 1 FROM terminal_records WHERE run_id=?", (run_id,)).fetchone():
                if db.execute(
                    """SELECT 1 FROM run_events
                       WHERE run_id=? AND owner_epoch=? AND event_kind=?""",
                    (run_id, epoch, "stop_after_terminal"),
                ).fetchone() is None:
                    self._record_event(
                        db, run_id, "stop_after_terminal", now, owner, epoch, {}
                    )
                return {"run_id": run_id, "status": "already_terminal"}
            if row["stop_confirmed_at"] is None:
                db.execute(
                    "UPDATE runs SET stop_confirmed_at=? WHERE run_id=?", (now, run_id)
                )
                self._record_event(
                    db, run_id, "stopped_confirmed", now, owner, epoch, {}
                )
                row = self._get_run(db, run_id)
            return {
                "run_id": run_id, "status": "confirmed",
                "stop_confirmed_at": row["stop_confirmed_at"],
            }

    def budget_closure(self, run_id: str) -> dict[str, Any]:
        run_id = self._validate_id(run_id)
        with self._immediate() as db:
            now = self._now(db)
            return self._budget_closure(db, run_id, now)

    def finalize(
        self,
        run_id: str,
        owner: str,
        epoch: int,
        *,
        assurance: str,
        work_complete: bool,
        required_failure: bool,
    ) -> dict[str, Any]:
        """現在leaseで終了判定し、終端recordと確定eventを原子的に保存する。"""
        run_id = self._validate_id(run_id)
        owner = self._validate_owner(owner)
        # termination部品の厳密型検査をDB変更より前に通す。
        if (
            type(work_complete) is not bool
            or type(required_failure) is not bool
            or type(assurance) is not str
            or assurance not in _ASSURANCE_STATES
        ):
            raise ValueError("invalid termination input")
        with self._immediate() as db:
            now = self._now(db)
            run = self._require_lease(
                db, run_id, owner, epoch, now, allow_after_deadline=True
            )
            existing = db.execute(
                "SELECT * FROM terminal_records WHERE run_id=?", (run_id,)
            ).fetchone()
            if existing is not None:
                return self._verified_terminal(existing)
            closure = self._budget_closure(db, run_id, now)
            try:
                from .termination import decide_terminal

                effective_assurance = "HOLD" if run["hold"] else assurance
                decision = decide_terminal(
                    cancelled=run["cancel_requested_at"] is not None,
                    stopped=run["stop_confirmed_at"] is not None,
                    deadline_reached=now >= run["deadline"],
                    work_complete=work_complete,
                    required_failure=required_failure,
                    budget_closed=closure["status"] == "CLOSED",
                    assurance=effective_assurance,
                )
            except ValueError:
                raise
            except Exception as exc:
                raise LedgerError("storage_failure", "termination failure") from exc
            body = dict(decision)
            body.update({
                "run_id": run_id,
                "owner": owner,
                "owner_epoch": epoch,
                "finalized_at": now if body.get("execution_status") != "WAITING" else None,
                "deadline": run["deadline"],
                "budget_closure": closure,
                "ledger_hold_reason": run["hold_reason"] if run["hold"] else None,
                "purpose": "component_validation",
                "ci_eligible": False,
            })
            if body.get("execution_status") == "WAITING":
                return body
            try:
                canonical = json.dumps(
                    body, ensure_ascii=False, sort_keys=True,
                    separators=(",", ":"), allow_nan=False,
                )
                payload = canonical.encode("utf-8")
            except (TypeError, ValueError, UnicodeError) as exc:
                raise LedgerError("invalid_report", "invalid report") from exc
            if len(payload) > _MAX_REPORT_BYTES:
                raise LedgerError("input_too_large", "input too large")
            digest = hashlib.sha256(payload).hexdigest()
            db.execute(
                "INSERT INTO terminal_records(run_id,canonical_json,payload,sha256) VALUES(?,?,?,?)",
                (run_id, canonical, payload, digest),
            )
            self._record_event(
                db, run_id, "terminal_finalized", now, owner, epoch,
                {"execution_status": body.get("execution_status"), "exit_code": body.get("exit_code")},
            )
            return body

    def get_terminal(self, run_id: str) -> dict[str, Any]:
        run_id = self._validate_id(run_id)
        with self._immediate() as db:
            self._now(db)
            row = db.execute(
                "SELECT * FROM terminal_records WHERE run_id=?", (run_id,)
            ).fetchone()
            if row is None:
                raise LedgerError("not_found", "terminal not found")
            return self._verified_terminal(row)

    @staticmethod
    def _verified_terminal(row: sqlite3.Row) -> dict[str, Any]:
        try:
            value = Ledger._verify_report_parts(
                row["canonical_json"], row["payload"], row["sha256"]
            )
        except LedgerError as exc:
            raise LedgerError("storage_failure", "stored terminal corrupted") from exc
        if (
            value.get("purpose") != "component_validation"
            or value.get("ci_eligible") is not False
            or value.get("run_id") != row["run_id"]
        ):
            raise LedgerError("storage_failure", "stored terminal corrupted")
        return value

    def snapshot(self, run_id: str) -> dict[str, Any]:
        run_id = self._validate_id(run_id)
        with self._immediate() as db:
            now = self._now(db)
            run = self._get_run(db, run_id)
            rows = db.execute(
                "SELECT * FROM operations WHERE run_id=? ORDER BY operation_id", (run_id,)
            ).fetchall()
            return {
                "run": self._run_dict(run),
                "operations": [self._operation_dict(row) for row in rows],
                "run_accounted_micros": self._run_accounted(db, run_id),
                "global_accounted_micros": self._global_accounted(db, now),
            }

    def store_report(self, request_id: str, report: dict[str, Any]) -> dict[str, Any]:
        request_id = self._validate_id(request_id)
        if not isinstance(report, dict):
            raise LedgerError("invalid_input", "invalid input")
        if report.get("ci_eligible") is not False or report.get("purpose") != "component_validation":
            raise LedgerError("invalid_report", "invalid report")
        try:
            canonical = json.dumps(
                report,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            payload = canonical.encode("utf-8")
        except (TypeError, ValueError, UnicodeError) as exc:
            raise LedgerError("invalid_report", "invalid report") from exc
        if len(payload) > _MAX_REPORT_BYTES:
            raise LedgerError("input_too_large", "input too large")
        digest = hashlib.sha256(payload).hexdigest()
        with self._immediate() as db:
            self._now(db)
            row = db.execute(
                "SELECT * FROM reports WHERE request_id=?", (request_id,)
            ).fetchone()
            if row is not None:
                if row["sha256"] != digest or row["canonical_json"] != canonical:
                    raise LedgerError("conflict", "report conflict")
                return {
                    "request_id": request_id,
                    "sha256": row["sha256"],
                    "report": self._verified_report(row),
                }
            try:
                db.execute(
                    "INSERT INTO reports(request_id,canonical_json,payload,sha256) VALUES(?,?,?,?)",
                    (request_id, canonical, payload, digest),
                )
            except sqlite3.IntegrityError as exc:
                raise LedgerError("conflict", "report conflict") from exc
            return {"request_id": request_id, "sha256": digest, "report": report}

    def get_report(self, request_id: str) -> dict[str, Any]:
        request_id = self._validate_id(request_id)
        with self._immediate() as db:
            self._now(db)
            row = db.execute(
                "SELECT canonical_json,payload,sha256 FROM reports WHERE request_id=?",
                (request_id,),
            ).fetchone()
            if row is None:
                raise LedgerError("not_found", "report not found")
            return self._verified_report(row)

    @staticmethod
    def _verified_report(row: sqlite3.Row) -> dict[str, Any]:
        """保存済みcanonical JSONをhashとbytesの一致確認後に復元する。"""
        canonical = row["canonical_json"]
        payload = row["payload"]
        try:
            expected_payload = canonical.encode("utf-8")
        except (AttributeError, UnicodeError) as exc:
            raise LedgerError("storage_failure", "stored report corrupted") from exc
        if not isinstance(payload, bytes) or payload != expected_payload:
            raise LedgerError("storage_failure", "stored report corrupted")
        if hashlib.sha256(payload).hexdigest() != row["sha256"]:
            raise LedgerError("storage_failure", "stored report corrupted")
        try:
            value = json.loads(canonical)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise LedgerError("storage_failure", "stored report corrupted") from exc
        if (
            not isinstance(value, dict)
            or value.get("ci_eligible") is not False
            or value.get("purpose") != "component_validation"
        ):
            raise LedgerError("storage_failure", "stored report corrupted")
        return value
