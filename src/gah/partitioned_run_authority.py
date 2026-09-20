"""Explicit schema-v6 extension for ref-only generation-1 diagnostic runs."""
from __future__ import annotations

import hashlib
from pathlib import Path

from . import evaluation_authority as base
from . import partitioned_authority as v5
from . import partitioned_plan_store as plans
from . import partitioned_run_store as runs
from .adoption import AdoptionError
from .contracts import ContractError
from .read_checks import checked_action


def source_digest() -> str:
    """Pin v5 plus the v2-run router, evidence book, and explicit migration source."""
    digest = hashlib.sha256(b"gah-partitioned-run-authority-v6\x00")
    try:
        digest.update(bytes.fromhex(v5.source_digest()))
        for name in (
            "partitioned_run_authority.py", "partitioned_run_store.py",
            "partitioned_run_evidence.py", "partitioned_run_migrations.py",
        ):
            raw = Path(__file__).with_name(name).read_bytes()
            digest.update(len(raw).to_bytes(8, "big"))
            digest.update(raw)
    except (OSError, ValueError):
        raise AdoptionError("EXTENSION_INVALID") from None
    return digest.hexdigest()


def _exact_source_digest(extension):
    """Resolve only the explicitly supported concrete authority implementations."""
    if type(extension) is PartitionedRunEvaluationExtension:
        return source_digest()
    from . import partitioned_corpus_authority as v7
    if type(extension) is v7.PartitionedCorpusEvaluationExtension:
        return v7.source_digest()
    raise AdoptionError("EXTENSION_INVALID")


