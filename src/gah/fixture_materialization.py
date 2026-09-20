"""固定fixtureの実体packと実行manifestを結び付ける純粋な部品。"""

from __future__ import annotations

from copy import deepcopy
import hashlib
from typing import Any

from .contracts import ContractError, MAX_INTEGER, require_digest, require_id, require_object
from .policy import validate_policy_profile
from .run_contracts import (
    bind_run_manifest, content_ref, validate_evaluation_contract,
    validate_run_manifest, validate_trial_plan,
)
from .cache_inputs import bind_run_manifest as cached_bind_run_manifest
from .registry import validate_registry
from .corpus import validate_case_set

_PROFILE_FIELDS = {"fixture_digest", "adapter_digest", "isolation_digest"}
_LOCK_FIELDS = {"schema_version", "image_id", "base_ref", "worker_digest", "docker_binary_digest",
                "entrypoint", "environment", "platform"}
_PACK_FIELDS = {"schema_version", "kind", "pack_id", "run_id", "worker_digest", "runtime_lock",
                "execution_profile", "materials", "calibration_materials"}
_MATERIAL_FIELDS = {"scenario", "control_id", "obligation_id", "case_id", "trial_id", "stage_id",
                    "input_payload", "oracle_payload", "initial_state_payload", "expected"}
_MANIFEST_FIELDS = {"schema_version", "kind", "materialization_id", "run_id", "worker_ref", "image_ref",
                    "profile", "source_digest", "records", "probes", "created_at", "ci_eligible"}
_RECORD_FIELDS = {"schema_version", "kind", "scenario", "input_ref", "oracle_ref", "control_id",
                  "obligation_id", "case_id", "trial_id", "stage_id", "variant", "expected"}
_PROBE_FIELDS = {"schema_version", "kind", "scenario", "expected_status", "expected_reason"}
_EXPECTED = {
    **{f"constraint:C{n:02d}:good": {"observation": "PASS"} for n in range(1, 11)},
    **{f"mutation:F{n:02d}:healthy": {"mutation_outcome": "KILLED"} for n in range(1, 6)},
}
_PROBES = {
    "probe:isolation": ("COMPLETED", None), "probe:child_timeout": ("TIMEOUT", "TIMEOUT"),
    "probe:oversized": ("FAILED", "OUTPUT_TOO_LARGE"), "probe:malformed": ("FAILED", "OUTPUT_REJECTED"),
    "probe:rejected_marker": ("FAILED", "OUTPUT_REJECTED"), "probe:crash": ("FAILED", "EXECUTION_FAILURE"),
}


def _bad(code: str = "INVALID_FIXTURE_MATERIALIZATION") -> ContractError:
    return ContractError(code)


def _digest(value: Any) -> None:
    try:
        require_digest(value)
    except ContractError:
        raise _bad("INVALID_DIGEST") from None


def _id(value: Any) -> None:
    try:
        require_id(value)
    except ContractError:
        raise _bad("INVALID_ID") from None


def _time(value: Any) -> int:
    if type(value) is not int or not 0 <= value <= MAX_INTEGER:
        raise _bad("INVALID_TIME")
    return value


def _ref(kind: str, identifier: str, value: Any) -> dict[str, str]:
    try:
        return content_ref(kind, identifier, value)
    except (ContractError, KeyError, TypeError, ValueError, RecursionError):
        raise _bad("REFERENCE_CONTENT_INVALID") from None


def _validate_profile(value: Any) -> dict[str, str]:
    try:
        require_object(value, _PROFILE_FIELDS)
    except ContractError:
        raise _bad("INVALID_PROFILE") from None
    for field in _PROFILE_FIELDS:
        _digest(value[field])
    return deepcopy(value)


def _validate_lock(value: Any) -> dict[str, Any]:
    try:
        require_object(value, _LOCK_FIELDS)
    except ContractError:
        raise _bad("INVALID_RUNTIME_LOCK") from None
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise _bad("UNSUPPORTED_VERSION")
    for field, prefix in (("image_id", "sha256:"), ("base_ref", "python@sha256:")):
        if type(value[field]) is not str or not value[field].startswith(prefix):
            raise _bad("INVALID_RUNTIME_LOCK")
        tail = value[field][len(prefix):]
        if len(tail) != 64 or any(c not in "0123456789abcdef" for c in tail):
            raise _bad("INVALID_RUNTIME_LOCK")
    _digest(value["worker_digest"]); _digest(value["docker_binary_digest"])
    if value["entrypoint"] != ["/usr/local/bin/python", "-I", "-B", "/opt/gah/fixture_worker.py"]:
        raise _bad("INVALID_RUNTIME_LOCK")
    if (type(value["environment"]) is not list or len(value["environment"]) > 32
            or any(type(v) is not str or len(v) > 2048 for v in value["environment"])):
        raise _bad("INVALID_RUNTIME_LOCK")
    if value["platform"] != "linux/amd64":
        raise _bad("INVALID_RUNTIME_LOCK")
    return deepcopy(value)


