"""完全入力の再利用、共有保持量の制限とclear競合を確認する。"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
from threading import Event
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from gah.immutable_cache import JsonCache


class ImmutableCacheTests(unittest.TestCase):
    def test_complete_key_and_namespaces_share_a_bounded_store(self):
        cache = JsonCache(20000, 4)
        calls = []
        @cache.memoize
        def first(source, impl, payload):
            calls.append(('first', source, payload))
            return impl(payload)
        @cache.memoize
        def second(source, impl, payload):
            calls.append(('second', source, payload))
            return impl(payload)
        identity = str
        for invoke in (first, second):
            self.assertEqual(invoke('a', identity, '{}'), '{}')
            self.assertEqual(invoke('a', identity, '{}'), '{}')
        first('b', identity, '{}')
        first('a', identity, '[]')
        self.assertEqual(len(calls), 4)
        first('a', lambda value: value, '{}')
        self.assertEqual(len(calls), 5)
        self.assertEqual(cache.info().currsize, 4)
        self.assertLessEqual(cache.info().retained_bytes, cache.info().max_bytes)
        first.cache_clear()
        self.assertEqual(cache.info().currsize, 1)
        second('a', identity, '{}')
        self.assertEqual(len(calls), 5)

    def test_byte_limit_and_oversized_result_do_not_flush_small_entries(self):
        cache = JsonCache(3600, 32)
        calls = []
        @cache.memoize
        def encode(payload):
            calls.append(payload)
            return payload
        for i in range(20):
            encode(str(i) + 'x' * 400)
            self.assertLessEqual(cache.info().retained_bytes, 3600)
        self.assertLess(cache.info().currsize, 20)
        before = cache.info()
        encode('z' * 4000)
        self.assertEqual(cache.info(), before)
        encode('19' + 'x' * 400)
        self.assertEqual(len(calls), 21)
        cache.clear()
        self.assertEqual(cache.info().currsize, 0)

    def test_lru_promotes_hits_and_limits_entry_count(self):
        cache = JsonCache(20000, 2)
        calls = []
        @cache.memoize
        def encode(payload):
            calls.append(payload)
            return payload
        for value in ['a', 'b', 'a', 'c', 'a', 'b']:
            encode(value)
        self.assertEqual(calls, ['a', 'b', 'c', 'b'])
        self.assertEqual(cache.info().currsize, 2)

    def test_exceptions_and_mutable_results_are_never_cached(self):
        cache = JsonCache(20000, 8)
        calls = []
        @cache.memoize
        def invalid(value):
            calls.append(value)
            if value == 'error':
                raise ValueError('FAILED')
            return []
        for _ in range(2):
            with self.assertRaisesRegex(ValueError, 'FAILED'): invalid('error')
            with self.assertRaisesRegex(TypeError, 'IMMUTABLE_JSON'): invalid('mutable')
        self.assertEqual(len(calls), 4)
        self.assertEqual(cache.info().currsize, 0)

    def test_clear_during_computation_does_not_restore_old_entries(self):
        cache = JsonCache(20000, 8)
        started, release = Event(), Event()
        @cache.memoize
        def slow(value):
            started.set()
            if not release.wait(5): raise RuntimeError('TEST_TIMEOUT')
            return value
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(slow, '{}')
            try:
                self.assertTrue(started.wait(5))
                slow.cache_clear()
            finally:
                release.set()
            self.assertEqual(future.result(timeout=5), '{}')
        self.assertEqual(cache.info().currsize, 0)

    def test_large_unicode_json_is_lossless_and_byte_keys_are_distinct(self):
        import zlib
        cache = JsonCache(20000, 8)
        calls = []
        @cache.memoize
        def echo(value):
            calls.append(1)
            return value if type(value) is str else 'bytes'
        payload = '{"text":"' + '日本語' * 6000 + '"}'
        self.assertEqual(echo(payload), payload)
        self.assertEqual(echo(payload), payload)
        self.assertEqual(len(calls), 1)
        self.assertLess(cache.info().retained_bytes, len(payload.encode('utf-8')))
        self.assertEqual(echo(zlib.compress(payload.encode('utf-8'), 1)), 'bytes')
        self.assertEqual(echo(payload + ' '), payload + ' ')
        self.assertEqual(len(calls), 3)

    def test_mutable_keys_bypass_cache(self):
        cache = JsonCache(20000, 8)
        calls = []
        @cache.memoize
        def encode(value):
            calls.append(1)
            return str(value)
        value = ['a']
        self.assertEqual(encode(value), "['a']")
        value.append('b')
        self.assertEqual(encode(value), "['a', 'b']")
        self.assertEqual(calls, [1, 1])
        self.assertEqual(cache.info().currsize, 0)


if __name__ == '__main__': unittest.main()
