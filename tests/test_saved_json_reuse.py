"""保存本文の解析再利用でも型・改変・容量・独立出力を検査する。"""
import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from gah import resources, run_evidence


class SavedJsonReuseTests(unittest.TestCase):
    def setUp(self):
        resources._flat_unpack.cache_clear()
        with run_evidence._LOAD_MARKER_LOCK:
            run_evidence._LOAD_MARKERS.clear()

    def test_flat_resource_result_is_independent_and_parsed_once(self):
        raw, digest = resources._packed({'calls': 1, 'billing_ref': None, 'mode': 'synthetic'})
        decoder = json.loads
        with patch.object(resources.json, 'loads', wraps=decoder) as loads:
            first = resources._unpack(raw, digest)
            first['calls'] = 999
            second = resources._unpack(raw, digest)
            self.assertEqual(second['calls'], 1)
            self.assertEqual(loads.call_count, 1)

    def test_resource_changed_body_or_digest_never_reuses_old_result(self):
        raw, digest = resources._packed({'calls': 1})
        resources._unpack(raw, digest)
        changed, other = resources._packed({'calls': 2})
        for body, ref in ((changed, digest), (raw, other)):
            with self.assertRaisesRegex(resources.ResourceError, 'STORAGE_CORRUPT'):
                resources._unpack(body, ref)
        self.assertEqual(resources._unpack(changed, other), {'calls': 2})

    def test_resource_nested_and_large_values_use_normal_independent_decoding(self):
        for value in ({'items': [{'count': 1}]}, {'text': 'a' * 5000}):
            raw, digest = resources._packed(value)
            one = resources._unpack(raw, digest)
            two = resources._unpack(raw, digest)
            self.assertEqual(one, two)
            self.assertIsNot(one, two)
            if 'items' in one:
                one['items'][0]['count'] = 9
                self.assertEqual(two['items'][0]['count'], 1)
        self.assertEqual(resources._flat_unpack.cache_info().currsize, 0)

    def test_resource_cache_is_bounded_and_parser_changes_are_not_hidden(self):
        for number in range(80):
            resources._unpack(*resources._packed({'count': number}))
        self.assertLessEqual(resources._flat_unpack.cache_info().currsize, 64)
        raw, digest = resources._packed({'count': 79})
        with patch.object(resources.json, 'loads', side_effect=ValueError('PARSER_UNAVAILABLE')):
            with self.assertRaisesRegex(resources.ResourceError, 'STORAGE_CORRUPT'):
                resources._unpack(raw, digest)

    def test_evidence_reuses_only_structure_check_and_returns_independent_values(self):
        raw, digest = run_evidence._pack({'nested': [{'n': 1}]})
        walker = run_evidence._walk_json
        with patch.object(run_evidence, '_walk_json', wraps=walker) as walk:
            first = run_evidence._load(raw, digest)
            first['nested'][0]['n'] = 99
            second = run_evidence._load(raw, digest)
            self.assertEqual(second['nested'][0]['n'], 1)
            self.assertEqual(walk.call_count, 1)

    def test_evidence_hash_is_checked_on_every_read(self):
        raw, digest = run_evidence._pack({'n': 1})
        run_evidence._load(raw, digest)
        changed, other = run_evidence._pack({'n': 2})
        for body, ref in ((changed, digest), (raw, other)):
            with self.assertRaisesRegex(run_evidence.EvidenceError, 'STORAGE_CORRUPT'):
                run_evidence._load(body, ref)
        self.assertEqual(run_evidence._load(changed, other), {'n': 2})

    def test_evidence_duplicate_keys_float_and_noncanonical_text_stay_invalid(self):
        for raw in ('{"n":1,"n":2}', '{"n":1.0}', '{"n": 1}', '{"n":NaN}'):
            digest = hashlib.sha256(raw.encode()).hexdigest()
            for _ in range(2):
                with self.assertRaisesRegex(run_evidence.EvidenceError, 'STORAGE_CORRUPT'):
                    run_evidence._load(raw, digest)

    def test_evidence_new_limits_or_implementation_invalidate_structure_marker(self):
        raw, digest = run_evidence._pack({'n': 2})
        run_evidence._load(raw, digest)
        with patch.object(run_evidence, 'MAX_INTEGER', 1):
            with self.assertRaisesRegex(run_evidence.EvidenceError, 'STORAGE_CORRUPT'):
                run_evidence._load(raw, digest)
        with patch.object(run_evidence, '_walk_json', side_effect=run_evidence.EvidenceError('INVALID')):
            with self.assertRaisesRegex(run_evidence.EvidenceError, 'STORAGE_CORRUPT'):
                run_evidence._load(raw, digest)

    def test_evidence_marker_capacity_does_not_retain_raw_documents(self):
        with patch.object(run_evidence, '_MAX_LOAD_MARKERS', 2):
            for number in range(3):
                raw, digest = run_evidence._pack({'unique_text': 'marker-' + str(number)})
                run_evidence._load(raw, digest)
            self.assertEqual(len(run_evidence._LOAD_MARKERS), 2)
            self.assertNotIn('unique_text', repr(run_evidence._LOAD_MARKERS))


if __name__ == '__main__':
    unittest.main()
