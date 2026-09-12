"""認証された観測と資源台帳を、同じtransactionの保存・判定へ結ぶ。

呼出主体の検査はAdoptionStore、契約の解決はEvaluationExtensionが所有する。
固定fixtureの実行経路だけを受け付け、データ実体の採択と通常CI利用は別境界とする。
"""
from copy import deepcopy
import hashlib
from pathlib import Path

from .adoption import AdoptionError
from .contracts import ContractError, MAX_DOCUMENT_BYTES, MAX_INTEGER, require_id, require_object
from .docker_runner import PROFILE
from . import resources, resource_authority, run_evidence, fixture_admission
from .run_contracts import content_ref
from .wire import canonical_bytes


TABLES = {
    "authority_artifacts": {"kind", "id", "digest", "payload_json", "run_id"},
    "authority_attempt_origins": {"attempt_id", "attempt_digest", "actor_id", "context", "permission_generation", "recorded_at"},
    "authority_run_receipts": {"run_id", "payload_json", "digest", "permission_generation", "created_at", "revoked_at"},
    "authority_run_events": {"event_id", "run_id", "payload_json", "digest", "created_at"},
}
_BASE = {"schema_version", "action", "request_id", "run_id"}
FIELDS = {
    "evidence_open": _BASE,
    "evidence_record": _BASE | {"attempt"},
    "evidence_finalize": _BASE,
    "evidence_current": _BASE | {"expected_bundle_digest"},
    "evidence_revoke": _BASE,
}
ACTIONS = {
    "evidence_open": {"operator"}, "evidence_record": {"validator"},
    "evidence_finalize": {"operator"}, "evidence_current": {"manager", "validator", "operator"},
    "evidence_revoke": {"operator"},
}
FRESH_ACTIONS = {"evidence_current"}


def _error(code):
    return AdoptionError(code)


def validate_request(request):
    action = request.get("action") if type(request) is dict else None
    if type(action) is not str or action not in FIELDS:
        raise _error("INVALID_ACTION")
    try:
        require_object(request, FIELDS[action])
        if type(request["schema_version"]) is not int or request["schema_version"] != 1:
            raise _error("UNSUPPORTED_VERSION")
        require_id(request["request_id"])
        require_id(request["run_id"])
        if action == "evidence_record":
            run_evidence._attempt(request["attempt"])
            if request["attempt"]["expected_binding"]["run_id"] != request["run_id"]:
                raise _error("BINDING_MISMATCH")
        if action == "evidence_current":
            run_evidence._digest(request["expected_bundle_digest"])
    except (ContractError, run_evidence.EvidenceError, KeyError, TypeError, ValueError) as error:
        if isinstance(error, AdoptionError):
            raise
        raise _error("INVALID_REQUEST") from None
    return deepcopy(request)


def create_schema(db):
    if not db.in_transaction:
        raise _error("TRANSACTION_REQUIRED")
    db.execute("CREATE TABLE authority_artifacts(kind TEXT NOT NULL, id TEXT NOT NULL, digest TEXT NOT NULL, payload_json TEXT NOT NULL, run_id TEXT NOT NULL, PRIMARY KEY(kind,id,digest))")
    db.execute("CREATE TABLE authority_attempt_origins(attempt_id TEXT PRIMARY KEY, attempt_digest TEXT NOT NULL, actor_id TEXT NOT NULL, context TEXT NOT NULL, permission_generation INTEGER NOT NULL, recorded_at INTEGER NOT NULL)")
    db.execute("CREATE TABLE authority_run_receipts(run_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL, digest TEXT NOT NULL, permission_generation INTEGER NOT NULL, created_at INTEGER NOT NULL, revoked_at INTEGER)")
    db.execute("CREATE TABLE authority_run_events(event_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, payload_json TEXT NOT NULL, digest TEXT NOT NULL, created_at INTEGER NOT NULL)")


def fixed_profile():
    lock = resource_authority._lock()
    return {
        "fixture_digest": lock["worker_digest"],
        "adapter_digests": [hashlib.sha256(Path(__file__).with_name("normalized.py").read_bytes()).hexdigest()],
        "isolation_digest": hashlib.sha256(canonical_bytes(PROFILE)).hexdigest(),
    }


def _save(db, run_id, kind, identifier, payload):
    raw = canonical_bytes(payload)
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise _error("DOCUMENT_SIZE")
    ref = content_ref(kind, identifier, payload)
    existing = db.execute("SELECT payload_json FROM authority_artifacts WHERE kind=? AND id=? AND digest=?",
        (kind, identifier, ref["digest"])).fetchone()
    if existing is not None and existing[0] != raw.decode("utf-8"):
        raise _error("STORAGE_CORRUPT")
    db.execute("INSERT OR IGNORE INTO authority_artifacts VALUES(?,?,?,?,?)",
        (kind, identifier, ref["digest"], raw.decode("utf-8"), run_id))
    return ref


