"""保存された通常runからのbaseline更新と、固定参照の履歴解決。"""
from .adoption import AdoptionError
from .contracts import MAX_INTEGER, require_digest, require_object
from .run_contracts import content_ref
from . import baseline_authority as base


KIND = "baseline_refresh_proposal"

def row_for_ref(db, series_id, reference):
    rows = db.execute("SELECT * FROM baseline_adoptions WHERE series_id=? AND baseline_digest=?",
        (series_id, reference["digest"])).fetchall()
    if len(rows) != 1:
        raise AdoptionError("BASELINE_REFERENCE_UNAVAILABLE")
    row = rows[0]
    record = base._load(row["baseline_json"], row["baseline_digest"])
    if content_ref("baseline", record["baseline_id"], record) != reference:
        raise AdoptionError("BINDING_MISMATCH")
    return row


def validate_proposal_row(row, value):
    require_object(value, {"schema_version", "kind", "proposal_id", "series_id", "run_id",
        "expected_generation", "contract_generation", "record", "comparison_context", "binding_digest", "created_at"})
    if (type(value["schema_version"]) is not int or value["schema_version"] != 1 or value["kind"] != KIND
            or row["actor_id"] != "manager" or row["context"] != "manager-context"
            or type(row["expected_generation"]) is not int or not 1 <= row["expected_generation"] < MAX_INTEGER
            or type(row["contract_generation"]) is not int or not 2 <= row["contract_generation"] <= MAX_INTEGER
            or type(row["created_at"]) is not int or not 0 <= row["created_at"] <= MAX_INTEGER
            or any(value[key] != row[key] for key in ("proposal_id", "series_id", "run_id",
                "expected_generation", "contract_generation", "created_at"))):
        raise AdoptionError("STORAGE_CORRUPT")
    for field in ("expected_generation", "contract_generation", "created_at"):
        if type(value[field]) is not int:
            raise AdoptionError("STORAGE_CORRUPT")
    for field in ("proposal_id", "series_id", "run_id"):
        base._id(value[field], "STORAGE_CORRUPT")
    require_digest(value["binding_digest"])
    record = base.validate_baseline_record(value["record"])
    comparison = base.validate_comparison_context(value["comparison_context"])
    if (record["generation"] != row["expected_generation"] + 1 or record["baseline_series_id"] != row["series_id"]
            or record["created_at"] != row["created_at"] or record["source_run_ref"]["id"] != row["run_id"]
            or comparison["expected_baseline_generation"] != row["expected_generation"]
            or comparison["expected_contract_generation"] != row["contract_generation"]
            or record["comparison_context_ref"] != content_ref("comparison_context", comparison["comparison_id"], comparison)):
        raise AdoptionError("STORAGE_CORRUPT")
    return value


def predecessor(db, proposal):
    reference = proposal["comparison_context"]["baseline_ref"]
    row = row_for_ref(db, proposal["series_id"], reference)
    if row["generation"] != proposal["expected_generation"]:
        raise AdoptionError("GENERATION_CONFLICT")
    _, record, _ = base._history(db, row)
    if record["created_at"] > proposal["created_at"] or row["adopted_at"] > proposal["created_at"]:
        raise AdoptionError("STORAGE_CORRUPT")
    return row, record


def current_predecessor(db, proposal):
    previous, record = predecessor(db, proposal)
    current = db.execute("SELECT * FROM baseline_current WHERE series_id=?", (proposal["series_id"],)).fetchone()
    if (current is None or current["generation"] != proposal["expected_generation"]
            or current["adoption_id"] != previous["adoption_id"]):
        raise AdoptionError("GENERATION_CONFLICT")
    _, current_record, _ = base._history(db, current)
    if current_record != record:
        raise AdoptionError("STORAGE_CORRUPT")
    return previous, current


