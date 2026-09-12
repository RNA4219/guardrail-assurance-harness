"""管理DBの同じtransactionで全資源を予約し、停止と使用量を別に観測する。"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from .contracts import ContractError, MAX_INTEGER, require_id, require_digest, require_object, require_ref, require_uint
from .ledger import Ledger, LedgerError
from .policy import validate_policy_profile
from .wire import canonical_bytes

TABLES = {
    "resource_meta": {"key", "value"},
    "resource_runs": {"run_id", "manifest_digest", "policy_json", "policy_digest", "profile", "created_at", "deadline",
                      "owner_id", "owner_epoch", "lease_until", "cancelled", "breached", "closed_at"},
    "resource_operations": {"operation_id", "run_id", "owner_epoch", "reservation_json", "reservation_digest",
                            "intended_at", "stopped_at", "released", "usage_json", "usage_digest", "settled_at",
                            "cost_micros", "conflicted", "exposure_micros"},
    "resource_events": {"event_id", "operation_id", "event_digest", "response_json", "response_digest"},
}
RESERVATION_FIELDS = {"case_trial_executions", "model_calls", "input_tokens", "output_tokens", "api_cost_usd_micros", "billing_ref", "billing_mode"}
COUNTERS = ("case_trial_executions", "model_calls", "input_tokens", "output_tokens", "api_cost_usd_micros")


class ResourceError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _packed(value):
    raw = canonical_bytes(value)
    return raw.decode("utf-8"), hashlib.sha256(raw).hexdigest()


def _unpack(raw, digest):
    if type(raw) is not str or type(digest) is not str:
        raise ResourceError("STORAGE_CORRUPT")
    try:
        value = json.loads(raw)
        expected, actual = _packed(value)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ResourceError("STORAGE_CORRUPT") from None
    if expected != raw or actual != digest:
        raise ResourceError("STORAGE_CORRUPT")
    return value


def create_schema(db):
    if not db.in_transaction:
        raise ResourceError("TRANSACTION_REQUIRED")
    db.execute("CREATE TABLE resource_meta(key TEXT PRIMARY KEY, value INTEGER NOT NULL)")
    db.execute("INSERT INTO resource_meta VALUES('last_clock',-1)")
    db.execute("""CREATE TABLE resource_runs(run_id TEXT PRIMARY KEY, manifest_digest TEXT NOT NULL,
        policy_json TEXT NOT NULL, policy_digest TEXT NOT NULL, profile TEXT NOT NULL, created_at INTEGER NOT NULL,
        deadline INTEGER NOT NULL, owner_id TEXT NOT NULL, owner_epoch INTEGER NOT NULL, lease_until INTEGER NOT NULL,
        cancelled INTEGER NOT NULL, breached INTEGER NOT NULL, closed_at INTEGER)""")
    db.execute("""CREATE TABLE resource_operations(operation_id TEXT PRIMARY KEY, run_id TEXT NOT NULL,
        owner_epoch INTEGER NOT NULL, reservation_json TEXT NOT NULL, reservation_digest TEXT NOT NULL,
        intended_at INTEGER, stopped_at INTEGER, released INTEGER NOT NULL, usage_json TEXT, usage_digest TEXT,
        settled_at INTEGER, cost_micros INTEGER, conflicted INTEGER NOT NULL, exposure_micros INTEGER NOT NULL,
        FOREIGN KEY(run_id) REFERENCES resource_runs(run_id))""")
    db.execute("""CREATE TABLE resource_events(event_id TEXT PRIMARY KEY, operation_id TEXT NOT NULL,
        event_digest TEXT NOT NULL, response_json TEXT NOT NULL, response_digest TEXT NOT NULL,
        FOREIGN KEY(operation_id) REFERENCES resource_operations(operation_id))""")


class ResourceBook:
    """認証とBEGIN IMMEDIATEは呼出側が担当。現在時刻はbrokerからだけ渡す。"""

    def __init__(self, db: sqlite3.Connection):
        self.db = db

    def _touch(self, now):
        if not self.db.in_transaction:
            raise ResourceError("TRANSACTION_REQUIRED")
        require_uint(now)
        row = self.db.execute("SELECT value FROM resource_meta WHERE key='last_clock'").fetchone()
        if row is None or type(row[0]) is not int or not -1 <= row[0] <= MAX_INTEGER:
            raise ResourceError("STORAGE_CORRUPT")
        if now < row[0]:
            raise ResourceError("CLOCK_ROLLBACK")
        self.db.execute("UPDATE resource_meta SET value=? WHERE key='last_clock'", (now,))

    def _run(self, run_id):
        require_id(run_id)
        row = self.db.execute("SELECT * FROM resource_runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            raise ResourceError("RUN_MISSING")
        return row

    def _operation(self, run_id, operation_id):
        require_id(operation_id)
        row = self.db.execute("SELECT * FROM resource_operations WHERE operation_id=?", (operation_id,)).fetchone()
        if row is None or row["run_id"] != run_id:
            raise ResourceError("OPERATION_MISSING")
        return row

    @staticmethod
    def _owner(run, owner_id, epoch, now, *, start=False):
        require_id(owner_id)
        require_uint(epoch)
        if run["owner_id"] != owner_id or run["owner_epoch"] != epoch or now >= run["lease_until"]:
            raise ResourceError("OWNER_STALE")
        if start and run["closed_at"] is not None:
            raise ResourceError("RUN_CLOSED")
        if start and (now >= run["deadline"] or run["cancelled"] or run["breached"]):
            raise ResourceError("START_DENIED")

    @staticmethod
    def _policy(run):
        return validate_policy_profile(_unpack(run["policy_json"], run["policy_digest"]))

    def create_run(self, run_id, manifest_digest, policy, profile, owner_id, now, deadline):
        self._touch(now)
        require_id(run_id)
        require_digest(manifest_digest)
        require_id(owner_id)
        require_uint(deadline)
        policy = validate_policy_profile(policy)
        if type(profile) is not str or profile not in {"pr", "full"} or not now < deadline <= min(MAX_INTEGER, now + policy["profiles"][profile]["elapsed_seconds"]):
            raise ResourceError("INVALID_DEADLINE")
        if self.db.execute("SELECT 1 FROM resource_runs WHERE run_id=?", (run_id,)).fetchone():
            raise ResourceError("RUN_EXISTS")
        raw, digest = _packed(policy)
        self.db.execute("INSERT INTO resource_runs VALUES(?,?,?,?,?,?,?,?,?,?,0,0,NULL)",
            (run_id, manifest_digest, raw, digest, profile, now, deadline, owner_id, 1, min(MAX_INTEGER, now + 60)))
        return self.snapshot(run_id, now)

    def claim(self, run_id, owner_id, now, *, recovery=False):
        self._touch(now)
        require_id(owner_id)
        run = self._run(run_id)
        if type(recovery) is not bool:
            raise ResourceError("INVALID_RECOVERY_MODE")
        if run["closed_at"] is not None:
            raise ResourceError("RUN_CLOSED")
        start_denied = now >= run["deadline"] or bool(run["cancelled"]) or bool(run["breached"])
        if start_denied and not recovery:
            raise ResourceError("START_DENIED")
        if recovery and not start_denied:
            raise ResourceError("RECOVERY_NOT_REQUIRED")
        if now < run["lease_until"] and owner_id != run["owner_id"]:
            raise ResourceError("OWNER_ACTIVE")
        epoch = run["owner_epoch"] + int(now >= run["lease_until"])
        if epoch > MAX_INTEGER or now > MAX_INTEGER - 60:
            raise ResourceError("GENERATION_EXHAUSTED")
        self.db.execute("UPDATE resource_runs SET owner_id=?,owner_epoch=?,lease_until=? WHERE run_id=?", (owner_id, epoch, now + 60, run_id))
        return {"owner_id": owner_id, "owner_epoch": epoch, "lease_until": now + 60, "recovery_only": recovery}

    def claim_recovery(self, run_id, owner_id, now):
        return self.claim(run_id, owner_id, now, recovery=True)

    @staticmethod
    def _reservation(value, policy):
        require_object(value, RESERVATION_FIELDS)
        for field in COUNTERS:
            require_uint(value[field])
        require_ref(value["billing_ref"])
        if value["billing_ref"]["kind"] != "billing_basis" or type(value["billing_mode"]) is not str or value["billing_mode"] not in {"metered", "non_billed_local"}:
            raise ResourceError("BILLING_BASIS_REQUIRED")
        if ((value["api_cost_usd_micros"] == 0) != (value["billing_mode"] == "non_billed_local")
                or value["case_trial_executions"] > 1 or value["model_calls"] > 1):
            raise ResourceError("INVALID_RESERVATION")
        if value["input_tokens"] > policy["per_call"]["input_tokens_max"] or value["output_tokens"] > policy["per_call"]["output_tokens_max"]:
            raise ResourceError("PER_CALL_LIMIT")
        if value["model_calls"] == 0 and (value["input_tokens"] or value["output_tokens"]):
            raise ResourceError("INVALID_RESERVATION")
        return dict(value)

    def _totals(self, run_id, now):
        totals = {field: 0 for field in COUNTERS}
        totals.update({"slots": 0, "unsettled": 0, "global_api_cost_usd_micros": 0})
        for op in self.db.execute("SELECT * FROM resource_operations"):
            reservation = _unpack(op["reservation_json"], op["reservation_digest"])
            usage = None if op["usage_json"] is None else _unpack(op["usage_json"], op["usage_digest"])
            settled = usage is not None and not op["conflicted"]
            cost = op["cost_micros"] if settled else max(reservation["api_cost_usd_micros"], op["exposure_micros"])
            if not op["released"] and (not settled or op["settled_at"] > now - 86400):
                totals["global_api_cost_usd_micros"] += cost
            if op["run_id"] != run_id or op["released"]:
                continue
            totals["slots"] += int(op["stopped_at"] is None)
            totals["unsettled"] += int(not settled)
            totals["case_trial_executions"] += reservation["case_trial_executions"]
            totals["model_calls"] += reservation["model_calls"]
            for field in ("input_tokens", "output_tokens"):
                totals[field] += usage[field] if settled else max(reservation[field], 0 if usage is None else usage[field])
            totals["api_cost_usd_micros"] += cost
        totals["total_tokens"] = totals["input_tokens"] + totals["output_tokens"]
        return totals

    def reserve(self, run_id, operation_id, owner_id, epoch, reservation, now):
        self._touch(now)
        require_id(operation_id)
        run = self._run(run_id)
        self._owner(run, owner_id, epoch, now, start=True)
        policy = self._policy(run)
        reservation = self._reservation(reservation, policy)
        raw, digest = _packed(reservation)
        existing = self.db.execute("SELECT * FROM resource_operations WHERE operation_id=?", (operation_id,)).fetchone()
        if existing is not None:
            if existing["run_id"] != run_id or existing["reservation_digest"] != digest or existing["owner_epoch"] != epoch:
                raise ResourceError("OPERATION_CONFLICT")
            return {"operation_id": operation_id, "existing": True, "dispatch_allowed": existing["intended_at"] is None and not existing["released"]}
        used = self._totals(run_id, now)
        limits = policy["profiles"][run["profile"]]
        if used["slots"] >= limits["concurrent_evaluations"]:
            raise ResourceError("CONCURRENCY_LIMIT")
        for field in ("case_trial_executions", "model_calls", "api_cost_usd_micros"):
            if used[field] + reservation[field] > limits[field]:
                raise ResourceError("RESOURCE_LIMIT")
        if used["total_tokens"] + reservation["input_tokens"] + reservation["output_tokens"] > limits["total_tokens"]:
            raise ResourceError("RESOURCE_LIMIT")
        if used["global_api_cost_usd_micros"] + reservation["api_cost_usd_micros"] > policy["global_api_budget"]["usd_micros_max"]:
            raise ResourceError("GLOBAL_COST_LIMIT")
        self.db.execute("INSERT INTO resource_operations VALUES(?,?,?,?,?,NULL,NULL,0,NULL,NULL,NULL,NULL,0,0)", (operation_id, run_id, epoch, raw, digest))
        return {"operation_id": operation_id, "existing": False, "dispatch_allowed": True}

    def dispatch_intent(self, run_id, operation_id, owner_id, epoch, now):
        self._touch(now)
        run = self._run(run_id)
        self._owner(run, owner_id, epoch, now, start=True)
        op = self._operation(run_id, operation_id)
        if op["owner_epoch"] != epoch or op["intended_at"] is not None or op["released"]:
            raise ResourceError("DISPATCH_NOT_PROVABLY_NEW")
        self.db.execute("UPDATE resource_operations SET intended_at=? WHERE operation_id=?", (now, operation_id))
        return {"operation_id": operation_id, "state": "DISPATCHING", "owner_epoch": epoch}

    def cancel(self, run_id, owner_id, epoch, now, *, terminal_pending=False):
        self._touch(now)
        run = self._run(run_id)
        if type(terminal_pending) is not bool:
            raise ResourceError("INVALID_REQUEST")
        if run["closed_at"] is not None and not terminal_pending:
            raise ResourceError("RUN_CLOSED")
        self._owner(run, owner_id, epoch, now)
        self.db.execute("UPDATE resource_runs SET cancelled=1 WHERE run_id=?", (run_id,))
        self.db.execute("UPDATE resource_operations SET released=1 WHERE run_id=? AND intended_at IS NULL", (run_id,))
        return self.snapshot(run_id, now)

    def close(self, run_id, owner_id, epoch, now):
        """会計が完了したrunを閉鎖し、以後の新規開始だけを禁止する。"""
        self._touch(now)
        run = self._run(run_id)
        self._owner(run, owner_id, epoch, now)
        if run["closed_at"] is not None:
            return self.snapshot(run_id, now)
        totals = self._totals(run_id, now)
        if totals["slots"] != 0 or totals["unsettled"] != 0:
            raise ResourceError("CLOSE_DENIED")
        self.db.execute("UPDATE resource_runs SET closed_at=? WHERE run_id=?", (now, run_id))
        return self.snapshot(run_id, now)

    def observe(self, run_id, operation_id, event_id, *, stopped, usage, now):
        """認証済みobserver専用。旧ownerの処理も同じoperationへ一度だけ精算する。"""
        self._touch(now)
        require_id(event_id)
        run = self._run(run_id)
        op = self._operation(run_id, operation_id)
        if type(stopped) is not bool or not stopped and usage is None or op["intended_at"] is None or op["released"]:
            raise ResourceError("INVALID_OBSERVATION")
        reservation = _unpack(op["reservation_json"], op["reservation_digest"])
        cost = None
        if usage is not None:
            require_object(usage, {"input_tokens", "output_tokens", "cost_usd"})
            require_uint(usage["input_tokens"])
            require_uint(usage["output_tokens"])
            try:
                _, cost = Ledger._parse_usd(usage["cost_usd"])
            except LedgerError:
                raise ResourceError("INVALID_AMOUNT") from None
            require_uint(cost)
            if cost == 0 and reservation["billing_mode"] != "non_billed_local":
                raise ResourceError("NON_BILLING_NOT_CONFIRMED")
        event = {"run_id": run_id, "operation_id": operation_id, "stopped": stopped, "usage": usage}
        _, event_digest = _packed(event)
        previous = self.db.execute("SELECT * FROM resource_events WHERE event_id=?", (event_id,)).fetchone()
        if previous is not None:
            if previous["operation_id"] != operation_id or previous["event_digest"] != event_digest:
                # 別operationへのevent ID再利用も、関係する両runの現在利用を止める。
                for affected in {operation_id, previous["operation_id"]}:
                    row = self.db.execute("SELECT * FROM resource_operations WHERE operation_id=?", (affected,)).fetchone()
                    if row is None:
                        raise ResourceError("STORAGE_CORRUPT")
                    prior_reservation = _unpack(row["reservation_json"], row["reservation_digest"])
                    self.db.execute("UPDATE resource_runs SET breached=1 WHERE run_id=?", (row["run_id"],))
                    self.db.execute("UPDATE resource_operations SET conflicted=1,exposure_micros=? WHERE operation_id=?",
                        (max(prior_reservation["api_cost_usd_micros"], row["exposure_micros"], cost or 0, row["cost_micros"] or 0), affected))
                return {"operation_id": operation_id, "accepted": False, "conflict": True,
                        "reason": "EVENT_CONFLICT", "ci_eligible": False}
            return _unpack(previous["response_json"], previous["response_digest"])
        conflict = bool(op["conflicted"])
        overrun = now > run["deadline"]
        if usage is not None:
            if op["usage_json"] is not None:
                old = _unpack(op["usage_json"], op["usage_digest"])
                conflict = conflict or old["input_tokens"] != usage["input_tokens"] or old["output_tokens"] != usage["output_tokens"] or not Ledger._decimal_equal(old["cost_usd"], usage["cost_usd"])
            else:
                raw, digest = _packed(usage)
                self.db.execute("UPDATE resource_operations SET usage_json=?,usage_digest=?,settled_at=?,cost_micros=? WHERE operation_id=?", (raw, digest, now, cost, operation_id))
            overrun = overrun or usage["input_tokens"] > reservation["input_tokens"] or usage["output_tokens"] > reservation["output_tokens"] or cost > reservation["api_cost_usd_micros"]
        if stopped:
            self.db.execute("UPDATE resource_operations SET stopped_at=COALESCE(stopped_at,?) WHERE operation_id=?", (now, operation_id))
        if conflict:
            self.db.execute("UPDATE resource_operations SET conflicted=1,exposure_micros=? WHERE operation_id=?", (max(reservation["api_cost_usd_micros"], op["exposure_micros"], cost or 0, op["cost_micros"] or 0), operation_id))
        if conflict or overrun:
            self.db.execute("UPDATE resource_runs SET breached=1 WHERE run_id=?", (run_id,))
        result = {"operation_id": operation_id, "accepted": not conflict, "conflict": conflict, "overrun": overrun,
                  "owner_epoch_at_dispatch": op["owner_epoch"], "current_owner_epoch": run["owner_epoch"], "ci_eligible": False}
        raw, digest = _packed(result)
        self.db.execute("INSERT INTO resource_events VALUES(?,?,?,?,?)", (event_id, operation_id, event_digest, raw, digest))
        return result

    def snapshot(self, run_id, now):
        self._touch(now)
        run = self._run(run_id)
        totals = self._totals(run_id, now)
        return {"run_id": run_id, "manifest_digest": run["manifest_digest"], "owner_id": run["owner_id"],
            "owner_epoch": run["owner_epoch"], "deadline": run["deadline"], "cancelled": bool(run["cancelled"]),
            "breached": bool(run["breached"]), "closed": run["closed_at"] is not None,
            "closed_at": run["closed_at"], "resources": totals,
            "budget_closure": totals["slots"] == 0 and totals["unsettled"] == 0 and not run["breached"],
            "ci_eligible": False}
