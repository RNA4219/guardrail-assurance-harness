"""最終ソース・証跡・文書索引の一致を新規の結果へ記録する。"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs/evidence/mvp-candidate-20260911"
PRIVACY = Path(__file__).resolve().parent / "privacy-final.json"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text("utf-8"))


def main():
    names = ("final-check-followup.json", "privacy-followup.json", "workflow-followup.json", "final-checker-followup.py")
    assert all(not (OUT / name).exists() for name in names)
    verification = read(OUT / "verification.json")
    privacy = read(PRIVACY)
    # 文書検査のstdoutをpacketへ保存し、この最終照合の時点へ結ぶ。
    run = subprocess.run([sys.executable, "-E", "-X", "utf8", "-m", "tools.workflow", "check"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60)
    workflow = json.loads(run.stdout)
    index = read(ROOT / "docs/birdseye/index.json")
    checks = {
        "evidence_hashes_match": all(sha(OUT / name) == digest
            for name, digest in verification["artifact_sha256"].items()),
        "tested_sources_match": all(sha(ROOT / name) == digest
            for name, digest in verification["source_sha256"].items()),
        "seven_preserved_sources_match": len(verification["preserved"]) == 7 and all(
            sha(ROOT / name) == value["previous_sha256"] == value["actual_sha256"]
            for name, value in verification["preserved"].items()),
        "workflow_check_passed": run.returncode == 0 and workflow["status"] == "pass",
        "index_and_sources_match": index["generated_at"] == "00046" and len(index["nodes"]) == 120
            and all(hashlib.sha256((ROOT / name).read_text("utf-8").encode("utf-8")).hexdigest()
                    == node["source_sha256"] for name, node in index["nodes"].items()),
        "privacy_check_passed": privacy["passed"] is True and privacy["private_reference_matches"] == 0
            and all(privacy["current_product_scan"][key] == 0
                    for key in ("path_matches", "content_matches", "read_errors")),
        "limited_scope_preserved": verification["full_mvp_accepted"] is False
            and verification["ci_eligible"] is False and all(verification["checks"].values()),
    }
    for name, data in (("privacy-followup.json", PRIVACY.read_bytes()),
                       ("workflow-followup.json", run.stdout.encode("utf-8")),
                       ("final-checker-followup.py", Path(__file__).read_bytes())):
        with (OUT / name).open("xb") as stream:
            stream.write(data)
    result = {
        "schema_version": 1, "checked_at": datetime.now(timezone.utc).isoformat(),
        "status": "passed" if all(checks.values()) else (
            "partial" if all(value for key, value in checks.items() if key != "privacy_check_passed")
            else "failed"), "checks": checks,
        "privacy_limitation": {
            "uninspected_directory": ".ga/authority-images/a3117c1630933eb7-xa1d5cpf",
            "directory_enumeration_failed": True,
            "elevated_read_also_failed": True, "acl_or_ownership_changed": False,
            "previous_walk_checks_prove_directory_coverage": False,
        },
        "verification_sha256": sha(OUT / "verification.json"),
        "final_artifact_sha256": {name: sha(OUT / name) for name in names if name != "final-check-followup.json"},
        "birdseye_generation": index["generated_at"], "birdseye_index_sha256": sha(ROOT / "docs/birdseye/index.json"),
        "full_mvp_accepted": False, "ci_eligible": False,
    }
    with (OUT / "final-check-followup.json").open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
