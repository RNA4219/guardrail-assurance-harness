"""Fixed query-scale LLM inputs for bounded, reference-only plan materialization.

This pure builder creates deterministic synthetic input artifacts. It does not
admit a run, prove freshness, reserve resources, or establish CI eligibility.
The query-scale single-stage family is distinct from the fixed mixed-stage pack.
"""

from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from typing import Any

from . import evaluation_data, execution_profiles, llm_materialization, query_scale_data
from .contracts import ContractError, MAX_INTEGER, require_digest, require_id, require_ref, require_uint
from .partitioned_scale_corpus import INDEX_KIND, partition_scale_corpus
from .partitioned_run_contracts import bind_partitioned_run_manifest
from .partitioned_trial_plan import partition_trial_plan
from .policy import validate_policy_profile
from .registry import validate_registry
from .run_contracts import bind_evaluation_contract, content_ref, validate_evaluation_contract

_ROOT = Path(__file__).resolve().parents[2]
_COUNTS = frozenset({400, 800, 1600})
_VERSIONS = frozenset({"baseline-v1", "degraded-v2"})
_CONTROL_ID = "LC-query-scale"
_OBLIGATION_ID = "LO-query-scale"
_EVALUATOR_ID = "finite-query-scale-evaluator"
_USE_CASES = ["UC-LLM"]
_OUTPUTS = ["decision", "evidence", "findings", "plans", "run_receipt"]
_SOURCE_FILES = (
    "partitioned_llm_materialization.py",
    "partitioned_run_contracts.py",
    "partitioned_trial_plan.py",
    "execution_profiles.py",
    "query_scale_data.py",
    "partitioned_scale_corpus.py",
    "partitioned_guardrail_results.py",
    "guardrail_results.py",
    "contracts.py",
    "wire.py",
    "run_contracts.py",
    "normalized.py",
    "llm_evaluator.py",
    "measurement_calibration.py",
    "evaluation_data.py",
    "corpus.py",
    "partitioned_case_set.py",
)


def _invalid(code: str = "PARTITIONED_LLM_MATERIALIZATION_INVALID") -> ContractError:
    return ContractError(code)


