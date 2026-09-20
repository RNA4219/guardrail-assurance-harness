from __future__ import annotations

import hashlib
import time
from typing import Any, Callable

from .benchmark import (OPERATION_EXIT_CODES, fraction_wire, metric_summary, ratio,
                        validate_observation, validate_observation_artifact)
from .contracts import ContractError, MAX_INTEGER, require_id, require_ref
from .productization import content_ref, validate_plan
from .wire import canonical_bytes

SURFACES = ("candidate", "current_ci", "report")
CASE_COUNTS = (400, 800, 1600)
HISTORY_COUNTS = (1, 10, 100)
COLD_COUNT, WARMUP_COUNT, WARM_COUNT = 3, 5, 100
DEFAULT_EXECUTION_BUDGET_NS = 7_200 * 1_000_000_000
MAX_EXECUTION_BUDGET_NS = 14_400 * 1_000_000_000
PLAN_KIND = "benchmark_query_series_plan"
CELL_KIND = "benchmark_query_series_cell"
RESULT_KIND = "benchmark_query_series_result"
RECEIPT_KIND = "benchmark_query_series_cell_receipt"


def _ref(value: Any) -> dict:
    require_ref(value)
    return value


def _validate_payload(payload: Any) -> dict:
    fields = {"surfaces", "case_counts", "history_run_counts", "page_size",
              "cold_iterations", "warmup_iterations", "warm_iterations",
              "valid_for_slo", "ci_eligible"}
    if type(payload) is not dict or set(payload) != fields:
        raise ContractError("INVALID_INPUT")
    if (type(payload["surfaces"]) is not list or payload["surfaces"] != list(SURFACES)
            or type(payload["case_counts"]) is not list
            or any(type(x) is not int for x in payload["case_counts"])
            or payload["case_counts"] != list(CASE_COUNTS)
            or type(payload["history_run_counts"]) is not list
            or any(type(x) is not int for x in payload["history_run_counts"])
            or payload["history_run_counts"] != list(HISTORY_COUNTS)):
        raise ContractError("INVALID_INPUT")
    for field, expected in (("cold_iterations", COLD_COUNT),
                            ("warmup_iterations", WARMUP_COUNT),
                            ("warm_iterations", WARM_COUNT)):
        if type(payload[field]) is not int or payload[field] != expected:
            raise ContractError("INVALID_INPUT")
    if (type(payload["page_size"]) is not int or not 1 <= payload["page_size"] <= 100
            or payload["valid_for_slo"] is not False or payload["ci_eligible"] is not False):
        raise ContractError("INVALID_INPUT")
    return payload


def make_query_series_plan(*, identifier: str, source_ref: dict,
                           requirements_ref: dict, page_size: int = 100,
                           created_at: int | None = None, expires_at: int | None = None) -> dict:
    """固定27-cell計画。これはSLO/CI採択を発行しない。"""
    require_id(identifier)
    _ref(source_ref)
    _ref(requirements_ref)
    if source_ref.get("kind") != "snapshot_manifest" or requirements_ref.get("kind") != "snapshot_manifest":
        raise ContractError("BINDING_MISMATCH")
    if type(page_size) is not int or not 1 <= page_size <= 100:
        raise ContractError("INVALID_INPUT")
    now = int(time.time()) if created_at is None else created_at
    if type(now) is not int or now < 0 or now >= MAX_INTEGER - 3600:
        raise ContractError("INVALID_INPUT")
    end = now + 3600 if expires_at is None else expires_at
    if type(end) is not int or end <= now:
        raise ContractError("INVALID_INPUT")
    plan = {"schema_version": 1, "kind": PLAN_KIND, "id": identifier,
            "requirement_ids": ["GAH-PR07"], "source_ref": dict(source_ref),
            "requirements_ref": dict(requirements_ref), "created_at": now,
            "expires_at": end,
            "payload": {"surfaces": list(SURFACES), "case_counts": list(CASE_COUNTS),
                        "history_run_counts": list(HISTORY_COUNTS), "page_size": page_size,
                        "cold_iterations": COLD_COUNT, "warmup_iterations": WARMUP_COUNT,
                        "warm_iterations": WARM_COUNT, "valid_for_slo": False,
                        "ci_eligible": False}}
    return validate_query_series_plan(plan, now=now)


