"""管理AI要求生成と応答採択の境界を検査する。"""

import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gah.management import (  # noqa: E402
    MAX_RESPONSE_BYTES,
    ManagementError,
    build_request,
    parse_response,
)
from gah.policy import initial_policy_profile  # noqa: E402


_MISSING = object()


def _response(*, timeout=110, reason="budget_headroom", usage=_MISSING, content=None, **extra):
    if content is None:
        content = json.dumps(
            {"timeout_seconds": timeout, "reason": reason},
            separators=(",", ":"),
        )
    document = {
        "model": "qwen3.8-flash-next",
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
    }
    if usage is _MISSING:
        usage = {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}
    if usage is not None:
        document["usage"] = usage
    document.update(extra)
    return json.dumps(document, separators=(",", ":")).encode("utf-8")


class ManagementRequestTests(unittest.TestCase):
    def test_build_request_is_fixed_and_returns_independent_messages(self):
        first = build_request()
        second = build_request()
        self.assertEqual(first, second)
        self.assertEqual(first["model"], "qwen3.8-flash-next")
        self.assertEqual(first["temperature"], 0)
        self.assertEqual(first["max_tokens"], 128)
        self.assertEqual(first["chat_template_kwargs"], {"enable_thinking": False})
        self.assertEqual(len(first["messages"]), 2)
        first["messages"][0]["content"] = "changed"
        self.assertNotEqual(first, second)
        self.assertNotIn("candidate", second["messages"][0]["content"])
        self.assertNotIn("history", second["messages"][0]["content"])


class ManagementResponseTests(unittest.TestCase):
    def test_valid_response_discards_provider_extras_and_applies_timeout(self):
        raw = _response(id="provider-id", created=123, system_fingerprint="ignored")
        result = parse_response(raw)
        self.assertEqual(
            set(result), {"schema_version", "kind", "selection", "policy", "model", "usage"}
        )
        self.assertEqual(result["selection"], {"timeout_seconds": 110, "reason": "budget_headroom"})
        self.assertEqual(result["policy"]["per_call"]["timeout_seconds"], 110)
        self.assertEqual(result["model"], "qwen3.8-flash-next")
        self.assertEqual(result["usage"], {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12})
        expected = initial_policy_profile()
        expected["per_call"]["timeout_seconds"] = 110
        self.assertEqual(result["policy"], expected)

    def test_result_policy_is_independent_from_a_later_parse(self):
        first = parse_response(_response(timeout=90, reason="lower_latency"))
        second = parse_response(_response(timeout=120, reason="keep_initial"))
        first["policy"]["per_call"]["timeout_seconds"] = 1
        self.assertEqual(second["policy"]["per_call"]["timeout_seconds"], 120)

    def test_raw_input_is_not_mutated_and_content_must_be_exact_selection(self):
        raw = _response(timeout=100, reason="keep_initial")
        before = bytes(raw)
        parse_response(raw)
        self.assertEqual(raw, before)
        for content in (
            "Here is the JSON: {\"timeout_seconds\":100,\"reason\":\"keep_initial\"}",
            '{"timeout_seconds":100,"reason":"keep_initial","actor":"manager"}',
            '{"timeout_seconds":100,"reason":"free text"}',
        ):
            with self.subTest(content=content), self.assertRaises(ManagementError):
                parse_response(_response(content=content))


class ManagementRejectionTests(unittest.TestCase):
    def assertCode(self, raw, code):
        with self.assertRaisesRegex(ManagementError, f"^{code}$") as context:
            parse_response(raw)
        self.assertEqual(context.exception.code, code)

    def test_input_encoding_and_json_boundaries(self):
        self.assertCode(None, "INPUT_TYPE")
        self.assertCode(b"x" * (MAX_RESPONSE_BYTES + 1), "RESPONSE_TOO_LARGE")
        self.assertCode(b"\xff", "INVALID_UTF8")
        self.assertCode(b"\xef\xbb\xbf{}", "BOM")
        self.assertCode(b'{"model":"qwen3.8-flash-next","model":"other"}', "DUPLICATE_KEY")
        self.assertCode(_response(content="NaN"), "NONFINITE_NUMBER")

    def test_provider_shape_and_model_boundaries(self):
        self.assertCode(_response(model="other"), "MODEL_MISMATCH")
        self.assertCode(_response(choices=[]), "INVALID_CHOICES")
        self.assertCode(_response(choices=[{}, {}]), "INVALID_CHOICES")
        self.assertCode(_response(choices=[{"message": {"content": "{}"}, "finish_reason": "length"}]), "INVALID_FINISH_REASON")
        self.assertCode(_response(choices=[{"message": {"content": "{}"}, "finish_reason": "stop"}]), "INVALID_SELECTION")

    def test_selection_values_are_strict(self):
        for timeout in (89, 121, True, "110"):
            with self.subTest(timeout=timeout):
                self.assertCode(_response(timeout=timeout), "INVALID_TIMEOUT")
        for reason in ("", "LOWER_LATENCY", True, 1):
            with self.subTest(reason=reason):
                self.assertCode(_response(reason=reason), "INVALID_REASON")
        self.assertCode(_response(content='{"timeout_seconds":110,"reason":"keep_initial","actor":"manager"}'), "INVALID_SELECTION")

    def test_usage_is_required_bounded_and_integer_consistent(self):
        self.assertCode(_response(usage=None), "INVALID_USAGE")
        for usage in (
            {"prompt_tokens": True, "completion_tokens": 2, "total_tokens": 3},
            {"prompt_tokens": 1, "completion_tokens": 129, "total_tokens": 130},
            {"prompt_tokens": 32769, "completion_tokens": 2, "total_tokens": 32771},
            {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 11},
            {"prompt_tokens": -1, "completion_tokens": 2, "total_tokens": 1},
            {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12, "extra": 0},
        ):
            with self.subTest(usage=usage):
                self.assertCode(_response(usage=usage), "INVALID_USAGE")

    def test_published_usage_details_are_discarded(self):
        result = parse_response(_response(usage={"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12,
            "prompt_tokens_details": {"cached_tokens": 4}, "completion_tokens_details": None}))
        self.assertEqual(result["usage"], {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12})


if __name__ == "__main__":
    unittest.main()
