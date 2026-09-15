"""固定fixtureの契約移行候補を保存する。同一transactionの内部境界。"""
from copy import deepcopy

from . import fixture_admission, resources, transition_materialization, candidate_sections
from .adoption import AdoptionError
from .read_checks import checked_read
from .contracts import ContractError, MAX_INTEGER, require_id, require_object, require_ref
from .run_contracts import content_ref
from .transition_migrations import TABLES, create_schema

PURPOSES = {"contract_old_regression", "contract_candidate"}
ACTIONS = {"contract_candidate_prepare": {"validator"}, "contract_candidate_begin": {"operator"}, "contract_candidate_read": {"operator", "validator"}}
_BASE = {"schema_version", "action", "request_id"}
_PRECONDITIONS = {"proposal_id", "baseline_series_id", "expected_contract_ref", "expected_baseline_ref"}
FIELDS = {
    "contract_candidate_prepare": _BASE | _PRECONDITIONS | {"candidate_id", "old_run_id", "new_run_id"},
    "contract_candidate_begin": _BASE | {"candidate_id", "side"},
    "contract_candidate_read": _BASE | {"candidate_id", "side"},
}




def validate_request(request):
    try:
        require_object(request, FIELDS[request["action"]])
        if type(request["schema_version"]) is not int or request["schema_version"] != 1:
            raise ContractError()
        for field in ("request_id", "candidate_id"):
            require_id(request[field])
        if request["action"] == "contract_candidate_prepare":
            for field in ("proposal_id", "baseline_series_id", "old_run_id", "new_run_id"):
                require_id(request[field])
            for field, kind in (("expected_contract_ref", "evaluation_contract"), ("expected_baseline_ref", "baseline")):
                require_ref(request[field])
                if request[field]["kind"] != kind:
                    raise ContractError()
            if request["old_run_id"] == request["new_run_id"]:
                raise ContractError()
        elif type(request["side"]) is not str or request["side"] not in {"old", "new"}:
            raise ContractError()
    except (ContractError, KeyError, TypeError):
        raise AdoptionError("INVALID_REQUEST") from None
    return deepcopy(request)


def _source_prepared(db, source_id, now):
    row = db.execute("SELECT * FROM fixture_admissions WHERE run_id=?", (source_id,)).fetchone()
    if row is None:
        raise AdoptionError("FIXTURE_ADMISSION_INVALID")
    return fixture_admission._verify(row, now)["prepared"]


def _build(db, transition, old_id, new_id, created_at, now):
    from .run_contracts import validate_evaluation_contract
    from .following_contracts import build_following_runs
    from . import regression_runs
    previous = validate_evaluation_contract(transition['previous_contract'])
    following = validate_evaluation_contract(transition['next_contract'])
    if following['generation'] != previous['generation'] + 1:
        raise AdoptionError('CANDIDATE_INVALID')
    expected_version = 1 if previous['generation'] == 1 else 2
    if type(transition.get('schema_version')) is not int or transition['schema_version'] != expected_version:
        raise AdoptionError('CANDIDATE_INVALID')
    if expected_version == 2:
        row = db.execute('SELECT * FROM eval_runs WHERE run_id=?', (transition['source_run_ref']['id'],)).fetchone()
        if row is None or row['contract_generation'] != previous['generation']:
            raise AdoptionError('CANDIDATE_INVALID')
        source = regression_runs.for_run(db, row, now)
        if previous['use_cases'] == ['UC-LLM']:
            from .llm_transitions import build_following
            return build_following(previous, following, baseline_record=transition['baseline_record'],
                source_prepared=source, now=created_at, old_run_id=old_id, new_run_id=new_id)
        return build_following_runs(previous, following, baseline_record=transition['baseline_record'],
            source_prepared=source, now=created_at, old_run_id=old_id, new_run_id=new_id)
    source = _source_prepared(db, transition["source_run_ref"]["id"], now)
    if previous["use_cases"] == ["UC-LLM"]:
        from .llm_transitions import build
        return build(previous, following, baseline_record=transition["baseline_record"], source_prepared=source,
            now=created_at, old_run_id=old_id, new_run_id=new_id, following_registry=transition.get("following_registry"))
    worker, lock, profile = fixture_admission.execution_context()
    return transition_materialization.build_transition_runs(
        transition["previous_contract"], transition["next_contract"],
        baseline_record=transition["baseline_record"], source_prepared=source,
        worker_source=worker, runtime_lock=lock, execution_profile=profile,
        now=created_at, old_run_id=old_id, new_run_id=new_id)


