"""Closed ResourceBook snapshotからのbudget warning authority integration。"""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

_spec = importlib.util.spec_from_file_location(
    "budget_warning_regression_seed", ROOT / "tests/test_regression_integration.py")
_seed = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_seed)
_request = _seed.request


class _Runtime:
    def __init__(self, store):
        self.store = store

    def client(self, uid, request):
        from gah.adoption import AdoptionError
        try:
            return self.store.dispatch(uid, uid, request)
        except AdoptionError as error:
            return {"kind": "authority_error", "reason": error.code, "ci_eligible": False}


class BudgetWarningAuthorityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.seed_case = _seed.RegressionIntegrationTests
        cls.seed_case.setUpClass()
        cls.addClassCleanup(cls.seed_case.doClassCleanups)

    def setUp(self):
        self.fixture = self.seed_case("runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_closed_snapshot_persists_warning_and_ci_report_without_downgrading_run(self):
        from gah.run_contracts import content_ref
        from tools.gah_report import build_report, render_markdown

        prepared = self.fixture.prepare("budget-warning")
        bound = prepared["bound_run"]
        run_id = bound["manifest"]["run_id"]
        records = prepared["materialization"]["manifest"]["records"]
        self.assertEqual(len(records), 30)
        original_dispatch = self.fixture.store.dispatch
        changed_clock = {"done": False}

        def dispatch_with_elapsed_close(uid, gid, request):
            if request.get("action") == "resource_close" and not changed_clock["done"]:
                row = self.fixture.store._db.execute(
                    "SELECT created_at FROM resource_runs WHERE run_id=?", (run_id,)).fetchone()
                self.assertIsNotNone(row)
                self.fixture.clock.value = row["created_at"] + 4320
                claim = original_dispatch(uid, gid, _request("resource_claim", "claim-" + run_id,
                    run_id=run_id, owner_id=request["owner_id"], recovery=False))
                self.assertFalse(claim["recovery_only"])
                request = {**request, "owner_epoch": claim["owner_epoch"]}
                changed_clock["done"] = True
            return original_dispatch(uid, gid, request)

        self.fixture.store.dispatch = dispatch_with_elapsed_close
        receipt = self.fixture.complete(prepared)
        self.assertTrue(changed_clock["done"])
        self.assertEqual(receipt["assurance"], "WARNING")
        self.assertFalse(receipt["ci_eligible"])
        self.assertEqual(self.fixture.store._db.execute(
            "SELECT COUNT(*) FROM attempts WHERE run_id=?", (run_id,)).fetchone()[0], 30)
        started = self.fixture.store._db.execute(
            "SELECT created_at FROM resource_runs WHERE run_id=?", (run_id,)).fetchone()[0]
        closure = self.fixture.store._db.execute(
            "SELECT closed_at FROM resource_runs WHERE run_id=?", (run_id,)).fetchone()[0]
        self.assertEqual(closure - started, 4320)

        decision_response = original_dispatch(12004, 12004, _request("run_artifact", "decision-warning",
            run_id=run_id, artifact_ref=receipt["decision_ref"]))
        decision = decision_response["artifact"]
        self.assertEqual(decision["assurance"], "WARNING")
        self.assertIn({"code": "warning", "state": "WARNING", "metric_id": None}, decision["reasons"])
        basis = decision["budget_warning"]
        self.assertEqual(set(basis), {"schema_version", "kind", "run_id", "manifest_ref", "policy_ref",
            "profile", "started_at", "closed_at", "usage", "limits", "warning_usage_min"})
        self.assertEqual(basis["kind"], "run_budget_warning_basis")
        self.assertEqual(basis["run_id"], run_id)
        self.assertEqual(basis["manifest_ref"], content_ref("run_manifest", run_id, bound["manifest"]))
        self.assertEqual(basis["policy_ref"], bound["manifest"]["policy_ref"])
        self.assertEqual(basis["profile"], "full")
        self.assertEqual(basis["started_at"], started)
        self.assertEqual(basis["closed_at"], closure)
        self.assertEqual(basis["usage"]["elapsed_seconds"], 4320)
        self.assertEqual(basis["limits"]["elapsed_seconds"], 5400)
        self.assertEqual(basis["warning_usage_min"], [4, 5])
        self.assertEqual(set(basis["usage"]), {"elapsed_seconds", "case_trial_executions", "model_calls",
            "total_tokens", "api_cost_usd_micros"})
        self.assertEqual(set(basis["limits"]), set(basis["usage"]))

        gate_request = self.fixture.gate_request(prepared)
        gate = original_dispatch(12004, 12004, gate_request)
        self.assertTrue(gate["ci_eligible"], gate)
        self.assertEqual(gate["exit_code"], 0)
        self.assertEqual(gate["assurance"], "WARNING")
        report = build_report(_Runtime(self.fixture.store), gate_request)
        self.assertEqual(report["exit_code"], 0)
        self.assertEqual(report["assurance"], "WARNING")
        self.assertEqual(report["budget_warning"], basis)
        json_text = json.dumps(report, ensure_ascii=False, sort_keys=True)
        markdown = render_markdown(report)
        self.assertIn("run_budget_warning_basis", json_text)
        self.assertIn('予算警告の対象: ["elapsed_seconds"]', markdown)
        for token in ("elapsed_seconds", "4320", "5400"):
            with self.subTest(token=token):
                self.assertIn(token, json_text)
                self.assertIn(token, markdown)

        row = self.fixture.store._db.execute(
            "SELECT payload_json,digest FROM authority_artifacts WHERE kind=? AND id=? AND digest=?",
            (receipt["decision_ref"]["kind"], receipt["decision_ref"]["id"],
             receipt["decision_ref"]["digest"])).fetchone()
        self.assertIsNotNone(row)
        decision_bytes = (row["payload_json"], row["digest"])
        self.fixture.store.close()
        self.fixture.store = self.fixture.open()
        replayed = self.fixture.store.dispatch(12004, 12004, _request(
            "evidence_finalize", "finalize-" + run_id, run_id=run_id))
        self.assertEqual(replayed, receipt)

        stale = deepcopy(gate_request)
        stale["request_id"] = "gate-stale-contract"
        stale["expected_contract_ref"] = content_ref("evaluation_contract", "other-contract", {})
        rejected = self.fixture.store.dispatch(12004, 12004, stale)
        self.assertFalse(rejected["ci_eligible"])
        after = self.fixture.store._db.execute(
            "SELECT payload_json,digest FROM authority_artifacts WHERE kind=? AND id=? AND digest=?",
            (receipt["decision_ref"]["kind"], receipt["decision_ref"]["id"],
             receipt["decision_ref"]["digest"])).fetchone()
        self.assertEqual((after["payload_json"], after["digest"]), decision_bytes)
        self.assertEqual(self.fixture.store.dispatch(12004, 12004, gate_request)["exit_code"], 0)
        conflict = self.fixture.store.dispatch(12003, 12003, _request(
            "resource_observe", "late-warning-conflict", run_id=run_id,
            operation_id=run_id + "-op-0", event_id=run_id + "-op-0-event", stopped=False,
            usage={"input_tokens": 0, "output_tokens": 0, "cost_usd": "0"}))
        self.assertTrue(conflict["conflict"])
        current = self.fixture.store.dispatch(12004, 12004, gate_request)
        self.assertFalse(current["ci_eligible"])
        self.assertNotEqual(current["exit_code"], 0)
        saved = self.fixture.store._db.execute(
            "SELECT payload_json,digest FROM authority_artifacts WHERE kind=? AND id=? AND digest=?",
            (receipt["decision_ref"]["kind"], run_id, receipt["decision_ref"]["digest"])).fetchone()
        self.assertEqual(tuple(saved), decision_bytes)
        historical = self.fixture.store.dispatch(12004, 12004, _request(
            "evidence_finalize", "finalize-after-conflict", run_id=run_id))
        self.assertEqual({k: v for k, v in historical.items() if k != "request_id"},
                         {k: v for k, v in receipt.items() if k != "request_id"})


if __name__ == "__main__":
    unittest.main()
