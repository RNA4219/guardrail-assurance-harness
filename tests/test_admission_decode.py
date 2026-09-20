"""Fixture dispatcher reuses only its fresh, verified LLM admission decode."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from gah import fixture_admission, llm_admission, resources
from gah.adoption import AdoptionError
from gah.contracts import MAX_INTEGER


class AdmissionDecodeTests(unittest.TestCase):
    def setUp(self):
        self.value = {
            "kind": llm_admission.KIND,
            "prepared": {"bound_run": {
                "manifest": {"run_id": "admission-run", "created_at": 5,
                             "contract_ref": {"digest": "a" * 64}},
                "policy": {"policy_id": "p"},
                "contract": {"policy_generation": 2},
            }, "target_document": {"behavior_version": "baseline-v1"}},
            "runtime_lock": {}, "materialization": {}, "calibration": {},
        }
        raw, digest = resources._packed(self.value)
        self.row = {"payload_json": raw, "digest": digest, "created_at": 5,
                    "permission_generation": 3, "run_id": "admission-run",
                    "contract_digest": "a" * 64}

    def test_dispatcher_unpacks_once_per_call_and_returns_independent_values(self):
        expected = deepcopy(self.value)
        with patch.object(llm_admission, "expected", return_value=expected), \
             patch.object(resources, "_unpack", wraps=resources._unpack) as unpack:
            first = fixture_admission._verify(self.row, 10)
            self.assertEqual(unpack.call_count, 1)
            second = fixture_admission._verify(self.row, 10)
            self.assertEqual(unpack.call_count, 2)
            first["prepared"]["bound_run"]["policy"]["policy_id"] = "mutated"
            third = fixture_admission._verify(self.row, 10)
            self.assertEqual(unpack.call_count, 3)
        self.assertEqual(first["prepared"]["bound_run"]["policy"]["policy_id"], "mutated")
        self.assertEqual(second, self.value)
        self.assertEqual(third, self.value)
        self.assertIsNot(first, second)
        self.assertIsNot(first["prepared"], second["prepared"])

    def test_public_llm_verify_still_unpacks_and_checks_each_call(self):
        with patch.object(llm_admission, "expected", return_value=deepcopy(self.value)), \
             patch.object(resources, "_unpack", wraps=resources._unpack) as unpack:
            first = llm_admission.verify(self.row, 10)
            second = llm_admission.verify(self.row, 10)
            with self.assertRaisesRegex(AdoptionError, "^LLM_ADMISSION_INVALID$"):
                llm_admission.verify(dict(self.row, contract_digest="b" * 64), 10)
        self.assertEqual(unpack.call_count, 3)
        self.assertEqual(first, self.value)
        self.assertIsNot(first, second)

    def test_corrupt_storage_and_row_time_binding_keep_fixed_failures(self):
        corrupt = dict(self.row, payload_json=self.row["payload_json"] + " ")
        with patch.object(llm_admission, "expected", return_value=deepcopy(self.value)):
            with self.assertRaisesRegex(AdoptionError, "^FIXTURE_ADMISSION_INVALID$"):
                fixture_admission._verify(corrupt, 10)
            wrong_binding = dict(self.row, contract_digest="b" * 64)
            with self.assertRaisesRegex(AdoptionError, "^FIXTURE_ADMISSION_INVALID$"):
                fixture_admission._verify(wrong_binding, 10)
            with self.assertRaisesRegex(AdoptionError, "^FIXTURE_ADMISSION_INVALID$"):
                fixture_admission._verify(self.row, 4)
            with self.assertRaisesRegex(AdoptionError, "^FIXTURE_ADMISSION_INVALID$"):
                fixture_admission._verify(self.row, MAX_INTEGER + 1)

    def test_changed_factory_source_result_is_not_accepted(self):
        changed = deepcopy(self.value)
        changed["runtime_lock"]["image_id"] = "changed-source-image"
        with patch.object(llm_admission, "expected", return_value=changed):
            with self.assertRaisesRegex(AdoptionError, "^FIXTURE_ADMISSION_INVALID$"):
                fixture_admission._verify(self.row, 10)


if __name__ == "__main__":
    unittest.main()
