"""Strict binding for a RunManifest that references a partitioned TrialPlan."""

from __future__ import annotations

from copy import deepcopy
import json
from typing import Any, Sequence

from .contracts import ContractError, require_id, require_object, require_uint
from .partitioned_trial_plan import (
    MAX_ARTIFACT_BYTES,
    MAX_ENTRIES,
    MAX_INTEGER,
    MAX_RECONSTRUCTED_BYTES,
    MAX_SEGMENTS,
    _canonical as _partitioned_canonical,
    restore_trial_plan,
)
from .policy import validate_policy_profile
from .registry import validate_registry
from .corpus import validate_case_set
from .run_contracts import (
    _PROFILES,
    _PURPOSES,
    _USE_CASES,
    _bind_trial_plan_validated,
    _enum,
    _invalid,
    _ref,
    _require_ref_match,
    _unique_ids,
    _unique_refs,
    content_ref,
    validate_evaluation_contract,
)


_MANIFEST_FIELDS = {
    "schema_version", "kind", "run_id", "contract_ref", "purpose", "use_cases",
    "target_refs", "control_ids", "baseline_ref", "plan_ref", "policy_ref",
    "profile", "environment_ref", "actor_context_ref", "created_at", "deadline",
}
_BOUND_FIELDS = {
    "schema_version", "kind", "manifest_ref", "contract_ref", "plan_index_ref",
    "policy_ref", "registry_ref", "case_set_ref", "selected_controls", "ci_eligible",
}

_MATERIALIZE_CACHE_INPUT_BYTES = 6 * 1024 * 1024


def validate_partitioned_run_manifest(value: Any) -> dict[str, Any]:
    """Validate the v2 manifest wire shape and its fixed-purpose constraints."""
    try:
        require_object(value, _MANIFEST_FIELDS)
        if type(value["schema_version"]) is not int or value["schema_version"] != 2:
            raise _invalid("UNSUPPORTED_VERSION")
        if value["kind"] != "run_manifest":
            raise _invalid()
        require_id(value["run_id"])
        _ref(value["contract_ref"], "evaluation_contract")
        _enum(value["purpose"], _PURPOSES)
        _unique_ids(value["use_cases"], maximum=2)
        for item in value["use_cases"]:
            _enum(item, _USE_CASES)
        _unique_refs(value["target_refs"], kind="target", maximum=1000)
        _unique_ids(value["control_ids"], maximum=1000)
        if value["baseline_ref"] is not None:
            _ref(value["baseline_ref"], "baseline")
        _ref(value["plan_ref"], "trial_plan_index")
        _ref(value["policy_ref"], "policy_profile")
        _enum(value["profile"], _PROFILES)
        _ref(value["environment_ref"], "environment")
        _ref(value["actor_context_ref"], "actor_context")
        require_uint(value["created_at"])
        require_uint(value["deadline"])
        if value["deadline"] <= value["created_at"]:
            raise _invalid("DEADLINE_INVALID")
        _partitioned_canonical(value, maximum=MAX_ARTIFACT_BYTES)
        return deepcopy(value)
    except ContractError:
        raise
    except (KeyError, TypeError, ValueError, RecursionError):
        raise _invalid() from None


