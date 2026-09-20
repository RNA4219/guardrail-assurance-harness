"""初回sample baselineを既存監督・runner・Evidenceへ接続する固定操作。"""
from copy import deepcopy
import hashlib
from pathlib import Path
import sys
import time
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gah.contracts import require_id
from gah.docker_runner import operation_lock
from gah.supervised_run import Supervisor, SupervisorError, OPERATOR, VALIDATOR, ENTRY_KEYS
from gah.llm_supervised_run import LlmSupervisor
from gah.supervisor_checkpoint import Checkpoint
from gah.run_contracts import content_ref, validate_run_manifest, validate_trial_plan
from gah.wire import canonical_bytes
from gah import fixture_materialization, guardrail_results, llm_admission


class _InitialMixin:
    def __init__(self, runtime, runner, checkpoint, prepared, *, request_prefix, contract_series_id, clock=time.time, hook=None):
        require_id(request_prefix); require_id(contract_series_id)
        self.prepared = deepcopy(prepared)
        bound = self.prepared["bound_run"]
        manifest = validate_run_manifest(bound["manifest"])
        request = {"schema_version": 1, "run_id": manifest["run_id"], "contract_series_id": contract_series_id,
                   "expected_contract_ref": manifest["contract_ref"], "trigger": "manual"}
        super().__init__(runtime, runner, checkpoint, request, clock=clock, hook=hook)
        self.tag = "initial-" + hashlib.sha256(canonical_bytes([request_prefix, request])).hexdigest()[:24]
        checkpoint.put("initial-identity", {"request_prefix": request_prefix,
            "prepared_digest": hashlib.sha256(canonical_bytes(self.prepared)).hexdigest(),
            "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in
                ("tools/setup_baseline.py", "src/gah/supervised_run.py", "src/gah/llm_supervised_run.py",
                 "src/gah/fixture_materialization.py", "src/gah/llm_admission.py", "src/gah/guardrail_results.py")}})

    def call(self, uid, action, key, *, durable=False, **fields):
        request = {"schema_version": 1, "action": action, "request_id": self.tag + "-" + key, **fields}
        if durable:
            self.checkpoint.put("request-" + key, {"uid": uid, "request": request})
            self.hook("request-" + key)
        value = self.runtime.client(uid, request)
        kind = {"evidence_open": "run_evidence_run", "evidence_record": "attempt_receipt",
                "evidence_finalize": "authority_run_receipt"}.get(action, "evaluation_authority_result")
        if type(value) is dict and value.get("kind") == "authority_error":
            reason = value.get("reason")
            raise SupervisorError(reason if type(reason) is str else "AUTHORITY_UNAVAILABLE")
        if (type(value) is not dict or type(value.get("schema_version")) is not int or value["schema_version"] != 1
                or value.get("kind") != kind or value.get("action") != action
                or value.get("request_id") != request["request_id"] or value.get("ci_eligible") is not False):
            raise SupervisorError("AUTHORITY_RESPONSE_INVALID")
        if durable:
            self.checkpoint.put("response-" + key, value)
            self.hook("response-" + key)
        return value

    def prepare(self):
        bound = self.prepared["bound_run"]
        manifest = validate_run_manifest(bound["manifest"])
        plan = validate_trial_plan(bound["plan"])
        if (manifest["purpose"] != "baseline_candidate" or manifest["baseline_ref"] is not None
                or manifest["profile"] != "full" or bound["contract"]["generation"] != 1
                or manifest["plan_ref"] != content_ref("trial_plan", plan["plan_id"], plan)
                or manifest["contract_ref"] != self.request["expected_contract_ref"]):
            raise SupervisorError("INITIAL_BASELINE_REQUIRED")
        entries = plan["entries"]
        if isinstance(self, _InitialGuardrail):
            if manifest["use_cases"] != ["UC-LLM"] or len(entries) != 400:
                raise SupervisorError("INITIAL_SAMPLE_INCOMPLETE")
            target_version = self.prepared["target_document"]["behavior_version"]
            if target_version != "baseline-v1":
                raise SupervisorError("INITIAL_SAMPLE_MISMATCH")
            expected = llm_admission.expected(bound["policy"], bound["contract"]["policy_generation"],
                self.run_id, manifest["created_at"], target_version)["prepared"]
            if expected != self.prepared:
                raise SupervisorError("INITIAL_SAMPLE_MISMATCH")
            records = [{"scenario": "guardrail:baseline-v1"} for _ in entries]
        else:
            if manifest["use_cases"] != ["UC-CI"] or len(entries) != 15:
                raise SupervisorError("INITIAL_SAMPLE_INCOMPLETE")
            pack = self.prepared["pack"]
            expected = fixture_materialization.build_fixture_pack(bound["policy"],
                (ROOT / "fixtures/runtime/fixture_worker.py").read_bytes(), self.runner.lock,
                pack["execution_profile"], manifest["created_at"], self.run_id,
                policy_generation=bound["contract"]["policy_generation"])
            if expected != self.prepared:
                raise SupervisorError("INITIAL_SAMPLE_MISMATCH")
            records = []
            for entry in entries:
                matches = [m for m in pack["materials"] if all(m[k] == entry[k] for k in ENTRY_KEYS if k != "variant")]
                if len(matches) != 1 or len(entry["stage_ids"]) != 1:
                    raise SupervisorError("INITIAL_SAMPLE_MISMATCH")
                records.append(matches[0])
        self.bound, self.manifest = bound, manifest
        self.manifest_ref = content_ref("run_manifest", self.run_id, manifest)
        self.scope = None
        self.entries = [(self.tag + "-op-" + str(index), entry, record)
                        for index, (entry, record) in enumerate(zip(entries, records))]
        return manifest

    def begin_initial(self):
        return self.call(OPERATOR, "run_begin", "begin", durable=True, manifest=self.manifest,
                         plan=self.bound["plan"], contract_series_id=self.request["contract_series_id"])

    def initial(self):
        self.prepare()
        self.begin_initial()
        snapshot = self.status()
        if snapshot["cancelled"] or snapshot["breached"]:
            raise SupervisorError("INITIAL_RUN_NOT_CONTINUABLE")
        # 確定済みfinalizeは元receiptを回収する。別runや再採択に変えない。
        if self.checkpoint.get("request-finalize") is not None:
            return self.finalize_initial()
        self.call(OPERATOR, "evidence_open", "open", durable=True, run_id=self.run_id)
        if not snapshot["closed"]:
            self.run_entries()
            self.claim()
            closed = self.call(OPERATOR, "resource_close", "close", durable=True, **self.owner)
            if closed.get("budget_closure") is not True or closed.get("closed") is not True:
                raise SupervisorError("BUDGET_OPEN")
        return self.finalize_initial()

    def finalize_initial(self):
        result = self.call(OPERATOR, "evidence_finalize", "finalize", durable=True, run_id=self.run_id)
        if (result.get("run_id") != self.run_id or result.get("manifest_ref") != self.manifest_ref
                or result.get("purpose") != self.manifest["purpose"]
                or result.get("resource_closure_verified") is not True or result.get("input_materialization_verified") is not True):
            raise SupervisorError("INITIAL_EVIDENCE_INCOMPLETE")
        return result

    def recover_started(self):
        # 起動済みの所有operationだけを停止回収する。既知の良好結果にも終了時刻を補わない。
        incomplete = False
        for op, entry, material in getattr(self, "entries", []):
            if self.checkpoint.get("end-" + op) is not None:
                continue
            try:
                record = self.journal_record(op)
                if record is None:
                    if self.checkpoint.get("start-" + op) is not None:
                        raise SupervisorError("INITIAL_RECOVERY_UNCONFIRMED")
                    continue
                receipt = self.runner.recover(self.run_id, op)
                self.checked_receipt(op, entry, material, receipt, record["binding"]["owner_epoch"])
                if not all(receipt.get(key) is True for key in ("stop_confirmed", "cleanup_confirmed", "isolation_config_verified")):
                    raise SupervisorError("INITIAL_RECOVERY_UNCONFIRMED")
                self.checkpoint.put("initial-recovery-" + op, {"receipt": receipt})
                self.call(VALIDATOR, "resource_observe", op + "-recovery-stop", durable=True,
                    run_id=self.run_id, operation_id=op, event_id=op + "-recovery-stop", stopped=True, usage=None)
            except Exception:
                incomplete = True
                self.checkpoint.put("initial-recovery-unknown-" + op, {"reason": "RECOVERY_UNCONFIRMED", "ci_eligible": False})
        if incomplete:
            raise SupervisorError("INITIAL_RECOVERY_UNCONFIRMED")


