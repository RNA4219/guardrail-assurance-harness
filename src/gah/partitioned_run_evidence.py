from __future__ import annotations

from copy import deepcopy
import json
import sqlite3
from typing import Any, Callable

from . import execution_profiles
from .contracts import ContractError, MAX_INTEGER
from .partitioned_aggregation import aggregate_partitioned
from .partitioned_run_contracts import bind_partitioned_run_manifest, validate_partitioned_run_manifest
from .partitioned_trial_plan import MAX_ARTIFACT_BYTES, MAX_SEGMENTS, restore_trial_plan, _canonical as _partitioned_canonical
from .run_evidence import (
    EvidenceError, RunEvidenceBook, _error, _load, _pack, _profile,
)
from .run_contracts import validate_evaluation_contract
from .policy import validate_policy_profile
from .registry import validate_registry
from .corpus import validate_case_set

_CONTEXT_FIELDS = {
    "manifest", "contract", "index", "segments", "policy", "registry",
    "case_set", "execution_profile", "baseline_context",
}
_RECEIPT_FIELDS = {
    "schema_version", "kind", "manifest_ref", "contract_ref", "plan_index_ref",
    "policy_ref", "registry_ref", "case_set_ref", "selected_controls", "ci_eligible",
}


def _fail(code: str = "PARTITIONED_CONTEXT_INVALID") -> EvidenceError:
    return _error(code)


