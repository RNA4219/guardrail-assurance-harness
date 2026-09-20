from __future__ import annotations

import copy
from pathlib import Path
import sqlite3
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gah.contracts import ContractError
from gah.partitioned_llm_materialization import build
from gah.partitioned_normal_evidence import PartitionedNormalEvidenceBook
from gah.partitioned_run_contracts import materialize_partitioned_run
from gah.policy import initial_policy_profile
from gah.run_evidence import EvidenceError, RunEvidenceBook, _pack, bound_bundle_digest, create_schema
from gah.run_contracts import content_ref

IMAGE = "sha256:" + "a" * 64
WORKER = "b" * 64
ISOLATION = {"schema_version": 1, "kind": "test_isolation", "network": "none"}


def _db():
    db = sqlite3.connect(":memory:", isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("BEGIN IMMEDIATE")
    create_schema(db)
    return db


def _materialize(value, baseline):
    return materialize_partitioned_run(
        value["manifest"], value["contract"], value["plan_index"], value["plan_segments"],
        value["policy"], value["registry"], value["case_set"], baseline_context=baseline,
    )


class PartitionedNormalEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        policy = initial_policy_profile()
        common = dict(case_count=400, policy_generation=1, now=1000, image_id=IMAGE,
                      worker_digest=WORKER, isolation_profile=ISOLATION)
        cls.first = build(policy, run_id="normal-partitioned-gen1", target_version="baseline-v1", **common)
        baseline_ref = content_ref("baseline", "normal-partitioned-baseline", {"generation": 1})
        baseline_input = {
            "baseline_ref": baseline_ref,
            "targets": [{"control_id": "LC-query-scale", "target_ref": cls.first["manifest"]["target_refs"][0]}],
            "contract": cls.first["contract"],
        }
        cls.second = build(
            policy, run_id="normal-partitioned-gen2", target_version="degraded-v2", now=2000,
            generation=2, baseline_context=baseline_input, case_count=400,
            policy_generation=1, image_id=IMAGE, worker_digest=WORKER,
            isolation_profile=ISOLATION,
        )
        cls.baseline = {"baseline_ref": baseline_ref, "targets": copy.deepcopy(baseline_input["targets"])}

    def setUp(self):
        self.values = {
            "normal-partitioned-gen1": (self.first, None),
            "normal-partitioned-gen2": (self.second, self.baseline),
        }
        self.calls = 0
        self.profile_override = None

        def resolver(db, now, receipt, stored_profile):
            self.calls += 1
            value, baseline = self.values[receipt["manifest_ref"]["id"]]
            bound = _materialize(value, baseline)
            profile = copy.deepcopy(self.profile_override or value["execution_profile"])
            return bound, profile, copy.deepcopy(baseline)

        self.resolver = resolver

    def _bound(self, key):
        value, baseline = self.values[key]
        return _materialize(value, baseline), value["execution_profile"], baseline

    def _book(self, db, key):
        bound, profile, baseline = self._bound(key)
        digest = bound_bundle_digest(bound, baseline)
        return PartitionedNormalEvidenceBook(
            db, now=3000, allowed_bindings={key: digest}, context_resolver=self.resolver,
        ), bound, profile, baseline

    def test_gen1_stores_only_receipt_and_preserves_public_mutation_isolation(self):
        db = _db()
        try:
            book, bound, profile, baseline = self._book(db, "normal-partitioned-gen1")
            started = book.start_run(bound, profile, baseline)
            self.assertEqual(started["schema_version"], 2)
            self.assertEqual(started["kind"], "run_evidence_run")
            self.assertEqual(started["bundle"], bound["_partitioned_receipt"])
            self.assertNotIn("plan", started["bundle"])
            self.assertFalse(started["ci_eligible"])
            self.assertFalse(started["authority_connected"])
            row = db.execute("SELECT * FROM bound_runs WHERE run_id=?", ("normal-partitioned-gen1",)).fetchone()
            raw_bundle = row["bundle_json"]
            self.assertEqual(_pack(started["bundle"])[0], raw_bundle)
            self.assertIsNone(row["baseline_json"])
            started["bundle"]["selected_controls"].clear()
            started["binding"]["scope_digest"] = "0" * 64
            again = book.get_run("normal-partitioned-gen1")
            self.assertTrue(again["bundle"]["selected_controls"])
            replay = book.start_run(bound, profile, baseline)
            self.assertEqual(replay["bundle_digest"], again["bundle_digest"])
            with self.assertRaisesRegex(EvidenceError, "^STORAGE_CORRUPT$"):
                RunEvidenceBook(db, now=3000, allowed_bindings={}).get_run("normal-partitioned-gen1")
            db.commit()
        finally:
            db.close()

    def test_gen1_old_contract_run_preserves_no_baseline_context(self):
        value = copy.deepcopy(self.first)
        value["manifest"]["purpose"] = "contract_old_regression"
        key = value["manifest"]["run_id"]
        self.values[key] = (value, None)
        db = _db()
        try:
            book, bound, profile, baseline = self._book(db, key)
            started = book.start_run(bound, profile, baseline)
            self.assertEqual(started["state"], "OPEN")
            self.assertIsNone(started["baseline_context"])
            self.assertFalse(started["ci_eligible"])
        finally:
            db.close()

    def test_gen2_saves_baseline_and_re_resolves_exact_comparison_binding(self):
        db = _db()
        try:
            book, bound, profile, baseline = self._book(db, "normal-partitioned-gen2")
            started = book.start_run(bound, profile, baseline)
            row = db.execute("SELECT * FROM bound_runs WHERE run_id=?", ("normal-partitioned-gen2",)).fetchone()
            self.assertEqual(_pack(baseline), (row["baseline_json"], row["baseline_digest"]))
            self.assertEqual(started["binding"]["baseline_digest"], row["baseline_digest"])
            self.assertEqual(started["bundle"], bound["_partitioned_receipt"])
            before = self.calls
            retrieved = book.get_run("normal-partitioned-gen2")
            self.assertGreater(self.calls, before)
            self.assertEqual(retrieved["baseline_context"], baseline)
            self.assertFalse(retrieved["baseline_freshness_verified"])
            db.commit()
        finally:
            db.close()

    def test_diagnostic_context_and_changed_baseline_fail_closed(self):
        db = _db()
        try:
            book, bound, profile, baseline = self._book(db, "normal-partitioned-gen2")
            changed = copy.deepcopy(bound)
            changed["_partitioned_context"]["manifest"]["purpose"] = "diagnostic"
            # The runtime validator rejects stale receipt/manifest linkage before storage.
            with self.assertRaises(EvidenceError):
                book.start_run(changed, profile, baseline)
            with self.assertRaisesRegex(EvidenceError, "^INVALID_BINDING$"):
                book.start_run(bound, profile, {**baseline, "baseline_ref": content_ref(
                    "baseline", "other-baseline", {"generation": 1}
                )})
            self.assertEqual(db.execute("SELECT count(*) FROM bound_runs").fetchone()[0], 0)
        finally:
            db.rollback()
            db.close()

    def test_replay_with_different_profile_conflicts_without_rewriting_saved_row(self):
        db = _db()
        try:
            book, bound, profile, baseline = self._book(db, "normal-partitioned-gen1")
            book.start_run(bound, profile, baseline)
            row_before = db.execute("SELECT bundle_json,profile_json FROM bound_runs WHERE run_id=?",
                                    ("normal-partitioned-gen1",)).fetchone()
            changed_profile = copy.deepcopy(profile)
            changed_profile["bindings"][0]["fixture_digest"] = "c" * 64
            self.profile_override = changed_profile
            with self.assertRaisesRegex(EvidenceError, "^RUN_CONFLICT$"):
                book.start_run(bound, changed_profile, baseline)
            row_after = db.execute("SELECT bundle_json,profile_json FROM bound_runs WHERE run_id=?",
                                   ("normal-partitioned-gen1",)).fetchone()
            self.assertEqual(tuple(row_before), tuple(row_after))
        finally:
            db.rollback()
            db.close()


if __name__ == "__main__":
    unittest.main()
