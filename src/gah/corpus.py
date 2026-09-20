"""CaseSetの構造検査、有限集合の件数報告、評価器校正を扱う部品。"""

from __future__ import annotations

import copy
import hashlib
import json
from collections import defaultdict
from itertools import islice
from typing import Any, Iterable

from .contracts import (
    MAX_DOCUMENT_BYTES,
    ContractError,
    require_id,
    require_object,
    require_ref,
)


_CASE_SET_FIELDS = {
    "schema_version",
    "kind",
    "case_set_id",
    "purpose",
    "required_categories",
    "cases",
}
_CASE_FIELDS = {
    "case_id",
    "lineage_group",
    "category",
    "expected_label",
    "oracle_ref",
    "initial_state_ref",
    "session_steps",
    "scored_stage_id",
}
_STAGE_FIELDS = {"stage_id", "input_ref", "expected_detection", "event_policy"}
_OBSERVATION_FIELDS = {"case_id", "stage_id", "detection"}
_PURPOSES = frozenset({"development", "calibration", "acceptance"})
_LABELS = ("positive", "negative", "indeterminate")
_DETECTIONS = frozenset({"detect", "allow", "indeterminate"})
_EVENT_POLICIES = frozenset({"forbidden", "aggregate", "none"})
_MAX_CATEGORIES = 256
_MAX_CASES = 10_000
_MAX_OTHER_SETS = 64
_MAX_OBSERVATIONS = _MAX_CASES * 2
_LABEL_TO_DETECTION = {
    "positive": "detect",
    "negative": "allow",
    "indeterminate": "indeterminate",
}
_CALIBRATION_REASON_ORDER = (
    "PURPOSE_NOT_CALIBRATION",
    "CASE_CONTENT_DUPLICATE",
    "CASE_LINEAGE_REUSE",
    "OBSERVATION_INVALID",
    "OBSERVATION_EXTRA",
    "OBSERVATION_DUPLICATE",
    "OBSERVATION_MISSING",
    "OBSERVATION_MISMATCH",
    "LABEL_MISSING",
)


def _invalid() -> ContractError:
    # 入力内容を例外やレポートへ含めず、境界からの漏えいを防ぐ。
    return ContractError()


def _require_enum(value: Any, values: frozenset[str]) -> None:
    if type(value) is not str or value not in values:
        raise _invalid()


def _require_id_list(value: Any, *, nonempty: bool) -> None:
    if (
        type(value) is not list
        or (nonempty and not value)
        or len(value) > _MAX_CATEGORIES
    ):
        raise _invalid()
    seen: set[str] = set()
    for item in value:
        require_id(item)
        if item in seen:
            raise _invalid()
        seen.add(item)


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        raise _invalid() from None


def _ref_content(reference: dict[str, str]) -> dict[str, str]:
    """参照IDに依存せず、kindと内容digestだけを取り出す。"""

    return {"kind": reference["kind"], "digest": reference["digest"]}


def _sample_fingerprint(case: dict[str, Any]) -> str:
    """同一実行入力を表すfingerprintを作る。

    case/lineage/stage/ref ID、カテゴリ、label、oracleは入力内容の核から除外し、
    段階順、初期状態、各段階のinput参照のkind+digestだけを保持する。
    """

    sample = {
        "initial_state": _ref_content(case["initial_state_ref"]),
        "inputs": [
            _ref_content(stage["input_ref"])
            for stage in case["session_steps"]
        ],
    }
    return hashlib.sha256(_canonical(sample)).hexdigest()


def _condition_signature(case: dict[str, Any]) -> dict[str, Any]:
    """sample再利用時に差分として追跡する条件を正規化する。"""

    scored_position = next(
        index
        for index, stage in enumerate(case["session_steps"])
        if stage["stage_id"] == case["scored_stage_id"]
    )
    return {
        "category": case["category"],
        "expected_label": case["expected_label"],
        "oracle": _ref_content(case["oracle_ref"]),
        "stage_conditions": [
            {
                "expected_detection": stage["expected_detection"],
                "event_policy": stage["event_policy"],
            }
            for stage in case["session_steps"]
        ],
        "scored_stage_position": scored_position,
    }


def _validate_stage(stage: Any) -> None:
    require_object(stage, _STAGE_FIELDS)
    require_id(stage["stage_id"])
    require_ref(stage["input_ref"])
    _require_enum(stage["expected_detection"], _DETECTIONS)
    _require_enum(stage["event_policy"], _EVENT_POLICIES)


