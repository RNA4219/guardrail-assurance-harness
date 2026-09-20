"""明示された厳密schema-v5 storeをv6 run tableへ移行する。"""
from __future__ import annotations
import json
from pathlib import Path
import sqlite3
import stat
from typing import Any
from . import adoption_migrations as migrations
from . import partitioned_plan_migrations as plan_migrations
from .adoption import AdoptionError
from .sqlite_limits import connect_sqlite
from .policy import initial_policy_profile

_V5_VERSION = 5
_V6_VERSION = 6
_PINNED_V5_EXTENSION = "db9d06517ec4c672cb997914c32fd476f20612b030f1bebc162657b2c88eeffd"
_PINNED_V5_VALIDATOR = "10d966d46ac275fa1977049487cbcf44d5c9326e6e1f2f0ea1a70f81d1efa291"
_PINNED_BOOTSTRAP = migrations._V2_BOOTSTRAP_DIGEST
_EXPECTED_CONFIG_KEYS = {"bootstrap_digest", "validator_digest", "extension_digest"}


def _error(code: str) -> migrations.MigrationError:
    return migrations._error(code)


def _current_v5_pair() -> tuple[str, str]:
    """現在checkout内で読み直したv5 extension/validator pairを確認する。"""
    try:
        from .partitioned_authority import PartitionedEvaluationExtension
        extension = PartitionedEvaluationExtension()
        ext_digest = extension.digest
        validator = plan_migrations._current_validator_digest()
        if (PartitionedEvaluationExtension().digest != ext_digest
                or plan_migrations._current_validator_digest() != validator):
            raise _error("SOURCE_CHANGED")
        return ext_digest, validator
    except migrations.MigrationError:
        raise
    except AdoptionError:
        raise _error("MIGRATION_UNAVAILABLE") from None
    except Exception:
        raise _error("MIGRATION_UNAVAILABLE") from None


def _v5_columns() -> dict[str, set[str]]:
    from . import partitioned_plan_store
    return {**migrations._V4_COLUMNS, **partitioned_plan_store.TABLES}


def _validate_path(path: str | Path) -> Path:
    if not isinstance(path, (str, Path)):
        raise _error("INVALID_PATH")
    target = Path(path)
    try:
        info = target.lstat()
    except FileNotFoundError:
        raise _error("STORE_MISSING") from None
    except OSError:
        raise _error("INVALID_PATH") from None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise _error("INVALID_PATH")
    return target


def _snapshot_rows(db: sqlite3.Connection, columns: dict[str, set[str]]):
    snapshot = {}
    for table in columns:
        names = [row[1] for row in db.execute('PRAGMA table_info("' + table + '")')]
        rows = [tuple(row) for row in db.execute('SELECT * FROM "' + table + '"')]
        snapshot[table] = (names, sorted(rows, key=repr))
    return snapshot


def _validate_partial_segment_ranges(ranges: list[tuple[int, int, int]],
                                     segment_count: int, entry_count: int) -> None:
    """Check that sparse staged ranges can still form a complete ordered plan."""
    from .contracts import MAX_INTEGER
    from .partitioned_trial_plan import MAX_SEGMENTS
    if (type(ranges) is not list or type(segment_count) is not int
            or not 1 <= segment_count <= MAX_SEGMENTS or type(entry_count) is not int
            or not 1 <= entry_count <= 10_000):
        raise _error("STORAGE_CORRUPT")
    ordered = sorted(ranges, key=lambda item: item[0] if type(item) is tuple and item else -1)
    previous = None
    for current in ordered:
        if (type(current) is not tuple or len(current) != 3
                or any(type(value) is not int or not 0 <= value <= MAX_INTEGER for value in current)):
            raise _error("STORAGE_CORRUPT")
        ordinal, first, end = current
        if (not 0 <= ordinal < segment_count or first >= end
                or first < ordinal
                or end > entry_count - (segment_count - ordinal - 1)):
            raise _error("STORAGE_CORRUPT")
        if ordinal == 0 and first != 0:
            raise _error("STORAGE_CORRUPT")
        if ordinal == segment_count - 1 and end != entry_count:
            raise _error("STORAGE_CORRUPT")
        if previous is not None:
            prior_ordinal, _prior_first, prior_end = previous
            missing = ordinal - prior_ordinal - 1
            if missing < 0 or first - prior_end < missing or (missing == 0 and first != prior_end):
                raise _error("STORAGE_CORRUPT")
        previous = current


