"""Read-only statvfs provider tests with a fake AuthorityRuntime; never starts Docker."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gah.contracts import ContractError
from tools.authority_storage_probe import (
    MAX_OUTPUT_BYTES,
    STATVFS_SCRIPT,
    observe_authority_storage,
    validate_storage_observation,
)


PREFIX = "gah-authority-" + "1" * 32
IMAGE = "sha256:" + "a" * 64
CONTAINER_ID = "b" * 64


class FakeRuntime:
    def __init__(self, *, outputs=None, inspect_sequences=None, volume_sequences=None):
        self.prefix = PREFIX
        self.state = {"prefix": PREFIX, "containers": [PREFIX + "-broker"]}
        self.lock = {"image_id": IMAGE}
        self.inspect_calls = []
        self.verify_calls = []
        self.command_calls = []
        self.outputs = list(outputs or [json.dumps({"state": self.scope(), "ipc": self.scope()})])
        self.inspect_sequences = inspect_sequences
        self.volume_sequences = volume_sequences or {}
        self.volume_counts = {}

    @staticmethod
    def scope(available=100, free=200, total=1000, inodes=0):
        return {"available_bytes": available, "free_bytes": free,
                "total_bytes": total, "available_inodes": inodes}

    def inspect_data(self, *, identity=CONTAINER_ID, restart_count=0,
                     running=True, started="started"):
        return {
            "Name": "/" + PREFIX + "-broker",
            "Id": identity,
            "Image": IMAGE,
            "RestartCount": restart_count,
            "Config": {"User": "12000:12000", "Cmd": ["broker"],
                       "Labels": {"org.gah.authority.instance": PREFIX}},
            "State": {"Running": running, "Status": "running" if running else "exited",
                      "Pid": 123 if running else 0, "StartedAt": started},
        }

    def inspect(self, name):
        self.inspect_calls.append(name)
        if name != PREFIX + "-broker":
            raise RuntimeError("untrusted name")
        if self.inspect_sequences is None:
            return copy.deepcopy(self.inspect_data())
        index = len(self.inspect_calls) - 1
        value = self.inspect_sequences[index]
        if isinstance(value, BaseException):
            raise value
        return copy.deepcopy(value)

    def _verify_config(self, data, uid, mode):
        self.verify_calls.append((data.get("Name"), uid, mode))
        if data.get("Config", {}).get("User") != "12000:12000" or data.get("Config", {}).get("Cmd") != ["broker"]:
            raise RuntimeError("bad config")

    def _verify_volume(self, volume, name):
        if (volume.get("Name") != name or volume.get("Driver") != "local"
                or volume.get("Scope") != "local" or volume.get("Options") not in (None, {})
                or volume.get("Labels", {}).get("org.gah.authority.instance") != PREFIX):
            raise RuntimeError("bad volume")

    def command(self, args, *, timeout=10, limit=1024 * 1024, input_bytes=None):
        self.command_calls.append((list(args), timeout, limit, input_bytes))
        if args[:2] == ["volume", "inspect"]:
            name = args[2]
            values = self.volume_sequences.get(name)
            index = self.volume_counts.get(name, 0)
            self.volume_counts[name] = index + 1
            if values is None:
                volume = {"Name": name, "Driver": "local", "Scope": "local",
                          "Options": None, "Mountpoint": "/var/lib/docker/volumes/" + name,
                          "CreatedAt": "2026-09-19T00:00:00Z",
                          "Labels": {"org.gah.authority.instance": PREFIX}}
            else:
                volume = values[min(index, len(values) - 1)]
            if isinstance(volume, BaseException):
                raise volume
            return json.dumps([volume], separators=(",", ":"))
        if args[:3] == ["container", "exec", PREFIX + "-broker"]:
            if args != ["container", "exec", PREFIX + "-broker", "python3", "-c", STATVFS_SCRIPT]:
                raise AssertionError("non-fixed stat command")
            if not self.outputs:
                raise RuntimeError("missing output")
            output = self.outputs.pop(0)
            if isinstance(output, BaseException):
                raise output
            return output
        raise AssertionError("unapproved command")


def observed(runtime):
    return observe_authority_storage(runtime)


class AuthorityStorageProbeTests(unittest.TestCase):
    def test_inode_null_preserves_observed_bytes_without_claiming_complete_capture(self):
        state = FakeRuntime.scope(100, 200, 1000, None)
        result = observed(FakeRuntime(outputs=[json.dumps({"state": state, "ipc": FakeRuntime.scope()})]))
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["reason"], "OBSERVATION_MISSING")
        self.assertEqual(result["scopes"]["state"]["available_bytes"], 100)
        self.assertIsNone(result["scopes"]["state"]["available_inodes"])

    def test_unhashable_status_is_a_contract_error_and_valid_result_is_isolated(self):
        value = observed(FakeRuntime())
        for status in ([], {}):
            with self.subTest(status=status), self.assertRaises(ContractError):
                validate_storage_observation({**value, "status": status})
        result = validate_storage_observation(value)
        result["source"]["container_id"] = None
        result["scopes"]["state"]["available_bytes"] = 0
        self.assertEqual(value["source"]["container_id"], CONTAINER_ID)
        self.assertEqual(value["scopes"]["state"]["available_bytes"], 100)

    def test_observes_fixed_volumes_and_checks_before_after_identity(self):
        runtime = FakeRuntime()
        result = observed(runtime)
        self.assertEqual(result["status"], "OBSERVED")
        self.assertEqual(result["source"], {"image_id": IMAGE, "prefix": PREFIX,
                                            "container_id": CONTAINER_ID})
        self.assertEqual(result["scopes"]["state"]["available_bytes"], 100)
        self.assertEqual(result["scopes"]["ipc"]["available_inodes"], 0)
        self.assertEqual(runtime.inspect_calls, [PREFIX + "-broker"] * 2)
        self.assertEqual(runtime.verify_calls, [("/" + PREFIX + "-broker", 12000, "broker")] * 2)
        self.assertEqual(len(runtime.volume_counts), 2)
        self.assertEqual(len(runtime.command_calls), 5)
        self.assertEqual(runtime.command_calls[2][1:3], (10, MAX_OUTPUT_BYTES))
        self.assertEqual(result["checked_at_source"], "host_observed")
        self.assertFalse(result["ci_eligible"])
        validate_storage_observation(result)

    def test_zero_free_space_is_observed_not_unknown(self):
        runtime = FakeRuntime(outputs=[json.dumps({"state": FakeRuntime.scope(0, 0, 1000, 0),
                                                   "ipc": FakeRuntime.scope(0, 0, 0, 0)})])
        result = observed(runtime)
        self.assertEqual(result["status"], "OBSERVED")
        self.assertEqual(result["scopes"]["state"]["available_bytes"], 0)

    def test_statvfs_missing_scope_is_unknown_with_null(self):
        result = observed(FakeRuntime(outputs=['{"state":null,"ipc":null}']))
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["reason"], "OBSERVATION_MISSING")
        self.assertIsNone(result["scopes"]["state"])

    def test_command_failure_and_oversize_are_sanitized_unknown(self):
        for output in (RuntimeError("secret path"), "x" * (MAX_OUTPUT_BYTES + 1)):
            with self.subTest(type=type(output).__name__):
                result = observed(FakeRuntime(outputs=[output]))
                self.assertEqual(result["status"], "UNKNOWN")
                self.assertEqual(result["reason"], "COMMAND_FAILED" if isinstance(output, RuntimeError) else "OBSERVATION_INVALID")
                self.assertNotIn("secret", json.dumps(result))

    def test_malformed_payloads_are_unknown_not_partial_success(self):
        invalid = [
            "{",  # malformed
            '{"state":{"available_bytes":1',  # truncated
            '{"state":{},"ipc":{}}',
            json.dumps({"state": FakeRuntime.scope(True, 2, 3, 1), "ipc": FakeRuntime.scope()}),
            json.dumps({"state": FakeRuntime.scope(-1, 2, 3, 1), "ipc": FakeRuntime.scope()}),
            json.dumps({"state": FakeRuntime.scope(3, 2, 3, 1), "ipc": FakeRuntime.scope()}),
            '{"state":null,"state":null,"ipc":null}',
            json.dumps({"state": FakeRuntime.scope(), "ipc": FakeRuntime.scope(), "extra": 1}),
        ]
        for raw in invalid:
            with self.subTest(raw=raw[:30]):
                result = observed(FakeRuntime(outputs=[raw]))
                self.assertEqual(result["status"], "UNKNOWN")
                self.assertEqual(result["reason"], "OBSERVATION_INVALID")

    def test_container_restart_or_replacement_during_observation_is_unknown(self):
        runtime = FakeRuntime(inspect_sequences=[
            FakeRuntime().inspect_data(),
            FakeRuntime().inspect_data(restart_count=1),
        ])
        result = observed(runtime)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["reason"], "BROKER_RESTARTED")

        runtime = FakeRuntime(inspect_sequences=[
            FakeRuntime().inspect_data(),
            FakeRuntime().inspect_data(identity="c" * 64),
        ])
        result = observed(runtime)
        self.assertEqual(result["reason"], "BROKER_RESTARTED")

    def test_config_volume_mismatch_and_disappearance_are_unknown(self):
        data = FakeRuntime().inspect_data()
        data["Config"]["Labels"]["org.gah.authority.instance"] = "other"
        runtime = FakeRuntime(inspect_sequences=[data])
        self.assertEqual(observed(runtime)["reason"], "CONFIG_MISMATCH")

        wrong = {"Name": PREFIX + "-state", "Driver": "local", "Scope": "local",
                 "Options": {}, "Mountpoint": "/tmp/other", "CreatedAt": "2026-09-19T00:00:00Z",
                 "Labels": {"org.gah.authority.instance": "other"}}
        runtime = FakeRuntime(volume_sequences={PREFIX + "-state": [wrong]})
        self.assertEqual(observed(runtime)["reason"], "VOLUME_MISMATCH")

        original = {"Name": PREFIX + "-state", "Driver": "local", "Scope": "local",
                    "Options": None, "Mountpoint": "/var/lib/docker/volumes/state-a",
                    "CreatedAt": "2026-09-19T00:00:00Z",
                    "Labels": {"org.gah.authority.instance": PREFIX}}
        replacement = {**original, "Mountpoint": "/var/lib/docker/volumes/state-b"}
        runtime = FakeRuntime(volume_sequences={PREFIX + "-state": [original, replacement]})
        self.assertEqual(observed(runtime)["reason"], "VOLUME_MISMATCH")

        runtime = FakeRuntime(inspect_sequences=[RuntimeError("gone")])
        self.assertEqual(observed(runtime)["reason"], "BROKER_UNAVAILABLE")

    def test_validator_rejects_bool_negative_and_inconsistent_documents(self):
        good = observed(FakeRuntime())
        bad_values = []
        for mutate in (
            lambda d: d["scopes"]["state"].update(available_bytes=True),
            lambda d: d["scopes"]["state"].update(free_bytes=-1),
            lambda d: d["scopes"]["state"].update(total_bytes=0),
            lambda d: d.update(ci_eligible=0),
            lambda d: d.update(extra=True),
            lambda d: d.update(checked_at=True),
            lambda d: d["source"].update(container_id="short"),
        ):
            value = copy.deepcopy(good)
            mutate(value)
            bad_values.append(value)
        for value in bad_values:
            with self.subTest(value=value):
                with self.assertRaisesRegex(ContractError, "^STORAGE_OBSERVATION_INVALID$"):
                    validate_storage_observation(value)

    def test_unknown_runtime_and_stopped_or_wrong_image_never_execute_script(self):
        runtime = FakeRuntime()
        runtime.state["containers"].clear()
        result = observed(runtime)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["reason"], "RUNTIME_INVALID")
        self.assertEqual(runtime.command_calls, [])

        runtime = FakeRuntime(inspect_sequences=[FakeRuntime().inspect_data(running=False)])
        self.assertEqual(observed(runtime)["status"], "UNKNOWN")
        self.assertEqual(runtime.command_calls, [])

        data = FakeRuntime().inspect_data()
        data["Image"] = "sha256:" + "c" * 64
        runtime = FakeRuntime(inspect_sequences=[data])
        self.assertEqual(observed(runtime)["reason"], "CONFIG_MISMATCH")
        self.assertEqual(runtime.command_calls, [])


if __name__ == "__main__":
    unittest.main()
