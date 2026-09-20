from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gah import run_evidence
from gah.baselines import (
    bind_baseline_record,
    repeat_config_for_plan,
    validate_baseline_record,
    validate_comparison_context,
)
from gah.contracts import ContractError
from gah.llm_materialization import target_document
from gah.partitioned_llm_materialization import build
from gah.partitioned_run_contracts import materialize_partitioned_run
from gah.partitioned_trial_plan import _canonical as canonical_plan
from gah.policy import initial_policy_profile
from gah.run_contracts import content_ref


IMAGE_ID = "sha256:" + "a" * 64
WORKER_DIGEST = "b" * 64
ISOLATION = {"schema_version": 1, "kind": "query_scale_isolation", "network": "none"}


def _large_regression():
    policy = initial_policy_profile()
    before = build(
        policy, case_count=1600, policy_generation=1, run_id="qs-base-1600",
        now=1000, target_version="baseline-v1", image_id=IMAGE_ID,
        worker_digest=WORKER_DIGEST, isolation_profile=ISOLATION,
    )
    baseline_ref = content_ref("baseline", "qs-baseline-v1", {"generation": 1})
    old_target = target_document("baseline-v1", IMAGE_ID)
    after = build(
        policy, case_count=1600, policy_generation=1, run_id="qs-regression-1600",
        now=2000, target_version="degraded-v2", image_id=IMAGE_ID,
        worker_digest=WORKER_DIGEST, isolation_profile=ISOLATION,
        generation=2,
        baseline_context={
            "baseline_ref": baseline_ref,
            "targets": [{"control_id": "LC-query-scale", "target_ref": content_ref(
                "target", old_target["target_id"], old_target
            )}],
            "contract": before["contract"],
        },
    )
    bound = materialize_partitioned_run(
        after["manifest"], after["contract"], after["plan_index"], after["plan_segments"],
        after["policy"], after["registry"], after["case_set"],
        baseline_context=after["baseline_context"],
    )
    return after, bound, baseline_ref


def _oracle_refs(case_set):
    refs = []
    seen = set()
    for case in case_set["cases"]:
        ref = case["oracle_ref"]
        key = (ref["kind"], ref["id"], ref["digest"])
        if key not in seen:
            seen.add(key)
            refs.append(deepcopy(ref))
    return refs


def _comparison_context(bound, repeat, baseline_ref):
    manifest = bound["manifest"]
    contract = bound["contract"]
    return {
        "schema_version": 2,
        "kind": "comparison_context",
        "comparison_id": "comparison-qs-regression",
        "mode": "required",
        "baseline_ref": deepcopy(baseline_ref),
        "reason": None,
        "changed_axes": ["target"],
        "expected_contract_generation": contract["generation"],
        "expected_baseline_generation": 1,
        "contract_ref": deepcopy(manifest["contract_ref"]),
        "policy_ref": deepcopy(manifest["policy_ref"]),
        "target_refs": deepcopy(manifest["target_refs"]),
        "evaluator_refs": deepcopy(contract["evaluator_refs"]),
        "case_set_ref": content_ref("case_set", bound["case_set"]["case_set_id"], bound["case_set"]),
        "oracle_refs": _oracle_refs(bound["case_set"]),
        "repeat_config_ref": content_ref("repeat_config", repeat["repeat_config_id"], repeat),
    }