class _InitialFixture(_InitialMixin, Supervisor):
    pass


class _InitialGuardrail(_InitialMixin, LlmSupervisor):
    pass


def execute_initial_baseline(runtime, runner, folder, prepared, *, request_prefix, contract_series_id, clock=time.time, hook=None):
    """callerはdeployment transport lockを保持し、固定runnerだけを渡す。採択は上位で行う。"""
    folder = Path(folder)
    run_id = prepared["bound_run"]["manifest"]["run_id"]
    with operation_lock(folder / "initial-execution.sqlite", run_id, "initial-baseline"):
        checkpoint = Checkpoint(folder / "initial-checkpoints")
        kind = _InitialGuardrail if getattr(runner, "execution_kind", None) == "guardrail" else _InitialFixture
        supervisor = kind(runtime, runner, checkpoint, prepared, request_prefix=request_prefix,
                          contract_series_id=contract_series_id, clock=clock, hook=hook)
        try:
            return supervisor.initial()
        except BaseException:
            # 回収失敗を成功へ変換しない。元の失敗とcheckpoint、未精算状態を保持する。
            try:
                supervisor.recover_started()
            except Exception:
                # raw例外や秘密を出さず、回収未確定を別記録に残す。
                checkpoint.put("initial-recovery-incomplete", {"reason": "RECOVERY_UNCONFIRMED", "ci_eligible": False})
            raise


