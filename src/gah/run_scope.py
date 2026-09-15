"""変更参照から検査範囲を決め、authorityの不変な準備応答へ結ぶ。"""
from copy import deepcopy
import hashlib

from .adoption import AdoptionError, _ACTORS
from .contracts import ContractError, require_ref
from .registry import affected_controls, dependency_closure, validate_registry
from .resources import _unpack
from .run_contracts import content_ref
from .wire import canonical_bytes


def normalize_refs(refs):
    if type(refs) is not list or not 0 < len(refs) <= 100:
        raise ContractError()
    for ref in refs:
        require_ref(ref)
    keys = [canonical_bytes(ref) for ref in refs]
    if len(set(keys)) != len(keys):
        raise ContractError("DUPLICATE_REFERENCE")
    return sorted(deepcopy(refs), key=canonical_bytes)


def request_id(run_id):
    return "scope-prepare-" + run_id


def _refs(value):
    if type(value) is dict:
        if set(value) == {"kind", "id", "digest"}:
            return {canonical_bytes(value)}
        return set().union(*(_refs(item) for item in value.values()))
    if type(value) is list:
        return set().union(*(_refs(item) for item in value))
    return set()


def derive(bound, run_id, changed_refs):
    changed = normalize_refs(changed_refs)
    registry = validate_registry(bound["registry"])
    controls = {c["control_id"]: c for c in registry["controls"]}
    all_ids = sorted(controls)
    shared = _refs(bound["contract"]) | {
        canonical_bytes(content_ref("evaluation_contract", bound["contract"]["contract_id"], bound["contract"]))}
    direct, unknown = set(), False
    for ref in changed:
        key = canonical_bytes(ref)
        matched = set(controls) if key in shared else {cid for cid, control in controls.items() if key in _refs(control)}
        if not matched:
            unknown = True
        direct.update(matched)
    selected = all_ids if unknown else dependency_closure(registry, affected_controls(registry, sorted(direct)))
    omitted = sorted(set(all_ids) - set(selected))
    return {"schema_version": 1, "kind": "run_scope", "run_id": run_id,
        "contract_ref": deepcopy(bound["manifest"]["contract_ref"]),
        "registry_ref": deepcopy(bound["contract"]["registry_ref"]),
        "changed_refs": changed, "direct_control_ids": sorted(direct),
        "selected_control_ids": selected, "unexecuted_control_ids": omitted,
        "executed_scope": "targeted" if omitted else "full",
        "reason": "UNKNOWN_IMPACT_FULL_FALLBACK" if unknown else "KNOWN_IMPACT_CLOSURE",
        "ci_eligible": False}


def load(db, history, run_id):
    """既存idempotency行のPKを使い、要求hash・主体・応答hashを照合する。"""
    row = db.execute("SELECT * FROM idempotency WHERE request_id=?", (request_id(run_id),)).fetchone()
    if row is None:
        return None
    try:
        response = _unpack(row["response_json"], row["response_digest"])
        scope = response["scope"]
        refs = normalize_refs(scope["changed_refs"])
        request = {"schema_version": 1, "action": "run_prepare_scoped", "request_id": request_id(run_id),
            "run_id": run_id, "contract_series_id": history["series_id"],
            "expected_contract_ref": scope["contract_ref"], "changed_refs": refs}
        if ((row["actor_id"], row["context"]) != _ACTORS[(12004, 12004)]
                or hashlib.sha256(canonical_bytes(request)).hexdigest() != row["request_digest"]):
            raise ContractError()
        return response
    except (ContractError, KeyError, TypeError, ValueError):
        raise AdoptionError("RUN_SCOPE_INVALID") from None


def restrict(scope, plan, manifest, context, materialization, registry):
    selected = set(scope["selected_control_ids"])
    controls = [c for c in registry["controls"] if c["control_id"] in selected]
    obligations = {o["obligation_id"] for c in controls for o in c["obligations"]}
    plan["entries"] = [e for e in plan["entries"] if e["obligation_id"] in obligations]
    manifest["control_ids"] = scope["selected_control_ids"][:]
    refs = {canonical_bytes(c["target_ref"]): c["target_ref"] for c in controls}
    manifest["target_refs"] = [deepcopy(refs[key]) for key in sorted(refs)]
    context["targets"] = [t for t in context["targets"] if t["control_id"] in selected]
    materialization["manifest"]["records"] = [r for r in materialization["manifest"]["records"]
        if r["obligation_id"] in obligations]


def require_full_source(source):
    bound = source["bound"]
    if set(bound["manifest"]["control_ids"]) != {c["control_id"] for c in bound["registry"]["controls"]}:
        raise AdoptionError("FULL_SCOPE_REQUIRED")
