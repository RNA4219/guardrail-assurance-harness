"""分割された固定query-scale prepared inputを全entry分だけ処理するtest helper。"""
from copy import deepcopy
import hashlib
from gah import partitioned_guardrail_results, guardrail_runtime
from gah.adoption import AdoptionError
from gah.docker_runner import PROFILE
from gah.execution_journal import ExecutionJournal
from gah.run_contracts import content_ref
from gah.wire import canonical_bytes
from tests import test_guardrail_runner as runner_tests


class PartitionedRuntime:
    prefix = 'gah-authority-' + 'a' * 32

    def __init__(self, store, clock):
        self.store = store
        self.clock = clock
        self.lock = {'image_id': 'sha256:' + 'b' * 64}
        self.calls = []

    def client(self, uid, request):
        self.calls.append((uid, deepcopy(request)))
        try:
            return self.store.dispatch(uid, uid, request)
        except AdoptionError as error:
            return {'kind': 'authority_error', 'reason': str(error), 'ci_eligible': False}


class SyntheticPartitionedRunner:
    execution_kind = 'guardrail'

    def __init__(self, folder, clock):
        self.lock = guardrail_runtime.read_lock()
        self.adapter_digest = self.lock['source_sha256']['src/gah/normalized.py']
        self.isolation_digest = hashlib.sha256(canonical_bytes(PROFILE)).hexdigest()
        self.journal_path = folder / 'execution.sqlite'
        self.clock = clock
        self.executed = []
        self.recovered = []

    def run_partitioned(self, request, *, case_count, run_deadline, timeout_seconds=120):
        request = partitioned_guardrail_results.validate_request(request, case_count=case_count)
        binding = request['stages'][0]['binding']
        self.executed.append(binding['operation_id'])
        scenario = 'guardrail:' + hashlib.sha256(canonical_bytes(request)).hexdigest()
        with ExecutionJournal(self.journal_path) as journal:
            row = journal.begin(binding, scenario, self.lock['image_id'],
                                run_deadline=run_deadline, timeout_seconds=timeout_seconds)
            if not row['new']:
                raise AssertionError('DUPLICATE_EXECUTION')
            args = (binding['run_id'], binding['operation_id'], row['owner_token'])
            container_id = hashlib.sha256(binding['operation_id'].encode()).hexdigest()
            journal.advance(*args, 'CREATED', container_id=container_id)
            journal.advance(*args, 'STOPPED', container_id=container_id)
            raw = runner_tests.worker.evaluate(
                request, clock=lambda: self.clock() * 1_000_000_000)
            bundle = {'request': request, 'worker_result': raw}
            partitioned_guardrail_results.validate_bundle(bundle, case_count=case_count)
            receipt = {
                'schema_version': 1, 'kind': 'guardrail_execution', 'binding': binding,
                'scenario': scenario, 'image_id': self.lock['image_id'],
                'container_id': container_id, 'execution_status': 'COMPLETED', 'exit_code': 0,
                'stop_confirmed': True, 'reason': None, 'elapsed_millis': 1,
                'isolation_config_verified': True, 'output_disposition': 'ADMITTED',
                'cleanup_confirmed': True, 'recovered': False, 'normalized_result': None,
                'probe_result': None, 'case_result': bundle, 'ci_eligible': False,
            }
            return journal.finish(*args, receipt)['receipt']

    def recover(self, run_id, operation_id):
        self.recovered.append(operation_id)
        with ExecutionJournal(self.journal_path) as journal:
            return journal.get(run_id, operation_id)['receipt']


