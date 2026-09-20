"""Pure partitioned transition cache stays bounded and returns isolated values."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from gah import immutable_cache, partitioned_llm_transitions as transitions
from gah.wire import canonical_bytes
from gah.contracts import ContractError
from tests import test_partitioned_prepared_io as io_fixtures
from tests import test_partitioned_contract_updates as contract_fixtures


def prepared(count):
    case = io_fixtures.PartitionedPreparedIOTests(
        "test_prepared_round_trip_and_wrong_artifact_rejected")
    return case.prepared(count)


def build_args(count):
    source = prepared(count)
    record, previous, following = contract_fixtures.PartitionedContractUpdateTests._baseline_and_next(
        source["bound_run"])
    return previous, following, dict(baseline_record=record, source_prepared=source, now=1100,
        old_run_id=f"cache-old-{count}", new_run_id=f"cache-new-{count}")


class PartitionedTransitionCacheTests(unittest.TestCase):
    def setUp(self):
        transitions._cached_build.cache_clear()
        transitions._cached_rebind.cache_clear()

    def test_build_400_hits_and_1600_falls_back_with_independent_results(self):
        for count in (400, 1600):
            with self.subTest(count=count):
                previous, following, kwargs = build_args(count)
                original = transitions._build
                calls = []
                def counted(*args, **call_kwargs):
                    calls.append(1)
                    return original(*args, **call_kwargs)
                with patch.object(transitions, "_build", side_effect=counted):
                    first = transitions.build(previous, following, **kwargs)
                    second = transitions.build(previous, following, **kwargs)
                    self.assertEqual(canonical_bytes(first), canonical_bytes(second))
                    self.assertIsNot(first, second)
                    second["new"]["manifest"]["run_id"] = "caller-mutated"
                    third = transitions.build(previous, following, **kwargs)
                expected_calls = 1 if count == 400 else 3
                self.assertEqual(len(calls), expected_calls)
                self.assertEqual(canonical_bytes(first), canonical_bytes(third))
                self.assertEqual(third["new"]["manifest"]["run_id"], kwargs["new_run_id"])
                info = immutable_cache.binding_cache.info()
                self.assertLessEqual(info.retained_bytes, info.max_bytes)

    def test_rebind_cache_hit_isolated_and_input_change_misses(self):
        previous, following, kwargs = build_args(400)
        candidate = transitions.build(previous, following, **kwargs)["new"]
        original = transitions._rebind
        calls = []
        def counted(*args, **call_kwargs):
            calls.append(1)
            return original(*args, **call_kwargs)
        with patch.object(transitions, "_rebind", side_effect=counted):
            first = transitions.rebind(candidate, run_id="cache-normal", now=1200)
            second = transitions.rebind(candidate, run_id="cache-normal", now=1200)
            self.assertEqual(canonical_bytes(first), canonical_bytes(second))
            self.assertIsNot(first, second)
            second["manifest"]["run_id"] = "caller-mutated"
            third = transitions.rebind(candidate, run_id="cache-normal", now=1200)
        self.assertEqual(len(calls), 1)
        self.assertEqual(third["manifest"]["run_id"], "cache-normal")
        changed = transitions.rebind(candidate, run_id="cache-normal-2", now=1200)
        self.assertNotEqual(first["manifest"]["run_id"], changed["manifest"]["run_id"])

    def test_source_and_callable_identity_are_cache_key_material(self):
        previous, following, kwargs = build_args(400)
        original = transitions._build
        calls = []
        def counted(*args, **call_kwargs):
            calls.append(1)
            return original(*args, **call_kwargs)
        with patch.object(transitions, "_build", side_effect=counted):
            with patch.object(transitions, "_source_digest", side_effect=["a" * 64, "b" * 64]):
                first = transitions.build(previous, following, **kwargs)
                second = transitions.build(previous, following, **kwargs)
        self.assertEqual(len(calls), 2)
        self.assertEqual(canonical_bytes(first), canonical_bytes(second))

        transitions._cached_build.cache_clear()
        calls.clear()
        with patch.object(transitions, "_source_digest", return_value="c" * 64):
            first = transitions.build(previous, following, **kwargs)
            original = transitions._build
            alternate_calls = []
            def alternate(*args, **call_kwargs):
                alternate_calls.append(1)
                return original(*args, **call_kwargs)
            with patch.object(transitions, "_build", side_effect=alternate):
                second = transitions.build(previous, following, **kwargs)
        self.assertEqual(len(alternate_calls), 1)
        self.assertEqual(canonical_bytes(first), canonical_bytes(second))

    def test_invalid_build_is_recomputed_and_never_cached(self):
        previous, following, kwargs = build_args(400)
        kwargs["source_prepared"]["ci_eligible"] = True
        original = transitions._build
        calls = []
        def counted(*args, **call_kwargs):
            calls.append(1)
            return original(*args, **call_kwargs)
        with patch.object(transitions, "_build", side_effect=counted):
            for _ in range(2):
                with self.assertRaises(ContractError):
                    transitions.build(previous, following, **kwargs)
        self.assertEqual(len(calls), 2)

    def test_uncached_output_matches_cached_output_and_budget_is_shared(self):
        previous, following, kwargs = build_args(400)
        cached = transitions.build(previous, following, **kwargs)
        with patch.object(transitions, "plain", return_value=False):
            uncached = transitions.build(previous, following, **kwargs)
        self.assertEqual(canonical_bytes(cached), canonical_bytes(uncached))
        info = immutable_cache.binding_cache.info()
        self.assertLessEqual(info.retained_bytes, 16 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