class PartitionedRunEvidenceBook(RunEvidenceBook):
    """Ref-only v2 evidence book using existing run-evidence tables and state machine.

    `resolve_context` is called inside the caller-owned SQLite transaction on every
    run operation. It must freshly authorize/read the current adopted gen1 diagnostic
    source, permission and committed plan context. This book itself does not provide
    OS authorization, source locks, currentness, admission, or CI eligibility.
    """

    def __init__(self, db: sqlite3.Connection, *, now: int,
                 allowed_bindings: dict[str, str],
                 resolve_context: Callable[[sqlite3.Connection, int, dict[str, Any], dict[str, Any]], dict[str, Any]]):
        if not callable(resolve_context):
            raise _fail("RESOLVER_REQUIRED")
        super().__init__(db, now=now, allowed_bindings=allowed_bindings)
        self._resolve_partitioned_context = resolve_context

    @staticmethod
    def _receipt(raw: Any, digest: Any) -> dict[str, Any]:
        value = _load(raw, digest)
        if (type(value) is not dict or set(value) != _RECEIPT_FIELDS
                or type(value.get("schema_version")) is not int or value["schema_version"] != 2
                or value.get("kind") != "bound_partitioned_run"
                or value.get("ci_eligible") is not False):
            raise _fail("STORAGE_CORRUPT")
        return value

    def _resolve_fresh(self, db: sqlite3.Connection, now: int, receipt: dict[str, Any],
                       stored_profile: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], Any]:
        if db is not self._db or not db.in_transaction:
            raise _fail("TRANSACTION_REQUIRED")
        try:
            supplied = self._resolve_partitioned_context(
                db, now, deepcopy(receipt), deepcopy(stored_profile)
            )
            if type(supplied) is not dict or set(supplied) != _CONTEXT_FIELDS:
                raise _fail()
            if supplied["baseline_context"] is not None:
                raise _fail("BASELINE_UNSUPPORTED")
            segments = supplied["segments"]
            if type(segments) not in (list, tuple) or not 1 <= len(segments) <= MAX_SEGMENTS:
                raise _fail()
            # Bound the snapshot before any segment/index canonicalization.
            segment_items = tuple(segments[:MAX_SEGMENTS + 1])
            if len(segment_items) != len(segments) or not 1 <= len(segment_items) <= MAX_SEGMENTS:
                raise _fail()
            manifest = validate_partitioned_run_manifest(supplied["manifest"])
            contract = validate_evaluation_contract(supplied["contract"])
            policy = validate_policy_profile(supplied["policy"])
            registry = validate_registry(supplied["registry"])
            case_set = validate_case_set(supplied["case_set"])
            index = json.loads(_partitioned_canonical(supplied["index"], maximum=MAX_ARTIFACT_BYTES))
            segment_snapshot = tuple(
                json.loads(_partitioned_canonical(item, maximum=MAX_ARTIFACT_BYTES)) for item in segment_items
            )
            profile = _profile(supplied["execution_profile"])
            if profile != stored_profile:
                raise _fail("PROFILE_MISMATCH")
            if (manifest["purpose"] != "diagnostic" or manifest["baseline_ref"] is not None
                    or contract["generation"] != 1
                    or contract["comparison"]["mode"] != "not_applicable"):
                raise _fail("SCOPE_UNSUPPORTED")
            bound_refs = bind_partitioned_run_manifest(
                manifest, contract, index, segment_snapshot, policy, registry, case_set,
                baseline_context=None,
            )
            if bound_refs != receipt:
                raise _fail("BINDING_MISMATCH")
            plan = restore_trial_plan(index, segment_snapshot)
            internal = {
                "manifest": manifest, "contract": contract, "plan": plan,
                "policy": policy, "registry": registry, "case_set": case_set,
                "selected_controls": deepcopy(bound_refs["selected_controls"]),
                "ci_eligible": False,
                "_partitioned_context": {
                    "manifest": manifest, "contract": contract, "index": index,
                    "segments": segment_snapshot, "policy": policy, "registry": registry,
                    "case_set": case_set,
                },
                "_partitioned_receipt": deepcopy(bound_refs),
            }
            execution_profiles.check_plan(profile, internal)
            return internal, profile, None
        except EvidenceError:
            raise
        except (ContractError, TypeError, ValueError, KeyError, IndexError, RecursionError, sqlite3.Error):
            raise _fail() from None
        except Exception:
            # Resolver expiry/currentness failures are deliberately fixed and fail closed.
            raise _fail("CURRENTNESS_UNAVAILABLE") from None

    def _resolve_run_context(self, db: sqlite3.Connection, row: sqlite3.Row,
                             now: int) -> tuple[dict[str, Any], dict[str, Any], Any]:
        receipt = self._receipt(row["bundle_json"], row["bundle_digest"])
        profile = _load(row["profile_json"], row["profile_digest"])
        if row["baseline_json"] is not None or row["baseline_digest"] is not None:
            raise _fail("BASELINE_UNSUPPORTED")
        return self._resolve_fresh(db, now, receipt, profile)

    def _binding_summary(self, bound: dict[str, Any], profile: dict[str, Any],
                         baseline: Any) -> dict[str, Any]:
        receipt = bound.get("_partitioned_receipt") if type(bound) is dict else None
        if receipt is None and type(bound) is dict and set(bound) == _RECEIPT_FIELDS:
            receipt = bound
        if type(receipt) is not dict or baseline is not None:
            raise _fail("STORAGE_CORRUPT")
        return {
            "run_id": receipt["manifest_ref"]["id"],
            "manifest_digest": receipt["manifest_ref"]["digest"],
            "contract_digest": receipt["contract_ref"]["digest"],
            "plan_index_digest": receipt["plan_index_ref"]["digest"],
            "profile_digest": _pack(profile)[1],
            "baseline_digest": None,
        }

    def _aggregate_value(self, bound: dict[str, Any], attempts: list[dict[str, Any]],
                         profile: dict[str, Any], baseline: Any) -> dict[str, Any]:
        context = bound.get("_partitioned_context")
        if type(context) is not dict or baseline is not None:
            raise _fail("STORAGE_CORRUPT")
        try:
            return aggregate_partitioned(
                context["manifest"], context["contract"], context["index"],
                context["segments"], context["policy"], context["registry"],
                context["case_set"], attempts, execution_profile=profile,
                baseline_context=None,
            )
        except EvidenceError:
            raise
        except (ContractError, TypeError, ValueError, KeyError, IndexError, RecursionError):
            raise _fail("AGGREGATION_INVALID") from None

    def _aggregate_kind(self) -> str:
        return "partitioned_aggregation"

    def _check_context(self, db: sqlite3.Connection, row: sqlite3.Row, now: int,
                       bound: dict[str, Any], profile: dict[str, Any], baseline: Any) -> None:
        receipt = self._receipt(row["bundle_json"], row["bundle_digest"])
        fresh_bound, fresh_profile, fresh_baseline = self._resolve_fresh(db, now, receipt, profile)
        if (fresh_bound["_partitioned_receipt"] != bound.get("_partitioned_receipt")
                or fresh_profile != profile or fresh_baseline is not baseline):
            raise _fail("BINDING_MISMATCH")

    def _run_view(self, db: sqlite3.Connection, run_id: str, now: int) -> dict[str, Any]:
        row = self._run_row(db, run_id)
        bound, profile, baseline = self._resolve_run_context(db, row, now)
        state = self._state(db, run_id)
        return {
            "schema_version": 2, "kind": "partitioned_run_evidence_run", "run_id": run_id,
            "bundle": deepcopy(bound["_partitioned_receipt"]),
            "bundle_digest": row["bundle_digest"],
            "execution_profile": profile, "baseline_context": None,
            "state": state["state"], "hold_reason": state["hold_reason"],
            "aggregate_digest": state["aggregate_digest"], "decision_digest": state["decision_digest"],
            "diagnostic_finalized": state["finalized_at"] is not None,
            "diagnostic_finalized_at": state["finalized_at"],
            "authority_connected": False, "resource_closure_verified": False,
            "baseline_freshness_verified": False, "adoption_verified": False,
            "ci_eligible": False,
        }

    def start_run(self, bound_run: Any, execution_profile: Any, baseline_context: Any = None) -> dict[str, Any]:
        """V1 entry is intentionally unavailable on a v2-only book."""
        raise _fail("PARTITIONED_ENTRY_REQUIRED")

    def start_partitioned_run(self, receipt: Any, execution_profile: Any) -> dict[str, Any]:
        if type(receipt) is not dict or set(receipt) != _RECEIPT_FIELDS:
            raise _fail("INVALID_BINDING")
        profile = _profile(execution_profile)
        run_id = receipt.get("manifest_ref", {}).get("id") if type(receipt.get("manifest_ref")) is dict else None
        try:
            from .contracts import require_id
            require_id(run_id)
            receipt_raw, receipt_digest = _pack(receipt)
            profile_raw, profile_digest = _pack(profile)
        except (ContractError, TypeError, ValueError, KeyError):
            raise _fail("INVALID_BINDING") from None
        if self._allowed.get(run_id) != receipt_digest:
            raise _fail("BINDING_NOT_ADMITTED")
        with self._transaction() as db:
            now = self._now(db)
            self._resolve_fresh(db, now, receipt, profile)
            existing = db.execute("SELECT * FROM bound_runs WHERE run_id=?", (run_id,)).fetchone()
            if existing is not None:
                if existing["bundle_digest"] != receipt_digest or existing["profile_digest"] != profile_digest:
                    raise _fail("RUN_CONFLICT")
                view = self._run_view(db, run_id, now)
                view["binding"] = self._binding_summary(self._resolve_run_context(db, existing, now)[0], profile, None)
                return view
            db.execute(
                "INSERT INTO bound_runs VALUES(?,?,?,?,?,?,?,?,?)",
                (run_id, receipt_raw, receipt_digest, profile_raw, profile_digest,
                 None, None, now, now),
            )
            db.execute(
                "INSERT INTO run_state VALUES(?,?,?,?,?,?,?,?,?,?)",
                (run_id, "OPEN", None, None, None, None, "UNKNOWN", now, None, 0),
            )
            view = self._run_view(db, run_id, now)
            view["binding"] = self._binding_summary(self._resolve_run_context(
                db, self._run_row(db, run_id), now
            )[0], profile, None)
            return view


    def get_attempt(self, attempt_id: str) -> dict[str, Any]:
        from .run_evidence import _id
        _id(attempt_id)
        with self._transaction() as db:
            now = self._now(db)
            row = db.execute("SELECT * FROM attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
            if row is None:
                raise _fail("ATTEMPT_NOT_FOUND")
            run_row = self._run_row(db, row["run_id"])
            self._resolve_run_context(db, run_row, now)
            return {"schema_version": 2, "kind": "partitioned_attempt_record",
                    "attempt": _load(row["attempt_json"], row["attempt_digest"]),
                    "attempt_digest": row["attempt_digest"], "delivery_count": row["delivery_count"],
                    "ci_eligible": False}

    def get_terminal(self, run_id: str) -> dict[str, Any]:
        from .run_evidence import _id
        _id(run_id)
        with self._transaction() as db:
            now = self._now(db)
            run_row = self._run_row(db, run_id)
            bound, profile, baseline = self._resolve_run_context(db, run_row, now)
            row = db.execute("SELECT * FROM terminals WHERE run_id=?", (run_id,)).fetchone()
            if row is None:
                raise _fail("NOT_FINALIZED")
            value = _load(row["terminal_json"], row["terminal_digest"])
            if value.get("binding") != self._binding_summary(bound, profile, baseline):
                raise _fail("STORAGE_CORRUPT")
            return deepcopy(value)


__all__ = ["PartitionedRunEvidenceBook"]
