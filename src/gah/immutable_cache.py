"""純粋なJSON再束縛の保持量を、二つの利用箇所で共有して制限する。"""
from collections import OrderedDict, namedtuple
from functools import wraps
import sys
import zlib
from threading import RLock

CacheInfo = namedtuple('CacheInfo', 'retained_bytes max_bytes currsize maxsize')


def _compact(value):
    if type(value) is str and len(value) > 4096:
        return zlib.compress(value.encode('utf-8', 'surrogatepass'), 1)
    return value


def _restore(value):
    if type(value) is bytes:
        return zlib.decompress(value).decode('utf-8', 'surrogatepass')
    return value


def _key_size(value):
    return sys.getsizeof(value) + (sum(_key_size(part) for part in value) if type(value) is tuple else 0)


class JsonCache:
    """鍵は固定実装のidentityと完全な不変入力。認証やDB状態は保存しない。

    長いJSONは可逆圧縮した全内容で照合し、digestだけの一致へ置き換えない。
    retained_bytesは鍵・圧縮結果・entryと辞書のPython保持サイズの保守的な合計。
    固定module関数の参照先や、計算中の一時メモリはこの上限に含まない。
    """
    def __init__(self, max_bytes, max_entries):
        self._entries = OrderedDict()
        if max_bytes < sys.getsizeof(self._entries) or max_entries < 1:
            raise ValueError('INVALID_CACHE_LIMIT')
        self._max_bytes = max_bytes
        self._max_entries = max_entries
        self._bytes = 0
        self._epoch = 0
        self._lock = RLock()

    def info(self):
        with self._lock:
            return CacheInfo(self._bytes + sys.getsizeof(self._entries),
                             self._max_bytes, len(self._entries), self._max_entries)

    def clear(self, namespace=None):
        with self._lock:
            self._epoch += 1
            for key in list(self._entries):
                if namespace is None or key[0] is namespace:
                    _, size = self._entries.pop(key)
                    self._bytes -= size
            if not self._entries:
                self._entries.clear()

    def memoize(self, function):
        @wraps(function)
        def cached(*args):
            # 利用箇所は完全JSONと固定module関数だけを渡す。
            if any(type(arg) not in (str, bytes) and not callable(arg) for arg in args):
                return function(*args)
            key = (function, *((str, _compact(arg)) if type(arg) is str and len(arg) > 4096 else arg for arg in args))
            try:
                hash(key)
            except TypeError:
                return function(*args)
            with self._lock:
                found = self._entries.get(key)
                if found is not None:
                    self._entries.move_to_end(key)
                    return _restore(found[0])
                epoch = self._epoch
            result = function(*args)
            if type(result) is not str:
                raise TypeError('IMMUTABLE_JSON_RESULT_REQUIRED')
            stored = _compact(result)
            size = (_key_size(key) + sys.getsizeof(stored)
                    + sys.getsizeof((None, 0)) + sys.getsizeof(0))
            with self._lock:
                # clear中の計算、大きすぎる結果、同じ鍵の競合計算は保持しない。
                if epoch != self._epoch or size + sys.getsizeof(OrderedDict()) > self._max_bytes:
                    return result
                if key not in self._entries:
                    self._entries[key] = (stored, size)
                    self._bytes += size
                self._entries.move_to_end(key)
                while (len(self._entries) > self._max_entries
                       or self._bytes + sys.getsizeof(self._entries) > self._max_bytes):
                    _, (_, removed_size) = self._entries.popitem(last=False)
                    self._bytes -= removed_size
                    if not self._entries:
                        self._entries.clear()
            return result
        cached.cache_clear = lambda: self.clear(function)
        cached.cache_info = self.info
        return cached


binding_cache = JsonCache(16 * 1024 * 1024, 32)
