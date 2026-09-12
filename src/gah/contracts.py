"""MVPの構造化契約に共通の、入力を漏らさない厳格検査。"""
import json
import re
from typing import Any

MAX_DOCUMENT_BYTES = 1048576
MAX_INTEGER = 2**53 - 1
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


class ContractError(ValueError):
    def __init__(self, code: str = "INVALID_CONTRACT"):
        self.code = code
        super().__init__(code)


def require_object(value: Any, keys: set[str]) -> None:
    if type(value) is not dict or set(value) != keys:
        raise ContractError()


def require_id(value: Any) -> None:
    if type(value) is not str or not _ID.fullmatch(value):
        raise ContractError()


def require_digest(value: Any) -> None:
    if type(value) is not str or not _DIGEST.fullmatch(value):
        raise ContractError()


def require_uint(value: Any, *, maximum: int = MAX_INTEGER) -> None:
    if type(value) is not int or not 0 <= value <= maximum:
        raise ContractError()


def require_text(value: Any, *, maximum: int = 512) -> None:
    if type(value) is not str or not value.strip() or len(value) > maximum:
        raise ContractError()
    if any(ord(char) < 32 for char in value):
        raise ContractError()


def require_ref(value: Any) -> None:
    require_object(value, {"kind", "id", "digest"})
    require_id(value["kind"])
    require_id(value["id"])
    require_digest(value["digest"])


def _pairs(pairs: list[tuple[str, Any]]) -> dict:
    value = {}
    for key, item in pairs:
        if key in value:
            raise ContractError("DUPLICATE_KEY")
        value[key] = item
    return value


def _reject_number(_: str) -> None:
    raise ContractError("NON_INTEGER_NUMBER")


def decode_document(raw: bytes) -> dict:
    if type(raw) is not bytes or len(raw) > MAX_DOCUMENT_BYTES:
        raise ContractError("DOCUMENT_SIZE")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                           parse_float=_reject_number, parse_constant=_reject_number)
        pending = [(value, 0)]
        nodes = 0
        while pending:
            current, depth = pending.pop()
            nodes += 1
            if depth > 16 or nodes > 100000:
                raise ContractError("DOCUMENT_COMPLEXITY")
            if type(current) is dict:
                pending.extend((child, depth + 1) for child in current.values())
            elif type(current) is list:
                pending.extend((child, depth + 1) for child in current)
            elif type(current) is int:
                if not -MAX_INTEGER <= current <= MAX_INTEGER:
                    raise ContractError("INTEGER_RANGE")
        if type(value) is not dict:
            raise ContractError()
        # Unicodeの孤立surrogateも、保存前に拒否する。
        json.dumps(value, ensure_ascii=False).encode("utf-8")
        return value
    except ContractError:
        raise
    except (UnicodeError, ValueError, RecursionError):
        raise ContractError("INVALID_JSON") from None
