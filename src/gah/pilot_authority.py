"""Pilot metadata authority extension with fixed bindings and plan receipts.

This module exposes only a small authority extension surface. It validates
immutable pilot bindings and plans, records independent validator and manager
receipts, and reads the current metadata state. It never starts a runner,
grants product execution authority, or marks metadata as CI evidence.
"""
from __future__ import annotations

import copy
import hashlib
from typing import Any

from . import resources
from .adoption import AdoptionError, VALIDATION_TTL
from .contracts import (
    ContractError,
    MAX_DOCUMENT_BYTES,
    MAX_INTEGER,
    require_id,
    require_object,
    require_ref,
    require_uint,
)
from .pilot import (
    validate_evaluation_target_binding,
    validate_pilot_artifact,
    validate_pilot_plan,
    validate_project_binding,
)
from .productization import content_ref
from .read_checks import checked_action
from .wire import canonical_bytes


_SCHEMA_VERSION = 1
_METADATA_RUN_ID = "pilot-metadata"
_ARTIFACT_KINDS = frozenset({
    "project_binding",
    "evaluation_target_binding",
    "pilot_plan",
    "pilot_plan_validation",
    "pilot_plan_adoption",
})
_BINDING_KINDS = frozenset({"project_binding", "evaluation_target_binding"})
_RECEIPT_KINDS = frozenset({"pilot_plan_validation", "pilot_plan_adoption"})

_BASE_FIELDS = {"schema_version", "action", "request_id"}
FIELDS = {
    "pilot_binding_register": _BASE_FIELDS | {"document"},
    "pilot_plan_register": _BASE_FIELDS | {"document"},
    "pilot_plan_validate": _BASE_FIELDS | {
        "plan_ref", "validation_id", "expected_generation",
    },
    "pilot_plan_adopt": _BASE_FIELDS | {
        "plan_ref", "validation_ref", "adoption_id", "expected_generation",
    },
    "pilot_plan_current": _BASE_FIELDS | {"plan_ref"},
}
ACTIONS = {
    "pilot_binding_register": {"manager"},
    "pilot_plan_register": {"manager"},
    "pilot_plan_validate": {"validator"},
    "pilot_plan_adopt": {"manager"},
    "pilot_plan_current": {"manager", "validator", "operator"},
}
FRESH_ACTIONS = {"pilot_plan_current"}

__all__ = [
    "FIELDS", "ACTIONS", "FRESH_ACTIONS",
    "validate_request", "request_key", "execute",
]


def _error(code: str = "INVALID_REQUEST") -> AdoptionError:
    return AdoptionError(code)


def _invalid() -> AdoptionError:
    return _error("INVALID_REQUEST")


def _canonical(value: Any) -> bytes:
    try:
        raw = canonical_bytes(value)
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise _invalid() from None
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise _error("REQUEST_TOO_LARGE")
    return raw


def _request_digest(request: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(request)).hexdigest()


def request_key(actor_id: str, action: str, request_id: str) -> str:
    """Return the private idempotency key for a pilot action.

    The namespace is intentionally outside the public ID grammar. The public
    request_id remains unchanged in every response and persisted receipt.
    """
    for value in (actor_id, action, request_id):
        try:
            require_id(value)
        except ContractError:
            raise _invalid() from None
    return "@pilot:" + hashlib.sha256(
        canonical_bytes([actor_id, action, request_id])
    ).hexdigest()


def _ref(value: Any, kind: str) -> dict[str, str]:
    try:
        require_ref(value)
    except ContractError:
        raise _invalid() from None
    if value["kind"] != kind:
        raise _error("REFERENCE_KIND")
    return copy.deepcopy(value)


def _id(value: Any) -> None:
    try:
        require_id(value)
    except ContractError:
        raise _invalid() from None


def _uint(value: Any) -> None:
    try:
        require_uint(value)
    except ContractError:
        raise _invalid() from None


