"""実行IDと測定・比較条件、対象版の差を分離する純粋な比較部品。"""
from copy import deepcopy
from fractions import Fraction
from .contracts import ContractError, require_object
from .run_contracts import bind_run_manifest, content_ref, validate_evaluation_contract
from .wire import canonical_bytes

CORE_FIELDS={'manifest','contract','plan','policy','registry','case_set','selected_controls','ci_eligible'}


def semantic_policy(policy):
    result=deepcopy(policy)
    for level in result['thresholds'].values():
        for key,value in level.items():
            number=Fraction(*value);level[key]=[number.numerator,number.denominator]
    for key in ('mandatory_coverage_min','warning_usage_min'):
        number=Fraction(*result[key]);result[key]=[number.numerator,number.denominator]
    for key in ('ci_success_states','warning_dimensions'):result[key].sort()
    return result


def signature(source, *, baseline_context=None):
    require_object(source,CORE_FIELDS)
    if source['ci_eligible'] is not False:
        raise ContractError('SOURCE_INVALID')
    try:
        contract = validate_evaluation_contract(source['contract'])
        if contract['comparison']['mode'] == 'required' and baseline_context is None:
            raise ContractError('BASELINE_CONTEXT_REQUIRED')
        bound = bind_run_manifest(*(source[k] for k in ('manifest','contract','plan','policy','registry','case_set')),
            baseline_context=baseline_context)
    except ContractError:
        raise
    except (KeyError, TypeError, ValueError, RecursionError):
        raise ContractError('SOURCE_INVALID') from None
    if source!=bound:
        raise ContractError('SOURCE_BINDING_MISMATCH')
    c,m=bound['contract'],bound['manifest']
    for field,kind,identifier in (('policy','policy_profile','policy_id'),
                                ('registry','control_registry','registry_id'),('case_set','case_set','case_set_id')):
        if c[field+'_ref']!=content_ref(kind,bound[field][identifier],bound[field]):
            raise ContractError('REFERENCE_MISMATCH')
    declared={tuple(ref[k] for k in ('kind','id','digest')) for ref in c['evaluator_refs']}
    resolved={tuple(obligation['evaluator_ref'][k] for k in ('kind','id','digest'))
        for control in bound['registry']['controls'] for obligation in control['obligations']}
    if declared!=resolved or set(c['required_categories'])!=set(bound['case_set']['required_categories']):
        raise ContractError('REFERENCE_MISMATCH')
    controls=[];evaluators=[];targets=[]
    for control in bound['registry']['controls']:
        projected={k:deepcopy(v) for k,v in control.items() if k not in ('target_ref','obligations')}
        projected['dependencies']=sorted(projected['dependencies'])
        projected['obligations']=sorted(({k:deepcopy(v) for k,v in obligation.items() if k!='evaluator_ref'}
            for obligation in control['obligations']),key=lambda o:o['obligation_id'])
        controls.append(projected)
        targets.append({'control_id':control['control_id'],'target_ref':deepcopy(control['target_ref'])})
        evaluators.extend({'obligation_id':o['obligation_id'],'evaluator_ref':deepcopy(o['evaluator_ref'])}
            for o in control['obligations'])
    entries=sorted(({k:deepcopy(v) for k,v in entry.items() if k not in ('target_ref','evaluator_ref')}
        for entry in bound['plan']['entries']),key=canonical_bytes)
    corpus=deepcopy(bound['case_set'])
    corpus['cases']=sorted(corpus['cases'],key=lambda item:item['case_id'])
    corpus['required_categories']=sorted(corpus['required_categories'])
    components={
        'policy':{'profile':m['profile'],'elapsed_limit':m['deadline']-m['created_at'],'policy':semantic_policy(bound['policy']),
            'controls':sorted(controls,key=lambda item:item['control_id']),
            'selected_controls':sorted(bound['selected_controls']),'use_cases':sorted(c['use_cases']),
            'required_outputs':sorted(c['required_outputs'])},
        'corpus':{'case_set':corpus,'required_categories':sorted(c['required_categories']),
            'calibration_case_set_ref':deepcopy(c['calibration_case_set_ref']),'entries':entries},
        'evaluator':sorted(evaluators,key=lambda item:item['obligation_id']),
        'environment':deepcopy(m['environment_ref']),
        'target':sorted(targets,key=lambda item:item['control_id'])}
    # plan_id、contract_id/generation、run_id、生成時刻は実行identityとして別に保持する。
    # stage順、trial、Control/Case ID、必須性、oracle、初期状態、予算は落とさない。
    digests={key:content_ref('conditions',key,value)['digest'] for key,value in components.items()}
    measurement={key:digests[key] for key in digests if key!='target'}
    comparison={key:deepcopy(c['comparison'][key]) for key in ('mode','baseline_ref')}
    comparison['baseline_targets']=None if baseline_context is None else sorted(deepcopy(baseline_context['targets']),key=lambda item:item['control_id'])
    return {'schema_version':1,'kind':'semantic_conditions','components':components,'digests':digests,
        'comparison':deepcopy(c['comparison']),'comparison_context':comparison,'manifest_ref':content_ref('run_manifest',m['run_id'],m),
        'measurement_conditions_ref':content_ref('conditions','measurement-conditions',measurement),
        'conditions_ref':content_ref('conditions','evaluation-conditions',{'measurement':measurement,'comparison':comparison}),
        'ci_eligible':False}


def compare(previous,following,*,previous_baseline_context=None,following_baseline_context=None):
    old=signature(previous,baseline_context=previous_baseline_context)
    new=signature(following,baseline_context=following_baseline_context)
    axes=sorted(key for key in old['digests'] if old['digests'][key]!=new['digests'][key])
    return {'schema_version':1,'kind':'semantic_conditions_comparison','changed_axes':axes,
        'same_evaluation_conditions':old['conditions_ref']==new['conditions_ref'],
        'same_measurement_conditions':old['measurement_conditions_ref']==new['measurement_conditions_ref'],
        'baseline_changed':old['comparison']['baseline_ref']!=new['comparison']['baseline_ref'],
        'baseline_targets_changed':old['comparison_context']['baseline_targets']!=new['comparison_context']['baseline_targets'],
        'previous':old,'following':new,'authority_connected':False,'ci_eligible':False}
