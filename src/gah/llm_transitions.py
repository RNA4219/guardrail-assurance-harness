"""初回合成LLM基準から、同じ測定条件の旧・新候補を構造的に生成する。"""
from copy import deepcopy
from functools import lru_cache
import json
from .wire import canonical_bytes
from .contracts import ContractError, require_id, require_uint
from .contract_updates import bind_contract_transition
from .run_contracts import content_ref
from .transition_materialization import _plan, _bind
from .execution_profiles import check_plan


def rebind(prepared, bound, baseline_context):
    """検査済みの固定入力を新しいrunへ結ぶ。入力本体と対象版を変更しない。"""
    result = {"bound_run":deepcopy(bound), "baseline_context":deepcopy(baseline_context),
        **{key:deepcopy(prepared[key]) for key in ("execution_profile","target_document","evaluator_document")}}
    if "target_documents" in prepared:
        result["target_documents"]=deepcopy(prepared["target_documents"])
    run_id = bound["manifest"]["run_id"]
    material = deepcopy(prepared["materialization"])
    material.update(run_id=run_id, manifest_ref=content_ref("run_manifest",run_id,bound["manifest"]),
        profile_ref=content_ref("execution_profile",run_id,result["execution_profile"]),
        planned_trials=len(bound["plan"]["entries"]), planned_stages=sum(len(e["stage_ids"]) for e in bound["plan"]["entries"]))
    result["materialization"] = material
    check_plan(result["execution_profile"],bound)
    return result


@lru_cache(maxsize=4)
def _cached_build(key,previous,following,baseline,source,now,old_run_id,new_run_id,registry):
    from .cache_inputs import encode_result
    return encode_result(_build(json.loads(previous),json.loads(following),baseline_record=json.loads(baseline),
        source_prepared=json.loads(source),now=now,old_run_id=old_run_id,new_run_id=new_run_id,following_registry=json.loads(registry)))


def build(previous, following, *, baseline_record, source_prepared, now, old_run_id, new_run_id, following_registry=None):
    from .llm_materialization import source_key
    require_uint(now)
    require_id(old_run_id);require_id(new_run_id)
    from .cache_inputs import plain
    if not plain([previous,following,baseline_record,source_prepared,following_registry]):
        return _build(previous,following,baseline_record=baseline_record,source_prepared=source_prepared,
            now=now,old_run_id=old_run_id,new_run_id=new_run_id,following_registry=following_registry)
    return json.loads(_cached_build(source_key(),canonical_bytes(previous),canonical_bytes(following),
        canonical_bytes(baseline_record),canonical_bytes(source_prepared),now,old_run_id,new_run_id,canonical_bytes(following_registry)))


def _build(previous, following, *, baseline_record, source_prepared, now, old_run_id, new_run_id, following_registry=None):
    """認証・採択は上位が実施する。ここでは同条件の初回移行だけを許す。"""
    try:
        require_uint(now)
        for identifier in (old_run_id,new_run_id):
            require_id(identifier)
            if len(identifier)>64:
                raise ContractError("INVALID_ID")
        source = source_prepared["bound_run"]
        if previous["use_cases"] != ["UC-LLM"] or previous["generation"] != 1:
            raise ContractError("UNSUPPORTED_LLM_TRANSITION")
        transition = bind_contract_transition(previous,following,baseline_record=baseline_record,baseline_source_bound=source,following_registry=following_registry)
        if len({old_run_id,new_run_id,source["manifest"]["run_id"]})!=3 or now<source["manifest"]["created_at"]:
            raise ContractError("RUN_ID_REUSED_OR_TIME_INVALID")
        old_plan = _plan(source["plan"],previous,old_run_id,include_baseline=False)
        new_plan = _plan(source["plan"],following,new_run_id,include_baseline=True)
        targets={c["control_id"]:c["target_ref"] for c in source["registry"]["controls"]}
        context={"baseline_ref":deepcopy(transition["baseline_ref"]),"targets":[
            {"control_id":key,"target_ref":deepcopy(targets[key])} for key in source["selected_controls"]]}
        old_bound=_bind(source,previous,old_plan,old_run_id,now,purpose="contract_old_regression",baseline_ref=None,baseline_context=None)
        new_source=source;new_prepared=source_prepared
        if transition.get("following_registry") is not None:
            from . import llm_materialization,guardrail_runtime
            from .docker_runner import PROFILE
            lock=guardrail_runtime.read_lock()
            options=[]
            for version in sorted(llm_materialization.VERSIONS):
                generated=llm_materialization.build(source["policy"],policy_generation=following["policy_generation"],
                    run_id=new_run_id,now=now,target_version=version,image_id=lock["image_id"],
                    worker_digest=lock["worker_digest"],isolation_profile=PROFILE,generation=2,baseline_context=context)
                if generated["bound_run"]["registry"]==transition["following_registry"]:
                    options.append(generated)
            if len(options)!=1:
                raise ContractError("UNSUPPORTED_TARGET_REVISION")
            new_prepared=options[0]
            new_prepared["target_documents"]=[deepcopy(source_prepared["target_document"]),deepcopy(new_prepared["target_document"])]
            new_source=deepcopy(source);new_source["registry"]=deepcopy(transition["following_registry"])
            new_source["manifest"]["target_refs"]=deepcopy(new_prepared["bound_run"]["manifest"]["target_refs"])
            targets={c["obligations"][0]["obligation_id"]:c["target_ref"] for c in new_source["registry"]["controls"]}
            for entry in new_plan["entries"]:
                if entry["variant"]=="candidate":entry["target_ref"]=deepcopy(targets[entry["obligation_id"]])
        new_bound=_bind(new_source,following,new_plan,new_run_id,now,purpose="contract_candidate",baseline_ref=context["baseline_ref"],baseline_context=context)
        for bound in (old_bound,new_bound):
            bound["manifest"]["deadline"]=now+source["manifest"]["deadline"]-source["manifest"]["created_at"]
        return {"transition":transition,"old":rebind(source_prepared,old_bound,None),
            "new":rebind(new_prepared,new_bound,context),"structurally_bound":True,"authority_connected":False,"ci_eligible":False}
    except ContractError:
        raise
    except (KeyError,TypeError,ValueError):
        raise ContractError("LLM_TRANSITION_INVALID") from None


