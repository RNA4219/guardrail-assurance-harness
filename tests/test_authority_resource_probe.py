
"""AuthorityResourceProbeProviderのfake runtime局所試験。実Dockerは起動しない。"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gah.contracts import ContractError
from gah import resource_probe
from tools.authority_resource_probe import (
    FIXED_CGROUP_READ_SCRIPT,
    AuthorityResourceProbeError,
    AuthorityResourceProbeProvider,
    parse_cgroup_payload,
    parse_cgroup_response,
)


PLAN_REF = {"kind": "benchmark_plan", "id": "plan-1", "digest": "a" * 64}
SOURCE_REF = {"kind": "snapshot_manifest", "id": "source-1", "digest": "b" * 64}
REQUEST_DIGEST = "c" * 64


class FakeRuntime:
    def __init__(
        self,
        *,
        client_specs=None,
        include_client=True,
        endpoint="unix:///var/run/docker.sock",
        inspect_sequences=None,
        command_payloads=None,
    ):
        self.prefix = "gah-authority-" + "1" * 32
        self.broker_name = self.prefix + "-broker"
        self.client_name = self.prefix + "-client-" + "2" * 32
        self.worker_name = self.prefix + "-client-" + "3" * 32
        names = [self.broker_name]
        if include_client:
            names.append(self.client_name)
        if client_specs and "worker" in client_specs:
            names.append(self.worker_name)
        self.state = {
            "prefix": self.prefix,
            "image_id": "image-1",
            "containers": names,
        }
        self.endpoint = endpoint
        self._running_clients = {}
        self._reusable_clients = {}
        if include_client:
            self._running_clients[12004] = self.client_name
        if client_specs and "worker" in client_specs:
            self._running_clients[12001] = self.worker_name
        self.client_specs = client_specs or {}
        self.inspect_sequences = inspect_sequences or {}
        self.command_payloads = command_payloads or {}
        self.inspect_calls = []
        self.verify_calls = []
        self.command_calls = []
        self._command_counts = {}

    def inspect_data(self, name, uid, mode, *, identity="id-1",
                     started="start-1", pid=100, running=True,
                     restart_count=0, oom_killed=False):
        return {
            "Name": "/" + name,
            "Id": identity,
            "Config": {
                "User": f"{uid}:{uid}",
                "Cmd": [mode],
            },
            "State": {
                "Running": running,
                "Status": "running" if running else "exited",
                "Pid": pid if running else 0,
                "StartedAt": started,
                "OOMKilled": oom_killed,
            },
            "RestartCount": restart_count,
        }

    def _default_inspect(self, name):
        if name == self.broker_name:
            return self.inspect_data(name, 12000, "broker")
        if name == self.client_name:
            return self.inspect_data(name, 12004, "client-host")
        if name == self.worker_name:
            return self.inspect_data(name, 12001, "client-host")
        raise RuntimeError("unexpected name")

    def inspect(self, name):
        self.inspect_calls.append(name)
        values = self.inspect_sequences.get(name)
        if values is None:
            return copy.deepcopy(self._default_inspect(name))
        if not values:
            raise RuntimeError("scope disappeared")
        value = values.pop(0) if len(values) > 1 else values[0]
        if isinstance(value, BaseException):
            raise value
        return copy.deepcopy(value)

    def _verify_config(self, data, uid, mode):
        self.verify_calls.append((data["Name"], uid, mode))
        if (
            data.get("Config", {}).get("User") != f"{uid}:{uid}"
            or data.get("Config", {}).get("Cmd") != [mode]
        ):
            raise RuntimeError("config mismatch")

    def command(self, args, *, timeout=10, limit=1024 * 1024, input_bytes=None):
        self.command_calls.append((list(args), timeout, limit, input_bytes))
        if args[0:2] != ["container", "exec"]:
            raise AssertionError("provider used an unapproved command")
        name = args[5]
        count = self._command_counts.get(name, 0)
        self._command_counts[name] = count + 1
        values = self.command_payloads.get(name)
        if values is None:
            payload = {
                "cpu_stat": "usage_usec 10\nuser_usec 4\nsystem_usec 6",
                "memory_current": "1000\n",
                "memory_peak": "2000\n",
                "io_stat": "8:0 rbytes=30 wbytes=40 rios=1 wios=1",
            }
        else:
            payload = values[count] if count < len(values) else values[-1]
        if isinstance(payload, BaseException):
            raise payload
        return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


class AuthorityResourceProbeTests(unittest.TestCase):
    def test_empty_io_preserves_cpu_and_memory_without_zero_fill(self):
        from tools.authority_resource_probe import parse_cgroup_payload
        result = parse_cgroup_payload({"cpu_stat":"usage_usec 12\n", "memory_current":"100\n", "memory_peak":"200\n", "io_stat":""})
        self.assertEqual(result["cpu_ns"],12000)
        self.assertEqual(result["memory_current_bytes"],100)
        self.assertIsNone(result["io_read_bytes"])
        self.assertIsNone(result["io_write_bytes"])

    def provider(self, runtime, **kwargs):
        return AuthorityResourceProbeProvider(
            runtime,
            run_id="run-1",
            **kwargs,
        )

    def test_cgroup_parser_is_closed_and_keeps_memory_kinds_separate(self):
        payload = {
            "cpu_stat": (
                "usage_usec 7\nuser_usec 3\nsystem_usec 4\n"
                "nr_periods 1\nnr_throttled 0\nthrottled_usec 0"
            ),
            "memory_current": "1234\n",
            "memory_peak": "5678\n",
            "io_stat": "8:0 rbytes=11 wbytes=13 rios=1 wios=1",
        }
        parsed = parse_cgroup_payload(payload)
        self.assertEqual(parsed["cpu_ns"], 7000)
        self.assertEqual(parsed["memory_current_bytes"], 1234)
        self.assertEqual(parsed["memory_peak_bytes"], 5678)
        self.assertEqual(parsed["io_read_bytes"], 11)
        self.assertEqual(parsed["io_write_bytes"], 13)
        self.assertEqual(parsed["missing"], [])

        unknown = dict(payload)
        unknown["cpu_stat"] += "\nnew_counter 1"
        with self.assertRaisesRegex(ContractError, "CGROUP_PAYLOAD_INVALID"):
            parse_cgroup_payload(unknown)

        duplicate_device = dict(payload)
        duplicate_device["io_stat"] = (
            "8:0 rbytes=1 wbytes=1\n8:0 rbytes=2 wbytes=2"
        )
        with self.assertRaisesRegex(ContractError, "CGROUP_PAYLOAD_INVALID"):
            parse_cgroup_payload(duplicate_device)

        unavailable = dict(payload)
        unavailable["memory_current"] = None
        unavailable["memory_peak"] = None
        parsed = parse_cgroup_payload(unavailable)
        self.assertIsNone(parsed["memory_current_bytes"])
        self.assertIsNone(parsed["memory_peak_bytes"])
        self.assertIn("memory_current_bytes", parsed["missing"])
        self.assertEqual(parse_cgroup_response(json.dumps(payload).encode("utf-8"))["cpu_ns"], 7000)
        self.assertIn("f.read(16385)", FIXED_CGROUP_READ_SCRIPT)
        self.assertIn("len(data)>16384", FIXED_CGROUP_READ_SCRIPT)
        self.assertNotIn("read_text", FIXED_CGROUP_READ_SCRIPT)
        with self.assertRaisesRegex(ContractError, "CGROUP_PAYLOAD_INVALID"):
            parse_cgroup_response('{"cpu_stat":0}')

    def test_snapshot_uses_only_fixed_exec_and_inspect_verify_hooks(self):
        runtime = FakeRuntime()
        provider = self.provider(runtime)
        value = provider.snapshot()
        snapshot = resource_probe.parse_snapshot(value)
        scopes = {scope.scope_id: scope for scope in snapshot.scopes}
        self.assertEqual(set(scopes), {
            runtime.broker_name, runtime.client_name, provider.host_scope_id,
        })
        self.assertEqual(scopes[runtime.broker_name].cpu_ns, 10000)
        self.assertEqual(scopes[runtime.broker_name].memory_current_bytes, 1000)
        self.assertEqual(scopes[runtime.broker_name].memory_peak_bytes, 2000)
        self.assertIsNone(scopes[runtime.broker_name].rss_bytes)
        self.assertEqual(scopes[provider.host_scope_id].scope_kind, "host_harness")
        self.assertIsInstance(scopes[provider.host_scope_id].cpu_ns, int)
        self.assertIsNone(scopes[provider.host_scope_id].rss_bytes)
        self.assertEqual(provider.endpoint, runtime.endpoint)
        self.assertFalse(provider.coverage_complete)
        self.assertIn("WORKER_SCOPE_UNCONNECTED", provider.coverage_reasons)
        self.assertEqual(provider.expected_scope_ids, (
            runtime.broker_name, runtime.client_name, provider.host_scope_id,
        ))
        self.assertTrue(runtime.verify_calls)
        self.assertIn(("/" + runtime.broker_name, 12000, "broker"),
                      runtime.verify_calls)
        self.assertIn(("/" + runtime.client_name, 12004, "client-host"),
                      runtime.verify_calls)
        for args, timeout, limit, input_bytes in runtime.command_calls:
            self.assertEqual(args[0:5], [
                "container", "exec", "--interactive", "--user",
                "12000:12000" if args[5] == runtime.broker_name else "12004:12004",
            ])
            self.assertEqual(args[6:10], [
                "/usr/local/bin/python", "-I", "-B", "-c",
            ])
            self.assertEqual(args[10], FIXED_CGROUP_READ_SCRIPT)
            self.assertEqual(timeout, 10)
            self.assertEqual(limit, 65536)
            self.assertIsNone(input_bytes)

    def test_recovered_client_runtime_failure_is_missing_and_preserves_other_scopes(self):
        runtime = FakeRuntime(client_specs={"worker": True})
        runtime.inspect_sequences[runtime.worker_name] = [
            RuntimeError("DOCKER_COMMAND_FAILED: private daemon detail"),
        ]
        provider = self.provider(runtime)
        value = provider.snapshot()
        scopes = {scope["scope_id"]: scope for scope in value["scopes"]}
        self.assertEqual(set(scopes), {
            runtime.broker_name, runtime.client_name, provider.host_scope_id,
        })
        self.assertEqual(scopes[runtime.broker_name]["cpu_ns"], 10000)
        self.assertEqual(scopes[runtime.client_name]["cpu_ns"], 10000)
        self.assertIsInstance(scopes[provider.host_scope_id]["cpu_ns"], int)
        self.assertNotIn(runtime.worker_name, scopes)
        self.assertIn("CLIENT_UNAVAILABLE", provider.coverage_reasons)
        self.assertIn("CLIENT_SCOPE_UNCONNECTED", provider.coverage_reasons)
        self.assertFalse(provider.coverage_complete)
        self.assertFalse(value.get("ci_eligible", False))
        self.assertNotIn("private daemon detail", repr(value))
        self.assertNotIn("DOCKER_COMMAND_FAILED", repr(value))

    def test_final_client_observations_keep_live_client_and_hide_runtime_error(self):
        runtime = FakeRuntime(client_specs={"worker": True})
        runtime.inspect_sequences[runtime.worker_name] = [
            RuntimeError("DOCKER_COMMAND_FAILED: private daemon detail"),
        ]
        provider = self.provider(runtime)
        value = provider.final_client_observations()
        scopes = {scope["scope_id"]: scope for scope in value["scopes"]}
        self.assertEqual(set(scopes), {runtime.client_name})
        self.assertEqual(value["missing_scope_ids"], [runtime.worker_name])
        self.assertIn("CLIENT_UNAVAILABLE", value["coverage"]["reasons"])
        self.assertIn("CLIENT_SCOPE_UNCONNECTED", value["coverage"]["reasons"])
        self.assertFalse(value["valid_for_slo"])
        self.assertFalse(value["ci_eligible"])
        self.assertNotIn("private daemon detail", repr(value))
        self.assertNotIn("DOCKER_COMMAND_FAILED", repr(value))

    def test_known_fixed_role_is_observed_without_container_enumeration(self):
        runtime = FakeRuntime(
            include_client=False,
            client_specs={"worker": True},
        )
        provider = self.provider(runtime)
        value = provider.snapshot()
        scopes = {scope["scope_id"]: scope for scope in value["scopes"]}
        self.assertIn(runtime.worker_name, scopes)
        self.assertEqual(
            [call[0][5] for call in runtime.command_calls],
            [runtime.broker_name, runtime.worker_name],
        )
        worker_args = runtime.command_calls[1][0]
        self.assertEqual(worker_args[4], "12001:12001")
        self.assertEqual(provider.expected_scope_ids, (
            runtime.broker_name, runtime.worker_name, provider.host_scope_id,
        ))
        self.assertIn("WORKER_SCOPE_UNCONNECTED", provider.coverage_reasons)
        self.assertFalse(any(call[0][0:2] == ["container", "ls"]
                             for call in runtime.command_calls))

    def test_unvalidated_client_is_marked_unconnected_without_exec(self):
        runtime = FakeRuntime(
            include_client=False,
            client_specs={"worker": True},
        )
        runtime.inspect_sequences[runtime.worker_name] = [
            runtime.inspect_data(runtime.worker_name, 13000, "client-host"),
        ]
        provider = self.provider(runtime)
        value = provider.snapshot()
        scopes = {scope["scope_id"]: scope for scope in value["scopes"]}
        self.assertNotIn(runtime.worker_name, scopes)
        self.assertIn("CLIENT_SCOPE_UNCONNECTED", provider.coverage_reasons)
        self.assertIn("CONFIG_MISMATCH", provider.coverage_reasons)
        self.assertEqual(
            [call[0][5] for call in runtime.command_calls],
            [runtime.broker_name],
        )

    def test_after_inspect_disappearance_discards_already_read_counters(self):
        runtime = FakeRuntime()
        normal = runtime.inspect_data(
            runtime.client_name, 12004, "client-host",
        )
        runtime.inspect_sequences[runtime.client_name] = [
            copy.deepcopy(normal),
            copy.deepcopy(normal),
            RuntimeError("gone"),
        ]
        provider = self.provider(runtime)
        value = provider.snapshot()
        scopes = {scope["scope_id"]: scope for scope in value["scopes"]}
        client = scopes[runtime.client_name]
        self.assertIsNone(client["cpu_ns"])
        self.assertIsNone(client["io_read_bytes"])
        self.assertIsNone(client["memory_current_bytes"])
        self.assertIn("SCOPE_DISAPPEARED", provider.coverage_reasons)

    def test_identity_change_after_read_discards_values(self):
        runtime = FakeRuntime()
        old = runtime.inspect_data(
            runtime.client_name, 12004, "client-host",
            identity="old", started="start-1",
        )
        new = runtime.inspect_data(
            runtime.client_name, 12004, "client-host",
            identity="new", started="start-2",
        )
        runtime.inspect_sequences[runtime.client_name] = [
            copy.deepcopy(old), copy.deepcopy(old), copy.deepcopy(new),
        ]
        provider = self.provider(runtime)
        value = provider.snapshot()
        client = next(
            scope for scope in resource_probe.parse_snapshot(value).scopes
            if scope.scope_id == runtime.client_name
        )
        self.assertIsNone(client.cpu_ns)
        self.assertIn("IDENTITY_CHANGED", provider.coverage_reasons)

    def test_stats_unavailable_is_null_with_fixed_scope(self):
        runtime = FakeRuntime(
            command_payloads={
                "gah-authority-" + "1" * 32 + "-broker": [{
                    "cpu_stat": None,
                    "memory_current": None,
                    "memory_peak": None,
                    "io_stat": None,
                }],
            },
        )
        provider = self.provider(runtime)
        value = provider.snapshot()
        broker = next(
            scope for scope in resource_probe.parse_snapshot(value).scopes
            if scope.scope_id == runtime.broker_name
        )
        self.assertIsNone(broker.cpu_ns)
        self.assertIsNone(broker.io_read_bytes)
        self.assertIn("STATS_UNAVAILABLE", provider.coverage_reasons)

    def test_runtime_state_and_constructor_inputs_are_strict(self):
        with self.assertRaisesRegex(ContractError, "INVALID_INPUT"):
            AuthorityResourceProbeProvider(
                FakeRuntime(), run_id="bad id",
            )
        with self.assertRaisesRegex(ContractError, "INVALID_INPUT"):
            AuthorityResourceProbeProvider(
                FakeRuntime(), run_id="run-1", fixed_uid=True,
            )
        runtime = FakeRuntime()
        runtime.state["containers"].append("arbitrary")
        with self.assertRaisesRegex(ContractError, "RUNTIME_STATE_INVALID"):
            self.provider(runtime)

    def test_probe_snapshot_can_feed_session_but_coverage_blocks_slo(self):
        runtime = FakeRuntime()
        provider = self.provider(runtime)
        session = resource_probe.ProbeSession(
            provider,
            plan_ref=PLAN_REF,
            source_ref=SOURCE_REF,
            request_digest=REQUEST_DIGEST,
            expected_scope_ids=provider.expected_scope_ids,
        )
        session.begin()
        result = session.end()
        self.assertFalse(result["valid_for_slo"])
        self.assertFalse(result["measurement_complete"])
        self.assertIn("WORKER_SCOPE_UNCONNECTED", provider.coverage_reasons)
        self.assertIn("SCOPE_INSUFFICIENT", result["reasons"])


if __name__ == "__main__":
    unittest.main()