def validate_request(request: Any) -> dict[str, Any]:
    """Validate the closed request vocabulary used by AdoptionStore."""
    if type(request) is not dict:
        raise _invalid()
    action = request.get("action")
    if type(action) is not str or action not in FIELDS:
        raise _error("INVALID_ACTION")
    try:
        require_object(request, FIELDS[action])
        if type(request["schema_version"]) is not int or request["schema_version"] != _SCHEMA_VERSION:
            raise _error("UNSUPPORTED_VERSION")
        _id(request["request_id"])
        if action == "pilot_binding_register":
            document = request["document"]
            if type(document) is not dict or document.get("kind") not in _BINDING_KINDS:
                raise _error("PILOT_KIND_UNSUPPORTED")
            if document["kind"] == "project_binding":
                validate_project_binding(document)
            else:
                validate_evaluation_target_binding(document)
        elif action == "pilot_plan_register":
            document = request["document"]
            if type(document) is not dict or document.get("kind") != "pilot_plan":
                raise _error("PILOT_KIND_UNSUPPORTED")
            validate_pilot_plan(document)
        elif action == "pilot_plan_validate":
            _ref(request["plan_ref"], "pilot_plan")
            _id(request["validation_id"])
            _uint(request["expected_generation"])
        elif action == "pilot_plan_adopt":
            _ref(request["plan_ref"], "pilot_plan")
            _ref(request["validation_ref"], "pilot_plan_validation")
            _id(request["adoption_id"])
            _uint(request["expected_generation"])
        else:
            _ref(request["plan_ref"], "pilot_plan")
    except AdoptionError:
        raise
    except (ContractError, KeyError, TypeError, ValueError, RecursionError):
        raise _invalid() from None
    return copy.deepcopy(request)


def _packed(value: dict[str, Any]) -> tuple[str, str]:
    raw = _canonical(value)
    return raw.decode("utf-8"), hashlib.sha256(raw).hexdigest()


def _save_artifact(
    db: Any, kind: str, identifier: str, value: dict[str, Any],
) -> dict[str, str]:
    if kind not in _ARTIFACT_KINDS:
        raise _error("PILOT_KIND_UNSUPPORTED")
    _id(identifier)
    if type(value) is not dict or value.get("kind") != kind or value.get("id") != identifier:
        raise _error("PILOT_BINDING_MISMATCH")
    try:
        ref = content_ref(kind, identifier, value)
        raw, _ = _packed(value)
        existing = db.execute(
            "SELECT digest, payload_json, run_id FROM authority_artifacts "
            "WHERE kind=? AND id=?",
            (kind, identifier),
        ).fetchall()
    except Exception:
        raise _error("STORAGE_CORRUPT") from None
    if any(row["run_id"] != _METADATA_RUN_ID for row in existing):
        raise _error("PILOT_ARTIFACT_CONFLICT")
    if any(row["digest"] != ref["digest"] for row in existing):
        raise _error("PILOT_ARTIFACT_CONFLICT")
    if existing:
        row = next((item for item in existing if item["digest"] == ref["digest"]), None)
        if row is None or row["payload_json"] != raw:
            raise _error("STORAGE_CORRUPT")
        return ref
    try:
        db.execute(
            "INSERT INTO authority_artifacts(kind,id,digest,payload_json,run_id) "
            "VALUES(?,?,?,?,?)",
            (kind, identifier, ref["digest"], raw, _METADATA_RUN_ID),
        )
    except Exception as error:
        if getattr(error, "sqlite_errorname", "") == "SQLITE_CONSTRAINT_PRIMARYKEY":
            raise _error("PILOT_ARTIFACT_CONFLICT") from None
        raise
    return ref


def _load_artifact(db: Any, ref: dict[str, str], kind: str | None = None) -> dict[str, Any]:
    try:
        require_ref(ref)
    except ContractError:
        raise _error("REFERENCE_MISSING") from None
    if kind is not None and ref["kind"] != kind:
        raise _error("REFERENCE_KIND")
    try:
        row = db.execute(
            "SELECT * FROM authority_artifacts WHERE kind=? AND id=? AND digest=?",
            (ref["kind"], ref["id"], ref["digest"]),
        ).fetchone()
    except Exception:
        raise _error("STORAGE_CORRUPT") from None
    if row is None or row["run_id"] != _METADATA_RUN_ID:
        raise _error("REFERENCE_MISSING")
    try:
        value = resources._unpack(row["payload_json"], row["digest"])
        if (
            type(value) is not dict
            or value.get("kind") != ref["kind"]
            or value.get("id") != ref["id"]
            or content_ref(ref["kind"], ref["id"], value) != dict(ref)
        ):
            raise ValueError
        if ref["kind"] == "project_binding":
            validate_project_binding(value)
        elif ref["kind"] == "evaluation_target_binding":
            validate_evaluation_target_binding(value)
        elif ref["kind"] == "pilot_plan":
            validate_pilot_plan(value)
    except (ContractError, KeyError, TypeError, ValueError, UnicodeError, RecursionError):
        raise _error("STORAGE_CORRUPT") from None
    return copy.deepcopy(value)


