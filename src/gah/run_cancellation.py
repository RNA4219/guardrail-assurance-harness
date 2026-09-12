"""通常runの停止済み取消しを、未精算の予約を残して原子的に確定する。"""
from copy import deepcopy

from .adoption import AdoptionError
from .contracts import ContractError, MAX_INTEGER, require_object, require_uint
from . import assurance_authority, resources, run_evidence, run_outputs, fixture_admission
from .run_contracts import content_ref

KIND = "authority_cancel_receipt"
FIELDS = {"schema_version", "kind", "run_id", "manifest_ref", "bundle_ref", "decision_ref",
    "evidence_ref", "closure_ref", "assurance", "purpose", "created_at", "permission_generation",
    "authority_connected", "input_materialization_verified", "resource_stop_verified", "budget_closure",
    "execution_status", "adoption_verified", "ci_eligible"}


def exists(db, run_id):
    return db.execute("SELECT 1 FROM authority_artifacts WHERE kind=? AND id=?", (KIND, run_id)).fetchone() is not None


def _book(db, bound, baseline, now, *, create=False):
    run_id = bound["manifest"]["run_id"]
    digest = run_evidence.bound_bundle_digest(bound)
    book = run_evidence.RunEvidenceBook(db, now=now, allowed_bindings={run_id: digest})
    profile = assurance_authority.fixed_profile()
    if bound["manifest"]["environment_ref"]["digest"] != profile["isolation_digest"]:
        raise AdoptionError("EXECUTION_PROFILE_MISMATCH")
    if create:
        book.start_run(bound, profile, baseline)
    view = book.get_run(run_id)
    if (view["bundle_digest"] != digest or view["execution_profile"] != profile
            or view["baseline_context"] != baseline):
        raise AdoptionError("BINDING_MISMATCH")
    return book


def _stopped(db, run_id, now):
    snapshot = resources.ResourceBook(db).snapshot(run_id, now)
    if not snapshot["cancelled"]:
        raise AdoptionError("CANCEL_REQUIRED")
    if snapshot["resources"]["slots"] != 0:
        raise AdoptionError("STOP_UNCONFIRMED")
    for operation in db.execute("SELECT * FROM resource_operations WHERE run_id=?", (run_id,)):
        resources._unpack(operation["reservation_json"], operation["reservation_digest"])
        if operation["intended_at"] is None:
            if operation["released"] != 1:
                raise AdoptionError("STOP_UNCONFIRMED")
        elif (operation["released"] or operation["stopped_at"] is None
                or not operation["intended_at"] <= operation["stopped_at"] <= now):
            raise AdoptionError("STOP_UNCONFIRMED")
    return snapshot


def _origins(db, run_id, generation, now):
    # 現在の失効で過去の観測を消さない。保存時の主体と単調な世代を検査する。
    for row in db.execute("SELECT * FROM attempts WHERE run_id=?", (run_id,)):
        attempt = resources._unpack(row["attempt_json"], row["attempt_digest"])
        origin = db.execute("SELECT * FROM authority_attempt_origins WHERE attempt_id=?", (row["attempt_id"],)).fetchone()
        if (origin is None or origin["attempt_digest"] != row["attempt_digest"]
                or origin["actor_id"] != "validator" or origin["context"] != "validator-context"
                or type(origin["permission_generation"]) is not int
                or not 0 <= origin["permission_generation"] <= generation
                or type(origin["recorded_at"]) is not int
                or not attempt["finished_at"] <= origin["recorded_at"] <= now):
            raise AdoptionError("EVIDENCE_ORIGIN_INVALID")
        # 未精算の操作はAttemptとして採用済みでない。停止記録・予約は別に残す。
        assurance_authority._operation(db, attempt, now)


