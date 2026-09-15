"""両用途を事前固定した複合run。子runの根拠と現在CIを独立に検査する。"""
from copy import deepcopy
import hashlib
from .adoption import AdoptionError
from .contracts import ContractError, require_object, require_id, require_ref, require_uint
from .run_contracts import content_ref
from .wire import canonical_bytes
from . import resources, regression_runs, assurance_authority

KIND = 'combined_run_binding'
RECEIPT = 'combined_run_receipt'
CANCEL = 'combined_cancel_request'
BASE = {'schema_version', 'action', 'request_id', 'run_id'}
FIELDS = {'combined_prepare': BASE | {'children'},
          'combined_child_read': BASE | {'expected_manifest_ref', 'use_case'},
          'combined_finalize': BASE | {'expected_manifest_ref'},
          'combined_cancel': BASE | {'expected_manifest_ref'},
          'combined_current': BASE | {'expected_manifest_ref'}}
ACTIONS = {action: {'operator'} for action in FIELDS}
FRESH_ACTIONS = {'combined_child_read', 'combined_current'}
USES = ('UC-CI', 'UC-LLM')


def validate_request(value):
    try:
        require_object(value, FIELDS[value['action']])
        if type(value['schema_version']) is not int or value['schema_version'] != 1:
            raise ContractError()
        require_id(value['run_id']); require_id(value['request_id'])
        if len(value['run_id']) > 64:
            raise ContractError()
        if 'expected_manifest_ref' in value:
            require_ref(value['expected_manifest_ref'])
            if value['expected_manifest_ref']['kind'] != 'combined_run_manifest':
                raise ContractError()
        if 'use_case' in value and value['use_case'] not in USES:
            raise ContractError()
        if 'children' in value:
            children=value['children']
            if type(children) is not list or len(children) != 2:
                raise ContractError()
            for child, use in zip(children, USES):
                require_object(child, {'use_case', 'run_id', 'contract_series_id', 'expected_contract_ref'})
                if child['use_case'] != use:
                    raise ContractError()
                require_id(child['run_id']); require_id(child['contract_series_id']); require_ref(child['expected_contract_ref'])
                if len(child['run_id']) > 64 or child['expected_contract_ref']['kind'] != 'evaluation_contract':
                    raise ContractError()
            if len({value['run_id'], *(child['run_id'] for child in children)}) != 3:
                raise ContractError()
        return deepcopy(value)
    except (ContractError, KeyError, TypeError, ValueError):
        raise AdoptionError('INVALID_REQUEST') from None


def _rows(db):
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='authority_artifacts'").fetchone() is None:
        return []
    rows=list(db.execute('SELECT * FROM authority_artifacts WHERE kind=? ORDER BY id,digest', (KIND,)))
    if len(rows)>1000:
        raise AdoptionError('COMBINED_HISTORY_LIMIT')
    return rows


def _raw(row):
    value=resources._unpack(row['payload_json'], row['digest'])
    require_object(value, {'kind', 'request', 'manifest', 'permission_generation'})
    request=validate_request(value['request'])
    if (value['kind']!=KIND or request['action']!='combined_prepare'
            or row['id']!=request['run_id'] or row['run_id']!=request['run_id']):
        raise AdoptionError('COMBINED_BINDING_INVALID')
    return value


def _describe(request, prepared, created_at):
    policies=[p['bound_run']['policy'] for p in prepared]
    if policies[0]!=policies[1]:
        raise AdoptionError('COMBINED_POLICY_MISMATCH')
    children=[]
    for selection, value in zip(request['children'], prepared):
        bound=value['bound_run']; manifest=bound['manifest']
        if (manifest['run_id']!=selection['run_id'] or manifest['use_cases']!=[selection['use_case']]
                or manifest['purpose']!='regression' or manifest['profile']!='full'
                or manifest['contract_ref']!=selection['expected_contract_ref']
                or manifest['created_at']!=created_at or value.get('scope') is not None):
            raise AdoptionError('COMBINED_BINDING_INVALID')
        children.append({'use_case':selection['use_case'], 'run_id':selection['run_id'],
            'contract_series_id':selection['contract_series_id'], 'manifest_ref':content_ref('run_manifest',manifest['run_id'],manifest),
            'contract_ref':manifest['contract_ref'], 'baseline_ref':manifest['baseline_ref'],
            'target_refs':manifest['target_refs'], 'plan_ref':manifest['plan_ref']})
    budget=policies[0]['profiles']['full']
    trials=sum(len(p['bound_run']['plan']['entries']) for p in prepared)
    if trials>budget['case_trial_executions']:
        raise AdoptionError('RESOURCE_LIMIT')
    return {'schema_version':1, 'kind':'combined_run_manifest', 'run_id':request['run_id'],
        'use_cases':list(USES), 'children':children, 'profile':'full', 'created_at':created_at,
        'deadline':min(p['bound_run']['manifest']['deadline'] for p in prepared),
        'policy_ref':prepared[0]['bound_run']['manifest']['policy_ref'],
        'budget':deepcopy(budget), 'planned_trials':trials}


