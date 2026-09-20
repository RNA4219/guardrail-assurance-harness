"""初回baseline後の旧条件回帰・新条件候補runを純粋に組み立てる。

このmoduleは保存、認証、採択、Evidence、Docker実行を行わない。返却値は
source packと契約条件から決定的に派生した構造であり、authority接続済みや
CI合格を意味しない。
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
from typing import Any

from . import fixture_materialization
from .baselines import validate_baseline_record
from .contracts import ContractError, require_id, require_object, require_uint
from .fixture_materialization import materialize_fixture_manifest
from .run_contracts import bind_run_manifest, content_ref, validate_evaluation_contract
from .contract_updates import bind_contract_transition



from .cache_inputs import bind_run_manifest
_CORE_FIELDS = ("manifest", "contract", "plan", "policy", "registry", "case_set",
                "selected_controls", "ci_eligible")
_PREPARED_FIELDS = {"pack", "pack_ref", "bound_run", "calibration_case_set",
                    "worker_source_digest", "ci_eligible", "authority_connected"}
_BOUND_FIELDS = {"manifest", "contract", "plan", "policy", "registry", "case_set",
                 "selected_controls", "ci_eligible", "fixture_pack", "calibration_case_set"}
_PACK_FIELDS = {"schema_version", "kind", "pack_id", "run_id", "worker_digest",
                "runtime_lock", "execution_profile", "materials", "calibration_materials"}
_CALIBRATION_FIELDS = {"schema_version", "kind", "case_set_id", "purpose",
                       "required_categories", "cases"}


def _bad(code: str = "INVALID_TRANSITION_MATERIALIZATION") -> ContractError:
    return ContractError(code)


def _id(value: Any) -> None:
    try:
        require_id(value)
    except ContractError:
        raise _bad("INVALID_ID") from None


def _time(value: Any) -> None:
    try:
        require_uint(value)
    except ContractError:
        raise _bad("INVALID_TIME") from None


def _deepcopy_core(value: dict[str, Any]) -> dict[str, Any]:
    return {field: deepcopy(value[field]) for field in _CORE_FIELDS}


def _source(source_prepared: Any, worker_source: bytes, runtime_lock: Any,
            execution_profile: Any, baseline_record: Any,
            previous_contract: Any, next_contract: Any, now: int) -> tuple[dict, dict, dict, dict]:
    try:
        require_object(source_prepared, _PREPARED_FIELDS)
    except ContractError:
        raise _bad("SOURCE_PREPARED_INVALID") from None
    source_bound = source_prepared["bound_run"]
    pack = source_prepared["pack"]
    calibration_case_set = source_prepared["calibration_case_set"]
    try:
        require_object(source_bound, _BOUND_FIELDS)
        require_object(pack, _PACK_FIELDS)
        require_object(calibration_case_set, _CALIBRATION_FIELDS)
    except ContractError:
        raise _bad("SOURCE_PREPARED_INVALID")
    if type(worker_source) is not bytes:
        raise _bad("INVALID_WORKER_SOURCE")
    if source_prepared["worker_source_digest"] != hashlib.sha256(worker_source).hexdigest():
        raise _bad("SOURCE_DIGEST_MISMATCH")
    if source_prepared["ci_eligible"] is not False or source_prepared["authority_connected"] is not False:
        raise _bad("SOURCE_FLAGS_INVALID")
    if (source_bound["fixture_pack"] != pack
            or source_bound["calibration_case_set"] != calibration_case_set
            or source_bound["ci_eligible"] is not False):
        raise _bad("SOURCE_PACK_MISMATCH")
    try:
        source_core = fixture_materialization._core_bound(source_bound)
    except ContractError:
        raise _bad("SOURCE_BINDING_INVALID") from None
    if source_prepared["pack_ref"] != content_ref("fixture_pack", pack["pack_id"], pack):
        raise _bad("SOURCE_PACK_MISMATCH")
    if now < source_core["manifest"]["created_at"]:
        raise _bad("INVALID_TIME")
    try:
        source_mat = materialize_fixture_manifest(
            bound_run=source_bound, worker_source=worker_source,
            runtime_lock=runtime_lock, execution_profile=execution_profile, now=now)
    except (ContractError, KeyError, TypeError, ValueError, RecursionError):
        raise _bad("SOURCE_MATERIALIZATION_INVALID") from None
    if source_mat.get("pack_ref") != content_ref("fixture_pack", pack["pack_id"], pack):
        raise _bad("SOURCE_PACK_MISMATCH")
    try:
        previous = validate_evaluation_contract(previous_contract)
        following = validate_evaluation_contract(next_contract)
        baseline = validate_baseline_record(baseline_record)
        transition = bind_contract_transition(
            previous, following, baseline_record=baseline,
            baseline_source_bound=source_core,
        )
    except ContractError:
        raise
    except (KeyError, TypeError, ValueError, RecursionError):
        raise _bad("SOURCE_BINDING_INVALID") from None
    if source_core["contract"] != previous:
        raise _bad("SOURCE_CONTRACT_MISMATCH")
    if baseline["source_run_ref"] != content_ref(
            "run_manifest", source_core["manifest"]["run_id"], source_core["manifest"]):
        raise _bad("BASELINE_SOURCE_MISMATCH")
    return source_core, pack, source_mat, transition


def _actor_context(run_id: str) -> dict[str, str]:
    return content_ref("actor_context", "fixture-operator-" + run_id,
                       {"role": "operator", "purpose": "transition-materialization"})


def _plan(source_plan: dict[str, Any], contract: dict[str, Any], run_id: str,
          *, include_baseline: bool) -> dict[str, Any]:
    plan_id = "transition-plan-" + run_id
    candidate_entries = deepcopy(source_plan["entries"])
    if include_baseline:
        baseline_entries = []
        for entry in candidate_entries:
            item = deepcopy(entry)
            item["variant"] = "baseline"
            baseline_entries.append(item)
        entries = baseline_entries + candidate_entries
    else:
        entries = candidate_entries
    plan = {"schema_version": 1, "kind": "trial_plan", "plan_id": plan_id,
            "contract_ref": content_ref("evaluation_contract", contract["contract_id"], contract),
            "entries": entries}
    return plan


def _manifest(source: dict[str, Any], contract: dict[str, Any], plan: dict[str, Any],
              run_id: str, now: int, *, purpose: str, baseline_ref: Any) -> dict[str, Any]:
    policy = source["policy"]
    manifest = {"schema_version": 1, "kind": "run_manifest", "run_id": run_id,
                "contract_ref": content_ref("evaluation_contract", contract["contract_id"], contract),
                "purpose": purpose, "use_cases": deepcopy(contract["use_cases"]),
                "target_refs": deepcopy(source["manifest"]["target_refs"]),
                "control_ids": deepcopy(source["manifest"]["control_ids"]),
                "baseline_ref": deepcopy(baseline_ref),
                "plan_ref": content_ref("trial_plan", plan["plan_id"], plan),
                "policy_ref": deepcopy(contract["policy_ref"]), "profile": "full",
                "environment_ref": deepcopy(source["manifest"]["environment_ref"]),
                "actor_context_ref": _actor_context(run_id), "created_at": now,
                "deadline": now + policy["profiles"]["full"]["elapsed_seconds"]}
    return manifest


def _bind(source: dict[str, Any], contract: dict[str, Any], plan: dict[str, Any],
          run_id: str, now: int, *, purpose: str, baseline_ref: Any,
          baseline_context: Any) -> dict[str, Any]:
    manifest = _manifest(source, contract, plan, run_id, now,
                         purpose=purpose, baseline_ref=baseline_ref)
    try:
        bound = bind_run_manifest(manifest, contract, plan, source["policy"],
                                  source["registry"], source["case_set"],
                                  baseline_context=baseline_context)
    except ContractError:
        raise
    except (KeyError, TypeError, ValueError, RecursionError):
        raise _bad("BINDING_INVALID") from None
    return bound


def _materialize(source_mat: dict[str, Any], source_pack_ref: dict[str, str], run_id: str, now: int,
                 *, record_baseline: bool) -> dict[str, Any]:
    """検証済みsource materializationからrun固有のwrapperを派生する。"""
    if source_mat.get("manifest", {}).get("run_id") is None:
        raise _bad("MATERIALIZATION_INVALID")
    manifest = deepcopy(source_mat["manifest"])
    manifest["run_id"] = run_id
    manifest["materialization_id"] = "materialization-" + run_id
    manifest["created_at"] = now
    if record_baseline:
        candidate_records = manifest["records"]
        baseline_records = []
        for record in candidate_records:
            item = deepcopy(record)
            item["variant"] = "baseline"
            baseline_records.append(item)
        manifest["records"] = baseline_records + candidate_records
    return {"manifest": manifest,
            "manifest_ref": content_ref("fixture_manifest", manifest["materialization_id"], manifest),
            "pack_ref": deepcopy(source_pack_ref), "structurally_bound": True,
            "authority_connected": False, "ci_eligible": False}


def build_transition_runs(previous_contract: Any, next_contract: Any, *,
                          baseline_record: Any, source_prepared: Any,
                          worker_source: bytes, runtime_lock: Any,
                          execution_profile: Any, now: int,
                          old_run_id: str, new_run_id: str) -> dict[str, Any]:
    """旧条件15件と新条件baseline/candidate各15件を決定的に作る。"""
    try:
        _id(old_run_id); _id(new_run_id); _time(now)
        if old_run_id == new_run_id:
            raise _bad("RUN_ID_REUSED")
        if type(worker_source) is not bytes:
            raise _bad("INVALID_WORKER_SOURCE")
        previous = validate_evaluation_contract(previous_contract)
        following = validate_evaluation_contract(next_contract)
        source_core, source_pack, source_mat, transition = _source(
            source_prepared, worker_source, runtime_lock, execution_profile,
            baseline_record, previous, following, now)
        source_run_id = source_core["manifest"]["run_id"]
        if old_run_id == source_run_id or new_run_id == source_run_id:
            raise _bad("RUN_ID_REUSED")
        baseline = validate_baseline_record(baseline_record)
        baseline_ref = content_ref("baseline", baseline["baseline_id"], baseline)
        old_plan = _plan(source_core["plan"], previous, old_run_id, include_baseline=False)
        new_plan = _plan(source_core["plan"], following, new_run_id, include_baseline=True)
        old_bound = _bind(source_core, previous, old_plan, old_run_id, now,
                           purpose="contract_old_regression", baseline_ref=None,
                           baseline_context=None)
        targets_by_control = {control["control_id"]: control["target_ref"]
                              for control in source_core["registry"]["controls"]}
        context = {"baseline_ref": deepcopy(baseline_ref),
                   "targets": [{"control_id": control_id,
                                "target_ref": deepcopy(targets_by_control[control_id])}
                               for control_id in source_core["manifest"]["control_ids"]]}
        new_bound = _bind(source_core, following, new_plan, new_run_id, now,
                           purpose="contract_candidate", baseline_ref=baseline_ref,
                           baseline_context=context)
        old_bound["ci_eligible"] = False
        new_bound["ci_eligible"] = False
        # source packのpayload・worker・runtime参照は変更せず、各runの束縛だけを生成する。
        source_pack_ref = deepcopy(source_prepared["pack_ref"])
        old_materialization = _materialize(
            source_mat, source_pack_ref, old_run_id, now, record_baseline=False)
        new_materialization = _materialize(
            source_mat, source_pack_ref, new_run_id, now, record_baseline=True)
        return {"transition": deepcopy(transition),
                "old": {"bound_run": _deepcopy_core(old_bound),
                        "baseline_context": None,
                        "materialization": old_materialization},
                "new": {"bound_run": _deepcopy_core(new_bound),
                        "baseline_context": context,
                        "materialization": new_materialization},
                "structurally_bound": True, "authority_connected": False,
                "ci_eligible": False}
    except ContractError:
        raise
    except (KeyError, TypeError, ValueError, RecursionError, OverflowError):
        raise _bad() from None


__all__ = ["build_transition_runs"]
