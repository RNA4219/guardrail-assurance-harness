"""認証済み管理入口だけが使う補助journal。authorityの採択・費用の正本ではない。"""
from __future__ import annotations
from contextlib import contextmanager
import hashlib
from pathlib import Path
import sqlite3
from .sqlite_limits import connect_sqlite
import time

from .contracts import ContractError, decode_document, require_digest, require_id, require_uint
from .productization import COMMANDS, validate_operation_result
from .wire import canonical_bytes


class OperationJournal:
    """principalはtrusted入口の認証結果。入力JSONから取り出してはならない。

    beginのcreated=Falseかつresult=Noneは送信済みか不明であり、再実行許可ではない。
    呼出側はauthorityの同request receiptを照会してrecoverする。DBは専用管理領域。
    """
    def __init__(self, path: str | Path, *, clock=None):
        self.clock = clock or (lambda: int(time.time()))
        try:
            self.db = connect_sqlite(str(path), isolation_level=None, timeout=5)
        except sqlite3.Error:
            raise ContractError("IO_ERROR") from None
        self.db.row_factory = sqlite3.Row
        try:
            with self._transaction():
                version = self.db.execute("PRAGMA user_version").fetchone()[0]
                tables = {r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if version == 0 and not tables:
                    self.db.execute("CREATE TABLE operation_clock (id INTEGER PRIMARY KEY CHECK(id=1), last_seen INTEGER NOT NULL)")
                    self.db.execute("INSERT INTO operation_clock VALUES (1,0)")
                    self.db.execute("CREATE TABLE operations (principal TEXT NOT NULL, command TEXT NOT NULL, request_id TEXT NOT NULL, input_digest TEXT NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, result BLOB, result_digest TEXT, PRIMARY KEY(principal,command,request_id))")
                    self.db.execute("PRAGMA user_version=1")
                elif version != 1 or tables != {"operation_clock", "operations"}:
                    raise ContractError("SCHEMA_UNSUPPORTED")
                expected = {"operation_clock": {"id", "last_seen"}, "operations": {
                    "principal", "command", "request_id", "input_digest", "created_at", "updated_at", "result", "result_digest"}}
                for table, columns in expected.items():
                    info = list(self.db.execute(f'PRAGMA table_info("{table}")'))
                    keys = {r[1]: r[5] for r in info if r[5]}
                    expected_keys = ({"id": 1} if table == "operation_clock" else
                                     {"principal": 1, "command": 2, "request_id": 3})
                    if {r[1] for r in info} != columns or keys != expected_keys:
                        raise ContractError("SCHEMA_UNSUPPORTED")
                if self.db.execute("SELECT count(*) FROM operation_clock").fetchone()[0] != 1:
                    raise ContractError("SCHEMA_UNSUPPORTED")
        except BaseException:
            self.db.close()
            raise

    @contextmanager
    def _transaction(self):
        try:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield
                self.db.execute("COMMIT")
            except BaseException:
                if self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                raise
        except sqlite3.Error:
            raise ContractError("IO_ERROR") from None

    def _now(self):
        now = self.clock()
        require_uint(now)
        last = self.db.execute("SELECT last_seen FROM operation_clock WHERE id=1").fetchone()
        if last is None or type(last[0]) is not int or now < last[0]:
            raise ContractError("CLOCK_ROLLBACK")
        self.db.execute("UPDATE operation_clock SET last_seen=? WHERE id=1", (now,))
        return now

    @staticmethod
    def _key(principal, command, request_id, input_digest):
        require_id(principal)
        require_id(request_id)
        require_digest(input_digest)
        if type(command) is not str or command not in COMMANDS or command.endswith(".invalid"):
            raise ContractError("INVALID_INPUT")
        return principal, command, request_id

    @staticmethod
    def _result(row):
        if row["result"] is None:
            if row["result_digest"] is not None:
                raise ContractError("BINDING_MISMATCH")
            return None
        raw = row["result"]
        if type(raw) is not bytes or hashlib.sha256(raw).hexdigest() != row["result_digest"]:
            raise ContractError("BINDING_MISMATCH")
        value = validate_operation_result(decode_document(raw))
        if value["command"] != row["command"] or value["request_id"] != row["request_id"]:
            raise ContractError("BINDING_MISMATCH")
        return value

    def begin(self, principal, command, request_id, input_digest):
        key = self._key(principal, command, request_id, input_digest)
        with self._transaction():
            now = self._now()
            row = self.db.execute("SELECT * FROM operations WHERE principal=? AND command=? AND request_id=?", key).fetchone()
            if row is not None:
                if row["input_digest"] != input_digest:
                    raise ContractError("IDEMPOTENCY_CONFLICT")
                return {"created": False, "result": self._result(row)}
            self.db.execute("INSERT INTO operations VALUES(?,?,?,?,?,?,NULL,NULL)", (*key, input_digest, now, now))
            return {"created": True, "result": None}

    def finish(self, principal, command, request_id, input_digest, result):
        key = self._key(principal, command, request_id, input_digest)
        result = validate_operation_result(result)
        if result["command"] != command or result["request_id"] != request_id:
            raise ContractError("BINDING_MISMATCH")
        raw = canonical_bytes(result)
        with self._transaction():
            now = self._now()
            row = self.db.execute("SELECT * FROM operations WHERE principal=? AND command=? AND request_id=?", key).fetchone()
            if row is None:
                raise ContractError("OPERATION_UNKNOWN")
            if row["input_digest"] != input_digest:
                raise ContractError("IDEMPOTENCY_CONFLICT")
            if row["result"] is not None:
                if self._result(row) != result:
                    raise ContractError("RESULT_CONFLICT")
                return result
            if not row["created_at"] <= result["checked_at"] <= now:
                raise ContractError("BINDING_MISMATCH")
            self.db.execute("UPDATE operations SET result=?,result_digest=?,updated_at=? WHERE principal=? AND command=? AND request_id=?",
                (raw, hashlib.sha256(raw).hexdigest(), now, *key))
            return result

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
