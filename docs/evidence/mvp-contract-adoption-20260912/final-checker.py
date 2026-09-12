"""製品検証・文書索引と非掲載検査の範囲を別々に最終照合する。"""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
LOCAL = Path(__file__).resolve().parent
OUT = ROOT / "docs/evidence/mvp-contract-adoption-20260912"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text("utf-8"))


def main():
    names = ("final-check.json", "privacy-final.json", "workflow-final.json", "final-checker.py", "privacy-checker.py")
    assert all(not (OUT / name).exists() for name in names)
    verification = read(OUT / "verification.json")
    privacy = read(LOCAL / "privacy-final.json")
    run = subprocess.run([sys.executable, "-E", "-X", "utf8", "-m", "tools.workflow", "check"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60)
    workflow = json.loads(run.stdout)
    index = read(ROOT / "docs/birdseye/index.json")
    checks = {
        "evidence_hashes_match": all(sha(OUT / p) == d for p, d in verification["artifact_sha256"].items()),
        "tested_sources_match": all(sha(ROOT / p) == d for p, d in verification["source_sha256"].items()),
        "seven_preserved_sources_match": len(verification["preserved"]) == 7 and all(
            sha(ROOT / p) == v["previous_sha256"] == v["actual_sha256"] for p, v in verification["preserved"].items()),
        "workflow_passed": run.returncode == 0 and workflow["status"] == "pass",
        "index_sources_match": all(hashlib.sha256((ROOT / p).read_text("utf-8").encode("utf-8")).hexdigest()
            == node["source_sha256"] for p, node in index["nodes"].items()),
        "privacy_full_coverage_passed": privacy["passed"] is True and privacy["private_reference_matches"] == 0
            and privacy["current_product_scan"]["read_errors"] == 0,
        "limited_scope_preserved": verification["full_mvp_accepted"] is False
            and verification["ci_eligible"] is False and all(verification["checks"].values()),
    }
    for name, data in (("privacy-final.json", (LOCAL / "privacy-final.json").read_bytes()),
                       ("workflow-final.json", run.stdout.encode("utf-8")),
                       ("final-checker.py", Path(__file__).read_bytes()),
                       ("privacy-checker.py", (LOCAL / "verify_privacy.py").read_bytes())):
        with (OUT / name).open("xb") as stream:
            stream.write(data)
    other_passed = all(v for k, v in checks.items() if k != "privacy_full_coverage_passed")
    result = {"schema_version": 1, "checked_at": datetime.now(timezone.utc).isoformat(),
        "status": "passed" if all(checks.values()) else ("partial" if other_passed else "failed"),
        "checks": checks, "verification_sha256": sha(OUT / "verification.json"),
        "final_artifact_sha256": {p: sha(OUT / p) for p in names if p != "final-check.json"},
        "birdseye_generation": index["generated_at"], "birdseye_nodes": len(index["nodes"]),
        "birdseye_index_sha256": sha(ROOT / "docs/birdseye/index.json"),
        "privacy_uninspected_paths": privacy.get("unreadable_paths", []),
        "qwen_final_review_completed": False, "luna_final_review_completed": False,
        "full_mvp_accepted": False, "ci_eligible": False}
    with (OUT / "final-check.json").open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
