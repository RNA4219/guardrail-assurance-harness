import sqlite3
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gah.adoption import AdoptionError
from gah.partitioned_run_authority import PartitionedRunEvaluationExtension
from gah.partitioned_run_store import ACTIONS, FRESH_ACTIONS, TABLES, _evidence_view, create_schema, has_v2_run_or_bound, validate_request
from gah import run_evidence
from gah.partitioned_plan_store import _exact_extension_digest


def _ref(kind, name):
    return {"kind": kind, "id": name, "digest": "a" * 64}


def _profile():
    return {
        "fixture_digest": "b" * 64,
        "adapter_digests": ["c" * 64],
        "isolation_digest": "d" * 64,
    }


def _manifest():
    return {
        "schema_version": 2,
        "kind": "run_manifest",
        "run_id": "diag-run-01",
        "contract_ref": _ref("evaluation_contract", "contract-01"),
        "purpose": "diagnostic",
        "use_cases": ["UC-LLM"],
        "target_refs": [_ref("target", "target-01")],
        "control_ids": ["CTRL-01"],
        "baseline_ref": None,
        "plan_ref": _ref("trial_plan_index", "plan-01"),
        "policy_ref": _ref("policy_profile", "policy-01"),
        "profile": "full",
        "environment_ref": _ref("environment", "env-01"),
        "actor_context_ref": _ref("actor_context", "actor-context-01"),
        "created_at": 10,
        "deadline": 20,
    }


def _begin():
    return {
        "schema_version": 1,
        "action": "run_begin_v2",
        "request_id": "request-01",
        "manifest": _manifest(),
        "contract_series_id": "series-01",
        "expected_generation": 1,
        "execution_profile": _profile(),
    }


