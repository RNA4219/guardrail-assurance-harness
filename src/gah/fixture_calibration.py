"""自作固定workerと正規化器の測定校正。外部対象やprobeを実行しない。"""
from pathlib import Path
import hashlib
import importlib.util
import json
import re

from . import normalized
from .contracts import ContractError
from .wire import canonical_bytes

ROOT = Path(__file__).resolve().parents[2]
WORKER = ROOT / "fixtures/runtime/fixture_worker.py"
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_LOCK_FIELDS = {
    "schema_version", "image_id", "base_ref", "worker_digest", "docker_binary_digest",
    "entrypoint", "environment", "platform",
}


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _reject_number(_):
    raise ValueError("number is not allowed")


def _digest(value):
    return type(value) is str and _DIGEST.fullmatch(value) is not None


def _load_lock(raw):
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs,
                           parse_float=_reject_number, parse_constant=_reject_number)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
        raise ContractError("FIXTURE_RUNTIME_MISMATCH") from None
    if type(value) is not dict or set(value) != _LOCK_FIELDS:
        raise ContractError("FIXTURE_RUNTIME_MISMATCH")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ContractError("FIXTURE_RUNTIME_MISMATCH")
    for field in ("image_id", "base_ref"):
        if type(value[field]) is not str or not value[field]:
            raise ContractError("FIXTURE_RUNTIME_MISMATCH")
    for field in ("worker_digest", "docker_binary_digest"):
        if not _digest(value[field]):
            raise ContractError("FIXTURE_RUNTIME_MISMATCH")
    for field in ("entrypoint", "environment"):
        if (type(value[field]) is not list or not value[field]
                or any(type(item) is not str or not item for item in value[field])):
            raise ContractError("FIXTURE_RUNTIME_MISMATCH")
    if value["platform"] != "linux/amd64":
        raise ContractError("FIXTURE_RUNTIME_MISMATCH")
    return value


def _worker():
    try:
        source = WORKER.read_bytes()
        lock = _load_lock((ROOT / "config/fixture-runtime.lock.json").read_bytes())
        matches = hashlib.sha256(source).hexdigest() == lock["worker_digest"]
    except (OSError, KeyError, TypeError, ValueError):
        raise ContractError("FIXTURE_RUNTIME_MISMATCH") from None
    if not matches:
        raise ContractError("FIXTURE_RUNTIME_MISMATCH")
    spec = importlib.util.spec_from_file_location("gah_fixed_calibration_worker", WORKER)
    if spec is None or spec.loader is None:
        raise ContractError("FIXTURE_RUNTIME_MISMATCH")
    module = importlib.util.module_from_spec(spec)
    # 検証したbytesを実行し、loaderによるファイルの再読込を避ける。
    exec(compile(source, str(WORKER), "exec"), module.__dict__)
    return module, lock["worker_digest"]