def _v2_bundle(after, bound, baseline_ref):
    baseline_context = after["baseline_context"]
    repeat = repeat_config_for_plan(
        bound["plan"], partitioned_context=bound, baseline_context=baseline_context
    )
    context = _comparison_context(bound, repeat, baseline_ref)
    bound = deepcopy(bound)
    bound["repeat_config"] = repeat
    bound["comparison_context"] = context
    runtime_fields = (
        "manifest", "contract", "plan", "policy", "registry", "case_set",
        "selected_controls", "ci_eligible", "_partitioned_context", "_partitioned_receipt",
    )
    runtime_bound = {field: bound[field] for field in runtime_fields}
    storage = run_evidence.bound_storage_document(runtime_bound, baseline_context=baseline_context)
    manifest = bound["manifest"]
    run_id = manifest["run_id"]
    decision = {
        "schema_version": 1, "kind": "run_decision", "decision_id": "decision-qs-regression",
        "run_id": run_id, "purpose": "normal", "assurance": "HEALTHY", "ci_eligible": False,
    }
    closure = {
        "schema_version": 1, "kind": "resource_closure", "closure_id": "closure-qs-regression",
        "run_id": run_id, "manifest_digest": content_ref("run_manifest", run_id, manifest)["digest"],
        "budget_closure": True,
    }
    evidence = {
        "schema_version": 1, "kind": "evidence", "evidence_id": "evidence-qs-regression",
        "subject_ref": content_ref("run_manifest", run_id, manifest),
        "conditions_ref": content_ref("bound_bundle", run_id, storage),
        "decision_ref": content_ref("run_decision", decision["decision_id"], decision),
        "closure_ref": content_ref("resource_closure", closure["closure_id"], closure),
        "purpose": "normal", "observed_at": 2000, "collected_at": 2000,
        "retention_until": 5000000, "valid_until": 5000000, "permission_generation": 1,
        "authority_connected": True, "resource_closure_verified": True,
        "input_materialization_verified": True, "ci_eligible": False,
    }
    record = {
        "schema_version": 2, "kind": "baseline", "baseline_id": "baseline-qs-next",
        "baseline_series_id": "baseline-qs-series", "generation": 2,
        "contract_ref": deepcopy(manifest["contract_ref"]),
        "policy_ref": deepcopy(manifest["policy_ref"]),
        "registry_ref": content_ref("control_registry", bound["registry"]["registry_id"], bound["registry"]),
        "case_set_ref": content_ref("case_set", bound["case_set"]["case_set_id"], bound["case_set"]),
        "target_refs": deepcopy(manifest["target_refs"]),
        "evaluator_refs": deepcopy(bound["contract"]["evaluator_refs"]),
        "oracle_refs": _oracle_refs(bound["case_set"]),
        "repeat_config_ref": content_ref("repeat_config", repeat["repeat_config_id"], repeat),
        "source_run_ref": content_ref("run_manifest", run_id, manifest),
        "trial_plan_ref": deepcopy(manifest["plan_ref"]),
        "decision_ref": content_ref("run_decision", decision["decision_id"], decision),
        "evidence_refs": [content_ref("evidence", evidence["evidence_id"], evidence)],
        "resource_closure_ref": content_ref("resource_closure", closure["closure_id"], closure),
        "comparison_context_ref": content_ref("comparison_context", context["comparison_id"], context),
        "created_at": 2000, "valid_until": 2000 + 86400,
    }
    return bound, context, record, decision, evidence, closure


