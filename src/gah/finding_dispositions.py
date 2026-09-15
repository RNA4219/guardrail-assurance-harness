"""修復とは別の基準改訂・対象廃止を、管理主体の実採択へ結ぶ。"""
from copy import deepcopy
from .adoption import AdoptionError
from .run_contracts import content_ref
from . import baseline_authority, target_retirement


def proof(store, db, source, finding, request, now, resolve_source, *, fresh):
    if request['disposition'] == 'TARGET_RETIRED':
        retired = target_retirement.resolve(db, request['authority_ref'], now)
        target = next(c['target_ref'] for c in source['bound']['registry']['controls'] if c['control_id'] == finding['control_ref']['id'])
        if retired['request']['target_ref']['id'] != target['id']:
            raise AdoptionError('TARGET_BINDING_MISMATCH')
        return {'disposition':'TARGET_RETIRED','authority_ref':deepcopy(request['authority_ref']),
            'original_target_ref':deepcopy(target),'recorded_at':now}
    old_ref = source['bound']['manifest']['baseline_ref']
    refs = [old_ref, request['authority_ref']]; rows=[]; records=[]
    for ref in refs:
        found = db.execute('SELECT * FROM baseline_adoptions WHERE baseline_digest=?', (ref['digest'],)).fetchall()
        if len(found) != 1: raise AdoptionError('BASELINE_REFERENCE_UNAVAILABLE')
        _, record, _ = baseline_authority._history(db, found[0])
        if content_ref('baseline', record['baseline_id'], record) != ref or found[0]['adopted_at'] > now:
            raise AdoptionError('BINDING_MISMATCH')
        rows.append(found[0]); records.append(record)
    if rows[0]['series_id'] != rows[1]['series_id'] or rows[1]['generation'] <= rows[0]['generation']:
        raise AdoptionError('NEW_BASELINE_REQUIRED')
    if fresh:
        result = baseline_authority.execute(store, db, {'schema_version':1,'action':'baseline_resolve',
            'request_id':request['request_id'],'series_id':rows[1]['series_id'],
            'expected_baseline_ref':request['authority_ref'],'expected_contract_ref':records[1]['contract_ref']},
            'manager','manager-context',now,resolve_source)
        if result.get('use') is not True: raise AdoptionError('BASELINE_UNAVAILABLE')
    return {'disposition':'BASELINE_REVISED','authority_ref':deepcopy(request['authority_ref']),
        'original_baseline_ref':deepcopy(old_ref),'recorded_at':now}