def _verify_partitioned_plan_rows(db: sqlite3.Connection, config: dict[str, str], *,
                                  allowed_extension_digests: set[str] | None = None) -> None:
    """Validate v5 plan bytes and bindings using only metadata saved with each row.

    Expiry, current permission generation, and current contract generation are not
    admission criteria here: old history is preserved. Their stored types and the
    historical contract reference are still checked.
    """
    from . import partitioned_plan_store as plans
    from .contracts import ContractError, MAX_INTEGER, require_digest, require_id, require_uint
    from .partitioned_trial_plan import MAX_SEGMENTS, restore_trial_plan
    from .run_contracts import content_ref, validate_evaluation_contract

    def corrupt_call(function, *args):
        try:
            return function(*args)
        except (AdoptionError, ContractError, TypeError, ValueError, KeyError,
                UnicodeError, RecursionError, sqlite3.Error):
            raise _error("STORAGE_CORRUPT") from None

    accepted_digests = ({config["extension_digest"]} if allowed_extension_digests is None
                        else allowed_extension_digests)
    if type(accepted_digests) is not set or not accepted_digests:
        raise _error("STORAGE_CORRUPT")
    for accepted_digest in accepted_digests:
        corrupt_call(require_digest, accepted_digest)

    def uint(value):
        if type(value) is not int or not 0 <= value <= MAX_INTEGER:
            raise _error("STORAGE_CORRUPT")

    def text_id(value):
        if type(value) is not str:
            raise _error("STORAGE_CORRUPT")
        corrupt_call(require_id, value)

    def saved_contract(series_id, generation, raw_ref):
        text_id(series_id)
        uint(generation)
        if type(raw_ref) is not str:
            raise _error("STORAGE_CORRUPT")
        ref = corrupt_call(json.loads, raw_ref)
        if type(ref) is not dict:
            raise _error("STORAGE_CORRUPT")
        canonical, _digest, _size = corrupt_call(plans._canonical, ref)
        if canonical != raw_ref:
            raise _error("STORAGE_CORRUPT")
        corrupt_call(plans._validate_ref, ref, "evaluation_contract")
        history = db.execute(
            "SELECT * FROM eval_adoptions WHERE series_id=? AND generation=?",
            (series_id, generation),
        ).fetchone()
        if history is None:
            raise _error("STORAGE_CORRUPT")
        payload = corrupt_call(migrations._stored_json, history["payload_json"], history["digest"])
        contract = corrupt_call(validate_evaluation_contract, payload)
        expected_ref = corrupt_call(content_ref, "evaluation_contract", contract["contract_id"], contract)
        if (contract.get("generation") != generation or history["series_id"] != series_id
                or history["generation"] != generation or ref != expected_ref):
            raise _error("STORAGE_CORRUPT")
        return ref

    def verify_metadata(row, *, upload):
        text_id(row["plan_id"])
        text_id(row["contract_series_id"])
        uint(row["contract_generation"])
        uint(row["permission_generation"])
        require_digest_value = row["extension_digest"]
        if type(require_digest_value) is not str:
            raise _error("STORAGE_CORRUPT")
        corrupt_call(require_digest, require_digest_value)
        if require_digest_value not in accepted_digests:
            raise _error("STORAGE_CORRUPT")
        text_id(row["owner_actor"])
        text_id(row["owner_context"])
        if upload:
            uint(row["created_at"])
            uint(row["expires_at"])
            uint(row["stored_bytes"])
            uint(row["expected_segments"])
            if row["expires_at"] != row["created_at"] + plans.UPLOAD_TTL:
                raise _error("STORAGE_CORRUPT")
        else:
            uint(row["committed_at"])
            uint(row["artifact_bytes"])
        return saved_contract(row["contract_series_id"], row["contract_generation"],
                              row["contract_ref_json"])

    def load_bound_index(row, cref):
        index = corrupt_call(plans._load_index, row)
        if (index.get("plan_id") != row["plan_id"] or index.get("contract_ref") != cref
                or content_ref("trial_plan_index", row["plan_id"], index)
                != {"kind": "trial_plan_index", "id": row["plan_id"], "digest": row["index_digest"]}):
            raise _error("STORAGE_CORRUPT")
        return index

    try:
        commit_count = db.execute("SELECT COUNT(*) FROM partition_plan_commits").fetchone()[0]
        upload_count = db.execute("SELECT COUNT(*) FROM partition_plan_upload").fetchone()[0]
        segment_count = db.execute("SELECT COUNT(*) FROM partition_plan_segments").fetchone()[0]
        if (type(commit_count) is not int or commit_count > plans.MAX_COMMITS
                or type(upload_count) is not int or upload_count > 1
                or type(segment_count) is not int
                or segment_count > (plans.MAX_COMMITS + 1) * MAX_SEGMENTS):
            raise _error("STORAGE_CORRUPT")
        commits = db.execute("SELECT * FROM partition_plan_commits ORDER BY plan_id").fetchall()
        upload_rows = db.execute("SELECT * FROM partition_plan_upload ORDER BY singleton").fetchall()
        segment_rows = db.execute("SELECT * FROM partition_plan_segments ORDER BY plan_id,segment_index").fetchall()
        committed_total = 0
        for committed_row in commits:
            amount = committed_row["artifact_bytes"]
            if type(amount) is not int or amount < 0:
                raise _error("STORAGE_CORRUPT")
            committed_total += amount
        if committed_total > plans.MAX_TOTAL_BYTES:
            raise _error("STORAGE_CORRUPT")
        by_plan: dict[str, list[Any]] = {}
        for segment_row in segment_rows:
            by_plan.setdefault(segment_row["plan_id"], []).append(segment_row)
        committed_ids = set()
        for row in commits:
            committed_ids.add(row["plan_id"])
            cref = verify_metadata(row, upload=False)
            index = load_bound_index(row, cref)
            rows = by_plan.pop(row["plan_id"], [])
            if (len(rows) != index["segment_count"]
                    or any(type(item["segment_index"]) is not int or item["segment_index"] != i
                           for i, item in enumerate(rows))):
                raise _error("STORAGE_CORRUPT")
            segments = []
            for i, item in enumerate(rows):
                segment = corrupt_call(plans._load_segment, item)
                if (item["plan_id"] != row["plan_id"] or segment["plan_id"] != row["plan_id"]
                        or segment["segment_index"] != i
                        or segment["contract_ref"] != cref
                        or content_ref("trial_plan_entries_segment", segment["id"], segment)
                        != index["ordered_segments"][i]):
                    raise _error("STORAGE_CORRUPT")
                segments.append(segment)
            plan = corrupt_call(restore_trial_plan, index, segments)
            expected_bytes = len(row["index_json"].encode("utf-8")) + sum(item["byte_count"] for item in rows)
            if plan["plan_id"] != row["plan_id"] or row["artifact_bytes"] != expected_bytes:
                raise _error("STORAGE_CORRUPT")
        if len(upload_rows) == 1:
            row = upload_rows[0]
            if row["singleton"] != 1 or row["plan_id"] in committed_ids:
                raise _error("STORAGE_CORRUPT")
            cref = verify_metadata(row, upload=True)
            index = load_bound_index(row, cref)
            if row["expected_segments"] != index["segment_count"]:
                raise _error("STORAGE_CORRUPT")
            upload_segments = by_plan.pop(row["plan_id"], [])
            staged_bytes = len(row["index_json"].encode("utf-8"))
            ranges = []
            staged_segments = []
            for item in upload_segments:
                segment = corrupt_call(plans._load_segment, item)
                ordinal = item["segment_index"]
                if (type(ordinal) is not int or not 0 <= ordinal < index["segment_count"]
                        or item["plan_id"] != row["plan_id"] or segment["plan_id"] != row["plan_id"]
                        or segment["segment_index"] != ordinal
                        or segment["contract_ref"] != cref
                        or content_ref("trial_plan_entries_segment", segment["id"], segment)
                        != index["ordered_segments"][ordinal]):
                    raise _error("STORAGE_CORRUPT")
                first = segment["first_entry_ordinal"]
                end = first + segment["entry_count"]
                ranges.append((ordinal, first, end))
                staged_segments.append(segment)
                staged_bytes += item["byte_count"]
            _validate_partial_segment_ranges(ranges, index["segment_count"], index["entry_count"])
            if len(staged_segments) == index["segment_count"]:
                corrupt_call(restore_trial_plan, index, staged_segments)
            if (row["stored_bytes"] != staged_bytes
                    or staged_bytes > plans.MAX_ARTIFACT_BYTES * (MAX_SEGMENTS + 1)
                    or committed_total + staged_bytes > plans.MAX_TOTAL_BYTES):
                raise _error("STORAGE_CORRUPT")
        if by_plan:
            raise _error("STORAGE_CORRUPT")
    except migrations.MigrationError:
        raise
    except (AdoptionError, ContractError, TypeError, ValueError, KeyError,
            UnicodeError, RecursionError, sqlite3.Error):
        raise _error("STORAGE_CORRUPT") from None


