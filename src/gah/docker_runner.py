"""内容固定fixtureを、ローカルDockerの制限済みcontainerで実行する。"""
from dataclasses import dataclass
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
from typing import Callable

from .contracts import ContractError, decode_document, require_digest, require_object, require_uint
from .execution_journal import ExecutionJournal, JournalError
from .normalized import normalize_generic, validate_binding
from .wire import canonical_bytes

STREAM_LIMIT = 256 * 1024
CONTROL_LIMIT = 128 * 1024
MEMORY = 128 * 1024 * 1024
ENTRYPOINT = ["/usr/local/bin/python", "-I", "-B", "/opt/gah/fixture_worker.py"]
PROBE_KEYS = {"nonroot", "root_readonly", "network_unreachable", "capabilities_dropped",
              "no_new_privileges", "seccomp_filter", "pid_limit", "memory_limit", "cpu_limit"}
TMPFS = {"/work": "rw,noexec,nosuid,nodev,size=16777216,mode=700,uid=65532,gid=65532",
         "/tmp": "rw,noexec,nosuid,nodev,size=16777216,mode=700,uid=65532,gid=65532",
         "/dev/shm": "ro,noexec,nosuid,nodev,size=1048576"}
PROFILE = {"network": "none", "readonly": True, "user": "65532:65532", "cap_drop": ["ALL"],
           "no_new_privileges": True, "memory": MEMORY, "memory_swap": MEMORY,
           "nano_cpus": 500000000, "pids_limit": 32, "tmpfs": TMPFS, "log_driver": "none"}
_IMAGE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_CONTAINER = re.compile(r"[0-9a-f]{64}\Z")


class RunnerError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@contextmanager
def operation_lock(journal_path: Path, run_id: str, operation_id: str):
    """同じローカル監督間の排他。owner死亡時にOSが解放し、回復は生存ownerを奪わない。"""
    key = hashlib.sha256(canonical_bytes([run_id, operation_id])).hexdigest()
    path = journal_path.resolve().with_name(journal_path.name + "." + key + ".lock")
    with path.open("a+b") as handle:
        if handle.seek(0, os.SEEK_END) == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RunnerError("OWNER_ACTIVE") from None
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@dataclass
class Capture:
    returncode: int | None
    stdout: bytes
    stderr: bytes
    reason: str | None


