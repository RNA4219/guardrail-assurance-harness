"""Finding/Plan の決定性、境界、再検証ライフサイクルを検査する。"""

from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gah.contracts import ContractError
from gah.remediation import (
    generate_findings,
    generate_plan,
    finding_ref,
    plan_to_yaml,
    record_recurrence,
    transition_finding,
    validate_finding,
)
from gah.run_contracts import content_ref


ZERO = "0" * 64


def _ref(kind: str, identifier: str, value: object) -> dict[str, str]:
    return content_ref(kind, identifier, value)


def _assessment(*, reasons: list[dict] | None = None) -> dict:
    return {
        "schema_version": 1,
        "request_id": "req-1",
        "request_digest": ZERO,
        "target_digest": "1" * 64,
        "contract_digest": "2" * 64,
        "assessed_at": 100,
        "purpose": "component_validation",
        "assurance": "DEGRADED",
        "ci_eligible": False,
        "metrics": [{
            "metric_id": "metric-1", "name": "recall", "value": [1, 2],
            "baseline_value": None, "absolute_pass": False, "delta_pass": None,
        }],
        "reasons": reasons if reasons is not None else [{
            "code": "metric_absolute_threshold", "state": "DEGRADED",
            "metric_id": "metric-1",
        }],
    }


def _context(*, candidates: list[dict] | None = None) -> dict:
    return {
        "control_ref": _ref("control", "control-1", {"critical": True}),
        "observation_ref": _ref("observation", "observation-1", {"value": "bad"}),
        "comparison_ref": _ref("comparison", "comparison-1", {"mode": "absolute"}),
        "evidence_refs": [_ref("evidence", "evidence-1", {"observed": True})],
        "recovery_target_ref": None,
        "cause_candidates": [] if candidates is None else candidates,
    }


def _actor() -> dict[str, str]:
    return _ref("actor_context", "verifier-1", {"role": "operator"})


def _conditions() -> dict:
    return {
        "policy_ref": _ref("policy_profile", "policy-1", {"v": 1}),
        "contract_ref": _ref("evaluation_contract", "contract-1", {"v": 1}),
        "registry_ref": _ref("control_registry", "registry-1", {"v": 1}),
        "case_set_ref": _ref("case_set", "case-set-1", {"v": 1}),
        "oracle_refs": [_ref("oracle", "oracle-1", {"v": 1})],
        "evaluator_refs": [_ref("evaluator", "evaluator-1", {"v": 1})],
        "plan_ref": _ref("trial_plan", "trial-plan-1", {"v": 1}),
        "repeat_config_ref": _ref("repeat_config", "repeat-1", {"v": 1}),
    }


def _fresh_evidence(identifier: str = "evidence-2") -> dict:
    return {
        "evidence_ref": _ref("evidence", identifier, {"observed": "fresh"}),
        "subject_ref": _ref("control", "control-1", {"critical": True}),
        "conditions_ref": _ref("conditions", "conditions-1", _conditions()),
        "producer_ref": _actor(),
        "observed_at": 103,
        "valid_until": 200,
        "revoked": False,
        "missing": False,
    }


