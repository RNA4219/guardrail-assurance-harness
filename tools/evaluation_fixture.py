"""保存・輸送の試験専用。400参照は製品の実評価集合・性能根拠ではない。"""
from copy import deepcopy
from gah.run_contracts import content_ref


def case_set(purpose, count):
    cases = []
    for index in range(count):
        label = (("positive", "negative", "indeterminate")[index % 3] if purpose == "calibration"
                 else ("positive", "negative")[index % 2])
        detection = {"positive": "detect", "negative": "allow", "indeterminate": "indeterminate"}[label]
        def ref(kind):
            return content_ref(kind, f"{purpose}-{kind}-{index}", {"purpose": purpose, "fixture_slot": index, "kind": kind})
        cases.append({"case_id": f"{purpose}-{index}", "lineage_group": f"{purpose}-lineage-{index}",
            "category": "transport-fixture", "expected_label": label, "oracle_ref": ref("oracle"),
            "initial_state_ref": ref("initial_state"), "session_steps": [{"stage_id": "fixture-acceptance",
                "input_ref": ref("input"), "expected_detection": detection, "event_policy": "none"}],
            "scored_stage_id": "fixture-acceptance"})
    return {"schema_version": 1, "kind": "case_set", "case_set_id": f"transport-{purpose}", "purpose": purpose,
        "required_categories": ["transport-fixture"], "cases": cases}


def documents(policy, target, evaluator):
    registry = {"schema_version": 1, "kind": "control_registry", "registry_id": "transport-registry", "controls": [{
        "control_id": "fixed-control", "owner": "fixture-supervisor", "invariant": "固定fixtureの制約検査",
        "criticality": "noncritical", "target_ref": deepcopy(target), "dependencies": [], "obligations": [{
            "obligation_id": "fixed-obligation", "kind": "constraint", "required": True, "event_policy": "none",
            "evaluator_ref": deepcopy(evaluator)}], "mutation_applicability": {"status": "not_applicable", "reason": "輸送境界の単一制約fixture"}}]}
    acceptance, calibration = case_set("acceptance", 400), case_set("calibration", 3)
    contract = {"schema_version": 1, "kind": "evaluation_contract", "contract_id": "transport-contract", "generation": 1,
        "policy_series_id": policy["policy_id"], "policy_generation": 1,
        "policy_ref": content_ref("policy_profile", policy["policy_id"], policy),
        "registry_ref": content_ref("control_registry", registry["registry_id"], registry),
        "case_set_ref": content_ref("case_set", acceptance["case_set_id"], acceptance),
        "calibration_case_set_ref": content_ref("case_set", calibration["case_set_id"], calibration),
        "evaluator_refs": [deepcopy(evaluator)], "required_categories": ["transport-fixture"], "use_cases": ["UC-CI"],
        "comparison": {"mode": "not_applicable", "baseline_ref": None, "changed_axes": ["target"], "reason": "initial_baseline_pending"},
        "required_outputs": ["decision", "evidence", "findings", "plans", "run_receipt"]}
    plan = {"schema_version": 1, "kind": "trial_plan", "plan_id": "transport-plan",
        "contract_ref": content_ref("evaluation_contract", contract["contract_id"], contract), "entries": [{
            "obligation_id": "fixed-obligation", "case_id": "acceptance-0", "trial_id": "trial-1", "variant": "candidate",
            "stage_ids": ["fixture-acceptance"], "required": True, "event_policy": "none",
            "evaluator_ref": deepcopy(evaluator), "target_ref": deepcopy(target)}]}
    return registry, acceptance, calibration, contract, plan


def manifest(contract, plan, run_id, now, environment_ref):
    return {"schema_version": 1, "kind": "run_manifest", "run_id": run_id, "contract_ref": deepcopy(plan["contract_ref"]),
        "purpose": "baseline_candidate", "use_cases": ["UC-CI"], "target_refs": [deepcopy(plan["entries"][0]["target_ref"])],
        "control_ids": ["fixed-control"], "baseline_ref": None, "plan_ref": content_ref("trial_plan", plan["plan_id"], plan),
        "policy_ref": deepcopy(contract["policy_ref"]), "profile": "full", "environment_ref": deepcopy(environment_ref),
        "actor_context_ref": content_ref("actor_context", "operator-context", {"uid": 12004, "context": "operator-context"}),
        "created_at": now, "deadline": now + 1200}
