from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gah.contracts import ContractError, MAX_DOCUMENT_BYTES
from gah.pilot_inputs import import_document, import_file, validate_import


_DIGEST = "a" * 64


def _ref(kind: str, identifier: str) -> dict[str, str]:
    return {"kind": kind, "id": identifier, "digest": _DIGEST}


def _bundle(use_case: str = "UC-CI") -> dict:
    common = {
        "schema_version": 1,
        "kind": "pilot_input_bundle",
        "id": "bundle-1",
        "use_case": use_case,
        "source_ref": _ref("snapshot_manifest", "source-1"),
        "target_ref": _ref("target", "target-1"),
        "dataset_ref": _ref("case_set", "dataset-1"),
        "oracle_ref": _ref("pilot_oracle", "oracle-1"),
        "revision_ref": _ref(
            "repository_snapshot" if use_case == "UC-CI" else "model_revision",
            "revision-1",
        ),
        "split_ref": _ref("dataset_split", "split-1"),
        "required_categories": [] if use_case == "UC-CI" else ["cat-a"],
        "created_at": 100,
        "cases": [],
    }
    if use_case == "UC-CI":
        common["cases"] = [
            {"case_id": "ci-allow", "expected": "allow", "observed": "allow",
             "status": "COMPLETE"},
            {"case_id": "ci-mismatch", "expected": "block", "observed": "allow",
             "status": "COMPLETE"},
            {"case_id": "ci-unknown", "expected": "block", "observed": None,
             "status": "UNKNOWN"},
            {"case_id": "ci-missing", "expected": "allow", "observed": None,
             "status": "MISSING"},
        ]
    else:
        def row(case_id, label, prediction, status="COMPLETE", **claims):
            return {
                "case_id": case_id, "category": "cat-a",
                "expected_label": label, "prediction": prediction,
                "status": status,
                "oracle_independent": claims.get("oracle_independent", True),
                "measurement_reproduced": claims.get("measurement_reproduced", True),
                "all_measurement_obligations_met": claims.get(
                    "all_measurement_obligations_met", True
                ),
            }
        common["cases"] = [
            row("llm-tp", "positive", "detect"),
            row("llm-fp", "negative", "detect"),
            row("llm-fn", "positive", "allow"),
            row("llm-tn", "negative", "allow"),
            row("llm-unknown", "indeterminate", "indeterminate"),
            row("llm-missing", None, None, "MISSING",
                oracle_independent=False, measurement_reproduced=False,
                all_measurement_obligations_met=False),
        ]
    return common