@checked_read
def load_candidate(db, candidate_id, now):
    """保存時の構造を再検査する。期限・現在pointerはfresh検査へ分ける。"""
    row = db.execute("SELECT * FROM transition_candidates WHERE candidate_id=?", (candidate_id,)).fetchone()
    if row is None:
        raise AdoptionError("CANDIDATE_MISSING")
    try:
        value = resources._unpack(row["payload_json"], row["digest"])
        require_object(value, _PRECONDITIONS | {"candidate_id", "proposal_digest", "runs"})
        if (value["candidate_id"] != candidate_id or row["actor_id"] != "validator"
                or row["context"] != "validator-context"
                or type(row["created_at"]) is not int or not 0 <= row["created_at"] <= now <= MAX_INTEGER
                or type(row["permission_generation"]) is not int or not 0 <= row["permission_generation"] <= MAX_INTEGER
                or any(row[key] != value[key] for key in ("proposal_id", "proposal_digest", "baseline_series_id"))):
            raise ContractError()
        proposal = db.execute("SELECT * FROM eval_proposals WHERE id=?", (row["proposal_id"],)).fetchone()
        value["runs"] = candidate_sections.unpack(db, candidate_id, value["runs"])
        transition = value["runs"]["transition"]
        contract = transition["next_contract"]
        if (proposal is None or proposal["digest"] != row["proposal_digest"]
                or resources._unpack(proposal["payload_json"], proposal["digest"]) != contract
                or proposal["actor_id"] != "manager" or proposal["context"] != "manager-context"
                or type(proposal["generation"]) is not int or proposal["generation"] != contract["generation"]):
            raise ContractError()
        old = transition["previous_contract"]
        source = db.execute("SELECT * FROM eval_runs WHERE run_id=?", (transition["source_run_ref"]["id"],)).fetchone()
        baseline = transition["baseline_record"]
        baselines = db.execute("SELECT * FROM baseline_adoptions WHERE series_id=? AND generation=?",
            (row["baseline_series_id"], baseline["generation"])).fetchall()
        if (source is None or source["contract_series_id"] != proposal["series_id"]
                or source["contract_generation"] != old["generation"] or len(baselines) != 1
                or resources._unpack(baselines[0]["baseline_json"], baselines[0]["baseline_digest"]) != baseline):
            raise ContractError()
        history = db.execute("SELECT * FROM eval_adoptions WHERE series_id=? AND generation=?",
            (proposal["series_id"], old["generation"])).fetchone()
        if history is None or resources._unpack(history["payload_json"], history["digest"]) != old:
            raise ContractError()
        if (value["expected_contract_ref"] != content_ref("evaluation_contract", old["contract_id"], old)
                or value["expected_baseline_ref"] != transition["baseline_ref"]
                or value["baseline_series_id"] != transition["baseline_record"]["baseline_series_id"]):
            raise ContractError()
        old_id = value["runs"]["old"]["bound_run"]["manifest"]["run_id"]
        new_id = value["runs"]["new"]["bound_run"]["manifest"]["run_id"]
        maps = [tuple(item) for item in db.execute(
            "SELECT run_id,candidate_id,side FROM transition_runs WHERE candidate_id=? ORDER BY side", (candidate_id,))]
        if maps != [(new_id, candidate_id, "new"), (old_id, candidate_id, "old")]:
            raise ContractError()
        expected = _build(db, transition, old_id, new_id, row["created_at"], now)
        if value["runs"] != expected:
            raise ContractError()
    except (ContractError, resources.ResourceError, KeyError, TypeError, ValueError, OSError):
        raise AdoptionError("CANDIDATE_INVALID") from None
    return row, value