def load(db, run_id, now):
    rows=[row for row in _rows(db) if row['id']==run_id]
    if len(rows)!=1:
        raise AdoptionError('COMBINED_MISSING' if not rows else 'COMBINED_BINDING_INVALID')
    value=_raw(rows[0]); manifest=value['manifest']; require_uint(manifest['created_at']);require_uint(value['permission_generation'])
    if manifest['created_at']>now:
        raise AdoptionError('TIME_ORDER')
    prepared=[]
    for child in value['request']['children']:
        history=db.execute('SELECT * FROM eval_adoptions WHERE series_id=? AND digest=?',
            (child['contract_series_id'],child['expected_contract_ref']['digest'])).fetchall()
        if len(history)!=1:
            raise AdoptionError('COMBINED_BINDING_INVALID')
        prepared.append(regression_runs.build(db,history[0],child['run_id'],manifest['created_at'],now))
    if _describe(value['request'],prepared,manifest['created_at'])!=manifest:
        raise AdoptionError('COMBINED_BINDING_INVALID')
    origin=db.execute('SELECT * FROM idempotency WHERE request_id=?',(value['request']['request_id'],)).fetchone()
    if (origin is None or origin['actor_id']!='operator' or origin['context']!='operator-context'
            or origin['request_digest']!=hashlib.sha256(canonical_bytes(value['request'])).hexdigest()):
        raise AdoptionError('COMBINED_ORIGIN_INVALID')
    response=resources._unpack(origin['response_json'],origin['response_digest'])
    if (response.get('manifest')!=manifest or response.get('manifest_ref')!=content_ref('combined_run_manifest',run_id,manifest)
            or response.get('action')!='combined_prepare' or response.get('request_id')!=value['request']['request_id']
            or response.get('ci_eligible') is not False):
        raise AdoptionError('COMBINED_ORIGIN_INVALID')
    return value,prepared


def for_child(db, run_id, now):
    matches=[]
    for row in _rows(db):
        value=_raw(row)
        if value['request']['run_id']==run_id:
            raise AdoptionError('COMBINED_PARENT_ID_RESERVED')
        if any(child['run_id']==run_id for child in value['request']['children']):
            matches.append(row['id'])
    if not matches:return None
    if len(matches)!=1:raise AdoptionError('COMBINED_BINDING_INVALID')
    value,prepared=load(db,matches[0],now)
    return value, next(p for p in prepared if p['bound_run']['manifest']['run_id']==run_id)


def check_binding(db, manifest, plan, now, series_id):
    found=for_child(db,manifest['run_id'],now)
    if found is not None:
        root,prepared=found
        child=next(c for c in root['manifest']['children'] if c['run_id']==manifest['run_id'])
        if prepared['bound_run']['manifest']!=manifest or prepared['bound_run']['plan']!=plan or child['contract_series_id']!=series_id:
            raise AdoptionError('COMBINED_BINDING_INVALID')
        if _cancelled(db,root,now):raise AdoptionError('COMBINED_STOP_REQUIRED')
        if now>=root['manifest']['deadline']:
            raise AdoptionError('COMBINED_EXPIRED')


def check_start(db,run_id,now,reservation=None,operation_id=None):
    found=for_child(db,run_id,now)
    if found is None:return
    root,_=found
    if _cancelled(db,root,now):raise AdoptionError('COMBINED_STOP_REQUIRED')
    if now>=root['manifest']['deadline']:raise AdoptionError('COMBINED_EXPIRED')
    book=resources.ResourceBook(db)
    totals={key:0 for key in (*resources.COUNTERS,'slots','unsettled','total_tokens')}
    for child in root['manifest']['children']:
        row=db.execute('SELECT * FROM resource_runs WHERE run_id=?',(child['run_id'],)).fetchone()
        if row is None:continue
        snapshot=book.snapshot(child['run_id'],now)
        if snapshot['breached'] or snapshot['cancelled']:
            raise AdoptionError('COMBINED_STOP_REQUIRED')
        for key in totals:totals[key]+=snapshot['resources'][key]
    limits=root['manifest']['budget']
    duplicate=operation_id is not None and db.execute('SELECT 1 FROM resource_operations WHERE operation_id=?',(operation_id,)).fetchone() is not None
    addition=reservation if reservation is not None and not duplicate else {key:0 for key in resources.COUNTERS}
    if totals['slots']+int(reservation is not None and not duplicate)>limits['concurrent_evaluations']:
        raise AdoptionError('CONCURRENCY_LIMIT')
    for key in ('case_trial_executions','model_calls','api_cost_usd_micros'):
        if totals[key]+addition[key]>limits[key]:raise AdoptionError('RESOURCE_LIMIT')
    if totals['total_tokens']+addition['input_tokens']+addition['output_tokens']>limits['total_tokens']:
        raise AdoptionError('RESOURCE_LIMIT')


