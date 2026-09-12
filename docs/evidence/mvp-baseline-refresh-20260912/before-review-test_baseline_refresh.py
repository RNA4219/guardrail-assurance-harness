"""固定gen2通常runからのbaseline更新と固定参照の現在利用を検査する。"""
from contextlib import closing
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sqlite3
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("baseline_refresh_seed", ROOT / "tests/test_run_cancellation.py")
seed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(seed)
from gah.adoption import AdoptionError
from gah import adoption_migrations as migrations
from gah import baseline_authority as base
from gah.run_contracts import content_ref
request = seed.request


class BaselineRefreshTests(unittest.TestCase):
    setUp = seed.CancellationIntegrationTests.setUp
    open = seed.CancellationIntegrationTests.open
    prepare = seed.CancellationIntegrationTests.prepare
    begin = seed.CancellationIntegrationTests.begin
    complete = seed.CancellationIntegrationTests.complete
    completed = seed.CancellationIntegrationTests.completed
    gate_request = seed.CancellationIntegrationTests.gate_request
    gate = seed.CancellationIntegrationTests.gate
    cancel = seed.CancellationIntegrationTests.cancel
    finalize = seed.CancellationIntegrationTests.finalize

    @classmethod
    def setUpClass(cls):
        seed.CancellationIntegrationTests.setUpClass.__func__(cls)

    def source(self):
        prepared, owner = self.completed()
        self.store.dispatch(12004, 12004, request("evidence_finalize", "complete-source", run_id="completed"))
        row = self.store._db.execute("SELECT * FROM baseline_current").fetchone()
        self.series = row["series_id"]
        self.old = json.loads(row["baseline_json"])
        return prepared

    def propose(self, name="refresh", run_id="completed"):
        return self.store.dispatch(12001, 12001, request("baseline_propose", name + "-propose",
            proposal_id=name, series_id=self.series, run_id=run_id, expected_generation=1))

    def validate(self, name="refresh"):
        return self.store.dispatch(12003, 12003, request("baseline_validate", name + "-validate",
            proposal_id=name, validation_id=name + "-validation"))

    def adopt(self, name="refresh"):
        return self.store.dispatch(12001, 12001, request("baseline_adopt", name + "-adopt",
            proposal_id=name, validation_id=name + "-validation", expected_generation=1))

    def current(self):
        return self.store.dispatch(12004, 12004, request("baseline_current", "current-refresh", series_id=self.series))

    def pinned(self, record, action="baseline_resolve", uid=12004):
        return self.store.dispatch(uid, uid, request(action, action + "-" + record["baseline_id"],
            series_id=self.series, expected_baseline_ref=content_ref("baseline", record["baseline_id"], record),
            expected_contract_ref=record["contract_ref"]))

    def test_refresh_keeps_contract_baseline_and_historical_artifacts(self):
        prepared = self.source()
        artifacts = tuple(self.store._db.execute("SELECT * FROM authority_artifacts ORDER BY kind,id,digest"))
        self.propose()
        self.validate()
        self.assertEqual(self.adopt()["generation"], 2)
        current = self.current()
        self.assertTrue(current["valid"], current)
        self.assertEqual(current["generation"], 2)
        self.assertTrue(self.pinned(self.old)["use"])
        self.assertTrue(self.pinned(current["baseline"])["use"])
        with self.assertRaisesRegex(AdoptionError, "^BINDING_MISMATCH$"):
            self.pinned(self.old, action="baseline_use")
        self.assertEqual(current["baseline"]["valid_until"], json.loads(self.store._db.execute(
            "SELECT payload_json FROM authority_artifacts WHERE run_id='completed' AND kind='evidence'").fetchone()[0])["valid_until"])
        self.assertEqual(tuple(self.store._db.execute("SELECT * FROM authority_artifacts ORDER BY kind,id,digest")), artifacts)
        self.gate(prepared, 0)
        self.begin(self.prepare("after-refresh"))
        self.store.close()
        self.store = self.open()
        self.assertTrue(self.current()["valid"])
        self.gate(prepared, 0)

    def test_reference_revocation_is_scoped_and_dependency_loss_propagates(self):
        prepared = self.source()
        self.propose(); self.validate(); self.adopt()
        new = self.current()["baseline"]
        self.pinned(new, action="baseline_revoke_ref")
        self.assertFalse(self.current()["valid"])
        self.assertTrue(self.pinned(self.old)["use"])
        self.gate(prepared, 0)
        self.pinned(self.old, action="baseline_revoke_ref")
        self.assertFalse(self.pinned(self.old)["use"])
        self.assertFalse(self.pinned(new)["use"])
        self.gate(prepared, 1)

    def test_predecessor_revocation_invalidates_unrevoked_successor(self):
        prepared = self.source()
        self.propose(); self.validate(); self.adopt()
        new = self.current()["baseline"]
        self.pinned(self.old, action="baseline_revoke_ref")
        self.assertEqual(self.store._db.execute(
            "SELECT COUNT(*) FROM baseline_revocations WHERE generation=2").fetchone()[0], 0)
        self.assertFalse(self.current()["valid"])
        self.assertFalse(self.pinned(new)["use"])
        self.gate(prepared, 1)

    def test_negative_run_cannot_be_promoted_and_keeps_findings(self):
        self.source()
        prepared = self.prepare("negative-refresh")
        def negative(record, observations):
            if record["scenario"].startswith("constraint:C01:") and record["variant"] == "candidate":
                return {**observations, "check": "FAIL"}
            return observations
        self.complete(prepared, mutate=negative)
        self.gate(prepared, 1)
        before = tuple(self.store._db.execute(
            "SELECT * FROM authority_artifacts WHERE run_id='negative-refresh' ORDER BY kind,id,digest"))
        with self.assertRaises(AdoptionError):
            self.propose("negative-proposal", run_id="negative-refresh")
            self.validate("negative-proposal")
        self.assertEqual(self.current()["generation"], 1)
        self.assertEqual(tuple(self.store._db.execute(
            "SELECT * FROM authority_artifacts WHERE run_id='negative-refresh' ORDER BY kind,id,digest")), before)
        self.assertTrue(json.loads(self.store._db.execute(
            "SELECT payload_json FROM authority_artifacts WHERE run_id='negative-refresh' AND kind='findings_report'").fetchone()[0])["items"])
        self.gate(prepared, 1)

    def test_update_failure_rolls_back_pointer_history_and_receipt(self):
        self.source(); self.propose(); self.validate()
        self.store._db.execute("CREATE TRIGGER fail_refresh BEFORE UPDATE OF generation ON baseline_current WHEN NEW.generation=2 BEGIN SELECT RAISE(ABORT,'test'); END")
        with self.assertRaises(AdoptionError):
            self.adopt()
        self.assertEqual(self.current()["generation"], 1)
        self.assertEqual(self.store._db.execute("SELECT COUNT(*) FROM baseline_adoptions").fetchone()[0], 1)
        self.assertIsNone(self.store._db.execute("SELECT 1 FROM idempotency WHERE request_id='refresh-adopt'").fetchone())
        self.store._db.execute("DROP TRIGGER fail_refresh")
        self.assertEqual(self.adopt()["generation"], 2)

    def test_competing_proposals_and_wrong_roles_cannot_advance_twice(self):
        self.source()
        value = request("baseline_propose", "wrong-role", proposal_id="wrong-role", series_id=self.series,
            run_id="completed", expected_generation=1)
        for uid in (12002, 12003, 12004):
            with self.subTest(uid=uid), self.assertRaises(AdoptionError):
                self.store.dispatch(uid, uid, value)
        for name in ("first", "second"):
            self.propose(name); self.validate(name)
        self.adopt("first")
        with self.assertRaisesRegex(AdoptionError, "^GENERATION_CONFLICT$"):
            self.adopt("second")
        self.assertEqual(self.store._db.execute("SELECT COUNT(*) FROM baseline_adoptions").fetchone()[0], 2)

    def test_cancelled_source_and_bool_in_stored_proposal_are_rejected(self):
        self.source()
        self.propose()
        row = self.store._db.execute("SELECT proposal_json FROM baseline_proposals WHERE proposal_id='refresh'").fetchone()
        value = json.loads(row[0]); value["expected_generation"] = True
        raw, digest = base._pack(value)
        self.store._db.execute("UPDATE baseline_proposals SET proposal_json=?,proposal_digest=? WHERE proposal_id='refresh'", (raw, digest))
        with self.assertRaises(AdoptionError):
            self.validate()
        other = self.prepare("cancelled-refresh")
        self.cancel(self.begin(other))
        self.finalize("cancelled-refresh")
        with self.assertRaises(AdoptionError):
            self.propose("cancelled-proposal", run_id="cancelled-refresh")

    def test_missing_predecessor_and_wrong_reference_fail_closed(self):
        prepared = self.source()
        self.propose(); self.validate(); self.adopt()
        bad = deepcopy(self.old); bad["generation"] = 2
        with self.assertRaises(AdoptionError):
            self.pinned(bad)
        self.store._db.execute("DELETE FROM baseline_adoptions WHERE generation=1")
        with self.assertRaises(AdoptionError):
            self.current()
        self.gate(prepared, 2)

    def test_previous_recovery_store_migrates_but_cannot_contain_refresh(self):
        prepared = self.source()
        self.store._db.execute("UPDATE adoption_config SET value=? WHERE key='extension_digest'", (migrations._V4_RECOVERY_EXTENSION_DIGEST,))
        self.store.close()
        result = migrations.migrate_evaluation_store(self.path)
        self.assertEqual(result["predecessor_extension_digest"], migrations._V4_RECOVERY_EXTENSION_DIGEST)
        self.store = self.open()
        self.gate(prepared, 0)
        self.propose()
        self.store._db.execute("UPDATE adoption_config SET value=? WHERE key='extension_digest'", (migrations._V4_RECOVERY_EXTENSION_DIGEST,))
        self.store.close()
        with self.assertRaises(migrations.MigrationError):
            migrations.migrate_evaluation_store(self.path)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT value FROM adoption_config WHERE key='extension_digest'").fetchone()[0], migrations._V4_RECOVERY_EXTENSION_DIGEST)