def _target_ref(worker_digest: str, scenario: str) -> dict[str, str]:
    return _ref("target", "fixture-target-" + scenario.replace(":", "-"),
                {"worker_digest": worker_digest, "scenario": scenario})


def _evaluator_ref(worker_digest: str) -> dict[str, str]:
    return {"kind": "evaluator", "id": "fixture-worker-evaluator", "digest": worker_digest}


def _fixed_environment_ref() -> dict[str, str]:
    try:
        from .assurance_authority import fixed_profile
        digest = fixed_profile()["isolation_digest"]
        _digest(digest)
        return {"kind": "environment", "id": "fixed-docker-profile", "digest": digest}
    except Exception:
        raise _bad("INVALID_ENVIRONMENT") from None


def _constraint_input(number: int) -> dict[str, Any]:
    values = {
        1: {"changed_paths": ["candidate/allowed.txt"], "allowed_paths": ["candidate/allowed.txt"]},
        2: {"read_paths": ["fixtures/input.json"], "declared_inputs": ["fixtures/input.json"]},
        3: {"protected_before": "protected-v1", "protected_after": "protected-v1"},
        4: {"artifact": {"result": "ok", "count": 1}},
        5: {"checks": {"required": True, "fixed": True}},
        6: {"required_check_failed": False, "declared_success": True},
        7: {"dependency_changes": ["dep/base"], "allowed_dependency_changes": ["dep/base"]},
        8: {"transfer_marker": "GAH_SYNTHETIC_TRANSFER_MARKER", "artifact_text": "safe"},
        9: {"tools_used": ["fixed-parser"], "fixed_tools": ["fixed-parser"]},
        10: {"artifact": {"id": "artifact-1"}, "check": {"id": "check-1"},
             "report": {"artifact_id": "artifact-1", "check_id": "check-1"}},
    }
    return deepcopy(values[number])


def _mutation_input(number: int) -> dict[str, Any]:
    fields = {1: {"required_check": False}, 2: {"observed": 9, "threshold": 10},
              3: {"input_ref": "input-b"}, 4: {"evidence_fresh": False},
              5: {"dependency_state": "decayed"}}
    return {"reached": True, "detector_signal": True, "unrelated_failure": False, **fields[number]}


def _payloads(worker_digest: str, scenario: str) -> tuple[dict, dict, dict]:
    kind, code, _ = scenario.split(":")
    candidate = (_constraint_input(int(code[1:])) if kind == "constraint"
                 else _mutation_input(int(code[1:])))
    input_payload = {"schema_version": 1, "kind": "fixture_input_payload", "scenario": scenario,
                     "worker_digest": worker_digest, "candidate": candidate}
    oracle_payload = {"schema_version": 1, "kind": "fixture_oracle_payload", "scenario": scenario,
                      "worker_digest": worker_digest, "expected": deepcopy(_EXPECTED[scenario])}
    initial_payload = {"schema_version": 1, "kind": "fixture_initial_state", "scenario": scenario,
                       "worker_digest": worker_digest, "state": "baseline"}
    return input_payload, oracle_payload, initial_payload


def _calibration_materials(worker_digest: str) -> list[dict[str, Any]]:
    materials = []
    for index in range(36):
        identifier = f"measurement-{index:02d}"
        input_payload = {"schema_version": 1, "kind": "measurement_calibration_input",
                         "scenario": "calibration", "vector_id": identifier,
                         "dimension": index % 6, "worker_digest": worker_digest}
        oracle_payload = {"schema_version": 1, "kind": "measurement_calibration_oracle",
                          "scenario": "calibration", "vector_id": identifier,
                          "expected": {"status": "fixed_metadata"}, "worker_digest": worker_digest}
        initial_payload = {"schema_version": 1, "kind": "measurement_calibration_initial_state",
                           "scenario": "calibration", "vector_id": identifier,
                           "state": "baseline", "worker_digest": worker_digest}
        materials.append({"vector_id": identifier, "case_id": "fixture-calibration-" + identifier,
                          "input_payload": input_payload, "oracle_payload": oracle_payload,
                          "initial_state_payload": initial_payload})
    return materials


