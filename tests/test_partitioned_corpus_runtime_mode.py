"""明示schema-v7 authority起動モードとdefault/v6非回帰。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
from unittest import TestCase, mock

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gah.adoption import AdoptionError, AdoptionStore
from gah.authority import AuthorityError, _database_extension
from gah.evaluation_authority import EvaluationExtension
from gah.partitioned_corpus_authority import PartitionedCorpusEvaluationExtension
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
    broker = mode in {"broker", "broker-v6", "broker-v7"}
    host = {"Tmpfs": runtime._expected_tmpfs(uid), "NetworkMode": "none", "ReadonlyRootfs": True,
        "Privileged": False, "CapDrop": ["ALL"], "CapAdd": None,
        "SecurityOpt": ["no-new-privileges=true"], "Memory": 134217728, "MemorySwap": 134217728,
        "NanoCpus": 500000000, "PidsLimit": 32, "PidMode": None, "IpcMode": "private",
        "CgroupnsMode": "private", "LogConfig": {"Type": "none"}, "RestartPolicy": {"Name": "no"},
        "Binds": None, "Devices": None, "DeviceRequests": None,
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
    def test_factory_accepts_only_exact_default_v6_and_v7_modes(self):
        self.assertIs(type(_database_extension("default")), EvaluationExtension)
        self.assertIs(type(_database_extension("partitioned-v6")), PartitionedRunEvaluationExtension)
        self.assertIs(type(_database_extension("partitioned-v7")), PartitionedCorpusEvaluationExtension)
        self.assertEqual(_database_extension("partitioned-v7").schema_version, 7)
        for value in (None, "v7", "partitioned-v8", 7, True):
            with self.subTest(value=value), self.assertRaisesRegex(AuthorityError, "^DATABASE_MODE_INVALID$"):
                _database_extension(value)

    def test_only_new_v7_database_is_selected_and_existing_modes_are_not_migrated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fresh = root / "fresh-v7.sqlite"
            with AdoptionStore(fresh, extension=_database_extension("partitioned-v7")) as store:
                self.assertEqual(store._db.execute("PRAGMA user_version").fetchone()[0], 7)
                self.assertIs(type(store._extension), PartitionedCorpusEvaluationExtension)
                self.assertIsNotNone(store._db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='partition_scale_corpus_upload'"
                ).fetchone())
            for mode, version in (("default", 4), ("partitioned-v6", 6)):
                path = root / f"{mode}.sqlite"
                with AdoptionStore(path, extension=_database_extension(mode)) as store:
                    self.assertEqual(store._db.execute("PRAGMA user_version").fetchone()[0], version)
                with self.assertRaises(AdoptionError):
                    AdoptionStore(path, extension=_database_extension("partitioned-v7"))
                db = sqlite3.connect(path)
                try:
                    self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], version)
                    self.assertIsNone(db.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='partition_scale_corpus_upload'"
                    ).fetchone())
                finally:
                    db.close()

    def test_entrypoint_adds_only_explicit_broker_v7_mode(self):
        with mock.patch("sys.argv", ["authority_entry.py", "broker"]), \
             mock.patch.object(authority_entry, "serve") as serve:
            self.assertEqual(authority_entry.main(), 0)
            serve.assert_called_once_with(authority_entry.SOCKET, authority_entry.DATABASE)
        with mock.patch("sys.argv", ["authority_entry.py", "broker-v6"]), \
             mock.patch.object(authority_entry, "serve") as serve:
            self.assertEqual(authority_entry.main(), 0)
            serve.assert_called_once_with(authority_entry.SOCKET, authority_entry.DATABASE,
                                          database_mode="partitioned-v6")
        with mock.patch("sys.argv", ["authority_entry.py", "broker-v7"]), \
             mock.patch.object(authority_entry, "serve") as serve:
            self.assertEqual(authority_entry.main(), 0)
            serve.assert_called_once_with(authority_entry.SOCKET, authority_entry.DATABASE,
                                          database_mode="partitioned-v7")


class RuntimeModeTests(TestCase):
    def test_mode_state_is_explicit_and_cross_mode_reuse_is_rejected(self):
        self.assertEqual(AuthorityRuntime._broker_mode("default"), "broker")
        self.assertEqual(AuthorityRuntime._broker_mode("partitioned-v6"), "broker-v6")
        self.assertEqual(AuthorityRuntime._broker_mode("partitioned-v7"), "broker-v7")
        with self.assertRaisesRegex(AuthorityRuntimeError, "^INVALID_RUNTIME_MODE$"):
            AuthorityRuntime._broker_mode("partitioned-v8")
        base = {"prefix": _PREFIX, "image_id": _IMAGE, "containers": []}
        self.assertEqual(AuthorityRuntime._validate_deployment_state(base, "default", _IMAGE), base)
        for mode in ("partitioned-v6", "partitioned-v7"):
            state = {**base, "database_mode": mode}
            self.assertEqual(AuthorityRuntime._validate_deployment_state(state, mode, _IMAGE), state)
            with self.assertRaisesRegex(AuthorityRuntimeError, "^DEPLOYMENT_CONFLICT$"):
                AuthorityRuntime._validate_deployment_state(state, "default", _IMAGE)
        with self.assertRaisesRegex(AuthorityRuntimeError, "^DEPLOYMENT_CONFLICT$"):
            AuthorityRuntime._validate_deployment_state({**base, "database_mode": "partitioned-v7"},
                                                        "partitioned-v6", _IMAGE)
        for malformed in ([], {}, True, 7):
            with self.subTest(malformed=malformed), self.assertRaisesRegex(
                    AuthorityRuntimeError, "^DEPLOYMENT_CONFLICT$"):
                AuthorityRuntime._validate_deployment_state({**base, "database_mode": malformed},
                                                            "partitioned-v7", _IMAGE)

    def test_v7_config_create_restart_remove_checks_exact_broker_mode(self):
        runtime = _runtime(mode="partitioned-v7")
        valid = _container(runtime, 12000, "broker-v7")
        runtime._verify_config(valid, 12000, "broker-v7")
        self.assertEqual({mount["Destination"] for mount in valid["Mounts"]}, {"/ipc", "/state"})
        drift = json.loads(json.dumps(valid))
        drift["Config"]["Cmd"] = ["broker-v6"]
        with self.assertRaisesRegex(AuthorityRuntimeError, "^CONFIG_MISMATCH$"):
            runtime._verify_config(drift, 12000, "broker-v7")
        runtime.state = {"prefix": _PREFIX, "image_id": _IMAGE, "containers": [],
                         "database_mode": "partitioned-v7"}
        runtime.folder = Path(".")
        runtime._save = mock.Mock()
        runtime.command = mock.Mock(return_value=b"")
        runtime.inspect = mock.Mock(return_value=valid)
        runtime._create(_PREFIX + "-broker", 12000, "broker-v7")
        create = runtime.command.call_args.args[0]
        self.assertEqual(create[-2:], [_IMAGE, "broker-v7"])
        runtime.command.reset_mock()
        runtime._wait_ready = mock.Mock()
        runtime.restart_broker()
        self.assertEqual(runtime.inspect.call_count, 3)
        self.assertEqual(runtime.command.call_args.args[0], ["container", "restart", "--time", "1",
                                                            _PREFIX + "-broker"])
        runtime.inspect.return_value = _container(runtime, 12000, "broker-v6")
        runtime.command.reset_mock()
        with self.assertRaisesRegex(AuthorityRuntimeError, "^CONFIG_MISMATCH$"):
            runtime.remove_container(_PREFIX + "-broker")
        runtime.command.assert_not_called()

    def test_constructor_persists_v7_mode_in_deployment_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config"
            config.mkdir()
            docker = root / "docker"
            docker.write_bytes(b"fixed test executable")
            (config / "authority-runtime.lock.json").write_text(json.dumps({
                "docker_binary_digest": hashlib.sha256(docker.read_bytes()).hexdigest(),
                "image_id": _IMAGE,
            }), encoding="utf-8")
            folder = root / "deployment"
            with mock.patch("tools.authority_runtime.ROOT", root), \
                 mock.patch("tools.authority_runtime.shutil.which", return_value=str(docker)):
                runtime = AuthorityRuntime(folder, database_mode="partitioned-v7")
                state = json.loads((folder / "deployment.json").read_text(encoding="utf-8"))
                self.assertEqual(state["database_mode"], "partitioned-v7")
                self.assertEqual(AuthorityRuntime(folder, database_mode="partitioned-v7").prefix,
                                 runtime.prefix)
                with self.assertRaisesRegex(AuthorityRuntimeError, "^DEPLOYMENT_CONFLICT$"):
                    AuthorityRuntime(folder, database_mode="partitioned-v6")

    def test_v7_cleanup_refuses_other_mode_state_before_docker(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = _runtime(mode="partitioned-v7")
            runtime.folder = Path(directory)
            runtime.state = {"prefix": _PREFIX, "image_id": _IMAGE, "containers": [],
                             "database_mode": "partitioned-v7"}
            wrong = {"prefix": _PREFIX, "image_id": _IMAGE, "containers": [],
                     "database_mode": "partitioned-v6"}
            (runtime.folder / "deployment.json").write_text(json.dumps(wrong), encoding="utf-8")
            runtime.command = mock.Mock()
            with self.assertRaisesRegex(AuthorityRuntimeError, "^DEPLOYMENT_CONFLICT$"):
                runtime.cleanup()
            runtime.command.assert_not_called()

    def test_v7_readiness_requires_schema_seven_and_fixed_diagnostic(self):
        runtime = _runtime(mode="partitioned-v7")
        running = _container(runtime, 12000, "broker-v7")
        running["State"].update(Running=True, Pid=27)
        runtime.inspect = mock.Mock(return_value=running)
        def response(request, version=7):
            return {"schema_version": 1, "kind": "evaluation_authority_result",
                "action": "authority_diagnostics", "request_id": request["request_id"],
                "ci_eligible": False, "database_schema_version": version,
                "permission_generation": 0, "extension_digest": "d" * 64, "checked_at": 123}
        runtime.client = mock.Mock(side_effect=lambda _uid, request: response(request))
        runtime._wait_ready()
        req = runtime.client.call_args.args[1]
        self.assertEqual(req["action"], "authority_diagnostics")
        self.assertTrue(req["request_id"].startswith("runtime-v7-readiness-"))
        bad = _runtime(mode="partitioned-v7")
        bad.inspect = mock.Mock(return_value=running)
        for version in (4, 6, 7.0, True, "7"):
            with self.subTest(version=version):
                bad = _runtime(mode="partitioned-v7")
                bad.inspect = mock.Mock(return_value=running)
                bad.client = mock.Mock(side_effect=lambda _uid, request, v=version: response(request, v))
                with self.assertRaisesRegex(AuthorityRuntimeError, "^BROKER_NOT_READY$"):
                    bad._wait_ready()


if __name__ == "__main__":
    import unittest
    unittest.main()
