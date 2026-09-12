"""同一SQLite transaction内の重複照合と、依存巡回の上限。"""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from functools import wraps
from inspect import signature
import sqlite3

_state = ContextVar('gah_read_checks', default=None)
MAX_DEPTH = 128
MAX_NODES = 1024


def _error(code):
    from .adoption import AdoptionError
    return AdoptionError(code)


def _freeze(value):
    if isinstance(value, sqlite3.Row):
        return ('row', tuple((key, _freeze(value[key])) for key in value.keys()))
    if type(value) is dict:
        return ('dict', tuple(sorted((_freeze(k), _freeze(v)) for k, v in value.items())))
    if type(value) in (list, tuple):
        return (type(value).__name__, tuple(_freeze(v) for v in value))
    if type(value) in (str, int, bool, float, bytes, type(None)):
        return (type(value).__name__, value)
    return ('object', id(value))


def _copy(value):
    if isinstance(value, sqlite3.Row):
        return value  # sqlite3.Rowは不変。接続へ依存するcursorを含まない。
    if type(value) is dict:
        return {key: _copy(item) for key, item in value.items()}
    if type(value) is list:
        return [_copy(item) for item in value]
    if type(value) is tuple:
        return tuple(_copy(item) for item in value)
    return deepcopy(value)


@contextmanager
def scope(db):
    # 呼出し境界で必ず新しい領域を作り、別requestに持ち越さない。
    token = _state.set({'db': db, 'changes': db.total_changes,
                       'values': {}, 'active': set(), 'nodes': 0, 'cacheable': True})
    try:
        yield
    finally:
        _state.reset(token)


def checked_read(function):
    parameters = signature(function)
    @wraps(function)
    def wrapped(*args, **kwargs):
        bound = parameters.bind(*args, **kwargs)
        bound.apply_defaults()
        db = bound.arguments['db']
        state = _state.get()
        if state is None or state['db'] is not db:
            with scope(db):
                return wrapped(*args, **kwargs)
        changes = db.total_changes
        if changes != state['changes']:
            state['cacheable'] = False
            state['values'].clear()
        key = (function, tuple((k, _freeze(v)) for k, v in bound.arguments.items() if k != 'db'))
        if key in state['active']:
            raise _error('DEPENDENCY_CYCLE')
        # transaction外の呼出しは再読取りし、同じ接続でも他者commitを隠さない。
        if state['cacheable'] and db.in_transaction and key in state['values']:
            return _copy(state['values'][key])
        if len(state['active']) >= MAX_DEPTH or state['nodes'] >= MAX_NODES:
            raise _error('DEPENDENCY_LIMIT')
        state['active'].add(key)
        state['nodes'] += 1
        try:
            result = function(*args, **kwargs)
            if db.total_changes != changes:
                raise _error('CHECK_MUTATED_STORAGE')
            if state['cacheable'] and db.in_transaction:
                state['values'][key] = _copy(result)
            return result
        finally:
            state['active'].remove(key)
    return wrapped


def checked_action(function):
    parameters = signature(function)
    @wraps(function)
    def wrapped(*args, **kwargs):
        db = parameters.bind(*args, **kwargs).arguments['db']
        with scope(db):
            return function(*args, **kwargs)
    return wrapped
