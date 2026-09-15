"""完了runの保存構造検査を、全保存行の同一性に限って再利用する。"""
from collections import OrderedDict
import hashlib
import json
from threading import RLock

_MAX_BYTES=4*1024*1024
_MAX_ROWS=10000
_MAX_MARKERS=32
_ORDER={'attempts':'attempt_id','attempt_events':'event_id','evidence_events':'event_id',
        'aggregates':'aggregate_digest','decisions':'decision_digest','terminals':'run_id'}
_markers=OrderedDict()
_lock=RLock()


def clear():
    with _lock:_markers.clear()


def _snapshot(db,run_id,row,state):
    digest=hashlib.sha256();size=0;count=0
    def add(table,item):
        nonlocal size,count
        # 列名・SQLiteの値型・全payloadを含む。保存digestだけを鍵にしない。
        values=[[key,type(item[key]).__name__,item[key]] for key in sorted(item.keys())]
        encoded=json.dumps([table,values],ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf8')
        size+=len(encoded);count+=1
        if size>_MAX_BYTES or count>_MAX_ROWS:return False
        digest.update(len(encoded).to_bytes(8,'big'));digest.update(encoded);return True
    try:
        if not add('bound_runs',row) or not add('run_state',state):return None
        for table,order in _ORDER.items():
            for item in db.execute('SELECT * FROM '+table+' WHERE run_id=? ORDER BY '+order,(run_id,)):
                if not add(table,item):return None
    except (TypeError,ValueError,UnicodeError,RecursionError):return None
    return digest.digest()


def check(db,run_id,now,row,bound,profile,baseline,state,validate):
    """現在のbinding・権限・失効・期限判定は呼出し側が毎回行う。"""
    case_set=bound.get('case_set') if type(bound) is dict else None
    cases=case_set.get('cases') if type(case_set) is dict else None
    if not db.in_transaction or type(cases) is not list or not 400<=len(cases)<=415 or state['finalized_at'] is None:
        return validate(db,run_id,now,row,bound,profile,baseline,state)
    snapshot=_snapshot(db,run_id,row,state)
    if snapshot is None:return validate(db,run_id,now,row,bound,profile,baseline,state)
    from .evaluation_authority import _source_digest
    implementation=getattr(validate,'__func__',validate)
    key=(_source_digest(),implementation,run_id,snapshot)
    with _lock:
        validated_at=_markers.get(key)
        # 検査内の時刻条件は保存時刻<=now。過去へ戻る照会は元の検査へ渡す。
        if validated_at is not None and now>=validated_at:
            _markers.move_to_end(key);return None
    changes=db.total_changes
    validate(db,run_id,now,row,bound,profile,baseline,state)
    if db.total_changes==changes:
        with _lock:
            _markers[key]=now;_markers.move_to_end(key)
            while len(_markers)>_MAX_MARKERS:_markers.popitem(last=False)
    return None
