"""未採択の契約候補のFinding・Planを、通常CIへの利用と分離して読む。"""
from copy import deepcopy
from .adoption import AdoptionError
from .contracts import ContractError,require_object,require_id,require_ref
from . import assurance_authority,run_outputs
ACTIONS={"candidate_outputs":{"operator","validator"},"candidate_artifact":{"operator","validator"}}
FRESH_ACTIONS=set(ACTIONS)
BASE={"schema_version","action","request_id","run_id"}
FIELDS={"candidate_outputs":BASE,"candidate_artifact":BASE|{"artifact_ref"}}


def validate_request(value):
    try:
        action=value.get("action") if type(value) is dict else None
        if type(action) is not str or action not in FIELDS:
            raise ContractError()
        require_object(value,FIELDS[action])
        if type(value['schema_version']) is not int or value['schema_version']!=1:
            raise ContractError()
        require_id(value['request_id']);require_id(value['run_id'])
        if 'artifact_ref' in value:require_ref(value['artifact_ref'])
    except (ContractError,KeyError,TypeError,ValueError):
        raise AdoptionError('INVALID_REQUEST') from None
    return deepcopy(value)


def execute(store,db,request,now,resolve_bound):
    request=validate_request(request);run_id=request['run_id']
    bound,_=resolve_bound(run_id)
    if bound['manifest']['purpose'] not in {'contract_candidate','contract_old_regression'}:
        raise AdoptionError('CANDIDATE_PURPOSE_REQUIRED')
    if db.execute('SELECT 1 FROM transition_runs WHERE run_id=?',(run_id,)).fetchone() is None:
        raise AdoptionError('CANDIDATE_MISSING')
    source=assurance_authority.baseline_source(store,db,run_id,now,resolve_bound)
    if request['action']=='candidate_artifact':
        return run_outputs.fetch(db,bound,source['receipt'],request['artifact_ref'])
    return run_outputs.read(db,bound,source['receipt'])
