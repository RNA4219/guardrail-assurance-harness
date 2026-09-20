"""明示・厳密なschema-v6からcorpus storeを加えたv7への移行。"""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import stat
from typing import Any

from . import adoption_migrations as migrations
from . import partitioned_plan_migrations as plan_migrations
from . import partitioned_run_migrations as run_migrations
from .adoption import AdoptionError
from .policy import initial_policy_profile
from .sqlite_limits import connect_sqlite

_V6_VERSION = 6
_V7_VERSION = 7
_PINNED_V6_EXTENSION = "267711ce3e4de23c9f5646e7e9bc68f28f92da9409f169a0220163a9efec5b00"
_PINNED_V6_VALIDATOR = "4dd2dbd519231346bb4536655b470684574d559bcefbc389d460c454f20d4494"
_PINNED_BOOTSTRAP = migrations._V2_BOOTSTRAP_DIGEST
_EXPECTED_CONFIG_KEYS = {"bootstrap_digest", "validator_digest", "extension_digest"}


def _error(code: str) -> migrations.MigrationError:
    return migrations._error(code)


def _current_v6_pair() -> tuple[str, str]:
    """現在のv6 source/validator bytesがentry内で安定していることを確認する。"""
    try:
        from .partitioned_run_authority import PartitionedRunEvaluationExtension
        extension = PartitionedRunEvaluationExtension()
        pair = (extension.digest, plan_migrations._current_validator_digest())
        if (PartitionedRunEvaluationExtension().digest != pair[0]
                or plan_migrations._current_validator_digest() != pair[1]):
            raise _error("SOURCE_CHANGED")
        return pair
    except migrations.MigrationError:
        raise
    except Exception:
        raise _error("MIGRATION_UNAVAILABLE") from None


def _v6_columns() -> dict[str, set[str]]:
    from . import partitioned_plan_store, partitioned_run_store
    return {**migrations._V4_COLUMNS, **partitioned_plan_store.TABLES,
            **partitioned_run_store.TABLES}


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
        info = list(db.execute('PRAGMA table_info("' + table + '")'))
        names = [row[1] for row in info]
        rows = [tuple(row) for row in db.execute('SELECT * FROM "' + table + '"')]
        snapshot[table] = (names, sorted(rows, key=repr))
    return snapshot


