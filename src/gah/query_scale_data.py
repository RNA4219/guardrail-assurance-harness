"""Deterministic synthetic corpora for query-scale diagnostics.

These provisioning-only fixtures are independent of the fixed 400-case acceptance
pack. They exercise structural CaseSet and reference handling; they do not establish
runtime admission, oracle independence, or real-workload performance. All three sizes
are single-stage and comparable only within this query-scale family, not against the
mixed-stage fixed 400 or normal LLM 800 paths.
"""

from __future__ import annotations

import copy
import hashlib
from collections import Counter
from typing import Any

from . import evaluation_data as _evaluation_data
from .contracts import ContractError, MAX_DOCUMENT_BYTES, require_id, require_ref
from .corpus import validate_case_set
from .wire import canonical_bytes


_NAMESPACE = "gah-query-scale-v1"
_COUNTS = frozenset({400, 800, 1600})
_FIELDS = {"schema_version", "kind", "corpus_id", "case_set", "documents", "claims"}
_DOC_FIELDS = {"ref", "document"}
_LABELS = ("positive", "negative")
_LABEL_TO_DETECTION = {"positive": "detect", "negative": "allow"}
_CLAIMS = {
    "runtime_admission": False,
    "semantic_oracle_independence": False,
    "real_workload_performance": False,
}


def _invalid() -> ContractError:
    return ContractError("QUERY_SCALE_CORPUS_INVALID")


def _add_document(documents: list[dict[str, Any]], kind: str, identifier: str, document: dict[str, Any]) -> dict[str, str]:
    require_id(kind)
    require_id(identifier)
    raw = canonical_bytes(document)
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise _invalid()
    ref = {"kind": kind, "id": identifier, "digest": hashlib.sha256(raw).hexdigest()}
    documents.append({"ref": ref, "document": document})
    return copy.deepcopy(ref)


def _input(category: str, ordinal: int, label: str) -> dict[str, Any]:
    features = _evaluation_data._FEATURES[category]
    masks = _evaluation_data._MASKS
    mask = masks[ordinal % len(masks)]
    required = [features[i] for i in range(8) if mask & (1 << i)]
    variant = ordinal // len(masks)
    observed: dict[str, bool | None] = {feature: True for feature in features}
    if label == "positive":
        observed[required[ordinal % len(required)]] = False
    unrequired = [feature for feature in features if feature not in required]
    for bit, feature in enumerate(unrequired):
        observed[feature] = bool((variant >> bit) & 1)
    value = {
        "schema_version": 1,
        "kind": "synthetic_policy_input",
        "category": category,
        "required": required,
        "observed": observed,
    }
    _evaluation_data._validate_input_document(value)
    detection = _evaluation_data._independent_detection(value)
    if detection != _LABEL_TO_DETECTION[label]:
        raise _invalid()
    return value


