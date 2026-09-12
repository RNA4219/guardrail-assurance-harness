"""所有権切れ後の取消し取得、停止・精算と旧取消しDBの移行を確認する。"""
from contextlib import closing
import importlib.util
import json
from pathlib import Path
import sqlite3
import unittest

ROOT = Path(__file__).resolve().parents[1]
def module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value

resource_seed = module("cancel_claim_resource_seed", "tests/test_resources.py")
integration = module("cancel_claim_integration_seed", "tests/test_run_cancellation.py")
from gah.resources import ResourceError
from gah.adoption import AdoptionError
from gah import adoption_migrations as migrations
from gah.contracts import MAX_INTEGER
request = integration.request


class CancelClaimResourceTests(unittest.TestCase):
    setUp = resource_seed.ResourceBookTests.setUp
    tearDown = resource_seed.ResourceBookTests.tearDown
    call = resource_seed.ResourceBookTests.call
    make_run = resource_seed.ResourceBookTests.make_run
    reserve = resource_seed.ResourceBookTests.reserve
    dispatch = resource_seed.ResourceBookTests.dispatch
    reservation = staticmethod(resource_seed.ResourceBookTests.reservation)

    def test_expiry_boundary_fences_owner_and_keeps_dispatched_reservation(self):
        self.make_run()
        self.reserve("pending")
        self.reserve("sent")
        self.dispatch("sent")
        with self.assertRaisesRegex(ResourceError, "^OWNER_ACTIVE$"):
            self.call("claim_for_cancel", "run-1", "recovery", 159)
        result = self.call("claim_for_cancel", "run-1", "recovery", 160)
        self.assertEqual(result["owner_epoch"], 2)
        self.assertTrue(result["cancelled"])
        self.assertTrue(result["recovery_only"])
        self.assertEqual(result["resources"]["slots"], 1)
        rows = {r[0]: tuple(r)[1:] for r in self.db.execute("SELECT operation_id,released,stopped_at,settled_at FROM resource_operations")}
        self.assertEqual(rows, {"pending": (1, None, None), "sent": (0, None, None)})
        with self.assertRaisesRegex(ResourceError, "^OWNER_STALE$"):
            self.call("cancel", "run-1", "owner-1", 1, 161)
        with self.assertRaisesRegex(ResourceError, "^START_DENIED$"):
            self.reserve("forbidden", owner="recovery", epoch=2, now=161)

    def test_current_owner_can_cancel_but_cannot_manufacture_stop(self):
        self.make_run()
        self.reserve("sent")
        self.dispatch("sent")
        first = self.call("claim_for_cancel", "run-1", "owner-1", 103)
        self.assertEqual(first["owner_epoch"], 1)
        self.assertFalse(first["budget_closure"])
        with self.assertRaisesRegex(ResourceError, "^CLOSE_DENIED$"):
            self.call("close", "run-1", "owner-1", 1, 104)
        second = self.call("claim_for_cancel", "run-1", "owner-1", 104)
        for key in ("slots", "unsettled", "case_trial_executions", "model_calls"):
            self.assertEqual(first["resources"][key], second["resources"][key])

    def test_cancel_failure_rolls_back_ownership_and_clock(self):
        self.make_run()
        before = tuple(self.db.execute("SELECT * FROM resource_runs").fetchone())
        self.db.execute("CREATE TRIGGER fail_cancel BEFORE UPDATE OF cancelled ON resource_runs BEGIN SELECT RAISE(ABORT,'test'); END")
        with self.assertRaises(sqlite3.Error):
            self.call("claim_for_cancel", "run-1", "recovery", 160)
        self.assertEqual(tuple(self.db.execute("SELECT * FROM resource_runs").fetchone()), before)
        self.assertEqual(self.db.execute("SELECT value FROM resource_meta WHERE key='last_clock'").fetchone()[0], 100)

    def test_generation_exhaustion_does_not_cancel(self):
        self.make_run()
        self.db.execute("UPDATE resource_runs SET owner_epoch=?", (MAX_INTEGER,))
        with self.assertRaisesRegex(ResourceError, "^GENERATION_EXHAUSTED$"):
            self.call("claim_for_cancel", "run-1", "recovery", 160)
        self.assertEqual(self.db.execute("SELECT cancelled FROM resource_runs").fetchone()[0], 0)


