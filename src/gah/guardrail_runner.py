"""無害な固定guardrailの1ケースを、独立containerと送信journalで実行する。"""
from pathlib import Path
import hashlib
import os
import shutil
import time
from . import guardrail_runtime, guardrail_results
from .contracts import require_uint
from .docker_runner import DockerRunner, RunnerError, PROFILE, _digest_file
from .wire import canonical_bytes


class GuardrailRunner(DockerRunner):
    execution_kind = "guardrail"
    def __init__(self, journal_path, *, clock=time.time):
        self.lock = guardrail_runtime.read_lock()
        self.docker = shutil.which("docker")
        if self.docker is None or _digest_file(self.docker) != self.lock["docker_binary_digest"]:
            raise RunnerError("DOCKER_UNAVAILABLE")
        self.endpoint = "npipe:////./pipe/dockerDesktopLinuxEngine" if os.name == "nt" else "unix:///var/run/docker.sock"
        self.environment = {key:os.environ[key] for key in ("PATH", "SystemRoot", "WINDIR", "USERPROFILE", "HOME", "TEMP", "TMP") if key in os.environ}
        self.cwd = Path(journal_path).resolve().parent
        self.journal_path = Path(journal_path)
        self.clock = clock
        self.adapter_digest = _digest_file(Path(__file__).with_name("normalized.py"))
        self.isolation_digest = hashlib.sha256(canonical_bytes(PROFILE)).hexdigest()

    def _verify_image(self, timeout, cancel_event=None):
        value = self._json_command(["image", "inspect", self.lock["image_id"]], timeout=timeout, cancel_event=cancel_event)
        try:
            image = value[0]; config = image["Config"]
            valid = (len(value) == 1 and image["Id"] == self.lock["image_id"]
                and image["Os"] == "linux" and image["Architecture"] == "amd64"
                and config["Entrypoint"] == guardrail_runtime.ENTRYPOINT and not config.get("Cmd")
                and config["Env"] == self.lock["environment"] and config["User"] == "65532:65532"
                and config["WorkingDir"] == "/work" and not config.get("Volumes")
                and not config.get("ExposedPorts") and not config.get("Healthcheck")
                and config["Labels"]["org.gah.guardrail.sources"] == self.lock["sources_digest"])
        except (KeyError, TypeError, IndexError):
            valid = False
        if not valid or guardrail_runtime.read_lock() != self.lock:
            raise RunnerError("IMAGE_MISMATCH")

    def _create_args(self, record):
        args = super()._create_args(record)
        if args[-2:] != ["--scenario", record["scenario"]]:
            raise RunnerError("CONFIG_MISMATCH")
        return args[:-2]

    def _verify_config(self, container, scenario):
        self._verify_container_config(container, guardrail_runtime.ENTRYPOINT, [])

    def _receipt(self, record, **fields):
        value = super()._receipt(record, **fields)
        value["kind"] = "guardrail_execution"
        value["case_result"] = value["normalized_result"]
        value["normalized_result"] = None
        return value

    def run(self, request, *, run_deadline, timeout_seconds=120, cancel_event=None):
        request = guardrail_results.validate_request(request)
        require_uint(run_deadline)
        raw = canonical_bytes(request)
        scenario = "guardrail:" + hashlib.sha256(raw).hexdigest()
        binding = request["stages"][0]["binding"]
        return self._run_fixed(scenario, binding, raw,
            lambda output:guardrail_results.from_worker(output, request), run_deadline=run_deadline,
            timeout_seconds=timeout_seconds, cancel_event=cancel_event)