def _build(case_count: int) -> dict[str, Any]:
    if type(case_count) is not int or case_count not in _COUNTS:
        raise ContractError("QUERY_SCALE_COUNT_INVALID")
    per_category = case_count // len(_evaluation_data._CATEGORIES)
    if per_category % 2:
        raise _invalid()

    documents: list[dict[str, Any]] = []
    cases: list[dict[str, Any]] = []
    seen_input_digests: set[tuple[str, str]] = set()
    states: dict[str, dict[str, str]] = {}
    oracles: dict[tuple[str, tuple[str, ...], str], dict[str, str]] = {}
    for category in _evaluation_data._CATEGORIES:
        for ordinal in range(per_category):
            label = _LABELS[ordinal % len(_LABELS)]
            category_index = _evaluation_data._CATEGORIES.index(category)
            count_code = {400: "4", 800: "8", 1600: "g"}[case_count]
            suffix = f"{count_code}{category_index}{ordinal:04d}"
            case_id = f"q{suffix}"
            lineage = f"l{suffix}"
            input_doc = _input(category, ordinal // 2, label)
            input_ref = _add_document(
                documents, "synthetic_policy_input", f"i{suffix}", input_doc
            )
            input_key = (category, input_ref["digest"])
            if input_key in seen_input_digests:
                raise _invalid()
            seen_input_digests.add(input_key)

            if category not in states:
                state = {"schema_version": 1, "kind": "synthetic_initial_state", "category": category, "state": "fresh"}
                states[category] = _add_document(
                    documents, "synthetic_initial_state", f"s{count_code}{category_index}", state
                )
            state_ref = copy.deepcopy(states[category])
            detection = _LABEL_TO_DETECTION[label]
            oracle_key = (category, tuple(input_doc["required"]), detection)
            if oracle_key not in oracles:
                oracle = {
                    "schema_version": 1,
                    "kind": "synthetic_policy_oracle",
                    "category": category,
                    "required": list(input_doc["required"]),
                    "expected_detection": detection,
                }
                oracle_id = f"o{count_code}{category_index}{ordinal // 2:04d}{detection[0]}"
                oracles[oracle_key] = _add_document(
                    documents, "synthetic_policy_oracle", oracle_id, oracle
                )
            oracle_ref = copy.deepcopy(oracles[oracle_key])

            stages = [{"stage_id": "score-stage", "input_ref": input_ref, "expected_detection": detection, "event_policy": "none"}]
            cases.append({
                "case_id": case_id,
                "lineage_group": lineage,
                "category": category,
                "expected_label": label,
                "oracle_ref": oracle_ref,
                "initial_state_ref": state_ref,
                "session_steps": stages,
                "scored_stage_id": "score-stage",
            })

    case_set = {
        "schema_version": 1,
        "kind": "case_set",
        "case_set_id": f"qs1-acceptance-{case_count}",
        "purpose": "acceptance",
        "required_categories": list(_evaluation_data._CATEGORIES),
        "cases": cases,
    }
    if len(canonical_bytes(case_set)) > MAX_DOCUMENT_BYTES:
        raise ContractError("QUERY_SCALE_CASE_SET_TOO_LARGE")
    return {
        "schema_version": 1,
        "kind": "query_scale_corpus",
        "corpus_id": f"{_NAMESPACE}-{case_count}",
        "case_set": case_set,
        "documents": documents,
        "claims": dict(_CLAIMS),
        "stage_counts": {"1": case_count, "2": 0},
    }


def build_scale_corpus(case_count: int) -> dict[str, Any]:
    """Build a fresh deterministic 400/800/1600-case diagnostic corpus."""
    return _build(case_count)


def _validate(value: Any) -> dict[str, Any]:
    if type(value) is not dict or set(value) != (_FIELDS | {"stage_counts"}):
        raise _invalid()
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise _invalid()
    if value["kind"] != "query_scale_corpus" or type(value["corpus_id"]) is not str:
        raise _invalid()
    case_set = validate_case_set(value["case_set"])
    count = len(case_set["cases"])
    if count not in _COUNTS or case_set["purpose"] != "acceptance":
        raise _invalid()
    if value["corpus_id"] != f"{_NAMESPACE}-{count}" or case_set["case_set_id"] != f"qs1-acceptance-{count}":
        raise _invalid()
    if case_set["required_categories"] != list(_evaluation_data._CATEGORIES):
        raise _invalid()
    if type(value["claims"]) is not dict or value["claims"] != _CLAIMS or any(type(flag) is not bool for flag in value["claims"].values()):
        raise _invalid()

    if type(value["documents"]) is not list or len(value["documents"]) > count * 4:
        raise _invalid()
    docs: dict[tuple[str, str], tuple[dict[str, str], dict[str, Any]]] = {}
    for item in value["documents"]:
        if type(item) is not dict or set(item) != _DOC_FIELDS:
            raise _invalid()
        reference = item["ref"]
        require_ref(reference)
        document = item["document"]
        if type(document) is not dict:
            raise _invalid()
        raw = canonical_bytes(document)
        if len(raw) > MAX_DOCUMENT_BYTES or hashlib.sha256(raw).hexdigest() != reference["digest"]:
            raise _invalid()
        key = (reference["kind"], reference["id"])
        if key in docs:
            raise _invalid()
        docs[key] = (reference, document)
        kind = reference["kind"]
        if kind == "synthetic_policy_input":
            _evaluation_data._validate_input_document(document)
        elif kind == "synthetic_initial_state":
            if (set(document) != {"schema_version", "kind", "category", "state"}
                    or type(document["schema_version"]) is not int or document["schema_version"] != 1
                    or document["kind"] != kind or type(document["category"]) is not str
                    or document["category"] not in _evaluation_data._CATEGORIES
                    or type(document["state"]) is not str or document["state"] != "fresh"):
                raise _invalid()
        elif kind == "synthetic_policy_oracle":
            category = document.get("category")
            required = document.get("required")
            if (set(document) != {"schema_version", "kind", "category", "required", "expected_detection"}
                    or type(document["schema_version"]) is not int or document["schema_version"] != 1
                    or document["kind"] != kind or type(category) is not str
                    or category not in _evaluation_data._CATEGORIES or type(required) is not list
                    or not 2 <= len(required) <= 8 or any(type(feature) is not str for feature in required)
                    or len(set(required)) != len(required)
                    or required != [feature for feature in _evaluation_data._FEATURES[category] if feature in required]
                    or type(document["expected_detection"]) is not str
                    or document["expected_detection"] not in {"detect", "allow"}):
                raise _invalid()
        else:
            raise _invalid()

    if (type(value["stage_counts"]) is not dict or set(value["stage_counts"]) != {"1", "2"}
            or any(type(number) is not int for number in value["stage_counts"].values())
            or value["stage_counts"] != {"1": count, "2": 0}):
        raise _invalid()
    category_label_counts: Counter[tuple[str, str]] = Counter()
    case_ids: set[str] = set()
    lineages: set[str] = set()
    referenced: set[tuple[str, str]] = set()
    scored_input_digests: set[tuple[str, str]] = set()
    category_ordinals: Counter[str] = Counter()
    for case in case_set["cases"]:
        if case["case_id"] in case_ids or case["lineage_group"] in lineages:
            raise _invalid()
        case_ids.add(case["case_id"])
        lineages.add(case["lineage_group"])
        category_index = _evaluation_data._CATEGORIES.index(case["category"])
        count_code = {400: "4", 800: "8", 1600: "g"}[count]
        ordinal = category_ordinals[case["category"]]
        category_ordinals[case["category"]] += 1
        expected_suffix = f"{count_code}{category_index}{ordinal:04d}"
        if case["case_id"] != f"q{expected_suffix}" or case["lineage_group"] != f"l{expected_suffix}":
            raise _invalid()
        category_label_counts[(case["category"], case["expected_label"])] += 1
        if len(case["session_steps"]) != 1:
            raise _invalid()
        for reference in [case["oracle_ref"], case["initial_state_ref"]] + [stage["input_ref"] for stage in case["session_steps"]]:
            pair = docs.get((reference["kind"], reference["id"]))
            if pair is None or pair[0] != reference:
                raise _invalid()
            referenced.add((reference["kind"], reference["id"]))
        category = case["category"]
        state = docs[(case["initial_state_ref"]["kind"], case["initial_state_ref"]["id"])][1]
        oracle = docs[(case["oracle_ref"]["kind"], case["oracle_ref"]["id"])][1]
        scored = next(s for s in case["session_steps"] if s["stage_id"] == case["scored_stage_id"])
        if scored["input_ref"]["id"] != f"i{count_code}{category_index}{ordinal:04d}":
            raise _invalid()
        score_key = (scored["input_ref"]["kind"], scored["input_ref"]["digest"])
        if score_key in scored_input_digests:
            raise _invalid()
        scored_input_digests.add(score_key)
        scored_input = docs[(scored["input_ref"]["kind"], scored["input_ref"]["id"])][1]
        if state["category"] != category or oracle["category"] != category or scored_input["category"] != category:
            raise _invalid()
        expected = _evaluation_data._independent_detection(scored_input)
        expected_oracle_id = f"o{count_code}{category_index}{(ordinal // 2) % len(_evaluation_data._MASKS):04d}{expected[0]}"
        if case["initial_state_ref"]["id"] != f"s{count_code}{category_index}" or case["oracle_ref"]["id"] != expected_oracle_id:
            raise _invalid()
        if expected != _LABEL_TO_DETECTION[case["expected_label"]]:
            raise _invalid()
        if expected != scored["expected_detection"] or expected != oracle["expected_detection"] or oracle["required"] != scored_input["required"]:
            raise _invalid()
    each = count // 4
    if any(category_label_counts[(category, label)] != each for category in _evaluation_data._CATEGORIES for label in _LABELS):
        raise _invalid()
    if len(referenced) != len(docs):
        raise _invalid()
    try:
        return copy.deepcopy(value)
    except (TypeError, ValueError, RecursionError):
        raise _invalid() from None


def validate_scale_corpus(value: Any) -> dict[str, Any]:
    """Provision-time validation only; not a hot-path API and not runtime admission.

    This corpus family is single-stage; compare only its 400/800/1600 variants.
    """
    return _validate(value)
