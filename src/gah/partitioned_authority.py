"""分割Planの保存・読取だけを明示的に追加するschema-v5拡張。"""
from __future__ import annotations

import hashlib
from pathlib import Path

from . import evaluation_authority as base
from . import partitioned_plan_store as plans
from .adoption import AdoptionError
from .read_checks import checked_action


def source_digest() -> str:
    """既存v4と分割保存の実ソースを結合し、配置側のdigestを作る。"""
    digest = hashlib.sha256(b"gah-partitioned-authority-v5\x00")
    digest.update(bytes.fromhex(base._compute_source_digest()))
    try:
        for name in (
            "partitioned_authority.py", "partitioned_plan_store.py",
            "partitioned_plan_migrations.py", "partitioned_trial_plan.py",
            "partitioned_run_contracts.py",
        ):
            raw = Path(__file__).with_name(name).read_bytes()
            digest.update(len(raw).to_bytes(8, "big"))
            digest.update(raw)
    except OSError:
        raise AdoptionError("EXTENSION_INVALID") from None
    return digest.hexdigest()


class PartitionedEvaluationExtension(base.EvaluationExtension):
    """v2 run開始・CI成功は有効化せず、v4経路の既定選択も変更しない。"""

    schema_version = 5
    tables = {**base.EvaluationExtension.tables, **plans.TABLES}
    actions = {**base.EvaluationExtension.actions, **plans.ACTIONS}
    fresh_actions = base.EvaluationExtension.fresh_actions | plans.FRESH_ACTIONS

    @property
    def digest(self):
        return source_digest()

    def create_schema(self, db):
        super().create_schema(db)
        plans.create_schema(db)

    def migrate_schema(self, db):
        # このmethodだけで任意の既存storeを再束縛しない。
        raise AdoptionError("EXPLICIT_MIGRATION_REQUIRED")

    def validate_request(self, request):
        if type(request) is dict and request.get("action") in plans.ACTIONS:
            return plans.validate_request(request)
        return super().validate_request(request)

    def execute(self, store, db, request, actor_id, context, now):
        if request["action"] not in plans.ACTIONS:
            return super().execute(store, db, request, actor_id, context, now)
        try:
            base.resources.ResourceBook(db)._touch(now)
            base.run_evidence.RunEvidenceBook(db, now=now, allowed_bindings={})._now(db)
        except (base.resources.ResourceError, base.run_evidence.EvidenceError) as error:
            raise AdoptionError(error.code) from None
        return self._partition_execute(store, db, request, actor_id, context, now)

    @checked_action
    def _partition_execute(self, store, db, request, actor_id, context, now):
        before = source_digest()
        if before != store._extension_digest:
            raise AdoptionError("EXTENSION_INVALID")
        result = plans.handle(self, store, db, request, actor_id, context, now)
        if source_digest() != before:
            raise AdoptionError("EXTENSION_INVALID")
        return result