def calibrate_fixed_fixture():
    """固定30対照と異常入力で測定側を検査する。対象runの成績には数えない。"""
    worker, worker_digest = _worker()
    profile = {"worker_digest": worker_digest,
        "normalizer_digest": hashlib.sha256(Path(normalized.__file__).read_bytes()).hexdigest(),
        "calibrator_digest": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    binding = {field: "calibration-" + field for field in (
        "run_id", "operation_id", "obligation_id", "case_id", "trial_id", "stage_id")}
    normalizer_digest = hashlib.sha256(Path(normalized.__file__).read_bytes()).hexdigest()
    binding.update({field: "a" * 64 for field in (
        "contract_digest", "target_digest", "policy_digest", "evaluator_digest",
        "isolation_digest")})
    binding["fixture_digest"] = worker_digest
    binding["adapter_digest"] = normalizer_digest
    binding["owner_epoch"] = 1
    checks = []

    def normalize(mode, observations, *, execution_status="COMPLETED"):
        raw = canonical_bytes({"schema_version": 1, "kind": "gah_generic_result",
            "binding": binding, "mode": mode, "observations": observations})
        return normalized.normalize_generic(raw, binding, execution_status=execution_status,
            exit_code=0 if execution_status == "COMPLETED" else 1, stop_confirmed=True)

    def expected(mode, *, observation=None, mutation_outcome=None, detection=None,
                 deviation=None, error_class=None, raw_digest=None):
        return {"schema_version": 1, "kind": "normalized_result", "binding": dict(binding),
            "mode": mode, "observation": observation, "mutation_outcome": mutation_outcome,
            "detection": detection, "deviation": deviation, "error_class": error_class,
            "raw_digest": raw_digest}

    def check_result(case, raw, actual, wanted):
        checks.append({"case": case, "passed": actual == wanted})

    def check_error(case, operation, expected_code):
        try:
            operation()
        except ContractError as error:
            checks.append({"case": case, "passed": error.code == expected_code})
        except Exception:
            checks.append({"case": case, "passed": False})
        else:
            checks.append({"case": case, "passed": False})

    for number in range(1, 11):
        for state in ("good", "bad"):
            observations = worker._constraint_observation(f"C{number:02d}", state)
            raw = canonical_bytes({"schema_version": 1, "kind": "gah_generic_result",
                "binding": binding, "mode": "constraint", "observations": observations})
            try:
                result = normalize("constraint", observations)
            except Exception:
                checks.append({"case": f"constraint:C{number:02d}:{state}", "passed": False})
            else:
                check_result(f"constraint:C{number:02d}:{state}", raw, result, expected(
                    "constraint", observation="PASS" if state == "good" else "FAIL",
                    raw_digest=hashlib.sha256(raw).hexdigest()))
    for number in range(1, 6):
        for state in ("healthy", "decayed"):
            observations = worker._mutation_observation(f"F{number:02d}", state)
            raw = canonical_bytes({"schema_version": 1, "kind": "gah_generic_result",
                "binding": binding, "mode": "mutation", "observations": observations})
            try:
                result = normalize("mutation", observations)
            except Exception:
                checks.append({"case": f"mutation:F{number:02d}:{state}", "passed": False})
            else:
                check_result(f"mutation:F{number:02d}:{state}", raw, result, expected(
                    "mutation", observation="PASS",
                    mutation_outcome="KILLED" if state == "healthy" else "SURVIVED",
                    raw_digest=hashlib.sha256(raw).hexdigest()))
    for field, value, outcome in (("baseline", "FAIL", "ERROR"),
                                   ("mutation_applied", False, "ERROR"),
                                   ("reached", False, "NO_COVERAGE")):
        observations = worker._mutation_observation("F01", "healthy")
        observations[field] = value
        if field == "reached":
            observations["detected"] = False
        raw = canonical_bytes({"schema_version": 1, "kind": "gah_generic_result",
            "binding": binding, "mode": "mutation", "observations": observations})
        try:
            result = normalize("mutation", observations)
        except Exception:
            checks.append({"case": "invalid-mutation-" + field, "passed": False})
        else:
            check_result("invalid-mutation-" + field, raw, result, expected(
                "mutation", observation="PASS" if field != "baseline" else "FAIL",
                mutation_outcome=outcome, error_class=(
                    "BASELINE_NOT_PASS" if field == "baseline" else
                    "MUTATION_NOT_APPLIED" if field == "mutation_applied" else None),
                raw_digest=hashlib.sha256(raw).hexdigest()))
    failed = normalize("constraint", {"check": "PASS"}, execution_status="FAILED")
    check_result("execution-failure", b"", failed, expected(
        None, mutation_outcome="ERROR", error_class="EXECUTION_FAILURE"))
    check_error("malformed-output", lambda: normalized.normalize_generic(
        b"{", binding, execution_status="COMPLETED", exit_code=0, stop_confirmed=True),
        "INVALID_JSON")
    contradiction = worker._mutation_observation("F01", "healthy")
    contradiction["reached"] = False
    check_error("contradictory-observation", lambda: normalize("mutation", contradiction),
                "CONTRADICTORY_OBSERVATION")
    return {"schema_version": 1, "kind": "fixture_measurement_calibration", "profile": profile,
        "profile_digest": hashlib.sha256(canonical_bytes(profile)).hexdigest(),
        "checks": checks, "vector_count": len(checks), "passed": all(item["passed"] for item in checks),
        "authority_connected": False, "ci_eligible": False}
