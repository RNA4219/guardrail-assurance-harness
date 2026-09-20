"""明示された既知v4 storeだけをpartitioned plan schema-v5へ移行する。"""

from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3
import stat
from typing import Any

from . import adoption_migrations as migrations
from . import evaluation_authority as base
from . import partitioned_authority as target_extension
from . import partitioned_plan_store as plan_store
from .adoption import AdoptionError
from .sqlite_limits import connect_sqlite
from .policy import initial_policy_profile


SOURCE_V8_EXTENSION_DIGEST = "8c5df825738c20fce068b026ae8d3619b91463b99f05fad6d4fcc84c524bbb3d"
V4_BOOTSTRAP_DIGEST = migrations._V2_BOOTSTRAP_DIGEST
SOURCE_V8_VALIDATOR_DIGEST = "208238ee2cabaaad6f756ff6eef847c248bf1da2b34fe666841bab88be06698a"
_V4_VERSION = 4
_V5_VERSION = 5
_EXPECTED_CONFIG_KEYS = {"bootstrap_digest", "validator_digest", "extension_digest"}


def _error(code: str) -> migrations.MigrationError:
    return migrations._error(code)


def _current_validator_digest() -> str:
    """現在のAdoption validator source bytesの長さprefix付きdigest。"""
    from .adoption import __file__ as adoption_file
    from . import policy

    digest = hashlib.sha256()
    paths = (Path(adoption_file), Path(policy.__file__),
             Path(adoption_file).resolve().parents[2] / "config" / "bootstrap-policy.v1.json")
    try:
        for source in paths:
            raw = source.read_bytes()
            digest.update(len(raw).to_bytes(8, "big"))
            digest.update(raw)
    except OSError:
        raise _error("MIGRATION_UNAVAILABLE") from None
    return digest.hexdigest()


def _checked_current_v4_pair() -> tuple[str, str]:
    """import時のextension digestと現在source bytesが混在していないことを確認する。"""
    try:
        pinned_extension = base.EvaluationExtension.digest
        current_extension = base._compute_source_digest()
        if pinned_extension != current_extension:
            raise _error("SOURCE_CHANGED")
        validator = _current_validator_digest()
        if (base.EvaluationExtension.digest != pinned_extension
                or base._compute_source_digest() != current_extension
                or _current_validator_digest() != validator):
            raise _error("SOURCE_CHANGED")
    except migrations.MigrationError:
        raise
    except Exception:
        raise _error("MIGRATION_UNAVAILABLE") from None
    return current_extension, validator


def _allowed_v4_pairs(current_pair: tuple[str, str]) -> set[tuple[str, str]]:
    # source-v8は固定pair、checkout側の現在v4はentryで照合した実pair。
    return {(SOURCE_V8_EXTENSION_DIGEST, SOURCE_V8_VALIDATOR_DIGEST), current_pair}


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
    if (stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1):
        raise _error("INVALID_PATH")
    return target


def _verify_v4_state(db: sqlite3.Connection, expected_source_digest: str,
                     current_pair: tuple[str, str]) -> dict[str, Any]:
    try:
        version = db.execute("PRAGMA user_version").fetchone()[0]
    except sqlite3.Error:
        raise _error("UNSUPPORTED_STORE") from None
    if version != _V4_VERSION:
        raise _error("UNSUPPORTED_STORE")
    migrations._verify_columns(db, migrations._V4_COLUMNS)
    config = migrations._config(db)
    if (set(config) != _EXPECTED_CONFIG_KEYS
            or config.get("bootstrap_digest") != V4_BOOTSTRAP_DIGEST):
        raise _error("CONFIG_MISMATCH")
    pair = (config.get("extension_digest"), config.get("validator_digest"))
    if pair not in _allowed_v4_pairs(current_pair) or expected_source_digest != pair[0]:
        raise _error("CONFIG_MISMATCH")
    meta = migrations._meta(db, _V4_VERSION)
    if migrations._digest(initial_policy_profile()) != V4_BOOTSTRAP_DIGEST:
        raise _error("BOOTSTRAP_MISMATCH")

    # 既存のread-only意味検査をSAVEPOINT内で実行し、検査起因の時計更新も保存しない。
    before = migrations._verification_snapshot(db)
    db.execute("SAVEPOINT gah_partitioned_v4_verify")
    try:
        migrations._verify_v3_json_rows(db, adopted=True, following=True)
        migrations._verify_v4_candidate_rows(
            db, adopted=True, regression=True, cancellation=True,
            following=True, scoped=True,
        )
        from . import baseline_refresh_migration
        try:
            baseline_refresh_migration.verify(db, meta["last_clock"], following=True)
        except AdoptionError:
            raise _error("STORAGE_CORRUPT") from None
        if migrations._verification_snapshot(db) != before:
            raise _error("STORAGE_CORRUPT")
    finally:
        db.execute("ROLLBACK TO gah_partitioned_v4_verify")
        db.execute("RELEASE gah_partitioned_v4_verify")
    return {"config": config, "meta": meta, "snapshot": before}


