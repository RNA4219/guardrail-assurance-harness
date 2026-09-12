"""独立run evidence storeの再配送、再計算、現在状態境界を検査する。"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from gah.run_contracts import bind_run_manifest
import gah.run_evidence as evidence_module
from gah.run_evidence import EvidenceError, RunEvidenceStore, bound_bundle_digest, MAX_INTEGER
from test_run_contracts import _fixtures, _manifest, _plan


ZERO = "0" * 64


class Clock:
    def __init__(self, value: int = 100):
        self.value = value

    def __call__(self) -> int:
        return self.value


def bound_fixture():
    fixtures = _fixtures()
    plan = _plan(fixtures)
    bound = bind_run_manifest(
        _manifest(fixtures, plan), fixtures["contract"], plan,
        fixtures["policy"], fixtures["registry"], fixtures["case_set"],
    )
    return fixtures, plan, bound


def attempt_for(fixtures, plan, obligation_id: str, *, attempt_id: str,
                mode: str, variant: str = "candidate") -> dict:
    entry = next(item for item in plan["entries"]
                 if item["obligation_id"] == obligation_id and item["variant"] == variant)
    binding = {
        "run_id": "run-1", "operation_id": "operation-" + attempt_id,
        "owner_epoch": 1, "contract_digest": fixtures["contract_ref_digest"],
        "target_digest": entry["target_ref"]["digest"], "obligation_id": obligation_id,
        "case_id": "case-1", "trial_id": "trial-1", "stage_id": "stage-1",
        "fixture_digest": ZERO, "adapter_digest": "1" * 64,
        "policy_digest": fixtures["policy_ref_digest"],
        "evaluator_digest": entry["evaluator_ref"]["digest"],
        "isolation_digest": "2" * 64,
    }
    if mode == "constraint":
        result = {"schema_version": 1, "kind": "normalized_result", "binding": binding,
                  "mode": "constraint", "observation": "PASS", "mutation_outcome": None,
                  "detection": None, "deviation": None, "error_class": None, "raw_digest": "a" * 64}
    elif mode == "mutation":
        result = {"schema_version": 1, "kind": "normalized_result", "binding": binding,
                  "mode": "mutation", "observation": "PASS", "mutation_outcome": "KILLED",
                  "detection": None, "deviation": None, "error_class": None, "raw_digest": "b" * 64}
    else:
        result = {"schema_version": 1, "kind": "normalized_result", "binding": binding,
                  "mode": "llm", "observation": None, "mutation_outcome": None,
                  "detection": "detect", "deviation": False, "error_class": None, "raw_digest": "c" * 64}
    return {"schema_version": 1, "kind": "attempt_record", "attempt_id": attempt_id,
            "variant": variant, "retry_of": None, "started_at": 100,
            "finished_at": 100, "stop_confirmed": True, "execution_status": "COMPLETED",
            "state_restored": True, "expected_binding": binding, "result": result}


class RunEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.clock = Clock()
        self.path = Path(self.temp.name) / "evidence.sqlite"
        self.fixtures, self.plan, self.bound = bound_fixture()
        self.fixtures["contract_ref_digest"] = self.bound["manifest"]["contract_ref"]["digest"]
        self.fixtures["policy_ref_digest"] = self.bound["manifest"]["policy_ref"]["digest"]
        self.profile = {"fixture_digest": ZERO, "adapter_digests": ["1" * 64],
                        "isolation_digest": "2" * 64}

    def open(self):
        return RunEvidenceStore(self.path, clock=self.clock,
                                allowed_bindings={"run-1": bound_bundle_digest(self.bound)})

    def start(self, store):
        return store.start_run(self.bound, self.profile)

    def records(self, store):
        for obligation, mode, name in (
            ("obligation-constraint", "constraint", "attempt-constraint"),
            ("obligation-mutation", "mutation", "attempt-mutation"),
            ("obligation-llm", "llm", "attempt-llm"),
        ):
            self.assertTrue(store.record_attempt(attempt_for(
                self.fixtures, self.plan, obligation, attempt_id=name, mode=mode))["accepted"])

    def finalized(self, store):
        started = self.start(store)
        self.records(store)
        terminal = store.finalize("run-1")
        return started, terminal

    def test_start_requires_trusted_digest_and_replay_is_unchanged(self):
        with self.assertRaisesRegex(EvidenceError, "^BINDING_NOT_ADMITTED$"):
            with RunEvidenceStore(self.path, clock=self.clock, allowed_bindings={"run-1": "f" * 64}) as rejected:
                rejected.start_run(self.bound, self.profile)
        with self.open() as store:
            first = self.start(store)
            replay = self.start(store)
            self.assertEqual(first["bundle_digest"], replay["bundle_digest"])
            self.assertFalse(first["ci_eligible"])
            self.assertFalse(first["authority_connected"])

    def test_attempt_same_content_is_idempotent_and_conflict_holds(self):
        with self.open() as store:
            self.start(store)
            attempt = attempt_for(self.fixtures, self.plan, "obligation-constraint", attempt_id="attempt-constraint", mode="constraint")
            self.assertFalse(store.record_attempt(attempt)["duplicate"])
            replay = store.record_attempt(deepcopy(attempt))
            self.assertTrue(replay["duplicate"])
            changed = deepcopy(attempt)
            changed["result"]["observation"] = "FAIL"
            conflict = store.record_attempt(changed)
            self.assertFalse(conflict["accepted"])
            self.assertEqual(conflict["reason"], "ATTEMPT_CONFLICT")
            self.assertEqual(store.get_run("run-1")["state"], "HOLD")
            self.assertEqual(store.get_attempt("attempt-constraint")["attempt"]["result"]["observation"], "PASS")

    def test_aggregate_and_finalize_recompute_without_caller_summary(self):
        with self.open() as store:
            self.start(store)
            self.records(store)
            aggregate = store.aggregate("run-1")
            self.assertEqual(aggregate["run_id"], "run-1")
            self.assertTrue(aggregate["metrics"])
            terminal = store.finalize("run-1")
            self.assertTrue(terminal["diagnostic_finalized"])
            self.assertFalse(terminal["authority_connected"])
            self.assertFalse(terminal["resource_closure_verified"])
            self.assertFalse(terminal["ci_eligible"])
            self.assertEqual(terminal, store.get_terminal("run-1"))
            self.assertEqual(terminal, store.finalize("run-1"))

    def test_finalization_does_not_accept_self_report_and_late_attempt_keeps_receipt(self):
        with self.open() as store:
            self.start(store)
            self.records(store)
            terminal = store.finalize("run-1")
            late = attempt_for(self.fixtures, self.plan, "obligation-constraint", attempt_id="attempt-late", mode="constraint")
            receipt = store.record_attempt(late)
            self.assertFalse(receipt["accepted"])
            self.assertEqual(store.get_terminal("run-1"), terminal)
            self.assertEqual(store.get_run("run-1")["state"], "HOLD")

    def test_current_use_is_review_only_and_evidence_expiry_is_not_success(self):
        with self.open() as store:
            started = self.start(store)
            self.records(store)
            store.finalize("run-1")
            store.record_evidence_state("run-1", {"state": "VALID", "valid_until": 200, "revocation_generation": 0})
            use = store.current_use("run-1", started["binding"])
            self.assertFalse(use["ready_for_authority_review"])
            self.assertFalse(use["use"])
            self.assertFalse(use["ci_eligible"])
            self.assertIn("EVIDENCE_UNVERIFIED", use["reasons"])
            self.assertIn("AUTHORITY_NOT_CONNECTED", use["reasons"])
            self.clock.value = 201
            expired = store.current_use("run-1", started["binding"])
            self.assertFalse(expired["ready_for_authority_review"])
            self.assertIn("EVIDENCE_NOT_CURRENT", expired["reasons"])

    def test_evidence_state_replay_is_immutable_and_revocation_is_irreversible(self):
        with self.open() as store:
            self.start(store)
            valid = {"state": "VALID", "valid_until": 200, "revocation_generation": 0}
            first = store.record_evidence_state("run-1", valid)
            self.clock.value = 101
            replay = store.record_evidence_state("run-1", deepcopy(valid))
            self.assertEqual(first["event_id"], replay["event_id"])
            self.assertEqual(first["checked_at"], replay["checked_at"])
            revoked = store.record_evidence_state(
                "run-1", {"state": "REVOKED", "valid_until": None, "revocation_generation": 1}
            )
            self.assertNotEqual(first["event_id"], revoked["event_id"])
            with self.assertRaisesRegex(EvidenceError, "^EVIDENCE_STATE_CONFLICT$"):
                store.record_evidence_state("run-1", valid)
            self.assertEqual(store.get_run("run-1")["state"], "OPEN")

    def test_evidence_deadline_cannot_be_extended_by_new_generation(self):
        with self.open() as store:
            self.start(store)
            store.record_evidence_state(
                "run-1", {"state": "VALID", "valid_until": 200, "revocation_generation": 0}
            )
            self.clock.value = 101
            with self.assertRaisesRegex(EvidenceError, "^EVIDENCE_STATE_CONFLICT$"):
                store.record_evidence_state(
                    "run-1", {"state": "VALID", "valid_until": 300, "revocation_generation": 1}
                )
            # 同じ期限の世代通知は受け付けるが、最初のchecked_atを維持する。
            store.record_evidence_state(
                "run-1", {"state": "VALID", "valid_until": 200, "revocation_generation": 1}
            )
            self.clock.value = 100 + 86400 + 1
            self.assertIn("EVIDENCE_NOT_CURRENT", store.current_use("run-1", self.start(store)["binding"])["reasons"])

    def test_evidence_generation_is_not_compared_with_clock(self):
        with self.open() as store:
            started = self.start(store)
            store.record_evidence_state(
                "run-1", {"state": "VALID", "valid_until": 200, "revocation_generation": MAX_INTEGER}
            )
            use = store.current_use("run-1", started["binding"])
            self.assertIn("EVIDENCE_UNVERIFIED", use["reasons"])

    def test_first_valid_observation_with_null_deadline_is_retained(self):
        with self.open() as store:
            started = self.start(store)
            store.record_evidence_state(
                "run-1", {"state": "VALID", "valid_until": None, "revocation_generation": 0}
            )
            self.clock.value = 101
            store.record_evidence_state(
                "run-1", {"state": "VALID", "valid_until": 200, "revocation_generation": 1}
            )
            self.clock.value = 100 + 86400 + 1
            self.assertIn("EVIDENCE_NOT_CURRENT", store.current_use("run-1", started["binding"])["reasons"])

    def test_empty_attempt_aggregate_keeps_run_start_observed_at(self):
        with self.open() as store:
            started = self.start(store)
            store.aggregate("run-1")
            self.clock.value = 101
            use = store.current_use("run-1", started["binding"])
            self.assertIn("NOT_FINALIZED", use["reasons"])
            self.assertNotIn("STORAGE_CORRUPT", use["reasons"])

    def test_empty_attempt_finalization_remains_valid_after_clock_advance(self):
        with self.open() as store:
            started = self.start(store)
            self.clock.value = 101
            store.finalize("run-1")
            self.clock.value = 102
            use = store.current_use("run-1", started["binding"])
            self.assertNotIn("STORAGE_CORRUPT", use["reasons"])

    def test_current_aggregate_digest_must_have_a_row(self):
        with self.open() as store:
            started = self.start(store)
            self.records(store)
            store.aggregate("run-1")
            store._db.execute("UPDATE run_state SET aggregate_digest=? WHERE run_id='run-1'", ("f" * 64,))
            with self.assertRaisesRegex(EvidenceError, "^STORAGE_CORRUPT$"):
                store.current_use("run-1", started["binding"])

    def test_current_use_rechecks_attempt_payload(self):
        with self.open() as store:
            started = self.start(store)
            self.records(store)
            store._db.execute("UPDATE attempts SET attempt_json='{}' WHERE attempt_id='attempt-constraint'")
            with self.assertRaisesRegex(EvidenceError, "^STORAGE_CORRUPT$"):
                store.current_use("run-1", started["binding"])

    def test_current_use_rechecks_aggregate_payload(self):
        with self.open() as store:
            started = self.start(store)
            self.records(store)
            store.aggregate("run-1")
            store._db.execute("UPDATE aggregates SET aggregate_json='{}' WHERE run_id='run-1'")
            with self.assertRaisesRegex(EvidenceError, "^STORAGE_CORRUPT$"):
                store.current_use("run-1", started["binding"])

    def test_current_use_rechecks_decision_payload(self):
        with self.open() as store:
            started, _ = self.finalized(store)
            store._db.execute("UPDATE decisions SET decision_json='{}' WHERE run_id='run-1'")
            with self.assertRaisesRegex(EvidenceError, "^STORAGE_CORRUPT$"):
                store.current_use("run-1", started["binding"])

    def test_current_use_recomputes_logically_tampered_decision(self):
        with self.open() as store:
            started, terminal = self.finalized(store)
            decision_row = store._db.execute("SELECT * FROM decisions WHERE run_id='run-1'").fetchone()
            changed_decision = evidence_module._load(decision_row["decision_json"], decision_row["decision_digest"])
            changed_decision["assurance"] = "HEALTHY"
            decision_raw, decision_digest = evidence_module._pack(changed_decision)
            store._db.execute(
                "UPDATE decisions SET decision_json=?, decision_digest=? WHERE run_id='run-1'",
                (decision_raw, decision_digest),
            )
            store._db.execute("UPDATE run_state SET decision_digest=? WHERE run_id='run-1'", (decision_digest,))
            changed_terminal = deepcopy(terminal)
            changed_terminal["decision_digest"] = decision_digest
            changed_terminal["decision"] = changed_decision
            terminal_raw, terminal_digest = evidence_module._pack(changed_terminal)
            store._db.execute(
                "UPDATE terminals SET terminal_json=?, terminal_digest=? WHERE run_id='run-1'",
                (terminal_raw, terminal_digest),
            )
            with self.assertRaisesRegex(EvidenceError, "^STORAGE_CORRUPT$"):
                store.current_use("run-1", started["binding"])

    def test_current_use_rechecks_terminal_payload(self):
        with self.open() as store:
            started, _ = self.finalized(store)
            store._db.execute("UPDATE terminals SET terminal_json='{}' WHERE run_id='run-1'")
            with self.assertRaisesRegex(EvidenceError, "^STORAGE_CORRUPT$"):
                store.current_use("run-1", started["binding"])

    def test_current_use_rechecks_evidence_event_payload(self):
        with self.open() as store:
            started = self.start(store)
            store.record_evidence_state(
                "run-1", {"state": "VALID", "valid_until": 200, "revocation_generation": 0}
            )
            store._db.execute("UPDATE evidence_events SET event_json='{}' WHERE run_id='run-1'")
            with self.assertRaisesRegex(EvidenceError, "^STORAGE_CORRUPT$"):
                store.current_use("run-1", started["binding"])

    def test_current_use_rechecks_bound_bundle_payload(self):
        with self.open() as store:
            started = self.start(store)
            store._db.execute("UPDATE bound_runs SET bundle_json='{}' WHERE run_id='run-1'")
            with self.assertRaisesRegex(EvidenceError, "^STORAGE_CORRUPT$"):
                store.current_use("run-1", started["binding"])

    def test_current_use_rechecks_run_state_enum(self):
        with self.open() as store:
            started = self.start(store)
            store._db.execute("UPDATE run_state SET state='BROKEN' WHERE run_id='run-1'")
            with self.assertRaisesRegex(EvidenceError, "^STORAGE_CORRUPT$"):
                store.current_use("run-1", started["binding"])

    def test_current_use_rechecks_attempt_event_identity(self):
        with self.open() as store:
            started = self.start(store)
            attempt = attempt_for(self.fixtures, self.plan, "obligation-constraint",
                                  attempt_id="attempt-event", mode="constraint")
            store.record_attempt(attempt)
            changed = deepcopy(attempt)
            changed["result"]["observation"] = "FAIL"
            store.record_attempt(changed)
            store._db.execute("UPDATE attempt_events SET received_digest=? WHERE run_id='run-1'", ("0" * 64,))
            with self.assertRaisesRegex(EvidenceError, "^STORAGE_CORRUPT$"):
                store.current_use("run-1", started["binding"])

    def test_evidence_freshness_boundary_uses_store_clock(self):
        with self.open() as store:
            started = self.start(store)
            store.record_evidence_state(
                "run-1", {"state": "VALID", "valid_until": 2 * 86400, "revocation_generation": 0}
            )
            self.clock.value = 100 + 86400
            boundary = store.current_use("run-1", started["binding"])
            self.assertNotIn("EVIDENCE_NOT_CURRENT", boundary["reasons"])
            self.clock.value += 1
            stale = store.current_use("run-1", started["binding"])
            self.assertIn("EVIDENCE_NOT_CURRENT", stale["reasons"])

    def test_future_attempt_is_not_saved_and_holds_current_state(self):
        with self.open() as store:
            self.start(store)
            future = attempt_for(self.fixtures, self.plan, "obligation-constraint",
                                 attempt_id="attempt-future", mode="constraint")
            future["started_at"] = 101
            future["finished_at"] = 101
            future["result"]["binding"] = deepcopy(future["expected_binding"])
            receipt = store.record_attempt(future)
            self.assertFalse(receipt["accepted"])
            self.assertEqual(receipt["reason"], "FUTURE_ATTEMPT")
            with self.assertRaisesRegex(EvidenceError, "^ATTEMPT_NOT_FOUND$"):
                store.get_attempt("attempt-future")
            self.assertEqual(store.get_run("run-1")["state"], "HOLD")

    def test_result_binding_must_match_expected_binding(self):
        with self.open() as store:
            self.start(store)
            attempt = attempt_for(self.fixtures, self.plan, "obligation-constraint",
                                  attempt_id="attempt-binding", mode="constraint")
            attempt["result"]["binding"] = deepcopy(attempt["expected_binding"])
            attempt["result"]["binding"]["operation_id"] = "other-operation"
            with self.assertRaisesRegex(EvidenceError, "^BINDING_MISMATCH$"):
                store.record_attempt(attempt)

    def test_clock_rollback_and_corrupt_receipt_fail_closed(self):
        with self.open() as store:
            self.start(store)
            self.clock.value = 99
            with self.assertRaisesRegex(EvidenceError, "^CLOCK_ROLLBACK$"):
                store.aggregate("run-1")
            self.clock.value = 100
            self.records(store)
            store.finalize("run-1")
            store._db.execute("UPDATE terminals SET terminal_json='{}'")
            with self.assertRaisesRegex(EvidenceError, "^STORAGE_CORRUPT$"):
                store.get_terminal("run-1")

    def test_independent_connection_replays_immutable_receipt(self):
        first = self.open()
        try:
            started = self.start(first)
            self.records(first)
            terminal = first.finalize("run-1")
        finally:
            first.close()
        with self.open() as second:
            self.assertEqual(second.get_terminal("run-1"), terminal)
            self.assertEqual(second.get_run("run-1")["bundle_digest"], started["bundle_digest"])


if __name__ == "__main__":
    unittest.main()
