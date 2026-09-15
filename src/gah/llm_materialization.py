"""自作guardrailの実入力・対象版・評価器・二段階planを事前契約へ結ぶ。"""
from copy import deepcopy
from functools import lru_cache
import hashlib
from pathlib import Path
from .contracts import ContractError, MAX_INTEGER, require_digest, require_id, require_uint
from .evaluation_data import build_pack
from .policy import validate_policy_profile
from .run_contracts import bind_run_manifest, content_ref
from . import execution_profiles
from .wire import canonical_bytes

ROOT=Path(__file__).resolve().parents[2]
VERSIONS={"baseline-v1", "degraded-v2"}
CATEGORIES=("data_handling", "work_scope")


def _sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def target_document(target_version, image_id):
    return {'schema_version':1,'kind':'synthetic_guardrail_target','target_id':'synthetic-guardrail',
        'behavior_version':target_version,'source_digest':_sha(ROOT/'fixtures/llm/guardrail_target.py'),
        'runtime_image_id':image_id,'trained_model':False}


def source_key():
    from .evaluation_authority import _source_digest
    return _source_digest()


@lru_cache(maxsize=2)
def _fixed_pack(key,builder):
    return builder()


def fixed_pack():
    """固定factoryだけを再利用し、呼出し側には独立したコピーを返す。"""
    return deepcopy(_fixed_pack(source_key(),build_pack))


@lru_cache(maxsize=2)
def _evaluator_document(key,builder):
    pack = _fixed_pack(key,builder)
    return {'schema_version':1,'kind':'llm_evaluator_implementation','evaluator_id':'finite-llm-evaluator',
        'source_sha256':{name:_sha(ROOT/'src/gah'/name) for name in ('llm_evaluator.py','normalized.py','measurement_calibration.py')},
        'oracle_pack_ref':content_ref('evaluation_pack',pack['pack_id'],pack)}


def evaluator_document():
    return deepcopy(_evaluator_document(source_key(),build_pack))


