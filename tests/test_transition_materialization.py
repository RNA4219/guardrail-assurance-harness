from copy import deepcopy
import hashlib
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gah.baseline_authority import build_candidate
from gah.contracts import ContractError, decode_document
from gah.assurance_authority import fixed_profile
from gah.fixture_materialization import build_fixture_pack
from gah.policy import initial_policy_profile
from gah.run_contracts import content_ref
from gah.transition_materialization import build_transition_runs


class TransitionMaterializationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.worker = (ROOT / "fixtures" / "runtime" / "fixture_worker.py").read_bytes()
        cls.lock = decode_document((ROOT / "config" / "fixture-runtime.lock.json").read_bytes())
        profile = fixed_profile()
        cls.profile = {"fixture_digest": profile["fixture_digest"],
                       "adapter_digest": profile["adapter_digests"][0],
                       "isolation_digest": profile["isolation_digest"]}
        cls.prepared = build_fixture_pack(initial_policy_profile(), cls.worker, cls.lock,
                                          cls.profile, 100, "transition-source")
        bound = cls.prepared["bound_run"]
        core = {key: deepcopy(bound[key]) for key in (
            "manifest", "contract", "plan", "policy", "registry", "case_set",
            "selected_controls", "ci_eligible")}
        manifest_ref = content_ref("run_manifest", bound["manifest"]["run_id"], bound["manifest"])
        source = {"bound": core, "receipt": {},
                  "decision": {"schema_version": 1, "kind": "run_decision",
                               "decision_id": "transition-decision", "run_id": bound["manifest"]["run_id"],
                               "purpose": "component_validation", "assurance": "HEALTHY", "ci_eligible": False},
                  "closure": {"schema_version": 1, "kind": "resource_closure",
                              "closure_id": "transition-closure", "run_id": bound["manifest"]["run_id"],
                              "manifest_digest": manifest_ref["digest"], "budget_closure": True},
                  "evidences": [{"evidence_id": "transition-evidence", "valid_until": 3_000_000}]}
        candidate = build_candidate(source, "transition-baseline-series", "transition-baseline-proposal", 100)
        cls.previous = deepcopy(bound["contract"])
        cls.next = deepcopy(cls.previous)
        cls.next["contract_id"] = "transition-contract-v2"
        cls.next["generation"] = 2
        cls.next["comparison"] = {"mode": "required",
                                   "baseline_ref": content_ref("baseline", candidate["record"]["baseline_id"], candidate["record"]),
                                   "changed_axes": [], "reason": None}
        cls.record = deepcopy(candidate["record"])

    def build(self, **changes):
        args = {"previous_contract": self.previous, "next_contract": self.next,
                "baseline_record": self.record, "source_prepared": self.prepared,
                "worker_source": self.worker, "runtime_lock": self.lock,
                "execution_profile": self.profile, "now": 100,
                "old_run_id": "transition-old-run", "new_run_id": "transition-new-run"}
        args.update(changes)
        return build_transition_runs(**args)

    def test_builds_old_15_and_new_30_with_bound_purpose(self):
        value = self.build()
        self.assertTrue(value["structurally_bound"])
        self.assertFalse(value["authority_connected"])
        self.assertFalse(value["ci_eligible"])
        self.assertEqual(value["old"]["bound_run"]["manifest"]["purpose"], "contract_old_regression")
        self.assertEqual(value["new"]["bound_run"]["manifest"]["purpose"], "contract_candidate")
        self.assertEqual(len(value["old"]["materialization"]["manifest"]["records"]), 15)
        records = value["new"]["materialization"]["manifest"]["records"]
        self.assertEqual(len(records), 30)
        self.assertEqual(sum(r["variant"] == "baseline" for r in records), 15)
        self.assertEqual(sum(r["variant"] == "candidate" for r in records), 15)

    def test_new_context_and_contract_refs_are_exact(self):
        value = self.build()
        new = value["new"]["bound_run"]
        expected = content_ref("baseline", self.record["baseline_id"], self.record)
        self.assertEqual(value["new"]["baseline_context"]["baseline_ref"], expected)
        self.assertEqual(new["manifest"]["baseline_ref"], expected)
        self.assertEqual(new["contract"], self.next)
        self.assertEqual(new["plan"]["contract_ref"], content_ref("evaluation_contract", self.next["contract_id"], self.next))

    def test_materialization_records_preserve_source_payload_refs(self):
        value = self.build()
        source_materials = {m["scenario"]: m for m in self.prepared["pack"]["materials"]}
        for record in value["new"]["materialization"]["manifest"]["records"]:
            material = source_materials[record["scenario"]]
            self.assertEqual(record["input_ref"], content_ref("input", material["case_id"], material["input_payload"]))
            self.assertEqual(record["oracle_ref"], content_ref("oracle", material["case_id"], material["oracle_payload"]))

    def test_ids_must_be_distinct_from_source_and_each_other(self):
        source_id = self.prepared["bound_run"]["manifest"]["run_id"]
        for kwargs in ({"old_run_id": source_id}, {"new_run_id": source_id},
                       {"new_run_id": "transition-old-run"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ContractError):
                self.build(**kwargs)
        with self.assertRaises(ContractError):
            self.build(old_run_id="x" * 129)

    def test_baseline_or_source_mutation_is_rejected(self):
        bad = deepcopy(self.record)
        bad["target_refs"] = []
        with self.assertRaises(ContractError):
            self.build(baseline_record=bad)
        changed = deepcopy(self.prepared)
        changed["bound_run"]["case_set"]["cases"][0]["case_id"] = "changed-case"
        with self.assertRaises(ContractError):
            self.build(source_prepared=changed)
        with self.assertRaises(ContractError):
            self.build(source_prepared={"bound_run": [], "pack": {}, "calibration_case_set": {}})

    def test_next_target_change_is_rejected(self):
        changed = deepcopy(self.next)
        changed["policy_generation"] = 2
        with self.assertRaises(ContractError):
            self.build(next_contract=changed)

    def test_source_metadata_and_clock_boundaries_are_rejected(self):
        changed = deepcopy(self.prepared)
        changed["worker_source_digest"] = "0" * 64
        with self.assertRaises(ContractError):
            self.build(source_prepared=changed)
        changed = deepcopy(self.prepared)
        changed["unexpected"] = True
        with self.assertRaises(ContractError):
            self.build(source_prepared=changed)
        changed = deepcopy(self.prepared)
        changed["bound_run"]["unexpected"] = True
        with self.assertRaises(ContractError):
            self.build(source_prepared=changed)
        for now in (True, 2 ** 53, 99):
            with self.subTest(now=now), self.assertRaises(ContractError):
                self.build(now=now)

    def test_old_and_new_manifest_refs_are_run_specific(self):
        value = self.build()
        for side in ("old", "new"):
            bound = value[side]["bound_run"]
            materialization = value[side]["materialization"]
            self.assertEqual(materialization["manifest"]["run_id"], bound["manifest"]["run_id"])
            self.assertEqual(materialization["manifest_ref"], content_ref(
                "fixture_manifest", materialization["manifest"]["materialization_id"], materialization["manifest"]))
            self.assertEqual(materialization["pack_ref"], self.prepared["pack_ref"])


if __name__ == "__main__":
    unittest.main()