def validate_query_series_plan(plan: Any, *, now: int | None = None) -> dict:
    def check(payload):
        return _validate_payload(payload)
    validated = validate_plan(plan, kind=PLAN_KIND, payload_validator=check, now=now)
    if validated["requirement_ids"] != ["GAH-PR07"]:
        raise ContractError("INVALID_INPUT")
    return validated


def _clone(document: dict) -> dict:
    # Existing canonical wire contract enforces size/depth and severs nested aliases.
    from .contracts import decode_document
    return decode_document(canonical_bytes(document))


def _cell_id(surface: str, cases: int, history: int) -> str:
    return f"query-{surface}-{cases}-{history}"


def _obs(plan: dict, cell_id: str, source_ref: dict, iteration: int,
         warmness: str, start: int | None, end: int | None,
         failure: str | None = None) -> dict:
    wall = _duration(start, end)
    if wall is None and failure is None:
        failure = "MEASUREMENT_ERROR"
    if failure == "CANCELLED_STOP_UNCONFIRMED":
        failure = "CANCELLED_STOP_UNCONFIRMED"
    status = "COMPLETED" if failure is None else "INCOMPLETE"
    failure_class = "NONE" if failure is None else failure
    payload = {"iteration": iteration, "warmness": "cold" if warmness == "cold" else "warm",
        "wall_ns": wall, "cpu_ns": None, "target_wait_ns": None,
        "harness_wall_ns": None, "rss_group_peak_bytes": None, "io_read_bytes": None,
        "io_write_bytes": None, "copy_count": None, "serialize_bytes": None,
        "hash_count": None, "db_scan_count": None, "authority_call_count": None,
        "stored_bytes": None, "retry_count": 0, "failure_class": failure_class,
        "operation_status": status, "exit_code": OPERATION_EXIT_CODES[status],
        "valid_for_slo": False}
    validate_observation(payload)
    return validate_observation_artifact({"schema_version": 1, "kind": "benchmark_observation",
        "id": f"{cell_id}-obs-{iteration:03d}", "plan_ref": content_ref(PLAN_KIND, plan["id"], plan),
        "source_ref": source_ref, "iteration_id": f"{cell_id}-obs-{iteration:03d}", "payload": payload})


class QuerySeriesCancelled(Exception):
    """Adapter-reported cancellation; shutdown confirmation is not established."""


class _Timer:
    def __init__(self, clock):
        self.clock = clock
        self.last = None
        self.failed = False
    def now(self):
        if self.failed:
            return None
        try:
            value = self.clock()
        except Exception:
            self.failed = True
            return None
        if type(value) is not int or value < 0 or (self.last is not None and value < self.last):
            self.failed = True
            return None
        self.last = value
        return value


def _duration(start, end):
    if type(start) is not int or type(end) is not int or end < start:
        return None
    duration = end-start
    return duration if duration <= MAX_INTEGER else None


def _query(session: Any, request: dict) -> dict:
    value = session.query(request)
    if type(value) is not dict or set(value) != {"exit_code"} or type(value["exit_code"]) is not int:
        raise ContractError("INVALID_INPUT")
    if value["exit_code"] == 3:
        raise QuerySeriesCancelled
    if value["exit_code"] != 0:
        raise RuntimeError("QUERY_FAILED")
    return value


def _summary(observations: list[dict], phase: str) -> dict:
    rows = [item["payload"] for item in observations if item["payload"]["warmness"] == phase]
    good = [item["wall_ns"] for item in rows if item["failure_class"] == "NONE"]
    if phase == "warm":
        good = [item["payload"]["wall_ns"] for item in observations
                if item["payload"]["warmness"] == "warm"
                and item["payload"]["iteration"] > COLD_COUNT + WARMUP_COUNT
                and item["payload"]["failure_class"] == "NONE"]
        if len(good) != WARM_COUNT:
            return {"count": len(good), "p95_ns": None, "median": None, "max_ns": None}
    if not good or (phase == "warm" and len(good) != WARM_COUNT):
        return {"count": len(good), "p95_ns": None, "median": None, "max_ns": None}
    summary = metric_summary(good)
    return {"count": summary["count"], "p95_ns": summary["p95"],
            "median": fraction_wire(summary["median"]), "max_ns": summary["max"]}