def _validate_case(case: Any) -> None:
    require_object(case, _CASE_FIELDS)
    require_id(case["case_id"])
    require_id(case["lineage_group"])
    require_id(case["category"])
    _require_enum(case["expected_label"], frozenset(_LABELS))
    require_ref(case["oracle_ref"])
    require_ref(case["initial_state_ref"])
    stages = case["session_steps"]
    if type(stages) is not list or not 1 <= len(stages) <= 2:
        raise _invalid()
    stage_ids: set[str] = set()
    for stage in stages:
        _validate_stage(stage)
        stage_id = stage["stage_id"]
        if stage_id in stage_ids:
            raise _invalid()
        stage_ids.add(stage_id)
    scored_stage_id = case["scored_stage_id"]
    require_id(scored_stage_id)
    if scored_stage_id not in stage_ids:
        raise _invalid()
    scored_stage = next(
        stage for stage in stages if stage["stage_id"] == scored_stage_id
    )
    if scored_stage["expected_detection"] != _LABEL_TO_DETECTION[case["expected_label"]]:
        raise _invalid()


def _validate_case_set(document: dict, *, copy_result: bool) -> dict:
    """CaseSetを完全検査し、必要な場合だけ独立copyを返す。"""

    require_object(document, _CASE_SET_FIELDS)
    if type(document["schema_version"]) is not int or document["schema_version"] != 1:
        raise _invalid()
    if type(document["kind"]) is not str or document["kind"] != "case_set":
        raise _invalid()
    require_id(document["case_set_id"])
    _require_enum(document["purpose"], _PURPOSES)
    _require_id_list(document["required_categories"], nonempty=True)
    cases = document["cases"]
    if type(cases) is not list or not cases or len(cases) > _MAX_CASES:
        raise _invalid()
    case_ids: set[str] = set()
    for case in cases:
        _validate_case(case)
        case_id = case["case_id"]
        if case_id in case_ids:
            raise _invalid()
        case_ids.add(case_id)
        if case["category"] not in document["required_categories"]:
            raise _invalid()
    if len(_canonical(document)) > MAX_DOCUMENT_BYTES:
        raise _invalid()
    if not copy_result:
        return document
    try:
        return copy.deepcopy(document)
    except (TypeError, ValueError, RecursionError):
        raise _invalid() from None


def validate_case_set(document: dict) -> dict:
    """CaseSetを検査し、呼出し側から独立したdeepcopyを返す。"""
    return _validate_case_set(document, copy_result=True)


def _validate_case_set_for_report(document: dict) -> dict:
    """完全検査後のCaseSetを、変更しないレポート計算向けに読む。"""
    return _validate_case_set(document, copy_result=False)


