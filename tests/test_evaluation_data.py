import copy
import hashlib
import json
import sys
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gah.contracts import ContractError
from gah.evaluation_data import (
    build_pack,
    input_document,
    main,
    oracle_detection,
    validate_pack,
)


class EvaluationDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pack = build_pack()

    def test_pack_has_exact_case_distribution_and_stage_mix(self):
        self.assertEqual(self.pack["pack_id"], "synthetic-policy-v1")
        expected = {
            "acceptance": (400, {"data_handling": 200, "work_scope": 200}),
            "calibration": (18, {"data_handling": 9, "work_scope": 9}),
            "development": (12, {"data_handling": 6, "work_scope": 6}),
        }
        for purpose, (total, category_totals) in expected.items():
            case_set = self.pack["case_sets"][purpose]
            self.assertEqual(len(case_set["cases"]), total)
            for category, category_total in category_totals.items():
                cases = [c for c in case_set["cases"] if c["category"] == category]
                self.assertEqual(len(cases), category_total)
                counts = {}
                for case in cases:
                    counts[case["expected_label"]] = counts.get(case["expected_label"], 0) + 1
                if purpose == "acceptance":
                    self.assertEqual(counts, {"positive": 100, "negative": 100})
                elif purpose == "calibration":
                    self.assertEqual(counts, {"positive": 3, "negative": 3, "indeterminate": 3})
                else:
                    self.assertEqual(counts, {"positive": 3, "negative": 3})
                self.assertEqual(sum(len(c["session_steps"]) == 1 for c in cases), sum(1 for i in range(category_total) if (i // 2) % 2 == 0))
                self.assertEqual(sum(len(c["session_steps"]) == 2 for c in cases), sum(1 for i in range(category_total) if (i // 2) % 2 == 1))

    def test_all_document_refs_have_canonical_digest_and_input_lookup_is_copy(self):
        for item in self.pack["documents"]:
            raw = json.dumps(item["document"], ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
            self.assertEqual(hashlib.sha256(raw).hexdigest(), item["ref"]["digest"])
        reference = next(item["ref"] for item in self.pack["documents"] if item["ref"]["kind"] == "synthetic_policy_input")
        document = input_document(self.pack, reference)
        document["observed"]["local_destination"] = not document["observed"]["local_destination"]
        self.assertNotEqual(document, input_document(self.pack, reference))

    def test_oracle_labels_are_derived_from_observed_conditions(self):
        documents = {
            (item["ref"]["kind"], item["ref"]["id"]): item["document"]
            for item in self.pack["documents"]
        }
        for purpose in ("acceptance", "calibration", "development"):
            for case in self.pack["case_sets"][purpose]["cases"]:
                score = case["session_steps"][-1]
                document = documents[(score["input_ref"]["kind"], score["input_ref"]["id"])]
                self.assertEqual(oracle_detection(document), score["expected_detection"])
        with self.assertRaises(ContractError):
            oracle_detection({
                "schema_version": 1,
                "kind": "synthetic_policy_input",
                "category": "data_handling",
                "required": ["local_destination", "authorized_recipient"],
                "observed": {
                    "local_destination": 1,
                    "authorized_recipient": True,
                    "declared_purpose": True,
                    "minimized_fields": True,
                    "marker_masked": True,
                    "retention_valid": True,
                    "redistribution_allowed": True,
                    "approved_source": True,
                },
            })

    def test_oracle_detects_false_before_indeterminate(self):
        document = input_document(
            self.pack,
            next(
                case["session_steps"][-1]["input_ref"]
                for case in self.pack["case_sets"]["acceptance"]["cases"]
                if case["expected_label"] == "positive"
            ),
        )
        document["observed"][document["required"][0]] = False
        document["observed"][document["required"][1]] = None
        self.assertEqual(oracle_detection(document), "detect")

    def test_positive_violations_cover_all_eight_required_features(self):
        documents = {
            (item["ref"]["kind"], item["ref"]["id"]): item["document"]
            for item in self.pack["documents"]
        }
        features = {
            "data_handling": {
                "local_destination", "authorized_recipient", "declared_purpose",
                "minimized_fields", "marker_masked", "retention_valid",
                "redistribution_allowed", "approved_source",
            },
            "work_scope": {
                "path_allowed", "tool_declared", "operation_allowed", "branch_allowed",
                "dependency_allowed", "budget_valid", "authorization_present", "output_allowed",
            },
        }
        for category in features:
            observed_false = set()
            for case in self.pack["case_sets"]["acceptance"]["cases"]:
                if case["category"] != category or case["expected_label"] != "positive":
                    continue
                document = documents[(case["session_steps"][-1]["input_ref"]["kind"], case["session_steps"][-1]["input_ref"]["id"])]
                observed_false.update(name for name in document["required"] if document["observed"][name] is False)
            self.assertEqual(observed_false, features[category])

    def test_degraded_oracles_are_rejected_by_calibration_and_acceptance(self):
        documents = {
            (item["ref"]["kind"], item["ref"]["id"]): item["document"]
            for item in self.pack["documents"]
        }
        mismatches = {
            purpose: {"any_observed": 0, "first_required_only": 0}
            for purpose in ("calibration", "acceptance")
        }
        for purpose in ("calibration", "acceptance"):
            for case in self.pack["case_sets"][purpose]["cases"]:
                score = case["session_steps"][-1]
                document = documents[(score["input_ref"]["kind"], score["input_ref"]["id"])]
                observed = document["observed"]
                def three_value(values):
                    if any(value is False for value in values):
                        return "detect"
                    if any(value is None for value in values):
                        return "indeterminate"
                    return "allow"

                any_false = three_value(observed.values())
                first_only = three_value([observed[document["required"][0]]])
                expected = score["expected_detection"]
                mismatches[purpose]["any_observed"] += any_false != expected
                mismatches[purpose]["first_required_only"] += first_only != expected
        for purpose in ("calibration", "acceptance"):
            self.assertGreater(mismatches[purpose]["any_observed"], 0)
            self.assertGreater(mismatches[purpose]["first_required_only"], 0)

    def test_unrequired_false_is_ignored_by_the_independent_oracle(self):
        documents = {
            (item["ref"]["kind"], item["ref"]["id"]): item["document"]
            for item in self.pack["documents"]
        }
        checked = 0
        for purpose in ("calibration", "acceptance", "development"):
            for case in self.pack["case_sets"][purpose]["cases"]:
                if case["expected_label"] != "negative":
                    continue
                score = case["session_steps"][-1]
                document = documents[(score["input_ref"]["kind"], score["input_ref"]["id"])]
                outside_false = [
                    name for name, value in document["observed"].items()
                    if name not in document["required"] and value is False
                ]
                if outside_false:
                    checked += 1
                    self.assertEqual(oracle_detection(document), "allow")
        self.assertGreater(checked, 0)

    def test_setup_and_indeterminate_have_one_irrelevant_false(self):
        documents = {
            (item["ref"]["kind"], item["ref"]["id"]): item["document"]
            for item in self.pack["documents"]
        }
        setup_checked = indeterminate_checked = 0
        for purpose in ("calibration", "acceptance"):
            for case in self.pack["case_sets"][purpose]["cases"]:
                if len(case["session_steps"]) == 2:
                    setup = documents[(case["session_steps"][0]["input_ref"]["kind"], case["session_steps"][0]["input_ref"]["id"])]
                    outside = [
                        name for name, value in setup["observed"].items()
                        if name not in setup["required"] and value is False
                    ]
                    self.assertEqual(len(outside), 1)
                    setup_checked += 1
                if case["expected_label"] == "indeterminate":
                    score = case["session_steps"][-1]
                    document = documents[(score["input_ref"]["kind"], score["input_ref"]["id"])]
                    outside = [
                        name for name, value in document["observed"].items()
                        if name not in document["required"] and value is False
                    ]
                    self.assertEqual(len(outside), 1)
                    self.assertEqual(oracle_detection(document), "indeterminate")
                    indeterminate_checked += 1
        self.assertGreater(setup_checked, 0)
        self.assertGreater(indeterminate_checked, 0)

    def test_two_stage_setup_uses_reserved_non_scoring_mask(self):
        scoring_digests = {
            case["session_steps"][-1]["input_ref"]["digest"]
            for case in self.pack["case_sets"]["acceptance"]["cases"]
        }
        two_stage = next(
            case for case in self.pack["case_sets"]["acceptance"]["cases"]
            if len(case["session_steps"]) == 2
        )
        setup = input_document(self.pack, two_stage["session_steps"][0]["input_ref"])
        self.assertEqual(setup["required"], [
            "authorized_recipient", "declared_purpose",
            "minimized_fields", "marker_masked", "retention_valid",
            "redistribution_allowed", "approved_source",
        ])
        self.assertEqual(setup["observed"]["local_destination"], False)
        self.assertNotIn(two_stage["session_steps"][0]["input_ref"]["digest"], scoring_digests)

    def test_calibration_contains_three_labels_and_is_not_acceptance(self):
        calibration = self.pack["case_sets"]["calibration"]
        labels = {case["expected_label"] for case in calibration["cases"]}
        self.assertEqual(labels, {"positive", "negative", "indeterminate"})
        acceptance_inputs = {
            case["session_steps"][-1]["input_ref"]["digest"]
            for case in self.pack["case_sets"]["acceptance"]["cases"]
        }
        calibration_inputs = {
            case["session_steps"][-1]["input_ref"]["digest"]
            for case in calibration["cases"]
        }
        self.assertTrue(acceptance_inputs.isdisjoint(calibration_inputs))

    def test_validate_rejects_id_renamed_duplicate_content(self):
        changed = copy.deepcopy(self.pack)
        cases = changed["case_sets"]["acceptance"]["cases"]
        duplicate = copy.deepcopy(cases[0])
        duplicate["case_id"] = "renamed-duplicate-case"
        duplicate["lineage_group"] = "renamed-duplicate-lineage"
        duplicate["session_steps"][0]["stage_id"] = "renamed-setup-stage"
        duplicate["session_steps"][-1]["stage_id"] = "renamed-score-stage"
        duplicate["scored_stage_id"] = "renamed-score-stage"
        cases[-1] = duplicate
        with self.assertRaises(ContractError):
            validate_pack(changed)

    def test_validate_rejects_ref_content_drift_and_category_shared_document(self):
        changed = copy.deepcopy(self.pack)
        item = next(item for item in changed["documents"] if item["ref"]["kind"] == "synthetic_policy_input")
        item["document"]["observed"]["local_destination"] = not item["document"]["observed"]["local_destination"]
        with self.assertRaises(ContractError):
            validate_pack(changed)

    def test_every_case_ref_must_match_the_stored_ref_digest(self):
        for field in ("initial_state_ref", "oracle_ref", "input_ref"):
            changed = copy.deepcopy(self.pack)
            target = changed["case_sets"]["acceptance"]["cases"][0]
            if field == "input_ref":
                target = target["session_steps"][-1]
            target[field]["digest"] = "f" * 64
            with self.subTest(field=field):
                with self.assertRaises(ContractError):
                    validate_pack(changed)

    def test_validate_returns_independent_copy(self):
        validated = validate_pack(self.pack)
        self.assertEqual(validated, self.pack)
        self.assertIsNot(validated, self.pack)
        validated["provenance"]["generator"] = "changed"
        self.assertNotEqual(validated["provenance"], self.pack["provenance"])

    def test_predicate_witness_and_safety_claims_are_explicit(self):
        self.assertIn("finite", self.pack["provenance"]["independence_scope"])
        self.assertIn("not claimed", self.pack["provenance"]["independence_scope"])
        self.assertIn("unverified", self.pack["provenance"]["oracle_scope"])
        self.assertIn("no secrets", self.pack["provenance"]["safety_boundary"])

    def test_pack_validation_does_not_trust_oracle_generator(self):
        with patch("gah.evaluation_data.oracle_detection", return_value="allow"):
            self.assertEqual(validate_pack(self.pack), self.pack)

    def test_explicit_build_writes_only_required_dataset_files(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(main(["--build", "--output", directory]), 0)
            self.assertEqual(
                {path.name for path in Path(directory).iterdir()},
                {"pack.json", "README.md", "manifest.json"},
            )
            loaded = json.loads((Path(directory) / "pack.json").read_text(encoding="utf-8"))
            self.assertEqual(validate_pack(loaded), self.pack)
            readme = (Path(directory) / "README.md").read_text(encoding="utf-8")
            self.assertTrue(readme.startswith(
                "---\nintent_id: INT-GAH-001\nowner: RNA4219\nstatus: draft\n"
                "last_reviewed_at: 2026-09-11\nnext_review_due: 2026-10-11\n---\n"
            ))


if __name__ == "__main__":
    unittest.main()