def build(policy, *, policy_generation, run_id, now, target_version, image_id, worker_digest,
          isolation_profile, generation=1, baseline_context=None):
    """構造を固定する部品。image実体の確認・採択・資源予約は上位で行う。"""
    policy=validate_policy_profile(policy)
    for value in (policy_generation, now, generation):require_uint(value)
    require_id(run_id);require_digest(worker_digest)
    if not policy_generation or not generation or type(target_version) is not str or target_version not in VERSIONS:
        raise ContractError('MATERIALIZATION_INVALID')
    if type(image_id) is not str or not image_id.startswith('sha256:'):raise ContractError('RUNTIME_IMAGE_INVALID')
    require_digest(image_id[7:])
    if type(isolation_profile) is not dict or not isolation_profile:raise ContractError('ISOLATION_PROFILE_INVALID')
    if now > MAX_INTEGER-policy['profiles']['full']['elapsed_seconds']:raise ContractError('TIME_RANGE')
    if (generation==1)!=(baseline_context is None):raise ContractError('BASELINE_CONTEXT_REQUIRED')
    pack=fixed_pack();cases=pack['case_sets']['acceptance'];calibration=pack['case_sets']['calibration']
    target_doc = target_document(target_version, image_id)
    target_ref = content_ref('target',target_doc['target_id'],target_doc)
    evaluator_doc = evaluator_document()
    evaluator_ref = content_ref('evaluator',evaluator_doc['evaluator_id'],evaluator_doc)
    controls=[{'control_id':'LC-guardrail','owner':'manager','invariant':'固定集合の検出率・誤検知と段階別の禁止事象を評価する',
        'criticality':'noncritical','target_ref':deepcopy(target_ref),'dependencies':[],
        'obligations':[{'obligation_id':'LO-guardrail','kind':'llm_metric','required':True,'event_policy':'aggregate','evaluator_ref':deepcopy(evaluator_ref)}],
        'mutation_applicability':{'status':'not_applicable','reason':'固定LLM性能評価はMutation得点を要求しない'}}]
    registry={'schema_version':1,'kind':'control_registry','registry_id':'llm-registry-'+target_version,'controls':controls}
    baseline_ref=None if baseline_context is None else baseline_context['baseline_ref']
    comparison={'mode':'not_applicable','baseline_ref':None,'changed_axes':['target'],'reason':'initial_baseline_pending'} if baseline_context is None else {
        'mode':'required','baseline_ref':baseline_ref,'changed_axes':['target'] if any(item['target_ref'] != target_ref for item in baseline_context['targets']) else [], 'reason':None}
    contract={'schema_version':1,'kind':'evaluation_contract','contract_id':'llm-contract-'+str(generation)+'-'+target_version,
        'generation':generation,'policy_series_id':policy['policy_id'],'policy_generation':policy_generation,
        'policy_ref':content_ref('policy_profile',policy['policy_id'],policy),
        'registry_ref':content_ref('control_registry',registry['registry_id'],registry),
        'case_set_ref':content_ref('case_set',cases['case_set_id'],cases),
        'calibration_case_set_ref':content_ref('case_set',calibration['case_set_id'],calibration),
        'evaluator_refs':[evaluator_ref],'required_categories':list(CATEGORIES),'use_cases':['UC-LLM'],
        'comparison':comparison,'required_outputs':['decision','evidence','findings','plans','run_receipt']}
    contract_ref=content_ref('evaluation_contract',contract['contract_id'],contract)
    old_targets={} if baseline_context is None else {item['control_id']:item['target_ref'] for item in baseline_context['targets']}
    entries=[]
    for case in cases['cases']:
        for variant in (('candidate',) if baseline_context is None else ('baseline','candidate')):
            entries.append({'obligation_id':'LO-guardrail','case_id':case['case_id'],'trial_id':case['case_id']+'-trial-1',
                'variant':variant,'stage_ids':[s['stage_id'] for s in case['session_steps']], 'required':True,'event_policy':'aggregate',
                'evaluator_ref':deepcopy(evaluator_ref),'target_ref':deepcopy(target_ref if variant=='candidate' else old_targets['LC-guardrail'])})
    plan={'schema_version':1,'kind':'trial_plan','plan_id':'llm-plan-'+run_id,'contract_ref':contract_ref,'entries':entries}
    manifest={'schema_version':1,'kind':'run_manifest','run_id':run_id,'contract_ref':contract_ref,
        'purpose':'baseline_candidate' if baseline_context is None else 'regression','use_cases':['UC-LLM'],
        'target_refs':[target_ref],'control_ids':[item['control_id'] for item in controls],'baseline_ref':baseline_ref,
        'plan_ref':content_ref('trial_plan',plan['plan_id'],plan),'policy_ref':contract['policy_ref'],'profile':'full',
        'environment_ref':content_ref('environment','isolated-guardrail',isolation_profile),
        'actor_context_ref':content_ref('actor_context','context-'+run_id,{'run_id':run_id,'role':'operator','request_fixed':True}),
        'created_at':now,'deadline':now+policy['profiles']['full']['elapsed_seconds']}
    bound=bind_run_manifest(manifest,contract,plan,policy,registry,cases,baseline_context=baseline_context)
    pairs=sorted({(entry['target_ref']['digest'],entry['evaluator_ref']['digest']) for entry in entries})
    profile={'schema_version':2,'kind':'execution_profile','isolation_digest':manifest['environment_ref']['digest'],
        'bindings':[{'target_digest':target,'evaluator_digest':evaluator,'fixture_digest':worker_digest,
                     'adapter_digests':[_sha(ROOT/'src/gah/normalized.py')]} for target,evaluator in pairs]}
    execution_profiles.check_plan(profile,bound)
    materialization={'schema_version':1,'kind':'llm_input_materialization','run_id':run_id,
        'pack_ref':content_ref('evaluation_pack',pack['pack_id'],pack),'manifest_ref':content_ref('run_manifest',run_id,manifest),
        'profile_ref':content_ref('execution_profile',run_id,profile),
        'target_ref':target_ref,'evaluator_ref':evaluator_ref,'case_count':len(cases['cases']),
        'planned_trials':len(entries),'planned_stages':sum(len(item['stage_ids']) for item in entries),
        'target_is_synthetic':True,'authority_connected':False,'runtime_verified':False,'ci_eligible':False}
    return {'bound_run':bound,'baseline_context':deepcopy(baseline_context),'execution_profile':profile,
        'materialization':materialization,'target_document':target_doc,'evaluator_document':evaluator_doc,
        'pack':pack,'calibration_case_set':calibration,'ci_eligible':False}


def prepared_response(value):
    """大きい実入力packを応答へ重複同梱しない。元packは固定refで照合する。"""
    return {key:deepcopy(value[key]) for key in ('bound_run','baseline_context','execution_profile','materialization','ci_eligible')}