def _core_bound(bound_run: Any) -> dict[str, Any]:
    if type(bound_run) is not dict:
        raise _bad("BINDING_INPUT_INVALID")
    required = {"manifest", "contract", "plan", "policy", "registry", "case_set", "ci_eligible"}
    if not required.issubset(bound_run):
        raise _bad("BINDING_INPUT_MISSING")
    try:
        rebound = cached_bind_run_manifest(
            bound_run["manifest"], bound_run["contract"], bound_run["plan"],
            bound_run["policy"], bound_run["registry"], bound_run["case_set"],
            baseline_context=bound_run.get("baseline_context"),
        )
    except ContractError:
        raise
    except (KeyError, TypeError, ValueError, RecursionError):
        raise _bad("BINDING_INPUT_INVALID") from None
    if bound_run.get("ci_eligible") is not False:
        raise _bad("CI_INELIGIBLE")
    fields = ("manifest", "contract", "plan", "policy", "registry", "case_set", "selected_controls", "ci_eligible")
    if any(field not in bound_run or bound_run[field] != rebound[field] for field in fields):
        raise _bad("BINDING_MISMATCH")
    # cached_bind_run_manifest returns a fresh JSON decode, so this projection
    # is isolated from bound_run without recursively copying each validated tree.
    return {field: rebound[field] for field in fields}