def _plan(value: dict[str, Any], *, now: int | None = None) -> dict[str, Any]:
    try:
        return validate_pilot_plan(value, now=now)
    except ContractError as error:
        raise _error(getattr(error, "code", "INVALID_REQUEST")) from None
    except (KeyError, TypeError, ValueError, RecursionError):
        raise _error("INVALID_REQUEST") from None


def _load_plan(db: Any, ref: dict[str, str], *, now: int | None = None) -> dict[str, Any]:
    plan = _load_artifact(db, ref, "pilot_plan")
    return _plan(plan, now=now)


def _local_bindings(db: Any, plan: dict[str, Any]) -> dict[str, Any]:
    payload = plan["payload"]
    projects = [
        _load_artifact(db, ref, "project_binding")
        for ref in payload["project_binding_refs"]
    ]
    baseline = _load_artifact(
        db, payload["baseline_target_binding_ref"], "evaluation_target_binding",
    )
    candidate = _load_artifact(
        db, payload["candidate_target_binding_ref"], "evaluation_target_binding",
    )
    try:
        if len({item["id"] for item in projects}) != 2:
            raise _error("BINDING_MISMATCH")
        if baseline["payload"]["target_ref"] != payload["evaluation_target_ref"]:
            raise _error("BINDING_MISMATCH")
        if candidate["payload"]["target_ref"] != payload["evaluation_target_ref"]:
            raise _error("BINDING_MISMATCH")
        base_payload = baseline["payload"]
        cand_payload = candidate["payload"]
        if base_payload["model_revision_ref"] == cand_payload["model_revision_ref"]:
            raise _error("BINDING_MISMATCH")
        if base_payload["adapter_ref"] != cand_payload["adapter_ref"]:
            raise _error("BINDING_MISMATCH")
        if base_payload["evaluator_ref"] != cand_payload["evaluator_ref"]:
            raise _error("BINDING_MISMATCH")
        if base_payload["adapter_ref"] not in payload["adapter_refs"]:
            raise _error("BINDING_MISMATCH")
        if base_payload["evaluator_ref"] not in payload["evaluator_refs"]:
            raise _error("BINDING_MISMATCH")
        if any(item["payload"]["owner_ref"] != payload["owner_ref"]
               for item in projects):
            raise _error("BINDING_MISMATCH")
        if any(item["payload"]["permission_ref"] not in payload["permission_refs"]
               for item in (*projects, baseline, candidate)):
            raise _error("BINDING_MISMATCH")
        if any(item["payload"]["resource_profile_ref"] != payload["resource_profile_ref"]
               for item in (*projects, baseline, candidate)):
            raise _error("BINDING_MISMATCH")
        if any(item["payload"]["retention_ref"] != payload["retention_ref"]
               for item in projects):
            raise _error("BINDING_MISMATCH")
        if base_payload["recipe_ref"] != cand_payload["recipe_ref"]:
            raise _error("BINDING_MISMATCH")
        if base_payload["redaction_profile_ref"] != cand_payload["redaction_profile_ref"]:
            raise _error("BINDING_MISMATCH")
    except KeyError:
        raise _error("STORAGE_CORRUPT") from None
    return {
        "projects": tuple(copy.deepcopy(projects)),
        "baseline": copy.deepcopy(baseline),
        "candidate": copy.deepcopy(candidate),
        # No external ledger/authentication is available at this boundary.
        "external_refs_verified": False,
    }


def _permission(db: Any) -> int:
    try:
        row = db.execute(
            "SELECT value FROM adoption_meta WHERE key='permission_generation'"
        ).fetchone()
    except Exception:
        raise _error("STORAGE_CORRUPT") from None
    if row is None or type(row[0]) is not int or not 0 <= row[0] <= MAX_INTEGER:
        raise _error("STORAGE_CORRUPT")
    return row[0]


