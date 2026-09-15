"""人間向け要約も成果物参照と最後のfresh CIからだけ作る。"""
from copy import deepcopy
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.gah_report import build_report, render_markdown, run
from gah.run_contracts import content_ref


class RunReportTests(unittest.TestCase):
    def setUp(self):
        ref = lambda kind: {"kind": kind, "id": "synthetic-" + kind, "digest": "a" * 64}
        self.manifest = {"run_id": "normal", "contract_ref": ref("evaluation_contract"),
            "baseline_ref": ref("baseline"), "target_refs": [ref("target")], "use_cases": ["UC-CI"],
            "profile": "full", "control_ids": ["constraint-one"]}
        manifest_ref = content_ref("run_manifest", "normal", self.manifest)
        self.request = {"schema_version": 1, "action": "ci_check", "request_id": "report-gate", "run_id": "normal",
            "expected_manifest_ref": manifest_ref, "expected_contract_ref": self.manifest["contract_ref"],
            "expected_baseline_ref": self.manifest["baseline_ref"], "expected_target_refs": self.manifest["target_refs"],
            "expected_use_cases": self.manifest["use_cases"]}
        count = dict.fromkeys(("tp", "fp", "tn", "fn", "planned", "complete", "error", "indeterminate",
            "detection_missing", "retry_count", "duplicate_deliveries", "killed", "survived", "no_coverage", "mutation_error"), 0)
        count.update(tp=8, fp=1, tn=9, fn=2, planned=34, complete=30, error=1,
            indeterminate=2, detection_missing=3, retry_count=4, duplicate_deliveries=5)
        self.aggregate = {"schema_version":1, "kind":"aggregation", "run_id":"normal",
            "contract_digest":self.manifest["contract_ref"]["digest"], "ci_eligible":False, "issues":[],
            "counts":{group:{"baseline":{}, "candidate":{}} for group in
                ("variant", "control", "obligation", "category", "control_category", "obligation_category")}}
        self.aggregate["counts"]["variant"] = {"baseline":deepcopy(count), "candidate":deepcopy(count)}
        aggregate_ref = content_ref("aggregation", "normal", self.aggregate)
        self.decision = {"aggregate_digest":aggregate_ref["digest"],"assurance": "HEALTHY", "metrics": [{"metric_id": "coverage", "name": "coverage",
            "value": [9, 10], "baseline_value": [1, 1], "absolute_pass": True, "delta_pass": True}],
            "metric_scopes": {"coverage": {"control_id": "constraint-one"}}, "reasons": []}
        self.evidence = {"observed_at": 90, "valid_until": 200}
        self.artifacts = {aggregate_ref["digest"]:self.aggregate}
        self.outputs = {"schema_version": 1, "kind": "run_outputs", "run_id": "normal"}
        for field, kind, value in (("manifest_ref", "run_manifest", self.manifest),
                ("decision", "run_decision", self.decision), ("evidence", "evidence", self.evidence),
                ("findings", "findings_report", {"items": []}), ("plans", "plans_report", {"items": []}),
                ("run_receipt", "authority_run_receipt", {"run_id": "normal", "ci_eligible": False})):
            reference = content_ref(kind, "normal", value)
            self.outputs[field] = reference
            self.artifacts[reference["digest"]] = value
        self.outputs_ref = content_ref("run_outputs", "normal", self.outputs)
        self.gate = {"schema_version": 1, "kind": "ci_gate_result", "action": "ci_check", "request_id": "report-gate",
            "run_id": "normal", "checked_at": 100, "expected_manifest_ref": manifest_ref,
            "outputs_ref": self.outputs_ref, "assurance": "HEALTHY", "reasons": [], "use": True,
            "ci_eligible": True, "execution_status": "COMPLETED", "exit_code": 0}
        self.runtime = Mock()
        self.runtime.client.side_effect = self.client

    def client(self, uid, request):
        self.assertEqual(uid, 12004)
        if request["action"] == "ci_check":
            return deepcopy(self.gate)
        if request["action"] == "mutation_review_current":
            from gah.mutation_reviews import POLICY_REF
            return {"schema_version":1,"kind":"evaluation_authority_result","action":request["action"],
                "request_id":request["request_id"],"run_id":"normal","evidence_ref":self.outputs["evidence"],
                "contract_ref":self.manifest["contract_ref"],"policy_ref":POLICY_REF,"checked_at":100,
                "items":[],"counts":{v:{"approved_exclusions":0,"pending_exclusions":0} for v in ("baseline","candidate")},
                "original_decision_ref":self.outputs["decision"],"original_decision_unchanged":True,
                "required_obligations_unchanged":True,"ci_eligible":False}
        value = {"schema_version": 1, "kind": "evaluation_authority_result", "action": request["action"],
            "request_id": request["request_id"], "ci_eligible": False}
        if request["action"] == "run_outputs":
            return {**value, "outputs_ref": deepcopy(self.outputs_ref), "outputs": deepcopy(self.outputs)}
        reference = request["artifact_ref"]
        return {**value, "artifact_ref": deepcopy(reference), "artifact": deepcopy(self.artifacts[reference["digest"]])}

    def test_json_and_human_summary_share_the_same_grounding_and_last_gate(self):
        report = build_report(self.runtime, self.request)
        self.assertEqual(report["metrics"][0]["difference"], [-1, 10])
        self.assertEqual(self.runtime.client.call_args.args[1], self.request)
        self.assertEqual(report["checked_at"], 100)
        self.assertEqual(report["valid_until"], 200)
        self.assertEqual(report["source_refs"]["decision"], self.outputs["decision"])
        human, machine = io.StringIO(), io.StringIO()
        self.assertEqual(run(self.runtime, self.request, human), 0)
        self.assertEqual(run(self.runtime, self.request, machine, output_format="json"), 0)
        self.assertEqual(json.loads(machine.getvalue()), report)
        self.assertEqual(human.getvalue(), render_markdown(report))
        self.assertIn("現在のCI利用: 可", human.getvalue())

    def test_saved_counts_are_not_reconstructed_from_reduced_fractions(self):
        report = build_report(self.runtime, self.request)
        candidate = next(row["counts"] for row in report["measurements"]["count_rows"]
            if row["scope"] == ["variant", "candidate"])
        self.assertEqual([candidate[k] for k in ("tp", "fp", "tn", "fn")], [8, 1, 9, 2])
        self.assertEqual([candidate[k] for k in ("error", "indeterminate", "detection_missing")], [1, 2, 3])
        self.assertEqual([candidate[k] for k in ("complete", "planned", "retry_count", "duplicate_deliveries")], [30, 34, 4, 5])
        self.assertEqual(report["source_refs"]["aggregation"]["digest"], self.decision["aggregate_digest"])
        self.assertEqual(report["measurements"]["counts"], self.aggregate["counts"])
        text = render_markdown(report)
        self.assertIn("| 8 | 1 | 9 | 2 | 3 | 1 | 2 |", text)
        self.assertIn("| 30/34 | 4 | 5 |", text)

    def test_aggregation_reference_and_body_changes_cannot_be_reported(self):
        original = deepcopy(self.aggregate)
        self.aggregate["counts"]["variant"]["candidate"]["tp"] += 1
        self.assertEqual(run(self.runtime, self.request, io.StringIO()), 2)
        self.aggregate.clear(); self.aggregate.update(original)
        def client(uid, request):
            value = self.client(uid, request)
            if request.get("artifact_ref", {}).get("kind") == "aggregation":
                value["artifact_ref"]["id"] = "other-run"
            return value
        self.runtime.client.side_effect = client
        self.assertEqual(run(self.runtime, self.request, io.StringIO()), 2)

    def test_count_shapes_reject_unknown_missing_boolean_and_negative_values(self):
        from tools.gah_report import _count_rows
        counts = self.aggregate["counts"]
        for bad in (True, -1, 1.5, 2**53, None):
            malformed = deepcopy(counts); malformed["variant"]["candidate"]["tp"] = bad
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "REPORT_COUNTS_INVALID"):
                _count_rows(malformed)
        for group in ("variant", "control"):
            malformed = deepcopy(counts); del malformed[group]["baseline"]
            with self.subTest(group=group), self.assertRaisesRegex(ValueError, "REPORT_COUNTS_INVALID"):
                _count_rows(malformed)
        # 制約IDが計数fieldと同じ名前でも木の深さで判定する。
        counts["control"]["candidate"]["tp"] = deepcopy(counts["variant"]["candidate"])
        self.assertTrue(any(row["scope"] == ["control", "candidate", "tp"] for row in _count_rows(counts)))

    def test_revocation_during_artifact_read_is_reflected_in_final_report(self):
        def client(uid, request):
            if request["action"] == "run_artifact":
                self.gate.update(exit_code=1, use=False, ci_eligible=False, reasons=["SOURCE_EXPIRED"])
            return self.client(uid, request)
        self.runtime.client.side_effect = client
        out = io.StringIO()
        self.assertEqual(run(self.runtime, self.request, out), 1)
        self.assertIn("現在のCI利用: 不可", out.getvalue())
        self.assertIn("SOURCE_EXPIRED", out.getvalue())
        self.assertIn("保存時のAssurance: HEALTHY", out.getvalue())

    def test_cancelled_gate_stays_cancelled_even_with_healthy_historical_decision(self):
        self.gate.update(exit_code=3, use=False, ci_eligible=False, execution_status="CANCELLED", reasons=["CANCEL_REQUESTED"])
        report = build_report(self.runtime, self.request)
        self.assertEqual(report["execution_status"], "CANCELLED")
        self.assertFalse(report["ci_eligible"])
        self.assertEqual(report["observed_assurance"], "HEALTHY")

    def test_mutated_artifact_wrong_target_and_changed_output_root_fail_closed(self):
        original = deepcopy(self.evidence)
        self.evidence["valid_until"] += 1
        self.assertEqual(run(self.runtime, self.request, io.StringIO()), 2)
        self.evidence.update(original)
        wrong = deepcopy(self.request)
        wrong["expected_target_refs"][0]["digest"] = "f" * 64
        self.assertEqual(run(self.runtime, wrong, io.StringIO()), 2)
        self.gate["outputs_ref"] = {**self.outputs_ref, "digest": "f" * 64}
        self.assertEqual(run(self.runtime, self.request, io.StringIO()), 2)

    def test_unknown_metric_keeps_missing_value_and_comparison_unknown(self):
        from tools.gah_report import _metrics
        value = {**self.decision["metrics"][0], "value": None}
        result = _metrics([value])[0]
        self.assertIsNone(result["value"])
        self.assertIsNone(result["difference"])
        self.assertEqual(result["comparison"], "NOT_COMPARABLE")
        for bad in ([1, 0], [True, 1], [1.0, 1]):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                _metrics([{**value, "value": bad}])

    def test_transport_shape_and_output_errors_return_two(self):
        self.runtime.client.return_value = {}
        self.runtime.client.side_effect = None
        self.assertEqual(run(self.runtime, self.request, io.StringIO()), 2)
        self.runtime.client.side_effect = self.client
        broken = Mock()
        broken.write.side_effect = OSError("output failed")
        self.assertEqual(run(self.runtime, self.request, broken), 2)

    def test_main_invalid_input_and_broken_output_return_two(self):
        from unittest.mock import patch
        from tools.gah_report import main
        with patch("sys.argv", ["gah_report", "--runtime", "missing-report-runtime", "--request", "missing-report.json"]), patch("builtins.print", side_effect=OSError("closed output")):
            self.assertEqual(main(), 2)
