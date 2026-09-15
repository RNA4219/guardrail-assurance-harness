"""除外審査の独立主体、採択前後、失効、不変保存の境界。"""
from copy import deepcopy
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from gah.adoption import AdoptionError,AdoptionStore
from gah.evaluation_authority import EvaluationExtension
from gah.policy import initial_policy_profile
from gah.run_contracts import content_ref
from gah.wire import canonical_bytes
from tests import test_aggregation as helpers


class MutationReviewBoundaryTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup);self.now=1000
        self.store=AdoptionStore(Path(folder.name)/"authority.sqlite",clock=lambda:self.now,
            bootstrap_policy=initial_policy_profile(),validator_digest="b"*64,extension=EvaluationExtension())
        self.addCleanup(self.store.close)
        f,plan,bound=helpers._bound()
        f["contract_ref_digest"]=bound["manifest"]["contract_ref"]["digest"]
        f["policy_ref_digest"]=bound["manifest"]["policy_ref"]["digest"]
        binding=helpers._binding(f,plan,"obligation-mutation")
        result=helpers._result(binding,"mutation",observation="PASS",mutation_outcome="ERROR",error_class="MUTATION_NOT_APPLIED")
        attempt=helpers._record(binding,attempt_id="invalid-mutant",result=result)
        raw=canonical_bytes(attempt);self.attempt=attempt
        from gah.run_evidence import RunEvidenceBook,bound_bundle_digest
        allowed={"run-1":bound_bundle_digest(bound)}
        profile={"fixture_digest":helpers.ZERO,"adapter_digests":["1"*64],"isolation_digest":"2"*64}
        self.store._db.execute("BEGIN IMMEDIATE")
        RunEvidenceBook(self.store._db,now=100,allowed_bindings=allowed).start_run(bound,profile)
        RunEvidenceBook(self.store._db,now=111,allowed_bindings=allowed).record_attempt(attempt)
        self.store._db.commit()
        self.evidence_ref=content_ref("evidence","run-1",{"fixed":True})
        self.source={"bound":bound,"receipt":{"evidence_ref":self.evidence_ref,"manifest_ref":content_ref("run_manifest","run-1",bound["manifest"]),
            "created_at":900,"decision_ref":content_ref("run_decision","run-1",{"assurance":"UNKNOWN"})},
            "evidences":[{"valid_until":10000}],"reasons":[]}
        # このクラスは認証・不変保存の境界。実測Evidenceの接続は統合試験で検査する。
        stub=patch.object(self.store._extension,"_baseline_source",side_effect=lambda *a,**k:deepcopy(self.source))
        stub.start();self.addCleanup(stub.stop)
    def request(self,action,name,**fields):
        return {"schema_version":1,"action":"mutation_review_"+action,"request_id":name,"run_id":"run-1",
            "expected_evidence_ref":deepcopy(self.evidence_ref),**fields}
    def validate(self,name="validate"):
        return self.store.dispatch(12003,12003,self.request("validate",name,attempt_id="invalid-mutant",rationale="固定Mutationの不成立を確認"))
    def approve(self,value,name="approve"):
        return self.store.dispatch(12001,12001,self.request("approve",name,validation_ref=value["validation_ref"]))
    def current(self):return self.store.dispatch(12004,12004,self.request("current","current"))

    def test_validator_and_manager_are_separate_and_original_error_is_immutable(self):
        before=self.store._db.execute("SELECT attempt_json FROM attempts").fetchone()[0]
        validation=self.validate();self.assertFalse(validation["ci_eligible"])
        pending=self.current();self.assertEqual(pending["counts"]["candidate"],{"approved_exclusions":0,"pending_exclusions":1})
        approval=self.approve(validation);current=self.current()
        self.assertEqual(current["items"][0]["status"],"EXCLUDED")
        self.assertEqual(current["counts"]["candidate"],{"approved_exclusions":1,"pending_exclusions":0})
        self.assertFalse(current["ci_eligible"]);self.assertTrue(current["original_decision_unchanged"])
        self.assertTrue(current["required_obligations_unchanged"])
        self.assertEqual(self.validate(),validation);self.assertEqual(self.approve(validation),approval)
        self.assertEqual(self.store._db.execute("SELECT attempt_json FROM attempts").fetchone()[0],before)
        self.assertEqual(current["items"][0]["approval"]["approved_by"]["actor_id"],"manager")

    def test_wrong_roles_wrong_evidence_and_unsupported_basis_are_rejected(self):
        request=self.request("validate","wrong",attempt_id="invalid-mutant",rationale="確認")
        for uid in (12001,12002,12004):
            with self.subTest(uid=uid),self.assertRaisesRegex(AdoptionError,"AUTHORITY_DENIED"):
                self.store.dispatch(uid,uid,request)
        bad=deepcopy(request);bad["expected_evidence_ref"]["digest"]="f"*64
        with self.assertRaisesRegex(AdoptionError,"BINDING_MISMATCH"):self.store.dispatch(12003,12003,bad)
        attempt=deepcopy(self.attempt);attempt["result"].update(mutation_outcome="SURVIVED",error_class=None)
        raw=canonical_bytes(attempt);self.store._db.execute("UPDATE attempts SET attempt_json=?,attempt_digest=?",(raw.decode(),hashlib.sha256(raw).hexdigest()))
        with self.assertRaisesRegex(AdoptionError,"BASIS_UNSUPPORTED"):self.validate()

    def test_validator_cannot_self_approve_and_duplicate_new_requests_conflict(self):
        value=self.validate();request=self.request("approve","denied",validation_ref=value["validation_ref"])
        for uid in (12003,12002,12004):
            with self.subTest(uid=uid),self.assertRaisesRegex(AdoptionError,"AUTHORITY_DENIED"):self.store.dispatch(uid,uid,request)
        with self.assertRaisesRegex(AdoptionError,"MUTATION_REVIEW_CONFLICT"):self.validate("duplicate")
        self.approve(value)
        with self.assertRaisesRegex(AdoptionError,"MUTATION_REVIEW_CONFLICT"):self.approve(value,"duplicate-approve")

    def test_revocation_and_expiry_return_pending_without_rewriting_approval(self):
        value=self.validate();approval=self.approve(value)
        self.source["reasons"]=["EVIDENCE_REVOKED"]
        current=self.current();self.assertEqual(current["items"][0]["status"],"EXCLUSION_PENDING")
        self.assertEqual(self.approve(value),approval)
        self.source["reasons"]=[];self.now=10001
        current=self.current();self.assertEqual(current["counts"]["candidate"]["approved_exclusions"],0)
        self.assertEqual(current["items"][0]["approval"],approval["approval"])

    def test_forged_validation_or_approval_origin_is_rejected(self):
        value=self.validate();self.approve(value)
        self.store._db.execute("UPDATE idempotency SET actor_id='validator' WHERE request_id='approve'")
        with self.assertRaisesRegex(AdoptionError,"MUTATION_REVIEW_ORIGIN_INVALID"):self.current()

    def test_insert_failure_rolls_back_and_same_request_can_retry(self):
        self.store._db.execute("CREATE TRIGGER reject_review BEFORE INSERT ON authority_artifacts WHEN NEW.kind='mutation_exclusion_validation' BEGIN SELECT RAISE(ABORT,'fixed'); END")
        with self.assertRaises(AdoptionError):self.validate()
        self.assertIsNone(self.store._db.execute("SELECT 1 FROM idempotency WHERE request_id='validate'").fetchone())
        self.store._db.execute("DROP TRIGGER reject_review")
        self.assertFalse(self.validate()["ci_eligible"])

    def test_cli_checks_record_bindings_counts_roles_and_never_grants_ci(self):
        from tools.gah_mutation_review import execute
        from unittest.mock import Mock
        runtime=Mock();runtime.client.side_effect=lambda uid,request:self.store.dispatch(uid,uid,request)
        validation,code=execute(runtime,self.request("validate","cli-validation",attempt_id="invalid-mutant",rationale="固定根拠"))
        self.assertEqual(code,0);self.assertEqual(runtime.client.call_args.args[0],12003)
        approval,code=execute(runtime,self.request("approve","cli-approval",validation_ref=validation["validation_ref"]))
        self.assertEqual(code,0);self.assertEqual(runtime.client.call_args.args[0],12001)
        current_request=self.request("current","cli-current");current,code=execute(runtime,current_request)
        self.assertEqual(code,0);self.assertEqual(runtime.client.call_args.args[0],12004);self.assertFalse(current["ci_eligible"])
        runtime.client.side_effect=None
        for change in ("ci","counts","reference"):
            forged=deepcopy(current)
            if change=="ci":forged["ci_eligible"]=True
            elif change=="counts":forged["counts"]["candidate"]["approved_exclusions"]=True
            else:forged["items"][0]["validation_ref"]["digest"]="f"*64
            runtime.client.return_value=forged
            with self.subTest(change=change),self.assertRaises(ValueError):execute(runtime,current_request)

    def test_source_change_during_review_rolls_back_and_invalidates_subsequent_reads(self):
        from gah import evaluation_authority,read_checks
        with patch.object(read_checks,"_source_invalid",False):
            with patch.object(evaluation_authority,"_compute_source_digest",side_effect=["a"*64,"b"*64]):
                with self.assertRaisesRegex(AdoptionError,"EXTENSION_INVALID"):self.validate()
            self.assertIsNone(self.store._db.execute("SELECT 1 FROM authority_artifacts WHERE kind='mutation_exclusion_validation'").fetchone())
            with self.assertRaisesRegex(AdoptionError,"EXTENSION_INVALID"):self.current()


if __name__=="__main__":unittest.main()
