"""固定LLM集計の再利用が入力変更・再起動条件・呼出側の編集を隠さないことを検査する。"""
from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from tests import test_llm_target_revision as materials
from gah import aggregation,guardrail_results,evaluation_authority
from tests.test_guardrail_runner import worker


class AggregationCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        helper=materials.LlmTargetRevisionTests('runTest');helper.setUpClass()
        cls.prepared=helper.initial;cls.records=[]
        for i,entry in enumerate(cls.prepared['bound_run']['plan']['entries']):
            request=guardrail_results.for_entry(cls.prepared,entry,'cache-'+str(i),1)
            raw=worker.evaluate(request,clock=lambda:1000_000_000_000)
            for j,result in enumerate(guardrail_results.validate_bundle({'request':request,'worker_result':raw})):
                cls.records.append({'schema_version':1,'kind':'attempt_record','attempt_id':'cache-'+str(i)+'-'+str(j),
                    'variant':entry['variant'],'retry_of':None,'started_at':1000,'finished_at':1000,
                    'execution_status':'COMPLETED','stop_confirmed':True,'state_restored':True,
                    'expected_binding':result['binding'],'result':result})
    def test_repeated_complete_input_matches_uncached_and_returns_fresh_objects(self):
        aggregation._completed_aggregate.cache_clear()
        p=self.prepared
        expected=aggregation._aggregate_uncached(p['bound_run'],self.records,execution_profile=p['execution_profile'])
        with patch.object(aggregation,'_aggregate_uncached',wraps=aggregation._aggregate_uncached) as function:
            first=aggregation.aggregate(p['bound_run'],self.records,execution_profile=p['execution_profile'])
            self.assertEqual(first,expected);first['integrity_failure']=True
            second=aggregation.aggregate(p['bound_run'],self.records,execution_profile=p['execution_profile'])
            self.assertEqual(second,expected);self.assertEqual(function.call_count,1)
            changed=deepcopy(self.records);changed[0]['expected_binding']['target_digest']='f'*64
            altered=aggregation.aggregate(p['bound_run'],changed,execution_profile=p['execution_profile'])
            self.assertTrue(altered['integrity_failure']);self.assertFalse(expected['integrity_failure']);self.assertEqual(function.call_count,2)
            with patch.object(evaluation_authority,'_source_digest',return_value='f'*64):
                aggregation.aggregate(p['bound_run'],self.records,execution_profile=p['execution_profile'])
            self.assertEqual(function.call_count,3)
    def test_non_json_container_types_do_not_become_valid_after_caching(self):
        from gah.contracts import ContractError
        p=self.prepared;changed=deepcopy(self.records);changed[0]=type('RecordSubclass',(dict,),{})(changed[0])
        with self.assertRaises(ContractError):aggregation.aggregate(p['bound_run'],changed,execution_profile=p['execution_profile'])
        from gah.cache_inputs import plain
        self.assertFalse(plain((p['bound_run'],)));self.assertFalse(plain({1:'not-a-json-object-key'}))

    def test_incomplete_evidence_is_not_replaced_by_completed_cache(self):
        p=self.prepared
        aggregation.aggregate(p['bound_run'],self.records,execution_profile=p['execution_profile'])
        partial=aggregation.aggregate(p['bound_run'],self.records[:-1],execution_profile=p['execution_profile'])
        self.assertTrue(partial['required_missing'])


if __name__=='__main__':unittest.main()
