from __future__ import annotations

import copy
import hashlib
import json
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gah.contracts import ContractError, MAX_DOCUMENT_BYTES
from gah.partitioned_trial_plan import (
    INDEX_KIND,
    MAX_ARTIFACT_BYTES,
    MAX_RECONSTRUCTED_BYTES,
    MAX_SEGMENTS,
    SEGMENT_KIND,
    _canonical,
    _segment_id,
    partition_trial_plan,
    restore_trial_plan,
    validate_partitioned_trial_plan,
)
from gah.run_contracts import content_ref, validate_trial_plan


def _plan(entries: int = 1, *, padded: bool = False) -> dict:
    contract = {"schema_version": 1, "kind": "evaluation_contract", "contract_id": "contract-1"}
    contract_ref = content_ref("evaluation_contract", "contract-1", contract)
    result = {
        "schema_version": 1,
        "kind": "trial_plan",
        "plan_id": "trial-plan-1",
        "contract_ref": contract_ref,
        "entries": [],
    }
    for number in range(entries):
        case_id = f"case-{number:04d}" + ("-" + "c" * 15 if padded else "")
        trial_id = f"trial-{number:04d}" + ("-" + "t" * 12 if padded else "")
        result["entries"].append({
            "obligation_id": "obligation-llm",
            "case_id": case_id,
            "trial_id": trial_id,
            "variant": "candidate",
            "stage_ids": ["stage-1"],
            "required": True,
            "event_policy": "none",
            "evaluator_ref": content_ref("evaluator", "evaluator-1", {"kind": "evaluator"}),
            "target_ref": content_ref("target", "target-1", {"kind": "target"}),
        })
    return result


