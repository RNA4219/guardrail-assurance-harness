"""利用者提供のoffline評価結果を安全に正規化する固定import adapter。

このモジュールは、外部refの解決、認証、oracleの独立性、評価完了、CIまたは
製品runの権限を発行しない。入力の構文・参照descriptor・観測行だけを検査し、
結果は常に検証待ちのimport artifactとして返す。任意のshell、URL、runner、
prompt、secret payloadは入力契約に含めない。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, NoReturn

from .contracts import (
    ContractError,
    MAX_DOCUMENT_BYTES,
    decode_document,
    require_id,
    require_ref,
    require_uint,
)
from .productization import content_ref, read_document
from .wire import canonical_bytes


INPUT_KIND = "pilot_input_bundle"
IMPORT_KIND = "pilot_input_import"
USE_CASES = frozenset({"UC-CI", "UC-LLM"})
MAX_CASES = 10_000
MAX_CATEGORIES = 256

# 外部catalogのpayloadを受け取らず、参照descriptorのkindだけを固定する。
_SOURCE_KINDS = frozenset({"snapshot_manifest"})
_TARGET_KINDS = frozenset({"target"})
_DATASET_KINDS = frozenset({"case_set"})
_ORACLE_KINDS = frozenset({"pilot_oracle"})
_SPLIT_KINDS = frozenset({"dataset_split"})
_CI_REVISION_KINDS = frozenset({"repository_snapshot"})
_LLM_REVISION_KINDS = frozenset({"model_revision"})

_INPUT_FIELDS = {
    "schema_version",
    "kind",
    "id",
    "use_case",
    "source_ref",
    "target_ref",
    "dataset_ref",
    "oracle_ref",
    "revision_ref",
    "split_ref",
    "required_categories",
    "created_at",
    "cases",
}
_CI_CASE_FIELDS = {"case_id", "expected", "observed", "status"}
_LLM_CASE_FIELDS = {
    "case_id",
    "category",
    "expected_label",
    "prediction",
    "status",
    "oracle_independent",
    "measurement_reproduced",
    "all_measurement_obligations_met",
}
_CI_STATUSES = frozenset(
    {"COMPLETE", "MISSING", "UNKNOWN", "ERROR", "TIMEOUT", "UNSUPPORTED"}
)
_LLM_STATUSES = _CI_STATUSES
_CI_EXPECTED = frozenset({"allow", "block"})
_CI_OBSERVED = frozenset({"allow", "block", "indeterminate"})
_LLM_LABELS = frozenset({"positive", "negative", "indeterminate"})
_LLM_PREDICTIONS = frozenset({"detect", "allow", "indeterminate"})

# import artifactのlimitationsはadapterが決める固定値だけにする。
_LIMITATIONS = [
    "EXTERNAL_REFS_UNVERIFIED",
    "INDEPENDENT_ORACLE_UNVERIFIED",
    "EVALUATION_COMPLETION_UNVERIFIED",
    "PRODUCT_RUN_AUTHORITY_UNAVAILABLE",
]


def _invalid(code: str = "INVALID_INPUT") -> NoReturn:
    # 入力値や秘密を例外メッセージへ混ぜない。
    raise ContractError(code)


def _checked(value: Any) -> dict[str, Any]:
    try:
        raw = canonical_bytes(value)
        return decode_document(raw)
    except ContractError:
        raise ContractError("INVALID_INPUT") from None
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise ContractError("INVALID_INPUT") from None


def _fields(value: Any, fields: set[str]) -> None:
    if type(value) is not dict or set(value) != fields:
        _invalid()


def _id(value: Any) -> None:
    try:
        require_id(value)
    except ContractError:
        _invalid()


def _uint(value: Any) -> None:
    try:
        require_uint(value)
    except ContractError:
        _invalid()


def _enum(value: Any, allowed: frozenset[str]) -> None:
    if type(value) is not str or value not in allowed:
        _invalid()


def _optional_enum(value: Any, allowed: frozenset[str]) -> None:
    if value is not None:
        _enum(value, allowed)


def _bool(value: Any) -> None:
    if type(value) is not bool:
        _invalid()


def _ref(value: Any, allowed: frozenset[str]) -> None:
    try:
        require_ref(value)
    except ContractError:
        _invalid()
    if value["kind"] not in allowed:
        _invalid("BINDING_MISMATCH")


def _categories(value: Any, *, use_case: str) -> list[str]:
    if type(value) is not list or len(value) > MAX_CATEGORIES:
        _invalid()
    if use_case == "UC-CI" and value:
        _invalid()
    if use_case == "UC-LLM" and not value:
        _invalid()
    result: list[str] = []
    for item in value:
        _id(item)
        if item in result:
            _invalid()
        result.append(item)
    return result


def _ci_case(value: Any) -> dict[str, Any]:
    _fields(value, _CI_CASE_FIELDS)
    _id(value["case_id"])
    _enum(value["status"], _CI_STATUSES)
    if value["status"] == "COMPLETE":
        _enum(value["expected"], _CI_EXPECTED)
        _enum(value["observed"], _CI_OBSERVED)
    else:
        _optional_enum(value["expected"], _CI_EXPECTED)
        _optional_enum(value["observed"], _CI_OBSERVED)
    return value


def _llm_case(value: Any) -> dict[str, Any]:
    _fields(value, _LLM_CASE_FIELDS)
    _id(value["case_id"])
    _id(value["category"])
    _enum(value["status"], _LLM_STATUSES)
    if value["status"] == "COMPLETE":
        _enum(value["expected_label"], _LLM_LABELS)
        _enum(value["prediction"], _LLM_PREDICTIONS)
    else:
        _optional_enum(value["expected_label"], _LLM_LABELS)
        _optional_enum(value["prediction"], _LLM_PREDICTIONS)
    _bool(value["oracle_independent"])
    _bool(value["measurement_reproduced"])
    _bool(value["all_measurement_obligations_met"])
    return value


def validate_import(value: Any, *, now: int | None = None) -> dict[str, Any]:
    """固定offline入力を検査し、独立した正規化前文書を返す。

    refはkind/id/digestのdescriptorとして構文検査するだけで、外部catalogの内容を
    解決しない。各行のcase_idは一意でなければならず、summaryの自己申告fieldは
    入力契約に存在しない。
    """
    document = _checked(value)
    _fields(document, _INPUT_FIELDS)
    if type(document["schema_version"]) is not int or document["schema_version"] != 1:
        _invalid()
    if document["kind"] != INPUT_KIND:
        _invalid()
    _id(document["id"])
    _enum(document["use_case"], frozenset(USE_CASES))
    _uint(document["created_at"])
    if now is not None:
        _uint(now)

    _ref(document["source_ref"], _SOURCE_KINDS)
    _ref(document["target_ref"], _TARGET_KINDS)
    _ref(document["dataset_ref"], _DATASET_KINDS)
    _ref(document["oracle_ref"], _ORACLE_KINDS)
    _ref(document["split_ref"], _SPLIT_KINDS)
    if document["use_case"] == "UC-CI":
        _ref(document["revision_ref"], _CI_REVISION_KINDS)
    else:
        _ref(document["revision_ref"], _LLM_REVISION_KINDS)

    categories = _categories(
        document["required_categories"], use_case=document["use_case"]
    )
    cases = document["cases"]
    if type(cases) is not list or not cases or len(cases) > MAX_CASES:
        _invalid()
    case_ids: set[str] = set()
    for case in cases:
        normalized = (
            _ci_case(case) if document["use_case"] == "UC-CI" else _llm_case(case)
        )
        case_id = normalized["case_id"]
        if case_id in case_ids:
            _invalid("DUPLICATE_CASE_ID")
        case_ids.add(case_id)
        if document["use_case"] == "UC-LLM" and normalized["category"] not in categories:
            _invalid("BINDING_MISMATCH")

    # memory APIにもwireのサイズ・数値・深度制限を適用する。
    try:
        if len(canonical_bytes(document)) > MAX_DOCUMENT_BYTES:
            _invalid("DOCUMENT_SIZE")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        _invalid()
    return document


def _ci_aggregate(cases: list[dict[str, Any]]) -> tuple[dict[str, Any], list[str], list[str]]:
    counts = {
        "case_count": len(cases),
        "complete_count": 0,
        "expected_allow": 0,
        "expected_block": 0,
        "observed_allow": 0,
        "observed_block": 0,
        "matched_count": 0,
        "mismatch_count": 0,
        "unknown_count": 0,
        "missing_count": 0,
        "error_count": 0,
        "timeout_count": 0,
        "unsupported_count": 0,
    }
    missing: list[str] = []
    unknown: list[str] = []
    for case in cases:
        expected = case["expected"]
        observed = case["observed"]
        status = case["status"]
        if expected == "allow":
            counts["expected_allow"] += 1
        elif expected == "block":
            counts["expected_block"] += 1
        if status == "COMPLETE":
            counts["complete_count"] += 1
            if observed == "allow":
                counts["observed_allow"] += 1
            elif observed == "block":
                counts["observed_block"] += 1
            if observed == "indeterminate":
                counts["unknown_count"] += 1
                unknown.append(case["case_id"])
            elif expected is not None and observed == expected:
                counts["matched_count"] += 1
            elif expected is not None:
                counts["mismatch_count"] += 1
        elif status == "UNKNOWN":
            counts["unknown_count"] += 1
            unknown.append(case["case_id"])
        else:
            counts["missing_count"] += 1
            missing.append(case["case_id"])
            if status == "ERROR":
                counts["error_count"] += 1
            elif status == "TIMEOUT":
                counts["timeout_count"] += 1
            elif status == "UNSUPPORTED":
                counts["unsupported_count"] += 1
    return counts, sorted(missing), sorted(unknown)


def _llm_aggregate(
    cases: list[dict[str, Any]], required_categories: list[str]
) -> tuple[dict[str, Any], list[str], list[str]]:
    categories = {
        category: {"positive": 0, "negative": 0, "indeterminate": 0, "complete": 0}
        for category in required_categories
    }
    counts = {
        "case_count": len(cases),
        "complete_count": 0,
        "positive_denominator": 0,
        "negative_denominator": 0,
        "oracle_indeterminate": 0,
        "prediction_indeterminate": 0,
        "unknown_count": 0,
        "missing_count": 0,
        "tp": 0,
        "tn": 0,
        "fp": 0,
        "fn": 0,
        "error_count": 0,
        "timeout_count": 0,
        "unsupported_count": 0,
        "declared_oracle_independent": 0,
        "declared_measurement_reproduced": 0,
        "declared_obligations_complete": 0,
        "required_categories_have_each_100": False,
    }
    missing: list[str] = []
    unknown: list[str] = []
    for case in cases:
        status = case["status"]
        case_id = case["case_id"]
        if status == "COMPLETE":
            counts["complete_count"] += 1
            category_counts = categories[case["category"]]
            category_counts["complete"] += 1
            label = case["expected_label"]
            prediction = case["prediction"]
            if label == "positive":
                counts["positive_denominator"] += 1
                category_counts["positive"] += 1
                if prediction == "detect":
                    counts["tp"] += 1
                elif prediction == "allow":
                    counts["fn"] += 1
            elif label == "negative":
                counts["negative_denominator"] += 1
                category_counts["negative"] += 1
                if prediction == "allow":
                    counts["tn"] += 1
                elif prediction == "detect":
                    counts["fp"] += 1
            else:
                counts["oracle_indeterminate"] += 1
                category_counts["indeterminate"] += 1
                if case_id not in unknown:
                    unknown.append(case_id)
            if prediction == "indeterminate":
                counts["prediction_indeterminate"] += 1
                if case_id not in unknown:
                    unknown.append(case_id)
            if case["oracle_independent"]:
                counts["declared_oracle_independent"] += 1
            if case["measurement_reproduced"]:
                counts["declared_measurement_reproduced"] += 1
            if case["all_measurement_obligations_met"]:
                counts["declared_obligations_complete"] += 1
        elif status == "UNKNOWN":
            counts["unknown_count"] += 1
            unknown.append(case_id)
        else:
            counts["missing_count"] += 1
            missing.append(case_id)
            if status == "ERROR":
                counts["error_count"] += 1
            elif status == "TIMEOUT":
                counts["timeout_count"] += 1
            elif status == "UNSUPPORTED":
                counts["unsupported_count"] += 1

    counts["unknown_count"] += counts["oracle_indeterminate"]
    counts["unknown_count"] += counts["prediction_indeterminate"]
    counts["unknown_count"] -= sum(
        1 for case in cases
        if case["status"] == "COMPLETE"
        and case["case_id"] in unknown
        and (
            case["expected_label"] == "indeterminate"
            and case["prediction"] == "indeterminate"
        )
    )
    # unknown_countはcase単位で返し、同じcaseの二つの不確実性を二重計上しない。
    counts["unknown_count"] = len(set(unknown))
    counts["categories"] = categories
    counts["required_categories_have_each_100"] = all(
        categories[category]["positive"] >= 100
        and categories[category]["negative"] >= 100
        for category in required_categories
    )
    return counts, sorted(missing), sorted(set(unknown))


def _safe_cases(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # 入力rowは閉じたscalarだけなので、構造化コピー後もraw prompt等を持たない。
    return [dict(case) for case in cases]


def import_document(value: Any, *, now: int | None = None) -> dict[str, Any]:
    """offline入力を検証待ちimport artifactへ変換する。"""
    document = validate_import(value, now=now)
    checked_at = document["created_at"] if now is None else now
    digest = hashlib.sha256(canonical_bytes(document)).hexdigest()
    input_ref = content_ref(INPUT_KIND, document["id"], document)
    if document["use_case"] == "UC-CI":
        aggregation, missing, unknown = _ci_aggregate(document["cases"])
    else:
        aggregation, missing, unknown = _llm_aggregate(
            document["cases"], document["required_categories"]
        )
    artifact = {
        "schema_version": 1,
        "kind": IMPORT_KIND,
        "id": "import-" + digest[:32],
        "input_ref": input_ref,
        "use_case": document["use_case"],
        "source_ref": dict(document["source_ref"]),
        "target_ref": dict(document["target_ref"]),
        "dataset_ref": dict(document["dataset_ref"]),
        "oracle_ref": dict(document["oracle_ref"]),
        "revision_ref": dict(document["revision_ref"]),
        "split_ref": dict(document["split_ref"]),
        "required_categories": list(document["required_categories"]),
        "created_at": document["created_at"],
        "checked_at": checked_at,
        "cases": _safe_cases(document["cases"]),
        "aggregation": aggregation,
        "missing": missing,
        "unknown": unknown,
        "conflicts": [],
        "verification_status": "PENDING",
        "external_refs_verified": False,
        "independent_oracle_verified": False,
        "evaluation_complete": False,
        "ci_eligible": False,
        "product_run_authority": False,
        "pac_status": "NOT_RUN",
        "limitations": list(_LIMITATIONS),
    }
    try:
        return decode_document(canonical_bytes(artifact))
    except ContractError:
        raise ContractError("INVALID_INPUT") from None


def import_file(
    workspace: str | Path, path: str | Path, *, now: int | None = None
) -> dict[str, Any]:
    """workspace内の単一JSONを読み、import_documentへ渡す。"""
    # read_documentがroot外・link/reparse・IO・サイズを先に拒否する。
    document = read_document(workspace, path)
    return import_document(document, now=now)


__all__ = [
    "INPUT_KIND",
    "IMPORT_KIND",
    "USE_CASES",
    "validate_import",
    "import_document",
    "import_file",
]
