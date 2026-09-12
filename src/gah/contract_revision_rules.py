"""後続契約の構造的差分と禁止変更を検査する。認証・採択は未接続。"""
from copy import deepcopy
from .contracts import ContractError, MAX_INTEGER
from .semantic_conditions import compare


def inspect_revision(previous, following, *, previous_baseline_context, following_baseline_context):
    comparison = compare(previous, following,
        previous_baseline_context=previous_baseline_context,
        following_baseline_context=following_baseline_context)
    old, new = previous['contract'], following['contract']
    if not 2 <= old['generation'] < MAX_INTEGER or new['generation'] != old['generation'] + 1:
        raise ContractError('CONTRACT_GENERATION_MISMATCH')
    if old['contract_id'] == new['contract_id']:
        raise ContractError('CONTRACT_ID_REUSED')
    if old['policy_series_id'] != new['policy_series_id'] or new['policy_generation'] < old['policy_generation']:
        raise ContractError('POLICY_LINEAGE_MISMATCH')
    if new['policy_generation'] == old['policy_generation'] and new['policy_ref'] != old['policy_ref']:
        raise ContractError('POLICY_GENERATION_REUSED')
    if old['comparison']['mode'] != 'required' or new['comparison']['mode'] != 'required':
        raise ContractError('COMPARISON_REQUIRED')
    if sorted(new['comparison']['changed_axes']) != comparison['changed_axes']:
        raise ContractError('DECLARED_AXES_MISMATCH')
    old_controls = {control['control_id']: control for control in previous['registry']['controls']}
    new_controls = {control['control_id']: control for control in following['registry']['controls']}
    changed_targets = []
    for identifier, control in old_controls.items():
        candidate = new_controls.get(identifier)
        required = [item for item in control['obligations'] if item['required']]
        if candidate is None:
            if control['criticality'] == 'critical' or required:
                raise ContractError('MANDATORY_CONTROL_REMOVED')
            continue
        if control['criticality'] == 'critical' and candidate['criticality'] != 'critical':
            raise ContractError('CRITICAL_DOWNGRADED')
        obligations = {item['obligation_id']: item for item in candidate['obligations']}
        for item in required:
            next_item = obligations.get(item['obligation_id'])
            if next_item is None or not next_item['required'] or next_item['kind'] != item['kind']:
                raise ContractError('MANDATORY_OBLIGATION_REMOVED')
            if item['event_policy'] == 'forbidden' and next_item['event_policy'] != 'forbidden':
                raise ContractError('FORBIDDEN_EVENT_WEAKENED')
        if control['target_ref'] != candidate['target_ref']:
            changed_targets.append({'control_id': identifier, 'previous': deepcopy(control['target_ref']),
                'following': deepcopy(candidate['target_ref']),
                'content_digest_changed': control['target_ref']['digest'] != candidate['target_ref']['digest']})
    return {'schema_version': 1, 'kind': 'contract_revision_inspection',
        'previous_contract_generation': old['generation'], 'following_contract_generation': new['generation'],
        'comparison': comparison, 'changed_targets': sorted(changed_targets, key=lambda item: item['control_id']),
        'requires_old_conditions_regression': True,
        'conditions_compatible_for_revalidation': comparison['same_evaluation_conditions']
            and comparison['changed_axes'] == ['target'] and bool(changed_targets)
            and all(item['content_digest_changed'] for item in changed_targets),
        'target_execution_verified': False, 'adoption_verified': False,
        'authority_connected': False, 'ci_eligible': False}
