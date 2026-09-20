"""trustedなローカル監督による、固定identityのauthority配置とclient起動。"""
import hashlib
import json
from contextlib import contextmanager
import os
from pathlib import Path
import re
import shutil
import sys
import time
from threading import TIMEOUT_MAX
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gah.contracts import (ContractError, MAX_DOCUMENT_BYTES, decode_document,
                           require_digest, require_id, require_object, require_uint)
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
    def _is_broker_mode(mode):
        return mode in {"broker", "broker-v6", "broker-v7"}

    @staticmethod
    def _broker_mode(database_mode):
        modes = {"default": "broker", "partitioned-v6": "broker-v6", "partitioned-v7": "broker-v7"}
        try:
            return modes[database_mode]
        except (KeyError, TypeError):
            raise AuthorityRuntimeError("INVALID_RUNTIME_MODE") from None

    @staticmethod
    def _validate_deployment_state(value, database_mode, image_id):
        base_fields={"prefix","image_id","containers"}
        if type(value) is not dict:
            raise AuthorityRuntimeError("DEPLOYMENT_CONFLICT")
        if set(value)==base_fields:
            saved_mode="default"
        elif (set(value)==base_fields|{"database_mode"}
                and type(value.get("database_mode")) is str
                and value["database_mode"] in {"partitioned-v6", "partitioned-v7"}):
            saved_mode=value["database_mode"]
        else:
            raise AuthorityRuntimeError("DEPLOYMENT_CONFLICT")
        if saved_mode!=database_mode:
            raise AuthorityRuntimeError("DEPLOYMENT_CONFLICT")
        if (type(value["prefix"]) is not str
                or re.fullmatch("gah-authority-[0-9a-f]{32}",value["prefix"]) is None
                or type(value["image_id"]) is not str or value["image_id"]!=image_id):
            raise AuthorityRuntimeError("DEPLOYMENT_CONFLICT")
        containers=value["containers"]
        if (type(containers) is not list or any(type(name) is not str for name in containers)
                or len(set(containers))!=len(containers)
                or any(not AuthorityRuntime._owned_container_name(value["prefix"],name) for name in containers)):
            raise AuthorityRuntimeError("DEPLOYMENT_CONFLICT")
        return value

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

    def __init__(self, folder, *, reuse_clients=False, keep_clients_running=False,
                 database_mode="default"):
        import threading
        if (type(database_mode) is not str or database_mode not in {"default","partitioned-v6","partitioned-v7"}
                or type(reuse_clients) is not bool or type(keep_clients_running) is not bool
                or (keep_clients_running and not reuse_clients)):
            raise AuthorityRuntimeError("INVALID_RUNTIME_MODE")
        self.database_mode=database_mode
        self.reuse_clients = reuse_clients
        self.keep_clients_running = keep_clients_running
        self._running_clients = {}
        self._reusable_clients = {}
        self._client_mutex = threading.RLock()
        self._last_authority_clock = None
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
            self.state=self._validate_deployment_state(
                decode_document(state.read_bytes()),self.database_mode,self.lock["image_id"])
        else:
            self.state={"prefix":"gah-authority-"+uuid.uuid4().hex,
                "image_id":self.lock["image_id"],"containers":[]}
            if self.database_mode!="default":
                self.state["database_mode"]=self.database_mode
            self._save()
        self.prefix = self.state["prefix"]

    def _save(self):
        """Persist deployment state under the caller's same-deployment exclusion.

        The read/merge/write sequence is not a cross-process compare-and-swap.
        """
        database_mode=getattr(self,"database_mode","default")
        self._validate_deployment_state(self.state,database_mode,self.lock["image_id"])
        path=self.folder/"deployment.json"
        if path.exists():
            try:
                raw=path.read_bytes()
                if len(raw)>MAX_DOCUMENT_BYTES:
                    raise AuthorityRuntimeError("DEPLOYMENT_CONFLICT")
                current=self._validate_deployment_state(
                    decode_document(raw),database_mode,self.lock["image_id"])
            except (OSError,ContractError,KeyError,TypeError):
                raise AuthorityRuntimeError("DEPLOYMENT_CONFLICT") from None
            if current["prefix"]!=self.state["prefix"]:
                raise AuthorityRuntimeError("DEPLOYMENT_CONFLICT")
            self.state["containers"]=list(dict.fromkeys(current["containers"]+self.state["containers"]))
        from gah.bounded_files import BoundedFileError, write_bounded
        try:
            write_bounded(path, canonical_bytes(self.state), immutable=False)
        except BoundedFileError as error:
            raise AuthorityRuntimeError(error.code) from None

    @staticmethod
    def _validate_deadline(deadline_ns):
        if deadline_ns is not None and (type(deadline_ns) is not int
                or deadline_ns < 0 or deadline_ns > 2**63 - 1):
            raise AuthorityRuntimeError("INVALID_DEADLINE")

    @classmethod
    def _remaining_seconds(cls, deadline_ns):
        cls._validate_deadline(deadline_ns)
        if deadline_ns is None:
            return None
        remaining_ns = deadline_ns - time.monotonic_ns()
        if remaining_ns <= 0:
            raise AuthorityRuntimeError("TIMEOUT")
        return remaining_ns / 1_000_000_000

    @classmethod
    def _deadline_timeout(cls, timeout, deadline_ns):
        remaining = cls._remaining_seconds(deadline_ns)
        return timeout if remaining is None else min(timeout, remaining)

    @contextmanager
    def _client_lock(self, deadline_ns):
        if deadline_ns is None:
            with self._client_mutex:
                yield
            return
        # Windows limits one Lock.acquire wait to less than the accepted deadline range.
        while True:
            remaining = self._remaining_seconds(deadline_ns)
            if self._client_mutex.acquire(timeout=min(remaining, TIMEOUT_MAX)):
                break
        try:
            # The lock may have become available at or just after the deadline.
            self._remaining_seconds(deadline_ns)
            yield
        finally:
            self._client_mutex.release()

    def command(self, args, *, input_bytes=None, timeout=10, limit=1024*1024,
                deadline_ns=None):
        if deadline_ns is not None:
            timeout = self._deadline_timeout(timeout, deadline_ns)
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

    def inspect(self, name, *, deadline_ns=None):
        if not self._owned_container_name(self.prefix, name):
            raise AuthorityRuntimeError("OWNERSHIP_MISMATCH")
        kwargs = {} if deadline_ns is None else {"deadline_ns": deadline_ns}
        data = json.loads(self.command(["container", "inspect", name], **kwargs))[0]
        if data["Name"] != "/" + name or data["Config"]["Labels"].get("org.gah.authority.instance") != self.prefix:
            raise AuthorityRuntimeError("OWNERSHIP_MISMATCH")
        return data

    def _verify_config(self, data, uid, mode):
        try:
            host, config = data["HostConfig"], data["Config"]
            expected_tmpfs = self._expected_tmpfs(uid)
            broker_mode=self._is_broker_mode(mode)
            expected_volumes = {"/ipc": (self.prefix + "-ipc", broker_mode)}
            if broker_mode:
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

    def _create(self, name, uid, mode, *, deadline_ns=None):
        if deadline_ns is not None:
            self._remaining_seconds(deadline_ns)
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
            "--mount", "type=volume,src=" + self.prefix + "-ipc,dst=/ipc" + ("" if self._is_broker_mode(mode) else ",readonly")]
        if self._is_broker_mode(mode):
            args += ["--mount", "type=volume,src=" + self.prefix + "-state,dst=/state"]
        kwargs = {} if deadline_ns is None else {"deadline_ns": deadline_ns}
        self.command(args + [self.lock["image_id"], mode], **kwargs)
        self._verify_config(self.inspect(name, **kwargs), uid, mode)

    def prepare(self):
        image = json.loads(self.command(["image", "inspect", self.lock["image_id"]]))[0]
        self._verify_image(image)
        for role in ("ipc", "state"):
            name = self.prefix + "-" + role
            self.command(["volume", "create", "--label", "org.gah.authority.instance=" + self.prefix, name])
            volume = json.loads(self.command(["volume", "inspect", name]))[0]
            self._verify_volume(volume, name)
        broker_mode=self._broker_mode(getattr(self,"database_mode","default"))
        self._create(self.prefix + "-broker", 12000, broker_mode)
        self.command(["container", "start", self.prefix + "-broker"])
        self._verify_config(self.inspect(self.prefix + "-broker"), 12000, broker_mode)
        self._wait_ready()

    def _wait_ready(self):
        """固定operatorのreadonly actionで選択済みbroker modeを確認する。"""
        for attempt in range(3):
            data=self.inspect(self.prefix+"-broker")
            if (data["State"]["Running"] is not True or type(data["State"]["Pid"]) is not int
                    or data["State"]["Pid"]<=0):
                raise AuthorityRuntimeError("BROKER_NOT_READY")
            if getattr(self,"database_mode","default")=="default":
                request={"schema_version":1,"action":"current","series_id":"runtime-readiness",
                    "request_id":"runtime-readiness-"+uuid.uuid4().hex}
            else:
                request={"schema_version":1,"action":"authority_diagnostics",
                    "request_id":"runtime-" + ("v6" if self.database_mode=="partitioned-v6" else "v7")
                        + "-readiness-"+uuid.uuid4().hex}
            try:
                result=self.client(12004,request)
            except AuthorityRuntimeError:
                if attempt<2:
                    time.sleep(0.1)
                    continue
                raise AuthorityRuntimeError("BROKER_NOT_READY") from None
            if getattr(self,"database_mode","default")=="default":
                valid=(type(result) is dict and type(result.get("schema_version")) is int
                    and result.get("schema_version")==1 and result.get("kind")=="policy_adoption_result"
                    and result.get("action")=="current" and result.get("request_id")==request["request_id"]
                    and result.get("series_id")==request["series_id"] and result.get("ci_eligible") is False)
            else:
                fields={"schema_version","kind","action","request_id","ci_eligible",
                        "database_schema_version","permission_generation","extension_digest","checked_at"}
                valid=(type(result) is dict and set(result)==fields
                    and type(result.get("schema_version")) is int and result.get("schema_version")==1
                    and result.get("kind")=="evaluation_authority_result"
                    and result.get("action")=="authority_diagnostics"
                    and result.get("request_id")==request["request_id"]
                    and type(result.get("database_schema_version")) is int
                    and result.get("database_schema_version")== (6 if self.database_mode=="partitioned-v6" else 7)
                    and type(result.get("permission_generation")) is int
                    and result.get("ci_eligible") is False)
                if valid:
                    try:
                        require_uint(result["permission_generation"])
                        require_digest(result["extension_digest"])
                        require_uint(result["checked_at"])
                    except ContractError:
                        valid=False
            if not valid:
                raise AuthorityRuntimeError("BROKER_NOT_READY")
            return

    def client(self, uid, request=None, *, probe=False, deadline_ns=None):
        """Invoke a fixed authority client, optionally bounded by an absolute monotonic deadline."""
        if type(uid) is not int or uid not in {12001, 12002, 12003, 12004}:
            raise AuthorityRuntimeError("IDENTITY_NOT_CONFIGURED")
        self._validate_deadline(deadline_ns)
        if deadline_ns is not None:
            self._remaining_seconds(deadline_ns)
        if probe:
            if deadline_ns is None:
                return self._client_once(uid, request, probe=True)
            return self._client_once(uid, request, probe=True, deadline_ns=deadline_ns)
        raw = canonical_bytes(request)
        for attempt in range(2):
            if deadline_ns is not None:
                self._remaining_seconds(deadline_ns)
                result = self._client_once(uid, decode_document(raw), deadline_ns=deadline_ns)
            else:
                result = self._client_once(uid, decode_document(raw))
            if (type(result) is not dict
                    or set(result) != {"schema_version", "kind", "reason", "ci_eligible"}
                    or type(result["schema_version"]) is not int or result["schema_version"] != 1
                    or result["kind"] != "authority_error" or result["reason"] != "CLOCK_ROLLBACK"
                    or result["ci_eligible"] is not False):
                return result
            # This response rolled back the authority transaction; a retry is a new bounded dispatch.
            if deadline_ns is None:
                retry_scheduled = attempt == 0
            else:
                remaining = (deadline_ns - time.monotonic_ns()) / 1_000_000_000
                retry_scheduled = attempt == 0 and remaining > 2.0
            self._record_clock_rejection(uid, raw, retry_scheduled=retry_scheduled)
            if deadline_ns is not None and remaining <= 0:
                raise AuthorityRuntimeError("TIMEOUT")
            if attempt == 0:
                if deadline_ns is not None and not retry_scheduled:
                    raise AuthorityRuntimeError("TIMEOUT")
                if deadline_ns is None:
                    time.sleep(2)
                else:
                    self._remaining_seconds(deadline_ns)
                    time.sleep(min(2.0, self._remaining_seconds(deadline_ns)))
                    self._remaining_seconds(deadline_ns)
        return result

    def clock(self):
        """Read UTC seconds from the fresh authority diagnostic."""
        request = {
            "schema_version": 1,
            "action": "authority_diagnostics",
            "request_id": "runtime-clock-" + uuid.uuid4().hex,
        }
        value = self.client(12004, request)
        if (type(value) is dict and value.get("kind") == "authority_error"
                and value.get("reason") == "CLOCK_ROLLBACK"):
            raise AuthorityRuntimeError("CLOCK_ROLLBACK")
        fields = {
            "schema_version", "kind", "action", "request_id", "ci_eligible",
            "database_schema_version", "permission_generation", "extension_digest",
            "checked_at",
        }
        if type(value) is not dict or set(value) != fields:
            raise AuthorityRuntimeError("CLOCK_RESPONSE_INVALID")
        if (type(value["schema_version"]) is not int or value["schema_version"] != 1
                or value["kind"] != "evaluation_authority_result"
                or value["action"] != "authority_diagnostics"
                or value["ci_eligible"] is not False):
            raise AuthorityRuntimeError("CLOCK_RESPONSE_INVALID")
        try:
            require_id(value["request_id"])
            require_uint(value["database_schema_version"])
            require_uint(value["permission_generation"])
            require_digest(value["extension_digest"])
            require_uint(value["checked_at"])
        except ContractError:
            raise AuthorityRuntimeError("CLOCK_RESPONSE_INVALID") from None
        if value["request_id"] != request["request_id"]:
            raise AuthorityRuntimeError("CLOCK_RESPONSE_INVALID")
        previous = getattr(self, "_last_authority_clock", None)
        if previous is not None:
            try:
                require_uint(previous)
            except ContractError:
                raise AuthorityRuntimeError("CLOCK_RESPONSE_INVALID") from None
            if value["checked_at"] < previous:
                raise AuthorityRuntimeError("CLOCK_ROLLBACK")
        self._last_authority_clock = value["checked_at"]
        return value["checked_at"]

    def _record_clock_rejection(self, uid, raw, *, retry_scheduled):
        event = {"schema_version": 1, "kind": "authority_clock_rejection",
            "uid": uid, "request_digest": hashlib.sha256(raw).hexdigest(),
            "reason": "CLOCK_ROLLBACK", "retry_scheduled": retry_scheduled}
        from gah.bounded_files import BoundedFileError, write_bounded
        try:
            folder = self.folder / "clock-rejections"
            write_bounded(folder / (uuid.uuid4().hex + ".json"), canonical_bytes(event))
        except (OSError, BoundedFileError):
            raise AuthorityRuntimeError("CLOCK_RECOVERY_UNAVAILABLE") from None

    def _client_once(self, uid, request=None, *, probe=False, deadline_ns=None):
        if deadline_ns is not None:
            self._remaining_seconds(deadline_ns)
        deadline_kwargs = {} if deadline_ns is None else {"deadline_ns": deadline_ns}
        if getattr(self, "keep_clients_running", False) and not probe:
            return self._running_client_once(uid, request, **deadline_kwargs)
        if getattr(self, "reuse_clients", False):
            return self._reused_client_once(uid, request, probe=probe, **deadline_kwargs)
        if type(uid) is not int or uid not in {12001, 12002, 12003, 12004}:
            raise AuthorityRuntimeError("IDENTITY_NOT_CONFIGURED")
        name = self.prefix + "-client-" + uuid.uuid4().hex
        try:
            self._create(name, uid, "probe" if probe else "client", **deadline_kwargs)
            raw = self.command(["container", "start", "--attach", "--interactive", name],
                input_bytes=None if probe else canonical_bytes(request), timeout=45, **deadline_kwargs)
            data = self.inspect(name, **deadline_kwargs)
            self._verify_config(data, uid, "probe" if probe else "client")
            if data["State"]["Running"] or data["State"]["Pid"] != 0 or data["State"]["ExitCode"] != 0:
                raise AuthorityRuntimeError("CLIENT_FAILED")
            try:
                return decode_document(raw)
            except ContractError:
                raise AuthorityRuntimeError("CLIENT_RESPONSE_INVALID") from None
        finally:
            # Cleanup has its own finite command timeouts and is always attempted; it is not
            # the authority transaction and its outcome does not imply transaction cancellation.
            self.remove_container(name)

    def _reused_client_once(self, uid, request=None, *, probe=False, deadline_ns=None):
        """固定UID client。mutex/transportは期限付き、失敗後の回収は有限予算で試みる。"""
        if type(uid) is not int or uid not in {12001, 12002, 12003, 12004}:
            raise AuthorityRuntimeError("IDENTITY_NOT_CONFIGURED")
        mode = "probe" if probe else "client"
        key = (uid, mode)
        with self._client_lock(deadline_ns):
            name = self._reusable_clients.get(key)
            if name is None:
                name = self.prefix + "-client-" + uuid.uuid4().hex
            try:
                if key not in self._reusable_clients:
                    self._create(name, uid, mode, **({} if deadline_ns is None else {"deadline_ns": deadline_ns}))
                    self._reusable_clients[key] = name
                kwargs = {} if deadline_ns is None else {"deadline_ns": deadline_ns}
                before = self.inspect(name, **kwargs)
                self._verify_config(before, uid, mode)
                if (before["State"]["Running"] or before["State"]["Pid"] != 0
                        or before["State"]["Status"] not in {"created", "exited"}):
                    raise AuthorityRuntimeError("CLIENT_ACTIVE")
                raw = self.command(["container", "start", "--attach", "--interactive", name],
                    input_bytes=None if probe else canonical_bytes(request), timeout=45, **kwargs)
                after = self.inspect(name, **kwargs)
                self._verify_config(after, uid, mode)
                if (after["Id"] != before["Id"] or after["State"]["Running"]
                        or after["State"]["Pid"] != 0 or after["State"]["ExitCode"] != 0):
                    raise AuthorityRuntimeError("CLIENT_FAILED")
                try:
                    return decode_document(raw)
                except ContractError:
                    raise AuthorityRuntimeError("CLIENT_RESPONSE_INVALID") from None
            except BaseException:
                self._reusable_clients.pop(key, None)
                self.remove_container(name)
                raise

    def _running_client_once(self, uid, request, *, deadline_ns=None):
        """実行中固定container上のexec。deadlineは全新規dispatchへ伝播する。"""
        if type(uid) is not int or uid not in {12001,12002,12003,12004}:
            raise AuthorityRuntimeError("IDENTITY_NOT_CONFIGURED")
        mode = "client-host"
        with self._client_lock(deadline_ns):
            name = self._running_clients.get(uid)
            created = name is None
            if created:
                name = self.prefix + "-client-" + uuid.uuid4().hex
                self._running_clients[uid] = name
            kwargs = {} if deadline_ns is None else {"deadline_ns": deadline_ns}
            try:
                if created:
                    self._create(name, uid, mode, **kwargs)
                    self.command(["container", "start", name], **kwargs)
                before = self.inspect(name, **kwargs)
                self._verify_config(before, uid, mode)
                if (before["State"]["Running"] is not True or before["State"]["Pid"] <= 0
                        or before["State"]["Status"] != "running" or before["State"].get("OOMKilled") is not False
                        or before.get("RestartCount") != 0):
                    raise AuthorityRuntimeError("CLIENT_FAILED")
                raw = self.command(["container", "exec", "--interactive", "--user", f"{uid}:{uid}", name,
                    "/usr/local/bin/python", "-I", "-B", "/opt/gah/tools/authority_entry.py", "client"],
                    input_bytes=canonical_bytes(request), timeout=45, **kwargs)
                after = self.inspect(name, **kwargs)
                self._verify_config(after, uid, mode)
                if (after["Id"] != before["Id"] or after["State"]["Running"] is not True
                        or after["State"]["Pid"] != before["State"]["Pid"]
                        or after["State"]["StartedAt"] != before["State"]["StartedAt"]
                        or after["State"]["Status"] != "running" or after["State"].get("OOMKilled") is not False
                        or after.get("RestartCount") != 0):
                    raise AuthorityRuntimeError("CLIENT_FAILED")
                try:
                    return decode_document(raw)
                except ContractError:
                    raise AuthorityRuntimeError("CLIENT_RESPONSE_INVALID") from None
            except BaseException:
                # Cleanup is finite and independent of the expired dispatch deadline.
                self.remove_container(name)
                del self._running_clients[uid]
                raise

    def close_clients(self):
        """このインスタンスが再利用したclientだけを回収し、brokerと保存volumeは保持する。"""
        failed = None
        with self._client_mutex:
            for clients in (getattr(self, "_running_clients", {}), self._reusable_clients):
                for key, name in list(clients.items()):
                    try:
                        self.remove_container(name)
                    except BaseException as error:
                        if failed is None:
                            failed = error
                    else:
                        del clients[key]
        if failed is not None:
            raise failed

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
        if name==self.prefix+"-broker" and getattr(self,"database_mode","default")!="default":
            self._verify_config(data,12000,self._broker_mode(self.database_mode))
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
        broker_mode=self._broker_mode(getattr(self,"database_mode","default"))
        self._verify_config(self.inspect(name),12000,broker_mode)
        self.command(["container","restart","--time","1",name])
        self._verify_config(self.inspect(name),12000,broker_mode)
        self._wait_ready()

    def cleanup(self, *, remove_state=False):
        # 別のCLIが同じdeploymentへ追加した所有記録も、回収直前に照合する。
        try:
            with (self.folder / "deployment.json").open("rb") as stream:
                current = decode_document(stream.read(MAX_DOCUMENT_BYTES + 1))
            current=self._validate_deployment_state(current,self.database_mode,self.lock["image_id"])
            names=current["containers"]
            if current["prefix"]!=self.prefix:
                raise AuthorityRuntimeError("DEPLOYMENT_CONFLICT")
            names = list(dict.fromkeys(self.state["containers"] + names))
            if any(not self._owned_container_name(self.prefix, name) for name in names):
                raise AuthorityRuntimeError("DEPLOYMENT_CONFLICT")
        except (OSError, ContractError, KeyError, TypeError):
            raise AuthorityRuntimeError("DEPLOYMENT_CONFLICT") from None
        self.state["containers"] = names
        self._save()
        failure = None
        for name in names:
            try:
                self.remove_container(name)
            except Exception as error:
                if failure is None:
                    failure = error
        if failure is not None:
            raise failure
        remaining = self.command(["container", "ls", "--all", "--filter",
            "label=org.gah.authority.instance=" + self.prefix, "--format", "{{.Names}}"])
        if remaining.strip():
            raise AuthorityRuntimeError("CLEANUP_INCOMPLETE")
        if remove_state:
            for role in ("ipc", "state"):
                name = self.prefix + "-" + role
                data = json.loads(self.command(["volume", "inspect", name]))[0]
                if data["Labels"].get("org.gah.authority.instance") != self.prefix:
                    raise AuthorityRuntimeError("OWNERSHIP_MISMATCH")
                self.command(["volume", "rm", name])
                self._confirm_volume_absent(name)
