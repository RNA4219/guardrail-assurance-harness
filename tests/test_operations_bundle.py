"""bundleをmetadata専用fixtureと実SQLiteで検査する。製品実行の受入ではない。"""
from contextlib import redirect_stderr
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from gah.adoption import AdoptionStore, AdoptionError
from gah.evaluation_authority import EvaluationExtension
from gah.policy import initial_policy_profile
from gah import operations_bundle as bundle, resources
from gah.productization_journal import OperationJournal
from gah.wire import canonical_bytes
from tools import evaluation_fixture


class OperationsBundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = AdoptionStore(self.root / "authority.sqlite", clock=lambda: 1000, extension=EvaluationExtension())
        self.addCleanup(self.store.close)
        policy = initial_policy_profile()
        ref = {"kind": "target", "id": "target", "digest": "a" * 64}
        evaluator = {"kind": "evaluator", "id": "evaluator", "digest": "b" * 64}
        _, _, _, contract, plan = evaluation_fixture.documents(policy, ref, evaluator)
        manifest = evaluation_fixture.manifest(contract, plan, "diagnostic-run", 900,
                        {"kind": "environment", "id": "environment", "digest": "c" * 64})
        mraw, mdigest = resources._packed(manifest); praw, pdigest = resources._packed(plan)
        polraw, poldigest = resources._packed(policy)
        self.store._db.execute("INSERT INTO eval_runs VALUES(?,?,?,?,?,?,?)",
            ("diagnostic-run", mraw, mdigest, praw, pdigest, "series", 1))
        self.store._db.execute("INSERT INTO resource_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("diagnostic-run", mdigest, polraw, poldigest, "full", 900, 2100, "SECRET_OWNER_VALUE", 0, 1200, 0, 0, None))
        secret = canonical_bytes({"raw_model_output": "SECRET_MODEL_VALUE"}).decode()
        self.store._db.execute("INSERT INTO authority_artifacts VALUES(?,?,?,?,?)",
            ("evidence", "evidence", hashlib.sha256(secret.encode()).hexdigest(), secret, "diagnostic-run"))
        store = self.store
        class Runtime:
            calls = 0
            def client(inner, uid, request):
                inner.calls += 1
                return store.dispatch(uid, uid, request)
        self.runtime = Runtime()
        self.runtime.folder = self.root / "runtime"
        self.runtime.folder.mkdir()
        (self.runtime.folder / "deployment.json").write_bytes(canonical_bytes(
            {"prefix": "fixture-runtime", "image_id": "fixture-image", "containers": []}))

    def create(self, rid="bundle-test", output="export"):
        return bundle.create(self.root, self.runtime, "diagnostic-run", output,
            request_id=rid, authenticated_principal="os-test", clock=lambda: 1000)

    def test_exports_only_metadata_and_replays_without_new_read(self):
        result = self.create(); self.assertEqual(result["operation_status"], "COMPLETED", result)
        files = sorted((self.root / "export").iterdir())
        self.assertEqual([f.name for f in files], ["diagnostics.json", "manifest.json"])
        combined = b"".join(f.read_bytes() for f in files)
        self.assertNotIn(b"SECRET_MODEL_VALUE", combined); self.assertNotIn(b"SECRET_OWNER_VALUE", combined)
        self.assertNotIn(b"raw_model_output", combined)
        data = json.loads(files[0].read_text())
        self.assertEqual(data["resource_limits"]["elapsed_seconds"], 5400)
        self.assertEqual(len(data["artifact_refs"]), 1)
        self.assertFalse(data["current_ci_checked"]); self.assertFalse(result["ci_eligible"])
        calls = self.runtime.calls
        self.assertEqual(self.create(), result); self.assertEqual(self.runtime.calls, calls)

    def test_existing_output_and_conflicting_replay_do_not_write(self):
        (self.root / "export").mkdir()
        original = self.root / "export" / "untouched.txt"; original.write_text("keep")
        result = self.create(); self.assertEqual(result["reasons"], ["RESULT_CONFLICT"])
        self.assertEqual(original.read_text(), "keep")
        changed = self.create(output="different")
        self.assertEqual(changed["reasons"], ["IDEMPOTENCY_CONFLICT"])
        self.assertFalse((self.root / "different").exists())

    def test_other_runtime_cannot_reuse_saved_result(self):
        result = self.create()
        self.assertEqual(result["operation_status"], "COMPLETED", result)
        deployment = self.runtime.folder / "deployment.json"
        deployment.write_bytes(canonical_bytes({"prefix": "other-runtime", "image_id": "fixture-image", "containers": []}))
        self.assertEqual(self.create()["reasons"], ["IDEMPOTENCY_CONFLICT"])

    def test_capacity_and_entry_limits_are_checked_before_output_creation(self):
        for name, value in (("MAX_BYTES", 1), ("MAX_ENTRIES", 1)):
            with patch.object(bundle, name, value):
                result = self.create(rid="cap-" + name, output="cap-" + name)
            self.assertEqual(result["reasons"], ["CAPACITY_EXCEEDED"])
            self.assertFalse((self.root / ("cap-" + name)).exists())

    def test_lost_final_journal_response_recovers_completed_bundle(self):
        with patch.object(OperationJournal, "finish", side_effect=OSError("lost journal commit")):
            result = self.create()
        self.assertEqual(result["operation_status"], "INCOMPLETE")
        calls = self.runtime.calls
        recovered = self.create()
        self.assertEqual(recovered["operation_status"], "COMPLETED", recovered)
        self.assertEqual(self.runtime.calls, calls)

    def test_partial_write_is_retained_and_not_reexecuted(self):
        original = bundle._write_chunked
        calls = []
        def fail_second(path, raw):
            calls.append(path)
            if len(calls) == 2: raise OSError("disk full")
            original(path, raw)
        with patch.object(bundle, "_write_chunked", side_effect=fail_second):
            result = self.create()
        self.assertEqual(result["operation_status"], "INCOMPLETE")
        self.assertTrue((self.root / "export" / "diagnostics.json").is_file())
        remote_calls = self.runtime.calls
        self.assertEqual(self.create()["reasons"], ["OPERATION_UNKNOWN"])
        self.assertEqual(self.runtime.calls, remote_calls)

    def test_permission_change_during_read_prevents_export(self):
        original = self.runtime.client
        def changing(uid, request):
            value = original(uid, request)
            if request["action"] == "run_diagnostics":
                self.store._db.execute("UPDATE adoption_meta SET value=value+1 WHERE key='permission_generation'")
            return value
        self.runtime.client = changing
        result = self.create()
        self.assertEqual(result["reasons"], ["STALE_OR_INVALIDATED"])
        self.assertFalse((self.root / "export").exists())

    def test_run_change_during_collection_does_not_mix_snapshots(self):
        original = self.runtime.client
        def changing(uid, request):
            value = original(uid, request)
            if request["action"] == "run_diagnostics":
                self.store._db.execute("UPDATE resource_runs SET cancelled=1 WHERE run_id='diagnostic-run'")
            return value
        self.runtime.client = changing
        self.assertEqual(self.create()["reasons"], ["STALE_OR_INVALIDATED"])
        self.assertFalse((self.root / "export").exists())

    def test_diagnostic_action_rejects_candidate_and_unknown_fields(self):
        request = {"schema_version": 1, "action": "run_diagnostics", "request_id": "diag", "run_id": "diagnostic-run"}
        with self.assertRaisesRegex(AdoptionError, "AUTHORITY_DENIED"):
            self.store.dispatch(12002, 12002, request)
        with self.assertRaisesRegex(AdoptionError, "INVALID_REQUEST"):
            self.store.dispatch(12004, 12004, {**request, "raw": True})
        value = self.store.dispatch(12004, 12004, request)
        self.assertIsNone(value["retention"]); self.assertIsNone(value["run_state"])
        self.assertFalse(value["ci_eligible"])


if __name__ == "__main__":
    unittest.main()