def _cancelled(db,root,now):
    run_id=root['manifest']['run_id']
    rows=list(db.execute('SELECT * FROM authority_artifacts WHERE kind=? AND id=?',(CANCEL,run_id)))
    if not rows:return False
    if len(rows)!=1:raise AdoptionError('COMBINED_CANCELLATION_INVALID')
    value=resources._unpack(rows[0]['payload_json'],rows[0]['digest'])
    require_object(value,{'kind','request','created_at'})
    request=validate_request(value['request']);require_uint(value['created_at'])
    if not root['manifest']['created_at']<=value['created_at']<=now:raise AdoptionError('TIME_ORDER')
    if (value['kind']!=CANCEL or request['action']!='combined_cancel' or request['run_id']!=run_id
            or request['expected_manifest_ref']!=content_ref('combined_run_manifest',run_id,root['manifest'])):
        raise AdoptionError('COMBINED_CANCELLATION_INVALID')
    origin=db.execute('SELECT * FROM idempotency WHERE request_id=?',(request['request_id'],)).fetchone()
    if (origin is None or origin['actor_id']!='operator' or origin['context']!='operator-context'
            or origin['request_digest']!=hashlib.sha256(canonical_bytes(request)).hexdigest()):
        raise AdoptionError('COMBINED_ORIGIN_INVALID')
    response=resources._unpack(origin['response_json'],origin['response_digest'])
    if response.get('cancellation_ref')!=content_ref(CANCEL,run_id,value):raise AdoptionError('COMBINED_ORIGIN_INVALID')
    return True


def _gates(store,db,root,now):
    result=[]
    for child in root['manifest']['children']:
        request={'schema_version':1,'action':'ci_check','request_id':'combined-ci-'+child['run_id'],
            'run_id':child['run_id'],'expected_manifest_ref':child['manifest_ref'],
            'expected_contract_ref':child['contract_ref'],'expected_baseline_ref':child['baseline_ref'],
            'expected_target_refs':child['target_refs'],'expected_use_cases':[child['use_case']]}
        gate=regression_runs.ci_check(store,db,request,now)
        result.append({'use_case':child['use_case'],'gate':gate})
    return result