def _bind_partitioned_run(
    manifest: Any,
    contract: Any,
    index: Any,
    segments: Sequence[Any],
    policy: Any,
    registry: Any,
    case_set: Any,
    *,
    baseline_context: Any = None,
    materialize: bool = False,
) -> dict[str, Any]:
    """共有の完全binding。内部利用時だけ検証済みlogical planを返す。"""
    m = validate_partitioned_run_manifest(manifest)
    c = validate_evaluation_contract(contract)
    p = restore_trial_plan(index, segments)
    policy_value = validate_policy_profile(policy)
    registry_value = validate_registry(registry)
    case_value = validate_case_set(case_set)
    try:
        _require_ref_match(m["contract_ref"], "evaluation_contract", c)
        _require_ref_match(c["policy_ref"], "policy_profile", policy_value)
        _require_ref_match(c["registry_ref"], "control_registry", registry_value)
        _require_ref_match(c["case_set_ref"], "case_set", case_value)
        _require_ref_match(m["plan_ref"], "trial_plan_index", index)
        _require_ref_match(m["policy_ref"], "policy_profile", policy_value)
        if set(m["use_cases"]) != set(c["use_cases"]):
            raise _invalid("USE_CASE_MISMATCH")
        if m["target_refs"] == []:
            raise _invalid("TARGET_MISMATCH")
        if m["purpose"] in {"calibration", "contract_validation"}:
            raise _invalid("BOOTSTRAP_REQUIRED")
        if m["purpose"] == "baseline_candidate":
            if m["baseline_ref"] is not None or c["comparison"]["mode"] != "not_applicable" or m["profile"] != "full":
                raise _invalid("BASELINE_CANDIDATE_MISMATCH")
        elif m["purpose"] == "contract_old_regression":
            if m["profile"] != "full" or m["baseline_ref"] != c["comparison"]["baseline_ref"]:
                raise _invalid("CONTRACT_OLD_REGRESSION_MISMATCH")
        elif m["purpose"] == "contract_candidate":
            if (m["baseline_ref"] is None or c["comparison"]["mode"] != "required"
                    or m["baseline_ref"] != c["comparison"]["baseline_ref"] or m["profile"] != "full"):
                raise _invalid("CONTRACT_CANDIDATE_MISMATCH")
        elif m["purpose"] == "regression":
            if m["baseline_ref"] is None or c["comparison"]["mode"] != "required":
                raise _invalid("BASELINE_REQUIRED")
            if m["baseline_ref"] != c["comparison"]["baseline_ref"]:
                raise _invalid("BASELINE_MISMATCH")
        elif m["purpose"] == "diagnostic":
            if c["comparison"]["mode"] == "required" and m["baseline_ref"] != c["comparison"]["baseline_ref"]:
                raise _invalid("BASELINE_MISMATCH")
            if c["comparison"]["mode"] == "not_applicable" and m["baseline_ref"] is not None:
                raise _invalid("BASELINE_NOT_APPLICABLE")
        elapsed = policy_value["profiles"][m["profile"]]["elapsed_seconds"]
        if m["deadline"] > m["created_at"] + elapsed:
            raise _invalid("DEADLINE_INVALID")

        # The restored plan is a fresh validated tree; all semantic coverage,
        # variant, stage, evaluator, target, and obligation checks remain v1's.
        bound_plan = _bind_trial_plan_validated(
            p, c, registry_value, case_value, m["control_ids"], m["target_refs"],
            baseline_context=baseline_context,
        )
        result = {
            "schema_version": 2,
            "kind": "bound_partitioned_run",
            "manifest_ref": content_ref("run_manifest", m["run_id"], m),
            "contract_ref": deepcopy(m["contract_ref"]),
            "plan_index_ref": content_ref("trial_plan_index", index["plan_id"], index),
            "policy_ref": deepcopy(m["policy_ref"]),
            "registry_ref": content_ref("control_registry", registry_value["registry_id"], registry_value),
            "case_set_ref": content_ref("case_set", case_value["case_set_id"], case_value),
            "selected_controls": deepcopy(bound_plan["selected_controls"]),
            "ci_eligible": False,
        }
        if set(result) != _BOUND_FIELDS:
            raise _invalid()
        _partitioned_canonical(result, maximum=MAX_ARTIFACT_BYTES)
        if materialize:
            return {
                "manifest": m, "contract": c, "plan": p, "policy": policy_value,
                "registry": registry_value, "case_set": case_value,
                "selected_controls": deepcopy(result["selected_controls"]), "ci_eligible": False,
                "_partitioned_context": {
                    "manifest": m, "contract": c, "index": index, "segments": segments,
                    "policy": policy_value, "registry": registry_value, "case_set": case_value,
                },
                "_partitioned_receipt": result,
            }
        return result
    except ContractError:
        raise
    except (KeyError, TypeError, ValueError, RecursionError):
        raise _invalid() from None


def bind_partitioned_run_manifest(manifest, contract, index, segments, policy, registry, case_set,
                                  *, baseline_context=None):
    """従来どおり、wireへ渡せる小さい参照だけを返す。"""
    return _bind_partitioned_run(manifest, contract, index, segments, policy, registry, case_set,
                                 baseline_context=baseline_context)


from .immutable_cache import binding_cache


