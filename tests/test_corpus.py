import copy
import json
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gah.contracts import ContractError
from gah.corpus import check_calibration, corpus_report, validate_case_set


_DIGEST = "a" * 64


def ref(kind="fixture", identifier="one", digest=_DIGEST):
    return {"kind": kind, "id": identifier, "digest": digest}


def stage(stage_id="stage-1", detection="detect", input_id="input-1"):
    return {
        "stage_id": stage_id,
        "input_ref": ref(identifier=input_id),
        "expected_detection": detection,
        "event_policy": "none",
    }


def case(
    case_id="case-1",
    lineage="lineage-1",
    category="category-a",
    label="positive",
    stages=None,
    scored_stage_id=None,
):
    stages = [stage(detection={"positive": "detect", "negative": "allow", "indeterminate": "indeterminate"}[label])] if stages is None else stages
    return {
        "case_id": case_id,
        "lineage_group": lineage,
        "category": category,
        "expected_label": label,
        "oracle_ref": ref(kind="oracle", identifier=f"oracle-{case_id}"),
        "initial_state_ref": ref(kind="state", identifier=f"state-{case_id}"),
        "session_steps": stages,
        "scored_stage_id": scored_stage_id or stages[0]["stage_id"],
    }


def case_set(*cases, purpose="calibration", categories=None):
    return {
        "schema_version": 1,
        "kind": "case_set",
        "case_set_id": "set-1",
        "purpose": purpose,
        "required_categories": categories or ["category-a"],
        "cases": list(cases),
    }