def _documents(policy: Any, worker_digest: str, lock: dict, profile: dict, now: int, run_id: str,
               policy_generation: int) -> dict:
    try:
        policy_value = validate_policy_profile(policy)
    except Exception:
        raise _bad("INVALID_POLICY") from None
    evaluator = _evaluator_ref(worker_digest)
    controls, cases, entries, materials = [], [], [], []
    for scenario in _EXPECTED:
        kind, code, _ = scenario.split(":")
        control_id, obligation_id = "fixture-" + code, "fixture-obligation-" + code
        case_id, trial_id, stage_id = "fixture-case-" + code, "fixture-trial-" + code, "fixture-stage-" + code
        target = _target_ref(worker_digest, scenario)
        input_payload, oracle_payload, initial_payload = _payloads(worker_digest, scenario)
        input_ref = _ref("input", case_id, input_payload)
        oracle_ref = _ref("oracle", case_id, oracle_payload)
        initial_ref = _ref("initial_state", case_id, initial_payload)
        controls.append({"control_id": control_id, "owner": "fixture-worker", "invariant": "固定workerの" + code + "境界",
                         "criticality": "noncritical", "target_ref": target, "dependencies": [],
                         "obligations": [{"obligation_id": obligation_id, "kind": kind, "required": True,
                                          "event_policy": "none", "evaluator_ref": evaluator}],
                         "mutation_applicability": {"status": "applicable" if kind == "mutation" else "not_applicable",
                                                     "reason": None if kind == "mutation" else "固定constraintのため対象外"}})
        category = "fixture-constraint" if kind == "constraint" else "fixture-mutation"
        cases.append({"case_id": case_id, "lineage_group": "fixture-lineage-" + code, "category": category,
                      "expected_label": "positive", "oracle_ref": oracle_ref, "initial_state_ref": initial_ref,
                      "session_steps": [{"stage_id": stage_id, "input_ref": input_ref,
                                         "expected_detection": "detect", "event_policy": "none"}],
                      "scored_stage_id": stage_id})
        entries.append({"obligation_id": obligation_id, "case_id": case_id, "trial_id": trial_id,
                        "variant": "candidate", "stage_ids": [stage_id], "required": True,
                        "event_policy": "none", "evaluator_ref": evaluator, "target_ref": target})
        materials.append({"scenario": scenario, "control_id": control_id, "obligation_id": obligation_id,
                          "case_id": case_id, "trial_id": trial_id, "stage_id": stage_id,
                          "input_payload": input_payload, "oracle_payload": oracle_payload,
                          "initial_state_payload": initial_payload, "expected": deepcopy(_EXPECTED[scenario])})
    registry = {"schema_version": 1, "kind": "control_registry", "registry_id": "fixture-registry-" + run_id,
                "controls": controls}
    case_set = {"schema_version": 1, "kind": "case_set", "case_set_id": "fixture-cases-" + run_id,
                "purpose": "acceptance", "required_categories": ["fixture-constraint", "fixture-mutation"], "cases": cases}
    calibration_materials = _calibration_materials(worker_digest)
    calibration_cases = []
    for material in calibration_materials:
        calibration_cases.append({
            "case_id": material["case_id"], "lineage_group": "fixture-calibration-lineage",
            "category": "fixture-calibration", "expected_label": "indeterminate",
            "oracle_ref": _ref("oracle", material["case_id"], material["oracle_payload"]),
            "initial_state_ref": _ref("initial_state", material["case_id"], material["initial_state_payload"]),
            "session_steps": [{"stage_id": "fixture-calibration-stage-" + material["vector_id"],
                               "input_ref": _ref("input", material["case_id"], material["input_payload"]),
                               "expected_detection": "indeterminate", "event_policy": "none"}],
            "scored_stage_id": "fixture-calibration-stage-" + material["vector_id"],
        })
    calibration = {"schema_version": 1, "kind": "case_set", "case_set_id": "fixture-calibration-set-" + run_id,
                   "purpose": "calibration", "required_categories": ["fixture-calibration"], "cases": calibration_cases}
    contract = {"schema_version": 1, "kind": "evaluation_contract", "contract_id": "fixture-contract-" + run_id,
                "generation": 1, "policy_series_id": policy_value["policy_id"], "policy_generation": policy_generation,
                "policy_ref": _ref("policy_profile", policy_value["policy_id"], policy_value),
                "registry_ref": _ref("control_registry", registry["registry_id"], registry),
                "case_set_ref": _ref("case_set", case_set["case_set_id"], case_set),
                "calibration_case_set_ref": _ref("case_set", calibration["case_set_id"], calibration),
                "evaluator_refs": [evaluator], "required_categories": case_set["required_categories"],
                "use_cases": ["UC-CI"], "comparison": {"mode": "not_applicable", "baseline_ref": None,
                "changed_axes": ["target"], "reason": "initial_baseline_pending"},
                "required_outputs": ["decision", "evidence", "findings", "plans", "run_receipt"]}
    plan = {"schema_version": 1, "kind": "trial_plan", "plan_id": "fixture-plan-" + run_id,
            "contract_ref": _ref("evaluation_contract", contract["contract_id"], contract), "entries": entries}
    manifest = {"schema_version": 1, "kind": "run_manifest", "run_id": run_id,
                "contract_ref": _ref("evaluation_contract", contract["contract_id"], contract), "purpose": "baseline_candidate",
                "use_cases": ["UC-CI"], "target_refs": [control["target_ref"] for control in controls],
                "control_ids": [control["control_id"] for control in controls], "baseline_ref": None,
                "plan_ref": _ref("trial_plan", plan["plan_id"], plan), "policy_ref": contract["policy_ref"],
                "profile": "full", "environment_ref": _fixed_environment_ref(),
                "actor_context_ref": _ref("actor_context", "fixture-operator-" + run_id,
                                            {"role": "operator", "purpose": "fixture-materialization"}),
                "created_at": now, "deadline": now + policy_value["profiles"]["full"]["elapsed_seconds"]}
    validate_registry(registry); validate_case_set(case_set); validate_case_set(calibration)
    validate_evaluation_contract(contract); validate_trial_plan(plan); validate_run_manifest(manifest)
    return {"policy": policy_value, "registry": registry, "case_set": case_set, "calibration": calibration,
            "contract": contract, "plan": plan, "manifest": manifest, "materials": materials,
            "calibration_materials": calibration_materials}


