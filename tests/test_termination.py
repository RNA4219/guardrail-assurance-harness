import itertools
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gah.termination import decide_terminal


_BOOL_FIELDS = (
    "cancelled",
    "stopped",
    "deadline_reached",
    "work_complete",
    "required_failure",
    "budget_closed",
)
_ASSURANCE_STATES = ("HEALTHY", "WARNING", "DEGRADED", "UNKNOWN", "HOLD")
_ASSURANCE_PRIORITY = {
    "HOLD": 0,
    "DEGRADED": 1,
    "UNKNOWN": 2,
    "WARNING": 3,
    "HEALTHY": 4,
}
_ANY = object()

# 正本の優先順を、実装と別の表として表す。左から引数の順に対応する。
_STATUS_RULES = (
    ((_ANY, _ANY, _ANY, _ANY, True, _ANY), ("FAILED", 2)),
    ((True, True, _ANY, _ANY, False, _ANY), ("CANCELLED", 3)),
    ((True, False, True, _ANY, False, _ANY), ("FAILED", 2)),
    ((True, False, False, _ANY, False, _ANY), ("WAITING", None)),
    ((False, _ANY, True, _ANY, False, _ANY), ("FAILED", 2)),
    ((False, True, False, True, False, True), ("COMPLETED", 1)),
    ((_ANY, _ANY, _ANY, _ANY, False, _ANY), ("WAITING", None)),
)
_REASON_RULES = (
    ("REQUIRED_FAILURE", 4, True),
    ("CANCEL_REQUESTED", 0, True),
    ("STOP_UNCONFIRMED", 1, False),
    ("DEADLINE_REACHED", 2, True),
    ("WORK_INCOMPLETE", 3, False),
    ("BUDGET_OPEN", 5, False),
)


def _status_from_table(flags):
    """仕様表から期待する終了状態を引く。"""

    return next(
        result
        for pattern, result in _STATUS_RULES
        if all(expected is _ANY or expected == actual for expected, actual in zip(pattern, flags))
    )


def _reasons_from_table(flags):
    """既知事実から理由codeを固定順で引く。"""

    return [
        code
        for code, index, expected in _REASON_RULES
        if flags[index] is expected
    ]


class TerminationTests(unittest.TestCase):
    def test_all_boolean_combinations_and_assurance_states(self):
        combinations = tuple(itertools.product((False, True), repeat=6))
        self.assertEqual(len(combinations), 64)

        for flags in combinations:
            arguments = dict(zip(_BOOL_FIELDS, flags))
            expected_status, expected_exit = _status_from_table(flags)
            expected_reasons = _reasons_from_table(flags)
            for assurance in _ASSURANCE_STATES:
                with self.subTest(flags=flags, assurance=assurance):
                    result = decide_terminal(**arguments, assurance=assurance)
                    self.assertEqual(
                        set(result),
                        {
                            "schema_version",
                            "purpose",
                            "ci_eligible",
                            "execution_status",
                            "exit_code",
                            "assurance",
                            "reasons",
                        },
                    )
                    self.assertEqual(result["schema_version"], 1)
                    self.assertEqual(result["purpose"], "component_validation")
                    self.assertIs(result["ci_eligible"], False)
                    self.assertEqual(result["execution_status"], expected_status)
                    self.assertEqual(result["exit_code"], expected_exit)
                    self.assertEqual(result["reasons"], expected_reasons)

                    # 停止・作業・費用の不足はUNKNOWN以上に緩めない。
                    shortfall = not (flags[1] and flags[3] and flags[5])
                    expected_assurance = (
                        "UNKNOWN"
                        if shortfall and _ASSURANCE_PRIORITY[assurance] > _ASSURANCE_PRIORITY["UNKNOWN"]
                        else assurance
                    )
                    self.assertEqual(result["assurance"], expected_assurance)

    def test_keyword_only_and_strict_input_types(self):
        values = dict(
            cancelled=False,
            stopped=True,
            deadline_reached=False,
            work_complete=True,
            required_failure=False,
            budget_closed=True,
            assurance="HEALTHY",
        )
        with self.assertRaises(TypeError):
            decide_terminal(False, True, False, True, False, True, "HEALTHY")

        for field in _BOOL_FIELDS:
            for invalid in (0, 1, "false", None):
                candidate = dict(values)
                candidate[field] = invalid
                with self.subTest(field=field, invalid=repr(invalid)):
                    with self.assertRaisesRegex(ValueError, "^invalid termination request$"):
                        decide_terminal(**candidate)

        for invalid in (True, 1, None, "healthy", "UNKNOWN "):
            candidate = dict(values)
            candidate["assurance"] = invalid
            with self.subTest(assurance=repr(invalid)):
                with self.assertRaisesRegex(ValueError, "^invalid termination request$"):
                    decide_terminal(**candidate)

    def test_reason_order_is_stable_when_all_reasons_apply(self):
        result = decide_terminal(
            cancelled=True,
            stopped=False,
            deadline_reached=True,
            work_complete=False,
            required_failure=True,
            budget_closed=False,
            assurance="HOLD",
        )
        self.assertEqual(
            result["reasons"],
            [
                "REQUIRED_FAILURE",
                "CANCEL_REQUESTED",
                "STOP_UNCONFIRMED",
                "DEADLINE_REACHED",
                "WORK_INCOMPLETE",
                "BUDGET_OPEN",
            ],
        )
        self.assertEqual(result["execution_status"], "FAILED")
        self.assertEqual(result["exit_code"], 2)
        self.assertEqual(result["assurance"], "HOLD")

    def test_no_input_value_is_echoed_in_validation_error(self):
        values = dict(
            cancelled=False,
            stopped=True,
            deadline_reached=False,
            work_complete=True,
            required_failure=False,
            budget_closed=True,
            assurance="SECRET-assurance",
        )
        with self.assertRaises(ValueError) as raised:
            decide_terminal(**values)
        self.assertEqual(str(raised.exception), "invalid termination request")
        self.assertNotIn("SECRET-assurance", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
