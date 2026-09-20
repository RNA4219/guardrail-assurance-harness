"""共通境界の故障・再配送・wire互換を実装へ対して検査する。"""
from copy import deepcopy
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from gah.contracts import ContractError
from gah.productization import (content_ref, operation_result, read_document,
    validate_acceptance_record, validate_operation_result, validate_plan,
    workspace_path, write_document)
from gah.wire import canonical_bytes


def ref(kind="snapshot_manifest", identifier="source"):
    return {"kind": kind, "id": identifier, "digest": "a" * 64}


def plan():
    return {"schema_version": 1, "kind": "example_plan", "id": "plan-1",
        "requirement_ids": ["GAH-PR01"], "source_ref": ref(), "requirements_ref": ref(identifier="requirements"),
        "created_at": 10, "expires_at": 20, "payload": {"count": 1}}


def payload(value):
    if set(value) != {"count"} or type(value["count"]) is not int or value["count"] < 1:
        raise ContractError("INVALID_INPUT")


def acceptance(status="NOT_RUN"):
    return {"schema_version": 1, "kind": "productization_acceptance_record", "id": "pac-1",
        "requirement_id": "GAH-PR01", "acceptance_id": "GAH-PAC01", "plan_ref": None,
        "source_ref": ref(), "evidence_refs": [], "status": status, "reasons": [], "checked_at": 10}


