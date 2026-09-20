"""固定imageを新規配布先で取得・構築する。既存checkoutのlockは変更しない。"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gah.docker_runner import capture_bounded
from gah.supervisor_checkpoint import _plain_directory
from tools.prepare_fixture_runtime import BASE

DIRECTORIES = ("src", "tools", "config", "fixtures", "datasets", "schemas", "examples", "docs", "third_party")
BUILDERS = ("fixture", "guardrail", "authority")
MAX_FILES = 5000
MAX_BYTES = 64 * 1024 * 1024
MAX_FILE = 8 * 1024 * 1024

class PreparationError(ValueError):
    pass

def _sha(data):
    return hashlib.sha256(data).hexdigest()

def _read(path):
    _plain_directory(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_FILE:
        raise PreparationError("SOURCE_PATH_INVALID")
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino, opened.st_size) != (info.st_dev, info.st_ino, info.st_size):
            raise PreparationError("SOURCE_CHANGED")
        body = stream.read(MAX_FILE + 1)
    _plain_directory(path)
    if len(body) > MAX_FILE:
        raise PreparationError("SOURCE_TOO_LARGE")
    return body

def _inventory(root):
    files = {}
    total = 0
    def add(path):
        nonlocal total
        raw = _read(path)
        total += len(raw)
        if total > MAX_BYTES or len(files) >= MAX_FILES:
            raise PreparationError("SOURCE_TOO_LARGE")
        files[path.relative_to(root).as_posix()] = _sha(raw)
    for name in DIRECTORIES:
        base = root / name
        _plain_directory(base)
        if not base.is_dir():
            raise PreparationError("SOURCE_INCOMPLETE")
        for folder, dirs, names in os.walk(base, followlinks=False):
            dirs[:] = sorted(d for d in dirs if d not in {".git", ".ga", "__pycache__"})
            for d in dirs:
                _plain_directory(Path(folder) / d)
            for name in sorted(names):
                if name.endswith((".pyc", ".pyo")):
                    continue
                add(Path(folder) / name)
    for name in ("README.md", "LICENSE"):
        add(root / name)
    return files

def _invoke(argv, cwd, timeout, environment):
    result = capture_bounded(argv, timeout=timeout, cwd=cwd,
                             environment=environment, limit=1024 * 1024)
    if result.reason or result.returncode != 0:
        raise PreparationError("IMAGE_PREPARATION_FAILED")
    return result.stdout

def _verify_image(kind, lock, image, docker_digest):
    uid = "12000:12000" if kind == "authority" else "65532:65532"
    cfg = image.get("Config", {})
    entry = ["/usr/local/bin/python", "-I", "-B", {
        "fixture": "/opt/gah/fixture_worker.py",
        "guardrail": "/opt/gah/guardrail_worker.py",
        "authority": "/opt/gah/tools/authority_entry.py"}[kind]]
    if (type(lock.get("schema_version")) is not int or lock["schema_version"] != 1
            or re.fullmatch(r"sha256:[0-9a-f]{64}", str(lock.get("image_id"))) is None
            or image.get("Id") != lock["image_id"] or lock.get("base_ref") != BASE
            or lock.get("docker_binary_digest") != docker_digest
            or image.get("Os") != "linux" or image.get("Architecture") != "amd64"
            or cfg.get("User") != uid or cfg.get("WorkingDir") != "/work"
            or cfg.get("Entrypoint") != entry or lock.get("entrypoint") != entry
            or cfg.get("Env") != lock.get("environment") or cfg.get("Cmd")
            or cfg.get("Volumes") or cfg.get("ExposedPorts")):
        raise PreparationError("IMAGE_CONFIG_MISMATCH")
    label = {"fixture": "org.gah.fixture.worker-sha256", "guardrail": "org.gah.guardrail.sources",
             "authority": "org.gah.authority.sources"}[kind]
    expected = lock.get("worker_digest") if kind == "fixture" else lock.get("sources_digest")
    if not expected or cfg.get("Labels", {}).get(label) != expected:
        raise PreparationError("IMAGE_SOURCE_MISMATCH")

def _write_new(path, value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n"
    with path.open("xb") as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())

def prepare(destination, *, offline=False, root=ROOT, invoke=_invoke):
    if type(offline) is not bool:
        raise PreparationError("INVALID_INPUT")
    root = _plain_directory(root).resolve()
    destination = _plain_directory(destination)
    if destination.exists():
        raise PreparationError("DESTINATION_EXISTS")
    if not destination.parent.is_dir():
        raise PreparationError("DESTINATION_PARENT_MISSING")
    # 親repo配下では出力専用.gaだけを許可し、sourceへ自己混入させない。
    if destination.is_relative_to(root) and not destination.is_relative_to(root / ".ga"):
        raise PreparationError("DESTINATION_PATH_INVALID")
    docker = shutil.which("docker")
    if docker is None:
        raise PreparationError("DOCKER_UNAVAILABLE")
    docker_digest = _sha(Path(docker).read_bytes())
    prefix = [docker, "--host", "npipe:////./pipe/dockerDesktopLinuxEngine" if os.name == "nt" else "unix:///var/run/docker.sock"]
    env = {k: os.environ[k] for k in ("PATH", "SystemRoot", "WINDIR", "USERPROFILE", "HOME", "TEMP", "TMP") if k in os.environ}
    # Check the fixed daemon endpoint before a failed inspect can be classified
    # as an unavailable cached base image.
    try:
        server_raw = invoke(prefix + ["version", "--format", "{{json .Server}}"], root, 10, env)
        server = json.loads(server_raw)
        if (not isinstance(server, dict) or not isinstance(server.get("Version"), str)
                or not server["Version"] or not isinstance(server.get("ApiVersion"), str)
                or not server["ApiVersion"]):
            raise ValueError("invalid Docker server response")
    except Exception:
        raise PreparationError("ENGINE_UNAVAILABLE") from None
    files = _inventory(root)
    destination.mkdir()
    stage = destination / ".ga" / "image-preparation"
    stage.mkdir(parents=True)
    begin = time.monotonic_ns()
    receipt = {"schema_version": 1, "kind": "image_preparation_receipt", "status": "INCOMPLETE",
               "ci_eligible": False, "offline": offline, "base_ref": BASE,
               "source_files": files, "docker_binary_digest": docker_digest}
    try:
        for relative, expected in files.items():
            body = _read(root / relative)
            if _sha(body) != expected:
                raise PreparationError("SOURCE_CHANGED")
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            _plain_directory(target)
            with target.open("xb") as stream:
                stream.write(body)
        if _inventory(root) != files:
            raise PreparationError("SOURCE_CHANGED")
        cache_hit = True
        try:
            invoke(prefix + ["image", "inspect", BASE], destination, 20, env)
        except PreparationError:
            cache_hit = False
            if offline:
                raise PreparationError("BASE_IMAGE_UNAVAILABLE") from None
            invoke(prefix + ["pull", "--platform=linux/amd64", BASE], destination, 600, env)
        raw = invoke(prefix + ["image", "inspect", BASE], destination, 20, env)
        base = json.loads(raw)[0]
        if (base.get("Os") != "linux" or base.get("Architecture") != "amd64"
                or not set(base.get("RepoDigests", [])) & {BASE, "docker.io/library/" + BASE}):
            raise PreparationError("BASE_IMAGE_MISMATCH")
        receipt["base_cache_hit"] = cache_hit
        images = {}
        for kind in BUILDERS:
            invoke([sys.executable, "-E", "-B", "-X", "utf8", "-m", "tools.prepare_" + kind + "_runtime"], destination, 240, env)
            lock = json.loads(_read(destination / "config" / (kind + "-runtime.lock.json")))
            raw = invoke(prefix + ["image", "inspect", lock["image_id"]], destination, 20, env)
            _verify_image(kind, lock, json.loads(raw)[0], docker_digest)
            for relative, digest in lock.get("source_sha256", {}).items():
                if relative not in files or _sha(_read(destination / relative)) != digest:
                    raise PreparationError("IMAGE_SOURCE_MISMATCH")
            if kind == "fixture":
                if lock.get("worker_digest") != _sha(_read(destination / "fixtures/runtime/fixture_worker.py")):
                    raise PreparationError("IMAGE_SOURCE_MISMATCH")
            else:
                from tools import prepare_guardrail_runtime, prepare_authority_runtime
                required = set((prepare_guardrail_runtime if kind == "guardrail" else prepare_authority_runtime).SOURCES)
                hashes = lock.get("source_sha256", {})
                calculated = _sha(json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode())
                if set(hashes) != required or calculated != lock.get("sources_digest"):
                    raise PreparationError("IMAGE_SOURCE_MISMATCH")
            images[kind] = {"image_id": lock["image_id"], "lock_sha256": _sha(_read(destination / "config" / (kind + "-runtime.lock.json")))}
        if _inventory(root) != files or _sha(Path(docker).read_bytes()) != docker_digest:
            raise PreparationError("SOURCE_CHANGED")
        lock_paths = {"config/" + k + "-runtime.lock.json" for k in BUILDERS}
        for relative, digest in files.items():
            if relative not in lock_paths and _sha(_read(destination / relative)) != digest:
                raise PreparationError("COPY_CHANGED")
        receipt.update(status="PREPARED", images=images, elapsed_ns=time.monotonic_ns() - begin)
        _write_new(stage / "receipt.json", receipt)
        return receipt
    except BaseException as error:
        receipt.update(reason=error.args[0] if isinstance(error, PreparationError) else "IMAGE_PREPARATION_FAILED",
                       elapsed_ns=time.monotonic_ns() - begin)
        _write_new(stage / "incomplete.json", receipt)
        raise

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare"])
    parser.add_argument("--destination", required=True)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = prepare(args.destination, offline=args.offline)
        print(json.dumps({"schema_version": 1, "kind": "image_preparation_result", "status": result["status"],
                          "images": result["images"], "base_cache_hit": result["base_cache_hit"],
                          "elapsed_ns": result["elapsed_ns"], "ci_eligible": False}))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as error:
        reason = error.args[0] if isinstance(error, PreparationError) else "IMAGE_PREPARATION_FAILED"
        print(json.dumps({"schema_version": 1, "kind": "image_preparation_result", "status": "INCOMPLETE", "reason": reason, "ci_eligible": False}))
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
