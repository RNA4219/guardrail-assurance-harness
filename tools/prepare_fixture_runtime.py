"""自作固定fixtureのみを公式Pythonからビルドし、実体のlockを作る。"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
BASE = "python@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea"
ENTRYPOINT = ["/usr/local/bin/python", "-I", "-B", "/opt/gah/fixture_worker.py"]


def digest_file(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(65536), b""):
            result.update(chunk)
    return result.hexdigest()


def main():
    docker = shutil.which("docker")
    if docker is None:
        raise SystemExit("DOCKER_UNAVAILABLE")
    endpoint = "npipe:////./pipe/dockerDesktopLinuxEngine" if os.name == "nt" else "unix:///var/run/docker.sock"
    prefix = [docker, "--host", endpoint]
    worker = ROOT / "fixtures/runtime/fixture_worker.py"
    worker_digest = digest_file(worker)
    tag = "gah-fixture:" + worker_digest[:16]
    parent = ROOT / ".ga/fixture-images"
    parent.mkdir(parents=True, exist_ok=True)
    context = parent / worker_digest
    context.mkdir(exist_ok=True)
    if context.is_symlink() or any(item.name not in {"Dockerfile", "fixture_worker.py"}
                                  or not item.is_file() or item.is_symlink() for item in context.iterdir()):
        raise SystemExit("CONTEXT_CONFLICT")
    snapshot = context / "fixture_worker.py"
    body = worker.read_bytes()
    if snapshot.exists() and snapshot.read_bytes() != body:
        raise SystemExit("CONTEXT_CONFLICT")
    snapshot.write_bytes(body)
    dockerfile = ("FROM " + BASE + "\n"
        "COPY fixture_worker.py /opt/gah/fixture_worker.py\n"
        "LABEL org.gah.fixture.worker-sha256=" + worker_digest + "\n"
        "ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONHASHSEED=0\n"
        "USER 65532:65532\nWORKDIR /work\n"
        "ENTRYPOINT " + json.dumps(ENTRYPOINT) + "\nCMD []\n")
    (context / "Dockerfile").write_text(dockerfile, encoding="utf-8")
    # 入力は固定した自作2ファイルのみ。モデル/評価payloadはcontextへ入れない。
    result = subprocess.run(prefix + ["build", "--network=none", "--pull=false", "--tag", tag, str(context)],
                            cwd=ROOT, timeout=180)
    if result.returncode:
        raise SystemExit("BUILD_FAILED")
    raw = subprocess.run(prefix + ["image", "inspect", tag], cwd=ROOT, capture_output=True, timeout=20)
    if raw.returncode or len(raw.stdout) > 131072:
        raise SystemExit("IMAGE_INSPECTION_FAILED")
    image = json.loads(raw.stdout)[0]
    if digest_file(worker) != worker_digest:
        raise SystemExit("SOURCE_CHANGED")
    lock = {"schema_version": 1, "image_id": image["Id"], "base_ref": BASE,
            "worker_digest": worker_digest, "docker_binary_digest": digest_file(docker),
            "entrypoint": ENTRYPOINT, "environment": image["Config"]["Env"],
            "platform": "linux/amd64"}
    destination = ROOT / "config/fixture-runtime.lock.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=destination.parent, delete=False) as handle:
        json.dump(lock, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    os.replace(temporary, destination)
    print(json.dumps({"status": "prepared", "image_id": image["Id"], "worker_digest": worker_digest}))


if __name__ == "__main__":
    main()
