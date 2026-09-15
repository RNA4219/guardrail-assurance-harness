"""対象版ごとの実行器と評価器を固定する。採択・認証は上位authorityが行う。"""
from copy import deepcopy
from .contracts import ContractError, require_digest, require_object

_LEGACY = {"fixture_digest", "adapter_digests", "isolation_digest"}
_BINDING = {"target_digest", "evaluator_digest", "fixture_digest", "adapter_digests"}


def _adapters(value):
    if type(value) is not list or not 1 <= len(value) <= 100:
        raise ContractError("INVALID_PROFILE")
    for item in value: require_digest(item)
    if len(set(value)) != len(value): raise ContractError("DUPLICATE_REFERENCE")


def validate(value):
    if type(value) is not dict: raise ContractError("INVALID_PROFILE")
    if set(value) == _LEGACY:
        require_digest(value["fixture_digest"]); require_digest(value["isolation_digest"])
        _adapters(value["adapter_digests"])
        return deepcopy(value)
    require_object(value, {"schema_version", "kind", "isolation_digest", "bindings"})
    if type(value["schema_version"]) is not int or value["schema_version"] != 2 or value["kind"] != "execution_profile":
        raise ContractError("INVALID_PROFILE")
    require_digest(value["isolation_digest"])
    bindings = value["bindings"]
    if type(bindings) is not list or not 1 <= len(bindings) <= 1000:
        raise ContractError("INVALID_PROFILE")
    seen = set()
    for item in bindings:
        require_object(item, _BINDING)
        for key in ("target_digest", "evaluator_digest", "fixture_digest"): require_digest(item[key])
        _adapters(item["adapter_digests"])
        key = (item["target_digest"], item["evaluator_digest"])
        if key in seen: raise ContractError("DUPLICATE_REFERENCE")
        seen.add(key)
    return deepcopy(value)


def check_plan(profile, bound):
    """新形式では実施planの全組を過不足なく固定する。旧形式の形を変更しない。"""
    profile = validate(profile)
    if "bindings" not in profile: return
    if profile["isolation_digest"] != bound["manifest"]["environment_ref"]["digest"]:
        raise ContractError("BINDING_MISMATCH")
    planned = {(entry["target_ref"]["digest"], entry["evaluator_ref"]["digest"]) for entry in bound["plan"]["entries"]}
    declared = {(item["target_digest"], item["evaluator_digest"]) for item in profile["bindings"]}
    if not planned or planned != declared: raise ContractError("EXECUTION_PROFILE_SCOPE_MISMATCH")


def expected(profile, target_digest, evaluator_digest):
    """検証済みprofileから一意の実行束縛だけを返す。未知対象へ既定値を使わない。"""
    profile = validate(profile)
    if "bindings" not in profile:
        return {key: deepcopy(profile[key]) for key in _LEGACY}
    require_digest(target_digest); require_digest(evaluator_digest)
    found = [item for item in profile["bindings"] if item["target_digest"] == target_digest
             and item["evaluator_digest"] == evaluator_digest]
    if len(found) != 1: raise ContractError("EXECUTION_PROFILE_SCOPE_MISMATCH")
    return {"fixture_digest": found[0]["fixture_digest"], "adapter_digests": deepcopy(found[0]["adapter_digests"]),
            "isolation_digest": profile["isolation_digest"]}
