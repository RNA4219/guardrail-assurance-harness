"""Control Registryの構造検査と依存関係の決定的な解決。"""

from __future__ import annotations

from copy import deepcopy
import json
from typing import Any

from .contracts import (
    MAX_DOCUMENT_BYTES,
    ContractError,
    require_id,
    require_object,
    require_ref,
    require_text,
)


MAX_CONTROLS = 1000
MAX_CONTROL_DEPENDENCIES = 100
MAX_CONTROL_OBLIGATIONS = 100

_CRITICALITIES = {"critical", "noncritical"}
_OBLIGATION_KINDS = {"constraint", "mutation", "llm_metric"}
_EVENT_POLICIES = {"forbidden", "aggregate", "none"}
_MUTATION_STATUSES = {"applicable", "not_applicable"}


def _require_list(value: Any, *, maximum: int, minimum: int = 0) -> None:
    if type(value) is not list or not minimum <= len(value) <= maximum:
        raise ContractError()


def _require_bool(value: Any) -> None:
    if type(value) is not bool:
        raise ContractError()


def _require_enum(value: Any, allowed: set[str]) -> None:
    if type(value) is not str or value not in allowed:
        raise ContractError()


def _validate_mutation_applicability(value: Any) -> str:
    require_object(value, {"status", "reason"})
    _require_enum(value["status"], _MUTATION_STATUSES)
    if value["status"] == "applicable":
        if value["reason"] is not None:
            raise ContractError()
    else:
        require_text(value["reason"])
    return value["status"]


def _validate_obligation(value: Any, obligation_ids: set[str]) -> str:
    require_object(
        value,
        {"obligation_id", "kind", "required", "event_policy", "evaluator_ref"},
    )
    require_id(value["obligation_id"])
    if value["obligation_id"] in obligation_ids:
        raise ContractError("DUPLICATE_ID")
    _require_enum(value["kind"], _OBLIGATION_KINDS)
    _require_bool(value["required"])
    _require_enum(value["event_policy"], _EVENT_POLICIES)
    require_ref(value["evaluator_ref"])
    obligation_ids.add(value["obligation_id"])
    return value["kind"]


def _validate_control(value: Any, control_ids: set[str], obligation_ids: set[str]) -> tuple[str, set[str]]:
    require_object(
        value,
        {
            "control_id", "owner", "invariant", "criticality", "target_ref",
            "dependencies", "obligations", "mutation_applicability",
        },
    )
    require_id(value["control_id"])
    if value["control_id"] in control_ids:
        raise ContractError("DUPLICATE_ID")
    require_text(value["owner"])
    require_text(value["invariant"])
    _require_enum(value["criticality"], _CRITICALITIES)
    require_ref(value["target_ref"])
    _require_list(value["dependencies"], maximum=MAX_CONTROL_DEPENDENCIES)
    dependency_ids: set[str] = set()
    for dependency in value["dependencies"]:
        require_id(dependency)
        if dependency in dependency_ids:
            raise ContractError("DUPLICATE_REFERENCE")
        dependency_ids.add(dependency)
    _require_list(value["obligations"], maximum=MAX_CONTROL_OBLIGATIONS, minimum=1)
    mutation_kinds: set[str] = set()
    for obligation in value["obligations"]:
        kind = _validate_obligation(obligation, obligation_ids)
        if kind == "mutation":
            mutation_kinds.add(kind)
    if value["criticality"] == "critical" and any(
        obligation["required"] is not True for obligation in value["obligations"]
    ):
        raise ContractError("CRITICAL_OPTIONAL")
    status = _validate_mutation_applicability(value["mutation_applicability"])
    if status == "applicable" and not mutation_kinds:
        raise ContractError("MUTATION_REQUIRED")
    if status == "not_applicable" and mutation_kinds:
        raise ContractError("MUTATION_NOT_APPLICABLE")
    control_ids.add(value["control_id"])
    return value["control_id"], dependency_ids


