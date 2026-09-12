"""固定版Promptfooの通常JSON出力をGAHの正規化結果へ写像する。"""

from __future__ import annotations

import hashlib
import json
import math
import re
from copy import deepcopy
from typing import Any

from .contracts import ContractError, MAX_INTEGER, MAX_DOCUMENT_BYTES
from .normalized import normalize_generic, validate_binding


MAX_RAW_BYTES = MAX_DOCUMENT_BYTES
MAX_STRUCTURED_OUTPUT_BYTES = 64 * 1024
_VERSION = 3
_WRAPPER_REQUIRED = {"evalId", "results", "config", "shareableUrl"}
_WRAPPER_OPTIONAL = {"metadata", "vars", "runtimeOptions", "traces", "blobAssets"}
_SUMMARY_FIELDS = {"version", "timestamp", "results", "prompts", "stats"}
_ROW_REQUIRED = {
    "promptIdx", "testIdx", "testCase", "promptId", "provider", "prompt", "vars",
    "failureReason", "success", "score", "latencyMs", "namedScores",
}
_ROW_OPTIONAL = {
    "id", "description", "response", "error", "gradingResult", "cost", "incurredCost",
    "metadata", "tokenUsage", "evaluationId", "traceId",
}
_STATS_REQUIRED = {"successes", "failures", "errors", "tokenUsage"}
_STATS_OPTIONAL = {"durationMs", "generationDurationMs", "evaluationDurationMs"}
_OBSERVATION_FIELDS = {"mode", "observations"}
_GENERIC_FIELDS = {"schema_version", "kind", "binding", "mode", "observations"}
_PROMPTFOO_VERSION = "0.123.0"
_BINDING_ID = re.compile(r"gah-binding(?::|-)[0-9a-f]{64}\Z")


class PromptfooAdapterError(ContractError):
    """Promptfoo adapterが公開する固定エラー。"""


AdapterError = PromptfooAdapterError


def _fail(code: str = "OUTPUT_INVALID") -> None:
    raise PromptfooAdapterError(code)


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            _fail("DUPLICATE_KEY")
        value[key] = item
    return value


def _parse_float(value: str) -> float:
    try:
        result = float(value)
    except ValueError:
        _fail("OUTPUT_INVALID")
    if not math.isfinite(result):
        _fail("NONFINITE_NUMBER")
    return result


def _parse_constant(_: str) -> None:
    _fail("NONFINITE_NUMBER")


def _walk(value: Any, depth: int = 0, nodes: list[int] | None = None) -> None:
    if nodes is None:
        nodes = [0]
    nodes[0] += 1
    if depth > 32 or nodes[0] > 100_000:
        _fail("DOCUMENT_COMPLEXITY")
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                _fail()
            _walk(item, depth + 1, nodes)
    elif type(value) is list:
        for item in value:
            _walk(item, depth + 1, nodes)
    elif type(value) is float:
        if not math.isfinite(value):
            _fail("NONFINITE_NUMBER")
    elif type(value) is int and not -MAX_INTEGER <= value <= MAX_INTEGER:
        _fail("INTEGER_RANGE")


def _decode(raw: bytes, *, maximum: int = MAX_RAW_BYTES) -> dict[str, Any]:
    if type(raw) is not bytes or len(raw) > maximum:
        _fail("OUTPUT_TOO_LARGE")
    if raw.startswith(b"\xef\xbb\xbf"):
        _fail("OUTPUT_INVALID")
    try:
        text = raw.decode("utf-8")
        if text.startswith("\ufeff"):
            _fail("OUTPUT_INVALID")
        value = json.loads(
            text,
            object_pairs_hook=_pairs,
            parse_float=_parse_float,
            parse_constant=_parse_constant,
        )
        _walk(value)
        if type(value) is not dict:
            _fail("OUTPUT_INVALID")
        json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        return value
    except PromptfooAdapterError:
        raise
    except (UnicodeError, ValueError, TypeError, RecursionError):
        _fail("OUTPUT_INVALID")
    raise AssertionError("到達不能")


def _keys(value: Any, required: set[str], optional: set[str] = frozenset()) -> None:
    if type(value) is not dict or not required <= set(value) or set(value) - required - optional:
        _fail()


def _text(value: Any, *, allow_none: bool = False, maximum: int = 512) -> None:
    if allow_none and value is None:
        return
    if type(value) is not str or not value.strip() or len(value) > maximum:
        _fail()
    if any(ord(char) < 32 for char in value):
        _fail()


def _uint(value: Any) -> None:
    if type(value) is not int or not 0 <= value <= MAX_INTEGER:
        _fail("VALUE_RANGE")


def _number(value: Any, *, nonnegative: bool = False) -> None:
    if type(value) not in (int, float) or isinstance(value, bool) or not math.isfinite(value):
        _fail("NONFINITE_NUMBER")
    if nonnegative and value < 0:
        _fail("VALUE_RANGE")


