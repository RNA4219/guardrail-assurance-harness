from __future__ import annotations

import copy
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from gah.partitioned_run_contracts import bind_partitioned_run_manifest
from gah.partitioned_trial_plan import partition_trial_plan
from gah.run_evidence import EvidenceError, RunEvidenceBook, _pack, bound_bundle_digest, create_schema
from gah.partitioned_run_evidence import PartitionedRunEvidenceBook
from gah.partitioned_trial_plan import MAX_SEGMENTS
from test_run_contracts import _fixtures, _manifest, _plan
from test_run_evidence import ZERO, attempt_for


def _setup():
    fixtures = _fixtures("not_applicable")
    plan = _plan(fixtures)
    manifest = _manifest(fixtures, plan)
    manifest["schema_version"] = 2
    manifest["purpose"] = "diagnostic"
    manifest["baseline_ref"] = None
    index, segments = partition_trial_plan(plan)
    from gah.run_contracts import content_ref
    manifest["plan_ref"] = content_ref("trial_plan_index", index["plan_id"], index)
    receipt = bind_partitioned_run_manifest(
        manifest, fixtures["contract"], index, segments,
        fixtures["policy"], fixtures["registry"], fixtures["case_set"],
    )
    profile = {"fixture_digest": ZERO, "adapter_digests": ["1" * 64],
               "isolation_digest": "2" * 64}
    context = {"manifest": manifest, "contract": fixtures["contract"], "index": index,
               "segments": segments, "policy": fixtures["policy"],
               "registry": fixtures["registry"], "case_set": fixtures["case_set"],
               "execution_profile": profile, "baseline_context": None}
    return fixtures, plan, manifest, index, segments, receipt, profile, context


def _attempts(fixtures, plan):
    fixtures = copy.deepcopy(fixtures)
    fixtures["contract_ref_digest"] = "0" * 64
    fixtures["policy_ref_digest"] = "0" * 64
    # Both references are content addresses from the same unchanged evaluation contract/policy.
    from gah.run_contracts import content_ref
    fixtures["contract_ref_digest"] = content_ref("evaluation_contract", "contract-1", fixtures["contract"])["digest"]
    fixtures["policy_ref_digest"] = content_ref("policy_profile", "policy-1", fixtures["policy"])["digest"]
    return [
        attempt_for(fixtures, plan, "obligation-constraint", attempt_id="part-a", mode="constraint"),
        attempt_for(fixtures, plan, "obligation-mutation", attempt_id="part-b", mode="mutation"),
        attempt_for(fixtures, plan, "obligation-llm", attempt_id="part-c", mode="llm"),
    ]