def _digest_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def capture_bounded(argv: list[str], *, timeout: float, cwd: Path, environment: dict[str, str],
                    input_bytes: bytes | None = None, limit: int = STREAM_LIMIT,
                    cancel_event: threading.Event | None = None) -> Capture:
    """各streamの上限を超えて収集せず、検査前の出力を通常logへ出さない。"""
    if timeout <= 0:
        return Capture(None, b"", b"", "TIMEOUT")
    if cancel_event is not None and cancel_event.is_set():
        return Capture(None, b"", b"", "CANCEL_REQUESTED")
    try:
        process = subprocess.Popen(argv, cwd=cwd, env=environment, shell=False,
            stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    except OSError:
        return Capture(None, b"", b"", "DOCKER_UNAVAILABLE")
    buffers = [bytearray(), bytearray()]
    overflow = threading.Event()
    read_failure = threading.Event()
    def read_stream(stream, index):
        try:
            while True:
                block = stream.read(4096)
                if not block:
                    break
                if len(buffers[index]) + len(block) > limit:
                    overflow.set()
                    break
                buffers[index].extend(block)
        except (OSError, ValueError):
            read_failure.set()
        finally:
            stream.close()
    def write_input():
        try:
            process.stdin.write(input_bytes)
            process.stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            pass
        finally:
            process.stdin.close()
    threads = [threading.Thread(target=read_stream, args=(process.stdout, 0), daemon=True),
               threading.Thread(target=read_stream, args=(process.stderr, 1), daemon=True)]
    if input_bytes is not None:
        threads.append(threading.Thread(target=write_input, daemon=True))
    for thread in threads:
        thread.start()
    end = time.monotonic() + timeout
    reason = None
    try:
        while process.poll() is None:
            if overflow.is_set():
                reason = "OUTPUT_TOO_LARGE"
            elif read_failure.is_set():
                reason = "DOCKER_UNAVAILABLE"
            elif cancel_event is not None and cancel_event.is_set():
                reason = "CANCEL_REQUESTED"
            elif time.monotonic() >= end:
                reason = "TIMEOUT"
            if reason:
                try:
                    process.kill()
                except OSError:
                    if process.poll() is None:
                        reason = "DOCKER_UNAVAILABLE"
                break
            time.sleep(0.01)
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            reason = "DOCKER_UNAVAILABLE"
    except BaseException:
        try:
            process.kill()
            process.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            pass
        raise
    finally:
        for thread in threads:
            thread.join(timeout=2)
    if overflow.is_set():
        reason = "OUTPUT_TOO_LARGE"
    elif any(thread.is_alive() for thread in threads) or read_failure.is_set():
        reason = reason or "DOCKER_UNAVAILABLE"
    if reason:
        return Capture(process.returncode, b"", b"", reason)
    return Capture(process.returncode, bytes(buffers[0]), bytes(buffers[1]), None)


def validate_probe(raw: bytes, binding: dict) -> dict:
    if len(raw) > STREAM_LIMIT:
        raise ContractError("OUTPUT_TOO_LARGE")
    value = decode_document(raw)
    require_object(value, {"schema_version", "kind", "binding", "checks"})
    if type(value["schema_version"]) is not int or value["schema_version"] != 1 or value["kind"] != "gah_isolation_probe":
        raise ContractError("OUTPUT_REJECTED")
    if validate_binding(value["binding"]) != binding:
        raise ContractError("BINDING_MISMATCH")
    require_object(value["checks"], PROBE_KEYS)
    if any(type(item) is not bool for item in value["checks"].values()):
        raise ContractError("OUTPUT_REJECTED")
    return value


class DockerRunner:
    """image lockとjournalはtrustedな監督の配置。候補payloadから構成しない。"""
    def __init__(self, image_lock: str | Path, journal_path: str | Path,
                 *, clock: Callable[[], float] = time.time):
        with Path(image_lock).open("rb") as stream:
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise RunnerError("IMAGE_MISMATCH")
        self.lock = decode_document(raw)
        require_object(self.lock, {"schema_version", "image_id", "base_ref", "worker_digest",
                                  "docker_binary_digest", "entrypoint", "environment", "platform"})
        if (type(self.lock["schema_version"]) is not int or self.lock["schema_version"] != 1
                or type(self.lock["image_id"]) is not str or not _IMAGE.fullmatch(self.lock["image_id"])
                or self.lock["entrypoint"] != ENTRYPOINT or self.lock["platform"] != "linux/amd64"
                or type(self.lock["base_ref"]) is not str or not re.fullmatch(r"python@sha256:[0-9a-f]{64}", self.lock["base_ref"])):
            raise RunnerError("IMAGE_MISMATCH")
        require_digest(self.lock["worker_digest"])
        require_digest(self.lock["docker_binary_digest"])
        if (type(self.lock["environment"]) is not list or len(self.lock["environment"]) > 32
                or any(type(item) is not str or len(item) > 2048 for item in self.lock["environment"])):
            raise RunnerError("IMAGE_MISMATCH")
        self.docker = shutil.which("docker")
        if self.docker is None or _digest_file(self.docker) != self.lock["docker_binary_digest"]:
            raise RunnerError("DOCKER_UNAVAILABLE")
        self.endpoint = "npipe:////./pipe/dockerDesktopLinuxEngine" if os.name == "nt" else "unix:///var/run/docker.sock"
        self.environment = {key: os.environ[key] for key in ("PATH", "SystemRoot", "WINDIR", "USERPROFILE", "HOME", "TEMP", "TMP") if key in os.environ}
        self.cwd = Path(journal_path).resolve().parent
        self.journal_path = Path(journal_path)
        self.clock = clock
        self.adapter_digest = _digest_file(Path(__file__).with_name("normalized.py"))
        self.isolation_digest = hashlib.sha256(canonical_bytes(PROFILE)).hexdigest()

    def target_digest(self, scenario: str) -> str:
        return hashlib.sha256(canonical_bytes({"worker_digest": self.lock["worker_digest"], "scenario": scenario})).hexdigest()

    def _capture(self, args: list[str], *, timeout: float = 5, input_bytes: bytes | None = None,
                 cancel_event: threading.Event | None = None, limit: int = CONTROL_LIMIT) -> Capture:
        return capture_bounded([self.docker, "--host", self.endpoint, *args], timeout=timeout, cwd=self.cwd,
            environment=self.environment, input_bytes=input_bytes, cancel_event=cancel_event, limit=limit)

    def _json_command(self, args: list[str], *, timeout: float = 5, cancel_event: threading.Event | None = None) -> object:
        result = self._capture(args, timeout=timeout, cancel_event=cancel_event)
        if result.reason or result.returncode != 0:
            raise RunnerError(result.reason or "DOCKER_UNAVAILABLE")
        try:
            return json.loads(result.stdout)
        except (ValueError, UnicodeError):
            raise RunnerError("DOCKER_UNAVAILABLE") from None

    def _verify_image(self, timeout: float, cancel_event: threading.Event | None = None) -> None:
        value = self._json_command(["image", "inspect", self.lock["image_id"]], timeout=timeout, cancel_event=cancel_event)
        try:
            image = value[0]
            config = image["Config"]
            valid = (len(value) == 1 and image["Id"] == self.lock["image_id"] and image["Os"] == "linux"
                     and image["Architecture"] == "amd64" and config["Entrypoint"] == ENTRYPOINT
                     and not config.get("Cmd") and config["Env"] == self.lock["environment"]
                     and config["User"] == "65532:65532" and config["WorkingDir"] == "/work"
                     and not config.get("Volumes") and not config.get("ExposedPorts") and not config.get("Healthcheck")
                     and config["Labels"]["org.gah.fixture.worker-sha256"] == self.lock["worker_digest"])
        except (KeyError, TypeError, IndexError):
            valid = False
        if not valid:
            raise RunnerError("IMAGE_MISMATCH")

    def _create_args(self, record: dict) -> list[str]:
        args = ["container", "create", "--name", record["container_name"], "--label", "org.gah.execution=" + record["request_digest"],
                "--platform", "linux/amd64", "--network", "none", "--read-only", "--user", "65532:65532",
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges=true", "--memory", str(MEMORY),
                "--memory-swap", str(MEMORY), "--cpus", "0.5", "--pids-limit", "32", "--ipc", "private",
                "--cgroupns", "private", "--workdir", "/work", "--log-driver", "none", "--restart", "no",
                "--stop-timeout", "1", "--no-healthcheck", "--interactive"]
        for destination, options in TMPFS.items():
            args.extend(["--tmpfs", destination + ":" + options])
        return args + [self.lock["image_id"], "--scenario", record["scenario"]]

    def _inspect(self, record: dict) -> dict | None:
        result = self._capture(["container", "inspect", record["container_name"]])
        if result.reason or result.returncode != 0:
            # 接続拒否/timeoutを「不存在」へ変換しない。
            listing = self._capture(["container", "ls", "--all", "--no-trunc", "--filter",
                "name=^/" + record["container_name"] + "$", "--format", "{{.ID}}"])
            if not listing.reason and listing.returncode == 0 and not listing.stdout.strip():
                return None
            raise RunnerError("DOCKER_UNAVAILABLE")
        try:
            data = json.loads(result.stdout)
            container = data[0]
            if (len(data) != 1 or not _CONTAINER.fullmatch(container["Id"])
                    or container["Name"] != "/" + record["container_name"]
                    or container["Image"] != record["image_id"]
                    or container["Config"]["Labels"].get("org.gah.execution") != record["request_digest"]
                    or record["container_id"] is not None and container["Id"] != record["container_id"]):
                raise ValueError()
            return container
        except (ValueError, KeyError, TypeError, IndexError):
            raise RunnerError("CONFIG_MISMATCH") from None

    def _verify_config(self, container: dict, scenario: str) -> None:
        self._verify_container_config(container, ENTRYPOINT, ["--scenario", scenario])

    def _verify_container_config(self, container: dict, entrypoint: list, command: list) -> None:
        try:
            host, config = container["HostConfig"], container["Config"]
            valid = (host["NetworkMode"] == "none" and host["ReadonlyRootfs"] is True and host["Privileged"] is False
                and host["CapDrop"] == ["ALL"] and not host.get("CapAdd") and host["SecurityOpt"] == ["no-new-privileges=true"]
                and host["Memory"] == MEMORY and host["MemorySwap"] == MEMORY and host["NanoCpus"] == 500000000
                and host["PidsLimit"] == 32 and host["IpcMode"] == "private" and host["CgroupnsMode"] == "private"
                and not host["PidMode"] and not host.get("Binds") and not host.get("Devices") and not host.get("DeviceRequests")
                and not host.get("VolumesFrom") and not host.get("PortBindings") and not host.get("ExtraHosts")
                and host["Tmpfs"] == TMPFS and host["LogConfig"]["Type"] == "none" and host["RestartPolicy"]["Name"] == "no"
                and config["User"] == "65532:65532" and config["WorkingDir"] == "/work" and config["Entrypoint"] == entrypoint
                and (config.get("Cmd") or []) == command and config["Env"] == self.lock["environment"]
                and not config.get("Volumes") and not config.get("ExposedPorts") and config["OpenStdin"] is True
                and config["Tty"] is False and all(mount["Type"] == "tmpfs" and mount["Destination"] in TMPFS for mount in container["Mounts"]))
        except (KeyError, TypeError):
            valid = False
        if not valid:
            raise RunnerError("CONFIG_MISMATCH")

    @staticmethod
    def _stopped(container: dict) -> bool:
        state = container.get("State", {})
        return state.get("Running") is False and state.get("Pid") == 0 and state.get("Status") in {"created", "exited", "dead"}

    def _cleanup(self, journal: ExecutionJournal, record: dict) -> tuple[bool, bool, int | None, dict]:
        try:
            container = self._inspect(record)
            if container is None:
                confirmed = record["container_id"] is None or record["state"] == "STOPPED"
                return confirmed, confirmed, None, record
            if record["container_id"] is None:
                record = journal.advance(record["binding"]["run_id"], record["binding"]["operation_id"], record["owner_token"],
                                         "CREATED", container_id=container["Id"])
            if not self._stopped(container):
                self._capture(["container", "kill", record["container_name"]])
                container = self._inspect(record)
            if container is None:
                return False, False, None, record
            if not self._stopped(container):
                return False, False, None, record
            exit_code = container["State"].get("ExitCode")
            record = journal.advance(record["binding"]["run_id"], record["binding"]["operation_id"], record["owner_token"],
                                     "STOPPED", container_id=record["container_id"])
            self._capture(["container", "rm", record["container_name"]])
            removed = self._inspect(record) is None
            return True, removed, exit_code, record
        except (RunnerError, JournalError, ContractError):
            return False, False, None, record

    def _receipt(self, record: dict, *, status: str, reason: str | None, stopped: bool,
                 cleaned: bool, exit_code: int | None, elapsed: int, verified: bool,
                 normalized: dict | None = None, probe: dict | None = None, recovered: bool = False) -> dict:
        return {"schema_version": 1, "kind": "fixture_execution", "binding": record["binding"],
                "scenario": record["scenario"], "image_id": record["image_id"], "container_id": record["container_id"],
                "execution_status": status, "reason": reason, "exit_code": exit_code, "stop_confirmed": stopped,
                "cleanup_confirmed": cleaned, "elapsed_millis": elapsed, "isolation_config_verified": verified,
                "output_disposition": "ADMITTED" if normalized is not None or probe is not None else "REJECTED" if reason in {"OUTPUT_REJECTED", "OUTPUT_TOO_LARGE"} else "NOT_COLLECTED",
                "normalized_result": normalized, "probe_result": probe, "recovered": recovered, "ci_eligible": False}

    def run(self, scenario: str, binding: dict, *, run_deadline: int, timeout_seconds: int = 120,
            cancel_event: threading.Event | None = None) -> dict:
        binding = validate_binding(binding)
        require_uint(run_deadline)
        if (binding["fixture_digest"] != self.lock["worker_digest"] or binding["evaluator_digest"] != self.lock["worker_digest"]
                or binding["adapter_digest"] != self.adapter_digest or binding["isolation_digest"] != self.isolation_digest
                or binding["target_digest"] != self.target_digest(scenario)):
            raise RunnerError("IMAGE_MISMATCH")
        return self._run_fixed(scenario, binding, canonical_bytes(binding), None, run_deadline=run_deadline,
            timeout_seconds=timeout_seconds, cancel_event=cancel_event)

    def _run_fixed(self, scenario, binding, input_bytes, normalize_output, *, run_deadline, timeout_seconds=120, cancel_event=None):
        with operation_lock(self.journal_path, binding["run_id"], binding["operation_id"]), ExecutionJournal(self.journal_path) as journal:
            record = journal.begin(binding, scenario, self.lock["image_id"], run_deadline=run_deadline, timeout_seconds=timeout_seconds)
            if not record["new"]:
                if record.get("receipt") is not None:
                    return record["receipt"]
                raise RunnerError("DISPATCH_UNRESOLVED")
            start = time.monotonic()
            verified, normalized, probe = False, None, None
            reason, status = None, "FAILED"
            def remaining():
                if cancel_event is not None and cancel_event.is_set():
                    raise RunnerError("CANCEL_REQUESTED")
                try:
                    now = self.clock()
                    if type(now) not in {int, float} or now < 0 or not now < float("inf"):
                        raise ValueError()
                    left = min(run_deadline - now, timeout_seconds - (time.monotonic() - start))
                except Exception:
                    raise RunnerError("CLOCK_FAILURE") from None
                if left <= 0:
                    raise RunnerError("TIMEOUT")
                return left
            interrupted = None
            try:
                if cancel_event is not None and cancel_event.is_set():
                    raise RunnerError("CANCEL_REQUESTED")
                self._verify_image(min(5, remaining()), cancel_event=cancel_event)
                created = self._capture(self._create_args(record), timeout=min(10, remaining()), cancel_event=cancel_event)
                if created.reason or created.returncode != 0:
                    raise RunnerError(created.reason or "DOCKER_UNAVAILABLE")
                try:
                    container_id = created.stdout.decode("ascii").strip()
                except UnicodeError:
                    raise RunnerError("CONFIG_MISMATCH") from None
                if not _CONTAINER.fullmatch(container_id):
                    raise RunnerError("CONFIG_MISMATCH")
                record = journal.advance(binding["run_id"], binding["operation_id"], record["owner_token"], "CREATED", container_id=container_id)
                container = self._inspect(record)
                if container is None:
                    raise RunnerError("CONFIG_MISMATCH")
                self._verify_config(container, scenario)
                verified = True
                record = journal.advance(binding["run_id"], binding["operation_id"], record["owner_token"], "STARTING", container_id=container_id)
                captured = self._capture(["container", "start", "--attach", "--interactive", record["container_name"]],
                    timeout=remaining(), input_bytes=input_bytes, cancel_event=cancel_event, limit=STREAM_LIMIT)
                container = self._inspect(record)
                if container is not None and container["State"].get("StartedAt", "0001").startswith("0001") is False:
                    record = journal.advance(binding["run_id"], binding["operation_id"], record["owner_token"], "RUNNING", container_id=container_id)
                if captured.reason:
                    raise RunnerError(captured.reason)
                if (container is None or record["state"] != "RUNNING" or captured.returncode != 0
                        or not self._stopped(container) or container["State"].get("ExitCode") != 0):
                    raise RunnerError("EXECUTION_FAILURE")
                remaining()
                if captured.stderr:
                    raise RunnerError("OUTPUT_REJECTED")
                try:
                    if normalize_output is not None:
                        normalized = normalize_output(captured.stdout)
                    elif scenario == "probe:isolation":
                        probe = validate_probe(captured.stdout, binding)
                        if not all(probe["checks"].values()):
                            raise RunnerError("CONFIG_MISMATCH")
                    elif scenario.startswith("probe:"):
                        raise RunnerError("OUTPUT_REJECTED")
                    else:
                        normalized = normalize_generic(captured.stdout, binding, execution_status="COMPLETED", exit_code=0, stop_confirmed=True)
                        if normalized["mode"] != scenario.split(":", 1)[0]:
                            raise RunnerError("OUTPUT_REJECTED")
                except ContractError:
                    raise RunnerError("OUTPUT_REJECTED") from None
                status = "COMPLETED"
            except RunnerError as error:
                reason = error.code
                status = "TIMEOUT" if reason == "TIMEOUT" else "CANCELLED" if reason == "CANCEL_REQUESTED" else "FAILED"
                normalized, probe = None, None
            except BaseException as error:
                reason, status, interrupted = "INTERRUPTED", "FAILED", error
            stopped, cleaned, exit_code, record = self._cleanup(journal, record)
            if not stopped:
                status, reason, normalized, probe = "FAILED", "STOP_UNCONFIRMED", None, None
            elif not cleaned:
                status, reason, normalized, probe = "FAILED", "CLEANUP_FAILED", None, None
            elif status == "COMPLETED":
                try:
                    remaining()
                    if cancel_event is not None and cancel_event.is_set():
                        raise RunnerError("CANCEL_REQUESTED")
                except RunnerError as error:
                    reason = error.code
                    status = "CANCELLED" if reason == "CANCEL_REQUESTED" else "TIMEOUT" if reason == "TIMEOUT" else "FAILED"
                    normalized, probe = None, None
            receipt = self._receipt(record, status=status, reason=reason, stopped=stopped, cleaned=cleaned,
                exit_code=exit_code, elapsed=int((time.monotonic() - start) * 1000), verified=verified, normalized=normalized, probe=probe)
            if stopped and cleaned:
                receipt = journal.finish(binding["run_id"], binding["operation_id"], record["owner_token"], receipt)["receipt"]
            if interrupted is not None:
                raise interrupted
            return receipt

    def recover(self, run_id: str, operation_id: str) -> dict:
        """再送は行わず、記録した同containerだけを停止/回収する。"""
        with operation_lock(self.journal_path, run_id, operation_id), ExecutionJournal(self.journal_path) as journal:
            record = journal.get(run_id, operation_id)
            if record.get("receipt") is not None:
                return record["receipt"]
            started = time.monotonic()
            stopped, cleaned, exit_code, record = self._cleanup(journal, record)
            reason = "INTERRUPTED" if stopped and cleaned else "STOP_UNCONFIRMED" if not stopped else "CLEANUP_FAILED"
            receipt = self._receipt(record, status="FAILED", reason=reason, stopped=stopped, cleaned=cleaned,
                exit_code=exit_code, elapsed=int((time.monotonic() - started) * 1000), verified=False, recovered=True)
            if stopped and cleaned:
                return journal.finish(run_id, operation_id, record["owner_token"], receipt)["receipt"]
            return receipt
