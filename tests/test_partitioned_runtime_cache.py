from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from gah import partitioned_run_contracts as module
from gah.contracts import ContractError
from gah.immutable_cache import binding_cache
from tests.test_partitioned_run_evidence import _setup


def _materialize(context):
    return module.materialize_partitioned_run(
        context["manifest"], context["contract"], context["index"], context["segments"],
        context["policy"], context["registry"], context["case_set"],
        baseline_context=context["baseline_context"],
    )


class PartitionedRuntimeCacheTests(unittest.TestCase):
    def setUp(self):
        module._cached_materialize_partitioned_run.cache_clear()
        self.fixtures, self.plan, self.manifest, self.index, self.segments, self.receipt, self.profile, self.context = _setup()
        binding_cache.clear()

    def tearDown(self):
        module._cached_materialize_partitioned_run.cache_clear()

    def test_full_input_cache_reuses_pure_binding_and_returns_detached_tree(self):
        implementation = module._bind_partitioned_run
        with patch.object(module, "_bind_partitioned_run", wraps=implementation) as bind:
            first = _materialize(self.context)
            second = _materialize(self.context)
            self.assertEqual(first, second)
            self.assertEqual(bind.call_count, 1)
            first["plan"]["entries"][0]["case_id"] = "caller-mutated"
            first["_partitioned_context"]["segments"][0]["entries"][0]["case_id"] = "caller-mutated"
            third = _materialize(self.context)
            self.assertEqual(bind.call_count, 1)
            self.assertNotEqual(third["plan"]["entries"][0]["case_id"], "caller-mutated")
            self.assertNotEqual(third["_partitioned_context"]["segments"][0]["entries"][0]["case_id"],
                                "caller-mutated")
        self.assertLessEqual(binding_cache.info().retained_bytes, binding_cache.info().max_bytes)

    def test_changed_input_source_or_validator_identity_misses_and_invalid_input_still_rejects(self):
        implementation = module._bind_partitioned_run
        with patch.object(module, "_bind_partitioned_run", wraps=implementation) as bind:
            _materialize(self.context)
            changed = copy.deepcopy(self.context)
            changed["manifest"]["deadline"] = changed["manifest"]["created_at"]
            with self.assertRaises(ContractError):
                _materialize(changed)
            self.assertEqual(bind.call_count, 2)
            with patch("gah.evaluation_authority._source_digest", return_value="f" * 64):
                _materialize(self.context)
            self.assertEqual(bind.call_count, 3)
            validator = module.validate_case_set
            with patch.object(module, "validate_case_set", wraps=validator) as changed_validator:
                _materialize(self.context)
                changed_validator.assert_called_once()
            self.assertEqual(bind.call_count, 4)

    def test_unbounded_or_nonplain_inputs_fall_back_to_full_validator(self):
        implementation = module._bind_partitioned_run
        with patch.object(module, "_MATERIALIZE_CACHE_INPUT_BYTES", 1), \
             patch.object(module, "_bind_partitioned_run", wraps=implementation) as bind:
            self.assertEqual(_materialize(self.context), _materialize(self.context))
            self.assertEqual(bind.call_count, 2)
        subclass = type("ManifestSubclass", (dict,), {})(self.context["manifest"])
        nonplain = dict(self.context)
        nonplain["manifest"] = subclass
        with self.assertRaises(ContractError):
            _materialize(nonplain)


if __name__ == "__main__":
    unittest.main()
