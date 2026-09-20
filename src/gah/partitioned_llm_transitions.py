"""Pure transition builders for fixed partitioned query-scale LLM inputs.

These functions bind immutable artifacts only. They do not consult authority,
adoption, currentness, or CI state; callers must do so on every operation.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any

from .contracts import ContractError, MAX_INTEGER, require_id, require_uint
from .run_contracts import validate_evaluation_contract
from .contract_updates import bind_contract_transition
from .execution_profiles import check_plan
from .partitioned_llm_materialization import _CONTROL_ID, _VERSIONS
from .partitioned_run_contracts import materialize_partitioned_run, validate_partitioned_runtime
from .partitioned_trial_plan import partition_trial_plan
from .run_contracts import content_ref
from .transition_materialization import _plan
from .cache_inputs import encode_result, plain
from .immutable_cache import binding_cache

_MAX_CACHE_INPUT_BYTES = 16 * 1024 * 1024


def _invalid(code: str = "PARTITIONED_LLM_TRANSITION_INVALID") -> ContractError:
    return ContractError(code)


def _source_digest() -> str:
    from .evaluation_authority import _source_digest as source_digest
    return source_digest()


def _build_identities():
    return (_build, validate_partitioned_runtime, _binder_baseline, bind_contract_transition,
            check_plan, materialize_partitioned_run, partition_trial_plan, content_ref,
            validate_evaluation_contract, _targets, _candidate_registry, _plan_for,
            _execution_profile, _manifest, _materialize, _plan)


@binding_cache.memoize
def _cached_build(source_digest, implementation, validate_runtime, binder_baseline,
                  bind_transition, plan_checker, materializer, partitioner, make_ref,
                  validate_contract, targets, candidate_registry, plan_builder,
                  profile_builder, manifest_builder, materialize_builder, base_plan, payload):
    previous, following, baseline_record, source_prepared, now, old_run_id, new_run_id, following_registry = json.loads(payload)
    return encode_result(implementation(previous, following, baseline_record=baseline_record,
        source_prepared=source_prepared, now=now, old_run_id=old_run_id,
        new_run_id=new_run_id, following_registry=following_registry))


def _rebind_identities():
    return (_rebind, validate_partitioned_runtime, _binder_baseline, check_plan,
            materialize_partitioned_run, partition_trial_plan, content_ref, validate_evaluation_contract,
            _targets, _plan_for, _execution_profile, _manifest, _materialize, _plan)


@binding_cache.memoize
def _cached_rebind(source_digest, implementation, validate_runtime, binder_baseline,
                   plan_checker, materializer, partitioner, make_ref, validate_contract,
                   targets, plan_builder, profile_builder, manifest_builder,
                   materialize_builder, base_plan, payload):
    prepared, run_id, now, purpose = json.loads(payload)
    return encode_result(implementation(prepared, run_id=run_id, now=now, purpose=purpose))


def _cached_json_result(cache, identities, values):
    if not plain(values):
        return None
    try:
        payload = encode_result(values)
        if len(payload.encode("utf-8")) > _MAX_CACHE_INPUT_BYTES:
            return None
        source_digest = _source_digest()
    except (ContractError, TypeError, ValueError, UnicodeError, RecursionError, OverflowError, OSError):
        return None
    try:
        return json.loads(cache(source_digest, *identities, payload))
    except ContractError:
        raise
    except (TypeError, ValueError, UnicodeError, RecursionError, OverflowError):
        return None


def _binder_baseline(value: Any):
    if value is None:
        return None
    if (type(value) is not dict or not {"baseline_ref", "targets"}.issubset(value)
            or set(value) - {"baseline_ref", "targets", "contract"}):
        raise _invalid("BASELINE_CONTEXT_INVALID")
    return {"baseline_ref": deepcopy(value["baseline_ref"]),
            "targets": deepcopy(value["targets"])}


def _targets(registry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result = {}
    for control in registry["controls"]:
        result[control["control_id"]] = control["target_ref"]
    if set(result) != {_CONTROL_ID}:
        raise _invalid("FIXED_CONTROL_MISMATCH")
    return result


def _candidate_registry(source: dict[str, Any], transition: dict[str, Any], supplied: Any):
    expected = transition.get("following_registry")
    if expected is None:
        if supplied is not None and supplied != source["registry"]:
            raise _invalid("FOLLOWING_REGISTRY_MISMATCH")
        return deepcopy(source["registry"])
    if supplied != expected:
        raise _invalid("FOLLOWING_REGISTRY_MISMATCH")
    return deepcopy(expected)


def _plan_for(source: dict[str, Any], contract: dict[str, Any], run_id: str,
              *, include_baseline: bool, baseline_target: Any = None,
              candidate_targets: dict[str, Any] | None = None):
    plan = _plan(source["plan"], contract, run_id, include_baseline=include_baseline)
    plan["plan_id"] = "qscale-plan-" + str(contract["generation"]) + "-" + hashlib.sha256(
        run_id.encode("utf-8")).hexdigest()[:24]
    control_targets = candidate_targets or _targets(source["registry"])
    obligation_targets = {}
    for control in source["registry"]["controls"]:
        for obligation in control["obligations"]:
            obligation_targets[obligation["obligation_id"]] = control_targets[control["control_id"]]
    for entry in plan["entries"]:
        if entry["variant"] == "baseline":
            if baseline_target is None:
                raise _invalid("BASELINE_TARGET_MISSING")
            entry["target_ref"] = deepcopy(baseline_target)
        else:
            entry["target_ref"] = deepcopy(obligation_targets[entry["obligation_id"]])
    return plan


def _execution_profile(source_prepared: dict[str, Any], manifest: dict[str, Any], plan: dict[str, Any]):
    bindings = source_prepared["execution_profile"]["bindings"]
    workers = {item["fixture_digest"] for item in bindings}
    if len(workers) != 1:
        raise _invalid("WORKER_BINDING_MISMATCH")
    worker_digest = next(iter(workers))
    evaluator = source_prepared["evaluator_document"]
    adapter = evaluator["source_sha256"]["normalized.py"]
    pairs = sorted({(entry["target_ref"]["digest"], entry["evaluator_ref"]["digest"])
                    for entry in plan["entries"]})
    profile = {
        "schema_version": 2,
        "kind": "execution_profile",
        "isolation_digest": manifest["environment_ref"]["digest"],
        "bindings": [{"target_digest": target, "evaluator_digest": evaluator_digest,
                      "fixture_digest": worker_digest, "adapter_digests": [adapter]}
                     for target, evaluator_digest in pairs],
    }
    check_plan(profile, {"manifest": manifest, "plan": plan})
    return profile


def _manifest(source: dict[str, Any], contract: dict[str, Any], plan_index: dict[str, Any],
              run_id: str, now: int, purpose: str, baseline_ref: Any,
              registry: dict[str, Any]):
    elapsed = source["manifest"]["deadline"] - source["manifest"]["created_at"]
    if elapsed <= 0 or now > MAX_INTEGER - elapsed:
        raise _invalid("TIME_RANGE")
    target_map = _targets(registry)
    targets = list(target_map.values())
    return {
        "schema_version": 2,
        "kind": "run_manifest",
        "run_id": run_id,
        "contract_ref": content_ref("evaluation_contract", contract["contract_id"], contract),
        "purpose": purpose,
        "use_cases": deepcopy(contract["use_cases"]),
        "target_refs": targets,
        "control_ids": [_CONTROL_ID],
        "baseline_ref": deepcopy(baseline_ref),
        "plan_ref": content_ref("trial_plan_index", plan_index["plan_id"], plan_index),
        "policy_ref": deepcopy(source["manifest"]["policy_ref"]),
        "profile": source["manifest"]["profile"],
        "environment_ref": deepcopy(source["manifest"]["environment_ref"]),
        "actor_context_ref": content_ref("actor_context", "qsa-" + hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:24],
                                          {"run_id": run_id, "role": "operator", "request_fixed": True}),
        "created_at": now,
        "deadline": now + elapsed,
    }


def _materialize(source_prepared: dict[str, Any], contract: dict[str, Any], registry: dict[str, Any],
                 run_id: str, now: int, purpose: str, baseline_ref: Any,
                 baseline_context: Any, *, include_baseline: bool, baseline_target: Any = None):
    source = source_prepared["bound_run"]
    plan = _plan_for(source, contract, run_id, include_baseline=include_baseline,
                     baseline_target=baseline_target, candidate_targets=_targets(registry))
    index, segments = partition_trial_plan(plan)
    manifest = _manifest(source, contract, index, run_id, now, purpose, baseline_ref, registry)
    bound = materialize_partitioned_run(manifest, contract, index, segments,
        source["policy"], registry, source["case_set"], baseline_context=baseline_context)
    profile = _execution_profile(source_prepared, manifest, plan)
    full = {key: deepcopy(value) for key, value in source_prepared.items()
            if key not in {"bound_run", "ci_eligible", "baseline_context", "baseline_contract", "materialization",
                           "manifest", "contract", "registry", "plan_index", "plan_segments",
                           "execution_profile"}}
    full.update({"manifest": manifest, "contract": deepcopy(contract), "registry": deepcopy(registry),
                 "plan_index": index, "plan_segments": segments, "execution_profile": profile,
                 "bound_run": bound, "baseline_context": deepcopy(baseline_context),
                 "ci_eligible": False})
    full["baseline_contract"] = deepcopy(source_prepared.get("contract") if baseline_context is not None else None)
    refs = _targets(registry)
    full["target_documents"] = {
        version: deepcopy(source_prepared["target_documents"][version])
        for version in sorted(_VERSIONS)
    }
    for control_id, target_ref in refs.items():
        if not any(content_ref("target", doc["target_id"], doc) == target_ref
                   for doc in full["target_documents"].values()):
            raise _invalid("TARGET_NOT_FIXED")
    full["materialization"] = {
        "schema_version": 1,
        "kind": "partitioned_query_scale_materialization",
        "run_id": run_id,
        "corpus_family": source_prepared["materialization"]["corpus_family"],
        "case_count": source_prepared["materialization"]["case_count"],
        "planned_trials": len(plan["entries"]),
        "planned_stages": sum(len(entry["stage_ids"]) for entry in plan["entries"]),
        "generation": contract["generation"],
        "manifest_ref": content_ref("run_manifest", run_id, manifest),
        "plan_index_ref": content_ref("trial_plan_index", index["plan_id"], index),
        "corpus_index_ref": deepcopy(source_prepared["materialization"]["corpus_index_ref"]),
        "target_ref": deepcopy(next(iter(refs.values()))),
        "evaluator_ref": deepcopy(source["contract"]["evaluator_refs"][0]),
        "target_is_synthetic": True,
        "authority_connected": False,
        "runtime_verified": False,
        "admission_verified": False,
        "semantic_oracle_independence": False,
        "real_workload_performance": False,
        "ci_eligible": False,
    }
    return full


def _build(previous, following, *, baseline_record, source_prepared, now,
           old_run_id, new_run_id, following_registry=None):
    """Create same-condition old/new structural inputs; authority checks remain external."""
    try:
        require_uint(now)
        for value in (old_run_id, new_run_id):
            require_id(value)
            if len(value) > 64:
                raise _invalid("INVALID_ID")
        if type(source_prepared) is not dict or source_prepared.get("ci_eligible") is not False:
            raise _invalid("SOURCE_PREPARED_INVALID")
        source = validate_partitioned_runtime(source_prepared["bound_run"],
                                               _binder_baseline(source_prepared.get("baseline_context")))
        for field in ("manifest", "contract", "policy", "registry", "case_set"):
            if source_prepared.get(field) != source[field]:
                raise _invalid("SOURCE_BINDING_MISMATCH")
        context = source["_partitioned_context"]
        if (source_prepared.get("plan_index") != context["index"]
                or source_prepared.get("plan_segments") != context["segments"]):
            raise _invalid("SOURCE_BINDING_MISMATCH")
        source_prepared = {**source_prepared, "bound_run": source}
        if source["manifest"]["schema_version"] != 2 or source["manifest"]["purpose"] != "baseline_candidate":
            raise _invalid("SOURCE_PURPOSE_INVALID")
        material = source_prepared.get("materialization")
        if (type(material) is not dict or material.get("kind") != "partitioned_query_scale_materialization"
                or material.get("run_id") != source["manifest"]["run_id"]
                or material.get("generation") != 1
                or material.get("manifest_ref") != content_ref("run_manifest", source["manifest"]["run_id"], source["manifest"])
                or material.get("plan_index_ref") != content_ref("trial_plan_index", context["index"]["plan_id"], context["index"])
                or material.get("planned_trials") != len(source["plan"]["entries"])
                or material.get("case_count") != len(source["case_set"]["cases"])
                or material.get("case_count") not in {400, 800, 1600}):
            raise _invalid("SOURCE_MATERIALIZATION_INVALID")
        check_plan(source_prepared["execution_profile"], source)
        evaluator = source_prepared["evaluator_document"]
        evaluator_ref = content_ref("evaluator", evaluator["evaluator_id"], evaluator)
        expected_targets = {version: content_ref("target", doc["target_id"], doc)
                            for version, doc in source_prepared["target_documents"].items()}
        if set(expected_targets) != set(_VERSIONS):
            raise _invalid("TARGET_SET_INVALID")
        if (source["contract"]["evaluator_refs"] != [evaluator_ref]
                or any(obligation["evaluator_ref"] != evaluator_ref
                       for control in source["registry"]["controls"]
                       for obligation in control["obligations"])):
            raise _invalid("EVALUATOR_BINDING_MISMATCH")
        source_targets = _targets(source["registry"])
        if any(ref not in expected_targets.values() for ref in source_targets.values()):
            raise _invalid("TARGET_BINDING_MISMATCH")
        if previous != source["contract"] or previous["generation"] != 1:
            raise _invalid("SOURCE_CONTRACT_MISMATCH")
        transition = bind_contract_transition(previous, following, baseline_record=baseline_record,
            baseline_source_bound=source, following_registry=following_registry)
        if len({old_run_id, new_run_id, source["manifest"]["run_id"]}) != 3:
            raise _invalid("RUN_ID_REUSED")
        if now < source["manifest"]["created_at"]:
            raise _invalid("TIME_INVALID")
        old_targets = _targets(source["registry"])
        record_targets = baseline_record["target_refs"]
        if len(record_targets) != len(old_targets):
            raise _invalid("BASELINE_TARGET_MISMATCH")
        baseline_target = record_targets[0]
        known_target_refs = [content_ref("target", doc["target_id"], doc)
                             for doc in source_prepared["target_documents"].values()]
        if baseline_target not in known_target_refs:
            raise _invalid("BASELINE_TARGET_NOT_FIXED")
        context = {"baseline_ref": deepcopy(transition["baseline_ref"]),
                   "targets": [{"control_id": _CONTROL_ID, "target_ref": deepcopy(baseline_target)}]}
        new_registry = _candidate_registry(source, transition, following_registry)
        old = _materialize(source_prepared, previous, source["registry"], old_run_id, now,
                           "contract_old_regression", previous["comparison"]["baseline_ref"], None,
                           include_baseline=False)
        new = _materialize(source_prepared, following, new_registry, new_run_id, now,
                           "contract_candidate", transition["baseline_ref"], context,
                           include_baseline=True, baseline_target=baseline_target)
        return {"transition": transition, "old": old, "new": new,
                "structurally_bound": True, "authority_connected": False, "ci_eligible": False}
    except ContractError:
        raise
    except (KeyError, TypeError, ValueError, IndexError, RecursionError, OverflowError):
        raise _invalid() from None


def _rebind(prepared, *, run_id, now, purpose="regression"):
    """Reidentify an already validated generation-2+ prepared input for normal regression."""
    try:
        require_id(run_id)
        require_uint(now)
        if len(run_id) > 64 or purpose != "regression":
            raise _invalid("REBIND_ARGUMENT_INVALID")
        if type(prepared) is not dict or prepared.get("ci_eligible") is not False:
            raise _invalid("SOURCE_PREPARED_INVALID")
        source = validate_partitioned_runtime(prepared["bound_run"],
                                               _binder_baseline(prepared.get("baseline_context")))
        for field in ("manifest", "contract", "policy", "registry", "case_set"):
            if prepared.get(field) != source[field]:
                raise _invalid("SOURCE_BINDING_MISMATCH")
        context = source["_partitioned_context"]
        if prepared.get("plan_index") != context["index"] or prepared.get("plan_segments") != context["segments"]:
            raise _invalid("SOURCE_BINDING_MISMATCH")
        prepared = {**prepared, "bound_run": source}
        if source["contract"]["generation"] < 2 or source["manifest"]["baseline_ref"] is None:
            raise _invalid("SOURCE_PURPOSE_INVALID")
        if now < source["manifest"]["created_at"] or run_id == source["manifest"]["run_id"]:
            raise _invalid("RUN_ID_REUSED_OR_TIME_INVALID")
        baseline_value = prepared["baseline_context"]
        if (type(baseline_value) is not dict
                or not {"baseline_ref", "targets"}.issubset(baseline_value)
                or set(baseline_value) - {"baseline_ref", "targets", "contract"}):
            raise _invalid("BASELINE_CONTEXT_INVALID")
        baseline = {"baseline_ref": deepcopy(baseline_value["baseline_ref"]),
                    "targets": deepcopy(baseline_value["targets"])}
        prior_contract = baseline_value.get("contract", prepared.get("baseline_contract"))
        if prior_contract is not None:
            prior_contract = validate_evaluation_contract(prior_contract)
            if (prepared.get("baseline_contract") not in (None, prior_contract)
                    or source["contract"]["generation"] != prior_contract["generation"] + 1):
                raise _invalid("BASELINE_CONTRACT_MISMATCH")
        elif source["contract"]["generation"] != 2:
            raise _invalid("BASELINE_CONTRACT_MISSING")
        registry = source["registry"]
        candidates = [deepcopy(entry) for entry in source["plan"]["entries"]
                      if entry["variant"] == "candidate"]
        if len(candidates) != len(source["case_set"]["cases"]):
            raise _invalid("PLAN_CANDIDATE_COVERAGE_INVALID")
        candidate_bound = deepcopy(source)
        candidate_bound["plan"] = {**deepcopy(source["plan"]), "entries": candidates}
        candidate_prepared = {**prepared, "bound_run": candidate_bound}
        result = _materialize(candidate_prepared, source["contract"], registry, run_id, now, purpose,
                              baseline["baseline_ref"], baseline, include_baseline=True,
                              baseline_target=baseline["targets"][0]["target_ref"])
        result["baseline_contract"] = deepcopy(prepared.get("baseline_contract") or baseline_value.get("contract"))
        return result
    except ContractError:
        raise
    except (KeyError, TypeError, ValueError, IndexError, RecursionError, OverflowError):
        raise _invalid() from None


def build(previous, following, *, baseline_record, source_prepared, now,
          old_run_id, new_run_id, following_registry=None):
    values = [previous, following, baseline_record, source_prepared, now, old_run_id, new_run_id, following_registry]
    cached = _cached_json_result(_cached_build, _build_identities(), values)
    if cached is not None:
        return cached
    return _build(previous, following, baseline_record=baseline_record, source_prepared=source_prepared,
                  now=now, old_run_id=old_run_id, new_run_id=new_run_id,
                  following_registry=following_registry)


def rebind(prepared, *, run_id, now, purpose="regression"):
    values = [prepared, run_id, now, purpose]
    cached = _cached_json_result(_cached_rebind, _rebind_identities(), values)
    if cached is not None:
        return cached
    return _rebind(prepared, run_id=run_id, now=now, purpose=purpose)


__all__ = ["build", "rebind"]
