"""固定要求で管理AIのtimeout候補を検査し、初期方針へ適用する。"""

from __future__ import annotations

import json
from typing import Any

from .policy import PolicyError, initial_policy_profile, validate_policy_profile


MODEL = "qwen3.8-flash-next"
MAX_RESPONSE_BYTES = 64 * 1024
MAX_COMPLETION_TOKENS = 128
ALLOWED_TIMEOUTS = frozenset({90, 100, 110, 120})
ALLOWED_REASONS = frozenset({"lower_latency", "budget_headroom", "keep_initial"})
_USAGE_FIELDS = frozenset({"prompt_tokens", "completion_tokens", "total_tokens"})
_USAGE_OPTIONAL = frozenset({"prompt_tokens_details", "completion_tokens_details"})


class ManagementError(ValueError):
    """管理AIの入力を採択しない理由を含む固定エラー。"""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ManagementError("DUPLICATE_KEY")
        result[key] = value
    return result


def _reject_number(_: str) -> None:
    raise ManagementError("NON_INTEGER_NUMBER")


def _reject_constant(_: str) -> None:
    raise ManagementError("NONFINITE_NUMBER")


def _decode(raw: bytes) -> dict[str, Any]:
    if type(raw) is not bytes:
        raise ManagementError("INPUT_TYPE")
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ManagementError("RESPONSE_TOO_LARGE")
    if raw.startswith(b"\xef\xbb\xbf"):
        raise ManagementError("BOM")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ManagementError("INVALID_UTF8") from None
    try:
        value = json.loads(
            text,
            object_pairs_hook=_pairs,
            parse_float=_reject_number,
            parse_constant=_reject_constant,
        )
    except ManagementError:
        raise
    except (json.JSONDecodeError, RecursionError, UnicodeError, ValueError):
        raise ManagementError("INVALID_JSON") from None
    if type(value) is not dict:
        raise ManagementError("INVALID_RESPONSE")
    return value


def _selection(content: str) -> dict[str, Any]:
    if content.startswith("\ufeff"):
        raise ManagementError("BOM")
    try:
        value = json.loads(
            content,
            object_pairs_hook=_pairs,
            parse_float=_reject_number,
            parse_constant=_reject_constant,
        )
    except ManagementError:
        raise
    except (json.JSONDecodeError, RecursionError, UnicodeError, ValueError):
        raise ManagementError("INVALID_CONTENT") from None
    if type(value) is not dict or set(value) != {"timeout_seconds", "reason"}:
        raise ManagementError("INVALID_SELECTION")
    timeout = value["timeout_seconds"]
    reason = value["reason"]
    if type(timeout) is not int or timeout not in ALLOWED_TIMEOUTS:
        raise ManagementError("INVALID_TIMEOUT")
    if type(reason) is not str or reason not in ALLOWED_REASONS:
        raise ManagementError("INVALID_REASON")
    return {"timeout_seconds": timeout, "reason": reason}


def _usage(value: Any) -> dict[str, int]:
    if (type(value) is not dict or not _USAGE_FIELDS <= set(value)
            or not set(value) <= _USAGE_FIELDS | _USAGE_OPTIONAL):
        raise ManagementError("INVALID_USAGE")
    if any(type(value[field]) is not int or value[field] < 0 for field in _USAGE_FIELDS):
        raise ManagementError("INVALID_USAGE")
    prompt = value["prompt_tokens"]
    completion = value["completion_tokens"]
    total = value["total_tokens"]
    if prompt > 32768 or completion > MAX_COMPLETION_TOKENS or prompt + completion != total:
        raise ManagementError("INVALID_USAGE")
    return {field: value[field] for field in ("prompt_tokens", "completion_tokens", "total_tokens")}


def _apply_policy(selection: dict[str, Any]) -> dict[str, Any]:
    try:
        profile = initial_policy_profile()
        profile["per_call"]["timeout_seconds"] = selection["timeout_seconds"]
        return validate_policy_profile(profile)
    except (KeyError, PolicyError, TypeError, ValueError, RecursionError):
        raise ManagementError("POLICY_INVALID") from None


def build_request() -> dict[str, Any]:
    """候補や履歴を含まない、毎回独立した固定Qwen要求を返す。"""
    return {
        "model": MODEL,
        "temperature": 0,
        "max_tokens": MAX_COMPLETION_TOKENS,
        "chat_template_kwargs": {"enable_thinking": False},
        "messages": [
            {
                "role": "system",
                "content": (
                    "管理方針のtimeout候補を選ぶ補助です。権限、actor、context、方針の閾値や予算を決めません。"
                    "入力候補や会話履歴はありません。"
                ),
            },
            {
                "role": "user",
                "content": (
                    '初期設定は120秒です。初回の固定fixture評価で、上限を維持するか厳しくするか選んでください。'
                    'JSON objectだけを返してください。キーは"timeout_seconds"と"reason"だけです。'
                    'timeout_secondsは90,100,110,120のいずれか、reasonは'
                    '"lower_latency","budget_headroom","keep_initial"のいずれかです。'
                ),
            },
        ],
    }


def parse_response(raw: bytes) -> dict[str, Any]:
    """Qwen応答を固定形へ絞り込み、検査済みtimeoutだけを初期方針へ適用する。"""
    document = _decode(raw)
    if document.get("model") != MODEL:
        raise ManagementError("MODEL_MISMATCH")
    choices = document.get("choices")
    if type(choices) is not list or len(choices) != 1:
        raise ManagementError("INVALID_CHOICES")
    choice = choices[0]
    if type(choice) is not dict or choice.get("finish_reason") != "stop":
        raise ManagementError("INVALID_FINISH_REASON")
    message = choice.get("message")
    if type(message) is not dict or type(message.get("content")) is not str:
        raise ManagementError("INVALID_CONTENT")
    selection = _selection(message["content"])
    usage = _usage(document.get("usage"))
    return {
        "schema_version": 1,
        "kind": "management_proposal",
        "selection": selection,
        "policy": _apply_policy(selection),
        "model": MODEL,
        "usage": usage,
    }


__all__ = [
    "ALLOWED_REASONS",
    "ALLOWED_TIMEOUTS",
    "MAX_COMPLETION_TOKENS",
    "MAX_RESPONSE_BYTES",
    "MODEL",
    "ManagementError",
    "build_request",
    "parse_response",
]