def bind_candidate(db, proposal, source, now):
    from . import run_evidence, assurance_authority
    if db is None or source["bound"]["manifest"]["purpose"] != "regression":
        raise AdoptionError("SOURCE_INVALID")
    from .run_scope import require_full_source
    require_full_source(source)
    candidate = base.build_candidate(source, proposal["series_id"], proposal["proposal_id"],
        proposal["created_at"], expected_generation=proposal["expected_generation"])
    if candidate != {"record": proposal["record"], "comparison_context": proposal["comparison_context"]}:
        raise AdoptionError("PROPOSAL_MISMATCH")
    old, _ = predecessor(db, proposal)
    manifest = source["bound"]["manifest"]
    if (manifest["baseline_ref"] != proposal["comparison_context"]["baseline_ref"]
            or manifest["created_at"] < old["adopted_at"]
            or base._trusted_binding(source["bound"]) != proposal["binding_digest"]):
        raise AdoptionError("BINDING_MISMATCH")
    from . import regression_runs
    run = db.execute("SELECT * FROM eval_runs WHERE run_id=?", (manifest["run_id"],)).fetchone()
    if run is None:
        raise AdoptionError("SOURCE_INVALID")
    prepared = regression_runs.for_run(db, run, now)
    if prepared["bound_run"] != source["bound"]:
        raise AdoptionError("BINDING_MISMATCH")
    digest = run_evidence.bound_bundle_digest(source["bound"], prepared["baseline_context"])
    book = run_evidence.RunEvidenceBook(db, now=now, allowed_bindings={manifest["run_id"]: digest})
    view = book.get_run(manifest["run_id"])
    profile = (prepared["execution_profile"] if manifest["use_cases"] == ["UC-LLM"]
               else assurance_authority.fixed_profile())
    if (view["execution_profile"] != profile or view["bundle_digest"] != digest
            or view["baseline_context"] != prepared["baseline_context"]):
        raise AdoptionError("BINDING_MISMATCH")
    enriched = {**source["bound"], "baseline_context": view["baseline_context"],
        "comparison_context": candidate["comparison_context"],
        "repeat_config": base.repeat_config_for_plan(source["bound"]["plan"])}
    base.bind_baseline_record(candidate["record"], bound_run=enriched, decision=source["decision"],
        evidences=source["evidences"], closure=source["closure"], now=now, evidence_states=source["evidence_states"])
    return candidate


def propose(store, db, request, actor, context, now, resolve_source):
    expected = request["expected_generation"]
    if (type(expected) is not int or not 1 <= expected < MAX_INTEGER
            or base._current_generation(db, request["series_id"]) != expected):
        raise AdoptionError("GENERATION_CONFLICT")
    source = base._source(base._source_from_resolver(resolve_source, request["run_id"]),
        request["run_id"], now, store, purpose="regression")
    from .run_scope import require_full_source
    require_full_source(source)
    contract_generation = source["bound"]["contract"]["generation"]
    if type(contract_generation) is not int or not 2 <= contract_generation <= MAX_INTEGER:
        raise AdoptionError("SOURCE_INVALID")
    candidate = base.build_candidate(source, request["series_id"], request["proposal_id"], now, expected_generation=expected)
    proposal = {"schema_version": 1, "kind": KIND, "proposal_id": request["proposal_id"],
        "series_id": request["series_id"], "run_id": request["run_id"], "expected_generation": expected,
        "contract_generation": contract_generation, **candidate, "binding_digest": base._trusted_binding(source["bound"]), "created_at": now}
    current_predecessor(db, proposal)
    bind_candidate(db, proposal, source, now)
    raw, digest = base._pack(proposal)
    existing = db.execute("SELECT proposal_digest FROM baseline_proposals WHERE proposal_id=?", (request["proposal_id"],)).fetchone()
    if existing is None:
        db.execute("INSERT INTO baseline_proposals VALUES(?,?,?,?,?,?,?,?,?,?)", (request["proposal_id"], request["series_id"],
            request["run_id"], expected, contract_generation, raw, digest, now, actor, context))
    elif existing[0] != digest:
        raise AdoptionError("PROPOSAL_CONFLICT")
    return base._result(request["action"], request["request_id"], proposal_id=request["proposal_id"],
        series_id=request["series_id"], generation=expected + 1, proposal_digest=digest)


