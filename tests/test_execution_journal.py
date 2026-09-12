import copy
import hashlib
import sqlite3
from pathlib import Path
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gah.contracts import ContractError
from gah.execution_journal import ExecutionJournal, JournalError
from gah.wire import canonical_bytes


_DIGEST = "a" * 64
_CONTAINER = "c" * 64


def binding(run_id="run-1", operation_id="op-1"):
    return {
        "run_id": run_id, "operation_id": operation_id, "owner_epoch": 1,
        "contract_digest": _DIGEST, "target_digest": _DIGEST,
        "obligation_id": "obligation-1", "case_id": "case-1", "trial_id": "trial-1",
        "stage_id": "stage-1", "fixture_digest": _DIGEST, "adapter_digest": _DIGEST,
        "policy_digest": _DIGEST, "evaluator_digest": _DIGEST, "isolation_digest": _DIGEST,
    }


def receipt(entry, *, status="COMPLETED", container_id=None):
    completed = status == "COMPLETED"
    return {
        "schema_version": 1, "kind": "fixture_execution", "binding": entry["binding"],
        "scenario": entry["scenario"], "image_id": entry["image_id"],
        "container_id": container_id if container_id is not None else entry["container_id"],
        "execution_status": status, "reason": None if completed else "EXECUTION_FAILURE",
        "exit_code": 0 if completed else 1, "stop_confirmed": True,
        "elapsed_millis": 12, "isolation_config_verified": completed,
        "output_disposition": "ADMITTED" if completed else "NOT_COLLECTED",
        "cleanup_confirmed": True, "recovered": False,
        "normalized_result": normalized(entry) if completed and entry["scenario"] != "probe:isolation" else None,
        "probe_result": probe(entry) if completed and entry["scenario"] == "probe:isolation" else None,
        "ci_eligible": False,
    }


def normalized(entry):
    return {
        "schema_version": 1, "kind": "normalized_result", "binding": entry["binding"],
        "mode": "constraint", "observation": "PASS", "mutation_outcome": None,
        "detection": None, "deviation": None, "error_class": None, "raw_digest": _DIGEST,
    }


def probe(entry):
    return {
        "schema_version": 1, "kind": "gah_isolation_probe", "binding": entry["binding"],
        "checks": {name: True for name in (
            "nonroot", "root_readonly", "network_unreachable", "capabilities_dropped",
            "no_new_privileges", "seccomp_filter", "pid_limit", "memory_limit", "cpu_limit",
        )},
    }


class ExecutionJournalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "execution.sqlite"
        self.scenario = "constraint:C01:good"
        self.image = "sha256:" + _DIGEST

    def open(self):
        return ExecutionJournal(self.path)

    def start(self, journal, run_id="run-1", operation_id="op-1"):
        return journal.begin(binding(run_id, operation_id), self.scenario, self.image,
                             run_deadline=2000, timeout_seconds=30)

    def test_begin_replay_is_immutable_and_conflict_is_rejected(self):
        with self.open() as journal:
            first = self.start(journal)
            replay = self.start(journal)
            self.assertTrue(first["new"])
            self.assertFalse(replay["new"])
            self.assertEqual(replay["request_digest"], first["request_digest"])
            self.assertEqual(replay["container_name"], first["container_name"])
            self.assertEqual(replay["owner_token"], first["owner_token"])
            with self.assertRaisesRegex(JournalError, "^REQUEST_CONFLICT$"):
                journal.begin(binding(), self.scenario, self.image,
                              run_deadline=2001, timeout_seconds=30)

    def test_ordered_states_owner_and_container_are_enforced(self):
        with self.open() as journal:
            entry = self.start(journal)
            token = entry["owner_token"]
            with self.assertRaisesRegex(JournalError, "^OWNER_MISMATCH$"):
                journal.advance("run-1", "op-1", "0" * 32, "CREATED", container_id=_CONTAINER)
            with self.assertRaisesRegex(JournalError, "^INVALID_TRANSITION$"):
                journal.advance("run-1", "op-1", token, "RUNNING", container_id=_CONTAINER)
            for state in ("CREATED", "STARTING", "RUNNING", "STOPPED"):
                self.assertEqual(
                    journal.advance("run-1", "op-1", token, state, container_id=_CONTAINER)["state"],
                    state,
                )
                self.assertEqual(
                    journal.advance("run-1", "op-1", token, state, container_id=_CONTAINER)["state"],
                    state,
                )
            with self.assertRaisesRegex(JournalError, "^STATE_CONFLICT$"):
                journal.advance("run-1", "op-1", token, "STOPPED", container_id="d" * 64)

    def test_finish_from_any_pending_state_is_immutable_and_read_verified(self):
        with self.open() as journal:
            entry = self.start(journal)
            final = receipt(entry, status="FAILED")
            stored = journal.finish("run-1", "op-1", entry["owner_token"], final)
            self.assertEqual(stored["state"], "FINISHED")
            self.assertEqual(stored["receipt"], final)
            self.assertEqual(journal.finish("run-1", "op-1", entry["owner_token"], copy.deepcopy(final))["receipt"], final)
            altered = copy.deepcopy(final)
            altered["reason"] = "DOCKER_UNAVAILABLE"
            with self.assertRaisesRegex(JournalError, "^RECEIPT_CONFLICT$"):
                journal.finish("run-1", "op-1", entry["owner_token"], altered)
            self.assertEqual(journal.pending(), [])

    def test_receipt_shape_binding_and_size_are_rejected(self):
        with self.open() as journal:
            entry = self.start(journal)
            entry = journal.advance("run-1", "op-1", entry["owner_token"], "CREATED", container_id=_CONTAINER)
            entry = journal.advance("run-1", "op-1", entry["owner_token"], "STOPPED", container_id=_CONTAINER)
            token = entry["owner_token"]
            bad = receipt(entry)
            bad["ci_eligible"] = True
            with self.assertRaises(ContractError):
                journal.finish("run-1", "op-1", token, bad)
            bad = receipt(entry)
            bad["binding"] = binding("other", "op-1")
            with self.assertRaises(ContractError):
                journal.finish("run-1", "op-1", token, bad)
            bad = receipt(entry)
            bad["container_id"] = None
            with self.assertRaises(ContractError):
                journal.finish("run-1", "op-1", token, bad)
            bad = receipt(entry)
            del bad["reason"]
            with self.assertRaises(ContractError):
                journal.finish("run-1", "op-1", token, bad)
            bad = receipt(entry)
            bad["extra"] = "raw"
            with self.assertRaises(ContractError):
                journal.finish("run-1", "op-1", token, bad)
            bad = receipt(entry)
            bad["scenario"] = "probe:crash"
            with self.assertRaises(ContractError):
                journal.finish("run-1", "op-1", token, bad)
            bad = receipt(entry, container_id="d" * 64)
            with self.assertRaises(ContractError):
                journal.finish("run-1", "op-1", token, bad)
            bad = receipt(entry)
            bad["extra"] = "x" * (512 * 1024)
            with self.assertRaisesRegex(ContractError, "^RECEIPT_TOO_LARGE$"):
                journal.finish("run-1", "op-1", token, bad)
            good = receipt(entry)
            good.update({
                "reason": None, "elapsed_millis": 12,
                "isolation_config_verified": True, "output_disposition": "ADMITTED",
                "cleanup_confirmed": True, "recovered": False,
                "normalized_result": normalized(entry), "probe_result": None,
            })
            self.assertEqual(journal.finish("run-1", "op-1", token, good)["receipt"], good)

    def test_stopped_can_be_recorded_directly_and_completion_requires_one_result(self):
        with self.open() as journal:
            entry = self.start(journal)
            token = entry["owner_token"]
            journal.advance("run-1", "op-1", token, "CREATED", container_id=_CONTAINER)
            stopped = journal.advance("run-1", "op-1", token, "STOPPED", container_id=_CONTAINER)
            self.assertEqual(stopped["state"], "STOPPED")

            invalid = receipt(stopped)
            invalid["stop_confirmed"] = False
            with self.assertRaises(ContractError):
                journal.finish("run-1", "op-1", token, invalid)

            invalid = receipt(stopped)
            invalid["probe_result"] = probe(stopped)
            with self.assertRaises(ContractError):
                journal.finish("run-1", "op-1", token, invalid)

            valid = receipt(stopped)
            self.assertEqual(journal.finish("run-1", "op-1", token, valid)["state"], "FINISHED")
            self.assertEqual(journal.finish("run-1", "op-1", token, valid)["receipt"], valid)
            altered = copy.deepcopy(valid)
            altered["elapsed_millis"] += 1
            with self.assertRaisesRegex(JournalError, "^RECEIPT_CONFLICT$"):
                journal.finish("run-1", "op-1", token, altered)

            probe_entry = journal.begin(binding("probe-run", "probe-op"), "probe:isolation", self.image,
                                        run_deadline=2000, timeout_seconds=30)
            probe_entry = journal.advance("probe-run", "probe-op", probe_entry["owner_token"],
                                          "CREATED", container_id=_CONTAINER)
            probe_entry = journal.advance("probe-run", "probe-op", probe_entry["owner_token"],
                                          "STOPPED", container_id=_CONTAINER)
            failed_probe = receipt(probe_entry)
            failed_probe["probe_result"]["checks"]["network_unreachable"] = False
            with self.assertRaises(ContractError):
                journal.finish("probe-run", "probe-op", probe_entry["owner_token"], failed_probe)
            self.assertEqual(
                journal.finish("probe-run", "probe-op", probe_entry["owner_token"], receipt(probe_entry))["state"],
                "FINISHED",
            )

    def test_pending_restart_and_concurrent_begin_have_one_intent(self):
        first = self.open()
        first.close()
        barrier = threading.Barrier(2)

        def attempt():
            with self.open() as journal:
                barrier.wait(timeout=5)
                return journal.begin(binding("same", "op"), self.scenario, self.image,
                                     run_deadline=2000, timeout_seconds=30)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [future.result(timeout=10) for future in [pool.submit(attempt) for _ in range(2)]]
        self.assertEqual(sum(result["new"] for result in results), 1)
        with self.open() as journal:
            pending = journal.pending()
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0]["state"], "INTENT")

    def test_storage_failure_rolls_back_and_corruption_is_rejected(self):
        with self.open() as journal:
            entry = self.start(journal)
            def deny_update(action, arg1, *_):
                if action == sqlite3.SQLITE_UPDATE and arg1 == "executions":
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK
            journal._db.set_authorizer(deny_update)
            with self.assertRaisesRegex(JournalError, "^STORAGE_FAILURE$"):
                journal.advance("run-1", "op-1", entry["owner_token"], "CREATED", container_id=_CONTAINER)
            journal._db.set_authorizer(None)
            self.assertEqual(journal.get("run-1", "op-1")["state"], "INTENT")
            journal._db.execute("UPDATE executions SET request_digest=?", ("b" * 64,))
            with self.assertRaisesRegex(JournalError, "^STORAGE_CORRUPT$"):
                journal.get("run-1", "op-1")

    def test_base_exception_rolls_back(self):
        with self.open() as journal:
            self.start(journal)
            with self.assertRaises(KeyboardInterrupt):
                with journal._transaction() as db:
                    db.execute("UPDATE executions SET state='CREATED'")
                    raise KeyboardInterrupt()
            self.assertEqual(journal.get("run-1", "op-1")["state"], "INTENT")

    def test_invalid_begin_inputs_and_missing_are_rejected(self):
        with self.open() as journal:
            for scenario in ("constraint:C11:good", "mutation:F06:healthy", "probe:unknown"):
                with self.assertRaises(ContractError):
                    journal.begin(binding(), scenario, self.image, run_deadline=2000, timeout_seconds=30)
            with self.assertRaises(ContractError):
                journal.begin(binding(), self.scenario, "sha256:" + "A" * 64,
                              run_deadline=2000, timeout_seconds=30)
            with self.assertRaises(ContractError):
                journal.begin(binding(), self.scenario, self.image,
                              run_deadline=2000, timeout_seconds=121)
            with self.assertRaisesRegex(JournalError, "^NOT_FOUND$"):
                journal.get("missing", "op")


if __name__ == "__main__":
    unittest.main()
