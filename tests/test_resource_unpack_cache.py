"""Nonflat unpack verifies with one parse and caches markers, never trees."""
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from gah import resources  # noqa: E402


def _old_unpack(raw, digest):
    """Pre-marker oracle: parse, canonicalize, and verify on every call."""
    if type(raw) is not str or type(digest) is not str:
        raise resources.ResourceError("STORAGE_CORRUPT")
    try:
        value = json.loads(raw)
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False).encode("utf-8")
        expected = encoded.decode("utf-8")
        actual = hashlib.sha256(encoded).hexdigest()
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise resources.ResourceError("STORAGE_CORRUPT") from None
    if expected != raw or actual != digest:
        raise resources.ResourceError("STORAGE_CORRUPT")
    return value


def _canonical(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), allow_nan=False).encode("utf-8")
    return raw.decode("utf-8"), hashlib.sha256(raw).hexdigest()


class ResourceUnpackCacheTests(unittest.TestCase):
    def setUp(self):
        resources._NONFLAT_VERIFIED.clear()
        resources._flat_unpack.cache_clear()

    def assert_matches_old(self, raw, digest):
        try:
            expected = _old_unpack(raw, digest)
        except resources.ResourceError as old_error:
            with self.assertRaises(resources.ResourceError) as caught:
                resources._unpack(raw, digest)
            self.assertEqual(caught.exception.code, old_error.code)
        else:
            self.assertEqual(resources._unpack(raw, digest), expected)

    def test_old_oracle_equivalence_valid_flat_nested_unicode_and_float(self):
        vectors = [
            {"a": 3, "b": True},
            {"billing_ref": {"digest": "a" * 64, "id": "basis", "kind": "billing_basis"},
             "case_trial_executions": 1, "billing_mode": "metered"},
            {"nested": [1, 1.25, {"label": "雪"}]},
            ["top-level-list", {"value": 2.5}],
        ]
        for value in vectors:
            with self.subTest(value_type=type(value).__name__):
                raw, digest = _canonical(value)
                self.assert_matches_old(raw, digest)
                self.assert_matches_old(raw, "0" * 64)

    def test_old_oracle_equivalence_rejections(self):
        cases = [
            ('{"x":1,"x":2}', hashlib.sha256(b'{"x":1,"x":2}').hexdigest()),
            ('{ "nested":{"x":1}}', hashlib.sha256(b'{ "nested":{"x":1}}').hexdigest()),
            ('{"nested":', "0" * 64),
            ('{"nested":{"x":1}}', "0" * 64),
            (b'{"x":1}', "0" * 64),
            ('{"nested":{"x":"\ud800"}}', "0" * 64),
        ]
        for raw, digest in cases:
            with self.subTest(raw_type=type(raw).__name__, raw_length=len(raw)):
                self.assert_matches_old(raw, digest)

    def test_nonflat_miss_and_hit_decode_once_each_but_verify_only_on_miss(self):
        raw, digest = _canonical({"nested": {"k": [1, 2]}})
        decoder, packer, encoder = json.loads, resources._packed, resources.canonical_bytes
        with patch.object(resources.json, "loads", wraps=decoder) as loads, \
                patch.object(resources, "_packed", wraps=packer) as packed, \
                patch.object(resources, "canonical_bytes", wraps=encoder) as encoded:
            first = resources._unpack(raw, digest)
            self.assertEqual((loads.call_count, packed.call_count, encoded.call_count), (1, 1, 2))
            first["nested"]["k"].append(3)
            second = resources._unpack(raw, digest)
            self.assertEqual((loads.call_count, packed.call_count, encoded.call_count), (2, 1, 2))
        self.assertEqual(second, {"nested": {"k": [1, 2]}})
        self.assertIsNot(first, second)
        self.assertIsNot(first["nested"], second["nested"])
        self.assertIsNot(first["nested"]["k"], second["nested"]["k"])
        self.assertEqual(len(resources._NONFLAT_VERIFIED), 1)
        self.assertIs(next(iter(resources._NONFLAT_VERIFIED.values())), resources._VERIFIED_MARKER)

    def test_full_raw_and_digest_are_cache_key_and_failures_are_not_cached(self):
        raw_a, digest_a = _canonical({"nested": {"value": "a"}})
        raw_b, _ = _canonical({"nested": {"value": "b"}})
        resources._unpack(raw_a, digest_a)
        before = len(resources._NONFLAT_VERIFIED)
        with self.assertRaisesRegex(resources.ResourceError, "^STORAGE_CORRUPT$"):
            resources._unpack(raw_b, digest_a)
        self.assertEqual(len(resources._NONFLAT_VERIFIED), before)
        with self.assertRaisesRegex(resources.ResourceError, "^STORAGE_CORRUPT$"):
            resources._unpack(raw_a, "f" * 64)
        self.assertEqual(len(resources._NONFLAT_VERIFIED), before)

    def test_duplicate_noncanonical_invalid_oversize_and_float_keep_old_meaning(self):
        duplicate = '{"nested":{"x":1,"x":2}}'
        whitespace = '{ "nested": {"x":1}}'
        invalid = '{"nested":'
        for raw in (duplicate, whitespace, invalid):
            with self.subTest(raw=raw):
                digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
                self.assert_matches_old(raw, digest)
        huge, huge_digest = _canonical({"nested": {"payload": "x" * 5000}})
        self.assertGreater(len(huge.encode("utf-8")), 4096)
        self.assertEqual(resources._unpack(huge, huge_digest), _old_unpack(huge, huge_digest))
        self.assertEqual(len(resources._NONFLAT_VERIFIED), 0)
        float_raw, float_digest = _canonical({"nested": [1.5]})
        self.assertEqual(resources._unpack(float_raw, float_digest), {"nested": [1.5]})

    def test_processing_function_identities_invalidate_marker(self):
        raw, digest = _canonical({"nested": {"k": [1]}})
        resources._unpack(raw, digest)
        patches = (
            patch.object(resources.json, "loads", wraps=json.loads),
            patch.object(resources, "_packed", wraps=resources._packed),
            patch.object(resources, "canonical_bytes", wraps=resources.canonical_bytes),
        )
        for identity_patch in patches:
            before = len(resources._NONFLAT_VERIFIED)
            with identity_patch:
                self.assertEqual(resources._unpack(raw, digest), {"nested": {"k": [1]}})
            self.assertEqual(len(resources._NONFLAT_VERIFIED), before + 1)

    def test_marker_lru_is_bounded_and_refreshes_on_hit(self):
        values = [_canonical({"nested": {"item": index}}) for index in range(64)]
        first, second = values[:2]
        extra = _canonical({"nested": {"item": 64}})
        packed = resources._packed
        with patch.object(resources, "_packed", wraps=packed) as packed_call:
            for value in values:
                resources._unpack(*value)
            self.assertEqual(packed_call.call_count, 64)
            self.assertEqual(len(resources._NONFLAT_VERIFIED), 64)
            resources._unpack(*first)  # refresh first so second becomes least recent
            self.assertEqual(packed_call.call_count, 64)
            resources._unpack(*extra)  # evicts second, without changing verifier identity
            self.assertEqual(packed_call.call_count, 65)
            self.assertEqual(len(resources._NONFLAT_VERIFIED), 64)
            resources._unpack(*first)
            self.assertEqual(packed_call.call_count, 65)  # first remains a verified hit
            resources._unpack(*second)
            self.assertEqual(packed_call.call_count, 66)  # second was truly evicted
        self.assertEqual(len(resources._NONFLAT_VERIFIED), 64)

    def test_concurrent_hit_returns_independent_trees(self):
        raw, digest = _canonical({"nested": [{"value": [1]}]})
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: resources._unpack(raw, digest), range(32)))
        results[0]["nested"][0]["value"].append(2)
        for result in results[1:]:
            self.assertEqual(result, {"nested": [{"value": [1]}]})
            self.assertIsNot(result, results[0])
        self.assertLessEqual(len(resources._NONFLAT_VERIFIED), 64)


if __name__ == "__main__":
    unittest.main()
