"""固定UC-CI packと測定校正を採択DBの保存実体へ結ぶ。"""
import json
from pathlib import Path
import hashlib

from . import fixture_calibration, fixture_materialization, resources
from .adoption import AdoptionError
from .cache_inputs import encode_result, plain
from .contracts import ContractError, MAX_INTEGER, decode_document, require_object
from .docker_runner import PROFILE
from .run_contracts import content_ref
from .immutable_cache import binding_cache
from .wire import canonical_bytes

ROOT = Path(__file__).resolve().parents[2]
TABLES = {"fixture_admissions": {"run_id", "contract_digest", "payload_json", "digest", "created_at", "permission_generation"}}


def create_schema(db):
    if not db.in_transaction:
        raise AdoptionError("TRANSACTION_REQUIRED")
    db.execute("CREATE TABLE fixture_admissions(run_id TEXT PRIMARY KEY, contract_digest TEXT NOT NULL UNIQUE, "
        "payload_json TEXT NOT NULL, digest TEXT NOT NULL, created_at INTEGER NOT NULL, permission_generation INTEGER NOT NULL)")


def execution_context():
    worker = (ROOT / "fixtures/runtime/fixture_worker.py").read_bytes()
    lock = decode_document((ROOT / "config/fixture-runtime.lock.json").read_bytes())
    profile = {"fixture_digest": hashlib.sha256(worker).hexdigest(),
        "adapter_digest": hashlib.sha256((ROOT / "src/gah/normalized.py").read_bytes()).hexdigest(),
        "isolation_digest": hashlib.sha256(canonical_bytes(PROFILE)).hexdigest()}
    return worker, lock, profile


_CACHE_PAYLOAD_BYTES = 3 * 1024 * 1024


@binding_cache.memoize
def _cached_fixture_pack(source_digest, implementation, payload):
    values = json.loads(payload)
    (policy, worker_hex, lock, profile, created_at, run_id,
     policy_generation) = values
    worker = bytes.fromhex(worker_hex)
    return encode_result(implementation(
        policy, worker, lock, profile, created_at, run_id,
        policy_generation=policy_generation,
    ))


def _expected_fixture_pack(policy, worker, lock, profile, created_at, run_id,
                           policy_generation):
    implementation = fixture_materialization.build_fixture_pack
    if type(worker) is not bytes:
        return implementation(
            policy, worker, lock, profile, created_at, run_id,
            policy_generation=policy_generation,
        )
    values = [
        policy, worker.hex(), lock, profile, created_at, run_id,
        policy_generation,
    ]
    if not plain(values):
        return implementation(
            policy, worker, lock, profile, created_at, run_id,
            policy_generation=policy_generation,
        )
    try:
        payload = encode_result(values)
        if len(payload.encode("utf-8")) > _CACHE_PAYLOAD_BYTES:
            raise ValueError("CACHE_PAYLOAD_TOO_LARGE")
        from .evaluation_authority import _source_digest
        source_digest = _source_digest()
    except (ImportError, OSError, TypeError, ValueError, UnicodeError,
            RecursionError):
        return implementation(
            policy, worker, lock, profile, created_at, run_id,
            policy_generation=policy_generation,
        )
    return json.loads(_cached_fixture_pack(
        source_digest, implementation, payload,
    ))


def _verify(row, now, db=None):
    try:
        value = resources._unpack(row["payload_json"], row["digest"])
        if value.get("kind") == "partitioned_guardrail_admission":
            from .partitioned_llm_admission import _verify as verify_partitioned
            if db is None:
                raise AdoptionError("RESOLVER_REQUIRED")
            return verify_partitioned(db, row, now)
        if value.get("kind") == "synthetic_guardrail_admission":
            from .llm_admission import _verify_value
            return _verify_value(row, now, value)
        require_object(value, {"prepared", "materialization", "calibration"})
        prepared = value["prepared"]
        bound = prepared["bound_run"]
        if (type(row["created_at"]) is not int or not 0 <= row["created_at"] <= now <= MAX_INTEGER
                or type(row["permission_generation"]) is not int or not 0 <= row["permission_generation"] <= MAX_INTEGER
                or bound["manifest"]["run_id"] != row["run_id"]
                or bound["manifest"]["created_at"] != row["created_at"]
                or bound["manifest"]["contract_ref"]["digest"] != row["contract_digest"]):
            raise ContractError()
        worker, lock, profile = execution_context()
        expected = _expected_fixture_pack(
            bound["policy"], worker, lock, profile, row["created_at"],
            row["run_id"], bound["contract"]["policy_generation"],
        )
        if prepared != expected:
            raise ContractError()
        materialization = fixture_materialization.validate_fixture_manifest(
            value["materialization"]["manifest"], bound_run=prepared["bound_run"], worker_source=worker,
            runtime_lock=lock, execution_profile=profile, now=now)
        if materialization != value["materialization"]:
            raise ContractError()
        current = fixture_calibration.calibrate_fixed_fixture()
        if (value["calibration"] != current or current["passed"] is not True
                or current["profile"]["worker_digest"] != profile["fixture_digest"]
                or current["profile"]["normalizer_digest"] != profile["adapter_digest"]
                or execution_context() != (worker, lock, profile)):
            raise ContractError()
    except (ContractError, resources.ResourceError, KeyError, TypeError, ValueError, OSError):
        raise AdoptionError("FIXTURE_ADMISSION_INVALID") from None
    return value