def load(db, bound, baseline, now):
    run_id = bound["manifest"]["run_id"]
    rows = db.execute("SELECT * FROM authority_artifacts WHERE kind=? AND id=?", (KIND, run_id)).fetchall()
    if len(rows) != 1 or rows[0]["run_id"] != run_id:
        raise AdoptionError("CANCELLATION_MISSING")
    try:
        receipt = resources._unpack(rows[0]["payload_json"], rows[0]["digest"])
        require_object(receipt, FIELDS)
        require_uint(receipt["created_at"])
        require_uint(receipt["permission_generation"])
        if (type(receipt["schema_version"]) is not int or receipt["schema_version"] != 1
                or receipt["kind"] != KIND or receipt["run_id"] != run_id
                or receipt["purpose"] != "regression" or bound["manifest"]["purpose"] != "regression"
                or receipt["permission_generation"] > db.execute(
                    "SELECT value FROM adoption_meta WHERE key='permission_generation'").fetchone()[0]
                or not bound["manifest"]["created_at"] <= receipt["created_at"] <= now
                or receipt["execution_status"] != "CANCELLED" or type(receipt["budget_closure"]) is not bool
                or any(receipt[k] is not True for k in ("authority_connected", "input_materialization_verified", "resource_stop_verified"))
                or any(receipt[k] is not False for k in ("adoption_verified", "ci_eligible"))):
            raise AdoptionError("CANCELLATION_INVALID")
        assurance_authority._resource_binding(db, bound)
        snapshot = _stopped(db, run_id, now)
        book = _book(db, bound, baseline, now)
        terminal = book.get_terminal(run_id)
        _origins(db, run_id, receipt["permission_generation"], receipt["created_at"])
        objects = {}
        for field, kind in (("manifest_ref", "run_manifest"), ("bundle_ref", "bound_bundle"),
                ("decision_ref", "run_decision"), ("evidence_ref", "evidence"), ("closure_ref", "resource_cancellation")):
            value = assurance_authority._artifact(db, receipt[field], run_id)
            if receipt[field] != content_ref(kind, run_id, value):
                raise AdoptionError("CANCELLATION_INVALID")
            objects[field] = value
        if (objects["manifest_ref"] != bound["manifest"] or objects["bundle_ref"] != bound
                or objects["decision_ref"] != terminal["decision"]
                or receipt["assurance"] != terminal["decision"]["assurance"]):
            raise AdoptionError("CANCELLATION_INVALID")
        closure = objects["closure_ref"]
        require_object(closure, {"schema_version", "kind", "run_id", "manifest_digest", "cancelled_at",
            "stopped", "resources", "budget_closure"})
        require_object(closure["resources"], set(resources.COUNTERS) |
            {"slots", "unsettled", "total_tokens", "global_api_cost_usd_micros"})
        if (type(closure["schema_version"]) is not int or closure["schema_version"] != 1
                or closure["kind"] != "resource_cancellation" or closure["run_id"] != run_id
                or closure["manifest_digest"] != receipt["manifest_ref"]["digest"]
                or type(closure["cancelled_at"]) is not int or closure["cancelled_at"] != receipt["created_at"]
                or closure["stopped"] is not True or closure["budget_closure"] is not receipt["budget_closure"]
                or closure["resources"]["slots"] != 0
                or any(type(n) is not int or not 0 <= n <= MAX_INTEGER for n in closure["resources"].values())
                or closure["resources"]["total_tokens"] != closure["resources"]["input_tokens"] + closure["resources"]["output_tokens"]
                or closure["budget_closure"] and closure["resources"]["unsettled"] != 0):
            raise AdoptionError("CANCELLATION_INVALID")
        aggregate = db.execute("SELECT aggregate_json,aggregate_digest FROM aggregates WHERE run_id=? AND aggregate_digest=?",
            (run_id, terminal["decision"]["aggregate_digest"])).fetchone()
        if aggregate is None:
            raise AdoptionError("CANCELLATION_INVALID")
        value = resources._unpack(aggregate[0], aggregate[1])
        expected = assurance_authority._evidence_payload(run_id, bound, value["observed_at"], receipt["created_at"],
            receipt["permission_generation"], receipt["manifest_ref"], receipt["bundle_ref"], receipt["decision_ref"],
            receipt["closure_ref"], input_materialization_verified=True)
        if expected != objects["evidence_ref"]:
            raise AdoptionError("CANCELLATION_INVALID")
        outputs = run_outputs.read(db, bound, receipt)
        current_assurance = "HOLD" if book.get_run(run_id)["state"] == "HOLD" or snapshot["breached"] else receipt["assurance"]
        return {"receipt": receipt, "outputs": outputs, "snapshot": snapshot, "assurance": current_assurance}
    except (ContractError, resources.ResourceError, run_evidence.EvidenceError, KeyError, TypeError, ValueError) as error:
        raise AdoptionError("CANCELLATION_INVALID") from None


def finalize(store, db, bound, baseline, now):
    run_id = bound["manifest"]["run_id"]
    if bound["manifest"]["purpose"] != "regression":
        raise AdoptionError("CI_PURPOSE_REQUIRED")
    if exists(db, run_id):
        return load(db, bound, baseline, now)["receipt"]
    if db.execute("SELECT 1 FROM authority_run_receipts WHERE run_id=?", (run_id,)).fetchone():
        raise AdoptionError("ALREADY_TERMINAL")
    assurance_authority._resource_binding(db, bound)
    snapshot = _stopped(db, run_id, now)
    book = _book(db, bound, baseline, now, create=True)
    generation = store._permission_generation(db)
    _origins(db, run_id, generation, now)
    terminal = book.finalize(run_id)
    manifest_ref = assurance_authority._save(db, run_id, "run_manifest", run_id, bound["manifest"])
    bundle_ref = assurance_authority._save(db, run_id, "bound_bundle", run_id, bound)
    decision_ref = assurance_authority._save(db, run_id, "run_decision", run_id, terminal["decision"])
    closure = {"schema_version": 1, "kind": "resource_cancellation", "run_id": run_id,
        "manifest_digest": manifest_ref["digest"], "cancelled_at": now, "stopped": True,
        "resources": deepcopy(snapshot["resources"]), "budget_closure": snapshot["budget_closure"]}
    closure_ref = assurance_authority._save(db, run_id, "resource_cancellation", run_id, closure)
    if not fixture_admission.materialized_run(db, bound, now):
        raise AdoptionError("INPUT_MATERIALIZATION_UNVERIFIED")
    aggregate = book.aggregate(run_id)
    evidence = assurance_authority._evidence_payload(run_id, bound, aggregate["observed_at"], now, generation,
        manifest_ref, bundle_ref, decision_ref, closure_ref, input_materialization_verified=True)
    evidence_ref = assurance_authority._save(db, run_id, "evidence", run_id, evidence)
    receipt = {"schema_version": 1, "kind": KIND, "run_id": run_id, "manifest_ref": manifest_ref,
        "bundle_ref": bundle_ref, "decision_ref": decision_ref, "evidence_ref": evidence_ref, "closure_ref": closure_ref,
        "assurance": terminal["decision"]["assurance"], "purpose": "regression", "created_at": now,
        "permission_generation": generation, "authority_connected": True, "input_materialization_verified": True,
        "resource_stop_verified": True, "budget_closure": snapshot["budget_closure"], "execution_status": "CANCELLED",
        "adoption_verified": False, "ci_eligible": False}
    assurance_authority._save(db, run_id, KIND, run_id, receipt)
    run_outputs.save(db, bound, receipt)
    return receipt
