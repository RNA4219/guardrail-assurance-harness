"""One-entry v2 diagnostic consumer tests; never starts Docker."""
from copy import deepcopy
from pathlib import Path
import hashlib
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gah import normalized
from gah.adoption import AdoptionError
from gah.assurance_authority import fixed_profile
from gah.docker_runner import DockerRunner, RunnerError, PROFILE, _digest_file
from gah.execution_journal import ExecutionJournal
from gah.supervisor_checkpoint import Checkpoint, CheckpointError
from gah.normalized import normalize_generic
from gah.partitioned_diagnostic_run import execute_one_entry, PartitionedDiagnosticError, SCENARIO
from gah.partitioned_run_authority import PartitionedRunEvaluationExtension
from gah.resource_authority import _lock
from gah.run_contracts import content_ref
from gah.wire import canonical_bytes
from tests.test_fixture_admission import request


class RuntimeAdapter:
    def __init__(self, store, clock):
        self.store = store
        self.clock = clock
        self.calls = []

    def client(self, uid, value):
        self.calls.append((uid, deepcopy(value)))
        return self.store.dispatch(uid, uid, deepcopy(value))


class PartitionedDiagnosticRunTests(unittest.TestCase):
    def setUp(self):
        from tests.test_partitioned_authority import PartitionedAuthorityTests
        self.helper = PartitionedAuthorityTests("test_real_adoption_upload_restart_replay_commit_and_fresh_read")
        self.helper.setUp()
        self.addCleanup(self.helper.doCleanups)
        self.store = self.helper.open(PartitionedRunEvaluationExtension())
        self.addCleanup(self.store.close)
        self.helper.seed(self.store)
        self.helper.begin(self.store)
        self.helper.put(self.store)
        self.store.dispatch(12001, 12001, request("plan_partition_commit", "diagnostic-plan-commit", upload_id="upload"))
        self.run_id = "one-entry-diagnostic-run"
        self.manifest = deepcopy(self.helper.bound["manifest"])
        self.manifest.update(schema_version=2, run_id=self.run_id, purpose="diagnostic",
                             baseline_ref=None, plan_ref=deepcopy(self.helper.plan_ref))
        self.profile = fixed_profile()
        self.begin_request = request(
            "run_begin_v2", "diagnostic-begin", manifest=self.manifest,
            contract_series_id="partition-series", expected_generation=1,
            execution_profile=self.profile)
        self.begin_response = self.store.dispatch(12004, 12004, deepcopy(self.begin_request))
        worker_digest = _lock()["worker_digest"]
        matches = [entry for entry in self.helper.bound["plan"]["entries"]
                   if entry["evaluator_ref"]["digest"] == worker_digest
                   and entry["target_ref"]["digest"] == hashlib.sha256(canonical_bytes({
                       "worker_digest": worker_digest, "scenario": SCENARIO})).hexdigest()
                   and len(entry["stage_ids"]) == 1]
        self.assertTrue(matches)
        self.selector = {key: matches[0][key] for key in
                         ("obligation_id", "case_id", "trial_id", "variant")}
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.runner = object.__new__(DockerRunner)
        self.runner.lock = _lock()
        self.runner.journal_path = self.folder / "execution.sqlite"
        self.runner.adapter_digest = _digest_file(Path(normalized.__file__))
        self.runner.isolation_digest = hashlib.sha256(canonical_bytes(PROFILE)).hexdigest()
        self.runtime = RuntimeAdapter(self.store, self.helper.clock)

    def fake_run(self, calls):
        def run(runner, scenario, binding, *, run_deadline, timeout_seconds=120, cancel_event=None):
            calls.append((scenario, deepcopy(binding)))
            image_id = runner.lock["image_id"]
            raw = canonical_bytes({"schema_version": 1, "kind": "gah_generic_result",
                "binding": binding, "mode": "constraint", "observations": {"check": "PASS"}})
            result = normalize_generic(raw, binding, execution_status="COMPLETED", exit_code=0,
                                       stop_confirmed=True)
            with ExecutionJournal(runner.journal_path) as journal:
                record = journal.begin(binding, scenario, image_id, run_deadline=run_deadline,
                                      timeout_seconds=timeout_seconds)
                record = journal.advance(binding["run_id"], binding["operation_id"], record["owner_token"],
                                         "CREATED", container_id="a" * 64)
                for state in ("STARTING", "RUNNING", "STOPPED"):
                    record = journal.advance(binding["run_id"], binding["operation_id"], record["owner_token"],
                                             state, container_id="a" * 64)
                receipt = runner._receipt(record, status="COMPLETED", reason=None, stopped=True,
                    cleaned=True, exit_code=0, elapsed=1, verified=True, normalized=result)
                return journal.finish(binding["run_id"], binding["operation_id"],
                                      record["owner_token"], receipt)["receipt"]
        return run

    def test_success_replay_dispatches_once_saves_v2_attempt_and_closes_resources(self):
        calls = []
        with patch.object(DockerRunner, "run", new=self.fake_run(calls)):
            first = execute_one_entry(self.runtime, self.runner, self.folder, self.begin_response,
                                      self.manifest, self.selector, begin_request=self.begin_request, clock=self.helper.clock)
            self.helper.clock.value += 61
            second = execute_one_entry(self.runtime, self.runner, self.folder, self.begin_response,
                                       self.manifest, self.selector, begin_request=self.begin_request, clock=self.helper.clock)
        self.assertEqual(len(calls), 1)
        self.assertEqual(first, second)
        self.assertEqual(first["execution_status"], "COMPLETED")
        self.assertFalse(first["diagnostic_finalized"])
        for flag in ("ci_eligible", "authority_connected", "adoption_verified", "admission_verified",
                     "baseline_freshness_verified", "resource_closure_verified"):
            self.assertFalse(first[flag])
        run_id = self.manifest["run_id"]
        attempt_rows = self.store._db.execute("SELECT attempt_id,attempt_json FROM attempts WHERE run_id=?", (run_id,)).fetchall()
        self.assertEqual(len(attempt_rows), 1)
        self.assertEqual(self.store._db.execute("SELECT COUNT(*) FROM eval_runs WHERE run_id=?", (run_id,)).fetchone()[0], 0)
        attempt = __import__("json").loads(attempt_rows[0]["attempt_json"])
        self.assertEqual(attempt["variant"], self.selector["variant"])
        self.assertEqual(attempt["expected_binding"]["operation_id"], first["operation_id"])
        operation = self.store._db.execute("SELECT stopped_at,settled_at FROM resource_operations WHERE operation_id=?",
                                           (first["operation_id"],)).fetchone()
        self.assertIsNotNone(operation["stopped_at"])
        self.assertIsNotNone(operation["settled_at"])
        self.assertIsNotNone(self.store._db.execute("SELECT closed_at FROM resource_runs WHERE run_id=?",
                                                     (run_id,)).fetchone()[0])
        actions = [value["action"] for _, value in self.runtime.calls]
        self.assertLess(actions.index("resource_start"), actions.index("resource_observe"))
        self.assertLess(actions.index("resource_observe"), actions.index("evidence_record_v2"))
        self.assertLess(actions.index("evidence_record_v2"), actions.index("resource_close"))
        self.assertEqual(actions.count("resource_claim"), 1)
        self.assertEqual(actions.count("resource_close"), 1)

    def test_timeout_leaves_dispatch_unsettled_and_never_records_attempt_or_closes(self):
        def timeout(*args, **kwargs):
            raise RunnerError("TIMEOUT")
        with patch.object(DockerRunner, "run", new=timeout),              patch.object(DockerRunner, "recover", side_effect=RunnerError("DOCKER_UNAVAILABLE")):
            with self.assertRaisesRegex(PartitionedDiagnosticError, "^TIMEOUT$"):
                execute_one_entry(self.runtime, self.runner, self.folder, self.begin_response,
                                  self.manifest, self.selector, begin_request=self.begin_request, clock=self.helper.clock)
        operation_id = next(value["operation_id"] for _, value in self.runtime.calls
                            if value["action"] == "resource_start")
        row = self.store._db.execute("SELECT stopped_at,settled_at FROM resource_operations WHERE operation_id=?",
                                     (operation_id,)).fetchone()
        self.assertIsNone(row["stopped_at"])
        self.assertIsNone(row["settled_at"])
        self.assertEqual(self.store._db.execute("SELECT COUNT(*) FROM attempts WHERE run_id=?",
                                                (self.manifest["run_id"],)).fetchone()[0], 0)
        actions = [value["action"] for _, value in self.runtime.calls]
        self.assertNotIn("resource_observe", actions)
        self.assertNotIn("evidence_record_v2", actions)
        self.assertNotIn("resource_close", actions)




    def _recover_from_partial_journal(self, state):
        operation_id_holder = []
        container_id = "c" * 64
        def partial_run(runner, scenario, binding, *, run_deadline, timeout_seconds=120, cancel_event=None):
            with ExecutionJournal(runner.journal_path) as journal:
                record = journal.begin(binding, scenario, runner.lock["image_id"],
                                       run_deadline=run_deadline, timeout_seconds=timeout_seconds)
                record = journal.advance(binding["run_id"], binding["operation_id"], record["owner_token"],
                                         "CREATED", container_id=container_id)
                for next_state in ("STARTING", "RUNNING"):
                    record = journal.advance(binding["run_id"], binding["operation_id"], record["owner_token"],
                                             next_state, container_id=container_id)
                if state == "STOPPED":
                    journal.advance(binding["run_id"], binding["operation_id"], record["owner_token"],
                                    "STOPPED", container_id=container_id)
            operation_id_holder.append(binding["operation_id"])
            raise RunnerError("TIMEOUT")
        with patch.object(DockerRunner, "run", new=partial_run), \
             patch.object(DockerRunner, "recover", side_effect=RunnerError("DOCKER_UNAVAILABLE")):
            with self.assertRaisesRegex(PartitionedDiagnosticError, "^TIMEOUT$"):
                execute_one_entry(self.runtime, self.runner, self.folder, self.begin_response,
                    self.manifest, self.selector, begin_request=self.begin_request, clock=self.helper.clock)
        self.assertEqual(len(operation_id_holder), 1)
        operation_id = operation_id_holder[0]
        def finalize_recovery(runner, run_id, op_id):
            with ExecutionJournal(runner.journal_path) as journal:
                record = journal.get(run_id, op_id)
                if record["state"] != "STOPPED":
                    record = journal.advance(run_id, op_id, record["owner_token"],
                                             "STOPPED", container_id=container_id)
                receipt = runner._receipt(record, status="FAILED", reason="INTERRUPTED",
                    stopped=True, cleaned=True, exit_code=137, elapsed=10, verified=False, recovered=True)
                return journal.finish(run_id, op_id, record["owner_token"], receipt)["receipt"]
        with patch.object(DockerRunner, "recover", new=finalize_recovery):
            with self.assertRaisesRegex(PartitionedDiagnosticError, "^INTERRUPTED_OPERATION$"):
                execute_one_entry(self.runtime, self.runner, self.folder, self.begin_response,
                    self.manifest, self.selector, begin_request=self.begin_request, clock=self.helper.clock)
        row = self.store._db.execute("SELECT stopped_at,settled_at FROM resource_operations WHERE operation_id=?",
                                     (operation_id,)).fetchone()
        self.assertIsNotNone(row["stopped_at"])
        self.assertIsNotNone(row["settled_at"])
        self.assertEqual(self.store._db.execute("SELECT COUNT(*) FROM attempts WHERE run_id=?",
                                                (self.manifest["run_id"],)).fetchone()[0], 0)
        self.assertIsNotNone(self.store._db.execute("SELECT closed_at FROM resource_runs WHERE run_id=?",
                                                     (self.manifest["run_id"],)).fetchone()[0])

    def test_running_journal_recover_finalized_stop_is_ledger_recovered_without_attempt(self):
        self._recover_from_partial_journal("RUNNING")

    def test_stopped_journal_recover_finalized_stop_is_ledger_recovered_without_attempt(self):
        self._recover_from_partial_journal("STOPPED")

    def _run_until_end_checkpoint_failure(self):
        calls = []
        original_put = Checkpoint.put
        failed = {"value": False}
        def fail_end_once(checkpoint, key, payload):
            if key.startswith("end-") and not failed["value"]:
                failed["value"] = True
                raise CheckpointError("INJECTED_END_CHECKPOINT_FAILURE")
            return original_put(checkpoint, key, payload)
        with patch.object(DockerRunner, "run", new=self.fake_run(calls)), \
             patch.object(Checkpoint, "put", new=fail_end_once):
            with self.assertRaisesRegex(CheckpointError, "INJECTED_END_CHECKPOINT_FAILURE"):
                execute_one_entry(self.runtime, self.runner, self.folder, self.begin_response,
                                  self.manifest, self.selector, begin_request=self.begin_request, clock=self.helper.clock)
        operation_id = next(value["operation_id"] for _, value in self.runtime.calls
                            if value["action"] == "resource_start")
        return operation_id, calls

    def _receipt_from_journal(self, run_id, operation_id):
        with ExecutionJournal(self.runner.journal_path) as journal:
            return journal.get(run_id, operation_id)["receipt"]

    def test_finished_journal_after_end_checkpoint_crash_recovers_ledger_without_attempt(self):
        operation_id, calls = self._run_until_end_checkpoint_failure()
        run_id = self.manifest["run_id"]
        with patch.object(DockerRunner, "recover", new=lambda runner, r, op: self._receipt_from_journal(r, op)):
            with self.assertRaisesRegex(PartitionedDiagnosticError, "^INTERRUPTED_OPERATION$"):
                execute_one_entry(self.runtime, self.runner, self.folder, self.begin_response,
                                  self.manifest, self.selector, begin_request=self.begin_request, clock=self.helper.clock)
        actions = [value["action"] for _, value in self.runtime.calls]
        self.assertIn("resource_cancel_claim", actions)
        self.assertIn("resource_observe", actions)
        self.assertIn("resource_close", actions)
        self.assertNotIn("evidence_record_v2", actions)
        self.assertEqual(self.store._db.execute("SELECT COUNT(*) FROM attempts WHERE run_id=?", (run_id,)).fetchone()[0], 0)
        op = self.store._db.execute("SELECT stopped_at,settled_at FROM resource_operations WHERE operation_id=?", (operation_id,)).fetchone()
        self.assertIsNotNone(op["stopped_at"])
        self.assertIsNotNone(op["settled_at"])
        self.assertIsNotNone(self.store._db.execute("SELECT closed_at FROM resource_runs WHERE run_id=?", (run_id,)).fetchone()[0])
        self.assertEqual(len(calls), 1)

    def test_saved_finished_end_recovers_after_expired_status_without_attempt(self):
        calls = []
        original_client = self.runtime.client
        class Expired(Exception):
            code = "SOURCE_EXPIRED"
        def deny_observe(uid, value):
            if value.get("action") == "resource_observe":
                raise Expired()
            return original_client(uid, value)
        self.runtime.client = deny_observe
        try:
            with patch.object(DockerRunner, "run", new=self.fake_run(calls)):
                with self.assertRaisesRegex(PartitionedDiagnosticError, "^SOURCE_EXPIRED$"):
                    execute_one_entry(self.runtime, self.runner, self.folder, self.begin_response,
                                      self.manifest, self.selector, begin_request=self.begin_request, clock=self.helper.clock)
        finally:
            self.runtime.client = original_client
        op_id = next(value["operation_id"] for _, value in self.runtime.calls
                     if value["action"] == "resource_start")
        self.assertTrue(self.folder.joinpath("checkpoints").exists())
        def deny_status(uid, value):
            if value.get("action") == "run_status_v2":
                raise Expired()
            return original_client(uid, value)
        self.runtime.client = deny_status
        try:
            with self.assertRaisesRegex(PartitionedDiagnosticError, "^SOURCE_EXPIRED$"):
                execute_one_entry(self.runtime, self.runner, self.folder, self.begin_response,
                                  self.manifest, self.selector, begin_request=self.begin_request, clock=self.helper.clock)
        finally:
            self.runtime.client = original_client
        row = self.store._db.execute("SELECT stopped_at,settled_at FROM resource_operations WHERE operation_id=?", (op_id,)).fetchone()
        self.assertIsNotNone(row["stopped_at"])
        self.assertIsNotNone(row["settled_at"])
        self.assertEqual(self.store._db.execute("SELECT COUNT(*) FROM attempts WHERE run_id=?", (self.manifest["run_id"],)).fetchone()[0], 0)
        self.assertIsNotNone(self.store._db.execute("SELECT closed_at FROM resource_runs WHERE run_id=?", (self.manifest["run_id"],)).fetchone()[0])
        actions = [value["action"] for _, value in self.runtime.calls]
        self.assertIn("resource_cancel_claim", actions)
        self.assertIn("resource_close", actions)
        self.assertEqual(len(calls), 1)

    def test_saved_finished_end_recovers_after_expired_close_snapshot(self):
        calls = []
        original_client = self.runtime.client
        class Expired(Exception):
            code = "SOURCE_EXPIRED"
        def deny_begin_snapshot(uid, value):
            if value.get("action") == "run_begin_v2":
                raise Expired()
            return original_client(uid, value)
        self.runtime.client = deny_begin_snapshot
        try:
            with patch.object(DockerRunner, "run", new=self.fake_run(calls)):
                with self.assertRaisesRegex(PartitionedDiagnosticError, "^SOURCE_EXPIRED$"):
                    execute_one_entry(self.runtime, self.runner, self.folder, self.begin_response,
                        self.manifest, self.selector, begin_request=self.begin_request,
                        clock=self.helper.clock)
        finally:
            self.runtime.client = original_client
        run_id = self.manifest["run_id"]
        op_id = next(value["operation_id"] for _, value in self.runtime.calls
                     if value["action"] == "resource_start")
        row = self.store._db.execute(
            "SELECT stopped_at,settled_at FROM resource_operations WHERE operation_id=?", (op_id,)
        ).fetchone()
        self.assertIsNotNone(row["stopped_at"])
        self.assertIsNotNone(row["settled_at"])
        actions = [value["action"] for _, value in self.runtime.calls]
        self.assertIn("resource_claim", actions, actions)
        self.assertIn("resource_close", actions, actions)
        self.assertIsNotNone(self.store._db.execute(
            "SELECT closed_at FROM resource_runs WHERE run_id=?", (run_id,)
        ).fetchone()[0])
        self.assertEqual(self.store._db.execute(
            "SELECT COUNT(*) FROM attempts WHERE run_id=?", (run_id,)
        ).fetchone()[0], 1)
        self.assertEqual(len(calls), 1)
        self.assertIsNone(Checkpoint(self.folder).get("diagnostic-resource-close-" + op_id))

    def test_61_second_worker_advances_owner_lease_before_close_only(self):
        calls = []
        normal = self.fake_run(calls)
        def slow_run(runner, scenario, binding, **kwargs):
            value = normal(runner, scenario, binding, **kwargs)
            self.helper.clock.value += 61
            return value
        with patch.object(DockerRunner, "run", new=slow_run):
            result = execute_one_entry(self.runtime, self.runner, self.folder, self.begin_response,
                                       self.manifest, self.selector, begin_request=self.begin_request, clock=self.helper.clock)
        row = self.store._db.execute("SELECT owner_epoch FROM resource_operations WHERE operation_id=?",
                                     (result["operation_id"],)).fetchone()
        attempt = self.store._db.execute("SELECT attempt_json FROM attempts WHERE attempt_id=?",
                                         (result["attempt_id"],)).fetchone()
        import json
        original_epoch = json.loads(attempt["attempt_json"])["expected_binding"]["owner_epoch"]
        current_epoch = self.store._db.execute("SELECT owner_epoch FROM resource_runs WHERE run_id=?",
                                               (result["run_id"],)).fetchone()[0]
        self.assertEqual(row["owner_epoch"], original_epoch)
        self.assertGreater(current_epoch, original_epoch)
        self.assertEqual(result["execution_status"], "COMPLETED")
        self.assertTrue(result["resource_closed"])

    def test_close_commit_before_checkpoint_is_recovered_by_duplicate_begin_snapshot(self):
        calls = []
        original_put = Checkpoint.put
        failed = {"value": False}
        def fail_close_ack(checkpoint, key, payload):
            if key.startswith("diagnostic-resource-close-") and not failed["value"]:
                failed["value"] = True
                raise CheckpointError("INJECTED_CLOSE_ACK_GAP")
            return original_put(checkpoint, key, payload)
        with patch.object(DockerRunner, "run", new=self.fake_run(calls)), \
             patch.object(Checkpoint, "put", new=fail_close_ack):
            with self.assertRaisesRegex(CheckpointError, "INJECTED_CLOSE_ACK_GAP"):
                execute_one_entry(self.runtime, self.runner, self.folder, self.begin_response,
                    self.manifest, self.selector, begin_request=self.begin_request, clock=self.helper.clock)
        run_id = self.manifest["run_id"]
        self.assertIsNotNone(self.store._db.execute("SELECT closed_at FROM resource_runs WHERE run_id=?", (run_id,)).fetchone()[0])
        self.helper.clock.value += 61
        with patch.object(DockerRunner, "run", new=self.fake_run(calls)):
            result = execute_one_entry(self.runtime, self.runner, self.folder, self.begin_response,
                self.manifest, self.selector, begin_request=self.begin_request, clock=self.helper.clock)
        self.assertTrue(result["resource_closed"])
        self.assertEqual(result["execution_status"], "COMPLETED")
        self.assertEqual(len(calls), 1)
        actions = [value["action"] for _, value in self.runtime.calls]
        self.assertEqual(actions.count("resource_close"), 1)
        self.assertEqual(self.store._db.execute("SELECT COUNT(*) FROM attempts WHERE run_id=?", (run_id,)).fetchone()[0], 1)

    def test_lost_close_claim_ack_replays_same_request_then_renews_expired_lease(self):
        calls = []
        original_client = self.runtime.client
        lost = {"value": False}
        class LostAck(Exception):
            code = "AUTHORITY_UNAVAILABLE"
        def lose_claim_ack(uid, value):
            response = original_client(uid, value)
            if value.get("action") == "resource_claim" and not lost["value"]:
                lost["value"] = True
                raise LostAck()
            return response
        self.runtime.client = lose_claim_ack
        try:
            with patch.object(DockerRunner, "run", new=self.fake_run(calls)):
                with self.assertRaisesRegex(PartitionedDiagnosticError, "^AUTHORITY_UNAVAILABLE$"):
                    execute_one_entry(self.runtime, self.runner, self.folder, self.begin_response,
                        self.manifest, self.selector, begin_request=self.begin_request, clock=self.helper.clock)
        finally:
            self.runtime.client = original_client
        claim_requests = [value for _, value in self.runtime.calls if value["action"] == "resource_claim"]
        self.assertEqual(len(claim_requests), 1)
        self.helper.clock.value += 61
        with patch.object(DockerRunner, "run", new=self.fake_run(calls)):
            result = execute_one_entry(self.runtime, self.runner, self.folder, self.begin_response,
                self.manifest, self.selector, begin_request=self.begin_request, clock=self.helper.clock)
        claim_requests = [value for _, value in self.runtime.calls if value["action"] == "resource_claim"]
        self.assertEqual(len(claim_requests), 2)
        self.assertEqual(claim_requests[0], claim_requests[1])
        self.assertTrue(result["resource_closed"])
        self.assertEqual(len(calls), 1)
        run_id, op_id = result["run_id"], result["operation_id"]
        attempt_row = self.store._db.execute("SELECT attempt_json FROM attempts WHERE run_id=?", (run_id,)).fetchone()
        import json
        original_epoch = json.loads(attempt_row["attempt_json"])["expected_binding"]["owner_epoch"]
        operation_epoch = self.store._db.execute("SELECT owner_epoch FROM resource_operations WHERE operation_id=?", (op_id,)).fetchone()[0]
        run_epoch = self.store._db.execute("SELECT owner_epoch FROM resource_runs WHERE run_id=?", (run_id,)).fetchone()[0]
        self.assertEqual(operation_epoch, original_epoch)
        self.assertGreater(run_epoch, original_epoch)
        self.assertEqual(self.store._db.execute("SELECT COUNT(*) FROM attempts WHERE run_id=?", (run_id,)).fetchone()[0], 1)

    def test_authority_read_denial_still_recovers_local_worker_and_preserves_reason(self):
        operation_id, _ = self._run_until_end_checkpoint_failure()
        recovered = []
        original_recover = DockerRunner.recover
        def recover(runner, run_id, op_id):
            recovered.append(op_id)
            return self._receipt_from_journal(run_id, op_id)
        original_client = self.runtime.client
        class Denied(Exception):
            code = "SOURCE_STALE"
        def denied(uid, value):
            if value.get("action") == "resource_operation":
                raise Denied()
            return original_client(uid, value)
        with patch.object(DockerRunner, "recover", new=recover):
            self.runtime.client = denied
            try:
                with self.assertRaisesRegex(PartitionedDiagnosticError, "^SOURCE_STALE$"):
                    execute_one_entry(self.runtime, self.runner, self.folder, self.begin_response,
                                      self.manifest, self.selector, begin_request=self.begin_request, clock=self.helper.clock)
            finally:
                self.runtime.client = original_client
        self.assertEqual(recovered, [operation_id])
        actions = [value["action"] for _, value in self.runtime.calls]
        self.assertNotIn("resource_cancel_claim", actions)
        row = self.store._db.execute("SELECT stopped_at,settled_at FROM resource_operations WHERE operation_id=?", (operation_id,)).fetchone()
        self.assertIsNone(row["stopped_at"])
        self.assertIsNone(row["settled_at"])

    def test_recovery_rejects_corrupted_finished_receipt_without_ledger_mutation(self):
        operation_id, _ = self._run_until_end_checkpoint_failure()
        def corrupt(runner, run_id, op_id):
            receipt = deepcopy(self._receipt_from_journal(run_id, op_id))
            receipt["image_id"] = "sha256:" + "0" * 64
            return receipt
        with patch.object(DockerRunner, "recover", new=corrupt):
            with self.assertRaisesRegex(PartitionedDiagnosticError, "^RECEIPT_INVALID$"):
                execute_one_entry(self.runtime, self.runner, self.folder, self.begin_response,
                                  self.manifest, self.selector, begin_request=self.begin_request, clock=self.helper.clock)
        actions = [value["action"] for _, value in self.runtime.calls]
        self.assertNotIn("resource_cancel_claim", actions)
        row = self.store._db.execute("SELECT stopped_at,settled_at FROM resource_operations WHERE operation_id=?", (operation_id,)).fetchone()
        self.assertIsNone(row["stopped_at"])
        self.assertIsNone(row["settled_at"])

    def test_keyboard_interrupt_attempts_recovery_and_keeps_unconfirmed_ledger_open(self):
        recovered = []
        def interrupt(*args, **kwargs):
            raise KeyboardInterrupt()
        def recover(runner, run_id, operation_id):
            recovered.append(operation_id)
            raise RunnerError("DOCKER_UNAVAILABLE")
        with patch.object(DockerRunner, "run", new=interrupt), \
             patch.object(DockerRunner, "recover", new=recover):
            with self.assertRaisesRegex(PartitionedDiagnosticError, "^INTERRUPTED_OPERATION$"):
                execute_one_entry(self.runtime, self.runner, self.folder, self.begin_response,
                                  self.manifest, self.selector, begin_request=self.begin_request, clock=self.helper.clock)
        self.assertEqual(len(recovered), 1)
        operation_id = recovered[0]
        row = self.store._db.execute("SELECT stopped_at,settled_at FROM resource_operations WHERE operation_id=?", (operation_id,)).fetchone()
        self.assertIsNone(row["stopped_at"])
        self.assertIsNone(row["settled_at"])

    def test_bool_selector_is_rejected_before_any_authority_dispatch(self):
        bad = dict(self.selector)
        bad["trial_id"] = True
        with self.assertRaisesRegex(PartitionedDiagnosticError, "^INVALID_SELECTOR$"):
            execute_one_entry(self.runtime, self.runner, self.folder, self.begin_response,
                              self.manifest, bad, begin_request=self.begin_request, clock=self.helper.clock)
        self.assertEqual(self.runtime.calls, [])

    def test_unknown_manifest_field_is_rejected_before_any_authority_dispatch(self):
        bad = dict(self.manifest)
        bad["untrusted"] = "ignored-is-not-allowed"
        with self.assertRaises(PartitionedDiagnosticError):
            execute_one_entry(self.runtime, self.runner, self.folder, self.begin_response,
                              bad, self.selector, begin_request=self.begin_request, clock=self.helper.clock)
        self.assertEqual(self.runtime.calls, [])

    def test_wrong_journal_path_is_rejected_before_any_authority_dispatch(self):
        other = object.__new__(DockerRunner)
        other.lock = self.runner.lock
        other.journal_path = self.folder / "other.sqlite"
        other.adapter_digest = self.runner.adapter_digest
        other.isolation_digest = self.runner.isolation_digest
        other.target_digest = self.runner.target_digest
        with self.assertRaisesRegex(PartitionedDiagnosticError, "^JOURNAL_PATH_MISMATCH$"):
            execute_one_entry(self.runtime, other, self.folder, self.begin_response,
                              self.manifest, self.selector, begin_request=self.begin_request, clock=self.helper.clock)
        self.assertEqual(self.runtime.calls, [])


if __name__ == "__main__":
    unittest.main()
