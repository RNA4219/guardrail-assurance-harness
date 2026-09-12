"""終了状態を副作用なしで決定する部品。"""

from typing import Any


_ASSURANCE_STATES = frozenset(
    {"HEALTHY", "WARNING", "DEGRADED", "UNKNOWN", "HOLD"}
)
_REASON_ORDER = (
    ("REQUIRED_FAILURE", "required_failure", True),
    ("CANCEL_REQUESTED", "cancelled", True),
    ("STOP_UNCONFIRMED", "stopped", False),
    ("DEADLINE_REACHED", "deadline_reached", True),
    ("WORK_INCOMPLETE", "work_complete", False),
    ("BUDGET_OPEN", "budget_closed", False),
)


def _invalid() -> ValueError:
    # 入力値を例外へ含めず、呼出し境界からの情報漏えいを防ぐ。
    return ValueError("invalid termination request")


def _require_bool(value: Any) -> None:
    # boolはintのサブクラスなので、typeで厳密に判定する。
    if type(value) is not bool:
        raise _invalid()


def decide_terminal(
    *,
    cancelled: bool,
    stopped: bool,
    deadline_reached: bool,
    work_complete: bool,
    required_failure: bool,
    budget_closed: bool,
    assurance: str,
) -> dict[str, Any]:
    """終了状態、終了コード、保証状態、既知理由を決定する。

    引数はすべてkeyword-onlyで受け、入力の検査以外の副作用を持たない。
    終了状態の優先順は lifecycle-detail-spec §5 に従う。
    """

    for value in (
        cancelled,
        stopped,
        deadline_reached,
        work_complete,
        required_failure,
        budget_closed,
    ):
        _require_bool(value)
    if type(assurance) is not str or assurance not in _ASSURANCE_STATES:
        raise _invalid()

    reasons: list[str] = []
    values = {
        "required_failure": required_failure,
        "cancelled": cancelled,
        "stopped": stopped,
        "deadline_reached": deadline_reached,
        "work_complete": work_complete,
        "budget_closed": budget_closed,
    }
    for code, field, expected in _REASON_ORDER:
        if values[field] is expected:
            reasons.append(code)

    # 必須の停止・作業・費用が不足した場合は、元のDEGRADED/HOLDを保つ。
    assurance_out = assurance
    if (
        (not stopped or not work_complete or not budget_closed)
        and assurance in {"HEALTHY", "WARNING"}
    ):
        assurance_out = "UNKNOWN"

    if required_failure:
        execution_status, exit_code = "FAILED", 2
    elif cancelled and stopped:
        execution_status, exit_code = "CANCELLED", 3
    elif cancelled and not stopped:
        if deadline_reached:
            execution_status, exit_code = "FAILED", 2
        else:
            execution_status, exit_code = "WAITING", None
    elif deadline_reached:
        execution_status, exit_code = "FAILED", 2
    elif stopped and work_complete and budget_closed:
        execution_status, exit_code = "COMPLETED", 1
    else:
        execution_status, exit_code = "WAITING", None

    return {
        "schema_version": 1,
        "purpose": "component_validation",
        "ci_eligible": False,
        "execution_status": execution_status,
        "exit_code": exit_code,
        "assurance": assurance_out,
        "reasons": reasons,
    }


__all__ = ["decide_terminal"]