def _binding_id(binding: dict[str, Any]) -> str:
    """検閲器の秘密値判定に当たらない不透明binding IDを作る。"""
    try:
        canonical = json.dumps(
            binding, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        _fail("INVALID_CONTRACT")
    return "gah-binding:" + hashlib.sha256(canonical).hexdigest()


def _normalize_identity(value: Any) -> dict[str, Any]:
    if type(value) is not dict:
        _fail("INVALID_CONTRACT")
    nested = {"evalId", "testIdx", "promptIdx", "provider", "promptId", "version"}
    flat = {"evalId", "testIdx", "promptIdx", "provider_id", "provider_label", "promptId", "version"}
    if set(value) == nested:
        provider = value["provider"]
        if type(provider) is not dict or set(provider) != {"id", "label"}:
            _fail("INVALID_CONTRACT")
        provider_id, provider_label = provider["id"], provider["label"]
    elif set(value) == flat:
        provider_id, provider_label = value["provider_id"], value["provider_label"]
    else:
        _fail("INVALID_CONTRACT")
    if value["evalId"] is not None:
        _text(value["evalId"])
    _uint(value["testIdx"])
    _uint(value["promptIdx"])
    _text(provider_id)
    _text(provider_label)
    _text(value["promptId"])
    if type(value["version"]) is not int or value["version"] != _VERSION:
        _fail("VERSION_MISMATCH")
    return {
        "evalId": value["evalId"],
        "testIdx": value["testIdx"],
        "promptIdx": value["promptIdx"],
        "provider": {"id": provider_id, "label": provider_label},
        "promptId": value["promptId"],
        "version": value["version"],
    }


def _validate_wrapper(document: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    _keys(document, _WRAPPER_REQUIRED, _WRAPPER_OPTIONAL)
    if document["evalId"] is not None:
        _text(document["evalId"])
    if type(document["config"]) is not dict:
        _fail()
    if document["shareableUrl"] is not None:
        _text(document["shareableUrl"], maximum=4096)
    for field, expected in (("metadata", dict), ("runtimeOptions", dict)):
        if field in document and type(document[field]) is not expected:
            _fail()
    if "metadata" in document and "promptfooVersion" in document["metadata"]:
        _text(document["metadata"]["promptfooVersion"])
        if document["metadata"]["promptfooVersion"] != _PROMPTFOO_VERSION:
            _fail("VERSION_MISMATCH")
    for field in ("vars", "traces", "blobAssets"):
        if field in document and type(document[field]) is not list:
            _fail()
    summary = document["results"]
    _keys(summary, _SUMMARY_FIELDS)
    if type(summary["version"]) is not int:
        _fail("VERSION_MISMATCH")
    if summary["version"] != _VERSION:
        _fail("VERSION_MISMATCH")
    _text(summary["timestamp"], maximum=128)
    if type(summary["prompts"]) is not list or type(summary["results"]) is not list:
        _fail()
    rows = summary["results"]
    if len(rows) != 1:
        _fail("OUTPUT_INVALID")
    return document, summary


def _validate_stats(stats: Any, row: dict[str, Any]) -> None:
    _keys(stats, _STATS_REQUIRED, _STATS_OPTIONAL)
    for field in ("successes", "failures", "errors"):
        _uint(stats[field])
    if type(stats["tokenUsage"]) is not dict:
        _fail()
    for field in _STATS_OPTIONAL:
        if field in stats:
            _number(stats[field], nonnegative=True)
    has_error = "error" in row and row["error"] is not None
    expected = (0, 0, 1) if has_error else ((1, 0, 0) if row["success"] else (0, 1, 0))
    if (stats["successes"], stats["failures"], stats["errors"]) != expected:
        _fail("OUTPUT_INVALID")


def _validate_row(row: Any, identity: dict[str, Any], expected_binding: dict[str, Any]) -> None:
    _keys(row, _ROW_REQUIRED, _ROW_OPTIONAL)
    for field in ("testIdx", "promptIdx"):
        _uint(row[field])
    if type(row["testCase"]) is not dict:
        _fail("MAPPING_UNAVAILABLE")
    metadata = row["testCase"].get("metadata")
    if type(metadata) is not dict or type(metadata.get("gah")) is not dict:
        _fail("MAPPING_UNAVAILABLE")
    gah_binding = metadata["gah"]
    if set(gah_binding) == {"binding_id"}:
        binding_id = gah_binding["binding_id"]
        if type(binding_id) is not str or not _BINDING_ID.fullmatch(binding_id):
            _fail("BINDING_MISMATCH")
        if binding_id not in {_binding_id(expected_binding), "gah-binding-" + _binding_id(expected_binding).split(":", 1)[1]}:
            _fail("BINDING_MISMATCH")
    elif set(gah_binding) == set(expected_binding):
        try:
            actual_binding = validate_binding(gah_binding)
        except ContractError:
            _fail("BINDING_MISMATCH")
        if actual_binding != expected_binding:
            _fail("BINDING_MISMATCH")
    else:
        _fail("OUTPUT_INVALID")
    if type(row["provider"]) is not dict or set(row["provider"]) != {"id", "label"}:
        _fail()
    _text(row["provider"]["id"])
    _text(row["provider"]["label"])
    _text(row["promptId"])
    if type(row["prompt"]) not in (str, dict, list):
        _fail()
    if type(row["vars"]) is not dict or type(row["namedScores"]) is not dict:
        _fail()
    for score in row["namedScores"].values():
        _number(score)
    if type(row["failureReason"]) is not int or row["failureReason"] not in {0, 1, 2}:
        _fail("VALUE_RANGE")
    if type(row["success"]) is not bool:
        _fail()
    _number(row["score"])
    _number(row["latencyMs"], nonnegative=True)
    for field in ("id", "description", "evaluationId", "traceId"):
        if field in row:
            _text(row[field])
    if "error" in row and row["error"] is not None:
        _text(row["error"], maximum=16 * 1024)
    for field in ("cost", "incurredCost"):
        if field in row:
            _number(row[field], nonnegative=True)
    for field in ("gradingResult", "metadata", "tokenUsage"):
        if field in row and row[field] is not None and type(row[field]) is not dict:
            _fail()
    if row["testIdx"] != identity["testIdx"] or row["promptIdx"] != identity["promptIdx"]:
        _fail("BINDING_MISMATCH")
    if row["promptId"] != identity["promptId"] or row["provider"] != identity["provider"]:
        _fail("BINDING_MISMATCH")


def _error_result(binding: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "kind": "normalized_result",
        "binding": deepcopy(binding),
        "mode": None,
        "observation": None,
        "mutation_outcome": "ERROR",
        "detection": None,
        "deviation": None,
        "error_class": "EXECUTION_FAILURE",
        "raw_digest": None,
    }


def _structured_to_generic(output: Any, binding: dict[str, Any]) -> bytes:
    if type(output) is dict:
        value = output
    elif type(output) is str:
        encoded = output.encode("utf-8")
        value = _decode(encoded, maximum=MAX_STRUCTURED_OUTPUT_BYTES)
    else:
        _fail("OUTPUT_INVALID")
    if set(value) == _OBSERVATION_FIELDS:
        generic = {
            "schema_version": 1,
            "kind": "gah_generic_result",
            "binding": deepcopy(binding),
            "mode": value["mode"],
            "observations": value["observations"],
        }
    elif set(value) == _GENERIC_FIELDS:
        if value["schema_version"] != 1 or value["kind"] != "gah_generic_result":
            _fail("OUTPUT_INVALID")
        if value["binding"] != binding:
            _fail("BINDING_MISMATCH")
        generic = value
    else:
        _fail("OUTPUT_INVALID")
    try:
        encoded = json.dumps(generic, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        _fail("OUTPUT_INVALID")
    if len(encoded) > MAX_STRUCTURED_OUTPUT_BYTES:
        _fail("OUTPUT_TOO_LARGE")
    return encoded


def normalize_promptfoo(
    raw: bytes,
    *,
    expected_binding: dict[str, Any],
    expected_identity: dict[str, Any],
) -> dict[str, Any]:
    """Promptfooの単一行JSONを、期待bindingへ固定して正規化する。"""
    try:
        binding = validate_binding(expected_binding)
    except ContractError:
        _fail("BINDING_MISMATCH")
    identity = _normalize_identity(expected_identity)
    document = _decode(raw)
    wrapper, summary = _validate_wrapper(document)
    if wrapper["evalId"] != identity["evalId"]:
        _fail("BINDING_MISMATCH")
    row = summary["results"][0]
    _validate_row(row, identity, binding)
    _validate_stats(summary["stats"], row)
    original_digest = hashlib.sha256(raw).hexdigest()
    if "error" in row and row["error"] is not None:
        return _error_result(binding)
    response = row.get("response")
    if type(response) is not dict or "output" not in response:
        _fail("MAPPING_UNAVAILABLE")
    generic_bytes = _structured_to_generic(response["output"], binding)
    try:
        result = normalize_generic(
            generic_bytes,
            binding,
            execution_status="COMPLETED",
            exit_code=0,
            stop_confirmed=True,
        )
    except ContractError:
        _fail("OUTPUT_INVALID")
    result["raw_digest"] = original_digest
    return result


__all__ = ["AdapterError", "PromptfooAdapterError", "normalize_promptfoo"]
