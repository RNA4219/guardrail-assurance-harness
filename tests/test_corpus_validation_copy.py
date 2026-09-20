import copy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gah import corpus
from gah.contracts import ContractError


def ref(kind="fixture", identifier="item"):
    return {"kind": kind, "id": identifier, "digest": "a" * 64}


def make_case(case_id="case-1", label="positive"):
    detection = {"positive": "detect", "negative": "allow", "indeterminate": "indeterminate"}[label]
    return {
        "case_id": case_id,
        "lineage_group": "lineage-" + case_id,
        "category": "category-a",
        "expected_label": label,
        "oracle_ref": ref("oracle", "oracle-" + case_id),
        "initial_state_ref": ref("state", "state-" + case_id),
        "session_steps": [{
            "stage_id": "stage-" + case_id,
            "input_ref": ref("fixture", "input-" + case_id),
            "expected_detection": detection,
            "event_policy": "none",
        }],
        "scored_stage_id": "stage-" + case_id,
    }


def make_set(*cases, set_id="set-1"):
    return {
        "schema_version": 1,
        "kind": "case_set",
        "case_set_id": set_id,
        "purpose": "development",
        "required_categories": ["category-a"],
        "cases": list(cases),
    }


def _legacy_report(document, other_sets=()):
    """Run the old public-copy path at both corpus_report validation sites."""
    with patch.object(corpus, "_validate_case_set_for_report", corpus.validate_case_set):
        return corpus.corpus_report(document, other_sets)


def _outcome(function, *args):
    try:
        return ("ok", function(*args))
    except Exception as exc:
        return (type(exc).__name__, getattr(exc, "code", None), str(exc))


class CorpusValidationCopyTests(unittest.TestCase):
    def test_public_validator_still_returns_detached_deepcopy(self):
        document = make_set(make_case())
        original = copy.deepcopy(document)
        result = corpus.validate_case_set(document)
        self.assertEqual(result, document)
        self.assertIsNot(result, document)
        result["cases"][0]["session_steps"][0]["input_ref"]["id"] = "mutated-result"
        self.assertEqual(document, original)
        document["cases"][0]["lineage_group"] = "changed-input"
        self.assertNotEqual(result["cases"][0]["lineage_group"], document["cases"][0]["lineage_group"])

    def test_report_matches_legacy_copy_path_and_has_no_case_set_alias(self):
        document = make_set(make_case(), make_case("case-2", "negative"))
        other = make_set(make_case("other-case"), set_id="set-2")
        before = copy.deepcopy(document)
        report = corpus.corpus_report(document, [other])
        legacy = _legacy_report(copy.deepcopy(document), [copy.deepcopy(other)])
        self.assertEqual(report, legacy)
        self.assertEqual(document, before)
        report["label_counts"]["positive"] = 999
        self.assertEqual(document, before)
        document["cases"][0]["expected_label"] = "negative"
        self.assertEqual(report["label_counts"]["positive"], 999)

    def test_exact_list_and_tuple_report_skip_only_internal_deepcopies(self):
        document = make_set(make_case())
        other = make_set(make_case("other-case"), set_id="set-2")
        original_deepcopy = copy.deepcopy
        calls = []
        shim = SimpleNamespace(deepcopy=lambda value: (calls.append(value), original_deepcopy(value))[1])
        with patch.object(corpus, "copy", shim):
            corpus.corpus_report(document, [other])
            self.assertEqual(calls, [])
            _legacy_report(document, [other])
            self.assertEqual(len(calls), 2)
            calls.clear()
            corpus.corpus_report(document, (other,))
            self.assertEqual(calls, [])
        # The public API still performs one detached copy.
        calls.clear()
        with patch.object(corpus, "copy", shim):
            corpus.validate_case_set(document)
        self.assertEqual(len(calls), 1)

    def test_lazy_other_iterable_mutating_document_keeps_legacy_snapshot(self):
        def run(report_function):
            document = make_set(make_case("case-1", "negative"))
            other = make_set(make_case("other-case"), set_id="set-2")
            def values():
                document["cases"][0]["expected_label"] = "positive"
                document["cases"][0]["session_steps"][0]["expected_detection"] = "detect"
                yield other
            report = report_function(document, values())
            return report, document
        optimized, changed = run(corpus.corpus_report)
        legacy, _ = run(_legacy_report)
        self.assertEqual(optimized, legacy)
        self.assertEqual(optimized["label_counts"]["negative"], 1)
        self.assertEqual(optimized["label_counts"]["positive"], 0)
        self.assertEqual(changed["cases"][0]["expected_label"], "positive")

    def test_invalid_missing_duplicate_unicode_and_type_boundaries_match_legacy(self):
        valid = make_set(make_case(), make_case("case-2", "negative"))
        examples = []
        missing = copy.deepcopy(valid); del missing["purpose"]; examples.append(missing)
        duplicate = copy.deepcopy(valid); duplicate["cases"][1]["case_id"] = duplicate["cases"][0]["case_id"]; examples.append(duplicate)
        wrong_type = copy.deepcopy(valid); wrong_type["schema_version"] = True; examples.append(wrong_type)
        wrong_list_type = copy.deepcopy(valid); wrong_list_type["required_categories"] = ("category-a",); examples.append(wrong_list_type)
        astral_id = copy.deepcopy(valid); astral_id["case_set_id"] = "set-😀"; examples.append(astral_id)
        lone_surrogate = copy.deepcopy(valid); lone_surrogate["cases"][0]["case_id"] = "case-\ud800"; examples.append(lone_surrogate)
        for document in examples:
            with self.subTest(case=document):
                public_result = _outcome(corpus.validate_case_set, document)
                old_report = _outcome(_legacy_report, document, ())
                new_report = _outcome(corpus.corpus_report, document, ())
                self.assertEqual(new_report, old_report)
                self.assertIsInstance(public_result[0], str)
                self.assertNotEqual(public_result[0], "ok")

    def test_same_document_in_other_sets_does_not_leak_mutable_alias(self):
        document = make_set(make_case())
        report = corpus.corpus_report(document, [document])
        report["usage_overlaps"].clear()
        report["label_counts"]["positive"] = 17
        self.assertEqual(document["cases"][0]["expected_label"], "positive")
        self.assertEqual(document["case_set_id"], "set-1")


if __name__ == "__main__":
    unittest.main()
