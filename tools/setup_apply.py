"""固定sampleの導入計画をauthority採択と監督へ接続する。"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sys
import time
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gah import operations as ops
from gah.contracts import ContractError, require_id, require_ref, require_uint
from gah.bounded_files import BoundedFileError, write_bounded
from gah.docker_runner import DockerRunner, operation_lock
from gah.policy import initial_policy_profile
from tools.setup_capacity import capacity_profile
from gah.productization import (REASONS, operation_result, read_document, validate_plan, workspace_path, write_document)
from gah.productization_journal import OperationJournal
from gah.run_contracts import content_ref, validate_run_manifest, validate_trial_plan
from gah.supervisor_checkpoint import Checkpoint
from gah.wire import canonical_bytes
from tools.authority_runtime import AuthorityRuntime
from tools.setup_baseline import execute_initial_baseline, execute_contract_candidate
from gah.supervised_run import SupervisorError

COMMAND = "ops.setup.apply"


class SetupError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _require(condition, reason="BINDING_MISMATCH"):
    if not condition: raise SetupError(reason)


def _stamp(clock):
    value = clock()
    _require(type(value) in (int, float) and 0 <= value < float("inf"), "CLOCK_UNAVAILABLE")
    stamp = int(value); require_uint(stamp)
    return stamp


class Steps:
    """固定roleの変更要求を同じrequestで回収する。拒否・輸送不明は成功保存しない。"""
    def __init__(self, runtime, checkpoint, tag):
        self.runtime, self.checkpoint, self.tag = runtime, checkpoint, tag

    def call(self, uid, action, key, *, fresh=False, **fields):
        request = {"schema_version": 1, "action": action, "request_id": self.tag + "-" + key, **fields}
        if not fresh:
            self.checkpoint.put("request-" + key, {"uid": uid, "request": request})
            saved = self.checkpoint.get("response-" + key)
            if saved is not None: return saved
        value = self.runtime.client(uid, request)
        if type(value) is dict and value.get("kind") == "authority_error":
            raise SetupError(value.get("reason") if type(value.get("reason")) is str else "AUTHORITY_REQUIRED")
        kind = "policy_adoption_result" if action in {"propose", "validate", "adopt", "current"} else "evaluation_authority_result"
        if action in {"baseline_propose", "baseline_validate", "baseline_adopt", "baseline_current"}:
            kind = "baseline_authority_result"
        _require(type(value) is dict and type(value.get("schema_version")) is int and value["schema_version"] == 1
                 and value.get("kind") == kind and value.get("action") == action
                 and value.get("request_id") == request["request_id"] and value.get("ci_eligible") is False)
        if not fresh: self.checkpoint.put("response-" + key, value)
        return value


def _validated(value):
    _require(value.get("passed") is True, "EVIDENCE_UNAVAILABLE")


def _adopt(steps, prefix, proposal_id, series_id, document, *, generation=0):
    is_policy = prefix == "policy"
    stem = "" if is_policy else "contract_"
    field = "policy" if is_policy else "contract"
    steps.call(12001, stem + "propose", prefix + "-propose", proposal_id=proposal_id,
               series_id=series_id, expected_generation=generation, **{field: document})
    validation = proposal_id + "-validation"
    checked = steps.call(12003, stem + "validate", prefix + "-validate", proposal_id=proposal_id, validation_id=validation)
    _validated(checked)
    adopted = steps.call(12001, stem + "adopt", prefix + "-adopt", proposal_id=proposal_id,
                         validation_id=validation, expected_generation=generation)
    _require(type(adopted.get("generation")) is int and adopted["generation"] == generation + 1)
    return adopted


def _runner(profile, folder):
    if profile == "sample-llm":
        from gah.guardrail_runner import GuardrailRunner
        return GuardrailRunner(folder / "execution.sqlite")
    return DockerRunner(ROOT / "config/fixture-runtime.lock.json", folder / "execution.sqlite")


def execute_contract_setup(runtime, folder, payload, *, clock, runner_factory=_runner):
    """新規runtimeで固定policy/初回baseline/比較gen2を採択。CLI依存を持たない内部接続。"""
    tag = "setup-" + hashlib.sha256(payload["setup_id"].encode()).hexdigest()[:24]
    checkpoint = Checkpoint(folder / "steps")
    steps = Steps(runtime, checkpoint, tag)
    policy = initial_policy_profile()
    series = tag + "-contracts"; baseline_series = tag + "-baselines"
    _adopt(steps, "policy", tag + "-policy", policy["policy_id"], policy)
    profile = payload["profile"]
    extra = {"target_version": "baseline-v1"} if profile == "sample-llm" else {}
    prepared_response = steps.call(12001, "guardrail_prepare" if extra else "fixture_prepare", "prepare-initial",
                                   run_id=payload["setup_id"], policy_series_id=policy["policy_id"], **extra)
    prepared = prepared_response["prepared"]
    bound = prepared["bound_run"]; contract = bound["contract"]
    _require(bound["manifest"]["run_id"] == payload["setup_id"])
    _require(content_ref("evaluation_contract", contract["contract_id"], contract) == payload["sample_contract_ref"])
    _require(content_ref("case_set", bound["case_set"]["case_set_id"], bound["case_set"]) == payload["sample_case_set_ref"])
    _require(contract["evaluator_refs"] == [payload["evaluator_ref"]])
    _adopt(steps, "contract", tag + "-contract", series, contract)
    initial_folder = folder / "initial"; initial_folder.mkdir(exist_ok=True)
    initial = execute_initial_baseline(runtime, runner_factory(profile, initial_folder), initial_folder, prepared,
        request_prefix=tag, contract_series_id=series, clock=clock)
    proposal = tag + "-baseline"; validation = proposal + "-validation"
    steps.call(12001, "baseline_propose", "baseline-propose", proposal_id=proposal,
        series_id=baseline_series, run_id=payload["setup_id"], expected_generation=0)
    _validated(steps.call(12003, "baseline_validate", "baseline-validate", proposal_id=proposal, validation_id=validation))
    adopted_baseline = steps.call(12001, "baseline_adopt", "baseline-adopt", proposal_id=proposal,
        validation_id=validation, expected_generation=0)
    _require(adopted_baseline.get("adoption_verified") is True and adopted_baseline.get("generation") == 1)
    # baseline_currentは常に現在照会。元runとの完全refを下記factoryでも再検査する。
    baseline = steps.call(12004, "baseline_current", "baseline-current", fresh=True, series_id=baseline_series)
    _require(baseline.get("valid") is True and baseline.get("generation") == 1, "EVIDENCE_UNAVAILABLE")
    record = baseline["baseline"]; baseline_ref = content_ref("baseline", record["baseline_id"], record)
    following = deepcopy(contract)
    following.update(contract_id=tag + "-comparison", generation=2,
                     comparison={"mode": "required", "baseline_ref": baseline_ref, "reason": None, "changed_axes": []})
    next_proposal = tag + "-next-proposal"; candidate_id = tag + "-candidate"
    steps.call(12001, "contract_propose", "next-propose", proposal_id=next_proposal,
               series_id=series, expected_generation=1, contract=following)
    preconditions = {"proposal_id": next_proposal, "baseline_series_id": baseline_series,
        "expected_contract_ref": payload["sample_contract_ref"], "expected_baseline_ref": baseline_ref}
    candidate = steps.call(12003, "contract_candidate_prepare", "candidate-prepare", **preconditions,
        candidate_id=candidate_id, old_run_id=tag + "-old", new_run_id=tag + "-new")
    _require(candidate.get("candidate_id") == candidate_id and candidate.get("adoption_verified") is False)
    require_ref(candidate["candidate_ref"])
    candidates = {}
    for side in ("old", "new"):
        # 保存responseは採択後の再開でも使う。新規要求ではfresh readで許可と全根拠を検査する。
        value = steps.call(12004, "contract_candidate_read", "candidate-read-" + side, candidate_id=candidate_id, side=side)
        _require(value.get("candidate_id") == candidate_id and value.get("side") == side
                 and value.get("candidate_ref") == candidate["candidate_ref"] and value.get("adoption_verified") is False)
        candidates[side] = value["prepared"]
    created = candidates["old"]["bound_run"]["manifest"]["created_at"]
    if profile == "sample-llm":
        from gah.llm_transitions import build
        expected = build(contract, following, baseline_record=record, source_prepared=prepared,
                         now=created, old_run_id=tag + "-old", new_run_id=tag + "-new")
    else:
        from gah.fixture_admission import execution_context
        from gah.transition_materialization import build_transition_runs
        worker, lock, execution_profile = execution_context()
        expected = build_transition_runs(contract, following, baseline_record=record, source_prepared=prepared,
            worker_source=worker, runtime_lock=lock, execution_profile=execution_profile,
            now=created, old_run_id=tag + "-old", new_run_id=tag + "-new")
    from gah.candidate_sections import reference
    candidate_body = {**preconditions, "candidate_id": candidate_id,
        "proposal_digest": content_ref("evaluation_contract", following["contract_id"], following)["digest"], "runs": expected}
    _require(reference(candidate_body) == candidate["candidate_ref"])
    finals = {}
    for side in ("old", "new"):
        _require(candidates[side] == expected[side])
        run_folder = folder / ("candidate-" + side); run_folder.mkdir(exist_ok=True)
        finals[side] = execute_contract_candidate(runtime, runner_factory(profile, run_folder), run_folder, candidates[side],
            request_prefix=tag, contract_series_id=series, candidate_id=candidate_id, side=side, clock=clock)
    validation_id = tag + "-candidate-validation"
    _validated(steps.call(12003, "contract_candidate_validate", "candidate-validate",
        candidate_id=candidate_id, validation_id=validation_id))
    adopted = steps.call(12001, "contract_candidate_adopt", "candidate-adopt", candidate_id=candidate_id,
        validation_id=validation_id, expected_contract_generation=1, expected_baseline_generation=1)
    _require(adopted.get("adoption_verified") is True and type(adopted.get("generation")) is int and adopted["generation"] == 2)
    current = steps.call(12004, "contract_current", "contract-current", fresh=True, series_id=series)
    _require(current.get("valid") is True and current.get("contract") == following and current.get("generation") == 2,
             "EVIDENCE_UNAVAILABLE")
    ref = content_ref("evaluation_contract", following["contract_id"], following)
    run_id = tag + "-run"
    run_request = {"schema_version": 1, "run_id": run_id, "contract_series_id": series,
                   "expected_contract_ref": ref, "trigger": "manual"}
    # 通常Supervisorと同一主体・request ID・本文でprepareを共有する。
    # authorityの保存receiptを再配送し、後続CLI時刻でmanifestを作り直さない。
    prepare_id = "supervised-" + hashlib.sha256(canonical_bytes(run_request)).hexdigest()[:24] + "-prepare"
    normal = steps.call(12004, "run_prepare", "normal-prepare", run_id=run_id,
                        contract_series_id=series, expected_contract_ref=ref, request_id=prepare_id)
    manifest = validate_run_manifest(normal["bound_run"]["manifest"])
    plan = validate_trial_plan(normal["bound_run"]["plan"])
    _require(manifest["run_id"] == run_id and manifest["contract_ref"] == ref and manifest["purpose"] == "regression"
             and manifest["baseline_ref"] == baseline_ref and manifest["plan_ref"] == content_ref("trial_plan", plan["plan_id"], plan))
    ci_request = {"schema_version": 1, "action": "ci_check", "request_id": tag + "-ci", "run_id": run_id,
        "expected_manifest_ref": content_ref("run_manifest", run_id, manifest), "expected_contract_ref": ref,
        "expected_baseline_ref": baseline_ref, "expected_target_refs": manifest["target_refs"], "expected_use_cases": manifest["use_cases"]}
    return {"run_request": run_request, "ci_request": ci_request, "contract_series_id": series,
            "baseline_series_id": baseline_series,
            "initial_receipt": initial, "candidate_receipts": finals, "baseline_ref": baseline_ref,
            "contract_ref": ref, "manifest_ref": ci_request["expected_manifest_ref"]}


def _check_plan(workspace, path, now):
    base = ops._workspace(workspace)
    _require(base.is_relative_to(ROOT), "PATH_REJECTED")
    plan = validate_plan(read_document(base, path), kind=ops.SETUP_PLAN_KIND,
                         payload_validator=ops.validate_setup_payload, now=now)
    payload = plan["payload"]
    _require(payload["workspace_path"] == str(base) and payload["platform"] == ops._platform_kind(), "CONDITION_MISMATCH")
    _require(plan["source_ref"] == ops._setup_source_ref() and plan["requirements_ref"] == ops._setup_requirements_ref(), "STALE_OR_INVALIDATED")
    for ref in (plan["source_ref"], plan["requirements_ref"]): ops.resolve_setup_ref(base, ref)
    values = {name: ops.resolve_setup_ref(base, payload[name]) for name in
              ("config_ref", "role_recipe_ref", "resource_profile_ref", "sample_contract_ref", "sample_case_set_ref", "evaluator_ref")}
    _require(values["config_ref"] == {"schema_version": 1, "kind": "setup_config", "profile": payload["profile"],
                                      "config": initial_policy_profile()})
    _require(values["role_recipe_ref"] == {"schema_version": 1, "kind": "role_recipe", "broker_uid": 12000,
        "actors": [{"role": "manager", "uid": 12001}, {"role": "validator", "uid": 12003}, {"role": "operator", "uid": 12004}]})
    config = initial_policy_profile(); management = config["management_profile"]
    _require(values["resource_profile_ref"] == {"schema_version": 1, "kind": "resource_profile", "profile": management,
        "limits": config["profiles"][management], "bundle_max_bytes": ops.BUNDLE_MAX_BYTES,
        "bundle_max_entries": ops.BUNDLE_MAX_ENTRIES, "cache_max_bytes": ops.CACHE_MAX_BYTES,
        "cache_max_entries": ops.CACHE_MAX_ENTRIES})
    sample_refs = ops._sample_refs(payload["profile"], payload["setup_id"], plan["created_at"])
    _require(sample_refs == tuple(payload[name] for name in ("sample_contract_ref", "sample_case_set_ref", "evaluator_ref")))
    _require(payload["existing_runtime_ref"] is None, "UNSUPPORTED_CAPABILITY")
    locks = ["authority", "fixture"] + (["guardrail"] if payload["profile"] == "sample-llm" else [])
    _require(len(payload["image_lock_refs"]) == len(locks))
    for name, ref in zip(locks, payload["image_lock_refs"]):
        lock = read_document(ROOT, "config/" + name + "-runtime.lock.json")
        _require(ops.resolve_setup_ref(base, ref) == lock, "STALE_OR_INVALIDATED")
    for path in payload["output_paths"].values(): workspace_path(base, path)
    _require(len(set(payload["output_paths"].values())) == 3)
    return base, plan


def _write_request(workspace, path, value):
    target = workspace_path(workspace, path)
    raw = canonical_bytes(value)
    try:
        write_bounded(target, raw, immutable=True)
    except BoundedFileError as error:
        reason = ("RESULT_CONFLICT" if error.code == "RESULT_CONFLICT" else
                  "CAPACITY_EXCEEDED" if error.code == "CAPACITY_EXCEEDED" else
                  "PATH_REJECTED" if error.code == "PATH_REJECTED" else "IO_ERROR")
        _require(False, reason)


def apply(workspace, plan_path, *, request_id=None, clock=time.time,
          runtime_factory=AuthorityRuntime, runner_factory=_runner, doctor=ops.run_doctor):
    # 公開APIのtime.time値を、journal/doctorの厳密なUTC整数秒へ一度正規化。
    source_clock = clock
    clock = lambda: _stamp(source_clock)
    rid = request_id
    try:
        if rid is not None: require_id(rid)
        base, plan = _check_plan(workspace, plan_path, _stamp(clock))
        if rid is None: rid = plan["id"]
        require_id(rid)
        principal = ops._trusted_principal(); require_id(principal)
        payload = plan["payload"]; paths = payload["output_paths"]
        plan_ref = content_ref(ops.SETUP_PLAN_KIND, plan["id"], plan)
        digest = hashlib.sha256(canonical_bytes({"plan_ref": plan_ref, "plan_path": str(workspace_path(base, plan_path)),
                                               "principal": principal})).hexdigest()
        root = workspace_path(base, ".ga/operations/setup"); root.mkdir(parents=True, exist_ok=True)
        # 同一setupへの別requestの並行適用も、このlockで直列化する。
        with operation_lock(root / "setup-lock", payload["setup_id"], "apply"):
            with OperationJournal(root / "journal.sqlite", clock=lambda: _stamp(clock)) as journal:
                intent = journal.begin(principal, COMMAND, rid, digest)
                if intent["result"] is not None: return intent["result"]
                stage = workspace_path(base, root / payload["setup_id"])
                cp = Checkpoint(stage / "metadata")
                identity = {"principal": principal, "request_id": rid, "plan_ref": plan_ref}
                previous_identity = cp.get("identity")
                _require(previous_identity is None or previous_identity == identity, "IDEMPOTENCY_CONFLICT")
                cp.put("identity", identity)
                folder = workspace_path(base, paths["runtime"])
                if intent["created"]:
                    _require(not any(Path(p).exists() for p in paths.values()), "RESULT_CONFLICT")
                    cp.put("outputs-initially-absent", {"paths": paths})
                else:
                    _require(cp.get("outputs-initially-absent") == {"paths": paths}, "OPERATION_UNKNOWN")
                capacity = capacity_profile(payload["profile"], workspace=base)
                boot, _ = doctor(base, "bootstrap", request_id=rid + "-bootstrap", clock=clock, capacity_profile=capacity)
                _require(boot["operation_status"] == "COMPLETED", boot["reasons"][0] if boot["reasons"] else "OBSERVATION_MISSING")
                folder.mkdir(parents=True, exist_ok=True)
                with operation_lock(folder / "supervised-transport", "deployment", "supervisor"):
                    runtime = runtime_factory(folder, reuse_clients=True, keep_clients_running=True)
                    try:
                        cp.put("runtime", {"prefix": runtime.prefix, "image_id": runtime.lock["image_id"]})
                        # 固定lockのimageだけを確認。任意tag取得やlock書換えをapplyに混ぜない。
                        for ref in payload["image_lock_refs"]:
                            lock = ops.resolve_setup_ref(base, ref)
                            images = json.loads(runtime.command(["image", "inspect", lock["image_id"]]))
                            _require(type(images) is list and len(images) == 1 and images[0]["Id"] == lock["image_id"], "IMAGE_UNAVAILABLE")
                        broker = runtime.prefix + "-broker"
                        if broker in runtime.state["containers"]:
                            runtime._verify_config(runtime.inspect(broker), 12000, "broker")
                            runtime._wait_ready()
                        else:
                            _require(not runtime.state["containers"], "OPERATION_UNKNOWN")
                            runtime.prepare()
                        result = execute_contract_setup(runtime, stage, payload, clock=runtime.clock, runner_factory=runner_factory)
                        metadata = {"schema_version": 2, "kind": "runtime_metadata",
                                    "contract_series_id": result["contract_series_id"],
                                    "baseline_series_id": result["baseline_series_id"], "profile": payload["profile"]}
                        _write_request(base, folder / "runtime-metadata.json", metadata)
                        ready, _ = doctor(base, "ready", runtime=folder, authority=runtime,
                            contract_series_id=result["contract_series_id"], baseline_series_id=result["baseline_series_id"],
                            request_id=rid + "-ready", clock=clock, capacity_profile=capacity)
                        _require(ready["operation_status"] == "COMPLETED", ready["reasons"][0] if ready["reasons"] else "OBSERVATION_MISSING")
                        # 途中のsource/plan変更、失効・時計逆行を出力直前にも拒否する。
                        _check_plan(base, plan_path, _stamp(clock))
                    finally:
                        runtime.close_clients()
                    for name in ("run_request", "ci_request"):
                        _write_request(base, paths[name], result[name])
                    details = {"schema_version": 1, "kind": "setup_result", "id": payload["setup_id"],
                        "plan_ref": plan_ref, "source_ref": plan["source_ref"], "profile": payload["profile"],
                        "runtime_ref": content_ref("runtime_deployment", runtime.prefix, {"prefix": runtime.prefix, "image_id": runtime.lock["image_id"]}),
                        "contract_ref": result["contract_ref"], "baseline_ref": result["baseline_ref"],
                        "initial_run_ref": result["initial_receipt"]["manifest_ref"],
                        "candidate_run_refs": [result["candidate_receipts"][side]["manifest_ref"] for side in ("old", "new")],
                        "run_request_ref": content_ref("run_request", result["run_request"]["run_id"], result["run_request"]),
                        "ci_request_ref": content_ref("ci_request", result["ci_request"]["request_id"], result["ci_request"]),
                        "output_paths": paths, "ready_ref": ready["result_ref"],
                        "current_ci_checked": False, "ci_eligible": False}
                    ref = write_document(base, stage / "result.json", details)
                    operation = operation_result(COMMAND, rid, "COMPLETED", result_ref=ref, checked_at=_stamp(clock))
                    return journal.finish(principal, COMMAND, rid, digest, operation)
    except KeyboardInterrupt:
        # 中断だけでは全外部操作の停止を証明できない。取消し確定へ昇格しない。
        return operation_result(COMMAND, rid, "INCOMPLETE", reasons=["OPERATION_CANCELLED", "OPERATION_UNKNOWN"])
    except Exception as error:
        try: require_id(rid)
        except ContractError: rid = None
        reason = getattr(error, "code", None)
        if isinstance(error, SupervisorError):
            reason = {"OPERATION_TIME_MISMATCH": "CLOCK_UNAVAILABLE", "CLOCK_FAILURE": "CLOCK_UNAVAILABLE"}.get(str(error), reason)
        if reason == "INVALID_CONTRACT": reason = "INVALID_INPUT"
        if reason not in REASONS: reason = "OPERATION_UNKNOWN"
        status = "REJECTED" if reason in {"INVALID_INPUT", "PATH_REJECTED", "PLAN_EXPIRED", "RESULT_CONFLICT", "IDEMPOTENCY_CONFLICT", "STALE_OR_INVALIDATED", "CONDITION_MISMATCH"} else "INCOMPLETE"
        return operation_result(COMMAND, rid, status, reasons=[reason])
