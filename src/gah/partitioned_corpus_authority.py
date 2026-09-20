"""Explicit schema-v7 authority for pre-contract synthetic corpus provisioning."""
from __future__ import annotations

import hashlib
from pathlib import Path

from . import partitioned_run_authority as v6
from . import partitioned_corpus_store as corpora
from .adoption import AdoptionError
from .read_checks import checked_action


def source_digest() -> str:
    """Pin inherited v6 behavior and the closed corpus codec/storage source set."""
    digest = hashlib.sha256(b"gah-partitioned-corpus-authority-v7\x00")
    try:
        digest.update(bytes.fromhex(v6.source_digest()))
        for name in (
            "partitioned_corpus_authority.py", "partitioned_corpus_store.py",
            "partitioned_corpus_migrations.py",
            "partitioned_scale_corpus.py", "partitioned_case_set.py", "query_scale_data.py",
        ):
            raw = Path(__file__).with_name(name).read_bytes()
            digest.update(len(raw).to_bytes(8, "big"))
            digest.update(raw)
    except (OSError, ValueError):
        raise AdoptionError("EXTENSION_INVALID") from None
    return digest.hexdigest()


class PartitionedCorpusEvaluationExtension(v6.PartitionedRunEvaluationExtension):
    """Provisioning does not adopt a contract or enable generation-2 runs/CI."""

    schema_version = 7
    tables = {**v6.PartitionedRunEvaluationExtension.tables, **corpora.TABLES}
    actions = {**v6.PartitionedRunEvaluationExtension.actions, **corpora.ACTIONS}
    fresh_actions = v6.PartitionedRunEvaluationExtension.fresh_actions | set(corpora.ACTIONS)

    @property
    def digest(self):
        return source_digest()

    def create_schema(self, db):
        super().create_schema(db)
        corpora.create_schema(db)

    def migrate_schema(self, db):
        raise AdoptionError("EXPLICIT_MIGRATION_REQUIRED")

    def validate_request(self, request):
        if type(request) is dict and request.get("action") in corpora.ACTIONS:
            return corpora.validate_request(request)
        return super().validate_request(request)

    def execute(self, store, db, request, actor_id, context, now):
        if request["action"] in corpora.ACTIONS:
            return self._corpus_execute(store, db, request, actor_id, context, now)
        return super().execute(store, db, request, actor_id, context, now)

    @checked_action
    def _corpus_execute(self, store, db, request, actor_id, context, now):
        return self._checked_handle(corpora.handle, store, db, request, actor_id, context, now)


__all__ = ["PartitionedCorpusEvaluationExtension", "source_digest"]
