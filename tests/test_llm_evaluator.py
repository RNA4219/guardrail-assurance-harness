"""無害なLLM入力、使用量回収、段階状態、独立した逸脱観測を検査する。"""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from gah.contracts import ContractError
from gah.evaluation_data import build_pack
from gah.llm_evaluator import MODEL, SyntheticEvaluator, parse_response


def response(detection="allow", action="apply", *, content=None, **changes):
    document = {"model": MODEL, "choices": [{"index": 0, "finish_reason": "stop",
        "message": {"role": "assistant", "content": content if content is not None else json.dumps({"detection": detection, "action": action})}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 15, "total_tokens": 115}}
    document.update(changes)
    return json.dumps(document).encode("utf-8")


class ResponseTests(unittest.TestCase):
    def test_usage_survives_invalid_selection_without_free_text(self):
        result = parse_response(response(content="untrusted-response-body"))
        self.assertEqual(result["status"], "INVALID_OUTPUT")
        self.assertEqual(result["usage"]["total_tokens"], 115)
        self.assertIsNone(result["selection"])
        self.assertNotIn("untrusted-response-body", json.dumps(result))

    def test_usage_and_model_require_exact_values(self):
        for usage in ({"prompt_tokens": True, "completion_tokens": 15, "total_tokens": 16},
                      {"prompt_tokens": 100, "completion_tokens": 15, "total_tokens": 114},
                      {"prompt_tokens": 100, "completion_tokens": 129, "total_tokens": 229}, None):
            with self.subTest(usage=usage):
                result = parse_response(response(usage=usage))
                self.assertEqual(result["status"], "INVALID_OUTPUT")
                self.assertIsNone(result["usage"])
        result = parse_response(response(model="unexpected-model"))
        self.assertEqual(result["reason_code"], "MODEL_MISMATCH")
        self.assertEqual(result["usage"]["total_tokens"], 115)

    def test_duplicate_keys_nonfinite_encoding_and_unknown_selection_fail(self):
        for raw in (b'{"usage":0,"usage":0}', b'{"usage":NaN}', b'\xff', b'[]', b'x' * 65537,
                    response(content='{"detection":"allow","detection":"detect","action":"block"}'),
                    response(content='{"detection":[],"action":"apply"}'),
                    response(content='{"detection":"allow","action":"execute"}'),
                    response(content='{"detection":"allow","action":"apply","command":"ignored"}')):
            with self.subTest(length=len(raw)):
                self.assertEqual(parse_response(raw)["status"], "INVALID_OUTPUT")

    def test_truncation_multiple_choices_and_tool_messages_fail(self):
        original = json.loads(response())
        variants = []
        doc = deepcopy(original)
        doc["choices"][0]["finish_reason"] = "length"
        variants.append(doc)
        doc = deepcopy(original)
        doc["choices"].append(deepcopy(doc["choices"][0]))
        variants.append(doc)
        doc = deepcopy(original)
        doc["choices"][0]["message"]["tool_calls"] = [{"name": "ignored"}]
        variants.append(doc)
        for doc in variants:
            result = parse_response(json.dumps(doc).encode())
            self.assertEqual(result["status"], "INVALID_OUTPUT")
            self.assertEqual(result["usage"]["total_tokens"], 115)


class SessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pack = build_pack()
        cls.evaluator = SyntheticEvaluator(cls.pack)

    def test_only_registered_cases_and_purposes_are_accepted(self):
        for purpose, case in (("acceptance", "missing"), ("calibration", "data_handling-acceptance-case-000"),
                              ("unknown", "missing")):
            with self.assertRaises(ContractError):
                self.evaluator.open_case(purpose, case)

    def test_each_case_is_fresh_and_does_not_send_expected_labels(self):
        case_id = "data_handling-acceptance-case-000"
        first = self.evaluator.open_case("acceptance", case_id)
        second = self.evaluator.open_case("acceptance", case_id)
        request_one = first.prepare()
        request_two = second.prepare()
        self.assertEqual(request_one, request_two)
        text = json.dumps(request_one["request"])
        self.assertNotIn("expected_label", text)
        self.assertNotIn("oracle_ref", text)
        request_one["request"]["messages"][0]["content"] = "mutated caller copy"
        first.accept(response("detect", "block"))
        self.assertFalse(second.snapshot()["complete"])
        self.assertTrue(first.snapshot()["complete"])
        self.assertFalse(first.snapshot()["ci_eligible"])
        with self.assertRaises(ContractError):
            first.prepare()

    def test_deviation_uses_counter_effect_not_detection_complement(self):
        for detection, action, expected in (("detect", "apply", True), ("allow", "block", False),
                                             ("allow", "apply", True), ("detect", "block", False)):
            with self.subTest(detection=detection, action=action):
                session = self.evaluator.open_case("acceptance", "data_handling-acceptance-case-000")
                session.prepare()
                result = session.accept(response(detection, action))
                self.assertIs(result["observations"]["deviation"], expected)
                self.assertEqual(result["effect"]["counter_after"], int(action == "apply"))
                self.assertEqual(result["observations"]["detection"], detection)

    def test_two_stage_order_retains_effect_and_sanitized_history(self):
        session = self.evaluator.open_case("acceptance", "data_handling-acceptance-case-002")
        first = session.prepare()
        self.assertEqual(first["stage_id"], "setup-stage")
        with self.assertRaises(ContractError):
            session.prepare()
        result = session.accept(response())
        self.assertFalse(result["observations"]["deviation"])
        self.assertFalse(session.snapshot()["complete"])
        second = session.prepare()
        self.assertEqual(len(second["request"]["messages"]), 4)
        self.assertEqual(second["stage_id"], "score-stage")
        result = session.accept(response("detect", "apply"))
        self.assertEqual(result["effect"], {"counter_before": 1, "counter_after": 2, "applied": True})
        self.assertTrue(result["observations"]["deviation"])
        self.assertTrue(session.snapshot()["complete"])
        with self.assertRaises(ContractError):
            session.accept(response())

    def test_failed_first_stage_cannot_be_replaced_with_success(self):
        session = self.evaluator.open_case("acceptance", "data_handling-acceptance-case-002")
        with self.assertRaises(ContractError):
            session.accept(response())
        session.prepare()
        session.accept(response(content="invalid"))
        self.assertTrue(session.snapshot()["failed"])
        self.assertFalse(session.snapshot()["complete"])
        with self.assertRaises(ContractError):
            session.prepare()
        with self.assertRaises(ContractError):
            session.accept(response())

    def test_indeterminate_applied_effect_is_not_claimed_safe(self):
        for action in ("apply", "block", "defer"):
            with self.subTest(action=action):
                session = self.evaluator.open_case("calibration", "data_handling-calibration-case-002")
                session.prepare()
                session.accept(response())
                session.prepare()
                result = session.accept(response("indeterminate", action))
                self.assertIsNone(result["observations"]["deviation"])
                self.assertEqual(result["effect"]["applied"], action == "apply")


if __name__ == "__main__":
    unittest.main()
