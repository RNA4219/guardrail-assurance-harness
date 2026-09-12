"""固定fixture用resource authorityの入力・binding・再配送境界を検査する。"""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gah.policy import initial_policy_profile  # noqa: E402
from gah.resource_authority import (  # noqa: E402
    BILLING_REF,
    FRESH_ACTIONS,
    execute,
    validate_request,
)
from gah.resources import ResourceBook, ResourceError, create_schema as create_resource_schema  # noqa: E402
from gah.run_contracts import content_ref  # noqa: E402
from gah.wire import canonical_bytes  # noqa: E402


class ResourceAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "authority.sqlite"
        self.db = sqlite3.connect(str(self.path), isolation_level=None, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.execute("BEGIN IMMEDIATE")
        create_resource_schema(self.db)
        from gah.resource_authority import create_schema as create_authority_schema
        create_authority_schema(self.db)
        self.db.commit()
        self.policy = initial_policy_profile()
        self.book = ResourceBook(self.db)
        self._tx("create_run", "run-1", "a" * 64, deepcopy(self.policy), "pr", "operator-1", 100, 1300)
        self.worker_digest = json.loads((Path(__file__).resolve().parents[1] / "config" / "fixture-runtime.lock.json").read_text(encoding="utf-8"))["worker_digest"]
        self.scenario = "constraint:C01:good"
        expected_target = hashlib.sha256(canonical_bytes({"worker_digest": self.worker_digest, "scenario": self.scenario})).hexdigest()
        self.entry = {
            "obligation_id": "obligation-1",
            "case_id": "case-1",
            "trial_id": "trial-1",
            "variant": "candidate",
            "stage_ids": ["stage-1"],
            "required": True,
            "event_policy": "none",
            "evaluator_ref": {"kind": "evaluator", "id": "evaluator-1", "digest": self.worker_digest},
            "target_ref": {"kind": "target", "id": "target-1", "digest": expected_target},
        }
        self.plan = {"entries": [self.entry]}
        # 要求には識別4項目だけを渡し、実行段数・digest等は採択済みplanで固定する。
        self.request_entry = {key: self.entry[key] for key in ("obligation_id", "case_id", "trial_id", "variant")}

    def tearDown(self):
        self.db.close()

    def _tx(self, method, *args, **kwargs):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            result = getattr(self.book, method)(*args, **kwargs)
        except BaseException:
            self.db.rollback()
            raise
        self.db.commit()
        return result

    def _check_start(self, run_id):
        self.assertEqual(run_id, "run-1")
        return {"run_id": run_id}, self.plan

    def _execute(self, request, now=101):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            result = execute(self.db, request, now, self._check_start)
        except BaseException:
            self.db.rollback()
            raise
        self.db.commit()
        return result

    def _execute_with_plan(self, request, plan, now=101):
        def check_start(run_id):
            self.assertEqual(run_id, "run-1")
            return {"run_id": run_id}, plan

        self.db.execute("BEGIN IMMEDIATE")
        try:
            result = execute(self.db, request, now, check_start)
        except BaseException:
            self.db.rollback()
            raise
        self.db.commit()
        return result

    @staticmethod
    def _request(action, request_id, **fields):
        return {"schema_version": 1, "action": action, "request_id": request_id, "run_id": "run-1", **fields}

    def _reserve_request(self, operation_id="op-1", request_id="request-1", *, entry=None, scenario=None):
        return self._request(
            "resource_reserve", request_id, owner_id="operator-1", owner_epoch=1,
            operation_id=operation_id, entry=deepcopy(self.request_entry if entry is None else entry),
            scenario=self.scenario if scenario is None else scenario,
        )

    def test_fixed_fixture_billing_and_plan_binding_are_used(self):
        request = self._reserve_request()
        self.assertEqual(validate_request(request), request)
        result = self._execute(request)
        self.assertFalse(result["existing"])
        row = self.db.execute("SELECT reservation_json FROM resource_operations WHERE operation_id='op-1'").fetchone()
        reservation = json.loads(row["reservation_json"])
        self.assertEqual(reservation["billing_ref"], BILLING_REF)
        self.assertEqual(reservation["billing_mode"], "non_billed_local")
        self.assertEqual(reservation["api_cost_usd_micros"], 0)
        self.assertEqual(reservation["model_calls"], 0)
        binding = self.db.execute("SELECT * FROM resource_bindings WHERE operation_id='op-1'").fetchone()
        self.assertEqual(binding["scenario"], self.scenario)

    def test_scenario_allowlist_rejects_unknown_and_two_stage_plan(self):
        for scenario in ("constraint:C99:good", "model:anything", "mutation:F99:healthy"):
            request = self._reserve_request(scenario=scenario)
            with self.subTest(scenario=scenario), self.assertRaisesRegex(ResourceError, "^FIXTURE_NOT_ALLOWED$"):
                validate_request(request)
        two_stage = deepcopy(self.entry)
        two_stage["stage_ids"] = ["stage-1", "stage-2"]
        two_stage_plan = {"entries": [two_stage]}
        with self.assertRaisesRegex(ResourceError, "^FIXTURE_BINDING_MISMATCH$"):
            self._execute_with_plan(self._reserve_request(), two_stage_plan)

    def test_plan_entry_and_operation_are_immutable_across_reuse(self):
        self._execute(self._reserve_request())
        replay = self._execute(self._reserve_request(request_id="request-replay"), now=102)
        self.assertTrue(replay["existing"])
        changed_entry = deepcopy(self.request_entry)
        changed_entry["case_id"] = "case-2"
        with self.assertRaisesRegex(ResourceError, "^ENTRY_NOT_PLANNED$"):
            self._execute(self._reserve_request(operation_id="op-2", entry=changed_entry), now=103)
        with self.assertRaisesRegex(ResourceError, "^ENTRY_ALREADY_RESERVED$"):
            self._execute(self._reserve_request(operation_id="op-2", request_id="request-2"), now=103)
        changed_scenario = self._reserve_request(scenario="constraint:C01:bad")
        bad_target = hashlib.sha256(canonical_bytes({
            "worker_digest": self.worker_digest, "scenario": "constraint:C01:bad",
        })).hexdigest()
        bad_plan_entry = deepcopy(self.entry)
        bad_plan_entry["target_ref"]["digest"] = bad_target
        with self.assertRaisesRegex(ResourceError, "^OPERATION_CONFLICT$"):
            self._execute_with_plan(changed_scenario, {"entries": [bad_plan_entry]}, now=104)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM resource_bindings").fetchone()[0], 1)

    def test_every_action_is_fresh_and_duplicate_reserve_has_no_new_row(self):
        self.assertEqual(FRESH_ACTIONS, {
            "resource_claim", "resource_reserve", "resource_dispatch", "resource_cancel",
            "resource_close", "resource_observe", "resource_cancel_claim", "resource_operation",
        })
        self._execute(self._reserve_request())
        self._execute(self._reserve_request(request_id="duplicate-request"), now=102)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM resource_operations").fetchone()[0], 1)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM resource_bindings").fetchone()[0], 1)

    def test_observe_rejects_operation_without_authority_binding(self):
        # ResourceBook直作成のoperationは、authorityのplan bindingがないため観測不可。
        reservation = {
            "case_trial_executions": 1,
            "model_calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "api_cost_usd_micros": 0,
            "billing_ref": BILLING_REF,
            "billing_mode": "non_billed_local",
        }
        self._tx("reserve", "run-1", "unbound-op", "operator-1", 1, reservation, 101)
        self._tx("dispatch_intent", "run-1", "unbound-op", "operator-1", 1, 102)
        observe = self._request(
            "resource_observe", "unbound-observe", operation_id="unbound-op",
            event_id="unbound-event", stopped=True, usage={"input_tokens": 0, "output_tokens": 0, "cost_usd": "0"},
        )
        with self.assertRaisesRegex(ResourceError, "^OPERATION_MISSING$"):
            self._execute(observe, now=103)
        self.assertIsNone(self.db.execute("SELECT 1 FROM resource_events WHERE event_id='unbound-event'").fetchone())

    def test_closed_run_keeps_late_observation_and_drops_budget_closure(self):
        reserve = self._reserve_request()
        self._execute(reserve)
        dispatch = self._request("resource_dispatch", "dispatch-1", owner_id="operator-1", owner_epoch=1, operation_id="op-1")
        self._execute(dispatch, now=102)
        usage = {"input_tokens": 0, "output_tokens": 0, "cost_usd": "0"}
        observe = self._request("resource_observe", "observe-1", operation_id="op-1", event_id="event-1", stopped=True, usage=usage)
        self._execute(observe, now=103)
        close = self._request("resource_close", "close-1", owner_id="operator-1", owner_epoch=1)
        closed = self._execute(close, now=104)
        self.assertTrue(closed["closed"])
        self.assertFalse(closed["ci_eligible"])

        contradictory = deepcopy(observe)
        contradictory["request_id"] = "observe-2"
        contradictory["stopped"] = False
        result = self._execute(contradictory, now=105)
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"], "EVENT_CONFLICT")
        snapshot = self._tx("snapshot", "run-1", 106)
        self.assertTrue(snapshot["closed"])
        self.assertEqual(snapshot["closed_at"], 104)
        self.assertTrue(snapshot["breached"])
        self.assertFalse(snapshot["budget_closure"])
        self.assertEqual(snapshot["resources"]["unsettled"], 1)


if __name__ == "__main__":
    unittest.main()
