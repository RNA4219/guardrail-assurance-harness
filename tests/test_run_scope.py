"""変更影響の閉包と保存された限定runの通常実行を検査する。"""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gah.adoption import AdoptionError
from gah.contracts import ContractError
from gah import run_scope, regression_runs, baseline_generations
from gah.run_contracts import content_ref
from gah.resources import _packed


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


registry = module("scope_registry_helpers", "tests/test_registry.py")
supervisor = module("scope_supervisor_helpers", "tests/test_supervised_run.py")
seed = supervisor.seed
request = seed.request


class RunScopeTests(unittest.TestCase):
    def bound(self):
        controls = [registry._control(0, mutation=False), registry._control(1, "C00"),
                    registry._control(2, "C01"), registry._control(3)]
        value = {"schema_version": 1, "kind": "control_registry", "registry_id": "scope", "controls": controls}
        contract = {"contract_id": "scope-contract", "registry_ref": content_ref("control_registry", "scope", value),
                    "policy_ref": registry._ref("policy_profile", "policy")}
        return {"registry": value, "contract": contract,
                "manifest": {"contract_ref": content_ref("evaluation_contract", "scope-contract", contract)}}

    def test_reverse_dependencies_and_prerequisites_keep_critical_and_omit_independent(self):
        bound = self.bound()
        scope = run_scope.derive(bound, "scoped", [bound["registry"]["controls"][1]["target_ref"]])
        self.assertEqual(scope["selected_control_ids"], ["C00", "C01", "C02"])
        self.assertEqual(scope["unexecuted_control_ids"], ["C03"])
        self.assertEqual(scope["executed_scope"], "targeted")
        self.assertFalse(scope["ci_eligible"])

    def test_shared_evaluator_policy_and_unknown_reference_expand_scope(self):
        bound = self.bound()
        shared = bound["registry"]["controls"][1]["obligations"][0]["evaluator_ref"]
        bound["registry"]["controls"][3]["obligations"][0]["evaluator_ref"] = deepcopy(shared)
        for ref in [shared, bound["contract"]["policy_ref"], registry._ref("target", "unmapped")]:
            with self.subTest(ref=ref["id"]):
                scope = run_scope.derive(bound, "full", [ref])
                self.assertEqual(scope["executed_scope"], "full")
                self.assertEqual(scope["unexecuted_control_ids"], [])
        changed_digest = deepcopy(bound["registry"]["controls"][3]["target_ref"])
        changed_digest["digest"] = "f" * 64
        self.assertEqual(run_scope.derive(bound, "unknown", [changed_digest])["reason"], "UNKNOWN_IMPACT_FULL_FALLBACK")

    def test_invalid_duplicate_and_excessive_references_rejected(self):
        ref = registry._ref("target", "target")
        for refs in [None, [], [ref, ref], [dict(ref, extra=True)], [ref] * 101, [True]]:
            with self.subTest(refs=type(refs).__name__), self.assertRaises(ContractError):
                run_scope.normalize_refs(refs)

    def test_partial_source_is_rejected_before_baseline_proposal_is_built(self):
        bound = self.bound()
        bound["manifest"].update(purpose="regression", control_ids=["C01"])
        with self.assertRaisesRegex(AdoptionError, "FULL_SCOPE_REQUIRED"):
            baseline_generations.bind_candidate(object(), {}, {"bound": bound}, 1)


