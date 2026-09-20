"""純粋な計算cacheのJSON化で、入力型の検査を省略しないための条件。"""

def plain(value):
    pending=[(value,0)];seen=0
    while pending:
        item,depth=pending.pop();seen+=1
        if depth>64 or seen>250000:return False
        if type(item) is dict:
            if any(type(key) is not str for key in item):return False
            pending.extend((child,depth+1) for child in item.values())
        elif type(item) is list:
            pending.extend((child,depth+1) for child in item)
        elif type(item) not in (str,int,bool,float,type(None)):
            return False
    return True


from functools import lru_cache
import json


def encode_result(value):
    """検証を終えた純粋関数のJSON結果だけを、不変な文字列として保持する。"""
    return json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False)


from .immutable_cache import binding_cache


_CACHE_LARGE_CASE_COUNTS = frozenset(range(400, 416))
_CACHE_SMALL_CI_CASE_COUNTS = frozenset((15, 30))


def _cacheable_case_set(manifest, contract, case_set):
    """固定大規模集合と固定CI小集合だけを純粋cacheへ送る。"""
    if type(case_set) is not dict or type(case_set.get('cases')) is not list:
        return False
    count = len(case_set['cases'])
    if count in _CACHE_LARGE_CASE_COUNTS:
        return True
    return (count in _CACHE_SMALL_CI_CASE_COUNTS
            and type(manifest) is dict
            and manifest.get('use_cases') == ['UC-CI']
            and type(contract) is dict
            and contract.get('use_cases') == ['UC-CI']
            and case_set.get('purpose') == 'acceptance')


@binding_cache.memoize
def _bound_manifest(source_digest, implementation, payload):
    manifest,contract,plan,policy,registry,case_set,baseline=json.loads(payload)
    return encode_result(implementation(manifest,contract,plan,policy,registry,case_set,baseline_context=baseline))


def bind_run_manifest(manifest,contract,plan,policy,registry,case_set,*,baseline_context=None):
    """固定JSONだけを完全入力で再束縛する。認証・DB状態を扱わない。"""
    from .run_contracts import bind_run_manifest as implementation
    values=[manifest,contract,plan,policy,registry,case_set,baseline_context]
    eligible=_cacheable_case_set(manifest,contract,case_set)
    if eligible and plain(values):
        try:payload=encode_result(values)
        except (TypeError,ValueError,UnicodeError,RecursionError):payload=None
        if payload is not None and len(payload.encode('utf8'))<=3*1024*1024:
            from .evaluation_authority import _source_digest
            return json.loads(_bound_manifest(_source_digest(),implementation,payload))
    return implementation(manifest,contract,plan,policy,registry,case_set,baseline_context=baseline_context)


@lru_cache(maxsize=2)
def _evaluation_inputs(source_digest, binder, reporter, payload):
    contract,policy,registry,acceptance,calibration,other_sets=json.loads(payload)
    bound=binder(contract,policy,registry,acceptance,calibration)
    report=reporter(acceptance,other_sets=tuple(other_sets))
    return encode_result({'contract':bound['contract'],'corpus_report':report})


def evaluation_inputs(contract,policy,registry,acceptance,calibration,other_sets):
    """全資料を読み取った後の純粋検査だけを再利用する。現在の採択・権限・時刻は含まない。"""
    from .run_contracts import bind_evaluation_contract
    from .corpus import corpus_report
    values=[contract,policy,registry,acceptance,calibration,list(other_sets)]
    eligible=(type(acceptance) is dict and type(acceptance.get('cases')) is list
              and 400<=len(acceptance['cases'])<=415)
    if eligible and plain(values):
        try:payload=encode_result(values)
        except (TypeError,ValueError,UnicodeError,RecursionError):payload=None
        if payload is not None and len(payload.encode('utf8'))<=512*1024:
            from .evaluation_authority import _source_digest
            return json.loads(_evaluation_inputs(_source_digest(),bind_evaluation_contract,corpus_report,payload))
    bound=bind_evaluation_contract(contract,policy,registry,acceptance,calibration)
    return {'contract':bound['contract'],'corpus_report':corpus_report(acceptance,other_sets=tuple(values[-1]))}
