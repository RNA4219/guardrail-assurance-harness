"""PAC11 report用のcontract/baseline世代をreadonlyで引けることを検査する。"""
from copy import deepcopy
from pathlib import Path
import sqlite3
import sys
from unittest import TestCase, mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gah import run_outputs, resources
from gah.adoption import AdoptionError
from gah.run_contracts import content_ref
from gah.wire import canonical_bytes


def _ref(kind, identifier):
    return content_ref(kind, identifier, {})


def _baseline():
    record = {
        "schema_version": 1, "kind": "baseline", "baseline_id": "baseline-a",
        "baseline_series_id": "baseline-series-a", "generation": 2,
        "contract_ref": _ref("evaluation_contract", "contract-a"),
        "policy_ref": _ref("policy_profile", "policy-a"),
        "registry_ref": _ref("control_registry", "registry-a"),
        "case_set_ref": _ref("case_set", "case-set-a"),
        "target_refs": [_ref("target", "target-a")],
        "evaluator_refs": [_ref("evaluator", "evaluator-a")],
        "oracle_refs": [_ref("oracle", "oracle-a")],
        "repeat_config_ref": _ref("repeat_config", "repeat-a"),
        "source_run_ref": _ref("run_manifest", "run-source-a"),
        "trial_plan_ref": _ref("trial_plan", "plan-a"),
        "decision_ref": _ref("run_decision", "decision-a"),
        "evidence_refs": [_ref("evidence", "evidence-a")],
        "resource_closure_ref": _ref("resource_closure", "closure-a"),
        "comparison_context_ref": _ref("comparison_context", "comparison-a"),
        "created_at": 10, "valid_until": 20,
    }
    return record