def _verify_v5_state(db: sqlite3.Connection, expected_source_digest: str,
                     current_pair: tuple[str, str]) -> dict[str, Any]:
    try:
        version = db.execute("PRAGMA user_version").fetchone()[0]
    except sqlite3.Error:
        raise _error("UNSUPPORTED_STORE") from None
    if version != _V5_VERSION:
        raise _error("UNSUPPORTED_STORE")
    columns = _v5_columns()
    migrations._verify_columns(db, columns)
    config = migrations._config(db)
    if set(config) != _EXPECTED_CONFIG_KEYS or config.get("bootstrap_digest") != _PINNED_BOOTSTRAP:
        raise _error("CONFIG_MISMATCH")
    pair = (config.get("extension_digest"), config.get("validator_digest"))
    if pair not in {(_PINNED_V5_EXTENSION, _PINNED_V5_VALIDATOR), current_pair}:
        raise _error("CONFIG_MISMATCH")
    if expected_source_digest != pair[0]:
        raise _error("CONFIG_MISMATCH")
    meta = migrations._meta(db, _V5_VERSION)
    if migrations._digest(initial_policy_profile()) != _PINNED_BOOTSTRAP:
        raise _error("BOOTSTRAP_MISMATCH")
    before = _snapshot_rows(db, columns)
    db.execute("SAVEPOINT gah_partitioned_v5_verify")
    try:
        migrations._verify_v3_json_rows(db, adopted=True, following=True)
        migrations._verify_v4_candidate_rows(db, adopted=True, regression=True,
            cancellation=True, following=True, scoped=True, expected_schema_version=_V5_VERSION)
        _verify_partitioned_plan_rows(db, config)
        try:
            from . import baseline_refresh_migration
            baseline_refresh_migration.verify(db, meta["last_clock"], following=True)
        except AdoptionError:
            raise _error("STORAGE_CORRUPT") from None
        if _snapshot_rows(db, columns) != before:
            raise _error("STORAGE_CORRUPT")
    finally:
        db.execute("ROLLBACK TO gah_partitioned_v5_verify")
        db.execute("RELEASE gah_partitioned_v5_verify")
    return {"config": config, "meta": meta, "snapshot": before, "columns": columns}


