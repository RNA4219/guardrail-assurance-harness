from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gah import pilot
from gah.productization import read_document, write_document
from tools import gah_pilot
from tests.test_pilot_authority import make_documents


def maintenance_observation(index):
    use_case = "UC-CI" if index < 10 else "UC-LLM"
    return {
        "observation_id": f"obs-{index}", "use_case": use_case,
        "status": "COMPLETE", "legacy_active_work_ns": 100,
        "gah_active_work_ns": 70, "legacy_wall_wait_ns": 10,
        "gah_wall_wait_ns": 10, "legacy_model_tool_calls": 2,
        "gah_model_tool_calls": 2, "legacy_token": 20, "gah_token": 20,
        "legacy_cpu_time_ns": 30, "gah_cpu_time_ns": 30,
        "legacy_cost_micro_usd": 4, "gah_cost_micro_usd": 4,
        "legacy_peak_rss_bytes": 100, "gah_peak_rss_bytes": 100,
        "legacy_storage_bytes": 100, "gah_storage_bytes": 100,
        "legacy_human_intervention": 1, "gah_human_intervention": 1,
        "legacy_false_alert_handling": 1, "gah_false_alert_handling": 1,
        "correctness_equal": True, "content_digest": f"{index + 2001:064x}",
    }


class _FakeRuntime:
    instances = []
    mode = "register"
    fail_once = False

    def __init__(self, folder):
        self.folder = Path(folder)
        self.calls = []
        self.closed = 0
        type(self).instances.append(self)

    def close_clients(self):
        self.closed += 1

    def client(self, uid, request):
        self.calls.append((uid, request))
        if type(self).fail_once:
            type(self).fail_once = False
            error = gah_pilot.AuthorityRuntimeError("CLIENT_FAILED")
            raise error
        if request["action"] in {"pilot_binding_register", "pilot_plan_register"}:
            document = request["document"]
            return {
                "schema_version": 1,
                "kind": "pilot_authority_result",
                "action": request["action"],
                "request_id": request["request_id"],
                "ci_eligible": False,
                "artifact_ref": pilot.content_ref(document["kind"], document["id"], document),
                "metadata_only": True,
                "external_refs_verified": False,
                "authority_required": True,
                "product_run_authority": False,
            }
        if request["action"] == "pilot_plan_current":
            return {
                "schema_version": 1,
                "kind": "pilot_authority_result",
                "action": request["action"],
                "request_id": request["request_id"],
                "ci_eligible": False,
                "plan_ref": request["plan_ref"],
                "adopted": self.mode != "expired",
                "valid": self.mode == "valid",
                "generation": 1 if self.mode != "expired" else 0,
                "validation_ref": None,
                "adoption_ref": None,
                "reasons": [] if self.mode == "valid" else (
                    ["PLAN_EXPIRED"] if self.mode == "expired" else ["NOT_STARTED"]),
                "metadata_only": True,
                "external_refs_verified": False,
                "authority_required": True,
                "product_run_authority": False,
            }
        raise AssertionError(request)


class PilotCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name)
        runtime = self.workspace / "runtime"
        runtime.mkdir()
        (runtime / "deployment.json").write_text("{}")
        self.documents = make_documents()
        write_document(self.workspace, "plan.json", self.documents["plan"])
        _FakeRuntime.instances.clear()
        _FakeRuntime.mode = "register"
        _FakeRuntime.fail_once = False

    def _invoke(self, argv):
        output = io.StringIO()
        with redirect_stdout(output):
            code = gah_pilot.main(argv)
        self.assertTrue(output.getvalue())
        value = json.loads(output.getvalue())
        return code, value

    def test_parse_errors_use_invalid_or_known_command_result(self):
        code, result = self._invoke(["unknown"])
        self.assertEqual(code, 1)
        self.assertEqual(set(result), {
            "schema_version", "kind", "command", "request_id",
            "operation_status", "checked_at", "result_ref", "reasons",
            "ci_eligible", "exit_code",
        })
        self.assertEqual(result["command"], "pilot.invalid")
        self.assertIsNone(result["request_id"])
        code, result = self._invoke([
            "status", "--workspace", str(self.workspace), "--request-id", "status-1",
        ])
        self.assertEqual(code, 1)
        self.assertEqual(result["command"], "pilot.status")
        self.assertEqual(result["request_id"], "status-1")

    def test_report_derives_twenty_observations_and_replays_journal(self):
        rows = [maintenance_observation(index) for index in range(20)]
        (self.workspace / "observations.json").write_text(
            json.dumps({"observations": rows}), encoding="utf-8",
        )
        output_path = "result.json"
        arguments = [
            "report", "--workspace", str(self.workspace), "--plan", "plan.json",
            "--observations", "observations.json", "--assessment", "maintenance",
            "--output", output_path, "--request-id", "report-1",
        ]
        code, first = self._invoke(arguments)
        self.assertEqual(code, 2)
        self.assertEqual(first["operation_status"], "INCOMPLETE")
        self.assertEqual(first["reasons"], ["EVIDENCE_UNAVAILABLE"])
        artifact = read_document(self.workspace, output_path)
        self.assertEqual(artifact["pac_status"], "INCONCLUSIVE")
        self.assertIn("external_refs_verified", artifact["missing"])
        self.assertEqual(artifact["counts"]["reduction_numerator"], 30)
        code, replay = self._invoke(arguments)
        self.assertEqual(code, 2)
        self.assertEqual(replay, first)

    def test_report_summary_input_is_inconclusive_and_unknown_field_is_rejected(self):
        summary = {
            "paired_observations": 20, "uc_ci_pairs": 10, "uc_llm_pairs": 10,
            "baseline_median_ns": 100, "candidate_median_ns": 70,
            "resource_dimensions_nonincreasing": True,
            "human_interventions_nonincreasing": True,
            "oracle_equivalent": True,
        }
        (self.workspace / "summary.json").write_text(
            json.dumps(summary), encoding="utf-8",
        )
        code, result = self._invoke([
            "report", "--workspace", str(self.workspace), "--plan", "plan.json",
            "--observations", "summary.json", "--assessment", "maintenance",
            "--output", "summary-result.json", "--request-id", "report-summary",
        ])
        self.assertEqual(code, 2)
        self.assertEqual(result["reasons"], ["EVIDENCE_UNAVAILABLE"])
        artifact = read_document(self.workspace, "summary-result.json")
        self.assertEqual(artifact["pac_status"], "INCONCLUSIVE")
        self.assertIn("raw_observations", artifact["missing"])
        (self.workspace / "bad.json").write_text(
            json.dumps({"observations": [], "untrusted": True}), encoding="utf-8",
        )
        code, result = self._invoke([
            "report", "--workspace", str(self.workspace), "--plan", "plan.json",
            "--observations", "bad.json", "--assessment", "maintenance",
            "--request-id", "report-bad",
        ])
        self.assertEqual(code, 1)
        self.assertEqual(result["operation_status"], "REJECTED")
        self.assertEqual(result["reasons"], ["INVALID_INPUT"])

    def test_register_and_plan_use_fixed_manager_and_save_output(self):
        binding = self.documents["project_a"]
        write_document(self.workspace, "binding.json", binding)
        with patch.object(gah_pilot, "AuthorityRuntime", _FakeRuntime):
            code, result = self._invoke([
                "register", "--workspace", str(self.workspace), "--binding", "binding.json",
                "--output", "binding-output.json", "--runtime", "runtime",
                "--request-id", "binding-1",
            ])
            self.assertEqual(code, 0)
            self.assertEqual(result["result_ref"], pilot.content_ref(
                binding["kind"], binding["id"], binding))
            self.assertEqual(_FakeRuntime.instances[-1].calls[0][0], 12001)
            self.assertEqual(_FakeRuntime.instances[-1].closed, 1)
            code, replay = self._invoke([
                "register", "--workspace", str(self.workspace), "--binding", "binding.json",
                "--output", "binding-output.json", "--runtime", "runtime",
                "--request-id", "binding-1",
            ])
            self.assertEqual(replay, result)
            # replayはjournalから返り、brokerのclientは一度だけ呼ばれる。
            self.assertEqual(sum(len(item.calls) for item in _FakeRuntime.instances), 1)
            code, plan_result = self._invoke([
                "plan", "--workspace", str(self.workspace), "--input", "plan.json",
                "--output", "plan-output.json", "--runtime", "runtime",
                "--request-id", "plan-1",
            ])
            self.assertEqual(code, 0)
            self.assertEqual(plan_result["result_ref"], pilot.content_ref(
                "pilot_plan", "plan-1", self.documents["plan"]))
            self.assertEqual(_FakeRuntime.instances[-1].calls[0][0], 12001)

    def test_status_is_fresh_and_saves_full_current_snapshot(self):
        _FakeRuntime.mode = "valid"
        with patch.object(gah_pilot, "AuthorityRuntime", _FakeRuntime):
            code, first = self._invoke([
                "status", "--workspace", str(self.workspace), "--plan", "plan.json",
                "--runtime", "runtime", "--request-id", "status-fresh",
            ])
            self.assertEqual(code, 0)
            first_path = next(self.workspace.glob(".ga/pilot-status/*.json"))
            first_artifact = read_document(
                self.workspace, str(first_path.relative_to(self.workspace)),
            )
            self.assertTrue(first_artifact["adopted"])
            self.assertTrue(first_artifact["valid"])
            _FakeRuntime.mode = "expired"
            code, second = self._invoke([
                "status", "--workspace", str(self.workspace), "--plan", "plan.json",
                "--runtime", "runtime", "--request-id", "status-fresh",
            ])
            self.assertEqual(code, 0)
            self.assertNotEqual(first["result_ref"], second["result_ref"])
            self.assertEqual(sum(len(item.calls) for item in _FakeRuntime.instances), 2)
            paths = list(self.workspace.glob(".ga/pilot-status/*.json"))
            self.assertEqual(len(paths), 2)
            second_path = next(item for item in paths if item != first_path)
            second_artifact = read_document(
                self.workspace, str(second_path.relative_to(self.workspace)),
            )
            self.assertFalse(second_artifact["adopted"])
            self.assertFalse(second_artifact["valid"])
            self.assertEqual(second_artifact["reasons"], ["PLAN_EXPIRED"])

    def test_explicit_invalid_request_id_is_rejected(self):
        rows = [maintenance_observation(index) for index in range(20)]
        (self.workspace / "observations.json").write_text(
            json.dumps({"observations": rows}), encoding="utf-8",
        )
        code, result = self._invoke([
            "report", "--workspace", str(self.workspace), "--plan", "plan.json",
            "--observations", "observations.json", "--assessment", "maintenance",
            "--request-id", "not a valid id",
        ])
        self.assertEqual(code, 1)
        self.assertIsNone(result["request_id"])
        self.assertEqual(result["reasons"], ["INVALID_INPUT"])

    def test_uncertain_authority_result_is_recovered_on_same_request(self):
        binding = self.documents["project_a"]
        write_document(self.workspace, "binding.json", binding)
        _FakeRuntime.fail_once = True
        with patch.object(gah_pilot, "AuthorityRuntime", _FakeRuntime):
            code, first = self._invoke([
                "register", "--workspace", str(self.workspace), "--binding", "binding.json",
                "--output", "binding-output.json", "--runtime", "runtime",
                "--request-id", "uncertain-1",
            ])
            self.assertEqual(code, 2)
            self.assertEqual(first["reasons"], ["RUNTIME_UNAVAILABLE"])
            code, second = self._invoke([
                "register", "--workspace", str(self.workspace), "--binding", "binding.json",
                "--output", "binding-output.json", "--runtime", "runtime",
                "--request-id", "uncertain-1",
            ])
            self.assertEqual(code, 0)
            self.assertIsNone(first["result_ref"])
            self.assertEqual(second["result_ref"], pilot.content_ref(
                binding["kind"], binding["id"], binding))
            self.assertEqual(sum(len(item.calls) for item in _FakeRuntime.instances), 2)

    def test_execute_stops_at_metadata_authority_boundary(self):
        _FakeRuntime.mode = "valid"
        with patch.object(gah_pilot, "AuthorityRuntime", _FakeRuntime):
            code, result = self._invoke([
                "execute", "--workspace", str(self.workspace), "--plan", "plan.json",
                "--runtime", "runtime", "--request-id", "execute-1",
            ])
        self.assertEqual(code, 2)
        self.assertEqual(result["operation_status"], "INCOMPLETE")
        self.assertEqual(result["reasons"], ["AUTHORITY_REQUIRED"])
        self.assertFalse(result["ci_eligible"])
        self.assertEqual(_FakeRuntime.instances[-1].calls[0][1]["action"],
                         "pilot_plan_current")
        _FakeRuntime.mode = "expired"
        with patch.object(gah_pilot, "AuthorityRuntime", _FakeRuntime):
            code, result = self._invoke([
                "execute", "--workspace", str(self.workspace), "--plan", "plan.json",
                "--runtime", "runtime", "--request-id", "execute-expired",
            ])
        self.assertEqual(code, 1)
        self.assertEqual(result["operation_status"], "REJECTED")
        self.assertEqual(result["reasons"], ["PLAN_EXPIRED"])


if __name__ == "__main__":
    unittest.main()