class CancelClaimIntegrationTests(unittest.TestCase):
    setUp = integration.CancellationIntegrationTests.setUp
    open = integration.CancellationIntegrationTests.open
    prepare = integration.CancellationIntegrationTests.prepare
    begin = integration.CancellationIntegrationTests.begin
    complete = integration.CancellationIntegrationTests.complete
    completed = integration.CancellationIntegrationTests.completed
    gate_request = integration.CancellationIntegrationTests.gate_request
    gate = integration.CancellationIntegrationTests.gate
    operation = integration.CancellationIntegrationTests.operation
    observe = integration.CancellationIntegrationTests.observe
    cancel = integration.CancellationIntegrationTests.cancel
    finalize = integration.CancellationIntegrationTests.finalize

    @classmethod
    def setUpClass(cls):
        integration.CancellationIntegrationTests.setUpClass.__func__(cls)

    def claim_cancel(self, run_id="normal", owner_id="recovery", identifier="cancel-claim"):
        return self.store.dispatch(12004, 12004, request("resource_cancel_claim", identifier,
            run_id=run_id, owner_id=owner_id))

    def test_lost_history_and_expired_owner_still_allow_stop_and_accounting(self):
        prepared = self.prepare()
        owner = self.begin(prepared)
        op = self.operation(prepared, owner)
        self.clock.value += 61
        self.store._db.execute("DELETE FROM eval_adoptions WHERE generation=2")
        for recovery in (False, True):
            with self.assertRaises(AdoptionError):
                self.store.dispatch(12004, 12004, request("resource_claim", "claim-" + str(recovery),
                    run_id="normal", owner_id="recovery", recovery=recovery))
        with self.assertRaisesRegex(AdoptionError, "^OWNER_STALE$"):
            self.cancel(owner)
        acquired = self.claim_cancel()
        self.assertEqual(acquired["owner_epoch"], 2)
        self.assertTrue(acquired["cancelled"])
        self.assertEqual(acquired["resources"]["slots"], 1)
        self.gate(prepared, 2)
        self.observe("normal", op, "stopped", usage=integration.ZERO)
        closed = self.store.dispatch(12004, 12004, request("resource_close", "recovered-close",
            run_id="normal", owner_id="recovery", owner_epoch=2))
        self.assertTrue(closed["budget_closure"])
        with self.assertRaises(AdoptionError):
            self.finalize()
        self.gate(prepared, 2)

    def test_revoked_source_and_expired_owner_keep_current_ci_cancelled(self):
        prepared = self.prepare()
        owner = self.begin(prepared)
        self.store.dispatch(12004, 12004, request("evidence_revoke", "revoke-source", run_id=self.source_id))
        self.clock.value += 61
        with self.assertRaises(AdoptionError):
            self.store.dispatch(12004, 12004, request("resource_claim", "resume",
                run_id="normal", owner_id="recovery", recovery=False))
        acquired = self.claim_cancel()
        self.assertEqual(acquired["owner_epoch"], 2)
        receipt = self.finalize()
        self.assertEqual(receipt["execution_status"], "CANCELLED")
        self.gate(prepared, 3)
        self.store.close()
        self.store = self.open()
        self.assertEqual(self.finalize(), receipt)
        self.gate(prepared, 3)

    def test_roles_shape_and_active_owner_do_not_change_state(self):
        self.begin(self.prepare())
        value = request("resource_cancel_claim", "invalid", run_id="normal", owner_id="recovery")
        before = tuple(self.store._db.execute("SELECT * FROM resource_runs WHERE run_id='normal'").fetchone())
        for uid in (12001, 12002, 12003):
            with self.subTest(uid=uid), self.assertRaises(AdoptionError):
                self.store.dispatch(uid, uid, value)
        for changes in ({"schema_version": True}, {"owner_epoch": 1}, {"stopped": True}, {"terminal_pending": True}):
            with self.subTest(changes=changes), self.assertRaises(AdoptionError):
                self.store.dispatch(12004, 12004, {**value, **changes})
        with self.assertRaisesRegex(AdoptionError, "^OWNER_ACTIVE$"):
            self.claim_cancel()
        self.assertEqual(tuple(self.store._db.execute("SELECT * FROM resource_runs WHERE run_id='normal'").fetchone()), before)

    def test_completed_and_cancelled_receipts_cannot_be_replaced(self):
        prepared, owner = self.completed()
        receipt = self.store.dispatch(12004, 12004, request("evidence_finalize", "normal-finalize", run_id="completed"))
        self.clock.value += 61
        self.assertTrue(self.claim_cancel("completed")["already_terminal"])
        self.assertEqual(self.store._db.execute("SELECT cancelled,owner_epoch FROM resource_runs WHERE run_id='completed'").fetchone()[:], (0, 1))
        self.gate(prepared, 0)
        prepared = self.prepare("cancelled")
        self.cancel(self.begin(prepared))
        cancelled = self.finalize("cancelled")
        self.clock.value += 61
        result = self.claim_cancel("cancelled", identifier="cancelled-claim")
        self.assertTrue(result["already_terminal"])
        self.assertTrue(result["cancelled"])
        self.assertEqual(self.finalize("cancelled"), cancelled)
        self.gate(prepared, 3)

    def test_closed_but_unfinalized_can_be_cancelled_after_expiry(self):
        prepared, owner = self.completed()
        self.clock.value += 61
        result = self.claim_cancel("completed")
        self.assertEqual(result["owner_epoch"], 2)
        self.assertTrue(result["closed"])
        self.assertTrue(result["cancelled"])
        self.finalize("completed")
        self.gate(prepared, 3)

    def test_previous_cancellation_store_migrates_without_rewriting_history(self):
        prepared, owner = self.completed()
        self.cancel(owner)
        receipt = self.finalize("completed")
        self.begin(self.prepare("unfinished"))
        before = tuple(self.store._db.execute("SELECT * FROM authority_artifacts ORDER BY kind,id,digest"))
        self.store._db.execute("UPDATE adoption_config SET value=? WHERE key='extension_digest'", (migrations._V4_CANCELLATION_EXTENSION_DIGEST,))
        self.store.close()
        result = migrations.migrate_evaluation_store(self.path)
        self.assertEqual(result["predecessor_extension_digest"], migrations._V4_CANCELLATION_EXTENSION_DIGEST)
        self.store = self.open()
        self.assertEqual(tuple(self.store._db.execute("SELECT * FROM authority_artifacts ORDER BY kind,id,digest")), before)
        self.assertEqual(self.finalize("completed"), receipt)
        self.gate(prepared, 3)
        self.store._db.execute("DELETE FROM authority_artifacts WHERE kind='plans_report' AND run_id='completed'")
        self.store._db.execute("UPDATE adoption_config SET value=? WHERE key='extension_digest'", (migrations._V4_CANCELLATION_EXTENSION_DIGEST,))
        self.store.close()
        with self.assertRaises(migrations.MigrationError):
            migrations.migrate_evaluation_store(self.path)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT value FROM adoption_config WHERE key='extension_digest'").fetchone()[0], migrations._V4_CANCELLATION_EXTENSION_DIGEST)
