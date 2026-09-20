"""Ref-only evidence storage adapter for explicitly selected partitioned runs.

This adapter reuses RunEvidenceBook's Attempt/Decision state machine. The
trusted context resolver remains responsible for authorization, source locks,
adoption/currentness and fresh baseline resolution on every operation.
"""
from __future__ import annotations

import json
import sqlite3
from copy import deepcopy
from typing import Any, Callable

from . import execution_profiles
from .contracts import ContractError
from .partitioned_aggregation import aggregate_partitioned
from .partitioned_run_contracts import validate_partitioned_runtime
from .run_evidence import (
    EvidenceError, RunEvidenceBook, _binding_summary, _error, _load, _pack, _profile,
    bound_bundle_digest, bound_storage_document,
)

_RECEIPT_FIELDS = {
    "schema_version", "kind", "manifest_ref", "contract_ref", "plan_index_ref",
    "policy_ref", "registry_ref", "case_set_ref", "selected_controls", "ci_eligible",
}


class PartitionedNormalEvidenceBook(RunEvidenceBook):
    """Normal-purpose partitioned evidence; it never grants CI eligibility."""

    def __init__(self, db: sqlite3.Connection, *, now: int,
                 allowed_bindings: dict[str, str],
                 context_resolver: Callable[[sqlite3.Connection, int, dict[str, Any], dict[str, Any]], tuple[Any, Any, Any]]):
        if not callable(context_resolver):
            raise _error("RESOLVER_REQUIRED")
        super().__init__(db, now=now, allowed_bindings=allowed_bindings)
        self._context_resolver = context_resolver

    @staticmethod
    def _receipt(raw: Any, digest: Any) -> dict[str, Any]:
        value = _load(raw, digest)
        if (type(value) is not dict or set(value) != _RECEIPT_FIELDS
                or type(value.get("schema_version")) is not int or value["schema_version"] != 2
                or value.get("kind") != "bound_partitioned_run"
                or value.get("ci_eligible") is not False):
            raise _error("STORAGE_CORRUPT")
        return value

    @staticmethod
    def _normal_purpose(bound: dict[str, Any], baseline: Any) -> None:
        manifest = bound["manifest"]
        contract = bound["contract"]
        generation = contract["generation"]
        purpose = manifest["purpose"]
        comparison = contract["comparison"]
        if type(generation) is not int:
            raise _error("SCOPE_UNSUPPORTED")
        if generation == 1:
            valid = (purpose in {"baseline_candidate", "contract_old_regression"} and manifest["baseline_ref"] is None
                     and comparison["mode"] == "not_applicable" and baseline is None)
        else:
            valid = (generation >= 2
                     and purpose in {"regression", "contract_old_regression", "contract_candidate"}
                     and comparison["mode"] == "required"
                     and manifest["baseline_ref"] == comparison["baseline_ref"]
                     and baseline is not None)
        if not valid:
            raise _error("SCOPE_UNSUPPORTED")

    def _resolve_fresh(self, db: sqlite3.Connection, now: int, receipt: dict[str, Any],
                       stored_profile: dict[str, Any], stored_baseline: Any) -> tuple[dict[str, Any], dict[str, Any], Any]:
        if db is not self._db or not db.in_transaction:
            raise _error("TRANSACTION_REQUIRED")
        try:
            supplied = self._context_resolver(db, now, deepcopy(receipt), deepcopy(stored_profile))
            if type(supplied) is not tuple or len(supplied) != 3:
                raise _error("CURRENTNESS_UNAVAILABLE")
            raw_bound, raw_profile, raw_baseline = supplied
            profile = _profile(raw_profile)
            if profile != stored_profile:
                raise _error("PROFILE_MISMATCH")
            baseline = None if raw_baseline is None else json.loads(_pack(raw_baseline)[0])
            if ((None if baseline is None else _pack(baseline)[1]) !=
                    (None if stored_baseline is None else _pack(stored_baseline)[1])):
                raise _error("BASELINE_MISMATCH")
            bound = validate_partitioned_runtime(raw_bound, baseline_context=baseline)
            self._normal_purpose(bound, baseline)
            if bound["_partitioned_receipt"] != receipt:
                raise _error("BINDING_MISMATCH")
            execution_profiles.check_plan(profile, bound)
            return bound, profile, baseline
        except EvidenceError:
            raise
        except (ContractError, TypeError, ValueError, KeyError, IndexError, RecursionError, sqlite3.Error):
            raise _error("CURRENTNESS_UNAVAILABLE") from None
        except Exception:
            raise _error("CURRENTNESS_UNAVAILABLE") from None

    def _resolve_run_context(self, db: sqlite3.Connection, row: sqlite3.Row,
                             now: int) -> tuple[dict[str, Any], dict[str, Any], Any]:
        receipt = self._receipt(row["bundle_json"], row["bundle_digest"])
        profile = _load(row["profile_json"], row["profile_digest"])
        baseline = None
        if row["baseline_json"] is not None:
            baseline = _load(row["baseline_json"], row["baseline_digest"])
        elif row["baseline_digest"] is not None:
            raise _error("STORAGE_CORRUPT")
        if receipt["manifest_ref"].get("id") != row["run_id"]:
            raise _error("STORAGE_CORRUPT")
        return self._resolve_fresh(db, now, receipt, profile, baseline)

    def _binding_summary(self, bound: dict[str, Any], profile: dict[str, Any], baseline: Any) -> dict[str, Any]:
        return _binding_summary(bound, profile, baseline)

    def _aggregate_value(self, bound: dict[str, Any], attempts: list[dict[str, Any]],
                         profile: dict[str, Any], baseline: Any) -> dict[str, Any]:
        context = bound.get("_partitioned_context")
        if type(context) is not dict:
            raise _error("STORAGE_CORRUPT")
        try:
            return aggregate_partitioned(
                context["manifest"], context["contract"], context["index"], context["segments"],
                context["policy"], context["registry"], context["case_set"], attempts,
                execution_profile=profile, baseline_context=baseline,
            )
        except EvidenceError:
            raise
        except (ContractError, TypeError, ValueError, KeyError, IndexError, RecursionError):
            raise _error("AGGREGATION_INVALID") from None

    def _aggregate_kind(self) -> str:
        return "partitioned_aggregation"

    def _check_context(self, db: sqlite3.Connection, row: sqlite3.Row, now: int,
                       bound: dict[str, Any], profile: dict[str, Any], baseline: Any) -> None:
        receipt = self._receipt(row["bundle_json"], row["bundle_digest"])
        fresh_bound, fresh_profile, fresh_baseline = self._resolve_fresh(
            db, now, receipt, _load(row["profile_json"], row["profile_digest"]),
            None if row["baseline_json"] is None else _load(row["baseline_json"], row["baseline_digest"]),
        )
        if (fresh_bound["_partitioned_receipt"] != bound["_partitioned_receipt"]
                or fresh_profile != profile
                or (None if fresh_baseline is None else _pack(fresh_baseline)[1]) !=
                   (None if baseline is None else _pack(baseline)[1])):
            raise _error("BINDING_MISMATCH")

    def _view(self, db: sqlite3.Connection, run_id: str, now: int,
              bound: dict[str, Any], profile: dict[str, Any], baseline: Any) -> dict[str, Any]:
        row = self._run_row(db, run_id)
        state = self._state(db, run_id)
        return {
            "schema_version": 2, "kind": "run_evidence_run", "run_id": run_id,
            "bundle": deepcopy(bound["_partitioned_receipt"]), "bundle_digest": row["bundle_digest"],
            "execution_profile": deepcopy(profile), "baseline_context": deepcopy(baseline),
            "state": state["state"], "hold_reason": state["hold_reason"],
            "aggregate_digest": state["aggregate_digest"], "decision_digest": state["decision_digest"],
            "diagnostic_finalized": state["finalized_at"] is not None,
            "diagnostic_finalized_at": state["finalized_at"],
            "authority_connected": False, "resource_closure_verified": False,
            "baseline_freshness_verified": False, "adoption_verified": False, "ci_eligible": False,
            "binding": self._binding_summary(bound, profile, baseline),
        }

    def _run_view(self, db: sqlite3.Connection, run_id: str, now: int) -> dict[str, Any]:
        row = self._run_row(db, run_id)
        context = self._resolve_run_context(db, row, now)
        return self._view(db, run_id, now, *context)

    def start_run(self, bound_run: Any, execution_profile: Any, baseline_context: Any = None) -> dict[str, Any]:
        try:
            baseline = None if baseline_context is None else json.loads(_pack(baseline_context)[0])
            bound = validate_partitioned_runtime(bound_run, baseline_context=baseline)
            profile = _profile(execution_profile)
            self._normal_purpose(bound, baseline)
            execution_profiles.check_plan(profile, bound)
            stored = bound_storage_document(bound, baseline)
            run_id = bound["manifest"]["run_id"]
            _, digest = _pack(stored)
            if bound_bundle_digest(bound, baseline) != digest or self._allowed.get(run_id) != digest:
                raise _error("BINDING_NOT_ADMITTED")
            profile_raw, profile_digest = _pack(profile)
            baseline_raw = baseline_digest = None
            if baseline is not None:
                baseline_raw, baseline_digest = _pack(baseline)
            bundle_raw, bundle_digest = _pack(stored)
        except EvidenceError:
            raise
        except (ContractError, TypeError, ValueError, KeyError, RecursionError):
            raise _error("INVALID_BINDING") from None
        with self._transaction() as db:
            now = self._now(db)
            fresh = self._resolve_fresh(db, now, stored, profile, baseline)
            row = db.execute("SELECT * FROM bound_runs WHERE run_id=?", (run_id,)).fetchone()
            if row is not None:
                if (row["bundle_digest"] != bundle_digest or row["profile_digest"] != profile_digest
                        or row["baseline_digest"] != baseline_digest):
                    raise _error("RUN_CONFLICT")
                return self._view(db, run_id, now, *fresh)
            db.execute("INSERT INTO bound_runs VALUES(?,?,?,?,?,?,?,?,?)",
                       (run_id, bundle_raw, bundle_digest, profile_raw, profile_digest,
                        baseline_raw, baseline_digest, now, now))
            db.execute("INSERT INTO run_state VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (run_id, "OPEN", None, None, None, None, "UNKNOWN", now, None, 0))
            return self._view(db, run_id, now, *fresh)

    def get_run(self, run_id: str) -> dict[str, Any]:
        from .run_evidence import _id
        _id(run_id)
        with self._transaction() as db:
            now = self._now(db)
            row = self._run_row(db, run_id)
            return self._view(db, run_id, now, *self._resolve_run_context(db, row, now))


__all__ = ["PartitionedNormalEvidenceBook"]