def fresh_candidate(store, db, candidate_id, now, check_transition):
    row, value = load_candidate(db, candidate_id, now)
    if (row["permission_generation"] != store._permission_generation(db)
            or store._actor_revoked(db, "validator") or store._actor_revoked(db, "manager")):
        raise AdoptionError("CANDIDATE_EXPIRED")
    checked = check_transition({**{key: value[key] for key in _PRECONDITIONS},
        "schema_version": 1, "action": "contract_preflight", "request_id": candidate_id})
    if checked["transition"] != value["runs"]["transition"]:
        raise AdoptionError("CANDIDATE_INVALID")
    return row, value


def prepare(store, db, request, now, actor_id, context, check_transition):
    if db.execute("SELECT 1 FROM transition_candidates WHERE candidate_id=?", (request["candidate_id"],)).fetchone():
        raise AdoptionError("CANDIDATE_CONFLICT")
    for identifier in (request["old_run_id"], request["new_run_id"]):
        if (db.execute("SELECT 1 FROM eval_runs WHERE run_id=?", (identifier,)).fetchone()
                or db.execute("SELECT 1 FROM transition_runs WHERE run_id=?", (identifier,)).fetchone()
                or db.execute("SELECT 1 FROM fixture_admissions WHERE run_id=?", (identifier,)).fetchone()
                or db.execute("SELECT 1 FROM resource_runs WHERE run_id=?", (identifier,)).fetchone()):
            raise AdoptionError("RUN_CONFLICT")
    checked = check_transition(request)
    runs = _build(db, checked["transition"], request["old_run_id"], request["new_run_id"], now, now)
    value = {key: deepcopy(request[key]) for key in _PRECONDITIONS | {"candidate_id"}}
    value.update(proposal_digest=checked["proposal_digest"], runs=runs)
    stored = deepcopy(value)
    stored["runs"] = candidate_sections.pack(db, request["candidate_id"], runs)
    raw, digest = resources._packed(stored)
    db.execute("INSERT INTO transition_candidates VALUES(?,?,?,?,?,?,?,?,?,?)",
        (request["candidate_id"], request["proposal_id"], checked["proposal_digest"], request["baseline_series_id"],
         raw, digest, now, store._permission_generation(db), actor_id, context))
    for side in ("old", "new"):
        db.execute("INSERT INTO transition_runs VALUES(?,?,?)", (request[side + "_run_id"], request["candidate_id"], side))
    _, saved = load_candidate(db, request["candidate_id"], now)
    return {"candidate_id": request["candidate_id"],
        "candidate_ref": candidate_sections.reference(saved),
        "runs": stored["runs"], "adoption_verified": False}


def candidate_run(db, bound_row, now):
    """採択後も候補の版へ固定し、通常runへ読み替えない。"""
    mapping = db.execute("SELECT * FROM transition_runs WHERE run_id=?", (bound_row["run_id"],)).fetchone()
    if mapping is None:
        raise AdoptionError("CANDIDATE_MISSING")
    _, value = load_candidate(db, mapping["candidate_id"], now)
    result = value["runs"][mapping["side"]]
    bound = result["bound_run"]
    proposal = db.execute("SELECT series_id FROM eval_proposals WHERE id=?", (value["proposal_id"],)).fetchone()
    if (bound_row["contract_series_id"] != proposal[0]
            or bound_row["contract_generation"] != bound["contract"]["generation"]
            or resources._unpack(bound_row["manifest_json"], bound_row["manifest_digest"]) != bound["manifest"]
            or resources._unpack(bound_row["plan_json"], bound_row["plan_digest"]) != bound["plan"]):
        raise AdoptionError("CANDIDATE_INVALID")
    return mapping, bound, result["baseline_context"]


def materialized_run(db, bound, now):
    row = db.execute("SELECT * FROM eval_runs WHERE run_id=?", (bound["manifest"]["run_id"],)).fetchone()
    if row is None or candidate_run(db, row, now)[1] != bound:
        raise AdoptionError("CANDIDATE_INVALID")
    return True