def _artifact(db, ref, run_id):
    row = db.execute("SELECT * FROM authority_artifacts WHERE kind=? AND id=? AND digest=?",
        (ref["kind"], ref["id"], ref["digest"])).fetchone()
    if row is None or row["run_id"] != run_id:
        raise _error("STORAGE_CORRUPT")
    return resources._unpack(row["payload_json"], row["digest"])


def _operation(db, attempt, now):
    binding = attempt["expected_binding"]
    row = db.execute("SELECT * FROM resource_operations WHERE operation_id=? AND run_id=?",
        (binding["operation_id"], binding["run_id"])).fetchone()
    joined = db.execute("SELECT * FROM resource_bindings WHERE operation_id=? AND run_id=?",
        (binding["operation_id"], binding["run_id"])).fetchone()
    if row is None or joined is None:
        raise _error("OPERATION_MISSING")
    if (row["owner_epoch"] != binding["owner_epoch"] or row["intended_at"] is None
            or row["released"] or row["conflicted"] or row["stopped_at"] is None
            or row["settled_at"] is None or row["settled_at"] > now):
        raise _error("OPERATION_NOT_SETTLED")
    resources._unpack(row["reservation_json"], row["reservation_digest"])
    resources._unpack(row["usage_json"], row["usage_digest"])
    if (attempt["finished_at"] is None or not attempt["stop_confirmed"]
            or not row["intended_at"] <= attempt["started_at"] <= attempt["finished_at"] <= row["stopped_at"] <= now):
        raise _error("OPERATION_TIME_MISMATCH")
    return joined


def _resource_binding(db, bound):
    manifest = bound["manifest"]
    row = db.execute("SELECT * FROM resource_runs WHERE run_id=?", (manifest["run_id"],)).fetchone()
    if (row is None or row["manifest_digest"] != content_ref("run_manifest", manifest["run_id"], manifest)["digest"]
            or row["policy_digest"] != manifest["policy_ref"]["digest"] or row["profile"] != manifest["profile"]
            or row["deadline"] != manifest["deadline"]
            or resources._unpack(row["policy_json"], row["policy_digest"]) != bound["policy"]):
        raise _error("RESOURCE_BINDING_MISMATCH")


def _origins(db, run_id, generation, now):
    for item in db.execute("SELECT * FROM attempts WHERE run_id=?", (run_id,)):
        origin = db.execute("SELECT * FROM authority_attempt_origins WHERE attempt_id=?", (item["attempt_id"],)).fetchone()
        attempt = resources._unpack(item["attempt_json"], item["attempt_digest"])
        if (origin is None or origin["attempt_digest"] != item["attempt_digest"]
                or origin["actor_id"] != "validator" or origin["context"] != "validator-context"
                or type(origin["permission_generation"]) is not int or origin["permission_generation"] != generation
                or type(origin["recorded_at"]) is not int
                or not attempt["finished_at"] <= origin["recorded_at"] <= now):
            raise _error("EVIDENCE_ORIGIN_INVALID")


