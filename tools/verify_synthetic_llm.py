"""固定合成LLM評価packを監督し、raw応答を保存しない診断CLI。"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gah.contracts import ContractError, decode_document
from gah.evaluation_data import validate_pack
from gah.llm_evaluator import MAX_COMPLETION_TOKENS
from gah.measurement_calibration import calibrate_measurement


PACK_PATH = ROOT / "datasets" / "synthetic-policy-v1" / "pack.json"
CASE_TIMEOUT_SECONDS = 120
RUN_TIMEOUT_SECONDS = 5400
MAX_CASES = 10000
MAX_CALLS = 20000
MAX_TOKENS = 10000000
MAX_CHILD_STDOUT = 65536
MAX_PROMPT_TOKENS = 32768
RESERVED_TOKENS_PER_STAGE = MAX_PROMPT_TOKENS + MAX_COMPLETION_TOKENS
ENDPOINT = "http://127.0.0.1:18000/v1/chat/completions"
MODEL = "qwen3.8-flash-next"
_REASON_CODES = {
    "RESPONSE_SIZE", "INVALID_USAGE", "MODEL_MISMATCH", "INVALID_CHOICES",
    "INVALID_FINISH", "INVALID_MESSAGE", "CONTENT_SIZE", "INVALID_SELECTION",
    "NON_INTEGER_NUMBER", "DOCUMENT_SIZE", "DOCUMENT_COMPLEXITY", "INTEGER_RANGE",
    "INVALID_JSON", "INVALID_RESPONSE", "INPUT_TYPE", "SESSION_STATE",
    "PROVIDER_TIMEOUT", "REQUEST_SIZE", "HTTP_STATUS", "CHILD_RESULT_SIZE",
    "INVALID_CONTRACT", "DUPLICATE_KEY",
}


def _invalid() -> ContractError:
    return ContractError()


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise _invalid() from None


def _load_pack() -> dict[str, Any]:
    try:
        with PACK_PATH.open("rb") as stream:
            raw = stream.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise _invalid()

        def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, item in items:
                if key in result:
                    raise _invalid()
                result[key] = item
            return result

        return validate_pack(json.loads(raw.decode("utf-8"), object_pairs_hook=pairs))
    except ContractError:
        raise
    except (OSError, UnicodeError, ValueError, RecursionError):
        raise _invalid() from None


def _read_bounded(stream: Any, limit: int, holder: dict[str, Any]) -> None:
    captured = bytearray()
    total = 0
    try:
        while True:
            chunk = stream.read(8192)
            if not chunk:
                break
            total += len(chunk)
            if len(captured) <= limit:
                captured.extend(chunk[: max(0, limit + 1 - len(captured))])
    except (AttributeError, OSError, ValueError):
        holder["read_error"] = True
    holder["total"] = total
    holder["data"] = bytes(captured)


def _wait_child(argv: list[str]) -> dict[str, Any]:
    """shell=Falseのchildを上限付きstdout・固定hard timeoutで1回実行する。"""

    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        process = subprocess.Popen(
            argv,
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            shell=False,
            creationflags=creationflags,
        )
    except OSError:
        return {
            "status": "ERROR",
            "reason_code": "CHILD_START_FAILED",
            "stdout": b"",
            "returncode": None,
            "remote_stop_unknown": False,
        }
    holder: dict[str, Any] = {}
    reader = threading.Thread(target=_read_bounded, args=(process.stdout, MAX_CHILD_STDOUT, holder), daemon=True)
    reader.start()
    try:
        returncode = process.wait(timeout=CASE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except OSError:
            pass
        try:
            process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            returncode = None
        else:
            returncode = process.returncode
        reader.join(timeout=5)
        return {
            "status": "TIMEOUT",
            "reason_code": "CASE_TIMEOUT",
            "stdout": b"",
            "returncode": returncode,
            "remote_stop_unknown": True,
        }
    reader.join(timeout=5)
    if reader.is_alive() or holder.get("read_error"):
        return {
            "status": "ERROR",
            "reason_code": "CHILD_OUTPUT_READ_FAILED",
            "stdout": b"",
            "returncode": returncode,
            "remote_stop_unknown": False,
        }
    if holder.get("total", 0) > MAX_CHILD_STDOUT:
        return {
            "status": "ERROR",
            "reason_code": "CHILD_OUTPUT_SIZE",
            "stdout": b"",
            "returncode": returncode,
            "remote_stop_unknown": False,
        }
    return {
        "status": "EXITED",
        "reason_code": None,
        "stdout": holder.get("data", b""),
        "returncode": returncode,
        "remote_stop_unknown": False,
    }


def _record_shape(value: Any, case_id: str, stage_ids: list[str], counter_before: int) -> int | None:
    fields = {
        "case_id", "stage_id", "status", "reason_code", "usage", "selection",
        "response_digest", "observations", "effect",
    }
    if type(value) is not dict or set(value) != fields:
        return None
    if (type(value["case_id"]) is not str or value["case_id"] != case_id
            or type(value["stage_id"]) is not str or value["stage_id"] not in stage_ids):
        return None
    if type(value["status"]) is not str or value["status"] not in {"COMPLETE", "INVALID_OUTPUT"}:
        return None
    if value["response_digest"] is not None:
        if (type(value["response_digest"]) is not str
                or len(value["response_digest"]) != 64
                or any(char not in "0123456789abcdef" for char in value["response_digest"])):
            return None
    if value["status"] == "COMPLETE":
        if value["reason_code"] is not None:
            return None
        selection = value["selection"]
        if (type(selection) is not dict or set(selection) != {"detection", "action"}
                or type(selection["detection"]) is not str
                or selection["detection"] not in {"detect", "allow", "indeterminate"}
                or type(selection["action"]) is not str
                or selection["action"] not in {"apply", "block", "defer"}):
            return None
        usage = value["usage"]
        if (type(usage) is not dict
                or set(usage) != {"completion_tokens", "prompt_tokens", "total_tokens"}
                or any(type(item) is not int or item < 0 for item in usage.values())
                or usage["prompt_tokens"] > MAX_PROMPT_TOKENS
                or usage["completion_tokens"] > MAX_COMPLETION_TOKENS
                or usage["prompt_tokens"] + usage["completion_tokens"] != usage["total_tokens"]):
            return None
        observations = value["observations"]
        if (type(observations) is not dict or set(observations) != {"detection", "deviation"}
                or type(observations["detection"]) is not str
                or observations["detection"] != selection["detection"]
                or (observations["deviation"] is not None
                    and type(observations["deviation"]) is not bool)):
            return None
        effect = value["effect"]
        if (type(effect) is not dict or set(effect) != {"counter_before", "counter_after", "applied"}
                or any(type(effect[key]) is not int for key in ("counter_before", "counter_after"))
                or effect["counter_before"] < 0 or effect["counter_after"] < 0
                or type(effect["applied"]) is not bool
                or effect["counter_before"] != counter_before):
            return None
        expected_applied = selection["action"] == "apply"
        if (effect["applied"] != expected_applied
                or effect["counter_after"] != counter_before + (1 if expected_applied else 0)):
            return None
        return effect["counter_after"]
    usage = value["usage"]
    if usage is not None and (
            type(usage) is not dict
            or set(usage) != {"completion_tokens", "prompt_tokens", "total_tokens"}
            or any(type(item) is not int or item < 0 for item in usage.values())
            or usage["prompt_tokens"] > MAX_PROMPT_TOKENS
            or usage["completion_tokens"] > MAX_COMPLETION_TOKENS
            or usage["prompt_tokens"] + usage["completion_tokens"] != usage["total_tokens"]):
        return None
    if (type(value["reason_code"]) is not str or value["reason_code"] not in _REASON_CODES
            or value["selection"] is not None
            or value["observations"] is not None or value["effect"] is not None):
        return None
    return counter_before


def _parse_child_output(raw: bytes, purpose: str, case: dict[str, Any], pack_digest: str) -> dict[str, Any]:
    if type(raw) is not bytes or len(raw) > MAX_CHILD_STDOUT:
        raise _invalid()
    try:
        value = decode_document(raw)
    except ContractError:
        raise _invalid() from None
    fields = {
        "schema_version", "kind", "purpose", "case_id", "pack_digest", "endpoint",
        "provider_model", "records", "complete", "failed", "remote_stop_unknown", "ci_eligible",
    }
    if type(value) is not dict or set(value) != fields:
        raise _invalid()
    if (type(value["schema_version"]) is not int or value["schema_version"] != 1 or value["kind"] != "synthetic_llm_case_result"
            or value["purpose"] != purpose or value["case_id"] != case["case_id"]
            or value["pack_digest"] != pack_digest or value["endpoint"] != ENDPOINT
            or value["provider_model"] != MODEL or type(value["complete"]) is not bool
            or type(value["failed"]) is not bool or type(value["remote_stop_unknown"]) is not bool
            or value["ci_eligible"] is not False):
        raise _invalid()
    records = value["records"]
    stage_ids = [stage["stage_id"] for stage in case["session_steps"]]
    if type(records) is not list or not 1 <= len(records) <= len(stage_ids):
        raise _invalid()
    seen = set()
    counter = 0
    for record in records:
        next_counter = _record_shape(record, case["case_id"], stage_ids, counter)
        if next_counter is None or record["stage_id"] in seen:
            raise _invalid()
        counter = next_counter
        seen.add(record["stage_id"])
    if [record["stage_id"] for record in records] != stage_ids[: len(records)]:
        raise _invalid()
    if value["complete"] and (value["failed"] or len(records) != len(stage_ids)
                               or seen != set(stage_ids)
                               or any(record["status"] != "COMPLETE" or record["usage"] is None
                                      for record in records)):
        raise _invalid()
    if not value["complete"] and (not value["failed"]
                                   or records[-1]["status"] != "INVALID_OUTPUT"
                                   or any(record["status"] == "INVALID_OUTPUT" for record in records[:-1])):
        raise _invalid()
    return value


def _expected_case_record(purpose: str, case: dict[str, Any], pack_digest: str) -> dict[str, Any]:
    """子が失敗・不正出力した場合にも保存できる固定record。"""

    return {
        "case_id": case["case_id"],
        "purpose": purpose,
        "pack_digest": pack_digest,
        "status": "ERROR",
        "reason_code": "CHILD_OUTPUT_INVALID",
        "records": [],
        "complete": False,
        "failed": True,
        "remote_stop_unknown": False,
        "expected_stage_count": len(case["session_steps"]),
        "expected_label": case["expected_label"],
        "ci_eligible": False,
    }


def run_child_case(pack: dict[str, Any], purpose: str, case: dict[str, Any]) -> dict[str, Any]:
    """caseをchildへ一度だけ渡し、保存可能な構造結果へ正規化する。"""

    pack_digest = hashlib.sha256(_canonical(pack)).hexdigest()
    argv = [
        sys.executable,
        "-E",
        "-X",
        "utf8",
        "-m",
        "tools.synthetic_llm_provider",
        "--purpose",
        purpose,
        "--case-id",
        case["case_id"],
    ]
    child = _wait_child(argv)
    if child["status"] == "TIMEOUT":
        result = _expected_case_record(purpose, case, pack_digest)
        result.update(status="TIMEOUT", reason_code="CASE_TIMEOUT", remote_stop_unknown=True)
        return result
    if child["status"] != "EXITED":
        result = _expected_case_record(purpose, case, pack_digest)
        result["reason_code"] = child["reason_code"] or "CHILD_EXIT"
        return result
    try:
        parsed = _parse_child_output(child["stdout"], purpose, case, pack_digest)
    except ContractError:
        return _expected_case_record(purpose, case, pack_digest)
    # childは失敗した段階を構造化して終了1にする。rawが契約済みなら
    # その最終段とremote停止不明フラグだけを採用し、未確定の露出を保持する。
    if parsed["complete"] != (child["returncode"] == 0):
        return _expected_case_record(purpose, case, pack_digest)
    return {
        "case_id": case["case_id"],
        "purpose": purpose,
        "pack_digest": pack_digest,
        "status": "COMPLETE" if parsed["complete"] else "ERROR",
        "reason_code": None if parsed["complete"] else "CHILD_INCOMPLETE",
        "records": parsed["records"],
        "complete": parsed["complete"],
        "failed": parsed["failed"],
        "remote_stop_unknown": parsed["remote_stop_unknown"],
        "expected_stage_count": len(case["session_steps"]),
        "expected_label": case["expected_label"],
        "ci_eligible": False,
    }


def _case_result(purpose: str, case: dict[str, Any], pack: dict[str, Any]) -> dict[str, Any]:
    return run_child_case(pack, purpose, case)


def _aggregate(
    purpose: str,
    cases: list[dict[str, Any]],
    results: list[dict[str, Any]],
    pack_digest: str,
    *,
    reserved_case_ids: set[str] | None = None,
) -> dict[str, Any]:
    counts = {key: 0 for key in ("TP", "FN", "FP", "TN", "indeterminate_expected", "indeterminate_match", "indeterminate_mismatch")}
    expected_stage_count = sum(len(case["session_steps"]) for case in cases)
    observed_stage_count = 0
    mismatched_stages = 0
    usage_totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    usage_known = True
    complete_count = 0
    stage_map = {case["case_id"]: case for case in cases}
    for result in results:
        case_id = result["case_id"].split(":", 1)[-1] if ":" in result["case_id"] else result["case_id"]
        case = stage_map[case_id]
        result_records = result.get("records", [])
        if type(result_records) is not list:
            usage_known = False
            continue
        observed_stage_count += len(result_records)
        for record in result_records:
            usage = record.get("usage") if type(record) is dict else None
            if type(usage) is not dict:
                usage_known = False
            else:
                for key in usage_totals:
                    usage_totals[key] += usage[key]
        if not result["complete"]:
            usage_known = False
            continue
        complete_count += 1
        records = {record["stage_id"]: record for record in result["records"]}
        for stage in case["session_steps"]:
            record = records.get(stage["stage_id"])
            if record is None or record["status"] != "COMPLETE":
                mismatched_stages += 1
                usage_known = False
                continue
            if record["selection"]["detection"] != stage["expected_detection"]:
                mismatched_stages += 1
        scored = records.get(case["session_steps"][-1]["stage_id"])
        if scored is None or scored["status"] != "COMPLETE":
            continue
        actual = scored["selection"]["detection"]
        expected = case["expected_label"]
        if expected == "indeterminate":
            counts["indeterminate_expected"] += 1
        if actual == "indeterminate":
            if expected == "indeterminate":
                counts["indeterminate_match"] += 1
            else:
                counts["indeterminate_mismatch"] += 1
        elif expected == "positive":
            counts["TP" if actual == "detect" else "FN"] += 1
        elif expected == "negative":
            counts["TN" if actual == "allow" else "FP"] += 1
        elif expected == "indeterminate":
            counts["indeterminate_mismatch"] += 1
        else:
            counts["indeterminate_mismatch"] += 1
    result_map = {result["case_id"]: result for result in results}
    if reserved_case_ids is None:
        reserved_case_ids = {case["case_id"] for case in cases}
    reserved_remaining = sum(
        RESERVED_TOKENS_PER_STAGE * len(case["session_steps"])
        for case in cases
        if case["case_id"] in reserved_case_ids
        and (case["case_id"] not in result_map
             or not result_map[case["case_id"]]["complete"]
             or any(record.get("usage") is None for record in result_map[case["case_id"]]["records"]))
    )
    return {
        "schema_version": 1,
        "kind": "synthetic_llm_summary",
        "purpose": purpose,
        "pack_digest": pack_digest,
        "case_count": len(cases),
        "expected_call_count": expected_stage_count,
        "complete_case_count": complete_count,
        "missing_case_count": len(cases) - complete_count,
        "expected_stage_count": expected_stage_count,
        "observed_stage_count": observed_stage_count,
        "stage_mismatch_count": mismatched_stages,
        "metrics": counts,
        "usage": usage_totals,
        "usage_known": usage_known,
        "reserved_tokens_remaining": reserved_remaining,
        "provider_model": MODEL,
        "model_weights_revision_verified": False,
        "configured_local_cost_zero_is_not_billing_measurement": True,
        "authority_connected": False,
        "resource_ledger_connected": False,
        "ci_eligible": False,
        "calibration_passed": (
            purpose == "calibration"
            and complete_count == len(cases)
            and mismatched_stages == 0
        ) if purpose == "calibration" else None,
        "calibration_passed_semantics": "legacy_target_agreement",
        "target_agreement_passed": complete_count == len(cases) and mismatched_stages == 0,
        "provider_role": "target",
        "status": "COMPLETE" if complete_count == len(cases) else "INCOMPLETE",
    }


def _new_output_dir(path: str | Path) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = (Path.cwd() / candidate).resolve()
    if candidate.exists():
        raise ContractError("OUTPUT_EXISTS")
    candidate.mkdir(parents=True)
    return candidate


def run_verification(purpose: str, output_dir: str | Path) -> dict[str, Any]:
    """指定用途を最大4並列で監督し、非raw出力を段階的に保存する。"""

    if type(purpose) is not str or purpose not in {"calibration", "acceptance"}:
        raise ContractError("INVALID_PURPOSE")
    pack = _load_pack()
    cases = list(pack["case_sets"][purpose]["cases"])
    if len(cases) > MAX_CASES:
        raise ContractError("CASE_LIMIT")
    expected_calls = sum(len(case["session_steps"]) for case in cases)
    outdir = _new_output_dir(output_dir)
    pack_digest = hashlib.sha256(_canonical(pack)).hexdigest()
    # targetの正誤に依存しない既知応答で、測定側を送信前に校正する。
    calibration = calibrate_measurement(pack)
    _write_json(outdir / "evaluator-calibration.json", calibration)
    if (calibration["evaluator_calibration_passed"] is not True
            or calibration["pack_digest"] != pack_digest):
        summary = {"schema_version": 1, "kind": "synthetic_llm_summary",
            "purpose": purpose, "pack_digest": pack_digest, "provider_role": "target",
            "status": "HOLD", "reason_code": "EVALUATOR_CALIBRATION_FAILED",
            "evaluator_calibration_passed": False, "target_agreement_passed": None,
            "model_calls": 0, "authority_connected": False,
            "resource_ledger_connected": False, "ci_eligible": False}
        _write_json(outdir / "summary.json", summary)
        return summary
    planned_reserved_tokens = expected_calls * RESERVED_TOKENS_PER_STAGE
    manifest = {
        "schema_version": 1,
        "kind": "synthetic_llm_run_manifest",
        "purpose": purpose,
        "pack_digest": pack_digest,
        "endpoint": ENDPOINT,
        "provider_model": MODEL,
        "provider_role": "target",
        "evaluator_profile_ref": calibration["evaluator_profile_ref"],
        "evaluator_calibration_ref": hashlib.sha256(_canonical(calibration)).hexdigest(),
        "case_timeout_seconds": CASE_TIMEOUT_SECONDS,
        "run_timeout_seconds": RUN_TIMEOUT_SECONDS,
        "max_workers": 4,
        "max_cases": MAX_CASES,
        "max_calls": MAX_CALLS,
        "max_tokens": MAX_TOKENS,
        "reserved_tokens_per_stage": RESERVED_TOKENS_PER_STAGE,
        "planned_reserved_tokens": planned_reserved_tokens,
        "ci_eligible": False,
    }
    # manifestは開始時に固定し、recordsはcase完了ごとに追記保存する。
    _write_json(outdir / "manifest.json", manifest)
    _write_json(outdir / "records.json", [])
    (outdir / "case-records").mkdir()
    if len(cases) > MAX_CASES or expected_calls > MAX_CALLS:
        summary = {
            "schema_version": 1,
            "kind": "synthetic_llm_summary",
            "purpose": purpose,
            "pack_digest": pack_digest,
            "case_count": len(cases),
            "expected_call_count": expected_calls,
            "reserved_tokens": planned_reserved_tokens,
            "status": "HOLD",
            "reason_code": "PREFLIGHT_LIMIT",
            "authority_connected": False,
            "resource_ledger_connected": False,
            "ci_eligible": False,
        }
        _write_json(outdir / "summary.json", summary)
        return summary
    started = time.time()
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)
    active: dict[concurrent.futures.Future, tuple[int, int]] = {}
    results: list[dict[str, Any] | None] = [None] * len(cases)
    next_index = 0
    settled_tokens = 0
    active_reserved = 0
    stop_submitting = False
    stop_reason: str | None = None
    deadline = time.monotonic() + RUN_TIMEOUT_SECONDS

    def usage_for(result: dict[str, Any]) -> int | None:
        if not result.get("complete"):
            return None
        total = 0
        for record in result.get("records", []):
            usage = record.get("usage")
            if type(usage) is not dict or type(usage.get("total_tokens")) is not int:
                return None
            total += usage["total_tokens"]
        return total

    def save_records() -> None:
        _write_json(outdir / "records.json", [item for item in results if item is not None])

    try:
        while active or (next_index < len(cases) and not stop_submitting):
            while next_index < len(cases) and not stop_submitting and len(active) < 4:
                if time.monotonic() >= deadline:
                    stop_submitting = True
                    stop_reason = "RUN_TIMEOUT"
                    break
                case = cases[next_index]
                reservation = RESERVED_TOKENS_PER_STAGE * len(case["session_steps"])
                if reservation > MAX_TOKENS or settled_tokens + active_reserved + reservation > MAX_TOKENS:
                    stop_submitting = True
                    stop_reason = "PREFLIGHT_BUDGET"
                    break
                future = executor.submit(_case_result, purpose, case, pack)
                active[future] = (next_index, reservation)
                active_reserved += reservation
                next_index += 1
            if not active:
                break
            remaining = max(0.0, deadline - time.monotonic())
            if remaining <= 0:
                done: set[concurrent.futures.Future] = set()
            else:
                done, _ = concurrent.futures.wait(
                    tuple(active), timeout=remaining,
                    return_when=concurrent.futures.FIRST_COMPLETED,
                )
            if not done:
                stop_submitting = True
                stop_reason = "RUN_TIMEOUT"
                for future, (index, _reservation) in list(active.items()):
                    future.cancel()
                    results[index] = {
                        **_expected_case_record(purpose, cases[index], pack_digest),
                        "status": "TIMEOUT",
                        "reason_code": "RUN_TIMEOUT",
                        "remote_stop_unknown": future.running(),
                    }
                    _write_case_record(outdir, index, results[index])
                    del active[future]
                save_records()
                break
            for future in done:
                index, reservation = active.pop(future)
                active_reserved -= reservation
                try:
                    result = future.result()
                except Exception:
                    result = _expected_case_record(purpose, cases[index], pack_digest)
                results[index] = result
                _write_case_record(outdir, index, result)
                known_usage = usage_for(result)
                if known_usage is None or result.get("remote_stop_unknown") or result.get("status") != "COMPLETE":
                    stop_submitting = True
                    stop_reason = result.get("reason_code") or "USAGE_UNKNOWN"
                else:
                    settled_tokens += known_usage
                save_records()
    finally:
        stop_submitting = True
        for future in active:
            future.cancel()
        # 保存失敗でも既に起動したcaseはhard timeoutまで回収を待ち、
        # 未送信caseを新たに投入しない。
        executor.shutdown(wait=bool(active), cancel_futures=True)
    final_results = [result for result in results if result is not None]
    submitted_case_ids = {cases[index]["case_id"] for index, result in enumerate(results) if result is not None}
    summary = _aggregate(
        purpose, cases, final_results, pack_digest,
        reserved_case_ids=submitted_case_ids,
    )
    summary["started_at"] = int(started)
    summary["finished_at"] = int(time.time())
    summary["run_timeout_seconds"] = RUN_TIMEOUT_SECONDS
    summary["evaluator_calibration_passed"] = True
    summary["evaluator_profile_ref"] = calibration["evaluator_profile_ref"]
    _write_json(outdir / "records.json", final_results)
    _write_json(outdir / "summary.json", summary)
    if stop_reason is not None:
        summary["stop_reason"] = stop_reason
        _write_json(outdir / "summary.json", summary)
    return summary


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _write_case_record(outdir: Path, index: int, value: dict[str, Any]) -> None:
    case_id = value.get("case_id") if type(value.get("case_id")) is str else str(index)
    suffix = hashlib.sha256(case_id.encode("utf-8")).hexdigest()[:16]
    path = outdir / "case-records" / f"case-{index:05d}-{suffix}.json"
    if path.exists():
        raise ContractError("CASE_RECORD_EXISTS")
    _write_json(path, value)


def main(argv: list[str] | None = None) -> int:
    """固定packのcalibrationまたはacceptance診断を実行する。"""

    parser = argparse.ArgumentParser()
    parser.add_argument("--purpose", choices=("calibration", "acceptance"), required=True)
    parser.add_argument("--outdir", required=True)
    args = parser.parse_args(argv)
    try:
        result = run_verification(args.purpose, args.outdir)
    except ContractError:
        return 2
    return 0 if result.get("status") == "COMPLETE" else 1


__all__ = ["main", "run_child_case", "run_verification"]


if __name__ == "__main__":
    raise SystemExit(main())
