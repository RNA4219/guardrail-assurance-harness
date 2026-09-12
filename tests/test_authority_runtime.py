"""AuthorityRuntimeのDocker設定・所有権・回収境界を実Dockerなしで検査する。"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import tempfile
from unittest import TestCase, mock

from tools.authority_runtime import AuthorityRuntime, AuthorityRuntimeError
from tools.prepare_authority_runtime import ENTRYPOINT


_DIGEST = "a" * 64
_PREFIX = "gah-authority-" + "b" * 32


def _runtime() -> AuthorityRuntime:
    runtime = object.__new__(AuthorityRuntime)
    runtime.prefix = _PREFIX
    runtime.lock = {
        "image_id": "sha256:" + _DIGEST,
        "environment": [],
        "working_dir": "/work",
        "os": "linux",
        "architecture": "amd64",
        "sources_digest": _DIGEST,
    }
    return runtime


def _container(runtime: AuthorityRuntime, uid: int = 12001, mode: str = "client") -> dict:
    tmpfs = runtime._expected_tmpfs(uid)
    host = {
        "Tmpfs": tmpfs,
        "NetworkMode": "none",
        "ReadonlyRootfs": True,
        "Privileged": False,
        "CapDrop": ["ALL"],
        "CapAdd": None,
        "SecurityOpt": ["no-new-privileges=true"],
        "Memory": 134217728,
        "MemorySwap": 134217728,
        "NanoCpus": 500000000,
        "PidsLimit": 32,
        "PidMode": None,
        "IpcMode": "private",
        "CgroupnsMode": "private",
        "LogConfig": {"Type": "none"},
        "RestartPolicy": {"Name": "no"},
        "Binds": None,
        "Devices": None,
        "DeviceRequests": None,
        "VolumesFrom": None,
        "PortBindings": None,
        "GroupAdd": None,
    }
    config = {
        "User": f"{uid}:{uid}",
        "WorkingDir": "/work",
        "Entrypoint": ENTRYPOINT,
        "Cmd": [mode],
        "Env": [],
        "Volumes": None,
        "ExposedPorts": None,
        "Healthcheck": {"Test": ["NONE"]},
        "OpenStdin": True,
        "Tty": False,
    }
    mounts = [{
        "Type": "volume", "Destination": "/ipc", "Name": runtime.prefix + "-ipc",
        "RW": mode == "broker", "Mode": "z", "Propagation": "",
    }]
    if mode == "broker":
        mounts.append({
            "Type": "volume", "Destination": "/state", "Name": runtime.prefix + "-state",
            "RW": True, "Mode": "z", "Propagation": "",
        })
    network = {
        "IPAMConfig": None, "Links": None, "Aliases": None, "MacAddress": "",
        "DriverOpts": None, "GwPriority": 0, "NetworkID": "", "EndpointID": "",
        "Gateway": "", "IPAddress": "", "IPPrefixLen": 0, "IPv6Gateway": "",
        "GlobalIPv6Address": "", "GlobalIPv6PrefixLen": 0, "DNSNames": None,
    }
    return {
        "Image": runtime.lock["image_id"],
        "HostConfig": host,
        "Config": config,
        "NetworkSettings": {"Networks": {"none": network}},
        "Mounts": mounts,
        "State": {"Running": False, "Pid": 0, "ExitCode": 0},
    }


class ConfigurationTests(TestCase):
    def test_mounts_and_network_are_exactly_allowlisted(self) -> None:
        runtime = _runtime()
        valid = _container(runtime)
        runtime._verify_config(valid, 12001, "client")

        cases = []
        extra = copy.deepcopy(valid)
        extra["Mounts"].append({
            "Type": "tmpfs", "Destination": "/unexpected", "RW": True, "Mode": "rw",
        })
        cases.append(extra)
        bad_options = copy.deepcopy(valid)
        bad_options["HostConfig"]["Tmpfs"]["/work"] += ",exec"
        cases.append(bad_options)
        bad_volume = copy.deepcopy(valid)
        bad_volume["Mounts"][-1]["Name"] = runtime.prefix + "-other"
        cases.append(bad_volume)
        network = copy.deepcopy(valid)
        network["NetworkSettings"]["Networks"] = {"none": {}}
        cases.append(network)
        extra_network = copy.deepcopy(valid)
        extra_network["NetworkSettings"]["Networks"]["none"]["IPAddress"] = "192.0.2.1"
        cases.append(extra_network)
        for drift in cases:
            with self.subTest(drift=drift):
                with self.assertRaisesRegex(AuthorityRuntimeError, "^CONFIG_MISMATCH$"):
                    runtime._verify_config(drift, 12001, "client")

    def test_working_directory_and_tmpfs_host_options_are_checked(self) -> None:
        runtime = _runtime()
        for path, value in (("WorkingDir", "/tmp"),):
            drift = _container(runtime)
            drift["Config"][path] = value
            with self.subTest(path=path):
                with self.assertRaisesRegex(AuthorityRuntimeError, "^CONFIG_MISMATCH$"):
                    runtime._verify_config(drift, 12001, "client")
        drift = _container(runtime)
        drift["HostConfig"]["Tmpfs"]["/work"] += ",exec"
        with self.assertRaisesRegex(AuthorityRuntimeError, "^CONFIG_MISMATCH$"):
            runtime._verify_config(drift, 12001, "client")

    def test_image_platform_and_working_directory_are_checked(self) -> None:
        runtime = _runtime()
        image = {
            "Id": runtime.lock["image_id"],
            "Os": "linux",
            "Architecture": "amd64",
            "Config": {
                "WorkingDir": "/work", "Entrypoint": ENTRYPOINT, "Env": [],
                "Labels": {"org.gah.authority.sources": _DIGEST},
            },
        }
        runtime._verify_image(image)
        for field, value in (("Os", "windows"), ("Architecture", "arm64")):
            drift = copy.deepcopy(image)
            drift[field] = value
            with self.subTest(field=field):
                with self.assertRaisesRegex(AuthorityRuntimeError, "^IMAGE_MISMATCH$"):
                    runtime._verify_image(drift)


class LifecycleVerificationTests(TestCase):
    def test_restart_rechecks_configuration_after_restart(self) -> None:
        runtime = _runtime()
        before = _container(runtime, 12000, "broker")
        after = copy.deepcopy(before)
        after["HostConfig"]["ReadonlyRootfs"] = False
        runtime.inspect = mock.Mock(side_effect=[before, after])
        runtime.command = mock.Mock(return_value=b"")
        with self.assertRaisesRegex(AuthorityRuntimeError, "^CONFIG_MISMATCH$"):
            runtime.restart_broker()
        runtime.command.assert_called_once_with(
            ["container", "restart", "--time", "1", runtime.prefix + "-broker"]
        )

    def test_prepare_rechecks_broker_after_start(self) -> None:
        runtime = _runtime()
        runtime.state = {"prefix": runtime.prefix, "image_id": runtime.lock["image_id"], "containers": []}
        image = {
            "Id": runtime.lock["image_id"], "Os": "linux", "Architecture": "amd64",
            "Config": {"WorkingDir": "/work", "Entrypoint": ENTRYPOINT, "Env": [],
                       "Labels": {"org.gah.authority.sources": _DIGEST}},
        }
        calls = []

        def command(args, **_kwargs):
            calls.append(args)
            if args[:2] == ["image", "inspect"]:
                return json.dumps([image]).encode()
            if args[:2] == ["volume", "inspect"]:
                volume_name = args[2]
                volume = {"Name": volume_name, "Driver": "local", "Scope": "local",
                          "Options": None, "Labels": {"org.gah.authority.instance": runtime.prefix}}
                return json.dumps([volume]).encode()
            return b""

        runtime.command = command
        runtime._create = mock.Mock()
        drift = _container(runtime, 12000, "broker")
        drift["HostConfig"]["NetworkMode"] = "bridge"
        runtime.inspect = mock.Mock(return_value=drift)
        with self.assertRaisesRegex(AuthorityRuntimeError, "^CONFIG_MISMATCH$"):
            runtime.prepare()
        self.assertIn(["container", "start", runtime.prefix + "-broker"], calls)


class OwnershipAndCleanupTests(TestCase):
    def test_deployment_rejects_bad_container_entries(self) -> None:
        cases = (( _PREFIX, [_PREFIX + "-client-" + "c" * 32] * 2),
                 (_PREFIX, ["foreign-container"]), (_PREFIX, "not-a-list"), (_PREFIX, [1]),
                 (123, []), ("foreign-prefix", []))
        for prefix, containers in cases:
            with self.subTest(prefix=prefix, containers=containers), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                docker = root / "docker"
                docker.write_bytes(b"docker-test")
                digest = hashlib.sha256(docker.read_bytes()).hexdigest()
                lock = {"image_id": "sha256:" + _DIGEST, "docker_binary_digest": digest}
                folder = root / "deploy"
                folder.mkdir()
                (root / "config").mkdir()
                (root / "config" / "authority-runtime.lock.json").write_bytes(b"lock")
                (folder / "deployment.json").write_bytes(b"state")
                state = {"prefix": prefix, "image_id": lock["image_id"], "containers": containers}
                with mock.patch("tools.authority_runtime.ROOT", root), \
                     mock.patch("tools.authority_runtime.shutil.which", return_value=str(docker)), \
                     mock.patch("tools.authority_runtime.decode_document", side_effect=[lock, state]):
                    with self.assertRaisesRegex(AuthorityRuntimeError, "^DEPLOYMENT_CONFLICT$"):
                        AuthorityRuntime(folder)

    def test_daemon_query_failure_never_reports_absent_success(self) -> None:
        runtime = _runtime()
        name = runtime.prefix + "-client-" + "c" * 32
        runtime.inspect = mock.Mock(side_effect=AuthorityRuntimeError("DOCKER_COMMAND_FAILED"))
        runtime.command = mock.Mock(side_effect=AuthorityRuntimeError("TIMEOUT"))
        with self.assertRaisesRegex(AuthorityRuntimeError, "^STOP_UNCONFIRMED$"):
            runtime.remove_container(name)

    def test_removed_container_is_confirmed_absent(self) -> None:
        runtime = _runtime()
        name = runtime.prefix + "-client-" + "c" * 32
        stopped = _container(runtime)
        runtime.inspect = mock.Mock(side_effect=[stopped, AuthorityRuntimeError("DOCKER_COMMAND_FAILED")])
        calls = []

        def command(args, **_kwargs):
            calls.append(args)
            return b""

        runtime.command = command
        runtime.remove_container(name)
        self.assertIn(["container", "rm", name], calls)
        self.assertIn(["container", "ls", "--all", "--filter", "name=^/" + name + "$", "--format", "{{.ID}}"], calls)

    def test_unknown_container_name_is_never_touched(self) -> None:
        runtime = _runtime()
        runtime.command = mock.Mock()
        with self.assertRaisesRegex(AuthorityRuntimeError, "^OWNERSHIP_MISMATCH$"):
            runtime.remove_container("foreign-container")
        runtime.command.assert_not_called()


if __name__ == "__main__":
    import unittest

    unittest.main()