@lru_cache(maxsize=2)
def _cached_following(source_digest, implementation, payload):
    from .cache_inputs import encode_result
    previous, following, baseline, prepared, now, old_id, new_id = json.loads(payload)
    return encode_result(implementation(previous, following, baseline_record=baseline,
        source_prepared=prepared, now=now, old_run_id=old_id, new_run_id=new_id))


def build_following(previous, following, *, baseline_record, source_prepared,
                    now, old_run_id, new_run_id):
    from .cache_inputs import plain, encode_result
    from .llm_materialization import source_key
    values = [previous, following, baseline_record, source_prepared, now, old_run_id, new_run_id]
    if plain(values):
        try:
            payload = encode_result(values)
        except (TypeError, ValueError, UnicodeError, RecursionError):
            payload = None
        if payload is not None and len(payload.encode('utf8')) <= 2*1024*1024:
            return json.loads(_cached_following(source_key(), _build_following, payload))
    return _build_following(previous, following, baseline_record=baseline_record,
        source_prepared=source_prepared, now=now, old_run_id=old_run_id, new_run_id=new_run_id)


def _build_following(previous, following, *, baseline_record, source_prepared,
                     now, old_run_id, new_run_id):
    """通常LLM全件を根拠に、旧比較を保持して次の基準へ結ぶ。採択は行わない。"""
    from .following_contracts import bind_following_transition
    from .contracts import require_object
    try:
        require_uint(now)
        for identifier in (old_run_id, new_run_id):
            require_id(identifier)
            if len(identifier) > 64:
                raise ContractError('INVALID_ID')
        required = {'bound_run', 'baseline_context', 'execution_profile',
                    'target_document', 'evaluator_document', 'materialization'}
        optional = {'target_documents'} if type(source_prepared) is dict and 'target_documents' in source_prepared else set()
        require_object(source_prepared, required | optional)
        source = source_prepared['bound_run']
        if previous['use_cases'] != ['UC-LLM']:
            raise ContractError('UNSUPPORTED_LLM_TRANSITION')
        transition = bind_following_transition(previous, following, baseline_record=baseline_record,
            baseline_source_bound=source, source_baseline_context=source_prepared['baseline_context'])
        if (len({old_run_id, new_run_id, source['manifest']['run_id']}) != 3
                or now < source['manifest']['created_at']):
            raise ContractError('RUN_ID_REUSED_OR_TIME_INVALID')
        material = source_prepared['materialization']
        entries = source['plan']['entries']
        candidates = [entry for entry in entries if entry['variant'] == 'candidate']
        if (len(source['case_set']['cases']) != 400 or len(entries) != 800 or len(candidates) != 400
                or material['kind'] != 'llm_input_materialization'
                or material['case_count'] != 400 or material['planned_trials'] != len(entries)
                or material['planned_stages'] != sum(len(entry['stage_ids']) for entry in entries)
                or material['manifest_ref'] != content_ref('run_manifest', source['manifest']['run_id'], source['manifest'])
                or material['profile_ref'] != content_ref('execution_profile', source['manifest']['run_id'], source_prepared['execution_profile'])):
            raise ContractError('SOURCE_MATERIALIZATION_INVALID')
        check_plan(source_prepared['execution_profile'], source)
        old_context = source_prepared['baseline_context']
        targets = {control['control_id']: control['target_ref'] for control in source['registry']['controls']}
        context = {'baseline_ref': deepcopy(transition['baseline_ref']), 'targets': [
            {'control_id': control, 'target_ref': deepcopy(targets[control])} for control in source['selected_controls']]}
        candidate_plan = {**source['plan'], 'entries': candidates}
        old_plan = _plan(source['plan'], previous, old_run_id, include_baseline=False)
        new_plan = _plan(candidate_plan, following, new_run_id, include_baseline=True)
        old_bound = _bind(source, previous, old_plan, old_run_id, now, purpose='contract_old_regression',
            baseline_ref=old_context['baseline_ref'], baseline_context=old_context)
        new_bound = _bind(source, following, new_plan, new_run_id, now, purpose='contract_candidate',
            baseline_ref=context['baseline_ref'], baseline_context=context)
        elapsed = source['manifest']['deadline'] - source['manifest']['created_at']
        for bound in (old_bound, new_bound):
            bound['manifest']['deadline'] = now + elapsed
        return {'transition': transition, 'old': rebind(source_prepared, old_bound, old_context),
            'new': rebind(source_prepared, new_bound, context),
            'structurally_bound': True, 'authority_connected': False, 'ci_eligible': False}
    except ContractError:
        raise
    except (KeyError, TypeError, ValueError, RecursionError, OverflowError):
        raise ContractError('LLM_TRANSITION_INVALID') from None