@binding_cache.memoize
def _cached_materialize_partitioned_run(source_digest, implementation, manifest_validator,
                                        contract_validator, plan_restorer, policy_validator,
                                        registry_validator, case_validator, trial_binder,
                                        canonicalizer, object_checker, id_checker, uint_checker,
                                        reference_checker, enum_checker, unique_ids_checker,
                                        unique_refs_checker, ref_matcher, content_ref_builder,
                                        identity, payload):
    """Cache only pure validated JSON output; never authority/currentness state."""
    del source_digest, manifest_validator, contract_validator, plan_restorer
    del policy_validator, registry_validator, case_validator, trial_binder, canonicalizer
    del object_checker, id_checker, uint_checker, reference_checker, enum_checker
    del unique_ids_checker, unique_refs_checker, ref_matcher, content_ref_builder, identity
    (manifest, contract, index, segment_container, segments, policy, registry,
     case_set, baseline_context) = json.loads(payload)
    del segment_container
    index_snapshot = json.loads(_partitioned_canonical(index, maximum=MAX_ARTIFACT_BYTES))
    segment_snapshots = [json.loads(_partitioned_canonical(item, maximum=MAX_ARTIFACT_BYTES))
                         for item in segments]
    result = implementation(manifest, contract, index_snapshot, segment_snapshots,
                            policy, registry, case_set, baseline_context=baseline_context,
                            materialize=True)
    return json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def materialize_partitioned_run(manifest, contract, index, segments, policy, registry, case_set,
                                 *, baseline_context=None):
    """完全contextの純粋bindingを共有cacheし、毎回独立treeを返す。

    source identityと全入力をkeyに使う。DB/currentness/authority結果は保持しない。
    非plainまたは大きすぎる入力は既存検査経路へfallbackする。
    """
    if type(segments) not in (list, tuple) or not 1 <= len(segments) <= MAX_SEGMENTS:
        raise _invalid("PARTITIONED_CONTEXT_INVALID")
    values = [manifest, contract, index, type(segments).__name__, list(segments),
              policy, registry, case_set, baseline_context]
    payload = None
    source_digest = None
    try:
        from .cache_inputs import encode_result, plain
        if plain(values):
            candidate = encode_result(values)
            if len(candidate.encode("utf-8")) <= _MATERIALIZE_CACHE_INPUT_BYTES:
                from .evaluation_authority import _source_digest
                source_digest = _source_digest()
                payload = candidate
    except (ContractError, TypeError, ValueError, UnicodeError, RecursionError, OSError):
        # Cache-key construction failure takes the ordinary validation path.
        pass
    if payload is not None and source_digest is not None:
        limits = ":".join(str(value) for value in (
            MAX_ARTIFACT_BYTES, MAX_ENTRIES, MAX_INTEGER, MAX_RECONSTRUCTED_BYTES,
            MAX_SEGMENTS, _MATERIALIZE_CACHE_INPUT_BYTES))
        cached = _cached_materialize_partitioned_run(
            source_digest, _bind_partitioned_run, validate_partitioned_run_manifest,
            validate_evaluation_contract, restore_trial_plan, validate_policy_profile,
            validate_registry, validate_case_set, _bind_trial_plan_validated,
            _partitioned_canonical, require_object, require_id, require_uint, _ref, _enum,
            _unique_ids, _unique_refs, _require_ref_match, content_ref, limits, payload,
        )
        return json.loads(cached)
    index_snapshot = json.loads(_partitioned_canonical(index, maximum=MAX_ARTIFACT_BYTES))
    segment_snapshots = [json.loads(_partitioned_canonical(item, maximum=MAX_ARTIFACT_BYTES))
                         for item in segments]
    return _bind_partitioned_run(manifest, contract, index_snapshot, segment_snapshots,
                                 policy, registry, case_set, baseline_context=baseline_context,
                                 materialize=True)


def validate_partitioned_runtime(value, baseline_context=None):
    """内部contextの全文と保存用ref-only receiptが同じ実体を指すことを照合。"""
    fields = {"manifest", "contract", "plan", "policy", "registry", "case_set", "selected_controls",
              "ci_eligible", "_partitioned_context", "_partitioned_receipt"}
    context_fields = {"manifest", "contract", "index", "segments", "policy", "registry", "case_set"}
    try:
        require_object(value, fields)
        context = value["_partitioned_context"]
        require_object(context, context_fields)
        if value["ci_eligible"] is not False:
            raise _invalid("PARTITIONED_CONTEXT_INVALID")
        for name in ("manifest", "contract", "policy", "registry", "case_set"):
            if context[name] != value[name]:
                raise _invalid("PARTITIONED_CONTEXT_INVALID")
        rebound = materialize_partitioned_run(
            context["manifest"], context["contract"], context["index"], context["segments"],
            context["policy"], context["registry"], context["case_set"], baseline_context=baseline_context)
        if (value["plan"] != rebound["plan"] or value["selected_controls"] != rebound["selected_controls"]
                or value["_partitioned_receipt"] != rebound["_partitioned_receipt"]):
            raise _invalid("PARTITIONED_CONTEXT_INVALID")
        return rebound
    except ContractError:
        raise
    except (KeyError, TypeError, ValueError, RecursionError):
        raise _invalid("PARTITIONED_CONTEXT_INVALID") from None


__all__ = ["bind_partitioned_run_manifest", "validate_partitioned_run_manifest",
           "materialize_partitioned_run", "validate_partitioned_runtime"]
