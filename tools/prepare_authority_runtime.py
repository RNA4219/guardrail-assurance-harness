"""自作の方針・採択・認証部だけを固定imageへ配置する。"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
BASE = "python@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea"
SOURCES = ["src/gah/__init__.py", "src/gah/contracts.py", "src/gah/wire.py", "src/gah/policy.py",
           "src/gah/adoption.py", "src/gah/authority.py", "src/gah/registry.py", "src/gah/corpus.py",
           "src/gah/run_contracts.py", "src/gah/resources.py", "src/gah/resource_authority.py", "src/gah/ledger.py", "src/gah/termination.py", "src/gah/mutation_reviews.py", "src/gah/evaluation_authority.py",
           "src/gah/adoption_migrations.py", "src/gah/run_evidence.py", "src/gah/aggregation.py", "src/gah/decision.py",
           "src/gah/normalized.py", "src/gah/docker_runner.py", "src/gah/execution_journal.py",
           "src/gah/assurance_authority.py", "src/gah/baselines.py", "src/gah/contract_updates.py",
           "src/gah/baseline_authority.py", "src/gah/baseline_generations.py",
           "src/gah/resource_operation.py", "src/gah/baseline_refresh_migration.py",
           "src/gah/following_contracts.py", "src/gah/semantic_conditions.py", "src/gah/contract_revision_rules.py", "src/gah/read_checks.py", "src/gah/fixture_admission.py", "src/gah/fixture_calibration.py",
           "src/gah/fixture_materialization.py", "src/gah/transition_materialization.py",
           "src/gah/transition_authority.py", "src/gah/transition_migrations.py", "src/gah/transition_acceptance.py",
           "src/gah/regression_runs.py", "src/gah/run_scope.py", "src/gah/finding_lifecycle.py", "src/gah/execution_profiles.py", "src/gah/run_outputs.py", "src/gah/run_cancellation.py", "src/gah/remediation.py", "fixtures/runtime/fixture_worker.py",
           "src/gah/guardrail_runtime.py", "src/gah/llm_materialization.py", "src/gah/llm_admission.py",
           "src/gah/evaluation_data.py", "src/gah/llm_evaluator.py", "src/gah/measurement_calibration.py",
           "src/gah/guardrail_results.py", "src/gah/guardrail_runner.py", "src/gah/candidate_sections.py", "src/gah/llm_transitions.py", "src/gah/evidence_retention.py", "src/gah/candidate_outputs.py", "src/gah/combined_runs.py", "src/gah/cache_inputs.py", "src/gah/evidence_snapshot_cache.py", "src/gah/immutable_cache.py", "src/gah/target_retirement.py", "src/gah/finding_dispositions.py", "src/gah/llm_migration.py",
           "fixtures/llm/guardrail_target.py", "fixtures/llm/guardrail_worker.py", "config/guardrail-runtime.lock.json",
           "config/bootstrap-policy.v1.json", "config/fixture-runtime.lock.json", "tools/authority_entry.py"]
ENTRYPOINT = ["/usr/local/bin/python", "-I", "-B", "/opt/gah/tools/authority_entry.py"]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    docker = shutil.which("docker")
    if docker is None:
        raise SystemExit("DOCKER_UNAVAILABLE")
    sources = {path: digest(ROOT / path) for path in SOURCES}
    source_digest = hashlib.sha256(json.dumps(sources, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    parent = ROOT / ".ga/authority-images"
    parent.mkdir(parents=True, exist_ok=True)
    context = Path(tempfile.mkdtemp(prefix=source_digest[:16] + "-", dir=parent))
    for path in SOURCES:
        destination = context / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((ROOT / path).read_bytes())
    dockerfile = ("FROM " + BASE + "\nCOPY src /opt/gah/src\nCOPY config /opt/gah/config\nCOPY tools /opt/gah/tools\nCOPY fixtures /opt/gah/fixtures\n"
        "RUN mkdir /ipc /state /work && chown 12000:12000 /ipc /state /work && chmod 0755 /ipc && chmod 0700 /state\n"
        "LABEL org.gah.authority.sources=" + source_digest + "\n"
        "ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1\nUSER 12000:12000\nWORKDIR /work\n"
        "ENTRYPOINT " + json.dumps(ENTRYPOINT) + "\nCMD []\n")
    (context / "Dockerfile").write_text(dockerfile, encoding="utf-8")
    endpoint = "npipe:////./pipe/dockerDesktopLinuxEngine" if os.name == "nt" else "unix:///var/run/docker.sock"
    prefix = [docker, "--host", endpoint]
    tag = "gah-authority:" + source_digest[:16]
    result = subprocess.run(prefix + ["build", "--platform=linux/amd64", "--network=none", "--pull=false", "--tag", tag, str(context)], cwd=ROOT, timeout=180)
    if result.returncode:
        raise SystemExit("BUILD_FAILED")
    result = subprocess.run(prefix + ["image", "inspect", tag], cwd=ROOT, capture_output=True, timeout=20)
    if result.returncode or len(result.stdout) > 131072:
        raise SystemExit("IMAGE_INSPECTION_FAILED")
    image = json.loads(result.stdout)[0]
    if (image["Os"] != "linux" or image["Architecture"] != "amd64"
            or image["Config"]["Entrypoint"] != ENTRYPOINT or image["Config"]["WorkingDir"] != "/work"
            or image["Config"]["User"] != "12000:12000" or image["Config"].get("Cmd")
            or image["Config"].get("Volumes") or image["Config"].get("ExposedPorts")
            or image["Config"]["Labels"].get("org.gah.authority.sources") != source_digest):
        raise SystemExit("IMAGE_CONFIG_MISMATCH")
    if any(digest(ROOT / path) != value for path, value in sources.items()):
        raise SystemExit("SOURCE_CHANGED")
    lock = {"schema_version": 1, "image_id": image["Id"], "base_ref": BASE, "source_sha256": sources,
            "sources_digest": source_digest, "docker_binary_digest": digest(docker),
            "entrypoint": ENTRYPOINT, "environment": image["Config"]["Env"],
            "os": "linux", "architecture": "amd64", "working_dir": "/work"}
    destination = ROOT / "config/authority-runtime.lock.json"
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=destination.parent, delete=False) as stream:
        json.dump(lock, stream, indent=2)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, destination)
    print(json.dumps({"status": "prepared", "image_id": image["Id"], "sources_digest": source_digest}))


if __name__ == "__main__":
    main()