def build_fixture_pack(policy: Any, worker_source: bytes, runtime_lock: dict[str, Any],
                       execution_profile: dict[str, str], now: int, run_id: str, *,
                       policy_generation: int = 1) -> dict[str, Any]:
    """固定良好baseline packを構築し、15 entryの実体payloadを保持する。"""
    _id(run_id); now = _time(now)
    if type(policy_generation) is not int or not 1 <= policy_generation <= MAX_INTEGER:
        raise _bad("INVALID_POLICY_GENERATION")
    if type(worker_source) is not bytes or len(worker_source) > 1_048_576:
        raise _bad("INVALID_WORKER_SOURCE")
    lock, profile = _validate_lock(runtime_lock), _validate_profile(execution_profile)
    digest = hashlib.sha256(worker_source).hexdigest()
    if lock["worker_digest"] != digest or profile["fixture_digest"] != digest:
        raise _bad("WORKER_DIGEST_MISMATCH")
    docs = _documents(policy, digest, lock, profile, now, run_id, policy_generation)
    bound = bind_run_manifest(docs["manifest"], docs["contract"], docs["plan"], docs["policy"], docs["registry"], docs["case_set"])
    pack = {"schema_version": 1, "kind": "fixture_pack", "pack_id": "fixture-pack-" + run_id,
            "run_id": run_id, "worker_digest": digest, "runtime_lock": lock,
            "execution_profile": profile, "materials": docs["materials"],
            "calibration_materials": docs["calibration_materials"]}
    return {"pack": deepcopy(pack), "pack_ref": _ref("fixture_pack", pack["pack_id"], pack),
            "bound_run": {**bound, "fixture_pack": deepcopy(pack),
                          "calibration_case_set": deepcopy(docs["calibration"])},
            "calibration_case_set": deepcopy(docs["calibration"]), "worker_source_digest": digest,
            "ci_eligible": False, "authority_connected": False}


def _validate_material(value: Any) -> dict[str, Any]:
    try:
        require_object(value, _MATERIAL_FIELDS)
    except ContractError:
        raise _bad("INVALID_MATERIAL") from None
    for field in ("scenario", "control_id", "obligation_id", "case_id", "trial_id", "stage_id"):
        _id(value[field])
    if value["scenario"] not in _EXPECTED or type(value["expected"]) is not dict:
        raise _bad("INVALID_MATERIAL")
    return deepcopy(value)


def _record(material: dict, worker_digest: str, case: dict, entry: dict) -> dict:
    scenario = material["scenario"]
    if material["expected"] != _EXPECTED[scenario]:
        raise _bad("INVALID_EXPECTATION")
    input_ref = _ref("input", material["case_id"], material["input_payload"])
    oracle_ref = _ref("oracle", material["case_id"], material["oracle_payload"])
    initial_ref = _ref("initial_state", material["case_id"], material["initial_state_payload"])
    if case["oracle_ref"] != oracle_ref or case["initial_state_ref"] != initial_ref:
        raise _bad("CASE_PAYLOAD_MISMATCH")
    expected_stage = {"stage_id": material["stage_id"], "input_ref": input_ref,
                      "expected_detection": "detect", "event_policy": "none"}
    if case["session_steps"] != [expected_stage] or case["scored_stage_id"] != material["stage_id"]:
        raise _bad("STAGE_MISMATCH")
    if entry["stage_ids"] != [material["stage_id"]] or entry["variant"] != "candidate":
        raise _bad("PLAN_MISMATCH")
    return {"schema_version": 1, "kind": "fixture_scenario", "scenario": scenario,
            "input_ref": input_ref, "oracle_ref": oracle_ref, "control_id": material["control_id"],
            "obligation_id": material["obligation_id"], "case_id": material["case_id"],
            "trial_id": material["trial_id"], "stage_id": material["stage_id"], "variant": "candidate",
            "expected": deepcopy(_EXPECTED[scenario])}


def _check_calibration(bound_run: dict, pack: dict, digest: str) -> None:
    calibration = bound_run.get("calibration_case_set")
    if type(calibration) is not dict:
        raise _bad("CALIBRATION_MISSING")
    try:
        validate_case_set(calibration)
    except Exception:
        raise _bad("CALIBRATION_INVALID") from None
    if bound_run["contract"]["calibration_case_set_ref"] != _ref("case_set", calibration["case_set_id"], calibration):
        raise _bad("CALIBRATION_REFERENCE_MISMATCH")
    materials = pack["calibration_materials"]
    if type(materials) is not list or len(materials) != 36:
        raise _bad("CALIBRATION_COVERAGE_MISSING")
    if materials != _calibration_materials(digest):
        raise _bad("CALIBRATION_PAYLOAD_MISMATCH")
    cases = {case["case_id"]: case for case in calibration.get("cases", [])}
    if len(cases) != 36:
        raise _bad("CALIBRATION_COVERAGE_MISSING")
    seen: set[str] = set()
    for material in materials:
        if (type(material) is not dict
                or set(material) != {"vector_id", "case_id", "input_payload", "oracle_payload", "initial_state_payload"}):
            raise _bad("INVALID_CALIBRATION_MATERIAL")
        case_id = material["case_id"]
        _id(case_id)
        if case_id in seen or case_id not in cases:
            raise _bad("CALIBRATION_REFERENCE_MISMATCH")
        seen.add(case_id)
        case = cases[case_id]
        input_ref = _ref("input", case_id, material["input_payload"])
        oracle_ref = _ref("oracle", case_id, material["oracle_payload"])
        initial_ref = _ref("initial_state", case_id, material["initial_state_payload"])
        if (case["oracle_ref"] != oracle_ref or case["initial_state_ref"] != initial_ref
                or case["session_steps"] != [{"stage_id": "fixture-calibration-stage-" + material["vector_id"],
                    "input_ref": input_ref, "expected_detection": "indeterminate", "event_policy": "none"}]
                or case["expected_label"] != "indeterminate"):
            raise _bad("CALIBRATION_REFERENCE_MISMATCH")
        for payload in (material["input_payload"], material["oracle_payload"], material["initial_state_payload"]):
            if payload.get("scenario") != "calibration" or payload.get("worker_digest") != digest:
                raise _bad("CALIBRATION_PAYLOAD_MISMATCH")
    if seen != set(cases):
        raise _bad("CALIBRATION_COVERAGE_MISSING")


