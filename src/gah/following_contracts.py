"""保存済み通常runを用いる、測定条件を維持した後続契約の構造結合。"""
from copy import deepcopy

from .baselines import repeat_config_for_plan, validate_baseline_record
from .contracts import ContractError, MAX_INTEGER, require_object, require_id, require_uint
from .contract_updates import _validate_source, _same_without, _oracle_refs, _evaluator_refs, _ref
from .run_contracts import bind_run_manifest, content_ref, validate_evaluation_contract
from .contract_revision_rules import inspect_revision


def bind_following_transition(previous_contract, next_contract, *, baseline_record,
                              baseline_source_bound, source_baseline_context):
    """構造の照合だけを行い、採択・現在の利用許可は発行しない。"""
    try:
        previous = validate_evaluation_contract(previous_contract)
        following = validate_evaluation_contract(next_contract)
        baseline = validate_baseline_record(baseline_record)
        manifest, source_contract, plan, policy, registry, case_set = _validate_source(baseline_source_bound)
        if not 2 <= previous['generation'] < MAX_INTEGER or following['generation'] != previous['generation'] + 1:
            raise ContractError('GENERATION_MISMATCH')
        if following['contract_id'] == previous['contract_id']:
            raise ContractError('CONTRACT_ID_REUSED')
        if source_contract != previous or manifest['purpose'] != 'regression' or manifest['profile'] != 'full':
            raise ContractError('BASELINE_SOURCE_MISMATCH')
        if previous['comparison']['mode'] != 'required' or source_baseline_context is None or baseline['generation'] < 2:
            raise ContractError('BASELINE_SOURCE_MISMATCH')
        rebound = bind_run_manifest(manifest, previous, plan, policy, registry, case_set,
                                    baseline_context=source_baseline_context)
        if rebound != baseline_source_bound:
            raise ContractError('SOURCE_BINDING_MISMATCH')
        expected = content_ref('baseline', baseline['baseline_id'], baseline)
        if following['comparison'] != {'mode': 'required', 'baseline_ref': expected, 'changed_axes': [], 'reason': None}:
            raise ContractError('COMPARISON_MISMATCH')
        ignored = {'contract_id', 'generation', 'comparison'}
        if _same_without(previous, ignored) != _same_without(following, ignored):
            raise ContractError('UNSUPPORTED_REVISION_CONDITIONS')
        for field, kind, key, value in (
                ('contract_ref', 'evaluation_contract', 'contract_id', previous),
                ('policy_ref', 'policy_profile', 'policy_id', policy),
                ('registry_ref', 'control_registry', 'registry_id', registry),
                ('case_set_ref', 'case_set', 'case_set_id', case_set),
                ('trial_plan_ref', 'trial_plan', 'plan_id', plan),
                ('source_run_ref', 'run_manifest', 'run_id', manifest)):
            _ref(baseline[field], kind, value[key], value)
        if (baseline['target_refs'] != manifest['target_refs']
                or baseline['evaluator_refs'] != _evaluator_refs(registry)
                or baseline['oracle_refs'] != _oracle_refs(case_set)):
            raise ContractError('BASELINE_SOURCE_MISMATCH')
        repeat = repeat_config_for_plan(plan)
        _ref(baseline['repeat_config_ref'], 'repeat_config', repeat['repeat_config_id'], repeat)
        comparison = {
            'schema_version': 1, 'kind': 'comparison_context',
            'comparison_id': baseline['comparison_context_ref']['id'],
            **deepcopy(previous['comparison']), 'expected_contract_generation': previous['generation'],
            'expected_baseline_generation': baseline['generation'] - 1,
            **{key: deepcopy(baseline[key]) for key in ('contract_ref', 'policy_ref', 'target_refs',
                'evaluator_refs', 'case_set_ref', 'oracle_refs', 'repeat_config_ref')},
        }
        _ref(baseline['comparison_context_ref'], 'comparison_context', comparison['comparison_id'], comparison)
        targets = {c['control_id']: c['target_ref'] for c in registry['controls']}
        context = {'baseline_ref': expected, 'targets': [
            {'control_id': c, 'target_ref': deepcopy(targets[c])} for c in rebound['selected_controls']]}
        new_plan = deepcopy(plan)
        new_plan['contract_ref'] = content_ref('evaluation_contract', following['contract_id'], following)
        targets_by_obligation = {o['obligation_id']: c['target_ref']
            for c in registry['controls'] for o in c['obligations']}
        for entry in new_plan['entries']:
            entry['target_ref'] = deepcopy(targets_by_obligation[entry['obligation_id']])
        new_manifest = deepcopy(manifest)
        new_manifest.update(contract_ref=new_plan['contract_ref'], baseline_ref=expected,
                            plan_ref=content_ref('trial_plan', new_plan['plan_id'], new_plan))
        new_bound = bind_run_manifest(new_manifest, following, new_plan, policy, registry, case_set,
                                      baseline_context=context)
        inspection = inspect_revision(rebound, new_bound, previous_baseline_context=source_baseline_context,
                                      following_baseline_context=context)
        if not inspection['comparison']['same_measurement_conditions']:
            raise ContractError('MEASUREMENT_CONDITIONS_MISMATCH')
        return {'schema_version': 2, 'kind': 'contract_transition_binding',
            'previous_contract': previous, 'next_contract': following,
            'previous_contract_ref': content_ref('evaluation_contract', previous['contract_id'], previous),
            'next_contract_ref': content_ref('evaluation_contract', following['contract_id'], following),
            'source_run_ref': deepcopy(baseline['source_run_ref']), 'baseline_ref': expected,
            'baseline_record': baseline, 'source_bound': rebound,
            'source_baseline_context': deepcopy(source_baseline_context),
            'comparison': deepcopy(following['comparison']), 'conditions_comparison': inspection['comparison'],
            'structurally_bound': True, 'authority_connected': False, 'adoption_verified': False, 'ci_eligible': False}
    except ContractError:
        raise
    except (KeyError, TypeError, ValueError, RecursionError, OverflowError):
        raise ContractError('INVALID_CONTRACT_TRANSITION') from None