class ProductizationTests(unittest.TestCase):
    def test_status_mapping_and_ci_always_false(self):
        for code, status in enumerate(("COMPLETED", "REJECTED", "INCOMPLETE", "CANCELLED")):
            result = operation_result("ops.doctor", "request", status,
                reasons=[] if code == 0 else ["OBSERVATION_MISSING"], checked_at=10)
            self.assertEqual(result["exit_code"], code)
            self.assertIs(result["ci_eligible"], False)
            self.assertEqual(len(result), 10)
            self.assertEqual(validate_operation_result(result), result)

    def test_false_success_and_unknown_fields_rejected(self):
        original = operation_result("ops.doctor", "request", "COMPLETED", checked_at=1)
        for changes in ({"ci_eligible": True}, {"exit_code": False}, {"schema_version": True},
                        {"command": "ops.shell"}, {"operation_status": "PASS"}, {"request_id": None},
                        {"extra": 1}, {"checked_at": -1}, {"checked_at": 2 ** 53}):
            with self.subTest(changes=changes), self.assertRaises(ContractError):
                validate_operation_result({**original, **changes})

    def test_unparsed_command_has_error_only_sentinel(self):
        for domain in ("ops", "benchmark", "pilot"):
            command = domain + ".invalid"
            result = operation_result(command, None, "REJECTED", reasons=["INVALID_INPUT"])
            self.assertEqual(result["exit_code"], 1)
            for status in ("COMPLETED", "INCOMPLETE", "CANCELLED"):
                with self.assertRaises(ContractError):
                    operation_result(command, "request", status, reasons=["INVALID_INPUT"])

    def test_incomplete_can_preserve_saved_ref(self):
        result = operation_result("pilot.execute", "request", "INCOMPLETE",
            reasons=["OBSERVATION_MISSING"], result_ref=ref("pilot_result"), checked_at=1)
        self.assertEqual(result["result_ref"], ref("pilot_result"))

    def test_reason_enum_and_noncomplete_reason_required(self):
        for reasons in ([], ["arbitrary user text"], ["IO_ERROR", "IO_ERROR"], [True]):
            with self.subTest(reasons=reasons), self.assertRaises(ContractError):
                operation_result("ops.doctor", None, "REJECTED", reasons=reasons)
        self.assertIsNone(operation_result("ops.doctor", None, "REJECTED",
            reasons=["INVALID_INPUT"])["request_id"])

    def test_canonical_ref_and_alias_independence(self):
        first = {"a": 1, "kind": "sample", "id": "one"}
        second = {"id": "one", "kind": "sample", "a": 1}
        self.assertEqual(content_ref("sample", "one", first), content_ref("sample", "one", second))
        self.assertEqual(content_ref("sample", "one", first)["digest"], hashlib.sha256(canonical_bytes(first)).hexdigest())
        value = plan()
        checked = validate_plan(value, kind="example_plan", payload_validator=payload, now=10)
        value["payload"]["count"] = 50
        self.assertEqual(checked["payload"]["count"], 1)

    def test_plan_fields_and_payload_are_closed(self):
        changes = ({"extra": 1}, {"schema_version": 2}, {"kind": "other"},
            {"requirement_ids": []}, {"requirement_ids": ["GAH-PR01", "GAH-PR01"]},
            {"requirement_ids": ["GAH-PR15"]}, {"payload": {"count": True}},
            {"payload": {"count": 1, "role": "manager"}}, {"source_ref": ref("branch")},
            {"expires_at": 10})
        for delta in changes:
            with self.subTest(delta=delta), self.assertRaises(ContractError):
                validate_plan({**plan(), **delta}, kind="example_plan", payload_validator=payload)

    def test_plan_time_boundary(self):
        for now in (9, 20, 21, True):
            with self.subTest(now=now), self.assertRaises(ContractError):
                validate_plan(plan(), kind="example_plan", payload_validator=payload, now=now)
        validate_plan(plan(), kind="example_plan", payload_validator=payload, now=19)

    def test_pass_requires_matching_obligations_and_evidence(self):
        for value in (acceptance("PASS"), {**acceptance(), "acceptance_id": "GAH-PAC02"},
                      {**acceptance(), "evidence_refs": [ref("evidence")]},
                      {**acceptance(), "status": "FAIL", "plan_ref": ref("pilot_plan")}):
            with self.subTest(value=value), self.assertRaises(ContractError):
                validate_acceptance_record(value)
        good = {**acceptance("PASS"), "plan_ref": ref("pilot_plan"), "evidence_refs": [ref("evidence")]}
        self.assertEqual(validate_acceptance_record(good), good)

    def test_fail_is_not_hidden_by_missing_observations(self):
        value = {**acceptance("FAIL"), "plan_ref": ref("pilot_plan"),
            "reasons": ["SLO_FAILED", "OBSERVATION_MISSING"]}
        self.assertEqual(validate_acceptance_record(value)["status"], "FAIL")

    def test_write_replay_and_different_content(self):
        with tempfile.TemporaryDirectory() as directory:
            value = {"schema_version": 1, "kind": "sample", "id": "one", "count": 1}
            saved = write_document(directory, "result.json", value)
            self.assertEqual(write_document(directory, "result.json", deepcopy(value)), saved)
            with self.assertRaisesRegex(ContractError, "RESULT_CONFLICT"):
                write_document(directory, "result.json", {**value, "count": 2})
            self.assertEqual(read_document(directory, "result.json"), value)

    def test_fsync_failure_before_publish_leaves_no_result_and_can_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            value = {"kind": "sample", "id": "one"}
            target = Path(directory) / "result.json"
            with patch("gah.productization.os.fsync", side_effect=OSError), self.assertRaisesRegex(ContractError, "IO_ERROR"):
                write_document(directory, "result.json", value)
            self.assertFalse(target.exists())
            self.assertEqual(write_document(directory, "result.json", value), content_ref("sample", "one", value))
            self.assertTrue(target.is_file())

    def test_directory_fsync_failure_publishes_atomic_file_and_replay_confirms_it(self):
        from gah.storage_budget import StorageBudgetError
        with tempfile.TemporaryDirectory() as directory:
            value = {"kind": "sample", "id": "one"}
            target = Path(directory) / "result.json"
            with patch("gah.storage_budget._fsync_directory",
                       side_effect=StorageBudgetError("DIRECTORY_FSYNC_FAILED")), \
                 self.assertRaisesRegex(ContractError, "IO_ERROR"):
                write_document(directory, "result.json", value)
            self.assertTrue(target.is_file())
            self.assertEqual(read_document(directory, "result.json"), value)
            self.assertEqual(write_document(directory, "result.json", value), content_ref("sample", "one", value))

    def test_paths_cannot_escape_and_output_directory_is_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            for path in ("../outside.json", Path(directory).parent / "outside.json", directory):
                with self.subTest(path=str(path)), self.assertRaises(ContractError):
                    workspace_path(directory, path)
            with self.assertRaisesRegex(ContractError, "IO_ERROR"):
                write_document(directory, "absent/result.json", {"kind": "sample", "id": "one"})

    def test_reparse_component_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "link.json"
            original = Path.lstat
            def mocked(path, *args, **kwargs):
                if path == target:
                    class Result:
                        st_mode = 0o100644
                        st_file_attributes = 1024
                    return Result()
                return original(path, *args, **kwargs)
            with patch("gah.productization.stat.FILE_ATTRIBUTE_REPARSE_POINT", 1024, create=True), patch.object(Path, "lstat", mocked):
                with self.assertRaisesRegex(ContractError, "PATH_REJECTED"):
                    workspace_path(directory, target)

    def test_reader_preserves_wire_limits(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.json"
            for raw in (b'{"n":1,"n":2}', b'{"n":1.0}', b'{"n":NaN}', b'x' * 1048577):
                path.write_bytes(raw)
                with self.assertRaises(ContractError):
                    read_document(directory, path)

    def test_memory_inputs_apply_wire_limits(self):
        for value in ({"n": 1.0}, {"n": 2 ** 53}, {"n": "x" * 1048576}):
            with self.assertRaises(ContractError):
                content_ref("sample", "one", value)


if __name__ == "__main__":
    unittest.main()
