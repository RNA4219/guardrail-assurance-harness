"""trustedなローカル監督による、固定identityのauthority配置とclient起動。"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gah.contracts import ContractError, decode_document, require_object
from gah.docker_runner import capture_bounded
from gah.wire import canonical_bytes
from tools.prepare_authority_runtime import ENTRYPOINT


class AuthorityRuntimeError(ValueError):
    pass


class AuthorityRuntime:
    @staticmethod
    def _owned_container_name(prefix, name):
        if type(name) is not str:
            return False
        return (name == prefix + "-broker"
                or re.fullmatch(re.escape(prefix) + r"-client-[0-9a-f]{32}", name) is not None)

    @staticmethod
    def _option_tokens(value):
        if type(value) is not str:
            return None
        parts = tuple(part.strip() for part in value.split(","))
        if not parts or any(not part for part in parts):
            return None
        return tuple(sorted(parts))

    def _expected_tmpfs(self, uid):
        return {
            "/work": f"rw,noexec,nosuid,nodev,size=16777216,uid={uid},gid={uid},mode=700",
            "/tmp": f"rw,noexec,nosuid,nodev,size=16777216,uid={uid},gid={uid},mode=700",
            "/dev/shm": "ro,noexec,nosuid,nodev,size=1048576",
        }

    def _verify_image(self, image):
        try:
            valid = (type(image) is dict and image["Id"] == self.lock["image_id"]
                     and image["Os"] == self.lock["os"]
                     and image["Architecture"] == self.lock["architecture"]
                     and image["Config"]["WorkingDir"] == self.lock["working_dir"]
                     and image["Config"]["Entrypoint"] == ENTRYPOINT
                     and image["Config"]["Env"] == self.lock["environment"]
                     and image["Config"]["Labels"].get("org.gah.authority.sources")
                     == self.lock["sources_digest"])
        except (KeyError, TypeError, AttributeError):
            valid = False
        if not valid:
            raise AuthorityRuntimeError("IMAGE_MISMATCH")

    def _verify_volume(self, volume, name):
        try:
            valid = (type(volume) is dict and volume["Name"] == name
                     and volume["Driver"] == "local" and volume["Scope"] == "local"
                     and volume.get("Options") in (None, {})
                     and volume["Labels"].get("org.gah.authority.instance") == self.prefix)
        except (KeyError, TypeError, AttributeError):
            valid = False
        if not valid:
            raise AuthorityRuntimeError("OWNERSHIP_MISMATCH")

    def _confirm_volume_absent(self, name):
        """削除後に、所有volumeが存在しないことを成功した照会で確認する。"""
        try:
            self.command(["volume", "inspect", name])
        except AuthorityRuntimeError:
            listing = self.command(["volume", "ls", "--filter", "name=^" + name + "$", "--format", "{{.Name}}"])
            if listing.strip():
                raise AuthorityRuntimeError("CLEANUP_FAILED")
            return
        raise AuthorityRuntimeError("CLEANUP_FAILED")

    def _confirm_absent(self, name):
        """削除後に、所有対象名が存在しないことを成功した照会で確認する。"""
        try:
            self.inspect(name)
        except AuthorityRuntimeError:
            try:
                listing = self.command(["container", "ls", "--all", "--filter", "name=^/" + name + "$", "--format", "{{.ID}}"])
            except AuthorityRuntimeError:
                raise AuthorityRuntimeError("CLEANUP_FAILED") from None
            if listing.strip():
                raise AuthorityRuntimeError("CLEANUP_FAILED")
            return
        raise AuthorityRuntimeError("CLEANUP_FAILED")

    def __init__(self, folder):
        self.folder = Path(folder).resolve()
        self.folder.mkdir(parents=True, exist_ok=True)
        self.lock = decode_document((ROOT / "config/authority-runtime.lock.json").read_bytes())
        self.docker = shutil.which("docker")
        if self.docker is None or hashlib.sha256(Path(self.docker).read_bytes()).hexdigest() != self.lock["docker_binary_digest"]:
            raise AuthorityRuntimeError("DOCKER_UNAVAILABLE")
        self.endpoint = "npipe:////./pipe/dockerDesktopLinuxEngine" if os.name == "nt" else "unix:///var/run/docker.sock"
        self.environment = {key: os.environ[key] for key in ("PATH", "SystemRoot", "WINDIR", "USERPROFILE", "HOME", "TEMP", "TMP") if key in os.environ}
        state = self.folder / "deployment.json"
        if state.exists():
            self.state = decode_document(state.read_bytes())
            require_object(self.state, {"prefix", "image_id", "containers"})
            if (type(self.state["prefix"]) is not str
                    or re.fullmatch("gah-authority-[0-9a-f]{32}", self.state["prefix"]) is None
                    or type(self.state["image_id"]) is not str
                    or self.state["image_id"] != self.lock["image_id"]):
                raise AuthorityRuntimeError("DEPLOYMENT_CONFLICT")
            containers = self.state["containers"]
            if (type(containers) is not list or any(type(name) is not str for name in containers)
                    or len(set(containers)) != len(containers)
                    or any(not self._owned_container_name(self.state["prefix"], name) for name in containers)):
                raise AuthorityRuntimeError("DEPLOYMENT_CONFLICT")
        else:
            self.state = {"prefix": "gah-authority-" + uuid.uuid4().hex, "image_id": self.lock["image_id"], "containers": []}
            self._save()
        self.prefix = self.state["prefix"]

    def _save(self):
        path = self.folder / "deployment.json"
        temp = self.folder / "deployment.pending.json"
        temp.write_bytes(canonical_bytes(self.state))
        os.replace(temp, path)

    def command(self, args, *, input_bytes=None, timeout=10, limit=1024*1024):
        result = capture_bounded([self.docker, "--host", self.endpoint, *args], timeout=timeout,
            cwd=self.folder, environment=self.environment, input_bytes=input_bytes, limit=limit)
        if result.reason or result.returncode != 0:
            error = AuthorityRuntimeError(result.reason or "DOCKER_COMMAND_FAILED")
            error.detail = {"operation": args[:2], "exit_code": result.returncode, "reason": result.reason}
            try:
                value = json.loads(result.stdout)
                if (type(value) is dict and value.get("kind") == "authority_client_error"
                        and type(value.get("reason")) is str and re.fullmatch(r"[A-Z_]{1,64}", value["reason"])):
                    error.detail["client_reason"] = value["reason"]
            except (ValueError, TypeError):
                pass
            raise error
        return result.stdout

    def inspect(self, name):
        if not self._owned_container_name(self.prefix, name):
            raise AuthorityRuntimeError("OWNERSHIP_MISMATCH")
        data = json.loads(self.command(["container", "inspect", name]))[0]
        if data["Name"] != "/" + name or data["Config"]["Labels"].get("org.gah.authority.instance") != self.prefix:
            raise AuthorityRuntimeError("OWNERSHIP_MISMATCH")
        return data

    def _verify_config(self, data, uid, mode):
        try:
            host, config = data["HostConfig"], data["Config"]
            expected_tmpfs = self._expected_tmpfs(uid)
            expected_volumes = {"/ipc": (self.prefix + "-ipc", mode == "broker")}
            if mode == "broker":
                expected_volumes["/state"] = (self.prefix + "-state", True)
            mounts = data["Mounts"]
            tmpfs = host["Tmpfs"]
            mount_destinations = {item["Destination"] for item in mounts}
            expected_destinations = set(expected_volumes)
            mounts_valid = type(mounts) is list and len(mounts) == len(expected_destinations)
            if mounts_valid:
                for item in mounts:
                    destination = item["Destination"]
                    if destination in expected_volumes:
                        source, writable = expected_volumes[destination]
                        mounts_valid = (item["Type"] == "volume" and item.get("Name") == source
                                        and item.get("RW") is writable
                                        and item.get("Mode") == "z"
                                        and item.get("Propagation") == "")
                    else:
                        mounts_valid = False
                    if not mounts_valid:
                        break
            tmpfs_valid = (type(tmpfs) is dict and set(tmpfs) == set(expected_tmpfs)
                           and all(self._option_tokens(tmpfs[destination])
                                   == self._option_tokens(options)
                                   for destination, options in expected_tmpfs.items()))
            networks = data["NetworkSettings"]["Networks"]
            network = networks.get("none") if type(networks) is dict else None
            network_fields = {
                "IPAMConfig", "Links", "Aliases", "MacAddress", "DriverOpts", "GwPriority",
                "NetworkID", "EndpointID", "Gateway", "IPAddress", "IPPrefixLen", "IPv6Gateway",
                "GlobalIPv6Address", "GlobalIPv6PrefixLen", "DNSNames",
            }
            network_valid = (type(networks) is dict and set(networks) == {"none"}
                             and type(network) is dict
                             and set(network) == network_fields
                             and network.get("IPAMConfig") is None
                             and network.get("Links") is None
                             and network.get("Aliases") is None
                             and network.get("DriverOpts") is None
                             and network.get("DNSNames") is None
                             and network.get("GwPriority") == 0
                             and network.get("MacAddress") == ""
                             and network.get("Gateway") == ""
                             and network.get("IPAddress") == ""
                             and network.get("IPPrefixLen") == 0
                             and network.get("IPv6Gateway") == ""
                             and network.get("GlobalIPv6Address") == ""
                             and network.get("GlobalIPv6PrefixLen") == 0
                             and type(network.get("NetworkID")) is str
                             and type(network.get("EndpointID")) is str)
            healthcheck = config.get("Healthcheck")
            healthcheck_valid = (type(healthcheck) is dict and set(healthcheck) == {"Test"}
                                 and healthcheck["Test"] == ["NONE"])
            valid = (data["Image"] == self.lock["image_id"] and config["User"] == f"{uid}:{uid}"
                and config["WorkingDir"] == self.lock["working_dir"] and config["Entrypoint"] == ENTRYPOINT
                and config["Cmd"] == [mode] and config["Env"] == self.lock["environment"]
                and not config.get("Volumes") and not config.get("ExposedPorts")
                and healthcheck_valid and config["OpenStdin"] is True and config["Tty"] is False
                and host["NetworkMode"] == "none"
                and network_valid and host["ReadonlyRootfs"] is True and host["Privileged"] is False
                and host["CapDrop"] == ["ALL"] and not host.get("CapAdd")
                and host["SecurityOpt"] == ["no-new-privileges=true"] and host["Memory"] == 134217728
                and host["MemorySwap"] == 134217728 and host["NanoCpus"] == 500000000 and host["PidsLimit"] == 32
                and not host["PidMode"] and host["IpcMode"] == "private" and host["CgroupnsMode"] == "private"
                and host["LogConfig"]["Type"] == "none" and host["RestartPolicy"]["Name"] == "no"
                and not host.get("Binds") and not host.get("Devices") and not host.get("DeviceRequests")
                and not host.get("VolumesFrom") and not host.get("PortBindings") and not host.get("GroupAdd")
                and mounts_valid and mount_destinations == expected_destinations and tmpfs_valid)
        except (KeyError, TypeError, AttributeError):
            valid = False
        if not valid:
            raise AuthorityRuntimeError("CONFIG_MISMATCH")

    def _create(self, name, uid, mode):
        if name not in self.state["containers"]:
            self.state["containers"].append(name)
            self._save()
        args = ["container", "create", "--name", name, "--label", "org.gah.authority.instance=" + self.prefix,
            "--platform", "linux/amd64", "--network", "none", "--read-only", "--user", f"{uid}:{uid}",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges=true", "--memory", "134217728",
            "--memory-swap", "134217728", "--cpus", "0.5", "--pids-limit", "32", "--ipc", "private",
            "--cgroupns", "private", "--log-driver", "none", "--restart", "no", "--no-healthcheck", "--interactive",
            "--tmpfs", f"/work:rw,noexec,nosuid,nodev,size=16777216,uid={uid},gid={uid},mode=700",
            "--tmpfs", f"/tmp:rw,noexec,nosuid,nodev,size=16777216,uid={uid},gid={uid},mode=700",
            "--tmpfs", "/dev/shm:ro,noexec,nosuid,nodev,size=1048576",
            "--mount", "type=volume,src=" + self.prefix + "-ipc,dst=/ipc" + ("" if mode == "broker" else ",readonly")]
        if mode == "broker":
            args += ["--mount", "type=volume,src=" + self.prefix + "-state,dst=/state"]
        self.command(args + [self.lock["image_id"], mode])
        self._verify_config(self.inspect(name), uid, mode)

    def prepare(self):
        image = json.loads(self.command(["image", "inspect", self.lock["image_id"]]))[0]
        self._verify_image(image)
        for role in ("ipc", "state"):
            name = self.prefix + "-" + role
            self.command(["volume", "create", "--label", "org.gah.authority.instance=" + self.prefix, name])
            volume = json.loads(self.command(["volume", "inspect", name]))[0]
            self._verify_volume(volume, name)
        self._create(self.prefix + "-broker", 12000, "broker")
        self.command(["container", "start", self.prefix + "-broker"])
        self._verify_config(self.inspect(self.prefix + "-broker"), 12000, "broker")
        self._wait_ready()

    def _wait_ready(self):
        """変更要求より前に固定operatorの読み取りでbroker起動完了を確認する。"""
        for attempt in range(3):
            data = self.inspect(self.prefix + "-broker")
            if (data["State"]["Running"] is not True or type(data["State"]["Pid"]) is not int
                    or data["State"]["Pid"] <= 0):
                raise AuthorityRuntimeError("BROKER_NOT_READY")
            request = {"schema_version": 1, "action": "current", "series_id": "runtime-readiness",
                       "request_id": "runtime-readiness-" + uuid.uuid4().hex}
            try:
                result = self.client(12004, request)
            except AuthorityRuntimeError:
                if attempt < 2:
                    time.sleep(0.1)
                    continue
                raise AuthorityRuntimeError("BROKER_NOT_READY") from None
            if (type(result) is not dict or type(result.get("schema_version")) is not int
                    or result.get("schema_version") != 1 or result.get("kind") != "policy_adoption_result"
                    or result.get("action") != "current" or result.get("request_id") != request["request_id"]
                    or result.get("series_id") != request["series_id"] or result.get("ci_eligible") is not False):
                raise AuthorityRuntimeError("BROKER_NOT_READY")
            return

    def client(self, uid, request=None, *, probe=False):
        if type(uid) is not int or uid not in {12001, 12002, 12003, 12004}:
            raise AuthorityRuntimeError("IDENTITY_NOT_CONFIGURED")
        name = self.prefix + "-client-" + uuid.uuid4().hex
        try:
            self._create(name, uid, "probe" if probe else "client")
            raw = self.command(["container", "start", "--attach", "--interactive", name],
                               input_bytes=None if probe else canonical_bytes(request), timeout=45)
            data = self.inspect(name)
            self._verify_config(data, uid, "probe" if probe else "client")
            if data["State"]["Running"] or data["State"]["Pid"] != 0 or data["State"]["ExitCode"] != 0:
                raise AuthorityRuntimeError("CLIENT_FAILED")
            try:
                return decode_document(raw)
            except ContractError:
                raise AuthorityRuntimeError("CLIENT_RESPONSE_INVALID") from None
        finally:
            self.remove_container(name)

    def remove_container(self, name):
        if not self._owned_container_name(self.prefix, name):
            raise AuthorityRuntimeError("OWNERSHIP_MISMATCH")
        try:
            data = self.inspect(name)
        except AuthorityRuntimeError as inspect_error:
            try:
                listing = self.command(["container", "ls", "--all", "--filter", "name=^/" + name + "$", "--format", "{{.ID}}"])
            except AuthorityRuntimeError:
                raise AuthorityRuntimeError("STOP_UNCONFIRMED") from None
            if listing.strip():
                if inspect_error.args == ("OWNERSHIP_MISMATCH",):
                    raise
                raise AuthorityRuntimeError("STOP_UNCONFIRMED") from None
            return
        if data["State"]["Running"] or data["State"]["Pid"] != 0:
            self.command(["container", "kill", name])
            try:
                data = self.inspect(name)
            except AuthorityRuntimeError:
                raise AuthorityRuntimeError("STOP_UNCONFIRMED") from None
        if data["State"]["Running"] or data["State"]["Pid"] != 0:
            raise AuthorityRuntimeError("STOP_UNCONFIRMED")
        self.command(["container", "rm", name])
        self._confirm_absent(name)

    def restart_broker(self):
        name = self.prefix + "-broker"
        self._verify_config(self.inspect(name), 12000, "broker")
        self.command(["container", "restart", "--time", "1", name])
        self._verify_config(self.inspect(name), 12000, "broker")
        self._wait_ready()

    def cleanup(self, *, remove_state=False):
        for name in self.state["containers"]:
            self.remove_container(name)
        if remove_state:
            for role in ("ipc", "state"):
                name = self.prefix + "-" + role
                data = json.loads(self.command(["volume", "inspect", name]))[0]
                if data["Labels"].get("org.gah.authority.instance") != self.prefix:
                    raise AuthorityRuntimeError("OWNERSHIP_MISMATCH")
                self.command(["volume", "rm", name])
                self._confirm_volume_absent(name)