def _lineage_groups(cases: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for case in cases:
        groups[case["lineage_group"]].append(case)
    return dict(groups)


def _case_ids_by_sample(cases: list[dict[str, Any]]) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = defaultdict(list)
    for case in cases:
        groups[_sample_fingerprint(case)].append(case["case_id"])
    return dict(groups)


def _lineage_counts(cases: list[dict[str, Any]]) -> dict[str, int]:
    return {
        lineage: len(group)
        for lineage, group in sorted(_lineage_groups(cases).items())
    }


def _empty_label_counts() -> dict[str, int]:
    return {label: 0 for label in _LABELS}


def _report_for_sets(
    case_set: dict[str, Any], other_sets: Iterable[dict[str, Any]]
) -> dict[str, Any]:
    cases = case_set["cases"]
    label_counts = _empty_label_counts()
    category_counts = {
        category: _empty_label_counts()
        for category in case_set["required_categories"]
    }
    category_lineages: dict[str, dict[str, set[str]]] = {
        category: {label: set() for label in _LABELS}
        for category in case_set["required_categories"]
    }
    label_lineages: dict[str, set[str]] = {label: set() for label in _LABELS}
    for case in cases:
        label = case["expected_label"]
        category = case["category"]
        lineage = case["lineage_group"]
        label_counts[label] += 1
        category_counts[category][label] += 1
        label_lineages[label].add(lineage)
        category_lineages[category][label].add(lineage)

    lineage_reuse = []
    lineage_conflicts = []
    for lineage, group in sorted(_lineage_groups(cases).items()):
        if len(group) > 1:
            lineage_reuse.append(
                {
                    "lineage_group": lineage,
                    "case_ids": sorted(case["case_id"] for case in group),
                }
            )
            labels = sorted({case["expected_label"] for case in group})
            categories = sorted({case["category"] for case in group})
            if len(labels) > 1 or len(categories) > 1:
                lineage_conflicts.append(
                    {
                        "lineage_group": lineage,
                        "case_ids": sorted(case["case_id"] for case in group),
                        "labels": labels,
                        "categories": categories,
                    }
                )

    duplicate_content_groups = [
        {
            "content_digest": fingerprint,
            "sample_fingerprint": fingerprint,
            "case_ids": sorted(case_ids),
        }
        for fingerprint, case_ids in sorted(_case_ids_by_sample(cases).items())
        if len(case_ids) > 1
    ]
    cases_by_id = {case["case_id"]: case for case in cases}
    sample_condition_differences = []
    for group in duplicate_content_groups:
        signatures = {
            json.dumps(
                _condition_signature(cases_by_id[case_id]),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            for case_id in group["case_ids"]
        }
        if len(signatures) > 1:
            fields = []
            reference = _condition_signature(cases_by_id[group["case_ids"][0]])
            for field in reference:
                if any(
                    _condition_signature(cases_by_id[case_id])[field]
                    != reference[field]
                    for case_id in group["case_ids"][1:]
                ):
                    fields.append(field)
            sample_condition_differences.append(
                {
                    "sample_fingerprint": group["sample_fingerprint"],
                    "case_ids": group["case_ids"],
                    "differing_fields": fields,
                }
            )

    validated_others = [_validate_case_set_for_report(other) for other in other_sets]
    all_sets = [
        case_set,
        *sorted(validated_others, key=lambda item: item["case_set_id"]),
    ]
    usage_overlaps = []
    for left_index, left in enumerate(all_sets):
        left_content = _case_ids_by_sample(left["cases"])
        left_lineages = set(_lineage_groups(left["cases"]))
        for right in all_sets[left_index + 1 :]:
            right_content = _case_ids_by_sample(right["cases"])
            content_overlap = sorted(set(left_content) & set(right_content))
            lineage_overlap = sorted(
                left_lineages & set(_lineage_groups(right["cases"]))
            )
            if content_overlap or lineage_overlap:
                usage_overlaps.append(
                    {
                        "case_set_ids": [
                            left["case_set_id"],
                            right["case_set_id"],
                        ],
                        "purposes": [left["purpose"], right["purpose"]],
                        "content_digest_overlap": content_overlap,
                        "sample_fingerprint_overlap": content_overlap,
                        "lineage_overlap": lineage_overlap,
                    }
                )

    positive_lineages = len(label_lineages["positive"])
    negative_lineages = len(label_lineages["negative"])
    per_category = {}
    for category in case_set["required_categories"]:
        positive = len(category_lineages[category]["positive"])
        negative = len(category_lineages[category]["negative"])
        per_category[category] = {
            "positive_lineages": positive,
            "negative_lineages": negative,
            "positive_minimum_met": positive >= 100,
            "negative_minimum_met": negative >= 100,
        }
    acceptance_minimums_met = (
        positive_lineages >= 200
        and negative_lineages >= 200
        and all(
            values["positive_minimum_met"] and values["negative_minimum_met"]
            for values in per_category.values()
        )
        and not lineage_reuse
        and not duplicate_content_groups
    )
    category_shortfalls = sorted(
        category
        for category, values in per_category.items()
        if not values["positive_minimum_met"] or not values["negative_minimum_met"]
    )
    return {
        "schema_version": 1,
        "kind": "corpus_report",
        "case_set_id": case_set["case_set_id"],
        "purpose": case_set["purpose"],
        "case_count": len(cases),
        "label_counts": label_counts,
        "category_counts": category_counts,
        "lineage_counts": _lineage_counts(cases),
        "duplicate_content_groups": duplicate_content_groups,
        "sample_condition_differences": sample_condition_differences,
        "lineage_reuse": lineage_reuse,
        "lineage_conflicts": lineage_conflicts,
        "usage_overlaps": usage_overlaps,
        "structural_requirements": {
            "positive_lineages": positive_lineages,
            "negative_lineages": negative_lineages,
            "per_category": per_category,
            "shortfalls": {
                "positive_lineages": max(0, 200 - positive_lineages),
                "negative_lineages": max(0, 200 - negative_lineages),
                "categories": category_shortfalls,
            },
            "acceptance_minimums_met": acceptance_minimums_met,
        },
        "independence_validated": False,
        "oracle_validated": False,
        "claims": {
            "population_performance": "unverified",
            "meaningful_independence": "unverified",
            "oracle_authentication": "unverified",
        },
    }


def corpus_report(document: dict, other_sets: Iterable[dict] = ()) -> dict:
    """CaseSetの構造的な件数・重複を報告する。

    件数の充足は構造上の結果であり、母集団性能、意味上の独立性、
    oracleの認証を意味しない。
    """

    # A lazy iterable can mutate document while it is consumed. Keep the
    # historical detached snapshot for that boundary; exact list/tuple inputs
    # have no iteration callbacks, so the internal read-only path is safe.
    case_set = (
        _validate_case_set_for_report(document)
        if type(other_sets) in (list, tuple)
        else validate_case_set(document)
    )
    if isinstance(other_sets, (str, bytes, dict)):
        raise _invalid()
    try:
        other_values = tuple(islice(other_sets, _MAX_OTHER_SETS + 1))
    except (TypeError, ValueError):
        raise _invalid() from None
    if len(other_values) > _MAX_OTHER_SETS:
        raise _invalid()
    return _report_for_sets(case_set, other_values)


def _invalid_calibration_report(case_set: dict, reasons: set[str], count: int) -> dict[str, Any]:
    ordered_reasons = [
        code for code in _CALIBRATION_REASON_ORDER if code in reasons
    ]
    return {
        "schema_version": 1,
        "kind": "calibration_report",
        "case_set_id": case_set["case_set_id"],
        "purpose": case_set["purpose"],
        "passed": False,
        "observation_count": count,
        "expected_observation_count": sum(
            len(case["session_steps"]) for case in case_set["cases"]
        ),
        "label_presence": {
            label: any(case["expected_label"] == label for case in case_set["cases"])
            for label in _LABELS
        },
        "reasons": ordered_reasons,
        "independence_validated": False,
        "oracle_validated": False,
    }


def check_calibration(case_set: dict, observations: Any) -> dict:
    """既知ラベルと全段階の観測が一致するかを構造的に校正する。"""

    validated = validate_case_set(case_set)
    if type(observations) is not list:
        return _invalid_calibration_report(
            validated, {"OBSERVATION_INVALID"}, 0
        )
    if len(observations) > _MAX_OBSERVATIONS:
        return _invalid_calibration_report(
            validated, {"OBSERVATION_EXTRA"}, len(observations)
        )

    reasons: set[str] = set()
    if validated["purpose"] != "calibration":
        reasons.add("PURPOSE_NOT_CALIBRATION")
    if any(
        len(case_ids) > 1
        for case_ids in _case_ids_by_sample(validated["cases"]).values()
    ):
        reasons.add("CASE_CONTENT_DUPLICATE")
    if any(
        len(group) > 1 for group in _lineage_groups(validated["cases"]).values()
    ):
        reasons.add("CASE_LINEAGE_REUSE")
    label_presence = {
        label: any(
            case["expected_label"] == label for case in validated["cases"]
        )
        for label in _LABELS
    }
    if not all(label_presence.values()):
        reasons.add("LABEL_MISSING")

    expected: dict[tuple[str, str], str] = {}
    for case in validated["cases"]:
        for stage in case["session_steps"]:
            expected[(case["case_id"], stage["stage_id"])] = stage[
                "expected_detection"
            ]

    seen: set[tuple[str, str]] = set()
    for observation in observations:
        if type(observation) is not dict or set(observation) != _OBSERVATION_FIELDS:
            reasons.add("OBSERVATION_INVALID")
            continue
        try:
            require_id(observation["case_id"])
            require_id(observation["stage_id"])
            _require_enum(observation["detection"], _DETECTIONS)
        except ContractError:
            reasons.add("OBSERVATION_INVALID")
            continue
        key = (observation["case_id"], observation["stage_id"])
        if key not in expected:
            reasons.add("OBSERVATION_EXTRA")
            continue
        if key in seen:
            reasons.add("OBSERVATION_DUPLICATE")
        seen.add(key)
        if observation["detection"] != expected[key]:
            reasons.add("OBSERVATION_MISMATCH")
    if seen != set(expected):
        reasons.add("OBSERVATION_MISSING")

    result = _invalid_calibration_report(validated, reasons, len(observations))
    if not reasons:
        result["passed"] = True
        result["reasons"] = []
    return result


__all__ = ["check_calibration", "corpus_report", "validate_case_set"]