def _historical_context(db: sqlite3.Connection, route: sqlite3.Row,
                        config: dict[str, str]) -> dict[str, Any]:
    """保存時のv6 bindingを再構成する。現在権限/期限を移行条件にしない。"""
    from . import evaluation_authority as authority
    from . import execution_profiles, partitioned_plan_store, partitioned_run_store
    from . import resources, run_evidence
    from .partitioned_run_evidence import PartitionedRunEvidenceBook
    from .partitioned_run_contracts import bind_partitioned_run_manifest
    from .registry import validate_registry
    from .corpus import validate_case_set
    from .run_contracts import content_ref, validate_evaluation_contract
    from .partitioned_run_store import _load_manifest

    try:
        run_id = route["run_id"]
        manifest = _load_manifest(route)
        if (route["extension_digest"] != config["extension_digest"]
                or type(route["contract_generation"]) is not int or route["contract_generation"] != 1
                or type(route["permission_generation"]) is not int
                or type(route["created_at"]) is not int
                or not manifest["created_at"] <= route["created_at"] < manifest["deadline"]
                or manifest["purpose"] != "diagnostic" or manifest["baseline_ref"] is not None):
            raise _error("STORAGE_CORRUPT")
        if db.execute("SELECT 1 FROM eval_runs WHERE run_id=?", (run_id,)).fetchone() is not None:
            raise _error("STORAGE_CORRUPT")
        saved = db.execute("SELECT * FROM bound_runs WHERE run_id=?", (run_id,)).fetchone()
        if saved is None:
            raise _error("STORAGE_CORRUPT")
        receipt = PartitionedRunEvidenceBook._receipt(saved["bundle_json"], saved["bundle_digest"])
        profile = run_evidence._profile(run_evidence._load(saved["profile_json"], saved["profile_digest"]))
        if (saved["baseline_json"] is not None or saved["baseline_digest"] is not None
                or route["bundle_digest"] != saved["bundle_digest"]
                or run_evidence._pack(receipt)[1] != route["bundle_digest"]):
            raise _error("STORAGE_CORRUPT")

        history = db.execute(
            "SELECT * FROM eval_adoptions WHERE series_id=? AND generation=?",
            (route["contract_series_id"], route["contract_generation"]),
        ).fetchone()
        if history is None:
            raise _error("STORAGE_CORRUPT")
        contract = validate_evaluation_contract(authority._load_json(history, "payload_json", "digest"))
        if (history["series_id"] != route["contract_series_id"]
                or history["generation"] != route["contract_generation"]
                or contract["generation"] != 1
                or contract["comparison"]["mode"] != "not_applicable"
                or authority._packed(contract)[1] != history["digest"]
                or content_ref("evaluation_contract", contract["contract_id"], contract)
                != manifest["contract_ref"]):
            raise _error("STORAGE_CORRUPT")
        validation = authority._assert_contract_history(db, history, contract)
        validation_value = authority._load_json(validation, "payload_json", "digest")
        if validation_value.get("permission_generation") != route["permission_generation"]:
            raise _error("STORAGE_CORRUPT")

        resource_book = resources.ResourceBook(db)
        resource_run = resource_book._run(run_id)
        policy = resource_book._policy(resource_run)
        if (resource_run["manifest_digest"] != route["manifest_digest"]
                or resource_run["created_at"] != route["created_at"]
                or resource_run["deadline"] != manifest["deadline"]
                or resource_run["profile"] != manifest["profile"]
                or content_ref("policy_profile", policy["policy_id"], policy) != manifest["policy_ref"]
                or contract["policy_ref"] != manifest["policy_ref"]):
            raise _error("STORAGE_CORRUPT")

        registry = validate_registry(authority._object(db, contract["registry_ref"]))
        case_set = validate_case_set(authority._object(db, contract["case_set_ref"]))
        plan_row = db.execute(
            "SELECT * FROM partition_plan_commits WHERE plan_id=?", (manifest["plan_ref"]["id"],)
        ).fetchone()
        if (plan_row is None or plan_row["contract_series_id"] != route["contract_series_id"]
                or plan_row["contract_generation"] != 1
                or plan_row["permission_generation"] != route["permission_generation"]
                or plan_row["extension_digest"] != route["extension_digest"]):
            raise _error("STORAGE_CORRUPT")
        index, segments, plan = partitioned_plan_store._validate_committed_with_plan(
            db, plan_row, contract, route["permission_generation"], route["extension_digest"]
        )
        if content_ref(partitioned_plan_store.INDEX_KIND, plan_row["plan_id"], index) != manifest["plan_ref"]:
            raise _error("STORAGE_CORRUPT")
        rebound = bind_partitioned_run_manifest(
            manifest, contract, index, segments, policy, registry, case_set, baseline_context=None
        )
        if rebound != receipt or profile["isolation_digest"] != manifest["environment_ref"]["digest"]:
            raise _error("STORAGE_CORRUPT")
        internal = {
            "manifest": manifest, "contract": contract, "plan": plan, "policy": policy,
            "registry": registry, "case_set": case_set,
            "selected_controls": rebound["selected_controls"], "ci_eligible": False,
            "_partitioned_context": {
                "manifest": manifest, "contract": contract, "index": index,
                "segments": segments, "policy": policy, "registry": registry,
                "case_set": case_set,
            },
            "_partitioned_receipt": rebound,
        }
        execution_profiles.check_plan(profile, internal)
        # Validate all existing resource operation/ledger rows at their saved clock.
        clock_row = db.execute("SELECT value FROM resource_meta WHERE key='last_clock'").fetchone()
        if clock_row is None or type(clock_row[0]) is not int or clock_row[0] < -1:
            raise _error("STORAGE_CORRUPT")
        resource_book.snapshot(run_id, clock_row[0])
        return {"manifest": manifest, "contract": contract, "index": index,
                "segments": segments, "policy": policy, "registry": registry,
                "case_set": case_set, "execution_profile": profile,
                "baseline_context": None, "_internal_bound": internal,
                "_receipt": rebound}
    except migrations.MigrationError:
        raise
    except AdoptionError:
        raise _error("STORAGE_CORRUPT") from None
    except Exception:
        raise _error("STORAGE_CORRUPT") from None


