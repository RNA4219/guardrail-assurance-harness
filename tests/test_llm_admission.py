"""400入力の採択と二段階保存を実SQLiteで確認する。Docker動作は別途確認する。"""
from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from tests import test_fixture_admission as fixture
from gah import llm_admission, guardrail_runtime, resource_authority
from gah.adoption import AdoptionError
from gah.contracts import ContractError, MAX_DOCUMENT_BYTES
from gah.run_contracts import content_ref
from gah.normalized import normalize_generic
from gah.wire import canonical_bytes
request = fixture.request


class LlmAdmissionTests(unittest.TestCase):
    setUp = fixture.FixtureAdmissionTests.setUp
    open = fixture.FixtureAdmissionTests.open
    adopt_policy = fixture.FixtureAdmissionTests.adopt_policy

    def prepare(self, store):
        return store.dispatch(12001, 12001, request("guardrail_prepare", "llm-prepare",
            run_id="llm-run", policy_series_id=self.policy["policy_id"], target_version="baseline-v1"))

    def setup_run(self, store):
        self.adopt_policy(store)
        response = self.prepare(store)
        bound = response["prepared"]["bound_run"]
        for uid, action, extra in (
            (12001, "contract_propose", {"proposal_id":"llm-proposal", "series_id":"llm-series", "expected_generation":0, "contract":bound["contract"]}),
            (12003, "contract_validate", {"proposal_id":"llm-proposal", "validation_id":"llm-validation"}),
            (12001, "contract_adopt", {"proposal_id":"llm-proposal", "validation_id":"llm-validation", "expected_generation":0}),
            (12004, "run_begin", {"manifest":bound["manifest"], "plan":bound["plan"], "contract_series_id":"llm-series"}),
            (12004, "evidence_open", {"run_id":"llm-run"})):
            store.dispatch(uid, uid, request(action, action + "-llm", **extra))
        return response

    def test_initial_adoption_records_calibration_and_two_stage_operation_without_claiming_complete(self):
        with self.open() as store:
            response = self.setup_run(store)
            self.assertLess(len(canonical_bytes(response)), MAX_DOCUMENT_BYTES)
            calibration = response["calibration"]["measurement"]
            self.assertTrue(calibration["evaluator_calibration_passed"])
            self.assertEqual(calibration["executed_vector_count"], 165)
            self.assertIsNone(calibration["target_agreement_passed"])
            prepared = response["prepared"]; bound = prepared["bound_run"]
            entry = next(e for e in bound["plan"]["entries"] if len(e["stage_ids"]) == 2)
            owner = {"run_id":"llm-run", "owner_id":"run_begin-llm", "owner_epoch":1}
            store.dispatch(12004, 12004, request("resource_claim", "claim-llm", run_id="llm-run", owner_id=owner["owner_id"], recovery=False))
            short = {key:entry[key] for key in ("obligation_id","case_id","trial_id","variant")}
            with self.assertRaises(AdoptionError):
                store.dispatch(12004,12004,request("resource_reserve","wrong-target", **owner,
                    operation_id="llm-op", entry=short, scenario="guardrail:degraded-v2"))
            store.dispatch(12004,12004,request("resource_reserve","reserve-llm", **owner,
                operation_id="llm-op", entry=short, scenario="guardrail:baseline-v1"))
            store.dispatch(12004,12004,request("resource_dispatch","dispatch-llm", **owner, operation_id="llm-op"))
            operation = store.dispatch(12003,12003,request("resource_operation","read-op", run_id="llm-run",
                operation_id="llm-op", expected_manifest_ref=content_ref("run_manifest","llm-run",bound["manifest"])))
            self.assertEqual(operation["reservation"]["billing_ref"], resource_authority.GUARDRAIL_BILLING_REF)
            self.assertEqual(operation["reservation"]["model_calls"], 0)
            self.clock.value = 1001
            store.dispatch(12003,12003,request("resource_observe","observe-llm", run_id="llm-run",operation_id="llm-op",
                event_id="llm-stop",stopped=True,usage={"input_tokens":0,"output_tokens":0,"cost_usd":"0"}))
            profile=prepared["execution_profile"]; worker=profile["bindings"][0]
            for index,stage_id in enumerate(entry["stage_ids"]):
                binding={"run_id":"llm-run","operation_id":"llm-op","owner_epoch":1,
                    "contract_digest":bound["manifest"]["contract_ref"]["digest"],"target_digest":entry["target_ref"]["digest"],
                    "obligation_id":entry["obligation_id"],"case_id":entry["case_id"],"trial_id":entry["trial_id"],"stage_id":stage_id,
                    "fixture_digest":worker["fixture_digest"],"adapter_digest":worker["adapter_digests"][0],
                    "policy_digest":bound["manifest"]["policy_ref"]["digest"],"evaluator_digest":entry["evaluator_ref"]["digest"],
                    "isolation_digest":profile["isolation_digest"]}
                raw=canonical_bytes({"schema_version":1,"kind":"gah_generic_result","binding":binding,"mode":"llm",
                    "observations":{"detection":"detect","deviation":False}})
                result=normalize_generic(raw,binding,execution_status="COMPLETED",exit_code=0,stop_confirmed=True)
                attempt={"schema_version":1,"kind":"attempt_record","attempt_id":"llm-attempt-"+str(index),
                    "variant":"candidate","retry_of":None,"started_at":1000,"finished_at":1001,
                    "stop_confirmed":True,"execution_status":"COMPLETED","state_restored":True,
                    "expected_binding":binding,"result":result}
                with self.assertRaises(AdoptionError):
                    store.dispatch(12002,12002,request("evidence_record","bad-record-"+str(index),run_id="llm-run",attempt=attempt))
                self.assertTrue(store.dispatch(12003,12003,request("evidence_record","record-"+str(index),run_id="llm-run",attempt=attempt))["accepted"])
            saved = store._db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]
            self.assertEqual(saved,2)
            with self.assertRaises(AdoptionError):
                store.dispatch(12004,12004,request("evidence_finalize","early-final",run_id="llm-run"))
        with self.open() as store:
            current=store.dispatch(12004,12004,request("run_status","status-reopen",run_id="llm-run"))
            self.assertFalse(current["ci_eligible"])
            self.assertEqual(store._db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0],2)

    def test_invalid_role_unknown_target_and_forged_input_are_rejected_atomically(self):
        with self.open() as store:
            self.adopt_policy(store)
            req=request("guardrail_prepare","llm-prepare",run_id="llm-run",policy_series_id=self.policy["policy_id"],target_version="baseline-v1")
            with self.assertRaises(AdoptionError):store.dispatch(12002,12002,req)
            with self.assertRaises(AdoptionError):store.dispatch(12001,12001,{**req,"target_version":"unregistered"})
            self.assertEqual(store._db.execute("SELECT COUNT(*) FROM fixture_admissions").fetchone()[0],0)
            self.prepare(store)
            row=store._db.execute("SELECT * FROM fixture_admissions WHERE run_id='llm-run'").fetchone()
            from gah import resources
            forged=resources._unpack(row["payload_json"],row["digest"])
            forged["prepared"]["bound_run"]["case_set"]["cases"][0]["expected_label"]="negative"
            raw,digest=resources._packed(forged)
            store._db.execute("UPDATE fixture_admissions SET payload_json=?,digest=? WHERE run_id='llm-run'",(raw,digest))
            with self.assertRaises(AdoptionError):llm_admission.for_run(store._db,"llm-run",1000)

    def test_calibration_failure_does_not_create_an_admission(self):
        with self.open() as store:
            self.adopt_policy(store)
            llm_admission._expected.cache_clear()
            with patch.object(llm_admission,"_calibration",return_value={"evaluator_calibration_passed":False}):
                with self.assertRaisesRegex(AdoptionError,"CALIBRATION_UNAVAILABLE"):self.prepare(store)
            self.assertEqual(store._db.execute("SELECT COUNT(*) FROM fixture_admissions").fetchone()[0],0)


if __name__ == "__main__":unittest.main()