class PartitionedRunEvaluationExtension(v5.PartitionedEvaluationExtension):
    """Opt-in v6 router; default v4/v5 selection and CI gates stay unchanged."""

    schema_version = 6
    tables = {**v5.PartitionedEvaluationExtension.tables, **runs.TABLES}
    actions = {**v5.PartitionedEvaluationExtension.actions, **runs.ACTIONS}
    fresh_actions = v5.PartitionedEvaluationExtension.fresh_actions | runs.FRESH_ACTIONS

    @property
    def digest(self):
        return source_digest()

    def create_schema(self, db):
        super().create_schema(db)
        runs.create_schema(db)

    def migrate_schema(self, db):
        raise AdoptionError("EXPLICIT_MIGRATION_REQUIRED")

    def validate_request(self, request):
        if type(request) is dict and request.get("action") in runs.ACTIONS:
            return runs.validate_request(request)
        return super().validate_request(request)

    def _check_start(self, store, db, run_id, now, *, actor_id=None, context=None):
        if db.execute("SELECT 1 FROM eval_runs_v2 WHERE run_id=?", (run_id,)).fetchone():
            return runs.check_start_v2(self, store, db, run_id, now)
        return base.EvaluationExtension._check_start(
            self, store, db, run_id, now, actor_id=actor_id, context=context,
        )

    def execute(self, store, db, request, actor_id, context, now):
        action = request["action"]
        run_id = request.get("run_id") if type(request) is dict else None
        if type(run_id) is str and db.execute(
            "SELECT 1 FROM eval_runs_v2 WHERE run_id=?", (run_id,)
        ).fetchone():
            # These inherited paths resolve v1 eval_runs/bound data and must not
            # interpret a v2 receipt as a v1 run or terminal record.
            if action in base.assurance_authority.FIELDS or action in {
                "run_status", "run_cancel_finalize", "run_outputs", "run_artifact"
            }:
                raise AdoptionError("SCOPE_UNSUPPORTED")
            if action == "resource_operation":
                return self._resource_operation_execute(store, db, request, actor_id, context, now)
            if action in {"resource_cancel", "resource_cancel_claim"}:
                return self._cancel_resource_execute(store, db, request, actor_id, context, now)
        if action in runs.ACTIONS:
            return self._run_execute(store, db, request, actor_id, context, now)
        if action in plans.ACTIONS:
            return self._plan_execute(store, db, request, actor_id, context, now)
        if action == "run_begin":
            run_id = request["manifest"]["run_id"]
            if runs.has_v2_run_or_bound(db, run_id):
                raise AdoptionError("RUN_CONFLICT")
        elif action == "contract_candidate_prepare":
            for run_id in (request["old_run_id"], request["new_run_id"]):
                if runs.has_v2_run_or_bound(db, run_id):
                    raise AdoptionError("RUN_CONFLICT")
        elif action == "contract_candidate_begin":
            from . import transition_authority
            check = lambda value: base.EvaluationExtension._transition_preflight(
                self, store, db, value, actor_id, context, now
            )
            _, candidate = transition_authority.fresh_candidate(
                store, db, request["candidate_id"], now, check
            )
            manifest = candidate["runs"][request["side"]]["bound_run"]["manifest"]
            if runs.has_v2_run_or_bound(db, manifest["run_id"]):
                raise AdoptionError("RUN_CONFLICT")
        elif action == "combined_prepare":
            for child in request["children"]:
                if runs.has_v2_run_or_bound(db, child["run_id"]):
                    raise AdoptionError("RUN_CONFLICT")
        elif action in {"run_prepare", "run_prepare_scoped"}:
            if runs.has_v2_run_or_bound(db, request["run_id"]):
                raise AdoptionError("RUN_CONFLICT")
        elif action in {"fixture_prepare", "guardrail_prepare"}:
            if runs.has_v2_run_or_bound(db, request["run_id"]):
                raise AdoptionError("RUN_CONFLICT")
        result = super().execute(store, db, request, actor_id, context, now)
        if type(run_id) is str and action in base.resource_authority.FIELDS and db.execute(
            "SELECT 1 FROM eval_runs_v2 WHERE run_id=?", (run_id,)
        ).fetchone():
            # The existing resource ledger is connected, but this diagnostic route
            # does not establish general authority, adoption, or closure acceptance.
            result = dict(result)
            result.update(
                authority_connected=False,
                resource_closure_verified=False,
                baseline_freshness_verified=False,
                adoption_verified=False,
                admission_verified=False,
                ci_eligible=False,
            )
            return result
        # Catch any legacy producer whose resolved output ID was not explicit at entry.
        run_id = result.get("run_id") if type(result) is dict else None
        if type(run_id) is str and db.execute(
            "SELECT 1 FROM eval_runs_v2 WHERE run_id=?", (run_id,)
        ).fetchone():
            raise AdoptionError("RUN_CONFLICT")
        return result

    @checked_action
    def _plan_execute(self, store, db, request, actor_id, context, now):
        return self._checked_handle(plans.handle, store, db, request, actor_id, context, now)

    @checked_action
    def _run_execute(self, store, db, request, actor_id, context, now):
        return self._checked_handle(runs.handle, store, db, request, actor_id, context, now)

    @checked_action
    def _resource_operation_execute(self, store, db, request, actor_id, context, now):
        before = _exact_source_digest(self)
        if before != store._extension_digest:
            raise AdoptionError("EXTENSION_INVALID")
        try:
            result = runs.read_resource_operation_v2(self, store, db, request, now)
        except base.resources.ResourceError as error:
            raise AdoptionError(error.code) from None
        if _exact_source_digest(self) != before:
            raise AdoptionError("EXTENSION_INVALID")
        return runs._result(request["action"], request["request_id"], **result)

    @checked_action
    def _cancel_resource_execute(self, store, db, request, actor_id, context, now):
        before = _exact_source_digest(self)
        if before != store._extension_digest:
            raise AdoptionError("EXTENSION_INVALID")
        result = runs.cancel_resource_v2(self, store, db, request, now)
        if _exact_source_digest(self) != before:
            raise AdoptionError("EXTENSION_INVALID")
        return result

    def _checked_handle(self, handler, store, db, request, actor_id, context, now):
        before = _exact_source_digest(self)
        if before != store._extension_digest:
            raise AdoptionError("EXTENSION_INVALID")
        try:
            base.resources.ResourceBook(db)._touch(now)
            base.run_evidence.RunEvidenceBook(db, now=now, allowed_bindings={})._now(db)
        except (base.resources.ResourceError, base.run_evidence.EvidenceError) as error:
            raise AdoptionError(error.code) from None
        try:
            result = handler(self, store, db, request, actor_id, context, now)
        except base.run_evidence.EvidenceError as error:
            raise AdoptionError(error.code) from None
        except ContractError as error:
            raise AdoptionError(error.code) from None
        if _exact_source_digest(self) != before:
            raise AdoptionError("EXTENSION_INVALID")
        return result


__all__ = ["PartitionedRunEvaluationExtension", "source_digest"]
