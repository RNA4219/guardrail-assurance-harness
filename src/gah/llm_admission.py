"""合成guardrailの入力実体と独立測定校正を、認証された採択DBへ結ぶ。"""
from copy import deepcopy
from functools import lru_cache
from .adoption import AdoptionError
from .contracts import ContractError, MAX_INTEGER, decode_document, require_object
from .docker_runner import PROFILE
from . import guardrail_runtime, llm_materialization, measurement_calibration, resources
from .run_contracts import content_ref
from .wire import canonical_bytes

KIND = "synthetic_guardrail_admission"


@lru_cache(maxsize=4)
def _expected(policy_raw, policy_generation, run_id, created_at, version, lock_raw, source_digest):
    # 保存DBの内容をcacheの正解にしない。固定factoryの出力だけを再利用する。
    policy, lock = decode_document(policy_raw), decode_document(lock_raw)
    full = llm_materialization.build(policy, policy_generation=policy_generation,
        run_id=run_id, now=created_at, target_version=version, image_id=lock["image_id"],
        worker_digest=lock["worker_digest"], isolation_profile=PROFILE)
    calibration = _calibration(source_digest)
    if calibration["evaluator_calibration_passed"] is not True:
        raise AdoptionError("CALIBRATION_UNAVAILABLE")
    prepared = llm_materialization.prepared_response(full)
    for key in ("target_document", "evaluator_document", "calibration_case_set"):
        prepared[key] = full[key]
    from .cache_inputs import encode_result
    return encode_result({"kind": KIND, "prepared": prepared, "runtime_lock": lock,
        "materialization": full["materialization"],
        "calibration": {"passed": True, "measurement": calibration,
            "target_agreement_is_admission_condition": False, "ci_eligible": False}})


@lru_cache(maxsize=2)
def _calibration(source_digest):
    from .evaluation_data import build_pack
    return measurement_calibration.calibrate_measurement(build_pack())


def expected(policy, generation, run_id, created_at, version):
    from .evaluation_authority import _source_digest
    lock = guardrail_runtime.read_lock()
    result = _expected(canonical_bytes(policy), generation, run_id, created_at,
        version, canonical_bytes(lock), _source_digest())
    import json
    return json.loads(result)


def _verify_value(row, now, value):
    """保存rowとfreshにdecode済みの値を照合するprivate verifier。"""
    try:
        require_object(value, {"kind", "prepared", "runtime_lock", "materialization", "calibration"})
        bound = value["prepared"]["bound_run"]
        if (value["kind"] != KIND or type(row["created_at"]) is not int
                or not 0 <= row["created_at"] <= now <= MAX_INTEGER
                or type(row["permission_generation"]) is not int or row["permission_generation"] < 0
                or bound["manifest"]["run_id"] != row["run_id"]
                or bound["manifest"]["created_at"] != row["created_at"]
                or bound["manifest"]["contract_ref"]["digest"] != row["contract_digest"]):
            raise ContractError()
        generated = expected(bound["policy"], bound["contract"]["policy_generation"],
            row["run_id"], row["created_at"], value["prepared"]["target_document"]["behavior_version"])
        if value != generated:
            raise ContractError()
        return value
    except (ContractError, resources.ResourceError, KeyError, TypeError, ValueError, OSError):
        raise AdoptionError("LLM_ADMISSION_INVALID") from None


def verify(row, now):
    try:
        value = resources._unpack(row["payload_json"], row["digest"])
    except (ContractError, resources.ResourceError, KeyError, TypeError, ValueError, OSError):
        raise AdoptionError("LLM_ADMISSION_INVALID") from None
    return _verify_value(row, now, value)


def prepare(db, policy, generation, run_id, now, permission_generation, version):
    if not db.in_transaction:
        raise AdoptionError("TRANSACTION_REQUIRED")
    existing = db.execute("SELECT * FROM fixture_admissions WHERE run_id=?", (run_id,)).fetchone()
    if existing is not None:
        value = verify(existing, now)
        bound = value["prepared"]["bound_run"]
        if (bound["policy"] != policy or bound["contract"]["policy_generation"] != generation
                or value["prepared"]["target_document"]["behavior_version"] != version
                or existing["permission_generation"] != permission_generation):
            raise AdoptionError("LLM_ADMISSION_CONFLICT")
        return value
    for table in ("eval_runs", "resource_runs", "transition_runs"):
        if db.execute("SELECT 1 FROM " + table + " WHERE run_id=?", (run_id,)).fetchone():
            raise AdoptionError("RUN_CONFLICT")
    value = expected(policy, generation, run_id, now, version)
    raw, digest = resources._packed(value)
    db.execute("INSERT INTO fixture_admissions VALUES(?,?,?,?,?,?)", (run_id,
        value["prepared"]["bound_run"]["manifest"]["contract_ref"]["digest"], raw, digest, now, permission_generation))
    return verify(db.execute("SELECT * FROM fixture_admissions WHERE run_id=?", (run_id,)).fetchone(), now)


def for_run(db, run_id, now):
    row = db.execute("SELECT * FROM fixture_admissions WHERE run_id=?", (run_id,)).fetchone()
    if row is not None:
        value = resources._unpack(row["payload_json"], row["digest"])
        if value.get("kind") == "partitioned_guardrail_admission":
            from .partitioned_llm_admission import _verify
            return _verify(db, row, now)
        return _verify_value(row, now, value)
    from . import transition_authority, regression_runs
    mapping = db.execute("SELECT * FROM transition_runs WHERE run_id=?", (run_id,)).fetchone()
    if mapping is not None:
        _, candidate = transition_authority.load_candidate(db, mapping["candidate_id"], now)
        prepared = candidate["runs"][mapping["side"]]
    else:
        run = db.execute("SELECT * FROM eval_runs WHERE run_id=?", (run_id,)).fetchone()
        if run is None:
            raise AdoptionError("LLM_ADMISSION_MISSING")
        prepared = regression_runs.for_run(db, run, now)
    if prepared["bound_run"]["manifest"]["use_cases"] != ["UC-LLM"]:
        raise AdoptionError("LLM_ADMISSION_INVALID")
    return {"prepared": prepared}


def execution_profile(db, bound, now):
    value = for_run(db, bound["manifest"]["run_id"], now)
    saved = value["prepared"]["bound_run"]
    if any(bound.get(key) != saved[key] for key in bound) or set(bound) != set(saved):
        raise AdoptionError("LLM_RUN_MISMATCH")
    return deepcopy(value["prepared"]["execution_profile"])


def check_entry(db, run_id, entry, scenario, now):
    value = for_run(db, run_id, now)
    prepared = value["prepared"]
    from .guardrail_results import target_for_entry
    if prepared["bound_run"]["manifest"].get("schema_version") == 2:
        documents = list(prepared["target_documents"].values())
        target = target_for_entry({"target_documents": documents, "target_document": documents[0]}, entry)
    else:
        target = target_for_entry(prepared, entry)
    if (scenario != "guardrail:" + target["behavior_version"]
            or entry not in prepared["bound_run"]["plan"]["entries"]):
        raise AdoptionError("LLM_ENTRY_MISMATCH")
    return value