def _raw(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


class PartitionedTrialPlanTests(unittest.TestCase):
    def test_small_plan_roundtrips_identically_to_v1_validator(self) -> None:
        plan = _plan(3)
        expected = validate_trial_plan(plan)
        index, segments = partition_trial_plan(plan)
        self.assertEqual(index["kind"], INDEX_KIND)
        self.assertEqual(validate_partitioned_trial_plan(index, segments), expected)
        self.assertEqual(restore_trial_plan(index, segments), expected)
        self.assertNotIn("ci_eligible", index)

    def test_3200_entries_above_v1_wire_limit_roundtrip_with_exact_digest(self) -> None:
        plan = _plan(3200, padded=True)
        logical_bytes = _raw(plan)
        self.assertGreater(len(logical_bytes), MAX_DOCUMENT_BYTES)
        self.assertGreater(len(logical_bytes), 1_200_000)
        self.assertLess(len(logical_bytes), MAX_RECONSTRUCTED_BYTES)
        self.assertEqual(len(logical_bytes), 1437017)
        with self.assertRaises(ContractError):
            validate_trial_plan(plan)

        index, segments = partition_trial_plan(plan)
        self.assertEqual(index["entry_count"], 3200)
        self.assertLessEqual(index["segment_count"], MAX_SEGMENTS)
        self.assertEqual(index["reconstructed_bytes"], len(logical_bytes))
        self.assertEqual(index["reconstructed_digest"], hashlib.sha256(logical_bytes).hexdigest())
        segment_lengths = [len(_raw(item)) for item in segments]
        self.assertTrue(all(length <= MAX_ARTIFACT_BYTES for length in segment_lengths))
        self.assertLessEqual(len(_raw(index)), MAX_ARTIFACT_BYTES)
        # Deterministic measured fixture: segment totals include repeated wire headers.
        self.assertEqual(segment_lengths, [899689, 537798])
        self.assertEqual(sum(segment_lengths), 1437487)

        restored = restore_trial_plan(index, segments)
        self.assertEqual(restored, plan)
        self.assertEqual(_raw(restored), logical_bytes)

    def test_partition_and_restore_return_mutation_isolated_trees(self) -> None:
        plan = _plan(4)
        expected = copy.deepcopy(plan)
        index, segments = partition_trial_plan(plan)
        plan["entries"][0]["case_id"] = "caller-mutated"
        self.assertEqual(restore_trial_plan(index, segments), expected)

        restored = restore_trial_plan(index, segments)
        restored["entries"][0]["target_ref"]["id"] = "returned-mutated"
        self.assertEqual(restore_trial_plan(index, segments), expected)
        self.assertNotEqual(segments[0]["entries"][0]["target_ref"]["id"], "returned-mutated")

    def test_ref_order_missing_segment_and_digest_mutations_reject(self) -> None:
        index, segments = partition_trial_plan(_plan(3200, padded=True))
        self.assertGreater(len(segments), 1)

        with self.assertRaises(ContractError):
            restore_trial_plan(index, segments[:-1])
        with self.assertRaises(ContractError):
            restore_trial_plan(index, list(reversed(segments)))
        duplicate_index = copy.deepcopy(index)
        duplicate_index["ordered_segments"][1] = copy.deepcopy(duplicate_index["ordered_segments"][0])
        with self.assertRaises(ContractError):
            restore_trial_plan(duplicate_index, segments)

        changed = copy.deepcopy(index)
        changed["reconstructed_digest"] = "0" * 64
        with self.assertRaises(ContractError):
            restore_trial_plan(changed, segments)

        changed_segments = copy.deepcopy(segments)
        changed_segments[0]["entries"][0]["case_id"] = "tampered-case"
        with self.assertRaises(ContractError):
            restore_trial_plan(index, changed_segments)

    def test_closed_shapes_integer_types_and_plan_contract_binding(self) -> None:
        index, segments = partition_trial_plan(_plan(2))
        unknown = copy.deepcopy(index)
        unknown["extra"] = 1
        with self.assertRaises(ContractError):
            restore_trial_plan(unknown, segments)

        boolean = copy.deepcopy(segments)
        boolean[0]["segment_index"] = True
        with self.assertRaises(ContractError):
            restore_trial_plan(index, boolean)

        wrong_contract = copy.deepcopy(segments)
        wrong_contract[0]["contract_ref"]["digest"] = "0" * 64
        with self.assertRaises(ContractError):
            restore_trial_plan(index, wrong_contract)

        wrong_plan = copy.deepcopy(segments)
        wrong_plan[0]["plan_id"] = "other-plan"
        with self.assertRaises(ContractError):
            restore_trial_plan(index, wrong_plan)

    def test_duplicate_entry_and_empty_or_excessive_index_reject(self) -> None:
        plan = _plan(2)
        plan["entries"][1] = copy.deepcopy(plan["entries"][0])
        with self.assertRaises(ContractError):
            partition_trial_plan(plan)

        index, segments = partition_trial_plan(_plan(1))
        empty = copy.deepcopy(segments[0])
        empty["entries"] = []
        empty["entry_count"] = 0
        with self.assertRaises(ContractError):
            restore_trial_plan(index, [empty])

        too_many = copy.deepcopy(index)
        too_many["segment_count"] = MAX_SEGMENTS + 1
        too_many["ordered_segments"] = [
            {"kind": SEGMENT_KIND, "id": f"segment-{i}", "digest": "0" * 64}
            for i in range(MAX_SEGMENTS + 1)
        ]
        with self.assertRaises(ContractError):
            restore_trial_plan(too_many, [segments[0]] * (MAX_SEGMENTS + 1))

    def test_canonical_rules_match_v1_for_depth_nodes_floats_integers_and_utf8(self) -> None:
        from gah.run_contracts import _canonical as v1_canonical

        valid = {"a": [1, True, None, "\u65e5\u672c\u8a9e"]}
        self.assertEqual(_canonical(valid, maximum=MAX_ARTIFACT_BYTES), v1_canonical(valid))
        invalid_values = (
            1.5,
            float("nan"),
            2**54,
            "\ud800",
            [[[[[[[[[[[[[[[[[[None]]]]]]]]]]]]]]]]]],
            [None] * 100_001,
        )
        for value in invalid_values:
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaises(ContractError):
                    _canonical(value, maximum=MAX_RECONSTRUCTED_BYTES)
                with self.assertRaises(ContractError):
                    v1_canonical(value)

    def test_global_node_limit_is_checked_after_valid_segment_refs(self) -> None:
        logical = _plan(6000)
        raw = _raw(logical)
        segments = []
        refs = []
        for segment_index, first in enumerate(range(0, 6000, 1500)):
            entries = copy.deepcopy(logical["entries"][first:first + 1500])
            segment = {
                "schema_version": 2,
                "kind": SEGMENT_KIND,
                "id": _segment_id(logical["plan_id"], segment_index),
                "plan_id": logical["plan_id"],
                "contract_ref": copy.deepcopy(logical["contract_ref"]),
                "segment_index": segment_index,
                "first_entry_ordinal": first,
                "entry_count": len(entries),
                "entries": entries,
            }
            _canonical(segment, maximum=MAX_ARTIFACT_BYTES)
            segments.append(segment)
            refs.append(content_ref(SEGMENT_KIND, segment["id"], segment))
        index = {
            "schema_version": 2,
            "kind": INDEX_KIND,
            "plan_id": logical["plan_id"],
            "contract_ref": copy.deepcopy(logical["contract_ref"]),
            "entry_count": len(logical["entries"]),
            "segment_count": len(segments),
            "ordered_segments": refs,
            "reconstructed_bytes": len(raw),
            "reconstructed_digest": hashlib.sha256(raw).hexdigest(),
        }
        with self.assertRaises(ContractError) as raised:
            restore_trial_plan(index, segments)
        self.assertEqual(raised.exception.code, "DOCUMENT_COMPLEXITY")

    def test_cross_segment_duplicate_rejects_after_all_refs_and_outer_digest_are_resigned(self) -> None:
        index, segments = partition_trial_plan(_plan(3200, padded=True))
        duplicated = copy.deepcopy(segments)
        duplicated[1]["entries"][0] = copy.deepcopy(duplicated[0]["entries"][0])
        logical_entries = [entry for segment in duplicated for entry in segment["entries"]]
        rebuilt = {
            "schema_version": 1,
            "kind": "trial_plan",
            "plan_id": index["plan_id"],
            "contract_ref": copy.deepcopy(index["contract_ref"]),
            "entries": logical_entries,
        }
        resigned = copy.deepcopy(index)
        resigned["ordered_segments"][1] = content_ref(
            SEGMENT_KIND, duplicated[1]["id"], duplicated[1]
        )
        raw = _raw(rebuilt)
        resigned["reconstructed_bytes"] = len(raw)
        resigned["reconstructed_digest"] = hashlib.sha256(raw).hexdigest()
        with self.assertRaises(ContractError) as raised:
            restore_trial_plan(resigned, duplicated)
        self.assertEqual(raised.exception.code, "DUPLICATE_ID")

    def test_reconstructed_byte_limit_and_strict_plain_json_rules(self) -> None:
        index, segments = partition_trial_plan(_plan(2))
        too_large = copy.deepcopy(index)
        too_large["reconstructed_bytes"] = MAX_RECONSTRUCTED_BYTES + 1
        with self.assertRaises(ContractError):
            restore_trial_plan(too_large, segments)

        invalid_float = _plan(1)
        invalid_float["entries"][0]["required"] = 1.0
        with self.assertRaises(ContractError):
            partition_trial_plan(invalid_float)

        invalid_int = _plan(1)
        invalid_int["entries"][0]["stage_ids"] = ["stage-1", 2**54]
        with self.assertRaises(ContractError):
            partition_trial_plan(invalid_int)


if __name__ == "__main__":
    unittest.main()
