"""既知SQLite AdoptionStoreの明示移行を補助操作として包む境界。

対象はworkspace内で明示されたhost-accessible SQLite fileだけであり、Dockerの
名前付きvolume、任意shell、任意runner、外部参照には接続しない。更新は既存
adoption_migrationsのBEGIN IMMEDIATE/SAVEPOINT/commitへ一度だけ委譲する。
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import sqlite3
from .sqlite_limits import inspect_sqlite_limits
import time
import uuid
from typing import Any, Callable

from . import adoption_migrations as migrations
from .contracts import (
    ContractError, MAX_INTEGER, require_digest, require_id, require_ref,
    require_text, require_uint,
)
from .productization import (
    content_ref, operation_result, read_document,
    validate_plan as _common_validate_plan, validate_operation_result, workspace_path, write_document,
)
from .productization_journal import OperationJournal
from .wire import canonical_bytes


MIGRATION_COMMAND_PREVIEW = "ops.migrate.preview"
MIGRATION_COMMAND_APPLY = "ops.migrate.apply"
MIGRATION_PLAN_KIND = "migration_plan"
MIGRATION_RESULT_KIND = "migration_operation_result"
MIGRATION_SOURCE_PURPOSE = "migration_source"
MIGRATION_REQUIREMENTS_PURPOSE = "migration_requirements"
MIGRATION_PLAN_TTL_SECONDS = 24 * 60 * 60
MIGRATION_REQUIREMENT_IDS = ["GAH-PR12"]
MIGRATION_ALLOWED_SCOPE = ["known_adoption_migration", "extension_metadata"]
MIGRATION_REF_ROOT = Path(".ga") / "operations" / "refs"
MIGRATION_ROOT = Path(".ga") / "operations" / "migrations"
MIGRATION_JOURNAL = Path(".ga") / "operations" / "migration-journal.sqlite"

_STATE_FIELDS = {
    "schema_version", "bootstrap_digest", "validator_digest",
    "extension_digest", "permission_generation", "last_clock", "logical_digest",
}
_REFERENCE_FIELDS = {"table_digests", "logical_digest"}
_UNSETTLED_FIELDS = {"open_ids", "cancelled_ids"}
_PLAN_PAYLOAD_FIELDS = {
    "database_path", "source_ref", "snapshot_path", "dry_run_path", "implementation_digest",
    "source_schema_version", "target_schema_version",
    "table_counts", "reference_digests", "unsettled_runs",
    "write_stop_required", "snapshot_required", "allowed_scope",
    "source_size_bytes", "source_digest", "source_state", "target_state",
}
_RESULT_FIELDS = {
    "schema_version", "kind", "id", "plan_ref", "source_ref", "snapshot_ref",
    "database_path", "source_schema_version", "target_schema_version",
    "before_table_counts", "after_table_counts",
    "before_reference_digests", "after_reference_digests",
    "before_unsettled_runs", "after_unsettled_runs",
    "predecessor_extension_digest", "predecessor_validator_digest",
    "extension_digest", "changed", "write_stop_confirmed",
    "metadata_only", "external_refs_verified", "product_run_authority",
    "ci_eligible", "checked_at",
}
_MANIFEST_FIELDS = {"schema_version", "kind", "id", "purpose", "files"}
_MANIFEST_FILE_FIELDS = {"path", "digest", "size_bytes"}
_VERSION_TABLES = {2: migrations._V2_COLUMNS, 3: migrations._V3_COLUMNS,
                   4: migrations._V4_COLUMNS}
_V4_PREVIOUS_EXTENSIONS = {
    migrations._V4_PREDECESSOR_EXTENSION_DIGEST,
    migrations._V4_ADOPTION_EXTENSION_DIGEST,
    migrations._V4_REGRESSION_EXTENSION_DIGEST,
    migrations._V4_CANCELLATION_EXTENSION_DIGEST,
    migrations._V4_RECOVERY_EXTENSION_DIGEST,
    migrations._V4_REFRESH_EXTENSION_DIGEST,
    migrations._V4_SUPERVISOR_EXTENSION_DIGEST,
    migrations._V4_LLM_INITIAL_EXTENSION_DIGEST,
} | set(migrations._FOLLOWING_VERSIONS)
_V4_VALIDATORS = {
    migrations._V2_VALIDATOR_DIGEST, migrations._V4_RUNTIME_VALIDATOR_DIGEST,
}


class MigrationOperationError(ValueError):
    """移行境界の内部エラーを固定codeで表す。"""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _fail(code: str) -> MigrationOperationError:
    return MigrationOperationError(code)


def _now(clock: Callable[[], Any] | None) -> int:
    try:
        value = int(time.time()) if clock is None else clock()
    except Exception as exc:
        raise _fail("CLOCK_UNAVAILABLE") from exc
    if type(value) is not int or not 0 <= value <= MAX_INTEGER:
        raise _fail("CLOCK_UNAVAILABLE")
    return value


def _valid_id(value: Any) -> bool:
    try:
        require_id(value)
    except ContractError:
        return False
    return True


def _request_id(value: Any, prefix: str) -> str | None:
    if value is None:
        return f"{prefix}-{uuid.uuid4().hex}"
    return value if _valid_id(value) else None


def _relpath(value: Any) -> str:
    if type(value) is not str or not value or len(value) > 512:
        raise _fail("PATH_REJECTED")
    try:
        path = Path(value)
    except (TypeError, ValueError):
        raise _fail("PATH_REJECTED") from None
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise _fail("PATH_REJECTED")
    if any(part in {"", "."} for part in path.parts):
        raise _fail("PATH_REJECTED")
    try:
        require_text(value, maximum=512)
    except ContractError:
        raise _fail("PATH_REJECTED") from None
    return path.as_posix()


def _workspace(value: str | Path) -> Path:
    try:
        base = Path(value).absolute()
        if not base.is_dir():
            raise _fail("RUNTIME_UNAVAILABLE")
        workspace_path(base, ".migration-boundary")
        return base
    except MigrationOperationError:
        raise
    except (OSError, TypeError, ValueError, ContractError) as exc:
        raise _fail("PATH_REJECTED") from exc


def _child(base: Path, value: str | Path) -> Path:
    try:
        return workspace_path(base, value)
    except ContractError as exc:
        raise _fail(getattr(exc, "code", "PATH_REJECTED")) from exc


def _relative_to(base: Path, target: Path) -> str:
    try:
        result = target.relative_to(base)
    except ValueError:
        raise _fail("PATH_REJECTED") from None
    if not result.parts:
        raise _fail("PATH_REJECTED")
    return _relpath(result.as_posix())


def _ensure_parent(base: Path, value: str | Path) -> Path:
    target = _child(base, value)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.parent != base:
            workspace_path(base, _relative_to(base, target.parent))
        if target.parent.is_symlink():
            raise _fail("PATH_REJECTED")
    except MigrationOperationError:
        raise
    except (OSError, ValueError, ContractError) as exc:
        raise _fail("IO_ERROR") from exc
    return target


def _save_doc(base: Path, value: str | Path, document: dict) -> dict:
    target = _ensure_parent(base, value)
    try:
        return write_document(base, _relative_to(base, target), document)
    except ContractError as exc:
        raise _fail(getattr(exc, "code", "IO_ERROR")) from exc
    except (TypeError, ValueError, OSError) as exc:
        raise _fail("IO_ERROR") from exc


def _ref_path(ref: dict) -> Path:
    try:
        require_ref(ref)
    except ContractError as exc:
        raise _fail("REFERENCE_MISMATCH") from exc
    return MIGRATION_REF_ROOT / ref["kind"] / ref["id"] / (ref["digest"] + ".json")


def _validate_manifest(value: Any, *, purpose: str, source: bool = False) -> dict:
    if type(value) is not dict or set(value) != _MANIFEST_FIELDS:
        raise _fail("REFERENCE_MISMATCH")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise _fail("REFERENCE_MISMATCH")
    if (value["kind"] != "snapshot_manifest" or not _valid_id(value["id"])
            or value["purpose"] != purpose):
        raise _fail("REFERENCE_MISMATCH")
    files = value["files"]
    if type(files) is not list or len(files) != (3 if source else 1):
        raise _fail("REFERENCE_MISMATCH")
    seen = set()
    for item in files:
        if type(item) is not dict or set(item) != _MANIFEST_FILE_FIELDS:
            raise _fail("REFERENCE_MISMATCH")
        path = _relpath(item["path"])
        require_digest(item["digest"])
        require_uint(item["size_bytes"])
        if path in seen:
            raise _fail("REFERENCE_MISMATCH")
        seen.add(path)
    return value


def _save_ref(base: Path, value: dict) -> dict:
    purpose = value.get("purpose")
    manifest = _validate_manifest(
        value, purpose=purpose,
        source=purpose == MIGRATION_SOURCE_PURPOSE,
    )
    ref = content_ref("snapshot_manifest", manifest["id"], manifest)
    wrapper = {
        "schema_version": 1, "kind": "setup_ref_artifact", "id": manifest["id"],
        "ref": ref, "value": manifest, "resolver": None, "ci_eligible": False,
    }
    _save_doc(base, _ref_path(ref), wrapper)
    return ref


def _resolve_ref(base: Path, ref: dict, *, purpose: str,
                 source: bool = False) -> dict:
    try:
        require_ref(ref)
    except ContractError as exc:
        raise _fail("REFERENCE_MISMATCH") from exc
    if ref["kind"] != "snapshot_manifest":
        raise _fail("REFERENCE_MISMATCH")
    try:
        wrapper = read_document(
            base, _relative_to(base, _child(base, _ref_path(ref))),
        )
    except (ContractError, MigrationOperationError) as exc:
        raise _fail(getattr(exc, "code", "REFERENCE_MISMATCH")) from exc
    fields = {"schema_version", "kind", "id", "ref", "value", "resolver", "ci_eligible"}
    if (type(wrapper) is not dict or set(wrapper) != fields
            or type(wrapper["schema_version"]) is not int or wrapper["schema_version"] != 1
            or wrapper["kind"] != "setup_ref_artifact" or wrapper["id"] != ref["id"]
            or wrapper["ref"] != ref or wrapper["resolver"] is not None
            or wrapper["ci_eligible"] is not False):
        raise _fail("REFERENCE_MISMATCH")
    value = _validate_manifest(wrapper["value"], purpose=purpose, source=source)
    if content_ref("snapshot_manifest", value["id"], value) != ref:
        raise _fail("REFERENCE_MISMATCH")
    return value


def _hash_file(path: Path) -> tuple[int, str]:
    try:
        info = path.lstat()
        if (not path.is_file() or path.is_symlink()
                or getattr(info, "st_file_attributes", 0) & 0x400):
            raise _fail("PATH_REJECTED")
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as stream:
            while True:
                block = stream.read(1024 * 1024)
                if not block:
                    break
                size += len(block)
                if size > MAX_INTEGER:
                    raise _fail("IO_ERROR")
                digest.update(block)
        return size, digest.hexdigest()
    except MigrationOperationError:
        raise
    except (OSError, ValueError) as exc:
        raise _fail("IO_ERROR") from exc


def _copy_stable(source: Path, target: Path, before: tuple[int, str]) -> None:
    created = False
    try:
        with source.open("rb") as src, target.open("xb") as dst:
            created = True
            while True:
                block = src.read(1024 * 1024)
                if not block:
                    break
                dst.write(block)
            dst.flush()
            os.fsync(dst.fileno())
        if _hash_file(source) != before or _hash_file(target) != before:
            raise _fail("BINDING_MISMATCH")
    except MigrationOperationError:
        if created:
            try:
                target.unlink()
            except OSError:
                pass
        raise
    except (OSError, ValueError) as exc:
        if created:
            try:
                target.unlink()
            except OSError:
                pass
        raise _fail("IO_ERROR") from exc


def _sql_value(value: Any) -> Any:
    if value is None or type(value) in {int, str}:
        return value
    if type(value) is bytes:
        return {"bytes": value.hex()}
    raise _fail("STORAGE_CORRUPT")


def _connect_read(path: Path) -> sqlite3.Connection:
    try:
        db = sqlite3.connect(str(path), timeout=5)
        inspect_sqlite_limits(db)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        return db
    except sqlite3.Error as exc:
        raise _fail("IO_ERROR") from exc


def _config_meta(db: sqlite3.Connection, version: int) -> tuple[dict, dict]:
    try:
        configs = db.execute("SELECT key,value FROM adoption_config").fetchall()
        metas = db.execute("SELECT key,value FROM adoption_meta").fetchall()
    except sqlite3.Error as exc:
        raise _fail("SCHEMA_UNSUPPORTED") from exc
    if (len(configs) != 3 or {r[0] for r in configs} !=
            {"bootstrap_digest", "validator_digest", "extension_digest"}):
        raise _fail("SCHEMA_UNSUPPORTED")
    if (len(metas) != 3 or {r[0] for r in metas} !=
            {"schema_version", "last_clock", "permission_generation"}):
        raise _fail("SCHEMA_UNSUPPORTED")
    config, meta = ({r[0]: r[1] for r in configs},
                    {r[0]: r[1] for r in metas})
    for item in config.values():
        try:
            require_digest(item)
        except ContractError:
            raise _fail("SCHEMA_UNSUPPORTED") from None
    if (type(meta["schema_version"]) is not int
            or meta["schema_version"] != version
            or type(meta["last_clock"]) is not int
            or not -1 <= meta["last_clock"] <= MAX_INTEGER):
        raise _fail("SCHEMA_UNSUPPORTED")
    try:
        require_uint(meta["permission_generation"])
    except ContractError:
        raise _fail("SCHEMA_UNSUPPORTED") from None
    return config, meta


def _allowed_config(version: int, config: dict) -> None:
    if version == 2:
        expected = (migrations._V2_BOOTSTRAP_DIGEST, migrations._V2_VALIDATOR_DIGEST,
                    migrations._V2_EXTENSION_DIGEST)
        if tuple(config[x] for x in ("bootstrap_digest", "validator_digest",
                                     "extension_digest")) != expected:
            raise _fail("SCHEMA_UNSUPPORTED")
        return
    if version == 3:
        expected = (migrations._V2_BOOTSTRAP_DIGEST, migrations._V2_VALIDATOR_DIGEST,
                    migrations._V3_EXTENSION_DIGEST)
        if tuple(config[x] for x in ("bootstrap_digest", "validator_digest",
                                     "extension_digest")) != expected:
            raise _fail("SCHEMA_UNSUPPORTED")
        return
    if config["bootstrap_digest"] != migrations._V2_BOOTSTRAP_DIGEST:
        raise _fail("SCHEMA_UNSUPPORTED")
    ext = config["extension_digest"]
    if ext not in _V4_PREVIOUS_EXTENSIONS:
        raise _fail("SCHEMA_UNSUPPORTED")
    allowed = _V4_VALIDATORS if (
        ext in migrations._FOLLOWING_VERSIONS
        or ext == migrations._V4_LLM_INITIAL_EXTENSION_DIGEST
    ) else {migrations._V2_VALIDATOR_DIGEST}
    if config["validator_digest"] not in allowed:
        raise _fail("SCHEMA_UNSUPPORTED")


def _current_extension_digest() -> str:
    try:
        digest = migrations._trusted_extension().digest
        require_digest(digest)
        return digest
    except (migrations.MigrationError, ContractError, AttributeError,
            TypeError, ValueError, ImportError):
        raise _fail("UNSUPPORTED_CAPABILITY") from None


def _verify_rows(db: sqlite3.Connection, version: int, config: dict) -> None:
    try:
        if version == 2:
            migrations._verify_json_rows(db)
        elif version == 3:
            migrations._verify_v3_json_rows(db)
        else:
            ext = config["extension_digest"]
            migrations._verify_v3_json_rows(
                db, adopted=ext in _V4_PREVIOUS_EXTENSIONS - {
                    migrations._V4_PREDECESSOR_EXTENSION_DIGEST,
                    migrations._V4_LLM_INITIAL_EXTENSION_DIGEST,
                }, following=ext in migrations._FOLLOWING_VERSIONS,
            )
    except migrations.MigrationError as exc:
        raise _fail("STORAGE_CORRUPT") from exc
    except (ContractError, KeyError, TypeError, ValueError, sqlite3.Error) as exc:
        raise _fail("STORAGE_CORRUPT") from exc


def _row_digest(db: sqlite3.Connection, table: str) -> str:
    try:
        info = list(db.execute('PRAGMA table_info("' + table + '")'))
        columns = [row[1] for row in info]
        rows = []
        for row in db.execute('SELECT * FROM "' + table + '"'):
            rows.append({"columns": columns, "values": [_sql_value(x) for x in row]})
        rows.sort(key=canonical_bytes)
        return hashlib.sha256(canonical_bytes(rows)).hexdigest()
    except MigrationOperationError:
        raise
    except (sqlite3.Error, TypeError, ValueError) as exc:
        raise _fail("STORAGE_CORRUPT") from exc


def _logical_digest(version: int, config: dict, meta: dict,
                    table_digests: dict[str, str]) -> str:
    try:
        return hashlib.sha256(canonical_bytes({
            "schema_version": version, "config": config, "meta": meta,
            "table_digests": table_digests,
        })).hexdigest()
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise _fail("STORAGE_CORRUPT") from exc


def _unsettled(db: sqlite3.Connection) -> dict:
    try:
        rows = db.execute(
            "SELECT run_id,cancelled,closed_at FROM resource_runs ORDER BY run_id",
        ).fetchall()
    except sqlite3.Error as exc:
        raise _fail("SCHEMA_UNSUPPORTED") from exc
    opened, cancelled = [], []
    for row in rows:
        if not _valid_id(row[0]) or type(row[1]) is not int or row[1] not in {0, 1}:
            raise _fail("STORAGE_CORRUPT")
        if row[2] is not None and type(row[2]) is not int:
            raise _fail("STORAGE_CORRUPT")
        if row[2] is None:
            opened.append(row[0])
        if row[1] == 1:
            cancelled.append(row[0])
    return {"open_ids": opened, "cancelled_ids": cancelled}


def _inspect(path: Path, *, allow_current: bool = False) -> dict:
    if not path.is_file():
        raise _fail("STORE_MISSING")
    db = _connect_read(path)
    try:
        try:
            version = db.execute("PRAGMA user_version").fetchone()[0]
        except sqlite3.Error as exc:
            raise _fail("SCHEMA_UNSUPPORTED") from exc
        if type(version) is not int or version not in _VERSION_TABLES:
            raise _fail("SCHEMA_UNSUPPORTED")
        try:
            migrations._verify_columns(db, _VERSION_TABLES[version])
        except migrations.MigrationError as exc:
            raise _fail("SCHEMA_UNSUPPORTED") from exc
        config, meta = _config_meta(db, version)
        if version == 4 and config["extension_digest"] == _current_extension_digest():
            if not allow_current:
                raise _fail("SCHEMA_UNSUPPORTED")
        else:
            _allowed_config(version, config)
        _verify_rows(db, version, config)
        counts, digests = {}, {}
        for table in sorted(_VERSION_TABLES[version]):
            try:
                count = db.execute('SELECT COUNT(*) FROM "' + table + '"').fetchone()[0]
            except sqlite3.Error as exc:
                raise _fail("STORAGE_CORRUPT") from exc
            if type(count) is not int or not 0 <= count <= MAX_INTEGER:
                raise _fail("STORAGE_CORRUPT")
            counts[table], digests[table] = count, _row_digest(db, table)
        logical = _logical_digest(version, config, meta, digests)
        return {
            "schema_version": version,
            "table_counts": counts,
            "reference_digests": {"table_digests": digests, "logical_digest": logical},
            "unsettled_runs": _unsettled(db),
            "state": {
                "schema_version": version,
                "bootstrap_digest": config["bootstrap_digest"],
                "validator_digest": config["validator_digest"],
                "extension_digest": config["extension_digest"],
                "permission_generation": meta["permission_generation"],
                "last_clock": meta["last_clock"], "logical_digest": logical,
            },
        }
    finally:
        db.close()


def inspect_source(workspace: str | Path, database: str | Path) -> dict:
    """host-accessible SQLite sourceの固定metadataをfreshに返す。"""
    base = _workspace(workspace)
    return _inspect(_child(base, database))


def _validate_state(value: Any, version: int) -> None:
    if type(value) is not dict or set(value) != _STATE_FIELDS:
        raise ContractError("INVALID_INPUT")
    if type(value["schema_version"]) is not int or value["schema_version"] != version:
        raise ContractError("INVALID_INPUT")
    for name in ("bootstrap_digest", "validator_digest", "extension_digest", "logical_digest"):
        require_digest(value[name])
    require_uint(value["permission_generation"])
    if (type(value["last_clock"]) is not int
            or not -1 <= value["last_clock"] <= MAX_INTEGER):
        raise ContractError("INVALID_INPUT")


def _validate_counts(value: Any, version: int) -> None:
    if type(value) is not dict or set(value) != {"before", "after"}:
        raise ContractError("INVALID_INPUT")
    for name, expected in (("before", _VERSION_TABLES[version]),
                           ("after", migrations._V4_COLUMNS)):
        item = value[name]
        if type(item) is not dict or set(item) != set(expected):
            raise ContractError("INVALID_INPUT")
        for count in item.values():
            require_uint(count)


def _validate_refs(value: Any, version: int) -> None:
    if type(value) is not dict or set(value) != {"before", "after"}:
        raise ContractError("INVALID_INPUT")
    for name, expected in (("before", _VERSION_TABLES[version]),
                           ("after", migrations._V4_COLUMNS)):
        item = value[name]
        if type(item) is not dict or set(item) != _REFERENCE_FIELDS:
            raise ContractError("INVALID_INPUT")
        if (type(item["table_digests"]) is not dict
                or set(item["table_digests"]) != set(expected)):
            raise ContractError("INVALID_INPUT")
        for digest in item["table_digests"].values():
            require_digest(digest)
        require_digest(item["logical_digest"])


def _validate_unsettled(value: Any) -> None:
    if type(value) is not dict or set(value) != {"before", "after"}:
        raise ContractError("INVALID_INPUT")
    for item in value.values():
        if type(item) is not dict or set(item) != _UNSETTLED_FIELDS:
            raise ContractError("INVALID_INPUT")
        for ids in item.values():
            if (type(ids) is not list or len(ids) > 100000
                    or len(ids) != len(set(ids))
                    or any(not _valid_id(x) for x in ids)):
                raise ContractError("INVALID_INPUT")


def _validate_payload(value: Any) -> dict:
    if type(value) is not dict or set(value) != _PLAN_PAYLOAD_FIELDS:
        raise ContractError("INVALID_INPUT")
    _relpath(value["database_path"])
    _relpath(value["snapshot_path"])
    _relpath(value["dry_run_path"])
    require_digest(value["implementation_digest"])
    if len({value["database_path"], value["snapshot_path"], value["dry_run_path"]}) != 3:
        raise ContractError("BINDING_MISMATCH")
    try:
        require_ref(value["source_ref"])
    except ContractError:
        raise ContractError("BINDING_MISMATCH") from None
    if value["source_ref"]["kind"] != "snapshot_manifest":
        raise ContractError("BINDING_MISMATCH")
    source_version = value["source_schema_version"]
    if type(source_version) is not int or source_version not in {2, 3, 4}:
        raise ContractError("INVALID_INPUT")
    if type(value["target_schema_version"]) is not int or value["target_schema_version"] != 4:
        raise ContractError("INVALID_INPUT")
    _validate_counts(value["table_counts"], source_version)
    _validate_refs(value["reference_digests"], source_version)
    _validate_unsettled(value["unsettled_runs"])
    if (value["write_stop_required"] is not True
            or value["snapshot_required"] is not True
            or value["allowed_scope"] != MIGRATION_ALLOWED_SCOPE):
        raise ContractError("INVALID_INPUT")
    require_uint(value["source_size_bytes"])
    require_digest(value["source_digest"])
    _validate_state(value["source_state"], source_version)
    _validate_state(value["target_state"], 4)
    return value


def validate_migration_plan(value: dict, *, now: int | None = None) -> dict:
    """共通plan外枠と専用payloadを同時に検査する。"""
    try:
        result = _common_validate_plan(
            value, kind=MIGRATION_PLAN_KIND,
            payload_validator=_validate_payload, now=now,
        )
    except ContractError as exc:
        raise MigrationOperationError(getattr(exc, "code", "INVALID_INPUT")) from exc
    if result["source_ref"] != result["payload"]["source_ref"]:
        raise _fail("REFERENCE_MISMATCH")
    return result


def _validate_result(value: Any) -> dict:
    if type(value) is not dict or set(value) != _RESULT_FIELDS:
        raise _fail("INVALID_INPUT")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise _fail("INVALID_INPUT")
    if value["kind"] != MIGRATION_RESULT_KIND or not _valid_id(value["id"]):
        raise _fail("INVALID_INPUT")
    try:
        for field in ("plan_ref", "source_ref", "snapshot_ref"):
            require_ref(value[field])
    except ContractError:
        raise _fail("BINDING_MISMATCH") from None
    if (value["plan_ref"]["kind"] != MIGRATION_PLAN_KIND
            or value["source_ref"]["kind"] != "snapshot_manifest"
            or value["snapshot_ref"]["kind"] != "snapshot_manifest"):
        raise _fail("BINDING_MISMATCH")
    _relpath(value["database_path"])
    source_version = value["source_schema_version"]
    if type(source_version) is not int or source_version not in {2, 3, 4}:
        raise _fail("INVALID_INPUT")
    if type(value["target_schema_version"]) is not int or value["target_schema_version"] != 4:
        raise _fail("INVALID_INPUT")
    _validate_counts({"before": value["before_table_counts"],
                      "after": value["after_table_counts"]}, source_version)
    _validate_refs({"before": value["before_reference_digests"],
                    "after": value["after_reference_digests"]}, source_version)
    for name in ("before_unsettled_runs", "after_unsettled_runs"):
        item = value[name]
        if type(item) is not dict or set(item) != _UNSETTLED_FIELDS:
            raise _fail("INVALID_INPUT")
        _validate_unsettled({"before": item, "after": item})
    for name in ("predecessor_extension_digest",
                 "predecessor_validator_digest", "extension_digest"):
        require_digest(value[name])
    for name in ("changed", "write_stop_confirmed", "metadata_only",
                 "external_refs_verified", "product_run_authority", "ci_eligible"):
        if type(value[name]) is not bool:
            raise _fail("INVALID_INPUT")
    if (value["changed"] is not True or value["write_stop_confirmed"] is not True
            or value["metadata_only"] is not True
            or value["external_refs_verified"] is not False
            or value["product_run_authority"] is not False
            or value["ci_eligible"] is not False):
        raise _fail("BINDING_MISMATCH")
    require_uint(value["checked_at"])
    return value


def _plan_ref(plan: dict) -> dict:
    return content_ref(MIGRATION_PLAN_KIND, plan["id"], plan)


def _result_id(plan: dict, after: dict) -> str:
    return "migration-result-" + hashlib.sha256(canonical_bytes({
        "plan_ref": _plan_ref(plan), "after": after,
    })).hexdigest()[:40]


def _build_result(plan: dict, after: dict, checked_at: int) -> dict:
    payload = plan["payload"]
    value = {
        "schema_version": 1, "kind": MIGRATION_RESULT_KIND,
        "id": _result_id(plan, after), "plan_ref": _plan_ref(plan),
        "source_ref": plan["source_ref"], "snapshot_ref": plan["source_ref"],
        "database_path": payload["database_path"],
        "source_schema_version": payload["source_schema_version"],
        "target_schema_version": 4,
        "before_table_counts": payload["table_counts"]["before"],
        "after_table_counts": after["table_counts"],
        "before_reference_digests": payload["reference_digests"]["before"],
        "after_reference_digests": after["reference_digests"],
        "before_unsettled_runs": payload["unsettled_runs"]["before"],
        "after_unsettled_runs": after["unsettled_runs"],
        "predecessor_extension_digest": payload["source_state"]["extension_digest"],
        "predecessor_validator_digest": payload["source_state"]["validator_digest"],
        "extension_digest": after["state"]["extension_digest"],
        "changed": True, "write_stop_confirmed": True, "metadata_only": True,
        "external_refs_verified": False, "product_run_authority": False,
        "ci_eligible": False, "checked_at": checked_at,
    }
    return _validate_result(value)


def _save_result(base: Path, result: dict) -> dict:
    relative = MIGRATION_ROOT / "results" / (result["id"] + ".json")
    _save_doc(base, relative, result)
    return content_ref(MIGRATION_RESULT_KIND, result["id"], result)


def _read_at(base: Path, relative: str | Path, ref: dict | None) -> dict | None:
    if ref is None:
        return None
    try:
        value = read_document(base, _relative_to(base, _child(base, relative)))
        if content_ref(value["kind"], value["id"], value) != ref:
            return None
        return value
    except (ContractError, MigrationOperationError, KeyError, TypeError, ValueError):
        return None


def _snapshot_path(request_id: str, source_info: dict) -> str:
    digest = hashlib.sha256(canonical_bytes({
        "request_id": request_id, "source_info": source_info,
    })).hexdigest()[:40]
    return (MIGRATION_ROOT / "snapshots" / ("snapshot-" + digest + ".sqlite")).as_posix()


def _implementation_digest() -> str:
    root = Path(__file__).resolve().parents[2]
    selected = ("src/gah/operations_migration.py", "src/gah/adoption_migrations.py",
                "src/gah/productization.py", "src/gah/productization_journal.py",
                "docs/productization-requirements.md")
    return hashlib.sha256(canonical_bytes({name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                                          for name in selected})).hexdigest()


def _requirements(base: Path) -> dict:
    source = Path(__file__).resolve().parents[2] / "docs/productization-requirements.md"
    size, digest = _hash_file(source)
    relative = MIGRATION_ROOT / "requirements" / (digest + ".md")
    target = _ensure_parent(base, relative)
    if target.exists():
        if _hash_file(target) != (size, digest):
            raise _fail("REFERENCE_MISMATCH")
    else:
        _copy_stable(source, target, (size, digest))
    manifest = {"schema_version": 1, "kind": "snapshot_manifest",
                "id": "migration-requirements-" + digest[:32],
                "purpose": MIGRATION_REQUIREMENTS_PURPOSE,
                "files": [{"path": relative.as_posix(), "digest": digest, "size_bytes": size}]}
    return _save_ref(base, manifest)


def _source_manifest(base: Path, database_path: str, snapshot_path: str,
                     source_info: dict, snapshot: Path, dry_run: Path) -> dict:
    source_size, source_digest = _hash_file(_child(base, database_path))
    if (source_size, source_digest) != (
            source_info["source_size_bytes"], source_info["source_digest"],
    ):
        raise _fail("BINDING_MISMATCH")
    snapshot_size, snapshot_digest = _hash_file(snapshot)
    if (snapshot_size, snapshot_digest) != (source_size, source_digest):
        raise _fail("BINDING_MISMATCH")
    dry_size, dry_digest = _hash_file(dry_run)
    manifest = {
        "schema_version": 1, "kind": "snapshot_manifest",
        "id": "migration-source-" + hashlib.sha256(canonical_bytes({
            "database": database_path, "source": source_digest,
            "snapshot": snapshot_digest, "dry_run": dry_digest,
        })).hexdigest()[:40],
        "purpose": MIGRATION_SOURCE_PURPOSE,
        "files": [
            {"path": database_path, "digest": source_digest, "size_bytes": source_size},
            {"path": snapshot_path, "digest": snapshot_digest, "size_bytes": snapshot_size},
            {"path": _relative_to(base, dry_run), "digest": dry_digest, "size_bytes": dry_size},
        ],
    }
    return _save_ref(base, manifest)


def _compare(info: dict, payload: dict, side: str) -> None:
    if (info["table_counts"] != payload["table_counts"][side]
            or info["reference_digests"] != payload["reference_digests"][side]
            or info["unsettled_runs"] != payload["unsettled_runs"][side]
            or info["state"] != payload["source_state" if side == "before" else "target_state"]):
        raise _fail("BINDING_MISMATCH")


def _verify_manifest_files(base: Path, manifest: dict) -> None:
    for item in manifest["files"]:
        if _hash_file(_child(base, item["path"])) != (
                item["size_bytes"], item["digest"]):
            raise _fail("REFERENCE_MISMATCH")


def _output_path(base: Path, output: str | Path | None, request_id: str) -> str:
    if output is None:
        return (MIGRATION_ROOT / "plans" / (request_id + ".json")).as_posix()
    value = output.as_posix() if isinstance(output, Path) else output
    if type(value) is not str:
        raise _fail("PATH_REJECTED")
    if Path(value).is_absolute():
        return _relative_to(base, _child(base, value))
    return _relpath(value)


def _read_plan(base: Path, value: str | Path, now: int) -> dict:
    try:
        plan = read_document(base, _relative_to(base, _child(base, value)))
    except (ContractError, MigrationOperationError) as exc:
        raise _fail(getattr(exc, "code", "IO_ERROR")) from exc
    return validate_migration_plan(plan, now=now)


def _map_migration_code(exc: BaseException) -> str:
    code = getattr(exc, "code", None)
    if code in {"UNSUPPORTED_STORE", "CONFIG_MISMATCH", "MIGRATION_UNSUPPORTED",
                "MIGRATION_SCHEMA_EMPTY", "MIGRATION_UNAVAILABLE"}:
        return "SCHEMA_UNSUPPORTED"
    if code in {"INVALID_PATH", "STORE_MISSING"}:
        return code
    if code is not None and isinstance(code, str):
        return code
    if isinstance(exc, (sqlite3.Error, OSError)):
        return "IO_ERROR"
    if isinstance(exc, (ContractError, TypeError, ValueError)):
        return getattr(exc, "code", "INVALID_INPUT")
    return "IO_ERROR"


def _status_reason(code: str) -> tuple[str, str]:
    if code in {"INVALID_INPUT", "PATH_REJECTED"}:
        return "REJECTED", code
    if code in {"STORE_MISSING", "SCHEMA_UNSUPPORTED", "STORAGE_CORRUPT"}:
        return "REJECTED", "SCHEMA_UNSUPPORTED"
    if code in {"REFERENCE_MISMATCH", "BINDING_MISMATCH", "PLAN_EXPIRED",
                "IDEMPOTENCY_CONFLICT", "RESULT_CONFLICT", "STALE_OR_INVALIDATED"}:
        return "REJECTED", code
    if code in {"AUTHORITY_REQUIRED", "IDENTITY_UNAVAILABLE"}:
        return "INCOMPLETE", "AUTHORITY_REQUIRED"
    if code in {"UNSUPPORTED_CAPABILITY", "CLOCK_UNAVAILABLE"}:
        return "INCOMPLETE", code
    if code in {"OWNER_ACTIVE", "OPERATION_UNKNOWN"}:
        return "INCOMPLETE", "OPERATION_UNKNOWN"
    return "INCOMPLETE", "IO_ERROR"


def _operation_error(command: str, request_id: str | None,
                     code: str, checked_at: int) -> dict:
    status, reason = _status_reason(code)
    return operation_result(command, request_id, status, reasons=(reason,),
                            checked_at=checked_at)


def _principal() -> str | None:
    try:
        from .operations import _trusted_principal
        return _trusted_principal()
    except (ImportError, AttributeError, TypeError, OSError):
        return None


@contextmanager
def _transport_lock(base: Path):
    """既存runnerと同じdeployment/supervisor keyで排他し、DB lockは委譲する。"""
    try:
        from .docker_runner import operation_lock
        with operation_lock(base / "supervised-transport", "deployment", "supervisor"):
            yield
    except Exception as exc:
        if getattr(exc, "code", None) == "OWNER_ACTIVE":
            raise _fail("OWNER_ACTIVE") from exc
        if isinstance(exc, MigrationOperationError):
            raise
        raise _fail("IO_ERROR") from exc


def _journal_path(base: Path) -> Path:
    target = _child(base, MIGRATION_JOURNAL)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise _fail("IO_ERROR") from exc
    return target


def _begin(base: Path, principal: str, command: str, request_id: str,
           input_value: dict, clock: Callable[[], Any] | None):
    try:
        digest = hashlib.sha256(canonical_bytes(input_value)).hexdigest()
        journal = OperationJournal(_journal_path(base), clock=clock)
        return journal, journal.begin(principal, command, request_id, digest), digest
    except (ContractError, TypeError, ValueError, OSError, sqlite3.Error) as exc:
        raise _fail(getattr(exc, "code", "IO_ERROR")) from exc


def _finish(journal: OperationJournal, principal: str, command: str,
            request_id: str, digest: str, result: dict) -> dict:
    try:
        return journal.finish(principal, command, request_id, digest, result)
    except ContractError as exc:
        raise _fail(getattr(exc, "code", "IO_ERROR")) from exc


def _preview_impl(base: Path, database: str | Path, output: str | Path | None,
                  request_id: str, now: int) -> tuple[dict, str]:
    source = _child(base, database)
    database_path = _relative_to(base, source)
    source_size, source_digest = _hash_file(source)
    source_info = _inspect(source)
    snapshot_path = _snapshot_path(request_id, source_info)
    snapshot = _ensure_parent(base, snapshot_path)
    _copy_stable(source, snapshot, (source_size, source_digest))
    dry_run = _ensure_parent(base, snapshot_path + ".dry-run.sqlite")
    _copy_stable(snapshot, dry_run, (source_size, source_digest))
    try:
        migrated = migrations.migrate_evaluation_store(dry_run)
    except migrations.MigrationError as exc:
        raise _fail(_map_migration_code(exc)) from exc
    except (sqlite3.Error, OSError) as exc:
        raise _fail("IO_ERROR") from exc
    if type(migrated) is not dict or migrated.get("changed") is not True:
        raise _fail("MIGRATION_FAILED")
    after = _inspect(dry_run, allow_current=True)
    if (after["schema_version"] != 4
            or migrated.get("extension_digest") != after["state"]["extension_digest"]):
        raise _fail("BINDING_MISMATCH")
    requirements_ref = _requirements(base)
    source_ref = _source_manifest(
        base, database_path, snapshot_path,
        {"source_size_bytes": source_size, "source_digest": source_digest},
        snapshot, dry_run,
    )
    payload = {
        "database_path": database_path, "source_ref": source_ref,
        "snapshot_path": snapshot_path, "dry_run_path": _relative_to(base, dry_run),
        "implementation_digest": _implementation_digest(),
        "source_schema_version": source_info["schema_version"],
        "target_schema_version": 4,
        "table_counts": {"before": source_info["table_counts"],
                         "after": after["table_counts"]},
        "reference_digests": {"before": source_info["reference_digests"],
                              "after": after["reference_digests"]},
        "unsettled_runs": {"before": source_info["unsettled_runs"],
                           "after": after["unsettled_runs"]},
        "write_stop_required": True, "snapshot_required": True,
        "allowed_scope": list(MIGRATION_ALLOWED_SCOPE),
        "source_size_bytes": source_size, "source_digest": source_digest,
        "source_state": source_info["state"], "target_state": after["state"],
    }
    _validate_payload(payload)
    plan = {
        "schema_version": 1, "kind": MIGRATION_PLAN_KIND,
        "id": "migration-plan-" + hashlib.sha256(canonical_bytes({
            "source_ref": source_ref, "requirements_ref": requirements_ref,
            "payload": payload, "created_at": now,
        })).hexdigest()[:40],
        "requirement_ids": list(MIGRATION_REQUIREMENT_IDS),
        "source_ref": source_ref, "requirements_ref": requirements_ref,
        "created_at": now, "expires_at": now + MIGRATION_PLAN_TTL_SECONDS,
        "payload": payload,
    }
    plan = validate_migration_plan(plan)
    output_path = _output_path(base, output, request_id)
    _save_doc(base, output_path, plan)
    return plan, output_path


def _target_matches(info: dict, plan: dict) -> bool:
    payload = plan["payload"]
    return (info["schema_version"] == 4
            and info["table_counts"] == payload["table_counts"]["after"]
            and info["reference_digests"] == payload["reference_digests"]["after"]
            and info["unsettled_runs"] == payload["unsettled_runs"]["after"]
            and info["state"] == payload["target_state"])


def _commit_path(principal: str, request_id: str) -> Path:
    token = hashlib.sha256(canonical_bytes({"principal": principal, "request_id": request_id})).hexdigest()
    return MIGRATION_ROOT / "commits" / (token + ".json")


def _save_commit(base: Path, plan: dict, principal: str, request_id: str,
                 digest: str, result: dict) -> None:
    path = _commit_path(principal, request_id)
    value = {"schema_version": 1, "kind": "migration_commit_receipt", "id": path.stem,
             "request_id": request_id, "input_digest": digest, "plan_ref": _plan_ref(plan),
             "operation_result": result}
    _save_doc(base, path, value)


def _recover_apply(base: Path, plan: dict, request_id: str, now: int,
                   principal: str, digest: str) -> tuple[dict, dict] | None:
    # 現DBの状態一致は当該requestのcommit証明ではない。保存済みreceiptだけを回収。
    try:
        path = _commit_path(principal, request_id)
        value = read_document(base, path)
        if (set(value) != {"schema_version", "kind", "id", "request_id", "input_digest", "plan_ref", "operation_result"}
                or type(value["schema_version"]) is not int or value["schema_version"] != 1
                or value["kind"] != "migration_commit_receipt" or value["id"] != path.stem
                or value["request_id"] != request_id or value["input_digest"] != digest
                or value["plan_ref"] != _plan_ref(plan)):
            return None
        result = validate_operation_result(value["operation_result"])
        if (result["command"] != MIGRATION_COMMAND_APPLY or result["request_id"] != request_id
                or result["operation_status"] != "COMPLETED"):
            return None
        ref = result["result_ref"]
        artifact = _read_at(base, MIGRATION_ROOT / "results" / (ref["id"] + ".json"), ref)
        _validate_result(artifact)
        if (artifact["plan_ref"] != _plan_ref(plan) or artifact["source_ref"] != plan["source_ref"]
                or artifact["database_path"] != plan["payload"]["database_path"]):
            return None
        return result, artifact
    except (MigrationOperationError, ContractError, OSError, KeyError, TypeError, ValueError):
        return None


def _apply_impl(base: Path, database: str | Path, plan_path: str | Path,
                request_id: str, now: int) -> tuple[dict, dict]:
    plan = _read_plan(base, plan_path, now)
    source = _child(base, database)
    database_path = _relative_to(base, source)
    payload = plan["payload"]
    if payload["implementation_digest"] != _implementation_digest():
        raise _fail("STALE_OR_INVALIDATED")
    if database_path != payload["database_path"]:
        raise _fail("BINDING_MISMATCH")
    manifest = _resolve_ref(
        base, plan["source_ref"], purpose=MIGRATION_SOURCE_PURPOSE, source=True,
    )
    # sourceの現在fingerprintを先に判定し、manifest側の古いraw digestを
    # REFERENCE_MISMATCHへ誤分類しない。
    source_size, source_digest = _hash_file(source)
    if (source_size, source_digest) != (
            payload["source_size_bytes"], payload["source_digest"],
    ):
        raise _fail("BINDING_MISMATCH")
    _verify_manifest_files(base, manifest)
    requirements = _resolve_ref(base, plan["requirements_ref"],
                                purpose=MIGRATION_REQUIREMENTS_PURPOSE)
    _verify_manifest_files(base, requirements)
    info = _inspect(source)
    _compare(info, payload, "before")
    with _transport_lock(base):
        info = _inspect(source)
        if _hash_file(source) != (payload["source_size_bytes"], payload["source_digest"]):
            raise _fail("BINDING_MISMATCH")
        _compare(info, payload, "before")
        expected_state = {
            "before": {
                **payload["source_state"],
                "table_digests": payload["reference_digests"]["before"]["table_digests"],
            },
            "after": {
                **payload["target_state"],
                "table_digests": payload["reference_digests"]["after"]["table_digests"],
            },
        }
        try:
            # 固定expected_stateだけを既存migrationの同一transactionへ渡す。
            migrated = migrations.migrate_evaluation_store(
                source, expected_state=expected_state,
            )
        except migrations.MigrationError as exc:
            raise _fail(_map_migration_code(exc)) from exc
        except sqlite3.Error as exc:
            raise _fail("OPERATION_UNKNOWN") from exc
        except OSError as exc:
            raise _fail("OPERATION_UNKNOWN") from exc
        try:
            after = _inspect(source, allow_current=True)
            if not _target_matches(after, plan):
                raise _fail("BINDING_MISMATCH")
            if migrated.get("extension_digest") != after["state"]["extension_digest"]:
                raise _fail("BINDING_MISMATCH")
        except Exception as exc:
            raise _fail("OPERATION_UNKNOWN") from exc
    try:
        artifact = _build_result(plan, after, now)
        ref = _save_result(base, artifact)
    except Exception as exc:
        raise _fail("OPERATION_UNKNOWN") from exc
    return operation_result(
        MIGRATION_COMMAND_APPLY, request_id, "COMPLETED",
        result_ref=ref, checked_at=now,
    ), artifact


def _dispatch_preview(workspace: str | Path, database: str | Path,
                      output: str | Path | None, request_id: Any,
                      clock: Callable[[], Any] | None):
    command = MIGRATION_COMMAND_PREVIEW
    rid = _request_id(request_id, "migration-preview")
    if rid is None:
        return operation_result(command, None, "REJECTED", reasons=("INVALID_INPUT",)), None
    journal = None
    try:
        base = _workspace(workspace)
        now = _now(clock)
        database_path = _relative_to(base, _child(base, database))
        output_path = _output_path(base, output, rid)
        input_value = {"database_path": database_path, "output_path": output_path}
        principal = _principal()
        if principal is None:
            return operation_result(command, rid, "INCOMPLETE",
                                    reasons=("AUTHORITY_REQUIRED",), checked_at=now), None
        journal, state, digest = _begin(
            base, principal, command, rid, input_value, clock,
        )
    except MigrationOperationError as exc:
        try:
            checked = _now(clock)
        except MigrationOperationError:
            checked = 0
        return _operation_error(command, rid, exc.code, checked), None
    try:
        if not state["created"]:
            if state["result"] is not None:
                return state["result"], _read_at(
                    base, output_path, state["result"].get("result_ref"),
                )
            return _operation_error(command, rid, "OPERATION_UNKNOWN", now), None
        try:
            with _transport_lock(base):
                plan, saved_path = _preview_impl(
                    base, database, output, rid, now,
                )
            result = operation_result(
                command, rid, "COMPLETED",
                result_ref=_plan_ref(plan), checked_at=now,
            )
            artifact_path = saved_path
        except (MigrationOperationError, migrations.MigrationError,
                ContractError, sqlite3.Error, OSError, TypeError, ValueError) as exc:
            result = _operation_error(command, rid, _map_migration_code(exc), now)
            artifact_path = output_path
        try:
            result = _finish(journal, principal, command, rid, digest, result)
        except MigrationOperationError as exc:
            return _operation_error(command, rid, exc.code, now), None
        return result, _read_at(
            base, artifact_path, result.get("result_ref"),
        ) if result.get("result_ref") else None
    finally:
        journal.close()


def _dispatch_apply(workspace: str | Path, database: str | Path,
                    plan: str | Path, request_id: Any,
                    clock: Callable[[], Any] | None):
    command = MIGRATION_COMMAND_APPLY
    rid = _request_id(request_id, "migration-apply")
    if rid is None:
        return operation_result(command, None, "REJECTED", reasons=("INVALID_INPUT",)), None
    journal = None
    try:
        base = _workspace(workspace)
        now = _now(clock)
        database_path = _relative_to(base, _child(base, database))
        plan_path = _relative_to(base, _child(base, plan))
        value = _read_plan(base, plan_path, None)
        input_value = {"database_path": database_path, "plan_path": plan_path, "plan_ref": _plan_ref(value)}
        principal = _principal()
        if principal is None:
            return operation_result(command, rid, "INCOMPLETE",
                                    reasons=("AUTHORITY_REQUIRED",), checked_at=now), None
        journal, state, digest = _begin(
            base, principal, command, rid, input_value, clock,
        )
    except MigrationOperationError as exc:
        try:
            checked = _now(clock)
        except MigrationOperationError:
            checked = 0
        return _operation_error(command, rid, exc.code, checked), None
    try:
        if not state["created"]:
            if state["result"] is not None:
                ref = state["result"].get("result_ref")
                return state["result"], _read_at(
                    base, MIGRATION_ROOT / "results" / (
                        ref["id"] + ".json") if ref else "", ref,
                )
            try:
                recovered = _recover_apply(base, value, rid, now, principal, digest)
            except (MigrationOperationError, ContractError, sqlite3.Error, OSError, ValueError):
                recovered = None
            if recovered is not None:
                result, artifact = recovered
                try:
                    _finish(journal, principal, command, rid, digest, result)
                except MigrationOperationError:
                    pass
                return result, artifact
            return _operation_error(command, rid, "OPERATION_UNKNOWN", now), None
        commit_confirmed = False
        try:
            result, artifact = _apply_impl(
                base, database, plan, rid, now,
            )
            commit_confirmed = True
            _save_commit(base, value, principal, rid, digest, result)
        except (MigrationOperationError, migrations.MigrationError,
                ContractError, sqlite3.Error, OSError, TypeError, ValueError) as exc:
            result = _operation_error(command, rid, _map_migration_code(exc), now)
            artifact = None
        except Exception:
            result = _operation_error(command, rid, "OPERATION_UNKNOWN", now)
            artifact = None
        if commit_confirmed and result["operation_status"] != "COMPLETED":
            result = _operation_error(command, rid, "OPERATION_UNKNOWN", now)
        if result["operation_status"] == "INCOMPLETE" and "OPERATION_UNKNOWN" in result["reasons"]:
            return result, artifact
        try:
            result = _finish(journal, principal, command, rid, digest, result)
        except MigrationOperationError as exc:
            return _operation_error(command, rid, exc.code, now), artifact
        return result, artifact
    finally:
        journal.close()


def preview(workspace: str | Path, database: str | Path,
            output: str | Path | None = None, *,
            request_id: Any = None,
            clock: Callable[[], Any] | None = None) -> tuple[dict, dict | None]:
    """元DBをコピー上で移行してplanを保存する。"""
    return _dispatch_preview(workspace, database, output, request_id, clock)


def apply(workspace: str | Path, database: str | Path, plan: str | Path, *,
          request_id: Any = None,
          clock: Callable[[], Any] | None = None) -> tuple[dict, dict | None]:
    """同じsourceとplanを検査し、既存migrationへ一度だけ委譲する。"""
    return _dispatch_apply(workspace, database, plan, request_id, clock)


__all__ = [
    "MIGRATION_COMMAND_PREVIEW", "MIGRATION_COMMAND_APPLY",
    "MIGRATION_PLAN_KIND", "MIGRATION_RESULT_KIND",
    "MigrationOperationError", "inspect_source", "validate_migration_plan",
    "preview", "apply",
]
