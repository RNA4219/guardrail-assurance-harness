"""初回baseline authorityのtransaction・binding・採択境界を検査する。"""

from copy import deepcopy
import sqlite3
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from gah import baseline_authority
from gah.adoption import AdoptionError
from gah.contracts import ContractError
from gah.run_evidence import bound_bundle_digest
from test_run_contracts import _fixtures, _manifest, _plan


class FakeStore:
    pass


def graph(now=100):
    fixtures = _fixtures()
    plan = _plan(fixtures)
    from gah.run_contracts import bind_run_manifest, content_ref
    bound = bind_run_manifest(_manifest(fixtures, plan), fixtures["contract"], plan,
                              fixtures["policy"], fixtures["registry"], fixtures["case_set"])
    decision = {"schema_version": 1, "kind": "run_decision", "run_id": "run-1", "decision_id": "decision-1", "assurance": "HEALTHY"}
    manifest_ref = content_ref("run_manifest", "run-1", bound["manifest"])
    evidence = {"schema_version": 1, "kind": "evidence", "evidence_id": "evidence-1", "subject_ref": manifest_ref, "observed_at": 2, "valid_until": 1000}
    closure = {"schema_version": 1, "kind": "resource_closure", "run_id": "run-1", "closure_id": "closure-1", "budget_closure": True}
    receipt = {
        "schema_version": 1, "kind": "authority_run_receipt", "run_id": "run-1",
        "input_materialization_verified": True,
        "manifest_ref": manifest_ref,
        "bundle_ref": {"kind": "bound_bundle", "id": "run-1", "digest": bound_bundle_digest(bound)},
        "decision_ref": __import__("gah.run_contracts", fromlist=["content_ref"]).content_ref("run_decision", "decision-1", decision),
        "evidence_ref": __import__("gah.run_contracts", fromlist=["content_ref"]).content_ref("evidence", "evidence-1", evidence),
        "closure_ref": __import__("gah.run_contracts", fromlist=["content_ref"]).content_ref("resource_closure", "closure-1", closure),
    }
    return {"bound": bound, "receipt": receipt, "decision": decision, "evidences": [evidence], "closure": closure,
            "evidence_states": {"evidence-1": {"revoked": False, "deleted": False}}, "reasons": []}


class BaselineAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:", isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("BEGIN IMMEDIATE")
        baseline_authority.create_schema(self.db)
        self.source = graph()
        self.store = FakeStore()

    def tearDown(self):
        if self.db.in_transaction:
            self.db.rollback()
        self.db.close()

    def req(self, action, request_id, **fields):
        return {"schema_version": 1, "action": action, "request_id": request_id, **fields}

    def resolve(self, run_id):
        self.assertEqual(run_id, "run-1")
        return deepcopy(self.source)

    def test_schema_requires_transaction_and_request_is_strict(self):
        other = sqlite3.connect(":memory:", isolation_level=None)
        with self.assertRaisesRegex(AdoptionError, "^TRANSACTION_REQUIRED$"):
            baseline_authority.create_schema(other)
        other.close()
        with self.assertRaisesRegex(AdoptionError, "^INVALID_REQUEST$"):
            baseline_authority.validate_request({"schema_version": 1, "action": "baseline_current", "request_id": "r", "series_id": "s", "extra": 1})
        tables = {row[0] for row in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertNotIn("baseline_meta", tables)
        self.assertNotIn("baseline_idempotency", tables)

    def test_materialization_incomplete_source_is_structural_proposal_only(self):
        request = self.req("baseline_propose", "proposal-req", proposal_id="proposal-1", series_id="series-1", run_id="run-1", expected_generation=0)
        broken = deepcopy(self.source)
        broken["reasons"] = ["MISSING"]
        result = baseline_authority.execute(self.store, self.db, request, "manager", "manager-context", 100, lambda _: broken)
        self.assertEqual(result["generation"], 1)
        validation = self.req("baseline_validate", "validation-req", proposal_id="proposal-1", validation_id="validation-1")
        with self.assertRaisesRegex(AdoptionError, "^PREREQUISITE_UNAVAILABLE$"):
            baseline_authority.execute(self.store, self.db, validation, "validator", "validator-context", 100, lambda _: broken)

    def test_self_claimed_success_with_incomplete_evidence_cannot_validate(self):
        propose = self.req("baseline_propose", "proposal-req", proposal_id="proposal-1", series_id="series-1", run_id="run-1", expected_generation=0)
        result = baseline_authority.execute(self.store, self.db, propose, "manager", "manager-context", 100, self.resolve)
        self.assertFalse(result["ci_eligible"])
        validation = self.req("baseline_validate", "validation-req", proposal_id="proposal-1", validation_id="validation-1")
        # このgraphは実保存Evidenceの必須fieldを欠く。成功flagだけで採択しない。
        # 実15entryの正常採択はtest_baseline_adoption_integrationで検査する。
        with self.assertRaises(ContractError):
            baseline_authority.execute(self.store, self.db, validation, "validator", "validator-context", 101, self.resolve)
        self.assertEqual(self.db.execute("SELECT count(*) FROM baseline_validations").fetchone()[0], 0)
        self.assertEqual(self.db.execute("SELECT count(*) FROM baseline_adoptions").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
