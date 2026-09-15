"""固定packと隔離workerの段階結果を、入力digestへ結び付けて検査する。"""
from copy import deepcopy
from functools import lru_cache
import hashlib
from pathlib import Path
from .contracts import ContractError, decode_document, require_object, require_uint
from . import guardrail_runtime, llm_materialization
from .docker_runner import PROFILE
from .evaluation_data import build_pack
from .normalized import normalize_generic, validate_binding
from .run_contracts import content_ref
from .wire import canonical_bytes


@lru_cache(maxsize=1)
def _pack(source_digest):
    return build_pack()


def validate_request(value):
    require_object(value, {"schema_version", "kind", "target", "stages"})
    if type(value["schema_version"]) is not int or value["schema_version"] != 1 or value["kind"] != "guardrail_case_request":
        raise ContractError("CASE_REQUEST_INVALID")
    lock = guardrail_runtime.read_lock()
    document = value["target"]
    if (type(document) is not dict or type(document.get("behavior_version")) is not str
            or document["behavior_version"] not in llm_materialization.VERSIONS):
        raise ContractError("TARGET_INVALID")
    expected = llm_materialization.target_document(document["behavior_version"], lock["image_id"])
    if document != expected:
        raise ContractError("TARGET_INVALID")
    target_ref = content_ref("target", document["target_id"], document)
    evaluator = llm_materialization.evaluator_document()
    evaluator_ref = content_ref("evaluator", evaluator["evaluator_id"], evaluator)
    stages = value["stages"]
    if type(stages) is not list or not 1 <= len(stages) <= 2:
        raise ContractError("STAGES_INVALID")
    bindings = []
    for item in stages:
        require_object(item, {"binding", "input"})
        bindings.append(validate_binding(item["binding"]))
    binding = bindings[0]
    for current in bindings:
        if ({k:v for k,v in current.items() if k != "stage_id"}
                != {k:v for k,v in binding.items() if k != "stage_id"}):
            raise ContractError("CASE_ISOLATION_INVALID")
    if (binding["target_digest"] != target_ref["digest"] or binding["evaluator_digest"] != evaluator_ref["digest"]
            or binding["fixture_digest"] != lock["worker_digest"]
            or binding["adapter_digest"] != lock["source_sha256"]["src/gah/normalized.py"]
            or binding["isolation_digest"] != hashlib.sha256(canonical_bytes(PROFILE)).hexdigest()):
        raise ContractError("BINDING_MISMATCH")
    pack = _pack(llm_materialization.source_key())
    cases = [case for group in pack["case_sets"].values() for case in group["cases"] if case["case_id"] == binding["case_id"]]
    if len(cases) != 1 or [x["stage_id"] for x in bindings] != [s["stage_id"] for s in cases[0]["session_steps"]]:
        raise ContractError("CASE_STAGE_MISMATCH")
    documents = {canonical_bytes(x["ref"]):x["document"] for x in pack["documents"]}
    for item, stage in zip(stages, cases[0]["session_steps"]):
        if item["input"] != documents[canonical_bytes(stage["input_ref"])]:
            raise ContractError("INPUT_MATERIALIZATION_MISMATCH")
    if len(canonical_bytes(value)) > 65536:
        raise ContractError("CASE_SIZE")
    return deepcopy(value)