class PartitionedBaselineTests(unittest.TestCase):
    def test_v1_baseline_reference_kind_remains_trial_plan(self):
        record = {
            "schema_version": 1, "kind": "baseline", "baseline_id": "baseline-v1",
            "baseline_series_id": "series-v1", "generation": 1,
            "contract_ref": {"kind": "evaluation_contract", "id": "c", "digest": "a" * 64},
            "policy_ref": {"kind": "policy_profile", "id": "p", "digest": "a" * 64},
            "registry_ref": {"kind": "control_registry", "id": "r", "digest": "a" * 64},
            "case_set_ref": {"kind": "case_set", "id": "cs", "digest": "a" * 64},
            "target_refs": [{"kind": "target", "id": "t", "digest": "a" * 64}],
            "evaluator_refs": [{"kind": "evaluator", "id": "e", "digest": "a" * 64}],
            "oracle_refs": [{"kind": "oracle", "id": "o", "digest": "a" * 64}],
            "repeat_config_ref": {"kind": "repeat_config", "id": "rc", "digest": "a" * 64},
            "source_run_ref": {"kind": "run_manifest", "id": "run", "digest": "a" * 64},
            "trial_plan_ref": {"kind": "trial_plan", "id": "plan", "digest": "a" * 64},
            "decision_ref": {"kind": "run_decision", "id": "d", "digest": "a" * 64},
            "evidence_refs": [{"kind": "evidence", "id": "ev", "digest": "a" * 64}],
            "resource_closure_ref": {"kind": "resource_closure", "id": "run", "digest": "a" * 64},
            "comparison_context_ref": {"kind": "comparison_context", "id": "cc", "digest": "a" * 64},
            "created_at": 1, "valid_until": 2,
        }
        self.assertEqual(validate_baseline_record(record), record)
        bad = deepcopy(record)
        bad["trial_plan_ref"]["kind"] = "trial_plan_index"
        with self.assertRaises(ContractError):
            validate_baseline_record(bad)

    def test_schema_two_uses_only_index_ref_and_same_comparison_fields(self):
        after, bound, baseline_ref = _large_regression()
        bound, context, record, _, _, _ = _v2_bundle(after, bound, baseline_ref)
        self.assertEqual(len(bound["plan"]["entries"]), 3200)
        self.assertGreater(len(canonical_plan(bound["plan"], maximum=8 * 1024 * 1024)), 1_048_576)
        self.assertEqual(record["trial_plan_ref"]["kind"], "trial_plan_index")
        self.assertEqual(record["trial_plan_ref"], bound["manifest"]["plan_ref"])
        self.assertEqual(validate_baseline_record(record), record)
        self.assertEqual(validate_comparison_context(context), context)
        repeat = bound["repeat_config"]
        self.assertEqual(repeat["schema_version"], 2)
        self.assertEqual(repeat["plan_ref"], bound["manifest"]["plan_ref"])
        with self.assertRaises(ContractError):
            validate_baseline_record({**record, "trial_plan_ref": {
                "kind": "trial_plan", "id": record["trial_plan_ref"]["id"],
                "digest": record["trial_plan_ref"]["digest"],
            }})
        # 単独contextにはplanの実体がなく、版不一致はrecordとの結合時に拒否する。
        self.assertEqual(validate_comparison_context({**context, "schema_version": 1})["schema_version"], 1)

    def test_v2_binding_checks_index_case_evaluator_and_saved_evidence_refs(self):
        after, bound, baseline_ref = _large_regression()
        bound, context, record, decision, evidence, closure = _v2_bundle(after, bound, baseline_ref)
        result = bind_baseline_record(
            record, bound_run=bound, decision=decision, evidences=[evidence], closure=closure,
            evidence_states={evidence["evidence_id"]: {"revoked": False, "deleted": False}},
            now=2000, baseline_context=after["baseline_context"],
        )
        self.assertTrue(result["structurally_bound"])
        self.assertFalse(result["adoption_verified"])
        self.assertFalse(result["authority_connected"])
        self.assertFalse(result["ci_eligible"])
        self.assertEqual(result["record"]["trial_plan_ref"], bound["manifest"]["plan_ref"])

        for field, bad_ref in (
            ("trial_plan_ref", {"kind": "trial_plan_index", "id": "other-index", "digest": "c" * 64}),
            ("case_set_ref", {"kind": "case_set", "id": "other-cases", "digest": "d" * 64}),
            ("evaluator_refs", [{"kind": "evaluator", "id": "other-evaluator", "digest": "e" * 64}]),
        ):
            bad = deepcopy(record)
            bad[field] = bad_ref
            with self.subTest(field=field), self.assertRaises(ContractError):
                bind_baseline_record(
                    bad, bound_run=bound, decision=decision, evidences=[evidence], closure=closure,
                    evidence_states={evidence["evidence_id"]: {"revoked": False, "deleted": False}},
                    now=2000, baseline_context=after["baseline_context"],
                )

        with self.assertRaises(ContractError):
            bind_baseline_record(record, bound_run={**bound, "comparison_context": {**context, "schema_version": 1}},
                decision=decision, evidences=[evidence], closure=closure, now=2000,
                evidence_states={evidence["evidence_id"]: {"revoked": False, "deleted": False}},
                baseline_context=after["baseline_context"])
        with self.assertRaises(ContractError):
            bind_baseline_record(
                {**record, "schema_version": 1}, bound_run=bound, decision=decision,
                evidences=[evidence], closure=closure,
                evidence_states={evidence["evidence_id"]: {"revoked": False, "deleted": False}},
                now=2000, baseline_context=after["baseline_context"],
            )


if __name__ == "__main__":
    unittest.main()