def _receipt(db, run_id, bound, book):
    row = db.execute("SELECT * FROM authority_run_receipts WHERE run_id=?", (run_id,)).fetchone()
    if row is None:
        raise _error("NOT_FINALIZED")
    try:
        value = resources._unpack(row["payload_json"], row["digest"])
        require_object(value, {"schema_version", "kind", "run_id", "manifest_ref", "bundle_ref",
            "decision_ref", "evidence_ref", "closure_ref", "assurance", "purpose", "created_at",
            "permission_generation", "authority_connected", "resource_closure_verified",
            "input_materialization_verified", "adoption_verified", "ci_eligible"})
        for field in ("created_at", "permission_generation"):
            if type(value[field]) is not int or not 0 <= value[field] <= MAX_INTEGER or value[field] != row[field]:
                raise _error("STORAGE_CORRUPT")
        for field in ("authority_connected", "resource_closure_verified"):
            if value[field] is not True:
                raise _error("STORAGE_CORRUPT")
        for field in ("adoption_verified", "ci_eligible"):
            if value[field] is not False:
                raise _error("STORAGE_CORRUPT")
        materialized = fixture_admission.materialized_run(db, bound, row["created_at"])
        if type(value["input_materialization_verified"]) is not bool or value["input_materialization_verified"] != materialized:
            raise _error("STORAGE_CORRUPT")
        if (type(value["schema_version"]) is not int or value["schema_version"] != 1
                or value["kind"] != "authority_run_receipt" or value["run_id"] != run_id
                or value["purpose"] != bound["manifest"]["purpose"]):
            raise _error("STORAGE_CORRUPT")
        objects = {field: _artifact(db, value[field], run_id) for field in
            ("manifest_ref", "bundle_ref", "decision_ref", "evidence_ref", "closure_ref")}
        for field, kind in (("manifest_ref", "run_manifest"), ("bundle_ref", "bound_bundle"),
                           ("decision_ref", "run_decision"), ("evidence_ref", "evidence"), ("closure_ref", "resource_closure")):
            if value[field] != content_ref(kind, run_id, objects[field]):
                raise _error("STORAGE_CORRUPT")
        _origins(db, run_id, row["permission_generation"], row["created_at"])
        terminal = book.get_terminal(run_id)
        if (objects["manifest_ref"] != bound["manifest"] or objects["bundle_ref"] != bound
                or objects["decision_ref"] != terminal["decision"]
                or value["assurance"] != terminal["decision"]["assurance"]):
            raise _error("STORAGE_CORRUPT")
        closure = objects["closure_ref"]
        require_object(closure, {"schema_version", "kind", "run_id", "manifest_digest",
                                 "closed_at", "resources", "budget_closure"})
        require_object(closure["resources"], set(resources.COUNTERS) |
                       {"slots", "unsettled", "total_tokens", "global_api_cost_usd_micros"})
        resource_row = db.execute("SELECT closed_at FROM resource_runs WHERE run_id=?", (run_id,)).fetchone()
        if (type(closure["schema_version"]) is not int or closure["schema_version"] != 1
                or type(closure["closed_at"]) is not int
                or not bound["manifest"]["created_at"] <= closure["closed_at"] <= row["created_at"]
                or resource_row is None or resource_row[0] != closure["closed_at"]
                or any(type(n) is not int or not 0 <= n <= MAX_INTEGER for n in closure["resources"].values())
                or closure["resources"]["slots"] != 0 or closure["resources"]["unsettled"] != 0
                or closure["resources"]["total_tokens"] != closure["resources"]["input_tokens"] + closure["resources"]["output_tokens"]
                or closure.get("kind") != "resource_closure" or closure.get("run_id") != run_id
                or closure.get("manifest_digest") != value["manifest_ref"]["digest"]
                or closure.get("budget_closure") is not True):
            raise _error("STORAGE_CORRUPT")
        evidence = objects["evidence_ref"]
        aggregate_row = db.execute("SELECT aggregate_json,aggregate_digest FROM aggregates WHERE run_id=? AND aggregate_digest=?",
            (run_id, terminal["decision"]["aggregate_digest"])).fetchone()
        if aggregate_row is None:
            raise _error("STORAGE_CORRUPT")
        aggregate = resources._unpack(aggregate_row[0], aggregate_row[1])
        expected = _evidence_payload(run_id, bound, aggregate["observed_at"], row["created_at"],
            row["permission_generation"], value["manifest_ref"], value["bundle_ref"], value["decision_ref"], value["closure_ref"],
            input_materialization_verified=materialized)
        if evidence != expected:
            raise _error("STORAGE_CORRUPT")
        revocations = list(db.execute("SELECT * FROM authority_run_events WHERE run_id=?", (run_id,)))
        if row["revoked_at"] is None:
            if revocations:
                raise _error("STORAGE_CORRUPT")
        else:
            if (type(row["revoked_at"]) is not int or not row["created_at"] <= row["revoked_at"] <= MAX_INTEGER
                    or len(revocations) != 1):
                raise _error("STORAGE_CORRUPT")
            event_row = revocations[0]
            event = resources._unpack(event_row["payload_json"], event_row["digest"])
            if (event_row["event_id"] != "revoke-" + row["digest"]
                    or event_row["created_at"] != row["revoked_at"]
                    or event != {"kind": "evidence_revocation", "run_id": run_id,
                        "receipt_digest": row["digest"], "revoked_at": row["revoked_at"]}):
                raise _error("STORAGE_CORRUPT")
    except (ContractError, run_evidence.EvidenceError, resources.ResourceError, KeyError, TypeError, ValueError):
        raise _error("STORAGE_CORRUPT") from None
    return row, value