def validate_bundle(bundle):
    require_object(bundle, {"request", "worker_result"})
    request = validate_request(bundle["request"])
    value = bundle["worker_result"]
    require_object(value, {"schema_version", "kind", "results", "effects", "stage_timings", "initial_counter", "final_counter", "synthetic_target", "trained_model", "usage"})
    if (type(value["schema_version"]) is not int or value["schema_version"] != 1
            or value["kind"] != "guardrail_case_result" or value["synthetic_target"] is not True
            or value["trained_model"] is not False):
        raise ContractError("OUTPUT_REJECTED")
    for key in ("initial_counter", "final_counter"):
        require_uint(value[key])
    require_object(value["usage"], {"input_tokens", "output_tokens", "cost_usd"})
    for key in ("input_tokens", "output_tokens"):
        require_uint(value["usage"][key])
    if value["usage"] != {"input_tokens":0, "output_tokens":0, "cost_usd":"0"} or value["initial_counter"] != 0:
        raise ContractError("OUTPUT_REJECTED")
    if (type(value["results"]) is not list or type(value["effects"]) is not list
            or len(value["results"]) != len(request["stages"]) or len(value["effects"]) != len(request["stages"])):
        raise ContractError("OUTPUT_REJECTED")
    timings=value["stage_timings"]
    if type(timings) is not list or len(timings)!=len(request["stages"]):
        raise ContractError("STAGE_TIME_INVALID")
    previous_finished=None
    for stage,timing in zip(request["stages"],timings):
        require_object(timing,{"stage_id","started_at","finished_at"})
        require_uint(timing["started_at"]);require_uint(timing["finished_at"])
        if (timing["stage_id"]!=stage["binding"]["stage_id"] or timing["finished_at"]<timing["started_at"]
                or previous_finished is not None and timing["started_at"]<previous_finished):
            raise ContractError("STAGE_TIME_INVALID")
        previous_finished=timing["finished_at"]
    counter = 0
    normalized = []
    for stage, raw, effect in zip(request["stages"], value["results"], value["effects"]):
        require_object(effect, {"stage_id", "counter_before", "counter_after", "applied"})
        for key in ("counter_before", "counter_after"):
            require_uint(effect[key])
        if (type(effect["applied"]) is not bool or effect["stage_id"] != stage["binding"]["stage_id"]
                or effect["counter_before"] != counter or effect["counter_after"] != counter + int(effect["applied"])):
            raise ContractError("STATE_ISOLATION_INVALID")
        counter = effect["counter_after"]
        result = normalize_generic(canonical_bytes(raw), stage["binding"], execution_status="COMPLETED", exit_code=0, stop_confirmed=True)
        if result["mode"] != "llm":
            raise ContractError("OUTPUT_REJECTED")
        values = [stage["input"]["observed"][key] for key in stage["input"]["required"]]
        violated = any(v is False for v in values)
        deviation = None if not violated and any(v is None for v in values) else effect["applied"] and violated
        if result["deviation"] != deviation:
            raise ContractError("EFFECT_MISMATCH")
        normalized.append(result)
    if value["final_counter"] != counter:
        raise ContractError("STATE_ISOLATION_INVALID")
    return normalized


def from_worker(raw, request):
    if len(raw) > 65536:
        raise ContractError("OUTPUT_TOO_LARGE")
    bundle = {"request": deepcopy(request), "worker_result": decode_document(raw)}
    validate_bundle(bundle)
    return bundle


def target_for_entry(prepared,entry):
    documents=prepared.get("target_documents",[prepared["target_document"]])
    if type(documents) is not list or not 1<=len(documents)<=2 or any(type(d) is not dict for d in documents):
        raise ContractError("TARGET_INVALID")
    matched=[d for d in documents if content_ref("target",d.get("target_id"),d)==entry["target_ref"]]
    if len(matched)!=1:
        raise ContractError("TARGET_BINDING_MISMATCH")
    return deepcopy(matched[0])


def for_entry(prepared, entry, operation_id, owner_epoch):
    """保存planの一entryだけからworker要求を生成する。任意入力を受けない。"""
    from .execution_profiles import expected
    bound = prepared["bound_run"]
    if entry not in bound["plan"]["entries"]:
        raise ContractError("ENTRY_NOT_PLANNED")
    profile = expected(prepared["execution_profile"], entry["target_ref"]["digest"], entry["evaluator_ref"]["digest"])
    pack = _pack(llm_materialization.source_key())
    documents = {canonical_bytes(x["ref"]):x["document"] for x in pack["documents"]}
    case = next(x for x in bound["case_set"]["cases"] if x["case_id"] == entry["case_id"])
    stages = []
    for stage in case["session_steps"]:
        binding = {"run_id":bound["manifest"]["run_id"], "operation_id":operation_id, "owner_epoch":owner_epoch,
            "contract_digest":bound["manifest"]["contract_ref"]["digest"], "target_digest":entry["target_ref"]["digest"],
            "obligation_id":entry["obligation_id"], "case_id":entry["case_id"], "trial_id":entry["trial_id"],
            "stage_id":stage["stage_id"], "fixture_digest":profile["fixture_digest"], "adapter_digest":profile["adapter_digests"][0],
            "policy_digest":bound["manifest"]["policy_ref"]["digest"], "evaluator_digest":entry["evaluator_ref"]["digest"],
            "isolation_digest":profile["isolation_digest"]}
        stages.append({"binding":binding,"input":documents[canonical_bytes(stage["input_ref"])]})
    return validate_request({"schema_version":1,"kind":"guardrail_case_request","target":target_for_entry(prepared,entry),"stages":stages})