class PilotInputsTests(unittest.TestCase):
    def test_ci_aggregates_expected_observed_unknown_and_missing(self):
        artifact = import_document(_bundle())
        self.assertEqual(artifact["kind"], "pilot_input_import")
        self.assertEqual(artifact["use_case"], "UC-CI")
        counts = artifact["aggregation"]
        self.assertEqual(counts["case_count"], 4)
        self.assertEqual(counts["complete_count"], 2)
        self.assertEqual(counts["expected_allow"], 2)
        self.assertEqual(counts["expected_block"], 2)
        self.assertEqual(counts["observed_allow"], 2)
        self.assertEqual(counts["matched_count"], 1)
        self.assertEqual(counts["mismatch_count"], 1)
        self.assertEqual(counts["unknown_count"], 1)
        self.assertEqual(counts["missing_count"], 1)
        self.assertEqual(artifact["unknown"], ["ci-unknown"])
        self.assertEqual(artifact["missing"], ["ci-missing"])
        self.assertEqual(artifact["verification_status"], "PENDING")
        self.assertEqual(artifact["pac_status"], "NOT_RUN")
        for name in (
            "external_refs_verified", "independent_oracle_verified",
            "evaluation_complete", "ci_eligible", "product_run_authority",
        ):
            self.assertIs(artifact[name], False)

    def test_llm_counts_detection_and_false_positive_without_promoting_claims(self):
        artifact = import_document(_bundle("UC-LLM"))
        counts = artifact["aggregation"]
        self.assertEqual(counts["tp"], 1)
        self.assertEqual(counts["tn"], 1)
        self.assertEqual(counts["fp"], 1)
        self.assertEqual(counts["fn"], 1)
        self.assertEqual(counts["positive_denominator"], 2)
        self.assertEqual(counts["negative_denominator"], 2)
        self.assertEqual(counts["unknown_count"], 1)
        self.assertEqual(counts["missing_count"], 1)
        self.assertFalse(counts["required_categories_have_each_100"])
        self.assertEqual(artifact["unknown"], ["llm-unknown"])
        self.assertEqual(artifact["missing"], ["llm-missing"])
        self.assertEqual(
            artifact["aggregation"]["categories"]["cat-a"]["positive"], 2
        )
        self.assertEqual(
            artifact["aggregation"]["categories"]["cat-a"]["negative"], 2
        )
        self.assertIs(artifact["external_refs_verified"], False)
        self.assertIs(artifact["independent_oracle_verified"], False)
        self.assertNotIn("PASS", json.dumps(artifact, ensure_ascii=False))

    def test_duplicate_and_open_or_summary_fields_are_rejected(self):
        duplicate = _bundle()
        duplicate["cases"].append(copy.deepcopy(duplicate["cases"][0]))
        with self.assertRaises(ContractError) as ctx:
            import_document(duplicate)
        self.assertEqual(ctx.exception.code, "DUPLICATE_CASE_ID")

        prompt = _bundle()
        prompt["cases"][0]["prompt"] = "secret prompt"
        with self.assertRaises(ContractError):
            validate_import(prompt)
        summary = _bundle("UC-LLM")
        summary["required_categories_have_each_100"] = True
        with self.assertRaises(ContractError):
            validate_import(summary)
        version = _bundle()
        version["schema_version"] = True
        with self.assertRaises(ContractError):
            validate_import(version)

    def test_refs_are_fixed_and_revision_depends_on_use_case(self):
        invalid = _bundle()
        invalid["target_ref"] = _ref("repository_snapshot", "wrong-target")
        with self.assertRaises(ContractError) as ctx:
            validate_import(invalid)
        self.assertEqual(ctx.exception.code, "BINDING_MISMATCH")

        invalid = _bundle()
        invalid["revision_ref"] = _ref("model_revision", "wrong-revision")
        with self.assertRaises(ContractError) as ctx:
            validate_import(invalid)
        self.assertEqual(ctx.exception.code, "BINDING_MISMATCH")

        invalid = _bundle("UC-LLM")
        invalid["revision_ref"] = _ref("repository_snapshot", "wrong-revision")
        with self.assertRaises(ContractError) as ctx:
            validate_import(invalid)
        self.assertEqual(ctx.exception.code, "BINDING_MISMATCH")

    def test_result_is_detached_and_does_not_copy_prompt_or_secret(self):
        source = _bundle("UC-LLM")
        artifact = import_document(source)
        source["cases"][0]["case_id"] = "changed-after-import"
        self.assertEqual(artifact["cases"][0]["case_id"], "llm-tp")
        text = json.dumps(artifact, ensure_ascii=False)
        self.assertNotIn("prompt", text)
        self.assertNotIn("secret", text)

    def test_file_entry_rejects_outside_and_oversized_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            input_path = workspace / "input.json"
            input_path.write_text(
                json.dumps(_bundle(), ensure_ascii=False), encoding="utf-8"
            )
            artifact = import_file(workspace, "input.json")
            self.assertEqual(artifact["kind"], "pilot_input_import")

            outside = workspace.parent / "outside.json"
            outside.write_text("{}", encoding="utf-8")
            try:
                with self.assertRaises(ContractError) as ctx:
                    import_file(workspace, outside)
                self.assertEqual(ctx.exception.code, "PATH_REJECTED")
            finally:
                outside.unlink(missing_ok=True)

            oversized = workspace / "oversized.json"
            oversized.write_bytes(b"{" + b"x" * (MAX_DOCUMENT_BYTES + 1))
            with self.assertRaises(ContractError) as ctx:
                import_file(workspace, oversized)
            self.assertIn(ctx.exception.code, {"DOCUMENT_SIZE", "INVALID_INPUT"})

    def test_file_entry_rejects_links_when_platform_allows_link_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            real = workspace / "real.json"
            real.write_text(
                json.dumps(_bundle(), ensure_ascii=False), encoding="utf-8"
            )
            link = workspace / "link.json"
            try:
                os.symlink(real, link)
            except (OSError, NotImplementedError):
                self.skipTest("このWindows環境ではlink作成権限がない")
            with self.assertRaises(ContractError) as ctx:
                import_file(workspace, link)
            self.assertEqual(ctx.exception.code, "PATH_REJECTED")


if __name__ == "__main__":
    unittest.main()