def _actor_revoked(db: Any, actor_id: str) -> bool:
    try:
        row = db.execute(
            "SELECT generation FROM revocations WHERE entity_type='actor' AND entity_id=?",
            (actor_id,),
        ).fetchone()
    except Exception:
        raise _error("STORAGE_CORRUPT") from None
    if row is None:
        return False
    if type(row[0]) is not int or not 0 <= row[0] <= MAX_INTEGER:
        raise _error("STORAGE_CORRUPT")
    return True


def _active(created_at: int, expires_at: int, now: int) -> None:
    if not created_at <= now < expires_at:
        raise _error("PLAN_EXPIRED")


def _current_adoptions(
    db: Any, plan_ref: dict[str, str],
) -> list[dict[str, Any]]:
    try:
        rows = db.execute(
            "SELECT kind,id,digest,run_id FROM authority_artifacts "
            "WHERE kind='pilot_plan_adoption' ORDER BY id"
        ).fetchall()
    except Exception:
        raise _error("STORAGE_CORRUPT") from None
    if len(rows) > 1000:
        raise _error("STORAGE_CORRUPT")
    result: list[dict[str, Any]] = []
    for row in rows:
        if row["run_id"] != _METADATA_RUN_ID:
            continue
        ref = {"kind": row["kind"], "id": row["id"], "digest": row["digest"]}
        adoption = _load_artifact(db, ref, "pilot_plan_adoption")
        adoption = _validate_adoption(adoption)
        if adoption["plan_ref"] == plan_ref:
            result.append(adoption)
    result.sort(key=lambda item: item["generation"])
    seen: set[int] = set()
    for item in result:
        generation = item["generation"]
        if generation in seen:
            raise _error("PILOT_ARTIFACT_CONFLICT")
        seen.add(generation)
    return result


_VALIDATION_FIELDS = {
    "schema_version", "kind", "id", "plan_ref", "expected_generation",
    "permission_generation", "created_at", "expires_at", "validated_by",
    "metadata_only", "external_refs_verified", "authority_required",
    "ci_eligible", "request",
}
_ADOPTION_FIELDS = {
    "schema_version", "kind", "id", "plan_ref", "validation_ref",
    "expected_generation", "generation", "permission_generation",
    "created_at", "expires_at", "adopted_by", "metadata_only",
    "external_refs_verified", "authority_required", "product_run_authority",
    "ci_eligible", "request",
}


def _validate_validation(
    value: Any, *, now: int | None = None,
) -> dict[str, Any]:
    if type(value) is not dict:
        raise _error("STORAGE_CORRUPT")
    try:
        require_object(value, _VALIDATION_FIELDS)
        if (type(value["schema_version"]) is not int
                or value["schema_version"] != 1
                or value["kind"] != "pilot_plan_validation"):
            raise _error("STORAGE_CORRUPT")
        _id(value["id"])
        _ref(value["plan_ref"], "pilot_plan")
        _uint(value["expected_generation"])
        _uint(value["permission_generation"])
        _uint(value["created_at"])
        _uint(value["expires_at"])
        if value["expires_at"] <= value["created_at"]:
            raise _error("STORAGE_CORRUPT")
        _id(value["validated_by"])
        if value["validated_by"] != "validator":
            raise _error("STORAGE_CORRUPT")
        for name, expected in (
            ("metadata_only", True), ("external_refs_verified", False),
            ("authority_required", True), ("ci_eligible", False),
        ):
            if type(value[name]) is not bool or value[name] is not expected:
                raise _error("STORAGE_CORRUPT")
        request = validate_request(value["request"])
        if request["action"] != "pilot_plan_validate":
            raise _error("STORAGE_CORRUPT")
        if (
            request["plan_ref"] != value["plan_ref"]
            or request["validation_id"] != value["id"]
            or request["expected_generation"] != value["expected_generation"]
        ):
            raise _error("STORAGE_CORRUPT")
    except AdoptionError:
        raise _error("STORAGE_CORRUPT") from None
    except (ContractError, KeyError, TypeError, ValueError, RecursionError):
        raise _error("STORAGE_CORRUPT") from None
    if now is not None:
        _active(value["created_at"], value["expires_at"], now)
    return copy.deepcopy(value)