class RunArtifactGenerationTests(TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("CREATE TABLE baseline_adoptions(adoption_id TEXT PRIMARY KEY, series_id TEXT NOT NULL, generation INTEGER NOT NULL, proposal_id TEXT NOT NULL, validation_id TEXT NOT NULL, baseline_json TEXT NOT NULL, baseline_digest TEXT NOT NULL, adopted_at INTEGER NOT NULL, actor_id TEXT NOT NULL, context TEXT NOT NULL, permission_generation INTEGER NOT NULL)")
        self.record = _baseline()
        self.contract = {"contract_id": "contract-a", "nested": {"display": "PAC11"}}
        self.contract_ref = content_ref("evaluation_contract", "contract-a", self.contract)
        self.baseline_ref = content_ref("baseline", self.record["baseline_id"], self.record)
        self.bound = {"manifest": {"run_id": "run-a", "contract_ref": self.contract_ref,
                                   "baseline_ref": self.baseline_ref}, "contract": deepcopy(self.contract),
                      "registry": {"controls": []}, "selected_controls": []}
        self.receipt = {"kind": "authority_run_receipt",
            "manifest_ref": _ref("run_manifest", "run-a"),
            "bundle_ref": _ref("bound_bundle", "run-a"),
            "decision_ref": _ref("run_decision", "run-a"),
            "evidence_ref": _ref("evidence_bundle", "run-a"),
            "closure_ref": _ref("resource_closure", "run-a")}
        self.read = mock.patch.object(run_outputs, "read", return_value={})
        self.build = mock.patch.object(run_outputs, "_build", return_value=([], {}))
        self.read.start()
        self.addCleanup(self.read.stop)
        self.build.start()
        self.addCleanup(self.build.stop)

    def tearDown(self):
        self.db.close()

    def _insert_baseline(self, record=None, *, series=None, generation=None,
                         digest_override=None, raw_override=None, adoption_id="adoption-a"):
        value = self.record if record is None else record
        raw, digest = resources._packed(value)
        self.db.execute("INSERT INTO baseline_adoptions VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (adoption_id, series or value["baseline_series_id"],
             value["generation"] if generation is None else generation,
             "proposal-a", "validation-a", raw if raw_override is None else raw_override,
             digest if digest_override is None else digest_override, 11, "manager", "manager-context", 0))
        self.db.commit()
        return raw, digest

    def test_contract_ref_returns_only_bound_contract_after_read_and_isolated(self):
        events = []
        run_outputs.read.side_effect = lambda *_args: events.append("read")
        run_outputs._build.side_effect = lambda *_args: (events.append("build") or ([], {}))
        result = run_outputs.fetch(self.db, self.bound, self.receipt, deepcopy(self.contract_ref))
        self.assertEqual(events, ["read", "build"])
        self.assertEqual(result["artifact"], self.contract)
        self.assertEqual(result["artifact_ref"], self.contract_ref)
        result["artifact"]["nested"]["display"] = "changed"
        self.assertEqual(self.bound["contract"], self.contract)

    def test_contract_requires_content_ref_match_and_rejects_other_run_refs(self):
        bound = deepcopy(self.bound)
        bound["contract"] = deepcopy(self.contract)
        bound["contract"]["nested"]["display"] = "tampered"
        with self.assertRaisesRegex(AdoptionError, "^RUN_OUTPUTS_INVALID$"):
            run_outputs.fetch(self.db, bound, self.receipt, self.contract_ref)
        wrong = content_ref("evaluation_contract", "other-contract", {"contract_id": "other-contract"})
        with self.assertRaisesRegex(AdoptionError, "^RUN_ARTIFACT_MISSING$"):
            run_outputs.fetch(self.db, self.bound, self.receipt, wrong)

    def test_baseline_ref_loads_unique_adopted_record_and_checks_generation_series(self):
        self._insert_baseline()
        result = run_outputs.fetch(self.db, self.bound, self.receipt, self.baseline_ref)
        self.assertEqual(result["artifact_ref"], self.baseline_ref)
        self.assertEqual(result["artifact"], self.record)
        before = self.db.execute("SELECT * FROM baseline_adoptions").fetchall()
        result["artifact"]["baseline_id"] = "mutated"
        self.assertEqual(self.db.execute("SELECT * FROM baseline_adoptions").fetchall(), before)

    def test_baseline_missing_duplicate_and_wrong_reference_are_rejected(self):
        with self.assertRaisesRegex(AdoptionError, "^RUN_ARTIFACT_MISSING$"):
            run_outputs.fetch(self.db, self.bound, self.receipt, self.baseline_ref)
        self._insert_baseline(adoption_id="adoption-a")
        self._insert_baseline(adoption_id="adoption-b")
        with self.assertRaisesRegex(AdoptionError, "^RUN_OUTPUTS_INVALID$"):
            run_outputs.fetch(self.db, self.bound, self.receipt, self.baseline_ref)
        other = content_ref("baseline", "baseline-other", {"baseline_id": "baseline-other"})
        with self.assertRaisesRegex(AdoptionError, "^RUN_ARTIFACT_MISSING$"):
            run_outputs.fetch(self.db, self.bound, self.receipt, other)

    def test_baseline_payload_digest_and_row_binding_corruption_are_rejected(self):
        raw, digest = resources._packed(self.record)
        self._insert_baseline(series="another-series")
        with self.assertRaisesRegex(AdoptionError, "^RUN_OUTPUTS_INVALID$"):
            run_outputs.fetch(self.db, self.bound, self.receipt, self.baseline_ref)
        self.db.execute("DELETE FROM baseline_adoptions")
        self._insert_baseline(generation=1)
        with self.assertRaisesRegex(AdoptionError, "^RUN_OUTPUTS_INVALID$"):
            run_outputs.fetch(self.db, self.bound, self.receipt, self.baseline_ref)
        self.db.execute("DELETE FROM baseline_adoptions")
        self._insert_baseline(digest_override="0" * 64)
        with self.assertRaisesRegex(AdoptionError, "^RUN_ARTIFACT_MISSING$"):
            run_outputs.fetch(self.db, self.bound, self.receipt, self.baseline_ref)
        self.db.execute("DELETE FROM baseline_adoptions")
        self._insert_baseline(raw_override=canonical_bytes({"tampered": True}).decode("utf-8"))
        with self.assertRaisesRegex(AdoptionError, "^RUN_OUTPUTS_INVALID$"):
            run_outputs.fetch(self.db, self.bound, self.receipt, self.baseline_ref)

    def test_manifest_without_baseline_does_not_expose_any_adoption(self):
        bound = deepcopy(self.bound)
        bound["manifest"]["baseline_ref"] = None
        self._insert_baseline()
        with self.assertRaisesRegex(AdoptionError, "^RUN_ARTIFACT_MISSING$"):
            run_outputs.fetch(self.db, bound, self.receipt, self.baseline_ref)


if __name__ == "__main__":
    import unittest
    unittest.main()