def migrate_partitioned_store(path: str | Path, *, expected_source_digest: str) -> dict[str, Any]:
    """既知のv4 storeをv5へ移行する。実行側で同一storeのoperation lockを保持すること。

    This explicit migration does not rebind any pre-existing authority record. It
    creates only the three v5 plan tables and changes schema/config metadata.
    """
    try:
        from .contracts import require_digest
        require_digest(expected_source_digest)
    except (ImportError, TypeError, ValueError):
        raise _error("CONFIG_MISMATCH") from None
    target = _validate_path(path)
    entry_source_pair = _checked_current_v4_pair()
    db: sqlite3.Connection | None = None
    try:
        db = connect_sqlite(str(target), isolation_level=None, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("BEGIN IMMEDIATE")
        verified = _verify_v4_state(db, expected_source_digest, entry_source_pair)

        # PartitionedEvaluationExtension.create_schema also recreates v4 base
        # tables, so only its additive store DDL is appropriate for v4.
        if _checked_current_v4_pair() != entry_source_pair:
            raise _error("SOURCE_CHANGED")
        try:
            new_digest = target_extension.PartitionedEvaluationExtension().digest
        except Exception:
            raise _error("MIGRATION_UNAVAILABLE") from None

        plan_store.create_schema(db)
        target_columns = {**migrations._V4_COLUMNS, **plan_store.TABLES}
        migrations._verify_columns(db, target_columns)
        for table in plan_store.TABLES:
            if db.execute('SELECT 1 FROM "' + table + '" LIMIT 1').fetchone() is not None:
                raise _error("STORAGE_CORRUPT")
        if migrations._verification_snapshot(db) != verified["snapshot"]:
            raise _error("STORAGE_CORRUPT")

        db.execute("UPDATE adoption_meta SET value=? WHERE key='schema_version'", (_V5_VERSION,))
        db.execute("UPDATE adoption_config SET value=? WHERE key='extension_digest'", (new_digest,))
        db.execute("PRAGMA user_version=5")
        # Re-read all state before commit. The pre-existing validator/bootstrap
        # bindings and non-schema metadata remain exactly as observed.
        if (migrations._meta(db, _V5_VERSION)["last_clock"] != verified["meta"]["last_clock"]
                or migrations._meta(db, _V5_VERSION)["permission_generation"] != verified["meta"]["permission_generation"]):
            raise _error("STORAGE_CORRUPT")
        config_after = migrations._config(db)
        if (config_after != {**verified["config"], "extension_digest": new_digest}
                or db.execute("PRAGMA user_version").fetchone()[0] != _V5_VERSION):
            raise _error("STORAGE_CORRUPT")
        if (_checked_current_v4_pair() != entry_source_pair
                or target_extension.PartitionedEvaluationExtension().digest != new_digest):
            raise _error("SOURCE_CHANGED")
        db.commit()
        return {
            "schema_version": _V5_VERSION,
            "kind": "partitioned_plan_migration_result",
            "changed": True,
            "predecessor_extension_digest": expected_source_digest,
            "extension_digest": new_digest,
            "ci_eligible": False,
        }
    except migrations.MigrationError:
        if db is not None:
            try:
                db.rollback()
            except sqlite3.Error:
                pass
        raise
    except (sqlite3.Error, OSError):
        if db is not None:
            try:
                db.rollback()
            except sqlite3.Error:
                pass
        raise _error("MIGRATION_FAILED") from None
    except Exception:
        if db is not None:
            try:
                db.rollback()
            except sqlite3.Error:
                pass
        raise _error("MIGRATION_FAILED") from None
    finally:
        if db is not None:
            try:
                db.close()
            except sqlite3.Error:
                pass


__all__ = ["migrate_partitioned_store"]
