"""Bounded pure codec tests for query-scale corpora."""
from copy import deepcopy
import hashlib
import sys
from pathlib import Path
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gah.contracts import ContractError, MAX_DOCUMENT_BYTES
from gah.partitioned_case_set import partition_case_set, restore_case_set
from gah import partitioned_scale_corpus as codec
from gah.query_scale_data import build_scale_corpus
from gah.run_contracts import content_ref
from gah.wire import canonical_bytes


def _encoded(value):
    return canonical_bytes(value)


def _resign_semantically_broken(parts):
    index, case_index, case_segments, document_segments = deepcopy(parts)
    case_set = restore_case_set(case_index, case_segments)
    docs = document_segments[0]["documents"]
    positive = next(case for case in case_set["cases"] if case["expected_label"] == "positive")
    input_ref = positive["session_steps"][0]["input_ref"]
    item = next(entry for entry in docs if entry["ref"]["kind"] == input_ref["kind"] and entry["ref"]["id"] == input_ref["id"])
    feature = item["document"]["required"][0]
    item["document"]["observed"][feature] = True
    new_digest = hashlib.sha256(_encoded(item["document"])).hexdigest()
    item["ref"]["digest"] = new_digest
    input_ref["digest"] = new_digest
    case_index, case_segments = partition_case_set(case_set)
    segment = document_segments[0]
    seg_digest = hashlib.sha256(_encoded(segment)).hexdigest()
    index["ordered_document_segments"][0]["digest"] = seg_digest
    index["case_set_index_ref"] = content_ref(codec.CASE_SET_INDEX_KIND, case_index["case_set_id"], case_index)
    corpus = {
        "schema_version": 1, "kind": "query_scale_corpus",
        "corpus_id": index["corpus_id"], "case_set": case_set,
        "documents": docs, "claims": index["claims"], "stage_counts": index["stage_counts"],
    }
    raw = _encoded(corpus)
    index["reconstructed_bytes"] = len(raw)
    index["reconstructed_digest"] = hashlib.sha256(raw).hexdigest()
    return index, case_index, case_segments, document_segments


