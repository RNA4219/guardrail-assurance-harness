"""setupの完了証拠・要求・runtime参照を通常入口の前で照合する。"""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from tools import setup_request
from gah import operations
from gah.contracts import ContractError
from gah.run_contracts import content_ref
from gah.wire import canonical_bytes
from tests import test_ci_client as ci_client

class SetupRequestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.path = self.root / "plan.json"
        operation, self.plan = operations.run_setup_preview(self.root, "sample-ci", self.path, clock=lambda: 1000)
        self.assertEqual(operation["operation_status"], "COMPLETED", operation)
        self.paths = {k: Path(v) for k,v in self.plan["payload"]["output_paths"].items()}
        helper = ci_client.CIClientTests("runTest"); helper.setUp(); self.ci = helper.request
        self.run = {"schema_version": 1, "run_id": "normal", "contract_series_id": "series",
                    "expected_contract_ref": self.ci["expected_contract_ref"], "trigger": "manual"}
        state = {"prefix": "gah-authority-"+"a"*32, "image_id": "sha256:"+"b"*64, "containers": []}
        self.write(self.paths["runtime"] / "deployment.json", state)
        self.write(self.paths["run_request"], self.run); self.write(self.paths["ci_request"], self.ci)
        self.result_path = self.root / ".ga/operations/setup" / self.plan["id"] / "result.json"
        result = {"schema_version": 1, "kind": "setup_result", "id": self.plan["id"],
            "plan_ref": content_ref("setup_plan", self.plan["id"], self.plan), "source_ref": self.plan["source_ref"],
            "profile": "sample-ci", "runtime_ref": content_ref("runtime_deployment", state["prefix"], {k:state[k] for k in ("prefix","image_id")}),
            "contract_ref": self.ci["expected_contract_ref"], "baseline_ref": self.ci["expected_baseline_ref"],
            "initial_run_ref": self.ci["expected_manifest_ref"], "candidate_run_refs": [],
            "run_request_ref": content_ref("run_request", self.run["run_id"], self.run),
            "ci_request_ref": content_ref("ci_request", self.ci["request_id"], self.ci),
            "output_paths": self.plan["payload"]["output_paths"], "ready_ref": None,
            "current_ci_checked": False, "ci_eligible": False}
        self.write(self.result_path, result)

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(canonical_bytes(value))

    def resolve(self): return setup_request.resolve(self.path, root=self.root)

    def test_completed_setup_resolves_paths_without_issuing_ci(self):
        paths = self.resolve()
        self.assertEqual(paths, {**self.paths, "runner": "fixture"})

    def test_changed_request_is_rejected(self):
        self.run["run_id"] = "other"; self.write(self.paths["run_request"], self.run)
        with self.assertRaises(ContractError): self.resolve()

    def test_replaced_deployment_is_rejected(self):
        self.write(self.paths["runtime"] / "deployment.json", {"prefix": "different", "image_id": "sha256:"+"b"*64, "containers": []})
        with self.assertRaises(ContractError): self.resolve()

    def test_incomplete_setup_cannot_resolve(self):
        self.result_path.unlink()
        with self.assertRaises(ContractError): self.resolve()
