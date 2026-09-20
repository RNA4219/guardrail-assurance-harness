"""固定query-scale入力を既存admission表へ参照形式で保存する。"""
from functools import lru_cache
from copy import deepcopy
import hashlib
import json

from .adoption import AdoptionError
from .contracts import ContractError, MAX_DOCUMENT_BYTES, MAX_INTEGER, require_id, require_object, require_uint
from .docker_runner import PROFILE
from . import guardrail_runtime, llm_admission, partitioned_llm_materialization
from .partitioned_run_contracts import materialize_partitioned_run
from .run_contracts import content_ref
from .wire import canonical_bytes
from .read_checks import checked_read

KIND = 'partitioned_guardrail_admission'
_INDEX_FIELDS = {'schema_version', 'kind', 'run_id', 'case_count', 'target_version',
                 'policy_generation', 'source_digest', 'binding', 'artifact_refs', 'ci_eligible'}


def _error(code='PARTITIONED_LLM_ADMISSION_INVALID'):
    return AdoptionError(code)


def _reference(name, document):
    # registry/CaseSetは既存採択が解決する名前。他の版違いartifactは内容IDで保持。
    if name == 'registry':
        return content_ref('control_registry', document['registry_id'], document)
    if name in {'case_set', 'calibration_case_set'}:
        return content_ref('case_set', document['case_set_id'], document)
    digest = hashlib.sha256(canonical_bytes(document)).hexdigest()
    return content_ref('partitioned_input_artifact', digest, document)


def _artifact_index(documents):
    refs, artifacts = {}, {}
    for name, value in documents.items():
        if value is None:
            refs[name] = None
            continue
        items = value if name in {'plan_segments','case_set_segments','document_segments'} else [value]
        references=[]
        for item in items:
            ref=_reference(name,item);raw=canonical_bytes(item)
            key=(ref['kind'],ref['id'])
            if key in artifacts and artifacts[key][1]!=raw:
                raise _error('OBJECT_CONFLICT')
            artifacts[key]=(ref,raw);references.append(ref)
        refs[name] = references if name in {'plan_segments','case_set_segments','document_segments'} else references[0]
    return refs, artifacts


def _prepared_parts(prepared):
    from .partitioned_run_contracts import validate_partitioned_runtime
    bound = validate_partitioned_runtime(prepared['bound_run'], prepared['baseline_context'])
    if any(prepared[name] != bound[name] for name in ('manifest','contract','policy','registry','case_set')):
        raise _error('BINDING_MISMATCH')
    if (prepared['plan_index'] != bound['_partitioned_context']['index']
            or prepared['plan_segments'] != bound['_partitioned_context']['segments']):
        raise _error('BINDING_MISMATCH')
    documents = {key:value for key,value in prepared.items() if key not in {'bound_run','ci_eligible'}}
    refs, artifacts = _artifact_index(documents)
    value = {'schema_version':2, 'kind':'partitioned_prepared_run',
             'run_id':bound['manifest']['run_id'], 'binding':bound['_partitioned_receipt'],
             'artifact_refs':refs, 'ci_eligible':False}
    content_ref('partitioned_prepared_run', value['run_id'], value)
    return value, artifacts


def compact_prepared(prepared):
    """完全な内部入力から、保存・wire用の小さい参照を決定的に作る。"""
    return _prepared_parts(prepared)[0]



def _verify_expected_artifact_bytes(db, ref, expected_raw):
    """保存rowのnamespace/digest/bytesだけを、信頼済みexpectedへ直結する。"""
    row = db.execute("SELECT digest,payload_json FROM eval_objects WHERE kind=? AND id=? "
                     "AND typeof(payload_json)='text' AND length(CAST(payload_json AS BLOB))<=?",
                     (ref['kind'], ref['id'], MAX_DOCUMENT_BYTES)).fetchone()
    if row is None:
        raise _error('REFERENCE_MISSING')
    if row['digest'] != ref['digest']:
        raise _error('REFERENCE_MISSING')
    payload = row['payload_json']
    if type(payload) is not str or len(payload) > MAX_DOCUMENT_BYTES:
        raise _error('STORAGE_CORRUPT')
    try:
        actual_raw = payload.encode('utf-8')
    except UnicodeError:
        raise _error('STORAGE_CORRUPT') from None
    if len(actual_raw) > MAX_DOCUMENT_BYTES or actual_raw != expected_raw:
        raise _error('STORAGE_CORRUPT')