def _db(path=":memory:"):
    db = sqlite3.connect(path, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("BEGIN IMMEDIATE")
    create_schema(db)
    return db


class PartitionedRunEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.fixtures, self.plan, self.manifest, self.index, self.segments, self.receipt, self.profile, self.context = _setup()
        self.calls = 0
        self.changed = False
        self.expired = False

        def resolver(db, now, receipt, stored_profile):
            self.calls += 1
            if self.expired:
                raise RuntimeError("private currentness detail")
            value = copy.deepcopy(self.context)
            if self.changed:
                value["policy"]["policy_id"] = "stale-policy"
            return value

        self.resolver = resolver
        self.receipt_digest = _pack(self.receipt)[1]

    def book(self, db):
        return PartitionedRunEvidenceBook(
            db, now=100, allowed_bindings={"run-1": self.receipt_digest},
            resolve_context=self.resolver,
        )

    def test_v1_and_partitioned_share_diagnostic_decision_semantics(self):
        from gah.run_contracts import bind_run_manifest
        v1_bound = bind_run_manifest(
            _manifest(self.fixtures, self.plan), self.fixtures["contract"], self.plan,
            self.fixtures["policy"], self.fixtures["registry"], self.fixtures["case_set"],
        )
        self.fixtures["contract_ref_digest"] = v1_bound["manifest"]["contract_ref"]["digest"]
        self.fixtures["policy_ref_digest"] = v1_bound["manifest"]["policy_ref"]["digest"]
        attempts = _attempts(self.fixtures, self.plan)
        legacy_db = _db()
        part_db = _db()
        try:
            legacy = RunEvidenceBook(legacy_db, now=100, allowed_bindings={"run-1": bound_bundle_digest(v1_bound)})
            legacy.start_run(v1_bound, self.profile)
            for item in attempts:
                self.assertTrue(legacy.record_attempt(item)["accepted"])
            legacy_aggregate = legacy.aggregate("run-1")
            legacy_terminal = legacy.finalize("run-1")

            part = self.book(part_db)
            started = part.start_partitioned_run(self.receipt, self.profile)
            self.assertEqual(started["bundle"], self.receipt)
            self.assertNotIn("plan", started["bundle"])
            for item in attempts:
                self.assertTrue(part.record_attempt(item)["accepted"])
            replay = part.record_attempt(attempts[0])
            self.assertTrue(replay["duplicate"])
            aggregate = part.aggregate("run-1")
            terminal = part.finalize("run-1")
            self.assertEqual(aggregate["metrics"], legacy_aggregate["metrics"])
            self.assertEqual(aggregate["counts"], legacy_aggregate["counts"])
            self.assertEqual(terminal["decision"]["assurance"], legacy_terminal["decision"]["assurance"])
            self.assertEqual(terminal["decision"]["metrics"], legacy_terminal["decision"]["metrics"])
            self.assertEqual(part.finalize("run-1"), terminal)
            self.assertEqual(part.get_terminal("run-1"), terminal)
            use = part.current_use("run-1", started["binding"])
            self.assertFalse(use["use"])
            self.assertIn("AUTHORITY_NOT_CONNECTED", use["reasons"])
            self.assertGreaterEqual(self.calls, 10)
            self.assertEqual(part_db.execute("PRAGMA foreign_key_check").fetchall(), [])
            part_db.commit()
        finally:
            legacy_db.close(); part_db.close()

    def test_reopen_replay_caller_mutation_and_default_v1_rejects_v2(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "evidence.sqlite")
            db = _db(path)
            part = self.book(db)
            result = part.start_partitioned_run(self.receipt, self.profile)
            result["bundle"]["selected_controls"].clear()
            again = part.get_run("run-1")
            self.assertTrue(again["bundle"]["selected_controls"])
            duplicate = part.start_partitioned_run(self.receipt, self.profile)
            self.assertEqual(duplicate["bundle_digest"], again["bundle_digest"])
            db.commit(); db.close()

            reopened = _db(path)
            part2 = self.book(reopened)
            self.assertEqual(part2.get_run("run-1")["bundle"], self.receipt)
            with self.assertRaisesRegex(EvidenceError, "^STORAGE_CORRUPT$"):
                RunEvidenceBook(reopened, now=100, allowed_bindings={}).get_run("run-1")
            reopened.rollback(); reopened.close()

    def test_each_operation_re_resolves_and_stale_or_profile_context_fails_closed(self):
        db = _db()
        try:
            part = self.book(db)
            part.start_partitioned_run(self.receipt, self.profile)
            calls_after_start = self.calls
            item = _attempts(self.fixtures, self.plan)[0]
            part.record_attempt(item)
            self.assertGreater(self.calls, calls_after_start)
            self.changed = True
            before = db.execute("SELECT count(*) FROM attempts").fetchone()[0]
            with self.assertRaises(EvidenceError):
                part.aggregate("run-1")
            self.assertEqual(db.execute("SELECT count(*) FROM attempts").fetchone()[0], before)
        finally:
            db.rollback(); db.close()

    def test_scope_profile_and_resolver_expiry_are_rejected(self):
        db = _db()
        try:
            part = self.book(db)
            bad = copy.deepcopy(self.context)
            bad["manifest"]["purpose"] = "regression"
            self.context = bad
            with self.assertRaises(EvidenceError):
                part.start_partitioned_run(self.receipt, self.profile)
            self.context = copy.deepcopy(self.context)
            self.context["manifest"]["purpose"] = "diagnostic"
            self.context["execution_profile"]["fixture_digest"] = "f" * 64
            with self.assertRaisesRegex(EvidenceError, "^PROFILE_MISMATCH$"):
                part.start_partitioned_run(self.receipt, self.profile)
            self.context["execution_profile"] = copy.deepcopy(self.profile)
            self.expired = True
            with self.assertRaisesRegex(EvidenceError, "^CURRENTNESS_UNAVAILABLE$"):
                part.start_partitioned_run(self.receipt, self.profile)
        finally:
            db.rollback(); db.close()

    def test_inherited_v1_start_is_rejected_before_any_row_and_commit(self):
        from gah.run_contracts import bind_run_manifest
        v1 = bind_run_manifest(
            _manifest(self.fixtures, self.plan), self.fixtures["contract"], self.plan,
            self.fixtures["policy"], self.fixtures["registry"], self.fixtures["case_set"],
        )
        v1_digest = bound_bundle_digest(v1)
        db = _db()
        try:
            book = PartitionedRunEvidenceBook(
                db, now=100, allowed_bindings={"run-1": v1_digest},
                resolve_context=self.resolver,
            )
            with self.assertRaisesRegex(EvidenceError, "^PARTITIONED_ENTRY_REQUIRED$"):
                book.start_run(v1, self.profile)
            self.assertEqual(db.execute("SELECT count(*) FROM bound_runs").fetchone()[0], 0)
            db.commit()
            self.assertEqual(db.execute("SELECT count(*) FROM bound_runs").fetchone()[0], 0)
        finally:
            db.close()

    def test_segment_over_limit_rejected_before_partition_canonicalizer(self):
        from unittest.mock import patch
        import gah.partitioned_run_evidence as module
        db = _db()
        try:
            book = self.book(db)
            self.context["segments"] = tuple(object() for _ in range(MAX_SEGMENTS + 1))
            with patch.object(module, "_partitioned_canonical", wraps=module._partitioned_canonical) as canonical:
                with self.assertRaises(EvidenceError):
                    book.start_partitioned_run(self.receipt, self.profile)
                canonical.assert_not_called()
            self.assertEqual(db.execute("SELECT count(*) FROM bound_runs").fetchone()[0], 0)
        finally:
            db.rollback(); db.close()

    def test_constructor_requires_existing_transaction(self):
        db = sqlite3.connect(":memory:", isolation_level=None)
        try:
            with self.assertRaisesRegex(EvidenceError, "^TRANSACTION_REQUIRED$"):
                PartitionedRunEvidenceBook(
                    db, now=100, allowed_bindings={"run-1": self.receipt_digest},
                    resolve_context=self.resolver,
                )
        finally:
            db.close()

    def test_baseline_context_is_rejected(self):
        db = _db()
        try:
            book = self.book(db)
            self.context["baseline_context"] = {"unverified": True}
            with self.assertRaisesRegex(EvidenceError, "^BASELINE_UNSUPPORTED$"):
                book.start_partitioned_run(self.receipt, self.profile)
            self.assertEqual(db.execute("SELECT count(*) FROM bound_runs").fetchone()[0], 0)
        finally:
            db.rollback(); db.close()

    def test_resolver_is_required_on_all_run_reads_and_writes(self):
        db = _db()
        try:
            book = self.book(db)
            started = book.start_partitioned_run(self.receipt, self.profile)
            for item in _attempts(self.fixtures, self.plan):
                book.record_attempt(item)
            book.finalize("run-1")
            attempt_id = "part-a"
            self.expired = True
            operations = [
                lambda: book.get_attempt(attempt_id),
                lambda: book.get_terminal("run-1"),
                lambda: book.record_evidence_state("run-1", {
                    "state": "VALID", "valid_until": 200, "revocation_generation": 0}),
                lambda: book.current_use("run-1", started["binding"]),
                lambda: book.finalize("run-1"),
            ]
            for operation in operations:
                with self.subTest(operation=operation):
                    with self.assertRaisesRegex(EvidenceError, "^CURRENTNESS_UNAVAILABLE$"):
                        operation()
        finally:
            db.rollback(); db.close()

    def test_contract_generation_two_is_outside_this_binding_scope(self):
        db = _db()
        try:
            part = self.book(db)
            self.context["contract"]["generation"] = 2
            with self.assertRaisesRegex(EvidenceError, "^SCOPE_UNSUPPORTED$"):
                part.start_partitioned_run(self.receipt, self.profile)
        finally:
            db.rollback(); db.close()

    def test_digest_corruption_is_rejected_without_mutating_rows(self):
        db = _db()
        try:
            part = self.book(db)
            part.start_partitioned_run(self.receipt, self.profile)
            before = db.execute("SELECT bundle_json,bundle_digest FROM bound_runs WHERE run_id='run-1'").fetchone()
            db.execute("UPDATE bound_runs SET bundle_digest=? WHERE run_id='run-1'", ("0" * 64,))
            with self.assertRaisesRegex(EvidenceError, "^STORAGE_CORRUPT$"):
                part.get_run("run-1")
            self.assertEqual(db.execute("SELECT bundle_json FROM bound_runs WHERE run_id='run-1'").fetchone()[0], before[0])
        finally:
            db.rollback(); db.close()


if __name__ == "__main__":
    unittest.main()
