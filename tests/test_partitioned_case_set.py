from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gah.contracts import ContractError, MAX_DOCUMENT_BYTES
from gah import partitioned_case_set as codec
from gah.corpus import validate_case_set
from gah.partitioned_case_set import (
    INDEX_KIND,
    MAX_ARTIFACT_BYTES,
    SEGMENT_KIND,
    partition_case_set,
    restore_case_set,
)
from gah.query_scale_data import build_scale_corpus
from gah.run_contracts import content_ref


def _raw(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _small_case_set(count: int = 2) -> dict:
    return copy.deepcopy(build_scale_corpus(400)["case_set"]) | {"cases": build_scale_corpus(400)["case_set"]["cases"][:count]}


class PartitionedCaseSetTests(unittest.TestCase):
    def test_v1_fitting_case_set_matches_existing_validator(self) -> None:
        source = _small_case_set(2)
        expected = validate_case_set(source)
        index, segments = partition_case_set(source)
        self.assertEqual(index["kind"], INDEX_KIND)
        self.assertEqual(restore_case_set(index, segments), expected)
        self.assertNotIn("ci_eligible", index)

    def test_query_scale_1600_roundtrips_in_two_bounded_segments(self) -> None:
        case_set = build_scale_corpus(1600)["case_set"]
        logical_raw = _raw(case_set)
        self.assertLessEqual(len(logical_raw), MAX_DOCUMENT_BYTES)
        index, segments = partition_case_set(case_set)
        self.assertEqual(index["case_count"], 1600)
        self.assertEqual(index["segment_count"], 2)
        self.assertEqual(index["reconstructed_bytes"], len(logical_raw))
        self.assertEqual(index["reconstructed_digest"], hashlib.sha256(logical_raw).hexdigest())
        segment_raw = [_raw(item) for item in segments]
        self.assertTrue(all(len(raw) <= MAX_ARTIFACT_BYTES for raw in segment_raw))
        self.assertEqual([item["first_case_ordinal"] for item in segments], [0, len(segments[0]["cases"])])
        self.assertEqual(restore_case_set(index, segments), case_set)
        self.assertEqual(_raw(restore_case_set(index, segments)), logical_raw)

    def test_input_and_returned_trees_are_mutation_isolated(self) -> None:
        source = _small_case_set(3)
        expected = copy.deepcopy(source)
        index, segments = partition_case_set(source)
        source["cases"][0]["case_id"] = "caller-mutated"
        self.assertEqual(restore_case_set(index, segments), expected)
        restored = restore_case_set(index, segments)
        restored["cases"][0]["oracle_ref"]["id"] = "returned-mutated"
        self.assertEqual(restore_case_set(index, segments), expected)
        segments[0]["cases"][0]["case_id"] = "segment-mutated"
        self.assertEqual(restore_case_set(index, partition_case_set(expected)[1]), expected)

    def test_missing_reordered_duplicate_and_rebound_segments_reject(self) -> None:
        index, segments = partition_case_set(build_scale_corpus(1600)["case_set"])
        with self.assertRaises(ContractError):
            restore_case_set(index, segments[:-1])
        with self.assertRaises(ContractError):
            restore_case_set(index, list(reversed(segments)))
        with self.assertRaises(ContractError):
            restore_case_set(index, [segments[0], segments[0]])
        changed = copy.deepcopy(segments)
        changed[1]["case_set_id"] = "different-case-set"
        with self.assertRaises(ContractError):
            restore_case_set(index, changed)

    def test_closed_schema_and_boolean_integer_reject(self) -> None:
        index, segments = partition_case_set(_small_case_set())
        extra = copy.deepcopy(index)
        extra["extra"] = 1
        with self.assertRaises(ContractError):
            restore_case_set(extra, segments)
        boolean = copy.deepcopy(segments)
        boolean[0]["segment_index"] = True
        with self.assertRaises(ContractError):
            restore_case_set(index, boolean)
        with self.assertRaises(ContractError):
            restore_case_set(index, (item for item in segments))
        oversized = copy.deepcopy(index)
        oversized["reconstructed_bytes"] = MAX_DOCUMENT_BYTES + 1
        with self.assertRaises(ContractError):
            restore_case_set(oversized, segments)

    def test_cross_segment_duplicate_rejected_after_all_digests_resigned(self) -> None:
        index, segments = partition_case_set(build_scale_corpus(1600)["case_set"])
        changed = copy.deepcopy(segments)
        changed[1]["cases"][0] = copy.deepcopy(changed[0]["cases"][0])
        logical = {
            "schema_version": 1,
            "kind": "case_set",
            "case_set_id": index["case_set_id"],
            "purpose": index["purpose"],
            "required_categories": copy.deepcopy(index["required_categories"]),
            "cases": [case for segment in changed for case in segment["cases"]],
        }
        resigned = copy.deepcopy(index)
        for position, segment in enumerate(changed):
            raw = _raw(segment)
            resigned["ordered_segments"][position]["digest"] = hashlib.sha256(raw).hexdigest()
        logical_raw = _raw(logical)
        resigned["reconstructed_bytes"] = len(logical_raw)
        resigned["reconstructed_digest"] = hashlib.sha256(logical_raw).hexdigest()
        resigned["case_set_ref"] = content_ref("case_set", resigned["case_set_id"], logical)
        with self.assertRaises(ContractError):
            restore_case_set(resigned, changed)

    def test_oversized_v1_document_and_non_json_values_reject(self) -> None:
        oversized = _small_case_set(2)
        oversized["cases"][0]["lineage_group"] = "l" * 128
        # Replicate valid cases with distinct ids/lineages until the canonical v1 cap is crossed.
        template = copy.deepcopy(oversized["cases"][0])
        oversized["cases"] = []
        for i in range(1600):
            case = copy.deepcopy(template)
            case["case_id"] = f"case-{i:04d}"
            case["lineage_group"] = f"lineage-{i:04d}-" + ("x" * 96)
            oversized["cases"].append(case)
        with self.assertRaises(ContractError):
            partition_case_set(oversized)

        invalid = _small_case_set(1)
        invalid["cases"][0]["case_id"] = float("nan")
        with self.assertRaises(ContractError):
            partition_case_set(invalid)


    def test_segment_byte_boundary_is_inclusive_and_next_byte_splits(self):
        source = _small_case_set(2)
        _, initial = partition_case_set(source)
        boundary = len(_raw(initial[0]))
        with mock.patch.object(codec, "MAX_ARTIFACT_BYTES", boundary):
            index, segments = partition_case_set(source)
            self.assertEqual(len(segments), 1)
            self.assertEqual(len(_raw(segments[0])), boundary)
            self.assertEqual(restore_case_set(index, segments), source)
        with mock.patch.object(codec, "MAX_ARTIFACT_BYTES", boundary - 1):
            index, segments = partition_case_set(source)
            self.assertEqual(len(segments), 2)
            self.assertTrue(all(len(_raw(segment)) <= boundary - 1 for segment in segments))
            self.assertEqual(restore_case_set(index, segments), source)
        with mock.patch.object(codec, "MAX_ARTIFACT_BYTES", boundary - 1), mock.patch.object(codec, "MAX_SEGMENTS", 1):
            with self.assertRaisesRegex(ContractError, "SEGMENT_LIMIT"):
                partition_case_set(source)

    def test_caller_mutation_after_reference_check_does_not_change_return(self):
        source = _small_case_set(2)
        index, segments = partition_case_set(source)
        real_ref = codec.content_ref
        def mutate_caller_after_check(*args):
            result = real_ref(*args)
            segments[0]["cases"][0]["case_id"] = "changed-after-check"
            index["required_categories"][0] = "changed-category"
            return result
        with mock.patch.object(codec, "content_ref", side_effect=mutate_caller_after_check):
            restored = restore_case_set(index, segments)
        self.assertEqual(restored, source)

    def test_all_index_and_segment_counters_reject_boolean(self):
        index, segments = partition_case_set(_small_case_set())
        for field in ("schema_version", "case_count", "segment_count", "reconstructed_bytes"):
            altered = copy.deepcopy(index); altered[field] = True
            with self.subTest(index=field), self.assertRaises(ContractError):
                restore_case_set(altered, segments)
        for field in ("schema_version", "segment_index", "first_case_ordinal", "case_count"):
            altered = copy.deepcopy(segments); altered[0][field] = True
            with self.subTest(segment=field), self.assertRaises(ContractError):
                restore_case_set(index, altered)

    def test_case_count_over_limit_rejected_even_when_v1_size_fits(self):
        source = _small_case_set(1)
        template = source["cases"][0]
        source["cases"] = [copy.deepcopy(template) for _ in range(1601)]
        for i, case in enumerate(source["cases"]):
            case["case_id"] = "c" + str(i)
            case["lineage_group"] = "l" + str(i)
        self.assertLess(len(_raw(source)), MAX_DOCUMENT_BYTES)
        validate_case_set(source)
        with self.assertRaisesRegex(ContractError, "CASE_COUNT"):
            partition_case_set(source)

    def test_restoration_checks_actual_size_despite_small_declared_length(self):
        source = _small_case_set(2)
        index, segments = partition_case_set(source)
        with mock.patch.object(codec, "MAX_DOCUMENT_BYTES", len(_raw(source)) - 1):
            index["reconstructed_bytes"] = len(_raw(source)) - 1
            with self.assertRaisesRegex(ContractError, "DOCUMENT_SIZE"):
                restore_case_set(index, segments)

    def test_each_case_length_is_measured_once(self):
        source = _small_case_set(12)
        with mock.patch.object(codec, "_case_length", wraps=codec._case_length) as measured:
            index, segments = partition_case_set(source)
        self.assertEqual(measured.call_count, 12)
        self.assertEqual(restore_case_set(index, segments), source)


if __name__ == "__main__":
    unittest.main()