def _validate_adoption(
    value: Any, *, now: int | None = None,
) -> dict[str, Any]:
    if type(value) is not dict:
        raise _error("STORAGE_CORRUPT")
    try:
        require_object(value, _ADOPTION_FIELDS)
        if (type(value["schema_version"]) is not int
                or value["schema_version"] != 1
                or value["kind"] != "pilot_plan_adoption"):
            raise _error("STORAGE_CORRUPT")
        _id(value["id"])
        _ref(value["plan_ref"], "pilot_plan")
        _ref(value["validation_ref"], "pilot_plan_validation")
        _uint(value["expected_generation"])
        _uint(value["generation"])
        if value["generation"] != value["expected_generation"] + 1:
            raise _error("STORAGE_CORRUPT")
        if value["generation"] > MAX_INTEGER:
            raise _error("STORAGE_CORRUPT")
        _uint(value["permission_generation"])
        _uint(value["created_at"])
        _uint(value["expires_at"])
        if value["expires_at"] <= value["created_at"]:
            raise _error("STORAGE_CORRUPT")
        _id(value["adopted_by"])
        for name, expected in (
            ("metadata_only", True), ("external_refs_verified", False),
            ("authority_required", True), ("product_run_authority", False),
            ("ci_eligible", False),
        ):
            if type(value[name]) is not bool or value[name] is not expected:
                raise _error("STORAGE_CORRUPT")
        request = validate_request(value["request"])
        if request["action"] != "pilot_plan_adopt":
            raise _error("STORAGE_CORRUPT")
        if (
            request["plan_ref"] != value["plan_ref"]
            or request["validation_ref"] != value["validation_ref"]
            or request["adoption_id"] != value["id"]
            or request["expected_generation"] != value["expected_generation"]
        ):
            raise _error("STORAGE_CORRUPT")
    except AdoptionError:
        raise _error("STORAGE_CORRUPT") from None
    except (ContractError, KeyError, TypeError, ValueError, RecursionError):
        raise _error("STORAGE_CORRUPT") from None
    if now is not None:
        _active(value["created_at"], value["expires_at"], now)
    return copy.deepcopy(value)


def _result(action: str, request_id: str, **fields: Any) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "kind": "pilot_authority_result",
        "action": action,
        "request_id": request_id,
        "ci_eligible": False,
        **fields,
    }


def _origin(db: Any, actor_id: str, action: str, request: dict[str, Any]) -> dict[str, Any]:
    """Verify the immutable dispatch receipt in the private pilot namespace."""
    try:
        key = request_key(actor_id, action, request["request_id"])
        row = db.execute(
            "SELECT * FROM idempotency WHERE request_id=?", (key,)
        ).fetchone()
    except Exception:
        raise _error("STORAGE_CORRUPT") from None
    if row is None:
        raise _error("AUTHORITY_REQUIRED")
    try:
        if (
            row["actor_id"] != actor_id
            or row["context"] != actor_id + "-context"
            or row["request_digest"] != _request_digest(request)
            or row["response_json"] is None
            or row["response_digest"] is None
        ):
            raise ValueError
        response = resources._unpack(row["response_json"], row["response_digest"])
        if (
            type(response) is not dict
            or type(response.get("schema_version")) is not int
            or response.get("schema_version") != 1
            or response.get("kind") != "pilot_authority_result"
            or response.get("action") != action
            or response.get("request_id") != request["request_id"]
            or response.get("ci_eligible") is not False
        ):
            raise ValueError
    except (ContractError, KeyError, TypeError, ValueError, UnicodeError, RecursionError):
        raise _error("AUTHORITY_REQUIRED") from None
    return copy.deepcopy(response)


def _role_check(action: str, actor_id: str, context: str) -> None:
    if actor_id not in ACTIONS.get(action, set()) or context != actor_id + "-context":
        raise _error("AUTHORITY_DENIED")


def _generation(db: Any, plan_ref: dict[str, str]) -> int:
    adoptions = _current_adoptions(db, plan_ref)
    return adoptions[-1]["generation"] if adoptions else 0


def _register(
    db: Any, request: dict[str, Any], now: int,
) -> dict[str, Any]:
    document = copy.deepcopy(request["document"])
    kind = document["kind"]
    if kind in _BINDING_KINDS:
        if kind == "project_binding":
            validate_project_binding(document)
        else:
            validate_evaluation_target_binding(document)
        ref = _save_artifact(db, kind, document["id"], document)
    elif kind == "pilot_plan":
        # A plan can be stored as a draft. Its current lifetime is checked
        # by independent validation and adoption.
        validate_pilot_plan(document)
        _local_bindings(db, document)
        ref = _save_artifact(db, kind, document["id"], document)
    else:
        raise _error("PILOT_KIND_UNSUPPORTED")
    return _result(
        request["action"], request["request_id"],
        artifact_ref=ref,
        metadata_only=True,
        external_refs_verified=False,
        authority_required=True,
        product_run_authority=False,
    )


