"""CI gate が検証済みEvidence撤回を理由として保ち、先行整合性異常を隠さない。"""
from copy import deepcopy
from pathlib import Path
import sqlite3
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gah import regression_runs, transition_acceptance
from gah import assurance_authority, run_cancellation, run_outputs, resources
from gah.adoption import AdoptionError
from tests.test_regression_integration import request


class CIErrorOrderingUnitTests(unittest.TestCase):
    def test_earlier_source_error_does_not_inspect_source_shape(self):
        class UntouchableSource(dict):
            def get(self, *args, **kwargs):
                raise AssertionError("SOURCE_SHAPE_TOUCHED_AFTER_EARLIER_ERROR")

        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.executescript("""
            CREATE TABLE eval_runs(run_id TEXT,manifest_json TEXT,manifest_digest TEXT,
                                   contract_series_id TEXT,contract_generation INTEGER);
            CREATE TABLE resource_runs(run_id TEXT,cancelled INTEGER);
            INSERT INTO eval_runs VALUES('source-order','{}','digest','series',1);
            INSERT INTO resource_runs VALUES('source-order',0);
        """)
        self.addCleanup(db.close)
        manifest = {"purpose": "regression", "run_id": "source-order"}
        source = UntouchableSource(bound={}, decision={"assurance": "HEALTHY"}, receipt={})
        request_value = {
            "action": "ci_check", "request_id": "source-order-check", "run_id": "source-order",
            "expected_manifest_ref": {"kind": "run_manifest", "id": "source-order", "digest": "a" * 64},
            "expected_contract_ref": {"kind": "evaluation_contract", "id": "contract", "digest": "b" * 64},
            "expected_baseline_ref": {"kind": "baseline", "id": "baseline", "digest": "c" * 64},
            "expected_target_refs": [{"kind": "target", "id": "target", "digest": "d" * 64}],
            "expected_use_cases": ["UC-CI"],
        }
        with patch.object(resources, "_unpack", return_value=manifest), \
             patch.object(run_cancellation, "exists", return_value=False), \
             patch.object(assurance_authority, "baseline_source", return_value=source), \
             patch.object(run_outputs, "read", return_value={"outputs_ref": None}), \
             patch.object(transition_acceptance, "_check_source",
                          side_effect=AdoptionError("SOURCE_BINDING_MISMATCH")):
            result = regression_runs.ci_check(object(), db, request_value, 1000)
        self.assertIn("EVIDENCE_UNAVAILABLE", result["reasons"])
        self.assertNotIn("EVIDENCE_REVOKED", result["reasons"])
        self.assertEqual(result["exit_code"], 2)


class CIReasonPreservationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.test_regression_integration import RegressionIntegrationTests
        cls._fixture_class = RegressionIntegrationTests
        cls._fixture_class.setUpClass()
        cls.addClassCleanup(cls._fixture_class.doClassCleanups)

    def setUp(self):
        self.fixture = self._fixture_class("runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_verified_revocation_reason_and_integrity_error_precedence(self):
        run_id = "ci-reason-revoked"
        prepared = self.fixture.prepare(run_id)
        self.fixture.complete(prepared)
        gate = self.fixture.gate_request(prepared)
        initial = self.fixture.store.dispatch(12004, 12004, gate)
        self.assertTrue(initial["ci_eligible"], initial)
        self.assertEqual(initial["exit_code"], 0)

        before = self.fixture.store._db.execute(
            "SELECT payload_json,digest,revoked_at FROM authority_run_receipts WHERE run_id=?",
            (run_id,),
        ).fetchone()
        self.assertIsNotNone(before)
        self.fixture.store.dispatch(12004, 12004, request(
            "evidence_revoke", "ci-reason-revoke", run_id=run_id,
        ))
        after = self.fixture.store._db.execute(
            "SELECT payload_json,digest,revoked_at FROM authority_run_receipts WHERE run_id=?",
            (run_id,),
        ).fetchone()
        self.assertEqual(after[0], before[0])
        self.assertEqual(after[1], before[1])
        self.assertIsNotNone(after[2])

        revoked = self.fixture.store.dispatch(12004, 12004, gate)
        self.assertFalse(revoked["ci_eligible"])
        self.assertFalse(revoked["use"])
        self.assertEqual(revoked["reasons"], ["EVIDENCE_REVOKED"])
        self.assertEqual(revoked["exit_code"], 1)

        # The DB contains a genuinely revoked run. Corrupt only the freshly loaded
        # source/bound relation at the checker boundary to exercise earlier integrity
        # validation; it must be sanitized as unavailable, never relabelled revoked.
        original_check = transition_acceptance._check_source

        def mismatched_bound(db, source, bound, now, *, allow_unhealthy=False):
            corrupted = deepcopy(source)
            corrupted["bound"]["manifest"]["run_id"] = "tampered-source-run"
            return original_check(db, corrupted, bound, now, allow_unhealthy=allow_unhealthy)

        with patch.object(transition_acceptance, "_check_source", side_effect=mismatched_bound):
            invalid = self.fixture.store.dispatch(12004, 12004, gate)
        self.assertFalse(invalid["ci_eligible"])
        self.assertFalse(invalid["use"])
        self.assertIn("EVIDENCE_UNAVAILABLE", invalid["reasons"])
        self.assertNotIn("EVIDENCE_REVOKED", invalid["reasons"])
        self.assertEqual(invalid["exit_code"], 2)


if __name__ == "__main__":
    unittest.main()