"""通常authorityで固定400 partitioned LLM runを全件実行しbaselineまで結ぶ。"""
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gah.adoption import AdoptionError
from gah.run_contracts import content_ref
from tests import test_fixture_admission as fixture
from tests.partitioned_case_helpers import (complete_all_partitioned_entries, resolve_prepared_root,
    PartitionedRuntime, SyntheticPartitionedRunner)
from tools.gah_run import execute


request = fixture.request


class PartitionedTransitionIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.FixtureAdmissionTests(
            "test_prepare_contract_adoption_and_run_begin_use_real_bound_objects"
        )
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.store = self.fixture.open()
        self.addCleanup(self.store.close)
        self.fixture.adopt_policy(self.store)

    def _prepare_adopt_begin_open(self, run_id, target_version="baseline-v1"):
        response = self.store.dispatch(12001, 12001, request(
            "guardrail_prepare_partitioned", "prepare-" + run_id, run_id=run_id,
            policy_series_id=self.fixture.policy["policy_id"], target_version=target_version,
            case_count=400,
        ))
        prepared_summary = response["prepared"]
        self.assertEqual(prepared_summary["kind"], "partitioned_guardrail_preparation")
        self.assertEqual(prepared_summary["admission_index"]["case_count"], 400)
        contract = prepared_summary["contract"]
        series_id = "contract-series-" + run_id
        proposal_id = "contract-proposal-" + run_id
        validation_id = "contract-validation-" + run_id
        self.store.dispatch(12001, 12001, request(
            "contract_propose", "contract-propose-" + run_id,
            proposal_id=proposal_id, series_id=series_id, expected_generation=0,
            contract=contract,
        ))
        validated = self.store.dispatch(12003, 12003, request(
            "contract_validate", "contract-validate-" + run_id,
            proposal_id=proposal_id, validation_id=validation_id,
        ))
        self.assertTrue(validated["passed"])
        adopted = self.store.dispatch(12001, 12001, request(
            "contract_adopt", "contract-adopt-" + run_id,
            proposal_id=proposal_id, validation_id=validation_id, expected_generation=0,
        ))
        self.assertEqual(adopted["generation"], 1)
        manifest = prepared_summary["manifest"]
        begin_request_id = "run-begin-" + run_id
        begun = self.store.dispatch(12004, 12004, request(
            "run_begin_partitioned", begin_request_id, run_id=run_id,
            contract_series_id=series_id,
            expected_manifest_ref=content_ref("run_manifest", run_id, manifest),
        ))
        self.assertEqual(begun["run_id"], run_id)
        opened = self.store.dispatch(12004, 12004, request(
            "evidence_open", "evidence-open-" + run_id, run_id=run_id,
        ))
        self.assertEqual(opened["run_id"], run_id)
        return prepared_summary, series_id, begin_request_id

    def _close_and_finalize(self, run_id, owner_id):
        owner = self.store.dispatch(12004, 12004, request(
            "resource_claim", "close-claim-" + run_id, run_id=run_id,
            owner_id=owner_id, recovery=False,
        ))
        closed = self.store.dispatch(12004, 12004, request(
            "resource_close", "resource-close-" + run_id, run_id=run_id,
            owner_id=owner_id, owner_epoch=owner["owner_epoch"],
        ))
        self.assertTrue(closed["budget_closure"])
        return self.store.dispatch(12004, 12004, request(
            "evidence_finalize", "evidence-finalize-" + run_id, run_id=run_id,
        ))

    def test_all_400_cases_close_finalize_and_adopt_baseline(self):
        run_id = "partitioned-llm-full-400"
        summary, contract_series_id, owner_id = self._prepare_adopt_begin_open(run_id)
        from gah import partitioned_llm_admission
        full = partitioned_llm_admission.for_run(
            self.store._db, run_id, self.fixture.clock.value
        )["prepared"]
        self.assertEqual(full["materialization"]["planned_trials"], 400)
        processed = complete_all_partitioned_entries(
            self.store, full, case_count=400, owner_id=owner_id, clock=self.fixture.clock,
            request=request, request_prefix="partitioned-llm-full",
        )
        self.assertEqual(processed["planned_entries"], 400)
        self.assertEqual(processed["completed_entries"], 400)
        self.assertEqual(processed["recorded_attempts"], 400)
        self.assertEqual(processed["prepared_cases_constructed"], 1)
        self.assertEqual(self.store._db.execute(
            "SELECT COUNT(*) FROM attempts WHERE run_id=?", (run_id,)
        ).fetchone()[0], 400)

        final = self._close_and_finalize(run_id, owner_id)
        self.assertTrue(final["authority_connected"])
        self.assertFalse(final["ci_eligible"])
        self.assertTrue(final["input_materialization_verified"])
        receipt_row = self.store._db.execute(
            "SELECT payload_json FROM authority_run_receipts WHERE run_id=?", (run_id,)
        ).fetchone()
        self.assertIsNotNone(receipt_row)
        receipt = json.loads(receipt_row["payload_json"])
        self.assertFalse(receipt["ci_eligible"])
        bound_row = self.store._db.execute(
            "SELECT bundle_json FROM bound_runs WHERE run_id=?", (run_id,)
        ).fetchone()
        self.assertIsNotNone(bound_row)
        self.assertLessEqual(len(bound_row["bundle_json"].encode("utf-8")), 1024 * 1024)
        bundle = json.loads(bound_row["bundle_json"])
        self.assertNotIn("plan", bundle)
        self.assertNotIn("case_set", bundle)

        baseline_series = "partitioned-llm-baseline-series"
        proposal_id = "partitioned-llm-baseline-proposal"
        validation_id = "partitioned-llm-baseline-validation"
        proposed = self.store.dispatch(12001, 12001, request(
            "baseline_propose", "baseline-propose-" + run_id, proposal_id=proposal_id,
            series_id=baseline_series, run_id=run_id, expected_generation=0,
        ))
        self.assertEqual(proposed["generation"], 1)
        validated = self.store.dispatch(12003, 12003, request(
            "baseline_validate", "baseline-validate-" + run_id,
            proposal_id=proposal_id, validation_id=validation_id,
        ))
        self.assertTrue(validated["passed"])
        adopted = self.store.dispatch(12001, 12001, request(
            "baseline_adopt", "baseline-adopt-" + run_id,
            proposal_id=proposal_id, validation_id=validation_id, expected_generation=0,
        ))
        self.assertTrue(adopted["adoption_verified"])
        self.assertFalse(adopted["ci_eligible"])
        current = self.store.dispatch(12004, 12004, request(
            "baseline_current", "baseline-current-" + run_id, series_id=baseline_series,
        ))
        self.assertTrue(current["valid"])
        baseline = current["baseline"]
        used = self.store.dispatch(12004, 12004, request(
            "baseline_use", "baseline-use-" + run_id, series_id=baseline_series,
            expected_baseline_ref=content_ref("baseline", baseline["baseline_id"], baseline),
            expected_contract_ref=baseline["contract_ref"],
        ))
        self.assertTrue(used["use"])
        self.assertFalse(used["ci_eligible"])
        self.assertEqual(summary["manifest"]["contract_ref"], content_ref(
            "evaluation_contract", summary["contract"]["contract_id"], summary["contract"]
        ))
        self.assertEqual(contract_series_id, self.store._db.execute(
            "SELECT contract_series_id FROM eval_runs WHERE run_id=?", (run_id,)
        ).fetchone()["contract_series_id"])

        self._compare_and_run(summary, contract_series_id, baseline, baseline_series)

    def _compare_and_run(self, summary, contract_series_id, baseline, baseline_series):
        # Continue the independently adopted baseline through the real comparison path.
        baseline_ref = content_ref("baseline", baseline["baseline_id"], baseline)
        previous = summary["contract"]
        following = json.loads(json.dumps(previous))
        following.update(contract_id="partitioned-llm-same-target-v2", generation=2,
            comparison={"mode": "required", "baseline_ref": baseline_ref,
                         "changed_axes": [], "reason": None})
        following_proposal = "partitioned-llm-same-target-proposal"
        self.store.dispatch(12001, 12001, request(
            "contract_propose", "partitioned-llm-same-target-propose",
            proposal_id=following_proposal, series_id=contract_series_id,
            expected_generation=1, contract=following,
        ))
        following_ref = content_ref("evaluation_contract", following["contract_id"], following)
        transition_ids = {"old": "partitioned-llm-v2-old", "new": "partitioned-llm-v2-new"}
        candidate_id = "partitioned-llm-v2-candidate"
        candidate_prepared = self.store.dispatch(12003, 12003, request(
            "contract_candidate_prepare", "partitioned-llm-v2-candidate-prepare",
            candidate_id=candidate_id, proposal_id=following_proposal,
            baseline_series_id=baseline_series, expected_contract_ref=content_ref(
                "evaluation_contract", previous["contract_id"], previous),
            expected_baseline_ref=baseline_ref, old_run_id=transition_ids["old"],
            new_run_id=transition_ids["new"],
        ))
        self.assertFalse(candidate_prepared["adoption_verified"])
        candidate_roots = {}
        for side in ("old", "new"):
            read = self.store.dispatch(12004, 12004, request(
                "contract_candidate_read", "partitioned-llm-v2-read-" + side,
                candidate_id=candidate_id, side=side,
            ))
            root = read["prepared"]
            self.assertEqual(root["kind"], "partitioned_prepared_run")
            self.assertEqual(root["run_id"], transition_ids[side])
            self.assertEqual(root["binding"]["manifest_ref"]["kind"], "run_manifest")
            # The compact root is intentionally resolved through the public reader,
            # never by querying eval_objects or candidate_sections directly.
            candidate_roots[side] = root
            self.assertFalse(read["adoption_verified"])
        candidate_full = {}
        for side in ("old", "new"):
            begin_id = "partitioned-llm-v2-begin-" + side
            begun = self.store.dispatch(12004, 12004, request(
                "contract_candidate_begin", begin_id, candidate_id=candidate_id, side=side,
            ))
            self.assertEqual(begun["run_id"], transition_ids[side])
            self.store.dispatch(12004, 12004, request(
                "evidence_open", "partitioned-llm-v2-open-" + side,
                run_id=transition_ids[side],
            ))
            expected_ref = candidate_roots[side]["binding"]["manifest_ref"]
            candidate_full[side], resolved = resolve_prepared_root(
                self.store, candidate_roots[side], run_id=transition_ids[side],
                expected_manifest_ref=expected_ref, request=request,
                request_prefix="partitioned-llm-v2-artifact-" + side,
            )
            expected_count = 400 if side == "old" else 800
            self.assertEqual(candidate_full[side]["materialization"]["planned_trials"], expected_count)
            self.assertGreater(resolved["resolved_refs"], 0)
            processed = complete_all_partitioned_entries(
                self.store, candidate_full[side], case_count=400, owner_id=begin_id,
                clock=self.fixture.clock, request=request,
                request_prefix="partitioned-llm-v2-" + side,
            )
            self.assertEqual(processed["completed_entries"], expected_count)
            candidate_final = self._close_and_finalize(transition_ids[side], begin_id)
            self.assertEqual(candidate_final["assurance"], "HEALTHY")
            self.assertTrue(candidate_final["input_materialization_verified"])
            self.assertFalse(candidate_final["ci_eligible"])
        validated_following = self.store.dispatch(12003, 12003, request(
            "contract_candidate_validate", "partitioned-llm-v2-validate",
            candidate_id=candidate_id, validation_id="partitioned-llm-v2-validation",
        ))
        self.assertTrue(validated_following["passed"])
        adopted_following = self.store.dispatch(12001, 12001, request(
            "contract_candidate_adopt", "partitioned-llm-v2-adopt",
            candidate_id=candidate_id, validation_id="partitioned-llm-v2-validation",
            expected_contract_generation=1, expected_baseline_generation=1,
        ))
        self.assertEqual(adopted_following["generation"], 2)
        self.assertFalse(adopted_following["ci_eligible"])

        self._run_normal_and_refresh(contract_series_id, following_ref, baseline_series)

    def _run_normal_and_refresh(self, contract_series_id, following_ref, baseline_series):
        normal_run_id = "partitioned-llm-normal-v2"
        normal_request = {
            'schema_version': 1, 'run_id': normal_run_id,
            'contract_series_id': contract_series_id,
            'expected_contract_ref': following_ref, 'trigger': 'manual',
        }
        with tempfile.TemporaryDirectory() as checkpoint_root:
            folder = Path(checkpoint_root)
            runtime = PartitionedRuntime(self.store, self.fixture.clock)
            runner = SyntheticPartitionedRunner(folder, self.fixture.clock)
            normal_result = execute(runtime, runner, folder, normal_request, 'run',
                                    clock=self.fixture.clock)
            self.assertEqual(normal_result['run_id'], normal_run_id)
            self.assertEqual(normal_result['exit_code'], 0, normal_result)
            self.assertTrue(normal_result['ci_eligible'], normal_result)
            self.assertTrue(normal_result['gate']['use'], normal_result['gate'])
            self.assertEqual(len(runner.executed), 800)
            self.assertEqual(len(set(runner.executed)), 800)
            self.assertGreater(sum(1 for _, call in runtime.calls
                                   if call['action'] == 'run_input_artifact'), 0)

            resumed = execute(runtime, runner, folder, normal_request, 'resume',
                              clock=self.fixture.clock)
            status = execute(runtime, runner, folder, normal_request, 'status',
                             clock=self.fixture.clock)
            self.assertEqual(resumed['exit_code'], 0, resumed)
            self.assertEqual(status['exit_code'], 0, status)
            self.assertEqual(len(runner.executed), 800)

            outputs = self.store.dispatch(12004, 12004, request(
                'run_outputs', 'partitioned-llm-normal-v2-outputs', run_id=normal_run_id,
            ))
            self.assertEqual(outputs['outputs']['run_id'], normal_run_id)
            self.assertEqual(outputs['outputs_ref'], normal_result['gate']['outputs_ref'])
            self.assertEqual(outputs['outputs_ref'], resumed['gate']['outputs_ref'])
            self.assertEqual(outputs['outputs_ref'], status['gate']['outputs_ref'])
            outputs_again = self.store.dispatch(12004, 12004, request(
                'run_outputs', 'partitioned-llm-normal-v2-outputs-again', run_id=normal_run_id,
            ))
            self.assertEqual(outputs_again['outputs_ref'], outputs['outputs_ref'])
            self.assertEqual(outputs_again['outputs'], outputs['outputs'])
        refreshed = self.store.dispatch(12001, 12001, request(
            "baseline_propose", "partitioned-llm-v2-baseline-propose",
            proposal_id="partitioned-llm-v2-baseline-proposal", series_id=baseline_series,
            run_id=normal_run_id, expected_generation=1,
        ))
        self.assertEqual(refreshed["generation"], 2)
        baseline_validated = self.store.dispatch(12003, 12003, request(
            "baseline_validate", "partitioned-llm-v2-baseline-validate",
            proposal_id="partitioned-llm-v2-baseline-proposal",
            validation_id="partitioned-llm-v2-baseline-validation",
        ))
        self.assertTrue(baseline_validated["passed"])
        refreshed_adoption = self.store.dispatch(12001, 12001, request(
            "baseline_adopt", "partitioned-llm-v2-baseline-adopt",
            proposal_id="partitioned-llm-v2-baseline-proposal",
            validation_id="partitioned-llm-v2-baseline-validation", expected_generation=1,
        ))
        self.assertTrue(refreshed_adoption["adoption_verified"])
        current_after_refresh = self.store.dispatch(12004, 12004, request(
            "baseline_current", "partitioned-llm-v2-baseline-current", series_id=baseline_series,
        ))
        self.assertTrue(current_after_refresh["valid"])
        self.assertEqual(current_after_refresh["generation"], 2)

    def test_incomplete_partitioned_run_cannot_be_adopted_as_baseline(self):
        run_id = "partitioned-llm-incomplete-400"
        _, _, owner_id = self._prepare_adopt_begin_open(run_id)
        # Start/close a legitimate empty owner lease and finalize without manufacturing attempts.
        final = self._close_and_finalize(run_id, owner_id)
        self.assertFalse(final["ci_eligible"])
        self.assertEqual(self.store._db.execute(
            "SELECT COUNT(*) FROM attempts WHERE run_id=?", (run_id,)
        ).fetchone()[0], 0)
        proposal_id = "partitioned-llm-incomplete-proposal"
        validation_id = "partitioned-llm-incomplete-validation"
        try:
            self.store.dispatch(12001, 12001, request(
                "baseline_propose", "incomplete-baseline-propose", proposal_id=proposal_id,
                series_id="partitioned-llm-incomplete-baseline", run_id=run_id, expected_generation=0,
            ))
        except AdoptionError:
            pass
        else:
            with self.assertRaises(AdoptionError):
                self.store.dispatch(12003, 12003, request(
                    "baseline_validate", "incomplete-baseline-validate",
                    proposal_id=proposal_id, validation_id=validation_id,
                ))
            with self.assertRaises(AdoptionError):
                self.store.dispatch(12001, 12001, request(
                    "baseline_adopt", "incomplete-baseline-adopt", proposal_id=proposal_id,
                    validation_id=validation_id, expected_generation=0,
                ))
        self.assertEqual(self.store._db.execute(
            "SELECT COUNT(*) FROM baseline_adoptions WHERE series_id=?",
            ("partitioned-llm-incomplete-baseline",),
        ).fetchone()[0], 0)
        self.assertIsNone(self.store._db.execute(
            "SELECT 1 FROM baseline_current WHERE series_id=?",
            ("partitioned-llm-incomplete-baseline",),
        ).fetchone())


if __name__ == "__main__":
    unittest.main()
