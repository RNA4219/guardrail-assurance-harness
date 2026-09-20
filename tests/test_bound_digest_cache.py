"""bound_bundle_digestのdigest-only純粋cache境界を検証する。"""
from copy import deepcopy
import unittest
from unittest.mock import patch

from tests import test_llm_target_revision as seed
from gah import run_evidence
from gah.immutable_cache import binding_cache


class BoundDigestCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        seed.LlmTargetRevisionTests.setUpClass.__func__(cls)

    def setUp(self):
        binding_cache.clear()
        self.bound = deepcopy(self.changed['bound_run'])
        self.context = deepcopy(self.context)
        self.assertEqual(len(self.bound['case_set']['cases']), 400)

    def oracle(self, bound=None, context='sentinel'):
        if bound is None:
            bound = self.bound
        if context == 'sentinel':
            context = self.context
        return run_evidence._pack(run_evidence._bound_uncached(bound, context))[1]

    def test_hit_matches_full_oracle_and_runs_binder_and_pack_once(self):
        expected = self.oracle()
        binder, packer = run_evidence._bound_uncached, run_evidence._pack
        with patch.object(run_evidence, '_bound_uncached', wraps=binder) as bind_spy, \
             patch.object(run_evidence, '_pack', wraps=packer) as pack_spy:
            first = run_evidence.bound_bundle_digest(self.bound, self.context)
            second = run_evidence.bound_bundle_digest(self.bound, self.context)
        self.assertEqual(first, expected)
        self.assertEqual(second, expected)
        self.assertEqual(bind_spy.call_count, 1)
        self.assertEqual(pack_spy.call_count, 1)

    def test_source_implementation_validator_and_limit_identities_invalidate(self):
        binder = run_evidence._bound_uncached
        with patch('gah.evaluation_authority._source_digest', return_value='a' * 64), \
             patch.object(run_evidence, '_bound_uncached', wraps=binder) as bind_spy:
            run_evidence.bound_bundle_digest(self.bound, self.context)
            run_evidence.bound_bundle_digest(self.bound, self.context)
            self.assertEqual(bind_spy.call_count, 1)
            with patch('gah.evaluation_authority._source_digest', return_value='b' * 64):
                run_evidence.bound_bundle_digest(self.bound, self.context)
            self.assertEqual(bind_spy.call_count, 2)
            with patch.object(run_evidence, '_pack', wraps=run_evidence._pack):
                run_evidence.bound_bundle_digest(self.bound, self.context)
            self.assertEqual(bind_spy.call_count, 3)
            with patch.object(run_evidence, '_walk_json', wraps=run_evidence._walk_json):
                run_evidence.bound_bundle_digest(self.bound, self.context)
            self.assertEqual(bind_spy.call_count, 4)
            with patch.object(run_evidence, 'MAX_DOCUMENT_BYTES', run_evidence.MAX_DOCUMENT_BYTES + 1):
                run_evidence.bound_bundle_digest(self.bound, self.context)
            self.assertEqual(bind_spy.call_count, 5)
            with patch.object(run_evidence, 'MAX_INTEGER', run_evidence.MAX_INTEGER + 1):
                run_evidence.bound_bundle_digest(self.bound, self.context)
            self.assertEqual(bind_spy.call_count, 6)
            with patch.object(run_evidence, '_bound_uncached', wraps=binder) as changed_impl:
                run_evidence.bound_bundle_digest(self.bound, self.context)
                self.assertEqual(changed_impl.call_count, 1)

    def test_input_and_baseline_content_are_part_of_the_complete_key(self):
        binder = run_evidence._bound_uncached
        with patch.object(run_evidence, '_bound_uncached', wraps=binder) as bind_spy:
            run_evidence.bound_bundle_digest(self.bound, self.context)
            altered = deepcopy(self.bound)
            altered['manifest']['run_id'] += '-other'
            run_evidence.bound_bundle_digest(altered, self.context)
            self.assertEqual(bind_spy.call_count, 2)
            with self.assertRaises(run_evidence.EvidenceError):
                run_evidence.bound_bundle_digest(self.bound, None)
            self.assertEqual(bind_spy.call_count, 3)

    def test_noneligible_nonplain_deep_and_oversize_use_legacy_path(self):
        cases = deepcopy(self.bound)
        cases['case_set']['cases'].pop()
        custom = deepcopy(self.bound)
        class DictSubclass(dict):
            pass
        custom['manifest'] = DictSubclass(custom['manifest'])
        deep = deepcopy(self.bound)
        deep['unexpected'] = {'nested': []}
        cursor = deep['unexpected']
        for _ in range(70):
            cursor['nested'] = {'nested': []}
            cursor = cursor['nested']
        oversized = deepcopy(self.bound)
        oversized['padding'] = 'x' * (2 * run_evidence.MAX_DOCUMENT_BYTES)
        with patch.object(run_evidence, '_cached_bound_digest', wraps=run_evidence._cached_bound_digest) as cached, \
             patch.object(run_evidence, '_bound_uncached', wraps=run_evidence._bound_uncached) as binder:
            for invalid in (cases, custom, deep, oversized):
                with self.assertRaises(run_evidence.EvidenceError):
                    run_evidence.bound_bundle_digest(invalid, self.context)
            self.assertEqual(cached.call_count, 0)
            self.assertEqual(binder.call_count, 4)

    def test_warm_digest_cannot_bypass_smaller_global_document_limit(self):
        canonical, expected = run_evidence._pack(
            run_evidence._bound_uncached(self.bound, self.context))
        self.assertEqual(run_evidence.bound_bundle_digest(self.bound, self.context), expected)
        global_limit = len(canonical.encode('utf-8')) - 1
        # 各componentは収まるが、完全bundleは1 byte超過する実データを使う。
        import json
        largest_component = max(len(json.dumps(value, sort_keys=True,
            ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
            for value in self.bound.values())
        self.assertLess(largest_component, global_limit)
        with patch.object(run_evidence, 'MAX_DOCUMENT_BYTES', global_limit):
            for _ in range(2):
                with self.assertRaisesRegex(run_evidence.EvidenceError, '^DOCUMENT_SIZE$'):
                    run_evidence.bound_bundle_digest(self.bound, self.context)
        self.assertEqual(run_evidence.bound_bundle_digest(self.bound, self.context), expected)

    def test_cache_hit_refreshes_source_identity_each_time(self):
        with patch('gah.evaluation_authority._source_digest', return_value='c' * 64) as source:
            first = run_evidence.bound_bundle_digest(self.bound, self.context)
            second = run_evidence.bound_bundle_digest(self.bound, self.context)
        self.assertEqual(first, second)
        self.assertEqual(source.call_count, 2)

    def test_full_pack_failure_is_not_cached(self):
        failure = run_evidence.EvidenceError('DOCUMENT_SIZE')
        with patch.object(run_evidence, '_pack', side_effect=failure) as packer:
            for _ in range(2):
                with self.assertRaisesRegex(run_evidence.EvidenceError, '^DOCUMENT_SIZE$'):
                    run_evidence.bound_bundle_digest(self.bound, self.context)
        self.assertEqual(packer.call_count, 2)

    def test_failed_miss_is_not_cached_and_shared_quota_remains_bounded(self):
        original = run_evidence._bound_uncached
        with patch.object(run_evidence, '_bound_uncached', side_effect=RuntimeError('probe')) as impl:
            with self.assertRaisesRegex(RuntimeError, '^probe$'):
                run_evidence.bound_bundle_digest(self.bound, self.context)
            self.assertEqual(impl.call_count, 1)
        actual = run_evidence.bound_bundle_digest(self.bound, self.context)
        self.assertEqual(actual, self.oracle())
        run_evidence._bound(self.bound, self.context)
        info = binding_cache.info()
        self.assertLessEqual(info.retained_bytes, info.max_bytes)
        self.assertLessEqual(info.currsize, info.maxsize)
        self.assertGreaterEqual(info.currsize, 2)


if __name__ == '__main__':
    unittest.main()