class PartitionedRunAuthorityRequestTests(unittest.TestCase):
    def test_extension_is_opt_in_v6_and_all_v2_actions_are_fresh(self):
        extension = PartitionedRunEvaluationExtension()
        self.assertEqual(extension.schema_version, 6)
        self.assertEqual(TABLES, {"eval_runs_v2": {
            "run_id", "manifest_json", "manifest_digest", "bundle_digest", "contract_series_id",
            "contract_generation", "permission_generation", "extension_digest", "created_at",
        }})
        self.assertTrue(set(ACTIONS).issubset(extension.actions))
        self.assertTrue(set(ACTIONS).issubset(extension.fresh_actions))
        self.assertEqual(FRESH_ACTIONS, set(ACTIONS))

    def test_begin_request_is_closed_and_returns_an_isolated_copy(self):
        request = _begin()
        validated = validate_request(request)
        request["manifest"]["control_ids"].append("MUTATED")
        request["execution_profile"]["adapter_digests"].clear()
        self.assertEqual(validated["manifest"]["control_ids"], ["CTRL-01"])
        self.assertEqual(validated["execution_profile"]["adapter_digests"], ["c" * 64])
        self.assertNotIn("plan", validated)
        self.assertEqual(PartitionedRunEvaluationExtension().validate_request(_begin()), _begin())

    def test_exact_v6_reader_gate_rejects_derived_extensions(self):
        class DerivedExtension(PartitionedRunEvaluationExtension):
            pass
        with self.assertRaises(AdoptionError) as caught:
            _exact_extension_digest(DerivedExtension())
        self.assertEqual(caught.exception.code, "EXTENSION_INVALID")

    def test_begin_rejects_extra_or_missing_fields_and_wrong_versions(self):
        request = _begin()
        for bad in (
            {**request, "plan": {"entries": []}},
            {key: value for key, value in request.items() if key != "execution_profile"},
            {**request, "schema_version": True},
            {**request, "expected_generation": True},
            {**request, "expected_generation": 2},
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(AdoptionError):
                    validate_request(bad)

    def test_manifest_reference_is_the_only_plan_reference(self):
        request = _begin()
        request["expected_generation"] = 2
        with self.assertRaises(AdoptionError) as caught:
            validate_request(request)
        self.assertEqual(caught.exception.code, "SCOPE_UNSUPPORTED")
        request = _begin()
        request["manifest"]["plan_ref"] = _ref("other_kind", "plan-01")
        with self.assertRaises(AdoptionError):
            validate_request(request)
        request = _begin()
        request["plan_ref"] = _ref("trial_plan_index", "plan-01")
        with self.assertRaises(AdoptionError):
            validate_request(request)

    def test_evidence_action_shapes_are_closed_and_fresh(self):
        samples = [
            {"schema_version": 1, "action": "run_status_v2", "request_id": "q1", "run_id": "diag-run-01"},
            {"schema_version": 1, "action": "evidence_record_v2", "request_id": "q2", "run_id": "diag-run-01", "attempt": {}},
            {"schema_version": 1, "action": "evidence_attempt_v2", "request_id": "q3", "attempt_id": "attempt-01"},
            {"schema_version": 1, "action": "evidence_finalize_v2", "request_id": "q4", "run_id": "diag-run-01"},
            {"schema_version": 1, "action": "evidence_terminal_v2", "request_id": "q5", "run_id": "diag-run-01"},
            {"schema_version": 1, "action": "evidence_current_v2", "request_id": "q6", "run_id": "diag-run-01", "expected_bundle_digest": "e" * 64},
        ]
        for sample in samples:
            with self.subTest(action=sample["action"]):
                self.assertEqual(validate_request(sample), sample)
                self.assertIn(sample["action"], FRESH_ACTIONS)
                with self.assertRaises(AdoptionError):
                    validate_request({**sample, "unexpected": None})

    def test_attempt_must_be_bounded_json_object_and_profile_is_validated(self):
        attempt = {"schema_version": 1, "action": "evidence_record_v2", "request_id": "q7",
                   "run_id": "diag-run-01", "attempt": []}
        with self.assertRaises(AdoptionError):
            validate_request(attempt)
        request = _begin()
        request["execution_profile"]["isolation_digest"] = "bad"
        with self.assertRaises(AdoptionError):
            validate_request(request)

    def test_evidence_view_changes_only_outer_schema_and_kind(self):
        legacy = {
            "schema_version": 1, "kind": "run_use_decision", "use": False,
            "decision": {"schema_version": 1, "kind": "diagnostic_decision", "assurance": "HOLD"},
            "ci_eligible": True,
        }
        result = _evidence_view(legacy, "partitioned_run_use_decision")
        self.assertEqual(result["schema_version"], 2)
        self.assertEqual(result["kind"], "partitioned_run_use_decision")
        self.assertIs(result["ci_eligible"], False)
        self.assertEqual(result["decision"], legacy["decision"])
        self.assertEqual(legacy["schema_version"], 1)
        self.assertEqual(legacy["kind"], "run_use_decision")

    def test_cross_version_guard_ignores_valid_v1_bound_and_rejects_corruption(self):
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        try:
            db.execute("CREATE TABLE eval_runs_v2(run_id TEXT PRIMARY KEY)")
            db.execute("CREATE TABLE bound_runs(run_id TEXT PRIMARY KEY,bundle_json TEXT,bundle_digest TEXT)")
            raw, digest = run_evidence._pack({"manifest": {"run_id": "same-id"}})
            db.execute("INSERT INTO bound_runs VALUES(?,?,?)", ("v1-id", raw, digest))
            self.assertFalse(has_v2_run_or_bound(db, "v1-id"))
            raw, digest = run_evidence._pack({
                "schema_version": 2, "kind": "bound_partitioned_run", "ci_eligible": False,
            })
            db.execute("INSERT INTO bound_runs VALUES(?,?,?)", ("v2-id", raw, digest))
            self.assertTrue(has_v2_run_or_bound(db, "v2-id"))
            db.execute("INSERT INTO eval_runs_v2 VALUES(?)", ("route-id",))
            self.assertTrue(has_v2_run_or_bound(db, "route-id"))
            db.execute("INSERT INTO bound_runs VALUES(?,?,?)", ("damaged-id", "{", "0" * 64))
            self.assertTrue(has_v2_run_or_bound(db, "damaged-id"))
        finally:
            db.close()

    def test_schema_creation_requires_caller_transaction_and_exact_route_columns(self):
        db = sqlite3.connect(":memory:", isolation_level=None)
        try:
            with self.assertRaises(AdoptionError):
                create_schema(db)
            db.execute("BEGIN IMMEDIATE")
            create_schema(db)
            columns = {row[1] for row in db.execute("PRAGMA table_info(eval_runs_v2)")}
            self.assertEqual(columns, TABLES["eval_runs_v2"])
            sql = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='eval_runs_v2'").fetchone()[0]
            self.assertIn("DEFERRABLE INITIALLY DEFERRED", sql)
            self.assertIn("REFERENCES bound_runs(run_id)", sql)
        finally:
            if db.in_transaction:
                db.rollback()
            db.close()


if __name__ == "__main__":
    unittest.main()