class _CandidateMixin(_InitialMixin):
    def __init__(self, *args, candidate_id, side, **kwargs):
        require_id(candidate_id)
        if side not in {"old", "new"}: raise SupervisorError("INVALID_CANDIDATE_SIDE")
        self.candidate_id, self.side = candidate_id, side
        super().__init__(*args, **kwargs)
        self.checkpoint.put("candidate-identity", {"candidate_id": candidate_id, "side": side})

    def prepare(self):
        # callerは固定factory全本文とcandidate完全refを照合済み。ここでも実行bindingを照合する。
        bound = self.prepared["bound_run"]
        manifest = validate_run_manifest(bound["manifest"])
        plan = validate_trial_plan(bound["plan"])
        is_llm = isinstance(self, _CandidateGuardrail)
        count = (400 if is_llm else 15) * (1 if self.side == "old" else 2)
        if (manifest["purpose"] != {"old": "contract_old_regression", "new": "contract_candidate"}[self.side]
                or bound["contract"]["generation"] != (1 if self.side == "old" else 2)
                or manifest["profile"] != "full" or manifest["run_id"] != self.run_id
                or manifest["use_cases"] != (["UC-LLM"] if is_llm else ["UC-CI"])
                or manifest["contract_ref"] != self.request["expected_contract_ref"]
                or manifest["plan_ref"] != content_ref("trial_plan", plan["plan_id"], plan)
                or len(plan["entries"]) != count):
            raise SupervisorError("CANDIDATE_BINDING_MISMATCH")
        entries = []
        if is_llm:
            from gah import execution_profiles
            execution_profiles.check_plan(self.prepared["execution_profile"], bound)
            for index, entry in enumerate(plan["entries"]):
                profile = execution_profiles.expected(self.prepared["execution_profile"], entry["target_ref"]["digest"], entry["evaluator_ref"]["digest"])
                target = guardrail_results.target_for_entry(self.prepared, entry)
                if (profile["fixture_digest"] != self.runner.lock["worker_digest"]
                        or profile["adapter_digests"] != [self.runner.adapter_digest]
                        or profile["isolation_digest"] != self.runner.isolation_digest
                        or target["runtime_image_id"] != self.runner.lock["image_id"]):
                    raise SupervisorError("CANDIDATE_BINDING_MISMATCH")
                entries.append((self.tag + "-op-" + str(index), entry, {"scenario": "guardrail:" + target["behavior_version"]}))
        else:
            material = self.prepared["materialization"]
            records = material["manifest"]["records"]
            if len(records) != count or material["manifest_ref"] != content_ref("fixture_manifest", material["manifest"]["materialization_id"], material["manifest"]):
                raise SupervisorError("CANDIDATE_BINDING_MISMATCH")
            for index, record in enumerate(records):
                matches = [e for e in plan["entries"] if all(e[k] == record[k] for k in ENTRY_KEYS)]
                if len(matches) != 1: raise SupervisorError("CANDIDATE_BINDING_MISMATCH")
                entry = matches[0]
                if (entry["evaluator_ref"]["digest"] != self.runner.lock["worker_digest"]
                        or entry["target_ref"]["digest"] != self.runner.target_digest(record["scenario"])
                        or len(entry["stage_ids"]) != 1):
                    raise SupervisorError("CANDIDATE_BINDING_MISMATCH")
                entries.append((self.tag + "-op-" + str(index), entry, record))
        if len({canonical_bytes({k: e[k] for k in ENTRY_KEYS}) for _, e, _ in entries}) != count:
            raise SupervisorError("CANDIDATE_BINDING_MISMATCH")
        self.bound, self.manifest, self.entries = bound, manifest, entries
        self.manifest_ref = content_ref("run_manifest", self.run_id, manifest)
        self.scope = None
        return manifest

    def begin_initial(self):
        value = self.call(OPERATOR, "contract_candidate_begin", "begin", durable=True,
                          candidate_id=self.candidate_id, side=self.side)
        if value.get("candidate_id") != self.candidate_id or value.get("side") != self.side or value.get("run_id") != self.run_id:
            raise SupervisorError("CANDIDATE_BINDING_MISMATCH")
        return value


class _CandidateFixture(_CandidateMixin, Supervisor):
    pass


class _CandidateGuardrail(_CandidateMixin, LlmSupervisor):
    pass


def execute_contract_candidate(runtime, runner, folder, prepared, *, request_prefix, contract_series_id, candidate_id, side, clock=time.time, hook=None):
    """固定factoryと照合済みの候補旧/新1sideを全件実行。採択は別操作。"""
    folder = Path(folder)
    run_id = prepared["bound_run"]["manifest"]["run_id"]
    with operation_lock(folder / "initial-execution.sqlite", run_id, "initial-baseline"):
        checkpoint = Checkpoint(folder / "initial-checkpoints")
        kind = _CandidateGuardrail if getattr(runner, "execution_kind", None) == "guardrail" else _CandidateFixture
        supervisor = kind(runtime, runner, checkpoint, prepared, request_prefix=request_prefix,
                          contract_series_id=contract_series_id, candidate_id=candidate_id, side=side, clock=clock, hook=hook)
        try:
            return supervisor.initial()
        except BaseException:
            try: supervisor.recover_started()
            except Exception:
                checkpoint.put("initial-recovery-incomplete", {"reason": "RECOVERY_UNCONFIRMED", "ci_eligible": False})
            raise
