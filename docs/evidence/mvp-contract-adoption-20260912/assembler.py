"""採択の検証結果と制限を、実行済みの不変記録へまとめる。"""
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json

ROOT = Path(__file__).resolve().parents[2]
LOCAL = Path(__file__).resolve().parent
DEST = ROOT / "docs/evidence/mvp-contract-adoption-20260912"


def read(path):
    return json.loads(path.read_text("utf-8"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    unit = read(LOCAL / "units-02/check.json")
    runtime = read(LOCAL / "runtime-02/check.json")
    receipts = read(LOCAL / "runtime-02/execution-receipts.json")
    previous = read(ROOT / "docs/evidence/mvp-candidate-20260911/runtime-check.json")
    qwen = [read(LOCAL / f"qwen-adoption-{n:02d}/receipt.json") for n in (1, 2)]
    sources = dict(unit["source_sha256"])
    for path, digest in runtime["source_sha256"].items():
        assert path not in sources or sources[path] == digest
        sources[path] = digest
    counts = Counter(item["normalized_result"]["binding"]["run_id"] for item in receipts)
    replaced = sorted(set(previous["checks"]) - set(runtime["checks"]))
    checks = {
        "unittest_passed_and_previous_494_retained": unit["passed"]
            and unit["test_counts"]["previous_retained"] == 494,
        "runtime_adoption_checks_passed": runtime["passed"] and all(runtime["checks"].values())
            and runtime["candidate_adoption_enabled"] is True,
        "sixty_actual_isolated_executions": len(receipts) == 60
            and len({r["normalized_result"]["binding"]["operation_id"] for r in receipts}) == 60
            and counts == {"baseline-runtime-run": 15, "candidate-old-runtime": 15, "candidate-new-runtime": 30}
            and all(r["execution_status"] == "COMPLETED" and all(r[k] is True for k in
                ("stop_confirmed", "cleanup_confirmed", "isolation_config_verified")) for r in receipts),
        "tested_source_hashes_match": all(sha(ROOT / p) == d for p, d in sources.items()),
        "seven_requirements_policy_examples_preserved": len(unit["preserved"]) == 7
            and all(sha(ROOT / p) == v["actual_sha256"] for p, v in unit["preserved"].items()),
        "full_mvp_and_ci_not_promoted": unit["full_mvp_accepted"] is False
            and runtime["full_mvp"] is False and runtime["ci_eligible"] is False,
    }
    assert all(checks.values()), checks
    DEST.mkdir(exist_ok=False)
    files = {"assembler.py": Path(__file__), "unit-checker.py": LOCAL / "verify_units_final.py",
        "unit-check.json": LOCAL / "units-02/check.json", "unittest.log": LOCAL / "units-02/unittest.log",
        "authority-runtime.lock.json": ROOT / "config/authority-runtime.lock.json",
        "fixture-runtime.lock.json": ROOT / "config/fixture-runtime.lock.json",
        "transition-spec.txt": ROOT / "docs/contract-transition-detail-spec.md"}
    for name in ("check.json", "observations.json", "execution-receipts.json", "manifest.json", "deployment.json"):
        files["runtime-" + name] = LOCAL / "runtime-02" / name
    for n in (1, 2):
        for name in ("receipt.json", "submitted-source.txt"):
            files[f"qwen-{n:02d}-" + name] = LOCAL / f"qwen-adoption-{n:02d}" / name
    files["qwen-checker-01.py"] = LOCAL / "ask_qwen.py"
    files["qwen-checker-02.py"] = LOCAL / "ask_qwen_bounded.py"
    files["prior-unit-check.json"] = LOCAL / "units-01/check.json"
    files["prior-unittest.log"] = LOCAL / "units-01/unittest.log"
    files["prior-runtime-check.json"] = LOCAL / "runtime-01/check.json"
    files["prior-runtime-execution-receipts.json"] = LOCAL / "runtime-01/execution-receipts.json"
    files["prior-authority-runtime.lock.json"] = LOCAL / "runtime-01/authority-runtime.lock.json"
    for path in ("src/gah/authority.py", "tools/authority_runtime.py", "src/gah/transition_acceptance.py", "src/gah/evaluation_authority.py", "src/gah/adoption_migrations.py",
                 "tests/test_transition_acceptance_integration.py", "tests/test_transition_acceptance_migration.py",
                 "tools/transition_acceptance_runtime_checks.py", "tools/candidate_runtime_checks.py",
                 "tools/verify_baseline_runtime.py", "tools/prepare_authority_runtime.py"):
        files[path.split("/")[0] + "-" + Path(path).name] = ROOT / path
    for name, source in files.items():
        with (DEST / name).open("xb") as stream:
            stream.write(source.read_bytes())
    result = {"schema_version": 1, "checked_at": datetime.now(timezone.utc).isoformat(), "status": "passed",
        "scope": "fixed_uc_ci_generation_one_to_two_contract_adoption_and_current_validity",
        "checks": checks, "test_counts": unit["test_counts"], "tests": unit["tests"],
        "runtime_check_count": len(runtime["checks"]), "actual_run_entry_counts": dict(counts),
        "previous_runtime_checks_retained": len(set(previous["checks"]) & set(runtime["checks"])),
        "previous_runtime_checks_replaced": replaced,
        "runtime_scope_change": "The adoption branch replaces the unadopted reservation probe with gen2 current and source revocation checks; the original candidate-only mode remains available.",
        "image_id": runtime["image_id"], "fixture_image_id": runtime["fixture_image_id"],
        "source_sha256": sources, "preserved": unit["preserved"],
        "artifact_sha256": {name: sha(DEST / name) for name in files},
        "qwen_review": {"completed": False, "attempts": qwen, "reason": "two bounded requests timed out"},
        "luna_review": {"initial_review_and_drafts_received": True, "final_review_completed": False,
            "reason": "usage limit; parent completed corrections and validation"},
        "migration_scope": "synthetic populated v4 DB, factory rebind, reopen and rollback; no deployed volume migration",
        "privacy_scope": "final scan separately records unreadable legacy build directory; no full-coverage pass assumed",
        "full_mvp_accepted": False, "ci_eligible": False, "release_gate": "no_go"}
    with (DEST / "verification.json").open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"checks": checks, "tests": unit["test_counts"], "runtime_checks": len(runtime["checks"]),
        "replaced_runtime_checks": replaced, "verification_sha256": sha(DEST / "verification.json")}))


if __name__ == "__main__":
    main()
