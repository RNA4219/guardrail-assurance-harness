"""固定版Promptfoo出力のwrapper、binding、行、観測写像を検査する。"""

import hashlib
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gah.promptfoo_adapter import normalize_promptfoo, PromptfooAdapterError


ZERO = "0" * 64


def binding() -> dict[str, object]:
    return {
        "run_id": "run-1",
        "operation_id": "operation-1",
        "owner_epoch": 2,
        "contract_digest": "a" * 64,
        "target_digest": "b" * 64,
        "obligation_id": "obligation-1",
        "case_id": "case-1",
        "trial_id": "trial-1",
        "stage_id": "stage-1",
        "fixture_digest": "c" * 64,
        "adapter_digest": "d" * 64,
        "policy_digest": "e" * 64,
        "evaluator_digest": "f" * 64,
        "isolation_digest": ZERO,
    }


def identity() -> dict[str, object]:
    return {
        "evalId": "eval-1",
        "testIdx": 0,
        "promptIdx": 0,
        "provider": {"id": "provider-1", "label": "fixed-provider"},
        "promptId": "prompt-1",
        "version": 3,
    }


def binding_id(value: dict[str, object] | None = None, *, separator: str = ":") -> str:
    canonical = json.dumps(value or binding(), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return "gah-binding" + separator + hashlib.sha256(canonical).hexdigest()


def row(*, success: bool = True, error: str | None = None, output: object | None = None,
        gah: dict[str, object] | None = None) -> dict[str, object]:
    value: dict[str, object] = {
        "promptIdx": 0,
        "testIdx": 0,
        "testCase": {"metadata": {"gah": binding() if gah is None else gah}},
        "promptId": "prompt-1",
        "provider": {"id": "provider-1", "label": "fixed-provider"},
        "prompt": "fixed prompt",
        "vars": {"case": "fixed"},
        "failureReason": 0 if success else 1,
        "success": success,
        "score": 1.0 if success else 0.0,
        "latencyMs": 2.0,
        "namedScores": {"default": 1.0 if success else 0.0},
    }
    if error is not None:
        value["error"] = error
    if output is not None:
        value["response"] = {"output": output}
    return value


def document(one_row: dict[str, object], *, version: int = 3, eval_id: str | None = "eval-1",
             metadata: dict[str, object] | None = None) -> bytes:
    errors = 1 if one_row.get("error") is not None else 0
    successes = 0 if errors or not one_row["success"] else 1
    failures = 1 if not errors and not one_row["success"] else 0
    value = {
        "evalId": eval_id,
        "results": {
            "version": version,
            "timestamp": "2026-09-11T00:00:00Z",
            "results": [one_row],
            "prompts": [],
            "stats": {
                "successes": successes,
                "failures": failures,
                "errors": errors,
                "tokenUsage": {},
            },
        },
        "config": {},
        "shareableUrl": None,
    }
    if metadata is not None:
        value["metadata"] = metadata
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


class PromptfooAdapterTests(unittest.TestCase):
    def test_structured_observation_maps_to_generic_without_using_score(self):
        payload = document(row(output={"mode": "constraint", "observations": {"check": "PASS"}}))
        result = normalize_promptfoo(payload, expected_binding=binding(), expected_identity=identity())
        self.assertEqual(result["mode"], "constraint")
        self.assertEqual(result["observation"], "PASS")
        self.assertIsNone(result["mutation_outcome"])
        self.assertEqual(result["raw_digest"], hashlib.sha256(payload).hexdigest())

    def test_promptfoo_success_does_not_turn_free_text_or_score_into_pass(self):
        for output in ("PASS", {"score": 1.0}, None):
            current = row(output=output)
            if output is None:
                current.pop("response", None)
            with self.subTest(output=output), self.assertRaises(PromptfooAdapterError):
                normalize_promptfoo(document(current), expected_binding=binding(), expected_identity=identity())

    def test_generic_structured_output_is_checked_against_expected_binding(self):
        output = {
            "schema_version": 1,
            "kind": "gah_generic_result",
            "binding": binding(),
            "mode": "mutation",
            "observations": {
                "baseline": "PASS",
                "mutation_applied": True,
                "reached": True,
                "detected": False,
                "unrelated_failure": False,
            },
        }
        result = normalize_promptfoo(document(row(output=output)), expected_binding=binding(), expected_identity=identity())
        self.assertEqual(result["mutation_outcome"], "SURVIVED")

        changed = dict(output)
        changed["binding"] = {**binding(), "run_id": "other-run"}
        with self.assertRaises(PromptfooAdapterError) as raised:
            normalize_promptfoo(document(row(output=changed)), expected_binding=binding(), expected_identity=identity())
        self.assertEqual(raised.exception.code, "BINDING_MISMATCH")

    def test_redacted_export_binding_id_is_derived_from_expected_binding(self):
        payload = document(
            row(gah={"binding_id": binding_id()}, output={"mode": "constraint", "observations": {"check": "PASS"}}),
            metadata={"promptfooVersion": "0.123.0"},
        )
        result = normalize_promptfoo(payload, expected_binding=binding(), expected_identity=identity())
        self.assertEqual(result["observation"], "PASS")

        # dash形式は提案値として受け付けるが、実exportで検閲されない生成形はcolon形式。
        dashed = document(row(gah={"binding_id": binding_id(separator="-")}, output={"mode": "constraint", "observations": {"check": "PASS"}}), metadata={"promptfooVersion": "0.123.0"})
        self.assertEqual(
            normalize_promptfoo(dashed, expected_binding=binding(), expected_identity=identity())["observation"],
            "PASS",
        )

    def test_binding_id_unknown_cross_run_and_version_metadata_are_rejected(self):
        for gah in (
            {"binding_id": binding_id({**binding(), "run_id": "run-2"})},
            {"binding_id": 1},
            {"binding_id": binding_id(), "run_id": "run-1"},
        ):
            with self.subTest(gah=gah), self.assertRaises(PromptfooAdapterError) as raised:
                normalize_promptfoo(document(row(gah=gah)), expected_binding=binding(), expected_identity=identity())
            self.assertEqual(raised.exception.code, "BINDING_MISMATCH" if set(gah) == {"binding_id"} else "OUTPUT_INVALID")

        with self.assertRaises(PromptfooAdapterError) as raised:
            normalize_promptfoo(
                document(row(), metadata={"promptfooVersion": "0.123.1"}),
                expected_binding=binding(), expected_identity=identity(),
            )
        self.assertEqual(raised.exception.code, "VERSION_MISMATCH")

    def test_promptfoo_error_is_execution_error_and_does_not_expose_text(self):
        payload = document(row(success=False, error="provider failed"))
        result = normalize_promptfoo(payload, expected_binding=binding(), expected_identity=identity())
        self.assertEqual(result["mutation_outcome"], "ERROR")
        self.assertEqual(result["error_class"], "EXECUTION_FAILURE")
        self.assertIsNone(result["raw_digest"])
        self.assertNotIn("provider failed", json.dumps(result))

    def test_identity_and_metadata_binding_are_fixed_by_caller(self):
        cases = []
        changed_eval = row(output={"mode": "constraint", "observations": {"check": "PASS"}})
        cases.append((document(changed_eval, eval_id="other-eval"), identity(), "BINDING_MISMATCH"))
        changed_row = row(output={"mode": "constraint", "observations": {"check": "PASS"}})
        changed_row["testIdx"] = 1
        cases.append((document(changed_row), identity(), "BINDING_MISMATCH"))
        changed_binding = row(output={"mode": "constraint", "observations": {"check": "PASS"}})
        changed_binding["testCase"] = {"metadata": {"gah": {**binding(), "case_id": "other-case"}}}
        cases.append((document(changed_binding), identity(), "BINDING_MISMATCH"))
        changed_type = row(output={"mode": "constraint", "observations": {"check": "PASS"}})
        changed_type["testCase"]["metadata"]["gah"]["owner_epoch"] = 2.0
        cases.append((document(changed_type), identity(), "BINDING_MISMATCH"))
        for payload, expected, code in cases:
            with self.subTest(code=code), self.assertRaises(PromptfooAdapterError) as raised:
                normalize_promptfoo(payload, expected_binding=binding(), expected_identity=expected)
            self.assertEqual(raised.exception.code, code)

    def test_unknown_version_missing_duplicate_or_extra_rows_are_rejected(self):
        good = row(output={"mode": "constraint", "observations": {"check": "PASS"}})
        with self.assertRaises(PromptfooAdapterError) as raised:
            normalize_promptfoo(document(good, version=2), expected_binding=binding(), expected_identity=identity())
        self.assertEqual(raised.exception.code, "VERSION_MISMATCH")

        two = document(good)
        parsed = json.loads(two)
        parsed["results"]["results"].append(good)
        with self.assertRaises(PromptfooAdapterError):
            normalize_promptfoo(json.dumps(parsed).encode(), expected_binding=binding(), expected_identity=identity())

        duplicate = b'{"evalId":"eval-1","evalId":"eval-1","results":{}}'
        with self.assertRaises(PromptfooAdapterError) as raised:
            normalize_promptfoo(duplicate, expected_binding=binding(), expected_identity=identity())
        self.assertEqual(raised.exception.code, "DUPLICATE_KEY")

    def test_nonfinite_bom_and_invalid_row_types_are_rejected(self):
        good = document(row(output={"mode": "constraint", "observations": {"check": "PASS"}}))
        for payload in (b"\xef\xbb\xbf" + good, good.replace(b"1.0", b"NaN", 1)):
            with self.subTest(payload=payload[:3]), self.assertRaises(PromptfooAdapterError):
                normalize_promptfoo(payload, expected_binding=binding(), expected_identity=identity())

        bad = row(output={"mode": "constraint", "observations": {"check": "PASS"}})
        bad["success"] = 1
        with self.assertRaises(PromptfooAdapterError):
            normalize_promptfoo(document(bad), expected_binding=binding(), expected_identity=identity())

    def test_structured_output_byte_limit_applies_to_object_outputs_too(self):
        output = {"mode": "constraint", "observations": {"check": "PASS"}}
        output["observations"] = {"check": "PASS", "padding": "x" * (64 * 1024)}
        payload = document(row(output=output))
        with self.assertRaises(PromptfooAdapterError) as raised:
            normalize_promptfoo(payload, expected_binding=binding(), expected_identity=identity())
        self.assertEqual(raised.exception.code, "OUTPUT_TOO_LARGE")

    def test_flat_identity_form_is_supported_with_strict_fields(self):
        expected = {
            "evalId": "eval-1",
            "testIdx": 0,
            "promptIdx": 0,
            "provider_id": "provider-1",
            "provider_label": "fixed-provider",
            "promptId": "prompt-1",
            "version": 3,
        }
        payload = document(row(output={"mode": "constraint", "observations": {"check": "UNKNOWN"}}))
        self.assertEqual(
            normalize_promptfoo(payload, expected_binding=binding(), expected_identity=expected)["observation"],
            "UNKNOWN",
        )


if __name__ == "__main__":
    unittest.main()