def verify_expected_prepared(db, root, expected_prepared):
    """Trusted factory出力を、保存済みcompact root/artifactへ直接照合する。"""
    from .evaluation_authority import _object
    expected_root, artifacts = _prepared_parts(expected_prepared)
    if type(root) is not dict or root != expected_root:
        raise _error('BINDING_MISMATCH')
    stored_root = _object(db, content_ref('partitioned_prepared_run', expected_root['run_id'], expected_root))
    if stored_root != expected_root:
        raise _error('BINDING_MISMATCH')
    for ref, expected_raw in artifacts.values():
        _verify_expected_artifact_bytes(db, ref, expected_raw)
    return expected_prepared


def store_prepared(db, prepared):
    """既存の不変object表へ保存する。採択や開始の権限は与えない。"""
    from .evaluation_authority import _store_object
    if not db.in_transaction:
        raise _error('TRANSACTION_REQUIRED')
    value, artifacts = _prepared_parts(prepared)
    for ref, raw in artifacts.values():
        if _store_object(db,ref['kind'],ref['id'],json.loads(raw)) != ref:
            raise _error('BINDING_MISMATCH')
    _store_object(db, 'partitioned_prepared_run', value['run_id'], value)
    return value


def load_prepared(db, value):
    """保存された内容参照を復元する。固定factoryとの比較は呼出側が行う。"""
    from .evaluation_authority import _object
    require_object(value, {'schema_version','kind','run_id','binding','artifact_refs','ci_eligible'})
    if type(value['schema_version']) is not int or value['schema_version'] != 2 or value['kind'] != 'partitioned_prepared_run' or value['ci_eligible'] is not False:
        raise _error('BINDING_MISMATCH')
    if _object(db,content_ref('partitioned_prepared_run',value['run_id'],value)) != value:
        raise _error('BINDING_MISMATCH')
    prepared = _loaded_value(db,value)['prepared']
    if compact_prepared(prepared) != value:
        raise _error('BINDING_MISMATCH')
    return prepared


def prepared_for_begin(db, run_id, now):
    """開始前の入力を取得する。呼出側は採択済み契約/factoryへ必ず再照合する。"""
    from .evaluation_authority import _load_json
    if db.execute('SELECT 1 FROM fixture_admissions WHERE run_id=?',(run_id,)).fetchone():
        return for_run(db,run_id,now)['prepared']
    row=db.execute("SELECT * FROM eval_objects WHERE kind='partitioned_prepared_run' AND id=?",(run_id,)).fetchone()
    if row is None:
        raise _error('RUN_MISSING')
    return load_prepared(db,_load_json(row,'payload_json','digest'))


@lru_cache(maxsize=2)
def _expected(policy_raw, policy_generation, run_id, created_at, version, case_count, lock_raw, source_digest):
    """純粋factoryの検査済みbytesだけを再利用。DB状態の正当性はcacheしない。"""
    policy, lock = json.loads(policy_raw), json.loads(lock_raw)
    full = partitioned_llm_materialization.build(policy, case_count=case_count,
        policy_generation=policy_generation, run_id=run_id, now=created_at, target_version=version,
        image_id=lock['image_id'], worker_digest=lock['worker_digest'], isolation_profile=PROFILE)
    calibration = llm_admission._calibration(source_digest)
    if calibration['evaluator_calibration_passed'] is not True:
        raise _error('CALIBRATION_UNAVAILABLE')
    documents = {key:value for key,value in full.items() if key not in {'bound_run','ci_eligible'}}
    documents['runtime_lock'] = lock
    documents['calibration'] = {'passed':True, 'measurement':calibration,
        'target_agreement_is_admission_condition':False, 'ci_eligible':False}
    refs, artifacts = _artifact_index(documents)
    index = {'schema_version':2,'kind':KIND,'run_id':run_id,'case_count':case_count,
        'target_version':version,'policy_generation':policy_generation,'source_digest':source_digest,
        'binding':full['bound_run'],'artifact_refs':refs,'ci_eligible':False}
    # 各保存artifactの通常上限はcontent_ref側で検査済み。集合の一括wire化は禁止。
    return canonical_bytes(index), tuple((canonical_bytes(ref),raw) for ref,raw in artifacts.values())