class RemediationTests(unittest.TestCase):
    def test_findings_are_deterministic_and_unknown_cause_is_preserved(self):
        first = generate_findings(_assessment(), context=_context())
        second = generate_findings(_assessment(), context=_context())
        self.assertEqual(first, second)
        self.assertEqual(first[0]["status"], "OPEN")
        self.assertEqual(first[0]["cause_class"], "UNKNOWN")
        self.assertEqual(first[0]["cause_candidates"][0]["confidence"], "UNKNOWN")
        self.assertFalse(first[0]["ci_eligible"])

    def test_reasonless_assessment_has_no_finding(self):
        self.assertEqual(generate_findings(_assessment(reasons=[]), context=_context()), [])

    def _plan(self, finding, **overrides):
        evidence = finding["evidence_refs"][0]
        values = {
            "change_targets": [_ref("target", "target-1", {"version": 2})],
            "purpose": "Restore the original check",
            "preserve_conditions": [{
                "condition_id": "same-threshold",
                "description": "Keep the accepted threshold unchanged",
                "required": True,
            }],
            "revalidation": {
                "same_binding": True,
                "same_conditions": True,
                "threshold_unchanged": True,
                "inspection_preserved": True,
                "required_evidence_refs": [evidence],
                "verifier_ref": _actor(),
                "steps": [{"step_id": "rerun", "description": "Run the same checks"}],
            },
            "rollout": {
                "steps": [{"step_id": "review", "description": "Review the change"}],
                "requires_authorization": True,
            },
            "rollback_target_ref": None,
            "authority_requirements": [{
                "role": "operator", "action": "approve", "reason": "Independent review",
            }],
            "missing_information": [],
            "created_at": 101,
        }
        values.update(overrides)
        return generate_plan(finding, **values)

    def test_plan_is_structured_non_executing_yaml_and_completeness_is_separate(self):
        finding = generate_findings(_assessment(), context=_context())[0]
        plan = self._plan(finding)
        self.assertEqual(json.loads(plan_to_yaml(plan)), plan)
        self.assertEqual(plan["execution_status"], "NOT_EXECUTED")
        self.assertEqual(plan["plan_status"], "NEEDS_INFORMATION")
        self.assertIn("復旧先が不明", plan["missing_information"])
        self.assertFalse(plan["ci_eligible"])
        complete = self._plan(
            finding, rollback_target_ref=_ref("target", "target-1", {"version": 2})
        )
        self.assertEqual(complete["plan_status"], "READY")
        incomplete = self._plan(
            finding,
            rollback_target_ref=_ref("target", "target-1", {"version": 2}),
            missing_information=["Need the recovery owner"],
        )
        self.assertEqual(incomplete["plan_status"], "NEEDS_INFORMATION")
        self.assertEqual(incomplete["execution_status"], "NOT_EXECUTED")

    def test_plan_has_deterministic_fallback_skeleton_without_caller_steps(self):
        finding = generate_findings(_assessment(), context=_context())[0]
        kwargs = {
            "change_targets": [_ref("target", "target-1", {"version": 2})],
            "purpose": "Restore the original check",
            "preserve_conditions": None, "revalidation": None, "rollout": None,
        }
        first = generate_plan(finding, **kwargs)
        second = generate_plan(finding, **kwargs)
        self.assertEqual(first, second)
        self.assertEqual(first["plan_status"], "NEEDS_INFORMATION")
        self.assertTrue(first["revalidation"]["steps"])
        self.assertEqual(first["execution_status"], "NOT_EXECUTED")
        changed = generate_plan(
            finding, **kwargs, missing_information=["追加確認が必要"]
        )
        self.assertNotEqual(first["plan_id"], changed["plan_id"])
        changed_time = generate_plan(finding, **kwargs, created_at=101)
        self.assertNotEqual(first["plan_id"], changed_time["plan_id"])

    def test_plan_rejects_changed_conditions_and_unchecked_candidate_evidence(self):
        finding = generate_findings(_assessment(), context=_context())[0]
        bad = self._plan(finding)
        bad["revalidation"]["threshold_unchanged"] = False
        with self.assertRaises(ContractError):
            plan_to_yaml(bad)
        bad_candidate = copy.deepcopy(finding)
        bad_candidate["cause_candidates"] = [{
            "candidate_id": "cause-1", "kind": "checker", "summary": "A checker issue",
            "confidence": "SUPPORTED", "evidence_refs": [
                _ref("evidence", "not-in-finding", {"x": 1})
            ],
        }]
        with self.assertRaises(ContractError):
            self._plan(bad_candidate)

    def test_lifecycle_requires_new_evidence_and_records_verifier(self):
        finding = generate_findings(_assessment(), context=_context())[0]
        started = transition_finding(finding, "start", now=101)
        waiting = transition_finding(started, "request_revalidation", now=102)
        verification = {
            "finding_ref": finding_ref(waiting),
            "changed_target_refs": [_ref("target", "target-1", {"version": 2})],
            "original_target_ref": _ref("target", "target-1", {"version": 1}),
            "control_ref": finding["control_ref"],
            "original_conditions": _conditions(),
            "new_conditions": _conditions(),
            "evidence": [_fresh_evidence()], "verifier_ref": _actor(), "checked_at": 103,
            "origin_binding_verified": False, "authority_connected": False,
        }
        candidate = transition_finding(waiting, "verify", verification=verification, now=103)
        self.assertEqual(candidate["status"], "AWAITING_REVALIDATION")
        self.assertEqual(candidate["revalidation_candidate"]["evidence"], [_fresh_evidence()])
        self.assertIsNone(candidate["verifier_ref"])
        self.assertIsNone(candidate["authority_confirmation_ref"])
        self.assertFalse(candidate["revalidation_candidate"]["origin_binding_verified"])
        self.assertFalse(candidate["revalidation_candidate"]["authority_connected"])
        with self.assertRaises(ContractError):
            transition_finding(finding, "verify", verification=verification)

    def test_lifecycle_rejects_old_evidence_or_changed_condition(self):
        finding = generate_findings(_assessment(), context=_context())[0]
        waiting = transition_finding(
            transition_finding(finding, "start", now=101),
            "request_revalidation", now=102,
        )
        base = {
            "finding_ref": finding_ref(waiting),
            "changed_target_refs": [_ref("target", "target-1", {"version": 2})],
            "original_target_ref": _ref("target", "target-1", {"version": 1}),
            "control_ref": finding["control_ref"], "original_conditions": _conditions(),
            "new_conditions": _conditions(), "evidence": [],
            "verifier_ref": _actor(), "checked_at": 103,
            "origin_binding_verified": False, "authority_connected": False,
        }
        with self.assertRaises(ContractError):
            transition_finding(waiting, "verify", verification=base, now=103)
        base["evidence"] = [_fresh_evidence()]
        base["new_conditions"] = {**_conditions(), "policy_ref": _ref("policy_profile", "policy-2", {"v": 2})}
        with self.assertRaises(ContractError):
            transition_finding(waiting, "verify", verification=base, now=103)

    def test_strict_shape_and_verification_time_boundaries_are_rejected(self):
        finding = generate_findings(_assessment(), context=_context())[0]
        waiting = transition_finding(
            transition_finding(finding, "start", now=101),
            "request_revalidation", now=102,
        )
        verification = {
            "finding_ref": finding_ref(waiting),
            "changed_target_refs": [_ref("target", "target-1", {"version": 2})],
            "original_target_ref": _ref("target", "target-1", {"version": 1}),
            "control_ref": finding["control_ref"], "original_conditions": _conditions(),
            "new_conditions": _conditions(), "evidence": [_fresh_evidence()],
            "verifier_ref": _actor(), "checked_at": 101,
            "origin_binding_verified": False, "authority_connected": False,
        }
        with self.assertRaises(ContractError):
            transition_finding(waiting, "verify", verification=verification, now=103)
        bad_control = copy.deepcopy(verification)
        bad_control["control_ref"] = _ref("control", "control-2", {"critical": False})
        with self.assertRaises(ContractError):
            transition_finding(waiting, "verify", verification=bad_control, now=103)
        bad_evidence = copy.deepcopy(verification)
        bad_evidence["evidence"][0]["revoked"] = True
        with self.assertRaises(ContractError):
            transition_finding(waiting, "verify", verification=bad_evidence, now=103)
        expired = copy.deepcopy(verification)
        expired["evidence"][0]["valid_until"] = 102
        with self.assertRaises(ContractError):
            transition_finding(waiting, "verify", verification=expired, now=103)
        forged_ref = copy.deepcopy(verification)
        forged_ref["finding_ref"] = _ref("finding", waiting["finding_id"], waiting)
        with self.assertRaises(ContractError):
            transition_finding(waiting, "verify", verification=forged_ref, now=103)
        unsupported_claim = copy.deepcopy(verification)
        unsupported_claim["origin_binding_verified"] = True
        with self.assertRaises(ContractError):
            transition_finding(waiting, "verify", verification=unsupported_claim, now=103)
        plan = self._plan(finding, rollback_target_ref=_ref("target", "target-1", {"version": 2}))
        plan["unexpected"] = True
        with self.assertRaises(ContractError):
            plan_to_yaml(plan)
        with self.assertRaises(ContractError):
            self._plan(
                finding,
                rollback_target_ref=_ref("target", "target-1", {"version": 2}),
                authority_requirements="operator",
            )
        forged = copy.deepcopy(finding)
        forged["status"] = "VERIFIED"
        forged["verifier_ref"] = _actor()
        forged["verified_at"] = 101
        forged["authority_confirmation_ref"] = _ref(
            "authority_confirmation", "confirmation-1", {"accepted": True}
        )
        with self.assertRaises(ContractError):
            validate_finding(forged)
    def test_disposition_is_separate_and_recurrence_preserves_parent(self):
        finding = generate_findings(_assessment(), context=_context())[0]
        revised = transition_finding(finding, "revise_baseline", reason="New approved baseline", now=101)
        self.assertEqual(revised["disposition"], "BASELINE_REVISED")
        self.assertEqual(revised["disposition_reason"], "New approved baseline")
        self.assertEqual(revised["status"], "OPEN")
        with self.assertRaises(ContractError):
            transition_finding(revised, "verify", verification={})
        recurrence = record_recurrence(finding, context=_context(), now=101)
        self.assertEqual(recurrence["status"], "OPEN")
        self.assertEqual(recurrence["parent_finding_ref"], finding_ref(finding))
        self.assertNotEqual(recurrence["finding_id"], finding["finding_id"])
        with self.assertRaises(ContractError):
            record_recurrence(finding, context=_context(), now=99)


if __name__ == "__main__":
    unittest.main()
