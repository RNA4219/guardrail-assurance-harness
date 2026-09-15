"""基準管理主体による対象廃止を不変記録へ保存し、新規実行を止める。"""
from copy import deepcopy
import hashlib
from .adoption import AdoptionError
from .contracts import ContractError, require_object, require_ref, require_id, require_uint
from .run_contracts import content_ref
from .wire import canonical_bytes
from . import assurance_authority, resources

KIND = 'target_retirement'
BASE = {'schema_version','action','request_id','contract_series_id','expected_contract_ref','target_ref','reason_code'}
ACTIONS = {'target_retire': {'manager'}}
FIELDS = {'target_retire': BASE}
FRESH_ACTIONS = set()


def validate_request(value):
    try:
        require_object(value, BASE)
        if type(value['schema_version']) is not int or value['schema_version'] != 1 or value['action'] != 'target_retire':
            raise ContractError()
        for key in ('request_id','contract_series_id','reason_code'): require_id(value[key])
        for key, kind in (('expected_contract_ref','evaluation_contract'),('target_ref','target')):
            require_ref(value[key])
            if value[key]['kind'] != kind: raise ContractError()
    except (ContractError, KeyError, TypeError, ValueError):
        raise AdoptionError('INVALID_REQUEST') from None
    return deepcopy(value)


def _identifier(target):
    return 'retire-' + hashlib.sha256(target['id'].encode()).hexdigest()[:32]


def _contract(db, request):
    from . import evaluation_authority as authority
    ref = request['expected_contract_ref']
    rows = db.execute('SELECT * FROM eval_adoptions WHERE series_id=? AND digest=?',
        (request['contract_series_id'], ref['digest'])).fetchall()
    if len(rows) != 1: raise AdoptionError('CONTRACT_INVALID')
    contract = authority._load_json(rows[0], 'payload_json', 'digest')
    if content_ref('evaluation_contract', contract['contract_id'], contract) != ref:
        raise AdoptionError('BINDING_MISMATCH')
    authority._assert_contract_history(db, rows[0], contract)
    registry = authority._object(db, contract['registry_ref'])
    if not any(c['target_ref'] == request['target_ref'] for c in registry['controls']):
        raise AdoptionError('TARGET_BINDING_MISMATCH')
    return rows[0], contract


def _load(db, row, now):
    value = resources._unpack(row['payload_json'], row['digest'])
    require_object(value, {'kind','request','actor_id','context','permission_generation','created_at'})
    request = validate_request(value['request']); require_uint(value['created_at']); require_uint(value['permission_generation'])
    if (value['kind'] != KIND or row['kind'] != KIND or row['id'] != _identifier(request['target_ref'])
            or row['run_id'] != row['id'] or value['actor_id'] != 'manager' or value['context'] != 'manager-context'
            or value['created_at'] > now): raise AdoptionError('RETIREMENT_INVALID')
    _contract(db, request)
    origin = db.execute('SELECT * FROM idempotency WHERE request_id=?', (request['request_id'],)).fetchone()
    ref = content_ref(KIND, row['id'], value)
    if (origin is None or origin['actor_id'] != value['actor_id'] or origin['context'] != value['context']
            or origin['request_digest'] != hashlib.sha256(canonical_bytes(request)).hexdigest()):
        raise AdoptionError('RETIREMENT_ORIGIN_INVALID')
    response = resources._unpack(origin['response_json'], origin['response_digest'])
    if (response.get('retirement_ref') != ref or response.get('retirement') != value or response.get('ci_eligible') is not False
            or response.get('kind') != 'evaluation_authority_result' or response.get('action') != 'target_retire'
            or response.get('request_id') != request['request_id']): raise AdoptionError('RETIREMENT_ORIGIN_INVALID')
    return value


def resolve(db, ref, now):
    require_ref(ref)
    if ref['kind'] != KIND: raise AdoptionError('RETIREMENT_INVALID')
    rows = db.execute('SELECT * FROM authority_artifacts WHERE kind=? AND id=? AND digest=?', (KIND, ref['id'], ref['digest'])).fetchall()
    if len(rows) != 1: raise AdoptionError('RETIREMENT_INVALID')
    return _load(db, rows[0], now)


def check_targets(db, targets, now):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='authority_artifacts'").fetchone(): return
    for target in targets:
        rows = db.execute('SELECT * FROM authority_artifacts WHERE kind=? AND id=?', (KIND, _identifier(target))).fetchall()
        if not rows: continue
        if len(rows) != 1: raise AdoptionError('RETIREMENT_INVALID')
        value = _load(db, rows[0], now)
        if value['request']['target_ref']['id'] != target['id']: raise AdoptionError('RETIREMENT_INVALID')
        raise AdoptionError('TARGET_RETIRED')


def execute(store, db, request, actor_id, context, now):
    request = validate_request(request)
    if actor_id != 'manager' or context != 'manager-context': raise AdoptionError('AUTHORITY_DENIED')
    row, contract = _contract(db, request)
    from . import evaluation_authority as authority
    current = db.execute('SELECT * FROM eval_current WHERE series_id=?', (request['contract_series_id'],)).fetchone()
    if current is None or current['digest'] != row['digest']: raise AdoptionError('CURRENT_CONTRACT_MISMATCH')
    authority._assert_contract_fresh(store, db, current, contract, now)
    check_targets(db, [request['target_ref']], now)
    identifier = _identifier(request['target_ref'])
    value = {'kind':KIND,'request':request,'actor_id':actor_id,'context':context,
        'permission_generation':store._permission_generation(db),'created_at':now}
    ref = assurance_authority._save(db, identifier, KIND, identifier, value)
    return {'retirement':value,'retirement_ref':ref,'ci_eligible':False}
