"""version 1の部品入力を読む。任意payloadの保存/転送は行わない。"""
import json
from pathlib import Path
from typing import Any

MAX_INPUT_BYTES = 65536
MAX_DEPTH = 16


class WireError(ValueError):
    """入力内容を含まない固定reason code。"""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise WireError("DUPLICATE_KEY")
        result[key] = value
    return result


def _constant(_: str) -> None:
    raise WireError("NONFINITE_NUMBER")


def _float(_: str) -> None:
    # version 1には小数/指数表記の数値fieldがない。float化によるinfも作らない。
    raise WireError("NON_INTEGER_NUMBER")


def _depth(value: Any, level: int = 0) -> None:
    if level > MAX_DEPTH:
        raise WireError("INPUT_DEPTH")
    if isinstance(value, dict):
        for child in value.values():
            _depth(child, level + 1)
    elif isinstance(value, list):
        for child in value:
            _depth(child, level + 1)


def decode_request(raw: bytes) -> dict[str, Any]:
    """構造と意味の検査はassess。ここでは厳密なJSONだけを受け付ける。"""
    if len(raw) > MAX_INPUT_BYTES:
        raise WireError("INPUT_TOO_LARGE")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant, parse_float=_float)
        _depth(value)
    except WireError:
        raise
    except (UnicodeError, ValueError, RecursionError):
        raise WireError("INVALID_JSON") from None
    if type(value) is not dict:
        raise WireError("INVALID_REQUEST")
    return value


def read_request(path: str | Path) -> dict[str, Any]:
    with Path(path).open("rb") as source:
        raw = source.read(MAX_INPUT_BYTES + 1)
    return decode_request(raw)


def canonical_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
