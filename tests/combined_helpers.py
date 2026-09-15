"""同じ認証DBへ、独立した固定UC-CI契約とbaselineを追加する試験fixture。"""
from copy import deepcopy
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from tests import test_regression_integration as regression
from gah.run_contracts import content_ref
from gah.policy import initial_policy_profile
request=regression.request


def add_ci_contract(store):
    policy=initial_policy_profile();run_id='combined-ci-source'
    response=store.dispatch(12001,12001,request('fixture_prepare','combined-ci-prepare',run_id=run_id,policy_series_id=policy['policy_id']))
    from gah import fixture_admission,fixture_materialization
    worker_source,lock,profile=fixture_admission.execution_context()
    material=fixture_materialization.materialize_fixture_manifest(bound_run=response['prepared']['bound_run'],
        worker_source=worker_source,runtime_lock=lock,execution_profile=profile,now=1000)
    if material['manifest_ref']!=response['materialization_ref']:raise AssertionError('MATERIALIZATION_REF_MISMATCH')
    prepared={**response['prepared'],'materialization':material};contract=prepared['bound_run']['contract']
    for uid,action,extra in (
        (12001,'contract_propose',{'proposal_id':'combined-ci-proposal','series_id':'fixture-contract-series','expected_generation':0,'contract':contract}),
        (12003,'contract_validate',{'proposal_id':'combined-ci-proposal','validation_id':'combined-ci-validation'}),
        (12001,'contract_adopt',{'proposal_id':'combined-ci-proposal','validation_id':'combined-ci-validation','expected_generation':0})):
        store.dispatch(uid,uid,request(action,'combined-ci-'+action,**extra))
    helper=regression.RegressionIntegrationTests('runTest');helper.store=store;helper.clock=SimpleNamespace(value=1000)
    spec=importlib.util.spec_from_file_location('combined_ci_worker',Path(__file__).resolve().parents[1]/'fixtures/runtime/fixture_worker.py')
    helper.worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper.worker)
    if helper.complete(prepared)['assurance']!='HEALTHY':raise AssertionError('CI_SOURCE_NOT_HEALTHY')
    for uid,action,extra in (
        (12001,'baseline_propose',{'proposal_id':'combined-ci-baseline-proposal','series_id':'fixture-baseline-series','run_id':run_id,'expected_generation':0}),
        (12003,'baseline_validate',{'proposal_id':'combined-ci-baseline-proposal','validation_id':'combined-ci-baseline-validation'}),
        (12001,'baseline_adopt',{'proposal_id':'combined-ci-baseline-proposal','validation_id':'combined-ci-baseline-validation','expected_generation':0})):
        store.dispatch(uid,uid,request(action,'combined-ci-'+action,**extra))
    baseline=store.dispatch(12004,12004,request('baseline_current','combined-ci-baseline-current',series_id='fixture-baseline-series'))['baseline']
    following=deepcopy(contract);following.update(contract_id='combined-ci-contract-v2',generation=2,
        comparison={'mode':'required','baseline_ref':content_ref('baseline',baseline['baseline_id'],baseline),'changed_axes':[],'reason':None})
    store.dispatch(12001,12001,request('contract_propose','combined-ci-following-propose',proposal_id='combined-ci-following-proposal',series_id='fixture-contract-series',expected_generation=1,contract=following))
    candidate=store.dispatch(12003,12003,request('contract_candidate_prepare','combined-ci-candidate-prepare',candidate_id='combined-ci-candidate',
        proposal_id='combined-ci-following-proposal',baseline_series_id='fixture-baseline-series',
        expected_contract_ref=content_ref('evaluation_contract',contract['contract_id'],contract),
        expected_baseline_ref=content_ref('baseline',baseline['baseline_id'],baseline),old_run_id='combined-ci-old',new_run_id='combined-ci-new'))
    for side in ('old','new'):
        def begin(bundle,side=side):
            tag='combined-ci-'+side;rid=bundle['bound_run']['manifest']['run_id']
            store.dispatch(12004,12004,request('contract_candidate_begin',tag+'-begin',candidate_id='combined-ci-candidate',side=side))
            store.dispatch(12004,12004,request('evidence_open',tag+'-open',run_id=rid))
            return {'run_id':rid,'owner_id':tag+'-begin','owner_epoch':1}
        helper.begin=begin
        if helper.complete(candidate['runs'][side])['assurance']!='HEALTHY':raise AssertionError('CI_CANDIDATE_NOT_HEALTHY')
    store.dispatch(12003,12003,request('contract_candidate_validate','combined-ci-candidate-validate',candidate_id='combined-ci-candidate',validation_id='combined-ci-candidate-valid'))
    adopted=store.dispatch(12001,12001,request('contract_candidate_adopt','combined-ci-candidate-adopt',candidate_id='combined-ci-candidate',validation_id='combined-ci-candidate-valid',expected_contract_generation=1,expected_baseline_generation=1))
    if not adopted['adoption_verified']:raise AssertionError('CI_ADOPTION_MISSING')
    return content_ref('evaluation_contract',following['contract_id'],following),helper.worker