def _validate_plan(
    db: Any, request: dict[str, Any], actor_id: str, now: int,
) -> dict[str, Any]:
    plan = _load_plan(db, request["plan_ref"], now=now)
    _local_bindings(db, plan)
    if _actor_revoked(db, actor_id):
        raise _error("AUTHORITY_REVOKED")
    current = _generation(db, request["plan_ref"])
    if request["expected_generation"] != current:
        raise _error("GENERATION_CONFLICT")
    permission = _permission(db)
    expires_at = min(plan["expires_at"], min(MAX_INTEGER, now + VALIDATION_TTL))
    if expires_at <= now:
        raise _error("PLAN_EXPIRED")
    receipt = {
        "schema_version": 1,
        "kind": "pilot_plan_validation",
        "id": request["validation_id"],
        "plan_ref": copy.deepcopy(request["plan_ref"]),
        "expected_generation": request["expected_generation"],
        "permission_generation": permission,
        "created_at": now,
        "expires_at": expires_at,
        "validated_by": actor_id,
        "metadata_only": True,
        "external_refs_verified": False,
        "authority_required": True,
        "ci_eligible": False,
        "request": copy.deepcopy(request),
    }
    _validate_validation(receipt, now=now)
    ref = _save_artifact(db, "pilot_plan_validation", receipt["id"], receipt)
    return _result(
        request["action"], request["request_id"],
        validation_ref=ref,
        validation=receipt,
        permission_generation=permission,
        metadata_only=True,
        external_refs_verified=False,
        authority_required=True,
        product_run_authority=False,
    )


def _adopt_plan(
    db: Any, request: dict[str, Any], actor_id: str, now: int,
) -> dict[str, Any]:
    plan = _load_plan(db, request["plan_ref"], now=now)
    _local_bindings(db, plan)
    if _actor_revoked(db, actor_id):
        raise _error("AUTHORITY_REVOKED")
    permission = _permission(db)
    current = _generation(db, request["plan_ref"])
    if request["expected_generation"] != current:
        raise _error("GENERATION_CONFLICT")
    validation = _load_artifact(db, request["validation_ref"], "pilot_plan_validation")
    validation = _validate_validation(validation, now=now)
    if (
        validation["plan_ref"] != request["plan_ref"]
        or validation["expected_generation"] != request["expected_generation"]
        or validation["permission_generation"] != permission
        or validation["validated_by"] == actor_id
        or validation["validated_by"] != "validator"
    ):
        raise _error("AUTHORITY_REQUIRED")
    if _actor_revoked(db, validation["validated_by"]):
        raise _error("AUTHORITY_REVOKED")
    _origin(
        db, validation["validated_by"], "pilot_plan_validate",
        validation["request"],
    )
    generation = request["expected_generation"] + 1
    if generation > MAX_INTEGER:
        raise _error("GENERATION_CONFLICT")
    expires_at = min(plan["expires_at"], validation["expires_at"])
    if expires_at <= now:
        raise _error("PLAN_EXPIRED")
    receipt = {
        "schema_version": 1,
        "kind": "pilot_plan_adoption",
        "id": request["adoption_id"],
        "plan_ref": copy.deepcopy(request["plan_ref"]),
        "validation_ref": copy.deepcopy(request["validation_ref"]),
        "expected_generation": request["expected_generation"],
        "generation": generation,
        "permission_generation": permission,
        "created_at": now,
        "expires_at": expires_at,
        "adopted_by": actor_id,
        "metadata_only": True,
        "external_refs_verified": False,
        "authority_required": True,
        "product_run_authority": False,
        "ci_eligible": False,
        "request": copy.deepcopy(request),
    }
    _validate_adoption(receipt, now=now)
    ref = _save_artifact(db, "pilot_plan_adoption", receipt["id"], receipt)
    return _result(
        request["action"], request["request_id"],
        adoption_ref=ref,
        adoption=receipt,
        adopted=True,
        generation=generation,
        metadata_only=True,
        external_refs_verified=False,
        authority_required=True,
        product_run_authority=False,
    )