class ScopedRunIntegrationTests(unittest.TestCase):
    open = seed.RegressionIntegrationTests.open
    prepare = seed.RegressionIntegrationTests.prepare
    begin = seed.RegressionIntegrationTests.begin
    complete = seed.RegressionIntegrationTests.complete
    gate_request = seed.RegressionIntegrationTests.gate_request
    setUp = supervisor.SupervisedRunTests.setUp
    run_mode = supervisor.SupervisedRunTests.run_mode

    @classmethod
    def setUpClass(cls):
        seed.RegressionIntegrationTests.setUpClass.__func__(cls)

    def scoped(self, name="targeted"):
        full = self.prepare("scope-template")
        target = full["bound_run"]["registry"]["controls"][0]["target_ref"]
        req = request("run_prepare_scoped", run_scope.request_id(name), run_id=name,
            contract_series_id="fixture-contract-series", expected_contract_ref=deepcopy(self.contract_ref),
            changed_refs=[deepcopy(target)])
        return req, self.store.dispatch(12004, 12004, req), full

    def test_normal_supervision_restart_and_ci_are_bound_to_only_executed_scope(self):
        req, prepared, full = self.scoped("supervised-run")
        self.input.update(trigger="change", changed_refs=req["changed_refs"])
        result = self.run_mode()
        self.assertEqual(result["exit_code"], 0, result)
        self.assertEqual(len(self.runner.executed), 2)
        self.assertEqual(result["executed_scope"], "targeted")
        self.assertEqual(len(result["unexecuted_control_ids"]), 14)
        from tools.gah_report import build_report, render_markdown
        report = build_report(self.runtime, self.gate_request(prepared))
        self.assertEqual(report["scope"]["executed_scope"], "targeted")
        self.assertEqual(report["scope"]["unexecuted_control_ids"], result["unexecuted_control_ids"])
        self.assertIn("未実施Control（合格根拠に含めない）", render_markdown(report))
        self.store.close(); self.store = self.open(); self.runtime.store = self.store
        again = self.run_mode("resume")
        self.assertEqual(again["exit_code"], 0, again)
        self.assertEqual(len(self.runner.executed), 2)
        gate = self.gate_request(prepared)
        gate["request_id"] = "gate-wrong-scope"
        gate["expected_target_refs"] = full["bound_run"]["manifest"]["target_refs"]
        rejected = self.store.dispatch(12004, 12004, gate)
        self.assertFalse(rejected["ci_eligible"])
        self.assertEqual(rejected["exit_code"], 1, rejected)
        self.assertEqual(rejected["reasons"], ["CI_TARGET_MISMATCH"])
        with self.assertRaisesRegex(AdoptionError, "FULL_SCOPE_REQUIRED"):
            self.store.dispatch(12001, 12001, request("baseline_propose", "scope-baseline-reject",
                run_id="supervised-run", proposal_id="scope-baseline", series_id="fixture-baseline-series",
                expected_generation=1))

    def test_scope_actor_request_hash_and_derived_scope_are_checked(self):
        req, prepared, _ = self.scoped()
        row = dict(self.store._db.execute("SELECT * FROM idempotency WHERE request_id=?", (req["request_id"],)).fetchone())
        for field, value in [("actor_id", "manager"), ("request_digest", "f" * 64), ("response_digest", "f" * 64)]:
            with self.subTest(field=field):
                self.store._db.execute("UPDATE idempotency SET " + field + "=? WHERE request_id=?", (value, req["request_id"]))
                with self.assertRaises(AdoptionError): self.begin(prepared)
                self.store._db.execute("UPDATE idempotency SET " + field + "=? WHERE request_id=?", (row[field], req["request_id"]))
        changed = json.loads(row["response_json"])
        changed["scope"]["unexecuted_control_ids"] = []
        raw, digest = _packed(changed)
        self.store._db.execute("UPDATE idempotency SET response_json=?, response_digest=? WHERE request_id=?",
            (raw, digest, req["request_id"]))
        with self.assertRaisesRegex(AdoptionError, "RUN_SCOPE_INVALID"): self.begin(prepared)
        self.assertEqual(self.store._db.execute("SELECT count(*) FROM eval_runs WHERE run_id='targeted'").fetchone()[0], 0)

    def test_unknown_change_runs_full_and_scope_request_has_one_identity(self):
        req, prepared, _ = self.scoped()
        for uid in (12001, 12002, 12003):
            with self.assertRaises(AdoptionError): self.store.dispatch(uid, uid, req)
        wrong_id = dict(req, request_id="arbitrary")
        with self.assertRaisesRegex(AdoptionError, "INVALID_REQUEST"): self.store.dispatch(12004, 12004, wrong_id)
        other = deepcopy(req)
        other.update(run_id="unknown", request_id=run_scope.request_id("unknown"))
        other["changed_refs"][0]["digest"] = "f" * 64
        expanded = self.store.dispatch(12004, 12004, other)
        self.assertEqual(len(expanded["bound_run"]["plan"]["entries"]), 30)
        self.assertEqual(expanded["scope"]["reason"], "UNKNOWN_IMPACT_FULL_FALLBACK")
        self.assertEqual(self.store.dispatch(12004, 12004, other), expanded)

    def test_preparation_storage_failure_rolls_back_and_missing_scope_cannot_resume(self):
        full = self.prepare("scope-template")
        req = request("run_prepare_scoped", run_scope.request_id("atomic"), run_id="atomic",
            contract_series_id="fixture-contract-series", expected_contract_ref=deepcopy(self.contract_ref),
            changed_refs=[full["bound_run"]["manifest"]["target_refs"][0]])
        self.store._db.execute("CREATE TRIGGER reject_scope BEFORE INSERT ON idempotency WHEN NEW.request_id='scope-prepare-atomic' BEGIN SELECT RAISE(ABORT,'synthetic'); END")
        with self.assertRaises(AdoptionError): self.store.dispatch(12004, 12004, req)
        self.assertIsNone(self.store._db.execute("SELECT 1 FROM idempotency WHERE request_id=?", (req["request_id"],)).fetchone())
        self.store._db.execute("DROP TRIGGER reject_scope")
        prepared = self.store.dispatch(12004, 12004, req)
        self.begin(prepared)
        self.store._db.execute("DELETE FROM idempotency WHERE request_id=?", (req["request_id"],))
        with self.assertRaisesRegex(AdoptionError, "REGRESSION_BINDING_INVALID"):
            regression_runs.for_run(self.store._db, self.store._db.execute("SELECT * FROM eval_runs WHERE run_id='atomic'").fetchone(), self.clock.value)


if __name__ == "__main__":
    unittest.main()
