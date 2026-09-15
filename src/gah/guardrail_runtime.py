"""固定合成guardrailの配置lockとローカル実装の一致を検査する。"""
from copy import deepcopy
import hashlib
from pathlib import Path
from .contracts import ContractError, decode_document, require_digest, require_object
from .wire import canonical_bytes

ROOT = Path(__file__).resolve().parents[2]
SOURCES = ("fixtures/llm/guardrail_worker.py", "fixtures/llm/guardrail_target.py",
    "src/gah/__init__.py", "src/gah/contracts.py", "src/gah/normalized.py",
    "src/gah/run_contracts.py", "src/gah/wire.py", "src/gah/policy.py",
    "src/gah/registry.py", "src/gah/corpus.py")
ENTRYPOINT = ["/usr/local/bin/python", "-I", "-B", "/opt/gah/guardrail_worker.py"]


def read_lock():
    try:
        value = decode_document((ROOT / "config/guardrail-runtime.lock.json").read_bytes())
        require_object(value, {"schema_version", "kind", "image_id", "base_ref", "worker_digest",
            "target_source_digest", "sources_digest", "source_sha256", "docker_binary_digest",
            "entrypoint", "environment", "platform"})
        if (type(value["schema_version"]) is not int or value["schema_version"] != 1
                or value["kind"] != "guardrail_runtime_lock" or value["entrypoint"] != ENTRYPOINT
                or value["platform"] != "linux/amd64" or type(value["image_id"]) is not str
                or not value["image_id"].startswith("sha256:")
                or type(value["base_ref"]) is not str or not value["base_ref"].startswith("python@sha256:")):
            raise ContractError()
        require_digest(value["image_id"][7:]); require_digest(value["base_ref"][14:])
        require_digest(value["docker_binary_digest"])
        require_object(value["source_sha256"], set(SOURCES))
        hashes = {}
        for name in SOURCES:
            path = ROOT / name
            if path.is_symlink():
                raise ContractError()
            hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        if (value["source_sha256"] != hashes
                or value["sources_digest"] != hashlib.sha256(canonical_bytes(hashes)).hexdigest()
                or value["worker_digest"] != hashes[SOURCES[0]]
                or value["target_source_digest"] != hashes[SOURCES[1]]):
            raise ContractError()
        if (type(value["environment"]) is not list or len(value["environment"]) > 32
                or any(type(item) is not str or len(item) > 2048 for item in value["environment"])):
            raise ContractError()
        return deepcopy(value)
    except (OSError, KeyError, TypeError, ValueError):
        raise ContractError("GUARDRAIL_RUNTIME_MISMATCH") from None
