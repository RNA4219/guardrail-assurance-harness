"""評価契約の採択historyをcurrentへ取り違えない検査。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from gah.adoption import AdoptionError, AdoptionStore
from gah.evaluation_authority import EvaluationExtension
import gah.evaluation_authority as evaluation_authority
from gah.policy import initial_policy_profile
import test_assurance_authority as _assurance_fixture


class _Clock:
    def __init__(self, value=1000):
        self.value = value

    def __call__(self):
        return self.value


class EvaluationHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "history.sqlite"
        self.clock = _Clock()
        self.policy = initial_policy_profile()
        lock = json.loads((ROOT / "config" / "fixture-runtime.lock.json").read_text(encoding="utf-8"))
        self.helper = object.__new__(_assurance_fixture.AssuranceAuthorityTests)
        self.helper.clock = self.clock
        self.helper.policy = self.policy
        self.helper.worker_digest = lock["worker_digest"]
        self.helper.scenario = "mutation:F01:healthy"
        self.helper.target = {"kind": "target", "id": "fixed-target", "digest": hashlib.sha256(
            json.dumps({"worker_digest": self.helper.worker_digest, "scenario": self.helper.scenario},
                       sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()}
        self.helper.evaluator = {"kind": "evaluator", "id": "fixed-evaluator", "digest": self.helper.worker_digest}

    def _open_ready(self):
        store = AdoptionStore(self.path, clock=self.clock, bootstrap_policy=self.policy,
                               validator_digest="b" * 64, extension=EvaluationExtension())
        values = _assurance_fixture.AssuranceAuthorityTests._setup_run(self.helper, store, "history-run")
        return store, values

    def _check_start(self, store, run_id="history-run"):
        with store._transaction() as db:
            return store._extension._check_start(store, db, run_id, self.clock.value)

    def test_history_and_current_contract_have_valid_immutable_binding(self):
        store, _ = self._open_ready()
        try:
            manifest, plan = self._check_start(store)
            self.assertEqual(manifest["run_id"], "history-run")
            self.assertEqual(plan["contract_ref"], manifest["contract_ref"])
        finally:
            store.close()

    def test_validation_column_digest_mismatch_is_rejected(self):
        store, _ = self._open_ready()
        try:
            original = store._db.execute(
                "SELECT proposal_digest FROM eval_validations WHERE id='contract-validation'"
            ).fetchone()[0]
            store._db.execute(
                "UPDATE eval_validations SET proposal_digest=? WHERE id='contract-validation'",
                ("f" * 64,),
            )
            with self.assertRaisesRegex(AdoptionError, "^CONTRACT_INVALID$"):
                self._check_start(store)
            store._db.execute(
                "UPDATE eval_validations SET proposal_digest=? WHERE id='contract-validation'",
                (original,),
            )
        finally:
            store.close()

    def test_history_proposal_series_mismatch_is_rejected_without_current_rewrite(self):
        store, _ = self._open_ready()
        try:
            original = store._db.execute(
                "SELECT series_id FROM eval_proposals WHERE id='contract-proposal'"
            ).fetchone()[0]
            store._db.execute(
                "UPDATE eval_proposals SET series_id=? WHERE id='contract-proposal'",
                ("other-series",),
            )
            with store._transaction() as db:
                with self.assertRaisesRegex(AdoptionError, "^CONTRACT_INVALID$"):
                    store._extension._bound_evidence_run(store, db, "history-run", self.clock.value)
            store._db.execute(
                "UPDATE eval_proposals SET series_id=? WHERE id='contract-proposal'",
                (original,),
            )
        finally:
            store.close()

    def test_history_contract_payload_hash_mismatch_is_rejected(self):
        store, _ = self._open_ready()
        try:
            row = store._db.execute(
                "SELECT payload_json FROM eval_adoptions WHERE series_id='evaluation-main' AND generation=1"
            ).fetchone()
            payload = json.loads(row[0])
            payload["contract_id"] = "changed-contract"
            raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
            store._db.execute(
                "UPDATE eval_adoptions SET payload_json=?, digest=? WHERE series_id='evaluation-main' AND generation=1",
                (raw, digest),
            )
            with store._transaction() as db:
                with self.assertRaisesRegex(AdoptionError, "^CONTRACT_INVALID$"):
                    store._extension._bound_evidence_run(store, db, "history-run", self.clock.value)
        finally:
            store.close()

    def test_proposal_actor_context_must_be_the_authorized_manager_identity(self):
        store, _ = self._open_ready()
        try:
            store._db.execute(
                "UPDATE eval_proposals SET actor_id=?, context=? WHERE id='contract-proposal'",
                ("validator", "validator-context"),
            )
            with self.assertRaisesRegex(AdoptionError, "^CONTRACT_INVALID$"):
                self._check_start(store)
        finally:
            store.close()

    def test_validation_payload_checked_at_and_reference_are_bound_to_contract(self):
        store, values = self._open_ready()
        try:
            row = store._db.execute(
                "SELECT payload_json FROM eval_validations WHERE id='contract-validation'"
            ).fetchone()
            payload = json.loads(row[0])
            payload["checked_at"] += 1
            payload["policy_ref"] = values["contract"]["registry_ref"]
            raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
            store._db.execute(
                "UPDATE eval_validations SET payload_json=?, digest=? WHERE id='contract-validation'",
                (raw, digest),
            )
            with self.assertRaisesRegex(AdoptionError, "^CONTRACT_INVALID$"):
                self._check_start(store)
        finally:
            store.close()

    def test_fresh_contract_rejects_revoked_manager_or_validator(self):
        store, values = self._open_ready()
        try:
            with store._transaction() as db:
                current = db.execute(
                    "SELECT * FROM eval_adoptions WHERE series_id='evaluation-main' AND generation=1"
                ).fetchone()
                contract = evaluation_authority._load_json(current, "payload_json", "digest")
                db.execute("UPDATE adoption_meta SET value=1 WHERE key='permission_generation'")
                db.execute(
                    "INSERT INTO revocations VALUES(?,?,?,?)",
                    ("actor", "validator", 1, self.clock.value),
                )
                with self.assertRaisesRegex(AdoptionError, "^CONTRACT_INVALID$"):
                    evaluation_authority._assert_current_valid(store, db, current, contract, self.clock.value)
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