def migrate_partitioned_run_store_v5_to_v6(path: str | Path, *, expected_source_digest: str) -> dict[str, Any]:
    """既知またはentry照合済みv5 pairだけを、明示操作でv6へ移行する。

    既存plan/evidenceの署名やextension digestは書き換えない。旧v5 planは
    v6 readerではstaleとなり、再登録には新しいplan_idが必要。同一plan_idの
    upload競合と旧evidenceの現在性は既存validatorの判定に委ねる。
    """
    try:
        from .contracts import require_digest
        require_digest(expected_source_digest)
    except (ImportError, TypeError, ValueError):
        raise _error("CONFIG_MISMATCH") from None
    target = _validate_path(path)
    entry_pair = _current_v5_pair()
    db: sqlite3.Connection | None = None
    try:
        db = connect_sqlite(str(target), isolation_level=None, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("BEGIN IMMEDIATE")
        verified = _verify_v5_state(db, expected_source_digest, entry_pair)
        if _current_v5_pair() != entry_pair:
            raise _error("SOURCE_CHANGED")
        try:
            from . import partitioned_run_authority, partitioned_run_store
            extension_type = partitioned_run_authority.PartitionedRunEvaluationExtension
            extension = extension_type()
            new_digest = extension.digest
            if getattr(extension, "schema_version", None) != _V6_VERSION or type(new_digest) is not str or len(new_digest) != 64:
                raise _error("MIGRATION_UNAVAILABLE")
            target_tables = {**verified["columns"], **partitioned_run_store.TABLES}
        except migrations.MigrationError:
            raise
        except Exception:
            raise _error("MIGRATION_UNAVAILABLE") from None
        # v6 extension.create_schema would recreate base tables; only additive DDL is permitted.
        partitioned_run_store.create_schema(db)
        migrations._verify_columns(db, target_tables)
        if db.execute("SELECT 1 FROM eval_runs_v2 LIMIT 1").fetchone() is not None:
            raise _error("STORAGE_CORRUPT")
        if _snapshot_rows(db, verified["columns"]) != verified["snapshot"]:
            raise _error("STORAGE_CORRUPT")
        fk = db.execute("PRAGMA foreign_key_list('eval_runs_v2')").fetchall()
        if not any(row[2] == "bound_runs" and row[3] == "run_id" and row[4] == "run_id" for row in fk):
            raise _error("UNSUPPORTED_STORE")
        ddl = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='eval_runs_v2'").fetchone()
        ddl_text = "".join(ddl[0].upper().split()) if ddl is not None and type(ddl[0]) is str else ""
        if ("DEFERRABLEINITIALLYDEFERRED" not in ddl_text
                or "CHECK(CONTRACT_GENERATION=1)" not in ddl_text
                or "CHECK(PERMISSION_GENERATION>=0)" not in ddl_text
                or "CHECK(CREATED_AT>=0)" not in ddl_text):
            raise _error("UNSUPPORTED_STORE")
        db.execute("UPDATE adoption_meta SET value=? WHERE key='schema_version'", (_V6_VERSION,))
        db.execute("UPDATE adoption_config SET value=? WHERE key='extension_digest'", (new_digest,))
        db.execute("PRAGMA user_version=6")
        meta_after = migrations._meta(db, _V6_VERSION)
        if meta_after["last_clock"] != verified["meta"]["last_clock"] or meta_after["permission_generation"] != verified["meta"]["permission_generation"]:
            raise _error("STORAGE_CORRUPT")
        config_after = migrations._config(db)
        if config_after != {**verified["config"], "extension_digest": new_digest}:
            raise _error("STORAGE_CORRUPT")
        if db.execute("PRAGMA user_version").fetchone()[0] != _V6_VERSION:
            raise _error("STORAGE_CORRUPT")
        after_rows = _snapshot_rows(db, verified["columns"])
        unchanged_tables = {table: rows for table, rows in verified["snapshot"].items()
                            if table not in {"adoption_meta", "adoption_config"}}
        after_unchanged_tables = {table: rows for table, rows in after_rows.items()
                                  if table not in {"adoption_meta", "adoption_config"}}
        if after_unchanged_tables != unchanged_tables:
            raise _error("STORAGE_CORRUPT")
        if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise _error("STORAGE_CORRUPT")
        if _current_v5_pair() != entry_pair or extension_type().digest != new_digest:
            raise _error("SOURCE_CHANGED")
        db.commit()
        return {"schema_version": _V6_VERSION, "kind": "partitioned_run_migration_result",
            "changed": True, "predecessor_extension_digest": expected_source_digest,
            "extension_digest": new_digest, "old_plan_rows_preserved": True,
            "old_plan_reupload_requires_new_plan_id": True, "ci_eligible": False}
    except migrations.MigrationError:
        if db is not None:
            try: db.rollback()
            except sqlite3.Error: pass
        raise
    except (sqlite3.Error, OSError):
        if db is not None:
            try: db.rollback()
            except sqlite3.Error: pass
        raise _error("MIGRATION_FAILED") from None
    except Exception:
        if db is not None:
            try: db.rollback()
            except sqlite3.Error: pass
        raise _error("MIGRATION_FAILED") from None
    finally:
        if db is not None:
            try: db.close()
            except sqlite3.Error: pass


__all__ = ["migrate_partitioned_run_store_v5_to_v6"]