def _records(bound: dict, pack: dict, digest: str) -> list[dict]:
    if set(pack) != _PACK_FIELDS or pack["schema_version"] != 1 or pack["kind"] != "fixture_pack":
        raise _bad("INVALID_PACK")
    _id(pack["pack_id"])
    if pack["run_id"] != bound["manifest"]["run_id"] or pack["worker_digest"] != digest:
        raise _bad("PACK_BINDING_MISMATCH")
    lock, profile = _validate_lock(pack["runtime_lock"]), _validate_profile(pack["execution_profile"])
    if lock["worker_digest"] != digest or profile["fixture_digest"] != digest:
        raise _bad("PACK_RUNTIME_MISMATCH")
    materials = pack["materials"]
    if type(materials) is not list or len(materials) != len(_EXPECTED):
        raise _bad("PACK_COVERAGE_MISSING")
    controls = {v["control_id"]: v for v in bound["registry"]["controls"]}
    obligations = {ob["obligation_id"]: (control, ob) for control in controls.values() for ob in control["obligations"]}
    cases = {v["case_id"]: v for v in bound["case_set"]["cases"]}
    entries = bound["plan"]["entries"]
    if len(entries) != len(_EXPECTED) or any(v["variant"] != "candidate" for v in entries):
        raise _bad("PLAN_NOT_ONE_TO_ONE")
    by_key = {(v["obligation_id"], v["case_id"], v["trial_id"]): v for v in entries}
    seen: set[str] = set(); result = []
    for raw in materials:
        m = _validate_material(raw); scenario = m["scenario"]
        if scenario in seen:
            raise _bad("SCENARIO_MISMATCH")
        seen.add(scenario)
        _, code, _ = scenario.split(":")
        if m["control_id"] != "fixture-" + code:
            raise _bad("CONTROL_MISMATCH")
        control = controls.get(m["control_id"]); pair = obligations.get(m["obligation_id"]); case = cases.get(m["case_id"])
        entry = by_key.get((m["obligation_id"], m["case_id"], m["trial_id"]))
        if control is None or pair is None or case is None or entry is None or pair[0] != control:
            raise _bad("REFERENCE_MISMATCH")
        target = _target_ref(digest, scenario); evaluator = _evaluator_ref(digest)
        if control["target_ref"] != target or entry["target_ref"] != target:
            raise _bad("TARGET_MISMATCH")
        if pair[1]["evaluator_ref"] != evaluator or entry["evaluator_ref"] != evaluator:
            raise _bad("EVALUATOR_MISMATCH")
        expected_input, expected_oracle, expected_initial = _payloads(digest, scenario)
        if (m["input_payload"] != expected_input or m["oracle_payload"] != expected_oracle
                or m["initial_state_payload"] != expected_initial):
            raise _bad("PAYLOAD_MISMATCH")
        result.append(_record(m, digest, case, entry))
    if seen != set(_EXPECTED):
        raise _bad("PACK_COVERAGE_MISSING")
    return result


