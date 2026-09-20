"""通常run取消しの停止境界、不変成果物、精算と移行を検査する。"""
from contextlib import closing
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("cancellation_seed_helpers", ROOT / "tests/test_regression_integration.py")
seed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(seed)
from gah.adoption import AdoptionError
from gah import adoption_migrations as migrations
from gah.assurance_authority import fixed_profile
from gah.normalized import normalize_generic
from gah.wire import canonical_bytes
from gah.run_contracts import content_ref
request = seed.request
ZERO = {"input_tokens": 0, "output_tokens": 0, "cost_usd": "0"}


class CancellationIntegrationTests(unittest.TestCase):
    setUp = seed.RegressionIntegrationTests.setUp
    open = seed.RegressionIntegrationTests.open
    prepare = seed.RegressionIntegrationTests.prepare
    begin = seed.RegressionIntegrationTests.begin
    complete = seed.RegressionIntegrationTests.complete
    gate_request = seed.RegressionIntegrationTests.gate_request

    @classmethod
    def setUpClass(cls):
        seed.RegressionIntegrationTests.setUpClass.__func__(cls)
        helper = cls("runTest")
        helper.setUp()
        try:
            cls.completed_prepared = helper.prepare("completed")
            helper.complete(cls.completed_prepared, finalize=False)
            cls.completed_path = Path(cls.temp.name) / "completed.sqlite"
            with closing(sqlite3.connect(cls.completed_path)) as target:
                helper.store._db.backup(target)
        finally:
            helper.doCleanups()

    def completed(self):
        self.store.close()
        with closing(sqlite3.connect(self.completed_path)) as source, closing(sqlite3.connect(self.path)) as target:
            source.backup(target)
        self.store = self.open()
        return deepcopy(self.completed_prepared), {"run_id": "completed", "owner_id": "begin-completed", "owner_epoch": 1}

    def cancel(self, owner, identifier="cancel"):
        return self.store.dispatch(12004, 12004, request("resource_cancel", identifier, **owner))

    def finalize(self, run_id="normal", identifier="cancel-finalize"):
        return self.store.dispatch(12004, 12004, request("run_cancel_finalize", identifier, run_id=run_id))

    def gate(self, prepared, code):
        result = self.store.dispatch(12004, 12004, self.gate_request(prepared))
        self.assertEqual(result["exit_code"], code, result)
        self.assertIs(result["ci_eligible"], code == 0)
        self.assertIs(result["use"], code == 0)
        if code == 3:
            self.assertEqual(result["execution_status"], "CANCELLED")
        return result

    def operation(self, prepared, owner, *, stop=False, usage=None, record=False):
        bound = prepared["bound_run"]
        material = next(r for r in prepared["materialization"]["manifest"]["records"]
            if r["scenario"].startswith("mutation:F01:") and r["variant"] == "candidate")
        entry = next(e for e in bound["plan"]["entries"] if all(e[k] == material[k]
            for k in ("obligation_id", "case_id", "trial_id", "variant")))
        op = owner["run_id"] + "-operation"
        self.store.dispatch(12004, 12004, request("resource_reserve", op + "-reserve", **owner,
            operation_id=op, entry={k: entry[k] for k in ("obligation_id", "case_id", "trial_id", "variant")},
            scenario=material["scenario"]))
        self.store.dispatch(12004, 12004, request("resource_dispatch", op + "-dispatch", **owner, operation_id=op))
        if stop:
            self.observe(owner["run_id"], op, "stop", usage=usage)
        if not record:
            return op
        profile = fixed_profile()
        binding = {"run_id": owner["run_id"], "operation_id": op, "owner_epoch": 1,
            "contract_digest": bound["manifest"]["contract_ref"]["digest"],
            "target_digest": entry["target_ref"]["digest"],
            **{k: entry[k] for k in ("obligation_id", "case_id", "trial_id")},
            "stage_id": entry["stage_ids"][0], "fixture_digest": profile["fixture_digest"],
            "adapter_digest": profile["adapter_digests"][0], "policy_digest": bound["manifest"]["policy_ref"]["digest"],
            "evaluator_digest": entry["evaluator_ref"]["digest"], "isolation_digest": profile["isolation_digest"]}
        _, code, state = material["scenario"].split(":")
        observation = {**self.worker._mutation_observation(code, state), "detected": False}
        normalized = normalize_generic(canonical_bytes({"schema_version": 1, "kind": "gah_generic_result",
            "binding": binding, "mode": "mutation", "observations": observation}), binding,
            execution_status="COMPLETED", exit_code=0, stop_confirmed=True)
        attempt = {"schema_version": 1, "kind": "attempt_record", "attempt_id": op + "-attempt",
            "variant": material["variant"], "retry_of": None, "started_at": self.clock.value,
            "finished_at": self.clock.value, "stop_confirmed": True, "execution_status": "COMPLETED",
            "state_restored": True, "expected_binding": binding, "result": normalized}
        saved = self.store.dispatch(12003, 12003, request("evidence_record", op + "-record",
            run_id=owner["run_id"], attempt=attempt))
        self.assertTrue(saved["accepted"])
        return op

    def observe(self, run_id, op, event, *, usage=None):
        return self.store.dispatch(12003, 12003, request("resource_observe", "observe-" + event,
            run_id=run_id, operation_id=op, event_id=event, stopped=True, usage=usage))

    def test_empty_cancellation_has_outputs_and_cannot_become_success(self):
        prepared = self.prepare()
        owner = self.begin(prepared)
        with self.assertRaises(AdoptionError):
            self.finalize()
        self.gate(prepared, 2)
        self.cancel(owner)
        receipt = self.finalize()
        self.assertEqual(receipt["kind"], "authority_cancel_receipt")
        self.assertEqual(receipt["assurance"], "UNKNOWN")
        self.gate(prepared, 3)
        outputs = self.store.dispatch(12004, 12004, request("run_outputs", "outputs", run_id="normal"))
        for field in ("decision", "evidence", "findings", "plans", "run_receipt"):
            result = self.store.dispatch(12004, 12004, request("run_artifact", "read-" + field,
                run_id="normal", artifact_ref=outputs["outputs"][field]))
            self.assertEqual(result["artifact_ref"], outputs["outputs"][field])
        with self.assertRaises(AdoptionError):
            self.store.dispatch(12004, 12004, request("evidence_finalize", "normal-finalize", run_id="normal"))
        repeated = self.cancel(owner, "cancel-again")
        self.assertTrue(repeated["already_terminal"])
        self.assertTrue(repeated["cancelled"])
        self.store.close()
        self.store = self.open()
        self.assertEqual(self.finalize(), receipt)
        self.gate(prepared, 3)

    def test_stopping_and_late_settlement_keep_reservation_and_receipt(self):
        prepared = self.prepare()
        owner = self.begin(prepared)
        op = self.operation(prepared, owner)
        self.cancel(owner)
        self.assertIsNone(self.store._db.execute(
            "SELECT 1 FROM authority_artifacts WHERE kind='authority_cancel_receipt' AND id='normal'"
        ).fetchone())
        with self.assertRaises(AdoptionError):
            self.finalize()
        pending = self.gate(prepared, 2)
        self.assertIn("STOP_UNCONFIRMED", pending["reasons"])
        self.assertFalse(pending["ci_eligible"])

        wrong_target = self.gate_request(prepared)
        wrong_target["request_id"] = "wrong-target-cancel-pending"
        wrong_target["expected_manifest_ref"]["digest"] = "f" * 64
        mismatch = self.store.dispatch(12004, 12004, wrong_target)
        self.assertIn("CI_TARGET_MISMATCH", mismatch["reasons"])
        self.assertNotIn("STOP_UNCONFIRMED", mismatch["reasons"])

        from tests.test_supervised_run import Runtime
        from tools.gah_report import run as report_run
        report_runtime = Runtime(self.store)
        json_stream = io.StringIO()
        json_exit = report_run(report_runtime, self.gate_request(prepared), json_stream, output_format="json")
        self.assertEqual(json_exit, 2)
        failure = json.loads(json_stream.getvalue())
        self.assertEqual(failure["kind"], "run_report_failure")
        self.assertIn("STOP_UNCONFIRMED", failure["ci_reasons"])
        self.assertIn("STOP_UNCONFIRMED", failure["reasons"])
        self.assertFalse(failure["ci_eligible"])
        markdown_stream = io.StringIO()
        markdown_exit = report_run(report_runtime, self.gate_request(prepared), markdown_stream, output_format="markdown")
        self.assertEqual(markdown_exit, 2)
        self.assertIn('"STOP_UNCONFIRMED"', markdown_stream.getvalue())

        self.observe("normal", op, "stopped")
        stopped_pending = self.gate(prepared, 2)
        self.assertIn("NOT_FINALIZED", stopped_pending["reasons"])
        self.assertNotIn("STOP_UNCONFIRMED", stopped_pending["reasons"])
        self.assertNotEqual(stopped_pending["execution_status"], "CANCELLED")
        receipt = self.finalize()
        self.assertFalse(receipt["budget_closure"])
        self.assertIn("BUDGET_OPEN", self.gate(prepared, 3)["reasons"])
        row = self.store._db.execute("SELECT settled_at,released FROM resource_operations WHERE operation_id=?", (op,)).fetchone()
        self.assertEqual(tuple(row), (None, 0))
        self.clock.value += 1
        self.observe("normal", op, "settled", usage=ZERO)
        closed = self.store.dispatch(12004, 12004, request("resource_close", "close", **owner))
        self.assertTrue(closed["budget_closure"])
        self.assertEqual(closed["resources"]["unsettled"], 0)
        self.assertNotIn("BUDGET_OPEN", self.gate(prepared, 3)["reasons"])
        self.assertEqual({k: v for k, v in self.finalize(identifier="finalize-again").items() if k != "request_id"},
            {k: v for k, v in receipt.items() if k != "request_id"})

    def test_known_negative_remains_in_cancelled_decision_and_reports(self):
        prepared = self.prepare()
        owner = self.begin(prepared)
        self.operation(prepared, owner, stop=True, usage=ZERO, record=True)
        self.cancel(owner)
        receipt = self.finalize()
        self.assertIn(receipt["assurance"], {"DEGRADED", "HOLD"})
        self.gate(prepared, 3)
        for kind in ("findings_report", "plans_report"):
            value = json.loads(self.store._db.execute("SELECT payload_json FROM authority_artifacts WHERE run_id='normal' AND kind=?", (kind,)).fetchone()[0])
            self.assertTrue(value["items"])

    def test_report_failure_rolls_back_terminal_but_retains_cancellation(self):
        prepared = self.prepare()
        owner = self.begin(prepared)
        self.operation(prepared, owner, stop=True)
        self.cancel(owner)
        self.store._db.execute("CREATE TRIGGER fail_cancel_report BEFORE INSERT ON authority_artifacts WHEN NEW.kind='plans_report' BEGIN SELECT RAISE(ABORT,'test'); END")
        with self.assertRaises(AdoptionError):
            self.finalize()
        for table in ("authority_artifacts", "terminals"):
            self.assertEqual(self.store._db.execute("SELECT COUNT(*) FROM " + table + " WHERE run_id='normal'").fetchone()[0], 0)
        self.assertIsNone(self.store._db.execute("SELECT 1 FROM idempotency WHERE request_id='cancel-finalize'").fetchone())
        self.assertEqual(self.store._db.execute("SELECT cancelled FROM resource_runs WHERE run_id='normal'").fetchone()[0], 1)
        self.assertIsNone(self.store._db.execute("SELECT settled_at FROM resource_operations WHERE run_id='normal'").fetchone()[0])
        self.store._db.execute("DROP TRIGGER fail_cancel_report")
        self.finalize()
        self.gate(prepared, 3)

    def test_closed_before_terminal_can_cancel_but_finalized_cannot_change(self):
        prepared, owner = self.completed()
        self.cancel(owner)
        self.assertEqual(self.finalize("completed")["assurance"], "HEALTHY")
        self.gate(prepared, 3)
        prepared, owner = self.completed()
        finalize = request("evidence_finalize", "complete-finalize", run_id="completed")
        receipt = self.store.dispatch(12004, 12004, finalize)
        result = self.cancel(owner)
        self.assertTrue(result["already_terminal"])
        self.assertFalse(result["cancelled"])
        self.assertEqual(self.store._db.execute("SELECT cancelled FROM resource_runs WHERE run_id='completed'").fetchone()[0], 0)
        self.assertEqual(self.store.dispatch(12004, 12004, finalize), receipt)
        self.gate(prepared, 0)

    def test_wrong_target_and_missing_outputs_are_not_cancellation_proof(self):
        prepared = self.prepare()
        self.cancel(self.begin(prepared))
        self.finalize()
        gate = self.gate_request(prepared)
        gate["request_id"] = "different-target"
        gate["expected_manifest_ref"]["digest"] = "f" * 64
        self.assertEqual(self.store.dispatch(12004, 12004, gate)["exit_code"], 1)
        self.store._db.execute("DELETE FROM authority_artifacts WHERE kind='plans_report' AND id='normal'")
        self.gate(prepared, 2)

    def test_finalize_roles_and_shape_cannot_mutate(self):
        prepared = self.prepare()
        self.cancel(self.begin(prepared))
        value = request("run_cancel_finalize", "cancel-finalize", run_id="normal")
        for uid in (12001, 12002, 12003):
            with self.subTest(uid=uid), self.assertRaises(AdoptionError):
                self.store.dispatch(uid, uid, value)
        for edits in ({"schema_version": True}, {"stop_confirmed": True}, {"ci_eligible": True}):
            with self.subTest(edits=edits), self.assertRaises(AdoptionError):
                self.store.dispatch(12004, 12004, {**value, **edits})
        self.gate(prepared, 2)

    def test_previous_normal_store_migrates_with_history_and_rejects_future_cancel(self):
        prepared, _ = self.completed()
        self.store.dispatch(12004, 12004, request("evidence_finalize", "completed-finalize", run_id="completed"))
        self.begin(self.prepare("unfinished"))
        before = tuple(self.store._db.execute("SELECT kind,id,digest,payload_json,run_id FROM authority_artifacts ORDER BY kind,id,digest"))
        self.store._db.execute("UPDATE adoption_config SET value=? WHERE key='extension_digest'", (migrations._V4_REGRESSION_EXTENSION_DIGEST,))
        self.store.close()
        result = migrations.migrate_evaluation_store(self.path)
        self.assertEqual(result["predecessor_extension_digest"], migrations._V4_REGRESSION_EXTENSION_DIGEST)
        self.store = self.open()
        self.assertEqual(tuple(self.store._db.execute("SELECT kind,id,digest,payload_json,run_id FROM authority_artifacts ORDER BY kind,id,digest")), before)
        self.gate(prepared, 0)
        self.cancel({"run_id": "unfinished", "owner_id": "begin-unfinished", "owner_epoch": 1})
        self.finalize("unfinished")
        self.store._db.execute("UPDATE adoption_config SET value=? WHERE key='extension_digest'", (migrations._V4_REGRESSION_EXTENSION_DIGEST,))
        self.store.close()
        with self.assertRaises(migrations.MigrationError):
            migrations.migrate_evaluation_store(self.path)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT value FROM adoption_config WHERE key='extension_digest'").fetchone()[0], migrations._V4_REGRESSION_EXTENSION_DIGEST)

    def test_cancel_between_begin_and_evidence_open(self):
        prepared = self.prepare()
        bound = prepared["bound_run"]
        self.store.dispatch(12004, 12004, request("run_begin", "begin-normal", manifest=bound["manifest"],
            plan=bound["plan"], contract_series_id="fixture-contract-series"))
        self.assertIsNone(self.store._db.execute("SELECT 1 FROM bound_runs WHERE run_id='normal'").fetchone())
        self.cancel({"run_id": "normal", "owner_id": "begin-normal", "owner_epoch": 1})
        self.assertEqual(self.finalize()["assurance"], "UNKNOWN")
        self.gate(prepared, 3)

    def test_late_success_is_recorded_without_changing_cancelled_terminal(self):
        prepared, owner = self.completed()
        self.cancel(owner)
        receipt = self.finalize("completed")
        attempt = json.loads(self.store._db.execute("SELECT attempt_json FROM attempts WHERE run_id='completed' LIMIT 1").fetchone()[0])
        attempt["attempt_id"] = "late-completed-attempt"
        result = self.store.dispatch(12003, 12003, request("evidence_record", "late-success",
            run_id="completed", attempt=attempt))
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"], "LATE_ATTEMPT")
        self.assertEqual(self.gate(prepared, 3)["assurance"], "HOLD")
        self.assertEqual(self.finalize("completed"), receipt)

    def test_cancel_stop_does_not_require_resolvable_adoption_history(self):
        prepared = self.prepare()
        owner = self.begin(prepared)
        op = self.operation(prepared, owner)
        self.store._db.execute("DELETE FROM eval_adoptions WHERE generation=2")
        self.assertTrue(self.cancel(owner)["cancelled"])
        self.observe("normal", op, "cleanup", usage=ZERO)
        closed = self.store.dispatch(12004, 12004, request("resource_close", "cleanup-close", **owner))
        self.assertTrue(closed["budget_closure"])
        self.gate(prepared, 2)
