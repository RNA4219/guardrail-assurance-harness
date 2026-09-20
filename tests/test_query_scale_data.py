import copy
import hashlib
import sys
from collections import Counter
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gah.contracts import ContractError, MAX_DOCUMENT_BYTES
from gah.corpus import validate_case_set
from gah.query_scale_data import build_scale_corpus, validate_scale_corpus
from gah.wire import canonical_bytes


def _independent_detection(document):
    values = [document["observed"][feature] for feature in document["required"]]
    if any(value is False for value in values):
        return "detect"
    if any(value is None for value in values):
        return "indeterminate"
    return "allow"


class QueryScaleDataTests(unittest.TestCase):
    def test_supported_sizes_are_deterministic_bounded_and_single_stage(self):
        sizes = {}
        ids_by_count = {}
        for count in (400, 800, 1600):
            first = build_scale_corpus(count)
            second = build_scale_corpus(count)
            self.assertEqual(first, second)
            case_set = first["case_set"]
            sizes[count] = len(canonical_bytes(case_set))
            ids_by_count[count] = {case["case_id"] for case in case_set["cases"]}
            self.assertLessEqual(sizes[count], MAX_DOCUMENT_BYTES)
            self.assertEqual(len(case_set["cases"]), count)
            checked = validate_case_set(case_set)
            self.assertEqual(checked, case_set)
            self.assertEqual(first["stage_counts"], {"1": count, "2": 0})
            self.assertTrue(all(len(case["session_steps"]) == 1 for case in checked["cases"]))
            self.assertEqual(validate_scale_corpus(first), first)
        self.assertTrue(ids_by_count[400].isdisjoint(ids_by_count[800]))
        self.assertTrue(ids_by_count[800].isdisjoint(ids_by_count[1600]))
        self.assertLess(sizes[400], sizes[800])
        self.assertLess(sizes[800], sizes[1600])
        self.assertLessEqual(sizes[1600], 1_048_576)

    def test_namespace_distribution_unique_inputs_and_independent_ref_recompute(self):
        value = build_scale_corpus(800)
        case_set = validate_case_set(value["case_set"])
        docs = {}
        for item in value["documents"]:
            ref = item["ref"]
            body = item["document"]
            digest = hashlib.sha256(canonical_bytes(body)).hexdigest()
            self.assertEqual(ref["digest"], digest)
            self.assertLessEqual(len(canonical_bytes(body)), MAX_DOCUMENT_BYTES)
            key = (ref["kind"], ref["id"])
            self.assertNotIn(key, docs)
            docs[key] = (ref, body)

        cases = case_set["cases"]
        self.assertTrue(all(case["case_id"].startswith("q") for case in cases))
        self.assertTrue(all(case["lineage_group"].startswith("l") for case in cases))
        self.assertEqual(len({case["case_id"] for case in cases}), len(cases))
        self.assertEqual(len({case["lineage_group"] for case in cases}), len(cases))
        counts = Counter((case["category"], case["expected_label"]) for case in cases)
        for category in case_set["required_categories"]:
            self.assertEqual(counts[(category, "positive")], 200)
            self.assertEqual(counts[(category, "negative")], 200)

        input_digests = set()
        referenced = set()
        for case in cases:
            oracle_ref = case["oracle_ref"]
            state_ref = case["initial_state_ref"]
            stage = case["session_steps"][0]
            input_ref = stage["input_ref"]
            for ref in (oracle_ref, state_ref, input_ref):
                key = (ref["kind"], ref["id"])
                self.assertIn(key, docs)
                self.assertEqual(docs[key][0], ref)
                referenced.add(key)
            category = case["category"]
            oracle = docs[(oracle_ref["kind"], oracle_ref["id"])][1]
            state = docs[(state_ref["kind"], state_ref["id"])][1]
            input_doc = docs[(input_ref["kind"], input_ref["id"])][1]
            self.assertEqual((oracle["category"], state["category"], input_doc["category"]), (category,) * 3)
            expected = _independent_detection(input_doc)
            self.assertEqual(expected, stage["expected_detection"])
            self.assertEqual(expected, oracle["expected_detection"])
            self.assertEqual(oracle["required"], input_doc["required"])
            self.assertEqual(expected, {"positive": "detect", "negative": "allow"}[case["expected_label"]])
            input_digests.add(input_ref["digest"])
        self.assertEqual(len(input_digests), len(cases))
        self.assertEqual(referenced, set(docs))

    def test_strict_count_and_detached_mutable_results(self):
        for invalid_count in (True, False, 399, 401, 1601, "800", None):
            with self.subTest(invalid_count=invalid_count):
                with self.assertRaises(ContractError):
                    build_scale_corpus(invalid_count)

        first = build_scale_corpus(400)
        second = build_scale_corpus(400)
        first["case_set"]["cases"][0]["session_steps"][0]["input_ref"]["id"] = "mutated"
        self.assertNotEqual(first, second)
        self.assertEqual(second["case_set"]["cases"][0]["session_steps"][0]["input_ref"]["id"], "i400000")
        validate_scale_corpus(second)
        validated_input = build_scale_corpus(400)
        detached = validate_scale_corpus(validated_input)
        detached["documents"][0]["document"]["category"] = "mutated"
        self.assertEqual(validated_input["documents"][0]["document"]["category"], "data_handling")

    def test_missing_duplicate_and_tampered_refs_are_rejected(self):
        source = build_scale_corpus(400)
        missing = copy.deepcopy(source)
        ref = missing["case_set"]["cases"][0]["session_steps"][0]["input_ref"]
        missing["documents"] = [item for item in missing["documents"] if item["ref"]["id"] != ref["id"]]
        with self.assertRaises(ContractError):
            validate_scale_corpus(missing)

        duplicate = copy.deepcopy(source)
        duplicate["documents"].append(copy.deepcopy(duplicate["documents"][0]))
        with self.assertRaises(ContractError):
            validate_scale_corpus(duplicate)

        tampered = copy.deepcopy(source)
        tampered["documents"][0]["document"]["category"] = "work_scope"
        with self.assertRaises(ContractError):
            validate_scale_corpus(tampered)

        bad_digest = copy.deepcopy(source)
        bad_digest["case_set"]["cases"][0]["session_steps"][0]["input_ref"]["digest"] = "0" * 64
        with self.assertRaises(ContractError):
            validate_scale_corpus(bad_digest)

        duplicate_lineage = copy.deepcopy(source)
        duplicate_lineage["case_set"]["cases"][1]["lineage_group"] = duplicate_lineage["case_set"]["cases"][0]["lineage_group"]
        with self.assertRaises(ContractError):
            validate_scale_corpus(duplicate_lineage)

    def test_wrong_distribution_stage_shape_oversize_and_truthy_claim_rejected(self):
        bad_label = copy.deepcopy(build_scale_corpus(400))
        bad_label["case_set"]["cases"][0]["expected_label"] = "negative"
        # The generic schema still accepts the case shape, but its score mapping/ref semantics do not.
        with self.assertRaises(ContractError):
            validate_scale_corpus(bad_label)

        bad_stage = copy.deepcopy(build_scale_corpus(400))
        case = bad_stage["case_set"]["cases"][0]
        case["session_steps"].append(copy.deepcopy(case["session_steps"][0]))
        case["session_steps"][1]["stage_id"] = "other-stage"
        with self.assertRaises(ContractError):
            validate_scale_corpus(bad_stage)

        swapped_labels = copy.deepcopy(build_scale_corpus(400))
        swapped_cases = swapped_labels["case_set"]["cases"]
        swapped_cases[0]["expected_label"], swapped_cases[1]["expected_label"] = (
            swapped_cases[1]["expected_label"], swapped_cases[0]["expected_label"]
        )
        with self.assertRaises(ContractError):
            validate_scale_corpus(swapped_labels)

        bad_stage_counts = copy.deepcopy(build_scale_corpus(400))
        bad_stage_counts["stage_counts"]["2"] = False
        with self.assertRaises(ContractError):
            validate_scale_corpus(bad_stage_counts)

        bad_oracle_type = copy.deepcopy(build_scale_corpus(400))
        oracle_item = next(item for item in bad_oracle_type["documents"] if item["ref"]["kind"] == "synthetic_policy_oracle")
        oracle_item["document"]["required"][0] = []
        oracle_item["ref"]["digest"] = hashlib.sha256(canonical_bytes(oracle_item["document"])).hexdigest()
        for case in bad_oracle_type["case_set"]["cases"]:
            if case["oracle_ref"]["id"] == oracle_item["ref"]["id"]:
                case["oracle_ref"]["digest"] = oracle_item["ref"]["digest"]
        with self.assertRaises(ContractError):
            validate_scale_corpus(bad_oracle_type)

        bad_claim = copy.deepcopy(build_scale_corpus(400))
        bad_claim["claims"]["runtime_admission"] = 0
        with self.assertRaises(ContractError):
            validate_scale_corpus(bad_claim)

        oversized = build_scale_corpus(1600)
        for index, case in enumerate(oversized["case_set"]["cases"]):
            category_index = 0 if case["category"] == "data_handling" else 1
            suffix = f"{category_index}-{index:04d}" + ("x" * 110)
            case["case_id"] = f"qs{suffix}"
            case["lineage_group"] = f"ql{suffix}"
        self.assertGreater(len(canonical_bytes(oversized["case_set"])), MAX_DOCUMENT_BYTES)
        with self.assertRaises(ContractError):
            validate_scale_corpus(oversized)

        oversized_document = build_scale_corpus(400)
        payload = {"payload": "x" * (MAX_DOCUMENT_BYTES + 1)}
        oversized_document["documents"].append({
            "ref": {"kind": "synthetic_policy_input", "id": "oversized-doc", "digest": hashlib.sha256(canonical_bytes(payload)).hexdigest()},
            "document": payload,
        })
        with self.assertRaises(ContractError):
            validate_scale_corpus(oversized_document)


if __name__ == "__main__":
    unittest.main()