def _expected_now(policy, policy_generation, run_id, created_at, version, case_count):
    from .evaluation_authority import _source_digest
    source = _source_digest()
    lock = guardrail_runtime.read_lock()
    index, artifacts = _expected(canonical_bytes(policy),policy_generation,run_id,created_at,
                                 version,case_count,canonical_bytes(lock),source)
    if _source_digest()!=source:
        raise _error('EXTENSION_INVALID')
    return json.loads(index), artifacts


def resolve_prepared(value, fetch_ref):
    """参照を個別取得し、全文をprocess内だけで再構築する。採択判定はしない。"""
    try:
        require_object(value, {'schema_version','kind','run_id','binding','artifact_refs','ci_eligible'})
        if (type(value['schema_version']) is not int or value['schema_version'] != 2
                or value['kind'] != 'partitioned_prepared_run' or value['ci_eligible'] is not False):
            raise _error('BINDING_MISMATCH')
        prepared = _resolve_value(value, fetch_ref)['prepared']
        if compact_prepared(prepared) != value:
            raise _error('BINDING_MISMATCH')
        return prepared
    except (KeyError,TypeError,ValueError,RecursionError):
        raise _error('BINDING_MISMATCH') from None


def _loaded_value(db, index):
    from .evaluation_authority import _object
    return _resolve_value(index, lambda ref: _object(db, ref))


def _resolve_value(index, fetch_ref):
    full={}
    for name,reference in index['artifact_refs'].items():
        if reference is None:
            full[name]=None
        elif type(reference) is list:
            full[name]=[fetch_ref(ref) for ref in reference]
        else:
            full[name]=fetch_ref(reference)
    full['ci_eligible'] = False
    full['bound_run'] = materialize_partitioned_run(full['manifest'],full['contract'],full['plan_index'],
        full['plan_segments'],full['policy'],full['registry'],full['case_set'],
        baseline_context=full['baseline_context'])
    if full['bound_run']['_partitioned_receipt'] != index['binding']:
        raise _error()
    return {'kind':KIND,'prepared':full,'runtime_lock':full.get('runtime_lock'),
            'materialization':full['materialization'],'calibration':full.get('calibration'),
            'admission_index':index}


@checked_read
def _verify(db, row, now):
    from .evaluation_authority import _load_json, _object
    try:
        require_uint(now)
        for field in ('created_at','permission_generation'):
            require_uint(row[field])
        if row['created_at']>now:
            raise _error()
        index=_load_json(row,'payload_json','digest')
        require_object(index,_INDEX_FIELDS)
        if (type(index['schema_version']) is not int or index['schema_version']!=2
                or index['kind']!=KIND or index['ci_eligible'] is not False or index['run_id']!=row['run_id']):
            raise _error()
        policy=_object(db,index['artifact_refs']['policy'])
        expected, artifacts = _expected_now(policy,index['policy_generation'],row['run_id'],row['created_at'],
                                             index['target_version'],index['case_count'])
        if index!=expected or index['binding']['contract_ref']['digest']!=row['contract_digest']:
            raise _error()
        # 各保存artifactを一度だけ完全検証し、同じverified documentから復元する。
        verified = {}
        for raw_ref,raw in artifacts:
            ref = json.loads(raw_ref)
            _verify_expected_artifact_bytes(db,ref,raw)
            verified[(ref['kind'],ref['id'],ref['digest'])] = json.loads(raw)
        def fetch_verified(ref):
            key=(ref['kind'],ref['id'],ref['digest'])
            return deepcopy(verified[key])
        return _resolve_value(index,fetch_verified)
    except AdoptionError:
        raise
    except (ContractError,KeyError,TypeError,ValueError,RecursionError,OSError):
        raise _error() from None


