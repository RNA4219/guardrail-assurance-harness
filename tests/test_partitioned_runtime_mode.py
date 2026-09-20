"""Explicit schema-v6 authority broker mode and default-mode compatibility tests."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
from unittest import TestCase, mock

from gah.adoption import AdoptionError, AdoptionStore
from gah.authority import AuthorityError, _database_extension
from gah.evaluation_authority import EvaluationExtension
from gah.partitioned_run_authority import PartitionedRunEvaluationExtension
from tools import authority_entry
from tools.authority_runtime import AuthorityRuntime, AuthorityRuntimeError
from tools.prepare_authority_runtime import ENTRYPOINT

_IMAGE = "sha256:" + "a" * 64
_PREFIX = "gah-authority-" + "b" * 32


def _runtime(*, mode="default"):
    value = object.__new__(AuthorityRuntime)
    value.database_mode = mode
    value.prefix = _PREFIX
    value.lock = {"image_id": _IMAGE, "environment": [], "working_dir": "/work",
                  "os": "linux", "architecture": "amd64", "sources_digest": "c" * 64}
    return value


def _container(runtime, uid, mode):
    broker = mode in {"broker", "broker-v6"}
    host = {"Tmpfs": runtime._expected_tmpfs(uid), "NetworkMode": "none", "ReadonlyRootfs": True,
        "Privileged": False, "CapDrop": ["ALL"], "CapAdd": None,
        "SecurityOpt": ["no-new-privileges=true"], "Memory": 134217728, "MemorySwap": 134217728,
        "NanoCpus": 500000000, "PidsLimit": 32, "PidMode": None, "IpcMode": "private",
        "CgroupnsMode": "private", "LogConfig": {"Type": "none"}, "RestartPolicy": {"Name": "no"},
        "Binds": None, "Devices": None, "DeviceRequests": None, "VolumesFrom": None,
        "PortBindings": None, "GroupAdd": None}
    config = {"User": f"{uid}:{uid}", "WorkingDir": "/work", "Entrypoint": ENTRYPOINT,
        "Cmd": [mode], "Env": [], "Volumes": None, "ExposedPorts": None,
        "Healthcheck": {"Test": ["NONE"]}, "OpenStdin": True, "Tty": False}
    mounts = [{"Type": "volume", "Destination": "/ipc", "Name": runtime.prefix + "-ipc",
        "RW": broker, "Mode": "z", "Propagation": ""}]
    if broker:
        mounts.append({"Type": "volume", "Destination": "/state", "Name": runtime.prefix + "-state",
            "RW": True, "Mode": "z", "Propagation": ""})
    network = {"IPAMConfig": None, "Links": None, "Aliases": None, "MacAddress": "",
        "DriverOpts": None, "GwPriority": 0, "NetworkID": "", "EndpointID": "", "Gateway": "",
        "IPAddress": "", "IPPrefixLen": 0, "IPv6Gateway": "", "GlobalIPv6Address": "",
        "GlobalIPv6PrefixLen": 0, "DNSNames": None}
    return {"Image": runtime.lock["image_id"], "HostConfig": host, "Config": config,
        "NetworkSettings": {"Networks": {"none": network}}, "Mounts": mounts,
        "State": {"Running": False, "Pid": 0, "ExitCode": 0}}


class ExtensionSelectionTests(TestCase):
    def test_fixed_factory_selects_only_default_or_exact_v6(self):
        self.assertIs(type(_database_extension("default")), EvaluationExtension)
        self.assertIs(type(_database_extension("partitioned-v6")), PartitionedRunEvaluationExtension)
        for value in (None, "v6", "partitioned-v5", 6):
            with self.subTest(value=value), self.assertRaisesRegex(AuthorityError, "^DATABASE_MODE_INVALID$"):
                _database_extension(value)

    def test_new_v6_database_is_created_at_schema_six_and_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fresh-v6.sqlite"
            with AdoptionStore(path, extension=_database_extension("partitioned-v6")) as store:
                self.assertEqual(store._db.execute("PRAGMA user_version").fetchone()[0], 6)
                self.assertIs(type(store._extension), PartitionedRunEvaluationExtension)
                self.assertIsNotNone(store._db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='eval_runs_v2'"
                ).fetchone())
            with self.assertRaises(AdoptionError) as error:
                AdoptionStore(path, extension=EvaluationExtension())
            self.assertIn(error.exception.code, {"UNSUPPORTED_STORE", "CONFIG_MISMATCH"})
            db = sqlite3.connect(path)
            try:
                self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 6)
            finally:
                db.close()

    def test_default_database_remains_default_and_v6_refuses_it(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "default.sqlite"
            with AdoptionStore(path, extension=_database_extension("default")) as store:
                version = store._db.execute("PRAGMA user_version").fetchone()[0]
                self.assertEqual(version, EvaluationExtension.schema_version)
            with self.assertRaises(AdoptionError) as error:
                AdoptionStore(path, extension=_database_extension("partitioned-v6"))
            self.assertIn(error.exception.code, {"UNSUPPORTED_STORE", "CONFIG_MISMATCH"})
            db = sqlite3.connect(path)
            try:
                self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], version)
            finally:
                db.close()

    def test_entrypoint_adds_only_explicit_broker_v6_mode(self):
        with mock.patch("sys.argv", ["authority_entry.py", "broker"]), \
             mock.patch.object(authority_entry, "serve") as serve:
            self.assertEqual(authority_entry.main(), 0)
            serve.assert_called_once_with(authority_entry.SOCKET, authority_entry.DATABASE)
        with mock.patch("sys.argv", ["authority_entry.py", "broker-v6"]), \
             mock.patch.object(authority_entry, "serve") as serve:
            self.assertEqual(authority_entry.main(), 0)
            serve.assert_called_once_with(authority_entry.SOCKET, authority_entry.DATABASE,
                                          database_mode="partitioned-v6")


class RuntimeModeTests(TestCase):
    def _runtime_root(self, root):
        config = root / "config"
        config.mkdir()
        docker = root / "docker"
        docker.write_bytes(b"fixed test executable")
        lock = {"docker_binary_digest": hashlib.sha256(docker.read_bytes()).hexdigest(),
                "image_id": _IMAGE}
        (config / "authority-runtime.lock.json").write_text(
            json.dumps(lock, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        return docker

    def test_deployment_mode_is_persisted_and_legacy_three_key_state_is_default_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            docker = self._runtime_root(root)
            default_folder = root / "default"
            with mock.patch("tools.authority_runtime.ROOT", root), \
                 mock.patch("tools.authority_runtime.shutil.which", return_value=str(docker)):
                default = AuthorityRuntime(default_folder)
                state = json.loads((default_folder / "deployment.json").read_text(encoding="utf-8"))
                self.assertEqual(set(state), {"prefix", "image_id", "containers"})
                with self.assertRaisesRegex(AuthorityRuntimeError, "^DEPLOYMENT_CONFLICT$"):
                    AuthorityRuntime(default_folder, database_mode="partitioned-v6")
                v6_folder = root / "v6"
                first = AuthorityRuntime(v6_folder, database_mode="partitioned-v6")
                v6_state = json.loads((v6_folder / "deployment.json").read_text(encoding="utf-8"))
                self.assertEqual(v6_state["database_mode"], "partitioned-v6")
                self.assertEqual(AuthorityRuntime(v6_folder, database_mode="partitioned-v6").prefix,
                                 first.prefix)
                with self.assertRaisesRegex(AuthorityRuntimeError, "^DEPLOYMENT_CONFLICT$"):
                    AuthorityRuntime(v6_folder, database_mode="default")
                v6_state["unexpected"] = True
                (v6_folder / "deployment.json").write_text(json.dumps(v6_state), encoding="utf-8")
                with self.assertRaisesRegex(AuthorityRuntimeError, "^DEPLOYMENT_CONFLICT$"):
                    AuthorityRuntime(v6_folder, database_mode="partitioned-v6")

    def test_invalid_mode_is_rejected(self):
        with self.assertRaisesRegex(AuthorityRuntimeError, "^INVALID_RUNTIME_MODE$"):
            AuthorityRuntime("unused", database_mode="broker-v6")

    def test_broker_v6_config_requires_state_and_ipc_rw_mounts_and_exact_cmd(self):
        runtime = _runtime(mode="partitioned-v6")
        valid = _container(runtime, 12000, "broker-v6")
        runtime._verify_config(valid, 12000, "broker-v6")
        self.assertEqual({mount["Destination"] for mount in valid["Mounts"]}, {"/ipc", "/state"})
        drift = json.loads(json.dumps(valid))
        drift["Config"]["Cmd"] = ["broker"]
        with self.assertRaisesRegex(AuthorityRuntimeError, "^CONFIG_MISMATCH$"):
            runtime._verify_config(drift, 12000, "broker-v6")

    def test_create_uses_broker_v6_and_writable_state_mount(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = _runtime(mode="partitioned-v6")
            runtime.folder = Path(directory)
            runtime.state = {"prefix": runtime.prefix, "image_id": _IMAGE,
                             "containers": [], "database_mode": "partitioned-v6"}
            commands = []
            runtime.command = lambda args, **kwargs: commands.append(args) or b""
            runtime.inspect = mock.Mock(return_value=_container(runtime, 12000, "broker-v6"))
            runtime._create(runtime.prefix + "-broker", 12000, "broker-v6")
            create = next(args for args in commands if args[:2] == ["container", "create"])
            self.assertEqual(create[-2:], [_IMAGE, "broker-v6"])
            self.assertIn("type=volume,src=" + runtime.prefix + "-ipc,dst=/ipc", create)
            self.assertIn("type=volume,src=" + runtime.prefix + "-state,dst=/state", create)

    def test_v6_readiness_is_a_fixed_read_only_missing_run_check(self):
        runtime = _runtime(mode="partitioned-v6")
        running = _container(runtime, 12000, "broker-v6")
        running["State"].update(Running=True, Pid=22)
        runtime.inspect = mock.Mock(return_value=running)
        def diagnostic(request):
            return {"schema_version": 1, "kind": "evaluation_authority_result",
                "action": "authority_diagnostics", "request_id": request["request_id"],
                "ci_eligible": False, "database_schema_version": 6,
                "permission_generation": 0, "extension_digest": "d" * 64, "checked_at": 123}
        runtime.client = mock.Mock(side_effect=lambda _uid, request: diagnostic(request))
        runtime._wait_ready()
        request = runtime.client.call_args.args[1]
        self.assertEqual(request["action"], "authority_diagnostics")
        self.assertTrue(request["request_id"].startswith("runtime-v6-readiness-"))
        bad = _runtime(mode="partitioned-v6")
        bad.inspect = mock.Mock(return_value=running)
        wrong_schema = diagnostic({"request_id": "wrong"})
        wrong_schema["request_id"] = "runtime-v6-readiness-wrong"
        for schema_version in (4, 6.0, True, "6"):
            with self.subTest(schema_version=schema_version):
                bad = _runtime(mode="partitioned-v6")
                bad.inspect = mock.Mock(return_value=running)
                wrong_schema = diagnostic({"request_id": "wrong"})
                wrong_schema["request_id"] = "runtime-v6-readiness-wrong"
                wrong_schema["database_schema_version"] = schema_version
                bad.client = mock.Mock(return_value=wrong_schema)
                with self.assertRaisesRegex(AuthorityRuntimeError, "^BROKER_NOT_READY$"):
                    bad._wait_ready()

    def test_default_readiness_keeps_legacy_current_probe(self):
        runtime=_runtime(mode="default")
        running=_container(runtime,12000,"broker")
        running["State"].update(Running=True,Pid=21)
        runtime.inspect=mock.Mock(return_value=running)
        runtime.client=mock.Mock(side_effect=lambda _uid,request:{
            "schema_version":1,"kind":"policy_adoption_result","action":"current",
            "request_id":request["request_id"],"series_id":"runtime-readiness","ci_eligible":False})
        runtime._wait_ready()
        self.assertEqual(runtime.client.call_args.args[1]["action"],"current")

    def test_restart_checks_selected_mode_before_and_after_restart(self):
        runtime = _runtime(mode="partitioned-v6")
        expected = _container(runtime, 12000, "broker-v6")
        runtime.inspect = mock.Mock(return_value=expected)
        runtime.command = mock.Mock(return_value=b"")
        runtime._wait_ready = mock.Mock()
        runtime.restart_broker()
        self.assertEqual(runtime.inspect.call_count, 2)
        runtime.command.assert_called_once_with(["container", "restart", "--time", "1",
                                                 runtime.prefix + "-broker"])
        drift = _container(runtime, 12000, "broker")
        runtime.inspect = mock.Mock(return_value=drift)
        runtime.command.reset_mock()
        with self.assertRaisesRegex(AuthorityRuntimeError, "^CONFIG_MISMATCH$"):
            runtime.restart_broker()
        runtime.command.assert_not_called()

    def test_v6_cleanup_refuses_wrong_broker_mode_before_removal(self):
        runtime = _runtime(mode="partitioned-v6")
        runtime.inspect = mock.Mock(return_value=_container(runtime, 12000, "broker"))
        runtime.command = mock.Mock()
        with self.assertRaisesRegex(AuthorityRuntimeError, "^CONFIG_MISMATCH$"):
            runtime.remove_container(runtime.prefix + "-broker")
        runtime.command.assert_not_called()

    def test_cleanup_rejects_deployment_state_from_other_mode_before_docker(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = _runtime(mode="partitioned-v6")
            runtime.folder = Path(directory)
            runtime.state = {"prefix": runtime.prefix, "image_id": _IMAGE,
                             "containers": [], "database_mode": "partitioned-v6"}
            legacy = {"prefix": runtime.prefix, "image_id": _IMAGE, "containers": []}
            (runtime.folder / "deployment.json").write_text(json.dumps(legacy), encoding="utf-8")
            runtime.command = mock.Mock()
            with self.assertRaisesRegex(AuthorityRuntimeError, "^DEPLOYMENT_CONFLICT$"):
                runtime.cleanup()
            runtime.command.assert_not_called()


if __name__ == "__main__":
    import unittest
    unittest.main()