"""保存済みrunから初回baseline候補へ渡す実DB境界を検査する。"""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from gah.adoption import AdoptionStore
from gah.assurance_authority import baseline_source
from gah.baseline_authority import build_candidate
from gah.baselines import bind_baseline_record, repeat_config_for_plan
from gah.contracts import ContractError
from gah.evaluation_authority import EvaluationExtension
from gah.policy import initial_policy_profile
from gah.wire import canonical_bytes
import test_assurance_authority as _assurance_fixture


def request(action, request_id, **fields):
    return {"schema_version": 1, "action": action, "request_id": request_id, **fields}


class Clock:
    def __init__(self, value=1000):
        self.value = value

    def __call__(self):
        return self.value


class BaselineSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "baseline-source.sqlite"
        self.clock = Clock()
        self.policy = initial_policy_profile()
        lock = json.loads((ROOT / "config" / "fixture-runtime.lock.json").read_text(encoding="utf-8"))
        self.worker_digest = lock["worker_digest"]
        self.scenario = "mutation:F01:healthy"
        self.target = {
            "kind": "target", "id": "fixed-target",
            "digest": hashlib.sha256(canonical_bytes({
                "worker_digest": self.worker_digest, "scenario": self.scenario,
            })).hexdigest(),
        }
        self.evaluator = {
            "kind": "evaluator", "id": "fixed-evaluator", "digest": self.worker_digest,
        }
        # 既存の統合fixture helperをTestCase discoveryへ流出させず、unbound helperとして使う。
        self.helper = object.__new__(_assurance_fixture.AssuranceAuthorityTests)
        self.helper.clock = self.clock
        self.helper.policy = self.policy
        self.helper.worker_digest = self.worker_digest
        self.helper.scenario = self.scenario
        self.helper.target = self.target
        self.helper.evaluator = self.evaluator

    def _open(self):
        return AdoptionStore(
            self.path, clock=self.clock, bootstrap_policy=self.policy,
            validator_digest="b" * 64, extension=EvaluationExtension(),
        )

    def _source(self, store, run_id):
        extension = store._extension
        with store._transaction() as db:
            return baseline_source(
                store, db, run_id, self.clock.value,
                lambda identifier: extension._bound_evidence_run(
                    store, db, identifier, self.clock.value),
            )

    def _completed_run(self, store, run_id="baseline-run"):
        setup = _assurance_fixture.AssuranceAuthorityTests._open_and_reserve(
            self.helper, store, run_id)
        values, _, _ = setup
        _assurance_fixture.AssuranceAuthorityTests._settle_and_close(
            self.helper, store, values)
        attempt = _assurance_fixture.AssuranceAuthorityTests._attempt(self.helper, values)
        recorded = store.dispatch(12003, 12003, request(
            "evidence_record", "evidence-record-" + run_id,
            run_id=run_id, attempt=attempt,
        ))
        self.assertTrue(recorded["accepted"])
        finalized = store.dispatch(12004, 12004, request(
            "evidence_finalize", "evidence-finalize-" + run_id, run_id=run_id,
        ))
        self.assertTrue(finalized["authority_connected"])
        return values

    def _bound_candidate(self, source):
        candidate = build_candidate(source, "baseline-series", "baseline-proposal", self.clock.value)
        bound = deepcopy(source["bound"])
        bound["repeat_config"] = repeat_config_for_plan(bound["plan"])
        bound["comparison_context"] = candidate["comparison_context"]
        binding = bind_baseline_record(
            candidate["record"], bound_run=bound, decision=source["decision"],
            evidences=source["evidences"], closure=source["closure"],
            evidence_states=source["evidence_states"], now=self.clock.value,
        )
        return candidate, binding

    def test_real_store_source_and_core_baseline_binding_are_diagnostic_only(self):
        with self._open() as store:
            self._completed_run(store)
            before = store._db.execute(
                "SELECT payload_json,digest FROM authority_run_receipts WHERE run_id=?",
                ("baseline-run",),
            ).fetchone()
            source = self._source(store, "baseline-run")
            candidate, binding = self._bound_candidate(source)
            after = store._db.execute(
                "SELECT payload_json,digest FROM authority_run_receipts WHERE run_id=?",
                ("baseline-run",),
            ).fetchone()

            self.assertEqual(before[0], after[0])
            self.assertEqual(before[1], after[1])
            self.assertIn("INPUT_MATERIALIZATION_UNVERIFIED", source["reasons"])
            self.assertNotIn("ADOPTION_NOT_CONNECTED", source["reasons"])
            self.assertTrue(binding["structurally_bound"])
            self.assertFalse(binding["adoption_verified"])
            self.assertFalse(binding["authority_connected"])
            self.assertFalse(binding["ci_eligible"])
            self.assertEqual(candidate["comparison_context"]["mode"], "not_applicable")

    def test_evidence_content_digest_mismatch_is_rejected_without_store_change(self):
        with self._open() as store:
            self._completed_run(store)
            source = self._source(store, "baseline-run")
            candidate = build_candidate(source, "baseline-series", "baseline-proposal", self.clock.value)
            bad_evidence = deepcopy(source["evidences"][0])
            bad_evidence["collected_at"] += 1
            with self.assertRaises(ContractError):
                bind_baseline_record(
                    candidate["record"], bound_run={
                        **deepcopy(source["bound"]),
                        "repeat_config": repeat_config_for_plan(source["bound"]["plan"]),
                        "comparison_context": candidate["comparison_context"],
                    }, decision=source["decision"], evidences=[bad_evidence],
                    closure=source["closure"], evidence_states=source["evidence_states"],
                    now=self.clock.value,
                )
            self.assertEqual(store._db.execute("SELECT COUNT(*) FROM authority_artifacts").fetchone()[0], 5)

    def test_case_set_and_repeat_plan_replacements_are_rejected(self):
        with self._open() as store:
            self._completed_run(store)
            source = self._source(store, "baseline-run")
            candidate = build_candidate(source, "baseline-series", "baseline-proposal", self.clock.value)
            base = {
                **deepcopy(source["bound"]),
                "repeat_config": repeat_config_for_plan(source["bound"]["plan"]),
                "comparison_context": candidate["comparison_context"],
            }
            changed_case = deepcopy(base)
            changed_case["case_set"]["case_set_id"] = "renamed-case-set"
            with self.assertRaises(ContractError):
                bind_baseline_record(candidate["record"], bound_run=changed_case,
                    decision=source["decision"], evidences=source["evidences"],
                    closure=source["closure"], evidence_states=source["evidence_states"],
                    now=self.clock.value)
            changed_plan = deepcopy(base)
            changed_plan["plan"]["plan_id"] = "renamed-plan"
            with self.assertRaises(ContractError):
                bind_baseline_record(candidate["record"], bound_run=changed_plan,
                    decision=source["decision"], evidences=source["evidences"],
                    closure=source["closure"], evidence_states=source["evidence_states"],
                    now=self.clock.value)

    def test_validator_revocation_blocks_current_use_and_preserves_receipt(self):
        with self._open() as store:
            self._completed_run(store)
            row_before = store._db.execute(
                "SELECT payload_json,digest,revoked_at FROM authority_run_receipts WHERE run_id=?",
                ("baseline-run",),
            ).fetchone()
            store.dispatch(12004, 12004, request(
                "revoke_actor", "revoke-validator", actor_id="validator"))
            source = self._source(store, "baseline-run")
            row_after = store._db.execute(
                "SELECT payload_json,digest,revoked_at FROM authority_run_receipts WHERE run_id=?",
                ("baseline-run",),
            ).fetchone()
            self.assertFalse(source["receipt"]["ci_eligible"])
            self.assertIn("EVIDENCE_ORIGIN_INVALID", source["reasons"])
            self.assertEqual(row_before[0], row_after[0])
            self.assertEqual(row_before[1], row_after[1])
            self.assertIsNone(row_before[2])
            self.assertIsNone(row_after[2])


if __name__ == "__main__":
    unittest.main()
