"""PAC11 next-operation guidance and outputless failure reporting."""
from copy import deepcopy
import contextlib
import io
import unittest.mock
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.gah_report import build_report, render_markdown, run
from tests import test_run_report as report_fixtures


class RunReportGuidanceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = report_fixtures.RunReportTests()
        self.fixture.setUp()
        self.runtime = self.fixture.runtime
        self.request = self.fixture.request

    def test_success_json_and_markdown_share_fixed_guidance(self):
        report = build_report(self.runtime, self.request)
        self.assertEqual(report["next_operation"], "追加操作不要。必要時に新しいrunを開始してください。")
        self.assertEqual(report["required_role"], "none")
        markdown = render_markdown(report)
        self.assertIn("次の操作: " + report["next_operation"], markdown)
        self.assertIn("必要な役割: none", markdown)
        self.assertIn("操作の目的: " + report["operation_effect"], markdown)
        json_out, markdown_out = io.StringIO(), io.StringIO()
        self.assertEqual(run(self.runtime, self.request, json_out, output_format="json"), 0)
        self.assertEqual(run(self.runtime, self.request, markdown_out), 0)
        self.assertEqual(json.loads(json_out.getvalue())["next_operation"], report["next_operation"])
        self.assertIn("次の操作: " + report["next_operation"], markdown_out.getvalue())

    def _stop_unconfirmed_without_outputs(self):
        original = self.fixture.client
        def client(uid, request):
            if request["action"] == "run_outputs":
                return {"schema_version": 1, "kind": "authority_error", "action": "run_outputs",
                    "request_id": request["request_id"], "run_id": self.request["run_id"],
                    "reason": "STOP_UNCONFIRMED", "ci_eligible": False}
            if request["action"] == "ci_check":
                gate = deepcopy(self.fixture.gate)
                gate.update(outputs_ref=None, assurance="UNKNOWN", reasons=["STOP_UNCONFIRMED"],
                    use=False, ci_eligible=False, execution_status="FAILED", exit_code=2)
                return gate
            return original(uid, request)
        self.runtime.client.side_effect = client

    def test_outputless_stop_state_is_reported_in_requested_format_without_success(self):
        self._stop_unconfirmed_without_outputs()
        json_out, markdown_out = io.StringIO(), io.StringIO()
        self.assertEqual(run(self.runtime, self.request, json_out, output_format="json"), 2)
        report = json.loads(json_out.getvalue())
        self.assertEqual(report["kind"], "run_report_failure")
        self.assertEqual(report["ci_reasons"], ["STOP_UNCONFIRMED"])
        self.assertEqual(report["reasons"], ["STOP_UNCONFIRMED"])
        self.assertEqual(report["requested_context"]["expected_manifest_ref"], self.request["expected_manifest_ref"])
        self.assertFalse(report["ci_eligible"])
        self.assertEqual(report["exit_code"], 2)
        self.assertIn("gah_run status", report["next_operation"])
        self.assertEqual(report["required_role"], "operator")
        self.assertIn("停止状態を照合", report["operation_effect"])
        self.assertEqual(run(self.runtime, self.request, markdown_out), 2)
        text = markdown_out.getvalue()
        self.assertTrue(text.startswith("# GAH実行結果"))
        self.assertIn("STOP_UNCONFIRMED", text)
        self.assertIn("要求対象（成果物を評価した事実ではない）", text)
        self.assertIn("`python -m tools.gah_run status --runtime <runtime> --request <run-request.json>`", text)
        self.assertIn("停止状態を照合する", text)
        self.assertNotIn("run_report_error", text)

    def test_healthy_fresh_gate_cannot_turn_missing_output_into_success(self):
        def client(uid, request):
            if request["action"] == "run_outputs":
                return {"schema_version": 1, "kind": "authority_error", "action": "run_outputs",
                    "request_id": request["request_id"], "run_id": self.request["run_id"],
                    "reason": "EVIDENCE_UNAVAILABLE", "ci_eligible": False}
            return self.fixture.client(uid, request)
        self.runtime.client.side_effect = client
        out = io.StringIO()
        self.assertEqual(run(self.runtime, self.request, out, output_format="json"), 2)
        report = json.loads(out.getvalue())
        self.assertEqual(report["gate_exit_code"], 0)
        self.assertEqual(report["reasons"], ["EVIDENCE_UNAVAILABLE"])
        self.assertFalse(report["ci_eligible"])
        self.assertEqual(report["exit_code"], 2)

    def test_mismatched_fresh_gate_is_not_represented_as_current(self):
        mutations = (
            ("request_id", "another-request"),
            ("run_id", "another-run"),
            ("expected_manifest_ref", {"kind":"run_manifest", "id":"another-run", "digest":"a" * 64}),
            ("outputs_ref", {"kind":"run_outputs", "id":"another-run", "digest":"a" * 64}),
        )
        for field, wrong in mutations:
            with self.subTest(field=field):
                fixture = report_fixtures.RunReportTests(); fixture.setUp()
                original = fixture.client
                def client(uid, request, *, _fixture=fixture, _original=original, _field=field, _wrong=wrong):
                    if request["action"] == "run_outputs":
                        return {"schema_version": 1, "kind": "authority_error", "action": "run_outputs",
                            "request_id": request["request_id"], "run_id": request["run_id"],
                            "reason": "STOP_UNCONFIRMED", "ci_eligible": False}
                    gate = deepcopy(_fixture.gate)
                    gate[_field] = deepcopy(_wrong)
                    return gate
                fixture.runtime.client.side_effect = client
                out = io.StringIO()
                self.assertEqual(run(fixture.runtime, fixture.request, out, output_format="json"), 2)
                report = json.loads(out.getvalue())
                self.assertIsNone(report["checked_at"])
                self.assertEqual(report["ci_reasons"], ["CI_GATE_UNAVAILABLE"])
                self.assertIn("doctor --phase ready", report["next_operation"])
                self.assertFalse(report["ci_eligible"])

    def test_eight_pac11_states_have_matching_guidance_and_parseable_cli(self):
        states = (
            ("healthy", "HEALTHY", [], 0, True, "追加操作不要。必要時に新しいrunを開始してください。", "none"),
            ("warning", "WARNING", [], 0, True, "python -m tools.gah_ops bundle create --workspace <workspace> --runtime <runtime> --run-id <run-id> --output <new-directory>", "operator"),
            ("degraded", "DEGRADED", ["ASSURANCE_NOT_ALLOWED"], 1, True, "python -m tools.gah_ops doctor --phase ready --workspace <workspace> --runtime <runtime> --json", "operator"),
            ("unknown", "UNKNOWN", ["ASSURANCE_NOT_ALLOWED"], 1, True, "python -m tools.gah_ops doctor --phase ready --workspace <workspace> --runtime <runtime> --json", "operator"),
            ("cancel", "HEALTHY", ["CANCEL_REQUESTED"], 3, True, "python -m tools.gah_run status --runtime <runtime> --request <run-request.json>", "operator"),
            ("budget_open", "HEALTHY", ["CANCEL_REQUESTED", "BUDGET_OPEN"], 3, True, "python -m tools.gah_run status --runtime <runtime> --request <run-request.json>", "operator"),
            ("revoked", "HOLD", ["EVIDENCE_REVOKED"], 1, False, "python -m tools.gah_run run --runtime <runtime> --request <new-run-request.json>", "operator"),
            ("stop_unknown", "UNKNOWN", ["STOP_UNCONFIRMED"], 2, False, "python -m tools.gah_run status --runtime <runtime> --request <run-request.json>", "operator"),
        )
        commands = set()
        for label, assurance, reasons, code, outputs_available, operation, role in states:
            with self.subTest(state=label):
                fixture = report_fixtures.RunReportTests(); fixture.setUp()
                run_id = fixture.request["run_id"]
                original = fixture.client
                def client(uid, request, *, _fixture=fixture, _original=original,
                           _reasons=reasons, _assurance=assurance, _code=code,
                           _outputs=outputs_available):
                    if request["action"] == "run_outputs" and not _outputs:
                        reason = _reasons[0]
                        return {"schema_version": 1, "kind": "authority_error", "action": "run_outputs",
                            "request_id": request["request_id"], "run_id": run_id,
                            "reason": reason, "ci_eligible": False}
                    if request["action"] == "ci_check":
                        gate = deepcopy(_fixture.gate)
                        gate.update(assurance=_assurance, reasons=list(_reasons),
                            use=(_code == 0), ci_eligible=(_code == 0), exit_code=_code,
                            execution_status={0:"COMPLETED", 1:"COMPLETED", 2:"FAILED", 3:"CANCELLED"}[_code],
                            outputs_ref=deepcopy(_fixture.outputs_ref) if _outputs else None)
                        return gate
                    return _original(uid, request)
                fixture.runtime.client.side_effect = client
                json_out, markdown_out = io.StringIO(), io.StringIO()
                self.assertEqual(run(fixture.runtime, fixture.request, json_out, output_format="json"), 2 if not outputs_available else code)
                machine = json.loads(json_out.getvalue())
                self.assertEqual(machine["next_operation"], operation)
                self.assertEqual(machine["required_role"], role)
                self.assertEqual(machine["ci_eligible"], outputs_available and code == 0)
                self.assertEqual(run(fixture.runtime, fixture.request, markdown_out), 2 if not outputs_available else code)
                self.assertIn("次の操作: " + ("`" + operation + "`" if operation.startswith("python -m ") else operation), markdown_out.getvalue())
                self.assertIn("必要な役割: " + role, markdown_out.getvalue())
                self.assertIn("操作の目的: " + machine["operation_effect"], markdown_out.getvalue())
                if operation.startswith("python -m "):
                    commands.add(operation)
        for command in commands:
            with self.subTest(cli=command):
                tokens = command.split()
                module = tokens[2]
                import argparse
                args = tokens[3:]
                parsed = []
                real_parse = argparse.ArgumentParser.parse_args
                class ParsedTemplate(BaseException):
                    pass
                def parse_without_executing(parser, arguments=None, namespace=None):
                    parsed.append(real_parse(parser, arguments, namespace))
                    raise ParsedTemplate()
                imported = __import__(module, fromlist=["main"])
                with unittest.mock.patch("sys.argv", [module, *args]), unittest.mock.patch.object(
                        argparse.ArgumentParser, "parse_args", new=parse_without_executing):
                    with self.assertRaises(ParsedTemplate):
                        if module == "tools.gah_ops":
                            imported.main(args)
                        else:
                            imported.main()
                self.assertEqual(len(parsed), 1)

    def test_unavailable_gate_stays_unknown_and_does_not_leak_exception(self):
        def client(uid, request):
            if request["action"] == "run_outputs":
                return {"schema_version": 1, "kind": "authority_error", "action": "run_outputs",
                    "request_id": request["request_id"], "run_id": self.request["run_id"],
                    "reason": "EVIDENCE_UNAVAILABLE", "ci_eligible": False}
            raise OSError("private transport detail")
        self.runtime.client.side_effect = client
        out = io.StringIO()
        self.assertEqual(run(self.runtime, self.request, out, output_format="json"), 2)
        report = json.loads(out.getvalue())
        self.assertEqual(report["assurance"], "UNKNOWN")
        self.assertEqual(report["ci_reasons"], ["CI_GATE_UNAVAILABLE"])
        self.assertIn("doctor --phase ready", report["next_operation"])
        self.assertFalse(report["ci_eligible"])
        self.assertNotIn("private transport detail", out.getvalue())


if __name__ == "__main__":
    unittest.main()
