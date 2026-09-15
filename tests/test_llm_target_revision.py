"""固定対象の版差を旧根拠と分離し、二つの版の実入力結果を集計する部品試験。"""
from copy import deepcopy
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from gah import llm_materialization as materials,llm_transitions,guardrail_runtime,guardrail_results,aggregation
from gah.baseline_authority import build_candidate
from gah.contracts import ContractError
from gah.policy import initial_policy_profile
from gah.docker_runner import PROFILE
from gah.run_contracts import content_ref
from tests.test_guardrail_runner import worker


class LlmTargetRevisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        lock=guardrail_runtime.read_lock()
        cls.args={'policy_generation':1,'run_id':'revision-source','now':1000,'target_version':'baseline-v1',
            'image_id':lock['image_id'],'worker_digest':lock['worker_digest'],'isolation_profile':PROFILE}
        cls.initial=materials.build(initial_policy_profile(),**cls.args)
        bound=cls.initial['bound_run']
        cls.baseline=build_candidate({'bound':bound,'receipt':{},'decision':{'diagnostic':True},'closure':{'diagnostic':True},
            'evidences':[{'evidence_id':'diagnostic-evidence','valid_until':100000}]},'diagnostic-baseline','diagnostic-proposal',1000)['record']
        cls.context={'baseline_ref':content_ref('baseline',cls.baseline['baseline_id'],cls.baseline),
            'targets':[{'control_id':c['control_id'],'target_ref':c['target_ref']} for c in bound['registry']['controls']]}
        cls.changed=materials.build(initial_policy_profile(),**{**cls.args,'run_id':'revision-new','target_version':'degraded-v2','generation':2,'baseline_context':cls.context})

    def build(self,following=None,registry=None):
        return llm_transitions.build(self.initial['bound_run']['contract'],following or self.changed['bound_run']['contract'],
            baseline_record=self.baseline,source_prepared=self.initial,now=1001,old_run_id='old-check',new_run_id='new-check',
            following_registry=registry or self.changed['bound_run']['registry'])

    def test_target_only_revision_preserves_old_conditions_and_resolves_each_variant(self):
        runs=self.build();old,new=runs['old'],runs['new']
        self.assertEqual(old['bound_run']['registry'],self.initial['bound_run']['registry'])
        self.assertEqual(new['bound_run']['case_set'],old['bound_run']['case_set'])
        self.assertEqual(new['evaluator_document'],old['evaluator_document'])
        self.assertEqual(new['bound_run']['contract']['comparison']['changed_axes'],['target'])
        self.assertFalse(runs['authority_connected']);self.assertFalse(runs['ci_eligible'])
        for variant,version in [('baseline','baseline-v1'),('candidate','degraded-v2')]:
            entry=next(e for e in new['bound_run']['plan']['entries'] if e['variant']==variant)
            req=guardrail_results.for_entry(new,entry,'revision-'+variant,1)
            self.assertEqual(req['target']['behavior_version'],version)
            self.assertEqual(req['stages'][0]['binding']['target_digest'],entry['target_ref']['digest'])

    def test_removing_requirement_and_undeclared_target_change_are_rejected(self):
        following=deepcopy(self.changed['bound_run']['contract']);following['comparison']['changed_axes']=[]
        with self.assertRaisesRegex(ContractError,'COMPARISON_MISMATCH'):self.build(following=following)
        registry=deepcopy(self.changed['bound_run']['registry']);registry['controls'][0]['obligations'][0]['required']=False
        following=deepcopy(self.changed['bound_run']['contract']);following['registry_ref']=content_ref('control_registry',registry['registry_id'],registry)
        with self.assertRaisesRegex(ContractError,'UNDECLARED_CONTRACT_CHANGE'):self.build(following=following,registry=registry)

    def test_fixed_pack_and_candidate_cache_do_not_share_mutable_result_objects(self):
        first=materials.fixed_pack();first['case_sets']['acceptance']['cases'][0]['expected_label']='unknown'
        self.assertNotEqual(materials.fixed_pack()['case_sets']['acceptance']['cases'][0]['expected_label'],'unknown')
        first=self.build();first['new']['bound_run']['registry']['controls'][0]['owner']='changed'
        self.assertEqual(self.build()['new']['bound_run']['registry']['controls'][0]['owner'],'manager')

    def test_saved_bundle_digest_requires_actual_baseline_targets(self):
        from gah.run_evidence import bound_bundle_digest, EvidenceError
        new=self.build()['new']
        expected=content_ref('bound_bundle',new['bound_run']['manifest']['run_id'],new['bound_run'])['digest']
        self.assertEqual(bound_bundle_digest(new['bound_run'],new['baseline_context']),expected)
        with self.assertRaises(EvidenceError):bound_bundle_digest(new['bound_run'])
        changed=deepcopy(new['baseline_context']);changed['targets'][0]['target_ref']=new['bound_run']['registry']['controls'][0]['target_ref']
        with self.assertRaises(EvidenceError):bound_bundle_digest(new['bound_run'],changed)

    def test_fixed_two_versions_produce_expected_counts_without_stage_overlap(self):
        new=self.build()['new'];records=[]
        for index,entry in enumerate(reversed(new['bound_run']['plan']['entries'])):
            request=guardrail_results.for_entry(new,entry,'pure-operation-'+str(index),1)
            ticks=iter([1001_000_000_000,1002_000_000_000,1003_000_000_000,1004_000_000_000])
            result=worker.evaluate(request,clock=lambda:next(ticks))
            normalized=guardrail_results.validate_bundle({'request':request,'worker_result':result})
            for step,(observed,timing) in enumerate(zip(normalized,result['stage_timings'])):
                records.append({'schema_version':1,'kind':'attempt_record','attempt_id':'pure-attempt-'+str(index)+'-'+str(step),
                    'variant':entry['variant'],'retry_of':None,'started_at':timing['started_at'],'finished_at':timing['finished_at'],
                    'stop_confirmed':True,'execution_status':'COMPLETED','state_restored':True,'expected_binding':observed['binding'],'result':observed})
        result=aggregation.aggregate(new['bound_run'],records,execution_profile=new['execution_profile'],baseline_context=new['baseline_context'])
        self.assertFalse(result['integrity_failure']);self.assertFalse(result['required_missing'])
        for variant,expected in [('baseline',{'tp':196,'fn':4,'fp':4,'tn':196}),('candidate',{'tp':184,'fn':16,'fp':2,'tn':198})]:
            actual=result['counts']['variant'][variant]
            self.assertEqual({key:actual[key] for key in expected},expected)
        self.assertFalse(result['ci_eligible'])


if __name__=='__main__':unittest.main()
