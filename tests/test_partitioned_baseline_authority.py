"""分割query-scale baselineの既存authority境界を検査する。"""

from copy import deepcopy
import sqlite3
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gah import baseline_authority
from gah.adoption import AdoptionError
from gah.baselines import bind_baseline_record, repeat_config_for_plan
from gah.contracts import ContractError
from gah.partitioned_llm_materialization import build
from gah.partitioned_run_contracts import materialize_partitioned_run
from gah.policy import initial_policy_profile
from gah.run_contracts import content_ref
from gah import run_evidence

IMAGE = "sha256:" + "a" * 64
WORKER = "b" * 64
ISOLATION = {"schema_version": 1, "kind": "query_scale_isolation", "network": "none"}


class Store:
    def _permission_generation(self, db):
        return 1

    def _actor_revoked(self, db, actor):
        return False


def _make_source(material, now):
    baseline_context = material["baseline_context"]
    bound = materialize_partitioned_run(
        material["manifest"], material["contract"], material["plan_index"],
        material["plan_segments"], material["policy"], material["registry"],
        material["case_set"], baseline_context=baseline_context,
    )
    run_id = bound["manifest"]["run_id"]
    decision = {"schema_version": 1, "kind": "run_decision", "decision_id": "decision-" + run_id,
        "run_id": run_id, "purpose": "normal", "assurance": "HEALTHY", "ci_eligible": False}
    closure = {"schema_version": 1, "kind": "resource_closure", "closure_id": "closure-" + run_id,
        "run_id": run_id, "manifest_digest": content_ref("run_manifest", run_id, bound["manifest"])["digest"],
        "budget_closure": True}
    evidence = {"schema_version": 1, "kind": "evidence", "evidence_id": "evidence-" + run_id,
        "subject_ref": content_ref("run_manifest", run_id, bound["manifest"]),
        "conditions_ref": content_ref("bound_bundle", run_id,
            run_evidence.bound_storage_document(bound, baseline_context)),
        "decision_ref": content_ref("run_decision", decision["decision_id"], decision),
        "closure_ref": content_ref("resource_closure", closure["closure_id"], closure),
        "purpose": "baseline_comparison" if bound["manifest"]["purpose"] == "baseline_candidate" else "normal",
        "observed_at": now, "collected_at": now,
        "valid_until": now + 86400, "retention_until": now + 100_000,
        "permission_generation": 1, "authority_connected": True,
        "resource_closure_verified": True, "input_materialization_verified": True,
        "ci_eligible": False}
    receipt = {"schema_version": 1, "kind": "authority_run_receipt", "run_id": run_id,
        "input_materialization_verified": True,
        "manifest_ref": content_ref("run_manifest", run_id, bound["manifest"]),
        "bundle_ref": {"kind": "bound_bundle", "id": run_id,
            "digest": run_evidence.bound_bundle_digest(bound, baseline_context)},
        "decision_ref": content_ref("run_decision", decision["decision_id"], decision),
        "evidence_ref": content_ref("evidence", evidence["evidence_id"], evidence),
        "closure_ref": content_ref("resource_closure", closure["closure_id"], closure)}
    source = {"bound": bound, "receipt": receipt, "decision": decision,
        "evidences": [evidence], "closure": closure,
        "evidence_states": {evidence["evidence_id"]: {"revoked": False, "deleted": False}},
        "reasons": [], "baseline_context": deepcopy(baseline_context)}
    return source


def _build_material(run_id, now, *, generation=1, baseline_context=None, target="baseline-v1"):
    policy = initial_policy_profile()
    material = build(policy, case_count=400, policy_generation=1, run_id=run_id, now=now,
        target_version=target, image_id=IMAGE, worker_digest=WORKER,
        isolation_profile=ISOLATION, generation=generation, baseline_context=baseline_context)
    return material


class PartitionedBaselineAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:", isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("BEGIN IMMEDIATE")
        baseline_authority.create_schema(self.db)
        self.store = Store()
        self.now = 1000

    def tearDown(self):
        if self.db.in_transaction:
            self.db.rollback()
        self.db.close()

    @staticmethod
    def request(action, request_id, **fields):
        return {"schema_version": 1, "action": action, "request_id": request_id, **fields}

    def test_v2_initial_baseline_existing_propose_validate_adopt_use(self):
        material = _build_material("partitioned-baseline-run", self.now)
        source = _make_source(material, self.now)
        resolve = lambda run_id: deepcopy(source)
        proposed = baseline_authority.execute(self.store, self.db,
            self.request("baseline_propose", "propose-v2", proposal_id="proposal-v2",
                series_id="partitioned-series", run_id="partitioned-baseline-run", expected_generation=0),
            "manager", "manager-context", self.now, resolve)
        self.assertEqual(proposed["generation"], 1)
        payload = baseline_authority._validate_proposal_row(self.db.execute(
            "SELECT * FROM baseline_proposals WHERE proposal_id='proposal-v2'").fetchone())
        self.assertEqual(payload["record"]["schema_version"], 2)
        self.assertEqual(payload["record"]["trial_plan_ref"], material["manifest"]["plan_ref"])

        self.now += 1
        validated = baseline_authority.execute(self.store, self.db,
            self.request("baseline_validate", "validate-v2", proposal_id="proposal-v2", validation_id="validation-v2"),
            "validator", "validator-context", self.now, resolve)
        self.assertTrue(validated["passed"])
        self.now += 1
        adopted = baseline_authority.execute(self.store, self.db,
            self.request("baseline_adopt", "adopt-v2", proposal_id="proposal-v2",
                validation_id="validation-v2", expected_generation=0),
            "manager", "manager-context", self.now, resolve)
        self.assertTrue(adopted["adoption_verified"])
        baseline = payload["record"]
        used = baseline_authority.execute(self.store, self.db,
            self.request("baseline_use", "use-v2", series_id="partitioned-series",
                expected_baseline_ref=content_ref("baseline", baseline["baseline_id"], baseline),
                expected_contract_ref=baseline["contract_ref"]),
            "operator", "operator-context", self.now, resolve)
        self.assertTrue(used["valid"])
        self.assertTrue(used["use"])
        self.assertFalse(used["ci_eligible"])

    def test_v2_regression_build_bind_and_forged_reference_rejection(self):
        initial = _build_material("partitioned-before-run", self.now)
        initial_source = _make_source(initial, self.now)
        first = baseline_authority.build_candidate(initial_source, "partitioned-series", "proposal-before", self.now)
        baseline = first["record"]
        target = baseline["target_refs"][0]
        context_input = {"baseline_ref": content_ref("baseline", baseline["baseline_id"], baseline),
            "targets": [{"control_id": "LC-query-scale", "target_ref": target}],
            "contract": initial["contract"]}
        regression = _build_material("partitioned-after-run", self.now + 10, generation=2,
            baseline_context=context_input, target="degraded-v2")
        source = _make_source(regression, self.now + 10)
        checked = baseline_authority._source(source, "partitioned-after-run", self.now + 10,
            self.store, purpose="regression")
        candidate = baseline_authority.build_candidate(checked, "partitioned-series", "proposal-after",
            self.now + 10, expected_generation=1)
        self.assertEqual(candidate["record"]["schema_version"], 2)
        self.assertEqual(candidate["record"]["generation"], 2)
        enriched = {**checked["bound"], "comparison_context": candidate["comparison_context"],
            "repeat_config": repeat_config_for_plan(checked["bound"]["plan"],
                partitioned_context=checked["bound"], baseline_context=checked["baseline_context"])}
        def bind(record):
            return bind_baseline_record(record, bound_run=enriched, decision=checked["decision"],
                evidences=checked["evidences"], closure=checked["closure"], now=self.now + 10,
                evidence_states=checked["evidence_states"], baseline_context=checked["baseline_context"])
        rebound = bind(candidate["record"])
        self.assertTrue(rebound["structurally_bound"])
        self.assertFalse(rebound["adoption_verified"])

        forged = deepcopy(checked)
        forged["receipt"]["bundle_ref"]["digest"] = "c" * 64
        with self.assertRaisesRegex(AdoptionError, "^SOURCE_INVALID$"):
            baseline_authority._source(forged, "partitioned-after-run", self.now + 10,
                self.store, purpose="regression")
        bad_record = deepcopy(candidate["record"])
        bad_record["trial_plan_ref"]["digest"] = "d" * 64
        with self.assertRaises(ContractError):
            bind(bad_record)


if __name__ == "__main__":
    unittest.main()
