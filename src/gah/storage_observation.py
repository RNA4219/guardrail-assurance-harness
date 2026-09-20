"""Closed validator for fixed Docker storage observations."""
from __future__ import annotations

import re
from typing import Any

from .contracts import ContractError, MAX_INTEGER


_FIELDS = {
    "schema_version", "kind", "status", "source", "scopes", "checked_at",
    "checked_at_source", "reason", "ci_eligible",
}
_SOURCE_FIELDS = {"image_id", "prefix", "container_id"}
_SCOPE_FIELDS = {"available_bytes", "free_bytes", "total_bytes", "available_inodes"}
_REASONS = {
    "RUNTIME_INVALID", "BROKER_UNAVAILABLE", "CONFIG_MISMATCH",
    "VOLUME_MISMATCH", "BROKER_RESTARTED", "COMMAND_FAILED",
    "OBSERVATION_MISSING", "OBSERVATION_INVALID",
}
_PREFIX = re.compile(r"gah-authority-[0-9a-f]{32}\Z")
_IMAGE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_CONTAINER_ID = re.compile(r"[0-9a-f]{64}\Z")


def _invalid() -> None:
    raise ContractError("STORAGE_OBSERVATION_INVALID")


def _uint(value: Any) -> bool:
    return type(value) is int and 0 <= value <= MAX_INTEGER


def validate_storage_observation(value: Any) -> dict[str, Any]:
    """Validate the full closed host-observed Docker storage shape.

    A zero available byte/inode count is a valid observation. Missing values are
    represented only by null and force UNKNOWN; they are never filled with zero.
    """
    if type(value) is not dict or set(value) != _FIELDS:
        _invalid()
    if (type(value["schema_version"]) is not int or value["schema_version"] != 1
            or value["kind"] != "gah_authority_storage_observation"
            or type(value["status"]) is not str
            or value["status"] not in {"OBSERVED", "UNKNOWN"}
            or value["checked_at_source"] != "host_observed"
            or not _uint(value["checked_at"])
            or value["ci_eligible"] is not False):
        _invalid()
    source = value["source"]
    if type(source) is not dict or set(source) != _SOURCE_FIELDS:
        _invalid()
    for name, pattern in (("image_id", _IMAGE), ("prefix", _PREFIX),
                          ("container_id", _CONTAINER_ID)):
        item = source[name]
        if item is not None and (type(item) is not str or pattern.fullmatch(item) is None):
            _invalid()
    scopes = value["scopes"]
    if type(scopes) is not dict or set(scopes) != {"state", "ipc"}:
        _invalid()
    missing = False
    for scope in scopes.values():
        if scope is None:
            missing = True
            continue
        if type(scope) is not dict or set(scope) != _SCOPE_FIELDS:
            _invalid()
        fields = [scope[name] for name in _SCOPE_FIELDS]
        if any(item is not None and not _uint(item) for item in fields):
            _invalid()
        available = scope["available_bytes"]
        free = scope["free_bytes"]
        total = scope["total_bytes"]
        if available is None or free is None or total is None or scope["available_inodes"] is None:
            missing = True
        if (available is not None and free is not None and available > free
                or free is not None and total is not None and free > total):
            _invalid()
    reason = value["reason"]
    if value["status"] == "OBSERVED":
        if missing or reason is not None or any(item is None for item in source.values()):
            _invalid()
    else:
        if type(reason) is not str or reason not in _REASONS:
            _invalid()
    return {**value, "source": dict(source),
            "scopes": {name: None if scope is None else dict(scope)
                       for name, scope in scopes.items()}}


__all__ = ["validate_storage_observation"]