class CorpusTests(unittest.TestCase):
    def test_validate_case_set_returns_independent_copy(self):
        document = case_set(
            case(label="positive"),
            case(case_id="case-2", lineage="lineage-2", label="negative"),
            case(case_id="case-3", lineage="lineage-3", label="indeterminate"),
        )
        original = copy.deepcopy(document)
        validated = validate_case_set(document)
        self.assertEqual(validated, document)
        self.assertIsNot(validated, document)
        validated["cases"][0]["session_steps"][0]["stage_id"] = "changed"
        self.assertEqual(document, original)

    def test_validate_rejects_lineage_category_stage_and_label_contract_errors(self):
        valid = case_set(case())
        invalid_documents = []

        duplicate_case = copy.deepcopy(valid)
        duplicate_case["cases"].append(copy.deepcopy(duplicate_case["cases"][0]))
        invalid_documents.append(duplicate_case)

        bad_category = copy.deepcopy(valid)
        bad_category["cases"][0]["category"] = "missing-category"
        invalid_documents.append(bad_category)

        duplicate_stage = copy.deepcopy(valid)
        duplicate_stage["cases"][0]["session_steps"] = [
            stage("same", "detect"),
            stage("same", "detect", "input-2"),
        ]
        invalid_documents.append(duplicate_stage)

        bad_scored_stage = copy.deepcopy(valid)
        bad_scored_stage["cases"][0]["scored_stage_id"] = "unknown-stage"
        invalid_documents.append(bad_scored_stage)

        bad_label_mapping = copy.deepcopy(valid)
        bad_label_mapping["cases"][0]["expected_label"] = "negative"
        invalid_documents.append(bad_label_mapping)

        too_many_stages = copy.deepcopy(valid)
        too_many_stages["cases"][0]["session_steps"] = [
            stage("one", "detect"),
            stage("two", "detect", "input-2"),
            stage("three", "detect", "input-3"),
        ]
        invalid_documents.append(too_many_stages)

        for document in invalid_documents:
            with self.subTest(document=document):
                with self.assertRaises(ContractError):
                    validate_case_set(document)

    def test_schema_has_closed_case_set_shape(self):
        schema_path = Path(__file__).resolve().parents[1] / "schemas" / "case-set.v1.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        self.assertEqual(schema["properties"]["kind"]["const"], "case_set")
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["properties"]["cases"]["items"]["$ref"], "#/$defs/case")

    def test_finite_case_set_limits_are_enforced(self):
        too_many_categories = case_set(case(), categories=[f"category-{index}" for index in range(257)])
        with self.assertRaises(ContractError):
            validate_case_set(too_many_categories)

        too_many_cases = case_set(*([case()] * 10001))
        with self.assertRaises(ContractError):
            validate_case_set(too_many_cases)

    def test_corpus_report_separates_counts_from_independence_claims(self):
        first = case()
        second = case(case_id="case-2", lineage="lineage-1")
        # case_idとlineage_groupを変えても、その他の内容が同じなら同内容とする。
        renamed_content = case(case_id="case-3", lineage="lineage-3")
        renamed_content["oracle_ref"] = copy.deepcopy(first["oracle_ref"])
        renamed_content["initial_state_ref"] = copy.deepcopy(first["initial_state_ref"])
        renamed_content["session_steps"][0]["input_ref"] = copy.deepcopy(
            first["session_steps"][0]["input_ref"]
        )
        document = case_set(first, second, renamed_content)
        other = case_set(
            case(case_id="other-1", lineage="other-lineage", label="negative"),
            purpose="acceptance",
        )
        other["case_set_id"] = "set-2"
        other["cases"][0]["oracle_ref"] = first["oracle_ref"]
        other["cases"][0]["initial_state_ref"] = first["initial_state_ref"]
        other["cases"][0]["session_steps"][0]["input_ref"] = first["session_steps"][0]["input_ref"]
        other["cases"][0]["expected_label"] = first["expected_label"]
        other["cases"][0]["session_steps"][0]["expected_detection"] = "detect"
        report = corpus_report(document, (other,))

        self.assertEqual(report["case_count"], 3)
        self.assertEqual(report["label_counts"]["positive"], 3)
        self.assertEqual(report["lineage_counts"]["lineage-1"], 2)
        self.assertTrue(report["lineage_reuse"])
        self.assertTrue(report["lineage_conflicts"] == [])
        self.assertTrue(report["duplicate_content_groups"])
        self.assertTrue(report["usage_overlaps"])
        self.assertFalse(report["structural_requirements"]["acceptance_minimums_met"])
        self.assertFalse(report["independence_validated"])
        self.assertFalse(report["oracle_validated"])
        self.assertEqual(report["claims"]["population_performance"], "unverified")

    def test_corpus_report_detects_lineage_cross_label_and_category(self):
        document = case_set(
            case(label="positive", lineage="shared", category="category-a"),
            case(case_id="case-2", lineage="shared", label="negative", category="category-b"),
            categories=["category-a", "category-b"],
        )
        report = corpus_report(document)
        self.assertEqual(len(report["lineage_conflicts"]), 1)
        conflict = report["lineage_conflicts"][0]
        self.assertEqual(conflict["labels"], ["negative", "positive"])
        self.assertEqual(conflict["categories"], ["category-a", "category-b"])

    def test_sample_fingerprint_ignores_ref_and_stage_ids_but_keeps_input_digest_distinct(self):
        original = case()
        renamed = copy.deepcopy(original)
        renamed["case_id"] = "renamed-case"
        renamed["lineage_group"] = "renamed-lineage"
        renamed["oracle_ref"]["id"] = "renamed-oracle"
        renamed["initial_state_ref"]["id"] = "renamed-state"
        renamed["session_steps"][0]["stage_id"] = "renamed-stage"
        renamed["session_steps"][0]["input_ref"]["id"] = "renamed-input"
        renamed["scored_stage_id"] = "renamed-stage"

        revised = copy.deepcopy(renamed)
        revised["case_id"] = "revised-case"
        revised["lineage_group"] = "revised-lineage"
        revised["category"] = "category-b"
        revised["expected_label"] = "negative"
        revised["oracle_ref"]["digest"] = "b" * 64
        revised["session_steps"][0]["expected_detection"] = "allow"

        different_input = copy.deepcopy(original)
        different_input["case_id"] = "different-input-case"
        different_input["lineage_group"] = "different-input-lineage"
        different_input["session_steps"][0]["input_ref"]["digest"] = "b" * 64

        report = corpus_report(
            case_set(
                original,
                renamed,
                revised,
                different_input,
                categories=["category-a", "category-b"],
            )
        )
        groups = report["duplicate_content_groups"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(
            set(groups[0]["case_ids"]),
            {"case-1", "renamed-case", "revised-case"},
        )
        self.assertEqual(len(report["sample_condition_differences"]), 1)
        self.assertEqual(
            set(report["sample_condition_differences"][0]["differing_fields"]),
            {"category", "expected_label", "oracle", "stage_conditions"},
        )
        self.assertNotIn("different-input-case", groups[0]["case_ids"])

    def test_corpus_report_checks_all_usage_pairs_and_keeps_current_counts_local(self):
        base = case()

        def usage_set(set_id, purpose, case_id, lineage):
            item = case(case_id=case_id, lineage=lineage)
            item["oracle_ref"] = copy.deepcopy(base["oracle_ref"])
            item["initial_state_ref"] = copy.deepcopy(base["initial_state_ref"])
            item["session_steps"][0]["input_ref"] = copy.deepcopy(
                base["session_steps"][0]["input_ref"]
            )
            result = case_set(item, purpose=purpose)
            result["case_set_id"] = set_id
            return result

        development = usage_set("A", "development", "a-case", "a-lineage")
        calibration = usage_set("B", "calibration", "b-case", "b-lineage")
        acceptance = usage_set("C", "acceptance", "c-case", "c-lineage")
        report = corpus_report(development, (calibration, acceptance))

        pairs = {tuple(overlap["case_set_ids"]) for overlap in report["usage_overlaps"]}
        self.assertEqual(pairs, {("A", "B"), ("A", "C"), ("B", "C")})
        purposes = {tuple(overlap["purposes"]) for overlap in report["usage_overlaps"]}
        self.assertEqual(
            purposes,
            {
                ("development", "calibration"),
                ("development", "acceptance"),
                ("calibration", "acceptance"),
            },
        )
        self.assertEqual(report["case_count"], 1)
        self.assertEqual(report["structural_requirements"]["positive_lineages"], 1)

    def test_calibration_passes_only_with_all_labels_and_all_unique_matching_stages(self):
        cases = (
            case(label="positive"),
            case(case_id="case-2", lineage="lineage-2", label="negative"),
            case(case_id="case-3", lineage="lineage-3", label="indeterminate"),
        )
        cases[1]["session_steps"][0]["input_ref"]["digest"] = "b" * 64
        cases[2]["session_steps"][0]["input_ref"]["digest"] = "c" * 64
        document = case_set(*cases)
        observations = [
            {"case_id": item["case_id"], "stage_id": "stage-1", "detection": detection}
            for item, detection in zip(cases, ("detect", "allow", "indeterminate"))
        ]
        result = check_calibration(document, observations)
        self.assertTrue(result["passed"])
        self.assertEqual(result["reasons"], [])
        self.assertEqual(result["expected_observation_count"], 3)
        self.assertEqual(result["observation_count"], 3)
        self.assertFalse(result["independence_validated"])
        self.assertFalse(result["oracle_validated"])

    def test_calibration_requires_three_labels_and_rejects_missing_duplicate_extra_mismatch(self):
        positive = case(label="positive")
        negative = case(case_id="case-2", lineage="lineage-2", label="negative")
        negative["session_steps"][0]["input_ref"]["digest"] = "b" * 64
        document = case_set(positive, negative)
        observations = [
            {"case_id": "case-1", "stage_id": "stage-1", "detection": "allow"},
            {"case_id": "case-1", "stage_id": "stage-1", "detection": "detect"},
            {"case_id": "unknown", "stage_id": "stage-1", "detection": "detect"},
            {"case_id": "case-2", "stage_id": "stage-1", "detection": "allow"},
        ]
        result = check_calibration(document, observations)
        self.assertFalse(result["passed"])
        self.assertEqual(
            result["reasons"],
            [
                "OBSERVATION_EXTRA",
                "OBSERVATION_DUPLICATE",
                "OBSERVATION_MISMATCH",
                "LABEL_MISSING",
            ],
        )

    def test_calibration_requires_every_stage_and_rejects_wrong_purpose_and_shape(self):
        two_stage_case = case(
            stages=[stage("stage-1", "detect"), stage("stage-2", "detect", "input-2")]
        )
        document = case_set(two_stage_case, purpose="development")
        result = check_calibration(
            document,
            [{"case_id": "case-1", "stage_id": "stage-1", "detection": "detect"}],
        )
        self.assertFalse(result["passed"])
        self.assertEqual(
            result["reasons"],
            ["PURPOSE_NOT_CALIBRATION", "OBSERVATION_MISSING", "LABEL_MISSING"],
        )

        malformed = check_calibration(document, [{"case_id": "case-1", "detection": "detect"}])
        self.assertFalse(malformed["passed"])
        self.assertIn("OBSERVATION_INVALID", malformed["reasons"])
        self.assertIn("OBSERVATION_MISSING", malformed["reasons"])


if __name__ == "__main__":
    unittest.main()