def run_query_series(plan: dict, adapter: Any, persist_cell: Callable[[dict], dict], *,
                     monotonic_clock: Callable[[], int] = time.perf_counter_ns,
                     execution_budget_ns: int = DEFAULT_EXECUTION_BUDGET_NS) -> dict:
    """Execute fixed cells with a cooperative boundary budget and retain slow observations.

    The budget is not a hard deadline: blocking adapter or persistence calls cannot be
    interrupted here and need independent transport timeouts. SLO thresholds are not
    execution timeouts. Adapter lifecycle is recorded but never trusted as SLO proof.
    """
    if (type(execution_budget_ns) is not int
            or not 1 <= execution_budget_ns <= MAX_EXECUTION_BUDGET_NS):
        raise ContractError("INVALID_INPUT")
    plan = validate_query_series_plan(plan, now=int(time.time()))
    if not callable(persist_cell) or not callable(monotonic_clock):
        raise ContractError("INVALID_INPUT")
    plan_ref = content_ref(PLAN_KIND, plan["id"], plan)
    source_ref = _clone(plan["source_ref"])
    cell_rows: list[dict] = []
    stopped = False
    stop_reason = None
    budget_exceeded = False
    timer = _Timer(monotonic_clock)
    started = timer.now()

    def stop(reason: str, *, override: bool = False) -> None:
        nonlocal stopped, stop_reason
        stopped = True
        priority = {
            "CLEANUP_FAILED": 50, "PERSIST_FAILED": 50, "RECEIPT_MISMATCH": 50,
            "CLOCK_UNAVAILABLE_OR_ROLLBACK": 40,
            "CANCELLED_STOP_UNCONFIRMED": 30, "TIMEOUT": 30,
            "SERIES_BUDGET_EXHAUSTED": 20,
        }
        if (stop_reason is None or override
                or priority.get(reason, 10) > priority.get(stop_reason, 10)):
            stop_reason = reason

    def within_budget(sample: int | None) -> bool:
        nonlocal budget_exceeded
        if sample is None or timer.failed or started is None or sample < started:
            stop("CLOCK_UNAVAILABLE_OR_ROLLBACK")
            return False
        if sample - started >= execution_budget_ns:
            budget_exceeded = True
            stop("SERIES_BUDGET_EXHAUSTED")
            return False
        return True

    def not_started_row(cid: str) -> dict:
        return {"cell_id": cid, "status": "NOT_STARTED", "cell_ref": None,
                "planned_observation_count": 108, "attempted_observation_count": 0,
                "not_run_iterations": list(range(1, 109)),
                "observation_count": 0, "warm_p95_ns": None,
                "persisted": False, "persistence_error": None,
                "failure": stop_reason, "cleanup_errors": []}

    if timer.failed:
        stop("CLOCK_UNAVAILABLE_OR_ROLLBACK")
    for surface in SURFACES:
        for cases in CASE_COUNTS:
            for history in HISTORY_COUNTS:
                cid = _cell_id(surface, cases, history)
                if stopped:
                    cell_rows.append(not_started_row(cid))
                    continue
                observations: list[dict] = []
                intervals: list[dict] = []
                cleanup_errors: list[str] = []
                attempted = 0
                cell_start = timer.now()
                cancel = False
                failure = None
                if not within_budget(cell_start):
                    failure = stop_reason

                def record_without_query(iteration: int, start: int | None,
                                         end: int | None, reason: str,
                                         *, phase: str = "cold_open_failed",
                                         warmness: str = "cold") -> None:
                    nonlocal attempted, failure
                    attempted += 1
                    intervals.append({"iteration": iteration, "phase": phase,
                                      "start_ns": start, "end_ns": end,
                                      "wall_ns": _duration(start, end)})
                    observations.append(_obs(plan, cid, source_ref, iteration,
                                             warmness, start, end, reason))
                    failure = reason

                def one(lifecycle: str, phase: str, iteration: int, session=None,
                        *, slot_start: int | None = None) -> bool:
                    nonlocal attempted, cancel, failure
                    dispatch = timer.now()
                    if not within_budget(dispatch):
                        failure = stop_reason
                        return False
                    if slot_start is None:
                        slot_start = dispatch
                    request = {"surface": surface, "case_count": cases,
                               "history_run_count": history,
                               "page_size": plan["payload"]["page_size"],
                               "lifecycle": lifecycle, "phase": phase,
                               "iteration": iteration}
                    error = None
                    try:
                        _query(session, request)
                    except QuerySeriesCancelled:
                        cancel = True
                        error = "CANCELLED_STOP_UNCONFIRMED"
                    except TimeoutError:
                        error = "TIMEOUT"
                    except Exception:
                        error = "TARGET_ERROR"
                    end = timer.now()
                    attempted += 1
                    intervals.append({"iteration": iteration, "phase": phase,
                                      "start_ns": slot_start, "end_ns": end,
                                      "wall_ns": _duration(slot_start, end)})
                    time_ok = within_budget(end)
                    if timer.failed and error is None:
                        error = "MEASUREMENT_ERROR"
                    if error is not None:
                        failure = error
                    elif not time_ok:
                        failure = stop_reason
                    observations.append(_obs(plan, cid, source_ref, iteration, phase,
                                             slot_start, end, error))
                    if cancel:
                        stop("CANCELLED_STOP_UNCONFIRMED")
                    elif error == "TIMEOUT":
                        stop("TIMEOUT")
                    return error is None and time_ok

                def open_session(lifecycle: str):
                    t0 = timer.now()
                    if not within_budget(t0):
                        return None, stop_reason
                    try:
                        session = adapter.open_session(
                            surface=surface, case_count=cases,
                            history_run_count=history,
                            page_size=plan["payload"]["page_size"],
                            lifecycle=lifecycle)
                    except TimeoutError:
                        t1 = timer.now()
                        intervals.append({"phase": "open_" + lifecycle, "start_ns": t0,
                                          "end_ns": t1, "wall_ns": _duration(t0, t1)})
                        within_budget(t1)
                        if not timer.failed:
                            stop("TIMEOUT")
                        return None, "TIMEOUT"
                    except Exception:
                        t1 = timer.now()
                        intervals.append({"phase": "open_" + lifecycle, "start_ns": t0,
                                          "end_ns": t1, "wall_ns": _duration(t0, t1)})
                        if not within_budget(t1):
                            return None, stop_reason
                        return None, "OPEN_FAILED"
                    t1 = timer.now()
                    intervals.append({"phase": "open_" + lifecycle, "start_ns": t0,
                                      "end_ns": t1, "wall_ns": _duration(t0, t1)})
                    if not within_budget(t1):
                        return session, stop_reason
                    return session, None

                def close_session(session) -> None:
                    nonlocal failure
                    if session is None:
                        return
                    t0 = timer.now()
                    close_failed = False
                    try:
                        session.close()
                    except Exception:
                        close_failed = True
                        cleanup_errors.append("CLEANUP_FAILED")
                    t1 = timer.now()
                    intervals.append({"phase": "cleanup_failed" if close_failed else "cleanup",
                                      "start_ns": t0, "end_ns": t1,
                                      "wall_ns": _duration(t0, t1)})
                    time_ok = within_budget(t1)
                    if close_failed:
                        failure = "CLEANUP_FAILED"
                        stop("CLEANUP_FAILED", override=True)
                    elif not time_ok:
                        if failure is None or failure == "TARGET_ERROR":
                            failure = stop_reason
                    if timer.failed and failure is None:
                        failure = "MEASUREMENT_ERROR"

                for iteration in range(1, COLD_COUNT + 1):
                    if stopped:
                        break
                    cold_start = timer.now()
                    if not within_budget(cold_start):
                        failure = stop_reason
                        break
                    session, open_error = open_session("cold")
                    if open_error:
                        if open_error == "TIMEOUT":
                            record_without_query(iteration, cold_start,
                                                 intervals[-1]["end_ns"], "TIMEOUT")
                        elif open_error != "OPEN_FAILED":
                            if open_error == "CLOCK_UNAVAILABLE_OR_ROLLBACK" and session is not None:
                                record_without_query(iteration, cold_start, None,
                                                     "MEASUREMENT_ERROR")
                            failure = open_error
                            close_session(session)
                        else:
                            failure = open_error
                    else:
                        try:
                            one("cold", "cold", iteration, session, slot_start=cold_start)
                        finally:
                            close_session(session)
                    if cancel or cleanup_errors or timer.failed or stopped:
                        break
                if not stopped and not cancel and not cleanup_errors and not timer.failed:
                    warm_start = timer.now()
                    if not within_budget(warm_start):
                        failure = stop_reason
                    else:
                        session, open_error = open_session("warm")
                        if open_error:
                            if open_error == "TIMEOUT":
                                record_without_query(COLD_COUNT + 1, warm_start,
                                    intervals[-1]["end_ns"], "TIMEOUT",
                                    phase="warm_open_failed", warmness="warm")
                            else:
                                failure = open_error
                            close_session(session)
                        else:
                            try:
                                for iteration in range(COLD_COUNT + 1,
                                                       COLD_COUNT + WARMUP_COUNT + WARM_COUNT + 1):
                                    phase = ("warmup" if iteration <= COLD_COUNT + WARMUP_COUNT
                                             else "warm")
                                    one("warm", phase, iteration, session)
                                    if stopped or cancel or timer.failed:
                                        break
                            finally:
                                close_session(session)
                if cleanup_errors:
                    failure = "CLEANUP_FAILED"
                    stop("CLEANUP_FAILED", override=True)
                elif cancel:
                    stop("CANCELLED_STOP_UNCONFIRMED")
                    failure = "CANCELLED_STOP_UNCONFIRMED"
                elif timer.failed:
                    if failure is None:
                        failure = "MEASUREMENT_ERROR"
                    stop("CLOCK_UNAVAILABLE_OR_ROLLBACK")
                elif budget_exceeded and failure is None:
                    failure = "SERIES_BUDGET_EXHAUSTED"
                cell_end = timer.now()
                if not within_budget(cell_end):
                    if failure is None or failure == "TARGET_ERROR":
                        failure = stop_reason
                present = {item["payload"]["iteration"] for item in observations}
                missing = [i for i in range(1, 109) if i not in present]
                status = "COMPLETED" if not failure and not missing else "INCOMPLETE"
                cell = {"schema_version": 1, "kind": CELL_KIND, "id": cid,
                    "plan_ref": plan_ref, "source_ref": source_ref,
                    "surface": surface, "case_count": cases, "history_run_count": history,
                    "page_size": plan["payload"]["page_size"], "status": status,
                    "valid_for_slo": False, "ci_eligible": False,
                    "lifecycle_verified": False,
                    "planned_observation_count": 108, "attempted_observation_count": attempted,
                    "observations": observations, "not_run_iterations": missing,
                    "intervals": intervals, "cell_wall_ns": _duration(cell_start, cell_end),
                    "cold_summary": _summary(observations, "cold"),
                    "warm_summary": _summary(observations, "warm"),
                    "warmup_wall_ns": (sum(x["wall_ns"] for x in intervals if x["phase"] == "warmup")
                                       if all(type(x["wall_ns"]) is int for x in intervals if x["phase"] == "warmup") else None),
                    "cleanup_errors": cleanup_errors,
                    "slo_ineligible_reasons": ["LIFECYCLE_UNPROVEN", "METRICS_INCOMPLETE"]}
                if failure:
                    cell["failure"] = failure
                cell_digest = content_ref(CELL_KIND, cid, cell)
                persist_error = None
                try:
                    handoff = _clone(cell)
                    receipt = persist_cell(handoff)
                    expected = {"schema_version": 1, "kind": RECEIPT_KIND,
                                "series_plan_ref": plan_ref, "cell_ref": cell_digest}
                    if (canonical_bytes(handoff) != canonical_bytes(cell)
                            or type(receipt) is not dict
                            or canonical_bytes(receipt) != canonical_bytes(expected)):
                        persist_error = "RECEIPT_MISMATCH"
                except Exception:
                    persist_error = "PERSIST_FAILED"
                persist_end = timer.now()
                if persist_error:
                    stop(persist_error, override=True)
                within_budget(persist_end)
                cell_rows.append({"cell_id": cid, "status": status, "cell_ref": cell_digest,
                    "planned_observation_count": 108, "attempted_observation_count": attempted,
                    "not_run_iterations": missing, "observation_count": len(observations),
                    "warm_p95_ns": cell["warm_summary"]["p95_ns"],
                    "persisted": persist_error is None,
                    "persistence_error": persist_error, "failure": failure,
                    "cleanup_errors": cleanup_errors})
                if persist_error:
                    cell_rows[-1]["cell_ref"] = None
    end_all = timer.now()
    within_budget(end_all)
    elapsed = _duration(started, end_all)
    overrun = None if elapsed is None else max(0, elapsed - execution_budget_ns)
    stored = [row["cell_ref"] for row in cell_rows if row["persisted"]]
    all_cells_complete = all(row["status"] == "COMPLETED" and row["persisted"] for row in cell_rows)
    aggregate = {"schema_version": 1, "kind": RESULT_KIND,
        "id": "qs-" + hashlib.sha256(plan["id"].encode("utf-8")).hexdigest()[:32],
        "plan_ref": plan_ref,
        "status": "COMPLETED" if all_cells_complete and not stopped else "INCOMPLETE",
        "planned_cell_count": 27, "stored_cell_count": len(stored),
        "missing_cell_count": 27-len(stored),
        "planned_observation_count": 27 * 108,
        "attempted_observation_count": sum(row["attempted_observation_count"] for row in cell_rows),
        "not_run_observation_count": sum(len(row["not_run_iterations"]) for row in cell_rows),
        "observation_count": sum(row["observation_count"] for row in cell_rows if row["persisted"]),
        "cells": cell_rows, "ordered_cell_refs": stored,
        "ordered_cell_refs_digest": hashlib.sha256(canonical_bytes(stored)).hexdigest(),
        "series_wall_ns": elapsed, "execution_budget_ns": execution_budget_ns,
        "execution_budget_exceeded": (True if budget_exceeded else None if timer.failed else False),
        "execution_overrun_ns": overrun, "stop_reason": stop_reason,
        "ratios": _ratios(cell_rows), "valid_for_slo": False, "ci_eligible": False,
        "slo_ineligible_reasons": ["LIFECYCLE_UNPROVEN", "PROCESS_METRICS_UNAVAILABLE"]}
    return aggregate


def _ratios(rows: list[dict]) -> list[dict]:
    refs = {(row["cell_id"].split("-")[1], int(row["cell_id"].split("-")[2]),
             int(row["cell_id"].split("-")[3])): row for row in rows}
    result = []
    for surface in SURFACES:
        for history in HISTORY_COUNTS:
            for small, large in zip(CASE_COUNTS, CASE_COUNTS[1:]):
                a = refs.get((surface, small, history), {})
                b = refs.get((surface, large, history), {})
                pa = a.get("warm_p95_ns")
                pb = b.get("warm_p95_ns")
                value = ratio(pb, pa) if (a.get("status") == "COMPLETED" and b.get("status") == "COMPLETED"
                                          and a.get("persisted") is True and b.get("persisted") is True
                                          and type(pb) is int and type(pa) is int) else None
                result.append({"surface": surface, "history_run_count": history,
                    "from_case_count": small, "to_case_count": large,
                    "ratio": None if value is None else fraction_wire(value)})
    return result
