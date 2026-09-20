"""全保存接続へ固定SQLite容量と一時領域設定を適用する。"""
from __future__ import annotations

import sqlite3

MAX_SQLITE_BYTES = 256 * 1024 * 1024


class SQLiteLimitError(sqlite3.OperationalError):
    """固定SQLite容量設定を安全に適用できなかった。"""


def _integer(connection: sqlite3.Connection, pragma: str) -> int:
    try:
        row = connection.execute("PRAGMA " + pragma).fetchone()
        value = row[0] if row else None
    except sqlite3.Error as exc:
        raise SQLiteLimitError("SQLITE_LIMIT_CONFIGURATION_FAILED") from exc
    if type(value) is not int or value < 0:
        raise SQLiteLimitError("SQLITE_LIMIT_READBACK_FAILED")
    return value


def inspect_sqlite_limits(connection: sqlite3.Connection) -> dict[str, int]:
    """読取専用検査。DB内容や永続PRAGMAを変更しない。"""
    if not isinstance(connection, sqlite3.Connection):
        raise SQLiteLimitError("SQLITE_LIMIT_INVALID_CONNECTION")
    page_size = _integer(connection, "page_size")
    page_count = _integer(connection, "page_count")
    mode_row = connection.execute("PRAGMA journal_mode").fetchone()
    mode = mode_row[0].lower() if mode_row and type(mode_row[0]) is str else None
    if page_size < 512 or page_size > 65536 or page_size & (page_size - 1):
        raise SQLiteLimitError("SQLITE_LIMIT_INVALID_PAGE_SIZE")
    if page_count * page_size > MAX_SQLITE_BYTES:
        raise SQLiteLimitError("SQLITE_LIMIT_EXCEEDED")
    return {"page_size": page_size, "page_count": page_count,
            "journal_mode": mode, "max_bytes": MAX_SQLITE_BYTES}


def apply_sqlite_limits(connection: sqlite3.Connection) -> dict[str, int | str]:
    """既存のpage_sizeを保ち、現在接続へ全固定上限を適用・readbackする。"""
    if not isinstance(connection, sqlite3.Connection):
        raise SQLiteLimitError("SQLITE_LIMIT_INVALID_CONNECTION")
    if connection.in_transaction:
        raise SQLiteLimitError("SQLITE_LIMIT_TRANSACTION_ACTIVE")
    current = inspect_sqlite_limits(connection)
    page_size = current["page_size"]
    page_count = current["page_count"]
    if current["journal_mode"] != "delete":
        raise SQLiteLimitError("SQLITE_LIMIT_JOURNAL_MODE_UNSUPPORTED")
    max_pages = MAX_SQLITE_BYTES // page_size
    if max_pages < page_count:
        raise SQLiteLimitError("SQLITE_LIMIT_EXCEEDED")
    try:
        connection.execute("PRAGMA max_page_count = " + str(max_pages))
        if _integer(connection, "max_page_count") != max_pages:
            raise SQLiteLimitError("SQLITE_LIMIT_READBACK_FAILED")
        if _integer(connection, "synchronous") != 2:
            connection.execute("PRAGMA synchronous = FULL")
        if _integer(connection, "synchronous") != 2:
            raise SQLiteLimitError("SQLITE_LIMIT_READBACK_FAILED")
        if _integer(connection, "temp_store") != 2:
            connection.execute("PRAGMA temp_store = MEMORY")
        if _integer(connection, "temp_store") != 2:
            raise SQLiteLimitError("SQLITE_LIMIT_READBACK_FAILED")
    except SQLiteLimitError:
        raise
    except sqlite3.Error as exc:
        raise SQLiteLimitError("SQLITE_LIMIT_CONFIGURATION_FAILED") from exc
    return {**current, "max_page_count": max_pages, "journal_mode": "delete",
            "synchronous": 2, "temp_store": 2}


def connect_sqlite(database, *args, **kwargs) -> sqlite3.Connection:
    """SQLite接続を生成し設定。失敗した接続は呼出側へ漏らさず閉じる。"""
    connection = sqlite3.connect(database, *args, **kwargs)
    try:
        apply_sqlite_limits(connection)
    except BaseException:
        connection.close()
        raise
    return connection


__all__ = ["MAX_SQLITE_BYTES", "SQLiteLimitError", "apply_sqlite_limits",
           "connect_sqlite", "inspect_sqlite_limits"]