def execute(store,db,request,now):
    request=validate_request(request); action=request['action'];run_id=request['run_id']
    if action=='combined_prepare':
        reserved={run_id,*(child['run_id'] for child in request['children'])}
        for row in _rows(db):
            old=_raw(row)['request']
            if reserved & {old['run_id'],*(c['run_id'] for c in old['children'])}:
                raise AdoptionError('RUN_CONFLICT')
        for identifier in reserved:
            if any(db.execute('SELECT 1 FROM '+table+' WHERE run_id=?',(identifier,)).fetchone()
                    for table in ('eval_runs','resource_runs','transition_runs','fixture_admissions')):
                raise AdoptionError('RUN_CONFLICT')
        prepared=[regression_runs.prepare(store,db,{'run_id':child['run_id'],'contract_series_id':child['contract_series_id'],
            'expected_contract_ref':child['expected_contract_ref']},now) for child in request['children']]
        manifest=_describe(request,prepared,now)
        value={'kind':KIND,'request':request,'manifest':manifest,'permission_generation':store._permission_generation(db)}
        assurance_authority._save(db,run_id,KIND,run_id,value)
        return {'manifest':manifest,'manifest_ref':content_ref('combined_run_manifest',run_id,manifest),'ci_eligible':False}
    root,prepared=load(db,run_id,now);manifest=root['manifest'];ref=content_ref('combined_run_manifest',run_id,manifest)
    if request['expected_manifest_ref']!=ref:raise AdoptionError('COMBINED_BINDING_INVALID')
    if action=='combined_child_read':
        index=USES.index(request['use_case'])
        return {'manifest_ref':ref,'prepared':prepared[index],'ci_eligible':False}
    if action=='combined_cancel':
        if db.execute('SELECT 1 FROM authority_artifacts WHERE kind=? AND id=?',(RECEIPT,run_id)).fetchone():
            raise AdoptionError('COMBINED_ALREADY_FINALIZED')
        if _cancelled(db,root,now):raise AdoptionError('COMBINED_ALREADY_CANCELLED')
        value={'kind':CANCEL,'request':request,'created_at':now}
        reference=assurance_authority._save(db,run_id,CANCEL,run_id,value)
        return {'manifest_ref':ref,'cancellation_ref':reference,'ci_eligible':False}
    children=_gates(store,db,root,now)
    cancelled=_cancelled(db,root,now)
    slots=sum(resources.ResourceBook(db).snapshot(c['run_id'],now)['resources']['slots']
        for c in manifest['children'] if db.execute('SELECT 1 FROM resource_runs WHERE run_id=?',(c['run_id'],)).fetchone())
    priority={'HOLD':0,'DEGRADED':1,'UNKNOWN':2,'WARNING':3,'HEALTHY':4}
    assurance=min((child['gate']['assurance'] for child in children),key=priority.__getitem__)
    rows=list(db.execute('SELECT * FROM authority_artifacts WHERE kind=? AND id=?',(RECEIPT,run_id)))
    if len(rows)>1:raise AdoptionError('COMBINED_RECEIPT_INVALID')
    saved=None if not rows else resources._unpack(rows[0]['payload_json'],rows[0]['digest'])
    outputs=[child['gate']['outputs_ref'] for child in children]
    complete=cancelled and slots==0 or all(child['gate']['execution_status'] in {'COMPLETED','CANCELLED'} and child['gate']['outputs_ref'] is not None for child in children)
    if action=='combined_finalize' and saved is None:
        if not complete:raise AdoptionError('COMBINED_INCOMPLETE')
        saved={'schema_version':1,'kind':RECEIPT,'run_id':run_id,'manifest_ref':ref,
            'child_outputs':outputs,'observed_assurance':assurance,'created_at':now,'request':request,'cancelled':cancelled,'ci_eligible':False}
        assurance_authority._save(db,run_id,RECEIPT,run_id,saved)
    if saved is not None:
        require_object(saved,{'schema_version','kind','run_id','manifest_ref','child_outputs','observed_assurance','created_at','request','cancelled','ci_eligible'})
        require_uint(saved['created_at'])
        saved_request=validate_request(saved['request'])
        if (type(saved['schema_version']) is not int or saved['schema_version']!=1 or saved['kind']!=RECEIPT
                or saved['run_id']!=run_id or saved['manifest_ref']!=ref or saved['ci_eligible'] is not False
                or not manifest['created_at']<=saved['created_at']<=now or saved['observed_assurance'] not in priority
                or type(saved['cancelled']) is not bool or saved_request['action']!='combined_finalize'
                or saved_request['run_id']!=run_id or saved_request['expected_manifest_ref']!=ref):
            raise AdoptionError('COMBINED_RECEIPT_INVALID')
        origin=db.execute('SELECT * FROM idempotency WHERE request_id=?',(saved_request['request_id'],)).fetchone()
        # 新規保存の応答はこのtransactionの最後にidempotencyへ入る。
        if rows:
            if (origin is None or origin['actor_id']!='operator' or origin['context']!='operator-context'
                    or origin['request_digest']!=hashlib.sha256(canonical_bytes(saved_request)).hexdigest()
                    or resources._unpack(origin['response_json'],origin['response_digest']).get('receipt')!=saved):
                raise AdoptionError('COMBINED_ORIGIN_INVALID')
    reasons=[]
    if saved is None:reasons.append('COMBINED_OUTPUT_REQUIRED')
    elif outputs!=saved['child_outputs']:reasons.append('CHILD_OUTPUT_UNAVAILABLE')
    if not all(child['gate']['ci_eligible'] for child in children):reasons.append('CHILD_NOT_ELIGIBLE')
    if root['permission_generation']!=store._permission_generation(db):reasons.append('AUTHORITY_STALE')
    if cancelled:reasons.append('CANCEL_REQUESTED')
    code=0 if not reasons else (3 if any(c['gate']['exit_code']==3 for c in children) else (2 if saved is None or any(c['gate']['exit_code']==2 for c in children) else 1))
    if cancelled:code=3 if slots==0 else 2
    return {'manifest':manifest,'manifest_ref':ref,'children':children,'checked_at':now,'cancelled':cancelled,'active_operations':slots,
        'assurance':assurance,'receipt':saved,'reasons':reasons,'ci_eligible':action=='combined_current' and not reasons,'exit_code':code}


def ci_check(store, db, request, now):
    """固定AdoptionStoreだけがCI利用へ昇格する、現在の複合照会。"""
    from .run_evidence import RunEvidenceBook, EvidenceError
    from .read_checks import scope, source_scope
    request = validate_request(request)
    if request['action'] != 'combined_current':
        raise AdoptionError('INVALID_ACTION')
    try:
        resources.ResourceBook(db)._touch(now)
        RunEvidenceBook(db, now=now, allowed_bindings={})._now(db)
    except (resources.ResourceError, EvidenceError) as error:
        raise AdoptionError(error.code) from None
    with scope(db), source_scope():
        return {'schema_version':1, 'kind':'evaluation_authority_result',
            'action':'combined_current', 'request_id':request['request_id'],
            **execute(store, db, request, now)}
