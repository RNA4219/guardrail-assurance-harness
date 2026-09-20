"""Schema-2 source/baseline binding for the gen1-to-gen2 transition."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gah.baselines import repeat_config_for_plan, validate_baseline_record
from gah.contract_updates import bind_contract_transition
from gah.contracts import ContractError
from gah.partitioned_llm_materialization import build
from gah.partitioned_run_contracts import materialize_partitioned_run
from gah.policy import initial_policy_profile
from gah.run_contracts import content_ref

IMAGE_ID = "sha256:" + "a" * 64
WORKER_DIGEST = "b" * 64
ISOLATION = {"schema_version": 1, "kind": "test_isolation", "network": "none"}


class PartitionedContractUpdateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        prepared = build(
            initial_policy_profile(), case_count=400, policy_generation=1,
            run_id="partitioned-transition-source", now=1000,
            target_version="baseline-v1", image_id=IMAGE_ID,
            worker_digest=WORKER_DIGEST, isolation_profile=ISOLATION,
        )
        cls.source = materialize_partitioned_run(
            prepared["manifest"], prepared["contract"], prepared["plan_index"],
            prepared["plan_segments"], prepared["policy"], prepared["registry"],
            prepared["case_set"],
        )
        cls.record, cls.previous, cls.following = cls._baseline_and_next(cls.source)

    @staticmethod
    def _baseline_and_next(source):
        bound = source
        manifest, contract, plan = bound["manifest"], bound["contract"], bound["plan"]
        repeat = repeat_config_for_plan(plan, partitioned_context=bound)
        baseline_id = "partitioned-transition-baseline"
        comparison_id = "partitioned-transition-comparison"
        record = {
            "schema_version": 2, "kind": "baseline", "baseline_id": baseline_id,
            "baseline_series_id": "partitioned-transition-series", "generation": 1,
            "contract_ref": content_ref("evaluation_contract", contract["contract_id"], contract),
            "policy_ref": content_ref("policy_profile", bound["policy"]["policy_id"], bound["policy"]),
            "registry_ref": content_ref("control_registry", bound["registry"]["registry_id"], bound["registry"]),
            "case_set_ref": content_ref("case_set", bound["case_set"]["case_set_id"], bound["case_set"]),
            "target_refs": deepcopy(manifest["target_refs"]),
            "evaluator_refs": deepcopy(contract["evaluator_refs"]),
            "oracle_refs": [],
            "repeat_config_ref": content_ref("repeat_config", repeat["repeat_config_id"], repeat),
            "source_run_ref": content_ref("run_manifest", manifest["run_id"], manifest),
            "trial_plan_ref": deepcopy(manifest["plan_ref"]),
            "decision_ref": content_ref("run_decision", "partitioned-transition-decision", {"id": 1}),
            "evidence_refs": [content_ref("evidence", "partitioned-transition-evidence", {"id": 1})],
            "resource_closure_ref": content_ref("resource_closure", "partitioned-transition-closure", {"id": 1}),
            "comparison_context_ref": {}, "created_at": 1000, "valid_until": 2000,
        }
        seen = set()
        for case in bound["case_set"]["cases"]:
            ref = case["oracle_ref"]
            key = (ref["kind"], ref["id"], ref["digest"])
            if key not in seen:
                seen.add(key)
                record["oracle_refs"].append(deepcopy(ref))
        context = {
            "schema_version": 2, "kind": "comparison_context", "comparison_id": comparison_id,
            "mode": "not_applicable", "baseline_ref": None,
            "reason": "initial_baseline_pending", "changed_axes": deepcopy(contract["comparison"]["changed_axes"]),
            "expected_contract_generation": 1, "expected_baseline_generation": 0,
            "contract_ref": deepcopy(record["contract_ref"]), "policy_ref": deepcopy(record["policy_ref"]),
            "target_refs": deepcopy(record["target_refs"]), "evaluator_refs": deepcopy(record["evaluator_refs"]),
            "case_set_ref": deepcopy(record["case_set_ref"]), "oracle_refs": deepcopy(record["oracle_refs"]),
            "repeat_config_ref": deepcopy(record["repeat_config_ref"]),
        }
        record["comparison_context_ref"] = content_ref("comparison_context", comparison_id, context)
        validate_baseline_record(record)
        following = deepcopy(contract)
        following["contract_id"] = "partitioned-transition-contract-v2"
        following["generation"] = 2
        following["comparison"] = {
            "mode": "required", "baseline_ref": content_ref("baseline", baseline_id, record),
            "changed_axes": [], "reason": None,
        }
        return record, deepcopy(contract), following

    def _bind(self, *, source=None, record=None, previous=None, following=None):
        return bind_contract_transition(
            self.previous if previous is None else previous,
            self.following if following is None else following,
            baseline_record=self.record if record is None else record,
            baseline_source_bound=self.source if source is None else source,
        )

    def test_partitioned_baseline_and_index_ref_bind_without_flat_plan_ref(self):
        result = self._bind()
        self.assertTrue(result["structurally_bound"])
        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(result["comparison"]["baseline_ref"], self.following["comparison"]["baseline_ref"])
        self.assertEqual(result["baseline_record"]["trial_plan_ref"], self.source["manifest"]["plan_ref"])
        self.assertEqual(result["source_bound"], self.source)
        self.assertFalse(result["ci_eligible"])
        self.assertFalse(result["authority_connected"])
        self.assertFalse(result["adoption_verified"])

    def test_mixed_schema_and_mismatched_index_refs_are_rejected(self):
        mixed_source = deepcopy(self.source)
        mixed_source["manifest"]["schema_version"] = 1
        with self.assertRaisesRegex(ContractError, "^SCHEMA_VERSION_MISMATCH$"):
            self._bind(source=mixed_source)
        record = deepcopy(self.record)
        record["trial_plan_ref"] = content_ref("trial_plan_index", "other-index", {"index": 1})
        following = deepcopy(self.following)
        following["comparison"]["baseline_ref"] = content_ref("baseline", record["baseline_id"], record)
        with self.assertRaisesRegex(ContractError, "^PLAN_REFERENCE_MISMATCH$"):
            self._bind(record=record, following=following)

    def test_baseline_registry_and_case_set_refs_must_match_partitioned_source(self):
        for field, kind, source_field in (
            ("registry_ref", "control_registry", "registry"),
            ("case_set_ref", "case_set", "case_set"),
        ):
            with self.subTest(field=field):
                record = deepcopy(self.record)
                source_obj = self.source[source_field]
                identifier_key = "registry_id" if source_field == "registry" else "case_set_id"
                record[field] = content_ref(kind, source_obj[identifier_key], {"different": True})
                following = deepcopy(self.following)
                following["comparison"]["baseline_ref"] = content_ref("baseline", record["baseline_id"], record)
                with self.assertRaisesRegex(ContractError, "^REFERENCE_MISMATCH$"):
                    self._bind(record=record, following=following)
    def test_contract_registry_case_and_evaluator_bindings_cannot_drift(self):
        source = deepcopy(self.source)
        source["_partitioned_context"]["index"]["count"] = 401
        with self.assertRaises(ContractError):
            self._bind(source=source)
        following = deepcopy(self.following)
        following["evaluator_refs"] = [content_ref("evaluator", "other-evaluator", {"v": 1})]
        with self.assertRaisesRegex(ContractError, "^UNDECLARED_CONTRACT_CHANGE$"):
            self._bind(following=following)


if __name__ == "__main__":
    unittest.main()