def complete_all_partitioned_entries(
    store,
    full_prepared,
    *,
    case_count,
    owner_id,
    clock,
    request,
    request_prefix,
):
    """固定in-process workerを使い、全entryを開始・観測・evidence保存へ通す。

    full_preparedはpartitioned_llm_admission.for_run(...)[prepared]の値。
    PreparedCasesは一度だけ構築し、各entryはその同じ検証済みsnapshotから要求化する。
    """
    bound = full_prepared["bound_run"]
    manifest = bound["manifest"]
    run_id = manifest["run_id"]
    expected_manifest_ref = content_ref("run_manifest", run_id, manifest)
    prepared_cases = partitioned_guardrail_results.PreparedCases(full_prepared)
    targets_by_digest = {
        content_ref("target", document["target_id"], document)["digest"]: document
        for document in full_prepared["target_documents"].values()
    }
    entries = bound["plan"]["entries"]
    completed_entries = 0
    recorded_attempts = 0
    last_owner_epoch = None
    for index, entry in enumerate(entries):
        operation_id = f"{request_prefix}-operation-{index:04d}"
        target = targets_by_digest.get(entry["target_ref"]["digest"])
        if target is None:
            raise AssertionError("prepared entry references an unknown fixed target")
        started = store.dispatch(12004, 12004, request(
            "resource_start", operation_id + "-start",
            run_id=run_id, owner_id=owner_id, operation_id=operation_id,
            entry={key: entry[key] for key in ("obligation_id", "case_id", "trial_id", "variant")},
            scenario="guardrail:" + target["behavior_version"],
            expected_manifest_ref=expected_manifest_ref,
        ))
        last_owner_epoch = started["owner_epoch"]
        worker_request = prepared_cases.for_entry(entry, operation_id, last_owner_epoch)
        observed_at = clock()
        raw = runner_tests.worker.evaluate(
            worker_request, clock=lambda observed_at=observed_at: observed_at * 1_000_000_000
        )
        normalized = partitioned_guardrail_results.validate_bundle(
            {"request": worker_request, "worker_result": raw}, case_count=case_count
        )
        timings = raw["stage_timings"]
        if len(normalized) != len(timings):
            raise AssertionError("fixed worker result/timing count mismatch")
        attempts = []
        for stage_index, result in enumerate(normalized):
            timing = timings[stage_index]
            attempts.append({
                "schema_version": 1, "kind": "attempt_record",
                "attempt_id": operation_id + "-attempt-" + str(stage_index),
                "variant": entry["variant"], "retry_of": None,
                "started_at": timing["started_at"], "finished_at": timing["finished_at"],
                "stop_confirmed": True, "execution_status": "COMPLETED", "state_restored": True,
                "expected_binding": result["binding"], "result": result,
            })
        completion = store.dispatch(12003, 12003, request(
            "evidence_complete", operation_id + "-complete",
            run_id=run_id, operation_id=operation_id, event_id=operation_id + "-stop",
            usage=raw["usage"], attempts=attempts,
        ))
        if completion.get("accepted") is not True or len(completion.get("records", [])) != len(attempts):
            raise AssertionError("partitioned entry completion was not fully accepted")
        completed_entries += 1
        recorded_attempts += len(attempts)
    return {
        "planned_entries": len(entries),
        "completed_entries": completed_entries,
        "recorded_attempts": recorded_attempts,
        "owner_epoch": last_owner_epoch,
        "prepared_cases_constructed": 1,
    }

def resolve_prepared_root(store, root, *, run_id, expected_manifest_ref, request, request_prefix):
    """Resolve a compact prepared root only through the public immutable-artifact reader."""
    from gah import partitioned_llm_admission

    if root.get("kind") != "partitioned_prepared_run" or root.get("run_id") != run_id:
        raise AssertionError("unexpected prepared root")
    if root.get("binding", {}).get("manifest_ref") != expected_manifest_ref:
        raise AssertionError("prepared root manifest mismatch")
    counter = 0

    def fetch_ref(reference):
        nonlocal counter
        response = store.dispatch(12004, 12004, request(
            "run_input_artifact", f"{request_prefix}-{counter:04d}", run_id=run_id,
            expected_manifest_ref=expected_manifest_ref, artifact_ref=reference,
        ))
        counter += 1
        if response.get("artifact_ref") != reference or response.get("manifest_ref") != expected_manifest_ref:
            raise AssertionError("artifact reader returned a mismatched receipt")
        return response["document"]

    prepared = partitioned_llm_admission.resolve_prepared(root, fetch_ref)
    return prepared, {"resolved_refs": counter}