def _build_manifest(bound_run: Any, worker_source: bytes, runtime_lock: Any, profile: Any, *, created_at: int) -> tuple[dict, dict]:
    if type(worker_source) is not bytes or len(worker_source) > 1_048_576:
        raise _bad("INVALID_WORKER_SOURCE")
    lock, profile_value = _validate_lock(runtime_lock), _validate_profile(profile)
    digest = hashlib.sha256(worker_source).hexdigest()
    if lock["worker_digest"] != digest or profile_value["fixture_digest"] != digest:
        raise _bad("WORKER_DIGEST_MISMATCH")
    created_at = _time(created_at)
    bound = _core_bound(bound_run); manifest = bound["manifest"]
    if manifest.get("purpose") != "baseline_candidate" or "UC-CI" not in manifest.get("use_cases", []):
        raise _bad("PURPOSE_MISMATCH")
    pack = bound_run.get("fixture_pack")
    if type(pack) is not dict:
        raise _bad("PACK_MISSING")
    if set(pack) != _PACK_FIELDS:
        raise _bad("INVALID_PACK")
    if pack["runtime_lock"] != lock or pack["execution_profile"] != profile_value:
        raise _bad("PACK_RUNTIME_MISMATCH")
    env = _fixed_environment_ref()
    if manifest["environment_ref"] != env:
        raise _bad("ENVIRONMENT_MISMATCH")
    _check_calibration(bound_run, pack, digest)
    result = {"schema_version": 1, "kind": "fixture_materialization",
              "materialization_id": "materialization-" + manifest["run_id"], "run_id": manifest["run_id"],
              "worker_ref": _ref("fixture_worker", "fixture_worker", {"sha256": digest}),
              "image_ref": _ref("fixture_image", lock["image_id"], lock), "profile": profile_value,
              "source_digest": digest, "records": _records(bound, pack, digest),
              "probes": [{"schema_version": 1, "kind": "fixture_probe", "scenario": scenario,
                          "expected_status": status, "expected_reason": reason}
                         for scenario, (status, reason) in _PROBES.items()],
              "created_at": created_at, "ci_eligible": False}
    return result, _ref("fixture_pack", pack["pack_id"], pack)


def materialize_fixture_manifest(*, bound_run: Any, worker_source: bytes,
                                 runtime_lock: dict[str, Any], execution_profile: dict[str, str], now: int) -> dict[str, Any]:
    manifest, pack_ref = _build_manifest(bound_run, worker_source, runtime_lock, execution_profile, created_at=now)
    return {"manifest": manifest, "manifest_ref": _ref("fixture_manifest", manifest["materialization_id"], manifest),
            "pack_ref": pack_ref, "structurally_bound": True, "authority_connected": False, "ci_eligible": False}


def validate_fixture_manifest(value: Any, *, bound_run: Any, worker_source: bytes,
                              runtime_lock: dict[str, Any], execution_profile: dict[str, str], now: int) -> dict[str, Any]:
    try:
        require_object(value, _MANIFEST_FIELDS)
    except ContractError:
        raise _bad("UNKNOWN_FIELD") from None
    current = _time(now)
    if type(value["created_at"]) is not int or value["created_at"] > current:
        raise _bad("INVALID_TIME")
    expected, pack_ref = _build_manifest(bound_run, worker_source, runtime_lock, execution_profile,
                                         created_at=value["created_at"])
    if value != expected:
        raise _bad("MANIFEST_MISMATCH")
    if len(value["records"]) != len(_EXPECTED) or len(value["probes"]) != len(_PROBES):
        raise _bad("COVERAGE_MISMATCH")
    for record in value["records"]:
        try:
            require_object(record, _RECORD_FIELDS)
        except ContractError:
            raise _bad("UNKNOWN_FIELD") from None
        if record["scenario"] not in _EXPECTED or record["expected"] != _EXPECTED[record["scenario"]]:
            raise _bad("INVALID_RECORD")
    for probe in value["probes"]:
        try:
            require_object(probe, _PROBE_FIELDS)
        except ContractError:
            raise _bad("UNKNOWN_FIELD") from None
        if probe["scenario"] not in _PROBES or (probe["expected_status"], probe["expected_reason"]) != _PROBES[probe["scenario"]]:
            raise _bad("INVALID_PROBE")
    return {"manifest": deepcopy(value), "manifest_ref": _ref("fixture_manifest", value["materialization_id"], value),
            "pack_ref": pack_ref, "structurally_bound": True, "authority_connected": False, "ci_eligible": False}


__all__ = ["build_fixture_pack", "materialize_fixture_manifest", "validate_fixture_manifest"]