def _sha(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        raise _invalid("SOURCE_NOT_READY") from None


def _target_document(version: str, image_id: str) -> dict[str, Any]:
    return llm_materialization.target_document(version, image_id)


def evaluator_document(corpus_index: Any) -> dict[str, Any]:
    """Return the fixed query-scale evaluator document bound to one corpus index."""
    from .partitioned_scale_corpus import INDEX_KIND as CORPUS_INDEX_KIND

    if type(corpus_index) is not dict or corpus_index.get("kind") != CORPUS_INDEX_KIND:
        raise _invalid("CORPUS_INDEX_INVALID")
    source_hashes = {name: _sha(_ROOT / "src" / "gah" / name) for name in _SOURCE_FILES}
    return {
        "schema_version": 1,
        "kind": "query_scale_llm_evaluator",
        "evaluator_id": _EVALUATOR_ID,
        "corpus_index_ref": content_ref("query_scale_corpus_index", corpus_index["corpus_id"], corpus_index),
        "source_sha256": source_hashes,
        "calibration_method": "fixed-pack-independent-calibration-v1",
        "real_workload_performance": False,
        "runtime_admission": False,
    }


def _baseline_inputs(
    value: Any,
    *,
    case_set: dict[str, Any],
    calibration: dict[str, Any],
    evaluator_ref: dict[str, str],
    policy_ref: dict[str, str],
    policy_generation: int,
    image_id: str,
    generation: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if type(value) is not dict or set(value) != {"baseline_ref", "targets", "contract"}:
        raise _invalid("BASELINE_CONTEXT_INVALID")
    baseline_ref = copy.deepcopy(value["baseline_ref"])
    require_ref(baseline_ref)
    if baseline_ref["kind"] != "baseline":
        raise _invalid("BASELINE_CONTEXT_INVALID")
    previous_contract = validate_evaluation_contract(value["contract"])
    if previous_contract["generation"] + 1 != generation:
        raise _invalid("BASELINE_GENERATION_MISMATCH")
    if (previous_contract["case_set_ref"] != content_ref("case_set", case_set["case_set_id"], case_set)
            or previous_contract["calibration_case_set_ref"] != content_ref("case_set", calibration["case_set_id"], calibration)
            or previous_contract["policy_ref"] != policy_ref
            or previous_contract["evaluator_refs"] != [evaluator_ref]
            or previous_contract["policy_series_id"] != policy_ref["id"]
            or previous_contract["policy_generation"] != policy_generation
            or previous_contract["required_categories"] != case_set["required_categories"]
            or previous_contract["use_cases"] != _USE_CASES
            or previous_contract["required_outputs"] != _OUTPUTS):
        raise _invalid("BASELINE_INPUT_MISMATCH")
    targets = value["targets"]
    if type(targets) is not list or len(targets) != 1 or type(targets[0]) is not dict:
        raise _invalid("BASELINE_TARGET_INVALID")
    target = targets[0]
    if set(target) != {"control_id", "target_ref"} or target["control_id"] != _CONTROL_ID:
        raise _invalid("BASELINE_TARGET_INVALID")
    supplied_ref = target["target_ref"]
    if (type(supplied_ref) is not dict or set(supplied_ref) != {"kind", "id", "digest"}
            or supplied_ref["kind"] != "target"):
        raise _invalid("BASELINE_TARGET_INVALID")
    allowed = [
        content_ref("target", doc["target_id"], doc)
        for doc in (_target_document(version, image_id) for version in _VERSIONS)
    ]
    # The old registry is authority-owned and is not part of this pure input.
    # Restrict this declaration to the two fixed target versions; adoption and
    # prior registry binding remain upper-layer responsibilities.
    if supplied_ref not in allowed:
        raise _invalid("BASELINE_TARGET_INVALID")
    normalized_context = {"baseline_ref": baseline_ref, "targets": [copy.deepcopy(target)]}
    return normalized_context, previous_contract


def build(
    policy: Any,
    *,
    case_count: int,
    policy_generation: int,
    run_id: str,
    now: int,
    target_version: str,
    image_id: str,
    worker_digest: str,
    isolation_profile: dict[str, Any],
    generation: int = 1,
    baseline_context: Any = None,
) -> dict[str, Any]:
    """Build a fixed query-scale, partitioned and reference-bound input set."""
    if type(case_count) is not int or case_count not in _COUNTS:
        raise _invalid("QUERY_SCALE_COUNT_INVALID")
    policy_value = validate_policy_profile(policy)
    for value in (policy_generation, now, generation):
        require_uint(value)
    require_id(run_id)
    require_digest(worker_digest)
    if not policy_generation or not generation:
        raise _invalid("GENERATION_INVALID")
    if type(target_version) is not str or target_version not in _VERSIONS:
        raise _invalid("TARGET_VERSION_INVALID")
    if type(image_id) is not str or not image_id.startswith("sha256:"):
        raise _invalid("RUNTIME_IMAGE_INVALID")
    require_digest(image_id[7:])
    if type(isolation_profile) is not dict or not isolation_profile:
        raise _invalid("ISOLATION_PROFILE_INVALID")
    elapsed = policy_value["profiles"]["full"]["elapsed_seconds"]
    if type(elapsed) is not int or now > MAX_INTEGER - elapsed:
        raise _invalid("TIME_RANGE")
    if (generation == 1) != (baseline_context is None):
        raise _invalid("BASELINE_CONTEXT_REQUIRED")

    corpus = query_scale_data.build_scale_corpus(case_count)
    corpus_index, case_set_index, case_set_segments, document_segments = partition_scale_corpus(corpus)
    case_set = corpus["case_set"]
    calibration = evaluation_data.build_pack()["case_sets"]["calibration"]
    evaluator = evaluator_document(corpus_index)
    evaluator_ref = content_ref("evaluator", evaluator["evaluator_id"], evaluator)
    target_docs = {version: _target_document(version, image_id) for version in sorted(_VERSIONS)}
    target_refs = {version: content_ref("target", doc["target_id"], doc) for version, doc in target_docs.items()}
    candidate_target = target_refs[target_version]
    policy_ref = content_ref("policy_profile", policy_value["policy_id"], policy_value)

    baseline_contract = None
    normalized_baseline = None
    binder_baseline = None
    if baseline_context is not None:
        normalized_baseline, baseline_contract = _baseline_inputs(
            baseline_context,
            case_set=case_set,
            calibration=calibration,
            evaluator_ref=evaluator_ref,
            policy_ref=policy_ref,
            policy_generation=policy_generation,
            image_id=image_id,
            generation=generation,
        )
        prior_target = normalized_baseline["targets"][0]["target_ref"]
        # The authority must resolve the prior contract's registry before adoption.
        if prior_target == candidate_target:
            changed_axes: list[str] = []
        else:
            changed_axes = ["target"]
        binder_baseline = {"baseline_ref": copy.deepcopy(normalized_baseline["baseline_ref"]),
                           "targets": copy.deepcopy(normalized_baseline["targets"])}

    controls = [{
        "control_id": _CONTROL_ID,
        "owner": "manager",
        "invariant": "全固定query-scaleケースの検出結果を評価する",
        "criticality": "noncritical",
        "target_ref": copy.deepcopy(candidate_target),
        "dependencies": [],
        "obligations": [{
            "obligation_id": _OBLIGATION_ID,
            "kind": "llm_metric",
            "required": True,
            "event_policy": "aggregate",
            "evaluator_ref": copy.deepcopy(evaluator_ref),
        }],
        "mutation_applicability": {"status": "not_applicable", "reason": "固定query-scale評価ではMutation得点を要求しない"},
    }]
    registry = validate_registry({
        "schema_version": 1,
        "kind": "control_registry",
        "registry_id": f"qscale-registry-{case_count}-{target_version}",
        "controls": controls,
    })
    registry_ref = content_ref("control_registry", registry["registry_id"], registry)
    contract = {
        "schema_version": 1,
        "kind": "evaluation_contract",
        "contract_id": f"qscale-contract-{case_count}-{generation}-{target_version}",
        "generation": generation,
        "policy_series_id": policy_value["policy_id"],
        "policy_generation": policy_generation,
        "policy_ref": copy.deepcopy(policy_ref),
        "registry_ref": copy.deepcopy(registry_ref),
        "case_set_ref": content_ref("case_set", case_set["case_set_id"], case_set),
        "calibration_case_set_ref": content_ref("case_set", calibration["case_set_id"], calibration),
        "evaluator_refs": [copy.deepcopy(evaluator_ref)],
        "required_categories": copy.deepcopy(case_set["required_categories"]),
        "use_cases": copy.deepcopy(_USE_CASES),
        "comparison": ({"mode": "not_applicable", "baseline_ref": None,
                        "changed_axes": ["target"], "reason": "initial_baseline_pending"}
                       if generation == 1 else
                       {"mode": "required", "baseline_ref": copy.deepcopy(normalized_baseline["baseline_ref"]),
                        "changed_axes": changed_axes, "reason": None}),
        "required_outputs": copy.deepcopy(_OUTPUTS),
    }
    contract = bind_evaluation_contract(contract, policy_value, registry, case_set, calibration)["contract"]
    contract_ref = content_ref("evaluation_contract", contract["contract_id"], contract)

    entries: list[dict[str, Any]] = []
    for case in case_set["cases"]:
        variants = ("candidate",) if generation == 1 else ("baseline", "candidate")
        for variant in variants:
            entry_target = candidate_target
            if variant == "baseline":
                entry_target = normalized_baseline["targets"][0]["target_ref"]
            entries.append({
                "obligation_id": _OBLIGATION_ID,
                "case_id": case["case_id"],
                "trial_id": f"{case['case_id']}-trial-1",
                "variant": variant,
                "stage_ids": [step["stage_id"] for step in case["session_steps"]],
                "required": True,
                "event_policy": "aggregate",
                "evaluator_ref": copy.deepcopy(evaluator_ref),
                "target_ref": copy.deepcopy(entry_target),
            })
    plan_id = f"qscale-plan-{generation}-{hashlib.sha256(run_id.encode()).hexdigest()[:24]}"
    plan = {"schema_version": 1, "kind": "trial_plan", "plan_id": plan_id,
            "contract_ref": copy.deepcopy(contract_ref), "entries": entries}
    plan_index, plan_segments = partition_trial_plan(plan)
    manifest = {
        "schema_version": 2,
        "kind": "run_manifest",
        "run_id": run_id,
        "contract_ref": copy.deepcopy(contract_ref),
        "purpose": "baseline_candidate" if generation == 1 else "regression",
        "use_cases": copy.deepcopy(_USE_CASES),
        "target_refs": [copy.deepcopy(candidate_target)],
        "control_ids": [_CONTROL_ID],
        "baseline_ref": None if generation == 1 else copy.deepcopy(normalized_baseline["baseline_ref"]),
        "plan_ref": content_ref("trial_plan_index", plan_index["plan_id"], plan_index),
        "policy_ref": copy.deepcopy(policy_ref),
        "profile": "full",
        "environment_ref": content_ref("environment", "isolated-query-scale", isolation_profile),
        "actor_context_ref": content_ref("actor_context", f"qsa-{hashlib.sha256(run_id.encode()).hexdigest()[:24]}",
                                          {"run_id": run_id, "role": "operator", "request_fixed": True}),
        "created_at": now,
        "deadline": now + elapsed,
    }
    bound = bind_partitioned_run_manifest(
        manifest, contract, plan_index, plan_segments, policy_value, registry, case_set,
        baseline_context=binder_baseline,
    )
    profile_pairs = sorted({(entry["target_ref"]["digest"], entry["evaluator_ref"]["digest"])
                            for entry in plan["entries"]})
    adapter_digest = _sha(_ROOT / "src" / "gah" / "normalized.py")
    profile = {
        "schema_version": 2,
        "kind": "execution_profile",
        "isolation_digest": manifest["environment_ref"]["digest"],
        "bindings": [{"target_digest": target_digest, "evaluator_digest": evaluator_digest,
                      "fixture_digest": worker_digest, "adapter_digests": [adapter_digest]}
                     for target_digest, evaluator_digest in profile_pairs],
    }
    execution_profiles.check_plan(profile, {"manifest": manifest, "plan": plan})
    materialization = {
        "schema_version": 1,
        "kind": "partitioned_query_scale_materialization",
        "run_id": run_id,
        "corpus_family": "query-scale-single-stage-v1",
        "case_count": case_count,
        "planned_trials": len(entries),
        "planned_stages": sum(len(item["stage_ids"]) for item in entries),
        "generation": generation,
        "manifest_ref": content_ref("run_manifest", run_id, manifest),
        "plan_index_ref": content_ref("trial_plan_index", plan_index["plan_id"], plan_index),
        "corpus_index_ref": content_ref(INDEX_KIND, corpus_index["corpus_id"], corpus_index),
        "target_ref": copy.deepcopy(candidate_target),
        "evaluator_ref": copy.deepcopy(evaluator_ref),
        "target_is_synthetic": True,
        "authority_connected": False,
        "runtime_verified": False,
        "admission_verified": False,
        "semantic_oracle_independence": False,
        "real_workload_performance": False,
        "ci_eligible": False,
    }
    result = {
        "policy": policy_value,
        "contract": contract,
        "registry": registry,
        "manifest": manifest,
        "plan_index": plan_index,
        "plan_segments": plan_segments,
        "corpus_index": corpus_index,
        "case_set_index": case_set_index,
        "case_set_segments": case_set_segments,
        "document_segments": document_segments,
        "case_set": case_set,
        "calibration_case_set": calibration,
        "target_documents": target_docs,
        "evaluator_document": evaluator,
        "execution_profile": profile,
        "bound_run": bound,
        "baseline_context": copy.deepcopy(normalized_baseline),
        "baseline_contract": copy.deepcopy(baseline_contract),
        "materialization": materialization,
        "ci_eligible": False,
    }
    return result


__all__ = ["build", "evaluator_document"]
