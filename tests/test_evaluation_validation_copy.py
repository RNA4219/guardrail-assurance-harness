from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gah import corpus, evaluation_data
from gah.contracts import ContractError


class EvaluationValidationCopyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.pack = evaluation_data._make_pack()

    def test_document_map_matches_legacy_copy_oracle_and_borrows_private_values(self) -> None:
        pack = copy.deepcopy(self.pack)
        with mock.patch.object(evaluation_data.copy, "deepcopy", wraps=copy.deepcopy) as copied:
            actual = evaluation_data._validate_documents(pack)
        copied.assert_not_called()
        legacy = {
            (item["ref"]["kind"], item["ref"]["id"]): (
                copy.deepcopy(item["ref"]), copy.deepcopy(item["document"])
            )
            for item in pack["documents"]
        }
        self.assertEqual(actual, legacy)
        for item in pack["documents"]:
            key = (item["ref"]["kind"], item["ref"]["id"])
            self.assertIs(actual[key][0], item["ref"])
            self.assertIs(actual[key][1], item["document"])

    def test_input_document_private_default_still_returns_independent_copy(self) -> None:
        document = next(
            item["document"] for item in self.pack["documents"]
            if item["ref"]["kind"] == "synthetic_policy_input"
        )
        validated = evaluation_data._validate_input_document(document)
        self.assertEqual(validated, document)
        self.assertIsNot(validated, document)
        self.assertIsNot(validated["observed"], document["observed"])
        key = next(iter(validated["observed"]))
        validated["observed"][key] = not validated["observed"][key]
        self.assertNotEqual(validated, document)

    def test_validate_pack_matches_detached_legacy_value_and_repeat_isolation(self) -> None:
        pack = copy.deepcopy(self.pack)
        expected_legacy_public_value = copy.deepcopy(pack)
        with mock.patch.object(
            corpus, "_validate_case_set", wraps=corpus._validate_case_set
        ) as validate_case_set_spy:
            result = evaluation_data.validate_pack(pack)
        self.assertEqual(result, expected_legacy_public_value)
        self.assertIsNot(result, pack)
        self.assertTrue(all(call.kwargs.get("copy_result") is False
                            for call in validate_case_set_spy.call_args_list))
        self.assertGreaterEqual(
            sum(call.kwargs.get("copy_result") is False for call in validate_case_set_spy.call_args_list),
            3,
        )
        result["documents"][0]["document"]["kind"] = "caller-mutated"
        result["case_sets"]["acceptance"]["cases"][0]["case_id"] = "caller-mutated"
        self.assertEqual(pack, self.pack)
        self.assertEqual(evaluation_data.validate_pack(pack), expected_legacy_public_value)

    def test_invalid_reference_case_shape_and_input_types_still_reject(self) -> None:
        mutations = []

        changed_ref = copy.deepcopy(self.pack)
        changed_ref["documents"][0]["ref"]["digest"] = "0" * 64
        mutations.append(changed_ref)

        changed_label = copy.deepcopy(self.pack)
        changed_label["case_sets"]["acceptance"]["cases"][0]["expected_label"] = "unknown"
        mutations.append(changed_label)

        changed_type = copy.deepcopy(self.pack)
        first_input = next(
            item["document"] for item in changed_type["documents"]
            if item["ref"]["kind"] == "synthetic_policy_input"
        )
        first_input["observed"][next(iter(first_input["observed"]))] = 1
        # Keep the document digest valid so the semantic type validator must reject.
        changed_item = next(item for item in changed_type["documents"] if item["document"] is first_input)
        changed_item["ref"]["digest"] = hashlib.sha256(json.dumps(
            first_input, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")).hexdigest()
        mutations.append(changed_type)

        changed_ref_shape = copy.deepcopy(self.pack)
        changed_ref_shape["documents"][0]["ref"]["extra"] = True
        mutations.append(changed_ref_shape)

        for invalid in mutations:
            with self.subTest(mutation=len(str(invalid))):
                with self.assertRaises(ContractError):
                    evaluation_data.validate_pack(invalid)


if __name__ == "__main__":
    unittest.main()
