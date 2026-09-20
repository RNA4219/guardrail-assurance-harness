"""同一SQLite transaction内の重複照合と、依存巡回の上限。"""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from functools import wraps
from inspect import signature
import json
import math
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
    if type(value) in (str, int, bool, float, bytes, type(None)):
        return value  # 不変のscalarを再帰的にdeepcopyへ渡す必要はない。
    return deepcopy(value)


# Cache snapshots are request-local; this marker never escapes the wrapper.
_JSON_CACHE_MARKER = object()


def _plain_json(value, depth=0):
    if depth > MAX_DEPTH:
        return False
    if type(value) in (str, int, bool, type(None)):
        return True
    if type(value) is float:
        return math.isfinite(value)
    if type(value) is list:
        return all(_plain_json(item, depth + 1) for item in value)
    if type(value) is dict:
        return all(type(key) is str and _plain_json(item, depth + 1)
                   for key, item in value.items())
    return False


def _json_cache_snapshot(value):
    if not _plain_json(value):
        return None
    try:
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False,
                         separators=(",", ":")).encode("utf-8", errors="strict")
        return (_JSON_CACHE_MARKER, "json", raw)
    except (TypeError, ValueError, RecursionError, UnicodeEncodeError):
        # Preserve Python's exact string representation for invalid Unicode.
        return None


def _cache_snapshot(value):
    if type(value) is tuple:
        # A root tuple can mix immutable sqlite rows/custom values with large
        # plain-JSON subtrees. Snapshot each slot independently; nested tuples
        # and other non-JSON values retain the legacy copy semantics.
        parts = []
        for item in value:
            encoded = _json_cache_snapshot(item)
            parts.append(encoded if encoded is not None else _copy(item))
        return (_JSON_CACHE_MARKER, "tuple_parts", tuple(parts))
    encoded = _json_cache_snapshot(value)
    return encoded if encoded is not None else _copy(value)


def _cache_restore(value):
    if type(value) is tuple and len(value) == 3 and value[0] is _JSON_CACHE_MARKER:
        if value[1] == "tuple_parts" and type(value[2]) is tuple:
            return tuple(_cache_restore(part) if (
                type(part) is tuple and len(part) == 3 and part[0] is _JSON_CACHE_MARKER
            ) else _copy(part) for part in value[2])
        if value[1] == "json":
            try:
                return json.loads(value[2].decode("utf-8", errors="strict"))
            except (AttributeError, TypeError, ValueError, UnicodeDecodeError, RecursionError):
                raise _error("READ_CACHE_CORRUPT") from None
        raise _error("READ_CACHE_CORRUPT")
    return _copy(value)


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
            return _cache_restore(state['values'][key])
        if len(state['active']) >= MAX_DEPTH or state['nodes'] >= MAX_NODES:
            raise _error('DEPENDENCY_LIMIT')
        state['active'].add(key)
        state['nodes'] += 1
        try:
            result = function(*args, **kwargs)
            if db.total_changes != changes:
                raise _error('CHECK_MUTATED_STORAGE')
            if state['cacheable'] and db.in_transaction:
                state['values'][key] = _cache_snapshot(result)
            return result
        finally:
            state['active'].remove(key)
    return wrapped


_source_context = ContextVar('gah_source_context', default=None)
_source_invalid = False


def source_digest_in_scope():
    if _source_invalid:
        raise _error('EXTENSION_INVALID')
    return _source_context.get()


@contextmanager
def source_scope():
    """要求内のソース照合をまとめ、完了前に再確認する。変更検出後は再起動を要する。"""
    from .evaluation_authority import _compute_source_digest
    active = source_digest_in_scope()
    if active is not None:
        yield
        return
    before = _compute_source_digest()
    token = _source_context.set(before)
    global _source_invalid
    try:
        try:
            yield
        finally:
            try:
                if _source_invalid or _compute_source_digest() != before:
                    raise _error('EXTENSION_INVALID')
            except BaseException as error:
                # 途中で作られた計算cacheを、その後の要求で再利用しない。
                _source_invalid = True
                if isinstance(error, Exception):
                    raise _error('EXTENSION_INVALID') from None
                raise
    finally:
        _source_context.reset(token)


def checked_action(function):
    parameters = signature(function)
    @wraps(function)
    def wrapped(*args, **kwargs):
        db = parameters.bind(*args, **kwargs).arguments['db']
        with scope(db), source_scope():
            return function(*args, **kwargs)
    return wrapped
