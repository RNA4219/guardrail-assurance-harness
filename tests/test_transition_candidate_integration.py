"""初回baseline後の旧条件回帰・候補run境界を実DBで検査する。"""

from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gah.adoption import AdoptionError
from gah.assurance_authority import fixed_profile
from gah.evaluation_authority import EvaluationExtension
from gah.normalized import normalize_generic
from gah.run_contracts import content_ref
from gah.wire import canonical_bytes


_HELPER_SPEC = importlib.util.spec_from_file_location(
    "baseline_candidate_helpers", ROOT / "tests" / "test_baseline_adoption_integration.py"
)
assert _HELPER_SPEC is not None and _HELPER_SPEC.loader is not None
_HELPERS = importlib.util.module_from_spec(_HELPER_SPEC)
_HELPER_SPEC.loader.exec_module(_HELPERS)


def request(action, request_id, **fields):
    return {"schema_version": 1, "action": action, "request_id": request_id, **fields}


class TransitionCandidateIntegrationTests(unittest.TestCase):
    """固定workerを同一SQLiteへ接続する候補runの統合検査。"""

    def setUp(self):
        self.fixture = _HELPERS.BaselineAdoptionIntegrationTests("runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def _arrange(self, run_id="transition-candidate-run"):
        store = self.fixture.open()
        self.addCleanup(store.close)
        _, prepared, owner = self.fixture._prepare_contract_run(store, run_id)
        self.fixture._complete_all_entries(store, prepared, owner)
        self.fixture._adopt_baseline(store, run_id)
        baseline_view = store.dispatch(12004, 12004, request(
            "baseline_current", "candidate-baseline-current-" + run_id,
            series_id="fixture-baseline-series"))
        baseline = deepcopy(baseline_view["baseline"])
        old_contract = deepcopy(prepared["bound_run"]["contract"])
        old_contract_ref = content_ref("evaluation_contract", old_contract["contract_id"], old_contract)
        baseline_ref = content_ref("baseline", baseline["baseline_id"], baseline)
        next_contract = deepcopy(old_contract)
        next_contract["contract_id"] = "fixture-contract-candidate-" + run_id
        next_contract["generation"] = 2
        next_contract["comparison"] = {
            "mode": "required", "baseline_ref": deepcopy(baseline_ref),
            "reason": None, "changed_axes": [],
        }
        proposal_id = "candidate-transition-proposal-" + run_id
        store.dispatch(12001, 12001, request(
            "contract_propose", "candidate-transition-propose-" + run_id,
            proposal_id=proposal_id, series_id="fixture-contract-series",
            expected_generation=1, contract=deepcopy(next_contract)))
        preflight = store.dispatch(12003, 12003, request(
            "contract_preflight", "candidate-transition-preflight-" + run_id,
            proposal_id=proposal_id, baseline_series_id="fixture-baseline-series",
            expected_contract_ref=deepcopy(old_contract_ref),
            expected_baseline_ref=deepcopy(baseline_ref)))
        self.assertTrue(preflight["preflight_ready"])
        candidate_id = "candidate-run-" + run_id
        old_run_id = "candidate-old-" + run_id
        new_run_id = "candidate-new-" + run_id
        prepare_request = request(
            "contract_candidate_prepare", "candidate-prepare-" + run_id,
            proposal_id=proposal_id, baseline_series_id="fixture-baseline-series",
            expected_contract_ref=deepcopy(old_contract_ref),
            expected_baseline_ref=deepcopy(baseline_ref), candidate_id=candidate_id,
            old_run_id=old_run_id, new_run_id=new_run_id)
        prepared_candidate = store.dispatch(12003, 12003, prepare_request)
        return store, prepared, preflight, prepared_candidate, prepare_request, {
            "candidate_id": candidate_id, "old_run_id": old_run_id, "new_run_id": new_run_id,
            "proposal_id": proposal_id, "old_contract": old_contract,
            "old_contract_ref": old_contract_ref, "baseline": baseline, "baseline_ref": baseline_ref,
        }

    @staticmethod
    def _run_side(store, fixture, candidate, values, side):
        candidate_id = values["candidate_id"]
        begin = request("contract_candidate_begin", f"candidate-begin-{side}-{candidate_id}",
                        candidate_id=candidate_id, side=side)
        begun = store.dispatch(12004, 12004, begin)
        run_id = begun["run_id"]
        expected_run_id = values[side + "_run_id"]
        if run_id != expected_run_id:
            raise AssertionError((side, run_id, expected_run_id))
        snapshot = begun.get("resource_snapshot")
        if (type(snapshot) is not dict or snapshot.get("owner_id") != begin["request_id"]
                or snapshot.get("owner_epoch") != 1):
            raise AssertionError("candidate owner lease")

        store.dispatch(12004, 12004, request("evidence_open", f"candidate-open-{side}-{candidate_id}",
                                             run_id=run_id))
        bundle = candidate["runs"][side]
        bound = bundle["bound_run"]
        records = bundle["materialization"]["manifest"]["records"]
        entries = bound["plan"]["entries"]
        profile = fixed_profile()
        worker = fixture.worker
        owner = {"run_id": run_id, "owner_id": begin["request_id"], "owner_epoch": 1}
        attempts = []
        base = fixture.clock.value + 1
        for index, record in enumerate(records):
            entry = next(item for item in entries
                         if all(item[field] == record[field]
                                for field in ("obligation_id", "case_id", "trial_id"))
                         and item["variant"] == record["variant"])
            reserve_at = base + index * 4
            fixture.clock.value = reserve_at
            store.dispatch(12004, 12004, request(
                "resource_claim", f"candidate-claim-{side}-{index:02d}-{candidate_id}",
                run_id=run_id, owner_id=owner["owner_id"], recovery=False))
            operation_id = f"candidate-operation-{side}-{candidate_id}-{index:02d}"
            short_entry = {key: entry[key] for key in
                           ("obligation_id", "case_id", "trial_id", "variant")}
            store.dispatch(12004, 12004, request(
                "resource_reserve", f"candidate-reserve-{side}-{index:02d}-{candidate_id}",
                **owner, operation_id=operation_id, entry=short_entry,
                scenario=record["scenario"]))
            store.dispatch(12004, 12004, request(
                "resource_dispatch", f"candidate-dispatch-{side}-{index:02d}-{candidate_id}",
                **owner, operation_id=operation_id))
            binding = {
                "run_id": run_id, "operation_id": operation_id, "owner_epoch": 1,
                "contract_digest": bound["manifest"]["contract_ref"]["digest"],
                "target_digest": entry["target_ref"]["digest"],
                "obligation_id": entry["obligation_id"], "case_id": entry["case_id"],
                "trial_id": entry["trial_id"], "stage_id": entry["stage_ids"][0],
                "fixture_digest": profile["fixture_digest"],
                "adapter_digest": profile["adapter_digests"][0],
                "policy_digest": bound["manifest"]["policy_ref"]["digest"],
                "evaluator_digest": entry["evaluator_ref"]["digest"],
                "isolation_digest": profile["isolation_digest"],
            }
            kind, code, state = record["scenario"].split(":")
            observations = (worker._constraint_observation(code, state)
                            if kind == "constraint"
                            else worker._mutation_observation(code, state))
            raw = canonical_bytes({
                "schema_version": 1, "kind": "gah_generic_result", "binding": binding,
                "mode": kind, "observations": observations,
            })
            fixture.clock.value = reserve_at + 1
            normalized = normalize_generic(raw, binding, execution_status="COMPLETED",
                                           exit_code=0, stop_confirmed=True)
            attempt = {
                "schema_version": 1, "kind": "attempt_record",
                "attempt_id": f"candidate-attempt-{side}-{candidate_id}-{index:02d}",
                "variant": record["variant"], "retry_of": None,
                "started_at": reserve_at, "finished_at": reserve_at + 1,
                "stop_confirmed": True, "execution_status": "COMPLETED",
                "state_restored": True, "expected_binding": binding, "result": normalized,
            }
            store.dispatch(12003, 12003, request(
                "resource_observe", f"candidate-observe-{side}-{index:02d}-{candidate_id}",
                run_id=run_id, operation_id=operation_id,
                event_id=f"candidate-event-{side}-{candidate_id}-{index:02d}",
                stopped=True, usage={"input_tokens": 0, "output_tokens": 0, "cost_usd": "0"}))
            fixture.clock.value = reserve_at + 2
            recorded = store.dispatch(12003, 12003, request(
                "evidence_record", f"candidate-record-{side}-{index:02d}-{candidate_id}",
                run_id=run_id, attempt=attempt))
            if recorded.get("accepted") is not True:
                raise AssertionError((side, index, recorded))
            attempts.append(attempt)
        fixture.clock.value = base + len(records) * 4 + 1
        closed = store.dispatch(12004, 12004, request(
            "resource_close", f"candidate-close-{side}-{candidate_id}", **owner))
        if closed.get("budget_closure") is not True:
            raise AssertionError((side, closed))
        fixture.clock.value += 1
        finalized = store.dispatch(12004, 12004, request(
            "evidence_finalize", f"candidate-finalize-{side}-{candidate_id}", run_id=run_id))
        if finalized.get("assurance") != "HEALTHY" or finalized.get("input_materialization_verified") is not True:
            raise AssertionError((side, finalized))
        if finalized.get("ci_eligible") is not False:
            raise AssertionError((side, "ci eligibility"))
        return begun, finalized, len(records)

    def test_prepare_binds_old_and_new_materialization_exactly(self):
        store, _, _, candidate, _, values = self._arrange()
        self.assertEqual(set(candidate), {
            "schema_version", "kind", "action", "request_id", "ci_eligible",
            "candidate_id", "candidate_ref", "runs", "adoption_verified",
        })
        self.assertEqual(candidate["candidate_id"], values["candidate_id"])
        self.assertIsInstance(candidate["candidate_ref"], dict)
        self.assertFalse(candidate["adoption_verified"])
        self.assertFalse(candidate["ci_eligible"])
        runs = candidate["runs"]
        self.assertEqual(set(runs), {"transition", "old", "new",
                                     "structurally_bound", "authority_connected", "ci_eligible"})
        self.assertTrue(runs["structurally_bound"])
        self.assertFalse(runs["authority_connected"])
        self.assertFalse(runs["ci_eligible"])
        for side, count, purpose, baseline_context in (
                ("old", 15, "contract_old_regression", None),
                ("new", 30, "contract_candidate", "required")):
            with self.subTest(side=side):
                bundle = runs[side]
                bound = bundle["bound_run"]
                self.assertEqual(bound["manifest"]["purpose"], purpose)
                self.assertEqual(len(bound["plan"]["entries"]), count)
                self.assertEqual(len(bundle["materialization"]["manifest"]["records"]), count)
                if baseline_context is None:
                    self.assertIsNone(bundle.get("baseline_context"))
                else:
                    self.assertIsInstance(bundle.get("baseline_context"), dict)
                    self.assertEqual(bundle["baseline_context"]["baseline_ref"], values["baseline_ref"])
                    self.assertIsInstance(bundle["baseline_context"]["targets"], list)
                record_keys = {
                    (item["obligation_id"], item["case_id"], item["trial_id"], item["variant"])
                    for item in bundle["materialization"]["manifest"]["records"]
                }
                entry_keys = {
                    (item["obligation_id"], item["case_id"], item["trial_id"], item["variant"])
                    for item in bound["plan"]["entries"]
                }
                self.assertEqual(record_keys, entry_keys)

    def test_old_and_new_candidate_sides_complete_in_real_db(self):
        store, _, _, candidate, _, values = self._arrange("transition-complete")
        old_begun, old_final, old_count = self._run_side(store, self.fixture, candidate, values, "old")
        new_begun, new_final, new_count = self._run_side(store, self.fixture, candidate, values, "new")
        self.assertEqual(old_count, 15)
        self.assertEqual(new_count, 30)
        self.assertNotEqual(old_begun["run_id"], new_begun["run_id"])
        self.assertTrue(old_final["input_materialization_verified"])
        self.assertTrue(new_final["input_materialization_verified"])
        self.assertFalse(old_final["ci_eligible"])
        self.assertFalse(new_final["ci_eligible"])
        for run_id in (old_begun["run_id"], new_begun["run_id"]):
            row = store._db.execute(
                "SELECT closed_at FROM resource_runs WHERE run_id=?", (run_id,)).fetchone()
            self.assertIsNotNone(row)
            self.assertIsNotNone(row[0])

    def test_candidate_prepare_and_begin_have_separate_authority_roles(self):
        store, _, _, candidate, prepare_request, values = self._arrange("transition-roles")
        for uid in (12001, 12002, 12004):
            with self.subTest(action="prepare", uid=uid), self.assertRaises(AdoptionError):
                store.dispatch(uid, uid, {**prepare_request,
                                          "request_id": f"candidate-prepare-role-{uid}"})
        for uid in (12001, 12002, 12003):
            with self.subTest(action="begin", uid=uid), self.assertRaises(AdoptionError):
                store.dispatch(uid, uid, request(
                    "contract_candidate_begin", f"candidate-begin-role-{uid}",
                    candidate_id=values["candidate_id"], side="old"))
        for side in ("old", "new"):
            bound = candidate["runs"][side]["bound_run"]
            with self.subTest(side=side), self.assertRaises(AdoptionError):
                store.dispatch(12004, 12004, request(
                    "run_begin", f"normal-run-begin-{side}", manifest=deepcopy(bound["manifest"]),
                    plan=deepcopy(bound["plan"]), contract_series_id="fixture-contract-series"))

    def test_candidate_id_and_source_run_collisions_are_rejected(self):
        store, _, _, _, prepare_request, values = self._arrange("transition-collisions")
        collisions = (
            {"candidate_id": values["candidate_id"], "old_run_id": "another-old", "new_run_id": "another-new"},
            {"candidate_id": "another-candidate", "old_run_id": values["old_run_id"], "new_run_id": "another-new2"},
            {"candidate_id": "another-candidate2", "old_run_id": "another-old2", "new_run_id": values["new_run_id"]},
        )
        for index, replacement in enumerate(collisions):
            with self.subTest(index=index), self.assertRaises(AdoptionError):
                store.dispatch(12003, 12003, {
                    **prepare_request, "request_id": f"candidate-collision-{index}", **replacement,
                })

    def test_evidence_revoke_blocks_candidate_begin_and_keeps_old_history(self):
        store, prepared, _, _, _, values = self._arrange("transition-revoke")
        old_count = store._db.execute(
            "SELECT COUNT(*) FROM eval_adoptions WHERE series_id=? AND generation=1",
            ("fixture-contract-series",)).fetchone()[0]
        store.dispatch(12004, 12004, request(
            "evidence_revoke", "candidate-revoke-source-evidence", run_id=prepared["bound_run"]["manifest"]["run_id"]))
        with self.assertRaises(AdoptionError):
            store.dispatch(12004, 12004, request(
                "contract_candidate_begin", "candidate-begin-after-revoke",
                candidate_id=values["candidate_id"], side="new"))
        self.assertEqual(store._db.execute(
            "SELECT COUNT(*) FROM eval_adoptions WHERE series_id=? AND generation=1",
            ("fixture-contract-series",)).fetchone()[0], old_count)

    def test_evidence_revoke_after_begin_blocks_dispatch_but_allows_stop_and_save(self):
        store, prepared, _, candidate, _, values = self._arrange("transition-dispatch-revoke")
        begin = request("contract_candidate_begin", "candidate-begin-dispatch-revoke",
                        candidate_id=values["candidate_id"], side="old")
        begun = store.dispatch(12004, 12004, begin)
        run_id = begun["run_id"]
        store.dispatch(12004, 12004, request(
            "evidence_open", "candidate-open-dispatch-revoke", run_id=run_id))
        bundle = candidate["runs"]["old"]
        bound = bundle["bound_run"]
        record = bundle["materialization"]["manifest"]["records"][0]
        entry = next(item for item in bound["plan"]["entries"]
                     if all(item[field] == record[field]
                            for field in ("obligation_id", "case_id", "trial_id"))
                     and item["variant"] == record["variant"])
        operation_id = "candidate-operation-dispatch-revoke"
        self.fixture.clock.value += 1
        store.dispatch(12004, 12004, request(
            "resource_claim", "candidate-claim-dispatch-revoke", run_id=run_id,
            owner_id=begin["request_id"], recovery=False))
        store.dispatch(12004, 12004, request(
            "resource_reserve", "candidate-reserve-dispatch-revoke", run_id=run_id,
            operation_id=operation_id, owner_id=begin["request_id"], owner_epoch=1,
            entry={key: entry[key] for key in ("obligation_id", "case_id", "trial_id", "variant")},
            scenario=record["scenario"]))
        store.dispatch(12004, 12004, request(
            "resource_dispatch", "candidate-dispatch-before-revoke", run_id=run_id,
            operation_id=operation_id, owner_id=begin["request_id"], owner_epoch=1))
        record2 = bundle["materialization"]["manifest"]["records"][1]
        entry2 = next(item for item in bound["plan"]["entries"]
                      if all(item[field] == record2[field]
                             for field in ("obligation_id", "case_id", "trial_id"))
                      and item["variant"] == record2["variant"])
        operation_id2 = "candidate-operation-dispatch-revoke-next"
        store.dispatch(12004, 12004, request(
            "resource_reserve", "candidate-reserve-dispatch-revoke-next", run_id=run_id,
            operation_id=operation_id2, owner_id=begin["request_id"], owner_epoch=1,
            entry={key: entry2[key] for key in ("obligation_id", "case_id", "trial_id", "variant")},
            scenario=record2["scenario"]))
        store.dispatch(12004, 12004, request(
            "evidence_revoke", "candidate-revoke-after-begin",
            run_id=prepared["bound_run"]["manifest"]["run_id"]))
        with self.assertRaises(AdoptionError):
            store.dispatch(12004, 12004, request(
                "resource_dispatch", "candidate-dispatch-after-revoke-next",
                run_id=run_id, operation_id=operation_id2,
                owner_id=begin["request_id"], owner_epoch=1))
        attempt_started = self.fixture.clock.value
        self.fixture.clock.value = attempt_started + 1
        store.dispatch(12003, 12003, request(
            "resource_observe", "candidate-observe-after-revoke", run_id=run_id,
            operation_id=operation_id, event_id="candidate-event-after-revoke",
            stopped=True, usage={"input_tokens": 0, "output_tokens": 0, "cost_usd": "0"}))
        attempt_finished = self.fixture.clock.value
        profile = fixed_profile()
        binding = {
            "run_id": run_id, "operation_id": operation_id, "owner_epoch": 1,
            "contract_digest": bound["manifest"]["contract_ref"]["digest"],
            "target_digest": entry["target_ref"]["digest"],
            "obligation_id": entry["obligation_id"], "case_id": entry["case_id"],
            "trial_id": entry["trial_id"], "stage_id": entry["stage_ids"][0],
            "fixture_digest": profile["fixture_digest"],
            "adapter_digest": profile["adapter_digests"][0],
            "policy_digest": bound["manifest"]["policy_ref"]["digest"],
            "evaluator_digest": entry["evaluator_ref"]["digest"],
            "isolation_digest": profile["isolation_digest"],
        }
        kind, code, state = record["scenario"].split(":")
        observations = (self.fixture.worker._constraint_observation(code, state)
                        if kind == "constraint"
                        else self.fixture.worker._mutation_observation(code, state))
        raw = canonical_bytes({"schema_version": 1, "kind": "gah_generic_result",
                               "binding": binding, "mode": kind, "observations": observations})
        normalized = normalize_generic(raw, binding, execution_status="COMPLETED", exit_code=0,
                                       stop_confirmed=True)
        self.fixture.clock.value = attempt_finished + 1
        saved = store.dispatch(12003, 12003, request(
            "evidence_record", "candidate-record-after-revoke", run_id=run_id,
            attempt={"schema_version": 1, "kind": "attempt_record",
                     "attempt_id": "candidate-attempt-after-revoke", "variant": record["variant"],
                     "retry_of": None, "started_at": attempt_started,
                     "finished_at": attempt_finished, "stop_confirmed": True,
                     "execution_status": "COMPLETED", "state_restored": True,
                     "expected_binding": binding, "result": normalized}))
        self.assertTrue(saved["accepted"])
        store.dispatch(12004, 12004, request(
            "resource_cancel", "candidate-cancel-after-revoke", run_id=run_id,
            owner_id=begin["request_id"], owner_epoch=1))

    def test_candidate_prepare_needs_fresh_source_and_begin_rejects_unknown_side(self):
        store, prepared, _, _, prepare_request, values = self._arrange("transition-precondition")
        store.dispatch(12004, 12004, request(
            "evidence_revoke", "candidate-precondition-revoke-source",
            run_id=prepared["bound_run"]["manifest"]["run_id"]))
        with self.assertRaises(AdoptionError):
            store.dispatch(12003, 12003, {
                **prepare_request, "request_id": "candidate-prepare-after-source-revoke",
            })
        with self.assertRaises(AdoptionError):
            store.dispatch(12004, 12004, request(
                "contract_candidate_begin", "candidate-begin-unknown-side",
                candidate_id=values["candidate_id"], side="other"))

    def test_candidate_begin_replay_does_not_duplicate_run_or_owner_lease(self):
        store, _, _, _, _, values = self._arrange("transition-replay")
        begin = request("contract_candidate_begin", "candidate-begin-replay", 
                        candidate_id=values["candidate_id"], side="old")
        first = store.dispatch(12004, 12004, begin)
        before = store._db.execute(
            "SELECT COUNT(*) FROM resource_runs WHERE run_id=?", (values["old_run_id"],)).fetchone()[0]
        second = store.dispatch(12004, 12004, begin)
        after = store._db.execute(
            "SELECT COUNT(*) FROM resource_runs WHERE run_id=?", (values["old_run_id"],)).fetchone()[0]
        self.assertEqual(first, second)
        self.assertEqual(before, 1)
        self.assertEqual(after, 1)

    def test_prepare_and_begin_trigger_failures_roll_back_all_reservations(self):
        store, _, _, _, prepare_request, values = self._arrange("transition-rollback")
        failed_candidate = "candidate-rollback"
        failed_prepare = {**prepare_request, "request_id": "candidate-prepare-trigger-failure",
                          "candidate_id": failed_candidate, "old_run_id": "rollback-old",
                          "new_run_id": "rollback-new"}
        store._db.execute("""
            CREATE TRIGGER fail_transition_run
            AFTER INSERT ON transition_runs
            BEGIN SELECT RAISE(ABORT, 'forced transition reservation failure'); END
        """)
        store._db.commit()
        with self.assertRaises(AdoptionError):
            store.dispatch(12003, 12003, failed_prepare)
        self.assertEqual(store._db.execute(
            "SELECT COUNT(*) FROM transition_candidates WHERE candidate_id=?",
            (failed_candidate,)).fetchone()[0], 0)
        self.assertEqual(store._db.execute(
            "SELECT COUNT(*) FROM transition_runs WHERE candidate_id=?",
            (failed_candidate,)).fetchone()[0], 0)
        self.assertEqual(store._db.execute(
            "SELECT COUNT(*) FROM eval_runs WHERE run_id IN (?,?)",
            ("rollback-old", "rollback-new")).fetchone()[0], 0)
        self.assertIsNone(store._db.execute(
            "SELECT 1 FROM idempotency WHERE request_id=?",
            (failed_prepare["request_id"],)).fetchone())
        store._db.execute("DROP TRIGGER fail_transition_run")
        store._db.commit()

        begin = request("contract_candidate_begin", "candidate-begin-trigger-failure",
                        candidate_id=values["candidate_id"], side="old")
        store._db.execute("""
            CREATE TRIGGER fail_candidate_eval_run
            AFTER INSERT ON eval_runs
            BEGIN SELECT RAISE(ABORT, 'forced candidate begin failure'); END
        """)
        store._db.commit()
        with self.assertRaises(AdoptionError):
            store.dispatch(12004, 12004, begin)
        self.assertEqual(store._db.execute(
            "SELECT COUNT(*) FROM eval_runs WHERE run_id=?",
            (values["old_run_id"],)).fetchone()[0], 0)
        self.assertEqual(store._db.execute(
            "SELECT COUNT(*) FROM resource_runs WHERE run_id=?",
            (values["old_run_id"],)).fetchone()[0], 0)
        self.assertIsNone(store._db.execute(
            "SELECT 1 FROM idempotency WHERE request_id=?",
            (begin["request_id"],)).fetchone())
        store._db.execute("DROP TRIGGER fail_candidate_eval_run")
        store._db.commit()
        retried = store.dispatch(12004, 12004, begin)
        self.assertEqual(retried["run_id"], values["old_run_id"])

    def test_reserved_candidate_run_cannot_be_reinterpreted_as_normal_run(self):
        store, _, _, _, _, values = self._arrange("transition-purpose-tamper")
        begin = request("contract_candidate_begin", "candidate-begin-purpose-tamper",
                        candidate_id=values["candidate_id"], side="old")
        begun = store.dispatch(12004, 12004, begin)
        row = store._db.execute(
            "SELECT manifest_json FROM eval_runs WHERE run_id=?",
            (begun["run_id"],)).fetchone()
        self.assertIsNotNone(row)
        manifest = json.loads(row["manifest_json"])
        manifest["purpose"] = "diagnostic"
        raw = canonical_bytes(manifest)
        store._db.execute(
            "UPDATE eval_runs SET manifest_json=?, manifest_digest=? WHERE run_id=?",
            (raw.decode("utf-8"), hashlib.sha256(raw).hexdigest(), begun["run_id"]))
        store._db.commit()
        with self.assertRaises(AdoptionError):
            store.dispatch(12004, 12004, request(
                "evidence_open", "candidate-open-purpose-tamper", run_id=begun["run_id"]))
        self.assertIsNotNone(store._db.execute(
            "SELECT 1 FROM transition_runs WHERE run_id=? AND side='old'",
            (begun["run_id"],)).fetchone())

    def test_fixture_and_candidate_run_reservations_cannot_cross_claim_each_other(self):
        store, _, _, _, prepare_request, values = self._arrange("transition-reservation-collision")

        def state():
            return tuple(store._db.execute(
                "SELECT (SELECT COUNT(*) FROM transition_candidates),"
                "(SELECT COUNT(*) FROM transition_runs),"
                "(SELECT COUNT(*) FROM eval_runs),"
                "(SELECT COUNT(*) FROM resource_runs),"
                "(SELECT COUNT(*) FROM fixture_admissions)").fetchone())

        before_fixture_claim = state()
        with self.assertRaisesRegex(AdoptionError, "^RUN_CONFLICT$"):
            store.dispatch(12001, 12001, request(
                "fixture_prepare", "fixture-prepare-candidate-reserved-id",
                run_id=values["old_run_id"], policy_series_id=self.fixture.policy["policy_id"]))
        self.assertEqual(state(), before_fixture_claim)

        free_fixture_run = "fixture-reservation-source"
        store.dispatch(12001, 12001, request(
            "fixture_prepare", "fixture-prepare-reservation-source",
            run_id=free_fixture_run, policy_series_id=self.fixture.policy["policy_id"]))
        before_candidate_claim = state()
        colliding_prepare = {
            **prepare_request,
            "request_id": "candidate-prepare-fixture-reserved-id",
            "candidate_id": "candidate-fixture-reservation-collision",
            "old_run_id": free_fixture_run,
            "new_run_id": "candidate-free-new-run",
        }
        with self.assertRaisesRegex(AdoptionError, "^RUN_CONFLICT$"):
            store.dispatch(12003, 12003, colliding_prepare)
        self.assertEqual(state(), before_candidate_claim)

    def test_saved_proposal_or_expected_reference_tamper_is_rejected(self):
        store, _, _, _, prepare_request, values = self._arrange("transition-tamper")
        store._db.execute("UPDATE eval_proposals SET payload_json=? WHERE id=?",
                          ('{"tampered":true}', values["proposal_id"]))
        store._db.commit()
        with self.assertRaises(AdoptionError):
            store.dispatch(12003, 12003, {**prepare_request,
                                          "request_id": "candidate-prepare-tampered-proposal"})

    def test_expected_reference_tamper_is_rejected(self):
        store, _, _, _, prepare_request, _ = self._arrange("transition-ref-tamper")
        wrong = deepcopy(prepare_request["expected_baseline_ref"])
        wrong["digest"] = "0" * 64
        with self.assertRaises(AdoptionError):
            store.dispatch(12003, 12003, {
                **prepare_request, "request_id": "candidate-prepare-tampered-ref",
                "expected_baseline_ref": wrong,
            })


if __name__ == "__main__":
    unittest.main()
