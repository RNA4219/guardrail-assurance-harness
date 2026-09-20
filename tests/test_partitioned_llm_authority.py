"""通常schema-1 authorityでpartitioned LLMの採択・開始・evidence_openを確認する。"""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gah.adoption import AdoptionError
from gah import partitioned_llm_admission, partitioned_guardrail_results
from tests import test_partitioned_guardrail_results as fixed_worker
from gah.run_contracts import content_ref
from gah.wire import canonical_bytes
from tests import test_fixture_admission as fixture
request = fixture.request


class PartitionedLlmAuthorityTests(unittest.TestCase):
    """既存fixture setupを借り、重い既存test moduleを再登録しない。"""

    def setUp(self):
        self.helper = fixture.FixtureAdmissionTests(
            "test_prepare_contract_adoption_and_run_begin_use_real_bound_objects"
        )
        self.helper.setUp()
        self.addCleanup(self.helper.doCleanups)
        self.store = self.helper.open()
        self.addCleanup(self.store.close)
        self.helper.adopt_policy(self.store)

    def prepare(self, *, run_id="partitioned-llm-run", case_count=400, request_id=None):
        return self.store.dispatch(12001, 12001, request(
            "guardrail_prepare_partitioned", request_id or "partitioned-prepare-" + run_id,
            run_id=run_id, policy_series_id=self.helper.policy["policy_id"],
            target_version="baseline-v1", case_count=case_count,
        ))

    def adopt_prepared_contract(self, contract):
        series_id = "partitioned-llm-contract-series"
        self.store.dispatch(12001, 12001, request(
            "contract_propose", "partitioned-contract-propose", proposal_id="partitioned-contract-proposal",
            series_id=series_id, expected_generation=0, contract=deepcopy(contract),
        ))
        validated = self.store.dispatch(12003, 12003, request(
            "contract_validate", "partitioned-contract-validate",
            proposal_id="partitioned-contract-proposal", validation_id="partitioned-contract-validation",
        ))
        self.assertTrue(validated["passed"])
        adopted = self.store.dispatch(12001, 12001, request(
            "contract_adopt", "partitioned-contract-adopt", proposal_id="partitioned-contract-proposal",
            validation_id="partitioned-contract-validation", expected_generation=0,
        ))
        self.assertEqual(adopted["generation"], 1)
        return series_id

    def test_partitioned_admission_adoption_begin_and_evidence_open(self):
        response = self.prepare(case_count=1600)
        prepared = response["prepared"]
        self.assertEqual(response["kind"], "evaluation_authority_result")
        self.assertEqual(prepared["schema_version"], 2)
        self.assertEqual(prepared["kind"], "partitioned_guardrail_preparation")
        self.assertFalse(prepared["ci_eligible"])
        self.assertEqual(prepared["materialization"]["case_count"], 1600)
        admission = prepared["admission_index"]
        self.assertEqual(admission["case_count"], 1600)
        self.assertEqual(admission["run_id"], "partitioned-llm-run")
        manifest = prepared["manifest"]
        self.assertEqual(manifest["schema_version"], 2)
        self.assertEqual(manifest["run_id"], "partitioned-llm-run")
        contract_series_id = self.adopt_prepared_contract(prepared["contract"])

        expected_manifest_ref = content_ref("run_manifest", manifest["run_id"], manifest)
        begin = request(
            "run_begin_partitioned", "partitioned-run-begin", run_id=manifest["run_id"],
            contract_series_id=contract_series_id, expected_manifest_ref=expected_manifest_ref,
        )
        before_eval = self.store._db.execute("SELECT COUNT(*) FROM eval_runs").fetchone()[0]
        before_bound = self.store._db.execute(
            "SELECT COUNT(*) FROM bound_runs WHERE run_id=?", (manifest["run_id"],)
        ).fetchone()[0]
        with self.assertRaises(AdoptionError):
            self.store.dispatch(12003, 12003, deepcopy(begin))
        self.assertEqual(self.store._db.execute("SELECT COUNT(*) FROM eval_runs").fetchone()[0], before_eval)
        self.assertEqual(self.store._db.execute(
            "SELECT COUNT(*) FROM bound_runs WHERE run_id=?", (manifest["run_id"],)
        ).fetchone()[0], before_bound)

        wrong = deepcopy(begin)
        wrong["request_id"] = "partitioned-run-begin-wrong-manifest"
        wrong["expected_manifest_ref"] = dict(expected_manifest_ref, digest="0" * 64)
        with self.assertRaises(AdoptionError):
            self.store.dispatch(12004, 12004, wrong)
        self.assertEqual(self.store._db.execute("SELECT COUNT(*) FROM eval_runs").fetchone()[0], before_eval)
        self.assertEqual(self.store._db.execute(
            "SELECT COUNT(*) FROM bound_runs WHERE run_id=?", (manifest["run_id"],)
        ).fetchone()[0], before_bound)

        begun = self.store.dispatch(12004, 12004, begin)
        self.assertFalse(begun["ci_eligible"])
        run_row = self.store._db.execute(
            "SELECT manifest_json,plan_json FROM eval_runs WHERE run_id=?", (manifest["run_id"],)
        ).fetchone()
        self.assertIsNotNone(run_row)
        stored_manifest = json.loads(run_row["manifest_json"])
        stored_plan_index = json.loads(run_row["plan_json"])
        self.assertEqual(stored_manifest, manifest)
        self.assertEqual(stored_plan_index["kind"], "trial_plan_index")
        self.assertNotIn("entries", stored_plan_index)

        opened = self.store.dispatch(12004, 12004, request(
            "evidence_open", "partitioned-evidence-open", run_id=manifest["run_id"],
        ))
        self.assertEqual(opened["run_id"], manifest["run_id"])
        self.assertEqual(opened["schema_version"], 1)
        self.assertEqual(opened["evidence"]["schema_version"], 2)
        self.assertEqual(opened["evidence"]["bundle"], admission["binding"])
        bound_row = self.store._db.execute(
            "SELECT bundle_json FROM bound_runs WHERE run_id=?", (manifest["run_id"],)
        ).fetchone()
        self.assertIsNotNone(bound_row)
        bundle_raw = bound_row["bundle_json"].encode("utf-8")
        self.assertLessEqual(len(bundle_raw), 1024 * 1024)
        receipt = json.loads(bundle_raw)
        self.assertNotIn("plan", receipt)
        self.assertNotIn("case_set", receipt)
        state = self.store._db.execute(
            "SELECT state FROM run_state WHERE run_id=?", (manifest["run_id"],)
        ).fetchone()
        self.assertEqual(state["state"], "OPEN")
        # 最大規模の末尾1ケースを同じ資源・validator・保存経路へ通す。
        full = partitioned_llm_admission.for_run(self.store._db, manifest["run_id"], 1000)["prepared"]
        entry = full["bound_run"]["plan"]["entries"][-1]
        started = self.store.dispatch(12004, 12004, request("resource_start", "partitioned-case-start",
            run_id=manifest["run_id"], owner_id=begin["request_id"], operation_id="partitioned-case-op",
            entry={key: entry[key] for key in ("obligation_id", "case_id", "trial_id", "variant")},
            scenario="guardrail:baseline-v1", expected_manifest_ref=expected_manifest_ref))
        worker_request = partitioned_guardrail_results.PreparedCases(full).for_entry(
            entry, "partitioned-case-op", started["owner_epoch"])
        output = fixed_worker.worker.evaluate(worker_request, clock=lambda: 1000_000_000_000)
        results = partitioned_guardrail_results.validate_bundle(
            {"request": worker_request, "worker_result": output}, case_count=1600)
        timing = output["stage_timings"][0]
        attempt = {"schema_version": 1, "kind": "attempt_record", "attempt_id": "partitioned-case-attempt",
            "variant": entry["variant"], "retry_of": None, "started_at": timing["started_at"],
            "finished_at": timing["finished_at"], "stop_confirmed": True, "execution_status": "COMPLETED",
            "state_restored": True, "expected_binding": results[0]["binding"], "result": results[0]}
        completed = self.store.dispatch(12003, 12003, request("evidence_complete", "partitioned-case-complete",
            run_id=manifest["run_id"], operation_id="partitioned-case-op", event_id="partitioned-case-stop",
            usage=output["usage"], attempts=[attempt]))
        self.assertTrue(completed["accepted"])
        self.assertEqual(self.store._db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0], 1)
        with self.assertRaises(AdoptionError):
            self.store.dispatch(12004, 12004, request("evidence_finalize", "partitioned-early-finalize",
                run_id=manifest["run_id"]))
        with self.helper.open() as reopened:
            status = reopened.dispatch(12004, 12004, request("run_status", "partitioned-reopened-status",
                run_id=manifest["run_id"]))
            self.assertEqual(status["plan"], stored_plan_index)
            self.assertFalse(status["ci_eligible"])
            self.assertEqual(reopened._db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0], 1)

    def test_admission_artifact_tampering_is_rejected_on_fresh_prepare(self):
        self.prepare(run_id="partitioned-tamper-run", request_id="partitioned-tamper-prepare")
        row = self.store._db.execute(
            "SELECT payload_json,digest FROM fixture_admissions WHERE run_id=?",
            ("partitioned-tamper-run",),
        ).fetchone()
        self.assertIsNotNone(row)
        index = json.loads(row["payload_json"])
        refs = index["artifact_refs"]
        ref = next(value for value in refs.values()
                   if type(value) is dict and value.get("kind") and value.get("id"))
        artifact = self.store._db.execute(
            "SELECT payload_json,digest FROM eval_objects WHERE kind=? AND id=?",
            (ref["kind"], ref["id"]),
        ).fetchone()
        self.assertIsNotNone(artifact)
        # 保存artifactのbytesを改変し、元のcontent digest/refは維持する。
        self.store._db.execute(
            "UPDATE eval_objects SET payload_json=? WHERE kind=? AND id=?",
            (canonical_bytes({"tampered": True}).decode("utf-8"), ref["kind"], ref["id"]),
        )
        self.store._db.commit()
        with self.assertRaises(AdoptionError):
            self.prepare(run_id="partitioned-tamper-run", request_id="partitioned-tamper-replay")
        self.assertEqual(self.store._db.execute("SELECT COUNT(*) FROM eval_runs").fetchone()[0], 0)
        self.assertEqual(self.store._db.execute("SELECT COUNT(*) FROM bound_runs").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
