"""現在Evidenceの再照合は呼出元の役割を引き継ぎ、失敗を隠さない。"""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from gah.adoption import AdoptionError
from gah.evaluation_authority import EvaluationExtension
from gah import assurance_authority, finding_lifecycle


class SourceActorContextTests(unittest.TestCase):
    def test_source_check_uses_the_actual_actor_and_context(self):
        extension=EvaluationExtension()
        for actor in ('manager','validator','operator'):
            source={'reasons':[]}
            with patch.object(assurance_authority,'baseline_source',return_value=source), \
                 patch.object(extension,'_check_start') as check:
                self.assertIs(extension._baseline_source(None,None,'saved',1000,
                    actor_id=actor,context=actor+'-context'),source)
                check.assert_called_once_with(None,None,'saved',1000,actor_id=actor,context=actor+'-context')

    def test_missing_actor_is_not_replaced_by_a_privileged_role(self):
        extension=EvaluationExtension()
        with patch.object(assurance_authority,'baseline_source',return_value={'reasons':[]}), \
             patch.object(extension,'_check_start',side_effect=AdoptionError('AUTHORITY_DENIED')) as check:
            result=extension._baseline_source(None,None,'saved',1000)
            self.assertEqual(result['reasons'],['ADOPTED_CONDITIONS_UNAVAILABLE'])
            self.assertIsNone(check.call_args.kwargs['actor_id'])

    def test_revoked_or_invalid_conditions_still_block_source_use(self):
        extension=EvaluationExtension()
        for reason in ('CANDIDATE_EXPIRED','POLICY_REVOKED','BASELINE_REVOKED','BINDING_MISMATCH'):
            with patch.object(assurance_authority,'baseline_source',return_value={'reasons':['EVIDENCE_REVOKED']}), \
                 patch.object(extension,'_check_start',side_effect=AdoptionError(reason)):
                result=extension._baseline_source(None,None,'saved',1000,actor_id='validator',context='validator-context')
                self.assertEqual(result['reasons'],['EVIDENCE_REVOKED','ADOPTED_CONDITIONS_UNAVAILABLE'])

    def test_finding_route_passes_authenticated_caller_into_resolver(self):
        extension=EvaluationExtension();saved={'reasons':[]}
        def finding(store,db,request,actor,context,now,resolve_source,resolve_bound):
            self.assertEqual(resolve_source('saved'),saved)
            return {'ci_eligible':False}
        request={'action':'finding_confirm','request_id':'actor-flow'}
        with patch.object(extension,'_baseline_source',return_value=saved) as resolve, \
             patch.object(finding_lifecycle,'execute',side_effect=finding):
            # request wrapperは別のtransaction/source scope試験で検証する。
            extension._execute.__wrapped__(extension,None,None,request,'validator','validator-context',1000)
            resolve.assert_called_once_with(None,None,'saved',1000,actor_id='validator',context='validator-context')


if __name__=='__main__':unittest.main()
