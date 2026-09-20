import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from tests.test_pilot_inputs import _bundle
from tools import gah_pilot

class PilotImportCliTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name)
        self.input=self.root/"input.json";self.input.write_text(json.dumps(_bundle()),encoding="utf-8")
        for name,value in [("_trusted_principal","test-principal"),("_now",200)]:
            p=patch.object(gah_pilot,name,return_value=value);p.start();self.addCleanup(p.stop)
        from gah.productization_journal import OperationJournal
        journal_patch=patch.object(gah_pilot,"OperationJournal",side_effect=lambda path: OperationJournal(path,clock=gah_pilot._now))
        journal_patch.start();self.addCleanup(journal_patch.stop)
    def call(self,*extra):
        output=io.StringIO()
        with contextlib.redirect_stdout(output):
            code=gah_pilot.main(["import","--workspace",str(self.root),"--input","input.json","--output","result.json","--request-id","request-1",*extra])
        return code,json.loads(output.getvalue())
    def test_cli_preserves_pending_and_replays_receipt(self):
        code,result=self.call(); self.assertEqual(code,0); self.assertEqual(result["command"],"pilot.import")
        self.assertFalse(result["ci_eligible"]); before=(self.root/"result.json").read_bytes()
        artifact=json.loads(before); self.assertEqual(artifact["verification_status"],"PENDING")
        self.assertFalse(artifact["evaluation_complete"]); self.assertEqual(artifact["pac_status"],"NOT_RUN")
        with patch.object(gah_pilot,"_now",return_value=300): self.assertEqual(self.call(),(code,result))
        self.assertEqual((self.root/"result.json").read_bytes(),before)
    def test_changed_input_same_request_is_rejected(self):
        self.assertEqual(self.call()[0],0)
        doc=_bundle();doc["cases"][0]["observed"]="block";self.input.write_text(json.dumps(doc),encoding="utf-8")
        code,result=self.call();self.assertEqual(code,1);self.assertIn("IDEMPOTENCY_CONFLICT",result["reasons"])
    def test_output_cannot_replace_input(self):
        before=self.input.read_bytes();code,result=self.call("--output","input.json")
        self.assertEqual(code,1);self.assertEqual(self.input.read_bytes(),before)
    def test_raw_payload_is_rejected_without_output(self):
        doc=_bundle();doc["prompt"]="private-sentinel";self.input.write_text(json.dumps(doc),encoding="utf-8")
        code,result=self.call();self.assertNotEqual(code,0)
        self.assertNotIn("private-sentinel",json.dumps(result));self.assertFalse((self.root/"result.json").exists())

    def test_invalid_explicit_request_id_is_rejected(self):
        for value in ("", "bad id"):
            code,result=self.call("--request-id",value)
            self.assertEqual(code,1);self.assertIn("INVALID_INPUT",result["reasons"])
        self.assertFalse((self.root/"result.json").exists())
    def test_output_outside_workspace_is_rejected(self):
        code,result=self.call("--output",str(self.root.parent/"outside-import.json"))
        self.assertEqual(code,1);self.assertIn("PATH_REJECTED",result["reasons"])
