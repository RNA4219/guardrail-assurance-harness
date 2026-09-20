"""Pure, immutable usage-threshold basis for saved run decisions."""
from copy import deepcopy

from .contracts import ContractError, MAX_DOCUMENT_BYTES, MAX_INTEGER, require_id, require_ref, require_uint
from .policy import validate_policy_profile
from .run_contracts import content_ref, validate_run_manifest
from .wire import canonical_bytes


AXES = (
    "elapsed_seconds",
    "case_trial_executions",
    "model_calls",
    "total_tokens",
    "api_cost_usd_micros",
)
BASIS_FIELDS = {
    "schema_version", "kind", "run_id", "manifest_ref", "policy_ref", "profile",
    "started_at", "closed_at", "usage", "limits", "warning_usage_min",
}
SNAPSHOT_FIELDS = {
    "run_id", "manifest_digest", "owner_id", "owner_epoch", "deadline", "cancelled",
    "breached", "closed", "closed_at", "resources", "budget_closure", "ci_eligible",
}
RESOURCE_FIELDS = {
    "case_trial_executions", "model_calls", "input_tokens", "output_tokens",
    "api_cost_usd_micros", "slots", "unsettled", "total_tokens",
    "global_api_cost_usd_micros",
}


def _invalid():
    return ContractError("BUDGET_WARNING_INVALID")


def _bound_values(bound):
    fields = {"manifest", "contract", "plan", "policy", "registry", "case_set",
              "selected_controls", "ci_eligible"}
    if type(bound) is not dict or type(bound.get("manifest")) is not dict:
        raise _invalid()
    if bound["manifest"].get("schema_version") == 2:
        from .partitioned_run_contracts import validate_partitioned_run_manifest
        if set(bound) != fields | {"_partitioned_context", "_partitioned_receipt"}:
            raise _invalid()
        manifest = validate_partitioned_run_manifest(bound["manifest"])
        if (bound["_partitioned_context"]["manifest"] != manifest
                or bound["_partitioned_context"]["policy"] != bound["policy"]
                or bound["_partitioned_receipt"]["manifest_ref"] != content_ref(
                    "run_manifest", manifest["run_id"], manifest)):
            raise _invalid()
    else:
        if set(bound) != fields:
            raise _invalid()
        manifest = validate_run_manifest(bound["manifest"])
    policy = validate_policy_profile(bound["policy"])
    profile = manifest["profile"]
    if (profile not in policy["profiles"] or bound["ci_eligible"] is not False
            or content_ref("policy_profile", policy["policy_id"], policy) != manifest["policy_ref"]):
        raise _invalid()
    manifest_ref = content_ref("run_manifest", manifest["run_id"], manifest)
    limits = {axis: policy["profiles"][profile][axis] for axis in AXES}
    threshold = policy["warning_usage_min"]
    if (type(threshold) is not list or len(threshold) != 2
            or any(type(part) is not int for part in threshold)
            or threshold[0] <= 0 or threshold[1] <= 0 or threshold[0] > threshold[1]):
        raise _invalid()
    if any(type(limits[axis]) is not int or not 0 < limits[axis] <= MAX_INTEGER for axis in AXES):
        raise _invalid()
    return manifest, policy, manifest_ref, limits, threshold


def _check_snapshot(snapshot, manifest, manifest_ref, started_at):
    if type(snapshot) is not dict or set(snapshot) != SNAPSHOT_FIELDS:
        raise _invalid()
    if (snapshot["run_id"] != manifest["run_id"]
            or snapshot["manifest_digest"] != manifest_ref["digest"]
            or type(snapshot["owner_id"]) is not str
            or type(snapshot["owner_epoch"]) is not int or snapshot["owner_epoch"] < 1
            or type(snapshot["deadline"]) is not int or snapshot["deadline"] != manifest["deadline"]
            or snapshot["cancelled"] is not False or snapshot["breached"] is not False
            or snapshot["closed"] is not True or snapshot["budget_closure"] is not True
            or snapshot["ci_eligible"] is not False):
        raise _invalid()
    require_id(snapshot["owner_id"])
    require_uint(snapshot["owner_epoch"])
    require_uint(snapshot["deadline"])
    closed_at = snapshot["closed_at"]
    require_uint(closed_at)
    if not manifest["created_at"] <= started_at <= closed_at <= manifest["deadline"]:
        raise _invalid()
    resources = snapshot["resources"]
    if type(resources) is not dict or set(resources) != RESOURCE_FIELDS:
        raise _invalid()
    for value in resources.values():
        require_uint(value)
    if (resources["slots"] != 0 or resources["unsettled"] != 0
            or resources["total_tokens"] != resources["input_tokens"] + resources["output_tokens"]):
        raise _invalid()
    return closed_at, resources


