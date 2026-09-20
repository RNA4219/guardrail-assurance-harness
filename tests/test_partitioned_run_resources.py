"""v6 diagnostic run resource lifecycle stays on the existing resource ledger."""
from copy import deepcopy
from pathlib import Path
import hashlib
import json
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from gah.adoption import AdoptionError
from gah.assurance_authority import fixed_profile
from gah.partitioned_run_authority import PartitionedRunEvaluationExtension
from gah.resources import ResourceBook, ResourceError
from gah.run_contracts import content_ref
from gah.wire import canonical_bytes
from tests.test_fixture_admission import request


class PartitionedRunResourceTests(unittest.TestCase):
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
        self.call(12001, "plan_partition_commit", "resource-plan-commit", upload_id="upload")
        self.run_id = "partition-resource-run"
        self.manifest = deepcopy(self.helper.bound["manifest"])
        self.manifest.update(schema_version=2, run_id=self.run_id, purpose="diagnostic",
                             baseline_ref=None, plan_ref=deepcopy(self.helper.plan_ref))
        self.profile = fixed_profile()
        self.begin_request = request("run_begin_v2", "resource-owner", manifest=self.manifest,
            contract_series_id="partition-series", expected_generation=1, execution_profile=self.profile)

    def call(self, uid, action, request_id, **fields):
        return self.store.dispatch(uid, uid, request(action, request_id, **fields))

    def begin(self, request_value=None):
        return self.store.dispatch(12004, 12004, deepcopy(request_value or self.begin_request))

    def _scenario_entry(self, scenario="constraint:C01:good"):
        from gah.resource_authority import _lock
        worker_digest = _lock()["worker_digest"]
        target_digest = hashlib.sha256(canonical_bytes({
            "worker_digest": worker_digest, "scenario": scenario,
        })).hexdigest()
        matches = [entry for entry in self.helper.bound["plan"]["entries"]
                   if entry["target_ref"]["digest"] == target_digest
                   and entry["evaluator_ref"]["digest"] == worker_digest
                   and len(entry["stage_ids"]) == 1]
        self.assertTrue(matches, "fixed adopted fixture plan has the requested fixed scenario")
        entry = matches[0]
        return scenario, {key: entry[key] for key in (
            "obligation_id", "case_id", "trial_id", "variant",
        )}

    def _snapshot(self):
        db = self.store._db
        names = [row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return {name: sorted((tuple(row) for row in db.execute('SELECT * FROM "' + name + '"')), key=repr)
                for name in names}

    def _resource_domain_snapshot(self):
        db = self.store._db
        names = [row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
            if row[0] not in {"adoption_meta", "resource_meta", "idempotency"}]
        return {name: sorted((tuple(row) for row in db.execute('SELECT * FROM "' + name + '"')), key=repr)
                for name in names}

    def test_begin_creates_resource_row_and_fixed_operation_settles_and_closes(self):
        begun = self.begin()
        self.assertFalse(begun["ci_eligible"])
        self.assertFalse(begun["resource_closure_verified"])
        self.assertFalse(begun["adoption_verified"])
        # 台帳のbudget closureは未予約ならtrueだが、診断のresource closure証拠はfalse。
        self.assertTrue(begun["resource_snapshot"]["budget_closure"])
        self.assertEqual(self.store._db.execute(
            "SELECT COUNT(*) FROM resource_runs WHERE run_id=?", (self.run_id,)).fetchone()[0], 1)
        scenario, entry = self._scenario_entry()
        owner_id = begun["resource_snapshot"]["owner_id"]
        expected_manifest = content_ref("run_manifest", self.run_id, self.manifest)
        started = self.call(12004, "resource_start", "resource-start", run_id=self.run_id,
            owner_id=owner_id, operation_id="resource-op-1", entry=entry, scenario=scenario,
            expected_manifest_ref=expected_manifest)
        self.assertTrue(started["operation"]["dispatch_intended"])
        self.assertFalse(started["ci_eligible"])
        self.assertFalse(started["authority_connected"])
        self.assertFalse(started["resource_closure_verified"])
        self.assertFalse(started["baseline_freshness_verified"])
        self.assertFalse(started["adoption_verified"])
        self.assertFalse(started["admission_verified"])
        observed = self.call(12003, "resource_observe", "resource-stop-settle", run_id=self.run_id,
            operation_id="resource-op-1", event_id="resource-stop-event", stopped=True,
            usage={"input_tokens": 0, "output_tokens": 0, "cost_usd": "0"})
        for flag in ("authority_connected", "resource_closure_verified", "baseline_freshness_verified",
                     "adoption_verified", "admission_verified", "ci_eligible"):
            self.assertFalse(observed[flag])
        closed = self.call(12004, "resource_close", "resource-close", run_id=self.run_id,
            owner_id=owner_id, owner_epoch=started["owner_epoch"])
        self.assertTrue(closed["budget_closure"])
        for flag in ("authority_connected", "ci_eligible", "resource_closure_verified",
                     "baseline_freshness_verified", "adoption_verified", "admission_verified"):
            self.assertFalse(closed[flag])
        run = self.store._db.execute("SELECT * FROM resource_runs WHERE run_id=?", (self.run_id,)).fetchone()
        operation = self.store._db.execute(
            "SELECT * FROM resource_operations WHERE operation_id=?", ("resource-op-1",)).fetchone()
        self.assertIsNotNone(run["closed_at"])
        self.assertIsNotNone(operation["intended_at"])
        self.assertIsNotNone(operation["stopped_at"])
        self.assertIsNotNone(operation["settled_at"])
        self.assertEqual(operation["released"], 0)

    def test_duplicate_begin_requires_existing_matching_resource_row(self):
        first = self.begin()
        replay = deepcopy(self.begin_request)
        replay["request_id"] = "resource-owner-recheck"
        second = self.begin(replay)
        self.assertTrue(second["duplicate"])
        self.assertEqual(second["resource_snapshot"]["owner_id"], first["resource_snapshot"]["owner_id"])
        self.assertEqual(self.store._db.execute(
            "SELECT COUNT(*) FROM resource_runs WHERE run_id=?", (self.run_id,)).fetchone()[0], 1)
        self.store._db.execute("DELETE FROM resource_runs WHERE run_id=?", (self.run_id,))
        with self.assertRaisesRegex(AdoptionError, "^STORAGE_CORRUPT$"):
            self.begin(request("run_begin_v2", "resource-owner-missing-row", manifest=self.manifest,
                contract_series_id="partition-series", expected_generation=1, execution_profile=self.profile))
        self.assertEqual(self.store._db.execute(
            "SELECT COUNT(*) FROM resource_runs WHERE run_id=?", (self.run_id,)).fetchone()[0], 0)

    def test_resource_create_failure_rolls_back_route_and_evidence_rows(self):
        before = self._snapshot()
        original = ResourceBook.create_run
        def fail_after_insert(book, *args, **kwargs):
            original(book, *args, **kwargs)
            raise ResourceError("INJECTED_RESOURCE_CREATE_FAILURE")
        with patch.object(ResourceBook, "create_run", fail_after_insert):
            with self.assertRaisesRegex(AdoptionError, "^INJECTED_RESOURCE_CREATE_FAILURE$"):
                self.begin()
        self.assertEqual(self._snapshot(), before)
        self.assertEqual(self.store._db.execute(
            "SELECT COUNT(*) FROM eval_runs_v2 WHERE run_id=?", (self.run_id,)).fetchone()[0], 0)
        self.assertEqual(self.store._db.execute(
            "SELECT COUNT(*) FROM resource_runs WHERE run_id=?", (self.run_id,)).fetchone()[0], 0)

    def test_v2_rejects_inherited_v1_evidence_operation_read_and_terminal_paths(self):
        self.begin()
        bad_requests = [
            request("evidence_open", "legacy-evidence-open", run_id=self.run_id),
            request("run_status", "legacy-run-status", run_id=self.run_id),
        ]
        before = self._snapshot()
        for item in bad_requests:
            with self.subTest(action=item["action"]), self.assertRaisesRegex(AdoptionError, "^SCOPE_UNSUPPORTED$"):
                self.store.dispatch(12004, 12004, item)
        self.assertEqual(self._snapshot(), before)

    def test_resource_operation_reads_saved_v2_entry_without_domain_writes(self):
        # A manifest may be prepared before the authority accepts the run.
        self.helper.clock.value = max(self.helper.clock.value, self.manifest["created_at"]) + 1
        begun = self.begin()
        saved_created_at = self.store._db.execute(
            "SELECT created_at FROM eval_runs_v2 WHERE run_id=?", (self.run_id,)).fetchone()[0]
        self.assertGreater(saved_created_at, self.manifest["created_at"])
        scenario, entry_fields = self._scenario_entry()
        started = self.call(12004, "resource_start", "resource-read-start", run_id=self.run_id,
            owner_id=begun["resource_snapshot"]["owner_id"], operation_id="resource-op-read",
            entry=entry_fields, scenario=scenario,
            expected_manifest_ref=content_ref("run_manifest", self.run_id, self.manifest))
        before = self._resource_domain_snapshot()
        denied = request("resource_operation", "resource-operation-read-role-denied", run_id=self.run_id,
            operation_id="resource-op-read", expected_manifest_ref=content_ref("run_manifest", self.run_id, self.manifest))
        with self.assertRaisesRegex(AdoptionError, "^AUTHORITY_DENIED$"):
            self.store.dispatch(12001, 12001, denied)
        self.assertEqual(self._resource_domain_snapshot(), before)
        value = self.call(12003, "resource_operation", "resource-operation-read-v2", run_id=self.run_id,
            operation_id="resource-op-read", expected_manifest_ref=content_ref("run_manifest", self.run_id, self.manifest))
        self.assertEqual(value["kind"], "partitioned_run_authority_result")
        self.assertEqual(value["entry"]["trial_id"], entry_fields["trial_id"])
        self.assertEqual(value["scenario"], scenario)
        self.assertEqual(value["owner_epoch"], started["owner_epoch"])
        self.assertTrue(value["dispatch_intended"])
        self.assertFalse(value["stopped"])
        self.assertFalse(value["settled"])
        for flag in ("authority_connected", "resource_closure_verified", "baseline_freshness_verified",
                     "adoption_verified", "admission_verified", "ci_eligible"):
            self.assertFalse(value[flag])
        self.assertEqual(self._resource_domain_snapshot(), before)

    def test_resource_operation_historical_read_survives_expiry_and_currentness_changes_but_start_does_not(self):
        begun = self.begin()
        scenario, entry_fields = self._scenario_entry()
        started = self.call(12004, "resource_start", "resource-historical-read-start", run_id=self.run_id,
            owner_id=begun["resource_snapshot"]["owner_id"], operation_id="resource-op-historical",
            entry=entry_fields, scenario=scenario,
            expected_manifest_ref=content_ref("run_manifest", self.run_id, self.manifest))
        self.call(12004, "revoke_actor", "resource-historical-revoke-manager", actor_id="manager")
        self.store._db.execute("UPDATE eval_current SET generation=2 WHERE series_id=?", ("partition-series",))
        self.call(12004, "resource_cancel", "resource-historical-cancel", run_id=self.run_id,
            owner_id=begun["resource_snapshot"]["owner_id"], owner_epoch=started["owner_epoch"])
        self.helper.clock.value = self.manifest["deadline"]
        before = self._resource_domain_snapshot()
        value = self.call(12004, "resource_operation", "resource-historical-read-stale", run_id=self.run_id,
            operation_id="resource-op-historical", expected_manifest_ref=content_ref("run_manifest", self.run_id, self.manifest))
        self.assertTrue(value["dispatch_intended"])
        self.assertFalse(value["ci_eligible"])
        self.assertEqual(self.store._db.execute(
            "SELECT cancelled FROM resource_runs WHERE run_id=?", (self.run_id,)).fetchone()[0], 1)
        self.assertEqual(self._resource_domain_snapshot(), before)
        start = {"run_id": self.run_id, "owner_id": begun["resource_snapshot"]["owner_id"],
            "operation_id": "resource-op-after-expiry", "entry": entry_fields, "scenario": scenario,
            "expected_manifest_ref": content_ref("run_manifest", self.run_id, self.manifest)}
        with self.assertRaises(AdoptionError):
            self.store.dispatch(12004, 12004, request("resource_start", "resource-start-after-expiry", **start))
        self.assertEqual(self._resource_domain_snapshot(), before)

    def test_resource_operation_rejects_missing_and_corrupt_saved_bindings(self):
        begun = self.begin()
        scenario, entry_fields = self._scenario_entry()
        self.call(12004, "resource_start", "resource-corrupt-read-start", run_id=self.run_id,
            owner_id=begun["resource_snapshot"]["owner_id"], operation_id="resource-op-corrupt-read",
            entry=entry_fields, scenario=scenario,
            expected_manifest_ref=content_ref("run_manifest", self.run_id, self.manifest))
        db = self.store._db
        request_value = request("resource_operation", "resource-corrupt-read", run_id=self.run_id,
            operation_id="resource-op-corrupt-read", expected_manifest_ref=content_ref("run_manifest", self.run_id, self.manifest))
        corruptions = [
            ("resource_bindings", "operation_id", "resource-op-corrupt-read", "DELETE FROM resource_bindings WHERE operation_id=?"),
            ("partition_plan_commits", "plan_id", self.helper.plan_ref["id"], "UPDATE partition_plan_commits SET index_json='{}' WHERE plan_id=?"),
            ("partition_plan_segments", "plan_id", self.helper.plan_ref["id"], "UPDATE partition_plan_segments SET segment_json='{}' WHERE plan_id=? AND segment_index=?"),
            ("eval_runs_v2", "run_id", self.run_id, "UPDATE eval_runs_v2 SET manifest_json='{}' WHERE run_id=?"),
            ("bound_runs", "run_id", self.run_id, "UPDATE bound_runs SET bundle_json='{}' WHERE run_id=?"),
        ]
        for index, (table, key, value, mutation) in enumerate(corruptions):
            where = f"{key}=?" + (" AND segment_index=?" if table == "partition_plan_segments" else "")
            key_args = (value, 0) if table == "partition_plan_segments" else (value,)
            row = db.execute(f'SELECT * FROM "{table}" WHERE {where}', key_args).fetchone()
            self.assertIsNotNone(row)
            saved = tuple(row)
            db.execute(mutation, key_args)
            request_value["request_id"] = f"resource-corrupt-read-{index}"
            before_failure = self._snapshot()
            with self.subTest(table=table), self.assertRaisesRegex(
                    AdoptionError, "^(STORAGE_CORRUPT|BINDING_MISMATCH|PLAN_MISSING|PLAN_STALE)$"):
                self.store.dispatch(12004, 12004, request_value)
            self.assertEqual(self._snapshot(), before_failure)
            column_names = [column[1] for column in db.execute(f'PRAGMA table_info("{table}")')]
            quoted = ",".join(f'"{column}"' for column in column_names)
            placeholders = ",".join("?" for _ in column_names)
            if table == "resource_bindings":
                db.execute(f'INSERT INTO "{table}" ({quoted}) VALUES ({placeholders})', saved)
            else:
                assignments = ",".join(f'"{column}"=?' for column in column_names)
                db.execute(f'UPDATE "{table}" SET {assignments} WHERE {where}', saved + key_args)

    def test_v2_resource_cancel_role_denial_rolls_back(self):
        begun = self.begin()
        value = request("resource_cancel", "resource-cancel-role-denied", run_id=self.run_id,
            owner_id=begun["resource_snapshot"]["owner_id"], owner_epoch=begun["resource_snapshot"]["owner_epoch"])
        before = self._snapshot()
        with self.assertRaisesRegex(AdoptionError, "^AUTHORITY_DENIED$"):
            self.store.dispatch(12003, 12003, value)
        self.assertEqual(self._snapshot(), before)

    def test_v2_cancel_releases_only_undispatched_reservation(self):
        begun = self.begin()
        scenario, entry = self._scenario_entry()
        owner = begun["resource_snapshot"]
        self.call(12004, "resource_reserve", "resource-reserve-before-cancel", run_id=self.run_id,
            owner_id=owner["owner_id"], owner_epoch=owner["owner_epoch"], operation_id="resource-op-reserved",
            entry=entry, scenario=scenario)
        cancelled = self.call(12004, "resource_cancel", "resource-cancel-reserved", run_id=self.run_id,
            owner_id=owner["owner_id"], owner_epoch=owner["owner_epoch"])
        self.assertTrue(cancelled["cancelled"])
        operation = self.store._db.execute(
            "SELECT intended_at,released,stopped_at,settled_at FROM resource_operations WHERE operation_id=?",
            ("resource-op-reserved",)).fetchone()
        self.assertIsNone(operation["intended_at"])
        self.assertEqual(operation["released"], 1)
        self.assertIsNone(operation["stopped_at"])
        self.assertIsNone(operation["settled_at"])
        for flag in ("authority_connected", "resource_closure_verified", "adoption_verified", "ci_eligible"):
            self.assertFalse(cancelled[flag])

    def test_v2_cancel_survives_stale_permission_and_contract_but_keeps_sent_operation_unsettled(self):
        begun = self.begin()
        scenario, entry = self._scenario_entry()
        started = self.call(12004, "resource_start", "resource-start-before-stale-cancel", run_id=self.run_id,
            owner_id=begun["resource_snapshot"]["owner_id"], operation_id="resource-op-stale-cancel",
            entry=entry, scenario=scenario,
            expected_manifest_ref=content_ref("run_manifest", self.run_id, self.manifest))
        # Change the shared permission generation and current contract marker without revoking this operator.
        self.call(12004, "revoke_actor", "resource-revoke-other-role", actor_id="manager")
        self.store._db.execute("UPDATE eval_current SET generation=2 WHERE series_id=?", ("partition-series",))
        before = self._snapshot()
        cancelled = self.call(12004, "resource_cancel", "resource-cancel-after-stale", run_id=self.run_id,
            owner_id=begun["resource_snapshot"]["owner_id"], owner_epoch=started["owner_epoch"])
        self.assertTrue(cancelled["cancelled"])
        operation = self.store._db.execute(
            "SELECT intended_at,released,stopped_at,settled_at FROM resource_operations WHERE operation_id=?",
            ("resource-op-stale-cancel",)).fetchone()
        self.assertIsNotNone(operation["intended_at"])
        self.assertEqual(operation["released"], 0)
        self.assertIsNone(operation["stopped_at"])
        self.assertIsNone(operation["settled_at"])
        with self.assertRaisesRegex(AdoptionError, "^CLOSE_DENIED$"):
            self.call(12004, "resource_close", "resource-close-cancel-unobserved", run_id=self.run_id,
                owner_id=begun["resource_snapshot"]["owner_id"], owner_epoch=started["owner_epoch"])
        self.assertNotEqual(self._snapshot(), before)

    def test_v2_cancel_claim_recovers_expired_owner_atomically_and_requires_observation_to_close(self):
        begun = self.begin()
        scenario, entry = self._scenario_entry()
        started = self.call(12004, "resource_start", "resource-start-expiring-cancel", run_id=self.run_id,
            owner_id=begun["resource_snapshot"]["owner_id"], operation_id="resource-op-expiring-cancel",
            entry=entry, scenario=scenario,
            expected_manifest_ref=content_ref("run_manifest", self.run_id, self.manifest))
        old_owner, old_epoch = begun["resource_snapshot"]["owner_id"], started["owner_epoch"]
        resource_run = self.store._db.execute(
            "SELECT lease_until FROM resource_runs WHERE run_id=?", (self.run_id,)).fetchone()
        recovery_now = max(resource_run["lease_until"], self.manifest["deadline"])
        self.helper.clock.value = recovery_now
        before_claim = self._snapshot()
        def fail_after_claim(book, *args, **kwargs):
            raise ResourceError("INJECTED_CANCEL_FAILURE")
        with patch.object(ResourceBook, "cancel", fail_after_claim):
            with self.assertRaisesRegex(AdoptionError, "^INJECTED_CANCEL_FAILURE$"):
                self.call(12004, "resource_cancel_claim", "resource-cancel-claim-rollback",
                    run_id=self.run_id, owner_id="resource-recovery-owner")
        self.assertEqual(self._snapshot(), before_claim)
        recovered = self.call(12004, "resource_cancel_claim", "resource-cancel-claim-expired",
            run_id=self.run_id, owner_id="resource-recovery-owner")
        self.assertTrue(recovered["cancelled"])
        self.assertEqual(recovered["owner_id"], "resource-recovery-owner")
        self.assertGreater(recovered["owner_epoch"], old_epoch)
        operation = self.store._db.execute(
            "SELECT released,stopped_at,settled_at FROM resource_operations WHERE operation_id=?",
            ("resource-op-expiring-cancel",)).fetchone()
        self.assertEqual(operation["released"], 0)
        self.assertIsNone(operation["stopped_at"])
        self.assertIsNone(operation["settled_at"])
        before_stale = self._snapshot()
        with self.assertRaisesRegex(AdoptionError, "^OWNER_STALE$"):
            self.call(12004, "resource_cancel", "resource-cancel-old-owner", run_id=self.run_id,
                owner_id=old_owner, owner_epoch=old_epoch)
        self.assertEqual(self._snapshot(), before_stale)
        with self.assertRaisesRegex(AdoptionError, "^CLOSE_DENIED$"):
            self.call(12004, "resource_close", "resource-close-before-validator", run_id=self.run_id,
                owner_id="resource-recovery-owner", owner_epoch=recovered["owner_epoch"])
        observed = self.call(12003, "resource_observe", "resource-observe-after-cancel", run_id=self.run_id,
            operation_id="resource-op-expiring-cancel", event_id="resource-stop-after-cancel", stopped=True,
            usage={"input_tokens": 0, "output_tokens": 0, "cost_usd": "0"})
        self.assertTrue(observed["accepted"])
        closed = self.call(12004, "resource_close", "resource-close-after-observation", run_id=self.run_id,
            owner_id="resource-recovery-owner", owner_epoch=recovered["owner_epoch"])
        self.assertTrue(closed["closed"])

    def _start_fixed(self, request_id="resource-start-check", *, scenario=None, entry=None):
        selected_scenario, selected_entry = self._scenario_entry()
        owner = self.store._db.execute(
            "SELECT owner_id FROM resource_runs WHERE run_id=?", (self.run_id,)
        ).fetchone()
        self.assertIsNotNone(owner, "run_begin_v2 has already created its resource row")
        value = {
            "run_id": self.run_id,
            "owner_id": owner["owner_id"],
            "operation_id": "resource-op-check",
            "entry": deepcopy(entry if entry is not None else selected_entry),
            "scenario": scenario if scenario is not None else selected_scenario,
            "expected_manifest_ref": content_ref("run_manifest", self.run_id, self.manifest),
        }
        return value

    def _assert_start_rejected_without_writes(self, value, expected_code=None):
        before = self._snapshot()
        with self.assertRaises(AdoptionError) as caught:
            self.store.dispatch(12004, 12004, request("resource_start", value.pop("request_id"), **value))
        if expected_code is not None:
            self.assertEqual(caught.exception.code, expected_code)
        self.assertEqual(self._snapshot(), before)

    def test_resource_start_rejects_corrupt_committed_plan_without_writes(self):
        self.begin()
        row = self.store._db.execute(
            "SELECT segment_index FROM partition_plan_segments WHERE plan_id=? ORDER BY segment_index LIMIT 1",
            (self.helper.index["plan_id"],),
        ).fetchone()
        self.assertIsNotNone(row)
        self.store._db.execute(
            "UPDATE partition_plan_segments SET segment_json=? WHERE plan_id=? AND segment_index=?",
            ('{"tampered":true}', self.helper.index["plan_id"], row["segment_index"]),
        )
        value = self._start_fixed()
        value["request_id"] = "resource-start-corrupt-plan"
        self._assert_start_rejected_without_writes(value)

    def test_resource_start_rejects_expired_permission_and_current_generation_without_writes(self):
        # Each invalidation is applied through the concrete store state; failed start must add no rows.
        self.begin()
        value = self._start_fixed()
        value["request_id"] = "resource-start-revoked-permission"
        self.call(12004, "revoke_actor", "resource-revoke-manager", actor_id="manager")
        before = self._snapshot()
        with self.assertRaises(AdoptionError):
            self.store.dispatch(12004, 12004, request("resource_start", value.pop("request_id"), **value))
        self.assertEqual(self._snapshot(), before)

        # A separate run is needed because a real permission revocation is monotonic.
        from tests.test_partitioned_authority import PartitionedAuthorityTests
        helper = PartitionedAuthorityTests("test_real_adoption_upload_restart_replay_commit_and_fresh_read")
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        store = helper.open(PartitionedRunEvaluationExtension())
        self.addCleanup(store.close)
        helper.seed(store)
        helper.begin(store)
        helper.put(store)
        store.dispatch(12001, 12001, request("plan_partition_commit", "resource-current-plan-commit", upload_id="upload"))
        run_id = "partition-resource-current-generation"
        manifest = deepcopy(helper.bound["manifest"])
        manifest.update(schema_version=2, run_id=run_id, purpose="diagnostic", baseline_ref=None,
                        plan_ref=deepcopy(helper.plan_ref))
        profile = fixed_profile()
        store.dispatch(12004, 12004, request("run_begin_v2", "resource-current-begin", manifest=manifest,
            contract_series_id="partition-series", expected_generation=1, execution_profile=profile))
        begun = store._db.execute("SELECT generation FROM eval_current WHERE series_id=?", ("partition-series",)).fetchone()
        self.assertEqual(begun["generation"], 1)
        store._db.execute("UPDATE eval_current SET generation=2 WHERE series_id=?", ("partition-series",))
        before = {name: sorted((tuple(row) for row in store._db.execute('SELECT * FROM "' + name + '"')), key=repr)
                  for name in [r[0] for r in store._db.execute("SELECT name FROM sqlite_master WHERE type='table'")]}
        from gah.resource_authority import _lock
        worker_digest = _lock()["worker_digest"]
        scenario = "constraint:C01:good"
        target_digest = hashlib.sha256(canonical_bytes({"worker_digest": worker_digest, "scenario": scenario})).hexdigest()
        entry = next(item for item in helper.bound["plan"]["entries"] if item["target_ref"]["digest"] == target_digest)
        start = {"run_id": run_id, "owner_id": store._db.execute("SELECT owner_id FROM resource_runs WHERE run_id=?", (run_id,)).fetchone()[0],
                 "operation_id": "resource-op-current", "entry": {key: entry[key] for key in ("obligation_id", "case_id", "trial_id", "variant")},
                 "scenario": scenario, "expected_manifest_ref": content_ref("run_manifest", run_id, manifest)}
        with self.assertRaises(AdoptionError):
            store.dispatch(12004, 12004, request("resource_start", "resource-start-current-invalid", **start))
        after = {name: sorted((tuple(row) for row in store._db.execute('SELECT * FROM "' + name + '"')), key=repr)
                 for name in [r[0] for r in store._db.execute("SELECT name FROM sqlite_master WHERE type='table'")]}
        self.assertEqual(after, before)

    def test_resource_start_rejects_expired_run_without_writes(self):
        begun = self.begin()
        value = self._start_fixed()
        value["request_id"] = "resource-start-expired"
        self.helper.clock.value = self.manifest["deadline"]
        before = self._snapshot()
        with self.assertRaises(AdoptionError):
            self.store.dispatch(12004, 12004, request("resource_start", value.pop("request_id"), **value))
        self.assertEqual(self._snapshot(), before)

    def test_resource_start_rejects_retired_target_without_writes(self):
        self.begin()
        target_ref = deepcopy(self.manifest["target_refs"][0])
        self.call(12001, "target_retire", "resource-retire-target", contract_series_id="partition-series",
            expected_contract_ref=deepcopy(self.manifest["contract_ref"]), target_ref=target_ref,
            reason_code="SERVICE_CLOSED")
        value = self._start_fixed()
        value["request_id"] = "resource-start-retired"
        self._assert_start_rejected_without_writes(value, "TARGET_RETIRED")

    def test_resource_start_rejects_mismatched_entry_and_scenario_without_writes(self):
        self.begin()
        scenario, entry = self._scenario_entry()
        wrong_entry = deepcopy(entry)
        wrong_entry["trial_id"] += "-unplanned"
        value = self._start_fixed(entry=wrong_entry)
        value["request_id"] = "resource-start-entry-mismatch"
        self._assert_start_rejected_without_writes(value, "ENTRY_NOT_PLANNED")
        value = self._start_fixed(scenario="constraint:C02:good")
        value["request_id"] = "resource-start-scenario-mismatch"
        self._assert_start_rejected_without_writes(value, "FIXTURE_BINDING_MISMATCH")

    def test_resource_start_is_rejected_after_finalize_or_hold(self):
        self.begin()
        finalized = self.call(12004, "evidence_finalize_v2", "resource-evidence-finalize", run_id=self.run_id)
        self.assertFalse(finalized["ci_eligible"])
        value = self._start_fixed()
        value["request_id"] = "resource-start-after-finalize"
        self._assert_start_rejected_without_writes(value, "RUN_FINALIZED")

        # A separate run receives a real future-attempt event, which moves its state to HOLD.
        from tests.test_partitioned_authority import PartitionedAuthorityTests
        helper = PartitionedAuthorityTests("test_real_adoption_upload_restart_replay_commit_and_fresh_read")
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        store = helper.open(PartitionedRunEvaluationExtension())
        self.addCleanup(store.close)
        helper.seed(store)
        helper.begin(store)
        helper.put(store)
        store.dispatch(12001, 12001, request("plan_partition_commit", "resource-held-plan-commit", upload_id="upload"))
        run_id = "partition-resource-held"
        manifest = deepcopy(helper.bound["manifest"])
        manifest.update(schema_version=2, run_id=run_id, purpose="diagnostic", baseline_ref=None,
                        plan_ref=deepcopy(helper.plan_ref))
        profile = fixed_profile()
        store.dispatch(12004, 12004, request("run_begin_v2", "resource-held-begin", manifest=manifest,
            contract_series_id="partition-series", expected_generation=1, execution_profile=profile))
        from gah.execution_profiles import expected as expected_profile
        from gah.resource_authority import _lock
        scenario = "constraint:C01:good"
        worker_digest = _lock()["worker_digest"]
        target_digest = hashlib.sha256(canonical_bytes({"worker_digest": worker_digest, "scenario": scenario})).hexdigest()
        entry = next(item for item in helper.bound["plan"]["entries"] if item["target_ref"]["digest"] == target_digest)
        profile_values = expected_profile(profile, target_digest, worker_digest)
        attempt = {"schema_version": 1, "kind": "attempt_record", "attempt_id": "resource-held-attempt",
            "variant": entry["variant"], "retry_of": None, "started_at": 1001, "finished_at": None,
            "stop_confirmed": False, "execution_status": "UNKNOWN", "state_restored": False,
            "expected_binding": {"run_id": run_id, "operation_id": "resource-evidence-op", "owner_epoch": 1,
                "contract_digest": manifest["contract_ref"]["digest"], "target_digest": target_digest,
                "obligation_id": entry["obligation_id"], "case_id": entry["case_id"], "trial_id": entry["trial_id"],
                "stage_id": entry["stage_ids"][0], "fixture_digest": profile_values["fixture_digest"],
                "adapter_digest": profile_values["adapter_digests"][0], "policy_digest": manifest["policy_ref"]["digest"],
                "evaluator_digest": worker_digest, "isolation_digest": profile_values["isolation_digest"]},
            "result": None}
        held = store.dispatch(12003, 12003, request("evidence_record_v2", "resource-held-attempt-record",
            run_id=run_id, attempt=attempt))
        self.assertEqual(held["evidence"]["reason"], "FUTURE_ATTEMPT")
        self.assertEqual(store._db.execute("SELECT state FROM run_state WHERE run_id=?", (run_id,)).fetchone()[0], "HOLD")
        owner_id = store._db.execute("SELECT owner_id FROM resource_runs WHERE run_id=?", (run_id,)).fetchone()[0]
        entry_fields = {key: entry[key] for key in ("obligation_id", "case_id", "trial_id", "variant")}
        start = {"run_id": run_id, "owner_id": owner_id, "operation_id": "resource-op-held",
            "entry": entry_fields, "scenario": scenario,
            "expected_manifest_ref": content_ref("run_manifest", run_id, manifest)}
        before = {name: sorted((tuple(row) for row in store._db.execute('SELECT * FROM "' + name + '"')), key=repr)
                  for name in [r[0] for r in store._db.execute("SELECT name FROM sqlite_master WHERE type='table'")]}
        with self.assertRaisesRegex(AdoptionError, "^RUN_HELD$"):
            store.dispatch(12004, 12004, request("resource_start", "resource-start-after-hold", **start))
        after = {name: sorted((tuple(row) for row in store._db.execute('SELECT * FROM "' + name + '"')), key=repr)
                 for name in [r[0] for r in store._db.execute("SELECT name FROM sqlite_master WHERE type='table'")]}
        self.assertEqual(after, before)

    def test_resource_close_refuses_unstopped_unsettled_operation(self):
        begun = self.begin()
        scenario, entry = self._scenario_entry()
        started = self.call(12004, "resource_start", "resource-start-open-op", run_id=self.run_id,
            owner_id=begun["resource_snapshot"]["owner_id"], operation_id="resource-op-open",
            entry=entry, scenario=scenario, expected_manifest_ref=content_ref("run_manifest", self.run_id, self.manifest))
        before = self._snapshot()
        with self.assertRaisesRegex(AdoptionError, "^CLOSE_DENIED$"):
            self.call(12004, "resource_close", "resource-close-open-op", run_id=self.run_id,
                owner_id=begun["resource_snapshot"]["owner_id"], owner_epoch=started["owner_epoch"])
        self.assertEqual(self._snapshot(), before)
        run = self.store._db.execute("SELECT closed_at FROM resource_runs WHERE run_id=?", (self.run_id,)).fetchone()
        operation = self.store._db.execute("SELECT stopped_at,settled_at FROM resource_operations WHERE operation_id=?",
            ("resource-op-open",)).fetchone()
        self.assertIsNone(run["closed_at"])
        self.assertIsNone(operation["stopped_at"])
        self.assertIsNone(operation["settled_at"])

    def test_v1_check_start_delegation_is_unchanged(self):
        expected = ({"run_id": "legacy-run"}, {"entries": []})
        with patch("gah.evaluation_authority.EvaluationExtension._check_start", return_value=expected) as original:
            value = self.store._extension._check_start(
                self.store, self.store._db, "legacy-run", 1000,
                actor_id="operator", context="operator-context",
            )
        self.assertEqual(value, expected)
        original.assert_called_once_with(
            self.store._extension, self.store, self.store._db, "legacy-run", 1000,
            actor_id="operator", context="operator-context",
        )


if __name__ == "__main__":
    unittest.main()