def prepare(db, policy, policy_generation, run_id, now, permission_generation):
    """固定factoryだけで候補を生成する。実行とbaseline採択はまだ行わない。"""
    if not db.in_transaction:
        raise AdoptionError("TRANSACTION_REQUIRED")
    existing = db.execute("SELECT * FROM fixture_admissions WHERE run_id=?", (run_id,)).fetchone()
    if existing is not None:
        value = _verify(existing, now)
        bound = value["prepared"]["bound_run"]
        if (bound["policy"] != policy or bound["contract"]["policy_generation"] != policy_generation
                or existing["permission_generation"] != permission_generation):
            raise AdoptionError("FIXTURE_ADMISSION_CONFLICT")
        # _verifyは保存JSONを毎回decodeし、新しいobject graphを返す。
        return value
    worker, lock, profile = execution_context()
    prepared = _expected_fixture_pack(
        policy, worker, lock, profile, now, run_id, policy_generation,
    )
    materialization = fixture_materialization.materialize_fixture_manifest(
        bound_run=prepared["bound_run"], worker_source=worker, runtime_lock=lock, execution_profile=profile, now=now)
    calibration = fixture_calibration.calibrate_fixed_fixture()
    if not calibration["passed"]:
        raise AdoptionError("CALIBRATION_UNAVAILABLE")
    value = {"prepared": prepared, "materialization": materialization, "calibration": calibration}
    raw, digest = resources._packed(value)
    db.execute("INSERT INTO fixture_admissions VALUES(?,?,?,?,?,?)",
        (run_id, prepared["bound_run"]["manifest"]["contract_ref"]["digest"], raw, digest, now, permission_generation))
    # 書き込み後の同じ検査を必須化し、生成側と読取側の条件差を残さない。
    return _verify(db.execute("SELECT * FROM fixture_admissions WHERE run_id=?", (run_id,)).fetchone(), now)


def for_contract(db, contract, now, permission_generation):
    digest = content_ref("evaluation_contract", contract["contract_id"], contract)["digest"]
    row = db.execute("SELECT * FROM fixture_admissions WHERE contract_digest=?", (digest,)).fetchone()
    if row is None:
        return None
    value = _verify(row, now, db)
    if row["permission_generation"] != permission_generation:
        raise AdoptionError("CALIBRATION_UNAVAILABLE")
    if value["prepared"]["bound_run"]["contract"] != contract:
        raise AdoptionError("FIXTURE_ADMISSION_INVALID")
    return value


def materialized_run(db, bound, now):
    """参照だけのtransport runはfalse。既存packとの不一致は固定error。"""
    from . import transition_authority, regression_runs
    if bound["manifest"].get("schema_version") == 2:
        from .partitioned_llm_admission import for_run
        value = for_run(db, bound["manifest"]["run_id"], now)
        if bound != value["prepared"]["bound_run"]:
            raise AdoptionError("FIXTURE_RUN_MISMATCH")
        return True
    if bound["manifest"]["purpose"] in transition_authority.PURPOSES:
        return transition_authority.materialized_run(db, bound, now)
    if bound["manifest"]["purpose"] == "regression" and bound["contract"]["generation"] >= 2:
        return regression_runs.materialized_run(db, bound, now)
    row = db.execute("SELECT * FROM fixture_admissions WHERE run_id=?", (bound["manifest"]["run_id"],)).fetchone()
    if row is None:
        return False
    value = _verify(row, now)
    # 比較だけに使い、保存内容も呼出し元のboundも変更しない。
    expected_bound = {key: value["prepared"]["bound_run"][key] for key in (
        "manifest", "contract", "plan", "policy", "registry", "case_set", "selected_controls", "ci_eligible")}
    if bound != expected_bound:
        raise AdoptionError("FIXTURE_RUN_MISMATCH")
    return True