def _encode(basis):
    raw = canonical_bytes(basis)
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise _invalid()
    return raw


def build_basis(bound, snapshot, started_at):
    """Build a closed 5-axis basis from a finalized ResourceBook snapshot."""
    try:
        manifest, policy, manifest_ref, limits, threshold = _bound_values(bound)
        require_uint(started_at)
        closed_at, resources = _check_snapshot(snapshot, manifest, manifest_ref, started_at)
        usage = {
            "elapsed_seconds": closed_at - started_at,
            "case_trial_executions": resources["case_trial_executions"],
            "model_calls": resources["model_calls"],
            "total_tokens": resources["total_tokens"],
            "api_cost_usd_micros": resources["api_cost_usd_micros"],
        }
        if any(usage[axis] > limits[axis] for axis in AXES):
            raise _invalid()
        basis = {
            "schema_version": 1,
            "kind": "run_budget_warning_basis",
            "run_id": manifest["run_id"],
            "manifest_ref": manifest_ref,
            "policy_ref": deepcopy(manifest["policy_ref"]),
            "profile": manifest["profile"],
            "started_at": started_at,
            "closed_at": closed_at,
            "usage": usage,
            "limits": limits,
            "warning_usage_min": deepcopy(threshold),
        }
        _encode(basis)
        return validate_basis(bound, basis, closed_at)
    except ContractError:
        raise _invalid() from None
    except (KeyError, TypeError, ValueError, RecursionError, OverflowError):
        raise _invalid() from None


def validate_basis(bound, basis, assessed_at):
    """Validate an immutable basis without consulting live resource state."""
    try:
        manifest, policy, manifest_ref, limits, threshold = _bound_values(bound)
        require_uint(assessed_at)
        if type(basis) is not dict or set(basis) != BASIS_FIELDS:
            raise _invalid()
        _encode(basis)
        if (type(basis["schema_version"]) is not int or basis["schema_version"] != 1
                or basis["kind"] != "run_budget_warning_basis"
                or basis["run_id"] != manifest["run_id"]
                or basis["manifest_ref"] != manifest_ref
                or basis["policy_ref"] != manifest["policy_ref"]
                or basis["profile"] != manifest["profile"]
                or basis["limits"] != limits
                or basis["warning_usage_min"] != threshold):
            raise _invalid()
        require_ref(basis["manifest_ref"])
        require_ref(basis["policy_ref"])
        if basis["policy_ref"]["kind"] != "policy_profile":
            raise _invalid()
        require_id(basis["run_id"])
        if type(basis["profile"]) is not str:
            raise _invalid()
        started_at, closed_at = basis["started_at"], basis["closed_at"]
        require_uint(started_at)
        require_uint(closed_at)
        if (not manifest["created_at"] <= started_at <= closed_at <= manifest["deadline"] <= MAX_INTEGER
                or assessed_at < closed_at):
            raise _invalid()
        if type(basis["usage"]) is not dict or set(basis["usage"]) != set(AXES):
            raise _invalid()
        if type(basis["limits"]) is not dict or set(basis["limits"]) != set(AXES):
            raise _invalid()
        for axis in AXES:
            require_uint(basis["usage"][axis])
            require_uint(basis["limits"][axis])
            if basis["limits"][axis] != limits[axis] or basis["usage"][axis] > limits[axis]:
                raise _invalid()
        if basis["usage"]["elapsed_seconds"] != closed_at - started_at:
            raise _invalid()
        if (type(basis["warning_usage_min"]) is not list or len(basis["warning_usage_min"]) != 2
                or any(type(part) is not int for part in basis["warning_usage_min"])):
            raise _invalid()
        if policy["warning_usage_min"] != basis["warning_usage_min"]:
            raise _invalid()
        return deepcopy(basis)
    except ContractError:
        raise _invalid() from None
    except (KeyError, TypeError, ValueError, RecursionError, OverflowError):
        raise _invalid() from None


def warning_dimensions(bound, basis, assessed_at):
    """Return policy-ordered dimensions at or above the exact rational threshold."""
    value = validate_basis(bound, basis, assessed_at)
    numerator, denominator = value["warning_usage_min"]
    policy = validate_policy_profile(bound["policy"])
    return [axis for axis in policy["warning_dimensions"]
        if value["usage"][axis] * denominator >= value["limits"][axis] * numerator]


__all__ = ["AXES", "build_basis", "validate_basis", "warning_dimensions"]