def build_following_runs(previous, following, *, baseline_record, source_prepared,
                         now, old_run_id, new_run_id):
    """保存sourceの旧比較を維持し、新比較はcandidate側だけから再構成する。"""
    from .transition_materialization import _bind, _materialize, _plan
    try:
        require_object(source_prepared, {'bound_run', 'baseline_context', 'materialization'})
        require_uint(now)
        for identifier in (old_run_id, new_run_id):
            require_id(identifier)
            if len(identifier) > 64:
                raise ContractError('INVALID_ID')
        source = source_prepared['bound_run']
        transition = bind_following_transition(previous, following, baseline_record=baseline_record,
            baseline_source_bound=source, source_baseline_context=source_prepared['baseline_context'])
        if (len({old_run_id, new_run_id, source['manifest']['run_id']}) != 3
                or now < source['manifest']['created_at']):
            raise ContractError('RUN_ID_REUSED_OR_TIME_INVALID')
        material = source_prepared['materialization']
        require_object(material, {'manifest', 'manifest_ref', 'pack_ref',
                                 'structurally_bound', 'authority_connected', 'ci_eligible'})
        if (material['structurally_bound'] is not True or material['authority_connected'] is not False
                or material['ci_eligible'] is not False
                or material['manifest']['run_id'] != source['manifest']['run_id']):
            raise ContractError('SOURCE_MATERIALIZATION_INVALID')
        _ref(material['manifest_ref'], 'fixture_manifest', material['manifest']['materialization_id'], material['manifest'])
        entries = source['plan']['entries']
        records = material['manifest']['records']
        keys = ('obligation_id', 'case_id', 'trial_id', 'variant')
        if (len(records) != len(entries) or len(entries) != 30
                or {tuple(e[k] for k in keys) for e in entries} != {tuple(r[k] for k in keys) for r in records}):
            raise ContractError('SOURCE_MATERIALIZATION_INVALID')
        candidate_plan = deepcopy(source['plan'])
        candidate_plan['entries'] = [e for e in candidate_plan['entries'] if e['variant'] == 'candidate']
        if len(candidate_plan['entries']) != 15:
            raise ContractError('SOURCE_MATERIALIZATION_INVALID')
        old_plan = _plan(source['plan'], previous, old_run_id, include_baseline=False)
        new_plan = _plan(candidate_plan, following, new_run_id, include_baseline=True)
        targets = {c['control_id']: c['target_ref'] for c in source['registry']['controls']}
        context = {'baseline_ref': deepcopy(transition['baseline_ref']), 'targets': [
            {'control_id': c, 'target_ref': deepcopy(targets[c])} for c in source['selected_controls']]}
        old_context = deepcopy(source_prepared['baseline_context'])
        old_bound = _bind(source, previous, old_plan, old_run_id, now, purpose='contract_old_regression',
                         baseline_ref=old_context['baseline_ref'], baseline_context=old_context)
        new_bound = _bind(source, following, new_plan, new_run_id, now, purpose='contract_candidate',
                         baseline_ref=context['baseline_ref'], baseline_context=context)
        # 実効時間上限も元条件。policy最大値への拡張はしない。
        elapsed = source['manifest']['deadline'] - source['manifest']['created_at']
        for bound in (old_bound, new_bound):
            bound['manifest']['deadline'] = now + elapsed
        candidate_material = deepcopy(material)
        candidate_material['manifest']['records'] = [r for r in records if r['variant'] == 'candidate']
        old_mat = _materialize(material, material['pack_ref'], old_run_id, now, record_baseline=False)
        new_mat = _materialize(candidate_material, material['pack_ref'], new_run_id, now, record_baseline=True)
        return {'transition': transition,
                'old': {'bound_run': old_bound, 'baseline_context': old_context, 'materialization': old_mat},
                'new': {'bound_run': new_bound, 'baseline_context': context, 'materialization': new_mat},
                'structurally_bound': True, 'authority_connected': False, 'ci_eligible': False}
    except ContractError:
        raise
    except (KeyError, TypeError, ValueError, RecursionError, OverflowError):
        raise ContractError('INVALID_TRANSITION_MATERIALIZATION') from None
