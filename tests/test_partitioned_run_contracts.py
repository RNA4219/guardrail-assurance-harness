from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from gah.contracts import ContractError, MAX_DOCUMENT_BYTES
from gah.partitioned_run_contracts import (
    bind_partitioned_run_manifest,
    validate_partitioned_run_manifest,
)
from gah.partitioned_trial_plan import partition_trial_plan
from gah.query_scale_data import build_scale_corpus
from gah.run_contracts import bind_run_manifest, content_ref, validate_run_manifest
from gah.wire import canonical_bytes
from test_run_contracts import _fixtures, _manifest, _plan


def _v2_manifest(
    fixtures: dict,
    index: dict,
    *,
    purpose: str = "baseline_candidate",
    baseline_ref: dict | None = None,
    profile: str = "full",
    deadline: int | None = None,
) -> dict:
    created_at = 100
    if deadline is None:
        deadline = created_at + fixtures["policy"]["profiles"][profile]["elapsed_seconds"]
    return {
        "schema_version": 2,
        "kind": "run_manifest",
        "run_id": "run-1",
        "contract_ref": content_ref("evaluation_contract", fixtures["contract"]["contract_id"], fixtures["contract"]),
        "purpose": purpose,
        "use_cases": copy.deepcopy(fixtures["contract"]["use_cases"]),
        "target_refs": [copy.deepcopy(fixtures["target"])],
        "control_ids": [control["control_id"] for control in fixtures["registry"]["controls"]],
        "baseline_ref": copy.deepcopy(baseline_ref),
        "plan_ref": content_ref("trial_plan_index", index["plan_id"], index),
        "policy_ref": content_ref("policy_profile", fixtures["policy"]["policy_id"], fixtures["policy"]),
        "profile": profile,
        "environment_ref": content_ref("environment", "env-1", {"os": "test"}),
        "actor_context_ref": content_ref("actor_context", "context-1", {"actor": "test"}),
        "created_at": created_at,
        "deadline": deadline,
    }


def _bind(fixtures: dict, manifest: dict, plan: dict, *, segments: list[dict] | None = None) -> dict:
    index, actual_segments = partition_trial_plan(plan)
    if segments is not None:
        actual_segments = segments
    manifest["plan_ref"] = content_ref("trial_plan_index", index["plan_id"], index)
    return bind_partitioned_run_manifest(
        manifest, fixtures["contract"], index, actual_segments, fixtures["policy"],
        fixtures["registry"], fixtures["case_set"],
    )