class PartitionedScaleCorpusTests(unittest.TestCase):
    def test_all_supported_corpora_roundtrip_and_artifacts_stay_bounded(self):
        for count in (400, 800, 1600):
            with self.subTest(count=count):
                source = build_scale_corpus(count)
                expected_raw = _encoded(source)
                parts = codec.partition_scale_corpus(source)
                index, case_index, case_segments, doc_segments = parts
                restored = codec.restore_scale_corpus(*parts)
                self.assertEqual(restored, source)
                self.assertEqual(_encoded(restored), expected_raw)
                self.assertEqual(len(source["case_set"]["cases"]), count)
                self.assertEqual(index["case_count"], count)
                self.assertEqual(index["stage_counts"], {"1": count, "2": 0})
                self.assertLessEqual(len(_encoded(index)), codec.MAX_ARTIFACT_BYTES)
                self.assertLessEqual(len(_encoded(case_index)), codec.MAX_ARTIFACT_BYTES)
                self.assertTrue(all(len(_encoded(x)) <= codec.MAX_ARTIFACT_BYTES for x in case_segments + doc_segments))
                self.assertLessEqual(len(expected_raw), codec.MAX_CORPUS_BYTES)
                self.assertEqual(index["reconstructed_bytes"], len(expected_raw))
                self.assertEqual(index["reconstructed_digest"], hashlib.sha256(expected_raw).hexdigest())

    def test_input_output_aliases_are_detached_from_checked_bytes(self):
        source = build_scale_corpus(400)
        original = deepcopy(source)
        real = codec._canonical
        changed = False
        def mutate_after_snapshot(value, *, maximum):
            nonlocal changed
            raw = real(value, maximum=maximum)
            if value is source and not changed:
                source["corpus_id"] = "caller-mutated-after-check"
                changed = True
            return raw
        with mock.patch.object(codec, "_canonical", side_effect=mutate_after_snapshot):
            parts = codec.partition_scale_corpus(source)
        self.assertTrue(changed)
        self.assertEqual(codec.restore_scale_corpus(*parts), original)
        saved_parts = deepcopy(parts)
        parts[0]["claims"]["runtime_admission"] = True
        parts[3][0]["documents"].clear()
        self.assertEqual(codec.restore_scale_corpus(*saved_parts), original)

    def test_document_wrappers_are_byte_measured_once(self):
        source = build_scale_corpus(1600)
        expected = len(source["documents"])
        counts = 0
        real = codec._canonical
        def count_wrappers(value, *, maximum):
            nonlocal counts
            if type(value) is dict and set(value) == {"ref", "document"}:
                counts += 1
            return real(value, maximum=maximum)
        with mock.patch.object(codec, "_canonical", side_effect=count_wrappers):
            codec.partition_scale_corpus(source)
        self.assertEqual(counts, expected)

    def test_closed_shapes_limits_types_refs_order_and_missing_parts(self):
        source = build_scale_corpus(400)
        index, case_index, case_segments, doc_segments = codec.partition_scale_corpus(source)
        bad = deepcopy(index); bad["extra"] = 1
        with self.assertRaises(ContractError): codec.restore_scale_corpus(bad, case_index, case_segments, doc_segments)
        bad = deepcopy(index); bad["case_count"] = True
        with self.assertRaises(ContractError): codec.restore_scale_corpus(bad, case_index, case_segments, doc_segments)
        bad = deepcopy(index); bad["reconstructed_bytes"] = codec.MAX_CORPUS_BYTES + 1
        with self.assertRaises(ContractError): codec.restore_scale_corpus(bad, case_index, case_segments, doc_segments)
        with self.assertRaises(ContractError): codec.restore_scale_corpus(index, case_index, case_segments, iter(case_segments))
        with self.assertRaises(ContractError): codec.restore_scale_corpus(index, case_index, case_segments, doc_segments[:-1])
        with self.assertRaises(ContractError): codec.restore_scale_corpus(index, case_index, case_segments, doc_segments + doc_segments)
        changed = deepcopy(doc_segments); changed[0]["documents"][0]["ref"]["digest"] = "f" * 64
        with self.assertRaises(ContractError): codec.restore_scale_corpus(index, case_index, case_segments, changed)
        changed = deepcopy(index); changed["case_set_index_ref"]["digest"] = "f" * 64
        with self.assertRaises(ContractError): codec.restore_scale_corpus(changed, case_index, case_segments, doc_segments)
        with self.assertRaises(ContractError): codec.partition_scale_corpus(build_scale_corpus(True))
        with mock.patch.object(codec, "MAX_CORPUS_BYTES", 1024):
            with self.assertRaises(ContractError): codec.partition_scale_corpus(source)

    def test_multisegment_reorder_duplicate_and_missing_parts_reject(self):
        parts = codec.partition_scale_corpus(build_scale_corpus(1600))
        index, case_index, case_segments, doc_segments = parts
        self.assertEqual(len(case_segments), 2)
        self.assertEqual(len(doc_segments), 2)
        with self.assertRaises(ContractError):
            codec.restore_scale_corpus(index, case_index, list(reversed(case_segments)), doc_segments)
        with self.assertRaises(ContractError):
            codec.restore_scale_corpus(index, case_index, case_segments[:1], doc_segments)
        with self.assertRaises(ContractError):
            codec.restore_scale_corpus(index, case_index, case_segments, list(reversed(doc_segments)))
        with self.assertRaises(ContractError):
            codec.restore_scale_corpus(index, case_index, case_segments, doc_segments[:1])
        duplicated = deepcopy(parts)
        segment = duplicated[3][0]
        segment["documents"][1] = deepcopy(segment["documents"][0])
        digest = hashlib.sha256(_encoded(segment)).hexdigest()
        duplicated[0]["ordered_document_segments"][0]["digest"] = digest
        with self.assertRaises(ContractError):
            codec.restore_scale_corpus(*duplicated)

    def test_resigned_document_digest_does_not_bypass_corpus_semantics(self):
        parts = codec.partition_scale_corpus(build_scale_corpus(400))
        forged = _resign_semantically_broken(parts)
        # Segment and corpus digests were resigned, and CaseSet refs still resolve.
        self.assertNotEqual(forged[0]["reconstructed_digest"], parts[0]["reconstructed_digest"])
        with self.assertRaises(ContractError): codec.restore_scale_corpus(*forged)

    def test_malformed_sequence_and_case_segment_fail_closed(self):
        parts = codec.partition_scale_corpus(build_scale_corpus(400))
        class FakeList(list): pass
        with self.assertRaises(ContractError): codec.restore_scale_corpus(parts[0], parts[1], parts[2], FakeList(parts[3]))
        changed = deepcopy(parts[2]); changed[0]["first_case_ordinal"] = True
        with self.assertRaises(ContractError): codec.restore_scale_corpus(parts[0], parts[1], changed, parts[3])
        with mock.patch.object(codec, "MAX_DOCUMENT_SEGMENTS", 0):
            with self.assertRaises(ContractError): codec.partition_scale_corpus(build_scale_corpus(400))


    def test_document_segment_boundary_is_inclusive(self):
        source = build_scale_corpus(400)
        parts = codec.partition_scale_corpus(source)
        boundary = len(_encoded(parts[3][0]))
        with mock.patch.object(codec, "MAX_ARTIFACT_BYTES", boundary):
            exact = codec.partition_scale_corpus(source)
            self.assertEqual(len(exact[3]), 1)
            self.assertEqual(codec.restore_scale_corpus(*exact), source)
        with mock.patch.object(codec, "MAX_ARTIFACT_BYTES", boundary - 1):
            split = codec.partition_scale_corpus(source)
            self.assertEqual(len(split[3]), 2)
            self.assertTrue(all(len(_encoded(item)) <= boundary - 1 for item in split[3]))
            self.assertEqual(codec.restore_scale_corpus(*split), source)

    def test_restore_uses_the_document_segment_bytes_it_checked(self):
        source = build_scale_corpus(400)
        parts = codec.partition_scale_corpus(source)
        target = parts[3][0]
        real = codec._canonical
        changed = False
        def change_caller_after_serializing(value, *, maximum):
            nonlocal changed
            raw = real(value, maximum=maximum)
            if value is target and not changed:
                target["documents"][0]["ref"]["digest"] = "f" * 64
                changed = True
            return raw
        with mock.patch.object(codec, "_canonical", side_effect=change_caller_after_serializing):
            restored = codec.restore_scale_corpus(*parts)
        self.assertTrue(changed)
        self.assertEqual(restored, source)


if __name__ == "__main__":
    unittest.main()
