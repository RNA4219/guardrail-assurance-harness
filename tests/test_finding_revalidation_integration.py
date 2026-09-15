"""固定400ケースの対象差・修復・再発を、認証SQLiteの新しい証拠へ通す。"""
from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import shutil
import sqlite3
import unittest
from tests import test_llm_revision_integration as revision
from tests import test_llm_supervision_integration as supervisor
from tests import test_supervised_run as runtime_helpers
from gah.adoption import AdoptionError,AdoptionStore
from gah.evaluation_authority import EvaluationExtension
from gah.policy import initial_policy_profile
from gah.run_contracts import content_ref
from tools.gah_finding import execute as finding_execute
from tools.gah_report import build_report,render_markdown
request=revision.request


class FindingRevalidationIntegrationTests(unittest.TestCase):
    candidate=revision.LlmRevisionIntegrationTests.candidate
    setUp=revision.LlmRevisionIntegrationTests.setUp
    at=1000
    def open(self):
        return AdoptionStore(self.path,clock=lambda:self.at,bootstrap_policy=initial_policy_profile(),
            validator_digest='b'*64,extension=EvaluationExtension())
    @classmethod
    def setUpClass(cls):
        revision.LlmRevisionIntegrationTests.setUpClass.__func__(cls)
        helper=cls('runTest');helper.setUp();cls.addClassCleanup(helper.doCleanups)
        revision.LlmRevisionIntegrationTests.test_new_target_is_measured_against_baseline_and_degraded_result_is_not_adopted(helper)
        with helper.open() as store:
            rows=store._db.execute("SELECT payload_json FROM authority_artifacts WHERE kind='finding' AND run_id='delta-new'")
            findings=[json.loads(row[0]) for row in rows]
            cls.finding=next(f for f in findings if f.get('status')=='OPEN' and f.get('category')=='degradation')
            cls.finding_ref=content_ref('finding',cls.finding['finding_id'],cls.finding)
            cls.original_contract_generation=store._db.execute("SELECT generation FROM eval_current WHERE series_id='llm-series'").fetchone()[0]
            store.dispatch(12003,12003,helper.candidate('repair'))
            prepared=store.dispatch(12004,12004,request('contract_candidate_read','repair-new-read',candidate_id='repair',side='new'))['prepared']
            store.dispatch(12004,12004,request('contract_candidate_begin','repair-new-begin',candidate_id='repair',side='new'))
            cls.fixed_bound=prepared['bound_run']
            runtime=runtime_helpers.Runtime(store)
            current,_=finding_execute(runtime,helper.req('finding_current','before'))
            started,_=finding_execute(runtime,helper.req('finding_start','start',expected_state_ref=current['state_ref']))
            control=next(c for c in cls.fixed_bound['registry']['controls'] if c['control_id']==cls.finding['control_ref']['id'])
            pending,code=finding_execute(runtime,helper.req('finding_request_revalidation','request',expected_state_ref=started['state_ref'],
                new_run_ref=content_ref('run_manifest','repair-new',cls.fixed_bound['manifest']),changed_target_ref=control['target_ref']))
            helper.assertEqual(code,0);helper.assertEqual(pending['state']['status'],'AWAITING_REVALIDATION')
            proof_request=helper.req('finding_confirm','confirm',expected_state_ref=pending['state_ref'])
            for uid in (12001,12002,12004):
                with helper.assertRaisesRegex(AdoptionError,'AUTHORITY_DENIED'):store.dispatch(uid,uid,proof_request)
            with helper.assertRaises(AdoptionError):store.dispatch(12003,12003,proof_request)
            receipt=supervisor.complete_side(store,'repair','new')
            helper.assertEqual(receipt['assurance'],'HEALTHY')
            confirmed,code=finding_execute(runtime,proof_request)
            helper.assertEqual(code,0,confirmed);helper.assertEqual(confirmed['state']['status'],'VERIFIED')
            current,_=finding_execute(runtime,helper.req('finding_current','verified'))
            helper.assertTrue(current['verification_current']);helper.assertFalse(current['ci_eligible'])
            cls.confirmed=current
            saved=Path(cls.folder.name)/'verified-seed.sqlite'
            with closing(sqlite3.connect(saved)) as out:store._db.backup(out)
            cls.seed=saved
    def req(self,action,name,**fields):
        return request(action,'repair-finding-'+name,run_id='delta-new',finding_ref=self.finding_ref,**fields)
    def current(self,store):return store.dispatch(12004,12004,self.req('finding_current','current'))
    def test_confirmed_state_restarts_and_candidate_report_preserves_ci_rejection(self):
        with self.open() as store:
            state=self.current(store);self.assertEqual(state['state'],self.confirmed['state']);self.assertTrue(state['verification_current'])
            source=EvaluationExtension()._bound_evidence_run(store,store._db,'delta-new',self.at)[0]
            m=source['manifest']
            gate=request('ci_check','repair-source-report',run_id='delta-new',expected_manifest_ref=content_ref('run_manifest','delta-new',m),
                expected_contract_ref=m['contract_ref'],expected_baseline_ref=m['baseline_ref'],expected_target_refs=m['target_refs'],expected_use_cases=m['use_cases'])
            report=build_report(runtime_helpers.Runtime(store),gate,candidate=True)
            self.assertFalse(report['ci_eligible']);self.assertEqual(report['observed_assurance'],'DEGRADED')
            managed=next(item for item in report['finding_management'] if item['finding_ref']==self.finding_ref)
            self.assertTrue(managed['verification_current']);self.assertIn('VERIFIED',render_markdown(report))
            self.assertEqual(store._db.execute("SELECT generation FROM eval_current WHERE series_id='llm-series'").fetchone()[0],self.original_contract_generation)
    def test_revoked_new_evidence_invalidates_current_confirmation_without_rewriting_history(self):
        with self.open() as store:
            before=self.current(store)
            store.dispatch(12004,12004,request('evidence_revoke','revoke-repair-evidence',run_id='repair-new'))
            after=self.current(store);self.assertEqual(after['state'],before['state']);self.assertFalse(after['verification_current'])
            self.assertTrue(after['reasons'])
    def test_deleted_new_evidence_keeps_the_confirmation_history_but_cannot_verify_now(self):
        with self.open() as store:
            before=self.current(store);ref=before['state']['confirmation']['evidence_ref']
            fields={'run_id':'repair-new','expected_evidence_ref':ref}
            state=store.dispatch(12004,12004,request('evidence_retention_state','repair-retention-state',**fields))
            plan=store.dispatch(12001,12001,request('evidence_retention_plan','repair-retention-plan',**fields,
                expected_hold_ref=state['hold_ref'],reason='EXPLICIT_REMOVAL'))
            store.dispatch(12004,12004,request('evidence_retention_apply','repair-retention-apply',**fields,plan_ref=plan['plan_ref']))
            after=self.current(store)
            self.assertEqual(after['state'],before['state']);self.assertFalse(after['verification_current'])
            self.assertIn('EVIDENCE_DELETED',after['reasons'])
            self.assertIsNone(store._db.execute("SELECT 1 FROM authority_artifacts WHERE kind='evidence' AND id='repair-new'").fetchone())
            store._db.execute("UPDATE idempotency SET actor_id='manager' WHERE request_id='repair-retention-apply'")
            with self.assertRaises(AdoptionError):self.current(store)
    def test_forged_confirmation_origin_is_rejected(self):
        with self.open() as store:
            store._db.execute("UPDATE idempotency SET actor_id='operator' WHERE request_id='repair-finding-confirm'")
            with self.assertRaisesRegex(AdoptionError,'FINDING_ORIGIN_INVALID'):self.current(store)
    def test_newly_observed_recurrence_keeps_a_link_to_the_original_finding(self):
        self.at=1001
        with self.open() as store:
            candidate=self.candidate('recurrence');candidate['proposal_id']='delta-proposal'
            store.dispatch(12003,12003,candidate)
            result=supervisor.complete_side(store,'recurrence','new',now=self.at)
            self.assertEqual(result['assurance'],'DEGRADED')
            rows=store._db.execute("SELECT payload_json FROM authority_artifacts WHERE kind='finding' AND run_id='recurrence-new'")
            new=next(f for f in (json.loads(r[0]) for r in rows) if f.get('status')=='OPEN'
                and f['control_ref']['id']==self.finding['control_ref']['id'] and f['reason_code']==self.finding['reason_code'] and f['metric_id']==self.finding['metric_id'])
            bound=EvaluationExtension()._bound_evidence_run(store,store._db,'recurrence-new',self.at)[0]
            before=self.current(store)
            linked=store.dispatch(12004,12004,self.req('finding_recur','recurrence',expected_state_ref=before['state_ref'],
                new_run_ref=content_ref('run_manifest','recurrence-new',bound['manifest']),new_finding_ref=content_ref('finding',new['finding_id'],new)))
            self.assertEqual(linked['state']['recurrences'][0]['parent_finding_ref'],self.finding_ref)
            self.assertEqual(linked['state']['recurrences'][0]['new_finding_ref']['id'],new['finding_id'])
            self.assertEqual(linked['state']['status'],'VERIFIED');self.assertFalse(linked['ci_eligible'])
            self.assertEqual(self.current(store)['state'],linked['state'])


if __name__=='__main__':unittest.main()