class PartitionedRunManifestTests(unittest.TestCase):
    def test_consistent_contract_refs_preserve_v1_purpose_stage_binding(self) -> None:
        fixtures = _fixtures()
        plan = _plan(fixtures)
        v1_manifest = _manifest(fixtures, plan)
        expected = bind_run_manifest(
            v1_manifest, fixtures["contract"], plan, fixtures["policy"],
            fixtures["registry"], fixtures["case_set"],
        )
        index, segments = partition_trial_plan(plan)
        manifest = copy.deepcopy(v1_manifest)
        manifest["schema_version"] = 2
        manifest["plan_ref"] = content_ref("trial_plan_index", index["plan_id"], index)
        actual = bind_partitioned_run_manifest(
            manifest, fixtures["contract"], index, segments, fixtures["policy"],
            fixtures["registry"], fixtures["case_set"],
        )
        self.assertEqual(actual["selected_controls"], expected["selected_controls"])
        self.assertEqual(actual["contract_ref"], manifest["contract_ref"])
        self.assertIs(actual["ci_eligible"], False)
        self.assertEqual(actual["kind"], "bound_partitioned_run")
        self.assertEqual(set(actual), {
            "schema_version", "kind", "manifest_ref", "contract_ref", "plan_index_ref",
            "policy_ref", "registry_ref", "case_set_ref", "selected_controls", "ci_eligible",
        })
        self.assertEqual(actual["manifest_ref"], content_ref("run_manifest", manifest["run_id"], manifest))
        self.assertEqual(actual["contract_ref"], content_ref("evaluation_contract", fixtures["contract"]["contract_id"], fixtures["contract"]))
        self.assertEqual(actual["plan_index_ref"], content_ref("trial_plan_index", index["plan_id"], index))
        self.assertEqual(actual["policy_ref"], content_ref("policy_profile", fixtures["policy"]["policy_id"], fixtures["policy"]))
        self.assertEqual(actual["registry_ref"], content_ref("control_registry", fixtures["registry"]["registry_id"], fixtures["registry"]))
        self.assertEqual(actual["case_set_ref"], content_ref("case_set", fixtures["case_set"]["case_set_id"], fixtures["case_set"]))
        self.assertNotIn("plan", actual)
        self.assertNotIn("segments", actual)
        self.assertLessEqual(len(json.dumps(actual, separators=(",", ":")).encode()), 900_000)

    def test_outer_resigning_cannot_hide_inconsistent_contract_refs(self) -> None:
        for field in ("policy_ref", "registry_ref", "case_set_ref"):
            with self.subTest(contract_ref_field=field):
                fixtures = _fixtures()
                fixtures["contract"][field]["digest"] = "0" * 64
                plan = _plan(fixtures)
                index, segments = partition_trial_plan(plan)
                manifest = _v2_manifest(fixtures, index)
                with self.assertRaises(ContractError) as caught:
                    bind_partitioned_run_manifest(
                        manifest, fixtures["contract"], index, segments, fixtures["policy"],
                        fixtures["registry"], fixtures["case_set"],
                    )
                self.assertEqual(caught.exception.code, "REFERENCE_MISMATCH")

    def test_valid_partitioned_plan_over_one_mib_binds(self) -> None:
        fixtures = _fixtures()
        base = _plan(fixtures)
        template = next(entry for entry in base["entries"] if entry["obligation_id"] == "obligation-llm")
        entries = [copy.deepcopy(entry) for entry in base["entries"] if entry["obligation_id"] != "obligation-llm"]
        for number in range(3200):
            item = copy.deepcopy(template)
            item["trial_id"] = f"trial-{number:04d}-" + ("x" * 90)
            entries.append(item)
        base["entries"] = entries
        index, segments = partition_trial_plan(base)
        reconstructed_bytes = index["reconstructed_bytes"]
        self.assertEqual(index["entry_count"], 3202)
        self.assertGreater(reconstructed_bytes, 1_048_576)
        self.assertGreater(len(segments), 1)
        wrong_order = copy.deepcopy(_manifest(fixtures, _plan(fixtures)))
        wrong_order["schema_version"] = 2
        wrong_order["plan_ref"] = content_ref("trial_plan_index", index["plan_id"], index)
        with self.assertRaises(ContractError):
            bind_partitioned_run_manifest(
                wrong_order, fixtures["contract"], index, list(reversed(segments)), fixtures["policy"],
                fixtures["registry"], fixtures["case_set"],
            )
        manifest = copy.deepcopy(_manifest(fixtures, _plan(fixtures)))
        manifest["schema_version"] = 2
        manifest["plan_ref"] = content_ref("trial_plan_index", index["plan_id"], index)
        result = bind_partitioned_run_manifest(
            manifest, fixtures["contract"], index, segments, fixtures["policy"],
            fixtures["registry"], fixtures["case_set"],
        )
        self.assertEqual(result["selected_controls"], ["control-ci", "control-llm"])
        self.assertNotIn("entries", result)

    def test_1600_case_required_variant_plan_and_resigned_coverage_failures(self) -> None:
        fixtures = _fixtures("required")
        scale_case_set = build_scale_corpus(1600)["case_set"]
        self.assertEqual(len(scale_case_set["cases"]), 1600)
        self.assertLessEqual(len(canonical_bytes(scale_case_set)), MAX_DOCUMENT_BYTES)
        fixtures["case_set"] = scale_case_set
        registry = copy.deepcopy(fixtures["registry"])
        llm_control = next(item for item in registry["controls"] if item["control_id"] == "control-llm")
        llm_control["dependencies"] = []
        registry["controls"] = [llm_control]
        fixtures["registry"] = registry
        contract = copy.deepcopy(fixtures["contract"])
        contract["registry_ref"] = content_ref("control_registry", registry["registry_id"], registry)
        contract["case_set_ref"] = content_ref("case_set", scale_case_set["case_set_id"], scale_case_set)
        contract["required_categories"] = copy.deepcopy(scale_case_set["required_categories"])
        contract["evaluator_refs"] = [copy.deepcopy(fixtures["evaluators"][2])]
        fixtures["contract"] = contract

        entries = []
        for case in scale_case_set["cases"]:
            trial_id = "trial-" + case["case_id"] + ("x" * 80)
            for variant in ("candidate", "baseline"):
                entries.append({
                    "obligation_id": "obligation-llm",
                    "case_id": case["case_id"],
                    "trial_id": trial_id,
                    "variant": variant,
                    "stage_ids": [stage["stage_id"] for stage in case["session_steps"]],
                    "required": True,
                    "event_policy": "none",
                    "evaluator_ref": copy.deepcopy(fixtures["evaluators"][2]),
                    "target_ref": copy.deepcopy(fixtures["target"]),
                })
        plan = {
            "schema_version": 1, "kind": "trial_plan", "plan_id": "scale-plan-1600",
            "contract_ref": content_ref("evaluation_contract", contract["contract_id"], contract),
            "entries": entries,
        }
        self.assertEqual(len(entries), 3200)
        self.assertEqual(len({entry["case_id"] for entry in entries}), 1600)
        self.assertEqual(sum(entry["variant"] == "candidate" for entry in entries), 1600)
        self.assertEqual(sum(entry["variant"] == "baseline" for entry in entries), 1600)
        index, segments = partition_trial_plan(plan)
        self.assertGreater(index["reconstructed_bytes"], 1_048_576)
        manifest = _v2_manifest(
            fixtures, index, purpose="regression", baseline_ref=fixtures["baseline"],
        )
        bound = bind_partitioned_run_manifest(
            manifest, contract, index, segments, fixtures["policy"], registry, scale_case_set,
        )
        self.assertEqual(bound["selected_controls"], ["control-llm"])

        missing_baseline = copy.deepcopy(plan)
        removed = False
        kept = []
        for entry in missing_baseline["entries"]:
            if not removed and entry["variant"] == "baseline":
                removed = True
                continue
            kept.append(entry)
        missing_baseline["entries"] = kept
        with self.assertRaises(ContractError) as baseline_error:
            _bind(fixtures, copy.deepcopy(manifest), missing_baseline)
        self.assertEqual(baseline_error.exception.code, "BASELINE_MISSING")

        missing_case = copy.deepcopy(plan)
        first_case_id = missing_case["entries"][0]["case_id"]
        missing_case["entries"] = [entry for entry in missing_case["entries"] if entry["case_id"] != first_case_id]
        with self.assertRaises(ContractError) as coverage_error:
            _bind(fixtures, copy.deepcopy(manifest), missing_case)
        self.assertEqual(coverage_error.exception.code, "CASE_COVERAGE_MISSING")

    def test_semantically_resigned_stage_mutation_is_rejected(self) -> None:
        fixtures = _fixtures()
        plan = _plan(fixtures)
        plan["entries"][0]["stage_ids"] = ["other-stage"]
        manifest = copy.deepcopy(_manifest(fixtures, _plan(fixtures)))
        manifest["schema_version"] = 2
        with self.assertRaises(ContractError) as caught:
            _bind(fixtures, manifest, plan)
        self.assertEqual(caught.exception.code, "STAGE_MISMATCH")

    def test_semantically_resigned_missing_baseline_pair_is_rejected(self) -> None:
        fixtures = _fixtures("required")
        plan = _plan(fixtures, comparison="required", include_baseline=True)
        removed = False
        entries = []
        for entry in plan["entries"]:
            if not removed and entry["obligation_id"] == "obligation-llm" and entry["variant"] == "baseline":
                removed = True
                continue
            entries.append(entry)
        plan["entries"] = entries
        manifest = copy.deepcopy(_manifest(fixtures, _plan(fixtures), purpose="regression"))
        manifest["schema_version"] = 2
        manifest["baseline_ref"] = fixtures["baseline"]
        with self.assertRaises(ContractError) as caught:
            _bind(fixtures, manifest, plan)
        self.assertEqual(caught.exception.code, "BASELINE_MISSING")

    def test_semantically_resigned_missing_required_obligation_is_rejected(self) -> None:
        fixtures = _fixtures()
        plan = _plan(fixtures)
        plan["entries"] = [entry for entry in plan["entries"] if entry["obligation_id"] != "obligation-llm"]
        manifest = copy.deepcopy(_manifest(fixtures, _plan(fixtures)))
        manifest["schema_version"] = 2
        with self.assertRaises(ContractError) as caught:
            _bind(fixtures, manifest, plan)
        self.assertEqual(caught.exception.code, "REQUIRED_OBLIGATION_MISSING")

    def test_manifest_shape_version_refs_and_order_are_strict(self) -> None:
        fixtures = _fixtures()
        index, segments = partition_trial_plan(_plan(fixtures))
        base = copy.deepcopy(_manifest(fixtures, _plan(fixtures)))
        base["schema_version"] = 2
        base["plan_ref"] = content_ref("trial_plan_index", index["plan_id"], index)
        self.assertEqual(validate_partitioned_run_manifest(base), base)
        with self.assertRaises(ContractError) as wrong_v1:
            validate_run_manifest(base)
        self.assertEqual(wrong_v1.exception.code, "UNSUPPORTED_VERSION")
        for mutate in (
            lambda x: x.update(extra=True),
            lambda x: x.pop("environment_ref"),
            lambda x: x.update(schema_version=True),
            lambda x: x.update(created_at=True),
            lambda x: x["plan_ref"].update(kind="trial_plan"),
            lambda x: x.update(deadline=x["created_at"]),
            lambda x: x.update(target_refs=[]),
        ):
            candidate = copy.deepcopy(base)
            mutate(candidate)
            with self.subTest(candidate=candidate):
                with self.assertRaises(ContractError):
                    validate_partitioned_run_manifest(candidate)
        bad_binding_ref = copy.deepcopy(base)
        bad_binding_ref["plan_ref"]["digest"] = "0" * 64
        with self.assertRaises(ContractError):
            bind_partitioned_run_manifest(
                bad_binding_ref, fixtures["contract"], index, segments, fixtures["policy"],
                fixtures["registry"], fixtures["case_set"],
            )
        reordered = copy.deepcopy(base)
        reordered["use_cases"] = list(reversed(reordered["use_cases"]))
        bind_partitioned_run_manifest(
            reordered, fixtures["contract"], index, segments, fixtures["policy"],
            fixtures["registry"], fixtures["case_set"],
        )

    def test_v1_v2_purpose_profile_baseline_deadline_rules_match(self) -> None:
        baseline = _fixtures("required")["baseline"]
        other_baseline = content_ref("baseline", "other-baseline", {"version": 0})
        rows = (
            ("baseline_candidate", "not_applicable", None, "full", "ok", False),
            ("baseline_candidate", "required", baseline, "pr", "BASELINE_CANDIDATE_MISMATCH", False),
            ("contract_old_regression", "required", baseline, "full", "ok", False),
            ("contract_old_regression", "required", baseline, "pr", "CONTRACT_OLD_REGRESSION_MISMATCH", False),
            ("contract_old_regression", "required", None, "full", "CONTRACT_OLD_REGRESSION_MISMATCH", False),
            ("contract_candidate", "required", baseline, "full", "ok", False),
            ("contract_candidate", "required", baseline, "pr", "CONTRACT_CANDIDATE_MISMATCH", False),
            ("contract_candidate", "required", None, "full", "CONTRACT_CANDIDATE_MISMATCH", False),
            ("regression", "required", baseline, "pr", "ok", False),
            ("regression", "required", other_baseline, "full", "BASELINE_MISMATCH", False),
            ("regression", "not_applicable", None, "full", "BASELINE_REQUIRED", False),
            ("diagnostic", "required", baseline, "full", "ok", False),
            ("diagnostic", "required", None, "full", "BASELINE_MISMATCH", False),
            ("diagnostic", "not_applicable", baseline, "full", "BASELINE_NOT_APPLICABLE", False),
            ("calibration", "not_applicable", None, "full", "BOOTSTRAP_REQUIRED", False),
            ("contract_validation", "not_applicable", None, "full", "BOOTSTRAP_REQUIRED", False),
            ("regression", "required", baseline, "full", "DEADLINE_INVALID", True),
        )
        for purpose, mode, manifest_baseline, profile, expected_code, late in rows:
            with self.subTest(purpose=purpose, mode=mode, profile=profile, expected=expected_code):
                fixtures = _fixtures(mode)
                contract = copy.deepcopy(fixtures["contract"])
                contract["comparison"] = {
                    "mode": mode,
                    "baseline_ref": copy.deepcopy(baseline if mode == "required" else None),
                    "changed_axes": ["target"],
                    "reason": None if mode == "required" else "initial_baseline_pending",
                }
                fixtures["contract"] = contract
                plan = _plan(fixtures, comparison=mode, include_baseline=(mode == "required"))
                manifest = _manifest(fixtures, plan, purpose=purpose, profile=profile)
                manifest["baseline_ref"] = copy.deepcopy(manifest_baseline)
                elapsed = fixtures["policy"]["profiles"][profile]["elapsed_seconds"]
                manifest["deadline"] = manifest["created_at"] + elapsed + int(late)

                observed_v1 = ("ok", None)
                try:
                    bind_run_manifest(manifest, contract, plan, fixtures["policy"], fixtures["registry"], fixtures["case_set"])
                except ContractError as exc:
                    observed_v1 = ("error", exc.code)

                index, segments = partition_trial_plan(plan)
                manifest_v2 = _v2_manifest(
                    fixtures, index, purpose=purpose, baseline_ref=manifest_baseline,
                    profile=profile, deadline=manifest["deadline"],
                )
                observed_v2 = ("ok", None)
                try:
                    bind_partitioned_run_manifest(
                        manifest_v2, contract, index, segments, fixtures["policy"],
                        fixtures["registry"], fixtures["case_set"],
                    )
                except ContractError as exc:
                    observed_v2 = ("error", exc.code)
                expected = ("ok", None) if expected_code == "ok" else ("error", expected_code)
                self.assertEqual(observed_v1, expected)
                self.assertEqual(observed_v2, expected)

    def test_mutable_inputs_and_result_are_isolated(self) -> None:
        fixtures = _fixtures()
        plan = _plan(fixtures)
        index, segments = partition_trial_plan(plan)
        manifest = copy.deepcopy(_manifest(fixtures, plan))
        manifest["schema_version"] = 2
        manifest["plan_ref"] = content_ref("trial_plan_index", index["plan_id"], index)
        before = copy.deepcopy((manifest, index, segments, fixtures))
        result = bind_partitioned_run_manifest(
            manifest, fixtures["contract"], index, segments, fixtures["policy"],
            fixtures["registry"], fixtures["case_set"],
        )
        self.assertEqual((manifest, index, segments, fixtures), before)
        result["selected_controls"].append("changed")
        result["manifest_ref"]["digest"] = "0" * 64
        self.assertEqual(manifest["run_id"], "run-1")
        self.assertEqual(index["kind"], "trial_plan_index")


if __name__ == "__main__":
    unittest.main()