def _current(
    db: Any, request: dict[str, Any], actor_id: str, now: int,
) -> dict[str, Any]:
    plan = _load_plan(db, request["plan_ref"])
    plan_active = plan["created_at"] <= now < plan["expires_at"]
    reasons: list[str] = []
    try:
        bindings = _local_bindings(db, plan)
        external_verified = bindings["external_refs_verified"]
    except AdoptionError:
        external_verified = False
        reasons.append("BINDING_MISMATCH")
    permission = _permission(db)
    adoptions = _current_adoptions(db, request["plan_ref"])
    if not adoptions:
        reasons.append("PLAN_EXPIRED" if not plan_active else "NOT_STARTED")
        return _result(
            request["action"], request["request_id"],
            plan_ref=copy.deepcopy(request["plan_ref"]),
            adopted=False,
            valid=False,
            generation=0,
            validation_ref=None,
            adoption_ref=None,
            reasons=list(dict.fromkeys(reasons)),
            metadata_only=True,
            external_refs_verified=external_verified,
            authority_required=True,
            product_run_authority=False,
        )
    adoption = adoptions[-1]
    adoption_ref = content_ref(
        "pilot_plan_adoption", adoption["id"], adoption,
    )
    validation = _load_artifact(db, adoption["validation_ref"], "pilot_plan_validation")
    validation = _validate_validation(validation)
    validation_ref = copy.deepcopy(adoption["validation_ref"])
    valid = True
    if not plan_active or not adoption["created_at"] <= now < adoption["expires_at"]:
        reasons.append("PLAN_EXPIRED")
        valid = False
    if not validation["created_at"] <= now < validation["expires_at"]:
        reasons.append("PLAN_EXPIRED")
        valid = False
    if adoption["permission_generation"] != permission:
        reasons.append("AUTHORITY_REQUIRED")
        valid = False
    if validation["permission_generation"] != permission:
        reasons.append("AUTHORITY_REQUIRED")
        valid = False
    if adoption["generation"] != _generation(db, request["plan_ref"]):
        reasons.append("BINDING_MISMATCH")
        valid = False
    if adoption["validation_ref"] != validation_ref or adoption["plan_ref"] != request["plan_ref"]:
        reasons.append("BINDING_MISMATCH")
        valid = False
    if _actor_revoked(db, adoption["adopted_by"]) or _actor_revoked(db, validation["validated_by"]):
        reasons.append("AUTHORITY_REQUIRED")
        valid = False
    try:
        if validation["validated_by"] != "validator":
            raise _error("AUTHORITY_REQUIRED")
        _origin(
            db, validation["validated_by"], "pilot_plan_validate",
            validation["request"],
        )
        _origin(
            db, adoption["adopted_by"], "pilot_plan_adopt",
            adoption["request"],
        )
    except AdoptionError:
        reasons.append("AUTHORITY_REQUIRED")
        valid = False
    return _result(
        request["action"], request["request_id"],
        plan_ref=copy.deepcopy(request["plan_ref"]),
        adopted=True,
        valid=valid,
        generation=adoption["generation"],
        validation_ref=validation_ref,
        adoption_ref=adoption_ref,
        reasons=list(dict.fromkeys(reasons)),
        metadata_only=True,
        external_refs_verified=external_verified,
        authority_required=True,
        product_run_authority=False,
    )


@checked_action
def execute(
    store: Any,
    db: Any,
    request: dict[str, Any],
    actor_id: str,
    context: str,
    now: int,
) -> dict[str, Any]:
    """Execute one fixed pilot authority action inside AdoptionStore's transaction."""
    normalized = validate_request(request)
    action = normalized["action"]
    _role_check(action, actor_id, context)
    _uint(now)
    if action in {"pilot_binding_register", "pilot_plan_register"}:
        return _register(db, normalized, now)
    if action == "pilot_plan_validate":
        return _validate_plan(db, normalized, actor_id, now)
    if action == "pilot_plan_adopt":
        return _adopt_plan(db, normalized, actor_id, now)
    if action == "pilot_plan_current":
        return _current(db, normalized, actor_id, now)
    raise _error("INVALID_ACTION")
