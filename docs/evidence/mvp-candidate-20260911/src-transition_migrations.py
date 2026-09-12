"""契約遷移候補のschemaを、既存authorityのtransactionへ追加する。"""

from __future__ import annotations

import sqlite3

from .adoption import AdoptionError


TABLES = {
    "transition_candidates": {
        "candidate_id", "proposal_id", "proposal_digest", "baseline_series_id",
        "payload_json", "digest", "created_at", "permission_generation", "actor_id", "context",
    },
    "transition_runs": {"run_id", "candidate_id", "side"},
}


def create_schema(db: sqlite3.Connection) -> None:
    """新規DBまたは明示migrationのtransaction内で遷移表を作成する。"""

    if not isinstance(db, sqlite3.Connection) or not db.in_transaction:
        raise AdoptionError("TRANSACTION_REQUIRED")
    db.execute(
        """CREATE TABLE transition_candidates(
            candidate_id TEXT PRIMARY KEY,
            proposal_id TEXT NOT NULL,
            proposal_digest TEXT NOT NULL,
            baseline_series_id TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            digest TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            permission_generation INTEGER NOT NULL,
            actor_id TEXT NOT NULL,
            context TEXT NOT NULL
        )"""
    )
    db.execute(
        """CREATE TABLE transition_runs(
            run_id TEXT PRIMARY KEY,
            candidate_id TEXT NOT NULL,
            side TEXT NOT NULL CHECK(side IN ('old','new')),
            UNIQUE(candidate_id, side),
            FOREIGN KEY(candidate_id) REFERENCES transition_candidates(candidate_id)
        )"""
    )


__all__ = ["TABLES", "create_schema"]
