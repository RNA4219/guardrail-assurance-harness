"""固定controlled responseで評価器の正規化契約を校正する。"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from .contracts import MAX_DOCUMENT_BYTES, ContractError
from .evaluation_data import build_pack, validate_pack
from .llm_evaluator import MODEL, MAX_COMPLETION_TOKENS, MAX_RESPONSE_BYTES, SyntheticEvaluator


_REQUEST_FIELDS = {"schema_version", "kind", "pack"}
_RESULT_FIELDS = {
    "schema_version", "kind", "evaluator_id", "evaluator_profile_ref", "source_digest",
    "source_digests", "profile_digest", "pack_digest", "calibration_vectors_digest",
    "vector_count", "executed_vector_count",
    "case_count", "executed_case_count", "failed_case_ids", "failed_vector_ids",
    "failure_count", "checks", "source_stable",
    "evaluator_calibration_passed", "target_agreement_passed", "ci_eligible",
}
_EVALUATOR_ID = "gah.llm_evaluator.normalizer-v1"
_DETECTIONS = ("detect", "allow", "indeterminate")
_ACTIONS = ("apply", "block", "defer")
_SELECTIONS = tuple(
    {"detection": detection, "action": action}
    for detection in _DETECTIONS
    for action in _ACTIONS
)
_SETTINGS = {
    "evaluator_id": _EVALUATOR_ID,
    "max_response_bytes": MAX_RESPONSE_BYTES,
    "max_prompt_tokens": 32768,
    "max_completion_tokens": MAX_COMPLETION_TOKENS,
    "allowed_detections": list(_DETECTIONS),
    "allowed_actions": list(_ACTIONS),
}


def _invalid() -> ContractError:
    """利用者入力やcontrolled response本文を返さない固定エラー。"""

    return ContractError()


def _canonical(value: Any) -> bytes:
    try:
        raw = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise _invalid() from None
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise _invalid()
    return raw


def _independent_oracle(document: dict[str, Any]) -> str:
    """評価対象のoracle実装と分けた、required値だけの3値判定。"""

    values = [document["observed"][name] for name in document["required"]]
    if any(value is False for value in values):
        return "detect"
    if any(value is None for value in values):
        return "indeterminate"
    return "allow"


def _response(selection: dict[str, str], *, prompt: int = 10, completion: int = 5) -> bytes:
    body = {
        "model": MODEL,
        "usage": {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
        },
        "choices": [{
            "index": 0,
            "finish_reason": "stop",
            "message": {
                "role": "assistant",
                "content": json.dumps(selection, separators=(",", ":")),
                "tool_calls": None,
                "function_call": None,
                "refusal": None,
            },
        }],
    }
    return json.dumps(body, separators=(",", ":")).encode("utf-8")


def _documents(pack: dict[str, Any]) -> dict[tuple[str, str, str], dict[str, Any]]:
    return {
        (item["ref"]["kind"], item["ref"]["id"], item["ref"]["digest"]): item["document"]
        for item in pack["documents"]
    }


def _input_for(pack_docs: dict[tuple[str, str, str], dict[str, Any]], ref: dict[str, str]) -> dict[str, Any]:
    try:
        return pack_docs[(ref["kind"], ref["id"], ref["digest"])]
    except (KeyError, TypeError):
        raise _invalid() from None


def _expected_record(
    case: dict[str, Any], stage: dict[str, Any], document: dict[str, Any],
    raw: bytes, selection: dict[str, str], counter_before: int,
    *, prompt: int = 10, completion: int = 5,
) -> dict[str, Any]:
    applied = selection["action"] == "apply"
    expected = _independent_oracle(document)
    return {
        "case_id": case["case_id"],
        "stage_id": stage["stage_id"],
        "status": "COMPLETE",
        "reason_code": None,
        "usage": {
            "completion_tokens": completion,
            "prompt_tokens": prompt,
            "total_tokens": prompt + completion,
        },
        "selection": deepcopy(selection),
        "response_digest": hashlib.sha256(raw).hexdigest(),
        "observations": {
            "detection": selection["detection"],
            "deviation": None if expected == "indeterminate" else applied and expected == "detect",
        },
        "effect": {
            "counter_before": counter_before,
            "counter_after": counter_before + (1 if applied else 0),
            "applied": applied,
        },
    }


def _source_parts() -> dict[str, str]:
    """実際に校正へ影響するsourceを要素別digestへ固定する。"""

    paths = {
        "measurement_calibration.py": Path(__file__),
        "llm_evaluator.py": Path(__file__).with_name("llm_evaluator.py"),
        "evaluation_data.py": Path(__file__).with_name("evaluation_data.py"),
        "contracts.py": Path(__file__).with_name("contracts.py"),
        "corpus.py": Path(__file__).with_name("corpus.py"),
    }
    parts: dict[str, str] = {}
    try:
        for name, path in paths.items():
            parts[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        raise _invalid() from None
    return parts


def _profile_digest(
    source_digests: dict[str, str], pack_digest: str, vector_digest: str,
) -> str:
    profile = {
        "source_digests": source_digests,
        "pack_digest": pack_digest,
        "calibration_vectors_digest": vector_digest,
        "settings": _SETTINGS,
    }
    return hashlib.sha256(_canonical(profile)).hexdigest()


def _source_aggregate(source_digests: dict[str, str]) -> str:
    return hashlib.sha256(_canonical(source_digests)).hexdigest()


def _fixed_vectors(pack: dict[str, Any]) -> list[dict[str, Any]]:
    cases = pack["case_sets"]["calibration"]["cases"]
    vectors: list[dict[str, Any]] = []
    vector_number = 0
    # 全18caseへ9組を適用し、ラベル・操作の相関を作らない。
    for case in cases:
        for selection in _SELECTIONS:
            vectors.append({
                "vector_id": f"valid-{vector_number:03d}-{case['case_id']}-{selection['detection']}-{selection['action']}",
                "case_id": case["case_id"],
                "stage_selections": [deepcopy(selection) for _ in case["session_steps"]],
                "scenario": "valid",
            })
            vector_number += 1
    two_stage = next(case for case in cases if len(case["session_steps"]) == 2)
    vectors.append({
        "vector_id": f"malformed-first-{two_stage['case_id']}",
        "case_id": two_stage["case_id"],
        "stage_selections": [{"detection": "allow", "action": "apply"}],
        "scenario": "malformed_first_stage",
    })
    single_stage_cases = [
        case for case in cases[9:] if len(case["session_steps"]) == 1
    ]
    vectors.append({
        "vector_id": f"usage-boundary-{single_stage_cases[0]['case_id']}",
        "case_id": single_stage_cases[0]["case_id"],
        "stage_selections": [{"detection": "detect", "action": "block"}],
        "scenario": "usage_boundary",
    })
    vectors.append({
        "vector_id": f"usage-overflow-{single_stage_cases[1]['case_id']}",
        "case_id": single_stage_cases[1]["case_id"],
        "stage_selections": [{"detection": "allow", "action": "apply"}],
        "scenario": "usage_overflow",
    })
    return vectors


def _validate_request(request: Any) -> dict[str, Any]:
    if type(request) is not dict or set(request) != _REQUEST_FIELDS:
        raise _invalid()
    if type(request["schema_version"]) is not int or request["schema_version"] != 1:
        raise _invalid()
    if request["kind"] != "evaluator_calibration_request":
        raise _invalid()
    pack = validate_pack(request["pack"])
    try:
        fixed_digest = hashlib.sha256(_canonical(build_pack())).hexdigest()
        actual_digest = hashlib.sha256(_canonical(pack)).hexdigest()
    except ContractError:
        raise
    if actual_digest != fixed_digest:
        raise _invalid()
    return {"schema_version": 1, "kind": request["kind"], "pack": pack}


def build_calibration_request(pack: dict[str, Any]) -> dict[str, Any]:
    """固定packだけを結び付けた校正要求を作る。"""

    validated = _validate_request({
        "schema_version": 1,
        "kind": "evaluator_calibration_request",
        "pack": pack,
    })
    return deepcopy(validated)


def _run_vector(
    evaluator: SyntheticEvaluator,
    pack_docs: dict[tuple[str, str, str], dict[str, Any]],
    case: dict[str, Any],
    vector: dict[str, Any],
    transform: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> bool:
    session = evaluator.open_case("calibration", case["case_id"])
    counter = 0
    complete_match = True
    stage_selections = vector["stage_selections"]
    if vector["scenario"] == "malformed_first_stage":
        raw_values: list[bytes] = [b"not-json"]
    elif vector["scenario"] == "usage_boundary":
        raw_values = [_response(stage_selections[0], prompt=32768, completion=MAX_COMPLETION_TOKENS)]
    elif vector["scenario"] == "usage_overflow":
        raw_values = [_response(stage_selections[0], prompt=32769)]
    else:
        raw_values = [_response(selection) for selection in stage_selections]
    for index, stage in enumerate(case["session_steps"]):
        try:
            session.prepare()
            record = session.accept(raw_values[index])
        except (ContractError, IndexError):
            return False
        if transform is not None and record["status"] == "COMPLETE":
            record = transform(deepcopy(record))
        if vector["scenario"] == "malformed_first_stage":
            expected = {
                "case_id": case["case_id"], "stage_id": stage["stage_id"],
                "status": "INVALID_OUTPUT", "reason_code": "INVALID_JSON",
                "usage": None, "selection": None,
                "response_digest": hashlib.sha256(b"not-json").hexdigest(),
                "observations": None, "effect": None,
            }
            complete_match &= record == expected
            snapshot = session.snapshot()
            continuation_rejected = True
            try:
                session.prepare()
            except ContractError:
                pass
            else:
                continuation_rejected = False
            try:
                session.accept(b"not-json")
            except ContractError:
                pass
            else:
                continuation_rejected = False
            return complete_match and len(snapshot["records"]) == 1 and snapshot["failed"] and continuation_rejected
        if vector["scenario"] == "usage_overflow":
            expected = {
                "case_id": case["case_id"], "stage_id": stage["stage_id"],
                "status": "INVALID_OUTPUT", "reason_code": "INVALID_USAGE",
                "usage": None, "selection": None,
                "response_digest": hashlib.sha256(raw_values[index]).hexdigest(),
                "observations": None, "effect": None,
            }
        else:
            expected = _expected_record(
                case, stage, _input_for(pack_docs, stage["input_ref"]),
                raw_values[index], stage_selections[index], counter,
                prompt=32768 if vector["scenario"] == "usage_boundary" else 10,
                completion=MAX_COMPLETION_TOKENS if vector["scenario"] == "usage_boundary" else 5,
            )
            counter = expected["effect"]["counter_after"]
        complete_match &= record == expected
    snapshot = session.snapshot()
    if vector["scenario"] == "valid":
        return complete_match and snapshot["complete"] and not snapshot["failed"]
    if vector["scenario"] == "usage_boundary":
        return complete_match and snapshot["complete"]
    if vector["scenario"] == "usage_overflow":
        return complete_match and len(snapshot["records"]) == 1 and snapshot["failed"]
    return False


def _execute_vectors(
    pack: dict[str, Any], vectors: list[dict[str, Any]],
    transform: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> tuple[list[str], int, set[str]]:
    """各vectorを実際のsessionへ通し、失敗をvector_idで返す。"""

    evaluator = SyntheticEvaluator(pack)
    pack_docs = _documents(pack)
    case_map = {case["case_id"]: case for case in pack["case_sets"]["calibration"]["cases"]}
    failures: list[str] = []
    executed = 0
    executed_cases: set[str] = set()
    for vector in vectors:
        vector_id = vector["vector_id"]
        case_id = vector["case_id"]
        case = case_map.get(case_id)
        if case is None:
            failures.append(vector_id)
            continue
        executed += 1
        executed_cases.add(case_id)
        try:
            matched = _run_vector(evaluator, pack_docs, case, vector, transform)
        except (ContractError, KeyError, TypeError, ValueError, IndexError, RecursionError):
            matched = False
        if not matched:
            failures.append(vector_id)
    return failures, executed, executed_cases


def _degradation_checks(pack: dict[str, Any], vectors: list[dict[str, Any]]) -> dict[str, bool]:
    def always_allow(record: dict[str, Any]) -> dict[str, Any]:
        record["selection"]["detection"] = "allow"
        record["observations"]["detection"] = "allow"
        return record

    def always_detect(record: dict[str, Any]) -> dict[str, Any]:
        record["selection"]["detection"] = "detect"
        record["observations"]["detection"] = "detect"
        return record

    def complement_deviation(record: dict[str, Any]) -> dict[str, Any]:
        if record["observations"]["deviation"] is not None:
            # 誤実装: 実際のactionを見ず、未検知をそのまま逸脱にする。
            record["observations"]["deviation"] = record["observations"]["detection"] == "allow"
        return record

    transforms = {
        "always_allow_rejected": always_allow,
        "always_detect_rejected": always_detect,
        "detection_complement_rejected": complement_deviation,
    }
    checks: dict[str, bool] = {}
    for name, transform in transforms.items():
        failures, executed, _ = _execute_vectors(pack, vectors, transform)
        checks[name] = executed == len(vectors) and bool(failures)
    return checks


def calibrate_evaluator(request: dict[str, Any]) -> dict[str, Any]:
    """controlled responseを固定評価器へ通し、校正結果だけを返す。"""

    validated = _validate_request(request)
    pack = validated["pack"]
    pack_digest = hashlib.sha256(_canonical(pack)).hexdigest()
    vectors = _fixed_vectors(pack)
    vector_digest = hashlib.sha256(_canonical(vectors)).hexdigest()
    source_digests_before = _source_parts()
    failures, executed_vectors, executed_cases = _execute_vectors(pack, vectors)
    checks = _degradation_checks(pack, vectors)
    source_digests = _source_parts()
    source_stable = source_digests_before == source_digests
    if not source_stable:
        failures.append("source-integrity")
    passed = not failures and all(checks.values())
    source_digest = _source_aggregate(source_digests)
    profile_digest = _profile_digest(source_digests, pack_digest, vector_digest)
    failed_cases = []
    for vector in vectors:
        if vector["vector_id"] in failures and vector["case_id"] not in failed_cases:
            failed_cases.append(vector["case_id"])
    return {
        "schema_version": 1,
        "kind": "evaluator_calibration_result",
        "evaluator_id": _EVALUATOR_ID,
        "evaluator_profile_ref": {
            "kind": "evaluator_profile",
            "id": _EVALUATOR_ID,
            "digest": profile_digest,
        },
        "source_digest": source_digest,
        "source_digests": source_digests,
        "profile_digest": profile_digest,
        "pack_digest": pack_digest,
        "calibration_vectors_digest": vector_digest,
        "vector_count": len(vectors),
        "executed_vector_count": executed_vectors,
        "case_count": len({vector["case_id"] for vector in vectors}),
        "executed_case_count": len(executed_cases),
        "failed_case_ids": failed_cases,
        "failed_vector_ids": failures,
        "failure_count": len(failures),
        "checks": checks,
        "source_stable": source_stable,
        "evaluator_calibration_passed": passed,
        "target_agreement_passed": None,
        "ci_eligible": False,
    }


def calibrate_measurement(pack: dict[str, Any]) -> dict[str, Any]:
    """固定packを評価器校正へ結び付ける簡潔な公開入口。"""

    return calibrate_evaluator(build_calibration_request(pack))


validate_calibration_request = _validate_request
run_calibration = calibrate_evaluator


__all__ = [
    "build_calibration_request", "calibrate_evaluator", "calibrate_measurement", "run_calibration",
    "validate_calibration_request",
]