def _historical_resolver(db: sqlite3.Connection, run_id: str, config: dict[str, str],
                         receipt: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    route = db.execute("SELECT * FROM eval_runs_v2 WHERE run_id=?", (run_id,)).fetchone()
    if route is None:
        raise _error("STORAGE_CORRUPT")
    fresh = _historical_context(db, route, config)
    if fresh["_receipt"] != receipt or fresh["execution_profile"] != profile:
        raise _error("STORAGE_CORRUPT")
    return {key: fresh[key] for key in (
        "manifest", "contract", "index", "segments", "policy", "registry",
        "case_set", "execution_profile", "baseline_context")}


class _HistoricalEvidenceBook:
    """Validate existing evidence using its saved v6 context, never current freshness."""

    @staticmethod
    def validate(db: sqlite3.Connection, run_id: str, route: sqlite3.Row,
                 context: dict[str, Any]) -> None:
        from .partitioned_run_evidence import PartitionedRunEvidenceBook
        from . import run_evidence

        fields = ("manifest", "contract", "index", "segments", "policy", "registry",
                  "case_set", "execution_profile", "baseline_context")
        resolver = lambda *_args: {key: context[key] for key in fields}
        try:
            clock = db.execute("SELECT value FROM run_evidence_meta WHERE key='last_clock'").fetchone()
            if clock is None or type(clock[0]) is not int or not -1 <= clock[0] <= run_evidence.MAX_INTEGER:
                raise _error("STORAGE_CORRUPT")
            book = PartitionedRunEvidenceBook(
                db, now=clock[0], allowed_bindings={run_id: route["bundle_digest"]},
                resolve_context=resolver,
            )
            row = book._run_row(db, run_id)
            bound, profile, baseline = book._resolve_run_context(db, row, clock[0])
            state = book._state(db, run_id)
            book._validate_store_contents(db, run_id, clock[0],
                                          row, bound, profile, baseline, state)
        except run_evidence.EvidenceError:
            raise _error("STORAGE_CORRUPT") from None


def _verify_v6_diagnostic_rows(db: sqlite3.Connection, config: dict[str, str]) -> None:
    try:
        for route in db.execute("SELECT * FROM eval_runs_v2 ORDER BY run_id"):
            run_id = route["run_id"]
            if type(run_id) is not str:
                raise _error("STORAGE_CORRUPT")
            context = _historical_context(db, route, config)
            _HistoricalEvidenceBook.validate(db, run_id, route, context)
        # A v2 receipt without its versioned route is ambiguous and cannot be migrated.
        for row in db.execute("SELECT run_id,bundle_json,bundle_digest FROM bound_runs"):
            try:
                value = json.loads(row["bundle_json"])
            except (TypeError, ValueError, UnicodeError):
                continue  # The existing v1 validator reports malformed bundles itself.
            if (type(value) is dict and value.get("schema_version") == 2
                    and value.get("kind") == "bound_partitioned_run"
                    and db.execute("SELECT 1 FROM eval_runs_v2 WHERE run_id=?", (row["run_id"],)).fetchone() is None):
                raise _error("STORAGE_CORRUPT")
    except migrations.MigrationError:
        raise
    except sqlite3.Error:
        raise _error("STORAGE_CORRUPT") from None


def _verify_v6_state(db: sqlite3.Connection, expected_source_digest: str,
                     current_pair: tuple[str, str]) -> dict[str, Any]:
    try:
        version = db.execute("PRAGMA user_version").fetchone()[0]
    except sqlite3.Error:
        raise _error("UNSUPPORTED_STORE") from None
    if version != _V6_VERSION:
        raise _error("UNSUPPORTED_STORE")
    columns = _v6_columns()
    migrations._verify_columns(db, columns)
    config = migrations._config(db)
    if set(config) != _EXPECTED_CONFIG_KEYS or config.get("bootstrap_digest") != _PINNED_BOOTSTRAP:
        raise _error("CONFIG_MISMATCH")
    pair = (config.get("extension_digest"), config.get("validator_digest"))
    allowed = {(_PINNED_V6_EXTENSION, _PINNED_V6_VALIDATOR), current_pair}
    if pair not in allowed or expected_source_digest != pair[0]:
        raise _error("CONFIG_MISMATCH")
    meta = migrations._meta(db, _V6_VERSION)
    if migrations._digest(initial_policy_profile()) != _PINNED_BOOTSTRAP:
        raise _error("BOOTSTRAP_MISMATCH")

    current_v5_pair = run_migrations._current_v5_pair()
    before = _snapshot_rows(db, columns)
    db.execute("SAVEPOINT gah_partitioned_v6_verify")
    try:
        migrations._verify_v3_json_rows(db, adopted=True, following=True)
        migrations._verify_v4_candidate_rows(db, adopted=True, regression=True,
            cancellation=True, following=True, scoped=True, expected_schema_version=_V6_VERSION)
        # Preserve stale v5 plans verbatim while validating all bytes and bindings.
        # The verifier keeps its old exact-config default for every other caller.
        for table in ("partition_plan_upload", "partition_plan_commits"):
            for row in db.execute(f'SELECT extension_digest FROM "{table}"'):
                saved_digest = row["extension_digest"]
                if type(saved_digest) is not str or saved_digest not in {
                        run_migrations._PINNED_V5_EXTENSION, current_v5_pair[0],
                        config["extension_digest"]}:
                    raise _error("STORAGE_CORRUPT")
        run_migrations._verify_partitioned_plan_rows(
            db, config, allowed_extension_digests={run_migrations._PINNED_V5_EXTENSION,
                current_v5_pair[0], config["extension_digest"]})
        _verify_v6_diagnostic_rows(db, config)
        try:
            from . import baseline_refresh_migration
            baseline_refresh_migration.verify(db, meta["last_clock"], following=True)
        except AdoptionError:
            raise _error("STORAGE_CORRUPT") from None
        if _snapshot_rows(db, columns) != before:
            raise _error("STORAGE_CORRUPT")
        if run_migrations._current_v5_pair() != current_v5_pair:
            raise _error("SOURCE_CHANGED")
    finally:
        db.execute("ROLLBACK TO gah_partitioned_v6_verify")
        db.execute("RELEASE gah_partitioned_v6_verify")
    return {"config": config, "meta": meta, "snapshot": before, "columns": columns,
            "v5_pair": current_v5_pair}


def migrate_partitioned_corpus_store_v6_to_v7(path: str | Path, *, expected_source_digest: str) -> dict[str, Any]:
    """既知またはentry照合済みv6 pairに4つのcorpus表だけを追加する。"""
    try:
        from .contracts import require_digest
        require_digest(expected_source_digest)
    except (ImportError, TypeError, ValueError):
        raise _error("CONFIG_MISMATCH") from None
    target = _validate_path(path)
    entry_pair = _current_v6_pair()
    db: sqlite3.Connection | None = None
    try:
        db = connect_sqlite(str(target), isolation_level=None, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("BEGIN IMMEDIATE")
        verified = _verify_v6_state(db, expected_source_digest, entry_pair)
        if (_current_v6_pair() != entry_pair
                or run_migrations._current_v5_pair() != verified["v5_pair"]):
            raise _error("SOURCE_CHANGED")
        try:
            from . import partitioned_corpus_authority, partitioned_corpus_store
            extension_type = partitioned_corpus_authority.PartitionedCorpusEvaluationExtension
            extension = extension_type()
            new_digest = extension.digest
            from .contracts import require_digest
            if getattr(extension, "schema_version", None) != _V7_VERSION or type(new_digest) is not str:
                raise _error("MIGRATION_UNAVAILABLE")
            try:
                require_digest(new_digest)
            except (TypeError, ValueError):
                raise _error("MIGRATION_UNAVAILABLE") from None
            target_tables = {**verified["columns"], **partitioned_corpus_store.TABLES}
        except migrations.MigrationError:
            raise
        except Exception:
            raise _error("MIGRATION_UNAVAILABLE") from None
        partitioned_corpus_store.create_schema(db)
        migrations._verify_columns(db, target_tables)
        for table in partitioned_corpus_store.TABLES:
            if db.execute(f'SELECT 1 FROM "{table}" LIMIT 1').fetchone() is not None:
                raise _error("STORAGE_CORRUPT")
        if _snapshot_rows(db, verified["columns"]) != verified["snapshot"]:
            raise _error("STORAGE_CORRUPT")
        # Confirm every new table has the declared owner/FK topology and no rows.
        fk_upload = db.execute("PRAGMA foreign_key_list('partition_scale_corpus_segments')").fetchall()
        fk_committed = db.execute("PRAGMA foreign_key_list('partition_scale_corpus_committed_segments')").fetchall()
        if (len(fk_upload) != 1
                or (fk_upload[0][2], fk_upload[0][3], fk_upload[0][4], fk_upload[0][6])
                   != ("partition_scale_corpus_upload", "upload_id", "upload_id", "CASCADE")):
            raise _error("UNSUPPORTED_STORE")
        committed_fk = sorted(
            ((row[3], row[4], row[6]) for row in fk_committed
             if row[2] == "partition_scale_corpus_commits"), key=lambda value: value[0]
        )
        if committed_fk != [
                ("corpus_id", "corpus_id", "CASCADE"),
                ("policy_generation", "policy_generation", "CASCADE"),
                ("policy_series_id", "policy_series_id", "CASCADE")]:
            raise _error("UNSUPPORTED_STORE")

        db.execute("UPDATE adoption_meta SET value=? WHERE key='schema_version'", (_V7_VERSION,))
        db.execute("UPDATE adoption_config SET value=? WHERE key='extension_digest'", (new_digest,))
        db.execute("PRAGMA user_version=7")
        meta_after = migrations._meta(db, _V7_VERSION)
        if (meta_after["last_clock"] != verified["meta"]["last_clock"]
                or meta_after["permission_generation"] != verified["meta"]["permission_generation"]):
            raise _error("STORAGE_CORRUPT")
        config_after = migrations._config(db)
        if config_after != {**verified["config"], "extension_digest": new_digest}:
            raise _error("STORAGE_CORRUPT")
        if db.execute("PRAGMA user_version").fetchone()[0] != _V7_VERSION:
            raise _error("STORAGE_CORRUPT")
        after_rows = _snapshot_rows(db, verified["columns"])
        unchanged = {name: rows for name, rows in verified["snapshot"].items()
                     if name not in {"adoption_meta", "adoption_config"}}
        after_unchanged = {name: rows for name, rows in after_rows.items()
                           if name not in {"adoption_meta", "adoption_config"}}
        if after_unchanged != unchanged:
            raise _error("STORAGE_CORRUPT")
        if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise _error("STORAGE_CORRUPT")
        if (_current_v6_pair() != entry_pair
                or run_migrations._current_v5_pair() != verified["v5_pair"]
                or extension_type().digest != new_digest):
            raise _error("SOURCE_CHANGED")
        db.commit()
        return {"schema_version": _V7_VERSION, "kind": "partitioned_corpus_migration_result",
            "changed": True, "predecessor_extension_digest": expected_source_digest,
            "extension_digest": new_digest, "old_run_and_plan_rows_preserved": True,
            "old_v6_plan_rows_require_new_upload": True, "ci_eligible": False}
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


__all__ = ["migrate_partitioned_corpus_store_v6_to_v7"]