def prepare(db, policy, policy_generation, run_id, now, permission_generation, target_version, case_count):
    from .evaluation_authority import _store_object
    if not db.in_transaction:
        raise _error('TRANSACTION_REQUIRED')
    require_id(run_id);require_uint(now);require_uint(permission_generation)
    existing=db.execute('SELECT * FROM fixture_admissions WHERE run_id=?',(run_id,)).fetchone()
    if existing is not None:
        value=_verify(db,existing,now);index=value['admission_index']
        if (existing['permission_generation']!=permission_generation or value['prepared']['policy']!=policy
                or index['policy_generation']!=policy_generation or index['target_version']!=target_version
                or type(case_count) is not int or index['case_count']!=case_count):
            raise _error('RUN_CONFLICT')
        return value
    for table in ('eval_runs','resource_runs','transition_runs','bound_runs'):
        if db.execute('SELECT 1 FROM '+table+' WHERE run_id=?',(run_id,)).fetchone():
            raise _error('RUN_CONFLICT')
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='eval_runs_v2'").fetchone():
        if db.execute('SELECT 1 FROM eval_runs_v2 WHERE run_id=?',(run_id,)).fetchone():
            raise _error('RUN_CONFLICT')
    index,artifacts=_expected_now(policy,policy_generation,run_id,now,target_version,case_count)
    contract_digest=index['binding']['contract_ref']['digest']
    if db.execute('SELECT 1 FROM fixture_admissions WHERE contract_digest=?',(contract_digest,)).fetchone():
        raise _error('CONTRACT_ADMISSION_EXISTS')
    for raw_ref,raw in artifacts:
        ref=json.loads(raw_ref)
        if _store_object(db,ref['kind'],ref['id'],json.loads(raw))!=ref:
            raise _error()
    raw=canonical_bytes(index)
    db.execute('INSERT INTO fixture_admissions VALUES(?,?,?,?,?,?)',
        (run_id,contract_digest,raw.decode('utf-8'),hashlib.sha256(raw).hexdigest(),now,permission_generation))
    return _loaded_value(db,index)


def for_contract(db, contract, now, permission_generation):
    digest=content_ref('evaluation_contract',contract['contract_id'],contract)['digest']
    row=db.execute('SELECT * FROM fixture_admissions WHERE contract_digest=?',(digest,)).fetchone()
    if row is None:
        return None
    value=_verify(db,row,now)
    if row['permission_generation']!=permission_generation:
        raise _error('CALIBRATION_UNAVAILABLE')
    if value['prepared']['contract']!=contract:
        raise _error()
    return value


def for_run(db, run_id, now):
    row=db.execute('SELECT * FROM fixture_admissions WHERE run_id=?',(run_id,)).fetchone()
    if row is not None:
        return _verify(db,row,now)
    from . import transition_authority, regression_runs
    mapping = db.execute('SELECT * FROM transition_runs WHERE run_id=?',(run_id,)).fetchone()
    if mapping is not None:
        _, candidate = transition_authority.load_candidate(db,mapping['candidate_id'],now)
        prepared = candidate['runs'][mapping['side']]
    else:
        run = db.execute('SELECT * FROM eval_runs WHERE run_id=?',(run_id,)).fetchone()
        if run is None:
            raise _error('LLM_ADMISSION_MISSING')
        prepared = regression_runs.for_run(db,run,now)
    if prepared['bound_run']['manifest'].get('schema_version') != 2:
        raise _error('BINDING_MISMATCH')
    return {'prepared':prepared}


def bound_for_row(db, row, contract, policy, now):
    from .evaluation_authority import _load_json
    full = for_run(db, row["run_id"], now)["prepared"]
    bound = full["bound_run"]
    if (bound["contract"] != contract or bound["policy"] != policy
            or bound["manifest"] != _load_json(row, "manifest_json", "manifest_digest")
            or full["plan_index"] != _load_json(row, "plan_json", "plan_digest")
            or contract["generation"] != row["contract_generation"]):
        raise _error("BINDING_MISMATCH")
    return bound, full["baseline_context"]


def prepared_response(value):
    full=value['prepared']
    return {'schema_version':2,'kind':'partitioned_guardrail_preparation',
        'admission_index':value['admission_index'],'contract':full['contract'],
        'manifest':full['manifest'],'materialization':value['materialization'],
        'calibration':value['calibration'],'ci_eligible':False}
