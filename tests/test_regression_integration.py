"""採択済み世代2の通常runと、現在のCI利用を実SQLiteで検査する。"""
from copy import deepcopy
from contextlib import closing
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gah.adoption import AdoptionError, AdoptionStore
from gah import adoption_migrations as migrations
from gah.assurance_authority import fixed_profile
from gah.evaluation_authority import EvaluationExtension
from gah.normalized import normalize_generic
from gah.policy import initial_policy_profile
from gah.run_contracts import content_ref
from gah.wire import canonical_bytes

spec = importlib.util.spec_from_file_location("regression_seed_helpers",
    ROOT / "tests/test_transition_acceptance_integration.py")
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)
request = helpers.request


class Clock:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value


class RegressionIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        helper = helpers.TransitionAcceptanceIntegrationTests("runTest")
        helper.setUp()
        cls.addClassCleanup(helper.doCleanups)
        base = helper.fixture.fixture
        base.open = lambda: AdoptionStore(base.path, clock=base.clock, bootstrap_policy=base.policy,
            validator_digest=migrations._V2_VALIDATOR_DIGEST, extension=EvaluationExtension())
        store, initial, _, candidate, _, values = helper._arrange("normal-seed")
        helper._complete(store, candidate, values)
        helper._validate(store, values, "normal-seed")
        store.dispatch(12001, 12001, request("contract_candidate_adopt", "adopt-normal-seed",
            candidate_id=values["candidate_id"], validation_id="validation-normal-seed",
            expected_contract_generation=1, expected_baseline_generation=1))
        cls.seed_path = Path(cls.temp.name) / "seed.sqlite"
        with closing(sqlite3.connect(cls.seed_path)) as target:
            store._db.backup(target)
        cls.at = helper.fixture.fixture.clock.value + 1
        cls.worker = helper.fixture.fixture.worker
        cls.contract_ref = candidate["runs"]["new"]["bound_run"]["manifest"]["contract_ref"]
        cls.source_id = initial["bound_run"]["manifest"]["run_id"]
        cls.new_id = values["new_run_id"]

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "test.sqlite"
        with closing(sqlite3.connect(self.seed_path)) as source, closing(sqlite3.connect(self.path)) as target:
            source.backup(target)
        self.clock = Clock(self.at)
        self.store = self.open()

    def open(self):
        store = AdoptionStore(self.path, clock=self.clock, bootstrap_policy=initial_policy_profile(),
            validator_digest=migrations._V2_VALIDATOR_DIGEST, extension=EvaluationExtension())
        self.addCleanup(store.close)
        return store

    def prepare(self, run_id="normal"):
        result = self.store.dispatch(12004, 12004, request("run_prepare", "prepare-" + run_id,
            run_id=run_id, contract_series_id="fixture-contract-series",
            expected_contract_ref=deepcopy(self.contract_ref)))
        self.assertFalse(result["ci_eligible"])
        return result

    def begin(self, prepared):
        bound = prepared["bound_run"]
        run_id = bound["manifest"]["run_id"]
        self.store.dispatch(12004, 12004, request("run_begin", "begin-" + run_id,
            manifest=bound["manifest"], plan=bound["plan"], contract_series_id="fixture-contract-series"))
        self.store.dispatch(12004, 12004, request("evidence_open", "open-" + run_id, run_id=run_id))
        return {"run_id": run_id, "owner_id": "begin-" + run_id, "owner_epoch": 1}

    def complete(self, prepared, *, finalize=True, mutate=None):
        owner = self.begin(prepared)
        bound = prepared["bound_run"]
        run_id = owner["run_id"]
        profile = fixed_profile()
        for index, record in enumerate(prepared["materialization"]["manifest"]["records"]):
            entry = next(e for e in bound["plan"]["entries"] if all(e[k] == record[k]
                for k in ("obligation_id", "case_id", "trial_id", "variant")))
            op = run_id + "-op-" + str(index)
            self.store.dispatch(12004, 12004, request("resource_reserve", op + "-reserve",
                **owner, operation_id=op, entry={k: entry[k] for k in
                    ("obligation_id", "case_id", "trial_id", "variant")}, scenario=record["scenario"]))
            self.store.dispatch(12004, 12004, request("resource_dispatch", op + "-dispatch",
                **owner, operation_id=op))
            binding = {"run_id": run_id, "operation_id": op, "owner_epoch": 1,
                "contract_digest": bound["manifest"]["contract_ref"]["digest"],
                "target_digest": entry["target_ref"]["digest"],
                **{k: entry[k] for k in ("obligation_id", "case_id", "trial_id")},
                "stage_id": entry["stage_ids"][0], "fixture_digest": profile["fixture_digest"],
                "adapter_digest": profile["adapter_digests"][0],
                "policy_digest": bound["manifest"]["policy_ref"]["digest"],
                "evaluator_digest": entry["evaluator_ref"]["digest"],
                "isolation_digest": profile["isolation_digest"]}
            kind, code, state = record["scenario"].split(":")
            observations = (self.worker._constraint_observation(code, state) if kind == "constraint"
                else self.worker._mutation_observation(code, state))
            if mutate is not None:
                observations = mutate(record, observations)
            result = normalize_generic(canonical_bytes({"schema_version": 1,
                "kind": "gah_generic_result", "binding": binding, "mode": kind,
                "observations": observations}), binding, execution_status="COMPLETED",
                exit_code=0, stop_confirmed=True)
            self.store.dispatch(12003, 12003, request("resource_observe", op + "-observe",
                run_id=run_id, operation_id=op, event_id=op + "-event", stopped=True,
                usage={"input_tokens": 0, "output_tokens": 0, "cost_usd": "0"}))
            attempt = {"schema_version": 1, "kind": "attempt_record", "attempt_id": op + "-attempt",
                "variant": record["variant"], "retry_of": None, "started_at": self.clock.value,
                "finished_at": self.clock.value, "stop_confirmed": True, "execution_status": "COMPLETED",
                "state_restored": True, "expected_binding": binding, "result": result}
            saved = self.store.dispatch(12003, 12003, request("evidence_record", op + "-record",
                run_id=run_id, attempt=attempt))
            self.assertTrue(saved["accepted"])
        self.store.dispatch(12004, 12004, request("resource_close", "close-" + run_id, **owner))
        if finalize:
            return self.store.dispatch(12004, 12004, request("evidence_finalize", "finalize-" + run_id,
                run_id=run_id))

    def gate_request(self, prepared):
        manifest = prepared["bound_run"]["manifest"]
        return request("ci_check", "gate-" + manifest["run_id"], run_id=manifest["run_id"],
            expected_manifest_ref=content_ref("run_manifest", manifest["run_id"], manifest),
            expected_contract_ref=manifest["contract_ref"], expected_baseline_ref=manifest["baseline_ref"],
            expected_target_refs=manifest["target_refs"], expected_use_cases=manifest["use_cases"])

    def test_normal_complete_outputs_and_fresh_ci_survive_restart_then_revocation(self):
        prepared = self.prepare()
        receipt = self.complete(prepared)
        self.assertEqual(receipt["assurance"], "HEALTHY")
        self.assertFalse(receipt["ci_eligible"])
        outputs = self.store.dispatch(12004, 12004, request("run_outputs", "outputs", run_id="normal"))
        self.assertEqual(set(outputs["outputs"]) & {"decision", "evidence", "findings", "plans", "run_receipt"},
            {"decision", "evidence", "findings", "plans", "run_receipt"})
        for kind in ("findings_report", "plans_report"):
            row = self.store._db.execute("SELECT payload_json FROM authority_artifacts WHERE kind=? AND id='normal'", (kind,)).fetchone()
            self.assertEqual(json.loads(row[0])["items"], [])
        gate = self.gate_request(prepared)
        result = self.store.dispatch(12004, 12004, gate)
        self.assertTrue(result["ci_eligible"], result)
        self.assertEqual(result["exit_code"], 0)
        self.store.close()
        self.store = self.open()
        self.assertEqual(self.store.dispatch(12004, 12004, gate), result)
        self.store.dispatch(12004, 12004, request("evidence_revoke", "revoke-source", run_id=self.source_id))
        revoked = self.store.dispatch(12004, 12004, gate)
        self.assertFalse(revoked["ci_eligible"])
        self.assertEqual(revoked["exit_code"], 1, revoked)
        self.assertEqual(self.store.dispatch(12004, 12004,
            request("evidence_finalize", "finalize-normal", run_id="normal")), receipt)

    def test_saved_aggregate_is_bound_to_decision_and_content_is_verified(self):
        prepared = self.prepare("measured"); receipt = self.complete(prepared)
        decision = self.store.dispatch(12004, 12004, request("run_artifact", "measured-decision",
            run_id="measured", artifact_ref=receipt["decision_ref"]))["artifact"]
        ref = {"kind":"aggregation", "id":"measured", "digest":decision["aggregate_digest"]}
        read = request("run_artifact", "measured-counts", run_id="measured", artifact_ref=ref)
        result = self.store.dispatch(12004, 12004, read)
        self.assertFalse(result["ci_eligible"])
        self.assertEqual(content_ref("aggregation", "measured", result["artifact"]), ref)
        for bad in ({**ref,"id":"unrelated"}, {**ref,"digest":"f"*64}):
            with self.subTest(ref=bad), self.assertRaises(AdoptionError):
                self.store.dispatch(12004, 12004, {**read,"request_id":"measured-wrong", "artifact_ref":bad})
        row = self.store._db.execute("SELECT * FROM aggregates WHERE run_id='measured'").fetchone()
        original = dict(row); changed = json.loads(original["aggregate_json"])
        changed["counts"]["variant"]["candidate"]["tp"] += 1
        self.store._db.execute("UPDATE aggregates SET aggregate_json=? WHERE run_id='measured'", (canonical_bytes(changed).decode(),))
        try:
            with self.assertRaises(AdoptionError): self.store.dispatch(12004, 12004, read)
        finally:
            self.store._db.execute("UPDATE aggregates SET aggregate_json=? WHERE run_id='measured'", (original["aggregate_json"],))
        self.store._db.execute("DELETE FROM aggregates WHERE run_id='measured'")
        try:
            with self.assertRaises(AdoptionError): self.store.dispatch(12004, 12004, read)
        finally:
            columns = list(original)
            self.store._db.execute("INSERT INTO aggregates("+",".join(columns)+") VALUES("+",".join("?" for _ in columns)+")", tuple(original.values()))
        self.assertEqual(self.store.dispatch(12004, 12004, read), result)
        from tests.test_supervised_run import Runtime
        from tools.gah_report import build_report
        report = build_report(Runtime(self.store), self.gate_request(prepared))
        self.assertTrue(report["ci_eligible"])
        self.assertEqual(report["measurements"]["counts"], result["artifact"]["counts"])

    def test_roles_shape_and_reserved_ids_reject_without_creating_runs(self):
        valid = request("run_prepare", "prepare-invalid", run_id="normal",
            contract_series_id="fixture-contract-series", expected_contract_ref=deepcopy(self.contract_ref))
        for uid in (12001, 12002, 12003):
            with self.subTest(uid=uid), self.assertRaises(AdoptionError):
                self.store.dispatch(uid, uid, valid)
        for edits in ({"run_id": self.new_id}, {"run_id": self.source_id}, {"run_id": "x" * 65},
                      {"schema_version": True}, {"unexpected": True}):
            with self.subTest(edits=edits), self.assertRaises(AdoptionError):
                self.store.dispatch(12004, 12004, {**valid, **edits})
        self.assertIsNone(self.store._db.execute("SELECT 1 FROM eval_runs WHERE run_id='normal'").fetchone())

    def test_negative_observation_creates_resolvable_findings_and_plans(self):
        def negative(record, observation):
            if record["scenario"].startswith("mutation:F01:") and record["variant"] == "candidate":
                return {**observation, "detected": False}
            return observation
        prepared = self.prepare()
        receipt = self.complete(prepared, mutate=negative)
        self.assertIn(receipt["assurance"], {"DEGRADED", "HOLD"})
        gate = self.store.dispatch(12004, 12004, self.gate_request(prepared))
        self.assertFalse(gate["ci_eligible"])
        self.assertEqual(gate["exit_code"], 1, gate)
        rows = self.store._db.execute("SELECT payload_json FROM authority_artifacts WHERE kind='remediation_plan' AND run_id='normal'").fetchall()
        self.assertGreater(len(rows), 0)
        for row in rows:
            plan = json.loads(row[0])
            self.assertEqual(plan["execution_status"], "NOT_EXECUTED")
            self.assertTrue(plan["missing_information"])
            ref = plan["finding_ref"]
            finding = self.store._db.execute("SELECT payload_json FROM authority_artifacts WHERE kind=? AND id=? AND digest=?",
                (ref["kind"], ref["id"], ref["digest"])).fetchone()
            self.assertIsNotNone(finding)
            self.assertEqual(content_ref(ref["kind"], ref["id"], json.loads(finding[0])), ref)
        plan = json.loads(rows[0][0])
        plan_ref = content_ref("remediation_plan", plan["plan_id"], plan)
        fetched = self.store.dispatch(12004, 12004, request("run_artifact", "read-plan",
            run_id="normal", artifact_ref=plan_ref))
        self.assertEqual(fetched["artifact"], plan)
        with self.assertRaises(AdoptionError):
            self.store.dispatch(12004, 12004, request("run_artifact", "read-other-run",
                run_id="normal", artifact_ref=content_ref("run_manifest", self.source_id, {})))

    def test_populated_previous_adoption_version_migrates_without_rewriting_history(self):
        before = tuple(self.store._db.execute("SELECT payload_json,digest FROM authority_run_receipts ORDER BY run_id"))
        self.store._db.execute("UPDATE adoption_config SET value=? WHERE key='extension_digest'",
            (migrations._V4_ADOPTION_EXTENSION_DIGEST,))
        self.store.close()
        result = migrations.migrate_evaluation_store(self.path)
        self.assertEqual(result["predecessor_extension_digest"], migrations._V4_ADOPTION_EXTENSION_DIGEST)
        self.store = self.open()
        self.assertEqual(tuple(self.store._db.execute("SELECT payload_json,digest FROM authority_run_receipts ORDER BY run_id")), before)
        self.assertEqual(self.prepare()["bound_run"]["contract"]["generation"], 2)

    def test_previous_version_cannot_contain_new_normal_run(self):
        self.begin(self.prepare())
        self.store._db.execute("UPDATE adoption_config SET value=? WHERE key='extension_digest'",
            (migrations._V4_ADOPTION_EXTENSION_DIGEST,))
        self.store.close()
        with self.assertRaises(migrations.MigrationError):
            migrations.migrate_evaluation_store(self.path)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT value FROM adoption_config WHERE key='extension_digest'").fetchone()[0],
                migrations._V4_ADOPTION_EXTENSION_DIGEST)

    def test_factory_binding_tamper_cannot_begin(self):
        prepared = self.prepare()
        for field, value in (("purpose", "baseline_candidate"), ("deadline", self.clock.value + 1)):
            bound = deepcopy(prepared["bound_run"])
            bound["manifest"][field] = value
            with self.subTest(field=field), self.assertRaises(AdoptionError):
                self.store.dispatch(12004, 12004, request("run_begin", "tamper-" + field,
                    manifest=bound["manifest"], plan=bound["plan"], contract_series_id="fixture-contract-series"))
        self.assertIsNone(self.store._db.execute("SELECT 1 FROM resource_runs WHERE run_id='normal'").fetchone())

    def test_incomplete_run_and_missing_outputs_cannot_pass_ci(self):
        prepared = self.prepare()
        self.complete(prepared, finalize=False)
        gate = self.gate_request(prepared)
        result = self.store.dispatch(12004, 12004, gate)
        self.assertFalse(result["ci_eligible"])
        self.assertEqual(result["exit_code"], 2)
        self.store.dispatch(12004, 12004, request("evidence_finalize", "finalize-normal", run_id="normal"))
        self.store._db.execute("DELETE FROM authority_artifacts WHERE kind='plans_report' AND id='normal'")
        result = self.store.dispatch(12004, 12004, gate)
        self.assertFalse(result["ci_eligible"])
        self.assertEqual(result["exit_code"], 2)

    def test_report_insert_failure_rolls_back_terminal_and_can_retry(self):
        prepared = self.prepare()
        self.complete(prepared, finalize=False)
        self.store._db.execute("CREATE TRIGGER fail_report BEFORE INSERT ON authority_artifacts "
            "WHEN NEW.kind='plans_report' BEGIN SELECT RAISE(ABORT,'test'); END")
        finalize = request("evidence_finalize", "finalize-normal", run_id="normal")
        with self.assertRaises(AdoptionError):
            self.store.dispatch(12004, 12004, finalize)
        for table in ("authority_artifacts", "authority_run_receipts"):
            self.assertEqual(self.store._db.execute("SELECT COUNT(*) FROM " + table + " WHERE run_id='normal'").fetchone()[0], 0)
        self.assertIsNone(self.store._db.execute("SELECT 1 FROM idempotency WHERE request_id='finalize-normal'").fetchone())
        self.store._db.execute("DROP TRIGGER fail_report")
        self.assertEqual(self.store.dispatch(12004, 12004, finalize)["assurance"], "HEALTHY")


if __name__ == "__main__":
    unittest.main()