def _evidence_payload(run_id, bound, observed_at, now, generation, manifest_ref, bundle_ref, decision_ref, closure_ref,
                      *, input_materialization_verified=False):
    purpose = "baseline_comparison" if bound["manifest"]["purpose"] == "baseline_candidate" else "normal"
    lifetime = run_evidence.BASELINE_SECONDS if purpose == "baseline_comparison" else run_evidence.FRESHNESS_SECONDS
    return {"schema_version": 1, "kind": "evidence", "evidence_id": run_id,
        "subject_ref": manifest_ref, "conditions_ref": bundle_ref, "decision_ref": decision_ref,
        "closure_ref": closure_ref, "purpose": purpose, "observed_at": observed_at,
        "collected_at": now, "valid_until": min(MAX_INTEGER, observed_at + lifetime),
        "retention_until": min(MAX_INTEGER, observed_at + run_evidence.RETENTION_SECONDS),
        "permission_generation": generation, "authority_connected": True,
        "resource_closure_verified": True, "input_materialization_verified": input_materialization_verified, "ci_eligible": False}


def execute(store, db, request, actor_id, context, now, resolve_bound):
    """固定extensionから呼ぶ。resolve_boundは同DBの保存実体だけを読む。"""
    action, run_id = request["action"], request["run_id"]
    bound, baseline = resolve_bound(run_id)
    digest = run_evidence.bound_bundle_digest(bound)
    book = run_evidence.RunEvidenceBook(db, now=now, allowed_bindings={run_id: digest})
    profile = fixed_profile()
    if bound["manifest"]["environment_ref"]["digest"] != profile["isolation_digest"]:
        raise _error("EXECUTION_PROFILE_MISMATCH")
    _resource_binding(db, bound)
    permission_generation = store._permission_generation(db)
    if action == "evidence_open":
        return book.start_run(bound, profile, baseline)
    view = book.get_run(run_id)
    if view["bundle_digest"] != digest or view["execution_profile"] != profile:
        raise _error("BINDING_MISMATCH")
    if action == "evidence_record":
        attempt = request["attempt"]
        joined = _operation(db, attempt, now)
        binding = attempt["expected_binding"]
        entries = [entry for entry in bound["plan"]["entries"] if all(entry[k] == binding[k]
            for k in ("obligation_id", "case_id", "trial_id")) and entry["variant"] == attempt["variant"]]
        if (len(entries) != 1 or content_ref("trial_entry", "entry", entries[0])["digest"] != joined["entry_digest"]):
            raise _error("ENTRY_NOT_PLANNED")
        receipt = book.record_attempt(attempt)
        if receipt["accepted"]:
            attempt_digest = run_evidence._attempt(attempt)[1]
            previous = db.execute("SELECT * FROM authority_attempt_origins WHERE attempt_id=?", (attempt["attempt_id"],)).fetchone()
            if previous is not None and previous["attempt_digest"] != attempt_digest:
                raise _error("STORAGE_CORRUPT")
            db.execute("INSERT OR IGNORE INTO authority_attempt_origins VALUES(?,?,?,?,?,?)",
                (attempt["attempt_id"], attempt_digest, actor_id, context, permission_generation, now))
        return {**receipt, "authority_connected": True, "ci_eligible": False}
    if action == "evidence_finalize":
        previous = db.execute("SELECT 1 FROM authority_run_receipts WHERE run_id=?", (run_id,)).fetchone()
        if previous is not None:
            book.current_use(run_id, run_evidence._binding_summary(bound, profile, baseline))
            return _receipt(db, run_id, bound, book)[1]
        snapshot = resources.ResourceBook(db).snapshot(run_id, now)
        if not snapshot["closed"] or not snapshot["budget_closure"] or snapshot["cancelled"]:
            raise _error("RESOURCE_CLOSURE_REQUIRED")
        _origins(db, run_id, permission_generation, now)
        if store._actor_revoked(db, "validator"):
            raise _error("EVIDENCE_ORIGIN_INVALID")
        for item in db.execute("SELECT * FROM attempts WHERE run_id=?", (run_id,)):
            _operation(db, resources._unpack(item["attempt_json"], item["attempt_digest"]), now)
        terminal = book.finalize(run_id)
        manifest_ref = _save(db, run_id, "run_manifest", run_id, bound["manifest"])
        bundle_ref = _save(db, run_id, "bound_bundle", run_id, bound)
        decision_ref = _save(db, run_id, "run_decision", run_id, terminal["decision"])
        # lease/現在時刻で変わるsnapshotを過去のclosure本文へ再解釈しない。
        closure = {"schema_version": 1, "kind": "resource_closure", "run_id": run_id,
            "manifest_digest": snapshot["manifest_digest"], "closed_at": snapshot["closed_at"],
            "resources": deepcopy(snapshot["resources"]), "budget_closure": True}
        closure_ref = _save(db, run_id, "resource_closure", run_id, closure)
        aggregate = book.aggregate(run_id)
        observed_at = aggregate["observed_at"]
        materialized = fixture_admission.materialized_run(db, bound, now)
        evidence = _evidence_payload(run_id, bound, observed_at, now, permission_generation,
            manifest_ref, bundle_ref, decision_ref, closure_ref, input_materialization_verified=materialized)
        evidence_ref = _save(db, run_id, "evidence", run_id, evidence)
        value = {"schema_version": 1, "kind": "authority_run_receipt", "run_id": run_id,
            "manifest_ref": manifest_ref, "bundle_ref": bundle_ref, "decision_ref": decision_ref,
            "evidence_ref": evidence_ref, "closure_ref": closure_ref,
            "assurance": terminal["decision"]["assurance"], "purpose": bound["manifest"]["purpose"],
            "created_at": now, "permission_generation": permission_generation,
            "authority_connected": True, "resource_closure_verified": True,
            "input_materialization_verified": materialized, "adoption_verified": False, "ci_eligible": False}
        raw = canonical_bytes(value)
        db.execute("INSERT INTO authority_run_receipts VALUES(?,?,?,?,?,NULL)",
            (run_id, raw.decode("utf-8"), hashlib.sha256(raw).hexdigest(), permission_generation, now))
        return value
    row, value = _receipt(db, run_id, bound, book)
    if action == "evidence_revoke":
        if row["revoked_at"] is None:
            event = {"kind": "evidence_revocation", "run_id": run_id, "receipt_digest": row["digest"], "revoked_at": now}
            raw = canonical_bytes(event)
            db.execute("INSERT INTO authority_run_events VALUES(?,?,?,?,?)", ("revoke-" + row["digest"], run_id,
                raw.decode("utf-8"), hashlib.sha256(raw).hexdigest(), now))
            db.execute("UPDATE authority_run_receipts SET revoked_at=? WHERE run_id=?", (now, run_id))
        return {"run_id": run_id, "revoked": True, "ci_eligible": False}
    if action == "evidence_current":
        # 独立storeの局所stateも再検査するが、自己申告VALIDを権限にしない。
        book.current_use(run_id, run_evidence._binding_summary(bound, profile, baseline))
        reasons = []
        if request["expected_bundle_digest"] != digest:
            reasons.append("BINDING_MISMATCH")
        if row["revoked_at"] is not None:
            reasons.append("EVIDENCE_REVOKED")
        if row["permission_generation"] != permission_generation or store._actor_revoked(db, "validator"):
            reasons.append("EVIDENCE_ORIGIN_INVALID")
        if view["state"] == "HOLD":
            reasons.append(view["hold_reason"] or "HOLD")
        evidence = _artifact(db, value["evidence_ref"], run_id)
        if now > min(evidence["valid_until"], evidence["retention_until"]):
            reasons.append("EVIDENCE_EXPIRED")
        snapshot = resources.ResourceBook(db).snapshot(run_id, now)
        if not snapshot["closed"] or not snapshot["budget_closure"] or snapshot["cancelled"]:
            reasons.append("RESOURCE_CLOSURE_REQUIRED")
        if not value["input_materialization_verified"]:
            reasons.append("INPUT_MATERIALIZATION_UNVERIFIED")
        reasons.append("ADOPTION_NOT_CONNECTED")
        return {"run_id": run_id, "checked_at": now, "receipt": value,
            "authority_connected": True, "reasons": reasons, "use": False, "ci_eligible": False}
    raise _error("INVALID_ACTION")


def baseline_source(store, db, run_id, now, resolve_bound):
    """採択serviceへ、同transactionで再検査した保存実体だけを渡す。"""
    bound, _ = resolve_bound(run_id)
    current = execute(store, db, {"action": "evidence_current", "run_id": run_id,
        "expected_bundle_digest": run_evidence.bound_bundle_digest(bound)},
        "manager", "manager-context", now, resolve_bound)
    receipt = current["receipt"]
    evidence = _artifact(db, receipt["evidence_ref"], run_id)
    row = db.execute("SELECT revoked_at FROM authority_run_receipts WHERE run_id=?", (run_id,)).fetchone()
    return {"bound": bound, "receipt": receipt,
        "decision": _artifact(db, receipt["decision_ref"], run_id),
        "evidences": [evidence], "closure": _artifact(db, receipt["closure_ref"], run_id),
        "evidence_states": {evidence["evidence_id"]: {"revoked": row[0] is not None, "deleted": False}},
        # 初回採択を検査するため、採択未接続そのものは候補の欠陥に数えない。
        "reasons": [reason for reason in current["reasons"] if reason != "ADOPTION_NOT_CONNECTED"]}
