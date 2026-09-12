"""authority brokerの起動直後readiness境界を検査する。"""

from unittest import TestCase
from unittest import mock

from tools.authority_runtime import AuthorityRuntime, AuthorityRuntimeError


class AuthorityReadinessTests(TestCase):
    def _runtime(self, inspections, responses):
        runtime = object.__new__(AuthorityRuntime)
        runtime.prefix = "gah-authority-" + "a" * 32
        runtime.inspect = mock.Mock(side_effect=inspections)
        runtime.client = mock.Mock(side_effect=responses)
        return runtime

    @staticmethod
    def _running():
        return {"State": {"Running": True, "Pid": 1}}

    @staticmethod
    def _response(request):
        return {
            "schema_version": 1,
            "kind": "policy_adoption_result",
            "action": "current",
            "request_id": request["request_id"],
            "series_id": "runtime-readiness",
            "valid": False,
            "ci_eligible": False,
        }

    def test_one_readiness_probe_accepts_unadopted_current(self):
        runtime = self._runtime([self._running()], [])
        runtime.client.side_effect = lambda uid, request: self._response(request)

        runtime._wait_ready()

        runtime.inspect.assert_called_once_with(runtime.prefix + "-broker")
        client_calls = runtime.client.call_args_list
        self.assertEqual(len(client_calls), 1)
        uid, request = client_calls[0].args
        self.assertEqual(uid, 12004)
        self.assertEqual(request["schema_version"], 1)
        self.assertEqual(request["action"], "current")
        self.assertEqual(request["series_id"], "runtime-readiness")
        self.assertIsInstance(request["request_id"], str)
        self.assertTrue(request["request_id"])
        self.assertFalse(self._response(request)["ci_eligible"])

    def test_client_failure_is_retried_then_valid_response_is_accepted(self):
        runtime = self._runtime([self._running(), self._running()], [])
        calls = 0

        def client(uid, request):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise AuthorityRuntimeError("CLIENT_FAILED")
            return self._response(request)

        runtime.client.side_effect = client

        with mock.patch("tools.authority_runtime.time.sleep") as sleep:
            runtime._wait_ready()

        self.assertEqual(runtime.client.call_count, 2)
        requests = [call.args[1] for call in runtime.client.call_args_list]
        self.assertEqual([request["action"] for request in requests], ["current", "current"])
        self.assertEqual([request["series_id"] for request in requests],
                         ["runtime-readiness", "runtime-readiness"])
        self.assertEqual(len({request["request_id"] for request in requests}), 2)
        sleep.assert_called_once_with(0.1)

    def test_three_client_failures_become_broker_not_ready(self):
        runtime = self._runtime(
            [self._running(), self._running(), self._running()],
            [AuthorityRuntimeError("CLIENT_FAILED")] * 3,
        )

        with mock.patch("tools.authority_runtime.time.sleep") as sleep:
            with self.assertRaisesRegex(AuthorityRuntimeError, "^BROKER_NOT_READY$"):
                runtime._wait_ready()

        self.assertEqual(runtime.client.call_count, 3)
        self.assertEqual(runtime.inspect.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    def test_stopped_broker_is_rejected_without_sending_current(self):
        runtime = self._runtime(
            [{"State": {"Running": False, "Pid": 0}}],
            [],
        )

        with mock.patch("tools.authority_runtime.time.sleep") as sleep:
            with self.assertRaisesRegex(AuthorityRuntimeError, "^BROKER_NOT_READY$"):
                runtime._wait_ready()

        runtime.client.assert_not_called()
        sleep.assert_not_called()

    def test_malformed_current_response_is_rejected(self):
        runtime = self._runtime(
            [self._running(), self._running(), self._running()],
            [{"kind": "wrong", "action": "current"}] * 3,
        )

        with mock.patch("tools.authority_runtime.time.sleep"):
            with self.assertRaisesRegex(AuthorityRuntimeError, "^BROKER_NOT_READY$"):
                runtime._wait_ready()

        self.assertLessEqual(runtime.client.call_count, 3)
        for call in runtime.client.call_args_list:
            self.assertEqual(call.args[0], 12004)
            self.assertEqual(call.args[1]["action"], "current")
            self.assertEqual(call.args[1]["series_id"], "runtime-readiness")

    def test_malformed_json_uses_real_decoder_and_cleans_up_all_three_probes(self):
        runtime = object.__new__(AuthorityRuntime)
        runtime.prefix = "gah-authority-" + "b" * 32
        runtime._create = mock.Mock()
        runtime._verify_config = mock.Mock()
        runtime.remove_container = mock.Mock()
        runtime.command = mock.Mock(return_value=b"{invalid-json")
        runtime.inspect = mock.Mock(side_effect=lambda name: self._running()
            if name.endswith("-broker") else {"State": {"Running": False, "Pid": 0, "ExitCode": 0}})
        with mock.patch("tools.authority_runtime.time.sleep") as sleep:
            with self.assertRaisesRegex(AuthorityRuntimeError, "^BROKER_NOT_READY$"):
                runtime._wait_ready()
        self.assertEqual(runtime.command.call_count, 3)
        self.assertEqual(runtime.remove_container.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual([call.args[1:] for call in runtime._create.call_args_list], [(12004, "client")] * 3)


if __name__ == "__main__":
    import unittest

    unittest.main()
