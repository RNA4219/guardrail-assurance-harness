"""時計逆行の限定再試行と、拒否記録の保持。実Dockerは起動しない。"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from tools.authority_runtime import AuthorityRuntime, AuthorityRuntimeError
from gah.wire import canonical_bytes

ERROR = {"schema_version": 1, "kind": "authority_error", "reason": "CLOCK_ROLLBACK", "ci_eligible": False}
REQUEST = {"schema_version": 1, "action": "current", "request_id": "clock-test", "series_id": "series"}

class AuthorityClockRecoveryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.runtime = object.__new__(AuthorityRuntime)
        self.runtime.folder = Path(temp.name)
        self.runtime._client_once = Mock()
        patcher = patch("tools.authority_runtime.time.sleep")
        self.sleep = patcher.start(); self.addCleanup(patcher.stop)

    def events(self):
        return [json.loads(p.read_text()) for p in (self.runtime.folder / "clock-rejections").glob("*.json")]

    def test_one_retry_preserves_request_and_records_original_rejection(self):
        success = {"kind": "policy_adoption_result", "ci_eligible": False}
        self.runtime._client_once.side_effect = [deepcopy(ERROR), success]
        self.assertEqual(self.runtime.client(12004, REQUEST), success)
        self.assertEqual(self.runtime._client_once.call_count, 2)
        for call in self.runtime._client_once.call_args_list:
            self.assertEqual(call.args, (12004, REQUEST))
        self.sleep.assert_called_once_with(2)
        self.assertEqual(self.events(), [{"schema_version": 1, "kind": "authority_clock_rejection", "uid": 12004,
            "request_digest": hashlib.sha256(canonical_bytes(REQUEST)).hexdigest(),
            "reason": "CLOCK_ROLLBACK", "retry_scheduled": True}])

    def test_persistent_rollback_is_returned_after_one_retry(self):
        self.runtime._client_once.return_value = deepcopy(ERROR)
        self.assertEqual(self.runtime.client(12004, REQUEST), ERROR)
        self.assertEqual(self.runtime._client_once.call_count, 2)
        self.assertEqual(sorted(e["retry_scheduled"] for e in self.events()), [False, True])
        self.sleep.assert_called_once_with(2)

    def test_other_or_malformed_responses_are_not_retried(self):
        for response in ({**ERROR, "reason": "AUTHORITY_DENIED"}, {**ERROR, "extra": 1},
                         {**ERROR, "schema_version": True}, {**ERROR, "ci_eligible": 0}, None):
            with self.subTest(response=response):
                self.runtime._client_once.reset_mock(); self.runtime._client_once.return_value = response
                self.assertEqual(self.runtime.client(12004, REQUEST), response)
                self.assertEqual(self.runtime._client_once.call_count, 1)
        self.sleep.assert_not_called(); self.assertEqual(self.events(), [])

    def test_failed_rejection_record_prevents_retry(self):
        self.runtime._client_once.return_value = deepcopy(ERROR)
        (self.runtime.folder / "clock-rejections").write_text("blocked")
        with self.assertRaisesRegex(AuthorityRuntimeError, "^CLOCK_RECOVERY_UNAVAILABLE$"):
            self.runtime.client(12004, REQUEST)
        self.assertEqual(self.runtime._client_once.call_count, 1); self.sleep.assert_not_called()

    def test_probe_and_unknown_identity_do_not_enter_recovery(self):
        self.runtime._client_once.return_value = deepcopy(ERROR)
        self.assertEqual(self.runtime.client(12004, probe=True), ERROR)
        self.assertEqual(self.runtime._client_once.call_count, 1)
        with self.assertRaisesRegex(AuthorityRuntimeError, "^IDENTITY_NOT_CONFIGURED$"):
            self.runtime.client(True, REQUEST)
        self.assertEqual(self.runtime._client_once.call_count, 1)
        self.sleep.assert_not_called(); self.assertEqual(self.events(), [])

if __name__ == "__main__":
    unittest.main()