def history(db, current, adopted):
    row = db.execute("SELECT * FROM baseline_proposals WHERE proposal_id=?", (adopted["proposal_id"],)).fetchone()
    proposal = base._validate_proposal_row(row)
    if proposal["kind"] != KIND:
        raise AdoptionError("STORAGE_CORRUPT")
    validation = base._validation(db, adopted["validation_id"], row, proposal)
    baseline = base._load(adopted["baseline_json"], adopted["baseline_digest"])
    if (baseline != proposal["record"] or base._load(current["baseline_json"], current["baseline_digest"]) != baseline
            or current["baseline_digest"] != adopted["baseline_digest"]
            or current["series_id"] != adopted["series_id"] or adopted["series_id"] != proposal["series_id"]
            or type(current["generation"]) is not int or current["generation"] != proposal["expected_generation"] + 1
            or adopted["generation"] != current["generation"]
            or adopted["actor_id"] != row["actor_id"] or adopted["context"] != row["context"]
            or type(adopted["adopted_at"]) is not int
            or not validation["created_at"] <= adopted["adopted_at"] < validation["expires_at"]
            or adopted["permission_generation"] != validation["permission_generation"]):
        raise AdoptionError("STORAGE_CORRUPT")
    predecessor(db, proposal)
    return proposal, baseline, validation


def adopt(db, request, proposal, actor, context, now, validation):
    previous, current = current_predecessor(db, proposal)
    if (request["expected_generation"] != proposal["expected_generation"] or current is None or current["generation"] != proposal["expected_generation"]
            or current["adoption_id"] != previous["adoption_id"]):
        raise AdoptionError("GENERATION_CONFLICT")
    generation = proposal["expected_generation"] + 1
    adoption_id = "adoption-" + proposal["proposal_id"]
    raw, digest = base._pack(proposal["record"])
    if db.execute("SELECT 1 FROM baseline_adoptions WHERE adoption_id=?", (adoption_id,)).fetchone():
        raise AdoptionError("PROPOSAL_CONFLICT")
    db.execute("INSERT INTO baseline_adoptions VALUES(?,?,?,?,?,?,?,?,?,?,?)", (adoption_id, proposal["series_id"], generation,
        proposal["proposal_id"], request["validation_id"], raw, digest, now, actor, context, validation["permission_generation"]))
    changed = db.execute("UPDATE baseline_current SET generation=?,adoption_id=?,baseline_json=?,baseline_digest=? WHERE series_id=? AND generation=?",
        (generation, adoption_id, raw, digest, proposal["series_id"], proposal["expected_generation"])).rowcount
    if changed != 1:
        raise AdoptionError("GENERATION_CONFLICT")
    return base._result(request["action"], request["request_id"], series_id=proposal["series_id"], generation=generation,
        adoption_id=adoption_id, baseline_digest=digest, adoption_verified=True)


def resolve(store, db, request, actor, now, resolve_source):
    row = row_for_ref(db, request["series_id"], request["expected_baseline_ref"])
    return base._fresh(store, db, request, actor, now, resolve_source, True, pinned=row)


def revoke(db, request, actor, context, now):
    row = row_for_ref(db, request["series_id"], request["expected_baseline_ref"])
    _, record, _ = base._history(db, row)
    if request["expected_contract_ref"] != record["contract_ref"]:
        raise AdoptionError("BINDING_MISMATCH")
    if row["adopted_at"] > now:
        raise AdoptionError("STORAGE_CORRUPT")
    existing = db.execute("SELECT * FROM baseline_revocations WHERE series_id=? AND generation=?",
        (request["series_id"], row["generation"])).fetchone()
    if existing is None:
        db.execute("INSERT INTO baseline_revocations VALUES(?,?,?,?,?)", (request["series_id"], row["generation"], now, actor, context))
    elif existing["actor_id"] != actor or existing["context"] != context:
        raise AdoptionError("STORAGE_CORRUPT")
    return base._result(request["action"], request["request_id"], series_id=request["series_id"], revoked=True,
        revocation_generation=row["generation"])
