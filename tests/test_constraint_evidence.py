"""制約専用runの集計・決定と、他family混在時のfail-closed境界を検査する。"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from gah.decision import assess
from gah.run_contracts import bind_run_manifest, content_ref
from gah.run_evidence import RunEvidenceStore, bound_bundle_digest
from test_run_contracts import _fixtures, _manifest, _plan


ZERO = "0" * 64
ADAPTER = "1" * 64
ISOLATION = "2" * 64


class Clock:
    def __init__(self, value: int = 100):
        self.value = value

    def __call__(self) -> int:
        return self.value


def constraint_fixture(*, critical: bool = False):
    """元fixtureのregistry/contract/planを制約一義へ固定して再bindする。"""
    fixtures = _fixtures()
    control = deepcopy(fixtures["registry"]["controls"][0])
    control["criticality"] = "critical" if critical else "noncritical"
    control["obligations"] = [deepcopy(control["obligations"][0])]
    control["mutation_applicability"] = {
        "status": "not_applicable", "reason": "constraint-only boundary"
    }
    fixtures["registry"]["controls"] = [control]
    fixtures["contract"]["registry_ref"] = content_ref(
        "control_registry", fixtures["registry"]["registry_id"], fixtures["registry"]
    )
    fixtures["contract"]["evaluator_refs"] = [
        deepcopy(control["obligations"][0]["evaluator_ref"])
    ]
    fixtures["contract"]["use_cases"] = ["UC-CI"]
    # 現行EvaluationContractの構造検査は初回でも1軸を要求するため、
    # constraint専用化とは独立した既存のtarget軸を維持する。
    fixtures["contract"]["comparison"]["changed_axes"] = ["target"]

    plan = _plan(fixtures)
    plan["entries"] = [plan["entries"][0]]
    plan["contract_ref"] = content_ref(
        "evaluation_contract", fixtures["contract"]["contract_id"], fixtures["contract"]
    )
    manifest = _manifest(fixtures, plan)
    manifest["use_cases"] = ["UC-CI"]
    manifest["control_ids"] = ["control-ci"]
    bound = bind_run_manifest(
        manifest, fixtures["contract"], plan, fixtures["policy"],
        fixtures["registry"], fixtures["case_set"],
    )
    fixtures["contract_ref_digest"] = bound["manifest"]["contract_ref"]["digest"]
    fixtures["policy_ref_digest"] = bound["manifest"]["policy_ref"]["digest"]
    return fixtures, plan, bound


def constraint_attempt(fixtures, plan, *, observation: str = "PASS",
                       attempt_id: str = "attempt-constraint") -> dict:
    entry = plan["entries"][0]
    binding = {
        "run_id": "run-1", "operation_id": "operation-" + attempt_id,
        "owner_epoch": 1, "contract_digest": fixtures["contract_ref_digest"],
        "target_digest": entry["target_ref"]["digest"],
        "obligation_id": entry["obligation_id"], "case_id": entry["case_id"],
        "trial_id": entry["trial_id"], "stage_id": entry["stage_ids"][0],
        "fixture_digest": ZERO, "adapter_digest": ADAPTER,
        "policy_digest": fixtures["policy_ref_digest"],
        "evaluator_digest": entry["evaluator_ref"]["digest"],
        "isolation_digest": ISOLATION,
    }
    result = {
        "schema_version": 1, "kind": "normalized_result", "binding": deepcopy(binding),
        "mode": "constraint", "observation": observation,
        "mutation_outcome": None, "detection": None, "deviation": None,
        "error_class": None, "raw_digest": "a" * 64,
    }
    return {
        "schema_version": 1, "kind": "attempt_record", "attempt_id": attempt_id,
        "variant": "candidate", "retry_of": None, "started_at": 100,
        "finished_at": 100, "stop_confirmed": True, "execution_status": "COMPLETED",
        "state_restored": True, "expected_binding": binding, "result": result,
    }


class ConstraintEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.clock = Clock()
        self.path = Path(self.temp.name) / "constraint.sqlite"
        self.fixtures, self.plan, self.bound = constraint_fixture()
        self.profile = {
            "fixture_digest": ZERO, "adapter_digests": [ADAPTER],
            "isolation_digest": ISOLATION,
        }

    def open(self, bound=None):
        bound = self.bound if bound is None else bound
        return RunEvidenceStore(
            self.path, clock=self.clock,
            allowed_bindings={"run-1": bound_bundle_digest(bound)},
        )

    def start(self, store):
        return store.start_run(self.bound, self.profile)

    def finish_with(self, observation="PASS", *, critical=False):
        if critical:
            self.fixtures, self.plan, self.bound = constraint_fixture(critical=True)
        with self.open() as store:
            started = self.start(store)
            accepted = store.record_attempt(
                constraint_attempt(self.fixtures, self.plan, observation=observation)
            )
            self.assertTrue(accepted["accepted"])
            terminal = store.finalize("run-1")
            return started, terminal

    def test_constraint_pass_finalizes_healthy(self):
        _, terminal = self.finish_with("PASS")
        self.assertEqual(terminal["decision"]["assurance"], "HEALTHY")
        self.assertEqual(terminal["decision"]["reasons"], [])
        self.assertFalse(terminal["ci_eligible"])

    def test_noncritical_constraint_fail_is_degraded(self):
        _, terminal = self.finish_with("FAIL")
        decision = terminal["decision"]
        self.assertEqual(decision["assurance"], "DEGRADED")
        self.assertIn("constraint_violation", {item["code"] for item in decision["reasons"]})

    def test_critical_constraint_fail_is_hold(self):
        _, terminal = self.finish_with("FAIL", critical=True)
        decision = terminal["decision"]
        self.assertEqual(decision["assurance"], "HOLD")
        self.assertIn("constraint_violation", {item["code"] for item in decision["reasons"]})

    def test_missing_and_unknown_constraint_are_unknown(self):
        with self.open() as store:
            self.start(store)
            missing = store.finalize("run-1")
            self.assertEqual(missing["decision"]["assurance"], "UNKNOWN")
            self.assertIn("required_missing", {item["code"] for item in missing["decision"]["reasons"]})

        self.path.unlink()
        with self.open() as store:
            self.start(store)
            store.record_attempt(constraint_attempt(
                self.fixtures, self.plan, observation="UNKNOWN", attempt_id="attempt-unknown"
            ))
            terminal = store.finalize("run-1")
            self.assertEqual(terminal["decision"]["assurance"], "UNKNOWN")

    def test_constraint_failure_cannot_be_hidden_by_other_family_metrics(self):
        """制約FAILをmutation/LLMの率で相殺せず、全体を成功扱いしない。"""
        from test_run_evidence import attempt_for, bound_fixture

        fixtures, plan, bound = bound_fixture()
        fixtures["contract_ref_digest"] = bound["manifest"]["contract_ref"]["digest"]
        fixtures["policy_ref_digest"] = bound["manifest"]["policy_ref"]["digest"]
        profile = {"fixture_digest": ZERO, "adapter_digests": [ADAPTER],
                   "isolation_digest": ISOLATION}
        with RunEvidenceStore(
            self.path, clock=self.clock,
            allowed_bindings={"run-1": bound_bundle_digest(bound)},
        ) as store:
            store.start_run(bound, profile)
            constraint = attempt_for(
                fixtures, plan, "obligation-constraint",
                attempt_id="mixed-constraint", mode="constraint",
            )
            constraint["result"]["observation"] = "FAIL"
            mutation = attempt_for(
                fixtures, plan, "obligation-mutation",
                attempt_id="mixed-mutation", mode="mutation",
            )
            llm = attempt_for(
                fixtures, plan, "obligation-llm",
                attempt_id="mixed-llm", mode="llm",
            )
            for attempt in (constraint, mutation, llm):
                self.assertTrue(store.record_attempt(attempt)["accepted"])
            terminal = store.finalize("run-1")
            decision = terminal["decision"]
            self.assertNotEqual(decision["assurance"], "HEALTHY")
            self.assertIn("constraint_violation", {item["code"] for item in decision["reasons"]})

    def test_public_diagnostic_assess_still_rejects_empty_metrics(self):
        request = {
            "schema_version": 1, "request_id": "diagnostic-empty",
            "target_digest": ZERO, "contract_digest": ZERO,
            "purpose": "component_validation", "observed_at": 100,
            "assessed_at": 100, "metrics": [],
            "required_missing": False, "critical_missing": False,
            "integrity_failure": False, "forbidden_violation": False,
            "critical_violation": False, "warning": False,
        }
        with self.assertRaises(ValueError):
            assess(request)


if __name__ == "__main__":
    unittest.main()