def _validate_graph(control_ids: set[str], dependencies: dict[str, set[str]]) -> None:
    for dependency_ids in dependencies.values():
        if not dependency_ids.issubset(control_ids):
            raise ContractError("UNKNOWN_REFERENCE")
    # Controlの上限までの鎖を安全に処理するため、再帰深度に依存しない。
    colors = {control_id: 0 for control_id in control_ids}
    for control_id in control_ids:
        if colors[control_id] != 0:
            continue
        stack: list[tuple[str, bool]] = [(control_id, False)]
        while stack:
            current, leaving = stack.pop()
            if leaving:
                colors[current] = 2
                continue
            if colors[current] == 1:
                raise ContractError("CYCLE")
            if colors[current] == 2:
                continue
            colors[current] = 1
            stack.append((current, True))
            for dependency in reversed(sorted(dependencies[current])):
                if colors[dependency] == 1:
                    raise ContractError("CYCLE")
                if colors[dependency] == 0:
                    stack.append((dependency, False))


def _validated_graph(document: Any) -> tuple[dict[str, Any], dict[str, set[str]]]:
    validated = validate_registry(document)
    controls = {control["control_id"]: control for control in validated["controls"]}
    dependencies = {
        control_id: set(control["dependencies"])
        for control_id, control in controls.items()
    }
    return validated, dependencies


def validate_registry(document: Any) -> dict[str, Any]:
    """Registryを検査し、呼出し元から独立したcopyを返す。"""
    require_object(document, {"schema_version", "kind", "registry_id", "controls"})
    if type(document["schema_version"]) is not int or document["schema_version"] != 1:
        raise ContractError("UNSUPPORTED_VERSION")
    if document["kind"] != "control_registry":
        raise ContractError()
    require_id(document["registry_id"])
    _require_list(document["controls"], maximum=MAX_CONTROLS, minimum=1)
    control_ids: set[str] = set()
    obligation_ids: set[str] = set()
    dependencies: dict[str, set[str]] = {}
    for control in document["controls"]:
        control_id, dependency_ids = _validate_control(control, control_ids, obligation_ids)
        dependencies[control_id] = dependency_ids
    _validate_graph(control_ids, dependencies)
    try:
        canonical = json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        raise ContractError("INVALID_CONTRACT") from None
    if len(canonical) > MAX_DOCUMENT_BYTES:
        raise ContractError("DOCUMENT_SIZE")
    return deepcopy(document)


def _require_selected_ids(value: Any) -> list[str]:
    _require_list(value, maximum=MAX_CONTROLS, minimum=1)
    selected: list[str] = []
    seen: set[str] = set()
    for control_id in value:
        require_id(control_id)
        if control_id in seen:
            raise ContractError("DUPLICATE_REFERENCE")
        seen.add(control_id)
        selected.append(control_id)
    return selected


def dependency_closure(document: Any, selected_ids: Any) -> list[str]:
    """選択Controlとその全依存を昇順IDで返す。"""
    _, dependencies = _validated_graph(document)
    selected = _require_selected_ids(selected_ids)
    if not set(selected).issubset(dependencies):
        raise ContractError("UNKNOWN_REFERENCE")
    closure = set(selected)
    pending = list(selected)
    while pending:
        control_id = pending.pop()
        for dependency in dependencies[control_id]:
            if dependency not in closure:
                closure.add(dependency)
                pending.append(dependency)
    return sorted(closure)


def affected_controls(document: Any, changed_ids: Any) -> list[str]:
    """変更Controlと、それに依存する全Controlを昇順IDで返す。"""
    _, dependencies = _validated_graph(document)
    if changed_ids is None:
        return sorted(dependencies)
    changed = _require_selected_ids(changed_ids)
    if not set(changed).issubset(dependencies):
        raise ContractError("UNKNOWN_REFERENCE")
    reverse: dict[str, set[str]] = {control_id: set() for control_id in dependencies}
    for control_id, dependency_ids in dependencies.items():
        for dependency in dependency_ids:
            reverse[dependency].add(control_id)
    affected = set(changed)
    pending = list(changed)
    while pending:
        control_id = pending.pop()
        for dependent in reverse[control_id]:
            if dependent not in affected:
                affected.add(dependent)
                pending.append(dependent)
    return sorted(affected)
