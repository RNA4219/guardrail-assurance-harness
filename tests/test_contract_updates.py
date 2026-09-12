"""初回baselineの旧契約から次世代契約への純粋preflightを検査する。"""

from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gah.baseline_authority import build_candidate
from gah.contract_updates import bind_contract_transition
from gah.contracts import ContractError, decode_document
from gah.assurance_authority import fixed_profile
from gah.fixture_materialization import build_fixture_pack
from gah.policy import initial_policy_profile
from gah.run_contracts import content_ref


ROOT = Path(__file__).resolve().parents[1]


class ContractUpdateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        policy = initial_policy_profile()
        worker = (ROOT / "fixtures" / "runtime" / "fixture_worker.py").read_bytes()
        lock = decode_document((ROOT / "config" / "fixture-runtime.lock.json").read_bytes())
        full_profile = fixed_profile()
        profile = {
            "fixture_digest": full_profile["fixture_digest"],
            "adapter_digest": full_profile["adapter_digests"][0],
            "isolation_digest": full_profile["isolation_digest"],
        }
        prepared = build_fixture_pack(policy, worker, lock, profile, 100, "transition-run")
        bound = prepared["bound_run"]
        core_fields = (
            "manifest", "contract", "plan", "policy", "registry",
            "case_set", "selected_controls", "ci_eligible",
        )
        core = {field: deepcopy(bound[field]) for field in core_fields}
        manifest_ref = content_ref("run_manifest", bound["manifest"]["run_id"], bound["manifest"])
        decision = {
            "schema_version": 1, "kind": "run_decision", "decision_id": "decision-transition",
            "run_id": bound["manifest"]["run_id"], "purpose": "component_validation",
            "assurance": "HEALTHY", "ci_eligible": False,
        }
        closure = {
            "schema_version": 1, "kind": "resource_closure", "closure_id": "closure-transition",
            "run_id": bound["manifest"]["run_id"], "manifest_digest": manifest_ref["digest"],
            "budget_closure": True,
        }
        evidence = {"evidence_id": "evidence-transition", "valid_until": 3_000_000}
        source = {
            "bound": core, "receipt": {}, "decision": decision, "closure": closure,
            "evidences": [evidence],
        }
        candidate = build_candidate(source, "baseline-transition-series", "baseline-proposal", 100)
        cls.previous = deepcopy(bound["contract"])
        cls.next = deepcopy(cls.previous)
        cls.next["contract_id"] = "fixture-contract-v2"
        cls.next["generation"] = 2
        cls.next["comparison"] = {
            "mode": "required",
            "baseline_ref": content_ref("baseline", candidate["record"]["baseline_id"], candidate["record"]),
            "changed_axes": [],
            "reason": None,
        }
        cls.record = deepcopy(candidate["record"])
        cls.source = core

    def call(self, previous=None, following=None, record=None, source=None):
        return bind_contract_transition(
            self.previous if previous is None else previous,
            self.next if following is None else following,
            baseline_record=self.record if record is None else record,
            baseline_source_bound=self.source if source is None else source,
        )

    def test_valid_transition_rebinds_actual_fixture_and_disables_authority(self):
        result = self.call()
        self.assertTrue(result["structurally_bound"])
        self.assertEqual(result["comparison"], self.next["comparison"])
        self.assertEqual(result["source_bound"], self.source)
        self.assertFalse(result["authority_connected"])
        self.assertFalse(result["adoption_verified"])
        self.assertFalse(result["ci_eligible"])
        self.assertEqual(
            result["previous_contract_ref"],
            content_ref("evaluation_contract", self.previous["contract_id"], self.previous),
        )
        self.assertEqual(
            result["next_contract_ref"],
            content_ref("evaluation_contract", self.next["contract_id"], self.next),
        )
        self.assertEqual(result["source_run_ref"], self.record["source_run_ref"])

    def test_inputs_are_not_mutated(self):
        previous = deepcopy(self.previous)
        following = deepcopy(self.next)
        record = deepcopy(self.record)
        source = deepcopy(self.source)
        self.call(previous, following, record, source)
        self.assertEqual(previous, self.previous)
        self.assertEqual(following, self.next)
        self.assertEqual(record, self.record)
        self.assertEqual(source, self.source)

    def test_contract_id_reuse_and_generation_skip_are_rejected(self):
        same_id = deepcopy(self.next)
        same_id["contract_id"] = self.previous["contract_id"]
        with self.assertRaisesRegex(ContractError, "^CONTRACT_ID_REUSED$"):
            self.call(following=same_id)
        skipped = deepcopy(self.next)
        skipped["generation"] = 3
        with self.assertRaisesRegex(ContractError, "^GENERATION_MISMATCH$"):
            self.call(following=skipped)

    def test_comparison_must_use_this_baseline_with_no_declared_change(self):
        for mutate, expected in (
            (lambda value: value["comparison"].update(changed_axes=["target"]), "COMPARISON_MISMATCH"),
            (lambda value: value["comparison"].update(reason="changed"), "INVALID_CONTRACT"),
            (lambda value: value["comparison"].update(
                baseline_ref=content_ref("baseline", "other-baseline", {"value": 1})),
             "COMPARISON_MISMATCH"),
        ):
            following = deepcopy(self.next)
            mutate(following)
            with self.subTest(expected=expected):
                with self.assertRaisesRegex(ContractError, f"^{expected}$"):
                    self.call(following=following)

    def test_unrelated_policy_evaluator_and_output_changes_are_rejected(self):
        for field, value in (
            ("policy_generation", 2),
            ("evaluator_refs", [content_ref("evaluator", "other", {"v": 1})]),
            ("required_outputs", ["decision", "evidence", "findings", "plans", "run_receipt", "extra"]),
        ):
            following = deepcopy(self.next)
            following[field] = value
            with self.subTest(field=field):
                expected = "INVALID_CONTRACT" if field == "required_outputs" else "UNDECLARED_CONTRACT_CHANGE"
                with self.assertRaisesRegex(ContractError, f"^{expected}$"):
                    self.call(following=following)

    def test_previous_contract_must_match_the_saved_source(self):
        previous = deepcopy(self.previous)
        previous["contract_id"] = "other-contract"
        with self.assertRaisesRegex(ContractError, "^PREVIOUS_CONTRACT_MISMATCH$"):
            self.call(previous=previous)
        with self.assertRaises(ContractError):
            self.call(previous={})

    def test_baseline_record_must_reference_saved_previous_contract_and_source(self):
        record = deepcopy(self.record)
        record["contract_ref"] = content_ref("evaluation_contract", "other-contract", {"v": 1})
        following = deepcopy(self.next)
        following["comparison"]["baseline_ref"] = content_ref(
            "baseline", record["baseline_id"], record
        )
        with self.assertRaisesRegex(ContractError, "^REFERENCE_MISMATCH$"):
            self.call(following=following, record=record)
        record = deepcopy(self.record)
        record["comparison_context_ref"]["digest"] = "0" * 64
        following = deepcopy(self.next)
        following["comparison"]["baseline_ref"] = content_ref(
            "baseline", record["baseline_id"], record
        )
        with self.assertRaisesRegex(ContractError, "^REFERENCE_MISMATCH$"):
            self.call(following=following, record=record)
        record = deepcopy(self.record)
        record["target_refs"] = []
        with self.assertRaisesRegex(ContractError, "^INVALID_REFERENCE_LIST$"):
            self.call(record=record)

    def test_source_is_rebound_and_unknown_or_mutated_fields_are_rejected(self):
        extra = deepcopy(self.source)
        extra["untrusted"] = True
        with self.assertRaisesRegex(ContractError, "^SOURCE_SHAPE$"):
            self.call(source=extra)
        changed = deepcopy(self.source)
        changed["manifest"]["target_refs"] = [content_ref("target", "other", {"v": 1})]
        with self.assertRaisesRegex(ContractError, "^SOURCE_BINDING_INVALID$"):
            self.call(source=changed)

    def test_source_must_be_the_initial_baseline_candidate(self):
        changed = deepcopy(self.source)
        changed["manifest"]["purpose"] = "diagnostic"
        with self.assertRaisesRegex(ContractError, "^BASELINE_SOURCE_MISMATCH$"):
            self.call(source=changed)
        previous = deepcopy(self.previous)
        previous["comparison"]["changed_axes"] = []
        with self.assertRaisesRegex(ContractError, "^PREVIOUS_CONTRACT_MISMATCH$"):
            self.call(previous=previous)

    def test_baseline_generation_and_oracle_refs_are_bound(self):
        record = deepcopy(self.record)
        record["generation"] = 2
        with self.assertRaisesRegex(ContractError, "^BASELINE_GENERATION_MISMATCH$"):
            self.call(record=record)
        record = deepcopy(self.record)
        record["oracle_refs"] = []
        with self.assertRaisesRegex(ContractError, "^INVALID_REFERENCE_LIST$"):
            self.call(record=record)

    def _relinked_transition(self, source):
        """純粋検査用の参照を再計算する。保存Evidenceの検証材料ではない。"""
        previous = deepcopy(source["contract"])
        ref = content_ref("evaluation_contract", previous["contract_id"], previous)
        source["plan"]["contract_ref"] = ref
        source["manifest"]["contract_ref"] = ref
        source["manifest"]["plan_ref"] = content_ref("trial_plan", source["plan"]["plan_id"], source["plan"])
        record = build_candidate({"bound": source, "receipt": {},
            "decision": {"decision_id": "reference-only-decision"},
            "closure": {"closure_id": "reference-only-closure"},
            "evidences": [{"evidence_id": "reference-only-evidence", "valid_until": 3_000_000}]},
            "baseline-transition-series", "baseline-proposal-relinked", 100)["record"]
        following = deepcopy(previous)
        following.update(contract_id="relinked-next-contract", generation=2,
            comparison={"mode": "required", "baseline_ref": content_ref("baseline", record["baseline_id"], record),
                        "reason": None, "changed_axes": []})
        return previous, following, record, source

    def test_relinked_contract_cannot_claim_different_source_documents(self):
        for field in ("registry_ref", "case_set_ref", "policy_ref"):
            with self.subTest(field=field):
                source = deepcopy(self.source)
                source["contract"][field]["digest"] = "0" * 64
                with self.assertRaisesRegex(ContractError, "^REFERENCE_MISMATCH$"):
                    self.call(*self._relinked_transition(source))

    def test_empty_change_set_preserves_comparison_and_strict_types(self):
        source = deepcopy(self.source)
        source["contract"]["comparison"]["changed_axes"] = []
        previous, following, record, source = self._relinked_transition(source)
        self.assertTrue(self.call(previous, following, record, source)["structurally_bound"])
        for key, value in (("generation", True), ("generation", 2**63), ("unexpected", True)):
            with self.subTest(key=key, value=value):
                candidate = deepcopy(following)
                candidate[key] = value
                with self.assertRaises(ContractError):
                    self.call(previous, candidate, record, source)


if __name__ == "__main__":
    unittest.main()
