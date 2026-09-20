"""guardrail corpus lookup index: source freshness, identity, and copy isolation."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gah import guardrail_results, guardrail_runtime, llm_materialization
from gah.contracts import ContractError
from gah.docker_runner import PROFILE
from gah.evaluation_data import build_pack
from gah.policy import initial_policy_profile
from gah.wire import canonical_bytes


class CountingList(list):
    def __init__(self, values):
        super().__init__(values)
        self.iterations = 0

    def __iter__(self):
        self.iterations += 1
        return super().__iter__()


class GuardrailLookupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        lock = guardrail_runtime.read_lock()
        cls.prepared = llm_materialization.build(
            initial_policy_profile(), policy_generation=1, run_id="lookup-run",
            now=1000, target_version="baseline-v1", image_id=lock["image_id"],
            worker_digest=lock["worker_digest"], isolation_profile=PROFILE,
        )
        cls.entry = next(
            entry for entry in cls.prepared["bound_run"]["plan"]["entries"]
            if len(entry["stage_ids"]) == 2
        )

    def setUp(self):
        guardrail_results._lookup_index.cache_clear()
        guardrail_results._pack.cache_clear()
        self.request = guardrail_results.for_entry(
            self.prepared, self.entry, "lookup-operation", 1,
        )

    def tearDown(self):
        guardrail_results._lookup_index.cache_clear()
        guardrail_results._pack.cache_clear()

    def test_repeated_queries_build_full_corpus_index_once(self):
        guardrail_results._lookup_index.cache_clear()
        guardrail_results._pack.cache_clear()
        pack = build_pack()
        pack["documents"] = CountingList(pack["documents"])
        case_lists = []
        for case_set in pack["case_sets"].values():
            case_set["cases"] = CountingList(case_set["cases"])
            case_lists.append(case_set["cases"])
        with patch.object(guardrail_results, "_pack", return_value=pack):
            with patch.object(
                guardrail_results, "_build_lookup_index",
                wraps=guardrail_results._build_lookup_index,
            ) as builder:
                with patch.object(llm_materialization, "source_key", return_value="e" * 64):
                    for index in range(6):
                        request = guardrail_results.for_entry(
                            self.prepared, self.entry, "lookup-repeat-" + str(index), 1,
                        )
                        guardrail_results.validate_request(request)
                self.assertEqual(builder.call_count, 1)
        self.assertEqual(pack["documents"].iterations, 1)
        self.assertTrue(all(items.iterations == 1 for items in case_lists))
        self.assertEqual(guardrail_results._lookup_index.cache_info().maxsize, 1)
        self.assertEqual(guardrail_results._pack.cache_info().maxsize, 1)

    def test_prepared_case_stage_mutation_is_rejected_as_before(self):
        prepared = deepcopy(self.prepared)
        case = prepared["bound_run"]["case_set"]["cases"][0]
        entry = next(
            item for item in prepared["bound_run"]["plan"]["entries"]
            if item["case_id"] == case["case_id"]
        )
        case["session_steps"][0]["stage_id"] = "tampered-stage"
        with self.assertRaisesRegex(ContractError, "CASE_STAGE_MISMATCH"):
            guardrail_results.for_entry(prepared, entry, "tampered-case", 1)

    def test_cached_document_map_preserves_duplicate_ref_last_wins(self):
        pack = build_pack()
        first = pack["documents"][0]
        duplicate_document = deepcopy(first["document"])
        duplicate_document["duplicate_ref_marker"] = "last"
        pack["documents"] = list(pack["documents"]) + [{
            "ref": deepcopy(first["ref"]), "document": duplicate_document,
        }]
        with patch.object(guardrail_results, "_pack", return_value=pack):
            index = guardrail_results._build_lookup_index("d" * 64)
        key = canonical_bytes(first["ref"])
        self.assertEqual(index["documents"][key], duplicate_document)

    def test_source_digest_change_invalidates_single_entry_index(self):
        guardrail_results._lookup_index.cache_clear()
        guardrail_results._pack.cache_clear()
        keys = ["a" * 64, "a" * 64, "b" * 64, "a" * 64]
        built = []
        real_builder = guardrail_results._build_lookup_index

        def counted(key):
            built.append(key)
            return real_builder(key)

        with patch.object(llm_materialization, "source_key", side_effect=keys):
            with patch.object(guardrail_results, "_build_lookup_index", side_effect=counted):
                first = guardrail_results._lookup_index(llm_materialization.source_key())
                repeated = guardrail_results._lookup_index(llm_materialization.source_key())
                changed = guardrail_results._lookup_index(llm_materialization.source_key())
                returned = guardrail_results._lookup_index(llm_materialization.source_key())
        self.assertIs(first, repeated)
        self.assertIsNot(first, changed)
        self.assertIsNot(changed, returned)
        self.assertEqual(built, ["a" * 64, "b" * 64, "a" * 64])

    def test_duplicate_case_id_is_still_rejected(self):
        index = guardrail_results._lookup_index(llm_materialization.source_key())
        case_id = self.request["stages"][0]["binding"]["case_id"]
        cases_by_id = dict(index["cases_by_id"])
        cases_by_id[case_id] = (cases_by_id[case_id][0], cases_by_id[case_id][0])
        forged_index = dict(index, cases_by_id=cases_by_id)
        with patch.object(guardrail_results, "_lookup_index", return_value=forged_index):
            with self.assertRaisesRegex(ContractError, "CASE_STAGE_MISMATCH"):
                guardrail_results.validate_request(self.request)

    def test_wrong_stage_and_input_are_rejected(self):
        bad_stage = deepcopy(self.request)
        bad_stage["stages"][1]["binding"]["stage_id"] = "other-stage"
        with self.assertRaises(ContractError):
            guardrail_results.validate_request(bad_stage)

        bad_input = deepcopy(self.request)
        key = next(iter(bad_input["stages"][0]["input"]["observed"]))
        value = bad_input["stages"][0]["input"]["observed"][key]
        bad_input["stages"][0]["input"]["observed"][key] = not value if type(value) is bool else "tampered"
        with self.assertRaises(ContractError):
            guardrail_results.validate_request(bad_input)

    def test_mutating_returned_requests_does_not_change_later_results(self):
        expected = guardrail_results.for_entry(
            self.prepared, self.entry, "isolation-operation", 1,
        )
        first = guardrail_results.for_entry(
            self.prepared, self.entry, "isolation-operation", 1,
        )
        first["stages"][0]["input"]["required"].reverse()
        first["target"]["behavior_version"] = "tampered"
        second = guardrail_results.for_entry(
            self.prepared, self.entry, "isolation-operation", 1,
        )
        self.assertEqual(second, expected)

        validated = guardrail_results.validate_request(expected)
        validated["stages"][0]["input"]["required"].clear()
        validated["target"]["behavior_version"] = "tampered"
        self.assertEqual(guardrail_results.validate_request(expected), expected)


if __name__ == "__main__":
    unittest.main()
